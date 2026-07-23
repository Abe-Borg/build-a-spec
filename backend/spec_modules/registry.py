"""Spec-module registry: the single source of truth for selectable modules.

Ported from Claude-Spec-Critic ``src/modules/registry.py`` — same pattern:
the registry is validated at import (:func:`validate_module_registry`) so an
inconsistent module definition fails at startup, never mid-session, and
:func:`get_module` is the one resolver with a degrade-to-default posture
(``None`` / empty / unknown ids resolve to :data:`DEFAULT_MODULE`), which is
what keeps project files written by other builds loading cleanly.
"""
from __future__ import annotations

from .base import SpecModule, validate_module_registry
from .general import GENERAL
from .hyperscale_fire import HYPERSCALE_FIRE

# The domain-neutral GENERAL module leads and is the default: a fresh session
# authors any CSI section in any discipline, not a fixed catalog. Specialized
# modules (HYPERSCALE_FIRE) follow and are selectable for their deep basis,
# playbook, and research.
_ALL_MODULES: tuple[SpecModule, ...] = (GENERAL, HYPERSCALE_FIRE)

validate_module_registry(_ALL_MODULES)

AVAILABLE_MODULES: dict[str, SpecModule] = {
    module.module_id: module for module in _ALL_MODULES
}

DEFAULT_MODULE: SpecModule = GENERAL


def get_module(module_id: str | None) -> SpecModule:
    """Resolve ``module_id`` to a :class:`SpecModule`, defaulting safely."""
    if not module_id:
        return DEFAULT_MODULE
    return AVAILABLE_MODULES.get(module_id.strip(), DEFAULT_MODULE)
