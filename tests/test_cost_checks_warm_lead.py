"""The warm lead checks that the batch reads its copy (Tier 1 finish, WL-1).

The warm lead streams one seat of a large cache lineage of Final QC's batched
verifier seats FIRST, at list price, and submits the batch only after that
seat's first output, so the rest can read the 1-hour entry it wrote. Whether
a batch request can read an entry a streamed request wrote is undocumented,
so after every batched phase that ENDS NORMALLY and sent a lead, the engine
hands each such lineage to ``cost_checks.check_warm_leads``, which reads the
usage the batch already reported (the Chunk 3 plan's Appendix B):

- per batched seat, its first billed response's first iteration: it read the
  shared prefix when read / (read + write) ≥ 0.95; a seat whose first
  iteration cannot be read is unmeasured, counted and never guessed;
- per lineage, with at least 8 measured seats: h₁ (the share that read), p
  (the median prefix), C (what the lead cost at list) and the break-even
  h₀* = [(n − 1)·b·h₁·Δ·p − (1 − b)·C] / (n·b·Δ·p);
- the rules, in order: ``not_read`` when h₁ < 0.5, ``unprofitable`` when
  h₀* ≤ 0, else kept. Either switches the lead off for the rest of the app
  session, and the next phase streams no lead.

What this file pins: the arithmetic, by hand, on Claude Opus 5.5; the latch;
the check end to end on both lineage kinds; every path that must NOT measure;
that the check runs after the leads are joined; that it changes no request,
record, multiplier, meter bucket, SSE payload or manifest; and the
diagnostics block. Every engine run passes ``batch_warm_lead=True`` and a
nonzero wait explicitly, so WL-2's flip of the default changes none of them,
and every wait is an event, never a real sleep.
"""
from __future__ import annotations

import json
import logging
import threading
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend import cost_checks, diagnostics, settings, usage_ledger
from backend.app import create_app
from backend.qc import engine
from backend.qc.engine import run_final_qc
from backend.spec_modules import DEFAULT_MODULE
from backend.tracing.redaction import _SECRET_KEY_PATTERN, scrub_data
from tests.fakes import pause_response, qc_verdict_response, search_result_block
from tests.test_cost_checks_tail_rejection import _CountingLock
from tests.test_qc_batch_verification import _bad_request, _rate_limited, _store
from tests.test_qc_batch_warm_lead import (
    _LeadClient,
    _LeapClock,
    _Sink,
    _batched_ids,
    _lineage_scripts,
    _medium,
    _minimums_at_the_floor,
    _titles,
    _verdicts,
)
from tests.test_qc_warm_launch import _canonical_requests, _fixed_clock

# The model the hand-computed numbers are priced on (Appendix B.1). Named
# here rather than read from settings, so an operator's model override cannot
# move the numbers under the assertions.
_OPUS = "claude-opus-5-5"
_WARM = 45
# Bounds a wait that only a broken build would ever reach.
_BOUND = 10.0
_URL = "https://codes.example.gov/nfpa13"

# A batched seat that read the lineage's shared prefix, and one that wrote it
# (the verifier seats write 1-hour entries).
_READ = {"input": 40, "output": 900, "cache_read": 30_000}
_WROTE = {"input": 40, "output": 900, "cache_write": 30_000, "cache_write_1h": 30_000}
# The lead: 70 × $4 + 1,300 × $20 + 32,000 × $8 per million = $0.28228.
_LEAD = {"input": 70, "output": 1_300, "cache_write": 32_000, "cache_write_1h": 32_000}
_LEAD_COST = 0.28228


@pytest.fixture(autouse=True)
def _published_rates() -> None:
    """The rates every hand-computed number below assumes (per million:
    input, output, cache read, 1-hour cache write), and the batch
    multiplier. If a price moves, this says so first."""
    rates = usage_ledger.model_rates(_OPUS)
    per_million = {
        name: round(rates[name] * 1_000_000, 6)
        for name in ("input", "output", "cache_read", "cache_write_1h")
    }
    assert per_million == {
        "input": 4.0,
        "output": 20.0,
        "cache_read": 0.2,
        "cache_write_1h": 8.0,
    }
    assert settings.BATCH_COST_MULTIPLIER == 0.5


# ---------------------------------------------------------------------------
# Unit fixtures: one lineage, by hand
# ---------------------------------------------------------------------------

# The unit tests' lead: 70 × $4 + 4,000 × $20 + 40,000 × $8 per million =
# $0.40028 — Appendix B.6's C ≈ $0.40.
_UNIT_LEAD = {
    "input_tokens": 70,
    "output_tokens": 4_000,
    "cache_creation_input_tokens": 40_000,
    "cache_creation_1h_input_tokens": 40_000,
}


def _seat(read: int, write: int, **extra: Any) -> SimpleNamespace:
    """A batched seat's first response, from ONE model iteration (a verdict
    tool call and nothing else), reading and writing that many tokens."""
    return qc_verdict_response(
        True,
        tokens={"input": 40, "output": 900, "cache_read": read, "cache_write": write, **extra},
    )


def _lineage(
    batched: list[Any],
    *,
    seats: int | None = None,
    lead: dict[str, int] | None = None,
    warm: bool = True,
    kind: str = "no-web",
) -> cost_checks.WarmLeadLineage:
    return cost_checks.WarmLeadLineage(
        kind=kind,
        seats=len(batched) + 1 if seats is None else seats,
        model=_OPUS,
        lead_usage=dict(_UNIT_LEAD if lead is None else lead),
        batched_first=tuple(batched),
        warm=warm,
    )


def _judge(lineage: cost_checks.WarmLeadLineage) -> dict[str, Any]:
    return cost_checks._lineage_record(cost_checks._judge_lineage(lineage))


def _warm_block() -> dict[str, Any]:
    return cost_checks.snapshot()["warm_lead"]


def _warnings(caplog) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "buildaspec.cost_checks" and record.levelno == logging.WARNING
    ]


def _infos(caplog) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "buildaspec.cost_checks" and record.levelno == logging.INFO
    ]


# ---------------------------------------------------------------------------
# The arithmetic (Appendix B), by hand
# ---------------------------------------------------------------------------


def test_h1_p_c_and_the_break_even_by_hand() -> None:
    """Nine batched seats read a 40,000-token prefix; n = 10.

    Δ = $8.00 − $0.20 = $7.80 per million, b = 0.5, so
    N = 9 × 0.5 × 1 × 7.8e-6 × 40,000 − 0.5 × 0.40028 = 1.404 − 0.20014
      = 1.20386,
    D = 10 × 0.5 × 7.8e-6 × 40,000 = 1.56, and h₀* = 1.20386 / 1.56.
    """
    judged = cost_checks._judge_lineage(_lineage([_seat(40_000, 0)] * 9))
    assert judged.read_share == 1
    assert judged.prefix == 40_000
    assert judged.lead_cost == Decimal("0.40028")
    assert judged.break_even == Decimal("1.20386") / Decimal("1.56")
    assert judged.verdict == "kept"
    assert cost_checks._lineage_record(judged) == {
        "kind": "no-web",
        "seats": 10,
        "measured": 9,
        "unmeasured": 0,
        "read_share": 1.0,
        "prefix_tokens": 40_000,
        "lead_cost_usd": 0.40028,
        # 0.771705… rounded AWAY from zero, so a hair above zero never
        # reads as 0.0 beside a kept verdict.
        "break_even_read_share": 0.7718,
        "verdict": "kept",
    }


def test_p_is_the_median_of_the_measured_prefixes() -> None:
    """An even count takes the mean of the middle two: four seats at 30,000
    and four at 40,000 give p = 35,000 (h₁ = 0.5: the 40,000s wrote)."""
    batched = [_seat(30_000, 0)] * 4 + [_seat(0, 40_000)] * 4
    judged = cost_checks._judge_lineage(_lineage(batched))
    assert judged.prefix == 35_000
    assert judged.read_share == Decimal("0.5")
    # N = 8 × 0.5 × 0.5 × 7.8e-6 × 35,000 − 0.20014 = 0.34586;
    # D = 9 × 0.5 × 7.8e-6 × 35,000 = 1.2285.
    assert judged.break_even == Decimal("0.34586") / Decimal("1.2285")
    # An odd count takes the middle one, whatever the order.
    odd = [_seat(0, 50_000), _seat(20_000, 0), *[_seat(30_000, 0)] * 7]
    assert cost_checks._judge_lineage(_lineage(odd)).prefix == 30_000


def test_a_seat_read_the_prefix_at_95_percent_and_not_below() -> None:
    """38,000 read and 2,000 written is 95%: read. 37,600 and 2,400 is 94%:
    written. Four of each is h₁ = 0.5 — exactly at the lineage threshold,
    which is not below it."""
    at = [_seat(38_000, 2_000)] * 4
    below = [_seat(37_600, 2_400)] * 4
    judged = cost_checks._judge_lineage(_lineage(at + below))
    assert (judged.reads, judged.measured) == (4, 8)
    assert judged.read_share == Decimal("0.5")
    assert judged.verdict == "kept"
    # N = 8 × 0.5 × 0.5 × 7.8e-6 × 40,000 − 0.20014 = 0.42386; D = 1.404.
    assert judged.break_even == Decimal("0.42386") / Decimal("1.404")


def test_not_read_below_half_and_kept_at_half() -> None:
    three = [_seat(40_000, 0)] * 3 + [_seat(0, 40_000)] * 5
    four = [_seat(40_000, 0)] * 4 + [_seat(0, 40_000)] * 4
    below = _judge(_lineage(three))
    assert below["read_share"] == 0.375
    assert below["verdict"] == "not_read"
    assert _judge(_lineage(four))["verdict"] == "kept"


def test_an_expensive_lead_is_unprofitable() -> None:
    """h₁ = 0.5 (not below the threshold), and a lead that cost $1.82028:
    50,000 output ($1.00), a 40,000-token 1-hour write ($0.32), 70 input
    and 50 web searches ($0.50). N = 0.624 − 0.91014 = −0.28614 ≤ 0: it lost
    money even if the batch alone would have read nothing."""
    expensive = {
        "input_tokens": 70,
        "output_tokens": 50_000,
        "cache_creation_input_tokens": 40_000,
        "cache_creation_1h_input_tokens": 40_000,
        "web_search_requests": 50,
    }
    batched = [_seat(40_000, 0)] * 4 + [_seat(0, 40_000)] * 4
    judged = cost_checks._judge_lineage(_lineage(batched, lead=expensive))
    assert judged.lead_cost == Decimal("1.82028")
    assert judged.break_even == Decimal("-0.28614") / Decimal("1.404")
    assert judged.verdict == "unprofitable"
    record = cost_checks._lineage_record(judged)
    assert record["break_even_read_share"] == -0.2039
    assert record["lead_cost_usd"] == 1.82028


def test_a_break_even_of_exactly_zero_is_unprofitable() -> None:
    """C = 62,400 output × $20/M = $1.248: N = 0.624 − 0.624 = 0, so h₀* = 0
    — the lead broke even only if the batch alone read nothing. One output
    token fewer and h₀* is a hair above zero: kept, shown as 0.0001."""
    batched = [_seat(40_000, 0)] * 4 + [_seat(0, 40_000)] * 4
    zero = cost_checks._judge_lineage(_lineage(batched, lead={"output_tokens": 62_400}))
    assert zero.break_even == 0
    assert zero.verdict == "unprofitable"
    above = _judge(_lineage(batched, lead={"output_tokens": 62_399}))
    assert above["verdict"] == "kept"
    assert above["break_even_read_share"] == 0.0001


def test_fewer_than_eight_measured_seats_give_no_verdict() -> None:
    """Seven batched seats cannot be judged however they read, and nine of
    which two are unmeasured are seven measured: ``too_few``, no numbers but
    the lead's cost. A lineage at the floor of 8 seats has only 7 batched."""
    seven = _judge(_lineage([_seat(0, 40_000)] * 7))
    assert seven["verdict"] == "too_few"
    assert (seven["measured"], seven["unmeasured"]) == (7, 0)
    assert seven["read_share"] is None
    assert seven["prefix_tokens"] is None
    assert seven["break_even_read_share"] is None
    assert seven["lead_cost_usd"] == 0.40028
    nine = _judge(_lineage([_seat(0, 40_000)] * 7 + [None, _seat(0, 0)]))
    assert (nine["verdict"], nine["measured"], nine["unmeasured"]) == ("too_few", 7, 2)


def test_a_lead_the_batch_did_not_wait_for_is_not_judged() -> None:
    """The batch went out before the lead's copy was readable (its wait timed
    out, or its first request failed): nothing about the lead can be read
    from the batch, however low h₁ is. The numbers are still reported."""
    record = _judge(_lineage([_seat(0, 40_000)] * 9, warm=False))
    assert record["verdict"] == "not_warm"
    assert record["read_share"] == 0.0
    assert record["measured"] == 9


def test_what_cannot_be_read_is_unmeasured_and_nothing_raises() -> None:
    """No response; a seat that searched (its top-level usage can sum several
    model iterations); one that read and wrote nothing; and malformed usage —
    a bool count, a negative count, a missing input count, no usage at all.
    Each is counted as unmeasured, never guessed."""
    searched = _seat(40_000, 0, searches=1)
    searched.content.insert(0, search_result_block([_URL]))
    malformed = [
        _seat(40_000, 0),
        _seat(40_000, 0),
        _seat(40_000, 0),
        SimpleNamespace(content=[], usage=None),
    ]
    malformed[0].usage.cache_read_input_tokens = True
    malformed[1].usage.cache_creation_input_tokens = -5
    del malformed[2].usage.input_tokens
    record = _judge(_lineage([None, searched, _seat(0, 0), *malformed, "garbage"]))
    assert (record["measured"], record["unmeasured"]) == (0, 8)
    assert record["verdict"] == "too_few"


def test_a_searched_seat_is_measured_from_its_reported_first_iteration() -> None:
    """With ``usage.iterations`` reported, the first ``message`` entry is the
    first model iteration, whatever the top-level usage sums."""
    iterations = [
        {
            "type": "message",
            "input_tokens": 20,
            "output_tokens": 300,
            "cache_read_input_tokens": 30_000,
            "cache_creation_input_tokens": 0,
        },
        {
            "type": "message",
            "input_tokens": 60,
            "output_tokens": 600,
            "cache_read_input_tokens": 36_000,
            "cache_creation_input_tokens": 6_000,
        },
    ]
    seats = []
    for _ in range(9):
        seat = _seat(66_000, 6_000, searches=1, iterations=iterations)
        seat.content.insert(0, search_result_block([_URL]))
        seats.append(seat)
    record = _judge(_lineage(seats, kind="web-tooled"))
    assert (record["measured"], record["read_share"]) == (9, 1.0)
    assert record["prefix_tokens"] == 30_000


def test_a_failure_inside_the_check_records_nothing(monkeypatch, caplog) -> None:
    """A check that cannot finish leaves no half-recorded state and no latch,
    and never raises into the engine."""
    caplog.set_level(logging.DEBUG, logger="buildaspec.cost_checks")
    losing = _lineage([_seat(0, 40_000)] * 9)
    cost_checks.check_warm_leads([_lineage([_seat(0, 40_000)] * 9, lead={"input_tokens": "x"})])
    assert _warm_block()["last_check"] is None

    def refuse(*_args: Any, **_kwargs: Any) -> float:
        raise RuntimeError("pricing unavailable")

    monkeypatch.setattr(usage_ledger, "estimate_usage_cost", refuse)
    cost_checks.check_warm_leads([losing])
    assert _warm_block()["last_check"] is None
    assert cost_checks.warm_lead_enabled()
    assert _warnings(caplog) == []
    assert any("could not check the warm lead" in m for m in caplog.messages)


def test_the_rates_come_from_the_ledger(monkeypatch) -> None:
    """Change what the ledger prices at, and the break-even follows: the
    check keeps no table of its own."""
    doubled = {
        name: rate * 2 for name, rate in settings.PRICING[_OPUS].items()
    }
    monkeypatch.setattr(usage_ledger, "_rates", lambda _model: doubled)
    judged = cost_checks._judge_lineage(_lineage([_seat(40_000, 0)] * 9))
    # Everything doubles: Δ, and C through the ledger's own estimate.
    assert judged.lead_cost == Decimal("0.80056")
    assert judged.break_even == Decimal("2.40772") / Decimal("3.12")


# ---------------------------------------------------------------------------
# The latch
# ---------------------------------------------------------------------------


def test_a_losing_lineage_latches_once_with_one_warning(caplog) -> None:
    caplog.set_level(logging.INFO, logger="buildaspec.cost_checks")
    assert cost_checks.warm_lead_enabled()
    before = _warm_block()
    assert (before["enabled"], before["reason"], before["since"]) == (True, "", None)

    cost_checks.check_warm_leads([_lineage([_seat(0, 40_000)] * 9)])
    block = _warm_block()
    assert not cost_checks.warm_lead_enabled()
    assert (block["enabled"], block["reason"]) == (False, "not_read")
    assert block["detail"] == (
        "The batch read the shared prefix on 0 of 9 measured no-web seats (0%); "
        "a batch that reads the lead's copy reads it on nearly all of them."
    )
    assert isinstance(block["since"], float)
    assert _warnings(caplog) == [
        "Cost self-check: the warm lead is switched off until the app restarts "
        f"(not_read). {block['detail']}"
    ]

    # The first latch wins: a later loss changes nothing and says nothing,
    # though the check it came with is still recorded.
    expensive = {"output_tokens": 62_400}
    cost_checks.check_warm_leads(
        [_lineage([_seat(40_000, 0)] * 4 + [_seat(0, 40_000)] * 4, lead=expensive)]
    )
    assert _warm_block()["reason"] == "not_read"
    assert _warm_block()["last_check"]["lineages"][0]["verdict"] == "unprofitable"
    assert len(_warnings(caplog)) == 1


def test_one_info_line_per_lineage_with_numbers_only(caplog) -> None:
    caplog.set_level(logging.INFO, logger="buildaspec.cost_checks")
    cost_checks.check_warm_leads(
        [
            _lineage([_seat(40_000, 0)] * 9),
            _lineage([_seat(0, 40_000)] * 7, kind="web-tooled"),
        ]
    )
    assert _infos(caplog) == [
        "Warm lead check: 10 no-web seats (the lead among them), 9 measured, "
        "0 unmeasured; read share 1.0000, break-even read share 0.7718, lead "
        "cost $0.400280: kept.",
        "Warm lead check: 8 web-tooled seats (the lead among them), 7 measured, "
        "0 unmeasured; read share n/a, break-even read share n/a, lead cost "
        "$0.400280: too_few.",
    ]
    assert _warnings(caplog) == []


def test_the_first_losing_lineage_sets_the_reason() -> None:
    expensive = {"output_tokens": 62_400}
    half = [_seat(40_000, 0)] * 4 + [_seat(0, 40_000)] * 4
    cost_checks.check_warm_leads(
        [
            _lineage([_seat(40_000, 0)] * 9),
            _lineage(half, lead=expensive),
            _lineage([_seat(0, 40_000)] * 9),
        ]
    )
    block = _warm_block()
    assert block["reason"] == "unprofitable"
    assert block["detail"] == (
        "The lead cost an estimated $1.248000, more than it could have saved on "
        "its 9-seat no-web lineage even if the batch alone had read nothing "
        "(break-even read share 0.0)."
    )
    assert [entry["verdict"] for entry in block["last_check"]["lineages"]] == [
        "kept",
        "unprofitable",
        "not_read",
    ]


def test_disable_takes_only_a_warm_lead_reason(caplog) -> None:
    caplog.set_level(logging.WARNING, logger="buildaspec.cost_checks")
    cost_checks.disable_warm_lead(reason="rejected", detail="not ours")
    assert cost_checks.warm_lead_enabled()
    cost_checks.disable_warm_lead(reason="unprofitable", detail="line one\nline two")
    block = _warm_block()
    assert (block["reason"], block["detail"]) == ("unprofitable", "line one line two")
    cost_checks.disable_warm_lead(reason="not_read")
    assert _warm_block()["reason"] == "unprofitable"
    assert len(_warnings(caplog)) == 1


def test_the_warm_lead_and_the_tails_latch_apart() -> None:
    cost_checks.disable_warm_lead(reason="not_read")
    assert all(
        cost_checks.continuation_tail_enabled(engine_name)
        for engine_name in cost_checks.TAIL_ENGINES
    )
    cost_checks.reset_for_tests()
    for engine_name in cost_checks.TAIL_ENGINES:
        cost_checks.disable_continuation_tail(engine_name, reason="rejected")
    assert cost_checks.warm_lead_enabled()


def test_every_read_and_write_takes_the_one_lock_once(monkeypatch) -> None:
    """A check is recorded and latched in one acquisition, so a reader never
    sees a check without the latch it earned; the snapshot reads the tails
    and the warm lead in one, so they describe one moment."""
    lock = _CountingLock()
    monkeypatch.setattr(cost_checks, "_lock", lock)
    losing = _lineage([_seat(0, 40_000)] * 9)
    for call in (
        cost_checks.warm_lead_enabled,
        lambda: cost_checks.disable_warm_lead(reason="not_read"),
        lambda: cost_checks.check_warm_leads([losing]),
        lambda: cost_checks.check_warm_leads([_lineage([_seat(40_000, 0)] * 9)]),
        cost_checks.snapshot,
        cost_checks.reset_for_tests,
        lambda: cost_checks.check_warm_leads([losing]),
    ):
        before = lock.taken
        call()
        assert lock.taken == before + 1
    assert _warm_block()["reason"] == "not_read"


def test_many_threads_check_and_one_latches(caplog) -> None:
    caplog.set_level(logging.WARNING, logger="buildaspec.cost_checks")
    start = threading.Barrier(8, timeout=_BOUND)
    failures: list[BaseException] = []
    losing = _lineage([_seat(0, 40_000)] * 9)

    def check() -> None:
        try:
            start.wait()
            for _ in range(10):
                cost_checks.check_warm_leads([losing])
                cost_checks.warm_lead_enabled()
        except BaseException as exc:  # noqa: BLE001 — collected, then asserted
            failures.append(exc)

    threads = [threading.Thread(target=check) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(_BOUND)
    assert failures == []
    assert _warm_block()["reason"] == "not_read"
    assert len(_warnings(caplog)) == 1


def test_an_empty_check_records_nothing() -> None:
    cost_checks.check_warm_leads([])
    assert _warm_block()["last_check"] is None


def test_a_latch_left_set_on_purpose() -> None:
    """Paired with the next test, which must find the warm lead clear."""
    cost_checks.check_warm_leads([_lineage([_seat(0, 40_000)] * 9)])
    assert not cost_checks.warm_lead_enabled()


def test_the_next_test_starts_with_the_warm_lead_clear() -> None:
    block = _warm_block()
    assert cost_checks.warm_lead_enabled()
    assert (block["reason"], block["detail"], block["since"]) == ("", "", None)
    assert block["last_check"] is None


# ---------------------------------------------------------------------------
# End to end: a real batched phase
# ---------------------------------------------------------------------------


def _run(client, *, store=None, sink=None, should_stop=lambda: False):
    store = store or _store()
    return run_final_qc(
        store.doc,
        None,
        DEFAULT_MODULE,
        client,
        model=_OPUS,
        max_tokens=4096,
        version_index=store.index,
        started_at="2026-09-24T10:00:00-07:00",
        finished_at="2026-09-24T10:01:00-07:00",
        run_id="qc-warm-lead-check",
        batch_verification=True,
        batch_warm_lead=True,
        warm_wait_seconds=_WARM,
        event_sink=sink or (lambda _event: None),
        should_stop=should_stop,
    )


def _doc_scripts(
    seat_tokens: list[dict[str, int]] | None = None,
    *,
    lead: dict[str, int] | None = None,
    count: int = 5,
) -> tuple[list[str], dict[str, list[object]]]:
    """A document-only lineage of ``2 × count`` seats (medium findings).

    The lead is the first seat of the first finding and takes that title's
    first turn; ``seat_tokens`` gives the other seats' usage, in submission
    order (default: every one read the 30,000-token prefix).
    """
    titles = _titles("Doc gap", count)
    scripts = _lineage_scripts(
        doc=_medium(titles),
        lead_first={titles[0]: [qc_verdict_response(True, tokens=lead or _LEAD)]},
    )
    tokens = list(seat_tokens or [_READ] * (2 * count - 1))
    assert len(tokens) == 2 * count - 1
    batched = [(titles[0], 1)] + [(title, j) for title in titles[1:] for j in (0, 1)]
    for (title, turn), usage_tokens in zip(batched, tokens):
        scripts[title][turn] = qc_verdict_response(True, tokens=usage_tokens)
    return titles, scripts


def test_a_lineage_the_batch_reads_is_kept_and_diagnostics_record_it(
    monkeypatch, caplog
) -> None:
    """Ten no-web seats at the floor; the nine batched ones read the 30,000
    prefix. N = 9 × 0.5 × 1 × 7.8e-6 × 30,000 − 0.5 × 0.28228 = 0.91186,
    D = 10 × 0.5 × 7.8e-6 × 30,000 = 1.17, h₀* = 0.77936…"""
    caplog.set_level(logging.INFO, logger="buildaspec.cost_checks")
    _minimums_at_the_floor(monkeypatch)
    titles, scripts = _doc_scripts()
    client = _LeadClient(scripts)
    result = _run(client)

    assert client.streamed == [titles[0]]
    assert result.execution_status == "complete"
    block = diagnostics.snapshot()["cost_checks"]["warm_lead"]
    assert block["enabled"] is True
    assert block["reason"] == ""
    assert isinstance(block["last_check"]["at"], float)
    assert block["last_check"]["lineages"] == [
        {
            "kind": "no-web",
            "seats": 10,
            "measured": 9,
            "unmeasured": 0,
            "read_share": 1.0,
            "prefix_tokens": 30_000,
            "lead_cost_usd": _LEAD_COST,
            "break_even_read_share": 0.7794,
            "verdict": "kept",
        }
    ]
    assert len(_infos(caplog)) == 1
    assert _warnings(caplog) == []


def test_a_batch_that_writes_the_prefix_switches_the_lead_off_for_the_next_run(
    monkeypatch, caplog
) -> None:
    caplog.set_level(logging.WARNING, logger="buildaspec.cost_checks")
    _minimums_at_the_floor(monkeypatch)
    titles, scripts = _doc_scripts([_WROTE] * 9)
    first = _LeadClient(scripts)
    _run(first)

    assert first.streamed == [titles[0]]
    block = _warm_block()
    assert (block["enabled"], block["reason"]) == (False, "not_read")
    assert block["last_check"]["lineages"][0]["read_share"] == 0.0
    assert len(_warnings(caplog)) == 1

    # The next run streams no lead: nothing reaches the provider before the
    # batch, and every seat, the one that would have led included, rides it.
    _titles_again, scripts_again = _doc_scripts([_READ] * 9)
    second = _LeadClient(scripts_again)
    result = _run(second)
    assert second.streamed == []
    assert second.batches_at_stream == []
    assert len(second.batches.created) == 1
    assert len(_batched_ids(second)) == 10
    assert all(
        verdict.cost_multiplier == settings.BATCH_COST_MULTIPLIER
        for verdict in _verdicts(result).values()
    )
    # A phase with no lead leaves the last check as it was.
    assert _warm_block()["last_check"] == block["last_check"]


def test_an_expensive_lead_on_a_lightly_read_lineage_is_unprofitable(
    monkeypatch,
) -> None:
    """Five of the nine batched seats read (h₁ = 5/9, not below one half), and
    the lead cost $1.45628 (60,000 output, a 32,000-token 1-hour write, 70
    input): N = 2.5 × 0.234 − 0.72814 = −0.14314."""
    _minimums_at_the_floor(monkeypatch)
    lead = {"input": 70, "output": 60_000, "cache_write": 32_000, "cache_write_1h": 32_000}
    _titles_used, scripts = _doc_scripts([_READ] * 5 + [_WROTE] * 4, lead=lead)
    _run(_LeadClient(scripts))

    block = _warm_block()
    assert block["reason"] == "unprofitable"
    (lineage,) = block["last_check"]["lineages"]
    assert lineage["read_share"] == 0.5556
    assert lineage["lead_cost_usd"] == 1.45628
    assert lineage["break_even_read_share"] == -0.1224
    assert lineage["verdict"] == "unprofitable"


def _web_scripts(
    seat_turn, *, count: int = 5
) -> tuple[list[str], dict[str, list[object]]]:
    """A web-tooled lineage of ``2 × count`` seats; the batched seats' turns
    come from ``seat_turn()``."""
    titles = _titles("Web gap", count)
    scripts = _lineage_scripts(web=_medium(titles))
    batched = [(titles[0], 1)] + [(title, j) for title in titles[1:] for j in (0, 1)]
    for title, turn in batched:
        scripts[title][turn] = seat_turn()
    return titles, scripts


def _searched_seat(*, iterations: list[dict] | None = None) -> SimpleNamespace:
    tokens: dict[str, Any] = {
        "input": 80,
        "output": 1_500,
        "cache_read": 66_000,
        "cache_write": 6_000,
        "searches": 1,
    }
    if iterations is not None:
        tokens["iterations"] = iterations
    return qc_verdict_response(True, searched_urls=[_URL], tokens=tokens)


def test_a_web_tooled_lineage_that_searched_without_iterations_is_unmeasured(
    monkeypatch,
) -> None:
    _minimums_at_the_floor(monkeypatch)
    titles, scripts = _web_scripts(_searched_seat)
    client = _LeadClient(scripts)
    _run(client)

    assert client.streamed == [titles[0]]
    (lineage,) = _warm_block()["last_check"]["lineages"]
    assert lineage["kind"] == "web-tooled"
    assert (lineage["measured"], lineage["unmeasured"]) == (0, 9)
    assert lineage["verdict"] == "too_few"
    assert cost_checks.warm_lead_enabled()


def test_a_web_tooled_lineage_with_iterations_is_measured(monkeypatch) -> None:
    iterations = [
        {
            "type": "message",
            "input_tokens": 20,
            "output_tokens": 300,
            "cache_read_input_tokens": 30_000,
            "cache_creation_input_tokens": 0,
        },
        {
            "type": "message",
            "input_tokens": 60,
            "output_tokens": 1_200,
            "cache_read_input_tokens": 36_000,
            "cache_creation_input_tokens": 6_000,
        },
    ]
    _minimums_at_the_floor(monkeypatch)
    _titles_used, scripts = _web_scripts(lambda: _searched_seat(iterations=iterations))
    _run(_LeadClient(scripts))

    (lineage,) = _warm_block()["last_check"]["lineages"]
    assert lineage["kind"] == "web-tooled"
    assert (lineage["measured"], lineage["read_share"]) == (9, 1.0)
    assert lineage["prefix_tokens"] == 30_000
    assert lineage["verdict"] == "kept"


def test_a_seat_the_round_ceiling_failed_is_still_measured(monkeypatch) -> None:
    """One batched seat paused in round 1 and the round ceiling is 1, so it
    ends failed — but its first billed response read the prefix, and that is
    what the check reads."""
    _minimums_at_the_floor(monkeypatch)
    monkeypatch.setattr(settings, "QC_BATCH_MAX_ROUNDS", 1)
    titles, scripts = _doc_scripts()
    paused = pause_response()
    paused.usage = SimpleNamespace(
        input_tokens=40,
        output_tokens=120,
        cache_read_input_tokens=30_000,
        cache_creation_input_tokens=0,
        server_tool_use=None,
    )
    scripts[titles[1]][0] = paused
    result = _run(_LeadClient(scripts))

    failed = [
        verdict
        for verdict in _verdicts(result).values()
        if verdict.status == "failed"
    ]
    assert len(failed) == 1 and "round ceiling" in failed[0].error
    (lineage,) = _warm_block()["last_check"]["lineages"]
    assert (lineage["measured"], lineage["read_share"]) == (9, 1.0)
    assert lineage["verdict"] == "kept"


def test_a_paused_seat_is_measured_from_its_first_response(monkeypatch) -> None:
    """A seat that paused in round 1 and finished in round 2 has two billed
    responses. Its FIRST is its request in the round that went out after the
    lead's release — what read or wrote the prefix — and that is what the
    check reads; its continuation (here scripted to write 36,000 tokens) is
    not the seat's read of the prefix."""
    _minimums_at_the_floor(monkeypatch)
    titles, scripts = _doc_scripts()
    paused = pause_response()
    paused.usage = SimpleNamespace(
        input_tokens=40,
        output_tokens=120,
        cache_read_input_tokens=30_000,
        cache_creation_input_tokens=0,
        server_tool_use=None,
    )
    scripts[titles[1]][0] = paused
    scripts[titles[1]].append(
        qc_verdict_response(
            True,
            tokens={"input": 40, "output": 900, "cache_write": 36_000, "cache_write_1h": 36_000},
        )
    )
    client = _LeadClient(scripts)
    result = _run(client)

    assert len(client.batches.created) == 2
    assert result.execution_status == "complete"
    (lineage,) = _warm_block()["last_check"]["lineages"]
    assert (lineage["measured"], lineage["read_share"]) == (9, 1.0)
    assert lineage["prefix_tokens"] == 30_000


def test_a_retried_seat_is_measured_only_by_its_first_batch(monkeypatch, caplog) -> None:
    """A seat whose item in its first batch errored is retried in the next
    round, after that round has ended, when it can read a copy an EARLIER
    batched seat stored rather than the lead's (Codex, PR #227). So the check
    reads a seat's reply to the first batch it rode, and a seat whose first
    batch brought none is unmeasured.

    Eighteen no-web seats: the lead, 8 batched seats that wrote the prefix
    in round 1, and 9 whose round-1 items errored and whose round-2 retries
    read it (from the writers' copies). Read from their retries the lineage
    would read 9 of 17 and keep the lead; read from their first batch it
    reads 0 of 8 measured, with the 9 retried seats unmeasured."""
    caplog.set_level(logging.WARNING, logger="buildaspec.cost_checks")
    _minimums_at_the_floor(monkeypatch)
    titles, scripts = _doc_scripts(count=9)
    overloaded = RuntimeError("overloaded")  # an api_error line: retryable

    def seat(tokens: dict[str, int]) -> SimpleNamespace:
        return qc_verdict_response(True, tokens=tokens)

    # A title's queue is popped in submission order: its first seat, then its
    # second, then round 2's retries. The lead took the first title's first
    # turn before any batch.
    scripts[titles[0]] = [scripts[titles[0]][0], overloaded, seat(_READ)]
    for title in titles[1:5]:
        scripts[title] = [seat(_WROTE), seat(_WROTE)]
    for title in titles[5:]:
        scripts[title] = [overloaded, overloaded, seat(_READ), seat(_READ)]
    client = _LeadClient(scripts)
    result = _run(client)

    assert client.streamed == [titles[0]]
    assert [len(batch) for batch in client.batches.created] == [17, 9]
    assert result.execution_status == "complete"
    (lineage,) = _warm_block()["last_check"]["lineages"]
    assert (lineage["seats"], lineage["measured"], lineage["unmeasured"]) == (18, 8, 9)
    assert lineage["read_share"] == 0.0
    assert lineage["verdict"] == "not_read"
    assert not cost_checks.warm_lead_enabled()
    assert len(_warnings(caplog)) == 1


def test_a_refused_submission_is_not_a_seats_first_batch(monkeypatch) -> None:
    """A 429 on the first ``batches.create`` ran nothing, so it is no seat's
    first batch: the batch the provider accepted next is, and its replies
    are read as usual."""
    _minimums_at_the_floor(monkeypatch)
    monkeypatch.setattr(engine.time, "sleep", lambda _seconds: None)
    titles, scripts = _doc_scripts()
    creates = {"n": 0}

    def refuse_the_first(_requests) -> None:
        creates["n"] += 1
        if creates["n"] == 1:
            raise _rate_limited()

    client = _LeadClient(scripts, on_create=refuse_the_first)
    result = _run(client)

    assert creates["n"] == 2
    assert len(client.batches.created) == 1
    assert result.execution_status == "complete"
    (lineage,) = _warm_block()["last_check"]["lineages"]
    assert (lineage["measured"], lineage["unmeasured"]) == (9, 0)
    assert lineage["read_share"] == 1.0
    assert lineage["verdict"] == "kept"


def test_a_lead_whose_first_request_failed_is_not_judged(monkeypatch) -> None:
    """The lead's first request failed fast (its ``finally`` released the
    wait), so the batch went out with no copy of the lead's to read. The
    lead's retry then succeeded, so it is a streamed lead — but the batch
    writing the prefix says nothing about it: ``not_warm``, no latch."""
    _minimums_at_the_floor(monkeypatch)
    monkeypatch.setattr(engine.time, "sleep", lambda _seconds: None)
    titles, scripts = _doc_scripts([_WROTE] * 9)
    scripts[titles[0]].insert(0, ConnectionError("peer closed connection"))
    client = _LeadClient(scripts)
    _run(client)

    assert client.streamed == [titles[0], titles[0]]
    (lineage,) = _warm_block()["last_check"]["lineages"]
    assert lineage["verdict"] == "not_warm"
    assert lineage["read_share"] == 0.0
    assert cost_checks.warm_lead_enabled()


def test_a_lead_whose_wait_timed_out_is_not_judged(monkeypatch) -> None:
    """The lead produced nothing before the bounded wait expired, so the batch
    went out without its copy. The lead is held on an event until the batch
    has been read, so a short wait can only end by expiring — never by the
    lead — whatever the machine's speed."""
    _minimums_at_the_floor(monkeypatch)
    titles, scripts = _doc_scripts([_WROTE] * 9)
    released = threading.Event()
    held: dict[str, bool] = {}

    def before_first(_title: str) -> None:
        held["released"] = released.wait(_BOUND)

    client = _LeadClient(
        scripts, before_first=before_first, on_results=lambda _id: released.set()
    )
    store = _store()
    result = run_final_qc(
        store.doc,
        None,
        DEFAULT_MODULE,
        client,
        model=_OPUS,
        max_tokens=4096,
        version_index=store.index,
        started_at="2026-09-24T10:00:00-07:00",
        finished_at="2026-09-24T10:01:00-07:00",
        run_id="qc-warm-lead-timeout",
        batch_verification=True,
        batch_warm_lead=True,
        warm_wait_seconds=0.05,
        event_sink=lambda _event: None,
    )

    assert held == {"released": True}
    assert result.execution_status == "complete"
    (lineage,) = _warm_block()["last_check"]["lineages"]
    assert lineage["verdict"] == "not_warm"
    assert cost_checks.warm_lead_enabled()


def test_the_check_reads_the_lead_only_after_it_is_joined(monkeypatch) -> None:
    """The lead is held after its first output until something JOINS it —
    its future's ``result()`` asked with the lead still out, which only the
    wait-mode fold does. So the check can see the lead only if the join runs
    first."""
    _minimums_at_the_floor(monkeypatch)
    release = threading.Event()
    real_pool = engine.ThreadPoolExecutor

    class Joining(real_pool):
        def submit(self, *args: Any, **kwargs: Any):
            future = super().submit(*args, **kwargs)
            if self._thread_name_prefix == "qc-lead":
                real_result = future.result

                def result(timeout: float | None = None):
                    release.set()
                    return real_result(timeout)

                future.result = result  # type: ignore[method-assign]
            return future

    monkeypatch.setattr(engine, "ThreadPoolExecutor", Joining)
    titles, scripts = _doc_scripts()
    held: dict[str, bool] = {}

    def after_first(_title: str) -> None:
        held["released"] = release.wait(_BOUND)

    client = _LeadClient(scripts, after_first=after_first)
    _run(client)

    assert held == {"released": True}
    (lineage,) = _warm_block()["last_check"]["lineages"]
    assert (lineage["measured"], lineage["verdict"]) == (9, "kept")
    assert lineage["lead_cost_usd"] == _LEAD_COST


# ---------------------------------------------------------------------------
# Paths that are never measured
# ---------------------------------------------------------------------------


@pytest.fixture
def check_calls(monkeypatch) -> list[Any]:
    """Every call the engine makes to the check, recorded; the real check
    still runs."""
    calls: list[Any] = []
    real = cost_checks.check_warm_leads

    def recording(lineages):
        calls.append(list(lineages))
        return real(lineages)

    monkeypatch.setattr(cost_checks, "check_warm_leads", recording)
    return calls


def _stop_after_submission() -> tuple[dict[str, Any], dict[str, Any]]:
    stop = threading.Event()
    return {"on_create": lambda _requests: stop.set()}, {"should_stop": stop.is_set}


def _refuse_submission() -> tuple[dict[str, Any], dict[str, Any]]:
    def refuse(_requests):
        raise _bad_request()

    return {"on_create": refuse}, {}


def _unreadable_results() -> tuple[dict[str, Any], dict[str, Any]]:
    def unreadable(_batch_id):
        raise RuntimeError("results stream dropped")

    return {"on_results": unreadable}, {}


@pytest.mark.parametrize(
    "hooks",
    [
        pytest.param(_stop_after_submission, id="stop-settlement-window"),
        pytest.param(_refuse_submission, id="refused-submission"),
        pytest.param(_unreadable_results, id="failed-results-read"),
    ],
)
def test_a_phase_nobody_finished_is_never_measured(
    monkeypatch, check_calls, hooks
) -> None:
    _minimums_at_the_floor(monkeypatch)
    client_hooks, run_kwargs = hooks()
    titles, scripts = _doc_scripts([_WROTE] * 9)
    client = _LeadClient(scripts, **client_hooks)
    result = _run(client, **run_kwargs)

    assert client.streamed == [titles[0]]
    assert result.execution_status == "partial"
    assert check_calls == []
    assert _warm_block()["last_check"] is None
    assert cost_checks.warm_lead_enabled()


def test_an_id_less_submission_is_never_measured(monkeypatch, check_calls) -> None:
    _minimums_at_the_floor(monkeypatch)
    titles, scripts = _doc_scripts([_WROTE] * 9)
    client = _LeadClient(scripts)
    client.batches.create = lambda *, requests: SimpleNamespace(
        id="", processing_status="in_progress", request_counts=None
    )
    result = _run(client)

    assert client.streamed == [titles[0]]
    assert result.execution_status == "partial"
    assert check_calls == []
    assert cost_checks.warm_lead_enabled()


def test_the_wall_clock_ceiling_is_never_measured(monkeypatch, check_calls) -> None:
    _minimums_at_the_floor(monkeypatch)
    clock = _LeapClock(on_leap=lambda: None)
    monkeypatch.setattr(engine, "time", clock)
    titles, scripts = _doc_scripts([_WROTE] * 9)
    # Round 1 leaves one batched seat paused, so the phase needs a round 2,
    # whose wall-clock check finds the ceiling passed.
    scripts[titles[1]][0] = pause_response()

    def arm(_batch_id) -> None:
        clock.armed = True

    client = _LeadClient(scripts, on_results=arm)
    sink = _Sink()
    _run(client, sink=sink)

    assert sink.of("verification_batch")[-1]["status"] == "timeout"
    assert check_calls == []
    assert cost_checks.warm_lead_enabled()


def test_a_stop_during_the_lead_wait_is_never_measured(monkeypatch, check_calls) -> None:
    _minimums_at_the_floor(monkeypatch)
    stop = threading.Event()

    def before_first(_title: str) -> None:
        stop.set()

    titles, scripts = _doc_scripts([_WROTE] * 9)
    client = _LeadClient(scripts, before_first=before_first)
    _run(client, should_stop=stop.is_set)

    assert client.streamed == [titles[0]]
    assert client.batches.created == []
    assert check_calls == []


def test_a_lead_that_sent_nothing_is_not_measured_on_a_normal_end(
    monkeypatch, check_calls
) -> None:
    """The phase ends normally, but its lead returned without sending a
    request, so it is not a streamed lead: priced with the batch, and never
    handed to the check."""
    _minimums_at_the_floor(monkeypatch)
    real_call = engine._run_streaming_call

    def sent_nothing(*args: Any, **kwargs: Any):
        if kwargs.get("event_prefix") == "verifier":
            return engine._CallResult(None, [], [], "Cancelled by user.", 0)
        return real_call(*args, **kwargs)

    monkeypatch.setattr(engine, "_run_streaming_call", sent_nothing)
    titles, scripts = _doc_scripts([_WROTE] * 9)
    client = _LeadClient(scripts)
    result = _run(client)

    assert client.streamed == []
    assert len(_batched_ids(client)) == 9
    lead = _verdicts(result)[(titles[0], 1)]
    assert lead.api_request_count == 0
    assert lead.cost_multiplier == settings.BATCH_COST_MULTIPLIER
    assert check_calls == []
    assert _warm_block()["last_check"] is None


def test_the_latch_stops_the_next_phase_picking_a_lead(monkeypatch) -> None:
    _minimums_at_the_floor(monkeypatch)
    cost_checks.disable_warm_lead(reason="not_read")
    _titles_used, scripts = _doc_scripts()
    client = _LeadClient(scripts)
    _run(client)

    assert client.streamed == []
    assert len(_batched_ids(client)) == 10


def test_gathering_for_the_check_never_fails_the_phase(monkeypatch, caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="buildaspec.qc")
    _minimums_at_the_floor(monkeypatch)

    def refuse(**_kwargs: Any):
        raise RuntimeError("cannot build a lineage")

    monkeypatch.setattr(cost_checks, "WarmLeadLineage", refuse)
    _titles_used, scripts = _doc_scripts([_WROTE] * 9)
    result = _run(_LeadClient(scripts))

    assert result.execution_status == "complete"
    assert cost_checks.warm_lead_enabled()
    assert _warm_block()["last_check"] is None
    assert any("could not gather the warm lead check" in m for m in caplog.messages)


# ---------------------------------------------------------------------------
# It changes nothing else
# ---------------------------------------------------------------------------


def _timeless(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _timeless(v) for k, v in value.items() if k != "duration_ms"}
    if isinstance(value, list):
        return [_timeless(v) for v in value]
    return value


def _event_log(sink: _Sink) -> list[str]:
    """Every event, in a comparable form. Phase 1's ``done`` counter is
    dropped: it counts lenses in the order their threads finish, which two
    runs need not share; nothing the check could touch."""
    return sorted(
        json.dumps(
            {k: v for k, v in event.items() if k != "done"},
            sort_keys=True,
            default=repr,
        )
        for event in sink.events
    )


def test_the_check_changes_no_request_record_multiplier_meter_event_or_manifest(
    monkeypatch,
) -> None:
    """The same latching phase twice: once with the check patched out, once
    with the real check, which switches the lead off. Everything the run
    produced is identical; only the next phase is different.

    Patched out means the engine's hand-off too, not only the check: the
    lineage it would hand over is never built, so nothing after the phase's
    own reads runs in the baseline — and a hand-off that wrote anything back
    would show up here as a difference."""
    _fixed_clock(monkeypatch)
    _minimums_at_the_floor(monkeypatch)
    store = _store()
    runs = []

    def patched_out(**_kwargs: Any):
        raise RuntimeError("the check is patched out")

    for real in (False, True):
        with monkeypatch.context() as patch:
            if not real:
                patch.setattr(cost_checks, "WarmLeadLineage", patched_out)
                patch.setattr(cost_checks, "check_warm_leads", lambda _lineages: None)
            _titles_used, scripts = _doc_scripts([_WROTE] * 9)
            client = _LeadClient(scripts)
            sink = _Sink()
            result = _run(client, store=store, sink=sink)
        runs.append((client, sink, result))
        assert cost_checks.warm_lead_enabled() is (not real)

    (off_client, off_sink, off), (on_client, on_sink, on) = runs
    assert _canonical_requests(off_client) == _canonical_requests(on_client)
    assert off_client.batches.created == on_client.batches.created
    # ``duration_ms`` is the one field a real clock writes: the run's own
    # wall time, which two runs never share.
    assert _timeless(off.to_dict()) == _timeless(on.to_dict())
    assert off.usage_by_meter_category() == on.usage_by_meter_category()
    assert off.input_manifest == on.input_manifest
    assert off.input_fingerprint == on.input_fingerprint
    assert sorted(v.cost_multiplier for v in _verdicts(off).values()) == sorted(
        v.cost_multiplier for v in _verdicts(on).values()
    )
    assert _event_log(off_sink) == _event_log(on_sink)
    assert "warm" not in json.dumps(on.input_manifest).lower()


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


def _keys(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _keys(child)


def test_diagnostics_report_the_last_check_and_survive_the_scrub(monkeypatch) -> None:
    """The block is reported whole, unchanged by ``scrub_data`` — a lineage's
    values sit six levels down in the diagnostics payload, the scrub's
    bound — no key trips the credential pattern, and ``/api/diagnostics``
    serves the same block."""
    monkeypatch.setattr(settings, "QC_BATCH_WARM_LEAD", True)
    clear = cost_checks.snapshot()["warm_lead"]
    assert clear == {
        "setting_on": True,
        "enabled": True,
        "reason": "",
        "detail": "",
        "since": None,
        "last_check": None,
    }

    _minimums_at_the_floor(monkeypatch)
    _titles_used, scripts = _doc_scripts([_WROTE] * 9)
    _run(_LeadClient(scripts))
    raw = cost_checks.snapshot()
    warm = raw["warm_lead"]
    assert warm["setting_on"] is True
    assert (warm["enabled"], warm["reason"]) == (False, "not_read")
    assert warm["last_check"]["lineages"][0]["verdict"] == "not_read"

    assert scrub_data(raw) == raw
    assert diagnostics.snapshot()["cost_checks"] == raw
    assert not [key for key in _keys(raw) if _SECRET_KEY_PATTERN.search(str(key))]
    served = TestClient(create_app()).get("/api/diagnostics").json()
    assert served["cost_checks"] == raw

    # A reader cannot edit the module's record through what it was handed.
    raw["warm_lead"]["last_check"]["lineages"][0]["verdict"] = "edited"
    assert cost_checks.snapshot()["warm_lead"]["last_check"]["lineages"][0][
        "verdict"
    ] == "not_read"

    monkeypatch.setattr(settings, "QC_BATCH_WARM_LEAD", False)
    assert cost_checks.snapshot()["warm_lead"]["setting_on"] is False
