"""Never-raise capture hooks for Build-a-Spec's surfaces.

The thin Build-a-Spec-native layer over the ported recorder (the analog of
Spec Critic's ``capture_hooks.py``, at drafting-app scale): one lazily
started app-lifetime recorder, plus wrappers the engine calls around
turns, tool dispatches, research runs, audits, and imports. Every function
swallows every exception — tracing must never sink a turn.

The recorder starts on first capture (when tracing is enabled) with a
run id of ``session-<launch hex>`` and stops at interpreter exit; session
resets stay inside the same trace, which is the useful forensic unit for
a desktop app launch.
"""
from __future__ import annotations

import atexit
import threading
import time
import uuid
from typing import Any

from .config import current_capture_level, trace_dir_for_run, trace_enabled
from .recorder import TraceRecorder, get_recorder, set_recorder
from .spans import (
    KIND_COMPLIANCE,
    KIND_IMPORT,
    KIND_RESEARCH,
    KIND_TOOL_DISPATCH,
    KIND_TURN,
    STATUS_ERROR,
    STATUS_OK,
    SpanHandle,
)

_START_LOCK = threading.Lock()
_ATEXIT_REGISTERED = False


def _ensure_recorder() -> TraceRecorder | None:
    """Start the app-lifetime recorder on first use (or None when off)."""
    global _ATEXIT_REGISTERED
    if not trace_enabled():
        return None
    recorder = get_recorder()
    if recorder is not None:
        return recorder
    with _START_LOCK:
        recorder = get_recorder()
        if recorder is not None:
            return recorder
        try:
            from .. import settings

            run_id = f"session-{uuid.uuid4().hex[:8]}-{int(time.time())}"
            recorder = TraceRecorder(
                run_id=run_id,
                trace_dir=trace_dir_for_run(run_id),
                capture_level=current_capture_level(),
                app_version=settings.VERSION,
            )
            recorder.start(
                model=settings.INTERVIEW_MODEL,
                module_id="",
            )
            set_recorder(recorder)
            if not _ATEXIT_REGISTERED:
                atexit.register(_stop_recorder)
                _ATEXIT_REGISTERED = True
            return recorder
        except Exception:  # noqa: BLE001 — tracing must never sink the app
            return None


def _stop_recorder() -> None:
    try:
        recorder = get_recorder()
        if recorder is not None:
            recorder.stop()
            set_recorder(None)
    except Exception:  # noqa: BLE001
        pass


def turn_start(*, model: str, history_len: int) -> SpanHandle | None:
    try:
        recorder = _ensure_recorder()
        if recorder is None:
            return None
        return recorder.open_span(
            KIND_TURN,
            f"turn #{history_len // 2 + 1}",
            inputs={"model": model, "history_messages": history_len},
        )
    except Exception:  # noqa: BLE001
        return None


def turn_end(
    handle: SpanHandle | None,
    *,
    stop_reason: Any = None,
    doc_changed: bool = False,
    error: str = "",
) -> None:
    try:
        recorder = get_recorder()
        if recorder is None or handle is None:
            return
        recorder.close_span(
            handle,
            outputs={"stop_reason": stop_reason, "doc_changed": doc_changed},
            status=STATUS_ERROR if error else STATUS_OK,
            error=error or None,
        )
    except Exception:  # noqa: BLE001
        pass


def tool_dispatch(
    parent: SpanHandle | None, *, ops: int, ok: bool, error: str = ""
) -> None:
    try:
        recorder = get_recorder()
        if recorder is None:
            return
        recorder.add_event(
            parent,
            KIND_TOOL_DISPATCH,
            ops=ops,
            ok=ok,
            error=error,
        )
    except Exception:  # noqa: BLE001
        pass


def research_start(*, project: str, dimensions: int) -> SpanHandle | None:
    try:
        recorder = _ensure_recorder()
        if recorder is None:
            return None
        return recorder.open_span(
            KIND_RESEARCH,
            "requirements research",
            inputs={"project": project, "dimensions": dimensions},
        )
    except Exception:  # noqa: BLE001
        return None


def research_event(handle: SpanHandle | None, event: dict) -> None:
    try:
        recorder = get_recorder()
        if recorder is None or handle is None:
            return
        recorder.add_event(handle, "research_progress", **dict(event))
    except Exception:  # noqa: BLE001
        pass


def research_end(
    handle: SpanHandle | None, *, status: str, items: int = 0, error: str = ""
) -> None:
    try:
        recorder = get_recorder()
        if recorder is None or handle is None:
            return
        recorder.close_span(
            handle,
            outputs={"status": status, "items": items},
            status=STATUS_ERROR if error else STATUS_OK,
            error=error or None,
        )
    except Exception:  # noqa: BLE001
        pass


def audit_span(*, controlling: int) -> SpanHandle | None:
    try:
        recorder = _ensure_recorder()
        if recorder is None:
            return None
        return recorder.open_span(
            KIND_COMPLIANCE,
            "compliance audit",
            inputs={"controlling_requirements": controlling},
        )
    except Exception:  # noqa: BLE001
        return None


def audit_end(
    handle: SpanHandle | None, *, status: str, findings: int = 0, error: str = ""
) -> None:
    try:
        recorder = get_recorder()
        if recorder is None or handle is None:
            return
        recorder.close_span(
            handle,
            outputs={"status": status, "findings": findings},
            status=STATUS_ERROR if error else STATUS_OK,
            error=error or None,
        )
    except Exception:  # noqa: BLE001
        pass


def import_event(*, blocks: int, warnings: int, tracked_changes: bool) -> None:
    try:
        recorder = _ensure_recorder()
        if recorder is None:
            return
        recorder.add_event(
            None,
            KIND_IMPORT,
            blocks=blocks,
            warnings=warnings,
            tracked_changes=tracked_changes,
        )
    except Exception:  # noqa: BLE001
        pass
