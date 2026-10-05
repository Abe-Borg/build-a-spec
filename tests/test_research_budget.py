"""Guard exhaustion salvages grounded research without buying another loop."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from backend import settings
from backend.research import engine
from backend.research.budget import count_request_tokens, _SERVER_TOOL_OVERHEAD_TOKENS
from backend.research.schema import RESEARCH_TOOL_NAME
from backend.spec_modules import AVAILABLE_MODULES
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


def _run(client, *, max_tokens=4096, model="claude-sonnet-5", **kwargs):
    return engine._run_dimension(
        client, module=HYPERSCALE_FIRE, profile=PROFILE, dimension=_DIMENSION,
        model=model, max_tokens=max_tokens, **kwargs,
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


# ---------------------------------------------------------------------------
# The web tools never change within a conversation. The prompt cache is a
# byte-exact prefix with the tools first, so every request of an area's
# conversation — opening, continuations, reminders, a resumed request, and a
# restart's new opening — sends the same tool bytes: a fixed per-request
# allowance, with the declared budgets enforced between requests by the
# cumulative ceilings. The one exception is the context-window clip, which
# switches once, near the window, and never back.
# ---------------------------------------------------------------------------

_SEARCHES = engine.RESEARCH_SEARCHES_PER_REQUEST
_FETCHES = engine.RESEARCH_FETCHES_PER_REQUEST


class _ToolBytesClient(_CountedClient):
    """Records each streamed request's tools as the bytes it sent, at send
    time — so a shared list mutated after the fact could not make two
    requests look alike."""

    def __init__(self, turns, counts=()):
        super().__init__(turns, counts)
        self.tool_bytes: list[str] = []

    def stream(self, **request):
        self.tool_bytes.append(json.dumps(request["tools"]))
        return super().stream(**request)


def _web_allowance(request) -> tuple[int, int]:
    search, fetch = request["tools"][:2]
    assert (search["name"], fetch["name"]) == ("web_search", "web_fetch")
    return search["max_uses"], fetch["max_uses"]


def _carries_binding(request) -> bool:
    return "block_binding" in (request.get("thinking") or {})


def _reset():
    import anthropic
    import httpx

    return anthropic.APIConnectionError(
        message="reset", request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
    )


@pytest.mark.parametrize("module", list(AVAILABLE_MODULES.values()), ids=lambda m: m.module_id)
def test_every_declared_budget_covers_the_per_request_allowance(module):
    # So every area of a shipped module sends the same tool bytes, and no
    # area's single request may spend more than the area declared.
    assert 1 < _FETCHES < _SEARCHES
    for dimension in module.research_dimensions:
        searches = dimension.max_searches or engine.RESEARCH_DEFAULT_MAX_SEARCHES
        fetches = dimension.max_fetches or engine.RESEARCH_DEFAULT_MAX_FETCHES
        assert searches >= _SEARCHES and fetches >= _FETCHES


@pytest.mark.parametrize("model", ["claude-sonnet-5", settings.RESEARCH_MODEL])
def test_every_request_of_a_conversation_sends_the_opening_tool_bytes(model):
    """Searches and fetches spent, two pauses and a reminder: every request
    carries the opening request's tool bytes (the old engine shrank the
    fetch allowance 12 → 8 → 6 here, rewriting the cached prefix each time),
    and spending the allowance edits nothing, so no request carries a
    thinking binding. The cached prefix behind the tools is unchanged too,
    and the continuations carry the tail that can read the rest."""
    client = _ToolBytesClient([
        research_response(
            items=None, searched_urls=[_URL], extra_blocks=fetch_blocks(_URL),
            searches=_SEARCHES, fetches=_FETCHES, stop_reason="pause_turn",
        ),
        research_response(
            items=None, extra_blocks=fetch_blocks(_URL + "/2"),
            searches=5, fetches=2, stop_reason="pause_turn",
        ),
        _text_only(),
        _final(),
    ])
    result = _run(client, model=model, continuation_cache=True)

    assert result.status.status == "completed", result.status.error
    assert result.items[0].grounded
    assert len(client.requests) == 4
    assert len(set(client.tool_bytes)) == 1
    opening = client.requests[0]
    for request in client.requests:
        assert _web_allowance(request) == (_SEARCHES, _FETCHES)
        assert not _carries_binding(request)
        assert "extra_headers" not in request
        assert request["system"] == opening["system"]
        assert request["messages"][0] == opening["messages"][0]
    # The two resumes carry the tail; the opening ends on the brief and the
    # reminder on its own user turn, so neither does.
    assert ["cache_control" in request for request in client.requests] == [
        False, True, True, False,
    ]
    assert client.requests[3]["messages"][-1]["role"] == "user"


def test_a_resume_and_a_restart_send_the_opening_tool_bytes(monkeypatch):
    monkeypatch.setattr(engine.time, "sleep", lambda _seconds: None)
    events = []
    client = _ToolBytesClient([
        research_response(
            items=None, searched_urls=[_URL], extra_blocks=fetch_blocks(_URL),
            searches=_SEARCHES, fetches=_FETCHES, stop_reason="pause_turn",
        ),
        _reset(), _reset(), _final(),
    ])
    result = _run(client, event_sink=events.append)

    assert result.status.status == "completed", result.status.error
    assert [e["mode"] for e in events if e["type"] == "dimension_retry"] == [
        "resume", "restart",
    ]
    opening, failed, resumed, restarted = client.requests
    assert resumed["messages"] == failed["messages"]
    assert restarted["messages"] == opening["messages"]
    assert len(set(client.tool_bytes)) == 1
    assert not any(_carries_binding(request) for request in client.requests)


@pytest.mark.parametrize(("guard", "spent", "reason"), [
    ("search", {"searches": _SEARCHES}, "web_search budget ceiling reached"),
    ("fetch", {"fetches": _FETCHES}, "web_fetch budget ceiling reached"),
])
def test_the_cumulative_ceilings_still_end_the_conversation(guard, spent, reason):
    """Each pause spends one request's whole allowance; the tools never
    shrink, and the request after the ceiling is the submission."""
    ceiling = 2 * _DIMENSION.max_searches if guard == "search" else _DIMENSION.max_fetches
    per_pause = next(iter(spent.values()))
    pauses = ceiling // per_pause
    assert pauses * per_pause == ceiling and pauses <= engine.RESEARCH_MAX_CONTINUATIONS
    client = _ToolBytesClient([
        *[research_response(
            items=None, searched_urls=[_URL], stop_reason="pause_turn", **spent,
        ) for _ in range(pauses)],
        _final(),
    ])
    result = _run(client)

    assert result.status.status == "completed", result.status.error
    assert result.items[0].grounded
    *web, submission = client.requests
    assert len(web) == pauses
    assert len(set(client.tool_bytes[:-1])) == 1
    assert all(_web_allowance(request) == (_SEARCHES, _FETCHES) for request in web)
    assert [tool["name"] for tool in submission["tools"]] == [RESEARCH_TOOL_NAME]
    assert reason in str(submission["messages"][-1])
    counted = (
        result.status.web_search_requests if guard == "search"
        else result.status.web_fetch_requests
    )
    assert counted == ceiling


def test_the_request_that_crosses_a_ceiling_overshoots_by_at_most_its_allowance():
    """One fetch short of the budget, the next request still offers the full
    per-request allowance — the bytes do not change — so the conversation can
    finish up to that allowance less one past its budget. The meter reports
    every fetch, and the next request is the submission."""
    budget = _DIMENSION.max_fetches
    spent = [_FETCHES] * ((budget - 1) // _FETCHES) + [(budget - 1) % _FETCHES]
    spent = [n for n in spent if n]
    assert sum(spent) == budget - 1
    client = _ToolBytesClient([
        *[research_response(
            items=None, searched_urls=[_URL], fetches=n, stop_reason="pause_turn",
        ) for n in spent],
        research_response(items=None, fetches=_FETCHES, stop_reason="pause_turn"),
        _final(),
    ])
    result = _run(client)

    assert result.status.status == "completed", result.status.error
    *web, submission = client.requests
    assert _web_allowance(web[-1]) == (_SEARCHES, _FETCHES)
    assert len(set(client.tool_bytes[:-1])) == 1
    assert "web_fetch budget ceiling reached" in str(submission["messages"][-1])
    assert result.status.web_fetch_requests == budget - 1 + _FETCHES


@pytest.mark.parametrize("model", ["claude-sonnet-5", settings.RESEARCH_MODEL])
def test_near_the_window_the_conversation_switches_once_to_one_fetch(model):
    """The first continuation cannot reserve room for the full allowance's
    fetches but can for one: the conversation switches to one fetch per
    request — after a response, an edit of the prefix replayed thinking is
    bound to, so it carries drop_block — and never switches back, even when
    a later request (its context shrunk) would fit the full allowance."""
    client = _ToolBytesClient(
        [pause_response(searched_urls=[_URL]), pause_response(searched_urls=[_URL]), _final()],
        counts=[1_000, 800_000, 800_000, 1_000],
    )
    result = _run(client, model=model)

    assert result.status.status == "completed", result.status.error
    assert len(client.count_requests) == 4
    opening, clipped, later = client.requests
    assert _web_allowance(opening) == (_SEARCHES, _FETCHES)
    assert _web_allowance(clipped) == _web_allowance(later) == (_SEARCHES, 1)
    assert client.tool_bytes[1] == client.tool_bytes[2] != client.tool_bytes[0]
    assert clipped["tools"][0] == opening["tools"][0]
    assert clipped["tools"][2] == opening["tools"][2]
    assert not _carries_binding(opening)
    assert _carries_binding(clipped) and _carries_binding(later)
    assert (
        800_000 + 2 * _SERVER_TOOL_OVERHEAD_TOKENS
        + clipped["max_tokens"] + engine._CONTEXT_MARGIN_TOKENS
        + _SEARCHES * engine._SEARCH_RESULT_RESERVE_TOKENS
        + 1 * engine.WEB_FETCH_MAX_CONTENT_TOKENS
    ) <= settings.RESEARCH_CONTEXT_WINDOW


def test_a_clip_at_the_opening_request_edits_nothing():
    client = _ToolBytesClient(
        [pause_response(searched_urls=[_URL]), _final()],
        counts=[800_000, 800_000, 800_000],
    )
    assert _run(client).status.status == "completed"
    opening, continuation = client.requests
    assert _web_allowance(opening) == (_SEARCHES, 1)
    assert client.tool_bytes[0] == client.tool_bytes[1]
    assert not _carries_binding(opening) and not _carries_binding(continuation)


def test_a_resume_keeps_the_clip_and_a_restart_reopens_with_the_full_allowance(monkeypatch):
    monkeypatch.setattr(engine.time, "sleep", lambda _seconds: None)
    client = _ToolBytesClient(
        [pause_response(searched_urls=[_URL]), _reset(), _reset(), _final()],
        counts=[1_000, 800_000, 800_000],
    )
    assert _run(client).status.status == "completed"
    opening, clipped, resumed, restarted = client.requests
    assert resumed["messages"] == clipped["messages"]
    assert client.tool_bytes[1] == client.tool_bytes[2] != client.tool_bytes[0]
    assert client.tool_bytes[3] == client.tool_bytes[0]
    assert _carries_binding(clipped) and _carries_binding(resumed)
    assert not _carries_binding(restarted)


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
    # Past even the one-fetch reserve, so the clip has nothing to switch to.
    client = _CountedClient([
        pause_response(searched_urls=[_URL]), _final(),
    ], counts=[1_000, 900_000, 900_000])
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
    client = _CountedClient([paused, _final()], counts=[1_000, 900_000, 970_000, 1_000])
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
    client = _CountedClient([paused, _final()], counts=[1_000, 900_000, 970_000, 1_000])
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


# ---------------------------------------------------------------------------
# The submission request is shaped per model (Sonnet 5.5 rejects disabled
# thinking and a forced tool_choice with HTTP 400; the fakes now reject both).
# ---------------------------------------------------------------------------

_SONNET_55 = settings.MODEL_SONNET_55
_OPUS_55 = settings.MODEL_OPUS_55


def _guard_turns(guard):
    """The responses that trip one guard and leave a submission to send."""
    if guard == "search":
        return [research_response(
            items=None, searched_urls=[_URL], searches=80,
            stop_reason="pause_turn", tokens={"input": 100, "output": 10},
        )]
    if guard == "fetch":
        return [research_response(
            items=None, extra_blocks=fetch_blocks(_URL), fetches=12,
            stop_reason="pause_turn", tokens={"input": 100, "output": 10},
        )]
    return [research_response(
        items=None, searched_urls=[_URL],
        stop_reason="pause_turn" if guard == "continuation" else "end_turn",
        tokens={"input": 100, "output": 10},
    ) for _ in range(3)]


def _signed_thinking(text="The authority adopted the 2021 code."):
    return SimpleNamespace(type="thinking", thinking=text, signature="signed-sig")


def _text_only(text="I have finished researching.", **kwargs):
    return research_response(
        items=None, extra_blocks=[text_block(text)], stop_reason="end_turn", **kwargs,
    )


def test_research_defaults_to_the_model_this_path_must_serve():
    # The regression below is about the shipped default, not a fixture.
    assert settings.RESEARCH_MODEL == _SONNET_55
    assert settings.RESEARCH_EFFORT in {"low", "medium", "high"}


@pytest.mark.parametrize("guard", ["search", "fetch", "continuation", "reminder"])
def test_default_model_submission_is_a_request_sonnet_55_accepts(guard, monkeypatch):
    monkeypatch.setattr(engine, "RESEARCH_MAX_CONTINUATIONS", 2)
    turns = _guard_turns(guard)
    client = _CountedClient([*turns, _final()])
    result = _run(client, model=settings.RESEARCH_MODEL, continuation_cache=True)

    assert result.status.status == "completed", result.status.error
    assert result.items[0].grounded
    assert result.status.input_tokens == 100 * len(turns) + 700
    assert len(client.requests) == len(turns) + 1
    submission = client.requests[-1]
    assert [tool["name"] for tool in submission["tools"]] == [RESEARCH_TOOL_NAME]
    # Neither 400 shape: the lowest thinking setting Sonnet 5.5 has, alone in
    # its dict, and automatic tool choice (no tool_choice key at all).
    assert submission["thinking"] == {"type": "between_tools"}
    assert "tool_choice" not in submission
    assert "extra_headers" not in submission
    assert submission["max_tokens"] == 4096
    assert "No more searches or fetches" in str(submission["messages"][-1])
    # The token counter saw the same shape the stream did.
    counted = client.count_requests[-1]
    assert counted["thinking"] == {"type": "between_tools"}
    assert "tool_choice" not in counted


@pytest.mark.parametrize(("model", "thinking", "forced"), [
    ("claude-sonnet-5", {"type": "disabled"}, True),
    ("claude-opus-5", {"type": "disabled"}, True),
    ("claude-opus-4-8", {"type": "disabled"}, False),
    (_SONNET_55, {"type": "between_tools"}, False),
])
def test_submission_thinking_and_tool_choice_follow_the_model(model, thinking, forced):
    client = _CountedClient([pause_response(searched_urls=[_URL], searches=80), _final()])
    assert _run(client, model=model).status.status == "completed"
    submission = client.requests[-1]
    assert submission["thinking"] == thinking
    if forced:
        assert submission["tool_choice"] == {
            "type": "tool", "name": RESEARCH_TOOL_NAME, "disable_parallel_tool_use": True,
        }
    else:
        assert "tool_choice" not in submission


def test_sonnet_55_submission_converts_thinking_to_notes_without_signatures():
    paused = research_response(
        items=None, searched_urls=[_URL], searches=80, stop_reason="pause_turn",
        extra_blocks=[_signed_thinking()],
    )
    client = _CountedClient([paused, _final()])
    assert _run(client, model=_SONNET_55).status.status == "completed"
    outgoing = client.requests[-1]["messages"]
    assert "The authority adopted the 2021 code." in str(outgoing)
    assert "signed-sig" not in str(outgoing)
    assert not any(
        getattr(block, "type", None) == "thinking"
        or (isinstance(block, dict) and block.get("type") == "thinking")
        for message in outgoing if isinstance(message["content"], list)
        for block in message["content"]
    )


@pytest.mark.parametrize(("model", "effort"), [(_OPUS_55, "medium"), (_SONNET_55, "xhigh")])
def test_submission_that_cannot_turn_thinking_off_replays_blocks_under_drop_block(
    model, effort, monkeypatch,
):
    # Opus 5.5 accepts neither disabled nor between_tools; Sonnet 5.5 rejects
    # between_tools above high. Adaptive thinking stays on, every thinking
    # block goes back unchanged, and drop_block covers the tool change.
    from backend.research.schema import PRESERVED_THINKING_BETA

    monkeypatch.setattr(settings, "RESEARCH_EFFORT", effort)
    thinking = _signed_thinking()
    paused = research_response(
        items=None, searched_urls=[_URL], searches=80, stop_reason="pause_turn",
        extra_blocks=[thinking],
    )
    client = _CountedClient([paused, _final()])
    assert _run(client, model=model).status.status == "completed"
    submission = client.requests[-1]
    assert [tool["name"] for tool in submission["tools"]] == [RESEARCH_TOOL_NAME]
    assert submission["thinking"] == {
        "type": "adaptive",
        "block_binding": {"prefix_mismatch_behavior": "drop_block"},
    }
    assert PRESERVED_THINKING_BETA in submission["extra_headers"]["anthropic-beta"]
    assert "tool_choice" not in submission
    assert submission["output_config"] == {"effort": effort}
    replayed = [
        block for message in submission["messages"]
        if message["role"] == "assistant" for block in message["content"]
    ]
    assert any(block is thinking for block in replayed)
    assert "[Earlier research notes]" not in str(submission["messages"])


def test_unforced_submission_that_records_nothing_is_asked_once_more():
    first_reply = research_response(
        items=None, stop_reason="end_turn",
        extra_blocks=[_signed_thinking("Progress note"), text_block("Done researching.")],
        tokens={"input": 50},
    )
    client = _CountedClient([
        pause_response(searched_urls=[_URL], searches=80), first_reply, _final(),
    ])
    result = _run(client, model=_SONNET_55)

    assert result.status.status == "completed", result.status.error
    assert result.items[0].grounded
    assert len(client.requests) == 3
    first, resend = client.requests[1], client.requests[2]
    for key in ("tools", "thinking", "max_tokens", "system"):
        assert resend[key] == first[key]
    assert "tool_choice" not in resend
    # Append-only: the first submission's messages, its reply, the request.
    assert resend["messages"][: len(first["messages"])] == first["messages"]
    assert resend["messages"][-2]["role"] == "assistant"
    assert "Done researching." in str(resend["messages"][-2])
    assert "No more searches or fetches" in str(resend["messages"][-1])
    # Thinking stays off, so the reply's signed block becomes a note.
    assert "Progress note" in str(resend["messages"])
    assert "signed-sig" not in str(resend["messages"])


def test_unforced_submission_answers_an_invented_tool_on_its_resend():
    invented = research_response(
        items=None, stop_reason="tool_use",
        extra_blocks=[tool_use_block("wrong_id", "Submit_Research_Findings", {})],
    )
    client = _CountedClient([
        pause_response(searched_urls=[_URL], searches=80), invented, _final(),
    ])
    assert _run(client, model=_SONNET_55).status.status == "completed"
    reply = client.requests[-1]["messages"][-1]["content"]
    assert len(reply) == 1
    assert reply[0]["type"] == "tool_result" and reply[0]["is_error"]
    assert reply[0]["tool_use_id"] == "wrong_id"
    assert "No more searches or fetches" in reply[0]["content"]


def test_unforced_submission_fails_after_its_one_resend_and_keeps_the_bill():
    client = _CountedClient([
        pause_response(searched_urls=[_URL], searches=80),
        _text_only(tokens={"input": 321}),
        _text_only(tokens={"input": 123}),
        _final(),
    ])
    result = _run(client, model=_SONNET_55)
    assert result.status.status == "failed"
    assert result.status.error_kind == engine.DIMENSION_ERROR_NO_PAYLOAD
    assert result.status.input_tokens == 321 + 123
    assert len(client.requests) == 1 + 1 + engine._SUBMISSION_RESENDS


def test_tagged_json_fallback_satisfies_an_unforced_submission_without_a_resend():
    import json

    payload = {"summary": "", "items": [_item("The adopted code governs.", [_URL])]}
    tagged = f"<{engine._RESEARCH_JSON_TAG}>{json.dumps(payload)}</{engine._RESEARCH_JSON_TAG}>"
    client = _CountedClient([
        pause_response(searched_urls=[_URL], searches=80), _text_only(tagged), _final(),
    ])
    result = _run(client, model=_SONNET_55)
    assert result.status.status == "completed", result.status.error
    assert result.parse_source == "text_fallback"
    assert len(client.requests) == 2


@pytest.mark.parametrize("stop_reason", ["pause_turn", "max_tokens", "refusal"])
def test_unforced_submission_stays_terminal_on_unfinished_replies(stop_reason):
    client = _CountedClient([
        pause_response(searched_urls=[_URL], searches=80),
        research_response(items=None, stop_reason=stop_reason, tokens={"input": 321}),
        _final(),
    ])
    result = _run(client, model=_SONNET_55)
    assert result.status.status == "failed"
    assert len(client.requests) == 2


def test_forced_submission_gets_no_resend():
    client = _CountedClient([
        pause_response(searched_urls=[_URL], searches=80), _text_only(), _final(),
    ])
    result = _run(client, model="claude-sonnet-5")
    assert result.status.error_kind == engine.DIMENSION_ERROR_NO_PAYLOAD
    assert len(client.requests) == 2


def test_a_resend_transport_failure_resumes_the_resend(monkeypatch):
    import anthropic
    import httpx

    monkeypatch.setattr(engine.time, "sleep", lambda _seconds: None)
    reset = anthropic.APIConnectionError(
        message="reset", request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
    )
    client = _CountedClient([
        pause_response(searched_urls=[_URL], searches=80), _text_only(), reset, _final(),
    ])
    result = _run(client, model=_SONNET_55)
    assert result.status.status == "completed", result.status.error
    assert len(client.requests) == 4
    assert client.requests[2]["messages"] == client.requests[3]["messages"]
