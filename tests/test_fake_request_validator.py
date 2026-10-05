"""The fakes refuse request shapes the provider refuses.

The research final submission sent ``thinking: {"type": "disabled"}`` and a
forced ``tool_choice`` to Sonnet 5.5, both HTTP 400s there, and the suite
passed because every fake accepted any dict and every test drove the path on
Sonnet 5. ``tests/fakes.py`` now checks each request it receives against the
documented per-model rules, and ``conftest.py`` fails the test that sent a
refused one. These tests pin the oracle itself, its wiring into every fake,
and production's lowest-thinking table against it.
"""
from __future__ import annotations

import anthropic
import pytest

from backend import settings
from backend.research.schema import (
    PRESERVED_THINKING_BETA,
    lowest_thinking,
    single_output_tool_kwargs,
    with_drop_block,
)
from tests.fakes import (
    REQUEST_SHAPE_VIOLATIONS,
    FakeClient,
    SequencedFakeClient,
    request_shape_problems,
    research_response,
    text_turn,
)


@pytest.fixture()
def refused_on_purpose():
    """For tests that send a refused shape deliberately: hand back what was
    recorded, then clear it so conftest's check sees only accidents."""
    yield REQUEST_SHAPE_VIOLATIONS
    REQUEST_SHAPE_VIOLATIONS.clear()


def _request(model, *, effort="medium", thinking=None, **extra):
    request = {
        "model": model,
        "max_tokens": 1024,
        "output_config": {"effort": effort},
        "messages": [{"role": "user", "content": "Hello"}],
        **extra,
    }
    if thinking is not None:
        request["thinking"] = thinking
    return request


_FORCED = {"type": "tool", "name": "submit"}


# ---------------------------------------------------------------------------
# The oracle
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("request_", [
    _request("claude-sonnet-5-5", thinking={"type": "adaptive"}),
    _request("claude-sonnet-5-5", thinking={"type": "between_tools"}),
    _request("claude-sonnet-5-5", effort="high", thinking={"type": "between_tools"}),
    _request("claude-sonnet-5-5", thinking={"type": "adaptive"},
             tool_choice={"type": "auto", "disable_parallel_tool_use": True}),
    _request("claude-sonnet-5", thinking={"type": "disabled"}, tool_choice=_FORCED),
    _request("claude-opus-5", effort="high", thinking={"type": "disabled"}),
    _request("claude-opus-4-8", effort="max", thinking={"type": "disabled"}),
    _request("claude-fable-5", thinking={"type": "adaptive"}, tool_choice=_FORCED),
    _request("claude-opus-5-5", thinking={"type": "adaptive"}),
    with_drop_block(_request("claude-opus-5-5", thinking={"type": "adaptive"})),
    _request("claude-opus-5-5", tool_choice={"type": "none"}),
    _request("some-future-model", thinking={"type": "disabled"}, tool_choice=_FORCED),
], ids=lambda request: f"{request['model']}:{request.get('thinking')}")
def test_shapes_the_provider_accepts_pass(request_):
    assert request_shape_problems(request_) == []


@pytest.mark.parametrize(("request_", "reason"), [
    (_request("claude-sonnet-5-5", thinking={"type": "disabled"}),
     '"thinking.type.disabled" is not supported'),
    (_request("claude-opus-5-5", thinking={"type": "disabled"}),
     '"thinking.type.disabled" is not supported'),
    (_request("claude-fable-5", thinking={"type": "disabled"}),
     '"thinking.type.disabled" is not supported'),
    (_request("claude-opus-5", effort="xhigh", thinking={"type": "disabled"}),
     "'xhigh' is not supported when thinking is disabled"),
    (_request("claude-sonnet-5-5", tool_choice=_FORCED),
     'type "tool" and "any" are not supported'),
    (_request("claude-opus-5-5", tool_choice={"type": "any"}),
     'type "tool" and "any" are not supported'),
    (_request("claude-sonnet-5", thinking={"type": "between_tools"}),
     '"thinking.type.between_tools" is not supported'),
    (_request("claude-opus-5-5", thinking={"type": "between_tools"}),
     '"thinking.type.between_tools" is not supported'),
    (_request("claude-sonnet-5-5", thinking={"type": "between_tools", "display": "summarized"}),
     "between_tools takes no other thinking field (got display)"),
    (with_drop_block(_request("claude-sonnet-5-5", thinking={"type": "between_tools"})),
     "between_tools takes no other thinking field (got block_binding)"),
    (_request("claude-sonnet-5-5", effort="max", thinking={"type": "between_tools"}),
     "'max' is not supported with between_tools"),
    (_request("claude-sonnet-5-5", thinking={"type": "enabled", "budget_tokens": 2048}),
     "manual thinking budget"),
    (_request("some-future-model", thinking={"type": "adaptive", "budget_tokens": 2048}),
     "manual thinking budget"),
    (_request("claude-opus-5-5", thinking={
        "type": "adaptive", "block_binding": {"prefix_mismatch_behavior": "drop_block"},
    }), "block_binding: Extra inputs are not permitted"),
    (with_drop_block(_request("claude-sonnet-5", thinking={"type": "disabled"})),
     "block_binding works only with adaptive thinking"),
], ids=lambda value: value["model"] if isinstance(value, dict) else "")
def test_shapes_the_provider_refuses_are_named(request_, reason):
    problems = request_shape_problems(request_)
    assert any(reason in problem for problem in problems), problems


def test_the_beta_is_found_in_betas_or_in_any_header_spelling():
    binding = {"type": "adaptive", "block_binding": {"prefix_mismatch_behavior": "drop_block"}}
    for extra in (
        {"betas": [PRESERVED_THINKING_BETA]},
        {"extra_headers": {"Anthropic-Beta": f"other-beta, {PRESERVED_THINKING_BETA}"}},
    ):
        assert request_shape_problems(
            _request("claude-opus-5-5", thinking=binding, **extra)
        ) == []


# ---------------------------------------------------------------------------
# Every fake applies it
# ---------------------------------------------------------------------------

_REFUSED = _request("claude-sonnet-5-5", thinking={"type": "disabled"})


def test_fake_client_raises_the_providers_400_and_records_it(refused_on_purpose):
    fake = FakeClient([text_turn(["unused"])])
    with pytest.raises(anthropic.BadRequestError, match="thinking.type.disabled"):
        fake.messages.stream(**_REFUSED)
    assert fake.messages.requests == [_REFUSED], "the request is still captured"
    assert len(refused_on_purpose) == 1
    # A valid request then consumes the scripted turn the refused one left.
    with fake.messages.stream(**_request("claude-sonnet-5-5")) as stream:
        assert stream.get_final_message().content[0].text == "unused"


def test_sequenced_fake_checks_streams_counts_and_batches(refused_on_purpose):
    client = SequencedFakeClient({"Hello": [research_response(items=[])]})
    with pytest.raises(anthropic.BadRequestError):
        client.stream(**_REFUSED)
    with pytest.raises(anthropic.BadRequestError):
        client.count_tokens(**_REFUSED)
    batch = client.batches.create(requests=[{"custom_id": "seat-1", "params": _REFUSED}])
    (line,) = client.batches.results(batch.id)
    assert line.result.type == "errored"
    assert line.result.error.error.type == "invalid_request_error"
    assert batch.request_counts.errored == 1
    assert len(refused_on_purpose) == 3


def test_a_subclass_override_cannot_skip_the_check(refused_on_purpose):
    class _Bypass(SequencedFakeClient):
        def stream(self, **request):
            raise AssertionError("an override must never see a refused shape")

        def count_tokens(self, **request):
            raise AssertionError("an override must never see a refused shape")

    client = _Bypass({})
    for method in (client.stream, client.count_tokens):
        with pytest.raises(anthropic.BadRequestError):
            method(**_REFUSED)
    assert len(refused_on_purpose) == 2


# ---------------------------------------------------------------------------
# Production's submission shape, checked against the oracle
# ---------------------------------------------------------------------------

_MODELS = (
    settings.MODEL_SONNET_55, settings.MODEL_SONNET_5, settings.MODEL_OPUS_55,
    settings.MODEL_OPUS_5, settings.MODEL_OPUS_48, settings.MODEL_FABLE_5,
    "claude-fable-5-1", "unverified-model",
)


@pytest.mark.parametrize("effort", settings.EFFORT_LEVELS)
@pytest.mark.parametrize("model", _MODELS)
def test_every_submission_shape_production_can_build_is_accepted(model, effort):
    thinking = lowest_thinking(model=model, effort=effort)
    request = _request(model, effort=effort, thinking=thinking)
    request.update(single_output_tool_kwargs(model=model, tool_name="submit"))
    if thinking["type"] == "adaptive":
        request = with_drop_block(request)
    assert request_shape_problems(request) == []


@pytest.mark.parametrize(("model", "effort", "expected"), [
    (settings.MODEL_SONNET_55, "low", "between_tools"),
    (settings.MODEL_SONNET_55, "medium", "between_tools"),
    (settings.MODEL_SONNET_55, "high", "between_tools"),
    (settings.MODEL_SONNET_55, "xhigh", "adaptive"),
    (settings.MODEL_SONNET_55, "max", "adaptive"),
    (settings.MODEL_SONNET_5, "max", "disabled"),
    (settings.MODEL_OPUS_48, "xhigh", "disabled"),
    (settings.MODEL_OPUS_5, "high", "disabled"),
    (settings.MODEL_OPUS_5, "xhigh", "adaptive"),
    (settings.MODEL_OPUS_55, "low", "adaptive"),
    (settings.MODEL_FABLE_5, "low", "adaptive"),
    ("claude-fable-5-1", "medium", "adaptive"),
    ("unverified-model", "medium", "adaptive"),
])
def test_lowest_thinking_is_the_lowest_each_model_accepts(model, effort, expected):
    assert lowest_thinking(model=model, effort=effort) == {"type": expected}


def test_lowest_thinking_returns_a_fresh_dict():
    first = lowest_thinking(model=settings.MODEL_SONNET_55, effort="medium")
    first["display"] = "summarized"
    assert lowest_thinking(model=settings.MODEL_SONNET_55, effort="medium") == {
        "type": "between_tools"
    }
