"""Suggested reply chips: validation units + the suggest_prompts tool loop.

Covers ``backend.suggestions`` (strict validation, lenient restore), the
``suggest_prompts`` tool end-to-end through ``/api/chat`` (the SSE event, the
token-discipline tool result, latest-only replace semantics, wind-down when
the tool is not called, failed-turn preservation, self-correction on a bad
payload), project save/load persistence, and the stable-prompt policy. Mirrors
the hermetic fake-client convention of ``test_figures.py``.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend import sessions
from backend.app import create_app
from backend.suggestions import (
    MAX_PROMPT_CHARS,
    MAX_PROMPTS,
    SuggestError,
    restore_prompts,
    validate_prompts,
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


_PROMPTS = [
    "Use your recommended default",
    "Draft PART 2 now",
    "The ceiling height is 32 ft",
]


def _suggest_turn(
    prompts: list[str], *, close: str = "Let me know how you'd like to proceed."
) -> FakeClient:
    """A two-round turn: stage chips, then a closing text round."""
    return FakeClient(
        [
            tool_turn(
                ["Here are some options. "],
                {"prompts": prompts},
                tool_id="toolu_sug",
                name="suggest_prompts",
            ),
            text_turn([close]),
        ]
    )


# ---------------------------------------------------------------------------
# validate_prompts / restore_prompts units
# ---------------------------------------------------------------------------


def test_validate_cleans_dedupes_and_preserves_order():
    out = validate_prompts(
        {
            "prompts": [
                "  Use your   default ",  # internal whitespace folds
                "Draft PART 2 now",
                "Use your default",  # duplicate of the first after cleanup
                "Yes, ordinary hazard group 2",
            ]
        }
    )
    assert out == [
        "Use your default",
        "Draft PART 2 now",
        "Yes, ordinary hazard group 2",
    ]


@pytest.mark.parametrize(
    "payload",
    [
        ["not", "a", "dict"],  # not a dict
        {},  # prompts missing
        {"prompts": "a string"},  # prompts not a list
        {"prompts": ["ok", 123]},  # a non-string entry
        {"prompts": ["ok", "   "]},  # a blank-after-strip entry
        {"prompts": ["x" * (MAX_PROMPT_CHARS + 1)]},  # over the char cap
        {"prompts": [f"prompt {i}" for i in range(MAX_PROMPTS + 1)]},  # too many
    ],
)
def test_validate_rejects_malformed(payload):
    with pytest.raises(SuggestError):
        validate_prompts(payload)


def test_validate_empty_list_is_valid():
    assert validate_prompts({"prompts": []}) == []


def test_six_with_a_duplicate_passes():
    # Six raw entries, one a duplicate → five after cleanup → under the cap.
    raw = [f"prompt {i}" for i in range(MAX_PROMPTS)] + ["prompt 0"]
    out = validate_prompts({"prompts": raw})
    assert out == [f"prompt {i}" for i in range(MAX_PROMPTS)]


def test_restore_prompts_degrades_gracefully():
    assert restore_prompts(None) == []
    assert restore_prompts("nope") == []
    assert restore_prompts({"prompts": []}) == []
    # A mixed list keeps only valid strings, cleaned, and caps at MAX_PROMPTS.
    messy = ["  keep me  ", 123, "", "x" * (MAX_PROMPT_CHARS + 1), "keep me", "second"]
    assert restore_prompts(messy) == ["keep me", "second"]
    assert len(restore_prompts([f"p{i}" for i in range(MAX_PROMPTS + 3)])) == MAX_PROMPTS


# ---------------------------------------------------------------------------
# The suggest_prompts tool through /api/chat
# ---------------------------------------------------------------------------


def test_suggest_prompts_emits_event_and_commits(monkeypatch):
    client = _client()
    _patch_client(monkeypatch, _suggest_turn(_PROMPTS))

    resp = client.post("/api/chat", json={"message": "What hazard class?"})
    events = _parse_sse(resp.text)

    suggest_events = [e for e in events if e["type"] == "suggested_prompts"]
    assert len(suggest_events) == 1
    assert suggest_events[0]["prompts"] == _PROMPTS
    assert events[-1]["type"] == "turn_complete"

    # Committed onto the session and surfaced via the doc payload.
    assert sessions.get_session().suggested_prompts == _PROMPTS
    assert client.get("/api/doc").json()["suggested_prompts"] == _PROMPTS

    # Token discipline: the tool RESULT is the compact count, not the list;
    # but the tool_use INPUT rides history verbatim (no elision — the model
    # sees last turn's chips naturally).
    history = sessions.get_session().history
    tool_results = [
        block
        for message in history
        for block in (message.get("content") or [])
        if isinstance(block, dict) and block.get("type") == "tool_result"
    ]
    assert any(
        r.get("content") == json.dumps({"suggested": len(_PROMPTS)})
        for r in tool_results
    )
    tool_uses = [
        block
        for message in history
        for block in (message.get("content") or [])
        if isinstance(block, dict)
        and block.get("type") == "tool_use"
        and block.get("name") == "suggest_prompts"
    ]
    assert tool_uses and tool_uses[0]["input"]["prompts"] == _PROMPTS


def test_not_calling_the_tool_clears_previous(monkeypatch):
    client = _client()
    # Turn 1 stages chips.
    _patch_client(monkeypatch, _suggest_turn(_PROMPTS))
    client.post("/api/chat", json={"message": "What hazard class?"})
    assert sessions.get_session().suggested_prompts == _PROMPTS

    # Turn 2 never calls the tool → the bar winds down to empty.
    _patch_client(monkeypatch, FakeClient([text_turn(["All set."])]))
    events = _parse_sse(client.post("/api/chat", json={"message": "Thanks."}).text)
    assert not any(e["type"] == "suggested_prompts" for e in events)
    assert sessions.get_session().suggested_prompts == []
    assert client.get("/api/doc").json()["suggested_prompts"] == []


def test_failed_turn_preserves_previous_list(monkeypatch):
    client = _client()
    # Turn 1 stages list A.
    _patch_client(monkeypatch, _suggest_turn(_PROMPTS))
    client.post("/api/chat", json={"message": "What hazard class?"})

    # Turn 2 stages a different list B, then blows up before committing.
    fake = FakeClient(
        [
            tool_turn(
                ["Reconsidering… "],
                {"prompts": ["Something else entirely"]},
                tool_id="toolu_sug2",
                name="suggest_prompts",
            ),
            RuntimeError("boom"),
        ]
    )
    _patch_client(monkeypatch, fake)
    events = _parse_sse(client.post("/api/chat", json={"message": "Change it."}).text)

    # The mid-turn event fired AND the turn failed — but the committed set is
    # still list A (staging is a turn-local, discarded on rollback).
    assert any(e["type"] == "suggested_prompts" for e in events)
    assert any(e["type"] == "error" for e in events)
    assert sessions.get_session().suggested_prompts == _PROMPTS
    assert client.get("/api/doc").json()["suggested_prompts"] == _PROMPTS


@pytest.mark.parametrize(
    "bad_input",
    [
        {"prompts": [f"prompt {i}" for i in range(MAX_PROMPTS + 1)]},  # too many
        {"prompts": ["ok", 123]},  # a non-string entry
        {"prompts": ["x" * (MAX_PROMPT_CHARS + 1)]},  # over the char cap
    ],
)
def test_invalid_payload_is_correctable_not_a_turn_failure(monkeypatch, bad_input):
    client = _client()
    fake = FakeClient(
        [
            tool_turn(
                ["Trying… "],
                bad_input,
                tool_id="toolu_bad",
                name="suggest_prompts",
            ),
            text_turn(["Never mind the chips."]),
        ]
    )
    _patch_client(monkeypatch, fake)

    events = _parse_sse(client.post("/api/chat", json={"message": "Suggest."}).text)
    assert not any(e["type"] == "error" for e in events)
    assert not any(e["type"] == "suggested_prompts" for e in events)
    assert events[-1]["type"] == "turn_complete"
    assert sessions.get_session().suggested_prompts == []

    # The rejection came back to the model as an is_error tool result.
    history = sessions.get_session().history
    assert any(
        isinstance(block, dict)
        and block.get("type") == "tool_result"
        and block.get("is_error")
        for message in history
        for block in (message.get("content") or [])
    )


def test_empty_list_call_is_valid_and_clears(monkeypatch):
    client = _client()
    # Turn 1 stages chips.
    _patch_client(monkeypatch, _suggest_turn(_PROMPTS))
    client.post("/api/chat", json={"message": "What hazard class?"})

    # Turn 2 explicitly calls the tool with an empty list → clears the bar.
    _patch_client(monkeypatch, _suggest_turn([]))
    events = _parse_sse(client.post("/api/chat", json={"message": "Nothing more."}).text)
    suggest_events = [e for e in events if e["type"] == "suggested_prompts"]
    assert len(suggest_events) == 1
    assert suggest_events[0]["prompts"] == []
    assert sessions.get_session().suggested_prompts == []


def test_latest_call_in_a_turn_wins(monkeypatch):
    client = _client()
    fake = FakeClient(
        [
            tool_turn(
                ["First… "],
                {"prompts": ["First set"]},
                tool_id="toolu_a",
                name="suggest_prompts",
            ),
            tool_turn(
                ["Actually… "],
                {"prompts": ["Second set", "And another"]},
                tool_id="toolu_b",
                name="suggest_prompts",
            ),
            text_turn(["Done."]),
        ]
    )
    _patch_client(monkeypatch, fake)
    client.post("/api/chat", json={"message": "Suggest twice."})
    assert sessions.get_session().suggested_prompts == ["Second set", "And another"]


def test_session_reset_clears_suggestions(monkeypatch):
    client = _client()
    _patch_client(monkeypatch, _suggest_turn(_PROMPTS))
    client.post("/api/chat", json={"message": "What hazard class?"})
    assert sessions.get_session().suggested_prompts == _PROMPTS

    client.post("/api/session/reset")
    assert sessions.get_session().suggested_prompts == []
    assert client.get("/api/doc").json()["suggested_prompts"] == []


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_suggestions_survive_project_save_and_load(monkeypatch):
    client = _client()
    _patch_client(monkeypatch, _suggest_turn(_PROMPTS))
    client.post("/api/chat", json={"message": "What hazard class?"})

    project = client.get("/api/project/save").json()
    assert project["suggested_prompts"] == _PROMPTS

    sessions.reset_session()
    assert client.get("/api/doc").json()["suggested_prompts"] == []

    loaded = client.post("/api/project/load", json=project)
    assert loaded.status_code == 200
    # Restored both in the load response (spreads _doc_payload) and on GET.
    assert loaded.json()["suggested_prompts"] == _PROMPTS
    assert client.get("/api/doc").json()["suggested_prompts"] == _PROMPTS


def test_empty_suggestions_omitted_from_the_project_file():
    client = _client()
    project = client.get("/api/project/save").json()
    assert "suggested_prompts" not in project  # no key when there is nothing to save


# ---------------------------------------------------------------------------
# Stable prompt + demo directive
# ---------------------------------------------------------------------------


def test_stable_prompt_carries_suggested_prompts_policy():
    from backend.llm.prompts import render_system_prompt
    from backend.spec_modules.hyperscale_fire import HYPERSCALE_FIRE

    prompt = render_system_prompt(HYPERSCALE_FIRE)
    assert "Suggested replies" in prompt
    assert "suggest_prompts" in prompt
    assert "USER'S voice" in prompt
    # It is stable content — no session-varying data leaked in.
    assert "Standards editions in effect" not in prompt


def test_demo_directive_skips_suggestions():
    from backend.llm.prompts import onboarding_demo_directive

    text = onboarding_demo_directive("Plumbing")
    assert "do NOT call suggest_prompts" in text
