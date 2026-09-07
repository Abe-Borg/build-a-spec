"""A declined turn is named, never dressed up as a truncated one.

The models this app runs (Sonnet 5 for the interview, research and template
passes; Opus 5 for Final QC) can decline a request outright: a normal HTTP
200 carrying ``stop_reason: "refusal"``, no exception raised, and — when the
classifier fires before any output — an empty ``content`` array. Every call
site used to fold that into the same bucket as a ``max_tokens`` cutoff,
which told the user their reply was "cut off before completion" and told a
support bundle nothing at all.

These tests pin the four call sites and the shared helper they read it
through. The property under test is honesty, not recovery: a refusal still
fails the call it lands in — what changed is that it now says so.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend import sessions, settings
from backend.app import _ai_generalized_template_document, create_app
from backend.llm.conversation import stream_user_turn
from backend.research.engine import (
    DIMENSION_ERROR_INCOMPLETE,
    DIMENSION_ERROR_REFUSAL,
    incomplete_dimension_facts,
    sanitized_error_kind,
)
from backend.research.grounding import (
    REFUSAL_KIND,
    STOP_CLASS_COMPLETE,
    STOP_CLASS_INCOMPLETE,
    STOP_CLASS_PAUSE,
    STOP_CLASS_REFUSED,
    classify_stop_reason,
    refusal_category,
)
from backend.templates import TemplateError
from tests.fakes import (
    FakeClient,
    SequencedFakeClient,
    qc_findings_response,
    qc_verdict_response,
    raw_turn,
    research_response,
    text_block,
)
from tests.test_qc import (  # noqa: F401 (shared scaffolding)
    _finding,
    _medium_scripts,
    _qc_scripts,
    _run as _run_qc,
    _section,
)
from tests.test_research_engine import (  # noqa: F401 (shared scaffolding)
    DIM_KEYS,
    _run as _run_research,
    _scripts,
)


# ---------------------------------------------------------------------------
# The shared classification helper
# ---------------------------------------------------------------------------


def test_refusal_is_its_own_stop_class_and_the_others_are_unchanged():
    assert classify_stop_reason("refusal") == STOP_CLASS_REFUSED
    # The classes every existing caller branches on keep their meaning: this
    # narrows the catch-all rather than re-routing anything already handled.
    assert classify_stop_reason("end_turn") == STOP_CLASS_COMPLETE
    assert classify_stop_reason("tool_use") == STOP_CLASS_COMPLETE
    assert classify_stop_reason("pause_turn") == STOP_CLASS_PAUSE
    assert classify_stop_reason("max_tokens") == STOP_CLASS_INCOMPLETE
    assert classify_stop_reason("stop_sequence") == STOP_CLASS_INCOMPLETE
    assert classify_stop_reason(None) == STOP_CLASS_INCOMPLETE


def test_the_category_is_read_defensively_from_both_response_shapes():
    sdk_shape = SimpleNamespace(
        stop_reason="refusal",
        stop_details=SimpleNamespace(type="refusal", category="cyber"),
    )
    assert refusal_category(sdk_shape) == "cyber"
    assert refusal_category({"stop_details": {"category": "bio"}}) == "bio"

    # Absent, null, and non-refusal shapes are ordinary answers, not errors:
    # stop_details is populated only on a refusal, and its category can be
    # null even then, so "" has to mean "no category named" rather than
    # "not a refusal" — the caller has already decided that from stop_reason.
    assert refusal_category(SimpleNamespace(stop_reason="end_turn")) == ""
    assert refusal_category(SimpleNamespace(stop_details=None)) == ""
    assert (
        refusal_category(
            SimpleNamespace(stop_details=SimpleNamespace(category=None))
        )
        == ""
    )
    assert refusal_category({}) == ""


def test_the_category_is_bounded_before_it_reaches_a_message():
    """Provider text from an OPEN set, so it is bounded rather than trusted.

    It reaches a research drawer, a chat transcript, and a Final QC report's
    error line; the closed telemetry token stays the caller's own kind.
    """
    assert refusal_category({"stop_details": {"category": "  cyber  "}}) == "cyber"
    assert refusal_category({"stop_details": {"category": "x" * 400}}) == "x" * 40
    assert (
        refusal_category({"stop_details": {"category": "</specification>"}})
        == "specification"
    )
    assert refusal_category({"stop_details": {"category": 7}}) == ""


# ---------------------------------------------------------------------------
# Research
# ---------------------------------------------------------------------------


def test_a_declined_dimension_is_named_a_refusal_and_is_not_retried():
    """The headline research case.

    A content decision is not a transient failure: the identical request
    earns the identical answer, so the dimension fails once and stops. The
    script carries exactly one turn for the declined dimension — a retry
    would exhaust it and raise instead of reaching the assertions.
    """
    client = SequencedFakeClient(
        _scripts(
            governing_codes=[
                research_response(
                    items=None,
                    stop_reason="refusal",
                    refusal_category="cyber",
                )
            ]
        )
    )
    profile = _run_research(client)

    declined = next(
        s for s in profile.dimension_statuses if s.dimension_id == "governing_codes"
    )
    assert declined.status == "failed"
    assert declined.error_kind == DIMENSION_ERROR_REFUSAL
    assert "declined" in declined.error and "cyber" in declined.error
    # The wording a truncation earns must not be borrowed here: it would
    # send the user to press Research again, which cannot help.
    assert "incomplete" not in declined.error.lower()

    # Exactly one request for the declined dimension: no retry was spent.
    governing = [
        r
        for r in client.requests
        if DIM_KEYS["governing_codes"] in json.dumps(r.get("messages", []))
    ]
    assert len(governing) == 1

    # The rest of the round is untouched — one declined area is not a failed run.
    assert profile.completed_dimensions == 3


def test_a_truncated_dimension_still_reads_as_incomplete():
    """The new branch narrows the catch-all; it must not swallow it."""
    client = SequencedFakeClient(
        _scripts(
            governing_codes=[
                research_response(items=None, stop_reason="max_tokens")
            ]
        )
    )
    profile = _run_research(client)

    declined = next(
        s for s in profile.dimension_statuses if s.dimension_id == "governing_codes"
    )
    assert declined.error_kind == DIMENSION_ERROR_INCOMPLETE
    assert "max_tokens" in declined.error


def test_the_refusal_kind_survives_the_telemetry_projection():
    """It is a real member of the closed vocabulary, not stray text.

    ``incomplete_dimension_facts`` is what reaches ``/api/diagnostics`` and a
    support bundle, and it degrades anything it does not recognize to
    ``unrecognized`` — so a kind that failed to land in the vocabulary would
    silently become the very "something failed" bucket this change exists to
    get out of.
    """
    assert sanitized_error_kind(DIMENSION_ERROR_REFUSAL) == DIMENSION_ERROR_REFUSAL
    assert DIMENSION_ERROR_REFUSAL == REFUSAL_KIND

    client = SequencedFakeClient(
        _scripts(
            governing_codes=[
                research_response(items=None, stop_reason="refusal")
            ]
        )
    )
    profile = _run_research(client)
    facts = incomplete_dimension_facts(profile)
    declined = next(f for f in facts if f["dimension_id"] == "governing_codes")
    assert declined["error_kind"] == "refusal"


# ---------------------------------------------------------------------------
# Final QC
# ---------------------------------------------------------------------------


def test_a_declined_lens_fails_the_call_and_leaves_the_run_partial():
    """A refusal must never read as "reviewed and found clean".

    It stays a FAILED lens, which is what keeps the existing safety property
    intact: incomplete coverage blocks issue readiness. What changes is only
    that the lens record now says why.
    """
    store = _section()
    client = SequencedFakeClient(
        _qc_scripts(
            completeness=[
                qc_findings_response(
                    "completeness",
                    findings=None,
                    stop_reason="refusal",
                    refusal_category="cyber",
                )
            ]
        )
    )
    result = _run_qc(client, store)

    declined = next(
        lens for lens in result.lens_statuses if lens.lens_id == "completeness"
    )
    assert declined.status == "failed"
    assert "declined" in declined.error and "cyber" in declined.error
    assert "incomplete (stop_reason" not in declined.error
    # The property that must not regress: a declined lens leaves the run
    # short of complete coverage.
    assert result.coverage_complete() is False


def test_both_verification_transports_describe_a_refused_seat_identically(
    monkeypatch,
):
    """Streamed and batched phase 2 differ in transport only.

    A refused VERIFIER SEAT is the case that reaches the batched path —
    phase 1 always streams, so a refused lens would exercise the streaming
    code twice and prove nothing. A reviewer comparing two runs must not
    find a refusal described one way in one and another way in the other,
    so both paths build the message from one helper.
    """
    messages: dict[bool, str] = {}
    for batched in (False, True):
        monkeypatch.setattr(settings, "QC_BATCH_VERIFICATION", batched)
        client = SequencedFakeClient(
            _medium_scripts(
                [
                    qc_verdict_response(True),
                    qc_verdict_response(
                        True, stop_reason="refusal", refusal_category="cyber"
                    ),
                ]
            )
        )
        result = _run_qc(client, _section())

        # The transport really differed — otherwise this test would compare
        # the streaming path against itself and pass for the wrong reason.
        assert bool(client.batches.created) is batched

        # An unaccounted seat makes the whole candidate inconclusive: a
        # refusal is infrastructure silence, never evidence for or against
        # the finding. That rule is unchanged; only its wording is new.
        assert len(result.inconclusive) == 1
        seat = next(
            v for v in result.inconclusive[0].verdicts if v.status != "completed"
        )
        messages[batched] = seat.error

    assert messages[False] == messages[True]
    assert "declined" in messages[False] and "cyber" in messages[False]


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


def _run_chat_turn(monkeypatch, turn) -> str:
    """Drive one chat turn against a scripted response; return the reply."""
    session = sessions.get_session()
    monkeypatch.setattr(
        "backend.llm.conversation.get_client", lambda: FakeClient([turn])
    )
    for _event in stream_user_turn(session, "Specify the access control system."):
        pass
    assistant = [m for m in session.history if m["role"] == "assistant"]
    assert assistant, "the turn should still commit"
    blocks = assistant[-1]["content"]
    return "".join(
        b.get("text", "") for b in blocks if b.get("type") == "text"
    )


def test_a_declined_chat_turn_says_so_rather_than_claiming_it_was_cut_off(
    monkeypatch,
):
    """The user-visible headline case.

    A classifier decline typically arrives with an EMPTY content array, so
    the placeholder below is the whole of what the user sees. "Cut off"
    invites them to resend the identical message, which earns the identical
    answer; the point of the fix is to tell them rewording is the move.
    """
    reply = _run_chat_turn(
        monkeypatch,
        raw_turn([], stop_reason="refusal", refusal_category="cyber"),
    )

    assert "declined" in reply
    assert "safety review: cyber" in reply
    assert "rephrasing" in reply.lower()
    assert "cut off" not in reply


def test_a_declined_chat_turn_without_a_category_still_reads_correctly(monkeypatch):
    """``stop_details`` can be absent even on a genuine refusal."""
    reply = _run_chat_turn(monkeypatch, raw_turn([], stop_reason="refusal"))

    assert "declined" in reply
    assert "safety review" not in reply
    assert "cut off" not in reply


def test_a_truncated_chat_turn_still_says_it_was_cut_off(monkeypatch):
    """Regression guard: the refusal branch narrows this case, not replaces it."""
    reply = _run_chat_turn(monkeypatch, raw_turn([], stop_reason="max_tokens"))

    assert reply == "[Response was cut off before completion.]"


def test_a_declined_chat_turn_keeps_whatever_text_arrived_first(monkeypatch):
    """A mid-stream decline bills the partial output, so it is not discarded.

    Only the empty case falls back to the placeholder — the same rule every
    other non-tool_use stop reason follows here.
    """
    reply = _run_chat_turn(
        monkeypatch,
        raw_turn(
            [text_block("Here is what I can say about that:")],
            stop_reason="refusal",
        ),
    )

    assert reply == "Here is what I can say about that:"


# ---------------------------------------------------------------------------
# Template AI-generalize
# ---------------------------------------------------------------------------


def test_a_declined_generalize_pass_is_not_reported_as_malformed_content(
    tmp_path, monkeypatch
):
    """A declined turn has no tool block at all.

    Without the check that reads as "the model returned malformed content" —
    the one wording guaranteed to send the user round the same loop again.
    """
    from tests.test_templates import _catalog, _starter

    catalog = _catalog(tmp_path)
    session = sessions.SessionState()
    session.doc.seed_template(_starter(catalog))

    class _Messages:
        def create(self, **_kwargs):
            return SimpleNamespace(
                content=[],
                stop_reason="refusal",
                stop_details=SimpleNamespace(type="refusal", category="cyber"),
                usage={"input_tokens": 9, "output_tokens": 0},
            )

    monkeypatch.setattr(
        "backend.app.get_client", lambda: SimpleNamespace(messages=_Messages())
    )
    with pytest.raises(TemplateError, match="declined") as excinfo:
        _ai_generalized_template_document(session)
    assert "cyber" in str(excinfo.value)
    # It points at the path that still works rather than at a retry.
    assert "Exact" in str(excinfo.value)


def test_the_endpoint_surface_is_untouched_by_this_change():
    """No route, event type, or payload key was added — this is response
    handling only. A smoke check that the app still builds and answers."""
    with TestClient(create_app()) as client:
        assert client.get("/api/health").status_code == 200
