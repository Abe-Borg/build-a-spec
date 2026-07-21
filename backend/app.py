"""FastAPI application: SSE chat, document model, key management, static frontend.

Endpoints (all JSON unless noted):

- ``GET  /api/health``        → app/model/key status for the UI header.
- ``POST /api/key``           → save an Anthropic API key (keyring → file).
- ``POST /api/session/reset`` → clear the conversation and the document.
- ``POST /api/chat``          → Server-Sent Events stream of turn events.
- ``GET  /api/doc``           → current document snapshot + open questions.
- ``POST /api/doc/undo``      → step to the previous per-turn version.
- ``POST /api/doc/redo``      → step forward again.
- ``GET  /api/export/docx``   → the section as a SectionFormat ``.docx``
  (with the assumptions schedule), as a download.
- ``POST /api/research/start``  → launch the requirements-research fan-out
  (requires a complete project profile; 409 while one runs).
- ``GET  /api/research/status`` → research state + event log + profile view.
- ``GET  /api/research/stream`` → SSE follow of the active/last run.
- ``GET  /api/project/save``  → project file (history + doc versions +
  research profile) as a JSON download.
- ``POST /api/project/load``  → restore a session from a project file.

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

from fastapi import FastAPI, UploadFile
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
from .api_key_store import load_api_key, save_api_key
from .llm.client import MissingApiKeyError, get_client, reset_client_cache
from .llm.conversation import standards_payload, stream_user_turn
from .project_profile import ProjectProfile
from .spec_doc import lint_document, open_questions
from .spec_doc.docx_export import build_docx, export_filename
from .spec_doc.importer import MasterImportError, parse_master_docx
from .spec_doc.project import chat_transcript, load_project, save_project

_DEV_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


class ChatRequest(BaseModel):
    message: str


class SaveKeyRequest(BaseModel):
    api_key: str


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


def _doc_payload(session) -> dict[str, Any]:
    profile = ProjectProfile.from_dict(session.doc.doc.project_profile)
    return {
        "doc": session.doc.snapshot(),
        "open_questions": open_questions(session.doc.doc),
        "lint": lint_document(session.doc.doc, session.module),
        "standards": standards_payload(session),
        "profile_complete": bool(profile and profile.is_complete()),
        "research_status": session.research.status,
    }


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

    @app.post("/api/session/reset")
    def reset() -> dict:
        sessions.reset_session()
        return {"ok": True}

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

    # --- Document ----------------------------------------------------------

    @app.get("/api/doc")
    def get_doc() -> dict:
        return _doc_payload(sessions.get_session())

    @app.post("/api/doc/undo")
    def undo_doc() -> JSONResponse:
        session = sessions.get_session()
        if not session.doc.undo():
            return JSONResponse(
                {"ok": False, "error": "Nothing to undo."}, status_code=409
            )
        return JSONResponse({"ok": True, **_doc_payload(session)})

    @app.post("/api/doc/redo")
    def redo_doc() -> JSONResponse:
        session = sessions.get_session()
        if not session.doc.redo():
            return JSONResponse(
                {"ok": False, "error": "Nothing to redo."}, status_code=409
            )
        return JSONResponse({"ok": True, **_doc_payload(session)})

    @app.get("/api/export/docx")
    def export_docx() -> Response:
        session = sessions.get_session()
        payload = build_docx(session.doc.doc, audit_result=session.audit.result)
        filename = export_filename(session.doc.doc)
        return Response(
            content=payload,
            media_type=(
                "application/vnd.openxmlformats-officedocument"
                ".wordprocessingml.document"
            ),
            headers=_attachment_headers(filename),
        )

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
        suffix = Path(file.filename or "").suffix.lower()
        if suffix != ".docx":
            return JSONResponse(
                {"ok": False, "error": "Upload a .docx master specification."},
                status_code=400,
            )
        with tempfile.NamedTemporaryFile(
            suffix=".docx", delete=False
        ) as handle:
            handle.write(await file.read())
            temp_path = Path(handle.name)
        try:
            result = parse_master_docx(temp_path)
            session.doc.adopt_imported(result.section)
        except (MasterImportError, ValueError) as exc:
            return JSONResponse(
                {"ok": False, "error": str(exc)}, status_code=400
            )
        finally:
            temp_path.unlink(missing_ok=True)
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
                "warnings": result.warnings,
                "imported_block_count": result.imported_block_count,
                "tracked_changes_detected": result.tracked_changes_detected,
                **_doc_payload(session),
            }
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

    # --- Project save / resume --------------------------------------------

    @app.get("/api/project/save")
    def project_save() -> Response:
        session = sessions.get_session()
        research_profile = session.research.profile_result
        payload = save_project(
            session.history,
            session.doc,
            session.module.module_id,
            requirements_profile=(
                research_profile.to_dict() if research_profile else None
            ),
            audit_result=session.audit.result,
        )
        stem = session.doc.doc.number.replace(" ", "") or "draft"
        return Response(
            content=json.dumps(payload, ensure_ascii=False, indent=2),
            media_type="application/json",
            headers=_attachment_headers(f"buildaspec-{stem}.json"),
        )

    @app.post("/api/project/load")
    def project_load(body: dict[str, Any]) -> JSONResponse:
        session = sessions.get_session()
        try:
            load_project(body, session)
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
