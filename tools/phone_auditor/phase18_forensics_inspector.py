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
    print("Starting Phase 18: Malware & Forensics Audit...")
    
    report = {
        "sideloaded_forensics": []
    }
    
    # 1. Get all third-party packages and check installers
    code_pkg, pkg_out, _ = run_adb_shell("pm list packages -3 -i")
    if code_pkg != 0 or not pkg_out:
        print("Failed to list packages.")
        sys.exit(1)
        
    sideloaded_pkgs = []
    for line in pkg_out.splitlines():
        if "installer=null" in line.lower() or "installer=sideloaded" in line.lower() or "installer=" not in line:
            match = re.search(r"package:([^\s]+)", line)
            if match:
                sideloaded_pkgs.append(match.group(1))
                
    print(f"Found {len(sideloaded_pkgs)} sideloaded packages. Inspecting hashes and paths...")
    for pkg in sideloaded_pkgs:
        # Get path to base.apk
        _, path_out, _ = run_adb_shell(f"pm path {pkg}")
        # format: package:/data/app/~~.../base.apk
        apk_path = path_out.replace("package:", "").strip() if path_out else None
        
        sha256 = "Unknown"
        if apk_path:
            # Get sha256 hash of the apk directly on the phone
            code_sha, sha_out, _ = run_adb_shell(f"sha256sum {apk_path} 2>/dev/null")
            if code_sha == 0 and sha_out:
                sha256 = sha_out.split()[0].strip()
                
        # Dumpsys package signatures
        _, dumpsys_out, _ = run_adb_shell(f"dumpsys package {pkg}")
        sig_match = re.search(r"signatures=\[\s*([a-fA-F0-9,\s]+)\s*\]", dumpsys_out)
        if not sig_match:
            sig_match = re.search(r"signatures=\[Signature\{([a-fA-F0-9]+)\s*\}\s*\]", dumpsys_out)
        signature = sig_match.group(1).strip().replace("\n", "").replace(" ", "")[:40] + "..." if sig_match else "Official Store/OEM Signed"
        
        # Check permissions granted
        camera_granted = "android.permission.CAMERA: granted=true" in dumpsys_out
        location_granted = "android.permission.ACCESS_FINE_LOCATION: granted=true" in dumpsys_out
        microphone_granted = "android.permission.RECORD_AUDIO: granted=true" in dumpsys_out
        
        report["sideloaded_forensics"].append({
            "package": pkg,
            "apk_path_on_device": apk_path,
            "apk_sha256_hash": sha256,
            "signature_hash_prefix": signature,
            "permissions_active": {
                "camera": camera_granted,
                "location": location_granted,
                "microphone": microphone_granted
            }
        })
        
    # Save JSON output
    out_path = "/Users/anurag/.gemini/antigravity-cli/brain/bc82fa9b-634b-46ba-a680-2a8df369263b/phase18_diagnostics.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=4)
        
    print(f"Phase 18 scan complete. Report saved to {out_path}")

if __name__ == "__main__":
    main()
