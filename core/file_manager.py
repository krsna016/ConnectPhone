"""Safe local-file and Android shared-storage helpers for the dual-pane UI."""

import os
import pathlib
import posixpath
import re
import shutil
import subprocess
import time

from core.remote_paths import adb_shell_command, valid_remote_path


_SAFE_NAME = re.compile(r"^[^/\\\x00-\x1f\x7f]+$")


def valid_item_name(value):
    return (
        isinstance(value, str)
        and value not in {"", ".", ".."}
        and len(value) <= 255
        and bool(_SAFE_NAME.fullmatch(value))
    )


def local_roots():
    home = pathlib.Path.home().resolve()
    candidates = [
        ("Home", home),
        ("Desktop", home / "Desktop"),
        ("Documents", home / "Documents"),
        ("Downloads", home / "Downloads"),
        ("Pictures", home / "Pictures"),
        ("Movies", home / "Movies"),
        ("Music", home / "Music"),
    ]
    roots = [
        {"name": name, "path": str(path), "kind": "folder"}
        for name, path in candidates
        if path.exists() and path.is_dir()
    ]
    volumes = pathlib.Path("/Volumes")
    if volumes.is_dir():
        for path in sorted(volumes.iterdir(), key=lambda item: item.name.lower()):
            try:
                resolved = path.resolve()
                if path.is_dir() and volumes in resolved.parents:
                    roots.append({"name": path.name, "path": str(resolved), "kind": "volume"})
            except OSError:
                continue
    return roots


def resolve_local_path(value, *, destructive=False, must_exist=True):
    if not isinstance(value, str) or not value or "\x00" in value or len(value) > 4096:
        raise ValueError("Invalid local path")
    candidate = pathlib.Path(value).expanduser()
    try:
        resolved = candidate.resolve(strict=must_exist)
    except (OSError, RuntimeError) as exc:
        raise ValueError("Local path is unavailable") from exc

    home = pathlib.Path.home().resolve()
    volumes = pathlib.Path("/Volumes").resolve()
    allowed = resolved == home or home in resolved.parents or resolved == volumes or volumes in resolved.parents
    if not allowed:
        raise ValueError("Local path is outside allowed folders")
    if destructive and resolved in {home, volumes}:
        raise ValueError("Refusing to modify a protected local root")
    return resolved


def list_local_files(value, *, show_hidden=False):
    directory = resolve_local_path(value)
    if not directory.is_dir():
        raise ValueError("Local path is not a directory")
    items = []
    with os.scandir(directory) as entries:
        for entry in entries:
            if not show_hidden and entry.name.startswith("."):
                continue
            try:
                path = resolve_local_path(entry.path)
                stat = entry.stat(follow_symlinks=False)
                is_dir = entry.is_dir(follow_symlinks=True)
            except (OSError, ValueError):
                continue
            items.append({
                "name": entry.name,
                "path": str(path),
                "is_dir": is_dir,
                "is_symlink": entry.is_symlink(),
                "size": 0 if is_dir else int(stat.st_size),
                "mtime": int(stat.st_mtime),
            })
    items.sort(key=lambda item: (not item["is_dir"], item["name"].lower()))
    return {"path": str(directory), "files": items}


def create_local_folder(parent, name):
    if not valid_item_name(name):
        raise ValueError("Invalid folder name")
    parent_path = resolve_local_path(parent)
    destination = resolve_local_path(str(parent_path / name), must_exist=False)
    destination.mkdir(mode=0o755, exist_ok=False)
    return str(destination)


def rename_local_item(source, new_name):
    if not valid_item_name(new_name):
        raise ValueError("Invalid item name")
    source_path = resolve_local_path(source, destructive=True)
    destination = resolve_local_path(str(source_path.parent / new_name), must_exist=False)
    if destination.exists():
        raise FileExistsError("An item with that name already exists")
    source_path.rename(destination)
    return str(destination)


def move_local_item_to_trash(source):
    source_path = resolve_local_path(source, destructive=True)
    trash = pathlib.Path.home() / ".Trash"
    trash.mkdir(mode=0o700, exist_ok=True)
    destination = trash / source_path.name
    if destination.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        destination = trash / f"{source_path.stem}-{stamp}{source_path.suffix}"
    shutil.move(str(source_path), str(destination))
    return str(destination)


def rename_remote_item(source, new_name, runner=subprocess.run):
    if not valid_remote_path(source, destructive=True) or not valid_item_name(new_name):
        raise ValueError("Invalid remote rename")
    destination = posixpath.join(posixpath.dirname(source), new_name)
    if not valid_remote_path(destination, destructive=True):
        raise ValueError("Invalid remote destination")
    exists = runner(adb_shell_command("test", "-e", destination), capture_output=True, timeout=5)
    if exists.returncode == 0:
        raise FileExistsError("An item with that name already exists")
    result = runner(adb_shell_command("mv", "--", source, destination), capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "Remote rename failed").strip())
    return destination


def list_phone_storages(runner=subprocess.run):
    script = 'for d in /storage/emulated/0 /storage/*; do [ -d "$d" ] && [ -r "$d" ] && printf "%s\\n" "$d"; done'
    result = runner(["adb", "shell", "sh", "-c", script], capture_output=True, text=True, timeout=8)
    paths = []
    for raw in (result.stdout or "").splitlines():
        path = posixpath.normpath(raw.strip())
        if path == "/storage/emulated/0":
            path = "/sdcard"
        if not valid_remote_path(path) or path in {"/storage/emulated", "/storage/self"} or path in paths:
            continue
        paths.append(path)
    if "/sdcard" not in paths:
        paths.insert(0, "/sdcard")
    return [
        {
            "name": "Internal storage" if path == "/sdcard" else f"SD card ({posixpath.basename(path)})",
            "path": path,
            "kind": "internal" if path == "/sdcard" else "removable",
        }
        for path in paths
    ]
