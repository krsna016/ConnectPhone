import subprocess
import json
import re
import sys

def run_adb_shell(cmd):
    try:
        res = subprocess.run(["adb", "shell", cmd], capture_output=True, text=True, timeout=30)
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except Exception as e:
        return -1, "", str(e)

def main():
    print("Starting Phase 15: ADB Deep Diagnostics...")
    
    report = {
        "adb_enabled": "Unknown",
        "adb_wifi_enabled": "Unknown",
        "secure_build": "Unknown",
        "debuggable_build": "Unknown",
        "adb_keys_readable": False
    }
    
    # 1. ADB Enabled status
    _, adb_en, _ = run_adb_shell("settings get global adb_enabled")
    report["adb_enabled"] = "Enabled" if adb_en == "1" else "Disabled"
    
    # 2. ADB Wi-Fi Enabled status
    _, adb_wifi, _ = run_adb_shell("settings get global adb_wifi_enabled")
    report["adb_wifi_enabled"] = "Enabled" if adb_wifi == "1" else "Disabled"
    
    # 3. Secure and Debuggable build tags
    _, secure, _ = run_adb_shell("getprop ro.secure")
    report["secure_build"] = "Yes" if secure == "1" else "No"
    
    _, debuggable, _ = run_adb_shell("getprop ro.debuggable")
    report["debuggable_build"] = "Yes" if debuggable == "1" else "No (Production Build)"
    
    # 4. Check if /data/misc/adb/adb_keys is readable (should be false on secure devices)
    code, _, _ = run_adb_shell("ls /data/misc/adb/adb_keys")
    report["adb_keys_readable"] = (code == 0)
    
    # Save JSON output
    out_path = "/Users/anurag/.gemini/antigravity-cli/brain/bc82fa9b-634b-46ba-a680-2a8df369263b/phase15_diagnostics.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=4)
        
    print(f"Phase 15 scan complete. Report saved to {out_path}")

if __name__ == "__main__":
    main()
