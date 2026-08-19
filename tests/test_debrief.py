"""The completion-debrief endpoints and their server-owned directives.

The moment a research round or a Final QC run completes, the frontend
fetches one of these messages and sends it through the ordinary chat path —
the draft_full pattern. These tests pin the guard matrices, the fact
derivation (the model must not have to re-derive round telemetry or finding
counts), and the honest variants: nothing-new rounds, partial QC runs,
stale reviews, and clean passes.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from backend import sessions
from backend.app import create_app
from backend.llm.prompts import (
    QcDebriefFacts,
    ResearchDebriefFacts,
    qc_debrief_directive,
    research_debrief_directive,
)
from backend.qc.engine import QCFinding, QCLensStatus, QCResult
from backend.research.engine import (
    DimensionStatus,
    RequirementsProfile,
    ResearchItem,
    ResearchRound,
)
from tests.fakes import audit_grade_qc_result


def _client() -> TestClient:
    return TestClient(create_app())


def _item(item_id: str, dimension_id: str, *, grounded: bool) -> ResearchItem:
    return ResearchItem(
        item_id=item_id,
        dimension_id=dimension_id,
        topic="Adopted codes",
        category="code_adoption",
        requirement=f"Requirement behind {item_id}.",
        grounded=grounded,
        confidence=0.8 if grounded else 0.3,
    )


def _install_profile(*, new_items: int = 2, repeat_items: int = 0) -> None:
    session = sessions.get_session()
    module_dims = list(session.module.research_dimensions)
    completed = module_dims[:2]
    profile = RequirementsProfile(
        items=[
            _item("r-aaaa11112222", completed[0].dimension_id, grounded=True),
            _item("r-bbbb33334444", completed[1].dimension_id, grounded=False),
        ],
        dimension_statuses=[
            DimensionStatus(
                dimension_id=d.dimension_id,
                status="completed",
                title=d.title,
                item_count=1,
            )
            for d in completed
        ],
        research_date="2026-08-19",
        rounds=[
            ResearchRound(
                round_index=2,
                research_date="2026-08-19",
                dimension_statuses=[
                    DimensionStatus(
                        dimension_id=completed[0].dimension_id,
                        status="completed",
                        title=completed[0].title,
                    ),
                    DimensionStatus(
                        dimension_id=completed[1].dimension_id,
                        status="failed",
                        title=completed[1].title,
                        error="rate limited",
                        error_kind="rate_limit",
                    ),
                ],
                new_items=new_items,
                repeat_items=repeat_items,
            )
        ],
    )
    session.research.profile_result = profile
    session.research.status = "complete"


# --- Research debrief endpoint --------------------------------------------


def test_research_debrief_refuses_without_a_settled_round() -> None:
    client = _client()
    assert client.post("/api/research/debrief").status_code == 409

    session = sessions.get_session()
    _install_profile()
    session.research.status = "running"
    try:
        assert client.post("/api/research/debrief").status_code == 409
    finally:
        session.research.status = "complete"

    session.turn_active = True
    try:
        assert client.post("/api/research/debrief").status_code == 409
    finally:
        session.turn_active = False

    assert client.post("/api/research/debrief").status_code == 200


def test_research_debrief_quotes_the_round_and_the_coverage() -> None:
    client = _client()
    _install_profile(new_items=2, repeat_items=1)
    payload = client.post("/api/research/debrief").json()
    assert payload["ok"] is True
    message = payload["message"]
    assert "round 2 just completed" in message
    assert "2 new item(s)" in message
    assert "re-confirmed 1" in message
    assert "2 item(s), 1 grounded" in message
    session = sessions.get_session()
    dims = list(session.module.research_dimensions)
    # The round roster and its failure are named…
    assert dims[0].title in message
    assert "FAILED this round" in message and dims[1].title in message
    # …and so is every never-completed declared area, required-labeled.
    assert dims[2].title in message
    assert "(required)" in message
    assert "findings ABSENT, not verified-empty" in message
    # The obligations: propose, never apply; chips; the closing ask.
    assert "do not apply anything in this turn" in message
    assert "Yes — apply the proposed changes" in message


def test_a_nothing_new_round_buys_the_short_confirmation_variant() -> None:
    client = _client()
    _install_profile(new_items=0, repeat_items=3)
    message = client.post("/api/research/debrief").json()["message"]
    assert "Nothing new was found" in message
    assert "Do not re-enumerate the profile" in message
    # The full proposal obligations do not ride the short variant.
    assert "Yes — apply the proposed changes" not in message


# --- QC debrief endpoint ---------------------------------------------------


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


def _qc_finding(finding_id: str, *, ops: bool) -> QCFinding:
    operations = (
        [
            {
                "action": "replace",
                "target_id": "pt1.a1.p1",
                "text": "Provide a complete, calculated wet-pipe system.",
                "status": "confirmed",
            }
        ]
        if ops
        else []
    )
    return QCFinding(
        finding_id=finding_id,
        lens_id="coordination_consistency",
        severity="critical" if ops else "medium",
        element_id="pt1.a1.p1" if ops else "",
        title=f"Finding {finding_id}",
        issue="Debrief-test issue.",
        rationale="Debrief-test rationale.",
        reviewed_ref="1.1.A",
        proposed_ops=operations,
        ops_semantic_status="approved" if ops else "not_proposed",
        ops_valid=ops,
        verification_outcome="upheld",
    )


def _install_qc_result(*, as_fresh_attempt: bool = True) -> QCResult:
    session = sessions.get_session()
    result = audit_grade_qc_result(
        session,
        [
            _qc_finding("qc-safe000fix1", ops=True),
            _qc_finding("qc-adv0000002", ops=False),
        ],
    )
    session.qc.result = result
    session.qc.status = "complete"
    if as_fresh_attempt:
        # Mirror a live run's settlement: the attempt record and the
        # retained result are the same completed report.
        session.qc.latest_attempt_run_id = result.run_id
        session.qc.latest_attempt_status = "complete"
        session.qc.latest_attempt_result = result
    return result


def test_qc_debrief_refuses_without_a_run_and_while_busy() -> None:
    client = _client()
    assert client.post("/api/qc/debrief").status_code == 409

    session = sessions.get_session()
    session.qc.status = "running"
    try:
        assert client.post("/api/qc/debrief").status_code == 409
    finally:
        session.qc.status = "idle"

    client.post("/api/doc/edit", json={"ops": _SEED_OPS})
    _install_qc_result()
    session.turn_active = True
    try:
        assert client.post("/api/qc/debrief").status_code == 409
    finally:
        session.turn_active = False
    assert client.post("/api/qc/debrief").status_code == 200


def test_qc_debrief_counts_classes_and_stages_the_approval_chip() -> None:
    client = _client()
    client.post("/api/doc/edit", json={"ops": _SEED_OPS})
    _install_qc_result()
    payload = client.post("/api/qc/debrief").json()
    assert payload["ok"] is True
    message = payload["message"]
    assert "Open findings: 2 (1 critical)" in message
    assert "1 with a verified safe fix, 1 advisory" in message
    assert "PROPOSE ONLY" in message
    assert "Yes — apply the verified safe fixes" in message
    assert "apply_qc_fixes" in message
    # A fresh completed attempt is NOT briefed as a retained-review recall.
    assert "RETAINED review" not in message


def test_debriefing_with_only_a_retained_review_says_so() -> None:
    """A project-load case: no fresh attempt, just the retained report."""
    client = _client()
    client.post("/api/doc/edit", json={"ops": _SEED_OPS})
    _install_qc_result(as_fresh_attempt=False)
    message = client.post("/api/qc/debrief").json()["message"]
    assert "RETAINED review" in message


def test_qc_debrief_marks_a_stale_review() -> None:
    client = _client()
    client.post("/api/doc/edit", json={"ops": _SEED_OPS})
    _install_qc_result()
    client.post(
        "/api/doc/edit",
        json={
            "ops": [
                {
                    "action": "add_paragraph",
                    "target_id": "pt1.a1",
                    "text": "Coordinate all interfaces.",
                    "status": "confirmed",
                }
            ]
        },
    )
    message = client.post("/api/qc/debrief").json()["message"]
    assert "STALE against the current document" in message
    assert "cannot be applied until Final QC is re-run" in message


def test_a_partial_attempt_gets_the_constrained_variant() -> None:
    client = _client()
    client.post("/api/doc/edit", json={"ops": _SEED_OPS})
    session = sessions.get_session()
    partial = QCResult(
        execution_status="partial",
        run_id="qc-partial-1",
        lens_statuses=[
            QCLensStatus(
                lens_id="completeness",
                title="Completeness",
                brief="",
                status="failed",
                error="rate limited",
            )
        ],
    )
    session.qc.latest_attempt_run_id = "qc-partial-1"
    session.qc.latest_attempt_status = "partial"
    session.qc.latest_attempt_result = partial
    session.qc.status = "failed"
    message = client.post("/api/qc/debrief").json()["message"]
    assert "the run is PARTIAL" in message
    assert "Completeness" in message
    assert "Nothing from an incomplete run is applyable" in message
    assert "Yes — apply the verified safe fixes" not in message


def test_a_clean_pass_says_so() -> None:
    client = _client()
    client.post("/api/doc/edit", json={"ops": _SEED_OPS})
    session = sessions.get_session()
    result = audit_grade_qc_result(session, [])
    session.qc.result = result
    session.qc.status = "complete"
    message = client.post("/api/qc/debrief").json()["message"]
    assert "The review is clean" in message
    assert "Apply nothing this turn" in message


# --- Pure directive units --------------------------------------------------


def test_research_directive_units_cover_the_obligations() -> None:
    facts = ResearchDebriefFacts(
        round_index=1,
        new_items=4,
        repeat_items=0,
        cumulative_items=4,
        grounded_items=3,
        areas_run=("Governing codes",),
    )
    text = research_debrief_directive(facts)
    assert "PROPOSE ONLY" in text
    assert "suggested replies" in text
    assert "asking whether I want you to proceed" in text


def test_qc_directive_units_cover_the_three_classes() -> None:
    facts = QcDebriefFacts(
        execution_status="complete",
        open_criticals=1,
        open_findings=3,
        open_disputed=1,
        safe_fixes=2,
        advisory=1,
        applied=0,
        dismissed=0,
        refuted=2,
        inconclusive=0,
    )
    text = qc_debrief_directive(facts)
    assert "verified safe fixes" in text
    assert "advisory findings" in text
    assert "disputed candidates" in text
    assert "PROPOSE ONLY" in text
    assert "Open disputed candidates: 1" in text
