"""Final QC streams its verifier seats leaders first (the streamed stagger).

Every verifier seat of one cache lineage carries the same ~50k-token prefix
(the section, the attached dossier, the facts), and a cache entry becomes
readable only once the response that writes it begins streaming. Seats that
start together therefore all miss and each writes that prefix at the write
premium. The streamed verification pool now sends one seat per lineage
first, with a ``first_output`` event, and lets the rest into its
slot-filling queue only when that leader releases them — the same wait
(``_LeaderWatch``) phase 1 runs through ``_launch_staggered``.

This replaces the batched transport as the default. On the run that
motivated it (docs/as-built.md, "Final QC streams its verifier seats,
leaders first") only 39% of the batched seats read the warm lead's copy and
76% of the phase's cost was the prefix written again at the one-hour rate.

The claim this file defends has two halves:

* **when** — a follower seat is sent only after its leader has produced
  output (or its first request has ended, or the bounded wait expired, or a
  Stop landed); nothing waits when the wait is zero;
* **what** — nothing about any request changes except the one documented
  difference between the transports, the cache TTL: streamed seats keep the
  5-minute default, batched seats the hour.

Every wait here is an event or a hook, never a real sleep.
"""
from __future__ import annotations

import ast
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from backend import resource_pressure, settings
from backend.qc import engine
from backend.qc.engine import run_final_qc
from backend.qc.schema import QC_LENSES
from backend.spec_modules import DEFAULT_MODULE
from tests.fakes import (
    SequencedFakeClient,
    qc_findings_response,
    qc_verdict_response,
    user_text,
)
from tests.test_qc_verifier_v3 import _InvalidRequestVerifierClient
from tests.test_qc_warm_launch import _HookedStream, _Sink, _store

_LENS_KEYS = {lens.lens_id: f"[[QC-LENS:{lens.lens_id}]]" for lens in QC_LENSES}
# One lineage per tool kind: the verifier seats of a web-toolless lens share
# a prefix with each other and never with a web-tooled lens's seats, whose
# tools array leads the byte prefix. Derived from the lens table, not named.
_NO_WEB_LENS = next(lens.lens_id for lens in QC_LENSES if not lens.web)
_WEB_LENS = next(lens.lens_id for lens in QC_LENSES if lens.web)
_HOLD_TIMEOUT = 10.0
_TITLES = ("Alpha finding", "Beta finding")


def _finding(title: str, *, severity: str = "medium") -> dict:
    return {
        "title": title,
        "severity": severity,
        "element_id": "pt1.a1.p1",
        "issue": f"Issue for {title}.",
        "rationale": f"Rationale for {title}.",
        "source_urls": [],
        "proposed_ops": None,
    }


def _scripts(
    titles: tuple[str, ...] = _TITLES,
    *,
    lens_id: str = _NO_WEB_LENS,
    verdicts: dict[str, list[object]] | None = None,
) -> dict[str, list[object]]:
    """Lenses raise one medium finding per title (a two-seat panel each)
    from ``lens_id``; every seat upholds unless ``verdicts`` says otherwise."""
    scripts: dict[str, list[object]] = {
        key: [qc_findings_response(other, findings=[])]
        for other, key in _LENS_KEYS.items()
    }
    scripts[_LENS_KEYS[lens_id]] = [
        qc_findings_response(lens_id, findings=[_finding(title) for title in titles])
    ]
    for title in titles:
        scripts[title] = list(
            (verdicts or {}).get(
                title, [qc_verdict_response(True), qc_verdict_response(True)]
            )
        )
    return scripts


def _seat_title(request: dict) -> str:
    text = user_text(request.get("messages") or [])
    return text.split("Reviewing finding: ", 1)[1].split("\n", 1)[0]


class _SeatClient(SequencedFakeClient):
    """Records verifier-seat arrivals in order; can hold the FIRST seat.

    Arrivals are recorded before the scripted turn is resolved, so a
    scripted failure still counts as a request that was sent. With
    ``hold_first_seat``, the first seat's stream runs ``before_first`` before
    yielding anything (the leader has produced nothing yet) and
    ``after_first`` once the relay has taken its first frame.
    """

    def __init__(
        self,
        scripts,
        *,
        hold_first_seat: bool = False,
        before_first=None,
        after_first=None,
    ) -> None:
        super().__init__(scripts)
        self._seat_arrivals: list[str] = []
        self._arrived = threading.Condition()
        self._hold = hold_first_seat
        self._before_first = before_first or (lambda: None)
        self._after_first = after_first or (lambda: None)

    @property
    def seat_arrivals(self) -> list[str]:
        with self._arrived:
            return list(self._seat_arrivals)

    def wait_for_seats(self, count: int, timeout: float = _HOLD_TIMEOUT) -> bool:
        with self._arrived:
            return self._arrived.wait_for(
                lambda: len(self._seat_arrivals) >= count, timeout=timeout
            )

    def stream(self, **request):
        if "[[QC-VERIFY:" not in user_text(request.get("messages") or []):
            return super().stream(**request)
        with self._arrived:
            index = len(self._seat_arrivals)
            self._seat_arrivals.append(_seat_title(request))
            self._arrived.notify_all()
        inner = super().stream(**request)
        if not self._hold or index != 0:
            return inner
        return _HookedStream(
            inner, before_first=self._before_first, after_first=self._after_first
        )


class _BarrierClient(SequencedFakeClient):
    """Lets no seat answer until ``parties`` seats have arrived together."""

    def __init__(self, scripts, *, parties: int) -> None:
        super().__init__(scripts)
        self.barrier = threading.Barrier(parties)
        self.broken = False

    def stream(self, **request):
        if "[[QC-VERIFY:" in user_text(request.get("messages") or []):
            try:
                self.barrier.wait(timeout=_HOLD_TIMEOUT)
            except threading.BrokenBarrierError:
                self.broken = True
        return super().stream(**request)


def _run(
    client,
    *,
    warm: float | None,
    store=None,
    sink=None,
    should_stop=lambda: False,
    batch: bool = False,
):
    store = store or _store()
    return run_final_qc(
        store.doc,
        None,
        DEFAULT_MODULE,
        client,
        model=settings.QC_MODEL,
        max_tokens=4096,
        version_index=store.index,
        started_at="2026-10-07T10:00:00-07:00",
        finished_at="2026-10-07T10:01:00-07:00",
        run_id="qc-streamed-stagger-test",
        batch_verification=batch,
        # The batched lead has its own contract (test_qc_batch_warm_lead.py).
        batch_warm_lead=False,
        warm_wait_seconds=warm,
        event_sink=sink or (lambda _event: None),
        should_stop=should_stop,
    )


def _verification_wait_records(caplog) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if record.name == "buildaspec.qc"
        and "streamed seats share a cached prefix" in record.getMessage()
    ]


def _verdicts(result) -> list:
    return [
        verdict
        for group in (result.findings, result.refuted, result.disputed, result.inconclusive)
        for finding in group
        for verdict in finding.verdicts
    ]


def _seat_requests(client) -> list[dict]:
    return [
        request
        for request in client.requests
        if "[[QC-VERIFY:" in user_text(request["messages"])
    ]


def _marker_ttls(request: dict) -> list[str | None]:
    return [
        block["cache_control"].get("ttl")
        for block in (
            *request["tools"],
            *request["system"],
            *request["messages"][0]["content"],
        )
        if "cache_control" in block
    ]


# ---------------------------------------------------------------------------
# When: who waits for whom
# ---------------------------------------------------------------------------


def test_follower_seats_start_only_after_the_leader_streams(caplog) -> None:
    """Two medium findings from one lens: four seats, one lineage, one leader."""
    observed: dict[str, object] = {}
    sink = _Sink()
    client: _SeatClient

    def before_first() -> None:
        # The leader has produced nothing: no other seat may have been sent,
        # or even started (a follower's ``verifier_started`` is its worker's).
        observed["seats_before"] = client.seat_arrivals
        observed["started_before"] = sum(
            1 for event in sink.events if event["type"] == "verifier_started"
        )

    def after_first() -> None:
        observed["followers_arrived"] = client.wait_for_seats(4)

    client = _SeatClient(
        _scripts(), hold_first_seat=True, before_first=before_first, after_first=after_first
    )
    with caplog.at_level(logging.INFO, logger="buildaspec.qc"):
        result = _run(client, warm=45, sink=sink)

    assert len(observed["seats_before"]) == 1
    assert observed["started_before"] == 1
    assert observed["followers_arrived"] is True
    assert len(client.seat_arrivals) == 4
    assert client.seat_arrivals[0] == observed["seats_before"][0]
    assert result.execution_status == "complete"
    assert sorted(finding.title for finding in result.findings) == sorted(_TITLES)
    assert all(verdict.status == "completed" for verdict in _verdicts(result))
    records = _verification_wait_records(caplog)
    assert len(records) == 1
    message = records[0].getMessage()
    assert "Final QC verification: 4 streamed seats share a cached prefix" in message
    assert "the 3 waiting were released (warm)" in message


def test_a_leader_that_fails_before_streaming_releases_the_followers(
    monkeypatch,
) -> None:
    """A dropped connection wrote no entry, so nobody waits out its backoff."""
    observed: dict[str, object] = {}
    sleeps: list[float] = []
    leader_title = _TITLES[0]
    client = _SeatClient(
        _scripts(
            verdicts={
                leader_title: [
                    RuntimeError("connection reset by peer"),
                    qc_verdict_response(True),
                    qc_verdict_response(True),
                ]
            }
        )
    )

    def recording_sleep(seconds: float) -> None:
        # The leader's retry backoff. The followers must already be on their
        # way: had they waited for the leader's first output, they would be
        # stuck behind this very sleep.
        observed.setdefault("followers_at_backoff", client.wait_for_seats(2))
        sleeps.append(seconds)

    monkeypatch.setattr(engine.time, "sleep", recording_sleep)
    result = _run(client, warm=45)

    assert client.seat_arrivals[0] == leader_title
    assert observed["followers_at_backoff"] is True
    assert sleeps == [5.0]
    assert len(client.seat_arrivals) == 5  # four seats, the leader twice
    assert result.execution_status == "complete"
    leader = max(
        (verdict for verdict in _verdicts(result)), key=lambda v: v.api_request_count
    )
    assert leader.api_request_count == 2
    assert leader.status == "completed"


def test_a_stop_during_the_wait_sends_no_follower_request(monkeypatch, caplog) -> None:
    monkeypatch.setattr(engine, "_WARM_WAIT_SLICE_SECONDS", 0.01)
    stop = threading.Event()
    sink = _Sink()
    observed: dict[str, object] = {}

    def before_first() -> None:
        stop.set()
        # The watch notices the Stop within a slice and lets the followers
        # through; each sees the Stop before sending and returns cancelled.
        observed["followers_cancelled"] = sink.wait_for(
            lambda events: sum(
                1
                for event in events
                if event["type"] == "verifier_complete"
                and event["status"] == "cancelled"
            )
            == 3
        )

    client = _SeatClient(_scripts(), hold_first_seat=True, before_first=before_first)
    with caplog.at_level(logging.INFO, logger="buildaspec.qc"):
        result = _run(client, warm=45, sink=sink, should_stop=stop.is_set)

    assert observed["followers_cancelled"] is True
    assert len(client.seat_arrivals) == 1
    statuses = sorted(verdict.status for verdict in _verdicts(result))
    assert statuses == ["cancelled", "cancelled", "cancelled", "completed"]
    (record,) = _verification_wait_records(caplog)
    assert "(stopped)" in record.getMessage()


def test_a_leaders_invalid_request_trips_the_breaker_after_one_request() -> None:
    """The shared-failure circuit breaker got cheaper: one request, not a
    pool's worth. The leader's 400 is the lineage's, and its followers are
    recorded without ever being sent."""
    client = _InvalidRequestVerifierClient(_scripts())

    result = _run(client, warm=45)

    assert client.verifier_request_count == 1
    assert result.execution_status == "partial"
    verdicts = _verdicts(result)
    assert len(verdicts) == 4
    assert all(verdict.status == "failed" for verdict in verdicts)
    not_started = [
        verdict
        for verdict in verdicts
        if "was not started after a shared request failure" in verdict.error
    ]
    assert len(not_started) == 3
    assert all(verdict.api_request_count == 0 for verdict in not_started)


def test_zero_wait_launches_every_seat_at_once(caplog) -> None:
    """``warm_wait_seconds=0`` is the pre-stagger pool: four seats, four
    workers, all in flight together — no seat can answer until all four
    have arrived, and none has to wait for a leader."""
    client = _BarrierClient(_scripts(), parties=4)
    with caplog.at_level(logging.INFO, logger="buildaspec.qc"):
        result = _run(client, warm=0)

    assert client.broken is False
    assert len(_seat_requests(client)) == 4
    assert result.execution_status == "complete"
    assert _verification_wait_records(caplog) == []


def test_two_lineages_each_have_their_own_leader(caplog) -> None:
    """A web-tooled lens's seats never share a prefix with a web-toolless
    lens's — their tools lead the byte prefix — so each lineage waits on a
    leader of its own, and the two leaders go out together."""
    sink = _Sink()
    scripts = _scripts(("Alpha finding",), lens_id=_NO_WEB_LENS)
    web = _scripts(("Beta finding",), lens_id=_WEB_LENS)
    scripts[_LENS_KEYS[_WEB_LENS]] = web[_LENS_KEYS[_WEB_LENS]]
    scripts["Beta finding"] = web["Beta finding"]
    client = _SeatClient(scripts)
    with caplog.at_level(logging.INFO, logger="buildaspec.qc"):
        result = _run(client, warm=45, sink=sink)

    assert result.execution_status == "complete"
    assert len(client.seat_arrivals) == 4
    records = _verification_wait_records(caplog)
    assert len(records) == 2
    assert all(
        "2 streamed seats share a cached prefix; the 1 waiting were released (warm)"
        in record.getMessage()
        for record in records
    )


def test_seats_are_grouped_by_the_lineage_key() -> None:
    """The key is computed from the real request builders: same lens, same
    key; a web-tooled lens forks it; so does another TTL, should a transport
    ever carry one again."""
    kwargs = dict(
        section_render="<section/>",
        module=DEFAULT_MODULE,
        model=settings.QC_MODEL,
        max_tokens=4096,
        effort="medium",
    )
    no_web = next(lens for lens in QC_LENSES if lens.lens_id == _NO_WEB_LENS)
    web = next(lens for lens in QC_LENSES if lens.lens_id == _WEB_LENS)

    def key(lens, title, **extra):
        spec = engine._verifier_call_spec(
            finding=_finding(title), lens=lens, **kwargs, **extra
        )
        return engine._spec_lineage_key(spec)

    assert key(no_web, "Alpha finding") == key(no_web, "Beta finding")
    assert key(no_web, "Alpha finding") != key(web, "Alpha finding")
    assert key(no_web, "Alpha finding") != key(
        no_web, "Alpha finding", cache_ttl="1h"
    )
    # Both transports store the prefix for 5 minutes since 2026-10-08, so a
    # batched seat and a streamed seat of one lens share one lineage.
    assert key(no_web, "Alpha finding") == key(
        no_web, "Alpha finding", cache_ttl=engine._BATCH_VERIFIER_CACHE_TTL
    )


def test_follower_waits_reach_the_pressure_ledger() -> None:
    """Each follower records how long it waited and for whom; a warm release
    is a number on the seat, never pressure (``backend.resource_pressure``)."""
    _run(_SeatClient(_scripts()), warm=45)

    runs = [
        run
        for run in resource_pressure.snapshot()["runs"]
        if run["engine"] == resource_pressure.ENGINE_QC
    ]
    assert len(runs) == 1
    agents = runs[0]["agents"]
    leader = agents["seat-0-0"]
    assert leader["warm_wait_outcome"] == ""
    for follower_id in ("seat-0-1", "seat-1-0", "seat-1-1"):
        follower = agents[follower_id]
        assert follower["warm_wait_outcome"] == "warm"
        assert follower["warm_lead"] == "seat-0-0"
        assert follower["warm_wait_ms"] is not None
        assert follower["pressure_counts"] == {}
        assert follower["starved"] is False


# ---------------------------------------------------------------------------
# What: the requests
# ---------------------------------------------------------------------------


def _canonical_requests(client) -> list[str]:
    return sorted(
        json.dumps(request, sort_keys=True, default=repr) for request in client.requests
    )


def _fixed_clock(monkeypatch) -> None:
    fixed = datetime(2026, 10, 7, 17, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(engine, "current_datetime", lambda *_a, **_k: fixed)


def test_staggering_changes_no_seat_request_bytes(monkeypatch) -> None:
    _fixed_clock(monkeypatch)
    staggered = SequencedFakeClient(_scripts())
    at_once = SequencedFakeClient(_scripts())
    _run(staggered, warm=45)
    _run(at_once, warm=0)

    assert len(_seat_requests(staggered)) == len(_seat_requests(at_once)) == 4
    assert _canonical_requests(staggered) == _canonical_requests(at_once)


def test_both_transports_store_the_seat_prefix_for_five_minutes(
    monkeypatch,
) -> None:
    """Streamed and batched seats both carry the provider's 5-minute default
    (docs/as-built.md, "The batched seats store their copy for five
    minutes"). The batched seats carried the one-hour TTL until 2026-10-08;
    on the measured run only 39% of them read the shared copy, where a
    one-hour store beats plain input only above 51% and a 5-minute store
    above 21%. Pinned from the requests each transport actually sends."""
    _fixed_clock(monkeypatch)
    streamed = SequencedFakeClient(_scripts())
    _run(streamed, warm=45)
    for request in _seat_requests(streamed):
        assert _marker_ttls(request) == [None, None, None]

    batched = SequencedFakeClient(_scripts())
    _run(batched, warm=0, batch=True)
    assert batched.batches.created
    batched_params = [
        dict(item["params"])
        for batch in batched.batches.created
        for item in batch
    ]
    assert batched_params
    for params in batched_params:
        assert _marker_ttls(params) == [None, None, None]
    assert engine._BATCH_VERIFIER_CACHE_TTL == ""
    assert engine._STREAMED_VERIFIER_CACHE_TTL == ""
    default_spec = engine._verifier_call_spec(
        finding=_finding("Alpha finding"),
        lens=next(lens for lens in QC_LENSES if lens.lens_id == _NO_WEB_LENS),
        section_render="<section/>",
        module=DEFAULT_MODULE,
        model=settings.QC_MODEL,
        max_tokens=4096,
        effort="medium",
    )
    assert default_spec.cache_ttl == engine._STREAMED_VERIFIER_CACHE_TTL


def test_a_retained_result_stays_current_across_the_wait(monkeypatch) -> None:
    """How a seat is scheduled is not a review input."""
    _fixed_clock(monkeypatch)
    store = _store()
    staggered = _run(SequencedFakeClient(_scripts()), warm=45, store=store)
    at_once = _run(SequencedFakeClient(_scripts()), warm=0, store=store)

    assert staggered.input_fingerprint == at_once.input_fingerprint
    for wait in (0, 45):
        monkeypatch.setattr(settings, "QC_WARM_WAIT_SECONDS", wait)
        assert staggered.matches_inputs(store.index, store.doc, None, DEFAULT_MODULE)
        assert at_once.matches_inputs(store.index, store.doc, None, DEFAULT_MODULE)


# ---------------------------------------------------------------------------
# The default
# ---------------------------------------------------------------------------


def test_the_default_transport_ships_streamed() -> None:
    """Read from the source, so no developer's environment can move it."""
    tree = ast.parse(Path(settings.__file__).read_text(encoding="utf-8"))
    assigned = {
        target.id: node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
        and target.id in ("QC_BATCH_VERIFICATION_DEFAULT", "QC_BATCH_VERIFICATION")
    }
    assert ast.literal_eval(assigned["QC_BATCH_VERIFICATION_DEFAULT"]) is False
    call = assigned["QC_BATCH_VERIFICATION"]
    assert isinstance(call, ast.Call)
    assert ast.literal_eval(call.args[0]) == "BUILD_A_SPEC_QC_BATCH_VERIFICATION"
    assert isinstance(call.args[1], ast.Name)
    assert call.args[1].id == "QC_BATCH_VERIFICATION_DEFAULT"
    assert settings.QC_BATCH_VERIFICATION_DEFAULT is False
