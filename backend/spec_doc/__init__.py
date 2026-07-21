"""Server-owned SectionFormat document model (Phase 2).

The tree, edit operations, per-turn snapshots, and derived open items live
here; ``backend.llm.conversation`` drives it through the
``apply_spec_edits`` tool and the FastAPI layer exposes snapshots,
undo/redo, ``.docx`` export, and project save/resume.
"""
from .model import (
    APPLY_SPEC_EDITS_TOOL,
    DocumentStore,
    SpecEditError,
    SpecSection,
    open_questions,
    outline,
)

__all__ = [
    "APPLY_SPEC_EDITS_TOOL",
    "DocumentStore",
    "SpecEditError",
    "SpecSection",
    "open_questions",
    "outline",
]
