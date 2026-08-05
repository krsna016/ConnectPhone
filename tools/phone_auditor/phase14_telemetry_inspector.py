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
    print("Starting Phase 14: Tracking & Telemetry Audit...")
    
    report = {
        "dns_level_blocking": "None",
        "system_telemetry_daemons": [],
        "system_properties_telemetry": []
    }
    
    # 1. DNS Level ad-blocking check
    _, dns_spec, _ = run_adb_shell("settings get global private_dns_specifier")
    if dns_spec == "dns.adguard.com":
        report["dns_level_blocking"] = "Enabled (AdGuard DNS Active - Blocking Telemetry/Ads)"
    else:
        report["dns_level_blocking"] = dns_spec if dns_spec else "Disabled"
        
    # 2. Check telemetry daemon package states
    telemetry_daemons = {
        "Xiaomi OneTrack Analytics": "com.miui.analytics",
        "Xiaomi Ads Solution (msa)": "com.miui.msa.global",
        "Xiaomi App Finder Daemon": "com.mi.appfinder"
    }
    
    for name, pkg in telemetry_daemons.items():
        _, dumpsys_out, _ = run_adb_shell(f"dumpsys package {pkg}")
        user_0_block = ""
        user_0_match = re.search(r"User 0:.*?(?:User \d+:|$)", dumpsys_out, re.DOTALL)
        if user_0_match:
            user_0_block = user_0_match.group(0)
            
        is_installed = "installed=true" in user_0_block
        report["system_telemetry_daemons"].append({
            "name": name,
            "package": pkg,
            "status": "Inactive/Uninstalled" if not is_installed else "Active (Telemetry Risk)"
        })
        
    # 3. Check system properties telemetry
    _, props_out, _ = run_adb_shell("getprop")
    for line in props_out.splitlines():
        if "analytics" in line.lower() or "telemetry" in line.lower() or "one-track" in line.lower():
            report["system_properties_telemetry"].append(line.strip())
            
    # Save JSON output
    out_path = "/Users/anurag/.gemini/antigravity-cli/brain/bc82fa9b-634b-46ba-a680-2a8df369263b/phase14_diagnostics.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=4)
        
    print(f"Phase 14 scan complete. Report saved to {out_path}")

if __name__ == "__main__":
    main()
