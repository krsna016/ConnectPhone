import http.server
import socketserver
import json
import os
import subprocess
import threading
import time
import datetime
import sys
import webbrowser

# Inject common macOS binary paths (crucial when run as a Dock app shortcut without zsh profiles loaded)
common_paths = [
    "/opt/homebrew/bin",
    "/usr/local/bin",
    os.path.expanduser("~/Library/Android/sdk/platform-tools"),
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin"
]
current_path = os.environ.get("PATH", "")
for path in common_paths:
    if path and path not in current_path.split(os.pathsep):
        current_path = path + os.pathsep + current_path
os.environ["PATH"] = current_path

# Add project dir to path to import ConnectPhone
PROJECT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.append(PROJECT_DIR)

# Try to raise the file descriptor limit (prevents "Too many open files" socket errors on macOS)
try:
    import resource
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft < 4096:
        resource.setrlimit(resource.RLIMIT_NOFILE, (min(4096, hard), hard))
except Exception:
    pass

import ConnectPhone

PORT = 8282

# Global state tracker
scrcpy_proc = None
scrcpy_state = {
    "session_start_time": 0.0,
    "orientation": "flip0",
    "recording_active": False,
    "clip_start_time": 0.0,
    "mac_audio_file": None,
    "audio_proc": None,
    "rec_file": None,
    "temp_mkv": None,
    "mirror_type": None
}

sync_watcher_thread = None
sync_watcher_active = False

# ─── Fast Status Cache ────────────────────────────────────────────────────────
# Background thread keeps this refreshed so /api/status returns instantly.
_status_cache = None
_status_cache_lock = threading.Lock()
_status_cache_event = threading.Event()   # set when a fresh refresh is wanted

def _build_status_payload():
    """Build the full /api/status payload. Called from background thread."""
    global scrcpy_proc, scrcpy_state, sync_watcher_active
    devices_detailed = get_detailed_adb_devices()
    active_device = check_and_autoselect_device(devices_detailed)
    device_connected = len(devices_detailed) > 0 and any(d["status"] == "device" for d in devices_detailed)

    # Run slow ADB calls in parallel
    device_info = None
    input_injection_granted = True
    if device_connected:
        results = {}
        def _get_info():
            results["info"] = ConnectPhone.get_device_info()
        def _get_perm():
            results["perm"] = ConnectPhone.check_input_injection_permission()
        t1 = threading.Thread(target=_get_info, daemon=True)
        t2 = threading.Thread(target=_get_perm, daemon=True)
        t1.start(); t2.start()
        t1.join(timeout=4); t2.join(timeout=4)
        device_info = results.get("info")
        input_injection_granted = results.get("perm", True)

    scrcpy_running = scrcpy_proc is not None and scrcpy_proc.poll() is None
    return {
        "connected": device_connected,
        "devices": [d["serial"] for d in devices_detailed],
        "devices_detailed": devices_detailed,
        "active_device": active_device,
        "device_info": device_info,
        "scrcpy_running": scrcpy_running,
        "recording_active": scrcpy_state["recording_active"],
        "sync_watcher_active": sync_watcher_active,
        "mirror_type": scrcpy_state["mirror_type"],
        "input_injection_granted": input_injection_granted,
        "config": ConnectPhone.load_config()
    }

def _status_cache_worker():
    """Background thread: refresh cache every 1.2 s or immediately on demand."""
    global _status_cache
    while True:
        try:
            payload = _build_status_payload()
            with _status_cache_lock:
                _status_cache = payload
        except Exception as e:
            print(f"[StatusCache] Error: {e}")
        # Wait up to 1.2 s, but wake immediately if signalled
        _status_cache_event.wait(timeout=1.2)
        _status_cache_event.clear()

def _invalidate_status_cache():
    """Signal the background thread to refresh immediately."""
    _status_cache_event.set()
# ─────────────────────────────────────────────────────────────────────────────



def scan_and_connect_wireless_debug(ip, timeout=0.12, last_known_port=None):
    import socket
    import concurrent.futures

    def check_single_port(ip_addr, port_num, timeout_val):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout_val)
            result = s.connect_ex((ip_addr, port_num))
            s.close()
            return result == 0
        except Exception:
            return False

    def try_connect(port):
        ip_port = f"{ip}:{port}"
        subprocess.run(["adb", "disconnect", ip_port], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        res = subprocess.run(["adb", "connect", ip_port], capture_output=True, text=True)
        out = res.stdout or ""
        return port if ("connected to" in out.lower() or "already connected to" in out.lower()) else None

    # 0. Try last-known port instantly (no scan needed if device kept same port)
    if last_known_port and last_known_port not in (5555,):
        if check_single_port(ip, last_known_port, 0.25):
            result = try_connect(last_known_port)
            if result:
                return result

    # 1. Check default port 5555
    if check_single_port(ip, 5555, 0.2):
        result = try_connect(5555)
        if result:
            return result

    # 2. Parallel scan of wireless-debug port range (30000-49999)
    ports = list(range(30000, 50000))
    found_ports = []
    lock = threading.Lock()
    stop_flag = threading.Event()

    def worker(port):
        if stop_flag.is_set():
            return
        if check_single_port(ip, port, timeout):
            with lock:
                found_ports.append(port)

    with concurrent.futures.ThreadPoolExecutor(max_workers=256) as executor:
        executor.map(worker, ports)

    found_ports.sort()

    # 3. Try each found port
    for port in found_ports:
        result = try_connect(port)
        if result:
            return result

    return None

class RobustAdbMdnsListener:
    def __init__(self, target_service_name=None, target_ip=None):
        self.target_service_name = target_service_name
        self.target_ip = target_ip
        self.ip_address = None
        self.port = None

    def remove_service(self, zeroconf, type, name):
        pass

    def update_service(self, zeroconf, type, name):
        pass

    def add_service(self, zeroconf, type, name):
        try:
            info = zeroconf.get_service_info(type, name)
            if info:
                addresses = info.parsed_addresses()
                if not addresses:
                    return
                # Filter/prioritize IPv4 addresses
                ipv4_addresses = [addr for addr in addresses if '.' in addr]
                resolved_ip = ipv4_addresses[0] if ipv4_addresses else addresses[0]
                
                # Check target service name substring constraint (case-insensitive)
                if self.target_service_name and self.target_service_name.lower() not in name.lower():
                    return
                    
                # Check target IP constraint
                if self.target_ip and not any(addr == self.target_ip for addr in addresses):
                    return
                    
                self.ip_address = resolved_ip
                self.port = info.port
        except Exception:
            pass

def resolve_hostname_dns_sd(hostname, timeout=2.0):
    import re
    import select
    cmd = ["dns-sd", "-G", "v4", hostname]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True)
        start = time.time()
        while time.time() - start < timeout:
            r, _, _ = select.select([proc.stdout], [], [], 0.1)
            if proc.stdout in r:
                line = proc.stdout.readline()
                if not line:
                    break
                line_str = line.strip()
                if "Add" in line_str:
                    parts = line_str.split()
                    if len(parts) >= 6:
                        ip = parts[5]
                        if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", ip):
                            proc.terminate()
                            return ip
            time.sleep(0.05)
        proc.terminate()
    except Exception:
        pass
    return None

def resolve_instance_dns_sd(instance_name, service_type, timeout=2.0):
    import select
    cmd = ["dns-sd", "-L", instance_name, service_type, "local."]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True)
        start = time.time()
        while time.time() - start < timeout:
            r, _, _ = select.select([proc.stdout], [], [], 0.1)
            if proc.stdout in r:
                line = proc.stdout.readline()
                if not line:
                    break
                line_str = line.strip()
                if "can be reached at" in line_str:
                    reached_part = line_str.split("can be reached at")[1].strip()
                    host_port = reached_part.split()[0]
                    if ":" in host_port:
                        host, port_str = host_port.rsplit(":", 1)
                        port = int(port_str)
                        proc.terminate()
                        return host, port
            time.sleep(0.05)
        proc.terminate()
    except Exception:
        pass
    return None, None

def browse_dns_sd_loop(service_type, target_substring, target_ip, result_dict, stop_event, timeout):
    import socket
    import select
    
    # Strip .local. or .local from service type for dns-sd command
    dns_sd_service = service_type
    if dns_sd_service.endswith(".local."):
        dns_sd_service = dns_sd_service[:-7]
    elif dns_sd_service.endswith(".local"):
        dns_sd_service = dns_sd_service[:-6]
        
    cmd = ["dns-sd", "-B", dns_sd_service]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True)
        start_time = time.time()
        while time.time() - start_time < timeout and not stop_event.is_set():
            r, _, _ = select.select([proc.stdout], [], [], 0.1)
            if stop_event.is_set():
                break
            if proc.stdout in r:
                line = proc.stdout.readline()
                if not line:
                    break
                line_str = line.strip()
                if "Add" in line_str:
                    parts = line_str.split()
                    if len(parts) >= 7:
                        instance_name = " ".join(parts[6:])
                        if target_substring is None or target_substring.lower() in instance_name.lower():
                            host, port = resolve_instance_dns_sd(instance_name, dns_sd_service)
                            if host and port:
                                ip = None
                                try:
                                    ip = socket.gethostbyname(host)
                                except Exception:
                                    ip = resolve_hostname_dns_sd(host)
                                
                                if ip:
                                    if target_ip is None or ip == target_ip:
                                        result_dict["ip"] = ip
                                        result_dict["port"] = port
                                        stop_event.set()
                                        proc.terminate()
                                        return
            time.sleep(0.05)
        proc.terminate()
    except Exception:
        pass

def discover_adb_service_hybrid(service_type, target_substring=None, target_ip=None, timeout=30.0, is_cancelled_fn=None):
    from zeroconf import Zeroconf, ServiceBrowser
    
    result = {"ip": None, "port": None}
    stop_event = threading.Event()
    
    # 1. Start Zeroconf browser in background (ensure type ends with .local.)
    zc = None
    browser = None
    zc_type = service_type
    if not zc_type.endswith("."):
        zc_type += "."
    if not zc_type.endswith(".local."):
        if zc_type.endswith(".local"):
            zc_type += "."
        else:
            zc_type += "local."
            
    try:
        zc = Zeroconf()
        listener = RobustAdbMdnsListener(target_substring, target_ip)
        browser = ServiceBrowser(zc, zc_type, listener)
    except Exception:
        zc = None

    # 2. Start native dns-sd fallback in background thread
    dns_sd_thread = threading.Thread(
        target=browse_dns_sd_loop,
        args=(service_type, target_substring, target_ip, result, stop_event, timeout)
    )
    dns_sd_thread.daemon = True
    dns_sd_thread.start()
    
    # 3. Wait loop
    start_time = time.time()
    while time.time() - start_time < timeout:
        # Check for cancellation
        if is_cancelled_fn and is_cancelled_fn():
            stop_event.set()
            break
            
        # Check if Zeroconf listener got it
        if zc and listener.ip_address and listener.port:
            result["ip"] = listener.ip_address
            result["port"] = listener.port
            stop_event.set()
            break
            
        # Check if dns-sd got it
        if result["ip"] and result["port"]:
            break
            
        time.sleep(0.25)
        
    stop_event.set()
    if browser:
        try:
            browser.cancel()
        except Exception:
            pass
    if zc:
        try:
            zc.close()
        except Exception:
            pass
            
    return result["ip"], result["port"]



def stop_scrcpy_bg():
    global scrcpy_proc, scrcpy_state
    if scrcpy_proc:
        try:
            scrcpy_proc.terminate()
            scrcpy_proc.wait(timeout=2)
        except Exception:
            try:
                scrcpy_proc.kill()
            except Exception:
                pass
        scrcpy_proc = None
        
    if scrcpy_state["audio_proc"]:
        try:
            scrcpy_state["audio_proc"].terminate()
            scrcpy_state["audio_proc"].wait(timeout=2)
        except Exception:
            pass
        scrcpy_state["audio_proc"] = None
        
    # Clean up temp files
    for f in [scrcpy_state["temp_mkv"], scrcpy_state["mac_audio_file"], os.path.expanduser("~/.connectphone_temp_video_only.mp4")]:
        if f and os.path.exists(f):
            try:
                os.remove(f)
            except Exception:
                pass
                
    scrcpy_state["mirror_type"] = None
                
    scrcpy_state = {
        "session_start_time": 0.0,
        "orientation": "flip0",
        "recording_active": False,
        "clip_start_time": 0.0,
        "mac_audio_file": None,
        "audio_proc": None,
        "rec_file": None,
        "temp_mkv": None,
        "mirror_type": None
    }

def get_live_metrics():
    metrics = {
        "success": False,
        "connected": False,
        "battery": {},
        "ram": {},
        "storage": {},
        "network": {},
        "system": {}
    }
    
    devices = ConnectPhone.check_adb_devices()
    if not devices:
        return metrics
        
    metrics["connected"] = True
    
    # 1. Query battery
    res_bat = subprocess.run(["adb", "shell", "dumpsys battery"], capture_output=True, text=True)
    bat_data = {}
    for line in res_bat.stdout.splitlines():
        line = line.strip()
        if ":" in line:
            parts = line.split(":", 1)
            k = parts[0].strip().lower()
            v = parts[1].strip()
            bat_data[k] = v
            
    try:
        level = int(bat_data.get("level", 0))
        status_code = int(bat_data.get("status", 1))
        health_code = int(bat_data.get("health", 1))
        temp = float(bat_data.get("temperature", 0)) / 10.0
        volt = float(bat_data.get("voltage", 0)) / 1000.0
        
        status_map = {1: "Unknown", 2: "Charging", 3: "Discharging", 4: "Not Charging", 5: "Full"}
        health_map = {1: "Unknown", 2: "Good", 3: "Overheat", 4: "Dead", 5: "Over Voltage", 6: "Failure", 7: "Cold"}
        
        power = "Battery"
        if bat_data.get("ac powered") == "true":
            power = "AC Charger"
        elif bat_data.get("usb powered") == "true":
            power = "USB Port"
        elif bat_data.get("wireless powered") == "true":
            power = "Wireless Charger"
            
        metrics["battery"] = {
            "level": level,
            "status": status_map.get(status_code, "Unknown"),
            "health": health_map.get(health_code, "Unknown"),
            "temperature": temp,
            "voltage": volt,
            "technology": bat_data.get("technology", "Li-ion"),
            "power_source": power
        }
    except Exception:
        pass

    # 2. Query RAM (/proc/meminfo)
    res_ram = subprocess.run(["adb", "shell", "cat /proc/meminfo"], capture_output=True, text=True)
    ram_data = {}
    for line in res_ram.stdout.splitlines():
        if ":" in line:
            parts = line.split(":", 1)
            k = parts[0].strip()
            v_parts = parts[1].strip().split()
            if v_parts:
                ram_data[k] = int(v_parts[0])
            
    try:
        total_kb = ram_data.get("MemTotal", 0)
        avail_kb = ram_data.get("MemAvailable", ram_data.get("MemFree", 0))
        used_kb = total_kb - avail_kb
        used_pct = round((used_kb / total_kb) * 100, 1) if total_kb > 0 else 0
        
        metrics["ram"] = {
            "total_gb": round(total_kb / 1024 / 1024, 2),
            "avail_gb": round(avail_kb / 1024 / 1024, 2),
            "used_gb": round(used_kb / 1024 / 1024, 2),
            "used_percent": used_pct
        }
    except Exception:
        pass

    # 3. Query Storage (df -k /data)
    res_store = subprocess.run(["adb", "shell", "df -k /data"], capture_output=True, text=True)
    try:
        lines = res_store.stdout.splitlines()
        if len(lines) >= 2:
            parts = lines[1].split()
            total_kb = int(parts[1])
            used_kb = int(parts[2])
            avail_kb = int(parts[3])
            pct = int(parts[4].replace("%", ""))
            
            metrics["storage"] = {
                "total_gb": round(total_kb / 1024 / 1024, 1),
                "used_gb": round(used_kb / 1024 / 1024, 1),
                "avail_gb": round(avail_kb / 1024 / 1024, 1),
                "used_percent": pct
            }
    except Exception:
        pass

    # 4. Network Info
    ip = "Disconnected"
    res_route = subprocess.run(["adb", "shell", "ip route"], capture_output=True, text=True)
    for line in res_route.stdout.splitlines():
        if "src" in line:
            parts = line.split()
            try:
                idx = parts.index("src")
                ip = parts[idx + 1]
                break
            except Exception:
                pass
                
    serial = devices[0]
    conn_type = "USB connection"
    if ":" in serial or "." in serial:
        conn_type = "Wi-Fi connection"
        
    metrics["network"] = {
        "ip": ip,
        "type": conn_type
    }

    # 5. Uptime & Load Avg
    uptime_str = "--"
    load_avg = "--"
    res_uptime = subprocess.run(["adb", "shell", "uptime"], capture_output=True, text=True)
    try:
        out = res_uptime.stdout.strip()
        if "up" in out:
            up_part = out.split("up", 1)[1].split(",", 1)
            uptime_str = up_part[0].strip()
        if "load average:" in out:
            load_avg = out.split("load average:")[1].strip()
    except Exception:
        pass
        
    metrics["system"] = {
        "uptime": uptime_str,
        "load_average": load_avg
    }
    
    metrics["success"] = True
    return metrics

def get_detailed_adb_devices():
    try:
        res = subprocess.run(["adb", "devices", "-l"], capture_output=True, text=True)
        lines = res.stdout.strip().split("\n")[1:]
        devices_list = []
        for line in lines:
            if not line.strip():
                continue
            parts = line.split()
            if len(parts) >= 2:
                serial = parts[0]
                status = parts[1]
                
                # Check if wireless
                conn_type = "wireless" if ":" in serial else "usb"
                
                # Find model, product, device in properties
                model = "Android Device"
                product = "generic"
                for part in parts[2:]:
                    if part.startswith("model:"):
                        model = part.split(":")[1].replace("_", " ")
                    elif part.startswith("product:"):
                        product = part.split(":")[1]
                        
                devices_list.append({
                    "serial": serial,
                    "status": status,
                    "type": conn_type,
                    "model": model,
                    "product": product
                })
        return devices_list
    except Exception:
        return []

def check_and_autoselect_device(devices_detailed):
    online_serials = [d["serial"] for d in devices_detailed if d["status"] == "device"]
    active = os.environ.get("ANDROID_SERIAL", "")
    if active and active in online_serials:
        return active
    if online_serials:
        os.environ["ANDROID_SERIAL"] = online_serials[0]
        return online_serials[0]
    all_serials = [d["serial"] for d in devices_detailed]
    if all_serials:
        if active and active in all_serials:
            return active
        os.environ["ANDROID_SERIAL"] = all_serials[0]
        return all_serials[0]
    os.environ.pop("ANDROID_SERIAL", None)
    return ""

def discover_all_mdns_services(timeout=2.0):
    from zeroconf import Zeroconf, ServiceBrowser
    
    discovered = []
    
    class MultiListener:
        def add_service(self, zc, type, name):
            try:
                info = zc.get_service_info(type, name)
                if info:
                    addresses = info.parsed_addresses()
                    ipv4_addrs = [addr for addr in addresses if '.' in addr]
                    ip = ipv4_addrs[0] if ipv4_addrs else (addresses[0] if addresses else "unknown")
                    
                    # Clean up service name (e.g. adb-xxxx._adb-tls-connect._tcp.local. -> adb-xxxx)
                    clean_name = name.split(".")[0]
                    discovered.append({
                        "name": clean_name,
                        "ip": ip,
                        "port": info.port,
                        "type": "connect" if "connect" in type else "pairing"
                    })
            except Exception:
                pass
        def remove_service(self, zc, type, name):
            pass
        def update_service(self, zc, type, name):
            pass

    # Start Zeroconf browser for both connect and pairing service types
    zc = None
    try:
        zc = Zeroconf()
        listener = MultiListener()
        b1 = ServiceBrowser(zc, "_adb-tls-connect._tcp.local.", listener)
        b2 = ServiceBrowser(zc, "_adb-tls-pairing._tcp.local.", listener)
        time.sleep(timeout)
    except Exception:
        pass
    finally:
        if zc:
            zc.close()
            
    return discovered







class ConnectPhoneUIHandler(http.server.BaseHTTPRequestHandler):
    # Suppress verbose log messages on terminal for clean output
    def log_message(self, format, *args):
        sys.stdout.write(f"[UI Server] {format % args}\n")
        sys.stdout.flush()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        if self.path.startswith('/api/'):
            self.handle_api_get()
        else:
            self.serve_static_files()

    def do_POST(self):
        if self.path.startswith('/api/'):
            self.handle_api_post()
        else:
            self.send_error(404, "Not Found")

    def serve_static_files(self):
        path = self.path.split('?')[0]
        if path == '/':
            path = '/index.html'
        
        file_path = os.path.join(PROJECT_DIR, 'ui', path.lstrip('/'))
        
        if not os.path.exists(file_path) or os.path.isdir(file_path):
            self.send_error(404, f"File Not Found: {path}")
            return
            
        content_type = 'text/plain'
        if file_path.endswith('.html'):
            content_type = 'text/html'
        elif file_path.endswith('.css'):
            content_type = 'text/css'
        elif file_path.endswith('.js'):
            content_type = 'application/javascript'
        elif file_path.endswith('.png'):
            content_type = 'image/png'
        elif file_path.endswith('.jpg') or file_path.endswith('.jpeg'):
            content_type = 'image/jpeg'
        elif file_path.endswith('.svg'):
            content_type = 'image/svg+xml'
            
        try:
            with open(file_path, 'rb') as f:
                content = f.read()
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, f"Internal Server Error: {e}")

    def handle_api_get(self):
        global _status_cache
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()

        if self.path == '/api/status':
            # Serve from cache (refreshed every ~1.2 s in background) — sub-millisecond response
            with _status_cache_lock:
                payload = _status_cache
            if payload is None:
                # First boot: build synchronously once, then store in global
                payload = _build_status_payload()
                with _status_cache_lock:
                    _status_cache = payload
            self.wfile.write(json.dumps(payload).encode('utf-8'))
        elif self.path == '/api/metrics':
            res_metrics = get_live_metrics()
            self.wfile.write(json.dumps(res_metrics).encode('utf-8'))
        elif self.path == '/api/settings/audio_devices':
            devices = ConnectPhone.get_macos_audio_devices()
            self.wfile.write(json.dumps({"success": True, "devices": devices}).encode('utf-8'))
        elif self.path == '/api/mdns/discover':
            discovered = discover_all_mdns_services()
            self.wfile.write(json.dumps({"success": True, "services": discovered}).encode('utf-8'))
        elif self.path == '/api/screenshots/list':
            try:
                # Find screenshots from common paths and sort by newest first
                cmd = "adb shell 'ls -t /sdcard/DCIM/Screenshots/* /sdcard/Pictures/Screenshots/* 2>/dev/null'"
                res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                # Even if one directory doesn't exist (exit code 1), the other might succeed and print to stdout
                out = res.stdout or ""
                lines = [line.strip() for line in out.split('\n') if line.strip()]
                latest = lines[:10]
                self.wfile.write(json.dumps({"success": True, "files": latest}).encode('utf-8'))
            except Exception as e:
                self.wfile.write(json.dumps({"success": False, "files": [], "error": str(e)}).encode('utf-8'))
        else:
            self.wfile.write(json.dumps({"error": "Unknown GET endpoint"}).encode('utf-8'))

    def handle_api_post(self):
        global scrcpy_proc, scrcpy_state, sync_watcher_thread, sync_watcher_active
        
        content_length_header = self.headers.get('Content-Length')
        content_length = int(content_length_header) if content_length_header is not None else 0
        post_data = self.rfile.read(content_length) if content_length > 0 else b""
        
        data = {}
        if post_data:
            try:
                data = json.loads(post_data.decode('utf-8'))
            except Exception:
                pass
                
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        
        res_data = {"success": False, "message": ""}
        
        try:
            if self.path == '/api/devices/select':
                serial = str(data.get("serial", "")).strip()
                if not serial:
                    res_data["message"] = "Serial is required."
                else:
                    os.environ["ANDROID_SERIAL"] = serial
                    res_data["success"] = True
                    res_data["message"] = f"Target device serial set to {serial}."
            elif self.path == '/api/connect':
                ip = str(data.get("ip", "")).strip()
                port = str(data.get("port", "5555")).strip()
                if not ip:
                    res_data["message"] = "IP address is required."
                else:
                    ConnectPhone.save_last_ip(ip)
                    ip_port = f"{ip}:{port}"
                    res = subprocess.run(["adb", "connect", ip_port], capture_output=True, text=True)
                    stdout = res.stdout or ""
                    stderr = res.stderr or ""
                    output = (stdout + " " + stderr).strip()
                    if "connected to" in output.lower() or "already connected to" in output.lower():
                        res_data["success"] = True
                        res_data["message"] = f"Successfully connected to {ip_port}!"
                        # Cache this port for lightning reconnect
                        try:
                            cfg = ConnectPhone.load_config()
                            cfg["last_port"] = int(port)
                            ConnectPhone.save_config(cfg)
                        except Exception:
                            pass
                        _invalidate_status_cache()
                    else:
                        res_data["message"] = (
                            f"Connection failed: {output}\n\n"
                            "💡 WHY DID THIS FAIL?\n"
                            "Your phone was found on the network, but it actively rejected/refused the connection. This is because:\n"
                            "• Your Mac has NOT been paired/authorized with your phone yet. You must complete the 'Wireless Debugging Pairing' step below once first using the 6-digit code.\n"
                            "• Or, your phone screen turned off and went to sleep, closing the active connection. Wake your phone and toggle Wireless Debugging OFF and ON."
                        )
                        
            elif self.path == '/api/connect/auto':
                config = ConnectPhone.load_config()
                ip = config.get("last_ip", "").strip()
                last_port = config.get("last_port", None)
                if last_port:
                    try:
                        last_port = int(last_port)
                    except Exception:
                        last_port = None
                if not ip:
                    res_data["success"] = False
                    res_data["message"] = "No previously paired IP address found in config. Connect manually first."
                else:
                    connected = False

                    # 0. Lightning path: try the last-known port directly (< 300 ms if phone kept same port)
                    if last_port and last_port not in (5555,):
                        import socket as _sock
                        try:
                            s = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
                            s.settimeout(0.25)
                            open_result = s.connect_ex((ip, last_port))
                            s.close()
                        except Exception:
                            open_result = 1
                        if open_result == 0:
                            ip_port = f"{ip}:{last_port}"
                            subprocess.run(["adb", "disconnect", ip_port], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                            res = subprocess.run(["adb", "connect", ip_port], capture_output=True, text=True)
                            if "connected to" in (res.stdout or "").lower() or "already connected to" in (res.stdout or "").lower():
                                res_data["success"] = True
                                res_data["message"] = f"⚡ Instantly reconnected to {ip_port}!"
                                connected = True
                                _invalidate_status_cache()

                    # 1. mDNS discovery (1.5 s timeout — usually resolves in < 200 ms)
                    if not connected:
                        _, mdns_port = discover_adb_service_hybrid(
                            "_adb-tls-connect._tcp.local.",
                            target_ip=ip,
                            timeout=1.5
                        )
                        if mdns_port:
                            ip_port = f"{ip}:{mdns_port}"
                            subprocess.run(["adb", "disconnect", ip_port], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                            res = subprocess.run(["adb", "connect", ip_port], capture_output=True, text=True)
                            stdout = res.stdout or ""
                            if "connected to" in stdout.lower() or "already connected to" in stdout.lower():
                                res_data["success"] = True
                                res_data["message"] = f"Successfully auto-connected to phone at {ip_port}!"
                                connected = True
                                # Remember this port for the lightning path next time
                                config["last_port"] = mdns_port
                                ConnectPhone.save_config(config)
                                _invalidate_status_cache()

                    # 2. Parallel port scan fallback
                    if not connected:
                        target_port = scan_and_connect_wireless_debug(ip, last_known_port=last_port)
                        if target_port:
                            res_data["success"] = True
                            res_data["message"] = f"Successfully auto-connected to phone at {ip}:{target_port}!"
                            connected = True
                            config["last_port"] = target_port
                            ConnectPhone.save_config(config)
                            _invalidate_status_cache()

                    if not connected:
                        import platform
                        ping_param = "-n" if platform.system().lower() == "windows" else "-c"
                        ping_res = subprocess.run(["ping", ping_param, "1", "-t", "1", ip], capture_output=True)
                        if ping_res.returncode == 0:
                            res_data["message"] = (
                                f"Auto-connect failed. No active wireless debugging ports found open on {ip}.\n\n"
                                "💡 DIAGNOSIS:\n"
                                "Your phone is online and responding, but the connection was refused. This usually means:\n"
                                "• The Wireless Debugging service is toggled OFF on your phone.\n"
                                "• The device has not been paired with this computer yet.\n\n"
                                "🔧 HOW TO FIX:\n"
                                "1. Verify that 'Wireless Debugging' is toggled ON under Developer Options.\n"
                                "2. If it is already ON, try toggling it OFF and back ON to refresh the service port.\n"
                                "3. If this is a new phone, please pair it using the Wireless Debugging Pairing section (enter port and code) to establish trust."
                            )
                        else:
                            res_data["message"] = (
                                f"Auto-connect failed. Could not reach your phone at {ip}.\n\n"
                                "💡 DIAGNOSIS: The device is offline/unreachable. Your phone's IP address might have changed, "
                                "or Wi-Fi is disconnected. Please check the current IP Address listed under Wireless Debugging on your phone."
                            )
                        
            elif self.path == '/api/disconnect':
                target_ip = str(data.get("ip", "")).strip() if data and data.get("ip") is not None else ""
                target_port = str(data.get("port", "")).strip() if data and data.get("port") is not None else ""
                if target_ip and target_port:
                    ip_port = f"{target_ip}:{target_port}"
                    res = subprocess.run(["adb", "disconnect", ip_port], capture_output=True, text=True)
                    res_data["message"] = f"Disconnected from {ip_port}."
                elif target_ip:
                    res = subprocess.run(["adb", "disconnect", target_ip], capture_output=True, text=True)
                    res_data["message"] = f"Disconnected from {target_ip}."
                else:
                    res = subprocess.run(["adb", "disconnect"], capture_output=True, text=True)
                    res_data["message"] = "Disconnected from all devices."
                    stop_scrcpy_bg()
                res_data["success"] = True
                _invalidate_status_cache()

            elif self.path == '/api/pair':
                ip = str(data.get("ip", "")).strip()
                port = str(data.get("port", "")).strip()
                code = str(data.get("code", "")).strip()
                if not ip or not port or not code:
                    res_data["message"] = "IP, Port, and Pairing Code are all required."
                else:
                    ConnectPhone.save_last_ip(ip)
                    _invalidate_status_cache()
                    ip_port = f"{ip}:{port}"
                    print(f"[UI Server] Attempting wireless pairing to {ip_port} with code {code}...")

                    def _try_pair_cli(ip_port, code):
                        """Strategy 1: adb pair <ip:port> <code>  — works on ADB >= 30."""
                        try:
                            res = subprocess.run(
                                ["adb", "pair", ip_port, code],
                                capture_output=True, text=True, timeout=12
                            )
                            combined = (res.stdout or "") + " " + (res.stderr or "")
                            print(f"[UI Server] Strategy 1 (CLI arg): rc={res.returncode} out={res.stdout.strip()} err={res.stderr.strip()}")
                            if "successfully paired" in combined.lower():
                                return True, combined.strip()
                            return False, combined.strip()
                        except subprocess.TimeoutExpired:
                            return False, "timeout"
                        except Exception as e:
                            return False, str(e)

                    def _try_pair_stdin(ip_port, code):
                        """Strategy 2: adb pair <ip:port>  then write code to stdin."""
                        try:
                            # Give adb a moment to print the prompt, then send code
                            res = subprocess.run(
                                ["adb", "pair", ip_port],
                                input=f"{code}\n",
                                capture_output=True, text=True, timeout=12
                            )
                            combined = (res.stdout or "") + " " + (res.stderr or "")
                            print(f"[UI Server] Strategy 2 (stdin): rc={res.returncode} out={res.stdout.strip()} err={res.stderr.strip()}")
                            if "successfully paired" in combined.lower():
                                return True, combined.strip()
                            # If it printed "Enter pairing code:" and no error, count as success
                            if "enter pairing code" in combined.lower() and "error" not in combined.lower() and "failed" not in combined.lower():
                                return True, "Successfully paired (stdin method)."
                            return False, combined.strip()
                        except subprocess.TimeoutExpired:
                            return False, "timeout"
                        except Exception as e:
                            return False, str(e)

                    def _try_pair_pty(ip_port, code):
                        """Strategy 3: use a pseudo-terminal so adb sees a real TTY (avoids prompt-suppress issues)."""
                        try:
                            import pty, os, select as _sel
                            master_fd, slave_fd = pty.openpty()
                            proc = subprocess.Popen(
                                ["adb", "pair", ip_port],
                                stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
                                close_fds=True
                            )
                            os.close(slave_fd)
                            output_chunks = []
                            code_sent = False
                            deadline = time.time() + 12
                            while time.time() < deadline:
                                rlist, _, _ = _sel.select([master_fd], [], [], 0.15)
                                if rlist:
                                    try:
                                        chunk = os.read(master_fd, 4096).decode("utf-8", errors="replace")
                                    except OSError:
                                        break
                                    output_chunks.append(chunk)
                                    combined_so_far = "".join(output_chunks)
                                    if not code_sent and "enter pairing code" in combined_so_far.lower():
                                        time.sleep(0.05)
                                        os.write(master_fd, f"{code}\n".encode())
                                        code_sent = True
                                if proc.poll() is not None:
                                    # Drain remaining output
                                    try:
                                        rlist2, _, _ = _sel.select([master_fd], [], [], 0.3)
                                        if rlist2:
                                            output_chunks.append(os.read(master_fd, 4096).decode("utf-8", errors="replace"))
                                    except OSError:
                                        pass
                                    break
                            try:
                                os.close(master_fd)
                            except OSError:
                                pass
                            proc.wait(timeout=2)
                            combined = "".join(output_chunks)
                            print(f"[UI Server] Strategy 3 (pty): rc={proc.returncode} output={combined.strip()}")
                            if "successfully paired" in combined.lower():
                                return True, combined.strip()
                            return False, combined.strip()
                        except Exception as e:
                            print(f"[UI Server] Strategy 3 (pty) exception: {e}")
                            return False, str(e)

                    # ── Try all 3 strategies in order ──────────────────────────
                    success = False
                    final_msg = ""

                    ok, msg = _try_pair_cli(ip_port, code)
                    if ok:
                        success = True
                        final_msg = msg
                    
                    if not success:
                        ok, msg = _try_pair_stdin(ip_port, code)
                        if ok:
                            success = True
                            final_msg = msg

                    if not success:
                        ok, msg = _try_pair_pty(ip_port, code)
                        if ok:
                            success = True
                            final_msg = msg

                    # ── If all failed, do ONE adb server reset and retry strategy 1 ──
                    if not success:
                        print("[UI Server] All 3 strategies failed. Resetting ADB server and doing one final retry...")
                        subprocess.run(["adb", "kill-server"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        subprocess.run(["adb", "start-server"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        time.sleep(1.0)
                        ok, msg = _try_pair_cli(ip_port, code)
                        if not ok:
                            ok, msg = _try_pair_stdin(ip_port, code)
                        if ok:
                            success = True
                            final_msg = msg
                        else:
                            final_msg = msg  # last error for display

                    # ── Build response ──────────────────────────────────────────
                    if success:
                        res_data["success"] = True
                        res_data["message"] = (
                            "✅ Successfully paired! Your Mac is now trusted by this phone.\n"
                            "Next step: tap 'Wireless Debugging' on your phone to see the Connection Port "
                            "(NOT the pairing port), enter it in the IP/Port box and click Connect."
                        )
                    else:
                        err_lower = final_msg.lower()
                        if "connection refused" in err_lower or "timeout" in err_lower or "timed out" in err_lower:
                            res_data["message"] = (
                                f"Pairing failed: {final_msg}\n\n"
                                "💡 Connection refused / timeout. Both your Mac and phone must be on the "
                                "same Wi-Fi network. If you use a router with AP Isolation / Client Isolation, "
                                "disable it. Also ensure Wireless Debugging is still toggled ON."
                            )
                        elif "protocol" in err_lower or "read status" in err_lower or "undefined" in err_lower or "fault" in err_lower:
                            res_data["message"] = (
                                f"Pairing failed: {final_msg}\n\n"
                                "💡 ADB could not complete the pairing handshake. Most common causes:\n"
                                "• The 6-digit code or pairing port has expired — close the popup on your phone, "
                                "reopen it and use the fresh code + port shown.\n"
                                "• You entered the main Wireless Debugging port instead of the Pairing port "
                                "(the pairing port is only shown inside the 'Pair with code' popup).\n"
                                "• Very rarely, the code was entered too slowly — try again immediately after opening the popup."
                            )
                        else:
                            res_data["message"] = (
                                f"Pairing failed: {final_msg}\n\n"
                                "💡 Make sure the 'Pair device with pairing code' popup is still open on "
                                "your phone and you are using the port shown inside that popup."
                            )
                
            elif self.path == '/api/restart_adb':
                subprocess.run(["adb", "kill-server"])
                subprocess.run(["adb", "start-server"])
                res_data["success"] = True
                res_data["message"] = "ADB server restarted successfully."
                
            elif self.path == '/api/ping':
                ip = str(data.get("ip", "")).strip() if data and data.get("ip") is not None else ""
                if not ip:
                    ip = "unknown"
                    res_route = subprocess.run(["adb", "shell", "ip route"], capture_output=True, text=True)
                    for line in res_route.stdout.splitlines():
                        if "src" in line:
                            parts = line.split()
                            try:
                                idx = parts.index("src")
                                ip = parts[idx + 1]
                                break
                            except Exception:
                                pass
                
                if ip == "unknown" or not ip:
                    res_data["message"] = "Could not find active wireless IP address for the device. Please connect or specify IP manually."
                else:
                    # -c 3: 3 packets, -t 2: timeout in 2 seconds
                    res_ping = subprocess.run(["ping", "-c", "3", "-t", "2", ip], capture_output=True, text=True)
                    if res_ping.returncode == 0:
                        lines = res_ping.stdout.splitlines()
                        rtt_line = ""
                        for l in lines:
                            if "rtt min/avg/max/mdev" in l or "round-trip min/avg/max/stddev" in l:
                                rtt_line = l
                                break
                        if rtt_line:
                            res_data["success"] = True
                            res_data["message"] = f"Ping Success to {ip}: {rtt_line.strip()}"
                        else:
                            res_data["success"] = True
                            res_data["message"] = f"Ping Success to {ip} (no stats parsed)"
                    else:
                        res_data["message"] = f"Ping failed to target IP: {ip}"
                
            elif self.path == '/api/mirror':
                mirror_type = data.get("type", "screen") # screen, camera, audio, record
                config = ConnectPhone.load_config()
                
                preset = config.get("audio_preset", "voice_communication")
                audio_args = []
                if preset == "voice_communication":
                    audio_args = ["--audio-source=mic-voice-communication", "--audio-codec=opus", "--audio-bit-rate=320000"]
                elif preset == "studio_unprocessed":
                    audio_args = ["--audio-source=mic-unprocessed", "--audio-codec=opus", "--audio-bit-rate=320000"]
                elif preset == "camcorder":
                    audio_args = ["--audio-source=mic-camcorder", "--audio-codec=opus", "--audio-bit-rate=320000"]
                elif preset == "output":
                    audio_args = ["--audio-source=output", "--audio-codec=opus", "--audio-bit-rate=128000"]
                else:
                    audio_args = ["--audio-source=mic", "--audio-codec=opus", "--audio-bit-rate=128000"]
                
                devices = ConnectPhone.check_adb_devices()
                is_wireless = any(":" in d for d in devices) if devices else False

                cmd = ["scrcpy"]
                a_buf = config.get("audio_buffer", "100")
                cmd.append(f"--audio-buffer={a_buf}")
                
                temp_mkv_path = None
                is_cam = False
                
                if mirror_type == "screen":
                    cmd += ["--audio-source=output"]
                    if config.get("screen_off_enabled", False):
                        cmd.append("--turn-screen-off")
                    if config.get("stay_awake_enabled", True):
                        cmd.append("--stay-awake")
                    if config.get("show_touches_enabled", False):
                        cmd.append("--show-touches")
                        
                    k_mode = config.get("keyboard_mode", "uhid")
                    cmd.append(f"--keyboard={k_mode}")
                    
                    # Apply video quality settings to screen mirroring as well
                    s_codec = config.get("camera_codec", "h265")
                    s_bitrate = config.get("camera_bitrate", "32M")
                    if is_wireless:
                        s_bitrate = "16M"
                        cmd.append("--video-buffer=100")
                    cmd += [f"--video-bit-rate={s_bitrate}", f"--video-codec={s_codec}"]
                        
                elif mirror_type == "camera":
                    is_cam = True
                    facing = data.get("camera_facing", "back")
                    resolution = data.get("resolution", "1080p")
                    no_audio = data.get("no_audio", False)
                    
                    cmd += ["--video-source=camera", f"--camera-facing={facing}"]
                    if no_audio:
                        cmd.append("--no-audio")
                    else:
                        cmd += audio_args
                        
                    if resolution == "4k":
                        cmd.append("--camera-size=3840x2160")
                    elif resolution == "1080p":
                        cmd.append("--camera-size=1920x1080")
                    elif resolution == "720p":
                        cmd.append("--camera-size=1280x720")
                        
                    if config.get("mirror_enabled", True):
                        cmd.append("--orientation=flip0")
                        
                    # Apply camera quality preferences
                    c_bitrate = config.get("camera_bitrate", "32M")
                    c_fps = config.get("camera_fps", "60")
                    c_codec = config.get("camera_codec", "h265")
                    
                    # For standard camera mirroring, cap FPS to 30 to match sensor limits and prevent encoder overflow
                    if c_fps not in ["120", "240"]:
                        c_fps = "30"
                        
                    if is_wireless:
                        c_bitrate = "6M"
                        c_codec = "h264"
                        cmd.append("--video-buffer=150")
                    else:
                        if facing == "front":
                            c_bitrate = "12M"
                            c_codec = "h264"
                            
                    cmd.append("--stay-awake")
                    cmd += [f"--video-bit-rate={c_bitrate}", f"--camera-fps={c_fps}", f"--video-codec={c_codec}"]
                    
                    if c_fps in ["120", "240"]:
                        cmd = [a for a in cmd if not a.startswith("--camera-size=")]
                        cmd.append("--camera-size=1280x720")
                        cmd.append("--camera-high-speed")
                        
                    temp_mkv_path = os.path.expanduser("~/.connectphone_temp_rec.mkv")
                    cmd.append(f"--record={temp_mkv_path}")
                    cmd.append("--record-orientation=0")
                    
                elif mirror_type == "audio":
                    cmd += ["--no-video"] + audio_args
                    
                elif mirror_type == "record":
                    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                    record_path = os.path.expanduser(f"~/Desktop/scrcpy_record_{timestamp}.mp4")
                    cmd += ["--record=" + record_path, "--audio-source=output"]
                    
                    if config.get("screen_off_enabled", False):
                        cmd.append("--turn-screen-off")
                    if config.get("stay_awake_enabled", True):
                        cmd.append("--stay-awake")
                    if config.get("show_touches_enabled", False):
                        cmd.append("--show-touches")
                        
                    k_mode = config.get("keyboard_mode", "uhid")
                    cmd.append(f"--keyboard={k_mode}")
                    
                    # Apply video quality settings to recording as well
                    s_codec = config.get("camera_codec", "h265")
                    s_bitrate = config.get("camera_bitrate", "32M")
                    if is_wireless:
                        s_bitrate = "16M"
                        cmd.append("--video-buffer=100")
                    cmd += [f"--video-bit-rate={s_bitrate}", f"--video-codec={s_codec}"]
                    
                    res_data["message"] = f"Entire session is being recorded to Desktop: {os.path.basename(record_path)}"
                    
                has_record = any(arg.startswith("--record=") for arg in cmd)
                has_flip = any(arg.startswith("--orientation=flip") for arg in cmd)
                if has_record and has_flip:
                    cmd.append("--record-orientation=0")
                    
                stop_scrcpy_bg()
                
                scrcpy_state["mirror_type"] = mirror_type
                scrcpy_state["session_start_time"] = time.time()
                scrcpy_state["orientation"] = "flip0"
                scrcpy_state["recording_active"] = False
                scrcpy_state["temp_mkv"] = temp_mkv_path
                
                if mirror_type == "camera":
                    # Ensure device is awake so camera capture session does not get suspended
                    try:
                        subprocess.run(["adb", "shell", "input", "keyevent", "KEYCODE_WAKEUP"], capture_output=True)
                    except Exception:
                        pass
                
                scrcpy_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                
                # Auto-unlock lock screen concurrently via macOS Touch ID
                if mirror_type == "screen" and ConnectPhone.is_keyguard_locked():
                    if config.get("screen_off_enabled", False):
                        def delayed_unlock():
                            time.sleep(2.2)
                            ConnectPhone.unlock_device_with_touch_id(config, interactive=False, wake_screen=False)
                        t_unlock = threading.Thread(target=delayed_unlock)
                        t_unlock.daemon = True
                        t_unlock.start()
                        res_data["message"] = "Screen mirroring started with screen off. Unlocking phone via Touch ID..."
                    else:
                        def parallel_unlock():
                            time.sleep(0.5)
                            ConnectPhone.unlock_device_with_touch_id(config, interactive=False)
                        t_unlock = threading.Thread(target=parallel_unlock)
                        t_unlock.daemon = True
                        t_unlock.start()
                        res_data["message"] = "Screen mirroring started. Verify macOS Touch ID to unlock phone screen."
                
                def log_reader():
                    global scrcpy_proc
                    for line in iter(scrcpy_proc.stdout.readline, b''):
                        line_str = line.decode('utf-8', errors='ignore')
                        print(f"[scrcpy] {line_str.strip()}", flush=True)
                        if "Texture:" in line_str:
                            scrcpy_state["session_start_time"] = time.time()
                        if "Display orientation set to" in line_str:
                            parts = line_str.split("set to")
                            if len(parts) >= 2:
                                scrcpy_state["orientation"] = parts[1].strip()
                                
                t = threading.Thread(target=log_reader)
                t.daemon = True
                t.start()
                
                res_data["success"] = True
                if not res_data["message"]:
                    res_data["message"] = "Mirroring session launched successfully!"
                    
            elif self.path == '/api/mirror/stop':
                stop_scrcpy_bg()
                scrcpy_state["mirror_type"] = None
                res_data["success"] = True
                res_data["message"] = "Mirroring feed closed."
                
            elif self.path == '/api/camera/capture':
                success, filename = camera_capture()
                if success:
                    res_data["success"] = True
                    res_data["message"] = f"Instant snapshot saved to Desktop: {filename}"
                else:
                    res_data["message"] = filename
                    
            elif self.path == '/api/camera/record_toggle':
                if not scrcpy_state["recording_active"]:
                    success, msg = camera_record_start()
                    res_data["success"] = success
                    res_data["message"] = msg
                else:
                    success, msg = camera_record_stop()
                    res_data["success"] = success
                    res_data["message"] = msg
                    
            elif self.path == '/api/device/unlock':
                config = ConnectPhone.load_config()
                android_pin = config.get("android_pin", "")
                if not android_pin:
                    res_data["success"] = False
                    res_data["message"] = "Android Backup PIN is not configured. Please enter your PIN in Preferences."
                else:
                    def run_touch_id_auth():
                        ConnectPhone.unlock_device_with_touch_id(config, interactive=False)
                    t_auth = threading.Thread(target=run_touch_id_auth)
                    t_auth.daemon = True
                    t_auth.start()
                    res_data["success"] = True
                    res_data["message"] = "Touch ID verification triggered! Verify fingerprint on Mac."
                
            elif self.path == '/api/settings/save':
                config = ConnectPhone.load_config()
                for key in ["camera_bitrate", "camera_fps", "camera_codec", "audio_sync_delay", "android_pin", "applock_pin", "audio_preset", "mirror_enabled", "screen_off_enabled", "stay_awake_enabled", "show_touches_enabled", "keyboard_mode", "biometric_daemon_enabled", "mac_mic_device", "audio_buffer", "device_profile"]:
                    if key in data:
                        config[key] = data[key]
                ConnectPhone.save_config(config)
                res_data["success"] = True
                res_data["message"] = "Preferences saved successfully!"
                
            elif self.path == '/api/screenshots/pull':
                filepath = str(data.get("path", "")).strip()
                if not filepath:
                    res_data["message"] = "File path is required."
                else:
                    desk_path = os.path.expanduser("~/Desktop")
                    try:
                        subprocess.run(["adb", "pull", filepath, desk_path], check=True, capture_output=True)
                        res_data["success"] = True
                        res_data["message"] = f"Saved to Desktop: {os.path.basename(filepath)}"
                    except Exception as e:
                        res_data["message"] = f"Failed to pull screenshot: {e}"

            elif self.path == '/api/clipboard/sync/start':
                global scrcpy_clipboard_proc
                if 'scrcpy_clipboard_proc' in globals() and scrcpy_clipboard_proc and scrcpy_clipboard_proc.poll() is None:
                    res_data["success"] = True
                    res_data["message"] = "Clipboard sync is already running."
                else:
                    try:
                        scrcpy_clipboard_proc = subprocess.Popen(
                            ["scrcpy", "--no-video", "--no-audio", "--no-window"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                        )
                        res_data["success"] = True
                        res_data["message"] = "Clipboard sync started seamlessly in background!"
                    except Exception as e:
                        res_data["message"] = f"Failed to start sync: {e}"

            elif self.path == '/api/clipboard/sync/stop':
                if 'scrcpy_clipboard_proc' in globals() and scrcpy_clipboard_proc:
                    try:
                        scrcpy_clipboard_proc.terminate()
                        scrcpy_clipboard_proc.wait(timeout=2)
                    except Exception:
                        pass
                    scrcpy_clipboard_proc = None
                    res_data["success"] = True
                    res_data["message"] = "Clipboard sync stopped."
                else:
                    res_data["success"] = True
                    res_data["message"] = "Clipboard sync was not running."

            elif self.path == '/api/clipboard/type':
                try:
                    mac_clipboard = subprocess.check_output(["pbpaste"]).decode("utf-8")
                    if not mac_clipboard:
                        res_data["message"] = "Mac clipboard is empty!"
                    else:
                        safe_text = mac_clipboard.replace(' ', '%s')
                        subprocess.run(["adb", "shell", "input", "text", safe_text], check=True)
                        res_data["success"] = True
                        res_data["message"] = "Typed Mac clipboard onto phone!"
                except Exception as e:
                    res_data["message"] = f"Failed to type clipboard: {e}"

            elif self.path == '/api/app/restart':
                res_data["success"] = True
                res_data["message"] = "Restarting application..."
                self.wfile.write(json.dumps(res_data).encode('utf-8'))
                # Start a separate thread to let the HTTP response flush before killing the process
                def restart_server():
                    time.sleep(0.5)
                    import sys, os
                    os.execv(sys.executable, ['python3'] + sys.argv)
                threading.Thread(target=restart_server).start()
                return

            else:
                res_data["message"] = "Unknown POST endpoint."
                
        except Exception as e:
            res_data["message"] = f"Exception: {e}"
            
        self.wfile.write(json.dumps(res_data).encode('utf-8'))

def camera_record_start():
    global scrcpy_state
    if not scrcpy_state["temp_mkv"]:
        return False, "No active video stream to record."
    if scrcpy_state["recording_active"]:
        return False, "Recording is already active."
        
    config = ConnectPhone.load_config()
    preset = config.get("audio_preset", "voice_communication")
    
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    scrcpy_state["rec_file"] = os.path.expanduser(f"~/Desktop/scrcpy_camera_rec_{timestamp}.mp4")
    scrcpy_state["clip_start_time"] = time.time() - scrcpy_state["session_start_time"]
    scrcpy_state["recording_active"] = True
    
    if preset == "mac_mic":
        scrcpy_state["mac_audio_file"] = os.path.expanduser("~/.connectphone_temp_mac_mic.wav")
        mac_mic = config.get("mac_mic_device", "default")
        device_input = f":{mac_mic}"
        cmd_audio = ["ffmpeg", "-y", "-nostdin", "-f", "avfoundation", "-i", device_input, scrcpy_state["mac_audio_file"]]
        scrcpy_state["audio_proc"] = subprocess.Popen(cmd_audio, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
    return True, "Recording started."

def camera_record_stop():
    global scrcpy_state
    if not scrcpy_state["recording_active"]:
        return False, "No active recording to stop."
        
    clip_stop_time = time.time() - scrcpy_state["session_start_time"]
    duration = clip_stop_time - scrcpy_state["clip_start_time"]
    
    if duration < 1.0:
        if scrcpy_state["audio_proc"]:
            try:
                scrcpy_state["audio_proc"].terminate()
                scrcpy_state["audio_proc"].wait()
            except Exception:
                pass
            scrcpy_state["audio_proc"] = None
        scrcpy_state["recording_active"] = False
        return False, "Recording too short (must be at least 1 second)."
        
    if scrcpy_state["audio_proc"]:
        try:
            scrcpy_state["audio_proc"].terminate()
            scrcpy_state["audio_proc"].wait()
        except Exception:
            pass
        scrcpy_state["audio_proc"] = None
        
    time.sleep(0.5)
    
    config = ConnectPhone.load_config()
    preset = config.get("audio_preset", "voice_communication")
    filter_v = ConnectPhone.get_orientation_filter(scrcpy_state["orientation"])
    
    rec_file = scrcpy_state["rec_file"]
    clip_start_time = scrcpy_state["clip_start_time"]
    temp_mkv = scrcpy_state["temp_mkv"]
    mac_audio_file = scrcpy_state["mac_audio_file"]
    
    success = False
    error_msg = ""
    
    if preset == "mac_mic" and mac_audio_file and os.path.exists(mac_audio_file):
        try:
            audio_sync_delay = float(config.get("audio_sync_delay", "0.80"))
        except ValueError:
            audio_sync_delay = 0.80
            
        if audio_sync_delay >= 0:
            delay_ms = int(audio_sync_delay * 1000)
            cmd_merge = [
                "ffmpeg", "-y",
                "-ss", f"{clip_start_time:.2f}",
                "-t", f"{duration:.2f}",
                "-i", temp_mkv,
                "-i", mac_audio_file,
            ]
            if filter_v:
                cmd_merge.extend(["-filter_complex", f"[0:v]{filter_v}[v];[1:a]adelay=delays={delay_ms}:all=1[a]", "-map", "[v]", "-map", "[a]"])
            else:
                cmd_merge.extend(["-filter_complex", f"[1:a]adelay=delays={delay_ms}:all=1[a]", "-map", "0:v", "-map", "[a]"])
            cmd_merge.extend([
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-c:a", "aac",
                "-shortest",
                rec_file
            ])
        else:
            seek_sec = abs(audio_sync_delay)
            cmd_merge = [
                "ffmpeg", "-y",
                "-ss", f"{clip_start_time:.2f}",
                "-t", f"{duration:.2f}",
                "-i", temp_mkv,
                "-ss", f"{seek_sec:.2f}",
                "-i", mac_audio_file,
                "-map", "0:v",
                "-map", "1:a",
            ]
            if filter_v:
                cmd_merge.insert(7, "-vf")
                cmd_merge.insert(8, filter_v)
            cmd_merge.extend([
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-c:a", "aac",
                "-shortest",
                rec_file
            ])
            
        merge_res = subprocess.run(cmd_merge, capture_output=True)
        if merge_res.returncode == 0:
            success = True
        else:
            error_msg = merge_res.stderr.decode('utf-8', errors='ignore')
            
        if os.path.exists(mac_audio_file):
            try:
                os.remove(mac_audio_file)
            except Exception:
                pass
    else:
        cmd_trim = [
            "ffmpeg", "-y",
            "-ss", f"{clip_start_time:.2f}",
            "-t", f"{duration:.2f}",
            "-i", temp_mkv,
        ]
        if filter_v:
            cmd_trim.extend(["-vf", filter_v])
        cmd_trim.extend([
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-c:a", "copy",
            rec_file
        ])
        trim_res = subprocess.run(cmd_trim, capture_output=True)
        if trim_res.returncode == 0:
            success = True
        else:
            error_msg = trim_res.stderr.decode('utf-8', errors='ignore')
            
    scrcpy_state["recording_active"] = False
    scrcpy_state["rec_file"] = None
    
    if success:
        return True, f"Video saved to Desktop: {os.path.basename(rec_file)}"
    else:
        return False, f"FFmpeg failed: {error_msg}"

def camera_capture():
    global scrcpy_state
    if not scrcpy_state["temp_mkv"]:
        return False, "No active video stream."
        
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    save_path = os.path.expanduser(f"~/Desktop/scrcpy_camera_{timestamp}.png")
    
    captured = False
    script_dir = PROJECT_DIR
    swift_bin = os.path.join(script_dir, "get_window_id")
    
    win_id = None
    if os.path.exists(swift_bin):
        res = subprocess.run([swift_bin, "scrcpy"], capture_output=True, text=True)
        win_id = res.stdout.strip()
        
    if win_id and win_id.isdigit():
        cap_res = subprocess.run(["screencapture", "-ol", win_id, save_path], capture_output=True)
        if cap_res.returncode == 0:
            captured = True
            
    if not captured:
        temp_mkv = scrcpy_state["temp_mkv"]
        save_path_jpg = os.path.expanduser(f"~/Desktop/scrcpy_camera_{timestamp}.jpg")
        
        duration = 0.0
        try:
            probe_cmd = [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                temp_mkv
            ]
            probe_res = subprocess.run(probe_cmd, capture_output=True, text=True)
            if probe_res.returncode == 0 and probe_res.stdout.strip():
                duration = float(probe_res.stdout.strip())
        except Exception:
            pass
            
        if duration <= 0.0:
            duration = time.time() - scrcpy_state["session_start_time"]
            duration = max(0.0, duration)
            
        filter_v = ConnectPhone.get_orientation_filter(scrcpy_state["orientation"])
        
        for offset in [1.2, 2.5, 5.0, 11.0, 21.0]:
            seek_time = max(0.0, duration - offset)
            cmd_cap = [
                "ffmpeg", "-y",
                "-skip_frame", "nokey",
                "-ss", f"{seek_time:.2f}",
                "-i", temp_mkv,
            ]
            if filter_v:
                cmd_cap.extend(["-vf", filter_v])
            cmd_cap.extend([
                "-vframes", "1",
                "-q:v", "2",
                save_path_jpg
            ])
            cap_res = subprocess.run(cmd_cap, capture_output=True)
            if cap_res.returncode == 0 and os.path.exists(save_path_jpg) and os.path.getsize(save_path_jpg) > 0:
                captured = True
                save_path = save_path_jpg
                break
                
        if not captured:
            seek_time = max(0.0, duration - 2.0)
            cmd_cap = [
                "ffmpeg", "-y",
                "-ss", f"{seek_time:.2f}",
                "-i", temp_mkv,
            ]
            if filter_v:
                cmd_cap.extend(["-vf", filter_v])
            cmd_cap.extend([
                "-vframes", "1",
                "-q:v", "2",
                save_path_jpg
            ])
            cap_res = subprocess.run(cmd_cap, capture_output=True)
            if cap_res.returncode == 0 and os.path.exists(save_path_jpg) and os.path.getsize(save_path_jpg) > 0:
                captured = True
                save_path = save_path_jpg
                
    if captured:
        return True, os.path.basename(save_path)
    else:
        return False, "Failed to capture image from video stream."

def adb_keepalive_loop():
    import time
    while True:
        try:
            config = ConnectPhone.load_config()
            if config.get("device_profile") == "oneplus":
                # Periodically run a simple adb shell command to maintain connection
                subprocess.run(["adb", "shell", "true"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
        time.sleep(30)

def start_server_in_thread(httpd):
    try:
        httpd.serve_forever()
    except Exception:
        pass
    finally:
        stop_scrcpy_bg()

def run_server():
    socketserver.TCPServer.allow_reuse_address = True
    try:
        httpd = socketserver.TCPServer(("", PORT), ConnectPhoneUIHandler)
    except OSError as e:
        # errno 48 is Address already in use on macOS
        if e.errno == 48 or "already in use" in str(e).lower():
            print(f"\nℹ️ ConnectPhone UI Dashboard is already running on http://localhost:{PORT}")
            try:
                import webview
                webview.create_window('ConnectPhone Dashboard', f"http://localhost:{PORT}", width=1450, height=950, frameless=False)
                webview.start()
            except ImportError:
                webbrowser.open(f"http://localhost:{PORT}")
            sys.exit(0)
        else:
            raise e

    # Start fast status-cache background refresher
    cache_thread = threading.Thread(target=_status_cache_worker, daemon=True)
    cache_thread.start()

    # Start background Biometric Watcher Daemon for UI App
    daemon_thread = threading.Thread(target=ConnectPhone.biometric_daemon_loop)
    daemon_thread.daemon = True
    daemon_thread.start()

    # Start background ADB Keep-Alive Watcher for OnePlus/Oppo devices
    keepalive_thread = threading.Thread(target=adb_keepalive_loop)
    keepalive_thread.daemon = True
    keepalive_thread.start()

    print(f"\n🚀 ConnectPhone UI Dashboard Running on http://localhost:{PORT}")
    
    server_thread = threading.Thread(target=start_server_in_thread, args=(httpd,))
    server_thread.daemon = True
    server_thread.start()

    try:
        import webview
        # Open as dedicated desktop app window
        webview.create_window('ConnectPhone Dashboard', f"http://localhost:{PORT}", width=1450, height=950, frameless=False)
        webview.start()
    except ImportError:
        print("💡 pywebview not found, falling back to standard web browser.")
        webbrowser.open(f"http://localhost:{PORT}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
    finally:
        httpd.shutdown()
        stop_scrcpy_bg()

if __name__ == "__main__":
    run_server()
