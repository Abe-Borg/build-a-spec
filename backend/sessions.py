"""Session store.

Phase 1 is a single-user local app with one active conversation, so this is
one module-level session behind a tiny accessor. Multi-project sessions and
on-disk persistence (the JSON project file) arrive with the document model.
"""
from __future__ import annotations

from typing import Any

from .llm.conversation import SessionState
from .spec_doc.project import save_project

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
    return bool(session.history) or not session.doc.doc.is_empty()


def project_payload(session: SessionState) -> dict[str, Any]:
    """The full save-project dict for ``session``.

    Single source of truth for both the ``/api/project/save`` download and
    the native save-on-close path — same bytes either way.
    """
    research_profile = session.research.profile_result
    return save_project(
        session.history,
        session.doc,
        session.module.module_id,
        requirements_profile=(
            research_profile.to_dict() if research_profile else None
        ),
        audit_result=session.audit.result,
        qc_result=session.qc.result.to_dict() if session.qc.result else None,
    )


def project_default_stem(session: SessionState) -> str:
    """Filename stem for a saved project (``buildaspec-<stem>.json``)."""
    return session.doc.doc.number.replace(" ", "") or "draft"
