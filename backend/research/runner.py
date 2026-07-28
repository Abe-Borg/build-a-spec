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
terminal state, so a page reload (or a test) can always catch up.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

from ..project_profile import ProjectProfile
from ..tracing import capture as _trace
from .engine import (
    RequirementsProfile,
    ResearchFanoutError,
    append_research_round,
    run_requirements_research,
)

STATUS_IDLE = "idle"
STATUS_RUNNING = "running"
STATUS_COMPLETE = "complete"
STATUS_FAILED = "failed"

_TERMINAL = (STATUS_COMPLETE, STATUS_FAILED)


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
        self.status = STATUS_IDLE
        self.error = ""
        self.error_kind = ""
        self.profile_result: RequirementsProfile | None = None
        self.events: list[dict[str, Any]] = []

    # -- events --------------------------------------------------------------

    def _emit(self, event: dict[str, Any], *, round_number: int = 0) -> None:
        with self._lock:
            event = dict(event)
            event["seq"] = len(self.events)
            event["ts"] = time.strftime("%H:%M:%S")
            if round_number:
                event["round"] = round_number
            self.events.append(event)

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
        on_settled: Callable[[], None] | None = None,
        usage_sink: Callable[[dict], None] | None = None,
    ) -> bool:
        """Kick off the fan-out on a daemon thread. False if already running.

        ``on_settled`` (optional) runs after the terminal state is set —
        the app layer uses it for nothing today but tests can synchronize
        on it.

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
            cancel_event = threading.Event()
            self._cancel_event = cancel_event

        trace_handle = _trace.research_start(
            project=project_profile.display_line(),
            dimensions=len(getattr(module, "research_dimensions", ()) or ()),
        )

        def _sink(event: dict) -> None:
            self._emit(event, round_number=round_number)
            _trace.research_event(trace_handle, event)

        def _work() -> None:
            try:
                result = run_requirements_research(
                    module,
                    project_profile,
                    client,
                    model=model,
                    max_tokens=max_tokens,
                    discipline=discipline,
                    event_sink=_sink,
                    should_stop=cancel_event.is_set,
                )
            except ResearchFanoutError as exc:
                message = self._failure_message(str(exc))
                kind = "auth_error" if getattr(exc, "auth_error", False) else ""
                if self._try_resolve(STATUS_FAILED, error=message, error_kind=kind):
                    self._emit(
                        {
                            "type": "research_failed",
                            "error": message,
                            **({"error_kind": kind} if kind else {}),
                        },
                        round_number=round_number,
                    )
                    _trace.research_end(
                        trace_handle, status=STATUS_FAILED, error=message
                    )
            except Exception as exc:  # noqa: BLE001 — surfaced, never raised
                message = self._failure_message(f"{type(exc).__name__}: {exc}")
                if self._try_resolve(STATUS_FAILED, error=message):
                    self._emit(
                        {"type": "research_failed", "error": message},
                        round_number=round_number,
                    )
                    _trace.research_end(
                        trace_handle, status=STATUS_FAILED, error=message
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
                merged: RequirementsProfile | None = None

                def _adopt(
                    previous: RequirementsProfile | None,
                ) -> RequirementsProfile:
                    nonlocal merged
                    merged = append_research_round(previous, result)
                    return merged

                if self._try_resolve(STATUS_COMPLETE, adopt=_adopt) and merged:
                    latest = merged.rounds[-1] if merged.rounds else None
                    self._emit(
                        {
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
                        },
                        round_number=round_number,
                    )
                    _trace.research_end(
                        trace_handle,
                        status=STATUS_COMPLETE,
                        items=len(merged.items),
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
    ) -> bool:
        """Atomically move RUNNING -> a terminal status; False if it lost the race.

        The single compare-and-set point for every way a run can end
        (success, failure, or :meth:`stop`) — whichever caller acquires the
        lock first while status is still ``running`` wins; a losing caller's
        result/error is silently discarded rather than clobbering whatever
        already resolved it.

        ``adopt`` (the success path) maps the accumulated profile to its
        replacement — the round-append merge — and runs INSIDE the same
        lock as the compare-and-set. That is what keeps a stopped run's
        late-finishing thread from folding its discarded round into a
        profile that has already moved on: it loses the CAS, so its merge
        never runs at all.
        """
        with self._lock:
            if self.status != STATUS_RUNNING:
                return False
            self.status = status
            self.error = error
            self.error_kind = error_kind
            if adopt is not None:
                self.profile_result = adopt(self.profile_result)
            return True

    def _failure_message(self, base: str) -> str:
        """A failure message that says what survived it.

        Rounds accumulate, so a failed or stopped run costs only the round
        in flight — the user should not read "failed" and assume the
        research they already paid for is gone.
        """
        with self._lock:
            retained = self.profile_result is not None
        if not retained:
            return base
        return f"{base} Earlier research rounds are unchanged and still in use."

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
        """
        # Read the active round BEFORE the compare-and-set: until it lands,
        # status is still running, so no fresh start() can have renumbered
        # it. A stop that beats the worker's first event would otherwise
        # leave the round's whole log a single untagged terminal event.
        round_number = self._round_number
        message = self._failure_message(
            "Stopped by user — this round's progress was discarded."
        )
        if not self._try_resolve(STATUS_FAILED, error=message):
            return False
        if self._cancel_event is not None:
            self._cancel_event.set()
        self._emit(
            {"type": "research_failed", "error": self.error},
            round_number=round_number,
        )
        return True

    def restore(self, profile: RequirementsProfile) -> None:
        """Adopt a previously-completed profile (project resume).

        The saved profile carries its own rounds, so a resumed session
        continues counting from where it left off — pressing Research adds
        round N+1, not a replacement.
        """
        with self._lock:
            self.status = STATUS_COMPLETE
            self.error = ""
            self.error_kind = ""
            self.profile_result = profile
            self.events = []
        self._emit(
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
        """Yield event dicts from seq 0, following until terminal + drained.

        Generator for the SSE endpoint: replays the existing log, then
        polls for new entries until the run is terminal and fully drained
        (or ``timeout_s`` elapses — a safety valve, far beyond any real
        run). A terminal ``stream_end`` sentinel closes the stream so
        clients need no timeout logic of their own.
        """
        seq = 0
        deadline = time.monotonic() + timeout_s
        while True:
            for event in self.events_since(seq):
                seq = event["seq"] + 1
                yield event
            if self.is_terminal and seq >= len(self.events):
                break
            if time.monotonic() > deadline:
                break
            time.sleep(poll_interval)
        yield {"type": "stream_end", "status": self.status}


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
