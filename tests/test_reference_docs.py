"""Reference documents: background material the model reads, never edits.

Three properties carry the feature and are pinned hardest here:

1. a reference document never becomes the spec — no tree, no lint, no export;
2. its body never rides the per-turn PROJECT CONTEXT, only a stub does, and it
   is elided from committed history after the turn that read it (the
   fetched-PDF posture) — otherwise every attached document would be re-billed
   forever;
3. it is user content, so it survives a save/load and counts as unsaved work.
"""
from __future__ import annotations

import io

from docx import Document
from fastapi.testclient import TestClient

from backend import sessions
from backend.app import create_app
from backend.llm.conversation import (
    _committed_messages,
    _run_tool,
    _turn_context_text,
)
from backend.reference_docs import (
    MAX_REFERENCE_DOCS,
    MAX_TEXT_CHARS,
    ReferenceDocError,
    ReferenceDocStore,
)
from backend.spec_doc.project import load_project

DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)

STANDARD = [
    "ACME DATA CENTERS — MECHANICAL DESIGN STANDARD",
    "3.2 Chilled Water",
    "Chilled water supply temperature shall be 44 degrees F at design.",
    "3.3 Redundancy",
    "All chiller plants shall be N+1 at the design day load.",
]
# A phrase that appears only inside the body, so "did the body leak here?" is
# a single unambiguous check everywhere below.
BODY_MARKER = "44 degrees F"


def _docx_bytes(lines: list[str]) -> bytes:
    document = Document()
    for text in lines:
        document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _attach(client: TestClient, name: str = "acme.docx", lines=None):
    # `is None`, not falsy: an explicit [] is the empty-document case.
    body = _docx_bytes(STANDARD if lines is None else lines)
    return client.post(
        "/api/reference/upload",
        files={"file": (name, body, DOCX_MEDIA_TYPE)},
    )


def _read_tool(session, ref_id: str):
    return _run_tool(
        session,
        {"name": "read_reference_doc", "id": "tu1", "input": {"ref_id": ref_id}},
    )[0]


# ---------------------------------------------------------------------------
# Store units
# ---------------------------------------------------------------------------


def test_ids_are_monotonic_and_never_reused():
    store = ReferenceDocStore()
    first = store.add(filename="a.docx", text="alpha", block_count=1)
    store.add(filename="b.docx", text="beta", block_count=1)
    store.delete(first.rid)
    third = store.add(filename="c.docx", text="gamma", block_count=1)

    assert first.rid == "ref-1"
    assert third.rid == "ref-3"


def test_an_empty_document_is_rejected():
    store = ReferenceDocStore()
    try:
        store.add(filename="blank.docx", text="   \n  ", block_count=0)
    except ReferenceDocError as exc:
        assert "no readable text" in str(exc)
    else:  # pragma: no cover - the guard must hold
        raise AssertionError("an empty reference document must be rejected")


def test_the_attachment_count_is_bounded():
    store = ReferenceDocStore()
    for i in range(MAX_REFERENCE_DOCS):
        store.add(filename=f"{i}.docx", text="body", block_count=1)
    try:
        store.add(filename="one-too-many.docx", text="body", block_count=1)
    except ReferenceDocError as exc:
        assert "maximum" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("the store must cap attachments")


def test_oversized_text_is_truncated_loudly_never_silently():
    store = ReferenceDocStore()
    doc = store.add(
        filename="huge.docx", text="x" * (MAX_TEXT_CHARS + 5_000), block_count=1
    )

    assert doc.truncated is True
    # The record keeps the true size, and the stored text says what is missing.
    assert doc.char_count == MAX_TEXT_CHARS + 5_000
    assert "truncated" in doc.text
    assert "has NOT been read" in doc.text


def test_metadata_never_carries_the_body():
    """It rides every document payload; a body here would cost a full copy
    of every attached document on each poll."""
    store = ReferenceDocStore()
    store.add(filename="a.docx", text=BODY_MARKER, block_count=1)

    metadata = store.snapshot()[0]

    assert "text" not in metadata
    assert metadata["excerpt"]


def test_malformed_persisted_data_degrades_to_empty():
    store = ReferenceDocStore()
    store.load({"reference_docs": ["not a dict", {"no": "rid"}, 17]})
    assert store.docs == []
    store.load("not even a dict")
    assert store.docs == []


# ---------------------------------------------------------------------------
# The endpoints
# ---------------------------------------------------------------------------


def test_attaching_a_document_does_not_touch_the_spec():
    client = TestClient(create_app())

    body = _attach(client).json()

    assert body["ok"] is True
    assert body["reference_doc"]["rid"] == "ref-1"
    # The whole point: the document is untouched and still importable.
    assert sessions.get_session().doc.doc.is_empty()
    assert client.get("/api/doc").json()["doc"]["parts"][0]["articles"] == []


def test_a_reference_can_be_attached_after_the_draft_has_content():
    """Unlike a master import there is no blank-document precondition."""
    client = TestClient(create_app())
    client.post(
        "/api/doc/edit",
        json={
            "ops": [
                {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"}
            ]
        },
    )

    assert _attach(client).status_code == 200


def test_the_document_payload_lists_references_without_bodies():
    client = TestClient(create_app())
    _attach(client)

    payload = client.get("/api/doc").json()

    assert [d["rid"] for d in payload["reference_docs"]] == ["ref-1"]
    assert "text" not in payload["reference_docs"][0]


def test_non_docx_and_unreadable_uploads_are_refused():
    client = TestClient(create_app())

    assert (
        client.post(
            "/api/reference/upload",
            files={"file": ("notes.txt", b"hello", "text/plain")},
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/reference/upload",
            files={"file": ("broken.docx", b"not a zip", DOCX_MEDIA_TYPE)},
        ).status_code
        == 400
    )


def test_a_document_with_no_text_is_refused():
    client = TestClient(create_app())
    assert _attach(client, "blank.docx", lines=[]).status_code == 400


def test_remove_and_list():
    client = TestClient(create_app())
    _attach(client)
    _attach(client, "second.docx")

    assert len(client.get("/api/references").json()["reference_docs"]) == 2
    removed = client.delete("/api/reference/ref-1")
    assert removed.status_code == 200
    assert [d["rid"] for d in removed.json()["reference_docs"]] == ["ref-2"]
    assert client.delete("/api/reference/ref-404").status_code == 404


def test_tracked_changes_are_reported_as_an_accept_all_read():
    client = TestClient(create_app())
    body = _attach(client).json()
    # No revisions in this fixture, so nothing should be claimed.
    assert body["reference_doc"]["tracked_changes"] is False
    assert body["warnings"] == []


# ---------------------------------------------------------------------------
# Token discipline — the design's spine
# ---------------------------------------------------------------------------


def test_only_a_stub_reaches_the_per_turn_context():
    client = TestClient(create_app())
    _attach(client)

    context = _turn_context_text(sessions.get_session())

    assert "REFERENCE DOCUMENTS" in context
    assert "ref-1" in context
    # The body must never ride a block that is re-billed every single turn.
    assert BODY_MARKER not in context


def test_no_reference_block_when_none_are_attached():
    assert "REFERENCE DOCUMENTS" not in _turn_context_text(
        sessions.get_session()
    )


def test_the_tool_returns_the_full_text():
    client = TestClient(create_app())
    _attach(client)

    result = _read_tool(sessions.get_session(), "ref-1")

    assert not result.get("is_error")
    assert BODY_MARKER in result["content"]


def test_an_unknown_id_is_a_correctable_tool_error_not_a_turn_failure():
    client = TestClient(create_app())
    _attach(client)

    result = _read_tool(sessions.get_session(), "ref-99")

    assert result["is_error"] is True
    # It must tell the model what it can actually read instead.
    assert "ref-1" in result["content"]


def test_the_body_is_elided_from_committed_history():
    """The fetched-PDF posture: read once, pay once. Leaving the text in
    history would re-bill it on every later turn and bloat the project file."""
    client = TestClient(create_app())
    _attach(client)
    result = _read_tool(sessions.get_session(), "ref-1")

    committed = _committed_messages(
        [
            {"role": "user", "content": [{"type": "text", "text": "ctx\nhi"}]},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tu1",
                        "name": "read_reference_doc",
                        "input": {"ref_id": "ref-1"},
                    }
                ],
            },
            {"role": "user", "content": [result]},
            {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
        ],
        "hi",
    )
    blob = str(committed)

    assert BODY_MARKER not in blob
    # The call itself stays readable, and the model is told it can re-read.
    assert "read_reference_doc" in blob
    assert "read_reference_doc again" in blob


def test_an_error_result_is_left_intact_in_history():
    """Only bodies are elided; a correction the model should learn from is
    small and must survive."""
    client = TestClient(create_app())
    _attach(client)
    error = _read_tool(sessions.get_session(), "ref-99")

    committed = _committed_messages(
        [
            {"role": "user", "content": [{"type": "text", "text": "ctx\nhi"}]},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tu1",
                        "name": "read_reference_doc",
                        "input": {"ref_id": "ref-99"},
                    }
                ],
            },
            {"role": "user", "content": [error]},
            {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
        ],
        "hi",
    )

    assert "no reference document with id" in str(committed)


def test_unrelated_tool_results_are_untouched_by_the_elision():
    committed = _committed_messages(
        [
            {"role": "user", "content": [{"type": "text", "text": "ctx\nhi"}]},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tu9",
                        "name": "apply_spec_edits",
                        "input": {"edits": []},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu9",
                        "content": "applied 1 op",
                    }
                ],
            },
            {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
        ],
        "hi",
    )

    assert "applied 1 op" in str(committed)


# ---------------------------------------------------------------------------
# Persistence and lifecycle
# ---------------------------------------------------------------------------


def test_attached_references_count_as_unsaved_work():
    """A user can attach a standard before typing anything; losing it to an
    unprompted New session would be exactly the loss the save gate exists to
    prevent."""
    client = TestClient(create_app())
    assert client.get("/api/session/unsaved").json()["unsaved"] is False

    _attach(client)

    assert client.get("/api/session/unsaved").json()["unsaved"] is True
    assert sessions.has_unsaved_progress(sessions.get_session()) is True


def test_references_survive_a_project_round_trip():
    client = TestClient(create_app())
    _attach(client)
    session = sessions.get_session()

    payload = sessions.project_payload(session)
    load_project(payload, session)

    assert [d["rid"] for d in session.references.snapshot()] == ["ref-1"]
    # The body came back too, not just the metadata.
    assert BODY_MARKER in _read_tool(session, "ref-1")["content"]


def test_loading_a_project_without_references_clears_the_previous_session():
    client = TestClient(create_app())
    _attach(client)
    session = sessions.get_session()
    payload = sessions.project_payload(session)
    payload.pop("reference_docs", None)

    load_project(payload, session)

    assert session.references.snapshot() == []


def test_reset_clears_attached_references():
    client = TestClient(create_app())
    _attach(client)

    client.post("/api/session/reset")

    assert sessions.get_session().references.snapshot() == []
    assert client.get("/api/doc").json()["reference_docs"] == []


def test_the_policy_is_in_the_stable_prompt_and_the_data_is_not():
    """The cache rule: guidance is module-stable, session content is not."""
    from backend.llm.prompts import render_system_prompt

    client = TestClient(create_app())
    _attach(client)
    session = sessions.get_session()

    stable = render_system_prompt(session.module)

    # The policy names the tool (and illustrates the id format) but must carry
    # nothing about what is actually attached to THIS session.
    assert "read_reference_doc" in stable
    assert "acme.docx" not in stable
    assert BODY_MARKER not in stable
    # Whereas the live list does reach the model — through PROJECT CONTEXT.
    assert "acme.docx" in _turn_context_text(session)
