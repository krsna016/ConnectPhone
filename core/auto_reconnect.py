"""Persistent, identity-pinned Wireless ADB connection supervisor."""

import ipaddress
import asyncio
import logging
import os
import subprocess
import threading
import time

from core.config_manager import persist_current_endpoint
from core.adb_lifecycle import endpoint_port_open, load_saved_devices, read_transport_identity, reset_wireless_transport, wireless_transport_states
from core.mdns_scanner import ZeroPingScanner
from core.paths import migrate_legacy_config


def discover_open_tcp_ports(ip, stop_event=None, start_port=30000, end_port=65535, timeout=0.12):
    """Find candidate Android dynamic ports on one trusted LAN address.

    The scan only identifies open TCP listeners. The caller must still use
    ADB authentication and verify the pinned Android hardware identity before
    accepting or persisting any endpoint.
    """
    async def probe(port):
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=timeout)
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
            return port
        except (OSError, asyncio.TimeoutError):
            return None

    async def scan():
        found = []
        batch_size = 512
        for first in range(int(start_port), int(end_port) + 1, batch_size):
            if stop_event is not None and stop_event.is_set():
                break
            last = min(first + batch_size, int(end_port) + 1)
            results = await asyncio.gather(*(probe(port) for port in range(first, last)))
            found.extend(port for port in results if port is not None)
        return sorted(set(found))

    try:
        return asyncio.run(scan())
    except (OSError, RuntimeError, ValueError):
        return []


class AutoReconnector:
    # ADB's host daemon occasionally stalls while scrcpy, file transfers, or
    # dashboard probes are opening transports.  Do not turn one such stall
    # into a user-visible disconnect.
    OFFLINE_GRACE = 8.0
    EXPLICIT_OFFLINE_GRACE = 2.0
    KEEPALIVE_INTERVAL = 10.0
    KEEPALIVE_FAILURE_LIMIT = 3
    LOOP_INTERVAL = 1.0
    CONNECT_TIMEOUT = 4.0
    PORT_SCAN_COOLDOWN = 30.0

    def __init__(self, config_path=None, scanner=None, command_runner=None, busy_check=None, port_discoverer=None):
        self.logger = logging.getLogger(__name__)
        self.config_path = config_path or migrate_legacy_config()
        self.scanner = scanner or ZeroPingScanner()
        self._run = command_runner or subprocess.run
        self._busy_check = busy_check or (lambda: False)
        self._discover_ports = port_discoverer or discover_open_tcp_ports
        self._thread = None
        self._stop_event = threading.Event()
        self.connected_endpoints = set()
        self._endpoint_serial = {}
        self._pending_identity = {}
        self._offline_since = {}
        self._next_attempt = {}
        self._failures = {}
        self._last_reset = {}
        self._last_keepalive = {}
        self._keepalive_failures = {}
        self._last_port_scan = {}

    def start_watching(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._watch_loop, name="ConnectPhone-Reconnect", daemon=True)
        self._thread.start()

    def _watch_loop(self):
        while not self._stop_event.is_set():
            try:
                trusted = self._trusted_devices()
                trusted_serials = {item["serial"] for item in trusted}
                states = self._verify_connections(trusted_serials)
                # An unavailable ADB daemon is not evidence that every phone
                # disconnected. Preserve state and try again next iteration.
                if states is None:
                    self._stop_event.wait(self.LOOP_INTERVAL)
                    continue
                self._keepalive(states)
                discovered = self.scanner.find_devices_instantly(search_time=0.5)
                for item in trusted:
                    serial = item["serial"]
                    if any(states.get(ep) == "device" and value == serial for ep, value in self._endpoint_serial.items()):
                        continue
                    matches = []
                    for device in discovered:
                        if device.get("type", "connect") != "connect":
                            continue
                        hint = device.get("device_serial_hint")
                        if hint == serial or (not hint and device.get("ip") == item["ip"]):
                            matches.append(f"{device['ip']}:{int(device['port'])}")
                    candidates = matches or [f"{item['ip']}:{item['port']}"]
                    for endpoint in dict.fromkeys(candidates):
                        if states.get(endpoint) == "device" or endpoint in self.connected_endpoints:
                            continue
                        if self._try_connect(endpoint, serial):
                            break
                    else:
                        self._recover_rotated_port(item)
            except Exception:
                self.logger.exception("Wireless supervisor iteration failed")
            self._stop_event.wait(self.LOOP_INTERVAL)

    def _trusted_devices(self):
        devices = []
        seen = set()
        for item in load_saved_devices(self.config_path):
            if not isinstance(item, dict) or not item.get("auto_reconnect", True):
                continue
            ip, port, serial = item.get("ip"), item.get("port"), str(item.get("device_serial") or "").strip()
            try:
                valid = isinstance(ipaddress.ip_address(ip), ipaddress.IPv4Address) and 1 <= int(port) <= 65535
            except (ValueError, TypeError):
                valid = False
            if valid and serial and serial not in seen:
                seen.add(serial)
                devices.append({"ip": ip, "port": int(port), "serial": serial})
        return devices

    def _run_adb(self, args, timeout):
        return self._run(["adb", *args], capture_output=True, text=True, timeout=timeout)

    def _schedule_failure(self, endpoint):
        failures = min(self._failures.get(endpoint, 0) + 1, 8)
        self._failures[endpoint] = failures
        self._next_attempt[endpoint] = time.monotonic() + min(4.0, 0.5 * (2 ** (failures - 1)))

    def _record_healthy(self, endpoint):
        """Forget old failures after an authoritative command succeeds.

        The previous implementation accumulated isolated failures for the
        lifetime of the process. Four unrelated timeouts hours apart could
        therefore trigger ``adb disconnect`` against a currently healthy
        phone. Recovery decisions must be based on consecutive failures.
        """
        self._failures.pop(endpoint, None)
        self._next_attempt.pop(endpoint, None)
        self._offline_since.pop(endpoint, None)
        self._keepalive_failures.pop(endpoint, None)

    def _mark_connected(self, endpoint, serial):
        self.connected_endpoints.add(endpoint)
        self._endpoint_serial[endpoint] = serial
        self._pending_identity.pop(endpoint, None)
        self._record_healthy(endpoint)
        # Reconnecting another trusted phone must not steal the dashboard's
        # explicitly selected target. The status selector will choose a device
        # only when the current target is unavailable.
        if not os.environ.get("ANDROID_SERIAL"):
            os.environ["ANDROID_SERIAL"] = endpoint
        if not persist_current_endpoint(self.config_path, endpoint, serial):
            self.logger.warning("Could not persist wireless endpoint %s", endpoint)

    @staticmethod
    def _port_open(endpoint):
        return endpoint_port_open(endpoint)

    def _maybe_reset_stale_endpoint(self, endpoint):
        now = time.monotonic()
        failures = self._failures.get(endpoint, 0)
        # If the phone's TCP endpoint is reachable but ADB is still stuck, the
        # daemon has retained a stale transport. Recover it promptly instead of
        # leaving the UI offline for minutes. Never restart a shared daemon
        # while another wireless target is attached.
        hard = failures >= 5
        reset_key = f"{endpoint}#daemon" if hard else endpoint
        cooldown = 600 if hard else 60
        last_reset = self._last_reset.get(reset_key)
        if self._busy_check() or failures < 4 or (last_reset is not None and now - last_reset < cooldown):
            return False
        if not self._port_open(endpoint):
            return False
        if hard:
            states = wireless_transport_states(self._run)
            if any(candidate != endpoint for candidate in states):
                hard = False
                reset_key = endpoint
                cooldown = 60
                last_reset = self._last_reset.get(reset_key)
                if last_reset is not None and now - last_reset < cooldown:
                    return False
        if reset_wireless_transport(self._run, endpoint, restart_daemon=hard):
            self.logger.warning("Recovered stale wireless transport: %s", endpoint)
            self._last_reset[reset_key] = now
            self._next_attempt[endpoint] = now + 2.0
            return True
        return False

    def _try_connect(self, endpoint, expected_serial):
        if not expected_serial or time.monotonic() < self._next_attempt.get(endpoint, 0):
            return False
        try:
            result = self._run_adb(["connect", endpoint], self.CONNECT_TIMEOUT)
            output = f"{result.stdout or ''} {result.stderr or ''}".lower()
            if "connected to" not in output and "already connected" not in output:
                self._schedule_failure(endpoint)
                self._maybe_reset_stale_endpoint(endpoint)
                return False
            identity = read_transport_identity(self._run, endpoint)
            if identity is None:
                self._pending_identity[endpoint] = expected_serial
                self._schedule_failure(endpoint)
                self._maybe_reset_stale_endpoint(endpoint)
                return False
            if identity != expected_serial:
                self.logger.error("Rejected wireless identity at %s", endpoint)
                self._run_adb(["disconnect", endpoint], 6)
                self._schedule_failure(endpoint)
                return False
            self._mark_connected(endpoint, identity)
            return True
        except (OSError, subprocess.TimeoutExpired):
            self._schedule_failure(endpoint)
            return False

    def _recover_rotated_port(self, item):
        """Recover an already-paired phone when Android rotates its TLS port."""
        if self._busy_check():
            return False
        ip = str(item.get("ip") or "").strip()
        expected_serial = str(item.get("serial") or "").strip()
        old_port = int(item.get("port") or 0)
        if not ip or not expected_serial:
            return False
        now = time.monotonic()
        if now - self._last_port_scan.get(expected_serial, 0) < self.PORT_SCAN_COOLDOWN:
            return False
        self._last_port_scan[expected_serial] = now
        try:
            ports = self._discover_ports(ip, self._stop_event)
        except (OSError, RuntimeError, TypeError, ValueError):
            return False
        for port in ports:
            try:
                port = int(port)
            except (TypeError, ValueError):
                continue
            if not 1 <= port <= 65535 or port == old_port:
                continue
            endpoint = f"{ip}:{port}"
            if self._try_connect(endpoint, expected_serial):
                self.logger.info("Recovered rotated Wireless Debugging port: %s", endpoint)
                return True
        return False

    def _verify_connections(self, trusted_serials):
        states = wireless_transport_states(self._run, unavailable=None)
        if states is None:
            self.logger.warning("ADB device-state query unavailable; retaining wireless connections")
            return None
        now = time.monotonic()
        for endpoint in list(self.connected_endpoints):
            state = states.get(endpoint)
            if state == "device":
                self._offline_since.pop(endpoint, None)
                if endpoint in self._pending_identity and now >= self._next_attempt.get(endpoint, 0):
                    identity = read_transport_identity(self._run, endpoint)
                    expected = self._pending_identity[endpoint]
                    if identity == expected:
                        self._mark_connected(endpoint, identity)
                    elif identity:
                        self._run_adb(["disconnect", endpoint], 6)
                        self.connected_endpoints.discard(endpoint)
                        self._pending_identity.pop(endpoint, None)
                        self._offline_since.pop(endpoint, None)
                        self._schedule_failure(endpoint)
                    else:
                        self._schedule_failure(endpoint)
                continue
            since = self._offline_since.setdefault(endpoint, now)
            grace = self.EXPLICIT_OFFLINE_GRACE if state == "offline" else self.OFFLINE_GRACE
            if now - since < grace:
                continue
            self.connected_endpoints.discard(endpoint)
            self._endpoint_serial.pop(endpoint, None)
            self._pending_identity.pop(endpoint, None)
            self._offline_since.pop(endpoint, None)
            self._last_keepalive.pop(endpoint, None)
            self._keepalive_failures.pop(endpoint, None)
            if state == "offline" and reset_wireless_transport(self._run, endpoint, restart_daemon=False):
                # `adb devices` explicitly confirmed a dead retained TLS
                # transport. Clearing it is required before `adb connect` can
                # create a fresh transport to the same advertised endpoint.
                self.logger.warning("Cleared confirmed offline wireless transport: %s", endpoint)
                self._last_reset[endpoint] = now
                self._next_attempt[endpoint] = now + 0.5
            else:
                self._schedule_failure(endpoint)
                self._maybe_reset_stale_endpoint(endpoint)
        for endpoint, state in states.items():
            if state != "device" or endpoint in self.connected_endpoints or now < self._next_attempt.get(endpoint, 0):
                continue
            identity = read_transport_identity(self._run, endpoint)
            if identity in trusted_serials:
                self._mark_connected(endpoint, identity)
            elif identity is None:
                # `adb devices` can retain a dead TLS transport as "device".
                # A real shell identity probe is authoritative; keep advancing
                # recovery instead of accepting the stale list entry forever.
                self._schedule_failure(endpoint)
                self._maybe_reset_stale_endpoint(endpoint)
        return states

    def _keepalive(self, states):
        if self._busy_check():
            return
        now = time.monotonic()
        for endpoint in list(self.connected_endpoints):
            if states.get(endpoint) != "device" or now - self._last_keepalive.get(endpoint, 0) < self.KEEPALIVE_INTERVAL:
                continue
            try:
                result = self._run_adb(["-s", endpoint, "shell", "true"], 8)
                if result.returncode == 0:
                    self._last_keepalive[endpoint] = now
                    self._record_healthy(endpoint)
                    continue
            except (OSError, subprocess.TimeoutExpired):
                pass

            failures = self._keepalive_failures.get(endpoint, 0) + 1
            self._keepalive_failures[endpoint] = failures
            if failures < self.KEEPALIVE_FAILURE_LIMIT:
                self.logger.warning(
                    "Wireless health probe missed for %s (%d/%d); connection retained",
                    endpoint,
                    failures,
                    self.KEEPALIVE_FAILURE_LIMIT,
                )
                continue

            # Several consecutive real-command failures are authoritative.
            # Only now expose the outage and enter bounded recovery.
            self.connected_endpoints.discard(endpoint)
            self._endpoint_serial.pop(endpoint, None)
            self._pending_identity.pop(endpoint, None)
            self._offline_since.pop(endpoint, None)
            self._last_keepalive.pop(endpoint, None)
            self._keepalive_failures.pop(endpoint, None)
            self._schedule_failure(endpoint)
            self._maybe_reset_stale_endpoint(endpoint)

    def stop_watching(self):
        self._stop_event.set()
        self.scanner.stop()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=4)
        self._thread = None
