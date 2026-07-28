"""Public contracts for guided Final-QC remediation preview and batch apply."""
from __future__ import annotations

import hashlib
import json
import threading
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from backend import sessions
from backend.app import create_app
from backend.qc.engine import QCFinding
from backend.spec_doc.model import DocumentStore
from tests.fakes import audit_grade_qc_result


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
                "text": "Provide a complete system.",
                "status": "confirmed",
            },
            {
                "action": "add_paragraph",
                "target_id": "pt1.a1",
                "text": "Coordinate all interfaces.",
                "status": "confirmed",
            },
        ]
    )
    store.commit_turn()
    return store


def _finding(finding_id: str, operations: list[dict]) -> QCFinding:
    return QCFinding(
        finding_id=finding_id,
        lens_id="coordination_consistency",
        severity="high",
        element_id=str(operations[0].get("target_id") if operations else ""),
        title=f"Finding {finding_id}",
        issue="A focused guided-remediation issue.",
        rationale="Exercise the public preview and apply contract.",
        proposed_ops=operations,
        ops_valid=bool(operations),
    )


def _install(findings: list[QCFinding]):
    session = sessions.get_session()
    session.doc = _store()
    result = audit_grade_qc_result(session, findings)
    session.qc.restore(result)
    return session, result


def _fingerprint(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def test_preview_is_pure_and_plans_dedupe_conflicts_stale_and_safe_apply() -> None:
    standard_op = {
        "action": "set_standard_edition",
        "target_id": "sec",
        "standard": "NFPA 13",
        "edition": "2025",
        "basis": "Project code basis.",
    }
    duplicate_owner = _finding(
        "duplicate-owner",
        [
            {
                # Key order and a schema-style null are canonically identical.
                "basis": "Project code basis.",
                "edition": "2025",
                "standard": "NFPA 13",
                "target_id": "sec",
                "action": "set_standard_edition",
                "position": None,
            },
            dict(standard_op),  # duplicate inside one finding too
        ],
    )
    findings = [
        _finding("standard-owner", [standard_op]),
        duplicate_owner,
        _finding(
            "compatible",
            [
                {
                    "action": "replace",
                    "target_id": "pt1.a1.p2",
                    "text": "Coordinate every specified system interface.",
                    "status": "confirmed",
                }
            ],
        ),
        _finding(
            "conflict-a",
            [
                {
                    "action": "replace",
                    "target_id": "pt1.a1.p1",
                    "text": "Provide revision A.",
                    "status": "confirmed",
                }
            ],
        ),
        _finding(
            "conflict-b",
            [
                {
                    "action": "replace",
                    "target_id": "pt1.a1.p1",
                    "text": "Provide revision B.",
                    "status": "confirmed",
                }
            ],
        ),
        _finding(
            "stale-operation",
            [
                {
                    "action": "replace",
                    "target_id": "pt9.a9.p9",
                    "text": "The target does not exist.",
                }
            ],
        ),
        _finding("no-operations", []),
        _finding(
            "semantic-rejection",
            [
                {
                    "action": "replace",
                    "target_id": "pt1.a1.p2",
                    "text": "A verifier-rejected proposal.",
                }
            ],
        ),
    ]
    session, result = _install(findings)
    rejected = result.finding("semantic-rejection")
    assert rejected is not None
    rejected.ops_semantic_status = "rejected"
    rejected.ops_semantic_reason = "The proposed text does not resolve the issue."

    selected = [
        "standard-owner",
        "duplicate-owner",
        "compatible",
        "conflict-a",
        "conflict-b",
        "stale-operation",
        "no-operations",
        "semantic-rejection",
        "unknown-id",
        "compatible",  # request duplicates are removed while order is retained
    ]
    before_doc = session.doc.to_dict()
    before_result = result.to_dict()
    before_events = list(session.qc.events)
    client = TestClient(create_app())

    response = client.post(
        "/api/qc/apply/preview", json={"finding_ids": selected}
    )
    assert response.status_code == 200, response.text
    preview = response.json()

    # Preview is observably read-only for both document history and QC audit.
    assert session.doc.to_dict() == before_doc
    assert result.to_dict() == before_result
    assert session.qc.events == before_events
    assert [decision["finding_id"] for decision in preview["decisions"]] == (
        selected[:-1]
    )

    basis = dict(preview["basis"])
    binding = basis.pop("binding_fingerprint")
    assert binding == _fingerprint(basis)
    assert basis["result_fingerprint"] == _fingerprint(before_result)
    assert basis["selected_finding_ids"] == selected[:-1]
    assert basis["run_id"] == result.run_id
    assert basis["document_version"] == session.doc.index
    assert all(
        len(basis[key]) == 64
        for key in (
            "input_fingerprint",
            "document_fingerprint",
            "result_fingerprint",
        )
    )

    decisions = {
        decision["finding_id"]: decision for decision in preview["decisions"]
    }
    assert decisions["standard-owner"]["outcome"] == "applyable"
    assert decisions["standard-owner"]["apply_operation_count"] == 1
    assert decisions["duplicate-owner"]["outcome"] == "applyable"
    assert decisions["duplicate-owner"]["apply_operation_count"] == 0
    assert decisions["duplicate-owner"]["duplicate_operation_count"] == 2
    assert decisions["compatible"]["outcome"] == "applyable"
    assert decisions["stale-operation"]["outcome"] == "stale"
    assert decisions["no-operations"]["outcome"] == "no_ops"
    assert decisions["semantic-rejection"]["outcome"] == "no_ops"
    assert decisions["semantic-rejection"]["reason_code"] == (
        "operations_not_approved"
    )
    assert decisions["unknown-id"]["outcome"] == "unknown"
    assert decisions["conflict-a"]["outcome"] == "conflict"
    assert decisions["conflict-a"]["conflicts_with"] == ["conflict-b"]
    assert decisions["conflict-b"]["conflicts_with"] == ["conflict-a"]

    assert preview["operation_counts"] == {
        "proposed": 7,
        "unique": 5,
        "duplicate": 2,
        "applyable": 2,
    }
    assert preview["applyable_finding_ids"] == [
        "standard-owner",
        "duplicate-owner",
        "compatible",
    ]
    assert len(preview["applyable_operations"]) == 2
    assert len(preview["conflicts"]) == 1
    assert preview["conflicts"][0]["finding_ids"] == [
        "conflict-a",
        "conflict-b",
    ]
    assert preview["deduplications"] == [
        {
            "operation": standard_op,
            "finding_ids": ["standard-owner", "duplicate-owner"],
            "occurrence_count": 3,
        }
    ]

    # Applying the server-selected safe subset makes one version; the exact
    # duplicate executes once while every owning finding is dispositioned.
    versions_before = len(session.doc.versions)
    applied = client.post(
        "/api/qc/apply",
        json={
            "finding_ids": preview["applyable_finding_ids"],
            "preview_basis": preview["basis"],
        },
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["outcomes"] == {
        "standard-owner": "applied",
        "duplicate-owner": "applied",
        "compatible": "applied",
    }
    assert len(session.doc.versions) == versions_before + 1
    assert session.doc.doc.edition_overrides["NFPA 13"]["edition"] == "2025"
    assert (
        session.doc.doc.parts[0].articles[0].paragraphs[1].text
        == "Coordinate every specified system interface."
    )
    assert all(
        result.finding(finding_id).status == "applied"  # type: ignore[union-attr]
        for finding_id in preview["applyable_finding_ids"]
    )

    undone = client.post("/api/doc/undo")
    assert undone.status_code == 200, undone.text
    assert session.doc.doc.edition_overrides == {}
    assert (
        session.doc.doc.parts[0].articles[0].paragraphs[1].text
        == "Coordinate all interfaces."
    )


def test_preview_and_bound_apply_allow_distinct_same_parent_additions() -> None:
    findings = [
        _finding(
            "paragraph-a",
            [
                {
                    "action": "add_paragraph",
                    "target_id": "pt1.a1",
                    "text": "Submit the first independent record.",
                    "status": "confirmed",
                }
            ],
        ),
        _finding(
            "paragraph-b",
            [
                {
                    "action": "add_paragraph",
                    "target_id": "pt1.a1",
                    "text": "Submit the second independent record.",
                    "status": "confirmed",
                    "position": None,
                }
            ],
        ),
        _finding(
            "article-a",
            [
                {
                    "action": "add_article",
                    "target_id": "pt1",
                    "text": "FIRST INDEPENDENT ARTICLE",
                }
            ],
        ),
        _finding(
            "article-b",
            [
                {
                    "action": "add_article",
                    "target_id": "pt1",
                    "text": "SECOND INDEPENDENT ARTICLE",
                }
            ],
        ),
    ]
    session, result = _install(findings)
    client = TestClient(create_app())
    selected_ids = [finding.finding_id for finding in findings]
    versions_before = len(session.doc.versions)

    response = client.post(
        "/api/qc/apply/preview", json={"finding_ids": selected_ids}
    )
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["conflicts"] == []
    assert preview["applyable_finding_ids"] == selected_ids
    assert preview["operation_counts"] == {
        "proposed": 4,
        "unique": 4,
        "duplicate": 0,
        "applyable": 4,
    }

    applied = client.post(
        "/api/qc/apply",
        json={
            "finding_ids": preview["applyable_finding_ids"],
            "preview_basis": preview["basis"],
        },
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["outcomes"] == {
        finding_id: "applied" for finding_id in selected_ids
    }
    assert len(session.doc.versions) == versions_before + 1
    article = session.doc.doc.parts[0].articles[0]
    assert [paragraph.text for paragraph in article.paragraphs[-2:]] == [
        "Submit the first independent record.",
        "Submit the second independent record.",
    ]
    assert [article.title for article in session.doc.doc.parts[0].articles[-2:]] == [
        "FIRST INDEPENDENT ARTICLE",
        "SECOND INDEPENDENT ARTICLE",
    ]
    assert all(
        result.finding(finding_id).status == "applied"  # type: ignore[union-attr]
        for finding_id in selected_ids
    )


def test_preview_refuses_partial_and_stale_reports() -> None:
    client = TestClient(create_app())
    session, result = _install(
        [
            _finding(
                "safe",
                [
                    {
                        "action": "set_status",
                        "target_id": "pt1.a1.p1",
                        "status": "confirmed",
                    }
                ],
            )
        ]
    )

    result.execution_status = "partial"
    partial = client.post(
        "/api/qc/apply/preview", json={"finding_ids": ["safe"]}
    )
    assert partial.status_code == 409
    assert "audit-complete" in partial.json()["error"]
    assert session.doc.index == 1

    # Restore a complete report, then move any material review input.
    session.qc.restore(
        audit_grade_qc_result(
            session,
            [
                _finding(
                    "safe",
                    [
                        {
                            "action": "set_status",
                            "target_id": "pt1.a1.p1",
                            "status": "confirmed",
                        }
                    ],
                )
            ],
        )
    )
    changed = client.post(
        "/api/doc/edit",
        json={
            "ops": [
                {
                    "action": "replace",
                    "target_id": "pt1.a1.p1",
                    "text": "The document changed after Final QC.",
                }
            ]
        },
    )
    assert changed.status_code == 200, changed.text
    stale = client.post(
        "/api/qc/apply/preview", json={"finding_ids": ["safe"]}
    )
    assert stale.status_code == 409
    assert "stale" in stale.json()["error"].lower()


def test_preview_rejects_a_state_change_during_its_out_of_lock_dry_run(
    monkeypatch,
) -> None:
    session, _result = _install(
        [
            _finding(
                "safe",
                [
                    {
                        "action": "replace",
                        "target_id": "pt1.a1.p1",
                        "text": "Previewed replacement.",
                    }
                ],
            )
        ]
    )
    client = TestClient(create_app())
    entered = threading.Event()
    release = threading.Event()
    from backend import app as app_module

    original = app_module._qc_apply_preview_plan

    def blocking_plan(result, working, selected_ids, **kwargs):
        entered.set()
        assert release.wait(timeout=5)
        return original(result, working, selected_ids, **kwargs)

    monkeypatch.setattr(app_module, "_qc_apply_preview_plan", blocking_plan)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(
            client.post,
            "/api/qc/apply/preview",
            json={"finding_ids": ["safe"]},
        )
        assert entered.wait(timeout=5)
        changed = client.post(
            "/api/doc/edit",
            json={
                "ops": [
                    {
                        "action": "replace",
                        "target_id": "pt1.a1.p2",
                        "text": "Concurrent document change.",
                    }
                ]
            },
        )
        assert changed.status_code == 200, changed.text
        changed_snapshot = session.doc.snapshot()
        release.set()
        response = future.result(timeout=5)

    assert response.status_code == 409
    assert "changed while previewing" in response.json()["error"]
    assert session.doc.snapshot() == changed_snapshot


def test_bound_apply_rejects_tamper_state_result_and_selection_drift() -> None:
    findings = [
        _finding(
            "first",
            [
                {
                    "action": "replace",
                    "target_id": "pt1.a1.p1",
                    "text": "First previewed replacement.",
                }
            ],
        ),
        _finding(
            "second",
            [
                {
                    "action": "replace",
                    "target_id": "pt1.a1.p2",
                    "text": "Second previewed replacement.",
                }
            ],
        ),
    ]
    session, result = _install(findings)
    client = TestClient(create_app())
    preview_response = client.post(
        "/api/qc/apply/preview",
        json={"finding_ids": ["first", "second"]},
    )
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    safe_ids = preview["applyable_finding_ids"]
    original_doc = session.doc.to_dict()

    tampered = deepcopy(preview["basis"])
    tampered["document_version"] += 1
    response = client.post(
        "/api/qc/apply",
        json={"finding_ids": safe_ids, "preview_basis": tampered},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "qc_preview_binding_invalid"

    forged_state = deepcopy(tampered)
    unsigned = dict(forged_state)
    unsigned.pop("binding_fingerprint")
    forged_state["binding_fingerprint"] = _fingerprint(unsigned)
    response = client.post(
        "/api/qc/apply",
        json={"finding_ids": safe_ids, "preview_basis": forged_state},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "qc_preview_stale"

    response = client.post(
        "/api/qc/apply",
        json={
            "finding_ids": ["first"],
            "preview_basis": preview["basis"],
        },
    )
    assert response.status_code == 409
    assert response.json()["code"] == "qc_preview_selection_mismatch"

    first = result.finding("first")
    assert first is not None
    first.title = "The retained result changed after preview."
    response = client.post(
        "/api/qc/apply",
        json={
            "finding_ids": safe_ids,
            "preview_basis": preview["basis"],
        },
    )
    assert response.status_code == 409
    assert response.json()["code"] == "qc_preview_stale"
    assert session.doc.to_dict() == original_doc
