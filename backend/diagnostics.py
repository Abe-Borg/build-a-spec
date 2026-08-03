"""Always-on local diagnostics: logging, crash capture, snapshots, bundle.

Build-a-Spec native (no Spec Critic source). Two halves:

1. Process-wide observability every run gets by default — a rotating log
   file beside the trace directory, ``faulthandler`` + exception hooks,
   and an unclean-shutdown marker. The packaged windowed build points
   stdout/stderr at ``os.devnull`` (``main._ensure_std_streams``), so
   without this file nothing the app or uvicorn prints survives; the log
   file IS the record.

2. Read-only helpers behind the ``/api/diagnostics*`` endpoints and the
   Settings → Developer tools modal: environment/session snapshot, log
   tail, trace-run inventory, and the downloadable support bundle.

Each launch writes inside
``<user_state_dir>/BuildASpec/logs/process-<launch-id>``. The parent is the
*state* root beside ``traces/``, deliberately not
:func:`app_paths.app_config_dir` (the *config* root holding the key file and
templates). On Windows both roots resolve to ``%LOCALAPPDATA%\\BuildASpec``,
so users find every forensic artifact in one folder.

Key material never enters a log line, the snapshot, or the bundle: the
snapshot reports :func:`api_key_store.key_status` (masked tail only) and
the whole payload passes ``tracing.redaction.scrub_data`` on the way
out. Document text is a different contract — traces deliberately contain
it (that is what makes them useful as an audit record), so the bundle
does too, and the UI copy says so before the download.
"""
from __future__ import annotations

import atexit
import datetime as _dt
import faulthandler
import hashlib
import json
import logging
import logging.handlers
import os
import platform
import re
import shutil
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from platformdirs import user_state_dir

ENV_LOG = "BUILD_A_SPEC_LOG"
ENV_LOG_LEVEL = "BUILD_A_SPEC_LOG_LEVEL"
ENV_LOG_DIR = "BUILD_A_SPEC_LOG_DIR"
ENV_LOG_MAX_RUNS = "BUILD_A_SPEC_LOG_MAX_RUNS"
ENV_LOG_MAX_AGE_DAYS = "BUILD_A_SPEC_LOG_MAX_AGE_DAYS"
ENV_LOG_MAX_MIB = "BUILD_A_SPEC_LOG_MAX_MIB"

LOG_FILENAME = "buildaspec.log"
CRASH_FILENAME = "crash-faulthandler.log"
RUN_MARKER_FILENAME = "run-marker.json"

_LOG_MAX_BYTES = 10 * 1024 * 1024
_LOG_BACKUP_COUNT = 5
# Per-launch directories make rotation safe when two desktop instances run at
# once. Independent storage ceilings keep those directories bounded over time;
# zero explicitly disables one ceiling.
DEFAULT_LOG_MAX_RUNS = 50
DEFAULT_LOG_MAX_AGE_DAYS = 30
DEFAULT_LOG_MAX_MIB = 256
_STALE_UNCLEAN_SECONDS = 7 * 24 * 60 * 60
_PROCESS_RUN_ID_RE = re.compile(r"^process-[0-9a-f]{32}$")
# tail_log never reads more than this from the end of the file, so the
# endpoint's cost is bounded regardless of how large the log grows.
_TAIL_READ_CAP = 2 * 1024 * 1024
# A support bundle carries enough of recent prior runs to correlate an incident
# across a restart, without silently turning three historically deep traces
# into an unbounded archive.
_PRIOR_RUN_COUNT = 3
_PRIOR_CONTEXT_BYTES = 512 * 1024
_INCIDENT_TAIL_BYTES = 512 * 1024
_INCIDENT_LIMIT = 50
_RUN_META_READ_CAP = 512 * 1024
_BUNDLE_LOG_TEXT_BYTES = 12 * 1024 * 1024

_DISABLE_TOKENS = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True)
class LogRetentionPolicy:
    """Independent per-launch log-directory storage ceilings."""

    max_runs: int = DEFAULT_LOG_MAX_RUNS
    max_age_days: int = DEFAULT_LOG_MAX_AGE_DAYS
    max_bytes: int = DEFAULT_LOG_MAX_MIB * 1024 * 1024

    def to_dict(self) -> dict[str, int]:
        return asdict(self)

# Chatty third-party loggers held above DEBUG so "root at DEBUG" means
# "the app at DEBUG", not a firehose of HTTP wire dumps.
_TAMED_LOGGERS: dict[str, int] = {
    "httpx": logging.WARNING,
    "httpcore": logging.WARNING,
    "urllib3": logging.WARNING,
    "keyring": logging.WARNING,
    "anthropic": logging.INFO,
    "uvicorn": logging.INFO,
    "uvicorn.error": logging.INFO,
    "uvicorn.access": logging.WARNING,
    "asyncio": logging.WARNING,
    "pywebview": logging.INFO,
}

_LOG_FORMAT = (
    "%(asctime)s.%(msecs)03d %(levelname)-8s %(name)s "
    "[pid=%(process_id)s version=%(app_version)s trace=%(trace_run_id)s "
    "thread=%(threadName)s] %(message)s"
)
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"

_log = logging.getLogger("buildaspec")

_INIT_LOCK = threading.Lock()
_INITIALIZED = False
_HANDLER: logging.Handler | None = None
_PREV_ROOT_LEVEL: int | None = None
_CRASH_HANDLE: Any = None
_PREV_SYS_EXCEPTHOOK: Any = None
_PREV_THREADING_EXCEPTHOOK: Any = None
_HOOKS_INSTALLED = False
_PREVIOUS_RUN_MARKER: dict[str, Any] | None = None
_LAST_LOG_RETENTION: dict[str, Any] | None = None
_LOG_INIT_ATTEMPTED = False
_LOG_INIT_SUCCEEDED: bool | None = None
_LOG_INIT_FAILURE_TYPE: str | None = None
_LOG_INIT_FAILURE_COUNT = 0
_LOG_INIT_LAST_FAILURE_AT: float | None = None

_PROCESS_RUN_ID = f"process-{uuid.uuid4().hex}"
_RUN_STARTED_AT = time.time()
_SERVER_IDENTITY_LOCK = threading.Lock()
_SERVER_IDENTITY: dict[str, Any] = {}


def set_runtime_server_identity(
    *, host: str, port: int, boot_nonce: str
) -> None:
    """Publish a correlation-safe launch identity.

    The raw nonce is bootstrap trust material. Diagnostics retain only its
    one-way fingerprint so records can be correlated without becoming a
    credential replay source.
    """
    nonce_fingerprint = hashlib.sha256(
        str(boot_nonce).encode("utf-8", errors="replace")
    ).hexdigest()
    identity = {
        "host": str(host)[:100],
        "bound_port": int(port),
        "boot_nonce_fingerprint": nonce_fingerprint,
    }
    with _SERVER_IDENTITY_LOCK:
        _SERVER_IDENTITY.clear()
        _SERVER_IDENTITY.update(identity)
    # init_logging creates the active marker before the listener is known.
    # Refresh it in place now, preserving the process/run start identity.
    try:
        if _INITIALIZED and log_enabled():
            _write_run_marker(clean=False)
    except Exception:  # noqa: BLE001 - diagnostics never block startup
        pass


def runtime_server_identity() -> dict[str, Any]:
    with _SERVER_IDENTITY_LOCK:
        return dict(_SERVER_IDENTITY)


class _LogContextFilter(logging.Filter):
    """Attach cheap correlation fields to every file-log record.

    A startup banner alone is insufficient once rotated logs from several
    processes share a bundle.  The recorder is process-global and the import
    is cached after the first line, so this adds no I/O and only a few
    attribute reads per record.  Before tracing starts, ``trace=-`` states the
    absence explicitly instead of making the formatter fail.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.process_id = os.getpid()
        try:
            from . import settings

            record.app_version = settings.VERSION
        except Exception:  # noqa: BLE001 - logging must remain fail-open
            record.app_version = "-"
        try:
            from .tracing.recorder import get_recorder

            recorder = get_recorder()
            record.trace_run_id = recorder.run_id if recorder is not None else "-"
        except Exception:  # noqa: BLE001
            record.trace_run_id = "-"
        return True


class _RedactingFormatter(logging.Formatter):
    """Format a private clone whose free text has credential shapes removed.

    The trace serializer already scrubs structured records, but normal Python
    log messages and exception text take a separate path straight to disk.
    Formatting a clone keeps other handlers' records untouched while ensuring
    a pasted provider key cannot survive in the activity log or support
    bundle.
    """

    @staticmethod
    def _redact(value: str) -> str:
        from .tracing.redaction import redact_text

        # A lone UTF-16 surrogate is legal in a Python/JSON string but cannot
        # be encoded by a strict UTF-8 file handler. Preserve it visibly as a
        # backslash escape instead of letting logging drop the whole record.
        return (
            redact_text(value)
            .encode("utf-8", errors="backslashreplace")
            .decode("utf-8")
        )

    def format(self, record: logging.LogRecord) -> str:
        clone = logging.makeLogRecord(record.__dict__.copy())
        try:
            clone.msg = self._redact(record.getMessage())
        except Exception:  # noqa: BLE001 - logging must remain fail-open
            clone.msg = "<unformattable log record>"
        clone.args = ()
        # Another handler may already have populated this cache with the raw
        # exception. Force our redacting formatException path to run.
        clone.exc_text = None
        return super().format(clone)

    def formatException(self, exc_info: Any) -> str:  # noqa: N802
        return self._redact(super().formatException(exc_info))

    def formatStack(self, stack_info: str) -> str:  # noqa: N802
        return self._redact(super().formatStack(stack_info))


def log_enabled() -> bool:
    """Default ON. Disable with ``BUILD_A_SPEC_LOG=0`` (the trace idiom)."""
    raw = os.environ.get(ENV_LOG)
    if raw is None:
        return True
    return raw.strip().lower() not in _DISABLE_TOKENS


def configured_level() -> int:
    """``BUILD_A_SPEC_LOG_LEVEL`` (default DEBUG; garbage degrades to DEBUG)."""
    raw = (os.environ.get(ENV_LOG_LEVEL) or "").strip().upper()
    if raw:
        level = logging.getLevelName(raw)
        if isinstance(level, int):
            return level
    return logging.DEBUG


def _env_nonnegative_int(name: str, default: int) -> int:
    """Read a retention integer without letting bad env break startup."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw.strip())
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


def log_retention_policy() -> LogRetentionPolicy:
    """Return configured local log-run ceilings.

    Invalid and negative values fall back to safe defaults. Zero disables
    only the corresponding ceiling.
    """
    max_mib = _env_nonnegative_int(ENV_LOG_MAX_MIB, DEFAULT_LOG_MAX_MIB)
    return LogRetentionPolicy(
        max_runs=_env_nonnegative_int(ENV_LOG_MAX_RUNS, DEFAULT_LOG_MAX_RUNS),
        max_age_days=_env_nonnegative_int(
            ENV_LOG_MAX_AGE_DAYS, DEFAULT_LOG_MAX_AGE_DAYS
        ),
        max_bytes=max_mib * 1024 * 1024,
    )


def log_dir() -> Path:
    """``<state dir>/BuildASpec/logs`` (override: ``BUILD_A_SPEC_LOG_DIR``)."""
    override = os.environ.get(ENV_LOG_DIR)
    if override:
        return Path(os.path.expanduser(os.path.expandvars(override)))
    return Path(user_state_dir("BuildASpec", appauthor=False)) / "logs"


def current_log_dir() -> Path:
    """The private directory for this process launch."""
    return log_dir() / _PROCESS_RUN_ID


def current_log_file() -> Path | None:
    """The active log path, or None when logging is disabled."""
    if not log_enabled():
        return None
    return current_log_dir() / LOG_FILENAME


def init_logging(*, force: bool = False) -> Path | None:
    """Attach the rotating file handler to the root logger. Idempotent.

    Returns the log path, or None when disabled. ``force=True`` tears the
    previous configuration down first (test hygiene — the
    ``reset_thinking_display_probe`` posture for process-global state).
    Never touches stdout/stderr: the handler is a file, so
    ``main._ensure_std_streams`` ordering is irrelevant.
    """
    global _INITIALIZED, _HANDLER, _PREV_ROOT_LEVEL
    global _LOG_INIT_ATTEMPTED, _LOG_INIT_SUCCEEDED
    global _LOG_INIT_FAILURE_TYPE, _LOG_INIT_FAILURE_COUNT
    global _LOG_INIT_LAST_FAILURE_AT
    with _INIT_LOCK:
        if _INITIALIZED and not force:
            return current_log_file()
        if force:
            _teardown_locked()
        _LOG_INIT_ATTEMPTED = True
        if not log_enabled():
            _LOG_INIT_SUCCEEDED = None
            _INITIALIZED = True
            return None
        handler: logging.Handler | None = None
        try:
            directory = current_log_dir()
            directory.mkdir(parents=True, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(
                directory / LOG_FILENAME,
                maxBytes=_LOG_MAX_BYTES,
                backupCount=_LOG_BACKUP_COUNT,
                encoding="utf-8",
                # Open during initialization so capture is never reported as
                # active when the directory exists but the file cannot be
                # created or opened.
                delay=False,
            )
            handler.setFormatter(
                _RedactingFormatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT)
            )
            handler.addFilter(_LogContextFilter())
            root = logging.getLogger()
            root.addHandler(handler)
            _PREV_ROOT_LEVEL = root.level
            root.setLevel(configured_level())
            for name, level in _TAMED_LOGGERS.items():
                logging.getLogger(name).setLevel(level)
            _HANDLER = handler
            _LOG_INIT_SUCCEEDED = True
            _LOG_INIT_FAILURE_TYPE = None
            _INITIALIZED = True
        except Exception as exc:  # noqa: BLE001 — diagnostics must never sink the app
            if handler is not None:
                try:
                    logging.getLogger().removeHandler(handler)
                    handler.close()
                except Exception:  # noqa: BLE001
                    pass
            _HANDLER = None
            if _PREV_ROOT_LEVEL is not None:
                try:
                    logging.getLogger().setLevel(_PREV_ROOT_LEVEL)
                except Exception:  # noqa: BLE001
                    pass
                _PREV_ROOT_LEVEL = None
            _LOG_INIT_SUCCEEDED = False
            _LOG_INIT_FAILURE_TYPE = type(exc).__name__
            _LOG_INIT_FAILURE_COUNT += 1
            _LOG_INIT_LAST_FAILURE_AT = time.time()
            _INITIALIZED = True
            try:
                fallback = getattr(sys, "__stderr__", None)
                if fallback is not None:
                    # Exception class only: never echo a path, credential, or
                    # user-derived message through an unredacted fallback.
                    fallback.write(
                        "Build-a-Spec diagnostics logging failed: "
                        f"{_LOG_INIT_FAILURE_TYPE}\n"
                    )
                    fallback.flush()
            except Exception:  # noqa: BLE001
                pass
            return None
        _install_crash_capture_locked()
        return directory / LOG_FILENAME


def log_startup_banner() -> None:
    """One block of INFO lines identifying the run. Never raises."""
    try:
        from . import settings
        from .tracing import config as trace_config

        offset = _dt.datetime.now().astimezone().strftime("%z")
        server_identity = runtime_server_identity()
        bound_port = int(server_identity.get("bound_port") or settings.PORT)
        _log.info("diagnostic process run id: %s", _PROCESS_RUN_ID)
        _log.info(
            "%s %s starting — platform=%s python=%s frozen=%s pid=%d "
            "port=%d utc_offset=%s",
            settings.APP_NAME,
            settings.VERSION,
            platform.platform(),
            platform.python_version(),
            bool(getattr(sys, "frozen", False)),
            os.getpid(),
            bound_port,
            offset,
        )
        _log.info(
            "models: interview=%s research=%s qc=%s",
            settings.INTERVIEW_MODEL,
            settings.RESEARCH_MODEL,
            settings.QC_MODEL,
        )
        _log.info(
            "tracing: enabled=%s level=%s root=%s",
            trace_config.trace_enabled(),
            trace_config.current_capture_level(),
            trace_config.default_trace_root(),
        )
        _log.info(
            "logging: level=%s file=%s",
            logging.getLevelName(configured_level()),
            current_log_file(),
        )
    except Exception:  # noqa: BLE001
        pass


def mark_clean_shutdown() -> None:
    """Rewrite the run marker as clean; registered atexit. Never raises."""
    try:
        _write_run_marker(clean=True)
    except Exception:  # noqa: BLE001
        pass


@dataclass(frozen=True)
class _LogRunInfo:
    path: Path
    run_id: str
    started_at: float
    ended_at: float | None
    updated_at: float
    size_bytes: int
    pid: int | None
    clean: bool
    marker: dict[str, Any]


def _process_is_alive(pid: int | None) -> bool:
    """Use the trace retention liveness verdict; uncertainty protects data."""
    if pid is None:
        return False
    try:
        from .tracing.retention import process_is_alive

        return process_is_alive(pid)
    except Exception:  # noqa: BLE001 - a failed probe must not enable deletion
        return True


def _number(value: Any, default: float) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return float(default)


def _optional_number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _log_directory_usage(path: Path) -> tuple[int, float]:
    total = 0
    newest = path.stat().st_mtime
    for directory, dirnames, filenames in os.walk(path, followlinks=False):
        base = Path(directory)
        dirnames[:] = [
            name for name in dirnames if not (base / name).is_symlink()
        ]
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


def _read_marker_path(path: Path) -> dict[str, Any] | None:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        if path.stat().st_size > 64 * 1024:
            return None
        marker = json.loads(path.read_text(encoding="utf-8"))
        return marker if isinstance(marker, dict) else None
    except (OSError, TypeError, ValueError):
        return None


def _read_log_run_info(
    path: Path, root_resolved: Path
) -> _LogRunInfo | None:
    try:
        if (
            path.is_symlink()
            or not path.is_dir()
            or not _PROCESS_RUN_ID_RE.fullmatch(path.name)
            or path.resolve().parent != root_resolved
        ):
            return None
        marker = _read_marker_path(path / RUN_MARKER_FILENAME)
        if marker is None or marker.get("run_id") != path.name:
            return None
        size_bytes, newest_mtime = _log_directory_usage(path)
        started_at = _number(marker.get("started_at"), newest_mtime)
        ended_at = _optional_number(marker.get("ended_at"))
        updated_at = max(
            newest_mtime,
            _number(marker.get("updated_at"), 0.0),
            ended_at or 0.0,
            started_at,
        )
        raw_pid = marker.get("pid")
        pid = (
            int(raw_pid)
            if isinstance(raw_pid, int)
            and not isinstance(raw_pid, bool)
            and raw_pid > 0
            else None
        )
        return _LogRunInfo(
            path=path,
            run_id=path.name,
            started_at=started_at,
            ended_at=ended_at,
            updated_at=updated_at,
            size_bytes=size_bytes,
            pid=pid,
            clean=marker.get("clean") is True,
            marker=marker,
        )
    except (OSError, TypeError, ValueError):
        return None


def _log_retention_timestamp(info: _LogRunInfo) -> float:
    return info.ended_at if info.ended_at is not None else info.updated_at


def _scan_log_runs(root: Path) -> tuple[list[_LogRunInfo], int, bool]:
    try:
        root_resolved = root.resolve(strict=True)
        children = list(root.iterdir())
    except OSError:
        return [], 0, True
    infos: list[_LogRunInfo] = []
    ignored = 0
    for child in children:
        info = _read_log_run_info(child, root_resolved)
        if info is None:
            ignored += 1
        else:
            infos.append(info)
    return infos, ignored, False


def _find_previous_run_marker(root: Path) -> dict[str, Any] | None:
    """Return the newest completed/dead marker, never a live sibling's."""
    candidates: list[tuple[float, dict[str, Any]]] = []
    infos, _ignored, _scan_failed = _scan_log_runs(root)
    for info in infos:
        if info.run_id == _PROCESS_RUN_ID or _process_is_alive(info.pid):
            continue
        candidates.append((_log_retention_timestamp(info), info.marker))

    # One-way migration compatibility: old versions wrote a flat marker at
    # the log root. It remains read-only and can still explain the preceding
    # launch, but a live PID is never called a crash.
    legacy = _read_marker_path(root / RUN_MARKER_FILENAME)
    if legacy is not None:
        raw_pid = legacy.get("pid")
        legacy_pid = (
            int(raw_pid)
            if isinstance(raw_pid, int)
            and not isinstance(raw_pid, bool)
            and raw_pid > 0
            else None
        )
        same_run = (
            legacy.get("run_id") == _PROCESS_RUN_ID
            and legacy_pid == os.getpid()
        )
        if not same_run and not _process_is_alive(legacy_pid):
            stamp = max(
                _number(legacy.get("ended_at"), 0.0),
                _number(legacy.get("updated_at"), 0.0),
                _number(legacy.get("started_at"), 0.0),
            )
            candidates.append((stamp, legacy))
    if not candidates:
        return None
    return dict(max(candidates, key=lambda item: item[0])[1])


def prune_log_runs(
    root: Path,
    *,
    current_run_id: str,
    policy: LogRetentionPolicy,
    now: float | None = None,
) -> dict[str, Any]:
    """Apply bounded retention to recognized per-launch log directories.

    The current directory, every live PID, and a recently dead unclean run are
    protected. Flat pre-upgrade artifacts and unrelated entries are ignored.
    Failures are summarized and never prevent startup.
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
    except OSError as exc:
        _log.warning("Could not inspect log retention root %s: %s", root, exc)
        result["failures"] = 1
        result["limits_satisfied"] = False
        return result

    infos, ignored, scan_failed = _scan_log_runs(root)
    result["recognized_runs"] = len(infos)
    result["ignored_entries"] = ignored
    if scan_failed:
        result["failures"] = 1
        result["limits_satisfied"] = False
        return result
    timestamp = time.time() if now is None else float(now)
    protected: set[Path] = set()
    eligible: dict[Path, _LogRunInfo] = {}
    for info in infos:
        age_seconds = max(0.0, timestamp - _log_retention_timestamp(info))
        if info.run_id == current_run_id or _process_is_alive(info.pid):
            protected.add(info.path)
        elif info.clean or age_seconds >= _STALE_UNCLEAN_SECONDS:
            eligible[info.path] = info
        else:
            protected.add(info.path)
    result["protected_runs"] = len(protected)
    remaining: dict[Path, _LogRunInfo] = {info.path: info for info in infos}

    def remove(info: _LogRunInfo, reason: str) -> bool:
        try:
            if not info.path.exists():
                # Another app instance may have completed the same retention
                # pass after our scan. Treat an already-absent run as a
                # successful convergence, not a maintenance failure.
                remaining.pop(info.path, None)
                eligible.pop(info.path, None)
                return True
            current = _read_log_run_info(info.path, root_resolved)
            if current is None or current.run_id != info.run_id:
                raise OSError("log run changed or is no longer recognized")
            if current.run_id == current_run_id or _process_is_alive(current.pid):
                protected.add(current.path)
                eligible.pop(info.path, None)
                return False
            current_age = max(
                0.0, timestamp - _log_retention_timestamp(current)
            )
            if not current.clean and current_age < _STALE_UNCLEAN_SECONDS:
                protected.add(current.path)
                eligible.pop(info.path, None)
                return False
            if (
                current.path.is_symlink()
                or current.path.resolve().parent != root_resolved
            ):
                raise OSError("log run is no longer a direct non-symlink child")
            shutil.rmtree(current.path)
        except OSError as exc:
            _log.warning("Could not prune log run %s: %s", info.path, exc)
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

    if policy.max_age_days > 0:
        max_age_seconds = policy.max_age_days * 24 * 60 * 60
        for info in sorted(eligible.values(), key=_log_retention_timestamp):
            if timestamp - _log_retention_timestamp(info) > max_age_seconds:
                remove(info, "age")

    if policy.max_runs > 0:
        for info in sorted(eligible.values(), key=_log_retention_timestamp):
            if len(remaining) <= policy.max_runs:
                break
            remove(info, "count")

    if policy.max_bytes > 0:
        total_bytes = sum(info.size_bytes for info in remaining.values())
        for info in sorted(eligible.values(), key=_log_retention_timestamp):
            if total_bytes <= policy.max_bytes:
                break
            if remove(info, "bytes"):
                total_bytes -= info.size_bytes

    remaining_bytes = sum(info.size_bytes for info in remaining.values())
    result["protected_runs"] = len(protected)
    result["remaining_runs"] = len(remaining)
    result["remaining_bytes"] = remaining_bytes
    age_satisfied = True
    if policy.max_age_days > 0:
        max_age_seconds = policy.max_age_days * 24 * 60 * 60
        age_satisfied = all(
            timestamp - _log_retention_timestamp(info) <= max_age_seconds
            for info in remaining.values()
        )
    result["limits_satisfied"] = (
        age_satisfied
        and (policy.max_runs <= 0 or len(remaining) <= policy.max_runs)
        and (policy.max_bytes <= 0 or remaining_bytes <= policy.max_bytes)
    )
    return result


def _install_crash_capture_locked() -> None:
    """faulthandler + exception hooks + the unclean-shutdown marker."""
    global _CRASH_HANDLE, _PREV_SYS_EXCEPTHOOK, _PREV_THREADING_EXCEPTHOOK
    global _HOOKS_INSTALLED, _PREVIOUS_RUN_MARKER, _LAST_LOG_RETENTION
    if _HOOKS_INSTALLED:
        return
    try:
        root = log_dir()
        root.mkdir(parents=True, exist_ok=True)
        directory = current_log_dir()
        directory.mkdir(parents=True, exist_ok=True)

        previous = _find_previous_run_marker(root)
        if previous is not None:
            _PREVIOUS_RUN_MARKER = dict(previous)
        if (
            previous is not None
            and previous.get("clean") is not True
        ):
            _log.warning(
                "previous run (pid %s, started %s) did not shut down "
                "cleanly — check diagnostic run %s and the latest trace "
                "for what it was doing",
                previous.get("pid"),
                _format_ts(previous.get("started_at")),
                previous.get("run_id") or "legacy-flat-log",
            )
        _write_run_marker(clean=False)
        _LAST_LOG_RETENTION = prune_log_runs(
            root,
            current_run_id=_PROCESS_RUN_ID,
            policy=log_retention_policy(),
        )
        atexit.register(mark_clean_shutdown)

        # The handle stays open for the process lifetime — faulthandler
        # writes to the raw fd, including from fatal-signal context.
        _CRASH_HANDLE = (directory / CRASH_FILENAME).open(
            "a", encoding="utf-8"
        )
        faulthandler.enable(file=_CRASH_HANDLE)

        _PREV_SYS_EXCEPTHOOK = sys.excepthook

        def _sys_hook(exc_type, exc_value, exc_tb):  # noqa: ANN001
            if not issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
                try:
                    logging.getLogger("buildaspec.crash").critical(
                        "uncaught exception on the main thread",
                        exc_info=(exc_type, exc_value, exc_tb),
                    )
                except Exception:  # noqa: BLE001
                    pass
            (_PREV_SYS_EXCEPTHOOK or sys.__excepthook__)(
                exc_type, exc_value, exc_tb
            )

        sys.excepthook = _sys_hook

        _PREV_THREADING_EXCEPTHOOK = threading.excepthook

        def _thread_hook(args):  # noqa: ANN001
            if args.exc_type is not SystemExit:
                try:
                    logging.getLogger("buildaspec.crash").critical(
                        "uncaught exception in thread %r",
                        getattr(args.thread, "name", "?"),
                        exc_info=(
                            args.exc_type,
                            args.exc_value,
                            args.exc_traceback,
                        ),
                    )
                except Exception:  # noqa: BLE001
                    pass
            (_PREV_THREADING_EXCEPTHOOK or threading.__excepthook__)(args)

        threading.excepthook = _thread_hook
        _HOOKS_INSTALLED = True
    except Exception:  # noqa: BLE001
        pass


def reset_for_tests() -> None:
    """Detach the handler and restore hooks (process-global state hygiene)."""
    with _INIT_LOCK:
        _teardown_locked()


def _teardown_locked() -> None:
    global _INITIALIZED, _HANDLER, _PREV_ROOT_LEVEL, _CRASH_HANDLE
    global _PREV_SYS_EXCEPTHOOK, _PREV_THREADING_EXCEPTHOOK, _HOOKS_INSTALLED
    global _PREVIOUS_RUN_MARKER, _LAST_LOG_RETENTION
    global _LOG_INIT_ATTEMPTED, _LOG_INIT_SUCCEEDED
    global _LOG_INIT_FAILURE_TYPE, _LOG_INIT_FAILURE_COUNT
    global _LOG_INIT_LAST_FAILURE_AT
    if _HANDLER is not None:
        try:
            logging.getLogger().removeHandler(_HANDLER)
            _HANDLER.close()
        except Exception:  # noqa: BLE001
            pass
        _HANDLER = None
    if _PREV_ROOT_LEVEL is not None:
        try:
            logging.getLogger().setLevel(_PREV_ROOT_LEVEL)
        except Exception:  # noqa: BLE001
            pass
        _PREV_ROOT_LEVEL = None
    if _HOOKS_INSTALLED:
        try:
            faulthandler.disable()
        except Exception:  # noqa: BLE001
            pass
        if _CRASH_HANDLE is not None:
            try:
                _CRASH_HANDLE.close()
            except Exception:  # noqa: BLE001
                pass
            _CRASH_HANDLE = None
        if _PREV_SYS_EXCEPTHOOK is not None:
            sys.excepthook = _PREV_SYS_EXCEPTHOOK
            _PREV_SYS_EXCEPTHOOK = None
        if _PREV_THREADING_EXCEPTHOOK is not None:
            threading.excepthook = _PREV_THREADING_EXCEPTHOOK
            _PREV_THREADING_EXCEPTHOOK = None
        try:
            atexit.unregister(mark_clean_shutdown)
        except Exception:  # noqa: BLE001
            pass
        _HOOKS_INSTALLED = False
    _INITIALIZED = False
    _PREVIOUS_RUN_MARKER = None
    _LAST_LOG_RETENTION = None
    _LOG_INIT_ATTEMPTED = False
    _LOG_INIT_SUCCEEDED = None
    _LOG_INIT_FAILURE_TYPE = None
    _LOG_INIT_FAILURE_COUNT = 0
    _LOG_INIT_LAST_FAILURE_AT = None
    with _SERVER_IDENTITY_LOCK:
        _SERVER_IDENTITY.clear()


def _read_run_marker() -> dict[str, Any] | None:
    path = current_log_dir() / RUN_MARKER_FILENAME
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — missing or torn marker = no verdict
        return None


def _write_run_marker(*, clean: bool) -> None:
    """Atomic tmp+replace (the updates.py discipline)."""
    from . import settings

    directory = current_log_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / RUN_MARKER_FILENAME
    payload: dict[str, Any] = {
        "run_id": _PROCESS_RUN_ID,
        "started_at": _RUN_STARTED_AT,
        "pid": os.getpid(),
        "version": settings.VERSION,
        "clean": clean,
        # During the process lifetime ``clean=False`` means "active", not
        # "crashed".  It becomes unclean-shutdown evidence only when a later
        # process finds it still active.
        "state": "clean_shutdown" if clean else "active",
        "updated_at": time.time(),
    }
    server_identity = runtime_server_identity()
    if server_identity:
        payload["server"] = server_identity
    if clean:
        payload["ended_at"] = time.time()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)

def _format_ts(value: Any) -> str:
    try:
        return _dt.datetime.fromtimestamp(float(value)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    except Exception:  # noqa: BLE001
        return repr(value)


# ---- read-only surfaces behind /api/diagnostics* ------------------------


def _marker_facts(
    marker: dict[str, Any] | None, *, current: bool
) -> dict[str, Any]:
    """Explain marker semantics without copying arbitrary marker fields."""
    if marker is None:
        return {
            "present": False,
            "shutdown_state": "unknown",
            "unclean_shutdown_evidence": False,
        }
    clean = marker.get("clean") is True
    same_run = bool(
        marker.get("pid") == os.getpid()
        and marker.get("run_id") == _PROCESS_RUN_ID
    )
    if current and same_run and not clean:
        shutdown_state = "active_at_capture"
        unclean = False
    elif clean:
        shutdown_state = "clean_shutdown"
        unclean = False
    elif current and same_run:
        shutdown_state = "active_at_capture"
        unclean = False
    else:
        shutdown_state = "unclean_shutdown"
        unclean = True
    return {
        "present": True,
        "run_id": marker.get("run_id"),
        "pid": marker.get("pid"),
        "version": marker.get("version"),
        "started_at": marker.get("started_at"),
        "ended_at": marker.get("ended_at"),
        "clean": marker.get("clean"),
        "belongs_to_captured_process": same_run,
        "shutdown_state": shutdown_state,
        "unclean_shutdown_evidence": unclean,
    }


def _safe_blocker(value: Any) -> str:
    text = str(value or "unrecorded")
    return text if re.fullmatch(r"[a-z0-9_]{1,80}", text) else "unrecorded"


def _source_capability_facts(session: Any) -> dict[str, Any]:
    """Why an imported document's editing affordances are what they are.

    "Everything says READ-ONLY" is a support question a bundle could not
    answer: the snapshot recorded that a source package was retained but
    nothing about the permissions derived from it, so telling a
    package-wide blocker (one ``pass_through_only`` cause disabling the
    whole document) apart from a per-paragraph one (markup Word cannot be
    patched through) meant asking the user to hover a badge.

    Only the closed blocker vocabulary travels — never a message, URL, or
    any provision text — the same posture as ``DimensionStatus.error_kind``.
    This is deliberately called after ``session_state_guard`` is released and
    uses the non-starting cache peek. Opening Developer Tools must never start,
    join, or inline-compute the multi-minute source-capability sweep.
    """
    try:
        active_scope = session._active_source_scope()
        report = session.peek_source_edit_capabilities()
    except Exception:  # noqa: BLE001 - diagnostics never break a snapshot
        return {
            "capabilities_status": "unavailable",
            "capabilities_snapshot_source": "peek_failed",
            "edit_blockers": {},
            "global_edit_blockers": {
                "causes": [],
                "denial_counts": {},
                "origins": {},
            },
            "per_operation_edit_blockers": {},
            "capability_operation_counts": {
                "elements": 0,
                "total": 0,
                "allowed": 0,
                "denied": 0,
            },
        }
    if report is None:
        warm = getattr(session, "_capability_warm", None)
        warm_done = getattr(warm, "done", None)
        analysis_pending = bool(
            active_scope
            and (
                (
                    warm_done is not None
                    and callable(getattr(warm_done, "is_set", None))
                    and not warm_done.is_set()
                )
                or getattr(session, "_capability_warm_next", None) is not None
            )
        )
        return {
            "capabilities_status": (
                "pending"
                if analysis_pending
                else "not_cached"
                if active_scope
                else None
            ),
            "capabilities_snapshot_source": (
                "analysis_running_without_materialized_report"
                if analysis_pending
                else "no_current_cache"
                if active_scope
                else "not_source_backed"
            ),
            "edit_blockers": {},
            "global_edit_blockers": {
                "causes": [],
                "denial_counts": {},
                "origins": {},
            },
            "per_operation_edit_blockers": {},
            "capability_operation_counts": {
                "elements": 0,
                "total": 0,
                "allowed": 0,
                "denied": 0,
            },
        }

    blockers: dict[str, int] = {}
    allowed = 0
    denied = 0
    for element in report.elements.values():
        for capability in element.to_dict().values():
            if not isinstance(capability, dict):
                continue
            if capability.get("allowed"):
                allowed += 1
                continue
            denied += 1
            blocker = _safe_blocker(capability.get("blocker"))
            blockers[blocker] = blockers.get(blocker, 0) + 1

    # A package/runtime-wide blocker is repeated once for every denied body
    # operation.  Preserve the legacy aggregate while also naming the cause
    # once and separating it from genuinely element/operation-local denials.
    global_causes: set[str] = set()
    global_origins: dict[str, set[str]] = {}

    def _record_global(blocker: Any, origin: str) -> None:
        safe = _safe_blocker(blocker)
        if safe == "unrecorded":
            return
        global_causes.add(safe)
        global_origins.setdefault(safe, set()).add(origin)

    source_map = getattr(session, "source_docx_map", None)
    for blocker in getattr(source_map, "global_blockers", ()) or ():
        _record_global(blocker, "source_package_scan")
    context = getattr(session, "source_patch_context", None)
    for blocker in getattr(context, "global_blockers", ()) or ():
        _record_global(blocker, "source_patch_context")
    for issue in getattr(context, "runtime_mutation_issues", ()) or ():
        _record_global(getattr(issue, "blocker", None), "source_patch_preflight")

    global_counts = {
        blocker: blockers.get(blocker, 0) for blocker in sorted(global_causes)
    }
    per_operation = {
        blocker: count
        for blocker, count in sorted(blockers.items())
        if blocker not in global_causes
    }
    return {
        "capabilities_status": report.status,
        "capabilities_snapshot_source": (
            "existing_current_pending_cache"
            if report.status == "pending"
            else "existing_current_settled_cache"
        ),
        "edit_blockers": dict(sorted(blockers.items())),
        "global_edit_blockers": {
            "causes": sorted(global_causes),
            "denial_counts": global_counts,
            "origins": {
                blocker: sorted(global_origins.get(blocker, ()))
                for blocker in sorted(global_causes)
            },
        },
        "per_operation_edit_blockers": per_operation,
        "capability_operation_counts": {
            "elements": len(report.elements),
            "total": allowed + denied,
            "allowed": allowed,
            "denied": denied,
        },
    }


def _document_shape(section: Any) -> dict[str, int]:
    articles = 0
    paragraphs = 0
    maximum_depth = 0
    for part in getattr(section, "parts", ()) or ():
        for article in getattr(part, "articles", ()) or ():
            articles += 1
            stack = [(item, 0) for item in getattr(article, "paragraphs", ())]
            while stack:
                paragraph, depth = stack.pop()
                paragraphs += 1
                maximum_depth = max(maximum_depth, depth)
                stack.extend(
                    (child, depth + 1)
                    for child in getattr(paragraph, "children", ()) or ()
                )
    return {
        "parts": len(getattr(section, "parts", ()) or ()),
        "articles": articles,
        "paragraphs": paragraphs,
        "maximum_paragraph_depth": maximum_depth,
    }


def _import_report_facts(report: Any) -> dict[str, Any]:
    if not isinstance(report, dict):
        return {
            "present": False,
            "warning_count": 0,
            "warning_code_counts": {},
            "warning_evidence": [],
            "warning_evidence_truncated": 0,
        }
    raw_warnings = report.get("warnings", [])
    warnings = (
        [item for item in raw_warnings if isinstance(item, str)]
        if isinstance(raw_warnings, (list, tuple))
        else []
    )
    # Reuse the trace classifier so one warning has the same stable code and
    # fingerprint in every forensic surface.  The helper intentionally emits
    # no warning prose, which can contain a document-derived section title.
    try:
        from .tracing.capture import _import_warning_evidence

        warning_counts, warning_evidence, warning_total = (
            _import_warning_evidence(warnings)
        )
    except Exception:  # noqa: BLE001 - report metadata remains useful
        warning_counts, warning_evidence, warning_total = {}, [], len(warnings)
    allowlist = (
        "filename",
        "sha256",
        "size_bytes",
        "zip_member_count",
        "zip_uncompressed_bytes",
        "imported_block_count",
        "skipped_empty_count",
        "tracked_changes_detected",
        "spec_shape_detected",
        "fidelity_notice",
    )
    facts = {key: report.get(key) for key in allowlist if key in report}
    facts.update(
        {
            "present": True,
            "warning_count": warning_total,
            "warning_code_counts": warning_counts,
            "warning_evidence": warning_evidence,
            "warning_evidence_truncated": max(
                0, warning_total - len(warning_evidence)
            ),
        }
    )
    return facts


def _thread_alive(runner: Any) -> bool:
    thread = getattr(runner, "_thread", None)
    try:
        return bool(thread is not None and thread.is_alive())
    except Exception:  # noqa: BLE001
        return False


def _result_collection_count(result: Any, name: str) -> int:
    try:
        return len(getattr(result, name, ()) or ())
    except Exception:  # noqa: BLE001
        return 0


def _qc_result_facts(result: Any) -> dict[str, Any]:
    if result is None:
        return {"present": False}
    return {
        "present": True,
        "run_id": getattr(result, "run_id", ""),
        "execution_status": getattr(result, "execution_status", ""),
        "version_index": getattr(result, "version_index", None),
        "findings": _result_collection_count(result, "findings"),
        "refuted": _result_collection_count(result, "refuted"),
        "disputed": _result_collection_count(result, "disputed"),
        "inconclusive": _result_collection_count(result, "inconclusive"),
        "lens_statuses": _result_collection_count(result, "lens_statuses"),
    }


def _bounded_scalar_mapping(value: Any, *, limit: int = 100) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for raw_key in sorted(value, key=lambda item: str(item))[:limit]:
        key = str(raw_key)[:100]
        item = value[raw_key]
        if item is None or isinstance(item, (bool, int, float)):
            result[key] = item
        elif isinstance(item, str):
            result[key] = item[:200]
    return result


def _current_trace_meta_facts(recorder: Any) -> dict[str, Any]:
    """Bounded schema-v2 run summary/health after a short flush barrier."""
    flush_complete = False
    try:
        flush_complete = bool(recorder.flush(timeout=0.5))
        path = recorder.trace_dir / "run.json"
        size = path.stat().st_size
        if size > _RUN_META_READ_CAP:
            return {
                "metadata_flush_complete": flush_complete,
                "metadata_size_bytes": size,
                "metadata_omitted_reason": "size_limit",
            }
        meta = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - snapshot remains available on trace gaps
        return {"metadata_flush_complete": flush_complete}
    if not isinstance(meta, dict):
        return {"metadata_flush_complete": flush_complete}

    summary_raw = meta.get("summary")
    health_raw = meta.get("recorder_health")
    summary: dict[str, Any] = {}
    if isinstance(summary_raw, dict):
        for key in (
            "coverage",
            "records_enqueued",
            "events_total",
            "failed_events",
            "spans_closed",
            "failed_spans",
            "prompts_stored",
            "includes_estimated_turn_usage",
            "last_recorded_at",
        ):
            if key in summary_raw:
                summary[key] = summary_raw[key]
        for key in (
            "events_by_type",
            "event_status_counts",
            "request_outcome_counts",
            "spans_by_kind",
            "spans_by_status",
            "turn_usage_totals",
        ):
            summary[key] = _bounded_scalar_mapping(summary_raw.get(key))

    health: dict[str, Any] = {}
    if isinstance(health_raw, dict):
        for key in (
            "state",
            "records_written",
            "dropped_records",
            "queue_count_drops",
            "queue_byte_drops",
            "run_byte_limit_drops",
            "serialization_failures",
            "unknown_file_drops",
            "write_failure_drops",
            "last_drop_reason",
            "write_failures",
            "metadata_write_failures",
            "queue_depth",
            "control_queue_depth",
            "queue_max_records",
            "queue_high_watermark",
            "queue_max_bytes",
            "queue_payload_bytes",
            "resident_payload_bytes",
            "inflight_payload_bytes",
            "queue_payload_high_watermark",
            "max_run_bytes",
            "data_bytes_written",
            "storage_limit_scope",
            "storage_limit_reached",
            "metadata_revision",
            "metadata_checkpointed_revision",
            "metadata_dirty",
            "thread_alive",
            "open_spans",
            "drain_timed_out",
            "fatal_error",
            "last_write_at",
            "captured_at",
        ):
            if key in health_raw:
                health[key] = health_raw[key]
        health["open_spans_by_kind"] = _bounded_scalar_mapping(
            health_raw.get("open_spans_by_kind")
        )
    return {
        "metadata_flush_complete": flush_complete,
        "metadata_size_bytes": size,
        "trace_schema_version": meta.get("trace_schema_version"),
        "process_instance_id": meta.get("process_instance_id"),
        "summary": summary,
        "recorder_health": health,
    }


def snapshot() -> dict[str, Any]:
    """The full environment + session snapshot (scrubbed on the way out).

    Session fields are read under ``session_state_guard()`` — field reads
    only, never ``_doc_payload``, never the capability sweep, never file
    I/O: anything held under that lock blocks model turns (the freeze
    class the capability-sweep work removed).
    """
    from . import api_key_store, sessions, settings
    from .llm.conversation import effective_discipline
    from .research.engine import incomplete_dimension_facts
    from .tracing import config as trace_config
    from .tracing.capture import trace_startup_failure_state
    from .tracing.recorder import get_recorder
    from .tracing.redaction import scrub_data

    lease = sessions.get_workspace()
    session = lease.session

    with session.session_state_guard():
        store = session.doc
        source_bytes = session.source_docx_bytes
        # Plain attribute reads of the runner, the way readiness and
        # `_doc_payload` already do it — never the runner's own lock, which
        # would nest a second lock under the session guard.
        research_profile = session.research.profile_result
        research_block: dict[str, Any] = {
            "status": session.research.status,
            "worker_alive": _thread_alive(session.research),
            "event_count": len(session.research.events),
            "active_round": getattr(session.research, "_round_number", 0),
            "error_present": bool(session.research.error),
            "error_kind": session.research.error_kind,
            "rounds": (
                research_profile.round_count if research_profile else 0
            ),
            "dimension_count": (
                len(research_profile.dimension_statuses) if research_profile else 0
            ),
            # Which coverage never completed — a partial run still reports
            # `complete`, so a support bundle would otherwise have to open
            # the project to find out. Sanitized kinds, never the raw
            # provider message.
            "incomplete_dimensions": incomplete_dimension_facts(research_profile),
        }
        audit_result = session.audit.result
        audit_block: dict[str, Any] = {
            "status": session.audit.status,
            "worker_alive": _thread_alive(session.audit),
            "error_present": bool(session.audit.error),
            "result_present": audit_result is not None,
            "version_index": (
                audit_result.get("version_index")
                if isinstance(audit_result, dict)
                else None
            ),
            "findings": (
                len(audit_result.get("findings", []))
                if isinstance(audit_result, dict)
                and isinstance(audit_result.get("findings", []), list)
                else 0
            ),
        }
        qc_latest = session.qc.latest_attempt_result
        qc_block: dict[str, Any] = {
            "status": session.qc.status,
            "worker_alive": _thread_alive(session.qc),
            "worker_settled": bool(
                getattr(session.qc, "_worker_settled", True)
            ),
            "event_count": len(session.qc.events),
            "error_present": bool(session.qc.error),
            "error_kind": session.qc.error_kind,
            "retained_result": _qc_result_facts(session.qc.result),
            "latest_attempt": {
                "run_id": session.qc.latest_attempt_run_id,
                "status": session.qc.latest_attempt_status,
                "error_present": bool(session.qc.latest_attempt_error),
                "error_kind": session.qc.latest_attempt_error_kind,
                "started_at": session.qc.latest_attempt_started_at,
                "finished_at": session.qc.latest_attempt_finished_at,
                "result": _qc_result_facts(qc_latest),
            },
        }
        warm = getattr(session, "_capability_warm", None)
        session_block: dict[str, Any] = {
            "history_len": len(session.history),
            "doc_version_index": store.index,
            "doc_version_count": len(store.versions),
            "baseline_index": store.baseline_index,
            "doc_empty": store.doc.is_empty(),
            "document_shape": _document_shape(store.doc),
            "can_undo": store.index > 0,
            "can_redo": store.index + 1 < len(store.versions),
            "turn_transaction_open": getattr(store, "_turn_backup", None)
            is not None,
            "turn_transaction_dirty": bool(getattr(store, "_dirty", False)),
            "figures": len(session.figures.figures),
            "references": len(session.references.docs),
            "suggested_prompts": len(session.suggested_prompts),
            "turn_active": session.turn_active,
            "stop_requested": session.stop_requested.is_set(),
            "last_context_tokens": session.last_context_tokens,
            "unsaved": sessions.has_unsaved_progress(session),
            "import_report_present": session.import_report is not None,
            "import": _import_report_facts(session.import_report),
            "template_origin_present": session.template_origin is not None,
            "module_id": session.module.module_id,
            "discipline": effective_discipline(session),
            "research": research_block,
            "audit": audit_block,
            "qc": qc_block,
            "source": {
                "retained": source_bytes is not None,
                "filename": session.source_docx_filename,
                "bytes": len(source_bytes) if source_bytes else 0,
                "map_present": session.source_docx_map is not None,
                "patch_context_present": session.source_patch_context is not None,
                "capability_cache_present": session._capability_cache is not None,
                "capability_analysis_running": bool(
                    warm is not None and not warm.done.is_set()
                ),
                "capability_analysis_queued": session._capability_warm_next
                is not None,
            },
        }
        busy = sessions.busy_reasons(session)
        usage = session.usage.snapshot()
        generation = session.generation

    # Why an imported document is showing read-only affordances. The cache
    # lookup hashes the semantic projection outside the session guard and,
    # crucially, never starts or joins the expensive capability sweep.
    session_block["source"].update(_source_capability_facts(session))

    key = dict(api_key_store.key_status())
    if key.get("source") == "env":
        key["env_locked"] = True

    trace_startup_failure = trace_startup_failure_state()
    recorder = get_recorder()
    tracing_block: dict[str, Any] = {
        "enabled": trace_config.trace_enabled(),
        "initialization_attempted": bool(
            recorder is not None or trace_startup_failure is not None
        ),
        "initialization_succeeded": (
            True
            if recorder is not None
            else False
            if trace_startup_failure is not None
            else None
        ),
        "capture_active": False,
        "startup_failure": trace_startup_failure,
        "level": trace_config.current_capture_level(),
        "root": str(trace_config.default_trace_root()),
        "retention_policy": trace_config.trace_retention_policy().to_dict(),
    }
    if recorder is not None:
        tracing_block["run_id"] = recorder.run_id
        tracing_block["run_dir"] = str(recorder.trace_dir)
        tracing_block.update(_current_trace_meta_facts(recorder))
        recorder_health = tracing_block.get("recorder_health")
        tracing_block["capture_active"] = bool(
            isinstance(recorder_health, dict)
            and recorder_health.get("state") == "running"
            and recorder_health.get("thread_alive") is True
        )

    log_file = current_log_file()
    logging_block: dict[str, Any] = {
        "enabled": log_enabled(),
        "initialization_attempted": _LOG_INIT_ATTEMPTED,
        "initialization_succeeded": _LOG_INIT_SUCCEEDED,
        "handler_attached": bool(
            _HANDLER is not None
            and _HANDLER in logging.getLogger().handlers
        ),
        "capture_active": bool(
            log_enabled()
            and _LOG_INIT_SUCCEEDED is True
            and _HANDLER is not None
            and _HANDLER in logging.getLogger().handlers
        ),
        "initialization_failure_type": _LOG_INIT_FAILURE_TYPE,
        "initialization_failure_count": _LOG_INIT_FAILURE_COUNT,
        "initialization_last_failure_at": _LOG_INIT_LAST_FAILURE_AT,
        "level": logging.getLevelName(configured_level()),
        "dir": str(log_dir()),
        "run_dir": str(current_log_dir()),
        "run_id": _PROCESS_RUN_ID,
        "retention_policy": log_retention_policy().to_dict(),
        "last_retention": dict(_LAST_LOG_RETENTION or {}),
    }
    if log_file is not None:
        logging_block["file"] = str(log_file)
        try:
            logging_block["size_bytes"] = log_file.stat().st_size
        except OSError:
            logging_block["size_bytes"] = 0

    captured_at = time.time()
    server_identity = runtime_server_identity()
    current_marker = _read_run_marker() if log_enabled() else None
    local_now = _dt.datetime.now().astimezone()
    process_block: dict[str, Any] = {
        "diagnostic_run_id": _PROCESS_RUN_ID,
        "pid": os.getpid(),
        "parent_pid": os.getppid(),
        "started_at": _RUN_STARTED_AT,
        "uptime_seconds": max(0.0, captured_at - _RUN_STARTED_AT),
        "capture_state": "active_at_capture",
        "thread_count": threading.active_count(),
        "timezone": local_now.tzname(),
        "utc_offset": local_now.strftime("%z"),
        "current_run_marker": _marker_facts(current_marker, current=True),
        "previous_run_marker": _marker_facts(
            _PREVIOUS_RUN_MARKER, current=False
        ),
    }

    payload: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": captured_at,
        "process": process_block,
        "app": {
            "name": settings.APP_NAME,
            "version": settings.VERSION,
            "platform": platform.platform(),
            "python": platform.python_version(),
            "frozen": bool(getattr(sys, "frozen", False)),
            "dev_mode": settings.dev_mode(),
            "port": int(server_identity.get("bound_port") or settings.PORT),
            "models": {
                "interview": settings.INTERVIEW_MODEL,
                "research": settings.RESEARCH_MODEL,
                "qc": settings.QC_MODEL,
            },
        },
        "tracing": tracing_block,
        "logging": logging_block,
        "key": key,
        "workspace": {
            "workspace_id": lease.workspace_id,
            "scope": lease.scope,
            "generation": generation,
            "busy": busy,
        },
        "session": session_block,
        "usage": usage,
    }
    if server_identity:
        payload["server"] = server_identity
    return scrub_data(payload)


def tail_log(tail: int = 500) -> dict[str, Any]:
    """Last ``tail`` lines of the current log (bounded read, grace on gaps)."""
    try:
        tail = int(tail)
    except (TypeError, ValueError):
        tail = 500
    tail = max(1, min(tail, 5000))
    path = current_log_file()
    if path is None or not path.exists():
        return {
            "enabled": log_enabled(),
            "path": str(path) if path is not None else None,
            "size_bytes": 0,
            "lines": [],
        }
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > _TAIL_READ_CAP:
                fh.seek(size - _TAIL_READ_CAP)
            data = fh.read(_TAIL_READ_CAP)
        lines = data.decode("utf-8", errors="replace").splitlines()
        if size > _TAIL_READ_CAP and lines:
            lines = lines[1:]  # the first line is almost certainly torn
        return {
            "enabled": True,
            "path": str(path),
            "size_bytes": size,
            "lines": lines[-tail:],
        }
    except OSError:
        return {
            "enabled": True,
            "path": str(path),
            "size_bytes": 0,
            "lines": [],
        }


def list_trace_runs(limit: int = 20) -> dict[str, Any]:
    """Trace-run inventory, newest first. Sizes only — never line counts
    (counting means reading every file; deep runs are tens of MB)."""
    from .tracing import config as trace_config
    from .tracing.recorder import get_recorder
    from .tracing.retention import process_is_alive

    root = trace_config.default_trace_root()
    recorder = get_recorder()
    current_id = recorder.run_id if recorder is not None else None
    runs: list[dict[str, Any]] = []
    try:
        candidates = [p for p in root.iterdir() if _is_trace_run_dir(p)]
    except OSError:
        candidates = []
    candidates.sort(key=_mtime_or_zero, reverse=True)
    for run_dir in candidates[: max(1, min(int(limit or 20), 100))]:
        meta: dict[str, Any] = {}
        try:
            meta = json.loads(
                (run_dir / "run.json").read_text(encoding="utf-8")
            )
        except Exception:  # noqa: BLE001 — a torn run.json is still a run
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        environment = meta.get("environment")
        raw_pid = environment.get("pid") if isinstance(environment, dict) else None
        owner_pid = (
            int(raw_pid)
            if isinstance(raw_pid, int)
            and not isinstance(raw_pid, bool)
            and raw_pid > 0
            else None
        )
        owner_process_alive = process_is_alive(owner_pid)
        files: dict[str, int] = {}
        total = 0
        for name in ("spans.jsonl", "events.jsonl", "prompts.jsonl", "run.json"):
            try:
                file_size = (run_dir / name).stat().st_size
            except OSError:
                continue
            files[name] = file_size
            total += file_size
        runs.append(
            {
                "run_id": run_dir.name,
                "started_at": meta.get("started_at"),
                "ended_at": meta.get("ended_at"),
                "current": run_dir.name == current_id,
                "owner_pid": owner_pid,
                "owner_process_alive": owner_process_alive,
                "size_bytes": total,
                "files": files,
            }
        )
    return {"root": str(root), "runs": runs}


def _mtime_or_zero(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _is_trace_run_dir(path: Path) -> bool:
    """Recognize a direct, non-symlink run from its self-naming metadata."""
    try:
        if path.is_symlink() or not path.is_dir():
            return False
        meta_path = path / "run.json"
        if meta_path.is_symlink() or not meta_path.is_file():
            return False
        if meta_path.stat().st_size > _RUN_META_READ_CAP:
            return False
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return isinstance(meta, dict) and meta.get("run_id") == path.name
    except (OSError, TypeError, ValueError):
        return False


def read_recent_trace_events(tail: int = 200) -> dict[str, Any]:
    """Recent events + open spans of the CURRENT run, parsed leniently.

    Reading the writer thread's append-mode file back is safe now that
    every line is flushed as written; the final line can still be torn
    mid-write, so unparsable lines are skipped, never fatal.
    """
    from .tracing.recorder import get_recorder

    recorder = get_recorder()
    if recorder is None:
        return {"enabled": False, "events": [], "spans": []}
    # Short barrier so "Recent activity" includes what just happened.
    recorder.flush(timeout=0.5)
    try:
        tail = int(tail)
    except (TypeError, ValueError):
        tail = 200
    tail = max(1, min(tail, 2000))
    events: list[dict[str, Any]] = []
    path = recorder.trace_dir / "events.jsonl"
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > _TAIL_READ_CAP:
                fh.seek(size - _TAIL_READ_CAP)
            data = fh.read(_TAIL_READ_CAP)
        for line in data.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except ValueError:
                continue
            if isinstance(parsed, dict):
                events.append(parsed)
    except OSError:
        pass
    return {
        "enabled": True,
        "run_id": recorder.run_id,
        "events": events[-tail:],
        "spans": recorder.open_span_summaries(),
    }


def _read_tail_bytes(path: Path, maximum: int) -> tuple[bytes, int, bool]:
    """Return a line-aligned bounded tail, source size, and truncation flag."""
    try:
        if path.is_symlink() or not path.is_file():
            return b"", 0, False
        size = path.stat().st_size
        start = max(0, size - maximum)
        with path.open("rb") as handle:
            if start:
                handle.seek(start)
            data = handle.read(maximum)
    except OSError:
        return b"", 0, False
    if start:
        newline = data.find(b"\n")
        data = data[newline + 1 :] if newline >= 0 else b""
    return data, size, bool(start)


def _bounded_jsonl_tail(path: Path) -> tuple[bytes, dict[str, Any]]:
    """A parseable, re-scrubbed bounded tail for prior-run JSONL."""
    from .tracing.redaction import scrub_data

    data, source_size, truncated = _read_tail_bytes(
        path, _PRIOR_CONTEXT_BYTES
    )
    valid: list[bytes] = []
    invalid_records = 0
    for line in data.splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
            encoded = json.dumps(
                scrub_data(parsed),
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError):
            invalid_records += 1
            continue
        valid.append(encoded)
    encoded = b"\n".join(valid) + (b"\n" if valid else b"")
    return encoded, {
        "source_size_bytes": source_size,
        "included_bytes": len(encoded),
        "truncated": truncated,
        "record_count": len(valid),
        "invalid_record_count": invalid_records,
        "transformation": "current_recursive_credential_redaction",
    }


_LOG_INCIDENT_RE = re.compile(r"\s(?:ERROR|CRITICAL)\s+")


def _recent_log_incidents(directory: Path) -> list[dict[str, Any]]:
    incidents: list[dict[str, Any]] = []
    # Oldest rotation first so the final slice is chronological.
    names = [
        f"{LOG_FILENAME}.{i}" for i in range(_LOG_BACKUP_COUNT, 0, -1)
    ] + [LOG_FILENAME]
    for name in names:
        data, _size, _truncated = _read_tail_bytes(
            directory / name, _INCIDENT_TAIL_BYTES
        )
        for line in data.decode("utf-8", errors="replace").splitlines():
            if _LOG_INCIDENT_RE.search(line):
                incidents.append({"file": name, "line": line[:2000]})
    return incidents[-_INCIDENT_LIMIT:]


def _trace_incidents(
    data: bytes, *, run_id: str, artifact: str
) -> list[dict[str, Any]]:
    """Extract a small allowlisted incident index from event/span JSONL."""
    incidents: list[dict[str, Any]] = []
    allowed = (
        "ts",
        "started_at",
        "ended_at",
        "type",
        "kind",
        "name",
        "path",
        "method",
        "status",
        "status_code",
        "code",
        "error_kind",
        "error",
        "message",
        "action",
        "blocker",
        "ok",
        "span_id",
        "parent_span_id",
        "request_id",
        "outcome_code",
        "exception_type",
        "trace_run_id",
        "process_instance_id",
        "record_seq",
        "workspace_id_before",
        "workspace_scope_before",
        "generation_before",
        "workspace_id_after",
        "workspace_scope_after",
        "generation_after",
    )
    for line in data.splitlines():
        try:
            item = json.loads(line)
        except (TypeError, ValueError):
            continue
        if not isinstance(item, dict):
            continue
        event_type = str(item.get("type") or item.get("kind") or "").lower()
        status = item.get("status")
        status_text = str(status or "").lower()
        numeric_status = status if isinstance(status, int) else item.get("status_code")
        incident = (
            item.get("ok") is False
            or bool(item.get("error"))
            or any(
                token in event_type
                for token in ("error", "failed", "conflict", "crash")
            )
            or status_text in {"error", "failed", "cancelled"}
            or (
                isinstance(numeric_status, int)
                and not isinstance(numeric_status, bool)
                and numeric_status >= 400
            )
        )
        if not incident:
            continue
        summary: dict[str, Any] = {
            "run_id": run_id,
            "artifact": artifact,
        }
        for key in allowed:
            if key not in item:
                continue
            value = item[key]
            summary[key] = value[:2000] if isinstance(value, str) else value
        incidents.append(summary)
    return incidents[-_INCIDENT_LIMIT:]


def _recent_trace_incidents(
    incidents: list[dict[str, Any]], *, limit: int = _INCIDENT_LIMIT
) -> list[dict[str, Any]]:
    """Select newest incidents by evidence time, not bundle append order."""

    def timestamp(item: dict[str, Any]) -> float:
        for key in ("ts", "ended_at", "started_at"):
            value = item.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
        return 0.0

    bounded = max(1, min(int(limit or _INCIDENT_LIMIT), _INCIDENT_LIMIT))
    return sorted(incidents, key=timestamp)[-bounded:]


def build_bundle() -> tuple[Path, str]:
    """Assemble the diagnostics zip into a temp file; (path, filename).

    Contents: the scrubbed snapshot, this launch's log files plus read-only
    legacy flat logs (rotation bounds both), the CURRENT trace run through a
    pre-copy flush barrier, and bounded JSONL tails for the three most recent
    completed prior runs. Runs owned by another live process are identified but
    never copied — full prior runs are unbounded
    and the trace list in the modal names the folder for anyone who
    needs more. A manifest declares exact inclusion/truncation scope, and a
    bounded incident index points at recent errors without replacing evidence.

    Written to a temporary FILE, not memory: a deep-trace run can be
    hundreds of MB, and an in-memory zip of it would spike (or kill) the
    desktop process exactly when the user is trying to report a problem.
    The route streams the file out and deletes it afterwards. Before
    copying the live run the recorder queue is flushed to disk, so a
    bundle grabbed right after an incident contains the incident.
    """
    from .tracing.recorder import get_recorder
    from .tracing.redaction import scrub_data

    recorder = get_recorder()
    recorder_flush_complete: bool | None = None
    if recorder is not None:
        recorder_flush_complete = recorder.flush(timeout=2.0)

    handle = tempfile.NamedTemporaryFile(
        prefix="buildaspec-diagnostics-", suffix=".zip", delete=False
    )
    path = Path(handle.name)
    capture_id = f"bundle-{uuid.uuid4().hex}"
    captured_at = time.time()
    captured_snapshot = snapshot()
    artifacts: list[dict[str, Any]] = []
    trace_incidents: list[dict[str, Any]] = []
    prior_scopes: list[dict[str, Any]] = []
    try:
        with handle, zipfile.ZipFile(
            handle, "w", compression=zipfile.ZIP_DEFLATED
        ) as zf:
            snapshot_bytes = json.dumps(
                captured_snapshot, indent=2, default=str
            ).encode("utf-8")
            zf.writestr("snapshot.json", snapshot_bytes)
            artifacts.append(
                {
                    "path": "snapshot.json",
                    "coverage": "point_in_time",
                    "included_bytes": len(snapshot_bytes),
                }
            )

            log_root = log_dir()
            directory = current_log_dir()
            log_names = [LOG_FILENAME] + [
                f"{LOG_FILENAME}.{i}" for i in range(1, _LOG_BACKUP_COUNT + 1)
            ]
            log_names += [CRASH_FILENAME, RUN_MARKER_FILENAME]
            for name in log_names:
                arcname = f"logs/{_PROCESS_RUN_ID}/{name}"
                if name == RUN_MARKER_FILENAME:
                    added = _add_sanitized_json_file_if_present(
                        zf,
                        directory / name,
                        arcname,
                        coverage="current_log_run_metadata",
                    )
                else:
                    added = _add_redacted_text_file_if_present(
                        zf, directory / name, arcname
                    )
                if added is not None:
                    artifacts.append(added)

            # Pre-upgrade versions wrote directly in the root. Keep those
            # source files untouched and preserve their historical archive
            # names so existing support tooling can still find them.
            for name in log_names:
                arcname = f"logs/{name}"
                if name == RUN_MARKER_FILENAME:
                    added = _add_sanitized_json_file_if_present(
                        zf,
                        log_root / name,
                        arcname,
                        coverage="legacy_flat_log_metadata",
                    )
                else:
                    added = _add_redacted_text_file_if_present(
                        zf, log_root / name, arcname
                    )
                if added is not None:
                    artifacts.append(added)

            current_id = None
            if recorder is not None:
                current_id = recorder.run_id
                for name in (
                    "spans.jsonl",
                    "events.jsonl",
                    "prompts.jsonl",
                    "run.json",
                ):
                    source_path = recorder.trace_dir / name
                    arcname = f"traces/{recorder.run_id}/{name}"
                    if name == "run.json":
                        added = _add_sanitized_json_file_if_present(
                            zf,
                            source_path,
                            arcname,
                            coverage="current_run_through_pre_copy_flush",
                        )
                    else:
                        added = _add_sanitized_jsonl_file_if_present(
                            zf,
                            source_path,
                            arcname,
                            coverage="current_run_through_pre_copy_flush",
                        )
                    if added is not None:
                        artifacts.append(added)
                    if name in {"events.jsonl", "spans.jsonl"}:
                        tail, _size, _truncated = _read_tail_bytes(
                            source_path, _INCIDENT_TAIL_BYTES
                        )
                        trace_incidents.extend(
                            _trace_incidents(
                                tail,
                                run_id=recorder.run_id,
                                artifact=name,
                            )
                        )

            trace_inventory = list_trace_runs(limit=100)["runs"]
            excluded_live_trace_run_ids = [
                str(run["run_id"])
                for run in trace_inventory
                if run["run_id"] != current_id
                and bool(run.get("owner_process_alive"))
            ]
            prior = [
                run
                for run in trace_inventory
                if run["run_id"] != current_id
                and not bool(run.get("owner_process_alive"))
            ][:_PRIOR_RUN_COUNT]
            from .tracing import config as trace_config

            root = trace_config.default_trace_root()
            for run in prior:
                run_id = str(run["run_id"])
                run_scope: dict[str, Any] = {
                    "run_id": run_id,
                    "coverage": "bounded_tail",
                    "artifacts": {},
                }
                added = _add_sanitized_json_file_if_present(
                    zf,
                    root / run_id / "run.json",
                    f"traces/{run_id}/run.json",
                    coverage="full_metadata",
                )
                if added is not None:
                    artifacts.append(added)
                    run_scope["artifacts"]["run.json"] = added
                for name in ("events.jsonl", "spans.jsonl"):
                    source_path = root / run_id / name
                    tail, tail_meta = _bounded_jsonl_tail(source_path)
                    if not tail:
                        continue
                    tail_name = name.replace(".jsonl", "-tail.jsonl")
                    arcname = f"traces/{run_id}/{tail_name}"
                    zf.writestr(arcname, tail)
                    added_tail = {
                        "path": arcname,
                        "coverage": "bounded_tail",
                        **tail_meta,
                    }
                    artifacts.append(added_tail)
                    run_scope["artifacts"][tail_name] = added_tail
                    trace_incidents.extend(
                        _trace_incidents(
                            tail,
                            run_id=run_id,
                            artifact=name,
                        )
                    )
                prior_scopes.append(run_scope)

            incident_index = scrub_data(
                {
                    "schema_version": 1,
                    "capture_id": capture_id,
                    "captured_at": captured_at,
                    "diagnostic_run_id": _PROCESS_RUN_ID,
                    "current_log_run_id": _PROCESS_RUN_ID,
                    "current_trace_run_id": current_id,
                    "current_trace_flush_complete": recorder_flush_complete,
                    "capture_state": "active_at_capture",
                    "current_run_marker": captured_snapshot.get(
                        "process", {}
                    ).get("current_run_marker"),
                    "previous_run_marker": captured_snapshot.get(
                        "process", {}
                    ).get("previous_run_marker"),
                    "recent_log_errors": _recent_log_incidents(directory),
                    "recent_trace_incidents": _recent_trace_incidents(
                        trace_incidents
                    ),
                }
            )
            incident_bytes = json.dumps(
                incident_index, indent=2, default=str
            ).encode("utf-8")
            zf.writestr("incident-index.json", incident_bytes)
            artifacts.append(
                {
                    "path": "incident-index.json",
                    "coverage": "bounded_summary",
                    "included_bytes": len(incident_bytes),
                }
            )

            manifest = {
                "schema_version": 1,
                "capture_id": capture_id,
                "captured_at": captured_at,
                "diagnostic_run_id": _PROCESS_RUN_ID,
                "current_log_run_id": _PROCESS_RUN_ID,
                "current_trace_run_id": current_id,
                "current_trace_flush_complete": recorder_flush_complete,
                "included_run_ids": [
                    value
                    for value in [
                        current_id,
                        *(scope["run_id"] for scope in prior_scopes),
                    ]
                    if value
                ],
                "scope": {
                    "snapshot": "point_in_time_while_process_active",
                    "logs": (
                        "current_per_launch_bounded_rotations_plus_read_only_"
                        "legacy_flat_files_with_secret_redaction"
                    ),
                    "current_trace": (
                        "through_pre_copy_flush"
                        if current_id is not None
                        else "unavailable"
                    ),
                    "prior_traces": prior_scopes,
                    "excluded_live_trace_run_ids": excluded_live_trace_run_ids,
                    "prior_tail_byte_limit_per_artifact": _PRIOR_CONTEXT_BYTES,
                    "trace_jsonl_transformation": (
                        "parse_then_current_recursive_credential_redaction"
                    ),
                },
                "artifacts": artifacts,
            }
            zf.writestr(
                "bundle-manifest.json",
                json.dumps(manifest, indent=2, default=str).encode("utf-8"),
            )
    except BaseException:
        unlink_quietly(path)
        raise

    stamp = _dt.datetime.now().strftime("%Y-%m-%d-%H%M%S")
    return path, f"buildaspec-diagnostics-{stamp}.zip"


def unlink_quietly(path: Path) -> None:
    """Best-effort temp-file cleanup (the bundle route's background task)."""
    try:
        os.unlink(path)
    except OSError:
        pass


def _add_file_if_present(
    zf: zipfile.ZipFile,
    path: Path,
    arcname: str,
    *,
    coverage: str = "full_bounded_file",
) -> dict[str, Any] | None:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        before = path.stat()
        zf.write(path, arcname)
        copied_size = zf.getinfo(arcname).file_size
        try:
            after = path.stat()
        except OSError:
            after = None
        changed = (
            after is None
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
        )
        final_size = after.st_size if after is not None else before.st_size
        return {
            "path": arcname,
            "coverage": coverage,
            "source_size_bytes": final_size,
            "source_size_at_copy_start_bytes": before.st_size,
            "included_bytes": copied_size,
            "source_changed_while_copying": changed,
            "truncated": copied_size < final_size,
        }
    except OSError:
        pass
    return None


def _add_sanitized_jsonl_file_if_present(
    zf: zipfile.ZipFile,
    path: Path,
    arcname: str,
    *,
    coverage: str,
) -> dict[str, Any] | None:
    """Stream JSONL through today's redactor before placing it in a bundle.

    Current recordings are already scrubbed, but a run directory can outlive
    the version that wrote it. This export boundary prevents an older unsafe
    serializer or credential-bearing dynamic key from being republished in a
    support bundle. Streaming preserves the bundle's bounded-memory contract.
    """
    from .tracing.redaction import scrub_data

    try:
        if path.is_symlink() or not path.is_file():
            return None
        before = path.stat()
        source_bytes_read = 0
        included_bytes = 0
        record_count = 0
        invalid_records = 0
        with path.open("rb") as source, zf.open(arcname, "w") as target:
            for line in source:
                source_bytes_read += len(line)
                if not line.strip():
                    continue
                try:
                    parsed = json.loads(line)
                    transformed = json.dumps(
                        scrub_data(parsed),
                        ensure_ascii=True,
                        allow_nan=False,
                        separators=(",", ":"),
                    ).encode("utf-8") + b"\n"
                except (TypeError, ValueError):
                    invalid_records += 1
                    continue
                target.write(transformed)
                included_bytes += len(transformed)
                record_count += 1
        try:
            after = path.stat()
        except OSError:
            after = None
        changed = (
            after is None
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
        )
        final_size = after.st_size if after is not None else before.st_size
        return {
            "path": arcname,
            "coverage": coverage,
            "transformation": "current_recursive_credential_redaction",
            "source_size_bytes": final_size,
            "source_size_at_copy_start_bytes": before.st_size,
            "source_bytes_read": source_bytes_read,
            "included_bytes": included_bytes,
            "record_count": record_count,
            "invalid_record_count": invalid_records,
            "source_changed_while_copying": changed,
            "truncated": source_bytes_read < final_size,
        }
    except OSError:
        return None


def _add_redacted_text_file_if_present(
    zf: zipfile.ZipFile,
    path: Path,
    arcname: str,
) -> dict[str, Any] | None:
    """Copy a bounded text artifact with retrospective credential redaction.

    New activity-log records are scrubbed by ``_RedactingFormatter``. This
    second boundary also protects rotations written by an older app version
    before that formatter existed. Source-byte and transformed-byte counts
    remain separate so redaction is never misreported as truncation.
    """
    from .tracing.redaction import redact_text

    try:
        if path.is_symlink() or not path.is_file():
            return None
        before = path.stat()
        source, source_size, tail_truncated = _read_tail_bytes(
            path, _BUNDLE_LOG_TEXT_BYTES
        )
        try:
            after = path.stat()
        except OSError:
            after = None
        transformed = redact_text(
            source.decode("utf-8", errors="replace")
        ).replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
        zf.writestr(arcname, transformed)
        changed = (
            after is None
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
        )
        final_size = after.st_size if after is not None else before.st_size
        return {
            "path": arcname,
            "coverage": (
                "bounded_recent_tail" if tail_truncated else "full_bounded_file"
            ),
            "transformation": "credential_substring_redaction",
            "newline_normalization": "lf",
            "source_size_bytes": final_size,
            "source_size_at_copy_start_bytes": source_size,
            "source_bytes_read": len(source),
            "included_bytes": len(transformed),
            "source_changed_while_copying": changed,
            "truncated": tail_truncated or len(source) < final_size,
        }
    except OSError:
        return None


def _replace_raw_boot_nonces(value: Any, *, _depth: int = 0) -> Any:
    """Recursively replace legacy raw nonce fields with fingerprints."""
    if _depth > 12:
        return "<max-depth>"
    if isinstance(value, dict):
        result: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and key.lower() == "boot_nonce":
                fingerprint = hashlib.sha256(
                    str(item).encode("utf-8", errors="replace")
                ).hexdigest()
                result["boot_nonce_fingerprint"] = fingerprint
            else:
                result[key] = _replace_raw_boot_nonces(
                    item, _depth=_depth + 1
                )
        return result
    if isinstance(value, list):
        return [
            _replace_raw_boot_nonces(item, _depth=_depth + 1)
            for item in value
        ]
    return value


def _add_sanitized_json_file_if_present(
    zf: zipfile.ZipFile,
    path: Path,
    arcname: str,
    *,
    coverage: str,
) -> dict[str, Any] | None:
    """Copy JSON metadata after structured secret and nonce sanitization."""
    from .tracing.redaction import scrub_data

    try:
        if path.is_symlink() or not path.is_file():
            return None
        before = path.stat()
        if before.st_size > _RUN_META_READ_CAP:
            return None
        source = path.read_bytes()
        parsed = json.loads(source.decode("utf-8"))
        transformed = json.dumps(
            scrub_data(_replace_raw_boot_nonces(parsed)),
            indent=2,
            default=str,
        ).encode("utf-8")
        try:
            after = path.stat()
        except OSError:
            after = None
        zf.writestr(arcname, transformed)
        changed = (
            after is None
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
        )
        final_size = after.st_size if after is not None else before.st_size
        return {
            "path": arcname,
            "coverage": coverage,
            "transformation": "structured_secret_and_boot_nonce_redaction",
            "source_size_bytes": final_size,
            "source_size_at_copy_start_bytes": before.st_size,
            "source_bytes_read": len(source),
            "included_bytes": len(transformed),
            "source_changed_while_copying": changed,
            "truncated": len(source) < final_size,
        }
    except (OSError, TypeError, ValueError, UnicodeError):
        return None
