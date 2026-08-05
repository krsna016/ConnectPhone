import subprocess
import json
import sys

def run_adb_shell(cmd):
    try:
        res = subprocess.run(["adb", "shell", cmd], capture_output=True, text=True, timeout=120)
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except Exception as e:
        return -1, "", str(e)

def main():
    print("Initiating Full Speed Compilation Sequence (Maximum Intensity)...")
    
    target_apps = [
        "com.android.chrome",
        "com.whatsapp",
        "app.revanced.android.youtube",
        "com.example.mobile"
    ]
    
    report = {
        "compilation_mode": "full-speed",
        "compiled_apps": []
    }
    
    for app in target_apps:
        # Check if installed
        _, out_check, _ = run_adb_shell(f"pm list packages {app}")
        if out_check:
            print(f"Executing full speed compilation on {app}...")
            # -f forces compilation even if already compiled
            # -m speed compiles everything to native machine code
            code_compile, _, _ = run_adb_shell(f"cmd package compile -f -m speed {app}")
            if code_compile == 0:
                report["compiled_apps"].append(app)
                
    out_path = "/Users/anurag/.gemini/antigravity-cli/brain/bc82fa9b-634b-46ba-a680-2a8df369263b/full_speed_compile_report.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=4)
        
    print(f"Full Speed Compilation complete. Saved report to {out_path}")

if __name__ == "__main__":
    main()
