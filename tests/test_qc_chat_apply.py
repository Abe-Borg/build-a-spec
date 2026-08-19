"""The apply_qc_fixes chat tool + the shared QC apply machinery.

The HTTP apply route and the chat tool must run ONE implementation of the
eligibility/freshness/dry-run policy (backend/qc/apply.py); the first tests
here pin the app.py aliases to that single implementation so a refactor that
quietly forks the policy fails loudly rather than drifting.

The tool's disposition commit shares the turn's ONE commit block — the same
guarded region test_stop.py pins for the stop path and the chunk-8 stress
suite pins for generation races — so a user stop commits staged
dispositions with the doc edits it kept, and a reset-raced turn records
nothing, by the same mechanism those suites already hold in place. What is
pinned here is the QC-specific half: dispositions land ONLY at commit, with
the committed version identity, against the exact result object they
describe.
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

import backend.app as app_module
from backend import sessions
from backend.app import create_app
from backend.qc import apply as qc_apply
from backend.qc.engine import QCFinding, QCResult, qc_version_fingerprint
from tests.fakes import (
    FakeClient,
    audit_grade_qc_result,
    text_turn,
    tool_turn,
)


def _client() -> TestClient:
    return TestClient(create_app())


def _patch_client(monkeypatch, fake: FakeClient) -> None:
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)


def _parse_sse(body: str) -> list[dict]:
    events = []
    for line in body.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: "):]))
    return events


def _tool_results(request: dict) -> list[dict]:
    return [
        block
        for message in request["messages"]
        if isinstance(message.get("content"), list)
        for block in message["content"]
        if isinstance(block, dict) and block.get("type") == "tool_result"
    ]


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

_FIXED_TEXT = "Provide a complete, hydraulically calculated wet-pipe system."


def _finding(finding_id: str, ops: list[dict]) -> QCFinding:
    return QCFinding(
        finding_id=finding_id,
        lens_id="coordination_consistency",
        severity="high",
        element_id=str((ops[0].get("target_id") if ops else "") or ""),
        title=f"Finding {finding_id}",
        issue="Chat-apply test issue.",
        rationale="Chat-apply test rationale.",
        reviewed_ref="1.1.A",
        proposed_ops=ops,
        ops_semantic_status="approved" if ops else "not_proposed",
        ops_valid=bool(ops),
        verification_outcome="upheld",
    )


def _seed_and_install(
    client: TestClient, findings: list[QCFinding]
) -> QCResult:
    assert client.post("/api/doc/edit", json={"ops": _SEED_OPS}).json()["ok"]
    session = sessions.get_session()
    result = audit_grade_qc_result(session, findings)
    session.qc.result = result
    session.qc.status = "complete"
    session.qc.latest_attempt_run_id = result.run_id
    session.qc.latest_attempt_status = "complete"
    session.qc.latest_attempt_result = result
    return result


def _safe_fix_finding(finding_id: str = "qc-safe000fix1") -> QCFinding:
    return _finding(
        finding_id,
        [
            {
                "action": "replace",
                "target_id": "pt1.a1.p1",
                "text": _FIXED_TEXT,
                "status": "confirmed",
            }
        ],
    )


def test_approved_fixes_apply_in_turn_with_audited_dispositions(
    monkeypatch,
) -> None:
    client = _client()
    _seed_and_install(client, [_safe_fix_finding()])
    fake = FakeClient(
        [
            tool_turn(
                ["Applying the approved fix… "],
                {"finding_ids": ["qc-safe000fix1"]},
                tool_id="toolu_qc1",
                name="apply_qc_fixes",
            ),
            text_turn(["Applied."]),
        ]
    )
    _patch_client(monkeypatch, fake)

    events = _parse_sse(
        client.post(
            "/api/chat", json={"message": "Yes — apply the verified safe fixes"}
        ).text
    )
    assert events[-1]["type"] == "turn_complete"
    assert any(e["type"] == "doc_patch" for e in events)
    dispositions = [e for e in events if e["type"] == "qc_dispositions"]
    assert dispositions == [
        {
            "type": "qc_dispositions",
            "outcomes": {"qc-safe000fix1": "applied"},
        }
    ]

    session = sessions.get_session()
    # The fix is in the committed document…
    assert _FIXED_TEXT in json.dumps(session.doc.doc.to_dict())
    # …and the audit disposition names the COMMITTED version identity.
    finding = session.qc.result.finding("qc-safe000fix1")
    assert finding.status == "applied"
    event = finding.disposition_events[-1]
    assert event.action == "applied"
    assert event.document_version == session.doc.index
    assert event.document_fingerprint == qc_version_fingerprint(
        session.doc.doc
    )
    # The tool result the model saw is the compact outcome payload.
    followup = fake.messages.requests[1]
    payload = json.loads(_tool_results(followup)[-1]["content"])
    assert payload["outcomes"] == {"qc-safe000fix1": "applied"}
    assert payload["applied_operations"] == 1
    # One undo step for the whole turn: undo returns the pre-turn text.
    undone = client.post("/api/doc/undo").json()
    assert undone["ok"] is True
    undo_doc = json.dumps(sessions.get_session().doc.doc.to_dict())
    assert _FIXED_TEXT not in undo_doc
    assert "Provide a complete wet-pipe system." in undo_doc


def test_qc_fixes_and_ordinary_edits_share_one_undo_step(monkeypatch) -> None:
    client = _client()
    _seed_and_install(client, [_safe_fix_finding()])
    fake = FakeClient(
        [
            tool_turn(
                ["Applying… "],
                {"finding_ids": ["qc-safe000fix1"]},
                tool_id="toolu_qc1",
                name="apply_qc_fixes",
            ),
            tool_turn(
                ["And the extra edit… "],
                {
                    "edits": [
                        {
                            "action": "add_paragraph",
                            "target_id": "pt1.a1",
                            "text": "Coordinate all trade interfaces.",
                            "status": "confirmed",
                        }
                    ]
                },
                tool_id="toolu_ed1",
            ),
            text_turn(["Done."]),
        ]
    )
    _patch_client(monkeypatch, fake)
    events = _parse_sse(
        client.post("/api/chat", json={"message": "yes, and add the note"}).text
    )
    assert events[-1]["type"] == "turn_complete"
    committed = json.dumps(sessions.get_session().doc.doc.to_dict())
    assert _FIXED_TEXT in committed
    assert "Coordinate all trade interfaces." in committed
    # An UNRELATED later edit changes none of the fix's evidence keys — the
    # applied record stands (only an edit to the fixed content voids it).
    assert (
        sessions.get_session().qc.result.finding("qc-safe000fix1").status
        == "applied"
    )
    assert client.post("/api/doc/undo").json()["ok"] is True
    reverted = json.dumps(sessions.get_session().doc.doc.to_dict())
    assert _FIXED_TEXT not in reverted
    assert "Coordinate all trade interfaces." not in reverted


def test_a_later_edit_overwriting_a_fix_voids_its_applied_record(
    monkeypatch,
) -> None:
    """"Applied" is a claim about the COMMITTED document (PR #135 review).

    A later apply_spec_edits in the SAME turn that rewrites the fixed
    provision means the committed version does not contain the verified
    remedy — the finding must stay open with an apply_stale outcome, never
    be recorded as an audited success.
    """
    client = _client()
    _seed_and_install(client, [_safe_fix_finding()])
    fake = FakeClient(
        [
            tool_turn(
                ["Applying… "],
                {"finding_ids": ["qc-safe000fix1"]},
                tool_id="toolu_qc1",
                name="apply_qc_fixes",
            ),
            tool_turn(
                ["And your rewording… "],
                {
                    "edits": [
                        {
                            "action": "replace",
                            "target_id": "pt1.a1.p1",
                            "text": "A completely different provision.",
                            "status": "confirmed",
                        }
                    ]
                },
                tool_id="toolu_ed1",
            ),
            text_turn(["Done."]),
        ]
    )
    _patch_client(monkeypatch, fake)
    events = _parse_sse(client.post("/api/chat", json={"message": "go"}).text)
    assert events[-1]["type"] == "turn_complete"
    outcomes = next(
        e for e in events if e["type"] == "qc_dispositions"
    )["outcomes"]
    assert outcomes == {"qc-safe000fix1": "stale"}
    session = sessions.get_session()
    committed = json.dumps(session.doc.doc.to_dict())
    assert "A completely different provision." in committed
    assert _FIXED_TEXT not in committed
    finding = session.qc.result.finding("qc-safe000fix1")
    assert finding.status == "open"
    event = finding.disposition_events[-1]
    assert event.action == "apply_stale"
    assert "later edit in the same turn" in event.reason


def test_a_failed_turn_rolls_back_and_records_no_disposition(
    monkeypatch,
) -> None:
    client = _client()
    _seed_and_install(client, [_safe_fix_finding()])
    fake = FakeClient(
        [
            tool_turn(
                ["Applying… "],
                {"finding_ids": ["qc-safe000fix1"]},
                tool_id="toolu_qc1",
                name="apply_qc_fixes",
            ),
            RuntimeError("boom"),  # the round after the fix blows up
        ]
    )
    _patch_client(monkeypatch, fake)
    events = _parse_sse(client.post("/api/chat", json={"message": "yes"}).text)
    assert any(e["type"] == "error" for e in events)
    assert not any(e["type"] == "qc_dispositions" for e in events)
    session = sessions.get_session()
    # Document rolled back; the finding is untouched and still open.
    assert _FIXED_TEXT not in json.dumps(session.doc.doc.to_dict())
    finding = session.qc.result.finding("qc-safe000fix1")
    assert finding.status == "open"
    assert finding.disposition_events == []


def test_a_stale_finding_is_skipped_and_recorded_beside_an_applied_one(
    monkeypatch,
) -> None:
    client = _client()
    ghost = _finding(
        "qc-ghost000001",
        [
            {
                "action": "replace",
                "target_id": "pt1.a9.p9",
                "text": "This element does not exist.",
            }
        ],
    )
    _seed_and_install(client, [_safe_fix_finding(), ghost])
    fake = FakeClient(
        [
            tool_turn(
                ["Applying… "],
                {"finding_ids": ["qc-safe000fix1", "qc-ghost000001"]},
                tool_id="toolu_qc1",
                name="apply_qc_fixes",
            ),
            text_turn(["One applied, one stale."]),
        ]
    )
    _patch_client(monkeypatch, fake)
    events = _parse_sse(client.post("/api/chat", json={"message": "yes"}).text)
    assert events[-1]["type"] == "turn_complete"
    outcomes = next(
        e for e in events if e["type"] == "qc_dispositions"
    )["outcomes"]
    assert outcomes == {
        "qc-safe000fix1": "applied",
        "qc-ghost000001": "stale",
    }
    session = sessions.get_session()
    assert session.qc.result.finding("qc-safe000fix1").status == "applied"
    ghost_after = session.qc.result.finding("qc-ghost000001")
    assert ghost_after.status == "open"
    assert ghost_after.disposition_events[-1].action == "apply_stale"


def test_an_in_turn_edit_before_the_tool_reads_as_stale(monkeypatch) -> None:
    client = _client()
    _seed_and_install(client, [_safe_fix_finding()])
    fake = FakeClient(
        [
            tool_turn(
                ["Editing first… "],
                {
                    "edits": [
                        {
                            "action": "add_paragraph",
                            "target_id": "pt1.a1",
                            "text": "An unrelated edit before the fixes.",
                            "status": "confirmed",
                        }
                    ]
                },
                tool_id="toolu_ed1",
            ),
            tool_turn(
                ["Now the fixes… "],
                {"finding_ids": ["qc-safe000fix1"]},
                tool_id="toolu_qc1",
                name="apply_qc_fixes",
            ),
            text_turn(["Understood — the review went stale."]),
        ]
    )
    _patch_client(monkeypatch, fake)
    events = _parse_sse(client.post("/api/chat", json={"message": "go"}).text)
    # The refusal is a correctable tool result, never a turn failure.
    assert events[-1]["type"] == "turn_complete"
    assert not any(e["type"] == "qc_dispositions" for e in events)
    refusal = _tool_results(fake.messages.requests[2])[-1]
    assert refusal.get("is_error") is True
    assert "first action" in refusal["content"]
    session = sessions.get_session()
    # The ordinary edit committed; the finding stayed open and unapplied.
    assert "An unrelated edit before the fixes." in json.dumps(
        session.doc.doc.to_dict()
    )
    assert session.qc.result.finding("qc-safe000fix1").status == "open"


def test_a_review_staled_before_the_turn_is_refused(monkeypatch) -> None:
    client = _client()
    _seed_and_install(client, [_safe_fix_finding()])
    client.post(
        "/api/doc/edit",
        json={
            "ops": [
                {
                    "action": "add_paragraph",
                    "target_id": "pt1.a1",
                    "text": "Edited after the review.",
                    "status": "confirmed",
                }
            ]
        },
    )
    fake = FakeClient(
        [
            tool_turn(
                ["Applying… "],
                {"finding_ids": ["qc-safe000fix1"]},
                tool_id="toolu_qc1",
                name="apply_qc_fixes",
            ),
            text_turn(["The review is stale."]),
        ]
    )
    _patch_client(monkeypatch, fake)
    events = _parse_sse(client.post("/api/chat", json={"message": "yes"}).text)
    assert events[-1]["type"] == "turn_complete"
    refusal = _tool_results(fake.messages.requests[1])[-1]
    assert refusal.get("is_error") is True
    assert "no longer matches" in refusal["content"]
    assert (
        sessions.get_session().qc.result.finding("qc-safe000fix1").status
        == "open"
    )


def test_a_running_qc_pass_refuses_the_tool(monkeypatch) -> None:
    client = _client()
    _seed_and_install(client, [_safe_fix_finding()])
    session = sessions.get_session()
    session.qc.status = "running"
    fake = FakeClient(
        [
            tool_turn(
                ["Applying… "],
                {"finding_ids": ["qc-safe000fix1"]},
                tool_id="toolu_qc1",
                name="apply_qc_fixes",
            ),
            text_turn(["Cannot apply while a run is active."]),
        ]
    )
    _patch_client(monkeypatch, fake)
    try:
        events = _parse_sse(
            client.post("/api/chat", json={"message": "yes"}).text
        )
    finally:
        session.qc.status = "complete"
    assert events[-1]["type"] == "turn_complete"
    refusal = _tool_results(fake.messages.requests[1])[-1]
    assert refusal.get("is_error") is True
    assert "active or settling" in refusal["content"]


def test_conflicting_fixes_are_refused_whole(monkeypatch) -> None:
    client = _client()
    conflict_a = _safe_fix_finding("qc-conflict001")
    conflict_b = _finding(
        "qc-conflict002",
        [
            {
                "action": "replace",
                "target_id": "pt1.a1.p1",
                "text": "A different replacement for the same element.",
            }
        ],
    )
    _seed_and_install(client, [conflict_a, conflict_b])
    fake = FakeClient(
        [
            tool_turn(
                ["Applying… "],
                {"finding_ids": ["qc-conflict001", "qc-conflict002"]},
                tool_id="toolu_qc1",
                name="apply_qc_fixes",
            ),
            text_turn(["Those conflict."]),
        ]
    )
    _patch_client(monkeypatch, fake)
    events = _parse_sse(client.post("/api/chat", json={"message": "yes"}).text)
    assert events[-1]["type"] == "turn_complete"
    refusal = _tool_results(fake.messages.requests[1])[-1]
    assert refusal.get("is_error") is True
    assert "conflicting operations" in refusal["content"]
    assert "qc-conflict001" in refusal["content"]
    session = sessions.get_session()
    assert session.qc.result.finding("qc-conflict001").status == "open"
    assert session.qc.result.finding("qc-conflict002").status == "open"


def test_a_malformed_call_is_correctable_within_the_turn(monkeypatch) -> None:
    client = _client()
    _seed_and_install(client, [_safe_fix_finding()])
    fake = FakeClient(
        [
            tool_turn(
                ["Trying… "],
                {"finding_ids": "qc-safe000fix1"},  # not a list
                tool_id="toolu_bad",
                name="apply_qc_fixes",
            ),
            tool_turn(
                ["Correcting… "],
                {"finding_ids": ["qc-safe000fix1"]},
                tool_id="toolu_qc1",
                name="apply_qc_fixes",
            ),
            text_turn(["Applied on the second try."]),
        ]
    )
    _patch_client(monkeypatch, fake)
    events = _parse_sse(client.post("/api/chat", json={"message": "yes"}).text)
    assert events[-1]["type"] == "turn_complete"
    first = _tool_results(fake.messages.requests[1])[-1]
    assert first.get("is_error") is True
    assert (
        sessions.get_session().qc.result.finding("qc-safe000fix1").status
        == "applied"
    )


def test_disputed_and_refuted_candidates_never_reach_apply() -> None:
    """Per-finding treatment at the shared eligibility policy.

    A disputed candidate is reachable (dismissable) but its operations are
    never panel-approved, so it classifies no_ops; a refuted candidate is
    not reachable at all — an audit record, not an action queue.
    """
    disputed = _finding("qc-disputed0001", [])
    disputed.verification_outcome = "disputed"
    refuted = _safe_fix_finding("qc-refuted00001")
    refuted.verification_outcome = "refuted"
    result = QCResult(findings=[], disputed=[disputed], refuted=[refuted])
    outcomes, skipped, eligible = qc_apply.select_apply_candidates(
        result, ["qc-disputed0001", "qc-refuted00001"]
    )
    assert outcomes == {
        "qc-disputed0001": "no_ops",
        "qc-refuted00001": "unknown",
    }
    assert eligible == []
    assert [event[0] for event in skipped] == ["qc-disputed0001"]


def test_a_dismissal_racing_the_staged_apply_keeps_both_events() -> None:
    """The record-both default for the dismiss race (no turn_active guard on
    /api/qc/dismiss is pre-existing): the ops ARE in the committed document,
    so the final status is applied, and the trail keeps both decisions."""
    client = _client()
    result = _seed_and_install(client, [_safe_fix_finding()])
    session = sessions.get_session()
    assert session.qc.dismiss(
        "qc-safe000fix1", "Setting it aside mid-turn."
    )
    session.qc.mark_applied(
        ["qc-safe000fix1"],
        document_version=session.doc.index,
        document_fingerprint=qc_version_fingerprint(session.doc.doc),
    )
    finding = result.finding("qc-safe000fix1")
    assert finding.status == "applied"
    actions = [e.action for e in finding.disposition_events]
    assert actions == ["dismissed", "applied"]
    # Dismiss memory does not survive the apply: the id left dismissed_ids.
    assert "qc-safe000fix1" not in result.dismissed_ids


def test_the_route_and_the_tool_share_one_apply_implementation() -> None:
    """app.py's historical names are aliases of backend/qc/apply.py."""
    assert app_module._qc_source_guard is qc_apply.build_source_guard
    assert (
        app_module._qc_matches_current_inputs is qc_apply.matches_current_inputs
    )
    assert (
        app_module._qc_result_is_audit_complete
        is qc_apply.result_is_audit_complete
    )
    assert (
        app_module._dry_run_qc_apply_findings is qc_apply.dry_run_apply_findings
    )
    assert app_module._source_baseline is qc_apply.source_baseline
    assert app_module._UNSAMPLED is qc_apply.UNSAMPLED


def test_fix_class_matches_the_apply_gates_eligibility_condition() -> None:
    """finding_fix_class is exactly the apply gate's safe-fix condition."""

    class _F:
        ops_semantic_status = "approved"
        ops_valid = True
        proposed_ops = [{"action": "replace"}]

    safe = _F()
    assert qc_apply.finding_fix_class(safe) == qc_apply.FIX_CLASS_SAFE

    rejected = _F()
    rejected.ops_semantic_status = "rejected"
    assert qc_apply.finding_fix_class(rejected) == qc_apply.FIX_CLASS_ADVISORY

    invalid = _F()
    invalid.ops_valid = False
    assert qc_apply.finding_fix_class(invalid) == qc_apply.FIX_CLASS_ADVISORY

    empty = _F()
    empty.proposed_ops = []
    assert qc_apply.finding_fix_class(empty) == qc_apply.FIX_CLASS_ADVISORY
