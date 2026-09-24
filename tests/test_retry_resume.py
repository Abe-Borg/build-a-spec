"""Resume, don't restart, on a transient failure (Research/QC cost Tier 1, Chunk 5).

A research area or a Final QC call is a conversation: an opening request,
then a ``pause_turn`` continuation for every pause. A transient failure — a
rate limit, a server error, a dropped connection — can land after that
conversation has made real, paid-for progress, and every retry used to throw
the progress away and pay for every finished continuation again.

The rule is ``retry_policy.retry_mode``: RESUME — send the request that
failed again, inside the same conversation — when the conversation has a
completed response and the retry about to run is not the final attempt;
RESTART otherwise. The final attempt always starts fresh, which is why there
is no switch: a conversation that keeps failing once resumed still gets the
clean attempt it always got.

Research and streamed Final QC run the same assertions through one harness
per engine. The two engines keep separate copies of the loop (the
copy-don't-import posture), so the same test over both is what keeps them
from drifting apart. Then the batched transport's two retry sites, the rule
itself, the mode reaching a run's event log, and the retained Final QC
result staying current (the plan's F3).
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

import anthropic
import httpx
import pytest

from backend import settings
from backend.qc import engine as qc_engine
from backend.qc.engine import run_final_qc
from backend.qc.schema import QC_LENSES
from backend.research import engine as research_engine
from backend.research import run_requirements_research
from backend.research.retry_policy import (
    DEFAULT_REALTIME_RETRY_POLICY,
    RETRY_MODE_RESTART,
    RETRY_MODE_RESUME,
    retry_mode,
)
from backend.spec_modules import DEFAULT_MODULE as QC_MODULE
from backend.spec_modules.hyperscale_fire import HYPERSCALE_FIRE as RESEARCH_MODULE
from tests.fakes import (
    SequencedFakeClient,
    qc_findings_response,
    qc_verdict_response,
    research_response,
)
from tests.test_qc_batch_verification import (
    _one_finding_scripts,
    _rate_limited,
)
from tests.test_qc_batch_verification import _run as _run_batched_qc
from tests.test_qc_live_events import _scripts as _qc_scripts
from tests.test_qc_live_events import _store
from tests.test_research_engine import DIM_KEYS, PROFILE, _item

_RESET = anthropic.APIConnectionError(
    message="connection reset",
    request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
)
_CITED = "Cites the page it read."
_PAGE = "https://example.gov/page-one"
_OTHER = "https://example.gov/page-two"


def _pause(*urls: str, tokens: dict | None = None, container: str | None = None):
    """A paused turn — one finished search, no output tool — on either engine.

    The search is paired (``server_tool_use`` then its result), the shape the
    API sends, so the resend sanitizer keeps it on every later request.
    """
    return research_response(
        items=None,
        queries=["a search that finished"] if urls else None,
        searched_urls=list(urls),
        stop_reason="pause_turn",
        tokens=tokens,
        container=container,
    )


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    """No real seconds: both engines read the one ``time`` module."""
    slept: list[float] = []
    monkeypatch.setattr(research_engine.time, "sleep", slept.append)
    return slept


# ---------------------------------------------------------------------------
# One harness per engine: the call's lifecycle function and its RECORD
# ---------------------------------------------------------------------------


@dataclass
class _Call:
    """What one scripted call produced, read the same way on both engines."""

    requests: list[dict]
    retries: list[dict]
    status: str
    error: str
    input_tokens: int
    searches: int
    grounded: bool
    # Final QC records these per lens; research records neither.
    api_requests: int | None = None
    responses: int | None = None


class _ResearchHarness:
    """One research dimension through ``_run_dimension`` — its whole record."""

    name = "research"
    engine = research_engine
    max_continuations = research_engine.RESEARCH_MAX_CONTINUATIONS
    event_base = {"type": "dimension_retry", "dimension_id": "governing_codes"}

    def final(self, *, cited=(), urls=(), tokens=None):
        return research_response(
            items=[_item(_CITED, list(cited))],
            searched_urls=list(urls),
            tokens=tokens,
        )

    def run(
        self, turns, *, continuation_cache=False, should_stop=lambda: False
    ) -> _Call:
        client = SequencedFakeClient({DIM_KEYS["governing_codes"]: list(turns)})
        events: list[dict] = []
        dimension = next(
            d
            for d in RESEARCH_MODULE.research_dimensions
            if d.dimension_id == "governing_codes"
        )
        outcome = research_engine._run_dimension(
            client,
            module=RESEARCH_MODULE,
            profile=PROFILE,
            dimension=dimension,
            model="claude-sonnet-5",
            max_tokens=4096,
            continuation_cache=continuation_cache,
            event_sink=events.append,
            should_stop=should_stop,
        )
        item = next((i for i in outcome.items if i.requirement == _CITED), None)
        return _Call(
            requests=list(client.requests),
            retries=[e for e in events if e["type"] == "dimension_retry"],
            status=outcome.status.status,
            error=outcome.status.error,
            input_tokens=outcome.status.input_tokens,
            searches=outcome.status.web_search_requests,
            grounded=bool(item and item.grounded),
        )


class _QcHarness:
    """The web-tooled compliance lens through ``_run_lens`` — its record."""

    name = "qc"
    engine = qc_engine
    max_continuations = qc_engine.QC_MAX_CONTINUATIONS
    event_base = {"type": "lens_retry", "lens_id": "code_compliance"}

    def final(self, *, cited=(), urls=(), tokens=None):
        return qc_findings_response(
            "code_compliance",
            findings=[],
            reviewed_checks=[
                {
                    "check": _CITED,
                    "outcome": "passed",
                    "notes": "Checked against the page it read.",
                    "element_ids": [],
                    "source_urls": list(cited),
                }
            ],
            searched_urls=list(urls),
            tokens=tokens,
        )

    def run(
        self, turns, *, continuation_cache=False, should_stop=lambda: False
    ) -> _Call:
        client = SequencedFakeClient({"[[QC-LENS:code_compliance]]": list(turns)})
        events: list[dict] = []
        lens = next(lens for lens in QC_LENSES if lens.lens_id == "code_compliance")
        store = _store()
        outcome = qc_engine._run_lens(
            client,
            lens=lens,
            pieces=qc_engine._lens_call_pieces(
                lens,
                section=store.doc,
                module=QC_MODULE,
                profile=None,
                model=settings.QC_MODEL,
            ),
            model=settings.QC_MODEL,
            max_tokens=4096,
            effort=settings.QC_LENS_EFFORT,
            continuation_cache=continuation_cache,
            event_sink=events.append,
            should_stop=should_stop,
        )
        status = outcome.status
        check = next((c for c in status.reviewed_checks if c.check == _CITED), None)
        return _Call(
            requests=list(client.requests),
            retries=[e for e in events if e["type"] == "lens_retry"],
            status=status.status,
            error=status.error,
            input_tokens=status.usage_totals.get("input_tokens", 0),
            searches=status.usage_totals.get("web_search_requests", 0),
            grounded=bool(
                check
                and check.source_checks
                and all(source.accepted for source in check.source_checks)
            ),
            api_requests=status.api_request_count,
            responses=status.model_response_count,
        )


@pytest.fixture(params=[_ResearchHarness(), _QcHarness()], ids=["research", "qc"])
def harness(request):
    return request.param


def _modes(call: _Call) -> list[str]:
    return [event["mode"] for event in call.retries]


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------


def test_the_rule_resumes_only_with_progress_and_never_on_the_final_attempt():
    """``retry_mode``'s whole truth table, at the shipped attempt ceiling and
    around it. With three attempts the first retry can resume and the second
    — the final attempt — always restarts."""
    assert DEFAULT_REALTIME_RETRY_POLICY.max_attempts == 3
    assert retry_mode(progressed=True, next_attempt=1, attempts=3) == RETRY_MODE_RESUME
    assert retry_mode(progressed=True, next_attempt=2, attempts=3) == RETRY_MODE_RESTART
    assert retry_mode(progressed=False, next_attempt=1, attempts=3) == RETRY_MODE_RESTART
    # Two attempts: the only retry IS the final attempt, so nothing resumes.
    assert retry_mode(progressed=True, next_attempt=1, attempts=2) == RETRY_MODE_RESTART
    # Longer ceilings resume until the last one.
    assert retry_mode(progressed=True, next_attempt=3, attempts=5) == RETRY_MODE_RESUME
    assert retry_mode(progressed=True, next_attempt=4, attempts=5) == RETRY_MODE_RESTART
    # Degenerate ceilings never resume.
    assert retry_mode(progressed=True, next_attempt=0, attempts=1) == RETRY_MODE_RESTART
    assert retry_mode(progressed=True, next_attempt=0, attempts=0) == RETRY_MODE_RESTART
    assert (RETRY_MODE_RESUME, RETRY_MODE_RESTART) == ("resume", "restart")


def test_every_retry_site_reads_the_one_rule():
    """Research, streamed Final QC and batched Final QC cannot disagree about
    when a conversation survives: all three read the same function."""
    assert research_engine.retry_mode is retry_mode
    assert qc_engine.retry_mode is retry_mode


# ---------------------------------------------------------------------------
# Research and streamed Final QC, one assertion set
# ---------------------------------------------------------------------------


def test_a_failure_after_continuations_resumes_the_same_conversation(harness):
    """The request that failed is sent again as it stood — conversation,
    container and all — and no earlier continuation is sent again."""
    call = harness.run(
        [
            _pause(_PAGE, container="cont_resume_1"),
            _pause(_OTHER),
            _RESET,
            harness.final(cited=[_PAGE]),
        ]
    )

    assert call.status == "completed", call.error
    assert len(call.requests) == 4
    opening, first, failed, resumed = call.requests
    assert resumed["messages"] == failed["messages"]
    assert [m["role"] for m in resumed["messages"]] == ["user", "assistant", "assistant"]
    assert resumed["container"] == failed["container"] == "cont_resume_1"
    # The opening request went out exactly once: nothing started over.
    assert sum(len(r["messages"]) == 1 for r in call.requests) == 1
    assert _modes(call) == [RETRY_MODE_RESUME]


def test_the_final_attempt_restarts_fresh(harness):
    """A resumed conversation that fails again gets the clean attempt every
    retry used to get: a new conversation from the opening request, no
    inherited container — and the abandoned conversation's evidence is
    billed but grounds nothing."""
    call = harness.run(
        [
            _pause(_PAGE, container="cont_resume_1"),
            _RESET,  # the first retry resumes …
            _RESET,  # … and the final attempt restarts.
            # Cites the page only the abandoned conversation read.
            harness.final(cited=[_PAGE], urls=[_OTHER]),
        ]
    )

    assert call.status == "completed", call.error
    opening, failed, resumed, fresh = call.requests
    assert resumed["messages"] == failed["messages"]
    assert resumed["container"] == "cont_resume_1"
    assert fresh["messages"] == opening["messages"]
    assert [m["role"] for m in fresh["messages"]] == ["user"]
    assert "container" not in fresh
    assert _modes(call) == [RETRY_MODE_RESUME, RETRY_MODE_RESTART]
    assert call.grounded is False


def test_a_failure_before_any_response_restarts(harness):
    """No completed response, no conversation to keep: a restart, exactly as
    every retry used to be."""
    call = harness.run([_RESET, harness.final(cited=[_PAGE], urls=[_PAGE])])

    assert call.status == "completed", call.error
    failed, retried = call.requests
    assert [m["role"] for m in failed["messages"]] == ["user"]
    assert retried["messages"] == failed["messages"]
    assert _modes(call) == [RETRY_MODE_RESTART]


def test_a_resumed_conversation_bills_each_response_once(harness):
    """200 for the pause, 100 for the reply — once each. The failed request
    returned no response, so it bills nothing, but it was SENT, and Final QC's
    client-request count says so."""
    call = harness.run(
        [
            _pause(_PAGE, tokens={"input": 200}),
            _RESET,
            harness.final(cited=[_PAGE], tokens={"input": 100}),
        ]
    )

    assert _modes(call) == [RETRY_MODE_RESUME]
    assert call.input_tokens == 300
    assert call.searches == 1
    if harness.name == "qc":
        assert call.responses == 2
        assert call.api_requests == 3


def test_resumed_retrievals_stay_eligible_for_grounding(harness):
    """A page the conversation read before the failure is a page the model
    saw, so it grounds a citation made after the resume."""
    call = harness.run([_pause(_PAGE), _RESET, harness.final(cited=[_PAGE])])

    assert _modes(call) == [RETRY_MODE_RESUME]
    assert call.grounded is True


def test_the_continuation_budget_spans_a_resume(harness):
    """The budget bounds the CONVERSATION: a resume spends none of it on the
    failed request and earns no second allowance."""
    budget = harness.max_continuations
    before = 10
    turns = [
        *(_pause(_PAGE) for _ in range(before)),
        _RESET,
        *(_pause(_PAGE) for _ in range(budget + 1 - before)),
    ]
    call = harness.run(turns)

    assert call.status == "failed"
    assert "maximum continuation" in call.error
    # The opening request and ``budget`` continuations, plus the one resend.
    assert len(call.requests) == budget + 2
    assert _modes(call) == [RETRY_MODE_RESUME]


def test_the_retry_event_names_its_mode(harness):
    """Exact dicts: the resume, then the final attempt's restart."""
    call = harness.run([_pause(_PAGE), _RESET, _RESET, harness.final()])

    assert call.status == "completed", call.error
    assert call.retries == [
        {
            **harness.event_base,
            "attempt": 1,
            "max_attempts": 3,
            "reason": "connection",
            "backoff_s": 5.0,
            "mode": RETRY_MODE_RESUME,
        },
        {
            **harness.event_base,
            "attempt": 2,
            "max_attempts": 3,
            "reason": "connection",
            "backoff_s": 5.0,
            "mode": RETRY_MODE_RESTART,
        },
    ]


def test_a_resumed_continuation_carries_the_tail_the_failed_one_did(harness):
    """Chunk 4's continuation tail rides the resend exactly as it rode the
    request that failed: one request, sent twice, byte for byte."""
    call = harness.run(
        [_pause(_PAGE), _RESET, harness.final()], continuation_cache=True
    )

    opening, failed, resumed = call.requests
    assert "cache_control" not in opening
    assert failed["cache_control"] == {"type": "ephemeral"}
    assert resumed == failed
    assert _modes(call) == [RETRY_MODE_RESUME]


def test_a_stop_during_a_resume_still_bills_the_conversation(harness, monkeypatch):
    """A Stop landing in the resume's backoff ends the call before its next
    request. The conversation it carried was paid for, so it is billed —
    the stop path used to read only what earlier RESTARTS had abandoned."""
    stop = threading.Event()
    monkeypatch.setattr(research_engine.time, "sleep", lambda _s: stop.set())
    call = harness.run(
        [_pause(_PAGE, tokens={"input": 200}), _RESET], should_stop=stop.is_set
    )

    assert call.status == "failed"
    assert "Cancelled by user." in call.error
    assert _modes(call) == [RETRY_MODE_RESUME]
    assert call.input_tokens == 200
    assert call.searches == 1


def test_a_failure_after_the_response_arrived_restarts(harness, monkeypatch):
    """Only a request's own failure can be sent again as it stood.

    Here the paused response arrived and the resend sanitizer then raised
    (as a retryable class, which nothing real does — it models any
    post-response failure). The conversation is mid-update, so the retry
    restarts, exactly as every retry used to, and the pause stays billed.
    """
    real = harness.engine.sanitize_messages_for_resend
    raised: list[bool] = []

    def flaky(messages):
        if not raised:
            raised.append(True)
            raise _RESET
        return real(messages)

    monkeypatch.setattr(harness.engine, "sanitize_messages_for_resend", flaky)
    call = harness.run(
        [
            _pause(_PAGE, tokens={"input": 200}),
            harness.final(cited=[_PAGE], tokens={"input": 100}),
        ]
    )

    assert call.status == "completed", call.error
    assert _modes(call) == [RETRY_MODE_RESTART]
    opening, fresh = call.requests
    assert fresh["messages"] == opening["messages"]
    assert call.input_tokens == 300
    # The restarted-away pause is billed but no longer the model's evidence.
    assert call.grounded is False


# ---------------------------------------------------------------------------
# The batched transport's two retry sites
# ---------------------------------------------------------------------------


def _record_submissions(client, *, refuse: set[int] = frozenset()) -> list[list[dict]]:
    """Every ``batches.create`` call's requests, refused ones included.

    ``refuse`` names 1-based calls that raise a 429 before reaching the fake.
    Messages are copied at submission, so a later round cannot rewrite what
    an earlier one was asked to send.
    """
    real_create = client.batches.create
    submitted: list[list[dict]] = []

    def create(*, requests):
        submitted.append(
            [
                {
                    "custom_id": r["custom_id"],
                    "params": {
                        **r["params"],
                        "messages": list(r["params"]["messages"]),
                    },
                }
                for r in requests
            ]
        )
        if len(submitted) in refuse:
            raise _rate_limited()
        return real_create(requests=requests)

    client.batches.create = create
    return submitted


def _by_id(requests: list[dict]) -> dict[str, dict]:
    return {r["custom_id"]: r["params"] for r in requests}


def _seat(result, reviewer_index: int):
    candidates = [*result.findings, *result.inconclusive, *result.disputed, *result.refuted]
    (candidate,) = candidates
    return next(v for v in candidate.verdicts if v.reviewer_index == reviewer_index)


def test_a_refused_submission_resubmits_the_same_messages():
    """Nothing ran, so each seat with progress resumes: the next round
    submits exactly the continuation the refused one tried to."""
    client = SequencedFakeClient(
        _one_finding_scripts(
            verdicts=[
                _pause(_PAGE, container="cont_batch_1"),
                _pause(_PAGE, container="cont_batch_1"),
                qc_verdict_response(True),
                qc_verdict_response(True),
            ]
        )
    )
    submitted = _record_submissions(client, refuse={2})
    events: list[dict] = []
    result = _run_batched_qc(client, events=events)

    assert len(submitted) == 3
    refused, resubmitted = _by_id(submitted[1]), _by_id(submitted[2])
    assert refused.keys() == resubmitted.keys() and len(refused) == 2
    for custom_id, params in refused.items():
        assert resubmitted[custom_id]["messages"] == params["messages"]
        assert params["messages"][-1]["role"] == "assistant"
        assert resubmitted[custom_id]["container"] == params["container"] == "cont_batch_1"
    retries = [e for e in events if e["type"] == "verifier_retry"]
    assert [e["mode"] for e in retries] == [RETRY_MODE_RESUME, RETRY_MODE_RESUME]
    assert all(e["attempt"] == 1 for e in retries)
    assert result.execution_status == "complete"
    for index in (1, 2):
        seat = _seat(result, index)
        # The pause and the verdict, once each; the refused round sent nothing.
        assert (seat.api_request_count, seat.model_response_count) == (2, 2)


def test_a_retryable_item_error_resumes_the_seat():
    """A seat whose continuation comes back errored re-submits that same
    continuation, and the page it read before the error still counts as
    evidence it saw."""
    client = SequencedFakeClient(
        _one_finding_scripts(
            verdicts=[
                # Round 1: both seats pause.
                _pause(_PAGE, container="cont_batch_1"),
                _pause(_PAGE, container="cont_batch_1"),
                # Round 2: seat 1's continuation errors; seat 2 answers.
                ConnectionError("peer closed connection"),
                qc_verdict_response(True),
                # Round 3: seat 1's resend answers.
                qc_verdict_response(True),
            ]
        )
    )
    events: list[dict] = []
    result = _run_batched_qc(client, events=events)

    assert len(client.batches.created) == 3
    (seat_one_errored,) = [
        r for r in client.batches.created[1] if r["custom_id"] == client.batches.created[2][0]["custom_id"]
    ]
    (resent,) = client.batches.created[2]
    assert resent["params"]["messages"] == seat_one_errored["params"]["messages"]
    assert resent["params"]["messages"][-1]["role"] == "assistant"
    assert resent["params"]["container"] == "cont_batch_1"
    retries = [e for e in events if e["type"] == "verifier_retry"]
    assert [(e["reviewer_index"], e["attempt"], e["mode"]) for e in retries] == [
        (1, 1, RETRY_MODE_RESUME)
    ]
    assert result.execution_status == "complete"
    seat = _seat(result, 1)
    assert seat.model_response_count == 2
    assert _PAGE in [source.url for source in seat.retrieved_sources]


def test_a_batched_seat_keeps_its_continuation_budget_across_a_resume():
    """The batch's budget is the conversation's too: ten pauses, an errored
    round the seat resumes from, and seven more pauses reach the limit that
    seventeen pauses always reached — the resume earned no second allowance.

    The pauses search nothing, so the 2× search ceiling cannot end the seat
    first and this reads the continuation budget alone.
    """
    budget = qc_engine.QC_MAX_CONTINUATIONS
    before = 10
    client = SequencedFakeClient(
        _one_finding_scripts(
            verdicts=[
                _pause(),  # R1 seat 1
                qc_verdict_response(True),  # R1 seat 2 settles
                *(_pause() for _ in range(before - 1)),  # R2..R10 seat 1
                ConnectionError("peer closed connection"),  # R11: resume
                *(_pause() for _ in range(budget + 1 - before)),  # R12..R18
            ]
        )
    )
    events: list[dict] = []
    result = _run_batched_qc(client, events=events)

    assert [e["mode"] for e in events if e["type"] == "verifier_retry"] == [
        RETRY_MODE_RESUME
    ]
    # The opening request, ``budget`` continuations and the resend: nothing
    # past the limit was ever submitted.
    assert len(client.batches.created) == budget + 2
    seat = _seat(result, 1)
    assert seat.status == "failed"
    assert "maximum continuations" in seat.error
    assert seat.model_response_count == budget + 1


def test_a_batched_seat_on_its_final_attempt_restarts_fresh():
    """The batch keeps the same last resort: a resumed seat that errors again
    starts a fresh conversation, and what the abandoned one read is billed
    evidence only."""
    client = SequencedFakeClient(
        _one_finding_scripts(
            verdicts=[
                _pause(_PAGE, container="cont_batch_1"),  # R1 seat 1
                _pause(_PAGE, container="cont_batch_1"),  # R1 seat 2
                ConnectionError("peer closed connection"),  # R2 seat 1: resume
                qc_verdict_response(True),  # R2 seat 2
                ConnectionError("peer closed connection"),  # R3 seat 1: restart
                qc_verdict_response(True),  # R4 seat 1, fresh
            ]
        )
    )
    events: list[dict] = []
    result = _run_batched_qc(client, events=events)

    assert len(client.batches.created) == 4
    (fresh,) = client.batches.created[3]
    assert [m["role"] for m in fresh["params"]["messages"]] == ["user"]
    assert "container" not in fresh["params"]
    assert [e["mode"] for e in events if e["type"] == "verifier_retry"] == [
        RETRY_MODE_RESUME,
        RETRY_MODE_RESTART,
    ]
    assert result.execution_status == "complete"
    seat = _seat(result, 1)
    assert _PAGE not in [source.url for source in seat.retrieved_sources]
    assert _PAGE in [source.url for source in seat.attempted_sources]
    # The pause and the fresh verdict, once each; the two errored requests
    # returned no response.
    assert seat.model_response_count == 2


# ---------------------------------------------------------------------------
# End to end: the mode reaches a run's log, and a retained result stays current
# ---------------------------------------------------------------------------


def test_a_research_round_records_the_resume():
    """Through the fan-out: the resumed dimension completes grounded, and its
    retry event reaches the round's log carrying the mode."""
    scripts = {
        key: [research_response(items=[], searched_urls=["https://x.gov"])]
        for key in DIM_KEYS.values()
    }
    scripts[DIM_KEYS["governing_codes"]] = [
        _pause(_PAGE),
        _RESET,
        research_response(items=[_item(_CITED, [_PAGE])]),
    ]
    events: list[dict] = []
    profile = run_requirements_research(
        RESEARCH_MODULE,
        PROFILE,
        SequencedFakeClient(scripts),
        model="claude-sonnet-5",
        max_tokens=4096,
        event_sink=events.append,
    )

    retries = [e for e in events if e["type"] == "dimension_retry"]
    assert [(e["dimension_id"], e["mode"]) for e in retries] == [
        ("governing_codes", RETRY_MODE_RESUME)
    ]
    item = next(i for i in profile.items if i.requirement == _CITED)
    assert item.grounded is True


def _fixed_qc_clock(monkeypatch) -> None:
    fixed = datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(qc_engine, "current_datetime", lambda *_a, **_k: fixed)


def _qc_run(client, store, events=None):
    return run_final_qc(
        store.doc,
        None,
        QC_MODULE,
        client,
        model=settings.QC_MODEL,
        max_tokens=4096,
        version_index=store.index,
        started_at="2026-09-24T10:00:00-07:00",
        finished_at="2026-09-24T10:01:00-07:00",
        run_id="qc-retry-resume-test",
        event_sink=(events.append if events is not None else (lambda _e: None)),
    )


def test_a_resumed_run_stays_current_like_a_clean_one(monkeypatch):
    """The plan's F3, for a chunk with no switch: how a retry began is not a
    review input. A run whose compliance lens resumed fingerprints its inputs
    exactly as a run that never failed, and both stay current."""
    _fixed_qc_clock(monkeypatch)
    monkeypatch.setattr(settings, "QC_MAX_WORKERS", 1)
    store = _store()
    final = qc_findings_response("code_compliance", findings=[])
    events: list[dict] = []
    resumed = _qc_run(
        SequencedFakeClient(
            _qc_scripts(code_compliance=[_pause(_PAGE), _RESET, final])
        ),
        store,
        events,
    )
    clean = _qc_run(
        SequencedFakeClient(_qc_scripts(code_compliance=[_pause(_PAGE), final])),
        store,
    )

    # Not vacuous: the first run really resumed, and its log says so.
    assert [
        e["mode"]
        for e in events
        if e["type"] == "lens_retry" and e["lens_id"] == "code_compliance"
    ] == [RETRY_MODE_RESUME]
    assert resumed.input_fingerprint == clean.input_fingerprint
    manifest = json.dumps(resumed.input_manifest, sort_keys=True, default=repr)
    assert manifest == json.dumps(clean.input_manifest, sort_keys=True, default=repr)
    assert "resume" not in manifest.lower()
    assert resumed.matches_inputs(store.index, store.doc, None, QC_MODULE)
    assert clean.matches_inputs(store.index, store.doc, None, QC_MODULE)
