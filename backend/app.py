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
- ``GET  /api/project/save``  → project file (history + doc versions) as a
  JSON download.
- ``POST /api/project/load``  → restore a session from a project file.

When ``frontend/dist`` exists (production / packaged), it is served at
``/``; in development the Vite dev server proxies ``/api`` here instead.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterator
from urllib.parse import quote

from fastapi import FastAPI
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
from .llm.client import reset_client_cache
from .llm.conversation import stream_user_turn
from .spec_doc import open_questions
from .spec_doc.docx_export import build_docx, export_filename
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
    return {
        "doc": session.doc.snapshot(),
        "open_questions": open_questions(session.doc.doc),
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
        return {
            "status": "ok",
            "app": settings.APP_NAME,
            "version": settings.VERSION,
            "model": settings.INTERVIEW_MODEL,
            "api_key_present": bool(load_api_key()),
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
        payload = build_docx(session.doc.doc)
        filename = export_filename(session.doc.doc)
        return Response(
            content=payload,
            media_type=(
                "application/vnd.openxmlformats-officedocument"
                ".wordprocessingml.document"
            ),
            headers=_attachment_headers(filename),
        )

    # --- Project save / resume --------------------------------------------

    @app.get("/api/project/save")
    def project_save() -> Response:
        session = sessions.get_session()
        payload = save_project(session.history, session.doc)
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
