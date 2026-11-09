"""A reached spend limit is never retried, and every surface says what it is.

Anthropic's rate-limits page documents two responses that stop an
organization's requests until a date, and neither ends within a run:

- the usage tier's monthly cap: a 429 ``rate_limit_error`` with no
  ``retry-after`` and, on the Messages API,
  ``error.details.error_code == "enforced_spend_limit_reached"``;
- a limit the organization set itself: a 400 ``invalid_request_error``
  whose message starts "You have reached your specified API usage limits".

The cap used to be classified as a rate limit: research and Final QC retried
it with backoff (each attempt also retried twice by the SDK) and then showed
the SDK's raw "Error code: 429 - {...}" text. These tests pin the new class
(``FailureClass.SPEND_LIMIT``), that nothing retries it, that Final QC's
verifier breaker trips on it, and the one message every surface shows.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend import resource_pressure
from backend.app import create_app
from backend.qc import engine as qc_engine
from backend.qc.engine import QCFanoutError
from backend.research import engine as research_engine
from backend.research.engine import ResearchFanoutError, run_requirements_research
from backend.research.retry_policy import (
    PROVIDER_MESSAGE_MAX_CHARS,
    SPEND_LIMIT_CAP,
    SPEND_LIMIT_OWN,
    FailureClass,
    classify_exception,
    is_retryable_failure_class,
    is_spend_limit_text,
    provider_error_message,
    spend_limit_kind,
    spend_limit_kind_of_error,
    spend_limit_message,
    spend_limit_text,
)
from tests.fakes import (
    OWN_SPEND_LIMIT_PROVIDER_MESSAGE,
    SPEND_CAP_PROVIDER_MESSAGE,
    FakeClient,
    SequencedFakeClient,
    own_spend_limit_error,
    rate_limit_error_with_body,
    spend_cap_error,
    user_text,
)
from tests.test_qc import _LENS_KEYS, _run as _run_qc, _section
from tests.test_qc_streamed_stagger import _run as _run_streamed, _scripts, _verdicts
from tests.test_qc_verifier_v3 import _InvalidRequestVerifierClient
from tests.test_research_engine import DEFAULT_MODULE as FIRE_MODULE, DIM_KEYS, PROFILE


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    """Every retry loop sleeps through one ``time`` module per engine; a
    spend limit must reach none of them."""
    slept: list[float] = []
    monkeypatch.setattr(research_engine.time, "sleep", slept.append)
    monkeypatch.setattr(qc_engine.time, "sleep", slept.append)
    return slept


def _client() -> TestClient:
    return TestClient(create_app())


def _parse_sse(body: str) -> list[dict]:
    return [
        json.loads(line[len("data: "):])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def test_the_tier_cap_is_a_spend_limit_never_a_rate_limit():
    exc = spend_cap_error()
    assert spend_limit_kind(exc) == SPEND_LIMIT_CAP
    assert classify_exception(exc) is FailureClass.SPEND_LIMIT
    assert not is_retryable_failure_class(FailureClass.SPEND_LIMIT)


def test_the_cap_is_recognized_by_its_message_when_the_code_is_missing():
    """Off the Messages API the page promises no error code; the message
    prefix on a 429 with no ``retry-after`` is the cap's other mark."""
    assert classify_exception(spend_cap_error(details=False)) is FailureClass.SPEND_LIMIT


def test_a_429_with_retry_after_is_a_rate_limit_even_with_the_caps_words():
    """The cap's 429 never carries ``retry-after``; one that does is a limit
    that ends (the Claude Code workspace's, for example) and is retried."""
    exc = spend_cap_error(details=False, retry_after="30")
    assert spend_limit_kind(exc) == ""
    assert classify_exception(exc) is FailureClass.RATE_LIMIT


def test_the_error_code_decides_alone():
    exc = spend_cap_error(retry_after="30")
    assert classify_exception(exc) is FailureClass.SPEND_LIMIT


def test_an_ordinary_rate_limit_is_still_retried():
    assert classify_exception(rate_limit_error_with_body()) is FailureClass.RATE_LIMIT


@pytest.mark.parametrize(
    "message",
    [
        OWN_SPEND_LIMIT_PROVIDER_MESSAGE,
        "You have reached your specified workspace API usage limits. You will "
        "regain access on 2026-12-01 at 00:00 UTC.",
    ],
)
def test_the_organizations_own_limit_is_a_spend_limit(message):
    exc = own_spend_limit_error(message)
    assert spend_limit_kind(exc) == SPEND_LIMIT_OWN
    assert classify_exception(exc) is FailureClass.SPEND_LIMIT


def test_a_400_that_merely_quotes_the_phrase_is_still_an_invalid_request():
    exc = own_spend_limit_error(
        "messages.0: text mentions 'You have reached your specified API usage limits'"
    )
    assert spend_limit_kind(exc) == ""
    assert classify_exception(exc) is FailureClass.INVALID_REQUEST


def test_a_batch_items_error_object_is_read_the_same_way():
    """Batch result errors arrive as objects with no status or headers."""
    cap = SimpleNamespace(
        type="rate_limit_error",
        message=SPEND_CAP_PROVIDER_MESSAGE,
        details=SimpleNamespace(error_code="enforced_spend_limit_reached"),
    )
    assert spend_limit_kind_of_error(cap) == SPEND_LIMIT_CAP
    assert (
        spend_limit_kind_of_error(
            {"type": "invalid_request_error", "message": OWN_SPEND_LIMIT_PROVIDER_MESSAGE}
        )
        == SPEND_LIMIT_OWN
    )
    assert spend_limit_kind_of_error({"type": "api_error", "message": "boom"}) == ""
    assert spend_limit_kind_of_error(None) == ""


def test_final_qcs_batch_errors_carry_the_class_and_the_words():
    failure_class, message = qc_engine._batch_error_facts(
        {
            "type": "error",
            "error": {
                "type": "rate_limit_error",
                "message": SPEND_CAP_PROVIDER_MESSAGE,
                "details": {"error_code": "enforced_spend_limit_reached"},
            },
        }
    )
    assert failure_class is FailureClass.SPEND_LIMIT
    assert message == spend_limit_text(SPEND_LIMIT_CAP, SPEND_CAP_PROVIDER_MESSAGE)
    # An ordinary item error keeps its old reading.
    failure_class, message = qc_engine._batch_error_facts(
        {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}}
    )
    assert failure_class is FailureClass.RATE_LIMIT
    assert message == "slow down"


@pytest.mark.parametrize(
    ("status", "error", "kind"),
    [
        (
            429,
            {
                "type": "rate_limit_error",
                "message": SPEND_CAP_PROVIDER_MESSAGE,
                "details": {"error_code": "enforced_spend_limit_reached"},
            },
            SPEND_LIMIT_CAP,
        ),
        (
            400,
            {"type": "invalid_request_error", "message": OWN_SPEND_LIMIT_PROVIDER_MESSAGE},
            SPEND_LIMIT_OWN,
        ),
    ],
)
def test_the_error_the_installed_sdk_raises_is_recognized(status, error, kind):
    """Not a hand-built exception: the installed SDK parses a real response
    (2.6.0 raises its own ``EnforcedSpendLimitReachedError``, a
    ``RateLimitError``, for the cap) and the app still reads it from the
    body, so either SDK release classifies it the same way."""
    import anthropic
    import httpx2

    def transport(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            status, json={"type": "error", "error": error, "request_id": "req_x"}
        )

    client = anthropic.Anthropic(
        api_key="test-key-hermetic-wire",
        max_retries=0,
        http_client=anthropic.DefaultHttpxClient(
            transport=httpx2.MockTransport(transport)
        ),
    )
    with pytest.raises(anthropic.APIStatusError) as exc_info:
        client.messages.count_tokens(
            model="claude-opus-5-5", messages=[{"role": "user", "content": "Hi"}]
        )
    exc = exc_info.value
    assert spend_limit_kind(exc) == kind
    assert classify_exception(exc) is FailureClass.SPEND_LIMIT
    assert spend_limit_message(exc) == spend_limit_text(kind, error["message"])


# ---------------------------------------------------------------------------
# The words
# ---------------------------------------------------------------------------


def test_the_message_quotes_the_provider_and_says_what_to_do():
    cap = spend_limit_message(spend_cap_error())
    assert cap.startswith("Your Anthropic organization has reached its monthly API spend cap")
    assert f'"{SPEND_CAP_PROVIDER_MESSAGE}"' in cap
    assert "Limits page" in cap
    assert is_spend_limit_text(cap)

    own = spend_limit_message(own_spend_limit_error())
    assert own.startswith("Your Anthropic organization has reached the spend limit set")
    assert f'"{OWN_SPEND_LIMIT_PROVIDER_MESSAGE}"' in own
    assert "Billing page" in own
    assert is_spend_limit_text(own)

    # Never the SDK's raw "Error code: 429 - {...}" rendering of the body.
    for text in (cap, own):
        assert "Error code" not in text
        assert "rate_limit_error" not in text
        assert "enforced_spend_limit_reached" not in text


def test_anything_else_gets_no_spend_limit_words():
    assert spend_limit_message(rate_limit_error_with_body()) == ""
    assert spend_limit_message(RuntimeError("boom")) == ""
    assert spend_limit_text("") == ""
    assert not is_spend_limit_text("RateLimitError: slow down")


def test_the_providers_words_are_one_line_and_bounded():
    long = "You have reached your API usage limits:\n  " + "x" * 2000
    folded = spend_limit_text(SPEND_LIMIT_CAP, long)
    quoted = folded.split('"')[1]
    assert "\n" not in quoted
    assert len(quoted) == PROVIDER_MESSAGE_MAX_CHARS
    assert quoted.endswith("…")
    # No provider words, no empty quotes.
    assert '""' not in spend_limit_text(SPEND_LIMIT_CAP, "")


def test_provider_error_message_reads_the_body_not_the_sdk_rendering():
    assert provider_error_message(rate_limit_error_with_body("slow down")) == "slow down"
    assert provider_error_message(RuntimeError("x")) == ""


# ---------------------------------------------------------------------------
# Research
# ---------------------------------------------------------------------------


def test_research_never_retries_a_spend_limit_and_says_it_once(_no_backoff):
    client = SequencedFakeClient({key: [spend_cap_error()] for key in DIM_KEYS.values()})
    with pytest.raises(ResearchFanoutError) as exc_info:
        run_requirements_research(
            FIRE_MODULE, PROFILE, client, model="claude-sonnet-5", max_tokens=4096
        )
    message = str(exc_info.value)
    expected = spend_limit_text(SPEND_LIMIT_CAP, SPEND_CAP_PROVIDER_MESSAGE)
    # One attempt per area, no backoff slept, the words said once.
    assert len(client.requests) == len(DIM_KEYS)
    assert _no_backoff == []
    assert message.count(expected) == 1
    assert "Error code" not in message
    assert exc_info.value.auth_error is False


def test_a_research_area_records_the_spend_limit_kind():
    """A round where one area is stopped by the limit and the rest finish
    keeps that area's own record (and the profile)."""
    events: list[dict] = []
    scripts = {key: [spend_cap_error()] for key in DIM_KEYS.values()}
    client = SequencedFakeClient(scripts)
    with pytest.raises(ResearchFanoutError):
        run_requirements_research(
            FIRE_MODULE,
            PROFILE,
            client,
            model="claude-sonnet-5",
            max_tokens=4096,
            event_sink=events.append,
        )
    failed = [event for event in events if event["type"] == "dimension_failed"]
    assert len(failed) == len(DIM_KEYS)
    assert all(is_spend_limit_text(event["error"]) for event in failed)
    assert not [event for event in events if event["type"] == "dimension_retry"]


# ---------------------------------------------------------------------------
# Final QC
# ---------------------------------------------------------------------------


def test_final_qc_lenses_never_retry_a_spend_limit_and_say_it_once(_no_backoff):
    client = SequencedFakeClient({key: [spend_cap_error()] for key in _LENS_KEYS.values()})
    with pytest.raises(QCFanoutError) as exc_info:
        _run_qc(client, _section())
    message = str(exc_info.value)
    expected = spend_limit_text(SPEND_LIMIT_CAP, SPEND_CAP_PROVIDER_MESSAGE)
    assert len(client.requests) == len(_LENS_KEYS)
    assert _no_backoff == []
    assert message.count(expected) == 1
    assert "Error code" not in message


class _SpendCapVerifierClient(_InvalidRequestVerifierClient):
    """Lenses answer; every verifier seat meets the spend cap."""

    def stream(self, **request):
        if "[[QC-VERIFY:" not in user_text(request.get("messages") or []):
            return SequencedFakeClient.stream(self, **request)
        with self._lock:
            self.requests.append(request)
            self.verifier_request_count += 1
        raise spend_cap_error()


def test_a_spend_limit_trips_the_verifier_breaker_after_one_request(_no_backoff):
    """The limit is the whole account's, so the first seat that meets it
    stops the phase: the rest are recorded, never sent."""
    client = _SpendCapVerifierClient(_scripts())

    result = _run_streamed(client, warm=45)

    assert client.verifier_request_count == 1
    assert _no_backoff == []
    verdicts = _verdicts(result)
    assert len(verdicts) == 4
    assert all(verdict.status == "failed" for verdict in verdicts)
    expected = spend_limit_text(SPEND_LIMIT_CAP, SPEND_CAP_PROVIDER_MESSAGE)
    assert all(expected in verdict.error for verdict in verdicts)
    not_started = [
        verdict
        for verdict in verdicts
        if "was not started after a shared request failure" in verdict.error
    ]
    assert len(not_started) == 3


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


def test_the_chat_says_the_spend_limit_in_words(monkeypatch):
    fake = FakeClient([spend_cap_error()])
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)

    frames = _parse_sse(_client().post("/api/chat", json={"message": "hello"}).text)
    (error,) = [frame for frame in frames if frame["type"] == "error"]
    assert error["message"] == spend_limit_text(SPEND_LIMIT_CAP, SPEND_CAP_PROVIDER_MESSAGE)
    assert error["kind"] == "spend_limit"
    # Not provider pressure: the ledger records the class, never a rate limit.
    (run,) = [
        run
        for run in resource_pressure.snapshot()["runs"]
        if run["engine"] == resource_pressure.ENGINE_CHAT
    ]
    turn = run["agents"]["turn"]
    assert turn["outcome"] == resource_pressure.OUTCOME_FAILED
    assert turn["error_kind"] == "spend_limit"
    assert not turn["pressure_counts"]
    assert run["starved"] is False


def test_the_chat_shows_the_providers_words_for_any_other_error(monkeypatch):
    fake = FakeClient([rate_limit_error_with_body("slow down")])
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)

    frames = _parse_sse(_client().post("/api/chat", json={"message": "hello"}).text)
    (error,) = [frame for frame in frames if frame["type"] == "error"]
    assert error["message"] == "Anthropic API error (429): slow down"
    assert "kind" not in error


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def test_the_key_test_says_the_key_works_but_the_account_is_capped(monkeypatch):
    def _capped_client(_key: str):
        def _list(**_kwargs):
            raise spend_cap_error()

        return SimpleNamespace(models=SimpleNamespace(list=_list))

    monkeypatch.setattr("backend.app.build_probe_client", _capped_client)
    data = _client().post("/api/key/test", json={"api_key": "sk-ant-anything"}).json()
    assert data["ok"] is False
    assert is_spend_limit_text(data["error"])


def test_the_harvest_names_a_spend_limit(monkeypatch):
    from tests.test_harvest import _conversation, _patch_harvest, _session

    client = _client()
    session = _session(client)
    _conversation(session, 2)
    _patch_harvest(monkeypatch, own_spend_limit_error())

    resp = client.post("/api/project/facts/harvest")
    body = resp.json()
    assert resp.status_code == 502
    assert body["code"] == "spend_limit"
    assert body["error"] == spend_limit_text(SPEND_LIMIT_OWN, OWN_SPEND_LIMIT_PROVIDER_MESSAGE)
