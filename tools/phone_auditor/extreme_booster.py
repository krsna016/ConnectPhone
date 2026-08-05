import subprocess
import json
import sys

def run_adb_shell(cmd):
    try:
        res = subprocess.run(["adb", "shell", cmd], capture_output=True, text=True, timeout=60)
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except Exception as e:
        return -1, "", str(e)

def main():
    print("Initiating Extreme Antigravity Booster Sequence...")
    
    # 1. Restrict maximum cached processes (Frees up memory manager overhead)
    print("Setting cached processes limit to 24...")
    run_adb_shell("settings put global max_cached_processes 24")
    
    # 2. Force Network Data Saver Mode (Blocks cellular background leaks)
    print("Enabling strict mobile data background restrictions...")
    run_adb_shell("cmd netpolicy set restrict-background true")
    
    # 3. Whitelist WhatsApp for background data (So you still get messages)
    print("Whitelisting WhatsApp from background network restriction...")
    run_adb_shell("cmd netpolicy add restrict-background-whitelist com.whatsapp")
    
    # 4. Aggressive background process purge (Force-stop all third party apps)
    _, pkgs_out, _ = run_adb_shell("pm list packages -3")
    third_party_pkgs = [line.replace("package:", "").strip() for line in pkgs_out.splitlines() if line.strip()]
    
    print(f"Purging processes for {len(third_party_pkgs)} background apps...")
    for pkg in third_party_pkgs:
        if pkg != "com.whatsapp":
            run_adb_shell(f"am force-stop {pkg}")
            
    report = {
        "max_cached_processes": 24,
        "background_data_saver": "Enabled",
        "background_data_whitelist": ["com.whatsapp"],
        "purged_packages_count": len(third_party_pkgs) - 1
    }
    
    out_path = "/Users/anurag/.gemini/antigravity-cli/brain/bc82fa9b-634b-46ba-a680-2a8df369263b/extreme_boost_report.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=4)
        
    print(f"Extreme boosting complete. Saved report to {out_path}")

if __name__ == "__main__":
    main()
