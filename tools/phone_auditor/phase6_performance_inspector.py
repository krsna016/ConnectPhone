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
    print("Starting Phase 6: Performance Engineering Audit...")
    
    report = {
        "cpu_usage_top": [],
        "zram_swap_info": {},
        "animation_scales": {},
        "cached_processes_limit": "Default",
        "gpu_rendering_stats": {}
    }
    
    # 1. CPU usage top processes
    print("Checking top CPU-consuming processes...")
    _, top_out, _ = run_adb_shell("top -b -n 1 -m 5")
    # Parse top lines
    lines = top_out.splitlines()
    header_found = False
    for line in lines:
        if "PID" in line and "USER" in line and "CPU" in line:
            header_found = True
            continue
        if header_found and line.strip():
            parts = line.split()
            if len(parts) >= 9:
                pid = parts[0]
                user = parts[1]
                cpu = parts[8]
                name = parts[-1]
                report["cpu_usage_top"].append({
                    "pid": pid,
                    "user": user,
                    "cpu_percent": f"{cpu}%",
                    "process_name": name
                })
                
    # 2. zRAM and Swap Info
    print("Checking memory swaps...")
    _, swap_out, _ = run_adb_shell("cat /proc/swaps")
    # format:
    # Filename                Type            Size            Used            Priority
    # /dev/block/zram0        partition       3145724         1024            -2
    lines = swap_out.splitlines()
    if len(lines) > 1:
        parts = lines[1].split()
        if len(parts) >= 4:
            report["zram_swap_info"] = {
                "filename": parts[0],
                "type": parts[1],
                "size_kb": parts[2],
                "used_kb": parts[3]
            }
    else:
        report["zram_swap_info"] = "No zRAM/Swap active or inaccessible"
        
    # 3. Animation Scales
    print("Checking animation scales...")
    _, win_scale, _ = run_adb_shell("settings get global window_animation_scale")
    _, trans_scale, _ = run_adb_shell("settings get global transition_animation_scale")
    _, anim_scale, _ = run_adb_shell("settings get global animator_duration_scale")
    
    report["animation_scales"] = {
        "window_animation_scale": win_scale if win_scale else "1.0",
        "transition_animation_scale": trans_scale if trans_scale else "1.0",
        "animator_duration_scale": anim_scale if anim_scale else "1.0"
    }
    
    # 4. Cached process limits
    _, limit_out, _ = run_adb_shell("settings get global max_cached_processes")
    if limit_out and limit_out != "null":
        report["cached_processes_limit"] = limit_out
        
    # 5. GFX rendering stats for system ui
    print("Checking GPU rendering profile...")
    _, gfx_out, _ = run_adb_shell("dumpsys gfxinfo com.android.systemui")
    gfx_lines = gfx_out.splitlines()
    for i, line in enumerate(gfx_lines):
        if "Stats since" in line:
            report["gpu_rendering_stats"]["collection_period"] = line.strip()
        if "Total frames rendered" in line:
            report["gpu_rendering_stats"]["total_frames"] = line.split(":")[-1].strip()
        if "Janky frames" in line:
            report["gpu_rendering_stats"]["janky_frames"] = line.split(":")[-1].strip()
            
    # Save JSON output
    out_path = "/Users/anurag/.gemini/antigravity-cli/brain/bc82fa9b-634b-46ba-a680-2a8df369263b/phase6_diagnostics.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=4)
        
    print(f"Phase 6 scan complete. Report saved to {out_path}")

if __name__ == "__main__":
    main()
