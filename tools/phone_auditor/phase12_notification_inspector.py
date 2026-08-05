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
    print("Starting Phase 12: Notification Audit...")
    
    report = {
        "notification_listeners": [],
        "lock_screen_notifications": {},
        "notification_history": "Disabled",
        "heads_up_notifications": "Enabled",
        "dnd_mode_state": "Unknown"
    }
    
    # 1. Enabled Notification Listeners
    _, listener_out, _ = run_adb_shell("settings get secure enabled_notification_listeners")
    if listener_out and listener_out != "null":
        report["notification_listeners"] = [s.strip().split("/")[0] for s in listener_out.split(":") if s.strip()]
        
    # 2. Lock screen notifications settings
    _, show_notif, _ = run_adb_shell("settings get secure lock_screen_show_notifications")
    _, allow_private, _ = run_adb_shell("settings get secure lock_screen_allow_private_notifications")
    
    report["lock_screen_notifications"] = {
        "show_notifications_on_lockscreen": "Enabled" if show_notif == "1" or not show_notif else "Disabled",
        "show_sensitive_content": "Enabled (Privacy Risk)" if allow_private == "1" else "Disabled (Secure)"
    }
    
    # 3. Notification History
    _, history, _ = run_adb_shell("settings get secure notification_history_enabled")
    report["notification_history"] = "Enabled" if history == "1" else "Disabled"
    
    # 4. Heads-up notifications
    _, heads_up, _ = run_adb_shell("settings get global heads_up_notifications_enabled")
    report["heads_up_notifications"] = "Enabled" if heads_up == "1" or not heads_up else "Disabled"
    
    # 5. DND Zen Mode
    _, zen, _ = run_adb_shell("settings get global zen_mode")
    zen_map = {
        "0": "Off",
        "1": "Alarms Only",
        "2": "Total Silence",
        "3": "Alarms & Selected Priority (Contacts)"
    }
    report["dnd_mode_state"] = zen_map.get(zen, "Unknown")
    
    # Save JSON output
    out_path = "/Users/anurag/.gemini/antigravity-cli/brain/bc82fa9b-634b-46ba-a680-2a8df369263b/phase12_diagnostics.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=4)
        
    print(f"Phase 12 scan complete. Report saved to {out_path}")

if __name__ == "__main__":
    main()
