"""Final QC's phase-2 transport: the Message Batches API.

Phase 2 is ~90% of a run's calls and every verifier seat is independent, so
it is submitted as one batch at 50% of standard token prices. The claim this
file exists to defend is narrow and total: **batching changes transport and
nothing else.** Same model, same per-phase effort, same panel sizes, same
prompts, same grounding, same v4 adjudication, same audit records.

What legitimately differs is evidence *about the call*, not about the
review: a batched seat is not streamed, so it emits no live activity,
search, fetch or thinking frames. That contract is pinned from the other
side in ``tests/test_qc_live_events.py``, which runs the streaming path.
"""
from __future__ import annotations

import threading
import time as _real_time
from types import SimpleNamespace

import anthropic
import httpx
from anthropic import BadRequestError

from backend import settings
from backend.qc import engine
from backend.qc.engine import run_final_qc
from backend.qc.schema import QC_LENSES
from backend.spec_doc.model import DocumentStore
from backend.spec_modules import DEFAULT_MODULE
from tests.fakes import (
    SequencedFakeClient,
    pause_response,
    qc_findings_response,
    qc_verdict_response,
)

_LENS_KEYS = {lens.lens_id: f"[[QC-LENS:{lens.lens_id}]]" for lens in QC_LENSES}


class _SteppedClock:
    """A deterministic stand-in for the ``time`` module ``engine`` reads.

    ``monotonic()`` advances a fixed step per reading and ``sleep()``
    advances by what it was asked to sleep, so a test can assert that a
    budget DERIVED from the clock fell between two calls without depending
    on the host's clock resolution — or on real seconds passing.

    The host clock cannot carry that assertion. Python backs
    ``time.monotonic()`` with ``GetTickCount64`` on Windows, whose
    granularity is ~15.6 ms, so two readings microseconds apart return the
    IDENTICAL value; on Linux it is nanosecond-resolution. A strict ``<``
    between two budgets computed either side of one cheap fake call
    therefore passes on Linux and fails on Windows — which is exactly how it
    reached a release build: CI runs the backend suite on ubuntu-latest, and
    a tag build is the only place it ever runs on Windows.

    Everything but ``monotonic``/``sleep`` delegates to the real module, so
    a wall-clock timestamp stays a wall-clock timestamp. Locked because the
    lens fan-out reads the same module name from worker threads.
    """

    def __init__(self, *, step: float = 0.05) -> None:
        self._step = float(step)
        self._now = 0.0
        self._lock = threading.Lock()

    def monotonic(self) -> float:
        with self._lock:
            self._now += self._step
            return self._now

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self._now += max(0.0, float(seconds))

    def __getattr__(self, name: str):
        return getattr(_real_time, name)


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
        ]
    )
    store.commit_turn()
    return store


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


def _scripts(**per_lens: list[object]) -> dict[str, list[object]]:
    return {
        key: list(
            per_lens.get(lens_id, [qc_findings_response(lens_id, findings=[])])
        )
        for lens_id, key in _LENS_KEYS.items()
    }


def _run(client: object, *, batch: bool = True, should_stop=lambda: False,
         events: list[dict] | None = None):
    store = _store()
    return run_final_qc(
        store.doc,
        None,
        DEFAULT_MODULE,
        client,
        model=settings.QC_MODEL,
        max_tokens=4096,
        version_index=store.index,
        started_at="2026-08-19T10:00:00-07:00",
        finished_at="2026-08-19T10:01:00-07:00",
        run_id="qc-batch-test",
        batch_verification=batch,
        event_sink=(events.append if events is not None else (lambda e: None)),
        should_stop=should_stop,
    )


def _one_finding_scripts(title="Batched finding", *, verdicts=None):
    """A run with one medium finding, so a two-seat panel."""
    scripts = _scripts(
        code_compliance=[
            qc_findings_response("code_compliance", findings=[_finding(title)])
        ]
    )
    scripts[title] = list(
        verdicts
        if verdicts is not None
        else [qc_verdict_response(True), qc_verdict_response(True)]
    )
    return scripts


def _bad_request() -> BadRequestError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return BadRequestError(
        "Invalid verifier output schema.",
        response=httpx.Response(400, request=request),
        body=None,
    )


# ---------------------------------------------------------------------------
# The headline: same review, cheaper transport
# ---------------------------------------------------------------------------


def test_batched_and_streamed_verification_reach_the_same_verdicts():
    """The parity claim, asserted on the audit record rather than a summary.

    If this ever diverges, the 50% saving is not free any more and the whole
    justification for the transport goes with it.
    """
    def outcome(batch: bool):
        result = _run(SequencedFakeClient(_one_finding_scripts()), batch=batch)
        return {
            "execution_status": result.execution_status,
            "upheld": [f.title for f in result.findings],
            "refuted": [f.title for f in result.refuted],
            "disputed": [f.title for f in result.disputed],
            "inconclusive": [f.title for f in result.inconclusive],
            "outcomes": [f.verification_outcome for f in result.findings],
            "panel": [
                (v.reviewer_index, v.status, v.upholds)
                for f in result.findings
                for v in f.verdicts
            ],
            "seat_requests": [
                v.api_request_count for f in result.findings for v in f.verdicts
            ],
        }

    assert outcome(batch=True) == outcome(batch=False)


def test_a_batched_seat_sends_the_same_request_bytes_as_a_streamed_one():
    """One cache lineage, or the saving is quietly cancelled by extra writes.

    The cache is a strict prefix match over tools -> system -> messages, so
    two transports that built those blocks separately would drift apart the
    moment either was edited — and the drift presents as a doubled bill, not
    as a failure. Both go through ``_qc_request_kwargs``; this is the pin.
    """
    streamed = SequencedFakeClient(_one_finding_scripts())
    _run(streamed, batch=False)
    batched = SequencedFakeClient(_one_finding_scripts())
    _run(batched, batch=True)

    def verifier_requests(client):
        return [
            r
            for r in client.requests
            if "[[QC-VERIFY:" in str(r.get("messages"))
        ]

    stream_reqs = verifier_requests(streamed)
    batch_reqs = verifier_requests(batched)
    assert len(stream_reqs) == len(batch_reqs) == 2
    for sent_stream, sent_batch in zip(stream_reqs, batch_reqs):
        for key in ("model", "system", "tools", "thinking", "output_config"):
            assert sent_stream[key] == sent_batch[key], key
        assert sent_stream["messages"] == sent_batch["messages"]


def test_every_seat_rides_one_batch_under_its_own_custom_id():
    scripts = _scripts(
        code_compliance=[
            qc_findings_response(
                "code_compliance",
                findings=[
                    _finding("Alpha", severity="critical"),
                    _finding("Beta"),
                ],
            )
        ]
    )
    scripts["Alpha"] = [qc_verdict_response(True) for _ in range(3)]
    scripts["Beta"] = [qc_verdict_response(True) for _ in range(2)]
    client = SequencedFakeClient(scripts)
    result = _run(client)

    # critical -> 3 seats, medium -> 2 seats, all in a single round.
    assert len(client.batches.created) == 1
    submitted = client.batches.created[0]
    assert len(submitted) == 5
    ids = [request["custom_id"] for request in submitted]
    assert len(set(ids)) == 5
    assert result.execution_status == "complete"


def test_the_roster_event_names_the_transport():
    events: list[dict] = []
    _run(SequencedFakeClient(_one_finding_scripts()), events=events)
    started = [e for e in events if e["type"] == "verification_started"]
    assert started and started[0]["transport"] == "batch"

    events.clear()
    _run(SequencedFakeClient(_one_finding_scripts()), batch=False, events=events)
    started = [e for e in events if e["type"] == "verification_started"]
    assert started and started[0]["transport"] == "stream"


def test_batch_progress_is_reported_from_real_request_counts():
    """Progress the provider actually reported, never an animation."""
    events: list[dict] = []
    _run(SequencedFakeClient(_one_finding_scripts()), events=events)
    batch_events = [e for e in events if e["type"] == "verification_batch"]
    statuses = [e["status"] for e in batch_events]
    assert "submitted" in statuses
    assert statuses[-1] == "ended"
    submitted = next(e for e in batch_events if e["status"] == "submitted")
    assert submitted["submitted"] == 2
    assert submitted["total"] == 2
    polling = [e for e in batch_events if e["status"] == "polling"]
    assert polling and polling[-1]["succeeded"] == 2
    assert polling[-1]["errored"] == 0
    # Every seat still announces itself, so the board can show the panel
    # before any result lands.
    assert len([e for e in events if e["type"] == "verifier_started"]) == 2


def test_a_batched_seat_emits_no_live_activity_frames():
    """Not a gap — the honest consequence of not streaming.

    Pinned so nobody later "restores" seat activity in batch mode by
    synthesizing frames the provider never sent.
    """
    events: list[dict] = []
    _run(SequencedFakeClient(_one_finding_scripts()), events=events)
    live = [
        e
        for e in events
        if e["type"].startswith("verifier_")
        and e["type"] not in {"verifier_started", "verifier_complete"}
    ]
    assert live == []
    # Phase 1 still streams, so lens activity is unaffected.
    assert [e for e in events if e["type"] == "lens_started"]


# ---------------------------------------------------------------------------
# The loop: continuations, retries, failures
# ---------------------------------------------------------------------------


def test_a_paused_seat_continues_in_a_second_round():
    scripts = _one_finding_scripts(
        verdicts=[
            pause_response(searched_urls=["https://example.org/a"]),
            qc_verdict_response(True),
            qc_verdict_response(True),
        ]
    )
    client = SequencedFakeClient(scripts)
    result = _run(client)

    assert len(client.batches.created) == 2
    # Round 2 carries only the seat that paused.
    assert len(client.batches.created[1]) == 1
    resumed = client.batches.created[1][0]["params"]["messages"]
    assert resumed[-1]["role"] == "assistant"
    assert result.execution_status == "complete"
    seat = result.findings[0].verdicts[0]
    # Two client requests for one seat: the pause and its continuation.
    assert seat.api_request_count == 2
    assert seat.status == "completed"


def test_a_retryable_seat_failure_restarts_on_a_fresh_conversation():
    """A retry abandons its attempt's conversation, exactly as streaming does."""
    scripts = _one_finding_scripts(
        verdicts=[
            ConnectionError("peer closed connection"),
            qc_verdict_response(True),
            qc_verdict_response(True),
        ]
    )
    client = SequencedFakeClient(scripts)
    events: list[dict] = []
    result = _run(client, events=events)

    assert len(client.batches.created) == 2
    retried = client.batches.created[1][0]["params"]["messages"]
    assert len(retried) == 1 and retried[0]["role"] == "user"
    retries = [e for e in events if e["type"] == "verifier_retry"]
    assert retries and retries[0]["attempt"] == 1
    assert retries[0]["candidate_id"] == "candidate-1"
    assert result.execution_status == "complete"


def test_a_nonretryable_seat_failure_leaves_the_candidate_inconclusive():
    scripts = _one_finding_scripts(
        verdicts=[_bad_request(), qc_verdict_response(True)]
    )
    result = _run(SequencedFakeClient(scripts))

    assert result.findings == []
    assert len(result.inconclusive) == 1
    statuses = sorted(v.status for v in result.inconclusive[0].verdicts)
    assert statuses == ["completed", "failed"]
    assert result.execution_status == "partial"


def test_a_shared_invalid_request_still_accounts_for_every_seat():
    """The batch cannot decline to START a queued seat — all are submitted.

    So the streamed circuit breaker's call cap does not apply here. What
    must still hold is the safety property it protects: every expected seat
    is present as a failed record, nothing is promoted, and the run is
    partial (which blocks readiness).
    """
    findings = [_finding(f"Circuit {index}") for index in range(4)]
    scripts = _scripts(
        code_compliance=[
            qc_findings_response("code_compliance", findings=findings)
        ]
    )
    for index in range(4):
        scripts[f"Circuit {index}"] = [_bad_request(), _bad_request()]
    result = _run(SequencedFakeClient(scripts))

    assert result.findings == []
    assert result.refuted == []
    assert len(result.inconclusive) == 4
    seats = [v for f in result.inconclusive for v in f.verdicts]
    assert len(seats) == 8
    assert all(v.status == "failed" for v in seats)
    assert result.execution_status == "partial"


def test_a_seat_with_no_result_line_is_recorded_failed_not_dropped():
    """A hole in the batch is not a verdict.

    Dropping it would shrink a panel silently, and a shrunken panel can
    reach `upheld` on fewer seats than the severity demanded.
    """
    client = SequencedFakeClient(_one_finding_scripts())
    real_results = client.batches.results

    def drop_one(batch_id):
        items = real_results(batch_id)
        return items[:-1]

    client.batches.results = drop_one
    result = _run(client)

    assert result.findings == []
    assert len(result.inconclusive) == 1
    missing = [
        v for v in result.inconclusive[0].verdicts if v.status == "failed"
    ]
    assert len(missing) == 1
    assert "no result" in missing[0].error.lower()
    assert result.execution_status == "partial"


def test_stopping_cancels_the_batch_and_collects_what_it_already_produced():
    """Stop lands while the batch is in flight, after phase 1 has finished.

    Setting the flag before the run would kill the lenses instead, which is
    a different (already covered) path — the point here is a batch already
    submitted.

    It is cancelled, and then SETTLED: the provider bills every seat it has
    already run whether or not the app reads the result, so the window
    collects them. This fake resolves a batch immediately, which models the
    case the recovery exists for — the work was done before the Stop
    landed. The seats therefore keep their verdicts and their usage, and no
    charge is disclosed as uncollected because none was.

    What the recovery must NOT do is launder a stopped review into a
    complete one: the run reads partial however many seats came back.
    """
    stop = threading.Event()
    client = SequencedFakeClient(_one_finding_scripts())
    real_create = client.batches.create

    def create_then_stop(*, requests):
        batch = real_create(requests=requests)
        stop.set()
        return batch

    client.batches.create = create_then_stop
    result = _run(client, should_stop=stop.is_set)

    assert client.batches.cancelled
    # Recovered, not written off: one adjudicated candidate with real seats.
    candidates = [
        *result.findings,
        *result.refuted,
        *result.disputed,
        *result.inconclusive,
    ]
    assert len(candidates) == 1
    verdicts = candidates[0].verdicts
    assert verdicts and all(v.status == "completed" for v in verdicts)
    # Each seat's response was actually folded, not synthesized as a failure.
    assert all(v.api_request_count > 0 for v in verdicts)
    # Every submitted row was read, so there is nothing to disclose.
    assert result.uncollected_batch_requests() == 0
    assert result.unassigned_batch_results == 0
    assert result.batch_usage_capture == "complete"
    # And a stopped review is never a complete one.
    assert result.execution_status == "partial"
    # The bound is applied to the calls inside the window, cancel included.
    assert client.request_options, "the settlement used the bounded client"
    assert all(
        options["max_retries"] == 0 for options in client.request_options
    )


def test_a_stop_before_submission_spends_nothing_on_phase_two():
    """A seat whose batch was never submitted costs nothing, and says so."""
    stop = threading.Event()
    client = SequencedFakeClient(_one_finding_scripts())
    lens_calls = {"n": 0}
    real_stream = client.stream

    def stream_then_stop(**request):
        # Phase 1 streams; only phase 2 goes through `batches`. Counting
        # streamed calls therefore lands the stop exactly on the boundary
        # between the last lens and the first batch submission.
        ctx = real_stream(**request)
        lens_calls["n"] += 1
        if lens_calls["n"] >= len(QC_LENSES):
            stop.set()
        return ctx

    client.stream = stream_then_stop
    result = _run(client, should_stop=stop.is_set)

    assert client.batches.created == []
    assert len(result.inconclusive) == 1
    assert all(
        v.api_request_count == 0 for v in result.inconclusive[0].verdicts
    )


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------


def test_the_transport_is_recorded_in_the_hashed_input_manifest():
    batched = _run(SequencedFakeClient(_one_finding_scripts()), batch=True)
    streamed = _run(SequencedFakeClient(_one_finding_scripts()), batch=False)

    assert batched.input_manifest["configuration"]["batch_verification"] is True
    assert streamed.input_manifest["configuration"]["batch_verification"] is False
    # A review executed on the other transport carries different call
    # evidence, so it must not read as like-for-like.
    assert batched.input_fingerprint != streamed.input_fingerprint


def test_a_retained_batched_result_reads_stale_once_the_transport_flips(
    monkeypatch,
):
    store = _store()
    result = run_final_qc(
        store.doc,
        None,
        DEFAULT_MODULE,
        SequencedFakeClient(_one_finding_scripts()),
        model=settings.QC_MODEL,
        max_tokens=4096,
        version_index=store.index,
        started_at="2026-08-19T10:00:00-07:00",
        finished_at="2026-08-19T10:01:00-07:00",
        batch_verification=True,
    )
    assert result.matches_inputs(store.index, store.doc, None, DEFAULT_MODULE)

    monkeypatch.setattr(settings, "QC_BATCH_VERIFICATION", False)
    assert not result.matches_inputs(
        store.index, store.doc, None, DEFAULT_MODULE
    )


# ---------------------------------------------------------------------------
# The bill
# ---------------------------------------------------------------------------
#
# The point of batching is that those tokens cost half. A meter that still
# quotes list price defeats the change and, worse, overstates what the user
# actually spent — in a report that presents itself as an audit record.


def test_a_batched_seat_is_priced_at_the_batch_rate():
    from backend.usage_ledger import estimate_usage_cost

    batched = _run(SequencedFakeClient(_one_finding_scripts()), batch=True)
    streamed = _run(SequencedFakeClient(_one_finding_scripts()), batch=False)

    batched_seats = [v for f in batched.findings for v in f.verdicts]
    streamed_seats = [v for f in streamed.findings for v in f.verdicts]
    assert batched_seats and streamed_seats

    for seat in batched_seats:
        assert seat.cost_multiplier == settings.BATCH_COST_MULTIPLIER
        assert seat.estimated_cost_usd == estimate_usage_cost(
            settings.QC_MODEL,
            seat.usage_totals,
            multiplier=settings.BATCH_COST_MULTIPLIER,
        )
    for seat in streamed_seats:
        assert seat.cost_multiplier == 1.0
        assert seat.estimated_cost_usd == estimate_usage_cost(
            settings.QC_MODEL, seat.usage_totals
        )


def test_lens_records_are_never_discounted():
    """Phase 1 still streams, so it is still billed at list price.

    Discounting the whole run because the run was 'batched' would understate
    the bill by as much as the old code overstated it.
    """
    from backend.usage_ledger import estimate_usage_cost

    result = _run(SequencedFakeClient(_one_finding_scripts()), batch=True)
    for lens in result.lens_statuses:
        assert lens.estimated_cost_usd == estimate_usage_cost(
            settings.QC_MODEL, lens.usage_totals
        )


def test_the_run_total_is_the_sum_of_its_records_when_any_was_discounted():
    """A mixed-rate run cannot be priced from merged usage, so it is summed.

    And the audit record has to agree with itself: `_audit_accounting_
    consistent` runs on load, so a total that did not reconcile would make
    `from_dict` discard the whole paid report.
    """
    result = _run(SequencedFakeClient(_one_finding_scripts()), batch=True)
    records = [
        *result.lens_statuses,
        *([result.consolidation] if result.consolidation is not None else []),
        *(v for f in result.findings for v in f.verdicts),
    ]
    assert result.estimated_cost_usd == round(
        sum(record.estimated_cost_usd for record in records), 6
    )
    from backend.qc.engine import QCResult

    assert QCResult.from_dict(result.to_dict()) is not None


def test_batching_actually_lowers_the_reported_run_cost():
    """The headline claim, asserted as a number rather than a comment."""

    def priced_scripts():
        # The default fixtures bill nothing, so a cost comparison over them
        # is vacuously equal. These seats spend.
        return _one_finding_scripts(
            verdicts=[
                qc_verdict_response(
                    True, tokens={"input": 20_000, "output": 4_000}
                ),
                qc_verdict_response(
                    True, tokens={"input": 20_000, "output": 4_000}
                ),
            ]
        )

    batched = _run(SequencedFakeClient(priced_scripts()), batch=True)
    streamed = _run(SequencedFakeClient(priced_scripts()), batch=False)
    # Identical tokens, identical verdicts — only the rate differs.
    assert batched.usage_totals == streamed.usage_totals
    assert batched.estimated_cost_usd > 0
    assert batched.estimated_cost_usd < streamed.estimated_cost_usd


def test_the_multiplier_survives_a_round_trip_and_bounds_are_enforced():
    from backend.qc.engine import QCResult

    result = _run(SequencedFakeClient(_one_finding_scripts()), batch=True)
    restored = QCResult.from_dict(result.to_dict())
    assert restored is not None
    assert all(
        v.cost_multiplier == settings.BATCH_COST_MULTIPLIER
        for f in restored.findings
        for v in f.verdicts
    )

    # A record claiming a multiplier outside (0, 1] is not a discount; it is
    # a way to understate real spend, so it is refused rather than clamped.
    for bad in (0.0, -0.5, 1.5, "half", True):
        payload = result.to_dict()
        payload["findings"][0]["verdicts"][0]["cost_multiplier"] = bad
        assert QCResult.from_dict(payload) is None, bad


def test_a_record_written_before_the_discount_prices_at_list():
    """Absent means 1.0, and the saved total must still reconcile."""
    from backend.qc.engine import QCResult

    payload = _run(
        SequencedFakeClient(_one_finding_scripts()), batch=False
    ).to_dict()
    for finding in payload["findings"]:
        for verdict in finding["verdicts"]:
            verdict.pop("cost_multiplier")

    restored = QCResult.from_dict(payload)
    assert restored is not None
    assert all(
        v.cost_multiplier == 1.0
        for f in restored.findings
        for v in f.verdicts
    )


def test_the_session_meter_prices_the_batched_phase_separately():
    """The ledger buckets discounted tokens apart, or it cannot price them."""
    from backend.usage_ledger import UsageLedger, estimate_usage_cost

    ledger = UsageLedger()
    tokens = {"input_tokens": 1_000_000, "output_tokens": 100_000}
    ledger.add("qc", dict(tokens))
    ledger.add("qc_batched", dict(tokens))
    snapshot = ledger.snapshot()

    by_category = snapshot["estimated_cost_usd"]["by_category"]
    assert by_category["qc"] == estimate_usage_cost(settings.QC_MODEL, tokens)
    assert by_category["qc_batched"] == estimate_usage_cost(
        settings.QC_MODEL, tokens, multiplier=settings.BATCH_COST_MULTIPLIER
    )
    assert by_category["qc_batched"] < by_category["qc"]


# ---------------------------------------------------------------------------
# The phase cannot hang: a refused submission, a Stop mid-wait, a batch
# with no id, and the wall-clock ceiling between rounds
# ---------------------------------------------------------------------------


def _rate_limited() -> anthropic.RateLimitError:
    request = httpx.Request(
        "POST", "https://api.anthropic.com/v1/messages/batches"
    )
    return anthropic.RateLimitError(
        "Rate limited.", response=httpx.Response(429, request=request), body=None
    )


def _refuse_submissions(client, *, on_calls: set[int]) -> dict:
    """Make ``batches.create`` raise a 429 on the given 1-based call numbers.

    Every other call goes through to the fake, so the phase still finishes
    once the refusal has been retried.
    """
    real_create = client.batches.create
    calls = {"n": 0}

    def create(*, requests):
        calls["n"] += 1
        if calls["n"] in on_calls:
            raise _rate_limited()
        return real_create(requests=requests)

    client.batches.create = create
    return calls


def _never_sleep(_seconds):
    raise AssertionError("the batch loop must not sleep on this path")


def _pauses(count: int) -> list:
    return [
        pause_response(searched_urls=["https://example.org/a"])
        for _ in range(count)
    ]


def test_a_refused_submission_backs_off_on_the_seat_attempt_not_the_round(
    monkeypatch,
):
    """Rounds climb on a healthy phase; the wait must not climb with them.

    Three pause_turn rounds put the loop at round index 3 before the first
    429. Keyed on the round, the old code slept 5 * 2**3 = 40s here — and
    85 minutes by round 10. Keyed on the seat attempt it is the first
    retry's 5s: exactly what the streaming path and the per-result retry
    already charge for the same failure.
    """
    waits: list[float] = []
    monkeypatch.setattr(
        engine,
        "_sleep_interruptibly",
        lambda seconds, **_kw: waits.append(seconds) or False,
    )
    scripts = _one_finding_scripts(
        verdicts=[*_pauses(6), qc_verdict_response(True), qc_verdict_response(True)]
    )
    client = SequencedFakeClient(scripts)
    _refuse_submissions(client, on_calls={4})
    events: list[dict] = []
    result = _run(client, events=events)

    assert waits == [5.0]
    retries = [e for e in events if e["type"] == "verifier_retry"]
    assert len(retries) == 2
    assert all(e["backoff_s"] == 5.0 and e["attempt"] == 1 for e in retries)
    # Four batches in the fake's ledger: three pause rounds and the retry;
    # the refused call never reached it.
    assert len(client.batches.created) == 4
    assert result.execution_status == "complete"


def test_the_submission_backoff_is_capped(monkeypatch):
    """The cap is local to the batch loop, and it binds.

    ``compute_backoff_seconds`` stays uncapped for the streaming callers,
    whose attempt ceiling already bounds it; this loop caps because a wait
    here is one the whole phase — and the user — sits through.
    """
    waits: list[float] = []
    monkeypatch.setattr(
        engine,
        "_sleep_interruptibly",
        lambda seconds, **_kw: waits.append(seconds) or False,
    )
    monkeypatch.setattr(engine, "compute_backoff_seconds", lambda *_a, **_kw: 1e4)
    client = SequencedFakeClient(_one_finding_scripts())
    _refuse_submissions(client, on_calls={1})
    result = _run(client)

    assert waits == [engine._BATCH_SUBMISSION_BACKOFF_CAP_SECONDS]
    assert result.execution_status == "complete"


def test_a_stop_during_the_submission_backoff_returns_promptly(monkeypatch):
    """Stop lands while the loop is waiting to resubmit.

    The old ``time.sleep(backoff)`` slept the whole wait with the phase
    locked behind it; the slice-and-check sleep notices the Stop on its
    next slice, and the top of the loop settles the seats as cancelled.
    """
    stop = threading.Event()
    slept: list[float] = []

    def sleep_then_stop(seconds):
        slept.append(seconds)
        stop.set()

    monkeypatch.setattr(engine.time, "sleep", sleep_then_stop)
    client = SequencedFakeClient(_one_finding_scripts())
    _refuse_submissions(client, on_calls={1})
    result = _run(client, should_stop=stop.is_set)

    # One slice, never the whole 5s wait — and no second submission.
    assert slept == [engine._BATCH_SLEEP_SLICE_SECONDS]
    assert client.batches.created == []
    assert len(result.inconclusive) == 1
    assert all(
        v.status == "cancelled" for v in result.inconclusive[0].verdicts
    )
    assert result.execution_status in {"partial", "cancelled"}


def test_a_submission_without_a_batch_id_fails_the_round_immediately(
    monkeypatch,
):
    """No id means nothing to poll, and the loop must not pretend otherwise.

    The old loop polled anyway: every ``retrieve("")`` raised, was swallowed
    as a dropped poll, and the phase sat on the two-hour ceiling before
    reporting a timeout that named the wrong cause.
    """
    monkeypatch.setattr(engine.time, "sleep", _never_sleep)
    client = SequencedFakeClient(_one_finding_scripts())
    real_create = client.batches.create
    real_retrieve = client.batches.retrieve
    retrieves: list[str] = []

    def create_without_id(*, requests):
        batch = real_create(requests=requests)
        return SimpleNamespace(
            id="",
            processing_status=batch.processing_status,
            request_counts=batch.request_counts,
        )

    def retrieve(batch_id):
        retrieves.append(batch_id)
        return real_retrieve(batch_id)

    client.batches.create = create_without_id
    client.batches.retrieve = retrieve
    events: list[dict] = []
    result = _run(client, events=events)

    assert retrieves == []
    assert result.execution_status == "partial"
    seats = result.inconclusive[0].verdicts
    assert len(seats) == 2
    assert all(
        v.status == "failed" and "no batch id" in v.error.lower() for v in seats
    )
    failed = [
        e
        for e in events
        if e["type"] == "verification_batch" and e["status"] == "failed"
    ]
    assert len(failed) == 1 and "no batch id" in failed[0]["error"].lower()


def test_the_round_loop_honours_the_wall_clock_ceiling_between_rounds(
    monkeypatch,
):
    """The ceiling is a phase-wide promise, not a per-poll one.

    It used to be checked only inside the poll loop, so a round that ended
    before the ceiling and then needed another (a continuation here) kept
    submitting past it. The clock jumps 100,000s after the first
    submission: round 1 still folds its results, round 2 must not start.
    """
    clock = {"now": 0.0}
    monkeypatch.setattr(engine.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(engine.time, "sleep", _never_sleep)
    scripts = _one_finding_scripts(
        verdicts=[*_pauses(1), qc_verdict_response(True), qc_verdict_response(True)]
    )
    client = SequencedFakeClient(scripts)
    real_create = client.batches.create

    def create_then_age(*, requests):
        batch = real_create(requests=requests)
        clock["now"] += 100_000.0
        return batch

    client.batches.create = create_then_age
    events: list[dict] = []
    result = _run(client, events=events)

    assert len(client.batches.created) == 1
    timeouts = [
        e
        for e in events
        if e["type"] == "verification_batch" and e["status"] == "timeout"
    ]
    assert len(timeouts) == 1 and timeouts[0]["round"] == 2
    assert result.execution_status == "partial"
    seats = result.inconclusive[0].verdicts
    cut_off = [v for v in seats if v.status == "failed"]
    assert len(cut_off) == 1 and "wall-clock ceiling" in cut_off[0].error
    assert [v.status for v in seats if v is not cut_off[0]] == ["completed"]



def test_a_refusal_with_no_round_left_fails_with_the_refusal_not_a_sleep(
    monkeypatch,
):
    """A retry needs a round to run in (caught in review on PR #151, Codex).

    Refused on the last allowed round, the old branch queued a retry, slept
    the backoff for nothing, and let the loop's tail blame the round
    ceiling — and a Stop landing in that sleep read as a ceiling breach
    rather than a cancellation. The seats now fail with the refusal itself,
    at once.
    """
    monkeypatch.setattr(settings, "QC_BATCH_MAX_ROUNDS", 1)
    monkeypatch.setattr(engine.time, "sleep", _never_sleep)
    client = SequencedFakeClient(_one_finding_scripts())
    _refuse_submissions(client, on_calls={1})
    events: list[dict] = []
    result = _run(client, events=events)

    assert client.batches.created == []
    assert [e for e in events if e["type"] == "verifier_retry"] == []
    failed = [
        e
        for e in events
        if e["type"] == "verification_batch" and e["status"] == "failed"
    ]
    assert len(failed) == 1 and "RateLimitError" in failed[0]["error"]
    seats = result.inconclusive[0].verdicts
    assert all(
        v.status == "failed" and "RateLimitError" in v.error for v in seats
    )
    assert not any("round ceiling" in v.error for v in seats)
    assert result.execution_status == "partial"


# ---------------------------------------------------------------------------
# Trustworthy accounting: what the phase read, and what it could not
# ---------------------------------------------------------------------------
#
# The provider bills a batch request whether or not the app ever reads its
# result. Two ways that used to go wrong. The whole results iterator was
# materialized before any row was folded, so one failure part way through
# discarded every row already yielded — the verdicts as much as the usage.
# And a Stop cancelled the batch and returned, leaving seats the provider
# had already finished unread and unbilled in the record.
#
# Now the read is row-at-a-time, a Stop or the ceiling opens a bounded
# settlement window, and whatever the window still cannot collect is
# disclosed rather than written off.


def _uncollected(result) -> int:
    return result.uncollected_batch_requests()


class _FailingResults:
    """A results stream that yields ``rows`` and then raises."""

    def __init__(self, batches, rows_before_failure: int):
        self._batches = batches
        self._rows = rows_before_failure

    def __call__(self, batch_id):
        for index, item in enumerate(self._batches.real_results(batch_id)):
            if index >= self._rows:
                raise anthropic.APIConnectionError(
                    request=httpx.Request("GET", "https://api.anthropic.com/x")
                )
            yield item


def _with_failing_results(client, rows_before_failure: int):
    batches = client.batches
    batches.real_results = batches.results
    batches.results = _FailingResults(batches, rows_before_failure)
    return client


def test_a_results_stream_that_fails_part_way_keeps_what_it_already_read():
    """The headline: one failure no longer discards the rows before it.

    Two seats, the first row folded and the second never delivered. The
    folded seat keeps its verdict; only the unread one is a gap.
    """
    client = _with_failing_results(SequencedFakeClient(_one_finding_scripts()), 1)
    result = _run(client)

    seats = [
        verdict
        for finding in [
            *result.findings,
            *result.refuted,
            *result.disputed,
            *result.inconclusive,
        ]
        for verdict in finding.verdicts
    ]
    assert len(seats) == 2
    completed = [seat for seat in seats if seat.status == "completed"]
    assert len(completed) == 1, "the row that arrived keeps its verdict"
    assert completed[0].api_request_count > 0
    # Exactly one request's result was never read.
    assert _uncollected(result) == 1
    assert result.batch_usage_capture == "incomplete"
    assert result.execution_status == "partial"


def test_a_failure_after_every_expected_row_invents_no_gap():
    """The read failed, but it had already read everything.

    A transport diagnostic is not evidence of a missing charge, and
    manufacturing one would make the disclosure noise.
    """
    client = _with_failing_results(SequencedFakeClient(_one_finding_scripts()), 2)
    result = _run(client)

    assert _uncollected(result) == 0
    assert result.unassigned_batch_results == 0
    assert result.batch_usage_capture == "complete"


def test_a_stop_whose_batch_never_ends_discloses_the_uncollected_requests(
    monkeypatch,
):
    """The window opens, the batch stays in flight, the bound expires.

    Nothing is recovered — and nothing is invented either. Both seats are
    cancelled and both are counted, because their billing outcome is
    genuinely unknown.

    The window is shortened to a second: this test is about what the bound
    DOES when it expires, not about how long the shipped default is.
    """
    monkeypatch.setattr(settings, "QC_BATCH_SETTLE_SECONDS", 1)
    stop = threading.Event()
    client = SequencedFakeClient(_one_finding_scripts())
    real_create = client.batches.create

    def create_then_stop(*, requests):
        batch = real_create(requests=requests)
        stop.set()
        return batch

    client.batches.create = create_then_stop
    # The batch never reports `ended`, so the window can only wait it out.
    client.batches.retrieve = lambda batch_id: SimpleNamespace(
        id=batch_id,
        processing_status="in_progress",
        request_counts=SimpleNamespace(
            processing=2, succeeded=0, errored=0, canceled=0, expired=0
        ),
    )
    result = _run(client, should_stop=stop.is_set)

    assert client.batches.cancelled
    assert _uncollected(result) == 2
    assert result.batch_usage_capture == "incomplete"
    assert result.execution_status == "partial"


def test_the_settlement_window_bounds_every_call_it_makes():
    """Including the cancel.

    The app client runs the SDK's own retries at a ten-minute read timeout,
    so one stalled call would hold the settling state — and the locked QC
    controls with it — for far longer than the window advertises.
    """
    stop = threading.Event()
    client = SequencedFakeClient(_one_finding_scripts())
    real_create = client.batches.create

    def create_then_stop(*, requests):
        batch = real_create(requests=requests)
        stop.set()
        return batch

    client.batches.create = create_then_stop
    _run(client, should_stop=stop.is_set)

    assert client.request_options, "the window re-optioned the client"
    for options in client.request_options:
        assert options["max_retries"] == 0
        # A short read timeout, well under the module default. On the shipped
        # window (two minutes) the per-call ceiling is what binds, and the
        # SDK's own connect timeout is left intact — folding connect into a
        # LONG read budget would make a black-holed connect the longest call
        # in the window. It is only ever bounded downward; see the next test.
        assert options["timeout"].read <= 60.0
        assert options["timeout"].connect == 5.0


def test_each_settlement_call_is_bounded_by_the_time_the_window_has_left(
    monkeypatch,
):
    """The budget is the remaining window, not a fixed per-call ceiling.

    The deadline is checked only BETWEEN operations, so a call granted more
    time than the window has left simply outlives it — and the settling
    state holds Final QC's start, apply, dismiss and export locked while it
    does. A window configured shorter than the ceiling was the case that
    made this visible: every call still got the full ceiling, plus a connect
    timeout several times the whole advertised window.

    The clock is a deterministic stand-in, and it has to be: the budget is
    the remaining window, so proving it FALLS means comparing two readings
    taken either side of one cheap fake call — microseconds apart, which a
    ~15.6 ms Windows tick cannot distinguish (see ``_SteppedClock``). It
    also removes the several real seconds the poll loop used to sleep.
    """
    monkeypatch.setattr(settings, "QC_BATCH_SETTLE_SECONDS", 2)
    monkeypatch.setattr(settings, "QC_BATCH_POLL_SECONDS", 1)
    monkeypatch.setattr(engine, "time", _SteppedClock())
    stop = threading.Event()
    client = SequencedFakeClient(_one_finding_scripts())
    real_create = client.batches.create

    def create_then_stop(*, requests):
        batch = real_create(requests=requests)
        stop.set()
        return batch

    client.batches.create = create_then_stop
    # Never ends, so the window polls until the budget is spent — which is
    # what gives us a call issued near the deadline to inspect.
    client.batches.retrieve = lambda batch_id: SimpleNamespace(
        id=batch_id,
        processing_status="in_progress",
        request_counts=SimpleNamespace(
            processing=2, succeeded=0, errored=0, canceled=0, expired=0
        ),
    )
    _run(client, should_stop=stop.is_set)

    assert client.request_options, "the window re-optioned the client"
    for options in client.request_options:
        assert options["max_retries"] == 0
        assert options["timeout"].read <= 2.0, "a call outlives its own window"
        assert options["timeout"].connect <= 2.0, "connect outlives the window"
    # And the budget really is falling, not one short value reused: the last
    # call has strictly less to spend than the first.
    reads = [options["timeout"].read for options in client.request_options]
    assert len(reads) > 1
    assert reads[-1] < reads[0]


def test_a_cancel_that_raises_still_opens_the_window():
    """Cancellation is advisory; collection is the valuable half.

    A provider that refuses the cancel has not stopped billing, so giving up
    on the read would forfeit exactly the charges worth recovering.
    """
    stop = threading.Event()
    client = SequencedFakeClient(_one_finding_scripts())
    real_create = client.batches.create

    def create_then_stop(*, requests):
        batch = real_create(requests=requests)
        stop.set()
        return batch

    def refuse_cancel(batch_id):
        raise anthropic.APIConnectionError(
            request=httpx.Request("POST", "https://api.anthropic.com/x")
        )

    client.batches.create = create_then_stop
    client.batches.cancel = refuse_cancel
    result = _run(client, should_stop=stop.is_set)

    # The results were still collected despite the failed cancellation.
    assert _uncollected(result) == 0
    assert result.batch_usage_capture == "complete"
    assert result.execution_status == "partial"


def test_a_duplicate_row_is_folded_once_and_counted_as_unattributable():
    """Identity is (round, custom_id), checked before every fold.

    `_apply_batch_item` deliberately leaves a seat unsettled after a
    pause_turn or a retryable error, so a repeated row for one of those
    would queue a second continuation or restart the attempt again — billing
    the same work twice.
    """
    client = SequencedFakeClient(_one_finding_scripts())
    batches = client.batches
    real_results = batches.results
    batches.results = lambda batch_id: [
        *(rows := list(real_results(batch_id))),
        rows[0],
    ]
    result = _run(client)

    seats = [
        verdict
        for finding in [
            *result.findings,
            *result.refuted,
            *result.disputed,
            *result.inconclusive,
        ]
        for verdict in finding.verdicts
    ]
    assert len(seats) == 2, "the duplicate did not mint a third seat"
    assert all(seat.api_request_count == 1 for seat in seats)
    # Read but unattributable: it belongs to no seat, so the run carries it.
    assert result.unassigned_batch_results == 1
    assert _uncollected(result) == 0
    assert result.batch_usage_capture == "incomplete"


def test_an_unknown_custom_id_prevents_a_false_complete_capture():
    """Every expected row arrived — plus one the run could not place.

    A per-seat counter cannot carry it, which is why the run keeps its own.
    Without that the report would read as having accounted for its charges
    while having dropped a provider result on the floor.
    """
    client = SequencedFakeClient(_one_finding_scripts())
    batches = client.batches
    real_results = batches.results
    batches.results = lambda batch_id: [
        *real_results(batch_id),
        SimpleNamespace(
            custom_id="seat-that-was-never-submitted",
            result=SimpleNamespace(type="succeeded", message=None),
        ),
    ]
    result = _run(client)

    assert result.unassigned_batch_results == 1
    assert _uncollected(result) == 0
    assert result.batch_usage_capture == "incomplete"


def test_a_clean_batched_run_records_complete_capture():
    """The control: nothing missing, nothing unattributable, nothing to say."""
    result = _run(SequencedFakeClient(_one_finding_scripts()))
    assert result.batch_usage_capture == "complete"
    assert _uncollected(result) == 0
    assert result.unassigned_batch_results == 0
    assert result.execution_status == "complete"


def test_a_streamed_run_says_not_applicable_not_predates_the_recording():
    """A streamed run submitted no batch request, and says so in its own state.

    Leaving the field empty conflated it with a report written before any of
    this existed — and both renderers turn that empty state into a
    limitation claiming the report predates cost-capture recording, which is
    false of a run this build just produced. The two must stay distinguishable
    in the record, and neither is ever promoted to complete.
    """
    from backend.qc.engine import (
        BATCH_CAPTURE_NOT_APPLICABLE,
        BATCH_CAPTURE_UNRECORDED,
    )

    result = _run(SequencedFakeClient(_one_finding_scripts()), batch=False)
    assert result.batch_usage_capture == BATCH_CAPTURE_NOT_APPLICABLE
    assert result.batch_usage_capture != BATCH_CAPTURE_UNRECORDED
    assert _uncollected(result) == 0

    # And it survives a round trip, so a saved project reads the same way.
    from backend.qc.engine import QCResult

    reloaded = QCResult.from_dict(result.to_dict())
    assert reloaded is not None
    assert reloaded.batch_usage_capture == BATCH_CAPTURE_NOT_APPLICABLE


def test_the_gap_reaches_the_session_meter_and_derives_its_own_flag():
    """The disclosure rides the batched bucket, not a second channel.

    That is what makes it inherit the sink, the session-generation guard,
    reset, project load and the detached-workspace merge for free — and what
    makes it impossible for a snapshot to show a run's new spend without the
    warning belonging to it.
    """
    from backend.usage_ledger import UNCOLLECTED_BATCH_REQUESTS_KEY, UsageLedger

    client = _with_failing_results(SequencedFakeClient(_one_finding_scripts()), 1)
    result = _run(client)
    buckets = result.usage_by_meter_category()
    assert buckets["qc_batched"][UNCOLLECTED_BATCH_REQUESTS_KEY] == 1

    ledger = UsageLedger()
    for category, bucket in buckets.items():
        ledger.add(category, bucket)
    snapshot = ledger.snapshot()
    assert snapshot["includes_uncollected_charges"] is True
    # A count of requests, never a token: no pricing helper reads this key,
    # so it cannot become a charge.
    priced = snapshot["estimated_cost_usd"]["by_category"]["qc_batched"]
    ledger_without = UsageLedger()
    ledger_without.add(
        "qc_batched",
        {
            k: v
            for k, v in buckets["qc_batched"].items()
            if k != UNCOLLECTED_BATCH_REQUESTS_KEY
        },
    )
    assert priced == (
        ledger_without.snapshot()["estimated_cost_usd"]["by_category"].get(
            "qc_batched", 0.0
        )
    )


def test_an_empty_subtotal_still_discloses_its_missing_charges():
    """No tokens read is not the same as no money spent.

    A run that read nothing back has the LEAST evidence about what it was
    billed, so suppressing the warning for want of a token count would drop
    it in exactly the case that needs it most.
    """
    from backend.usage_ledger import UNCOLLECTED_BATCH_REQUESTS_KEY, UsageLedger

    ledger = UsageLedger()
    ledger.add("qc_batched", {UNCOLLECTED_BATCH_REQUESTS_KEY: 2})
    snapshot = ledger.snapshot()

    assert snapshot["includes_uncollected_charges"] is True
    assert snapshot["estimated_cost_usd"]["by_category"]["qc_batched"] == 0.0
    assert snapshot["includes_estimated_output"] is False


def test_a_clean_run_sets_no_warning_anywhere():
    """The control: the flag is derived, so silence means silence."""
    from backend.usage_ledger import UsageLedger

    result = _run(SequencedFakeClient(_one_finding_scripts()))
    ledger = UsageLedger()
    for category, bucket in result.usage_by_meter_category().items():
        ledger.add(category, bucket)
    assert ledger.snapshot()["includes_uncollected_charges"] is False


def test_the_capture_record_survives_a_round_trip_and_polices_itself():
    """Serialized, reloaded — and refused when it contradicts its evidence."""
    from backend.qc.engine import QCResult

    client = _with_failing_results(SequencedFakeClient(_one_finding_scripts()), 1)
    result = _run(client)
    payload = result.to_dict()
    assert payload["batch_usage_capture"] == "incomplete"
    assert payload["uncollected_batch_requests"] == 1

    reloaded = QCResult.from_dict(payload)
    assert reloaded is not None
    assert reloaded.batch_usage_capture == "incomplete"
    assert reloaded.uncollected_batch_requests() == 1

    # A record claiming it accounted for everything, over evidence that it
    # did not, is contradicting itself and is refused rather than believed.
    forged = result.to_dict()
    forged["batch_usage_capture"] = "complete"
    assert QCResult.from_dict(forged) is None

    # So is a serialized total that disagrees with the seats under it.
    miscounted = result.to_dict()
    miscounted["uncollected_batch_requests"] = 0
    assert QCResult.from_dict(miscounted) is None


def test_a_report_written_before_the_disclosure_is_never_promoted():
    """It carries no counts, and cannot say. "" is the honest reading."""
    from backend.qc.engine import QCResult

    payload = _run(SequencedFakeClient(_one_finding_scripts())).to_dict()
    payload.pop("batch_usage_capture")
    payload.pop("uncollected_batch_requests")
    payload.pop("unassigned_batch_results")

    reloaded = QCResult.from_dict(payload)
    assert reloaded is not None
    assert reloaded.batch_usage_capture == ""
    assert reloaded.unassigned_batch_results == 0


def test_the_disclosure_does_not_stale_a_retained_review():
    """Cost capture is not a review input.

    Putting it in the hashed manifest would flip every retained result stale
    to describe something the reviewers never read.
    """
    store = _store()
    result = _run(SequencedFakeClient(_one_finding_scripts()))
    assert result.matches_inputs(store.index, store.doc, None, DEFAULT_MODULE)
    manifest_text = repr(result.input_manifest)
    for key in (
        "batch_usage_capture",
        "uncollected_batch_requests",
        "unassigned_batch_results",
    ):
        assert key not in manifest_text
