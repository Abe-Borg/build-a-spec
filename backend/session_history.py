"""Session identity and its history across app launches.

Build-a-Spec native (no Spec Critic source). A leaf like ``cost_checks``:
stdlib only, imported by the session store, the project serializer,
diagnostics and the trace recorder, so none of them has to import another.

A Build-a-Spec SESSION is one section's work: what a ``.baspec`` holds. It
outlives the app LAUNCH that opened it — a section can take weeks, reopened
every morning — while every diagnostic artifact is per launch: one log
directory (``logs/process-<id>``) and one trace run (``traces/session-<id>``,
named "session" for historical reasons but created once per launch). Until
this module nothing tied the two together: a support bundle could only take
the current launch in full, and retention pruned a long section's first
launches by age and count before anyone asked for them.

Three things fix that, all here or driven from here:

1. **Identity.** Every session carries a ``session_uid`` (32 hex). A new or
   reset session mints one; a loaded ``.baspec`` brings its own back; a file
   saved before this existed gets one at load and keeps it from its next
   save. A tutorial clone is a different session and gets its own. Log lines
   (``session=``) and trace records (``session_uid``) are stamped with the
   ACTIVE session's uid — the workspace's, read lock-free — so a launch that
   worked on several sections can be told apart line by line. Work that
   outlives a load (a research run the UI let continue) is stamped with the
   session that is active when the record is written, not the one that
   started it; loads are refused while work runs, so this is rare.

2. **The journal.** One entry per VISIT — the span from a session being
   created or opened until it is replaced — rides the ``.baspec`` under
   :data:`JOURNAL_KEY`: when, which launch (log and trace run ids), which
   app version, how many turns and what they cost. Numbers and ids only,
   never text of the work. It is small (at most :data:`MAX_JOURNAL_VISITS`
   entries) and survives retention, a moved machine and a shared file, so
   a bundle can always say which launches the section had and which of
   them are no longer on disk. A visit is recorded only when it is saved: a
   visit that ends in a crash before its first save is not in the file, but
   its launch was tagged at load (below), so the bundle still finds it.

3. **The launch index.** When a session is loaded or saved, its uid is
   recorded in that launch's log run marker and trace ``run.json``
   (``session_uids``, :data:`MAX_RUN_SESSION_UIDS` at most). Retention reads
   it: a session is LIVE while any launch tagged with it was active in the
   last :func:`session_history_days` days, and every launch of a live
   session is exempt from the age and count ceilings. The byte ceiling still
   holds — it is the disk guarantee — but removes other launches first.
   A blank session nobody loaded or saved never tags a launch, so the
   fresh session every launch starts with cannot keep that launch alive.
"""
from __future__ import annotations

import math
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

JOURNAL_KEY = "session_journal"
JOURNAL_SCHEMA = 1

# A visit a year at three launches a working day is ~750; a section that
# lives longer than that keeps its newest visits and counts the rest.
MAX_JOURNAL_VISITS = 500
# Sessions one launch can be tagged with. A launch that opens more sections
# than this keeps the newest; the uids are 32 bytes each.
MAX_RUN_SESSION_UIDS = 64

ENV_SESSION_HISTORY_DAYS = "BUILD_A_SPEC_SESSION_HISTORY_DAYS"
DEFAULT_SESSION_HISTORY_DAYS = 90

SESSION_UID_RE = re.compile(r"^[0-9a-f]{32}$")
_PROCESS_RUN_ID_RE = re.compile(r"^process-[0-9a-f]{32}$")
# ``tracing.capture`` mints ``session-<8 hex>-<epoch seconds>``.
_TRACE_RUN_ID_RE = re.compile(r"^session-[0-9a-f]{8}-[0-9]{1,12}$")
_TOKEN_KEY_RE = re.compile(r"^[a-z0-9_]{1,64}$")
_APP_VERSION_RE = re.compile(r"^[0-9A-Za-z.+-]{1,40}$")

# How a visit began. ``tutorial`` is a disposable clone of the user's
# session, which is a different session with its own uid.
VISIT_BEGINNINGS = ("new", "opened", "tutorial")

# The billed counters a visit keeps. Everything else the usage meter tracks
# (pricing tiers, request counts) is bookkeeping for the cost estimate.
VISIT_TOKEN_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "thinking_tokens",
    "web_search_requests",
    "web_fetch_requests",
)


def new_session_uid() -> str:
    return uuid.uuid4().hex


def valid_session_uid(value: Any) -> str:
    """``value`` when it is a well-formed uid, else ``""``."""
    if isinstance(value, str) and SESSION_UID_RE.fullmatch(value):
        return value
    return ""


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    if not math.isfinite(float(value)) or value < 0:
        return 0
    return int(value)


def _matching(value: Any, pattern: re.Pattern[str]) -> str:
    return value if isinstance(value, str) and pattern.fullmatch(value) else ""


def sanitize_visit(value: Any) -> dict[str, Any] | None:
    """One journal visit, rebuilt from known fields only; None if unusable.

    A shared file is untrusted input: every field is re-typed and bounded,
    and anything unrecognized is dropped rather than carried forward.
    """
    if not isinstance(value, Mapping):
        return None
    visit_id = valid_session_uid(value.get("visit_id"))
    started_at = _finite_number(value.get("started_at"))
    if not visit_id or started_at is None:
        return None
    began = value.get("began")
    tokens_in = value.get("tokens")
    tokens: dict[str, int] = {}
    if isinstance(tokens_in, Mapping):
        for key in VISIT_TOKEN_KEYS:
            amount = _count(tokens_in.get(key))
            if amount:
                tokens[key] = amount
    cost = _finite_number(value.get("estimated_cost_usd"))
    visit: dict[str, Any] = {
        "visit_id": visit_id,
        "began": began if began in VISIT_BEGINNINGS else "opened",
        "started_at": started_at,
        "last_saved_at": _finite_number(value.get("last_saved_at")),
        "process_run_id": _matching(value.get("process_run_id"), _PROCESS_RUN_ID_RE),
        "trace_run_id": _matching(value.get("trace_run_id"), _TRACE_RUN_ID_RE),
        "app_version": _matching(value.get("app_version"), _APP_VERSION_RE),
        "turns": _count(value.get("turns")),
        "estimated_cost_usd": round(cost, 6) if cost is not None else 0.0,
        "tokens": tokens,
    }
    return visit


@dataclass(frozen=True)
class RestoredJournal:
    """What a loaded file's journal yields, already sanitized."""

    session_uid: str
    created_at: float | None
    visits: tuple[dict[str, Any], ...]
    dropped_visits: int


def sanitize_journal(value: Any) -> RestoredJournal | None:
    """The journal of a loaded file, or None when absent or unusable.

    Lenient like every optional project key: a malformed journal loads as
    "no history", never as a failed load. A bad visit is dropped on its own;
    a bad uid makes the whole journal unusable, because visits under a uid
    nobody can match describe nothing.
    """
    if not isinstance(value, Mapping):
        return None
    uid = valid_session_uid(value.get("session_uid"))
    if not uid:
        return None
    raw_visits = value.get("visits")
    visits: list[dict[str, Any]] = []
    seen: set[str] = set()
    if isinstance(raw_visits, list):
        for raw in raw_visits:
            visit = sanitize_visit(raw)
            if visit is None or visit["visit_id"] in seen:
                continue
            seen.add(visit["visit_id"])
            visits.append(visit)
    dropped = _count(value.get("dropped_visits"))
    if len(visits) > MAX_JOURNAL_VISITS:
        dropped += len(visits) - MAX_JOURNAL_VISITS
        visits = visits[-MAX_JOURNAL_VISITS:]
    return RestoredJournal(
        session_uid=uid,
        created_at=_finite_number(value.get("created_at")),
        visits=tuple(visits),
        dropped_visits=dropped,
    )


def _visit_usage(usage_snapshot: Mapping[str, Any] | None) -> dict[str, Any]:
    snapshot = usage_snapshot if isinstance(usage_snapshot, Mapping) else {}
    totals = snapshot.get("totals")
    tokens: dict[str, int] = {}
    if isinstance(totals, Mapping):
        for key in VISIT_TOKEN_KEYS:
            amount = _count(totals.get(key))
            if amount:
                tokens[key] = amount
    estimated = snapshot.get("estimated_cost_usd")
    cost = (
        _finite_number(estimated.get("total"))
        if isinstance(estimated, Mapping)
        else None
    )
    return {
        "turns": _count(snapshot.get("turns")),
        "estimated_cost_usd": round(cost, 6) if cost is not None else 0.0,
        "tokens": tokens,
    }


@dataclass
class SessionIdentity:
    """Who this session is, and the visits it had before this one.

    Lives on ``SessionState.identity``. Replaced, never mutated in place, on
    reset (a new session), load (the file's identity) and tutorial clone
    (a different session), so a reader holding the old object sees a
    consistent one.
    """

    session_uid: str = field(default_factory=new_session_uid)
    # When the session was first created; None for a file saved before
    # session history existed (its first visit is the earliest record).
    created_at: float | None = field(default_factory=time.time)
    # Visits saved before this one, oldest first, already sanitized.
    visits: tuple[dict[str, Any], ...] = ()
    dropped_visits: int = 0
    # This visit.
    visit_id: str = field(default_factory=new_session_uid)
    visit_started_at: float = field(default_factory=time.time)
    began: str = "new"

    @classmethod
    def fresh(cls, *, began: str = "new") -> "SessionIdentity":
        return cls(began=began if began in VISIT_BEGINNINGS else "new")

    @classmethod
    def from_project(cls, project: Mapping[str, Any]) -> "SessionIdentity":
        """The identity a loaded file brings back, as a new visit of it.

        A file without a usable journal — every file saved before this
        existed — gets a fresh uid with no known creation time. It keeps
        that uid from its next save; a file opened twice without a save in
        between gets two different ones, and nothing was recorded under the
        first.
        """
        restored = sanitize_journal(project.get(JOURNAL_KEY))
        if restored is None:
            return cls(created_at=None, began="opened")
        return cls(
            session_uid=restored.session_uid,
            created_at=restored.created_at,
            visits=restored.visits,
            dropped_visits=restored.dropped_visits,
            began="opened",
        )

    def current_visit(
        self,
        *,
        usage_snapshot: Mapping[str, Any] | None,
        process_run_id: str,
        trace_run_id: str,
        app_version: str,
        now: float | None,
    ) -> dict[str, Any]:
        """This visit as a journal entry. ``now`` is its save time, or None
        for a visit described without being saved (the support bundle)."""
        return {
            "visit_id": self.visit_id,
            "began": self.began,
            "started_at": self.visit_started_at,
            "last_saved_at": now,
            "process_run_id": _matching(process_run_id, _PROCESS_RUN_ID_RE),
            "trace_run_id": _matching(trace_run_id, _TRACE_RUN_ID_RE),
            "app_version": _matching(app_version, _APP_VERSION_RE),
            **_visit_usage(usage_snapshot),
        }

    def journal(
        self,
        *,
        usage_snapshot: Mapping[str, Any] | None,
        process_run_id: str,
        trace_run_id: str,
        app_version: str,
        now: float | None,
    ) -> dict[str, Any]:
        """The journal with this visit upserted last (the ``.baspec`` key).

        Saving the same visit again replaces its entry, so a visit is one
        entry however often it is saved.
        """
        current = self.current_visit(
            usage_snapshot=usage_snapshot,
            process_run_id=process_run_id,
            trace_run_id=trace_run_id,
            app_version=app_version,
            now=now,
        )
        visits = [
            dict(visit)
            for visit in self.visits
            if visit.get("visit_id") != self.visit_id
        ]
        visits.append(current)
        dropped = self.dropped_visits
        if len(visits) > MAX_JOURNAL_VISITS:
            dropped += len(visits) - MAX_JOURNAL_VISITS
            visits = visits[-MAX_JOURNAL_VISITS:]
        return {
            "schema": JOURNAL_SCHEMA,
            "session_uid": self.session_uid,
            "created_at": self.created_at,
            "visits": visits,
            "dropped_visits": dropped,
        }


def journal_totals(journal: Mapping[str, Any]) -> dict[str, Any]:
    """Flat roll-up of a journal for the snapshot (scalars only)."""
    visits = [v for v in journal.get("visits") or () if isinstance(v, Mapping)]
    starts = [
        v["started_at"]
        for v in visits
        if isinstance(v.get("started_at"), (int, float))
    ]
    launches = {
        v.get("process_run_id") or v.get("trace_run_id")
        for v in visits
        if v.get("process_run_id") or v.get("trace_run_id")
    }
    return {
        "session_uid": journal.get("session_uid", ""),
        "created_at": journal.get("created_at"),
        "visits_recorded": len(visits),
        "visits_dropped": _count(journal.get("dropped_visits")),
        "launches_recorded": len(launches),
        "first_visit_at": min(starts) if starts else None,
        "turns_total": sum(_count(v.get("turns")) for v in visits),
        "estimated_cost_usd_total": round(
            sum(
                _finite_number(v.get("estimated_cost_usd")) or 0.0
                for v in visits
            ),
            6,
        ),
    }


# ---- the active session, read lock-free ------------------------------------

_PROVIDER_LOCK = threading.Lock()
_ACTIVE_PROVIDER: Callable[[], str] | None = None


def register_active_session_provider(provider: Callable[[], str] | None) -> None:
    """``sessions`` registers the workspace reader at import.

    A provider rather than an import so the log filter and the trace
    recorder never import the session store (and, through it, the whole
    engine) from inside a logging call.
    """
    global _ACTIVE_PROVIDER
    with _PROVIDER_LOCK:
        _ACTIVE_PROVIDER = provider


def active_session_uid() -> str:
    """The active workspace's session uid, or ``""``. Never raises, never
    blocks: it runs on every log line and every trace record."""
    provider = _ACTIVE_PROVIDER
    if provider is None:
        return ""
    try:
        return valid_session_uid(provider())
    except Exception:  # noqa: BLE001 - observability must stay fail-open
        return ""


# ---- the launch index and retention ----------------------------------------


def run_session_uids(meta: Any) -> tuple[str, ...]:
    """The valid ``session_uids`` a run marker or ``run.json`` names."""
    if not isinstance(meta, Mapping):
        return ()
    raw = meta.get("session_uids")
    if not isinstance(raw, list):
        return ()
    out: list[str] = []
    for value in raw[-MAX_RUN_SESSION_UIDS:]:
        uid = valid_session_uid(value)
        if uid and uid not in out:
            out.append(uid)
    return tuple(out)


def remember_uid(uids: list[str], uid: str) -> bool:
    """Append ``uid`` to a launch's bounded list; True when it was new."""
    if not valid_session_uid(uid) or uid in uids:
        return False
    uids.append(uid)
    del uids[:-MAX_RUN_SESSION_UIDS]
    return True


def session_history_days() -> int:
    """How long a session's launches outlive age/count pruning after its
    last activity. ``0`` turns the protection off."""
    raw = os.environ.get(ENV_SESSION_HISTORY_DAYS)
    if raw is None or not raw.strip():
        return DEFAULT_SESSION_HISTORY_DAYS
    try:
        value = int(raw.strip())
    except ValueError:
        return DEFAULT_SESSION_HISTORY_DAYS
    return max(0, value)


def live_session_uids(
    runs: Iterable[tuple[float, Sequence[str]]],
    *,
    now: float,
    days: int,
) -> set[str]:
    """Sessions with any tagged launch active within ``days`` of ``now``.

    ``runs`` pairs each launch's last-activity timestamp with its tags.
    """
    if days <= 0:
        return set()
    horizon = now - days * 24 * 60 * 60
    live: set[str] = set()
    for timestamp, uids in runs:
        if timestamp >= horizon:
            live.update(uids)
    return live
