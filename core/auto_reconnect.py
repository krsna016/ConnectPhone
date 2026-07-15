import logging
import threading
import subprocess
import time
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
                # Use our Zero-Ping engine to listen for beacons
                devices = self.scanner.find_devices_instantly(search_time=2.0)
                
                # Periodically clean up the connected list by checking actual ADB status
                self._verify_connections()
                
                for device in devices:
                    ip_port = f"{device['ip']}:{device['port']}"
                    
                    if ip_port not in self.connected_endpoints:
                        print(f"[*] 📡 Device beacon detected at {ip_port}. Executing RSA handshake...")
                        
                        # ADB daemon handles the mathematical RSA signature verification automatically
                        res = subprocess.run(["adb", "connect", ip_port], capture_output=True, text=True)
                        output = res.stdout.lower()
                        
                        if "connected to" in output or "already connected" in output:
                            print(f"[+] ✅ Zero-Interaction Authorization Successful for {ip_port}!")
                            self.connected_endpoints.add(ip_port)
                        elif "unauthorized" in output or "failed to authenticate" in output:
                            print(f"[-] ❌ Connection Rejected: Device at {ip_port} does not trust this Mac's RSA key.")
                            print("    Action Required: Plug the phone in via USB once to accept the RSA fingerprint.")
                
            except Exception as e:
                self.logger.error(f"Auto-Reconnector loop error: {e}")
                time.sleep(2)

    def _verify_connections(self):
        """Removes devices from tracking if they disconnected from the network."""
        res = subprocess.run(["adb", "devices"], capture_output=True, text=True)
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
