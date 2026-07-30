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

Logs live in ``<user_state_dir>/BuildASpec/logs`` — the *state* root,
beside ``traces/``, deliberately not :func:`app_paths.app_config_dir`
(the *config* root holding the key file and templates). On Windows both
roots resolve to ``%LOCALAPPDATA%\\BuildASpec``, so users find every
forensic artifact in one folder.

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
import json
import logging
import logging.handlers
import os
import platform
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

from platformdirs import user_state_dir

ENV_LOG = "BUILD_A_SPEC_LOG"
ENV_LOG_LEVEL = "BUILD_A_SPEC_LOG_LEVEL"
ENV_LOG_DIR = "BUILD_A_SPEC_LOG_DIR"

LOG_FILENAME = "buildaspec.log"
CRASH_FILENAME = "crash-faulthandler.log"
RUN_MARKER_FILENAME = "run-marker.json"

_LOG_MAX_BYTES = 10 * 1024 * 1024
_LOG_BACKUP_COUNT = 5
# tail_log never reads more than this from the end of the file, so the
# endpoint's cost is bounded regardless of how large the log grows.
_TAIL_READ_CAP = 2 * 1024 * 1024

_DISABLE_TOKENS = frozenset({"0", "false", "no", "off"})

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
    "[%(threadName)s] %(message)s"
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


def log_dir() -> Path:
    """``<state dir>/BuildASpec/logs`` (override: ``BUILD_A_SPEC_LOG_DIR``)."""
    override = os.environ.get(ENV_LOG_DIR)
    if override:
        return Path(os.path.expanduser(os.path.expandvars(override)))
    return Path(user_state_dir("BuildASpec", appauthor=False)) / "logs"


def current_log_file() -> Path | None:
    """The active log path, or None when logging is disabled."""
    if not log_enabled():
        return None
    return log_dir() / LOG_FILENAME


def init_logging(*, force: bool = False) -> Path | None:
    """Attach the rotating file handler to the root logger. Idempotent.

    Returns the log path, or None when disabled. ``force=True`` tears the
    previous configuration down first (test hygiene — the
    ``reset_thinking_display_probe`` posture for process-global state).
    Never touches stdout/stderr: the handler is a file, so
    ``main._ensure_std_streams`` ordering is irrelevant.
    """
    global _INITIALIZED, _HANDLER, _PREV_ROOT_LEVEL
    with _INIT_LOCK:
        if _INITIALIZED and not force:
            return current_log_file()
        if force:
            _teardown_locked()
        if not log_enabled():
            _INITIALIZED = True
            return None
        try:
            directory = log_dir()
            directory.mkdir(parents=True, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(
                directory / LOG_FILENAME,
                maxBytes=_LOG_MAX_BYTES,
                backupCount=_LOG_BACKUP_COUNT,
                encoding="utf-8",
                delay=True,
            )
            handler.setFormatter(
                logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT)
            )
            root = logging.getLogger()
            root.addHandler(handler)
            _PREV_ROOT_LEVEL = root.level
            root.setLevel(configured_level())
            for name, level in _TAMED_LOGGERS.items():
                logging.getLogger(name).setLevel(level)
            _HANDLER = handler
            _INITIALIZED = True
        except Exception:  # noqa: BLE001 — diagnostics must never sink the app
            _INITIALIZED = True
            return None
        _install_crash_capture_locked()
        return directory / LOG_FILENAME


def log_startup_banner() -> None:
    """One block of INFO lines identifying the run. Never raises."""
    try:
        from . import settings
        from .tracing import config as trace_config

        offset = _dt.datetime.now().astimezone().strftime("%z")
        _log.info(
            "%s %s starting — platform=%s python=%s frozen=%s pid=%d "
            "port=%d utc_offset=%s",
            settings.APP_NAME,
            settings.VERSION,
            platform.platform(),
            platform.python_version(),
            bool(getattr(sys, "frozen", False)),
            os.getpid(),
            settings.PORT,
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


def _install_crash_capture_locked() -> None:
    """faulthandler + exception hooks + the unclean-shutdown marker."""
    global _CRASH_HANDLE, _PREV_SYS_EXCEPTHOOK, _PREV_THREADING_EXCEPTHOOK
    global _HOOKS_INSTALLED
    if _HOOKS_INSTALLED:
        return
    try:
        directory = log_dir()
        directory.mkdir(parents=True, exist_ok=True)

        previous = _read_run_marker()
        if previous is not None and previous.get("clean") is not True:
            _log.warning(
                "previous run (pid %s, started %s) did not shut down "
                "cleanly — check %s and the latest trace for what it was "
                "doing",
                previous.get("pid"),
                _format_ts(previous.get("started_at")),
                CRASH_FILENAME,
            )
        _write_run_marker(clean=False)
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


def _read_run_marker() -> dict[str, Any] | None:
    path = log_dir() / RUN_MARKER_FILENAME
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — missing or torn marker = no verdict
        return None


def _write_run_marker(*, clean: bool) -> None:
    """Atomic tmp+replace (the updates.py discipline)."""
    from . import settings

    directory = log_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / RUN_MARKER_FILENAME
    payload: dict[str, Any] = {
        "started_at": _RUN_STARTED_AT,
        "pid": os.getpid(),
        "version": settings.VERSION,
        "clean": clean,
    }
    if clean:
        payload["ended_at"] = time.time()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


_RUN_STARTED_AT = time.time()


def _format_ts(value: Any) -> str:
    try:
        return _dt.datetime.fromtimestamp(float(value)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    except Exception:  # noqa: BLE001
        return repr(value)


# ---- read-only surfaces behind /api/diagnostics* ------------------------


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
        session_block: dict[str, Any] = {
            "history_len": len(session.history),
            "doc_version_index": store.index,
            "doc_version_count": len(store.versions),
            "baseline_index": store.baseline_index,
            "doc_empty": store.doc.is_empty(),
            "figures": len(session.figures.figures),
            "references": len(session.references.docs),
            "suggested_prompts": len(session.suggested_prompts),
            "turn_active": session.turn_active,
            "stop_requested": session.stop_requested.is_set(),
            "unsaved": sessions.has_unsaved_progress(session),
            "import_report_present": session.import_report is not None,
            "module_id": session.module.module_id,
            "discipline": effective_discipline(session),
            "research": research_block,
            "source": {
                "retained": source_bytes is not None,
                "filename": session.source_docx_filename,
                "bytes": len(source_bytes) if source_bytes else 0,
            },
        }
        busy = sessions.busy_reasons(session)
        usage = session.usage.snapshot()
        generation = session.generation

    key = dict(api_key_store.key_status())
    if key.get("source") == "env":
        key["env_locked"] = True

    tracing_block: dict[str, Any] = {
        "enabled": trace_config.trace_enabled(),
        "level": trace_config.current_capture_level(),
        "root": str(trace_config.default_trace_root()),
    }
    recorder = get_recorder()
    if recorder is not None:
        tracing_block["run_id"] = recorder.run_id
        tracing_block["run_dir"] = str(recorder.trace_dir)

    log_file = current_log_file()
    logging_block: dict[str, Any] = {
        "enabled": log_enabled(),
        "level": logging.getLevelName(configured_level()),
        "dir": str(log_dir()),
    }
    if log_file is not None:
        logging_block["file"] = str(log_file)
        try:
            logging_block["size_bytes"] = log_file.stat().st_size
        except OSError:
            logging_block["size_bytes"] = 0

    payload: dict[str, Any] = {
        "generated_at": time.time(),
        "app": {
            "name": settings.APP_NAME,
            "version": settings.VERSION,
            "platform": platform.platform(),
            "python": platform.python_version(),
            "frozen": bool(getattr(sys, "frozen", False)),
            "dev_mode": settings.dev_mode(),
            "port": settings.PORT,
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

    root = trace_config.default_trace_root()
    recorder = get_recorder()
    current_id = recorder.run_id if recorder is not None else None
    runs: list[dict[str, Any]] = []
    try:
        candidates = [p for p in root.iterdir() if p.is_dir()]
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


def build_bundle() -> tuple[Path, str]:
    """Assemble the diagnostics zip into a temp file; (path, filename).

    Contents: the scrubbed snapshot, every log file (rotation already
    bounds them), the CURRENT trace run in full, and ``run.json`` only
    for the three most recent prior runs — full prior runs are unbounded
    and the trace list in the modal names the folder for anyone who
    needs more.

    Written to a temporary FILE, not memory: a deep-trace run can be
    hundreds of MB, and an in-memory zip of it would spike (or kill) the
    desktop process exactly when the user is trying to report a problem.
    The route streams the file out and deletes it afterwards. Before
    copying the live run the recorder queue is flushed to disk, so a
    bundle grabbed right after an incident contains the incident.
    """
    from .tracing.recorder import get_recorder

    recorder = get_recorder()
    if recorder is not None:
        recorder.flush(timeout=2.0)

    handle = tempfile.NamedTemporaryFile(
        prefix="buildaspec-diagnostics-", suffix=".zip", delete=False
    )
    path = Path(handle.name)
    try:
        with handle, zipfile.ZipFile(
            handle, "w", compression=zipfile.ZIP_DEFLATED
        ) as zf:
            zf.writestr(
                "snapshot.json",
                json.dumps(snapshot(), indent=2, default=str),
            )

            directory = log_dir()
            log_names = [LOG_FILENAME] + [
                f"{LOG_FILENAME}.{i}" for i in range(1, _LOG_BACKUP_COUNT + 1)
            ]
            log_names += [CRASH_FILENAME, RUN_MARKER_FILENAME]
            for name in log_names:
                _add_file_if_present(zf, directory / name, f"logs/{name}")

            current_id = None
            if recorder is not None:
                current_id = recorder.run_id
                for name in (
                    "spans.jsonl",
                    "events.jsonl",
                    "prompts.jsonl",
                    "run.json",
                ):
                    _add_file_if_present(
                        zf,
                        recorder.trace_dir / name,
                        f"traces/{recorder.run_id}/{name}",
                    )

            prior = [
                run
                for run in list_trace_runs(limit=10)["runs"]
                if run["run_id"] != current_id
            ][:3]
            from .tracing import config as trace_config

            root = trace_config.default_trace_root()
            for run in prior:
                _add_file_if_present(
                    zf,
                    root / run["run_id"] / "run.json",
                    f"traces/{run['run_id']}/run.json",
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
    zf: zipfile.ZipFile, path: Path, arcname: str
) -> None:
    try:
        if path.is_file():
            zf.write(path, arcname)
    except OSError:
        pass
