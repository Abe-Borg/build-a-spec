"""Cost self-checks: runtime watchers that can only switch a saving OFF.

Tier 1 of the research and Final QC cost program built two savings that ship
off until a measured trial proves them, and that trial is not coming (the
Tier 1 progress file's O6). The Tier 1 finish program (decision FD1,
``docs/plans/tier1-finish/``) replaces the trial with checks that watch the
runs the app makes anyway, and this module holds their state.

The one check so far is the **continuation tail's guard** (session CT-1). The
tail is a top-level ``cache_control`` on a streamed request that resumes a
``pause_turn`` (``settings.CONTINUATION_CACHE``). If the provider ever
refuses a continuation that carries it, with a 400 when the stream opens, the
engine sends the same request once more without it and asks this module to
switch that engine's tail off for the rest of the app session. That turns the
tail's one unbounded failure (every paused research area and compliance
review failing, on every run) into one extra request per engine per app
session. The resend itself is engine code (``_open_stream`` in each engine,
copied rather than shared); this module holds only the latch.

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
logged per request. :func:`snapshot` is what Settings → Developer tools and a
support bundle read, through ``diagnostics.snapshot()``'s top-level
``cost_checks`` block.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

import anthropic

from . import settings

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
_TAIL_REASONS = frozenset({REASON_REJECTED})

# The detail a latch keeps: the error type and the start of its message.
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


_lock = threading.Lock()
_tail_latches: dict[str, _Latch] = {engine: _Latch() for engine in TAIL_ENGINES}


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
            latch = _tail_latches[engine]
            if latch.reason:
                return
            latch.reason = reason
            latch.detail = clipped
            latch.since = time.time()
        _log.warning(
            "Cost self-check: the continuation tail is switched off for %s "
            "until the app restarts (%s). %s",
            engine,
            reason,
            clipped or "No detail.",
        )
    except Exception:  # noqa: BLE001 — a check never fails a request
        _log.debug(
            "could not switch the continuation tail off for %r", engine, exc_info=True
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


def snapshot() -> dict[str, Any]:
    """What the checks have decided, for diagnostics. Never raises.

    ``{"continuation_tail": {engine: {setting_on, enabled, reason, detail,
    since}}}``. ``setting_on`` is the live switch; ``enabled`` is the checks'
    verdict alone (``False`` once switched off), so the tail is sent only
    when both are true. ``since`` is when the latch was set, in seconds since
    the epoch (``None`` while it is not). Grouped by behavior, so a later
    check's block sits beside this one rather than among the engine names.
    """
    try:
        setting_on = bool(settings.CONTINUATION_CACHE)
        with _lock:
            tail = {
                engine: {
                    "setting_on": setting_on,
                    "enabled": not latch.reason,
                    "reason": latch.reason,
                    "detail": latch.detail,
                    "since": latch.since,
                }
                for engine, latch in _tail_latches.items()
            }
        return {"continuation_tail": tail}
    except Exception:  # noqa: BLE001 — diagnostics must not fail on a check
        _log.debug("could not snapshot the cost self-checks", exc_info=True)
        return {}


def reset_for_tests() -> None:
    """Clear every latch (the conftest calls this around every test)."""
    with _lock:
        for engine in TAIL_ENGINES:
            _tail_latches[engine] = _Latch()
