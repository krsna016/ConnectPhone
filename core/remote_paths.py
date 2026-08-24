"""Validation helpers for Android shared-storage paths."""

import posixpath
import re
import shlex

REMOTE_ROOTS = ("/sdcard", "/storage")
PROTECTED_ROOTS = {"/sdcard", "/storage", "/storage/emulated", "/storage/emulated/0"}


def valid_remote_path(value, destructive=False):
    if not isinstance(value, str) or not value.startswith("/") or len(value) > 4096:
        return False
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return False
    normalized = posixpath.normpath(value)
    if normalized != value.rstrip("/") or not any(normalized == root or normalized.startswith(root + "/") for root in REMOTE_ROOTS):
        return False
    return not destructive or normalized not in PROTECTED_ROOTS


def safe_download_name(remote_path, fallback="download"):
    name = posixpath.basename(remote_path.rstrip("/")) or fallback
    return re.sub(r"[^\w.() -]", "_", name, flags=re.UNICODE)[:180] or fallback


def adb_shell_command(*arguments):
    """Build one safely quoted command string for Android's remote shell.

    ADB joins arguments following ``adb shell`` before the device shell parses
    them, so a local subprocess argument list alone does not protect filenames
    containing shell metacharacters.
    """
    if not arguments or any(not isinstance(item, str) or "\x00" in item for item in arguments):
        raise ValueError("Invalid Android shell command")
    return ["adb", "shell", shlex.join(arguments)]
