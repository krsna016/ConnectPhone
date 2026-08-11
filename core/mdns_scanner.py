import logging
import socket
import time
import threading
from zeroconf import Zeroconf, ServiceBrowser, ServiceListener

class AdbMdnsListener(ServiceListener):
    """Event listener that triggers the exact millisecond an Android beacon is detected."""
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.found_devices = []
        self.lock = threading.Lock()

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        with self.lock:
            self.found_devices = [d for d in self.found_devices if d.get("name") != name]
        print(f"[-] ⚡ mDNS Service Removed: {name}")

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        # Same as add_service, just refresh
        self.add_service(zc, type_, name)

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        try:
            info = zc.get_service_info(type_, name)
            if info:
                addresses = [socket.inet_ntoa(addr) for addr in info.addresses]
                if addresses:
                    ip = addresses[0]
                    port = info.port
                    device = {"ip": ip, "port": port, "name": name}
                    with self.lock:
                        # Avoid duplicates: update if name matches, else append
                        for idx, d in enumerate(self.found_devices):
                            if d.get("name") == name:
                                self.found_devices[idx] = device
                                return
                        self.found_devices.append(device)
                    print(f"[+] ⚡ Instant mDNS Discovery! Found Android at {ip}:{port}")
        except Exception as e:
            self.logger.error(f"Error in add_service: {e}")

class ZeroPingScanner:
    """
    Zero-Ping mDNS Discovery Engine for Wireless ADB.
    Completely eliminates the need for port scanning by hooking into macOS Bonjour network layers.
    """
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self._zc = None
        self._listener = None
        self._browser = None
        self._lock = threading.Lock()

    def start(self):
        with self._lock:
            if self._zc is None:
                try:
                    self._zc = Zeroconf()
                    self._listener = AdbMdnsListener()
                    self._browser = ServiceBrowser(self._zc, "_adb-tls-connect._tcp.local.", self._listener)
                except Exception as e:
                    self.logger.error(f"Failed to start Zero-Ping mDNS scanner: {e}")
                    self._zc = None
                    self._listener = None
                    self._browser = None

    def stop(self):
        with self._lock:
            if self._browser:
                try:
                    self._browser.cancel()
                except Exception:
                    pass
                self._browser = None
            if self._zc:
                try:
                    self._zc.close()
                except Exception:
                    pass
                self._zc = None
            self._listener = None

    def find_devices_instantly(self, search_time=1.5):
        """Listens for the invisible _adb-tls-connect._tcp beacon."""
        self.start()
        # Sleep to allow discovery
        time.sleep(search_time)
        with self._lock:
            if self._listener:
                with self._listener.lock:
                    return list(self._listener.found_devices)
        return []

