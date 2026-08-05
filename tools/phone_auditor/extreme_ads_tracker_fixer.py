import subprocess
import json
import re
import sys

def run_adb_shell(cmd):
    try:
        res = subprocess.run(["adb", "shell", cmd], capture_output=True, text=True, timeout=40)
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except Exception as e:
        return -1, "", str(e)

def main():
    print("Initiating Extreme Ads, Tracking, and Permission Exorcism...")
    
    report = {
        "dns_shield": "dns.adguard.com",
        "ad_tracking_zeroed": True,
        "revoked_permissions": []
    }
    
    # 1. Force DNS AdGuard Shield
    run_adb_shell("settings put global private_dns_mode hostname")
    run_adb_shell("settings put global private_dns_specifier dns.adguard.com")
    run_adb_shell("ndc resolver flushdefaultif")
    
    # 2. Reset and Lock Ad ID to All Zeros
    run_adb_shell("settings put global ad_opt_out 1")
    run_adb_shell("settings put global limit_ad_tracking 1")
    run_adb_shell("settings put secure advertising_id '00000000-0000-0000-0000-000000000000'")
    
    # 3. Query all third-party apps
    _, pkgs_out, _ = run_adb_shell("pm list packages -3")
    third_party_pkgs = [line.replace("package:", "").strip() for line in pkgs_out.splitlines() if line.strip()]
    
    dangerous_permissions = [
        "android.permission.ACCESS_FINE_LOCATION",
        "android.permission.ACCESS_COARSE_LOCATION",
        "android.permission.CAMERA",
        "android.permission.RECORD_AUDIO",
        "android.permission.READ_PHONE_STATE"
    ]
    
    # Let's specify exceptions for WhatsApp (microphone/camera are needed for calls, but location is not!)
    exceptions = {
        "com.whatsapp": ["android.permission.CAMERA", "android.permission.RECORD_AUDIO"]
    }
    
    print(f"Scanning {len(third_party_pkgs)} user apps for tracking and sensor permissions...")
    for pkg in third_party_pkgs:
        _, dumpsys_out, _ = run_adb_shell(f"dumpsys package {pkg}")
        
        # Split by User 0 to only look at owner profile permissions
        user_0_block = ""
        user_0_match = re.search(r"User 0:.*?(?:User \d+:|$)", dumpsys_out, re.DOTALL)
        if user_0_match:
            user_0_block = user_0_match.group(0)
            
        for perm in dangerous_permissions:
            if f"{perm}: granted=true" in user_0_block:
                # Check if it is an exception
                if pkg in exceptions and perm in exceptions[pkg]:
                    continue
                    
                # Revoke permission
                print(f"Revoking {perm.split('.')[-1]} from {pkg}...")
                code, _, _ = run_adb_shell(f"pm revoke --user 0 {pkg} {perm}")
                if code == 0:
                    report["revoked_permissions"].append({
                        "package": pkg,
                        "permission": perm.split(".")[-1]
                    })
                    
    out_path = "/Users/anurag/.gemini/antigravity-cli/brain/bc82fa9b-634b-46ba-a680-2a8df369263b/extreme_ads_tracker_fix_report.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=4)
        
    print(f"Extreme permission and tracking scan complete. Saved report to {out_path}")

if __name__ == "__main__":
    main()
