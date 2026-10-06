"""The resource pressure ledger: can diagnostics say whether an agent was
starved while it ran?

Three layers, hermetic throughout (``tests/fakes.py``; no network, no real
seconds — every backoff sleep is recorded, every bounded wait is a stepped
clock):

* the ledger itself (``backend/resource_pressure.py``): what a run and an
  agent record, the first-outcome-wins rule, the bounds, the sanitizers,
  the SDK retry observer, the scrub-safe snapshot shape;
* the three engines reporting into it at the spots pressure is observed —
  a research area that is rate limited, hits its search ceiling, waits the
  whole bound for a lead, or is cut at ``max_tokens``; a Final QC lens that
  is rate limited, a batched phase whose submission is refused, a batched
  seat that expires, a clean batch round noted and not counted; a chat turn
  that fails on a 429 or is cut at ``max_tokens``;
* the diagnostics endpoint carrying the block intact through the scrub.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import anthropic
import httpx
import pytest
from fastapi.testclient import TestClient

from backend import resource_pressure
from backend.app import create_app
from backend.qc import engine as qc_engine
from backend.research import engine as research_engine
from backend.research.engine import run_requirements_research
from backend.spec_modules.hyperscale_fire import HYPERSCALE_FIRE as RESEARCH_MODULE
from backend.tracing.redaction import scrub_data
from tests.fakes import (
    FakeClient,
    SequencedFakeClient,
    pause_response,
    qc_findings_response,
    research_response,
    text_turn,
    tool_turn,
)
from tests.test_app import _SEED_EDITS, _client, _parse_sse, _patch_client
from tests.test_qc_batch_verification import (
    _SteppedClock,
    _one_finding_scripts,
    _refuse_submissions,
    _scripts as _qc_scripts,
)
from tests.test_qc_batch_verification import _run as _run_qc
from tests.test_research_engine import PROFILE, _item, _scripts
from tests.test_research_engine import _run as _run_research
from tests.test_research_warm_launch import _FOLLOWERS, _LEAD, _WatchedClient

RP = resource_pressure


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    """Both engines read the one ``time`` module for their retry sleeps."""
    slept: list[float] = []
    monkeypatch.setattr(research_engine.time, "sleep", slept.append)
    return slept


def _rate_limited(retry_after: str | None = None) -> anthropic.RateLimitError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    headers = {"retry-after": retry_after} if retry_after is not None else {}
    return anthropic.RateLimitError(
        "Rate limited.",
        response=httpx.Response(429, request=request, headers=headers),
        body=None,
    )


def _runs(engine: str) -> list[dict]:
    return [run for run in RP.snapshot()["runs"] if run["engine"] == engine]


def _only_run(engine: str) -> dict:
    runs = _runs(engine)
    assert len(runs) == 1, runs
    return runs[0]


def _events(run: dict, agent: str, kind: str | None = None) -> list[dict]:
    return [
        event
        for event in run["events"]
        if event["agent"] == agent and (kind is None or event["kind"] == kind)
    ]


# ---------------------------------------------------------------------------
# The ledger
# ---------------------------------------------------------------------------


def test_the_vocabulary_is_closed_and_every_kind_has_a_source() -> None:
    kinds = {
        value
        for name, value in vars(RP).items()
        if name.startswith("KIND_") and isinstance(value, str)
    }
    assert kinds == set(RP.PRESSURE_KINDS)
    assert set(RP.PRESSURE_SOURCES.values()) == {
        RP.SOURCE_PROVIDER,
        RP.SOURCE_BUDGET,
        RP.SOURCE_CONTEXT,
        RP.SOURCE_OUTPUT,
        RP.SOURCE_SCHEDULING,
    }
    for token in (*RP.PRESSURE_KINDS, *RP.OUTCOMES, *RP.ENGINES, *RP.AGENT_KINDS):
        assert RP._TOKEN_RE.match(token), token
    # The retryable failure classes map onto their own kinds, by value.
    from backend.research.retry_policy import FailureClass, is_retryable_failure_class

    retryable = {c.value for c in FailureClass if is_retryable_failure_class(c)}
    assert set(RP.RETRY_PRESSURE_KINDS) == retryable


def test_a_run_with_no_pressure_is_not_starved() -> None:
    run = RP.begin_run(RP.ENGINE_RESEARCH, label="round 1")
    agent = run.agent("governing_codes", kind=RP.AGENT_DIMENSION)
    agent.submitted()
    agent.started()
    agent.ended(RP.OUTCOME_COMPLETED, attempts=1)
    run.end()

    view = _only_run(RP.ENGINE_RESEARCH)
    assert view["status"] == RP.RUN_ENDED
    assert view["label"] == "round 1"
    assert view["starved"] is False
    assert view["starved_agents"] == 0
    assert view["agents_total"] == 1
    assert view["agents_completed"] == 1
    assert view["pressure_counts"] == {}
    assert view["events"] == []
    area = view["agents"]["governing_codes"]
    assert area["kind"] == RP.AGENT_DIMENSION
    assert area["outcome"] == RP.OUTCOME_COMPLETED
    assert area["attempts"] == 1
    assert area["starved"] is False
    assert area["queued_ms"] is not None and area["queued_ms"] >= 0
    totals = RP.snapshot()["totals"][RP.ENGINE_RESEARCH]
    assert totals["runs_ended"] == 1
    assert totals["runs_starved"] == 0
    assert totals["agents"] == 1
    assert totals["starved_agents"] == 0


def test_a_retry_records_its_kind_backoff_and_the_providers_ask() -> None:
    run = RP.begin_run(RP.ENGINE_QC, run_id="qc-run-1", label="Final QC")
    seat = run.agent("code_compliance", kind=RP.AGENT_LENS)
    seat.started()
    seat.retry(
        failure_class="rate_limit",
        attempt=1,
        max_attempts=3,
        backoff_s=5.0,
        mode="restart",
        retry_after_s=7.0,
    )
    seat.retry(
        failure_class="server_error",
        attempt=2,
        max_attempts=3,
        backoff_s=10.0,
        mode="resume",
    )
    # A class the ledger has no pressure for records nothing.
    seat.retry(
        failure_class="invalid_request", attempt=3, max_attempts=3, backoff_s=0.0
    )
    seat.ended(RP.OUTCOME_COMPLETED, attempts=3)
    run.end()

    view = _only_run(RP.ENGINE_QC)
    assert view["run_id"] == "qc-run-1"
    assert view["starved"] is True
    assert view["pressure_counts"] == {"rate_limit": 1, "server_error": 1}
    assert view["backoff_s"] == 15.0
    lens = view["agents"]["code_compliance"]
    assert lens["starved"] is True
    assert lens["pressure_counts"] == {"rate_limit": 1, "server_error": 1}
    assert lens["backoff_s"] == 15.0
    assert lens["outcome"] == RP.OUTCOME_COMPLETED
    first, second = _events(view, "code_compliance")
    assert first["kind"] == "rate_limit"
    assert first["attempt"] == 1 and first["max_attempts"] == 3
    assert first["backoff_s"] == 5.0 and first["mode"] == "restart"
    assert first["retry_after_s"] == 7.0
    assert "final" not in first
    assert second["kind"] == "server_error" and second["mode"] == "resume"
    assert "retry_after_s" not in second
    assert RP.snapshot()["totals"][RP.ENGINE_QC]["pressure_counts"] == {
        "rate_limit": 1,
        "server_error": 1,
    }


def test_a_queue_wait_counts_only_past_the_floor(monkeypatch) -> None:
    clock = {"now": 100.0}
    monkeypatch.setattr(
        RP,
        "time",
        SimpleNamespace(time=RP.time.time, monotonic=lambda: clock["now"]),
    )
    run = RP.begin_run(RP.ENGINE_QC, label="Final QC")
    quick = run.agent("seat-0-0", kind=RP.AGENT_VERIFIER)
    slow = run.agent("seat-0-1", kind=RP.AGENT_VERIFIER)
    quick.submitted()
    slow.submitted()
    clock["now"] += 0.5
    quick.started()
    clock["now"] += 2.0
    slow.started()
    run.end()

    view = _only_run(RP.ENGINE_QC)
    assert view["agents"]["seat-0-0"]["queued_ms"] == 500
    assert view["agents"]["seat-0-0"]["starved"] is False
    assert view["agents"]["seat-0-1"]["queued_ms"] == 2500
    assert view["agents"]["seat-0-1"]["pressure_counts"] == {"queued": 1}
    assert _events(view, "seat-0-1", "queued")[0]["queued_ms"] == 2500
    assert view["queued_max_ms"] == 2500
    # Never started: no queue wait to report, not a zero.
    never = run.agent("seat-0-2", kind=RP.AGENT_VERIFIER)
    never.submitted()
    assert _only_run(RP.ENGINE_QC)["agents"]["seat-0-2"]["queued_ms"] is None


def test_a_warm_wait_is_pressure_only_when_it_timed_out() -> None:
    run = RP.begin_run(RP.ENGINE_RESEARCH, label="round 1")
    for ident, outcome in (
        ("ahj_requirements", "warm"),
        ("client_standards", "timeout"),
        ("site_environment", "stopped"),
    ):
        run.agent(ident, kind=RP.AGENT_DIMENSION).warm_wait(
            outcome=outcome, waited_ms=45_000, lead="governing_codes"
        )
    run.end()

    agents = _only_run(RP.ENGINE_RESEARCH)["agents"]
    assert agents["ahj_requirements"]["warm_wait_outcome"] == "warm"
    assert agents["ahj_requirements"]["warm_wait_ms"] == 45_000
    assert agents["ahj_requirements"]["warm_lead"] == "governing_codes"
    assert agents["ahj_requirements"]["starved"] is False
    assert agents["client_standards"]["pressure_counts"] == {"warm_wait_timeout": 1}
    assert agents["site_environment"]["starved"] is False
    # An outcome the launch never names is reduced, never repeated.
    run.agent("x", kind=RP.AGENT_DIMENSION).warm_wait(outcome="whatever", waited_ms=1)
    assert _only_run(RP.ENGINE_RESEARCH)["agents"]["x"]["warm_wait_outcome"] == (
        RP.UNRECOGNIZED
    )


def test_the_first_outcome_wins_and_the_run_end_interrupts_the_rest() -> None:
    run = RP.begin_run(RP.ENGINE_RESEARCH, label="round 1")
    reported = run.agent("governing_codes", kind=RP.AGENT_DIMENSION)
    silent = run.agent("ahj_requirements", kind=RP.AGENT_DIMENSION)
    reported.started()
    silent.started()
    reported.ended(RP.OUTCOME_FAILED, error_kind="rate_limit", attempts=3)
    # A second report — the coordinator closing a worker that did report.
    reported.ended(RP.OUTCOME_COMPLETED, attempts=1)
    run.end()
    # A worker that outlives its run cannot rewrite the record it closed.
    silent.ended(RP.OUTCOME_COMPLETED)
    run.end()

    view = _only_run(RP.ENGINE_RESEARCH)
    assert view["agents"]["governing_codes"]["outcome"] == RP.OUTCOME_FAILED
    assert view["agents"]["governing_codes"]["error_kind"] == "rate_limit"
    assert view["agents"]["governing_codes"]["attempts"] == 3
    assert view["agents"]["ahj_requirements"]["outcome"] == RP.OUTCOME_INTERRUPTED
    assert view["agents_interrupted"] == 1
    assert RP.snapshot()["totals"][RP.ENGINE_RESEARCH]["runs_ended"] == 1


def test_unknown_tokens_are_reduced_or_refused() -> None:
    assert RP.begin_run("not-an-engine").active is False
    run = RP.begin_run(RP.ENGINE_CHAT, label="turn")
    agent = run.agent("a turn/with odd chars!", kind="mystery")
    agent.pressure("not_a_kind")
    agent.pressure(RP.KIND_OUTPUT_TRUNCATED, note="x" * 500, nested={"a": 1}, Bad_Key=1)
    agent.ended("weird", error_kind="Bad Kind!")
    run.note("not_a_note", waited_ms=5)
    run.end()

    view = _only_run(RP.ENGINE_CHAT)
    (ident,) = view["agents"]
    assert ident == "a_turn_with_odd_chars_"
    turn = view["agents"][ident]
    assert turn["kind"] == RP.UNRECOGNIZED
    assert turn["outcome"] == RP.OUTCOME_FAILED
    assert turn["error_kind"] == RP.UNRECOGNIZED
    assert turn["pressure_counts"] == {"output_truncated": 1}
    (event,) = view["events"]
    assert event["kind"] == "output_truncated"
    assert len(event["note"]) == RP._MAX_FIELD_STR_CHARS
    assert "nested" not in event and "Bad_Key" not in event
    assert view["batch_rounds"] == 0


def test_the_ledger_is_bounded(monkeypatch) -> None:
    monkeypatch.setattr(RP, "MAX_RUNS_PER_ENGINE", 2)
    monkeypatch.setattr(RP, "MAX_AGENTS_PER_RUN", 2)
    monkeypatch.setattr(RP, "MAX_EVENTS_PER_RUN", 2)
    RP.reset_for_tests()
    for index in range(3):
        run = RP.begin_run(RP.ENGINE_CHAT, label=f"turn {index}")
        run.end()
    run = RP.begin_run(RP.ENGINE_CHAT, label="busy")
    kept = [run.agent(f"a{i}", kind=RP.AGENT_TURN) for i in range(2)]
    dropped = run.agent("a2", kind=RP.AGENT_TURN)
    assert dropped.active is False
    for _ in range(3):
        kept[0].pressure(RP.KIND_SDK_RETRY)
    run.end()

    snapshot = RP.snapshot()
    totals = snapshot["totals"][RP.ENGINE_CHAT]
    assert totals["runs_recorded"] == 4
    assert totals["runs_kept"] == 2
    assert snapshot["max_runs_per_engine"] == 2
    labels = [run["label"] for run in _runs(RP.ENGINE_CHAT)]
    assert labels == ["busy", "turn 2"]
    busy = _runs(RP.ENGINE_CHAT)[0]
    assert busy["agents_total"] == 2 and busy["agents_dropped"] == 1
    assert busy["agents"]["a0"]["pressure_counts"] == {"sdk_retry": 3}
    assert len(busy["events"]) == 2 and busy["events_dropped"] == 1
    # The totals count the dropped agent too: it ran, it just has no record.
    assert totals["agents"] == 3


def test_the_null_handles_record_nothing() -> None:
    assert RP.NO_RUN.active is False
    agent = RP.NO_RUN.agent("x", kind=RP.AGENT_TURN)
    assert agent is RP.NO_AGENT
    agent.submitted()
    agent.started()
    agent.pressure(RP.KIND_RATE_LIMIT)
    agent.retry(failure_class="rate_limit", attempt=1, max_attempts=3, backoff_s=5)
    agent.warm_wait(outcome="timeout", waited_ms=1)
    agent.sdk_retry(0.5)
    agent.ended(RP.OUTCOME_FAILED)
    with agent.requesting():
        pass
    RP.NO_RUN.note(RP.NOTE_BATCH_ROUND, waited_ms=1)
    RP.NO_RUN.end()
    with RP.NO_RUN:
        pass
    snapshot = RP.snapshot()
    assert snapshot["runs"] == []
    assert all(
        totals["runs_recorded"] == 0 for totals in snapshot["totals"].values()
    )


def test_the_sdk_retry_observer_attributes_to_the_agent_in_flight(caplog) -> None:
    """The SDK logs each retry it makes on its own at INFO; the observer
    counts it against the thread's agent in flight, or as unattributed."""
    run = RP.begin_run(RP.ENGINE_RESEARCH, label="round 1")
    agent = run.agent("governing_codes", kind=RP.AGENT_DIMENSION)
    caplog.set_level(logging.INFO, logger=RP.SDK_RETRY_LOGGER)
    sdk_log = logging.getLogger(RP.SDK_RETRY_LOGGER)
    url = "https://api.anthropic.com/v1/messages"
    with agent.requesting():
        sdk_log.info("Retrying request to %s in %f seconds", url, 0.5)
        sdk_log.info("Something else about %s", url)
    # No agent in flight on this thread: counted, never guessed onto one.
    sdk_log.info("Retrying request to %s in %f seconds", url, 1.25)
    run.end()

    snapshot = RP.snapshot()
    observer = snapshot["sdk_retry_observer"]
    assert observer["attached"] is True
    assert observer["listening"] is True
    assert observer["logger"] == RP.SDK_RETRY_LOGGER
    assert observer["unattributed_retries"] == 1
    assert observer["unattributed_sleep_s"] == 1.25
    assert snapshot["sdk_retries_per_request"] >= 0
    view = _only_run(RP.ENGINE_RESEARCH)
    area = view["agents"]["governing_codes"]
    assert area["sdk_retries"] == 1
    assert area["sdk_sleep_s"] == 0.5
    assert area["pressure_counts"] == {"sdk_retry": 1}
    assert area["starved"] is True
    (event,) = _events(view, "governing_codes", "sdk_retry")
    assert event["sleep_s"] == 0.5
    assert view["sdk_retries"] == 1
    # The real SDK's line, from this installed version, is what it watches.
    import inspect

    import anthropic._base_client as base_client

    assert 'log.info("Retrying request to %s in %f seconds"' in inspect.getsource(
        base_client
    )
    assert base_client.log.name == RP.SDK_RETRY_LOGGER


def test_a_muted_sdk_logger_is_disclosed_not_guessed(caplog) -> None:
    RP.begin_run(RP.ENGINE_CHAT, label="turn").end()
    caplog.set_level(logging.WARNING, logger=RP.SDK_RETRY_LOGGER)
    assert RP.snapshot()["sdk_retry_observer"]["listening"] is False


def test_retry_after_seconds_reads_the_providers_header() -> None:
    assert RP.retry_after_seconds(_rate_limited("7")) == 7.0
    assert RP.retry_after_seconds(_rate_limited("2.5")) == 2.5
    assert RP.retry_after_seconds(_rate_limited()) is None
    assert RP.retry_after_seconds(_rate_limited("Wed, 21 Oct 2026 07:28:00 GMT")) is None
    assert RP.retry_after_seconds(_rate_limited("-3")) is None
    assert RP.retry_after_seconds(RuntimeError("no response here")) is None


def test_the_snapshot_fits_the_scrub_bound_with_its_agents_intact() -> None:
    run = RP.begin_run(RP.ENGINE_RESEARCH, label="round 1")
    area = run.agent("governing_codes", kind=RP.AGENT_DIMENSION)
    area.started()
    area.retry(failure_class="rate_limit", attempt=1, max_attempts=3, backoff_s=5)
    area.pressure(RP.KIND_SEARCH_CEILING, searches=81)
    area.ended(RP.OUTCOME_COMPLETED, attempts=2)
    run.note(RP.NOTE_BATCH_ROUND, round=1, waited_ms=10)
    run.end()

    scrubbed = scrub_data({"ok": True, "resource_pressure": RP.snapshot()})
    text = repr(scrubbed)
    assert "<max-depth>" not in text
    agent = scrubbed["resource_pressure"]["runs"][0]["agents"]["governing_codes"]
    assert agent["pressure_counts"] == {"rate_limit": 1, "search_ceiling": 1}
    assert scrubbed["resource_pressure"]["runs"][0]["batch_rounds"] == 1
    assert scrubbed["resource_pressure"]["runs"][0]["batch_wait_ms"] == 10
    assert scrubbed["resource_pressure"]["totals"][RP.ENGINE_RESEARCH][
        "pressure_counts"
    ] == {"rate_limit": 1, "search_ceiling": 1}


def test_reset_clears_everything_but_the_observer() -> None:
    run = RP.begin_run(RP.ENGINE_CHAT, label="turn")
    run.agent("turn", kind=RP.AGENT_TURN).pressure(RP.KIND_PROMPT_TOO_LONG)
    run.end()
    RP.record_sdk_retry(0.1)
    RP.reset_for_tests()
    snapshot = RP.snapshot()
    assert snapshot["runs"] == []
    assert snapshot["sdk_retry_observer"]["unattributed_retries"] == 0
    assert snapshot["sdk_retry_observer"]["attached"] is True


# ---------------------------------------------------------------------------
# Research reports into it
# ---------------------------------------------------------------------------


def test_a_rate_limited_area_is_recorded_starved_and_recovered(_no_backoff) -> None:
    client = SequencedFakeClient(
        _scripts(
            governing_codes=[
                _rate_limited("12"),
                research_response(items=[], searched_urls=["https://x.gov"]),
            ]
        )
    )
    profile = _run_research(client)
    assert all(s.status == "completed" for s in profile.dimension_statuses)
    assert _no_backoff == [5.0]

    view = _only_run(RP.ENGINE_RESEARCH)
    assert view["label"] == "round 1"
    assert view["status"] == RP.RUN_ENDED
    assert view["starved"] is True
    assert view["starved_agents"] == 1
    assert view["agents_total"] == 4
    assert view["agents_completed"] == 4
    assert view["pressure_counts"] == {"rate_limit": 1}
    assert view["backoff_s"] == 5.0
    area = view["agents"]["governing_codes"]
    assert area["kind"] == RP.AGENT_DIMENSION
    assert area["outcome"] == RP.OUTCOME_COMPLETED
    assert area["attempts"] == 2
    assert area["pressure_counts"] == {"rate_limit": 1}
    (event,) = _events(view, "governing_codes")
    assert event["attempt"] == 1 and event["max_attempts"] == 3
    assert event["backoff_s"] == 5.0 and event["mode"] == "restart"
    assert event["retry_after_s"] == 12.0
    for ident in ("ahj_requirements", "client_standards", "site_environment"):
        assert view["agents"][ident]["starved"] is False
        assert view["agents"][ident]["outcome"] == RP.OUTCOME_COMPLETED
    # A round briefed on an earlier one reads as the next round.
    run_requirements_research(
        RESEARCH_MODULE,
        PROFILE,
        SequencedFakeClient(_scripts()),
        model="claude-sonnet-5",
        max_tokens=4096,
        established=profile,
    )
    assert [run["label"] for run in _runs(RP.ENGINE_RESEARCH)] == ["round 2", "round 1"]


def test_an_area_that_gives_up_records_the_final_failure_too(_no_backoff) -> None:
    client = SequencedFakeClient(
        _scripts(governing_codes=[_rate_limited(), _rate_limited(), _rate_limited("3")])
    )
    profile = _run_research(client)
    failed = next(
        s for s in profile.dimension_statuses if s.dimension_id == "governing_codes"
    )
    assert failed.status == "failed" and failed.error_kind == "rate_limit"

    area = _only_run(RP.ENGINE_RESEARCH)["agents"]["governing_codes"]
    assert area["outcome"] == RP.OUTCOME_FAILED
    assert area["error_kind"] == "rate_limit"
    assert area["attempts"] == 3
    # Two retries and the failure that gave up: three pressures, not two.
    assert area["pressure_counts"] == {"rate_limit": 3}
    events = _events(_only_run(RP.ENGINE_RESEARCH), "governing_codes")
    assert [event.get("final") for event in events] == [None, None, True]
    assert events[-1]["retry_after_s"] == 3.0
    assert events[-1]["backoff_s"] == 0.0


def test_a_search_ceiling_is_budget_pressure_on_the_area() -> None:
    # governing_codes budget is 40 → ceiling 80 (test_research_engine's
    # recipe): two pauses totalling 81 searches trip it; the submission
    # saves the work.
    client = SequencedFakeClient(
        _scripts(
            governing_codes=[
                pause_response(searched_urls=["https://a.gov"], searches=41),
                pause_response(searched_urls=["https://b.gov"], searches=40),
                research_response(items=[_item("Retained finding.", ["https://b.gov"])]),
            ]
        )
    )
    profile = _run_research(client)
    assert next(
        s for s in profile.dimension_statuses if s.dimension_id == "governing_codes"
    ).status == "completed"

    view = _only_run(RP.ENGINE_RESEARCH)
    area = view["agents"]["governing_codes"]
    assert area["outcome"] == RP.OUTCOME_COMPLETED
    assert area["pressure_counts"] == {"search_ceiling": 1}
    (event,) = _events(view, "governing_codes")
    assert event["searches"] == 81
    assert event["responses"] == 2
    assert RP.PRESSURE_SOURCES[event["kind"]] == RP.SOURCE_BUDGET


def test_a_max_tokens_cut_is_output_pressure_on_the_area() -> None:
    client = SequencedFakeClient(
        _scripts(governing_codes=[research_response(items=[], stop_reason="max_tokens")])
    )
    profile = _run_research(client)
    status = next(
        s for s in profile.dimension_statuses if s.dimension_id == "governing_codes"
    )
    assert status.status == "failed" and status.error_kind == "incomplete_response"

    area = _only_run(RP.ENGINE_RESEARCH)["agents"]["governing_codes"]
    assert area["outcome"] == RP.OUTCOME_FAILED
    assert area["error_kind"] == "incomplete_response"
    assert area["pressure_counts"] == {"output_truncated": 1}
    (event,) = _events(_only_run(RP.ENGINE_RESEARCH), "governing_codes")
    assert event["max_tokens"] == 4096
    assert event["submission"] is False


def test_a_timed_out_warm_wait_is_pressure_on_the_followers(monkeypatch) -> None:
    """test_research_warm_launch's bounded-wait recipe: the stepped clock
    expires the bound on the first check, so the followers are released by
    the bound and never by the lead's output."""
    monkeypatch.setattr(research_engine, "time", _SteppedClock(step=100.0))
    client: _WatchedClient

    def before_first() -> None:
        client.wait_for(_FOLLOWERS)

    from tests.test_research_warm_launch import _run as _run_warm
    from tests.test_research_warm_launch import _scripts as _warm_scripts

    client = _WatchedClient(_warm_scripts(), hold=_LEAD, before_first=before_first)
    profile = _run_warm(client, warm=45)
    assert all(s.status == "completed" for s in profile.dimension_statuses)

    view = _only_run(RP.ENGINE_RESEARCH)
    assert view["starved"] is True
    assert view["starved_agents"] == len(_FOLLOWERS)
    assert view["pressure_counts"] == {"warm_wait_timeout": len(_FOLLOWERS)}
    lead = view["agents"][_LEAD]
    assert lead["warm_wait_outcome"] == "" and lead["starved"] is False
    for ident in _FOLLOWERS:
        follower = view["agents"][ident]
        assert follower["warm_wait_outcome"] == "timeout"
        assert follower["warm_lead"] == _LEAD
        assert follower["pressure_counts"] == {"warm_wait_timeout": 1}
        assert follower["outcome"] == RP.OUTCOME_COMPLETED
        (event,) = _events(view, ident)
        assert event["lead"] == _LEAD
        assert isinstance(event["waited_ms"], int)


def test_a_warm_release_is_a_number_on_the_follower_not_pressure() -> None:
    _run_research(SequencedFakeClient(_scripts()))
    view = _only_run(RP.ENGINE_RESEARCH)
    assert view["starved"] is False
    followers = [agent for ident, agent in view["agents"].items() if ident != _LEAD]
    assert followers and all(f["warm_wait_outcome"] == "warm" for f in followers)
    assert all(f["warm_lead"] == _LEAD for f in followers)
    assert all(isinstance(f["warm_wait_ms"], int) for f in followers)


# ---------------------------------------------------------------------------
# Final QC reports into it
# ---------------------------------------------------------------------------


def test_a_rate_limited_lens_is_recorded_on_the_qc_run(_no_backoff) -> None:
    client = SequencedFakeClient(
        _qc_scripts(
            code_compliance=[
                _rate_limited("4"),
                qc_findings_response("code_compliance", findings=[]),
            ]
        )
    )
    result = _run_qc(client, batch=False)
    assert result.execution_status == "complete"

    view = _only_run(RP.ENGINE_QC)
    assert view["run_id"] == "qc-batch-test"
    assert view["label"] == "Final QC"
    assert view["starved"] is True
    assert view["pressure_counts"] == {"rate_limit": 1}
    lens = view["agents"]["code_compliance"]
    assert lens["kind"] == RP.AGENT_LENS
    assert lens["outcome"] == RP.OUTCOME_COMPLETED
    assert lens["attempts"] == 2
    (event,) = _events(view, "code_compliance")
    assert event["retry_after_s"] == 4.0 and event["mode"] == "restart"
    others = [a for ident, a in view["agents"].items() if ident != "code_compliance"]
    assert others and all(a["starved"] is False for a in others)
    assert all(a["outcome"] == RP.OUTCOME_COMPLETED for a in others)
    # The lenses that followed a leader record the wait as a number only.
    assert any(a["warm_wait_outcome"] == "warm" for a in others)


def test_a_refused_batch_submission_is_recorded_on_every_seat(monkeypatch) -> None:
    monkeypatch.setattr(qc_engine.time, "sleep", lambda _s: None)
    client = SequencedFakeClient(_one_finding_scripts())
    calls = _refuse_submissions(client, on_calls={1})
    result = _run_qc(client)
    assert calls["n"] == 2
    assert result.execution_status == "complete"

    view = _only_run(RP.ENGINE_QC)
    assert view["starved"] is True
    seats = {ident: a for ident, a in view["agents"].items() if ident.startswith("seat-")}
    assert set(seats) == {"seat-0-0", "seat-0-1"}
    for ident, seat in seats.items():
        assert seat["kind"] == RP.AGENT_VERIFIER
        assert seat["outcome"] == RP.OUTCOME_COMPLETED
        assert seat["pressure_counts"] == {"rate_limit": 1}
        (event,) = _events(view, ident)
        assert event["mode"] == "restart"
        assert event["backoff_s"] == 5.0
    assert view["agents"]["code_compliance"]["starved"] is False
    assert view["pressure_counts"] == {"rate_limit": 2}
    # One batch round the provider ran, noted beside the pressures.
    assert view["batch_rounds"] == 1
    note = next(e for e in view["events"] if e["kind"] == RP.NOTE_BATCH_ROUND)
    assert note["agent"] == ""
    assert note["submitted"] == 2 and note["succeeded"] == 2


def test_an_expired_batch_item_is_provider_pressure_on_its_seat() -> None:
    client = SequencedFakeClient(_one_finding_scripts())
    real_results = client.batches.results

    def results(batch_id):
        items = list(real_results(batch_id))
        items[0] = SimpleNamespace(
            custom_id=items[0].custom_id, result=SimpleNamespace(type="expired")
        )
        return items

    client.batches.results = results
    result = _run_qc(client)
    assert result.execution_status == "partial"

    view = _only_run(RP.ENGINE_QC)
    expired = view["agents"]["seat-0-0"]
    assert expired["outcome"] == RP.OUTCOME_FAILED
    assert expired["error_kind"] == "connection"
    assert expired["pressure_counts"] == {"batch_expired": 1}
    assert view["agents"]["seat-0-1"]["outcome"] == RP.OUTCOME_COMPLETED
    assert view["agents"]["seat-0-1"]["starved"] is False
    assert view["starved_agents"] == 1


def test_a_clean_batched_run_notes_its_round_and_is_not_starved() -> None:
    result = _run_qc(SequencedFakeClient(_one_finding_scripts()))
    assert result.execution_status == "complete"

    view = _only_run(RP.ENGINE_QC)
    assert view["starved"] is False
    assert view["pressure_counts"] == {}
    assert view["batch_rounds"] == 1
    assert isinstance(view["batch_wait_ms"], int)
    for ident in ("seat-0-0", "seat-0-1"):
        seat = view["agents"][ident]
        assert seat["outcome"] == RP.OUTCOME_COMPLETED
        assert seat["started_at"] is not None
        assert seat["queued_ms"] is None
    assert RP.snapshot()["totals"][RP.ENGINE_QC]["runs_starved"] == 0


def test_a_qc_run_that_raises_still_closes_its_record(monkeypatch) -> None:
    def boom(*_args, **_kwargs):
        raise RuntimeError("kaput")

    monkeypatch.setattr(qc_engine, "_lens_call_pieces", boom)
    with pytest.raises(RuntimeError):
        _run_qc(SequencedFakeClient(_qc_scripts()), batch=False)
    view = _only_run(RP.ENGINE_QC)
    assert view["status"] == RP.RUN_ENDED
    assert view["agents_total"] == 0


# ---------------------------------------------------------------------------
# The chat turn reports into it
# ---------------------------------------------------------------------------


def test_a_rate_limited_turn_fails_and_is_recorded(monkeypatch) -> None:
    _patch_client(monkeypatch, FakeClient([_rate_limited("9")]))
    resp = _client().post("/api/chat", json={"message": "hello"})
    events = _parse_sse(resp.text)
    assert events[-1]["type"] == "error"
    assert "429" in events[-1]["message"]

    view = _only_run(RP.ENGINE_CHAT)
    assert view["label"] == "turn"
    assert view["starved"] is True
    turn = view["agents"]["turn"]
    assert turn["kind"] == RP.AGENT_TURN
    assert turn["outcome"] == RP.OUTCOME_FAILED
    assert turn["error_kind"] == "rate_limit"
    assert turn["pressure_counts"] == {"rate_limit": 1}
    (event,) = _events(view, "turn")
    assert event["final"] is True
    assert event["retry_after_s"] == 9.0
    assert event["backoff_s"] == 0.0


def test_a_max_tokens_turn_is_output_pressure_and_still_completes(monkeypatch) -> None:
    _patch_client(
        monkeypatch,
        FakeClient([tool_turn(["Partial draft"], _SEED_EDITS, stop_reason="max_tokens")]),
    )
    resp = _client().post("/api/chat", json={"message": "go"})
    assert _parse_sse(resp.text)[-1]["stop_reason"] == "max_tokens"

    view = _only_run(RP.ENGINE_CHAT)
    turn = view["agents"]["turn"]
    assert turn["outcome"] == RP.OUTCOME_COMPLETED
    assert turn["pressure_counts"] == {"output_truncated": 1}
    (event,) = _events(view, "turn")
    assert event["round"] == 0
    assert isinstance(event["max_tokens"], int)


def test_a_clean_turn_leaves_no_pressure(monkeypatch) -> None:
    _patch_client(monkeypatch, FakeClient([text_turn(["Hello."])]))
    resp = _client().post("/api/chat", json={"message": "hi"})
    assert _parse_sse(resp.text)[-1]["type"] == "turn_complete"
    view = _only_run(RP.ENGINE_CHAT)
    assert view["starved"] is False
    assert view["agents"]["turn"]["outcome"] == RP.OUTCOME_COMPLETED
    assert view["agents"]["turn"]["error_kind"] == ""


def test_sdk_retries_on_the_shortened_request_are_the_turns_too(
    monkeypatch, caplog
) -> None:
    """The prompt-too-long fallback opens a second stream; the SDK's retries
    on that open belong to the turn as much as the first's (Codex review on
    PR #282 — the fallback open sat outside the attribution scope)."""
    from backend.llm import conversation
    from tests.fakes import bad_request
    from tests.test_chat_compaction import _chat, _chat_turns, _grow

    caplog.set_level(logging.INFO, logger=RP.SDK_RETRY_LOGGER)
    sdk_log = logging.getLogger(RP.SDK_RETRY_LOGGER)
    real_enter = conversation._enter_stream

    def noisy_enter(*args, **kwargs):
        # The SDK retried once, inside this open, before it succeeded.
        sdk_log.info(
            "Retrying request to %s in %f seconds",
            "https://api.anthropic.com/v1/messages",
            0.25,
        )
        return real_enter(*args, **kwargs)

    monkeypatch.setattr(conversation, "_enter_stream", noisy_enter)
    fake = FakeClient(
        _chat_turns(3)
        + [
            bad_request("prompt is too long: 1204112 tokens > 1000000 maximum"),
            text_turn(["Recovered."]),
        ]
    )
    _patch_client(monkeypatch, fake)
    client = _client()
    _grow(client, 3)
    RP.reset_for_tests()

    events = _chat(client, "One more")
    assert events[-1]["type"] == "turn_complete"

    view = _runs(RP.ENGINE_CHAT)[0]
    turn = view["agents"]["turn"]
    assert turn["outcome"] == RP.OUTCOME_COMPLETED
    # Both opens — the rejected request's and the shortened request's — were
    # the turn's: two SDK retries on the agent, none unattributed.
    assert turn["pressure_counts"] == {"prompt_too_long": 1, "sdk_retry": 2}
    assert turn["sdk_retries"] == 2
    assert turn["sdk_sleep_s"] == 0.5
    assert RP.snapshot()["sdk_retry_observer"]["unattributed_retries"] == 0
    (fallback,) = _events(view, "turn", "prompt_too_long")
    assert fallback["round"] == 0 and fallback["hidden_turns"] >= 1


def test_a_turn_that_fails_before_the_model_is_recorded_failed(monkeypatch) -> None:
    def _boom():
        raise RuntimeError("kaput")

    monkeypatch.setattr("backend.llm.conversation.get_client", _boom)
    _client().post("/api/chat", json={"message": "hello"})
    turn = _only_run(RP.ENGINE_CHAT)["agents"]["turn"]
    assert turn["outcome"] == RP.OUTCOME_FAILED
    assert turn["error_kind"] == "unknown"
    assert turn["starved"] is False


# ---------------------------------------------------------------------------
# Diagnostics carries it
# ---------------------------------------------------------------------------


def test_the_diagnostics_snapshot_carries_the_ledger_intact() -> None:
    run = RP.begin_run(RP.ENGINE_RESEARCH, label="round 3")
    area = run.agent("governing_codes", kind=RP.AGENT_DIMENSION)
    area.started()
    area.retry(failure_class="rate_limit", attempt=1, max_attempts=3, backoff_s=5)
    area.retry(failure_class="rate_limit", attempt=2, max_attempts=3, backoff_s=10)
    area.ended(RP.OUTCOME_COMPLETED, attempts=3)
    run.end()

    resp = TestClient(create_app()).get("/api/diagnostics")
    assert resp.status_code == 200
    block = resp.json()["resource_pressure"]
    assert block["schema_version"] == 1
    assert "<max-depth>" not in resp.text
    (view,) = block["runs"]
    assert view["engine"] == RP.ENGINE_RESEARCH and view["label"] == "round 3"
    assert view["starved"] is True
    assert view["agents"]["governing_codes"]["pressure_counts"] == {"rate_limit": 2}
    assert view["agents"]["governing_codes"]["backoff_s"] == 15.0
    assert block["totals"][RP.ENGINE_RESEARCH]["pressure_counts"] == {"rate_limit": 2}
    assert block["sdk_retry_observer"]["attached"] is True
    assert "queue_pressure_min_ms" in block
