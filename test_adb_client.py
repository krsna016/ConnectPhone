import unittest
from unittest.mock import patch, MagicMock
from adb_client import ADBClient

class TestADBClient(unittest.TestCase):
    def setUp(self):
        self.adb = ADBClient(device_id="dummy_device")

    @patch('subprocess.run')
    def test_get_device_state_connected(self, mock_run):
        mock_result = MagicMock()
        mock_result.stdout = "device"
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        
        state = self.adb.get_device_state()
        self.assertEqual(state, "device")
        mock_run.assert_called_with(["adb", "-s", "dummy_device", "get-state"], capture_output=True, text=True, check=True)

    @patch('subprocess.run')
    def test_push_file_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        
        result = self.adb.push_file("local.txt", "/sdcard/remote.txt")
        self.assertTrue(result)
        mock_run.assert_called_with(["adb", "-s", "dummy_device", "push", "local.txt", "/sdcard/remote.txt"], capture_output=True, text=True, check=True)

    @patch('subprocess.run')
    def test_run_shell_command_timeout(self, mock_run):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["adb", "shell", "sleep", "10"], timeout=5)
        
        with self.assertRaises(subprocess.TimeoutExpired):
            self.adb.run_shell_command("sleep 10", timeout=5)

if __name__ == '__main__':
    unittest.main()
