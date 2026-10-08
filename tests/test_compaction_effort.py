"""The condensing summary thinks at its own depth without moving the cache.

Owner decision (2026-10-08): the summary that stands in for the oldest turns
runs at "high" (``settings.COMPACTION_EFFORT``). It forks the chat request to
read the conversation the chat already cached, so its TOP-LEVEL effort must
stay the chat's: a top-level effort change would bill the whole conversation
(up to 600k tokens) again as a cache write. The depth rides Anthropic's
per-message effort instead: an effort-only ``role: "system"`` message right
before the instruction, behind its beta.

What must stay true:

- the cached prefix and every top-level key are byte for byte what the chat
  sent, and the message sits after them;
- the message goes only to a model Anthropic documents it for, only with
  adaptive thinking, and not at all when the two depths are equal;
- a provider that refuses it costs one unbilled 400: the summary is resent
  without it, at the chat's depth, and later summaries skip it until the app
  restarts;
- any other 400 is not the effort message's to answer.
"""
from __future__ import annotations

import logging

import pytest

from backend import sessions, settings
from backend.llm import conversation
from tests.fakes import FakeClient, bad_request, request_shape_problems, text_turn
from tests.test_chat_compaction import _summary

_EFFORT_BETA = "mid-conversation-output-config-2026-07-01"


def _inputs(model: str | None = None) -> conversation._CompactionInputs:
    session = sessions.get_session()
    history = [
        {"role": "user", "content": [{"type": "text", "text": "Turn 1"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Reply 1"}]},
        {"role": "user", "content": [{"type": "text", "text": "Turn 2"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Reply 2"}]},
    ]
    return conversation._CompactionInputs(
        history=history,
        view_spec=None,
        module=session.module,
        model=model or settings.INTERVIEW_MODEL,
        keep_from=2,
        covers_turns=1,
        kept_turns=1,
        instruction="Condense request from the app: summarize.",
        generation=session.generation,
        tokens_per_char=None,
        tokens_before=0,
        trigger="background",
    )


def _effort_messages(request: dict) -> list[dict]:
    return [m for m in request["messages"] if m.get("role") == "system"]


def test_the_shipped_summary_depth_is_high_and_differs_from_the_chat():
    assert settings.COMPACTION_EFFORT == "high"
    assert settings.INTERVIEW_EFFORT == "medium"
    assert settings.INTERVIEW_MODEL in settings.PER_MESSAGE_EFFORT_MODELS


def test_the_summary_asks_for_its_depth_after_the_cached_prefix():
    inputs = _inputs()
    request = conversation._build_compaction_request(inputs)
    plain = conversation._build_compaction_request(inputs, per_message_effort=False)

    # The top level is the chat's, effort included: the cache key is intact.
    assert request["output_config"] == {"effort": settings.INTERVIEW_EFFORT}
    for key in ("model", "system", "tools", "thinking", "output_config", "max_tokens"):
        assert request[key] == plain[key], key
    # Everything before the instruction is the prefix the chat cached; the
    # effort message comes after it and right before the instruction.
    assert request["messages"][:-2] == plain["messages"][:-1]
    assert request["messages"][-1] == plain["messages"][-1]
    assert request["messages"][-2] == {
        "role": "system",
        "content": [],
        "output_config": {"effort": "high"},
    }
    assert request["extra_headers"] == {"anthropic-beta": _EFFORT_BETA}
    assert "extra_headers" not in plain
    assert conversation.summary_effort(request) == "high"
    assert conversation.summary_effort(plain) == settings.INTERVIEW_EFFORT
    # A shape the provider documents as accepted.
    assert request_shape_problems(request) == []


def test_equal_depths_send_the_summary_exactly_as_before(monkeypatch):
    monkeypatch.setattr(settings, "COMPACTION_EFFORT", settings.INTERVIEW_EFFORT)
    inputs = _inputs()
    request = conversation._build_compaction_request(inputs)
    assert request == conversation._build_compaction_request(
        inputs, per_message_effort=False
    )
    assert _effort_messages(request) == []
    assert "extra_headers" not in request


@pytest.mark.parametrize(
    "model", [settings.MODEL_SONNET_5, settings.MODEL_FABLE_5, "some-future-model"]
)
def test_a_model_without_the_feature_never_gets_the_message(model):
    request = conversation._build_compaction_request(_inputs(model))
    assert _effort_messages(request) == []
    assert "extra_headers" not in request


def test_a_summary_without_adaptive_thinking_never_gets_the_message(monkeypatch):
    monkeypatch.setattr(
        conversation, "_thinking_param", lambda: {"type": "between_tools"}
    )
    request = conversation._build_compaction_request(_inputs())
    assert _effort_messages(request) == []


def test_a_refused_effort_message_costs_one_400_and_switches_off(caplog):
    session = sessions.get_session()
    fake = FakeClient(
        [
            bad_request(
                "messages.4.output_config: Extra inputs are not permitted"
            ),
            text_turn([_summary()]),
            text_turn([_summary()]),
        ]
    )
    with caplog.at_level(logging.WARNING, logger="buildaspec.chat"):
        record, error, _kind = conversation._drain(
            conversation._summary_attempt(session, _inputs(), fake)
        )
    assert record is not None, error
    first, second = fake.messages.requests
    assert _effort_messages(first) and not _effort_messages(second)
    assert "extra_headers" not in second
    # The resend is still the chat's fork: same top level, same prefix.
    assert second["output_config"] == first["output_config"]
    assert second["messages"] == [m for m in first["messages"] if m.get("role") != "system"]
    assert not conversation.per_message_effort_available()

    # Later summaries skip it rather than paying the round trip again.
    record, error, _kind = conversation._drain(
        conversation._summary_attempt(session, _inputs(), fake)
    )
    assert record is not None, error
    assert _effort_messages(fake.messages.requests[2]) == []
    warnings = [r for r in caplog.records if "per-message effort" in r.getMessage()]
    assert len(warnings) == 1


@pytest.mark.parametrize(
    "message",
    [
        "prompt is too long: 1000001 tokens > 1000000 maximum",
        "messages.2.content.0.text: text content blocks must be non-empty",
    ],
)
def test_any_other_400_is_not_the_effort_messages_to_answer(message):
    session = sessions.get_session()
    fake = FakeClient([bad_request(message)])
    record, error, kind = conversation._drain(
        conversation._summary_attempt(session, _inputs(), fake)
    )
    assert record is None
    assert kind == "api_400"
    assert len(fake.messages.requests) == 1
    assert conversation.per_message_effort_available()


def test_the_trace_records_the_depth_the_summary_ran_at(monkeypatch):
    events: list[dict] = []
    monkeypatch.setattr(
        conversation, "_trace_compaction", lambda **fields: events.append(fields)
    )
    session = sessions.get_session()
    conversation._drain(
        conversation._summary_attempt(
            session, _inputs(), FakeClient([text_turn([_summary()])])
        )
    )
    assert events[-1]["effort"] == "high"
    assert events[-1]["outcome"] == "ready"
