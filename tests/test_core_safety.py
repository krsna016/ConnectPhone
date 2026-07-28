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


if __name__ == "__main__":
    unittest.main()
