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

import logging
import os
import sys
from pathlib import Path

APP_NAME = "Build-a-Spec"
VERSION = "1.9.1"

# --- Models -----------------------------------------------------------------

MODEL_SONNET_5 = "claude-sonnet-5"
MODEL_OPUS_48 = "claude-opus-4-8"
MODEL_FABLE_5 = "claude-fable-5"
# "Final QC" runs on Opus 5 — the one place a model other than Sonnet 5
# appears (frozen decision 2026-07-21, model superseded 2026-07-28: Fable 5
# → Opus 5 at half the token rate, for a review pass Opus 5 is explicitly
# strong at). Thinking is on by default on Opus 5; requests state adaptive
# thinking + an effort level, never a manual budget (a manual budget 400s).
MODEL_OPUS_5 = "claude-opus-5"

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


_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


def _bool_env(name: str, default: bool) -> bool:
    """Read an on/off knob, keeping ``default`` for anything unrecognized."""
    value = os.environ.get(name, "").strip().lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    return default


# The model's own output ceiling (Sonnet 5: 128k output tokens, thinking
# included) — a "limit" at the model maximum is no app limit at all.
MODEL_MAX_OUTPUT_TOKENS = 128_000

# Sonnet 5's context window (VERIFIED 2026-07 against the claude-api
# reference): the denominator for the session context meter. The default is
# a model fact, not a tuning knob — the env override exists ONLY to pair
# with a BUILD_A_SPEC_INTERVIEW_MODEL override whose window differs (e.g.
# Haiku 4.5 is 200k).
MODEL_CONTEXT_WINDOW = _int_env("BUILD_A_SPEC_CONTEXT_WINDOW", 1_000_000)

INTERVIEW_MAX_TOKENS = _int_env(
    "BUILD_A_SPEC_MAX_TOKENS", MODEL_MAX_OUTPUT_TOKENS
)

# --- Adaptive thinking / effort ---------------------------------------------

# Sonnet 5 runs adaptive thinking by default; requests state it explicitly
# (``thinking: {type: "adaptive"}``) plus an effort level via
# ``output_config``. Interview turns default to "high" — the model's own
# default: deep on complex work without stalling an interactive chat.
# Research passes are background work and default to "high" (dialed back
# 2026-07-28 from "xhigh" — cost/quality tradeoff, confirmed with Abraham).
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
RESEARCH_EFFORT = _effort_env("BUILD_A_SPEC_RESEARCH_EFFORT", "high")

# --- Final QC (the pre-issue review pass, on Opus 5) -------------------------

# The one model other than Sonnet 5 in the app (frozen decision). A
# user-triggered lens fan-out + adversarial verification pass before a
# section goes out the door. Opus 5 runs adaptive thinking by default;
# depth is set via output_config effort.
#
# Effort is "high" (2026-07-28, was "xhigh"): a run fans out to ~40 calls —
# five lenses plus two or three verifier seats per finding — so xhigh's extra
# reasoning depth compounded across the whole fan-out, and thinking bills as
# output. Same reasoning that dialed RESEARCH_EFFORT back at four calls.
QC_MODEL = os.environ.get("BUILD_A_SPEC_QC_MODEL", "").strip() or MODEL_OPUS_5
QC_MAX_TOKENS = _int_env("BUILD_A_SPEC_QC_MAX_TOKENS", MODEL_MAX_OUTPUT_TOKENS)
QC_EFFORT = _effort_env("BUILD_A_SPEC_QC_EFFORT", "high")

# Concurrent streaming calls in flight across a QC fan-out (lenses share the
# pool with verifiers). Phase 2 is ~35 of a run's ~40 calls, so this is what
# sets its wall clock. Opus 5 draws on its own rate-limit bucket rather than
# the Opus 4.x pool, so raise this only against measured ITPM/OTPM headroom.
QC_MAX_WORKERS = max(1, _int_env("BUILD_A_SPEC_QC_MAX_WORKERS", 8))

# Adversarial verification panel sizes. Medium/low findings face
# QC_VERIFIERS_STANDARD refuters; critical/high face QC_VERIFIERS_CRITICAL.
# Adjudication is final-qc/4 (see backend/qc/engine.VERIFICATION_RULE_V4):
# a UNANIMOUS panel upholds, a majority refutation refutes, and any other
# split is `disputed` and escalates to a human. Raising a panel size
# therefore increases scrutiny — under the old strict-majority rule the
# extra critical seat bought leniency instead (2-of-3 passed where a
# 2-seat panel needed 2-of-2).
QC_VERIFIERS_STANDARD = _int_env("BUILD_A_SPEC_QC_VERIFIERS_STANDARD", 2)
QC_VERIFIERS_CRITICAL = _int_env("BUILD_A_SPEC_QC_VERIFIERS_CRITICAL", 3)

# Cross-lens candidate consolidation (Chunk 5.2): near-duplicate findings
# raised by different lenses about the SAME defect at the same element share
# one verifier panel instead of buying one each. Off means every raw candidate
# gets its own panel — the pre-5.2 behaviour, and the deterministic fallback
# every failure path already lands on, so disabling it can only cost money,
# never correctness. The flag is recorded in the QC input manifest, so a
# report always states which regime produced it.
QC_CONSOLIDATION = _bool_env("BUILD_A_SPEC_QC_CONSOLIDATION", True)
# A runaway guard on ONE grouping call's input, not a quality limit. A bucket
# past this size falls back to singletons with the reason recorded in the
# audit record (never silently truncated), because asking one call to
# partition an enormous candidate set is where a grouping mistake stops being
# recoverable by the strict validator.
QC_CONSOLIDATION_MAX_BUCKET = max(
    2, _int_env("BUILD_A_SPEC_QC_CONSOLIDATION_MAX_BUCKET", 25)
)

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
# Cache read is 0.1× input. Cache WRITE is per-TTL and this table carries
# both rates: ``cache_write`` is the 5-minute ephemeral entry at 1.25× input,
# ``cache_write_1h`` the one-hour entry at 2.0× input (VERIFIED 2026-07 —
# the 1h entry lives longer, so it costs more to create). The provider
# reports the one-hour subtotal INSIDE the cache-creation total, so the two
# rates apply to disjoint slices (``usage_ledger.estimate_usage_cost``);
# charging the subtotal at both rates would double-bill it. Final QC's
# verifier requests are the app's only one-hour writes today (v1.8.0), and
# Chunk 4.2 puts the interview on them too.
#
# Opus 5 ($5/$25) is the Final-QC model; Fable 5 ($10/$50) is retained
# because BUILD_A_SPEC_QC_MODEL can still select it. Web search bills
# $10 / 1,000 requests ($0.01 each); web fetch has no per-request fee (token
# cost only). Keep this current when Anthropic's list pricing moves.
#
# A model absent from this table is metered at MODEL_SONNET_5's rates
# (``usage_ledger._rates``) — every QC dollar figure would silently
# under-report, so a new QC model MUST land here in the same change.
PRICING: dict[str, dict[str, float]] = {
    MODEL_SONNET_5: {
        "input": 3.0 / 1_000_000,
        "output": 15.0 / 1_000_000,
        "cache_read": 0.30 / 1_000_000,
        "cache_write": 3.75 / 1_000_000,
        "cache_write_1h": 6.00 / 1_000_000,
    },
    MODEL_OPUS_48: {
        "input": 5.0 / 1_000_000,
        "output": 25.0 / 1_000_000,
        "cache_read": 0.50 / 1_000_000,
        "cache_write": 6.25 / 1_000_000,
        "cache_write_1h": 10.00 / 1_000_000,
    },
    MODEL_FABLE_5: {
        "input": 10.0 / 1_000_000,
        "output": 50.0 / 1_000_000,
        "cache_read": 1.00 / 1_000_000,
        "cache_write": 12.50 / 1_000_000,
        "cache_write_1h": 20.00 / 1_000_000,
    },
    MODEL_OPUS_5: {
        "input": 5.0 / 1_000_000,
        "output": 25.0 / 1_000_000,
        "cache_read": 0.50 / 1_000_000,
        "cache_write": 6.25 / 1_000_000,
        "cache_write_1h": 10.00 / 1_000_000,
    },
}

# Per-request cost of a server-side web search ($10 / 1,000). Web fetch has
# no separate per-request charge — only the tokens it returns.
WEB_SEARCH_COST = 10.0 / 1_000

# --- Prompt cache -----------------------------------------------------------

# The TTL every breakpoint in a chat request is written at. One hour by
# default: an interview turn is a person reading a drafted provision and
# typing a reply, which routinely exceeds the 5-minute default, and a lapsed
# entry is re-WRITTEN at full price rather than read at 0.1x. A 1h entry
# costs 2.0x input to create against 1.25x for 5m, so it breaks even after
# ~3 reads instead of ~2 — a trade the app's turn pacing wins easily.
#
# Mixed TTLs impose a provider ordering constraint — longer-lived entries
# must precede shorter-lived ones in tools -> system -> messages order —
# and violating it is a nonretryable 400, not a degraded cache. This module
# makes that violation unbuildable rather than merely avoided: the ONLY
# breakpoint allowed to differ is the request tail, and it is pinned to the
# SHORTEST supported TTL, so it can never precede a longer-lived one.
#
# SUPPORTED_CACHE_TTLS is ordered shortest-first and that order is load
# bearing (``_cache_ttl_rank``). A new TTL must be inserted in the right
# place, not appended.
SUPPORTED_CACHE_TTLS = ("5m", "1h")
CHAT_CACHE_TTL_DEFAULT = "1h"

# The request tail covers the fresh PROJECT CONTEXT and the user's text —
# bytes that commit strips, so no LATER turn can ever read this entry. Its
# only readers are continuation rounds inside the same turn, seconds apart.
# A one-hour lifetime buys nothing there and costs 2.0x input to write
# against 1.25x, on a block the size of the whole document. Deliberately
# NOT env-overridable: a knob here would let an operator put a long-lived
# tail after a short-lived system block, which is exactly the 400 the
# shortest-TTL pin exists to make impossible.
CHAT_TAIL_CACHE_TTL = SUPPORTED_CACHE_TTLS[0]


def _cache_ttl_rank(ttl: str) -> int:
    """Sort key for TTL lifetime; unknown values sort shortest.

    Ranking an unrecognized TTL as shortest is the safe direction: it can
    only make a request look MORE ordering-violating to the guard, never
    less.
    """
    try:
        return SUPPORTED_CACHE_TTLS.index(ttl)
    except ValueError:
        return -1


def _cache_ttl_env(name: str, default: str) -> str:
    """A provider-supported TTL, or the default with a loud complaint.

    An unsupported TTL is rejected by the API on every request, so silently
    passing one through would take chat down entirely. Degrading to the
    default keeps the app working; the warning is what tells the operator
    their override did nothing.
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    if raw in SUPPORTED_CACHE_TTLS:
        return raw
    logging.getLogger("buildaspec.settings").warning(
        "%s=%r is not a supported prompt-cache TTL (%s); using %r.",
        name,
        raw,
        ", ".join(SUPPORTED_CACHE_TTLS),
        default,
    )
    return default


CHAT_CACHE_TTL = _cache_ttl_env(
    "BUILD_A_SPEC_CHAT_CACHE_TTL", CHAT_CACHE_TTL_DEFAULT
)

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
