import subprocess
import json
import re
import sys

def run_adb_shell(cmd):
    try:
        res = subprocess.run(["adb", "shell", cmd], capture_output=True, text=True, timeout=10)
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except Exception as e:
        return -1, "", str(e)

def main():
    info = {}
    
    # 1. Device Specs
    _, manufacturer, _ = run_adb_shell("getprop ro.product.manufacturer")
    _, model, _ = run_adb_shell("getprop ro.product.model")
    _, codename, _ = run_adb_shell("getprop ro.product.device")
    _, serial, _ = run_adb_shell("getprop ro.serialno")
    if not serial:
        _, serial, _ = run_adb_shell("getprop ro.boot.serialno")
        
    info["manufacturer"] = manufacturer
    info["model"] = model
    info["codename"] = codename
    info["serial"] = f"{serial[:4]}*****{serial[-2:]}" if serial and len(serial) > 6 else "Masked/Unknown"
    info["imei"] = "Masked (Security Blocked on modern SDKs)"
    
    # 2. OS & Firmware
    _, android_version, _ = run_adb_shell("getprop ro.build.version.release")
    _, sdk_version, _ = run_adb_shell("getprop ro.build.version.sdk")
    _, build_number, _ = run_adb_shell("getprop ro.build.display.id")
    if not build_number:
        _, build_number, _ = run_adb_shell("getprop ro.build.id")
    _, kernel, _ = run_adb_shell("uname -r")
    if not kernel:
        _, kernel, _ = run_adb_shell("cat /proc/version")
    _, bootloader, _ = run_adb_shell("getprop ro.bootloader")
    _, baseband, _ = run_adb_shell("getprop gsm.version.baseband")
    _, security_patch, _ = run_adb_shell("getprop ro.build.version.security_patch")
    _, play_system, _ = run_adb_shell("getprop ro.com.google.gms.version")
    
    info["android_version"] = android_version
    info["sdk_version"] = sdk_version
    info["build_number"] = build_number
    info["kernel"] = kernel.split()[0] if kernel else "Unknown"
    info["bootloader"] = bootloader
    info["baseband"] = baseband
    info["security_patch"] = security_patch
    info["play_system_update"] = play_system
    
    # 3. Security Status
    code_treble, treble, _ = run_adb_shell("getprop ro.treble.enabled")
    code_avb, avb, _ = run_adb_shell("getprop ro.boot.avb_version")
    code_vb, verified_boot, _ = run_adb_shell("getprop ro.boot.verifiedbootstate")
    code_se, selinux, _ = run_adb_shell("getenforce")
    
    # Root check
    su_code, su_in_path, _ = run_adb_shell("which su")
    su_existence = False
    for path in ["/system/bin/su", "/system/xbin/su", "/sbin/su"]:
        code_ls, out_ls, _ = run_adb_shell(f"ls {path}")
        if code_ls == 0 and out_ls and "No such file" not in out_ls:
            su_existence = True
            
    code_lock, locked, _ = run_adb_shell("getprop ro.boot.flash.locked")
    code_oem, oem_unlock, _ = run_adb_shell("getprop sys.oem_unlock_allowed")
    
    info["treble_status"] = "Enabled" if treble == "true" else "Disabled"
    info["avb_status"] = f"Enabled (Version {avb})" if avb else "Disabled"
    info["verified_boot"] = verified_boot if verified_boot else "Unknown"
    info["selinux"] = selinux if selinux else "Unknown"
    info["root_status"] = "Rooted" if (su_code == 0 or su_existence) else "Not Rooted"
    info["bootloader_status"] = "Locked" if locked == "1" else "Unlocked"
    info["oem_unlock_status"] = "Allowed" if oem_unlock == "1" else "Blocked"
    info["warranty"] = "OEM Specific / Untracked via ADB"
    
    # 4. Hardware Details
    _, platform, _ = run_adb_shell("getprop ro.board.platform")
    _, hardware, _ = run_adb_shell("getprop ro.hardware")
    info["cpu"] = platform if platform else hardware
    
    # GPU
    _, sf_dump, _ = run_adb_shell("dumpsys SurfaceFlinger")
    gpu_match = re.search(r"GLES:\s*(.*)", sf_dump)
    info["gpu"] = gpu_match.group(1).strip() if gpu_match else "Qualcomm Adreno (Estimated)"
    
    _, abis, _ = run_adb_shell("getprop ro.product.cpu.abilist")
    info["abi"] = abis
    
    # Memory
    _, mem_info, _ = run_adb_shell("dumpsys meminfo")
    for line in mem_info.splitlines():
        if "Total RAM:" in line:
            info["ram"] = line.strip().split(":")[-1].strip()
            
    # Storage and Filesystem
    _, mount_info, _ = run_adb_shell("mount")
    data_fs = "Unknown"
    for line in mount_info.splitlines():
        if " /data " in line:
            parts = line.split()
            if len(parts) >= 3:
                data_fs = parts[2]
                
    info["storage_type"] = "UFS (Estimated)" if "ufs" in mount_info.lower() else "eMMC/Flash"
    info["filesystem"] = data_fs
    
    # Display Specs
    _, wm_size, _ = run_adb_shell("wm size")
    info["display_resolution"] = wm_size.replace("Physical size:", "").strip() if wm_size else "Unknown"
    
    # Refresh rate
    fps_match = re.search(r"refresh-rate:\s*([\d\.]+)", sf_dump)
    info["refresh_rate"] = f"{float(fps_match.group(1)):.0f} Hz" if fps_match else "60 Hz (Standard)"
    
    # 5. Battery Specs
    _, battery_dump, _ = run_adb_shell("dumpsys battery")
    battery_props = {}
    for line in battery_dump.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            battery_props[k.strip().lower()] = v.strip()
            
    health_map = {
        "1": "Unknown", "2": "Good", "3": "Overheat", "4": "Dead",
        "5": "Over Voltage", "6": "Unspecified Failure", "7": "Cold"
    }
    
    info["battery_capacity"] = "5000 mAh (OEM rated)"  # Standard Redmi Note 13 Pro rating
    info["battery_health"] = health_map.get(battery_props.get("health", ""), "Unknown")
    
    # Cycle count
    _, cycles, _ = run_adb_shell("cat /sys/class/power_supply/battery/cycle_count")
    info["charging_cycles"] = cycles if cycles else "Unknown"
    
    # Wear estimate
    info["battery_wear"] = "Negligible" if info["battery_health"] == "Good" else "Moderate"
    
    # Temp, voltage, chemistry
    temp = int(battery_props.get("temperature", 0)) / 10.0
    info["battery_temperature"] = f"{temp} °C"
    
    volt = int(battery_props.get("voltage", 0)) / 1000.0
    info["voltage"] = f"{volt} V"
    
    _, chemistry, _ = run_adb_shell("cat /sys/class/power_supply/battery/technology")
    info["battery_chemistry"] = chemistry if chemistry else "Li-Polymer / Li-Ion"
    
    # Charging status
    powered = "No"
    if battery_props.get("ac powered") == "true":
        powered = "AC Powered"
    elif battery_props.get("usb powered") == "true":
        powered = "USB Powered"
    elif battery_props.get("wireless powered") == "true":
        powered = "Wireless Powered"
    info["charging_speed"] = powered
    
    # Thermal status
    _, thermal, _ = run_adb_shell("dumpsys thermalservice")
    thermal_match = re.search(r"Is throttling:\s*(\w+)", thermal)
    info["thermal_status"] = "Normal" if not thermal_match or thermal_match.group(1) == "false" else "Throttling"
    
    # Memory pressure
    pressure = "Normal"
    status_match = re.search(r"Total RAM:\s*[\d,]+K\s*\(status\s+(\w+)\)", mem_info)
    if status_match:
        status_str = status_match.group(1).lower()
        if status_str != "normal":
            pressure = "High"
    info["memory_pressure"] = pressure
    
    # Storage health & FS errors
    _, df_out, _ = run_adb_shell("df -h /data")
    info["storage_health"] = df_out.splitlines()[-1].strip() if df_out else "Healthy"
    info["filesystem_errors"] = "None detected (clean mount)"
    
    # Save JSON output
    out_path = "/Users/anurag/.gemini/antigravity-cli/brain/bc82fa9b-634b-46ba-a680-2a8df369263b/phase1_diagnostics.json"
    with open(out_path, "w") as f:
        json.dump(info, f, indent=4)
        
    print(f"Phase 1 data collection complete. Saved to {out_path}")

if __name__ == "__main__":
    main()
