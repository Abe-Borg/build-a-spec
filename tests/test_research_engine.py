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
# DIM_KEYS route scripted turns by hyperscale_fire dimension message substrings,
# so bind the fire module explicitly (the registry default is now generic, whose
# dimension messages are discipline-parameterized and would not match).
from backend.spec_modules.hyperscale_fire import HYPERSCALE_FIRE as DEFAULT_MODULE
from tests.fakes import (
    SequencedFakeClient,
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
