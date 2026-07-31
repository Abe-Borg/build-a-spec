"""History-safety regressions for applied Final-QC dispositions."""
from __future__ import annotations

from fastapi.testclient import TestClient

from backend import sessions
from backend.app import create_app
from backend.qc.engine import QCFinding
from backend.spec_doc.model import DocumentStore
from tests.fakes import audit_grade_qc_result


def _reviewed_store() -> DocumentStore:
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
                "status": "confirmed",
            },
        ]
    )
    store.commit_turn()
    return store


def _readiness_check(client: TestClient, check_id: str) -> dict:
    return next(
        check
        for check in client.get("/api/readiness").json()["checks"]
        if check["id"] == check_id
    )


def test_apply_replies_with_the_version_it_committed_not_a_later_one(
    monkeypatch,
) -> None:
    """The response has to describe the apply's own result.

    ``_doc_payload`` was built after the committing guard released, so a
    mutation landing in that window — another undoable edit, a turn's
    commit — was what the caller got back, beside outcomes describing an
    apply it never saw. The frontend replaces its whole document state from
    this payload, so the user's panel would jump to a version the apply did
    not produce.

    The seam is the trace event, which really does run between the guard and
    the response: whatever mutates there must not reach the reply.
    """
    from backend.app import _trace_capture

    client = TestClient(create_app())
    session = sessions.get_session()
    session.doc = _reviewed_store()
    finding = QCFinding(
        finding_id="qc-payload-freshness",
        lens_id="code_compliance",
        severity="high",
        element_id="pt1.a1.p1",
        title="Update the referenced edition",
        issue="The reviewed provision cites an obsolete project edition.",
        rationale="Exercise the applied response payload's coherence.",
        proposed_ops=[
            {
                "action": "replace",
                "target_id": "pt1.a1.p1",
                "text": "Comply with NFPA 13-2025 throughout.",
                "status": "confirmed",
            }
        ],
        ops_valid=True,
    )
    session.qc.restore(audit_grade_qc_result(session, [finding]))
    reviewed_index = session.doc.index

    real_app_event = _trace_capture.app_event
    intruded: dict[str, int] = {}

    def mutate_then_record(event_type: str, **fields):
        if event_type == "qc_apply" and "index" not in intruded:
            # A second undoable version, exactly as a concurrent edit or a
            # committing turn would produce.
            session.doc.begin_turn()
            session.doc.apply_edits(
                [
                    {
                        "action": "add_article",
                        "target_id": "pt1",
                        "text": "LATER ARTICLE",
                    }
                ]
            )
            session.doc.commit_turn()
            intruded["index"] = session.doc.index
        return real_app_event(event_type, **fields)

    monkeypatch.setattr(_trace_capture, "app_event", mutate_then_record)

    applied = client.post(
        "/api/qc/apply", json={"finding_ids": [finding.finding_id]}
    )
    assert applied.status_code == 200, applied.text
    body = applied.json()
    assert body["outcomes"][finding.finding_id] == "applied"

    # The intruder really did land, and really is a different version.
    applied_index = reviewed_index + 1
    assert intruded["index"] == applied_index + 1
    assert session.doc.index == applied_index + 1

    # …and the reply still describes the version this apply produced.
    assert body["doc"]["version"]["index"] == applied_index
    assert body["doc"]["version"]["count"] == applied_index + 1
    titles = [
        article["title"] for part in body["doc"]["parts"] for article in part["articles"]
    ]
    assert "LATER ARTICLE" not in titles


def test_undoing_qc_fix_cannot_resurrect_a_current_applied_report() -> None:
    """Undo restores the reviewed defect but never makes its applied claim current.

    Finding disposition events live in the audit record rather than in the
    document's version list.  The post-application version/fingerprint anchor
    must therefore continue to fail freshness after Undo returns to the exact
    reviewed snapshot.
    """
    client = TestClient(create_app())
    session = sessions.get_session()
    session.doc = _reviewed_store()
    finding = QCFinding(
        finding_id="qc-history-edition",
        lens_id="code_compliance",
        severity="high",
        element_id="pt1.a1.p1",
        title="Update the referenced edition",
        issue="The reviewed provision cites an obsolete project edition.",
        rationale="Exercise QC disposition freshness across document history.",
        proposed_ops=[
            {
                "action": "replace",
                "target_id": "pt1.a1.p1",
                "text": "Comply with NFPA 13-2025 throughout.",
                "status": "confirmed",
            }
        ],
        ops_valid=True,
    )
    session.qc.restore(audit_grade_qc_result(session, [finding]))

    assert _readiness_check(client, "qc_current")["ok"] is True
    reviewed_index = session.doc.index
    reviewed_text = session.doc.doc.parts[0].articles[0].paragraphs[0].text

    applied = client.post(
        "/api/qc/apply", json={"finding_ids": [finding.finding_id]}
    )
    assert applied.status_code == 200, applied.text
    assert session.doc.index == reviewed_index + 1
    assert session.qc.result is not None
    applied_finding = session.qc.result.finding(finding.finding_id)
    assert applied_finding is not None
    assert applied_finding.status == "applied"
    assert applied_finding.disposition_events[-1].document_version == (
        reviewed_index + 1
    )

    undone = client.post("/api/doc/undo")
    assert undone.status_code == 200, undone.text
    assert session.doc.index == reviewed_index
    assert session.doc.doc.parts[0].articles[0].paragraphs[0].text == reviewed_text

    snapshot = client.get("/api/qc/status").json()
    assert snapshot["stale"] is True
    assert snapshot["result"]["findings"][0]["status"] == "applied"
    assert _readiness_check(client, "qc_current")["ok"] is False

    retry = client.post(
        "/api/qc/apply", json={"finding_ids": [finding.finding_id]}
    )
    assert retry.status_code == 409
    assert "stale" in retry.json()["error"].lower()

