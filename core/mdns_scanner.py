"""Thread-safe Bonjour discovery for Android Wireless Debugging."""

import ipaddress
import logging
import threading
import time

from zeroconf import IPVersion, ServiceBrowser, ServiceListener, Zeroconf


ADB_CONNECT_SERVICE = "_adb-tls-connect._tcp.local."
ADB_PAIRING_SERVICE = "_adb-tls-pairing._tcp.local."


class AdbMdnsListener(ServiceListener):
    """Maintain a live service snapshot and wake waiters on every change."""

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self._devices = {}
        self.lock = threading.RLock()
        self.changed = threading.Event()

    @property
    def found_devices(self):
        with self.lock:
            return list(self._devices.values())

    def remove_service(self, zc, type_, name):
        with self.lock:
            self._devices.pop((type_, name), None)
        self.changed.set()

    def update_service(self, zc, type_, name):
        self.add_service(zc, type_, name)

    def add_service(self, zc, type_, name):
        try:
            info = zc.get_service_info(type_, name, timeout=1500)
            if not info:
                return
            ipv4 = []
            for address in info.parsed_addresses(IPVersion.V4Only):
                try:
                    parsed = ipaddress.ip_address(address)
                    if isinstance(parsed, ipaddress.IPv4Address) and not parsed.is_unspecified:
                        ipv4.append(str(parsed))
                except ValueError:
                    continue
            if not ipv4 or not 1 <= int(info.port) <= 65535:
                return
            instance = name.split(".", 1)[0]
            serial_hint = None
            if instance.startswith("adb-") and "-" in instance[4:]:
                serial_hint = instance[4:].rsplit("-", 1)[0] or None
            device = {
                "ip": ipv4[0],
                "port": int(info.port),
                "name": name,
                "device_serial_hint": serial_hint,
                "type": "pairing" if "pairing" in type_ else "connect",
                "seen_at": time.monotonic(),
            }
            with self.lock:
                self._devices[(type_, name)] = device
            self.changed.set()
        except Exception as exc:
            self.logger.warning("Could not resolve mDNS service %s: %s", name, exc)


class ZeroPingScanner:
    """Persistent IPv4 Bonjour browser with bounded, interruptible waits."""

    def __init__(self, include_pairing=False):
        self.logger = logging.getLogger(__name__)
        self.include_pairing = include_pairing
        self._zc = None
        self._listener = None
        self._browsers = []
        self._lock = threading.RLock()

    def start(self):
        with self._lock:
            if self._zc is not None:
                return True
            try:
                self._zc = Zeroconf(ip_version=IPVersion.V4Only)
                self._listener = AdbMdnsListener()
                types = [ADB_CONNECT_SERVICE]
                if self.include_pairing:
                    types.append(ADB_PAIRING_SERVICE)
                self._browsers = [ServiceBrowser(self._zc, item, self._listener) for item in types]
                return True
            except Exception as exc:
                self.logger.error("Failed to start Bonjour discovery: %s", exc)
                self._close_locked()
                return False

    def _close_locked(self):
        for browser in self._browsers:
            try:
                browser.cancel()
            except Exception:
                pass
        self._browsers = []
        if self._zc:
            try:
                self._zc.close()
            except Exception:
                pass
        self._zc = None
        self._listener = None

    def stop(self):
        with self._lock:
            self._close_locked()

    def find_devices_instantly(self, search_time=1.5):
        """Return a current snapshot after at most ``search_time`` seconds."""
        if not self.start():
            return []
        with self._lock:
            listener = self._listener
        if listener is None:
            return []
        deadline = time.monotonic() + max(0.0, float(search_time))
        listener.changed.wait(timeout=max(0.0, deadline - time.monotonic()))
        listener.changed.clear()
        while time.monotonic() < deadline:
            if not listener.changed.wait(timeout=min(0.25, deadline - time.monotonic())):
                break
            listener.changed.clear()
        devices = listener.found_devices
        # Prefer the newest advertisement and suppress exact duplicates.
        unique = {}
        for device in sorted(devices, key=lambda item: item.get("seen_at", 0), reverse=True):
            unique.setdefault((device["ip"], device["port"], device["type"]), device)
        return list(unique.values())
