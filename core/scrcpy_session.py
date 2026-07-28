import subprocess
import threading
import time
import sys
import os
from typing import List, Optional
from core.config_manager import ConfigurationManager

class ScrcpySession:
    """
    Object-Oriented encapsulation of a mirroring session.
    Replaces the massive procedural run_scrcpy function and eliminates global state mutation.
    """

    def __init__(self, config_manager: ConfigurationManager):
        self.config = config_manager
        self.process: Optional[subprocess.Popen] = None
        self.session_start_time: float = 0.0
        self.orientation: str = "flip0"
        self._stream_started = threading.Event()
        self._is_camera = False

    def start_session(self, args: List[str], is_camera: bool = False) -> None:
        self._is_camera = is_camera
        
        # --- Apple Silicon Hardware Acceleration Engine ---
        # Force SDL2 to use the low-level Apple Metal API instead of OpenGL
        env = os.environ.copy()
        env["SDL_RENDER_DRIVER"] = "metal"
        # Force macOS VideoToolbox for ultra-low latency hardware decoding
        env["AV_HWACCEL"] = "videotoolbox"
        
        # Build command using injected config
        audio_buffer = self.config.get("audio_buffer", "100")
        
        # Keep the session wrapper transport-neutral.  Camera mode chooses its
        # codec/bitrate explicitly below; injecting another codec here creates
        # duplicate --video-codec flags and makes the effective quality
        # dependent on argument ordering.
        hw_args = [
            "--display-buffer=0"  # no intentional playback buffering
        ]
        
        cmd = ["scrcpy", "--window-title", "ConnectPhone", f"--audio-buffer={audio_buffer}"] + hw_args + args

        self._print_startup_tips()

        if not self._is_camera:
            subprocess.run(cmd, env=env)
            return

        # Camera session with threaded log monitoring
        self.process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
        
        reader_thread = threading.Thread(target=self._log_reader, daemon=True)
        reader_thread.start()

        self._wait_for_initialization()

    def _log_reader(self) -> None:
        """Reads subprocess stdout and updates encapsulated instance state, avoiding global dictionaries."""
        if not self.process or not self.process.stdout:
            return

        for line in iter(self.process.stdout.readline, b''):
            line_str = line.decode('utf-8', errors='ignore')
            sys.stdout.write(line_str)
            sys.stdout.flush()

            if "Texture:" in line_str:
                self.session_start_time = time.time()
                self._stream_started.set()
            
            if "Display orientation set to" in line_str:
                parts = line_str.split("set to")
                if len(parts) >= 2:
                    self.orientation = parts[1].strip()

    def _wait_for_initialization(self) -> None:
        print("\n⏳ Waiting for camera stream to initialize...")
        initialized = self._stream_started.wait(timeout=10.0)
        
        if not initialized:
            print("⚠️ Stream initialization took longer than expected.")
            self.session_start_time = time.time()
        else:
            print("✅ Camera stream initialized!")

    def _print_startup_tips(self) -> None:
        print("\n💡 Useful Tips:")
        print("  👉 Flip Horizontally on-the-fly: Press Alt + Shift + Left or Right Arrow.")
        if self._is_camera:
            print("  👉 Capture snapshot: Type 'c' in this terminal and press Enter.")
            print("  👉 Start Video Recording: Type 'r' in this terminal and press Enter.")
