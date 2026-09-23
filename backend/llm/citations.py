"""Keep every document citation pointing at a document its request holds.

A citation into a document (``char_location``, ``page_location``,
``content_block_location``) names that document by ``document_index``,
which Anthropic's citations docs define as "0-indexed from the list of all
document content blocks in the request (spanning across all messages)". The
API checks each citation against the document its index lands on: the
fetch-elision canary's refusal on 2026-09-23 was ``Start index 2406 is
beyond document length 257``. So a citation is only valid inside the
request it is sent in, and three things in this app change that request
under a citation that was valid when the model wrote it:

- A condensed conversation (compaction plan Phase 3) sends a VIEW that
  leaves its oldest turns out. Every page those turns fetched leaves the
  document list with them, so every later citation's index lands early: on
  the wrong page, or past the end of the list.
- A reply's citations are numbered against the request that produced it. A
  turn answered under one view carries that view's numbers, and sent again
  under another (a newer summary, or the whole history once a reference
  delete dropped the summary) they land on the wrong page again. Saved
  history can hold citations numbered against several requests, and
  nothing records which.
- A fetched PDF becomes a plain-text note when its turn is saved
  (``research.resend_sanitizer.elide_all_pdf_sources``), so a
  ``page_location`` citation into it points at a document that no longer
  has pages.

:func:`repair_document_citations` fixes the outgoing request rather than
the history, and needs no record of which request numbered what, because a
citation says what it cites: the quoted text at its span, and the
document's title. Each citation is checked against the document its index lands on,
among the documents that come BEFORE it in the request. One that does not
fit there is re-pointed at the preceding document it does fit (the nearest
to its old index), or dropped when none does: the reply's text stays, and
only the pointer goes. Looking only backwards is also what keeps it
prefix-stable. Repairing a request never changes a message that an earlier,
shorter request repaired the same way, so the cached prefix every chat turn
reads is untouched. An ordinary request needs no repair, and gets back the
same list object.

A leaf module, like ``server_tool_pairing``: the conversation engine
repairs every request with it, and ``history_hygiene`` uses
:func:`citation_fits` to find the citations into a page it is about to
trim. Neither can import the other's home without a cycle.
"""
from __future__ import annotations

import logging
import re
from bisect import bisect_left
from typing import Any

_log = logging.getLogger("buildaspec.citations")

# The citation kinds that name a document by ``document_index``. Search
# result citations (``web_search_result_location``) name a URL instead and
# are never touched.
DOCUMENT_CITATION_TYPES = frozenset(
    {"char_location", "page_location", "content_block_location"}
)

_WHITESPACE = re.compile(r"\s+")


def _is_index(value: Any) -> bool:
    # bool is an int subclass; a True index is a corrupted record, not 1.
    return isinstance(value, int) and not isinstance(value, bool)


def _norm(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def same_passage(cited: str, span: str) -> bool:
    """Whether ``cited`` is the quoted text of ``span``, whitespace aside.

    The API gives ``cited_text`` as the span with its surrounding whitespace
    trimmed: the citations docs cite characters 0-20 of "The grass is green.
    The sky is blue." as "The grass is green.", without the space the span
    ends on. So comparison collapses whitespace, and accepts the span
    containing the quote. The reverse (the quote containing a span at least
    half its length) is accepted too, in case the provider ever quotes a
    little more than it points at. That asymmetry only makes a valid
    citation harder to lose, and a different page's text almost never
    contains the passage.
    """
    a, b = _norm(cited), _norm(span)
    if not a or not b:
        return False
    return a == b or a in b or (b in a and len(b) * 2 >= len(a))


def documents_in_block(block: Any) -> list[dict[str, Any]]:
    """The document blocks one content block puts in the request, in order.

    A top-level ``document``; the document a successful web fetch returned
    (``web_fetch_tool_result`` → ``web_fetch_result`` → ``content``); and
    any documents inside a client ``tool_result``'s content list. The chat
    never produces that last kind, but the API counts every document in the
    request, so this does too.
    """
    if not isinstance(block, dict):
        return []
    kind = block.get("type")
    if kind == "document":
        return [block]
    if kind == "web_fetch_tool_result":
        result = block.get("content")
        if isinstance(result, dict):
            document = result.get("content") or result.get("document")
            if isinstance(document, dict) and document.get("type") == "document":
                return [document]
        return []
    if kind == "tool_result":
        content = block.get("content")
        if isinstance(content, list):
            return [
                item
                for item in content
                if isinstance(item, dict) and item.get("type") == "document"
            ]
    return []


def _titles_agree(citation: dict[str, Any], document: dict[str, Any]) -> bool:
    title = citation.get("document_title")
    document_title = document.get("title")
    if not (isinstance(title, str) and title):
        return True
    if not (isinstance(document_title, str) and document_title):
        return True
    return title == document_title


def citation_fits(citation: dict[str, Any], document: dict[str, Any]) -> bool:
    """Whether ``citation`` can point at ``document`` and be accepted.

    The span must lie inside the document, and the document must be the
    kind the citation addresses: text for character spans, a PDF for page
    spans, custom content for block spans. A character span that quotes
    must quote that document (:func:`same_passage`), and the quote alone
    decides. The quote is the strong evidence, and if a document's title
    were ever written differently in the citation than on the document,
    requiring both would drop valid citations from ordinary requests. The
    title decides only where there is no quote to compare: a character span
    without one, and page and block spans. Page spans cannot be checked
    against the pages without reading the PDF, so a PDF is enough there.
    That is also what rules a fetched PDF out once its turn is saved: the
    saved copy is a plain-text note.
    """
    kind = citation.get("type")
    source = document.get("source")
    if not isinstance(source, dict):
        return False
    source_type = source.get("type")
    if kind == "char_location":
        data = source.get("data")
        start = citation.get("start_char_index")
        end = citation.get("end_char_index")
        if source_type != "text" or not isinstance(data, str):
            return False
        if not (_is_index(start) and _is_index(end) and 0 <= start <= end <= len(data)):
            return False
        cited = citation.get("cited_text")
        if isinstance(cited, str) and cited.strip():
            return same_passage(cited, data[start:end])
        return _titles_agree(citation, document)
    if kind == "page_location":
        start = citation.get("start_page_number")
        end = citation.get("end_page_number")
        if source_type in {"text", "content"}:
            return False
        return (
            _is_index(start)
            and _is_index(end)
            and 1 <= start <= end
            and _titles_agree(citation, document)
        )
    if kind == "content_block_location":
        content = source.get("content")
        start = citation.get("start_block_index")
        end = citation.get("end_block_index")
        if source_type != "content" or not isinstance(content, list):
            return False
        return (
            _is_index(start)
            and _is_index(end)
            and 0 <= start <= end <= len(content)
            and _titles_agree(citation, document)
        )
    return False


def request_documents(
    messages: list[Any],
) -> list[tuple[tuple[int, int], dict[str, Any]]]:
    """Every document in ``messages``, in request order, with its position.

    A position is ``(message index, block index)``, and the list is sorted
    by it. Its order is the order ``document_index`` counts in.
    """
    found: list[tuple[tuple[int, int], dict[str, Any]]] = []
    for message_index, message in enumerate(messages):
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block_index, block in enumerate(content):
            for document in documents_in_block(block):
                found.append(((message_index, block_index), document))
    return found


def repair_document_citations(messages: list[Any]) -> list[Any]:
    """``messages`` with every document citation valid (copy-on-write).

    Returns the SAME list object when every citation already fits the
    document its index lands on, which is every request that nothing
    condensed or trimmed. Changed messages and blocks are rebuilt, and
    nothing given is ever mutated. A text block left with no citations
    loses its ``citations`` key, which is how the API returns uncited text.
    Only document citations in assistant text are judged, since citations
    are the model's. Search result citations and anything unrecognized are
    kept as they are.
    """
    documents = request_documents(messages)
    positions = [position for position, _document in documents]
    result: list[Any] | None = None
    repointed = dropped = 0
    for message_index, message in enumerate(messages):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        new_content: list[Any] | None = None
        for block_index, block in enumerate(content):
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            citations = block.get("citations")
            if not isinstance(citations, list) or not citations:
                continue
            # Only documents that come before this block can be what it
            # cites, and only they keep the repair prefix-stable.
            before = bisect_left(positions, (message_index, block_index))
            kept: list[Any] = []
            changed = False
            for citation in citations:
                if (
                    not isinstance(citation, dict)
                    or citation.get("type") not in DOCUMENT_CITATION_TYPES
                ):
                    kept.append(citation)
                    continue
                index = citation.get("document_index")
                if (
                    _is_index(index)
                    and 0 <= index < before
                    and citation_fits(citation, documents[index][1])
                ):
                    kept.append(citation)
                    continue
                changed = True
                candidates = [
                    i for i in range(before) if citation_fits(citation, documents[i][1])
                ]
                if candidates:
                    target = index if _is_index(index) else before
                    best = min(candidates, key=lambda i: (abs(i - target), i))
                    kept.append({**citation, "document_index": best})
                    repointed += 1
                else:
                    dropped += 1
            if not changed:
                continue
            repaired = dict(block)
            if kept:
                repaired["citations"] = kept
            else:
                repaired.pop("citations", None)
            if new_content is None:
                new_content = list(content)
            new_content[block_index] = repaired
        if new_content is not None:
            if result is None:
                result = list(messages)
            result[message_index] = {**message, "content": new_content}
    if result is None:
        return messages
    _log.debug(
        "Repaired document citations for the request: %d re-pointed, %d dropped.",
        repointed,
        dropped,
    )
    return result
