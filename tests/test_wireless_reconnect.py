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
            # A background reconnect refreshes this phone's rotating endpoint
            # without stealing the user's selected/last-active target.
            self.assertEqual(saved["last_port"], 5555)
            self.assertEqual(saved["saved_devices"][0]["port"], 43210)

    def test_rotated_port_is_recovered_without_mdns_or_new_pairing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._config(directory, port=38787, serial="SERIAL-A")
            commands = []

            def runner(command, **_kwargs):
                commands.append(command)
                if command[1:3] == ["connect", "192.0.2.10:43210"]:
                    return subprocess.CompletedProcess(command, 0, "connected to 192.0.2.10:43210", "")
                if "getprop" in command and "192.0.2.10:43210" in command:
                    return subprocess.CompletedProcess(command, 0, "SERIAL-A\n", "")
                return subprocess.CompletedProcess(command, 1, "", "connection refused")

            reconnector = AutoReconnector(
                path,
                scanner=FakeScanner(),
                command_runner=runner,
                port_discoverer=lambda ip, stop_event: [43210],
            )
            self.assertTrue(reconnector._recover_rotated_port({
                "ip": "192.0.2.10", "port": 38787, "serial": "SERIAL-A",
            }))
            self.assertIn(["adb", "connect", "192.0.2.10:43210"], commands)
            with open(path, encoding="utf-8") as handle:
                saved = json.load(handle)
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

    def test_temporarily_unavailable_identity_is_not_marked_connected(self):
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
        self.assertNotIn(endpoint, reconnector.connected_endpoints)
        self.assertEqual(reconnector._pending_identity[endpoint], "SERIAL-A")
        self.assertNotIn(["adb", "disconnect", endpoint], commands)
        connect_count = commands.count(["adb", "connect", endpoint])
        reconnector._next_attempt.clear()
        self.assertFalse(reconnector._try_connect(endpoint, "SERIAL-A"))
        self.assertEqual(commands.count(["adb", "connect", endpoint]), connect_count + 1)

    def test_stale_adb_daemon_is_restarted_only_after_repeated_reachable_failures(self):
        commands = []

        def runner(command, **_kwargs):
            commands.append(command)
            return subprocess.CompletedProcess(command, 1, "", "No route to host")

        reconnector = AutoReconnector("/nonexistent", scanner=FakeScanner(), command_runner=runner)
        # A newly booted host can have a low monotonic clock. Missing cooldown
        # entries must not be confused with a reset at boot time.
        with mock.patch("core.auto_reconnect.time.monotonic", return_value=10.0), \
             mock.patch.object(reconnector, "_port_open", return_value=True):
            for _ in range(5):
                reconnector._next_attempt.clear()
                self.assertFalse(reconnector._try_connect("192.0.2.10:43210", "SERIAL-A"))
        self.assertEqual(commands.count(["adb", "kill-server"]), 1)
        self.assertEqual(commands.count(["adb", "start-server"]), 1)

    def test_stale_daemon_is_not_restarted_during_transfer(self):
        commands = []

        def runner(command, **_kwargs):
            commands.append(command)
            return subprocess.CompletedProcess(command, 1, "", "No route to host")

        reconnector = AutoReconnector(
            "/nonexistent",
            scanner=FakeScanner(),
            command_runner=runner,
            busy_check=lambda: True,
        )
        with mock.patch.object(reconnector, "_port_open", return_value=True):
            for _ in range(5):
                reconnector._next_attempt.clear()
                self.assertFalse(reconnector._try_connect("192.0.2.10:43210", "SERIAL-A"))
        self.assertNotIn(["adb", "kill-server"], commands)


if __name__ == "__main__":
    unittest.main()
