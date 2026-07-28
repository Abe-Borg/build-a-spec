"""Hermetic coverage for the Final-QC v3 verifier contract."""
from __future__ import annotations

import copy
import threading

import httpx
from anthropic import BadRequestError

from backend.qc.engine import (
    QC_PROTOCOL_VERSION,
    QC_REPORT_SCHEMA_VERSION,
    QCResult,
    _verifier_system_prompt,
    _verifier_request_suffix,
    run_final_qc,
)
from backend.qc.schema import (
    QC_LENSES,
    QC_VERDICT_SCHEMA,
    normalize_verdict,
    submit_qc_verdict_tool,
)
from backend import settings
from backend.spec_doc.model import DocumentStore
from backend.spec_modules import DEFAULT_MODULE
from tests.fakes import (
    SequencedFakeClient,
    _user_text,
    qc_findings_response,
    qc_verdict_response,
)


_LENS_KEYS = {
    lens.lens_id: f"[[QC-LENS:{lens.lens_id}]]" for lens in QC_LENSES
}


def _store() -> DocumentStore:
    store = DocumentStore()
    store.begin_turn()
    store.apply_edits(
        [
            {
                "action": "replace",
                "target_id": "sec",
                "text": "WET-PIPE SPRINKLER SYSTEMS",
                "numbering": "21 13 13",
            },
            {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"},
            {
                "action": "add_paragraph",
                "target_id": "pt1.a1",
                "text": "Comply with NFPA 13-2019 throughout.",
                "status": "assumed",
            },
        ]
    )
    store.commit_turn()
    return store


def _finding(
    title: str,
    *,
    severity: str = "medium",
    ops: list[dict] | None = None,
) -> dict:
    return {
        "title": title,
        "severity": severity,
        "element_id": "pt1.a1.p1",
        "issue": f"Issue for {title}.",
        "rationale": f"Rationale for {title}.",
        "source_urls": [],
        "proposed_ops": ops,
    }


def _replace_op(text: str) -> list[dict]:
    return [
        {
            "action": "replace",
            "target_id": "pt1.a1.p1",
            "text": text,
            "status": "confirmed",
        }
    ]


def _scripts(findings: list[dict]) -> dict[str, list]:
    scripts = {
        key: [qc_findings_response(lens_id, findings=[])]
        for lens_id, key in _LENS_KEYS.items()
    }
    scripts[_LENS_KEYS["coordination_consistency"]] = [
        qc_findings_response("coordination_consistency", findings=findings)
    ]
    return scripts


def _run(client: object) -> QCResult:
    store = _store()
    return run_final_qc(
        store.doc,
        None,
        DEFAULT_MODULE,
        client,
        model=settings.QC_MODEL,
        max_tokens=4096,
        version_index=store.index,
        started_at="2026-07-25T10:00:00-07:00",
        finished_at="2026-07-25T10:01:00-07:00",
    )


def _bad_request_error() -> BadRequestError:
    http_request = httpx.Request(
        "POST", "https://api.anthropic.com/v1/messages"
    )
    response = httpx.Response(400, request=http_request)
    return BadRequestError(
        "Invalid verifier output schema.",
        response=response,
        body={"error": {"type": "invalid_request_error"}},
    )


def test_verdict_schema_avoids_nullable_enum_and_requires_fix_review() -> None:
    revised = QC_VERDICT_SCHEMA["properties"]["revised_severity"]
    assert revised["type"] == ["string", "null"]
    assert "enum" not in revised
    assert set(QC_VERDICT_SCHEMA["required"]) == {
        "upholds",
        "revised_severity",
        "note",
        "ops_adequate",
        "ops_note",
    }
    tool = submit_qc_verdict_tool(model=settings.QC_MODEL)
    assert tool["input_schema"]["properties"]["revised_severity"] == revised

    def assert_no_nullable_enum(value: object) -> None:
        if isinstance(value, dict):
            node_type = value.get("type")
            if isinstance(node_type, list) and "null" in node_type:
                assert "enum" not in value
            for child in value.values():
                assert_no_nullable_enum(child)
        elif isinstance(value, list):
            for child in value:
                assert_no_nullable_enum(child)

    assert_no_nullable_enum(tool["input_schema"])


def test_verdict_normalization_clamps_severity_and_inconsistent_fix_votes() -> None:
    for severity in ("critical", "high", "medium", "low"):
        normalized = normalize_verdict(
            {
                "upholds": True,
                "revised_severity": severity.upper(),
                "note": "valid",
                "ops_adequate": True,
                "ops_note": "complete",
            }
        )
        assert normalized["revised_severity"] == severity
        assert normalized["ops_adequate"] is True

    unknown = normalize_verdict(
        {
            "upholds": True,
            "revised_severity": "blocker",
            "note": "unknown severity",
            "ops_adequate": True,
            "ops_note": "complete",
        }
    )
    assert unknown["revised_severity"] == ""
    assert normalize_verdict(
        {
            "upholds": True,
            "revised_severity": None,
            "note": "keep",
            "ops_adequate": True,
            "ops_note": "none",
        },
        has_proposed_ops=False,
    )["ops_adequate"] is False
    assert normalize_verdict(
        {
            "upholds": False,
            "revised_severity": None,
            "note": "refuted",
            "ops_adequate": True,
            "ops_note": "inconsistent",
        }
    )["ops_adequate"] is False


def test_verifier_prompt_includes_complete_untrusted_operations() -> None:
    finding = _finding(
        "Prompt operation",
        ops=_replace_op("Comply with NFPA 13-2025 throughout."),
    )
    lens = next(
        lens for lens in QC_LENSES if lens.lens_id == "coordination_consistency"
    )
    message = _verifier_request_suffix(finding, lens)
    assert '<proposed_ops trust="untrusted-data">' in message
    assert (
        '"action":"replace","status":"confirmed",'
        '"target_id":"pt1.a1.p1","text":"Comply with NFPA 13-2025 throughout."'
    ) in message
    system = _verifier_system_prompt(DEFAULT_MODULE)
    assert "fix only part" in system
    assert "[TBD]" in system
    assert "create a contradiction" in system


def test_semantic_fix_approval_is_unanimous_but_finding_vote_is_majority() -> None:
    findings = [
        _finding("Unanimous fix", ops=_replace_op("Unanimous replacement.")),
        _finding(
            "Majority finding",
            severity="high",
            ops=_replace_op("Majority replacement."),
        ),
        _finding("Unsafe fix", ops=_replace_op("Unsafe replacement.")),
        _finding("Advisory only", ops=None),
        _finding("Refuted fix", ops=_replace_op("Refuted replacement.")),
    ]
    scripts = _scripts(findings)
    scripts["Unanimous fix"] = [
        qc_verdict_response(True, ops_adequate=True),
        qc_verdict_response(True, ops_adequate=True),
    ]
    scripts["Majority finding"] = [
        qc_verdict_response(True, ops_adequate=True),
        qc_verdict_response(True, ops_adequate=True),
        qc_verdict_response(False, note="The third reviewer refuted it."),
    ]
    scripts["Unsafe fix"] = [
        qc_verdict_response(True, ops_adequate=True),
        qc_verdict_response(
            True,
            ops_adequate=False,
            ops_note="The rewrite introduces an unresolved choice.",
        ),
    ]
    scripts["Advisory only"] = [
        qc_verdict_response(True, ops_adequate=True),
        qc_verdict_response(True, ops_adequate=True),
    ]
    scripts["Refuted fix"] = [
        qc_verdict_response(False),
        qc_verdict_response(False),
    ]

    result = _run(SequencedFakeClient(scripts))
    survivors = {finding.title: finding for finding in result.findings}
    assert survivors["Unanimous fix"].ops_semantic_status == "approved"
    assert survivors["Unanimous fix"].ops_valid is True
    assert survivors["Majority finding"].verification_outcome == "upheld"
    assert survivors["Majority finding"].ops_semantic_status == "rejected"
    assert survivors["Majority finding"].ops_valid is False
    assert survivors["Unsafe fix"].ops_semantic_status == "rejected"
    assert "unresolved choice" in survivors["Unsafe fix"].ops_semantic_reason
    assert survivors["Advisory only"].ops_semantic_status == "not_proposed"
    assert survivors["Advisory only"].ops_valid is False
    assert result.refuted[0].ops_semantic_status == "not_evaluated"


class _InvalidRequestVerifierClient(SequencedFakeClient):
    def __init__(self, scripts: dict[str, list]):
        super().__init__(scripts)
        self.verifier_request_count = 0

    def stream(self, **request):
        user_message = _user_text(request.get("messages") or [])
        if "[[QC-VERIFY:" not in user_message:
            return super().stream(**request)
        with self._lock:
            self.requests.append(request)
            self.verifier_request_count += 1
        raise _bad_request_error()


class _SynchronizedInvalidRequestVerifierClient(
    _InvalidRequestVerifierClient
):
    """Release one full pool's worth of verifier requests like a provider 400."""

    def __init__(self, scripts: dict[str, list]):
        super().__init__(scripts)
        self._verifier_barrier = threading.Barrier(settings.QC_MAX_WORKERS)

    def stream(self, **request):
        user_message = _user_text(request.get("messages") or [])
        if "[[QC-VERIFY:" not in user_message:
            return SequencedFakeClient.stream(self, **request)
        with self._lock:
            self.requests.append(request)
            self.verifier_request_count += 1
        self._verifier_barrier.wait(timeout=5)
        raise _bad_request_error()


def test_invalid_request_circuit_breaker_caps_calls_and_preserves_every_seat() -> None:
    findings = [_finding(f"Circuit finding {index}") for index in range(8)]
    client = _SynchronizedInvalidRequestVerifierClient(_scripts(findings))

    result = _run(client)

    assert client.verifier_request_count == settings.QC_MAX_WORKERS
    assert result.execution_status == "partial"
    assert result.findings == []
    assert result.refuted == []
    assert len(result.inconclusive) == len(findings)
    all_verdicts = [
        verdict for finding in result.inconclusive for verdict in finding.verdicts
    ]
    assert len(all_verdicts) == len(findings) * 2
    assert all(verdict.status == "failed" for verdict in all_verdicts)
    assert sum(verdict.api_request_count for verdict in all_verdicts) == (
        client.verifier_request_count
    )
    synthetic = [
        verdict
        for verdict in all_verdicts
        if "was not started after a shared request failure" in verdict.error
    ]
    assert synthetic
    assert all(
        verdict.api_request_count == 0 and verdict.model_response_count == 0
        for verdict in synthetic
    )
    assert all(
        "Invalid verifier output schema" in verdict.error for verdict in synthetic
    )


def test_output_and_ordinary_call_failures_do_not_trip_shared_breaker() -> None:
    findings = [
        _finding("Malformed output"),
        _finding("Ordinary call failure"),
        _finding("Post-response invalid request"),
        _finding("Later candidate"),
    ]
    scripts = _scripts(findings)
    malformed = qc_verdict_response(True)
    malformed.content[-1].input.pop("ops_adequate")
    scripts["Malformed output"] = [malformed, qc_verdict_response(True)]
    scripts["Ordinary call failure"] = [
        RuntimeError("one verifier failed"),
        qc_verdict_response(True),
    ]
    scripts["Post-response invalid request"] = [
        qc_verdict_response(True, stop_reason="pause_turn"),
        _bad_request_error(),
        qc_verdict_response(True),
    ]
    scripts["Later candidate"] = [
        qc_verdict_response(True),
        qc_verdict_response(True),
    ]
    client = SequencedFakeClient(scripts)

    result = _run(client)

    verifier_requests = [
        request
        for request in client.requests
        if "[[QC-VERIFY:" in _user_text(request["messages"])
    ]
    assert len(verifier_requests) == 9
    assert {finding.title for finding in result.inconclusive} == {
        "Malformed output",
        "Ordinary call failure",
        "Post-response invalid request",
    }
    assert [finding.title for finding in result.findings] == ["Later candidate"]


def test_v3_semantic_fields_round_trip_and_v2_remains_readable() -> None:
    finding = _finding("Round-trip fix", ops=_replace_op("Replacement."))
    scripts = _scripts([finding])
    scripts["Round-trip fix"] = [
        qc_verdict_response(True),
        qc_verdict_response(True),
    ]
    result = _run(SequencedFakeClient(scripts))
    payload = result.to_dict()

    assert payload["schema_version"] == 3 == QC_REPORT_SCHEMA_VERSION
    assert payload["protocol_version"] == "final-qc/3" == QC_PROTOCOL_VERSION
    restored = QCResult.from_dict(copy.deepcopy(payload))
    assert restored is not None
    assert restored.findings[0].ops_semantic_status == "approved"
    assert all(verdict.ops_adequate for verdict in restored.findings[0].verdicts)

    missing_verdict_field = copy.deepcopy(payload)
    del missing_verdict_field["findings"][0]["verdicts"][0]["ops_adequate"]
    assert QCResult.from_dict(missing_verdict_field) is None
    bad_semantic_status = copy.deepcopy(payload)
    bad_semantic_status["findings"][0]["ops_semantic_status"] = "approved-ish"
    assert QCResult.from_dict(bad_semantic_status) is None

    legacy = copy.deepcopy(payload)
    legacy["schema_version"] = 2
    legacy["protocol_version"] = "final-qc/2"
    for raw_finding in legacy["findings"]:
        raw_finding.pop("ops_semantic_status")
        raw_finding.pop("ops_semantic_reason")
        for raw_verdict in raw_finding["verdicts"]:
            raw_verdict.pop("ops_adequate")
            raw_verdict.pop("ops_note")
    restored_legacy = QCResult.from_dict(legacy)
    assert restored_legacy is not None
    assert restored_legacy.schema_version == 2
    assert restored_legacy.schema_version != QC_REPORT_SCHEMA_VERSION
