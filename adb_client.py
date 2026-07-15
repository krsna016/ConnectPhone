import subprocess
import re
import json

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
        
        return f"{GREEN}{BOLD}Device: {model} | Battery: {level} | Storage: {storage_info}{RESET}"
    except Exception as e:
        print(f"Exception: {e}")
        return f"{GREEN}{BOLD}Connected: Android Device{RESET}"

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
    except Exception as e:
        print(f"Exception: {e}")
        pass

    try:
        out = subprocess.check_output(["adb", "shell", "dumpsys", "keyguard"], stderr=subprocess.DEVNULL).decode("utf-8")
        if "can't find service" in out.lower() or not out.strip():
            return False
        return "showing=true" in out.lower() or "showing: true" in out.lower()
    except Exception as e:
        print(f"Exception: {e}")
        return False

def is_fingerprint_active():
    try:
        out = subprocess.check_output(["adb", "shell", "dumpsys", "fingerprint"], stderr=subprocess.DEVNULL).decode("utf-8")
        for line in out.split("\n"):
            if "current operation" in line.lower() and "fingerprintauthenticationclient" in line.lower():
                return True
    except Exception as e:
        print(f"Exception: {e}")
        pass
    return False

