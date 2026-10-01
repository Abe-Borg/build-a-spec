"""Single-shot outputs respect model compatibility and require a finished turn.

Hermetic: a valid payload on an unfinished turn must still be rejected, and
each response is billed once without retrying or changing the active project.
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from backend import sessions, settings
from backend.app import _ai_generalized_template_document
from backend.harvest import (
    HARVEST_TOOL_NAME,
    HarvestError,
    build_harvest_request,
    run_harvest,
)
from backend.templates import TEMPLATE_DOCUMENT_TOOL_NAME, TemplateError
from tests.fakes import FakeClient, raw_turn, token_usage, tool_use_block
from tests.test_templates import _catalog, _starter


@pytest.fixture(params=["harvest", "template"])
def output_case(request, tmp_path):
    session = sessions.get_session()
    session.doc.seed_template(_starter(_catalog(tmp_path)))
    return request.param, session


def _reply(kind, session, *, stop_reason="tool_use", payload=None):
    tool_name = HARVEST_TOOL_NAME if kind == "harvest" else TEMPLATE_DOCUMENT_TOOL_NAME
    if payload is None:
        payload = (
            {"proposals": []}
            if kind == "harvest"
            else {"document": session.doc.doc.to_dict()}
        )
    return raw_turn(
        [tool_use_block("toolu_output", tool_name, payload)],
        stop_reason=stop_reason,
        usage=token_usage(input=1000, output=200),
    )


def _run(kind, session, fake, monkeypatch, *, model):
    monkeypatch.setattr(settings, "INTERVIEW_MODEL", model)
    if kind == "harvest":
        return run_harvest(
            fake, build_harvest_request(session), model=model, effort="medium"
        )
    monkeypatch.setattr("backend.app.get_client", lambda: fake)
    return _ai_generalized_template_document(session)


@pytest.mark.parametrize(
    "model,forced",
    [
        (settings.MODEL_SONNET_5, True),
        (settings.MODEL_OPUS_5, True),
        (settings.MODEL_FABLE_5, True),
        (settings.MODEL_SONNET_55, False),
        (settings.MODEL_OPUS_55, False),
        ("claude-fable-5-1", False),
        ("claude-mythos-5-1", False),
        (settings.MODEL_OPUS_48, False),
        ("unverified-model", False),
    ],
)
def test_only_confirmed_compatible_models_force_the_declared_tool(
    output_case, monkeypatch, model, forced
):
    kind, session = output_case
    fake = FakeClient([_reply(kind, session)])
    result = _run(kind, session, fake, monkeypatch, model=model)
    assert result is not None
    assert len(fake.messages.requests) == 1
    request = fake.messages.requests[0]
    assert request["model"] == model
    assert request["thinking"] == {"type": "adaptive"}
    assert len(request["tools"]) == 1
    tool_name = request["tools"][0]["name"]
    if forced:
        assert request["tool_choice"] == {"type": "tool", "name": tool_name}
    else:
        assert "tool_choice" not in request
    if kind == "template":
        assert "strict" not in request["tools"][0], "the recursive tool stays lenient"


@pytest.mark.parametrize(
    "stop_reason",
    ["max_tokens", "pause_turn", "model_context_window_exceeded", "stop_sequence", None, "refusal"],
)
def test_an_unfinished_or_refused_reply_never_adopts_a_valid_payload(
    output_case, monkeypatch, stop_reason
):
    kind, session = output_case
    before = deepcopy(session.doc.doc.to_dict())
    reply = _reply(kind, session, stop_reason=stop_reason)
    fake = FakeClient([reply])
    error_type = HarvestError if kind == "harvest" else TemplateError
    with pytest.raises(error_type) as raised:
        _run(kind, session, fake, monkeypatch, model=settings.MODEL_SONNET_5)
    assert len(fake.messages.requests) == 1, "a paid single-shot call never retries itself"
    assert session.doc.doc.to_dict() == before
    assert session.facts.items == []
    assert session.last_harvest_bubble == 0
    if kind == "harvest":
        expected_code = {
            "max_tokens": "harvest_cut_off",
            "refusal": "harvest_refused",
        }.get(stop_reason, "harvest_incomplete")
        assert raised.value.code == expected_code
        assert raised.value.usage is reply.usage
    else:
        assert session.usage.snapshot()["categories"]["template"] == {
            "input_tokens": 1000,
            "output_tokens": 200,
        }


def test_a_completed_end_turn_with_the_output_tool_is_still_accepted(
    output_case, monkeypatch
):
    kind, session = output_case
    fake = FakeClient([_reply(kind, session, stop_reason="end_turn")])
    assert _run(kind, session, fake, monkeypatch, model=settings.MODEL_SONNET_55) is not None


def test_forcing_a_tool_does_not_bypass_payload_validation(output_case, monkeypatch):
    kind, session = output_case
    if kind == "harvest":
        payload = {"proposals": [{"statement": "Missing required fields."}]}
        error_type, message = HarvestError, "no scope"
    else:
        document = session.doc.doc.to_dict()
        document["parts"][0]["articles"].pop()
        payload = {"document": document}
        error_type, message = TemplateError, "changed structure"
    fake = FakeClient([_reply(kind, session, payload=payload)])
    with pytest.raises(error_type, match=message):
        _run(kind, session, fake, monkeypatch, model=settings.MODEL_SONNET_5)
