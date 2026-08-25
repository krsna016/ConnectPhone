"""Safe installer for the bundled Android Companion APK."""

from __future__ import annotations

import os
import subprocess

from core.multi_device import validate_serial


PHONE_APK_PATH = "/sdcard/Download/ConnectPhone-Companion.apk"


def find_companion_apk(project_dir):
    """Find the packaged APK or the most appropriate source-build APK."""
    root = os.path.realpath(str(project_dir))
    candidates = (
        os.path.join(root, "companion", "ConnectPhone-Companion.apk"),
        os.path.join(root, "companion-android", "app", "build", "outputs", "apk", "release", "app-release.apk"),
        os.path.join(root, "companion-android", "app", "build", "outputs", "apk", "debug", "app-debug.apk"),
    )
    for candidate in candidates:
        if os.path.isfile(candidate):
            return os.path.realpath(candidate)
    raise FileNotFoundError("The Companion APK is missing. Build ConnectPhone or run the Android Companion build first.")


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

    lowered = output.lower()
    if "install_failed_update_incompatible" in lowered:
        return {
            "success": False,
            "installed": False,
            "copied": False,
            "message": "A differently signed Companion is already installed. Remove that old app from the phone, then press Install again.",
        }
    if "install_failed_version_downgrade" in lowered:
        return {
            "success": False,
            "installed": False,
            "copied": False,
            "message": "The phone already has a newer Companion version. Keep the newer app or uninstall it before installing this build.",
        }

    # HyperOS commonly requires the user to approve package installation. Keep
    # the fallback recoverable and visible instead of attempting to bypass it.
    pushed = runner(
        ["adb", "-s", serial, "push", apk_path, PHONE_APK_PATH],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if pushed.returncode == 0:
        picker = runner(
            [
                "adb", "-s", serial, "shell", "am", "start",
                "-a", "android.intent.action.OPEN_DOCUMENT",
                "-c", "android.intent.category.OPENABLE",
                "-t", "application/vnd.android.package-archive",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        reason = output[-500:] if output else "Android requires user approval."
        if "install_failed_user_restricted" in lowered:
            guidance = (
                "Xiaomi/Android blocked silent ADB installation. The APK was copied to Downloads. "
                + ("The APK picker is open on the phone—tap ConnectPhone-Companion.apk and approve Install. " if picker.returncode == 0 else "Open Files > Downloads and tap ConnectPhone-Companion.apk. ")
                + "For one-click installs later, enable Developer options > Install via USB."
            )
        else:
            guidance = (
                "Android blocked direct installation. The APK was copied to Downloads. "
                + ("Choose ConnectPhone-Companion.apk in the picker on the phone and approve Install." if picker.returncode == 0 else "Open Files > Downloads, tap ConnectPhone-Companion.apk, and approve Install.")
            )
        return {
            "success": picker.returncode == 0,
            "installed": False,
            "copied": True,
            "picker_opened": picker.returncode == 0,
            "requires_phone_confirmation": True,
            "phone_path": PHONE_APK_PATH,
            "message": f"{guidance} Android response: {reason}",
        }
    push_error = (pushed.stderr or pushed.stdout or "Copy failed").strip()
    return {"success": False, "installed": False, "copied": False, "message": f"Installation failed: {output or push_error}"}
