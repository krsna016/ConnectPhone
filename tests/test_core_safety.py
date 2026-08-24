import json
import os
import tempfile
import unittest

from core.config_manager import ConfigurationManager


class ConfigurationSafetyTests(unittest.TestCase):
    def test_identity_is_preserved_when_port_is_updated_without_serial(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = ConfigurationManager(os.path.join(directory, "config.json"))
            manager.load()
            manager.update_last_connection("192.0.2.10", 5555, "SERIAL-A")
            manager.update_last_connection("192.0.2.10", 5555)
            with open(manager.config_path, encoding="utf-8") as handle:
                saved = json.load(handle)
            self.assertEqual(saved["saved_devices"][0]["device_serial"], "SERIAL-A")
            self.assertTrue(saved["saved_devices"][0]["auto_reconnect"])

    def test_unknown_endpoint_is_not_auto_enrolled(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = ConfigurationManager(os.path.join(directory, "config.json"))
            manager.load()
            manager.update_last_connection("192.0.2.10", 5555)
            with open(manager.config_path, encoding="utf-8") as handle:
                saved = json.load(handle)
            self.assertIsNone(saved["saved_devices"][0]["device_serial"])
            self.assertFalse(saved["saved_devices"][0]["auto_reconnect"])

    def test_endpoint_string_is_never_saved_as_physical_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = ConfigurationManager(os.path.join(directory, "config.json"))
            manager.load()
            manager.update_last_connection("192.0.2.10", 43210, "192.0.2.10:43210")
            saved = manager.load()["saved_devices"][0]
            self.assertIsNone(saved["device_serial"])
            self.assertFalse(saved["auto_reconnect"])

    def test_legacy_endpoint_identity_migrates_to_unambiguous_selected_serial(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "config.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({
                    "selected_device_serial": "HARDWARE-123",
                    "saved_devices": [{
                        "ip": "192.0.2.10", "port": 43210,
                        "device_serial": "192.0.2.10:43210", "auto_reconnect": True,
                    }],
                }, handle)
            loaded = ConfigurationManager(path).load()
            self.assertEqual(loaded["saved_devices"][0]["device_serial"], "HARDWARE-123")
            self.assertTrue(loaded["saved_devices"][0]["auto_reconnect"])

    def test_mdns_alias_selection_migrates_to_saved_hardware_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "config.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({
                    "selected_device_serial": "adb-8ff8852d-szXllo._adb-tls-connect._tcp",
                    "saved_devices": [{
                        "ip": "192.168.29.172", "port": 38787,
                        "device_serial": "8ff8852d", "auto_reconnect": True,
                    }],
                }, handle)
            loaded = ConfigurationManager(path).load()
            self.assertEqual(loaded["selected_device_serial"], "8ff8852d")
            with open(path, encoding="utf-8") as handle:
                persisted = json.load(handle)
            self.assertEqual(persisted["selected_device_serial"], "8ff8852d")

    def test_invalid_endpoint_is_rejected(self):
        manager = ConfigurationManager(os.path.join(tempfile.gettempdir(), "unused.json"))
        with self.assertRaises(ValueError):
            manager.update_last_connection("not-an-ip", 5555)

    def test_background_endpoint_refresh_preserves_selection_and_name(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = ConfigurationManager(os.path.join(directory, "config.json"))
            manager.load()
            manager.update_last_connection("192.0.2.10", 5555, "SERIAL-A")
            manager.rename_device("SERIAL-A", "Studio Phone")
            manager.update_device_endpoint("192.0.2.10", 43210, "SERIAL-A")
            loaded = manager.load()
            self.assertEqual(loaded["selected_device_serial"], "SERIAL-A")
            self.assertEqual(loaded["last_port"], 5555)
            self.assertEqual(loaded["saved_devices"][0]["port"], 43210)
            self.assertEqual(loaded["saved_devices"][0]["name"], "Studio Phone")

    def test_forgetting_one_phone_keeps_other_enrollments(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = ConfigurationManager(os.path.join(directory, "config.json"))
            manager.load()
            manager.update_last_connection("192.0.2.10", 5555, "SERIAL-A")
            manager.update_last_connection("192.0.2.11", 5555, "SERIAL-B")
            removed = manager.forget_device("SERIAL-A")
            self.assertEqual(removed["device_serial"], "SERIAL-A")
            self.assertEqual(
                [item["device_serial"] for item in manager.load()["saved_devices"]],
                ["SERIAL-B"],
            )

    def test_corrupt_config_is_quarantined_and_rebuilt(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "config.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{broken")
            manager = ConfigurationManager(path)
            loaded = manager.load()
            self.assertEqual(loaded["saved_devices"], [])
            self.assertTrue(os.path.exists(path))
            self.assertTrue(any(name.startswith("config.json.corrupt-") for name in os.listdir(directory)))

    def test_valid_json_with_malformed_endpoints_is_sanitized(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "config.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({
                    "last_ip": {"not": "a string"},
                    "last_port": "invalid",
                    "saved_ips": ["192.0.2.10", "bad"],
                    "selected_device_serial": "bad;serial",
                    "saved_devices": [
                        {"ip": "192.0.2.10", "port": 43210, "device_serial": "SERIAL-A", "name": "Phone"},
                        {"ip": "bad", "port": -1, "device_serial": "SERIAL-B"},
                        "not-an-object",
                    ],
                }, handle)
            loaded = ConfigurationManager(path).load()
            self.assertEqual(loaded["last_ip"], "")
            self.assertIsNone(loaded["last_port"])
            self.assertEqual(loaded["saved_ips"], ["192.0.2.10"])
            self.assertEqual(loaded["selected_device_serial"], "")
            self.assertEqual(loaded["saved_devices"], [{
                "ip": "192.0.2.10", "port": 43210, "device_serial": "SERIAL-A",
                "auto_reconnect": True, "name": "Phone",
            }])


if __name__ == "__main__":
    unittest.main()
