import subprocess
import json
import sys
import re

def run_adb_shell(cmd):
    try:
        res = subprocess.run(["adb", "shell", cmd], capture_output=True, text=True, timeout=40)
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except Exception as e:
        return -1, "", str(e)

def main():
    print("Initiating Super Extreme Antigravity Sandboxing Sequence...")
    
    report = {
        "appops_restricted_apps": [],
        "network_blacklisted_apps": []
    }
    
    # 1. Query all third-party apps with UIDs
    _, pkgs_out, _ = run_adb_shell("pm list packages -3 -U")
    
    excluded_apps = ["com.whatsapp", "app.revanced.android.gms"]
    
    print("Applying strict AppOps and network restrictions to user apps...")
    for line in pkgs_out.splitlines():
        if not line.strip():
            continue
        # format: package:com.ubercab uid:10345
        match = re.search(r"package:([^\s]+)\s+uid:(\d+)", line)
        if match:
            pkg = match.group(1)
            uid = match.group(2)
            
            if pkg in excluded_apps:
                continue
                
            # Ban background execution
            run_adb_shell(f"appops set {pkg} RUN_IN_BACKGROUND ignore")
            run_adb_shell(f"appops set {pkg} RUN_ANY_IN_BACKGROUND ignore")
            report["appops_restricted_apps"].append(pkg)
            
            # Blacklist from background mobile/Wi-Fi data using UID
            code_net, _, _ = run_adb_shell(f"cmd netpolicy add restrict-background-blacklist {uid}")
            if code_net == 0:
                report["network_blacklisted_apps"].append(f"{pkg} (uid: {uid})")
            
    out_path = "/Users/anurag/.gemini/antigravity-cli/brain/bc82fa9b-634b-46ba-a680-2a8df369263b/super_extreme_boost_report.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=4)
        
    print(f"Super Extreme boosting complete. Saved report to {out_path}")

if __name__ == "__main__":
    main()
