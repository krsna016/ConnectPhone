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
    print("Starting Phase 8: Privacy Audit...")
    
    report = {
        "location_settings": {},
        "ad_tracking_settings": {},
        "usage_diagnostics_telemetry": {},
        "clipboard_alerts": "Disabled",
        "autofill_service": "None",
        "sensitive_permission_apps": {
            "sms_granted": [],
            "contacts_granted": [],
            "call_log_granted": []
        }
    }
    
    # 1. Location Scanning settings
    _, wifi_scan, _ = run_adb_shell("settings get global wifi_scan_always_enabled")
    _, bt_scan, _ = run_adb_shell("settings get global bluetooth_scan_always_enabled")
    _, location_mode, _ = run_adb_shell("settings get secure location_mode")
    
    report["location_settings"] = {
        "wifi_background_scanning": "Enabled" if wifi_scan == "1" else "Disabled",
        "bluetooth_background_scanning": "Enabled" if bt_scan == "1" else "Disabled",
        "location_mode_state": location_mode if location_mode else "0"
    }
    
    # 2. Ad Tracking
    _, limit_ad, _ = run_adb_shell("settings get global limit_ad_tracking")
    report["ad_tracking_settings"] = {
        "limit_ad_tracking": "Enabled (Personalized Ads Blocked)" if limit_ad == "1" else "Disabled (Personalized Ads Tracking Active)"
    }
    
    # 3. Usage & Diagnostics
    _, send_diag, _ = run_adb_shell("settings get global send_action_report_enable")
    _, upload_log, _ = run_adb_shell("settings get secure upload_log_enable")
    
    report["usage_diagnostics_telemetry"] = {
        "send_action_reports": "Enabled" if send_diag == "1" else "Disabled",
        "upload_logs_to_oem": "Enabled" if upload_log == "1" else "Disabled"
    }
    
    # 4. Clipboard alerts
    _, clip_notify, _ = run_adb_shell("settings get secure show_clip_access_notification")
    report["clipboard_alerts"] = "Enabled" if clip_notify == "1" else "Disabled"
    
    # 5. Autofill Service
    _, autofill, _ = run_adb_shell("settings get secure autofill_service")
    report["autofill_service"] = autofill if autofill and autofill != "null" else "None"
    
    # 6. Check dangerous permission applications
    print("Scanning apps with sensitive permissions...")
    permissions_to_check = {
        "sms_granted": "android.permission.READ_SMS",
        "contacts_granted": "android.permission.READ_CONTACTS",
        "call_log_granted": "android.permission.READ_CALL_LOG"
    }
    
    # Get all 3rd party packages
    _, pkg_out, _ = run_adb_shell("pm list packages -3")
    packages = [line.replace("package:", "").strip() for line in pkg_out.splitlines() if line.strip()]
    
    for key, permission in permissions_to_check.items():
        _, perm_holders, _ = run_adb_shell(f"pm list packages -3 --uid | cut -d':' -f2")
        # We can query dumpsys package for each package to see if permission is granted
        for pkg in packages:
            _, dumpsys_out, _ = run_adb_shell(f"dumpsys package {pkg}")
            # Check if this permission is in granted permissions section
            # Simple heuristic: locate the permission and ensure it doesn't say "granted=false" or is in the list
            perm_escaped = re.escape(permission)
            match = re.search(rf"{perm_escaped}:\s*granted=true", dumpsys_out)
            if match:
                report["sensitive_permission_apps"][key].append(pkg)
                
    # Save JSON output
    out_path = "/Users/anurag/.gemini/antigravity-cli/brain/bc82fa9b-634b-46ba-a680-2a8df369263b/phase8_diagnostics.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=4)
        
    print(f"Phase 8 scan complete. Report saved to {out_path}")

if __name__ == "__main__":
    main()
