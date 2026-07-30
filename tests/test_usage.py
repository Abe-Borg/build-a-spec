"""WI4: the session-scoped usage ledger + /api/usage cost meter."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend import settings
from backend.app import create_app
from backend import sessions
from backend.usage_ledger import (
    UsageLedger,
    cache_write_split,
    estimate_usage_cost,
    usage_pricing_snapshot,
    usage_to_dict,
)
from tests.fakes import (
    FakeClient,
    SequencedFakeClient,
    research_response,
    text_block,
    text_turn,
    token_usage,
    tool_turn,
)
from tests.test_research_engine import DIM_KEYS, _item, _scripts


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


# ---------------------------------------------------------------------------
# Stopped-turn output estimate (Chunk 4.4)
# ---------------------------------------------------------------------------
#
# Provider counts and heuristics never share a field. `output_tokens` is
# always exactly what the provider reported; a stopped turn's unreported
# output lands in `estimated_output_tokens` beside it, disjoint by
# construction, and every surface that includes it says so.


def test_estimated_output_is_priced_at_the_output_rate():
    # Sonnet 5 output is $15/M. 200k reported + 100k estimated = 300k at
    # $15/M = 4.5, on top of 1M input at $3/M.
    ledger = UsageLedger()
    ledger.add(
        "interview",
        {
            "input_tokens": 1_000_000,
            "output_tokens": 200_000,
            "estimated_output_tokens": 100_000,
        },
    )
    snap = ledger.snapshot()
    assert snap["estimated_cost_usd"]["by_category"]["interview"] == 7.5
    # Disclosed, and the two counters stay separate in the bucket.
    assert snap["includes_estimated_output"] is True
    assert snap["totals"]["output_tokens"] == 200_000
    assert snap["totals"]["estimated_output_tokens"] == 100_000


def test_a_ledger_with_no_estimate_says_so():
    ledger = UsageLedger()
    ledger.add("interview", {"input_tokens": 10, "output_tokens": 5})
    assert ledger.snapshot()["includes_estimated_output"] is False
    assert UsageLedger().snapshot()["includes_estimated_output"] is False


def test_the_disclosure_flag_is_never_counted_as_a_token():
    """`bool` is a subclass of `int` in Python, so a usage record carrying
    `usage_estimated: True` would otherwise accumulate as a token count —
    1, then 2, then 3 — and render in the usage table as if it were one."""
    ledger = UsageLedger()
    for _ in range(3):
        ledger.add(
            "interview",
            {"output_tokens": 5, "usage_estimated": True},
        )
    bucket = ledger.snapshot()["categories"]["interview"]
    assert bucket == {"output_tokens": 15}
    assert "usage_estimated" not in bucket


def test_a_stopped_turns_estimate_reaches_the_session_meter(monkeypatch):
    """End to end: the estimate lands in `/api/usage`, priced and disclosed,
    without disturbing the provider-reported output count."""
    from tests.test_stop import _stopped_turn_usage

    client = _client()
    turn_usage = _stopped_turn_usage(
        [text_block("word " * 400)],  # 2000 chars → ~500 tokens
        snapshot_usage=token_usage(input=1000, output=4),
    )
    assert turn_usage["estimated_output_tokens"] == 496

    usage = client.get("/api/usage").json()
    interview = usage["categories"]["interview"]
    assert interview["output_tokens"] == 4  # provider-reported, untouched
    assert interview["estimated_output_tokens"] == 496
    assert usage["includes_estimated_output"] is True
    # The flag never became a counter on the way through the ledger.
    assert "usage_estimated" not in interview
    # 1000 in @ $3/M + (4 + 496) out @ $15/M = 0.003 + 0.0075
    assert usage["estimated_cost_usd"]["by_category"]["interview"] == 0.0105


# ---------------------------------------------------------------------------
# Per-TTL cache-write pricing (Chunk 4.1)
# ---------------------------------------------------------------------------
#
# The provider reports `cache_creation_input_tokens` as the TOTAL across TTL
# classes, with the one-hour count nested at
# `usage.cache_creation.ephemeral_1h_input_tokens` INSIDE that total. A
# one-hour entry costs 2x input to create where a five-minute entry costs
# 1.25x, so the two rates must apply to disjoint slices — charging the
# subtotal at both is a real over-report, charging it only at the cheap rate
# a real under-report.


def test_every_priced_model_configures_both_cache_write_rates():
    # A model whose table is missing either rate silently mis-prices every
    # run on it, and nothing else in the app would notice.
    for model, rates in settings.PRICING.items():
        assert set(rates) == {
            "input",
            "output",
            "cache_read",
            "cache_write",
            "cache_write_1h",
        }, model
        assert rates["cache_write"] == pytest.approx(1.25 * rates["input"]), model
        assert rates["cache_write_1h"] == pytest.approx(2.0 * rates["input"]), model


def test_one_hour_cache_writes_price_at_twice_input_not_the_five_minute_rate():
    # Opus 5 (the Final QC model): $5/M input, so a one-hour write is $10/M.
    # The five-minute rate would say $6.25 — the number this chunk removes.
    cost = estimate_usage_cost(
        settings.MODEL_OPUS_5,
        {
            "cache_creation_input_tokens": 1_000_000,
            "cache_creation_1h_input_tokens": 1_000_000,
        },
    )
    assert cost == 10.0


def test_a_pure_five_minute_cache_write_still_prices_at_1_25x():
    cost = estimate_usage_cost(
        settings.MODEL_OPUS_5, {"cache_creation_input_tokens": 1_000_000}
    )
    assert cost == 6.25


def test_mixed_ttl_cache_writes_are_not_double_counted():
    # 600k five-minute at $6.25/M = 3.75, 400k one-hour at $10/M = 4.00.
    # Double-counting the subtotal on top of the total would read 10.25.
    cost = estimate_usage_cost(
        settings.MODEL_OPUS_5,
        {
            "cache_creation_input_tokens": 1_000_000,
            "cache_creation_1h_input_tokens": 400_000,
        },
    )
    assert cost == 7.75


def test_zero_cache_creation_costs_nothing():
    assert (
        estimate_usage_cost(
            settings.MODEL_OPUS_5,
            {"cache_creation_input_tokens": 0, "cache_creation_1h_input_tokens": 0},
        )
        == 0.0
    )


def test_a_subtotal_larger_than_its_total_is_clamped_not_inverted():
    # Live provider data is clamped so a malformed subtotal skews the
    # estimate rather than subtracting a negative five-minute slice from
    # it. (Persisted audit records are held to a stricter standard — see
    # test_qc_audit_report.)
    cost = estimate_usage_cost(
        settings.MODEL_OPUS_5,
        {
            "cache_creation_input_tokens": 100_000,
            "cache_creation_1h_input_tokens": 900_000,
        },
    )
    assert cost == pytest.approx(1.0)
    assert cache_write_split(
        {
            "cache_creation_input_tokens": 100_000,
            "cache_creation_1h_input_tokens": 900_000,
        }
    ) == (0, 100_000)
    assert cache_write_split({"cache_creation_1h_input_tokens": -5}) == (0, 0)


def test_the_ledger_prices_a_one_hour_subtotal_it_accrued():
    ledger = UsageLedger()
    ledger.add(
        "qc",
        {
            "cache_creation_input_tokens": 1_000_000,
            "cache_creation_1h_input_tokens": 400_000,
        },
    )
    snap = ledger.snapshot()
    assert snap["totals"]["cache_creation_1h_input_tokens"] == 400_000
    assert snap["estimated_cost_usd"]["by_category"]["qc"] == 7.75


def test_usage_to_dict_reads_the_nested_one_hour_subtotal():
    flat = usage_to_dict(token_usage(cache_write=900, cache_write_1h=400))
    assert flat["cache_creation_input_tokens"] == 900
    assert flat["cache_creation_1h_input_tokens"] == 400
    # Absent nested object (the ordinary five-minute request) adds no key,
    # so a legacy record and a fresh all-5m one serialize identically.
    assert "cache_creation_1h_input_tokens" not in usage_to_dict(
        token_usage(cache_write=900)
    )


def test_a_malformed_nested_cache_creation_is_ignored_not_fatal():
    from types import SimpleNamespace

    for creation in (None, "nonsense", SimpleNamespace(), SimpleNamespace(
        ephemeral_1h_input_tokens="lots"
    )):
        flat = usage_to_dict(
            SimpleNamespace(input_tokens=10, cache_creation=creation)
        )
        assert flat["input_tokens"] == 10
        assert "cache_creation_1h_input_tokens" not in flat


def test_the_pricing_snapshot_publishes_the_one_hour_rate_and_explains_it():
    snap = usage_pricing_snapshot(settings.MODEL_OPUS_5)
    assert snap["rates_per_token"]["cache_write_1h"] == pytest.approx(
        10.0 / 1_000_000
    )
    treatment = snap["cache_write_treatment"]
    assert "one-hour" in treatment and "charged once" in treatment


def test_the_chat_loop_records_the_providers_one_hour_subtotal(monkeypatch):
    # The interview aggregates usage itself (_merge_usage) instead of going
    # through usage_to_dict, so it needs the nested read of its own — this
    # is what Chunk 4.2's one-hour interview breakpoints will be priced on.
    client = _client()
    fake = FakeClient(
        [
            tool_turn(
                ["Working. "],
                _EDITS,
                usage=token_usage(cache_write=500, cache_write_1h=200),
            ),
            text_turn(
                ["Done."], usage=token_usage(cache_write=300, cache_write_1h=100)
            ),
        ]
    )
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)
    resp = client.post("/api/chat", json={"message": "go"})

    (done,) = [e for e in _parse_sse(resp.text) if e["type"] == "turn_complete"]
    assert done["usage"]["cache_creation_input_tokens"] == 800
    assert done["usage"]["cache_creation_1h_input_tokens"] == 300
    interview = client.get("/api/usage").json()["categories"]["interview"]
    assert interview["cache_creation_1h_input_tokens"] == 300


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


def _seed_research_session(client: TestClient, monkeypatch) -> None:
    """A session research can start from: the fire module + a full profile.

    Research routes by the fire module's dimension messages (and requires a
    curated module's discipline gate to pass); the neutral default is now
    the generic module, so select fire first.
    """
    client.post("/api/session/reset", json={"module_id": "hyperscale_fire"})
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


def _wait_research(client: TestClient, status: str) -> None:
    import time as _time

    deadline = _time.monotonic() + 5.0
    while _time.monotonic() < deadline:
        if client.get("/api/research/status").json()["status"] == status:
            return
        _time.sleep(0.05)
    raise AssertionError(f"research never reached {status!r}")


def test_research_run_rolls_up_into_ledger(monkeypatch):
    client = _client()
    _seed_research_session(client, monkeypatch)

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
    _wait_research(client, "complete")

    usage = client.get("/api/usage").json()
    research = usage["categories"]["research"]
    assert research["input_tokens"] == 5000
    assert research["output_tokens"] == 1000
    # Four dimensions each ran one search (defaults + governing_codes).
    assert research["web_search_requests"] == 4
    # Research is its own category, priced on the research model.
    assert usage["estimated_cost_usd"]["by_category"]["research"] > 0


def test_a_research_round_that_failed_outright_still_reaches_the_meter(
    monkeypatch,
):
    """The round produced no profile, so the success path's meter never
    runs — and until the fan-out error started carrying its usage, every
    one of those calls was invisible to the session's spend."""
    client = _client()
    _seed_research_session(client, monkeypatch)

    # Every dimension banks a billed response and then fails on it.
    research_fake = SequencedFakeClient(
        {
            key: [
                research_response(
                    items=[],
                    stop_reason="max_tokens",
                    searched_urls=["https://x.gov"],
                    tokens={"input": 500, "output": 60},
                )
            ]
            for key in DIM_KEYS.values()
        }
    )
    monkeypatch.setattr("backend.app.get_client", lambda: research_fake)

    assert client.post("/api/research/start").json()["ok"] is True
    _wait_research(client, "failed")

    usage = client.get("/api/usage").json()
    research = usage["categories"]["research"]
    assert research["input_tokens"] == 2000  # 4 × 500
    assert research["output_tokens"] == 240  # 4 × 60
    assert research["web_search_requests"] == 4
    assert usage["estimated_cost_usd"]["by_category"]["research"] > 0


# ---------------------------------------------------------------------------
# The context gauge (overall conversation size, not spend)
# ---------------------------------------------------------------------------
#
# ``context.tokens`` is the conversation size after the last committed turn:
# the FINAL round's request (input + cache write + cache read — the exact
# Anthropic-counted size of everything sent to the model: system prompt,
# tools, history, PROJECT CONTEXT, user text) PLUS that round's retained
# reply (output minus thinking, which is stripped at commit). A gauge with
# last-round-wins semantics, deliberately different from the ledger's
# cumulative per-round sums.


def test_no_context_gauge_before_any_turn():
    client = _client()
    usage = client.get("/api/usage").json()
    assert usage["context"] is None


def test_context_gauge_is_the_prompt_plus_the_retained_reply(monkeypatch):
    client = _client()
    fake = FakeClient(
        [
            text_turn(
                ["Hi."],
                usage=token_usage(
                    input=1200,
                    output=400,
                    cache_read=300,
                    cache_write=100,
                    thinking=150,
                ),
            )
        ]
    )
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)
    client.post("/api/chat", json={"message": "hi"})

    context = client.get("/api/usage").json()["context"]
    # 1200 fresh + 100 cache-written + 300 cache-read = the request's full
    # prompt, plus the committed reply: 400 output − 150 thinking (thinking
    # blocks are stripped at commit and never enter history).
    assert context == {"tokens": 1850, "window": 1_000_000}


def test_context_gauge_is_the_last_round_not_the_sum(monkeypatch):
    client = _client()
    fake = FakeClient(
        [
            tool_turn(["Working. "], _EDITS, usage=token_usage(input=800, output=60)),
            text_turn(
                ["Done."], usage=token_usage(input=2000, cache_read=500, output=100)
            ),
        ]
    )
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)
    client.post("/api/chat", json={"message": "go"})

    usage = client.get("/api/usage").json()
    # The ledger sums rounds (800 + 2000); the gauge is the final round's
    # prompt (2500) + its reply (100). Round one's 60-token reply is already
    # inside round two's input, so counting it again would double-bill.
    assert usage["categories"]["interview"]["input_tokens"] == 2800
    assert usage["context"]["tokens"] == 2600


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
