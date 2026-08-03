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
- ``POST /api/qc/start``       → launch Final QC on Opus 5.
- ``GET  /api/qc/status``      → QC state + event log + result view.
- ``GET  /api/qc/stream``      → SSE follow of the active/last QC run.
- ``POST /api/qc/stop``        → stop the running Final QC pass; preserves the
  cancelled attempt identity and any partial audit record that settles (409
  if none is running).
- ``POST /api/qc/apply/preview`` → plan a safe, read-only fix batch.
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

import hashlib
import json
import logging
import re
import secrets
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Literal
from urllib.parse import quote

import anthropic
from fastapi import Body, FastAPI, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from . import diagnostics, settings, sessions
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
from .llm.conversation import (
    SessionState,
    effective_discipline,
    standards_payload,
    stream_user_turn,
)
from .llm.prompts import (
    DraftPrerequisites,
    draft_prerequisites,
    draft_prerequisites_directive,
    full_draft_directive,
)
from .project_profile import ProjectProfile
from .research.engine import (
    research_coverage,
    research_manifest_facts,
    validate_research_facts,
)
from .qc.engine import (
    DISPUTE_REASON_INSUFFICIENT_EVIDENCE,
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
from .reference_docs import ReferenceDocError, prepare_reference_text
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
    CAPABILITY_STATUS_PENDING,
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
from .templates import (
    MAX_TEMPLATE_BYTES,
    TEMPLATE_MEDIA_TYPE,
    TemplateError,
    TemplateImmutableError,
    TemplateNotFoundError,
    get_template_catalog,
    template_summary,
)
from .tracing import capture as _trace_capture
from .tutorial import (
    TUTORIAL_MANIFEST_VERSION,
    analyze_tutorial_coverage,
    blank_practice_copy,
    build_showcase_session,
    media_practice_copy,
    review_practice_copy,
    structural_practice_copy,
)

_DEV_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

_api_log = logging.getLogger("buildaspec.api")

# Poll-driven endpoints the frontend hits every few seconds: logged at
# DEBUG and kept out of the trace so a quiet session doesn't bury the
# forensic record under liveness noise.
_QUIET_PATHS = frozenset(
    {
        "/api/health",
        "/api/doc/capabilities",
        "/api/qc/status",
        "/api/research/status",
        "/api/readiness",
        "/api/usage",
    }
)

_REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_OUTCOME_HEADER = "X-BuildASpec-Outcome-Code"
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_DESKTOP_TOKEN_HEADER = "X-BuildASpec-Token"
_DESKTOP_BOOT_HEADER = "X-BuildASpec-Boot-Nonce"
_DESKTOP_COOKIE_PREFIX = "buildaspec_session_"
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_UNAUTHENTICATED_API_PATHS = frozenset(
    {"/api/health", "/api/bootstrap", "/api/trace/viewer"}
)


@dataclass(frozen=True, repr=False)
class DesktopSecurityConfig:
    """Per-launch loopback trust material supplied only by ``main.py``.

    The global ``backend.app:app`` and ordinary ``create_app()`` calls remain
    intentionally unsecured for hermetic TestClient use and explicit ASGI
    embedding.  The token must never enter repr/log/trace output.
    """

    boot_nonce: str
    api_token: str
    bound_host: str
    bound_port: int
    allowed_hosts: tuple[str, ...]
    allowed_origins: tuple[str, ...]


def _desktop_token_matches(candidate: str, expected: str) -> bool:
    # ``compare_digest`` rejects non-ASCII ``str`` values. Raw HTTP obs-text
    # bytes are decoded by Starlette as Latin-1, so reject those candidates
    # before they can turn an unauthenticated request into a 500 response.
    return (
        bool(candidate)
        and candidate.isascii()
        and expected.isascii()
        and secrets.compare_digest(candidate, expected)
    )


def _desktop_cookie_name(config: DesktopSecurityConfig) -> str:
    """Return a launch-unique, non-secret cookie name.

    Cookies are scoped by host/path, not port. A fixed name lets a second
    ephemeral-port instance overwrite the first instance's download cookie.
    Deriving only the name from the boot nonce lets independent instances'
    HttpOnly cookies coexist without exposing either API token.
    """
    suffix = hashlib.sha256(config.boot_nonce.encode("utf-8")).hexdigest()[:16]
    return f"{_DESKTOP_COOKIE_PREFIX}{suffix}"


def _desktop_boot_nonce_fingerprint(config: DesktopSecurityConfig) -> str:
    """Return a correlation-safe launch identity, never the capability."""
    return hashlib.sha256(config.boot_nonce.encode("utf-8")).hexdigest()


def _apply_defensive_headers(response: Response, *, api_path: bool) -> None:
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
    )
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; base-uri 'none'; object-src 'none'; "
        "frame-ancestors 'none'; form-action 'self'; connect-src 'self'; "
        "script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob: https:; font-src 'self' data:",
    )
    if api_path:
        response.headers.setdefault("Cache-Control", "no-store")


def _desktop_security_error(
    *, status_code: int, code: str, message: str
) -> JSONResponse:
    return _coded_error_response(
        {"ok": False, "code": code, "error": message},
        status_code=status_code,
    )


def _request_correlation_id(request: Request) -> str:
    """Accept a bounded opaque caller ID or create a launch-local one."""
    supplied = request.headers.get(_REQUEST_ID_HEADER, "").strip()
    if _REQUEST_ID_RE.fullmatch(supplied):
        return supplied
    return uuid.uuid4().hex


def _workspace_diagnostic_state() -> dict[str, Any] | None:
    """Best-effort lease identity for traces; diagnostics must never block."""
    try:
        lease = sessions.get_workspace()
        return {
            "workspace_id": lease.workspace_id,
            "workspace_scope": lease.scope,
            "generation": lease.generation,
        }
    except Exception:  # noqa: BLE001 - request handling must stay independent
        return None


def _coded_error_response(
    payload: dict[str, Any], *, status_code: int
) -> JSONResponse:
    """Tag a declared API error for body-free request diagnostics."""
    response = JSONResponse(payload, status_code=status_code)
    declared = str(payload.get("code", "") or "")
    normalized = _trace_capture.normalize_request_outcome_code(
        status_code, declared
    )
    if normalized == declared:
        response.headers[_REQUEST_OUTCOME_HEADER] = normalized
    return response


class ChatRequest(BaseModel):
    message: str


class SaveKeyRequest(BaseModel):
    api_key: str


_CLIENT_EVENT_KINDS = frozenset(
    {"error", "unhandledrejection", "console.error", "console.warn"}
)


class ClientEventRequest(BaseModel):
    kind: str
    message: str
    stack: str = ""
    source: str = ""


class EditDocRequest(BaseModel):
    ops: list[dict[str, Any]]
    workspace_id: int | None = None
    generation: int | None = None


class WorkspaceMutationRequest(BaseModel):
    workspace_id: int | None = None
    generation: int | None = None


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


class QcApplyPreviewBasis(BaseModel):
    """Immutable facts that the read-only remediation plan was built from."""

    workspace_id: int
    generation: int
    run_id: str
    input_fingerprint: str
    document_version: int
    document_fingerprint: str
    result_fingerprint: str
    selected_finding_ids: list[str]
    binding_fingerprint: str


class QcApplyRequest(BaseModel):
    finding_ids: list[str]
    preview_basis: QcApplyPreviewBasis | None = None
    workspace_id: int | None = None
    generation: int | None = None


class QcApplyPreviewDecision(BaseModel):
    """One selected finding's predicted outcome in the planned batch."""

    finding_id: str
    title: str
    severity: str
    status: str
    outcome: Literal[
        "applyable",
        "unknown",
        "no_ops",
        "already_applied",
        "not_open",
        "conflict",
        "stale",
        "source_blocked",
    ]
    reason_code: str
    reason: str
    applyable: bool
    proposed_operation_count: int
    apply_operation_count: int
    duplicate_operation_count: int
    conflicts_with: list[str]


class QcApplyPreviewOperationCounts(BaseModel):
    proposed: int
    unique: int
    duplicate: int
    applyable: int


class QcApplyPreviewDeduplication(BaseModel):
    operation: dict[str, Any]
    finding_ids: list[str]
    occurrence_count: int


class QcApplyPreviewConflict(BaseModel):
    write_keys: list[str]
    finding_ids: list[str]
    operations: list[dict[str, Any]]


class QcApplyPreviewResponse(BaseModel):
    ok: Literal[True] = True
    basis: QcApplyPreviewBasis
    decisions: list[QcApplyPreviewDecision]
    operation_counts: QcApplyPreviewOperationCounts
    deduplications: list[QcApplyPreviewDeduplication]
    conflicts: list[QcApplyPreviewConflict]
    applyable_finding_ids: list[str]
    applyable_operations: list[dict[str, Any]]


class QcStartRequest(BaseModel):
    acknowledge_scope_mismatch: bool = False
    workspace_id: int | None = None
    generation: int | None = None


class QcDismissRequest(BaseModel):
    finding_id: str
    reason: str
    workspace_id: int | None = None
    generation: int | None = None


class TestKeyRequest(BaseModel):
    api_key: str | None = None


class TemplatePreviewRequest(BaseModel):
    name: str
    description: str = ""
    mode: Literal["exact", "ai_generalize"] = "exact"


class TemplateCommitRequest(BaseModel):
    preview_token: str


class TemplateUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None


class TutorialStartRequest(BaseModel):
    request_id: str
    # The bundled showcase is the tutorial's only source (no current-project
    # copy, no live generation) — the field survives so a stale client
    # sending another value gets a clear 422 rather than a silent showcase.
    source: Literal["showcase"] = "showcase"
    workspace_id: int
    generation: int


class TutorialRequest(BaseModel):
    tutorial_id: str
    workspace_id: int
    generation: int


class TutorialScenarioRequest(TutorialRequest):
    chapter: str


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


@dataclass(frozen=True)
class _ExportInputs:
    """One coherent export snapshot, fully detached from the live session.

    Captured under ``session_state_guard`` and rendered without it. A ZIP
    rebuild, an XML reparse or a python-docx render is seconds of work on a
    real section, and the turn-state lock is what a chat turn needs to claim
    — so holding it across the render blocks the very turn (and the stop
    request for it) that the lock exists to serialize.

    Coherence does not depend on holding the lock, only on capturing
    together: every field here comes from one guarded read, so the bytes,
    the filename and the QC closing describe the same document however long
    the render takes and whatever the session does meanwhile.
    """

    selected_mode: str
    #: Detached copies — safe to walk after the guard releases.
    current: SpecSection
    redline_base: SpecSection | None = None
    source_bytes: bytes | None = None
    source_map: Any | None = None
    baseline: SpecSection | None = None
    #: Only when already built. When absent the renderer builds one outside
    #: the lock (inside ``build_source_preserving_docx``) and deliberately
    #: does not write it back — caching is not required for correctness, and
    #: publishing it would need the identity revalidated under the guard.
    source_context: Any | None = None
    audit_result: Any | None = None
    qc_result: dict[str, Any] | None = None


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

    The QC endpoints that ACT on permissions — starting a run and both apply
    paths — reason from real ones (``block=True``), and the sweep that
    derives them is minutes of work on a large master. Doing it inside
    ``session_state_guard()`` would hold ``_turn_state_lock`` for that whole
    time — the same lock ``claim_model_turn`` needs — so a chat turn could
    not even start. Settle first, then take the guard and re-check, exactly
    as the import handler does with its parse. A body change landing in that
    window just costs one more sweep behind the lock, which needs a manual
    edit inside the gap; an audit-grade result reasoning from real
    permissions is worth that. The two QC report DOWNLOADS deliberately do
    NOT call this: a download that silently waits out the sweep reads as a
    dead button, so they answer promptly and disclose a pending verification
    inside the export instead (see ``_qc_export_current_state``).
    """
    session.source_edit_capabilities(block=True)


# Sentinel distinguishing "the caller did not pre-sample" from a genuine
# pre-sampled None (a session that is not source-backed).
_UNSAMPLED: Any = object()


def _qc_source_guard(
    session, *, block: bool = False, capability_report: Any = _UNSAMPLED
) -> QCSourceGuard | None:
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

    ``capability_report`` lets a caller that must derive SEVERAL facts from
    ONE capability state pre-sample it and thread it through — the background
    warm publishes under its own lock, not the session guard, so two samples
    inside one request can straddle the publish and disagree (the QC export's
    coherence bug, caught in review on PR #116).
    """
    # Use the same active-branch/source-less boundary as manual/model edits
    # and the capability payload. In particular, a legacy JSON project may
    # retain an import baseline while intentionally carrying neither source
    # bytes nor a source map; QC for that project remains semantic-only.
    if capability_report is _UNSAMPLED:
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


def _qc_matches_current_inputs(
    session, result, *, block: bool = False, source_guard: Any = _UNSAMPLED
) -> bool:
    """Server-authoritative freshness over document + all QC inputs.

    ``block`` decides whether the imported-source half of the comparison may
    wait for the background permission sweep. Set it on paths that ACT on the
    answer — applying fixes — because a not-yet-derived capability summary
    reads as a mismatch, i.e. "stale", and refusing a fix for a sweep that
    simply has not finished would be wrong. Leave it False on the hot
    ``/api/readiness`` and ``/api/qc/status`` polls, where waiting minutes on
    a large master is exactly the freeze this design removes and the
    conservative "assume stale" answer is harmless.

    ``source_guard`` lets a caller thread ONE pre-built guard through every
    comparison it makes, so all its answers describe the same capability
    state (see ``_qc_source_guard``); ``block`` is ignored when it is given.
    """
    return bool(
        result is not None
        and result.matches_inputs(
            session.doc.index,
            session.doc.doc,
            session.research.profile_result,
            session.module,
            effective_discipline(session),
            (
                _qc_source_guard(session, block=block)
                if source_guard is _UNSAMPLED
                else source_guard
            ),
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


def _json_fingerprint(value: object) -> str:
    """Content-address a JSON-compatible snapshot for preview binding."""
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _dry_run_qc_apply_findings(
    working: SpecSection,
    findings: list[tuple[str, list[dict[str, Any]]]],
) -> tuple[
    list[dict[str, Any]],
    list[str],
    dict[str, str],
    dict[str, int],
    SpecSection,
]:
    """Dry-run ordered findings with the same dedupe used by mutation.

    Returns canonical operations, findings that can be dispositioned as
    applied, per-finding stale errors, and each finding's contribution to the
    unique operation batch. A duplicate-only finding is still applyable: the
    shared operation executes once while every finding that owns it can be
    dispositioned.
    """
    combined_ops: list[dict[str, Any]] = []
    applyable_ids: list[str] = []
    stale_errors: dict[str, str] = {}
    operation_counts: dict[str, int] = {}
    successful_identities: set[str] = set()
    for finding_id, proposed_ops in findings:
        normalized = [
            canonical_qc_operation(operation) for operation in proposed_ops
        ]
        local_identities: set[str] = set()
        novel_ops: list[dict[str, Any]] = []
        for operation in normalized:
            identity = qc_operation_identity(operation)
            if (
                identity in successful_identities
                or identity in local_identities
            ):
                continue
            local_identities.add(identity)
            novel_ops.append(operation)
        try:
            if novel_ops:
                working, _applied = apply_edits(working, novel_ops)
        except SpecEditError as exc:
            stale_errors[finding_id] = str(exc)
            operation_counts[finding_id] = 0
            continue
        combined_ops.extend(novel_ops)
        successful_identities.update(local_identities)
        applyable_ids.append(finding_id)
        operation_counts[finding_id] = len(novel_ops)
    return (
        combined_ops,
        applyable_ids,
        stale_errors,
        operation_counts,
        working,
    )


def _qc_apply_preview_plan(
    result,
    working: SpecSection,
    selected_ids: list[str],
    *,
    candidate_validator: Callable[[SpecSection], None] | None = None,
) -> dict[str, Any]:
    """Build a read-only, conflict-free remediation plan for a QC result."""
    decisions_by_id: dict[str, dict[str, Any]] = {}
    eligible_findings: list[tuple[str, list[dict[str, Any]]]] = []
    for finding_id in selected_ids:
        finding = result.finding(finding_id)
        if finding is None:
            decisions_by_id[finding_id] = {
                "finding_id": finding_id,
                "title": "",
                "severity": "",
                "status": "unknown",
                "outcome": "unknown",
                "reason_code": "finding_unknown",
                "reason": "The retained Final QC result has no such finding.",
                "applyable": False,
                "proposed_operation_count": 0,
                "apply_operation_count": 0,
                "duplicate_operation_count": 0,
                "conflicts_with": [],
            }
            continue

        decision = {
            "finding_id": finding_id,
            "title": finding.title,
            "severity": finding.severity,
            "status": finding.status,
            "outcome": "no_ops",
            "reason_code": "no_operations",
            "reason": "This finding has no executable proposed operations.",
            "applyable": False,
            "proposed_operation_count": len(finding.proposed_ops),
            "apply_operation_count": 0,
            "duplicate_operation_count": 0,
            "conflicts_with": [],
        }
        decisions_by_id[finding_id] = decision
        if not finding.proposed_ops:
            continue
        if getattr(finding, "ops_semantic_status", "") != "approved":
            decision["reason_code"] = "operations_not_approved"
            decision["reason"] = (
                "The verifier panel did not semantically approve this "
                "finding's complete operation set."
            )
            if str(getattr(finding, "ops_semantic_reason", "") or "").strip():
                decision["reason"] += (
                    " " + str(finding.ops_semantic_reason).strip()
                )
            continue
        if not finding.ops_valid:
            decision["reason_code"] = "operations_invalid"
            decision["reason"] = (
                "The proposed operations did not pass mechanical validation."
            )
            if str(getattr(finding, "ops_invalid_reason", "") or "").strip():
                decision["reason"] += (
                    " " + str(finding.ops_invalid_reason).strip()
                )
            continue
        if finding.status == "applied":
            decision["outcome"] = "already_applied"
            decision["reason_code"] = "already_applied"
            decision["reason"] = "This finding is already marked applied."
            continue
        if finding.status != "open":
            decision["outcome"] = "not_open"
            decision["reason_code"] = "finding_not_open"
            decision["reason"] = (
                f"This finding is {finding.status!r}; only open findings may "
                "be applied."
            )
            continue
        eligible_findings.append((finding_id, finding.proposed_ops))

    identity_records: dict[str, dict[str, Any]] = {}
    seen_identities: set[str] = set()
    proposed_operation_count = 0
    for finding_id, proposed_ops in eligible_findings:
        duplicate_count = 0
        for raw_operation in proposed_ops:
            operation = canonical_qc_operation(raw_operation)
            identity = qc_operation_identity(operation)
            proposed_operation_count += 1
            record = identity_records.setdefault(
                identity,
                {
                    "operation": operation,
                    "finding_ids": [],
                    "occurrence_count": 0,
                },
            )
            record["occurrence_count"] += 1
            if finding_id not in record["finding_ids"]:
                record["finding_ids"].append(finding_id)
            if identity in seen_identities:
                duplicate_count += 1
            else:
                seen_identities.add(identity)
        decisions_by_id[finding_id]["duplicate_operation_count"] = (
            duplicate_count
        )

    batch = plan_qc_operation_batch(working, eligible_findings)
    conflict_ids = {
        finding_id
        for conflict in batch.conflicts
        for finding_id in conflict["finding_ids"]
    }
    conflicts_with: dict[str, set[str]] = {
        finding_id: set() for finding_id in conflict_ids
    }
    for conflict in batch.conflicts:
        involved = list(conflict["finding_ids"])
        for finding_id in involved:
            conflicts_with.setdefault(finding_id, set()).update(
                other_id for other_id in involved if other_id != finding_id
            )
    for finding_id in conflict_ids:
        decision = decisions_by_id[finding_id]
        related = sorted(conflicts_with.get(finding_id, set()))
        decision["outcome"] = "conflict"
        decision["reason_code"] = "operation_conflict"
        decision["reason"] = (
            "This finding's proposed operations conflict with another "
            "selected finding."
            if related
            else "This finding contains proposed operations that conflict "
            "with one another."
        )
        decision["conflicts_with"] = related

    nonconflicting = [
        item for item in eligible_findings if item[0] not in conflict_ids
    ]
    safe_batch = plan_qc_operation_batch(working, nonconflicting)
    # Removing every owner named by the original conflict plan must leave a
    # conflict-free subset. Fail closed if a future planner changes that
    # invariant instead of presenting a batch that apply would reject.
    if safe_batch.conflicts:  # pragma: no cover - defensive contract guard
        raise RuntimeError("QC preview conflict exclusion was incomplete.")
    (
        applyable_operations,
        applyable_ids,
        stale_errors,
        per_finding_apply_counts,
        applyable_candidate,
    ) = _dry_run_qc_apply_findings(working, nonconflicting)
    for finding_id in applyable_ids:
        decision = decisions_by_id[finding_id]
        decision["outcome"] = "applyable"
        decision["reason_code"] = "ready"
        decision["reason"] = (
            "These approved operations apply cleanly in the selected, "
            "non-conflicting batch."
        )
        decision["applyable"] = True
        decision["apply_operation_count"] = per_finding_apply_counts[
            finding_id
        ]
    for finding_id, error in stale_errors.items():
        decision = decisions_by_id[finding_id]
        decision["outcome"] = "stale"
        decision["reason_code"] = "operations_stale"
        decision["reason"] = (
            "The proposed operations no longer apply cleanly in the selected "
            f"batch: {error}"
        )

    source_error = ""
    if applyable_ids and candidate_validator is not None:
        try:
            candidate_validator(applyable_candidate)
        except SpecEditError as exc:
            source_error = str(exc)
    if source_error:
        for finding_id in applyable_ids:
            decision = decisions_by_id[finding_id]
            decision["outcome"] = "source_blocked"
            decision["reason_code"] = "source_preservation_rejected"
            decision["reason"] = source_error
            decision["applyable"] = False
            decision["apply_operation_count"] = 0
        applyable_ids = []
        applyable_operations = []

    return {
        "decisions": [decisions_by_id[value] for value in selected_ids],
        "operation_counts": {
            "proposed": proposed_operation_count,
            "unique": len(batch.operations),
            "duplicate": proposed_operation_count - len(batch.operations),
            "applyable": len(applyable_operations),
        },
        "deduplications": [
            record
            for record in identity_records.values()
            if record["occurrence_count"] > 1
        ],
        "conflicts": list(batch.conflicts),
        "applyable_finding_ids": applyable_ids,
        "applyable_operations": applyable_operations,
    }


def _qc_export_current_state(
    session,
    result,
    *,
    qc_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Canonical export-time context shared by JSON and Word reports.

    The staleness verdict here deliberately does NOT wait for the imported-
    source permission sweep. The sweep is minutes of work on a large master,
    and a download that silently blocks for it reads as a dead button — the
    exact field report that prompted this. The report being exported is
    already paid, immutable history; only this generated companion changes.
    A pending sweep therefore degrades to the same conservative answer the
    hot ``/api/readiness`` and ``/api/qc/status`` polls give ("stale"), and
    the export SAYS the verification was still pending rather than recording
    the conservative guess as a settled fact — the disclosure rides
    ``input_verification_pending`` into the JSON envelope and the Word
    limitations, so the artifact never claims a verdict it did not compute.
    """
    if qc_record is None:
        qc_record = session.qc.audit_record_snapshot()
    # ONE capability sample, threaded through everything derived below. The
    # background warm publishes under its own lock — the session guard does
    # not exclude it — so a second sample inside this same request can land
    # after the publish and disagree with the first, and an export that says
    # "verification pending" beside "matches current inputs" is an audit
    # artifact contradicting itself (caught in review on PR #116).
    capability_report = session.source_edit_capabilities(block=False)
    verification_pending = bool(
        capability_report is not None
        and capability_report.status == CAPABILITY_STATUS_PENDING
    )
    source_guard = _qc_source_guard(session, capability_report=capability_report)
    current_manifest = build_qc_input_manifest(
        session.doc.doc,
        session.research.profile_result,
        session.module,
        version_index=session.doc.index,
        discipline=effective_discipline(session),
        source_guard=source_guard,
        model=settings.QC_MODEL,
        max_tokens=settings.QC_MAX_TOKENS,
    )
    matches = _qc_matches_current_inputs(
        session, result, source_guard=source_guard
    )
    state = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "document_version": session.doc.index,
        "document_fingerprint": qc_version_fingerprint(session.doc.doc),
        "current_input_fingerprint": qc_input_fingerprint(current_manifest),
        "current_input_manifest": current_manifest,
        "report_matches_current_inputs": matches,
        "stale": not matches,
        "input_verification_pending": verification_pending,
        "runner": dict(qc_record.get("runner") or {}),
        "latest_attempt": qc_record.get("latest_attempt"),
        "readiness": _readiness_payload(
            session, qc_record=qc_record, source_guard=source_guard
        ),
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


def _draft_prerequisites(session) -> DraftPrerequisites:
    """The full-draft gate for the session's current document.

    ONE derivation, two callers — the payload (so the panel can say what is
    still needed before the click) and ``POST /api/draft/full`` (which is
    authoritative at the click). A frontend reimplementation would be free
    to disagree with the endpoint about whether the button is about to
    draft or about to ask.

    The tree is bound once and every field read off that one reference: a
    committing turn swaps ``store.doc`` wholesale, so re-reading per field
    could mix a section number from one version with a country from
    another. Coherence needs the inputs captured together, not a lock held.
    """
    doc = session.doc.doc
    identity = getattr(doc, "project_identity", {}) or {}
    profile = getattr(doc, "project_profile", {}) or {}
    return draft_prerequisites(
        section_number=doc.number,
        section_title=doc.title,
        project_type=str(identity.get("project_type", "") or ""),
        country=str(profile.get("country", "") or ""),
    )


def _doc_payload(session, *, workspace=None) -> dict[str, Any]:
    """Build the full document payload.

    ``workspace`` MUST be supplied by any caller holding
    ``session_state_guard()`` without an ``active_write`` lease. Looking the
    lease up here takes the SessionManager lock, and a tutorial transition
    takes those two locks in the opposite order — manager lock held while
    calling ``invalidate_model_turn()``, which takes the session's
    turn-state lock. That is an AB/BA deadlock, and it wedges every later
    workspace access with it. Undo/redo/edit are safe without this because
    ``active_write`` and the transitions mutually exclude each other under
    the manager lock; ``/api/doc`` and QC apply take no such lease.
    """
    profile = ProjectProfile.from_dict(session.doc.doc.project_profile)
    preservation = _source_readiness(session)
    capabilities = session.source_edit_capabilities()
    if workspace is None:
        workspace = sessions.get_workspace()
    workspace_fields = (
        {
            "workspace_id": workspace.workspace_id,
            "workspace_scope": workspace.scope,
            "generation": session.generation,
        }
        if workspace.session is session
        else {}
    )
    return {
        **workspace_fields,
        "doc": session.doc.snapshot(),
        "open_questions": open_questions(session.doc.doc),
        "lint": lint_document(
            session.doc.doc,
            session.module,
            unstructured_import=session.import_is_unstructured(),
        ),
        "standards": standards_payload(session),
        "profile_complete": bool(profile and profile.is_complete()),
        # What "Draft full section" still needs before it can draft rather
        # than ask (section / project type / country). Server-derived so the
        # panel's tooltip and the endpoint's decision share one answer.
        "draft_prerequisites": _draft_prerequisites(session).to_dict(),
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
        "template_origin": session.template_origin,
        "source_available": session.source_docx_bytes is not None,
        "preservation_ready": bool(preservation and preservation.ready),
        "source_preservation": _source_preservation_payload(
            session, preservation
        ),
        "source_capabilities": (
            capabilities.to_dict() if capabilities is not None else None
        ),
    }


def _research_readiness(session: SessionState) -> tuple[bool, str]:
    """Is the section's requirements research complete enough to issue?

    Runner status alone was the whole test, and it is the wrong one: a round
    reports ``complete`` when ANY dimension completed, so three of four
    dimensions could have failed and readiness still passed — the false pass
    this replaces. What decides it now is the module's declared coverage
    joined to the CUMULATIVE profile statuses (never the latest round's
    events: a dimension that completed earlier and failed in a later round is
    researched, and a failed rerun must not revoke that).

    Everything else about research is unchanged. A round still succeeds when
    one dimension does, the profile still accumulates, and pressing Research
    again is still the remediation path — which is what the failing detail
    says, because it is the only action the user can take.
    """
    status = session.research.status
    if status != "complete":
        return False, f"Research status: {status}."
    profile = session.research.profile_result
    if profile is None:
        return (
            False,
            "Research reports complete but no profile was recorded — press "
            "Research again.",
        )
    module = session.module
    facts = research_manifest_facts(profile, module)
    invalid = validate_research_facts(facts, module)
    if invalid:
        # Fail closed: a self-contradicting record is not evidence that the
        # research happened. Project loading stays permissive.
        return False, f"{invalid} Press Research again."
    coverage = research_coverage(module, profile)
    required_gaps = coverage.required_gaps
    if required_gaps:
        names = ", ".join(gap.title for gap in required_gaps)
        return (
            False,
            f"Required research coverage is missing: {names} "
            f"({len(coverage.completed)} of {coverage.total} dimensions "
            "completed). Press Research again to retry.",
        )
    optional_gaps = coverage.optional_gaps
    if optional_gaps:
        named = "; ".join(
            f"{gap.title} (declared optional: {gap.optional_rationale})"
            if gap.optional_rationale
            else gap.title
            for gap in optional_gaps
        )
        # Passing, but never silently: the absent areas are named, and so is
        # the reason each was declared optional.
        return (
            True,
            f"Requirements research complete for {len(coverage.completed)} of "
            f"{coverage.total} dimensions. Absent optional coverage: {named}.",
        )
    return True, "Requirements research complete."


def _readiness_payload(
    session,
    *,
    qc_record: dict[str, Any] | None = None,
    source_guard: Any = _UNSAMPLED,
) -> dict[str, Any]:
    """The deterministic issue-readiness checklist.

    Non-advisory checks gate ``ready`` (the "can it go out the door" bar,
    per the batch acceptance criteria): no open items, no unreviewed
    imported/assumed blocks, lint clean, research complete, and a current QC
    with no open criticals. ``profile_complete`` is shown but advisory —
    ``research_complete`` already subsumes it.

    ``source_guard`` is the QC export's one-capability-state threading (see
    ``_qc_source_guard``); the live ``/api/readiness`` poll omits it and
    samples for itself as before.
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
    research_ok, research_detail = _research_readiness(session)

    if qc_record is None:
        qc_record = session.qc.audit_record_snapshot()
    qc_result = qc_record.get("result_model")
    runner_state = qc_record.get("runner") or {}
    runner_status = str(runner_state.get("status") or "idle")
    qc_matches_inputs = _qc_matches_current_inputs(
        session, qc_result, source_guard=source_guard
    )
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
    # Chunk 5.4 splits the old collapsed `qc_audit_complete` in two, because
    # it answered two questions with one boolean and a reader could not tell
    # which half had failed. `qc_execution_complete` is about COVERAGE — did
    # every lens and verifier seat actually run — and `no_open_qc_findings`
    # is about DISPOSITION.
    qc_execution_complete = bool(
        qc_result is not None and qc_audit_grade and qc_result.is_complete()
    )
    # Every surviving open finding, not just the criticals. The old bar let a
    # report claim issue-readiness on its identity page while its own sign-off
    # said "OPEN FINDINGS REMAIN" — the sign-off's meaning is the ratified
    # one. Dismiss-with-reason remains the pressure valve, and an
    # undispositioned dispute blocks exactly as an open finding does (a
    # separate term, so dismissing it can actually clear the gate — folding it
    # into is_complete() deadlocked the dismiss route).
    no_open_qc_findings = bool(
        qc_result is not None
        and qc_audit_grade
        and qc_result.open_finding_count() == 0
        and qc_result.open_disputed_count() == 0
    )
    # Retained as a DERIVED alias so existing API consumers keep a single
    # boolean to branch on. It is now the conjunction of the two checks above
    # rather than an independent predicate, so it cannot drift from them.
    qc_audit_complete = qc_execution_complete and no_open_qc_findings
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
            # Deliberately avoids "settled": in this subsystem settling is a
            # term of art for a STOPPED attempt unwinding its paid work, and
            # this branch is the ordinary running case.
            qc_current_detail = (
                "Final QC is running; no completed report is available yet."
            )
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
            "Final QC is stale — the document, another review input "
            "(research, standards, module, or source policy), or the review "
            "configuration itself (model, effort, panel sizes, app version) "
            "has changed since the report was produced."
        )
    else:
        qc_current_detail = (
            "Final QC belongs to the current document and complete review "
            "input set."
        )

    # Two details for the two split checks. They share the "there is no
    # usable report" branches, because neither question can be answered
    # without one — saying "no open findings" about a review that never
    # completed would be the same false reassurance this chunk removes.
    if qc_result is None:
        if settling:
            unavailable = (
                "No actionable audit-complete report is available while the "
                "stopped attempt is still settling."
            )
        elif latest_status in {"partial", "cancelled", "failed"}:
            unavailable = (
                f"The {latest_status} attempt evidence is preserved, but no "
                "actionable audit-complete retained report is available."
            )
        else:
            unavailable = "No retained Final QC report is available."
        qc_execution_detail = unavailable
        qc_findings_detail = unavailable
    elif not qc_audit_grade:
        legacy = (
            "The saved Final QC result is a legacy or unsupported record "
            "without the current full-input audit contract; re-run Final QC."
        )
        qc_execution_detail = legacy
        qc_findings_detail = legacy
    else:
        if not qc_result.is_complete():
            failed_lenses = sum(
                1
                for status in qc_result.lens_statuses
                if status.status != "completed"
            )
            missing_lens_records = sum(
                1
                for status in qc_result.lens_statuses
                if status.status == "completed" and not status.reviewed_checks
            )
            all_candidates = [
                *qc_result.findings,
                *qc_result.refuted,
                *qc_result.disputed,
                *qc_result.inconclusive,
            ]
            failed_seats = sum(
                1
                for finding in all_candidates
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
                for finding in all_candidates
            )
            qc_execution_detail = (
                "Final QC has incomplete coverage "
                f"({failed_lenses} failed lens(es), {missing_lens_records} lens "
                f"record(s) missing, {failed_seats} failed and {missing_seats} "
                "missing verifier seat(s)); re-run before issue."
            )
        else:
            qc_execution_detail = (
                "Audit-grade lens coverage and verifier panels are complete."
            )

        open_disputes = [f for f in qc_result.disputed if f.status == "open"]
        open_findings = [f for f in qc_result.findings if f.status == "open"]
        if open_disputes:
            # A complete panel that disagreed is not an incomplete review, and
            # saying "re-run" would be wrong advice: re-running re-litigates a
            # disagreement rather than resolving it. The disposition is a
            # human's — dismiss with a reason, or fix the provision. Already
            # dismissed disputes are resolved and no longer named here.
            unevidenced = sum(
                1
                for finding in open_disputes
                if finding.dispute_reason
                == DISPUTE_REASON_INSUFFICIENT_EVIDENCE
            )
            also_open = (
                f" {len(open_findings)} other finding(s) are also open."
                if open_findings
                else ""
            )
            qc_findings_detail = (
                f"Final QC has {len(open_disputes)} disputed finding(s) "
                "awaiting human review: the verifier panel completed but did "
                "not agree"
                + (
                    f", and {unevidenced} of them refuted a critical/high "
                    "finding without citing evidence"
                    if unevidenced
                    else ""
                )
                + ". Review each and dismiss with a reason, or address it, "
                "before issue." + also_open
            )
        elif open_findings:
            # Named by severity band so the reader can see at a glance
            # whether this is a life-safety queue or a tidy-up.
            by_severity: dict[str, int] = {}
            for finding in open_findings:
                by_severity[finding.severity] = (
                    by_severity.get(finding.severity, 0) + 1
                )
            breakdown = ", ".join(
                f"{by_severity[name]} {name}"
                for name in ("critical", "high", "medium", "low")
                if by_severity.get(name)
            ) or "severity not recorded"
            qc_findings_detail = (
                f"{len(open_findings)} surviving finding(s) still open "
                f"({breakdown}) — apply each fix or dismiss it with a reason "
                "before issue."
            )
        else:
            qc_findings_detail = (
                "Every surviving finding is applied or dismissed with a "
                "recorded reason, and no dispute is awaiting review."
            )

    checks = [
        {
            "id": "no_open_items",
            # DOCUMENT open items — [TBD] markers and needs_input blocks.
            # Deliberately not QC findings, which `no_open_qc_findings`
            # owns; the two were easy to confuse when only one existed.
            "ok": len(open_items) == 0,
            "detail": "No open document items ([TBD]/needs-input)."
            if not open_items
            else f"{len(open_items)} open document item(s) ([TBD]/needs-input).",
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
            "detail": research_detail,
            "advisory": False,
        },
        {
            "id": "qc_current",
            "ok": qc_current,
            "detail": qc_current_detail,
            "advisory": False,
        },
        {
            "id": "qc_execution_complete",
            "ok": qc_execution_complete,
            "detail": qc_execution_detail,
            "advisory": False,
        },
        {
            "id": "no_open_qc_findings",
            "ok": no_open_qc_findings,
            "detail": qc_findings_detail,
            "advisory": False,
        },
        {
            # A derived alias for API compatibility: exactly the conjunction
            # of the two checks above. Kept non-advisory so an existing
            # consumer that branches on it alone still sees the same gate.
            #
            # `derived` says it RESTATES other checks rather than adding a
            # fact. Without it, a surface listing "what is blocking issue"
            # reports one open finding as two blockers with byte-identical
            # detail (caught in review on PR #106). Deliberately not
            # `advisory`, which in this payload means "shown but does not
            # gate" — the alias does gate; it is simply not independent.
            "derived": True,
            "id": "qc_audit_complete",
            "ok": qc_audit_complete,
            "detail": (
                qc_execution_detail
                if not qc_execution_complete
                else qc_findings_detail
            ),
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
        "error_kind": runner.get("error_kind", ""),
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


def _usage_payload(session: SessionState) -> dict[str, Any]:
    """The ledger snapshot plus the session context gauge.

    ``context`` is the Anthropic-counted conversation size after the last
    committed chat turn (its final request's full prompt plus the retained
    reply) against the model's context window — a gauge, not spend, which is
    why it rides beside the ledger snapshot rather than inside it. None
    until a turn commits (fresh session, reset, or a just-loaded project).
    """
    with session.session_state_guard():
        snapshot = session.usage.snapshot()
        tokens = session.last_context_tokens
    snapshot["context"] = (
        {"tokens": tokens, "window": settings.MODEL_CONTEXT_WINDOW}
        if tokens is not None
        else None
    )
    return snapshot


def _session_bundle(lease: sessions.WorkspaceLease | None = None) -> dict[str, Any]:
    """One coherent hydration payload for workspace transitions."""
    lease = lease or sessions.get_workspace()
    session = lease.session
    with session.session_state_guard():
        doc_payload = _doc_payload(session, workspace=lease)
        return {
            "workspace_id": lease.workspace_id,
            "workspace_scope": lease.scope,
            "generation": session.generation,
            "tutorial_id": lease.tutorial_id,
            "scenario_kind": lease.scenario_kind,
            "tutorial_source": lease.tutorial_source,
            "chat": chat_transcript(session.history),
            **doc_payload,
            "module_id": session.module.module_id,
            "module": session.module.display_name,
            "discipline": effective_discipline(session),
            "project_context": session.project_context,
            "research": session.research.snapshot(),
            "audit": session.audit.snapshot(),
            "qc": _qc_snapshot_payload(session),
            "readiness": _readiness_payload(session),
            "usage": _usage_payload(session),
            "health": {
                "status": "ok",
                "app": settings.APP_NAME,
                "version": settings.VERSION,
                "model": settings.INTERVIEW_MODEL,
                "api_key_present": bool(load_api_key()),
                "module": session.module.display_name,
                "module_id": session.module.module_id,
                "discipline": effective_discipline(session),
                "legacy_discipline": session.discipline,
                "project_context": session.project_context,
                "workspace_id": lease.workspace_id,
                "workspace_scope": lease.scope,
                "generation": session.generation,
            },
        }


def _template_binding(lease: sessions.WorkspaceLease) -> dict[str, Any]:
    return {
        "workspace_id": lease.workspace_id,
        "generation": lease.session.generation,
        "doc_version": lease.session.doc.index,
    }


def _response_text(response: Any) -> str:
    parts: list[str] = []
    for block in getattr(response, "content", []) or []:
        if isinstance(block, dict):
            text = block.get("text")
        else:
            text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts).strip()


def _template_structure_contract(section: SpecSection) -> dict[str, Any]:
    """The facts an AI template preview is never allowed to rewrite.

    AI mode may generalize wording, but it is not a second drafting turn.  A
    stable structural contract makes that distinction enforceable instead of
    relying only on the prompt: no blocks can appear/disappear/reparent, IDs
    remain stable, and unresolved decisions stay unresolved at the same IDs.
    """
    nodes: list[tuple[str, str, int, bool, bool]] = []
    for part in section.parts:
        for article in part.articles:
            nodes.append((article.uid, part.uid, -1, False, False))

            def visit(paragraphs: list[Any], parent: str, depth: int) -> None:
                for paragraph in paragraphs:
                    nodes.append(
                        (
                            paragraph.uid,
                            parent,
                            depth,
                            paragraph.status == "needs_input",
                            "[TBD:" in paragraph.text,
                        )
                    )
                    visit(paragraph.children, paragraph.uid, depth + 1)

            visit(article.paragraphs, article.uid, 0)
    return {
        "number": section.number,
        "title": section.title,
        "identity": dict(section.project_identity),
        "parts": [
            (part.uid, part.number, part.title, [article.uid for article in part.articles])
            for part in section.parts
        ],
        "nodes": nodes,
    }


def _ai_generalized_template_document(session: SessionState) -> dict[str, Any]:
    """Generalize body wording on a clone; never mutate the active project."""
    document = session.doc.doc.to_dict()
    encoded = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
    if len(encoded) > 1_500_000:
        raise TemplateError(
            "This section is too large for AI template generalization; use Exact instead."
        )
    prompt = (
        "Turn the following specification into a reusable semantic starter. "
        "Return ONLY one JSON object with a 'document' field. Preserve every "
        "id, sequence counter, PART/article/paragraph shape, section number, "
        "section title, discipline, and project type. Generalize client, site, "
        "location, quantity, and project-specific body wording without adding "
        "new requirements. Clear project_profile, edition_overrides, "
        "suppressed_standards, and every source_item_id. Keep needs_input and "
        "[TBD: ...] items. Do not invent citations, standards, research, or QC.\n\n"
        + encoded
    )
    try:
        response = get_client().messages.create(
            model=settings.INTERVIEW_MODEL,
            max_tokens=settings.INTERVIEW_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
    except MissingApiKeyError:
        raise
    session.usage.add("template", getattr(response, "usage", None), count_turn=True)
    text = _response_text(response)
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S)
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise TemplateError(
            "The model did not return a valid template preview; try again or use Exact."
        ) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("document"), dict):
        raise TemplateError(
            "The model did not return a valid template document; try again or use Exact."
        )
    try:
        generalized = SpecSection.from_dict(payload["document"])
    except (TypeError, ValueError, RecursionError) as exc:
        raise TemplateError(
            "The model returned malformed template content; try again or use Exact."
        ) from exc
    if _template_structure_contract(generalized) != _template_structure_contract(
        session.doc.doc
    ):
        raise TemplateError(
            "The proposed AI template changed structure, identity, or an unresolved "
            "decision. Nothing was saved; try again or use Exact."
        )
    return generalized.to_dict()


def create_app(
    *,
    desktop_security: DesktopSecurityConfig | None = None,
    _record_start_event: bool = True,
) -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.VERSION,
        docs_url=None if desktop_security is not None else "/docs",
        redoc_url=None if desktop_security is not None else "/redoc",
        openapi_url=None if desktop_security is not None else "/openapi.json",
    )
    app.state.desktop_security = desktop_security

    # Whether the app has ever run on this machine, sampled ONCE at boot —
    # before any request can race it. ``/api/release-notes`` needs to tell a
    # fresh install (announce nothing) from an upgrade off a build that
    # predates ``last_seen_version`` (announce everything). The signal is the
    # update state file, which ``/api/update/check`` CREATES on first run, so
    # reading it per-request would misread a first launch as an upgrade
    # whenever the update check happened to land first. A pure read — nothing
    # is written here, so the hermetic suite is unaffected.
    try:
        from . import updates as _updates_boot

        app.state.ran_before = _updates_boot.default_state_path().exists()
    except Exception:  # noqa: BLE001 — cosmetic signal, never fatal at boot
        app.state.ran_before = False

    if desktop_security is None:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=_DEV_ORIGINS,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    else:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(desktop_security.allowed_origins),
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=[
                "Accept",
                "Content-Type",
                _DESKTOP_TOKEN_HEADER,
                _DESKTOP_BOOT_HEADER,
                _REQUEST_ID_HEADER,
            ],
        )

    # Registration is intentionally deferred until after the lease middleware
    # below. Starlette prepends each new user middleware, giving the effective
    # order diagnostics -> desktop security -> lease -> CORS. Direct
    # create_app() callers have no DesktopSecurityConfig and retain the
    # historical open ASGI API.
    async def _desktop_loopback_security(request: Request, call_next):
        if desktop_security is None:
            return await call_next(request)

        path = request.url.path
        is_api = path == "/api" or path.startswith("/api/")
        host = request.headers.get("host", "").strip().lower()
        allowed_hosts = {
            value.strip().lower() for value in desktop_security.allowed_hosts
        }
        if host not in allowed_hosts:
            response = _desktop_security_error(
                status_code=421,
                code="invalid_host",
                message="This local server does not accept that Host header.",
            )
            _apply_defensive_headers(response, api_path=is_api)
            return response

        origin = request.headers.get("origin", "").strip().rstrip("/").lower()
        allowed_origins = {
            value.strip().rstrip("/").lower()
            for value in desktop_security.allowed_origins
        }
        if origin and origin not in allowed_origins:
            response = _desktop_security_error(
                status_code=403,
                code="invalid_origin",
                message="This local server does not accept that Origin.",
            )
            _apply_defensive_headers(response, api_path=is_api)
            return response

        header_valid = _desktop_token_matches(
            request.headers.get(_DESKTOP_TOKEN_HEADER, ""),
            desktop_security.api_token,
        )
        cookie_valid = _desktop_token_matches(
            request.cookies.get(_desktop_cookie_name(desktop_security), ""),
            desktop_security.api_token,
        )
        request.state.desktop_authenticated = header_valid or cookie_valid

        if is_api and request.method == "OPTIONS":
            # CORS validates the requested method/headers after this guard.
            response = await call_next(request)
        elif path == "/api/bootstrap":
            if not _desktop_token_matches(
                request.headers.get(_DESKTOP_BOOT_HEADER, ""),
                desktop_security.boot_nonce,
            ):
                response = _desktop_security_error(
                    status_code=403,
                    code="invalid_boot_nonce",
                    message="The desktop bootstrap identity is invalid.",
                )
            else:
                response = await call_next(request)
        elif path in _UNAUTHENTICATED_API_PATHS:
            response = await call_next(request)
        elif is_api and not (header_valid or cookie_valid):
            response = _desktop_security_error(
                status_code=401,
                code="desktop_auth_required",
                message="Desktop API authentication is required.",
            )
        elif (
            is_api
            and request.method in _MUTATING_METHODS
            and not header_valid
            and not (cookie_valid and origin in allowed_origins)
        ):
            # Cookie-only browser mutations must prove same-origin. The custom
            # token header independently proves bootstrap and also makes a
            # cross-origin browser issue a preflight.
            response = _desktop_security_error(
                status_code=403,
                code="csrf_required",
                message="This mutation requires same-origin CSRF proof.",
            )
        else:
            response = await call_next(request)

        _apply_defensive_headers(response, api_path=is_api)
        return response

    @app.middleware("http")
    async def _lease_slow_session_operations(request: Request, call_next):
        """Keep workspace transitions out of delayed upload/load commits.

        These handlers yield to bounded reads and CPU worker threads before
        mutating session state.  Holding a manager lease for the complete
        request makes tutorial start/restore report a truthful busy guard,
        while the handler's own generation checks still reject a reset/load
        that replaced the same session object.
        """
        guarded = {
            ("POST", "/api/reference/upload"),
            ("POST", "/api/import/master"),
            ("POST", "/api/project/load-file"),
            ("POST", "/api/research/start"),
            ("POST", "/api/research/stop"),
            ("POST", "/api/audit/start"),
            ("POST", "/api/qc/start"),
            ("POST", "/api/qc/stop"),
            ("POST", "/api/qc/apply/preview"),
            ("POST", "/api/qc/apply"),
            ("POST", "/api/qc/dismiss"),
        }
        if (request.method, request.url.path) not in guarded:
            return await call_next(request)
        lease = sessions.get_workspace()
        try:
            with sessions.active_write(lease.workspace_id):
                return await call_next(request)
        except sessions.WorkspaceConflictError as exc:
            _trace_capture.app_event(
                "workspace_conflict",
                method=request.method,
                path=request.url.path,
                error_kind=type(exc).__name__,
                request_id=getattr(request.state, "request_id", ""),
            )
            return _coded_error_response(
                {
                    "ok": False,
                    "code": "stale_workspace",
                    "error": str(exc),
                },
                status_code=409,
            )

    # Diagnostics is registered last and is therefore outermost, recording
    # security failures and lease 409s. Duration is time-to-response-START;
    # for SSE that is time-to-first-frame, avoiding a lease held for a stream.
    # Security is registered after the lease so it is outside the lease at
    # runtime; rejected requests therefore never acquire active_write.
    app.middleware("http")(_desktop_loopback_security)

    @app.middleware("http")
    async def _request_diagnostics(request: Request, call_next):
        request_id = _request_correlation_id(request)
        request.state.request_id = request_id
        workspace_before = _workspace_diagnostic_state()
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:
            ms = int((time.perf_counter() - started) * 1000)
            _api_log.warning(
                "%s %s -> unhandled %s after %dms [request_id=%s]",
                request.method,
                request.url.path,
                type(exc).__name__,
                ms,
                request_id,
            )
            _trace_capture.request_event(
                method=request.method,
                path=request.url.path,
                status=500,
                duration_ms=ms,
                request_id=request_id,
                query=str(request.url.query)[:200],
                declared_outcome_code="internal_error",
                exception_type=type(exc).__name__,
                workspace_before=workspace_before,
                workspace_after=_workspace_diagnostic_state(),
            )
            raise
        ms = int((time.perf_counter() - started) * 1000)
        response.headers[_REQUEST_ID_HEADER] = request_id
        workspace_after = _workspace_diagnostic_state()
        quiet = request.url.path in _QUIET_PATHS or not request.url.path.startswith(
            "/api"
        )
        _api_log.log(
            logging.DEBUG if quiet else logging.INFO,
            "%s %s -> %d in %dms [request_id=%s]",
            request.method,
            request.url.path,
            response.status_code,
            ms,
            request_id,
        )
        # Every API outcome contributes to the run summary. Poll paths remain
        # DEBUG-only in the text log, but suppressing them from JSONL made
        # request totals look complete when they were not.
        if request.url.path == "/api" or request.url.path.startswith("/api/"):
            _trace_capture.request_event(
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                duration_ms=ms,
                request_id=request_id,
                query=str(request.url.query)[:200],
                declared_outcome_code=response.headers.get(
                    _REQUEST_OUTCOME_HEADER, ""
                ),
                workspace_before=workspace_before,
                workspace_after=workspace_after,
            )
        return response

    async def _internal_error(request: Request, exc: Exception) -> JSONResponse:
        """Catch-all: log the traceback, answer in the app's error idiom.

        Without this an unhandled route error is a bare-text 500 the
        frontend's ``{ok, error}`` JSON parsing chokes on. Starlette sends
        this response and then RE-RAISES the exception by design (so the
        server also sees it) — tests assert through
        ``raise_server_exceptions=False``.
        """
        _api_log.exception(
            "Unhandled error on %s %s [request_id=%s]",
            request.method,
            request.url.path,
            getattr(request.state, "request_id", "unknown"),
        )
        request_id = getattr(request.state, "request_id", "unknown")
        response = _coded_error_response(
            {
                "ok": False,
                "error": (
                    "An internal error occurred. Use the request ID when "
                    "reporting this problem."
                ),
                "code": "internal_error",
                "request_id": request_id,
            },
            status_code=500,
        )
        # Unhandled exceptions are converted by Starlette's outer error
        # middleware, so the normal request/security middleware cannot add
        # these response headers on this path.
        response.headers[_REQUEST_ID_HEADER] = request_id
        if desktop_security is not None:
            _apply_defensive_headers(
                response,
                api_path=request.url.path == "/api"
                or request.url.path.startswith("/api/"),
            )
        return response

    app.add_exception_handler(Exception, _internal_error)

    async def _validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """422s in the same idiom — from loc/msg ONLY. pydantic v2's
        ``errors()[i]["input"]`` carries the submitted value, which on
        ``/api/key`` would echo the key into the response and the log."""
        detail = "; ".join(
            f"{'.'.join(str(p) for p in e.get('loc', []))}: {e.get('msg', '')}"
            for e in exc.errors()[:5]
        )[:500]
        _api_log.warning(
            "422 on %s %s: %s [request_id=%s]",
            request.method,
            request.url.path,
            detail,
            getattr(request.state, "request_id", "unknown"),
        )
        return _coded_error_response(
            {
                "ok": False,
                "error": f"Invalid request: {detail}",
                "code": "validation_error",
            },
            status_code=422,
        )

    app.add_exception_handler(RequestValidationError, _validation_error)

    @app.get("/api/bootstrap", include_in_schema=False)
    def desktop_bootstrap() -> JSONResponse:
        """Exchange the launch nonce for the in-memory desktop API token.

        Middleware validates Host/Origin and the nonce header before this
        handler runs. The token is returned once to the same-origin frontend
        and mirrored into an HttpOnly Strict session cookie for downloads and
        streaming clients that cannot attach a custom header.
        """
        if desktop_security is None:
            return JSONResponse(
                {"ok": False, "error": "Desktop bootstrap is not active."},
                status_code=404,
            )
        response = JSONResponse(
            {
                "ok": True,
                "api_token": desktop_security.api_token,
                "boot_nonce": desktop_security.boot_nonce,
                "bound_port": desktop_security.bound_port,
            }
        )
        response.set_cookie(
            _desktop_cookie_name(desktop_security),
            desktop_security.api_token,
            path="/api",
            httponly=True,
            secure=False,  # loopback is intentionally HTTP-only
            samesite="strict",
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/health")
    def health(request: Request) -> dict:
        if desktop_security is not None and not bool(
            getattr(request.state, "desktop_authenticated", False)
        ):
            # Startup's unauthenticated identity probe is deliberately
            # minimal: enough to reject a wrong listener, no project/key data
            # and no replayable bootstrap capability.
            return {
                "status": "ok",
                "app": settings.APP_NAME,
                "version": settings.VERSION,
                "boot_nonce_fingerprint": _desktop_boot_nonce_fingerprint(
                    desktop_security
                ),
                "bound_host": desktop_security.bound_host,
                "bound_port": desktop_security.bound_port,
            }
        workspace = sessions.get_workspace()
        session = workspace.session
        payload = {
            "status": "ok",
            "app": settings.APP_NAME,
            "version": settings.VERSION,
            "model": settings.INTERVIEW_MODEL,
            "api_key_present": bool(load_api_key()),
            "module": session.module.display_name,
            "module_id": session.module.module_id,
            "discipline": effective_discipline(session),
            # Stable fallback for old projects. The frontend must not use the
            # effective value as a fallback because it can become stale when
            # undo removes versioned identity.
            "legacy_discipline": session.discipline,
            "project_context": session.project_context,
            "workspace_id": workspace.workspace_id,
            "workspace_scope": workspace.scope,
            "generation": session.generation,
        }
        if desktop_security is not None:
            payload.update(
                {
                    "boot_nonce_fingerprint": _desktop_boot_nonce_fingerprint(
                        desktop_security
                    ),
                    "bound_host": desktop_security.bound_host,
                    "bound_port": desktop_security.bound_port,
                }
            )
        return payload

    @app.post("/api/key")
    def save_key(body: SaveKeyRequest) -> JSONResponse:
        try:
            stored_in = save_api_key(body.api_key)
        except ValueError:
            _trace_capture.app_event("key", action="save", ok=False)
            return JSONResponse(
                {"ok": False, "error": "API key is empty."}, status_code=400
            )
        except OSError as exc:
            _trace_capture.app_event("key", action="save", ok=False)
            return JSONResponse(
                {"ok": False, "error": f"Could not store the key: {exc}"},
                status_code=500,
            )
        reset_client_cache()
        _trace_capture.app_event(
            "key", action="save", ok=True, stored_in=stored_in
        )
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
        _trace_capture.app_event("key", action="delete", ok=True)
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
            _trace_capture.app_event("key", action="test", ok=False)
            return JSONResponse({"ok": False, "error": str(exc)})
        except anthropic.APIStatusError as exc:
            _trace_capture.app_event(
                "key", action="test", ok=False, status=exc.status_code
            )
            return JSONResponse({"ok": False, "error": exc.message})
        except anthropic.APIConnectionError:
            _trace_capture.app_event(
                "key", action="test", ok=False, status="connection"
            )
            return JSONResponse(
                {
                    "ok": False,
                    "error": "Could not reach the Anthropic API — check "
                    "your connection.",
                }
            )
        except Exception as exc:  # noqa: BLE001 — surfaced to the user
            _trace_capture.app_event("key", action="test", ok=False)
            return JSONResponse({"ok": False, "error": str(exc)})
        _trace_capture.app_event("key", action="test", ok=True)
        return JSONResponse({"ok": True})

    @app.post("/api/session/reset")
    def reset(body: SessionResetRequest | None = Body(default=None)) -> Response:
        workspace = sessions.get_workspace()
        if workspace.scope != "original":
            return _coded_error_response(
                {
                    "ok": False,
                    "code": "tutorial_active",
                    "error": "End the tour and return to your project before starting a new session.",
                },
                status_code=409,
            )
        session = workspace.session
        had_content = sessions.has_unsaved_progress(session)
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
        _trace_capture.app_event(
            "session_reset",
            module_id=session.module.module_id,
            had_content=had_content,
        )
        return JSONResponse(
            {
                "ok": True,
                "module_id": session.module.module_id,
                "module": session.module.display_name,
                "discipline": effective_discipline(session),
                "project_context": session.project_context,
                # Reset advances the generation synchronously. Returning the
                # resulting lease closes the interval where the UI could send
                # a fresh-session mutation with the discarded generation.
                "workspace_id": workspace.workspace_id,
                "workspace_scope": workspace.scope,
                "generation": session.generation,
            }
        )

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

    @app.get("/api/session/bundle")
    def session_bundle() -> dict:
        """Hydrate the active session after a native-shell restore."""
        return _session_bundle()

    @app.get("/api/modules")
    def modules() -> dict:
        """Compatibility module registry retained for future templates."""
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

    # --- Reusable semantic templates --------------------------------------

    def _template_error(exc: Exception) -> JSONResponse:
        if isinstance(exc, TemplateNotFoundError):
            status = 404
        elif isinstance(exc, TemplateImmutableError):
            status = 409
        else:
            status = 400
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=status)

    @app.get("/api/templates")
    def template_list() -> dict:
        try:
            listing = get_template_catalog().list()
        except TemplateError as exc:
            return _template_error(exc)
        return {
            "ok": True,
            "templates": listing.templates,
            "invalid_personal_count": listing.invalid_personal_count,
        }

    @app.post("/api/templates/preview")
    def template_preview(
        body: TemplatePreviewRequest, request: Request
    ) -> Response:
        def build_preview() -> dict[str, Any]:
            lease = sessions.get_workspace()
            session = lease.session
            with sessions.active_write(lease.workspace_id):
                with session.session_state_guard():
                    if session.turn_active:
                        raise TemplateError(
                            "Wait for the current model turn before creating a template."
                        )
                    version = session.doc.index
                    override = (
                        _ai_generalized_template_document(session)
                        if body.mode == "ai_generalize"
                        else None
                    )
                    sessions.workspace_manager().assert_active(lease)
                    if session.doc.index != version:
                        raise TemplateError(
                            "The project changed while the preview was being created."
                        )
                    token, template = get_template_catalog().preview(
                        session.doc.doc,
                        name=body.name,
                        description=body.description,
                        module_id=session.module.module_id,
                        document_override=override,
                        binding=_template_binding(lease),
                    )
                    preview_section = SpecSection.from_dict(template["document"])
                    preview_diff = (
                        diff_sections(session.doc.doc, preview_section).to_dict()
                        if body.mode == "ai_generalize"
                        else None
                    )
            _trace_capture.app_event(
                "template", action="preview", mode=body.mode, ok=True
            )
            return {
                "ok": True,
                "preview_token": token,
                "template": template_summary(template, "personal"),
                "document": template["document"],
                "mode": body.mode,
                "diff": preview_diff,
            }

        if "text/event-stream" in request.headers.get("accept", ""):
            def events() -> Iterator[str]:
                yield _sse(
                    {
                        "type": "template_status",
                        "stage": (
                            "generalizing"
                            if body.mode == "ai_generalize"
                            else "preparing"
                        ),
                    }
                )
                try:
                    preview = build_preview()
                except (TemplateError, MissingApiKeyError) as exc:
                    yield _sse({"type": "error", "message": str(exc)})
                    return
                except sessions.WorkspaceConflictError as exc:
                    yield _sse(
                        {
                            "type": "error",
                            "code": "stale_workspace",
                            "message": str(exc),
                        }
                    )
                    return
                yield _sse({"type": "template_preview", "preview": preview})

            return StreamingResponse(
                events(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        try:
            return JSONResponse(build_preview())
        except (TemplateError, MissingApiKeyError) as exc:
            return _template_error(exc)
        except sessions.WorkspaceConflictError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=409)

    @app.post("/api/templates")
    def template_commit(body: TemplateCommitRequest) -> JSONResponse:
        lease = sessions.get_workspace()
        try:
            with sessions.active_write(lease.workspace_id):
                with lease.session.session_state_guard():
                    sessions.workspace_manager().assert_fresh(lease)
                    summary = get_template_catalog().commit_preview(
                        body.preview_token, binding=_template_binding(lease)
                    )
        except TemplateError as exc:
            return _template_error(exc)
        except sessions.WorkspaceConflictError:
            return _stale_tutorial_response()
        _trace_capture.app_event(
            "template", action="create", id=summary.get("id"), ok=True
        )
        return JSONResponse({"ok": True, "template": summary})

    @app.patch("/api/templates/{template_id:path}")
    def template_update(
        template_id: str, body: TemplateUpdateRequest
    ) -> JSONResponse:
        try:
            current, _source = get_template_catalog().get(template_id)
            summary = get_template_catalog().update(
                template_id,
                name=current["name"] if body.name is None else body.name,
                description=(
                    current["description"]
                    if body.description is None
                    else body.description
                ),
            )
        except TemplateError as exc:
            return _template_error(exc)
        _trace_capture.app_event(
            "template", action="update", id=template_id, ok=True
        )
        return JSONResponse({"ok": True, "template": summary})

    @app.delete("/api/templates/{template_id:path}")
    def template_delete(template_id: str) -> JSONResponse:
        try:
            get_template_catalog().delete(template_id)
        except TemplateError as exc:
            return _template_error(exc)
        _trace_capture.app_event(
            "template", action="delete", id=template_id, ok=True
        )
        return JSONResponse({"ok": True})

    @app.get("/api/templates/{template_id:path}/export")
    def template_export(template_id: str) -> Response:
        try:
            payload, filename = get_template_catalog().export(template_id)
        except TemplateError as exc:
            return _template_error(exc)
        _trace_capture.app_event(
            "export", kind="template", id=template_id, ok=True
        )
        return Response(
            content=payload,
            media_type=TEMPLATE_MEDIA_TYPE,
            headers=_attachment_headers(filename),
        )

    @app.post("/api/templates/import")
    async def template_import(file: UploadFile) -> JSONResponse:
        data = await file.read(MAX_TEMPLATE_BYTES + 1)
        if len(data) > MAX_TEMPLATE_BYTES:
            return JSONResponse(
                {
                    "ok": False,
                    "error": f"Template exceeds the {MAX_TEMPLATE_BYTES // (1024 * 1024)} MiB limit.",
                },
                status_code=413,
            )
        try:
            # The event-loop rule: this is an ``async def`` handler, and
            # importing a template parses and atomically writes up to 16 MiB
            # under the catalog lock. Inline, that is seconds during which
            # the server answers nothing at all.
            summary = await run_in_threadpool(
                get_template_catalog().import_bytes, data
            )
        except TemplateError as exc:
            return _template_error(exc)
        _trace_capture.app_event(
            "template", action="import", id=summary.get("id"), ok=True
        )
        return JSONResponse({"ok": True, "template": summary})

    @app.post("/api/templates/{template_id:path}/instantiate")
    def template_instantiate(template_id: str) -> JSONResponse:
        lease = sessions.get_workspace()
        if lease.scope == "tutorial" or (
            lease.scope == "scenario" and lease.scenario_kind != "template"
        ):
            return _coded_error_response(
                {
                    "ok": False,
                    "code": "tutorial_scenario_required",
                    "error": "Open the tutorial template scenario before replacing its document.",
                },
                status_code=409,
            )
        try:
            with sessions.active_write(lease.workspace_id):
                with lease.session.session_state_guard():
                    result = get_template_catalog().instantiate(
                        template_id, lease.session
                    )
            _trace_capture.app_event(
                "template", action="instantiate", id=template_id, ok=True
            )
            return JSONResponse(
                {"ok": True, **result, "session": _session_bundle(lease)}
            )
        except TemplateError as exc:
            return _template_error(exc)
        except sessions.WorkspaceConflictError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=409)

    # --- Actual-content tutorial workspaces --------------------------------

    def _tutorial_error(exc: Exception) -> JSONResponse:
        status = 409 if isinstance(exc, sessions.WorkspaceConflictError) else 400
        payload = {"ok": False, "error": str(exc)}
        if status == 409:
            payload["code"] = (
                "workspace_busy"
                if isinstance(exc, sessions.WorkspaceBusyError)
                else "stale_workspace"
            )
        return _coded_error_response(payload, status_code=status)

    def _tutorial_request_is_current(
        lease: sessions.WorkspaceLease, body: TutorialRequest
    ) -> bool:
        return (
            lease.scope in {"tutorial", "scenario"}
            and lease.tutorial_id == body.tutorial_id
            and lease.workspace_id == body.workspace_id
            and lease.generation == body.generation
        )

    def _stale_tutorial_response() -> JSONResponse:
        return _coded_error_response(
            {
                "ok": False,
                "code": "stale_workspace",
                "error": "This tutorial workspace or generation is no longer active.",
            },
            status_code=409,
        )

    @app.get("/api/tutorial/status")
    def tutorial_status() -> dict:
        lease = sessions.get_workspace()
        coverage = (
            analyze_tutorial_coverage(lease.session).to_dict()
            if lease.scope in {"tutorial", "scenario"}
            else None
        )
        payload = {
            "ok": True,
            "active": lease.scope != "original",
            "manifest_version": TUTORIAL_MANIFEST_VERSION,
            "tutorial_id": lease.tutorial_id,
            "workspace_id": lease.workspace_id,
            "generation": lease.session.generation,
            "scope": lease.scope,
            "scenario_kind": lease.scenario_kind,
            "source": lease.tutorial_source,
            "coverage": coverage,
        }
        if lease.scope != "original":
            payload["session"] = _session_bundle(lease)
        return payload

    @app.post("/api/tutorial/start")
    def tutorial_start(body: TutorialStartRequest) -> JSONResponse:
        manager = sessions.workspace_manager()
        try:
            lease = manager.begin_tutorial(
                body.workspace_id,
                expected_generation=body.generation,
                staged_session=build_showcase_session(),
                request_id=body.request_id,
                source="showcase",
            )
            coverage = analyze_tutorial_coverage(lease.session)
            _trace_capture.app_event(
                "tutorial",
                action="start",
                source="showcase",
                coverage_ready=coverage.ready,
            )
            return JSONResponse(
                {
                    "ok": True,
                    "tutorial_id": lease.tutorial_id,
                    "workspace_id": lease.workspace_id,
                    "generation": lease.generation,
                    "source": "showcase",
                    "coverage": coverage.to_dict(),
                    "session": _session_bundle(lease),
                }
            )
        except (sessions.WorkspaceConflictError, sessions.WorkspaceBusyError) as exc:
            return _tutorial_error(exc)

    @app.post("/api/tutorial/scenario/start")
    def tutorial_scenario_start(body: TutorialScenarioRequest) -> JSONResponse:
        lease = sessions.get_workspace()
        if lease.scope != "tutorial" or not _tutorial_request_is_current(lease, body):
            return _stale_tutorial_response()
        chapter = body.chapter.strip().lower()
        # Ordered substring match with a `structural` catch-all: an unmapped
        # chapter name does NOT error, it quietly starts the structural
        # practice copy.  Every new kind therefore needs its own branch here,
        # ahead of that fallback. "references" also covers Chapter 6's
        # figures (media_practice_copy).
        kind = (
            "blank"
            if "blank" in chapter
            else "review"
            if "review" in chapter
            else "import"
            if "import" in chapter
            else "template"
            if "template" in chapter
            else "project_roundtrip"
            if "save" in chapter or "project" in chapter
            else "references"
            if "reference" in chapter or "figure" in chapter
            else "research"
            if "research" in chapter
            else "qc"
            if "qc" in chapter or "readiness" in chapter
            else "structural"
        )
        try:
            staged: SessionState | None = None
            if kind == "blank":
                staged = blank_practice_copy(lease.session)
            elif kind == "structural":
                staged = structural_practice_copy(lease.session)
            elif kind == "review":
                staged = review_practice_copy(lease.session)
            elif kind == "import":
                source_bytes = build_docx(lease.session.doc.doc)
                imported, report, source_context = _prepare_master_import(
                    source_bytes, "tutorial-section.docx"
                )
                staged = SessionState()
                staged.module = lease.session.module
                staged.doc.adopt_imported(imported.section)
                staged.source_docx_bytes = source_bytes
                staged.source_docx_filename = "tutorial-section.docx"
                staged.source_docx_map = imported.source_map
                staged.source_patch_context = source_context
                staged.import_report = report
            elif kind == "template":
                staged = SessionState()
                token, _template = get_template_catalog().preview(
                    lease.session.doc.doc,
                    name="Tutorial spec starter",
                    description="Temporary template created from the active tutorial specification.",
                    module_id=lease.session.module.module_id,
                )
                get_template_catalog().instantiate_preview(token, staged)
            elif kind == "project_roundtrip":
                project_bytes = sessions.project_package_bytes(lease.session)
                _parsed, staged, _typed_map, _source_context = _stage_project_load(
                    project_bytes
                )
            build = media_practice_copy if kind == "references" else None
            # "references" goes through push_scenario's reserve-then-build
            # sequence (via `build=`) rather than computing `staged` here.
            # The build is bundled-only now — no billed model call — but the
            # ordering (reserve the scenario slot, then construct) is still
            # the safe shape and is what the manager's race tests pin.
            scenario = sessions.workspace_manager().push_scenario(
                body.workspace_id, kind=kind, staged_session=staged, build=build
            )
        except (
            sessions.WorkspaceConflictError,
            sessions.WorkspaceBusyError,
            TemplateError,
            MasterImportError,
            SourcePatchError,
            ProjectPackageError,
            ValueError,
        ) as exc:
            return _tutorial_error(exc)
        _trace_capture.app_event(
            "tutorial", action="scenario_start", chapter=chapter, kind=kind
        )
        return JSONResponse({"ok": True, "session": _session_bundle(scenario)})

    @app.post("/api/tutorial/scenario/finish")
    def tutorial_scenario_finish(body: TutorialRequest) -> JSONResponse:
        lease = sessions.get_workspace()
        if lease.scope != "scenario" or not _tutorial_request_is_current(lease, body):
            return _stale_tutorial_response()
        try:
            restored = sessions.workspace_manager().pop_scenario(body.workspace_id)
        except (sessions.WorkspaceConflictError, sessions.WorkspaceBusyError) as exc:
            return _tutorial_error(exc)
        _trace_capture.app_event("tutorial", action="scenario_finish")
        return JSONResponse({"ok": True, "session": _session_bundle(restored)})

    @app.post("/api/tutorial/restore")
    def tutorial_restore(body: TutorialRequest) -> JSONResponse:
        lease = sessions.get_workspace()
        if not _tutorial_request_is_current(lease, body):
            return _stale_tutorial_response()
        try:
            current_workspace_id = body.workspace_id
            if lease.scope == "scenario":
                tutorial_lease = sessions.workspace_manager().pop_scenario(
                    current_workspace_id
                )
                current_workspace_id = tutorial_lease.workspace_id
            restored = sessions.workspace_manager().finish_tutorial(
                current_workspace_id
            )
        except (sessions.WorkspaceConflictError, sessions.WorkspaceBusyError) as exc:
            return _tutorial_error(exc)
        _trace_capture.app_event("tutorial", action="restore")
        return JSONResponse({"ok": True, "session": _session_bundle(restored)})

    @app.post("/api/chat")
    def chat(body: ChatRequest) -> StreamingResponse:
        lease = sessions.get_workspace()
        session = lease.session

        def event_stream() -> Iterator[str]:
            try:
                with sessions.active_write(lease.workspace_id):
                    for event in stream_user_turn(session, body.message):
                        if lease.scope == "original":
                            yield _sse(event)
                        else:
                            yield _sse(
                                {
                                    **event,
                                    "workspace_id": lease.workspace_id,
                                    "generation": session.generation,
                                }
                            )
            except sessions.WorkspaceConflictError as exc:
                yield _sse({"type": "error", "message": str(exc)})

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
            _trace_capture.app_event(
                "stop_requested", target="chat", accepted=False
            )
            return JSONResponse(
                {"ok": False, "error": "No turn is streaming."},
                status_code=409,
            )
        _trace_capture.app_event("stop_requested", target="chat", accepted=True)
        return JSONResponse({"ok": True})

    @app.post("/api/draft/full")
    def draft_full() -> JSONResponse:
        """Hand the frontend the user message the full-draft click should send.

        Deliberately thin: it owns no drafting machinery of its own. The
        message is an ordinary user message the frontend sends back through
        ``/api/chat``, so the pass rides the existing SSE stream, tool loop,
        status strip, one-undo-step commit, and rollback — one code path for
        turns, no duplicated pipeline. Refused (409) while a model turn is
        streaming or research is running, mirroring the manual-edit guard: a
        drafting turn launched into either would collide with in-flight work.

        WHICH message depends on the draft prerequisites. A whole-section
        draft anchors on the section, the project type, and the country, and
        every defaulted provision it lays down inherits them — so with any
        of the three unknown this returns a directive that COLLECTS the
        missing facts instead of one that drafts blind. That is a 200 with
        ``ready: false``, not an error: the request succeeded and the
        payload says what happens next. The click is always honored and
        always advances the work; only the turn it buys changes.
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
        prereqs = _draft_prerequisites(session)
        return JSONResponse(
            {
                "ok": True,
                "ready": prereqs.ready,
                "missing": list(prereqs.missing),
                "message": (
                    full_draft_directive(prereqs)
                    if prereqs.ready
                    else draft_prerequisites_directive(prereqs)
                ),
            }
        )

    # --- Document ----------------------------------------------------------

    @app.get("/api/doc")
    def get_doc() -> dict:
        # The lease is captured BEFORE the guard and passed in: looking it
        # up inside would take the manager lock in the opposite order from
        # a tutorial transition and deadlock (see ``_doc_payload``). This
        # route takes no ``active_write`` lease, so nothing else excludes a
        # transition while it runs.
        lease = sessions.get_workspace()
        session = lease.session
        # One guarded state: the document, its lint report, open questions,
        # figures, suggestions and version pair are read together or not at
        # all. Unguarded, a turn committing mid-payload could return a tree
        # from one version beside a lint report computed against another.
        with session.session_state_guard():
            return _doc_payload(session, workspace=lease)

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

    def _mutation_lease_matches(
        lease: sessions.WorkspaceLease,
        body: WorkspaceMutationRequest | EditDocRequest | None,
    ) -> bool:
        if body is None:
            return True
        return (
            (body.workspace_id is None or body.workspace_id == lease.workspace_id)
            and (body.generation is None or body.generation == lease.generation)
        )

    @app.post("/api/doc/undo")
    def undo_doc(body: WorkspaceMutationRequest | None = None) -> JSONResponse:
        lease = sessions.get_workspace()
        if not _mutation_lease_matches(lease, body):
            return _stale_tutorial_response()
        session = lease.session
        try:
            with sessions.active_write(lease.workspace_id):
                with session.session_state_guard():
                    sessions.workspace_manager().assert_fresh(lease)
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
                    payload = _doc_payload(session, workspace=lease)
                    version_index = session.doc.index
        except sessions.WorkspaceConflictError:
            return _stale_tutorial_response()
        # After the locks release: app_event may lazily start the recorder
        # (one-time mkdir + run.json write), which must not run under the
        # turn-state lock.
        _trace_capture.app_event(
            "doc_history", action="undo", ok=True, index=version_index
        )
        return JSONResponse({"ok": True, **payload})

    @app.post("/api/doc/redo")
    def redo_doc(body: WorkspaceMutationRequest | None = None) -> JSONResponse:
        lease = sessions.get_workspace()
        if not _mutation_lease_matches(lease, body):
            return _stale_tutorial_response()
        session = lease.session
        try:
            with sessions.active_write(lease.workspace_id):
                with session.session_state_guard():
                    sessions.workspace_manager().assert_fresh(lease)
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
                    payload = _doc_payload(session, workspace=lease)
                    version_index = session.doc.index
        except sessions.WorkspaceConflictError:
            return _stale_tutorial_response()
        _trace_capture.app_event(
            "doc_history", action="redo", ok=True, index=version_index
        )
        return JSONResponse({"ok": True, **payload})

    @app.post("/api/doc/edit")
    def edit_doc(body: EditDocRequest) -> JSONResponse:
        """Apply a manual (user-authored) edit batch as one undoable version.

        Same op vocabulary as the model's ``apply_spec_edits`` tool; thanks
        to the v0.6.0 context architecture the model sees the result in its
        next turn's PROJECT CONTEXT with no history surgery. Rejected while a
        model turn streams (409) — a mid-turn manual edit would be swept into
        that turn's commit/rollback.
        """
        lease = sessions.get_workspace()
        if not _mutation_lease_matches(lease, body):
            return _stale_tutorial_response()
        session = lease.session
        try:
            with sessions.active_write(lease.workspace_id):
                with session.session_state_guard():
                    sessions.workspace_manager().assert_fresh(lease)
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
                    edit_error = ""
                    try:
                        applied = session.apply_doc_edits(body.ops)
                    except SpecEditError as exc:
                        session.doc.rollback_turn()
                        edit_error = str(exc)
                        payload = None
                    else:
                        session.doc.commit_turn()
                        payload = _doc_payload(session, workspace=lease)
        except sessions.WorkspaceConflictError:
            return _stale_tutorial_response()
        actions = sorted(
            {
                str(op.get("action", "?"))
                for op in body.ops
                if isinstance(op, dict)
            }
        )[:10]
        if payload is None:
            _trace_capture.app_event(
                "doc_edit",
                ops=len(body.ops),
                actions=actions,
                ok=False,
                error=edit_error,
            )
            return JSONResponse(
                {"ok": False, "error": edit_error}, status_code=400
            )
        _trace_capture.app_event(
            "doc_edit", ops=len(body.ops), actions=actions, ok=True
        )
        return JSONResponse({"ok": True, "applied": applied, **payload})

    def _redline_base_for_export(
        store, redline: str | None, base: int | None
    ) -> tuple[SpecSection | None, JSONResponse | None]:
        """Resolve the ``?redline=`` export mode into a base section (or 400).

        ``master`` compares the current doc against the imported baseline;
        ``version`` against ``versions[base]``. Returns ``(section, None)`` on
        success or ``(None, error_response)`` on a bad request. The section is
        detached, so the caller can run ``diff_sections`` after releasing the
        guard — the diff is the expensive half and needs no lock.
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
        return SpecSection.from_dict(store.versions[base_index]), None

    def _capture_export_inputs(
        session,
        redline: str | None = None,
        base: int | None = None,
        mode: str | None = None,
    ) -> _ExportInputs | JSONResponse:
        """Validate the request and snapshot everything the render needs.

        The CALLER holds ``session_state_guard``; nothing here builds a
        document. Returns the detached inputs, or the error response the
        request earns — every existing status code and message is preserved,
        including the fail-closed 409 when source-preserving export is asked
        for without a validated source package.
        """
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
        redline_base, error = _redline_base_for_export(store, redline, base)
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
            if redline_base is not None
            else (mode or ("source" if imported_scope else "normalized"))
        )
        # Detach the current tree: the render walks it long after the guard
        # is gone, and a committing turn replaces ``store.doc`` outright.
        current = SpecSection.from_dict(store.doc.to_dict())
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
            return _ExportInputs(
                selected_mode="source",
                current=current,
                # Immutable by contract: the retained package bytes and the
                # map built from them at import.
                source_bytes=session.source_docx_bytes,
                source_map=source_map,
                baseline=baseline,
                source_context=session.source_patch_context,
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
        return _ExportInputs(
            selected_mode="normalized",
            current=current,
            redline_base=redline_base,
            audit_result=session.audit.result,
            qc_result=qc_result,
        )

    def _render_export(inputs: _ExportInputs) -> Response:
        """Render a captured snapshot. Runs WITHOUT the session guard."""
        redline_diff = (
            diff_sections(inputs.redline_base, inputs.current)
            if inputs.redline_base is not None
            else None
        )
        if inputs.selected_mode == "source":
            try:
                payload = build_source_preserving_docx(
                    source_bytes=inputs.source_bytes,
                    source_map=inputs.source_map,
                    baseline=inputs.baseline,
                    current=inputs.current,
                    context=inputs.source_context,
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
                headers=_attachment_headers(export_filename(inputs.current)),
            )

        payload = build_docx(
            inputs.current,
            audit_result=inputs.audit_result,
            qc_result=inputs.qc_result,
            redline=redline_diff,
        )
        filename = (
            redline_filename(inputs.current)
            if redline_diff is not None
            else export_filename(inputs.current)
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
        # Capture one coherent snapshot under the guard, then render it
        # without. Coherence comes from capturing together, not from holding
        # the lock: the ZIP/XML/python-docx work is seconds on a real
        # section, and the turn-state lock is what a chat turn must claim —
        # so holding it across the render blocked the turn, and the stop.
        with session.session_state_guard():
            captured = _capture_export_inputs(session, redline, base, mode)
        response = (
            captured
            if isinstance(captured, JSONResponse)
            else _render_export(captured)
        )
        _trace_capture.app_event(
            "export",
            kind="docx",
            mode=mode or "normalized",
            redline=redline or "",
            ok=response.status_code == 200,
        )
        return response

    @app.get("/api/doc/diff")
    def doc_diff(base: int, cur: int | None = None) -> JSONResponse:
        """Serialized SectionDiff between two versions (in-app compare view).

        ``cur`` defaults to the current version index. Indices must be in
        range and distinct.
        """
        session = sessions.get_session()
        with session.session_state_guard():
            store = session.doc
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
            # Bind the two records while the guard still holds. Bounds were
            # checked against the list length a moment ago, and an edit after
            # an undo truncates the redo tail — so reading them afterwards
            # could raise IndexError and surface as a 500 rather than the 400
            # this route defines. A version record is immutable history (QC
            # apply's staleness check depends on that identity), so holding
            # the reference is enough; the expensive tree build and diff then
            # run outside the lock.
            base_record = store.versions[base]
            cur_record = store.versions[cur_index]
            baseline_index = store.baseline_index
        base_section = SpecSection.from_dict(base_record)
        cur_section = SpecSection.from_dict(cur_record)
        diff = diff_sections(base_section, cur_section)
        return JSONResponse(
            {
                "ok": True,
                **diff.to_dict(),
                "base_index": base,
                "cur_index": cur_index,
                "baseline_index": baseline_index,
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
    def figure_delete(
        fid: str, body: WorkspaceMutationRequest | None = None
    ) -> JSONResponse:
        lease = sessions.get_workspace()
        if not _mutation_lease_matches(lease, body):
            return _stale_tutorial_response()
        session = lease.session
        try:
            with sessions.active_write(lease.workspace_id):
                sessions.workspace_manager().assert_fresh(lease)
                delete_status, figures = session.delete_figure_if_idle(fid)
                if delete_status == "active":
                    return JSONResponse(
                        {
                            "ok": False,
                            "error": "A turn is generating — try again in a moment.",
                        },
                        status_code=409,
                    )
                if delete_status == "missing":
                    return JSONResponse(
                        {"ok": False, "error": f"No figure {fid!r}."},
                        status_code=404,
                    )
            _trace_capture.app_event("figure_delete", fid=fid, ok=True)
            return JSONResponse({"ok": True, "figures": figures})
        except sessions.WorkspaceConflictError:
            return _stale_tutorial_response()

    # --- Reference documents ------------------------------------------------

    @app.get("/api/references")
    def references_list() -> JSONResponse:
        session = sessions.get_session()
        return JSONResponse(
            {"ok": True, "reference_docs": session.references.snapshot()}
        )

    @app.post("/api/reference/upload")
    async def reference_upload(
        file: UploadFile,
        workspace_id: int | None = None,
        generation: int | None = None,
    ) -> JSONResponse:
        """Attach a document as background context for the model.

        Accepts every type in ``reference_extract.REFERENCE_KINDS`` (Word,
        PDF, text, XML, CSV) — background material arrives in whatever format
        the office already has it in, and none of it becomes the spec.

        Deliberately unlike ``/api/import/master``: this never touches the
        document tree, so it has no blank-document precondition and stays
        available at any point in a session.
        """
        entry_lease = sessions.get_workspace()
        if not _mutation_lease_matches(
            entry_lease,
            WorkspaceMutationRequest(
                workspace_id=workspace_id, generation=generation
            ),
        ):
            return _stale_tutorial_response()
        session = entry_lease.session
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
        try:
            retained_text, _, _ = prepare_reference_text(extraction.text)
        except ReferenceDocError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        # Use Anthropic's canonical counter rather than a characters/4
        # estimate. This is intentionally done before taking the session lock:
        # it is a network call and the cumulative limit is checked atomically
        # by ReferenceDocStore.add below.
        try:
            counted = await run_in_threadpool(
                get_client().messages.count_tokens,
                model=settings.INTERVIEW_MODEL,
                messages=[{"role": "user", "content": retained_text}],
            )
            token_count = int(counted.input_tokens)
        except Exception as exc:  # noqa: BLE001 - provider errors become UI errors
            return JSONResponse(
                {
                    "ok": False,
                    "error": "Could not count the document with Anthropic's token counter: "
                    + str(exc),
                },
                status_code=502,
            )
        with session.session_state_guard():
            try:
                sessions.workspace_manager().assert_active(entry_lease)
            except sessions.WorkspaceConflictError:
                return _coded_error_response(
                    {
                        "ok": False,
                        "code": "stale_workspace",
                        "error": "The workspace changed while the document was being read — attach it again.",
                    },
                    status_code=409,
                )
            if session.generation != entry_generation:
                return _coded_error_response(
                    {
                        "ok": False,
                        "code": "stale_workspace",
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
                    token_count=token_count,
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
        _trace_capture.app_event(
            "reference",
            action="upload",
            rid=doc.rid,
            kind=doc.kind,
            chars=doc.char_count,
            truncated=doc.truncated,
            warnings=len(warnings),
            ok=True,
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
    def reference_delete(
        rid: str, body: WorkspaceMutationRequest | None = None
    ) -> JSONResponse:
        lease = sessions.get_workspace()
        if not _mutation_lease_matches(lease, body):
            return _stale_tutorial_response()
        session = lease.session
        try:
            with sessions.active_write(lease.workspace_id):
                with session.session_state_guard():
                    sessions.workspace_manager().assert_fresh(lease)
                    delete_status, snapshot = session.delete_reference_if_idle(
                        rid
                    )
                    if delete_status == "active":
                        return JSONResponse(
                            {
                                "ok": False,
                                "error": "A turn is generating — try again in a moment.",
                            },
                            status_code=409,
                        )
                    if delete_status == "missing":
                        return JSONResponse(
                            {
                                "ok": False,
                                "error": f"No reference document {rid!r}.",
                            },
                            status_code=404,
                        )
                    suggested = list(session.suggested_prompts)
                    figures_snapshot = session.figures.snapshot()
        except sessions.WorkspaceConflictError:
            return _stale_tutorial_response()
        _trace_capture.app_event("reference", action="delete", rid=rid, ok=True)
        return JSONResponse(
            {
                "ok": True,
                "reference_docs": snapshot,
                "suggested_prompts": suggested,
                "figures": figures_snapshot,
            }
        )

    # --- Master-spec import (Phase 5) ---------------------------------------

    @app.post("/api/import/master")
    async def import_master(request: Request, file: UploadFile) -> JSONResponse:
        entry_lease = sessions.get_workspace()
        session = entry_lease.session
        if entry_lease.scope == "tutorial":
            return _coded_error_response(
                {
                    "ok": False,
                    "code": "tutorial_scenario_required",
                    "error": "Open the tutorial import scenario before replacing its document.",
                },
                status_code=409,
            )
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
                try:
                    sessions.workspace_manager().assert_active(entry_lease)
                except sessions.WorkspaceConflictError:
                    return _coded_error_response(
                        {
                            "ok": False,
                            "code": "stale_workspace",
                            "error": "The workspace changed while the master was being read — import it again.",
                        },
                        status_code=409,
                    )
                if session.generation != entry_generation:
                    return _coded_error_response(
                        {
                            "ok": False,
                            "code": "stale_workspace",
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
                import_generation_after = session.generation
        except ValueError as exc:
            return JSONResponse(
                {"ok": False, "error": str(exc)}, status_code=400
            )
        _trace_capture.import_event(
            blocks=result.imported_block_count,
            warnings=len(report["warnings"]),
            tracked_changes=result.tracked_changes_detected,
            warning_messages=report["warnings"],
            skipped_empty=report["skipped_empty_count"],
            source_sha256=report["sha256"],
            source_bytes=report["size_bytes"],
            zip_member_count=report["zip_member_count"],
            zip_uncompressed_bytes=report["zip_uncompressed_bytes"],
            spec_shape_detected=report.get("spec_shape_detected", True),
            workspace_id=entry_lease.workspace_id,
            workspace_scope=entry_lease.scope,
            generation_before=entry_generation,
            generation_after=import_generation_after,
            request_id=getattr(request.state, "request_id", ""),
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
        payload = await run_in_threadpool(
            _doc_payload, session, workspace=entry_lease
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
        _trace_capture.app_event("export", kind="original_source", ok=True)
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
    def research_start(
        body: WorkspaceMutationRequest | None = None,
    ) -> JSONResponse:
        lease = sessions.get_workspace()
        if not _mutation_lease_matches(lease, body):
            return _stale_tutorial_response()
        session = lease.session
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
            discipline = effective_discipline(session)
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
        # An open-catalog session researches "{discipline} work" — without a
        # document identity (or legacy saved-project fallback), its templates
        # have nothing reliable to research.
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
    def research_stop(
        body: WorkspaceMutationRequest | None = None,
    ) -> JSONResponse:
        """Stop the running research fan-out. Discards whatever it found.

        Resolves immediately as a failed run (the UI never waits on the
        background thread to notice); a 409 means nothing is running.
        """
        lease = sessions.get_workspace()
        if not _mutation_lease_matches(lease, body):
            return _stale_tutorial_response()
        if not lease.session.research.stop():
            _trace_capture.app_event(
                "stop_requested", target="research", accepted=False
            )
            return JSONResponse(
                {"ok": False, "error": "Research is not running."},
                status_code=409,
            )
        _trace_capture.app_event(
            "stop_requested", target="research", accepted=True
        )
        return JSONResponse({"ok": True})

    @app.get("/api/research/stream")
    def research_stream() -> StreamingResponse:
        runner = sessions.get_session().research
        # Bind the stream to the run in flight NOW (QC precedent): calling
        # `sse_events` inside the response iterator would defer the binding
        # until Starlette begins streaming and could silently attach a
        # queued response to a replacement run.
        bound_events = runner.sse_events()

        def event_stream() -> Iterator[str]:
            for event in bound_events:
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
            discipline = effective_discipline(session)
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

    # --- Final QC on Opus 5 -------------------------------------------------

    @app.post("/api/qc/start")
    def qc_start(
        body: QcStartRequest | None = Body(default=None),
    ) -> JSONResponse:
        """Launch the Final-QC pass on Opus 5.

        Research is NOT required — when absent, the completeness lens adapts
        and the result is flagged ``research_profile_present: false``. Gates:
        non-empty draft, an API key, no QC already running, and no model turn
        streaming (a QC of a mid-turn tree would review a moving target).
        """
        lease = sessions.get_workspace()
        if not _mutation_lease_matches(lease, body):
            return _stale_tutorial_response()
        session = lease.session
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
                return _coded_error_response(
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
                effort=settings.QC_EFFORT,
                version_index=session.doc.index,
                discipline=effective_discipline(session),
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
    def qc_stop(
        body: WorkspaceMutationRequest | None = None,
    ) -> JSONResponse:
        """Stop the running pass while preserving its eventual partial record.

        Resolves immediately as a failed run (the UI never waits on the
        background thread to notice); a 409 means nothing is running.
        """
        lease = sessions.get_workspace()
        if not _mutation_lease_matches(lease, body):
            return _stale_tutorial_response()
        if not lease.session.qc.stop():
            _trace_capture.app_event(
                "stop_requested", target="qc", accepted=False
            )
            return JSONResponse(
                {"ok": False, "error": "Final QC is not running."},
                status_code=409,
            )
        _trace_capture.app_event("stop_requested", target="qc", accepted=True)
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

    @app.post(
        "/api/qc/apply/preview",
        response_model=QcApplyPreviewResponse,
    )
    def qc_apply_preview(body: QcApplyRequest):
        """Plan selected Final-QC fixes without mutating document or audit.

        The preview uses the same audit/current-input gates, conflict planner,
        canonical operation identities, and transactional dry-run as apply.
        It excludes every finding implicated in a conflict rather than
        choosing a winner, then returns the remaining applyable batch with an
        immutable confirmation basis. Bound batch apply recomputes this plan,
        and every apply path still repeats the final mutation validation.
        """
        lease = sessions.get_workspace()
        if not _mutation_lease_matches(lease, body):
            return _stale_tutorial_response()
        session = lease.session
        _settle_source_capabilities(session)
        with session.session_state_guard():
            if session.turn_active:
                return JSONResponse(
                    {
                        "ok": False,
                        "error": "A model turn is streaming — preview fixes "
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
                            "previewing a retained report."
                            if session.qc.is_settling
                            else "Final QC is running — wait for the active "
                            "attempt before previewing an older report."
                        ),
                    },
                    status_code=409,
                )
            result = session.qc.result
            if result is None:
                return JSONResponse(
                    {"ok": False, "error": "No QC result to preview."},
                    status_code=409,
                )
            if not _qc_result_is_audit_complete(result):
                return JSONResponse(
                    {
                        "ok": False,
                        "error": (
                            "The retained Final QC result is not an "
                            "audit-complete current-schema report; re-run "
                            "Final QC before previewing fixes."
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
                            "review input changed; re-run it before previewing "
                            "fixes."
                        ),
                    },
                    status_code=409,
                )
            selected_ids = list(dict.fromkeys(body.finding_ids))
            result_record = result.to_dict()
            result_fingerprint = _json_fingerprint(result_record)
            generation = session.generation
            version_index = session.doc.index
            version_record = session.doc.versions[version_index]
            working = SpecSection.from_dict(version_record)
            document_fingerprint = qc_version_fingerprint(working)

        plan = _qc_apply_preview_plan(
            result,
            working,
            selected_ids,
            candidate_validator=lambda candidate: (
                session.validate_source_backed_candidate(
                    candidate,
                    current=working,
                )
            ),
        )

        # A read-only result must still describe one coherent state. The
        # operation dry-run happens outside the state lock, so repeat the
        # complete binding check before returning it just as apply does before
        # committing a version.
        # Keep manager -> session lock ordering consistent with the rest of
        # the application. The request middleware holds an active workspace
        # lease for this route, so the captured lease cannot transition before
        # the guarded coherence check finishes.
        current_lease = sessions.get_workspace()
        with session.session_state_guard():
            if (
                current_lease.session is not session
                or current_lease.workspace_id != lease.workspace_id
                or session.turn_active
                or session.qc.status == "running"
                or session.qc.is_settling
                or not _qc_result_is_audit_complete(result)
                or not _qc_matches_current_inputs(session, result, block=True)
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
                        "error": (
                            "The session or QC result changed while previewing "
                            "the fixes; request a fresh preview."
                        ),
                    },
                    status_code=409,
                )

        binding_values = {
            "workspace_id": lease.workspace_id,
            "generation": generation,
            "run_id": result.run_id,
            "input_fingerprint": result.input_fingerprint,
            "document_version": version_index,
            "document_fingerprint": document_fingerprint,
            "result_fingerprint": result_fingerprint,
            "selected_finding_ids": selected_ids,
        }
        basis = {
            **binding_values,
            "binding_fingerprint": _json_fingerprint(binding_values),
        }
        return QcApplyPreviewResponse(basis=basis, **plan)

    @app.post("/api/qc/apply")
    def qc_apply(body: QcApplyRequest) -> JSONResponse:
        """Apply accepted findings' validated ops as ONE undoable version.

        The result must match the current version index and deterministic
        document fingerprint; stale results are rejected before any dry-run.
        Each selected finding is then re-dry-run and the accepted set commits
        atomically. When ``preview_basis`` is supplied, the original preview
        is recomputed and the submitted ids must exactly match its safe set.
        Rejected (409) while a model turn streams.
        """
        lease = sessions.get_workspace()
        if not _mutation_lease_matches(lease, body):
            return _stale_tutorial_response()
        session = lease.session
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
                            "Final QC is stale because the document, another "
                            "review input, or the review configuration "
                            "changed; re-run it before applying fixes."
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

        def preview_binding_error(code: str, message: str) -> JSONResponse:
            return _coded_error_response(
                {"ok": False, "code": code, "error": message},
                status_code=409,
            )

        if body.preview_basis is not None:
            basis = body.preview_basis
            binding_values = basis.model_dump(
                exclude={"binding_fingerprint"}
            )
            if (
                basis.binding_fingerprint != _json_fingerprint(binding_values)
                or basis.selected_finding_ids
                != list(dict.fromkeys(basis.selected_finding_ids))
            ):
                return preview_binding_error(
                    "qc_preview_binding_invalid",
                    "The Final QC preview binding was changed or is invalid; "
                    "request a fresh preview.",
                )

            current_document_fingerprint = qc_version_fingerprint(working)
            if (
                basis.workspace_id != lease.workspace_id
                or basis.generation != generation
                or basis.run_id != result.run_id
                or basis.input_fingerprint != result.input_fingerprint
                or basis.document_version != version_index
                or basis.document_fingerprint
                != current_document_fingerprint
                or basis.result_fingerprint
                != _json_fingerprint(result_record)
            ):
                return preview_binding_error(
                    "qc_preview_stale",
                    "The session, document, or Final QC result no longer "
                    "matches the confirmed preview; request a fresh preview.",
                )

            confirmed_plan = _qc_apply_preview_plan(
                result,
                working,
                basis.selected_finding_ids,
                candidate_validator=lambda candidate: (
                    session.validate_source_backed_candidate(
                        candidate,
                        current=working,
                    )
                ),
            )
            if body.finding_ids != confirmed_plan["applyable_finding_ids"]:
                return preview_binding_error(
                    "qc_preview_selection_mismatch",
                    "The submitted Final QC fixes do not exactly match the "
                    "safe set from the confirmed preview; request a fresh "
                    "preview.",
                )

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
            return _coded_error_response(
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
        (
            combined_ops,
            applied_ids,
            stale_errors,
            _per_finding_operation_counts,
            _validated_candidate,
        ) = _dry_run_qc_apply_findings(working, eligible_findings)
        for finding_id in applied_ids:
            outcomes[finding_id] = "applied"
        for finding_id in stale_errors:
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
                    or not _qc_matches_current_inputs(
                        session, result, block=True
                    )
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
                # Frozen inside the same guard that committed it: the reply
                # has to describe the version this apply produced, not one a
                # turn landed while the response was being assembled. The
                # lease rides in from outside — see ``_doc_payload``.
                applied_payload = _doc_payload(session, workspace=lease)
        else:
            # No document mutation occurred, but outcome events still belong
            # only to the exact result/version validated above.
            with session.session_state_guard():
                if (
                    session.qc.status == "running"
                    or session.qc.is_settling
                    or not _qc_result_is_audit_complete(result)
                    or not _qc_matches_current_inputs(
                        session, result, block=True
                    )
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
                applied_payload = _doc_payload(session, workspace=lease)

        outcome_counts: dict[str, int] = {}
        for outcome in outcomes.values():
            outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
        _trace_capture.app_event(
            "qc_apply",
            requested=len(body.finding_ids),
            outcomes=outcome_counts,
            finding_ids=sorted(outcomes)[:20],
        )
        return JSONResponse(
            {"ok": True, "outcomes": outcomes, **applied_payload}
        )

    @app.post("/api/qc/dismiss")
    def qc_dismiss(body: QcDismissRequest) -> JSONResponse:
        lease = sessions.get_workspace()
        if not _mutation_lease_matches(lease, body):
            return _stale_tutorial_response()
        session = lease.session
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
            qc_payload = _qc_snapshot_payload(session)
        _trace_capture.app_event("qc_dismiss", finding_id=body.finding_id)
        return JSONResponse({"ok": True, "qc": qc_payload})

    @app.get("/api/qc/export")
    def qc_export(run_id: str = "") -> Response:
        # Deliberately NOT settled behind the capability sweep: downloading
        # an already-paid report must answer promptly. A pending sweep is
        # disclosed inside the export instead (see _qc_export_current_state).
        session = sessions.get_session()
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
        _trace_capture.app_event("export", kind="qc_docx", stale=stale, ok=True)
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
        """Machine-readable twin of the detailed Word Final QC report.

        Like the Word route above, this never waits for the capability
        sweep — a pending verification is disclosed in ``current_state``.
        """
        session = sessions.get_session()
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
        _trace_capture.app_event("export", kind="qc_json", ok=True)
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
        permanent, exact record. ``context`` (the conversation-size gauge)
        rides the same payload — see :func:`_usage_payload`.
        """
        return _usage_payload(sessions.get_session())

    # --- Project save / resume --------------------------------------------

    @app.get("/api/project/save")
    def project_save(scope: str | None = None) -> Response:
        workspace = sessions.get_workspace()
        if workspace.scope != "original" and scope != "tutorial":
            return _coded_error_response(
                {
                    "ok": False,
                    "code": "tutorial_active",
                    "error": (
                        "A tutorial workspace is active. Use Save in the panel to "
                        "download the tutorial copy, or end the tour to return to "
                        "your project and save that."
                    ),
                },
                status_code=409,
            )
        try:
            session = (
                sessions.workspace_manager().tutorial_for_save()
                if workspace.scope != "original"
                else workspace.session
            )
        except sessions.WorkspaceConflictError as exc:
            return JSONResponse(
                {"ok": False, "error": str(exc)}, status_code=409
            )
        try:
            with session.session_state_guard():
                payload = sessions.project_package_bytes(session)
                filename = sessions.project_default_filename(session)
        except ProjectPackageError as exc:
            return JSONResponse(
                {"ok": False, "error": str(exc)}, status_code=409
            )
        _trace_capture.app_event(
            "project_save",
            scope=scope or workspace.scope,
            bytes=len(payload),
            filename=filename,
        )
        return Response(
            content=payload,
            media_type=PACKAGE_MEDIA_TYPE,
            headers=_attachment_headers(filename),
        )

    @app.post("/api/project/load")
    def project_load(body: dict[str, Any]) -> JSONResponse:
        """Legacy format-1 JSON load (source-less compatibility endpoint)."""
        workspace = sessions.get_workspace()
        if workspace.scope != "original":
            return _coded_error_response(
                {
                    "ok": False,
                    "code": "tutorial_active",
                    "error": "End the tour and return to your project before opening another one.",
                },
                status_code=409,
            )
        session = workspace.session
        try:
            with session.session_state_guard():
                load_project(body, session)
                # JSON has no binary source member. Never retain or claim a
                # map that cannot be checked against exact source bytes.
                session.source_docx_map = None
                session.source_patch_context = None
        except ValueError as exc:
            _trace_capture.app_event(
                "project_load", mode="legacy_json", ok=False, error=str(exc)
            )
            return JSONResponse(
                {"ok": False, "error": str(exc)}, status_code=400
            )
        _trace_capture.app_event("project_load", mode="legacy_json", ok=True)
        return JSONResponse(
            {
                "ok": True,
                "chat": chat_transcript(session.history),
                **_doc_payload(session, workspace=workspace),
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
        entry_lease = sessions.get_workspace()
        if entry_lease.scope != "original":
            return _coded_error_response(
                {
                    "ok": False,
                    "code": "tutorial_active",
                    "error": "End the tour and return to your project before opening another one.",
                },
                status_code=409,
            )
        entry_generation = entry_lease.session.generation
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
            _trace_capture.app_event(
                "project_load", mode="package", ok=False, error=str(exc)
            )
            return JSONResponse(
                {"ok": False, "error": str(exc)}, status_code=413
            )
        except (ProjectPackageError, ValueError) as exc:
            _trace_capture.app_event(
                "project_load", mode="package", ok=False, error=str(exc)
            )
            return JSONResponse(
                {"ok": False, "error": str(exc)}, status_code=400
            )

        # The same semantic payload was fully staged above, so these writes
        # are the commit point. A rejected package never reaches them.
        session = entry_lease.session
        with session.session_state_guard():
            try:
                sessions.workspace_manager().assert_active(entry_lease)
            except sessions.WorkspaceConflictError:
                return _coded_error_response(
                    {
                        "ok": False,
                        "code": "stale_workspace",
                        "error": "The workspace changed while the project was being read.",
                    },
                    status_code=409,
                )
            if session.generation != entry_generation:
                return _coded_error_response(
                    {
                        "ok": False,
                        "code": "stale_workspace",
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
        _trace_capture.app_event(
            "project_load",
            mode="package",
            ok=True,
            source_retained=parsed.source_docx_bytes is not None,
        )
        # Same reason as the import response: a source-backed project pays for
        # the first capability sweep here, which must not run on the loop.
        payload = await run_in_threadpool(
            _doc_payload, session, workspace=entry_lease
        )
        return JSONResponse(
            {
                "ok": True,
                "chat": chat_transcript(session.history),
                **payload,
            }
        )

    # --- Developer tools / diagnostics --------------------------------------
    #
    # The read-only surface behind Settings → Developer tools. All routes are
    # plain ``def`` (file I/O belongs on a worker thread, never the event
    # loop) and ``include_in_schema=False`` (the trace-viewer precedent —
    # forensic plumbing, not product API).

    @app.get("/api/diagnostics", include_in_schema=False)
    def diagnostics_snapshot() -> JSONResponse:
        """Environment + session snapshot (scrubbed; key masked, never raw)."""
        return JSONResponse({"ok": True, **diagnostics.snapshot()})

    @app.get("/api/diagnostics/log", include_in_schema=False)
    def diagnostics_log(tail: int = 500) -> JSONResponse:
        """Tail of the activity log (bounded read; grace when disabled)."""
        return JSONResponse({"ok": True, **diagnostics.tail_log(tail)})

    @app.get("/api/diagnostics/traces", include_in_schema=False)
    def diagnostics_traces() -> JSONResponse:
        """Trace-run inventory, newest first (sizes only)."""
        return JSONResponse({"ok": True, **diagnostics.list_trace_runs()})

    @app.get("/api/diagnostics/activity", include_in_schema=False)
    def diagnostics_activity(tail: int = 200) -> JSONResponse:
        """Recent trace events + open spans of the current run."""
        return JSONResponse(
            {"ok": True, **diagnostics.read_recent_trace_events(tail)}
        )

    @app.get("/api/diagnostics/bundle", include_in_schema=False)
    def diagnostics_bundle() -> FileResponse:
        """Download the bounded, manifest-described local support bundle.

        Includes a point-in-time snapshot, this launch's bounded log rotations,
        the current trace through a flush barrier, bounded tails from up to
        three completed prior runs, an inclusion/truncation manifest, and a
        time-ordered incident index. Live sibling runs are never copied. It
        contains draft text and prompts by design (the trace posture — that is what
        makes it useful); the modal copy says so before this link. Credential
        shapes are redacted from snapshots, prompts, normal log messages, and
        exception text. Streamed from a temp file — a deep-trace run can be
        hundreds of MB and an in-memory zip would spike the desktop process
        exactly when the user needs it least.
        """
        path, filename = diagnostics.build_bundle()
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        _trace_capture.app_event(
            "export", kind="diagnostics_bundle", bytes=size, ok=True
        )
        return FileResponse(
            path,
            media_type="application/zip",
            headers={
                **_attachment_headers(filename),
                # Same posture as /api/import/original: a diagnostics bundle
                # holds project content and must not be cached outside it.
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
            background=BackgroundTask(diagnostics.unlink_quietly, path),
        )

    @app.post("/api/diagnostics/client-event", include_in_schema=False)
    def diagnostics_client_event(body: ClientEventRequest) -> JSONResponse:
        """Frontend error collector (window.onerror / console.error shim).

        The client throttles and dedupes; this side just bounds, logs, and
        traces. A broken collector must never break the app, so the shape
        is deliberately forgiving — only oversized payloads are rejected.
        """
        total = len(body.kind) + len(body.message) + len(body.stack) + len(
            body.source
        )
        if total > 32_000:
            return JSONResponse(
                {"ok": False, "error": "Client event too large."},
                status_code=400,
            )
        kind = body.kind if body.kind in _CLIENT_EVENT_KINDS else "error"
        message = body.message[:2000]
        client_log = logging.getLogger("buildaspec.client")
        level = logging.WARNING if kind == "console.warn" else logging.ERROR
        if body.source and body.stack:
            client_log.log(
                level, "client %s at %s: %s\n%s",
                kind, body.source[:300], message, body.stack[:4000],
            )
        elif body.stack:
            client_log.log(level, "client %s: %s\n%s", kind, message, body.stack[:4000])
        else:
            client_log.log(level, "client %s: %s", kind, message)
        _trace_capture.app_event(
            "client_error",
            kind=kind,
            message=message,
            stack=body.stack[:4000],
            source=body.source[:300],
        )
        return JSONResponse({"ok": True})

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

    # --- Release notes ("what's new") ---------------------------------------

    @app.get("/api/release-notes")
    def release_notes_get(all: bool = False) -> dict:
        """The release notes this user has not been shown yet.

        ``all=true`` returns the current version's entry regardless of what
        has been seen — that is the Settings "What's new" button, which must
        work on demand. The default is the launch check: ``pending`` drives
        the one-time modal after an update.
        """
        from . import release_notes, updates

        state = updates.load_state(updates.default_state_path())
        seen = updates.last_seen_version(state)
        if all:
            entries = release_notes.notes_between(
                after="", through=settings.VERSION
            )
            pending = False
        else:
            entries = release_notes.resolve_pending(
                current=settings.VERSION,
                last_seen=seen,
                ran_before=bool(getattr(app.state, "ran_before", False)),
            )
            pending = bool(entries)
        return {
            "ok": True,
            "current": settings.VERSION,
            "last_seen": seen,
            "pending": pending,
            "entries": [note.to_dict() for note in entries],
        }

    @app.post("/api/release-notes/seen")
    def release_notes_seen() -> dict:
        """Record that the current version's notes have been shown.

        Called when the user dismisses the What's-new modal, so it opens
        once per update rather than every launch.
        """
        from . import updates

        path = updates.default_state_path()
        state = updates.load_state(path)
        updates.mark_version_seen(state, settings.VERSION)
        updates.save_state(path, state)
        _trace_capture.app_event(
            "release_notes", action="seen", version=settings.VERSION
        )
        return {"ok": True, "last_seen": settings.VERSION}

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
        _trace_capture.app_event(
            "update", action="check", status=payload["status"], forced=force
        )
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
            _trace_capture.app_event(
                "update", action="install", ok=False, error=str(exc)
            )
            return JSONResponse(
                {"ok": False, "error": str(exc)}, status_code=502
            )
        _trace_capture.app_event(
            "update", action="install", ok=True, version=result.info.version
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

    # The trace run dir exists from boot, not first turn — a launch that
    # crashes before any chat still leaves a run to inspect.
    if _record_start_event:
        _trace_capture.app_event(
            "server_started",
            version=settings.VERSION,
            port=(
                desktop_security.bound_port
                if desktop_security is not None
                else settings.PORT
            ),
            desktop_security=desktop_security is not None,
            frozen=bool(getattr(sys, "frozen", False)),
            dev_mode=settings.dev_mode(),
        )
    return app


# The importable ASGI surface is created for uvicorn/embedding, not necessarily
# served. Explicit create_app() and the secure desktop launcher record the
# actual server start instead of leaving a false boot event at import time.
app = create_app(_record_start_event=False)
