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
from collections import Counter
import hashlib
import logging
import os
import platform
import re
import sys
import threading
import time
import uuid
from typing import Any

from .config import (
    current_capture_level,
    default_trace_root,
    trace_dir_for_run,
    trace_enabled,
    trace_retention_policy,
)
from .recorder import TraceRecorder, get_recorder, set_recorder
from .redaction import redact_text
from .retention import prune_trace_runs
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
_STARTUP_FAILURE_LOCK = threading.Lock()
_STARTUP_FAILURE_STATE: dict[str, Any] | None = None
_MAX_STARTUP_FAILURE_COUNT = 2_147_483_647
_MAX_STARTUP_LOG_MESSAGE_CHARS = 1_000

_log = logging.getLogger(__name__)

# Request outcomes are deliberately a closed diagnostic vocabulary.  Route
# messages remain user-facing prose and may contain provider or document
# details; traces receive only one of these stable codes.  Explicit codes are
# the public API codes already returned by selected routes.  Every other
# handled error falls back to its HTTP-semantic category.
_DECLARED_REQUEST_OUTCOME_CODES = frozenset(
    {
        "csrf_required",
        "desktop_auth_required",
        "internal_error",
        "invalid_boot_nonce",
        "invalid_host",
        "invalid_origin",
        "module_section_mismatch",
        "qc_operation_conflict",
        "qc_preview_binding_invalid",
        "qc_preview_selection_mismatch",
        "qc_preview_stale",
        "stale_workspace",
        "tutorial_active",
        "tutorial_scenario_required",
        "validation_error",
        "workspace_busy",
    }
)
_DEFAULT_REQUEST_OUTCOME_CODES = {
    400: "bad_request",
    401: "authentication_required",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    408: "request_timeout",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    502: "upstream_error",
    503: "service_unavailable",
    504: "upstream_timeout",
}

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_IMPORT_LINE_RE = re.compile(r"^Line (\d+):")
_IMPORT_PERCENT_RE = re.compile(r"contains (\d+)% non-text elements")
_IMPORT_DEPTH_RE = re.compile(r"nesting deeper than (\d+) levels")
_MAX_IMPORT_WARNING_EVIDENCE = 64
_MAX_IMPORT_WARNING_CHARS = 4_000


def trace_startup_failure_state() -> dict[str, Any] | None:
    """Return bounded evidence for the latest tracing-startup failure."""
    with _STARTUP_FAILURE_LOCK:
        if _STARTUP_FAILURE_STATE is None:
            return None
        return dict(_STARTUP_FAILURE_STATE)


def _record_startup_failure(exc: BaseException) -> None:
    """Retain only type/time/count; never hold an exception or raw message."""
    global _STARTUP_FAILURE_STATE
    with _STARTUP_FAILURE_LOCK:
        previous_count = (
            int(_STARTUP_FAILURE_STATE.get("count") or 0)
            if _STARTUP_FAILURE_STATE is not None
            else 0
        )
        _STARTUP_FAILURE_STATE = {
            "exception_type": str(type(exc).__name__)[:120],
            "last_failed_at": time.time(),
            "count": min(_MAX_STARTUP_FAILURE_COUNT, previous_count + 1),
        }


def normalize_request_outcome_code(status: int, declared: str = "") -> str:
    """Return the stable trace code for an HTTP outcome.

    Unknown route-provided codes are never copied into a trace.  That keeps
    this field queryable across releases and prevents arbitrary response data
    from becoming diagnostic metadata.
    """
    candidate = str(declared or "").strip()
    if candidate in _DECLARED_REQUEST_OUTCOME_CODES:
        return candidate
    if status in _DEFAULT_REQUEST_OUTCOME_CODES:
        return _DEFAULT_REQUEST_OUTCOME_CODES[status]
    if 400 <= status < 500:
        return "client_error"
    if 500 <= status < 600:
        return "server_error"
    if 300 <= status < 400:
        return "redirect"
    if 200 <= status < 300:
        return "success"
    if 100 <= status < 200:
        return "informational"
    return "unknown_status"


def _import_warning_code(message: str) -> str:
    """Classify importer prose without retaining document-derived prose."""
    if message.startswith("This file does not look like a SectionFormat"):
        return "unstructured_document"
    if message.startswith("The master contains") and "non-text elements" in message:
        return "non_text_content"
    if message.startswith("The master carries pending tracked changes"):
        return "tracked_changes_accepted"
    if message.startswith("The master contains tables"):
        return "tables_flattened"
    if "content arrived before any article heading" in message:
        return "synthetic_article"
    if "nesting deeper than" in message and "clamped" in message:
        return "nesting_clamped"
    if "paragraph level jumped deeper than its context" in message:
        return "nesting_gap_rebased"
    if message.endswith("acknowledge this scope mismatch."):
        return "module_section_mismatch"
    if "before the first PART heading" in message:
        return "front_matter_preserved"
    if "was read from the cover page" in message:
        return "title_from_cover_page"
    if "was read from the page header/footer" in message:
        return "identity_from_chrome"
    if "no 'SECTION' keyword" in message:
        return "bare_header_line"
    if "after 'END OF SECTION' were not imported" in message:
        return "trailing_content"
    return "other"


def _import_warning_evidence(
    warning_messages: list[str] | tuple[str, ...] | None,
) -> tuple[dict[str, int], list[dict[str, Any]], int]:
    """Build bounded, content-free evidence for import warnings.

    Known templates retain only useful numeric facts.  Every entry gets a
    fingerprint of its bounded source string, which lets support determine
    whether two runs saw the same warning without placing the warning text
    (which can include a section title) into the trace.
    """
    if not warning_messages:
        return {}, [], 0
    counts: Counter[str] = Counter()
    evidence: list[dict[str, Any]] = []
    total = 0
    for raw in warning_messages:
        if not isinstance(raw, str):
            continue
        total += 1
        bounded = raw.strip()[:_MAX_IMPORT_WARNING_CHARS]
        code = _import_warning_code(bounded)
        counts[code] += 1
        if len(evidence) >= _MAX_IMPORT_WARNING_EVIDENCE:
            continue
        item: dict[str, Any] = {
            "code": code,
            "fingerprint": hashlib.sha256(
                bounded.encode("utf-8", errors="replace")
            ).hexdigest(),
        }
        line_match = _IMPORT_LINE_RE.match(bounded)
        if line_match:
            item["line"] = int(line_match.group(1))
        percent_match = _IMPORT_PERCENT_RE.search(bounded)
        if percent_match:
            item["percent"] = int(percent_match.group(1))
        depth_match = _IMPORT_DEPTH_RE.search(bounded)
        if depth_match:
            item["max_depth"] = int(depth_match.group(1))
        if len(raw.strip()) > _MAX_IMPORT_WARNING_CHARS:
            item["fingerprint_input_truncated"] = True
        evidence.append(item)
    return dict(sorted(counts.items())), evidence, total


def _environment_meta() -> dict[str, Any]:
    """Machine/build identity for run.json — a trace should explain itself."""
    from .. import diagnostics, settings

    server_identity = diagnostics.runtime_server_identity()

    payload = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "port": int(server_identity.get("bound_port") or settings.PORT),
        "pid": os.getpid(),
        "models": {
            "interview": settings.INTERVIEW_MODEL,
            "research": settings.RESEARCH_MODEL,
            "qc": settings.QC_MODEL,
        },
    }
    if server_identity:
        payload["server"] = server_identity
    return payload


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
            policy = trace_retention_policy()
            recorder = TraceRecorder(
                run_id=run_id,
                trace_dir=trace_dir_for_run(run_id),
                capture_level=current_capture_level(),
                app_version=settings.VERSION,
                max_run_bytes=policy.max_bytes,
            )
            environment = _environment_meta()
            environment["trace_retention_policy"] = policy.to_dict()
            recorder.start(
                model=settings.INTERVIEW_MODEL,
                module_id="",
                environment=environment,
            )
            try:
                maintenance = prune_trace_runs(
                    default_trace_root(),
                    current_run_id=run_id,
                    policy=policy,
                )
            except Exception as exc:  # noqa: BLE001
                maintenance = {
                    "policy": policy.to_dict(),
                    "failures": 1,
                    "limits_satisfied": False,
                    "exception_type": type(exc).__name__,
                }
            recorder.add_event(None, "trace_retention", **maintenance)
            set_recorder(recorder)
            if not _ATEXIT_REGISTERED:
                atexit.register(_stop_recorder)
                _ATEXIT_REGISTERED = True
            return recorder
        except Exception as exc:  # noqa: BLE001 — tracing must never sink the app
            _record_startup_failure(exc)
            try:
                # Do not attach exc_info: traceback rendering would append the
                # original exception message after this redacted copy.
                message = redact_text(str(exc))[:_MAX_STARTUP_LOG_MESSAGE_CHARS]
                _log.error(
                    "Tracing startup failed (%s): %s",
                    str(type(exc).__name__)[:120],
                    message or "<no message>",
                )
            except Exception:  # noqa: BLE001 - diagnostics stays fail-open
                pass
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


def request_event(
    *,
    method: str,
    path: str,
    status: int,
    duration_ms: int,
    request_id: str,
    query: str = "",
    declared_outcome_code: str = "",
    exception_type: str = "",
    workspace_before: dict[str, Any] | None = None,
    workspace_after: dict[str, Any] | None = None,
) -> None:
    """Record one bounded HTTP outcome without request/response bodies."""
    try:
        recorder = _ensure_recorder()
        if recorder is None:
            return
        fields: dict[str, Any] = {
            "method": str(method)[:16],
            "path": str(path)[:300],
            "status": int(status),
            "ms": max(0, int(duration_ms)),
            "request_id": str(request_id)[:64],
        }
        if query:
            fields["query"] = str(query)[:200]
        outcome_code = normalize_request_outcome_code(
            int(status), declared_outcome_code
        )
        if outcome_code:
            fields["outcome_code"] = outcome_code
        if exception_type:
            # A class name only, never ``str(exc)``.
            fields["exception_type"] = str(exception_type)[:100]
        for suffix, state in (
            ("before", workspace_before),
            ("after", workspace_after),
        ):
            if not isinstance(state, dict):
                continue
            fields[f"workspace_id_{suffix}"] = state.get("workspace_id")
            fields[f"workspace_scope_{suffix}"] = state.get("workspace_scope")
            fields[f"generation_{suffix}"] = state.get("generation")
        recorder.add_event(None, "api_request", **fields)
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


def import_event(
    *,
    blocks: int,
    warnings: int,
    tracked_changes: bool,
    detached: bool = False,
    warning_messages: list[str] | tuple[str, ...] | None = None,
    skipped_empty: int = 0,
    source_sha256: str = "",
    source_bytes: int = 0,
    zip_member_count: int = 0,
    zip_uncompressed_bytes: int = 0,
    spec_shape_detected: bool = True,
    workspace_id: int | None = None,
    workspace_scope: str = "",
    generation_before: int | None = None,
    generation_after: int | None = None,
    request_id: str = "",
    header_source: str = "",
    front_matter_count: int = 0,
    style_numbering_resolved: bool = False,
) -> None:
    """Record import provenance and sanitized parse evidence.

    The source hash is evidence, not source content.  Warning prose is never
    recorded; :func:`_import_warning_evidence` emits closed codes, numeric
    details, and bounded fingerprints instead.
    """
    try:
        recorder = _ensure_recorder()
        if recorder is None:
            return
        warning_counts, warning_evidence, warning_message_count = (
            _import_warning_evidence(warning_messages)
        )
        fields: dict[str, Any] = {
            "blocks": max(0, int(blocks)),
            "skipped_empty": max(0, int(skipped_empty)),
            "warnings": max(0, int(warnings)),
            "warning_message_count": warning_message_count,
            "warning_code_counts": warning_counts,
            "warning_evidence": warning_evidence,
            "warning_evidence_truncated": max(
                0, warning_message_count - len(warning_evidence)
            ),
            "tracked_changes": bool(tracked_changes),
            "detached": bool(detached),
            "spec_shape_detected": bool(spec_shape_detected),
            # Which parser path the master took: where its identity came
            # from, how much front matter preceded it, and whether its
            # outline lived on styles. Closed vocabulary and counts only.
            "header_source": str(header_source)[:16],
            "front_matter_count": max(0, int(front_matter_count)),
            "style_numbering_resolved": bool(style_numbering_resolved),
            "source_bytes": max(0, int(source_bytes)),
            "zip_member_count": max(0, int(zip_member_count)),
            "zip_uncompressed_bytes": max(
                0, int(zip_uncompressed_bytes)
            ),
            "workspace_id": workspace_id,
            "workspace_scope": str(workspace_scope)[:32],
            "generation_before": generation_before,
            "generation_after": generation_after,
            "request_id": str(request_id)[:64],
        }
        if _SHA256_RE.fullmatch(str(source_sha256 or "")):
            fields["source_sha256"] = str(source_sha256).lower()
        recorder.add_event(
            None,
            KIND_IMPORT,
            **fields,
        )
    except Exception:  # noqa: BLE001
        pass
