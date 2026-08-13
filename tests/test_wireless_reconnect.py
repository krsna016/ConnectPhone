import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from core.auto_reconnect import AutoReconnector
from core.config_manager import ConfigurationManager


class FakeScanner:
    def stop(self):
        pass


class WirelessReconnectTests(unittest.TestCase):
    def _config(self, directory, port=5555, serial="SERIAL-A"):
        path = os.path.join(directory, "config.json")
        with mock.patch("core.config_manager.keychain.available", return_value=False):
            manager = ConfigurationManager(path)
            manager.load()
            manager.update_last_connection("192.0.2.10", port, serial)
        return path

    def test_rotated_mdns_port_is_identity_checked_and_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._config(directory)

            def runner(command, **_kwargs):
                if command[1:3] == ["connect", "192.0.2.10:43210"]:
                    return subprocess.CompletedProcess(command, 0, "connected to 192.0.2.10:43210", "")
                if "getprop" in command:
                    return subprocess.CompletedProcess(command, 0, "SERIAL-A\n", "")
                return subprocess.CompletedProcess(command, 0, "", "")

            reconnector = AutoReconnector(path, scanner=FakeScanner(), command_runner=runner)
            self.assertTrue(reconnector._try_connect("192.0.2.10:43210", "SERIAL-A"))
            with open(path, encoding="utf-8") as handle:
                saved = json.load(handle)
            self.assertEqual(saved["last_port"], 43210)
            self.assertEqual(saved["saved_devices"][0]["port"], 43210)

    def test_identity_mismatch_disconnects_and_does_not_persist(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._config(directory)
            commands = []

            def runner(command, **_kwargs):
                commands.append(command)
                if command[1] == "connect":
                    return subprocess.CompletedProcess(command, 0, "connected to target", "")
                if "getprop" in command:
                    return subprocess.CompletedProcess(command, 0, "SERIAL-B\n", "")
                return subprocess.CompletedProcess(command, 0, "", "")

            reconnector = AutoReconnector(path, scanner=FakeScanner(), command_runner=runner)
            self.assertFalse(reconnector._try_connect("192.0.2.10:43210", "SERIAL-A"))
            self.assertIn(["adb", "disconnect", "192.0.2.10:43210"], commands)
            with open(path, encoding="utf-8") as handle:
                saved = json.load(handle)
            self.assertEqual(saved["last_port"], 5555)

    def test_unpinned_endpoint_is_never_connected(self):
        commands = []
        reconnector = AutoReconnector("/nonexistent", scanner=FakeScanner(), command_runner=lambda command, **kwargs: commands.append(command))
        self.assertFalse(reconnector._try_connect("192.0.2.10:43210", None))
        self.assertEqual(commands, [])

    def test_temporarily_unavailable_identity_does_not_disconnect_or_reconnect(self):
        commands = []

        def runner(command, **_kwargs):
            commands.append(command)
            if command[1] == "connect":
                return subprocess.CompletedProcess(command, 0, "connected to target", "")
            if "getprop" in command:
                return subprocess.CompletedProcess(command, 0, "", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        reconnector = AutoReconnector("/nonexistent", scanner=FakeScanner(), command_runner=runner)
        endpoint = "192.0.2.10:43210"
        self.assertFalse(reconnector._try_connect(endpoint, "SERIAL-A"))
        self.assertIn(endpoint, reconnector.connected_endpoints)
        self.assertEqual(reconnector._pending_identity[endpoint], "SERIAL-A")
        self.assertNotIn(["adb", "disconnect", endpoint], commands)
        connect_count = commands.count(["adb", "connect", endpoint])
        reconnector._next_attempt.clear()
        if endpoint not in reconnector.connected_endpoints:
            reconnector._try_connect(endpoint, "SERIAL-A")
        self.assertEqual(commands.count(["adb", "connect", endpoint]), connect_count)

    def test_stale_adb_daemon_is_restarted_only_after_repeated_reachable_failures(self):
        commands = []

        def runner(command, **_kwargs):
            commands.append(command)
            return subprocess.CompletedProcess(command, 1, "", "No route to host")

        reconnector = AutoReconnector("/nonexistent", scanner=FakeScanner(), command_runner=runner)
        with mock.patch.object(reconnector, "_port_open", return_value=True):
            for _ in range(3):
                reconnector._next_attempt.clear()
                self.assertFalse(reconnector._try_connect("192.0.2.10:43210", "SERIAL-A"))
        self.assertEqual(commands.count(["adb", "kill-server"]), 1)
        self.assertEqual(commands.count(["adb", "start-server"]), 1)


if __name__ == "__main__":
    unittest.main()
