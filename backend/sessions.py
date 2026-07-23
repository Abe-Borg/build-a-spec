"""Session store and portable project serialization.

The local app has one active conversation, so this is one module-level session
behind a tiny accessor. Semantic state is JSON; portable ``.baspec`` files can
also carry the exact imported DOCX as a separate binary member.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .llm.conversation import SessionState
from .spec_doc.project import save_project
from .spec_doc.project_package import build_project_package

_session = SessionState()


def get_session() -> SessionState:
    return _session


def reset_session() -> None:
    _session.reset()


def has_unsaved_progress(session: SessionState) -> bool:
    """True when the session holds work worth saving before it is discarded.

    Any conversation history or any document content counts. Deliberately
    coarse — there is no since-last-save dirty flag, so a fresh, untouched
    session never prompts, and anything else always offers the save.
    """
    return (
        bool(session.history)
        or not session.doc.doc.is_empty()
        or session.import_report is not None
        or session.source_docx_bytes is not None
    )


def _portable_source_attachment(
    session: SessionState,
) -> tuple[bytes | None, str, Any | None]:
    """Return source artifacts only while the imported baseline still exists.

    Undoing before import keeps the redo tail (and therefore its baseline), so
    the attachment remains portable. Committing a new branch truncates that
    tail and ``DocumentStore`` clears ``baseline_index``; at that point source
    bytes/map can no longer be proven against this project and must not be
    written into a package that would reject itself on load.
    """
    baseline_index = session.doc.baseline_index
    if (
        isinstance(baseline_index, bool)
        or not isinstance(baseline_index, int)
        or not 0 <= baseline_index < len(session.doc.versions)
    ):
        return None, "", None
    return (
        session.source_docx_bytes,
        session.source_docx_filename,
        getattr(session, "source_docx_map", None),
    )


def project_payload(session: SessionState) -> dict[str, Any]:
    """The semantic JSON payload embedded in a portable project.

    Legacy JSON loading consumes this shape directly. Native/browser saves
    wrap it through :func:`project_package_bytes` so exact source bytes remain
    a separate member.
    """
    research_profile = session.research.profile_result
    source_bytes, _source_filename, source_docx_map = (
        _portable_source_attachment(session)
    )
    if source_bytes is None:
        source_docx_map = None
    source_map_payload = (
        source_docx_map.to_dict()
        if source_docx_map is not None and hasattr(source_docx_map, "to_dict")
        else source_docx_map
    )
    return save_project(
        session.history,
        session.doc,
        session.module.module_id,
        requirements_profile=(
            research_profile.to_dict() if research_profile else None
        ),
        audit_result=session.audit.result,
        qc_result=session.qc.result.to_dict() if session.qc.result else None,
        discipline=session.discipline,
        figures=session.figures.to_dict(),
        suggested_prompts=list(session.suggested_prompts),
        import_report=session.import_report,
        source_map=source_map_payload,
    )


def project_package_bytes(session: SessionState) -> bytes:
    """Return the portable ``.baspec`` representation of ``session``.

    Single source of truth for both the API download and native save-on-close.
    The exact imported source is a distinct binary ZIP member; it is never
    encoded into or mixed with the semantic project JSON.
    """
    source_bytes, source_filename, _source_docx_map = (
        _portable_source_attachment(session)
    )
    return build_project_package(
        project_payload(session),
        source_docx_bytes=source_bytes,
        source_docx_filename=source_filename,
    )


def project_default_stem(session: SessionState) -> str:
    """Section-derived stem used by the portable project filename."""
    return session.doc.doc.number.replace(" ", "") or "draft"


def project_default_filename(session: SessionState) -> str:
    """Timestamped filename for a saved project.

    ``buildaspec-<stem>-<YYYY-MM-DD-HHMMSS>.baspec`` (UTC). Single source of
    truth for both the ``/api/project/save`` download and the native
    save-on-close path. The time component (not just the date) is
    deliberate: two saves of the same section on the same day still need
    distinct names, or the native Save dialog would default to the prior
    save's filename and risk silently overwriting it.
    """
    stem = project_default_stem(session)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    return f"buildaspec-{stem}-{stamp}.baspec"
