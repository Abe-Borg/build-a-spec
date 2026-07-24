"""Chunk 8 adversarial coverage for overlapping session activity."""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from backend import sessions
from backend.app import create_app
from backend.llm import conversation
from backend.llm.conversation import SessionState, stream_user_turn
from backend.qc.engine import QCFinding, QCResult, qc_version_fingerprint
from backend.spec_doc.model import apply_edits as apply_spec_edits
from tests.fakes import FakeClient, text_turn, tool_turn


_SEED_EDITS = {
    "edits": [
        {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"},
        {
            "action": "add_paragraph",
            "target_id": "pt1.a1",
            "text": "One concurrency-safe provision.",
            "status": "confirmed",
        },
    ]
}


def test_second_model_turn_is_rejected_without_touching_first(monkeypatch):
    session = SessionState()
    fake = FakeClient([text_turn(["First turn complete."])])
    monkeypatch.setattr(
        "backend.llm.conversation.get_client",
        lambda: fake,
    )

    first = stream_user_turn(session, "first")
    assert next(first) == {"type": "status", "kind": "working", "round": 0}
    assert session.turn_active is True
    before_doc = session.doc.snapshot()
    before_history = list(session.history)

    second = list(stream_user_turn(session, "second"))
    assert second == [
        {"type": "error", "message": "A model turn is already streaming."}
    ]
    assert session.doc.snapshot() == before_doc
    assert session.history == before_history
    # The rejected stream never reaches the shared model client.
    assert fake.messages.requests == []

    completed = list(first)
    assert completed[-1]["type"] == "turn_complete"
    assert session.turn_active is False
    assert len(fake.messages.requests) == 1
    assert session.history[0] == {
        "role": "user",
        "content": [{"type": "text", "text": "first"}],
    }


def test_stop_request_is_atomic_with_model_turn_finalization(monkeypatch):
    client = TestClient(create_app())
    session = sessions.get_session()
    stop_entered = threading.Event()
    release_stop = threading.Event()

    class _BlockingStopEvent:
        def __init__(self):
            self._event = threading.Event()

        def clear(self) -> None:
            self._event.clear()

        def is_set(self) -> bool:
            return self._event.is_set()

        def set(self) -> None:
            stop_entered.set()
            assert release_stop.wait(timeout=5)
            self._event.set()

    session.stop_requested = _BlockingStopEvent()
    fake = FakeClient([text_turn(["turn that will be stopped"])])
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)
    active = stream_user_turn(session, "hold for stop")
    assert next(active)["type"] == "status"

    with ThreadPoolExecutor(max_workers=2) as pool:
        stop_future = pool.submit(client.post, "/api/chat/stop")
        assert stop_entered.wait(timeout=5)
        close_future = pool.submit(active.close)
        # request_model_stop holds the shared ownership lock through Event.set,
        # so the old turn cannot finalize and expose a newer turn to this stop.
        assert close_future.done() is False
        assert session.turn_active is True
        release_stop.set()
        stop_response = stop_future.result(timeout=5)
        close_future.result(timeout=5)

    assert stop_response.status_code == 200
    assert session.turn_active is False


def test_stop_after_claim_is_not_cleared_by_store_startup(monkeypatch):
    client = TestClient(create_app())
    session = sessions.get_session()
    context_entered = threading.Event()
    release_context = threading.Event()
    real_context = conversation._turn_context_text

    def blocking_context(active_session: SessionState) -> str:
        context_entered.set()
        assert release_context.wait(timeout=5)
        return real_context(active_session)

    fake = FakeClient([text_turn(["must never be requested"])])
    monkeypatch.setattr(conversation, "_turn_context_text", blocking_context)
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)
    turn = stream_user_turn(session, "stop during startup")

    with ThreadPoolExecutor(max_workers=1) as pool:
        first_event_future = pool.submit(next, turn)
        assert context_entered.wait(timeout=5)
        stopped = client.post("/api/chat/stop")
        assert stopped.status_code == 200
        release_context.set()
        first_event = first_event_future.result(timeout=5)

    assert first_event["type"] == "turn_complete"
    assert first_event["stop_reason"] == "user_stop"
    assert list(turn) == []
    assert fake.messages.requests == []
    assert session.turn_active is False


def test_reset_zombie_cannot_release_or_rollback_a_new_turn(monkeypatch):
    session = SessionState()
    old_fake = FakeClient([text_turn(["obsolete"])])
    monkeypatch.setattr(
        "backend.llm.conversation.get_client",
        lambda: old_fake,
    )

    old_turn = stream_user_turn(session, "old")
    assert next(old_turn)["type"] == "status"
    old_generation = session.generation
    assert session.turn_active is True

    session.reset()
    assert session.generation == old_generation + 1
    assert session.turn_active is False

    new_fake = FakeClient(
        [
            tool_turn(["Drafting."], _SEED_EDITS),
            text_turn(["New turn complete."]),
        ]
    )
    monkeypatch.setattr(
        "backend.llm.conversation.get_client",
        lambda: new_fake,
    )
    new_turn = stream_user_turn(session, "new")
    assert next(new_turn)["type"] == "status"
    new_backup = session.doc._turn_backup
    assert new_backup is not None
    assert session.turn_active is True

    # Closing the invalidated generator runs its finally block. It must not
    # clear the newer token or roll back the newer store transaction.
    old_turn.close()
    assert session.turn_active is True
    assert session.doc._turn_backup == new_backup

    completed = list(new_turn)
    assert completed[-1]["type"] == "turn_complete"
    assert session.turn_active is False
    assert session.doc._turn_backup is None
    assert session.doc.index == 1
    assert session.doc.doc.parts[0].articles[0].title == "SUMMARY"


def test_committed_tail_keeps_model_ownership_until_snapshot_is_delivered(
    monkeypatch,
):
    session = SessionState()
    fake = FakeClient(
        [
            tool_turn(["Drafting."], _SEED_EDITS),
            text_turn(["Draft complete."]),
        ]
    )
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)
    turn = stream_user_turn(session, "draft with a committed snapshot")

    while True:
        event = next(turn)
        if event["type"] == "doc_snapshot":
            break

    assert session.turn_active is True
    assert list(stream_user_turn(session, "must wait for the old tail")) == [
        {"type": "error", "message": "A model turn is already streaming."}
    ]

    remaining = list(turn)
    assert remaining[-1]["type"] == "turn_complete"
    assert session.turn_active is False


def test_reset_during_turn_context_capture_discards_startup_atomically(
    monkeypatch,
):
    session = SessionState()
    fake = FakeClient([text_turn(["must not be requested"])])
    monkeypatch.setattr(
        "backend.llm.conversation.get_client",
        lambda: fake,
    )
    real_context = conversation._turn_context_text

    def reset_during_context(active_session: SessionState) -> str:
        active_session.reset()
        return real_context(active_session)

    monkeypatch.setattr(conversation, "_turn_context_text", reset_during_context)
    events = list(stream_user_turn(session, "racing reset"))

    assert events == [
        {
            "type": "error",
            "message": "The session was reset while this turn was starting; "
            "the turn was discarded.",
        }
    ]
    assert session.turn_active is False
    assert session.doc._turn_backup is None
    assert session.figures._turn_mark is None
    assert fake.messages.requests == []


def test_model_turn_blocks_undo_redo_manual_edit_qc_and_another_model(
    monkeypatch,
):
    client = TestClient(create_app())
    seeded = client.post(
        "/api/doc/edit",
        json={"ops": _SEED_EDITS["edits"]},
    )
    assert seeded.status_code == 200, seeded.text
    session = sessions.get_session()
    before = session.doc.snapshot()

    fake = FakeClient([text_turn(["Holding the model slot."])])
    monkeypatch.setattr(
        "backend.llm.conversation.get_client",
        lambda: fake,
    )
    active = stream_user_turn(session, "hold")
    assert next(active)["type"] == "status"

    try:
        assert client.post("/api/doc/undo").status_code == 409
        assert client.post("/api/doc/redo").status_code == 409
        assert (
            client.post(
                "/api/doc/edit",
                json={"ops": _SEED_EDITS["edits"]},
            ).status_code
            == 409
        )
        assert client.post("/api/qc/start").status_code == 409
        assert (
            client.post(
                "/api/qc/apply",
                json={"finding_ids": []},
            ).status_code
            == 409
        )
        assert list(stream_user_turn(session, "overlap"))[0]["type"] == "error"
        assert session.doc.snapshot() == before
    finally:
        active.close()

    assert session.turn_active is False
    assert client.post("/api/doc/undo").status_code == 200


def test_running_qc_snapshot_survives_undo_model_and_redo_activity(
    monkeypatch,
):
    client = TestClient(create_app())
    assert client.post(
        "/api/doc/edit",
        json={"ops": _SEED_EDITS["edits"]},
    ).status_code == 200
    assert client.post(
        "/api/doc/edit",
        json={
            "ops": [
                {
                    "action": "replace",
                    "target_id": "pt1.a1.p1",
                    "text": "Second undoable concurrency provision.",
                    "status": "confirmed",
                }
            ]
        },
    ).status_code == 200

    release_qc = threading.Event()

    class _BlockingQCClient:
        def __init__(self):
            self.messages = self

        def stream(self, **_request):
            release_qc.wait(timeout=5)
            raise RuntimeError("adversarial QC released")

    monkeypatch.setattr("backend.app.get_client", lambda: _BlockingQCClient())
    started = client.post("/api/qc/start")
    assert started.status_code == 200, started.text
    session = sessions.get_session()
    qc_runner = session.qc
    assert qc_runner.status == "running"

    try:
        # QC owns an immutable version snapshot, so ordinary history movement
        # can continue without letting the eventual result apply as current.
        assert client.post("/api/doc/undo").status_code == 200
        undone = session.doc.snapshot()

        model_fake = FakeClient([text_turn(["No document edit."])])
        monkeypatch.setattr(
            "backend.llm.conversation.get_client",
            lambda: model_fake,
        )
        model_turn = stream_user_turn(session, "review the undone version")
        assert next(model_turn)["type"] == "status"
        assert client.post("/api/doc/redo").status_code == 409
        assert client.post("/api/qc/start").status_code == 409
        assert (
            client.post(
                "/api/qc/apply",
                json={"finding_ids": []},
            ).status_code
            == 409
        )
        assert session.doc.snapshot() == undone
        assert list(model_turn)[-1]["type"] == "turn_complete"
        assert client.post("/api/doc/redo").status_code == 200
        redone = session.doc.snapshot()
    finally:
        release_qc.set()

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not qc_runner.is_terminal:
        time.sleep(0.01)
    assert qc_runner.status == "failed"
    assert session.doc.snapshot() == redone


def test_undo_transaction_excludes_a_racing_model_claim(monkeypatch):
    client = TestClient(create_app())
    assert client.post(
        "/api/doc/edit",
        json={"ops": _SEED_EDITS["edits"]},
    ).status_code == 200
    session = sessions.get_session()
    original_undo = session.doc.undo
    undo_entered = threading.Event()
    release_undo = threading.Event()

    def blocking_undo() -> bool:
        undo_entered.set()
        assert release_undo.wait(timeout=5)
        return original_undo()

    monkeypatch.setattr(session.doc, "undo", blocking_undo)
    claim_attempted = threading.Event()
    original_claim = session.claim_model_turn

    def observed_claim():
        claim_attempted.set()
        return original_claim()

    monkeypatch.setattr(session, "claim_model_turn", observed_claim)
    fake = FakeClient([text_turn(["after undo"])])
    monkeypatch.setattr(
        "backend.llm.conversation.get_client",
        lambda: fake,
    )
    model_turn = stream_user_turn(session, "race the undo")

    with ThreadPoolExecutor(max_workers=2) as pool:
        undo_future = pool.submit(client.post, "/api/doc/undo")
        assert undo_entered.wait(timeout=5)
        model_future = pool.submit(next, model_turn)
        assert claim_attempted.wait(timeout=5)
        # undo() is blocked while holding the shared state lock, so the model
        # claim cannot observe a pre-undo tree or begin a competing backup.
        assert model_future.done() is False
        assert session.turn_active is False
        release_undo.set()
        undo_response = undo_future.result(timeout=5)
        first_model_event = model_future.result(timeout=5)

    assert undo_response.status_code == 200
    assert session.doc.index == 0
    assert first_model_event["type"] == "status"
    assert session.turn_active is True
    model_turn.close()
    assert session.turn_active is False


def test_figure_delete_transaction_excludes_a_racing_model_claim(monkeypatch):
    client = TestClient(create_app())
    session = sessions.get_session()
    figure = session.figures.create(
        {
            "kind": "mermaid",
            "title": "Concurrency figure",
            "caption": "Figure delete/model claim interleaving fixture.",
            "alt_text": "Two nodes connected by an arrow.",
            "source": "graph LR; A-->B;",
        }
    )
    original_delete = session.figures.delete
    delete_entered = threading.Event()
    release_delete = threading.Event()

    def blocking_delete(fid: str) -> bool:
        delete_entered.set()
        assert release_delete.wait(timeout=5)
        return original_delete(fid)

    monkeypatch.setattr(session.figures, "delete", blocking_delete)
    claim_attempted = threading.Event()
    original_claim = session.claim_model_turn

    def observed_claim():
        claim_attempted.set()
        return original_claim()

    monkeypatch.setattr(session, "claim_model_turn", observed_claim)
    fake = FakeClient([text_turn(["after figure delete"])])
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)
    model_turn = stream_user_turn(session, "race the figure delete")

    with ThreadPoolExecutor(max_workers=2) as pool:
        delete_future = pool.submit(client.delete, f"/api/figure/{figure.fid}")
        assert delete_entered.wait(timeout=5)
        model_future = pool.submit(next, model_turn)
        assert claim_attempted.wait(timeout=5)
        assert model_future.done() is False
        assert session.turn_active is False
        release_delete.set()
        delete_response = delete_future.result(timeout=5)
        first_model_event = model_future.result(timeout=5)

    assert delete_response.status_code == 200
    assert delete_response.json()["figures"] == []
    assert first_model_event["type"] == "status"
    assert session.turn_active is True
    model_turn.close()
    assert session.turn_active is False
    assert session.figures.snapshot() == []


def test_qc_apply_rejects_same_index_replacement_branch_aba(monkeypatch):
    client = TestClient(create_app())
    assert client.post(
        "/api/doc/edit",
        json={"ops": _SEED_EDITS["edits"]},
    ).status_code == 200
    session = sessions.get_session()
    reviewed_record = session.doc.versions[session.doc.index]
    finding_id = "qc-index-aba"
    session.qc.restore(
        QCResult(
            findings=[
                QCFinding(
                    finding_id=finding_id,
                    lens_id="constructability",
                    severity="high",
                    element_id="pt1.a1.p1",
                    title="Replace reviewed provision",
                    issue="Exercise the two-phase QC apply guard.",
                    rationale="The fix belongs only to the reviewed version.",
                    proposed_ops=[
                        {
                            "action": "replace",
                            "target_id": "pt1.a1.p1",
                            "text": "QC-approved provision.",
                            "status": "confirmed",
                        }
                    ],
                    ops_valid=True,
                )
            ],
            version_index=session.doc.index,
            version_fingerprint=qc_version_fingerprint(session.doc.doc),
        )
    )

    validation_entered = threading.Event()
    release_validation = threading.Event()

    def blocking_validation(section, edits):
        validation_entered.set()
        assert release_validation.wait(timeout=5)
        return apply_spec_edits(section, edits)

    monkeypatch.setattr("backend.app.apply_edits", blocking_validation)
    with ThreadPoolExecutor(max_workers=2) as pool:
        apply_future = pool.submit(
            client.post,
            "/api/qc/apply",
            json={"finding_ids": [finding_id]},
        )
        assert validation_entered.wait(timeout=5)
        assert client.post("/api/doc/undo").status_code == 200
        replacement = client.post(
            "/api/doc/edit",
            json={
                "ops": [
                    {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"},
                    {
                        "action": "add_paragraph",
                        "target_id": "pt1.a1",
                        "text": "Unreviewed replacement-branch provision.",
                        "status": "confirmed",
                    },
                ]
            },
        )
        assert replacement.status_code == 200, replacement.text
        assert session.doc.index == 1
        assert session.doc.versions[1] is not reviewed_record
        replacement_snapshot = session.doc.snapshot()
        release_validation.set()
        response = apply_future.result(timeout=5)

    assert response.status_code == 409, response.text
    assert session.doc.snapshot() == replacement_snapshot
    assert session.qc.result.finding(finding_id).status == "open"


def test_completed_qc_is_stale_after_same_index_branch_replacement():
    client = TestClient(create_app())
    assert client.post(
        "/api/doc/edit",
        json={"ops": _SEED_EDITS["edits"]},
    ).status_code == 200
    session = sessions.get_session()
    finding_id = "qc-completed-index-aba"
    session.qc.restore(
        QCResult(
            findings=[
                QCFinding(
                    finding_id=finding_id,
                    lens_id="constructability",
                    severity="high",
                    element_id="pt1.a1.p1",
                    title="Fix reviewed wording",
                    issue="This finding belongs to the original branch only.",
                    rationale="Version identity must survive numeric-index ABA.",
                    proposed_ops=[
                        {
                            "action": "replace",
                            "target_id": "pt1.a1.p1",
                            "text": "QC text for the abandoned branch.",
                            "status": "confirmed",
                        }
                    ],
                    ops_valid=True,
                )
            ],
            version_index=session.doc.index,
            version_fingerprint=qc_version_fingerprint(session.doc.doc),
        )
    )
    current_check = next(
        item
        for item in client.get("/api/readiness").json()["checks"]
        if item["id"] == "qc_current"
    )
    assert current_check["ok"] is True

    assert client.post("/api/doc/undo").status_code == 200
    replacement = client.post(
        "/api/doc/edit",
        json={
            "ops": [
                {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"},
                {
                    "action": "add_paragraph",
                    "target_id": "pt1.a1",
                    "text": "Different content at the same deterministic IDs.",
                    "status": "confirmed",
                },
            ]
        },
    )
    assert replacement.status_code == 200, replacement.text
    assert session.doc.index == 1
    replacement_snapshot = session.doc.snapshot()

    stale_check = next(
        item
        for item in client.get("/api/readiness").json()["checks"]
        if item["id"] == "qc_current"
    )
    assert stale_check["ok"] is False
    rejected = client.post(
        "/api/qc/apply",
        json={"finding_ids": [finding_id]},
    )
    assert rejected.status_code == 409
    assert "stale" in rejected.json()["error"].lower()
    assert session.doc.snapshot() == replacement_snapshot


def test_reset_discards_late_qc_usage_from_abandoned_runner(monkeypatch):
    client = TestClient(create_app())
    assert client.post(
        "/api/doc/edit",
        json={"ops": _SEED_EDITS["edits"]},
    ).status_code == 200
    session = sessions.get_session()
    captured: dict[str, object] = {}

    def capture_start(**kwargs):
        captured["usage_sink"] = kwargs["usage_sink"]
        return True

    monkeypatch.setattr("backend.app.get_client", lambda: object())
    monkeypatch.setattr(session.qc, "start", capture_start)
    assert client.post("/api/qc/start").status_code == 200

    late_usage = captured["usage_sink"]
    session.reset()
    late_usage({"input_tokens": 17, "output_tokens": 9})

    assert session.usage.snapshot()["categories"] == {}
    assert session.usage.snapshot()["totals"] == {}
