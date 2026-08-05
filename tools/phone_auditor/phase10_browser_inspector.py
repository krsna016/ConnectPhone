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
    print("Starting Phase 10: Browser Audit...")
    
    report = {
        "default_browser": "Unknown",
        "installed_browsers": [],
        "browser_diagnostics": {}
    }
    
    # 1. Get default browser handler for http/https URLs
    _, default_out, _ = run_adb_shell("cmd package resolve-activity --brief http://google.com")
    # format:
    #   com.android.chrome/com.google.android.apps.chrome.Main
    match = re.search(r"([^\s/]+)/", default_out)
    if match:
        report["default_browser"] = match.group(1)
    else:
        report["default_browser"] = "None (No default browser set or prompt is active)"
        
    # 2. Check for installed browsers
    browsers_to_check = {
        "Google Chrome": "com.android.chrome",
        "Mozilla Firefox": "org.mozilla.firefox",
        "Opera": "com.opera.browser",
        "MIUI Browser (Mint/Xiaomi)": "com.mi.globalbrowser",
        "Brave Browser": "com.brave.browser",
        "Microsoft Edge": "com.microsoft.emmx",
        "DuckDuckGo": "com.duckduckgo.mobile.android"
    }
    
    for name, pkg in browsers_to_check.items():
        code, out, _ = run_adb_shell(f"pm list packages {pkg}")
        if out:
            # Query version
            _, ver_out, _ = run_adb_shell(f"dumpsys package {pkg} | grep versionName")
            version = ver_out.split("=")[-1].strip() if ver_out else "Unknown"
            
            # Check installer
            _, inst_out, _ = run_adb_shell(f"pm list packages -i {pkg}")
            installer = "Store" if "vending" in inst_out else "Sideloaded/OEM"
            
            report["installed_browsers"].append({
                "name": name,
                "package": pkg,
                "version": version,
                "installer_type": installer
            })
            
    # 3. Diagnostics details (check if Chrome or MIUI Browser have dangerous permissions)
    # Browsers should only have essential permissions (e.g. Internet, but location/camera should be runtime requested)
    for browser in report["installed_browsers"]:
        pkg = browser["package"]
        _, dumpsys_out, _ = run_adb_shell(f"dumpsys package {pkg}")
        
        # Check permissions granted specifically for User 0
        user_0_block = ""
        user_0_match = re.search(r"User 0:.*?(?:User \d+:|$)", dumpsys_out, re.DOTALL)
        if user_0_match:
            user_0_block = user_0_match.group(0)
            
        camera_granted = "android.permission.CAMERA: granted=true" in user_0_block
        location_granted = "android.permission.ACCESS_FINE_LOCATION: granted=true" in user_0_block
        microphone_granted = "android.permission.RECORD_AUDIO: granted=true" in user_0_block
        
        report["browser_diagnostics"][browser["name"]] = {
            "camera_access": "Granted" if camera_granted else "Revoked",
            "location_access": "Granted" if location_granted else "Revoked",
            "microphone_access": "Granted" if microphone_granted else "Revoked"
        }
        
    # Save JSON output
    out_path = "/Users/anurag/.gemini/antigravity-cli/brain/bc82fa9b-634b-46ba-a680-2a8df369263b/phase10_diagnostics.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=4)
        
    print(f"Phase 10 scan complete. Report saved to {out_path}")

if __name__ == "__main__":
    main()
