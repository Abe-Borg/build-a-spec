"""Session-bound research run lifecycle: background thread, event log, SSE.

Build-a-Spec-specific (no Spec Critic source): the engine
(:mod:`.engine`) is a pure synchronous function; this module gives the
FastAPI layer something to start, watch, and stream. One
:class:`ResearchRunner` lives on the session (``SessionState.research``);
``reset()`` / project load replace it, so a thread still running against
the old session finishes into an abandoned object — the zombie-turn
pattern from the conversation engine, applied to research.

Event log entries are plain dicts ``{seq, ts, type, ...}``; the SSE
endpoint replays them from any ``seq`` and follows until the run reaches a
terminal state, so a page reload (or a test) can always catch up. Workers
emit fine-grained live progress (dimension started/activity/search/fetch/
retry), so each run is identified by an unforgeable per-start token: a
stopped run's still-unwinding threads can neither append to a later
round's log nor resolve over it.

Ending a run is ONE transaction (:meth:`ResearchRunner._try_resolve`).
Everything a terminal transition publishes — the merged profile, the
error, the cancel signal, the terminal event, the trace handle, the
status — moves under a single acquisition of the runner lock, and the
status is published last. Splitting any of it back out reopens a race
against the very next ``start()``: a successor round clears the event log
and installs its own cancel event, so a post-lock append lands in the
wrong round's log and a post-lock ``cancel_event.set()`` cancels a run
the user never stopped.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Callable

from ..project_profile import ProjectProfile
from ..tracing import capture as _trace
from .engine import (
    RequirementsProfile,
    ResearchFanoutError,
    append_research_round,
    incomplete_dimension_facts,
    run_requirements_research,
    select_research_dimensions,
)

STATUS_IDLE = "idle"
STATUS_RUNNING = "running"
STATUS_COMPLETE = "complete"
STATUS_FAILED = "failed"

_TERMINAL = (STATUS_COMPLETE, STATUS_FAILED)


@dataclass
class _Resolution:
    """Everything a winning terminal transition published.

    Handed back to whichever caller won the compare-and-set so it never has
    to reread a mutable runner attribute after the lock is released — by
    then a successor round may already own ``status``, ``error``, the
    cancel event and the event log. The loser gets ``None`` and does
    nothing at all, which is what makes "exactly one of stop and the worker
    closes the trace span" true by construction.
    """

    status: str
    error: str
    error_kind: str
    round_number: int
    profile: RequirementsProfile | None = None
    #: The terminal log entry as it landed (seq/ts/round stamped).
    event: dict[str, Any] | None = None
    #: Claimed from the runner — the winner closes it, exactly once.
    trace_handle: Any | None = None


def _research_failed_event(resolution: _Resolution) -> dict[str, Any]:
    """The terminal event every failure path publishes — one shape, one place.

    Fan-out failure, an unexpected exception, and a user stop all end the
    same way as far as a follower is concerned; only the message differs.
    """
    return {
        "type": "research_failed",
        "error": resolution.error,
        **(
            {"error_kind": resolution.error_kind}
            if resolution.error_kind
            else {}
        ),
    }


class ResearchRunner:
    """One session's research state machine.

    States: ``idle`` → ``running`` → ``complete`` | ``failed``. A completed
    runner holds the :class:`RequirementsProfile` the conversation engine
    splices into the dynamic context. ``restore()`` rebuilds a completed
    runner from a project file.

    Runs ACCUMULATE. The Research button can be pressed any number of times
    in a session, and each press appends a round to the profile rather than
    replacing it (:func:`.engine.append_research_round`) — earlier findings
    stay in the drafting context, keep their ``[r-…]`` ids for the
    provenance chips provisions already cite, and are never quietly
    unbought. A run that fails or is stopped leaves those earlier rounds
    exactly as they were; only the round in flight is lost.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel_event: threading.Event | None = None
        # The round the active run is (1-based) — so a terminal event
        # raised outside the worker, i.e. stop(), can name its round too.
        self._round_number = 0
        # Unforgeable identity of the run in flight (QCRunner pattern):
        # minted per start(), cleared on any terminal resolve. Worker
        # events and the worker's own resolution carry it, so a stopped
        # run's still-unwinding threads can neither append to a later
        # round's log nor adopt their discarded round into its profile.
        self._run_token: object | None = None
        # The run's open trace span, claimed by whichever caller resolves the
        # run — so a stopped run's span is closed instead of hanging open
        # forever (which the trace viewer renders as a crash).
        self._trace_handle: Any | None = None
        self.status = STATUS_IDLE
        self.error = ""
        self.error_kind = ""
        self.profile_result: RequirementsProfile | None = None
        self.events: list[dict[str, Any]] = []

    # -- events --------------------------------------------------------------

    def _append_event_locked(
        self, event: dict[str, Any], *, round_number: int = 0
    ) -> dict[str, Any]:
        """Append one event while the caller already holds ``self._lock``.

        Returns the stamped entry. The terminal transition appends through
        this rather than :meth:`_emit`, so publishing the terminal status
        and its event is one critical section: ``sse_events`` decides
        "terminal and drained" from exactly this pair under exactly this
        lock, and a window between them lets a follower reach ``stream_end``
        without ever being sent the terminal frame.
        """
        stamped = dict(event)
        stamped["seq"] = len(self.events)
        stamped["ts"] = time.strftime("%H:%M:%S")
        if round_number:
            stamped["round"] = round_number
        self.events.append(stamped)
        return stamped

    def _emit(
        self,
        event: dict[str, Any],
        *,
        round_number: int = 0,
        run_token: object | None = None,
        mirror: Callable[[dict[str, Any]], None] | None = None,
    ) -> bool:
        """Append to the event log; True if the event landed.

        A ``run_token`` that no longer matches the runner's current one is
        a stale worker talking past its run's end — the event is dropped.

        ``mirror`` (the trace) runs INSIDE the critical section, and has to.
        The terminal transition claims the trace handle at this same lock
        and closes the span only afterwards, so an event accepted here is
        always enqueued on the recorder's queue — which is FIFO — before
        the close is. Mirroring after the release instead let a dimension
        thread preempted in that gap record progress against a span a
        concurrent stop had already ended, timestamping it past its own
        ``ended_at``. Only possible since stops began closing the span at
        all; the whole point of doing that is a truthful timeline.
        """
        with self._lock:
            if run_token is not None and run_token is not self._run_token:
                return False
            self._append_event_locked(event, round_number=round_number)
            if mirror is not None:
                mirror(event)
            return True

    def events_since(self, seq: int) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.events[seq:])

    # -- lifecycle -----------------------------------------------------------

    def start(
        self,
        *,
        module: Any,
        project_profile: ProjectProfile,
        client: Any,
        model: str,
        max_tokens: int,
        discipline: str = "",
        dimension_ids: Sequence[str] | None = None,
        reference_docs: Sequence[Any] | None = None,
        section_label: str = "",
        project_facts: Sequence[Any] | None = None,
        on_settled: Callable[[], None] | None = None,
        usage_sink: Callable[[dict], None] | None = None,
    ) -> bool:
        """Kick off the fan-out on a daemon thread. False if already running.

        ``project_facts`` are the session's active established facts; they
        are briefed to every dimension beside the attached documents and
        snapshotted here for the same reason those are.

        ``section_label`` is the section number of the session pressing
        Research; it is stamped on the round record so a section seeded
        from a project brief can tell the rounds it ran from the ones it
        was handed. Captured under the lock beside the round number.

        ``dimension_ids`` scopes the round to a subset of the module's
        declared dimensions (``None`` runs them all). The accumulated
        profile is passed to the engine as the round's ESTABLISHED facts,
        so a later round is briefed on what the session already knows
        rather than re-deriving it — captured here, under the same lock
        that numbers the round, so the brief and the round number describe
        the same moment.

        ``reference_docs`` are the documents the user attached to the
        project. They are rendered into every dimension's brief verbatim (see
        ``engine.reference_context_block``) so the round researches what the
        owner actually requires rather than re-deriving it. Captured here
        under the same lock as ``established``, so the round's brief and its
        number describe one moment.

        ``on_settled`` (optional) runs after the terminal state is set —
        the app layer uses it for nothing today but tests can synchronize
        on it.

        ``usage_sink`` (optional) receives THIS round's own billed usage,
        once, whatever the outcome: the merged profile's total on success,
        and the failed round's aggregate when every dimension failed or was
        cancelled. A discarded round is still a paid round. It is never fed
        the cumulative profile total, which would re-bill every earlier
        round each time a new one lands.

        The existing ``profile_result`` is deliberately NOT cleared: this
        run is the next round on top of it, and until it resolves the model
        keeps drafting from the research the session already paid for. The
        event log IS cleared — it is this round's progress log, and the
        accumulated knowledge lives in the profile.
        """
        with self._lock:
            if self.status == STATUS_RUNNING:
                return False
            self.status = STATUS_RUNNING
            self.error = ""
            self.error_kind = ""
            self.events = []
            round_number = (
                self.profile_result.round_count + 1
                if self.profile_result is not None
                else 1
            )
            self._round_number = round_number
            # The round's brief is written against the profile as it stands
            # right now. Captured under the lock beside the round number,
            # rather than read from `_work` later: only this run can write
            # `profile_result` while it is running, but a snapshot taken
            # here cannot disagree with the round it is numbering.
            established = self.profile_result
            # Snapshotted with the round it briefs, for the same reason: a
            # document attached while this round is in flight belongs to the
            # next one, not to workers already briefed and already paying for
            # a cached prefix built without it.
            briefing_docs = list(reference_docs or [])
            briefing_facts = list(project_facts or [])
            section = " ".join(section_label.split())
            cancel_event = threading.Event()
            self._cancel_event = cancel_event
            run_token = object()
            self._run_token = run_token

        trace_handle = _trace.research_start(
            project=project_profile.display_line(),
            # The count this round will actually run, not what the module
            # declares — a span that claimed four on a two-dimension retry
            # would read as two silently missing workers.
            dimensions=len(select_research_dimensions(module, dimension_ids)),
        )
        # Opened AFTER the compare-and-set, so a losing double-start never
        # fabricates a run span — which leaves a narrow window in which a
        # stop can already have resolved this run. Adopt the handle against
        # our own token; if the run is over already, close the span right
        # here, because by then nobody else holds it.
        orphaned: tuple[str, str] | None = None
        with self._lock:
            if self._run_token is run_token:
                self._trace_handle = trace_handle
            else:
                # Normally the stop's own terminal state. Only read it when
                # it is still ours to read — a successor that also started in
                # this window owns `status`/`error` now, and reporting its
                # `running` as this span's outcome would be a false record.
                orphaned = (
                    (self.status, self.error)
                    if self.status in _TERMINAL
                    else (
                        STATUS_FAILED,
                        "The run ended before its trace span was adopted.",
                    )
                )
        if orphaned is not None:
            _trace.research_end(
                trace_handle, status=orphaned[0], error=orphaned[1]
            )

        def _mirror(event: dict) -> None:
            _trace.research_event(trace_handle, event)

        def _sink(event: dict) -> None:
            # A dropped (stale-token) event stays out of the trace too —
            # the trace mirrors the log, not the abandoned worker. The
            # mirror runs under the runner lock (see ``_emit``), so it
            # cannot land after a concurrent stop has closed the span.
            self._emit(
                event,
                round_number=round_number,
                run_token=run_token,
                mirror=_mirror,
            )

        def _work() -> None:
            try:
                result = run_requirements_research(
                    module,
                    project_profile,
                    client,
                    model=model,
                    max_tokens=max_tokens,
                    discipline=discipline,
                    dimension_ids=dimension_ids,
                    established=established,
                    reference_docs=briefing_docs,
                    section_label=section,
                    project_facts=briefing_facts,
                    event_sink=_sink,
                    should_stop=cancel_event.is_set,
                )
            except ResearchFanoutError as exc:
                # Meter BEFORE resolving, and unconditionally. A round where
                # every dimension failed or was cancelled still spent real
                # money, and on a user stop `stop()` has already resolved
                # this run — so gating the meter on winning the CAS below
                # would silently drop exactly the spend the user is most
                # likely to ask about. The app's sink carries the
                # generation guard that decides whether this session still
                # owns the charge.
                if usage_sink is not None and exc.usage_totals:
                    try:
                        usage_sink(exc.usage_totals)
                    except Exception:  # noqa: BLE001 — metering never hides failure
                        pass
                kind = "auth_error" if getattr(exc, "auth_error", False) else ""
                self._close_trace(
                    self._try_resolve(
                        STATUS_FAILED,
                        error=str(exc),
                        error_kind=kind,
                        terminal_event=_research_failed_event,
                        run_token=run_token,
                    )
                )
            except Exception as exc:  # noqa: BLE001 — surfaced, never raised
                self._close_trace(
                    self._try_resolve(
                        STATUS_FAILED,
                        error=f"{type(exc).__name__}: {exc}",
                        terminal_event=_research_failed_event,
                        run_token=run_token,
                    )
                )
            else:
                # Meter first — the spend is real even on a run that ends up
                # discarded below (stopped, or superseded by a fresh start).
                # Meter THIS round's own usage, never the merged profile's:
                # that total is cumulative and would re-bill every earlier
                # round each time a new one lands.
                if usage_sink is not None:
                    try:
                        usage_sink(result.usage_total())
                    except Exception:  # noqa: BLE001 — metering never sinks a run
                        pass

                def _adopt(
                    previous: RequirementsProfile | None,
                ) -> RequirementsProfile:
                    return append_research_round(previous, result)

                def _complete_event(
                    resolution: _Resolution,
                ) -> dict[str, Any]:
                    # ``adopt`` already ran, inside this same transaction, so
                    # the resolution carries the merged profile.
                    merged = resolution.profile or result
                    latest = merged.rounds[-1] if merged.rounds else None
                    return {
                        "type": "research_complete",
                        # Cumulative — what the session now holds.
                        "item_count": len(merged.items),
                        "grounded_count": len(merged.grounded_items()),
                        "completed_dimensions": merged.completed_dimensions,
                        "failed_dimensions": merged.failed_dimensions,
                        # This round's own contribution.
                        "round_item_count": len(result.items),
                        "new_item_count": latest.new_items if latest else 0,
                        "repeat_item_count": (
                            latest.repeat_items if latest else 0
                        ),
                    }

                resolution = self._try_resolve(
                    STATUS_COMPLETE,
                    adopt=_adopt,
                    terminal_event=_complete_event,
                    run_token=run_token,
                )
                merged = resolution.profile if resolution is not None else None
                self._close_trace(
                    resolution,
                    items=len(merged.items) if merged is not None else 0,
                    # A run reports complete when ANY dimension did, so the
                    # span has to name what did not — cumulatively, since an
                    # earlier round may already have covered it.
                    incomplete_dimensions=(
                        incomplete_dimension_facts(merged)
                        if merged is not None
                        else None
                    ),
                )
            finally:
                if on_settled is not None:
                    try:
                        on_settled()
                    except Exception:  # noqa: BLE001
                        pass

        thread = threading.Thread(target=_work, daemon=True)
        self._thread = thread
        thread.start()
        return True

    def _try_resolve(
        self,
        status: str,
        *,
        error: str = "",
        error_kind: str = "",
        adopt: Callable[
            [RequirementsProfile | None], RequirementsProfile
        ] | None = None,
        terminal_event: Callable[[_Resolution], dict[str, Any]] | None = None,
        cancel: bool = False,
        run_token: object | None = None,
    ) -> _Resolution | None:
        """Atomically move RUNNING -> terminal; None if it lost the race.

        The single compare-and-set point for every way a run can end
        (success, failure, or :meth:`stop`) — whichever caller acquires the
        lock first while status is still ``running`` wins; a losing caller's
        result/error is silently discarded rather than clobbering whatever
        already resolved it. A worker passes its ``run_token`` so that a
        stopped round's late-finishing thread also loses once the NEXT
        round is running — the status check alone would let it through
        then. Winning clears the token: after a terminal event, nothing
        (log entries included) may arrive from the ended run.

        It is one transaction, not a compare-and-set followed by cleanup,
        because the very next thing that can happen after this lock is
        released is a fresh ``start()`` — which clears the event log and
        installs its own cancel event. Publishing the terminal event or
        setting the cancel event afterwards therefore addressed the
        SUCCESSOR: the ended round's terminal frame landed in the new
        round's log, and a stop cancelled the run that replaced it.

        The order inside the lock is deliberate, because several readers
        (readiness, ``_doc_payload``, the diagnostics snapshot) sample
        ``status`` and ``profile_result`` WITHOUT this lock:

        ``adopt`` (the success path) maps the accumulated profile to its
        replacement — the round-append merge — and runs FIRST, so a
        lock-free reader sees either ``running`` beside the old profile or
        ``complete`` beside the new one, never ``complete`` beside the old
        one. Running it here is also what keeps a stopped run's
        late-finishing thread from folding its discarded round into a
        profile that has already moved on: it loses the CAS, so its merge
        never runs at all. ``status`` is assigned LAST for the same reason.
        """
        with self._lock:
            if self.status != STATUS_RUNNING:
                return None
            if run_token is not None and run_token is not self._run_token:
                return None
            message = self._failure_message_locked(error) if error else ""
            profile = self.profile_result
            if adopt is not None:
                profile = adopt(profile)
                self.profile_result = profile
            self.error = message
            self.error_kind = error_kind
            if cancel and self._cancel_event is not None:
                # The winning run's own cancel event, read under the lock
                # that proves the run is still ours.
                self._cancel_event.set()
            resolution = _Resolution(
                status=status,
                error=message,
                error_kind=error_kind,
                # Still this run's: status is `running`, so no fresh start()
                # can have renumbered it.
                round_number=self._round_number,
                profile=profile,
            )
            if terminal_event is not None:
                resolution.event = self._append_event_locked(
                    terminal_event(resolution),
                    round_number=resolution.round_number,
                )
            resolution.trace_handle = self._trace_handle
            self._trace_handle = None
            self._run_token = None
            self.status = status
            return resolution

    def _failure_message_locked(self, base: str) -> str:
        """A failure message that says what survived it (caller holds the lock).

        Rounds accumulate, so a failed or stopped run costs only the round
        in flight — the user should not read "failed" and assume the
        research they already paid for is gone.
        """
        if self.profile_result is None:
            return base
        return f"{base} Earlier research rounds are unchanged and still in use."

    def _close_trace(
        self,
        resolution: _Resolution | None,
        *,
        items: int = 0,
        incomplete_dimensions: list[dict] | None = None,
    ) -> None:
        """Close the run's trace span — the winner closes it, exactly once.

        The handle is claimed inside the terminal transaction, so a stop and
        a late-finishing worker can never both close it (the loser gets no
        resolution at all) and a stopped run no longer leaves its span open
        forever, which the trace viewer renders as a crashed run.
        """
        if resolution is None or resolution.trace_handle is None:
            return
        _trace.research_end(
            resolution.trace_handle,
            status=resolution.status,
            error=resolution.error,
            items=items,
            incomplete_dimensions=incomplete_dimensions,
        )

    def stop(self) -> bool:
        """Request cancellation of the running run. False if none is running.

        Resolves the run as ``failed`` immediately (the UI never waits on the
        background thread) and signals ``should_stop`` so dimension work that
        hasn't started its network call yet bails without spending anything;
        a dimension already mid-call completes naturally but its result is
        discarded — ``_try_resolve`` in the background thread's completion
        handler will find the status already resolved and do nothing, so its
        round is never merged in.

        Only the round in flight is lost: rounds that already completed stay
        in the profile, which is what the message says.

        The round is discarded, so the trace span closes here rather than
        waiting on work whose outcome is already thrown away.
        """
        resolution = self._try_resolve(
            STATUS_FAILED,
            error="Stopped by user — this round's progress was discarded.",
            terminal_event=_research_failed_event,
            cancel=True,
        )
        if resolution is None:
            return False
        self._close_trace(resolution)
        return True

    def restore(self, profile: RequirementsProfile) -> None:
        """Adopt a previously-completed profile (project resume).

        The saved profile carries its own rounds, so a resumed session
        continues counting from where it left off — pressing Research adds
        round N+1, not a replacement.

        A restore is a project-file read, not a provider run: it publishes
        the compatibility event but never opens (or fabricates) a run span.
        """
        with self._lock:
            self.profile_result = profile
            self.error = ""
            self.error_kind = ""
            self.events = []
            self._append_event_locked(
                {
                    "type": "research_complete",
                    "restored": True,
                    "item_count": len(profile.items),
                    "grounded_count": len(profile.grounded_items()),
                    "completed_dimensions": profile.completed_dimensions,
                    "failed_dimensions": profile.failed_dimensions,
                },
                round_number=profile.round_count,
            )
            self._run_token = None
            self.status = STATUS_COMPLETE

    # -- snapshots -----------------------------------------------------------

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL

    @property
    def is_settling(self) -> bool:
        """Whether a stopped/provider worker can still report paid usage."""
        thread = self._thread
        return bool(thread is not None and thread.is_alive())

    def snapshot(self) -> dict[str, Any]:
        """UI-shaped status payload (poll endpoint + initial page load)."""
        with self._lock:
            payload: dict[str, Any] = {
                "status": self.status,
                "error": self.error,
                "error_kind": self.error_kind,
                "events": list(self.events),
            }
            result = self.profile_result
        if result is not None:
            payload["profile"] = _profile_view(result)
        return payload

    def sse_events(
        self, *, poll_interval: float = 0.2, timeout_s: float = 1800.0
    ) -> "Any":
        """Return a generator of event dicts from seq 0 until terminal + drained.

        Binds to the run in flight AT CALL TIME (the QC runner's shape):
        the run token is captured here, before the inner generator is
        handed to Starlette, so a queued response can never silently
        attach to a replacement run. The stream replays the existing log,
        then polls for new entries until the run is terminal and fully
        drained (or ``timeout_s`` elapses — a safety valve, far beyond any
        real run). A terminal ``stream_end`` sentinel closes the stream so
        clients need no timeout logic of their own; if a NEWER run takes
        the runner over mid-stream, the sentinel says ``superseded``
        instead (same two keys — the sentinel never gains a field).
        """
        with self._lock:
            # None while idle/terminal — then any future live token is
            # someone else's run and ends this stream as superseded.
            bound_token = self._run_token

        def _stream() -> Any:
            seq = 0
            deadline = time.monotonic() + timeout_s
            while True:
                with self._lock:
                    current_token = self._run_token
                    superseded = (
                        current_token is not None
                        and current_token is not bound_token
                    )
                    pending = [] if superseded else list(self.events[seq:])
                    status = self.status
                    drained = (
                        not superseded
                        and status in _TERMINAL
                        and seq + len(pending) >= len(self.events)
                    )
                if superseded:
                    yield {"type": "stream_end", "status": "superseded"}
                    return
                for event in pending:
                    seq = event["seq"] + 1
                    yield event
                if drained or time.monotonic() > deadline:
                    break
                time.sleep(poll_interval)
            yield {"type": "stream_end", "status": status}

        return _stream()


def _dimension_view(status: Any) -> dict[str, Any]:
    return {
        "dimension_id": status.dimension_id,
        "title": status.title,
        "status": status.status,
        "item_count": status.item_count,
        "grounded_count": status.grounded_count,
        "web_search_requests": status.web_search_requests,
        "web_fetch_requests": status.web_fetch_requests,
        "error": status.error,
    }


def _profile_view(profile: RequirementsProfile) -> dict[str, Any]:
    """The research drawer's view of a completed profile.

    ``dimension_statuses`` is the cumulative view across every round;
    ``rounds`` is what each round did on its own, so the findings report
    can show that (say) round 2 added four items and re-confirmed nine.
    """
    return {
        "research_date": profile.research_date,
        "project": dict(profile.project or {}),
        "dimension_statuses": [
            _dimension_view(s) for s in profile.dimension_statuses
        ],
        "rounds": [
            {
                "round_index": r.round_index,
                "research_date": r.research_date,
                "dimension_statuses": [
                    _dimension_view(s) for s in r.dimension_statuses
                ],
                "new_items": r.new_items,
                "repeat_items": r.repeat_items,
                **({"section": r.section} if r.section else {}),
            }
            for r in profile.rounds
        ],
        "items": [
            {
                "item_id": i.item_id,
                "dimension_id": i.dimension_id,
                "topic": i.topic,
                "category": i.category,
                "requirement": i.requirement,
                "authority": i.authority,
                "code_reference": i.code_reference,
                "accepted_sources": list(i.accepted_sources),
                "grounded": i.grounded,
                "confidence": i.confidence,
                "actionability": i.actionability,
                "notes": i.notes,
                "research_date": i.research_date,
                "round_index": i.round_index,
            }
            for i in profile.items
        ],
    }
