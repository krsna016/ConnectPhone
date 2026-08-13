"""Bounded, identity-pinned reconnect service for Wireless ADB."""

import ipaddress
import logging
import os
import socket
import subprocess
import threading
import time

from core.config_manager import ConfigurationManager
from core.mdns_scanner import ZeroPingScanner
from core.paths import migrate_legacy_config


class AutoReconnector:
    def __init__(self, config_path=None, scanner=None, command_runner=None):
        self.logger = logging.getLogger(__name__)
        self.config_path = config_path or migrate_legacy_config()
        self.scanner = scanner or ZeroPingScanner()
        self._run = command_runner or subprocess.run
        self._is_running = False
        self._thread = None
        self._stop_event = threading.Event()
        self.connected_endpoints = set()
        self._next_attempt = {}
        self._failures = {}
        self._last_daemon_recovery = 0.0
        self._pending_identity = {}

    def start_watching(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._is_running = True
        self._thread = threading.Thread(target=self._watch_loop, name="ConnectPhone-Reconnect", daemon=True)
        self._thread.start()
        self.logger.info("Wireless auto-reconnect watcher started")

    def _watch_loop(self):
        while not self._stop_event.is_set():
            try:
                trusted = self._trusted_endpoints()
                trusted_by_ip = {ip: (port, serial) for ip, port, serial in trusted}
                self._verify_connections()

                # A saved port is a fast hint only; Wireless Debugging ports rotate.
                for ip, port, serial in trusted:
                    endpoint = f"{ip}:{port}"
                    # Do not gate ADB on a tiny raw-TCP preflight. Sleeping
                    # phones on power-saving Wi-Fi routinely need >200 ms to
                    # answer even though the TLS endpoint is healthy.
                    if endpoint not in self.connected_endpoints:
                        self._try_connect(endpoint, serial)

                for device in self.scanner.find_devices_instantly(search_time=1.5):
                    if device.get("type", "connect") != "connect":
                        continue
                    expected = trusted_by_ip.get(device.get("ip"))
                    if not expected:
                        continue
                    endpoint = f"{device['ip']}:{int(device['port'])}"
                    if endpoint not in self.connected_endpoints:
                        self._try_connect(endpoint, expected[1])
            except Exception:
                self.logger.exception("Wireless reconnect iteration failed")
            self._stop_event.wait(1.0)

    def _trusted_endpoints(self):
        try:
            manager = ConfigurationManager(self.config_path)
            config = manager.load()
        except (OSError, ValueError, TypeError):
            return []
        endpoints = []
        seen_ips = set()
        for item in config.get("saved_devices", []):
            if not isinstance(item, dict) or not item.get("auto_reconnect", True):
                continue
            ip, port, serial = item.get("ip"), item.get("port"), item.get("device_serial")
            try:
                valid_ip = isinstance(ipaddress.ip_address(ip), ipaddress.IPv4Address)
                valid_port = 1 <= int(port) <= 65535
            except (ValueError, TypeError):
                continue
            if valid_ip and valid_port and serial and ip not in seen_ips:
                seen_ips.add(ip)
                endpoints.append((ip, int(port), str(serial)))
        return endpoints

    @staticmethod
    def _port_open(ip, port, timeout):
        try:
            with socket.create_connection((ip, int(port)), timeout=timeout):
                return True
        except OSError:
            return False

    def _run_adb(self, args, timeout):
        return self._run(["adb", *args], capture_output=True, text=True, timeout=timeout)

    def _read_identity(self, endpoint):
        for prop in ("ro.serialno", "ro.boot.serialno"):
            try:
                result = self._run_adb(["-s", endpoint, "shell", "getprop", prop], 4)
                value = (result.stdout or "").strip()
                if result.returncode == 0 and value and value.lower() not in {"unknown", "null", "no permissions"}:
                    return value
            except (OSError, subprocess.TimeoutExpired):
                continue
        return None

    def _schedule_failure(self, endpoint):
        failures = min(self._failures.get(endpoint, 0) + 1, 8)
        self._failures[endpoint] = failures
        self._next_attempt[endpoint] = time.monotonic() + min(15.0, 2 ** failures)

    def _maybe_recover_stale_adb_daemon(self, endpoint):
        """Repair a stale host daemon only when TCP proves the phone is reachable."""
        if self._failures.get(endpoint, 0) < 3 or time.monotonic() - self._last_daemon_recovery < 60:
            return False
        ip, port = endpoint.rsplit(":", 1)
        if not self._port_open(ip, int(port), 0.5):
            return False
        try:
            self.logger.warning("ADB daemon is stale while %s is reachable; restarting it once", endpoint)
            self._run_adb(["kill-server"], 5)
            self._run_adb(["start-server"], 8)
            self._last_daemon_recovery = time.monotonic()
            self._next_attempt[endpoint] = time.monotonic() + 1.0
            return True
        except (OSError, subprocess.TimeoutExpired):
            return False

    def _try_connect(self, endpoint, expected_serial=None):
        if not expected_serial or time.monotonic() < self._next_attempt.get(endpoint, 0):
            return False
        try:
            result = self._run_adb(["connect", endpoint], 8)
            output = f"{result.stdout or ''} {result.stderr or ''}".lower()
            if "connected to" not in output and "already connected" not in output:
                self._schedule_failure(endpoint)
                self._maybe_recover_stale_adb_daemon(endpoint)
                return False
            identity = self._read_identity(endpoint)
            if identity is None:
                # Android may expose the transport in `adb devices` before
                # property queries are ready. Keep the accepted TLS transport
                # pending and verify it later without issuing another connect
                # handshake (which causes repeated phone notifications).
                self.connected_endpoints.add(endpoint)
                self._pending_identity[endpoint] = expected_serial
                self.logger.info("Wireless endpoint awaiting identity verification: %s", endpoint)
                return False
            if identity != expected_serial:
                self.logger.error("Rejected wireless identity at %s (expected %s, got %s)", endpoint, expected_serial, identity)
                self._run_adb(["disconnect", endpoint], 5)
                self._pending_identity.pop(endpoint, None)
                self._schedule_failure(endpoint)
                return False
            self.connected_endpoints.add(endpoint)
            self._failures.pop(endpoint, None)
            self._next_attempt.pop(endpoint, None)
            self._pending_identity.pop(endpoint, None)
            self._persist_current_endpoint(endpoint, identity)
            self.logger.info("Trusted wireless endpoint connected: %s", endpoint)
            return True
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.logger.debug("Reconnect failed for %s: %s", endpoint, exc)
            self._schedule_failure(endpoint)
            return False

    def _persist_current_endpoint(self, endpoint, identity):
        ip, port = endpoint.rsplit(":", 1)
        try:
            manager = ConfigurationManager(self.config_path)
            manager.load()
            manager.update_last_connection(ip, int(port), identity)
        except (OSError, ValueError, TypeError, RuntimeError):
            self.logger.exception("Could not persist refreshed wireless endpoint %s", endpoint)

    def _verify_connections(self):
        try:
            result = self._run_adb(["devices"], 5)
            active = {
                line.split()[0]
                for line in (result.stdout or "").splitlines()[1:]
                if len(line.split()) >= 2 and line.split()[1] == "device" and ":" in line.split()[0]
            }
        except (OSError, subprocess.TimeoutExpired):
            return
        self.connected_endpoints.intersection_update(active)
        for endpoint in list(self._pending_identity):
            if endpoint not in active:
                self._pending_identity.pop(endpoint, None)
                self.connected_endpoints.discard(endpoint)
                continue
            expected = self._pending_identity[endpoint]
            identity = self._read_identity(endpoint)
            if identity is None:
                continue
            if identity != expected:
                self.logger.error("Rejected pending wireless identity at %s", endpoint)
                try:
                    self._run_adb(["disconnect", endpoint], 5)
                except (OSError, subprocess.TimeoutExpired):
                    pass
                self.connected_endpoints.discard(endpoint)
                self._pending_identity.pop(endpoint, None)
                self._schedule_failure(endpoint)
                continue
            self._pending_identity.pop(endpoint, None)
            self._failures.pop(endpoint, None)
            self._next_attempt.pop(endpoint, None)
            self._persist_current_endpoint(endpoint, identity)
            self.logger.info("Pending wireless identity verified: %s", endpoint)

    def stop_watching(self):
        self._is_running = False
        self._stop_event.set()
        self.scanner.stop()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)
        self._thread = None
