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
    print("Starting Phase 11: Cloud Audit...")
    
    report = {
        "master_sync_automatically": "Unknown",
        "registered_accounts": [],
        "sync_adapters_active": []
    }
    
    # 1. Check Master Sync Switch
    _, sync_dump, _ = run_adb_shell("dumpsys content")
    master_match = re.search(r"Auto sync:\s*u0=(\w+)", sync_dump)
    if master_match:
        report["master_sync_automatically"] = "Enabled" if master_match.group(1).lower() == "true" else "Disabled"
        
    # 2. Get registered accounts
    print("Checking registered user accounts...")
    _, account_dump, _ = run_adb_shell("dumpsys account")
    account_section = False
    for line in account_dump.splitlines():
        if "Accounts:" in line:
            account_section = True
            continue
        elif account_section and line.startswith("  "):
            # e.g., Account {name=username@gmail.com, type=com.google}
            match = re.search(r"Account\s+\{name=([^,]+),\s*type=([^\}]+)\}", line)
            if match:
                # Mask username
                name = match.group(1)
                ac_type = match.group(2).strip()
                masked_name = name.split("@")[0][:3] + "***" + ("@" + name.split("@")[1] if "@" in name else "")
                report["registered_accounts"].append({
                    "account_type": ac_type,
                    "account_name_masked": masked_name
                })
        elif account_section and not line.startswith("  ") and line.strip():
            account_section = False
            
    # 3. Active Sync Adapters & Status
    provider_section = False
    for line in sync_dump.splitlines():
        if "Periodic Syncs:" in line:
            provider_section = True
            continue
        if provider_section:
            if "JobId=" in line:
                # JobId=137 016.krsna@gmail.com/app.revanced u0 [com.android.contacts] ...
                match = re.search(r"JobId=\d+\s+([^\s]+)\s+u\d+\s+\[([^\]]+)\]", line)
                if match:
                    ac_name = match.group(1)
                    authority = match.group(2)
                    masked_ac = ac_name.split("@")[0][:3] + "***" + ("@" + ac_name.split("@")[1] if "@" in ac_name else "")
                    report["sync_adapters_active"].append(f"{masked_ac} -> {authority}")
            elif ":" in line and "JobId=" not in line and line.strip():
                provider_section = False
            
    # Save JSON output
    out_path = "/Users/anurag/.gemini/antigravity-cli/brain/bc82fa9b-634b-46ba-a680-2a8df369263b/phase11_diagnostics.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=4)
        
    print(f"Phase 11 scan complete. Report saved to {out_path}")

if __name__ == "__main__":
    main()
