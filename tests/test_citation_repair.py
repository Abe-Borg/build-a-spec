"""Every document citation a chat request carries points at a document the
request holds (``backend/llm/citations.py``).

The fetch-elision canary's first run (2026-09-23) showed the provider checks
a citation against the document its ``document_index`` lands on: it refused
a saved chat with "Start index 2406 is beyond document length 257". The
index counts every document in the request, so anything that changes the
request's documents under a saved citation can break it:

- a condensed conversation's view leaves its oldest turns out, and the
  pages they fetched with them, so every later citation lands early (the
  compaction plan's Phase 3 shipped without allowing for this);
- a reply answered under one view carries that view's numbers into saved
  history, and they are wrong under any other;
- a fetched PDF becomes a plain-text note when its turn is saved, so a page
  citation into it points at a document with no pages;
- the page-text trim (Phase 2) replaces a page with a short note.

The request repair re-points a citation at the earlier document it fits, or
drops it; the trim folds what a citation quoted into the page's note. These
tests pin the premise (the unrepaired shapes break the provider's rule),
both mechanisms, that an ordinary request is untouched, that the repair
never changes a message an earlier request already sent (the cached
prefix), and the whole thing through the chat engine and the summary call.
"""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace
from typing import Any

from backend import sessions, settings
from backend.app import create_app
from backend.llm import conversation
from backend.llm.citations import (
    citation_fits,
    repair_document_citations,
    request_documents,
    same_passage,
)
from backend.llm.compaction import compacted_view, view_spec_for
from backend.llm.history_hygiene import (
    FETCHED_PAGE_NOTE,
    QUOTED_PASSAGES_HEADER,
    QUOTED_PASSAGES_MAX_CHARS,
    elide_fetched_page_text,
)
from backend.research import resend_sanitizer
from fastapi.testclient import TestClient
from tests.fakes import FakeClient, raw_turn, text_turn
from tests.test_app import _parse_sse, _patch_client
from tests.test_chat_compaction import _record_for


def _page(label: str, cited: str) -> str:
    """A fetched page whose cited sentence sits well past a note's length."""
    return (
        "".join(f"{label} filler line {n} that no reply ever quotes.\n" for n in range(30))
        + cited
        + "\nEnd of page.\n"
    )


_A_URL = "https://a.example/nfpa-adoption"
_B_URL = "https://b.example/ahj-amendments"
_A_TITLE = "State adoption table"
_B_TITLE = "AHJ amendment list"
_A_CITED = "The state adopted NFPA 13-2022 on January 1."
_B_CITED = "Amendment 4 requires a fire pump test header."
_A_PAGE = _page("A", _A_CITED)
_B_PAGE = _page("B", _B_CITED)


def _document(page: str, title: str) -> dict[str, Any]:
    return {
        "type": "document",
        "source": {"type": "text", "media_type": "text/plain", "data": page},
        "title": title,
        "citations": {"enabled": True},
    }


def _citation(page: str, cited: str, title: str, index: int) -> dict[str, Any]:
    start = page.index(cited)
    return {
        "type": "char_location",
        "cited_text": cited,
        "document_index": index,
        "document_title": title,
        "start_char_index": start,
        "end_char_index": start + len(cited),
    }


def _fetch_turn(
    n: int, url: str, page: str, title: str, cited: str, index: int
) -> list[dict[str, Any]]:
    """One typed turn: the ask, then a fetch and a reply citing the page."""
    use_id = f"srvtoolu_fetch_{n}"
    return [
        {"role": "user", "content": [{"type": "text", "text": f"Turn {n}: read {url}"}]},
        {
            "role": "assistant",
            "content": [
                {"type": "server_tool_use", "id": use_id, "name": "web_fetch",
                 "input": {"url": url}},
                {"type": "web_fetch_tool_result", "tool_use_id": use_id, "content": {
                    "type": "web_fetch_result", "url": url,
                    "retrieved_at": "2026-09-23T10:00:00Z",
                    "content": _document(page, title)}},
                {"type": "text", "text": "Per the page, "},
                {"type": "text", "text": f"finding {n}",
                 "citations": [_citation(page, cited, title, index)]},
                {"type": "text", "text": "."},
            ],
        },
    ]


def _history() -> list[dict[str, Any]]:
    """Two fetch turns, each citation numbered against the whole history,
    then a plain third turn: what a conversation looks like before anything
    condenses it."""
    return [
        *_fetch_turn(1, _A_URL, _A_PAGE, _A_TITLE, _A_CITED, 0),
        *_fetch_turn(2, _B_URL, _B_PAGE, _B_TITLE, _B_CITED, 1),
        {"role": "user", "content": [{"type": "text", "text": "Turn 3: thanks"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "You're welcome."}]},
    ]


def _provider_rule_violations(messages: list[Any]) -> list[str]:
    """What the provider refuses, as far as it is known: an index past the
    request's documents, a character span past the end of the text document
    it lands on, or a page span into a document that is not a PDF."""
    documents = [document for _position, document in request_documents(messages)]
    problems: list[str] = []
    for m, message in enumerate(messages):
        for b, block in enumerate(message.get("content") or []):
            # A document's own "citations" is its setting, not citations.
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            for citation in block.get("citations") or []:
                kind = citation.get("type")
                if kind not in {"char_location", "page_location"}:
                    continue
                where = f"messages.{m}.content.{b}"
                index = citation["document_index"]
                if index >= len(documents):
                    problems.append(f"{where}: document_index {index} out of range")
                    continue
                source = documents[index]["source"]
                if kind == "page_location":
                    if source.get("type") == "text":
                        problems.append(f"{where}: page span into a text document")
                    continue
                if citation["start_char_index"] > len(source.get("data") or ""):
                    problems.append(f"{where}: start index beyond document length")
    return problems


def _cited_indices(messages: list[Any]) -> list[int]:
    return [
        citation["document_index"]
        for message in messages
        for block in message.get("content") or []
        if isinstance(block, dict) and block.get("type") == "text"
        for citation in block.get("citations") or []
    ]


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------


def test_a_quoted_passage_matches_its_span_whitespace_aside():
    # The citations docs cite characters 0-20 of "The grass is green. The sky
    # is blue." as "The grass is green." — the span ends on the space.
    document = "The grass is green. The sky is blue."
    assert same_passage("The grass is green.", document[0:20])
    assert same_passage("The sky  is\nblue.", document[20:36])
    assert not same_passage("The sky is blue.", document[0:20])
    assert not same_passage("", document[0:20])
    # A quote a little longer than its span still counts; a span far shorter
    # than the quote does not.
    assert same_passage("The grass is green. The", document[0:20])
    assert not same_passage("The grass is green. The sky is blue. And more.", "green.")


def test_a_citation_fits_only_the_document_it_describes():
    page = _A_PAGE
    citation = _citation(page, _A_CITED, _A_TITLE, 0)
    assert citation_fits(citation, _document(page, _A_TITLE))
    # Another page's text, or a note too short for the span.
    assert not citation_fits(citation, _document(_B_PAGE, _A_TITLE))
    assert not citation_fits(citation, _document(FETCHED_PAGE_NOTE.format(url=_A_URL), _A_TITLE))
    # A quote that matches decides on its own: a title written differently
    # must not cost an ordinary request its valid citations.
    assert citation_fits(citation, _document(page, _B_TITLE))
    # Without a quote the title decides, and a missing one rules nothing out.
    unquoted = {k: v for k, v in citation.items() if k != "cited_text"}
    assert citation_fits(unquoted, _document(page, _A_TITLE))
    assert not citation_fits(unquoted, _document(page, _B_TITLE))
    untitled = _document(page, _A_TITLE)
    del untitled["title"]
    assert citation_fits(unquoted, untitled)

    pdf = {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                          "data": "JVBERi0x"}}
    page_citation = {"type": "page_location", "cited_text": "x", "document_index": 0,
                     "start_page_number": 2, "end_page_number": 3}
    assert citation_fits(page_citation, pdf)
    assert not citation_fits({**page_citation, "document_title": "b.pdf"},
                             {**pdf, "title": "a.pdf"})
    pdf_note = resend_sanitizer._ELISION_NOTE.format(detail="this document is 3 pages.", limit=600)
    assert not citation_fits(page_citation, _document(pdf_note, "guide.pdf"))


def test_the_premise_a_condensed_view_breaks_its_citations():
    """Why the repair exists: condensing turn 1 takes page A out of the
    request, so page B's citation (index 1) lands past the one page left."""
    history = _history()
    assert _provider_rule_violations(history) == []
    view, _ = compacted_view(history, view_spec_for(_record_for(history, 2)))
    assert _provider_rule_violations(view) == [
        "messages.1.content.3: document_index 1 out of range"
    ]


def test_the_repair_re_points_a_condensed_views_citation_and_mutates_nothing():
    history = _history()
    view, _ = compacted_view(history, view_spec_for(_record_for(history, 2)))
    before = copy.deepcopy(view)

    repaired = repair_document_citations(view)
    assert view == before, "the input is never mutated"
    assert _provider_rule_violations(repaired) == []
    assert _cited_indices(repaired) == [0], "page B is the first page the view holds"
    # Only the citation moved: its message is rebuilt, everything else is
    # the same object.
    assert repaired[0] is view[0] and repaired[2:] == view[2:]
    assert repaired[1]["content"][3]["citations"][0] == {
        **before[1]["content"][3]["citations"][0], "document_index": 0,
    }


def test_a_citation_into_a_condensed_page_is_dropped_and_its_words_kept():
    history = _history()
    # Turn 2's reply also cites page A (fetched in turn 1).
    history[3]["content"][3]["citations"].append(
        _citation(_A_PAGE, _A_CITED, _A_TITLE, 0)
    )
    view, _ = compacted_view(history, view_spec_for(_record_for(history, 2)))
    repaired = repair_document_citations(view)
    assert _provider_rule_violations(repaired) == []
    block = repaired[1]["content"][3]
    assert block["text"] == "finding 2"
    assert [c["document_title"] for c in block["citations"]] == [_B_TITLE]

    # A block left with no citations loses the key, as the API sends it.
    only_a = copy.deepcopy(view)
    only_a[1]["content"][3]["citations"] = [_citation(_A_PAGE, _A_CITED, _A_TITLE, 0)]
    dropped = repair_document_citations(only_a)[1]["content"][3]
    assert dropped == {"type": "text", "text": "finding 2"}


def test_an_ordinary_request_is_returned_untouched():
    history = _history()
    assert repair_document_citations(history) is history
    plain = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    assert repair_document_citations(plain) is plain


def test_a_reply_numbered_under_a_view_is_re_pointed_in_the_whole_history():
    """A turn answered while the conversation was condensed numbered its
    citation against the view (page B was document 0 there). Sent later
    with the whole history, where page A precedes it, that 0 lands on A."""
    history = _history()
    history[3]["content"][3]["citations"][0]["document_index"] = 0
    assert _provider_rule_violations(history) == []  # in range, but on page A
    repaired = repair_document_citations(history)
    assert _cited_indices(repaired) == [0, 1]


def test_a_page_citation_into_a_saved_pdf_note_is_dropped():
    pdf_note = resend_sanitizer._ELISION_NOTE.format(detail="this document is 3 pages.", limit=600)
    page_citation = {"type": "page_location", "cited_text": "Pump curve on page 2.",
                     "document_index": 0, "document_title": "guide.pdf",
                     "start_page_number": 2, "end_page_number": 3}
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "Read the guide."}]},
        {"role": "assistant", "content": [
            {"type": "web_fetch_tool_result", "tool_use_id": "s1", "content": {
                "type": "web_fetch_result", "url": "https://example.test/guide.pdf",
                "content": _document(pdf_note, "guide.pdf")}},
            {"type": "text", "text": "the pump curve", "citations": [page_citation]},
        ]},
    ]
    assert _provider_rule_violations(messages) == ["messages.1.content.1: page span into a text document"]
    repaired = repair_document_citations(messages)
    assert repaired[1]["content"][1] == {"type": "text", "text": "the pump curve"}


def test_search_result_citations_and_unknown_shapes_are_left_alone():
    search = {"type": "web_search_result_location", "url": "https://example.test",
              "title": "Result", "encrypted_index": "abc", "cited_text": "text"}
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "Search."}]},
        {"role": "assistant", "content": [
            {"type": "text", "text": "found", "citations": [search, "not a dict"]},
        ]},
    ]
    assert repair_document_citations(messages) is messages


def test_a_citation_is_never_pointed_at_a_later_document():
    """The same page fetched again later in the request must not attract a
    citation made before that fetch."""
    history = _history()
    view, _ = compacted_view(history, view_spec_for(_record_for(history, 2)))
    refetch = _fetch_turn(4, _B_URL, _B_PAGE, _B_TITLE, _B_CITED, 5)
    repaired = repair_document_citations([*view, *refetch])
    assert _cited_indices(repaired) == [0, 1]


def test_the_repair_never_changes_what_a_shorter_request_already_sent():
    """Prefix-stable, so the cached prefix a turn reads is the one the turn
    before it wrote: repairing a longer request leaves every message a
    shorter one repaired exactly as it was."""
    history = _history()
    history[3]["content"][3]["citations"].append(_citation(_A_PAGE, _A_CITED, _A_TITLE, 7))
    view, _ = compacted_view(history, view_spec_for(_record_for(history, 2)))
    messages = [*view, *_fetch_turn(4, _A_URL, _A_PAGE, _A_TITLE, _A_CITED, 3)]
    whole = repair_document_citations(messages)
    for k in range(len(messages) + 1):
        assert repair_document_citations(messages[:k]) == whole[:k], k


# ---------------------------------------------------------------------------
# The page-text trim folds what a citation quoted into the page's note
# ---------------------------------------------------------------------------


def _note(url: str, *quotes: str) -> str:
    lines = [FETCHED_PAGE_NOTE.format(url=url)]
    if quotes:
        lines += [QUOTED_PASSAGES_HEADER, *(f'- "{q}"' for q in quotes)]
    return "\n".join(lines)


def _page_data(messages: list[Any], n: int) -> str:
    pages = [
        block["content"]["content"]["source"]["data"]
        for message in messages
        for block in message.get("content") or []
        if isinstance(block, dict) and block.get("type") == "web_fetch_tool_result"
    ]
    return pages[n]


def test_the_trim_keeps_each_quote_once_and_the_history_sendable():
    history = _history()
    # Turn 2 quotes page A too, and quotes page B twice.
    history[3]["content"][3]["citations"] += [
        _citation(_A_PAGE, _A_CITED, _A_TITLE, 0),
        _citation(_B_PAGE, _B_CITED, _B_TITLE, 1),
    ]
    trimmed = elide_fetched_page_text(history)
    assert _page_data(trimmed, 0) == _note(_A_URL, _A_CITED)
    assert _page_data(trimmed, 1) == _note(_B_URL, _B_CITED)
    assert _cited_indices(trimmed) == []
    assert _provider_rule_violations(trimmed) == []
    # Trimmed pages are recognized, quotes and all: a second pass is a no-op.
    assert elide_fetched_page_text(trimmed) is trimmed


_MIRROR_A = "https://mirror-a.example/adoption"
_MIRROR_B = "https://mirror-b.example/adoption"


def _mirror_turn(first_index: int) -> list[dict[str, Any]]:
    """One turn fetching the same page from two mirrors (same title, same
    text), then citing the FIRST mirror by index."""
    blocks: list[dict[str, Any]] = []
    for n, url in enumerate((_MIRROR_A, _MIRROR_B)):
        use_id = f"srvtoolu_mirror_{n}"
        blocks += [
            {"type": "server_tool_use", "id": use_id, "name": "web_fetch",
             "input": {"url": url}},
            {"type": "web_fetch_tool_result", "tool_use_id": use_id, "content": {
                "type": "web_fetch_result", "url": url,
                "content": _document(_A_PAGE, _A_TITLE)}},
        ]
    blocks.append({"type": "text", "text": "the adoption date",
                   "citations": [_citation(_A_PAGE, _A_CITED, _A_TITLE, first_index)]})
    return [
        {"role": "user", "content": [{"type": "text", "text": "Read both mirrors."}]},
        {"role": "assistant", "content": blocks},
    ]


def test_the_trim_files_a_quote_under_the_page_the_citation_names():
    """Two mirrors hold the same passage under the same title; only the
    citation's index says which one the reply cited, and the quote must be
    kept under that page's address, not the nearest match's."""
    trimmed = elide_fetched_page_text(_mirror_turn(0))
    assert _page_data(trimmed, 0) == _note(_MIRROR_A, _A_CITED)
    assert _page_data(trimmed, 1) == _note(_MIRROR_B)
    assert _cited_indices(trimmed) == []


def test_the_trim_reads_the_index_against_the_request_it_was_written_in():
    """A committed turn's citations are numbered against its whole request,
    whose earlier documents are not in the list the trim is given: the
    offset says where the list starts. An index that lands on no page it
    fits falls back to the nearest one."""
    turn = _mirror_turn(3)
    offset = elide_fetched_page_text(turn, document_offset=3)
    assert _page_data(offset, 0) == _note(_MIRROR_A, _A_CITED)
    no_offset = elide_fetched_page_text(turn)
    assert _page_data(no_offset, 0) == _note(_MIRROR_A)
    assert _page_data(no_offset, 1) == _note(_MIRROR_B, _A_CITED)


def test_the_trim_leaves_a_citation_a_kept_document_still_answers():
    """A citation that also fits a document the trim keeps is not folded:
    it can still point at that one, and the request repair moves it there."""
    history = _history()
    history[0]["content"].insert(0, _document(_A_PAGE, _A_TITLE))
    history[1]["content"][3]["citations"][0]["document_index"] = 1
    history[3]["content"][3]["citations"][0]["document_index"] = 2
    trimmed = elide_fetched_page_text(history)
    assert _page_data(trimmed, 0) == _note(_A_URL)
    assert _cited_indices(trimmed) == [1]  # B's citation folded, A's kept
    assert _provider_rule_violations(trimmed) == [
        "messages.1.content.3: start index beyond document length"
    ]
    assert _cited_indices(repair_document_citations(trimmed)) == [0]


def test_the_trim_bounds_the_quotes_it_keeps_and_says_how_many_it_left_out():
    sentences = [
        f"Requirement {n:03d} states a long enough sentence to count against the budget of quoted text."
        for n in range(80)
    ]
    page = "\n".join(["Header line."] * 200 + sentences + ["End."])
    use_id = "srvtoolu_long"
    reply = [
        {"type": "text", "text": f"point {n}",
         "citations": [_citation(page, sentence, _A_TITLE, 0)]}
        for n, sentence in enumerate(sentences)
    ]
    history = [
        {"role": "user", "content": [{"type": "text", "text": "Read it all."}]},
        {"role": "assistant", "content": [
            {"type": "web_fetch_tool_result", "tool_use_id": use_id, "content": {
                "type": "web_fetch_result", "url": _A_URL,
                "content": _document(page, _A_TITLE)}},
            *reply,
        ]},
    ]
    trimmed = elide_fetched_page_text(history)
    note = _page_data(trimmed, 0)
    quote_lines = [line for line in note.splitlines() if line.startswith('- "')]
    assert 0 < len(quote_lines) < len(sentences)
    assert sum(len(line) for line in quote_lines) <= QUOTED_PASSAGES_MAX_CHARS
    assert quote_lines[0] == f'- "{sentences[0]}"', "quotes keep the order they were made in"
    left_out = len(sentences) - len(quote_lines)
    assert note.endswith(
        f"({left_out} quoted passage(s) not kept here; fetch the page again for them.)"
    )
    assert len(note) < len(page)
    assert _cited_indices(trimmed) == []


def test_the_trim_never_grows_a_page_even_to_keep_a_quote():
    """A page only a little longer than the note has no room for the quote
    as well. The trim must still only ever shrink the history, so the note
    goes in bare: it still says to fetch the page again for its wording."""
    bare = FETCHED_PAGE_NOTE.format(url=_A_URL)
    cited = "Keep spare heads on site."
    page = "x" * (len(bare) + 5 - len(cited) - 1) + "\n" + cited
    assert len(bare) < len(page) < len(_note(_A_URL, cited))
    history = [
        {"role": "user", "content": [{"type": "text", "text": "Read it."}]},
        {"role": "assistant", "content": [
            {"type": "web_fetch_tool_result", "tool_use_id": "s1", "content": {
                "type": "web_fetch_result", "url": _A_URL,
                "content": _document(page, _A_TITLE)}},
            {"type": "text", "text": "heads on site",
             "citations": [_citation(page, cited, _A_TITLE, 0)]},
        ]},
    ]
    trimmed = elide_fetched_page_text(history)
    assert _page_data(trimmed, 0) == bare
    assert len(_page_data(trimmed, 0)) < len(page)
    assert _cited_indices(trimmed) == []


def test_an_earlier_builds_trimmed_page_is_not_trimmed_again_and_still_sends():
    """What a build with the Phase 2 trim switched on saved: the bare note,
    and the reply's citation still pointing past its end. The trim leaves
    the note alone; the request repair drops the dangling citation."""
    history = _history()[:2]
    history[1]["content"][1]["content"]["content"]["source"]["data"] = (
        FETCHED_PAGE_NOTE.format(url=_A_URL)
    )
    assert elide_fetched_page_text(history) is history
    assert _provider_rule_violations(history) == [
        "messages.1.content.3: start index beyond document length"
    ]
    repaired = repair_document_citations(history)
    assert _provider_rule_violations(repaired) == []
    assert _cited_indices(repaired) == []


# ---------------------------------------------------------------------------
# Through the chat engine and the summary call
# ---------------------------------------------------------------------------


def test_a_condensed_conversation_sends_citations_the_provider_accepts(monkeypatch):
    session = sessions.get_session()
    session.history[:] = _history()
    session.compaction = _record_for(session.history, 2)

    fake = FakeClient([text_turn(["Noted."])])
    _patch_client(monkeypatch, fake)
    resp = TestClient(create_app()).post("/api/chat", json={"message": "What's next?"})
    assert _parse_sse(resp.text)[-1]["type"] == "turn_complete"

    sent = fake.messages.requests[0]["messages"]
    assert "Turn 1:" not in json.dumps(sent), "turn 1 was condensed out of the view"
    assert _provider_rule_violations(sent) == []
    assert _cited_indices(sent) == [0]


def test_a_committed_turn_files_its_quote_under_the_page_it_cited(monkeypatch):
    """Through the engine, in a condensed conversation: the turn's request
    held one page ahead of it (the view keeps turn 2's; turn 1's was
    condensed away), so its citation to the first mirror is index 1. The
    commit must count the VIEW's pages, not the whole history's (2) and not
    none (0); either would file the quote under the second mirror."""
    monkeypatch.setattr(settings, "ELIDE_FETCHED_PAGE_TEXT", True)
    session = sessions.get_session()
    session.history[:] = _history()
    session.compaction = _record_for(session.history, 2)

    blocks = []
    for n, url in enumerate((_MIRROR_A, _MIRROR_B)):
        use_id = f"srvtoolu_mirror_{n}"
        blocks += [
            SimpleNamespace(type="server_tool_use", id=use_id, name="web_fetch",
                            input={"url": url}),
            SimpleNamespace(type="web_fetch_tool_result", tool_use_id=use_id,
                            content={"type": "web_fetch_result", "url": url,
                                     "content": _document(_A_PAGE, _A_TITLE)}),
        ]
    blocks.append(SimpleNamespace(
        type="text", text="the adoption date",
        citations=[_citation(_A_PAGE, _A_CITED, _A_TITLE, 1)],
    ))
    fake = FakeClient([raw_turn(blocks, stop_reason="end_turn")])
    _patch_client(monkeypatch, fake)
    resp = TestClient(create_app()).post("/api/chat", json={"message": "Read both mirrors."})
    assert _parse_sse(resp.text)[-1]["type"] == "turn_complete"

    committed = session.history[-2:]
    assert _page_data(committed, 0) == _note(_MIRROR_A, _A_CITED)
    assert _page_data(committed, 1) == _note(_MIRROR_B)
    assert _cited_indices(committed) == []


def test_the_summary_call_repairs_its_prefix_exactly_as_the_chat_request_does():
    """The summary call reads the cache the last chat request wrote, so its
    copy of the view must come out of the repair byte for byte the same."""
    history = _history()
    spec = view_spec_for(_record_for(history, 2))
    session = sessions.get_session()
    new_turn = [{"role": "user", "content": [{"type": "text", "text": "Turn 4: next"}]}]
    chat = conversation._build_chat_request(
        conversation._ChatRequestInputs(
            history=history,
            new_messages=new_turn,
            module=session.module,
            model=settings.INTERVIEW_MODEL,
            max_tokens=1024,
            view_spec=spec,
        )
    )
    summary = conversation._build_compaction_request(
        conversation._CompactionInputs(
            history=history,
            view_spec=spec,
            module=session.module,
            model=settings.INTERVIEW_MODEL,
            keep_from=0,
            covers_turns=0,
            kept_turns=0,
            instruction="Summarize.",
            generation=session.generation,
            tokens_per_char=None,
            tokens_before=0,
            trigger="routine",
        )
    )

    def without_cache_marks(messages: list[Any]) -> list[Any]:
        stripped = json.loads(json.dumps(messages))
        for message in stripped:
            for block in message.get("content") or []:
                if isinstance(block, dict):
                    block.pop("cache_control", None)
        return stripped

    view_part = without_cache_marks(summary["messages"][:-1])
    assert view_part == without_cache_marks(chat["messages"][: len(view_part)])
    assert _provider_rule_violations(summary["messages"]) == []
    assert _cited_indices(view_part) == [0]
