"""Ownership-aware cleanup for the host ADB daemon and wireless transports."""

import json
import socket
import subprocess
import threading


_DEFAULT_UNAVAILABLE = object()


def load_saved_devices(config_path):
    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data.get("saved_devices", []) if isinstance(data, dict) else []
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return []


def read_transport_identity(runner, transport, timeout=6):
    for prop in ("ro.serialno", "ro.boot.serialno"):
        try:
            result = runner(
                ["adb", "-s", transport, "shell", "getprop", prop],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            identity = (result.stdout or "").strip()
            if result.returncode == 0 and identity and identity.lower() not in {"unknown", "null", "no permissions"}:
                return identity
        except (OSError, subprocess.TimeoutExpired):
            pass
    return None


def valid_physical_identity(identity, transport=""):
    """Reject ADB transport endpoints masquerading as hardware identities."""
    value = str(identity or "").strip()
    return bool(
        value
        and value != str(transport or "").strip()
        and ":" not in value
        and value.lower() not in {"unknown", "null", "no permissions"}
    )


def wireless_transport_states(runner, unavailable=_DEFAULT_UNAVAILABLE):
    """Return wireless ADB states, distinguishing failure from no devices.

    Historically a timed-out ``adb devices`` call and a successful empty list
    both returned ``{}``.  A connection supervisor cannot safely interpret
    those cases the same way: treating a busy/unavailable host daemon as an
    empty device list causes healthy transports to be torn down.

    Existing callers retain the old empty-dict fallback.  Supervisors that
    need the distinction can pass an explicit sentinel (normally ``None``).
    """
    try:
        result = runner(["adb", "devices"], capture_output=True, text=True, timeout=8)
        if result.returncode != 0:
            return {} if unavailable is _DEFAULT_UNAVAILABLE else unavailable
        return {
            parts[0]: parts[1]
            for line in (result.stdout or "").splitlines()[1:]
            if len(parts := line.split()) >= 2 and ":" in parts[0]
        }
    except (OSError, subprocess.TimeoutExpired):
        return {} if unavailable is _DEFAULT_UNAVAILABLE else unavailable


def endpoint_port_open(endpoint, timeout=4.0):
    try:
        ip, port = endpoint.rsplit(":", 1)
        with socket.create_connection((ip, int(port)), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def reset_wireless_transport(runner, endpoint, restart_daemon=False):
    try:
        result = runner(["adb", "disconnect", endpoint], capture_output=True, text=True, timeout=8)
        if result.returncode == 0:
            return True
    except (OSError, subprocess.TimeoutExpired):
        pass
    if not restart_daemon:
        return False
    try:
        runner(["adb", "kill-server"], capture_output=True, text=True, timeout=8)
        runner(["adb", "start-server"], capture_output=True, text=True, timeout=12)
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


class AdbLifecycle:
    def __init__(self, runner=None, daemon_probe=None):
        self._run = runner or subprocess.run
        self._daemon_probe = daemon_probe or self._default_daemon_probe
        self.daemon_preexisting = bool(self._daemon_probe())
        self._owned_endpoints = set()
        self._lock = threading.RLock()

    @staticmethod
    def _default_daemon_probe():
        try:
            with socket.create_connection(("127.0.0.1", 5037), timeout=0.15):
                return True
        except OSError:
            return False

    def register(self, endpoint):
        if isinstance(endpoint, str) and ":" in endpoint:
            with self._lock:
                self._owned_endpoints.add(endpoint)

    @staticmethod
    def _active_transports(output):
        transports = []
        for line in (output or "").splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                transports.append(parts[0])
        return transports

    def _transport_identity(self, transport):
        return read_transport_identity(self._run, transport, timeout=3)

    def cleanup(self, extra_endpoints=(), owned_serials=()):
        """Disconnect app-owned transports and stop an app-owned idle daemon.

        Pairing records remain saved for the next launch. A daemon that existed
        before ConnectPhone launched is never stopped, and a daemon started by
        ConnectPhone is preserved if another ADB transport still depends on it.
        """
        with self._lock:
            endpoints = set(self._owned_endpoints)
            endpoints.update(item for item in extra_endpoints if isinstance(item, str) and ":" in item)
            self._owned_endpoints.clear()

        for endpoint in sorted(endpoints):
            try:
                self._run(
                    ["adb", "disconnect", endpoint],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass

        if self.daemon_preexisting:
            return False

        try:
            result = self._run(
                ["adb", "devices"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            transports = self._active_transports(result.stdout)
            owned_serials = {str(item) for item in owned_serials if item}
            for transport in transports:
                if self._transport_identity(transport) not in owned_serials:
                    return False
            self._run(
                ["adb", "kill-server"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return True
        except (OSError, subprocess.TimeoutExpired):
            return False
