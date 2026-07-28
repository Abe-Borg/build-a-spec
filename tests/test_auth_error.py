"""An invalid/expired stored API key must never surface raw SDK exception
text — chat, research, and Final QC all classify a 401
(``anthropic.AuthenticationError``) into the one shared friendly message
(``AUTH_ERROR_MESSAGE``) and flag it with ``kind``/``error_kind`` so the
frontend can show a gentle explanatory modal instead of an error bubble
full of JSON."""
from __future__ import annotations

import json
import threading

import pytest
from fastapi.testclient import TestClient

from backend import settings
from backend.app import create_app
from backend.llm.client import AUTH_ERROR_MESSAGE
from backend.qc.engine import QCFanoutError, run_final_qc
from backend.qc.runner import QCRunner, STATUS_FAILED as QC_STATUS_FAILED
from backend.research.engine import ResearchFanoutError, run_requirements_research
from backend.research.runner import ResearchRunner, STATUS_FAILED as RESEARCH_STATUS_FAILED
from backend.spec_modules import DEFAULT_MODULE
from tests.fakes import FakeClient, SequencedFakeClient, auth_error
from tests.test_qc import _LENS_KEYS, _run as _run_qc, _section
from tests.test_research_engine import DEFAULT_MODULE as FIRE_MODULE, DIM_KEYS, PROFILE


def _client() -> TestClient:
    return TestClient(create_app())


def _parse_sse(body: str) -> list[dict]:
    return [
        json.loads(line[len("data: "):])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


def test_chat_401_is_a_friendly_auth_error_not_raw_json(monkeypatch):
    fake = FakeClient([auth_error()])
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)

    resp = _client().post("/api/chat", json={"message": "hello"})
    frames = _parse_sse(resp.text)
    error_frames = [f for f in frames if f["type"] == "error"]
    assert len(error_frames) == 1
    assert error_frames[0]["message"] == AUTH_ERROR_MESSAGE
    assert error_frames[0]["kind"] == "auth_error"
    # Never the raw SDK body.
    assert "authentication_error" not in error_frames[0]["message"]
    assert "401" not in error_frames[0]["message"]


# ---------------------------------------------------------------------------
# Research
# ---------------------------------------------------------------------------


def _all_dimensions_auth_fail() -> dict[str, list]:
    return {key: [auth_error()] for key in DIM_KEYS.values()}


def test_all_research_dimensions_failing_on_auth_is_flagged_at_the_engine():
    client = SequencedFakeClient(_all_dimensions_auth_fail())
    with pytest.raises(ResearchFanoutError) as exc_info:
        run_requirements_research(
            FIRE_MODULE, PROFILE, client, model="claude-sonnet-5", max_tokens=4096
        )
    assert exc_info.value.auth_error is True
    assert AUTH_ERROR_MESSAGE in str(exc_info.value)
    assert "authentication_error" not in str(exc_info.value)


def test_research_runner_surfaces_auth_error_kind_end_to_end():
    runner = ResearchRunner()
    settled = threading.Event()
    assert runner.start(
        module=FIRE_MODULE,
        project_profile=PROFILE,
        client=SequencedFakeClient(_all_dimensions_auth_fail()),
        model="claude-sonnet-5",
        max_tokens=4096,
        on_settled=settled.set,
    )
    assert settled.wait(timeout=10)

    assert runner.status == RESEARCH_STATUS_FAILED
    assert runner.error_kind == "auth_error"
    assert AUTH_ERROR_MESSAGE in runner.error
    assert "authentication_error" not in runner.error

    snapshot = runner.snapshot()
    assert snapshot["error_kind"] == "auth_error"

    failed_events = [e for e in runner.events if e["type"] == "research_failed"]
    assert len(failed_events) == 1
    assert failed_events[0]["error_kind"] == "auth_error"


def test_a_genuinely_mixed_research_failure_is_not_mislabeled_as_auth():
    scripts = _all_dimensions_auth_fail()
    # One dimension fails for an unrelated reason — the whole run must not
    # be reported as an auth error just because most dimensions were.
    scripts[DIM_KEYS["site_environment"]] = [RuntimeError("boom")]
    client = SequencedFakeClient(scripts)
    with pytest.raises(ResearchFanoutError) as exc_info:
        run_requirements_research(
            FIRE_MODULE, PROFILE, client, model="claude-sonnet-5", max_tokens=4096
        )
    assert exc_info.value.auth_error is False


# ---------------------------------------------------------------------------
# Final QC
# ---------------------------------------------------------------------------


def _all_lenses_auth_fail() -> dict[str, list]:
    return {key: [auth_error()] for key in _LENS_KEYS.values()}


def test_all_qc_lenses_failing_on_auth_is_flagged_at_the_engine():
    store = _section()
    with pytest.raises(QCFanoutError) as exc_info:
        _run_qc(SequencedFakeClient(_all_lenses_auth_fail()), store)
    assert exc_info.value.auth_error is True
    assert AUTH_ERROR_MESSAGE in str(exc_info.value)
    assert "authentication_error" not in str(exc_info.value)


def test_qc_runner_surfaces_auth_error_kind_end_to_end():
    store = _section()
    runner = QCRunner()
    settled = threading.Event()
    assert runner.start(
        section=store.doc,
        profile=None,
        module=DEFAULT_MODULE,
        client=SequencedFakeClient(_all_lenses_auth_fail()),
        model=settings.QC_MODEL,
        max_tokens=4096,
        version_index=0,
        on_settled=settled.set,
    )
    assert settled.wait(timeout=10)

    assert runner.status == QC_STATUS_FAILED
    assert runner.error_kind == "auth_error"
    assert AUTH_ERROR_MESSAGE in runner.error
    assert "authentication_error" not in runner.error
    assert runner.latest_attempt_error_kind == "auth_error"

    snapshot = runner.snapshot()
    assert snapshot["error_kind"] == "auth_error"
    assert snapshot["latest_attempt"]["error_kind"] == "auth_error"

    failed_events = [e for e in runner.events if e["type"] == "qc_failed"]
    assert len(failed_events) == 1
    assert failed_events[0]["error_kind"] == "auth_error"
