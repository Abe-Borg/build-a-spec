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
import os
import platform
import sys
import threading
import time
import uuid
from typing import Any

from .config import current_capture_level, trace_dir_for_run, trace_enabled
from .recorder import TraceRecorder, get_recorder, set_recorder
from .spans import (
    KIND_COMPLIANCE,
    KIND_IMPORT,
    KIND_QC,
    KIND_RESEARCH,
    KIND_TOOL_DISPATCH,
    KIND_TURN,
    STATUS_ERROR,
    STATUS_OK,
    SpanHandle,
)

_START_LOCK = threading.Lock()
_ATEXIT_REGISTERED = False


def _environment_meta() -> dict[str, Any]:
    """Machine/build identity for run.json — a trace should explain itself."""
    from .. import settings

    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "port": settings.PORT,
        "pid": os.getpid(),
        "models": {
            "interview": settings.INTERVIEW_MODEL,
            "research": settings.RESEARCH_MODEL,
            "qc": settings.QC_MODEL,
        },
    }


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
                environment=_environment_meta(),
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


def app_event(event_type: str, **fields: Any) -> None:
    """Record a run-level application event (never raises).

    The one-liner the REST layer calls for every state-changing action —
    edits, exports, project saves, QC dispositions, stops, key changes.
    Starts the recorder if this is the first capture of the run, so a
    launch that never reaches a chat turn still leaves a trace.
    """
    try:
        recorder = _ensure_recorder()
        if recorder is None:
            return
        recorder.add_event(None, event_type, **fields)
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
    usage: dict | None = None,
    error: str = "",
) -> None:
    try:
        recorder = get_recorder()
        if recorder is None or handle is None:
            return
        outputs: dict[str, Any] = {
            "stop_reason": stop_reason,
            "doc_changed": doc_changed,
        }
        if usage:
            outputs["usage"] = dict(usage)
        recorder.close_span(
            handle,
            outputs=outputs,
            status=STATUS_ERROR if error else STATUS_OK,
            error=error or None,
        )
    except Exception:  # noqa: BLE001
        pass


def turn_round(
    handle: SpanHandle | None,
    *,
    round_index: int,
    stop_reason: Any,
    duration_ms: int,
    usage: dict | None = None,
    tool_uses: int = 0,
    web_searches: int = 0,
    web_fetches: int = 0,
) -> None:
    """One ``round_end`` event per streaming round of a turn (never raises).

    The per-round record is what answers "which round stalled / where did
    the tokens go" without reconstructing the turn from raw SSE.
    """
    try:
        recorder = get_recorder()
        if recorder is None or handle is None:
            return
        recorder.add_event(
            handle,
            "round_end",
            round=round_index,
            stop_reason=stop_reason,
            ms=duration_ms,
            usage=dict(usage) if usage else {},
            tool_uses=tool_uses,
            web_searches=web_searches,
            web_fetches=web_fetches,
        )
    except Exception:  # noqa: BLE001
        pass


def turn_prompts(
    handle: SpanHandle | None,
    *,
    system_text: str,
    context_text: str,
    user_text: str,
) -> None:
    """Record the turn's prompt material as one ``prompt_refs`` event.

    At the default capture level each text goes through
    ``recorder.prompt_ref`` — content-hashed into prompts.jsonl once per
    distinct text (the stable system block therefore costs one entry per
    app run); deep mode inlines the text into the event itself.
    """
    try:
        recorder = get_recorder()
        if recorder is None or handle is None:
            return
        recorder.add_event(
            handle,
            "prompt_refs",
            system=recorder.prompt_ref("system", system_text),
            project_context=recorder.prompt_ref("project_context", context_text),
            user=recorder.prompt_ref("user", user_text),
        )
    except Exception:  # noqa: BLE001
        pass


def note(handle: SpanHandle | None, message: str, **fields: Any) -> None:
    """Attach a free-form ``note`` event to a span (never raises).

    Used for one-off forensic breadcrumbs like the thinking.display
    capability degrade — cheap to record, occasionally load-bearing when a
    turn behaves unexpectedly.
    """
    try:
        recorder = get_recorder()
        if recorder is None:
            return
        recorder.add_event(handle, "note", message=message, **fields)
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
        # The sink event's own "type" key would collide with add_event's
        # positional ``type`` parameter — rename it to ``event_type``.
        fields = dict(event)
        event_type = str(fields.pop("type", "") or "")
        if event_type:
            fields["event_type"] = event_type
        recorder.add_event(handle, "research_progress", **fields)
    except Exception:  # noqa: BLE001
        pass


def research_end(
    handle: SpanHandle | None,
    *,
    status: str,
    items: int = 0,
    error: str = "",
    incomplete_dimensions: list[dict] | None = None,
) -> None:
    """Close the research span.

    ``incomplete_dimensions`` records which coverage never completed, as
    ``{dimension_id, title, error_kind}`` — a partial run reports
    ``status="complete"``, so without it a trace cannot answer "which
    coverage failed" and a support bundle has to open the project to guess.
    Sanitized kinds only: the dimension's own error MESSAGE can carry
    provider exception text and never enters a trace.
    """
    try:
        recorder = get_recorder()
        if recorder is None or handle is None:
            return
        outputs: dict = {"status": status, "items": items}
        if incomplete_dimensions:
            outputs["incomplete_dimensions"] = incomplete_dimensions
        recorder.close_span(
            handle,
            outputs=outputs,
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


def qc_start(*, lenses: int) -> SpanHandle | None:
    try:
        recorder = _ensure_recorder()
        if recorder is None:
            return None
        return recorder.open_span(
            KIND_QC, "final qc", inputs={"lenses": lenses}
        )
    except Exception:  # noqa: BLE001
        return None


def qc_event(handle: SpanHandle | None, event: dict) -> None:
    try:
        recorder = get_recorder()
        if recorder is None or handle is None:
            return
        # Same "type"-key collision as research_event — see there.
        fields = dict(event)
        event_type = str(fields.pop("type", "") or "")
        if event_type:
            fields["event_type"] = event_type
        recorder.add_event(handle, "qc_progress", **fields)
    except Exception:  # noqa: BLE001
        pass


def qc_end(
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
