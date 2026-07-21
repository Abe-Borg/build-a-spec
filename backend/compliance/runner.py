"""Session-bound compliance-audit lifecycle (thread + status snapshot).

Same pattern as ``research.runner.ResearchRunner`` (Build-a-Spec native):
one :class:`AuditRunner` per session, replaced on reset/load so an
in-flight audit settles into an abandoned object. An audit is a single
streaming call (~a minute), so there is no SSE follow — the frontend polls
``GET /api/audit/status``.

The result records the document version it audited (``version_index``) so
every surface can mark staleness: an audit of v7 shown against a v9 draft
is advisory history, not a current verdict.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

from ..tracing import capture as _trace
from .checker import ComplianceAuditError, run_compliance_audit

STATUS_IDLE = "idle"
STATUS_RUNNING = "running"
STATUS_COMPLETE = "complete"
STATUS_FAILED = "failed"


class AuditRunner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.status = STATUS_IDLE
        self.error = ""
        self.result: dict[str, Any] | None = None

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
        on_settled: Callable[[], None] | None = None,
    ) -> bool:
        """Audit ``section`` (a deep-copied snapshot) on a daemon thread."""
        with self._lock:
            if self.status == STATUS_RUNNING:
                return False
            self.status = STATUS_RUNNING
            self.error = ""
            self.result = None

        controlling = sum(
            1
            for item in getattr(profile, "items", [])
            if getattr(item, "grounded", False)
            and not getattr(item, "is_process_advisory", False)
        )
        trace_handle = _trace.audit_span(controlling=controlling)

        def _work() -> None:
            try:
                outcome = run_compliance_audit(
                    section,
                    profile,
                    module,
                    client,
                    model=model,
                    max_tokens=max_tokens,
                )
            except ComplianceAuditError as exc:
                with self._lock:
                    self.status = STATUS_FAILED
                    self.error = str(exc)
                _trace.audit_end(
                    trace_handle, status=STATUS_FAILED, error=str(exc)
                )
            except Exception as exc:  # noqa: BLE001 — surfaced, never raised
                message = f"{type(exc).__name__}: {exc}"
                with self._lock:
                    self.status = STATUS_FAILED
                    self.error = message
                _trace.audit_end(
                    trace_handle, status=STATUS_FAILED, error=message
                )
            else:
                outcome["audited_at"] = time.strftime("%Y-%m-%d %H:%M")
                outcome["version_index"] = version_index
                with self._lock:
                    self.status = STATUS_COMPLETE
                    self.result = outcome
                _trace.audit_end(
                    trace_handle,
                    status=STATUS_COMPLETE,
                    findings=len(outcome.get("findings", [])),
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

    def restore(self, result: dict[str, Any]) -> None:
        """Adopt a persisted audit result (project resume)."""
        with self._lock:
            self.status = STATUS_COMPLETE
            self.error = ""
            self.result = dict(result)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            payload: dict[str, Any] = {
                "status": self.status,
                "error": self.error,
            }
            if self.result is not None:
                payload["result"] = self.result
            return payload
