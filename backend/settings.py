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
VERSION = "1.1.0"

# --- Models -----------------------------------------------------------------

MODEL_SONNET_5 = "claude-sonnet-5"
MODEL_OPUS_48 = "claude-opus-4-8"
# Batch 4 "Final QC" runs on Fable 5 — the one place a model other than
# Sonnet 5 appears (frozen decision). Thinking is always-on on Fable 5;
# requests state adaptive thinking + an effort level, never a manual budget.
MODEL_FABLE_5 = "claude-fable-5"

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

# --- Final QC (Batch 4: spare-no-expense pre-issue review on Fable 5) --------

# The one model other than Sonnet 5 in the app (frozen decision). A
# user-triggered lens fan-out + adversarial verification pass before a
# section goes out the door. Fable 5's adaptive thinking is always-on;
# depth is set via output_config effort (default xhigh — quality over cost).
QC_MODEL = os.environ.get("BUILD_A_SPEC_QC_MODEL", "").strip() or MODEL_FABLE_5
QC_MAX_TOKENS = _int_env("BUILD_A_SPEC_QC_MAX_TOKENS", MODEL_MAX_OUTPUT_TOKENS)
QC_EFFORT = _effort_env("BUILD_A_SPEC_QC_EFFORT", "xhigh")

# Adversarial verification panel sizes. Medium/low findings face
# QC_VERIFIERS_STANDARD refuters; critical/high face QC_VERIFIERS_CRITICAL.
# Majority upholds → the finding survives (a tie goes to the refuters —
# that is the point of the pass).
QC_VERIFIERS_STANDARD = _int_env("BUILD_A_SPEC_QC_VERIFIERS_STANDARD", 2)
QC_VERIFIERS_CRITICAL = _int_env("BUILD_A_SPEC_QC_VERIFIERS_CRITICAL", 3)

# Per-call web allowances (runaway guards, not budgets — env-overridable).
# The code-compliance lens gets the big search allowance to check standards'
# actual current content; the other lenses and verifiers get the small one.
QC_MAX_SEARCHES_COMPLIANCE = _int_env("BUILD_A_SPEC_QC_MAX_SEARCHES_COMPLIANCE", 24)
QC_MAX_SEARCHES_LENS = _int_env("BUILD_A_SPEC_QC_MAX_SEARCHES_LENS", 8)
QC_MAX_FETCHES_COMPLIANCE = _int_env("BUILD_A_SPEC_QC_MAX_FETCHES_COMPLIANCE", 8)
QC_MAX_FETCHES_LENS = _int_env("BUILD_A_SPEC_QC_MAX_FETCHES_LENS", 4)

# --- Pricing (WI4 cost meter) -----------------------------------------------

# USD per token unless noted. VERIFIED 2026-07 against the claude-api
# reference (Current Models table) + Anthropic's web-search pricing. Sonnet 5
# lists an intro rate ($2/$10 per MTok through 2026-08-31); we deliberately
# use the POST-intro numbers ($3/$15) so the meter never under-reports.
# Cache read is 0.1× input; cache write (5-minute ephemeral TTL) is 1.25×
# input. Fable 5 ($10/$50) is Batch 4's Final-QC model. Web search bills
# $10 / 1,000 requests ($0.01 each); web fetch has no per-request fee (token
# cost only). Keep this current when Anthropic's list pricing moves.
PRICING: dict[str, dict[str, float]] = {
    MODEL_SONNET_5: {
        "input": 3.0 / 1_000_000,
        "output": 15.0 / 1_000_000,
        "cache_read": 0.30 / 1_000_000,
        "cache_write": 3.75 / 1_000_000,
    },
    MODEL_OPUS_48: {
        "input": 5.0 / 1_000_000,
        "output": 25.0 / 1_000_000,
        "cache_read": 0.50 / 1_000_000,
        "cache_write": 6.25 / 1_000_000,
    },
    MODEL_FABLE_5: {
        "input": 10.0 / 1_000_000,
        "output": 50.0 / 1_000_000,
        "cache_read": 1.00 / 1_000_000,
        "cache_write": 12.50 / 1_000_000,
    },
}

# Per-request cost of a server-side web search ($10 / 1,000). Web fetch has
# no separate per-request charge — only the tokens it returns.
WEB_SEARCH_COST = 10.0 / 1_000

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
