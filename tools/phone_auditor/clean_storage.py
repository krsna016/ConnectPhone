import subprocess
import json
import os
import sys

def run_adb_shell(shell_cmd):
    try:
        res = subprocess.run(["adb", "shell", shell_cmd], capture_output=True, text=True, timeout=10)
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except Exception as e:
        return -1, "", str(e)

def main():
    report_path = "/Users/anurag/.gemini/antigravity-cli/brain/bc82fa9b-634b-46ba-a680-2a8df369263b/storage_check.json"
    if not os.path.exists(report_path):
        print("Storage check report not found. Run check_cache.py first.")
        sys.exit(1)
        
    with open(report_path, "r") as f:
        data = json.load(f)
        
    print("🧹 Starting storage cleanup on connected phone...")
    
    # 1. Clear Trashed Files
    trashed_files = data.get("trashed_files", [])
    print(f"Deleting {len(trashed_files)} trashed files...")
    for f in trashed_files:
        path = f.get("path")
        if path:
            code, _, err = run_adb_shell(f"rm -f '{path}'")
            if code != 0:
                print(f"Warning: Failed to delete trashed file '{path}': {err}")
                
    # 2. Clear App Caches
    cache_dirs = data.get("cache_directories", [])
    print(f"Clearing {len(cache_dirs)} app cache directories...")
    for c in cache_dirs:
        path = c.get("path")
        if path:
            # We delete the contents of the cache directory to keep the directory itself,
            # or just delete the directory. Deleting the directory is cleaner.
            code, _, err = run_adb_shell(f"rm -rf '{path}'")
            if code != 0:
                print(f"Warning: Failed to delete cache dir '{path}': {err}")
                
    # 3. Clear WhatsApp Databases
    wa_dbs = data.get("whatsapp_databases", [])
    print(f"Deleting {len(wa_dbs)} WhatsApp database backups...")
    for db in wa_dbs:
        path = db.get("path")
        if path:
            code, _, err = run_adb_shell(f"rm -f '{path}'")
            if code != 0:
                print(f"Warning: Failed to delete WhatsApp database '{path}': {err}")
                
    # 4. Clear WhatsApp Temp/Cache folders
    wa_temps = data.get("whatsapp_temp_folders", [])
    print(f"Clearing {len(wa_temps)} WhatsApp temp/cache folders...")
    for t in wa_temps:
        path = t.get("path")
        if path:
            code, _, err = run_adb_shell(f"rm -rf '{path}'")
            if code != 0:
                print(f"Warning: Failed to delete WhatsApp temp folder '{path}': {err}")
                
    # 5. Clear WhatsApp Media folders
    wa_media = data.get("whatsapp_media_folders", [])
    print(f"Clearing contents of {len(wa_media)} WhatsApp media subfolders...")
    for m in wa_media:
        path = m.get("path")
        if path:
            # Delete directory and recreate it empty
            code, _, err = run_adb_shell(f"rm -rf '{path}'")
            if code == 0:
                run_adb_shell(f"mkdir -p '{path}'")
            else:
                print(f"Warning: Failed to clear WhatsApp media folder '{path}': {err}")
                
    print("\n✅ Storage cleanup completed successfully!")

if __name__ == "__main__":
    main()
