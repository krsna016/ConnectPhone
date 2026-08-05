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
    print("Starting Phase 17: Attack Surface Reduction...")
    
    report = {
        "hardware_radios": {},
        "print_services": "Enabled",
        "multi_user_status": {},
        "sideload_install_allowed_apps": []
    }
    
    # 1. Hardware Radios
    _, bt_on, _ = run_adb_shell("settings get global bluetooth_on")
    _, nfc_on, _ = run_adb_shell("settings get global nfc_on")
    _, hotspot_on, _ = run_adb_shell("settings get global wifi_ap_on")
    
    report["hardware_radios"] = {
        "bluetooth_active": bt_on == "1",
        "nfc_active": nfc_on == "1",
        "hotspot_active": hotspot_on == "1"
    }
    
    # 2. Print Spooler
    _, print_pkg, _ = run_adb_shell("pm list packages com.android.printspooler")
    if print_pkg:
        _, print_status, _ = run_adb_shell("dumpsys package com.android.printspooler | grep -E 'User 0:.*installed='")
        if "installed=false" in print_status:
            report["print_services"] = "Disabled/Uninstalled"
        else:
            report["print_services"] = "Enabled"
    else:
        report["print_services"] = "Disabled/Not Present"
        
    # 3. Multi-user status
    _, users_out, _ = run_adb_shell("pm list users")
    users = []
    for line in users_out.splitlines():
        if "UserInfo{" in line:
            users.append(line.strip())
            
    _, user_switch, _ = run_adb_shell("settings get secure user_switcher_enabled")
    
    report["multi_user_status"] = {
        "active_users_count": len(users),
        "user_profiles": users,
        "lockscreen_user_switching": "Enabled" if user_switch == "1" else "Disabled"
    }
    
    # 4. Apps allowed to install unknown apps (sideloading)
    print("Scanning apps with unknown source installation permissions...")
    _, pkgs_out, _ = run_adb_shell("pm list packages -3")
    third_party_packages = [line.replace("package:", "").strip() for line in pkgs_out.splitlines() if line.strip()]
    
    for pkg in third_party_packages:
        _, appops_out, _ = run_adb_shell(f"appops get {pkg} REQUEST_INSTALL_PACKAGES 2>/dev/null")
        if "allow" in appops_out.lower():
            report["sideload_install_allowed_apps"].append(pkg)
            
    # Save JSON output
    out_path = "/Users/anurag/.gemini/antigravity-cli/brain/bc82fa9b-634b-46ba-a680-2a8df369263b/phase17_diagnostics.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=4)
        
    print(f"Phase 17 scan complete. Report saved to {out_path}")

if __name__ == "__main__":
    main()
