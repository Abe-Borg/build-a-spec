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
- ``GET  /api/doc/capabilities`` → just the imported-source permission
  report, for polling while the background sweep derives it.
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
- ``POST /api/reference/upload`` → attach a ``.docx`` as background context
  for the model to read. Never touches the document tree, so unlike a master
  import it has no blank-document precondition; the bytes are inspected and
  discarded, only extracted text is kept.
- ``GET  /api/references``    → attached reference documents (metadata only).
- ``DELETE /api/reference/{rid}`` → detach one (404 when unknown).
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
- ``POST /api/qc/stop``        → stop the running Final QC pass; preserves the
  cancelled attempt identity and any partial audit record that settles (409
  if none is running).
- ``POST /api/qc/apply``       → apply accepted findings' fixes (one undo step).
- ``POST /api/qc/dismiss``     → dismiss a finding (remembered across re-runs).
- ``GET  /api/qc/export``      → the detailed QC report as ``.docx``.
- ``GET  /api/qc/export.json`` → the same auditable record as JSON.
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
from datetime import datetime, timezone
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
from starlette.concurrency import run_in_threadpool

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
)
from .project_profile import ProjectProfile
from .qc.engine import (
    QC_PROTOCOL_VERSION,
    QC_REPORT_SCHEMA_VERSION,
    QCSourceGuard,
    build_qc_input_manifest,
    qc_input_fingerprint,
    qc_version_fingerprint,
)
from .qc.op_conflicts import (
    canonical_qc_operation,
    plan_qc_operation_batch,
    qc_operation_identity,
)
from .qc.preflight import module_section_compatibility
from .spec_modules import AVAILABLE_MODULES, DEFAULT_MODULE
from .spec_doc import SpecEditError, diff_sections, lint_document, open_questions
from .spec_doc.docx_export import (
    build_docx,
    build_qc_memo,
    export_filename,
    redline_filename,
)
from .reference_docs import ReferenceDocError
from .reference_extract import (
    extract_reference_document,
    reference_kind_for_filename,
    sanitize_reference_filename,
    supported_extensions_phrase,
)
from .spec_doc.importer import (
    MasterImportError,
    ReferenceExtraction,
    parse_master_docx,
)
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
from .spec_doc import source_patch as source_patch_module
from .spec_doc.source_patch import (
    SourcePatchIssue,
    SourcePatchReadiness,
    SourcePatchError,
    build_source_preserving_docx,
    source_capability_summary,
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


class QcStartRequest(BaseModel):
    acknowledge_scope_mismatch: bool = False


class QcDismissRequest(BaseModel):
    finding_id: str
    reason: str


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


def _prepare_master_import(
    source_bytes: bytes, safe_filename: str
) -> tuple[Any, dict[str, Any], Any]:
    """Inspect, parse and index an uploaded master — the blocking half.

    Pure CPU work over the uploaded bytes: no session state is read or
    written, so it is safe to run on a worker thread while the event loop
    keeps serving the chat stream and every other request. Errors keep the
    exact types the endpoint maps to responses, and the temp file is always
    removed on the thread that created it.
    """
    package_info = inspect_docx_package(source_bytes)
    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as handle:
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
            spec_shape_detected=result.spec_shape_detected,
        )
        if result.source_map is None:
            raise MasterImportError(
                "The source document could not be mapped safely for "
                "preserving export."
            )
        source_context = source_patch_module.build_source_patch_context(
            source_bytes=source_bytes,
            source_map=result.source_map,
            baseline=result.section,
        )
    finally:
        temp_path.unlink(missing_ok=True)
    return result, report, source_context


def _prepare_reference_upload(
    source_bytes: bytes, *, filename: str
) -> "ReferenceExtraction":
    """Extract an uploaded reference file's text — the blocking half.

    Same event-loop rule as ``_prepare_master_import``: this is pure CPU over
    the uploaded bytes and must never run inline on the loop. It touches no
    session state, so it is safe on a worker thread. Which extractor runs, and
    the safety pass each type gets, is ``reference_extract``'s business; a
    ``.docx`` still goes through the same bounded ZIP/OPC inspection as a
    master because it is the same attack surface. Nothing is retained for any
    type: the text is extracted and the bytes are dropped.
    """
    return extract_reference_document(source_bytes, filename=filename)


def _stage_project_load(
    payload: bytes,
) -> tuple[Any, SessionState, SourceBodyMap | None, Any]:
    """Validate a project upload against a throwaway session — blocking half.

    Everything here runs against ``payload`` and a staged
    :class:`SessionState`; the live session is never touched, so this is safe
    on a worker thread. Re-parsing the attached master costs the same seconds
    of CPU as an import, which is exactly why it must not run on the event
    loop. Raises the same ``ProjectPackageError`` / ``ValueError`` types the
    endpoint maps to a 400, and ``ProjectPackageTooLargeError`` to a 413.
    """
    parsed = parse_project_file(payload)

    staged = SessionState()
    load_project(parsed.project, staged)
    typed_map: SourceBodyMap | None = None
    source_context = None
    if parsed.source_docx_bytes is not None:
        if parsed.source_map is None:
            raise ProjectPackageError(
                "The project source DOCX has no preservation map."
            )
        try:
            stored_map = SourceBodyMap.from_dict(parsed.source_map)
        except ValueError as exc:
            raise ProjectPackageError(str(exc)) from exc

        # Rebuild anchors from the attached source instead of trusting
        # serialized indices/hashes. The stored map is an integrity record;
        # the recomputed map is the authority used by the live session.
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as handle:
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
        source_context = parsed.source_patch_context
        if source_context is None:
            source_context = source_patch_module.build_source_patch_context(
                source_bytes=parsed.source_docx_bytes,
                source_map=typed_map,
                baseline=baseline,
            )
        staged.source_patch_context = source_context
        # Every retained state on the source-backed side of history must fit
        # the preservation boundary. Checking only the active index would let
        # an unsafe forged redo/undo version enter the session and become
        # active later without another package validation pass.
        baseline_index = staged.doc.baseline_index
        for version_index in range(baseline_index, len(staged.doc.versions)):
            retained = SpecSection.from_dict(staged.doc.versions[version_index])
            preservation = source_patch_readiness(
                source_bytes=staged.source_docx_bytes,
                source_map=typed_map,
                baseline=baseline,
                current=retained,
                context=source_context,
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
    return parsed, staged, typed_map, source_context


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
    context = None
    try:
        context = session.ensure_source_patch_context(baseline=baseline)
    except SourcePatchError as exc:
        return SourcePatchReadiness(
            False,
            False,
            blockers=(SourcePatchIssue(exc.uid, exc.blocker, exc.detail),),
        )
    return source_patch_readiness(
        source_bytes=session.source_docx_bytes,
        source_map=getattr(session, "source_docx_map", None),
        baseline=baseline,
        current=session.doc.doc,
        context=context,
    )


def _settle_source_capabilities(session) -> None:
    """Derive the imported-source permission report before taking the guard.

    The QC endpoints below reason from real permissions (``block=True``), and
    the sweep that derives them is minutes of work on a large master. Doing it
    inside ``session_state_guard()`` would hold ``_turn_state_lock`` for that
    whole time — the same lock ``claim_model_turn`` needs — so a chat turn
    could not even start. Settle first, then take the guard and re-check,
    exactly as the import handler does with its parse. A body change landing
    in that window just costs one more sweep behind the lock, which needs a
    manual edit inside the gap; an audit-grade result reasoning from real
    permissions is worth that.
    """
    session.source_edit_capabilities(block=True)


def _qc_source_guard(session, *, block: bool = False) -> QCSourceGuard | None:
    """Capture immutable source inputs for the exact QC document snapshot.

    Presence means the runner must enforce source preservation. An active
    source-backed session with malformed or missing context still returns a
    required, incomplete guard so proposal validation fails closed.

    ``block`` is set only when starting a run, which must reason from real
    permissions. The freshness comparisons that reach here from the hot
    ``/api/readiness`` and ``/api/qc/status`` polls leave it False: they must
    never wait on the sweep, and a not-yet-derived capability summary simply
    reads as "the recorded inputs no longer match", i.e. stale. That is the
    conservative answer, and after an import (which requires an empty
    document) or a body change (which makes a prior QC stale anyway) it is
    also the correct one.
    """
    # Use the same active-branch/source-less boundary as manual/model edits
    # and the capability payload. In particular, a legacy JSON project may
    # retain an import baseline while intentionally carrying neither source
    # bytes nor a source map; QC for that project remains semantic-only.
    capability_report = session.source_edit_capabilities(block=block)
    if capability_report is None:
        return None

    source_map = getattr(session, "source_docx_map", None)
    baseline_index = session.doc.baseline_index
    baseline_valid = (
        not isinstance(baseline_index, bool)
        and isinstance(baseline_index, int)
        and 0 <= baseline_index < len(session.doc.versions)
    )
    if baseline_valid and session.doc.index < baseline_index:
        # The import was undone. Match SessionState.apply_doc_edits: a new
        # pre-import branch is not constrained by the abandoned source.
        return None
    baseline = _source_baseline(session) if baseline_valid else None
    context = None
    if baseline is not None:
        try:
            context = session.ensure_source_patch_context(baseline=baseline)
        except SourcePatchError:
            # A required guard with no context fails closed in the QC engine.
            pass
    capability_summary = source_capability_summary(
        capability_report,
        session.doc.doc,
    )
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
        baseline=baseline,
        context=context,
        capability_summary=capability_summary,
    )


def _qc_matches_current_inputs(session, result, *, block: bool = False) -> bool:
    """Server-authoritative freshness over document + all QC inputs.

    ``block`` decides whether the imported-source half of the comparison may
    wait for the background permission sweep. Set it on paths that ACT on the
    answer — applying fixes, producing an export — because a not-yet-derived
    capability summary reads as a mismatch, i.e. "stale", and refusing a fix
    for a sweep that simply has not finished would be wrong. Leave it False
    on the hot ``/api/readiness`` and ``/api/qc/status`` polls, where waiting
    minutes on a large master is exactly the freeze this design removes and
    the conservative "assume stale" answer is harmless.
    """
    return bool(
        result is not None
        and result.matches_inputs(
            session.doc.index,
            session.doc.doc,
            session.research.profile_result,
            session.module,
            session.discipline,
            _qc_source_guard(session, block=block),
            model=settings.QC_MODEL,
            max_tokens=settings.QC_MAX_TOKENS,
        )
    )


def _qc_result_is_audit_complete(result) -> bool:
    """Whether a retained result meets the actionable Final-QC contract."""
    return bool(
        result is not None
        and result.schema_version == QC_REPORT_SCHEMA_VERSION
        and result.protocol_version == QC_PROTOCOL_VERSION
        and result.input_fingerprint
        and result.input_manifest
        and result.execution_status == "complete"
        and result.is_complete()
    )


def _qc_export_current_state(
    session,
    result,
    *,
    qc_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Canonical export-time context shared by JSON and Word reports."""
    if qc_record is None:
        qc_record = session.qc.audit_record_snapshot()
    # An export is an audit artifact, so its staleness verdict must come from
    # real permissions rather than from a sweep that has not finished yet.
    current_manifest = build_qc_input_manifest(
        session.doc.doc,
        session.research.profile_result,
        session.module,
        version_index=session.doc.index,
        discipline=session.discipline,
        source_guard=_qc_source_guard(session, block=True),
        model=settings.QC_MODEL,
        max_tokens=settings.QC_MAX_TOKENS,
    )
    matches = _qc_matches_current_inputs(session, result, block=True)
    state = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "document_version": session.doc.index,
        "document_fingerprint": qc_version_fingerprint(session.doc.doc),
        "current_input_fingerprint": qc_input_fingerprint(current_manifest),
        "current_input_manifest": current_manifest,
        "report_matches_current_inputs": matches,
        "stale": not matches,
        "runner": dict(qc_record.get("runner") or {}),
        "latest_attempt": qc_record.get("latest_attempt"),
        "readiness": _readiness_payload(session, qc_record=qc_record),
    }
    retained = qc_record.get("result")
    if (
        isinstance(retained, dict)
        and str(retained.get("run_id") or "")
        and str(retained.get("run_id") or "") != result.run_id
    ):
        state["last_successful_report"] = {
            "run_id": retained.get("run_id"),
            "execution_status": retained.get("execution_status"),
            "started_at": retained.get("started_at"),
            "finished_at": retained.get("finished_at"),
            "version_index": retained.get("version_index"),
            "version_fingerprint": retained.get("version_fingerprint"),
            "input_fingerprint": retained.get("input_fingerprint"),
            "summary": retained.get("summary"),
        }
    return state


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
        # This compatibility field remains document-level; exact per-UID and
        # per-operation decisions live in the sibling source_capabilities.
        "body_editing": "bounded" if status == "ready" else "disabled",
        "no_op": bool(preservation and preservation.no_op),
        "changed_uids": list(preservation.changed_uids) if preservation else [],
        "blockers": blockers,
    }


def _doc_payload(session) -> dict[str, Any]:
    profile = ProjectProfile.from_dict(session.doc.doc.project_profile)
    preservation = _source_readiness(session)
    capabilities = session.source_edit_capabilities()
    return {
        "doc": session.doc.snapshot(),
        "open_questions": open_questions(session.doc.doc),
        "lint": lint_document(
            session.doc.doc,
            session.module,
            unstructured_import=session.import_is_unstructured(),
        ),
        "standards": standards_payload(session),
        "profile_complete": bool(profile and profile.is_complete()),
        "research_status": session.research.status,
        # The imported-master version index (Batch 5), for the compare
        # picker's "Master (import)" option; ``None`` for from-scratch.
        "baseline_index": session.doc.baseline_index,
        # Chat-authored figures (diagrams/schematics/tables) — full source so
        # the frontend can render + offer downloads. Not part of the doc tree.
        "figures": session.figures.snapshot(),
        # Attached reference material — metadata only; the bodies are read on
        # demand through the model's tool, never shipped with every payload.
        "reference_docs": session.references.snapshot(),
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
        "source_capabilities": (
            capabilities.to_dict() if capabilities is not None else None
        ),
    }


def _readiness_payload(
    session, *, qc_record: dict[str, Any] | None = None
) -> dict[str, Any]:
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
    lint_items = lint_document(
        doc,
        session.module,
        unstructured_import=session.import_is_unstructured(),
    )
    profile = ProjectProfile.from_dict(doc.project_profile)
    profile_ok = bool(profile and profile.is_complete())
    research_ok = session.research.status == "complete"

    if qc_record is None:
        qc_record = session.qc.audit_record_snapshot()
    qc_result = qc_record.get("result_model")
    runner_state = qc_record.get("runner") or {}
    runner_status = str(runner_state.get("status") or "idle")
    qc_matches_inputs = _qc_matches_current_inputs(session, qc_result)
    latest_attempt = qc_record.get("latest_attempt")
    latest_status = (
        str(latest_attempt.get("status") or "").lower()
        if isinstance(latest_attempt, dict)
        else ""
    )
    latest_report = (
        latest_attempt.get("report")
        if isinstance(latest_attempt, dict)
        else None
    )
    latest_has_report = bool(
        isinstance(latest_attempt, dict)
        and latest_attempt.get("report_available")
        and isinstance(latest_report, dict)
    )
    settling = bool(runner_state.get("settling", False))
    qc_audit_grade = bool(
        qc_result is not None
        and qc_result.schema_version == QC_REPORT_SCHEMA_VERSION
        and qc_result.protocol_version == QC_PROTOCOL_VERSION
        and qc_result.input_fingerprint
        and qc_result.input_manifest
    )
    latest_attempt_matches = bool(
        qc_result is not None
        and runner_status == "complete"
        and not settling
        and isinstance(latest_attempt, dict)
        and latest_status == "complete"
        and latest_attempt.get("run_id") == qc_result.run_id
        and latest_has_report
        and latest_report.get("run_id") == qc_result.run_id
        and latest_report.get("execution_status") == "complete"
    )
    # Freshness and audit sufficiency are intentionally separate readiness
    # checks.  "Current" answers only whether the retained report belongs to
    # the live review inputs and is also the latest completed attempt;
    # "audit complete" answers whether that current report carries the full
    # current coverage/verifier contract and has no unresolved critical findings.
    # Keeping the predicates distinct makes a failed gate diagnosable instead
    # of collapsing provenance, completeness, and severity into one boolean.
    qc_current = bool(qc_matches_inputs and latest_attempt_matches)
    qc_audit_complete = bool(
        qc_result is not None
        and qc_audit_grade
        and qc_result.is_complete()
        and qc_result.open_critical_count() == 0
    )
    evidence_detail = (
        "Its paid report is preserved in QC status and export."
        if latest_has_report
        else "Its attempt metadata is preserved, but no report was recovered."
    )
    if settling:
        qc_current_detail = (
            "The stopped Final QC attempt is still settling while already-paid "
            "work is attached to its audit record; wait for settlement before "
            "relying on readiness."
        )
    elif qc_result is None:
        if latest_status == "partial":
            qc_current_detail = (
                "The latest Final QC attempt settled partial, not complete. "
                f"{evidence_detail} No audit-complete retained result is available."
            )
        elif latest_status == "cancelled":
            qc_current_detail = (
                "The latest Final QC attempt was cancelled. "
                f"{evidence_detail} No audit-complete retained result is available."
            )
        elif latest_status == "failed":
            qc_current_detail = (
                "The latest Final QC attempt failed. "
                f"{evidence_detail} No audit-complete retained result is available."
            )
        elif latest_status == "running":
            qc_current_detail = "Final QC is running and has not settled."
        elif latest_status == "complete":
            qc_current_detail = (
                "The latest attempt is labeled complete, but no validated "
                "audit-complete retained result is available."
            )
        else:
            qc_current_detail = "Final QC has not been run."
    elif not latest_attempt_matches:
        if latest_status == "partial":
            qc_current_detail = (
                "The latest Final QC rerun settled partial. "
                f"{evidence_detail} The retained complete report is not the "
                "latest completed attempt."
            )
        elif latest_status == "cancelled":
            qc_current_detail = (
                "The latest Final QC rerun was cancelled. "
                f"{evidence_detail} The retained complete report is not the "
                "latest completed attempt."
            )
        elif latest_status == "failed":
            qc_current_detail = (
                "The latest Final QC rerun failed. "
                f"{evidence_detail} The retained complete report is not the "
                "latest completed attempt."
            )
        elif latest_status == "running":
            qc_current_detail = (
                "A newer Final QC attempt is running; the retained complete "
                "report is not the latest attempt."
            )
        else:
            qc_current_detail = (
                "The retained Final QC report does not match a validated latest "
                "complete attempt; re-run Final QC."
            )
    elif not qc_matches_inputs:
        qc_current_detail = (
            "Final QC is stale — the document or another review input "
            "(research, standards, module, or source policy) has changed."
        )
    else:
        qc_current_detail = (
            "Final QC belongs to the current document and complete review "
            "input set."
        )

    if qc_result is None:
        if settling:
            qc_audit_detail = (
                "No actionable audit-complete report is available while the "
                "stopped attempt is still settling."
            )
        elif latest_status in {"partial", "cancelled", "failed"}:
            qc_audit_detail = (
                f"The {latest_status} attempt evidence is preserved, but no "
                "actionable audit-complete retained report is available."
            )
        else:
            qc_audit_detail = "No retained Final QC report is available."
    elif not qc_audit_grade:
        qc_audit_detail = (
            "The saved Final QC result is a legacy or unsupported record "
            "without the current full-input audit contract; re-run Final QC."
        )
    elif not qc_result.is_complete():
        failed_lenses = sum(
            1 for status in qc_result.lens_statuses if status.status != "completed"
        )
        missing_lens_records = sum(
            1
            for status in qc_result.lens_statuses
            if status.status == "completed" and not status.reviewed_checks
        )
        failed_seats = sum(
            1
            for finding in [
                *qc_result.findings,
                *qc_result.refuted,
                *qc_result.inconclusive,
            ]
            for verdict in finding.verdicts
            if verdict.status != "completed"
        )
        missing_seats = sum(
            max(
                0,
                (
                    finding.verification_panel_size
                    or (
                        max(1, settings.QC_VERIFIERS_CRITICAL)
                        if (finding.original_severity or finding.severity)
                        in ("critical", "high")
                        else max(1, settings.QC_VERIFIERS_STANDARD)
                    )
                )
                - len(finding.verdicts),
            )
            for finding in [
                *qc_result.findings,
                *qc_result.refuted,
                *qc_result.inconclusive,
            ]
        )
        qc_audit_detail = (
            "Final QC has incomplete coverage "
            f"({failed_lenses} failed lens(es), {missing_lens_records} lens "
            f"record(s) missing, {failed_seats} failed and {missing_seats} "
            "missing verifier seat(s)); re-run before issue."
        )
    elif qc_result.open_critical_count() > 0:
        qc_audit_detail = (
            f"{qc_result.open_critical_count()} open critical finding(s) — "
            "resolve or dismiss them."
        )
    else:
        qc_audit_detail = (
            "Audit-grade lens coverage and verifier panels are complete, "
            "with no open critical findings."
        )

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
            "detail": qc_current_detail,
            "advisory": False,
        },
        {
            "id": "qc_audit_complete",
            "ok": qc_audit_complete,
            "detail": qc_audit_detail,
            "advisory": False,
        },
    ]
    ready = all(c["ok"] for c in checks if not c["advisory"])
    return {"checks": checks, "ready": ready}


def _qc_snapshot_payload(session) -> dict[str, Any]:
    """Coherent runner, action-result, and export-report status snapshot."""
    qc_record = session.qc.audit_record_snapshot()
    runner = qc_record.get("runner") or {}
    result = qc_record.get("result_model")
    report = qc_record.get("report_for_export_model")
    latest_attempt = qc_record.get("latest_attempt")
    payload: dict[str, Any] = {
        "status": runner.get("status", "idle"),
        "error": runner.get("error", ""),
        "settling": bool(runner.get("settling", False)),
        "events": qc_record.get("events") or [],
        "latest_attempt": latest_attempt,
        "module_section_compatibility": module_section_compatibility(
            session.doc.doc, session.module
        ),
    }
    if qc_record.get("result") is not None:
        payload["result"] = qc_record["result"]
    if qc_record.get("report_for_export") is not None:
        payload["report"] = qc_record["report_for_export"]
    payload["stale"] = bool(
        result is not None
        and not _qc_matches_current_inputs(session, result)
    )
    payload["report_stale"] = bool(
        report is not None
        and not _qc_matches_current_inputs(session, report)
    )
    payload["report_is_latest_attempt"] = bool(
        report is not None
        and isinstance(latest_attempt, dict)
        and latest_attempt.get("report_available")
        and latest_attempt.get("run_id") == report.run_id
    )
    return payload


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
        if body is None:
            session.reset()
        else:
            # Reset and its replacement configuration share the turn-state
            # lock, so a new model stream cannot capture the half-configured
            # fresh session between these writes.
            session.reset(
                module_id=body.module_id,
                discipline=body.discipline,
                project_context=body.project_context,
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
        if not session.request_model_stop():
            return JSONResponse(
                {"ok": False, "error": "No turn is streaming."},
                status_code=409,
            )
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

    @app.get("/api/doc/capabilities")
    def get_doc_capabilities() -> dict:
        """Just the imported-source permission report, for polling.

        The per-element sweep runs in the background, so the panel needs a
        way to notice it finished. This route exists instead of re-polling
        ``GET /api/doc`` because that payload also rebuilds the outline, the
        lint report and the source-readiness plan — all O(document) work the
        poller does not need. ``status`` is ``pending`` while the sweep is
        still running; anything else is settled and the client refreshes the
        document once.
        """
        session = sessions.get_session()
        capabilities = session.source_edit_capabilities()
        return {
            "source_capabilities": (
                capabilities.to_dict() if capabilities is not None else None
            ),
        }

    @app.post("/api/doc/undo")
    def undo_doc() -> JSONResponse:
        session = sessions.get_session()
        with session.session_state_guard():
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
        with session.session_state_guard():
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
        with session.session_state_guard():
            if session.turn_active:
                return JSONResponse(
                    {
                        "ok": False,
                        "error": "A model turn is streaming — try the edit "
                        "again once it finishes.",
                    },
                    status_code=409,
                )
            session.doc.begin_turn()
            try:
                applied = session.apply_doc_edits(body.ops)
            except SpecEditError as exc:
                session.doc.rollback_turn()
                return JSONResponse(
                    {"ok": False, "error": str(exc)}, status_code=400
                )
            session.doc.commit_turn()
            return JSONResponse(
                {"ok": True, "applied": applied, **_doc_payload(session)}
            )

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

    def _export_docx_locked(
        session,
        redline: str | None = None,
        base: int | None = None,
        mode: str | None = None,
    ) -> Response:
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
                context = session.ensure_source_patch_context(
                    baseline=baseline
                )
                payload = build_source_preserving_docx(
                    source_bytes=session.source_docx_bytes,
                    source_map=source_map,
                    baseline=baseline,
                    current=store.doc,
                    context=context,
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

        qc_record = session.qc.audit_record_snapshot()
        readiness = _readiness_payload(session, qc_record=qc_record)
        readiness_by_id = {
            str(check.get("id") or ""): bool(check.get("ok"))
            for check in readiness.get("checks", [])
            if isinstance(check, dict)
        }
        qc_is_issue_grade = bool(
            readiness_by_id.get("qc_current")
            and readiness_by_id.get("qc_audit_complete")
        )
        retained_report = qc_record.get("result")
        qc_result = (
            retained_report
            if isinstance(retained_report, dict) and qc_is_issue_grade
            else None
        )
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

    @app.get("/api/export/docx")
    def export_docx(
        redline: str | None = None,
        base: int | None = None,
        mode: str | None = None,
    ) -> Response:
        session = sessions.get_session()
        # Export one coherent snapshot. This intentionally holds the session
        # guard through generation so a concurrent edit, rerun completion, or
        # disposition cannot mix document bytes with a different QC closing.
        with session.session_state_guard():
            return _export_docx_locked(session, redline, base, mode)

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
        delete_status, figures = session.delete_figure_if_idle(fid)
        if delete_status == "active":
            # Deleting mid-turn would shift the list under the turn's
            # provisional-figure bookkeeping (begin/rollback by index).
            return JSONResponse(
                {
                    "ok": False,
                    "error": "A turn is generating — try again in a moment.",
                },
                status_code=409,
            )
        if delete_status == "missing":
            return JSONResponse(
                {"ok": False, "error": f"No figure {fid!r}."}, status_code=404
            )
        return JSONResponse({"ok": True, "figures": figures})

    # --- Reference documents ------------------------------------------------

    @app.get("/api/references")
    def references_list() -> JSONResponse:
        session = sessions.get_session()
        return JSONResponse(
            {"ok": True, "reference_docs": session.references.snapshot()}
        )

    @app.post("/api/reference/upload")
    async def reference_upload(file: UploadFile) -> JSONResponse:
        """Attach a document as background context for the model.

        Accepts every type in ``reference_extract.REFERENCE_KINDS`` (Word,
        PDF, text, XML, CSV) — background material arrives in whatever format
        the office already has it in, and none of it becomes the spec.

        Deliberately unlike ``/api/import/master``: this never touches the
        document tree, so it has no blank-document precondition and stays
        available at any point in a session.
        """
        session = sessions.get_session()
        entry_generation = session.generation
        submitted_name = (
            (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
        )
        kind = reference_kind_for_filename(submitted_name)
        if kind is None:
            return JSONResponse(
                {
                    "ok": False,
                    "error": (
                        f"That file type is not supported as a reference. "
                        f"Attach a {supported_extensions_phrase()} file."
                    ),
                },
                status_code=400,
            )
        safe_filename = sanitize_reference_filename(submitted_name, kind=kind)
        try:
            source_bytes = await read_upload_bounded(file, label="reference")
        except UploadTooLargeError as exc:
            return JSONResponse(
                {"ok": False, "error": str(exc)}, status_code=413
            )
        except SourcePackageError as exc:
            return JSONResponse(
                {"ok": False, "error": str(exc)}, status_code=400
            )
        # Extraction is seconds of CPU on a long document — off the loop, or
        # it blocks the chat stream (see _prepare_master_import).
        try:
            extraction = await run_in_threadpool(
                _prepare_reference_upload,
                source_bytes,
                filename=safe_filename,
            )
        except (MasterImportError, SourcePackageError, ValueError) as exc:
            return JSONResponse(
                {"ok": False, "error": str(exc)}, status_code=400
            )
        with session.session_state_guard():
            if session.generation != entry_generation:
                return JSONResponse(
                    {
                        "ok": False,
                        "error": "The session was replaced while the "
                        "document was being read — attach it again.",
                    },
                    status_code=409,
                )
            try:
                doc = session.references.add(
                    filename=safe_filename,
                    text=extraction.text,
                    block_count=extraction.block_count,
                    tracked_changes=extraction.tracked_changes,
                    kind=extraction.kind,
                )
            except ReferenceDocError as exc:
                return JSONResponse(
                    {"ok": False, "error": str(exc)}, status_code=400
                )
            snapshot = session.references.snapshot()
        # Whatever the read itself left out (pages past the cap, undecodable
        # pages, a non-UTF-8 encoding) comes first: it is the part the user
        # cannot see from the row that just appeared in the panel.
        warnings: list[str] = list(extraction.warnings)
        if doc.truncated:
            warnings.append(
                f"Only the first {len(doc.text):,} characters were kept "
                f"(the document holds {doc.char_count:,}). The model is told "
                "the tail was not read."
            )
        if doc.tracked_changes:
            warnings.append(
                "The document carries pending tracked changes; it was read "
                "as the Accept-All-Changes view."
            )
        return JSONResponse(
            {
                "ok": True,
                "reference_doc": doc.metadata(),
                "reference_docs": snapshot,
                "warnings": warnings,
            }
        )

    @app.delete("/api/reference/{rid}")
    def reference_delete(rid: str) -> JSONResponse:
        session = sessions.get_session()
        with session.session_state_guard():
            if not session.references.delete(rid):
                return JSONResponse(
                    {"ok": False, "error": f"No reference document {rid!r}."},
                    status_code=404,
                )
            snapshot = session.references.snapshot()
        return JSONResponse({"ok": True, "reference_docs": snapshot})

    # --- Master-spec import (Phase 5) ---------------------------------------

    @app.post("/api/import/master")
    async def import_master(file: UploadFile) -> JSONResponse:
        session = sessions.get_session()
        # The session this upload was chosen for. Reading the master now
        # yields the event loop, so "New session" / a project load can land
        # in between — and a fresh session is blank, so the body-content
        # check below would happily let this master drop into a session the
        # user deliberately started over (possibly on another module or
        # discipline). Generation is the app's existing answer to "was the
        # session replaced out from under this work".
        entry_generation = session.generation
        if session.doc.doc.has_body_content():
            return JSONResponse(
                {
                    "ok": False,
                    "error": "The document already has content — a master "
                    "import is a starting point, not a merge. Start a new "
                    "session first.",
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
        except UploadTooLargeError as exc:
            return JSONResponse(
                {"ok": False, "error": str(exc)}, status_code=413
            )
        except SourcePackageError as exc:
            return JSONResponse(
                {"ok": False, "error": str(exc)}, status_code=400
            )
        # Inspecting, parsing and lexically indexing a master is seconds of
        # blocking CPU on a large section. Running it inline on the event loop
        # froze the whole server for the duration — a streaming chat turn could
        # not deliver a single SSE frame until the import finished. It belongs
        # on a worker thread; the request still awaits it, so the endpoint's
        # contract is unchanged.
        try:
            result, report, source_context = await run_in_threadpool(
                _prepare_master_import, source_bytes, safe_filename
            )
        except (MasterImportError, SourcePatchError, ValueError) as exc:
            return JSONResponse(
                {"ok": False, "error": str(exc)}, status_code=400
            )
        # Adopt the recovery artifact only after validation, parsing, and the
        # document-store transaction all succeed. Failed imports leave the
        # active session untouched.
        try:
            with session.session_state_guard():
                if session.generation != entry_generation:
                    return JSONResponse(
                        {
                            "ok": False,
                            "error": "The session was replaced while the "
                            "master was being read — import it again into "
                            "the current session.",
                        },
                        status_code=409,
                    )
                if session.turn_active:
                    return JSONResponse(
                        {
                            "ok": False,
                            "error": "A model turn is streaming — import "
                            "again once it finishes.",
                        },
                        status_code=409,
                    )
                if session.doc.doc.has_body_content():
                    return JSONResponse(
                        {
                            "ok": False,
                            "error": "The document changed while the master "
                            "was being inspected. Start a new session before "
                            "importing it.",
                        },
                        status_code=409,
                    )
                compatibility = module_section_compatibility(
                    result.section, session.module
                )
                if compatibility["status"] == "mismatch":
                    # Keep the imported specification authoritative.  The
                    # closed-catalog mismatch is advisory and travels with
                    # the existing import warnings for later recovery/load.
                    report["warnings"] = [
                        *report.get("warnings", []),
                        compatibility["message"],
                    ]
                session.doc.adopt_imported(result.section)
                session.source_docx_bytes = source_bytes
                session.source_docx_filename = safe_filename
                session.source_docx_map = result.source_map
                session.source_patch_context = source_context
                session.import_report = report
                # The import counts as session-changing work: invalidate any
                # turn that was streaming against the empty document.
                session.invalidate_model_turn()
        except ValueError as exc:
            return JSONResponse(
                {"ok": False, "error": str(exc)}, status_code=400
            )
        from .tracing import capture as _trace_capture

        _trace_capture.import_event(
            blocks=result.imported_block_count,
            warnings=len(report["warnings"]),
            tracked_changes=result.tracked_changes_detected,
        )
        # The per-element permission sweep is the most expensive thing this
        # app does (O(document) per probe, ~5 probes per paragraph). It no
        # longer runs inline anywhere: start it here so it is already working
        # while this response is written, and report ``pending`` capabilities
        # until it lands. The panel polls ``GET /api/doc/capabilities``.
        session.start_capability_warm()
        # ``_doc_payload`` still costs one source-readiness plan on a freshly
        # imported master. Every other endpoint reaches it through a plain
        # ``def`` handler, i.e. already on a worker thread — this async
        # handler is the one that has to say so.
        payload = await run_in_threadpool(_doc_payload, session)
        return JSONResponse(
            {
                "ok": True,
                "warnings": report["warnings"],
                "imported_block_count": report["imported_block_count"],
                "skipped_empty_count": report["skipped_empty_count"],
                "tracked_changes_detected": report[
                    "tracked_changes_detected"
                ],
                **payload,
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
        with session.session_state_guard():
            if session.qc.status == "running":
                return JSONResponse(
                    {
                        "ok": False,
                        "error": "Final QC is running — let it finish before "
                        "changing the requirements-research input.",
                    },
                    status_code=409,
                )
            run_generation = session.generation
            runner = session.research
            module = session.module
            discipline = session.discipline
            profile_data = dict(session.doc.doc.project_profile)
        profile = ProjectProfile.from_dict(profile_data)
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
        if not module.research_dimensions:
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
        if getattr(module, "open_catalog", False) and not discipline:
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
        with session.session_state_guard():
            if (
                session.generation != run_generation
                or session.research is not runner
            ):
                return JSONResponse(
                    {
                        "ok": False,
                        "error": "The session changed while research was starting.",
                    },
                    status_code=409,
                )
            started = runner.start(
                module=module,
                project_profile=profile,
                client=client,
                model=settings.RESEARCH_MODEL,
                max_tokens=settings.RESEARCH_MAX_TOKENS,
                discipline=discipline,
                usage_sink=lambda u, g=run_generation: (
                    session.add_usage_if_current(g, "research", u)
                ),
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
        # Capture every session-derived input beside its owning runner and
        # generation. A reset after this point abandons the old runner and its
        # generation-bound usage sink instead of feeding old inputs into the
        # fresh session.
        with session.session_state_guard():
            profile = session.research.profile_result
            snapshot = SpecSection.from_dict(session.doc.doc.to_dict())
            module = session.module
            discipline = session.discipline
            version_index = session.doc.index
            run_generation = session.generation
            runner = session.audit
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
        if snapshot.is_empty():
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
        with session.session_state_guard():
            if session.generation != run_generation or session.audit is not runner:
                return JSONResponse(
                    {
                        "ok": False,
                        "error": "The session changed while the audit was starting.",
                    },
                    status_code=409,
                )
            started = runner.start(
                section=snapshot,
                profile=profile,
                module=module,
                client=client,
                model=settings.RESEARCH_MODEL,
                max_tokens=settings.RESEARCH_MAX_TOKENS,
                version_index=version_index,
                discipline=discipline,
                usage_sink=lambda u, g=run_generation: (
                    session.add_usage_if_current(g, "audit", u)
                ),
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
    def qc_start(
        body: QcStartRequest | None = Body(default=None),
    ) -> JSONResponse:
        """Launch the spare-no-expense Final-QC pass on Fable 5.

        Research is NOT required — when absent, the completeness lens adapts
        and the result is flagged ``research_profile_present: false``. Gates:
        non-empty draft, an API key, no QC already running, and no model turn
        streaming (a QC of a mid-turn tree would review a moving target).
        """
        session = sessions.get_session()
        _settle_source_capabilities(session)
        with session.session_state_guard():
            if session.turn_active:
                return JSONResponse(
                    {
                        "ok": False,
                        "error": "A model turn is streaming — let it finish "
                        "before running Final QC.",
                    },
                    status_code=409,
                )
            if session.research.status == "running":
                return JSONResponse(
                    {
                        "ok": False,
                        "error": "Requirements research is running — let it "
                        "finish before taking the Final QC snapshot.",
                    },
                    status_code=409,
                )
            if session.doc.doc.is_empty():
                return JSONResponse(
                    {"ok": False, "error": "There is no draft to review yet."},
                    status_code=400,
                )
            compatibility = module_section_compatibility(
                session.doc.doc, session.module
            )
            acknowledged = bool(
                body is not None and body.acknowledge_scope_mismatch
            )
            if compatibility["status"] == "mismatch" and not acknowledged:
                return JSONResponse(
                    {
                        "ok": False,
                        "code": "module_section_mismatch",
                        "error": compatibility["message"],
                        "module_section_compatibility": compatibility,
                    },
                    status_code=409,
                )
            try:
                client = get_client()
            except MissingApiKeyError as exc:
                return JSONResponse(
                    {"ok": False, "error": str(exc)}, status_code=400
                )
            # Snapshot the tree while model-turn claiming is excluded; carry
            # prior dismiss decisions into content-identical findings.
            snapshot = SpecSection.from_dict(session.doc.doc.to_dict())
            source_guard = _qc_source_guard(session, block=True)
            remembered = session.qc.remembered_dismissals()
            run_generation = session.generation
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
                usage_sink=lambda u, g=run_generation: session.add_usage_if_current(
                    g, "qc", u
                ),
            )
            if not started:
                runner_state = session.qc.audit_record_snapshot().get("runner") or {}
                return JSONResponse(
                    {
                        "ok": False,
                        "error": (
                            "The stopped Final QC attempt is still settling; "
                            "wait for its paid partial report to be preserved."
                            if runner_state.get("settling")
                            else "Final QC is already running."
                        ),
                    },
                    status_code=409,
                )
            return JSONResponse({"ok": True})

    @app.get("/api/qc/status")
    def qc_status() -> dict:
        session = sessions.get_session()
        with session.session_state_guard():
            return _qc_snapshot_payload(session)

    @app.post("/api/qc/stop")
    def qc_stop() -> JSONResponse:
        """Stop the running pass while preserving its eventual partial record.

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
        # Bind ownership now, while handling this request. Calling
        # ``sse_events`` inside the response iterator would defer the binding
        # until Starlette begins streaming and could silently attach a queued
        # response to a replacement run.
        bound_events = runner.sse_events()

        def event_stream() -> Iterator[str]:
            for event in bound_events:
                yield _sse(event)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/qc/apply")
    def qc_apply(body: QcApplyRequest) -> JSONResponse:
        """Apply accepted findings' validated ops as ONE undoable version.

        The result must match the current version index and deterministic
        document fingerprint; stale results are rejected before any dry-run.
        Each selected finding is then re-dry-run and the accepted set commits
        atomically. Rejected (409) while a model turn streams.
        """
        session = sessions.get_session()
        _settle_source_capabilities(session)
        with session.session_state_guard():
            if session.turn_active:
                return JSONResponse(
                    {
                        "ok": False,
                        "error": "A model turn is streaming — apply the fix "
                        "once it finishes.",
                    },
                    status_code=409,
                )
            if session.qc.status == "running" or session.qc.is_settling:
                return JSONResponse(
                    {
                        "ok": False,
                        "error": (
                            "Final QC is still settling its stopped attempt — "
                            "wait for paid audit evidence to attach before "
                            "applying a retained report."
                            if session.qc.is_settling
                            else "Final QC is running — wait for the active "
                            "attempt before applying an older report."
                        ),
                    },
                    status_code=409,
                )
            result = session.qc.result
            if result is None:
                return JSONResponse(
                    {"ok": False, "error": "No QC result to apply from."},
                    status_code=409,
                )
            if not _qc_result_is_audit_complete(result):
                return JSONResponse(
                    {
                        "ok": False,
                        "error": (
                            "The retained Final QC result is not an "
                            "audit-complete current-schema report; re-run "
                            "Final QC before applying findings."
                        ),
                    },
                    status_code=409,
                )
            if not _qc_matches_current_inputs(session, result, block=True):
                return JSONResponse(
                    {
                        "ok": False,
                        "error": (
                            "Final QC is stale because the document or another "
                            "review input changed; re-run it before applying fixes."
                        ),
                    },
                    status_code=409,
                )
            result_record = result.to_dict()
            # Capture a coherent working copy and identity. The validated ops
            # are committed only if this exact session/version/result still
            # owns the live state below.
            generation = session.generation
            version_index = session.doc.index
            # Keep the immutable history record itself as the version token.
            # An index alone is ABA-prone: undo followed by a new branch can
            # return to the same numeric index with different content while
            # the potentially expensive QC dry-run below is in progress.
            version_record = session.doc.versions[version_index]
            working = SpecSection.from_dict(version_record)
        outcomes: dict[str, str] = {}
        skipped_events: list[tuple[str, str, str]] = []
        eligible_findings: list[tuple[str, list[dict[str, Any]]]] = []
        selected_ids = list(dict.fromkeys(body.finding_ids))
        for finding_id in selected_ids:
            finding = result.finding(finding_id)
            if finding is None:
                outcomes[finding_id] = "unknown"
                continue
            if (
                getattr(finding, "ops_semantic_status", "") != "approved"
                or not finding.ops_valid
                or not finding.proposed_ops
            ):
                outcomes[finding_id] = "no_ops"
                skipped_events.append(
                    (
                        finding_id,
                        "apply_no_ops",
                        "No semantically approved and mechanically validated "
                        "operations were available.",
                    )
                )
                continue
            if finding.status == "applied":
                outcomes[finding_id] = "already_applied"
                skipped_events.append(
                    (
                        finding_id,
                        "apply_already_applied",
                        "Finding was already marked applied.",
                    )
                )
                continue
            if finding.status != "open":
                outcomes[finding_id] = "not_open"
                skipped_events.append(
                    (
                        finding_id,
                        "apply_not_open",
                        f"Finding disposition is {finding.status!r}; only open "
                        "findings may be applied.",
                    )
                )
                continue
            eligible_findings.append((finding_id, finding.proposed_ops))

        batch = plan_qc_operation_batch(working, eligible_findings)
        if batch.conflicts:
            conflicting_ids = list(
                dict.fromkeys(
                    finding_id
                    for conflict in batch.conflicts
                    for finding_id in conflict["finding_ids"]
                )
            )
            write_keys = sorted(
                {
                    write_key
                    for conflict in batch.conflicts
                    for write_key in conflict["write_keys"]
                }
            )
            return JSONResponse(
                {
                    "ok": False,
                    "code": "qc_operation_conflict",
                    "error": (
                        "Selected Final QC fixes contain conflicting "
                        "operations; nothing was applied."
                    ),
                    "finding_ids": conflicting_ids,
                    "write_keys": write_keys,
                    "conflicts": list(batch.conflicts),
                },
                status_code=409,
            )

        # Preserve the established per-finding stale outcome: one malformed
        # proposal does not prevent unrelated, compatible findings from being
        # applied. Exact operations already accepted for an earlier finding
        # are omitted from later batches, so destructive/additive duplicates
        # execute once while every owning finding can still be dispositioned.
        combined_ops: list[dict[str, Any]] = []
        applied_ids: list[str] = []
        successful_identities: set[str] = set()
        for finding_id, proposed_ops in eligible_findings:
            previously_successful = set(successful_identities)
            normalized = [
                canonical_qc_operation(operation)
                for operation in proposed_ops
            ]
            novel_ops = [
                operation
                for operation in normalized
                if qc_operation_identity(operation) not in previously_successful
            ]
            try:
                if novel_ops:
                    working, _applied = apply_edits(working, novel_ops)
            except SpecEditError:
                outcomes[finding_id] = "stale"
                skipped_events.append(
                    (
                        finding_id,
                        "apply_stale",
                        "The proposed operations no longer applied cleanly in "
                        "the selected batch; nothing from this finding was "
                        "applied.",
                    )
                )
                continue
            combined_ops.extend(novel_ops)
            successful_identities.update(
                qc_operation_identity(operation) for operation in normalized
            )
            applied_ids.append(finding_id)
            outcomes[finding_id] = "applied"

        def record_skipped_outcomes() -> None:
            """Append outcome events while ``session_state_guard`` is held."""
            outcome_version = session.doc.index
            outcome_fingerprint = qc_version_fingerprint(session.doc.doc)
            for finding_id, action, reason in skipped_events:
                session.qc.record_disposition_outcome(
                    finding_id,
                    action=action,
                    reason=reason,
                    document_version=outcome_version,
                    document_fingerprint=outcome_fingerprint,
                )

        if combined_ops:
            with session.session_state_guard():
                if (
                    session.turn_active
                    or session.qc.status == "running"
                    or session.qc.is_settling
                    or not _qc_result_is_audit_complete(result)
                    or session.generation != generation
                    or session.doc.index != version_index
                    or session.doc.versions[version_index] is not version_record
                    or session.doc.doc.to_dict() != version_record
                    or session.qc.result is not result
                    or result.to_dict() != result_record
                ):
                    return JSONResponse(
                        {
                            "ok": False,
                            "error": "The session changed while validating "
                            "the QC fixes; nothing was applied.",
                        },
                        status_code=409,
                    )
                session.doc.begin_turn()
                try:
                    session.apply_doc_edits(combined_ops)
                except SpecEditError as exc:  # pragma: no cover — validated above
                    session.doc.rollback_turn()
                    return JSONResponse(
                        {"ok": False, "error": str(exc)}, status_code=400
                    )
                session.doc.commit_turn()
                session.qc.mark_applied(
                    applied_ids,
                    document_version=session.doc.index,
                    document_fingerprint=qc_version_fingerprint(
                        session.doc.doc
                    ),
                )
                # Applied findings and skipped outcomes are one disposition
                # transaction.  Releasing the lock between these steps could
                # let a new QC run start, producing a 409 response after the
                # document had already committed.
                record_skipped_outcomes()
        else:
            # No document mutation occurred, but outcome events still belong
            # only to the exact result/version validated above.
            with session.session_state_guard():
                if (
                    session.qc.status == "running"
                    or session.qc.is_settling
                    or not _qc_result_is_audit_complete(result)
                    or session.generation != generation
                    or session.doc.index != version_index
                    or session.doc.versions[session.doc.index]
                    is not version_record
                    or session.doc.doc.to_dict() != version_record
                    or session.qc.result is not result
                    or result.to_dict() != result_record
                ):
                    return JSONResponse(
                        {
                            "ok": False,
                            "error": (
                                "The session or QC result changed while "
                                "recording application outcomes; no stale "
                                "outcome was recorded."
                            ),
                        },
                        status_code=409,
                    )
                record_skipped_outcomes()

        return JSONResponse(
            {"ok": True, "outcomes": outcomes, **_doc_payload(session)}
        )

    @app.post("/api/qc/dismiss")
    def qc_dismiss(body: QcDismissRequest) -> JSONResponse:
        session = sessions.get_session()
        with session.session_state_guard():
            if session.qc.status == "running" or session.qc.is_settling:
                return JSONResponse(
                    {
                        "ok": False,
                        "error": (
                            "Final QC is still settling its stopped attempt — "
                            "wait for paid audit evidence to attach before "
                            "recording a disposition."
                            if session.qc.is_settling
                            else "Final QC is running — wait for the active "
                            "attempt before recording a disposition."
                        ),
                    },
                    status_code=409,
                )
            reason = body.reason.strip()
            if not reason:
                return JSONResponse(
                    {
                        "ok": False,
                        "error": (
                            "A dismissal rationale is required for the "
                            "Final QC audit record."
                        ),
                    },
                    status_code=400,
                )
            result = session.qc.result
            if result is not None and not _qc_result_is_audit_complete(result):
                return JSONResponse(
                    {
                        "ok": False,
                        "error": (
                            "The retained Final QC result is not an "
                            "audit-complete current-schema report; re-run "
                            "Final QC before dismissing findings."
                        ),
                    },
                    status_code=409,
                )
            finding = (
                result.finding(body.finding_id) if result is not None else None
            )
            if finding is None:
                return JSONResponse(
                    {"ok": False, "error": "No such finding to dismiss."},
                    status_code=404,
                )
            if finding.status == "applied":
                return JSONResponse(
                    {
                        "ok": False,
                        "error": (
                            "This finding is already applied and cannot be "
                            "rewritten as dismissed."
                        ),
                    },
                    status_code=409,
                )
            if not session.qc.dismiss(
                body.finding_id,
                reason,
                document_version=session.doc.index,
                document_fingerprint=qc_version_fingerprint(session.doc.doc),
            ):
                return JSONResponse(
                    {
                        "ok": False,
                        "error": "The finding disposition could not be changed.",
                    },
                    status_code=409,
                )
            return JSONResponse({"ok": True, "qc": _qc_snapshot_payload(session)})

    @app.get("/api/qc/export")
    def qc_export(run_id: str = "") -> Response:
        session = sessions.get_session()
        _settle_source_capabilities(session)
        with session.session_state_guard():
            qc_record = session.qc.audit_record_snapshot()
            result = qc_record.get("report_for_export_model")
            result_payload = qc_record.get("report_for_export")
            if result is None or not isinstance(result_payload, dict):
                return JSONResponse(
                    {"ok": False, "error": "Run Final QC first."},
                    status_code=409,
                )
            expected_run_id = run_id.strip()
            selected_run_id = str(result_payload.get("run_id") or "")
            if expected_run_id and selected_run_id != expected_run_id:
                return JSONResponse(
                    {
                        "ok": False,
                        "error": (
                            "The selected Final QC report changed before "
                            "download; refresh QC status and try again."
                        ),
                        "expected_run_id": expected_run_id,
                        "selected_run_id": selected_run_id,
                    },
                    status_code=409,
                )
            section = SpecSection.from_dict(session.doc.doc.to_dict())
            current_state = _qc_export_current_state(
                session, result, qc_record=qc_record
            )
            stale = bool(current_state["stale"])
            result_payload["export_current_state"] = current_state
            stem = section.number.replace(" ", "") or "draft"
        payload = build_qc_memo(result_payload, section, stale=stale)
        return Response(
            content=payload,
            media_type=(
                "application/vnd.openxmlformats-officedocument"
                ".wordprocessingml.document"
            ),
            headers=_attachment_headers(f"FINAL QC REPORT {stem}.docx"),
        )

    @app.get("/api/qc/export.json")
    def qc_export_json(run_id: str = "") -> Response:
        """Machine-readable twin of the detailed Word Final QC report."""
        session = sessions.get_session()
        _settle_source_capabilities(session)
        with session.session_state_guard():
            qc_record = session.qc.audit_record_snapshot()
            result = qc_record.get("report_for_export_model")
            report_payload = qc_record.get("report_for_export")
            if result is None or not isinstance(report_payload, dict):
                return JSONResponse(
                    {"ok": False, "error": "Run Final QC first."},
                    status_code=409,
                )
            expected_run_id = run_id.strip()
            selected_run_id = str(report_payload.get("run_id") or "")
            if expected_run_id and selected_run_id != expected_run_id:
                return JSONResponse(
                    {
                        "ok": False,
                        "error": (
                            "The selected Final QC report changed before "
                            "download; refresh QC status and try again."
                        ),
                        "expected_run_id": expected_run_id,
                        "selected_run_id": selected_run_id,
                    },
                    status_code=409,
                )
            section = session.doc.doc
            stem = section.number.replace(" ", "") or "draft"
            current_state = _qc_export_current_state(
                session, result, qc_record=qc_record
            )
            payload = {
                "report": report_payload,
                "current_state": current_state,
            }
            retained_payload = qc_record.get("result")
            if (
                isinstance(retained_payload, dict)
                and retained_payload.get("run_id") != report_payload.get("run_id")
            ):
                payload["last_successful_report"] = retained_payload
        return Response(
            content=json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
            media_type="application/json",
            headers=_attachment_headers(f"FINAL QC REPORT {stem}.json"),
        )

    # --- Readiness gate (deterministic; no model call) ----------------------

    @app.get("/api/readiness")
    def readiness() -> dict:
        """The "can it go out the door" checklist — pure functions of state."""
        session = sessions.get_session()
        with session.session_state_guard():
            return _readiness_payload(session)

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
            with session.session_state_guard():
                payload = sessions.project_package_bytes(session)
                filename = sessions.project_default_filename(session)
        except ProjectPackageError as exc:
            return JSONResponse(
                {"ok": False, "error": str(exc)}, status_code=409
            )
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
            with session.session_state_guard():
                load_project(body, session)
                # JSON has no binary source member. Never retain or claim a
                # map that cannot be checked against exact source bytes.
                session.source_docx_map = None
                session.source_patch_context = None
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
        # The session the user chose this file for. Staging yields the event
        # loop for seconds, so "New session" can complete in between — and
        # this commit replaces everything, so a stale load would silently
        # discard the session the user just deliberately started.
        entry_generation = sessions.get_session().generation
        try:
            payload = await read_project_upload_bounded(file)
            # Staging re-parses and re-indexes the attached master, which is
            # the same seconds-of-CPU work the import path does. Keep it off
            # the event loop so an open never freezes a streaming turn.
            # ``_staged`` is the throwaway session the package was validated
            # against; the live commit below replays the same payload.
            parsed, _staged, typed_map, source_context = await run_in_threadpool(
                _stage_project_load, payload
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
        with session.session_state_guard():
            if session.generation != entry_generation:
                return JSONResponse(
                    {
                        "ok": False,
                        "error": "The session was replaced while the project "
                        "was being read — open it again from the current "
                        "session.",
                    },
                    status_code=409,
                )
            load_project(parsed.project, session)
            session.source_docx_bytes = parsed.source_docx_bytes
            session.source_docx_filename = (
                parsed.source_docx_filename if parsed.source_docx_bytes else ""
            )
            session.source_docx_map = typed_map
            session.source_patch_context = (
                source_context if parsed.source_docx_bytes is not None else None
            )
        # Same reason as the import response: a source-backed project pays for
        # the first capability sweep here, which must not run on the loop.
        payload = await run_in_threadpool(_doc_payload, session)
        return JSONResponse(
            {
                "ok": True,
                "chat": chat_transcript(session.history),
                **payload,
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
