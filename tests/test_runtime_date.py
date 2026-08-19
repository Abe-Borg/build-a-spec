"""The app tells the model what day it is — on every surface that judges
edition currency, and without breaking the caches those surfaces depend on.

The bug this pins: a model has no clock, so an app that never states the
date leaves it answering "which edition is current?" from training data
that gets staler every month the build stays in the field. Codes revise on
multi-year cycles, so that is a wrong answer with a confident tone.

Two properties matter and only one of them is obvious. The obvious one is
that the date is present. The other is that each fan-out reads the clock
ONCE per run: the QC lens and verifier shared prefixes carry
``cache_control``, and a per-call clock read would fork their cache
lineages the moment a run crossed midnight — a regression that shows up
only as a bill that quietly failed to drop.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from backend import runtime_context, settings
from backend.app import create_app
from backend.compliance.checker import build_audit_user_message
from backend.qc.engine import run_final_qc
from backend.research import run_requirements_research
from backend.research.engine import RequirementsProfile
from backend.runtime_context import (
    DATE_AWARENESS_DIRECTIVE,
    current_date_iso,
    current_datetime,
    date_context_block,
)
from backend.spec_doc.model import SpecSection
from tests.fakes import (
    FakeClient,
    SequencedFakeClient,
    qc_findings_response,
    qc_verdict_response,
    request_context_text,
    research_response,
    text_turn,
)
from tests.test_qc import _LENS_KEYS, _finding, _qc_scripts, _section
from tests.test_research_engine import (
    DEFAULT_MODULE as FIRE_MODULE,
    DIM_KEYS,
    PROFILE,
)

_PINNED = datetime(2031, 3, 14, 15, 9)


# ---------------------------------------------------------------------------
# The helper
# ---------------------------------------------------------------------------


def test_the_stamp_is_the_local_wall_clock_and_timezone_aware():
    """Local, not UTC: this is a desktop app and "today" means the user's.

    A UTC reading is a day ahead for every user in the Americas each
    evening.
    """
    stamp = current_datetime()
    assert stamp.tzinfo is not None
    assert stamp.utcoffset() == datetime.now().astimezone().utcoffset()

    # A naive override reads as local time, which is what a caller passing a
    # bare datetime means.
    assert current_datetime(_PINNED).hour == 15
    assert current_date_iso(_PINNED) == "2031-03-14"

    # An aware override is converted, not reinterpreted.
    utc_evening = datetime(2031, 3, 14, 23, 30, tzinfo=timezone.utc)
    assert current_datetime(utc_evening).utcoffset() == stamp.utcoffset()


def test_the_block_states_the_date_and_what_to_do_about_the_gap():
    """The stamp alone would leave the model free to trust its own memory."""
    chat = date_context_block(_PINNED, with_time=True)
    assert "CURRENT DATE AND TIME:" in chat
    assert "Friday, 14 March 2031" in chat
    assert "2031-03-14" in chat
    assert "15:09" in chat
    assert DATE_AWARENESS_DIRECTIVE in chat

    # The fan-out variant is date-only: a clock time cannot change the
    # answer to a code-cycle question, and it is exactly the component that
    # would churn a cached prefix on every call.
    fanout = date_context_block(_PINNED)
    assert fanout.startswith("CURRENT DATE: Friday, 14 March 2031 (2031-03-14).")
    assert "15:09" not in fanout
    assert DATE_AWARENESS_DIRECTIVE in fanout


def test_the_zone_is_named_without_reading_redundantly():
    """The model should never have to guess which clock it is reading."""
    assert "local time" in date_context_block(_PINNED, with_time=True)
    # On a UTC host the name and the numeric offset say the same thing;
    # "local time, UTC, UTC+00:00" is noise.
    utc_stamp = datetime(2031, 3, 14, 15, 9, tzinfo=timezone.utc)
    assert runtime_context._zone_label(utc_stamp) == "local time, UTC"
    west = timezone(timedelta(hours=-7))
    assert "UTC-07:00" in runtime_context._zone_label(
        datetime(2031, 3, 14, 15, 9, tzinfo=west)
    )


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


def test_the_chat_turn_carries_the_date_and_the_cached_prompt_does_not(
    monkeypatch,
):
    """Dynamic context, never the stable block.

    The system prompt carries ``cache_control`` and must stay
    byte-identical for the whole session; a date there would rewrite it
    daily and violate the invariant the whole context architecture rests
    on. The PROJECT CONTEXT block is rebuilt per turn and stripped again at
    commit, so it costs nothing there.
    """
    fake = FakeClient([text_turn(["ok"])])
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)
    client = TestClient(create_app())
    client.post("/api/chat", json={"message": "hello"})

    request = fake.messages.last_request
    context = request_context_text(request)
    assert "CURRENT DATE AND TIME:" in context
    assert current_date_iso() in context
    assert DATE_AWARENESS_DIRECTIVE in context
    # First, because everything under it is dated — the editions in effect,
    # the research profile's as-of stamps, the lint report.
    assert context.index("CURRENT DATE AND TIME:") < context.index(
        "PROJECT IDENTITY"
    )

    stable = request["system"][0]
    assert stable["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert "CURRENT DATE" not in stable["text"]
    assert current_date_iso() not in stable["text"]


def test_the_context_block_does_not_fossilize_the_date_into_history(
    monkeypatch,
):
    """A stale date in history would contradict the current one.

    The commit-time strip already covers this for every other context
    fact; assert it for the date specifically, because it is the one whose
    staleness the model has no other way to detect.
    """
    fake = FakeClient([text_turn(["one"]), text_turn(["two"])])
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)
    client = TestClient(create_app())
    client.post("/api/chat", json={"message": "first"})
    client.post("/api/chat", json={"message": "second"})

    request = fake.messages.last_request
    stamps = [
        block.get("text", "")
        for message in request["messages"]
        for block in (
            message["content"]
            if isinstance(message.get("content"), list)
            else []
        )
        if isinstance(block, dict) and "CURRENT DATE" in block.get("text", "")
    ]
    assert len(stamps) == 1


# ---------------------------------------------------------------------------
# Research
# ---------------------------------------------------------------------------


def _research_scripts() -> dict[str, list]:
    return {
        key: [research_response(items=[], searched_urls=["https://x.gov"])]
        for key in DIM_KEYS.values()
    }


def test_every_research_dimension_is_told_the_same_date_it_stamps(monkeypatch):
    """One clock reading per round, threaded to all four workers.

    Research exists to establish what a jurisdiction has adopted *now*, so
    a worker that does not know the date is checking currency against its
    training data. The counter clock is the point of the test: it hands out
    a different day on every call, so any per-worker read shows up as four
    disagreeing dates, and a round that started at 23:59 can never file its
    findings under a day its workers were never told about.
    """
    calls: list[int] = []

    def _advancing_clock(now=None):
        calls.append(1)
        return datetime(2031, 3, 14) + timedelta(days=len(calls) - 1)

    monkeypatch.setattr(
        "backend.research.engine.current_datetime", _advancing_clock
    )

    fake = SequencedFakeClient(_research_scripts())
    profile = run_requirements_research(
        FIRE_MODULE, PROFILE, fake, model="claude-sonnet-5", max_tokens=4096
    )

    assert len(calls) == 1, "the round must read the clock exactly once"
    assert profile.research_date == "2031-03-14"

    # Block 0 is the cached shared half of the user turn (see
    # ``engine._dimension_user_content``). The date has to lead THAT block,
    # not merely appear somewhere in the message: it is part of the prefix
    # every continuation re-reads from cache.
    shared_blocks = [
        request["messages"][0]["content"][0]["text"] for request in fake.requests
    ]
    assert len(shared_blocks) == len(DIM_KEYS)
    for message in shared_blocks:
        assert message.startswith("CURRENT DATE: Friday, 14 March 2031")
        assert DATE_AWARENESS_DIRECTIVE in message
        # The project header still follows it, unchanged.
        assert "Project: Ashburn, Virginia" in message


# ---------------------------------------------------------------------------
# Final QC
# ---------------------------------------------------------------------------


def _qc_run(client, store, **kwargs):
    return run_final_qc(
        store.doc,
        None,
        FIRE_MODULE,
        client,
        model=settings.QC_MODEL,
        max_tokens=4096,
        version_index=0,
        started_at="2031-03-14T10:00:00+00:00",
        finished_at="2031-03-14T10:05:00+00:00",
        **kwargs,
    )


def _qc_client_with_one_finding() -> SequencedFakeClient:
    scripts = _qc_scripts(
        code_compliance=[
            qc_findings_response(
                "code_compliance",
                findings=[_finding("A finding", "issue", severity="medium")],
            )
        ],
    )
    scripts["A finding"] = [qc_verdict_response(True), qc_verdict_response(True)]
    return SequencedFakeClient(scripts)


def test_one_qc_run_shares_one_date_so_the_cached_prefix_still_holds(
    monkeypatch,
):
    """The load-bearing test for the v1.8.0 cost work.

    Both shared prefixes carry ``cache_control`` and are matched as strict
    byte prefixes. Reading the clock inside the prefix builders would look
    correct in review and pass every same-day test, then silently fork both
    lineages on any run that crossed midnight. The counter clock makes that
    refactor fail here instead of on a bill.
    """
    reads: list[str] = []

    def _advancing_block(now=None, *, with_time=False):
        reads.append(f"CURRENT DATE: day-{len(reads) + 1}")
        return reads[-1]

    monkeypatch.setattr("backend.qc.engine.date_context_block", _advancing_block)

    fake = _qc_client_with_one_finding()
    result = _qc_run(fake, _section())
    assert result.execution_status == "complete"

    assert len(reads) == 1, "the run must read the clock exactly once"

    lens_prefixes, verifier_prefixes = [], []
    for request in fake.requests:
        blocks = request["messages"][0]["content"]
        prefix, suffix = blocks[0], blocks[1]
        assert prefix["cache_control"]["type"] == "ephemeral"
        target = (
            verifier_prefixes
            if "[[QC-VERIFY:" in suffix["text"]
            else lens_prefixes
        )
        target.append(prefix["text"])

    assert len(lens_prefixes) == len(_LENS_KEYS)
    assert len(verifier_prefixes) == 2
    # Every call in the run — both phases — saw the same day, and it leads
    # the prefix so the cached bytes begin with it.
    for prefix in lens_prefixes + verifier_prefixes:
        assert prefix.startswith("<current_date>\nCURRENT DATE: day-1\n</current_date>")


def test_a_qc_review_carries_the_real_date_through_both_phases():
    """Without the counter clock: the real block reaches lenses and seats."""
    fake = _qc_client_with_one_finding()
    _qc_run(fake, _section())

    today = current_date_iso()
    assert fake.requests
    for request in fake.requests:
        prefix = request["messages"][0]["content"][0]["text"]
        assert today in prefix
        assert DATE_AWARENESS_DIRECTIVE in prefix
        # Date only — a per-call timestamp would miss the cache every time.
        assert "local time" not in prefix


def test_the_run_date_is_recorded_but_not_hashed_into_the_input_manifest(
    monkeypatch,
):
    """Deliberate: a retained review must not go stale at every midnight.

    The manifest fingerprint is what flips a saved result to "re-run me",
    and Final QC costs real money — so the date the reviewers were given is
    recorded on the result and left out of the fingerprint. Two runs a day
    apart on an unchanged document stay mutually current.
    """
    clocks = iter([datetime(2031, 3, 14), datetime(2031, 3, 15)])
    monkeypatch.setattr(
        "backend.qc.engine.current_datetime", lambda now=None: next(clocks)
    )

    first = _qc_run(_qc_client_with_one_finding(), _section())
    second = _qc_run(_qc_client_with_one_finding(), _section())

    assert first.context_date == "2031-03-14"
    assert second.context_date == "2031-03-15"
    assert first.input_fingerprint == second.input_fingerprint


def test_the_recorded_date_is_the_one_the_reviewers_were_actually_given():
    """``started_at`` cannot stand in for it — different clocks.

    ``started_at`` is a UTC audit timestamp; the reviewers get the user's
    LOCAL date. West of UTC in the evening those are different calendar
    days, so a report that only carried ``started_at`` could not
    reconstruct the date the edition-currency judgements were made against.
    One reading feeds both the prompt and the record, so they cannot drift.
    """
    fake = _qc_client_with_one_finding()
    result = _qc_run(fake, _section())

    assert result.context_date == current_date_iso()
    for request in fake.requests:
        prefix = request["messages"][0]["content"][0]["text"]
        assert result.context_date in prefix

    # Survives the project round-trip, and an older record reads as absent
    # rather than as a defaulted-to-today claim it cannot support.
    from backend.qc.engine import QCResult

    assert QCResult.from_dict(result.to_dict()).context_date == result.context_date
    legacy = result.to_dict()
    legacy.pop("context_date")
    assert QCResult.from_dict(legacy).context_date == ""


# ---------------------------------------------------------------------------
# The deprecated audit path
# ---------------------------------------------------------------------------


def test_the_deprecated_audit_is_not_the_one_surface_left_guessing():
    """Superseded by the QC lenses, but still reachable — so still told."""
    message = build_audit_user_message(
        SpecSection(), RequirementsProfile(), discipline="Plumbing"
    )
    assert "<current_date>" in message
    assert current_date_iso() in message
    assert DATE_AWARENESS_DIRECTIVE in message
    assert message.index("<current_date>") < message.index("<project_discipline>")
