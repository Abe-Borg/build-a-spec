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
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.status = STATUS_IDLE
        self.error = ""
        self.profile_result: RequirementsProfile | None = None
        self.events: list[dict[str, Any]] = []

    # -- events --------------------------------------------------------------

    def _emit(self, event: dict[str, Any]) -> None:
        with self._lock:
            event = dict(event)
            event["seq"] = len(self.events)
            event["ts"] = time.strftime("%H:%M:%S")
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
        on_settled: Callable[[], None] | None = None,
        usage_sink: Callable[[dict], None] | None = None,
    ) -> bool:
        """Kick off the fan-out on a daemon thread. False if already running.

        ``on_settled`` (optional) runs after the terminal state is set —
        the app layer uses it for nothing today but tests can synchronize
        on it.
        """
        with self._lock:
            if self.status == STATUS_RUNNING:
                return False
            self.status = STATUS_RUNNING
            self.error = ""
            self.profile_result = None
            self.events = []

        trace_handle = _trace.research_start(
            project=project_profile.display_line(),
            dimensions=len(getattr(module, "research_dimensions", ()) or ()),
        )

        def _sink(event: dict) -> None:
            self._emit(event)
            _trace.research_event(trace_handle, event)

        def _work() -> None:
            try:
                result = run_requirements_research(
                    module,
                    project_profile,
                    client,
                    model=model,
                    max_tokens=max_tokens,
                    event_sink=_sink,
                )
            except ResearchFanoutError as exc:
                with self._lock:
                    self.status = STATUS_FAILED
                    self.error = str(exc)
                self._emit({"type": "research_failed", "error": str(exc)})
                _trace.research_end(
                    trace_handle, status=STATUS_FAILED, error=str(exc)
                )
            except Exception as exc:  # noqa: BLE001 — surfaced, never raised
                message = f"{type(exc).__name__}: {exc}"
                with self._lock:
                    self.status = STATUS_FAILED
                    self.error = message
                self._emit({"type": "research_failed", "error": message})
                _trace.research_end(
                    trace_handle, status=STATUS_FAILED, error=message
                )
            else:
                # Meter first, then flip to terminal — a status poller that
                # sees "complete" must find the ledger already updated.
                if usage_sink is not None:
                    try:
                        usage_sink(result.usage_total())
                    except Exception:  # noqa: BLE001 — metering never sinks a run
                        pass
                with self._lock:
                    self.status = STATUS_COMPLETE
                    self.profile_result = result
                self._emit(
                    {
                        "type": "research_complete",
                        "item_count": len(result.items),
                        "grounded_count": len(result.grounded_items()),
                        "completed_dimensions": result.completed_dimensions,
                        "failed_dimensions": result.failed_dimensions,
                    }
                )
                _trace.research_end(
                    trace_handle,
                    status=STATUS_COMPLETE,
                    items=len(result.items),
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

    def restore(self, profile: RequirementsProfile) -> None:
        """Adopt a previously-completed profile (project resume)."""
        with self._lock:
            self.status = STATUS_COMPLETE
            self.error = ""
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
            }
        )

    # -- snapshots -----------------------------------------------------------

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL

    def snapshot(self) -> dict[str, Any]:
        """UI-shaped status payload (poll endpoint + initial page load)."""
        with self._lock:
            payload: dict[str, Any] = {
                "status": self.status,
                "error": self.error,
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


def _profile_view(profile: RequirementsProfile) -> dict[str, Any]:
    """The research drawer's view of a completed profile."""
    return {
        "research_date": profile.research_date,
        "project": dict(profile.project or {}),
        "dimension_statuses": [
            {
                "dimension_id": s.dimension_id,
                "status": s.status,
                "item_count": s.item_count,
                "grounded_count": s.grounded_count,
                "web_search_requests": s.web_search_requests,
                "web_fetch_requests": s.web_fetch_requests,
                "error": s.error,
            }
            for s in profile.dimension_statuses
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
            }
            for i in profile.items
        ],
    }
