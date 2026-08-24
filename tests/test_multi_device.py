import subprocess
import unittest

from core.multi_device import MirrorSessionManager, build_fleet, control_devices


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


if __name__ == "__main__":
    unittest.main()
