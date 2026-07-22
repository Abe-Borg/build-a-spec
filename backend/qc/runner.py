"""Session-bound Final-QC run lifecycle: daemon thread, event log, SSE.

Structural clone of :class:`backend.research.runner.ResearchRunner` (the
engine is a pure synchronous function; this gives the FastAPI layer
something to start, watch, and stream). One :class:`QCRunner` lives on the
session (``SessionState.qc``); reset / project load replace it so a thread
still running against the old session settles into an abandoned object
(the zombie-turn pattern).

Status vocabulary ``idle|running|complete|failed``; the SSE endpoint
replays the event log from seq 0 and follows until terminal, closing with
a ``stream_end`` sentinel (event types: ``qc_started``, ``lens_complete``,
``lens_failed``, ``verify_progress``, ``qc_complete``, ``qc_failed``).
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

from ..tracing import capture as _trace
from .engine import QCFanoutError, QCResult, run_final_qc
from .schema import QC_LENSES

STATUS_IDLE = "idle"
STATUS_RUNNING = "running"
STATUS_COMPLETE = "complete"
STATUS_FAILED = "failed"

_TERMINAL = (STATUS_COMPLETE, STATUS_FAILED)


class QCRunner:
    """One session's Final-QC state machine."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel_event: threading.Event | None = None
        self.status = STATUS_IDLE
        self.error = ""
        self.result: QCResult | None = None
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
        section: Any,
        profile: Any,
        module: Any,
        client: Any,
        model: str,
        max_tokens: int,
        version_index: int,
        remembered_dismissed: set[str] | None = None,
        on_settled: Callable[[], None] | None = None,
        usage_sink: Callable[[dict], None] | None = None,
    ) -> bool:
        """Kick the QC pipeline off on a daemon thread. False if already running."""
        with self._lock:
            if self.status == STATUS_RUNNING:
                return False
            self.status = STATUS_RUNNING
            self.error = ""
            self.result = None
            self.events = []
            cancel_event = threading.Event()
            self._cancel_event = cancel_event

        trace_handle = _trace.qc_start(lenses=len(QC_LENSES))

        def _sink(event: dict) -> None:
            self._emit(event)
            _trace.qc_event(trace_handle, event)

        started_at = time.strftime("%Y-%m-%d %H:%M")

        def _work() -> None:
            try:
                result = run_final_qc(
                    section,
                    profile,
                    module,
                    client,
                    model=model,
                    max_tokens=max_tokens,
                    version_index=version_index,
                    started_at=started_at,
                    finished_at=time.strftime("%Y-%m-%d %H:%M"),
                    remembered_dismissed=remembered_dismissed,
                    event_sink=_sink,
                    should_stop=cancel_event.is_set,
                )
            except QCFanoutError as exc:
                if self._try_resolve(STATUS_FAILED, error=str(exc)):
                    self._emit({"type": "qc_failed", "error": str(exc)})
                    _trace.qc_end(
                        trace_handle, status=STATUS_FAILED, error=str(exc)
                    )
            except Exception as exc:  # noqa: BLE001 — surfaced, never raised
                message = f"{type(exc).__name__}: {exc}"
                if self._try_resolve(STATUS_FAILED, error=message):
                    self._emit({"type": "qc_failed", "error": message})
                    _trace.qc_end(trace_handle, status=STATUS_FAILED, error=message)
            else:
                # Stamp finished_at + meter BEFORE resolving — the spend is
                # real even on a run that ends up discarded below (stopped,
                # or superseded by a fresh start).
                result.finished_at = time.strftime("%Y-%m-%d %H:%M")
                if usage_sink is not None:
                    try:
                        usage_sink(result.usage_totals)
                    except Exception:  # noqa: BLE001 — metering never sinks a run
                        pass
                if self._try_resolve(STATUS_COMPLETE, result=result):
                    self._emit(
                        {
                            "type": "qc_complete",
                            "finding_count": len(result.findings),
                            "refuted_count": len(result.refuted),
                            "open_criticals": result.open_critical_count(),
                        }
                    )
                    _trace.qc_end(
                        trace_handle,
                        status=STATUS_COMPLETE,
                        findings=len(result.findings),
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
        self, status: str, *, error: str = "", result: QCResult | None = None
    ) -> bool:
        """Atomically move RUNNING -> a terminal status; False if it lost the race.

        The single compare-and-set point for every way a run can end
        (success, failure, or :meth:`stop`) — whichever caller acquires the
        lock first while status is still ``running`` wins; a losing caller's
        result/error is silently discarded rather than clobbering whatever
        already resolved it.
        """
        with self._lock:
            if self.status != STATUS_RUNNING:
                return False
            self.status = status
            self.error = error
            if result is not None:
                self.result = result
            return True

    def stop(self) -> bool:
        """Request cancellation of the running run. False if none is running.

        Resolves the run as ``failed`` immediately (the UI never waits on the
        background thread) and signals ``should_stop`` so lens/verifier work
        that hasn't started its network call yet bails without spending
        anything; work already mid-call completes naturally but its result
        is discarded — ``_try_resolve`` in the background thread's
        completion handler will find the status already resolved and do
        nothing.
        """
        if not self._try_resolve(
            STATUS_FAILED,
            error="Stopped by user — progress was discarded.",
        ):
            return False
        if self._cancel_event is not None:
            self._cancel_event.set()
        self._emit({"type": "qc_failed", "error": self.error})
        return True

    def restore(self, result: QCResult) -> None:
        """Adopt a previously-completed result (project resume)."""
        with self._lock:
            self.status = STATUS_COMPLETE
            self.error = ""
            self.result = result
            self.events = []
        self._emit(
            {
                "type": "qc_complete",
                "restored": True,
                "finding_count": len(result.findings),
                "refuted_count": len(result.refuted),
                "open_criticals": result.open_critical_count(),
            }
        )

    # -- mutation (accept / dismiss; guarded) --------------------------------

    def remembered_dismissed(self) -> set[str]:
        with self._lock:
            if self.result is None:
                return set()
            return set(self.result.dismissed_ids)

    def mark_applied(self, finding_ids: list[str]) -> None:
        with self._lock:
            if self.result is None:
                return
            wanted = set(finding_ids)
            for f in self.result.findings:
                if f.finding_id in wanted:
                    f.status = "applied"

    def dismiss(self, finding_id: str, reason: str = "") -> bool:
        with self._lock:
            if self.result is None:
                return False
            f = self.result.finding(finding_id)
            if f is None:
                return False
            f.status = "dismissed"
            f.dismiss_reason = reason
            self.result.dismissed_ids = sorted(
                set(self.result.dismissed_ids) | {finding_id}
            )
            return True

    # -- snapshots -----------------------------------------------------------

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            payload: dict[str, Any] = {
                "status": self.status,
                "error": self.error,
                "events": list(self.events),
            }
            result = self.result
            if result is not None:
                payload["result"] = result.to_dict()
            return payload

    def sse_events(
        self, *, poll_interval: float = 0.2, timeout_s: float = 1800.0
    ) -> "Any":
        """Yield event dicts from seq 0, following until terminal + drained."""
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
