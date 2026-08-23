"""Background, cancellable ADB transfer queue for local/phone file copies."""

import io
import os
import pathlib
import posixpath
import queue
import re
import shutil
import subprocess
import threading
import time
import uuid

from core.file_manager import resolve_local_path
from core.remote_paths import safe_download_name, valid_remote_path


_PROGRESS_PATTERN = re.compile(r"\[\s*(\d{1,3})%\]")


class TransferManager:
    VALID_DIRECTIONS = {"local_to_phone", "phone_to_local"}
    VALID_CONFLICTS = {"rename", "overwrite", "skip"}

    def __init__(self, runner=None, popen_factory=None):
        self._run = runner or subprocess.run
        self._popen = popen_factory or subprocess.Popen
        self._jobs = {}
        self._queue = queue.Queue()
        self._lock = threading.RLock()
        self._shutdown = threading.Event()
        self._worker = None
        self._processes = {}

    def _ensure_worker(self):
        with self._lock:
            if self._worker and self._worker.is_alive():
                return
            self._shutdown.clear()
            self._worker = threading.Thread(target=self._work_loop, name="ConnectPhone-Transfers", daemon=True)
            self._worker.start()

    @staticmethod
    def _normalize_items(items):
        if not isinstance(items, list) or not items or len(items) > 500:
            raise ValueError("Select between 1 and 500 items")
        normalized = []
        for item in items:
            if isinstance(item, str):
                item = {"path": item}
            if not isinstance(item, dict):
                raise ValueError("Invalid transfer item")
            path = str(item.get("path", "")).strip()
            if not path:
                raise ValueError("Transfer item path is required")
            try:
                size = max(0, int(item.get("size", 0)))
            except (TypeError, ValueError):
                size = 0
            normalized.append({"path": path, "size": size, "is_dir": bool(item.get("is_dir", False))})
        return normalized

    def start(self, *, direction, items, destination, conflict="rename"):
        if direction not in self.VALID_DIRECTIONS:
            raise ValueError("Invalid transfer direction")
        if conflict not in self.VALID_CONFLICTS:
            raise ValueError("Invalid conflict policy")
        normalized = self._normalize_items(items)
        if direction == "local_to_phone":
            if not valid_remote_path(destination):
                raise ValueError("Invalid phone destination")
            for item in normalized:
                item["path"] = str(resolve_local_path(item["path"]))
        else:
            destination = str(resolve_local_path(destination))
            if not pathlib.Path(destination).is_dir():
                raise ValueError("Local destination must be a directory")
            if any(not valid_remote_path(item["path"]) for item in normalized):
                raise ValueError("Invalid phone source path")

        job_id = uuid.uuid4().hex
        now = time.time()
        job = {
            "id": job_id,
            "direction": direction,
            "items": normalized,
            "destination": destination,
            "conflict": conflict,
            "status": "queued",
            "progress": 0,
            "active_progress": 0,
            "active_name": "",
            "completed_items": 0,
            "total_items": len(normalized),
            "errors": [],
            "created_at": now,
            "updated_at": now,
            "cancel_requested": False,
        }
        with self._lock:
            self._jobs[job_id] = job
            self._trim_history_locked()
        self._queue.put(job_id)
        self._ensure_worker()
        return self.get(job_id)

    def _trim_history_locked(self):
        completed = sorted(
            (job for job in self._jobs.values() if job["status"] in {"completed", "failed", "cancelled"}),
            key=lambda job: job["updated_at"],
        )
        while len(self._jobs) > 50 and completed:
            self._jobs.pop(completed.pop(0)["id"], None)

    def get(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def list(self):
        with self._lock:
            return [dict(job) for job in sorted(self._jobs.values(), key=lambda item: item["created_at"], reverse=True)]

    def has_active(self):
        with self._lock:
            return any(job["status"] in {"queued", "running"} for job in self._jobs.values())

    def cancel(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job["status"] in {"completed", "failed", "cancelled"}:
                return False
            job["cancel_requested"] = True
            job["updated_at"] = time.time()
            process = self._processes.get(job_id)
        if process and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
        return True

    def _update(self, job_id, **values):
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.update(values)
                job["updated_at"] = time.time()

    def _work_loop(self):
        while not self._shutdown.is_set():
            try:
                job_id = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                self._execute(job_id)
            finally:
                self._queue.task_done()

    def _execute(self, job_id):
        job = self.get(job_id)
        if not job:
            return
        if job["cancel_requested"]:
            self._update(job_id, status="cancelled")
            return
        self._update(job_id, status="running")
        errors = []
        for index, item in enumerate(job["items"]):
            current = self.get(job_id)
            if not current or current["cancel_requested"] or self._shutdown.is_set():
                self._update(job_id, status="cancelled")
                return
            name = os.path.basename(item["path"].rstrip("/")) or "item"
            self._update(job_id, active_name=name, active_progress=0)
            try:
                copied = self._copy_item(job_id, job, item)
                if copied:
                    completed = index + 1
                else:
                    completed = index + 1
            except Exception as exc:
                errors.append(f"{name}: {exc}")
                completed = index + 1
            self._update(
                job_id,
                completed_items=completed,
                active_progress=100,
                progress=round(completed * 100 / job["total_items"]),
                errors=list(errors),
            )
        self._update(job_id, status="completed" if not errors else "failed", active_name="")

    def _copy_item(self, job_id, job, item):
        if job["direction"] == "local_to_phone":
            source = resolve_local_path(item["path"])
            destination = posixpath.join(job["destination"], source.name)
            destination = self._resolve_remote_conflict(destination, job["conflict"])
            if destination is None:
                return False
            command = ["adb", "push", "-p", str(source), destination]
        else:
            source = item["path"]
            filename = safe_download_name(source, "phone-item")
            destination = pathlib.Path(job["destination"]) / filename
            destination = self._resolve_local_conflict(destination, job["conflict"])
            if destination is None:
                return False
            command = ["adb", "pull", "-a", source, str(destination)]
        self._run_process(job_id, command)
        return True

    def _run_process(self, job_id, command):
        process = self._popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        with self._lock:
            self._processes[job_id] = process
        output = []
        try:
            stream = process.stdout or io.StringIO("")
            while True:
                chunk = stream.read(256)
                if not chunk:
                    break
                output.append(chunk)
                matches = _PROGRESS_PATTERN.findall("".join(output[-3:]))
                if matches:
                    self._update(job_id, active_progress=min(100, int(matches[-1])))
                current = self.get(job_id)
                if current and current["cancel_requested"] and process.poll() is None:
                    process.terminate()
            returncode = process.wait(timeout=10)
            if returncode != 0:
                raise RuntimeError("".join(output).strip()[-500:] or "ADB transfer failed")
        finally:
            with self._lock:
                self._processes.pop(job_id, None)

    def _remote_exists(self, path):
        result = self._run(["adb", "shell", "test", "-e", path], capture_output=True, timeout=5)
        return result.returncode == 0

    def _resolve_remote_conflict(self, destination, policy):
        if not valid_remote_path(destination, destructive=True):
            raise ValueError("Invalid phone destination")
        if not self._remote_exists(destination):
            return destination
        if policy == "skip":
            return None
        if policy == "overwrite":
            result = self._run(["adb", "shell", "rm", "-rf", "--", destination], capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                raise RuntimeError((result.stderr or "Could not replace phone item").strip())
            return destination
        base, extension = posixpath.splitext(destination)
        for index in range(1, 1000):
            candidate = f"{base} ({index}){extension}"
            if not self._remote_exists(candidate):
                return candidate
        raise RuntimeError("Could not choose a unique phone filename")

    @staticmethod
    def _resolve_local_conflict(destination, policy):
        resolve_local_path(str(destination.parent))
        if not destination.exists():
            return destination
        if policy == "skip":
            return None
        if policy == "overwrite":
            if destination.is_dir():
                shutil.rmtree(destination)
            else:
                destination.unlink()
            return destination
        for index in range(1, 1000):
            candidate = destination.with_name(f"{destination.stem} ({index}){destination.suffix}")
            if not candidate.exists():
                return candidate
        raise RuntimeError("Could not choose a unique local filename")

    def shutdown(self):
        self._shutdown.set()
        with self._lock:
            job_ids = [job_id for job_id, job in self._jobs.items() if job["status"] in {"queued", "running"}]
        for job_id in job_ids:
            self.cancel(job_id)
        worker = self._worker
        if worker and worker.is_alive() and worker is not threading.current_thread():
            worker.join(timeout=5)
