"""Multi-device fleet views and independently routed scrcpy sessions."""

from __future__ import annotations

import datetime
import concurrent.futures
import os
import re
import secrets
import subprocess
import threading
import time
from pathlib import Path


MAX_FLEET_DEVICES = 10
MAX_MIRROR_SESSIONS = 20
_SERIAL_RE = re.compile(r"^[A-Za-z0-9._:\-\[\]]{1,200}$")


def validate_serial(serial: object) -> str:
    value = str(serial or "").strip()
    if not _SERIAL_RE.fullmatch(value):
        raise ValueError("Invalid ADB device serial")
    return value


def build_fleet(adb_devices, saved_devices, active_transport="", selected_identity="", sessions=()):
    """Merge current ADB transports with trusted identities for the dashboard."""
    online = {
        item.get("serial"): dict(item)
        for item in adb_devices
        if isinstance(item, dict) and item.get("serial") and item.get("status") == "device"
    }
    session_map = {}
    for session in sessions:
        session_map.setdefault(session.get("serial"), []).append(session)

    fleet = []
    consumed = set()
    for saved in saved_devices[:MAX_FLEET_DEVICES]:
        if not isinstance(saved, dict):
            continue
        identity = str(saved.get("device_serial") or "").strip()
        ip = str(saved.get("ip") or "").strip()
        port = saved.get("port")
        endpoint = f"{ip}:{port}" if ip and port else ""
        transport = endpoint if endpoint in online else (identity if identity in online else endpoint)
        current = online.get(transport, {})
        consumed.update(item for item in (endpoint, identity) if item in online)
        name = str(saved.get("name") or current.get("model") or "Android Device").strip()[:60]
        device_sessions = list(session_map.get(transport, []))
        if identity and identity != transport:
            device_sessions.extend(session_map.get(identity, []))
        fleet.append({
            "identity": identity,
            "serial": transport,
            "endpoint": endpoint,
            "name": name,
            "model": current.get("model") or name,
            "type": current.get("type") or ("wireless" if endpoint else "unknown"),
            "status": "online" if transport in online else "offline",
            "trusted": bool(identity),
            "auto_reconnect": bool(saved.get("auto_reconnect", bool(identity))),
            "selected": bool(
                (selected_identity and identity == selected_identity)
                or (not selected_identity and transport == active_transport)
            ),
            "sessions": device_sessions,
        })

    for serial, current in online.items():
        if serial in consumed or len(fleet) >= MAX_FLEET_DEVICES:
            continue
        fleet.append({
            "identity": serial if ":" not in serial else "",
            "serial": serial,
            "endpoint": serial if ":" in serial else "",
            "name": current.get("model") or "Android Device",
            "model": current.get("model") or "Android Device",
            "type": current.get("type") or ("wireless" if ":" in serial else "usb"),
            "status": "online",
            "trusted": False,
            "auto_reconnect": False,
            "selected": serial == active_transport,
            "sessions": session_map.get(serial, []),
        })
    return fleet


class MirrorSessionManager:
    """Own multiple scrcpy processes without relying on ANDROID_SERIAL."""

    MODES = {"screen", "camera", "audio", "call", "record"}

    def __init__(self, runner=None, popen=None, desktop=None):
        self._run = runner or subprocess.run
        self._popen = popen or subprocess.Popen
        self._desktop = Path(desktop or Path.home() / "Desktop")
        self._lock = threading.RLock()
        self._sessions = {}

    def _prune(self):
        dead = [key for key, item in self._sessions.items() if item["process"].poll() is not None]
        for key in dead:
            self._sessions.pop(key, None)

    def list(self):
        with self._lock:
            self._prune()
            return [self._public(item) for item in self._sessions.values()]

    @staticmethod
    def _public(item):
        return {
            "id": item["id"],
            "serial": item["serial"],
            "mode": item["mode"],
            "title": item["title"],
            "started_at": item["started_at"],
            "running": item["process"].poll() is None,
        }

    def start(self, serial, mode, config=None, options=None, title=None, tile_index=0):
        serial = validate_serial(serial)
        mode = str(mode or "screen").strip().lower()
        if mode not in self.MODES:
            raise ValueError("Unsupported mirror mode")
        config = dict(config or {})
        options = dict(options or {})
        key = (serial, mode)

        with self._lock:
            self._prune()
            existing = self._sessions.get(key)
            if existing:
                return self._public(existing), False
            if len(self._sessions) >= MAX_MIRROR_SESSIONS:
                raise RuntimeError(f"The {MAX_MIRROR_SESSIONS}-session safety limit has been reached")

            label = str(title or serial).strip()[:60]
            window_title = f"ConnectPhone — {label} — {mode.title()}"
            used_ports = {item["port"] for item in self._sessions.values()}
            port = next((candidate for candidate in range(27300, 27400) if candidate not in used_ports), None)
            if port is None:
                raise RuntimeError("No fleet mirror tunnel port is available")
            command = self.build_command(serial, mode, config, options, window_title, tile_index, port=port)
            process = self._popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            # Voice-call capture may take a little longer to either initialize
            # or return Android's privileged-audio permission error.
            time.sleep(1.0 if mode == "call" else 0.3)
            if process.poll() is not None:
                output = (process.communicate(timeout=1)[0] or b"").decode("utf-8", errors="replace")
                raise RuntimeError(output[-1200:].strip() or "scrcpy exited before opening its window")

            session_id = secrets.token_urlsafe(9)
            item = {
                "id": session_id,
                "serial": serial,
                "mode": mode,
                "title": window_title,
                "started_at": time.time(),
                "process": process,
                "port": port,
            }
            self._sessions[key] = item
            threading.Thread(target=self._drain_output, args=(key, process), daemon=True).start()
            return self._public(item), True

    def build_command(self, serial, mode, config, options, window_title, tile_index=0, port=27300):
        audio_buffer = max(10, min(2000, int(config.get("audio_buffer", 20))))
        x = 40 + (int(tile_index) % 5) * 70
        y = 60 + (int(tile_index) // 5) * 70
        command = [
            "scrcpy", "-s", serial,
            f"--port={int(port)}",
            "--window-title", window_title,
            f"--window-x={x}", f"--window-y={y}",
            f"--audio-buffer={audio_buffer}", "--audio-output-buffer=10",
        ]
        wireless = ":" in serial

        if mode in {"screen", "record"}:
            command.append("--audio-source=output")
            if config.get("screen_off_enabled", False):
                command.append("--turn-screen-off")
            if config.get("stay_awake_enabled", True):
                command.append("--stay-awake")
            if config.get("show_touches_enabled", False):
                command.append("--show-touches")
            keyboard = str(config.get("keyboard_mode", "uhid"))
            if keyboard not in {"uhid", "sdk"}:
                keyboard = "uhid"
            command.append(f"--keyboard={keyboard}")
            codec = str(config.get("camera_codec", "h265"))
            if codec not in {"h264", "h265"}:
                codec = "h264"
            bitrate = "8M" if wireless else "16M"
            command += [f"--video-bit-rate={bitrate}", f"--video-codec={codec}"]
            if wireless:
                command.append("--video-buffer=80")
            if mode == "record":
                stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                safe_serial = re.sub(r"[^A-Za-z0-9._-]", "_", serial)[:40]
                record_path = self._desktop / f"ConnectPhone_{safe_serial}_{stamp}.mp4"
                command.append(f"--record={record_path}")
        elif mode == "camera":
            facing = str(options.get("camera_facing", "back"))
            resolution = str(options.get("resolution", "1080p"))
            if facing not in {"front", "back"} or resolution not in {"720p", "1080p", "4k"}:
                raise ValueError("Invalid camera options")
            size = {"720p": "1280x720", "1080p": "1920x1080", "4k": "3840x2160"}[resolution]
            command += [
                "--video-source=camera", f"--camera-facing={facing}", f"--camera-size={size}",
                "--camera-fps=30", "--stay-awake", "--no-downsize-on-error",
                f"--video-bit-rate={'16M' if resolution == '4k' else '8M'}",
                f"--video-codec={'h265' if resolution == '4k' else 'h264'}",
            ]
            if bool(options.get("no_audio", False)):
                command.append("--no-audio")
            else:
                command += ["--audio-source=mic-camcorder", "--audio-codec=opus", "--audio-bit-rate=128000"]
        elif mode == "audio":
            command += ["--no-video", "--audio-source=output", "--audio-codec=opus", "--audio-bit-rate=128000"]
        elif mode == "call":
            command += [
                "--no-video", "--no-control", "--require-audio",
                "--audio-source=voice-call", "--audio-codec=opus", "--audio-bit-rate=128000",
            ]
        return command

    def _drain_output(self, key, process):
        try:
            for raw_line in iter(process.stdout.readline, b""):
                if raw_line:
                    print(f"[scrcpy:{key[0]}:{key[1]}] {raw_line.decode(errors='replace').strip()}")
        finally:
            with self._lock:
                current = self._sessions.get(key)
                if current and current["process"] is process and process.poll() is not None:
                    self._sessions.pop(key, None)

    def stop(self, session_id="", serial="", mode=""):
        with self._lock:
            self._prune()
            matches = []
            for key, item in self._sessions.items():
                if session_id and item["id"] != session_id:
                    continue
                if serial and item["serial"] != serial:
                    continue
                if mode and item["mode"] != mode:
                    continue
                matches.append((key, item))
            for key, item in matches:
                self._terminate(item["process"])
                self._sessions.pop(key, None)
            return len(matches)

    def stop_all(self):
        return self.stop()

    @staticmethod
    def _terminate(process):
        try:
            process.terminate()
            process.wait(timeout=2)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass


FLEET_CONTROL_ACTIONS = {
    "wake": ["shell", "input", "keyevent", "KEYCODE_WAKEUP"],
    "sleep": ["shell", "input", "keyevent", "KEYCODE_SLEEP"],
    "home": ["shell", "input", "keyevent", "KEYCODE_HOME"],
    "back": ["shell", "input", "keyevent", "KEYCODE_BACK"],
    "volume_up": ["shell", "input", "keyevent", "KEYCODE_VOLUME_UP"],
    "volume_down": ["shell", "input", "keyevent", "KEYCODE_VOLUME_DOWN"],
    "mute": ["shell", "input", "keyevent", "KEYCODE_VOLUME_MUTE"],
}

_ALARM_VOLUME_RE = re.compile(r"volume is (\d+) in range \[(\d+)\.\.(\d+)\]")
_COMPONENT_RE = re.compile(r"(?m)^([A-Za-z0-9._]+)/(?:[A-Za-z0-9._$]+)$")
_DISMISS_TIMER_ACTION_RE = re.compile(r'Action: "([A-Za-z0-9._-]*DISMISS_TIMER)"')
_ALERT_LOCK = threading.RLock()
_ALERT_ORIGINAL_VOLUMES = {}


def _run_device_command(runner, serial, args, timeout=8):
    return runner(
        ["adb", "-s", serial, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def start_emergency_alerts(serials, runner=None):
    """Start a one-second native timer that rings until dismissed."""
    runner = runner or subprocess.run
    targets = [validate_serial(item) for item in serials][:MAX_FLEET_DEVICES]

    def start(serial):
        try:
            volume_result = _run_device_command(
                runner, serial, ["shell", "cmd", "media_session", "volume", "--stream", "4", "--get"]
            )
            match = _ALARM_VOLUME_RE.search((volume_result.stdout or "") + (volume_result.stderr or ""))
            original_volume = int(match.group(1)) if match else None
            maximum_volume = int(match.group(3)) if match else 15

            _run_device_command(runner, serial, ["shell", "input", "keyevent", "KEYCODE_WAKEUP"])
            set_result = _run_device_command(
                runner, serial,
                ["shell", "cmd", "media_session", "volume", "--stream", "4", "--set", str(maximum_volume)],
            )
            if set_result.returncode != 0:
                raise RuntimeError((set_result.stderr or set_result.stdout or "Could not raise alarm volume").strip())

            alert_result = _run_device_command(
                runner, serial,
                [
                    "shell", "am", "start",
                    "-a", "android.intent.action.SET_TIMER",
                    "--ei", "android.intent.extra.alarm.LENGTH", "1",
                    # ADB serializes shell arguments again on the device. Keep
                    # this value whitespace-free so vendor `am` parsers do not
                    # reinterpret a word as the target package.
                    "--es", "android.intent.extra.alarm.MESSAGE", "ConnectPhone-Emergency-Alert",
                    "--ez", "android.intent.extra.alarm.SKIP_UI", "true",
                ],
            )
            alert_output = (alert_result.stdout or "") + (alert_result.stderr or "")
            if alert_result.returncode != 0 or "Error:" in alert_output:
                if original_volume is not None:
                    _run_device_command(
                        runner, serial,
                        ["shell", "cmd", "media_session", "volume", "--stream", "4", "--set", str(original_volume)],
                    )
                raise RuntimeError((alert_result.stderr or alert_result.stdout or "No alarm app accepted the alert").strip())

            with _ALERT_LOCK:
                if original_volume is not None and serial not in _ALERT_ORIGINAL_VOLUMES:
                    _ALERT_ORIGINAL_VOLUMES[serial] = original_volume
            return {"serial": serial, "success": True, "message": "Emergency alert started"}
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            return {"serial": serial, "success": False, "message": str(exc) or "Emergency alert failed"}

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(targets))) as executor:
        return list(executor.map(start, targets))


def stop_emergency_alerts(serials, runner=None):
    """Dismiss ringing timers and restore the previous alarm volume."""
    runner = runner or subprocess.run
    targets = [validate_serial(item) for item in serials][:MAX_FLEET_DEVICES]

    def stop(serial):
        try:
            dismiss_result = _run_device_command(
                runner, serial, ["shell", "am", "start", "-a", "android.intent.action.DISMISS_TIMER"]
            )
            dismiss_output = (dismiss_result.stdout or "") + (dismiss_result.stderr or "")
            dismiss_ok = dismiss_result.returncode == 0 and "Error:" not in dismiss_output

            # Some vendor Clock apps accept SET_TIMER but expose a namespaced
            # dismissal action instead of Android's standard DISMISS_TIMER.
            # Discover that action from the selected Clock package rather than
            # force-stopping the package or hard-coding a vendor name.
            if not dismiss_ok:
                resolve_result = _run_device_command(
                    runner, serial,
                    ["shell", "cmd", "package", "resolve-activity", "--brief", "-a", "android.intent.action.SET_TIMER"],
                )
                component = _COMPONENT_RE.search(resolve_result.stdout or "")
                if component:
                    package_name = component.group(1)
                    package_result = _run_device_command(
                        runner, serial, ["shell", "dumpsys", "package", package_name], timeout=12
                    )
                    actions = [
                        item for item in _DISMISS_TIMER_ACTION_RE.findall(package_result.stdout or "")
                        if item != "android.intent.action.DISMISS_TIMER"
                    ]
                    if actions:
                        dismiss_result = _run_device_command(
                            runner, serial, ["shell", "am", "start", "-a", actions[0]]
                        )
                        dismiss_output = (dismiss_result.stdout or "") + (dismiss_result.stderr or "")
                        dismiss_ok = dismiss_result.returncode == 0 and "Error:" not in dismiss_output

            with _ALERT_LOCK:
                original_volume = _ALERT_ORIGINAL_VOLUMES.pop(serial, None)
            if original_volume is not None:
                _run_device_command(
                    runner, serial,
                    ["shell", "cmd", "media_session", "volume", "--stream", "4", "--set", str(original_volume)],
                )
            message = "Emergency alert stopped" if dismiss_ok else (dismiss_output.strip() or "Timer dismissal failed")
            return {"serial": serial, "success": dismiss_ok, "message": message}
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"serial": serial, "success": False, "message": str(exc) or "Emergency alert stop failed"}

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(targets))) as executor:
        return list(executor.map(stop, targets))


def control_devices(serials, action, runner=None):
    runner = runner or subprocess.run
    if action not in FLEET_CONTROL_ACTIONS:
        raise ValueError("Unsupported fleet control action")
    targets = [validate_serial(item) for item in serials][:MAX_FLEET_DEVICES]
    def send(serial):
        try:
            result = runner(
                ["adb", "-s", serial, *FLEET_CONTROL_ACTIONS[action]],
                capture_output=True,
                text=True,
                timeout=8,
            )
            return {"serial": serial, "success": result.returncode == 0}
        except (OSError, subprocess.TimeoutExpired):
            return {"serial": serial, "success": False}

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(targets))) as executor:
        return list(executor.map(send, targets))
