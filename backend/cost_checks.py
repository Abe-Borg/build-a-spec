"""Cost self-checks: runtime watchers that can only switch a saving OFF.

Tier 1 of the research and Final QC cost program built two savings that
shipped off until a measured trial proved them, and that trial is not coming
(the Tier 1 progress file's O6). The Tier 1 finish program (decision FD1,
``docs/plans/tier1-finish/``) replaces the trial with checks that watch the
runs the app makes anyway, and this module holds their state. With the
checks below in place, the continuation tail defaults on (session CT-3).

The first check is the **continuation tail's guard** (session CT-1). The
tail is a top-level ``cache_control`` on a streamed request that resumes a
``pause_turn`` (``settings.CONTINUATION_CACHE``). If the provider ever
refuses a continuation that carries it, with a 400 when the stream opens, the
engine sends the same request once more without it and asks this module to
switch that engine's tail off for the rest of the app session. That turns the
tail's one unbounded failure (every paused research area and compliance
review failing, on every run) into one extra request per engine per app
session. The resend itself is engine code (``_open_stream`` in each engine,
copied rather than shared); this module holds only its latch.

The second is the **continuation tail's value check** (session CT-2). Each
engine hands every response to a request that carried the tail, with its
conversation's opening response, to :func:`observe_continuation`, which
measures what the tail saved where the reported usage can prove it (the
Chunk 4 plan's Appendix A) and counts the rest as unmeasured. Once an
engine has six or more measured observations whose summed saving is below
zero, its tail switches off (``unprofitable``). Every measured term is the
request's exact saving or an upper bound on it, so the rule latches only on
a proven loss.

The third is the **warm lead's check** (session WL-1). The warm lead streams
one seat of a large cache lineage of Final QC's batched verifier seats FIRST,
at list price, and submits the batch only after that seat's first output, so
the batch can read the 1-hour entry the lead wrote instead of each seat
writing its own (``settings.QC_BATCH_WARM_LEAD``). Whether a batch request
can read an entry a streamed request wrote is undocumented. So after every
batched phase that ends normally and sent a lead, the engine hands each such
lineage to :func:`check_warm_leads`, which reads the usage the batch already
reported (the Chunk 3 plan's Appendix B): h₁, the share of the measured
batched seats whose first iteration read the shared prefix; p, the prefix;
C, what the lead cost; and h₀*, the break-even — the lead paid for itself if
and only if the batch, without it, would have read less than h₀* of the
prefix. Two rules can switch the lead off for the rest of the app session:
``not_read`` when h₁ is below one half (a lead the batch reads lifts h₁ close
to 1, because every seat was submitted after the lead's entry was readable),
and ``unprofitable`` when h₀* is at most zero (the lead cost more than it
could have saved even if the batch alone would have read nothing).

There is deliberately **no rule on h₀* above zero.** h₀ — what the batch
would have read without the lead — is never observed: every run that sends a
lead measures only h₁, the read share WITH it, and batch cache hits are
best-effort. A lead that is read but was not needed (the batch would have
read the prefix anyway) is therefore kept; it costs its own batch discount
per large lineage per run, which is the price of not measuring h₀ (the
plan's §2 and B.6). Any rule on h₀* > 0 would have to guess h₀.

Rules, all binding (the tracker's R5, R6, R8, R10):

- **Nothing here sends a request.** A check reads only what the app was
  already sending and receiving; there is no probe of the provider.
- **OFF only, in memory, one per process.** A latch, once set, stays set
  until the process ends, and an app restart re-arms it. Nothing is
  persisted, and nothing reaches a request, a record, a usage total, a
  project file, a project brief or the QC input manifest.
- **A leaf.** The standard library, :mod:`backend.settings`,
  :mod:`backend.usage_ledger` and ``anthropic`` only, so both engines can
  import it (the one exception to copy-don't-import besides the ledger: the
  state is one per process, not one per engine copy). Thread-safe: research
  runs four dimension threads and Final QC up to eight workers, so every read
  and write takes the one module lock.
- **It never raises into a request path.** Every entry point an engine calls
  catches its own exceptions and logs them at DEBUG; a check that fails
  leaves the saving exactly as the switch set it.
- **Its diagnostics survive redaction.** No key contains ``token`` unless an
  ``s`` follows it (``tracing.redaction``'s ``token(?!s)``).

One WARNING on ``buildaspec.cost_checks`` (the activity log) the first time a
latch is set, naming the behavior, the engine and the reason. Nothing is
logged per request; the warm lead's check writes one INFO line per lineage
it judges, numbers only. :func:`snapshot` is what Settings → Developer tools
and a support bundle read, through ``diagnostics.snapshot()``'s top-level
``cost_checks`` block.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, ROUND_UP, Decimal
from typing import Any

import anthropic

from . import settings, usage_ledger

_log = logging.getLogger("buildaspec.cost_checks")

# The engines that send the continuation tail. Separate latches, because the
# two send different models (Sonnet 5 for research, Opus 5.5 for Final QC by
# default), and a refusal is a property of a request shape on one model.
ENGINE_RESEARCH = "research"
ENGINE_QC = "qc"
TAIL_ENGINES = (ENGINE_RESEARCH, ENGINE_QC)

# Why a saving was switched off: a closed vocabulary, which the diagnostics
# block reports verbatim. Each check adds the reason it sets.
REASON_REJECTED = "rejected"  # the provider refused a request carrying it
REASON_UNPROFITABLE = "unprofitable"  # measured, it cost more than it saved
REASON_NOT_READ = "not_read"  # the batch did not read the warm lead's copy
_TAIL_REASONS = frozenset({REASON_REJECTED, REASON_UNPROFITABLE})
_WARM_LEAD_REASONS = frozenset({REASON_NOT_READ, REASON_UNPROFITABLE})

# The value check latches only on at least this many measured observations:
# one odd request cannot decide it, and a losing tail is still caught within
# about one research round (four areas, each pausing a few times).
_TAIL_MIN_OBSERVATIONS = 6

# The token counts one model iteration reports. ``input_tokens`` and
# ``output_tokens`` are always there; a cache count the provider leaves out
# (``None``) was zero.
_REQUIRED_COUNTS = ("input_tokens", "output_tokens")
_CACHE_COUNTS = ("cache_read_input_tokens", "cache_creation_input_tokens")

# The content blocks a response made in ONE model iteration can hold. Any
# other block — a server tool's use or result (``server_tool_use``,
# ``web_search_tool_result`` …), or a block type this code has never seen —
# means a server tool may have run inside the request, so its top-level
# usage may sum several iterations.
_SINGLE_ITERATION_BLOCKS = frozenset(
    {"text", "thinking", "redacted_thinking", "tool_use"}
)
# A server tool's call, which a ``*_tool_result`` block in the same response
# answers.
_SERVER_TOOL_CALLS = frozenset({"server_tool_use", "mcp_tool_use"})

_USD_PLACES = Decimal("0.000001")

# The detail a latch keeps: for a refusal, the error type and the start of
# its message; for a loss, the measurement that proved it.
DETAIL_MAX_CHARS = 200

# A continuation that is too long fails the same way with or without the
# tail, so its 400 is not a verdict on the tail (the thinking-display
# degrade's exclusion in ``llm/conversation.py``, for the same reason).
_PROMPT_TOO_LONG = re.compile(r"prompt is too long", re.IGNORECASE)


@dataclass
class _Latch:
    """One saving's switch-off record. ``reason`` empty means not switched off."""

    reason: str = ""
    detail: str = ""
    since: float | None = None


@dataclass
class _TailValue:
    """What the value check has seen for one engine since the process began.

    ``saving`` is the signed sum of every measured term, in dollars, kept as a
    ``Decimal`` so a sum of exactly zero never reads as a loss through float
    rounding. ``last_observed_at`` is when a response to a request carrying
    the tail was last observed, measured or not.
    """

    exact: int = 0
    bound: int = 0
    unmeasured: int = 0
    saving: Decimal = field(default_factory=Decimal)
    last_observed_at: float | None = None

    @property
    def measured(self) -> int:
        return self.exact + self.bound


_lock = threading.Lock()
_tail_latches: dict[str, _Latch] = {engine: _Latch() for engine in TAIL_ENGINES}
_tail_values: dict[str, _TailValue] = {
    engine: _TailValue() for engine in TAIL_ENGINES
}


def continuation_tail_enabled(engine: str) -> bool:
    """False once a check has switched ``engine``'s continuation tail off.

    Read on every request, after the switch (``settings.CONTINUATION_CACHE``,
    pinned per research round and per Final QC run): the switch decides
    whether the tail is wanted, and this can only take it away, in every
    thread, from the next request on. Never raises; if the check itself fails
    it answers ``True``, which leaves the tail exactly as the switch set it.
    """
    try:
        with _lock:
            return not _tail_latches[engine].reason
    except Exception:  # noqa: BLE001 — a check never fails a request
        _log.debug("continuation tail check failed for %r", engine, exc_info=True)
        return True


def disable_continuation_tail(engine: str, *, reason: str, detail: str = "") -> None:
    """Switch ``engine``'s continuation tail off until the app restarts.

    The first latch wins: a later call — another dimension refused at the same
    moment, say — changes nothing and logs nothing. ``detail`` is clipped to
    :data:`DETAIL_MAX_CHARS` and reaches diagnostics (which scrub it), never a
    record. Never raises: a malformed call is logged at DEBUG and ignored.
    """
    try:
        if reason not in _TAIL_REASONS:
            raise ValueError(f"not a continuation-tail reason: {reason!r}")
        clipped = _clip(detail)
        with _lock:
            latched = _latch_locked(engine, reason, clipped)
        if latched:
            _warn_latched(engine, reason, clipped)
    except Exception:  # noqa: BLE001 — a check never fails a request
        _log.debug(
            "could not switch the continuation tail off for %r", engine, exc_info=True
        )


def _latch_locked(engine: str, reason: str, detail: str) -> bool:
    """Set ``engine``'s latch unless one is set; the caller holds ``_lock``.

    True when this call set it. The first latch wins, whatever its reason.
    """
    return _set_locked(_tail_latches[engine], reason, detail)


def _set_locked(latch: _Latch, reason: str, detail: str) -> bool:
    """Set ``latch`` unless it is set; the caller holds ``_lock``.

    True when this call set it. Every latch in this module goes through
    here, so "the first latch wins, whatever its reason" is one rule.
    """
    if latch.reason:
        return False
    latch.reason = reason
    latch.detail = detail
    latch.since = time.time()
    return True


def _warn_latched(engine: str, reason: str, detail: str) -> None:
    """The one WARNING a latch writes, outside the lock."""
    _log.warning(
        "Cost self-check: the continuation tail is switched off for %s "
        "until the app restarts (%s). %s",
        engine,
        reason,
        detail or "No detail.",
    )


def is_tail_rejection(exc: BaseException) -> bool:
    """Whether a failed stream open may be the provider refusing the tail.

    A ``BadRequestError`` (a 400) whose text is not "prompt is too long". The
    caller asks only about an error raised while OPENING a stream whose
    request carried the tail; a 400 on a request without it, any other status,
    and anything raised after the stream opened are never the tail's. Never
    raises; answers ``False`` when unsure, which leaves the failure on the
    path it always took.
    """
    try:
        if not isinstance(exc, anthropic.BadRequestError):
            return False
        return not _PROMPT_TOO_LONG.search(str(exc))
    except Exception:  # noqa: BLE001 — a check never fails a request
        _log.debug("could not classify a stream-open failure", exc_info=True)
        return False


def exception_detail(exc: BaseException) -> str:
    """``"<ErrorType>: <message>"``, clipped to :data:`DETAIL_MAX_CHARS`."""
    try:
        return _clip(f"{type(exc).__name__}: {exc}")
    except Exception:  # noqa: BLE001 — a check never fails a request
        return _clip(type(exc).__name__)


def _clip(text: str) -> str:
    """One line, at most :data:`DETAIL_MAX_CHARS` characters."""
    return " ".join(str(text).split())[:DETAIL_MAX_CHARS]


# ---------------------------------------------------------------------------
# The continuation tail's value check (CT-2)
# ---------------------------------------------------------------------------


def _field(source: Any, name: str) -> Any:
    """``source[name]`` for a mapping, else its attribute; ``None`` if absent.

    Usage arrives as SDK models, as the plain dicts an extra field is kept as
    (``Usage`` allows extras, so ``iterations`` on a GA response would be a
    list of dicts), and as the test fakes' namespaces.
    """
    if isinstance(source, Mapping):
        return source.get(name)
    return getattr(source, name, None)


def _count(value: Any, *, required: bool) -> int | None:
    """A reported token count, or ``None`` when it is not one.

    A bool is not a count (``isinstance(True, int)`` is true). A missing
    cache count (``None``) was zero; a missing required count is malformed.
    """
    if value is None:
        return None if required else 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _iteration_counts(source: Any) -> dict[str, int] | None:
    """One iteration's four token counts, or ``None`` if any is malformed."""
    counts: dict[str, int] = {}
    for name in _REQUIRED_COUNTS + _CACHE_COUNTS:
        value = _count(_field(source, name), required=name in _REQUIRED_COUNTS)
        if value is None:
            return None
        counts[name] = value
    return counts


def _reported_iterations(usage: Any) -> list[Any] | None:
    """``usage.iterations`` as a list, or ``None`` when it was not reported.

    Anything reported that is not a list is treated as not reported: the
    caller then falls back to what the response itself proves.
    """
    iterations = _field(usage, "iterations")
    if isinstance(iterations, (list, tuple)):
        return list(iterations)
    return None


def _requested_server_tools(usage: Any) -> bool | None:
    """Whether the usage records a server-tool request of any kind.

    ``None`` when the record is malformed (a count that is not a count), so
    the caller can refuse to guess.
    """
    record = _field(usage, "server_tool_use")
    if record is None:
        return False
    # The two web tools the app uses, and any other ``*_requests`` count the
    # record carries — a declared field, or an extra a newer provider sent.
    names = {"web_search_requests", "web_fetch_requests"}
    sources = (
        (record,)
        if isinstance(record, Mapping)
        else (
            getattr(record, "__dict__", None),
            getattr(record, "__pydantic_extra__", None),
        )
    )
    for source in sources:
        if isinstance(source, Mapping):
            names.update(
                name
                for name in source
                if isinstance(name, str) and name.endswith("_requests")
            )
    for name in names:
        value = _count(_field(record, name), required=False)
        if value is None:
            return None
        if value:
            return True
    return False


def _single_iteration(response: Any, usage: Any) -> bool:
    """Whether the response provably ran ONE model iteration.

    Its content holds only the blocks one iteration can produce (text,
    thinking, client tool calls) — no server tool's use or result — and its
    usage records no server-tool request. Then its top-level usage is that
    iteration's. Anything unreadable answers ``False``.
    """
    content = _field(response, "content")
    if not isinstance(content, (list, tuple)):
        return False
    for block in content:
        if _field(block, "type") not in _SINGLE_ITERATION_BLOCKS:
            return False
    return _requested_server_tools(usage) is False


def first_iteration_usage(response: Any) -> dict[str, int] | None:
    """The token counts of ``response``'s first model iteration, or ``None``.

    In order (the Chunk 4 plan's CT-2 design, item 2):

    - **Reported.** ``response.usage.iterations`` is a list: the first entry
      whose ``type`` is ``message``, read whether it is a dict (an extra
      field on the GA ``Usage``) or an object (the beta ``BetaUsage``).
    - **Single iteration.** Nothing reported, but the response provably ran
      one model iteration (:func:`_single_iteration`): its top-level usage.
    - **Otherwise** ``None``: the top-level usage of a request that ran a
      server-side tool loop sums every iteration, and every later iteration
      re-reads the whole prefix, so it cannot say what the first one did.

    Returns ``input_tokens``, ``output_tokens``, ``cache_read_input_tokens``
    and ``cache_creation_input_tokens``, or ``None`` when any is malformed.
    Reads the response and never changes it. Never raises.
    """
    try:
        usage = _field(response, "usage")
        if usage is None:
            return None
        iterations = _reported_iterations(usage)
        if iterations is not None:
            for entry in iterations:
                if _field(entry, "type") == "message":
                    return _iteration_counts(entry)
            return None
        if not _single_iteration(response, usage):
            return None
        return _iteration_counts(usage)
    except Exception:  # noqa: BLE001 — a check never fails a request
        _log.debug("could not read a response's first iteration", exc_info=True)
        return None


def _one_model_iteration(response: Any) -> bool:
    """Whether nothing after the first iteration could read the tail's entry.

    With ``usage.iterations`` reported, exactly one entry, a ``message``. A
    second iteration (after a server tool ran) reads the entry the tail
    wrote, which the first iteration's usage cannot show: its saving on that
    request is then larger than the first iteration's alone, so the first
    iteration no longer bounds the request's. Without iterations, the
    response must provably be a single iteration.
    """
    usage = _field(response, "usage")
    iterations = _reported_iterations(usage)
    if iterations is not None:
        return len(iterations) == 1 and _field(iterations[0], "type") == "message"
    return _single_iteration(response, usage)


def _answers_pending_tool(response: Any) -> bool:
    """Whether the response answers a server tool call made BEFORE it.

    A continuation that resumes a pending server tool runs the tool first,
    and its first iteration's input is the request plus that tool's result,
    behind an automatic cache breakpoint of the provider's own — so the
    breakpoint the tail adds is not what its reads and writes show. Such a
    response begins with a ``*_tool_result`` block that no server tool call
    in the same response made. Unreadable content answers ``True``: never
    measure what cannot be read.
    """
    content = _field(response, "content")
    if not isinstance(content, (list, tuple)):
        return True
    called: set[str] = set()
    for block in content:
        kind = _field(block, "type")
        if kind in _SERVER_TOOL_CALLS:
            use_id = _field(block, "id")
            if isinstance(use_id, str):
                called.add(use_id)
        elif isinstance(kind, str) and kind.endswith("_tool_result"):
            if _field(block, "tool_use_id") not in called:
                return True
    return False


def _rate(rates: Mapping[str, Any], name: str) -> Decimal:
    """A per-token rate as a ``Decimal``, to twelve significant digits.

    ``settings.PRICING`` divides per-million prices by a million in floats,
    which leaves noise (0.20 / 1_000_000 is 2.0000000000000002e-07); twelve
    digits strips it and touches no real price. Without that, a term whose
    read and write exactly balance could sum to a hair below zero and count
    as a loss.
    """
    return Decimal(format(float(rates[name]), ".12g"))


def _tail_saving(
    model: str, opening: Any, response: Any
) -> tuple[str, Decimal | None]:
    """Classify one observation: ``("exact" | "bound" | "unmeasured", saving)``.

    The Chunk 4 plan's Appendix A, with u, r and w the model's input,
    cache-read and 5-minute cache-write rates (the tail's own entries are
    5-minute ones, even after a verifier seat's 1-hour markers):

    - **exact** — the continuation ran one model iteration and its opening
      response's first iteration is known. With ``base`` the opening's read
      plus write (the explicit prefix), R = max(0, read − base) is what the
      tail let it read, ``missed`` = max(0, base − read) the explicit prefix
      written again because its entry expired (with or without the tail, so
      not the tail's), and W = max(0, write − missed) what the tail wrote.
      S = R·(u − r) − W·(w − u).
    - **bound** — the continuation ran one model iteration and read something,
      but the opening's first iteration is unknown: credit every read to the
      tail and charge every write to it. S_max = read·(u − r) − write·(w − u)
      is an upper bound on S while the explicit prefix's entry is alive.
    - **unmeasured** — anything else: a continuation that answers a pending
      server tool call, one that ran more than one iteration (a later one
      reads the tail's entry, so it saves at least what its first iteration
      shows), a usage record that cannot be read, or a bound with nothing
      read.
    """
    if _answers_pending_tool(response) or not _one_model_iteration(response):
        return "unmeasured", None
    continuation = first_iteration_usage(response)
    if continuation is None:
        return "unmeasured", None
    rates = usage_ledger.model_rates(model)
    u = _rate(rates, "input")
    r = _rate(rates, "cache_read")
    w = _rate(rates, "cache_write")
    read = continuation["cache_read_input_tokens"]
    write = continuation["cache_creation_input_tokens"]
    opened = first_iteration_usage(opening)
    if opened is not None:
        base = opened["cache_read_input_tokens"] + opened["cache_creation_input_tokens"]
        reused = max(0, read - base)
        missed = max(0, base - read)
        written = max(0, write - missed)
        return "exact", reused * (u - r) - written * (w - u)
    if read > 0:
        return "bound", read * (u - r) - write * (w - u)
    return "unmeasured", None


def observe_continuation(
    engine: str, *, model: str, opening: Any, response: Any
) -> None:
    """Record what the continuation tail saved on one request.

    ``response`` answered a request that carried the tail (a tail-free resend
    is never passed); ``opening`` is its conversation's opening response;
    ``model`` is the model both were sent to. The observation is exact, a
    bound, or unmeasured (:func:`_tail_saving`). Once ``engine`` has at least
    :data:`_TAIL_MIN_OBSERVATIONS` measured observations whose summed saving
    is below zero, its tail switches off (``unprofitable``) with one WARNING.
    Every measured term is exact or an upper bound, so the sum bounds what the
    tail saved on those requests: the rule latches only on a proven loss.
    Observing carries on after a latch, for diagnostics.

    Reads the two responses and never changes them; touches no request,
    record, usage total, meter or manifest. Never raises: a failure is
    logged at DEBUG and records nothing.
    """
    try:
        kind, saving = _tail_saving(model, opening, response)
        now = time.time()
        latched = False
        detail = ""
        with _lock:
            value = _tail_values[engine]
            value.last_observed_at = now
            if saving is None:
                value.unmeasured += 1
            else:
                if kind == "exact":
                    value.exact += 1
                else:
                    value.bound += 1
                value.saving += saving
                if (
                    value.measured >= _TAIL_MIN_OBSERVATIONS
                    and value.saving < 0
                    and not _tail_latches[engine].reason
                ):
                    detail = _clip(_unprofitable_detail(value))
                    latched = _latch_locked(engine, REASON_UNPROFITABLE, detail)
        if latched:
            _warn_latched(engine, REASON_UNPROFITABLE, detail)
    except Exception:  # noqa: BLE001 — a check never fails a request
        _log.debug(
            "could not observe a continuation for %r", engine, exc_info=True
        )


def _unprofitable_detail(value: _TailValue) -> str:
    """Why the value check switched the tail off, in one line."""
    return (
        f"{value.measured} measured continuations ({value.exact} exact, "
        f"{value.bound} bound) cost an estimated ${_usd(-value.saving):.6f} "
        "more than they saved, even on the most generous reading."
    )


def _usd(amount: Decimal) -> float:
    """A dollar amount rounded to six places, away from zero, never ``-0.0``.

    Away from zero so the sign survives: a sum can sit a fraction of a
    millionth of a dollar below zero, latch, and would otherwise read as a
    saving of ``0.0``. A sum of exactly zero stays ``0.0``.
    """
    return float(amount.quantize(_USD_PLACES, rounding=ROUND_UP)) + 0.0


# ---------------------------------------------------------------------------
# The warm lead's check (WL-1)
# ---------------------------------------------------------------------------

# What the check concluded about one lineage: the diagnostics block's
# ``verdict``, a closed vocabulary. The two that switch the lead off are the
# reasons above; the other three leave it on.
VERDICT_KEPT = "kept"  # judged, and neither rule applies
VERDICT_TOO_FEW = "too_few"  # too few batched seats measured to judge
VERDICT_NOT_WARM = "not_warm"  # the batch went out before the lead's copy was ready
WARM_LEAD_VERDICTS = (
    VERDICT_KEPT,
    VERDICT_TOO_FEW,
    VERDICT_NOT_WARM,
    REASON_NOT_READ,
    REASON_UNPROFITABLE,
)

# A batched seat read the shared prefix when at least this share of its first
# iteration's cached input was read rather than written (Appendix B.2). The
# slack covers a small inner breakpoint (tools, system) written while the
# prefix's own block hit.
_WARM_LEAD_SEAT_READ_SHARE = Decimal("0.95")
# A lineage is judged only on at least this many measured batched seats: a
# handful of seats cannot tell a batch that does not read the lead's copy
# from one that was scheduled unluckily.
_WARM_LEAD_MIN_MEASURED = 8
# Below this share of measured seats reading the prefix, the batch is not
# reading the lead's copy (Appendix B.5): every seat was submitted after the
# lead's entry was readable, so a batch that can read it sits near 1.
_WARM_LEAD_MIN_READ_SHARE = Decimal("0.5")

_SHARE_PLACES = Decimal("0.0001")


@dataclass(frozen=True)
class WarmLeadLineage:
    """One cache lineage of a batched phase that ended normally with its lead.

    - ``kind`` — ``web-tooled`` or ``no-web`` (the engine's lineage kinds);
    - ``seats`` — n, the lineage's seats, the lead among them;
    - ``model`` — the model its seats were sent to;
    - ``lead_usage`` — every billed response of the lead, summed
      (``usage_ledger.usage_to_dict``'s keys); billed at list price;
    - ``batched_first`` — each OTHER seat's reply to the first batch it
      rode (submitted after the lead's release), or ``None`` for a seat
      whose first batch brought none. A retried seat's later reply is never
      here: it ran after that round ended, when it could read a copy an
      earlier batched seat stored rather than the lead's;
    - ``warm`` — whether the batch went out after the lead's entry was
      readable: the lead's first output arrived before the wait ended and
      none of its requests failed. When it did not, the batch had no copy of
      the lead's to read, and the lineage says nothing about the lead.
    """

    kind: str
    seats: int
    model: str
    lead_usage: Mapping[str, int]
    batched_first: tuple[Any, ...]
    warm: bool = True


@dataclass
class _WarmLeadState:
    """The warm lead's latch, and the last check, for diagnostics."""

    latch: _Latch = field(default_factory=_Latch)
    last_check: dict[str, Any] | None = None


@dataclass(frozen=True)
class _LineageJudgment:
    """What :func:`_judge_lineage` found. The numbers are ``None`` below
    :data:`_WARM_LEAD_MIN_MEASURED` measured seats (``break_even`` also when
    the rates leave nothing to divide by); ``lead_cost`` is always known."""

    kind: str
    seats: int
    measured: int
    unmeasured: int
    reads: int
    read_share: Decimal | None
    prefix: Decimal | None
    lead_cost: Decimal
    break_even: Decimal | None
    verdict: str


_warm_lead = _WarmLeadState()


def warm_lead_enabled() -> bool:
    """False once a check has switched the warm lead off.

    Read once per batched verifier phase, after the switch
    (``settings.QC_BATCH_WARM_LEAD``, pinned per Final QC run): the switch
    decides whether a lead is wanted, and this can only take it away, from
    the next phase on. Never raises; if the check itself fails it answers
    ``True``, which leaves the lead exactly as the switch set it.
    """
    try:
        with _lock:
            return not _warm_lead.latch.reason
    except Exception:  # noqa: BLE001 — a check never fails a request
        _log.debug("warm lead check failed", exc_info=True)
        return True


def disable_warm_lead(*, reason: str, detail: str = "") -> None:
    """Switch the warm lead off until the app restarts.

    ``reason`` is ``not_read`` or ``unprofitable``. The first latch wins: a
    later call changes nothing and logs nothing. ``detail`` is clipped to
    :data:`DETAIL_MAX_CHARS` and reaches diagnostics (which scrub it), never
    a record. Never raises: a malformed call is logged at DEBUG and ignored.
    """
    try:
        if reason not in _WARM_LEAD_REASONS:
            raise ValueError(f"not a warm-lead reason: {reason!r}")
        clipped = _clip(detail)
        with _lock:
            latched = _set_locked(_warm_lead.latch, reason, clipped)
        if latched:
            _warn_warm_lead_latched(reason, clipped)
    except Exception:  # noqa: BLE001 — a check never fails a request
        _log.debug("could not switch the warm lead off", exc_info=True)


def _warn_warm_lead_latched(reason: str, detail: str) -> None:
    """The one WARNING the warm lead's latch writes, outside the lock."""
    _log.warning(
        "Cost self-check: the warm lead is switched off until the app "
        "restarts (%s). %s",
        reason,
        detail or "No detail.",
    )


def _seat_prefix(response: Any) -> tuple[int, int] | None:
    """A batched seat's first iteration, as ``(read, write)`` cached tokens.

    ``None`` — the seat is unmeasured, counted and never guessed — when the
    seat's first batch brought no reply, its first iteration cannot be read
    (a web-tooled seat that searched, with no ``usage.iterations``
    reported), or it read and wrote nothing.
    """
    counts = first_iteration_usage(response)
    if counts is None:
        return None
    read = counts["cache_read_input_tokens"]
    write = counts["cache_creation_input_tokens"]
    if read + write == 0:
        return None
    return read, write


def _median(values: list[int]) -> Decimal:
    """The median, exact: the mean of the middle two for an even count."""
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return Decimal(ordered[middle])
    return (Decimal(ordered[middle - 1]) + Decimal(ordered[middle])) / 2


def _judge_lineage(lineage: WarmLeadLineage) -> _LineageJudgment:
    """Appendix B for one lineage: h₁, p, C, h₀* and the verdict.

    With w₁ and r the model's 1-hour cache-write and cache-read rates, b the
    batch multiplier and Δ = w₁ − r, the lead paid if and only if h₀ < h₀*,

        h₀* = [(n − 1)·b·h₁·Δ·p − (1 − b)·C] / (n·b·Δ·p).

    The verdicts, in order: ``not_warm`` when the batch went out before the
    lead's copy was ready (the lineage says nothing about the lead);
    ``too_few`` below :data:`_WARM_LEAD_MIN_MEASURED` measured seats;
    ``not_read`` when h₁ < :data:`_WARM_LEAD_MIN_READ_SHARE`; ``unprofitable``
    when h₀* ≤ 0 — the numerator at most zero, which says the same whatever
    the denominator's sign; otherwise ``kept``.
    """
    measured: list[int] = []
    reads = 0
    unmeasured = 0
    for response in lineage.batched_first:
        prefix = _seat_prefix(response)
        if prefix is None:
            unmeasured += 1
            continue
        read, write = prefix
        measured.append(read + write)
        if read >= _WARM_LEAD_SEAT_READ_SHARE * (read + write):
            reads += 1

    rates = usage_ledger.model_rates(lineage.model)
    one_hour = "cache_write_1h" if "cache_write_1h" in rates else "cache_write"
    delta = _rate(rates, one_hour) - _rate(rates, "cache_read")
    batch = Decimal(format(float(settings.BATCH_COST_MULTIPLIER), ".12g"))
    lead_cost = Decimal(
        str(usage_ledger.estimate_usage_cost(lineage.model, dict(lineage.lead_usage)))
    )
    seats = int(lineage.seats)

    read_share = prefix = break_even = numerator = None
    if len(measured) >= _WARM_LEAD_MIN_MEASURED:
        read_share = Decimal(reads) / Decimal(len(measured))
        prefix = _median(measured)
        numerator = (seats - 1) * batch * read_share * delta * prefix - (
            1 - batch
        ) * lead_cost
        denominator = seats * batch * delta * prefix
        if denominator > 0:
            break_even = numerator / denominator

    if not lineage.warm:
        verdict = VERDICT_NOT_WARM
    elif read_share is None or numerator is None:
        verdict = VERDICT_TOO_FEW
    elif read_share < _WARM_LEAD_MIN_READ_SHARE:
        verdict = REASON_NOT_READ
    elif numerator <= 0:
        verdict = REASON_UNPROFITABLE
    else:
        verdict = VERDICT_KEPT
    return _LineageJudgment(
        kind=str(lineage.kind),
        seats=seats,
        measured=len(measured),
        unmeasured=unmeasured,
        reads=reads,
        read_share=read_share,
        prefix=prefix,
        lead_cost=lead_cost,
        break_even=break_even,
        verdict=verdict,
    )


def _share(value: Decimal | None, *, rounding: str) -> float | None:
    """A share rounded to four places, never ``-0.0``."""
    if value is None:
        return None
    return float(value.quantize(_SHARE_PLACES, rounding=rounding)) + 0.0


def _lineage_record(judged: _LineageJudgment) -> dict[str, Any]:
    """One lineage of the diagnostics block's ``last_check``: scalars only.

    ``read_share`` rounds half-even; ``break_even_read_share`` rounds away
    from zero, so a break-even a hair above zero never reads as ``0.0`` beside
    a ``kept`` verdict (nor one a hair below as a positive).
    """
    prefix = judged.prefix
    return {
        "kind": judged.kind,
        "seats": judged.seats,
        "measured": judged.measured,
        "unmeasured": judged.unmeasured,
        "read_share": _share(judged.read_share, rounding=ROUND_HALF_EVEN),
        "prefix_tokens": (
            None
            if prefix is None
            else int(prefix)
            if prefix == prefix.to_integral_value()
            else float(prefix)
        ),
        "lead_cost_usd": _usd(judged.lead_cost),
        "break_even_read_share": _share(judged.break_even, rounding=ROUND_UP),
        "verdict": judged.verdict,
    }


def _warm_lead_detail(judged: _LineageJudgment) -> str:
    """Why the check switched the lead off, in one line."""
    record = _lineage_record(judged)
    if judged.verdict == REASON_NOT_READ:
        return (
            f"The batch read the shared prefix on {judged.reads} of "
            f"{judged.measured} measured {judged.kind} seats "
            f"({record['read_share']:.0%}); a batch that reads the lead's "
            "copy reads it on nearly all of them."
        )
    return (
        f"The lead cost an estimated ${record['lead_cost_usd']:.6f}, more than "
        f"it could have saved on its {judged.seats}-seat {judged.kind} lineage "
        "even if the batch alone had read nothing (break-even read share "
        f"{record['break_even_read_share']})."
    )


def _log_judgment(judged: _LineageJudgment) -> None:
    """The one INFO line per lineage: numbers only, never finding text."""
    record = _lineage_record(judged)
    _log.info(
        "Warm lead check: %d %s seats (the lead among them), %d measured, "
        "%d unmeasured; read share %s, break-even read share %s, lead cost "
        "$%.6f: %s.",
        judged.seats,
        judged.kind,
        judged.measured,
        judged.unmeasured,
        "n/a" if record["read_share"] is None else f"{record['read_share']:.4f}",
        "n/a"
        if record["break_even_read_share"] is None
        else f"{record['break_even_read_share']:.4f}",
        record["lead_cost_usd"],
        judged.verdict,
    )


def check_warm_leads(lineages: Sequence[WarmLeadLineage]) -> None:
    """Judge the lineages whose lead one batched phase streamed.

    The engine calls this once per batched verifier phase, when the phase
    ended normally, after the leads were joined, with one entry per lineage
    whose lead sent a request. Each lineage is judged (:func:`_judge_lineage`)
    and logged in one INFO line; the phase's judgments replace the last
    check in diagnostics; and the first lineage judged ``not_read`` or
    ``unprofitable`` switches the lead off for the rest of the app session,
    with one WARNING. Recording and latching are one lock acquisition, so a
    reader never sees a check without the latch it earned.

    Reads the responses and never changes them; touches no request, record,
    usage total, meter or manifest. Never raises: a failure is logged at
    DEBUG and records nothing.
    """
    try:
        judged = [_judge_lineage(lineage) for lineage in lineages]
        if not judged:
            return
        record = {
            "at": time.time(),
            "lineages": [_lineage_record(judgment) for judgment in judged],
        }
        losing = next(
            (j for j in judged if j.verdict in _WARM_LEAD_REASONS), None
        )
        detail = _clip(_warm_lead_detail(losing)) if losing is not None else ""
        with _lock:
            _warm_lead.last_check = record
            latched = losing is not None and _set_locked(
                _warm_lead.latch, losing.verdict, detail
            )
        for judgment in judged:
            _log_judgment(judgment)
        if latched:
            _warn_warm_lead_latched(losing.verdict, detail)
    except Exception:  # noqa: BLE001 — a check never fails a request
        _log.debug("could not check the warm lead", exc_info=True)


def _copy_check(check: dict[str, Any] | None) -> dict[str, Any] | None:
    """The last check, copied, so a reader can never edit the module's."""
    if check is None:
        return None
    return {
        "at": check["at"],
        "lineages": [dict(lineage) for lineage in check["lineages"]],
    }


def snapshot() -> dict[str, Any]:
    """What the checks have decided, for diagnostics. Never raises.

    ``{"continuation_tail": {engine: {setting_on, enabled, reason, detail,
    since, measured, exact, bound, unmeasured, saving_usd,
    last_observed_at}}}``. ``setting_on`` is the live switch; ``enabled`` is
    the checks' verdict alone (``False`` once switched off), so the tail is
    sent only when both are true. ``since`` is when the latch was set, in
    seconds since the epoch (``None`` while it is not). The value check's
    counts follow: ``measured`` is ``exact`` plus ``bound``, ``saving_usd``
    the signed sum of their savings rounded to six places (away from zero,
    :func:`_usd`), and ``last_observed_at`` when a response to a request
    carrying the tail was last observed. Grouped by behavior, so a later
    check's block sits beside this one rather than among the engine names.

    ``"warm_lead": {setting_on, enabled, reason, detail, since,
    last_check}`` sits beside it (WL-1): the same five keys for the warm
    lead's one latch, and ``last_check`` — ``None`` until a phase is
    checked, then ``{at, lineages}``, one entry per lineage the last checked
    phase judged, each ``{kind, seats, measured, unmeasured, read_share,
    prefix_tokens, lead_cost_usd, break_even_read_share, verdict}``
    (:func:`_lineage_record`). Every value in a lineage is a scalar: the
    support bundle scrubs this block six levels down, and a lineage's values
    sit at the sixth. Both blocks are read in one lock acquisition, so they
    describe one moment.
    """
    try:
        setting_on = bool(settings.CONTINUATION_CACHE)
        warm_setting_on = bool(settings.QC_BATCH_WARM_LEAD)
        with _lock:
            tail = {
                engine: {
                    "setting_on": setting_on,
                    "enabled": not latch.reason,
                    "reason": latch.reason,
                    "detail": latch.detail,
                    "since": latch.since,
                    "measured": _tail_values[engine].measured,
                    "exact": _tail_values[engine].exact,
                    "bound": _tail_values[engine].bound,
                    "unmeasured": _tail_values[engine].unmeasured,
                    "saving_usd": _usd(_tail_values[engine].saving),
                    "last_observed_at": _tail_values[engine].last_observed_at,
                }
                for engine, latch in _tail_latches.items()
            }
            warm = {
                "setting_on": warm_setting_on,
                "enabled": not _warm_lead.latch.reason,
                "reason": _warm_lead.latch.reason,
                "detail": _warm_lead.latch.detail,
                "since": _warm_lead.latch.since,
                "last_check": _copy_check(_warm_lead.last_check),
            }
        return {"continuation_tail": tail, "warm_lead": warm}
    except Exception:  # noqa: BLE001 — diagnostics must not fail on a check
        _log.debug("could not snapshot the cost self-checks", exc_info=True)
        return {}


def reset_for_tests() -> None:
    """Clear every latch, observation and check (the conftest calls this
    around every test)."""
    with _lock:
        for engine in TAIL_ENGINES:
            _tail_latches[engine] = _Latch()
            _tail_values[engine] = _TailValue()
        _warm_lead.latch = _Latch()
        _warm_lead.last_check = None
