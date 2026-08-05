import subprocess
import json
import re

def run_adb_shell(cmd):
    try:
        res = subprocess.run(["adb", "shell", cmd], capture_output=True, text=True, timeout=60)
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except Exception as e:
        return -1, "", str(e)

def get_dir_size(path):
    code, out, _ = run_adb_shell(f"du -sh {path} 2>/dev/null")
    if code == 0 and out:
        return out.split()[0].strip()
    return "0B"

def list_large_files(path, min_size_mb=10):
    # Find files larger than min_size_mb
    code, out, _ = run_adb_shell(f"find {path} -type f -size +{min_size_mb}M -exec ls -lh {{}} \\; 2>/dev/null")
    files = []
    if code == 0 and out:
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 9:
                size = parts[4]
                filepath = " ".join(parts[8:])
                files.append({"path": filepath, "size": size})
    return files

def main():
    print("Starting deep storage file and cache scanner...")
    
    report = {
        "whatsapp_storage": {},
        "downloads_folder": {},
        "large_media_files": [],
        "system_logs_and_caches": []
    }
    
    # 1. Check WhatsApp Media & Databases
    wa_base_path = "/storage/emulated/0/Android/media/com.whatsapp/WhatsApp"
    print("Scanning WhatsApp databases and media storage...")
    report["whatsapp_storage"] = {
        "total_whatsapp_size": get_dir_size(wa_base_path),
        "databases": get_dir_size(f"{wa_base_path}/Databases"),
        "media_images": get_dir_size(f"{wa_base_path}/Media/WhatsApp Images"),
        "media_video": get_dir_size(f"{wa_base_path}/Media/WhatsApp Video"),
        "media_voice_notes": get_dir_size(f"{wa_base_path}/Media/WhatsApp Voice Notes"),
        "media_documents": get_dir_size(f"{wa_base_path}/Media/WhatsApp Documents")
    }
    
    # 2. Check Downloads folder
    print("Scanning Downloads folder...")
    report["downloads_folder"] = {
        "total_downloads_size": get_dir_size("/storage/emulated/0/Download")
    }
    
    # 3. Scan for large media/zip/apk files (> 15MB) in shared storage
    print("Searching for files larger than 15MB...")
    report["large_media_files"] = list_large_files("/storage/emulated/0", 15)
    
    # 4. Scan log and cache locations
    log_locations = [
        "/storage/emulated/0/Android/data/com.miui.gallery/files",
        "/storage/emulated/0/MIUI/debug_log",
        "/storage/emulated/0/Android/data/com.android.providers.media/cache"
    ]
    
    print("Scanning common log and cache locations...")
    for loc in log_locations:
        size = get_dir_size(loc)
        if size != "0B" and size != "":
            report["system_logs_and_caches"].append({
                "path": loc,
                "size": size
            })
            
    out_path = "/Users/anurag/.gemini/antigravity-cli/brain/bc82fa9b-634b-46ba-a680-2a8df369263b/deep_storage_scan.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=4)
        
    print(f"Deep scan complete. Saved results to {out_path}")

if __name__ == "__main__":
    main()
