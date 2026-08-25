import os
import subprocess
import tempfile
import unittest

from core.companion_installer import find_companion_apk, install_companion, PHONE_APK_PATH


class Runner:
    def __init__(self, results):
        self.results = list(results)
        self.commands = []
    def __call__(self, command, **kwargs):
        self.commands.append(command)
        return self.results.pop(0)


class CompanionInstallerTests(unittest.TestCase):
    def apk(self):
        handle = tempfile.NamedTemporaryFile(suffix=".apk", delete=False)
        handle.close()
        self.addCleanup(lambda: os.path.exists(handle.name) and os.unlink(handle.name))
        return handle.name

    def test_installs_to_explicit_serial(self):
        runner = Runner([subprocess.CompletedProcess([], 0, "Success\n", "")])
        result = install_companion("phone-123", self.apk(), runner)
        self.assertTrue(result["installed"])
        self.assertEqual(runner.commands[0][:4], ["adb", "-s", "phone-123", "install"])

    def test_xiaomi_restriction_copies_apk_to_downloads(self):
        runner = Runner([
            subprocess.CompletedProcess([], 1, "", "Failure [INSTALL_FAILED_USER_RESTRICTED]"),
            subprocess.CompletedProcess([], 0, "1 file pushed", ""),
            subprocess.CompletedProcess([], 0, "Starting: Intent", ""),
        ])
        result = install_companion("192.0.2.10:5555", self.apk(), runner)
        self.assertTrue(result["copied"])
        self.assertTrue(result["picker_opened"])
        self.assertEqual(runner.commands[1][-1], PHONE_APK_PATH)
        self.assertIn("android.intent.action.OPEN_DOCUMENT", runner.commands[2])

    def test_finds_packaged_then_source_build_apk(self):
        with tempfile.TemporaryDirectory() as directory:
            source_apk = os.path.join(directory, "companion-android", "app", "build", "outputs", "apk", "debug", "app-debug.apk")
            os.makedirs(os.path.dirname(source_apk))
            open(source_apk, "wb").close()
            self.assertEqual(find_companion_apk(directory), os.path.realpath(source_apk))
            packaged_apk = os.path.join(directory, "companion", "ConnectPhone-Companion.apk")
            os.makedirs(os.path.dirname(packaged_apk))
            open(packaged_apk, "wb").close()
            self.assertEqual(find_companion_apk(directory), os.path.realpath(packaged_apk))

    def test_signature_conflict_does_not_destroy_existing_install(self):
        runner = Runner([subprocess.CompletedProcess([], 1, "", "Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE]")])
        result = install_companion("phone-123", self.apk(), runner)
        self.assertFalse(result["installed"])
        self.assertEqual(len(runner.commands), 1)

    def test_rejects_untrusted_serial_or_missing_apk(self):
        with self.assertRaises(ValueError):
            install_companion("phone; reboot", self.apk(), Runner([]))
        with self.assertRaises(FileNotFoundError):
            install_companion("phone", "/missing/app.apk", Runner([]))


if __name__ == "__main__":
    unittest.main()
