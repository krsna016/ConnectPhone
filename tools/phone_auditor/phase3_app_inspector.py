import subprocess
import json
import re
import sys

def run_adb_shell(cmd):
    try:
        res = subprocess.run(["adb", "shell", cmd], capture_output=True, text=True, timeout=20)
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except Exception as e:
        return -1, "", str(e)

def main():
    print("Starting Phase 3: Enterprise Application Audit inspector...")
    
    # 1. Get all 3rd party packages & installers
    code_pkg, pkg_out, _ = run_adb_shell("pm list packages -3 -i")
    if code_pkg != 0 or not pkg_out:
        print("Failed to get third-party packages.")
        sys.exit(1)
        
    package_installers = {}
    for line in pkg_out.splitlines():
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
                
    # 2. Get active accessibility services, device admins, overlays
    _, acc_out, _ = run_adb_shell("settings get secure enabled_accessibility_services")
    acc_services = [s.strip().split("/")[0] for s in acc_out.split(":") if s.strip() and s.strip() != "null"]
    
    # Device administrators
    _, dev_admins_out, _ = run_adb_shell("dumpsys device_policy")
    active_admins = []
    for line in dev_admins_out.splitlines():
        if "ComponentInfo{" in line or "admin=" in line:
            match = re.search(r"ComponentInfo\{([^/]+)/", line)
            if match:
                active_admins.append(match.group(1))
            else:
                match2 = re.search(r"admin=([^/]+)/", line)
                if match2:
                    active_admins.append(match2.group(1))
    active_admins = list(set(active_admins))
    
    # Overlay apps (SYSTEM_ALERT_WINDOW)
    # We can check which apps have overlay permission using appops
    overlay_apps = []
    
    # Usage Stats (for Last Used time)
    _, usage_out, _ = run_adb_shell("dumpsys usagestats")
    
    app_audit = []
    
    print(f"Auditing {len(package_installers)} packages...")
    for pkg, installer in package_installers.items():
        print(f"Checking package: {pkg} ...")
        # Dumpsys package info
        _, dumpsys_out, _ = run_adb_shell(f"dumpsys package {pkg}")
        
        # Parse version
        ver_match = re.search(r"versionName=([^\s]+)", dumpsys_out)
        version = ver_match.group(1) if ver_match else "Unknown"
        
        # Parse timestamps
        install_time = "Unknown"
        update_time = "Unknown"
        match_install = re.search(r"firstInstallTime=([\d\-]+\s+[\d:]+)", dumpsys_out)
        if match_install:
            install_time = match_install.group(1)
        match_update = re.search(r"lastUpdateTime=([\d\-]+\s+[\d:]+)", dumpsys_out)
        if match_update:
            update_time = match_update.group(1)
            
        # Signature/Signatures
        sig_match = re.search(r"signatures=\[\s*([a-fA-F0-9,\s]+)\s*\]", dumpsys_out)
        if not sig_match:
            sig_match = re.search(r"signatures=\[Signature\{([a-fA-F0-9]+)\s*\}\s*\]", dumpsys_out)
        signature = sig_match.group(1).strip().replace("\n", "").replace(" ", "")[:40] + "..." if sig_match else "Official OEM/Store Signed"
        
        # Parse last used from usagestats
        last_used = "Not recently used"
        pkg_escaped = re.escape(pkg)
        usage_match = re.search(rf"package={pkg_escaped}\s+.*\s+lastTimeUsed=([\d\-]+\s+[\d:]+|[\d]+)", usage_out)
        if usage_match:
            last_used = usage_match.group(1)
            
        # Parse permissions
        requested_permissions = []
        granted_permissions = []
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
                req_section = False
                inst_section = False
                runtime_section = False
                
            if req_section and line_strip:
                requested_permissions.append(line_strip)
            elif inst_section and line_strip:
                parts = line_strip.split(":")
                if len(parts) >= 2 and "granted=true" in parts[1]:
                    granted_permissions.append(parts[0].strip())
            elif runtime_section and line_strip:
                parts = line_strip.split(":")
                if len(parts) >= 2 and "granted=true" in parts[1]:
                    granted_permissions.append(parts[0].strip())
                    
        # Filter permission arrays
        requested_permissions = [p for p in requested_permissions if p.startswith("android.permission.") or p.startswith("com.android.")]
        granted_permissions = [p for p in granted_permissions if p.startswith("android.permission.") or p.startswith("com.android.")]
        
        # AppOps details
        _, alert_out, _ = run_adb_shell(f"appops get {pkg} OP_SYSTEM_ALERT_WINDOW 2>/dev/null")
        has_overlay = "allow" in alert_out.lower()
        if has_overlay:
            overlay_apps.append(pkg)
            
        _, bg_out, _ = run_adb_shell(f"appops get {pkg} RUN_IN_BACKGROUND 2>/dev/null")
        bg_activity = "allow" in bg_out.lower() or not bg_out
        
        # Check active foreground service
        _, activity_services_out, _ = run_adb_shell(f"dumpsys activity services {pkg} 2>/dev/null")
        has_fg_service = "ServiceRecord" in activity_services_out
        
        # Check SDKs and trackers via library strings in package info
        # Sideloaded / Play Store classification
        supply_chain = "Low"
        if installer is None:
            supply_chain = "Medium (Sideloaded)"
            if pkg.startswith("com.example.") or pkg.startswith("test."):
                supply_chain = "High (Unknown Dev Signature)"
                
        # Risk levels
        risk_level = "Low"
        reasons = []
        
        # Risk factors
        dangerous_ops = ["android.permission.CAMERA", "android.permission.RECORD_AUDIO", "android.permission.ACCESS_FINE_LOCATION", "android.permission.READ_SMS", "android.permission.RECEIVE_SMS", "android.permission.SEND_SMS", "android.permission.READ_CONTACTS", "android.permission.READ_CALL_LOG"]
        dangerous_granted = [p for p in granted_permissions if p in dangerous_ops]
        
        if pkg in acc_services:
            risk_level = "Critical"
            reasons.append("Active Accessibility Service enabled")
        elif pkg in active_admins:
            risk_level = "High"
            reasons.append("Active Device Administrator enabled")
        elif pkg in overlay_apps and installer is None:
            risk_level = "High"
            reasons.append("Sideloaded app with active System Overlay permission")
        elif installer is None and len(dangerous_granted) >= 2:
            risk_level = "High"
            reasons.append(f"Sideloaded app with {len(dangerous_granted)} dangerous permissions granted")
        elif len(dangerous_granted) >= 4:
            risk_level = "Medium"
            reasons.append(f"Store app with excessive dangerous permissions ({len(dangerous_granted)})")
        elif installer is None:
            risk_level = "Medium"
            reasons.append("Sideloaded app")
            
        app_audit.append({
            "package": pkg,
            "installer": installer or "Sideloaded/None",
            "version": version,
            "install_time": install_time,
            "update_time": update_time,
            "last_used": last_used,
            "signature": signature,
            "granted_permissions": granted_permissions,
            "requested_permissions": requested_permissions,
            "accessibility_enabled": pkg in acc_services,
            "device_admin_enabled": pkg in active_admins,
            "overlay_enabled": pkg in overlay_apps,
            "foreground_service_active": has_fg_service,
            "background_activity_allowed": bg_activity,
            "supply_chain_risk": supply_chain,
            "risk_level": risk_level,
            "reasons": reasons
        })
        
    # Write output
    out_path = "/Users/anurag/.gemini/antigravity-cli/brain/bc82fa9b-634b-46ba-a680-2a8df369263b/phase3_diagnostics.json"
    with open(out_path, "w") as f:
        json.dump(app_audit, f, indent=4)
        
    print(f"Phase 3 scan complete. Audited {len(app_audit)} apps. Report saved to {out_path}")

if __name__ == "__main__":
    main()
