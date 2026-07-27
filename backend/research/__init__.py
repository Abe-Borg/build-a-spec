"""Grounded requirements research (Phase 4).

The near-verbatim port of Claude-Spec-Critic's requirements-research
fan-out (``src/research/``), with the grounding/retry helpers extracted
from its verification package and a Build-a-Spec-native runner for the
background-thread + SSE lifecycle. See ``engine.py`` for the port notes.
"""
from .engine import (
    RequirementsProfile,
    ResearchFanoutError,
    ResearchItem,
    ResearchRound,
    append_research_round,
    research_context_block,
    run_requirements_research,
)
from .runner import ResearchRunner

__all__ = [
    "RequirementsProfile",
    "ResearchFanoutError",
    "ResearchItem",
    "ResearchRound",
    "ResearchRunner",
    "append_research_round",
    "research_context_block",
    "run_requirements_research",
]
