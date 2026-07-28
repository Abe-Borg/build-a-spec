"""What day it is at run time — the one fact the model cannot infer.

A language model has no clock. Left unsaid, it answers "what edition is
current?" from the shape of its training data, which is frozen at some
point in the past and drifts further out of date with every month the
build stays in the field. For a specification tool that is not a cosmetic
gap: NFPA and the I-codes revise on fixed multi-year cycles, so an app
that never states the date will keep proposing whatever edition was
newest when the model was trained, indefinitely and with total
confidence.

This module is the one place the app reads the wall clock for the model's
benefit. Three rules hold everywhere it is used:

- **Local time, not UTC.** This is a desktop app; "today" means the user's
  today. A UTC date is a day ahead for a user in the Americas every
  evening, and the stamp is rendered with its zone so the model is never
  guessing which clock it is reading. (Audit timestamps like
  ``QCResult.started_at`` stay UTC — those are records, not context.)
- **Never in a cached prefix, unless pinned once per run.** The stable
  system prompt carries ``cache_control`` and must stay byte-identical for
  the whole session, so the date renders into dynamic context only. The
  QC and research fan-outs DO put it in a cached shared prefix — which is
  safe only because those runs read the clock once and thread the result
  to every call. A per-call read would split the cache lineage the moment
  a run crossed midnight; a per-call *timestamp* would break it on every
  single call.
- **Date alone in the fan-outs, date and time in chat.** Time of day
  cannot change the answer to a code-cycle question, and it is exactly the
  component that would churn a cached prefix. In chat, where the block is
  rebuilt per turn and stripped again at commit, the full stamp is free
  and occasionally useful.

:func:`current_datetime` takes an optional ``now`` so tests can pin a date
without monkeypatching the clock module-wide.
"""
from __future__ import annotations

from datetime import datetime

# The actionable half. Stating the date alone still leaves the model free
# to treat its own recollection as current; this says what to do with the
# gap, and it is deliberately shared verbatim by every surface (chat,
# research, Final QC, the deprecated audit) so the instruction cannot
# drift into four subtly different postures.
DATE_AWARENESS_DIRECTIVE = (
    "This is the real-world date at run time and it is authoritative — it "
    "supersedes any sense of the present you carry from training. Your "
    "training data ends at some earlier point, so a code or standard "
    "edition you recall as the newest may since have been superseded, and "
    "a revision cycle you recall as pending may since have closed. Never "
    "call an edition current from memory alone: go by the editions in "
    "effect recorded for this project, by grounded research, or by a web "
    "lookup. When the date makes it likely that a newer edition exists "
    "than the one recorded, say so plainly instead of quietly drafting to "
    "either one."
)


def current_datetime(now: datetime | None = None) -> datetime:
    """The current local time as a timezone-aware datetime.

    ``now`` overrides the clock (tests, and any future caller that needs a
    fixed reading). A naive ``now`` is interpreted as local time, which is
    what a caller passing ``datetime(2029, 3, 1, 9, 0)`` means.
    """
    return (now or datetime.now()).astimezone()


def current_date_iso(now: datetime | None = None) -> str:
    """Local calendar date as ``YYYY-MM-DD``."""
    return current_datetime(now).strftime("%Y-%m-%d")


def _zone_label(stamp: datetime) -> str:
    """``local time, PDT, UTC-07:00`` — degrading as the platform allows.

    ``%Z`` is empty on some platforms and ``%z`` is empty for a naive
    stamp, so both are optional and the phrase still reads correctly with
    neither.
    """
    parts: list[str] = []
    name = (stamp.tzname() or "").strip()
    if name:
        parts.append(name)
    offset = stamp.strftime("%z")
    numeric = f"UTC{offset[:3]}:{offset[3:5]}" if len(offset) >= 5 else ""
    # On a UTC host the name is already "UTC" and the numeric form would
    # read "local time, UTC, UTC+00:00".
    if numeric and not (
        numeric == "UTC+00:00" and name.upper() in {"UTC", "GMT"}
    ):
        parts.append(numeric)
    return "local time" + (f", {', '.join(parts)}" if parts else "")


def date_context_block(
    now: datetime | None = None, *, with_time: bool = False
) -> str:
    """The rendered date block: the stamp, then the shared directive.

    ``with_time`` adds the clock time and zone — correct for the per-turn
    chat context, wrong for a fan-out's cached shared prefix (see the
    module docstring). The date is written out longhand as well as
    numerically because a bare ISO string is the format most easily
    mistaken for an example value.
    """
    stamp = current_datetime(now)
    written = f"{stamp.day} {stamp:%B %Y}"
    if with_time:
        head = (
            f"CURRENT DATE AND TIME: {stamp:%A}, {written}, "
            f"{stamp:%H:%M} ({_zone_label(stamp)}). "
            f"Today's date is {stamp:%Y-%m-%d}."
        )
    else:
        head = (
            f"CURRENT DATE: {stamp:%A}, {written} "
            f"({stamp:%Y-%m-%d})."
        )
    return f"{head}\n{DATE_AWARENESS_DIRECTIVE}"
