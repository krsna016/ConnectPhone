"""Small macOS Keychain wrapper for ConnectPhone secrets."""

import getpass
import shutil
import subprocess

SERVICE = "com.krsna016.ConnectPhone"


def _account():
    return getpass.getuser()


def available():
    return shutil.which("security") is not None


def get(name):
    if not available():
        return None
    result = subprocess.run(
        ["security", "find-generic-password", "-a", _account(), "-s", f"{SERVICE}.{name}", "-w"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    return result.stdout.rstrip("\n") if result.returncode == 0 else None


def set(name, value):
    if not available():
        raise RuntimeError("macOS Keychain is unavailable")
    value = "" if value is None else str(value)
    result = subprocess.run(
        ["security", "add-generic-password", "-a", _account(), "-s", f"{SERVICE}.{name}", "-w", value, "-U"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Keychain write failed")


def delete(name):
    if not available():
        return
    subprocess.run(
        ["security", "delete-generic-password", "-a", _account(), "-s", f"{SERVICE}.{name}"],
        capture_output=True,
        text=True,
        timeout=5,
    )
