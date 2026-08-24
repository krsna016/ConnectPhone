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


if __name__ == "__main__":
    unittest.main()
