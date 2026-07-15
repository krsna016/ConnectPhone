import os
import json
import logging
from typing import Dict, Any

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
        "last_ip": "192.168.29.201",
        "android_pin": "",
        "biometric_daemon_enabled": False,
        "camera_bitrate": "32M",
        "camera_fps": "60",
        "camera_codec": "h265",
        "audio_sync_delay": "0.80",
        "keyboard_mode": "uhid",
        "mac_mic_device": "default",
        "audio_buffer": "100",
        "device_profile": "generic",
        "saved_ips": []
    }

    def __init__(self, config_path: str):
        # Dependency Injection: The path is passed in, making this class testable without writing to ~/.
        self.config_path = config_path
        self._config_data = self.DEFAULT_CONFIG.copy()
        self.logger = logging.getLogger(__name__)

    def load(self) -> Dict[str, Any]:
        """Loads configuration from the filesystem. Does not swallow critical errors."""
        if not os.path.exists(self.config_path):
            self.logger.info(f"Config not found at {self.config_path}, returning defaults.")
            return self._config_data

        try:
            with open(self.config_path, "r") as f:
                data = json.load(f)
                
            # Merge defaults
            for key, value in self.DEFAULT_CONFIG.items():
                if key not in data:
                    data[key] = value
            
            self._config_data = data
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
            with open(self.config_path, "w") as f:
                json.dump(self._config_data, f, indent=4)
        except (IOError, PermissionError) as e:
            self.logger.error(f"Failed to save configuration: {e}")
            raise

    def get(self, key: str, default: Any = None) -> Any:
        return self._config_data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._config_data[key] = value

    def update_last_ip(self, ip: str) -> None:
        """Domain-specific logic extracted from procedural code."""
        if self._is_valid_ip(ip):
            self.set("last_ip", ip)
            saved_ips = self.get("saved_ips", [])
            if ip in saved_ips:
                saved_ips.remove(ip)
            saved_ips.insert(0, ip)
            self.set("saved_ips", saved_ips[:5])
            self.save()

    @staticmethod
    def _is_valid_ip(ip: str) -> bool:
        parts = ip.split('.')
        if len(parts) == 4:
            try:
                return all(0 <= int(part) <= 255 for part in parts)
            except ValueError:
                return False
        return False
