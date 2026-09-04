"""Waiting on you: the follow-up store, its chat tool, and the panel's route.

Covers ``backend.followups`` (all-or-nothing batches, duplicate suppression,
the snapshot rollback a high-water mark could not do, lenient restore), the
``track_followups`` tool end-to-end through ``/api/chat``, the per-turn
context block and its absence from the cached system prompt, the user-side
``POST /api/followup/{fid}`` route, project persistence, and the readiness
line. Mirrors the hermetic fake-client convention of
``test_suggested_prompts.py``.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend import sessions
from backend.app import create_app
from backend.followups import (
    MAX_OPEN,
    MAX_RESOLVED_KEPT,
    MAX_TITLE_CHARS,
    PANEL_RESOLUTION,
    FollowUpError,
    FollowUpStore,
    validate_track_payload,
)
from tests.fakes import FakeClient, text_turn, tool_turn


def _client() -> TestClient:
    return TestClient(create_app())


def _parse_sse(body: str) -> list[dict]:
    return [
        json.loads(line[len("data: "):])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


def _patch_client(monkeypatch, fake: FakeClient) -> None:
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)


def _track_turn(payload: dict, *, close: str = "Noted.") -> FakeClient:
    """A two-round turn: call the tool, then a closing text round."""
    return FakeClient(
        [
            tool_turn(
                ["Let me note that. "],
                payload,
                tool_id="toolu_fu",
                name="track_followups",
            ),
            text_turn([close]),
        ]
    )


def _store(*titles: str) -> FollowUpStore:
    store = FollowUpStore()
    store.apply(
        {
            "add": [{"kind": "question", "title": title} for title in titles],
            "resolve": [],
        },
        message_index=1,
    )
    return store


# ---------------------------------------------------------------------------
# Store units


def test_ids_are_monotonic_and_survive_a_rollback():
    """A rolled-back id is skipped, never recycled (the document-store rule)."""
    store = _store("A?")
    store.begin_turn()
    store.apply({"add": [{"kind": "todo", "title": "B"}], "resolve": []})
    store.rollback_turn()
    store.apply({"add": [{"kind": "todo", "title": "C"}], "resolve": []})
    assert [item.fid for item in store.items] == ["fu-1", "fu-3"]


def test_rollback_restores_a_resolution():
    """The case a high-water mark cannot handle: a turn MUTATED an item.

    FigureStore rolls back with ``del figures[mark:]`` because it is
    append-only within a turn. Resolving edits an existing item in place, so
    the store snapshots instead — and a failed turn must put the item back
    on the waiting list.
    """
    store = _store("Which hazard class?")
    store.begin_turn()
    store.resolve("fu-1", "User said Class IV.")
    assert store.get("fu-1").status == "resolved"
    store.rollback_turn()
    assert store.get("fu-1").status == "open"
    assert store.get("fu-1").resolution == ""


def test_restating_an_open_item_does_not_duplicate_it():
    store = _store("Is the site on a dedicated fire main?")
    summary = store.apply(
        {
            "add": [
                {
                    "kind": "question",
                    "title": "  IS the SITE on a   dedicated fire main?  ",
                }
            ],
            "resolve": [],
        }
    )
    assert summary["already_tracked"] == ["fu-1"]
    assert len(store.items) == 1


def test_a_settled_question_that_comes_back_is_a_new_item():
    store = _store("What is the ceiling height?")
    store.resolve("fu-1", "32 ft.")
    store.apply(
        {"add": [{"kind": "question", "title": "What is the ceiling height?"}],
         "resolve": []}
    )
    assert [item.fid for item in store.items] == ["fu-1", "fu-2"]


def test_a_batch_is_all_or_nothing():
    """An unknown id rejects the whole call, so the model never has to reason
    about which half of its request survived."""
    store = _store("A?")
    with pytest.raises(FollowUpError) as excinfo:
        store.apply(
            {
                "add": [{"kind": "todo", "title": "B"}],
                "resolve": [{"id": "fu-99", "resolution": "done"}],
            }
        )
    assert "fu-1" in str(excinfo.value)  # the open ids are echoed back
    assert [item.fid for item in store.items] == ["fu-1"]

    with pytest.raises(FollowUpError):
        store.apply(
            {
                "add": [
                    {"kind": "todo", "title": "B"},
                    {"kind": "nonsense", "title": "C"},
                ],
                "resolve": [],
            }
        )
    assert [item.fid for item in store.items] == ["fu-1"]


def test_resolving_twice_is_idempotent_not_an_error():
    store = _store("A?")
    store.resolve("fu-1", "settled")
    summary = store.apply(
        {"add": [], "resolve": [{"id": "fu-1", "resolution": "settled again"}]}
    )
    assert summary["already_settled"] == ["fu-1"]
    assert store.get("fu-1").resolution == "settled"


def test_reopen_puts_an_item_back():
    store = _store("A?")
    store.resolve("fu-1", "settled", by="user")
    assert store.reopen("fu-1") is True
    item = store.get("fu-1")
    assert (item.status, item.resolution, item.resolved_by) == ("open", "", "")
    assert store.reopen("fu-1") is False  # already open


def test_the_open_list_is_bounded():
    store = FollowUpStore()
    store.apply(
        {
            "add": [
                {"kind": "question", "title": f"Q{n}?"} for n in range(MAX_OPEN)
            ],
            "resolve": [],
        }
    )
    with pytest.raises(FollowUpError, match="settle some of them"):
        store.apply({"add": [{"kind": "question", "title": "one more?"}],
                     "resolve": []})


def test_the_settled_tail_is_trimmed_oldest_first():
    """A working list, not an audit log — it must not grow without bound."""
    store = FollowUpStore()
    for n in range(MAX_RESOLVED_KEPT + 5):
        store.apply({"add": [{"kind": "todo", "title": f"T{n}"}], "resolve": []})
        store.resolve(f"fu-{n + 1}", "done")
    assert len(store.items) == MAX_RESOLVED_KEPT
    assert store.items[0].title == "T5"


def test_settling_one_and_raising_its_replacement_fits_at_the_cap():
    """The tool tells the model to send both halves in ONE call, so the cap
    has to see the batch's final state. Checking capacity before applying
    the batch's own resolutions deadlocked a saturated tracker — and said
    "settle some of them before adding more", which is exactly what the
    rejected call was doing."""
    store = FollowUpStore()
    store.apply(
        {
            "add": [
                {"kind": "question", "title": f"Q{n}?"} for n in range(MAX_OPEN)
            ],
            "resolve": [],
        }
    )
    summary = store.apply(
        {
            "add": [{"kind": "question", "title": "the replacement?"}],
            "resolve": [{"id": "fu-1", "resolution": "answered"}],
        }
    )
    assert summary["waiting"] == MAX_OPEN
    assert summary["added"] == [f"fu-{MAX_OPEN + 1}"]
    assert summary["resolved"] == ["fu-1"]

    # Still a real ceiling: adding without settling anything is refused.
    with pytest.raises(FollowUpError, match="settle some of them"):
        store.apply({"add": [{"kind": "question", "title": "one too many?"}],
                     "resolve": []})


def test_a_rejected_batch_rolls_back_its_own_resolutions_too():
    """All-or-nothing spans BOTH halves: the resolve now runs first, so a
    later bad addition must put the settled item back."""
    store = _store("A?", "B?")
    with pytest.raises(FollowUpError):
        store.apply(
            {
                "add": [{"kind": "nonsense", "title": "C"}],
                "resolve": [{"id": "fu-1", "resolution": "answered"}],
            }
        )
    assert store.get("fu-1").status == "open"
    assert store.get("fu-1").resolution == ""


def test_an_over_long_title_is_refused_with_its_length():
    store = FollowUpStore()
    with pytest.raises(FollowUpError, match="too long"):
        store.apply(
            {"add": [{"kind": "todo", "title": "x" * (MAX_TITLE_CHARS + 1)}],
             "resolve": []}
        )


def test_a_resolution_must_say_something():
    with pytest.raises(FollowUpError, match="resolution"):
        validate_track_payload({"resolve": [{"id": "fu-1", "resolution": "  "}]})


def test_an_empty_call_is_refused():
    with pytest.raises(FollowUpError, match="at least one"):
        validate_track_payload({})


def test_next_to_surface_prefers_blocking_then_oldest():
    store = FollowUpStore()
    store.apply(
        {"add": [{"kind": "question", "title": "old, not blocking"}], "resolve": []},
        message_index=1,
    )
    store.apply(
        {"add": [{"kind": "question", "title": "newer, not blocking"}], "resolve": []},
        message_index=5,
    )
    assert store.next_to_surface().fid == "fu-1"
    store.apply(
        {
            "add": [
                {"kind": "decision", "title": "newest, blocking", "blocking": True}
            ],
            "resolve": [],
        },
        message_index=9,
    )
    assert store.next_to_surface().fid == "fu-3"
    store.resolve("fu-3", "settled")
    assert store.next_to_surface().fid == "fu-1"


def test_the_context_block_is_empty_for_an_empty_store():
    """So a session with no tracked items builds a byte-identical request."""
    assert FollowUpStore().context_block() == ""


def test_the_context_block_marks_exactly_one_next_item():
    store = FollowUpStore()
    store.apply(
        {
            "add": [
                {"kind": "question", "title": "First asked"},
                {
                    "kind": "decision",
                    "title": "Commodity class",
                    "blocking": True,
                    "detail": "Sets the density basis.",
                    "element_id": "pt2.a1.p1",
                },
            ],
            "resolve": [],
        },
        message_index=2,
    )
    block = store.context_block(message_index=6)
    assert block.count("[NEXT]") == 2  # the marked row + the trailing rule
    assert "[NEXT] fu-2" in block
    assert "BLOCKING" in block
    assert "Sets the density basis." in block
    assert "Relates to element pt2.a1.p1." in block
    assert "raised 4 replies ago" in block


def test_a_panel_resolve_with_no_note_is_disclosed_to_the_model():
    """A UI shortcut must not become a decision the model invents around."""
    store = _store("Which insurer standard applies?")
    store.resolve("fu-1", PANEL_RESOLUTION, by="user")
    block = store.context_block(message_index=3)
    assert "without saying what was decided" in block

    store2 = _store("Which insurer standard applies?")
    store2.resolve("fu-1", "FM Global 8-9.", by="user")
    assert "FM Global 8-9." in store2.context_block(message_index=3)


def test_load_never_mints_a_colliding_id():
    store = FollowUpStore()
    store.load({"followups": [
        {"fid": "fu-7", "kind": "todo", "title": "restored", "status": "open"}
    ], "next_seq": 1})
    item, _ = store.add({"kind": "todo", "title": "fresh"})
    assert item.fid == "fu-8"


def test_load_degrades_on_junk_and_clears_what_was_there():
    store = _store("A?")
    store.load({"followups": [{"kind": "nope", "title": "bad"}, "junk", 3]})
    assert store.items == []
    store2 = _store("A?")
    store2.load(None)
    assert store2.items == []


# ---------------------------------------------------------------------------
# The tool, end to end through /api/chat


def test_tracking_an_item_streams_it_and_commits(monkeypatch):
    client = _client()
    _patch_client(
        monkeypatch,
        _track_turn(
            {
                "add": [
                    {
                        "kind": "decision",
                        "title": "Confirm the commodity classification",
                        "blocking": True,
                    }
                ]
            }
        ),
    )
    resp = client.post("/api/chat", json={"message": "Start 21 13 13."})
    events = _parse_sse(resp.text)

    tracked = [e for e in events if e["type"] == "followups"]
    assert len(tracked) == 1
    assert [f["title"] for f in tracked[0]["followups"]] == [
        "Confirm the commodity classification"
    ]
    assert events[-1]["type"] == "turn_complete"

    session = sessions.get_session()
    assert [item.fid for item in session.followups.open_items()] == ["fu-1"]
    assert client.get("/api/doc").json()["followups"][0]["blocking"] is True


def test_the_tool_result_is_compact_and_the_input_rides_history(monkeypatch):
    """Token discipline: the result echoes counts; the payload is small
    enough to ride committed history verbatim (the suggest_prompts posture,
    not the figure one)."""
    client = _client()
    fake = _track_turn({"add": [{"kind": "todo", "title": "Send the datasheet"}]})
    _patch_client(monkeypatch, fake)
    client.post("/api/chat", json={"message": "hi"})

    history = sessions.get_session().history
    results = [
        block
        for message in history
        if isinstance(message.get("content"), list)
        for block in message["content"]
        if isinstance(block, dict) and block.get("type") == "tool_result"
    ]
    assert json.loads(results[-1]["content"]) == {"waiting": 1, "added": ["fu-1"]}
    uses = [
        block
        for message in history
        if isinstance(message.get("content"), list)
        for block in message["content"]
        if isinstance(block, dict) and block.get("type") == "tool_use"
    ]
    assert uses[-1]["input"]["add"][0]["title"] == "Send the datasheet"


def test_a_failed_turn_leaves_the_list_untouched(monkeypatch):
    client = _client()
    _patch_client(
        monkeypatch,
        _track_turn({"add": [{"kind": "question", "title": "A?"}]}),
    )
    client.post("/api/chat", json={"message": "first"})
    assert len(sessions.get_session().followups.items) == 1

    _patch_client(
        monkeypatch,
        FakeClient(
            [
                tool_turn(
                    ["and another. "],
                    {
                        "add": [{"kind": "question", "title": "B?"}],
                        "resolve": [{"id": "fu-1", "resolution": "settled"}],
                    },
                    tool_id="toolu_fu2",
                    name="track_followups",
                ),
                RuntimeError("boom"),
            ]
        ),
    )
    events = _parse_sse(client.post("/api/chat", json={"message": "second"}).text)
    assert any(e["type"] == "followups" for e in events)  # it streamed mid-turn
    assert any(e["type"] == "error" for e in events)

    store = sessions.get_session().followups
    assert [item.fid for item in store.items] == ["fu-1"]
    assert store.get("fu-1").status == "open"


def test_an_unknown_id_is_correctable_not_a_turn_failure(monkeypatch):
    client = _client()
    _patch_client(
        monkeypatch,
        _track_turn({"resolve": [{"id": "fu-42", "resolution": "done"}]}),
    )
    events = _parse_sse(client.post("/api/chat", json={"message": "hi"}).text)
    assert not any(e["type"] == "error" for e in events)
    assert events[-1]["type"] == "turn_complete"

    history = sessions.get_session().history
    errors = [
        block
        for message in history
        if isinstance(message.get("content"), list)
        for block in message["content"]
        if isinstance(block, dict) and block.get("is_error")
    ]
    assert errors and "fu-42" in errors[-1]["content"]


def test_the_block_reaches_the_turn_but_never_the_cached_prompt(monkeypatch):
    client = _client()
    _patch_client(
        monkeypatch,
        _track_turn({"add": [{"kind": "question", "title": "Ceiling height?"}]}),
    )
    client.post("/api/chat", json={"message": "first"})

    fake = FakeClient([text_turn(["ok"])])
    _patch_client(monkeypatch, fake)
    client.post("/api/chat", json={"message": "second"})

    request = fake.messages.last_request
    from tests.fakes import request_context_text

    context = request_context_text(request)
    assert "WAITING ON THE USER" in context
    assert "Ceiling height?" in context
    system = json.dumps(request["system"])
    assert "Ceiling height?" not in system


def test_the_block_never_fossilizes_into_history(monkeypatch):
    client = _client()
    _patch_client(
        monkeypatch,
        _track_turn({"add": [{"kind": "question", "title": "Ceiling height?"}]}),
    )
    client.post("/api/chat", json={"message": "first"})
    assert "WAITING ON THE USER" not in json.dumps(sessions.get_session().history)


# ---------------------------------------------------------------------------
# The user's side: POST /api/followup/{fid}


def test_the_user_can_tick_an_item_off_and_put_it_back(monkeypatch):
    client = _client()
    _patch_client(
        monkeypatch,
        _track_turn({"add": [{"kind": "question", "title": "Fire main?"}]}),
    )
    client.post("/api/chat", json={"message": "hi"})

    resp = client.post("/api/followup/fu-1", json={"status": "resolved"})
    assert resp.status_code == 200
    item = resp.json()["followups"][0]
    assert item["status"] == "resolved"
    assert item["resolved_by"] == "user"
    assert item["resolution"] == PANEL_RESOLUTION

    resp = client.post(
        "/api/followup/fu-1", json={"status": "resolved", "note": "Shared service."}
    )
    assert resp.json()["followups"][0]["resolution"] == "Shared service."

    resp = client.post("/api/followup/fu-1", json={"status": "open"})
    assert resp.json()["followups"][0]["status"] == "open"


def test_the_route_refuses_an_unknown_id_and_a_bad_status():
    client = _client()
    assert client.post("/api/followup/fu-9", json={"status": "resolved"}).status_code == 404
    assert client.post("/api/followup/fu-9", json={"status": "sideways"}).status_code == 400


def test_a_resolve_is_refused_while_a_turn_owns_the_store(monkeypatch):
    """Otherwise the turn's rollback would silently revert it, which reads to
    the user as the checkbox not working."""
    client = _client()
    _patch_client(
        monkeypatch,
        _track_turn({"add": [{"kind": "question", "title": "Fire main?"}]}),
    )
    client.post("/api/chat", json={"message": "hi"})

    session = sessions.get_session()
    token = session.claim_model_turn()
    try:
        resp = client.post("/api/followup/fu-1", json={"status": "resolved"})
        assert resp.status_code == 409
    finally:
        session.release_model_turn(token[0] if isinstance(token, tuple) else token)


# ---------------------------------------------------------------------------
# Persistence and readiness


def test_followups_survive_project_save_and_load(monkeypatch):
    client = _client()
    _patch_client(
        monkeypatch,
        _track_turn(
            {
                "add": [
                    {"kind": "question", "title": "Fire main?"},
                    {"kind": "todo", "title": "Send the datasheet"},
                ]
            }
        ),
    )
    client.post("/api/chat", json={"message": "hi"})
    client.post("/api/followup/fu-2", json={"status": "resolved", "note": "Sent."})

    project = json.loads(json.dumps(sessions.project_payload(sessions.get_session())))
    assert len(project["followups"]["followups"]) == 2

    sessions.reset_session()
    assert client.get("/api/doc").json()["followups"] == []

    loaded = client.post("/api/project/load", json=project)
    assert loaded.status_code == 200
    restored = loaded.json()["followups"]
    assert [(f["fid"], f["status"]) for f in restored] == [
        ("fu-1", "open"),
        ("fu-2", "resolved"),
    ]
    assert restored[1]["resolution"] == "Sent."
    # Ids keep counting from where the restored list left off.
    assert sessions.get_session().followups.add({"kind": "todo", "title": "x"})[
        0
    ].fid == "fu-3"


def test_an_empty_list_is_omitted_from_the_project_file():
    project = json.loads(json.dumps(sessions.project_payload(sessions.get_session())))
    assert "followups" not in project


def test_readiness_names_what_is_waiting_but_does_not_gate(monkeypatch):
    """Advisory: an unanswered question does not make the draft wrong the way
    an unresolved [TBD] does — but the checklist is the last place anyone
    looks before issuing, which is when a forgotten decision costs most."""
    client = _client()
    check = next(
        c
        for c in client.get("/api/readiness").json()["checks"]
        if c["id"] == "followups_clear"
    )
    assert check["ok"] is True and check["advisory"] is True

    _patch_client(
        monkeypatch,
        _track_turn(
            {
                "add": [
                    {"kind": "question", "title": "Fire main?"},
                    {
                        "kind": "decision",
                        "title": "Commodity class",
                        "blocking": True,
                    },
                ]
            }
        ),
    )
    client.post("/api/chat", json={"message": "hi"})

    payload = client.get("/api/readiness").json()
    check = next(c for c in payload["checks"] if c["id"] == "followups_clear")
    assert check["ok"] is False
    assert "blocking: Commodity class" in check["detail"]
    assert "(+1 more)" in check["detail"]
    # Advisory checks never move `ready`.
    assert all(c["ok"] for c in payload["checks"] if not c["advisory"]) == payload[
        "ready"
    ]


def test_the_stable_prompt_carries_the_policy():
    from backend.llm.prompts import render_system_prompt
    from backend.spec_modules.hyperscale_fire import HYPERSCALE_FIRE

    prompt = render_system_prompt(HYPERSCALE_FIRE)
    assert "Waiting on you" in prompt
    assert "track_followups" in prompt
    # The rule the owner asked for, and the boundary against Open items.
    assert "[NEXT]" in prompt
    assert "needs_input" in prompt
