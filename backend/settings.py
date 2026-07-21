"""Runtime settings for Build-a-Spec.

Model ids mirror Spec Critic's current stack (``api_config.py`` in the
Claude-Spec-Critic repo): Sonnet 5 for interactive interview/drafting turns.
Every value is env-overridable with the same degrade-gracefully posture as
Spec Critic — a bad value falls back to the default rather than crashing.

Token posture (project decision, 2026-07-21): the app imposes NO quality
limits of its own. ``max_tokens`` defaults sit at the model's output
ceiling; the only caps that remain are runaway circuit breakers (tool-round
and search-budget ceilings) sized so no legitimate turn ever meets them.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "Build-a-Spec"
VERSION = "0.6.0"

# --- Models -----------------------------------------------------------------

MODEL_SONNET_5 = "claude-sonnet-5"
MODEL_OPUS_48 = "claude-opus-4-8"

INTERVIEW_MODEL_DEFAULT = MODEL_SONNET_5
INTERVIEW_MODEL = (
    os.environ.get("BUILD_A_SPEC_INTERVIEW_MODEL", "").strip()
    or INTERVIEW_MODEL_DEFAULT
)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


# The model's own output ceiling (Sonnet 5: 128k output tokens, thinking
# included) — a "limit" at the model maximum is no app limit at all.
MODEL_MAX_OUTPUT_TOKENS = 128_000

INTERVIEW_MAX_TOKENS = _int_env(
    "BUILD_A_SPEC_MAX_TOKENS", MODEL_MAX_OUTPUT_TOKENS
)

# --- Adaptive thinking / effort ---------------------------------------------

# Sonnet 5 runs adaptive thinking by default; requests state it explicitly
# (``thinking: {type: "adaptive"}``) plus an effort level via
# ``output_config``. Interview turns default to "high" — the model's own
# default: deep on complex work without stalling an interactive chat.
# Research passes are background work and default to "xhigh".
EFFORT_LEVELS = ("low", "medium", "high", "max", "xhigh")


def _effort_env(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip().lower()
    return value if value in EFFORT_LEVELS else default


INTERVIEW_EFFORT = _effort_env("BUILD_A_SPEC_INTERVIEW_EFFORT", "high")

# Thinking-summary display. Sonnet 5 defaults to ``omitted`` — thinking
# blocks stream with empty text, so a reasoning-heavy turn looks like a long
# silent pause. ``summarized`` streams a readable summary of the model's
# reasoning through thinking deltas: exactly the "see what the model is
# thinking" liveness signal the streaming UX wants, and billing is identical
# either way. On a model/endpoint that rejects the ``display`` key the engine
# degrades to ``omitted`` at runtime (once, remembered for the process) and
# relies on the ``thinking`` status strip alone.
_DISPLAY_LEVELS = ("summarized", "omitted")


def _display_env(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip().lower()
    return value if value in _DISPLAY_LEVELS else default


THINKING_DISPLAY = _display_env("BUILD_A_SPEC_THINKING_DISPLAY", "summarized")

# --- Interview web lookups ---------------------------------------------------

# Per-request allowances for the interview loop's web_search / web_fetch
# server tools. They renew every continuation round — per-call runaway
# guards, not a session budget.
CHAT_MAX_SEARCHES = _int_env("BUILD_A_SPEC_CHAT_MAX_SEARCHES", 8)
CHAT_MAX_FETCHES = _int_env("BUILD_A_SPEC_CHAT_MAX_FETCHES", 4)

# --- Research (Phase 4) -----------------------------------------------------

RESEARCH_MODEL = (
    os.environ.get("BUILD_A_SPEC_RESEARCH_MODEL", "").strip()
    or MODEL_SONNET_5
)
RESEARCH_MAX_TOKENS = _int_env(
    "BUILD_A_SPEC_RESEARCH_MAX_TOKENS", MODEL_MAX_OUTPUT_TOKENS
)
RESEARCH_EFFORT = _effort_env("BUILD_A_SPEC_RESEARCH_EFFORT", "xhigh")

# --- Server -----------------------------------------------------------------

HOST = "127.0.0.1"
PORT = _int_env("BUILD_A_SPEC_PORT", 8756)

# Vite dev server (used by main.py when BUILD_A_SPEC_DEV=1).
DEV_FRONTEND_URL = "http://localhost:5173"


def dev_mode() -> bool:
    return os.environ.get("BUILD_A_SPEC_DEV", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


# --- Paths ------------------------------------------------------------------


def _resolve_frontend_dist() -> Path:
    """The built frontend, source checkout or frozen app.

    In the PyInstaller build (``packaging/windows/build-a-spec.spec``) the
    Vite output is bundled at ``frontend/dist`` relative to the bundle
    root (``sys._MEIPASS`` for the extracted resources), so the packaged
    app serves the same files the source checkout does.
    """
    if getattr(sys, "frozen", False):
        bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        return bundle_root / "frontend" / "dist"
    return Path(__file__).resolve().parent.parent / "frontend" / "dist"


REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIST = _resolve_frontend_dist()
