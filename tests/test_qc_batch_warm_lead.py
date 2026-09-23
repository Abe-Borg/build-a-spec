"""A streamed lead seat warms the batched verifier cache (cost Tier 1, Chunk 3).

A batch submits its verifier seats together, so how many of them read the
shared document prefix and how many pay to write it is up to how the provider
schedules the batch. When one cache lineage carries many seats, one of them is
now streamed FIRST, at list price, and the batch goes out only after that
seat's first output, so the rest can read its cache entry. The claim this
file defends has four halves:

* **when** — the batch is created only after every lead's first output (or
  the lead's first request ended, or the bounded wait expired), a Stop in
  that wait sends no batch at all, and a lineage below its minimum never
  gets a lead;
* **what** — the lead is an ordinary seat: same request bytes, same verdict
  rules, its own live frames, and a record priced at list while the batched
  seats' are priced at the batch rate;
* **every exit** — the lead's billed record reaches the report on every path
  out of the phase, and the phase's last frame counts it;
* **off means off** — with the switch off (its shipped default) the phase is
  the batch it was before this chunk, byte for byte, and a retained Final QC
  result stays current with the switch in either position (the plan's F3).

Every wait here is an event, never a real sleep (the Windows lesson in
CLAUDE.md, "A test that had only ever run on Linux").
"""
from __future__ import annotations

import ast
import json
import logging
import re
import sys
import threading
import time as _real_time
from pathlib import Path
from types import SimpleNamespace

from backend import settings
from backend.qc import engine
from backend.qc.engine import QCResult, run_final_qc
from backend.qc.schema import QC_LENSES
from backend.spec_doc.docx_export import (
    QC_WARM_LEAD_METHODOLOGY_NOTE,
    build_qc_memo,
    qc_streamed_lead_seats,
)
from backend.spec_modules import DEFAULT_MODULE
from tests.fakes import (
    SequencedFakeClient,
    pause_response,
    qc_findings_response,
    qc_verdict_response,
    search_result_block,
    user_text,
)
from tests.test_qc_batch_verification import _bad_request, _finding, _scripts, _store
from tests.test_qc_warm_launch import _canonical_requests, _fixed_clock

# Derived from the lens table, never hard-coded: a verifier seat's lineage is
# decided by its tools, and only a web lens's seats carry the web tools.
_WEB_LENS = next(lens.lens_id for lens in QC_LENSES if lens.web)
_DOC_LENS = next(lens.lens_id for lens in QC_LENSES if not lens.web)
_SEAT_RE = re.compile(r"\[\[QC-VERIFY:[a-z_]+\]\] Reviewing finding: ([^\n]+)\n")
_HOLD_TIMEOUT = 10.0
_WARM = 45
_REPO = Path(__file__).resolve().parents[1]

_VERDICT_TOKENS = {"input": 40, "output": 900, "cache_read": 30_000}
# A lead's own usage differs from every batched seat's, so a test can find
# it in a record or a meter bucket and cannot mistake it for another seat.
_LEAD_TOKENS = {"input": 70, "output": 1_300, "cache_write": 32_000}


def _titles(prefix: str, count: int) -> list[str]:
    # Zero-padded, so no title is a substring of another: the fake client
    # routes a request by substring.
    return [f"{prefix} {number:02d}" for number in range(1, count + 1)]


def _lineage_scripts(
    *,
    web: list[tuple[str, str]] = (),
    doc: list[tuple[str, str]] = (),
    lead_first: dict[str, list[object]] | None = None,
) -> dict[str, list[object]]:
    """Scripts for a run whose findings fill the two verifier lineages.

    ``web`` findings come from the web lens (their seats carry the web tools),
    ``doc`` findings from a document-only lens. Each entry is ``(title,
    severity)``. ``lead_first`` replaces the FIRST scripted turns of a
    title's queue: a lead streams before its batch is created, so the lead
    of a lineage — the first seat of that lineage's first finding — always
    takes its title's first turn.
    """
    scripts = _scripts(
        **{
            _WEB_LENS: [
                qc_findings_response(
                    _WEB_LENS,
                    findings=[_finding(title, severity=sev) for title, sev in web],
                )
            ],
            _DOC_LENS: [
                qc_findings_response(
                    _DOC_LENS,
                    findings=[_finding(title, severity=sev) for title, sev in doc],
                )
            ],
        }
    )
    for title, severity in [*web, *doc]:
        seats = engine._panel_size(severity)
        scripts[title] = [
            qc_verdict_response(True, tokens=_VERDICT_TOKENS) for _ in range(seats)
        ]
    for title, turns in (lead_first or {}).items():
        scripts[title][: len(turns)] = list(turns)
    return scripts


def _medium(titles: list[str]) -> list[tuple[str, str]]:
    return [(title, "medium") for title in titles]


def _lead_verdict(*, query: str = "", url: str = "") -> SimpleNamespace:
    """An upholding verdict with the lead's own usage, optionally searching."""
    turn = qc_verdict_response(True, tokens=_LEAD_TOKENS)
    if query:
        use_id = "srvtoolu_lead_search"
        turn.content[:0] = [
            SimpleNamespace(
                type="server_tool_use",
                id=use_id,
                name="web_search",
                input={"query": query},
            ),
            search_result_block([url], tool_use_id=use_id),
        ]
    return turn


def _minimums_at_the_floor(monkeypatch) -> None:
    """Both lineage minimums at 8 — the floor — so a fixture stays small."""
    monkeypatch.setattr(engine, "_WARM_LEAD_MIN_SEATS_WEB", 8)
    monkeypatch.setattr(engine, "_WARM_LEAD_MIN_SEATS_NO_WEB", 8)


class _SeatStream:
    """A verifier seat's stream, with hooks around its first frame.

    ``before_first`` runs before any frame is handed over: the seat has
    produced nothing, so its ``first_output`` cannot be set. ``after_first``
    runs when the relay asks for the SECOND frame, i.e. after it has
    processed the first one.
    """

    def __init__(self, inner, *, title: str, client: "_LeadClient") -> None:
        self._inner = inner
        self._title = title
        self._client = client

    def __enter__(self):
        self._inner.__enter__()
        return self

    def __exit__(self, *exc):
        return self._inner.__exit__(*exc)

    def __iter__(self):
        frames = iter(self._inner)
        self._client.before_first(self._title)
        first = next(frames, None)
        if first is not None:
            with self._client.lock:
                self._client.handed_over.add(self._title)
            yield first
        self._client.after_first(self._title)
        yield from frames

    def get_final_message(self):
        return self._inner.get_final_message()


class _LeadClient(SequencedFakeClient):
    """Records how the phase's requests reached the provider.

    A verifier request that arrives through ``stream()`` in a batched run is
    a lead — batched seats reach the fake through ``batches.create``. Each
    lead's stream runs the hooks; ``batches.create`` and ``batches.results``
    can be intercepted to stand in for a refusal or to release a held lead.
    """

    def __init__(
        self,
        scripts,
        *,
        before_first=None,
        after_first=None,
        on_create=None,
        on_results=None,
    ) -> None:
        super().__init__(scripts)
        self.lock = threading.Lock()
        self.streamed: list[str] = []
        self.batches_at_stream: list[int] = []
        self.handed_over: set[str] = set()
        self.handed_over_at_create: list[set[str]] = []
        self._before_first = before_first or (lambda _title: None)
        self._after_first = after_first or (lambda _title: None)
        real_create = self.batches.create
        real_results = self.batches.results

        def create(*, requests):
            with self.lock:
                self.handed_over_at_create.append(set(self.handed_over))
            if on_create is not None:
                on_create(requests)
            return real_create(requests=requests)

        def results(batch_id):
            items = real_results(batch_id)
            if on_results is not None:
                on_results(batch_id)
            return items

        self.batches.create = create
        self.batches.results = results

    def before_first(self, title: str) -> None:
        self._before_first(title)

    def after_first(self, title: str) -> None:
        self._after_first(title)

    def stream(self, **request):
        match = _SEAT_RE.search(user_text(request.get("messages") or []))
        if match is None:
            return super().stream(**request)
        title = match.group(1)
        with self.lock:
            self.streamed.append(title)
            self.batches_at_stream.append(len(self.batches.created))
        inner = super().stream(**request)
        return _SeatStream(inner, title=title, client=self)


class _Sink:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self._lock = threading.Lock()

    def __call__(self, event: dict) -> None:
        with self._lock:
            self.events.append(event)

    def of(self, event_type: str) -> list[dict]:
        with self._lock:
            return [event for event in self.events if event["type"] == event_type]


def _run(
    client,
    *,
    lead: bool = True,
    warm: float = _WARM,
    batch: bool = True,
    sink=None,
    should_stop=lambda: False,
    store=None,
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
        started_at="2026-09-23T10:00:00-07:00",
        finished_at="2026-09-23T10:01:00-07:00",
        run_id="qc-warm-lead-test",
        batch_verification=batch,
        batch_warm_lead=lead,
        warm_wait_seconds=warm,
        event_sink=sink or (lambda _event: None),
        should_stop=should_stop,
    )


def _lead_records(caplog) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "buildaspec.qc"
        and record.getMessage().startswith("Final QC verification:")
    ]


def _verdicts(result: QCResult):
    return {
        (finding.title, verdict.reviewer_index): verdict
        for finding in [
            *result.findings,
            *result.refuted,
            *result.disputed,
            *result.inconclusive,
        ]
        for verdict in finding.verdicts
    }


def _batched_ids(client: SequencedFakeClient) -> list[str]:
    return [request["custom_id"] for batch in client.batches.created for request in batch]


# ---------------------------------------------------------------------------
# When: who goes first, and who never goes at all
# ---------------------------------------------------------------------------


def test_one_lead_per_large_lineage_streams_before_the_batch_is_created(
    monkeypatch, caplog
) -> None:
    _minimums_at_the_floor(monkeypatch)
    web, doc = _titles("Web gap", 4), _titles("Doc gap", 4)
    client = _LeadClient(_lineage_scripts(web=_medium(web), doc=_medium(doc)))
    sink = _Sink()
    with caplog.at_level(logging.INFO, logger="buildaspec.qc"):
        result = _run(client, sink=sink)

    # One lead per lineage: the first seat of each lineage's first finding.
    assert sorted(client.streamed) == sorted([web[0], doc[0]])
    # Both reached the provider before any batch existed ...
    assert client.batches_at_stream == [0, 0]
    # ... and the batch went out only after both had produced output.
    assert client.handed_over_at_create == [{web[0], doc[0]}]
    # Every other seat rides the batch, each under its own custom_id.
    assert len(client.batches.created) == 1
    ids = _batched_ids(client)
    assert len(ids) == len(set(ids)) == 14
    roster = sink.of("verification_started")[0]["candidates"]
    firsts = {
        lens: next(
            index for index, entry in enumerate(roster) if entry["lens_id"] == lens
        )
        for lens in (_WEB_LENS, _DOC_LENS)
    }
    leads = {f"seat-{index}-0" for index in firsts.values()}
    every = {f"seat-{i}-{j}" for i in range(len(roster)) for j in range(2)}
    assert set(ids) == every - leads

    assert result.execution_status == "complete"
    verdicts = _verdicts(result)
    assert verdicts[(web[0], 1)].status == "completed"
    assert verdicts[(doc[0], 1)].status == "completed"
    records = _lead_records(caplog)
    assert len(records) == 2
    assert all("8" in message and "(warm)" in message for message in records)
    assert any("web-tooled" in message for message in records)
    assert any("no-web" in message for message in records)


def test_a_lineage_below_the_minimum_has_no_lead(caplog) -> None:
    """At the shipped minimums: 19 seats get no lead, 20 seats get one."""
    web = [(title, "critical") for title in _titles("Web gap", 5)]
    web += _medium(_titles("Web note", 2))  # 5 x 3 + 2 x 2 = 19 seats
    doc = _medium(_titles("Doc gap", 10))  # 10 x 2 = 20 seats
    assert engine._warm_lead_minimum(engine.LINEAGE_WEB_TOOLED) == 20
    assert engine._warm_lead_minimum(engine.LINEAGE_NO_WEB) == 20
    client = _LeadClient(_lineage_scripts(web=web, doc=doc))
    with caplog.at_level(logging.INFO, logger="buildaspec.qc"):
        result = _run(client)

    assert client.streamed == ["Doc gap 01"]
    assert len(client.batches.created) == 1
    assert len(_batched_ids(client)) == 19 + 20 - 1
    assert result.execution_status == "complete"
    records = _lead_records(caplog)
    assert len(records) == 1
    assert "20 no-web seats" in records[0]


def test_a_lead_that_fails_before_streaming_still_releases_the_batch(
    monkeypatch, caplog
) -> None:
    """A refused first request wrote no entry, so nobody waits out the bound.

    Released by the request's end, not by the 45-second bound: the record
    says ``warm``, and a wait held to the bound would say ``timeout``.
    """
    _minimums_at_the_floor(monkeypatch)
    doc = _titles("Doc gap", 4)
    client = _LeadClient(
        _lineage_scripts(doc=_medium(doc), lead_first={doc[0]: [_bad_request()]})
    )
    with caplog.at_level(logging.INFO, logger="buildaspec.qc"):
        result = _run(client)

    assert client.streamed == [doc[0]]
    assert len(client.batches.created) == 1
    assert len(_batched_ids(client)) == 7
    lead = _verdicts(result)[(doc[0], 1)]
    assert lead.status == "failed"
    assert "Invalid verifier output schema." in lead.error
    assert lead.cost_multiplier == 1.0
    # The rest of the lineage still reached its verdicts through the batch.
    assert [finding.title for finding in result.findings] == doc[1:]
    assert [finding.title for finding in result.inconclusive] == [doc[0]]
    assert result.execution_status == "partial"
    records = _lead_records(caplog)
    assert len(records) == 1 and "(warm)" in records[0]


def test_a_lead_in_its_retry_backoff_does_not_hold_the_batch(monkeypatch) -> None:
    """A lead that has to retry has already released the batch."""
    _minimums_at_the_floor(monkeypatch)
    doc = _titles("Doc gap", 4)
    created = threading.Event()
    observed: dict[str, object] = {}
    sleeps: list[float] = []
    client = _LeadClient(
        _lineage_scripts(
            doc=_medium(doc),
            lead_first={doc[0]: [ConnectionError("peer closed connection")]},
        ),
        on_create=lambda _requests: created.set(),
    )
    # One more upholding turn for the lead's retry.
    client._scripts[doc[0]].append(qc_verdict_response(True, tokens=_VERDICT_TOKENS))

    def recording_sleep(seconds: float) -> None:
        # The lead's retry backoff. Had the batch waited on the lead's first
        # OUTPUT, it would be stuck behind this very sleep.
        observed["batch_at_backoff"] = created.wait(_HOLD_TIMEOUT)
        sleeps.append(seconds)

    monkeypatch.setattr(engine.time, "sleep", recording_sleep)
    result = _run(client)

    assert observed["batch_at_backoff"] is True
    assert sleeps == [5.0]
    assert client.streamed == [doc[0], doc[0]]
    lead = _verdicts(result)[(doc[0], 1)]
    assert lead.status == "completed"
    assert lead.api_request_count == 2
    assert result.execution_status == "complete"


def test_a_stop_during_the_lead_wait_submits_no_batch_and_joins_the_lead(
    monkeypatch, caplog
) -> None:
    _minimums_at_the_floor(monkeypatch)
    monkeypatch.setattr(engine, "_WARM_WAIT_SLICE_SECONDS", 0.01)
    doc = _titles("Doc gap", 4)
    stop = threading.Event()
    stopped_logged = threading.Event()
    observed: dict[str, object] = {}

    class _Watch(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            message = record.getMessage()
            if message.startswith("Final QC verification:") and "(stopped)" in message:
                stopped_logged.set()

    def before_first(_title: str) -> None:
        # The lead has produced nothing yet. Stop, and hold the lead until
        # the wait has noticed — so the batch decision is made while it runs.
        stop.set()
        observed["wait_noticed"] = stopped_logged.wait(_HOLD_TIMEOUT)

    client = _LeadClient(
        _lineage_scripts(doc=_medium(doc), lead_first={doc[0]: [_lead_verdict()]}),
        before_first=before_first,
    )
    watch = _Watch()
    logger = logging.getLogger("buildaspec.qc")
    logger.addHandler(watch)
    try:
        with caplog.at_level(logging.INFO, logger="buildaspec.qc"):
            result = _run(client, should_stop=stop.is_set)
    finally:
        logger.removeHandler(watch)

    assert observed["wait_noticed"] is True
    assert client.batches.created == []
    verdicts = _verdicts(result)
    # The lead was joined: its in-flight request finished and is recorded.
    lead = verdicts[(doc[0], 1)]
    assert lead.status == "completed"
    assert lead.api_request_count == 1
    assert lead.usage_totals.get("output_tokens") == _LEAD_TOKENS["output"]
    # Nothing else was sent, so nothing else was billed.
    others = [verdict for key, verdict in verdicts.items() if key != (doc[0], 1)]
    assert len(others) == 7
    assert all(verdict.status == "cancelled" for verdict in others)
    assert all(verdict.api_request_count == 0 for verdict in others)
    assert result.execution_status == "partial"
    records = _lead_records(caplog)
    assert len(records) == 1 and "(stopped)" in records[0]


def test_a_lead_that_raises_is_recorded_failed_and_releases_the_batch(
    monkeypatch, caplog
) -> None:
    """``_run_streaming_call`` never raises by contract; if it ever did, the
    lead must still release the batch (its task ended) and still leave a
    record, rather than taking the phase down or vanishing from the panel."""
    _minimums_at_the_floor(monkeypatch)
    doc = _titles("Doc gap", 4)
    real_call = engine._run_streaming_call

    def exploding(*args, **kwargs):
        if kwargs.get("event_prefix") == "verifier":
            raise RuntimeError("lead exploded")
        return real_call(*args, **kwargs)

    monkeypatch.setattr(engine, "_run_streaming_call", exploding)
    client = _LeadClient(_lineage_scripts(doc=_medium(doc)))
    with caplog.at_level(logging.INFO, logger="buildaspec.qc"):
        result = _run(client)

    lead = _verdicts(result)[(doc[0], 1)]
    assert lead.status == "failed"
    assert "RuntimeError: lead exploded" in lead.error
    assert lead.api_request_count == 0
    assert len(_batched_ids(client)) == 7
    assert result.execution_status == "partial"
    # Released by the task ending — never held to the bound.
    records = _lead_records(caplog)
    assert len(records) == 1 and "(warm)" in records[0]


def _watch_lead_pool(monkeypatch) -> threading.Event:
    """An event set once a lead's task is DONE — its future resolved — so a
    test can wait for exactly the state a fold looks for."""
    done = threading.Event()
    real = engine.ThreadPoolExecutor

    class Watched(real):
        def submit(self, *args, **kwargs):
            future = super().submit(*args, **kwargs)
            if self._thread_name_prefix == "qc-lead":
                future.add_done_callback(lambda _future: done.set())
            return future

    monkeypatch.setattr(engine, "ThreadPoolExecutor", Watched)
    return done


def test_a_finished_lead_counts_toward_the_batch_line_at_the_next_poll(
    monkeypatch,
) -> None:
    """Folded at every poll, so the board counts the lead as soon as it is
    done — not only when the whole phase ends.

    The lead is held until the batch exists, so the fold at the top of the
    round cannot have counted it: only a poll can.
    """
    _minimums_at_the_floor(monkeypatch)
    lead_done = _watch_lead_pool(monkeypatch)
    doc = _titles("Doc gap", 4)
    release = threading.Event()

    def created(_requests) -> None:
        release.set()
        assert lead_done.wait(_HOLD_TIMEOUT)

    client = _held_lead_client(
        _lineage_scripts(doc=_medium(doc)), release, on_create=created
    )
    sink = _Sink()
    result = _run(client, sink=sink)

    frames = sink.of("verification_batch")
    submitted = next(frame for frame in frames if frame["status"] == "submitted")
    polling = next(frame for frame in frames if frame["status"] == "polling")
    assert submitted["settled"] == 0
    assert polling["settled"] == 1
    assert result.execution_status == "complete"


def test_a_finished_lead_counts_toward_the_batch_line_at_the_next_round(
    monkeypatch,
) -> None:
    """Folded at every round boundary too, so a second round's first frame
    already counts a lead that finished during the first round's read."""
    _minimums_at_the_floor(monkeypatch)
    lead_done = _watch_lead_pool(monkeypatch)
    doc = _titles("Doc gap", 4)
    release = threading.Event()
    scripts = _lineage_scripts(doc=_medium(doc))
    # Round 1 leaves one seat paused; its continuation settles in round 2.
    scripts[doc[1]][0] = pause_response()
    scripts[doc[1]].append(qc_verdict_response(True, tokens=_VERDICT_TOKENS))

    def read(_batch_id) -> None:
        release.set()
        assert lead_done.wait(_HOLD_TIMEOUT)

    client = _held_lead_client(scripts, release, on_results=read)
    sink = _Sink()
    result = _run(client, sink=sink)

    second = next(
        frame
        for frame in sink.of("verification_batch")
        if frame["status"] == "submitted" and frame.get("round") == 2
    )
    # Round 1 settled six of its seven batched seats; the lead makes seven.
    assert second["settled"] == 7
    assert second["total"] == 8
    assert result.execution_status == "complete"


# ---------------------------------------------------------------------------
# Every exit joins the lead
# ---------------------------------------------------------------------------


class _LeapClock:
    """The engine's ``time``, until armed; then every reading is far past
    any ceiling, and the first one releases a held lead.

    Armed from inside ``batches.results``, so the first reading after it is
    the next round's wall-clock check — which runs AFTER that round's
    non-blocking fold, so the held lead is still out when the phase ends.
    """

    def __init__(self, on_leap) -> None:
        self.armed = False
        self._on_leap = on_leap
        self._lock = threading.Lock()

    def monotonic(self) -> float:
        with self._lock:
            if not self.armed:
                return _real_time.monotonic()
            self._on_leap()
            return _real_time.monotonic() + 10**9

    def __getattr__(self, name: str):
        return getattr(_real_time, name)


def _held_lead_client(scripts, release: threading.Event, **hooks) -> _LeadClient:
    """The lead produces its first output, then waits for ``release``."""
    held: dict[str, bool] = {}

    def after_first(_title: str) -> None:
        held["released"] = release.wait(_HOLD_TIMEOUT)

    client = _LeadClient(scripts, after_first=after_first, **hooks)
    client.held = held  # type: ignore[attr-defined]
    return client


def _assert_lead_recorded(result: QCResult, title: str, sink: _Sink) -> None:
    lead = _verdicts(result)[(title, 1)]
    assert lead.status == "completed"
    assert lead.cost_multiplier == 1.0
    assert lead.usage_totals.get("output_tokens") == _LEAD_TOKENS["output"]
    assert lead.usage_totals.get("cache_creation_input_tokens") == (
        _LEAD_TOKENS["cache_write"]
    )
    # The phase's last frame counts every seat, the lead included: joined
    # BEFORE the frame, not only before the outcome.
    last = sink.of("verification_batch")[-1]
    assert last["settled"] == last["total"]


def test_every_return_path_joins_the_leads_on_a_refused_submission(
    monkeypatch,
) -> None:
    _minimums_at_the_floor(monkeypatch)
    doc = _titles("Doc gap", 4)
    release = threading.Event()

    def refuse(_requests) -> None:
        release.set()
        raise _bad_request()

    client = _held_lead_client(
        _lineage_scripts(doc=_medium(doc), lead_first={doc[0]: [_lead_verdict()]}),
        release,
        on_create=refuse,
    )
    sink = _Sink()
    result = _run(client, sink=sink)

    assert client.held == {"released": True}
    assert sink.of("verification_batch")[-1]["status"] == "failed"
    _assert_lead_recorded(result, doc[0], sink)
    others = [v for key, v in _verdicts(result).items() if key != (doc[0], 1)]
    assert all(v.status == "failed" for v in others)
    assert all("Invalid verifier output schema." in v.error for v in others)


def test_every_return_path_joins_the_leads_at_the_wall_clock_ceiling(
    monkeypatch,
) -> None:
    _minimums_at_the_floor(monkeypatch)
    doc = _titles("Doc gap", 4)
    release = threading.Event()
    clock = _LeapClock(on_leap=release.set)
    monkeypatch.setattr(engine, "time", clock)
    # Round 1 leaves one batched seat paused, so the phase needs a round 2.
    scripts = _lineage_scripts(doc=_medium(doc), lead_first={doc[0]: [_lead_verdict()]})
    scripts[doc[1]][0] = pause_response()

    def arm(_batch_id) -> None:
        clock.armed = True

    client = _held_lead_client(scripts, release, on_results=arm)
    sink = _Sink()
    result = _run(client, sink=sink)

    assert client.held == {"released": True}
    assert len(client.batches.created) == 1
    assert sink.of("verification_batch")[-1]["status"] == "timeout"
    _assert_lead_recorded(result, doc[0], sink)
    paused = _verdicts(result)[(doc[1], 1)]
    assert paused.status == "failed"
    assert "wall-clock ceiling" in paused.error
    assert result.execution_status == "partial"


def test_every_return_path_joins_the_leads_at_the_round_ceiling(monkeypatch) -> None:
    _minimums_at_the_floor(monkeypatch)
    monkeypatch.setattr(settings, "QC_BATCH_MAX_ROUNDS", 1)
    doc = _titles("Doc gap", 4)
    release = threading.Event()
    scripts = _lineage_scripts(doc=_medium(doc), lead_first={doc[0]: [_lead_verdict()]})
    scripts[doc[1]][0] = pause_response()
    client = _held_lead_client(
        scripts, release, on_results=lambda _batch_id: release.set()
    )
    sink = _Sink()
    result = _run(client, sink=sink)

    assert client.held == {"released": True}
    assert sink.of("verification_batch")[-1]["status"] == "ended"
    _assert_lead_recorded(result, doc[0], sink)
    paused = _verdicts(result)[(doc[1], 1)]
    assert paused.status == "failed"
    assert "round ceiling" in paused.error


# ---------------------------------------------------------------------------
# What: an ordinary seat, priced at list
# ---------------------------------------------------------------------------


def test_the_lead_is_priced_at_list_and_the_rest_at_the_batch_rate(monkeypatch) -> None:
    _minimums_at_the_floor(monkeypatch)
    doc = _titles("Doc gap", 4)
    client = _LeadClient(
        _lineage_scripts(doc=_medium(doc), lead_first={doc[0]: [_lead_verdict()]})
    )
    result = _run(client)

    verdicts = _verdicts(result)
    assert verdicts[(doc[0], 1)].cost_multiplier == 1.0
    batched = [v for key, v in verdicts.items() if key != (doc[0], 1)]
    assert len(batched) == 7
    assert all(v.cost_multiplier == settings.BATCH_COST_MULTIPLIER for v in batched)

    # The meter files the lead with the list-price phase 1, the rest apart.
    buckets = result.usage_by_meter_category()
    assert buckets["qc_batched"]["output_tokens"] == 7 * _VERDICT_TOKENS["output"]
    lens_output = sum(
        status.usage_totals.get("output_tokens", 0) for status in result.lens_statuses
    )
    consolidation_output = (
        result.consolidation.usage_totals.get("output_tokens", 0)
        if result.consolidation is not None
        else 0
    )
    assert buckets["qc"]["output_tokens"] == (
        lens_output + consolidation_output + _LEAD_TOKENS["output"]
    )

    # The report reproduces its own arithmetic, and survives a reload.
    assert result._audit_accounting_consistent()
    reloaded = QCResult.from_dict(result.to_dict())
    assert reloaded is not None
    assert reloaded.estimated_cost_usd == result.estimated_cost_usd
    reloaded_multipliers = sorted(
        v.cost_multiplier for v in _verdicts(reloaded).values()
    )
    assert reloaded_multipliers == sorted(v.cost_multiplier for v in verdicts.values())


def test_a_lead_still_lowers_the_reported_run_cost(monkeypatch) -> None:
    """Batching must still pay with one seat per lineage back at list price.

    The fake has no cache, so every seat reports the same usage whatever its
    transport: the lead can only cost its own batch discount here, never
    save anything. That puts the run strictly between the two.
    """
    _minimums_at_the_floor(monkeypatch)
    doc = _titles("Doc gap", 4)

    def cost(**kwargs) -> float:
        return _run(
            _LeadClient(_lineage_scripts(doc=_medium(doc))), **kwargs
        ).estimated_cost_usd

    streamed = cost(batch=False)
    with_lead = cost(lead=True)
    without_lead = cost(lead=False)
    assert without_lead < with_lead < streamed


def test_a_run_with_a_lead_reaches_the_same_verdicts(monkeypatch) -> None:
    """Parity is the point: a lead changes transport, never adjudication."""
    _minimums_at_the_floor(monkeypatch)
    web, doc = _titles("Web gap", 4), _titles("Doc gap", 4)

    def outcome(**kwargs):
        result = _run(
            _LeadClient(_lineage_scripts(web=_medium(web), doc=_medium(doc))), **kwargs
        )
        return {
            "execution_status": result.execution_status,
            "upheld": [f.title for f in result.findings],
            "refuted": [f.title for f in result.refuted],
            "disputed": [f.title for f in result.disputed],
            "inconclusive": [f.title for f in result.inconclusive],
            "panel": [
                (f.title, v.reviewer_index, v.status, v.upholds)
                for f in result.findings
                for v in f.verdicts
            ],
            "seat_requests": [
                v.api_request_count for f in result.findings for v in f.verdicts
            ],
        }

    assert outcome(lead=True) == outcome(lead=False) == outcome(batch=False)


def test_the_lead_streams_its_own_frames_and_the_batch_stays_quiet(
    monkeypatch,
) -> None:
    _minimums_at_the_floor(monkeypatch)
    web = _titles("Web gap", 4)
    client = _LeadClient(
        _lineage_scripts(
            web=_medium(web),
            lead_first={
                web[0]: [
                    _lead_verdict(
                        query="NFPA 13 hanger spacing",
                        url="https://example.org/nfpa-13",
                    )
                ]
            },
        )
    )
    sink = _Sink()
    _run(client, sink=sink)

    roster = sink.of("verification_started")[0]["candidates"]
    lead_candidate = next(
        entry["candidate_id"] for entry in roster if entry["title"] == web[0]
    )
    live = [
        event
        for event in sink.events
        if event["type"].startswith("verifier_")
        and event["type"] not in {"verifier_started", "verifier_complete"}
    ]
    assert live, "the lead streamed, so it must say so"
    assert {(e["candidate_id"], e["reviewer_index"]) for e in live} == {
        (lead_candidate, 1)
    }
    searches = [e for e in live if e["type"] == "verifier_search"]
    assert [e["query"] for e in searches] == ["NFPA 13 hanger spacing"]
    # Every seat still announces itself once, and every verdict — the lead's
    # included — lands only after the phase returns.
    assert len(sink.of("verifier_started")) == 8
    types = [event["type"] for event in sink.events]
    last_batch = max(i for i, t in enumerate(types) if t == "verification_batch")
    completes = [i for i, t in enumerate(types) if t == "verifier_complete"]
    assert len(completes) == 8
    assert min(completes) > last_batch
    # The phase-level count on the last batch frame includes the lead.
    assert sink.of("verification_batch")[-1]["settled"] == 8
    assert sink.of("verification_batch")[-1]["total"] == 8
    # The roster event is untouched: no new keys for the lead.
    assert set(sink.of("verification_started")[0]) == {
        "type",
        "candidates",
        "total_candidates",
        "total_seats",
        "max_workers",
        "transport",
    }


# ---------------------------------------------------------------------------
# Off means off
# ---------------------------------------------------------------------------


def test_the_switch_off_is_todays_batch_exactly(monkeypatch) -> None:
    """Switched off, inert at a zero wait, or on a lineage too small for a
    lead, the phase is one batch of every seat, with the same bytes."""
    _fixed_clock(monkeypatch)
    doc = _titles("Doc gap", 4)  # 8 seats

    def run(**kwargs) -> _LeadClient:
        client = _LeadClient(_lineage_scripts(doc=_medium(doc)))
        result = _run(client, **kwargs)
        assert all(
            v.cost_multiplier == settings.BATCH_COST_MULTIPLIER
            for v in _verdicts(result).values()
        )
        return client

    off = run(lead=False)
    too_small = run(lead=True)  # 8 seats, below the shipped minimum of 20
    # At the floor the same lineage qualifies, so only the zero wait keeps
    # the switch inert here.
    _minimums_at_the_floor(monkeypatch)
    inert = run(lead=True, warm=0)
    off_at_the_floor = run(lead=False)
    for client in (off, too_small, inert, off_at_the_floor):
        assert client.streamed == []
        assert len(client.batches.created) == 1
        assert len(client.batches.created[0]) == 8
        assert _canonical_requests(client) == _canonical_requests(off)
        assert client.batches.created == off.batches.created

    # With a lead, the SAME requests go out: one of them streamed instead.
    with_lead = _LeadClient(_lineage_scripts(doc=_medium(doc)))
    _run(with_lead, lead=True)
    assert len(with_lead.streamed) == 1
    assert len(with_lead.requests) == len(off.requests)
    assert _canonical_requests(with_lead) == _canonical_requests(off)


def test_a_retained_result_stays_current_across_the_switch(monkeypatch) -> None:
    """The plan's F3: how a seat is sent is not a review input."""
    _fixed_clock(monkeypatch)
    _minimums_at_the_floor(monkeypatch)
    store = _store()
    doc = _titles("Doc gap", 4)
    led = _run(_LeadClient(_lineage_scripts(doc=_medium(doc))), lead=True, store=store)
    plain = _run(_LeadClient(_lineage_scripts(doc=_medium(doc))), lead=False, store=store)

    assert qc_streamed_lead_seats(led.to_dict()) == 1
    assert led.input_fingerprint == plain.input_fingerprint
    assert "lead" not in json.dumps(led.input_manifest).lower()
    for switch in (False, True):
        monkeypatch.setattr(settings, "QC_BATCH_WARM_LEAD", switch)
        assert led.matches_inputs(store.index, store.doc, None, DEFAULT_MODULE)
        assert plain.matches_inputs(store.index, store.doc, None, DEFAULT_MODULE)


def test_the_qc_profiler_shows_the_lead_as_its_own_list_price_row(
    monkeypatch, tmp_path
) -> None:
    """The plan's M3 reads the lead off the profiler; it must be there.

    A lineage's lead is its own ``seat:list-price:<lineage>`` row with one
    record, beside the batched row with the rest — the two rows M3's
    arithmetic compares. Run on the real tool, over a real record.
    """
    import importlib.util

    _minimums_at_the_floor(monkeypatch)
    doc = _titles("Doc gap", 4)
    led = _run(_LeadClient(_lineage_scripts(doc=_medium(doc))), lead=True)
    export = tmp_path / "qc-export.json"
    export.write_text(json.dumps({"report": led.to_dict()}), encoding="utf-8")
    spec = importlib.util.spec_from_file_location(
        "qc_export_cost_profile", _REPO / "tools" / "qc_export_cost_profile.py"
    )
    profiler = importlib.util.module_from_spec(spec)
    # Registered before it runs: its dataclasses resolve their own module.
    monkeypatch.setitem(sys.modules, spec.name, profiler)
    spec.loader.exec_module(profiler)
    out = tmp_path / "profile.md"
    assert profiler.main([str(export), "--out", str(out)]) == 0

    rows = {
        line.split("|")[1].strip(): line.split("|")[2].strip()
        for line in out.read_text(encoding="utf-8").splitlines()
        if line.startswith("| `seat:")
    }
    assert rows["`seat:list-price:no-web`"] == "1"
    assert rows["`seat:batched:no-web` ×½"] == "7"


# ---------------------------------------------------------------------------
# The report says so — only when it happened
# ---------------------------------------------------------------------------


def _memo_text(result: QCResult) -> str:
    import io

    from docx import Document

    store = _store()
    document = Document(io.BytesIO(build_qc_memo(result.to_dict(), store.doc, stale=False)))
    return "\n".join(paragraph.text for paragraph in document.paragraphs)


def test_the_methodology_sentence_is_the_same_in_both_projections(monkeypatch) -> None:
    source = (_REPO / "frontend" / "src" / "lib" / "qcReport.ts").read_text(
        encoding="utf-8"
    )
    match = re.search(
        r'export const QC_WARM_LEAD_METHODOLOGY_NOTE =\s*"((?:[^"\\]|\\.)*)";', source
    )
    assert match is not None
    assert json.loads(f'"{match.group(1)}"') == QC_WARM_LEAD_METHODOLOGY_NOTE

    # The modal renders it only for a run that sent a lead, as the memo does.
    modal = (_REPO / "frontend" / "src" / "components" / "QCReportModal.tsx").read_text(
        encoding="utf-8"
    )
    assert re.search(
        r"qcStreamedLeadSeats\(report\) > 0\s*\?\s*\[\[\"Streamed lead seat\", "
        r"QC_WARM_LEAD_METHODOLOGY_NOTE\]\]",
        modal,
    )

    _minimums_at_the_floor(monkeypatch)
    doc = _titles("Doc gap", 4)
    led = _run(_LeadClient(_lineage_scripts(doc=_medium(doc))), lead=True)
    plain = _run(_LeadClient(_lineage_scripts(doc=_medium(doc))), lead=False)
    streamed = _run(_LeadClient(_lineage_scripts(doc=_medium(doc))), batch=False)

    text = _memo_text(led)
    assert f"Streamed lead seat. {QC_WARM_LEAD_METHODOLOGY_NOTE}" in text
    assert text.count(QC_WARM_LEAD_METHODOLOGY_NOTE) == 1
    for other in (plain, streamed):
        assert "Streamed lead seat" not in _memo_text(other)
        assert qc_streamed_lead_seats(other.to_dict()) == 0


def test_a_record_without_multipliers_reports_no_lead() -> None:
    """A report from before batched verification reads as list price
    throughout — which is not a lead, because nothing was discounted."""
    legacy = {
        "findings": [
            {"verdicts": [{"reviewer_index": 1}, {"reviewer_index": 2}]},
        ],
        "refuted": [{"verdicts": [{"cost_multiplier": float("nan")}]}],
    }
    assert qc_streamed_lead_seats(legacy) == 0
    mixed = {
        "findings": [
            {"verdicts": [{"cost_multiplier": 1.0}, {"cost_multiplier": 0.5}]},
        ],
        # Neither a bool nor a non-finite float is a multiplier, so both read
        # as list price — never as a discount (False is 0.0 as a number) and
        # never as nothing at all. The frontend mirror reads them the same way.
        "inconclusive": [
            {"verdicts": [{"cost_multiplier": False}, {"cost_multiplier": float("nan")}]}
        ],
    }
    assert qc_streamed_lead_seats(mixed) == 3


# ---------------------------------------------------------------------------
# The minimums and the switch
# ---------------------------------------------------------------------------


def _doc_specs(count: int) -> dict[str, engine._CallSpec]:
    return {
        f"seat-{index}-0": engine._CallSpec(
            system_prompt="system",
            shared_prefix="the document",
            request_suffix=f"finding {index}",
            tools=({"name": "submit_qc_verdict"},),
            tool_name="submit_qc_verdict",
            json_tag="VERDICT",
            model=settings.QC_MODEL,
            max_tokens=4096,
            effort="medium",
            max_searches=0,
            cache_ttl="1h",
        )
        for index in range(count)
    }


def test_the_lineage_minimums_are_never_below_eight(monkeypatch) -> None:
    assert engine._WARM_LEAD_SEAT_FLOOR == 8
    assert engine._WARM_LEAD_MIN_SEATS_WEB >= 8
    assert engine._WARM_LEAD_MIN_SEATS_NO_WEB >= 8
    # And the floor holds at runtime, whatever a constant is lowered to.
    monkeypatch.setattr(engine, "_WARM_LEAD_MIN_SEATS_WEB", 2)
    monkeypatch.setattr(engine, "_WARM_LEAD_MIN_SEATS_NO_WEB", 2)
    assert engine._warm_lead_minimum(engine.LINEAGE_WEB_TOOLED) == 8
    assert engine._warm_lead_minimum(engine.LINEAGE_NO_WEB) == 8
    assert engine._pick_warm_leads(_doc_specs(7)) == []
    picked = engine._pick_warm_leads(_doc_specs(8))
    assert [(lead.key, lead.kind, lead.lineage_size) for lead in picked] == [
        ("seat-0-0", engine.LINEAGE_NO_WEB, 8)
    ]


def test_a_lineage_is_what_the_seats_send_not_which_lens_asked() -> None:
    """Seats sharing a prefix are one lineage; a different system prompt or
    a web tool splits them — the same key Chunk 2's staggering uses."""
    specs = _doc_specs(8)
    first = specs["seat-0-0"]
    specs["seat-9-0"] = engine._CallSpec(
        **{**first.__dict__, "tools": ({"name": "web_search"}, {"name": "submit_qc_verdict"})}
    )
    specs["seat-10-0"] = engine._CallSpec(**{**first.__dict__, "system_prompt": "other"})
    keys = {engine._spec_lineage_key(spec) for spec in specs.values()}
    assert len(keys) == 3
    assert engine._spec_lineage_kind(specs["seat-9-0"]) == engine.LINEAGE_WEB_TOOLED
    assert engine._spec_lineage_kind(first) == engine.LINEAGE_NO_WEB
    # The per-seat suffix is after the breakpoint and forks nothing.
    assert len({engine._spec_lineage_key(specs[f"seat-{i}-0"]) for i in range(8)}) == 1


def test_warm_lead_ships_switched_off() -> None:
    """Read from the source, so no developer's environment can move it.

    The default flips only on a recorded M3 pass (the plan's Chunk 3
    "Flip"), which replaces this test with its switched-on twin.
    """
    tree = ast.parse(Path(settings.__file__).read_text(encoding="utf-8"))
    calls = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "QC_BATCH_WARM_LEAD"
            for target in node.targets
        )
    ]
    assert len(calls) == 1
    call = calls[0]
    assert isinstance(call, ast.Call)
    assert isinstance(call.func, ast.Name) and call.func.id == "_bool_env"
    assert [ast.literal_eval(arg) for arg in call.args] == [
        "BUILD_A_SPEC_QC_BATCH_WARM_LEAD",
        False,
    ]


def test_the_setting_reaches_the_run(monkeypatch) -> None:
    """``batch_warm_lead=None`` reads ``settings.QC_BATCH_WARM_LEAD``."""
    _minimums_at_the_floor(monkeypatch)
    doc = _titles("Doc gap", 4)
    for switch, streamed in ((True, [doc[0]]), (False, [])):
        monkeypatch.setattr(settings, "QC_BATCH_WARM_LEAD", switch)
        client = _LeadClient(_lineage_scripts(doc=_medium(doc)))
        store = _store()
        run_final_qc(
            store.doc,
            None,
            DEFAULT_MODULE,
            client,
            model=settings.QC_MODEL,
            max_tokens=4096,
            version_index=store.index,
            started_at="2026-09-23T10:00:00-07:00",
            finished_at="2026-09-23T10:01:00-07:00",
            run_id="qc-warm-lead-setting",
            batch_verification=True,
            warm_wait_seconds=_WARM,
        )
        assert client.streamed == streamed
