"""The continuation tail measures what it saves (Tier 1 finish, CT-2).

Each engine hands every response to a request that actually carried the
continuation tail, with its conversation's opening response, to
``cost_checks.observe_continuation``. The check reads the provider's usage
and records one observation (the Chunk 4 plan's Appendix A):

- **exact** — the continuation ran one model iteration and the opening
  response's first iteration is known: S = R·(u − r) − W·(w − u);
- **bound** — the continuation ran one model iteration and read something,
  but the opening's first iteration is unknown: S_max = read·(u − r) −
  write·(w − u), an upper bound on S;
- **unmeasured** — anything else, counted and never guessed.

Once an engine has six or more measured observations summing below zero,
its tail switches off (``unprofitable``) for the rest of the app session.

What this file pins: the arithmetic, by hand, on Claude Sonnet 5 (research)
and Claude Opus 5.5 (Final QC); the two cases the plan's exact formula
cannot see and the check therefore leaves unmeasured (a continuation that
ran more than one iteration, and one that answers a pending server tool
call); the latch rule; the hooks, end to end on both engines, including
that a tail-free resend and an opening response are never observed; that
the measurement changes no request, record, usage total or manifest; the
diagnostics block; and that the rates come from the ledger.

Every engine run passes ``continuation_cache=True`` explicitly — the
runners take it as a required keyword — so CT-3's flip of the default
changes none of them.
"""
from __future__ import annotations

import copy
import logging
import math
import threading
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend import cost_checks, diagnostics, settings, usage_ledger
from backend.app import create_app
from backend.qc.engine import QCResult, run_final_qc
from backend.research import run_requirements_research
from backend.spec_modules import DEFAULT_MODULE as QC_MODULE
from backend.spec_modules.hyperscale_fire import HYPERSCALE_FIRE as RESEARCH_MODULE
from backend.tracing.redaction import _SECRET_KEY_PATTERN, scrub_data
from tests.fakes import (
    SequencedFakeClient,
    qc_findings_response,
    qc_verdict_response,
    research_response,
    search_result_block,
    tool_use_block,
    usage,
    user_text,
)
from tests.test_continuation_cache import _canonical, _fixed_clocks
from tests.test_cost_checks_tail_rejection import _CountingLock, _refused
from tests.test_qc_live_events import _finding, _lens_requests, _store
from tests.test_qc_live_events import _scripts as _qc_scripts
from tests.test_research_engine import DIM_KEYS, PROFILE, _item
from tests.test_research_engine import _scripts as _research_scripts

_PAGE = "https://a.gov/one"
# The models the hand-computed savings are priced on (Appendix A.1). Named
# here rather than read from settings, so an operator's model override
# cannot move the numbers under the assertions.
_SONNET = "claude-sonnet-5"
_OPUS = "claude-opus-5-5"
_RESEARCH = cost_checks.ENGINE_RESEARCH
_QC = cost_checks.ENGINE_QC
_TAIL = {"type": "ephemeral"}
# Bounds a wait that only a broken build would ever reach.
_BOUND = 10.0


@pytest.fixture(autouse=True)
def _published_rates() -> None:
    """The rates every hand-computed saving below assumes (per million:
    input, cache read, 5-minute cache write). If a price moves, this says
    so first, instead of every arithmetic test failing for a reason it
    does not name."""
    per_million = {
        model: tuple(
            round(usage_ledger.model_rates(model)[name] * 1_000_000, 6)
            for name in ("input", "cache_read", "cache_write")
        )
        for model in (_SONNET, _OPUS)
    }
    assert per_million == {_SONNET: (2.0, 0.2, 2.5), _OPUS: (4.0, 0.2, 5.0)}


# ---------------------------------------------------------------------------
# Scripted responses
# ---------------------------------------------------------------------------


def _iteration(
    read: int, write: int, *, as_object: bool = False, kind: str = "message"
) -> Any:
    """One ``usage.iterations`` entry: a dict (an extra field on the GA
    ``Usage``) or an object (the beta ``BetaMessageIterationUsage``)."""
    entry = {
        "type": kind,
        "input_tokens": 3,
        "output_tokens": 2,
        "cache_read_input_tokens": read,
        "cache_creation_input_tokens": write,
    }
    return SimpleNamespace(**entry) if as_object else entry


def _one_iteration(
    *, read: int, write: int, iterations: list | None = None
) -> SimpleNamespace:
    """A response from ONE model iteration: thinking, text and a client tool
    call, with no server tool run — so its top-level usage is that
    iteration's."""
    return SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking="Enough.", signature="sig"),
            SimpleNamespace(type="text", text="Submitting."),
            tool_use_block(
                "toolu_submit",
                "submit_requirements_research",
                {"summary": "", "items": []},
            ),
        ],
        stop_reason="tool_use",
        usage=usage(
            input=40,
            output=900,
            cache_read=read,
            cache_write=write,
            iterations=iterations,
        ),
    )


def _searched(
    *, read: int = 0, write: int = 0, iterations: list | None = None
) -> SimpleNamespace:
    """A response whose request ran a server-side search: its top-level
    usage sums every iteration of that loop."""
    use_id = "srvtoolu_value_check"
    return SimpleNamespace(
        content=[
            SimpleNamespace(
                type="server_tool_use",
                id=use_id,
                name="web_search",
                input={"query": "adopted edition"},
            ),
            search_result_block([_PAGE], tool_use_id=use_id),
            SimpleNamespace(type="text", text="Still looking."),
        ],
        stop_reason="pause_turn",
        usage=usage(
            1,
            0,
            input=100,
            output=300,
            cache_read=read,
            cache_write=write,
            iterations=iterations,
        ),
    )


def _answers_an_earlier_call(
    *, read: int, write: int, iterations: list | None = None
) -> SimpleNamespace:
    """A continuation that resumed a PENDING search: it opens with the result
    of a call the previous response made, which the provider ran first."""
    return SimpleNamespace(
        content=[
            search_result_block([_PAGE], tool_use_id="srvtoolu_from_the_pause"),
            SimpleNamespace(type="text", text="Found it."),
            tool_use_block(
                "toolu_submit",
                "submit_requirements_research",
                {"summary": "", "items": []},
            ),
        ],
        stop_reason="tool_use",
        usage=usage(
            0,
            0,
            input=40,
            output=900,
            cache_read=read,
            cache_write=write,
            iterations=iterations,
        ),
    )


def _opening_with_iterations(*, as_object: bool = False) -> SimpleNamespace:
    """An opening response whose first iteration read nothing and wrote the
    explicit prefix (10,000 tokens), reported through ``usage.iterations``."""
    return _searched(
        write=12_000,
        iterations=[
            _iteration(0, 10_000, as_object=as_object),
            _iteration(10_000, 2_000, as_object=as_object),
        ],
    )


def _pause(*, tokens: dict | None = None) -> SimpleNamespace:
    """A paused turn for either engine: one search and its result."""
    return research_response(
        items=None,
        queries=["still looking"],
        searched_urls=[_PAGE],
        stop_reason="pause_turn",
        tokens=tokens or {"input": 100},
    )


def _research_done(tokens: dict) -> SimpleNamespace:
    """Research's closing turn, from one model iteration (no search)."""
    return research_response(
        items=[_item("Resumed.", [_PAGE])], searched_urls=None, tokens=tokens
    )


def _lens_done(tokens: dict) -> SimpleNamespace:
    """The compliance lens's closing turn, from one model iteration."""
    return qc_findings_response("code_compliance", findings=[], tokens=tokens)


def _same(got: list, expected: list) -> bool:
    """The very objects, in order (a fake's responses compare equal by value)."""
    return len(got) == len(expected) and all(a is b for a, b in zip(got, expected))


def _observe(response: Any, *, opening: Any = None, engine: str = _RESEARCH) -> None:
    cost_checks.observe_continuation(
        engine,
        model=_SONNET if engine == _RESEARCH else _OPUS,
        opening=opening if opening is not None else _searched(),
        response=response,
    )


def _entry(engine: str = _RESEARCH) -> dict[str, Any]:
    return cost_checks.snapshot()["continuation_tail"][engine]


def _counts(engine: str = _RESEARCH) -> tuple[int, int, int, int]:
    entry = _entry(engine)
    return (entry["measured"], entry["exact"], entry["bound"], entry["unmeasured"])


def _warnings(caplog) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if record.name == "buildaspec.cost_checks"
        and record.levelno == logging.WARNING
    ]


# A bound observation that loses money: a thousand tokens read, a hundred
# thousand written. 1,000 × 1.8 − 100,000 × 0.5 per million = −$0.0482.
_LOSS = Decimal("-0.0482")


def _loss() -> SimpleNamespace:
    return _one_iteration(read=1_000, write=100_000)


# ---------------------------------------------------------------------------
# Runners (``continuation_cache`` is always a required keyword)
# ---------------------------------------------------------------------------


def _run_research(client: Any, *, continuation_cache: bool):
    return run_requirements_research(
        RESEARCH_MODULE,
        PROFILE,
        client,
        model=_SONNET,
        max_tokens=4096,
        continuation_cache=continuation_cache,
    )


def _run_qc(client: Any, *, continuation_cache: bool, store=None):
    store = store or _store()
    return run_final_qc(
        store.doc,
        None,
        QC_MODULE,
        client,
        model=_OPUS,
        max_tokens=4096,
        version_index=store.index,
        started_at="2026-09-24T10:00:00-07:00",
        finished_at="2026-09-24T10:01:00-07:00",
        run_id="qc-tail-value-test",
        # The streamed transport, so a verifier seat streams too.
        batch_verification=False,
        batch_warm_lead=False,
        warm_wait_seconds=0,
        continuation_cache=continuation_cache,
    )


def _governing_codes_requests(client: SequencedFakeClient) -> list[dict]:
    key = DIM_KEYS["governing_codes"]
    return [r for r in client.requests if key in user_text(r["messages"])]


@pytest.fixture
def observed(monkeypatch) -> list[tuple[str, str, Any, Any]]:
    """Every call the engines make, recorded, then passed through."""
    calls: list[tuple[str, str, Any, Any]] = []
    real = cost_checks.observe_continuation

    def record(engine: str, *, model: str, opening: Any, response: Any) -> None:
        calls.append((engine, model, opening, response))
        real(engine, model=model, opening=opening, response=response)

    monkeypatch.setattr(cost_checks, "observe_continuation", record)
    return calls


# ---------------------------------------------------------------------------
# The arithmetic (Appendix A)
# ---------------------------------------------------------------------------


def test_an_exact_observation_measures_what_the_tail_read_and_wrote() -> None:
    """The opening's first iteration wrote the explicit prefix (10,000). The
    continuation read 50,000 and wrote 8,000: the tail let it read 40,000
    beyond the prefix and wrote 8,000 of its own.
    40,000 × 1.8 − 8,000 × 0.5 per million = $0.068."""
    opening = _opening_with_iterations()
    response = _one_iteration(read=50_000, write=8_000)
    assert cost_checks._tail_saving(_SONNET, opening, response) == (
        "exact",
        Decimal("0.068"),
    )

    _observe(response, opening=opening)
    assert _counts() == (1, 1, 0, 0)
    assert _entry()["saving_usd"] == 0.068
    assert isinstance(_entry()["last_observed_at"], float)
    assert _counts(_QC) == (0, 0, 0, 0)


def test_an_expired_opening_entry_is_not_charged_to_the_tail() -> None:
    """The continuation read nothing: the explicit prefix's entry had
    expired, so it wrote those 10,000 tokens again as well as its own
    30,000. The rewrite happens with or without the tail (``missed``), so
    only 30,000 are charged: −30,000 × 0.5 per million = −$0.015."""
    opening = _opening_with_iterations()
    assert cost_checks._tail_saving(
        _SONNET, opening, _one_iteration(read=0, write=40_000)
    ) == ("exact", Decimal("-0.015"))
    # Part of the prefix read, the rest written again: the same 30,000.
    assert cost_checks._tail_saving(
        _SONNET, opening, _one_iteration(read=4_000, write=36_000)
    ) == ("exact", Decimal("-0.015"))


def test_a_bound_credits_every_read_and_charges_every_write() -> None:
    """The opening searched without reporting iterations, so its first
    iteration is unknown: 50,000 × 1.8 − 8,000 × 0.5 per million = $0.086.
    That is the exact saving plus the explicit prefix read at the input
    rate (10,000 × 1.8 per million = $0.018): an upper bound."""
    response = _one_iteration(read=50_000, write=8_000)
    kind, bound = cost_checks._tail_saving(_SONNET, _searched(), response)
    assert (kind, bound) == ("bound", Decimal("0.086"))
    _, exact = cost_checks._tail_saving(_SONNET, _opening_with_iterations(), response)
    assert bound - exact == Decimal("0.018")

    _observe(response)
    assert _counts() == (1, 0, 1, 0)
    assert _entry()["saving_usd"] == 0.086


def test_final_qc_is_priced_on_its_own_model() -> None:
    """Opus 5.5: 50,000 × 3.8 − 8,000 × 1.0 per million = $0.182."""
    _observe(_one_iteration(read=50_000, write=8_000), engine=_QC)
    assert _counts(_QC) == (1, 0, 1, 0)
    assert _entry(_QC)["saving_usd"] == 0.182
    assert _counts() == (0, 0, 0, 0)


def test_a_response_that_ran_server_tools_without_iterations_is_unmeasured() -> None:
    """Its top-level usage sums the whole search loop, so it cannot say what
    the first iteration — the one the tail changed — read and wrote."""
    _observe(_searched(read=50_000, write=8_000))
    assert _counts() == (0, 0, 0, 1)
    assert _entry()["saving_usd"] == 0.0


def test_a_bound_with_nothing_read_is_skipped() -> None:
    """With nothing read, the explicit prefix's entry was not alive, and the
    bound's assumption does not hold."""
    _observe(_one_iteration(read=0, write=30_000))
    assert _counts() == (0, 0, 0, 1)


def test_iterations_are_read_as_dicts_and_as_objects() -> None:
    """The first ``message`` entry, whichever shape, past any other kind."""
    expected = {
        "input_tokens": 3,
        "output_tokens": 2,
        "cache_read_input_tokens": 50_000,
        "cache_creation_input_tokens": 8_000,
    }
    for as_object in (False, True):
        response = _searched(
            iterations=[
                _iteration(0, 0, as_object=as_object, kind="compaction"),
                _iteration(50_000, 8_000, as_object=as_object),
                _iteration(58_000, 1_000, as_object=as_object),
            ]
        )
        assert cost_checks.first_iteration_usage(response) == expected

    # And end to end, exact from object entries on both sides.
    opening = _opening_with_iterations(as_object=True)
    response = _one_iteration(
        read=50_000, write=8_000, iterations=[_iteration(50_000, 8_000, as_object=True)]
    )
    assert cost_checks._tail_saving(_SONNET, opening, response) == (
        "exact",
        Decimal("0.068"),
    )


def test_a_continuation_that_ran_more_than_one_iteration_is_unmeasured() -> None:
    """A deviation from the plan's exact formula, in the safe direction.

    A second iteration (after a server tool ran) reads the entry the tail
    wrote in the first, where without the tail it would write it again, so
    the tail saves more on such a request than its first iteration shows.
    The first iteration's saving is then not an upper bound on the
    request's, and summing it could latch a tail that was saving money.
    """
    opening = _opening_with_iterations()
    response = _searched(
        read=108_000,
        write=9_000,
        iterations=[_iteration(10_000, 8_000), _iteration(98_000, 1_000)],
    )
    # The reader still reports the first iteration, as the plan says...
    assert cost_checks.first_iteration_usage(response)["cache_creation_input_tokens"] == 8_000
    # ...and the observation does not use it.
    assert cost_checks._tail_saving(_SONNET, opening, response) == ("unmeasured", None)
    _observe(response, opening=opening)
    assert _counts() == (0, 0, 0, 1)


def test_a_continuation_that_answers_a_pending_call_is_unmeasured() -> None:
    """A deviation from the plan, in the safe direction.

    A continuation that resumes a pending server tool runs the tool first,
    and its first iteration's input is the request PLUS that tool's result,
    behind an automatic breakpoint of the provider's own. Its reads and
    writes are not the tail's: a large fetched page written there would be
    charged to the tail as a loss it never caused.
    """
    opening = _opening_with_iterations()
    one = [_iteration(50_000, 8_000)]
    pending = _answers_an_earlier_call(read=50_000, write=8_000, iterations=one)
    assert cost_checks._tail_saving(_SONNET, opening, pending) == ("unmeasured", None)
    # The same usage without the orphaned result block is measured.
    control = _one_iteration(read=50_000, write=8_000, iterations=one)
    assert cost_checks._tail_saving(_SONNET, opening, control)[0] == "exact"


def test_the_top_level_usage_stands_in_only_when_no_server_tool_ran() -> None:
    """The single-iteration fallback: only the blocks one iteration can
    produce, and no server-tool request of any kind in the usage."""
    single = _one_iteration(read=7, write=9)
    assert cost_checks.first_iteration_usage(single) == {
        "input_tokens": 40,
        "output_tokens": 900,
        "cache_read_input_tokens": 7,
        "cache_creation_input_tokens": 9,
    }

    def variant(**change: Any) -> SimpleNamespace:
        response = copy.deepcopy(single)
        for name, value in change.items():
            setattr(response.usage.server_tool_use, name, value)
        return response

    # A search or a fetch the usage records, even with no block to show it.
    assert cost_checks.first_iteration_usage(variant(web_search_requests=1)) is None
    assert cost_checks.first_iteration_usage(variant(web_fetch_requests=2)) is None
    # Any other server tool's count a newer provider reports.
    assert cost_checks.first_iteration_usage(variant(code_execution_requests=1)) is None
    for block in (
        SimpleNamespace(type="server_tool_use", id="s", name="web_fetch", input={}),
        search_result_block([_PAGE], tool_use_id="s"),
        SimpleNamespace(type="container_upload", file_id="f"),
    ):
        response = copy.deepcopy(single)
        response.content.append(block)
        assert cost_checks.first_iteration_usage(response) is None, block.type

    # No server-tool record at all is no request; a cache count the provider
    # left out was zero.
    bare = copy.deepcopy(single)
    del bare.usage.server_tool_use
    bare.usage.cache_read_input_tokens = None
    assert cost_checks.first_iteration_usage(bare)["cache_read_input_tokens"] == 0


def test_malformed_usage_raises_nothing_and_measures_nothing(
    caplog, monkeypatch
) -> None:
    """A malformed record, a missing one or a wrong type anywhere: never an
    exception, never a measurement, never a latch."""
    caplog.set_level(logging.DEBUG, logger="buildaspec.cost_checks")

    def with_usage(**change: Any) -> SimpleNamespace:
        response = _one_iteration(read=1_000, write=100_000)
        for name, value in change.items():
            setattr(response.usage, name, value)
        return response

    garbage: list[Any] = [
        None,
        "text",
        SimpleNamespace(content=[], stop_reason="tool_use"),
        SimpleNamespace(content="not a list", usage=usage()),
        with_usage(input_tokens="12"),
        with_usage(input_tokens=None),
        with_usage(output_tokens=True),
        with_usage(cache_read_input_tokens=-1),
        with_usage(cache_creation_input_tokens=1.5),
        with_usage(iterations=[{"type": "message", "input_tokens": True}]),
        with_usage(iterations=[{"type": "message"}]),
        with_usage(iterations=[]),
        with_usage(iterations=[None, 7, "message"]),
        with_usage(server_tool_use=SimpleNamespace(web_search_requests="1")),
    ]
    for response in garbage:
        assert cost_checks.first_iteration_usage(response) is None
        for opening in (None, "text", response):
            before = copy.deepcopy(vars(response)) if hasattr(response, "__dict__") else None
            for _ in range(7):
                cost_checks.observe_continuation(
                    _RESEARCH, model=_SONNET, opening=opening, response=response
                )
            if before is not None:
                assert copy.deepcopy(vars(response)) == before

    entry = _entry()
    assert entry["measured"] == 0 and entry["saving_usd"] == 0.0
    assert entry["enabled"] is True
    assert not _warnings(caplog)

    # A failure inside the check itself is logged at DEBUG and records nothing.
    unmeasured = entry["unmeasured"]

    def boom(model: str) -> dict:
        raise RuntimeError("rates unavailable")

    caplog.clear()
    monkeypatch.setattr(usage_ledger, "model_rates", boom)
    cost_checks.observe_continuation(
        _RESEARCH,
        model=_SONNET,
        opening=_searched(),
        response=_one_iteration(read=1, write=1),
    )
    assert _entry()["unmeasured"] == unmeasured and _entry()["measured"] == 0
    assert any(
        record.levelno == logging.DEBUG and "could not observe" in record.getMessage()
        for record in caplog.records
    )
    # An engine it does not know: nothing recorded anywhere, nothing raised.
    cost_checks.observe_continuation(
        "chat", model=_SONNET, opening=_searched(), response=_loss()
    )
    assert _counts(_QC) == (0, 0, 0, 0)


# ---------------------------------------------------------------------------
# The latch
# ---------------------------------------------------------------------------


def test_five_losing_observations_do_not_latch_and_the_sixth_does(caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="buildaspec.cost_checks")
    for _ in range(5):
        _observe(_loss())
    assert cost_checks.continuation_tail_enabled(_RESEARCH)
    assert not _warnings(caplog)

    _observe(_loss())
    assert not cost_checks.continuation_tail_enabled(_RESEARCH)
    entry = _entry()
    assert entry["reason"] == "unprofitable" and entry["enabled"] is False
    assert entry["detail"] == (
        "6 measured continuations (0 exact, 6 bound) cost an estimated "
        "$0.289200 more than they saved, even on the most generous reading."
    )
    assert isinstance(entry["since"], float)
    assert entry["saving_usd"] == float(6 * _LOSS)
    (warning,) = _warnings(caplog)
    message = warning.getMessage()
    for words in ("continuation tail", "research", "unprofitable", "$0.289200"):
        assert words in message
    # One engine's loss is that engine's alone.
    assert cost_checks.continuation_tail_enabled(_QC)


def test_unmeasured_observations_never_count_toward_the_six() -> None:
    """Twenty unmeasured observations first, so a rule that counted them
    would latch at the very first loss; then five losses, which must not."""
    for _ in range(20):
        _observe(_searched(read=1_000, write=100_000))
    for _ in range(5):
        _observe(_loss())
    assert _counts() == (5, 0, 5, 20)
    assert cost_checks.continuation_tail_enabled(_RESEARCH)


def test_a_sum_at_or_above_zero_never_latches() -> None:
    """Twelve observations that exactly break even (5 read × 1.8 = 18
    written × 0.5 per million) sum to exactly zero — not a hair below it,
    which float arithmetic on the ledger's rates would give — and a mix
    whose win outweighs ten losses sums above it."""
    for _ in range(12):
        _observe(_one_iteration(read=5, write=18))
    assert _counts() == (12, 0, 12, 0)
    saving = _entry()["saving_usd"]
    assert saving == 0.0 and math.copysign(1.0, saving) == 1.0
    assert cost_checks.continuation_tail_enabled(_RESEARCH)

    # The win first, so the running sum is above zero at every step; the
    # rule reads the running sum, never the order the terms came in.
    _observe(_one_iteration(read=1_000_000, write=0), engine=_QC)
    for _ in range(10):
        _observe(_loss(), engine=_QC)
    # Opus 5.5: one win of 1,000,000 × 3.8 and ten losses of 1,000 × 3.8 −
    # 100,000 × 1.0 per million.
    assert _entry(_QC)["saving_usd"] == pytest.approx(10 * (0.0038 - 0.1) + 3.8)
    assert cost_checks.continuation_tail_enabled(_QC)


def test_a_loss_under_a_millionth_of_a_dollar_still_reads_as_a_loss() -> None:
    """The rule reads the exact sum, so a tenth of a millionth of a dollar
    below zero latches — and every figure that reports it keeps its sign:
    the diagnostics round away from zero, never to a saving of 0.0."""
    # 3 × 1.8 − 11 × 0.5 per million = −$0.0000001, then five that break even.
    _observe(_one_iteration(read=3, write=11))
    for _ in range(5):
        _observe(_one_iteration(read=5, write=18))
    entry = _entry()
    assert entry["reason"] == "unprofitable" and entry["enabled"] is False
    assert entry["saving_usd"] == -0.000001
    assert "cost an estimated $0.000001 more than they saved" in entry["detail"]
    # The other direction, and a signed zero.
    assert cost_checks._usd(Decimal("0.0000001")) == 0.000001
    assert math.copysign(1.0, cost_checks._usd(Decimal("-0"))) == 1.0


def test_the_latch_persists_however_many_winning_observations_follow(caplog) -> None:
    caplog.set_level(logging.WARNING, logger="buildaspec.cost_checks")
    for _ in range(6):
        _observe(_loss())
    assert not cost_checks.continuation_tail_enabled(_RESEARCH)
    for _ in range(50):
        _observe(_one_iteration(read=50_000, write=8_000))
    entry = _entry()
    # Observing carries on, for diagnostics; nothing switches it back on.
    assert entry["measured"] == 56 and entry["saving_usd"] > 0
    assert entry["reason"] == "unprofitable" and entry["enabled"] is False
    assert len(_warnings(caplog)) == 1


def test_a_refused_tail_is_never_relabelled_unprofitable(caplog) -> None:
    """The first latch wins, whatever its reason."""
    caplog.set_level(logging.WARNING, logger="buildaspec.cost_checks")
    cost_checks.disable_continuation_tail(_RESEARCH, reason="rejected", detail="400")
    for _ in range(8):
        _observe(_loss())
    entry = _entry()
    assert (entry["reason"], entry["detail"]) == ("rejected", "400")
    assert entry["measured"] == 8
    assert len(_warnings(caplog)) == 1


def test_an_observation_takes_the_one_lock_once(monkeypatch) -> None:
    """Counting, inspecting and latching happen in one acquisition, so a
    reader never sees a count without the latch it earned."""
    lock = _CountingLock()
    monkeypatch.setattr(cost_checks, "_lock", lock)
    for response in [_searched(), *[_loss()] * 6, _loss()]:
        before = lock.taken
        _observe(response)
        assert lock.taken == before + 1
    assert _entry()["reason"] == "unprofitable"


def test_many_threads_observe_without_losing_a_count() -> None:
    start = threading.Barrier(8, timeout=_BOUND)
    failures: list[BaseException] = []

    def observe() -> None:
        try:
            start.wait()
            for _ in range(25):
                _observe(_one_iteration(read=50_000, write=8_000), engine=_QC)
                _observe(_searched(), engine=_QC)
        except BaseException as exc:  # noqa: BLE001 — collected, then asserted
            failures.append(exc)

    threads = [threading.Thread(target=observe) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(_BOUND)
    assert failures == []
    assert _counts(_QC) == (200, 0, 200, 200)
    assert _entry(_QC)["saving_usd"] == pytest.approx(200 * 0.182)


# ---------------------------------------------------------------------------
# The hooks, end to end
# ---------------------------------------------------------------------------


def test_a_paused_research_dimension_observes_each_continuation(observed) -> None:
    """Three requests: the opening (no tail), a continuation that searched
    again (tail, unmeasured), and the closing continuation from one
    iteration (tail, bound: 50,000 × 1.8 − 8,000 × 0.5 per million)."""
    turns = [
        _pause(),
        _pause(),
        _research_done({"input": 40, "cache_read": 50_000, "cache_write": 8_000}),
    ]
    client = SequencedFakeClient(_research_scripts(governing_codes=list(turns)))
    profile = _run_research(client, continuation_cache=True)

    (status,) = [
        s for s in profile.dimension_statuses if s.dimension_id == "governing_codes"
    ]
    assert status.status == "completed"
    requests = _governing_codes_requests(client)
    assert ["cache_control" in r for r in requests] == [False, True, True]
    # Exactly the two responses to tail-bearing requests, each with its
    # conversation's opening response — the very objects the engine saw.
    assert [(engine, model) for engine, model, _, _ in observed] == [
        (_RESEARCH, _SONNET),
        (_RESEARCH, _SONNET),
    ]
    assert _same([opening for _, _, opening, _ in observed], [turns[0], turns[0]])
    assert _same([response for *_, response in observed], turns[1:])
    assert _counts() == (1, 0, 1, 1)
    assert _entry()["saving_usd"] == 0.086
    assert _counts(_QC) == (0, 0, 0, 0)


def test_reported_iterations_make_a_research_observation_exact() -> None:
    """With ``usage.iterations`` on both responses, the same closing turn is
    exact: 40,000 × 1.8 − 8,000 × 0.5 per million = $0.068."""
    opening = _pause(
        tokens={
            "input": 100,
            "cache_write": 12_000,
            "iterations": [_iteration(0, 10_000), _iteration(10_000, 2_000)],
        }
    )
    done = _research_done(
        {
            "input": 40,
            "cache_read": 50_000,
            "cache_write": 8_000,
            "iterations": [_iteration(50_000, 8_000)],
        }
    )
    client = SequencedFakeClient(_research_scripts(governing_codes=[opening, done]))
    _run_research(client, continuation_cache=True)
    assert _counts() == (1, 1, 0, 0)
    assert _entry()["saving_usd"] == 0.068


def test_a_paused_compliance_lens_observes_its_continuations(observed) -> None:
    """Final QC's lens, on Opus 5.5: 50,000 × 3.8 − 8,000 × 1.0 per million."""
    turns = [
        _pause(),
        _pause(),
        _lens_done({"input": 40, "cache_read": 50_000, "cache_write": 8_000}),
    ]
    client = SequencedFakeClient(_qc_scripts(code_compliance=list(turns)))
    result = _run_qc(client, continuation_cache=True)

    (lens,) = [s for s in result.lens_statuses if s.lens_id == "code_compliance"]
    assert lens.status == "completed"
    assert ["cache_control" in r for r in _lens_requests(client, "code_compliance")] == [
        False,
        True,
        True,
    ]
    assert [(engine, model) for engine, model, _, _ in observed] == [
        (_QC, _OPUS),
        (_QC, _OPUS),
    ]
    assert _same([opening for _, _, opening, _ in observed], [turns[0], turns[0]])
    assert _same([response for *_, response in observed], turns[1:])
    assert _counts(_QC) == (1, 0, 1, 1)
    assert _entry(_QC)["saving_usd"] == 0.182
    assert _counts() == (0, 0, 0, 0)


def test_a_streamed_web_tooled_seat_observes_its_continuation(monkeypatch, observed) -> None:
    """A compliance finding's first seat pauses, then closes from one
    iteration: 20,000 × 3.8 − 5,000 × 1.0 per million = $0.071."""
    monkeypatch.setattr(settings, "QC_MAX_WORKERS", 1)
    title = "Seat value candidate"
    scripts = _qc_scripts(
        code_compliance=[
            qc_findings_response("code_compliance", findings=[_finding(title)])
        ]
    )
    seat_opening = _pause()
    seat_done = qc_verdict_response(
        True, tokens={"input": 30, "cache_read": 20_000, "cache_write": 5_000}
    )
    scripts[title] = [seat_opening, seat_done, qc_verdict_response(True)]
    client = SequencedFakeClient(scripts)
    result = _run_qc(client, continuation_cache=True)

    assert [finding.title for finding in result.findings] == [title]
    seats = [r for r in client.requests if "[[QC-VERIFY:" in user_text(r["messages"])]
    assert ["cache_control" in r for r in seats] == [False, True, False]
    ((engine, model, opening, response),) = observed
    assert (engine, model) == (_QC, _OPUS)
    assert opening is seat_opening and response is seat_done
    assert _counts(_QC) == (1, 0, 1, 0)
    assert _entry(_QC)["saving_usd"] == 0.071


def _research_run(turns: list, *, continuation_cache: bool):
    client = SequencedFakeClient(_research_scripts(governing_codes=list(turns)))
    profile = _run_research(client, continuation_cache=continuation_cache)
    return client, profile


def _qc_run(turns: list, *, continuation_cache: bool, store=None):
    client = SequencedFakeClient(_qc_scripts(code_compliance=list(turns)))
    result = _run_qc(client, continuation_cache=continuation_cache, store=store)
    return client, result


_ENGINES = [
    pytest.param((_RESEARCH, _research_run, _research_done), id="research"),
    pytest.param((_QC, _qc_run, _lens_done), id="qc"),
]


@pytest.mark.parametrize("engine", _ENGINES)
def test_a_tail_free_resend_is_never_observed(engine, observed) -> None:
    """CT-1's resend after a refusal carries no tail, so nothing credits the
    tail with it: no observation at all, not even an unmeasured one."""
    name, run, done = engine
    run(
        [_pause(), _refused(), done({"cache_read": 50_000, "cache_write": 8_000})],
        continuation_cache=True,
    )
    assert _entry(name)["reason"] == "rejected"
    assert observed == []
    assert _counts(name) == (0, 0, 0, 0)
    assert _entry(name)["last_observed_at"] is None


@pytest.mark.parametrize("engine", _ENGINES)
def test_an_opening_response_is_never_observed(engine, observed) -> None:
    """A call that completes on its first request sent no continuation, and a
    paused call with the switch off sent no tail: neither is observed."""
    name, run, done = engine
    run([done({"cache_read": 50_000, "cache_write": 8_000})], continuation_cache=True)
    run([_pause(), done({"cache_read": 50_000})], continuation_cache=False)
    assert observed == []
    assert _counts(name) == (0, 0, 0, 0)


@pytest.mark.parametrize("engine", _ENGINES)
def test_an_unprofitable_latch_takes_the_tail_off_the_next_request(engine, observed) -> None:
    """Latched ``unprofitable``, the engine's next continuation goes without
    the tail — the latch is read on every request — and is not observed."""
    name, run, done = engine
    for _ in range(6):
        _observe(_loss(), engine=name)
    assert _entry(name)["reason"] == "unprofitable"
    observed.clear()
    client, _ = run([_pause(), done({"cache_read": 1})], continuation_cache=True)
    assert not [r for r in client.requests if "cache_control" in r]
    assert observed == []
    assert _entry(name)["measured"] == 6


# What differs between two identical runs by construction: a research
# round's id (uuid4, minted per run) and Final QC's wall-clock durations.
_PER_RUN_KEYS = frozenset({"round_id", "duration_ms"})


def _comparable(value: Any) -> Any:
    """A record minus the fields no two runs share, however alike."""
    if isinstance(value, dict):
        return {
            k: _comparable(v) for k, v in value.items() if k not in _PER_RUN_KEYS
        }
    if isinstance(value, list):
        return [_comparable(v) for v in value]
    return value


def test_the_measurement_is_invisible(monkeypatch) -> None:
    """The plan's F3 and F4, and R6: with ``observe_continuation`` a no-op,
    every request, record, usage total, meter bucket, input manifest and
    fingerprint is exactly what the measuring runs produced. Both engines,
    one continuation measured exact, one bound, one unmeasured."""
    _fixed_clocks(monkeypatch)
    # The transport IS a review input; compare under the one these runs use.
    monkeypatch.setattr(settings, "QC_BATCH_VERIFICATION", False)
    store = _store()

    research_turns = [
        _pause(
            tokens={
                "input": 100,
                "cache_write": 12_000,
                "iterations": [_iteration(0, 10_000), _iteration(10_000, 2_000)],
            }
        ),
        _pause(),
        _research_done(
            {
                "input": 40,
                "cache_read": 50_000,
                "cache_write": 8_000,
                "iterations": [_iteration(50_000, 8_000)],
            }
        ),
    ]
    qc_turns = [
        _pause(),
        _lens_done({"input": 40, "cache_read": 50_000, "cache_write": 8_000}),
    ]

    def run_both() -> tuple:
        # The same scripted objects both times, so the two runs differ in
        # nothing but the measurement (a fake's ids are minted per build).
        research_client, profile = _research_run(research_turns, continuation_cache=True)
        qc_client, result = _qc_run(qc_turns, continuation_cache=True, store=store)
        return research_client, profile, qc_client, result

    measured = run_both()
    assert _counts() == (1, 1, 0, 1) and _counts(_QC) == (1, 0, 1, 0)

    monkeypatch.setattr(cost_checks, "observe_continuation", lambda *a, **k: None)
    silent = run_both()

    for index in (0, 2):
        assert _canonical(measured[index].requests) == _canonical(silent[index].requests)
    assert _canonical(_comparable(measured[1].to_dict())) == _canonical(
        _comparable(silent[1].to_dict())
    )
    assert measured[1].usage_total() == silent[1].usage_total()
    assert _canonical(_comparable(measured[3].to_dict())) == _canonical(
        _comparable(silent[3].to_dict())
    )
    assert measured[3].usage_by_meter_category() == silent[3].usage_by_meter_category()
    assert measured[3].input_fingerprint == silent[3].input_fingerprint
    manifest = _canonical(measured[3].input_manifest).lower()
    for word in ("cost_check", "continuation_tail", "unprofitable", "saving"):
        assert word not in manifest
    assert measured[3].matches_inputs(store.index, store.doc, None, QC_MODULE)
    assert QCResult.from_dict(measured[3].to_dict()) is not None


def test_diagnostics_report_the_counts_and_survive_the_scrub(monkeypatch) -> None:
    """``diagnostics.snapshot()["cost_checks"]`` carries each engine's counts
    and saving, unchanged by ``scrub_data`` (no key trips the credential
    pattern), and ``/api/diagnostics`` serves the same block."""
    monkeypatch.setattr(settings, "CONTINUATION_CACHE", True)
    _research_run(
        [
            _pause(),
            _pause(),
            _research_done({"input": 40, "cache_read": 50_000, "cache_write": 8_000}),
        ],
        continuation_cache=True,
    )
    raw = cost_checks.snapshot()
    research = raw["continuation_tail"][_RESEARCH]
    assert {k: research[k] for k in ("measured", "exact", "bound", "unmeasured")} == {
        "measured": 1,
        "exact": 0,
        "bound": 1,
        "unmeasured": 1,
    }
    assert research["saving_usd"] == 0.086
    assert isinstance(research["last_observed_at"], float)
    assert raw["continuation_tail"][_QC]["last_observed_at"] is None

    assert scrub_data(raw) == raw
    assert diagnostics.snapshot()["cost_checks"] == raw

    def keys(value: Any):
        if isinstance(value, dict):
            for key, child in value.items():
                yield key
                yield from keys(child)

    assert not [key for key in keys(raw) if _SECRET_KEY_PATTERN.search(str(key))]
    served = TestClient(create_app()).get("/api/diagnostics").json()
    assert served["cost_checks"] == raw


# ---------------------------------------------------------------------------
# The rates
# ---------------------------------------------------------------------------


def test_the_rates_come_from_the_ledger(monkeypatch) -> None:
    """``usage_ledger.model_rates`` is the ledger's own lookup, fallback
    included, handed out as a copy; the check has no table of its own."""
    for model in [*settings.PRICING, "a-model-nobody-priced"]:
        assert usage_ledger.model_rates(model) == usage_ledger._rates(model)
    assert usage_ledger.model_rates("a-model-nobody-priced") == settings.PRICING[
        settings.MODEL_SONNET_5
    ]
    copied = usage_ledger.model_rates(_SONNET)
    copied["input"] = 999.0
    assert settings.PRICING[_SONNET]["input"] != 999.0

    source = Path(cost_checks.__file__).read_text(encoding="utf-8")
    for lookup in ("PRICING[", "PRICING.get", "_rates("):
        assert lookup not in source.replace("model_rates(", ""), lookup
    assert "usage_ledger.model_rates(" in source

    # Change what the ledger prices at, and the saving follows.
    doubled = {name: rate * 2 for name, rate in settings.PRICING[_SONNET].items()}
    monkeypatch.setattr(usage_ledger, "_rates", lambda model: doubled)
    _observe(_one_iteration(read=50_000, write=8_000))
    assert _entry()["saving_usd"] == 2 * 0.086
