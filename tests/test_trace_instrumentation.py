"""Focused contracts for request/import diagnostic evidence."""
from __future__ import annotations

import hashlib
import json
import re
import time
from io import BytesIO

import pytest
from docx import Document
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.tracing import config as trace_config
from backend.tracing import recorder as recorder_module
from backend.tracing.recorder import set_recorder


@pytest.fixture
def trace_env(monkeypatch, tmp_path):
    monkeypatch.setenv(trace_config.ENV_TRACE, "1")
    trace_dir = tmp_path / "traces"
    monkeypatch.setenv(trace_config.ENV_TRACE_DIR, str(trace_dir))
    set_recorder(None)
    yield trace_dir
    recorder = recorder_module.get_recorder()
    if recorder is not None:
        recorder.stop()
    set_recorder(None)


def _wait_events(predicate, timeout: float = 3.0) -> list[dict]:
    recorder = recorder_module.get_recorder()
    assert recorder is not None
    path = recorder.trace_dir / "events.jsonl"
    deadline = time.monotonic() + timeout
    events: list[dict] = []
    while time.monotonic() < deadline:
        if path.exists():
            events = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            if predicate(events):
                return events
        time.sleep(0.02)
    return events


def _loose_docx() -> bytes:
    document = Document()
    document.add_paragraph("Loose body text")
    stream = BytesIO()
    document.save(stream)
    return stream.getvalue()


def test_request_id_is_echoed_logged_and_traced_with_closed_error_code(
    trace_env, caplog
):
    client = TestClient(create_app())
    request_id = "support-case_42"

    with caplog.at_level("INFO", logger="buildaspec.api"):
        response = client.post(
            "/api/doc/edit",
            json={"ops": [], "workspace_id": 999_999, "generation": 0},
            headers={"X-Request-ID": request_id},
        )

    assert response.status_code == 409
    assert response.json()["code"] == "stale_workspace"
    assert response.headers["X-Request-ID"] == request_id
    assert f"request_id={request_id}" in caplog.text

    events = _wait_events(
        lambda values: any(
            event.get("type") == "api_request"
            and event.get("request_id") == request_id
            for event in values
        )
    )
    event = next(
        event
        for event in events
        if event.get("type") == "api_request"
        and event.get("request_id") == request_id
    )
    assert event["outcome_code"] == "stale_workspace"
    assert event["workspace_id_before"] == event["workspace_id_after"]
    assert event["generation_before"] == event["generation_after"]
    assert "error" not in event and "body" not in event


def test_invalid_incoming_request_id_is_replaced_and_generic_4xx_is_coded(
    trace_env,
):
    client = TestClient(create_app())
    supplied = "not safe because it has spaces"
    response = client.get(
        "/api/does-not-exist",
        headers={"X-Request-ID": supplied},
    )

    generated = response.headers["X-Request-ID"]
    assert response.status_code == 404
    assert generated != supplied
    assert re.fullmatch(r"[0-9a-f]{32}", generated)

    events = _wait_events(
        lambda values: any(
            event.get("type") == "api_request"
            and event.get("request_id") == generated
            for event in values
        )
    )
    event = next(
        event
        for event in events
        if event.get("type") == "api_request"
        and event.get("request_id") == generated
    )
    assert event["outcome_code"] == "not_found"


def test_import_trace_has_source_and_sanitized_warning_evidence(trace_env):
    client = TestClient(create_app())
    source = _loose_docx()
    response = client.post(
        "/api/import/master",
        files={
            "file": (
                "loose.docx",
                source,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        headers={"X-Request-ID": "import-evidence"},
    )
    assert response.status_code == 200, response.text
    warning_text = "\n".join(response.json()["warnings"])
    assert "does not look like a SectionFormat" in warning_text

    events = _wait_events(
        lambda values: any(event.get("type") == "import" for event in values)
    )
    event = next(event for event in events if event.get("type") == "import")
    assert event["request_id"] == "import-evidence"
    assert event["source_sha256"] == hashlib.sha256(source).hexdigest()
    assert event["source_bytes"] == len(source)
    assert event["zip_member_count"] > 0
    assert event["zip_uncompressed_bytes"] >= len(source)
    assert event["generation_after"] == event["generation_before"] + 1
    assert event["workspace_scope"] == "original"
    assert event["warning_message_count"] == event["warnings"]
    assert event["warning_code_counts"]["unstructured_document"] == 1
    assert event["warning_code_counts"]["synthetic_article"] == 1
    assert all(
        re.fullmatch(r"[0-9a-f]{64}", item["fingerprint"])
        for item in event["warning_evidence"]
    )
    # Warning prose can include source-derived titles.  The trace keeps only
    # codes, numeric facts, and fingerprints.
    serialized = json.dumps(event, ensure_ascii=False)
    assert "Loose body text" not in serialized
    assert "does not look like a SectionFormat" not in serialized
