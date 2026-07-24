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

import copy
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from ..tracing import capture as _trace
from .engine import (
    QCDispositionEvent,
    QCSourceGuard,
    QCFanoutError,
    QCResult,
    run_final_qc,
)
from .schema import QC_LENSES

STATUS_IDLE = "idle"
STATUS_RUNNING = "running"
STATUS_COMPLETE = "complete"
STATUS_FAILED = "failed"

_TERMINAL = (STATUS_COMPLETE, STATUS_FAILED)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class QCRunner:
    """One session's Final-QC state machine."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel_event: threading.Event | None = None
        self._run_token: object | None = None
        # ``status`` resolves immediately when the user stops a run, but an
        # already-billed provider call may still be returning a partial audit
        # record.  SSE must not close (and a replacement run must not start)
        # until that worker has had one final chance to attach its report.
        self._worker_settled = True
        self.status = STATUS_IDLE
        self.error = ""
        self.result: QCResult | None = None
        self.latest_attempt_result: QCResult | None = None
        self.latest_attempt_run_id = ""
        self.latest_attempt_status = ""
        self.latest_attempt_error = ""
        self.latest_attempt_started_at = ""
        self.latest_attempt_finished_at = ""
        self.events: list[dict[str, Any]] = []

    # -- events --------------------------------------------------------------

    def _emit(
        self, event: dict[str, Any], *, run_token: object | None = None
    ) -> bool:
        with self._lock:
            if run_token is not None and run_token is not self._run_token:
                return False
            self._append_event_locked(event)
            return True

    def _append_event_locked(self, event: dict[str, Any]) -> None:
        """Append an event while the caller holds ``self._lock``."""
        stamped = dict(event)
        stamped["seq"] = len(self.events)
        stamped["ts"] = time.strftime("%H:%M:%S")
        self.events.append(stamped)

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
        discipline: str = "",
        source_guard: QCSourceGuard | None = None,
        remembered_dismissed: set[str] | dict[str, dict[str, Any]] | None = None,
        on_settled: Callable[[], None] | None = None,
        usage_sink: Callable[[dict], None] | None = None,
    ) -> bool:
        """Kick QC off; false while another attempt runs or still settles."""
        with self._lock:
            if self.status == STATUS_RUNNING or not self._worker_settled:
                return False
            self.status = STATUS_RUNNING
            self.error = ""
            self._worker_settled = False
            # Preserve the last completed report until the re-run succeeds.
            # A costly failed re-run must not erase the user's prior audit
            # record; first runs still have ``result is None`` as before.
            self.events = []
            cancel_event = threading.Event()
            self._cancel_event = cancel_event
            run_token = object()
            self._run_token = run_token
            run_id = f"qc-run-{uuid.uuid4().hex}"
            started_at = _now_iso()
            self.latest_attempt_result = None
            self.latest_attempt_run_id = run_id
            self.latest_attempt_status = STATUS_RUNNING
            self.latest_attempt_error = ""
            self.latest_attempt_started_at = started_at
            self.latest_attempt_finished_at = ""

        trace_handle = _trace.qc_start(lenses=len(QC_LENSES))

        def _sink(event: dict) -> None:
            if self._emit(event, run_token=run_token):
                _trace.qc_event(trace_handle, event)

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
                    finished_at=_now_iso(),
                    discipline=discipline,
                    source_guard=source_guard,
                    remembered_dismissed=remembered_dismissed,
                    run_id=run_id,
                    event_sink=_sink,
                    should_stop=cancel_event.is_set,
                )
            except QCFanoutError as exc:
                if usage_sink is not None and exc.usage_totals:
                    try:
                        usage_sink(exc.usage_totals)
                    except Exception:  # noqa: BLE001 — metering never hides failure
                        pass
                if exc.result is not None:
                    exc.result.finished_at = _now_iso()
                if self._finalize_attempt(
                    run_token,
                    runner_status=STATUS_FAILED,
                    attempt_status="failed",
                    error=str(exc),
                    result=exc.result,
                    install_result=False,
                    cancel_event=cancel_event,
                    terminal_event={"type": "qc_failed", "error": str(exc)},
                ):
                    _trace.qc_end(
                        trace_handle, status=STATUS_FAILED, error=str(exc)
                    )
            except Exception as exc:  # noqa: BLE001 — surfaced, never raised
                message = f"{type(exc).__name__}: {exc}"
                if self._finalize_attempt(
                    run_token,
                    runner_status=STATUS_FAILED,
                    attempt_status="failed",
                    error=message,
                    install_result=False,
                    cancel_event=cancel_event,
                    terminal_event={"type": "qc_failed", "error": message},
                ):
                    _trace.qc_end(trace_handle, status=STATUS_FAILED, error=message)
            else:
                # Stamp finished_at + meter BEFORE resolving — the spend is
                # real even on a run that ends up discarded below (stopped,
                # or superseded by a fresh start).
                result.finished_at = _now_iso()
                if usage_sink is not None:
                    try:
                        usage_sink(result.usage_totals)
                    except Exception:  # noqa: BLE001 — metering never sinks a run
                        pass
                if self._finalize_attempt(
                    run_token,
                    runner_status=STATUS_COMPLETE,
                    attempt_status=result.execution_status,
                    result=result,
                    # A partial report is valuable audit evidence and remains
                    # the latest attempt, but only a fully complete run may
                    # replace the retained successful action/readiness result.
                    install_result=result.execution_status == "complete",
                    cancel_event=cancel_event,
                    terminal_event={
                        "type": "qc_complete",
                        "run_id": result.run_id,
                        "execution_status": result.execution_status,
                        "finding_count": len(result.findings),
                        "refuted_count": len(result.refuted),
                        "inconclusive_count": len(result.inconclusive),
                        "open_criticals": result.open_critical_count(),
                    },
                ):
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

    def _finalize_attempt(
        self,
        run_token: object,
        *,
        runner_status: str,
        attempt_status: str,
        error: str = "",
        result: QCResult | None = None,
        install_result: bool,
        cancel_event: threading.Event,
        terminal_event: dict[str, Any] | None = None,
    ) -> bool:
        """Finalize one worker's runner + audit state in one critical section.

        ``stop()`` competes at this same lock.  If stop wins, the completed
        worker may still attach its paid partial report to the cancelled
        attempt, but it cannot replace the retained successful result or
        resolve a newer run.  If the worker wins, status, retained result, and
        latest-attempt metadata become visible atomically.
        """
        with self._lock:
            if run_token is not self._run_token:
                return False
            cancelled = cancel_event.is_set()
            self.latest_attempt_status = (
                "cancelled" if cancelled else attempt_status
            )
            self.latest_attempt_error = (
                self.error
                if cancelled and self.error
                else (
                    "Stopped by user — partial activity was preserved."
                    if cancelled
                    else error
                )
            )
            self.latest_attempt_finished_at = _now_iso()
            if result is not None:
                self.latest_attempt_result = result
            self._worker_settled = True
            if self.status != STATUS_RUNNING:
                # ``stop()`` won the status race.  Preserve whatever paid
                # report the worker could assemble, then explicitly notify
                # the still-open stream that attachment is complete.
                self._append_event_locked(
                    {
                        "type": "qc_attempt_settled",
                        "run_id": self.latest_attempt_run_id,
                        "status": self.latest_attempt_status,
                        "execution_status": (
                            result.execution_status
                            if result is not None
                            else attempt_status
                        ),
                        "report_available": result is not None,
                        "finding_count": (
                            len(result.findings) if result is not None else 0
                        ),
                        "refuted_count": (
                            len(result.refuted) if result is not None else 0
                        ),
                        "inconclusive_count": (
                            len(result.inconclusive) if result is not None else 0
                        ),
                    }
                )
                return False
            self.status = runner_status
            self.error = error
            if (
                install_result
                and result is not None
                and result.execution_status == "complete"
            ):
                self.result = result
            if terminal_event is not None:
                self._append_event_locked(terminal_event)
            return True

    def stop(self) -> bool:
        """Request cancellation of the running run. False if none is running.

        Resolves controls as ``failed`` immediately and signals ``should_stop``
        so work that has not started a network call can bail. Already-billed
        work completes naturally. The bound SSE stream remains open in the
        explicit settling phase until ``_finalize_attempt`` attaches any paid
        partial report and emits ``qc_attempt_settled``.
        """
        with self._lock:
            if self.status != STATUS_RUNNING or self._run_token is None:
                return False
            self.status = STATUS_FAILED
            self.error = "Stopped by user — partial activity will be preserved."
            self.latest_attempt_status = "cancelled"
            self.latest_attempt_error = self.error
            self.latest_attempt_finished_at = _now_iso()
            if self._cancel_event is not None:
                self._cancel_event.set()
            self._append_event_locked(
                {"type": "qc_failed", "error": self.error}
            )
        return True

    def restore(self, result: QCResult) -> None:
        """Restore one persisted report without promoting partial evidence.

        Only a report whose execution itself completed may occupy ``result``,
        the retained action/readiness slot. Partial, failed, and cancelled
        reports remain available as latest-attempt evidence and for export.
        Endpoint guards independently enforce the stronger audit-completeness
        contract before any disposition or document mutation.
        """
        attempt_status = str(result.execution_status or "failed").lower()
        if attempt_status not in {"complete", "partial", "failed", "cancelled"}:
            attempt_status = "failed"
        retain_result = attempt_status == "complete" and result.is_complete()
        with self._lock:
            if self._cancel_event is not None:
                self._cancel_event.set()
            self._run_token = None
            self._worker_settled = True
            self.status = (
                STATUS_COMPLETE
                if attempt_status in {"complete", "partial"}
                else STATUS_FAILED
            )
            self.error = (
                ""
                if self.status == STATUS_COMPLETE
                else f"Restored Final QC attempt is {attempt_status}."
            )
            self.result = result if retain_result else None
            self.latest_attempt_result = result
            self.latest_attempt_run_id = result.run_id
            self.latest_attempt_status = attempt_status
            self.latest_attempt_error = self.error
            self.latest_attempt_started_at = result.started_at
            self.latest_attempt_finished_at = result.finished_at
            self.events = []
        self._emit(
            {
                "type": "qc_complete",
                "restored": True,
                "finding_count": len(result.findings),
                "refuted_count": len(result.refuted),
                "inconclusive_count": len(result.inconclusive),
                "open_criticals": result.open_critical_count(),
            }
        )

    def restore_attempt(self, payload: object) -> None:
        """Restore the optional latest-attempt audit record from a project."""
        if not isinstance(payload, dict):
            return
        run_id = str(payload.get("run_id") or "").strip()
        if not run_id:
            return
        status = str(payload.get("status") or "failed").strip().lower()
        if status not in {
            "running",
            "complete",
            "partial",
            "failed",
            "cancelled",
        }:
            status = "failed"
        restored_report = QCResult.from_dict(payload.get("report"))
        with self._lock:
            # A settled project's ``qc_result`` and latest-attempt report
            # serialize the same run twice. Keep one live object so later
            # apply/dismiss mutations are reflected by status, readiness, and
            # exports alike. Distinct failed/partial attempts retain their own
            # report object beside the last successful result.
            compatible_execution_statuses = {
                "complete": {"complete"},
                "partial": {"partial"},
                "failed": {"failed"},
                # Cancellation is runner metadata: the already-paid worker
                # may have assembled any terminal execution report before the
                # stop won the status race. This is the one intentional
                # compatibility family rather than string equality.
                "cancelled": {
                    "complete",
                    "partial",
                    "failed",
                    "cancelled",
                },
                "running": set(),
            }
            report_is_compatible = bool(
                restored_report is not None
                and restored_report.run_id == run_id
                and restored_report.execution_status
                in compatible_execution_statuses[status]
            )
            same_retained_identity = bool(
                self.result is not None and run_id == self.result.run_id
            )
            same_settled_run = bool(
                same_retained_identity
                and status == "complete"
                and report_is_compatible
            )
            metadata_error = ""
            if same_settled_run:
                restored_report = self.result
            elif same_retained_identity:
                # One run id cannot simultaneously denote a retained complete
                # result and partial/failed/cancelled latest-attempt metadata.
                restored_report = None
                metadata_error = (
                    "Saved Final QC metadata is contradictory: the retained "
                    f"complete run {run_id} is labeled as {status}."
                )
            elif restored_report is not None and not report_is_compatible:
                restored_report = None
                metadata_error = (
                    "Saved Final QC attempt metadata does not match its "
                    "embedded report identity or execution status."
                )
            settled_partial = bool(
                status == "partial" and restored_report is not None
            )
            if self._cancel_event is not None:
                self._cancel_event.set()
            self._run_token = None
            self._worker_settled = True
            self.latest_attempt_run_id = run_id
            self.latest_attempt_status = (
                "failed" if status == "running" else status
            )
            self.latest_attempt_error = (
                metadata_error or str(payload.get("error") or "")
            )
            if status == "running" and not self.latest_attempt_error:
                self.latest_attempt_error = (
                    "The saved project captured an in-progress attempt; it "
                    "cannot resume and must be run again."
                )
            self.latest_attempt_started_at = str(
                payload.get("started_at") or ""
            )
            self.latest_attempt_finished_at = str(
                payload.get("finished_at") or ""
            )
            self.latest_attempt_result = restored_report
            if same_settled_run or settled_partial:
                # ``partial`` describes audit coverage, not runner liveness.
                # The attempt is settled/current; readiness independently
                # keeps the audit-completeness gate red.
                self.status = STATUS_COMPLETE
                self.error = ""
            else:
                self.status = STATUS_FAILED
                self.error = self.latest_attempt_error or (
                    f"Latest saved Final QC attempt is {self.latest_attempt_status}."
                )

    # -- mutation (accept / dismiss; guarded) --------------------------------

    def remembered_dismissed(self) -> set[str]:
        with self._lock:
            if self.result is None:
                return set()
            return set(self.result.dismissed_ids)

    def remembered_dismissals(self) -> dict[str, dict[str, Any]]:
        """Dismissal reasons/events carried into content-identical re-runs."""
        with self._lock:
            if self.result is None:
                return {}
            return {
                finding.finding_id: {
                    "reason": finding.dismiss_reason,
                    "events": [event.to_dict() for event in finding.disposition_events],
                }
                for finding in self.result.findings
                if finding.finding_id in self.result.dismissed_ids
            }

    def mark_applied(
        self,
        finding_ids: list[str],
        *,
        document_version: int | None = None,
        document_fingerprint: str = "",
    ) -> None:
        with self._lock:
            if self.result is None:
                return
            wanted = set(finding_ids)
            for f in self.result.findings:
                if f.finding_id in wanted:
                    f.status = "applied"
                    f.disposition_events.append(
                        QCDispositionEvent(
                            action="applied",
                            at=_now_iso(),
                            document_version=document_version,
                            document_fingerprint=document_fingerprint,
                        )
                    )
            self.result.dismissed_ids = sorted(
                set(self.result.dismissed_ids) - wanted
            )

    def dismiss(
        self,
        finding_id: str,
        reason: str = "",
        *,
        document_version: int | None = None,
        document_fingerprint: str = "",
    ) -> bool:
        normalized_reason = str(reason or "").strip()
        if not normalized_reason:
            return False
        with self._lock:
            if self.result is None:
                return False
            f = self.result.finding(finding_id)
            if f is None:
                return False
            if f.status == "dismissed":
                # Exact retries are idempotent: do not append another event or
                # rewrite history. A changed rationale requires an explicit
                # future disposition-edit workflow, not silent mutation here.
                return f.dismiss_reason.strip() == normalized_reason
            if f.status != "open":
                # Applied is a document-changing terminal disposition. More
                # generally, dismissal is an open -> dismissed transition.
                return False
            f.status = "dismissed"
            f.dismiss_reason = normalized_reason
            f.disposition_events.append(
                QCDispositionEvent(
                    action="dismissed",
                    at=_now_iso(),
                    reason=normalized_reason,
                    document_version=document_version,
                    document_fingerprint=document_fingerprint,
                )
            )
            self.result.dismissed_ids = sorted(
                set(self.result.dismissed_ids) | {finding_id}
            )
            return True

    def record_disposition_outcome(
        self,
        finding_id: str,
        *,
        action: str,
        reason: str,
        document_version: int | None = None,
        document_fingerprint: str = "",
    ) -> bool:
        """Append a non-mutating apply/dismiss outcome to the audit trail."""
        with self._lock:
            if self.result is None:
                return False
            finding = self.result.finding(finding_id)
            if finding is None:
                return False
            finding.disposition_events.append(
                QCDispositionEvent(
                    action=action,
                    at=_now_iso(),
                    reason=reason,
                    document_version=document_version,
                    document_fingerprint=document_fingerprint,
                )
            )
            return True

    # -- snapshots -----------------------------------------------------------

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL

    @property
    def is_settling(self) -> bool:
        """Whether a stopped worker still owns a final report attachment."""
        with self._lock:
            return not self._worker_settled

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
            payload["latest_attempt"] = self._latest_attempt_payload_locked(
                include_report=False
            )
            return payload

    def _latest_attempt_payload_locked(
        self, *, include_report: bool
    ) -> dict[str, Any] | None:
        if not self.latest_attempt_run_id:
            return None
        payload: dict[str, Any] = {
            "run_id": self.latest_attempt_run_id,
            "status": self.latest_attempt_status,
            "error": self.latest_attempt_error,
            "started_at": self.latest_attempt_started_at,
            "finished_at": self.latest_attempt_finished_at,
            "report_available": self.latest_attempt_result is not None,
        }
        if include_report and self.latest_attempt_result is not None:
            payload["report"] = self.latest_attempt_result.to_dict()
        return payload

    def latest_attempt_snapshot(
        self, *, include_report: bool = False
    ) -> dict[str, Any] | None:
        with self._lock:
            return self._latest_attempt_payload_locked(
                include_report=include_report
            )

    def audit_record_snapshot(self) -> dict[str, Any]:
        """Return one coherent persistence/export view under the runner lock.

        Callers that need more than one of retained result, latest attempt,
        selected export report, and runner state must use this method instead
        of sampling the public attributes separately. Every report is
        copied and serialized while disposition mutations are excluded by the
        same lock. Model entries are detached snapshots: callers may inspect
        them after the lock is released without observing later mutations.
        """
        with self._lock:
            retained_model = copy.deepcopy(self.result)
            if self.latest_attempt_result is self.result:
                latest_model = retained_model
            else:
                latest_model = copy.deepcopy(self.latest_attempt_result)
            selected_model = latest_model or retained_model
            retained = (
                retained_model.to_dict() if retained_model is not None else None
            )
            latest = self._latest_attempt_payload_locked(include_report=False)
            if latest is not None and latest_model is not None:
                latest["report"] = latest_model.to_dict()
            return {
                "runner": {
                    "status": self.status,
                    "error": self.error,
                    "settling": not self._worker_settled,
                },
                "events": copy.deepcopy(self.events),
                "result": retained,
                "result_model": retained_model,
                "latest_attempt": latest,
                "report_for_export": (
                    selected_model.to_dict()
                    if selected_model is not None
                    else None
                ),
                "report_for_export_model": selected_model,
            }

    def report_for_export(self) -> QCResult | None:
        """Latest attempt report when available, else last successful report."""
        with self._lock:
            return self.latest_attempt_result or self.result

    def sse_events(
        self, *, poll_interval: float = 0.2, timeout_s: float = 1800.0
    ) -> "Any":
        """Yield one run's events until terminal, timeout, or supersession.

        A stream is bound immediately to the run that owns the runner when
        this method is called. If another run starts while a client is
        connected, the old stream closes without ever leaking the new run's
        events into the old response.
        """
        with self._lock:
            bound_token = self._run_token
            bound_run_id = self.latest_attempt_run_id
            initial_status = self.status

        def _stream() -> "Any":
            seq = 0
            deadline = time.monotonic() + timeout_s
            last_status = initial_status
            while True:
                with self._lock:
                    ownership_changed = (
                        self._run_token is not bound_token
                        or self.latest_attempt_run_id != bound_run_id
                    )
                    if ownership_changed:
                        pending: list[dict[str, Any]] = []
                        terminal_and_drained = False
                    else:
                        pending = list(self.events[seq:])
                        last_status = self.status
                        terminal_and_drained = (
                            self.status in _TERMINAL
                            and self._worker_settled
                            and seq + len(pending) >= len(self.events)
                        )
                if ownership_changed:
                    yield {
                        "type": "stream_end",
                        "status": "superseded",
                        "run_id": bound_run_id,
                    }
                    return
                for event in pending:
                    seq = event["seq"] + 1
                    yield event
                if terminal_and_drained:
                    break
                if time.monotonic() > deadline:
                    break
                time.sleep(poll_interval)
            yield {"type": "stream_end", "status": last_status}

        return _stream()
