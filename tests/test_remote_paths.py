import unittest

from core.remote_paths import adb_shell_command, safe_download_name, valid_remote_path


class RemotePathTests(unittest.TestCase):
    def test_allows_normal_shared_storage_children(self):
        self.assertTrue(valid_remote_path("/sdcard/Download/report.pdf"))
        self.assertTrue(valid_remote_path("/storage/emulated/0/DCIM/photo.jpg"))

    def test_rejects_traversal_controls_and_prefix_confusion(self):
        self.assertFalse(valid_remote_path("/sdcard/../data/local/tmp"))
        self.assertFalse(valid_remote_path("/sdcard2/private"))
        self.assertFalse(valid_remote_path("/sdcard/file\nname"))

    def test_protects_storage_roots_from_recursive_delete(self):
        for root in ("/sdcard", "/storage", "/storage/emulated", "/storage/emulated/0"):
            self.assertFalse(valid_remote_path(root, destructive=True))
        self.assertTrue(valid_remote_path("/sdcard/Download/item", destructive=True))

    def test_download_filename_cannot_inject_headers(self):
        name = safe_download_name('/sdcard/a"\r\nX-Evil: yes.txt')
        self.assertNotIn("\r", name)
        self.assertNotIn("\n", name)
        self.assertNotIn('"', name)

    def test_adb_shell_metacharacters_are_single_quoted(self):
        command = adb_shell_command("rm", "-rf", "--", "/sdcard/a; touch /sdcard/pwned")
        self.assertEqual(command[:2], ["adb", "shell"])
        self.assertEqual(command[2], "rm -rf -- '/sdcard/a; touch /sdcard/pwned'")
        with self.assertRaises(ValueError):
            adb_shell_command("rm", "bad\x00path")


if __name__ == "__main__":
    unittest.main()
