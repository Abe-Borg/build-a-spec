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

import httpx
from anthropic import BadRequestError

from backend import settings
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


def test_stopping_cancels_the_batch_and_settles_every_open_seat():
    """Stop lands while the batch is in flight, after phase 1 has finished.

    Setting the flag before the run would kill the lenses instead, which is
    a different (already covered) path — the point here is that a batch we
    have already submitted is cancelled rather than waited out.
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
    assert len(result.inconclusive) == 1
    assert all(
        v.status == "cancelled" for v in result.inconclusive[0].verdicts
    )
    assert result.execution_status in {"partial", "cancelled"}


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
