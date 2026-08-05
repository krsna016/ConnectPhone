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
    print("Starting Phase 16: Long-Term Reliability Audit...")
    
    report = {
        "uptime": "Unknown",
        "last_boot_reason": "Unknown",
        "stability_events_count": {}
    }
    
    # 1. Uptime
    _, uptime_out, _ = run_adb_shell("uptime")
    report["uptime"] = uptime_out if uptime_out else "Unknown"
    
    # 2. Boot reasons
    _, boot_reason, _ = run_adb_shell("getprop sys.boot.reason")
    _, boot_reason_last, _ = run_adb_shell("getprop sys.boot.reason.last")
    _, ro_boot_reason, _ = run_adb_shell("getprop ro.boot.bootreason")
    
    report["last_boot_reason"] = {
        "sys_boot_reason": boot_reason if boot_reason else "Unknown",
        "sys_boot_reason_last": boot_reason_last if boot_reason_last else "Unknown",
        "ro_boot_reason": ro_boot_reason if ro_boot_reason else "Unknown"
    }
    
    # 3. Dropbox Crash and ANR Stats
    print("Querying Dropbox system metrics...")
    _, dropbox_out, _ = run_adb_shell("dumpsys dropbox")
    
    event_tags = [
        "system_app_crash",
        "system_app_anr",
        "data_app_crash",
        "data_app_anr",
        "system_server_crash",
        "SYSTEM_TOMBSTONE",
        "kernel_panic"
    ]
    
    for tag in event_tags:
        # Match lines containing the tag
        matches = re.findall(rf"\b{tag}\b", dropbox_out)
        report["stability_events_count"][tag] = len(matches)
        
    # Save JSON output
    out_path = "/Users/anurag/.gemini/antigravity-cli/brain/bc82fa9b-634b-46ba-a680-2a8df369263b/phase16_diagnostics.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=4)
        
    print(f"Phase 16 scan complete. Report saved to {out_path}")

if __name__ == "__main__":
    main()
