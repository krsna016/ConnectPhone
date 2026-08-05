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
    print("Starting Phase 9: Security Audit...")
    
    report = {
        "lock_screen_settings": {},
        "trust_agents_smartlock": "Disabled",
        "backup_status": {},
        "google_play_protect": {},
        "developer_options": {},
        "find_my_device": "Unknown"
    }
    
    # 1. Lock Screen settings
    _, timeout, _ = run_adb_shell("settings get system screen_off_timeout")
    _, instant_lock, _ = run_adb_shell("settings get secure lockscreen.power_button_instantly_locks")
    _, private_notif, _ = run_adb_shell("settings get secure lock_screen_allow_private_notifications")
    
    # Check if lockscreen is secured
    _, pwd_type, _ = run_adb_shell("settings get secure lockscreen.password_type")
    
    report["lock_screen_settings"] = {
        "screen_timeout_ms": timeout if timeout else "Unknown",
        "power_button_instantly_locks": "Enabled" if instant_lock == "1" or not instant_lock else "Disabled",
        "allow_private_notifications_on_lockscreen": "Enabled" if private_notif == "1" else "Disabled",
        "password_type_hex": pwd_type if pwd_type else "None/Not Secured"
    }
    
    # 2. Smart Lock
    _, trust_agents, _ = run_adb_shell("settings get secure trust_agents_initialized")
    report["trust_agents_smartlock"] = "Enabled" if trust_agents and trust_agents != "null" else "Disabled"
    
    # 3. Backup Status
    _, backup_en, _ = run_adb_shell("settings get secure backup_enabled")
    report["backup_status"] = {
        "google_backup_active": "Enabled" if backup_en == "1" else "Disabled"
    }
    
    # 4. Play Protect
    _, verifier_en, _ = run_adb_shell("settings get global package_verifier_enable")
    report["google_play_protect"] = {
        "package_verifier_enabled": "Enabled" if verifier_en == "1" or not verifier_en else "Disabled"
    }
    
    # 5. Developer options
    _, dev_en, _ = run_adb_shell("settings get global development_settings_enabled")
    report["developer_options"] = {
        "developer_options_active": "Enabled" if dev_en == "1" else "Disabled"
    }
    
    # 6. Find My Device
    _, fmd_active, _ = run_adb_shell("settings get secure find_my_device_active")
    if fmd_active == "1":
        report["find_my_device"] = "Enabled"
    elif fmd_active == "0":
        report["find_my_device"] = "Disabled"
    else:
        # Fallback check if package offline beacon service is running
        _, services, _ = run_adb_shell("dumpsys activity services | grep -i findmydevice")
        report["find_my_device"] = "Enabled" if services else "Disabled"
        
    # Save JSON output
    out_path = "/Users/anurag/.gemini/antigravity-cli/brain/bc82fa9b-634b-46ba-a680-2a8df369263b/phase9_diagnostics.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=4)
        
    print(f"Phase 9 scan complete. Report saved to {out_path}")

if __name__ == "__main__":
    main()
