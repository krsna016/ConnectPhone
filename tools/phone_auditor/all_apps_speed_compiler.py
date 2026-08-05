import subprocess
import json
import re
import sys

def run_adb_shell(cmd):
    try:
        res = subprocess.run(["adb", "shell", cmd], capture_output=True, text=True, timeout=180)
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except Exception as e:
        return -1, "", str(e)

def main():
    print("Initiating Global Native Speed Compilation (Maximum Intensity)...")
    
    # Disable secure diagnostic logging properties
    print("Disabling Google and MIUI diagnostic upload logs...")
    run_adb_shell("settings put secure upload_log 0")
    run_adb_shell("settings put secure send_action_logs 0")
    run_adb_shell("settings put secure diagnostics_data_enabled 0")
    
    # Get all 3rd party apps
    _, pkgs_out, _ = run_adb_shell("pm list packages -3")
    third_party_pkgs = [line.replace("package:", "").strip() for line in pkgs_out.splitlines() if line.strip()]
    
    report = {
        "diagnostic_logs_disabled": True,
        "compiled_packages": []
    }
    
    print(f"Compiling {len(third_party_pkgs)} third-party apps to native machine code...")
    for app in third_party_pkgs:
        print(f"Optimizing {app}...")
        # Compiles the app with 'speed' filter (fully native compilation)
        code_compile, _, _ = run_adb_shell(f"cmd package compile -f -m speed {app}")
        if code_compile == 0:
            report["compiled_packages"].append(app)
            
    out_path = "/Users/anurag/.gemini/antigravity-cli/brain/bc82fa9b-634b-46ba-a680-2a8df369263b/global_speed_compile_report.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=4)
        
    print(f"Global speed compilation complete. Report saved to {out_path}")

if __name__ == "__main__":
    main()
