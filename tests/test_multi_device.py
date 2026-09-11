import subprocess
import unittest

from core.multi_device import (
    MirrorSessionManager,
    build_fleet,
    collapse_adb_transports,
    control_devices,
    is_wireless_transport,
    start_emergency_alerts,
    stop_emergency_alerts,
    transport_matches_identity,
)


class FakeProcess:
    def __init__(self):
        self.returncode = None
        self.stdout = type("Output", (), {"readline": lambda self: b""})()

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


class MultiDeviceTests(unittest.TestCase):
    def test_fleet_merges_saved_identity_with_live_wireless_endpoint(self):
        fleet = build_fleet(
            [{"serial": "192.0.2.10:43210", "status": "device", "type": "wireless", "model": "Pixel_9"}],
            [{"ip": "192.0.2.10", "port": 43210, "device_serial": "PIXEL-A", "auto_reconnect": True}],
            selected_identity="PIXEL-A",
        )
        self.assertEqual(len(fleet), 1)
        self.assertEqual(fleet[0]["identity"], "PIXEL-A")
        self.assertEqual(fleet[0]["serial"], "192.0.2.10:43210")
        self.assertEqual(fleet[0]["status"], "online")
        self.assertTrue(fleet[0]["selected"])

    def test_fleet_keeps_two_identical_models_as_distinct_phones(self):
        adb = [
            {"serial": "USB-A", "status": "device", "type": "usb", "model": "Pixel"},
            {"serial": "USB-B", "status": "device", "type": "usb", "model": "Pixel"},
        ]
        fleet = build_fleet(adb, [])
        self.assertEqual({item["serial"] for item in fleet}, {"USB-A", "USB-B"})

    def test_fleet_collapses_mdns_alias_and_ip_transport_for_same_phone(self):
        alias = "adb-8ff8852d-szXllo._adb-tls-connect._tcp"
        fleet = build_fleet(
            [
                {"serial": "192.168.29.172:38787", "status": "device", "type": "wireless", "model": "Redmi"},
                {"serial": alias, "status": "device", "type": "wireless", "model": "Redmi"},
            ],
            [{"ip": "192.168.29.172", "port": 38787, "device_serial": "8ff8852d", "auto_reconnect": True}],
            selected_identity=alias,
        )
        self.assertTrue(transport_matches_identity(alias, "8ff8852d"))
        self.assertEqual(len(fleet), 1)
        self.assertEqual(fleet[0]["identity"], "8ff8852d")
        self.assertEqual(fleet[0]["serial"], "192.168.29.172:38787")
        self.assertTrue(fleet[0]["selected"])

    def test_mdns_adb_alias_is_wireless_not_usb(self):
        alias = "adb-8ff8852d-szXllo._adb-tls-connect._tcp"
        self.assertTrue(is_wireless_transport(alias))
        self.assertTrue(is_wireless_transport("192.168.29.172:38787"))
        self.assertFalse(is_wireless_transport("8ff8852d"))

    def test_attached_device_rows_collapse_two_transports_for_one_phone(self):
        alias = "adb-8ff8852d-szXllo._adb-tls-connect._tcp"
        rows = collapse_adb_transports(
            [
                {"serial": "192.168.29.172:38787", "status": "device", "type": "wireless", "model": "Redmi"},
                {"serial": alias, "status": "device", "type": "wireless", "model": "Redmi"},
            ],
            [{"ip": "192.168.29.172", "port": 38787, "device_serial": "8ff8852d"}],
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["serial"], "192.168.29.172:38787")
        self.assertEqual(rows[0]["transport_count"], 2)
        self.assertEqual(set(rows[0]["aliases"]), {"192.168.29.172:38787", alias})

    def test_mirror_sessions_are_independent_per_serial_and_mode(self):
        commands = []

        def popen(command, **_kwargs):
            commands.append(command)
            return FakeProcess()

        manager = MirrorSessionManager(popen=popen)
        first, created_first = manager.start("USB-A", "screen", tile_index=0)
        second, created_second = manager.start("USB-B", "screen", tile_index=1)
        camera, created_camera = manager.start("USB-A", "camera", options={"resolution": "720p"})

        self.assertTrue(created_first and created_second and created_camera)
        self.assertEqual(len(manager.list()), 3)
        self.assertIn(["scrcpy", "-s", "USB-A"], [command[:3] for command in commands])
        self.assertIn(["scrcpy", "-s", "USB-B"], [command[:3] for command in commands])
        self.assertEqual(len({next(arg for arg in command if arg.startswith("--port=")) for command in commands}), 3)
        self.assertEqual(manager.stop(session_id=first["id"]), 1)
        self.assertEqual(len(manager.list()), 2)

    def test_group_control_routes_each_command_with_explicit_serial(self):
        commands = []

        def runner(command, **_kwargs):
            commands.append(command)
            return subprocess.CompletedProcess(command, 0, "", "")

        results = control_devices(["USB-A", "USB-B"], "home", runner=runner)
        self.assertTrue(all(item["success"] for item in results))
        self.assertEqual({tuple(command[:3]) for command in commands}, {
            ("adb", "-s", "USB-A"),
            ("adb", "-s", "USB-B"),
        })

    def test_call_audio_requests_both_call_directions_and_fails_closed(self):
        manager = MirrorSessionManager()
        command = manager.build_command("USB-A", "call", {}, {}, "Call Audio")
        self.assertIn("--audio-source=voice-call", command)
        self.assertIn("--require-audio", command)
        self.assertIn("--no-video", command)
        self.assertIn("--no-control", command)

    def test_emergency_alert_uses_media_player_and_restores_volume(self):
        commands = []

        def media_runner(command, **_kwargs):
            commands.append(command)
            joined = " ".join(command)
            if command[-1] == "--get":
                stdout = "[V] volume is 6 in range [0..15]"
            elif "resolve-activity" in joined:
                stdout = "com.vendor.player/.AudioPreview\n"
            else:
                stdout = "Starting: Intent"
            return subprocess.CompletedProcess(command, 0, stdout, "")

        self.assertTrue(start_emergency_alerts(["USB-A"], runner=media_runner, sound_path="/tmp/test.wav")[0]["success"])
        self.assertTrue(stop_emergency_alerts(["USB-A"], runner=media_runner)[0]["success"])
        flattened = [" ".join(command) for command in commands]
        self.assertTrue(any("android.intent.action.VIEW" in command for command in flattened))
        self.assertTrue(any("media_session dispatch stop" in command for command in flattened))
        self.assertTrue(any("push /tmp/test.wav" in command for command in flattened))
        self.assertTrue(any("--set 15" in command for command in flattened))
        self.assertTrue(any("--set 6" in command for command in flattened))

    def test_camera_mode_tuning_for_tailscale_and_audio(self):
        manager = MirrorSessionManager()
        cmd_ts = manager.build_command("100.93.0.20:5555", "camera", {}, {"resolution": "1080p", "no_audio": True}, "Live Camera")
        self.assertIn("--video-source=camera", cmd_ts)
        self.assertIn("--video-bit-rate=2M", cmd_ts)
        self.assertIn("--video-buffer=90", cmd_ts)
        self.assertIn("--no-audio", cmd_ts)
        self.assertIn("--no-control", cmd_ts)
        self.assertFalse(any(a.startswith("--audio-buffer=") for a in cmd_ts))

        # Test ultra_low latency mode on Tailscale
        cmd_ts_low = manager.build_command("100.93.0.20:5555", "camera", {}, {"resolution": "1080p", "no_audio": True, "latency_mode": "ultra_low"}, "Live Camera")
        self.assertIn("--video-buffer=50", cmd_ts_low)

        # Test smooth latency mode on Tailscale
        cmd_ts_smooth = manager.build_command("100.93.0.20:5555", "camera", {}, {"resolution": "1080p", "no_audio": True, "latency_mode": "smooth"}, "Live Camera")
        self.assertIn("--video-buffer=150", cmd_ts_smooth)

        # Test Wi-Fi wireless camera buffers
        cmd_wifi = manager.build_command("192.168.1.50:5555", "camera", {}, {"resolution": "1080p", "no_audio": True}, "Live Camera")
        self.assertIn("--video-buffer=25", cmd_wifi)
        cmd_wifi_low = manager.build_command("192.168.1.50:5555", "camera", {}, {"resolution": "1080p", "no_audio": True, "latency_mode": "ultra_low"}, "Live Camera")
        self.assertIn("--video-buffer=15", cmd_wifi_low)

        # Test audio enabled on Tailscale camera uses jitter cushion
        cmd_ts_audio = manager.build_command("100.93.0.20:5555", "camera", {}, {"resolution": "1080p", "no_audio": False}, "Live Camera")
        self.assertIn("--audio-buffer=40", cmd_ts_audio)

        # Test camera orientation: default back camera opens in portrait (display-orientation 90)
        self.assertIn("--display-orientation=90", cmd_ts)
        self.assertIn("--record-orientation=90", cmd_ts)

        # Test front camera with mirror enabled uses flip270 for portrait
        cmd_front = manager.build_command("100.93.0.20:5555", "camera", {"mirror_enabled": True}, {"camera_facing": "front", "resolution": "1080p"}, "Front Camera")
        self.assertIn("--display-orientation=flip270", cmd_front)
        self.assertIn("--record-orientation=270", cmd_front)

        # Test front camera without mirror uses 270 for portrait
        cmd_front_nomirror = manager.build_command("100.93.0.20:5555", "camera", {"mirror_enabled": False}, {"camera_facing": "front", "resolution": "1080p"}, "Front Camera")
        self.assertIn("--display-orientation=270", cmd_front_nomirror)
        self.assertIn("--record-orientation=270", cmd_front_nomirror)

        # Test landscape camera mode does not add portrait display-orientation
        cmd_land = manager.build_command("100.93.0.20:5555", "camera", {}, {"resolution": "1080p", "orientation": "landscape"}, "Landscape Camera")
        self.assertFalse(any(a.startswith("--display-orientation=") for a in cmd_land))

    def test_fleet_collapses_failover_endpoint_without_duplicates(self):
        adb_devices = [
            {"serial": "100.93.0.20:5555", "status": "device", "model": "Redmi Note 13 Pro 5G"},
        ]
        saved_devices = [
            {
                "ip": "192.168.29.222",
                "port": 5555,
                "device_serial": "8ff8852d",
                "fallback_endpoints": ["100.93.0.20:5555"],
                "name": "My Redmi Phone",
            }
        ]
        # Collapse adb transports
        collapsed = collapse_adb_transports(adb_devices, saved_devices)
        self.assertEqual(len(collapsed), 1)
        self.assertEqual(collapsed[0]["identity"], "8ff8852d")

        # Build fleet
        fleet = build_fleet(adb_devices, saved_devices, identity_map={"100.93.0.20:5555": "8ff8852d"})
        self.assertEqual(len(fleet), 1)
        self.assertEqual(fleet[0]["status"], "online")
        self.assertEqual(fleet[0]["serial"], "100.93.0.20:5555")
        self.assertEqual(fleet[0]["identity"], "8ff8852d")
        self.assertEqual(fleet[0]["name"], "My Redmi Phone")


if __name__ == "__main__":
    unittest.main()
