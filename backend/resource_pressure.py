"""The resource pressure ledger: was an agent starved while it ran?

Build-a-Spec native (no Spec Critic source). A leaf like ``cost_checks``:
in-memory, per-process, lock-guarded, imported by the research engine, the
Final QC engine and the chat turn, and read by ``diagnostics.snapshot()``,
so Developer tools and the support bundle carry it.

An AGENT here is one unit of model work the app runs on its own thread or
conversation: a research area (``_run_dimension``), a Final QC lens,
grouping call or verifier seat (``_run_streaming_call`` and the batched
seats), or a chat turn (``stream_user_turn``). It is STARVED when it did
not get a resource it needed when it needed it. Each way that can happen
is one KIND of pressure in a closed vocabulary, grouped by what was short
(:data:`PRESSURE_SOURCES`):

- ``provider`` — the API's capacity: a 429, a 529/5xx, a dropped
  connection (each app-level retry, and the final failure that gave up), a
  retry the SDK made on its own before the app ever saw a failure, and a
  batched request that expired before the provider ran it.
- ``budget`` — one of the app's own allowances ran out and ended the
  exploration: searches, fetches, ``pause_turn`` continuations,
  missing-output-tool reminders, the chat's tool rounds, the batched
  phase's round and wall-clock ceilings.
- ``context`` — the context window: the reserve a request could not keep,
  the one-fetch clip, raw sources elided to fit a submission, a chat
  request the provider called too long.
- ``output`` — the output allowance: a reply cut at ``max_tokens``.
- ``scheduling`` — a turn to run: a pool worker the agent waited at least
  :data:`QUEUE_PRESSURE_MIN_MS` for, or a staggered launch's lead that
  never started streaming inside the bound (``timeout``), so its followers
  waited the whole wait for nothing.

Every kind is a starvation signal by construction, so an agent with any
recorded pressure is starved and a run with any starved agent is starved;
there is no threshold to tune beyond the queue wait's. A warm wait that
ended ``warm`` or ``stopped`` and a queue wait under the floor are recorded
as numbers on the agent, never as pressure: they are the launch working as
designed.

Why a ledger: the runners' event logs are cleared at every start and reach
the snapshot as a count; the per-area and per-seat records a profile or a
QC report persists carry billed usage and a failure kind, but nothing about
what a COMPLETED agent waited for or ran out of; the staggered launch's
wait outcome, the budget ceilings, the context clip and ``max_tokens`` were
INFO lines or nothing; and the SDK's own retries (``SDK_MAX_RETRIES`` per
request, so every failure the app records hides that many more attempts)
were invisible everywhere. The ledger records each of those at the spot in
the engine where it is observed.

What travels: closed tokens, ids the modules declare (area ids, lens ids,
seat keys made of candidate and reviewer numbers), counts, durations and
timestamps — never a message, a URL, a title or any text of the work. A
value the engines would hand it that is not one of its tokens is reduced
to ``unrecognized``, the way ``research.engine.sanitized_error_kind`` does
it, because the snapshot is handed to other people. Everything is bounded:
the last :data:`MAX_RUNS_PER_ENGINE` runs per engine, at most
:data:`MAX_AGENTS_PER_RUN` agents and :data:`MAX_EVENTS_PER_RUN` event
records per run (counts keep running past the caps; the drops are
counted), and the snapshot is shaped for ``tracing.redaction.scrub_data``'s
six-level bound: an agent's ``pressure_counts`` sit at the sixth level and
hold scalars only.

Handles are null-object safe: :data:`NO_RUN` and :data:`NO_AGENT` accept
every call and record nothing, so an engine function that takes a handle
can default to one and a direct caller — a test, a tool — changes nothing
by passing none. A handle keeps a reference to its own run, so a worker
that outlives a Stop and a successor run writes into the run it belongs
to, never the new one; a run evicted from the deque is simply unreachable.
Nothing here raises into an engine: every recording path swallows and
logs at DEBUG, like ``cost_checks``.
"""
from __future__ import annotations

import contextlib
import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterator

from . import settings

_log = logging.getLogger("buildaspec.resource_pressure")

# ---------------------------------------------------------------------------
# Vocabulary — closed, lower-case tokens. The frontend's formatter pins these
# names by reading this file (frontend/tests/resourcePressure.test.ts, the
# costChecks idiom), so one added on either side cannot drop out of the other.
# ---------------------------------------------------------------------------

ENGINE_RESEARCH = "research"
ENGINE_QC = "qc"
ENGINE_CHAT = "chat"
ENGINES = (ENGINE_RESEARCH, ENGINE_QC, ENGINE_CHAT)

SOURCE_PROVIDER = "provider"
SOURCE_BUDGET = "budget"
SOURCE_CONTEXT = "context"
SOURCE_OUTPUT = "output"
SOURCE_SCHEDULING = "scheduling"

# The provider's capacity.
KIND_RATE_LIMIT = "rate_limit"
KIND_SERVER_ERROR = "server_error"
KIND_CONNECTION = "connection"
KIND_SDK_RETRY = "sdk_retry"
KIND_BATCH_EXPIRED = "batch_expired"
# The app's own allowances.
KIND_SEARCH_CEILING = "search_ceiling"
KIND_FETCH_CEILING = "fetch_ceiling"
KIND_CONTINUATION_CEILING = "continuation_ceiling"
KIND_REMINDER_CEILING = "reminder_ceiling"
KIND_TOOL_ROUNDS_EXHAUSTED = "tool_rounds_exhausted"
KIND_BATCH_ROUND_CEILING = "batch_round_ceiling"
KIND_BATCH_WALL_CLOCK = "batch_wall_clock"
# The context window.
KIND_CONTEXT_RESERVE = "context_reserve"
KIND_NEAR_WINDOW_CLIP = "near_window_clip"
KIND_SUBMISSION_ELIDED = "submission_elided"
KIND_PROMPT_TOO_LONG = "prompt_too_long"
# The output allowance.
KIND_OUTPUT_TRUNCATED = "output_truncated"
# A turn to run.
KIND_QUEUED = "queued"
KIND_WARM_WAIT_TIMEOUT = "warm_wait_timeout"

PRESSURE_SOURCES: dict[str, str] = {
    KIND_RATE_LIMIT: SOURCE_PROVIDER,
    KIND_SERVER_ERROR: SOURCE_PROVIDER,
    KIND_CONNECTION: SOURCE_PROVIDER,
    KIND_SDK_RETRY: SOURCE_PROVIDER,
    KIND_BATCH_EXPIRED: SOURCE_PROVIDER,
    KIND_SEARCH_CEILING: SOURCE_BUDGET,
    KIND_FETCH_CEILING: SOURCE_BUDGET,
    KIND_CONTINUATION_CEILING: SOURCE_BUDGET,
    KIND_REMINDER_CEILING: SOURCE_BUDGET,
    KIND_TOOL_ROUNDS_EXHAUSTED: SOURCE_BUDGET,
    KIND_BATCH_ROUND_CEILING: SOURCE_BUDGET,
    KIND_BATCH_WALL_CLOCK: SOURCE_BUDGET,
    KIND_CONTEXT_RESERVE: SOURCE_CONTEXT,
    KIND_NEAR_WINDOW_CLIP: SOURCE_CONTEXT,
    KIND_SUBMISSION_ELIDED: SOURCE_CONTEXT,
    KIND_PROMPT_TOO_LONG: SOURCE_CONTEXT,
    KIND_OUTPUT_TRUNCATED: SOURCE_OUTPUT,
    KIND_QUEUED: SOURCE_SCHEDULING,
    KIND_WARM_WAIT_TIMEOUT: SOURCE_SCHEDULING,
}
PRESSURE_KINDS: tuple[str, ...] = tuple(PRESSURE_SOURCES)

# The retryable failure classes (``research.retry_policy.FailureClass``) and
# the pressure each one records. Deliberately spelled out rather than
# imported: this module is a leaf both engines import, and the classes'
# str values are already wire vocabulary ("for cheap telemetry").
RETRY_PRESSURE_KINDS: dict[str, str] = {
    "rate_limit": KIND_RATE_LIMIT,
    "server_error": KIND_SERVER_ERROR,
    "connection": KIND_CONNECTION,
}

# What an agent is. Ids come from the modules (area ids, lens ids) or from
# the engines' own key schemes (candidate and reviewer numbers), never from
# the work's text.
AGENT_DIMENSION = "dimension"
AGENT_LENS = "lens"
AGENT_CONSOLIDATION = "consolidation"
AGENT_VERIFIER = "verifier"
AGENT_TURN = "turn"
AGENT_KINDS = (
    AGENT_DIMENSION,
    AGENT_LENS,
    AGENT_CONSOLIDATION,
    AGENT_VERIFIER,
    AGENT_TURN,
)

# How an agent ended. ``interrupted`` is the run's word for an agent still
# running when the run ended — a worker that outlived a Stop, or a record
# the engine never closed; a later ``ended`` from it is ignored (first
# wins), so an outcome can never be rewritten.
OUTCOME_RUNNING = "running"
OUTCOME_COMPLETED = "completed"
OUTCOME_FAILED = "failed"
OUTCOME_CANCELLED = "cancelled"
OUTCOME_INTERRUPTED = "interrupted"
OUTCOMES = (
    OUTCOME_RUNNING,
    OUTCOME_COMPLETED,
    OUTCOME_FAILED,
    OUTCOME_CANCELLED,
    OUTCOME_INTERRUPTED,
)

# The staggered launch's wait outcomes, as both engines name them.
WARM_WAIT_OUTCOMES = ("warm", "timeout", "stopped")

# Run-level notes that are NOT pressure: numbers a reader needs beside the
# pressures to judge them. A batch round's wait is the ordinary cost of the
# Batches API, so it is noted, never counted as starvation.
NOTE_BATCH_ROUND = "batch_round"
NOTE_KINDS = (NOTE_BATCH_ROUND,)

RUN_RUNNING = "running"
RUN_ENDED = "ended"

# Bounds. Counts keep running past every cap; only records are dropped, and
# the drops are counted.
MAX_RUNS_PER_ENGINE = 8
MAX_AGENTS_PER_RUN = 200
MAX_EVENTS_PER_RUN = 300
# A pool worker wait shorter than this is thread scheduling, not pressure:
# recorded on the agent as ``queued_ms``, never as a ``queued`` pressure.
QUEUE_PRESSURE_MIN_MS = 1000

# The SDK logs each retry it makes on its own at INFO, from this logger and
# with this message prefix (``anthropic/_base_client.py``, ``log.info(
# "Retrying request to %s in %f seconds", ...)``). An observer attached to
# that logger counts them against the agent whose request is in flight on
# the logging thread — the sync SDK retries synchronously, so it is the same
# thread. A changed message or logger name blinds the observer silently;
# ``snapshot()`` discloses whether it is attached and whether the logger is
# enabled for INFO at all (``BUILD_A_SPEC_LOG_LEVEL=WARNING`` mutes it).
SDK_RETRY_LOGGER = "anthropic._base_client"
_SDK_RETRY_MESSAGE_PREFIX = "Retrying request"

UNRECOGNIZED = "unrecognized"

_TOKEN_RE = re.compile(r"^[a-z0-9_]{1,40}$")
_ID_BAD_RE = re.compile(r"[^A-Za-z0-9_.:-]")
_FIELD_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,29}$")
_MAX_ID_CHARS = 120
_MAX_LABEL_CHARS = 60
_MAX_FIELD_STR_CHARS = 60


# ---------------------------------------------------------------------------
# Sanitizers — the telemetry-safe guarantee is made here, once.
# ---------------------------------------------------------------------------


def _token(value: object, *, allowed: tuple[str, ...] | None = None) -> str:
    """A closed token, or ``unrecognized``; ``""`` stays ``""``."""
    if value is None:
        return ""
    text = str(value)
    if not text:
        return ""
    if allowed is not None:
        return text if text in allowed else UNRECOGNIZED
    return text if _TOKEN_RE.match(text) else UNRECOGNIZED


def _agent_id(value: object) -> str:
    text = _ID_BAD_RE.sub("_", str(value or ""))[:_MAX_ID_CHARS]
    return text or "agent"


def _label(value: object) -> str:
    text = "".join(ch for ch in str(value or "") if ch.isprintable())
    return " ".join(text.split())[:_MAX_LABEL_CHARS]


def _scalar(value: object) -> Any:
    """A scalar the snapshot may carry, or ``None`` for anything else."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value, 3) if value == value else None  # NaN never travels
    if isinstance(value, str):
        return value[:_MAX_FIELD_STR_CHARS]
    return None


def _fields(fields: dict[str, Any]) -> dict[str, Any]:
    """The scalar fields of an event record, keys and values both bounded."""
    out: dict[str, Any] = {}
    for key, value in fields.items():
        name = str(key)
        if not _FIELD_KEY_RE.match(name):
            continue
        scalar = _scalar(value)
        if scalar is None and value is not None:
            continue
        out[name] = scalar
    return out


def retry_after_seconds(exc: BaseException) -> float | None:
    """The ``retry-after`` header of a provider error, in seconds, or None.

    Duck-typed over the SDK's ``APIStatusError.response.headers`` so this
    leaf never imports the SDK. Only the delay-seconds form is read (an
    HTTP-date form is left as ``None``), and a negative value is nonsense.
    """
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        raw = headers.get("retry-after")
    except Exception:  # noqa: BLE001 — a header map that is not a mapping
        return None
    if raw is None:
        return None
    try:
        value = float(str(raw).strip())
    except ValueError:
        return None
    return value if value >= 0 else None


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass
class _Agent:
    agent_id: str
    kind: str
    submitted_mono: float | None = None
    started_mono: float | None = None
    ended_mono: float | None = None
    started_at: float | None = None
    ended_at: float | None = None
    outcome: str = OUTCOME_RUNNING
    error_kind: str = ""
    attempts: int = 0
    queued_ms: int | None = None
    warm_wait_outcome: str = ""
    warm_wait_ms: int | None = None
    warm_lead: str = ""
    backoff_s: float = 0.0
    sdk_retries: int = 0
    sdk_sleep_s: float = 0.0
    pressure_counts: dict[str, int] = field(default_factory=dict)

    @property
    def starved(self) -> bool:
        return bool(self.pressure_counts)


@dataclass
class _Run:
    engine: str
    run_id: str
    label: str
    sequence: int
    started_at: float
    started_mono: float
    status: str = RUN_RUNNING
    ended_at: float | None = None
    agents: dict[str, _Agent] = field(default_factory=dict)
    agents_dropped: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)
    events_dropped: int = 0
    pressure_counts: dict[str, int] = field(default_factory=dict)
    backoff_s: float = 0.0
    sdk_retries: int = 0
    batch_rounds: int = 0
    batch_wait_ms: int = 0
    counted_in_totals: bool = False

    @property
    def starved_agents(self) -> int:
        return sum(1 for agent in self.agents.values() if agent.starved)


@dataclass
class _Totals:
    runs: int = 0
    runs_starved: int = 0
    agents: int = 0
    starved_agents: int = 0
    backoff_s: float = 0.0
    sdk_retries: int = 0
    pressure_counts: dict[str, int] = field(default_factory=dict)


@dataclass
class _EngineState:
    runs: deque[_Run] = field(
        default_factory=lambda: deque(maxlen=MAX_RUNS_PER_ENGINE)
    )
    runs_recorded: int = 0
    totals: _Totals = field(default_factory=_Totals)


@dataclass
class _Unattributed:
    """SDK retries seen on a thread with no agent in flight (the QC batch
    submission, a key probe) — counted so they are not lost, never guessed
    onto an agent."""

    retries: int = 0
    sleep_s: float = 0.0


_lock = threading.Lock()
_engines: dict[str, _EngineState] = {engine: _EngineState() for engine in ENGINES}
_unattributed = _Unattributed()
_current = threading.local()
_run_counter = 0


def _now() -> tuple[float, float]:
    return time.time(), time.monotonic()


def _increment(counts: dict[str, int], key: str) -> None:
    counts[key] = counts.get(key, 0) + 1


# ---------------------------------------------------------------------------
# Handles
# ---------------------------------------------------------------------------


class AgentPressure:
    """One agent's handle. Every method is a no-op on :data:`NO_AGENT`."""

    __slots__ = ("_run", "_agent")

    def __init__(self, run: _Run | None, agent: _Agent | None) -> None:
        self._run = run
        self._agent = agent

    @property
    def active(self) -> bool:
        return self._agent is not None

    @property
    def agent_id(self) -> str:
        return self._agent.agent_id if self._agent is not None else ""

    def submitted(self) -> None:
        """The agent was handed to a pool; its queue wait starts now."""
        if self._agent is None:
            return
        try:
            with _lock:
                if self._agent.submitted_mono is None:
                    self._agent.submitted_mono = time.monotonic()
        except Exception:  # noqa: BLE001 — the ledger never fails an engine
            _log.debug("could not record a submission", exc_info=True)

    def started(self, *, in_flight: bool = True) -> None:
        """The agent's own work began.

        Records the queue wait since :meth:`submitted` (a ``queued``
        pressure once it reaches :data:`QUEUE_PRESSURE_MIN_MS`). With
        ``in_flight`` (the default) this thread's requests are the agent's
        until :meth:`ended`, so the SDK retry observer can attribute what
        it sees; a batched seat, whose requests ride a batch the
        coordinator submits, passes ``False`` so the coordinator's own
        retries are never pinned on whichever seat was named last.
        """
        if self._agent is None:
            return
        try:
            wall, mono = _now()
            queued: int | None = None
            with _lock:
                agent = self._agent
                if agent.started_mono is None:
                    agent.started_mono = mono
                    agent.started_at = wall
                    if agent.submitted_mono is not None:
                        queued = max(0, int((mono - agent.submitted_mono) * 1000))
                        agent.queued_ms = queued
            if in_flight:
                _current.agent = self
            if queued is not None and queued >= QUEUE_PRESSURE_MIN_MS:
                self.pressure(KIND_QUEUED, queued_ms=queued)
        except Exception:  # noqa: BLE001
            _log.debug("could not record a start", exc_info=True)

    def pressure(self, kind: str, **fields: Any) -> None:
        """Record one pressure of ``kind`` (a :data:`PRESSURE_KINDS` token)."""
        if self._agent is None:
            return
        try:
            token = _token(kind, allowed=PRESSURE_KINDS)
            if token == UNRECOGNIZED:
                _log.debug("ignoring an unknown pressure kind %r", kind)
                return
            with _lock:
                _record_locked(self._run, self._agent, token, _fields(fields))
        except Exception:  # noqa: BLE001
            _log.debug("could not record a pressure", exc_info=True)

    def retry(
        self,
        *,
        failure_class: str,
        attempt: int,
        max_attempts: int,
        backoff_s: float,
        mode: str = "",
        retry_after_s: float | None = None,
        final: bool = False,
    ) -> None:
        """An app-level retry (or, with ``final``, the failure that gave up)
        after a retryable provider failure.

        ``failure_class`` is the engines' ``FailureClass`` value; a class
        this ledger has no pressure for records nothing. ``backoff_s`` is
        what the engine is about to sleep (0 for the final failure) and
        accumulates on the agent and the run — the seconds the provider cost
        the work. ``retry_after_s`` is the provider's own ask, when it sent
        one (:func:`retry_after_seconds`).
        """
        if self._agent is None:
            return
        kind = RETRY_PRESSURE_KINDS.get(str(failure_class))
        if kind is None:
            _log.debug("no pressure kind for failure class %r", failure_class)
            return
        try:
            backoff = max(0.0, float(backoff_s or 0.0))
            with _lock:
                self._agent.backoff_s += backoff
                self._run.backoff_s += backoff
                _record_locked(
                    self._run,
                    self._agent,
                    kind,
                    _fields(
                        {
                            "attempt": int(attempt),
                            "max_attempts": int(max_attempts),
                            "backoff_s": round(backoff, 2),
                            "mode": mode or None,
                            "retry_after_s": retry_after_s,
                            "final": True if final else None,
                        }
                    ),
                )
        except Exception:  # noqa: BLE001
            _log.debug("could not record a retry", exc_info=True)

    def warm_wait(self, *, outcome: str, waited_ms: int, lead: str = "") -> None:
        """How this follower's wait for its lineage's lead ended.

        ``warm`` and ``stopped`` are numbers on the agent; ``timeout`` is a
        ``warm_wait_timeout`` pressure too — the follower waited the whole
        bound for a lead that never started streaming.
        """
        if self._agent is None:
            return
        try:
            token = _token(outcome, allowed=WARM_WAIT_OUTCOMES)
            waited = max(0, int(waited_ms))
            with _lock:
                self._agent.warm_wait_outcome = token
                self._agent.warm_wait_ms = waited
                self._agent.warm_lead = _agent_id(lead) if lead else ""
            if token == "timeout":
                self.pressure(
                    KIND_WARM_WAIT_TIMEOUT,
                    waited_ms=waited,
                    lead=_agent_id(lead) if lead else None,
                )
        except Exception:  # noqa: BLE001
            _log.debug("could not record a warm wait", exc_info=True)

    def sdk_retry(self, sleep_s: float | None) -> None:
        """A retry the SDK made on its own for this agent's request."""
        if self._agent is None:
            return
        try:
            sleep = max(0.0, float(sleep_s)) if sleep_s is not None else 0.0
            with _lock:
                self._agent.sdk_retries += 1
                self._agent.sdk_sleep_s += sleep
                self._run.sdk_retries += 1
                _record_locked(
                    self._run,
                    self._agent,
                    KIND_SDK_RETRY,
                    _fields({"sleep_s": round(sleep, 2) if sleep_s is not None else None}),
                )
        except Exception:  # noqa: BLE001
            _log.debug("could not record an SDK retry", exc_info=True)

    def ended(
        self, outcome: str, *, error_kind: str = "", attempts: int | None = None
    ) -> None:
        """The agent's terminal outcome. The first call wins; later ones are
        ignored, so a worker that outlives its run cannot rewrite the record
        the run closed."""
        if self._agent is None:
            return
        try:
            token = _token(outcome, allowed=OUTCOMES)
            if token in (UNRECOGNIZED, OUTCOME_RUNNING):
                token = OUTCOME_FAILED
            wall, mono = _now()
            with _lock:
                agent = self._agent
                if agent.outcome == OUTCOME_RUNNING:
                    agent.outcome = token
                    agent.error_kind = _token(error_kind)
                    if attempts is not None:
                        agent.attempts = max(0, int(attempts))
                    agent.ended_mono = mono
                    agent.ended_at = wall
            if getattr(_current, "agent", None) is self:
                _current.agent = None
        except Exception:  # noqa: BLE001
            _log.debug("could not record an outcome", exc_info=True)

    @contextlib.contextmanager
    def requesting(self) -> Iterator[None]:
        """Make this the thread's agent in flight for one request.

        For an agent whose work hops threads between requests (the chat turn
        is a generator the server iterates from a thread pool): the SDK's
        retries happen inside the request call, on whatever thread runs it,
        so attribution follows the call rather than the thread.
        """
        previous = getattr(_current, "agent", None)
        if self._agent is not None:
            _current.agent = self
        try:
            yield
        finally:
            _current.agent = previous


class RunPressure:
    """One run's handle (a research round, a Final QC run, a chat turn).
    Every method is a no-op on :data:`NO_RUN`."""

    __slots__ = ("_run",)

    def __init__(self, run: _Run | None) -> None:
        self._run = run

    @property
    def active(self) -> bool:
        return self._run is not None

    @property
    def run_id(self) -> str:
        return self._run.run_id if self._run is not None else ""

    @property
    def engine(self) -> str:
        return self._run.engine if self._run is not None else ""

    def agent(self, agent_id: str, *, kind: str = "") -> AgentPressure:
        """This run's agent ``agent_id``, created on first mention.

        Idempotent and order-free: the staggered launch names a follower
        (its warm wait) before the pool starts it, and the coordinator may
        close an agent whose worker raised. Past :data:`MAX_AGENTS_PER_RUN`
        a new id gets :data:`NO_AGENT` and is counted as dropped.
        """
        if self._run is None:
            return NO_AGENT
        try:
            ident = _agent_id(agent_id)
            agent_kind = _token(kind, allowed=AGENT_KINDS) if kind else ""
            with _lock:
                run = self._run
                agent = run.agents.get(ident)
                if agent is None:
                    if len(run.agents) >= MAX_AGENTS_PER_RUN:
                        run.agents_dropped += 1
                        return NO_AGENT
                    agent = _Agent(agent_id=ident, kind=agent_kind)
                    run.agents[ident] = agent
                elif agent_kind and not agent.kind:
                    agent.kind = agent_kind
            return AgentPressure(run, agent)
        except Exception:  # noqa: BLE001
            _log.debug("could not create an agent record", exc_info=True)
            return NO_AGENT

    def note(self, kind: str, **fields: Any) -> None:
        """A run-level note (a :data:`NOTE_KINDS` token) — numbers beside
        the pressures, never a pressure itself."""
        if self._run is None:
            return
        try:
            token = _token(kind, allowed=NOTE_KINDS)
            if token == UNRECOGNIZED:
                _log.debug("ignoring an unknown note kind %r", kind)
                return
            clean = _fields(fields)
            with _lock:
                run = self._run
                if token == NOTE_BATCH_ROUND:
                    run.batch_rounds += 1
                    waited = clean.get("waited_ms")
                    if isinstance(waited, int) and not isinstance(waited, bool):
                        run.batch_wait_ms += max(0, waited)
                _append_event_locked(run, "", token, clean)
        except Exception:  # noqa: BLE001
            _log.debug("could not record a note", exc_info=True)

    def end(self) -> None:
        """Close the run: agents still running are ``interrupted`` and the
        run's numbers join its engine's totals. Idempotent."""
        if self._run is None:
            return
        try:
            wall, mono = _now()
            with _lock:
                run = self._run
                if run.status == RUN_ENDED:
                    return
                run.status = RUN_ENDED
                run.ended_at = wall
                for agent in run.agents.values():
                    if agent.outcome == OUTCOME_RUNNING:
                        agent.outcome = OUTCOME_INTERRUPTED
                        agent.ended_mono = mono
                        agent.ended_at = wall
                totals = _engines[run.engine].totals
                if not run.counted_in_totals:
                    run.counted_in_totals = True
                    totals.runs += 1
                    totals.runs_starved += 1 if run.starved_agents else 0
                    totals.agents += len(run.agents) + run.agents_dropped
                    totals.starved_agents += run.starved_agents
                    totals.backoff_s += run.backoff_s
                    totals.sdk_retries += run.sdk_retries
                    for kind, count in run.pressure_counts.items():
                        totals.pressure_counts[kind] = (
                            totals.pressure_counts.get(kind, 0) + count
                        )
        except Exception:  # noqa: BLE001
            _log.debug("could not end a run record", exc_info=True)

    def __enter__(self) -> "RunPressure":
        return self

    def __exit__(self, *exc: object) -> None:
        self.end()


NO_RUN = RunPressure(None)
NO_AGENT = AgentPressure(None, None)


def _record_locked(
    run: _Run | None, agent: _Agent, kind: str, fields: dict[str, Any]
) -> None:
    _increment(agent.pressure_counts, kind)
    if run is not None:
        _increment(run.pressure_counts, kind)
        _append_event_locked(run, agent.agent_id, kind, fields)


def _append_event_locked(
    run: _Run, agent_id: str, kind: str, fields: dict[str, Any]
) -> None:
    if len(run.events) >= MAX_EVENTS_PER_RUN:
        run.events_dropped += 1
        return
    wall, mono = _now()
    record: dict[str, Any] = {
        "at": wall,
        "elapsed_ms": max(0, int((mono - run.started_mono) * 1000)),
        "agent": agent_id,
        "kind": kind,
    }
    for key, value in fields.items():
        if key not in record and value is not None:
            record[key] = value
    run.events.append(record)


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


def begin_run(engine: str, *, run_id: str = "", label: str = "") -> RunPressure:
    """Open a run for ``engine`` (an :data:`ENGINES` token) and return its
    handle. An unknown engine returns :data:`NO_RUN`. Attaches the SDK retry
    observer on first use."""
    global _run_counter
    try:
        token = _token(engine, allowed=ENGINES)
        if token == UNRECOGNIZED:
            _log.debug("ignoring a run for unknown engine %r", engine)
            return NO_RUN
        install_sdk_retry_observer()
        wall, mono = _now()
        with _lock:
            _run_counter += 1
            state = _engines[token]
            state.runs_recorded += 1
            run = _Run(
                engine=token,
                run_id=_agent_id(run_id) if run_id else f"{token}-{_run_counter}",
                label=_label(label) or token,
                sequence=_run_counter,
                started_at=wall,
                started_mono=mono,
            )
            state.runs.append(run)
        return RunPressure(run)
    except Exception:  # noqa: BLE001
        _log.debug("could not begin a run record", exc_info=True)
        return NO_RUN


# ---------------------------------------------------------------------------
# The SDK retry observer
# ---------------------------------------------------------------------------


class _SdkRetryObserver(logging.Handler):
    """Counts the SDK's own retries off its INFO log line (see
    :data:`SDK_RETRY_LOGGER`). Never raises out of ``emit``."""

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
        try:
            message = str(record.msg)
            if not message.startswith(_SDK_RETRY_MESSAGE_PREFIX):
                return
            sleep: float | None = None
            args = record.args
            if isinstance(args, tuple) and len(args) >= 2:
                candidate = args[1]
                if isinstance(candidate, (int, float)) and not isinstance(
                    candidate, bool
                ):
                    sleep = float(candidate)
            record_sdk_retry(sleep)
        except Exception:  # noqa: BLE001 — an observer never breaks logging
            pass


_observer: _SdkRetryObserver | None = None
_observer_lock = threading.Lock()


def install_sdk_retry_observer() -> None:
    """Attach the observer to the SDK's logger once (idempotent)."""
    global _observer
    with _observer_lock:
        if _observer is not None:
            return
        handler = _SdkRetryObserver()
        logging.getLogger(SDK_RETRY_LOGGER).addHandler(handler)
        _observer = handler


def record_sdk_retry(sleep_s: float | None) -> None:
    """Attribute one SDK retry to the thread's agent in flight, or count it
    as unattributed."""
    agent = getattr(_current, "agent", None)
    if isinstance(agent, AgentPressure) and agent.active:
        agent.sdk_retry(sleep_s)
        return
    try:
        with _lock:
            _unattributed.retries += 1
            if sleep_s is not None:
                _unattributed.sleep_s += max(0.0, float(sleep_s))
    except Exception:  # noqa: BLE001
        _log.debug("could not count an unattributed SDK retry", exc_info=True)


def _observer_state_locked() -> dict[str, Any]:
    logger = logging.getLogger(SDK_RETRY_LOGGER)
    return {
        "attached": _observer is not None,
        "logger": SDK_RETRY_LOGGER,
        # Whether the SDK's INFO line can reach the observer at all: a
        # ``BUILD_A_SPEC_LOG_LEVEL`` above INFO mutes the whole tree.
        "listening": _observer is not None and logger.isEnabledFor(logging.INFO),
        "unattributed_retries": _unattributed.retries,
        "unattributed_sleep_s": round(_unattributed.sleep_s, 2),
    }


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


def _agent_view(agent: _Agent) -> dict[str, Any]:
    duration: int | None = None
    if agent.started_mono is not None:
        end = agent.ended_mono if agent.ended_mono is not None else time.monotonic()
        duration = max(0, int((end - agent.started_mono) * 1000))
    return {
        "kind": agent.kind,
        "outcome": agent.outcome,
        "error_kind": agent.error_kind,
        "attempts": agent.attempts,
        "started_at": agent.started_at,
        "ended_at": agent.ended_at,
        "duration_ms": duration,
        "queued_ms": agent.queued_ms,
        "warm_wait_outcome": agent.warm_wait_outcome,
        "warm_wait_ms": agent.warm_wait_ms,
        "warm_lead": agent.warm_lead,
        "backoff_s": round(agent.backoff_s, 2),
        "sdk_retries": agent.sdk_retries,
        "sdk_sleep_s": round(agent.sdk_sleep_s, 2),
        "starved": agent.starved,
        # Sixth level of the diagnostics payload: scalars only below here.
        "pressure_counts": dict(agent.pressure_counts),
    }


def _run_view(run: _Run) -> dict[str, Any]:
    by_outcome = {outcome: 0 for outcome in OUTCOMES}
    queued_max = 0
    for agent in run.agents.values():
        by_outcome[agent.outcome] = by_outcome.get(agent.outcome, 0) + 1
        if agent.queued_ms:
            queued_max = max(queued_max, agent.queued_ms)
    # The wall clocks are what the snapshot carries; an ended run's duration
    # comes from them too, so a reader can reconcile the two.
    duration_ms = (
        max(0, int((run.ended_at - run.started_at) * 1000))
        if run.ended_at is not None
        else max(0, int((time.monotonic() - run.started_mono) * 1000))
    )
    return {
        "engine": run.engine,
        "run_id": run.run_id,
        "label": run.label,
        "status": run.status,
        "started_at": run.started_at,
        "ended_at": run.ended_at,
        "duration_ms": duration_ms,
        "starved": run.starved_agents > 0,
        "starved_agents": run.starved_agents,
        "agents_total": len(run.agents),
        "agents_running": by_outcome[OUTCOME_RUNNING],
        "agents_completed": by_outcome[OUTCOME_COMPLETED],
        "agents_failed": by_outcome[OUTCOME_FAILED],
        "agents_cancelled": by_outcome[OUTCOME_CANCELLED],
        "agents_interrupted": by_outcome[OUTCOME_INTERRUPTED],
        "agents_dropped": run.agents_dropped,
        "pressure_counts": dict(run.pressure_counts),
        "backoff_s": round(run.backoff_s, 2),
        "sdk_retries": run.sdk_retries,
        "queued_max_ms": queued_max,
        "batch_rounds": run.batch_rounds,
        "batch_wait_ms": run.batch_wait_ms,
        "agents": {ident: _agent_view(agent) for ident, agent in run.agents.items()},
        "events": [dict(event) for event in run.events],
        "events_dropped": run.events_dropped,
    }


def _totals_view(state: _EngineState) -> dict[str, Any]:
    totals = state.totals
    return {
        "runs_recorded": state.runs_recorded,
        "runs_kept": len(state.runs),
        "runs_ended": totals.runs,
        "runs_starved": totals.runs_starved,
        "agents": totals.agents,
        "starved_agents": totals.starved_agents,
        "backoff_s": round(totals.backoff_s, 2),
        "sdk_retries": totals.sdk_retries,
        "pressure_counts": dict(totals.pressure_counts),
    }


def snapshot() -> dict[str, Any]:
    """What the ledger holds, for diagnostics. Never raises.

    ``{"schema_version", "sdk_retries_per_request", "queue_pressure_min_ms",
    "max_runs_per_engine", "sdk_retry_observer": {attached, logger,
    listening, unattributed_retries, unattributed_sleep_s}, "totals":
    {engine: {runs_recorded, runs_kept, runs_ended, runs_starved, agents,
    starved_agents, backoff_s, sdk_retries, pressure_counts}}, "runs":
    [run, ...]}`` — the kept runs of every engine, newest first. A run is
    ``{engine, run_id, label, status, started_at, ended_at, duration_ms,
    starved, starved_agents, agents_total, agents_running,
    agents_completed, agents_failed, agents_cancelled, agents_interrupted,
    agents_dropped, pressure_counts, backoff_s, sdk_retries, queued_max_ms,
    batch_rounds, batch_wait_ms, agents: {id: agent}, events: [...],
    events_dropped}``; an agent is ``{kind, outcome, error_kind, attempts,
    started_at, ended_at, duration_ms, queued_ms, warm_wait_outcome,
    warm_wait_ms, warm_lead, backoff_s, sdk_retries, sdk_sleep_s, starved,
    pressure_counts}``; an event is ``{at, elapsed_ms, agent, kind, ...}``
    with scalar fields. Totals count ENDED runs; a running run's numbers
    are on the run itself. Read in one lock acquisition, so the whole
    snapshot describes one moment.
    """
    try:
        with _lock:
            kept = [
                run
                for state in _engines.values()
                for run in state.runs
            ]
            # Wall-clock ticks can tie on Windows or move backwards. The
            # sequence records creation order across every engine under this
            # same lock, without adding anything to the public snapshot.
            kept.sort(key=lambda run: run.sequence, reverse=True)
            runs = [_run_view(run) for run in kept]
            totals = {engine: _totals_view(state) for engine, state in _engines.items()}
            observer = _observer_state_locked()
        return {
            "schema_version": 1,
            "sdk_retries_per_request": int(settings.SDK_MAX_RETRIES),
            "queue_pressure_min_ms": QUEUE_PRESSURE_MIN_MS,
            "max_runs_per_engine": MAX_RUNS_PER_ENGINE,
            "sdk_retry_observer": observer,
            "totals": totals,
            "runs": runs,
        }
    except Exception:  # noqa: BLE001 — diagnostics must not fail on the ledger
        _log.debug("could not snapshot the resource pressure ledger", exc_info=True)
        return {}


def reset_for_tests() -> None:
    """Clear every run, total and unattributed count (the conftest calls
    this around every test). The observer stays attached: it is process
    state, like a log handler."""
    with _lock:
        for engine in ENGINES:
            _engines[engine] = _EngineState()
        _unattributed.retries = 0
        _unattributed.sleep_s = 0.0
    _current.agent = None
