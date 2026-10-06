"""The P55-2 progress-update canary, checked without a network or a key.

``tools/prompt55_progress_update_canary.py --run`` is one of the paid
exceptions CLAUDE.md allows; only Abraham runs it. These tests never let it
reach a provider. They pin that it sends nothing without ``--run``; that
every request it sends is the one the production engine built, with only
``thinking.display``, the ``max_tokens`` cap and the beta changed, and only
through the beta endpoint; that its verdict reads scripted responses the way
the plan says (a pass, a question in a progress note, no closing text); and
that a 400 and a refusal are reported plainly, never retried without the
beta.
"""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from backend import settings
from backend.llm import conversation
from backend.llm.client import MissingApiKeyError
from tests.fakes import (
    FakeClient,
    bad_request,
    raw_turn,
    text_block,
    thinking_block,
    tool_use_block,
)
from tools import prompt55_progress_update_canary as canary

_CLOSING = (
    "I set the section header and drafted two PART 1 provisions, both "
    "stamped assumed. What design density should the data halls use? I "
    "recommend 0.20 gpm/sq ft over 1,500 sq ft."
)
_NOTE = "Setting the section header and drafting the first two provisions."
_CHIPS = '<suggested_replies>["Use your recommended density", "Use 0.30 gpm/sq ft"]</suggested_replies>'

_EDITS = {
    "edits": [
        {
            "action": "replace",
            "target_id": "sec",
            "text": "WET-PIPE SPRINKLER SYSTEMS",
            "numbering": "21 13 13",
        },
        {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"},
    ]
}


def _turn(*, note: str = _NOTE, closing: str = _CLOSING, chips: str = _CHIPS) -> list:
    """Two rounds: edits (with a progress note), then the closing text, which
    ends with its suggested-replies block (one round fewer than when the
    chips were a ``suggest_prompts`` call of their own)."""
    return [
        raw_turn(
            [thinking_block(note), tool_use_block("toolu_edit", "apply_spec_edits", _EDITS)],
            stop_reason="tool_use",
        ),
        raw_turn(
            [thinking_block(""), text_block(f"{closing}\n\n{chips}" if chips else closing)],
            stop_reason="end_turn",
        ),
    ]


def _beta_client(turns: list) -> tuple[SimpleNamespace, FakeClient]:
    """A client with ONLY a beta namespace: a request through
    ``client.messages`` would be an AttributeError, so every request the
    canary sends is provably a beta request."""
    fake = FakeClient(turns)
    return SimpleNamespace(beta=SimpleNamespace(messages=fake.messages)), fake


@pytest.fixture(autouse=True)
def _pinned_clock(monkeypatch):
    # The PROJECT CONTEXT opens with the date and time; pin it so two turns
    # built moments apart build the same request.
    monkeypatch.setattr(
        conversation,
        "date_context_block",
        lambda *_args, **_kwargs: "CURRENT DATE: pinned for the test.",
    )


def test_the_canary_sends_nothing_without_run(monkeypatch, capsys):
    def no_client():
        raise AssertionError("the canary built a client without --run")

    monkeypatch.setattr(canary, "get_client", no_client)
    assert canary.main([]) == 0
    assert "No request sent" in capsys.readouterr().out


def test_the_resent_request_differs_only_in_display_the_cap_and_the_beta():
    production = {
        "model": settings.INTERVIEW_MODEL,
        "max_tokens": 128_000,
        "system": [{"type": "text", "text": "stable"}],
        "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        "tools": [{"name": "apply_spec_edits"}],
        "thinking": {"type": "adaptive", "display": "summarized"},
        "output_config": {"effort": "medium"},
    }
    before = copy.deepcopy(production)
    sent = canary.progress_update_request(production, max_tokens=32_000)

    assert production == before  # the input is never mutated
    assert sent["thinking"] == {"type": "adaptive", "display": "updates"}
    assert sent["betas"] == ["thinking-display-updates-2026-08-18"]
    assert sent["max_tokens"] == 32_000
    rest = {k: v for k, v in sent.items() if k not in {"thinking", "betas", "max_tokens"}}
    assert rest == {
        k: v for k, v in production.items() if k not in {"thinking", "max_tokens"}
    }
    # The cap only ever lowers the ceiling.
    assert canary.progress_update_request(
        {**production, "max_tokens": 8_000}, max_tokens=32_000
    )["max_tokens"] == 8_000


def test_every_round_resends_exactly_what_the_production_engine_builds():
    """The same scripted turn, once through the plain engine and once through
    the canary: each round's recorded request IS the production request, and
    what went over the wire differs from it only as specified."""
    plain = FakeClient(_turn())
    session = conversation.SessionState()
    original = conversation.get_client
    conversation.get_client = lambda: plain
    try:
        for _event in conversation.stream_user_turn(
            session, canary.USER_MESSAGE, allow_compaction=False
        ):
            pass
    finally:
        conversation.get_client = original

    client, beta = _beta_client(_turn())
    wrapped = canary.run_turn(client, max_tokens=32_000)

    assert len(wrapped.rounds) == len(plain.messages.requests) == 2
    assert len(beta.messages.requests) == 2
    for round_, production, sent in zip(
        wrapped.rounds, plain.messages.requests, beta.messages.requests, strict=True
    ):
        assert round_.request == production
        assert sent == canary.progress_update_request(production, max_tokens=32_000)
        assert sent["thinking"]["display"] == "updates"
    assert conversation.get_client is original  # restored after the turn


def test_the_verdict_passes_a_reply_written_after_the_last_tool_call():
    client, _fake = _beta_client(_turn())
    wrapped = canary.run_turn(client, max_tokens=32_000)
    result = canary.verdict(wrapped.rounds)
    assert result.passed, result.reasons
    lines = canary.report(wrapped.rounds)
    assert f"  progress note [0]: {_NOTE}" in lines
    assert "Tool calls in order: apply_spec_edits." in lines
    assert "Suggested replies: 2 chip(s), at the very end." in lines
    assert not any(line.startswith("suggest_prompts:") for line in lines)


@pytest.mark.parametrize(
    ("chips", "summary"),
    [
        ("", "none in the closing message"),
        ('<suggested_replies>["Use it"', "a block that never closed (nothing staged)"),
        ("<suggested_replies>not json</suggested_replies>", "a block that did not validate"),
        ('<suggested_replies>["Use it"]</suggested_replies> And more.', "1 chip(s), with text after it"),
    ],
)
def test_the_report_says_what_the_block_held_without_judging_it(chips, summary):
    """The block is reported, never judged: the ordering verdict is the
    canary's subject, and a reply without chips still answers it."""
    client, _fake = _beta_client(_turn(chips=chips))
    wrapped = canary.run_turn(client, max_tokens=32_000)
    assert any(
        line.startswith(f"Suggested replies: {summary}")
        for line in canary.report(wrapped.rounds)
    )
    assert canary.verdict(wrapped.rounds).passed


def test_a_retired_suggest_prompts_call_is_reported():
    turns = _turn()
    turns.insert(
        1,
        raw_turn(
            [tool_use_block("toolu_chips", "suggest_prompts", {"prompts": ["Use it"]})],
            stop_reason="tool_use",
        ),
    )
    client, _fake = _beta_client(turns)
    lines = canary.report(canary.run_turn(client, max_tokens=32_000).rounds)
    assert "suggest_prompts: called (retired; it staged nothing)." in lines


def test_the_verdict_judges_the_closing_text_without_its_chip_block():
    """A question mark or length inside the block is not the reply asking
    anything: the chat never shows the block."""
    client, _fake = _beta_client(
        _turn(
            closing="Done.",
            chips='<suggested_replies>["' + "Why not the alternative layout?" * 3 + '"]</suggested_replies>',
        )
    )
    result = canary.verdict(canary.run_turn(client, max_tokens=32_000).rounds)
    assert not result.passed
    assert "The closing text is 5 characters, under 80." in result.reasons
    assert "The closing text asks no question." in result.reasons


def test_the_verdict_fails_a_question_in_a_progress_note():
    client, _fake = _beta_client(
        _turn(note="Which hazard classification applies to the data halls?")
    )
    result = canary.verdict(canary.run_turn(client, max_tokens=32_000).rounds)
    assert not result.passed
    assert any(
        "Round 1, block 0: a progress note asks a question" in reason
        for reason in result.reasons
    )


def test_the_verdict_fails_a_turn_with_no_closing_text():
    """The failure F1 describes: the questions arrive as a progress note and
    the turn ends without any text after the last tool call."""
    turns = _turn()
    turns[-1] = raw_turn(
        [thinking_block("What design density should the data halls use?")],
        stop_reason="end_turn",
    )
    client, _fake = _beta_client(turns)
    result = canary.verdict(canary.run_turn(client, max_tokens=32_000).rounds)
    assert not result.passed
    assert "The turn's last round ends with a 'thinking' block, not closing text." in (
        result.reasons
    )
    assert any("progress note asks a question" in r for r in result.reasons)


def test_the_verdict_fails_a_short_closing_or_one_that_asks_nothing():
    client, _fake = _beta_client(_turn(closing="Done."))
    result = canary.verdict(canary.run_turn(client, max_tokens=32_000).rounds)
    assert not result.passed
    assert "The closing text is 5 characters, under 80." in result.reasons
    assert "The closing text asks no question." in result.reasons


def test_the_verdict_fails_a_turn_that_did_not_end_normally():
    turns = _turn()
    turns[-1] = raw_turn([text_block(_CLOSING)], stop_reason="max_tokens")
    client, _fake = _beta_client(turns)
    result = canary.verdict(canary.run_turn(client, max_tokens=32_000).rounds)
    assert not result.passed
    assert "The turn's last round stopped on 'max_tokens', not end_turn." in (
        result.reasons
    )


class _RefusedOnEnter:
    """The real SDK sends the request when the stream context is ENTERED,
    so that is where a 400 surfaces (the fakes raise from ``stream()``)."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.requests: list[dict] = []

    def stream(self, **request):
        self.requests.append(request)
        exc = self._exc

        class _Manager:
            def __enter__(self):
                raise exc

            def __exit__(self, *args):
                return False

        return _Manager()


def test_a_request_refused_on_enter_is_recorded_and_not_resent(monkeypatch, capsys):
    messages = _RefusedOnEnter(bad_request("thinking-display-updates is not enabled"))
    client = SimpleNamespace(beta=SimpleNamespace(messages=messages))
    monkeypatch.setattr(canary, "get_client", lambda: client)
    assert canary.main(["--run"]) == 1
    assert "thinking-display-updates is not enabled" in capsys.readouterr().err
    assert len(messages.requests) == 1


class _BreaksMidStream:
    """A request that is accepted and then fails while streaming: nothing
    the canary's client sees fails, only the engine's turn does."""

    def __init__(self) -> None:
        self.requests: list[dict] = []

    def stream(self, **request):
        self.requests.append(request)

        class _Stream:
            def __iter__(self):
                raise RuntimeError("the connection dropped mid-stream")
                yield  # pragma: no cover - makes this a generator

        class _Manager:
            def __enter__(self):
                return _Stream()

            def __exit__(self, *args):
                return False

        return _Manager()


def test_a_turn_that_fails_after_its_request_is_reported(monkeypatch, capsys):
    client = SimpleNamespace(beta=SimpleNamespace(messages=_BreaksMidStream()))
    monkeypatch.setattr(canary, "get_client", lambda: client)
    assert canary.main(["--run"]) == 1
    err = capsys.readouterr().err
    assert "the turn failed" in err
    assert "the connection dropped mid-stream" in err


def test_a_stopped_stream_records_its_snapshot():
    """The engine reads ``current_message_snapshot`` instead of the final
    message when a turn is stopped; the canary records whichever it read."""
    snapshot = SimpleNamespace(content=[text_block("partial")], stop_reason=None)
    stream = SimpleNamespace(current_message_snapshot=snapshot)
    round_ = canary.Round(request={}, sent={})
    assert canary._RecordingStream(stream, round_).current_message_snapshot is snapshot
    assert round_.response is snapshot


def test_main_reports_a_pass(monkeypatch, capsys):
    client, _fake = _beta_client(_turn())
    monkeypatch.setattr(canary, "get_client", lambda: client)
    assert canary.main(["--run"]) == 0
    out = capsys.readouterr().out
    assert "Progress-update canary passed" in out
    assert _CLOSING in out


def test_main_reports_a_failed_verdict_nonzero(monkeypatch, capsys):
    client, _fake = _beta_client(_turn(closing="Done."))
    monkeypatch.setattr(canary, "get_client", lambda: client)
    assert canary.main(["--run"]) == 1
    assert "Progress-update canary FAILED" in capsys.readouterr().err


def test_a_400_is_reported_plainly_and_never_retried_without_the_beta(
    monkeypatch, capsys
):
    """The engine degrades a rejected thinking.display once by resending
    without it. The canary must not let that happen: it would run the turn
    without "updates" and report a result about nothing."""
    client, fake = _beta_client([bad_request("unknown thinking.display value")])
    monkeypatch.setattr(canary, "get_client", lambda: client)
    assert canary.main(["--run"]) == 1
    err = capsys.readouterr().err
    assert "a request failed" in err
    assert "unknown thinking.display value" in err
    assert len(fake.messages.requests) == 1


def test_a_refusal_is_reported_plainly(monkeypatch, capsys):
    client, _fake = _beta_client(
        [raw_turn([], stop_reason="refusal", refusal_category="cyber")]
    )
    monkeypatch.setattr(canary, "get_client", lambda: client)
    assert canary.main(["--run"]) == 1
    assert "The model declined the turn (category: cyber)." in capsys.readouterr().err


def test_a_missing_key_exits_2(monkeypatch, capsys):
    def missing():
        raise MissingApiKeyError("No API key is configured.")

    monkeypatch.setattr(canary, "get_client", missing)
    assert canary.main(["--run"]) == 2
    assert "No API key is configured." in capsys.readouterr().err


def test_the_output_ceiling_is_bounded(monkeypatch):
    def no_client():
        raise AssertionError("a request was built for an out-of-range ceiling")

    monkeypatch.setattr(canary, "get_client", no_client)
    assert canary.main(["--run", "--max-tokens", "100"]) == 2
    assert canary.main(["--run", "--max-tokens", "999999"]) == 2
