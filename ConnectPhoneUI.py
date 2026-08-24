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
import urllib.request
import ipaddress
import shlex
import secrets
import re
from urllib.parse import urlsplit, parse_qs
import tempfile
import posixpath
import pathlib
import shutil
import atexit
import signal

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

# ConnectPhone discovers the phone itself, pins its physical serial, and then
# connects to the current IP:port. Disable ADB's separate Bonjour auto-connect
# path so one phone cannot appear as both an IP transport and an mDNS transport.
os.environ["ADB_MDNS_AUTO_CONNECT"] = "0"


# Setup logging to a file in user's home directory so the logs can be inspected
LOG_DIRECTORY = os.path.expanduser("~/Library/Logs/ConnectPhone")
os.makedirs(LOG_DIRECTORY, mode=0o700, exist_ok=True)
try:
    os.chmod(LOG_DIRECTORY, 0o700)
except OSError:
    pass
LOG_FILE_PATH = os.path.join(LOG_DIRECTORY, "connectphone.log")
try:
    if os.path.exists(LOG_FILE_PATH) and os.path.getsize(LOG_FILE_PATH) > 5 * 1024 * 1024:
        os.replace(LOG_FILE_PATH, f"{LOG_FILE_PATH}.1")
    log_fd = os.open(LOG_FILE_PATH, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.close(log_fd)
    os.chmod(LOG_FILE_PATH, 0o600)
except Exception:
    pass

class TeeStream:
    def __init__(self, original, file_path):
        self.original = original
        self.file_path = file_path
        self._lock = threading.Lock()
        try:
            self._file = open(file_path, "a", encoding="utf-8", buffering=1)
        except OSError:
            self._file = None

    def write(self, data):
        if self.original:
            self.original.write(data)
        if self._file:
            try:
                with self._lock:
                    self._file.write(data)
            except OSError:
                pass

    def flush(self):
        if self.original and hasattr(self.original, 'flush'):
            self.original.flush()
        if self._file:
            self._file.flush()

    def isatty(self):
        if self.original and hasattr(self.original, 'isatty'):
            return self.original.isatty()
        return False

    @property
    def encoding(self):
        if self.original and hasattr(self.original, 'encoding'):
            return self.original.encoding
        return "utf-8"

sys.stdout = TeeStream(sys.stdout, LOG_FILE_PATH)
sys.stderr = TeeStream(sys.stderr, LOG_FILE_PATH)
print(f"\n--- ConnectPhone Started at {datetime.datetime.now()} ---")

# Add project dir to path to import ConnectPhone
if getattr(sys, 'frozen', False):
    # If the application is run as a bundle, the PyInstaller bootloader
    # extends the sys module by a flag frozen=True and sets the app 
    # path into variable _MEIPASS'.
    PROJECT_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.realpath(__file__)))
else:
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
from core import keychain
from core.adb_lifecycle import AdbLifecycle
from core.wireless_pairing import pair_with_code, pair_with_secret
from core.qr_pairing import new_qr_credentials, svg_data_url
from core.remote_paths import valid_remote_path as _valid_remote_path, safe_download_name as _safe_download_name
from core.file_manager import (
    create_local_folder,
    list_local_files,
    list_phone_storages,
    local_roots,
    move_local_item_to_trash,
    rename_local_item,
    rename_remote_item,
)
from core.transfer_manager import TransferManager
from core.multi_device import (
    MAX_FLEET_DEVICES,
    MirrorSessionManager,
    build_fleet,
    control_devices,
    start_emergency_alerts,
    stop_emergency_alerts,
    validate_serial,
)

ADB_LIFECYCLE = AdbLifecycle()
TRANSFER_MANAGER = TransferManager()
MIRROR_MANAGER = MirrorSessionManager()
UNLOCK_OPERATION_LOCK = threading.Lock()

PORT = 8282
UI_HOST = "127.0.0.1"
MAX_REQUEST_BODY = 1024 * 1024
API_TOKEN = keychain.get("api_token") or secrets.token_urlsafe(32)
try:
    keychain.set("api_token", API_TOKEN)
except RuntimeError:
    pass


def run_adb_cmd_with_retry(cmd_args, timeout=10, max_retries=3, delay=0.25):
    """
    Executes an ADB command with automatic retries for transient connection drops.
    """
    for attempt in range(max_retries):
        try:
            res = subprocess.run(cmd_args, capture_output=True, text=True, timeout=timeout)
            err_msg = (res.stderr or "").lower()
            if res.returncode != 0 and any(msg in err_msg for msg in ["device offline", "device still authorizing", "no devices/emulators found", "more than one device", "connection refused"]):
                if attempt < max_retries - 1:
                    time.sleep(delay)
                    continue
            return res
        except (subprocess.TimeoutExpired, OSError):
            if attempt < max_retries - 1:
                time.sleep(delay)
                continue
            raise
    return res


def _token_allowed(handler):
    # Check header first
    header_token = handler.headers.get("X-ConnectPhone-Token", "")
    if secrets.compare_digest(header_token, API_TOKEN):
        return True
    
    return False


def _origin_allowed(origin):
    """Allow only this app's local origins; block drive-by browser requests."""
    if not origin or origin == "null":
        return True
    parsed = urlsplit(origin)
    return parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"} and parsed.port == PORT


def _valid_ipv4(value):
    try:
        return isinstance(value, str) and isinstance(ipaddress.ip_address(value.strip()), ipaddress.IPv4Address)
    except ValueError:
        return False


def _valid_port(value):
    try:
        return 1 <= int(value) <= 65535
    except (TypeError, ValueError):
        return False




def parse_multipart(rfile, headers):
    content_type = headers.get('Content-Type', '')
    if 'boundary=' not in content_type:
        return None, None
    boundary = content_type.split('boundary=')[-1].strip().encode('utf-8')
    content_length = int(headers.get('Content-Length', 0))
    body = rfile.read(content_length)
    parts = body.split(b'--' + boundary)
    fields = {}
    files = {}
    for part in parts:
        if not part or part == b'\r\n' or part == b'--\r\n' or part == b'--':
            continue
        if part.startswith(b'\r\n'):
            part = part[2:]
        if part.endswith(b'\r\n'):
            part = part[:-2]
        if b'\r\n\r\n' not in part:
            continue
        header_part, content = part.split(b'\r\n\r\n', 1)
        header_lines = header_part.decode('utf-8', errors='ignore').split('\r\n')
        disposition = ''
        for line in header_lines:
            if line.lower().startswith('content-disposition:'):
                disposition = line
                break
        if not disposition:
            continue
        name_match = re.search(r'name="([^"]+)"', disposition)
        filename_match = re.search(r'filename="([^"]+)"', disposition)
        if name_match:
            name = name_match.group(1)
            if filename_match:
                filename = filename_match.group(1)
                files[name] = {
                    'filename': filename,
                    'content': content
                }
            else:
                fields[name] = content.decode('utf-8', errors='ignore')
    return fields, files


def _validated_settings(data):
    """Return only supported, type-safe preference updates."""
    updates = {}
    enums = {
        "camera_bitrate": {"32M", "16M", "8M", "4M"},
        "camera_fps": {"30", "60", "120", "240"},
        "camera_codec": {"h264", "h265"},
        "audio_preset": {"voice_communication", "studio_unprocessed", "camcorder", "output", "mac_mic"},
        "keyboard_mode": {"uhid", "sdk"},
        "device_profile": {"generic", "oneplus"},
    }
    bools = {"mirror_enabled", "screen_off_enabled", "stay_awake_enabled", "show_touches_enabled", "biometric_daemon_enabled"}
    for key, allowed in enums.items():
        if key in data and str(data[key]) in allowed:
            updates[key] = str(data[key])
    for key in bools:
        if key in data and isinstance(data[key], bool):
            updates[key] = data[key]
    for key, low, high in (("audio_buffer", 10, 2000),):
        if key in data:
            try:
                value = int(data[key])
                if low <= value <= high:
                    updates[key] = str(value)
            except (TypeError, ValueError):
                pass
    if "audio_sync_delay" in data:
        try:
            value = float(data["audio_sync_delay"])
            if -10.0 <= value <= 10.0:
                updates["audio_sync_delay"] = f"{value:.2f}"
        except (TypeError, ValueError):
            pass
    for key in ("mac_mic_device",):
        if key in data and isinstance(data[key], str) and 0 < len(data[key]) <= 200:
            updates[key] = data[key]
    for key in ("android_pin", "applock_pin"):
        if key in data and isinstance(data[key], str) and data[key] == "":
            continue
        if key in data and isinstance(data[key], str) and data[key].isdigit() and 4 <= len(data[key]) <= 32:
            updates[key] = data[key]
    return updates


def _get_adb_device_serial(endpoint, timeout=4, fallback_attempts=3):
    """Read the Android identity behind an already-authorized ADB endpoint."""
    # Strategy A: Try to get the real hardware serial number
    for prop in ("ro.serialno", "ro.boot.serialno"):
        try:
            result = subprocess.run(
                ["adb", "-s", endpoint, "shell", "getprop", prop],
                capture_output=True,
                text=True,
                timeout=timeout
            )
            val = (result.stdout or "").strip()
            if result.returncode == 0 and val and val.lower() not in {"unknown", "no permissions", "null", ""}:
                return val
        except Exception:
            pass

    # Strategy B: Fallback to standard get-serialno
    for attempt in range(max(1, int(fallback_attempts))):
        try:
            result = subprocess.run(
                ["adb", "-s", endpoint, "get-serialno"],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            serial = (result.stdout or "").strip()
            if result.returncode == 0 and serial and serial.lower() not in {"unknown", "no permissions", ""}:
                return serial
        except (OSError, subprocess.TimeoutExpired):
            pass
        if attempt + 1 < max(1, int(fallback_attempts)):
            time.sleep(0.2)
    return None


def _saved_wireless_serial(ip):
    try:
        for item in ConnectPhone.load_config().get("saved_devices", []):
            if isinstance(item, dict) and item.get("ip") == ip:
                return item.get("device_serial") or None
    except (TypeError, ValueError, OSError, KeyError):
        pass
    return None


def _accept_auto_wireless_connection(ip, port):
    """Verify a reconnect target before persisting it as the trusted endpoint."""
    endpoint = f"{ip}:{int(port)}"
    expected = _saved_wireless_serial(ip)
    actual = _get_adb_device_serial(endpoint, timeout=1.5, fallback_attempts=1)

    # IP addresses and ADB's endpoint-form get-serialno value are not device
    # identities. Automatic trust requires a previously pinned physical serial
    # and an exact match from Android system properties.
    if not expected or ":" in expected or not actual or ":" in actual or actual != expected:
        subprocess.run(["adb", "disconnect", endpoint], capture_output=True, timeout=5)
        return False, "Wireless device identity could not be verified; pair it explicitly before auto-connecting."

    ConnectPhone.save_wireless_endpoint(ip, int(port), actual)
    return True, actual


def _adb_connect(ip, port, attempts=2, timeout=8):
    """Connect without tearing down a healthy ADB transport first.

    ``adb connect`` is idempotent. Calling ``adb disconnect`` immediately
    before every attempt creates a race with ADB's transport thread and was
    the main source of intermittent reconnects.
    """
    endpoint = f"{ip}:{int(port)}"
    last_output = ""
    for attempt in range(max(1, int(attempts))):
        try:
            result = subprocess.run(
                ["adb", "connect", endpoint],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            last_output = ((result.stdout or "") + " " + (result.stderr or "")).strip()
            lowered = last_output.lower()
            if "connected to" in lowered or "already connected" in lowered:
                ADB_LIFECYCLE.register(endpoint)
                return True, last_output
        except (OSError, subprocess.TimeoutExpired) as exc:
            last_output = str(exc)
        if attempt + 1 < attempts:
            time.sleep(0.35)
    return False, last_output

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


termux_install_state = {
    "status": "idle",
    "message": ""
}

def run_termux_install_background():
    global termux_install_state
    
    termux_install_state["status"] = "downloading"
    termux_install_state["message"] = "Downloading Termux (112 MB)..."
    
    downloads_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools", "downloads")
    os.makedirs(downloads_dir, exist_ok=True)
    
    termux_apk_path = os.path.join(downloads_dir, "termux.apk")
    termux_api_apk_path = os.path.join(downloads_dir, "termux_api.apk")
    
    termux_url = "https://github.com/termux/termux-app/releases/download/v0.118.3/termux-app_v0.118.3+github-debug_universal.apk"
    termux_api_url = "https://github.com/termux/termux-api/releases/download/v0.53.0/termux-api-app_v0.53.0+github.debug.apk"
    
    try:
        if not os.path.exists(termux_apk_path) or os.path.getsize(termux_apk_path) < 10000000:
            urllib.request.urlretrieve(termux_url, termux_apk_path)
            
        termux_install_state["message"] = "Downloading Termux:API (8.6 MB)..."
        if not os.path.exists(termux_api_apk_path) or os.path.getsize(termux_api_apk_path) < 1000000:
            urllib.request.urlretrieve(termux_api_url, termux_api_apk_path)
            
        termux_install_state["status"] = "installing"
        termux_install_state["message"] = "Installing Termux on phone (Accept prompt on screen!)..."
        
        # Install Termux
        res1 = subprocess.run(["adb", "install", termux_apk_path], capture_output=True, text=True)
        if res1.returncode != 0:
            raise Exception(f"Termux install failed: {res1.stderr or res1.stdout}")
            
        termux_install_state["message"] = "Installing Termux:API on phone..."
        res2 = subprocess.run(["adb", "install", termux_api_apk_path], capture_output=True, text=True)
        if res2.returncode != 0:
            raise Exception(f"Termux:API install failed: {res2.stderr or res2.stdout}")
            
        # Post installation configurations
        termux_install_state["message"] = "Configuring permissions and properties..."
        subprocess.run(["adb", "shell", "pm", "grant", "com.termux", "com.termux.permission.RUN_COMMAND"])
        subprocess.run(["adb", "shell", "pm", "grant", "com.termux.api", "android.permission.READ_PHONE_STATE"])
        subprocess.run(["adb", "shell", "pm", "grant", "com.termux.api", "android.permission.ACCESS_FINE_LOCATION"])
        subprocess.run(["adb", "shell", "pm", "grant", "com.termux.api", "android.permission.CAMERA"])
        
        subprocess.run(["adb", "shell", "run-as", "com.termux", "mkdir", "/data/data/com.termux/files/home/.termux"])
        subprocess.run([
            "adb", "shell", "run-as", "com.termux", "sh", "-c",
            "printf '%s\\n' 'allow-external-apps = true' >> /data/user/0/com.termux/files/home/.termux/termux.properties"
        ], check=False)
        subprocess.run(["adb", "shell", "run-as", "com.termux", "/data/data/com.termux/files/usr/bin/termux-reload-settings"])
        
        termux_install_state["status"] = "success"
        termux_install_state["message"] = "Termux + API successfully installed and configured!"
    except Exception as e:
        termux_install_state["status"] = "error"
        termux_install_state["message"] = f"Installation failed: {str(e)}"


# ─── Fast Status Cache ────────────────────────────────────────────────────────
# Background thread keeps this refreshed so /api/status returns instantly.
_status_cache = None
_status_cache_lock = threading.Lock()
_status_cache_event = threading.Event()   # set when a fresh refresh is wanted
_shutdown_event = threading.Event()
_input_permission_cache: dict[object, tuple] = {}
_device_info_cache: dict[object, tuple] = {}
_adb_action_lock = threading.RLock()

def _build_status_payload():
    """Build the full /api/status payload. Called from background thread."""
    global scrcpy_proc, scrcpy_state, sync_watcher_active
    devices_detailed = get_detailed_adb_devices()
    active_device = check_and_autoselect_device(devices_detailed)
    device_connected = len(devices_detailed) > 0 and any(d["status"] == "device" for d in devices_detailed)

    # Device information is fetched off the request threads. Input-injection
    # capability is intentionally sampled infrequently because the probe is
    # itself an Android key event and must not fire every poll cycle.
    device_info = None
    input_injection_granted = True
    transfer_active = TRANSFER_MANAGER.has_active()
    if device_connected and not transfer_active:
        cached_info = _device_info_cache.get(active_device)
        if cached_info and time.monotonic() - cached_info[0] < 15:
            device_info = cached_info[1]
        else:
            device_info = ConnectPhone.get_device_info()
            _device_info_cache[active_device] = (time.monotonic(), device_info)
        cached_permission = _input_permission_cache.get(active_device)
        if cached_permission and time.monotonic() - cached_permission[0] < 120:
            input_injection_granted = cached_permission[1]
        else:
            input_injection_granted = ConnectPhone.check_input_injection_permission()
            _input_permission_cache[active_device] = (time.monotonic(), input_injection_granted)

    mirror_sessions = MIRROR_MANAGER.list()
    scrcpy_running = (scrcpy_proc is not None and scrcpy_proc.poll() is None) or bool(mirror_sessions)
    config = ConnectPhone.load_config()
    fleet = build_fleet(
        devices_detailed,
        [item for item in config.get("saved_devices", []) if isinstance(item, dict)],
        active_transport=active_device,
        selected_identity=str(config.get("selected_device_serial", "")),
        sessions=mirror_sessions,
    )
    public_config = dict(config)
    public_config.pop("android_pin", None)
    public_config.pop("applock_pin", None)
    public_config["android_pin_configured"] = bool(config.get("android_pin"))
    public_config["applock_pin_configured"] = bool(config.get("applock_pin"))
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
        "mirror_sessions": mirror_sessions,
        "fleet": fleet,
        "fleet_limit": MAX_FLEET_DEVICES,
        "input_injection_granted": input_injection_granted,
        "transfer_active": transfer_active,
        "config": public_config,
        "dependencies": {name: bool(shutil.which(name)) for name in ("adb", "scrcpy", "ffmpeg", "ffprobe")},
    }

def _status_cache_worker():
    """Background thread: refresh cache every 1.2 s or immediately on demand."""
    global _status_cache
    while not _shutdown_event.is_set():
        try:
            # Status collection may block on a sleeping/offline phone. It must
            # never hold the foreground action lock and delay a user-requested
            # reconnect by the sum of its ADB timeouts.
            payload = _build_status_payload()
            with _status_cache_lock:
                _status_cache = payload
        except Exception as e:
            print(f"[StatusCache] Error: {e}")
        # ADB device metadata is not volatile enough to justify a 1.2-second
        # command storm. Actions still invalidate the cache immediately.
        _status_cache_event.wait(timeout=3.0)
        _status_cache_event.clear()

def _invalidate_status_cache():
    """Signal the background thread to refresh immediately."""
    _status_cache_event.set()
# ─────────────────────────────────────────────────────────────────────────────



def scan_and_connect_wireless_debug(ip, timeout=0.12, last_known_port=None, allow_port_scan=False):
    import socket
    import asyncio

    def try_connect(port):
        connected, _ = _adb_connect(ip, port, attempts=2)
        return port if connected else None

    async def check_port_async(port):
        try:
            conn = asyncio.open_connection(ip, port)
            reader, writer = await asyncio.wait_for(conn, timeout=timeout)
            writer.close()
            await writer.wait_closed()
            return port
        except Exception:
            return None

    async def scan_ports_async(ports):
        # Keep the scan bounded: creating one task per port can exhaust the
        # UI process before ADB gets a chance to connect.
        found = []
        port_list = list(ports)
        for start in range(0, len(port_list), 256):
            batch = port_list[start:start + 256]
            results = await asyncio.gather(*(check_port_async(p) for p in batch))
            found.extend(p for p in results if p is not None)
            if found:
                break
        return found

    def check_single_port_sync(port_num, timeout_val):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout_val)
            result = s.connect_ex((ip, port_num))
            s.close()
            return result == 0
        except Exception:
            return False

    if last_known_port and last_known_port != 5555:
        if check_single_port_sync(last_known_port, 0.25):
            res = try_connect(last_known_port)
            if res: return res

    if check_single_port_sync(5555, 0.2):
        res = try_connect(5555)
        if res: return res

    # 1. First, try the Zero-Ping mDNS Scanner (Instant Discovery)
    try:
        from core.mdns_scanner import ZeroPingScanner
        mdns_scanner = ZeroPingScanner()
        try:
            devices = mdns_scanner.find_devices_instantly(search_time=2.5)
            for d in devices:
                if d['ip'] == ip or d['ip'].startswith(ip):
                    res = try_connect(d['port'])
                    if res: return res
        finally:
            mdns_scanner.stop()
    except Exception:
        pass


    # Android Wireless Debugging advertises its rotating TLS port over mDNS.
    # A broad 20,000-port scan is both slow and noisy, so only enable it for
    # explicit legacy troubleshooting—not for the normal Connect/Auto path.
    if not allow_port_scan:
        return None
    print(f"[*] Starting legacy port scan on {ip}...")
    found_ports = asyncio.run(scan_ports_async(range(30000, 50000)))
    found_ports.sort()

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
    try:
        from zeroconf import Zeroconf, ServiceBrowser
    except Exception:
        Zeroconf = None
        ServiceBrowser = None
    
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
            
    listener = None
    if Zeroconf and ServiceBrowser:
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
        if zc and listener and listener.ip_address and listener.port:
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
    
    detailed_devices = get_detailed_adb_devices()
    devices = [item["serial"] for item in detailed_devices if item["status"] == "device"]
    if not devices:
        return metrics
        
    metrics["connected"] = True

    active_serial = os.environ.get("ANDROID_SERIAL", "")
    serial = active_serial if active_serial in devices else devices[0]

    def metric_shell(*args):
        try:
            return subprocess.run(
                ["adb", "-s", serial, "shell", *args],
                capture_output=True,
                text=True,
                timeout=4,
            )
        except (OSError, subprocess.TimeoutExpired):
            return subprocess.CompletedProcess(args, 124, "", "metric command timed out")
    
    # 1. Query battery
    res_bat = metric_shell("dumpsys battery")
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
    res_ram = metric_shell("cat /proc/meminfo")
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
    res_store = metric_shell("df -k /data")
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
    res_route = metric_shell("ip route")
    for line in res_route.stdout.splitlines():
        if "src" in line:
            parts = line.split()
            try:
                idx = parts.index("src")
                ip = parts[idx + 1]
                break
            except Exception:
                pass
                
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
    res_uptime = metric_shell("uptime")
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
        res = subprocess.run(["adb", "devices", "-l"], capture_output=True, text=True, timeout=5)
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
        # Keep every serial visible. Two phones can share the same model and
        # product; deduplicating by hardware name hides a real target device.
        return devices_list
    except Exception:
        return []

def check_and_autoselect_device(devices_detailed):
    online_serials = [d["serial"] for d in devices_detailed if d["status"] == "device"]
    # Selection is identity-based, so reconnecting several trusted phones does
    # not make the last successful background connection steal the dashboard.
    try:
        config = ConnectPhone.load_config()
        selected_identity = str(config.get("selected_device_serial", "")).strip()
        if selected_identity:
            selected_candidates = [selected_identity]
            selected_candidates.extend(
                f"{item.get('ip')}:{item.get('port')}"
                for item in config.get("saved_devices", [])
                if isinstance(item, dict) and item.get("device_serial") == selected_identity
            )
            selected = next((item for item in selected_candidates if item in online_serials), "")
            if selected:
                os.environ["ANDROID_SERIAL"] = selected
                return selected
        last_ip = str(config.get("last_ip", "")).strip()
        last_port = config.get("last_port")
        preferred_wireless = f"{last_ip}:{int(last_port)}" if last_ip and _valid_port(last_port) else ""
        if preferred_wireless in online_serials:
            os.environ["ANDROID_SERIAL"] = preferred_wireless
            return preferred_wireless
    except (TypeError, ValueError, OSError):
        pass
    active = os.environ.get("ANDROID_SERIAL", "")
    if active and active in online_serials:
        return active
    if online_serials:
        wireless = next((serial for serial in online_serials if ":" in serial), "")
        selected = wireless or online_serials[0]
        os.environ["ANDROID_SERIAL"] = selected
        return selected
    all_serials = [d["serial"] for d in devices_detailed]
    if all_serials:
        if active and active in all_serials:
            return active
        os.environ["ANDROID_SERIAL"] = all_serials[0]
        return all_serials[0]
    os.environ.pop("ANDROID_SERIAL", None)
    return ""

def discover_all_mdns_services(timeout=2.0, target_ip=None):
    try:
        from core.mdns_scanner import ZeroPingScanner
        scanner = ZeroPingScanner(include_pairing=True)
        try:
            services = scanner.find_devices_instantly(search_time=timeout)
        finally:
            scanner.stop()
        services = [item for item in services if not target_ip or item.get("ip") == target_ip]
        if services:
            return services
    except Exception as exc:
        print(f"[mDNS] Primary Bonjour discovery failed: {exc}")
    try:
        from zeroconf import Zeroconf, ServiceBrowser
    except Exception:
        Zeroconf = None
        ServiceBrowser = None
    
    discovered = []
    seen = set()
    
    class MultiListener:
        def _add(self, zc, type, name):
            try:
                info = zc.get_service_info(type, name)
                if info:
                    addresses = info.parsed_addresses()
                    ipv4_addrs = [addr for addr in addresses if '.' in addr]
                    ip = ipv4_addrs[0] if ipv4_addrs else (addresses[0] if addresses else "unknown")
                    if target_ip and ip != target_ip:
                        return
                    clean_name = name.split(".")[0]
                    item = {
                        "name": clean_name,
                        "ip": ip,
                        "port": info.port,
                        "type": "connect" if "connect" in type else "pairing"
                    }
                    key = (item["ip"], int(item["port"]), item["type"])
                    if key not in seen:
                        seen.add(key)
                        discovered.append(item)
            except Exception:
                pass

        def add_service(self, zc, type, name):
            self._add(zc, type, name)
        def remove_service(self, zc, type, name):
            pass
        def update_service(self, zc, type, name):
            self._add(zc, type, name)

    # Start Zeroconf browser for both connect and pairing service types
    zc = None
    if Zeroconf and ServiceBrowser:
        try:
            zc = Zeroconf()
            listener = MultiListener()
            b1 = ServiceBrowser(zc, "_adb-tls-connect._tcp.local.", listener)
            b2 = ServiceBrowser(zc, "_adb-tls-pairing._tcp.local.", listener)
            # Bonjour callbacks normally arrive within a few hundred ms.
            # Keep a short settling window for phones that wake the service
            # lazily, without making every scan wait several seconds.
            time.sleep(min(max(0.8, float(timeout)), 1.5))
        except Exception:
            pass
        finally:
            if zc:
                zc.close()

    # Native macOS fallback: use dns-sd hybrid resolver if Zeroconf path
    # is unavailable or did not discover targets in the time window.
    if not discovered:
        for service_type, service_name, kind in (
            ("_adb-tls-connect._tcp.local.", "adb-connect", "connect"),
            ("_adb-tls-pairing._tcp.local.", "adb-pairing", "pairing"),
        ):
            ip, port = discover_adb_service_hybrid(
                service_type,
                target_substring=None,
                target_ip=target_ip,
                timeout=max(1.0, float(timeout)),
            )
            if ip and port:
                item = {"name": service_name, "ip": ip, "port": int(port), "type": kind}
                key = (item["ip"], item["port"], item["type"])
                if key not in seen:
                    seen.add(key)
                    discovered.append(item)
            
    return discovered


def pair_and_connect_wireless(ip, port, code):
    """Complete one secure pairing attempt, then resolve the connect service."""
    endpoint = f"{ip}:{int(port)}"
    success, detail = pair_with_code(endpoint, code)
    if not success:
        return {
            "success": False,
            "message": (
                f"Pairing failed: {detail}\n\n"
                "Keep the pairing-code dialog open on the phone and submit its current "
                "six-digit code and pairing port. Codes expire quickly; request a new code "
                "after any failed attempt."
            ),
        }

    return _connect_after_pairing(ip)


def _connect_after_pairing(ip):
    """Resolve Android's distinct TLS connect service after either pairing mode."""
    deadline = time.monotonic() + 6.0
    attempted = set()
    last_error = ""
    while time.monotonic() < deadline:
        services = discover_all_mdns_services(timeout=1.2, target_ip=ip)
        connect_ports = [
            int(item["port"])
            for item in services
            if item.get("type") == "connect" and _valid_port(item.get("port"))
        ]
        for connect_port in connect_ports:
            if connect_port in attempted:
                continue
            attempted.add(connect_port)
            connected, last_error = _adb_connect(ip, connect_port, attempts=2)
            if not connected:
                continue
            wireless_endpoint = f"{ip}:{connect_port}"
            serial = _get_adb_device_serial(wireless_endpoint)
            if not serial or ":" in serial:
                subprocess.run(["adb", "disconnect", wireless_endpoint], capture_output=True, timeout=5)
                last_error = "Android hardware identity could not be verified"
                continue
            os.environ["ANDROID_SERIAL"] = wireless_endpoint
            ConnectPhone.save_wireless_endpoint(ip, connect_port, serial)
            _invalidate_status_cache()
            return {"success": True, "message": f"Paired and connected securely to {wireless_endpoint}."}
        time.sleep(0.3)
    return {
        "success": True,
        "message": (
            "Pairing succeeded, but the phone has not advertised its connect service yet. "
            "Keep Wireless Debugging enabled and click Scan Network, then Connect."
            + (f" Last connection error: {last_error}" if last_error else "")
        ),
    }


def connect_previously_authorized_devices():
    """Fast-connect a saved phone, falling back to authorized mDNS targets."""
    candidates = []
    seen = set()

    # The endpoint persisted after the last successful identity check is the
    # overwhelmingly common path. Trying it before Bonjour removes seconds of
    # avoidable discovery latency and still re-verifies the physical serial.
    config = ConnectPhone.load_config()
    saved = [item for item in config.get("saved_devices", []) if isinstance(item, dict)]
    saved.sort(key=lambda item: item.get("ip") != config.get("last_ip"))
    for item in saved:
        ip, port = str(item.get("ip", "")).strip(), item.get("port")
        serial = str(item.get("device_serial", "")).strip()
        if _valid_ipv4(ip) and _valid_port(port) and serial and ":" not in serial:
            endpoint = (ip, int(port), serial)
            if endpoint[:2] not in seen:
                seen.add(endpoint[:2])
                candidates.append(endpoint)

    for ip, port, expected_serial in candidates:
        endpoint = f"{ip}:{port}"
        accepted, _detail = _adb_connect(ip, port, attempts=1, timeout=8)
        if not accepted:
            continue
        serial = _get_adb_device_serial(endpoint, timeout=1.5, fallback_attempts=1)
        if serial == expected_serial:
            os.environ["ANDROID_SERIAL"] = endpoint
            ConnectPhone.save_wireless_endpoint(ip, port, serial)
            _publish_connected_endpoint(endpoint, serial)
            return {
                "success": True,
                "message": f"Connected instantly to {endpoint}.",
                "devices": [{"endpoint": endpoint, "serial": serial}],
                "authorization_required": 0,
                "fast_path": True,
            }
        subprocess.run(["adb", "disconnect", endpoint], capture_output=True, timeout=3)

    # Saved ports rotate. Only pay the Bonjour cost after the direct path
    # fails, and keep the scan window short because the scanner retains live
    # service snapshots.
    services = discover_all_mdns_services(timeout=0.8)
    candidates = []
    for item in services:
        if item.get("type") != "connect":
            continue
        ip, port = str(item.get("ip", "")).strip(), item.get("port")
        if not _valid_ipv4(ip) or not _valid_port(port):
            continue
        endpoint = (ip, int(port))
        if endpoint not in seen:
            seen.add(endpoint)
            candidates.append(endpoint)

    connected = []
    authorization_required = 0
    for ip, port in candidates:
        endpoint = f"{ip}:{port}"
        accepted, _detail = _adb_connect(ip, port, attempts=1, timeout=3)
        if not accepted:
            authorization_required += 1
            continue
        serial = _get_adb_device_serial(endpoint, timeout=1.5, fallback_attempts=1)
        if not serial or ":" in serial:
            subprocess.run(["adb", "disconnect", endpoint], capture_output=True, timeout=5)
            authorization_required += 1
            continue
        ConnectPhone.save_wireless_endpoint(ip, port, serial)
        connected.append({"endpoint": endpoint, "serial": serial})

    if connected:
        os.environ["ANDROID_SERIAL"] = connected[0]["endpoint"]
        _publish_connected_endpoint(connected[0]["endpoint"], connected[0]["serial"])
        return {
            "success": True,
            "message": f"Connected {len(connected)} previously authorized device(s).",
            "devices": connected,
            "authorization_required": authorization_required,
        }
    return {
        "success": False,
        "message": (
            "No previously authorized phones accepted this Mac's ADB key. "
            "Turn on Wireless Debugging; a new phone requires one initial pairing authorization."
        ),
        "devices": [],
        "authorization_required": authorization_required,
    }


def connect_all_trusted_devices():
    """Reconnect every enrolled phone while preserving the selected target."""
    config = ConnectPhone.load_config()
    saved = [
        dict(item) for item in config.get("saved_devices", [])
        if isinstance(item, dict) and item.get("auto_reconnect", True)
        and item.get("device_serial") and _valid_ipv4(str(item.get("ip", "")))
        and _valid_port(item.get("port"))
    ][:MAX_FLEET_DEVICES]
    try:
        adb_result = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=3)
        online = {
            parts[0] for line in (adb_result.stdout or "").splitlines()[1:]
            if len(parts := line.split()) >= 2 and parts[1] == "device"
        }
    except (OSError, subprocess.TimeoutExpired):
        online = set()

    connected = []
    unresolved = []
    for item in saved:
        ip, port = str(item["ip"]), int(item["port"])
        identity = str(item["device_serial"])
        endpoint = f"{ip}:{port}"
        if endpoint in online or identity in online:
            connected.append({"endpoint": endpoint if endpoint in online else identity, "serial": identity})
            continue
        accepted, _ = _adb_connect(ip, port, attempts=1, timeout=4)
        actual = _get_adb_device_serial(endpoint, timeout=1.5, fallback_attempts=1) if accepted else None
        if actual == identity:
            ConnectPhone.global_config_mgr.update_device_endpoint(ip, port, identity)
            connected.append({"endpoint": endpoint, "serial": identity})
        else:
            if accepted:
                subprocess.run(["adb", "disconnect", endpoint], capture_output=True, timeout=3)
            unresolved.append(item)

    if unresolved:
        services = discover_all_mdns_services(timeout=1.0)
        by_ip = {}
        for service in services:
            if service.get("type") == "connect" and _valid_port(service.get("port")):
                by_ip.setdefault(str(service.get("ip")), []).append(int(service["port"]))
        for item in unresolved:
            ip, identity = str(item["ip"]), str(item["device_serial"])
            for port in by_ip.get(ip, []):
                endpoint = f"{ip}:{port}"
                accepted, _ = _adb_connect(ip, port, attempts=1, timeout=3)
                actual = _get_adb_device_serial(endpoint, timeout=1.5, fallback_attempts=1) if accepted else None
                if actual == identity:
                    ConnectPhone.global_config_mgr.update_device_endpoint(ip, port, identity)
                    connected.append({"endpoint": endpoint, "serial": identity})
                    break
                if accepted:
                    subprocess.run(["adb", "disconnect", endpoint], capture_output=True, timeout=3)

    _invalidate_status_cache()
    return {
        "success": bool(connected),
        "message": f"Connected {len(connected)} of {len(saved)} trusted phone(s).",
        "devices": connected,
        "total": len(saved),
    }


def _publish_connected_endpoint(endpoint, physical_serial):
    """Make a successful foreground connection visible without a cache wait."""
    global _status_cache
    with _status_cache_lock:
        if not isinstance(_status_cache, dict):
            _invalidate_status_cache()
            return
        payload = dict(_status_cache)
        details = [dict(item) for item in payload.get("devices_detailed", []) if item.get("serial") != endpoint]
        details.append({
            "serial": endpoint,
            "status": "device",
            "type": "wireless",
            "model": next((item.get("model") for item in details if item.get("serial") == physical_serial), "Android Device"),
            "product": "wireless",
        })
        payload.update(
            connected=True,
            active_device=endpoint,
            devices=[item["serial"] for item in details if item.get("status") == "device"],
            devices_detailed=details,
        )
        _status_cache = payload
    _invalidate_status_cache()


_foreground_connect_lock = threading.Lock()
_foreground_connect_thread = None
_foreground_connect_result = None


def _saved_online_endpoint():
    try:
        result = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=1)
        active = {
            line.split()[0] for line in (result.stdout or "").splitlines()[1:]
            if len(line.split()) >= 2 and line.split()[1] == "device"
        }
        config = ConnectPhone.load_config()
        for item in config.get("saved_devices", []):
            if not isinstance(item, dict):
                continue
            endpoint = f"{item.get('ip')}:{item.get('port')}"
            serial = item.get("device_serial")
            if endpoint in active and serial:
                return endpoint, serial
    except (OSError, subprocess.TimeoutExpired, TypeError, ValueError):
        pass
    return None


def _foreground_connect_worker():
    global _foreground_connect_result
    result = connect_all_trusted_devices()
    with _foreground_connect_lock:
        _foreground_connect_result = result


def start_foreground_connect():
    """Acknowledge immediately and reconnect all trusted phones in background."""
    global _foreground_connect_thread, _foreground_connect_result
    with _foreground_connect_lock:
        if _foreground_connect_thread and _foreground_connect_thread.is_alive():
            return {"success": True, "pending": True, "message": "Connection is already in progress…"}
        _foreground_connect_result = None
        _foreground_connect_thread = threading.Thread(
            target=_foreground_connect_worker,
            name="ConnectPhone-ForegroundConnect",
            daemon=True,
        )
        _foreground_connect_thread.start()
    return {
        "success": True,
        "pending": True,
        "message": "Connecting all trusted phones now…",
    }


_qr_pairing_sessions: dict[str, dict] = {}
_qr_pairing_lock = threading.RLock()


def _public_qr_session(session):
    return {
        "status": session["status"],
        "message": session["message"],
        "expires_at": session["expires_at"],
    }


def _qr_pairing_worker(session_id):
    with _qr_pairing_lock:
        session = _qr_pairing_sessions.get(session_id)
        if not session:
            return
        service_name = session["service_name"]
        password = session["password"]
        expires_at = session["expires_at"]

    while time.time() < expires_at:
        services = discover_all_mdns_services(timeout=1.2)
        match = next(
            (
                item for item in services
                if item.get("type") == "pairing"
                and str(item.get("name", "")).split(".", 1)[0] == service_name
                and _valid_ipv4(str(item.get("ip", "")))
                and _valid_port(item.get("port"))
            ),
            None,
        )
        if not match:
            time.sleep(0.25)
            continue

        endpoint = f"{match['ip']}:{int(match['port'])}"
        success, detail = pair_with_secret(endpoint, password, timeout=15)
        if success:
            result = _connect_after_pairing(str(match["ip"]))
            status = "success" if result.get("success") else "error"
            message = result.get("message", "QR pairing completed.")
        else:
            status = "error"
            message = f"QR pairing failed: {detail}"
        with _qr_pairing_lock:
            current = _qr_pairing_sessions.get(session_id)
            if current:
                current.update(status=status, message=message, password=None)
        return

    with _qr_pairing_lock:
        current = _qr_pairing_sessions.get(session_id)
        if current:
            current.update(
                status="expired",
                message="QR code expired. Generate a new code and scan it again.",
                password=None,
            )


def start_qr_pairing():
    now = time.time()
    with _qr_pairing_lock:
        for key in list(_qr_pairing_sessions):
            if _qr_pairing_sessions[key]["expires_at"] + 30 < now:
                del _qr_pairing_sessions[key]
        if sum(1 for item in _qr_pairing_sessions.values() if item["status"] == "waiting") >= 2:
            return {"success": False, "message": "Two QR pairing sessions are already active."}

        service_name, password, payload = new_qr_credentials()
        session_id = secrets.token_urlsafe(18)
        expires_at = now + 90
        _qr_pairing_sessions[session_id] = {
            "service_name": service_name,
            "password": password,
            "status": "waiting",
            "message": "Waiting for a phone to scan this QR code…",
            "expires_at": expires_at,
        }
        qr_url = svg_data_url(payload)

    threading.Thread(target=_qr_pairing_worker, args=(session_id,), daemon=True).start()
    return {
        "success": True,
        "session_id": session_id,
        "qr_image": qr_url,
        "expires_at": expires_at,
    }







class ConnectPhoneUIHandler(http.server.BaseHTTPRequestHandler):
    # Suppress verbose log messages on terminal for clean output
    def log_message(self, format, *args):
        # The WebView polls this cached endpoint frequently; successful polls
        # add no diagnostic value and otherwise rotate logs every few days.
        rendered = format % args
        if '"GET /api/status ' in rendered and '" 200 ' in rendered:
            return
        message = re.sub(r"([?&]token=)[^&\s]+", r"\1[REDACTED]", rendered)
        sys.stdout.write(f"[UI Server] {message}\n")
        sys.stdout.flush()

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Permissions-Policy", "camera=(), geolocation=(), microphone=(self)")
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' blob: data:; "
            "style-src 'self' 'unsafe-inline'; font-src 'self'; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        )
        super().end_headers()

    def do_OPTIONS(self):
        if not _origin_allowed(self.headers.get("Origin")):
            self.send_error(403, "Cross-origin requests are not allowed")
            return
        self.send_response(200)
        origin = self.headers.get('Origin')
        if origin:
            self.send_header('Access-Control-Allow-Origin', origin)
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-ConnectPhone-Token')
        self.end_headers()

    def do_GET(self):
        if not _origin_allowed(self.headers.get("Origin")):
            self.send_error(403, "Cross-origin requests are not allowed")
            return
        if self.path.startswith('/api/') and not _token_allowed(self):
            self.send_error(401, "Authentication required")
            return
        if self.path.startswith('/api/'):
            if self.path.split("?", 1)[0] == "/api/status":
                self.handle_api_get()
            else:
                with _adb_action_lock:
                    self.handle_api_get()
        else:
            self.serve_static_files()

    def do_POST(self):
        if not _origin_allowed(self.headers.get("Origin")):
            self.send_error(403, "Cross-origin requests are not allowed")
            return
        if self.path.startswith('/api/') and not _token_allowed(self):
            self.send_error(401, "Authentication required")
            return
        if self.path.startswith('/api/'):
            with _adb_action_lock:
                self.handle_api_post()
        else:
            self.send_error(404, "Not Found")

    def serve_static_files(self):
        path = self.path.split('?')[0]
        if path == '/':
            path = '/index.html'
        
        ui_root = os.path.realpath(os.path.join(PROJECT_DIR, 'ui'))
        file_path = os.path.realpath(os.path.join(ui_root, path.lstrip('/')))
        if os.path.commonpath((ui_root, file_path)) != ui_root:
            self.send_error(404, "File Not Found")
            return
        
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
            origin = self.headers.get('Origin')
            if origin:
                self.send_header('Access-Control-Allow-Origin', origin)
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, f"Internal Server Error: {e}")

    def handle_api_get(self):
        global _status_cache
        retired = {
            "/api/termux/install/status",
            "/api/screenshots/list",
            "/api/clipboard/sync/status",
        }
        if self.path in retired:
            self.send_response(410)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"error": "This feature has been retired from ConnectPhone."}).encode('utf-8'))
            return
        parsed_path = urlsplit(self.path)
        if parsed_path.path == '/api/storage/list':
            target_path = str(parse_qs(parsed_path.query).get("path", ["/sdcard"])[0]).strip() or "/sdcard"
            if not _valid_remote_path(target_path):
                self.send_error(400, "Invalid directory path")
                return

        known_get_paths = {
            "/api/status", "/api/metrics", "/api/settings/audio_devices",
            "/api/mdns/discover", "/api/pair/qr/status", "/api/storage/list",
            "/api/storage/download", "/api/files/roots", "/api/files/local",
            "/api/files/storages", "/api/transfers",
        }
        if parsed_path.path not in known_get_paths:
            self.send_error(404, "Unknown GET endpoint")
            return

        if not self.path.startswith('/api/storage/download'):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()

        if self.path == '/api/status':
            # Serve from cache (refreshed every ~3 s in background) — sub-millisecond response
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
        elif self.path.startswith('/api/mdns/discover'):
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            requested_ip = str((query.get("ip", [""])[0] or "")).strip()
            if requested_ip and not _valid_ipv4(requested_ip):
                requested_ip = None
            discovered = discover_all_mdns_services(target_ip=requested_ip or None)
            self.wfile.write(json.dumps({"success": True, "services": discovered}).encode('utf-8'))

        elif self.path.startswith('/api/pair/qr/status'):
            session_id = str(parse_qs(urlsplit(self.path).query).get("id", [""])[0])
            with _qr_pairing_lock:
                session = _qr_pairing_sessions.get(session_id)
                response = _public_qr_session(session) if session else None
            if response is None:
                self.wfile.write(json.dumps({"success": False, "message": "QR pairing session not found."}).encode('utf-8'))
            else:
                self.wfile.write(json.dumps({"success": True, **response}).encode('utf-8'))

        elif parsed_path.path == '/api/files/roots':
            self.wfile.write(json.dumps({"success": True, "roots": local_roots()}).encode('utf-8'))

        elif parsed_path.path == '/api/files/local':
            query = parse_qs(parsed_path.query)
            requested = str(query.get("path", [str(pathlib.Path.home())])[0]).strip()
            show_hidden = str(query.get("show_hidden", ["0"])[0]).lower() in {"1", "true", "yes"}
            try:
                listing = list_local_files(requested, show_hidden=show_hidden)
                self.wfile.write(json.dumps({"success": True, **listing}).encode('utf-8'))
            except (OSError, ValueError) as exc:
                self.wfile.write(json.dumps({"success": False, "message": str(exc)}).encode('utf-8'))

        elif parsed_path.path == '/api/files/storages':
            try:
                self.wfile.write(json.dumps({"success": True, "storages": list_phone_storages()}).encode('utf-8'))
            except (OSError, subprocess.TimeoutExpired) as exc:
                self.wfile.write(json.dumps({"success": False, "message": str(exc), "storages": []}).encode('utf-8'))

        elif parsed_path.path == '/api/transfers':
            self.wfile.write(json.dumps({"success": True, "jobs": TRANSFER_MANAGER.list()}).encode('utf-8'))

        elif self.path == '/api/screenshots/list':
            try:
                # Find screenshots from common paths and sort by newest first
                cmd = "ls -t /sdcard/DCIM/Screenshots/* /sdcard/Pictures/Screenshots/* 2>/dev/null"
                res = run_adb_cmd_with_retry(["adb", "shell", "sh", "-c", cmd], timeout=10)
                # Even if one directory doesn't exist (exit code 1), the other might succeed and print to stdout
                out = res.stdout or ""
                lines = [line.strip() for line in out.split('\n') if line.strip()]
                latest = lines[:10]
                self.wfile.write(json.dumps({"success": True, "files": latest}).encode('utf-8'))
            except Exception as e:
                self.wfile.write(json.dumps({"success": False, "files": [], "error": str(e)}).encode('utf-8'))
        elif self.path.startswith('/api/storage/list'):
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            target_path = str(query.get("path", ["/sdcard"])[0]).strip()
            if not target_path:
                target_path = "/sdcard"
            show_hidden = str(query.get("show_hidden", ["0"])[0]).lower() in {"1", "true", "yes"}
            if not _valid_remote_path(target_path):
                self.wfile.write(json.dumps({"success": False, "message": "Invalid directory path"}).encode('utf-8'))
                return
            try:
                cmd = f'find -L {shlex.quote(target_path)} -mindepth 1 -maxdepth 1 -exec stat -L -c "%F|%s|%Y|%n" {{}} +'
                res = run_adb_cmd_with_retry(["adb", "shell", cmd], timeout=15)
                files = []
                lines = (res.stdout or "").split('\n')
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split('|', 3)
                    if len(parts) < 4:
                        continue
                    file_type, size_str, mtime_str, full_path = parts
                    is_dir = 'directory' in file_type.lower()
                    try:
                        size = int(size_str)
                    except ValueError:
                        size = 0
                    try:
                        mtime = int(mtime_str)
                    except ValueError:
                        mtime = 0
                    name = os.path.basename(full_path)
                    if name in ('.', '..'):
                        continue
                    if not show_hidden and name.startswith('.'):
                        continue
                    files.append({
                        "name": name,
                        "path": full_path,
                        "is_dir": is_dir,
                        "size": size,
                        "mtime": mtime
                    })
                files.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
                self.wfile.write(json.dumps({"success": True, "path": target_path, "files": files}).encode('utf-8'))
            except Exception as e:
                self.wfile.write(json.dumps({"success": False, "message": str(e)}).encode('utf-8'))
        elif self.path.startswith('/api/storage/download'):
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            remote_path = str(query.get("path", [""])[0]).strip()
            if not _valid_remote_path(remote_path):
                self.send_error(400, "Invalid remote path")
                return
                
            try:
                # Check if it is a directory on the phone
                check_dir = subprocess.run(["adb", "shell", "test", "-d", remote_path], capture_output=True, timeout=5)
                is_directory = (check_dir.returncode == 0)
                
                if is_directory:
                    filename = _safe_download_name(remote_path, "phone-folder") + ".zip"
                    # Pull folder and zip it
                    with tempfile.TemporaryDirectory() as tmp_dir:
                        pull_dest = os.path.join(tmp_dir, os.path.basename(remote_path.rstrip('/')))
                        res = subprocess.run(["adb", "pull", remote_path, pull_dest], capture_output=True, text=True, timeout=120)
                        if res.returncode != 0:
                            self.send_error(500, f"Failed to pull folder: {res.stderr}")
                            return
                        
                        import shutil
                        zip_base = tempfile.mktemp()
                        zip_file_path = shutil.make_archive(zip_base, 'zip', tmp_dir)
                        
                        try:
                            file_size = os.path.getsize(zip_file_path)
                            self.send_response(200)
                            self.send_header('Content-Type', 'application/zip')
                            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
                            self.send_header('Content-Length', str(file_size))
                            self.end_headers()
                            with open(zip_file_path, 'rb') as f:
                                while True:
                                    chunk = f.read(64 * 1024)
                                    if not chunk:
                                        break
                                    self.wfile.write(chunk)
                        finally:
                            if os.path.exists(zip_file_path):
                                try:
                                    os.remove(zip_file_path)
                                except Exception:
                                    pass
                else:
                    filename = _safe_download_name(remote_path)
                    with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
                        tmp_path = tmp_file.name
                    try:
                        res = subprocess.run(["adb", "pull", remote_path, tmp_path], capture_output=True, text=True, timeout=30)
                        if res.returncode != 0:
                            self.send_error(500, f"Failed to pull file: {res.stderr}")
                            return
                        file_size = os.path.getsize(tmp_path)
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/octet-stream')
                        self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
                        self.send_header('Content-Length', str(file_size))
                        self.end_headers()
                        with open(tmp_path, 'rb') as f:
                            while True:
                                chunk = f.read(64 * 1024)
                                if not chunk:
                                    break
                                self.wfile.write(chunk)
                    finally:
                        if os.path.exists(tmp_path):
                            try:
                                os.remove(tmp_path)
                            except Exception:
                                pass
            except Exception as e:
                self.send_error(500, str(e))
        else:
            self.wfile.write(json.dumps({"error": "Unknown GET endpoint"}).encode('utf-8'))

    def handle_api_post(self):
        global scrcpy_proc, scrcpy_state, sync_watcher_thread, sync_watcher_active, scrcpy_clipboard_proc, morse_state

        if self.path == '/api/storage/upload':
            try:
                try:
                    upload_length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    upload_length = -1
                if upload_length <= 0 or upload_length > 64 * 1024 * 1024:
                    self.send_error(413, "Upload must be between 1 byte and 64 MiB")
                    return
                fields, files = parse_multipart(self.rfile, self.headers)
                if not fields or not files or 'file' not in files:
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": False, "message": "Missing file or upload path."}).encode('utf-8'))
                    return
                
                remote_dir = fields.get("path", "/sdcard/Download").strip()
                if not _valid_remote_path(remote_dir):
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": False, "message": "Invalid upload directory."}).encode('utf-8'))
                    return
                
                uploaded_file = files['file']
                filename = uploaded_file['filename']
                
                # Check for explicit relative path from folder drops
                rel_path = fields.get("relativePath", "").strip()
                is_folder_file = False
                if rel_path:
                    # Clean the path
                    rel_path = posixpath.normpath(rel_path.replace('\\', '/'))
                    if rel_path.startswith("/") or rel_path == ".." or rel_path.startswith("../") or any(ord(c) < 32 for c in rel_path):
                        self.send_response(400)
                        self.send_header('Content-Type', 'application/json')
                        self.end_headers()
                        self.wfile.write(json.dumps({"success": False, "message": "Invalid relative path."}).encode('utf-8'))
                        return
                    filename = rel_path
                    is_folder_file = True
                else:
                    if not filename or '/' in filename or '\\' in filename or any(ord(c) < 32 for c in filename):
                        self.send_response(400)
                        self.send_header('Content-Type', 'application/json')
                        self.end_headers()
                        self.wfile.write(json.dumps({"success": False, "message": "Invalid filename."}).encode('utf-8'))
                        return
                
                with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
                    tmp_path = tmp_file.name
                    tmp_file.write(uploaded_file['content'])
                    
                try:
                    remote_dest = posixpath.join(remote_dir, filename)
                    remote_parent = posixpath.dirname(remote_dest)
                    if not _valid_remote_path(remote_dest):
                        raise ValueError("Invalid upload destination")
                    
                    # Create parent directories recursively on phone first!
                    subprocess.run(["adb", "shell", "mkdir", "-p", remote_parent], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    
                    res = subprocess.run(["adb", "push", tmp_path, remote_dest], capture_output=True, text=True, timeout=60)
                    if res.returncode == 0:
                        # 1. Trigger MediaScanner so the phone indices register the new file instantly!
                        subprocess.run(["adb", "shell", "am", "broadcast", "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE", "-d", f"file://{remote_dest}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/json')
                        self.end_headers()
                        self.wfile.write(json.dumps({"success": True, "message": f"Uploaded {filename} successfully."}).encode('utf-8'))
                    else:
                        self.send_response(500)
                        self.send_header('Content-Type', 'application/json')
                        self.end_headers()
                        self.wfile.write(json.dumps({"success": False, "message": f"ADB Push failed: {res.stderr.strip()}"}).encode('utf-8'))
                finally:
                    if os.path.exists(tmp_path):
                        try:
                            os.remove(tmp_path)
                        except Exception:
                            pass
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "message": str(e)}).encode('utf-8'))
            return

        elif self.path == '/api/storage/download_zip':
            try:
                content_length_header = self.headers.get('Content-Length')
                content_length = int(content_length_header) if content_length_header is not None else 0
                if content_length > 0:
                    body = self.rfile.read(content_length).decode('utf-8')
                    data = json.loads(body)
                else:
                    data = {}
            except Exception as e:
                self.send_error(400, f"Invalid JSON: {str(e)}")
                return

            paths = data.get("paths", [])
            if not paths:
                self.send_error(400, "No paths provided")
                return

            # Sanitize paths
            for p in paths:
                if not _valid_remote_path(p):
                    self.send_error(400, f"Invalid path detected: {p}")
                    return

            import zipfile
            with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp_file:
                zip_path = tmp_file.name

            try:
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zip_f:
                    for p in paths:
                        with tempfile.TemporaryDirectory() as temp_dir:
                            name = os.path.basename(p.rstrip('/'))
                            dest = os.path.join(temp_dir, name)
                            res = subprocess.run(["adb", "pull", p, dest], capture_output=True, text=True, timeout=120)
                            if res.returncode == 0:
                                if os.path.isdir(dest):
                                    for root, _, files in os.walk(dest):
                                        for file in files:
                                            filepath = os.path.join(root, file)
                                            arcname = os.path.relpath(filepath, temp_dir)
                                            zip_f.write(filepath, arcname)
                                else:
                                    zip_f.write(dest, name)

                file_size = os.path.getsize(zip_path)
                self.send_response(200)
                self.send_header('Content-Type', 'application/zip')
                self.send_header('Content-Disposition', 'attachment; filename="phone_files.zip"')
                self.send_header('Content-Length', str(file_size))
                
                # CORS headers
                origin = self.headers.get('Origin')
                if origin:
                    self.send_header('Access-Control-Allow-Origin', origin)
                self.end_headers()

                with open(zip_path, 'rb') as f:
                    while True:
                        chunk = f.read(64 * 1024)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
            except Exception as e:
                self.send_error(500, f"Error building zip: {str(e)}")
            finally:
                if os.path.exists(zip_path):
                    try:
                        os.unlink(zip_path)
                    except Exception:
                        pass
            return

        retired = {
            "/api/action/ocr",
            "/api/action/ai-click",
            "/api/screenshots/pull",
            "/api/clipboard/sync/start",
            "/api/clipboard/sync/status",
            "/api/clipboard/sync/stop",
            "/api/clipboard/type",
            "/api/termux/execute",
            "/api/termux/install",
            "/api/termux/tts",
            "/api/termux/sensors",
            "/api/termux/scan",
        }
        if self.path in retired:
            self.send_response(410)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"success": False, "message": "This feature has been retired from ConnectPhone."}).encode('utf-8'))
            return
        
        content_length_header = self.headers.get('Content-Length')
        try:
            content_length = int(content_length_header) if content_length_header is not None else 0
        except ValueError:
            self.send_error(400, "Invalid Content-Length")
            return
        if content_length < 0 or content_length > MAX_REQUEST_BODY:
            self.send_error(413, "Request body too large")
            return
        post_data = self.rfile.read(content_length) if content_length > 0 else b""
        
        data = {}
        if post_data:
            try:
                data = json.loads(post_data.decode('utf-8'))
            except Exception:
                self.send_error(400, "Request body must be valid JSON")
                return

        if self.path == '/api/storage/delete':
            if not _valid_remote_path(str(data.get("path", "")).strip(), destructive=True):
                self.send_error(400, "Invalid remote path")
                return
        if self.path == '/api/storage/delete_multiple':
            paths = data.get("paths")
            if not isinstance(paths, list) or not paths or any(
                not _valid_remote_path(path, destructive=True) for path in paths
            ):
                self.send_error(400, "Invalid remote path list")
                return
                
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        origin = self.headers.get('Origin')
        if origin:
            self.send_header('Access-Control-Allow-Origin', origin)
        self.end_headers()
        
        res_data = {"success": False, "message": ""}
        
        try:
            if self.path == '/api/transfers/start':
                try:
                    job = TRANSFER_MANAGER.start(
                        direction=str(data.get("direction", "")),
                        items=data.get("items", []),
                        destination=str(data.get("destination", "")),
                        conflict=str(data.get("conflict", "rename")),
                    )
                    res_data.update(success=True, message="Transfer queued", job=job)
                except (OSError, ValueError) as exc:
                    res_data["message"] = str(exc)
            elif self.path == '/api/transfers/cancel':
                job_id = str(data.get("id", ""))
                cancelled = TRANSFER_MANAGER.cancel(job_id)
                res_data.update(success=cancelled, message="Cancellation requested" if cancelled else "Transfer not found or already finished")
            elif self.path == '/api/devices/select':
                serial = validate_serial(data.get("serial", ""))
                online = {
                    item["serial"] for item in get_detailed_adb_devices()
                    if item.get("status") == "device"
                }
                if serial not in online:
                    res_data["message"] = "That phone is not currently online."
                else:
                    identity = str(data.get("identity", "")).strip()
                    if not identity:
                        for item in ConnectPhone.load_config().get("saved_devices", []):
                            if not isinstance(item, dict):
                                continue
                            endpoint = f"{item.get('ip')}:{item.get('port')}"
                            if serial in {endpoint, item.get("device_serial")}:
                                identity = str(item.get("device_serial") or serial)
                                break
                    identity = identity or serial
                    ConnectPhone.global_config_mgr.select_device(identity)
                    os.environ["ANDROID_SERIAL"] = serial
                    _invalidate_status_cache()
                    res_data.update(success=True, message=f"Selected {serial} for Storage, Metrics, and legacy controls.")
            elif self.path == '/api/devices/rename':
                identity = validate_serial(data.get("identity", ""))
                ConnectPhone.global_config_mgr.rename_device(identity, data.get("name", ""))
                _invalidate_status_cache()
                res_data.update(success=True, message="Phone name updated.")
            elif self.path == '/api/devices/forget':
                identity = validate_serial(data.get("identity", ""))
                removed = ConnectPhone.global_config_mgr.forget_device(identity)
                if not removed:
                    res_data["message"] = "Trusted phone was not found."
                else:
                    endpoint = f"{removed.get('ip')}:{removed.get('port')}"
                    MIRROR_MANAGER.stop(serial=endpoint)
                    if _valid_ipv4(str(removed.get("ip", ""))) and _valid_port(removed.get("port")):
                        subprocess.run(["adb", "disconnect", endpoint], capture_output=True, timeout=5)
                    _invalidate_status_cache()
                    res_data.update(success=True, message="Phone forgotten. Pair it again before future automatic reconnects.")
            elif self.path == '/api/devices/reconnect-all':
                res_data.update(start_foreground_connect())
            elif self.path == '/api/fleet/mirror/start':
                mode = str(data.get("mode", "screen")).strip().lower()
                requested = data.get("serials") if isinstance(data.get("serials"), list) else [data.get("serial")]
                requested = [validate_serial(item) for item in requested if item]
                online_details = {
                    item["serial"]: item for item in get_detailed_adb_devices()
                    if item.get("status") == "device"
                }
                targets = [item for item in requested if item in online_details][:MAX_FLEET_DEVICES]
                if not targets:
                    res_data["message"] = "No requested phones are currently online."
                elif not shutil.which("scrcpy"):
                    res_data["message"] = "scrcpy is not installed."
                else:
                    config = ConnectPhone.load_config()
                    started = []
                    existing = []
                    failures = []
                    for index, serial in enumerate(targets):
                        try:
                            detail = online_details[serial]
                            session, created = MIRROR_MANAGER.start(
                                serial,
                                mode,
                                config=config,
                                options=data,
                                title=detail.get("model") or serial,
                                tile_index=index,
                            )
                            (started if created else existing).append(session)
                        except (OSError, RuntimeError, ValueError) as exc:
                            failures.append({"serial": serial, "message": str(exc)})
                    _invalidate_status_cache()
                    res_data.update(
                        success=bool(started or existing),
                        message=f"Started {len(started)} {mode} session(s); {len(existing)} already running.",
                        sessions=started + existing,
                        failures=failures,
                    )
            elif self.path == '/api/fleet/mirror/stop':
                stopped = MIRROR_MANAGER.stop(
                    session_id=str(data.get("session_id", "")).strip(),
                    serial=str(data.get("serial", "")).strip(),
                    mode=str(data.get("mode", "")).strip(),
                )
                _invalidate_status_cache()
                res_data.update(success=True, message=f"Stopped {stopped} mirror session(s).")
            elif self.path == '/api/fleet/control':
                online = [
                    item["serial"] for item in get_detailed_adb_devices()
                    if item.get("status") == "device"
                ][:MAX_FLEET_DEVICES]
                requested = data.get("serials")
                targets = online if requested == "all" else [item for item in (requested or []) if item in online]
                results = control_devices(targets, str(data.get("action", "")))
                succeeded = sum(1 for item in results if item["success"])
                res_data.update(
                    success=bool(results) and succeeded == len(results),
                    message=f"Command reached {succeeded} of {len(results)} phone(s).",
                    results=results,
                )
            elif self.path in {'/api/fleet/alert/start', '/api/fleet/alert/stop'}:
                online = [
                    item["serial"] for item in get_detailed_adb_devices()
                    if item.get("status") == "device"
                ][:MAX_FLEET_DEVICES]
                requested = data.get("serials")
                if requested == "all":
                    targets = online
                elif isinstance(requested, list):
                    targets = [item for item in requested if item in online]
                else:
                    serial = str(data.get("serial", "")).strip()
                    targets = [serial] if serial in online else []
                if not targets:
                    res_data["message"] = "No requested phones are currently online."
                else:
                    alert_action = start_emergency_alerts if self.path.endswith('/start') else stop_emergency_alerts
                    results = alert_action(targets)
                    succeeded = sum(1 for item in results if item["success"])
                    verb = "started on" if self.path.endswith('/start') else "stopped on"
                    res_data.update(
                        success=succeeded == len(targets),
                        message=f"Emergency alert {verb} {succeeded} of {len(targets)} phone(s).",
                        results=results,
                    )
            elif self.path == '/api/storage/delete':
                remote_path = str(data.get("path", "")).strip()
                if not _valid_remote_path(remote_path, destructive=True):
                    res_data["message"] = "Invalid remote path"
                else:
                    res = run_adb_cmd_with_retry(["adb", "shell", "rm", "-rf", "--", remote_path], timeout=10)
                    if res.returncode == 0:
                        res_data["success"] = True
                        res_data["message"] = f"Deleted {os.path.basename(remote_path)}"
                    else:
                        res_data["message"] = f"Delete failed: {res.stderr.strip()}"
            elif self.path == '/api/storage/delete_multiple':
                paths = data.get("paths", [])
                if not paths:
                    res_data["message"] = "No paths provided"
                else:
                    invalid = False
                    for p in paths:
                        if not _valid_remote_path(p, destructive=True):
                            invalid = True
                            break
                    if invalid:
                        res_data["message"] = "Invalid remote path detected"
                    else:
                        success_count = 0
                        errors = []
                        for p in paths:
                            res = run_adb_cmd_with_retry(["adb", "shell", "rm", "-rf", "--", p], timeout=10)
                            if res.returncode == 0:
                                success_count += 1
                            else:
                                errors.append(f"{os.path.basename(p)}: {res.stderr.strip()}")
                        
                        if success_count == len(paths):
                            res_data["success"] = True
                            res_data["message"] = f"Successfully deleted {success_count} items."
                        else:
                            res_data["success"] = success_count > 0
                            res_data["message"] = f"Deleted {success_count}/{len(paths)} items. Errors: {', '.join(errors)}"
            elif self.path == '/api/storage/download_external':
                remote_path = str(data.get("path", "")).strip()
                if not _valid_remote_path(remote_path):
                    res_data["message"] = "Invalid remote path"
                else:
                    filename = _safe_download_name(remote_path)
                    downloads_dir = os.path.expanduser("~/Downloads")
                    local_path = os.path.join(downloads_dir, filename)
                    
                    # Ensure filename is unique to avoid overwriting existing files
                    base, ext = os.path.splitext(filename)
                    counter = 1
                    while os.path.exists(local_path):
                        local_path = os.path.join(downloads_dir, f"{base}_{counter}{ext}")
                        counter += 1
                        
                    res = run_adb_cmd_with_retry(["adb", "pull", remote_path, local_path], timeout=60)
                    if res.returncode == 0:
                        res_data["success"] = True
                        res_data["message"] = f"Downloaded {os.path.basename(local_path)} to Downloads"
                        subprocess.Popen(["open", local_path])
                    else:
                        res_data["message"] = f"ADB pull failed: {res.stderr.strip()}"
            elif self.path == '/api/storage/download_zip_external':
                paths = data.get("paths", [])
                if not paths:
                    res_data["message"] = "No paths provided"
                else:
                    invalid = False
                    for p in paths:
                        if not _valid_remote_path(p):
                            invalid = True
                            break
                    if invalid:
                        res_data["message"] = "Invalid remote path detected"
                    else:
                        downloads_dir = os.path.expanduser("~/Downloads")
                        local_zip = os.path.join(downloads_dir, "phone_files.zip")
                        
                        # Handle filename collisions
                        counter = 1
                        while os.path.exists(local_zip):
                            local_zip = os.path.join(downloads_dir, f"phone_files_{counter}.zip")
                            counter += 1
                            
                        import zipfile
                        try:
                            with zipfile.ZipFile(local_zip, 'w', zipfile.ZIP_DEFLATED) as zip_f:
                                for p in paths:
                                    with tempfile.TemporaryDirectory() as temp_dir:
                                        name = os.path.basename(p.rstrip('/'))
                                        dest = os.path.join(temp_dir, name)
                                        res = subprocess.run(["adb", "pull", p, dest], capture_output=True, text=True, timeout=120)
                                        if res.returncode == 0:
                                            if os.path.isdir(dest):
                                                for root, _, files in os.walk(dest):
                                                    for file in files:
                                                        filepath = os.path.join(root, file)
                                                        arcname = os.path.relpath(filepath, temp_dir)
                                                        zip_f.write(filepath, arcname)
                                            else:
                                                zip_f.write(dest, name)
                            
                            res_data["success"] = True
                            res_data["message"] = f"Saved {os.path.basename(local_zip)} to Downloads"
                            subprocess.Popen(["open", "-R", local_zip])
                        except Exception as e:
                            res_data["message"] = f"Failed to compile ZIP: {str(e)}"
                            if os.path.exists(local_zip):
                                try:
                                    os.unlink(local_zip)
                                except Exception:
                                    pass
            elif self.path == '/api/storage/mkdir':
                parent_dir = str(data.get("parent", "")).strip()
                name = str(data.get("name", "")).strip()
                if not _valid_remote_path(parent_dir):
                    res_data["message"] = "Invalid parent path"
                elif not name or '/' in name or '\\' in name or any(c in name for c in ';&|$`'):
                    res_data["message"] = "Invalid folder name"
                else:
                    new_path = os.path.join(parent_dir, name)
                    res = subprocess.run(["adb", "shell", "mkdir", "-p", new_path], capture_output=True, text=True, timeout=10)
                    if res.returncode == 0:
                        res_data["success"] = True
                        res_data["message"] = f"Created folder '{name}'"
                    else:
                        res_data["message"] = f"Failed to create folder: {res.stderr.strip()}"
            elif self.path == '/api/files/local/mkdir':
                try:
                    destination = create_local_folder(str(data.get("parent", "")), str(data.get("name", "")))
                    res_data.update(success=True, message="Folder created", path=destination)
                except (OSError, ValueError) as exc:
                    res_data["message"] = str(exc)
            elif self.path == '/api/files/local/rename':
                try:
                    destination = rename_local_item(str(data.get("path", "")), str(data.get("name", "")))
                    res_data.update(success=True, message="Item renamed", path=destination)
                except (OSError, ValueError) as exc:
                    res_data["message"] = str(exc)
            elif self.path == '/api/files/local/trash':
                try:
                    destination = move_local_item_to_trash(str(data.get("path", "")))
                    res_data.update(success=True, message="Item moved to Trash", path=destination)
                except (OSError, ValueError) as exc:
                    res_data["message"] = str(exc)
            elif self.path == '/api/files/remote/rename':
                try:
                    destination = rename_remote_item(str(data.get("path", "")), str(data.get("name", "")))
                    res_data.update(success=True, message="Item renamed", path=destination)
                except (OSError, ValueError, RuntimeError) as exc:
                    res_data["message"] = str(exc)
            elif self.path == '/api/connect':
                ip = str(data.get("ip", "")).strip()
                port = str(data.get("port", "5555")).strip()
                if not _valid_ipv4(ip):
                    res_data["message"] = "A valid IPv4 address is required."
                elif not _valid_port(port):
                    res_data["message"] = "A valid TCP port is required."
                else:
                    ip_port = f"{ip}:{port}"
                    connected, output = _adb_connect(ip, int(port), attempts=1, timeout=4)
                    if connected:
                        res_data["success"] = True
                        os.environ["ANDROID_SERIAL"] = ip_port
                        device_serial = _get_adb_device_serial(ip_port)
                        if device_serial:
                            ConnectPhone.save_wireless_endpoint(ip, int(port), device_serial)
                            res_data["message"] = f"Connected to {ip_port}; identity pinned for persistent reconnect."
                            _publish_connected_endpoint(ip_port, device_serial)
                        else:
                            res_data["message"] = "Connected, but Android identity could not be verified; persistent reconnect is disabled until you reconnect manually."
                        # Cache this port for lightning reconnect
                        try:
                            cfg = ConnectPhone.load_config()
                            cfg["last_port"] = int(port)
                            if device_serial:
                                cfg["last_device_serial"] = device_serial
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
                        
            elif self.path == '/api/pair/qr/start':
                res_data.update(start_qr_pairing())

            elif self.path == '/api/connect/authorized':
                res_data.update(start_foreground_connect())

            elif self.path == '/api/connect/auto':
                # Use the same identity-pinned fast connector as the explicit
                # Authorized button. The legacy implementation below remains
                # as compatibility code but is intentionally bypassed.
                res_data.update(start_foreground_connect())
                self.wfile.write(json.dumps(res_data).encode('utf-8'))
                return
                config = ConnectPhone.load_config()
                saved = [
                    item for item in config.get("saved_devices", [])
                    if isinstance(item, dict) and item.get("auto_reconnect", True)
                    and _valid_ipv4(str(item.get("ip", "")).strip())
                    and _valid_port(item.get("port"))
                ]
                preferred = saved[0] if saved else {}
                ip = str(preferred.get("ip") or config.get("last_ip", "")).strip()
                last_port = preferred.get("port") or config.get("last_port", None)
                if last_port:
                    try:
                        last_port = int(last_port)
                    except Exception:
                        last_port = None
                if not ip:
                    res_data["success"] = False
                    res_data["message"] = "No previously paired IP address found in config. Connect manually first."
                else:
                    if not _valid_ipv4(ip):
                        res_data["message"] = "Saved IP is invalid. Choose a saved device or connect manually."
                        self.wfile.write(json.dumps(res_data).encode('utf-8'))
                        return
                    connected = False

                    # 0. Lightning path: ask ADB directly. A raw TCP preflight
                    # produced false negatives on power-saving Wi-Fi and sent
                    # healthy endpoints through the slow discovery fallback.
                    if last_port and last_port not in (5555,):
                        ip_port = f"{ip}:{last_port}"
                        connected_now, _ = _adb_connect(ip, int(last_port), attempts=1, timeout=3)
                        if connected_now:
                            accepted, identity = _accept_auto_wireless_connection(ip, int(last_port))
                            if accepted:
                                os.environ["ANDROID_SERIAL"] = ip_port
                                res_data["success"] = True
                                res_data["message"] = f"⚡ Instantly reconnected to {ip_port}!"
                                connected = True
                                _publish_connected_endpoint(ip_port, identity)

                    # 1. mDNS discovery (1.5 s timeout — usually resolves in < 200 ms)
                    if not connected:
                        services = discover_all_mdns_services(timeout=0.8, target_ip=ip)
                        mdns_port = next((item["port"] for item in services if item.get("type") == "connect"), None)
                        if mdns_port:
                            ip_port = f"{ip}:{mdns_port}"
                            connected_now, _ = _adb_connect(ip, int(mdns_port), attempts=1, timeout=3)
                            if connected_now:
                                accepted, _ = _accept_auto_wireless_connection(ip, int(mdns_port))
                                if accepted:
                                    os.environ["ANDROID_SERIAL"] = ip_port
                                    res_data["success"] = True
                                    res_data["message"] = f"Successfully auto-connected to phone at {ip_port}!"
                                    connected = True
                                    _publish_connected_endpoint(ip_port, _saved_wireless_serial(ip))

                    # 2. Parallel port scan fallback
                    if not connected:
                        target_port = scan_and_connect_wireless_debug(ip, last_known_port=last_port, allow_port_scan=False)
                        if target_port:
                            accepted, _ = _accept_auto_wireless_connection(ip, int(target_port))
                            if accepted:
                                os.environ["ANDROID_SERIAL"] = f"{ip}:{int(target_port)}"
                                res_data["success"] = True
                                res_data["message"] = f"Successfully auto-connected to phone at {ip}:{target_port}!"
                                connected = True
                                _invalidate_status_cache()

                    if not connected:
                        import platform
                        ping_param = "-n" if platform.system().lower() == "windows" else "-c"
                        ping_res = subprocess.run(["ping", ping_param, "1", "-W", "1000", ip], capture_output=True, timeout=5)
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
                if target_ip and not _valid_ipv4(target_ip):
                    res_data["message"] = "A valid IPv4 address is required."
                    self.wfile.write(json.dumps(res_data).encode('utf-8'))
                    return
                if target_port and not _valid_port(target_port):
                    res_data["message"] = "A valid TCP port is required."
                    self.wfile.write(json.dumps(res_data).encode('utf-8'))
                    return
                if target_ip and target_port:
                    ip_port = f"{target_ip}:{target_port}"
                    res = subprocess.run(["adb", "disconnect", ip_port], capture_output=True, text=True)
                    res_data["message"] = f"Disconnected from {ip_port}."
                    ConnectPhone.global_config_mgr.disable_auto_reconnect(target_ip, int(target_port))
                elif target_ip:
                    res = subprocess.run(["adb", "disconnect", target_ip], capture_output=True, text=True)
                    res_data["message"] = f"Disconnected from {target_ip}."
                    ConnectPhone.global_config_mgr.disable_auto_reconnect(target_ip)
                else:
                    res = subprocess.run(["adb", "disconnect"], capture_output=True, text=True)
                    res_data["message"] = "Disconnected from all devices."
                    ConnectPhone.global_config_mgr.disable_auto_reconnect()
                    stop_scrcpy_bg()
                res_data["success"] = True
                _invalidate_status_cache()

            elif self.path == '/api/pair':
                ip = str(data.get("ip", "")).strip()
                port = str(data.get("port", "")).strip()
                code = str(data.get("code", "")).strip()
                if not _valid_ipv4(ip) or not _valid_port(port) or not code.isdigit() or len(code) != 6:
                    res_data["message"] = "Enter a valid IPv4 address, TCP port, and 6-digit pairing code."
                else:
                    res_data.update(pair_and_connect_wireless(ip, int(port), code))
                    self.wfile.write(json.dumps(res_data).encode('utf-8'))
                    return
                    ip_port = f"{ip}:{port}"
                    print(f"[UI Server] Attempting wireless pairing to {ip_port}...")

                    # ── Pre-pairing: restart ADB server to clear stale TLS
                    # state from prior USB or wireless sessions.  On ADB >= 35
                    # this is the #1 reason wireless pairing silently fails.
                    try:
                        print("[UI Server] Restarting ADB server to clear stale TLS state...")
                        subprocess.run(["adb", "kill-server"], capture_output=True, timeout=5)
                        time.sleep(0.3)
                        subprocess.run(["adb", "start-server"], capture_output=True, timeout=5)
                        time.sleep(0.5)
                    except Exception as e:
                        print(f"[UI Server] ADB restart warning: {e}")

                    def _try_pair_cli(target_ip_port, pair_code):
                        """Strategy 1: adb pair <ip:port> <code>  — works on ADB >= 30."""
                        try:
                            res = subprocess.run(
                                ["adb", "pair", target_ip_port, pair_code],
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

                    def _try_pair_stdin(target_ip_port, pair_code):
                        """Strategy 2: adb pair <ip:port>  then write code to stdin."""
                        try:
                            res = subprocess.run(
                                ["adb", "pair", target_ip_port],
                                input=f"{pair_code}\n",
                                capture_output=True, text=True, timeout=12
                            )
                            combined = (res.stdout or "") + " " + (res.stderr or "")
                            print(f"[UI Server] Strategy 2 (stdin): rc={res.returncode} out={res.stdout.strip()} err={res.stderr.strip()}")
                            if "successfully paired" in combined.lower():
                                return True, combined.strip()
                            # Only the explicit "successfully paired" message
                            # proves the handshake completed.  Seeing the
                            # "Enter pairing code:" prompt alone does NOT mean
                            # pairing succeeded — report failure so Strategy 3
                            # (PTY) gets a chance.
                            return False, combined.strip()
                        except subprocess.TimeoutExpired:
                            return False, "timeout"
                        except Exception as e:
                            return False, str(e)

                    def _try_pair_pty(target_ip_port, pair_code):
                        """Strategy 3: use a pseudo-terminal so adb sees a real TTY (avoids prompt-suppress issues)."""
                        try:
                            import pty, os, select as _sel
                            master_fd, slave_fd = pty.openpty()
                            proc = subprocess.Popen(
                                ["adb", "pair", target_ip_port],
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
                                        os.write(master_fd, f"{pair_code}\n".encode())
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

                    def _run_all_pair_strategies(target_ip_port, pair_code):
                        """Run all 3 pairing strategies in order, return (success, message)."""
                        ok, msg = _try_pair_cli(target_ip_port, pair_code)
                        if ok:
                            return True, msg
                        ok, msg = _try_pair_stdin(target_ip_port, pair_code)
                        if ok:
                            return True, msg
                        ok, msg = _try_pair_pty(target_ip_port, pair_code)
                        if ok:
                            return True, msg
                        return False, msg

                    # ── Phase 1: Try pairing with the user-entered port first.
                    # The pairing code is cryptographically bound to the port
                    # shown on the phone; we must NOT silently replace it.
                    success, final_msg = _run_all_pair_strategies(ip_port, code)

                    # ── Phase 2 (fallback): If the user's port failed, try the
                    # mDNS-advertised pairing port.  The phone may have
                    # re-advertised on a new port since the user read it.
                    if not success:
                        fresh_ip, fresh_port = discover_adb_service_hybrid(
                            "_adb-tls-pairing._tcp.local.",
                            target_ip=ip,
                            timeout=1.5,
                        )
                        if fresh_ip == ip and fresh_port and str(int(fresh_port)) != port:
                            mdns_ip_port = f"{ip}:{int(fresh_port)}"
                            print(f"[UI Server] User port failed; retrying with mDNS-discovered port: {mdns_ip_port}")
                            success, final_msg = _run_all_pair_strategies(mdns_ip_port, code)

                    # ── Build response ──────────────────────────────────────────
                    if success:
                        # Pairing authorizes the Mac but uses a different,
                        # rotating port from the main ADB connect service.
                        # Resolve that service immediately so the user does
                        # not have to copy a second port by hand.
                        connected_port = None
                        _, discovered_port = discover_adb_service_hybrid(
                            "_adb-tls-connect._tcp.local.",
                            target_ip=ip,
                            timeout=1.2,
                        )
                        if not discovered_port:
                            discovered_port = scan_and_connect_wireless_debug(ip, allow_port_scan=True)
                        if discovered_port:
                            connected_now, _ = _adb_connect(ip, int(discovered_port), attempts=3)
                            if connected_now:
                                connected_port = int(discovered_port)
                                serial = _get_adb_device_serial(f"{ip}:{connected_port}")
                                os.environ["ANDROID_SERIAL"] = f"{ip}:{connected_port}"
                                ConnectPhone.save_wireless_endpoint(ip, connected_port, serial)
                                _invalidate_status_cache()
                        res_data["success"] = True
                        if connected_port:
                            res_data["message"] = f"✅ Paired and connected to {ip}:{connected_port}."
                        else:
                            res_data["message"] = (
                                "✅ Successfully paired. The phone did not publish its connect service yet; "
                                "keep Wireless Debugging enabled and click Connect/Auto-connect once."
                            )
                    else:
                        err_lower = final_msg.lower()
                        if "connection refused" in err_lower or "timeout" in err_lower or "timed out" in err_lower:
                            res_data["message"] = (
                                f"Pairing failed: {final_msg}\n\n"
                                "💡 Connection refused / timeout. Both your Mac and phone must be on the "
                                "same Wi-Fi network. If you use a router with AP Isolation / Client Isolation, "
                                "disable it. Also ensure Wireless Debugging is still toggled ON.\n\n"
                                "🔧 Also try: close the pairing-code popup on your phone, "
                                "turn Wireless Debugging OFF and ON, then tap 'Pair device with pairing code' "
                                "again to get a fresh port and code."
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
                                "your phone and you are using the port shown inside that popup. If the popup "
                                "was open before scanning, close it, open a fresh pairing-code popup, scan "
                                "again, and submit the new code immediately."
                            )
                
            elif self.path == '/api/restart_adb':
                subprocess.run(["adb", "kill-server"], timeout=8)
                subprocess.run(["adb", "start-server"], timeout=8)
                res_data["success"] = True
                res_data["message"] = "ADB server restarted successfully."
                
            elif self.path == '/api/ping':
                ip = str(data.get("ip", "")).strip() if data and data.get("ip") is not None else ""
                if ip and not _valid_ipv4(ip):
                    res_data["message"] = "A valid IPv4 address is required."
                    ip = ""
                if not ip:
                    ip = "unknown"
                    res_route = subprocess.run(["adb", "shell", "ip route"], capture_output=True, text=True, timeout=5)
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
                    res_ping = subprocess.run(["ping", "-c", "3", "-W", "2000", ip], capture_output=True, text=True, timeout=8)
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
                mirror_type = data.get("type", "screen") # screen, camera, audio, call, record
                if mirror_type not in {"screen", "camera", "audio", "call", "record"}:
                    raise ValueError("Unsupported mirroring mode")
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
                is_wireless = ":" in os.environ.get("ANDROID_SERIAL", "")

                cmd = ["scrcpy", "--window-title", "ConnectPhone"]
                a_buf = config.get("audio_buffer", "20")
                cmd.append(f"--audio-buffer={a_buf}")
                # Keep the macOS playback queue short as well. The transport
                # buffer above absorbs network jitter; this queue should not
                # add another large delay to live mic/audio mirroring.
                cmd.append("--audio-output-buffer=10")
                
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
                    if facing not in {"front", "back"} or resolution not in {"720p", "1080p", "4k"} or not isinstance(no_audio, bool):
                        raise ValueError("Invalid camera options")
                    
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
                        
                    # Apply camera quality preferences.  Do not add a
                    # wireless buffer here: scrcpy already defaults to zero
                    # video buffering, which is the lowest-latency path.  A
                    # buffer hides jitter but makes the preview visibly late.
                    c_bitrate = config.get("camera_bitrate", "32M")
                    c_fps = config.get("camera_fps", "60")
                    c_codec = config.get("camera_codec", "h265")
                    
                    # For standard camera mirroring, cap FPS to 30 to match sensor limits and prevent encoder overflow
                    if c_fps not in ["120", "240"]:
                        c_fps = "30"
                        
                    # Use a predictable Wi-Fi profile instead of silently
                    # dropping every camera stream to 6 Mbps.  This phone
                    # advertises 4K/30 on the rear camera and 1080p/30 on the
                    # front camera. H.264 is the safer low-latency choice for
                    # 1080p; H.265 is retained for 4K where bandwidth matters.
                    if facing == "front":
                        c_bitrate = "12M"
                        c_codec = "h264"
                    elif resolution == "4k":
                        c_bitrate = "32M"
                        c_codec = "h265"
                    elif is_wireless:
                        c_bitrate = "16M"
                        c_codec = "h264"
                            
                    cmd.append("--stay-awake")
                    cmd += [f"--video-bit-rate={c_bitrate}", f"--camera-fps={c_fps}", f"--video-codec={c_codec}"]
                    # Never silently replace a requested HD size with a
                    # smaller one. Fail clearly if a different phone cannot
                    # provide the requested camera mode.
                    cmd.append("--no-downsize-on-error")
                    
                    if c_fps in ["120", "240"]:
                        cmd = [a for a in cmd if not a.startswith("--camera-size=")]
                        cmd.append("--camera-size=1280x720")
                        cmd.append("--camera-high-speed")
                        
                    temp_mkv_path = os.path.expanduser("~/.connectphone_temp_rec.mkv")
                    cmd.append(f"--record={temp_mkv_path}")
                    cmd.append("--record-orientation=0")
                    
                elif mirror_type == "audio":
                    cmd += ["--no-video"] + audio_args

                elif mirror_type == "call":
                    # VOICE_CALL requests the telephony uplink and downlink.
                    # Requiring audio prevents a misleading successful session
                    # when Android reserves capture for system components.
                    cmd += [
                        "--no-video", "--no-control", "--require-audio",
                        "--audio-source=voice-call", "--audio-codec=opus",
                        "--audio-bit-rate=128000",
                    ]
                    res_data["message"] = "Call audio is playing on this Mac. Start or answer the call on the phone."
                    
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
                time.sleep(1.0 if mirror_type == "call" else 0.35)
                if scrcpy_proc.poll() is not None:
                    output = (scrcpy_proc.communicate(timeout=1)[0] or b"").decode("utf-8", errors="replace").strip()
                    scrcpy_proc = None
                    scrcpy_state["mirror_type"] = None
                    raise RuntimeError(output[-1200:] or "scrcpy exited before the stream initialized")

                if mirror_type == "camera" and temp_mkv_path:
                    def enforce_camera_file_limit(process, path, limit=2 * 1024 * 1024 * 1024):
                        while process.poll() is None:
                            try:
                                if os.path.getsize(path) > limit:
                                    print("[Camera] Temporary recording reached 2 GiB; stopping to protect disk space")
                                    process.terminate()
                                    return
                            except OSError:
                                pass
                            time.sleep(10)
                    threading.Thread(target=enforce_camera_file_limit, args=(scrcpy_proc, temp_mkv_path), daemon=True).start()
                
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
                requested_serial = str(data.get("serial", "")).strip()
                serial = validate_serial(requested_serial or os.environ.get("ANDROID_SERIAL", ""))
                kind = str(data.get("kind", "auto")).strip().lower()
                if kind not in {"auto", "phone", "app"}:
                    raise ValueError("Unsupported unlock type")
                online = {
                    item["serial"] for item in get_detailed_adb_devices()
                    if item.get("status") == "device"
                }
                if serial not in online:
                    res_data["message"] = "That phone is not currently online."
                elif not UNLOCK_OPERATION_LOCK.acquire(blocking=False):
                    res_data["message"] = "Another Touch ID unlock is already in progress."
                else:
                    try:
                        def lock_state():
                            policy = subprocess.run(
                                ["adb", "-s", serial, "shell", "dumpsys", "window", "policy"],
                                capture_output=True, text=True, timeout=5,
                            ).stdout.lower().replace(" ", "")
                            window = subprocess.run(
                                ["adb", "-s", serial, "shell", "dumpsys", "window"],
                                capture_output=True, text=True, timeout=5,
                            ).stdout
                            focus_lines = "\n".join(
                                line.lower() for line in window.splitlines()
                                if "mCurrentFocus" in line or "mFocusedApp" in line
                            )
                            phone_locked = any(marker in policy for marker in (
                                "showing=true", "misshowing=true", "iskeyguardshowing=true",
                                "mshowinglockscreen=true", "inputrestricted=true",
                            ))
                            app_locked = any(marker in focus_lines for marker in (
                                "securitycenter", "applock", "passcode", "credential",
                            ))
                            return phone_locked, app_locked

                        phone_locked, app_locked = lock_state()
                        effective_kind = kind
                        if kind == "auto":
                            effective_kind = "app" if app_locked else "phone"
                        if effective_kind == "phone" and not phone_locked:
                            res_data.update(success=True, message="Phone is already unlocked.")
                        elif effective_kind == "app" and not app_locked:
                            res_data["message"] = "Open the locked app on the phone first, then press Unlock App."
                        else:
                            config = ConnectPhone.load_config()
                            android_pin = str(config.get("android_pin", ""))
                            app_pin = str(config.get("applock_pin", ""))
                            selected_pin = app_pin if effective_kind == "app" else android_pin
                            preference_name = "App Lock PIN" if effective_kind == "app" else "Android Lockscreen PIN"
                            if not selected_pin:
                                res_data["message"] = f"{preference_name} is not configured. Save it in Preferences first."
                            else:
                                # The legacy unlock routine is intentionally scoped
                                # to the explicitly requested transport. App unlock
                                # receives only the App Lock PIN, avoiding a failed
                                # attempt with the device PIN.
                                scoped_config = dict(config)
                                scoped_config["android_pin"] = selected_pin
                                scoped_config["applock_pin"] = selected_pin
                                os.environ["ANDROID_SERIAL"] = serial
                                ConnectPhone.unlock_device_with_touch_id(
                                    scoped_config, interactive=False, wake_screen=True
                                )
                                phone_locked_after, app_locked_after = lock_state()
                                unlocked = not (phone_locked_after if effective_kind == "phone" else app_locked_after)
                                label = "Phone" if effective_kind == "phone" else "App Lock"
                                res_data.update(
                                    success=unlocked,
                                    message=f"{label} unlocked successfully." if unlocked else
                                            f"{label} is still locked. Confirm Touch ID and enable Xiaomi USB debugging (Security settings).",
                                )
                    except (OSError, subprocess.TimeoutExpired) as exc:
                        res_data["message"] = f"Unlock check failed: {exc}"
                    finally:
                        UNLOCK_OPERATION_LOCK.release()
                
            elif self.path == '/api/settings/save':
                config = ConnectPhone.load_config()
                updates = _validated_settings(data)
                if any(key in data for key in ("android_pin", "applock_pin")) and not any(key in updates for key in ("android_pin", "applock_pin")):
                    res_data["message"] = "PIN must contain 4–32 digits."
                    self.wfile.write(json.dumps(res_data).encode('utf-8'))
                    return
                config.update(updates)
                ConnectPhone.save_config(config)
                res_data["success"] = True
                res_data["message"] = "Preferences saved successfully!" if updates else "No valid preference changes were supplied."
                
            elif self.path == '/api/screenshots/pull':
                filepath = str(data.get("path", "")).strip()
                allowed_roots = ("/sdcard/DCIM/Screenshots/", "/sdcard/Pictures/Screenshots/")
                if not _valid_remote_path(filepath) or not filepath.startswith(allowed_roots) or posixpath.basename(filepath) in {"", ".", ".."}:
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

            elif self.path == '/api/clipboard/sync/status':
                is_running = False
                if 'scrcpy_clipboard_proc' in globals() and scrcpy_clipboard_proc and scrcpy_clipboard_proc.poll() is None:
                    is_running = True
                res_data["success"] = True
                res_data["is_running"] = is_running

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


            elif self.path == '/api/termux/execute':
                command = str(data.get("command", "")).strip()
                if not command:
                    res_data["message"] = "Command is required."
                elif len(command) > 4096 or "\x00" in command:
                    res_data["message"] = "Command is too long or contains invalid characters."
                elif not re.fullmatch(
                    r"(?:termux-(?:battery-status|telephony-deviceinfo|wifi-connectioninfo)|uname -a|df -h /data|pm list packages --user 0 -3)",
                    command,
                ):
                    res_data["message"] = "This command is not permitted by the built-in diagnostics policy."
                else:
                    try:
                        # Fully export PATH, LD_LIBRARY_PATH, and HOME for Termux binaries (using canonical user paths)
                        termux_env_cmd = (
                            "export PATH=/data/user/0/com.termux/files/usr/bin:$PATH\n"
                            "export LD_LIBRARY_PATH=/data/user/0/com.termux/files/usr/lib\n"
                            "export HOME=/data/user/0/com.termux/files/home\n"
                            "cd /data/user/0/com.termux/files/home\n"
                            f"{command}\n"
                            "exit\n"
                        )
                        proc = subprocess.Popen(
                            ["adb", "shell", "run-as", "com.termux", "sh"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
                        )
                        stdout, stderr = proc.communicate(input=termux_env_cmd, timeout=15)
                        res_data["success"] = True
                        res_data["stdout"] = stdout
                        res_data["stderr"] = stderr
                        res_data["exit_code"] = proc.returncode
                    except subprocess.TimeoutExpired:
                        res_data["success"] = False
                        res_data["message"] = "Command timed out after 15 seconds."
                    except Exception as e:
                        res_data["success"] = False
                        res_data["message"] = f"Execution error: {e}"

            elif self.path == '/api/termux/install':
                global termux_install_state
                if termux_install_state["status"] in ["downloading", "installing"]:
                    res_data["success"] = True
                    res_data["message"] = "Installation is already in progress."
                else:
                    threading.Thread(target=run_termux_install_background, daemon=True).start()
                    res_data["success"] = True
                    res_data["message"] = "Started Termux background installation."

            elif self.path == '/api/termux/tts':
                text = str(data.get("text", "")).strip()
                pitch = float(data.get("pitch", 1.0))
                rate = float(data.get("rate", 1.0))
                if not text:
                    res_data["message"] = "Text is required."
                elif len(text) > 4096 or not 0.1 <= pitch <= 2.0 or not 0.1 <= rate <= 2.0:
                    res_data["message"] = "Text or voice settings are out of range."
                else:
                    try:
                        tts_cmd = (
                            "export PATH=/data/user/0/com.termux/files/usr/bin:$PATH\n"
                            "export LD_LIBRARY_PATH=/data/user/0/com.termux/files/usr/lib\n"
                            f"termux-tts-speak -p {pitch} -r {rate} {shlex.quote(text)}\n"
                            "exit\n"
                        )
                        proc = subprocess.Popen(
                            ["adb", "shell", "run-as", "com.termux", "sh"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
                        )
                        stdout, stderr = proc.communicate(input=tts_cmd, timeout=10)
                        res_data["success"] = True
                        res_data["stdout"] = stdout
                        res_data["stderr"] = stderr
                    except Exception as e:
                        res_data["success"] = False
                        res_data["message"] = f"TTS failed: {e}"

            elif self.path == '/api/termux/sensors':
                try:
                    def run_termux_cmd(cmd):
                        termux_env_cmd = (
                            "export PATH=/data/user/0/com.termux/files/usr/bin:$PATH\n"
                            "export LD_LIBRARY_PATH=/data/user/0/com.termux/files/usr/lib\n"
                            f"{cmd}\n"
                            "exit\n"
                        )
                        proc = subprocess.Popen(
                            ["adb", "shell", "run-as", "com.termux", "sh"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
                        )
                        stdout, _ = proc.communicate(input=termux_env_cmd, timeout=5)
                        return stdout.strip()
                    
                    battery = run_termux_cmd("termux-battery-status")
                    wifi = run_termux_cmd("termux-wifi-connectioninfo")
                    telephony = run_termux_cmd("termux-telephony-deviceinfo")
                    
                    res_data["success"] = True
                    try:
                        res_data["battery"] = json.loads(battery) if battery else {}
                    except Exception:
                        res_data["battery"] = {"error": battery or "No response"}
                    try:
                        res_data["wifi"] = json.loads(wifi) if wifi else {}
                    except Exception:
                        res_data["wifi"] = {"error": wifi or "No response"}
                    try:
                        res_data["telephony"] = json.loads(telephony) if telephony else {}
                    except Exception:
                        res_data["telephony"] = {"error": telephony or "No response"}
                except Exception as e:
                    res_data["success"] = False
                    res_data["message"] = f"Telemetry failed: {e}"

            elif self.path == '/api/termux/scan':
                target = str(data.get("target", "")).strip()
                scan_type = str(data.get("type", "fast")).strip()
                if not target:
                    res_data["message"] = "Target IP or Subnet is required."
                else:
                    try:
                        # Use aggressive timing template -T4 to execute scans much faster and avoid subnet timeouts
                        if scan_type not in {"ping", "fast", "full"}:
                            res_data["message"] = "Invalid scan type."
                            raise ValueError("invalid scan type")
                        try:
                            ipaddress.ip_network(target, strict=False)
                        except ValueError:
                            res_data["message"] = "Target must be a valid IP address or CIDR network."
                            raise ValueError("invalid scan target")
                        flags = "-sn" if scan_type == "ping" else ("-F -T4" if scan_type == "fast" else "-p 1-1000 -T4")
                        scan_cmd = (
                            "export PATH=/data/user/0/com.termux/files/usr/bin:$PATH\n"
                            "export LD_LIBRARY_PATH=/data/user/0/com.termux/files/usr/lib\n"
                            f"nmap {flags} {shlex.quote(target)}\n"
                            "exit\n"
                        )
                        proc = subprocess.Popen(
                            ["adb", "shell", "run-as", "com.termux", "sh"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
                        )
                        stdout, stderr = proc.communicate(input=scan_cmd, timeout=120)
                        res_data["success"] = True
                        res_data["stdout"] = stdout
                        res_data["stderr"] = stderr
                    except subprocess.TimeoutExpired:
                        res_data["success"] = False
                        res_data["message"] = "Scan timed out (limit: 120s). Reduce range or run a Ping Scan."
                    except Exception as e:
                        res_data["success"] = False
                        res_data["message"] = f"Scan execution failed: {e}"
            elif self.path == '/api/app/restart':
                res_data["success"] = True
                res_data["message"] = "Restarting application..."
                self.wfile.write(json.dumps(res_data).encode('utf-8'))
                # Start a separate thread to let the HTTP response flush before killing the process
                def restart_server():
                    time.sleep(0.5)
                    import sys, os
                    if getattr(sys, "frozen", False):
                        subprocess.Popen(
                            [sys.executable, "--relaunch-wait", str(os.getpid())],
                            start_new_session=True,
                            close_fds=True,
                        )
                        os._exit(0)
                    os.execv(sys.executable, [sys.executable] + sys.argv)
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
    while not _shutdown_event.is_set():
        try:
            config = ConnectPhone.load_config()
            if config.get("device_profile") == "oneplus":
                # Periodically run a simple adb shell command to maintain connection
                subprocess.run(["adb", "shell", "true"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
        _shutdown_event.wait(30)

def start_server_in_thread(httpd):
    try:
        httpd.serve_forever()
    except Exception:
        pass
    finally:
        stop_scrcpy_bg()

def run_server():
    _shutdown_event.clear()
    if "--relaunch-wait" in sys.argv:
        try:
            index = sys.argv.index("--relaunch-wait")
            old_pid = int(sys.argv[index + 1])
            del sys.argv[index:index + 2]
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    os.kill(old_pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.1)
        except (ValueError, IndexError):
            pass
    # Prompt biometric/passcode authentication before starting the application!
    script_dir = os.path.dirname(os.path.realpath(__file__))
    helper_path = os.path.join(script_dir, "touch_id_helper")
    if not os.path.exists(helper_path):
        touch_id_swift = os.path.join(script_dir, "touch_id.swift")
        if os.path.exists(touch_id_swift):
            try:
                subprocess.run(["swiftc", touch_id_swift, "-o", helper_path])
            except Exception:
                pass

    if os.path.exists(helper_path):
        res = subprocess.run([helper_path, "Authenticate to access ConnectPhone"], capture_output=True, text=True)
        stdout = res.stdout or ""
        if "SUCCESS" not in stdout:
            print("❌ Authentication failed. Exiting ConnectPhone.")
            sys.exit(1)

    class WebviewApi:
        def __init__(self):
            self.window = None
            
        def set_window(self, window):
            self.window = window
            
        def show_inspector(self):
            if self.window:
                self.window.show_inspector()
                return {"success": True}
            return {"success": False}

    class ThreadingHTTPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
        allow_reuse_address = True
        daemon_threads = True

    try:
        httpd = ThreadingHTTPServer((UI_HOST, PORT), ConnectPhoneUIHandler)
    except OSError as e:
        # errno 48 is Address already in use on macOS
        if e.errno == 48 or "already in use" in str(e).lower():
            print(f"\nℹ️ TCP port {PORT} is already in use; refusing to disclose credentials to an unverified listener.")
            sys.exit(0)
        else:
            raise e

    # Start fast status-cache background refresher
    cache_thread = threading.Thread(target=_status_cache_worker, daemon=True)
    cache_thread.start()

    # Start background ADB Keep-Alive Watcher for OnePlus/Oppo devices
    keepalive_thread = threading.Thread(target=adb_keepalive_loop)
    keepalive_thread.daemon = True
    keepalive_thread.start()

    print(f"\n🚀 ConnectPhone UI Dashboard Running on http://localhost:{PORT}")
    
    from core.auto_reconnect import AutoReconnector
    auto_reconnector = AutoReconnector(busy_check=TRANSFER_MANAGER.has_active)
    auto_reconnector.start_watching()

    cleanup_lock = threading.Lock()
    cleanup_complete = False

    def cleanup_resources(*_args):
        nonlocal cleanup_complete
        with cleanup_lock:
            if cleanup_complete:
                return
            cleanup_complete = True
        print("ConnectPhone is closing; cleaning up local services and ADB transports.")
        _shutdown_event.set()
        _status_cache_event.set()
        auto_reconnector.stop_watching()
        if cache_thread.is_alive():
            cache_thread.join(timeout=15)
        if keepalive_thread.is_alive():
            keepalive_thread.join(timeout=2)
        stop_scrcpy_bg()
        MIRROR_MANAGER.stop_all()
        TRANSFER_MANAGER.shutdown()
        try:
            saved = ConnectPhone.load_config().get("saved_devices", [])
            owned_serials = {
                item.get("device_serial")
                for item in saved
                if isinstance(item, dict) and item.get("device_serial")
            }
        except (OSError, TypeError, ValueError):
            owned_serials = set()
        ADB_LIFECYCLE.cleanup(auto_reconnector.connected_endpoints, owned_serials)
        httpd.shutdown()

    atexit.register(cleanup_resources)

    def handle_termination(_signum, _frame):
        cleanup_resources()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, handle_termination)
    
    server_thread = threading.Thread(target=start_server_in_thread, args=(httpd,))
    server_thread.daemon = True
    server_thread.start()

    try:
        import webview
        webview.settings['OPEN_DEVTOOLS_IN_DEBUG'] = False
        js_api = WebviewApi()
        win = webview.create_window('ConnectPhone Dashboard', f"http://{UI_HOST}:{PORT}/#token={API_TOKEN}", width=1450, height=950, frameless=False, js_api=js_api)
        js_api.set_window(win)
        win.events.closing += cleanup_resources
        win.events.closed += cleanup_resources
        webview.start(debug=False)
    except ImportError:
        print("💡 pywebview not found, falling back to standard web browser.")
        webbrowser.open(f"http://{UI_HOST}:{PORT}/#token={API_TOKEN}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
    finally:
        cleanup_resources()

if __name__ == "__main__":
    run_server()
