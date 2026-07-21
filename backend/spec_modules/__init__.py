"""Registry-validated spec modules (Phase 3).

Discipline knowledge lives here — frozen :class:`SpecModule` objects
(section catalog, defaults-first interview playbook, pinned standards
basis, drafting prompt slots, lint vocabulary) picked from a validated
registry. The engine (document model, conversation loop, lint logic)
stays module-neutral. Architecture ported from Claude-Spec-Critic
``src/modules/``.
"""
from .base import (
    InterviewTopic,
    ResearchDimension,
    SectionDef,
    SpecModule,
    validate_module_registry,
)
from .registry import AVAILABLE_MODULES, DEFAULT_MODULE, get_module

__all__ = [
    "AVAILABLE_MODULES",
    "DEFAULT_MODULE",
    "InterviewTopic",
    "ResearchDimension",
    "SectionDef",
    "SpecModule",
    "get_module",
    "validate_module_registry",
]
