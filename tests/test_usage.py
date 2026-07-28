"""WI4: the session-scoped usage ledger + /api/usage cost meter."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from backend.app import create_app
from backend import sessions
from backend.usage_ledger import UsageLedger
from tests.fakes import (
    FakeClient,
    SequencedFakeClient,
    research_response,
    text_turn,
    token_usage,
    tool_turn,
)
from tests.test_research_engine import _item, _scripts


def _client() -> TestClient:
    return TestClient(create_app())


def _parse_sse(body: str) -> list[dict]:
    return [
        json.loads(line[len("data: "):])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


# ---------------------------------------------------------------------------
# Ledger units
# ---------------------------------------------------------------------------


def test_ledger_accumulates_and_totals():
    ledger = UsageLedger()
    ledger.add("interview", {"input_tokens": 100, "output_tokens": 50}, count_turn=True)
    ledger.add("interview", {"input_tokens": 30}, count_turn=True)
    ledger.add("research", {"input_tokens": 10, "web_search_requests": 4})
    assert ledger.turns == 2
    assert ledger.categories["interview"] == {"input_tokens": 130, "output_tokens": 50}
    totals = ledger.totals() if hasattr(ledger, "totals") else ledger._totals()
    assert totals["input_tokens"] == 140
    assert totals["web_search_requests"] == 4


def test_empty_usage_is_a_noop_and_does_not_count_a_turn():
    ledger = UsageLedger()
    ledger.add("interview", {}, count_turn=True)
    ledger.add("interview", None, count_turn=True)
    assert ledger.turns == 0
    assert ledger.categories == {}


def test_estimate_math_is_golden_against_pricing():
    # Sonnet 5 (interview): $3/M in, $15/M out, $0.30/M cache-read; web $0.01.
    ledger = UsageLedger()
    ledger.add(
        "interview",
        {
            "input_tokens": 1_000_000,
            "output_tokens": 200_000,
            "cache_read_input_tokens": 1_000_000,
            "web_search_requests": 5,
        },
    )
    snap = ledger.snapshot()
    # 3.0 + 3.0 + 0.3 + 0.05 = 6.35
    assert snap["estimated_cost_usd"]["by_category"]["interview"] == 6.35
    assert snap["estimated_cost_usd"]["total"] == 6.35
    # Cache saved = 1e6 * (3e-6 - 0.30e-6) = 2.7
    assert snap["cache_saved_usd"] == 2.7


def test_ledger_reset_clears():
    ledger = UsageLedger()
    ledger.add("interview", {"input_tokens": 100}, count_turn=True)
    ledger.reset()
    assert ledger.turns == 0
    assert ledger.snapshot()["categories"] == {}


# ---------------------------------------------------------------------------
# Wired through the API
# ---------------------------------------------------------------------------

_EDITS = {
    "edits": [
        {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"},
    ]
}


def test_interview_turn_adds_to_ledger(monkeypatch):
    client = _client()
    assert client.get("/api/usage").json()["totals"] == {}

    fake = FakeClient(
        [text_turn(["Hello."], usage=token_usage(input=1200, output=400, searches=2))]
    )
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)
    resp = client.post("/api/chat", json={"message": "hi"})
    # turn_complete carries the same usage the ledger accrued.
    (done,) = [e for e in _parse_sse(resp.text) if e["type"] == "turn_complete"]
    assert done["usage"]["input_tokens"] == 1200

    usage = client.get("/api/usage").json()
    assert usage["turns"] == 1
    assert usage["categories"]["interview"]["input_tokens"] == 1200
    assert usage["categories"]["interview"]["output_tokens"] == 400
    assert usage["categories"]["interview"]["web_search_requests"] == 2
    # Estimated: 1200*3e-6 + 400*15e-6 + 2*0.01 = 0.0036 + 0.006 + 0.02
    assert usage["estimated_cost_usd"]["total"] == round(0.0036 + 0.006 + 0.02, 6)


def test_failed_turn_still_adds_spend(monkeypatch):
    client = _client()
    fake = FakeClient(
        [
            tool_turn(["Working. "], _EDITS, usage=token_usage(input=800)),
            RuntimeError("kaput"),
        ]
    )
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)

    events = _parse_sse(client.post("/api/chat", json={"message": "go"}).text)
    assert events[-1]["type"] == "error"
    # The document rolled back, but the tokens were really spent.
    assert sessions.get_session().doc.doc.is_empty()
    usage = client.get("/api/usage").json()
    assert usage["categories"]["interview"]["input_tokens"] == 800


def test_reset_clears_the_meter(monkeypatch):
    client = _client()
    fake = FakeClient([text_turn(["Hi."], usage=token_usage(input=500))])
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)
    client.post("/api/chat", json={"message": "hi"})
    assert client.get("/api/usage").json()["totals"]["input_tokens"] == 500

    client.post("/api/session/reset")
    assert client.get("/api/usage").json()["totals"] == {}


def test_research_run_rolls_up_into_ledger(monkeypatch):
    import time as _time

    client = _client()
    # Research routes by the fire module's dimension messages (and requires a
    # curated module's discipline gate to pass); the neutral default is now
    # the generic module, so select fire first.
    client.post("/api/session/reset", json={"module_id": "hyperscale_fire"})
    # Record a complete profile via chat so research can start.
    profile_edits = {
        "edits": [
            {
                "action": "set_project_profile",
                "target_id": "sec",
                "city": "Ashburn",
                "state": "Virginia",
                "country": "USA",
                "client": "ExampleCo",
            }
        ]
    }
    chat_fake = FakeClient(
        [tool_turn(["Recorded."], profile_edits), text_turn(["Done."])]
    )
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: chat_fake)
    client.post("/api/chat", json={"message": "Ashburn VA, ExampleCo"})

    research_fake = SequencedFakeClient(
        _scripts(
            governing_codes=[
                research_response(
                    items=[_item("2021 VCC governs.", ["https://x.gov"])],
                    searched_urls=["https://x.gov"],
                    tokens={"input": 5000, "output": 1000},
                )
            ]
        )
    )
    monkeypatch.setattr("backend.app.get_client", lambda: research_fake)

    assert client.post("/api/research/start").json()["ok"] is True
    deadline = _time.monotonic() + 5.0
    while _time.monotonic() < deadline:
        if client.get("/api/research/status").json()["status"] == "complete":
            break
        _time.sleep(0.05)

    usage = client.get("/api/usage").json()
    research = usage["categories"]["research"]
    assert research["input_tokens"] == 5000
    assert research["output_tokens"] == 1000
    # Four dimensions each ran one search (defaults + governing_codes).
    assert research["web_search_requests"] == 4
    # Research is its own category, priced on the research model.
    assert usage["estimated_cost_usd"]["by_category"]["research"] > 0


# ---------------------------------------------------------------------------
# The context gauge (overall conversation size, not spend)
# ---------------------------------------------------------------------------
#
# ``context.tokens`` is the rendered-prompt size of the last committed turn's
# FINAL request: input + cache write + cache read from that round's usage —
# the exact Anthropic-counted size of everything sent to the model (system
# prompt, tools, history, PROJECT CONTEXT, user text). A gauge with
# last-round-wins semantics, deliberately different from the ledger's
# cumulative per-round sums.


def test_no_context_gauge_before_any_turn():
    client = _client()
    usage = client.get("/api/usage").json()
    assert usage["context"] is None


def test_context_gauge_reports_the_last_requests_prompt_size(monkeypatch):
    client = _client()
    fake = FakeClient(
        [
            text_turn(
                ["Hi."],
                usage=token_usage(input=1200, output=400, cache_read=300, cache_write=100),
            )
        ]
    )
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)
    client.post("/api/chat", json={"message": "hi"})

    context = client.get("/api/usage").json()["context"]
    # 1200 fresh + 100 cache-written + 300 cache-read = the full prompt.
    # Output tokens are NOT part of the context measurement.
    assert context == {"tokens": 1600, "window": 1_000_000}


def test_context_gauge_is_the_last_round_not_the_sum(monkeypatch):
    client = _client()
    fake = FakeClient(
        [
            tool_turn(["Working. "], _EDITS, usage=token_usage(input=800)),
            text_turn(["Done."], usage=token_usage(input=2000, cache_read=500)),
        ]
    )
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)
    client.post("/api/chat", json={"message": "go"})

    usage = client.get("/api/usage").json()
    # The ledger sums rounds (800 + 2000); the gauge is the final round only.
    assert usage["categories"]["interview"]["input_tokens"] == 2800
    assert usage["context"]["tokens"] == 2500


def test_failed_turn_does_not_move_the_context_gauge(monkeypatch):
    client = _client()
    fake = FakeClient([text_turn(["Hi."], usage=token_usage(input=1000))])
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)
    client.post("/api/chat", json={"message": "hi"})
    assert client.get("/api/usage").json()["context"]["tokens"] == 1000

    failing = FakeClient(
        [
            tool_turn(["Working. "], _EDITS, usage=token_usage(input=800)),
            RuntimeError("kaput"),
        ]
    )
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: failing)
    events = _parse_sse(client.post("/api/chat", json={"message": "go"}).text)
    assert events[-1]["type"] == "error"

    usage = client.get("/api/usage").json()
    # The spend is real (ledger grew) but history rolled back, so the gauge
    # still describes the surviving one-turn conversation.
    assert usage["categories"]["interview"]["input_tokens"] == 1800
    assert usage["context"]["tokens"] == 1000


def test_a_committed_turn_without_usage_keeps_the_previous_measurement(
    monkeypatch,
):
    client = _client()
    fake = FakeClient([text_turn(["Hi."], usage=token_usage(input=1000))])
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)
    client.post("/api/chat", json={"message": "hi"})

    # The default fake turn carries usage=None (as most scripted tests do);
    # a committed turn with no usage data must not zero the gauge.
    silent = FakeClient([text_turn(["Okay."])])
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: silent)
    client.post("/api/chat", json={"message": "again"})

    assert client.get("/api/usage").json()["context"]["tokens"] == 1000


def test_reset_clears_the_context_gauge(monkeypatch):
    client = _client()
    fake = FakeClient([text_turn(["Hi."], usage=token_usage(input=1000))])
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)
    client.post("/api/chat", json={"message": "hi"})
    assert client.get("/api/usage").json()["context"] is not None

    client.post("/api/session/reset")
    assert client.get("/api/usage").json()["context"] is None


def test_project_load_clears_the_context_gauge(monkeypatch):
    client = _client()
    fake = FakeClient([text_turn(["Hi."], usage=token_usage(input=1000))])
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)
    client.post("/api/chat", json={"message": "hi"})
    assert client.get("/api/usage").json()["context"] is not None

    project = json.loads(
        json.dumps(sessions.project_payload(sessions.get_session()))
    )
    assert client.post("/api/project/load", json=project).json()["ok"] is True
    # A loaded project has no measurement until its first turn commits.
    assert client.get("/api/usage").json()["context"] is None
