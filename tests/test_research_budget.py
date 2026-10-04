"""Guard exhaustion salvages grounded research without buying another loop."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend import settings
from backend.research import engine
from backend.research.budget import count_request_tokens, _SERVER_TOOL_OVERHEAD_TOKENS
from backend.research.schema import RESEARCH_TOOL_NAME
from backend.spec_modules.hyperscale_fire import HYPERSCALE_FIRE
from tests.fakes import (
    SequencedFakeClient, bad_request, fetch_blocks, pause_response,
    research_response, text_block, tool_use_block,
)
from tests.test_research_engine import DIM_KEYS, PROFILE, _item

_URL = "https://codes.gov/adoption"
_KEY = DIM_KEYS["governing_codes"]
_DIMENSION = HYPERSCALE_FIRE.research_dimensions[0]


class _CountedClient(SequencedFakeClient):
    def __init__(self, turns, counts=()):
        super().__init__({_KEY: turns})
        self.counts = list(counts)
        self.count_requests = []

    def count_tokens(self, **request):
        # Match the counter's supported contract; the stream still receives
        # server tools and their replay blocks, but the counter must not.
        assert all(tool.get("type") in (None, "custom") for tool in request["tools"])
        assert all(message["role"] == "user" for message in request["messages"])
        assert all(block["type"] in {"text", "document", "image"}
                   for message in request["messages"] for block in message["content"])
        self.count_requests.append({**request, "messages": list(request["messages"])})
        value = self.counts.pop(0) if self.counts else 1_000
        if isinstance(value, Exception):
            raise value
        return SimpleNamespace(input_tokens=value)


def _run(client, *, max_tokens=4096, **kwargs):
    return engine._run_dimension(
        client, module=HYPERSCALE_FIRE, profile=PROFILE, dimension=_DIMENSION,
        model="claude-sonnet-5", max_tokens=max_tokens, **kwargs,
    )


def _final():
    return research_response(
        items=[_item("The adopted code governs.", [_URL])],
        tokens={"input": 700, "output": 80},
    )


@pytest.mark.parametrize("guard", ["search", "fetch", "continuation", "reminder"])
def test_each_guard_requests_one_submission_and_retains_grounding_and_usage(guard, monkeypatch):
    monkeypatch.setattr(engine, "RESEARCH_MAX_CONTINUATIONS", 2)
    if guard == "search":
        turns = [research_response(
            items=None, searched_urls=[_URL], searches=80,
            stop_reason="pause_turn", tokens={"input": 100, "output": 10},
        )]
    elif guard == "fetch":
        turns = [research_response(
            items=None, extra_blocks=fetch_blocks(_URL), fetches=12,
            stop_reason="pause_turn", tokens={"input": 100, "output": 10},
        )]
    else:
        turns = [research_response(
            items=None, searched_urls=[_URL],
            stop_reason="pause_turn" if guard == "continuation" else "end_turn",
            tokens={"input": 100, "output": 10},
        ) for _ in range(3)]
    client = _CountedClient([*turns, _final()])
    result = _run(client, continuation_cache=True)

    assert result.status.status == "completed", result.status.error
    assert result.items[0].grounded
    assert result.status.input_tokens == 100 * len(turns) + 700
    assert result.status.output_tokens == 10 * len(turns) + 80
    assert len(client.requests) == len(turns) + 1
    submission = client.requests[-1]
    assert [tool["name"] for tool in submission["tools"]] == [RESEARCH_TOOL_NAME]
    assert submission["tool_choice"] == {
        "type": "tool", "name": RESEARCH_TOOL_NAME, "disable_parallel_tool_use": True,
    }
    assert submission["thinking"] == {"type": "disabled"}
    assert "cache_control" not in submission
    assert "No more searches or fetches" in str(submission["messages"][-1])


def test_tool_allowances_shrink_across_continuations():
    client = _CountedClient([
        research_response(items=None, stop_reason="pause_turn", searches=40, fetches=2),
        research_response(items=None, stop_reason="pause_turn", searches=20, fetches=3),
        _final(),
    ])
    assert _run(client).status.status == "completed"
    allowances = [tuple(tool["max_uses"] for tool in request["tools"][:2])
                  for request in client.requests]
    assert allowances == [(40, 12), (40, 10), (20, 7)]
    for request in client.requests[1:]:
        assert request["thinking"]["block_binding"] == {
            "prefix_mismatch_behavior": "drop_block"
        }


def test_fetch_batch_is_reduced_to_fit_reserved_context():
    client = _CountedClient([_final()], counts=[500_000, 500_000])
    assert _run(client).status.status == "completed"
    request = client.requests[0]
    fetch = request["tools"][1]
    assert fetch["max_uses"] == 4
    assert (
        500_000 + 2 * _SERVER_TOOL_OVERHEAD_TOKENS
        + request["max_tokens"] + engine._CONTEXT_MARGIN_TOKENS
        + request["tools"][0]["max_uses"] * engine._SEARCH_RESULT_RESERVE_TOKENS
        + fetch["max_uses"] * fetch["max_content_tokens"]
    ) <= settings.RESEARCH_CONTEXT_WINDOW


def test_counter_uses_supported_equivalent_and_reserves_server_tool_overhead():
    from backend.research.schema import build_web_fetch_tool, build_web_search_tool
    from backend.research.schema import requirements_research_tool

    client = _CountedClient([], counts=[42])
    tools = [build_web_search_tool(max_uses=3), build_web_fetch_tool(max_uses=2),
             requirements_research_tool(model="claude-sonnet-5")]
    blocks = fetch_blocks(_URL)
    blocks[-1].content["content"] = {
        "type": "document", "source": {
            "type": "base64", "media_type": "application/pdf", "data": "PDF_BYTES",
        },
    }
    messages = [{"role": "assistant", "content": blocks}]
    request = {"model": "claude-sonnet-5", "tools": tools, "system": "Research brief"}

    assert count_request_tokens(client, messages, request) == 42 + 2 * _SERVER_TOOL_OVERHEAD_TOKENS
    counted = client.count_requests[0]
    assert counted["tools"] == [tools[-1]]
    assert counted["system"] == request["system"]
    content = counted["messages"][0]["content"]
    assert content[1] == blocks[-1].content["content"]
    assert "PDF_BYTES" not in content[0]["text"]
    assert _URL in content[0]["text"]
    assert messages[0]["content"] is blocks
    assert request["tools"] is tools


def test_counter_contract_through_real_sdk_rejects_server_tool_declarations():
    import json

    import anthropic
    import httpx2

    from backend.research.schema import build_web_fetch_tool, build_web_search_tool
    from backend.research.schema import requirements_research_tool

    requests = []

    def counter(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert request.url.path == "/v1/messages/count_tokens"
        if any(tool.get("type") not in (None, "custom") for tool in payload["tools"]):
            return httpx2.Response(400, json={
                "type": "error", "error": {"type": "invalid_request_error",
                "message": "Server tools are not supported by token counting."},
            })
        assert all(block["type"] == "text" for message in payload["messages"]
                   for block in message["content"])
        return httpx2.Response(200, json={"input_tokens": 123})

    tools = [build_web_search_tool(max_uses=3), build_web_fetch_tool(max_uses=2),
             requirements_research_tool(model="claude-sonnet-5")]
    paused = pause_response(searched_urls=[_URL], pending_query="still searching")
    with anthropic.Anthropic(
        api_key="test-key-hermetic", max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(counter)),
    ) as client:
        tokens = count_request_tokens(client, [{"role": "assistant", "content": paused.content}],
                                      {"model": "claude-sonnet-5", "tools": tools})

    assert tokens == 123 + 2 * _SERVER_TOOL_OVERHEAD_TOKENS
    assert len(requests) == 1
    assert requests[0]["tools"] == [tools[-1]]


@pytest.mark.parametrize("invalid", [None, True, -1, "100"])
def test_counter_rejects_invalid_counts(invalid):
    client = _CountedClient([], counts=[invalid])
    with pytest.raises(ValueError, match="invalid input count"):
        count_request_tokens(client, [], {"model": "claude-sonnet-5", "tools": []})


def test_near_full_context_submits_instead_of_sending_another_web_request():
    client = _CountedClient([
        pause_response(searched_urls=[_URL]), _final(),
    ], counts=[1_000, 800_000, 800_000])
    result = _run(client)
    assert result.status.status == "completed"
    assert result.items[0].grounded
    assert len(client.requests) == 2
    assert len(client.requests[-1]["tools"]) == 1
    assert "context window" in str(client.requests[-1]["messages"][-1])
    # The count includes the cached prefix and output schema, not just prose.
    assert all("system" in count and "tools" in count for count in client.count_requests)


def test_oversized_submission_elides_raw_fetch_content_without_mutating_evidence():
    blocks = fetch_blocks(_URL)
    source = {"type": "text", "media_type": "text/plain", "data": "RAW_FETCH_BODY"}
    blocks[-1].content["content"] = {
        "type": "document", "source": source, "title": "Adoption ordinance",
    }
    cited = text_block("Extracted adopted edition: 2021.")
    cited.citations = [SimpleNamespace(
        type="char_location", document_index=0, start_char_index=0,
        end_char_index=14, cited_text="2021 code", document_title="Adoption ordinance",
    )]
    paused = research_response(
        items=None, extra_blocks=[*blocks, cited],
        stop_reason="pause_turn", fetches=1,
    )
    client = _CountedClient([paused, _final()], counts=[1_000, 800_000, 970_000, 1_000])
    result = _run(client)
    assert result.status.status == "completed", result.status.error
    assert result.items[0].grounded
    outgoing = str(client.requests[-1]["messages"])
    assert "RAW_FETCH_BODY" not in outgoing
    assert "Extracted adopted edition: 2021." in outgoing
    assert "Previously cited passage: 2021 code" in outgoing
    assert "document_index" not in outgoing
    assert _URL in outgoing
    assert source["data"] == "RAW_FETCH_BODY"
    assert blocks[-1].content["content"]["source"] is source
    assert cited.citations[0].document_index == 0


def test_search_result_elision_retains_urls_and_readable_research_notes():
    paused = research_response(
        items=None, searched_urls=[_URL], queries=["Adopted code"],
        extra_blocks=[SimpleNamespace(
            type="thinking", thinking="The authority adopted the 2021 code.", signature="signed",
        )], stop_reason="pause_turn",
    )
    paused.content[1].content[0].encrypted_content = "RAW_SEARCH_BODY"
    client = _CountedClient([paused, _final()], counts=[1_000, 800_000, 970_000, 1_000])
    result = _run(client)
    assert result.status.status == "completed"
    assert result.items[0].grounded
    outgoing = str(client.requests[-1]["messages"])
    assert _URL in outgoing
    assert "The authority adopted the 2021 code." in outgoing
    assert "RAW_SEARCH_BODY" not in outgoing
    assert "signed" not in outgoing
    assert paused.content[1].content[0].encrypted_content == "RAW_SEARCH_BODY"


def test_an_unshrinkable_submission_is_never_sent_and_preserves_the_bill():
    client = _CountedClient([
        research_response(items=None, stop_reason="pause_turn", tokens={"input": 200}),
    ], counts=[1_000, 970_000, 970_000])
    result = _run(client)
    assert result.status.error_kind == engine.DIMENSION_ERROR_BUDGET
    assert result.status.input_tokens == 200
    assert len(client.requests) == 1


@pytest.mark.parametrize("stop_reason", ["pause_turn", "end_turn", "max_tokens", "refusal"])
def test_submission_is_bounded_even_when_the_model_never_submits(stop_reason):
    client = _CountedClient([
        pause_response(searched_urls=[_URL], searches=80),
        research_response(items=None, stop_reason=stop_reason, tokens={"input": 321}),
        _final(),
    ])
    result = _run(client)
    assert result.status.status == "failed"
    assert result.status.input_tokens == 321
    assert len(client.requests) == 2


def test_submission_answers_invented_tools_and_drops_signed_thinking(monkeypatch):
    monkeypatch.setattr(engine, "_MISSING_TOOL_REMINDERS", 0)
    invented = research_response(items=None, extra_blocks=[
        SimpleNamespace(type="thinking", thinking="Internal research", signature="signed"),
        tool_use_block("wrong_id", "invented", {}),
    ])
    client = _CountedClient([invented, _final()])
    assert _run(client).status.status == "completed"
    messages = client.requests[-1]["messages"]
    assert "signed" not in str(messages)
    reply = messages[-1]["content"][0]
    assert reply["type"] == "tool_result" and reply["is_error"]
    assert reply["tool_use_id"] == "wrong_id"
    assert "Internal research" in str(messages)


def test_submission_caps_output_and_checks_user_stop_after_counting():
    client = _CountedClient([pause_response(searches=80), _final()])
    assert _run(client, max_tokens=128_000).status.status == "completed"
    assert client.requests[-1]["max_tokens"] == 32_000

    stopped = _CountedClient([_final()])
    result = _run(stopped, should_stop=lambda: bool(stopped.count_requests))
    assert result.status.error_kind == engine.DIMENSION_ERROR_CANCELLED
    assert stopped.requests == []


def test_failed_token_count_does_not_send_unchecked_paid_request():
    client = _CountedClient([_final()], counts=[bad_request("Cannot count input.")])
    result = _run(client)
    assert result.status.status == "failed"
    assert client.requests == []


def test_a_submission_transport_failure_resumes_even_on_the_final_attempt(monkeypatch):
    import anthropic
    import httpx

    monkeypatch.setattr(engine.time, "sleep", lambda _seconds: None)
    reset = anthropic.APIConnectionError(
        message="reset", request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
    )
    client = _CountedClient([
        pause_response(searched_urls=[_URL], searches=80), reset, reset, _final(),
    ])
    result = _run(client)
    assert result.status.status == "completed"
    assert result.items[0].grounded
    assert len(client.requests) == 4
    for key in ("messages", "tools", "thinking", "tool_choice"):
        assert client.requests[1][key] == client.requests[2][key] == client.requests[3][key]
