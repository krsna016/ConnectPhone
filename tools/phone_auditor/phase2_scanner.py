import subprocess
import json
import os
import sys

def run_adb_shell(cmd):
    try:
        res = subprocess.run(["adb", "shell", cmd], capture_output=True, text=True, timeout=30)
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except Exception as e:
        return -1, "", str(e)

def get_dir_size(path):
    code, out, _ = run_adb_shell(f"du -sh '{path}' 2>/dev/null")
    if code == 0 and out:
        return out.split()[0]
    return "0B"

def main():
    print("Initializing Phase 2 Deep Storage Scanner...")
    
    report = {
        "user_folders": {},
        "large_files": [],
        "archive_files": [],
        "apk_files": [],
        "hidden_files": [],
        "log_files": [],
        "cache_directories": [],
        "social_media_folders": {},
        "offline_maps": "0B",
        "recycle_bin": [],
        "obb_size": "0B",
        "thumbnails": []
    }
    
    # 1. Inspect User Folders
    user_folders = ["Download", "DCIM", "Pictures", "Movies", "Music", "Documents"]
    for folder in user_folders:
        path = f"/storage/emulated/0/{folder}"
        report["user_folders"][folder] = get_dir_size(path)
        
    # 2. Large Files (>20MB)
    print("Scanning for large files...")
    code, out, _ = run_adb_shell("find /storage/emulated/0 -type f -size +20M 2>/dev/null")
    for line in out.splitlines():
        file_path = line.strip()
        if file_path:
            size_code, size_out, _ = run_adb_shell(f"du -sh '{file_path}' 2>/dev/null")
            if size_code == 0 and size_out:
                report["large_files"].append({
                    "path": file_path,
                    "size": size_out.split()[0]
                })
                
    # 3. Archive files (zip, rar, tar, gz, iso)
    print("Scanning for archive files...")
    code, out, _ = run_adb_shell("find /storage/emulated/0 -type f \\( -name '*.zip' -o -name '*.rar' -o -name '*.tar' -o -name '*.gz' -o -name '*.iso' \\) 2>/dev/null")
    for line in out.splitlines():
        file_path = line.strip()
        if file_path:
            size_code, size_out, _ = run_adb_shell(f"du -sh '{file_path}' 2>/dev/null")
            if size_code == 0 and size_out:
                report["archive_files"].append({
                    "path": file_path,
                    "size": size_out.split()[0]
                })
                
    # 4. APK files
    print("Scanning for APK files...")
    code, out, _ = run_adb_shell("find /storage/emulated/0 -type f -name '*.apk' 2>/dev/null")
    for line in out.splitlines():
        file_path = line.strip()
        if file_path:
            size_code, size_out, _ = run_adb_shell(f"du -sh '{file_path}' 2>/dev/null")
            if size_code == 0 and size_out:
                report["apk_files"].append({
                    "path": file_path,
                    "size": size_out.split()[0]
                })
                
    # 5. Hidden files starting with a dot (excluding Android folder system files)
    print("Scanning for hidden files...")
    code, out, _ = run_adb_shell("find /storage/emulated/0 -maxdepth 2 -type f -name '.*' 2>/dev/null")
    for line in out.splitlines():
        file_path = line.strip()
        if file_path:
            size_code, size_out, _ = run_adb_shell(f"du -sh '{file_path}' 2>/dev/null")
            if size_code == 0 and size_out:
                report["hidden_files"].append({
                    "path": file_path,
                    "size": size_out.split()[0]
                })
                
    # 6. Log Files (*.log)
    print("Scanning for log files...")
    code, out, _ = run_adb_shell("find /storage/emulated/0 -type f -name '*.log' 2>/dev/null")
    for line in out.splitlines():
        file_path = line.strip()
        if file_path:
            size_code, size_out, _ = run_adb_shell(f"du -sh '{file_path}' 2>/dev/null")
            if size_code == 0 and size_out:
                report["log_files"].append({
                    "path": file_path,
                    "size": size_out.split()[0]
                })
                
    # 7. Cache directories in Android/data
    print("Scanning for cache directories...")
    code, out, _ = run_adb_shell("find /storage/emulated/0/Android/data -type d -name cache -o -name code_cache 2>/dev/null")
    for line in out.splitlines():
        dir_path = line.strip()
        if dir_path:
            size = get_dir_size(dir_path)
            if size != "0B" and size != "4.0K" and size != "8.0K" and size != "12K":
                report["cache_directories"].append({
                    "path": dir_path,
                    "size": size
                })
                
    # 8. Social Media Folders (WhatsApp, Telegram, etc.)
    print("Checking social media directories...")
    social_apps = {
        "WhatsApp": "/storage/emulated/0/Android/media/com.whatsapp",
        "Telegram": "/storage/emulated/0/Telegram",
        "Telegram_Media": "/storage/emulated/0/Android/media/org.telegram.messenger",
        "Discord": "/storage/emulated/0/Android/data/com.discord",
        "Instagram": "/storage/emulated/0/Android/data/com.instagram.android",
        "Facebook": "/storage/emulated/0/Android/data/com.facebook.katana",
        "TikTok": "/storage/emulated/0/Android/data/com.zhiliaoapp.musically"
    }
    for app_name, path in social_apps.items():
        size = get_dir_size(path)
        if size != "0B":
            report["social_media_folders"][app_name] = size
            
    # 9. Offline Maps (Google Maps Cache)
    report["offline_maps"] = get_dir_size("/storage/emulated/0/Android/data/com.google.android.apps.maps")
    
    # 10. Recycle Bin (.trashed-* files)
    print("Scanning for recycle bin trashed files...")
    code, out, _ = run_adb_shell("find /storage/emulated/0 -type f -name '.trashed-*' 2>/dev/null")
    for line in out.splitlines():
        file_path = line.strip()
        if file_path:
            size_code, size_out, _ = run_adb_shell(f"du -sh '{file_path}' 2>/dev/null")
            if size_code == 0 and size_out:
                report["recycle_bin"].append({
                    "path": file_path,
                    "size": size_out.split()[0]
                })
                
    # 11. OBB files (unused game asset expansion)
    report["obb_size"] = get_dir_size("/storage/emulated/0/Android/obb")
    
    # 12. Thumbnail cache folders
    print("Scanning for thumbnail cache directories...")
    code, out, _ = run_adb_shell("find /storage/emulated/0 -type d -name '.thumbnails' 2>/dev/null")
    for line in out.splitlines():
        dir_path = line.strip()
        if dir_path:
            size = get_dir_size(dir_path)
            if size != "0B":
                report["thumbnails"].append({
                    "path": dir_path,
                    "size": size
                })
                
    # Save output
    out_path = "/Users/anurag/.gemini/antigravity-cli/brain/bc82fa9b-634b-46ba-a680-2a8df369263b/phase2_diagnostics.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=4)
        
    print(f"Phase 2 scan complete. Report saved to {out_path}")

if __name__ == "__main__":
    main()
