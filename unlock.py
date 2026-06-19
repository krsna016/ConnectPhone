#!/usr/bin/env python3
import subprocess
import os
import json
import time
import re
import xml.etree.ElementTree as ET

CONFIG_FILE = os.path.expanduser("~/.connectphone_config.json")
script_dir = os.path.dirname(os.path.realpath(__file__))
HELPER_PATH = os.path.join(script_dir, "touch_id_helper")

# ANSI Escape Codes for styling
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BOLD = "\033[1m"
CYAN = "\033[96m"
RESET = "\033[0m"

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

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

def main():
    config = load_config()
    android_pin = config.get("android_pin", "")
    applock_pin = config.get("applock_pin", android_pin)
    
    if not android_pin:
        print(f"{RED}❌ Android PIN not configured in ConnectPhone preferences.{RESET}")
        android_pin = input("Enter your phone's unlock PIN to configure now: ").strip()
        if not android_pin.isdigit() or len(android_pin) < 4:
            print(f"{RED}❌ Invalid PIN. Must be digits only and at least 4 characters.{RESET}")
            return
        config["android_pin"] = android_pin
        applock_pin = config.get("applock_pin", android_pin)
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(config, f, indent=4)
            print(f"{GREEN}✅ PIN saved successfully.{RESET}")
        except Exception as e:
            print(f"{RED}❌ Error saving PIN: {e}{RESET}")
            return

    if not os.path.exists(HELPER_PATH):
        print(f"{RED}❌ Touch ID helper binary not found.{RESET}")
        print(f"{YELLOW}💡 Please run ConnectPhone.py first to compile it automatically.{RESET}")
        return

    print(f"🔑 {BOLD}Prompting Mac Touch ID...{RESET}")
    res = subprocess.run([HELPER_PATH], capture_output=True, text=True)
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

if __name__ == "__main__":
    main()
