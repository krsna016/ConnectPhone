import subprocess
import json
import re
import os

def run_adb_shell(cmd):
    try:
        res = subprocess.run(["adb", "shell", cmd], capture_output=True, text=True, timeout=60)
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except Exception as e:
        return -1, "", str(e)

def main():
    print("Initiating strict A-to-Z shared storage files audit...")
    
    # Get all files and folders in /storage/emulated/0
    code, out, _ = run_adb_shell("find /storage/emulated/0 -maxdepth 1")
    if code != 0 or not out:
        print("Failed to list storage items.")
        sys.exit(1)
        
    items = []
    for line in out.splitlines():
        path = line.strip()
        if path == "/storage/emulated/0" or not path:
            continue
            
        # Get size
        size_code, size_out, _ = run_adb_shell(f"du -sh '{path}' 2>/dev/null")
        size = "0B"
        if size_code == 0 and size_out:
            size = size_out.split()[0].strip()
            
        name = os.path.basename(path)
        is_dir = True
        
        # Check if file or directory
        check_code, check_out, _ = run_adb_shell(f"if [ -d '{path}' ]; then echo 'd'; else echo 'f'; fi")
        if check_out.strip() == "f":
            is_dir = False
            
        items.append({
            "name": name,
            "path": path,
            "size": size,
            "type": "Directory" if is_dir else "File"
        })
        
    # Sort alphabetically by name (A-Z case insensitive)
    items.sort(key=lambda x: x["name"].lower())
    
    # Save JSON output
    out_path = "/Users/anurag/.gemini/antigravity-cli/brain/bc82fa9b-634b-46ba-a680-2a8df369263b/atoz_storage_audit.json"
    with open(out_path, "w") as f:
        json.dump(items, f, indent=4)
        
    print(f"A-to-Z audit complete. Report saved to {out_path}")

if __name__ == "__main__":
    main()
