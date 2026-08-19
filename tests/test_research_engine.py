"""Research engine tests: fan-out policy, grounding, continuations, caps —
hermetic against the sequenced fake client."""
from __future__ import annotations

import dataclasses

import pytest

from backend.project_profile import ProjectProfile
from backend.research import (
    RequirementsProfile,
    ResearchFanoutError,
    research_context_block,
    run_requirements_research,
)
from backend.research.engine import (
    DIMENSION_ERROR_KINDS,
    ESTABLISHED_FACTS_MAX_TOKENS,
    DimensionStatus,
    ResearchItem,
    _estimate_tokens,
    build_dimension_user_message,
    established_facts_block,
    established_facts_for,
    incomplete_dimension_facts,
    sanitized_error_kind,
)
from backend.research.schema import (
    RESEARCH_TOOL_NAME,
    WEB_BLOCKED_DOMAINS,
    WEB_FETCH_MAX_CONTENT_TOKENS,
    build_web_fetch_tool,
    build_web_search_tool,
)
# DIM_KEYS route scripted turns by hyperscale_fire dimension message substrings,
# so bind the fire module explicitly (the registry default is now generic, whose
# dimension messages are discipline-parameterized and would not match).
from backend.spec_modules.hyperscale_fire import HYPERSCALE_FIRE as DEFAULT_MODULE
from tests.fakes import (
    SequencedFakeClient,
    _synthesize_events,
    user_text,
    block_start_event,
    block_stop_event,
    code_execution_tool_events,
    fetch_blocks,
    input_json_delta_event,
    pause_response,
    research_response,
)

PROFILE = ProjectProfile("Ashburn", "VA", "USA", "ExampleCo")

# Substrings unique to each hyperscale_fire dimension's user message, used
# to route scripted turns to the right worker thread.
DIM_KEYS = {
    "governing_codes": "governing building and fire codes",
    "ahj_requirements": "authority having jurisdiction",
    "client_standards": "who reviews risk",
    "site_environment": "site and environmental factors",
}


def _item(requirement: str, urls: list[str], **overrides) -> dict:
    raw = {
        "topic": "topic",
        "category": "governing_code",
        "requirement": requirement,
        "actionability": "spec_requirement",
        "authority": "State",
        "code_reference": "",
        "source_urls": urls,
        "confidence": 0.8,
        "notes": "",
    }
    raw.update(overrides)
    return raw


def _scripts(**per_dimension) -> dict[str, list]:
    """Build the client script map; unspecified dimensions succeed empty."""
    scripts = {}
    for dim_id, key in DIM_KEYS.items():
        scripts[key] = per_dimension.get(
            dim_id, [research_response(items=[], searched_urls=["https://x.gov"])]
        )
    return scripts


def _run(client) -> RequirementsProfile:
    return run_requirements_research(
        DEFAULT_MODULE, PROFILE, client, model="claude-sonnet-5", max_tokens=4096
    )


def test_fanout_merges_in_declaration_order_and_grounds_items():
    client = SequencedFakeClient(
        _scripts(
            governing_codes=[
                research_response(
                    items=[
                        _item("VCC 2021 governs.", ["https://dhcd.virginia.gov/vcc"]),
                        _item("Invented rule.", ["https://never-retrieved.example"]),
                    ],
                    searched_urls=["https://dhcd.virginia.gov/vcc"],
                )
            ],
            site_environment=[
                research_response(
                    items=[
                        _item(
                            "SDC B at the site.",
                            ["https://usgs.gov/ws"],
                            category="site_environment",
                        )
                    ],
                    searched_urls=["https://usgs.gov/ws"],
                )
            ],
        )
    )
    profile = _run(client)

    assert [s.dimension_id for s in profile.dimension_statuses] == [
        "governing_codes",
        "ahj_requirements",
        "client_standards",
        "site_environment",
    ]
    assert profile.completed_dimensions == 4 and profile.failed_dimensions == 0

    grounded = {i.requirement: i for i in profile.items}
    assert grounded["VCC 2021 governs."].grounded is True
    assert grounded["VCC 2021 governs."].accepted_sources == [
        "https://dhcd.virginia.gov/vcc"
    ]
    # Cited-but-never-retrieved URL → ungrounded, kept.
    assert grounded["Invented rule."].grounded is False
    assert grounded["Invented rule."].accepted_sources == []
    # Stable content-addressed ids.
    assert all(i.item_id.startswith("r-") for i in profile.items)

    # Every request carried the project's own search locale.
    search_tools = [
        t
        for req in client.requests
        for t in req["tools"]
        if t.get("name") == "web_search"
    ]
    assert all(
        t["user_location"]["city"] == "Ashburn"
        and t["user_location"]["region"] == "Virginia"
        for t in search_tools
    )


def test_partial_failure_keeps_going_total_failure_raises():
    boom = RuntimeError("kaput")  # non-retryable (UNKNOWN class)
    client = SequencedFakeClient(_scripts(ahj_requirements=[boom]))
    profile = _run(client)
    statuses = {s.dimension_id: s for s in profile.dimension_statuses}
    assert statuses["ahj_requirements"].status == "failed"
    assert "kaput" in statuses["ahj_requirements"].error
    assert profile.completed_dimensions == 3

    all_fail = SequencedFakeClient(
        {key: [RuntimeError("dead")] for key in DIM_KEYS.values()}
    )
    with pytest.raises(ResearchFanoutError, match="All 4"):
        _run(all_fail)


def test_web_tools_declare_direct_callers_on_every_research_request():
    """Both web tools pin ``allowed_callers: ["direct"]``, byte-exactly.

    Left unset, the ``_20260209`` versions default to the code-execution
    caller: pause_turn continuations would then need the provider container
    id (a nonretryable 400 without it) and per-search inputs would stop
    streaming. Asserting the whole dict — not just the one key — is
    deliberate: the tool bytes lead the cached prefix, so an unnoticed
    change here silently rewrites every cache lineage.
    """
    client = SequencedFakeClient(_scripts())
    _run(client)

    assert len(client.requests) == 4
    for request in client.requests:
        by_name = {tool.get("name"): tool for tool in request["tools"]}
        assert by_name["web_search"] == {
            "type": "web_search_20260209",
            "name": "web_search",
            "allowed_callers": ["direct"],
            "blocked_domains": list(WEB_BLOCKED_DOMAINS),
            "max_uses": by_name["web_search"]["max_uses"],
            "user_location": PROFILE.web_search_user_location(),
        }
        assert by_name["web_fetch"] == {
            "type": "web_fetch_20260209",
            "name": "web_fetch",
            "allowed_callers": ["direct"],
            "blocked_domains": list(WEB_BLOCKED_DOMAINS),
            "max_uses": by_name["web_fetch"]["max_uses"],
            "citations": {"enabled": True},
            "max_content_tokens": WEB_FETCH_MAX_CONTENT_TOKENS,
        }
        # The caller mode never rides the cache breakpoint — that stays on
        # the output tool, which is last.
        assert "cache_control" not in by_name["web_search"]
        assert "cache_control" not in by_name["web_fetch"]
        assert request["tools"][-1]["name"] == RESEARCH_TOOL_NAME


def test_builders_are_the_only_source_of_the_web_tool_shape():
    """A caller cannot silently opt back into the provider default."""
    search = build_web_search_tool(max_uses=3)
    fetch = build_web_fetch_tool(max_uses=2)
    assert search["allowed_callers"] == ["direct"]
    assert fetch["allowed_callers"] == ["direct"]
    # Copies, not the shared tuple: a consumer mutating one request's tool
    # list must not reach across into another's.
    assert search["allowed_callers"] is not fetch["allowed_callers"]
    search["allowed_callers"].append("code_execution_20260120")
    assert build_web_search_tool(max_uses=3)["allowed_callers"] == ["direct"]
    # Omitting user_location is still supported (the chat loop's shape).
    assert "user_location" not in search


def test_pause_turn_continuation_pools_grounding_across_responses():
    client = SequencedFakeClient(
        _scripts(
            governing_codes=[
                pause_response(searched_urls=["https://a.gov/one"]),
                research_response(
                    items=[_item("Uses page one.", ["https://a.gov/one"])],
                    searched_urls=["https://b.gov/two"],
                ),
            ]
        )
    )
    profile = _run(client)
    item = next(i for i in profile.items if i.requirement == "Uses page one.")
    # The citation grounded against the FIRST response's retrieval even
    # though the tool call came in the second.
    assert item.grounded is True

    # The continuation resumed with the assistant content re-sent.
    governing_requests = [
        req
        for req in client.requests
        if DIM_KEYS["governing_codes"] in user_text(req["messages"])
    ]
    assert len(governing_requests) == 2
    assert governing_requests[1]["messages"][1]["role"] == "assistant"


def test_search_budget_ceiling_cuts_off_runaway_dimension():
    # governing_codes budget is 40 → ceiling 80. Two pauses totalling 81
    # searches trip the guard before a third call.
    client = SequencedFakeClient(
        _scripts(
            governing_codes=[
                pause_response(searched_urls=["https://a.gov"], searches=41),
                pause_response(searched_urls=["https://b.gov"], searches=40),
            ]
        )
    )
    profile = _run(client)
    status = next(
        s for s in profile.dimension_statuses if s.dimension_id == "governing_codes"
    )
    assert status.status == "failed"
    assert "budget ceiling" in status.error
    assert status.web_search_requests == 81


def test_incomplete_stop_reason_and_missing_payload_fail_cleanly():
    client = SequencedFakeClient(
        _scripts(
            governing_codes=[
                research_response(items=[], stop_reason="max_tokens")
            ],
            client_standards=[
                # Completes but never calls the tool nor tagged JSON.
                research_response(items=None, searched_urls=["https://x.gov"])
            ],
        )
    )
    profile = _run(client)
    statuses = {s.dimension_id: s for s in profile.dimension_statuses}
    assert "stop_reason" in statuses["governing_codes"].error
    assert "no parseable payload" in statuses["client_standards"].error


def test_retryable_failure_retries_then_succeeds(monkeypatch):
    import backend.research.engine as engine

    monkeypatch.setattr(engine.time, "sleep", lambda _s: None)
    import anthropic
    import httpx

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    retryable = anthropic.APIConnectionError(message="reset", request=request)
    client = SequencedFakeClient(
        _scripts(
            governing_codes=[
                retryable,
                research_response(
                    items=[_item("Recovered.", ["https://a.gov"])],
                    searched_urls=["https://a.gov"],
                ),
            ]
        )
    )
    profile = _run(client)
    status = next(
        s for s in profile.dimension_statuses if s.dimension_id == "governing_codes"
    )
    assert status.status == "completed"
    assert any(i.requirement == "Recovered." for i in profile.items)


def test_pause_continuation_echoes_the_container_and_a_retry_drops_it(monkeypatch):
    """The provider continuation container, end to end for one dimension.

    Defense-in-depth: with ``allowed_callers: ["direct"]`` no container is
    expected. But a code-execution-called server tool can only be resumed
    inside the container it started in, so if one ever arrives the resume
    has to echo it — and a *retry*, which abandons the conversation for a
    fresh one, must not.

    One scripted dimension covers the whole contract: pause with a
    container, pause without one (not a revocation), a retryable failure,
    then success on a clean attempt.
    """
    import backend.research.engine as engine

    monkeypatch.setattr(engine.time, "sleep", lambda _s: None)
    import anthropic
    import httpx

    http_request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    retryable = anthropic.APIConnectionError(message="reset", request=http_request)
    first_pause = pause_response(
        searched_urls=["https://a.gov/one"], container="cont_research_1"
    )
    client = SequencedFakeClient(
        _scripts(
            governing_codes=[
                first_pause,
                # Omits the field — the container is not revoked, so the
                # next request must still carry it.
                pause_response(searched_urls=["https://a.gov/two"]),
                retryable,
                research_response(
                    items=[_item("Recovered.", ["https://a.gov/one"])],
                    searched_urls=["https://a.gov/one"],
                ),
            ]
        )
    )
    profile = _run(client)
    status = next(
        s for s in profile.dimension_statuses if s.dimension_id == "governing_codes"
    )
    assert status.status == "completed"

    requests = [
        req
        for req in client.requests
        if DIM_KEYS["governing_codes"] in user_text(req["messages"])
    ]
    assert len(requests) == 4
    # 1: opening request, no container to know about yet.
    assert "container" not in requests[0]
    # 2: the paused response supplied one.
    assert requests[1]["container"] == "cont_research_1"
    # 3: the second pause omitted it; the latest nonblank id is retained.
    assert requests[2]["container"] == "cont_research_1"
    # 4: a fresh retry attempt — a new conversation, so no inherited id.
    assert "container" not in requests[3]

    # The pause contract itself is unchanged: the paused assistant content
    # is re-sent verbatim, with no synthetic user turn wedged in.
    assert [m["role"] for m in requests[1]["messages"]] == ["user", "assistant"]
    assert requests[1]["messages"][1]["content"] == first_pause.content

    # The container is a top-level request argument and nothing else — it
    # must never reach the cached prefix or the conversation.
    for request in requests:
        assert "container" not in str(request["system"])
        assert "container" not in str(request["tools"])
        assert "cont_research_1" not in str(request["messages"])


def test_a_pending_search_is_resent_verbatim_on_a_research_pause():
    """The fan-out resends paused content verbatim, pending call included.

    Research shares ``sanitize_messages_for_resend`` with the chat loop, so
    the server-tool pairing guard there must not touch the message being
    resumed — the trailing result-less ``server_tool_use`` is what the
    provider picks the work back up from.
    """
    paused = pause_response(
        searched_urls=["https://a.gov/one"], pending_query="still running"
    )
    client = SequencedFakeClient(
        _scripts(
            governing_codes=[
                paused,
                research_response(items=[], searched_urls=["https://a.gov/one"]),
            ]
        )
    )
    _run(client)

    requests = [
        req
        for req in client.requests
        if DIM_KEYS["governing_codes"] in user_text(req["messages"])
    ]
    assert len(requests) == 2
    resumed = requests[1]["messages"][1]
    assert resumed["role"] == "assistant"
    assert resumed["content"] == paused.content
    assert any(
        getattr(block, "type", "") == "server_tool_use"
        for block in resumed["content"]
    )


def test_a_dimension_without_any_container_is_unaffected():
    """The normal direct-caller path: no container key on any request."""
    client = SequencedFakeClient(
        _scripts(
            governing_codes=[
                pause_response(searched_urls=["https://a.gov/one"]),
                research_response(items=[], searched_urls=["https://a.gov/one"]),
            ]
        )
    )
    _run(client)
    assert all("container" not in req for req in client.requests)


def _dimension_events(events: list[dict], dimension_id: str) -> list[dict]:
    return [e for e in events if e.get("dimension_id") == dimension_id]


def test_workers_emit_live_progress_events():
    """Workers narrate as they go: started → activity/search/fetch → the
    coordinator's terminal event, per dimension. Events interleave freely
    ACROSS dimensions (four threads), so assertions here are per-dimension
    subsequences, never global order."""
    events: list[dict] = []
    client = SequencedFakeClient(
        _scripts(
            governing_codes=[
                research_response(
                    items=[_item("VCC 2021 governs.", ["https://a.gov"])],
                    queries=["ashburn virginia fire code adoption"],
                    searched_urls=["https://a.gov"],
                    extra_blocks=fetch_blocks("https://a.gov/doc"),
                )
            ]
        )
    )
    run_requirements_research(
        DEFAULT_MODULE,
        PROFILE,
        client,
        model="claude-sonnet-5",
        max_tokens=4096,
        event_sink=events.append,
    )

    # The roster event still leads, and now names every dimension.
    assert events[0]["type"] == "research_started"
    assert events[0]["dimension_titles"] == {
        d.dimension_id: d.title for d in DEFAULT_MODULE.research_dimensions
    }

    # Every dimension announced itself with its budgets.
    started = [e for e in events if e["type"] == "dimension_started"]
    assert {e["dimension_id"] for e in started} == set(DIM_KEYS)
    assert all(
        e["title"] and e["max_searches"] > 0 and e["max_fetches"] > 0
        for e in started
    )

    # Every worker event carries its dimension id.
    worker_types = {
        "dimension_started",
        "dimension_activity",
        "dimension_search",
        "dimension_fetch",
        "dimension_retry",
    }
    assert all(
        e.get("dimension_id") for e in events if e["type"] in worker_types
    )

    # governing_codes: started first, then the exact live query and URL,
    # all before its terminal event.
    gov = _dimension_events(events, "governing_codes")
    types = [e["type"] for e in gov]
    assert types[0] == "dimension_started"
    assert types[-1] == "dimension_complete"
    search = next(e for e in gov if e["type"] == "dimension_search")
    assert search["query"] == "ashburn virginia fire code adoption"
    fetch = next(e for e in gov if e["type"] == "dimension_fetch")
    assert fetch["url"] == "https://a.gov/doc"
    kinds = [e["kind"] for e in gov if e["type"] == "dimension_activity"]
    assert kinds == ["searching", "fetching", "writing"]


def test_activity_is_emitted_on_change_only():
    """Two consecutive searches announce ``searching`` once — the activity
    stream reports phase changes, not every block."""
    events: list[dict] = []
    client = SequencedFakeClient(
        _scripts(
            governing_codes=[
                research_response(
                    items=[_item("Rule.", ["https://a.gov"])],
                    queries=["first query", "second query"],
                    searched_urls=["https://a.gov"],
                )
            ]
        )
    )
    run_requirements_research(
        DEFAULT_MODULE,
        PROFILE,
        client,
        model="claude-sonnet-5",
        max_tokens=4096,
        event_sink=events.append,
    )
    gov = _dimension_events(events, "governing_codes")
    kinds = [e["kind"] for e in gov if e["type"] == "dimension_activity"]
    assert kinds == ["searching", "writing"]
    queries = [e["query"] for e in gov if e["type"] == "dimension_search"]
    assert queries == ["first query", "second query"]


def test_retry_emits_dimension_retry_event(monkeypatch):
    import backend.research.engine as engine

    monkeypatch.setattr(engine.time, "sleep", lambda _s: None)
    import anthropic
    import httpx

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    retryable = anthropic.APIConnectionError(message="reset", request=request)
    events: list[dict] = []
    client = SequencedFakeClient(
        _scripts(
            governing_codes=[
                retryable,
                research_response(
                    items=[_item("Recovered.", ["https://a.gov"])],
                    searched_urls=["https://a.gov"],
                ),
            ]
        )
    )
    run_requirements_research(
        DEFAULT_MODULE,
        PROFILE,
        client,
        model="claude-sonnet-5",
        max_tokens=4096,
        event_sink=events.append,
    )
    gov = _dimension_events(events, "governing_codes")
    retries = [e for e in gov if e["type"] == "dimension_retry"]
    assert retries == [
        {
            "type": "dimension_retry",
            "dimension_id": "governing_codes",
            "attempt": 1,
            "max_attempts": 3,
            "reason": "connection",
            "backoff_s": 5.0,
        }
    ]
    # The retry precedes the dimension's recovery.
    types = [e["type"] for e in gov]
    assert types.index("dimension_retry") < types.index("dimension_complete")
    assert next(
        e for e in gov if e["type"] == "dimension_complete"
    )["item_count"] == 1


def test_malformed_stream_frames_never_fail_a_dimension():
    """Garbage raw-stream frames are skipped by the relay — the dimension
    still completes from the final message, and a garbled search input
    yields no blank-query event."""
    from types import SimpleNamespace

    response = research_response(
        items=[_item("Survives.", ["https://a.gov"])],
        searched_urls=["https://a.gov"],
    )
    response.events = [
        # A block start with no content_block at all.
        SimpleNamespace(type="content_block_start", index=0, content_block=None),
        # A delta with no delta payload.
        SimpleNamespace(type="content_block_delta", index=0, delta=None),
        # A search block whose input JSON never parses → no blank chip.
        SimpleNamespace(
            type="content_block_start",
            index=1,
            content_block=SimpleNamespace(type="server_tool_use", name="web_search"),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=1,
            delta=SimpleNamespace(type="input_json_delta", partial_json="{not json"),
        ),
        SimpleNamespace(type="content_block_stop", index=1),
        # An unknown frame type entirely.
        SimpleNamespace(type="mystery_frame"),
        *_synthesize_events(response.content, []),
    ]
    events: list[dict] = []
    client = SequencedFakeClient(_scripts(governing_codes=[response]))
    profile = run_requirements_research(
        DEFAULT_MODULE,
        PROFILE,
        client,
        model="claude-sonnet-5",
        max_tokens=4096,
        event_sink=events.append,
    )
    status = next(
        s for s in profile.dimension_statuses if s.dimension_id == "governing_codes"
    )
    assert status.status == "completed"
    assert any(i.requirement == "Survives." for i in profile.items)
    # No emission from the garbage: every search event has a real query.
    assert all(
        e["query"] for e in events if e["type"] == "dimension_search"
    )


def test_start_input_only_frames_still_emit_the_real_query_and_url():
    """A server tool whose input arrives complete on the START frame — the
    code-execution caller's shape, which streams no ``input_json_delta`` at
    all — still names its query and URL on the agent board.

    Direct callers (``allowed_callers: ["direct"]``, Chunk 1.1) make this
    shape unexpected, not impossible: this is the fallback that keeps the
    labels real for any future code-execution-called tool or provider-side
    shape drift.
    """
    response = research_response(
        items=[_item("Recorded.", ["https://a.gov"])],
        searched_urls=["https://a.gov"],
    )
    response.events = [
        *code_execution_tool_events(
            0, "web_search", {"query": "virginia adopted fire code edition"}
        ),
        *code_execution_tool_events(
            1, "web_fetch", {"url": "https://a.gov/adoption.pdf"}
        ),
        *_synthesize_events(response.content, []),
    ]
    events: list[dict] = []
    client = SequencedFakeClient(_scripts(governing_codes=[response]))
    profile = run_requirements_research(
        DEFAULT_MODULE,
        PROFILE,
        client,
        model="claude-sonnet-5",
        max_tokens=4096,
        event_sink=events.append,
    )

    gov = _dimension_events(events, "governing_codes")
    assert [e for e in gov if e["type"] == "dimension_search"] == [
        {
            "type": "dimension_search",
            "dimension_id": "governing_codes",
            "query": "virginia adopted fire code edition",
        }
    ]
    assert [e for e in gov if e["type"] == "dimension_fetch"] == [
        {
            "type": "dimension_fetch",
            "dimension_id": "governing_codes",
            "url": "https://a.gov/adoption.pdf",
        }
    ]
    kinds = [e["kind"] for e in gov if e["type"] == "dimension_activity"]
    assert kinds == ["searching", "fetching", "writing"]
    # The run itself is untouched — this chunk changes labels, not findings.
    assert any(i.requirement == "Recorded." for i in profile.items)


def test_streamed_deltas_win_and_an_absent_input_says_nothing():
    """Precedence and absence. Deltas are the live truth when a stream
    supplies both shapes; a block with neither emits no chip at all; a
    start input that is not a mapping is garbage, not a crash."""
    from types import SimpleNamespace

    response = research_response(
        items=[_item("Still fine.", ["https://a.gov"])],
        searched_urls=["https://a.gov"],
    )
    response.events = [
        # Both shapes on one block: the streamed deltas win.
        block_start_event(
            0, "server_tool_use", "web_search", input={"query": "start copy"}
        ),
        input_json_delta_event(0, '{"query": "streamed delta"}'),
        block_stop_event(0),
        # Neither shape: research skips a genuinely missing URL.
        block_start_event(1, "server_tool_use", "web_fetch"),
        block_stop_event(1),
        # A start input that is not a mapping at all.
        SimpleNamespace(
            type="content_block_start",
            index=2,
            content_block=SimpleNamespace(
                type="server_tool_use", name="web_search", input="not-a-mapping"
            ),
        ),
        block_stop_event(2),
        *_synthesize_events(response.content, []),
    ]
    events: list[dict] = []
    client = SequencedFakeClient(_scripts(governing_codes=[response]))
    profile = run_requirements_research(
        DEFAULT_MODULE,
        PROFILE,
        client,
        model="claude-sonnet-5",
        max_tokens=4096,
        event_sink=events.append,
    )

    gov = _dimension_events(events, "governing_codes")
    assert [e["query"] for e in gov if e["type"] == "dimension_search"] == [
        "streamed delta"
    ]
    assert not [e for e in gov if e["type"] == "dimension_fetch"]
    # All three blocks announced their activity — the non-mapping input was
    # rejected by the reader, not by the frame's try/except (which would
    # have swallowed the start frame whole and lost the third "searching").
    kinds = [e["kind"] for e in gov if e["type"] == "dimension_activity"]
    assert kinds == ["searching", "fetching", "searching", "writing"]
    status = next(
        s for s in profile.dimension_statuses if s.dimension_id == "governing_codes"
    )
    assert status.status == "completed"


def test_a_finished_block_is_forgotten_at_stop():
    """Every tracking dict drops the index at ``content_block_stop``, so a
    long stream never retains completed payloads. Observable proof: a second
    stop for the same index has nothing left to report."""
    response = research_response(
        items=[_item("Once.", ["https://a.gov"])],
        searched_urls=["https://a.gov"],
    )
    response.events = [
        *code_execution_tool_events(0, "web_search", {"query": "only once"}),
        block_stop_event(0),  # a repeat stop for a block already retired
        *_synthesize_events(response.content, []),
    ]
    events: list[dict] = []
    client = SequencedFakeClient(_scripts(governing_codes=[response]))
    run_requirements_research(
        DEFAULT_MODULE,
        PROFILE,
        client,
        model="claude-sonnet-5",
        max_tokens=4096,
        event_sink=events.append,
    )
    gov = _dimension_events(events, "governing_codes")
    assert [e["query"] for e in gov if e["type"] == "dimension_search"] == ["only once"]


def test_retry_success_counts_billed_usage_from_abandoned_attempt(monkeypatch):
    """A response streamed before a retryable failure is billed spend — the
    successful DimensionStatus must include it, or the cost meter
    under-reports (WI4)."""
    import backend.research.engine as engine

    monkeypatch.setattr(engine.time, "sleep", lambda _s: None)
    import anthropic
    import httpx

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    retryable = anthropic.APIConnectionError(message="reset", request=request)
    client = SequencedFakeClient(
        _scripts(
            governing_codes=[
                # Attempt 1: streams a billed response (200 in), then pauses…
                research_response(
                    searched_urls=["https://a.gov"],
                    stop_reason="pause_turn",
                    tokens={"input": 200},
                ),
                # …and the continuation dies with a retryable error.
                retryable,
                # Attempt 2 succeeds (100 in).
                research_response(
                    items=[_item("Recovered.", ["https://b.gov"])],
                    searched_urls=["https://b.gov"],
                    tokens={"input": 100},
                ),
            ]
        )
    )
    profile = _run(client)
    status = next(
        s for s in profile.dimension_statuses if s.dimension_id == "governing_codes"
    )
    assert status.status == "completed"
    # 200 (abandoned but billed) + 100 (successful attempt) — not just 100.
    assert status.input_tokens == 300
    assert profile.usage_total()["input_tokens"] == 300


# ---------------------------------------------------------------------------
# A round that produced nothing still produced a bill
# ---------------------------------------------------------------------------


def test_a_round_where_every_dimension_failed_still_reports_its_bill():
    """Nothing was adopted, but four dimensions' requests were still paid
    for. The raised error is the only thing that carries that spend out of
    the fan-out — without it the session meter never hears about it."""
    client = SequencedFakeClient(
        {
            DIM_KEYS["governing_codes"]: [
                research_response(
                    items=[],
                    stop_reason="max_tokens",
                    searched_urls=["https://a.gov"],
                    tokens={"input": 100, "output": 10},
                )
            ],
            DIM_KEYS["ahj_requirements"]: [
                research_response(
                    items=[],
                    stop_reason="max_tokens",
                    searched_urls=["https://b.gov", "https://c.gov"],
                    tokens={"input": 200, "output": 20, "cache_read": 7},
                )
            ],
            DIM_KEYS["client_standards"]: [
                research_response(
                    items=[],
                    stop_reason="max_tokens",
                    tokens={"input": 300, "output": 30, "cache_write": 11},
                )
            ],
            DIM_KEYS["site_environment"]: [
                research_response(
                    items=[],
                    stop_reason="max_tokens",
                    fetches=2,
                    tokens={"input": 400, "output": 40},
                )
            ],
        }
    )
    with pytest.raises(ResearchFanoutError) as exc_info:
        _run(client)

    # Exact, not "nonzero": an aggregate that quietly drops a field is the
    # failure mode this carries.
    assert exc_info.value.usage_totals == {
        "input_tokens": 1000,
        "output_tokens": 100,
        "cache_read_input_tokens": 7,
        "cache_creation_input_tokens": 11,
        "web_search_requests": 3,
        "web_fetch_requests": 2,
    }


def test_the_failed_round_bill_counts_a_retried_attempt_exactly_once(monkeypatch):
    """A retryable death abandons its attempt's conversation, not its bill.

    The success path already accounts for that (see
    ``test_retry_success_counts_billed_usage_from_abandoned_attempt``); the
    all-failed path has to agree, and must not double-count either.
    """
    import backend.research.engine as engine

    monkeypatch.setattr(engine.time, "sleep", lambda _s: None)
    import anthropic
    import httpx

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    retryable = anthropic.APIConnectionError(message="reset", request=request)
    # The other three fail immediately and bill nothing, so the assertion
    # below is purely about the retried dimension.
    scripts = {
        key: [research_response(items=[], stop_reason="max_tokens")]
        for key in DIM_KEYS.values()
    }
    scripts[DIM_KEYS["governing_codes"]] = [
        # Attempt 1: a billed pause…
        research_response(
            searched_urls=["https://a.gov"],
            stop_reason="pause_turn",
            tokens={"input": 200},
        ),
        # …whose continuation dies retryably.
        retryable,
        # Attempt 2: billed, and terminal.
        research_response(
            items=[], stop_reason="max_tokens", tokens={"input": 100}
        ),
    ]
    with pytest.raises(ResearchFanoutError) as exc_info:
        _run(client := SequencedFakeClient(scripts))
    assert client  # the fake was consumed

    # 200 (abandoned but billed) + 100 (the attempt that gave up) — once each.
    assert exc_info.value.usage_totals["input_tokens"] == 300


def test_a_failure_that_never_reached_the_provider_carries_no_bill():
    """The no-op case the runner's metering guard relies on: an empty
    ``usage_totals`` must stay empty rather than become a row of zeroes."""
    module = dataclasses.replace(DEFAULT_MODULE, research_dimensions=())
    with pytest.raises(ResearchFanoutError) as exc_info:
        run_requirements_research(
            module,
            PROFILE,
            SequencedFakeClient({}),
            model="claude-sonnet-5",
            max_tokens=4096,
        )
    assert exc_info.value.usage_totals == {}


def test_every_recorded_usage_field_reaches_the_meter():
    """The guard on the shared key tuple.

    A usage field added to ``DimensionStatus`` but not to
    ``_DIMENSION_USAGE_KEYS`` would be recorded per dimension and then
    billed by neither the profile total nor the failed-round bill.
    """
    import backend.research.engine as engine

    recorded = {
        f.name
        for f in dataclasses.fields(DimensionStatus)
        if f.name.endswith(("_tokens", "_requests"))
    }
    assert recorded == set(engine._DIMENSION_USAGE_KEYS)


def test_the_failed_bill_and_the_profile_total_are_the_same_computation():
    """Both callers go through one helper, so they cannot drift apart."""
    import backend.research.engine as engine

    statuses = [
        DimensionStatus(
            dimension_id="governing_codes",
            status="failed",
            input_tokens=1,
            output_tokens=2,
            cache_read_input_tokens=3,
            cache_creation_input_tokens=4,
            web_search_requests=5,
            web_fetch_requests=6,
        ),
        # A dimension that never issued a request contributes no keys.
        DimensionStatus(dimension_id="ahj_requirements", status="failed"),
    ]
    profile = RequirementsProfile(items=[], dimension_statuses=statuses)
    assert engine.dimension_usage_total(statuses) == profile.usage_total()
    assert engine.dimension_usage_total(statuses) == {
        "input_tokens": 1,
        "output_tokens": 2,
        "cache_read_input_tokens": 3,
        "cache_creation_input_tokens": 4,
        "web_search_requests": 5,
        "web_fetch_requests": 6,
    }


def test_render_text_is_deterministic_and_marks_unverified():
    profile = RequirementsProfile.from_dict(
        {
            "items": [
                {
                    "item_id": "r-aaa",
                    "dimension_id": "governing_codes",
                    "topic": "t",
                    "category": "governing_code",
                    "requirement": "Grounded fact.",
                    "accepted_sources": ["https://a.gov"],
                    "grounded": True,
                    "confidence": 0.9,
                    "actionability": "spec_requirement",
                },
                {
                    "item_id": "r-bbb",
                    "dimension_id": "governing_codes",
                    "topic": "t",
                    "category": "governing_code",
                    "requirement": "Ungrounded lead.",
                    "grounded": False,
                    "confidence": 0.4,
                    "actionability": "spec_requirement",
                },
                {
                    "item_id": "r-ccc",
                    "dimension_id": "ahj_requirements",
                    "topic": "t",
                    "category": "ahj_requirement",
                    "requirement": "Permit fee due.",
                    "accepted_sources": ["https://b.gov"],
                    "grounded": True,
                    "confidence": 0.8,
                    "actionability": "process_advisory",
                },
            ],
            "dimension_statuses": [
                {"dimension_id": "governing_codes", "status": "completed"},
                {"dimension_id": "ahj_requirements", "status": "completed"},
            ],
            "research_date": "2026-07-21",
            "project": PROFILE.to_dict(),
        }
    )
    text = profile.render_text()
    assert text == profile.render_text()
    assert "PROJECT REQUIREMENTS PROFILE" in text
    assert "Ashburn, Virginia, USA" in text
    assert "Sources: [UNVERIFIED]" in text
    assert "[PROCESS] Permit fee due." in text
    # Section grouping: governing before AHJ.
    assert text.index("GOVERNING CODES & AMENDMENTS") < text.index(
        "AHJ REQUIREMENTS"
    )
    # Round-trip.
    again = RequirementsProfile.from_dict(profile.to_dict())
    assert again.render_text() == text


def test_research_context_block_trims_lowest_confidence_first():
    items = [
        {
            "item_id": f"r-{i:03d}",
            "dimension_id": "governing_codes",
            "topic": "t",
            "category": "governing_code",
            "requirement": f"Fact {i} " + ("x" * 200),
            "grounded": True,
            "confidence": 0.1 + i * 0.05,
            "actionability": "spec_requirement",
        }
        for i in range(10)
    ]
    profile = RequirementsProfile.from_dict(
        {
            "items": items,
            "dimension_statuses": [
                {"dimension_id": "governing_codes", "status": "completed"}
            ],
            "research_date": "2026-07-21",
            "project": PROFILE.to_dict(),
        }
    )
    full, dropped_none = research_context_block(profile, max_tokens=100_000)
    assert dropped_none == 0 and "Fact 0" in full

    trimmed, dropped = research_context_block(profile, max_tokens=300)
    assert dropped > 0
    # Lowest-confidence items (the early ones) dropped first.
    assert "Fact 0 " not in trimmed
    assert "Fact 9 " in trimmed
    # Structured profile untouched.
    assert len(profile.items) == 10


# ---------------------------------------------------------------------------
# Incomplete coverage is named, not just counted (Chunk 3.1)
# ---------------------------------------------------------------------------


def _profile_with(statuses: list[tuple[str, str, str]]) -> RequirementsProfile:
    """A profile carrying only dimension statuses: (id, status, title)."""
    return RequirementsProfile(
        items=[],
        dimension_statuses=[
            DimensionStatus(dimension_id=did, status=status, title=title)
            for did, status, title in statuses
        ],
        research_date="2026-07-21",
        project=PROFILE.to_dict(),
    )


def test_a_fully_complete_profile_renders_exactly_as_before():
    profile = _profile_with(
        [
            ("governing_codes", "completed", "Governing codes"),
            ("ahj_requirements", "completed", "AHJ requirements"),
        ]
    )
    text = profile.render_text()
    assert "INCOMPLETE COVERAGE" not in text
    # The header is byte-identical to the pre-3.1 rendering: provenance line
    # followed immediately by the two marker legends, nothing spliced between.
    assert (
        "Generated by location/client research (2 of 2 dimensions completed), "
        "researched 2026-07-21. Edition and process facts are as-of that "
        "date.\nItems marked [UNVERIFIED] could not be grounded in retrieved "
        "sources.\nItems marked [PROCESS] are project-team process/schedule "
        "advisories, not specification content."
    ) in text


def test_one_never_completed_dimension_is_named_as_absent_not_empty():
    profile = _profile_with(
        [
            ("governing_codes", "completed", "Governing codes"),
            ("site_environment", "failed", "Site and environment"),
        ]
    )
    text = profile.render_text()
    assert "INCOMPLETE COVERAGE: research for Site and environment never" in text
    # The semantic point: absent, not verified-empty. A count alone ("1 of 2")
    # cannot tell the model that nobody looked.
    assert "ABSENT, not verified-empty" in text
    assert "this area" in text and "these areas" not in text
    assert "Governing codes" not in text.split("INCOMPLETE COVERAGE")[1]


def test_several_never_completed_dimensions_are_all_named_in_module_order():
    profile = _profile_with(
        [
            ("governing_codes", "completed", "Governing codes"),
            ("ahj_requirements", "failed", "AHJ requirements"),
            ("client_standards", "failed", "Client and insurer standards"),
        ]
    )
    warning = profile.render_text().split("INCOMPLETE COVERAGE: ")[1]
    assert warning.startswith(
        "research for AHJ requirements, Client and insurer standards never "
        "completed."
    )
    assert "these areas" in warning


def test_a_dimension_with_no_recorded_title_falls_back_to_its_id():
    # Legacy profiles saved before titles were stored would otherwise name
    # nothing at all.
    profile = _profile_with([("ahj_requirements", "failed", "")])
    assert "research for ahj_requirements never completed" in profile.render_text()


def test_the_raw_provider_error_never_reaches_the_drafting_context():
    profile = _profile_with([("ahj_requirements", "failed", "AHJ requirements")])
    profile.dimension_statuses[0].error = (
        "BadRequestError: {'type': 'error', 'message': 'container_id required'}"
    )
    text = profile.render_text()
    assert "BadRequestError" not in text and "container_id" not in text


def test_context_trimming_cannot_drop_the_incomplete_coverage_warning():
    """The warning is provenance/control text, not a low-confidence item.

    ``research_context_block`` fits the block under the cap by dropping whole
    ITEMS, so a profile whose findings are all trimmed away must still say
    that its coverage was partial — that is precisely the profile where
    over-trusting what remains does the most damage.
    """
    profile = RequirementsProfile(
        items=[
            ResearchItem(
                item_id=f"i-{n}",
                dimension_id="governing_codes",
                topic="topic",
                category="governing_code",
                requirement=f"Fact {n} " + "padding " * 40,
                confidence=0.1 + n / 100,
            )
            for n in range(12)
        ],
        dimension_statuses=[
            DimensionStatus("governing_codes", "completed", title="Governing codes"),
            DimensionStatus("ahj_requirements", "failed", title="AHJ requirements"),
        ],
        research_date="2026-07-21",
        project=PROFILE.to_dict(),
    )
    trimmed, dropped = research_context_block(profile, max_tokens=180)
    assert dropped > 0
    assert "INCOMPLETE COVERAGE: research for AHJ requirements" in trimmed


def test_a_failed_dimension_records_a_sanitized_kind_beside_its_message():
    """The kind is telemetry-safe; the message is not, and stays user-facing."""
    client = SequencedFakeClient(
        _scripts(
            governing_codes=[research_response(items=[], stop_reason="max_tokens")],
            client_standards=[research_response(items=None)],
            ahj_requirements=[RuntimeError("kaput")],
        )
    )
    profile = _run(client)
    kinds = {s.dimension_id: s.error_kind for s in profile.dimension_statuses}
    assert kinds["governing_codes"] == "incomplete_response"
    assert kinds["client_standards"] == "no_payload"
    # A raised provider error reports its failure CLASS, never its text.
    assert kinds["ahj_requirements"] == "unknown"
    assert kinds["site_environment"] == ""
    for status in profile.dimension_statuses:
        assert status.error_kind in ("", *DIMENSION_ERROR_KINDS)
        assert "kaput" not in status.error_kind


def test_incomplete_facts_are_telemetry_safe_and_in_module_order():
    profile = _profile_with(
        [
            ("governing_codes", "completed", "Governing codes"),
            ("ahj_requirements", "failed", "AHJ requirements"),
            ("site_environment", "failed", ""),
        ]
    )
    profile.dimension_statuses[1].error = "RateLimitError: retry later"
    profile.dimension_statuses[1].error_kind = "rate_limit"
    assert incomplete_dimension_facts(profile) == [
        {
            "dimension_id": "ahj_requirements",
            "title": "AHJ requirements",
            "error_kind": "rate_limit",
        },
        {
            "dimension_id": "site_environment",
            "title": "site_environment",
            "error_kind": "",
        },
    ]
    assert incomplete_dimension_facts(None) == []


def test_a_project_file_cannot_smuggle_text_into_the_telemetry_kind():
    """`.baspec` files are shared between people and the loader is permissive.

    So the closed vocabulary has to be enforced, not just documented: without
    this, arbitrary text from a project file rode the facts projection into
    /api/diagnostics and a support bundle — the exact payload the vocabulary
    exists to keep out. Raised in review on PR #96.
    """
    hostile = "BadRequestError: {'message': 'container_id required', 'key': 'sk-x'}"
    restored = RequirementsProfile.from_dict(
        {
            "items": [],
            "dimension_statuses": [
                {
                    "dimension_id": "ahj_requirements",
                    "status": "failed",
                    "title": "AHJ requirements",
                    "error": hostile,
                    "error_kind": hostile,
                }
            ],
            "research_date": "2026-07-21",
            "project": PROFILE.to_dict(),
        }
    )
    assert restored is not None
    # Permissive load — the profile still opens, and the user-facing message
    # keeps the detail it is allowed to keep.
    assert restored.dimension_statuses[0].error == hostile
    # The kind does not: flagged as unrecognized rather than echoed.
    assert restored.dimension_statuses[0].error_kind == "unrecognized"
    facts = incomplete_dimension_facts(restored)
    assert facts[0]["error_kind"] == "unrecognized"
    assert hostile not in str(facts)

    # And the guarantee holds at the PROJECTION, not only at load: a status
    # constructed directly with a bad kind is sanitized too, so a future code
    # path that bypasses the deserializer cannot reopen this.
    forged = RequirementsProfile(
        items=[],
        dimension_statuses=[
            DimensionStatus(
                "ahj_requirements", "failed", title="AHJ", error_kind=hostile
            )
        ],
        research_date="2026-07-21",
    )
    assert incomplete_dimension_facts(forged)[0]["error_kind"] == "unrecognized"


def test_sanitizing_a_kind_keeps_every_real_one_and_rejects_the_rest():
    for kind in DIMENSION_ERROR_KINDS:
        assert sanitized_error_kind(kind) == kind
    # Absence stays absence — it means "a success, or a pre-3.1 file", which
    # is not the same claim as "the file said something we do not know".
    assert sanitized_error_kind("") == ""
    assert sanitized_error_kind(None) == ""
    assert sanitized_error_kind(["rate_limit"]) == ""
    assert sanitized_error_kind("RATE_LIMIT") == "unrecognized"


# ---------------------------------------------------------------------------
# Scoped rounds (A) — a later round need not re-run the areas that are done
# ---------------------------------------------------------------------------


def _dimension_message(client, dimension_id: str) -> str:
    """The user brief one dimension was actually sent, or "" if it never ran."""
    key = DIM_KEYS[dimension_id]
    for request in client.requests:
        text = user_text(request.get("messages", []))
        if key in text:
            return text
    return ""


def _ran_dimensions(client) -> set[str]:
    return {
        dim_id
        for dim_id in DIM_KEYS
        if _dimension_message(client, dim_id)
    }


def test_a_scoped_round_runs_only_the_named_dimensions():
    client = SequencedFakeClient(_scripts())
    events: list[dict] = []
    profile = run_requirements_research(
        DEFAULT_MODULE,
        PROFILE,
        client,
        model="claude-sonnet-5",
        max_tokens=4096,
        dimension_ids=["ahj_requirements", "site_environment"],
        event_sink=events.append,
    )

    # The settled areas were never asked again — which is the entire point:
    # a full round pays four search budgets to re-answer two.
    assert _ran_dimensions(client) == {"ahj_requirements", "site_environment"}
    assert [s.dimension_id for s in profile.dimension_statuses] == [
        "ahj_requirements",
        "site_environment",
    ]
    roster = next(e for e in events if e["type"] == "research_started")
    assert roster["dimensions"] == ["ahj_requirements", "site_environment"]
    assert set(roster["dimension_titles"]) == {
        "ahj_requirements",
        "site_environment",
    }
    # The live board seeds from the roster, so it must show two agents and
    # say what it is two OF — never four cards, half of which never start.
    assert roster["declared_dimension_count"] == 4


def test_scope_order_comes_from_the_module_not_the_caller():
    client = SequencedFakeClient(_scripts())
    profile = run_requirements_research(
        DEFAULT_MODULE,
        PROFILE,
        client,
        model="claude-sonnet-5",
        max_tokens=4096,
        # Caller order reversed, and one id repeated.
        dimension_ids=["site_environment", "governing_codes", "site_environment"],
    )
    assert [s.dimension_id for s in profile.dimension_statuses] == [
        "governing_codes",
        "site_environment",
    ]


def test_an_unknown_scope_id_is_ignored_and_an_empty_scope_refuses():
    client = SequencedFakeClient(_scripts())
    profile = run_requirements_research(
        DEFAULT_MODULE,
        PROFILE,
        client,
        model="claude-sonnet-5",
        max_tokens=4096,
        dimension_ids=["governing_codes", "not_a_dimension"],
    )
    # Filtered against what the module declares — never fabricated, because a
    # dimension with no brief has nothing to research.
    assert [s.dimension_id for s in profile.dimension_statuses] == [
        "governing_codes"
    ]

    with pytest.raises(ResearchFanoutError) as excinfo:
        run_requirements_research(
            DEFAULT_MODULE,
            PROFILE,
            SequencedFakeClient(_scripts()),
            model="claude-sonnet-5",
            max_tokens=4096,
            dimension_ids=["not_a_dimension"],
        )
    assert "matched the requested scope" in str(excinfo.value)


def test_an_unscoped_round_still_runs_every_declared_dimension():
    client = SequencedFakeClient(_scripts())
    profile = _run(client)
    assert _ran_dimensions(client) == set(DIM_KEYS)
    assert len(profile.dimension_statuses) == 4


# ---------------------------------------------------------------------------
# Established facts (B) — a later round is told what it need not re-derive
# ---------------------------------------------------------------------------


def _established_profile() -> RequirementsProfile:
    return RequirementsProfile(
        items=[
            ResearchItem(
                item_id="r-aaa",
                dimension_id="governing_codes",
                topic="code",
                category="governing_code",
                requirement="VCC 2021 governs the work.",
                authority="Virginia DHCD",
                code_reference="VCC 101",
                accepted_sources=["https://dhcd.virginia.gov/vcc"],
                grounded=True,
                confidence=0.9,
                research_date="2026-07-01",
            ),
            ResearchItem(
                item_id="r-bbb",
                dimension_id="governing_codes",
                topic="code",
                category="governing_code",
                requirement="A local amendment may apply.",
                grounded=False,
                confidence=0.3,
                research_date="2026-07-01",
            ),
            ResearchItem(
                item_id="r-ccc",
                dimension_id="site_environment",
                topic="site",
                category="site_environment",
                requirement="Seismic design category B.",
                grounded=True,
                confidence=0.8,
                research_date="2026-07-01",
            ),
        ],
        dimension_statuses=[
            DimensionStatus("governing_codes", "completed", title="Codes"),
            DimensionStatus("site_environment", "completed", title="Site"),
        ],
        research_date="2026-07-01",
        # Which project these findings belong to. Without it the brief is
        # withheld — see established_facts_for.
        project=PROFILE.to_dict(),
    )


def test_a_first_round_brief_is_byte_identical_to_what_it_always_was():
    # No profile threaded, and no items for the dimension: both render "",
    # so the message the engine sends is unchanged from before this work.
    assert established_facts_block(None, "governing_codes") == ""
    assert (
        established_facts_block(_established_profile(), "client_standards") == ""
    )

    dimension = DEFAULT_MODULE.research_dimensions[0]
    assert build_dimension_user_message(
        DEFAULT_MODULE, PROFILE, dimension, established_facts=""
    ) == build_dimension_user_message(DEFAULT_MODULE, PROFILE, dimension)


def test_established_facts_reach_only_their_own_dimension():
    client = SequencedFakeClient(_scripts())
    run_requirements_research(
        DEFAULT_MODULE,
        PROFILE,
        client,
        model="claude-sonnet-5",
        max_tokens=4096,
        established=_established_profile(),
    )

    codes = _dimension_message(client, "governing_codes")
    assert "VCC 2021 governs the work." in codes
    # Another dimension's finding is noise in a brief this narrow — and a
    # dimension independently corroborating it is a feature, since the merge
    # confirms such an item in place rather than duplicating it.
    assert "Seismic design category B." not in codes

    site = _dimension_message(client, "site_environment")
    assert "Seismic design category B." in site
    assert "VCC 2021 governs the work." not in site

    # A dimension with nothing established is briefed exactly as a first
    # round would brief it.
    assert "<already_established>" not in _dimension_message(
        client, "client_standards"
    )


def test_the_established_block_marks_unverified_and_asks_for_corrections():
    block = established_facts_block(_established_profile(), "governing_codes")

    # Grounded facts are stated plainly; the ungrounded one is flagged,
    # because re-verifying it is the one thing the directive asks for.
    assert "- VCC 2021 governs the work." in block
    assert "Authority: Virginia DHCD" in block and "Ref: VCC 101" in block
    assert "[UNVERIFIED]" in block
    assert block.index("VCC 2021") < block.index("A local amendment")

    # The instruction is the load-bearing half: without it the block is just
    # more context to confidently re-derive.
    assert "Do not re-derive them." in block
    assert "NEW, CHANGED, or CORRECTED" in block
    assert "superseded" in block


def test_established_facts_are_trimmed_under_the_cap_and_say_so():
    # Half the cap each, so the first lands whole and the rest cannot —
    # exercising the trim path rather than the oversized-first-item one.
    filler = "x" * (ESTABLISHED_FACTS_MAX_TOKENS * 2)
    profile = RequirementsProfile(
        items=[
            ResearchItem(
                item_id=f"r-{n}",
                dimension_id="governing_codes",
                topic="t",
                category="governing_code",
                requirement=f"{filler} {n}",
                grounded=True,
                confidence=0.9 - n / 100,
            )
            for n in range(3)
        ],
        dimension_statuses=[
            DimensionStatus("governing_codes", "completed", title="Codes")
        ],
        research_date="2026-07-01",
    )
    block = established_facts_block(profile, "governing_codes")

    # Highest-confidence first, so the tail is what goes — the established,
    # well-evidenced facts re-deriving would waste the most money on stay.
    assert f"{filler} 0" in block
    assert f"{filler} 2" not in block
    # Disclosed, never silent: an omitted fact is one this round may go and
    # re-derive, which is the thing the block exists to prevent.
    assert "2 further established item(s) omitted" in block
    assert "partial, not exhaustive" in block


def test_a_corrected_project_profile_is_never_briefed_on_the_old_project():
    """Editing the profile must not have the next round told to skip work.

    The block asserts its facts as established and tells the worker not to
    re-derive them — so briefing the old project's findings would make a
    full re-run commissioned BECAUSE the project changed skip the very
    requirements it exists to find.
    """
    established = _established_profile()
    established.project = PROFILE.to_dict()

    # Same project: briefed, as usual.
    assert established_facts_for(established, PROFILE) is established

    # Any field differing is a different project — including the client,
    # which is what the client/insurer-standards dimension researches.
    for changed in (
        ProjectProfile("Reno", "NV", "USA", "ExampleCo"),
        ProjectProfile("Ashburn", "VA", "USA", "OtherCo"),
        ProjectProfile("Ashburn", "ON", "CA", "ExampleCo"),
    ):
        assert established_facts_for(established, changed) is None

    # Fail closed: a profile that does not record what it researched (a
    # legacy or hand-edited file) is not briefed either. The cost of being
    # wrong is one full round — exactly what every round used to cost.
    established.project = None
    assert established_facts_for(established, PROFILE) is None
    established.project = {"city": "Ashburn"}
    assert established_facts_for(established, PROFILE) is None
    assert established_facts_for(None, PROFILE) is None


def test_a_changed_project_sends_a_brief_with_no_established_facts():
    established = _established_profile()
    established.project = ProjectProfile("Reno", "NV", "USA", "OtherCo").to_dict()

    client = SequencedFakeClient(_scripts())
    run_requirements_research(
        DEFAULT_MODULE,
        PROFILE,
        client,
        model="claude-sonnet-5",
        max_tokens=4096,
        established=established,
    )
    for dim_id in DIM_KEYS:
        assert "<already_established>" not in _dimension_message(client, dim_id)


def test_one_oversized_fact_is_truncated_rather_than_blowing_the_cap():
    """A `.baspec` is a shared file and `requirement` is unbounded on load.

    The first fact always lands, but it lands truncated — otherwise a single
    hand-edited item carries the block past the model's context limit and
    fails the dimension outright, trading a reliability regression for a
    cost saving.
    """
    huge = "y" * (ESTABLISHED_FACTS_MAX_TOKENS * 40)
    profile = RequirementsProfile(
        items=[
            ResearchItem(
                item_id="r-huge",
                dimension_id="governing_codes",
                topic="t",
                category="governing_code",
                requirement=huge,
                grounded=True,
                confidence=0.9,
            )
        ],
        dimension_statuses=[
            DimensionStatus("governing_codes", "completed", title="Codes")
        ],
        research_date="2026-07-01",
    )
    block = established_facts_block(profile, "governing_codes")

    assert len(block) < len(huge)
    assert _estimate_tokens(block) < ESTABLISHED_FACTS_MAX_TOKENS * 2
    # Truncation is disclosed: a requirement that stops mid-sentence would
    # otherwise read as the whole of it.
    assert "[truncated for length]" in block
