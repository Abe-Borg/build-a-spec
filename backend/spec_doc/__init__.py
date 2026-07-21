"""Server-owned SectionFormat document model (Phase 2).

The tree, edit operations, per-turn snapshots, and derived open items live
here; ``backend.llm.conversation`` drives it through the
``apply_spec_edits`` tool and the FastAPI layer exposes snapshots,
undo/redo, ``.docx`` export, and project save/resume.
"""
from .diffing import SectionDiff, diff_sections
from .linting import lint_document
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
    "SectionDiff",
    "SpecEditError",
    "SpecSection",
    "diff_sections",
    "lint_document",
    "open_questions",
    "outline",
]
