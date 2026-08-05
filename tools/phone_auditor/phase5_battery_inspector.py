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
    print("Starting Phase 5: Battery Engineering Audit...")
    
    report = {
        "battery_health": {},
        "system_radios_state": {},
        "battery_saver_config": {},
        "top_consumers": [],
        "adaptive_battery": "Unknown"
    }
    
    # 1. dumpsys battery basic metrics
    _, bat_out, _ = run_adb_shell("dumpsys battery")
    bat_props = {}
    for line in bat_out.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            bat_props[k.strip().lower()] = v.strip()
            
    temp = int(bat_props.get("temperature", 0)) / 10.0
    volt = int(bat_props.get("voltage", 0)) / 1000.0
    
    report["battery_health"] = {
        "status": bat_props.get("status", "Unknown"),
        "health": bat_props.get("health", "Unknown"),
        "level": f"{bat_props.get('level', '0')}%",
        "temperature": f"{temp} °C",
        "voltage": f"{volt} V",
        "technology": bat_props.get("technology", "Li-ion"),
        "ac_powered": bat_props.get("ac powered") == "true",
        "usb_powered": bat_props.get("usb powered") == "true",
        "wireless_powered": bat_props.get("wireless powered") == "true"
    }
    
    # 2. System Radios & Display Configuration
    _, wifi_on, _ = run_adb_shell("settings get global wifi_on")
    _, bt_on, _ = run_adb_shell("settings get global bluetooth_on")
    _, location_mode, _ = run_adb_shell("settings get secure location_mode")
    _, mobile_data, _ = run_adb_shell("settings get global mobile_data")
    _, hot_on, _ = run_adb_shell("settings get global wifi_ap_on")
    
    # Get refresh rate
    _, sf_dump, _ = run_adb_shell("dumpsys SurfaceFlinger")
    fps_match = re.search(r"refresh-rate:\s*([\d\.]+)", sf_dump)
    refresh_rate = f"{float(fps_match.group(1)):.0f} Hz" if fps_match else "60 Hz (Standard)"
    
    report["system_radios_state"] = {
        "wifi_enabled": wifi_on == "1",
        "bluetooth_enabled": bt_on == "1",
        "location_mode": location_mode if location_mode else "0",
        "mobile_data_enabled": mobile_data == "1",
        "hotspot_enabled": hot_on == "1",
        "display_refresh_rate": refresh_rate
    }
    
    # 3. Battery Saver & Adaptive Battery Configuration
    _, saver_on, _ = run_adb_shell("settings get global low_power")
    _, saver_trigger, _ = run_adb_shell("settings get global low_power_trigger_level")
    _, adaptive_bat, _ = run_adb_shell("settings get global adaptive_battery_management_enabled")
    
    report["battery_saver_config"] = {
        "battery_saver_active": saver_on == "1",
        "battery_saver_trigger_level": f"{saver_trigger}%" if saver_trigger else "0%"
    }
    report["adaptive_battery"] = "Enabled" if adaptive_bat == "1" else "Disabled"
    
    # 4. Top Consumers (via dumpsys batterystats)
    print("Checking batterystats top consumers...")
    _, bs_out, _ = run_adb_shell("dumpsys batterystats --daily")
    if not bs_out:
        _, bs_out, _ = run_adb_shell("dumpsys batterystats")
        
    # Search for UID consumption details or top draining apps
    consumers = []
    for line in bs_out.splitlines():
        if "Estimated power use by Uids:" in line or "Capacity:" in line:
            # We are close to the power usage stats
            pass
        match = re.search(r"Uid\s+(\d+):\s*([\d\.]+)\s*mAh", line)
        if match:
            uid = match.group(1)
            mah = float(match.group(2))
            consumers.append((uid, mah))
            
    # Sort consumers and take top 5
    consumers.sort(key=lambda x: x[1], reverse=True)
    for uid, mah in consumers[:5]:
        # Convert uid to package name if possible
        _, pkg_name, _ = run_adb_shell(f"cmd package list packages --uid {uid}")
        pkg_cleaned = pkg_name.replace("package:", "").split()[0] if pkg_name else f"System/UID {uid}"
        report["top_consumers"].append({
            "uid": uid,
            "package": pkg_cleaned,
            "estimated_consumption_mah": mah
        })
        
    # Save JSON output
    out_path = "/Users/anurag/.gemini/antigravity-cli/brain/bc82fa9b-634b-46ba-a680-2a8df369263b/phase5_diagnostics.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=4)
        
    print(f"Phase 5 scan complete. Report saved to {out_path}")

if __name__ == "__main__":
    main()
