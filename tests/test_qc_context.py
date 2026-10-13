"""The FINAL QC REVIEW block: Final QC results reach the chat model.

Pins the investigation's fix — QC lived on its own channel and the chat
model never saw a single finding. The block rides the dynamic PROJECT
CONTEXT (never the stable prompt, never committed history) and renders the
compact action-queue projection: ids, severity, fix class, staleness.
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from backend import sessions
from backend.app import create_app
from backend.qc.context import qc_review_context_block
from backend.qc.engine import QCFinding, QCResult
from tests.fakes import (
    FakeClient,
    audit_grade_qc_result,
    request_context_text,
    text_turn,
)


def _client() -> TestClient:
    return TestClient(create_app())


def _patch_client(monkeypatch, fake: FakeClient) -> None:
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)


_SEED_OPS = [
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
        "text": "Provide a complete wet-pipe system.",
        "status": "confirmed",
    },
]


def _finding(
    finding_id: str,
    *,
    severity: str = "high",
    ops: list[dict] | None = None,
    title: str = "",
) -> QCFinding:
    return QCFinding(
        finding_id=finding_id,
        lens_id="coordination_consistency",
        severity=severity,
        element_id="pt1.a1.p1" if ops else "",
        title=title or f"Finding {finding_id}",
        issue=f"The issue behind {finding_id}.",
        rationale="Endpoint-test rationale.",
        reviewed_ref="1.1.A",
        proposed_ops=list(ops or []),
        ops_semantic_status="approved" if ops else "not_proposed",
        ops_valid=bool(ops),
        verification_outcome="upheld",
    )


def _seed_and_install(client: TestClient) -> QCResult:
    assert client.post("/api/doc/edit", json={"ops": _SEED_OPS}).json()["ok"]
    session = sessions.get_session()
    replace_op = {
        "action": "replace",
        "target_id": "pt1.a1.p1",
        "text": "Provide a complete, hydraulically calculated wet-pipe system.",
        "status": "confirmed",
    }
    result = audit_grade_qc_result(
        session,
        [
            _finding("qc-safe000fix1", ops=[replace_op], title="Vague scope"),
            _finding("qc-advisory002", severity="medium", title="Needs a look"),
        ],
    )
    session.qc.result = result
    session.qc.status = "complete"
    return result


def test_qc_findings_reach_chat_context_but_never_the_stable_prompt(
    monkeypatch,
) -> None:
    client = _client()
    _seed_and_install(client)
    fake = FakeClient([text_turn(["ok"])])
    _patch_client(monkeypatch, fake)
    client.post("/api/chat", json={"message": "what did the review find?"})

    request = fake.messages.last_request
    context = request_context_text(request)
    assert "FINAL QC REVIEW" in context
    assert "qc-safe000fix1" in context
    assert "[high] qc-safe000fix1 1.1.A: Vague scope" in context
    assert "verified safe fix, 1 op(s)" in context
    assert "qc-advisory002" in context
    assert "advisory only" in context
    assert "Freshness: CURRENT" in context
    # The stable (cached) block carries the static POLICY — which may name
    # the block it explains — but never a byte of session data.
    stable = request["system"][0]["text"]
    assert "qc-safe000fix1" not in stable
    assert "Vague scope" not in stable
    assert "Freshness:" not in stable
    assert "# Final QC findings" in stable
    assert "apply_qc_fixes" in stable


def test_without_a_qc_result_no_block_renders(monkeypatch) -> None:
    client = _client()
    fake = FakeClient([text_turn(["ok"])])
    _patch_client(monkeypatch, fake)
    client.post("/api/chat", json={"message": "hello"})
    assert "FINAL QC REVIEW" not in request_context_text(
        fake.messages.last_request
    )


def test_an_edit_after_the_review_flips_the_block_stale(monkeypatch) -> None:
    client = _client()
    _seed_and_install(client)
    assert client.post(
        "/api/doc/edit",
        json={
            "ops": [
                {
                    "action": "add_paragraph",
                    "target_id": "pt1.a1",
                    "text": "Coordinate every trade interface.",
                    "status": "confirmed",
                }
            ]
        },
    ).json()["ok"]
    fake = FakeClient([text_turn(["ok"])])
    _patch_client(monkeypatch, fake)
    client.post("/api/chat", json={"message": "and now?"})
    context = request_context_text(fake.messages.last_request)
    assert "Freshness: STALE" in context
    assert "fixes CANNOT be applied until Final QC is re-run" in context


def test_the_qc_block_never_fossilizes_into_history(monkeypatch) -> None:
    client = _client()
    _seed_and_install(client)
    fake = FakeClient([text_turn(["ok"])])
    _patch_client(monkeypatch, fake)
    client.post("/api/chat", json={"message": "summarize the review"})
    history = sessions.get_session().history
    assert "FINAL QC REVIEW" not in json.dumps(history)


def _unit_result(
    findings: list[QCFinding], disputed: list[QCFinding] | None = None
) -> QCResult:
    return QCResult(findings=findings, disputed=list(disputed or []))


_LONG_ISSUE = (
    "A deliberately long issue narrative for trim-order testing, padded so "
    "that dropping one rendered finding line frees a predictable, "
    "substantial number of estimated tokens from the candidate block. " * 3
)


def test_trim_drops_lowest_severity_first_and_discloses() -> None:
    findings = [
        _finding("qc-critical0001", severity="critical", title="Life safety"),
        _finding("qc-low00000001", severity="low"),
        _finding("qc-low00000002", severity="low"),
        _finding("qc-low00000003", severity="low"),
    ]
    for f in findings:
        f.issue = _LONG_ISSUE
    result = _unit_result(findings)
    section = sessions.get_session().doc.doc
    full, trimmed = qc_review_context_block(
        result, current_version_index=0, current_section=section
    )
    assert trimmed == 0
    # A cap just under the full render forces exactly one long line out.
    block, trimmed = qc_review_context_block(
        result,
        current_version_index=0,
        current_section=section,
        max_tokens=(len(full) // 4) - 10,
    )
    assert trimmed == 1
    assert "qc-critical0001" in block
    assert "qc-low00000003" not in block  # lowest severity, later-first
    assert "qc-low00000001" in block
    assert "1 finding(s) were trimmed" in block
    assert "PARTIAL" in block


def test_open_disputed_candidates_trim_last() -> None:
    survivors = [
        _finding("qc-surv0000001", severity="low"),
        _finding("qc-surv0000002", severity="low"),
    ]
    disputed = [_finding("qc-disp0000001", severity="medium")]
    disputed[0].verification_outcome = "disputed"
    for f in [*survivors, *disputed]:
        f.issue = _LONG_ISSUE
    result = _unit_result(survivors, disputed)
    section = sessions.get_session().doc.doc
    full, _ = qc_review_context_block(
        result, current_version_index=0, current_section=section
    )
    block, trimmed = qc_review_context_block(
        result,
        current_version_index=0,
        current_section=section,
        max_tokens=(len(full) // 4) - 10,
    )
    # Exactly one trim, and it took a SURVIVOR: the dispute blocks issue
    # readiness and needs the user's decision, so it is the line that
    # survives longest.
    assert trimmed == 1
    assert "qc-disp0000001" in block
    assert "qc-surv0000002" not in block
    assert "qc-surv0000001" in block
    # An impossible cap eventually takes everything — and terminates.
    _, trimmed_all = qc_review_context_block(
        result,
        current_version_index=0,
        current_section=section,
        max_tokens=1,
    )
    assert trimmed_all == 3


def test_a_fully_dispositioned_report_still_renders_the_rollup() -> None:
    applied = _finding("qc-applied00001")
    applied.status = "applied"
    dismissed = _finding("qc-dismissed001", severity="low")
    dismissed.status = "dismissed"
    result = _unit_result([applied, dismissed])
    section = sessions.get_session().doc.doc
    block, trimmed = qc_review_context_block(
        result, current_version_index=0, current_section=section
    )
    assert trimmed == 0
    assert "OPEN FINDINGS: none" in block
    assert "1 applied" in block
    assert "1 dismissed" in block


# ---------------------------------------------------------------------------
# Freshness covers every review input, not just the document (2026-10-13)
# ---------------------------------------------------------------------------


def test_a_review_input_moved_after_the_review_reads_stale(monkeypatch) -> None:
    """Research re-run after Final QC, document untouched: ``apply_qc_fixes``
    and the readiness gate would call the review stale, so the block must
    too — it said CURRENT on the document version alone before."""
    from tests.test_context_sizes import _profile

    client = _client()
    _seed_and_install(client)
    fake = FakeClient([text_turn(["ok"]), text_turn(["ok"])])
    _patch_client(monkeypatch, fake)

    client.post("/api/chat", json={"message": "what did the review find?"})
    fresh = request_context_text(fake.messages.last_request)
    assert "Freshness: CURRENT" in fresh

    sessions.get_session().research.profile_result = _profile(3)
    client.post("/api/chat", json={"message": "and now?"})
    moved = request_context_text(fake.messages.last_request)
    assert "Freshness: STALE — the document is unchanged, but another review input" in moved
    assert "Fixes CANNOT be applied until Final QC is re-run" in moved
    assert "Freshness: CURRENT" not in moved
    # The readiness gate agrees.
    checks = {c["id"]: c for c in client.get("/api/readiness").json()["checks"]}
    assert checks["qc_current"]["ok"] is False


def test_a_freshness_check_that_fails_never_fails_the_turn(monkeypatch) -> None:
    client = _client()
    _seed_and_install(client)

    def boom(*_args, **_kwargs):
        raise RuntimeError("manifest unavailable")

    monkeypatch.setattr("backend.llm.conversation.matches_current_inputs", boom)
    fake = FakeClient([text_turn(["ok"])])
    _patch_client(monkeypatch, fake)
    resp = client.post("/api/chat", json={"message": "status?"})
    assert '"type": "error"' not in resp.text
    context = request_context_text(fake.messages.last_request)
    # Read as not current — the conservative answer, never CURRENT.
    assert "another review input changed" in context


def test_the_document_reason_wins_when_both_moved() -> None:
    from backend.spec_doc.model import SpecSection

    result = _unit_result([_finding("qc-a00000000001")])
    section = SpecSection.empty()
    # A unit result carries no version fingerprint, so it never matches the
    # document: the document reason is the one said, inputs or not.
    for inputs_current in (True, False):
        block, _ = qc_review_context_block(
            result,
            current_version_index=0,
            current_section=section,
            inputs_current=inputs_current,
        )
        assert "Freshness: STALE — the document changed after this review." in block
        assert "another review input" not in block


def test_a_stale_review_never_offers_a_fix_as_applyable(monkeypatch) -> None:
    """The freshness line and the finding rows agree (Codex review on PR
    #321): once the review is stale, for either reason, no row and no header
    says "apply via apply_qc_fixes"."""
    from tests.test_context_sizes import _profile

    client = _client()
    _seed_and_install(client)
    fake = FakeClient([text_turn(["ok"]), text_turn(["ok"])])
    _patch_client(monkeypatch, fake)

    client.post("/api/chat", json={"message": "fresh?"})
    fresh = request_context_text(fake.messages.last_request)
    assert "verified safe fix, 1 op(s) — apply via apply_qc_fixes" in fresh
    assert "ids are apply_qc_fixes inputs" in fresh

    sessions.get_session().research.profile_result = _profile(3)
    client.post("/api/chat", json={"message": "and now?"})
    moved = request_context_text(fake.messages.last_request)
    assert "verified safe fix, 1 op(s) — not applyable until Final QC is re-run" in moved
    assert "apply via apply_qc_fixes" not in moved
    assert "ids are apply_qc_fixes inputs" not in moved
    assert "the review is stale, so none is applyable" in moved


def test_a_document_stale_review_never_offers_a_fix_as_applyable() -> None:
    result = _unit_result(
        [
            _finding(
                "qc-safe00000001",
                ops=[{"action": "replace", "target_id": "pt1.a1.p1", "text": "X."}],
            )
        ]
    )
    from backend.spec_doc.model import SpecSection

    block, _ = qc_review_context_block(
        result, current_version_index=0, current_section=SpecSection.empty()
    )
    assert "Freshness: STALE — the document changed" in block
    assert "verified safe fix, 1 op(s) — not applyable until Final QC is re-run" in block
    assert "apply via apply_qc_fixes" not in block
