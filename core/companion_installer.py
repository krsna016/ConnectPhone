"""Safe installer for the bundled Android Companion APK."""

from __future__ import annotations

import os
import subprocess

from core.multi_device import validate_serial


PHONE_APK_PATH = "/sdcard/Download/ConnectPhone-Companion.apk"


def install_companion(serial, apk_path, runner=subprocess.run):
    serial = validate_serial(serial)
    apk_path = os.path.realpath(str(apk_path))
    if not os.path.isfile(apk_path) or not apk_path.lower().endswith(".apk"):
        raise FileNotFoundError("The bundled Companion APK is missing. Rebuild ConnectPhone first.")

    result = runner(
        ["adb", "-s", serial, "install", "-r", apk_path],
        capture_output=True,
        text=True,
        timeout=120,
    )
    output = "\n".join(part.strip() for part in (result.stdout or "", result.stderr or "") if part.strip())
    if result.returncode == 0 and "success" in output.lower():
        return {"success": True, "installed": True, "message": "ConnectPhone Companion installed successfully."}

    # HyperOS commonly requires the user to approve package installation. Keep
    # the fallback recoverable and visible instead of attempting to bypass it.
    pushed = runner(
        ["adb", "-s", serial, "push", apk_path, PHONE_APK_PATH],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if pushed.returncode == 0:
        reason = output[-500:] if output else "Android requires user approval."
        return {
            "success": False,
            "installed": False,
            "copied": True,
            "phone_path": PHONE_APK_PATH,
            "message": f"Android blocked direct installation: {reason} Open Files > Downloads > ConnectPhone-Companion.apk and tap Install.",
        }
    push_error = (pushed.stderr or pushed.stdout or "Copy failed").strip()
    return {"success": False, "installed": False, "copied": False, "message": f"Installation failed: {output or push_error}"}
