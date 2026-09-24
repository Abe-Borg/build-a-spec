"""The continuation tail survives a refusal (Tier 1 finish, CT-1).

The continuation tail is a top-level ``cache_control`` on a streamed request
that resumes a ``pause_turn`` (``settings.CONTINUATION_CACHE``, still off by
default). If the provider refuses a continuation that carries it, with a 400
when the stream opens, the engine sends the same request once more without
it and switches that engine's tail off until the app restarts
(``backend.cost_checks``). A refusal therefore costs one request per engine
per app session, never the call.

What this file pins:

- the resend, on both engines (one assertion set run over research's
  ``_run_dimension`` and Final QC's compliance lens, because the engines keep
  separate copies of the guard): the same messages and every other argument,
  the container included, sent at once, with nothing billed for the refused
  request, and Final QC counting both requests;
- when the latch is set (the resend opens, or fails any way but a 400) and
  when it is not (the resend refused too);
- what the guard never touches: "prompt is too long", a 400 on a request
  without the tail, and anything raised after the stream opened;
- the latch read on every request, after the switch, in every thread, and a
  latched run's requests equal a switch-off run's;
- a streamed verifier seat, the retained Final QC result staying current
  (F3), two dimensions refused at once, the diagnostics block, and the
  reset between tests.

Every test that runs an engine passes ``continuation_cache=`` explicitly —
the run helpers take it as a required keyword — so CT-3's flip of the
default changes none of them.
"""
from __future__ import annotations

import ast
import json
import logging
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import pytest
from fastapi.testclient import TestClient

from backend import cost_checks, diagnostics, settings
from backend.app import create_app
from backend.qc import engine as qc_engine
from backend.qc.engine import QCResult, run_final_qc
from backend.research import engine as research_engine
from backend.research import run_requirements_research
from backend.spec_modules import DEFAULT_MODULE as QC_MODULE
from backend.spec_modules.hyperscale_fire import HYPERSCALE_FIRE as RESEARCH_MODULE
from backend.tracing.redaction import _SECRET_KEY_PATTERN, scrub_data
from tests.fakes import (
    SequencedFakeClient,
    auth_error,
    bad_request,
    block_start_event,
    qc_findings_response,
    qc_verdict_response,
    research_response,
    usage,
    user_text,
)
from tests.test_continuation_cache import _canonical, _fixed_clocks
from tests.test_qc_live_events import _finding, _lens_requests, _store
from tests.test_qc_live_events import _scripts as _qc_scripts
from tests.test_research_engine import DIM_KEYS, PROFILE, _item
from tests.test_research_engine import _scripts as _research_scripts

_TAIL = {"type": "ephemeral"}
_REFUSAL = (
    "messages: cache_control: automatic caching is not supported on this request"
)
_OTHER_400 = "messages.1.content.0: unexpected block type"
_TOO_LONG = "prompt is too long: 1000001 tokens > 1000000 maximum"
_PAGE = "https://a.gov/one"
# Bounds a wait that only a broken build would ever reach.
_BOUND = 10.0


# ---------------------------------------------------------------------------
# Scripted turns and errors
# ---------------------------------------------------------------------------


def _http_request() -> httpx.Request:
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _refused(message: str = _REFUSAL) -> anthropic.BadRequestError:
    """A real 400, which is how a refused tail would arrive."""
    return bad_request(message)


def _status_error(cls: type, status: int) -> anthropic.APIStatusError:
    return cls(
        f"status {status}",
        response=httpx.Response(status, request=_http_request()),
        body=None,
    )


def _rate_limited() -> anthropic.RateLimitError:
    return _status_error(anthropic.RateLimitError, 429)


def _pause(*, container: str | None = None) -> SimpleNamespace:
    """A paused turn: one search and its result, then ``pause_turn``.

    Built with the research builder for both engines — neither reads which
    builder made a turn — so the one shape carries a billed usage record.
    """
    return research_response(
        items=None,
        queries=["still looking"],
        searched_urls=[_PAGE],
        stop_reason="pause_turn",
        tokens={"input": 100},
        container=container,
    )


class _RaisesMidStream:
    """Stream events that raise partway through, AFTER the stream opened."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def __iter__(self):
        yield block_start_event(0, "thinking")
        raise self._exc


def _opens_then_raises(exc: BaseException) -> SimpleNamespace:
    """A turn whose stream opens normally, then fails while relaying."""
    return SimpleNamespace(
        content=[],
        stop_reason="end_turn",
        usage=usage(),
        events=_RaisesMidStream(exc),
    )


def _without_tail(request: dict) -> dict:
    return {key: value for key, value in request.items() if key != "cache_control"}


def _dimension_of(messages: list) -> str | None:
    """The dimension a request belongs to, routed as the fake client routes."""
    text = user_text(messages)
    return next(
        (dimension for dimension, key in DIM_KEYS.items() if key in text), None
    )


def _latched(engine: str) -> bool:
    return not cost_checks.continuation_tail_enabled(engine)


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    """Every backoff sleep, recorded instead of slept."""
    recorded: list[float] = []
    monkeypatch.setattr(time, "sleep", recorded.append)
    return recorded


# ---------------------------------------------------------------------------
# Runners (``continuation_cache`` is always a required keyword)
# ---------------------------------------------------------------------------


def _run_research(client: Any, *, continuation_cache: bool, events: list | None = None):
    return run_requirements_research(
        RESEARCH_MODULE,
        PROFILE,
        client,
        model="claude-sonnet-5",
        max_tokens=4096,
        continuation_cache=continuation_cache,
        event_sink=(events if events is not None else []).append,
    )


def _run_qc(
    client: Any,
    *,
    continuation_cache: bool,
    store=None,
    events: list | None = None,
):
    store = store or _store()
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
        run_id="qc-tail-rejection-test",
        # The streamed transport, so a verifier seat streams too.
        batch_verification=False,
        batch_warm_lead=False,
        warm_wait_seconds=0,
        continuation_cache=continuation_cache,
        event_sink=(events if events is not None else []).append,
    )


@dataclass
class _Call:
    """What one guarded call did, read the same way on either engine."""

    completed: bool
    error: str
    kind: str | None
    requests: list[dict]
    all_requests: list[dict]
    retries: list[dict]
    counts: tuple[int, int] | None
    input_tokens: int
    record: str


class _ResearchHarness:
    """Research's governing-codes dimension; the other three succeed empty."""

    name = cost_checks.ENGINE_RESEARCH
    other = cost_checks.ENGINE_QC
    # A non-retryable 400's recorded kind; research records one.
    invalid_kind: str | None = "invalid_request"

    @staticmethod
    def done() -> SimpleNamespace:
        return research_response(
            items=[_item("Resumed.", [_PAGE])],
            searched_urls=[_PAGE],
            tokens={"input": 40},
        )

    @staticmethod
    def counts(requests: int, responses: int) -> None:
        return None  # research counts no requests

    def run(self, turns: list, *, continuation_cache: bool) -> _Call:
        client = SequencedFakeClient(_research_scripts(governing_codes=list(turns)))
        events: list[dict] = []
        profile = _run_research(
            client, continuation_cache=continuation_cache, events=events
        )
        (status,) = [
            s for s in profile.dimension_statuses if s.dimension_id == "governing_codes"
        ]
        return _Call(
            completed=status.status == "completed",
            error=status.error,
            kind=status.error_kind,
            requests=[
                r
                for r in client.requests
                if _dimension_of(r["messages"]) == "governing_codes"
            ],
            all_requests=list(client.requests),
            retries=[
                e
                for e in events
                if e.get("type") == "dimension_retry"
                and e.get("dimension_id") == "governing_codes"
            ],
            counts=None,
            input_tokens=status.input_tokens,
            record=json.dumps(profile.to_dict(), default=repr),
        )


class _QcHarness:
    """Final QC's compliance lens (the web-tooled one); the rest pass clean."""

    name = cost_checks.ENGINE_QC
    other = cost_checks.ENGINE_RESEARCH
    # A QC lens record carries no kind, only the message.
    invalid_kind: str | None = None

    @staticmethod
    def done() -> SimpleNamespace:
        return qc_findings_response(
            "code_compliance", findings=[], tokens={"input": 40}
        )

    @staticmethod
    def counts(requests: int, responses: int) -> tuple[int, int]:
        return (requests, responses)

    def run(self, turns: list, *, continuation_cache: bool) -> _Call:
        client = SequencedFakeClient(_qc_scripts(code_compliance=list(turns)))
        events: list[dict] = []
        result = _run_qc(client, continuation_cache=continuation_cache, events=events)
        (lens,) = [s for s in result.lens_statuses if s.lens_id == "code_compliance"]
        return _Call(
            completed=lens.status == "completed",
            error=lens.error,
            kind=None,
            requests=_lens_requests(client, "code_compliance"),
            all_requests=list(client.requests),
            retries=[
                e
                for e in events
                if e.get("type") == "lens_retry"
                and e.get("lens_id") == "code_compliance"
            ],
            counts=(lens.api_request_count, lens.model_response_count),
            input_tokens=lens.usage_totals.get("input_tokens", 0),
            record=json.dumps(result.to_dict(), default=repr),
        )


_RESEARCH = _ResearchHarness()
_QC = _QcHarness()


@pytest.fixture(params=[_RESEARCH, _QC], ids=["research", "qc"])
def harness(request):
    return request.param


# ---------------------------------------------------------------------------
# The resend (both engines)
# ---------------------------------------------------------------------------


def test_a_refused_continuation_is_sent_again_once_without_the_tail(
    harness, sleeps
) -> None:
    """The refused continuation goes again, at once, minus the tail alone.

    Three requests: the opening one, the continuation carrying the tail (the
    one the provider refuses), and the same continuation without it — the
    same messages and every other argument, the container included. The call
    completes; nothing was billed for the refused request (only the two
    responses' usage); no retry, no backoff; Final QC counts all three
    requests. This engine's tail is now off, and the other engine's is not.
    """
    call = harness.run(
        [_pause(container="cont_1"), _refused(), harness.done()],
        continuation_cache=True,
    )

    assert call.completed, call.error
    opening, refused, resent = call.requests
    assert "cache_control" not in opening and "container" not in opening
    assert refused["cache_control"] == _TAIL
    assert refused["messages"][-1]["role"] == "assistant"
    assert refused["container"] == "cont_1"
    # The resend is the refused request minus the tail, and nothing else.
    assert set(refused) - set(resent) == {"cache_control"}
    assert _canonical(resent) == _canonical(_without_tail(refused))
    assert resent["messages"] == refused["messages"]
    assert resent["container"] == "cont_1"

    assert call.retries == [] and sleeps == []
    assert call.input_tokens == 140
    assert call.counts == harness.counts(3, 2)

    assert _latched(harness.name)
    assert not _latched(harness.other)
    block = cost_checks.snapshot()["continuation_tail"][harness.name]
    assert block["reason"] == cost_checks.REASON_REJECTED
    assert block["detail"] == f"BadRequestError: {_REFUSAL}"
    # The refusal reaches diagnostics, never a record.
    assert _REFUSAL not in call.record


def test_a_resend_refused_too_fails_as_before_and_latches_nothing(harness) -> None:
    """A 400 that survives removing the tail was not the tail's.

    The call fails exactly as it failed before the guard existed — with the
    resend's own message, not retried — and the tail stays on.
    """
    call = harness.run(
        [_pause(), _refused(), _refused(_OTHER_400)], continuation_cache=True
    )

    assert not call.completed
    assert call.error == f"BadRequestError: {_OTHER_400}"
    assert call.kind == harness.invalid_kind
    assert len(call.requests) == 3
    assert "cache_control" in call.requests[1]
    assert "cache_control" not in call.requests[2]
    assert call.retries == []
    assert call.counts == harness.counts(3, 1)
    assert not _latched(harness.name) and not _latched(harness.other)


def test_a_resend_that_fails_another_way_latches_then_resumes_without_the_tail(
    harness, sleeps
) -> None:
    """The 400 went away with the tail, so the latch is set; the resend's
    own failure (a rate limit) then takes the ordinary retry path, where the
    resume sends the continuation again — now without the tail.
    """
    call = harness.run(
        [_pause(), _refused(), _rate_limited(), harness.done()],
        continuation_cache=True,
    )

    assert call.completed, call.error
    assert _latched(harness.name)
    opening, refused, resent, resumed = call.requests
    assert refused["cache_control"] == _TAIL
    assert _canonical(resent) == _canonical(_without_tail(refused))
    # The resume is the resend, sent again as it stood.
    assert _canonical(resumed) == _canonical(resent)
    assert [(r["mode"], r["reason"]) for r in call.retries] == [
        ("resume", "rate_limit")
    ]
    assert len(sleeps) == 1
    # Final QC counts every request sent, the one that raised included.
    assert call.counts == harness.counts(4, 2)


def test_prompt_too_long_is_not_the_tails_refusal(harness) -> None:
    """Too long with the tail is too long without it: no resend, no latch."""
    call = harness.run([_pause(), _refused(_TOO_LONG)], continuation_cache=True)

    assert not call.completed
    assert call.error == f"BadRequestError: {_TOO_LONG}"
    assert len(call.requests) == 2
    assert call.counts == harness.counts(2, 1)
    assert not _latched(harness.name)


def test_a_400_on_a_request_without_the_tail_takes_todays_path(harness) -> None:
    """Only a tail-bearing open is guarded.

    A refused OPENING request (which never carries the tail), and a refused
    continuation with the switch off, both fail on the first request exactly
    as they always did: no resend, no latch.
    """
    opening = harness.run([_refused()], continuation_cache=True)
    assert not opening.completed
    assert opening.error == f"BadRequestError: {_REFUSAL}"
    assert len(opening.requests) == 1
    assert opening.counts == harness.counts(1, 0)

    switched_off = harness.run([_pause(), _refused()], continuation_cache=False)
    assert not switched_off.completed
    assert switched_off.error == f"BadRequestError: {_REFUSAL}"
    assert len(switched_off.requests) == 2
    assert all("cache_control" not in r for r in switched_off.requests)
    assert switched_off.counts == harness.counts(2, 1)

    assert not _latched(harness.name)


def test_an_error_after_the_stream_opened_is_not_intercepted(harness) -> None:
    """A 400 raised WHILE RELAYING a tail-bearing stream is not a verdict on
    the request's shape: the stream had opened. It takes the path it always
    took — here, a non-retryable failure — with no resend and no latch.
    """
    call = harness.run(
        [_pause(), _opens_then_raises(_refused())], continuation_cache=True
    )

    assert not call.completed
    assert call.error == f"BadRequestError: {_REFUSAL}"
    assert len(call.requests) == 2
    assert call.requests[1]["cache_control"] == _TAIL
    assert call.counts == harness.counts(2, 1)
    assert not _latched(harness.name)


# ---------------------------------------------------------------------------
# The latch: read on every request, after the switch
# ---------------------------------------------------------------------------


def test_the_latch_is_read_on_every_request_after_the_switch(
    harness, monkeypatch
) -> None:
    """With the switch off the latch is never consulted; with it on, it is
    consulted once per request — every request, the opening ones included —
    so a latch set mid-run reaches the very next request in any thread.
    """
    consulted: list[str] = []
    original = cost_checks.continuation_tail_enabled

    def spy(engine: str) -> bool:
        consulted.append(engine)
        return original(engine)

    monkeypatch.setattr(cost_checks, "continuation_tail_enabled", spy)

    off = harness.run([_pause(), harness.done()], continuation_cache=False)
    assert consulted == []

    on = harness.run([_pause(), harness.done()], continuation_cache=True)
    assert consulted == [harness.name] * len(on.all_requests)
    assert len(on.all_requests) == len(off.all_requests)


def test_after_the_latch_a_later_call_sends_exactly_a_switch_off_calls_requests(
    harness, monkeypatch
) -> None:
    """Once latched, a later run (a second research round, a second Final QC
    run) resumes without the tail, and every request it sends is byte for
    byte what the same run sends with the switch off. The turns are built
    once and shared, and the clock is pinned, so only the latch can differ.
    """
    _fixed_clocks(monkeypatch)
    first = harness.run(
        [_pause(), _refused(), harness.done()], continuation_cache=True
    )
    assert first.completed and _latched(harness.name)

    turns = [_pause(), harness.done()]
    latched = harness.run(turns, continuation_cache=True)
    switched_off = harness.run(turns, continuation_cache=False)

    # Not vacuous: the later call really paused and resumed.
    assert len(latched.requests) == 2
    assert latched.requests[1]["messages"][-1]["role"] == "assistant"
    assert all("cache_control" not in r for r in latched.all_requests)
    assert sorted(map(_canonical, latched.all_requests)) == sorted(
        map(_canonical, switched_off.all_requests)
    )


def test_a_latch_reaches_a_request_another_thread_builds_next(monkeypatch) -> None:
    """In every thread: a research dimension whose opening request is held
    until another dimension's refusal latches resumes without the tail.

    Governing codes pauses and its continuation is refused, which latches;
    the authority-having-jurisdiction dimension's opening waits for that, so
    its continuation — built after the latch, on another worker thread —
    carries no tail. Deterministic: the hold is an event, never a sleep.
    """
    latched = threading.Event()
    original = cost_checks.disable_continuation_tail

    def disable(engine: str, **kwargs: Any) -> None:
        original(engine, **kwargs)
        latched.set()

    monkeypatch.setattr(cost_checks, "disable_continuation_tail", disable)

    class _HoldOpening(SequencedFakeClient):
        def __init__(self, scripts) -> None:
            super().__init__(scripts)
            self.waited: list[bool] = []

        def stream(self, **request):
            messages = request.get("messages") or []
            if _dimension_of(messages) == "ahj_requirements" and len(messages) == 1:
                self.waited.append(latched.wait(_BOUND))
            return super().stream(**request)

    client = _HoldOpening(
        _research_scripts(
            governing_codes=[_pause(), _refused(), _RESEARCH.done()],
            ahj_requirements=[_pause(), _RESEARCH.done()],
        )
    )
    profile = _run_research(client, continuation_cache=True)

    assert client.waited == [True]
    statuses = {s.dimension_id: s.status for s in profile.dimension_statuses}
    assert statuses["governing_codes"] == statuses["ahj_requirements"] == "completed"
    governing = [
        r for r in client.requests if _dimension_of(r["messages"]) == "governing_codes"
    ]
    ahj = [
        r for r in client.requests if _dimension_of(r["messages"]) == "ahj_requirements"
    ]
    assert governing[1]["cache_control"] == _TAIL
    assert len(ahj) == 2
    assert ahj[1]["messages"][-1]["role"] == "assistant"
    assert "cache_control" not in ahj[1]


def test_two_dimensions_refused_at_once_latch_once_and_both_complete(
    monkeypatch, caplog
) -> None:
    """Both continuations are built — tail included — before either is
    refused (a barrier holds each at the provider), so both resend and both
    ask for the latch. The first wins: one latch, one WARNING, and both
    dimensions complete.
    """
    asked: list[str] = []
    original = cost_checks.disable_continuation_tail

    def disable(engine: str, **kwargs: Any) -> None:
        asked.append(engine)
        original(engine, **kwargs)

    monkeypatch.setattr(cost_checks, "disable_continuation_tail", disable)
    together = threading.Barrier(2, timeout=_BOUND)

    class _RefuseTogether(SequencedFakeClient):
        def stream(self, **request):
            if "cache_control" in request:
                together.wait()
            return super().stream(**request)

    client = _RefuseTogether(
        _research_scripts(
            governing_codes=[_pause(), _refused(), _RESEARCH.done()],
            ahj_requirements=[_pause(), _refused(), _RESEARCH.done()],
        )
    )
    caplog.set_level(logging.WARNING, logger="buildaspec.cost_checks")
    profile = _run_research(client, continuation_cache=True)

    statuses = {s.dimension_id: s.status for s in profile.dimension_statuses}
    assert statuses["governing_codes"] == statuses["ahj_requirements"] == "completed"
    assert sum("cache_control" in r for r in client.requests) == 2
    assert asked == [cost_checks.ENGINE_RESEARCH] * 2
    warnings = [
        record
        for record in caplog.records
        if record.name == "buildaspec.cost_checks"
        and record.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    assert _latched(cost_checks.ENGINE_RESEARCH)


# ---------------------------------------------------------------------------
# Final QC: a streamed seat, the record's accounting, F3
# ---------------------------------------------------------------------------


def test_a_refused_lens_record_still_reconciles_and_reloads() -> None:
    """The lens record counts both requests, and the run total is the sum of
    its records, so a saved report with a resend in it loads again rather
    than being discarded as an accounting mismatch.
    """
    client = SequencedFakeClient(
        _qc_scripts(code_compliance=[_pause(), _refused(), _QC.done()])
    )
    result = _run_qc(client, continuation_cache=True)

    (lens,) = [s for s in result.lens_statuses if s.lens_id == "code_compliance"]
    assert (lens.api_request_count, lens.model_response_count) == (3, 2)
    assert result.execution_status == "complete"
    assert QCResult.from_dict(result.to_dict()) is not None


def test_a_refused_streamed_verifier_seat_is_sent_again(monkeypatch) -> None:
    """A web-tooled verifier seat on the streamed transport, in the same shape.

    The compliance lens raises one medium finding (two seats, one worker, so
    the first seat takes the pause). Its continuation is refused and sent
    again without the tail; the seat's record counts all three requests; the
    finding is upheld; Final QC's tail is off.
    """
    monkeypatch.setattr(settings, "QC_MAX_WORKERS", 1)
    title = "Seat refusal candidate"
    scripts = _qc_scripts(
        code_compliance=[
            qc_findings_response("code_compliance", findings=[_finding(title)])
        ]
    )
    scripts[title] = [
        _pause(),
        _refused(),
        qc_verdict_response(True),
        qc_verdict_response(True),
    ]
    client = SequencedFakeClient(scripts)
    result = _run_qc(client, continuation_cache=True)

    assert [finding.title for finding in result.findings] == [title]
    seat_requests = [
        r for r in client.requests if "[[QC-VERIFY:" in user_text(r["messages"])
    ]
    opening, refused, resent, second_seat = seat_requests
    assert refused["cache_control"] == _TAIL
    # The seat's own explicit markers are 1-hour; the resend keeps them.
    assert _canonical(resent) == _canonical(_without_tail(refused))
    assert "cache_control" not in opening and "cache_control" not in second_seat
    (finding,) = result.findings
    assert sorted(
        (v.api_request_count, v.model_response_count) for v in finding.verdicts
    ) == [(1, 1), (3, 2)]
    assert _latched(cost_checks.ENGINE_QC)
    assert not _latched(cost_checks.ENGINE_RESEARCH)
    assert QCResult.from_dict(result.to_dict()) is not None


def test_a_retained_result_stays_current_after_the_latch(monkeypatch) -> None:
    """The plan's F3: the latch is not a review input.

    A run whose compliance lens was refused and resent fingerprints its
    inputs exactly as the same run with the switch off, the manifest names
    none of it, and the result matches the live inputs while the latch is
    set and after it is cleared.
    """
    _fixed_clocks(monkeypatch)
    # The transport IS a review input, and the staleness check rebuilds the
    # manifest with the live setting: compare under the one these runs use.
    monkeypatch.setattr(settings, "QC_BATCH_VERIFICATION", False)
    store = _store()
    latched_run = _run_qc(
        SequencedFakeClient(
            _qc_scripts(code_compliance=[_pause(), _refused(), _QC.done()])
        ),
        continuation_cache=True,
        store=store,
    )
    assert _latched(cost_checks.ENGINE_QC)
    plain_run = _run_qc(
        SequencedFakeClient(_qc_scripts(code_compliance=[_pause(), _QC.done()])),
        continuation_cache=False,
        store=store,
    )

    assert latched_run.input_fingerprint == plain_run.input_fingerprint
    assert _canonical(latched_run.input_manifest) == _canonical(
        plain_run.input_manifest
    )
    manifest = _canonical(latched_run.input_manifest).lower()
    for word in ("cost_check", "continuation_tail", "cache_control", "rejected"):
        assert word not in manifest
    assert latched_run.matches_inputs(store.index, store.doc, None, QC_MODULE)
    cost_checks.reset_for_tests()
    assert latched_run.matches_inputs(store.index, store.doc, None, QC_MODULE)


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


def _keys(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _keys(child)


def test_diagnostics_report_the_latch_and_survive_the_scrub(monkeypatch) -> None:
    """``diagnostics.snapshot()["cost_checks"]`` says what the checks decided.

    Clear to begin with; after a real refusal, the research engine's entry
    names the reason, the detail and the time, and the QC entry is still
    clear. The block survives ``scrub_data`` unchanged (no key trips the
    credential pattern), and ``/api/diagnostics`` carries it.
    """
    monkeypatch.setattr(settings, "CONTINUATION_CACHE", True)
    clear = diagnostics.snapshot()["cost_checks"]["continuation_tail"]
    for engine in cost_checks.TAIL_ENGINES:
        assert clear[engine] == {
            "setting_on": True,
            "enabled": True,
            "reason": "",
            "detail": "",
            "since": None,
            # The value check's counts (CT-2), nothing observed yet.
            "measured": 0,
            "exact": 0,
            "bound": 0,
            "unmeasured": 0,
            "saving_usd": 0.0,
            "last_observed_at": None,
        }

    before = time.time()
    _RESEARCH.run([_pause(), _refused(), _RESEARCH.done()], continuation_cache=True)
    raw = cost_checks.snapshot()
    research = raw["continuation_tail"][cost_checks.ENGINE_RESEARCH]
    assert research["setting_on"] is True
    assert research["enabled"] is False
    assert research["reason"] == "rejected"
    assert research["detail"] == f"BadRequestError: {_REFUSAL}"
    assert isinstance(research["since"], float)
    assert before <= research["since"] <= time.time()
    assert raw["continuation_tail"][cost_checks.ENGINE_QC]["enabled"] is True

    assert scrub_data(raw) == raw
    assert diagnostics.snapshot()["cost_checks"] == raw
    assert not [key for key in _keys(raw) if _SECRET_KEY_PATTERN.search(str(key))]
    served = TestClient(create_app()).get("/api/diagnostics").json()
    assert served["cost_checks"] == raw

    monkeypatch.setattr(settings, "CONTINUATION_CACHE", False)
    assert all(
        entry["setting_on"] is False
        for entry in cost_checks.snapshot()["continuation_tail"].values()
    )


# ---------------------------------------------------------------------------
# cost_checks itself
# ---------------------------------------------------------------------------


def test_only_a_400_other_than_too_long_is_a_tail_rejection() -> None:
    assert cost_checks.is_tail_rejection(_refused())
    assert cost_checks.is_tail_rejection(_refused(_OTHER_400))
    assert not cost_checks.is_tail_rejection(_refused(_TOO_LONG))
    assert not cost_checks.is_tail_rejection(_refused("Prompt Is Too Long: 5 > 4"))
    for error in (
        auth_error(),
        _status_error(anthropic.PermissionDeniedError, 403),
        _status_error(anthropic.NotFoundError, 404),
        _status_error(anthropic.RequestTooLargeError, 413),
        _status_error(anthropic.UnprocessableEntityError, 422),
        _rate_limited(),
        _status_error(anthropic.InternalServerError, 500),
        _status_error(anthropic.OverloadedError, 529),
        anthropic.APIConnectionError(request=_http_request()),
        anthropic.APITimeoutError(request=_http_request()),
        RuntimeError(_REFUSAL),
    ):
        assert not cost_checks.is_tail_rejection(error), type(error).__name__


def test_the_first_latch_wins_and_logs_one_warning(caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="buildaspec.cost_checks")
    cost_checks.disable_continuation_tail("research", reason="rejected", detail="first")
    cost_checks.disable_continuation_tail("research", reason="rejected", detail="second")

    entry = cost_checks.snapshot()["continuation_tail"]["research"]
    assert entry["detail"] == "first"
    warnings = [
        record
        for record in caplog.records
        if record.name == "buildaspec.cost_checks"
        and record.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    for word in ("continuation tail", "research", "rejected", "first"):
        assert word in message
    assert cost_checks.continuation_tail_enabled("qc")


def test_the_detail_is_one_line_and_clipped() -> None:
    long_detail = "line one\nline two\t" + "x" * 500
    cost_checks.disable_continuation_tail("qc", reason="rejected", detail=long_detail)
    detail = cost_checks.snapshot()["continuation_tail"]["qc"]["detail"]
    assert "\n" not in detail and "\t" not in detail
    assert detail.startswith("line one line two x")
    assert len(detail) == cost_checks.DETAIL_MAX_CHARS
    assert len(cost_checks.exception_detail(_refused("y" * 500))) == 200


def test_a_malformed_call_never_raises_and_switches_nothing_off(caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="buildaspec.cost_checks")
    # A reason outside the closed vocabulary (CT-1 used ``unprofitable``
    # here, which CT-2 made a real reason; this one never will be).
    cost_checks.disable_continuation_tail("research", reason="not_a_reason")
    cost_checks.disable_continuation_tail("research", reason="")
    cost_checks.disable_continuation_tail("chat", reason="rejected")
    assert all(
        cost_checks.continuation_tail_enabled(engine)
        for engine in cost_checks.TAIL_ENGINES
    )
    # An engine it does not know reads as enabled: a check never takes a
    # saving away by failing.
    assert cost_checks.continuation_tail_enabled("chat") is True
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_many_threads_latch_once() -> None:
    """Sixteen threads ask at once; exactly one detail is kept, no one raises."""
    start = threading.Barrier(16, timeout=_BOUND)
    failures: list[BaseException] = []

    def ask(index: int) -> None:
        try:
            start.wait()
            cost_checks.disable_continuation_tail(
                "qc", reason="rejected", detail=f"thread {index}"
            )
            cost_checks.continuation_tail_enabled("qc")
        except BaseException as exc:  # noqa: BLE001 — collected, then asserted
            failures.append(exc)

    threads = [threading.Thread(target=ask, args=(i,)) for i in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(_BOUND)
    assert failures == []
    detail = cost_checks.snapshot()["continuation_tail"]["qc"]["detail"]
    assert detail in {f"thread {i}" for i in range(16)}
    assert _latched("qc") and not _latched("research")


class _CountingLock:
    """The module lock, counted: a race cannot be forced deterministically,
    but whether every read and write takes the lock can be."""

    def __init__(self) -> None:
        self._inner = threading.Lock()
        self.taken = 0

    def __enter__(self) -> "_CountingLock":
        self._inner.acquire()
        self.taken += 1
        return self

    def __exit__(self, *exc: Any) -> bool:
        self._inner.release()
        return False


def test_every_read_and_write_takes_the_one_lock(monkeypatch) -> None:
    lock = _CountingLock()
    monkeypatch.setattr(cost_checks, "_lock", lock)
    for call in (
        lambda: cost_checks.continuation_tail_enabled("research"),
        lambda: cost_checks.disable_continuation_tail("research", reason="rejected"),
        lambda: cost_checks.disable_continuation_tail("research", reason="rejected"),
        cost_checks.snapshot,
        cost_checks.reset_for_tests,
    ):
        before = lock.taken
        call()
        assert lock.taken == before + 1


def test_cost_checks_is_a_leaf_both_engines_share() -> None:
    """The standard library, ``anthropic``, ``settings`` and ``usage_ledger``
    only — so both engines can import it — and both import the same module,
    under their own engine names."""
    source = Path(cost_checks.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                assert top in sys.stdlib_module_names or top == "anthropic", alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                top = (node.module or "").split(".")[0]
                assert top in sys.stdlib_module_names or top == "anthropic", node.module
            else:
                assert node.level == 1, ast.unparse(node)
                named = {node.module} if node.module else {a.name for a in node.names}
                assert named <= {"settings", "usage_ledger"}, ast.unparse(node)
    assert research_engine.cost_checks is cost_checks
    assert qc_engine.cost_checks is cost_checks
    assert research_engine._TAIL_ENGINE == cost_checks.ENGINE_RESEARCH
    assert qc_engine._TAIL_ENGINE == cost_checks.ENGINE_QC


# ---------------------------------------------------------------------------
# The helper itself: what it yields, and the SDK's shape
# ---------------------------------------------------------------------------


class _Tracked:
    """A stream context that records whether it was entered and exited."""

    def __init__(self, name: str, *, refuse_on_enter: BaseException | None = None):
        self.name = name
        self.refuse_on_enter = refuse_on_enter
        self.entered = 0
        self.exited = 0

    def __enter__(self):
        if self.refuse_on_enter is not None:
            raise self.refuse_on_enter
        self.entered += 1
        return self.name

    def __exit__(self, *exc: Any) -> bool:
        self.exited += 1
        return False


class _Provider:
    """``client.messages.stream(...)`` handing out scripted contexts, or
    raising a scripted error from the call itself (the fakes' shape)."""

    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[dict] = []
        self.messages = self

    def stream(self, **request: Any):
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


_ENGINE_MODULES = [
    pytest.param(research_engine, id="research"),
    pytest.param(qc_engine, id="qc"),
]


def _open(module: Any, provider: _Provider, stream_kwargs: dict, counted: list):
    kwargs: dict[str, Any] = {
        "messages": [{"role": "user", "content": "brief"}],
        "stream_kwargs": stream_kwargs,
    }
    if module is qc_engine:
        kwargs["count_request"] = lambda: counted.append(1)
    return module._open_stream(provider, **kwargs)


@pytest.mark.parametrize("module", _ENGINE_MODULES)
def test_the_helper_says_whether_the_request_that_opened_carried_the_tail(
    module,
) -> None:
    """``(stream, carried)``: CT-2 reads ``carried``, so it must be the truth
    about the request that OPENED — ``False`` after a resend."""
    counted: list[int] = []

    plain = _Provider([_Tracked("plain")])
    with _open(module, plain, {"model": "m"}, counted) as (stream, carried):
        assert (stream, carried) == ("plain", False)

    tailed = _Provider([_Tracked("tailed")])
    with _open(module, tailed, {"model": "m", "cache_control": _TAIL}, counted) as (
        stream,
        carried,
    ):
        assert (stream, carried) == ("tailed", True)
    assert not _latched(module._TAIL_ENGINE)

    resent = _Tracked("resent")
    refused = _Provider([_refused(), resent])
    with _open(module, refused, {"model": "m", "cache_control": _TAIL}, counted) as (
        stream,
        carried,
    ):
        assert (stream, carried) == ("resent", False)
    assert refused.requests[1] == {
        "messages": [{"role": "user", "content": "brief"}],
        "model": "m",
    }
    assert (resent.entered, resent.exited) == (1, 1)
    assert _latched(module._TAIL_ENGINE)
    # Only Final QC counts requests, and only the resend is its to count.
    assert counted == ([1] if module is qc_engine else [])


@pytest.mark.parametrize("module", _ENGINE_MODULES)
def test_the_helper_guards_the_sdks_shape_a_refusal_on_entering_the_stream(
    module,
) -> None:
    """The real SDK sends the request when the stream context is ENTERED, so
    its 400 comes out of ``__enter__``, not out of ``stream(...)``. The guard
    catches it there too; the refused context is never exited (it never
    opened), and the resent one is exited exactly once."""
    refused_on_enter = _Tracked("refused", refuse_on_enter=_refused())
    resent = _Tracked("resent")
    provider = _Provider([refused_on_enter, resent])
    with _open(module, provider, {"cache_control": _TAIL}, []) as (stream, carried):
        assert (stream, carried) == ("resent", False)
    assert refused_on_enter.exited == 0
    assert (resent.entered, resent.exited) == (1, 1)
    assert len(provider.requests) == 2
    assert _latched(module._TAIL_ENGINE)


@pytest.mark.parametrize("module", _ENGINE_MODULES)
def test_the_helper_lets_a_failure_inside_the_stream_through_untouched(
    module,
) -> None:
    """An error raised in the ``with`` body — after the open — reaches the
    caller as it was raised, exits the stream once, and is never resent."""
    opened = _Tracked("opened")
    provider = _Provider([opened])
    with pytest.raises(anthropic.BadRequestError) as raised:
        with _open(module, provider, {"cache_control": _TAIL}, []):
            raise _refused()
    assert str(raised.value) == _REFUSAL
    assert (opened.entered, opened.exited) == (1, 1)
    assert len(provider.requests) == 1
    assert not _latched(module._TAIL_ENGINE)


# ---------------------------------------------------------------------------
# Nothing leaks between tests
# ---------------------------------------------------------------------------


def test_the_conftest_resets_the_latches_before_and_after_every_test() -> None:
    source = (Path(__file__).parent / "conftest.py").read_text(encoding="utf-8")
    (fixture,) = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == "_fresh_session"
    ]
    assert any(
        "autouse=True" in ast.unparse(decorator)
        for decorator in fixture.decorator_list
    )
    (at,) = [
        index
        for index, statement in enumerate(fixture.body)
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Yield)
    ]

    def resets(statements: list[ast.stmt]) -> bool:
        return any(
            isinstance(node, ast.Call)
            and ast.unparse(node.func) == "cost_checks.reset_for_tests"
            for statement in statements
            for node in ast.walk(statement)
        )

    assert resets(fixture.body[:at])
    assert resets(fixture.body[at + 1 :])


def test_a_latch_left_set_on_purpose() -> None:
    """Paired with the next test, which must find both latches clear."""
    for engine in cost_checks.TAIL_ENGINES:
        cost_checks.disable_continuation_tail(engine, reason="rejected")
        assert _latched(engine)


def test_the_next_test_starts_with_both_latches_clear() -> None:
    for engine in cost_checks.TAIL_ENGINES:
        assert cost_checks.continuation_tail_enabled(engine)
        entry = cost_checks.snapshot()["continuation_tail"][engine]
        assert (entry["reason"], entry["detail"], entry["since"]) == ("", "", None)

