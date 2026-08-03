"""Safe, bounded retention for local trace runs.

Only direct children of the configured trace root whose ``run.json`` names
that same directory are considered trace runs.  Symlinks and unrelated
directories are never followed or removed. The current run and every run
owned by a live process are protected.
"""
from __future__ import annotations

import ctypes
import json
import logging
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import TraceRetentionPolicy

_log = logging.getLogger(__name__)

# A hard-killed launch has no ``ended_at``.  It becomes eligible only after a
# generous grace window and only when its recorded PID is no longer alive.
_STALE_UNFINISHED_SECONDS = 7 * 24 * 60 * 60


@dataclass(frozen=True)
class _RunInfo:
    path: Path
    run_id: str
    started_at: float
    ended_at: float | None
    updated_at: float
    size_bytes: int
    pid: int | None


def prune_trace_runs(
    root: Path,
    *,
    current_run_id: str,
    policy: TraceRetentionPolicy,
    now: float | None = None,
) -> dict[str, Any]:
    """Apply ``policy`` and return a compact maintenance summary.

    Failures are reported and logged, never raised: retention is a support
    feature and must not prevent tracing or application startup.
    """
    root = Path(root)
    result: dict[str, Any] = {
        "policy": policy.to_dict(),
        "recognized_runs": 0,
        "protected_runs": 0,
        "ignored_entries": 0,
        "removed_runs": 0,
        "removed_bytes": 0,
        "removed_by_reason": {},
        "failures": 0,
        "remaining_runs": 0,
        "remaining_bytes": 0,
        "limits_satisfied": True,
    }
    if not root.exists():
        return result

    try:
        root_resolved = root.resolve(strict=True)
        children = list(root.iterdir())
    except OSError as exc:
        _log.warning("Could not inspect trace retention root %s: %s", root, exc)
        result["failures"] = 1
        result["limits_satisfied"] = False
        return result

    infos: list[_RunInfo] = []
    for child in children:
        info = _read_run_info(child, root_resolved)
        if info is None:
            result["ignored_entries"] += 1
            continue
        infos.append(info)
    result["recognized_runs"] = len(infos)

    timestamp = time.time() if now is None else float(now)
    protected: set[Path] = set()
    eligible: dict[Path, _RunInfo] = {}
    for info in infos:
        age_seconds = max(0.0, timestamp - _retention_timestamp(info))
        # A sibling recorder marks ended_at before its writer finishes the
        # drain/final-metadata sequence. PID liveness is therefore stronger
        # protection than the ended flag: never prune a run owned by any live
        # process, even if its metadata already says ended.
        if info.run_id == current_run_id or process_is_alive(info.pid):
            protected.add(info.path)
        elif info.ended_at is not None:
            eligible[info.path] = info
        elif (
            age_seconds >= _STALE_UNFINISHED_SECONDS
            and not process_is_alive(info.pid)
        ):
            eligible[info.path] = info
        else:
            protected.add(info.path)
    result["protected_runs"] = len(protected)

    remaining: dict[Path, _RunInfo] = {info.path: info for info in infos}

    def remove(info: _RunInfo, reason: str) -> bool:
        # Re-check the containment/symlink invariants immediately before the
        # destructive operation; the filesystem may have changed since scan.
        try:
            current = _read_run_info(info.path, root_resolved)
            if current is None or current.run_id != info.run_id:
                raise OSError("trace run changed or is no longer recognized")
            current_age = max(
                0.0, timestamp - _retention_timestamp(current)
            )
            if current.run_id == current_run_id or process_is_alive(current.pid):
                protected.add(current.path)
                eligible.pop(info.path, None)
                return False
            if (
                current.ended_at is None
                and current_age < _STALE_UNFINISHED_SECONDS
            ):
                protected.add(current.path)
                eligible.pop(info.path, None)
                return False
            if (
                current.path.is_symlink()
                or current.path.resolve().parent != root_resolved
            ):
                raise OSError("trace run is no longer a direct, non-symlink child")
            shutil.rmtree(current.path)
        except OSError as exc:
            _log.warning("Could not prune trace run %s: %s", info.path, exc)
            result["failures"] += 1
            eligible.pop(info.path, None)
            return False
        remaining.pop(info.path, None)
        eligible.pop(info.path, None)
        result["removed_runs"] += 1
        result["removed_bytes"] += current.size_bytes
        reasons = result["removed_by_reason"]
        reasons[reason] = int(reasons.get(reason, 0)) + 1
        return True

    # Age is an independent ceiling.
    if policy.max_age_days > 0:
        max_age_seconds = policy.max_age_days * 24 * 60 * 60
        for info in sorted(eligible.values(), key=_retention_timestamp):
            if timestamp - _retention_timestamp(info) > max_age_seconds:
                remove(info, "age")

    # Count and bytes retain the newest eligible runs.  Protected runs count
    # toward each total, but are never sacrificed merely to satisfy a limit.
    if policy.max_runs > 0:
        for info in sorted(eligible.values(), key=_retention_timestamp):
            if len(remaining) <= policy.max_runs:
                break
            remove(info, "count")

    if policy.max_bytes > 0:
        total_bytes = sum(info.size_bytes for info in remaining.values())
        for info in sorted(eligible.values(), key=_retention_timestamp):
            if total_bytes <= policy.max_bytes:
                break
            if remove(info, "bytes"):
                total_bytes -= info.size_bytes

    remaining_bytes = sum(info.size_bytes for info in remaining.values())
    result["remaining_runs"] = len(remaining)
    result["remaining_bytes"] = remaining_bytes
    age_satisfied = True
    if policy.max_age_days > 0:
        max_age_seconds = policy.max_age_days * 24 * 60 * 60
        age_satisfied = all(
            timestamp - _retention_timestamp(info) <= max_age_seconds
            for info in remaining.values()
        )
    result["limits_satisfied"] = (
        age_satisfied
        and (policy.max_runs <= 0 or len(remaining) <= policy.max_runs)
        and (policy.max_bytes <= 0 or remaining_bytes <= policy.max_bytes)
    )
    return result


def _read_run_info(path: Path, root_resolved: Path) -> _RunInfo | None:
    try:
        if path.is_symlink() or not path.is_dir():
            return None
        if path.resolve().parent != root_resolved:
            return None
        meta_path = path / "run.json"
        if meta_path.is_symlink() or not meta_path.is_file():
            return None
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if not isinstance(meta, dict):
            return None
        run_id = str(meta.get("run_id") or "")
        if not run_id or run_id != path.name:
            return None
        size_bytes, newest_mtime = _directory_usage(path)
        started_at = _number(meta.get("started_at"), newest_mtime)
        ended_at = _optional_number(meta.get("ended_at"))
        updated_at = max(newest_mtime, ended_at or 0.0, started_at)
        environment = meta.get("environment")
        raw_pid = environment.get("pid") if isinstance(environment, dict) else None
        pid = (
            int(raw_pid)
            if isinstance(raw_pid, int)
            and not isinstance(raw_pid, bool)
            and raw_pid > 0
            else None
        )
        return _RunInfo(
            path=path,
            run_id=run_id,
            started_at=started_at,
            ended_at=ended_at,
            updated_at=updated_at,
            size_bytes=size_bytes,
            pid=pid,
        )
    except (OSError, ValueError, TypeError):
        return None


def _directory_usage(path: Path) -> tuple[int, float]:
    total = 0
    newest = path.stat().st_mtime
    for directory, dirnames, filenames in os.walk(path, followlinks=False):
        base = Path(directory)
        # Never descend through a link planted inside a trace run.
        dirnames[:] = [name for name in dirnames if not (base / name).is_symlink()]
        for name in filenames:
            item = base / name
            if item.is_symlink():
                continue
            try:
                stat = item.stat()
            except OSError:
                continue
            total += max(0, stat.st_size)
            newest = max(newest, stat.st_mtime)
    return total, newest


def _retention_timestamp(info: _RunInfo) -> float:
    return info.ended_at if info.ended_at is not None else info.updated_at


def _number(value: Any, default: float) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return float(default)


def _optional_number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def process_is_alive(pid: int | None) -> bool:
    """Best-effort liveness check that never sends a Windows process signal."""
    if pid is None:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        # ``os.kill(pid, 0)`` is not used on Windows: its implementation is
        # not the POSIX signal-0 existence probe on every supported Python.
        try:
            from ctypes import wintypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            open_process = kernel32.OpenProcess
            open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            open_process.restype = wintypes.HANDLE
            close_handle = kernel32.CloseHandle
            close_handle.argtypes = [wintypes.HANDLE]
            close_handle.restype = wintypes.BOOL
            handle = open_process(0x1000, False, pid)
            if handle:
                close_handle(handle)
                return True
            # Access denied also proves that the process exists.
            return int(kernel32.GetLastError()) == 5
        except (AttributeError, OSError):
            return True  # uncertainty protects data
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True
