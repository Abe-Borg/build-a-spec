"""Session store.

Phase 1 is a single-user local app with one active conversation, so this is
one module-level session behind a tiny accessor. Multi-project sessions and
on-disk persistence (the JSON project file) arrive with the document model.
"""
from __future__ import annotations

from .llm.conversation import SessionState

_session = SessionState()


def get_session() -> SessionState:
    return _session


def reset_session() -> None:
    _session.reset()
