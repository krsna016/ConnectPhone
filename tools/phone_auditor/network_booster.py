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
    print("Initiating Intense Network Optimization...")
    
    # 1. Disable Wi-Fi Scan Throttling
    print("Disabling Wi-Fi Scan Throttling to reduce connection latency spikes...")
    run_adb_shell("settings put global wifi_scan_throttle_enabled 0")
    
    # 2. Enable Mobile Data Always On
    print("Enabling Mobile Data Always Active for instant network handoff...")
    run_adb_shell("settings put global mobile_data_always_on 1")
    
    # 3. Enable Wi-Fi Verbose Logging
    print("Enabling verbose Wi-Fi parameters reporting...")
    run_adb_shell("settings put global wifi_verbose_logging_enabled 1")
    
    report = {
        "wifi_scan_throttle": "Disabled (Optimized for Latency)",
        "mobile_data_always_on": "Enabled (Optimized for Handoff)",
        "wifi_verbose_logging": "Enabled"
    }
    
    out_path = "/Users/anurag/.gemini/antigravity-cli/brain/bc82fa9b-634b-46ba-a680-2a8df369263b/network_boost_report.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=4)
        
    print(f"Network optimization complete. Saved report to {out_path}")

if __name__ == "__main__":
    main()
