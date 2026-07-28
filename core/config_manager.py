import os
import json
import logging
import tempfile
import copy
import ipaddress
from typing import Dict, Any
from core import keychain

class ConfigurationManager:
    """
    Handles loading, saving, and managing configuration settings.
    Demonstrates Single Responsibility Principle and Dependency Injection.
    """
    
    DEFAULT_CONFIG = {
        "mirror_enabled": True,
        "screen_off_enabled": False,
        "stay_awake_enabled": True,
        "show_touches_enabled": False,
        "audio_preset": "voice_communication",
        "last_ip": "",
        "last_port": None,
        "android_pin": "",
        "biometric_daemon_enabled": False,
        "camera_bitrate": "32M",
        "camera_fps": "60",
        "camera_codec": "h265",
        "audio_sync_delay": "0.80",
        "keyboard_mode": "uhid",
        "mac_mic_device": "default",
        # 20 ms keeps wireless audio responsive while retaining a small
        # jitter cushion.  The old 100 ms default made mic/audio mirroring
        # feel noticeably delayed.
        "audio_buffer": "20",
        "device_profile": "generic",
        "saved_ips": [],
        "saved_devices": []
    }
    SECRET_KEYS = ("android_pin", "applock_pin")

    def __init__(self, config_path: str):
        # Dependency Injection: The path is passed in, making this class testable without writing to ~/.
        self.config_path = config_path
        self._config_data = copy.deepcopy(self.DEFAULT_CONFIG)
        self.logger = logging.getLogger(__name__)

    def load(self) -> Dict[str, Any]:
        """Loads configuration from the filesystem. Does not swallow critical errors."""
        if not os.path.exists(self.config_path):
            self.logger.info(f"Config not found at {self.config_path}, returning defaults.")
            return self._config_data

        try:
            try:
                os.chmod(self.config_path, 0o600)
            except OSError:
                pass
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Migrate legacy plaintext secrets once, then hydrate memory from Keychain.
            migrated_legacy_secret = False
            for secret_name in self.SECRET_KEYS:
                legacy_value = data.pop(secret_name, "")
                if legacy_value:
                    migrated_legacy_secret = True
                    try:
                        keychain.set(secret_name, legacy_value)
                    except RuntimeError:
                        self.logger.warning("Could not migrate %s to Keychain", secret_name)
                stored_value = keychain.get(secret_name)
                data[secret_name] = stored_value or (legacy_value if not keychain.available() else "")
                
            # Merge defaults
            for key, value in self.DEFAULT_CONFIG.items():
                if key not in data:
                    data[key] = value

            # Migrate the previous shipped default, but preserve any other
            # value the user deliberately selected.
            if str(data.get("audio_buffer", "")) == "100":
                data["audio_buffer"] = "20"
            
            self._config_data = data
            if migrated_legacy_secret and keychain.available():
                self.save()
            return self._config_data
            
        except json.JSONDecodeError as e:
            self.logger.error(f"Config file is corrupted. JSON decode failed: {e}")
            # Raise exception instead of silent pass, so the app can handle it gracefully.
            raise
        except PermissionError as e:
            self.logger.error(f"Permission denied reading config: {e}")
            raise

    def save(self) -> None:
        """Saves current configuration state to the filesystem."""
        try:
            directory = os.path.dirname(os.path.abspath(self.config_path))
            os.makedirs(directory, mode=0o700, exist_ok=True)
            persisted = copy.deepcopy(self._config_data)
            for secret_name in self.SECRET_KEYS:
                secret_value = persisted.pop(secret_name, "")
                if secret_value:
                    keychain.set(secret_name, secret_value)
                else:
                    keychain.delete(secret_name)
            fd, temp_path = tempfile.mkstemp(prefix=".connectphone-", dir=directory, text=True)
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(persisted, f, indent=4)
                    f.write("\n")
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temp_path, self.config_path)
            except Exception:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
                raise
        except (IOError, PermissionError) as e:
            self.logger.error(f"Failed to save configuration: {e}")
            raise

    def get(self, key: str, default: Any = None) -> Any:
        return self._config_data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._config_data[key] = value

    def update_last_ip(self, ip: str) -> None:
        """Remember an IP while retaining the last known wireless port."""
        if self._is_valid_ip(ip):
            self.update_last_connection(ip, self.get("last_port") or 5555)

    def update_last_connection(self, ip: str, port: int, device_serial: str | None = None) -> None:
        """Persist a manually successful endpoint and enable auto-reconnect."""
        if not self._is_valid_ip(ip) or not self._is_valid_port(port):
            raise ValueError("Invalid wireless endpoint")
        port = int(port)
        self.set("last_ip", ip)
        self.set("last_port", port)

        saved_ips = [item for item in self.get("saved_ips", []) if item != ip]
        self.set("saved_ips", [ip, *saved_ips][:10])

        existing_serial = None
        devices = []
        for item in self.get("saved_devices", []):
            if isinstance(item, dict):
                try:
                    is_same = item.get("ip") == ip and int(item.get("port", -1)) == port
                except (TypeError, ValueError):
                    is_same = False
                if is_same:
                    existing_serial = item.get("device_serial") or None
                if not is_same:
                    devices.append(item)
        # Auto-discovery often knows the endpoint before it has re-read the
        # device serial. Never erase an enrolled identity during that path.
        device_serial = device_serial or existing_serial
        devices.insert(0, {
            "ip": ip,
            "port": port,
            "device_serial": device_serial,
            "auto_reconnect": bool(device_serial),
        })
        self.set("saved_devices", devices[:10])
        self.save()

    def disable_auto_reconnect(self, ip: str = "", port: int | None = None) -> None:
        """Stop reconnect attempts but keep endpoints available in the dropdown."""
        devices = []
        for item in self.get("saved_devices", []):
            if not isinstance(item, dict):
                continue
            same_ip = not ip or item.get("ip") == ip
            try:
                same_port = port is None or int(item.get("port", -1)) == int(port)
            except (TypeError, ValueError):
                same_port = False
            if same_ip and same_port:
                item = dict(item)
                item["auto_reconnect"] = False
            devices.append(item)
        if not devices and self._is_valid_ip(self.get("last_ip", "")):
            devices = [{"ip": self.get("last_ip"), "port": self.get("last_port") or 5555, "auto_reconnect": False}]
        self.set("saved_devices", devices)
        self.save()

    def reconnect_endpoints(self):
        """Return enabled saved endpoints, with legacy-config fallback."""
        endpoints = []
        for item in self.get("saved_devices", []):
            if isinstance(item, dict) and item.get("auto_reconnect", True):
                try:
                    if self._is_valid_ip(item["ip"]) and self._is_valid_port(item["port"]):
                        endpoints.append((item["ip"], int(item["port"])))
                except (KeyError, TypeError, ValueError):
                    continue
        if not endpoints and not self.get("saved_devices") and self._is_valid_ip(self.get("last_ip", "")):
            port = self.get("last_port") or 5555
            if self._is_valid_port(port):
                endpoints.append((self.get("last_ip"), int(port)))
        return endpoints

    @staticmethod
    def _is_valid_ip(ip: str) -> bool:
        try:
            return isinstance(ip, str) and isinstance(ipaddress.ip_address(ip.strip()), ipaddress.IPv4Address)
        except ValueError:
            return False

    @staticmethod
    def _is_valid_port(port: Any) -> bool:
        try:
            return 1 <= int(port) <= 65535
        except (TypeError, ValueError):
            return False
