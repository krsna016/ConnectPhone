import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

from core.file_manager import (
    create_local_folder,
    local_roots,
    list_local_files,
    list_phone_storages,
    rename_local_item,
    rename_remote_item,
    resolve_local_path,
    valid_item_name,
)


class FileManagerTests(unittest.TestCase):
    def test_local_roots_do_not_expose_system_volume_alias(self):
        self.assertNotIn("/", [item["path"] for item in local_roots()])

    def test_item_name_validation_rejects_traversal_and_separators(self):
        self.assertTrue(valid_item_name("Camera 2026"))
        for value in ("", ".", "..", "a/b", "a\\b", "bad\nname"):
            self.assertFalse(valid_item_name(value))

    def test_local_operations_stay_inside_home(self):
        with tempfile.TemporaryDirectory(dir=pathlib.Path.home()) as temp_dir:
            created = create_local_folder(temp_dir, "Folder")
            source = pathlib.Path(created) / "one.txt"
            source.write_text("hello", encoding="utf-8")
            listing = list_local_files(created)
            self.assertEqual([item["name"] for item in listing["files"]], ["one.txt"])
            renamed = rename_local_item(str(source), "two.txt")
            self.assertTrue(pathlib.Path(renamed).exists())
        with self.assertRaises(ValueError):
            resolve_local_path("/etc/passwd")

    def test_remote_rename_quotes_android_shell_arguments(self):
        runner = mock.Mock(side_effect=[
            subprocess.CompletedProcess([], 1, "", ""),
            subprocess.CompletedProcess([], 0, "", ""),
        ])
        destination = rename_remote_item("/sdcard/Old name.txt", "New name.txt", runner=runner)
        self.assertEqual(destination, "/sdcard/New name.txt")
        self.assertEqual(runner.call_args_list[1].args[0], [
            "adb", "shell", "mv -- '/sdcard/Old name.txt' '/sdcard/New name.txt'"
        ])

    def test_phone_storage_discovery_deduplicates_internal_alias(self):
        runner = mock.Mock(return_value=subprocess.CompletedProcess(
            [], 0, "/storage/emulated/0\n/storage/ABCD-1234\n/storage/self\n", ""
        ))
        storages = list_phone_storages(runner=runner)
        self.assertEqual([item["path"] for item in storages], ["/sdcard", "/storage/ABCD-1234"])


if __name__ == "__main__":
    unittest.main()
