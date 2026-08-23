import subprocess
import unittest

from core.adb_lifecycle import AdbLifecycle


class FakeRunner:
    def __init__(self, devices_output="List of devices attached\n", identities=None):
        self.devices_output = devices_output
        self.identities = identities or {}
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        if args[1:] == ["devices"]:
            stdout = self.devices_output
        elif len(args) >= 6 and args[1] == "-s" and args[3:6] == ["shell", "getprop", "ro.serialno"]:
            stdout = self.identities.get(args[2], "")
        else:
            stdout = ""
        return subprocess.CompletedProcess(args, 0, stdout, "")


class AdbLifecycleTests(unittest.TestCase):
    def test_disconnects_owned_endpoint_and_stops_owned_idle_daemon(self):
        runner = FakeRunner()
        lifecycle = AdbLifecycle(runner=runner, daemon_probe=lambda: False)
        lifecycle.register("192.0.2.10:43210")

        self.assertTrue(lifecycle.cleanup())
        self.assertIn(["adb", "disconnect", "192.0.2.10:43210"], runner.calls)
        self.assertIn(["adb", "kill-server"], runner.calls)

    def test_never_stops_daemon_that_predated_app(self):
        runner = FakeRunner()
        lifecycle = AdbLifecycle(runner=runner, daemon_probe=lambda: True)
        lifecycle.register("192.0.2.10:43210")

        self.assertFalse(lifecycle.cleanup())
        self.assertNotIn(["adb", "kill-server"], runner.calls)

    def test_keeps_owned_daemon_when_unrelated_transport_remains(self):
        runner = FakeRunner("List of devices attached\nemulator-5554 device\n")
        lifecycle = AdbLifecycle(runner=runner, daemon_probe=lambda: False)

        self.assertFalse(lifecycle.cleanup())
        self.assertNotIn(["adb", "kill-server"], runner.calls)

    def test_stops_owned_daemon_with_matching_bonjour_transport(self):
        transport = "adb-SERIAL-._adb-tls-connect._tcp"
        runner = FakeRunner(
            f"List of devices attached\n{transport} device\n",
            identities={transport: "SERIAL-A"},
        )
        lifecycle = AdbLifecycle(runner=runner, daemon_probe=lambda: False)

        self.assertTrue(lifecycle.cleanup(owned_serials={"SERIAL-A"}))
        self.assertIn(["adb", "kill-server"], runner.calls)


if __name__ == "__main__":
    unittest.main()
