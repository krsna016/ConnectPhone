import subprocess
import sys

def run_adb_shell(shell_cmd):
    try:
        res = subprocess.run(["adb", "shell", shell_cmd], capture_output=True, text=True, timeout=15)
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except Exception as e:
        return -1, "", str(e)

def main():
    print("🧹 Cleaning additional app caches, wallpapers, and logs...")
    
    paths_to_delete = [
        # 1. MIUI Gallery Disk Cache (delete contents)
        "/storage/emulated/0/Android/data/com.miui.gallery/files/gallery_disk_cache",
        # 2. Theme Manager Wallpaper History
        "/storage/emulated/0/Android/data/com.android.thememanager/files/MIUI/.wallpaper_history",
        # 3. Theme Manager Temp
        "/storage/emulated/0/Android/data/com.android.thememanager/files/MIUI/.tmp",
        # 4. Sound Recorder Debug Log
        "/storage/emulated/0/Android/data/com.android.soundrecorder/files/debug_log/com.android.soundrecorder.log",
        # 5. Sound Recorder Trash
        "/storage/emulated/0/Android/data/com.android.soundrecorder/files/.trash"
    ]
    
    for path in paths_to_delete:
        print(f"Clearing: {path} ...")
        # For folders, we delete the directory and recreate it empty so that the app doesn't break
        if path.endswith(".log"):
            code, _, err = run_adb_shell(f"rm -f '{path}'")
        else:
            code, _, err = run_adb_shell(f"rm -rf '{path}'")
            if code == 0:
                run_adb_shell(f"mkdir -p '{path}'")
                
        if code != 0:
            print(f"Warning: Failed to clear '{path}': {err}")
            
    print("\n✅ Additional storage cleanup completed successfully!")

if __name__ == "__main__":
    main()
