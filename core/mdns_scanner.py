import logging
import socket
import time
from zeroconf import Zeroconf, ServiceBrowser, ServiceListener

class AdbMdnsListener(ServiceListener):
    """Event listener that triggers the exact millisecond an Android beacon is detected."""
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.found_devices = []

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        pass

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        pass

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        info = zc.get_service_info(type_, name)
        if info:
            addresses = [socket.inet_ntoa(addr) for addr in info.addresses]
            if addresses:
                ip = addresses[0]
                port = info.port
                self.found_devices.append({"ip": ip, "port": port, "name": name})
                print(f"[+] ⚡ Instant mDNS Discovery! Found Android at {ip}:{port}")

class ZeroPingScanner:
    """
    Zero-Ping mDNS Discovery Engine for Wireless ADB.
    Completely eliminates the need for port scanning by hooking into macOS Bonjour network layers.
    """
    def __init__(self):
        self.logger = logging.getLogger(__name__)

    def find_devices_instantly(self, search_time=1.5):
        """Listens for the invisible _adb-tls-connect._tcp beacon."""
        zeroconf = Zeroconf()
        listener = AdbMdnsListener()
        
        print("[*] Hooking into macOS mDNS layer... waiting for Android beacons...")
        # Start the background service browser
        browser = ServiceBrowser(zeroconf, "_adb-tls-connect._tcp.local.", listener)
        
        # Keep the listener alive for a short window
        time.sleep(search_time)
        zeroconf.close()
        
        if not listener.found_devices:
            print("[-] No wireless Android beacons detected on this network.")
            
        return listener.found_devices
