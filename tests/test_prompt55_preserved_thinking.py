"""Thinking stays valid when the harness edits a request (5.5 prompting upgrade, P55-6).

Both 5.5 models bind each thinking block to the conversation prefix that
produced it ("preserved thinking"). On an account created on or after
2026-08-31 — and this app is bring-your-own-key, so the USER's account age
decides — a request that replays a block after the harness edited an earlier
part of that prefix is a 400. The one mid-conversation edit the app makes is
the resend sanitizer's (a fetched PDF over the page limit elided, an unpaired
server-tool call dropped), and in the chat the citation repair after it.

So a request the harness edited carries ``thinking.block_binding``
``prefix_mismatch_behavior: "drop_block"`` and the
``thinking-binding-controls-2026-08-01`` beta, and the API drops the
invalidated blocks instead of failing. A request the harness did not edit is
byte-identical to what it always was. The chat decides per request (it
re-sanitizes the raw history every round); research and streamed Final QC
keep a per-conversation flag, set by the first edit and cleared by a restart,
and run ONE assertion set over both engines (the ``test_retry_resume.py``
precedent). The batched transport never carries it: the Batches API's unset
default already drops such a block.

Also: the chat's ``thinking.display`` probe degrades only on a 400 that names
``display`` (a rejected ``block_binding`` must not silence the summaries);
CT-1's tail guard never latches on a ``block_binding`` rejection; and a
non-empty ``input_transformations`` is logged as counts, never text.

The PDF in these tests is unreadable (``"not-a-pdf"``), which the sanitizer
cannot count and therefore always elides — a deterministic edit without
touching its page limit.
"""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import anthropic
import pytest
from fastapi.testclient import TestClient

from backend import cost_checks, settings
from backend.app import create_app
from backend.llm import conversation
from backend.qc import engine as qc_engine
from backend.research import engine as research_engine
from backend.research.schema import (
    PRESERVED_THINKING_BETA,
    input_transformation_counts,
    with_beta_header,
    with_drop_block,
)
from tests.fakes import (
    FakeClient,
    bad_request,
    qc_verdict_response,
    raw_turn,
    research_response,
    text_block,
    text_turn,
    thinking_block,
)
from tests.test_qc_batch_verification import _one_finding_scripts
from tests.test_qc_batch_verification import _run as _run_batched_qc
from tests.test_retry_resume import _RESET, _QcHarness, _ResearchHarness
from tools import prompt55_progress_update_canary as canary

_DROP = {"prefix_mismatch_behavior": "drop_block"}
_BETA_HEADERS = {"anthropic-beta": PRESERVED_THINKING_BETA}
_PDF_URL = "https://example.gov/code-book.pdf"
_NOT_A_PDF = "bm90LWEtcGRm"  # base64 of "not-a-pdf": un-countable, always elided
_BINDING_400 = (
    "messages.1.content.3: Invalid `signature` in `thinking` block. The block "
    "is bound to a different conversation."
)
# What a request carried before this session, whatever it is otherwise
# (the chat, research and Final QC requests share the one key set).
_REQUEST_KEYS = {
    "model",
    "max_tokens",
    "system",
    "messages",
    "tools",
    "thinking",
    "output_config",
}


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    """No real seconds: both engines read the one ``time`` module."""
    monkeypatch.setattr(research_engine.time, "sleep", lambda _seconds: None)


def _pdf_fetch(tool_use_id: str = "srvtoolu_pdf_fetch") -> list:
    """A finished ``web_fetch`` of a PDF: the call, then its result.

    The result is a plain dict, as a history block is, so the sanitizer can
    elide its source in place.
    """
    return [
        SimpleNamespace(
            type="server_tool_use",
            id=tool_use_id,
            name="web_fetch",
            input={"url": _PDF_URL},
        ),
        {
            "type": "web_fetch_tool_result",
            "tool_use_id": tool_use_id,
            "content": {
                "type": "web_fetch_result",
                "url": _PDF_URL,
                "content": {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": _NOT_A_PDF,
                    },
                },
            },
        },
    ]


def _carries_drop_block(request: dict) -> bool:
    thinking = request.get("thinking") or {}
    headers = request.get("extra_headers") or {}
    return (
        thinking.get("block_binding") == _DROP
        and PRESERVED_THINKING_BETA
        in str(headers.get("anthropic-beta", "")).split(",")
    )


def _untouched(request: dict, keys: set[str], thinking: dict) -> bool:
    """The request as it was before this session: its keys (a container or
    a tail aside), and the thinking config exactly."""
    extra = set(request) - keys - {"container", "cache_control"}
    return not extra and request["thinking"] == thinking


def _pdf_elided(request: dict) -> bool:
    return _NOT_A_PDF not in json.dumps(request["messages"], default=str)


# ---------------------------------------------------------------------------
# The shared helpers
# ---------------------------------------------------------------------------


def test_drop_block_builds_new_dicts_and_leaves_the_request_alone():
    shared_thinking = {"type": "adaptive", "display": "summarized"}
    request = {"model": "m", "thinking": shared_thinking, "messages": []}
    sent = with_drop_block(request)
    assert sent["thinking"] == {
        "type": "adaptive",
        "display": "summarized",
        "block_binding": _DROP,
    }
    assert sent["extra_headers"] == _BETA_HEADERS
    # Nothing the caller reuses for its next request was touched.
    assert request == {"model": "m", "thinking": shared_thinking, "messages": []}
    assert shared_thinking == {"type": "adaptive", "display": "summarized"}
    assert sent["messages"] is request["messages"]


def test_the_beta_merges_with_one_already_there_and_never_twice():
    assert with_beta_header(None, "b") == {"anthropic-beta": "b"}
    merged = with_beta_header({"Anthropic-Beta": "a, c", "x-other": "1"}, "b")
    assert merged == {"x-other": "1", "anthropic-beta": "a,c,b"}
    assert with_beta_header({"anthropic-beta": "b"}, "b") == {"anthropic-beta": "b"}
    # A request that already carries another feature's beta keeps it.
    sent = with_drop_block({"thinking": {"type": "adaptive"}, "extra_headers": {"anthropic-beta": "server-side-fallback-2026-07-01"}})
    assert sent["extra_headers"] == {
        "anthropic-beta": f"server-side-fallback-2026-07-01,{PRESERVED_THINKING_BETA}"
    }


def test_input_transformations_are_counted_by_type_and_reason_only():
    entries = [
        {"type": "thinking_dropped", "path": "messages.1.content.0", "reason": "prefix_binding_mismatch"},
        SimpleNamespace(type="thinking_dropped", path="messages.3.content.0", reason="prefix_binding_mismatch"),
        {"type": "thinking_dropped", "reason": "model_binding_mismatch"},
        {"type": "Some Text the API never sends", "reason": None},
    ]
    counts = input_transformation_counts(SimpleNamespace(input_transformations=entries))
    assert counts == {
        "thinking_dropped/prefix_binding_mismatch": 2,
        "thinking_dropped/model_binding_mismatch": 1,
        "other/other": 1,
    }
    # A response already turned into a dict reads the same way.
    assert input_transformation_counts({"input_transformations": entries}) == counts
    # Absent, empty or malformed: nothing.
    assert input_transformation_counts(SimpleNamespace()) == {}
    assert input_transformation_counts({}) == {}
    assert input_transformation_counts(SimpleNamespace(input_transformations=[])) == {}
    assert input_transformation_counts(SimpleNamespace(input_transformations="x")) == {}


# ---------------------------------------------------------------------------
# The chat
# ---------------------------------------------------------------------------


def _client() -> TestClient:
    return TestClient(create_app())


def _parse_sse(body: str) -> list[dict]:
    return [
        json.loads(line[len("data: "):])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


def _chat(monkeypatch, turns: list, message: str = "check the code book") -> tuple[FakeClient, list[dict]]:
    fake = FakeClient(turns)
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)
    events = _parse_sse(_client().post("/api/chat", json={"message": message}).text)
    return fake, events


def test_every_chat_request_the_sanitizer_edited_carries_drop_block_and_the_beta(
    monkeypatch,
):
    """The turn fetches a PDF, pauses, pauses again, then answers. From the
    first request that re-sends the PDF on, the sanitizer elides it — and the
    chat re-sanitizes the raw history every round, so every one of those
    requests carries ``drop_block`` and the beta. The first request, which
    held nothing to edit, is exactly what it always was."""
    fake, events = _chat(
        monkeypatch,
        [
            raw_turn(
                [thinking_block("Fetching the code book."), *_pdf_fetch()],
                stop_reason="pause_turn",
            ),
            raw_turn([thinking_block("Still reading.")], stop_reason="pause_turn"),
            text_turn(["Checked against the code book."]),
        ],
    )
    assert events[-1]["type"] == "turn_complete"
    first, *edited = fake.messages.requests
    assert len(edited) == 2
    assert _untouched(first, _REQUEST_KEYS, conversation._thinking_param())
    assert "extra_headers" not in first
    for request in edited:
        assert _pdf_elided(request)
        assert _carries_drop_block(request)
        assert request["thinking"] == {**conversation._thinking_param(), "block_binding": _DROP}
        assert request["extra_headers"] == _BETA_HEADERS
        # Everything else is the request as it always was.
        for key in ("model", "max_tokens", "system", "tools", "output_config"):
            assert request[key] == first[key]


def test_a_chat_turn_the_harness_never_edits_is_byte_identical(monkeypatch):
    """No PDF, no edit: every request has exactly the keys it always had,
    the thinking config exactly, and neither the field nor the beta anywhere."""
    fake, events = _chat(
        monkeypatch,
        [
            raw_turn([thinking_block("Looking it up.")], stop_reason="pause_turn"),
            text_turn(["Done."]),
        ],
    )
    assert events[-1]["type"] == "turn_complete"
    assert len(fake.messages.requests) == 2
    for request in fake.messages.requests:
        assert _untouched(request, _REQUEST_KEYS, conversation._thinking_param())
        dumped = json.dumps(request, default=str)
        assert "block_binding" not in dumped
        assert PRESERVED_THINKING_BETA not in dumped


def test_the_builder_marks_only_a_request_whose_messages_changed():
    """``_build_chat_request`` directly: the same inputs with and without an
    un-countable PDF in this turn's history."""
    edited_turn = [
        {"role": "user", "content": [{"type": "text", "text": "check it"}]},
        {"role": "assistant", "content": conversation._content_blocks_to_dicts(_pdf_fetch())},
    ]
    plain_turn = [
        {"role": "user", "content": [{"type": "text", "text": "check it"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
    ]

    def inputs(turn):
        return conversation._ChatRequestInputs(
            history=[],
            new_messages=turn,
            module=conversation.SessionState().module,
            model="claude-sonnet-5-5",
            max_tokens=4096,
            effort=settings.INTERVIEW_EFFORT,
        )

    plain = conversation._build_chat_request(inputs(plain_turn))
    edited = conversation._build_chat_request(inputs(edited_turn))
    assert set(plain) == _REQUEST_KEYS
    assert plain["thinking"] == conversation._thinking_param()
    assert _carries_drop_block(edited)
    assert set(edited) == _REQUEST_KEYS | {"extra_headers"}


# ---------------------------------------------------------------------------
# The display probe
# ---------------------------------------------------------------------------


def _probe_request(**extra) -> dict:
    return {
        "model": "claude-sonnet-5-5",
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": "hi"}],
        "thinking": {"type": "adaptive", "display": "summarized"},
        **extra,
    }


def test_a_display_worded_400_still_degrades_and_keeps_block_binding():
    request = with_drop_block(_probe_request())
    fake = FakeClient([bad_request("thinking.display: unsupported"), text_turn(["ok"])])
    conversation._enter_stream(fake, request)
    rejected, retried = fake.messages.requests
    assert rejected["thinking"]["display"] == "summarized"
    # The resend drops display and nothing else.
    assert retried["thinking"] == {"type": "adaptive", "block_binding": _DROP}
    assert retried["extra_headers"] == _BETA_HEADERS
    assert conversation._display_probe_disabled is True


@pytest.mark.parametrize(
    "message",
    [
        _BINDING_400,
        "thinking.block_binding: Extra inputs are not permitted",
        "messages.2: something else about the request",
    ],
    ids=["binding-mismatch", "binding-without-beta", "unrelated"],
)
def test_any_other_400_is_raised_and_the_summaries_stay_on(message):
    """A 400 that does not name ``display`` is not the display key's: it is
    raised as it came, sent once, and the probe stays armed."""
    request = with_drop_block(_probe_request())
    fake = FakeClient([bad_request(message), text_turn(["never sent"])])
    with pytest.raises(anthropic.BadRequestError) as caught:
        conversation._enter_stream(fake, request)
    assert message in str(caught.value)
    assert len(fake.messages.requests) == 1
    assert conversation._display_probe_disabled is False
    assert conversation._thinking_param() == {"type": "adaptive", "display": "summarized"}


def test_a_prompt_too_long_400_is_still_not_a_display_rejection():
    fake = FakeClient(
        [bad_request("prompt is too long: 1204112 tokens > 1000000 maximum")]
    )
    with pytest.raises(anthropic.BadRequestError):
        conversation._enter_stream(fake, _probe_request())
    assert len(fake.messages.requests) == 1
    assert conversation._display_probe_disabled is False


def test_a_rejected_binding_fails_the_turn_without_silencing_the_next_one(monkeypatch):
    """End to end: the edited continuation is refused over its binding. The
    turn fails (retry-safe, as every failure is) and the NEXT turn still
    asks for the reasoning summary."""
    fake, events = _chat(
        monkeypatch,
        [
            raw_turn([thinking_block("Fetching."), *_pdf_fetch()], stop_reason="pause_turn"),
            bad_request(_BINDING_400),
        ],
    )
    assert events[-1]["type"] == "error"
    assert len(fake.messages.requests) == 2
    assert _carries_drop_block(fake.messages.requests[1])
    assert conversation._display_probe_disabled is False
    fake2, events2 = _chat(monkeypatch, [text_turn(["Fine."])], message="again")
    assert events2[-1]["type"] == "turn_complete"
    assert fake2.messages.requests[0]["thinking"] == {"type": "adaptive", "display": "summarized"}


# ---------------------------------------------------------------------------
# input_transformations, logged as counts
# ---------------------------------------------------------------------------


_DROPPED = [
    {"type": "thinking_dropped", "path": "messages.1.content.4", "reason": "prefix_binding_mismatch"},
    {"type": "thinking_dropped", "path": "messages.2.content.0", "reason": "prefix_binding_mismatch"},
]


def test_the_chat_logs_what_the_api_dropped_without_any_path_or_text(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="buildaspec.chat")
    _fake, events = _chat(
        monkeypatch,
        [
            raw_turn([thinking_block("Fetching."), *_pdf_fetch()], stop_reason="pause_turn", input_transformations=[]),
            raw_turn([text_block("The secret reply text.")], stop_reason="end_turn", input_transformations=_DROPPED),
        ],
    )
    assert events[-1]["type"] == "turn_complete"
    lines = [r.getMessage() for r in caplog.records if r.name == "buildaspec.chat"]
    assert lines == [
        "Chat round 1: the API transformed the request's input "
        "(thinking_dropped/prefix_binding_mismatch=2)."
    ]
    assert "messages." not in lines[0] and "secret" not in lines[0]


def test_a_chat_turn_without_the_array_logs_nothing(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="buildaspec.chat")
    _chat(monkeypatch, [text_turn(["Done."])])
    assert not [r for r in caplog.records if r.name == "buildaspec.chat"]


# ---------------------------------------------------------------------------
# Research and streamed Final QC: ONE assertion set over both engines
# ---------------------------------------------------------------------------


@pytest.fixture(params=[_ResearchHarness(), _QcHarness()], ids=["research", "qc"])
def harness(request):
    return request.param


def _pause(*, pdf: bool = False):
    """A paused turn — optionally one that fetched a PDF — on either engine."""
    extra = [thinking_block("Reading.")]
    if pdf:
        extra.extend(_pdf_fetch())
    return research_response(items=None, extra_blocks=extra, stop_reason="pause_turn")


def _engine_thinking() -> dict:
    return {"type": "adaptive"}


def test_the_edit_marks_that_request_and_every_later_one(harness):
    """Opening request: untouched. The pause that fetched a PDF is sanitized
    before the next request, so that continuation carries ``drop_block`` —
    and so does every later request of the conversation, including one whose
    own round edited nothing."""
    call = harness.run([_pause(pdf=True), _pause(), harness.final()])
    assert call.status == "completed"
    first, *later = call.requests
    assert len(later) == 2
    assert _untouched(first, _REQUEST_KEYS, _engine_thinking())
    assert "extra_headers" not in first
    for request in later:
        assert _pdf_elided(request)
        assert _carries_drop_block(request)
        assert request["thinking"] == {"type": "adaptive", "block_binding": _DROP}
        assert request["extra_headers"] == _BETA_HEADERS
        for key in ("model", "max_tokens", "system", "tools", "output_config"):
            assert request[key] == first[key]


def test_a_conversation_the_sanitizer_never_edits_is_byte_identical(harness):
    call = harness.run([_pause(), _pause(), harness.final()])
    assert call.status == "completed"
    assert len(call.requests) == 3
    for request in call.requests:
        assert _untouched(request, _REQUEST_KEYS, _engine_thinking())
        dumped = json.dumps(request, default=str)
        assert "block_binding" not in dumped
        assert PRESERVED_THINKING_BETA not in dumped


def test_a_resume_keeps_the_flag_and_a_restart_clears_it(harness):
    """Edited conversation, then a dropped connection on the continuation:
    the first retry RESUMES (the same request, still carrying it); the next
    one — the final attempt — RESTARTS, a new conversation with nothing
    edited, so its opening request carries none of it."""
    call = harness.run([_pause(pdf=True), _RESET, _RESET, harness.final()])
    assert call.status == "completed"
    assert [event["mode"] for event in call.retries] == ["resume", "restart"]
    opening, failed, resumed, restarted = call.requests
    assert not _carries_drop_block(opening)
    assert _carries_drop_block(failed) and _carries_drop_block(resumed)
    assert resumed["messages"] == failed["messages"]
    assert _untouched(restarted, _REQUEST_KEYS, _engine_thinking())
    assert "extra_headers" not in restarted


def test_a_reminder_the_sanitizer_edited_carries_it_too(harness):
    """A text-only end of turn that fetched a PDF is reminded (P55-4); the
    reminder's sanitizer edit marks the reminder request."""
    reply = research_response(
        items=None,
        extra_blocks=[thinking_block("Done reading."), *_pdf_fetch(), text_block("I have my findings.")],
        stop_reason="end_turn",
    )
    call = harness.run([reply, harness.final()])
    assert call.status == "completed"
    opening, reminder = call.requests
    assert not _carries_drop_block(opening)
    assert reminder["messages"][-1]["role"] == "user"
    assert _pdf_elided(reminder)
    assert _carries_drop_block(reminder)


def test_the_tail_and_the_binding_ride_the_same_continuation(harness):
    call = harness.run(
        [_pause(pdf=True), _pause(), harness.final()], continuation_cache=True
    )
    assert call.status == "completed"
    for request in call.requests[1:]:
        assert request["cache_control"] == {"type": "ephemeral"}
        assert _carries_drop_block(request)


def test_ct1_never_latches_on_a_binding_rejection(harness):
    """The edited continuation carries the tail and the binding. Refused
    with a 400, it is resent without the tail — still carrying the binding —
    and refused again: a 400 that survives removing the tail is not the
    tail's, so the tail stays on."""
    engine = harness.engine._TAIL_ENGINE
    call = harness.run(
        [_pause(pdf=True), bad_request(_BINDING_400), bad_request(_BINDING_400)],
        continuation_cache=True,
    )
    assert call.status == "failed"
    assert _BINDING_400 in call.error
    _opening, refused, resent = call.requests
    assert "cache_control" in refused and "cache_control" not in resent
    assert _carries_drop_block(refused) and _carries_drop_block(resent)
    assert resent["messages"] == refused["messages"]
    assert cost_checks.continuation_tail_enabled(engine) is True


def test_what_the_api_dropped_is_logged_with_the_calls_id_and_counts_only(harness, caplog):
    logger = {"research": "buildaspec.research", "qc": "buildaspec.qc"}[harness.name]
    caplog.set_level(logging.INFO, logger=logger)
    final = harness.final()
    final.input_transformations = _DROPPED
    call = harness.run([_pause(pdf=True), final])
    assert call.status == "completed"
    lines = [
        r.getMessage()
        for r in caplog.records
        if r.name == logger and "transformed" in r.getMessage()
    ]
    assert len(lines) == 1
    assert lines[0].endswith(
        "the API transformed the request's input "
        "(thinking_dropped/prefix_binding_mismatch=2)."
    )
    ident = {"research": "governing_codes", "qc": "lens_id=code_compliance"}[harness.name]
    assert ident in lines[0]
    assert "messages." not in lines[0]


def test_a_response_without_the_array_logs_nothing(harness, caplog):
    logger = {"research": "buildaspec.research", "qc": "buildaspec.qc"}[harness.name]
    caplog.set_level(logging.INFO, logger=logger)
    harness.run([_pause(), harness.final()])
    assert not [r for r in caplog.records if "transformed" in r.getMessage()]


# ---------------------------------------------------------------------------
# The batched transport never carries it
# ---------------------------------------------------------------------------


def test_batched_params_never_carry_it_even_after_an_edit():
    """A batched seat that fetched a PDF and paused is sanitized before its
    next round, and its round-2 params still carry neither the field nor the
    beta: the Batches API's unset default drops an invalidated block
    instead of failing the item."""
    from tests.fakes import SequencedFakeClient

    scripts = _one_finding_scripts(
        verdicts=[
            research_response(
                items=None,
                extra_blocks=[thinking_block("Checking."), *_pdf_fetch()],
                stop_reason="pause_turn",
            ),
            qc_verdict_response(True),
            qc_verdict_response(True),
        ]
    )
    client = SequencedFakeClient(scripts)
    result = _run_batched_qc(client)
    assert result.execution_status == "complete"
    rounds = client.batches.created
    assert len(rounds) == 2
    params = [request["params"] for batch in rounds for request in batch]
    (resumed,) = [request["params"] for request in rounds[1]]
    assert _pdf_elided(resumed)
    for sent in params:
        assert "extra_headers" not in sent
        assert "block_binding" not in sent["thinking"]
        assert PRESERVED_THINKING_BETA not in json.dumps(sent, default=str)


def test_the_one_request_shape_carries_nothing_of_it():
    kwargs = qc_engine._qc_request_kwargs(
        system_prompt="s",
        tools=[{"name": "t", "input_schema": {}}],
        model=settings.QC_MODEL,
        max_tokens=10,
        effort="medium",
        cache_ttl="1h",
    )
    assert kwargs["thinking"] == {"type": "adaptive"}
    assert "extra_headers" not in kwargs


# ---------------------------------------------------------------------------
# The progress-update canary keeps both betas
# ---------------------------------------------------------------------------


def test_the_canary_folds_a_production_beta_into_betas():
    """The SDK lets ``extra_headers`` override the header ``betas`` builds,
    so a production request that carries the preserved-thinking beta has it
    moved into ``betas`` beside the progress-update one; the header sent is
    the same, and neither beta is lost."""
    production = with_drop_block(
        {"model": "m", "max_tokens": 128_000, "thinking": {"type": "adaptive", "display": "summarized"}}
    )
    production["extra_headers"] = {**production["extra_headers"], "x-keep": "1"}
    sent = canary.progress_update_request(production, max_tokens=32_000)
    assert sent["betas"] == [canary.BETA, PRESERVED_THINKING_BETA]
    assert sent["extra_headers"] == {"x-keep": "1"}
    assert sent["thinking"] == {"type": "adaptive", "display": "updates", "block_binding": _DROP}
    # A header holding only the beta goes away entirely.
    only_beta = with_drop_block({"model": "m", "max_tokens": 10})
    assert "extra_headers" not in canary.progress_update_request(only_beta, max_tokens=4096)
    # An unedited production request is re-shaped exactly as before.
    plain = canary.progress_update_request({"model": "m", "max_tokens": 10}, max_tokens=4096)
    assert plain["betas"] == [canary.BETA] and "extra_headers" not in plain
