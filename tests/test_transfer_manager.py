import io
import pathlib
import subprocess
import tempfile
import time
import unittest

from core.transfer_manager import TransferManager


class FakeProcess:
    def __init__(self, returncode=0):
        self.stdout = io.StringIO("[ 50%] copying\n[100%] done\n")
        self.returncode = returncode

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        self.returncode = -15


class TransferManagerTests(unittest.TestCase):
    def test_reports_active_queued_job(self):
        manager = TransferManager()
        manager._jobs["queued"] = {"status": "queued", "created_at": 0}
        self.assertTrue(manager.has_active())
        manager._jobs["queued"]["status"] = "completed"
        self.assertFalse(manager.has_active())

    def test_rejects_paths_outside_allowed_roots(self):
        manager = TransferManager()
        with self.assertRaises(ValueError):
            manager.start(
                direction="local_to_phone",
                items=[{"path": "/etc/passwd"}],
                destination="/sdcard/Download",
            )

    def test_queues_and_completes_local_to_phone_copy(self):
        calls = []

        def runner(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, 1 if args[1:4] == ["shell", "test", "-e"] else 0, "", "")

        def popen(args, **kwargs):
            calls.append(args)
            return FakeProcess()

        with tempfile.TemporaryDirectory(dir=pathlib.Path.home()) as temp_dir:
            source = pathlib.Path(temp_dir) / "large.bin"
            source.write_bytes(b"data")
            manager = TransferManager(runner=runner, popen_factory=popen)
            job = manager.start(
                direction="local_to_phone",
                items=[{"path": str(source), "size": 4}],
                destination="/sdcard/Download",
            )
            deadline = time.time() + 2
            while time.time() < deadline and manager.get(job["id"])["status"] not in {"completed", "failed"}:
                time.sleep(0.01)
            result = manager.get(job["id"])
            manager.shutdown()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["progress"], 100)
        self.assertIn(["adb", "push", "-p", str(source), "/sdcard/Download/large.bin"], calls)

    def test_rename_conflict_keeps_existing_local_file(self):
        with tempfile.TemporaryDirectory(dir=pathlib.Path.home()) as temp_dir:
            destination = pathlib.Path(temp_dir) / "photo.jpg"
            destination.write_text("original", encoding="utf-8")
            renamed = TransferManager._resolve_local_conflict(destination, "rename")
            self.assertEqual(renamed.name, "photo (1).jpg")
            self.assertEqual(destination.read_text(encoding="utf-8"), "original")


if __name__ == "__main__":
    unittest.main()
