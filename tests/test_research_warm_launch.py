"""Staggered launch for research areas that share a cached prefix.

Since PR #269 every area of a round declares the same web tools, and the
system prompt (module-level) and shared block (project-level, with its own
cache breakpoint) are the same for all of them, so the four opening requests
are byte-identical up to that breakpoint
(``test_research_engine.test_the_four_areas_open_with_one_shared_cached_prefix``).
A cache entry becomes readable only once the response that writes it begins
streaming, so four requests sent together each paid to write the same entry.
The research fan-out now sends one lead area first and the rest once its
first output arrives — the shape of Final QC's lens stagger
(``tests/test_qc_warm_launch.py``). The claim this file defends has three
parts:

* **when** — a follower is sent only after its lead has produced output, or
  its first request has ended, or an attempt of its failed, or the bounded
  wait expired, or a Stop landed; an area with no lineage-mate never waits;
* **what** — nothing about any request changes: the requests are the same
  multiset either way, and the merged profile is the same;
* **truthful events** — a follower is announced as waiting on its lead
  (``dimension_waiting``) and reports ``dimension_started`` only once it is
  actually released.

Every wait here is an event or a stepped clock, never a real sleep (the
Windows lesson in docs/as-built.md, "A test that had only ever run on Linux").
"""
from __future__ import annotations

import ast
import dataclasses
import json
import logging
import threading
from concurrent.futures import Future
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from backend import settings
from backend.research import engine
from backend.research.engine import run_requirements_research
from backend.spec_modules.hyperscale_fire import HYPERSCALE_FIRE as MODULE
from tests.fakes import (
    SequencedFakeClient,
    block_start_event,
    pause_response,
    research_response,
    user_text,
)
from tests.test_qc_batch_verification import _SteppedClock
from tests.test_research_engine import DIM_KEYS, PROFILE, _item, _scripts

# Derived from the module, never hard-coded: the lead is the first declared
# area of the lineage, and every shipped area shares one lineage.
_ORDER = [d.dimension_id for d in MODULE.research_dimensions]
_LEAD = _ORDER[0]
_FOLLOWERS = set(_ORDER[1:])
_HOLD_TIMEOUT = 10.0


class _HookedStream:
    """A stream context that runs hooks around its first frame.

    ``before_first`` runs before the first frame is handed over — the lead
    has produced nothing yet. ``after_first`` runs when the relay asks for the
    SECOND frame, i.e. after it has processed the first one and set
    ``first_output``.
    """

    def __init__(self, inner, *, before_first, after_first) -> None:
        self._inner = inner
        self._before_first = before_first
        self._after_first = after_first

    def __enter__(self):
        self._inner.__enter__()
        return self

    def __exit__(self, *exc):
        return self._inner.__exit__(*exc)

    def __iter__(self):
        frames = iter(self._inner)
        self._before_first()
        first = next(frames, None)
        if first is not None:
            yield first
        self._after_first()
        yield from frames

    def get_final_message(self):
        return self._inner.get_final_message()


class _WatchedClient(SequencedFakeClient):
    """Records which areas' streamed requests arrived, and can hold one.

    Arrivals are recorded before the scripted turn is resolved, so a scripted
    failure still counts as a request that was sent. Token counts are not
    arrivals: they are free and write no cache entry.
    """

    def __init__(self, scripts, *, hold: str = "", before_first=None, after_first=None):
        super().__init__(scripts)
        self._arrivals: list[str] = []
        self._arrived = threading.Condition()
        self._hold = hold
        self._before_first = before_first or (lambda: None)
        self._after_first = after_first or (lambda: None)

    @staticmethod
    def call_name(request: dict) -> str:
        text = user_text(request.get("messages") or [])
        for dimension_id, key in DIM_KEYS.items():
            if key in text:
                return dimension_id
        return "other"

    @property
    def arrivals(self) -> list[str]:
        with self._arrived:
            return list(self._arrivals)

    def wait_for(self, names: set[str], timeout: float = _HOLD_TIMEOUT) -> bool:
        with self._arrived:
            return self._arrived.wait_for(
                lambda: names <= set(self._arrivals), timeout=timeout
            )

    def stream(self, **request):
        name = self.call_name(request)
        with self._arrived:
            self._arrivals.append(name)
            self._arrived.notify_all()
        inner = super().stream(**request)
        if name != self._hold:
            return inner
        return _HookedStream(
            inner,
            before_first=self._before_first,
            after_first=self._after_first,
        )


class _Sink:
    """An event sink a test can wait on."""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self._changed = threading.Condition()

    def __call__(self, event: dict) -> None:
        with self._changed:
            self.events.append(event)
            self._changed.notify_all()

    def wait_for(self, predicate, timeout: float = _HOLD_TIMEOUT) -> bool:
        with self._changed:
            return self._changed.wait_for(
                lambda: predicate(list(self.events)), timeout=timeout
            )

    def of(self, event_type: str) -> list[dict]:
        with self._changed:
            return [e for e in self.events if e.get("type") == event_type]


def _run(client, *, warm, module=MODULE, sink=None, should_stop=lambda: False):
    return run_requirements_research(
        module,
        PROFILE,
        client,
        model=settings.RESEARCH_MODEL,
        max_tokens=4096,
        warm_wait_seconds=warm,
        event_sink=sink or (lambda _event: None),
        should_stop=should_stop,
    )


def _wait_records(caplog) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if record.name == "buildaspec.research"
        and "share a cached prefix" in record.getMessage()
    ]


def _statuses(profile) -> dict:
    return {status.dimension_id: status for status in profile.dimension_statuses}


# ---------------------------------------------------------------------------
# When: who waits for whom
# ---------------------------------------------------------------------------


def test_followers_start_only_after_the_lead_streams(caplog) -> None:
    observed: dict[str, object] = {}
    client: _WatchedClient
    sink = _Sink()

    def before_first() -> None:
        # The coordinator announces the followers right after it sends the
        # lead, on its own thread, so wait for that — it never depends on
        # the lead. Then: the lead has produced nothing, so nothing else may
        # have been sent, and no follower may have claimed to start.
        observed["announced"] = sink.wait_for(
            lambda events: _FOLLOWERS
            <= {
                e["dimension_id"]
                for e in events
                if e.get("type") == "dimension_waiting"
            }
        )
        observed["before"] = list(client.arrivals)
        observed["started_before"] = {
            e["dimension_id"] for e in sink.of("dimension_started")
        }
        observed["waiting_before"] = {
            e["dimension_id"]: e for e in sink.of("dimension_waiting")
        }

    def after_first() -> None:
        observed["followers_arrived"] = client.wait_for(_FOLLOWERS)

    client = _WatchedClient(
        _scripts(), hold=_LEAD, before_first=before_first, after_first=after_first
    )
    with caplog.at_level(logging.INFO, logger="buildaspec.research"):
        profile = _run(client, warm=45, sink=sink)

    assert observed["announced"] is True
    assert observed["before"] == [_LEAD]
    # The lead reports itself started before its first request, so it is
    # there; no follower is.
    assert observed["started_before"] == {_LEAD}
    # Every follower was announced as waiting on the lead the moment the lead
    # was sent, with the bound it waits under; the lead waits on nobody.
    waiting = observed["waiting_before"]
    assert set(waiting) == _FOLLOWERS
    for event in waiting.values():
        assert event["lead_id"] == _LEAD
        assert event["max_wait_s"] == 45
    assert observed["followers_arrived"] is True
    assert client.arrivals[0] == _LEAD
    assert set(client.arrivals) == set(_ORDER)
    # The round's records still come out in declaration order, however the
    # launch was scheduled.
    assert [s.dimension_id for s in profile.dimension_statuses] == _ORDER
    assert all(s.status == "completed" for s in profile.dimension_statuses)
    records = _wait_records(caplog)
    assert len(records) == 1
    message = records[0].getMessage()
    assert f"{len(_ORDER)} areas share a cached prefix (lead {_LEAD})" in message
    assert "(warm)" in message


def test_a_followers_events_say_it_waited_then_started() -> None:
    """The board's truth: queued-and-waiting, then started, then terminal."""
    sink = _Sink()
    _run(SequencedFakeClient(_scripts()), warm=45, sink=sink)

    for dimension_id in _ORDER:
        own = [
            e["type"] for e in sink.events if e.get("dimension_id") == dimension_id
        ]
        assert own[-1] == "dimension_complete"
        assert own.count("dimension_started") == 1
        if dimension_id == _LEAD:
            assert "dimension_waiting" not in own
        else:
            assert own.index("dimension_waiting") < own.index("dimension_started")
            assert own.count("dimension_waiting") == 1
    types = [e["type"] for e in sink.events]
    assert types[0] == "research_started"


def test_a_lead_that_fails_before_streaming_releases_the_followers(monkeypatch) -> None:
    """A dropped connection wrote no entry, so nobody waits out its backoff."""
    observed: dict[str, object] = {}
    sleeps: list[float] = []
    client = _WatchedClient(
        _scripts(
            **{
                _LEAD: [
                    RuntimeError("connection reset by peer"),
                    research_response(items=[], searched_urls=["https://x.gov"]),
                ]
            }
        )
    )

    def recording_sleep(seconds: float) -> None:
        # The lead's retry backoff. The followers must already be on their
        # way: had they waited for the lead's first output, they would be
        # stuck behind this very sleep and the wait would time out.
        observed["followers_at_backoff"] = client.wait_for(_FOLLOWERS)
        sleeps.append(seconds)

    monkeypatch.setattr(engine.time, "sleep", recording_sleep)
    profile = _run(client, warm=45)

    assert observed["followers_at_backoff"] is True
    assert sleeps == [5.0]
    assert client.arrivals.count(_LEAD) == 2
    assert all(s.status == "completed" for s in profile.dimension_statuses)


def test_a_failed_token_count_releases_the_followers(monkeypatch) -> None:
    """The count runs before any stream opens; a lead whose count fails has
    written nothing, so its backoff must not hold the others either."""
    observed: dict[str, object] = {}

    class _CountFailsOnce(_WatchedClient):
        failed = False

        def count_tokens(self, **request):
            if self.call_name(request) == _LEAD and not type(self).failed:
                type(self).failed = True
                raise RuntimeError("connection reset by peer")
            return super().count_tokens(**request)

    client = _CountFailsOnce(_scripts())

    def recording_sleep(_seconds: float) -> None:
        observed["followers_at_backoff"] = client.wait_for(_FOLLOWERS)
        # Not one streamed request of the lead's had been sent yet.
        observed["lead_streamed"] = _LEAD in client.arrivals

    monkeypatch.setattr(engine.time, "sleep", recording_sleep)
    profile = _run(client, warm=45)

    assert observed["followers_at_backoff"] is True
    assert observed["lead_streamed"] is False
    assert all(s.status == "completed" for s in profile.dimension_statuses)


def test_a_lead_that_fails_outright_never_holds_the_round(caplog) -> None:
    """A non-retryable failure ends the lead's request at once; the others
    run and the round completes without it."""
    client = _WatchedClient(_scripts(**{_LEAD: [RuntimeError("kaput")]}))
    with caplog.at_level(logging.INFO, logger="buildaspec.research"):
        profile = _run(client, warm=45)

    statuses = _statuses(profile)
    assert statuses[_LEAD].status == "failed"
    assert "kaput" in statuses[_LEAD].error
    for dimension_id in _FOLLOWERS:
        assert statuses[dimension_id].status == "completed"
    records = _wait_records(caplog)
    assert len(records) == 1
    assert "(warm)" in records[0].getMessage()


def test_a_stop_during_the_wait_sends_no_follower_request(monkeypatch, caplog) -> None:
    monkeypatch.setattr(engine, "_WARM_WAIT_SLICE_SECONDS", 0.01)
    stop = threading.Event()
    sink = _Sink()
    observed: dict[str, object] = {}

    def before_first() -> None:
        stop.set()
        # The waiting areas are submitted anyway, see the Stop before they
        # send anything, and report themselves cancelled.
        observed["followers_cancelled"] = sink.wait_for(
            lambda events: _FOLLOWERS
            <= {
                e["dimension_id"]
                for e in events
                if e.get("type") == "dimension_started"
            }
        )

    client = _WatchedClient(_scripts(), hold=_LEAD, before_first=before_first)
    with caplog.at_level(logging.INFO, logger="buildaspec.research"):
        profile = _run(client, warm=45, sink=sink, should_stop=stop.is_set)

    assert observed["followers_cancelled"] is True
    assert not (_FOLLOWERS & set(client.arrivals))
    statuses = _statuses(profile)
    for dimension_id in _FOLLOWERS:
        status = statuses[dimension_id]
        assert status.status == "failed"
        assert status.error == "Cancelled by user."
        assert status.error_kind == engine.DIMENSION_ERROR_CANCELLED
        assert not any(engine.dimension_usage_total([status]).values())
    # The lead's request was already in flight; it finishes and is recorded.
    assert statuses[_LEAD].status == "completed"
    records = _wait_records(caplog)
    assert len(records) == 1
    assert "(stopped)" in records[0].getMessage()


def test_the_wait_is_bounded(monkeypatch, caplog) -> None:
    """A lead that never produces output releases its followers at the bound.

    The stepped clock advances further per reading than the whole wait, so
    the bound expires on the first check — no real second passes.
    """
    monkeypatch.setattr(engine, "time", _SteppedClock(step=100.0))
    observed: dict[str, object] = {}
    client: _WatchedClient

    def before_first() -> None:
        # The lead has produced nothing, and will produce nothing until its
        # followers are sent: only the bound can release them.
        observed["released_by_the_bound"] = client.wait_for(_FOLLOWERS)

    client = _WatchedClient(_scripts(), hold=_LEAD, before_first=before_first)
    with caplog.at_level(logging.INFO, logger="buildaspec.research"):
        profile = _run(client, warm=45)

    assert observed["released_by_the_bound"] is True
    assert all(s.status == "completed" for s in profile.dimension_statuses)
    records = _wait_records(caplog)
    assert len(records) == 1
    assert "(timeout)" in records[0].getMessage()


def test_zero_wait_launches_everything_at_once(caplog) -> None:
    observed: dict[str, object] = {}
    client: _WatchedClient
    sink = _Sink()

    def before_first() -> None:
        # Every other area reaches the provider while the would-be lead has
        # produced nothing: nobody is waiting on anybody.
        observed["all_arrived"] = client.wait_for(_FOLLOWERS)

    client = _WatchedClient(_scripts(), hold=_LEAD, before_first=before_first)
    with caplog.at_level(logging.INFO, logger="buildaspec.research"):
        profile = _run(client, warm=0, sink=sink)

    assert observed["all_arrived"] is True
    assert all(s.status == "completed" for s in profile.dimension_statuses)
    assert _wait_records(caplog) == []
    assert sink.of("dimension_waiting") == []


def test_the_setting_reaches_the_round(monkeypatch) -> None:
    """``warm_wait_seconds=None`` — what the runner passes — reads
    ``settings.RESEARCH_WARM_WAIT_SECONDS``, and only that knob."""
    observed: dict[str, object] = {}
    client: _WatchedClient

    def before_first() -> None:
        observed["all_arrived"] = client.wait_for(_FOLLOWERS)

    monkeypatch.setattr(settings, "RESEARCH_WARM_WAIT_SECONDS", 0)
    monkeypatch.setattr(settings, "QC_WARM_WAIT_SECONDS", 45)
    client = _WatchedClient(_scripts(), hold=_LEAD, before_first=before_first)
    _run(client, warm=None)
    assert observed["all_arrived"] is True


def test_the_qc_knob_does_not_switch_research_off(monkeypatch, caplog) -> None:
    monkeypatch.setattr(settings, "RESEARCH_WARM_WAIT_SECONDS", 45)
    monkeypatch.setattr(settings, "QC_WARM_WAIT_SECONDS", 0)
    client = _WatchedClient(_scripts())
    with caplog.at_level(logging.INFO, logger="buildaspec.research"):
        _run(client, warm=None)
    assert client.arrivals[0] == _LEAD
    assert len(_wait_records(caplog)) == 1


# ---------------------------------------------------------------------------
# Which areas share a lineage
# ---------------------------------------------------------------------------


def _give_own_allowance(monkeypatch, dimension_id: str) -> int:
    """Give one area fewer searches per request than the rest — so its tool
    bytes, and its cache lineage, are its own. Every shipped area declares
    the same allowance by design, whatever budget it declares
    (``engine._per_request_allowance`` reads none of it), so the seam is the
    function itself, as a module-specific exception would use it. Returns
    the searches the area declares."""
    real = engine._per_request_allowance
    searches = engine.RESEARCH_SEARCHES_PER_REQUEST - 4

    def own(dimension):
        if dimension.dimension_id == dimension_id:
            return searches, engine.RESEARCH_FETCHES_PER_REQUEST
        return real(dimension)

    monkeypatch.setattr(engine, "_per_request_allowance", own)
    return searches


def test_an_area_with_its_own_tool_bytes_never_waits(caplog, monkeypatch) -> None:
    solo = _ORDER[-1]
    followers = _FOLLOWERS - {solo}
    observed: dict[str, object] = {}
    client: _WatchedClient
    sink = _Sink()

    def before_first() -> None:
        # The area whose tools differ is a lineage of its own: it reaches the
        # provider while the lead has produced nothing.
        observed["solo_arrived"] = client.wait_for({solo})
        observed["before"] = set(client.arrivals)

    def after_first() -> None:
        observed["followers_arrived"] = client.wait_for(followers)

    client = _WatchedClient(
        _scripts(), hold=_LEAD, before_first=before_first, after_first=after_first
    )
    own_searches = _give_own_allowance(monkeypatch, solo)
    with caplog.at_level(logging.INFO, logger="buildaspec.research"):
        profile = _run(client, warm=45, sink=sink)

    assert observed["solo_arrived"] is True
    assert observed["before"] == {_LEAD, solo}
    assert observed["followers_arrived"] is True
    assert {e["dimension_id"] for e in sink.of("dimension_waiting")} == followers
    assert all(s.status == "completed" for s in profile.dimension_statuses)
    solo_tools = {
        tool["name"]: tool
        for request in client.requests
        if _WatchedClient.call_name(request) == solo
        for tool in request["tools"]
    }
    assert solo_tools["web_search"]["max_uses"] == own_searches
    records = _wait_records(caplog)
    assert len(records) == 1
    assert f"{len(_ORDER) - 1} areas share a cached prefix" in records[0].getMessage()


def test_the_lineage_key_follows_exactly_what_precedes_the_breakpoint() -> None:
    model = settings.RESEARCH_MODEL
    searches, fetches = engine._per_request_allowance(MODULE.research_dimensions[0])
    tools = engine._research_tools(
        searches=searches, fetches=fetches, profile=PROFILE, model=model
    )
    shared, _task = engine.build_dimension_user_message(
        MODULE, PROFILE, MODULE.research_dimensions[0], today="Tuesday, 6 October 2026"
    )
    base = dict(
        tools=tools,
        system_prompt=engine.build_research_system_prompt(MODULE),
        shared=shared,
        model=model,
        effort="medium",
    )
    base_key = engine._opening_lineage_key(**base)
    for field, other in (
        ("model", "claude-sonnet-5"),
        ("effort", "high"),
        ("tools", [*tools[:-1], {**tools[-1], "name": "other"}]),
        ("system_prompt", base["system_prompt"] + " "),
        ("shared", shared + " "),
    ):
        assert engine._opening_lineage_key(**{**base, field: other}) != base_key, field
    # Every area of the shipped module renders the same shared half — the
    # per-area brief is the task block, after the breakpoint, and never forks.
    halves = {
        engine.build_dimension_user_message(
            MODULE, PROFILE, d, today="Tuesday, 6 October 2026"
        )[0]
        for d in MODULE.research_dimensions
    }
    assert halves == {shared}


def test_a_key_that_cannot_be_built_launches_that_area_unstaggered(monkeypatch) -> None:
    """A lineage key never fails a round: the area it could not key starts at
    once on its own, and its worker reports whatever went wrong."""
    real = engine.build_dimension_user_message
    solo = _ORDER[-1]
    keyed: list[str] = []

    def flaky(module, profile, dimension, *args, **kwargs):
        # Only the coordinator's key calls pass no established facts; the
        # worker's own call goes through untouched.
        if dimension.dimension_id == solo and "established_facts" not in kwargs:
            keyed.append(dimension.dimension_id)
            raise KeyError("template")
        return real(module, profile, dimension, *args, **kwargs)

    monkeypatch.setattr(engine, "build_dimension_user_message", flaky)
    sink = _Sink()
    profile = _run(SequencedFakeClient(_scripts()), warm=45, sink=sink)
    assert keyed == [solo]
    assert solo not in {e["dimension_id"] for e in sink.of("dimension_waiting")}
    assert all(s.status == "completed" for s in profile.dimension_statuses)


# ---------------------------------------------------------------------------
# The launcher itself
# ---------------------------------------------------------------------------


def _done(item) -> Future:
    future: Future = Future()
    future.set_result(item)
    return future


def _dims(*ids: str) -> list[SimpleNamespace]:
    return [SimpleNamespace(dimension_id=i, title=i.title()) for i in ids]


def test_a_single_area_goes_ahead_of_the_wait_only_while_a_worker_is_free(caplog) -> None:
    """``capacity`` decides where a single-area lineage is queued: with a
    worker free for it, it starts at once; without one it goes behind the
    released followers, never in the pool's queue ahead of them."""

    def order(capacity: int) -> list[str]:
        submitted: list[str] = []

        def submit(dimension, _first_output):
            submitted.append(dimension.dimension_id)
            return _done(dimension)

        engine._launch_staggered(
            _dims("lead", "solo", "follow1", "follow2"),
            key_of=lambda d: "solo" if d.dimension_id == "solo" else "shared",
            submit=submit,
            wait_seconds=3600,
            should_stop=lambda: True,
            capacity=capacity,
            on_wait=lambda _follower, _lead: None,
        )
        return submitted

    with caplog.at_level(logging.INFO, logger="buildaspec.research"):
        assert order(1) == ["lead", "follow1", "follow2", "solo"]
        assert order(2) == ["lead", "solo", "follow1", "follow2"]
        assert order(4) == ["lead", "solo", "follow1", "follow2"]


def test_the_launcher_releases_a_lead_whose_task_ends_without_output(caplog) -> None:
    """A lead that returns before streaming — a Stop, or an unexpected raise
    before its first request — still releases its followers at once, through
    the done-callback, rather than holding them to the bound.

    The lead here never touches ``first_output``. Its future is already done,
    so only the done-callback can mark it released; without it the loop would
    fall through to ``should_stop``, which answers yes, and the record would
    say ``stopped`` instead.
    """
    submitted: list[tuple[str, bool]] = []
    announced: list[tuple[str, str]] = []

    def submit(dimension, first_output):
        submitted.append((dimension.dimension_id, first_output is not None))
        return _done(dimension)

    with caplog.at_level(logging.INFO, logger="buildaspec.research"):
        futures = engine._launch_staggered(
            _dims("a", "b", "c"),
            key_of=lambda _d: "shared",
            submit=submit,
            wait_seconds=3600,
            should_stop=lambda: True,
            capacity=4,
            on_wait=lambda follower, lead: announced.append(
                (follower.dimension_id, lead.dimension_id)
            ),
        )
    assert submitted == [("a", True), ("b", False), ("c", False)]
    assert announced == [("b", "a"), ("c", "a")]
    assert sorted(d.dimension_id for d in futures.values()) == ["a", "b", "c"]
    records = _wait_records(caplog)
    assert len(records) == 1
    assert "(warm)" in records[0].getMessage()


def test_no_shared_lineage_announces_nothing_and_never_waits() -> None:
    submitted: list[tuple[str, bool]] = []
    announced: list[str] = []

    def submit(dimension, first_output):
        submitted.append((dimension.dimension_id, first_output is not None))
        return _done(dimension)

    engine._launch_staggered(
        _dims("a", "b"),
        key_of=lambda d: d.dimension_id,
        submit=submit,
        wait_seconds=3600,
        should_stop=lambda: False,
        capacity=4,
        on_wait=lambda follower, _lead: announced.append(follower.dimension_id),
    )
    assert submitted == [("a", False), ("b", False)]
    assert announced == []


def test_the_relay_releases_on_the_first_output_not_on_message_start() -> None:
    """``message_start`` opens the stream; the first frame after it is the
    first output, and only that marks the lead's cache entry readable."""

    def relay(frames) -> bool:
        released = threading.Event()
        engine._relay_stream_activity(
            frames,
            dimension_id="x",
            event_sink=lambda _event: None,
            activity_state={"kind": ""},
            first_output=released,
        )
        return released.is_set()

    assert relay([SimpleNamespace(type="message_start")]) is False
    assert relay([]) is False
    assert (
        relay([SimpleNamespace(type="message_start"), block_start_event(0, "thinking")])
        is True
    )


def test_a_request_that_streams_nothing_still_releases_when_it_ends() -> None:
    """A response with no streamed frames (here: only search results, which
    stream nothing) still releases its waiting areas once its request ends."""
    released = threading.Event()
    client = SequencedFakeClient(
        {
            DIM_KEYS[_LEAD]: [
                pause_response(searched_urls=["https://a.gov/one"]),
                research_response(items=[], searched_urls=["https://a.gov/two"]),
            ]
        }
    )
    seen: list[bool] = []
    original = client.stream

    def stream(**request):
        # Recorded as each request opens: the first opens unreleased, and
        # the second opens only after the first one ended.
        seen.append(released.is_set())
        return original(**request)

    client.stream = stream
    outcome = engine._run_dimension(
        client,
        module=MODULE,
        profile=PROFILE,
        dimension=MODULE.research_dimensions[0],
        model=settings.RESEARCH_MODEL,
        max_tokens=4096,
        first_output=released,
    )
    assert outcome.status.status == "completed"
    assert seen == [False, True]
    assert released.is_set()


# ---------------------------------------------------------------------------
# What: nothing about any request, or the merged profile, changes
# ---------------------------------------------------------------------------


def _canonical_requests(client: SequencedFakeClient) -> list[str]:
    return sorted(
        json.dumps(request, sort_keys=True, default=repr) for request in client.requests
    )


def _fixed_clock(monkeypatch) -> None:
    fixed = datetime(2026, 10, 6, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(engine, "current_datetime", lambda *_a, **_k: fixed)


def _rich_scripts() -> dict[str, list]:
    """A pause on one follower and grounded items on two areas, so the
    comparison covers continuations, grounding and the merge."""
    follower = _ORDER[1]
    return _scripts(
        **{
            _LEAD: [
                research_response(
                    items=[_item("VCC 2021 governs.", ["https://dhcd.virginia.gov/vcc"])],
                    searched_urls=["https://dhcd.virginia.gov/vcc"],
                )
            ],
            follower: [
                pause_response(searched_urls=["https://a.gov/one"]),
                research_response(
                    items=[_item("Uses page one.", ["https://a.gov/one"])],
                    searched_urls=["https://b.gov/two"],
                ),
            ],
        }
    )


def test_staggering_changes_no_request_bytes_and_no_finding(monkeypatch) -> None:
    _fixed_clock(monkeypatch)
    staggered = SequencedFakeClient(_rich_scripts())
    at_once = SequencedFakeClient(_rich_scripts())
    staggered_profile = _run(staggered, warm=45)
    at_once_profile = _run(at_once, warm=0)

    assert len(staggered.requests) == len(at_once.requests) == len(_ORDER) + 1
    assert _canonical_requests(staggered) == _canonical_requests(at_once)

    def findings(profile) -> list[tuple]:
        return [
            (i.dimension_id, i.requirement, i.grounded, tuple(i.accepted_sources))
            for i in profile.items
        ]

    assert findings(staggered_profile) == findings(at_once_profile)
    assert [dataclasses.asdict(s) for s in staggered_profile.dimension_statuses] == [
        dataclasses.asdict(s) for s in at_once_profile.dimension_statuses
    ]
    assert any(i.grounded for i in staggered_profile.items)


# ---------------------------------------------------------------------------
# The switch
# ---------------------------------------------------------------------------


def test_staggering_ships_switched_on_with_its_own_knob() -> None:
    """Read from the source, so no developer's environment can move it."""
    tree = ast.parse(Path(settings.__file__).read_text(encoding="utf-8"))
    calls = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "RESEARCH_WARM_WAIT_SECONDS"
            for target in node.targets
        )
    ]
    assert len(calls) == 1
    call = calls[0]
    assert isinstance(call, ast.Call)
    assert isinstance(call.func, ast.Name) and call.func.id == "_int_env"
    assert [ast.literal_eval(arg) for arg in call.args] == [
        "BUILD_A_SPEC_RESEARCH_WARM_WAIT_SECONDS",
        45,
    ]
    assert {kw.arg: ast.literal_eval(kw.value) for kw in call.keywords} == {
        "minimum": 0
    }
