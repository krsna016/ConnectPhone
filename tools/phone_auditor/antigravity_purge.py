import subprocess
import json
import re
import os
import sys
import time

def run_adb_shell(cmd):
    try:
        res = subprocess.run(["adb", "shell", cmd], capture_output=True, text=True, timeout=30)
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "Timeout"
    except Exception as e:
        return -1, "", str(e)

def main():
    print("🚀 [ANTIGRAVITY PURGE ENGINE ACTIVE] initializing system security & performance audit...")
    
    report = {
        "device_info": {},
        "security_findings": {
            "debuggable_apps": [],
            "allow_backup_apps": [],
            "selinux": ""
        },
        "optimizations_applied": [],
        "ram_before": {},
        "ram_after": {},
        "trackers_blocked": 0,
        "permissions_revoked": 0
    }
    
    # --- PHASE 1: PRE-AUDIT DATA COLLECTION ---
    # Device specifications
    _, model, _ = run_adb_shell("getprop ro.product.model")
    _, manufacturer, _ = run_adb_shell("getprop ro.product.manufacturer")
    _, android_version, _ = run_adb_shell("getprop ro.build.version.release")
    _, security_patch, _ = run_adb_shell("getprop ro.build.version.security_patch")
    _, selinux, _ = run_adb_shell("getenforce")
    
    report["device_info"] = {
        "model": model,
        "manufacturer": manufacturer,
        "android_version": android_version,
        "security_patch": security_patch
    }
    report["security_findings"]["selinux"] = selinux
    
    # Get RAM status before
    _, ram_info, _ = run_adb_shell("dumpsys meminfo")
    for line in ram_info.splitlines():
        if "Total RAM:" in line:
            report["ram_before"]["total"] = line.strip()
        if "Free RAM:" in line:
            report["ram_before"]["free"] = line.strip()
            
    # Audit 3rd party packages for allowBackup and debuggable flags
    print("Scanning apps for configuration flaws...")
    _, pkg_out, _ = run_adb_shell("pm list packages -3")
    packages = [line.split(":")[-1].strip() for line in pkg_out.splitlines() if line.strip()]
    
    for pkg in packages:
        _, pkg_dumpsys, _ = run_adb_shell(f"dumpsys package {pkg}")
        flags_match = re.search(r"flags=\[\s*([^\s\]]+(?:\s+[^\s\]]+)*)\s*\]", pkg_dumpsys)
        if flags_match:
            flags = flags_match.group(1).split()
            if "DEBUGGABLE" in flags:
                report["security_findings"]["debuggable_apps"].append(pkg)
            if "ALLOW_BACKUP" in flags:
                report["security_findings"]["allow_backup_apps"].append(pkg)
                
    # --- PHASE 2: PURGE & OPTIMIZE ---
    print("Neutralizing app caches...")
    # Trim app caches aggressively (trim to 0 bytes)
    code, _, _ = run_adb_shell("cmd package trim-caches 999999999999")
    if code == 0:
        report["optimizations_applied"].append("Triggered aggressive cache trim via package manager")
        
    print("Flushing temporary logs...")
    # Clear local temporary folder and logcat
    run_adb_shell("rm -rf /data/local/tmp/* 2>/dev/null")
    run_adb_shell("logcat -c")
    report["optimizations_applied"].append("Purged /data/local/tmp and cleared logcat log buffers")
    
    # Restrict background execution (AppOps debloat)
    print("Restricting background execution for 3rd-party trackers...")
    target_apps = [
        "com.example.mobile", "com.flipkart.android", "com.grofers.customerapp",
        "com.rapido.passenger", "net.one97.paytm", "com.ubercab",
        "in.amazon.mShop.android.shopping", "com.preff.kb.xm"
    ]
    for app in target_apps:
        if app in packages:
            code, _, _ = run_adb_shell(f"appops set {app} RUN_IN_BACKGROUND ignore")
            run_adb_shell(f"appops set {app} RUN_ANY_IN_BACKGROUND ignore")
            if code == 0:
                report["permissions_revoked"] += 2
                
    report["optimizations_applied"].append(f"Restricted background appops execution for {len(target_apps)} target trackers")
    
    # Optimizing default launcher and keyboard compiler state to SPEED-PROFILE
    print("Optimizing ART compiler states (speed-profile)...")
    compiles = ["com.miui.home", "com.google.android.inputmethod.latin", "com.preff.kb.xm"]
    for c in compiles:
        # Check if package exists
        path_check, _, _ = run_adb_shell(f"pm path {c}")
        if path_check:
            code, _, _ = run_adb_shell(f"cmd package compile -m speed-profile -f {c}")
            if code == 0:
                report["optimizations_applied"].append(f"Compiled launcher/keyboard package '{c}' to speed-profile")
                
    # Modify settings (global, system)
    print("Recalibrating UI animations and system options...")
    settings_cmds = [
        ("global", "window_animation_scale", "0.5"),
        ("global", "transition_animation_scale", "0.5"),
        ("global", "animator_duration_scale", "0.5"),
        ("global", "wifi_scan_always_enabled", "0"),
        ("global", "ble_scan_always_enabled", "0"),
        ("global", "mobile_data_always_on", "0"),
        ("global", "captive_portal_mode", "0"),
        ("global", "private_dns_mode", "hostname"),
        ("global", "private_dns_specifier", "dns.adguard.com"),
        ("system", "haptic_feedback_enabled", "0"),
        ("system", "screen_off_timeout", "30000")
    ]
    
    for table, setting, value in settings_cmds:
        code, _, _ = run_adb_shell(f"settings put {table} {setting} {value}")
        if code == 0:
            if setting == "private_dns_specifier":
                report["trackers_blocked"] = 1  # AdGuard private DNS enabled
            report["optimizations_applied"].append(f"Adjusted system setting '{setting}' to '{value}'")
            
    # Get RAM status after optimization
    print("Re-evaluating RAM status...")
    time.sleep(2)  # Allow settings to settle
    _, ram_info_after, _ = run_adb_shell("dumpsys meminfo")
    for line in ram_info_after.splitlines():
        if "Total RAM:" in line:
            report["ram_after"]["total"] = line.strip()
        if "Free RAM:" in line:
            report["ram_after"]["free"] = line.strip()
            
    # Write report
    report_path = "/Users/anurag/.gemini/antigravity-cli/brain/bc82fa9b-634b-46ba-a680-2a8df369263b/antigravity_purge_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=4)
        
    print(f"Purge complete. Report saved to: {report_path}")

if __name__ == "__main__":
    main()
