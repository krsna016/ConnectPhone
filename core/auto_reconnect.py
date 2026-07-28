import logging
import threading
import subprocess
import time
import json
import os
import ipaddress
from core.mdns_scanner import ZeroPingScanner

class AutoReconnector:
    """
    Enterprise-grade background service that enforces zero-interaction 
    auto-reconnection for previously trusted devices.
    """
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.scanner = ZeroPingScanner()
        self._is_running = False
        self._thread = None
        self.connected_endpoints = set()

    def start_watching(self):
        """Starts the autonomous network watcher on a background thread."""
        if self._is_running:
            return
            
        self._is_running = True
        self._thread = threading.Thread(target=self._watch_loop, daemon=True)
        self._thread.start()
        print("[+] 🛡️ Secure Auto-Reconnector daemon started.")
        print("[*] Waiting for trusted devices to appear on the Wi-Fi network...")

    def _watch_loop(self):
        while self._is_running:
            try:
                # Retry explicitly saved endpoints first. A Wi-Fi sleep or DHCP
                # hiccup must not require the user to press Connect again.
                for ip, port, device_serial in self._trusted_endpoints():
                    ip_port = f"{ip}:{port}"
                    if ip_port not in self.connected_endpoints:
                        self._try_connect(ip_port, device_serial)

                # Use our Zero-Ping engine to listen for beacons
                devices = self.scanner.find_devices_instantly(search_time=2.0)
                
                # Periodically clean up the connected list by checking actual ADB status
                self._verify_connections()
                
                for device in devices:
                    ip_port = f"{device['ip']}:{device['port']}"
                    if not self._is_trusted_ip(device["ip"]):
                        self.logger.warning("Ignoring untrusted mDNS ADB device at %s", ip_port)
                        continue
                    
                    if ip_port not in self.connected_endpoints:
                        print(f"[*] 📡 Device beacon detected at {ip_port}. Executing RSA handshake...")
                        
                        expected = next((serial for saved_ip, _, serial in self._trusted_endpoints() if saved_ip == device["ip"]), None)
                        self._try_connect(ip_port, expected)
                
            except Exception as e:
                self.logger.error(f"Auto-Reconnector loop error: {e}")
                time.sleep(2)

    @staticmethod
    def _trusted_endpoints():
        try:
            with open(os.path.expanduser("~/.connectphone_config.json"), encoding="utf-8") as f:
                config = json.load(f)
            endpoints = []
            for item in config.get("saved_devices", []):
                if not isinstance(item, dict) or not item.get("auto_reconnect", True):
                    continue
                ip = item.get("ip")
                port = item.get("port")
                try:
                    if isinstance(ipaddress.ip_address(ip), ipaddress.IPv4Address) and 1 <= int(port) <= 65535:
                        device_serial = item.get("device_serial")
                        if not device_serial:
                            continue
                        endpoints.append((ip, int(port), str(device_serial)))
                except (ValueError, TypeError):
                    continue
            # Legacy entries without an identity require one manual enrollment.
            return endpoints
        except (OSError, ValueError, TypeError):
            return []

    def _is_trusted_ip(self, ip):
        return any(saved_ip == ip for saved_ip, _, _ in self._trusted_endpoints())

    def _try_connect(self, ip_port, expected_serial=None):
        """Attempt one bounded reconnect without blocking the watcher forever."""
        try:
            res = subprocess.run(["adb", "connect", ip_port], capture_output=True, text=True, timeout=8)
            output = (res.stdout or "").lower()
            if "connected to" in output or "already connected" in output:
                if not expected_serial:
                    self.logger.warning("Refusing unpinned wireless endpoint %s", ip_port)
                    subprocess.run(["adb", "disconnect", ip_port], capture_output=True, timeout=5)
                    return
                identity_result = subprocess.run(
                    ["adb", "-s", ip_port, "get-serialno"],
                    capture_output=True,
                    text=True,
                    timeout=8,
                )
                identity = (identity_result.stdout or "").strip()
                if identity != expected_serial:
                    self.logger.error("Identity mismatch for %s; refusing reconnect", ip_port)
                    subprocess.run(["adb", "disconnect", ip_port], capture_output=True, timeout=5)
                    return
                print(f"[+] ✅ Trusted wireless endpoint connected: {ip_port}")
                self.connected_endpoints.add(ip_port)
            elif "unauthorized" in output or "failed to authenticate" in output:
                self.logger.warning("ADB rejected trusted endpoint %s; phone authorization is required", ip_port)
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.logger.debug("Reconnect attempt failed for %s: %s", ip_port, exc)

    def _verify_connections(self):
        """Removes devices from tracking if they disconnected from the network."""
        try:
            res = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=8)
        except (OSError, subprocess.TimeoutExpired):
            return
        active_list = res.stdout
        
        stale_endpoints = []
        for endpoint in self.connected_endpoints:
            if endpoint not in active_list:
                stale_endpoints.append(endpoint)
                
        for stale in stale_endpoints:
            self.connected_endpoints.remove(stale)
            print(f"[*] 🔌 Device {stale} disconnected. Ready for next auto-reconnect.")

    def stop_watching(self):
        self._is_running = False
        print("[-] Auto-Reconnector daemon stopped.")
