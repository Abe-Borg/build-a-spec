"""Fetched web-page text stays out of saved chat history (compaction plan,
Phase 2; owner decision D2).

A chat ``web_fetch`` can leave tens of thousands of tokens of page text in
the conversation, and everything committed is re-sent on every later turn.
Within the turn the model is still reading the page, so it keeps it; once
the turn commits, the saved form keeps the fetch (URL, title, retrieval
time, citation setting) and a note in place of the text, and the reply
keeps the passages it quoted in its citations. These tests pin both
halves, the same trim applied to a project saved by an earlier build, and
that a fetched PDF keeps the note its own elision already wrote.

1.21.0 ships the trim SWITCHED OFF (``settings.ELIDE_FETCHED_PAGE_TEXT``,
env ``BUILD_A_SPEC_ELIDE_FETCHED_PAGES``): its live canary was never run,
and a saved history the provider refused would fail every later message in
that project. So the trim's own tests run with the switch on (the autouse
fixture below), and the last section pins the shipped state: the default is
off, and with it off commit, project load and the profiler all keep the page
text exactly as they did before this phase.
"""
from __future__ import annotations

import ast
import base64
import copy
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend import sessions, settings
from backend.app import create_app
from backend.llm.conversation import _committed_messages
from backend.llm.history_hygiene import (
    FETCHED_PAGE_CATEGORY,
    FETCHED_PAGE_NOTE,
    PDF_ELISION_NOTE_PREFIX,
    count_fetched_page_texts,
    count_stale_outlines,
    elide_fetched_page_text,
    history_composition,
)
from backend.research import resend_sanitizer
from backend.spec_doc.project import chat_transcript
from tests.fakes import FakeClient, raw_turn, text_block, text_turn, tool_use_block
from tests.test_app import _SEED_EDITS, _parse_sse, _patch_client

_URL = "https://example.test/owner/equipment-guide"
_TITLE = "Owner equipment guide"
_USE_ID = "srvtoolu_fetch_guide_1"
_CITED = "Spare parts must be kept on site for ten years."
# Appears only in the page body — never in the cited sentence, the reply or
# the note — so its presence anywhere means page text survived.
_FILLER = "FILLER-PARAGRAPH-TEXT"
_PAGE = (
    "".join(f"{_FILLER} {n}: background the reply never needed.\n" for n in range(40))
    + _CITED
    + "\nEnd of page.\n"
)
_CITED_START = _PAGE.index(_CITED)
_CITED_END = _CITED_START + len(_CITED)


@pytest.fixture(autouse=True)
def _page_text_trim_on(monkeypatch):
    """The trim's behaviour is what most of this module pins, so it runs
    with the switch on. The shipped default is off; the tests at the end
    turn it back off explicitly."""
    monkeypatch.setattr(settings, "ELIDE_FETCHED_PAGE_TEXT", True)


def _client() -> TestClient:
    return TestClient(create_app())


def _chat(client: TestClient, message: str) -> list[dict]:
    resp = client.post("/api/chat", json={"message": message})
    events = _parse_sse(resp.text)
    assert events[-1]["type"] == "turn_complete", events[-1]
    return events


def _fetch_result(data: str, *, url: str = _URL) -> dict:
    return {
        "type": "web_fetch_result",
        "url": url,
        "retrieved_at": "2026-09-22T10:00:00Z",
        "content": {
            "type": "document",
            "source": {"type": "text", "media_type": "text/plain", "data": data},
            "title": _TITLE,
            "citations": {"enabled": True},
        },
    }


def _citation() -> dict:
    return {
        "type": "char_location",
        "cited_text": _CITED,
        "document_index": 0,
        "document_title": _TITLE,
        "start_char_index": _CITED_START,
        "end_char_index": _CITED_END,
    }


def _fetch_turn() -> FakeClient:
    """Fetch the page, edit the draft (a second round), then cite the page."""
    return FakeClient(
        [
            raw_turn(
                [
                    text_block("Reading the owner's guide."),
                    SimpleNamespace(
                        type="server_tool_use",
                        id=_USE_ID,
                        name="web_fetch",
                        input={"url": _URL},
                    ),
                    SimpleNamespace(
                        type="web_fetch_tool_result",
                        tool_use_id=_USE_ID,
                        content=_fetch_result(_PAGE),
                    ),
                    tool_use_block("toolu_seed", "apply_spec_edits", _SEED_EDITS),
                ],
                stop_reason="tool_use",
            ),
            raw_turn(
                [
                    text_block("Per the owner's guide, "),
                    SimpleNamespace(
                        type="text",
                        text="spare parts stay on site for ten years",
                        citations=[_citation()],
                    ),
                    text_block("."),
                ],
                stop_reason="end_turn",
            ),
        ]
    )


def _blocks(history: list[dict], block_type: str) -> list[dict]:
    return [
        block
        for message in history
        for block in (message.get("content") or [])
        if isinstance(block, dict) and block.get("type") == block_type
    ]


def _cited_blocks(history: list[dict]) -> list[dict]:
    return [block for block in _blocks(history, "text") if block.get("citations")]


# ---------------------------------------------------------------------------
# Through the chat engine
# ---------------------------------------------------------------------------


def test_a_saved_turn_keeps_the_fetch_and_its_citation_but_not_the_page(
    monkeypatch,
):
    client = _client()
    fake = _fetch_turn()
    _patch_client(monkeypatch, fake)
    _chat(client, "Check the owner's equipment guide.")

    # Mid-turn, the continuation still carries the whole page: the model is
    # reading it while it drafts.
    continuation = json.dumps(fake.messages.requests[1]["messages"], ensure_ascii=False)
    assert _FILLER in continuation and _CITED in continuation

    history = sessions.get_session().history
    (fetch,) = _blocks(history, "web_fetch_tool_result")
    result = fetch["content"]
    assert fetch["tool_use_id"] == _USE_ID
    assert result["type"] == "web_fetch_result"
    assert result["url"] == _URL
    assert result["retrieved_at"] == "2026-09-22T10:00:00Z"
    document = result["content"]
    assert document["type"] == "document", "the document block itself stays"
    assert document["title"] == _TITLE
    assert document["citations"] == {"enabled": True}
    assert document["source"] == {
        "type": "text",
        "media_type": "text/plain",
        "data": FETCHED_PAGE_NOTE.format(url=_URL),
    }
    # The fetch call and the reply's quoted passage are kept exactly.
    (use,) = _blocks(history, "server_tool_use")
    assert use["input"] == {"url": _URL}
    (cited,) = _cited_blocks(history)
    assert cited["citations"] == [_citation()]
    assert _FILLER not in json.dumps(history, ensure_ascii=False)

    # The next turn re-sends the note, never the page, and keeps the quote.
    follow_up = FakeClient([text_turn(["Sure."])])
    _patch_client(monkeypatch, follow_up)
    _chat(client, "What's next?")
    history_part = json.dumps(
        follow_up.messages.last_request["messages"][:-1], ensure_ascii=False
    )
    assert _FILLER not in history_part
    assert FETCHED_PAGE_NOTE.format(url=_URL) in history_part
    assert _CITED in history_part

    # Developer tools reads the same canary Phase 1 does for outlines.
    makeup = client.get("/api/diagnostics").json()["session"]["history_composition"]
    assert makeup["fetched_page_texts"] == 0
    names = {c["category"] for c in makeup["categories"]}
    assert "fetched web pages" in names
    assert FETCHED_PAGE_CATEGORY not in names
    assert _FILLER not in json.dumps(makeup) and _URL not in json.dumps(makeup)


def test_a_fetched_pdf_keeps_the_note_its_own_elision_wrote():
    # The PDF elision's note is what the page-text elision must recognize.
    assert resend_sanitizer._ELISION_NOTE.startswith(PDF_ELISION_NOTE_PREFIX)
    pdf_url = "https://example.test/owner/guide.pdf"
    turn = [
        {"role": "user", "content": [{"type": "text", "text": "context + ask"}]},
        {
            "role": "assistant",
            "content": [
                {"type": "server_tool_use", "id": "srvtoolu_pdf_1", "name": "web_fetch",
                 "input": {"url": pdf_url}},
                {"type": "web_fetch_tool_result", "tool_use_id": "srvtoolu_pdf_1",
                 "content": {
                     "type": "web_fetch_result",
                     "url": pdf_url,
                     "content": {
                         "type": "document",
                         "source": {
                             "type": "base64",
                             "media_type": "application/pdf",
                             "data": base64.b64encode(b"%PDF-1.4 not a real pdf").decode(),
                         },
                         "citations": {"enabled": True},
                     },
                 }},
                {"type": "text", "text": "The PDF says so."},
            ],
        },
    ]
    committed = _committed_messages(turn, "ask")
    (fetch,) = _blocks(committed, "web_fetch_tool_result")
    data = fetch["content"]["content"]["source"]["data"]
    assert data.startswith(PDF_ELISION_NOTE_PREFIX)
    assert "page count could not be determined" in data
    # This URL makes the page note the shorter of the two, so only the
    # prefix check keeps it from overwriting what the PDF note records.
    assert len(FETCHED_PAGE_NOTE.format(url=pdf_url)) < len(data)


def test_projects_saved_before_the_elision_drop_page_text_when_opened(
    monkeypatch, caplog
):
    client = _client()
    _patch_client(monkeypatch, _fetch_turn())
    _chat(client, "Check the owner's equipment guide.")
    project = json.loads(json.dumps(sessions.project_payload(sessions.get_session())))

    # What an older build saved: the page text still inside the result.
    for message in project["history"]:
        for block in message["content"]:
            if block.get("type") == "web_fetch_tool_result":
                block["content"]["content"]["source"]["data"] = _PAGE
    assert _FILLER in json.dumps(project["history"], ensure_ascii=False)
    seen_before = chat_transcript(project["history"])

    client.post("/api/session/reset")
    with caplog.at_level(logging.INFO, logger="buildaspec.project"):
        loaded = client.post("/api/project/load", json=project)
    assert loaded.status_code == 200, loaded.text
    assert "Dropped the text of 1 fetched web page(s)" in caplog.text

    history = sessions.get_session().history
    (fetch,) = _blocks(history, "web_fetch_tool_result")
    assert fetch["content"]["content"]["source"]["data"] == FETCHED_PAGE_NOTE.format(url=_URL)
    assert _cited_blocks(history)[0]["citations"] == [_citation()]
    # Nothing the user sees changed.
    assert loaded.json()["chat"] == seen_before
    # The next save writes the trimmed form.
    resaved = json.dumps(
        sessions.project_payload(sessions.get_session())["history"], ensure_ascii=False
    )
    assert _FILLER not in resaved
    assert FETCHED_PAGE_NOTE.format(url=_URL) in resaved


# ---------------------------------------------------------------------------
# The elision itself
# ---------------------------------------------------------------------------


def _fetch_block(use_id: str, content) -> dict:
    return {"type": "web_fetch_tool_result", "tool_use_id": use_id, "content": content}


def _history() -> list[dict]:
    """One of every fetch-result shape the elision must tell apart."""
    pdf_note = resend_sanitizer._ELISION_NOTE.format(detail="this document is 3 pages.", limit=600)
    return [
        {"role": "user", "content": [{"type": "text", "text": "Look these up."}]},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Fetching."},
                _fetch_block("s-page", _fetch_result(_PAGE)),
                # A page no longer than its note: replacing it would grow it.
                _fetch_block("s-short", _fetch_result("Short page.")),
                # An error result carries no page.
                _fetch_block("s-err", {"type": "web_fetch_tool_result_error",
                                       "error_code": "url_not_accessible"}),
                # A fetched PDF is the PDF elision's job, not this one's.
                _fetch_block("s-pdf", {
                    "type": "web_fetch_result", "url": "https://example.test/a.pdf",
                    "content": {"type": "document", "source": {
                        "type": "base64", "media_type": "application/pdf", "data": "JVBERi0x"}}}),
                # ...and a PDF it already turned into a note stays that note.
                _fetch_block("s-pdfnote", {
                    "type": "web_fetch_result", "url": "https://example.test/b.pdf",
                    "content": {"type": "document", "source": {
                        "type": "text", "media_type": "text/plain", "data": pdf_note}}}),
                {"type": "text", "text": "Cited.", "citations": [_citation()]},
            ],
        },
        # Server results never sit in user messages; one there is left alone.
        {"role": "user", "content": [_fetch_block("s-user", _fetch_result(_PAGE))]},
        {"role": "assistant", "content": [{"type": "text", "text": "Done."}]},
    ]


def test_page_text_elision_is_copy_on_write_idempotent_and_scoped():
    history = _history()
    before = copy.deepcopy(history)
    assert count_fetched_page_texts(history) == 1

    trimmed = elide_fetched_page_text(history)
    assert trimmed is not history
    assert history == before, "the input must never be mutated"

    blocks = {b.get("tool_use_id"): b for b in trimmed[1]["content"]}
    page = blocks["s-page"]
    expected = copy.deepcopy(before[1]["content"][1])
    expected["content"]["content"]["source"]["data"] = FETCHED_PAGE_NOTE.format(url=_URL)
    assert page == expected, "only the page text changes"
    for kept in ("s-short", "s-err", "s-pdf", "s-pdfnote"):
        original = next(b for b in before[1]["content"] if b.get("tool_use_id") == kept)
        assert blocks[kept] == original, kept
    assert trimmed[1]["content"][-1] == before[1]["content"][-1], "citations untouched"
    # Untouched messages are the same objects, not copies.
    assert trimmed[0] is history[0] and trimmed[2] is history[2]
    assert trimmed[3] is history[3]

    # A second pass finds nothing — and says so by returning the same list.
    assert count_fetched_page_texts(trimmed) == 0
    assert elide_fetched_page_text(trimmed) is trimmed
    plain = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    assert elide_fetched_page_text(plain) is plain


def test_history_composition_separates_removable_page_text():
    history = _history()
    composition = history_composition(history)
    by_name = {c["category"]: c for c in composition["categories"]}
    assert composition["fetched_page_texts"] == 1
    assert by_name[FETCHED_PAGE_CATEGORY]["blocks"] == 1
    assert "fetched web pages" in by_name

    # Sizes only.
    dumped = json.dumps(composition)
    for private in (_FILLER, _CITED, _URL, "Look these up"):
        assert private not in dumped

    # The trim removes exactly that category and nothing else moves.
    after = history_composition(elide_fetched_page_text(history))
    assert FETCHED_PAGE_CATEGORY not in {c["category"] for c in after["categories"]}
    assert after["fetched_page_texts"] == 0
    assert after["chars"] == composition["chars"] - by_name[FETCHED_PAGE_CATEGORY]["chars"]


def test_the_profiler_reports_fetched_page_text(monkeypatch, tmp_path):
    from tools import chat_history_profile

    client = _client()
    _patch_client(monkeypatch, _fetch_turn())
    _chat(client, "Check the owner's equipment guide.")
    project = json.loads(json.dumps(sessions.project_payload(sessions.get_session())))
    for message in project["history"]:
        for block in message["content"]:
            if block.get("type") == "web_fetch_tool_result":
                block["content"]["content"]["source"]["data"] = _PAGE
    path = tmp_path / "client-name-project.json"
    path.write_text(json.dumps(project), encoding="utf-8")
    out = tmp_path / "measurement.md"

    assert chat_history_profile.main([str(path), "--out", str(out)]) == 0
    report = out.read_text(encoding="utf-8")
    assert "| Fetched pages |" in report
    assert "Fetched web pages still carrying their text: 1" in report
    # "Now" is what this build sends: the page text is gone from it.
    assert FETCHED_PAGE_CATEGORY not in report.split("By category, as this build sends it:")[1]
    for private in ("client-name", _FILLER, _CITED, _URL):
        assert private not in report


# ---------------------------------------------------------------------------
# The shipped state: switched off until the live canary passes
# ---------------------------------------------------------------------------


def _older_project_with_page_text(client: TestClient, monkeypatch) -> dict:
    _patch_client(monkeypatch, _fetch_turn())
    _chat(client, "Check the owner's equipment guide.")
    project = json.loads(json.dumps(sessions.project_payload(sessions.get_session())))
    for message in project["history"]:
        for block in message["content"]:
            if block.get("type") == "web_fetch_tool_result":
                block["content"]["content"]["source"]["data"] = _PAGE
    return project


def test_the_page_text_trim_ships_switched_off():
    """1.21.0 ships the trim OFF: its live canary
    (``tools/fetch_elision_canary.py --run``) was never run, and a history
    the provider refused would fail every later message in that project.
    Read from the source, not the loaded value, so a developer's own
    environment cannot make this pass or fail. Change this expectation only
    together with a recorded canary pass (the compaction plan's Phase 2 →
    Canary result)."""
    source = (Path(settings.__file__)).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "ELIDE_FETCHED_PAGE_TEXT"
            for target in node.targets
        ):
            call = node.value
            assert isinstance(call, ast.Call) and getattr(call.func, "id", "") == "_bool_env"
            name, default = (arg.value for arg in call.args)
            assert name == "BUILD_A_SPEC_ELIDE_FETCHED_PAGES"
            assert default is False, "the trim may only default on after a recorded canary pass"
            return
    raise AssertionError("settings.ELIDE_FETCHED_PAGE_TEXT is gone")


def test_with_the_trim_off_a_saved_turn_keeps_the_page(monkeypatch):
    monkeypatch.setattr(settings, "ELIDE_FETCHED_PAGE_TEXT", False)
    client = _client()
    _patch_client(monkeypatch, _fetch_turn())
    _chat(client, "Check the owner's equipment guide.")

    history = sessions.get_session().history
    (fetch,) = _blocks(history, "web_fetch_tool_result")
    assert fetch["content"]["content"]["source"]["data"] == _PAGE
    (cited,) = _cited_blocks(history)
    assert cited["citations"] == [_citation()]
    # Only the page-text trim is off: the edit's outline still leaves.
    assert count_stale_outlines(history) == 0

    # The next turn re-sends the page, exactly as before this phase.
    follow_up = FakeClient([text_turn(["Sure."])])
    _patch_client(monkeypatch, follow_up)
    _chat(client, "What's next?")
    history_part = json.dumps(
        follow_up.messages.last_request["messages"][:-1], ensure_ascii=False
    )
    assert _FILLER in history_part
    assert FETCHED_PAGE_NOTE.format(url=_URL) not in history_part

    # Developer tools counts the page the history keeps.
    makeup = client.get("/api/diagnostics").json()["session"]["history_composition"]
    assert makeup["fetched_page_texts"] == 1


def test_with_the_trim_off_an_opened_project_keeps_its_pages(monkeypatch, caplog):
    monkeypatch.setattr(settings, "ELIDE_FETCHED_PAGE_TEXT", False)
    client = _client()
    project = _older_project_with_page_text(client, monkeypatch)

    client.post("/api/session/reset")
    with caplog.at_level(logging.INFO, logger="buildaspec.project"):
        loaded = client.post("/api/project/load", json=project)
    assert loaded.status_code == 200, loaded.text
    assert "fetched web page" not in caplog.text

    history = sessions.get_session().history
    (fetch,) = _blocks(history, "web_fetch_tool_result")
    assert fetch["content"]["content"]["source"]["data"] == _PAGE


def test_with_the_trim_off_the_profiler_reports_the_pages_as_kept(monkeypatch, tmp_path):
    from tools import chat_history_profile

    monkeypatch.setattr(settings, "ELIDE_FETCHED_PAGE_TEXT", False)
    client = _client()
    project = _older_project_with_page_text(client, monkeypatch)
    path = tmp_path / "project.json"
    path.write_text(json.dumps(project), encoding="utf-8")
    out = tmp_path / "measurement.md"

    assert chat_history_profile.main([str(path), "--out", str(out)]) == 0
    report = out.read_text(encoding="utf-8")
    assert "Fetched web pages still carrying their text: 1" in report
    assert "kept: the page-text trim is switched off" in report
    # "Now" is what this build sends, and with the trim off that is the page.
    assert FETCHED_PAGE_CATEGORY in report.split("By category, as this build sends it:")[1]
