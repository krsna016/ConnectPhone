import subprocess
import unittest

from core.wireless_pairing import pair_with_code, pair_with_secret


class WirelessPairingTests(unittest.TestCase):
    def test_code_is_sent_only_over_stdin_and_redacted(self):
        observed = {}

        def runner(command, **kwargs):
            observed["command"] = command
            observed["input"] = kwargs["input"]
            return subprocess.CompletedProcess(command, 0, "Successfully paired using 123456", "")

        success, message = pair_with_code("192.0.2.1:43210", "123456", runner=runner)
        self.assertTrue(success)
        self.assertEqual(observed["command"], ["adb", "pair", "192.0.2.1:43210"])
        self.assertEqual(observed["input"], "123456\n")
        self.assertNotIn("123456", message)

    def test_success_text_with_nonzero_status_is_not_accepted(self):
        runner = lambda command, **kwargs: subprocess.CompletedProcess(command, 1, "Successfully paired", "protocol fault")
        self.assertFalse(pair_with_code("192.0.2.1:43210", "123456", runner=runner)[0])

    def test_invalid_code_never_spawns_adb(self):
        called = []
        success, _ = pair_with_code("192.0.2.1:43210", "12 456", runner=lambda *args, **kwargs: called.append(args))
        self.assertFalse(success)
        self.assertEqual(called, [])

    def test_invalid_pairing_endpoint_never_spawns_adb(self):
        for endpoint in ("host.example:12345", "192.0.2.1:0", "192.0.2.1:99999", "[::1]:12345"):
            with self.subTest(endpoint=endpoint):
                called = []
                success, _ = pair_with_secret(
                    endpoint,
                    "AbCd1234",
                    runner=lambda *args, **kwargs: called.append(args),
                )
                self.assertFalse(success)
                self.assertEqual(called, [])

    def test_qr_secret_is_sent_only_over_stdin(self):
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, "Successfully paired", "")

        success, _ = pair_with_secret("192.0.2.10:43210", "AbCd1234EfGh", runner=runner)
        self.assertTrue(success)
        self.assertEqual(calls[0][0], ["adb", "pair", "192.0.2.10:43210"])
        self.assertEqual(calls[0][1]["input"], "AbCd1234EfGh\n")


if __name__ == "__main__":
    unittest.main()
