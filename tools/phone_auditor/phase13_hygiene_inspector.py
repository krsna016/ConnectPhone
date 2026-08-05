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
    print("Starting Phase 13: Digital Hygiene...")
    
    report = {
        "unused_apps_detected": [],
        "bloatware_candidates": [],
        "disabled_packages": []
    }
    
    # 1. Check for unused applications
    # Look up usagestats and list packages that have not been used recently (or never)
    print("Analyzing application usage patterns...")
    _, usage_out, _ = run_adb_shell("dumpsys usagestats")
    
    # Get 3rd party packages
    _, pkg_out, _ = run_adb_shell("pm list packages -3")
    third_party_pkgs = [line.replace("package:", "").strip() for line in pkg_out.splitlines() if line.strip()]
    
    for pkg in third_party_pkgs:
        pkg_escaped = re.escape(pkg)
        match = re.search(rf"package={pkg_escaped}\s+.*\s+lastTimeUsed=([\d\-]+\s+[\d:]+|[\d]+)", usage_out)
        if not match:
            # Not found in active usagestats (meaning not used since stats reset/boot)
            report["unused_apps_detected"].append(pkg)
            
    # 2. Check for common Xiaomi/MIUI bloatware packages
    bloatware_list = {
        "MIUI System Ads (msa)": "com.miui.msa.global",
        "Xiaomi Analytics (OneTrack)": "com.miui.analytics",
        "Xiaomi GetApps App Store": "com.xiaomi.mipicks",
        "App Finder (App store helper)": "com.mi.appfinder",
        "Xiaomi Games App Store": "com.xiaomi.glgm",
        "Xiaomi Pay Service": "com.xiaomi.payment",
        "Xiaomi Daemon (Joyose)": "com.xiaomi.joyose"
    }
    
    print("Scanning for bloatware candidate packages...")
    for name, pkg in bloatware_list.items():
        code, out, _ = run_adb_shell(f"pm list packages {pkg}")
        if out:
            # Check if currently installed and enabled for User 0
            _, dumpsys_out, _ = run_adb_shell(f"dumpsys package {pkg}")
            user_0_block = ""
            user_0_match = re.search(r"User 0:.*?(?:User \d+:|$)", dumpsys_out, re.DOTALL)
            if user_0_match:
                user_0_block = user_0_match.group(0)
            
            is_installed = "installed=true" in user_0_block
            is_disabled = "disabled" in user_0_block.lower() or "disabled=true" in user_0_block.lower() or not is_installed
            
            report["bloatware_candidates"].append({
                "name": name,
                "package": pkg,
                "status": "Disabled" if is_disabled else "Enabled (Bloatware Active)"
            })
            
    # 3. Get all currently disabled packages
    print("Getting disabled packages...")
    _, disabled_out, _ = run_adb_shell("pm list packages -d")
    report["disabled_packages"] = [line.replace("package:", "").strip() for line in disabled_out.splitlines() if line.strip()]
    
    # Save JSON output
    out_path = "/Users/anurag/.gemini/antigravity-cli/brain/bc82fa9b-634b-46ba-a680-2a8df369263b/phase13_diagnostics.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=4)
        
    print(f"Phase 13 scan complete. Report saved to {out_path}")

if __name__ == "__main__":
    main()
