"""FastAPI application: SSE chat, document model, key management, static frontend.

Endpoints (all JSON unless noted):

- ``GET  /api/health``        → app/model/key status for the UI header.
- ``POST /api/key``           → save an Anthropic API key (keyring → file).
- ``GET  /api/key/status``    → where the key resolves from + a masked tail.
- ``DELETE /api/key``         → remove the stored key (keyring + files).
- ``POST /api/key/test``      → validate a candidate/stored key (no save).
- ``POST /api/session/reset`` → clear the conversation and the document.
- ``POST /api/chat``          → Server-Sent Events stream of turn events.
- ``POST /api/chat/stop``     → stop the in-flight turn (Claude.ai-style);
  keeps whatever text/edits landed so far instead of rolling back (409 if
  no turn is streaming).
- ``POST /api/draft/full``    → the canned full-section draft directive for
  the frontend to send through the normal chat path (409 while a turn or
  research runs).
- ``POST /api/onboarding/demo`` → the guided-tour demo directive (Batch 6)
  for the frontend to send through the normal chat path (409 while a turn
  or research runs, or when the document is not blank).
- ``GET  /api/doc``           → current document snapshot + open questions.
- ``POST /api/doc/undo``      → step to the previous per-turn version.
- ``POST /api/doc/redo``      → step forward again.
- ``POST /api/doc/edit``      → apply a manual edit batch (one undoable
  version; 409 while a model turn streams).
- ``GET  /api/doc/diff``      → serialized version diff (``?base=N[&cur=M]``)
  for the in-app compare view (Batch 5).
- ``GET  /api/export/docx``   → explicit ``?mode=source|normalized`` DOCX
  export. Imported source mode applies only verified body edits (structural
  edits require a proven flat island with isolated direct Word list bindings);
  ``?redline=master`` or ``?redline=version&base=N`` remains a normalized
  semantic tracked-changes export.
- ``POST /api/research/start``  → launch the requirements-research fan-out
  (requires a complete project profile; 409 while one runs).
- ``GET  /api/usage``         → this session's billed usage + est. cost.
- ``GET  /api/research/status`` → research state + event log + profile view.
- ``GET  /api/research/stream`` → SSE follow of the active/last run.
- ``POST /api/research/stop``  → stop the running research fan-out (discards
  whatever it found so far; 409 if none is running).
- ``POST /api/qc/start``       → launch Final QC on Fable 5 (Batch 4).
- ``GET  /api/qc/status``      → QC state + event log + result view.
- ``GET  /api/qc/stream``      → SSE follow of the active/last QC run.
- ``POST /api/qc/stop``        → stop the running Final QC pass (discards
  whatever it found so far; 409 if none is running).
- ``POST /api/qc/apply``       → apply accepted findings' fixes (one undo step).
- ``POST /api/qc/dismiss``     → dismiss a finding (remembered across re-runs).
- ``GET  /api/qc/export``      → the QC memo as a standalone ``.docx``.
- ``GET  /api/readiness``      → deterministic "can it go out the door" checklist.
- ``GET  /api/project/save``  → native ``.baspec`` package (semantic state +
  exact source DOCX when available).
- ``POST /api/project/load-file`` → stage and restore ``.baspec`` or legacy JSON.
- ``POST /api/project/load``  → legacy source-less JSON compatibility load.

When ``frontend/dist`` exists (production / packaged), it is served at
``/``; in development the Vite dev server proxies ``/api`` here instead.
"""
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import quote

import anthropic
from fastapi import Body, FastAPI, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import settings, sessions
from .api_key_store import (
    delete_api_key,
    key_status,
    load_api_key,
    save_api_key,
)
from .llm.client import (
    MissingApiKeyError,
    build_probe_client,
    get_client,
    reset_client_cache,
)
from .llm.conversation import SessionState, standards_payload, stream_user_turn
from .llm.prompts import (
    FULL_DRAFT_DIRECTIVE,
    onboarding_demo_directive,
    sanitize_discipline,
    sanitize_project_context,
)
from .project_profile import ProjectProfile
from .qc.engine import QCSourceGuard
from .spec_modules import AVAILABLE_MODULES, DEFAULT_MODULE, get_module
from .spec_doc import SpecEditError, diff_sections, lint_document, open_questions
from .spec_doc.docx_export import (
    build_docx,
    build_qc_memo,
    export_filename,
    redline_filename,
)
from .spec_doc.importer import MasterImportError, parse_master_docx
from .spec_doc.model import SpecSection, apply_edits, iter_paragraphs
from .spec_doc.project import chat_transcript, load_project
from .spec_doc.project_package import (
    PACKAGE_MEDIA_TYPE,
    ProjectPackageError,
    ProjectPackageTooLargeError,
    parse_project_file,
    read_project_upload_bounded,
)
from .spec_doc.source_mapping import SourceBodyMap, source_blocker_message
from .spec_doc.source_patch import (
    SourcePatchError,
    build_source_preserving_docx,
    source_patch_readiness,
)
from .spec_doc.source_package import (
    SourcePackageError,
    UploadTooLargeError,
    build_import_report,
    inspect_docx_package,
    read_upload_bounded,
    sanitize_source_filename,
)

_DEV_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


class ChatRequest(BaseModel):
    message: str


class SaveKeyRequest(BaseModel):
    api_key: str


class EditDocRequest(BaseModel):
    ops: list[dict[str, Any]]


class OnboardingDemoRequest(BaseModel):
    discipline: str


class SessionResetRequest(BaseModel):
    """Optional body for POST /api/session/reset (Batch 10).

    Absent body = the historical contract exactly (reset keeps the active
    module and discipline). ``module_id`` blank keeps the current module;
    unknown ids degrade to the default (the registry posture). Discipline
    only sticks when the resulting module is open-catalog (the invariant).
    ``project_context`` is optional priming text; it applies to any module.
    """

    module_id: str = ""
    discipline: str = ""
    project_context: str = ""


class QcApplyRequest(BaseModel):
    finding_ids: list[str]


class QcDismissRequest(BaseModel):
    finding_id: str
    reason: str | None = None


class TestKeyRequest(BaseModel):
    api_key: str | None = None


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _attachment_headers(filename: str) -> dict[str, str]:
    """Content-Disposition safe for any filename (headers are latin-1).

    ASCII fallback plus the RFC 5987 ``filename*`` form — a section title
    with an em dash (or any non-latin-1 character) must not 500 the
    download or inject into the header.
    """
    fallback = re.sub(r"[^A-Za-z0-9 ._-]+", "_", filename).strip() or "download"
    return {
        "Content-Disposition": (
            f'attachment; filename="{fallback}"; '
            f"filename*=UTF-8''{quote(filename, safe='')}"
        )
    }


def _source_baseline(session) -> SpecSection | None:
    """Return the immutable imported semantic baseline, when still present."""
    index = session.doc.baseline_index
    if index is None or not 0 <= index < len(session.doc.versions):
        return None
    try:
        return SpecSection.from_dict(session.doc.versions[index])
    except (TypeError, ValueError):  # pragma: no cover - store validates on load
        return None


def _source_readiness(session):
    baseline = _source_baseline(session)
    if baseline is None:
        return None
    return source_patch_readiness(
        source_bytes=session.source_docx_bytes,
        source_map=getattr(session, "source_docx_map", None),
        baseline=baseline,
        current=session.doc.doc,
    )


def _qc_source_guard(session) -> QCSourceGuard | None:
    """Capture immutable source inputs for the exact QC document snapshot.

    Presence means the runner must enforce source preservation. An active
    source-backed session with malformed or missing context still returns a
    required, incomplete guard so proposal validation fails closed.
    """
    source_map = getattr(session, "source_docx_map", None)
    baseline_index = session.doc.baseline_index
    if baseline_index is None:
        return None
    baseline_valid = (
        not isinstance(baseline_index, bool)
        and isinstance(baseline_index, int)
        and 0 <= baseline_index < len(session.doc.versions)
    )
    if baseline_valid and session.doc.index < baseline_index:
        # The import was undone. Match SessionState.apply_doc_edits: a new
        # pre-import branch is not constrained by the abandoned source.
        return None
    return QCSourceGuard(
        required=True,
        source_bytes=(
            session.source_docx_bytes
            if isinstance(session.source_docx_bytes, bytes)
            else None
        ),
        source_map=(
            source_map if isinstance(source_map, SourceBodyMap) else None
        ),
        baseline=_source_baseline(session) if baseline_valid else None,
    )


def _source_preservation_payload(
    session, preservation
) -> dict[str, Any] | None:
    """Describe source export and mutation as separate capabilities.

    ``preservation_ready`` predates package-wide mutation blockers and means
    only that the *current* state can be exported through source mode.  A
    signed, protected, revision-bearing, or active-content source is therefore
    ready when it is an exact no-op, even though no body mutation is allowed.
    Keep that boolean for compatibility and expose the distinction here.
    """
    imported_scope = (
        session.import_report is not None
        or session.doc.baseline_index is not None
    )
    if not imported_scope:
        return None

    source_available = session.source_docx_bytes is not None
    source_map = getattr(session, "source_docx_map", None)
    global_blockers = (
        tuple(source_map.global_blockers)
        if isinstance(source_map, SourceBodyMap)
        else ()
    )
    runtime_mutation_blockers = (
        tuple(preservation.mutation_blockers)
        if preservation is not None
        else ()
    )

    if preservation is not None and preservation.ready:
        if preservation.no_op and (
            global_blockers or runtime_mutation_blockers
        ):
            status = "pass_through_only"
            blockers = [
                {
                    "uid": "source",
                    "blocker": blocker,
                    "message": source_blocker_message(blocker),
                }
                for blocker in global_blockers
            ]
            blockers.extend(
                issue.to_dict() for issue in runtime_mutation_blockers
            )
        else:
            status = "ready"
            blockers = []
    elif source_available:
        status = "blocked"
        blockers = (
            [issue.to_dict() for issue in preservation.blockers]
            if preservation is not None
            else [
                {
                    "uid": "source",
                    "blocker": "baseline_unavailable",
                    "message": "the imported semantic baseline is unavailable",
                }
            ]
        )
    else:
        status = "unavailable"
        blockers = (
            [issue.to_dict() for issue in preservation.blockers]
            if preservation is not None
            else [
                {
                    "uid": "source",
                    "blocker": "source_unavailable",
                    "message": "the exact imported DOCX is unavailable",
                }
            ]
        )

    return {
        "status": status,
        "source_export_ready": bool(preservation and preservation.ready),
        "exact_original_available": source_available,
        # This is deliberately document-level and bounded. Per-UID edit
        # eligibility is a separate future contract.
        "body_editing": "bounded" if status == "ready" else "disabled",
        "no_op": bool(preservation and preservation.no_op),
        "changed_uids": list(preservation.changed_uids) if preservation else [],
        "blockers": blockers,
    }


def _doc_payload(session) -> dict[str, Any]:
    profile = ProjectProfile.from_dict(session.doc.doc.project_profile)
    preservation = _source_readiness(session)
    return {
        "doc": session.doc.snapshot(),
        "open_questions": open_questions(session.doc.doc),
        "lint": lint_document(session.doc.doc, session.module),
        "standards": standards_payload(session),
        "profile_complete": bool(profile and profile.is_complete()),
        "research_status": session.research.status,
        # The imported-master version index (Batch 5), for the compare
        # picker's "Master (import)" option; ``None`` for from-scratch.
        "baseline_index": session.doc.baseline_index,
        # Chat-authored figures (diagrams/schematics/tables) — full source so
        # the frontend can render + offer downloads. Not part of the doc tree.
        "figures": session.figures.snapshot(),
        # Suggested-reply chips staged by the model (Batch 9); [] when none.
        # Surfaced here so boot, project load, undo/redo, and the failed-turn
        # refresh all sync the bar one way — a failed turn's refresh returns
        # the untouched pre-turn list, restoring the bar for free.
        "suggested_prompts": list(session.suggested_prompts),
        # Import honesty/recovery metadata. Native .baspec packages carry the
        # source as a separate binary member; legacy JSON remains source-less.
        "import_report": session.import_report,
        "source_available": session.source_docx_bytes is not None,
        "preservation_ready": bool(preservation and preservation.ready),
        "source_preservation": _source_preservation_payload(
            session, preservation
        ),
    }


def _readiness_payload(session) -> dict[str, Any]:
    """The deterministic issue-readiness checklist.

    Non-advisory checks gate ``ready`` (the "can it go out the door" bar,
    per the batch acceptance criteria): no open items, no unreviewed
    imported/assumed blocks, lint clean, research complete, and a current QC
    with no open criticals. ``profile_complete`` is shown but advisory —
    ``research_complete`` already subsumes it.
    """
    doc = session.doc.doc
    open_items = open_questions(doc)
    imported = 0
    assumed = 0
    for _part, _article, p, _depth, _ref in iter_paragraphs(doc):
        if p.status == "imported":
            imported += 1
        elif p.status == "assumed":
            assumed += 1
    lint_items = lint_document(doc, session.module)
    profile = ProjectProfile.from_dict(doc.project_profile)
    profile_ok = bool(profile and profile.is_complete())
    research_ok = session.research.status == "complete"

    qc_result = session.qc.result
    qc_current = (
        qc_result is not None
        and qc_result.version_index == session.doc.index
        and qc_result.open_critical_count() == 0
    )
    if qc_result is None:
        qc_detail = "Final QC has not been run."
    elif qc_result.version_index != session.doc.index:
        qc_detail = "Final QC is stale — the document has changed since it ran."
    elif qc_result.open_critical_count() > 0:
        qc_detail = (
            f"{qc_result.open_critical_count()} open critical finding(s) — "
            "resolve or dismiss them."
        )
    else:
        qc_detail = "Final QC is current with no open criticals."

    checks = [
        {
            "id": "no_open_items",
            "ok": len(open_items) == 0,
            "detail": "No open items."
            if not open_items
            else f"{len(open_items)} open item(s) ([TBD]/needs-input).",
            "advisory": False,
        },
        {
            "id": "no_imported_left",
            "ok": imported == 0,
            "detail": "No unreviewed imported blocks."
            if imported == 0
            else f"{imported} imported block(s) not yet reviewed.",
            "advisory": False,
        },
        {
            "id": "no_assumed_left",
            "ok": assumed == 0,
            "detail": "No unreviewed assumed blocks."
            if assumed == 0
            else f"{assumed} assumed block(s) awaiting review.",
            "advisory": False,
        },
        {
            "id": "lint_clean",
            "ok": len(lint_items) == 0,
            "detail": "Lint clean."
            if not lint_items
            else f"{len(lint_items)} advisory lint issue(s).",
            "advisory": False,
        },
        {
            "id": "profile_complete",
            "ok": profile_ok,
            "detail": "Project profile complete."
            if profile_ok
            else "Project profile is incomplete.",
            "advisory": True,
        },
        {
            "id": "research_complete",
            "ok": research_ok,
            "detail": "Requirements research complete."
            if research_ok
            else f"Research status: {session.research.status}.",
            "advisory": False,
        },
        {
            "id": "qc_current",
            "ok": qc_current,
            "detail": qc_detail,
            "advisory": False,
        },
    ]
    ready = all(c["ok"] for c in checks if not c["advisory"])
    return {"checks": checks, "ready": ready}


def create_app() -> FastAPI:
    app = FastAPI(title=settings.APP_NAME, version=settings.VERSION)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_DEV_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health() -> dict:
        session = sessions.get_session()
        return {
            "status": "ok",
            "app": settings.APP_NAME,
            "version": settings.VERSION,
            "model": settings.INTERVIEW_MODEL,
            "api_key_present": bool(load_api_key()),
            "module": session.module.display_name,
            "module_id": session.module.module_id,
            "discipline": session.discipline,
            "project_context": session.project_context,
        }

    @app.post("/api/key")
    def save_key(body: SaveKeyRequest) -> JSONResponse:
        try:
            stored_in = save_api_key(body.api_key)
        except ValueError:
            return JSONResponse(
                {"ok": False, "error": "API key is empty."}, status_code=400
            )
        except OSError as exc:
            return JSONResponse(
                {"ok": False, "error": f"Could not store the key: {exc}"},
                status_code=500,
            )
        reset_client_cache()
        return JSONResponse({"ok": True, "stored_in": stored_in})

    @app.get("/api/key/status")
    def key_status_endpoint() -> dict:
        """Where the key resolves from + a masked tail (never the key)."""
        status = key_status()
        if status.get("source") == "env":
            status["env_locked"] = True
        return status

    @app.delete("/api/key")
    def delete_key() -> JSONResponse:
        """Remove the stored key (keyring + files) and drop the client cache.

        The env var cannot be cleared from here; the fresh status shows
        whether a key still resolves (e.g. an env var still set).
        """
        cleared = delete_api_key()
        reset_client_cache()
        return JSONResponse({"ok": True, "cleared": cleared, **key_status()})

    @app.post("/api/key/test")
    def test_key(body: TestKeyRequest) -> JSONResponse:
        """Validate a candidate (or the stored) key with one cheap call.

        Never stores anything as a side effect — the frontend tests, then
        saves separately on success.
        """
        candidate = (body.api_key or "").strip() or load_api_key()
        if not candidate:
            return JSONResponse(
                {"ok": False, "error": "No API key to test."}
            )
        try:
            probe = build_probe_client(candidate)
            probe.models.list(limit=1)
        except MissingApiKeyError as exc:
            return JSONResponse({"ok": False, "error": str(exc)})
        except anthropic.APIStatusError as exc:
            return JSONResponse({"ok": False, "error": exc.message})
        except anthropic.APIConnectionError:
            return JSONResponse(
                {
                    "ok": False,
                    "error": "Could not reach the Anthropic API — check "
                    "your connection.",
                }
            )
        except Exception as exc:  # noqa: BLE001 — surfaced to the user
            return JSONResponse({"ok": False, "error": str(exc)})
        return JSONResponse({"ok": True})

    @app.post("/api/session/reset")
    def reset(body: SessionResetRequest | None = Body(default=None)) -> dict:
        session = sessions.get_session()
        session.reset()
        if body is not None:
            if body.module_id.strip():
                session.module = get_module(body.module_id)
            # Invariant: discipline is non-empty only while an open-catalog
            # module is active. A curated module always clears it.
            if getattr(session.module, "open_catalog", False):
                session.discipline = sanitize_discipline(body.discipline)
            else:
                session.discipline = ""
            # Priming text applies to any module (not gated by open_catalog);
            # reset() already cleared it, so a bodyless reset leaves it "".
            session.project_context = sanitize_project_context(
                body.project_context
            )
        return {
            "ok": True,
            "module_id": session.module.module_id,
            "module": session.module.display_name,
            "discipline": session.discipline,
            "project_context": session.project_context,
        }

    @app.get("/api/session/unsaved")
    def session_unsaved() -> dict:
        """Whether the session holds work worth saving before it is discarded.

        The in-app New-session / Open-project save gate calls this so it uses
        the SAME predicate as the native window-close prompt
        (``main._CloseController``) — one source of truth for "is there
        anything to lose here?".
        """
        return {
            "ok": True,
            "unsaved": sessions.has_unsaved_progress(sessions.get_session()),
        }

    @app.get("/api/modules")
    def modules() -> dict:
        """The selectable module registry, for the session-start picker."""
        return {
            "ok": True,
            "modules": [
                {
                    "module_id": module.module_id,
                    "display_name": module.display_name,
                    "description": module.description,
                    "generic": module.open_catalog,
                    "default": module.module_id == DEFAULT_MODULE.module_id,
                }
                for module in AVAILABLE_MODULES.values()
            ],
        }

    @app.post("/api/chat")
    def chat(body: ChatRequest) -> StreamingResponse:
        session = sessions.get_session()

        def event_stream() -> Iterator[str]:
            for event in stream_user_turn(session, body.message):
                yield _sse(event)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/chat/stop")
    def chat_stop() -> JSONResponse:
        """Stop the in-flight turn (Claude.ai-style stop button).

        Not a rollback: whatever text/edits the turn produced before this
        call lands normally through the SAME turn's ``turn_complete`` — the
        streaming response just ends sooner. A 409 means no turn is
        streaming (it likely just finished on its own); safe to ignore.
        """
        session = sessions.get_session()
        if not session.turn_active:
            return JSONResponse(
                {"ok": False, "error": "No turn is streaming."},
                status_code=409,
            )
        session.stop_requested.set()
        return JSONResponse({"ok": True})

    @app.post("/api/draft/full")
    def draft_full() -> JSONResponse:
        """Hand the frontend the canned full-section draft directive (WI1).

        Deliberately thin: it owns no drafting machinery of its own. The
        directive is an ordinary user message the frontend sends back through
        ``/api/chat``, so the pass rides the existing SSE stream, tool loop,
        status strip, one-undo-step commit, and rollback — one code path for
        turns, no duplicated pipeline. Refused (409) while a model turn is
        streaming or research is running, mirroring the manual-edit guard: a
        drafting turn launched into either would collide with in-flight work.
        """
        session = sessions.get_session()
        if session.turn_active:
            return JSONResponse(
                {
                    "ok": False,
                    "error": "A model turn is already streaming — wait for it "
                    "to finish before drafting the full section.",
                },
                status_code=409,
            )
        if session.research.status == "running":
            return JSONResponse(
                {
                    "ok": False,
                    "error": "Requirements research is running — let it finish "
                    "so the draft can use the grounded results.",
                },
                status_code=409,
            )
        return JSONResponse({"ok": True, "message": FULL_DRAFT_DIRECTIVE})

    @app.post("/api/onboarding/demo")
    def onboarding_demo(body: OnboardingDemoRequest) -> JSONResponse:
        """Hand the frontend the guided-tour demo directive (Batch 6).

        Thin like ``/api/draft/full``: the returned message goes back
        through ``/api/chat`` as an ordinary, visible user turn, so the
        demo rides the one streaming path. The extra guard is the blank
        document — the tour drafts its demo onto a clean page only; the
        frontend offers "start fresh" first, and this 409 backstops it.
        """
        session = sessions.get_session()
        if session.turn_active:
            return JSONResponse(
                {
                    "ok": False,
                    "error": "A model turn is already streaming — wait for "
                    "it to finish before starting the demo.",
                },
                status_code=409,
            )
        if session.research.status == "running":
            return JSONResponse(
                {
                    "ok": False,
                    "error": "Requirements research is running — let it "
                    "finish before starting the demo.",
                },
                status_code=409,
            )
        if not session.doc.doc.is_empty():
            return JSONResponse(
                {
                    "ok": False,
                    "error": "The guided tour drafts its demo into a blank "
                    "session — start a New session first (the tour offers "
                    "this).",
                },
                status_code=409,
            )
        # On an open-catalog session, align the session discipline with the
        # demo's chosen discipline (honoring the invariant — a curated module
        # stays ""). Otherwise the demo directive would draft discipline B
        # while the PROJECT CONTEXT still names an earlier discipline A.
        if getattr(session.module, "open_catalog", False):
            session.discipline = sanitize_discipline(body.discipline)
        return JSONResponse(
            {"ok": True, "message": onboarding_demo_directive(body.discipline)}
        )

    # --- Document ----------------------------------------------------------

    @app.get("/api/doc")
    def get_doc() -> dict:
        return _doc_payload(sessions.get_session())

    @app.post("/api/doc/undo")
    def undo_doc() -> JSONResponse:
        session = sessions.get_session()
        if session.turn_active:
            return JSONResponse(
                {
                    "ok": False,
                    "error": "A model turn is streaming — try undo again "
                    "once it finishes.",
                },
                status_code=409,
            )
        if not session.doc.undo():
            return JSONResponse(
                {"ok": False, "error": "Nothing to undo."}, status_code=409
            )
        return JSONResponse({"ok": True, **_doc_payload(session)})

    @app.post("/api/doc/redo")
    def redo_doc() -> JSONResponse:
        session = sessions.get_session()
        if session.turn_active:
            return JSONResponse(
                {
                    "ok": False,
                    "error": "A model turn is streaming — try redo again "
                    "once it finishes.",
                },
                status_code=409,
            )
        if not session.doc.redo():
            return JSONResponse(
                {"ok": False, "error": "Nothing to redo."}, status_code=409
            )
        return JSONResponse({"ok": True, **_doc_payload(session)})

    @app.post("/api/doc/edit")
    def edit_doc(body: EditDocRequest) -> JSONResponse:
        """Apply a manual (user-authored) edit batch as one undoable version.

        Same op vocabulary as the model's ``apply_spec_edits`` tool; thanks
        to the v0.6.0 context architecture the model sees the result in its
        next turn's PROJECT CONTEXT with no history surgery. Rejected while a
        model turn streams (409) — a mid-turn manual edit would be swept into
        that turn's commit/rollback.
        """
        session = sessions.get_session()
        if session.turn_active:
            return JSONResponse(
                {
                    "ok": False,
                    "error": "A model turn is streaming — try the edit again "
                    "once it finishes.",
                },
                status_code=409,
            )
        generation = session.generation
        session.doc.begin_turn()
        try:
            applied = session.apply_doc_edits(body.ops)
        except SpecEditError as exc:
            session.doc.rollback_turn()
            return JSONResponse(
                {"ok": False, "error": str(exc)}, status_code=400
            )
        if session.generation != generation:
            # Reset/load raced in between begin and commit: discard the edit
            # so the fresh/loaded session stays exactly as the user made it.
            session.doc.rollback_turn()
            return JSONResponse(
                {
                    "ok": False,
                    "error": "The session changed while the edit was "
                    "applying; the edit was discarded.",
                },
                status_code=409,
            )
        session.doc.commit_turn()
        return JSONResponse({"ok": True, "applied": applied, **_doc_payload(session)})

    def _redline_for_export(
        store, redline: str | None, base: int | None
    ) -> tuple[Any | None, JSONResponse | None]:
        """Resolve the ``?redline=`` export mode into a SectionDiff (or 400).

        ``master`` diffs the current doc against the imported baseline;
        ``version`` against ``versions[base]``. Returns ``(diff, None)`` on
        success or ``(None, error_response)`` on a bad request.
        """
        if redline is None:
            return None, None
        if redline == "master":
            if store.baseline_index is None:
                return None, JSONResponse(
                    {
                        "ok": False,
                        "error": "This project has no imported master — "
                        "choose a version to compare against.",
                    },
                    status_code=400,
                )
            base_index = store.baseline_index
        elif redline == "version":
            if base is None or not (0 <= base < len(store.versions)):
                return None, JSONResponse(
                    {"ok": False, "error": "Provide a valid 'base' version index."},
                    status_code=400,
                )
            base_index = base
        else:
            return None, JSONResponse(
                {"ok": False, "error": "redline must be 'master' or 'version'."},
                status_code=400,
            )
        base_section = SpecSection.from_dict(store.versions[base_index])
        return diff_sections(base_section, store.doc), None

    @app.get("/api/export/docx")
    def export_docx(
        redline: str | None = None,
        base: int | None = None,
        mode: str | None = None,
    ) -> Response:
        session = sessions.get_session()
        store = session.doc
        if mode not in (None, "source", "normalized"):
            return JSONResponse(
                {
                    "ok": False,
                    "error": "mode must be 'source' or 'normalized'.",
                },
                status_code=400,
            )
        if redline is not None and mode == "source":
            return JSONResponse(
                {
                    "ok": False,
                    "error": "Source-preserving export and semantic redline "
                    "export are separate modes.",
                },
                status_code=400,
            )
        redline_diff, error = _redline_for_export(store, redline, base)
        if error is not None:
            return error

        # Redlines are always generated from the semantic tree. Otherwise an
        # imported project defaults to the preservation path and never
        # silently falls back to a normalized reconstruction.
        imported_scope = (
            session.import_report is not None or store.baseline_index is not None
        )
        selected_mode = (
            "normalized"
            if redline_diff is not None
            else (mode or ("source" if imported_scope else "normalized"))
        )
        if selected_mode == "source":
            baseline = _source_baseline(session)
            source_map = getattr(session, "source_docx_map", None)
            if (
                baseline is None
                or session.source_docx_bytes is None
                or source_map is None
            ):
                return JSONResponse(
                    {
                        "ok": False,
                        "error": "Source-preserving export is unavailable: "
                        "this project does not contain a validated source "
                        "DOCX, source map, and imported baseline. Choose "
                        "normalized export explicitly if that is intended.",
                    },
                    status_code=409,
                )
            try:
                payload = build_source_preserving_docx(
                    source_bytes=session.source_docx_bytes,
                    source_map=source_map,
                    baseline=baseline,
                    current=store.doc,
                )
            except SourcePatchError as exc:
                return JSONResponse(
                    {"ok": False, "error": str(exc)}, status_code=409
                )
            return Response(
                content=payload,
                media_type=(
                    "application/vnd.openxmlformats-officedocument."
                    "wordprocessingml.document"
                ),
                headers=_attachment_headers(export_filename(store.doc)),
            )

        qc_result = session.qc.result.to_dict() if session.qc.result else None
        payload = build_docx(
            store.doc,
            audit_result=session.audit.result,
            qc_result=qc_result,
            redline=redline_diff,
        )
        filename = (
            redline_filename(store.doc)
            if redline_diff is not None
            else export_filename(store.doc)
        )
        return Response(
            content=payload,
            media_type=(
                "application/vnd.openxmlformats-officedocument"
                ".wordprocessingml.document"
            ),
            headers=_attachment_headers(filename),
        )

    @app.get("/api/doc/diff")
    def doc_diff(base: int, cur: int | None = None) -> JSONResponse:
        """Serialized SectionDiff between two versions (in-app compare view).

        ``cur`` defaults to the current version index. Indices must be in
        range and distinct.
        """
        store = sessions.get_session().doc
        cur_index = store.index if cur is None else cur
        n = len(store.versions)
        if not (0 <= base < n) or not (0 <= cur_index < n):
            return JSONResponse(
                {"ok": False, "error": "Version index out of range."},
                status_code=400,
            )
        if base == cur_index:
            return JSONResponse(
                {
                    "ok": False,
                    "error": "Choose two different versions to compare.",
                },
                status_code=400,
            )
        base_section = SpecSection.from_dict(store.versions[base])
        cur_section = SpecSection.from_dict(store.versions[cur_index])
        diff = diff_sections(base_section, cur_section)
        return JSONResponse(
            {
                "ok": True,
                **diff.to_dict(),
                "base_index": base,
                "cur_index": cur_index,
                "baseline_index": store.baseline_index,
            }
        )

    # --- Chat-authored figures (diagrams / schematics / tables) -------------
    #
    # Figures are created by the model through the create_figure tool and ride
    # the SSE ``figure`` event + every _doc_payload; these routes cover a
    # standalone snapshot, the CSV download for table figures, and delete.
    # Diagram (SVG/PNG) downloads are produced client-side from the sanitized
    # source (the server never serves executable SVG) — see
    # ``frontend/src/lib/figures.ts``.

    @app.get("/api/figures")
    def figures_list() -> JSONResponse:
        session = sessions.get_session()
        return JSONResponse({"ok": True, "figures": session.figures.snapshot()})

    @app.get("/api/figure/{fid}/csv")
    def figure_csv(fid: str) -> Response:
        session = sessions.get_session()
        figure = session.figures.get(fid)
        if figure is None:
            return JSONResponse(
                {"ok": False, "error": f"No figure {fid!r}."}, status_code=404
            )
        if figure.kind != "table":
            return JSONResponse(
                {"ok": False, "error": "Only table figures export as CSV."},
                status_code=400,
            )
        return Response(
            content=figure.to_csv(),
            media_type="text/csv; charset=utf-8",
            headers=_attachment_headers(f"{figure.title or figure.fid}.csv"),
        )

    @app.delete("/api/figure/{fid}")
    def figure_delete(fid: str) -> JSONResponse:
        session = sessions.get_session()
        if session.turn_active:
            # Deleting mid-turn would shift the list under the turn's
            # provisional-figure bookkeeping (begin/rollback by index).
            return JSONResponse(
                {
                    "ok": False,
                    "error": "A turn is generating — try again in a moment.",
                },
                status_code=409,
            )
        if not session.figures.delete(fid):
            return JSONResponse(
                {"ok": False, "error": f"No figure {fid!r}."}, status_code=404
            )
        return JSONResponse({"ok": True, "figures": session.figures.snapshot()})

    # --- Master-spec import (Phase 5) ---------------------------------------

    @app.post("/api/import/master")
    async def import_master(file: UploadFile) -> JSONResponse:
        session = sessions.get_session()
        if not session.doc.doc.is_empty():
            return JSONResponse(
                {
                    "ok": False,
                    "error": "The document is not empty — a master import "
                    "is a starting point. Start a new session first.",
                },
                status_code=409,
            )
        submitted_name = (
            (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
        )
        if not submitted_name.lower().endswith(".docx"):
            return JSONResponse(
                {"ok": False, "error": "Upload a .docx master specification."},
                status_code=400,
            )
        safe_filename = sanitize_source_filename(submitted_name)
        try:
            source_bytes = await read_upload_bounded(file)
            package_info = inspect_docx_package(source_bytes)
        except UploadTooLargeError as exc:
            return JSONResponse(
                {"ok": False, "error": str(exc)}, status_code=413
            )
        except SourcePackageError as exc:
            return JSONResponse(
                {"ok": False, "error": str(exc)}, status_code=400
            )
        with tempfile.NamedTemporaryFile(
            suffix=".docx", delete=False
        ) as handle:
            handle.write(source_bytes)
            temp_path = Path(handle.name)
        try:
            result = parse_master_docx(temp_path)
            report = build_import_report(
                filename=safe_filename,
                source_bytes=source_bytes,
                package_info=package_info,
                imported_block_count=result.imported_block_count,
                skipped_empty_count=result.skipped_empty_count,
                warnings=result.warnings,
                tracked_changes_detected=result.tracked_changes_detected,
            )
            if result.source_map is None:
                raise MasterImportError(
                    "The source document could not be mapped safely for "
                    "preserving export."
                )
            session.doc.adopt_imported(result.section)
        except (MasterImportError, ValueError) as exc:
            return JSONResponse(
                {"ok": False, "error": str(exc)}, status_code=400
            )
        finally:
            temp_path.unlink(missing_ok=True)
        # Adopt the recovery artifact only after validation, parsing, and the
        # document-store transaction all succeed. Failed imports leave the
        # active session untouched.
        session.source_docx_bytes = source_bytes
        session.source_docx_filename = safe_filename
        session.source_docx_map = result.source_map
        session.import_report = report
        # The import counts as session-changing work: invalidate any turn
        # that was streaming against the empty document.
        session.generation += 1
        from .tracing import capture as _trace_capture

        _trace_capture.import_event(
            blocks=result.imported_block_count,
            warnings=len(result.warnings),
            tracked_changes=result.tracked_changes_detected,
        )
        return JSONResponse(
            {
                "ok": True,
                "warnings": report["warnings"],
                "imported_block_count": report["imported_block_count"],
                "skipped_empty_count": report["skipped_empty_count"],
                "tracked_changes_detected": report[
                    "tracked_changes_detected"
                ],
                **_doc_payload(session),
            }
        )

    @app.get("/api/import/original")
    def import_original() -> Response:
        """Download the exact validated upload while this session retains it."""
        session = sessions.get_session()
        if session.source_docx_bytes is None:
            if session.import_report is not None:
                return JSONResponse(
                    {
                        "ok": False,
                        "error": "The original master is not available in this "
                        "resumed legacy project. Legacy JSON files retain the "
                        "import report, but not source DOCX bytes.",
                    },
                    status_code=409,
                )
            return JSONResponse(
                {
                    "ok": False,
                    "error": "No original master is available in this session.",
                },
                status_code=404,
            )
        filename = session.source_docx_filename or "imported-master.docx"
        return Response(
            content=session.source_docx_bytes,
            media_type=(
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
            headers={
                **_attachment_headers(filename),
                # Do not let a browser/proxy retain a project source outside
                # the application's own bounded project package.
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    # --- Requirements research (Phase 4) ------------------------------------

    @app.post("/api/research/start")
    def research_start() -> JSONResponse:
        session = sessions.get_session()
        profile = ProjectProfile.from_dict(session.doc.doc.project_profile)
        if profile is None or not profile.is_complete():
            return JSONResponse(
                {
                    "ok": False,
                    "error": "The project profile is incomplete — the "
                    "interview needs city, state, country, and client "
                    "before research can run.",
                },
                status_code=400,
            )
        if not session.module.research_dimensions:
            return JSONResponse(
                {
                    "ok": False,
                    "error": "The active module defines no research "
                    "dimensions.",
                },
                status_code=400,
            )
        # Batch 10 backstop: an open-catalog session researches "{discipline}
        # work" — without a stated discipline the templates have nothing to
        # research. The session-start picker normally guarantees this.
        if getattr(session.module, "open_catalog", False) and not (
            session.discipline
        ):
            return JSONResponse(
                {
                    "ok": False,
                    "error": "State the discipline first — the generic "
                    "module needs it before research can run.",
                },
                status_code=400,
            )
        try:
            client = get_client()
        except MissingApiKeyError as exc:
            return JSONResponse(
                {"ok": False, "error": str(exc)}, status_code=400
            )
        started = session.research.start(
            module=session.module,
            project_profile=profile,
            client=client,
            model=settings.RESEARCH_MODEL,
            max_tokens=settings.RESEARCH_MAX_TOKENS,
            discipline=session.discipline,
            usage_sink=lambda u: session.usage.add("research", u),
        )
        if not started:
            return JSONResponse(
                {"ok": False, "error": "Research is already running."},
                status_code=409,
            )
        return JSONResponse({"ok": True})

    @app.get("/api/research/status")
    def research_status() -> dict:
        return sessions.get_session().research.snapshot()

    @app.post("/api/research/stop")
    def research_stop() -> JSONResponse:
        """Stop the running research fan-out. Discards whatever it found.

        Resolves immediately as a failed run (the UI never waits on the
        background thread to notice); a 409 means nothing is running.
        """
        if not sessions.get_session().research.stop():
            return JSONResponse(
                {"ok": False, "error": "Research is not running."},
                status_code=409,
            )
        return JSONResponse({"ok": True})

    @app.get("/api/research/stream")
    def research_stream() -> StreamingResponse:
        runner = sessions.get_session().research

        def event_stream() -> Iterator[str]:
            for event in runner.sse_events():
                yield _sse(event)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    # --- Compliance audit (Phase 5) -----------------------------------------

    @app.post("/api/audit/start")
    def audit_start() -> JSONResponse:
        session = sessions.get_session()
        profile = session.research.profile_result
        if profile is None:
            return JSONResponse(
                {
                    "ok": False,
                    "error": "Run the requirements research first — the "
                    "audit evaluates the draft against the researched "
                    "profile.",
                },
                status_code=400,
            )
        if session.doc.doc.is_empty():
            return JSONResponse(
                {"ok": False, "error": "There is no draft to audit yet."},
                status_code=400,
            )
        try:
            client = get_client()
        except MissingApiKeyError as exc:
            return JSONResponse(
                {"ok": False, "error": str(exc)}, status_code=400
            )
        # Audit a snapshot: a turn streaming mid-audit must not mutate the
        # tree under the call.
        from .spec_doc.model import SpecSection

        snapshot = SpecSection.from_dict(session.doc.doc.to_dict())
        started = session.audit.start(
            section=snapshot,
            profile=profile,
            module=session.module,
            client=client,
            model=settings.RESEARCH_MODEL,
            max_tokens=settings.RESEARCH_MAX_TOKENS,
            version_index=session.doc.index,
            discipline=session.discipline,
            usage_sink=lambda u: session.usage.add("audit", u),
        )
        if not started:
            return JSONResponse(
                {"ok": False, "error": "An audit is already running."},
                status_code=409,
            )
        return JSONResponse({"ok": True})

    @app.get("/api/audit/status")
    def audit_status() -> dict:
        return sessions.get_session().audit.snapshot()

    # --- Final QC on Fable 5 (Batch 4) --------------------------------------

    @app.post("/api/qc/start")
    def qc_start() -> JSONResponse:
        """Launch the spare-no-expense Final-QC pass on Fable 5.

        Research is NOT required — when absent, the completeness lens adapts
        and the result is flagged ``research_profile_present: false``. Gates:
        non-empty draft, an API key, no QC already running, and no model turn
        streaming (a QC of a mid-turn tree would review a moving target).
        """
        session = sessions.get_session()
        if session.turn_active:
            return JSONResponse(
                {
                    "ok": False,
                    "error": "A model turn is streaming — let it finish before "
                    "running Final QC.",
                },
                status_code=409,
            )
        if session.doc.doc.is_empty():
            return JSONResponse(
                {"ok": False, "error": "There is no draft to review yet."},
                status_code=400,
            )
        try:
            client = get_client()
        except MissingApiKeyError as exc:
            return JSONResponse(
                {"ok": False, "error": str(exc)}, status_code=400
            )
        # Snapshot the tree so a turn streaming mid-QC can't mutate it under
        # the call; capture dismiss memory before start() clears the result.
        snapshot = SpecSection.from_dict(session.doc.doc.to_dict())
        source_guard = _qc_source_guard(session)
        remembered = session.qc.remembered_dismissed()
        started = session.qc.start(
            section=snapshot,
            profile=session.research.profile_result,
            module=session.module,
            client=client,
            model=settings.QC_MODEL,
            max_tokens=settings.QC_MAX_TOKENS,
            version_index=session.doc.index,
            discipline=session.discipline,
            source_guard=source_guard,
            remembered_dismissed=remembered,
            usage_sink=lambda u: session.usage.add("qc", u),
        )
        if not started:
            return JSONResponse(
                {"ok": False, "error": "Final QC is already running."},
                status_code=409,
            )
        return JSONResponse({"ok": True})

    @app.get("/api/qc/status")
    def qc_status() -> dict:
        return sessions.get_session().qc.snapshot()

    @app.post("/api/qc/stop")
    def qc_stop() -> JSONResponse:
        """Stop the running Final QC pass. Discards whatever it found.

        Resolves immediately as a failed run (the UI never waits on the
        background thread to notice); a 409 means nothing is running.
        """
        if not sessions.get_session().qc.stop():
            return JSONResponse(
                {"ok": False, "error": "Final QC is not running."},
                status_code=409,
            )
        return JSONResponse({"ok": True})

    @app.get("/api/qc/stream")
    def qc_stream() -> StreamingResponse:
        runner = sessions.get_session().qc

        def event_stream() -> Iterator[str]:
            for event in runner.sse_events():
                yield _sse(event)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/qc/apply")
    def qc_apply(body: QcApplyRequest) -> JSONResponse:
        """Apply accepted findings' validated ops as ONE undoable version.

        Re-dry-runs each finding against the CURRENT doc first (it may have
        moved since QC ran): a finding whose ops no longer apply is reported
        ``stale`` and skipped, never partially applied. Rejected (409) while a
        model turn streams.
        """
        session = sessions.get_session()
        if session.turn_active:
            return JSONResponse(
                {
                    "ok": False,
                    "error": "A model turn is streaming — apply the fix once it "
                    "finishes.",
                },
                status_code=409,
            )
        result = session.qc.result
        if result is None:
            return JSONResponse(
                {"ok": False, "error": "No QC result to apply from."},
                status_code=409,
            )
        # Validate each finding onto an ACCUMULATING working copy so the
        # combined batch is guaranteed to replay cleanly on the live tree.
        working = SpecSection.from_dict(session.doc.doc.to_dict())
        combined_ops: list[dict[str, Any]] = []
        applied_ids: list[str] = []
        outcomes: dict[str, str] = {}
        for finding_id in body.finding_ids:
            finding = result.finding(finding_id)
            if finding is None:
                outcomes[finding_id] = "unknown"
                continue
            if not finding.ops_valid or not finding.proposed_ops:
                outcomes[finding_id] = "no_ops"
                continue
            if finding.status == "applied":
                outcomes[finding_id] = "already_applied"
                continue
            try:
                working, _applied = apply_edits(working, finding.proposed_ops)
            except SpecEditError:
                outcomes[finding_id] = "stale"
                continue
            combined_ops.extend(finding.proposed_ops)
            applied_ids.append(finding_id)
            outcomes[finding_id] = "applied"

        if combined_ops:
            generation = session.generation
            session.doc.begin_turn()
            try:
                session.apply_doc_edits(combined_ops)
            except SpecEditError as exc:  # pragma: no cover — validated above
                session.doc.rollback_turn()
                return JSONResponse(
                    {"ok": False, "error": str(exc)}, status_code=400
                )
            if session.generation != generation:
                session.doc.rollback_turn()
                return JSONResponse(
                    {
                        "ok": False,
                        "error": "The session changed while applying; nothing "
                        "was applied.",
                    },
                    status_code=409,
                )
            session.doc.commit_turn()
            session.qc.mark_applied(applied_ids)

        return JSONResponse(
            {"ok": True, "outcomes": outcomes, **_doc_payload(session)}
        )

    @app.post("/api/qc/dismiss")
    def qc_dismiss(body: QcDismissRequest) -> JSONResponse:
        session = sessions.get_session()
        if not session.qc.dismiss(body.finding_id, body.reason or ""):
            return JSONResponse(
                {"ok": False, "error": "No such finding to dismiss."},
                status_code=404,
            )
        return JSONResponse({"ok": True, "qc": session.qc.snapshot()})

    @app.get("/api/qc/export")
    def qc_export() -> Response:
        session = sessions.get_session()
        if session.qc.result is None:
            return JSONResponse(
                {"ok": False, "error": "Run Final QC first."}, status_code=409
            )
        stale = session.qc.result.version_index != session.doc.index
        payload = build_qc_memo(
            session.qc.result.to_dict(), session.doc.doc, stale=stale
        )
        stem = session.doc.doc.number.replace(" ", "") or "draft"
        return Response(
            content=payload,
            media_type=(
                "application/vnd.openxmlformats-officedocument"
                ".wordprocessingml.document"
            ),
            headers=_attachment_headers(f"QC MEMO {stem}.docx"),
        )

    # --- Readiness gate (deterministic; no model call) ----------------------

    @app.get("/api/readiness")
    def readiness() -> dict:
        """The "can it go out the door" checklist — pure functions of state."""
        return _readiness_payload(sessions.get_session())

    # --- Usage & cost meter (WI4) -------------------------------------------

    @app.get("/api/usage")
    def usage() -> dict:
        """This session's billed usage + an estimated cost from list pricing.

        Session-scoped: reset and project load clear it. The dollar figures
        are estimates (labeled as such in the UI); the trace files remain the
        permanent, exact record.
        """
        return sessions.get_session().usage.snapshot()

    # --- Project save / resume --------------------------------------------

    @app.get("/api/project/save")
    def project_save() -> Response:
        session = sessions.get_session()
        try:
            payload = sessions.project_package_bytes(session)
        except ProjectPackageError as exc:
            return JSONResponse(
                {"ok": False, "error": str(exc)}, status_code=409
            )
        filename = sessions.project_default_filename(session)
        return Response(
            content=payload,
            media_type=PACKAGE_MEDIA_TYPE,
            headers=_attachment_headers(filename),
        )

    @app.post("/api/project/load")
    def project_load(body: dict[str, Any]) -> JSONResponse:
        """Legacy format-1 JSON load (source-less compatibility endpoint)."""
        session = sessions.get_session()
        try:
            load_project(body, session)
            # JSON has no binary source member. Never retain or claim a map
            # that cannot be checked against exact source bytes.
            session.source_docx_map = None
        except ValueError as exc:
            return JSONResponse(
                {"ok": False, "error": str(exc)}, status_code=400
            )
        return JSONResponse(
            {
                "ok": True,
                "chat": chat_transcript(session.history),
                **_doc_payload(session),
            }
        )

    @app.post("/api/project/load-file")
    async def project_load_file(file: UploadFile) -> JSONResponse:
        """Load a native .baspec package or a legacy JSON project upload.

        The complete outer package, semantic history, source DOCX, typed
        source map, and current preservation plan are validated against a
        throwaway session before the live session is touched.
        """
        try:
            payload = await read_project_upload_bounded(file)
            parsed = parse_project_file(payload)

            staged = SessionState()
            load_project(parsed.project, staged)
            typed_map: SourceBodyMap | None = None
            if parsed.source_docx_bytes is not None:
                if parsed.source_map is None:
                    raise ProjectPackageError(
                        "The project source DOCX has no preservation map."
                    )
                try:
                    stored_map = SourceBodyMap.from_dict(parsed.source_map)
                except ValueError as exc:
                    raise ProjectPackageError(str(exc)) from exc

                # Rebuild anchors from the attached source instead of
                # trusting serialized indices/hashes. The stored map is an
                # integrity record; the recomputed map is the authority used
                # by the live session.
                with tempfile.NamedTemporaryFile(
                    suffix=".docx", delete=False
                ) as handle:
                    handle.write(parsed.source_docx_bytes)
                    source_path = Path(handle.name)
                try:
                    reparsed = parse_master_docx(source_path)
                except MasterImportError as exc:
                    raise ProjectPackageError(
                        "The attached source DOCX cannot be re-imported safely."
                    ) from exc
                finally:
                    source_path.unlink(missing_ok=True)
                if reparsed.source_map is None:
                    raise ProjectPackageError(
                        "The attached source DOCX cannot be remapped safely."
                    )
                if stored_map.to_dict() != reparsed.source_map.to_dict():
                    raise ProjectPackageError(
                        "The project source map does not match a fresh parse "
                        "of the attached DOCX."
                    )
                typed_map = reparsed.source_map
                staged.source_docx_bytes = parsed.source_docx_bytes
                staged.source_docx_filename = parsed.source_docx_filename
                staged.source_docx_map = typed_map
                baseline = _source_baseline(staged)
                if baseline is None:
                    raise ProjectPackageError(
                        "The project source has no imported semantic baseline."
                    )
                # Every retained state on the source-backed side of history
                # must fit the preservation boundary. Checking only the
                # active index would let an unsafe forged redo/undo version
                # enter the session and become active later without another
                # package validation pass.
                baseline_index = staged.doc.baseline_index
                for version_index in range(
                    baseline_index, len(staged.doc.versions)
                ):
                    retained = SpecSection.from_dict(
                        staged.doc.versions[version_index]
                    )
                    preservation = source_patch_readiness(
                        source_bytes=staged.source_docx_bytes,
                        source_map=typed_map,
                        baseline=baseline,
                        current=retained,
                    )
                    if preservation is None or not preservation.ready:
                        detail = (
                            preservation.blockers[0].message
                            if preservation and preservation.blockers
                            else "the current body exceeds the preservation boundary"
                        )
                        raise ProjectPackageError(
                            "The project source cannot restore retained "
                            f"version {version_index} safely: {detail}"
                        )
            elif parsed.source_map is not None and not parsed.legacy_json:
                raise ProjectPackageError(
                    "The project contains a source map without its source DOCX."
                )
        except ProjectPackageTooLargeError as exc:
            return JSONResponse(
                {"ok": False, "error": str(exc)}, status_code=413
            )
        except (ProjectPackageError, ValueError) as exc:
            return JSONResponse(
                {"ok": False, "error": str(exc)}, status_code=400
            )

        # The same semantic payload was fully staged above, so these writes
        # are the commit point. A rejected package never reaches them.
        session = sessions.get_session()
        load_project(parsed.project, session)
        session.source_docx_bytes = parsed.source_docx_bytes
        session.source_docx_filename = (
            parsed.source_docx_filename if parsed.source_docx_bytes else ""
        )
        session.source_docx_map = typed_map
        return JSONResponse(
            {
                "ok": True,
                "chat": chat_transcript(session.history),
                **_doc_payload(session),
            }
        )

    # --- Trace viewer (Phase 5) ---------------------------------------------

    @app.get("/api/trace/viewer", include_in_schema=False)
    def trace_viewer() -> FileResponse:
        """The bundled HTML trace viewer (open, then load a run directory).

        Traces live under the app state dir (see
        ``tracing.default_trace_root``); the viewer reads spans.jsonl /
        events.jsonl from a chosen run folder.
        """
        viewer = (
            Path(__file__).resolve().parent
            / "tracing"
            / "viewer"
            / "trace_viewer.html"
        )
        return FileResponse(viewer, media_type="text/html")

    # --- Self-update (Phase 5) ----------------------------------------------

    @app.get("/api/update/check")
    def update_check(force: bool = False) -> dict:
        """Check for a newer release. Throttled unless ``force``.

        The throttle state also carries "skip this version"; a skipped
        version reports as up-to-date on auto-checks but still surfaces on
        a forced (user-clicked) check.
        """
        from datetime import datetime

        from . import updates

        state_path = updates.default_state_path()
        state = updates.load_state(state_path)
        if not force and not updates.should_auto_check(
            state, now=datetime.now()
        ):
            return {"status": "THROTTLED", "current": settings.VERSION}
        result = updates.check_for_update(settings.VERSION)
        updates.record_check(state, now=datetime.now())
        updates.save_state(state_path, state)
        payload: dict[str, Any] = {
            "status": result.status,
            "current": result.current,
            "releases_url": updates.releases_page_url(),
            "platform_supported": updates.installer_platform_supported(),
        }
        if result.error:
            payload["error"] = result.error
        if result.info is not None:
            skipped = updates.version_is_skipped(state, result.info.version)
            if result.update_available and skipped and not force:
                payload["status"] = updates.STATUS_UP_TO_DATE
            else:
                payload["version"] = result.info.version
                payload["notes"] = result.info.notes
        return payload

    @app.post("/api/update/install")
    def update_install() -> JSONResponse:
        """Download + SHA-256-verify the latest installer, then launch it.

        Returns only after the verified installer has been spawned; the
        frontend then tells the user the app will close for the update.
        """
        from . import updates

        if not updates.installer_platform_supported():
            return JSONResponse(
                {
                    "ok": False,
                    "error": "The installer is Windows-only; download "
                    "releases manually on this platform.",
                },
                status_code=400,
            )
        result = updates.check_for_update(settings.VERSION)
        if not result.update_available or result.info is None:
            return JSONResponse(
                {"ok": False, "error": "No update is available."},
                status_code=409,
            )
        try:
            installer = updates.download_installer(
                result.info, updates.default_download_dir()
            )
            updates.spawn_installer(installer)
        except updates.UpdateError as exc:
            return JSONResponse(
                {"ok": False, "error": str(exc)}, status_code=502
            )
        return JSONResponse({"ok": True, "version": result.info.version})

    # --- Static frontend (production / packaged) ---------------------------
    dist = settings.FRONTEND_DIST
    if dist.is_dir():
        app.mount(
            "/assets", StaticFiles(directory=dist / "assets"), name="assets"
        )

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(dist / "index.html")

    return app


app = create_app()
