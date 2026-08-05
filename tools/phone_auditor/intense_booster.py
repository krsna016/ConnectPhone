import subprocess
import json
import sys

def run_adb_shell(cmd):
    try:
        res = subprocess.run(["adb", "shell", cmd], capture_output=True, text=True, timeout=30)
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except Exception as e:
        return -1, "", str(e)

def main():
    print("Initiating intense Antigravity boosting sequence...")
    
    # 1. Log Buffer Minimization (Reduces CPU overhead from logging)
    print("Shrinking logcat buffers to reduce logging daemon CPU usage...")
    run_adb_shell("logcat -G 64K")
    
    # 2. Reclaim memory aggressively
    print("Compacting RAM and forcing garbage collection...")
    run_adb_shell("echo 3 > /proc/sys/vm/drop_caches")
    run_adb_shell("am kill-all")
    
    # 3. Retrieve list of 3rd party apps to place into RESTRICTED bucket (except critical/communication apps like WhatsApp)
    _, pkgs_out, _ = run_adb_shell("pm list packages -3")
    third_party_pkgs = [line.replace("package:", "").strip() for line in pkgs_out.splitlines() if line.strip()]
    
    excluded_apps = ["com.whatsapp", "app.revanced.android.gms"]
    restricted_apps = []
    
    print("Enforcing Restricted Standby Bucket (45) on third-party background applications...")
    for pkg in third_party_pkgs:
        if pkg not in excluded_apps:
            code, _, _ = run_adb_shell(f"am set-standby-bucket {pkg} restricted")
            if code == 0:
                restricted_apps.append(pkg)
                
    report = {
        "log_buffer_size": "64K",
        "restricted_apps_count": len(restricted_apps),
        "restricted_apps_list": restricted_apps
    }
    
    out_path = "/Users/anurag/.gemini/antigravity-cli/brain/bc82fa9b-634b-46ba-a680-2a8df369263b/intense_boost_report.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=4)
        
    print(f"Intense boosting complete. Report saved to {out_path}")

if __name__ == "__main__":
    main()
