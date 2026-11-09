"""A whole-section pass raises its effort per message and keeps the cache.

Since P55-3 a ready full-draft or adapt-imported pass runs at
``settings.DRAFT_PASS_EFFORT`` (``high``) while ordinary turns run at
``INTERVIEW_EFFORT`` (``medium``). The top-level effort is part of the
messages cache's key, so the pass used to write the whole committed
conversation (and the project block) again instead of reading it — decision
D3 accepted that, because Anthropic's per-message effort would have put a
``role: "system"`` message into saved history.

Since 2026-11-09 the pass keeps the interview's effort at the top level and
raises its depth with an effort-only system message right before its first
message, inserted into each round's request and never into the turn's
messages. These tests pin: the request's cached prefix is byte for byte an
ordinary turn's; the message sits at the same index every round; nothing of
it reaches history; a refusal falls back to the old top-level request once
and switches the feature off for the process (the latch condensing shares);
and the cases that keep the old request.
"""
from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient

from backend import sessions, settings
from backend.app import create_app
from backend.llm import conversation
from backend.llm.conversation import split_turn_effort
from backend.llm.prompts import adapt_imported_directive, draft_prerequisites
from tests.fakes import FakeClient, bad_request, request_shape_problems, text_turn, tool_turn

_BETA = settings.PER_MESSAGE_EFFORT_BETA


def _client() -> TestClient:
    return TestClient(create_app())


def _parse_sse(body: str) -> list[dict]:
    return [
        json.loads(line[len("data: "):])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


def _directive() -> str:
    return adapt_imported_directive(
        draft_prerequisites(
            section_number="21 13 13",
            section_title="WET-PIPE SPRINKLER SYSTEMS",
            project_type="Hyperscale Data Center",
            country="US",
        )
    )


def _two_round_pass() -> list:
    """An edit, then the closing message."""
    return [
        tool_turn(
            ["Laying down PART 1. "],
            {"edits": [{"action": "add_article", "target_id": "pt1", "text": "SUMMARY"}]},
            tool_id="toolu_e1",
        ),
        text_turn(["Done. Anything else?"]),
    ]


def _chat(client: TestClient, message: str) -> list[dict]:
    events = _parse_sse(client.post("/api/chat", json={"message": message}).text)
    assert events[-1]["type"] == "turn_complete", events[-1]
    return events


def _effort_message_indexes(request: dict) -> list[int]:
    return [
        index
        for index, message in enumerate(request["messages"])
        if message.get("role") == "system"
    ]


def _betas(request: dict) -> set[str]:
    value = dict(request.get("extra_headers") or {}).get("anthropic-beta", "")
    return {part.strip() for part in value.split(",") if part.strip()}


def _strip_effort(request: dict) -> list[dict]:
    return [m for m in request["messages"] if m.get("role") != "system"]


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------


def test_a_pass_keeps_the_interview_effort_at_the_top_level():
    top, message = split_turn_effort(settings.DRAFT_PASS_EFFORT, settings.INTERVIEW_MODEL)
    assert (top, message) == (settings.INTERVIEW_EFFORT, settings.DRAFT_PASS_EFFORT)
    assert settings.INTERVIEW_MODEL in settings.PER_MESSAGE_EFFORT_MODELS


def test_an_ordinary_turn_sends_its_effort_at_the_top_level():
    assert split_turn_effort(settings.INTERVIEW_EFFORT, settings.INTERVIEW_MODEL) == (
        settings.INTERVIEW_EFFORT,
        "",
    )


def test_a_model_without_the_feature_keeps_the_old_request():
    assert split_turn_effort("high", settings.MODEL_SONNET_5) == ("high", "")


def test_a_refused_feature_keeps_the_old_request():
    conversation._refuse_per_message_effort("messages.3.output_config: Extra inputs")
    assert split_turn_effort("high", settings.INTERVIEW_MODEL) == ("high", "")


# ---------------------------------------------------------------------------
# The request
# ---------------------------------------------------------------------------


_HISTORY = [
    {"role": "user", "content": [{"type": "text", "text": "Start the section."}]},
    {"role": "assistant", "content": [{"type": "text", "text": "Which edition?"}]},
]


def _inputs(text: str, *, effort: str, message_effort: str = "", history=None):
    return conversation._ChatRequestInputs(
        history=list(_HISTORY if history is None else history),
        new_messages=[{"role": "user", "content": [{"type": "text", "text": text}]}],
        module=sessions.get_session().module,
        model=settings.INTERVIEW_MODEL,
        max_tokens=1024,
        effort=effort,
        project_block="=== PROJECT BACKGROUND ===\nResearch findings.",
        message_effort=message_effort,
    )


def test_the_cached_prefix_is_byte_for_byte_an_ordinary_turns():
    """Everything a cache entry is keyed on up to the committed boundary —
    tools, system, thinking, top-level effort and the history with its
    breakpoints and project block — is identical, so the pass reads what the
    turns before it wrote."""
    ordinary = conversation._build_chat_request(
        _inputs("What else?", effort=settings.INTERVIEW_EFFORT)
    )
    top, message = split_turn_effort(settings.DRAFT_PASS_EFFORT, settings.INTERVIEW_MODEL)
    draft = conversation._build_chat_request(
        _inputs(_directive(), effort=top, message_effort=message)
    )
    for key in ("model", "system", "tools", "thinking", "output_config"):
        assert draft[key] == ordinary[key], key
    assert draft["output_config"] == {"effort": settings.INTERVIEW_EFFORT}
    n = len(_HISTORY)
    assert draft["messages"][:n] == ordinary["messages"][:n]
    # The boundary breakpoint the turns before wrote is on the same block.
    assert draft["messages"][n - 1]["content"][-1]["cache_control"] == (
        ordinary["messages"][n - 1]["content"][-1]["cache_control"]
    )
    # Then the effort-only message, then the turn.
    assert draft["messages"][n] == {
        "role": "system",
        "content": [],
        "output_config": {"effort": settings.DRAFT_PASS_EFFORT},
    }
    assert _BETA in _betas(draft)
    assert "extra_headers" not in ordinary
    assert request_shape_problems(draft) == []


def test_on_an_empty_history_the_project_block_takes_its_own_message():
    top, message = split_turn_effort(settings.DRAFT_PASS_EFFORT, settings.INTERVIEW_MODEL)
    request = conversation._build_chat_request(
        _inputs(_directive(), effort=top, message_effort=message, history=[])
    )
    roles = [m["role"] for m in request["messages"]]
    assert roles == ["user", "system", "user"]
    assert request["messages"][0]["content"][0]["text"].startswith(
        "=== PROJECT BACKGROUND ==="
    )
    assert request_shape_problems(request) == []


def test_an_edited_conversation_carries_both_betas():
    """The resend sanitizer's edits add ``drop_block`` and its beta; the
    effort message's beta merges beside it instead of replacing it."""
    history = [
        {"role": "user", "content": [{"type": "text", "text": "Look it up."}]},
        {
            "role": "assistant",
            "content": [
                {"type": "server_tool_use", "id": "srvtoolu_x", "name": "web_search", "input": {"query": "q"}},
                {"type": "text", "text": "Found nothing."},
            ],
        },
    ]
    top, message = split_turn_effort(settings.DRAFT_PASS_EFFORT, settings.INTERVIEW_MODEL)
    request = conversation._build_chat_request(
        _inputs(_directive(), effort=top, message_effort=message, history=history)
    )
    assert request["thinking"]["block_binding"] == {"prefix_mismatch_behavior": "drop_block"}
    assert _BETA in _betas(request)
    assert "thinking-binding-controls-2026-08-01" in _betas(request)
    assert request_shape_problems(request) == []


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------


def test_every_round_of_a_pass_sends_the_message_at_the_same_place(monkeypatch):
    fake = FakeClient([text_turn(["Which edition?"]), *_two_round_pass(), text_turn(["Sure?"])])
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)
    client = _client()

    _chat(client, "Start the section.")
    _chat(client, _directive())
    _chat(client, "What else?")
    ordinary, round0, round1, after = fake.messages.requests

    # The ordinary turns: no message, no beta, the interview's effort.
    for request in (ordinary, after):
        assert _effort_message_indexes(request) == []
        assert _BETA not in _betas(request)
        assert request["output_config"] == {"effort": settings.INTERVIEW_EFFORT}

    # Both rounds of the pass: the interview's effort at the top level, the
    # message right after the two committed messages, every round.
    for request in (round0, round1):
        assert request["output_config"] == {"effort": settings.INTERVIEW_EFFORT}
        # Inserting the message is not an edit of the conversation: no
        # drop_block, the same thinking config as every ordinary turn.
        assert request["thinking"] == ordinary["thinking"]
        assert _effort_message_indexes(request) == [2]
        assert request["messages"][2]["output_config"] == {"effort": settings.DRAFT_PASS_EFFORT}
        assert _BETA in _betas(request)
    # The second round extends the first: same prefix through the turn's
    # opening message, and the effort message never moves.
    assert round1["messages"][:3] == round0["messages"][:3]
    assert conversation.summary_effort(round0) == settings.DRAFT_PASS_EFFORT

    # Nothing of it reached saved history.
    history = sessions.get_session().history
    assert all(message.get("role") in ("user", "assistant") for message in history)
    assert all("output_config" not in message for message in history)
    # So the turn after it sends a conversation with no system message.
    assert all(m["role"] != "system" for m in after["messages"])


def test_a_refused_message_resends_the_old_request_once_and_switches_off(
    monkeypatch, caplog
):
    fake = FakeClient(
        [
            text_turn(["Which edition?"]),
            bad_request("messages.2.output_config: Extra inputs are not permitted"),
            *_two_round_pass(),
            *_two_round_pass(),
        ]
    )
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)
    client = _client()
    _chat(client, "Start the section.")
    with caplog.at_level(logging.WARNING, logger="buildaspec.chat"):
        _chat(client, _directive())
        _chat(client, _directive())

    _ordinary, refused, *rest = fake.messages.requests
    assert _effort_message_indexes(refused) == [2]
    # Every request after the refusal — the resend, its next round, and the
    # whole next pass — is the old top-level request.
    assert len(rest) == 4
    for request in rest:
        assert _effort_message_indexes(request) == []
        assert _BETA not in _betas(request)
        assert request["output_config"] == {"effort": settings.DRAFT_PASS_EFFORT}
    # The resend is the refused request with the depth moved back up.
    assert rest[0]["messages"] == _strip_effort(refused)
    assert not conversation.per_message_effort_available()
    warnings = [r for r in caplog.records if "per-message effort" in r.getMessage()]
    assert len(warnings) == 1


def test_another_400_is_not_read_as_a_refusal(monkeypatch):
    fake = FakeClient([bad_request("messages.1: something else entirely")])
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)
    events = _parse_sse(_client().post("/api/chat", json={"message": _directive()}).text)
    assert events[-1]["type"] == "error"
    assert conversation.per_message_effort_available()


def test_the_boost_switched_off_sends_no_message(monkeypatch):
    monkeypatch.setattr(settings, "DRAFT_PASS_EFFORT", settings.INTERVIEW_EFFORT)
    fake = FakeClient(_two_round_pass())
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)
    _chat(_client(), _directive())
    for request in fake.messages.requests:
        assert _effort_message_indexes(request) == []
        assert "extra_headers" not in request or _BETA not in _betas(request)


@pytest.fixture(autouse=True)
def _armed():
    """The conftest re-arms the latch around every test; say so here too."""
    conversation.reset_per_message_effort_probe()
    yield
    conversation.reset_per_message_effort_probe()
