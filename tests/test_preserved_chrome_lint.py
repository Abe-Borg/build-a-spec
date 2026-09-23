"""Every lint surface reads the same preserved header, footer and cover page.

An imported master keeps its headers, footers and front matter exactly as
they were, and the ``stale_document_identifier`` lint rule reads those lines
for a section number the body no longer carries. Four places lint the live
document: the doc payload (``GET /api/doc`` and every REST mutation), the
readiness checklist, the PROJECT CONTEXT a chat turn sends, and the ``lint``
SSE event a doc-changing turn emits after it commits.

The SSE event was the one that linted WITHOUT the preserved lines. So on a
master whose footer still names another section, the finding vanished from
the Issues drawer after every doc-changing turn, until the refetch that
follows ``turn_complete`` put it back: the stream disagreed with the payload
right behind it. ``SessionState.preserved_chrome()`` is now the one
derivation all four read.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.llm.conversation import SessionState
from backend.spec_doc.source_format import SourceFormatMap
from tests.fakes import FakeClient, request_context_text, text_turn, tool_turn
from tests.test_preserving_export import FOOTER_TEXT, _import, _master_bytes

STALE = "stale_document_identifier"

# The master's footer reads FOOTER_TEXT ("23 05 48 - 1"); this makes the
# section something else, which is exactly when that footer goes stale.
RENUMBER = {
    "action": "replace",
    "target_id": "sec",
    "text": "SEISMIC CONTROLS",
    "numbering": "23 05 93",
}

# A doc-changing edit that leaves the section identity alone.
UNRELATED_EDIT = {
    "action": "add_paragraph",
    "target_id": "pt1.a1",
    "text": "Isolators shall be factory assembled.",
    "status": "assumed",
}


def _parse_sse(body: str) -> list[dict]:
    return [
        json.loads(line[len("data: "):])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


def _stale(items: list[dict]) -> list[dict]:
    return [item for item in items if item["rule"] == STALE]


def _patch_client(monkeypatch, fake: FakeClient) -> None:
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)


def test_preserved_chrome_needs_the_retained_upload():
    session = SessionState()
    assert session.preserved_chrome() == ()

    session.source_format_map = SourceFormatMap(
        document_sha256="d" * 64,
        body_child_count=0,
        header_footer_text=("ACME ENGINEERING", FOOTER_TEXT),
        front_matter_text=("Section Number: 23 05 48",),
    )
    # The lines describe one specific upload. Reported beside a session that
    # no longer carries it, they would be a warning about nothing.
    assert session.preserved_chrome() == ()

    session.source_docx_bytes = b"PK\x03\x04 pretend master"
    assert session.preserved_chrome() == (
        "ACME ENGINEERING",
        FOOTER_TEXT,
        "Section Number: 23 05 48",
    )


@pytest.mark.parametrize("renumbered_by", ["turn", "panel"])
def test_the_turn_lint_event_matches_the_payload_behind_it(
    monkeypatch, renumbered_by
):
    """The finding must be in the ``lint`` event, not just the refetch.

    ``turn``: the chat turn itself renumbers the section, so the finding
    first appears with that turn's event. ``panel``: the section was
    renumbered before the turn and the turn edits something else, so the
    finding is already on screen and the event must not drop it.
    """
    client = TestClient(create_app())
    _import(client, _master_bytes())
    if renumbered_by == "panel":
        edited = client.post("/api/doc/edit", json={"ops": [RENUMBER]})
        assert edited.status_code == 200, edited.text
        assert _stale(edited.json()["lint"])
        edits = [UNRELATED_EDIT]
    else:
        edits = [RENUMBER]
    fake = FakeClient(
        [tool_turn(["Editing."], {"edits": edits}), text_turn(["Done."])]
    )
    _patch_client(monkeypatch, fake)

    response = client.post("/api/chat", json={"message": "Carry on."})
    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert events[-1]["type"] == "turn_complete", events[-1]
    lint_events = [event for event in events if event["type"] == "lint"]
    assert len(lint_events) == 1

    findings = _stale(lint_events[0]["items"])
    assert len(findings) == 1, lint_events[0]["items"]
    assert "23 05 48" in findings[0]["message"]
    assert "23 05 93" in findings[0]["message"]

    # The stream and the payload behind it agree, item for item.
    payload = client.get("/api/doc").json()
    assert lint_events[0]["items"] == payload["lint"]
    assert lint_events[0]["standards"] == payload["standards"]


def test_readiness_and_the_model_read_the_same_chrome(monkeypatch):
    """The two surfaces the event fix did not touch still see the finding."""
    client = TestClient(create_app())
    _import(client, _master_bytes())
    edited = client.post("/api/doc/edit", json={"ops": [RENUMBER]})
    assert edited.status_code == 200, edited.text
    payload_lint = edited.json()["lint"]
    assert _stale(payload_lint)

    readiness = client.get("/api/readiness").json()
    lint_check = next(
        check for check in readiness["checks"] if check["id"] == "lint_clean"
    )
    assert lint_check["ok"] is False
    assert lint_check["detail"] == (
        f"{len(payload_lint)} advisory lint issue(s)."
    )

    # The model is the one that can offer to renumber the section, so its
    # LINT REPORT carries the finding too.
    fake = FakeClient([text_turn(["Noted."])])
    _patch_client(monkeypatch, fake)
    response = client.post("/api/chat", json={"message": "What is left?"})
    assert _parse_sse(response.text)[-1]["type"] == "turn_complete"
    assert f"[{STALE}]" in request_context_text(fake.messages.requests[0])
