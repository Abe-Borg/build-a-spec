"""A fan-out call that skipped its output tool is reminded (5.5 prompting upgrade, P55-4).

The Opus 5.5 prompting guide, "Unattended agentic runs": some progress
updates "end the turn with text rather than a tool call", and a loop that
treats such a turn as the end of the task stops there. A research area and
a streamed Final QC call are recorded only by their output tool, so such a
turn used to FAIL the call outright. Now it is treated as a report: the
assistant content is appended verbatim, then ONE user turn naming the output
tool (a text block, or an ``is_error`` result for every client tool the
model invented), at most twice per conversation.

Research's ``_run_dimension`` and Final QC's ``_run_lens`` keep separate
copies of the loop (the copy-don't-import posture), so ONE assertion set runs
over both (the ``tests/test_retry_resume.py`` precedent). Then the other
streamed QC calls — a streamed verifier seat, a grouping call that paused,
a warm lead — the shared shape helper, the system-prompt lines, and the
retained Final QC result staying current (the plan's F3).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from backend import settings
from backend.qc import engine as qc_engine
from backend.qc.engine import run_final_qc
from backend.qc.schema import (
    QC_CONSOLIDATION_TOOL_NAME,
    QC_FINDINGS_TOOL_NAME,
    QC_LENSES,
    QC_VERDICT_TOOL_NAME,
)
from backend.research import engine as research_engine
from backend.research.schema import RESEARCH_TOOL_NAME, missing_output_tool_reply
from backend.spec_modules import DEFAULT_MODULE as QC_MODULE
from backend.spec_modules.hyperscale_fire import HYPERSCALE_FIRE as RESEARCH_MODULE
from tests.fakes import (
    SequencedFakeClient,
    pause_response,
    qc_findings_response,
    qc_verdict_response,
    research_response,
    singleton_consolidation_for,
    text_block,
    tool_use_block,
    user_text,
)
from tests.test_qc_batch_warm_lead import _lineage_scripts, _medium, _titles
from tests.test_qc_live_events import _finding
from tests.test_qc_live_events import _scripts as _qc_scripts
from tests.test_qc_live_events import _store
from tests.test_qc_warm_launch import _store as _two_paragraph_store
from tests.test_qc_warm_launch import _two_bucket_scripts
from tests.test_research_engine import DIM_KEYS, PROFILE, _item

_RESET = anthropic.APIConnectionError(
    message="connection reset",
    request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
)
_CITED = "Cites the page it read before the reminder."
_PAGE = "https://example.gov/page-read-before"
_SAID = "I have finished the review and will now record it."


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    """No real seconds: both engines read the one ``time`` module."""
    monkeypatch.setattr(research_engine.time, "sleep", lambda _s: None)


def _text_only(*, urls=(), tokens=None, said: str = _SAID):
    """A turn that ends with text and no output tool (``end_turn``).

    The shape is engine-neutral, so it serves research and Final QC alike.
    ``urls`` adds one finished, paired search whose result the turn read.
    """
    return research_response(
        items=None,
        queries=["a search that finished"] if urls else None,
        searched_urls=list(urls),
        extra_blocks=[text_block(said)],
        stop_reason="end_turn",
        tokens=tokens,
    )


def _invented(*names: str):
    """A turn that calls client tools the request never declared."""
    return research_response(
        items=None,
        extra_blocks=[
            tool_use_block(f"toolu_invented_{index}", name, {"items": []})
            for index, name in enumerate(names)
        ],
        stop_reason="tool_use",
    )


def _pause():
    return pause_response()


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
    grounded: bool
    # Final QC records these per call; research records neither.
    api_requests: int | None = None
    responses: int | None = None


class _StopAfter(SequencedFakeClient):
    """Lands a Stop once ``after`` requests have been answered."""

    def __init__(self, scripts, *, after: int) -> None:
        super().__init__(scripts)
        self.after = after
        self.stopped = False

    def stream(self, **request):
        ctx = super().stream(**request)
        if len(self.requests) >= self.after:
            self.stopped = True
        return ctx


class _ResearchHarness:
    """One research dimension through ``_run_dimension``."""

    name = "research"
    engine = research_engine
    tool = RESEARCH_TOOL_NAME
    key = DIM_KEYS["governing_codes"]
    max_continuations = research_engine.RESEARCH_MAX_CONTINUATIONS
    no_payload = "Research produced no parseable payload"
    logger = "buildaspec.research"

    def final(self, *, cited=(), tokens=None):
        return research_response(
            items=[_item(_CITED, list(cited))], tokens=tokens
        )

    def run(self, client, *, continuation_cache=False, should_stop=None) -> _Call:
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
            model="claude-sonnet-5-5",
            max_tokens=4096,
            continuation_cache=continuation_cache,
            event_sink=events.append,
            should_stop=should_stop or (lambda: False),
        )
        item = next((i for i in outcome.items if i.requirement == _CITED), None)
        return _Call(
            requests=list(client.requests),
            retries=[e for e in events if e["type"] == "dimension_retry"],
            status=outcome.status.status,
            error=outcome.status.error,
            input_tokens=outcome.status.input_tokens,
            grounded=bool(item and item.grounded),
        )


class _QcHarness:
    """The web-tooled compliance lens through ``_run_lens``."""

    name = "qc"
    engine = qc_engine
    tool = QC_FINDINGS_TOOL_NAME
    key = "[[QC-LENS:code_compliance]]"
    max_continuations = qc_engine.QC_MAX_CONTINUATIONS
    no_payload = "QC produced no parseable payload"
    logger = "buildaspec.qc"

    def final(self, *, cited=(), tokens=None):
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
            tokens=tokens,
        )

    def run(self, client, *, continuation_cache=False, should_stop=None) -> _Call:
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
            should_stop=should_stop or (lambda: False),
        )
        status = outcome.status
        check = next((c for c in status.reviewed_checks if c.check == _CITED), None)
        return _Call(
            requests=list(client.requests),
            retries=[e for e in events if e["type"] == "lens_retry"],
            status=status.status,
            error=status.error,
            input_tokens=status.usage_totals.get("input_tokens", 0),
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


def _client(harness, turns) -> SequencedFakeClient:
    return SequencedFakeClient({harness.key: list(turns)})


def _reminder_turn(request: dict) -> dict:
    """The user turn a reminder request ends on."""
    return request["messages"][-1]


# ---------------------------------------------------------------------------
# P55-4.1 — when it reminds, and how often
# ---------------------------------------------------------------------------


def test_a_text_only_end_of_turn_is_reminded_once_then_recorded(harness) -> None:
    text = _text_only()
    call = harness.run(_client(harness, [text, harness.final()]))

    assert call.status == "completed"
    assert call.error == ""
    assert len(call.requests) == 2
    reminder = call.requests[1]["messages"]
    # The opening turn, the reply verbatim, one user turn — nothing else.
    assert reminder[0] == call.requests[0]["messages"][0]
    assert reminder[1]["role"] == "assistant"
    assert reminder[1]["content"] is text.content
    assert len(reminder) == 3


def _extra_research_requests(harness) -> int:
    """Research hands in what it has once its reminders are spent: one final
    submission, plus the resend Sonnet 5.5's automatic tool choice earns it
    (Sonnet 5.5 rejects a forced ``tool_choice``). Final QC has neither."""
    if harness.name != "research":
        return 0
    return 1 + harness.engine._SUBMISSION_RESENDS


def test_two_reminders_then_the_familiar_failure_with_the_count(harness) -> None:
    extra_submission = _extra_research_requests(harness)
    call = harness.run(
        _client(harness, [_text_only() for _ in range(3 + extra_submission)])
    )

    assert len(call.requests) == 3 + extra_submission
    assert call.status == "failed"
    assert call.error.startswith(harness.no_payload)
    assert "reminders sent: 2" in call.error
    # The second reminder answers the second reply, appended after the first.
    last = call.requests[2]["messages"]
    assert [m["role"] for m in last] == [
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
    ]
    assert harness.engine._MISSING_TOOL_REMINDERS == 2


@pytest.mark.parametrize(
    "stop_reason, reason_word",
    [("max_tokens", "incomplete"), ("refusal", "declined")],
)
def test_no_reminder_after_max_tokens_or_a_refusal(
    harness, stop_reason, reason_word
) -> None:
    cut = research_response(
        items=None, extra_blocks=[text_block(_SAID)], stop_reason=stop_reason
    )
    call = harness.run(_client(harness, [cut, harness.final()]))

    assert len(call.requests) == 1
    assert call.status == "failed"
    assert reason_word in call.error.lower()
    assert "reminders sent" not in call.error


def test_no_reminder_once_a_stop_has_landed(harness, caplog) -> None:
    """A Stop at the reminder decision takes the Stop's own path: nothing is
    sent, and nothing claims a reminder was — the loop's own Stop check
    would stop the request anyway, so the log is what tells them apart."""
    client = _StopAfter({harness.key: [_text_only(), harness.final()]}, after=1)
    with caplog.at_level(logging.INFO, logger=harness.logger):
        call = harness.run(client, should_stop=lambda: client.stopped)

    assert len(call.requests) == 1
    assert call.status == "failed"
    assert call.error == "Cancelled by user."
    assert not [
        record
        for record in caplog.records
        if record.name == harness.logger and "reminder" in record.getMessage()
    ]


def test_a_reminder_needs_budget_left_like_a_continuation(harness) -> None:
    """The reminder request counts against the conversation's continuation
    budget. With the budget spent, no reminder is sent — the familiar
    failure, with a count of zero; one request short of it, the reminder
    goes out and its response is the last the budget allows."""
    budget = harness.max_continuations
    extra_submission = _extra_research_requests(harness)
    spent = harness.run(_client(harness, [
        *[_pause()] * budget, *[_text_only() for _ in range(1 + extra_submission)]
    ]))
    assert len(spent.requests) == budget + 1 + extra_submission
    assert spent.status == "failed"
    assert "reminders sent: 0" in spent.error

    last = harness.run(
        _client(harness, [*[_pause()] * (budget - 1), _text_only(), harness.final()])
    )
    assert len(last.requests) == budget + 1
    assert last.status == "completed"


# ---------------------------------------------------------------------------
# P55-4.2 — the reminder's shape
# ---------------------------------------------------------------------------


def test_the_reminder_is_one_text_block_naming_the_tool_and_carries_no_tail(
    harness,
) -> None:
    call = harness.run(
        _client(harness, [_text_only(), harness.final()]), continuation_cache=True
    )

    turn = _reminder_turn(call.requests[1])
    assert turn["role"] == "user"
    assert len(turn["content"]) == 1
    block = turn["content"][0]
    assert block["type"] == "text"
    assert f"without a {harness.tool} call" in block["text"]
    assert f"Call {harness.tool} now" in block["text"]
    assert "nothing was recorded" in block["text"]
    # Ends on the user, so it is not a continuation: no tail, even switched on.
    assert "cache_control" not in call.requests[1]


def test_the_reminder_request_is_sanitized_like_a_pause_resume(harness) -> None:
    """The reply is appended and then the resend sanitizer runs, as on the
    pause path. Once a user turn follows it, the reply is no longer the
    trailing assistant message, so a search it left unpaired is dropped
    rather than sent as an invalid request."""
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
    call = harness.run(_client(harness, [text, harness.final()]))

    assert call.status == "completed"
    sent = call.requests[1]["messages"][1]["content"]
    assert [getattr(block, "type", None) for block in sent] == ["text"]


def test_an_invented_tool_gets_an_error_result_for_every_call(harness) -> None:
    invented = _invented("submit_findings", "record_research")
    call = harness.run(_client(harness, [invented, harness.final()]))

    assert call.status == "completed"
    assert call.requests[1]["messages"][1]["content"] is invented.content
    turn = _reminder_turn(call.requests[1])
    assert turn["role"] == "user"
    # One is_error result per call, in order, and no text beside them.
    assert [block["type"] for block in turn["content"]] == [
        "tool_result",
        "tool_result",
    ]
    assert [block["tool_use_id"] for block in turn["content"]] == [
        "toolu_invented_0",
        "toolu_invented_1",
    ]
    for block, name in zip(turn["content"], ("submit_findings", "record_research")):
        assert block["is_error"] is True
        assert f"`{name}`" in block["content"]
        assert f"recorded only by {harness.tool}" in block["content"]
        assert f"Call {harness.tool} now" in block["content"]


def test_each_reminder_is_logged_with_the_calls_id_and_never_content(
    harness, caplog
) -> None:
    with caplog.at_level(logging.INFO, logger=harness.logger):
        harness.run(_client(harness, [_text_only(), _text_only(), harness.final()]))

    lines = [
        record.getMessage()
        for record in caplog.records
        if record.name == harness.logger and "reminder" in record.getMessage()
    ]
    assert len(lines) == 2
    assert "reminder 1 of 2" in lines[0] and "reminder 2 of 2" in lines[1]
    ident = "governing_codes" if harness.name == "research" else "code_compliance"
    for line in lines:
        assert ident in line
        assert harness.tool in line
        assert _SAID not in line
        assert "nothing was recorded" not in line


# ---------------------------------------------------------------------------
# P55-4.3 — billing, grounding, the budget, retries and the tail
# ---------------------------------------------------------------------------


def test_each_response_is_billed_once_and_every_request_counted(harness) -> None:
    call = harness.run(
        _client(
            harness,
            [_text_only(tokens={"input": 100}), harness.final(tokens={"input": 7})],
        )
    )
    assert call.status == "completed"
    assert call.input_tokens == 107
    if harness.name == "qc":
        assert call.api_requests == 2
        assert call.responses == 2


def test_a_citation_in_the_reminded_reply_grounds_on_a_page_read_before(
    harness,
) -> None:
    call = harness.run(
        _client(harness, [_text_only(urls=[_PAGE]), harness.final(cited=[_PAGE])])
    )
    assert call.status == "completed"
    assert call.grounded is True


def test_a_failure_in_flight_resends_the_reminder_as_it_stood(harness) -> None:
    call = harness.run(_client(harness, [_text_only(), _RESET, harness.final()]))

    assert call.status == "completed"
    assert [event["mode"] for event in call.retries] == ["resume"]
    assert len(call.requests) == 3
    # The resent request IS the reminder request that failed.
    assert json.dumps(call.requests[2]["messages"], default=repr) == json.dumps(
        call.requests[1]["messages"], default=repr
    )


def test_a_restart_starts_the_reminder_count_again(harness) -> None:
    """Attempt 1 reminds once, its reminder request fails and resumes, fails
    again and — the final attempt — restarts. The fresh conversation may
    remind twice; had the count carried over, its second text-only reply
    would have failed the call."""
    call = harness.run(
        _client(
            harness,
            [
                _text_only(),
                _RESET,
                _RESET,
                _text_only(),
                _text_only(),
                harness.final(),
            ],
        )
    )
    assert [event["mode"] for event in call.retries] == ["resume", "restart"]
    assert call.status == "completed"
    assert len(call.requests) == 6
    # The restarted conversation's first reminder looks like the first one.
    assert len(call.requests[4]["messages"]) == 3
    assert len(call.requests[5]["messages"]) == 5


def test_a_pause_after_a_reminder_resumes_with_the_tail(harness) -> None:
    """The reminder request carries no tail; the continuation of ITS pause
    ends on the assistant, so it does — the tail's rules are untouched."""
    call = harness.run(
        _client(harness, [_text_only(), _pause(), harness.final()]),
        continuation_cache=True,
    )
    assert call.status == "completed"
    assert "cache_control" not in call.requests[1]
    assert call.requests[2]["messages"][-1]["role"] == "assistant"
    assert call.requests[2]["cache_control"] == {"type": "ephemeral"}


# ---------------------------------------------------------------------------
# Every streamed Final QC call: a streamed seat, a grouping call, a lead
# ---------------------------------------------------------------------------


def _qc_run(client, *, store=None, batch=False, lead=False, warm=0.0):
    store = store or _store()
    return run_final_qc(
        store.doc,
        None,
        QC_MODULE,
        client,
        model=settings.QC_MODEL,
        max_tokens=4096,
        version_index=store.index,
        started_at="2026-09-30T10:00:00-07:00",
        finished_at="2026-09-30T10:01:00-07:00",
        run_id="qc-missing-tool-reminder-test",
        batch_verification=batch,
        batch_warm_lead=lead,
        warm_wait_seconds=warm,
        continuation_cache=False,
    )


def _requests_for(client: SequencedFakeClient, marker: str) -> list[dict]:
    return [r for r in client.requests if marker in user_text(r["messages"])]


def test_a_streamed_verifier_seat_is_reminded(monkeypatch) -> None:
    monkeypatch.setattr(settings, "QC_MAX_WORKERS", 1)
    title = "Seat reminder candidate"
    scripts = _qc_scripts(
        code_compliance=[
            qc_findings_response("code_compliance", findings=[_finding(title)])
        ]
    )
    # Seat 1 answers in text first; its reminder takes the next verdict.
    scripts[title] = [
        _text_only(),
        qc_verdict_response(True),
        qc_verdict_response(True),
    ]
    client = SequencedFakeClient(scripts)
    result = _qc_run(client)

    seat_requests = _requests_for(client, title)
    assert len(seat_requests) == 3
    turn = _reminder_turn(seat_requests[1])
    assert f"without a {QC_VERDICT_TOOL_NAME} call" in turn["content"][0]["text"]
    finding = next(f for f in result.findings if f.title == title)
    seats = sorted(finding.verdicts, key=lambda v: v.reviewer_index)
    assert [seat.status for seat in seats] == ["completed", "completed"]
    assert [seat.api_request_count for seat in seats] == [2, 1]
    assert [seat.model_response_count for seat in seats] == [2, 1]
    assert finding.verification_outcome == "upheld"


# The grouping request's own marker (see tests/test_continuation_cache.py).
_P1_BUCKET = "[[QC-CONSOLIDATE:element:pt1.a1.p1]]"


def test_a_grouping_call_that_paused_is_reminded() -> None:
    scripts = _two_bucket_scripts()
    scripts[_P1_BUCKET] = [
        pause_response(searched_urls=["https://example.test/group"]),
        _text_only(),
        singleton_consolidation_for(
            '<candidate index="0"></candidate><candidate index="1"></candidate>'
        ),
    ]
    client = SequencedFakeClient(scripts)
    result = _qc_run(client, store=_two_paragraph_store())

    assert result.consolidation.status == "complete"
    paused = _requests_for(client, _P1_BUCKET)
    assert len(paused) == 3
    roles = [m["role"] for m in paused[2]["messages"]]
    # Opening, the paused turn, the text-only continuation, the reminder.
    assert roles == ["user", "assistant", "assistant", "user"]
    text = _reminder_turn(paused[2])["content"][0]["text"]
    assert f"without a {QC_CONSOLIDATION_TOOL_NAME} call" in text


def test_a_warm_lead_is_reminded(monkeypatch) -> None:
    """A lead (cost Tier 1, Chunk 3) is an ordinary streamed seat."""
    monkeypatch.setattr(qc_engine, "_WARM_LEAD_MIN_SEATS_WEB", 8)
    monkeypatch.setattr(qc_engine, "_WARM_LEAD_MIN_SEATS_NO_WEB", 8)
    doc = _titles("Doc gap", 4)
    scripts = _lineage_scripts(doc=_medium(doc))
    # The lead streams before its batch exists, so it takes its title's
    # first turn. Its reminder and the batched second seat then take the two
    # verdicts in either order — they are identical, so the race is moot.
    scripts[doc[0]] = [
        _text_only(),
        qc_verdict_response(True),
        qc_verdict_response(True),
    ]

    class _Streamed(SequencedFakeClient):
        def __init__(self, scripts) -> None:
            super().__init__(scripts)
            self.streamed: list[dict] = []

        def stream(self, **request):
            self.streamed.append(
                {**request, "messages": list(request.get("messages") or [])}
            )
            return super().stream(**request)

    client = _Streamed(scripts)
    result = _qc_run(client, batch=True, lead=True, warm=45)

    # The grouping call quotes every title, so only verifier requests count.
    lead = [
        r
        for r in client.streamed
        if "[[QC-VERIFY:" in user_text(r["messages"])
        and doc[0] in user_text(r["messages"])
    ]
    assert len(lead) == 2
    text = _reminder_turn(lead[1])["content"][0]["text"]
    assert f"without a {QC_VERDICT_TOOL_NAME} call" in text
    finding = next(f for f in result.findings if f.title == doc[0])
    assert all(seat.status == "completed" for seat in finding.verdicts)


# ---------------------------------------------------------------------------
# The shared shape helper
# ---------------------------------------------------------------------------


def test_the_helper_answers_text_with_text_and_calls_with_results() -> None:
    def wrong(name: str) -> str:
        return f"wrong:{name}"

    text = SimpleNamespace(content=[text_block("done")])
    assert missing_output_tool_reply(text, reminder="R", wrong_tool=wrong) == {
        "role": "user",
        "content": [{"type": "text", "text": "R"}],
    }
    # Server-tool blocks never need an answer; only client calls do.
    mixed = {
        "content": [
            {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search"},
            {"type": "tool_use", "id": "toolu_a", "name": "made_up", "input": {}},
            tool_use_block("toolu_b", "also_made_up", {}),
        ]
    }
    assert missing_output_tool_reply(mixed, reminder="R", wrong_tool=wrong) == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "toolu_a",
                "is_error": True,
                "content": "wrong:made_up",
            },
            {
                "type": "tool_result",
                "tool_use_id": "toolu_b",
                "is_error": True,
                "content": "wrong:also_made_up",
            },
        ],
    }
    # Pure: the reply it read is unchanged.
    assert len(mixed["content"]) == 3


# ---------------------------------------------------------------------------
# P55-4.4 — the system-prompt lines, and F3
# ---------------------------------------------------------------------------


def _early_stop_asserts(prompt: str, tool: str) -> None:
    assert f"recorded only by the {tool} call" in prompt
    assert "A message without a tool call ends your turn and records nothing" in prompt
    assert "in the same message as your next tool call" in prompt
    assert f"end by calling {tool}" in prompt


def test_every_fan_out_system_prompt_names_the_early_stop() -> None:
    research = research_engine.build_research_system_prompt(RESEARCH_MODULE)
    research = " ".join(research.split())
    _early_stop_asserts(research, RESEARCH_TOOL_NAME)
    _early_stop_asserts(qc_engine._lens_system_prompt(QC_MODULE), QC_FINDINGS_TOOL_NAME)
    _early_stop_asserts(
        qc_engine._consolidation_system_prompt(QC_MODULE), QC_CONSOLIDATION_TOOL_NAME
    )
    _early_stop_asserts(
        qc_engine._verifier_system_prompt(QC_MODULE), QC_VERDICT_TOOL_NAME
    )
    # Each line sits before its call's tagged-JSON fallback, in <output>.
    for prompt in (
        research,
        qc_engine._lens_system_prompt(QC_MODULE),
        qc_engine._consolidation_system_prompt(QC_MODULE),
        qc_engine._verifier_system_prompt(QC_MODULE),
    ):
        output = prompt[prompt.index("<output>") :]
        assert output.index("records nothing") < output.index(
            "If you cannot call the tool"
        )


def test_the_lines_ride_the_cached_system_block_of_every_call(harness) -> None:
    """Each line is in the request's cached ``system`` block, so each cache
    lineage is written once more after the update — and nowhere else."""
    call = harness.run(_client(harness, [harness.final()]))
    system = call.requests[0]["system"]
    assert len(system) == 1 and "cache_control" in system[0]
    assert "records nothing" in system[0]["text"]
    assert "records nothing" not in json.dumps(call.requests[0]["messages"])


def test_a_retained_result_stays_current_across_the_new_lines(monkeypatch) -> None:
    """The plan's F3: the QC input manifest hashes lens briefs, never these
    system prompts, so the change leaves every retained result current.

    The staleness check rebuilds the manifest with the LIVE transport setting,
    so it is pinned to the transport this run used."""
    monkeypatch.setattr(settings, "QC_BATCH_VERIFICATION", False)
    store = _store()
    result = _qc_run(SequencedFakeClient(_qc_scripts()), store=store)
    manifest = json.dumps(result.input_manifest)
    assert "records nothing" not in manifest
    assert result.matches_inputs(store.index, store.doc, None, QC_MODULE)

    # The same review with the lines taken back out: identical inputs.
    monkeypatch.setattr(qc_engine, "_early_stop_line", lambda *_a: "")
    before = _qc_run(SequencedFakeClient(_qc_scripts()), store=store)
    assert before.input_fingerprint == result.input_fingerprint
    assert result.matches_inputs(store.index, store.doc, None, QC_MODULE)
