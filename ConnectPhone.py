import subprocess
import sys
import os
import json
import datetime
import time
import threading
import re
import xml.etree.ElementTree as ET

# Inject common macOS binary paths (crucial when run as a Dock app shortcut without zsh profiles loaded)
common_paths = [
    "/opt/homebrew/bin",
    "/usr/local/bin",
    os.path.expanduser("~/Library/Android/sdk/platform-tools"),
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin"
]
current_path = os.environ.get("PATH", "")
for path in common_paths:
    if path and path not in current_path.split(os.pathsep):
        current_path = path + os.pathsep + current_path
os.environ["PATH"] = current_path

CONFIG_FILE = os.path.expanduser("~/.connectphone_config.json")

# ANSI Escape Codes for CLI Styling (macOS terminal native)
BLUE = "\033[94m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
MAGENTA = "\033[95m"
BOLD = "\033[1m"
UNDERLINE = "\033[4m"
RESET = "\033[0m"

def print_header(title):
    os.system('clear')
    print(f"{BLUE}{BOLD}=================================================={RESET}")
    print(f"{CYAN}{BOLD}📱 {title}{RESET}")
    print(f"{BLUE}{BOLD}=================================================={RESET}")

def is_valid_ip(ip):
    parts = ip.split('.')
    if len(parts) == 4:
        try:
            return all(0 <= int(part) <= 255 for part in parts)
        except ValueError:
            return False
    return False

def load_config():
    default_config = {
        "mirror_enabled": True,
        "screen_off_enabled": False,
        "stay_awake_enabled": True,
        "show_touches_enabled": False,
        "audio_preset": "voice_communication",
        "last_ip": "192.168.29.201",
        "android_pin": "",
        "biometric_daemon_enabled": False,
        "camera_bitrate": "32M",
        "camera_fps": "60",
        "camera_codec": "h265",
        "audio_sync_delay": "0.80",
        "keyboard_mode": "uhid",
        "mac_mic_device": "default",
        "audio_buffer": "100",
        "device_profile": "generic"
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
                # Merge defaults for backward compatibility
                for k, v in default_config.items():
                    if k not in data:
                        data[k] = v
                return data
        except Exception:
            pass
    return default_config

def save_config(config):
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=4)
    except Exception:
        pass

def get_macos_audio_devices():
    devices = []
    try:
        res = subprocess.run(
            ["ffmpeg", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
            capture_output=True,
            text=True
        )
        stderr = res.stderr
        in_audio_section = False
        for line in stderr.split("\n"):
            if "AVFoundation audio devices:" in line:
                in_audio_section = True
                continue
            if in_audio_section:
                match = re.search(r'\[(\d+)\]\s+(.+)', line)
                if match:
                    device_index = match.group(1)
                    device_name = match.group(2).strip()
                    devices.append({"index": device_index, "name": device_name})
                elif "AVFoundation video devices:" in line or "Error opening input" in line or line.startswith("[in#"):
                    in_audio_section = False
    except Exception:
        pass
    return devices

def load_last_ip():
    config = load_config()
    return config["last_ip"]

def save_last_ip(ip):
    if is_valid_ip(ip):
        config = load_config()
        config["last_ip"] = ip
        save_config(config)

def check_adb_devices():
    try:
        output = subprocess.check_output(["adb", "devices"]).decode("utf-8")
        lines = output.strip().split("\n")[1:]
        devices = []
        for line in lines:
            if line.strip():
                parts = line.split()
                if len(parts) >= 2:
                    devices.append(parts[0])
        return devices
    except Exception as e:
        print(f"{RED}Error checking ADB: {e}{RESET}")
        return []

def get_orientation_filter(orientation):
    o = orientation.lower().strip()
    if o == "flip0":
        return "hflip"
    elif o == "90":
        return "transpose=1"
    elif o == "flip90":
        return "transpose=1,hflip"
    elif o == "180":
        return "transpose=2,transpose=2"
    elif o == "flip180":
        return "vflip"
    elif o == "270":
        return "transpose=2"
    elif o == "flip270":
        return "transpose=2,hflip"
    return ""

def run_scrcpy(args, is_camera=False):
    try:
        temp_mkv = None
        if is_camera:
            # Ensure the device is awake to prevent camera session termination
            try:
                subprocess.run(["adb", "shell", "input", "keyevent", "KEYCODE_WAKEUP"], capture_output=True)
            except Exception:
                pass
            has_custom_record = any(arg.startswith("--record=") for arg in args)
            if not has_custom_record:
                temp_mkv = os.path.expanduser("~/.connectphone_temp_rec.mkv")
                # Remove any existing --record or -r flags
                args = [arg for arg in args if not arg.startswith("--record=") and arg != "-r"]
                args.append(f"--record={temp_mkv}")

        # Fix record-orientation crash if display has flip orientation
        has_record = any(arg.startswith("--record=") for arg in args)
        has_flip = any(arg.startswith("--orientation=flip") for arg in args)
        if has_record and has_flip:
            args.append("--record-orientation=0")

        config = load_config()
        a_buf = config.get("audio_buffer", "100")
        cmd = ["scrcpy", f"--audio-buffer={a_buf}"] + args
        print(f"\n🚀 Running: {' '.join(cmd)}")
        print(f"{YELLOW}💡 Useful Tips:{RESET}")
        print(f"  👉 {BOLD}Flip Horizontally on-the-fly{RESET}: Press {CYAN}Alt + Shift + Left or Right Arrow{RESET} while the scrcpy window is active.")
        if is_camera:
            print(f"  👉 {BOLD}Capture snapshot{RESET}: Type {GREEN}c{RESET} in this terminal and press Enter to save to Desktop.")
            if temp_mkv:
                print(f"  👉 {BOLD}Start Video Recording{RESET}: Type {GREEN}r{RESET} in this terminal and press Enter to start/stop HD recording.")
            print(f"  👉 Closing the scrcpy window or pressing Ctrl+C here will stop camera feed.")
        else:
            print(f"  👉 Keep this terminal window open. Closing the scrcpy window or pressing Ctrl+C here will stop mirroring.")
            
        if not is_camera:
            subprocess.run(cmd)
        else:
            # Load audio preset configuration
            config = load_config()
            preset = config.get("audio_preset", "voice_communication")
            
            audio_proc = None
            mac_audio_file = os.path.expanduser("~/.connectphone_temp_mac_mic.wav")
            
            # Run scrcpy and pipe output to parse first frame arrival time (Texture initialization)
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            
            # State to share session start time and current display orientation
            state = {
                "session_start_time": time.time(),
                "orientation": "flip0" # Default orientation
            }
            start_event = threading.Event()
            
            # Start output reading thread
            def log_reader():
                for line in iter(proc.stdout.readline, b''):
                    line_str = line.decode('utf-8', errors='ignore')
                    sys.stdout.write(line_str)
                    sys.stdout.flush()
                    if "Texture:" in line_str:
                        state["session_start_time"] = time.time()
                        start_event.set()
                    if "Display orientation set to" in line_str:
                        parts = line_str.split("set to")
                        if len(parts) >= 2:
                            state["orientation"] = parts[1].strip()
                        
            reader_thread = threading.Thread(target=log_reader)
            reader_thread.daemon = True
            reader_thread.start()
            
            # Wait for scrcpy window/stream to load
            print(f"\n⏳ Waiting for camera stream to initialize...")
            stream_started = start_event.wait(timeout=10.0)
            if not stream_started:
                print(f"{YELLOW}⚠️ Stream initialization took longer than expected.{RESET}")
                session_start_time = time.time()
            else:
                print(f"{GREEN}✅ Camera stream initialized!{RESET}")
                session_start_time = state["session_start_time"]
            
            recording_active = False
            clip_start_time = 0.0
            rec_file = None
            
            try:
                while proc.poll() is None:
                    print_header("Live Camera Control Center")
                    print("🚀 Camera mirroring is active!")
                    print("💡 You can capture photos or record HD video directly to your Mac.")
                    
                    if temp_mkv:
                        if recording_active:
                            # Calculate active duration dynamically
                            dur = time.time() - session_start_time - clip_start_time
                            print(f"\n{RED}{BOLD}🔴 RECORDING ACTIVE{RESET} ➔ Saving to: {os.path.basename(rec_file)} ({dur:.0f}s)")
                            if preset == "mac_mic":
                                print(f"{BLUE}🎙️ Capturing from Mac Microphone / Earbuds{RESET}")
                        else:
                            print(f"\n⚪ Ready to Record")
                    else:
                        print(f"\n🟢 Entire session is being recorded to Desktop")
                        
                    print("\nOptions:")
                    print(f"[{GREEN}c{RESET}] 📸 Capture live frame and save to Mac Desktop")
                    
                    if temp_mkv:
                        if recording_active:
                            print(f"[{RED}r{RESET}] 🛑 Stop video recording")
                        else:
                            print(f"[{GREEN}r{RESET}] 🎥 Start video recording (Save to Mac Desktop)")
                    
                    print(f"[{RED}q{RESET}] 🛑 Stop camera feed and return")
                    
                    try:
                        choice = input("\nEnter command: ").strip().lower()
                    except KeyboardInterrupt:
                        print(f"\n{YELLOW}🛑 Exiting camera controller...{RESET}")
                        break
                        
                    if choice == 'c':
                        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                        save_path = os.path.expanduser(f"~/Desktop/scrcpy_camera_{timestamp}.png")
                        
                        captured = False
                        script_dir = os.path.dirname(os.path.realpath(__file__))
                        swift_script = os.path.join(script_dir, "get_window_id.swift")
                        swift_bin = os.path.join(script_dir, "get_window_id")
                        
                        # Compile Swift script to binary if not done yet
                        if os.path.exists(swift_script) and not os.path.exists(swift_bin):
                            print("\n⚙️ Compiling window helper script for instant capture...")
                            subprocess.run(["swiftc", swift_script, "-o", swift_bin], capture_output=True)
                            
                        # Find window ID
                        win_id = None
                        if os.path.exists(swift_bin):
                            res = subprocess.run([swift_bin, "scrcpy"], capture_output=True, text=True)
                            win_id = res.stdout.strip()
                        elif os.path.exists(swift_script):
                            res = subprocess.run(["swift", swift_script, "scrcpy"], capture_output=True, text=True)
                            win_id = res.stdout.strip()
                            
                        if win_id and win_id.isdigit():
                            print("\n📸 Capturing frame instantly...")
                            cap_res = subprocess.run(["screencapture", "-ol", win_id, save_path], capture_output=True)
                            if cap_res.returncode == 0:
                                print(f"\n{GREEN}✅ Capture saved successfully to Desktop: {os.path.basename(save_path)}{RESET}")
                                captured = True
                            else:
                                print(f"\n{YELLOW}⚠️ macOS screencapture failed. Falling back to stream extraction...{RESET}")
                                
                        if not captured:
                            # Fallback: Extract from FFmpeg stream (takes slightly longer but works under any condition)
                            if temp_mkv:
                                save_path_jpg = os.path.expanduser(f"~/Desktop/scrcpy_camera_{timestamp}.jpg")
                                print("\n⏳ Extracting live HD frame from video stream...")
                                
                                # Query actual duration of growing stream
                                duration = 0.0
                                try:
                                    probe_cmd = [
                                        "ffprobe", "-v", "error",
                                        "-show_entries", "format=duration",
                                        "-of", "default=noprint_wrappers=1:nokey=1",
                                        temp_mkv
                                    ]
                                    probe_res = subprocess.run(probe_cmd, capture_output=True, text=True)
                                    if probe_res.returncode == 0 and probe_res.stdout.strip():
                                        duration = float(probe_res.stdout.strip())
                                except Exception:
                                    pass
                                
                                # Fall back to computed system elapsed time if ffprobe fails or returned 0
                                if duration <= 0.0:
                                    duration = time.time() - session_start_time
                                    duration = max(0.0, duration)
                                
                                filter_v = get_orientation_filter(state.get("orientation", "flip0"))
                                
                                # Try fast keyframe seeking at progressive offsets
                                for offset in [1.2, 2.5, 5.0, 11.0, 21.0]:
                                    seek_time = max(0.0, duration - offset)
                                    cmd_cap = [
                                        "ffmpeg", "-y",
                                        "-skip_frame", "nokey",
                                        "-ss", f"{seek_time:.2f}",
                                        "-i", temp_mkv,
                                    ]
                                    if filter_v:
                                        cmd_cap.extend(["-vf", filter_v])
                                    cmd_cap.extend([
                                        "-vframes", "1",
                                        "-q:v", "2",
                                        save_path_jpg
                                    ])
                                    cap_res = subprocess.run(cmd_cap, capture_output=True)
                                    if cap_res.returncode == 0 and os.path.exists(save_path_jpg) and os.path.getsize(save_path_jpg) > 0:
                                        captured = True
                                        break
                                
                                # Absolute fallback (full decode seek - slow but 100% reliable)
                                if not captured:
                                    seek_time = max(0.0, duration - 2.0)
                                    cmd_cap = [
                                        "ffmpeg", "-y",
                                        "-ss", f"{seek_time:.2f}",
                                        "-i", temp_mkv,
                                    ]
                                    if filter_v:
                                        cmd_cap.extend(["-vf", filter_v])
                                    cmd_cap.extend([
                                        "-vframes", "1",
                                        "-q:v", "2",
                                        save_path_jpg
                                    ])
                                    cap_res = subprocess.run(cmd_cap, capture_output=True)
                                    if cap_res.returncode == 0 and os.path.exists(save_path_jpg) and os.path.getsize(save_path_jpg) > 0:
                                        captured = True
                                
                                if captured:
                                    print(f"\n{GREEN}✅ Live HD capture saved successfully to Desktop: {os.path.basename(save_path_jpg)}{RESET}")
                                else:
                                    print(f"\n{RED}❌ Failed to extract frame: {cap_res.stderr.decode('utf-8', errors='ignore')}{RESET}")
                            else:
                                print(f"\n{RED}❌ Could not capture image (window not found and no video stream available).{RESET}")
                                
                        input("\nPress Enter to continue...")
                    elif choice == 'r':
                        if not temp_mkv:
                            print(f"\n{YELLOW}⚠️ Recording option is disabled because the entire session is already being recorded to your Desktop.{RESET}")
                            input("\nPress Enter to continue...")
                            continue
                            
                        if recording_active:
                            clip_stop_time = time.time() - session_start_time
                            duration = clip_stop_time - clip_start_time
                            
                            if duration < 1.0:
                                print(f"\n{RED}❌ Recording too short (must be at least 1 second).{RESET}")
                                if preset == "mac_mic" and audio_proc:
                                    audio_proc.terminate()
                                    audio_proc.wait()
                                    audio_proc = None
                                recording_active = False
                                input("\nPress Enter to continue...")
                                continue
                                
                            print("\n⏳ Finalizing and saving clip to Desktop...")
                            
                            if preset == "mac_mic" and audio_proc:
                                print("🎙️ Stopping Mac mic audio capture...")
                                audio_proc.terminate()
                                audio_proc.wait()
                                audio_proc = None
                                
                            time.sleep(0.5) # Let OS flush writes
                            
                            if preset == "mac_mic":
                                # Read audio sync offset
                                try:
                                    audio_sync_delay = float(config.get("audio_sync_delay", "0.80"))
                                except ValueError:
                                    audio_sync_delay = 0.80
                                    
                                filter_v = get_orientation_filter(state.get("orientation", "flip0"))
                                if audio_sync_delay >= 0:
                                    # Positive delay means we pad the audio with silence at the start
                                    delay_ms = int(audio_sync_delay * 1000)
                                    cmd_merge = [
                                        "ffmpeg", "-y",
                                        "-ss", f"{clip_start_time:.2f}",
                                        "-t", f"{duration:.2f}",
                                        "-i", temp_mkv,
                                        "-i", mac_audio_file,
                                    ]
                                    if filter_v:
                                        cmd_merge.extend(["-filter_complex", f"[0:v]{filter_v}[v];[1:a]adelay=delays={delay_ms}:all=1[a]", "-map", "[v]", "-map", "[a]"])
                                    else:
                                        cmd_merge.extend(["-filter_complex", f"[1:a]adelay=delays={delay_ms}:all=1[a]", "-map", "0:v", "-map", "[a]"])
                                    cmd_merge.extend([
                                        "-c:v", "libx264",
                                        "-preset", "ultrafast",
                                        "-c:a", "aac",
                                        "-shortest",
                                        rec_file
                                    ])
                                else:
                                    # Negative delay means we seek into the audio file (cut off the start)
                                    seek_sec = abs(audio_sync_delay)
                                    cmd_merge = [
                                        "ffmpeg", "-y",
                                        "-ss", f"{clip_start_time:.2f}",
                                        "-t", f"{duration:.2f}",
                                        "-i", temp_mkv,
                                        "-ss", f"{seek_sec:.2f}",
                                        "-i", mac_audio_file,
                                        "-map", "0:v",
                                        "-map", "1:a",
                                    ]
                                    if filter_v:
                                        cmd_merge.insert(7, "-vf")
                                        cmd_merge.insert(8, filter_v)
                                    cmd_merge.extend([
                                        "-c:v", "libx264",
                                        "-preset", "ultrafast",
                                        "-c:a", "aac",
                                        "-shortest",
                                        rec_file
                                    ])
                                    
                                merge_res = subprocess.run(cmd_merge, capture_output=True)
                                if merge_res.returncode == 0:
                                    print(f"\n{GREEN}✅ Video clip with Mac mic audio saved successfully to: {rec_file}{RESET}")
                                else:
                                    print(f"\n{RED}❌ Failed to merge audio and video using ffmpeg: {merge_res.stderr.decode('utf-8')}{RESET}")
                                    
                                # Clean up temporary files
                                for f in [mac_audio_file]:
                                    if os.path.exists(f):
                                        try:
                                            os.remove(f)
                                        except Exception:
                                            pass
                            else:
                                # Run ffmpeg to extract standard video + audio clip precisely and with orientation
                                filter_v = get_orientation_filter(state.get("orientation", "flip0"))
                                cmd_trim = [
                                    "ffmpeg", "-y",
                                    "-ss", f"{clip_start_time:.2f}",
                                    "-t", f"{duration:.2f}",
                                    "-i", temp_mkv,
                                ]
                                if filter_v:
                                    cmd_trim.extend(["-vf", filter_v])
                                cmd_trim.extend([
                                    "-c:v", "libx264",
                                    "-preset", "ultrafast",
                                    "-c:a", "copy",
                                    rec_file
                                ])
                                trim_res = subprocess.run(cmd_trim, capture_output=True)
                                if trim_res.returncode == 0:
                                    print(f"\n{GREEN}✅ Video clip saved successfully to: {rec_file}{RESET}")
                                else:
                                    print(f"\n{RED}❌ Failed to extract clip using ffmpeg: {trim_res.stderr.decode('utf-8')}{RESET}")
                            
                            recording_active = False
                            rec_file = None
                            input("\nPress Enter to continue...")
                        else:
                            clip_start_time = time.time() - session_start_time
                            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                            rec_file = os.path.expanduser(f"~/Desktop/scrcpy_camera_rec_{timestamp}.mp4")
                            recording_active = True
                            
                            if preset == "mac_mic":
                                mac_mic = config.get("mac_mic_device", "default")
                                device_input = f":{mac_mic}"
                                print(f"\n🎙️ Starting Mac microphone recording (Device: {mac_mic}) to: {mac_audio_file}...")
                                cmd_audio = ["ffmpeg", "-y", "-nostdin", "-f", "avfoundation", "-i", device_input, mac_audio_file]
                                audio_proc = subprocess.Popen(cmd_audio, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                                
                            print(f"\n{GREEN}🔴 Recording started! Press 'r' again to stop.{RESET}")
                            input("\nPress Enter to continue...")
                    elif choice == 'q':
                        proc.terminate()
                        try:
                            proc.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                        break
            finally:
                if 'audio_proc' in locals() and audio_proc and audio_proc.poll() is None:
                    audio_proc.terminate()
                    audio_proc.wait()
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait()
                # Clean up temporary recording and audio files
                for f in [temp_mkv, mac_audio_file, os.path.expanduser("~/.connectphone_temp_video_only.mp4")]:
                    if f and os.path.exists(f):
                        try:
                            os.remove(f)
                        except Exception:
                            pass
    except Exception as e:
        print(f"{RED}Error running scrcpy: {e}{RESET}")

def get_device_info():
    try:
        # Get battery
        battery_out = subprocess.check_output(["adb", "shell", "dumpsys battery"], stderr=subprocess.DEVNULL).decode("utf-8")
        level = "Unknown"
        for line in battery_out.split("\n"):
            if line.strip().startswith("level:"):
                level = line.split(":")[-1].strip() + "%"
        
        # Get storage
        storage_out = subprocess.check_output(["adb", "shell", "df -h /sdcard"], stderr=subprocess.DEVNULL).decode("utf-8")
        storage_lines = storage_out.strip().split("\n")
        storage_info = "Unknown"
        if len(storage_lines) >= 2:
            parts = storage_lines[1].split()
            if len(parts) >= 5:
                storage_info = f"{parts[2]}/{parts[1]} used ({parts[4]})"
                
        # Get Model
        model = subprocess.check_output(["adb", "shell", "getprop ro.product.model"], stderr=subprocess.DEVNULL).decode("utf-8").strip()
        
        return f"{GREEN}{BOLD}📱 Device: {model} | 🔋 Battery: {level} | 💾 Storage: {storage_info}{RESET}"
    except Exception:
        return f"{GREEN}{BOLD}📱 Connected: Android Device{RESET}"

def pair_wireless_device():
    while True:
        print_header("Wireless Device Pairing (Android 11+)")
        print("Select pairing method:")
        print(f"1) {GREEN}📸 Scan a QR Code{RESET} (Easiest - displays QR on screen)")
        print(f"2) {YELLOW}🔑 Type a Pairing Code{RESET} (Manual / Troubleshooting)")
        print(f"3) {RED}Return to main menu{RESET}")
        
        choice = input(f"\nEnter choice (1-3): ").strip()
        
        if choice == "1":
            try:
                print(f"\n{BLUE}🚀 Initializing QR Code Pairing Server...{RESET}")
                print("Please stand by. When the QR code appears:")
                print(f"1. Go to {BOLD}Settings > Developer Options > Wireless Debugging{RESET} on your phone.")
                print(f"2. Tap {BOLD}'Pair device with QR code'{RESET} and scan the QR code in this window.\n")
                input("Press Enter to display the QR Code...")
                subprocess.run(["adb-connect-qr"])
            except Exception as e:
                print(f"{RED}❌ Error starting QR pairing: {e}{RESET}")
                input("\nPress Enter to continue...")
                
        elif choice == "2":
            while True:
                print_header("Manual Pairing via Pairing Code")
                print(f"{BOLD}Instructions:{RESET}")
                print(f"1. Go to {YELLOW}Settings > Developer Options > Wireless Debugging{RESET} on your phone.")
                print(f"2. Tap {YELLOW}'Pair device with pairing code'{RESET}.")
                print(f"3. Note down the {BOLD}Pairing IP address, Port, and Wi-Fi Pairing Code{RESET}.\n")
                
                last_ip = load_last_ip()
                ip = input(f"Enter Pairing IP (default: {last_ip}): ").strip()
                if not ip:
                    ip = last_ip
                
                if not is_valid_ip(ip):
                    print(f"{RED}❌ Invalid IP address entered.{RESET}")
                    input("\nPress Enter to retry...")
                    continue
                    
                save_last_ip(ip)
                
                port = input(f"Enter {YELLOW}Pairing Port{RESET} (e.g., 40605): ").strip()
                if not port:
                    print(f"{RED}❌ Invalid port.{RESET}")
                    input("\nPress Enter to retry...")
                    continue
                    
                ip_port = f"{ip}:{port}"
                code = input(f"Enter {GREEN}Wi-Fi Pairing Code{RESET} (e.g., 098234): ").strip()
                if not code:
                    print(f"{RED}❌ Invalid pairing code.{RESET}")
                    input("\nPress Enter to retry...")
                    continue
                    
                try:
                    print(f"\n⏳ Pairing with {ip_port} using code {code}...")
                    process = subprocess.run(["adb", "pair", ip_port, code], capture_output=True, text=True, timeout=15)
                    
                    stdout = process.stdout or ""
                    stderr = process.stderr or ""
                    
                    if stdout.strip():
                        print(f"{GREEN}{stdout}{RESET}")
                    if stderr.strip():
                        print(f"{RED}{stderr}{RESET}")
                    
                    is_success = "Successfully paired to" in stdout or "Successfully paired to" in stderr or (process.returncode == 0 and "error" not in stderr.lower())
                    if "protocol fault" in stderr.lower() or "protocol fault" in stdout.lower() or "failed" in stderr.lower():
                        is_success = False
                    
                    if is_success:
                        print(f"\n{GREEN}{BOLD}✅ Successfully paired!{RESET}")
                        
                        # Connection step loop
                        while True:
                            print(f"\n{BLUE}{BOLD}=================================================={RESET}")
                            print(f"{CYAN}{BOLD}🔗 Connection Step{RESET}")
                            print(f"{BLUE}{BOLD}=================================================={RESET}")
                            print(f"1. Look back at the {YELLOW}Wireless Debugging{RESET} screen on your phone.")
                            print(f"2. Locate the {BOLD}IP address & Port{RESET} section at the top (NOT the pairing code popup).")
                            print(f"   {RED}{BOLD}⚠️ IMPORTANT:{RESET} This port is {UNDERLINE}DIFFERENT{RESET} from the pairing port you just entered.")
                            
                            connect_ip = input(f"\nEnter Connection IP (default: {ip}): ").strip()
                            if not connect_ip:
                                connect_ip = ip
                                
                            if not is_valid_ip(connect_ip):
                                print(f"{RED}❌ Invalid IP address.{RESET}")
                                continue
                                
                            connect_port = input(f"Enter {CYAN}Connection Port{RESET}: ").strip()
                            if not connect_port:
                                print(f"{RED}❌ Connection port is required.{RESET}")
                                continue
                                
                            connect_ip_port = f"{connect_ip}:{connect_port}"
                            print(f"\n⏳ Connecting to {connect_ip_port}...")
                            connect_process = subprocess.run(["adb", "connect", connect_ip_port], capture_output=True, text=True)
                            
                            c_stdout = connect_process.stdout or ""
                            c_stderr = connect_process.stderr or ""
                            
                            if c_stdout.strip():
                                print(f"{GREEN}{c_stdout}{RESET}")
                            if c_stderr.strip():
                                print(f"{RED}{c_stderr}{RESET}")
                            
                            if "connected to" in c_stdout.lower() or "already connected to" in c_stdout.lower():
                                print(f"\n{GREEN}{BOLD}🎉 Successfully connected to {connect_ip_port}!{RESET}")
                                input("\nPress Enter to return to main menu...")
                                return
                            else:
                                print(f"\n{RED}❌ Connection failed.{RESET}")
                                print("Options:")
                                print("1) Retry connection with a different port/IP")
                                print("2) Restart ADB Server and restart entire pairing process")
                                print("3) Return to main menu")
                                conn_choice = input("\nEnter choice (1-3): ").strip()
                                if conn_choice == "2":
                                    print(f"\n🔄 Restarting ADB server...")
                                    subprocess.run(["adb", "kill-server"])
                                    subprocess.run(["adb", "start-server"])
                                    print(f"{GREEN}✅ ADB server restarted. Please toggle Wireless Debugging and start pairing again.{RESET}")
                                    input("\nPress Enter to start over...")
                                    break # break connection loop, goes back to pairing loop
                                elif conn_choice == "3":
                                    return
                        break # break pairing loop if successfully paired & connected or aborted
                    else:
                        print(f"\n{RED}{BOLD}❌ Pairing Failed!{RESET}")
                        print(f"\n{BOLD}Common Troubleshooting Checklist:{RESET}")
                        print(f"  👉 VPN: Disable any active VPN/proxies on your Mac or Phone.")
                        print(f"  👉 Wi-Fi: Ensure both devices are on the exact same Wi-Fi network (2.4Ghz vs 5Ghz).")
                        print(f"  👉 Refresh: Turn Wireless Debugging OFF and ON again on your phone.")
                        print(f"  👉 Server: Stale state detected. Try restarting the ADB server.")
                        
                        print(f"\nOptions:")
                        print(f"1) {GREEN}Restart ADB Server & Retry Pairing (Recommended){RESET}")
                        print(f"2) Retry Pairing manually with new details")
                        print(f"3) Return to Main Menu")
                        
                        fail_choice = input("\nEnter choice (1-3): ").strip()
                        if fail_choice == "1":
                            print(f"\n🔄 Restarting ADB server...")
                            subprocess.run(["adb", "kill-server"])
                            subprocess.run(["adb", "start-server"])
                            print(f"{GREEN}✅ ADB server restarted.{RESET}")
                            print(f"\n{BOLD}Instructions:{RESET}")
                            print("1. On your phone, toggle Wireless Debugging OFF and then back ON.")
                            print("2. Tap 'Pair device with pairing code' to get a fresh port and code.")
                            input("\nPress Enter when ready to retry pairing...")
                            continue # loops back to pairing input
                        elif fail_choice == "3":
                            return
                except subprocess.TimeoutExpired:
                    print(f"{RED}❌ Pairing timed out. Make sure the pairing screen is still active on your phone.{RESET}")
                    input("\nPress Enter to try again...")
                except Exception as e:
                    print(f"{RED}❌ Error during pairing: {e}{RESET}")
                    input("\nPress Enter to try again...")
        elif choice == "3":
            break

def run_mirroring_flow(mode, config):
    args = []
    
    preset = config.get("audio_preset", "voice_communication")
    audio_args = []
    if preset == "voice_communication":
        audio_args = ["--audio-source=mic-voice-communication", "--audio-codec=opus", "--audio-bit-rate=320000"]
    elif preset == "studio_unprocessed":
        audio_args = ["--audio-source=mic-unprocessed", "--audio-codec=opus", "--audio-bit-rate=320000"]
    elif preset == "camcorder":
        audio_args = ["--audio-source=mic-camcorder", "--audio-codec=opus", "--audio-bit-rate=320000"]
    elif preset == "output":
        audio_args = ["--audio-source=output", "--audio-codec=opus", "--audio-bit-rate=128000"]
    else: # standard
        audio_args = ["--audio-source=mic", "--audio-codec=opus", "--audio-bit-rate=128000"]
        
    if mode == 1:
        facing = input(f"Camera to use [{BOLD}back{RESET}/{BOLD}front{RESET}] (default: back): ").strip().lower()
        if facing not in ["front", "back"]:
            facing = "back"
        resolution = input(f"Resolution [{BOLD}4k{RESET}/{BOLD}1080p{RESET}/{BOLD}720p{RESET}/{BOLD}max{RESET}] (default: 1080p): ").strip().lower()
        if resolution not in ["4k", "1080p", "720p", "max"]:
            resolution = "1080p"
            
        args += ["--video-source=camera", f"--camera-facing={facing}"] + audio_args
        if resolution == "4k":
            args.append("--camera-size=3840x2160")
        elif resolution == "1080p":
            args.append("--camera-size=1920x1080")
        elif resolution == "720p":
            args.append("--camera-size=1280x720")
        if config["mirror_enabled"]:
            args.append("--orientation=flip0")
            
        # Apply camera quality preferences (Auto-optimize front camera and wireless feeds to prevent lag)
        devices = check_adb_devices()
        is_wireless = any(":" in d for d in devices) if devices else False
        
        if is_wireless:
            c_bitrate = "6M"
            c_codec = "h264"
            args.append("--video-buffer=150")
            print(f"\n{YELLOW}📶 Wireless connection detected. Auto-tuning camera to 6M H.264 and 150ms buffer for lag-free performance...{RESET}")
        else:
            if facing == "front":
                c_bitrate = "12M"
                c_codec = "h264"
            else:
                c_bitrate = config.get("camera_bitrate", "32M")
                c_codec = config.get("camera_codec", "h265")

        if facing == "front" or is_wireless:
            c_fps = "30"
        else:
            c_fps = config.get("camera_fps", "60")
                
        args += [f"--video-bit-rate={c_bitrate}", f"--camera-fps={c_fps}", f"--video-codec={c_codec}"]
        args.append("--stay-awake")
        if c_fps in ["120", "240"]:
            if resolution != "720p":
                print(f"\n{YELLOW}⚠️ High-Speed Mode ({c_fps} FPS) is restricted to 720p or lower resolution on this device.{RESET}")
                print(f"{YELLOW}   Adjusting resolution to 720p (1280x720) to prevent session creation failure.{RESET}")
                args = [a for a in args if not a.startswith("--camera-size=")]
                args.append("--camera-size=1280x720")
            args.append("--camera-high-speed")
            
    elif mode == 2:
        args += ["--no-video"] + audio_args
        
    elif mode == 3:
        args += ["--audio-source=output"]
        
    elif mode == 4:
        facing = input(f"Camera to use [{BOLD}back{RESET}/{BOLD}front{RESET}] (default: back): ").strip().lower()
        if facing not in ["front", "back"]:
            facing = "back"
        resolution = input(f"Resolution [{BOLD}4k{RESET}/{BOLD}1080p{RESET}/{BOLD}720p{RESET}/{BOLD}max{RESET}] (default: 1080p): ").strip().lower()
        if resolution not in ["4k", "1080p", "720p", "max"]:
            resolution = "1080p"
            
        args += ["--video-source=camera", f"--camera-facing={facing}", "--no-audio"]
        if resolution == "4k":
            args.append("--camera-size=3840x2160")
        elif resolution == "1080p":
            args.append("--camera-size=1920x1080")
        elif resolution == "720p":
            args.append("--camera-size=1280x720")
        if config["mirror_enabled"]:
            args.append("--orientation=flip0")
            
        # Apply camera quality preferences (Auto-optimize front camera and wireless feeds to prevent lag)
        devices = check_adb_devices()
        is_wireless = any(":" in d for d in devices) if devices else False
        
        if is_wireless:
            c_bitrate = "6M"
            c_codec = "h264"
            args.append("--video-buffer=150")
            print(f"\n{YELLOW}📶 Wireless connection detected. Auto-tuning camera to 6M H.264 and 150ms buffer for lag-free performance...{RESET}")
        else:
            if facing == "front":
                c_bitrate = "12M"
                c_codec = "h264"
            else:
                c_bitrate = config.get("camera_bitrate", "32M")
                c_codec = config.get("camera_codec", "h265")

        if facing == "front" or is_wireless:
            c_fps = "30"
        else:
            c_fps = config.get("camera_fps", "60")
                
        args += [f"--video-bit-rate={c_bitrate}", f"--camera-fps={c_fps}", f"--video-codec={c_codec}"]
        args.append("--stay-awake")
        if c_fps in ["120", "240"]:
            if resolution != "720p":
                print(f"\n{YELLOW}⚠️ High-Speed Mode ({c_fps} FPS) is restricted to 720p or lower resolution on this device.{RESET}")
                print(f"{YELLOW}   Adjusting resolution to 720p (1280x720) to prevent session creation failure.{RESET}")
                args = [a for a in args if not a.startswith("--camera-size=")]
                args.append("--camera-size=1280x720")
            args.append("--camera-high-speed")
            
    # Apply Preferences: ONLY for Screen Mirroring (Mode 3) to prevent exceptions in Camera Modes
    if mode == 3:
        if config["screen_off_enabled"]:
            args.append("--turn-screen-off")
        if config["stay_awake_enabled"]:
            args.append("--stay-awake")
        if config["show_touches_enabled"]:
            args.append("--show-touches")
            
        # Configure keyboard input simulation mode (uhid hides Gboard/on-screen keyboard)
        k_mode = config.get("keyboard_mode", "uhid")
        args.append(f"--keyboard={k_mode}")
            
        # Check lock state and attempt to unlock once before launching scrcpy
        if is_keyguard_locked():
            if config.get("screen_off_enabled", False):
                print(f"\n{YELLOW}🔑 Phone is locked. Launching scrcpy first to keep physical screen off...{RESET}")
                try:
                    cmd = ["scrcpy"] + args
                    print(f"\n🚀 Running: {' '.join(cmd)}")
                    print(f"{YELLOW}💡 Useful Tips:{RESET}")
                    print(f"  👉 {BOLD}Flip Horizontally on-the-fly{RESET}: Press {CYAN}Alt + Shift + Left or Right Arrow{RESET} while the scrcpy window is active.")
                    print(f"  👉 Keep this terminal open. Closing the scrcpy window or pressing Ctrl+C here will stop mirroring.")
                    proc = subprocess.Popen(cmd)
                    
                    # Wait for scrcpy to start and turn screen off
                    time.sleep(2.2)
                    
                    print(f"\n{YELLOW}🔑 Running Touch ID Unlock under the hood...{RESET}")
                    unlock_device_with_touch_id(config, interactive=False, wake_screen=False)
                    
                    proc.wait()
                except Exception as e:
                    print(f"{RED}Error running scrcpy: {e}{RESET}")
                input("\nPress Enter to return...")
                return
            else:
                print(f"\n{YELLOW}🔑 Phone is locked. Running Touch ID Unlock...{RESET}")
                unlock_device_with_touch_id(config, interactive=False)
            
    is_cam = (mode in [1, 4])
    run_scrcpy(args, is_camera=is_cam)
    input("\nPress Enter to return...")

def mirror_and_record(config):
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    record_path = os.path.expanduser(f"~/Desktop/scrcpy_record_{timestamp}.mp4")
    
    print("\nSelect video source to record:")
    print("1) Screen Mirroring (default)")
    print("2) Camera Mirroring")
    rec_src = input("Enter choice (1-2): ").strip()
    
    preset = config.get("audio_preset", "voice_communication")
    audio_args = []
    if preset == "voice_communication":
        audio_args = ["--audio-source=mic-voice-communication", "--audio-codec=opus", "--audio-bit-rate=320000"]
    elif preset == "studio_unprocessed":
        audio_args = ["--audio-source=mic-unprocessed", "--audio-codec=opus", "--audio-bit-rate=320000"]
    elif preset == "camcorder":
        audio_args = ["--audio-source=mic-camcorder", "--audio-codec=opus", "--audio-bit-rate=320000"]
    elif preset == "output":
        audio_args = ["--audio-source=output", "--audio-codec=opus", "--audio-bit-rate=128000"]
    else: # standard
        audio_args = ["--audio-source=mic", "--audio-codec=opus", "--audio-bit-rate=128000"]
        
    args = []
    if rec_src == "2":
        facing = input(f"Camera to use [{BOLD}back{RESET}/{BOLD}front{RESET}] (default: back): ").strip().lower()
        if facing not in ["front", "back"]:
            facing = "back"
        resolution = input(f"Resolution [{BOLD}4k{RESET}/{BOLD}1080p{RESET}/{BOLD}720p{RESET}/{BOLD}max{RESET}] (default: 1080p): ").strip().lower()
        if resolution not in ["4k", "1080p", "720p", "max"]:
            resolution = "1080p"
            
        args += ["--video-source=camera", f"--camera-facing={facing}"] + audio_args
        if resolution == "4k":
            args.append("--camera-size=3840x2160")
        elif resolution == "1080p":
            args.append("--camera-size=1920x1080")
        elif resolution == "720p":
            args.append("--camera-size=1280x720")
        if config["mirror_enabled"]:
            args.append("--orientation=flip0")
            
        # Apply camera quality preferences
        c_bitrate = config.get("camera_bitrate", "32M")
        c_fps = config.get("camera_fps", "60")
        c_codec = config.get("camera_codec", "h265")
        args += [f"--video-bit-rate={c_bitrate}", f"--camera-fps={c_fps}", f"--video-codec={c_codec}"]
        if c_fps in ["120", "240"]:
            if resolution != "720p":
                print(f"\n{YELLOW}⚠️ High-Speed Mode ({c_fps} FPS) is restricted to 720p or lower resolution on this device.{RESET}")
                print(f"{YELLOW}   Adjusting resolution to 720p (1280x720) to prevent session creation failure.{RESET}")
                args = [a for a in args if not a.startswith("--camera-size=")]
                args.append("--camera-size=1280x720")
            args.append("--camera-high-speed")
    else:
        args += ["--audio-source=output"]
        
    # Apply Preferences: ONLY for Screen Mirroring (rec_src == "1" or default)
    if rec_src != "2":
        if config["screen_off_enabled"]:
            args.append("--turn-screen-off")
        if config["stay_awake_enabled"]:
            args.append("--stay-awake")
        if config["show_touches_enabled"]:
            args.append("--show-touches")
            
        # Configure keyboard input simulation mode (uhid hides Gboard/on-screen keyboard)
        k_mode = config.get("keyboard_mode", "uhid")
        args.append(f"--keyboard={k_mode}")
        
    args.append(f"--record={record_path}")
    
    print(f"\n🎥 Recording stream to: {record_path}")
    run_scrcpy(args, is_camera=(rec_src == "2"))
    print(f"\n{GREEN}✅ Recording finished. Video saved to: {record_path}{RESET}")
    input("\nPress Enter to continue...")

def configure_preferences():
    while True:
        config = load_config()
        print_header("Preferences & Settings")
        
        m_status = f"{GREEN}ON (Flipped){RESET}" if config["mirror_enabled"] else f"{RED}OFF (Normal){RESET}"
        s_status = f"{GREEN}ON (Screen Off){RESET}" if config["screen_off_enabled"] else f"{RED}OFF (Screen On){RESET}"
        w_status = f"{GREEN}ON (Stay Awake){RESET}" if config["stay_awake_enabled"] else f"{RED}OFF (Normal sleep){RESET}"
        t_status = f"{GREEN}ON (Show touches){RESET}" if config["show_touches_enabled"] else f"{RED}OFF (Hide touches){RESET}"
        
        preset = config.get("audio_preset", "voice_communication")
        if preset == "voice_communication":
            a_status = f"{GREEN}Voice Call Mic (Echo Cancelled - Prevents feedback/squealing){RESET}"
        elif preset == "studio_unprocessed":
            a_status = f"{MAGENTA}Studio Unprocessed Mic (High Fidelity - Requires headphones){RESET}"
        elif preset == "camcorder":
            a_status = f"{CYAN}Camcorder Mic (Tuned for video recording){RESET}"
        elif preset == "output":
            a_status = f"{YELLOW}Internal System Audio (Record phone's system sounds instead of mic){RESET}"
        elif preset == "mac_mic":
            a_status = f"{BLUE}Mac Microphone / Earbuds (Records from computer's default input device){RESET}"
        else:
            a_status = f"{YELLOW}Standard Device Mic (128kbps noise cancelled){RESET}"
        
        d_status = f"{GREEN}ON (Auto-Prompt){RESET}" if config.get("biometric_daemon_enabled", False) else f"{RED}OFF (Manual Only){RESET}"
        
        print(f"1) 🔄 Camera Mirroring: {m_status}")
        print(f"2) 📱 Turn Off Phone Screen: {s_status} (Saves battery/Prevents heat)")
        print(f"3) ☕ Keep Phone Awake: {w_status} (Prevents phone sleep)")
        print(f"4) 🎯 Show Screen Touches: {t_status} (Shows where you click)")
        print(f"5) 🎙️ Audio Quality Profile: {a_status}")
        print(f"6) 🔑 Auto-Biometric Prompt (Daemon): {d_status}")
        print(f"7) 📷 Camera Capture Bitrate: {config.get('camera_bitrate', '32M')}")
        print(f"8) ⚡ Camera Frame Rate: {config.get('camera_fps', '60')} FPS" + (" (High-Speed Mode Required)" if config.get('camera_fps', '60') in ['120', '240'] else ""))
        print(f"9) 📼 Camera Video Codec: {config.get('camera_codec', 'h265').upper()}")
        print(f"10) 🎙️ Audio Sync Offset: {config.get('audio_sync_delay', '1.2')}s")
        print(f"11) 🔙 Return to Mirroring Menu")
        
        choice = input(f"\nEnter choice (1-11): ").strip()
        if choice == "1":
            config["mirror_enabled"] = not config["mirror_enabled"]
        elif choice == "2":
            config["screen_off_enabled"] = not config["screen_off_enabled"]
        elif choice == "3":
            config["stay_awake_enabled"] = not config["stay_awake_enabled"]
        elif choice == "4":
            config["show_touches_enabled"] = not config["show_touches_enabled"]
        elif choice == "5":
            print_header("Select Microphone / Audio Source Profile")
            print("1) 📞 Voice Call Mic (Echo Cancelled - Prevents feedback/squealing)")
            print("2) 🎧 Studio Unprocessed Mic (High Fidelity - Requires headphones)")
            print("3) 📹 Camcorder Mic (Tuned for video recording)")
            print("4) 🎙️ Standard Device Mic (128kbps noise cancelled)")
            print("5) 🔊 Internal System Audio (Record phone's system sounds instead of mic)")
            print("6) 💻 Mac Audio Input (Record Mac Mic or Mac-connected Earbuds instead of Phone Mic)")
            
            sub_choice = input("\nEnter choice (1-6): ").strip()
            if sub_choice == "1":
                config["audio_preset"] = "voice_communication"
            elif sub_choice == "2":
                config["audio_preset"] = "studio_unprocessed"
            elif sub_choice == "3":
                config["audio_preset"] = "camcorder"
            elif sub_choice == "4":
                config["audio_preset"] = "standard"
            elif sub_choice == "5":
                config["audio_preset"] = "output"
            elif sub_choice == "6":
                config["audio_preset"] = "mac_mic"
        elif choice == "6":
            config["biometric_daemon_enabled"] = not config.get("biometric_daemon_enabled", False)
        elif choice == "7":
            # Cycle through: 8M -> 16M -> 32M -> 64M
            bitrate = config.get("camera_bitrate", "32M")
            if bitrate == "8M":
                config["camera_bitrate"] = "16M"
            elif bitrate == "16M":
                config["camera_bitrate"] = "32M"
            elif bitrate == "32M":
                config["camera_bitrate"] = "64M"
            else:
                config["camera_bitrate"] = "8M"
        elif choice == "8":
            # Cycle through: 30 -> 60 -> 120 -> 240
            fps = config.get("camera_fps", "60")
            if fps == "30":
                config["camera_fps"] = "60"
            elif fps == "60":
                config["camera_fps"] = "120"
            elif fps == "120":
                config["camera_fps"] = "240"
            else:
                config["camera_fps"] = "30"
        elif choice == "9":
            # Cycle through: h264 -> h265 -> av1
            codec = config.get("camera_codec", "h265")
            if codec == "h264":
                config["camera_codec"] = "h265"
            elif codec == "h265":
                config["camera_codec"] = "av1"
            else:
                config["camera_codec"] = "h264"
        elif choice == "10":
            new_delay = input(f"\nEnter audio sync offset in seconds (e.g. 1.20 or -0.50): ").strip()
            try:
                val = float(new_delay)
                config["audio_sync_delay"] = f"{val:.2f}"
            except ValueError:
                print(f"\n{RED}❌ Invalid number format. Please enter a valid number (e.g. 1.25).{RESET}")
                input("\nPress Enter to continue...")
        elif choice == "11":
            break
        else:
            continue
            
        save_config(config)

def push_file_to_phone():
    print_header("Send File to Phone")
    path = input("\nEnter path to file on Mac: ").strip()
    if path.startswith("'") or path.startswith('"'):
        path = path[1:-1]
        
    if not os.path.exists(path):
        print(f"{RED}❌ File does not exist.{RESET}")
        input("\nPress Enter to return...")
        return
        
    filename = os.path.basename(path)
    phone_path = f"/sdcard/Download/{filename}"
    print(f"\n⏳ Pushing {filename} to Phone's Download folder...")
    res = subprocess.run(["adb", "push", path, phone_path], capture_output=True, text=True)
    
    if res.returncode == 0:
        print(f"{GREEN}✅ File pushed successfully to phone at: {phone_path}{RESET}")
    else:
        print(f"{RED}❌ Push failed: {res.stderr}{RESET}")
    input("\nPress Enter to return...")

def pull_latest_photos(interactive=True):
    if not interactive:
        try:
            cmd = ["adb", "shell", "ls", "-t", "/sdcard/DCIM/Camera/"]
            output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode("utf-8").strip()
            files = [f.strip() for f in output.split("\n") if f.strip()]
            if not files:
                return False, "No photos found in Camera folder."
            filename = files[0]
            phone_path = f"/sdcard/DCIM/Camera/{filename}"
            mac_path = os.path.expanduser(f"~/Downloads/{filename}")
            res = subprocess.run(["adb", "pull", phone_path, mac_path], capture_output=True, text=True)
            if res.returncode == 0:
                return True, f"Successfully pulled latest photo to Mac Downloads: {filename}"
            else:
                return False, f"ADB pull failed: {res.stderr}"
        except Exception as e:
            return False, f"Error pulling latest photo: {str(e)}"

    print_header("Pull Camera Photos to Mac")
    try:
        cmd = ["adb", "shell", "ls", "-t", "/sdcard/DCIM/Camera/"]
        output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode("utf-8").strip()
        files = [f.strip() for f in output.split("\n") if f.strip()]
        
        if not files:
            print(f"{YELLOW}No photos found in Camera folder.{RESET}")
            input("\nPress Enter to return...")
            return
            
        display_limit = min(5, len(files))
        print(f"\n{BOLD}Select photo to pull to Mac Downloads folder:{RESET}")
        for i in range(display_limit):
            print(f"{i+1}) {files[i]}")
        print(f"{display_limit+1}) Cancel")
        
        choice = input(f"\nEnter choice (1-{display_limit+1}): ").strip()
        try:
            idx = int(choice) - 1
            if 0 <= idx < display_limit:
                filename = files[idx]
                phone_path = f"/sdcard/DCIM/Camera/{filename}"
                mac_path = os.path.expanduser(f"~/Downloads/{filename}")
                print(f"\n⏳ Pulling {filename} to Mac Downloads...")
                subprocess.run(["adb", "pull", phone_path, mac_path])
                print(f"{GREEN}✅ Pulled successfully to: {mac_path}{RESET}")
            else:
                print("Cancelled.")
        except ValueError:
            print("Invalid input. Cancelled.")
    except Exception as e:
        print(f"{RED}Error listing/pulling photos: {e}{RESET}")
    input("\nPress Enter to continue...")

def watch_send_to_mac_folder(interactive=True):
    if interactive:
        print_header("Auto-File Sync Monitor")
        print(f"{GREEN}{BOLD}🔄 Sync Monitor started!{RESET}")
        print("\nHow to transfer files from Phone to Mac:")
        print(f"1. On your phone's File Manager, copy/move files into the folder: {CYAN}{BOLD}SendToMac{RESET}")
        print(f"   (This folder is in your internal storage: /sdcard/SendToMac/)")
        print(f"2. They will automatically be pulled to your Mac's {GREEN}{BOLD}Desktop{RESET} in real-time.\n")
        print(f"{YELLOW}💡 Keep this terminal window open in the background to receive files.{RESET}")
        print("Press Ctrl+C to stop the monitor and return to the menu.\n")
    
    # Create folder on phone if it doesn't exist
    subprocess.run(["adb", "shell", "mkdir", "-p", "/sdcard/SendToMac"], stderr=subprocess.DEVNULL)
    
    mac_desktop = os.path.expanduser("~/Desktop")
    
    try:
        while True:
            if not interactive:
                # Stop if sync_watcher_active is set to False in ConnectPhoneUI
                try:
                    import ConnectPhoneUI
                    if not ConnectPhoneUI.sync_watcher_active:
                        break
                except Exception:
                    pass

            # Check for files on phone
            cmd = ["adb", "shell", "ls", "/sdcard/SendToMac"]
            res = subprocess.run(cmd, capture_output=True, text=True)
            files = [f.strip() for f in res.stdout.split("\n") if f.strip()]
            
            # If files found and not error message
            if files and not any("no such file" in f.lower() for f in files):
                for filename in files:
                    phone_path = f"/sdcard/SendToMac/{filename}"
                    mac_path = os.path.join(mac_desktop, filename)
                    
                    if interactive:
                        print(f"📥 Found file: {filename}. Pulling to Mac Desktop...")
                    pull_res = subprocess.run(["adb", "pull", phone_path, mac_path], capture_output=True, text=True)
                    if pull_res.returncode == 0:
                        # Clean up phone directory
                        subprocess.run(["adb", "shell", "rm", f"'/sdcard/SendToMac/{filename}'"], stderr=subprocess.DEVNULL)
                        if interactive:
                            print(f"{GREEN}✅ Transferred successfully to: {mac_path}{RESET}")
                    else:
                        if interactive:
                            print(f"{RED}❌ Transfer failed: {pull_res.stderr}{RESET}")
            time.sleep(1.5)
    except KeyboardInterrupt:
        if interactive:
            print(f"\n{YELLOW}🛑 Monitor stopped.{RESET}")
    if interactive:
        input("\nPress Enter to return...")

def install_apk():
    print_header("Install APK on Phone")
    path = input("\nEnter path to APK on Mac: ").strip()
    if path.startswith("'") or path.startswith('"'):
        path = path[1:-1]
        
    if not os.path.exists(path) or not path.endswith(".apk"):
        print(f"{RED}❌ Invalid APK path.{RESET}")
        input("\nPress Enter to return...")
        return
        
    print(f"\n⏳ Installing APK onto device...")
    res = subprocess.run(["adb", "install", path], capture_output=True, text=True)
    if res.returncode == 0:
        print(f"{GREEN}✅ APK installed successfully!{RESET}")
    else:
        print(f"{RED}❌ Install failed: {res.stderr}{RESET}")
    input("\nPress Enter to return...")

def type_text():
    print_header("Remote Text Input")
    text = input("\nEnter text to type on phone: ").strip()
    if text:
        # Wrap in single quotes and escape single quotes to prevent injection & variable expansion
        escaped_text = text.replace("'", "'\\''")
        subprocess.run(["adb", "shell", "input", "text", f"'{escaped_text}'"])
        print(f"{GREEN}✅ Text sent.{RESET}")
    else:
        print("Empty text. Cancelled.")
    input("\nPress Enter to return...")

def show_shortcuts():
    print_header("scrcpy Keyboard Shortcuts")
    print("While the mirroring window is active, press:")
    print(f"  {BOLD}Alt + f{RESET}             Toggle Fullscreen")
    print(f"  {BOLD}Alt + o{RESET}             Turn device screen off (keep mirroring)")
    print(f"  {BOLD}Alt + p{RESET}             Power button (Lock/Unlock)")
    print(f"  {BOLD}Alt + h{RESET}             Home button")
    print(f"  {BOLD}Alt + b{RESET}             Back button")
    print(f"  {BOLD}Alt + s{RESET}             App Switcher")
    print(f"  {BOLD}Alt + Up/Down{RESET}       Adjust volume")
    print(f"  {BOLD}Alt + Shift + L/R{RESET}   Flip stream horizontally (mirror)")
    print(f"  {BOLD}Alt + r{RESET}             Rotate screen 90° clockwise")
    print(f"  {BOLD}Alt + g{RESET}             Resize window to 1:1 pixel size")
    print(f"\n{BLUE}=================================================={RESET}")
    input("\nPress Enter to return...")

def get_use_credential_coords():
    try:
        subprocess.run(["adb", "shell", "uiautomator", "dump"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        xml_content = subprocess.check_output(["adb", "shell", "cat", "/sdcard/window_dump.xml"], stderr=subprocess.DEVNULL).decode("utf-8", errors="ignore")
        if not xml_content:
            return None
        root = ET.fromstring(xml_content)
        for node in root.iter("node"):
            resource_id = node.get("resource-id", "")
            text = node.get("text", "")
            if "button_use_credential" in resource_id or "Use PIN" in text or "Use pattern" in text or "Use password" in text or "Use credential" in text:
                bounds = node.get("bounds", "")
                m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds)
                if m:
                    x1, y1, x2, y2 = map(int, m.groups())
                    return (x1 + x2) // 2, (y1 + y2) // 2
    except Exception:
        pass
    return None

def get_biometric_dismiss_coords():
    try:
        subprocess.run(["adb", "shell", "uiautomator", "dump"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        xml_content = subprocess.check_output(["adb", "shell", "cat", "/sdcard/window_dump.xml"], stderr=subprocess.DEVNULL).decode("utf-8", errors="ignore")
        if not xml_content or not xml_content.strip():
            return None
        root = ET.fromstring(xml_content)
        for node in root.iter("node"):
            resource_id = node.get("resource-id", "").lower()
            text = node.get("text", "").lower()
            class_name = node.get("class", "").lower()
            
            # Match negative button or cancel button or use credentials button
            if ("button" in class_name or "button" in resource_id or "cancel" in resource_id or "negative" in resource_id) and \
               ("cancel" in text or "use" in text or "pin" in text or "password" in text or "pattern" in text or "credential" in text):
                bounds = node.get("bounds", "")
                m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds)
                if m:
                    x1, y1, x2, y2 = map(int, m.groups())
                    return (x1 + x2) // 2, (y1 + y2) // 2
            
            # Text matches for Cancel / PIN fallback
            if "cancel" in text or "use pin" in text or "use pattern" in text or "use password" in text or "use credential" in text:
                bounds = node.get("bounds", "")
                m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds)
                if m:
                    x1, y1, x2, y2 = map(int, m.groups())
                    return (x1 + x2) // 2, (y1 + y2) // 2
    except Exception:
        pass
    return None

def get_pin_input_coords():
    try:
        subprocess.run(["adb", "shell", "uiautomator", "dump"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        xml_content = subprocess.check_output(["adb", "shell", "cat", "/sdcard/window_dump.xml"], stderr=subprocess.DEVNULL).decode("utf-8", errors="ignore")
        if not xml_content or not xml_content.strip():
            return None
        root = ET.fromstring(xml_content)
        for node in root.iter("node"):
            resource_id = node.get("resource-id", "").lower()
            class_name = node.get("class", "").lower()
            password = node.get("password", "").lower()
            
            if "edittext" in class_name or "password" in password or "pin" in resource_id or "password" in resource_id:
                bounds = node.get("bounds", "")
                m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds)
                if m:
                    x1, y1, x2, y2 = map(int, m.groups())
                    return (x1 + x2) // 2, (y1 + y2) // 2
    except Exception:
        pass
    return None

def is_keyguard_locked():
    try:
        # Check using dumpsys window (very reliable across MIUI/Xiaomi devices)
        out2 = subprocess.check_output(["adb", "shell", "dumpsys", "window"], stderr=subprocess.DEVNULL).decode("utf-8")
        is_showing = False
        for line in out2.split("\n"):
            line_lower = line.lower().replace(" ", "")
            if "iskeyguardshowing=true" in line_lower or "mshowinglockscreen=true" in line_lower:
                is_showing = True
                break
        if is_showing:
            return True
        # As a fallback, check if NotificationShade has current focus (line-by-line check)
        for line in out2.split("\n"):
            if "mcurrentfocus" in line.lower() and "notificationshade" in line.lower():
                return True
    except Exception:
        pass

    try:
        out = subprocess.check_output(["adb", "shell", "dumpsys", "keyguard"], stderr=subprocess.DEVNULL).decode("utf-8")
        if "can't find service" in out.lower() or not out.strip():
            return False
        return "showing=true" in out.lower() or "showing: true" in out.lower()
    except Exception:
        return False

def check_input_injection_permission():
    try:
        res = subprocess.run(["adb", "shell", "input", "keyevent", "0"], capture_output=True, text=True, timeout=1.5)
        output = (res.stdout or "") + (res.stderr or "")
        if "SecurityException" in output or "injectInputEvent" in output:
            return False
        return True
    except Exception:
        return True

def is_fingerprint_active():
    try:
        out = subprocess.check_output(["adb", "shell", "dumpsys", "fingerprint"], stderr=subprocess.DEVNULL).decode("utf-8")
        for line in out.split("\n"):
            if "current operation" in line.lower() and "fingerprintauthenticationclient" in line.lower():
                return True
    except Exception:
        pass
    return False

def clear_input_field(count=10):
    subprocess.run(["adb", "shell", "input", "keyevent"] + ["67"] * count, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def send_pin_via_keyevents(pin):
    keycodes = []
    for char in pin:
        if char.isdigit():
            # '0' is keycode 7, '1' is 8, ..., '9' is 16
            keycodes.append(str(int(char) + 7))
    if keycodes:
        subprocess.run(["adb", "shell", "input", "keyevent"] + keycodes, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)



def unlock_device_with_touch_id(config, interactive=True, wake_screen=True):
    android_pin = config.get("android_pin", "")
    applock_pin = config.get("applock_pin", android_pin)
    if not android_pin:
        print(f"\n{YELLOW}⚠️ Android Backup PIN is not configured.{RESET}")
        android_pin = input("Enter your phone's unlock PIN to configure now (saved locally): ").strip()
        if not android_pin.isdigit() or len(android_pin) < 4:
            print(f"{RED}❌ Invalid PIN. Must be digits only and at least 4 characters.{RESET}")
            input("\nPress Enter to return...")
            return
        config["android_pin"] = android_pin
        applock_pin = config.get("applock_pin", android_pin)
        save_config(config)
        print(f"{GREEN}✅ PIN saved successfully.{RESET}")

    script_dir = os.path.dirname(os.path.realpath(__file__))
    helper_path = os.path.join(script_dir, "touch_id_helper")
    touch_id_swift = os.path.join(script_dir, "touch_id.swift")
    if not os.path.exists(helper_path):
        print(f"{RED}❌ Touch ID helper binary not found.{RESET}")
        print(f"{YELLOW}💡 Re-compiling helper binary...{RESET}")
        try:
            subprocess.run(["swiftc", touch_id_swift, "-o", helper_path])
        except Exception as e:
            print(f"{RED}❌ Compilation failed: {e}{RESET}")
            input("\nPress Enter to return...")
            return

    print(f"🔑 {BOLD}Prompting Mac Touch ID...{RESET}")
    res = subprocess.run([helper_path], capture_output=True, text=True)
    stdout = res.stdout or ""
    
    if "SUCCESS" in stdout:
        print(f"\n{GREEN}✅ Touch ID Verified! Sending unlock sequence to Android...{RESET}")
        
        # Re-verify if the biometric dialog or lockscreen is STILL active before sending inputs!
        still_active = False
        if is_keyguard_locked():
            still_active = True
        else:
            focus_check = subprocess.check_output(["adb", "shell", "dumpsys", "window"], stderr=subprocess.DEVNULL).decode("utf-8")
            for line in focus_check.split("\n"):
                if "mCurrentFocus" in line or "mFocusedApp" in line:
                    if any(kw in line.lower() for kw in ["securitycenter", "applock", "passcode", "auth", "credential"]):
                        still_active = True
                        break
        if not still_active:
            print("Device/App was already unlocked manually. Aborting automated PIN entry.")
            return
        
        # 1. Wake screen if off
        if wake_screen:
            try:
                power_state = subprocess.check_output(["adb", "shell", "dumpsys", "power"], stderr=subprocess.DEVNULL).decode("utf-8")
                is_screen_on = False
                for line in power_state.split("\n"):
                    if "mwakefulness=" in line.lower().replace(" ", ""):
                        is_screen_on = "awake" in line.lower()
                        break
                if not is_screen_on:
                    is_screen_on = "mholdingdisplaysuspendblocker=true" in power_state.lower() or "state=on" in power_state.lower()
                if not is_screen_on:
                    subprocess.run(["adb", "shell", "input", "keyevent", "224"])
                    time.sleep(0.1)
            except Exception:
                pass
            
        # 2. Check for "Use PIN" / credential button, or swipe up if keyguard is showing
        coords = get_use_credential_coords()
        if coords:
            print(f"Detected 'Use PIN' button at {coords}. Tapping to open PIN screen...")
            subprocess.run(["adb", "shell", "input", "tap", str(coords[0]), str(coords[1])])
            time.sleep(0.3)
            
            print("Clearing any existing input...")
            clear_input_field(10)
            time.sleep(0.05)
            
            print("Typing PIN...")
            send_pin_via_keyevents(android_pin)
            time.sleep(0.2)
            
            subprocess.run(["adb", "shell", "input", "keyevent", "66"])
            print(f"{GREEN}🎉 Keypresses sent!{RESET}")
        else:
            locked = is_keyguard_locked()
            if locked:
                print("Detected Lock Screen. Swiping up...")
                subprocess.run(["adb", "shell", "input", "swipe", "500", "1800", "500", "200", "250"])
                time.sleep(0.4)
                
                print("Clearing any existing input...")
                clear_input_field(10)
                time.sleep(0.05)
                
                print("Typing PIN...")
                send_pin_via_keyevents(android_pin)
                time.sleep(0.05)
                
                subprocess.run(["adb", "shell", "input", "keyevent", "66"])
                print(f"{GREEN}🎉 Keypresses sent!{RESET}")
            else:
                focus_check = subprocess.check_output(["adb", "shell", "dumpsys", "window"], stderr=subprocess.DEVNULL).decode("utf-8")
                is_app_lock = False
                for line in focus_check.split("\n"):
                    if "mCurrentFocus" in line or "mFocusedApp" in line:
                        line_lower = line.lower()
                        if any(kw in line_lower for kw in ["securitycenter", "applock", "passcode", "auth", "credential"]):
                            is_app_lock = True
                            break
                            
                if is_app_lock:
                    fg_active = is_fingerprint_active()
                    if fg_active:
                        print("Detected App Lock Screen with active fingerprint dialog. Dismissing it...")
                        coords = get_biometric_dismiss_coords()
                        if coords:
                            print(f"Tapping dismiss button at {coords}...")
                            subprocess.run(["adb", "shell", "input", "tap", str(coords[0]), str(coords[1])], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        else:
                            print("Dismiss button not found. Sending Back key to transition to PIN entry...")
                            subprocess.run(["adb", "shell", "input", "keyevent", "4"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                            time.sleep(0.3)
                            # Tap coordinates where "Use PIN" or similar button might be (just in case)
                            subprocess.run(["adb", "shell", "input", "tap", "600", "2400"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        time.sleep(0.35)
                        
                    # Tap the input field to request keyboard focus
                    pin_coords = get_pin_input_coords()
                    if pin_coords:
                        print(f"Tapping PIN input field at {pin_coords} to focus...")
                        subprocess.run(["adb", "shell", "input", "tap", str(pin_coords[0]), str(pin_coords[1])], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    else:
                        print("PIN input field not found in dump. Tapping default input area...")
                        subprocess.run(["adb", "shell", "input", "tap", "600", "1000"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    time.sleep(0.15)

                    
                    print("Clearing any existing input...")
                    clear_input_field(10)
                    time.sleep(0.05)
                    
                    print("Typing Lock PIN (7 digits)...")
                    send_pin_via_keyevents(android_pin)
                    time.sleep(0.1)
                    
                    # Check if still locked (maybe it needs Enter, or maybe it auto-submitted)
                    recheck = subprocess.check_output(["adb", "shell", "dumpsys", "window"], stderr=subprocess.DEVNULL).decode("utf-8")
                    still_locked = False
                    for line in recheck.split("\n"):
                        if "mCurrentFocus" in line or "mFocusedApp" in line:
                            if "applock" in line.lower() or "securitycenter" in line.lower():
                                still_locked = True
                                break
                                
                    if still_locked:
                        print("App Lock still active. Sending Enter key...")
                        subprocess.run(["adb", "shell", "input", "keyevent", "66"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        time.sleep(0.8)
                        
                        # Recheck again
                        recheck = subprocess.check_output(["adb", "shell", "dumpsys", "window"], stderr=subprocess.DEVNULL).decode("utf-8")
                        still_locked = False
                        for line in recheck.split("\n"):
                            if "mCurrentFocus" in line or "mFocusedApp" in line:
                                if "applock" in line.lower() or "securitycenter" in line.lower():
                                    still_locked = True
                                    break
                                    
                    if still_locked:
                        # Fallback to the 6-digit PIN in case it is configured differently
                        print("Still locked. Clearing and trying App Lock PIN (6 digits)...")
                        clear_input_field(10)
                        time.sleep(0.05)
                        
                        send_pin_via_keyevents(applock_pin)
                        time.sleep(0.3)
                        subprocess.run(["adb", "shell", "input", "keyevent", "66"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        time.sleep(0.8)
                    
                    # Final validation
                    recheck = subprocess.check_output(["adb", "shell", "dumpsys", "window"], stderr=subprocess.DEVNULL).decode("utf-8")
                    still_locked = False
                    for line in recheck.split("\n"):
                        if "mCurrentFocus" in line or "mFocusedApp" in line:
                            if "applock" in line.lower() or "securitycenter" in line.lower():
                                still_locked = True
                                break
                                
                    if still_locked:
                        print(f"{YELLOW}⚠️ App Lock remains active after trying both PINs.{RESET}")
                        prompt_pin = input("Please enter your App Lock PIN (or device PIN) to try manual terminal unlock: ").strip()
                        if prompt_pin:
                            print("Sending entered PIN via keyevents...")
                            clear_input_field(10)
                            send_pin_via_keyevents(prompt_pin)
                            time.sleep(0.3)
                            subprocess.run(["adb", "shell", "input", "keyevent", "66"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                            time.sleep(0.8)
                    else:
                        print(f"{GREEN}🎉 App Lock unlocked successfully!{RESET}")
                else:
                    print(f"{YELLOW}⚠️ Biometric prompt was dismissed before Touch ID verification. Aborting PIN entry.{RESET}")
        
        # Verify lockscreen state and prompt if still locked
        time.sleep(1.2)  # Wait for screen transition animation to complete
        if is_keyguard_locked():
            print(f"{YELLOW}⚠️ Phone remains locked.{RESET}")
            prompt_pin = input("Please enter your phone's unlock PIN in this terminal to try manual unlock (or press Enter to skip): ").strip()
            if prompt_pin:
                print("Sending entered PIN via keyevents...")
                clear_input_field(10)
                send_pin_via_keyevents(prompt_pin)
                time.sleep(0.3)
                subprocess.run(["adb", "shell", "input", "keyevent", "66"])
                time.sleep(0.8)
                if not is_keyguard_locked():
                    print(f"{GREEN}🎉 Phone unlocked successfully!{RESET}")
                else:
                    print(f"{RED}❌ Still locked. Please unlock it manually on the phone screen.{RESET}")
        else:
            # Check focus to see if app lock is active
            focus_check = subprocess.check_output(["adb", "shell", "dumpsys", "window"], stderr=subprocess.DEVNULL).decode("utf-8")
            is_app_lock = False
            for line in focus_check.split("\n"):
                if "mCurrentFocus" in line or "mFocusedApp" in line:
                    if any(kw in line.lower() for kw in ["securitycenter", "applock"]):
                        is_app_lock = True
                        break
            if not is_app_lock:
                print(f"{GREEN}🎉 Phone unlocked successfully!{RESET}")

        # Print Xiaomi and dialog alerts
        if interactive or is_keyguard_locked():
            print(f"\n{YELLOW}💡 Troubleshooting Tip:{RESET}")
            print(f"  👉 If the app did not unlock, make sure you have tapped {BOLD}'Use PIN'{RESET} or {BOLD}'Cancel'{RESET}")
            print(f"     on your phone's fingerprint dialog first, so that the PIN input box is active.")
            print(f"  👉 {BOLD}Xiaomi Security Block:{RESET} On Xiaomi/Redmi devices, you must enable")
            print(f"     {CYAN}Settings > Developer Options > USB debugging (Security settings){RESET}")
            print(f"     otherwise simulated inputs are blocked on password screens.")
    else:
        print(f"{RED}❌ Mac Touch ID authentication failed or was cancelled.{RESET}")
        if "NOT_AVAILABLE" in stdout:
            print(f"{YELLOW}💡 Touch ID is not available or registered on this Mac.{RESET}")
    if interactive:
        input("\nPress Enter to return...")

def run_controls_menu():
    while True:
        config = load_config()
        print_header("Quick Controls & Input")
        print(f"1) {GREEN}🔑 Unlock via Mac Touch ID (Biometric Bridge){RESET}")
        print(f"2) {YELLOW}⚙️ Configure Android Backup PIN{RESET}")
        print(f"3) {YELLOW}⚙️ Configure App Lock PIN{RESET}")
        print(f"4) {GREEN}🔌 Simulate Power Button (Lock/Unlock){RESET}")
        print(f"5) {CYAN}🔊 Volume Up{RESET}")
        print(f"6) {CYAN}🔉 Volume Down{RESET}")
        print(f"7) {BLUE}🏠 Home Button{RESET}")
        print(f"8) {BLUE}🔙 Back Button{RESET}")
        print(f"9) {BLUE}🔀 App Switcher (Recent Apps){RESET}")
        print(f"10) {CYAN}🔇 Mute/Unmute Audio{RESET}")
        print(f"11) {CYAN}⏯️ Play/Pause Media{RESET}")
        print(f"12) {BLUE}⚙️ Open Android Settings App{RESET}")
        print(f"13) {MAGENTA}⌨️ Type text onto phone screen{RESET}")
        print(f"14) {YELLOW}📋 Show scrcpy Shortcut Cheat-sheet{RESET}")
        print(f"15) {RED}🔙 Return to Main Menu{RESET}")
        
        choice = input(f"\nEnter choice (1-15): ").strip()
        if choice == "1":
            unlock_device_with_touch_id(config)
        elif choice == "2":
            print_header("Configure Android PIN")
            pin = input("Enter your phone's unlock PIN (saved locally): ").strip()
            if not pin.isdigit() or len(pin) < 4:
                print(f"{RED}❌ Invalid PIN. Must be digits only and at least 4 characters.{RESET}")
            else:
                config["android_pin"] = pin
                save_config(config)
                print(f"{GREEN}✅ PIN saved successfully.{RESET}")
            input("\nPress Enter to continue...")
        elif choice == "3":
            print_header("Configure App Lock PIN")
            pin = input("Enter your App Lock PIN (saved locally): ").strip()
            if not pin.isdigit() or len(pin) < 4:
                print(f"{RED}❌ Invalid PIN. Must be digits only and at least 4 characters.{RESET}")
            else:
                config["applock_pin"] = pin
                save_config(config)
                print(f"{GREEN}✅ App Lock PIN saved successfully.{RESET}")
            input("\nPress Enter to continue...")
        elif choice == "4":
            subprocess.run(["adb", "shell", "input", "keyevent", "26"])
            print("Sent KEYCODE_POWER")
            input("\nPress Enter to continue...")
        elif choice == "5":
            subprocess.run(["adb", "shell", "input", "keyevent", "24"])
            print("Sent KEYCODE_VOLUME_UP")
            input("\nPress Enter to continue...")
        elif choice == "6":
            subprocess.run(["adb", "shell", "input", "keyevent", "25"])
            print("Sent KEYCODE_VOLUME_DOWN")
            input("\nPress Enter to continue...")
        elif choice == "7":
            subprocess.run(["adb", "shell", "input", "keyevent", "3"])
            print("Sent KEYCODE_HOME")
            input("\nPress Enter to continue...")
        elif choice == "8":
            subprocess.run(["adb", "shell", "input", "keyevent", "4"])
            print("Sent KEYCODE_BACK")
            input("\nPress Enter to continue...")
        elif choice == "9":
            subprocess.run(["adb", "shell", "input", "keyevent", "187"])
            print("Sent KEYCODE_APP_SWITCH")
            input("\nPress Enter to continue...")
        elif choice == "10":
            subprocess.run(["adb", "shell", "input", "keyevent", "164"])
            print("Sent KEYCODE_VOLUME_MUTE")
            input("\nPress Enter to continue...")
        elif choice == "11":
            subprocess.run(["adb", "shell", "input", "keyevent", "85"])
            print("Sent KEYCODE_MEDIA_PLAY_PAUSE")
            input("\nPress Enter to continue...")
        elif choice == "12":
            subprocess.run(["adb", "shell", "am", "start", "-a", "android.settings.SETTINGS"])
            print("Opened Android Settings")
            input("\nPress Enter to continue...")
        elif choice == "13":
            type_text()
        elif choice == "14":
            show_shortcuts()
        elif choice == "15":
            break

def biometric_daemon_loop():
    # Reload config inside loop to always get the current PIN
    prompting = False
    last_prompt_time = 0
    script_dir = os.path.dirname(os.path.realpath(__file__))
    helper_path = os.path.join(script_dir, "touch_id_helper")
    
    while True:
        config = load_config()
        if not config.get("biometric_daemon_enabled", False):
            time.sleep(2)
            continue
            
        android_pin = config.get("android_pin", "")
        applock_pin = config.get("applock_pin", android_pin)
        
        if not android_pin or not os.path.exists(helper_path):
            time.sleep(5)
            continue
            
        devices = check_adb_devices()
        if not devices:
            time.sleep(5)
            continue
            
        try:
            # 1. Lightweight check using dumpsys window
            focus_out = subprocess.check_output(["adb", "shell", "dumpsys", "window"], stderr=subprocess.DEVNULL).decode("utf-8")
            
            is_locked = is_keyguard_locked()
            
            is_biometric_active = False
            if not is_locked:
                potential_match = False
                for line in focus_out.split("\n"):
                    if "mCurrentFocus" in line or "mFocusedApp" in line:
                        line_lower = line.lower()
                        if any(kw in line_lower for kw in ["securitycenter", "applock", "biometric", "fingerprint", "passcode", "auth", "credential"]):
                            potential_match = True
                            break
                
                if potential_match:
                    # 2. Confirm by checking if standard credentials button or App Lock is active
                    focused_line = ""
                    for line in focus_out.split("\n"):
                        if "mCurrentFocus" in line or "mFocusedApp" in line:
                            focused_line += " " + line.lower()
                    
                    if any(kw in focused_line for kw in ["securitycenter", "applock", "passcode"]):
                        is_biometric_active = True
                    elif get_use_credential_coords() is not None:
                        is_biometric_active = True
                            
            current_time = time.time()
            if is_biometric_active and not prompting and (current_time > last_prompt_time):
                prompting = True
                print(f"\n{YELLOW}🔑 Biometric Prompt detected on phone! Launching Mac Touch ID...{RESET}")
                
                def prompt_touch_id():
                    nonlocal prompting, last_prompt_time
                    res = subprocess.run([helper_path], capture_output=True, text=True)
                    stdout = res.stdout or ""
                    
                    if "SUCCESS" in stdout:
                        print(f"\n{GREEN}✅ Touch ID Verified! Simulating unlock...{RESET}")
                        # Re-verify if the biometric dialog or lockscreen is STILL active before sending inputs!
                        still_active = False
                        if is_keyguard_locked():
                            still_active = True
                        else:
                            focus_check = subprocess.check_output(["adb", "shell", "dumpsys", "window"], stderr=subprocess.DEVNULL).decode("utf-8")
                            for line in focus_check.split("\n"):
                                if "mCurrentFocus" in line or "mFocusedApp" in line:
                                    if any(kw in line.lower() for kw in ["securitycenter", "applock", "passcode", "auth", "credential"]):
                                        still_active = True
                                        break
                        if not still_active:
                            print("Device/App was already unlocked manually. Aborting automated PIN entry.")
                            prompting = False
                            return
                        
                        # Verify the biometric prompt is STILL active before typing PIN
                        coords = get_use_credential_coords()
                        unlocked_successfully = True
                        if coords:
                            print(f"Tapping 'Use PIN' button at {coords}...")
                            subprocess.run(["adb", "shell", "input", "tap", str(coords[0]), str(coords[1])], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                            time.sleep(0.8)
                            
                            print("Clearing any existing input...")
                            clear_input_field(10)
                            time.sleep(0.05)
                            
                            print("Typing PIN...")
                            send_pin_via_keyevents(android_pin)
                            time.sleep(0.2)
                            
                            subprocess.run(["adb", "shell", "input", "keyevent", "66"])
                            time.sleep(0.8)
                            if is_keyguard_locked():
                                unlocked_successfully = False
                        else:
                            if is_keyguard_locked():
                                print("Detected Lock Screen. Swiping up...")
                                subprocess.run(["adb", "shell", "input", "swipe", "500", "1800", "500", "200", "250"])
                                time.sleep(0.4)
                                
                                print("Clearing any existing input...")
                                clear_input_field(10)
                                time.sleep(0.05)
                                
                                print("Typing PIN...")
                                send_pin_via_keyevents(android_pin)
                                time.sleep(0.2)
                                
                                subprocess.run(["adb", "shell", "input", "keyevent", "66"])
                                time.sleep(0.8)
                                if is_keyguard_locked():
                                    unlocked_successfully = False
                            else:
                                # Check if we are focused on an app lock / credential activity
                                focus_check = subprocess.check_output(["adb", "shell", "dumpsys", "window"], stderr=subprocess.DEVNULL).decode("utf-8")
                                is_app_lock = False
                                for line in focus_check.split("\n"):
                                    if "mCurrentFocus" in line or "mFocusedApp" in line:
                                        line_lower = line.lower()
                                        if any(kw in line_lower for kw in ["securitycenter", "applock", "passcode", "auth", "credential"]):
                                            is_app_lock = True
                                            break
                                
                                if is_app_lock:
                                    fg_active = is_fingerprint_active()
                                    if fg_active:
                                        print("Detected App Lock Screen with active fingerprint dialog. Dismissing it...")
                                        coords = get_biometric_dismiss_coords()
                                        if coords:
                                            print(f"Tapping dismiss button at {coords}...")
                                            subprocess.run(["adb", "shell", "input", "tap", str(coords[0]), str(coords[1])], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                                        else:
                                            print("Dismiss button not found. Sending Back key to transition to PIN entry...")
                                            subprocess.run(["adb", "shell", "input", "keyevent", "4"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                                            time.sleep(0.3)
                                            # Tap coordinates where "Use PIN" or similar button might be (just in case)
                                            subprocess.run(["adb", "shell", "input", "tap", "600", "2400"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                                        time.sleep(0.8)
                                        
                                    print("Clearing any existing input...")
                                    clear_input_field(10)
                                    time.sleep(0.05)
                                    
                                    print("Typing Lock PIN (7 digits)...")
                                    send_pin_via_keyevents(android_pin)
                                    time.sleep(0.1)
                                    
                                    # Check if still locked (maybe it needs Enter, or maybe it auto-submitted)
                                    recheck = subprocess.check_output(["adb", "shell", "dumpsys", "window"], stderr=subprocess.DEVNULL).decode("utf-8")
                                    still_locked = False
                                    for line in recheck.split("\n"):
                                        if "mCurrentFocus" in line or "mFocusedApp" in line:
                                            if "applock" in line.lower() or "securitycenter" in line.lower():
                                                still_locked = True
                                                break
                                                
                                    if still_locked:
                                        print("App Lock still active. Sending Enter key...")
                                        subprocess.run(["adb", "shell", "input", "keyevent", "66"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                                        time.sleep(0.8)
                                        
                                        # Recheck again
                                        recheck = subprocess.check_output(["adb", "shell", "dumpsys", "window"], stderr=subprocess.DEVNULL).decode("utf-8")
                                        still_locked = False
                                        for line in recheck.split("\n"):
                                            if "mCurrentFocus" in line or "mFocusedApp" in line:
                                                if "applock" in line.lower() or "securitycenter" in line.lower():
                                                    still_locked = True
                                                    break
                                                    
                                    if still_locked:
                                        # Fallback to the 6-digit PIN in case it is configured differently
                                        print("Still locked. Clearing and trying App Lock PIN (6 digits)...")
                                        clear_input_field(10)
                                        time.sleep(0.05)
                                        
                                        send_pin_via_keyevents(applock_pin)
                                        time.sleep(0.3)
                                        subprocess.run(["adb", "shell", "input", "keyevent", "66"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                                        time.sleep(0.8)
                                    
                                    # Final validation
                                    recheck = subprocess.check_output(["adb", "shell", "dumpsys", "window"], stderr=subprocess.DEVNULL).decode("utf-8")
                                    still_locked = False
                                    for line in recheck.split("\n"):
                                        if "mCurrentFocus" in line or "mFocusedApp" in line:
                                            if "applock" in line.lower() or "securitycenter" in line.lower():
                                                still_locked = True
                                                break
                                                
                                    if still_locked:
                                        print("⚠️ App Lock remains active after trying both PINs.")
                                        unlocked_successfully = False
                                    else:
                                        print(f"{GREEN}🎉 App Lock unlocked successfully!{RESET}")
                                else:
                                    print(f"{YELLOW}⚠️ Biometric prompt was dismissed before Touch ID verification. Aborting PIN entry.{RESET}")
                                    unlocked_successfully = False
                        
                        if unlocked_successfully:
                            print(f"{GREEN}🎉 Unlock PIN typed and verified!{RESET}")
                            last_prompt_time = time.time() + 10  # standard cooldown
                        else:
                            print(f"{YELLOW}⚠️ Unlock failed or device remains locked. Backing off Touch ID prompts for 60 seconds...{RESET}")
                            last_prompt_time = time.time() + 60
                    else:
                        print(f"\n{RED}❌ Mac Touch ID verification failed or cancelled. Backing off prompts for 30 seconds...{RESET}")
                        last_prompt_time = time.time() + 30
                        
                    prompting = False
                    
                t = threading.Thread(target=prompt_touch_id)
                t.daemon = True
                t.start()
        except Exception:
            pass
            
        time.sleep(1.5)

def record_native_video_workflow():
    print_header("Native Phone Camera HD Recording & Sync")
    print(f"{YELLOW}This option lets you record high-definition video directly on your phone's native Camera app (with full control of focus, zoom, and settings) and then automatically transfers the final video file to your Mac Desktop.{RESET}")
    
    # 1. Scan camera directory before starting
    print("\n⏳ Scanning phone's DCIM folder...")
    def get_camera_files():
        try:
            output = subprocess.check_output(["adb", "shell", "ls", "-t", "/sdcard/DCIM/Camera/"], stderr=subprocess.DEVNULL).decode("utf-8").strip()
            return [f.strip() for f in output.split("\n") if f.strip()]
        except Exception:
            return []
            
    files_before = get_camera_files()
    
    # 2. Launch native Camera in video mode
    print("\n🚀 Opening Phone's Native Camera in Video Mode...")
    subprocess.run(["adb", "shell", "am", "start", "-a", "android.media.action.VIDEO_CAPTURE"], capture_output=True)
    
    print(f"\n{GREEN}🎉 Native Camera is now open on your phone!{RESET}")
    print("👉 Use your phone's screen to start, control, and stop the recording.")
    print("👉 Once the recording has stopped and is saved, press [Enter] here to automatically pull it.")
    
    input("\nPress Enter to sync and pull the recorded video to your Mac...")
    
    # 3. Scan camera directory after stopping
    print("\n⏳ Searching for newly recorded video file on phone...")
    files_after = get_camera_files()
    
    new_files = [f for f in files_after if f not in files_before]
    video_extensions = (".mp4", ".mov", ".3gp", ".mkv", ".webm")
    
    target_file = None
    # Check for new video files first
    new_videos = [f for f in new_files if f.lower().endswith(video_extensions)]
    if new_videos:
        target_file = new_videos[0] # Pick the newest one
    elif new_files:
        # If any other new files, see if we can find one
        target_file = new_files[0]
    else:
        # Fallback: scan DCIM for the absolute latest video file, regardless of files_before
        all_videos = [f for f in files_after if f.lower().endswith(video_extensions)]
        if all_videos:
            target_file = all_videos[0]
            print(f"{YELLOW}⚠️ No new file signature found, falling back to latest existing video on phone.{RESET}")
            
    if target_file:
        phone_path = f"/sdcard/DCIM/Camera/{target_file}"
        mac_path = os.path.expanduser(f"~/Desktop/{target_file}")
        print(f"\n⏳ Transferring video to Mac Desktop: {target_file}...")
        res = subprocess.run(["adb", "pull", phone_path, mac_path])
        if res.returncode == 0:
            print(f"\n{GREEN}✅ Success! Video saved to: {mac_path}{RESET}")
        else:
            print(f"\n{RED}❌ ADB transfer failed.{RESET}")
    else:
        print(f"\n{RED}❌ No recorded video file could be detected in /sdcard/DCIM/Camera/.{RESET}")
        print("Please make sure you recorded a video and saved it.")
        
    input("\nPress Enter to return...")

def run_mirroring_menu():
    while True:
        config = load_config()
        print_header("Mirroring & Camera Modes")
        print(f"1) {GREEN}🖥️ Standard Screen Mirroring{RESET}")
        print(f"2) {CYAN}📷 Live Camera Feed to Mac (With Mic Audio){RESET}")
        print(f"3) {CYAN}📷 Live Camera Feed to Mac (No Audio){RESET}")
        print(f"4) {BLUE}🔊 Audio-Only Mirroring (Listen to Phone Mic){RESET}")
        print(f"5) {MAGENTA}🎥 Native Phone Camera HD Record & Auto-Pull{RESET} (Full camera control, sync to Mac)")
        print(f"6) {MAGENTA}🎥 Mirror & Record Video Feed to Mac Desktop{RESET} (scrcpy feed record)")
        print(f"7) {YELLOW}⚙️ Edit Mirroring Preferences & Audio Profile{RESET}")
        print(f"8) {RED}🔙 Return to Main Menu{RESET}")
        
        choice = input(f"\nEnter choice (1-8): ").strip()
        if choice == "1":
            run_mirroring_flow(3, config)
        elif choice == "2":
            run_mirroring_flow(1, config)
        elif choice == "3":
            run_mirroring_flow(4, config)
        elif choice == "4":
            run_mirroring_flow(2, config)
        elif choice == "5":
            record_native_video_workflow()
        elif choice == "6":
            mirror_and_record(config)
        elif choice == "7":
            configure_preferences()
        elif choice == "8":
            break

def run_files_menu():
    while True:
        print_header("File Transfer & App Installer")
        print(f"1) {GREEN}📤 Push file from Mac to Phone's Download folder{RESET}")
        print(f"2) {CYAN}📥 Pull latest photo from Phone to Mac Downloads{RESET}")
        print(f"3) {BLUE}🔄 Start Auto-File Sync Monitor (Real-time sync to Desktop){RESET}")
        print(f"4) {MAGENTA}🔌 Install Android APK on Phone{RESET}")
        print(f"5) {RED}🔙 Return to Main Menu{RESET}")
        
        choice = input(f"\nEnter choice (1-5): ").strip()
        if choice == "1":
            push_file_to_phone()
        elif choice == "2":
            pull_latest_photos()
        elif choice == "3":
            watch_send_to_mac_folder()
        elif choice == "4":
            install_apk()
        elif choice == "5":
            break

def main():
    # Start background Biometric Watcher Daemon (Disabled to prevent loop issues and unexpected prompts)
    daemon_thread = threading.Thread(target=biometric_daemon_loop)
    daemon_thread.daemon = True
    daemon_thread.start()
    
    while True:
        devices = check_adb_devices()
        
        if not devices:
            print_header("ConnectPhone - Mac & Android Integration Tool")
            print(f"{RED}{BOLD}❌ No connected Android devices found.{RESET}")
            print(f"\n{BOLD}Options:{RESET}")
            print(f"1) {GREEN}Pair a new device wirelessly{RESET} (QR Code / Pairing Code)")
            print(f"2) {CYAN}Connect to already paired device{RESET} (Wi-Fi IP/Port)")
            print(f"3) {YELLOW}Troubleshoot / Restart ADB Server{RESET}")
            print(f"4) {YELLOW}Refresh / Scan again{RESET}")
            print(f"5) {RED}Exit{RESET}")
            
            choice = input(f"\nEnter choice (1-5): ").strip()
            if choice == "1":
                pair_wireless_device()
            elif choice == "2":
                last_ip = load_last_ip()
                ip = input(f"Enter phone IP (default: {last_ip}): ").strip()
                if not ip:
                    ip = last_ip
                
                if not is_valid_ip(ip):
                    print(f"{RED}❌ Invalid IP address.{RESET}")
                    input("\nPress Enter to continue...")
                    continue
                    
                save_last_ip(ip)
                
                port = input(f"Enter Connection Port (default: 5555, or port from Wireless Debugging screen): ").strip()
                if not port:
                    port = "5555"
                    
                ip_port = f"{ip}:{port}"
                print(f"\n⏳ Connecting to {ip_port}...")
                res = subprocess.run(["adb", "connect", ip_port], capture_output=True, text=True)
                stdout = res.stdout or ""
                stderr = res.stderr or ""
                
                if stdout.strip():
                    print(stdout)
                if stderr.strip():
                    print(stderr)
                
                if "connected to" in stdout.lower() or "already connected to" in stdout.lower():
                    print(f"{GREEN}🎉 Successfully connected to {ip_port}!{RESET}")
                else:
                    print(f"\n{RED}❌ Connection failed.{RESET}")
                    print(f"\n{BOLD}Possible solutions:{RESET}")
                    print("  👉 Check if VPN is active on your Mac or Phone (disable it).")
                    print("  👉 Turn Wireless Debugging OFF and ON again on your phone.")
                    print("  👉 Restart ADB Server.")
                    choice_reset = input("\nWould you like to restart the ADB server now? (y/n): ").strip().lower()
                    if choice_reset in ['y', 'yes', '']:
                        print("\n🔄 Restarting ADB server...")
                        subprocess.run(["adb", "kill-server"])
                        subprocess.run(["adb", "start-server"])
                        print(f"{GREEN}✅ ADB server restarted.{RESET}")
                input("\nPress Enter to continue...")
            elif choice == "3":
                print("\n🔄 Restarting ADB server...")
                subprocess.run(["adb", "kill-server"])
                subprocess.run(["adb", "start-server"])
                print(f"{GREEN}✅ ADB server restarted successfully.{RESET}")
                input("\nPress Enter to continue...")
            elif choice == "5":
                break
            continue
            
        # Device Connected Dashboard
        info_line = get_device_info()
        print_header("ConnectPhone - Integration Command Center")
        print(info_line)
        print("\n" + f"{BOLD}Select integration category:{RESET}")
        print(f"1) {GREEN}🖥️ Mirroring & Camera Modes{RESET}")
        print(f"2) {CYAN}📁 File Transfer & App Installer{RESET}")
        print(f"3) {BLUE}🎮 Quick Controls & Keyboard{RESET}")
        print(f"4) {YELLOW}🔧 Connection Settings & Troubleshooting{RESET}")
        print(f"5) {RED}Exit{RESET}")
        
        choice = input(f"\nEnter choice (1-5): ").strip()
        if choice == "1":
            run_mirroring_menu()
        elif choice == "2":
            run_files_menu()
        elif choice == "3":
            run_controls_menu()
        elif choice == "4":
            while True:
                print_header("Connection Settings")
                print(f"1) {GREEN}🔑 Pair another device wirelessly{RESET}")
                print(f"2) {CYAN}🔄 Restart ADB Server{RESET}")
                print(f"3) {RED}🔙 Return to Main Menu{RESET}")
                trouble_choice = input(f"\nEnter choice (1-3): ").strip()
                if trouble_choice == "1":
                    pair_wireless_device()
                elif trouble_choice == "2":
                    print("\n🔄 Restarting ADB server...")
                    subprocess.run(["adb", "kill-server"])
                    subprocess.run(["adb", "start-server"])
                    print(f"{GREEN}✅ ADB server restarted successfully.{RESET}")
                    input("\nPress Enter to continue...")
                elif trouble_choice == "3":
                    break
        elif choice == "5":
            print("Exiting.")
            break

if __name__ == "__main__":
    main()
