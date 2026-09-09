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
VERSION = "1.17.0"

# --- Models -----------------------------------------------------------------

MODEL_SONNET_5 = "claude-sonnet-5"
# Opus 4.8 is not a default anywhere; it is reachable only through the model
# env overrides (BUILD_A_SPEC_QC_MODEL / _RESEARCH_MODEL / _INTERVIEW_MODEL)
# and stays in PRICING and the strict-capable model list so an override on
# it is priced and its output tools stay strict (a model in one table and
# not the other is metered at the wrong rate or degrades to lenient tools).
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


# --- Startup log buffer -----------------------------------------------------
#
# This module is imported before ``diagnostics.init_logging()`` has attached
# the activity-log handler (``main.py`` imports settings at the top; the
# handler is attached inside ``main()``), so a warning emitted here — an
# unparseable knob, a below-floor clamp, an unsupported cache TTL — would
# otherwise reach only ``logging.lastResort``: stderr, which the windowed
# build points at devnull or has not even created yet. The buffer holds those
# records until ``init_logging`` replays them through ``flush_startup_log``,
# each with its original timestamp. While NO handler is configured anywhere,
# a record is ALSO mirrored through lastResort, so a script that imports
# settings and never initializes logging (a dev shell, the packaging tools)
# still sees exactly what it saw before this buffer existed.

_LOGGER_NAME = "buildaspec.settings"
_STARTUP_BUFFER_NAME = "buildaspec.settings.startup-buffer"
_STARTUP_BUFFER_CAP = 64  # bounded by the number of knobs, not by traffic


class _StartupLogBuffer(logging.Handler):
    """Hold this module's records until durable logging exists."""

    def __init__(self) -> None:
        super().__init__(level=logging.NOTSET)
        self.set_name(_STARTUP_BUFFER_NAME)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        if len(self.records) < _STARTUP_BUFFER_CAP:
            self.records.append(record)
        # Nothing configured anywhere: keep today's console behaviour. The
        # stdlib would have handed this record to lastResort had no handler
        # been attached at all. ``sys.stderr`` may be None in a windowed
        # build before ``main._ensure_std_streams`` runs, so never raise.
        root = logging.getLogger()
        if not root.handlers and logging.lastResort is not None:
            try:
                logging.lastResort.handle(record)
            except Exception:  # noqa: BLE001 — a warning must never sink the import
                pass

    def drain(self) -> list[logging.LogRecord]:
        held, self.records = self.records, []
        return held


def _startup_buffers() -> list[logging.Handler]:
    logger = logging.getLogger(_LOGGER_NAME)
    return [h for h in logger.handlers if h.get_name() == _STARTUP_BUFFER_NAME]


def _install_startup_buffer() -> _StartupLogBuffer:
    """Attach ONE buffer, however many times this module is (re)imported.

    Matched by handler NAME, not ``isinstance``: ``importlib.reload`` mints a
    fresh class object, so a buffer left by the previous import would not be
    an instance of the new one and would pile up beside it.
    """
    logger = logging.getLogger(_LOGGER_NAME)
    for stale in _startup_buffers():
        logger.removeHandler(stale)
    buffer = _StartupLogBuffer()
    logger.addHandler(buffer)
    return buffer


def pending_startup_records() -> int:
    """How many records are held for replay (0 once flushed)."""
    return sum(len(getattr(h, "records", ())) for h in _startup_buffers())


def flush_startup_log() -> int:
    """Replay held records into whatever logging is configured NOW.

    Called by ``diagnostics.init_logging()`` once the activity-log handler is
    attached. The buffer is detached FIRST, so a replayed record cannot land
    back in it and later warnings go straight through; each record keeps its
    original ``created`` time, and the file handler's context filter stamps
    it at handle time like any other. Idempotent: a second call returns 0.
    """
    logger = logging.getLogger(_LOGGER_NAME)
    buffers = _startup_buffers()
    for buffer in buffers:
        logger.removeHandler(buffer)
    replayed = 0
    for buffer in buffers:
        drain = getattr(buffer, "drain", None)
        for record in drain() if drain is not None else []:
            logging.getLogger(record.name).handle(record)
            replayed += 1
    return replayed


_install_startup_buffer()


def _int_env(name: str, default: int, *, minimum: int | None = None) -> int:
    """An integer knob, clamped to ``minimum`` with a loud complaint.

    Every numeric setting has a value below which the app stops working
    rather than working differently — a zero-seat verifier panel "upholds"
    every finding it never looked at, a zero token ceiling is a 400 on every
    request, a zero port is not the fixed port Vite proxies to — so a value
    under the floor is corrected to the floor, and an unparseable one falls
    back to the default. Both are logged: silently correcting an override
    leaves an operator believing it took effect (the ``_cache_ttl_env``
    posture). Every call site in this module passes ``minimum``; a test
    walks the file to keep it that way.
    """
    raw = os.environ.get(name, "").strip()
    value = default
    if raw:
        try:
            value = int(raw)
        except ValueError:
            logging.getLogger(_LOGGER_NAME).warning(
                "%s=%r is not an integer; using the default %d.",
                name,
                raw,
                default,
            )
            value = default
    if minimum is not None and value < minimum:
        logging.getLogger(_LOGGER_NAME).warning(
            "%s=%d is below its floor of %d; using %d.",
            name,
            value,
            minimum,
            minimum,
        )
        value = minimum
    return value


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
MODEL_CONTEXT_WINDOW = _int_env("BUILD_A_SPEC_CONTEXT_WINDOW", 1_000_000, minimum=1)

INTERVIEW_MAX_TOKENS = _int_env("BUILD_A_SPEC_MAX_TOKENS", MODEL_MAX_OUTPUT_TOKENS, minimum=1)

# --- SDK transport ------------------------------------------------------------

# The Anthropic SDK's own request retries and timeout, made explicit (they
# were the SDK's silent defaults until Batch 9 of the 2026-09-02 program
# diagnosis; both defaults are the SDK's, VERIFIED against anthropic 1.4.0).
# The SDK retries a request on 429 / 5xx / connection errors BEFORE the app
# ever sees an exception, honoring the provider's retry-after header — which
# the fan-outs' own retry policy (research/retry_policy.py: 3 attempts,
# 5·2^n s) does not. So a research dimension or a QC seat can cost up to
# ``RetryPolicy.max_attempts × (1 + SDK_MAX_RETRIES)`` requests under a
# persistent outage: bounded, deliberate, and the SDK's share is the half
# that behaves well under rate limiting. Zeroing it here would remove that
# etiquette from eight concurrent seats; that is a decision to make on real
# run telemetry, not a default.
SDK_MAX_RETRIES = _int_env("BUILD_A_SPEC_SDK_MAX_RETRIES", 2, minimum=0)

# Read/write/pool timeout per request, in seconds. The CONNECT timeout is
# not this value: llm/client.py keeps the SDK's 5 s connect timeout beside
# it, because a bare number passed to the SDK applies to connecting too,
# and a black-holed connect would then wait this long before the first
# retry. Streaming reads count between chunks, so a long reply is fine.
API_TIMEOUT_SECONDS = _int_env("BUILD_A_SPEC_API_TIMEOUT_SECONDS", 600, minimum=30)

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

# AI template generalization is a bounded, mechanical rewrite: same tree,
# same ids, same unresolved decisions, project-specific wording made
# reusable. The structural contract (``app._template_structure_contract``)
# rejects anything more ambitious, so depth beyond "medium" buys nothing
# the contract would accept. Declared here rather than hardcoded at the call
# site so every model call in the app states its effort the same way.
TEMPLATE_EFFORT = _effort_env("BUILD_A_SPEC_TEMPLATE_EFFORT", "medium")

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
CHAT_MAX_SEARCHES = _int_env("BUILD_A_SPEC_CHAT_MAX_SEARCHES", 8, minimum=1)
CHAT_MAX_FETCHES = _int_env("BUILD_A_SPEC_CHAT_MAX_FETCHES", 4, minimum=1)

# Whether a completed research round / Final QC run auto-sends one debrief
# chat turn (the model briefs the user on the findings and asks whether to
# proceed). A real, billed model turn fires without a direct click, so the
# switch exists for operators who want completions to land silently in the
# panels instead; the debrief endpoints themselves stay callable either way.
AUTO_DEBRIEF = _bool_env("BUILD_A_SPEC_AUTO_DEBRIEF", True)

# --- Research (Phase 4) -----------------------------------------------------

RESEARCH_MODEL = (
    os.environ.get("BUILD_A_SPEC_RESEARCH_MODEL", "").strip()
    or MODEL_SONNET_5
)
RESEARCH_MAX_TOKENS = _int_env("BUILD_A_SPEC_RESEARCH_MAX_TOKENS", MODEL_MAX_OUTPUT_TOKENS, minimum=1)
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
QC_MAX_TOKENS = _int_env("BUILD_A_SPEC_QC_MAX_TOKENS", MODEL_MAX_OUTPUT_TOKENS, minimum=1)
QC_EFFORT = _effort_env("BUILD_A_SPEC_QC_EFFORT", "high")

# Effort is now set PER PHASE, because the two phases are not the same kind of
# work and thinking bills as output at the QC model's output rate.
#
# A lens GENERATES: it reads the whole specification cold and has to decide
# what is wrong with it, so its depth is the review's depth — it stays at
# QC_EFFORT.
#
# A verifier seat ADJUDICATES: it is handed one finding, its rationale, its
# proposed operations and the same document, and answers a bounded question
# about that one claim. Phase 2 is ~90% of a run's calls, so this is where
# reasoning depth compounds hardest and buys least. Default "medium".
#
# BUILD_A_SPEC_QC_EFFORT still moves BOTH (it is each one's fallback), so the
# existing global override keeps working; the two specific knobs override it
# per phase. Both are recorded in the hashed input manifest, so a report
# always states the depth each phase actually ran at.
# Resolution order for the verifier seat, and the order matters: an operator
# who explicitly set BUILD_A_SPEC_QC_EFFORT asked for a depth and must get it,
# including when that depth is BELOW this default. Falling back to a literal
# "medium" would silently raise the verifier above a global "low".
QC_LENS_EFFORT = _effort_env("BUILD_A_SPEC_QC_LENS_EFFORT", QC_EFFORT)
QC_VERIFIER_EFFORT = _effort_env(
    "BUILD_A_SPEC_QC_VERIFIER_EFFORT",
    QC_EFFORT if _effort_env("BUILD_A_SPEC_QC_EFFORT", "") else "medium",
)

# Concurrent streaming calls in flight across a QC fan-out (lenses share the
# pool with verifiers). Phase 2 is ~35 of a run's ~40 calls, so this is what
# sets its wall clock. Opus 5 draws on its own rate-limit bucket rather than
# the Opus 4.x pool, so raise this only against measured ITPM/OTPM headroom.
QC_MAX_WORKERS = _int_env("BUILD_A_SPEC_QC_MAX_WORKERS", 8, minimum=1)

# Adversarial verification panel sizes. Medium/low findings face
# QC_VERIFIERS_STANDARD refuters; critical/high face QC_VERIFIERS_CRITICAL.
# Adjudication is final-qc/4 (see backend/qc/engine.VERIFICATION_RULE_V4):
# a UNANIMOUS panel upholds, a majority refutation refutes, and any other
# split is `disputed` and escalates to a human. Raising a panel size
# therefore increases scrutiny — under the old strict-majority rule the
# extra critical seat bought leniency instead (2-of-3 passed where a
# 2-seat panel needed 2-of-2).
QC_VERIFIERS_STANDARD = _int_env("BUILD_A_SPEC_QC_VERIFIERS_STANDARD", 2, minimum=1)
QC_VERIFIERS_CRITICAL = _int_env("BUILD_A_SPEC_QC_VERIFIERS_CRITICAL", 3, minimum=1)

# --- Batched verification (phase 2 on the Message Batches API) ---------------

# Phase 2 is ~90% of a run's calls and every seat is independent, which is
# exactly the shape the Message Batches API exists for: the same requests at
# 50% of standard token prices. Nothing about the REVIEW changes — same model,
# same per-phase effort, same panel sizes, same prompts, same grounding, same
# v4 adjudication. What changes is transport, and it costs two things:
#
#   1. No streaming, so a seat emits no live activity/search/fetch frames. The
#      Review Room keeps candidate- and seat-level state (queued -> running ->
#      outcome); it loses the per-seat shimmer. Phase 1 still streams.
#   2. Latency is the provider's queue, not ours. Most batches end well inside
#      the window a streamed phase 2 already takes, but the ceiling is higher.
#      QC_BATCH_MAX_WAIT_SECONDS is the runaway guard, not a target: on breach
#      the batch is cancelled and the unfinished seats are recorded as failed
#      (which makes the run partial and blocks readiness — never a silent pass).
#
# Off falls back to the streaming ThreadPoolExecutor path, which is retained
# verbatim and is still what phase 1 uses. The flag is recorded in the hashed
# input manifest, so a report always states which transport produced it.
QC_BATCH_VERIFICATION = _bool_env("BUILD_A_SPEC_QC_BATCH_VERIFICATION", True)
# Poll interval while a verification batch is in flight. Also the granularity
# at which a user Stop is noticed, so it is seconds, not minutes.
QC_BATCH_POLL_SECONDS = _int_env("BUILD_A_SPEC_QC_BATCH_POLL_SECONDS", 5, minimum=1)
# Total wall-clock ceiling across every round of one verification phase.
# Two hours: the provider targets an hour for a whole batch, and a phase can
# need a second round for pause_turn continuations and retries.
QC_BATCH_MAX_WAIT_SECONDS = _int_env("BUILD_A_SPEC_QC_BATCH_MAX_WAIT_SECONDS", 7200, minimum=60)
# Rounds of batch submission within one verification phase. A round exists to
# carry pause_turn continuations and retryable failures forward, and rounds
# are SHARED — round N carries every seat that still needs work — so the
# count needed is the worst single seat's, not the sum. This is a runaway
# breaker on that loop, not a quality limit; the wall-clock ceiling above is
# the guard that normally binds first. A seat cut off here is recorded as a
# FAILED seat (so the run goes partial and readiness stays blocked), never
# dropped from its panel. Note it can bite before a pathological seat has
# spent its full per-seat QC_MAX_CONTINUATIONS x retry budget; that is
# deliberate, and 20 is far above anything a real verifier seat reaches.
QC_BATCH_MAX_ROUNDS = _int_env("BUILD_A_SPEC_QC_BATCH_MAX_ROUNDS", 20, minimum=1)
# After a Stop or the wall-clock ceiling, how long the batched phase may keep
# collecting results the provider has ALREADY produced before disclosing the
# rest as uncollected. Those requests are billed whether or not the app reads
# them, so a bounded settlement recovers real money and real verdicts; the
# cost is that a replacement run, apply, dismiss and export stay locked while
# the attempt settles. The bound covers the cancellation call, the polls and
# the results read together — see ``qc.engine._settle_open_batch``.
QC_BATCH_SETTLE_SECONDS = _int_env(
    "BUILD_A_SPEC_QC_BATCH_SETTLE_SECONDS", 120, minimum=1
)

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
QC_CONSOLIDATION_MAX_BUCKET = _int_env("BUILD_A_SPEC_QC_CONSOLIDATION_MAX_BUCKET", 25, minimum=2)

# Per-call web allowances (runaway guards, not budgets — env-overridable).
# The code-compliance lens gets the big search allowance to check standards'
# actual current content; the other lenses and verifiers get the small one.
QC_MAX_SEARCHES_COMPLIANCE = _int_env("BUILD_A_SPEC_QC_MAX_SEARCHES_COMPLIANCE", 24, minimum=1)
QC_MAX_SEARCHES_LENS = _int_env("BUILD_A_SPEC_QC_MAX_SEARCHES_LENS", 8, minimum=1)
QC_MAX_FETCHES_COMPLIANCE = _int_env("BUILD_A_SPEC_QC_MAX_FETCHES_COMPLIANCE", 8, minimum=1)
QC_MAX_FETCHES_LENS = _int_env("BUILD_A_SPEC_QC_MAX_FETCHES_LENS", 4, minimum=1)

# --- Pricing (WI4 cost meter) -----------------------------------------------

# USD per token unless noted. VERIFIED 2026-08-25 against
# platform.claude.com/docs/en/about-claude/pricing. Sonnet 5 launched with
# $2/$10 per MTok as introductory pricing through 2026-08-31, with a
# scheduled increase to $3/$15 the next day — this table used to price at
# the post-increase rate defensively, so the meter would never under-report
# once it took effect. Anthropic has since confirmed that increase will NOT
# happen: $2/$10 is now the permanent standard rate. Pricing here follows
# suit — the defensive $3/$15 would now OVER-report every dollar figure the
# app shows for Sonnet 5 usage instead of protecting against under-reporting.
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
        "input": 2.0 / 1_000_000,
        "output": 10.0 / 1_000_000,
        "cache_read": 0.20 / 1_000_000,
        "cache_write": 2.50 / 1_000_000,
        "cache_write_1h": 4.00 / 1_000_000,
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

# The Message Batches API prices all token usage at 50% of standard rates
# (VERIFIED 2026-08). Deliberately NOT env-overridable: this is a published
# provider rate, like the PRICING table above, not an operator preference —
# and a meter that could be told the discount is something else would stop
# describing the invoice. Final QC's batched verification phase is the only
# thing that uses it today. Keep it current if Anthropic's discount moves.
BATCH_COST_MULTIPLIER = 0.5

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
    logging.getLogger(_LOGGER_NAME).warning(
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
PORT = _int_env("BUILD_A_SPEC_PORT", 8756, minimum=1)

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
