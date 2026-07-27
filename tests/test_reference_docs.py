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

import pytest
from docx import Document
from fastapi.testclient import TestClient

from backend import reference_extract, sessions
from backend.app import create_app
from backend.llm.conversation import (
    _committed_messages,
    _run_tool,
    _turn_context_text,
)
from backend.reference_docs import (
    MAX_REFERENCE_DOCS,
    MAX_REFERENCE_TOKENS,
    MAX_TEXT_CHARS,
    ReferenceDocError,
    ReferenceDocStore,
    TurnReferenceBudget,
    prepare_reference_text,
)
from backend.spec_doc.project import load_project


def test_reference_store_enforces_cumulative_anthropic_token_limit():
    store = ReferenceDocStore()
    first = store.add(
        filename="one.txt",
        text="one",
        block_count=1,
        token_count=MAX_REFERENCE_TOKENS - 1,
    )
    assert first.metadata()["token_count"] == MAX_REFERENCE_TOKENS - 1

    with pytest.raises(ReferenceDocError, match="100,001 tokens"):
        store.add(filename="two.txt", text="two", block_count=1, token_count=2)

    assert [doc.rid for doc in store.docs] == ["ref-1"]


def test_legacy_reference_without_token_count_reserves_quota():
    store = ReferenceDocStore()
    store.load(
        {
            "reference_docs": [
                {"rid": "ref-1", "filename": "old.txt", "text": "legacy body"}
            ]
        }
    )

    # Legacy projects load offline, so reserve at least one token per UTF-8
    # byte (plus message overhead) rather than treating attachments as free.
    assert store.docs[0].token_count > len(b"legacy body")
    with pytest.raises(ReferenceDocError, match="maximum is 100,000"):
        store.add(
            filename="new.txt",
            text="new",
            block_count=1,
            token_count=MAX_REFERENCE_TOKENS,
        )


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


def _pdf_bytes(pages: list[list[str]]) -> bytes:
    """A minimal, valid, text-bearing PDF — no new test dependency.

    Hand-built because the repo has no PDF writer and pypdf cannot draw text:
    one Helvetica font, one content stream per page, a real xref table. Enough
    for ``extract_text`` to return exactly the lines handed in.
    """
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    font_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    content_ids = []
    for lines in pages:
        parts = [b"BT", b"/F1 12 Tf", b"72 720 Td"]
        for line in lines:
            escaped = (
                line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
            ).encode("latin-1", "replace")
            parts += [b"(" + escaped + b") Tj", b"0 -14 Td"]
        parts.append(b"ET")
        stream = b"\n".join(parts)
        content_ids.append(
            add(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
        )
    # The page objects have to name their parent, which is written after them.
    pages_id = len(objects) + len(pages) + 1
    page_ids = [
        add(
            b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
            % (pages_id, font_id, content_id)
        )
        for content_id in content_ids
    ]
    add(
        b"<< /Type /Pages /Kids [%s] /Count %d >>"
        % (b" ".join(b"%d 0 R" % pid for pid in page_ids), len(page_ids))
    )
    catalog_id = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id)

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % index + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        catalog_id,
        xref_at,
    )
    return bytes(out)


def _upload(client: TestClient, name: str, payload: bytes):
    return client.post(
        "/api/reference/upload",
        files={"file": (name, payload, "application/octet-stream")},
    )


def _attach(client: TestClient, name: str = "acme.docx", lines=None):
    # `is None`, not falsy: an explicit [] is the empty-document case.
    body = _docx_bytes(STANDARD if lines is None else lines)
    return client.post(
        "/api/reference/upload",
        files={"file": (name, body, DOCX_MEDIA_TYPE)},
    )


def _attach_pdf(client: TestClient, name: str = "acme.pdf", pages=None):
    return _upload(client, name, _pdf_bytes([STANDARD] if pages is None else pages))


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


def test_token_counter_payload_is_the_exact_retained_truncated_text():
    source = "x" * (MAX_TEXT_CHARS + 5_000)
    retained, total, truncated = prepare_reference_text(source)
    doc = ReferenceDocStore().add(
        filename="huge.txt", text=source, block_count=1, token_count=12
    )

    assert truncated is True
    assert total == len(source)
    assert retained == doc.text
    assert len(retained) < len(source)


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


def test_an_unsupported_type_is_refused_and_names_what_is_accepted():
    client = TestClient(create_app())

    refused = client.post(
        "/api/reference/upload",
        files={"file": ("slides.pptx", b"anything", "application/octet-stream")},
    )

    assert refused.status_code == 400
    error = refused.json()["error"]
    for extension in (".docx", ".pdf", ".txt", ".xml", ".csv"):
        assert extension in error


def test_an_unreadable_file_of_a_supported_type_is_refused():
    """The extension only chooses the extractor; the bytes still have to be
    what they claim."""
    client = TestClient(create_app())

    assert (
        client.post(
            "/api/reference/upload",
            files={"file": ("broken.docx", b"not a zip", DOCX_MEDIA_TYPE)},
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/reference/upload",
            files={"file": ("broken.pdf", b"not a pdf", "application/pdf")},
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


def test_remove_forgets_the_turn_that_read_the_document_and_every_later_turn():
    client = TestClient(create_app())
    _attach(client)
    session = sessions.get_session()
    session.history.extend(
        [
            {"role": "user", "content": [{"type": "text", "text": "safe"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "safe reply"}]},
            {"role": "user", "content": [{"type": "text", "text": "read it"}]},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "read-1",
                        "name": "read_reference_doc",
                        "input": {"ref_id": "ref-1"},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "read-1",
                        "content": "[Reference document text omitted from history]",
                    }
                ],
            },
            {"role": "assistant", "content": [{"type": "text", "text": BODY_MARKER}]},
            {"role": "user", "content": [{"type": "text", "text": "later"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "later reply"}]},
        ]
    )

    removed = client.delete("/api/reference/ref-1")

    assert removed.status_code == 200
    assert session.history == [
        {"role": "user", "content": [{"type": "text", "text": "safe"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "safe reply"}]},
    ]
    assert "REFERENCE DOCUMENTS" not in _turn_context_text(session)
    assert BODY_MARKER not in str(session.history)


def test_remove_does_not_cross_an_active_model_turn():
    client = TestClient(create_app())
    _attach(client)
    session = sessions.get_session()
    session.turn_active = True

    refused = client.delete("/api/reference/ref-1")

    assert refused.status_code == 409
    assert session.references.get("ref-1") is not None


def test_the_attachment_keeps_its_own_extension():
    """The shared filename sanitizer defaults to the imported-master case and
    appends ``.docx`` to anything else — which would rename a PDF and then
    send it to the Word extractor."""
    client = TestClient(create_app())

    body = _attach_pdf(client, "acme-standard.pdf").json()

    assert body["reference_doc"]["filename"] == "acme-standard.pdf"


def test_tracked_changes_are_reported_as_an_accept_all_read():
    client = TestClient(create_app())
    body = _attach(client).json()
    # No revisions in this fixture, so nothing should be claimed.
    assert body["reference_doc"]["tracked_changes"] is False
    assert body["warnings"] == []


# ---------------------------------------------------------------------------
# File types — background material arrives in whatever the office already has
# ---------------------------------------------------------------------------


def test_every_supported_type_attaches_and_keeps_its_text():
    client = TestClient(create_app())
    uploads = [
        ("acme.docx", _docx_bytes(STANDARD), "docx"),
        ("acme.pdf", _pdf_bytes([STANDARD]), "pdf"),
        ("acme.txt", "\n".join(STANDARD).encode(), "txt"),
        ("acme.csv", b"system,supply temp\nchilled water,44 degrees F", "csv"),
        (
            "acme.xml",
            b"<standard><chw>44 degrees F at design.</chw></standard>",
            "xml",
        ),
    ]

    for index, (name, payload, kind) in enumerate(uploads, start=1):
        response = client.post(
            "/api/reference/upload",
            files={"file": (name, payload, "application/octet-stream")},
        )
        assert response.status_code == 200, (name, response.json())
        assert response.json()["reference_doc"]["kind"] == kind
        # Every one of them is readable in full through the tool.
        result = _read_tool(sessions.get_session(), f"ref-{index}")
        assert not result.get("is_error")
        assert BODY_MARKER in result["content"], name


def test_structure_is_the_content_for_csv_and_xml():
    """Neither is flattened or parsed away: a CSV's rows and an XML's tags are
    exactly what make the file worth reading."""
    client = TestClient(create_app())
    csv = b"system,supply temp,redundancy\nchilled water,44 F,N+1\n"
    xml = b'<plant type="chilled-water">\n  <supply>44 F</supply>\n</plant>\n'

    _upload(client, "schedule.csv", csv)
    _upload(client, "plant.xml", xml)
    session = sessions.get_session()

    assert "system,supply temp,redundancy" in _read_tool(session, "ref-1")["content"]
    assert '<plant type="chilled-water">' in _read_tool(session, "ref-2")["content"]


def test_a_pdf_is_read_page_by_page_with_page_markers():
    """The markers are what let the model answer "where does it say that?"
    with a page number."""
    client = TestClient(create_app())
    _attach_pdf(
        client, pages=[["ACME STANDARD", BODY_MARKER], ["Second page text."]]
    )

    text = _read_tool(sessions.get_session(), "ref-1")["content"]

    assert "[page 1]" in text
    assert "[page 2]" in text
    assert text.index("[page 1]") < text.index(BODY_MARKER) < text.index("[page 2]")


def test_a_pdf_with_no_text_layer_is_refused_with_the_reason():
    """A scan attached silently as an empty document is worse than an error:
    the model would be told it holds material it cannot read."""
    client = TestClient(create_app())

    refused = _attach_pdf(client, pages=[[]])

    assert refused.status_code == 400
    assert "scanned" in refused.json()["error"].lower()


def test_a_password_protected_pdf_is_refused_with_the_reason():
    from pypdf import PdfWriter

    client = TestClient(create_app())
    writer = PdfWriter(clone_from=io.BytesIO(_pdf_bytes([STANDARD])))
    writer.encrypt(user_password="secret")
    buffer = io.BytesIO()
    writer.write(buffer)

    refused = _upload(client, "locked.pdf", buffer.getvalue())

    assert refused.status_code == 400
    assert "password-protected" in refused.json()["error"]


def test_binary_content_behind_a_text_extension_is_refused():
    """The decode ladder ends in Latin-1, which never raises, so this is the
    only place a renamed binary can be caught."""
    client = TestClient(create_app())

    refused = _upload(client, "sneaky.txt", b"\x89PNG\r\n\x1a\n\x00\x00\x00rest")

    assert refused.status_code == 400
    assert "readable text" in refused.json()["error"]


def test_a_non_utf8_text_file_is_read_and_the_fallback_is_reported():
    """Windows is the primary platform: a hand-saved .txt or an Excel .csv
    export is routinely Windows-1252 or UTF-16, and refusing them would be
    refusing files the user can plainly read."""
    client = TestClient(create_app())

    latin = _upload(
        client, "notes.txt", "Chilled water — 44 degrees F".encode("cp1252")
    )
    utf16 = _upload(client, "export.csv", "system,44 degrees F".encode("utf-16"))

    assert latin.status_code == 200
    assert utf16.status_code == 200
    assert any("Windows-1252" in w for w in latin.json()["warnings"])
    assert any("UTF-16" in w for w in utf16.json()["warnings"])
    session = sessions.get_session()
    assert "—" in _read_tool(session, "ref-1")["content"]
    assert BODY_MARKER in _read_tool(session, "ref-2")["content"]


def test_a_long_pdf_is_bounded_and_says_so():
    client = TestClient(create_app())
    pages = [[f"page {n} body text"] for n in range(reference_extract.MAX_PDF_PAGES + 3)]

    body = _attach_pdf(client, pages=pages).json()

    assert any("pages were read" in w for w in body["warnings"])
    text = _read_tool(sessions.get_session(), "ref-1")["content"]
    assert f"[page {reference_extract.MAX_PDF_PAGES}]" in text
    assert f"[page {reference_extract.MAX_PDF_PAGES + 1}]" not in text
    # The model must be told, not left to infer it from a missing page.
    assert "were not read" in text


def test_the_type_reaches_the_panel_the_stub_and_the_tool_header():
    """How a reference should be read depends on what it is — a CSV is data,
    a PDF carries page markers, a Word standard is prose."""
    client = TestClient(create_app())
    _attach_pdf(client, "acme.pdf")
    session = sessions.get_session()

    metadata = client.get("/api/references").json()["reference_docs"][0]
    assert metadata["kind"] == "pdf"
    assert metadata["kind_label"] == "PDF"
    assert "PDF" in session.references.context_stubs()
    assert "page markers" in session.references.context_stubs()
    assert "PDF" in _read_tool(session, "ref-1")["content"]


def test_a_project_saved_before_types_existed_reads_as_word():
    """The store shipped Word-only, so an entry without a kind can only be a
    Word attachment."""
    store = ReferenceDocStore()
    store.load({"reference_docs": [{"rid": "ref-1", "text": "body"}]})

    assert store.docs[0].kind == "docx"
    assert store.docs[0].kind_label() == "Word"


def test_the_type_survives_a_project_round_trip():
    client = TestClient(create_app())
    _attach_pdf(client)
    session = sessions.get_session()

    load_project(sessions.project_payload(session), session)

    assert session.references.docs[0].kind == "pdf"


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


def test_one_turn_cannot_pull_in_unbounded_reference_text():
    """Elision runs at commit, so every body read this turn stays in the
    continuation request. Without a cumulative ceiling a model comparing
    several large attachments overruns the context and fails the turn on an
    opaque API error instead of something it can act on."""
    session = sessions.get_session()
    for i in range(3):
        session.references.add(
            filename=f"doc{i}.docx", text="x" * 400, block_count=1
        )
    # Explicit arithmetic: two 400-char reads fit in 1000, the third cannot.
    budget = TurnReferenceBudget(limit=1_000)

    results = [
        _run_tool(
            session,
            {
                "name": "read_reference_doc",
                "id": f"tu{i}",
                "input": {"ref_id": f"ref-{i + 1}"},
            },
            reference_budget=budget,
        )[0]
        for i in range(3)
    ]

    assert not results[0].get("is_error")
    assert not results[1].get("is_error")
    # The third is refused, and told what to do instead — not a turn failure.
    assert results[2]["is_error"] is True
    assert "per-turn budget" in results[2]["content"]
    assert "Nothing was read" in results[2]["content"]
    assert budget.spent <= budget.limit


def test_the_shipped_budget_stops_two_maximal_documents_in_one_turn():
    """The case that motivated the ceiling: several huge attachments read in a
    single turn. The default limits must actually prevent it, while still
    letting one full-size document through."""
    session = sessions.get_session()
    for i in range(2):
        session.references.add(
            filename=f"huge{i}.docx", text="x" * MAX_TEXT_CHARS, block_count=1
        )
    budget = TurnReferenceBudget()

    first = _run_tool(
        session,
        {"name": "read_reference_doc", "id": "a", "input": {"ref_id": "ref-1"}},
        reference_budget=budget,
    )[0]
    second = _run_tool(
        session,
        {"name": "read_reference_doc", "id": "b", "input": {"ref_id": "ref-2"}},
        reference_budget=budget,
    )[0]

    assert not first.get("is_error"), "one full-size document must still read"
    assert second["is_error"] is True


def test_the_turn_budget_is_per_turn_not_per_session():
    """A refusal must not poison later turns — each gets a fresh allowance."""
    client = TestClient(create_app())
    session = sessions.get_session()
    session.references.add(
        filename="big.docx", text="x" * (MAX_TEXT_CHARS - 1), block_count=1
    )

    spent = TurnReferenceBudget()
    spent.take(spent.limit)
    refused = _run_tool(
        session,
        {"name": "read_reference_doc", "id": "t", "input": {"ref_id": "ref-1"}},
        reference_budget=spent,
    )[0]
    fresh = _run_tool(
        session,
        {"name": "read_reference_doc", "id": "t", "input": {"ref_id": "ref-1"}},
        reference_budget=TurnReferenceBudget(),
    )[0]

    assert refused["is_error"] is True
    assert not fresh.get("is_error")


def test_a_refused_read_charges_nothing():
    """A document that does not fit must not consume budget on its way out."""
    session = sessions.get_session()
    session.references.add(
        filename="big.docx", text="x" * (MAX_TEXT_CHARS - 1), block_count=1
    )
    session.references.add(filename="small.docx", text="tiny", block_count=1)
    budget = TurnReferenceBudget(limit=1_000)

    refused = _run_tool(
        session,
        {"name": "read_reference_doc", "id": "a", "input": {"ref_id": "ref-1"}},
        reference_budget=budget,
    )[0]
    assert refused["is_error"] is True
    assert budget.spent == 0
    # The small one still fits afterwards.
    assert not _run_tool(
        session,
        {"name": "read_reference_doc", "id": "b", "input": {"ref_id": "ref-2"}},
        reference_budget=budget,
    )[0].get("is_error")


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
