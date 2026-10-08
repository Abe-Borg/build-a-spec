"""A batched verifier seat that skipped its output tool is reminded (5.5 prompting upgrade, P55-5).

P55-4 reminds a STREAMED research or Final QC call whose reply ends its turn
without the output tool (the Opus 5.5 guide's early stop). A batched
verifier seat is the same call on another transport, and it used to fail at
once ("QC produced no parseable payload."), so the two transports could
reach different verdicts for the same reply. Now the batched seat gets the
same reminder, with the same text and the same cap, in the NEXT batch round:
the reply is appended verbatim, then one user turn naming
``submit_qc_verdict``, and the seat stays unsettled until a later round
settles it.

What is batch-specific, and pinned here: a reminder needs a round to run in
(the ``no_round_left`` rule a refused submission's retry already follows),
the settlement window never buys one, the count survives a resume and
resets on a restart like the rest of the conversation, and the reminded
seat's record is priced at the batch rate and reconciles. The last test
holds the two transports to the same verdicts and the same failure text.
"""
from __future__ import annotations

import logging
import threading
from types import SimpleNamespace

import pytest

from backend import settings
from backend.qc import engine as qc_engine
from backend.qc.engine import QCResult, _BatchSeatState, _CallSpec
from backend.qc.schema import QC_VERDICT_TOOL_NAME
from backend.usage_ledger import estimate_usage_cost
from tests.fakes import (
    SequencedFakeClient,
    pause_response,
    qc_verdict_response,
    research_response,
    text_block,
    user_text,
)
from tests.test_prompt55_missing_tool_reminder import _SAID, _invented, _text_only
from tests.test_qc_batch_verification import _one_finding_scripts, _run

_TITLE = "Batched reminder finding"
_NO_PAYLOAD = "QC produced no parseable payload"
_QUEUED = "queued for the next batch round"


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """No real seconds: a streamed retry's backoff reads the engine's ``time``."""
    monkeypatch.setattr(qc_engine.time, "sleep", lambda _s: None)


def _client(*turns) -> SequencedFakeClient:
    """One two-seat panel. The batch fake answers a round's seats in order,
    so the first turn is seat 1's opening reply, the second seat 2's, and
    every later turn belongs to whichever seat the next round submits."""
    return SequencedFakeClient(_one_finding_scripts(_TITLE, verdicts=list(turns)))


def _rounds(client: SequencedFakeClient) -> list[list[dict]]:
    return client.batches.created


def _seats(result):
    finding = next(
        f
        for f in [
            *result.findings,
            *result.refuted,
            *result.disputed,
            *result.inconclusive,
        ]
        if f.title == _TITLE
    )
    return finding, sorted(finding.verdicts, key=lambda v: v.reviewer_index)


def _reminder_lines(caplog) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "buildaspec.qc" and _QUEUED in record.getMessage()
    ]


def _seat_requests(client: SequencedFakeClient) -> list[dict]:
    """Every verifier request for the panel, both transports (the fake keeps
    a batched seat's params in ``requests`` too)."""
    return [
        r
        for r in client.requests
        if "[[QC-VERIFY:" in user_text(r["messages"]) and _TITLE in user_text(r["messages"])
    ]


# ---------------------------------------------------------------------------
# P55-5.1 — reminded in the next round, at most twice
# ---------------------------------------------------------------------------


def test_a_seat_that_ends_without_the_tool_is_reminded_in_the_next_round(caplog):
    caplog.set_level(logging.INFO, logger="buildaspec.qc")
    text = _text_only()
    client = _client(text, qc_verdict_response(True), qc_verdict_response(True))
    result = _run(client)

    rounds = _rounds(client)
    assert len(rounds) == 2
    # Round 2 carries only the reminded seat, under its own custom id.
    assert len(rounds[1]) == 1
    assert rounds[1][0]["custom_id"] == rounds[0][0]["custom_id"]
    params = rounds[1][0]["params"]
    messages = params["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    # The opening request as it was, then the reply, then ONE reminder turn.
    assert messages[0] == rounds[0][0]["params"]["messages"][0]
    assert _SAID in str(messages[1]["content"])
    assert messages[2]["content"] == [
        {
            "type": "text",
            "text": qc_engine._missing_tool_reminder(QC_VERDICT_TOOL_NAME),
        }
    ]
    # A batched request never carries the continuation tail or a container.
    assert "cache_control" not in params
    assert "container" not in params

    finding, seats = _seats(result)
    assert [seat.status for seat in seats] == ["completed", "completed"]
    assert [seat.api_request_count for seat in seats] == [2, 1]
    assert [seat.model_response_count for seat in seats] == [2, 1]
    assert finding.verification_outcome == "upheld"
    assert result.execution_status == "complete"
    assert len(_reminder_lines(caplog)) == 1


def test_the_reminder_request_is_the_streamed_one(monkeypatch):
    """One reminder, two transports: the same request bytes in the same order."""
    monkeypatch.setattr(settings, "QC_MAX_WORKERS", 1)
    streamed = _client(_text_only(), qc_verdict_response(True), qc_verdict_response(True))
    _run(streamed, batch=False)
    batched = _client(_text_only(), qc_verdict_response(True), qc_verdict_response(True))
    _run(batched)

    stream_reminder = _seat_requests(streamed)[1]
    batch_reminder = _rounds(batched)[1][0]["params"]

    def without_markers(value):
        """The same request with every ``cache_control`` marker removed: the
        cache TTL is the one documented difference between the transports
        (streamed seats 5 minutes, batched seats 1 hour — the streamed
        stagger), and it is pinned in tests/test_qc_batch_verification.py."""
        if isinstance(value, dict):
            return {
                k: without_markers(v) for k, v in value.items() if k != "cache_control"
            }
        if isinstance(value, list):
            return [without_markers(item) for item in value]
        return value

    for key in ("model", "system", "tools", "thinking", "output_config", "messages"):
        assert without_markers(stream_reminder[key]) == without_markers(
            batch_reminder[key]
        ), key


def test_the_reminder_request_is_sanitized_like_a_pause_resume():
    """The reply is appended and then the resend sanitizer runs, as on the
    pause path and the streamed reminder. Once the reminder turn follows it,
    the reply is no longer the trailing assistant message, so a search it
    left unpaired is dropped rather than submitted as an invalid request."""
    dangling = SimpleNamespace(
        type="server_tool_use",
        id="srvtoolu_dangling",
        name="web_search",
        input={"query": "a search with no result"},
    )
    text = research_response(
        items=None,
        extra_blocks=[dangling, text_block(_SAID)],
        stop_reason="end_turn",
    )
    client = _client(text, qc_verdict_response(True), qc_verdict_response(True))
    result = _run(client)

    sent = _rounds(client)[1][0]["params"]["messages"][1]["content"]
    types = [
        block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
        for block in sent
    ]
    assert types == ["text"]
    _, seats = _seats(result)
    assert [seat.status for seat in seats] == ["completed", "completed"]


def test_an_invented_tool_gets_an_error_result_for_every_call():
    client = _client(
        _invented("record_verdict", "submit_verdict"),
        qc_verdict_response(True),
        qc_verdict_response(True),
    )
    result = _run(client)

    turn = _rounds(client)[1][0]["params"]["messages"][-1]
    assert turn["role"] == "user"
    blocks = turn["content"]
    assert [b["type"] for b in blocks] == ["tool_result", "tool_result"]
    assert [b["tool_use_id"] for b in blocks] == ["toolu_invented_0", "toolu_invented_1"]
    assert all(b["is_error"] is True for b in blocks)
    assert all(QC_VERDICT_TOOL_NAME in b["content"] for b in blocks)
    _, seats = _seats(result)
    assert seats[0].status == "completed"


def test_two_reminders_then_the_familiar_failure_with_the_count(caplog):
    caplog.set_level(logging.INFO, logger="buildaspec.qc")
    client = _client(
        _text_only(), qc_verdict_response(True), _text_only(), _text_only()
    )
    result = _run(client)

    assert len(_rounds(client)) == 3
    finding, seats = _seats(result)
    assert seats[0].status == "failed"
    assert seats[0].error == f"{_NO_PAYLOAD} (reminders sent: 2)."
    assert seats[0].api_request_count == 3
    assert seats[1].status == "completed"
    assert finding in result.inconclusive
    assert result.execution_status == "partial"
    lines = _reminder_lines(caplog)
    assert len(lines) == 2
    assert "reminder 1 of 2" in lines[0]
    assert "reminder 2 of 2" in lines[1]


def test_a_reminder_is_logged_with_the_seats_ids_and_never_content(caplog):
    caplog.set_level(logging.INFO, logger="buildaspec.qc")
    client = _client(_text_only(), qc_verdict_response(True), qc_verdict_response(True))
    _run(client)

    [line] = _reminder_lines(caplog)
    assert "QC batched verifier seat" in line
    assert "candidate_id=candidate-1" in line
    assert "reviewer_index=1" in line
    assert f"without {QC_VERDICT_TOOL_NAME}" in line
    assert "reminder 1 of 2" in line
    assert _SAID not in line


# ---------------------------------------------------------------------------
# P55-5.1 — never while recovering, never without a round left, never after
# a Stop, never past the continuation budget
# ---------------------------------------------------------------------------


def test_the_settlement_window_never_reminds(caplog):
    """A Stop lands with the batch live: the window collects, buys nothing."""
    caplog.set_level(logging.INFO, logger="buildaspec.qc")
    stopped = threading.Event()
    client = _client(_text_only(), qc_verdict_response(True), qc_verdict_response(True))
    submit = client.batches.create

    def create(*, requests):
        batch = submit(requests=requests)
        stopped.set()
        return batch

    client.batches.create = create
    result = _run(client, should_stop=stopped.is_set)

    assert len(_rounds(client)) == 1
    assert client.batches.cancelled  # the window really ran
    _, seats = _seats(result)
    assert seats[0].status == "failed"
    assert seats[0].error == f"{_NO_PAYLOAD} (reminders sent: 0)."
    assert seats[1].status == "completed"
    assert _reminder_lines(caplog) == []


@pytest.mark.parametrize(
    ("max_rounds", "turns", "sent"),
    [
        (1, ["text", "verdict"], 0),
        (2, ["text", "verdict", "text"], 1),
    ],
    ids=["one-round", "two-rounds"],
)
def test_the_last_round_never_reminds(monkeypatch, caplog, max_rounds, turns, sent):
    """A reminder needs a round to run in; the seat fails with its own cause,
    never the round ceiling's."""
    caplog.set_level(logging.INFO, logger="buildaspec.qc")
    monkeypatch.setattr(settings, "QC_BATCH_MAX_ROUNDS", max_rounds)
    scripted = {"text": _text_only, "verdict": lambda: qc_verdict_response(True)}
    client = _client(*(scripted[turn]() for turn in turns))
    result = _run(client)

    assert len(_rounds(client)) == max_rounds
    _, seats = _seats(result)
    assert seats[0].error == f"{_NO_PAYLOAD} (reminders sent: {sent})."
    assert "round ceiling" not in seats[0].error
    assert len(_reminder_lines(caplog)) == sent


def test_no_reminder_once_a_stop_has_landed(caplog):
    """A Stop landing while the round's results are read takes the Stop's path."""
    caplog.set_level(logging.INFO, logger="buildaspec.qc")
    stopped = threading.Event()
    client = _client(_text_only(), qc_verdict_response(True), qc_verdict_response(True))
    read = client.batches.results

    def results(batch_id):
        stopped.set()
        return read(batch_id)

    client.batches.results = results
    result = _run(client, should_stop=stopped.is_set)

    assert len(_rounds(client)) == 1
    _, seats = _seats(result)
    assert seats[0].status == "cancelled"
    assert seats[0].error == "Cancelled by user."
    assert seats[1].status == "completed"
    assert _reminder_lines(caplog) == []


@pytest.mark.parametrize(
    ("ceiling", "batched", "streamed", "sent"),
    [
        (1, ["pause", "verdict", "text"], ["pause", "text", "verdict"], 0),
        (
            2,
            ["pause", "verdict", "text", "text"],
            ["pause", "text", "text", "verdict"],
            1,
        ),
    ],
    ids=["budget-spent", "one-left"],
)
def test_a_reminder_needs_budget_left_like_a_continuation(
    monkeypatch, ceiling, batched, streamed, sent
):
    """The continuation budget counts reminders, on both transports alike."""
    monkeypatch.setattr(qc_engine, "QC_MAX_CONTINUATIONS", ceiling)
    monkeypatch.setattr(settings, "QC_MAX_WORKERS", 1)
    scripted = {
        "pause": pause_response,
        "text": _text_only,
        "verdict": lambda: qc_verdict_response(True),
    }
    errors = []
    for batch, turns in ((True, batched), (False, streamed)):
        result = _run(_client(*(scripted[t]() for t in turns)), batch=batch)
        _, seats = _seats(result)
        errors.append(seats[0].error)
    assert errors == [f"{_NO_PAYLOAD} (reminders sent: {sent})."] * 2


# ---------------------------------------------------------------------------
# P55-5.2 — the count survives a resume and resets on a restart
# ---------------------------------------------------------------------------


def test_a_resume_resubmits_the_reminder_and_keeps_the_count(caplog):
    caplog.set_level(logging.INFO, logger="buildaspec.qc")
    events: list[dict] = []
    client = _client(
        _text_only(),
        qc_verdict_response(True),
        ConnectionError("peer closed connection"),  # the reminder request fails
        _text_only(),
        _text_only(),
    )
    result = _run(client, events=events)

    rounds = _rounds(client)
    assert len(rounds) == 4
    # The resume submits the reminder request exactly as it stood.
    assert rounds[2][0]["params"]["messages"] == rounds[1][0]["params"]["messages"]
    retries = [e for e in events if e["type"] == "verifier_retry"]
    assert [e["mode"] for e in retries] == ["resume"]
    # Kept across the resume: one more reminder, then the failure at two.
    _, seats = _seats(result)
    assert seats[0].error == f"{_NO_PAYLOAD} (reminders sent: 2)."
    lines = _reminder_lines(caplog)
    assert len(lines) == 2 and "reminder 2 of 2" in lines[1]


def test_a_restart_starts_the_count_again(caplog):
    caplog.set_level(logging.INFO, logger="buildaspec.qc")
    events: list[dict] = []
    client = _client(
        _text_only(),
        qc_verdict_response(True),
        _text_only(),  # the first reminder's answer: a second reminder
        ConnectionError("peer closed connection"),  # resumed
        ConnectionError("peer closed connection"),  # final attempt: restart
        _text_only(),  # a fresh conversation, reminded again
        qc_verdict_response(True),
    )
    result = _run(client, events=events)

    rounds = _rounds(client)
    assert len(rounds) == 6
    retries = [e for e in events if e["type"] == "verifier_retry"]
    assert [e["mode"] for e in retries] == ["resume", "restart"]
    # The restart submits the opening request alone; the round after it
    # carries a reminder again.
    assert [m["role"] for m in rounds[4][0]["params"]["messages"]] == ["user"]
    assert [m["role"] for m in rounds[5][0]["params"]["messages"]] == [
        "user",
        "assistant",
        "user",
    ]
    _, seats = _seats(result)
    assert seats[0].status == "completed"
    lines = _reminder_lines(caplog)
    assert [line.split("reminder ")[1][:6] for line in lines] == [
        "1 of 2",
        "2 of 2",
        "1 of 2",
    ]


def _spec() -> _CallSpec:
    return _CallSpec(
        system_prompt="system",
        shared_prefix="shared",
        request_suffix="suffix",
        tools=(),
        tool_name=QC_VERDICT_TOOL_NAME,
        json_tag="qc_verdict_json",
        model=settings.QC_MODEL,
        max_tokens=4096,
        effort="medium",
        max_searches=0,
        cache_ttl="1h",
    )


def test_the_seat_state_keeps_the_count_on_a_resume_and_drops_it_on_a_restart():
    state = _BatchSeatState(spec=_spec(), messages=[])
    state.messages = state.initial_messages()
    state.remind(_text_only())
    assert (state.reminders_sent, state.continuations) == (1, 1)
    assert state.may_remind()

    state.resume_attempt()
    assert state.reminders_sent == 1
    state.remind(_text_only())
    assert not state.may_remind()  # two sent

    state.restart_attempt()
    assert state.reminders_sent == 0
    assert state.continuations == 0
    assert state.may_remind()


def test_may_remind_reads_the_continuation_budget(monkeypatch):
    monkeypatch.setattr(qc_engine, "QC_MAX_CONTINUATIONS", 3)
    state = _BatchSeatState(spec=_spec(), messages=[])
    state.continuations = 2
    assert state.may_remind()
    state.continuations = 3
    assert not state.may_remind()


# ---------------------------------------------------------------------------
# P55-5.3 — the reminded seat's record reconciles at the batch rate
# ---------------------------------------------------------------------------


def test_the_reminded_seat_is_priced_at_the_batch_rate_and_reconciles():
    client = _client(
        _text_only(tokens={"input": 20_000, "output": 3_000}),
        qc_verdict_response(True, tokens={"input": 20_000, "output": 4_000}),
        qc_verdict_response(True, tokens={"input": 21_000, "output": 5_000}),
    )
    result = _run(client)

    _, seats = _seats(result)
    reminded = seats[0]
    assert reminded.api_request_count == reminded.model_response_count == 2
    # Both of its responses, and nothing of its neighbour's.
    assert reminded.usage_totals["input_tokens"] == 41_000
    assert reminded.usage_totals["output_tokens"] == 8_000
    assert reminded.cost_multiplier == settings.BATCH_COST_MULTIPLIER
    assert reminded.estimated_cost_usd > 0
    assert reminded.estimated_cost_usd == estimate_usage_cost(
        settings.QC_MODEL,
        reminded.usage_totals,
        multiplier=settings.BATCH_COST_MULTIPLIER,
    )
    # The run's totals are the sum of its records, and the report loads.
    assert result._audit_accounting_consistent()
    batched_bucket = result.usage_by_meter_category()["qc_batched"]
    assert batched_bucket["input_tokens"] == 41_000 + 20_000
    restored = QCResult.from_dict(result.to_dict())
    assert restored is not None
    _, restored_seats = _seats(restored)
    assert restored_seats[0].usage_totals == reminded.usage_totals
    assert restored_seats[0].cost_multiplier == settings.BATCH_COST_MULTIPLIER


def test_progress_stays_phase_level_and_no_new_event_type():
    """A reminded seat is unsettled until it settles, and nothing new is emitted."""
    events: list[dict] = []
    _run(
        _client(_text_only(), qc_verdict_response(True), qc_verdict_response(True)),
        events=events,
    )

    batch = [e for e in events if e["type"] == "verification_batch"]
    submitted = [e for e in batch if e["status"] == "submitted"]
    assert [(e["submitted"], e["settled"], e["total"]) for e in submitted] == [
        (2, 0, 2),
        (1, 1, 2),
    ]
    assert batch[-1]["status"] == "ended" and batch[-1]["settled"] == 2
    # No reminder event, no retry line, and still no live seat frames.
    assert not [e for e in events if "remind" in e["type"]]
    assert [
        e["type"]
        for e in events
        if e["type"].startswith("verifier_")
        and e["type"] not in {"verifier_started", "verifier_complete"}
    ] == []


# ---------------------------------------------------------------------------
# The two transports agree
# ---------------------------------------------------------------------------


def _outcome(result) -> dict:
    finding, seats = _seats(result)
    return {
        "execution_status": result.execution_status,
        "verification_outcome": finding.verification_outcome,
        "panel": [(s.reviewer_index, s.status, s.upholds, s.error) for s in seats],
        "seat_requests": [s.api_request_count for s in seats],
        "seat_responses": [s.model_response_count for s in seats],
    }


def test_batched_and_streamed_verdicts_agree_for_a_reminded_seat(monkeypatch):
    # One worker, so the streamed seats run in order and take their turns
    # in the order the scripts list them.
    monkeypatch.setattr(settings, "QC_MAX_WORKERS", 1)
    batched = _run(
        _client(_text_only(), qc_verdict_response(True), qc_verdict_response(True))
    )
    streamed = _run(
        _client(_text_only(), qc_verdict_response(True), qc_verdict_response(True)),
        batch=False,
    )
    assert _outcome(batched) == _outcome(streamed)
    assert _outcome(batched)["verification_outcome"] == "upheld"


def test_batched_and_streamed_fail_the_same_way_when_the_reminders_run_out(
    monkeypatch,
):
    monkeypatch.setattr(settings, "QC_MAX_WORKERS", 1)
    # The same replies per seat; only the order the two transports ask for
    # them differs (a batch round answers every seat before the next round).
    batched = _run(
        _client(_text_only(), qc_verdict_response(True), _text_only(), _text_only())
    )
    streamed = _run(
        _client(_text_only(), _text_only(), _text_only(), qc_verdict_response(True)),
        batch=False,
    )
    assert _outcome(batched) == _outcome(streamed)
    assert _outcome(batched)["panel"][0][3] == f"{_NO_PAYLOAD} (reminders sent: 2)."
