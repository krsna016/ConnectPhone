import subprocess
import json
import re
import os
import sys

def run_adb(cmd_list):
    try:
        # Prepends 'adb' if it's not there, but let's assume cmd_list is just the args after 'adb'
        full_cmd = ["adb"] + cmd_list
        res = subprocess.run(full_cmd, capture_output=True, text=True, timeout=10)
        return res.stdout.strip(), res.stderr.strip(), res.returncode
    except subprocess.TimeoutExpired:
        return "", "Timeout", -1
    except Exception as e:
        return "", str(e), -1

def run_adb_shell(shell_cmd):
    return run_adb(["shell", shell_cmd])

def main():
    print("Starting Android Security Audit via ADB...")
    
    # 1. Check if ADB works and device is connected
    stdout, stderr, code = run_adb(["devices"])
    if code != 0 or not stdout:
        print(f"Error checking ADB devices: {stderr}")
        sys.exit(1)
        
    lines = stdout.splitlines()[1:]
    devices = [line.split()[0] for line in lines if line.strip() and "device" in line]
    
    if not devices:
        print("No Android devices found in 'device' state. Please ensure your phone is connected and USB debugging is authorized.")
        sys.exit(1)
        
    device_id = devices[0]
    print(f"Connected to device: {device_id}")
    
    audit_data = {
        "device_id": device_id,
        "system_info": {},
        "security_settings": {},
        "root_check": {},
        "privileged_apps": {},
        "installed_apps": [],
        "running_services": [],
        "network_connections": [],
        "anomalies": []
    }
    
    # 2. Collect System Info
    props = {
        "model": "ro.product.model",
        "manufacturer": "ro.product.manufacturer",
        "android_version": "ro.build.version.release",
        "sdk_version": "ro.build.version.sdk",
        "security_patch": "ro.build.version.security_patch",
        "build_tags": "ro.build.tags",
        "bootloader_locked": "ro.boot.flash.locked",
        "verified_boot": "ro.boot.verifiedbootstate"
    }
    
    for key, prop in props.items():
        val, _, _ = run_adb_shell(f"getprop {prop}")
        audit_data["system_info"][key] = val
        
    selinux, _, _ = run_adb_shell("getenforce")
    audit_data["system_info"]["selinux"] = selinux
    
    # 3. Security Settings
    adb_enabled, _, _ = run_adb_shell("settings get global adb_enabled")
    dev_settings, _, _ = run_adb_shell("settings get global development_settings_enabled")
    mock_loc, _, _ = run_adb_shell("settings get secure mock_location")
    
    audit_data["security_settings"] = {
        "usb_debugging_enabled": adb_enabled == "1",
        "developer_options_enabled": dev_settings == "1",
        "mock_locations_enabled": mock_loc == "1"
    }
    
    # Accessibility Services
    acc_services, _, _ = run_adb_shell("settings get secure enabled_accessibility_services")
    audit_data["security_settings"]["enabled_accessibility_services"] = [
        s.strip() for s in acc_services.split(":") if s.strip() and s.strip() != "null"
    ]
    
    # Device Administrators
    dev_admins, _, _ = run_adb_shell("dumpsys device_policy")
    active_admins = []
    for line in dev_admins.splitlines():
        if "Active Admin" in line or "admin=" in line:
            # try to extract package name
            match = re.search(r"ComponentInfo\{([^/]+)/", line)
            if match:
                active_admins.append(match.group(1))
            else:
                match2 = re.search(r"admin=([^/]+)/", line)
                if match2:
                    active_admins.append(match2.group(1))
    audit_data["security_settings"]["active_device_admins"] = list(set(active_admins))
    
    # Notification Listeners
    notif_listeners, _, _ = run_adb_shell("settings get secure enabled_notification_listeners")
    audit_data["security_settings"]["enabled_notification_listeners"] = [
        l.strip() for l in notif_listeners.split(":") if l.strip() and l.strip() != "null"
    ]
    
    # 4. Root / Su Check
    su_in_path, _, su_code = run_adb_shell("which su")
    su_existence = False
    for path in ["/system/bin/su", "/system/xbin/su", "/sbin/su", "/data/local/xbin/su", "/data/local/bin/su"]:
        out_check, _, check_code = run_adb_shell(f"ls {path}")
        if "No such file" not in out_check and out_check:
            su_existence = True
            break
            
    audit_data["root_check"] = {
        "su_in_path": su_code == 0 or bool(su_in_path),
        "su_file_exists": su_existence,
        "test_keys": "test-keys" in (audit_data["system_info"]["build_tags"] or "")
    }
    
    # Check for known root manager apps
    root_packages = ["com.topjohnwu.magisk", "me.weishu.kernelsu", "com.noshufou.android.su", "com.thirdparty.superuser"]
    found_root_apps = []
    for rp in root_packages:
        pkg_check, _, _ = run_adb_shell(f"pm path {rp}")
        if pkg_check:
            found_root_apps.append(rp)
    audit_data["root_check"]["found_root_apps"] = found_root_apps
    
    # 5. App Audit
    # Get all 3rd party packages & installers
    packages_out, _, _ = run_adb_shell("pm list packages -3 -i")
    
    package_installers = {}
    for line in packages_out.splitlines():
        if line.startswith("package:"):
            # format: package:com.example.app  installer=com.android.vending (or installer=null)
            parts = line.replace("package:", "").split("installer=")
            if len(parts) == 2:
                pkg = parts[0].strip()
                installer = parts[1].strip()
                if installer == "null" or not installer:
                    installer = None
                package_installers[pkg] = installer
            else:
                pkg = parts[0].strip()
                package_installers[pkg] = None
                
    # Inspect each package
    print(f"Auditing {len(package_installers)} third-party packages...")
    for pkg, installer in package_installers.items():
        # Get dumpsys package info for permissions
        dumpsys_out, _, _ = run_adb_shell(f"dumpsys package {pkg}")
        
        # Parse first install time
        install_time = "Unknown"
        match_time = re.search(r"firstInstallTime=([\d\-]+\s+[\d:]+)", dumpsys_out)
        if match_time:
            install_time = match_time.group(1)
            
        # Parse version
        version_name = "Unknown"
        match_ver = re.search(r"versionName=([^\s]+)", dumpsys_out)
        if match_ver:
            version_name = match_ver.group(1)
            
        # Extract requested and granted permissions
        requested_perms = []
        granted_perms = []
        
        req_section = False
        inst_section = False
        runtime_section = False
        
        for line in dumpsys_out.splitlines():
            line_strip = line.strip()
            if "requested permissions:" in line:
                req_section = True
                inst_section = False
                runtime_section = False
                continue
            elif "install permissions:" in line:
                req_section = False
                inst_section = True
                runtime_section = False
                continue
            elif "runtime permissions:" in line:
                req_section = False
                inst_section = False
                runtime_section = True
                continue
            elif ":" in line and not line_strip.startswith("android.permission.") and not line_strip.startswith("com."):
                # Section ended
                req_section = False
                inst_section = False
                runtime_section = False
                
            if req_section and line_strip:
                requested_perms.append(line_strip)
            elif inst_section and line_strip:
                # e.g., android.permission.INTERNET: granted=true
                parts = line_strip.split(":")
                if len(parts) >= 2 and "granted=true" in parts[1]:
                    granted_perms.append(parts[0].strip())
            elif runtime_section and line_strip:
                # e.g., android.permission.CAMERA: granted=true, flags=[...]
                parts = line_strip.split(":")
                if len(parts) >= 2 and "granted=true" in parts[1]:
                    granted_perms.append(parts[0].strip())
                    
        # Let's clean up permissions list
        requested_perms = [p for p in requested_perms if p.startswith("android.permission.") or p.startswith("com.android.")]
        granted_perms = [p for p in granted_perms if p.startswith("android.permission.") or p.startswith("com.android.")]
        
        # Check package size or path
        path_out, _, _ = run_adb_shell(f"pm path {pkg}")
        app_path = path_out.replace("package:", "").strip() if path_out else "Unknown"
        
        audit_data["installed_apps"].append({
            "package": pkg,
            "installer": installer,
            "version": version_name,
            "install_time": install_time,
            "app_path": app_path,
            "requested_permissions": requested_perms,
            "granted_permissions": granted_perms
        })
        
    # 6. Network Connections (netstat / ss / /proc/net/tcp)
    print("Checking network sockets...")
    netstat_out, _, _ = run_adb_shell("netstat -anp")
    if not netstat_out or "Permission denied" in netstat_out:
        netstat_out, _, _ = run_adb_shell("netstat -an")
        
    connections = []
    for line in netstat_out.splitlines():
        # filter established or listening
        if "ESTABLISHED" in line or "LISTEN" in line:
            parts = line.split()
            if len(parts) >= 4:
                connections.append(line)
    audit_data["network_connections"] = connections
    
    # 7. Running Services
    print("Checking active services...")
    services_out, _, _ = run_adb_shell("dumpsys activity services")
    active_services = []
    current_service = None
    for line in services_out.splitlines():
        if "  * ServiceRecord{" in line:
            # e.g.,   * ServiceRecord{e6a3928 u0 com.example.mobile/.MyService}
            match = re.search(r"ServiceRecord\{[^\s]+\s+[^\s]+\s+([^/]+)/", line)
            if match:
                active_services.append(match.group(1))
    audit_data["running_services"] = list(set(active_services))
    
    # 8. Analyse Anomalies & Risk Levels
    # Define dangerous permissions
    dangerous_perms = {
        "android.permission.BIND_ACCESSIBILITY_SERVICE": "Accessibility Service (Can log keys, capture screen, click elements)",
        "android.permission.SYSTEM_ALERT_WINDOW": "System Overlay (Can draw over other apps, perform overlay/phishing attacks)",
        "android.permission.READ_SMS": "Read SMS (Can intercept 2FA OTP codes)",
        "android.permission.RECEIVE_SMS": "Receive SMS (Can intercept 2FA OTP codes)",
        "android.permission.SEND_SMS": "Send SMS (Can send premium rate messages or exfiltrate data)",
        "android.permission.RECORD_AUDIO": "Microphone Access (Can record surrounding conversations/spyware)",
        "android.permission.CAMERA": "Camera Access (Can take photos/videos secretly)",
        "android.permission.ACCESS_FINE_LOCATION": "Precise Location (Can track movement)",
        "android.permission.ACCESS_BACKGROUND_LOCATION": "Background Location (Can track movement silently in background)",
        "android.permission.READ_CONTACTS": "Read Contacts (Can harvest contacts list)",
        "android.permission.READ_CALL_LOG": "Read Call Log (Can harvest call history)",
        "android.permission.WRITE_CALL_LOG": "Write Call Log (Can manipulate call history)",
        "android.permission.REQUEST_INSTALL_PACKAGES": "Install Unknown Apps (Can sideload additional malware)",
        "android.permission.WRITE_SETTINGS": "Modify System Settings (Can lower security configuration)",
        "android.permission.WRITE_SECURE_SETTINGS": "Modify Secure System Settings (Requires high privileges)"
    }
    
    anomalies = []
    
    # Root status alert
    if audit_data["root_check"]["su_file_exists"] or audit_data["root_check"]["su_in_path"]:
        anomalies.append({
            "level": "CRITICAL",
            "type": "ROOTED_DEVICE",
            "message": "Device is rooted (su binary detected). This bypasses the Android sandbox and exposes the entire system to malware.",
            "details": f"su found. Path check: {audit_data['root_check']['su_in_path']}, file check: {audit_data['root_check']['su_file_exists']}"
        })
        
    if audit_data["root_check"]["test_keys"]:
        anomalies.append({
            "level": "MEDIUM",
            "type": "TEST_KEYS_BUILD",
            "message": "System build contains 'test-keys'. This indicates a custom ROM or debug firmware which might be less secure.",
            "details": f"ro.build.tags={audit_data['system_info']['build_tags']}"
        })
        
    # Check accessibility services
    for service in audit_data["security_settings"]["enabled_accessibility_services"]:
        # Parse package
        pkg = service.split("/")[0]
        # Check if sideloaded
        installer = package_installers.get(pkg, "Unknown System/Pre-installed")
        is_sideloaded = (installer is None)
        
        level = "CRITICAL" if is_sideloaded else "HIGH"
        anomalies.append({
            "level": level,
            "type": "ACCESSIBILITY_SERVICE_ENABLED",
            "message": f"Accessibility Service is active for app '{pkg}' (Installer: {installer or 'Sideloaded/None'}). This is a critical security risk because it grants full screen read/write capabilities.",
            "details": f"Service: {service}"
        })
        
    # Check device administrators
    for admin in audit_data["security_settings"]["active_device_admins"]:
        installer = package_installers.get(admin, "Unknown System/Pre-installed")
        is_sideloaded = (installer is None)
        level = "CRITICAL" if is_sideloaded else "HIGH"
        anomalies.append({
            "level": level,
            "type": "DEVICE_ADMIN_ENABLED",
            "message": f"App '{admin}' is active as a Device Administrator (Installer: {installer or 'Sideloaded/None'}). Device admins can wipe the device, change lock screens, and are very difficult to uninstall.",
            "details": f"Admin: {admin}"
        })
        
    # Check notification listeners
    for listener in audit_data["security_settings"]["enabled_notification_listeners"]:
        pkg = listener.split("/")[0]
        installer = package_installers.get(pkg, "Unknown System/Pre-installed")
        is_sideloaded = (installer is None)
        level = "HIGH" if is_sideloaded else "MEDIUM"
        anomalies.append({
            "level": level,
            "type": "NOTIFICATION_LISTENER_ENABLED",
            "message": f"App '{pkg}' is authorized as a Notification Listener (Installer: {installer or 'Sideloaded/None'}). It can read all notification contents, including incoming SMS OTP codes.",
            "details": f"Listener: {listener}"
        })
        
    # Check apps
    for app in audit_data["installed_apps"]:
        pkg = app["package"]
        installer = app["installer"]
        granted = app["granted_permissions"]
        
        # 1. Suspicious package name
        is_suspicious_name = False
        if pkg.startswith("com.example.") or pkg.startswith("test.") or pkg.startswith("com.demo.") or "malware" in pkg or "spy" in pkg:
            is_suspicious_name = True
            
        # 2. Sideloaded apps
        is_sideloaded = (installer is None)
        
        # Let's count dangerous permissions
        app_dangerous_granted = []
        for p in granted:
            if p in dangerous_perms:
                app_dangerous_granted.append((p, dangerous_perms[p]))
                
        # Risk assessment for this app
        if is_suspicious_name:
            anomalies.append({
                "level": "CRITICAL" if len(app_dangerous_granted) > 0 else "HIGH",
                "type": "SUSPICIOUS_PACKAGE_NAME",
                "message": f"App '{pkg}' has a suspicious placeholder name. Sideloaded: {is_sideloaded}.",
                "details": f"Granted dangerous permissions: {[p[0].split('.')[-1] for p in app_dangerous_granted]}"
            })
            
        # If sideloaded and has multiple dangerous permissions
        if is_sideloaded and len(app_dangerous_granted) >= 2:
            # Let's see if it's Revanced (often sideloaded and safe, but has microG etc., we should check, but alert the user)
            is_known_sideload = "revanced" in pkg or "gms" in pkg or "microg" in pkg
            level = "MEDIUM" if is_known_sideload else "HIGH"
            
            anomalies.append({
                "level": level,
                "type": "SIDELOADED_APP_WITH_DANGEROUS_PERMISSIONS",
                "message": f"Sideloaded app '{pkg}' is granted {len(app_dangerous_granted)} dangerous permissions. This is a common pattern for spyware/malware.",
                "details": ", ".join([f"{p[0].split('.')[-1]} ({p[1]})" for p in app_dangerous_granted])
            })
        elif is_sideloaded and "android.permission.REQUEST_INSTALL_PACKAGES" in granted:
            anomalies.append({
                "level": "HIGH",
                "type": "SIDELOADED_APP_CAN_INSTALL_PACKAGES",
                "message": f"Sideloaded app '{pkg}' has permission to install other packages. This can allow it to act as a dropper.",
                "details": "Granted: REQUEST_INSTALL_PACKAGES"
            })
        elif not is_sideloaded and len(app_dangerous_granted) >= 4:
            # Store-installed with excessive dangerous permissions
            anomalies.append({
                "level": "MEDIUM",
                "type": "EXCESSIVE_PERMISSIONS_STORE_APP",
                "message": f"App '{pkg}' (installed from {installer}) is granted {len(app_dangerous_granted)} dangerous permissions. Ensure this app actually needs all these privileges.",
                "details": ", ".join([f"{p[0].split('.')[-1]}" for p in app_dangerous_granted])
            })
            
    audit_data["anomalies"] = anomalies
    
    # 9. Output to file
    out_dir = "/Users/anurag/.gemini/antigravity-cli/brain/bc82fa9b-634b-46ba-a680-2a8df369263b"
    os.makedirs(out_dir, exist_ok=True)
    report_path = os.path.join(out_dir, "phone_audit_report.json")
    with open(report_path, "w") as f:
        json.dump(audit_data, f, indent=4)
        
    print(f"\nAudit completed. JSON report saved to: {report_path}")
    print(f"Total Anomalies Found: {len(anomalies)}")
    for a in anomalies:
        print(f"[{a['level']}] {a['type']}: {a['message']}")

if __name__ == "__main__":
    main()
