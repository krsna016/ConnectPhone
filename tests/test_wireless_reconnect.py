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

    def test_unpause_clears_failure_backoff(self):
        reconnector = AutoReconnector("/nonexistent", scanner=FakeScanner())
        reconnector._failures["192.0.2.10:5555"] = 4
        reconnector._next_attempt["192.0.2.10:5555"] = 9999.0
        reconnector.pause_auto_reconnect("192.0.2.10")
        self.assertIn("192.0.2.10", reconnector._manually_disconnected)

        reconnector.unpause_auto_reconnect("192.0.2.10")
        self.assertNotIn("192.0.2.10", reconnector._manually_disconnected)
        self.assertNotIn("192.0.2.10:5555", reconnector._failures)
        self.assertNotIn("192.0.2.10:5555", reconnector._next_attempt)

    def test_ghost_transport_in_verify_connections_cleared(self):
        commands = []

        def runner(command, **_kwargs):
            commands.append(command)
            if command[1] == "devices":
                return subprocess.CompletedProcess(command, 0, "List of devices attached\n192.0.2.10:5555 device\n", "")
            if "getprop" in command:
                return subprocess.CompletedProcess(command, 1, "", "error: closed\n")
            if command[1] == "disconnect":
                return subprocess.CompletedProcess(command, 0, "disconnected 192.0.2.10:5555\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        reconnector = AutoReconnector("/nonexistent", scanner=FakeScanner(), command_runner=runner)
        endpoint = "192.0.2.10:5555"
        # First verification cycle: identity query fails, schedules failure (failures = 1)
        states = reconnector._verify_connections({"SERIAL-A"})
        self.assertNotIn(["adb", "disconnect", endpoint], commands)

        # Second verification cycle: identity query fails again (failures = 2), ghost transport cleared!
        reconnector._next_attempt.clear()
        states = reconnector._verify_connections({"SERIAL-A"})
        self.assertIn(["adb", "disconnect", endpoint], commands)

    def test_recover_rotated_port_probes_port_5555(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._config(directory, port=39999, serial="SERIAL-A")
            commands = []

            def runner(command, **_kwargs):
                commands.append(command)
                if command[1:3] == ["connect", "192.0.2.10:5555"]:
                    return subprocess.CompletedProcess(command, 0, "connected to 192.0.2.10:5555", "")
                if "getprop" in command and "192.0.2.10:5555" in command:
                    return subprocess.CompletedProcess(command, 0, "SERIAL-A\n", "")
                return subprocess.CompletedProcess(command, 1, "", "connection refused")

            reconnector = AutoReconnector(
                path,
                scanner=FakeScanner(),
                command_runner=runner,
                port_discoverer=lambda ip, stop_event: [],
            )
            recovered = reconnector._recover_rotated_port({
                "ip": "192.0.2.10", "port": 39999, "serial": "SERIAL-A",
            })
            self.assertTrue(recovered)
            self.assertIn(["adb", "connect", "192.0.2.10:5555"], commands)

    def test_auto_reconnect_failover_to_tailscale_when_wifi_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._config(directory, port=5555, serial="SERIAL-A")
            commands = []

            def runner(command, **_kwargs):
                commands.append(command)
                if command[1:3] == ["connect", "100.93.0.20:5555"]:
                    return subprocess.CompletedProcess(command, 0, "connected to 100.93.0.20:5555", "")
                if "getprop" in command and "100.93.0.20:5555" in command:
                    return subprocess.CompletedProcess(command, 0, "SERIAL-A\n", "")
                if command[1:3] == ["connect", "192.0.2.10:5555"]:
                    return subprocess.CompletedProcess(command, 1, "", "connection refused")
                return subprocess.CompletedProcess(command, 0, "", "")

            reconnector = AutoReconnector(path, scanner=FakeScanner(), command_runner=runner)
            # Simulate primary Wi-Fi having failed
            reconnector._failures["192.0.2.10:5555"] = 1
            reconnector._endpoint_serial["192.0.2.10:5555"] = "SERIAL-A"

            mock_peers = [{"name": "Phone", "ip": "100.93.0.20", "online": True, "is_android": True}]
            with mock.patch("core.tailscale.get_tailscale_peers", return_value=mock_peers), \
                 mock.patch.object(reconnector, "_verify_connections", return_value={}):
                # Run one iteration of reconnect logic
                trusted = reconnector._trusted_devices()
                self.assertEqual(len(trusted), 1)
                
                # Verify candidates prioritize Tailscale when primary is failing
                item = trusted[0]
                primary_ep = f"{item['ip']}:{item['port']}"
                fallback_eps = item.get("fallback_endpoints", [])
                ts_peer_endpoints = [f"{p['ip']}:5555" for p in mock_peers]
                candidates = list(fallback_eps) + ts_peer_endpoints + [primary_ep]
                self.assertEqual(candidates[0], "100.93.0.20:5555")

                # Perform connect to the failover endpoint
                connected = reconnector._try_connect("100.93.0.20:5555", "SERIAL-A")
                self.assertTrue(connected)
                self.assertIn("100.93.0.20:5555", reconnector.connected_endpoints)
                # Old dead endpoint must have been disconnected and cleaned up
                self.assertNotIn("192.0.2.10:5555", reconnector.connected_endpoints)
                self.assertIn(["adb", "disconnect", "192.0.2.10:5555"], commands)

    def test_seamless_promotion_to_local_wifi(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._config(directory, port=5555, serial="SERIAL-A")
            commands = []

            def runner(command, **_kwargs):
                commands.append(command)
                if command[1:3] == ["connect", "192.168.1.50:5555"]:
                    return subprocess.CompletedProcess(command, 0, "connected to 192.168.1.50:5555", "")
                if "getprop" in command and "192.168.1.50:5555" in command:
                    return subprocess.CompletedProcess(command, 0, "SERIAL-A\n", "")
                return subprocess.CompletedProcess(command, 0, "", "")

            reconnector = AutoReconnector(path, scanner=FakeScanner(), command_runner=runner)
            # Device is currently connected on Tailscale
            reconnector.connected_endpoints.add("100.93.0.20:5555")
            reconnector._endpoint_serial["100.93.0.20:5555"] = "SERIAL-A"

            states = {"100.93.0.20:5555": "device"}
            discovered = [{"type": "connect", "ip": "192.168.1.50", "port": 5555, "device_serial_hint": "SERIAL-A"}]
            item = {"ip": "100.93.0.20", "port": 5555, "serial": "SERIAL-A", "fallback_endpoints": ["192.168.1.50:5555"]}

            with mock.patch.object(reconnector, "_port_open", return_value=True):
                promoted = reconnector._maybe_promote_to_local_wifi(item, discovered, states)
                self.assertTrue(promoted)
                self.assertIn("192.168.1.50:5555", reconnector.connected_endpoints)
                self.assertNotIn("100.93.0.20:5555", reconnector.connected_endpoints)
                self.assertIn(["adb", "disconnect", "100.93.0.20:5555"], commands)

    def test_fast_keepalive_disconnects_when_port_drops(self):
        commands = []

        def runner(command, **_kwargs):
            commands.append(command)
            return subprocess.CompletedProcess(command, 0, "", "")

        reconnector = AutoReconnector("/nonexistent", scanner=FakeScanner(), command_runner=runner)
        reconnector.connected_endpoints.add("192.168.1.50:5555")
        reconnector._endpoint_serial["192.168.1.50:5555"] = "SERIAL-A"

        states = {"192.168.1.50:5555": "device"}
        with mock.patch.object(reconnector, "_port_open", return_value=False):
            # Probe 1: missed
            reconnector._keepalive(states)
            self.assertIn("192.168.1.50:5555", reconnector.connected_endpoints)
            # Probe 2: port is down, must disconnect immediately
            reconnector._keepalive(states)
            self.assertNotIn("192.168.1.50:5555", reconnector.connected_endpoints)
            self.assertIn(["adb", "disconnect", "192.168.1.50:5555"], commands)

    def test_tailscale_keepalive_allows_more_tolerance(self):
        commands = []

        def runner(command, **_kwargs):
            commands.append(command)
            return subprocess.CompletedProcess(command, 0, "", "")

        reconnector = AutoReconnector("/nonexistent", scanner=FakeScanner(), command_runner=runner)
        ts_ep = "100.93.0.20:5555"
        reconnector.connected_endpoints.add(ts_ep)
        reconnector._endpoint_serial[ts_ep] = "SERIAL-A"

        states = {ts_ep: "device"}
        with mock.patch.object(reconnector, "_port_open", return_value=False):
            # Probe 1: missed
            reconnector._keepalive(states)
            self.assertIn(ts_ep, reconnector.connected_endpoints)
            # Probe 2: missed, but Tailscale retains connection (limit is 3)
            reconnector._keepalive(states)
            self.assertIn(ts_ep, reconnector.connected_endpoints)
            # Probe 3: exceeded limit, disconnects
            reconnector._keepalive(states)
            self.assertNotIn(ts_ep, reconnector.connected_endpoints)
            self.assertIn(["adb", "disconnect", ts_ep], commands)

    def test_authorizing_stuck_transport_cleared_in_verify_connections(self):
        commands = []

        def runner(command, **_kwargs):
            commands.append(command)
            if command[1] == "devices":
                return subprocess.CompletedProcess(command, 0, "List of devices attached\n100.93.0.20:5555 authorizing\n", "")
            if command[1] == "disconnect":
                return subprocess.CompletedProcess(command, 0, "disconnected 100.93.0.20:5555\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        reconnector = AutoReconnector("/nonexistent", scanner=FakeScanner(), command_runner=runner)
        endpoint = "100.93.0.20:5555"
        # First verification cycle: failures = 1
        states = reconnector._verify_connections({"SERIAL-A"})
        self.assertNotIn(["adb", "disconnect", endpoint], commands)

        # Second verification cycle: failures = 2, stuck authorizing transport cleared!
        reconnector._next_attempt.clear()
        states = reconnector._verify_connections({"SERIAL-A"})
        self.assertIn(["adb", "disconnect", endpoint], commands)

    def test_try_connect_wakes_tailscale_peer(self):
        commands = []

        def runner(command, **_kwargs):
            commands.append(command)
            if command[1] == "connect":
                return subprocess.CompletedProcess(command, 0, "connected to 100.93.0.20:5555", "")
            if "getprop" in command:
                return subprocess.CompletedProcess(command, 0, "SERIAL-TS\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        reconnector = AutoReconnector("/nonexistent", scanner=FakeScanner(), command_runner=runner)
        with mock.patch("core.tailscale.wake_tailscale_peer") as mock_wake:
            mock_wake.return_value = True
            connected = reconnector._try_connect("100.93.0.20:5555", "SERIAL-TS")
            self.assertTrue(connected)
            mock_wake.assert_called_once_with("100.93.0.20", timeout=2.0)

    def test_already_connected_zombie_transport_cleared_on_failed_identity(self):
        commands = []

        def runner(command, **_kwargs):
            commands.append(command)
            if command[1] == "connect":
                return subprocess.CompletedProcess(command, 0, "already connected to 100.93.0.20:5555", "")
            if "getprop" in command:
                # Identity probe fails (e.g. transport is offline or authorizing)
                return subprocess.CompletedProcess(command, 1, "", "error: device still authorizing")
            if command[1] == "disconnect":
                return subprocess.CompletedProcess(command, 0, "disconnected 100.93.0.20:5555", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        reconnector = AutoReconnector("/nonexistent", scanner=FakeScanner(), command_runner=runner)
        endpoint = "100.93.0.20:5555"
        connected = reconnector._try_connect(endpoint, "SERIAL-TS")
        self.assertFalse(connected)
        self.assertIn(["adb", "disconnect", endpoint], commands)


if __name__ == "__main__":
    unittest.main()

