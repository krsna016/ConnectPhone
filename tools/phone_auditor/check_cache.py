import subprocess
import re
import json
import os
import sys

def run_adb_shell(shell_cmd):
    try:
        res = subprocess.run(["adb", "shell", shell_cmd], capture_output=True, text=True, timeout=30)
        return res.stdout.strip(), res.stderr.strip(), res.returncode
    except Exception as e:
        return "", str(e), -1

def get_dir_size(path):
    out, _, _ = run_adb_shell(f"du -sh '{path}' 2>/dev/null")
    if out:
        return out.split()[0]
    return "0B"

def main():
    print("Checking phone for cache, temporary files, and social media media...")
    
    data = {
        "trashed_files": [],
        "cache_directories": [],
        "whatsapp_databases": [],
        "whatsapp_temp_folders": [],
        "whatsapp_media_folders": [],
        "large_media_folders": []
    }
    
    # 1. Check for .trashed-* files
    print("Searching for trashed files...")
    out, _, _ = run_adb_shell("find /storage/emulated/0 -type f -name '.trashed-*' 2>/dev/null")
    for line in out.splitlines():
        file_path = line.strip()
        if file_path:
            size_out, _, _ = run_adb_shell(f"du -sk '{file_path}' 2>/dev/null")
            if size_out:
                parts = size_out.split()
                if len(parts) >= 1:
                    try:
                        size_kb = int(parts[0])
                        data["trashed_files"].append({
                            "path": file_path,
                            "size_kb": size_kb,
                            "size_str": f"{size_kb} KB" if size_kb < 1024 else f"{size_kb/1024:.2f} MB"
                        })
                    except ValueError:
                        pass
                
    # 2. Check for cache directories
    print("Searching for app cache directories...")
    out, _, _ = run_adb_shell("find /storage/emulated/0/Android/data -type d -name cache -o -name code_cache 2>/dev/null")
    for line in out.splitlines():
        dir_path = line.strip()
        if dir_path:
            # Get size of this directory
            size_str = get_dir_size(dir_path)
            if size_str != "0B" and size_str != "4.0K" and size_str != "8.0K" and size_str != "12K":
                data["cache_directories"].append({
                    "path": dir_path,
                    "size": size_str
                })
                
    # 3. WhatsApp Databases
    print("Checking WhatsApp database backups...")
    db_path = "/sdcard/Android/media/com.whatsapp/WhatsApp/Databases"
    out, _, _ = run_adb_shell(f"ls -lh '{db_path}' 2>/dev/null")
    for line in out.splitlines():
        if "msgstore" in line or "db.crypt" in line:
            parts = line.split()
            if len(parts) >= 8:
                size = parts[4]
                date = f"{parts[5]} {parts[6]} {parts[7]}"
                name = parts[-1]
                data["whatsapp_databases"].append({
                    "name": name,
                    "size": size,
                    "date": date,
                    "path": os.path.join(db_path, name)
                })
                
    # 4. WhatsApp Temp/Cache folders
    print("Checking WhatsApp temporary/cache media...")
    wa_media_root = "/sdcard/Android/media/com.whatsapp/WhatsApp/Media"
    temp_folders = [".Statuses", ".Links", ".wamocache", ".udDHFY8K4Eqg"]
    for tf in temp_folders:
        path = os.path.join(wa_media_root, tf)
        size = get_dir_size(path)
        if size != "0B" and size != "4.0K":
            data["whatsapp_temp_folders"].append({
                "folder": tf,
                "size": size,
                "path": path
            })
            
    # 5. WhatsApp Media folders
    print("Checking WhatsApp media subfolders...")
    out, _, _ = run_adb_shell(f"ls -1 '{wa_media_root}' 2>/dev/null")
    for line in out.splitlines():
        name = line.strip()
        if name and not name.startswith("."):
            path = os.path.join(wa_media_root, name)
            size = get_dir_size(path)
            data["whatsapp_media_folders"].append({
                "folder": name,
                "size": size,
                "path": path
            })
                
    # 6. Large media/user directories
    print("Checking other large storage directories...")
    user_dirs = ["Download", "DCIM", "Pictures", "Movies", "Music", "Documents"]
    for ud in user_dirs:
        path = os.path.join("/sdcard", ud)
        size = get_dir_size(path)
        data["large_media_folders"].append({
            "folder": ud,
            "size": size,
            "path": path
        })
        
    # Output report
    report_path = "/Users/anurag/.gemini/antigravity-cli/brain/bc82fa9b-634b-46ba-a680-2a8df369263b/storage_check.json"
    with open(report_path, "w") as f:
        json.dump(data, f, indent=4)
        
    print(f"\nStorage check complete. Results saved to {report_path}")

if __name__ == "__main__":
    main()
