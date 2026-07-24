"""Focused regressions for Final-QC runner audit/state integrity."""

from __future__ import annotations

import copy
import threading
from types import SimpleNamespace

import pytest

from backend import sessions
from backend.app import create_app
from backend.llm.conversation import SessionState
from backend.qc.engine import (
    QCFinding,
    QCLensStatus,
    QCResult,
    QCVerdict,
    _validated_remembered_dismissal,
)
from backend.qc.runner import (
    QCRunner,
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_RUNNING,
)
from backend.spec_doc.project import load_project
from backend.usage_ledger import usage_pricing_snapshot


def _legacy_result(
    run_id: str,
    *,
    execution_status: str = "complete",
    lens_status: str = "completed",
    with_finding: bool = False,
) -> QCResult:
    findings = (
        [
            QCFinding(
                finding_id="finding-1",
                lens_id="test-lens",
                severity="medium",
                element_id="",
                title="Test finding",
                issue="Test issue",
                rationale="Test rationale",
                verdicts=[
                    QCVerdict(upholds=True),
                    QCVerdict(upholds=True),
                ],
            )
        ]
        if with_finding
        else []
    )
    return QCResult(
        schema_version=1,
        protocol_version="legacy-final-qc/1",
        run_id=run_id,
        execution_status=execution_status,
        findings=findings,
        lens_statuses=[
            QCLensStatus(
                lens_id="test-lens",
                title="Test lens",
                status=lens_status,
            )
        ],
        started_at="2026-07-24T10:00:00+00:00",
        finished_at="2026-07-24T10:01:00+00:00",
    )


def _seed_running_attempt(
    runner: QCRunner,
    *,
    token: object,
    run_id: str,
    cancel_event: threading.Event,
) -> None:
    with runner._lock:
        runner.status = STATUS_RUNNING
        runner.error = ""
        runner._run_token = token
        runner._cancel_event = cancel_event
        runner._worker_settled = False
        runner.latest_attempt_result = None
        runner.latest_attempt_run_id = run_id
        runner.latest_attempt_status = STATUS_RUNNING
        runner.latest_attempt_error = ""
        runner.latest_attempt_started_at = "2026-07-24T11:00:00+00:00"
        runner.latest_attempt_finished_at = ""
        runner.events = []


def test_terminal_finalization_and_audit_snapshot_are_one_coherent_state() -> None:
    runner = QCRunner()
    runner.restore(_legacy_result("retained-run"))
    token = object()
    cancel_event = threading.Event()
    _seed_running_attempt(
        runner,
        token=token,
        run_id="completed-run",
        cancel_event=cancel_event,
    )
    completed = _legacy_result("completed-run")
    completed.inconclusive = [
        QCFinding(
            finding_id="inconclusive-1",
            lens_id="test-lens",
            severity="medium",
            element_id="",
            title="Inconclusive candidate",
            issue="The review could not reach a supported conclusion.",
            rationale="The candidate remains visible in the audit trail.",
        )
    ]

    assert runner._finalize_attempt(
        token,
        runner_status=STATUS_COMPLETE,
        attempt_status="complete",
        result=completed,
        install_result=True,
        cancel_event=cancel_event,
        terminal_event={
            "type": "qc_complete",
            "inconclusive_count": len(completed.inconclusive),
        },
    )

    record = runner.audit_record_snapshot()
    assert record["runner"] == {
        "status": "complete",
        "error": "",
        "settling": False,
    }
    assert record["events"][-1]["type"] == "qc_complete"
    assert record["events"][-1]["inconclusive_count"] == 1
    assert record["result"]["run_id"] == "completed-run"
    assert record["latest_attempt"]["run_id"] == "completed-run"
    assert record["latest_attempt"]["status"] == "complete"
    assert record["latest_attempt"]["report"]["run_id"] == "completed-run"
    assert record["report_for_export"]["run_id"] == "completed-run"
    assert record["result"]["inconclusive"][0]["finding_id"] == (
        "inconclusive-1"
    )
    assert record["result_model"] is not completed
    assert record["result_model"].inconclusive[0].finding_id == (
        "inconclusive-1"
    )
    assert record["report_for_export_model"] is record["result_model"]
    assert runner.events[-1]["inconclusive_count"] == 1
    assert runner.result is completed
    assert runner.latest_attempt_result is completed


def test_cancelled_worker_preserves_paid_partial_without_replacing_success() -> None:
    runner = QCRunner()
    retained = _legacy_result("retained-run")
    runner.restore(retained)
    token = object()
    cancel_event = threading.Event()
    _seed_running_attempt(
        runner,
        token=token,
        run_id="cancelled-run",
        cancel_event=cancel_event,
    )
    assert runner.stop()
    stream = runner.sse_events(poll_interval=0.001, timeout_s=1)
    assert next(stream)["type"] == "qc_failed"
    assert runner.audit_record_snapshot()["runner"]["settling"] is True
    # A replacement cannot steal the runner/stream while the paid worker is
    # still assembling its final partial audit record.
    assert not runner.start(
        section=None,
        profile=None,
        module=None,
        client=None,
        model="test-model",
        max_tokens=1,
        version_index=0,
    )
    partial = _legacy_result(
        "cancelled-run",
        execution_status="partial",
        lens_status="failed",
    )

    assert not runner._finalize_attempt(
        token,
        runner_status=STATUS_COMPLETE,
        attempt_status="partial",
        result=partial,
        install_result=True,
        cancel_event=cancel_event,
    )

    record = runner.audit_record_snapshot()
    assert record["runner"]["status"] == STATUS_FAILED
    assert record["runner"]["settling"] is False
    assert record["result"]["run_id"] == "retained-run"
    assert record["latest_attempt"]["run_id"] == "cancelled-run"
    assert record["latest_attempt"]["status"] == "cancelled"
    assert record["latest_attempt"]["report"]["run_id"] == "cancelled-run"
    assert record["report_for_export"]["run_id"] == "cancelled-run"
    assert record["result_model"] is not retained
    assert record["report_for_export_model"] is not partial
    assert record["result_model"].run_id == "retained-run"
    assert record["report_for_export_model"].run_id == "cancelled-run"
    assert runner.result is retained
    assert runner.latest_attempt_result is partial
    remaining_events = list(stream)
    assert [event["type"] for event in remaining_events] == [
        "qc_attempt_settled",
        "stream_end",
    ]
    assert remaining_events[0]["report_available"] is True
    assert remaining_events[0]["execution_status"] == "partial"
    assert remaining_events[-1]["status"] == STATUS_FAILED


def test_settled_partial_is_latest_but_never_replaces_retained_success() -> None:
    runner = QCRunner()
    retained = _legacy_result("retained-complete")
    runner.restore(retained)
    token = object()
    cancel_event = threading.Event()
    _seed_running_attempt(
        runner,
        token=token,
        run_id="newer-partial",
        cancel_event=cancel_event,
    )
    partial = _legacy_result(
        "newer-partial",
        execution_status="partial",
        lens_status="failed",
    )

    assert runner._finalize_attempt(
        token,
        runner_status=STATUS_COMPLETE,
        attempt_status="partial",
        result=partial,
        # Even a mistaken permissive caller cannot violate the invariant.
        install_result=True,
        cancel_event=cancel_event,
        terminal_event={"type": "qc_complete", "execution_status": "partial"},
    )

    record = runner.audit_record_snapshot()
    assert record["runner"] == {
        "status": STATUS_COMPLETE,
        "error": "",
        "settling": False,
    }
    assert runner.result is retained
    assert runner.latest_attempt_result is partial
    assert record["result"]["run_id"] == "retained-complete"
    assert record["latest_attempt"]["status"] == "partial"
    assert record["latest_attempt"]["report"]["run_id"] == "newer-partial"
    assert record["report_for_export"]["run_id"] == "newer-partial"


def test_partial_qc_result_restore_is_evidence_not_retained_result() -> None:
    partial = _legacy_result(
        "partial-run",
        execution_status="partial",
        lens_status="failed",
    )
    runner = QCRunner()
    runner.restore(partial)
    assert runner.events[-1]["inconclusive_count"] == 0
    assert runner.result is None
    assert runner.latest_attempt_result is partial

    runner.restore_attempt(
        {
            "run_id": partial.run_id,
            "status": "partial",
            "error": "One lens did not complete.",
            "started_at": partial.started_at,
            "finished_at": partial.finished_at,
            "report": partial.to_dict(),
        }
    )

    assert runner.status == STATUS_COMPLETE
    assert runner.error == ""
    assert runner.latest_attempt_status == "partial"
    assert runner.result is None
    assert runner.latest_attempt_result is not None
    assert runner.latest_attempt_result.run_id == partial.run_id
    assert not runner.latest_attempt_result.coverage_complete()
    record = runner.audit_record_snapshot()
    assert record["runner"]["status"] == "complete"
    assert record["result"] is None
    assert record["latest_attempt"]["status"] == "partial"
    assert record["report_for_export"]["run_id"] == partial.run_id


def test_project_persistence_consumes_one_locked_qc_snapshot() -> None:
    class SnapshotOnlyRunner:
        def __init__(self) -> None:
            self.calls = 0

        @property
        def result(self):  # pragma: no cover - must never be sampled directly
            raise AssertionError("project persistence sampled qc.result directly")

        def latest_attempt_snapshot(self, **_kwargs):
            raise AssertionError("latest attempt was sampled separately")

        def audit_record_snapshot(self):
            self.calls += 1
            return {
                "runner": {"status": "failed", "error": "retry failed"},
                "result": {"run_id": "retained-run"},
                "latest_attempt": {
                    "run_id": "latest-run",
                    "status": "failed",
                },
                "report_for_export": {"run_id": "latest-run"},
            }

    session = SessionState()
    fake_runner = SnapshotOnlyRunner()
    session.qc = fake_runner  # type: ignore[assignment]

    payload = sessions.project_payload(session)

    assert fake_runner.calls == 1
    assert payload["qc_result"] == {"run_id": "retained-run"}
    assert payload["qc_latest_attempt"] == {
        "run_id": "latest-run",
        "status": "failed",
    }


@pytest.mark.parametrize(
    ("attempt_status", "report_status", "expected_runner_status"),
    [
        ("partial", "partial", STATUS_COMPLETE),
        ("failed", "failed", STATUS_FAILED),
        ("cancelled", "partial", STATUS_FAILED),
    ],
)
def test_distinct_latest_attempt_roundtrips_beside_retained_success(
    attempt_status: str,
    report_status: str,
    expected_runner_status: str,
) -> None:
    source = SessionState()
    retained = _legacy_result("retained-success")
    latest = _legacy_result(
        f"latest-{attempt_status}",
        execution_status=report_status,
        lens_status=("completed" if report_status == "complete" else "failed"),
    )
    source.qc.restore(retained)
    source.qc.restore_attempt(
        {
            "run_id": latest.run_id,
            "status": attempt_status,
            "error": (
                "Stopped after preserving paid work."
                if attempt_status == "cancelled"
                else ""
            ),
            "started_at": latest.started_at,
            "finished_at": latest.finished_at,
            "report": latest.to_dict(),
        }
    )

    payload = sessions.project_payload(source)
    assert payload["qc_result"]["run_id"] == "retained-success"
    assert payload["qc_latest_attempt"]["run_id"] == latest.run_id
    assert payload["qc_latest_attempt"]["report"]["run_id"] == latest.run_id

    restored = SessionState()
    load_project(payload, restored)
    record = restored.qc.audit_record_snapshot()

    assert restored.qc.status == expected_runner_status
    assert restored.qc.result is not None
    assert restored.qc.result.run_id == "retained-success"
    assert restored.qc.latest_attempt_status == attempt_status
    assert restored.qc.latest_attempt_result is not None
    assert restored.qc.latest_attempt_result.run_id == latest.run_id
    assert record["result"]["run_id"] == "retained-success"
    assert record["report_for_export"]["run_id"] == latest.run_id
    assert restored.qc.report_for_export().run_id == latest.run_id


def test_incompatible_latest_attempt_report_is_not_selected() -> None:
    runner = QCRunner()
    runner.restore(_legacy_result("retained-success"))
    contradictory = _legacy_result(
        "contradictory-run",
        execution_status="partial",
        lens_status="failed",
    )

    runner.restore_attempt(
        {
            "run_id": contradictory.run_id,
            "status": "complete",
            "report": contradictory.to_dict(),
        }
    )

    assert runner.status == STATUS_FAILED
    assert runner.latest_attempt_result is None
    assert runner.report_for_export().run_id == "retained-success"


def test_contradictory_same_run_partial_metadata_never_aliases_complete() -> None:
    runner = QCRunner()
    retained = _legacy_result("shared-run")
    runner.restore(retained)
    contradictory = _legacy_result(
        "shared-run",
        execution_status="partial",
        lens_status="failed",
    )

    runner.restore_attempt(
        {
            "run_id": "shared-run",
            "status": "partial",
            "report": contradictory.to_dict(),
        }
    )

    record = runner.audit_record_snapshot()
    assert runner.status == STATUS_FAILED
    assert runner.result is retained
    assert runner.latest_attempt_status == "partial"
    assert runner.latest_attempt_result is None
    assert "contradictory" in runner.latest_attempt_error.lower()
    assert record["latest_attempt"]["report_available"] is False
    assert record["report_for_export"]["run_id"] == "shared-run"


@pytest.mark.parametrize(
    ("execution_status", "lens_status", "expected_runner_status"),
    [
        ("partial", "failed", STATUS_COMPLETE),
        ("failed", "failed", STATUS_FAILED),
    ],
)
def test_project_load_demotes_noncomplete_qc_result_to_attempt_evidence(
    execution_status: str,
    lens_status: str,
    expected_runner_status: str,
) -> None:
    evidence = _legacy_result(
        f"evidence-{execution_status}",
        execution_status=execution_status,
        lens_status=lens_status,
        with_finding=True,
    )
    payload = sessions.project_payload(SessionState())
    payload["qc_result"] = evidence.to_dict()
    payload.pop("qc_latest_attempt", None)

    restored = SessionState()
    load_project(payload, restored)
    record = restored.qc.audit_record_snapshot()

    assert restored.qc.status == expected_runner_status
    assert restored.qc.result is None
    assert restored.qc.latest_attempt_result is not None
    assert restored.qc.latest_attempt_result.run_id == evidence.run_id
    assert record["result"] is None
    assert record["report_for_export"]["run_id"] == evidence.run_id
    resaved = sessions.project_payload(restored)
    assert "qc_result" not in resaved
    assert resaved["qc_latest_attempt"]["report"]["run_id"] == evidence.run_id


def test_latest_qc_attempt_is_staged_before_live_project_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = SessionState()
    source.history.append(
        {"role": "user", "content": [{"type": "text", "text": "incoming"}]}
    )
    retained = _legacy_result("retained-success")
    partial = _legacy_result(
        "latest-partial",
        execution_status="partial",
        lens_status="failed",
    )
    source.qc.restore(retained)
    source.qc.restore_attempt(
        {
            "run_id": partial.run_id,
            "status": "partial",
            "report": partial.to_dict(),
        }
    )
    payload = sessions.project_payload(source)

    target = SessionState()
    target.history.append(
        {"role": "user", "content": [{"type": "text", "text": "original"}]}
    )
    original_history = copy.deepcopy(target.history)
    original_document = target.doc.doc.to_dict()
    original_qc = target.qc
    original_from_dict = QCResult.from_dict

    def fail_on_latest(cls, raw: object):
        if isinstance(raw, dict) and raw.get("run_id") == "latest-partial":
            raise RuntimeError("injected latest-attempt parser failure")
        return original_from_dict(raw)

    monkeypatch.setattr(QCResult, "from_dict", classmethod(fail_on_latest))

    with pytest.raises(RuntimeError, match="latest-attempt parser failure"):
        load_project(payload, target)

    assert target.history == original_history
    assert target.doc.doc.to_dict() == original_document
    assert target.qc is original_qc


def test_sse_stream_closes_without_leaking_replacement_run_events() -> None:
    runner = QCRunner()
    first_token = object()
    with runner._lock:
        runner._run_token = first_token
        runner.latest_attempt_run_id = "first-run"
        runner.status = STATUS_RUNNING
        runner.events = [{"type": "first_event", "seq": 0, "ts": "11:00:00"}]

    stream = runner.sse_events(poll_interval=0, timeout_s=1)
    assert next(stream)["type"] == "first_event"

    with runner._lock:
        runner._run_token = object()
        runner.latest_attempt_run_id = "replacement-run"
        runner.status = STATUS_RUNNING
        runner.events = [
            {"type": "replacement_event", "seq": 0, "ts": "11:00:01"}
        ]

    assert next(stream) == {
        "type": "stream_end",
        "status": "superseded",
        "run_id": "first-run",
    }
    assert list(stream) == []


def test_sse_stream_binds_when_created_before_iteration_starts() -> None:
    runner = QCRunner()
    first_token = object()
    with runner._lock:
        runner._run_token = first_token
        runner.latest_attempt_run_id = "first-run"
        runner.status = STATUS_RUNNING
        runner.events = [{"type": "first_event", "seq": 0, "ts": "11:00:00"}]

    stream = runner.sse_events(poll_interval=0, timeout_s=1)
    with runner._lock:
        runner._run_token = object()
        runner.latest_attempt_run_id = "replacement-run"
        runner.events = [
            {"type": "replacement_event", "seq": 0, "ts": "11:00:01"}
        ]

    assert list(stream) == [
        {
            "type": "stream_end",
            "status": "superseded",
            "run_id": "first-run",
        }
    ]


def test_qc_stream_endpoint_binds_runner_before_response_iteration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BindingProbe:
        def __init__(self) -> None:
            self.calls = 0

        def sse_events(self):
            self.calls += 1
            return iter(())

    probe = BindingProbe()
    monkeypatch.setattr(
        sessions,
        "get_session",
        lambda: SimpleNamespace(qc=probe),
    )
    app = create_app()
    endpoint = next(
        route.endpoint
        for route in app.routes
        if getattr(route, "path", "") == "/api/qc/stream"
        and "GET" in (getattr(route, "methods", set()) or set())
    )

    response = endpoint()

    assert response.media_type == "text/event-stream"
    assert probe.calls == 1


def test_runner_dismissal_requires_and_normalizes_audit_rationale() -> None:
    runner = QCRunner()
    result = _legacy_result("dismiss-run", with_finding=True)
    runner.restore(result)
    finding = result.findings[0]

    assert not runner.dismiss(finding.finding_id, "  \t  ")
    assert finding.status == "open"
    assert finding.dismiss_reason == ""
    assert finding.disposition_events == []

    assert runner.dismiss(
        finding.finding_id,
        "  Reviewed with the engineer of record.  ",
    )
    assert finding.status == "dismissed"
    assert finding.dismiss_reason == "Reviewed with the engineer of record."
    assert finding.disposition_events[-1].reason == finding.dismiss_reason


def test_dismissal_is_one_way_and_exact_retry_is_idempotent() -> None:
    runner = QCRunner()
    result = _legacy_result("dismiss-run", with_finding=True)
    runner.restore(result)
    finding = result.findings[0]

    assert runner.dismiss(
        finding.finding_id,
        "Reviewed with engineer of record.",
        document_version=2,
        document_fingerprint="doc-fingerprint",
    )
    original_events = copy.deepcopy(finding.disposition_events)
    assert runner.dismiss(
        finding.finding_id,
        "  Reviewed with engineer of record.  ",
        document_version=99,
        document_fingerprint="must-not-be-appended",
    )
    assert finding.disposition_events == original_events
    assert not runner.dismiss(
        finding.finding_id,
        "A changed rationale must not rewrite history.",
    )
    assert finding.dismiss_reason == "Reviewed with engineer of record."
    assert finding.disposition_events == original_events

    applied_runner = QCRunner()
    applied_result = _legacy_result("applied-run", with_finding=True)
    applied_runner.restore(applied_result)
    applied = applied_result.findings[0]
    applied_runner.mark_applied(
        [applied.finding_id],
        document_version=3,
        document_fingerprint="applied-document",
    )
    applied_events = copy.deepcopy(applied.disposition_events)

    assert not applied_runner.dismiss(
        applied.finding_id,
        "Cannot overwrite an applied disposition.",
    )
    assert applied.status == "applied"
    assert applied.dismiss_reason == ""
    assert applied.disposition_events == applied_events
    assert applied.finding_id not in applied_result.dismissed_ids


def test_remembered_dismissal_requires_rationale_and_versioned_evidence() -> None:
    valid = {
        "reason": "  Reviewed with engineer of record.  ",
        "events": [
            {
                "action": "dismissed",
                "at": "2026-07-24T12:00:00+00:00",
                "reason": "Reviewed with engineer of record.",
                "document_version": 2,
                "document_fingerprint": "doc-fingerprint",
            }
        ],
    }
    carried = _validated_remembered_dismissal(valid)
    assert carried is not None
    assert carried[0] == "Reviewed with engineer of record."
    assert carried[1][-1].document_version == 2

    invalid_records = [
        {},
        {**valid, "reason": "   "},
        {**valid, "events": []},
        {
            **valid,
            "events": [{**valid["events"][0], "reason": "different"}],
        },
        {
            **valid,
            "events": [{**valid["events"][0], "document_version": None}],
        },
        {
            **valid,
            "events": [{**valid["events"][0], "document_fingerprint": ""}],
        },
        {
            **valid,
            "events": [{**valid["events"][0], "document_version": float("inf")}],
        },
    ]
    assert all(
        _validated_remembered_dismissal(record) is None
        for record in invalid_records
    )


def test_persisted_qc_numbers_and_pricing_basis_are_strict_and_finite() -> None:
    baseline = _legacy_result("numeric-run", with_finding=True).to_dict()
    baseline["cost_basis"] = usage_pricing_snapshot("test-model")
    assert QCResult.from_dict(copy.deepcopy(baseline)) is not None

    mutations: list[tuple[str, object]] = [
        ("schema_version", float("inf")),
        ("version_index", -1),
        ("duration_ms", 1.5),
        ("estimated_cost_usd", float("nan")),
        ("estimated_cost_usd", 10**10000),
        ("api_request_count", 1 << 63),
        ("usage_totals", {"input_tokens": -1}),
        ("usage_totals", {"input_tokens": 1.5}),
    ]
    for field, value in mutations:
        payload = copy.deepcopy(baseline)
        payload[field] = value
        assert QCResult.from_dict(payload) is None, field

    lens_payload = copy.deepcopy(baseline)
    lens_payload["lens_statuses"][0]["estimated_cost_usd"] = float("inf")
    assert QCResult.from_dict(lens_payload) is None

    verdict_payload = copy.deepcopy(baseline)
    verdict_payload["findings"][0]["verdicts"] = [
        {"upholds": True, "usage_totals": {"input_tokens": 1.25}}
    ]
    assert QCResult.from_dict(verdict_payload) is None

    disposition_payload = copy.deepcopy(baseline)
    disposition_payload["findings"][0]["disposition_events"] = [
        {
            "action": "dismissed",
            "reason": "bad numeric evidence",
            "document_version": float("inf"),
            "document_fingerprint": "doc",
        }
    ]
    assert QCResult.from_dict(disposition_payload) is None

    for value in (float("nan"), float("inf"), -0.01):
        cost_payload = copy.deepcopy(baseline)
        cost_payload["cost_basis"]["rates_per_token"]["input"] = value
        assert QCResult.from_dict(cost_payload) is None

    bad_label = copy.deepcopy(baseline)
    bad_label["cost_basis"]["used_fallback_rate"] = "false"
    assert QCResult.from_dict(bad_label) is None
