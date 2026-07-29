"""Research engine tests: fan-out policy, grounding, continuations, caps —
hermetic against the sequenced fake client."""
from __future__ import annotations

import pytest

from backend.project_profile import ProjectProfile
from backend.research import (
    RequirementsProfile,
    ResearchFanoutError,
    research_context_block,
    run_requirements_research,
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
    fetch_blocks,
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
        if DIM_KEYS["governing_codes"] in req["messages"][0]["content"]
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
        if DIM_KEYS["governing_codes"] in req["messages"][0]["content"]
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
        if DIM_KEYS["governing_codes"] in req["messages"][0]["content"]
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

    from tests.fakes import _synthesize_events

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
