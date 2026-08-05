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
    print("Starting Phase 7: Network Engineering Audit...")
    
    report = {
        "private_dns": {},
        "proxy_settings": {},
        "data_saver": "Unknown",
        "wifi_connection_details": {},
        "active_vpn": "None",
        "open_sockets": []
    }
    
    # 1. Private DNS
    _, dns_mode, _ = run_adb_shell("settings get global private_dns_mode")
    _, dns_spec, _ = run_adb_shell("settings get global private_dns_specifier")
    report["private_dns"] = {
        "mode": dns_mode if dns_mode else "off",
        "specifier": dns_spec if dns_spec else "None"
    }
    
    # 2. Proxy Settings
    _, proxy, _ = run_adb_shell("settings get global http_proxy")
    _, global_proxy, _ = run_adb_shell("settings get global global_http_proxy_host")
    report["proxy_settings"] = {
        "http_proxy": proxy if proxy and proxy != "null" else "None",
        "global_proxy_host": global_proxy if global_proxy and global_proxy != "null" else "None"
    }
    
    # 3. Data Saver Mode
    _, ds_out, _ = run_adb_shell("cmd netpolicy get restrict-background-status")
    # restrict-background-status returns integer or enabled/disabled
    # 1 = disabled, 3 = enabled (varies, but usually status 3 means enabled, status 1 means disabled)
    report["data_saver"] = "Enabled" if "enabled" in ds_out.lower() or ds_out == "3" else "Disabled"
    
    # 4. Wi-Fi Details
    print("Checking Wi-Fi connection states...")
    _, wifi_out, _ = run_adb_shell("dumpsys wifi")
    ssid_match = re.search(r"SSID:\s*\"([^\"]+)\"", wifi_out)
    rssi_match = re.search(r"RSSI:\s*(-\d+)", wifi_out)
    link_match = re.search(r"TxLinkSpeed:\s*(\d+)", wifi_out)
    
    report["wifi_connection_details"] = {
        "connected_ssid": ssid_match.group(1) if ssid_match else "Unknown",
        "signal_strength_rssi": f"{rssi_match.group(1)} dBm" if rssi_match else "Unknown",
        "link_speed_mbps": f"{link_match.group(1)} Mbps" if link_match else "Unknown"
    }
    
    # 5. VPN settings
    print("Checking active VPN states...")
    _, conn_out, _ = run_adb_shell("dumpsys connectivity")
    vpn_active = False
    for line in conn_out.splitlines():
        if "VPN" in line and "[CONNECTED]" in line:
            vpn_active = True
            report["active_vpn"] = line.strip()
            break
            
    # 6. Sockets & Connections (Checking for listening or established sockets)
    print("Checking open sockets...")
    _, netstat_out, _ = run_adb_shell("netstat -a -n 2>/dev/null")
    if not netstat_out:
        _, netstat_out, _ = run_adb_shell("ss -t -u -a 2>/dev/null")
        
    for line in netstat_out.splitlines()[:50]: # Cap to first 50 sockets to avoid massive report size
        line_strip = line.strip()
        if "ESTABLISHED" in line or "LISTEN" in line:
            report["open_sockets"].append(line_strip)
            
    # Save JSON output
    out_path = "/Users/anurag/.gemini/antigravity-cli/brain/bc82fa9b-634b-46ba-a680-2a8df369263b/phase7_diagnostics.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=4)
        
    print(f"Phase 7 scan complete. Report saved to {out_path}")

if __name__ == "__main__":
    main()
