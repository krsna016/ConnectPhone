import tempfile
import unittest
import os
import subprocess
from core.tailscale import is_tailscale_ip, is_valid_host_or_ip, get_tailscale_status
from core.config_manager import ConfigurationManager
from core.wireless_pairing import pair_with_secret


class TailscaleSupportTests(unittest.TestCase):
    def test_is_tailscale_ip_cgnat(self):
        # 100.64.0.0/10 range: 100.64.0.0 to 100.127.255.255
        self.assertTrue(is_tailscale_ip("100.64.0.1"))
        self.assertTrue(is_tailscale_ip("100.85.12.34"))
        self.assertTrue(is_tailscale_ip("100.127.255.254"))

        # Outside Tailscale CGNAT range
        self.assertFalse(is_tailscale_ip("192.168.1.1"))
        self.assertFalse(is_tailscale_ip("10.0.0.1"))
        self.assertFalse(is_tailscale_ip("127.0.0.1"))
        self.assertFalse(is_tailscale_ip("100.63.255.255"))
        self.assertFalse(is_tailscale_ip("100.128.0.1"))

    def test_is_tailscale_magicdns(self):
        self.assertTrue(is_tailscale_ip("phone.ts.net"))
        self.assertTrue(is_tailscale_ip("pixel7.tail-net.ts.net"))
        self.assertTrue(is_tailscale_ip("phone.tailscale.net"))

        self.assertFalse(is_tailscale_ip("google.com"))
        self.assertFalse(is_tailscale_ip("phone.example.ts.net.fake"))
        self.assertFalse(is_tailscale_ip(""))
        self.assertFalse(is_tailscale_ip(None))

    def test_is_valid_host_or_ip(self):
        self.assertTrue(is_valid_host_or_ip("192.168.1.10"))
        self.assertTrue(is_valid_host_or_ip("100.85.12.34"))
        self.assertTrue(is_valid_host_or_ip("phone.ts.net"))
        self.assertTrue(is_valid_host_or_ip("my-phone.home.arpa"))

        self.assertFalse(is_valid_host_or_ip("bad"))
        self.assertFalse(is_valid_host_or_ip("0.0.0.0"))
        self.assertFalse(is_valid_host_or_ip("999.999.999.999"))
        self.assertFalse(is_valid_host_or_ip(""))
        self.assertFalse(is_valid_host_or_ip("   "))
        self.assertFalse(is_valid_host_or_ip(None))

    def test_get_tailscale_status_structure(self):
        status = get_tailscale_status(force_refresh=True)
        self.assertIsInstance(status, dict)
        for key in ("installed", "running", "mac_ip", "peers", "android_peers"):
            self.assertIn(key, status)
        self.assertIsInstance(status["installed"], bool)
        self.assertIsInstance(status["running"], bool)
        self.assertIsInstance(status["peers"], list)
        self.assertIsInstance(status["android_peers"], list)

    def test_config_manager_persists_tailscale_device(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "config.json")
            manager = ConfigurationManager(path)
            manager.load()
            manager.set("tailscale_remote_profile", True)
            manager.update_last_connection("100.85.12.34", 5555, "TAILSCALE-PHONE-1")
            manager.save()

            loaded = ConfigurationManager(path).load()
            self.assertTrue(loaded["tailscale_remote_profile"])
            self.assertEqual(loaded["last_ip"], "100.85.12.34")
            self.assertEqual(loaded["last_port"], 5555)
            self.assertEqual(len(loaded["saved_devices"]), 1)
            self.assertEqual(loaded["saved_devices"][0]["ip"], "100.85.12.34")
            self.assertEqual(loaded["saved_devices"][0]["device_serial"], "TAILSCALE-PHONE-1")

    def test_config_manager_persists_magicdns_device(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "config.json")
            manager = ConfigurationManager(path)
            manager.load()
            manager.update_last_connection("pixel7.ts.net", 42100, "TAILSCALE-MAGICDNS-1")
            manager.save()

            loaded = ConfigurationManager(path).load()
            self.assertEqual(loaded["last_ip"], "pixel7.ts.net")
            self.assertEqual(loaded["last_port"], 42100)
            self.assertEqual(loaded["saved_devices"][0]["ip"], "pixel7.ts.net")

    def test_wireless_pairing_with_tailscale_endpoints(self):
        observed = []

        def runner(command, **kwargs):
            observed.append(command)
            return subprocess.CompletedProcess(command, 0, "Successfully paired", "")

        # Tailscale IP
        success, _ = pair_with_secret("100.85.12.34:43210", "Secret123", runner=runner)
        self.assertTrue(success)
        self.assertEqual(observed[-1], ["adb", "pair", "100.85.12.34:43210"])

        # Tailscale MagicDNS
        success, _ = pair_with_secret("pixel.ts.net:43210", "Secret123", runner=runner)
        self.assertTrue(success)
        self.assertEqual(observed[-1], ["adb", "pair", "pixel.ts.net:43210"])

        # Arbitrary non-tailscale hostname is rejected
        success, _ = pair_with_secret("host.example:43210", "Secret123", runner=runner)
        self.assertFalse(success)


if __name__ == "__main__":
    unittest.main()
