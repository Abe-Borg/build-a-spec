"""The Review Room's click-through: what a lens or reviewer seat is weighing.

Owner request (Abraham, 2026-10-08): click a lens card or a reviewer seat and
see what that agent is up to. A card that only ever says "Thinking through
the specification…" cannot answer that, because Opus 5.5 and Sonnet 5.5
stream thinking blocks with EMPTY text unless the request asks for
``thinking.display: "summarized"`` (billing is the same either way).

Pinned here:

- **The request** (``_qc_thinking``): reasoning summaries for the documented
  models only, never for an override outside the list, never when
  ``THINKING_DISPLAY`` is ``omitted``; and every streamed Final QC request of
  a run carries it, through the one builder both transports share.
- **The relay** (``_relay_stream_activity``): summary text travels as coarse
  ``{prefix}_thinking`` frames — throttled, closed by a ``final`` frame at
  the block's end, capped per request with a ``truncated`` marker — for
  lenses and seats only. Answer text and output-tool payloads still never
  cross.
- **The context the click-through shows**: ``qc_started`` names each lens's
  brief verbatim, the roster names the claim each panel tries to refute, and
  ``verifier_complete`` carries the seat's submitted one-line reasons.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend import settings
from backend.qc import engine
from backend.qc.schema import QC_LENSES
from tests.fakes import (
    SequencedFakeClient,
    _synthesize_events,
    block_start_event,
    block_stop_event,
    qc_findings_response,
    qc_verdict_response,
    text_delta_event,
    thinking_delta_event,
)
from tests.test_qc_live_events import _events_for, _finding, _run_client, _scripts


# ---------------------------------------------------------------------------
# The request
# ---------------------------------------------------------------------------


def test_documented_models_ask_for_reasoning_summaries():
    for model in settings.QC_THINKING_DISPLAY_MODELS:
        assert engine._qc_thinking(model) == {
            "type": "adaptive",
            "display": "summarized",
        }
    # The shipped lens and seat models are both on the list.
    assert settings.MODEL_OPUS_55 in settings.QC_THINKING_DISPLAY_MODELS
    assert settings.QC_VERIFIER_MODEL_DEFAULT in settings.QC_THINKING_DISPLAY_MODELS


@pytest.mark.parametrize("model", ["claude-opus-4-6", "a-proxy-model-id", ""])
def test_a_model_outside_the_list_sends_exactly_what_it_always_did(model):
    assert engine._qc_thinking(model) == {"type": "adaptive"}


def test_the_shared_display_switch_turns_the_summaries_off(monkeypatch):
    monkeypatch.setattr(settings, "THINKING_DISPLAY", "omitted")
    assert engine._qc_thinking(settings.MODEL_OPUS_55) == {"type": "adaptive"}


def test_the_one_builder_carries_it_for_both_transports():
    kwargs = engine._qc_request_kwargs(
        system_prompt="s",
        tools=[{"name": "t", "input_schema": {}}],
        model=settings.MODEL_SONNET_55,
        max_tokens=10,
        effort="high",
        cache_ttl="",
    )
    assert kwargs["thinking"] == {"type": "adaptive", "display": "summarized"}


# ---------------------------------------------------------------------------
# The relay
# ---------------------------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _timed(clock: _Clock, script: list[tuple[float, object]]):
    for advance, event in script:
        clock.now += advance
        yield event


def _relay(stream, *, prefix: str = "lens", relay_thinking: bool = True) -> list[dict]:
    frames: list[dict] = []
    engine._relay_stream_activity(
        stream,
        event_prefix=prefix,
        event_fields={"lens_id": "completeness"},
        event_sink=frames.append,
        activity_state={},
        relay_thinking=relay_thinking,
    )
    return frames


def _thinking_frames(frames: list[dict], prefix: str = "lens") -> list[dict]:
    return [frame for frame in frames if frame["type"] == f"{prefix}_thinking"]


def test_summary_text_travels_in_throttled_chunks_closed_by_a_final_frame(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(engine.time, "monotonic", clock)
    stream = _timed(
        clock,
        [
            (0.0, block_start_event(0, "thinking")),
            (0.5, thinking_delta_event(0, "**Checking scope**\n\n")),
            (0.5, thinking_delta_event(0, "Looking at PART 2 ")),
            # 2.5 s since the stream opened: the buffer goes out.
            (1.5, thinking_delta_event(0, "products.")),
            (0.1, thinking_delta_event(0, " Then PART 3.")),
            (0.1, block_stop_event(0)),
        ],
    )
    frames = _relay(stream)
    thinking = _thinking_frames(frames)
    assert [frame["text"] for frame in thinking] == [
        "**Checking scope**\n\nLooking at PART 2 products.",
        " Then PART 3.",
    ]
    assert "final" not in thinking[0]
    assert thinking[1]["final"] is True
    assert all(frame["lens_id"] == "completeness" for frame in thinking)
    assert not any("truncated" in frame for frame in thinking)
    # The activity change is still announced, once.
    assert [f["kind"] for f in frames if f["type"] == "lens_activity"] == ["thinking"]


def test_a_block_relayed_in_full_still_closes_with_an_empty_final_frame(monkeypatch):
    monkeypatch.setattr(engine, "_THINKING_RELAY_INTERVAL_S", 0.0)
    frames = _relay(
        iter(
            [
                block_start_event(0, "thinking"),
                thinking_delta_event(0, "Weighing the edition."),
                block_stop_event(0),
            ]
        )
    )
    assert _thinking_frames(frames) == [
        {"type": "lens_thinking", "lens_id": "completeness", "text": "Weighing the edition."},
        {"type": "lens_thinking", "lens_id": "completeness", "text": "", "final": True},
    ]


def test_an_empty_thinking_block_relays_nothing():
    # ``omitted`` display: the block streams, its text never does.
    frames = _relay(iter([block_start_event(0, "thinking"), block_stop_event(0)]))
    assert _thinking_frames(frames) == []


def test_the_cap_cuts_once_and_says_so(monkeypatch):
    monkeypatch.setattr(engine, "_THINKING_RELAY_INTERVAL_S", 0.0)
    monkeypatch.setattr(engine, "_THINKING_RELAY_MAX_CHARS", 10)
    frames = _relay(
        iter(
            [
                block_start_event(0, "thinking"),
                thinking_delta_event(0, "12345678"),
                thinking_delta_event(0, "abcdef"),
                thinking_delta_event(0, "ghij"),
                block_stop_event(0),
                block_start_event(1, "thinking"),
                thinking_delta_event(1, "a whole new block"),
                block_stop_event(1),
            ]
        )
    )
    assert [
        (frame["text"], frame.get("final"), frame.get("truncated"))
        for frame in _thinking_frames(frames)
    ] == [("12345678", None, None), ("ab", True, True)]


def test_the_cap_on_a_fresh_block_still_says_so(monkeypatch):
    monkeypatch.setattr(engine, "_THINKING_RELAY_INTERVAL_S", 0.0)
    monkeypatch.setattr(engine, "_THINKING_RELAY_MAX_CHARS", 4)
    frames = _relay(
        iter(
            [
                block_start_event(0, "thinking"),
                thinking_delta_event(0, "1234"),
                block_stop_event(0),
                block_start_event(1, "thinking"),
                thinking_delta_event(1, "more"),
                block_stop_event(1),
            ]
        )
    )
    assert [
        (frame["text"], frame.get("final"), frame.get("truncated"))
        for frame in _thinking_frames(frames)
    ] == [("1234", None, None), ("", True, None), ("", True, True)]


def test_answer_text_and_the_grouping_call_relay_no_reasoning(monkeypatch):
    monkeypatch.setattr(engine, "_THINKING_RELAY_INTERVAL_S", 0.0)
    events = [
        block_start_event(0, "thinking"),
        thinking_delta_event(0, "Grouping these."),
        block_stop_event(0),
        block_start_event(1, "text"),
        text_delta_event(1, "The answer text never relays."),
        block_stop_event(1),
    ]
    assert _thinking_frames(_relay(iter(events), relay_thinking=False)) == []
    relayed = _relay(iter(events))
    assert "answer text" not in str(relayed)
    assert engine._THINKING_RELAY_PREFIXES == frozenset({"lens", "verifier"})


# ---------------------------------------------------------------------------
# End to end through run_final_qc
# ---------------------------------------------------------------------------


def _with_thinking(response: SimpleNamespace, summary: list[str]) -> SimpleNamespace:
    """Prefix a scripted response's raw stream with one summarized block."""
    content_events = _synthesize_events(response.content, [])
    shifted = []
    for event in content_events:
        index = getattr(event, "index", None)
        if index is not None:
            event = SimpleNamespace(**{**vars(event), "index": index + 1})
        shifted.append(event)
    response.events = [
        block_start_event(0, "thinking"),
        *[thinking_delta_event(0, chunk) for chunk in summary],
        block_stop_event(0),
        *shifted,
    ]
    return response


def test_a_run_relays_lens_and_seat_reasoning_with_the_context_around_it():
    title = "Missing hydraulic design criteria"
    lens_response = _with_thinking(
        qc_findings_response(
            "completeness",
            findings=[_finding(title, severity="medium")],
        ),
        ["**Comparing scope to the profile**\n\n", "PART 2 lacks density."],
    )
    verdict = _with_thinking(
        qc_verdict_response(
            True,
            note="The criteria are absent.",
            ops_adequate=False,
            ops_note="No fix was proposed.",
        ),
        ["**Trying to refute**\n\n", "Searched PART 1; nothing covers it."],
    )
    scripts = _scripts(completeness=[lens_response])
    scripts[title] = [verdict, qc_verdict_response(True)]
    events: list[dict] = []
    client = SequencedFakeClient(scripts)
    result = _run_client(client, events)
    assert len(result.findings) == 1
    # Every streamed request of the run — lenses, grouping, seats — asks for
    # the summaries through the one builder.
    assert client.requests
    assert all(
        request["thinking"] == {"type": "adaptive", "display": "summarized"}
        for request in client.requests
    )

    started = _events_for(events, type="qc_started")[0]
    assert started["lenses"] == [
        {
            "lens_id": lens.lens_id,
            "title": lens.title,
            "brief": lens.brief,
            "web": lens.web,
        }
        for lens in QC_LENSES
    ]

    lens_thinking = _events_for(events, type="lens_thinking", lens_id="completeness")
    assert "".join(frame["text"] for frame in lens_thinking) == (
        "**Comparing scope to the profile**\n\nPART 2 lacks density."
    )
    assert lens_thinking[-1]["final"] is True

    roster = _events_for(events, type="verification_started")[0]["candidates"]
    assert roster[0]["issue"] == f"Issue for {title}."
    assert roster[0]["element_id"] == "pt1.a1.p1"

    seat = _events_for(events, candidate_id="candidate-1", reviewer_index=1)
    seat_thinking = [e for e in seat if e["type"] == "verifier_thinking"]
    assert "".join(e["text"] for e in seat_thinking) == (
        "**Trying to refute**\n\nSearched PART 1; nothing covers it."
    )
    complete = seat[-1]
    assert complete["type"] == "verifier_complete"
    assert complete["note"] == "The criteria are absent."
    assert complete["ops_note"] == "No fix was proposed."

    # Reasoning frames come only from lenses and seats.
    assert not [e for e in events if e["type"] == "consolidation_thinking"]
