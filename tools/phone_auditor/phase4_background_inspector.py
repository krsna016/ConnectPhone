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
    print("Starting Phase 4: Background Activity Audit...")
    
    report = {
        "running_services": [],
        "foreground_services": [],
        "wake_locks": [],
        "doze_whitelist": [],
        "pending_jobs": [],
        "sync_adapters": [],
        "persistent_apps": []
    }
    
    # 1. Running Services & Foreground Services
    print("Checking active services...")
    code, services_out, _ = run_adb_shell("dumpsys activity services")
    if code == 0:
        current_service = None
        for line in services_out.splitlines():
            # Match ServiceRecord
            if "* ServiceRecord{" in line:
                # e.g., * ServiceRecord{9c1d683 u0 com.whatsapp/.messaging.MessageService}
                match = re.search(r"ServiceRecord\{[^\s]+\s+[^\s]+\s+([^/]+)/([^ \}]+)", line)
                if match:
                    pkg = match.group(1)
                    srv = match.group(2)
                    report["running_services"].append(f"{pkg}/{srv}")
            if "isForeground=true" in line or "fgRequired=true" in line:
                if len(report["running_services"]) > 0:
                    report["foreground_services"].append(report["running_services"][-1])
                    
        report["running_services"] = list(set(report["running_services"]))
        report["foreground_services"] = list(set(report["foreground_services"]))
        
    # 2. Wake Locks
    print("Checking wake locks...")
    _, power_out, _ = run_adb_shell("dumpsys power")
    wl_section = False
    for line in power_out.splitlines():
        if "Wake Locks:" in line:
            wl_section = True
            continue
        elif wl_section and line.startswith("  "):
            # e.g., PARTIAL_WAKE_LOCK      'AudioMix' ACQ=-3s174ms (uid=1041 ws=null)
            line_strip = line.strip()
            if line_strip:
                report["wake_locks"].append(line_strip)
        elif wl_section and not line.startswith("  ") and line.strip():
            # Section ended
            wl_section = False
            
    # 3. Doze Exceptions / Battery optimization exceptions
    print("Checking doze exceptions...")
    _, whitelist_out, _ = run_adb_shell("dumpsys deviceidle whitelist")
    for line in whitelist_out.splitlines():
        # format: system-override,com.google.android.gms,10024
        # or user,com.whatsapp,10148
        parts = line.strip().split(",")
        if len(parts) >= 2:
            report["doze_whitelist"].append({
                "type": parts[0],
                "package": parts[1]
            })
            
    # 4. JobScheduler pending jobs
    print("Checking JobScheduler tasks...")
    _, jobs_out, _ = run_adb_shell("dumpsys jobscheduler")
    # Parse pending jobs for third-party apps
    for line in jobs_out.splitlines():
        if "JOB #" in line:
            # e.g., JOB #10148/1: com.whatsapp/.job.BackupJob
            match = re.search(r"JOB\s+#(\d+)/(\d+):\s+([^/]+)/", line)
            if match:
                pkg = match.group(3)
                report["pending_jobs"].append(pkg)
    report["pending_jobs"] = list(set(report["pending_jobs"]))
    
    # 5. Sync Adapters
    print("Checking sync adapters...")
    _, sync_out, _ = run_adb_shell("dumpsys sync")
    for line in sync_out.splitlines():
        # e.g., Authority: com.android.contacts
        if "Authority:" in line:
            parts = line.split(":")
            if len(parts) >= 2:
                report["sync_adapters"].append(parts[1].strip())
    report["sync_adapters"] = list(set(report["sync_adapters"]))
    
    # 6. Persistent processes
    print("Checking persistent applications...")
    _, activity_out, _ = run_adb_shell("dumpsys activity processes")
    for line in activity_out.splitlines():
        if "PERSISTENT" in line or "persistent=true" in line:
            # e.g., *ProcessRecord{56ab213 1002 com.android.phone/1001}
            match = re.search(r"ProcessRecord\{[^\s]+\s+\d+\s+([^/]+)/", line)
            if match:
                report["persistent_apps"].append(match.group(1))
    report["persistent_apps"] = list(set(report["persistent_apps"]))
    
    # Save JSON output
    out_path = "/Users/anurag/.gemini/antigravity-cli/brain/bc82fa9b-634b-46ba-a680-2a8df369263b/phase4_diagnostics.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=4)
        
    print(f"Phase 4 scan complete. Report saved to {out_path}")

if __name__ == "__main__":
    main()
