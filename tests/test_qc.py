"""Final QC (Batch 4): lens fan-out, adversarial verification, ops
validation, and the accept/dismiss/readiness/memo API — hermetic against
the sequenced fake client."""
from __future__ import annotations

import io
import json
import time

import pytest
from docx import Document
from fastapi.testclient import TestClient

from backend import sessions
from backend.app import create_app
from backend.qc.engine import QCFanoutError, QCResult, run_final_qc
from backend.qc.schema import QC_LENSES
from backend.spec_doc.model import DocumentStore
from backend.spec_modules import DEFAULT_MODULE
from tests.fakes import (
    FakeClient,
    SequencedFakeClient,
    qc_findings_response,
    qc_verdict_response,
    text_turn,
    tool_turn,
)

_LENS_KEYS = {lens.lens_id: f"[[QC-LENS:{lens.lens_id}]]" for lens in QC_LENSES}


def _client() -> TestClient:
    return TestClient(create_app())


def _section() -> "DocumentStore":
    """A minimal drafted document (one assumed provision)."""
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
    issue: str,
    *,
    element_id: str | None = "pt1.a1.p1",
    severity: str = "high",
    ops: list[dict] | None = None,
    source_urls: list[str] | None = None,
) -> dict:
    return {
        "title": title,
        "severity": severity,
        "element_id": element_id,
        "issue": issue,
        "rationale": f"rationale for {title}",
        "source_urls": source_urls or [],
        "proposed_ops": ops,
    }


def _qc_scripts(**per_lens) -> dict[str, list]:
    """Lens scripts; unspecified lenses return zero findings."""
    scripts: dict[str, list] = {}
    for lens_id, key in _LENS_KEYS.items():
        scripts[key] = per_lens.get(
            lens_id, [qc_findings_response(lens_id, findings=[])]
        )
    return scripts


def _run(client, store, *, profile=None, remembered=None, sink=None, version_index=0):
    return run_final_qc(
        store.doc,
        profile,
        DEFAULT_MODULE,
        client,
        model="claude-fable-5",
        max_tokens=4096,
        version_index=version_index,
        started_at="2026-07-21 10:00",
        finished_at="2026-07-21 10:05",
        remembered_dismissed=remembered,
        event_sink=sink or (lambda _e: None),
    )


# ---------------------------------------------------------------------------
# Phase 1 — lens fan-out
# ---------------------------------------------------------------------------


def test_fanout_runs_all_lenses_grounds_and_survives_defaults():
    store = _section()
    scripts = _qc_scripts(
        code_compliance=[
            qc_findings_response(
                "code_compliance",
                findings=[
                    _finding(
                        "Stale NFPA 13 edition",
                        "The draft cites NFPA 13-2019 but the effective edition is 2025.",
                        severity="high",
                        source_urls=["https://nfpa.org/13"],
                    )
                ],
                searched_urls=["https://nfpa.org/13"],
            )
        ],
    )
    # The finding is high → 3-panel; script 3 uphold verdicts.
    scripts["Stale NFPA 13 edition"] = [
        qc_verdict_response(True) for _ in range(3)
    ]
    result = _run(SequencedFakeClient(scripts), store)

    assert [s.lens_id for s in result.lens_statuses] == [
        l.lens_id for l in QC_LENSES
    ]
    assert all(s.status == "completed" for s in result.lens_statuses)
    assert len(result.findings) == 1
    f = result.findings[0]
    assert f.lens_id == "code_compliance"
    assert f.grounded is True
    assert f.accepted_sources == ["https://nfpa.org/13"]
    assert f.finding_id.startswith("qc-")
    assert result.research_profile_present is False


def test_one_failing_lens_does_not_kill_the_run():
    store = _section()
    scripts = _qc_scripts(
        completeness=[RuntimeError("lens boom")],  # non-retryable
    )
    result = _run(SequencedFakeClient(scripts), store)
    statuses = {s.lens_id: s for s in result.lens_statuses}
    assert statuses["completeness"].status == "failed"
    assert "boom" in statuses["completeness"].error
    assert sum(1 for s in result.lens_statuses if s.status == "completed") == 4


def test_all_lenses_failing_raises_fanout_error():
    store = _section()
    scripts = {key: [RuntimeError("dead")] for key in _LENS_KEYS.values()}
    with pytest.raises(QCFanoutError, match="All 5"):
        _run(SequencedFakeClient(scripts), store)


def test_ungrounded_citation_is_kept_but_marked():
    store = _section()
    scripts = _qc_scripts(
        code_compliance=[
            qc_findings_response(
                "code_compliance",
                findings=[
                    _finding(
                        "Cites unretrieved page",
                        "Rationale cites a URL never fetched.",
                        severity="medium",
                        source_urls=["https://never-retrieved.example"],
                    )
                ],
                searched_urls=["https://nfpa.org/13"],  # different URL retrieved
            )
        ],
    )
    scripts["Cites unretrieved page"] = [qc_verdict_response(True) for _ in range(2)]
    result = _run(SequencedFakeClient(scripts), store)
    f = result.findings[0]
    assert f.grounded is False
    assert f.accepted_sources == []


# ---------------------------------------------------------------------------
# Phase 2 — adversarial verification
# ---------------------------------------------------------------------------


def test_two_panel_tie_kills_the_finding():
    store = _section()
    scripts = _qc_scripts(
        enforceability_language=[
            qc_findings_response(
                "enforceability_language",
                findings=[
                    _finding("Vague language", "Uses 'as required'.", severity="medium")
                ],
            )
        ],
    )
    # medium → 2-panel. 1 uphold, 1 refute = tie → refuted.
    scripts["Vague language"] = [qc_verdict_response(True), qc_verdict_response(False)]
    result = _run(SequencedFakeClient(scripts), store)
    assert result.findings == []
    assert len(result.refuted) == 1
    assert result.refuted[0].title == "Vague language"


def test_three_panel_majority_survives_for_criticals_and_medians_severity():
    store = _section()
    scripts = _qc_scripts(
        code_compliance=[
            qc_findings_response(
                "code_compliance",
                findings=[
                    _finding("Wrong edition", "Edition mismatch.", severity="high")
                ],
            )
        ],
    )
    # high → 3-panel. 2 uphold (revising to critical), 1 refute → survives.
    scripts["Wrong edition"] = [
        qc_verdict_response(True, severity="critical"),
        qc_verdict_response(True, severity="critical"),
        qc_verdict_response(False),
    ]
    result = _run(SequencedFakeClient(scripts), store)
    assert len(result.findings) == 1
    # median(["high","critical","critical"]) → critical.
    assert result.findings[0].severity == "critical"
    assert len(result.findings[0].verdicts) == 3


def test_dead_verifier_counts_as_refuted():
    store = _section()
    scripts = _qc_scripts(
        provenance_hygiene=[
            qc_findings_response(
                "provenance_hygiene",
                findings=[
                    _finding("Risky assumed block", "Assumed default.", severity="low")
                ],
            )
        ],
    )
    # low → 2-panel. One uphold, one dead verifier → 1 of 2 → refuted.
    scripts["Risky assumed block"] = [
        qc_verdict_response(True),
        RuntimeError("verifier died"),
    ]
    result = _run(SequencedFakeClient(scripts), store)
    assert result.findings == []
    assert len(result.refuted) == 1


# ---------------------------------------------------------------------------
# Phase 3 — ops validation
# ---------------------------------------------------------------------------


def test_valid_ops_dry_run_marks_valid_without_mutating_source():
    store = _section()
    before = store.doc.to_dict()
    scripts = _qc_scripts(
        code_compliance=[
            qc_findings_response(
                "code_compliance",
                findings=[
                    _finding(
                        "Fixable edition",
                        "Update the reference.",
                        severity="medium",
                        ops=[
                            {
                                "action": "replace",
                                "target_id": "pt1.a1.p1",
                                "text": "Comply with NFPA 13-2025 throughout.",
                                "status": "confirmed",
                            }
                        ],
                    )
                ],
            )
        ],
    )
    scripts["Fixable edition"] = [qc_verdict_response(True) for _ in range(2)]
    result = _run(SequencedFakeClient(scripts), store)
    assert result.findings[0].ops_valid is True
    # The snapshot was NOT mutated by the dry-run.
    assert store.doc.to_dict() == before


def test_invalid_ops_become_advisory_with_reason():
    store = _section()
    scripts = _qc_scripts(
        coordination_consistency=[
            qc_findings_response(
                "coordination_consistency",
                findings=[
                    _finding(
                        "Bad target",
                        "Fix references a missing id.",
                        severity="medium",
                        ops=[
                            {
                                "action": "replace",
                                "target_id": "pt9.a9.p9",  # does not exist
                                "text": "nope",
                            }
                        ],
                    )
                ],
            )
        ],
    )
    scripts["Bad target"] = [qc_verdict_response(True) for _ in range(2)]
    result = _run(SequencedFakeClient(scripts), store)
    assert result.findings[0].ops_valid is False
    assert "pt9.a9.p9" in result.findings[0].ops_invalid_reason


# ---------------------------------------------------------------------------
# Dismiss memory across re-runs (content-addressed ids)
# ---------------------------------------------------------------------------


def test_dismiss_memory_survives_a_rerun():
    store = _section()
    scripts = _qc_scripts(
        code_compliance=[
            qc_findings_response(
                "code_compliance",
                findings=[_finding("Persistent finding", "Same each run.", severity="medium")],
            )
        ],
    )
    scripts["Persistent finding"] = [qc_verdict_response(True) for _ in range(2)]
    first = _run(SequencedFakeClient(scripts), store)
    fid = first.findings[0].finding_id

    scripts2 = _qc_scripts(
        code_compliance=[
            qc_findings_response(
                "code_compliance",
                findings=[_finding("Persistent finding", "Same each run.", severity="medium")],
            )
        ],
    )
    scripts2["Persistent finding"] = [qc_verdict_response(True) for _ in range(2)]
    second = _run(
        SequencedFakeClient(scripts2), store, remembered={fid}
    )
    assert second.findings[0].finding_id == fid
    assert second.findings[0].status == "dismissed"
    assert fid in second.dismissed_ids


# ---------------------------------------------------------------------------
# API lifecycle: start gate, apply, dismiss, project round-trip, staleness
# ---------------------------------------------------------------------------


def _seed_doc(client: TestClient, monkeypatch) -> None:
    draft = FakeClient(
        [
            tool_turn(
                ["Drafting."],
                {
                    "edits": [
                        {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"},
                        {
                            "action": "add_paragraph",
                            "target_id": "pt1.a1",
                            "text": "Comply with NFPA 13-2019 throughout.",
                            "status": "assumed",
                        },
                    ]
                },
            ),
            text_turn(["Done."]),
        ]
    )
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: draft)
    client.post("/api/chat", json={"message": "draft"})


def _wait_qc(client: TestClient, timeout_s: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        snap = client.get("/api/qc/status").json()
        if snap["status"] in ("complete", "failed"):
            return snap
        time.sleep(0.05)
    raise AssertionError("QC did not settle")


def _apply_scripts_with_fix() -> dict[str, list]:
    scripts = _qc_scripts(
        code_compliance=[
            qc_findings_response(
                "code_compliance",
                findings=[
                    _finding(
                        "Edition fix",
                        "Bump edition.",
                        severity="high",
                        ops=[
                            {
                                "action": "replace",
                                "target_id": "pt1.a1.p1",
                                "text": "Comply with NFPA 13-2025 throughout.",
                                "status": "confirmed",
                            }
                        ],
                    )
                ],
            )
        ],
    )
    scripts["Edition fix"] = [qc_verdict_response(True) for _ in range(3)]
    return scripts


def test_qc_start_gates_and_apply_is_one_undo_step(monkeypatch):
    client = _client()
    # Empty doc → 400.
    assert client.post("/api/qc/start").status_code == 400

    _seed_doc(client, monkeypatch)
    monkeypatch.setattr(
        "backend.app.get_client", lambda: SequencedFakeClient(_apply_scripts_with_fix())
    )
    assert client.post("/api/qc/start").json()["ok"] is True
    snap = _wait_qc(client)
    assert snap["status"] == "complete"
    finding = snap["result"]["findings"][0]
    assert finding["ops_valid"] is True
    fid = finding["finding_id"]

    versions_before = client.get("/api/doc").json()["doc"]["version"]["count"]
    resp = client.post("/api/qc/apply", json={"finding_ids": [fid]}).json()
    assert resp["ok"] is True
    assert resp["outcomes"][fid] == "applied"
    doc = client.get("/api/doc").json()["doc"]
    assert doc["version"]["count"] == versions_before + 1  # exactly one new version
    assert "2025" in doc["parts"][0]["articles"][0]["paragraphs"][0]["text"]
    # The finding is now marked applied.
    assert client.get("/api/qc/status").json()["result"]["findings"][0]["status"] == "applied"

    # One undo step reverts the whole accept-set.
    client.post("/api/doc/undo")
    reverted = client.get("/api/doc").json()["doc"]
    assert "2019" in reverted["parts"][0]["articles"][0]["paragraphs"][0]["text"]


def test_qc_apply_reports_stale_when_doc_moved(monkeypatch):
    client = _client()
    _seed_doc(client, monkeypatch)
    monkeypatch.setattr(
        "backend.app.get_client", lambda: SequencedFakeClient(_apply_scripts_with_fix())
    )
    client.post("/api/qc/start")
    snap = _wait_qc(client)
    fid = snap["result"]["findings"][0]["finding_id"]

    # Delete the target the fix depends on → the fix is now stale.
    client.post(
        "/api/doc/edit",
        json={"ops": [{"action": "delete", "target_id": "pt1.a1.p1"}]},
    )
    resp = client.post("/api/qc/apply", json={"finding_ids": [fid]}).json()
    assert resp["outcomes"][fid] == "stale"


def test_qc_dismiss_and_project_round_trip_and_staleness(monkeypatch):
    client = _client()
    _seed_doc(client, monkeypatch)
    monkeypatch.setattr(
        "backend.app.get_client", lambda: SequencedFakeClient(_apply_scripts_with_fix())
    )
    client.post("/api/qc/start")
    snap = _wait_qc(client)
    fid = snap["result"]["findings"][0]["finding_id"]

    # Dismiss with a reason.
    dm = client.post(
        "/api/qc/dismiss", json={"finding_id": fid, "reason": "handled offline"}
    ).json()
    assert dm["ok"] is True
    assert dm["qc"]["result"]["dismissed_ids"] == [fid]

    # Project round-trips the QC result.
    project = json.loads(client.get("/api/project/save").content)
    assert project["qc_result"]["findings"]
    client.post("/api/session/reset")
    assert client.get("/api/qc/status").json()["status"] == "idle"
    client.post("/api/project/load", json=project)
    restored = client.get("/api/qc/status").json()
    assert restored["status"] == "complete"
    assert restored["result"]["findings"][0]["status"] == "dismissed"
    assert restored["events"][0].get("restored") is True

    # Staleness: change the doc → version_index no longer matches.
    client.post(
        "/api/doc/edit",
        json={
            "ops": [
                {"action": "add_paragraph", "target_id": "pt1.a1", "text": "New line."}
            ]
        },
    )
    ready = client.get("/api/readiness").json()
    qc_check = next(c for c in ready["checks"] if c["id"] == "qc_current")
    assert qc_check["ok"] is False


def test_qc_double_start_conflicts_and_reset_abandons(monkeypatch):
    client = _client()
    _seed_doc(client, monkeypatch)

    import threading

    release = threading.Event()

    class _Blocking:
        def __init__(self):
            self.messages = self

        def stream(self, **_request):
            release.wait(timeout=5)
            raise RuntimeError("aborted")

    monkeypatch.setattr("backend.app.get_client", lambda: _Blocking())
    assert client.post("/api/qc/start").json()["ok"] is True
    assert client.get("/api/qc/status").json()["status"] == "running"
    # Double-start conflicts.
    assert client.post("/api/qc/start").status_code == 409

    old_runner = sessions.get_session().qc
    client.post("/api/session/reset")
    assert client.get("/api/qc/status").json()["status"] == "idle"
    release.set()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not old_runner.is_terminal:
        time.sleep(0.05)
    assert old_runner.status == "failed"
    assert client.get("/api/qc/status").json()["status"] == "idle"


def test_qc_apply_and_start_reject_while_turn_active(monkeypatch):
    client = _client()
    _seed_doc(client, monkeypatch)
    sessions.get_session().turn_active = True
    try:
        assert client.post("/api/qc/start").status_code == 409
        assert client.post("/api/qc/apply", json={"finding_ids": []}).status_code == 409
    finally:
        sessions.get_session().turn_active = False


# ---------------------------------------------------------------------------
# Readiness + memo export + usage
# ---------------------------------------------------------------------------


def test_readiness_reflects_state(monkeypatch):
    client = _client()
    _seed_doc(client, monkeypatch)
    ready = client.get("/api/readiness").json()
    checks = {c["id"]: c for c in ready["checks"]}
    # A fresh assumed draft with no research is not ready.
    assert checks["no_assumed_left"]["ok"] is False
    assert checks["research_complete"]["ok"] is False
    assert checks["qc_current"]["ok"] is False
    assert ready["ready"] is False
    # profile_complete is advisory (does not gate on its own).
    assert checks["profile_complete"]["advisory"] is True


def test_qc_memo_export_smoke(monkeypatch):
    client = _client()
    _seed_doc(client, monkeypatch)
    # No QC yet → 409.
    assert client.get("/api/qc/export").status_code == 409

    monkeypatch.setattr(
        "backend.app.get_client", lambda: SequencedFakeClient(_apply_scripts_with_fix())
    )
    client.post("/api/qc/start")
    _wait_qc(client)
    resp = client.get("/api/qc/export")
    assert resp.status_code == 200
    texts = [p.text for p in Document(io.BytesIO(resp.content)).paragraphs]
    assert any("FINAL QC REVIEW MEMORANDUM" in t for t in texts)
    assert any("Edition fix" in t for t in texts)


def test_qc_usage_rolls_up_under_fable_pricing(monkeypatch):
    client = _client()
    _seed_doc(client, monkeypatch)
    scripts = _qc_scripts(
        code_compliance=[
            qc_findings_response(
                "code_compliance",
                findings=[_finding("A finding", "issue", severity="medium")],
                tokens={"input": 4000, "output": 800},
            )
        ],
    )
    scripts["A finding"] = [
        qc_verdict_response(True, tokens={"input": 500}),
        qc_verdict_response(True, tokens={"input": 500}),
    ]
    monkeypatch.setattr("backend.app.get_client", lambda: SequencedFakeClient(scripts))
    client.post("/api/qc/start")
    _wait_qc(client)

    usage = client.get("/api/usage").json()
    qc = usage["categories"]["qc"]
    assert qc["input_tokens"] == 5000  # 4000 lens + 2 × 500 verifiers
    assert qc["output_tokens"] == 800
    # Priced on Fable 5 ($10/M in, $50/M out): 5000*10e-6 + 800*50e-6.
    assert usage["estimated_cost_usd"]["by_category"]["qc"] == round(
        5000 * 10e-6 + 800 * 50e-6, 6
    )


def test_qc_result_from_dict_degrades_on_malformed_data():
    # A non-numeric version_index / finding_count must degrade to None, never
    # raise — project.load restores QC after the doc/history are swapped in.
    assert (
        QCResult.from_dict(
            {
                "findings": [
                    {"finding_id": "qc-x", "title": "t", "issue": "i", "severity": "high"}
                ],
                "version_index": "not-a-number",
            }
        )
        is None
    )
    assert (
        QCResult.from_dict(
            {"lens_statuses": [{"lens_id": "x", "finding_count": "NaN"}]}
        )
        is None
    )
    assert QCResult.from_dict("garbage") is None


def test_project_load_survives_malformed_qc_result(monkeypatch):
    client = _client()
    _seed_doc(client, monkeypatch)
    project = json.loads(client.get("/api/project/save").content)
    # A malformed QC result (non-numeric version_index) would raise in a naive
    # from_dict — the load must still succeed and the doc must still restore.
    project["qc_result"] = {
        "findings": [
            {"finding_id": "qc-x", "title": "t", "issue": "i", "severity": "high"}
        ],
        "version_index": "not-a-number",
    }
    client.post("/api/session/reset")
    resp = client.post("/api/project/load", json=project)
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    # QC degraded to "not run"; the document still loaded.
    assert client.get("/api/qc/status").json()["status"] == "idle"
    assert client.get("/api/doc").json()["doc"]["parts"][0]["articles"]


def test_qc_result_from_dict_round_trips():
    store = _section()
    scripts = _qc_scripts(
        code_compliance=[
            qc_findings_response(
                "code_compliance",
                findings=[_finding("Round trip", "issue", severity="high")],
            )
        ],
    )
    scripts["Round trip"] = [qc_verdict_response(True) for _ in range(3)]
    result = _run(SequencedFakeClient(scripts), store)
    again = QCResult.from_dict(result.to_dict())
    assert again is not None
    assert again.findings[0].title == "Round trip"
    assert again.model == "claude-fable-5"


def test_qc_proposed_ops_allow_set_standard_suppressed():
    """A QC fix may propose set_standard_suppressed (the standards-manager op):
    it survives normalize_findings/_clean_op with its fields intact, so a
    standards-scope fix QC can now describe is also one Apply QC can enact —
    matching the /api/doc/edit vocabulary the lens reasons from."""
    from backend.qc.schema import QC_OP_ACTIONS, normalize_findings

    assert "set_standard_suppressed" in QC_OP_ACTIONS
    op = {
        "action": "set_standard_suppressed",
        "target_id": "sec",
        "standard": "NFPA 2001",
        "suppressed": True,
        "basis": "no clean-agent system in scope",
    }
    payload = {
        "summary": "s",
        "findings": [
            _finding(
                "Exclude NFPA 2001",
                "No clean-agent system; it should not reach REFERENCES.",
                element_id="sec",
                ops=[op],
            )
        ],
    }
    cleaned = normalize_findings(payload)["findings"][0]["proposed_ops"]
    assert cleaned == [op]
    # An added standard's title also rides through (the other mirrored field).
    add = {
        "action": "set_standard_edition",
        "target_id": "sec",
        "standard": "NFPA 30",
        "edition": "2024",
        "basis": "on-site flammable storage",
        "title": "Flammable and Combustible Liquids Code",
    }
    add_payload = {"summary": "s", "findings": [_finding("Add", "x", ops=[add])]}
    assert normalize_findings(add_payload)["findings"][0]["proposed_ops"] == [add]
