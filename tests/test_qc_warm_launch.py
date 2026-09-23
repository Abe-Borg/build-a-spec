"""Staggered launch for QC calls that share a cached prefix (cost Tier 1, Chunk 2).

A cache entry becomes readable only once the response that writes it begins
streaming, so calls that share a prefix and start together all miss and all
pay to write the same entry. Final QC now sends one call of each shared
lineage first, waits for its first streamed output, and only then sends the
rest. The claim this file defends has two halves:

* **when** — a follower is sent only after its leader has produced output
  (or its first request has ended, or the bounded wait expired, or a Stop
  landed), and a call with no lineage-mate never waits at all;
* **what** — nothing about any request changes. The requests are the same
  multiset either way, and a retained Final QC result stays current with the
  switch in either position.

Every wait here is an event or a stepped clock, never a real sleep (the
Windows lesson in CLAUDE.md, "A test that had only ever run on Linux").
"""
from __future__ import annotations

import ast
import json
import logging
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from backend import settings
from backend.qc import engine
from backend.qc.engine import run_final_qc
from backend.qc.schema import QC_LENSES
from backend.spec_doc.model import DocumentStore
from backend.spec_modules import DEFAULT_MODULE
from tests.fakes import (
    SequencedFakeClient,
    block_start_event,
    qc_findings_response,
    qc_verdict_response,
    user_text,
)
from tests.test_qc_batch_verification import _SteppedClock

_LENS_KEYS = {lens.lens_id: f"[[QC-LENS:{lens.lens_id}]]" for lens in QC_LENSES}
# Derived from the lens table, never hard-coded: the lineage key decides who
# waits, and a later lens change must keep these tests meaning what they say.
_WEB_TOOLLESS = [lens.lens_id for lens in QC_LENSES if not lens.web]
_LEADER = _WEB_TOOLLESS[0]
_FOLLOWERS = set(_WEB_TOOLLESS[1:])
_SOLO = {lens.lens_id for lens in QC_LENSES if lens.web}
_LENS_RE = re.compile(r"\[\[QC-LENS:([a-z_]+)\]\]")
_BUCKET_RE = re.compile(r"\[\[QC-CONSOLIDATE:([^\]]+)\]\]")
_HOLD_TIMEOUT = 10.0


def _store() -> DocumentStore:
    store = DocumentStore()
    store.begin_turn()
    store.apply_edits(
        [
            {
                "action": "replace",
                "target_id": "sec",
                "text": "WET-PIPE SPRINKLER SYSTEMS",
                "numbering": "21 13 13",
            },
            {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"},
            {
                "action": "add_paragraph",
                "target_id": "pt1.a1",
                "text": "Comply with NFPA 13-2019 throughout.",
                "status": "assumed",
            },
            {
                "action": "add_paragraph",
                "target_id": "pt1.a1",
                "text": "Provide a hydraulic calculation for each system.",
                "status": "assumed",
            },
        ]
    )
    store.commit_turn()
    return store


def _scripts(**per_lens: list[object]) -> dict[str, list[object]]:
    return {
        key: list(
            per_lens.get(lens_id, [qc_findings_response(lens_id, findings=[])])
        )
        for lens_id, key in _LENS_KEYS.items()
    }


def _finding(title: str, element_id: str) -> dict:
    return {
        "title": title,
        "severity": "medium",
        "element_id": element_id,
        "issue": f"Issue for {title}.",
        "rationale": f"Rationale for {title}.",
        "source_urls": [],
        "proposed_ops": None,
    }


class _HookedStream:
    """A stream context that runs hooks around its first frame.

    ``before_first`` runs before the first frame is handed over — the leader
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
    """Records which streamed calls arrived, and can hold one of them.

    ``hold`` names the call whose stream runs the hooks: a lens id, or a
    ``bucket:<id>`` for a consolidation call. Arrivals are recorded before
    the scripted turn is resolved, so a scripted failure still counts as a
    request that was sent.
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
        bucket = _BUCKET_RE.search(text)
        if bucket:
            return f"bucket:{bucket.group(1)}"
        lens = _LENS_RE.search(text)
        return lens.group(1) if lens else "other"

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


def _run(client, *, warm: float | None, store=None, sink=None, should_stop=lambda: False):
    store = store or _store()
    return run_final_qc(
        store.doc,
        None,
        DEFAULT_MODULE,
        client,
        model=settings.QC_MODEL,
        max_tokens=4096,
        version_index=store.index,
        started_at="2026-09-23T10:00:00-07:00",
        finished_at="2026-09-23T10:01:00-07:00",
        run_id="qc-warm-test",
        warm_wait_seconds=warm,
        event_sink=sink or (lambda _event: None),
        should_stop=should_stop,
    )


def _wait_records(caplog) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if record.name == "buildaspec.qc" and "share a cached prefix" in record.getMessage()
    ]


# ---------------------------------------------------------------------------
# When: who waits for whom
# ---------------------------------------------------------------------------


def test_followers_start_only_after_the_leader_streams(caplog) -> None:
    observed: dict[str, object] = {}
    client: _WatchedClient

    def before_first() -> None:
        # The web-tooled lens is a lineage of its own, so it must reach the
        # provider while the leader has produced nothing. Held behind the
        # leader, this wait would time out.
        observed["solo_arrived"] = client.wait_for(_SOLO)
        observed["before"] = set(client.arrivals)

    def after_first() -> None:
        observed["followers_arrived"] = client.wait_for(_FOLLOWERS)

    client = _WatchedClient(
        _scripts(),
        hold=_LEADER,
        before_first=before_first,
        after_first=after_first,
    )
    with caplog.at_level(logging.INFO, logger="buildaspec.qc"):
        result = _run(client, warm=45)

    assert observed["solo_arrived"] is True
    assert observed["before"] == {_LEADER, *_SOLO}
    assert observed["followers_arrived"] is True
    assert client.arrivals[0] == _LEADER
    assert set(client.arrivals) == set(_LENS_KEYS)
    # The phase's records still come out in the declared lens order, however
    # the launch was scheduled.
    assert [status.lens_id for status in result.lens_statuses] == [
        lens.lens_id for lens in QC_LENSES
    ]
    assert all(status.status == "completed" for status in result.lens_statuses)
    records = _wait_records(caplog)
    assert len(records) == 1
    message = records[0].getMessage()
    assert "lenses" in message
    assert f"{len(_WEB_TOOLLESS)} calls share a cached prefix" in message
    assert "(warm)" in message


def test_a_leader_that_fails_before_streaming_releases_the_followers(
    monkeypatch,
) -> None:
    """A 429 or a connection drop wrote no entry, so nobody waits out its backoff."""
    observed: dict[str, object] = {}
    sleeps: list[float] = []
    client = _WatchedClient(
        _scripts(
            **{
                _LEADER: [
                    RuntimeError("connection reset by peer"),
                    qc_findings_response(_LEADER, findings=[]),
                ]
            }
        )
    )

    def recording_sleep(seconds: float) -> None:
        # The leader's retry backoff. The followers must already be on their
        # way: had they waited for the leader's first output, they would be
        # stuck behind this very sleep and the wait would time out.
        observed["followers_at_backoff"] = client.wait_for(_FOLLOWERS)
        sleeps.append(seconds)

    monkeypatch.setattr(engine.time, "sleep", recording_sleep)
    result = _run(client, warm=45)

    assert observed["followers_at_backoff"] is True
    assert sleeps == [5.0]
    assert client.arrivals.count(_LEADER) == 2
    statuses = {status.lens_id: status for status in result.lens_statuses}
    assert statuses[_LEADER].status == "completed"
    assert statuses[_LEADER].api_request_count == 2


def test_a_stop_during_the_wait_sends_no_follower_request(monkeypatch, caplog) -> None:
    monkeypatch.setattr(engine, "_WARM_WAIT_SLICE_SECONDS", 0.01)
    stop = threading.Event()
    sink = _Sink()
    observed: dict[str, object] = {}

    def before_first() -> None:
        stop.set()
        observed["followers_started"] = sink.wait_for(
            lambda events: _FOLLOWERS
            <= {
                event["lens_id"]
                for event in events
                if event.get("type") == "lens_started"
            }
        )

    client = _WatchedClient(_scripts(), hold=_LEADER, before_first=before_first)
    with caplog.at_level(logging.INFO, logger="buildaspec.qc"):
        result = _run(client, warm=45, sink=sink, should_stop=stop.is_set)

    assert observed["followers_started"] is True
    assert not (_FOLLOWERS & set(client.arrivals))
    statuses = {status.lens_id: status for status in result.lens_statuses}
    for lens_id in _FOLLOWERS:
        assert statuses[lens_id].status == "failed"
        assert statuses[lens_id].error == "Cancelled by user."
        assert statuses[lens_id].api_request_count == 0
    # The leader's request was already in flight; it finishes and is recorded.
    assert statuses[_LEADER].status == "completed"
    records = _wait_records(caplog)
    assert len(records) == 1
    assert "(stopped)" in records[0].getMessage()


def test_the_wait_is_bounded(monkeypatch, caplog) -> None:
    """A leader that never produces output releases its followers at the bound.

    The stepped clock advances further per reading than the whole wait, so
    the bound expires on the first check — no real second passes.
    """
    monkeypatch.setattr(engine, "time", _SteppedClock(step=100.0))
    observed: dict[str, object] = {}
    client: _WatchedClient

    def before_first() -> None:
        # The leader has produced nothing, and will produce nothing until its
        # followers are sent: only the bound can release them.
        observed["released_by_the_bound"] = client.wait_for(_FOLLOWERS)

    client = _WatchedClient(_scripts(), hold=_LEADER, before_first=before_first)
    with caplog.at_level(logging.INFO, logger="buildaspec.qc"):
        result = _run(client, warm=45)

    assert observed["released_by_the_bound"] is True
    assert all(status.status == "completed" for status in result.lens_statuses)
    records = _wait_records(caplog)
    assert len(records) == 1
    assert "(timeout)" in records[0].getMessage()


def test_zero_wait_launches_everything_at_once(caplog) -> None:
    observed: dict[str, object] = {}
    client: _WatchedClient

    def before_first() -> None:
        # Every other lens reaches the provider while the would-be leader has
        # produced nothing: nobody is waiting on anybody.
        observed["all_arrived"] = client.wait_for(set(_LENS_KEYS) - {_LEADER})

    client = _WatchedClient(_scripts(), hold=_LEADER, before_first=before_first)
    with caplog.at_level(logging.INFO, logger="buildaspec.qc"):
        result = _run(client, warm=0)

    assert observed["all_arrived"] is True
    assert all(status.status == "completed" for status in result.lens_statuses)
    assert _wait_records(caplog) == []


def test_the_setting_reaches_the_run(monkeypatch) -> None:
    """``warm_wait_seconds=None`` reads ``settings.QC_WARM_WAIT_SECONDS``."""
    observed: dict[str, object] = {}
    client: _WatchedClient

    def before_first() -> None:
        observed["all_arrived"] = client.wait_for(set(_LENS_KEYS) - {_LEADER})

    monkeypatch.setattr(settings, "QC_WARM_WAIT_SECONDS", 0)
    client = _WatchedClient(_scripts(), hold=_LEADER, before_first=before_first)
    _run(client, warm=None)
    assert observed["all_arrived"] is True


def test_a_one_worker_pool_keeps_the_lineage_together(monkeypatch) -> None:
    """A pool of one runs the leader, its followers, then everything else.

    The leader goes first, so it is never parked behind a minutes-long call
    while its followers wait on it. And ``code_compliance``, which the pool
    cannot start at once, is queued BEHIND the released followers: queued
    ahead of them, the sole worker would run it between the leader and its
    followers, long enough for the leader's 5-minute entry to expire and a
    follower to pay for a second write (Codex, PR #210).
    """
    monkeypatch.setattr(settings, "QC_MAX_WORKERS", 1)
    client = _WatchedClient(_scripts())
    result = _run(client, warm=45)

    arrivals = client.arrivals
    assert arrivals[0] == _LEADER
    assert set(arrivals[1 : 1 + len(_FOLLOWERS)]) == _FOLLOWERS
    assert set(arrivals[1 + len(_FOLLOWERS) :]) == _SOLO
    assert all(status.status == "completed" for status in result.lens_statuses)


def test_a_single_call_goes_ahead_of_the_wait_only_while_a_worker_is_free(
    caplog,
) -> None:
    """``capacity`` decides where a single-call lineage is queued.

    With a worker free for it, it starts at once and never waits on anyone.
    Without one it would sit in the pool's queue ahead of the followers, so
    it goes behind them instead.
    """
    from concurrent.futures import Future

    def order(capacity: int) -> list[str]:
        submitted: list[str] = []

        def submit(item, _first_output):
            submitted.append(item)
            done: Future = Future()
            done.set_result(item)
            return done

        engine._launch_staggered(
            ["lead", "solo", "follow-1", "follow-2"],
            key_of=lambda item: "solo" if item == "solo" else "shared",
            submit=submit,
            wait_seconds=3600,
            should_stop=lambda: True,
            label="test",
            capacity=capacity,
        )
        return submitted

    with caplog.at_level(logging.INFO, logger="buildaspec.qc"):
        assert order(1) == ["lead", "follow-1", "follow-2", "solo"]
        assert order(2) == ["lead", "solo", "follow-1", "follow-2"]
        assert order(8) == ["lead", "solo", "follow-1", "follow-2"]


def test_the_relay_releases_on_the_first_output_not_on_message_start() -> None:
    """``message_start`` opens the stream; the first frame after it is the
    first output, and only that marks the leader's cache entry readable."""

    def relay(frames) -> bool:
        released = threading.Event()
        engine._relay_stream_activity(
            frames,
            event_prefix="lens",
            event_fields={"lens_id": "x"},
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


def test_a_call_stopped_before_its_first_request_still_releases() -> None:
    """Every return path of ``_run_streaming_call`` releases its followers —
    here a Stop that lands before anything was sent."""
    released = threading.Event()
    client = SequencedFakeClient({})
    result = engine._run_streaming_call(
        client,
        system_prompt="system",
        shared_prefix="prefix",
        request_suffix="suffix",
        tools=[engine.submit_qc_findings_tool(model=settings.QC_MODEL)],
        tool_name=engine.QC_FINDINGS_TOOL_NAME,
        json_tag=engine._FINDINGS_JSON_TAG,
        model=settings.QC_MODEL,
        max_tokens=64,
        effort="high",
        max_searches=0,
        event_prefix="lens",
        event_fields={"lens_id": "x"},
        should_stop=lambda: True,
        first_output=released,
    )
    assert result.error == "Cancelled by user."
    assert client.requests == []
    assert released.is_set()


# ---------------------------------------------------------------------------
# Which calls share a lineage
# ---------------------------------------------------------------------------


def _pieces(lens, *, model=None):
    store = _store()
    return engine._lens_call_pieces(
        lens,
        section=store.doc,
        module=DEFAULT_MODULE,
        profile=None,
        model=model or settings.QC_MODEL,
        today="Tuesday, 23 September 2026",
    )


def test_the_lineage_key_groups_exactly_the_calls_that_share_a_prefix() -> None:
    model = settings.QC_MODEL
    keys = {
        lens.lens_id: engine._pieces_lineage_key(
            _pieces(lens), model=model, effort="high"
        )
        for lens in QC_LENSES
    }
    web_toolless = {keys[lens_id] for lens_id in _WEB_TOOLLESS}
    assert len(web_toolless) == 1
    for lens_id in _SOLO:
        assert keys[lens_id] not in web_toolless
    # The per-lens brief is block 1, after the breakpoint: it never forks.
    assert len({_pieces(lens).request_suffix for lens in QC_LENSES}) == len(QC_LENSES)

    leader = _pieces(next(lens for lens in QC_LENSES if lens.lens_id == _LEADER))
    base = dict(
        tools=leader.tools,
        system_prompt=leader.system_prompt,
        shared_prefix=leader.shared_prefix,
        model=model,
        effort="high",
        cache_ttl="",
    )
    base_key = engine._prefix_lineage_key(**base)
    assert base_key == keys[_LEADER]
    for field, other in (
        ("model", "claude-sonnet-5"),
        ("effort", "medium"),
        ("cache_ttl", "1h"),
        ("tools", [*leader.tools, {"name": "extra", "type": "custom"}]),
        ("system_prompt", leader.system_prompt + " "),
        ("shared_prefix", leader.shared_prefix + " "),
    ):
        assert engine._prefix_lineage_key(**{**base, field: other}) != base_key, field
    # The key reads the tools as the builders return them; building the
    # request stamps its cache breakpoint on a COPY, so sending a call can
    # never change the key its lineage-mates were grouped under.
    engine._qc_request_kwargs(
        system_prompt=leader.system_prompt,
        tools=leader.tools,
        model=model,
        max_tokens=4096,
        effort="high",
        cache_ttl="",
    )
    assert all("cache_control" not in tool for tool in leader.tools)
    assert engine._prefix_lineage_key(**base) == base_key


# ---------------------------------------------------------------------------
# Consolidation
# ---------------------------------------------------------------------------


def _two_bucket_scripts() -> dict[str, list[object]]:
    """Two lenses each raise one defect at each of two elements: two
    eligible consolidation buckets, which share one grouping lineage."""
    first, second = _WEB_TOOLLESS[0], _WEB_TOOLLESS[1]
    titles = {
        first: ["Edition at p1 (a)", "Calcs at p2 (a)"],
        second: ["Edition at p1 (b)", "Calcs at p2 (b)"],
    }
    scripts = _scripts(
        **{
            lens_id: [
                qc_findings_response(
                    lens_id,
                    findings=[
                        _finding(pair[0], "pt1.a1.p1"),
                        _finding(pair[1], "pt1.a1.p2"),
                    ],
                )
            ]
            for lens_id, pair in titles.items()
        }
    )
    for pair in titles.values():
        for title in pair:
            scripts[title] = [qc_verdict_response(True), qc_verdict_response(True)]
    return scripts


def test_consolidation_buckets_stagger_behind_the_first(caplog) -> None:
    lead_bucket = "bucket:element:pt1.a1.p1"
    other_bucket = "bucket:element:pt1.a1.p2"
    observed: dict[str, object] = {}
    client: _WatchedClient

    def before_first() -> None:
        observed["other_before"] = other_bucket in client.arrivals

    def after_first() -> None:
        observed["other_after"] = client.wait_for({other_bucket})

    client = _WatchedClient(
        _two_bucket_scripts(),
        hold=lead_bucket,
        before_first=before_first,
        after_first=after_first,
    )
    with caplog.at_level(logging.INFO, logger="buildaspec.qc"):
        result = _run(client, warm=45)

    assert observed["other_before"] is False
    assert observed["other_after"] is True
    buckets = [name for name in client.arrivals if name.startswith("bucket:")]
    assert buckets == [lead_bucket, other_bucket]
    assert result.consolidation.status == "complete"
    records = [
        record for record in _wait_records(caplog) if "consolidation" in record.getMessage()
    ]
    assert len(records) == 1
    assert "2 calls share a cached prefix" in records[0].getMessage()
    assert "(warm)" in records[0].getMessage()


def test_one_eligible_consolidation_bucket_never_waits(caplog) -> None:
    first, second = _WEB_TOOLLESS[0], _WEB_TOOLLESS[1]
    scripts = _scripts(
        **{
            lens_id: [
                qc_findings_response(
                    lens_id, findings=[_finding(f"Edition ({lens_id})", "pt1.a1.p1")]
                )
            ]
            for lens_id in (first, second)
        }
    )
    for lens_id in (first, second):
        scripts[f"Edition ({lens_id})"] = [
            qc_verdict_response(True),
            qc_verdict_response(True),
        ]
    client = _WatchedClient(scripts)
    with caplog.at_level(logging.INFO, logger="buildaspec.qc"):
        result = _run(client, warm=45)

    assert result.consolidation.status == "complete"
    assert [name for name in client.arrivals if name.startswith("bucket:")] == [
        "bucket:element:pt1.a1.p1"
    ]
    assert not [
        record for record in _wait_records(caplog) if "consolidation" in record.getMessage()
    ]


# ---------------------------------------------------------------------------
# What: nothing about any request changes
# ---------------------------------------------------------------------------


def _canonical_requests(client: SequencedFakeClient) -> list[str]:
    return sorted(
        json.dumps(request, sort_keys=True, default=repr) for request in client.requests
    )


def _fixed_clock(monkeypatch) -> None:
    fixed = datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(engine, "current_datetime", lambda *_a, **_k: fixed)


def test_staggering_changes_no_request_bytes(monkeypatch) -> None:
    _fixed_clock(monkeypatch)
    staggered = SequencedFakeClient(_two_bucket_scripts())
    at_once = SequencedFakeClient(_two_bucket_scripts())
    _run(staggered, warm=45)
    _run(at_once, warm=0)

    assert len(staggered.requests) == len(at_once.requests)
    assert _canonical_requests(staggered) == _canonical_requests(at_once)
    # The consolidation calls and the verifier batch really ran, so the
    # comparison covers every staggered phase, not only the lenses.
    assert any("[[QC-CONSOLIDATE:" in user_text(r["messages"]) for r in staggered.requests)
    assert staggered.batches.created


def test_a_retained_result_stays_current_across_the_switch(monkeypatch) -> None:
    """The plan's F3: transport timing is not a review input."""
    _fixed_clock(monkeypatch)
    store = _store()
    staggered = _run(SequencedFakeClient(_two_bucket_scripts()), warm=45, store=store)
    at_once = _run(SequencedFakeClient(_two_bucket_scripts()), warm=0, store=store)

    assert staggered.input_fingerprint == at_once.input_fingerprint
    assert "warm" not in json.dumps(staggered.input_manifest).lower()
    for wait in (0, 45):
        monkeypatch.setattr(settings, "QC_WARM_WAIT_SECONDS", wait)
        assert staggered.matches_inputs(store.index, store.doc, None, DEFAULT_MODULE)
        assert at_once.matches_inputs(store.index, store.doc, None, DEFAULT_MODULE)


# ---------------------------------------------------------------------------
# The switch
# ---------------------------------------------------------------------------


def test_staggering_ships_switched_on() -> None:
    """Read from the source, so no developer's environment can move it."""
    tree = ast.parse(Path(settings.__file__).read_text(encoding="utf-8"))
    calls = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "QC_WARM_WAIT_SECONDS"
            for target in node.targets
        )
    ]
    assert len(calls) == 1
    call = calls[0]
    assert isinstance(call, ast.Call)
    assert [ast.literal_eval(arg) for arg in call.args] == [
        "BUILD_A_SPEC_QC_WARM_WAIT_SECONDS",
        45,
    ]
    assert {kw.arg: ast.literal_eval(kw.value) for kw in call.keywords} == {
        "minimum": 0
    }


def test_the_launcher_releases_a_leader_whose_task_ends_without_output(
    caplog,
) -> None:
    """A leader that returns before streaming — a stop, or an unexpected
    raise before its first request — still releases its followers at once,
    through the done-callback, rather than holding them to the bound.

    The leader here never touches ``first_output``. Its future is already
    done, so only the done-callback can mark it released; without it the
    loop would fall through to ``should_stop``, which answers yes, and the
    record would say ``stopped`` instead.
    """
    from concurrent.futures import Future

    submitted: list[tuple[str, bool]] = []

    def submit(item, first_output):
        submitted.append((item, first_output is not None))
        done: Future = Future()
        done.set_result(item)
        return done

    with caplog.at_level(logging.INFO, logger="buildaspec.qc"):
        futures = engine._launch_staggered(
            ["a", "b", "c", "solo"],
            key_of=lambda item: "solo" if item == "solo" else "shared",
            submit=submit,
            wait_seconds=3600,
            should_stop=lambda: True,
            label="test",
            capacity=8,
        )
    assert submitted == [("a", True), ("solo", False), ("b", False), ("c", False)]
    assert sorted(futures.values()) == ["a", "b", "c", "solo"]
    records = _wait_records(caplog)
    assert len(records) == 1
    assert "(warm)" in records[0].getMessage()
