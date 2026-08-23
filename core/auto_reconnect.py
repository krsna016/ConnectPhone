"""Persistent, identity-pinned Wireless ADB connection supervisor."""

import ipaddress
import logging
import os
import subprocess
import threading
import time

from core.config_manager import persist_current_endpoint
from core.adb_lifecycle import endpoint_port_open, load_saved_devices, read_transport_identity, reset_wireless_transport, wireless_transport_states
from core.mdns_scanner import ZeroPingScanner
from core.paths import migrate_legacy_config


class AutoReconnector:
    OFFLINE_GRACE = 15.0
    KEEPALIVE_INTERVAL = 30.0
    LOOP_INTERVAL = 3.0

    def __init__(self, config_path=None, scanner=None, command_runner=None, busy_check=None):
        self.logger = logging.getLogger(__name__)
        self.config_path = config_path or migrate_legacy_config()
        self.scanner = scanner or ZeroPingScanner()
        self._run = command_runner or subprocess.run
        self._busy_check = busy_check or (lambda: False)
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
        self._next_attempt[endpoint] = time.monotonic() + min(60.0, 2 ** failures)

    def _mark_connected(self, endpoint, serial):
        self.connected_endpoints.add(endpoint)
        self._endpoint_serial[endpoint] = serial
        self._pending_identity.pop(endpoint, None)
        self._offline_since.pop(endpoint, None)
        self._next_attempt.pop(endpoint, None)
        self._failures.pop(endpoint, None)
        os.environ["ANDROID_SERIAL"] = endpoint
        if not persist_current_endpoint(self.config_path, endpoint, serial):
            self.logger.warning("Could not persist wireless endpoint %s", endpoint)

    @staticmethod
    def _port_open(endpoint):
        return endpoint_port_open(endpoint)

    def _maybe_reset_stale_endpoint(self, endpoint):
        now = time.monotonic()
        failures = self._failures.get(endpoint, 0)
        hard = failures >= 5
        reset_key = f"{endpoint}#daemon" if hard else endpoint
        cooldown = 300 if hard else 90
        if self._busy_check() or failures < 3 or now - self._last_reset.get(reset_key, 0) < cooldown:
            return False
        if not self._port_open(endpoint):
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
            result = self._run_adb(["connect", endpoint], 12)
            output = f"{result.stdout or ''} {result.stderr or ''}".lower()
            if "connected to" not in output and "already connected" not in output:
                self._schedule_failure(endpoint)
                self._maybe_reset_stale_endpoint(endpoint)
                return False
            identity = read_transport_identity(self._run, endpoint)
            if identity is None:
                self.connected_endpoints.add(endpoint)
                self._pending_identity[endpoint] = expected_serial
                self._schedule_failure(endpoint)
                return True
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

    def _verify_connections(self, trusted_serials):
        states = wireless_transport_states(self._run)
        now = time.monotonic()
        for endpoint in list(self.connected_endpoints):
            if states.get(endpoint) == "device":
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
            if now - since < self.OFFLINE_GRACE:
                continue
            self.connected_endpoints.discard(endpoint)
            self._endpoint_serial.pop(endpoint, None)
            self._pending_identity.pop(endpoint, None)
            self._offline_since.pop(endpoint, None)
            self._schedule_failure(endpoint)
            self._maybe_reset_stale_endpoint(endpoint)
        for endpoint, state in states.items():
            if state != "device" or endpoint in self.connected_endpoints or now < self._next_attempt.get(endpoint, 0):
                continue
            identity = read_transport_identity(self._run, endpoint)
            if identity in trusted_serials:
                self._mark_connected(endpoint, identity)
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
            except (OSError, subprocess.TimeoutExpired):
                self._offline_since.setdefault(endpoint, now)

    def stop_watching(self):
        self._stop_event.set()
        self.scanner.stop()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=4)
        self._thread = None
