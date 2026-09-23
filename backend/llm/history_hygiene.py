"""Keep saved chat history free of data held elsewhere, and measure what fills it.

Every chat turn re-sends the whole committed history, so anything committed
is paid for again on every later request, counts toward the model's context
window for the rest of the session, and is written into the saved project.
The largest thing that ever lands there is not conversation at all: every
successful ``apply_spec_edits`` call (and ``apply_qc_fixes``, and a
rejected edit batch) returns the ENTIRE document outline so the model can
map element ids between calls. Within a turn that is exactly what the model
needs. After the turn commits it is dead weight — stale the moment the next
edit lands, truncated at 160 characters per provision, and superseded on
every later turn by the full, current document (every element id included)
that PROJECT CONTEXT carries. Measured on a realistic 300-paragraph section,
one full draft committed ~260k tokens of history, 85% of it these outlines;
each later one-sentence edit committed ~17k (see
``docs/plans/CHAT_HISTORY_COMPACTION_2026-09-22.md``).

:func:`elide_stale_outlines` removes them from COMMITTED history — the same
posture as the fetched-PDF, figure-source and reference-body elisions in
``conversation._committed_messages``: machine payloads whose content lives
somewhere else. What an edit DID stays (the ``applied`` records, the QC
``outcomes``, a rejected batch's error text); only the snapshot of the
document goes. It is cache-free: commit already rewrites the last exchange
(the PROJECT CONTEXT block is stripped), so the next turn writes that
exchange fresh whether or not an outline is in it.

:func:`elide_fetched_page_text` does the same for the text of web pages the
chat fetched (compaction plan Phase 2, owner decision D2): up to
``WEB_FETCH_MAX_CONTENT_TOKENS`` of page text per fetch, re-sent on every
later turn. The page is not stored anywhere else in the app, but it lives on
the web: the note that replaces it names the URL, the ``url`` field the
model may re-fetch from stays, and so do the document's title, retrieval
time and citation setting. The document block itself is never removed —
citation ``document_index`` counts every document in the request, so
dropping one would re-point every later citation at the wrong page.

A reply that quoted the page cited it with character spans into the ORIGINAL
text, and the API checks a span against the document it lands on: the live
canary's first run (2026-09-23) was refused with ``Start index 2406 is
beyond document length 257``. So the trim also removes the citations that
point into a page it trims, and keeps what they quoted by folding each
passage into the page's note, where the model reads it on later turns.
The API does not bill ``cited_text`` as input when it is passed back, which
says it is not re-sent to the model as text; with the page gone, the note is
the one place the passage can still be read. A citation that also fits a
page this trim keeps is left alone, and so is one that points at a page this
list does not hold. ``citations.repair_document_citations`` checks every
outgoing request against what the request really holds.

Fetched PDFs are not touched here; ``research.resend_sanitizer.
elide_all_pdf_sources`` already turns each one into a short note at commit.
The callers apply the page trim only while ``settings.ELIDE_FETCHED_PAGE_
TEXT`` is on, which it is not by default until the live canary
(``tools/fetch_elision_canary.py``) passes on this shape; this module stays
a leaf and reads no settings itself.

:func:`history_composition` says what a history is made of, by category,
in sizes only — never text — for Developer tools, the support bundle and
``tools/chat_history_profile.py``.

A leaf module on purpose (the ``server_tool_pairing`` precedent): the
conversation engine applies the elision at commit and the project loader
applies it to histories saved by earlier builds, and ``project.py`` cannot
import the engine without a cycle.
"""
from __future__ import annotations

import json
from bisect import bisect_left
from typing import Any

from .citations import citation_fits, request_documents

# The app's estimate when it has no real count to hand (len/4). An estimate,
# labelled as one everywhere it is shown; it is not a tokenizer.
CHARS_PER_TOKEN = 4

# Tools whose results carry a document outline. The name is matched through
# the result's ``tool_use_id``, never guessed from the result's shape, so a
# tool that happens to return an ``outline`` key is not touched unless it is
# listed here.
OUTLINE_BEARING_TOOLS = frozenset({"apply_spec_edits", "apply_qc_fixes"})

# The rejected-batch result is "<error>" + this header + the outline. The
# producer (``conversation._run_tool``) builds it from this constant, so the
# elider can never disagree with the text it has to find.
REJECTED_BATCH_DOCUMENT_HEADER = "\n\nCurrent specification document:\n"

# Deliberately does not name the context block by its header: history must
# never contain that header (``test_context_block_never_fossilizes_into_
# history`` probes for it), and the note only has to say where to look.
STALE_OUTLINE_NOTE = (
    "[Document outline omitted from saved history. It showed the document "
    "as it stood right after this edit and is out of date now; the current "
    "document, with every element id, arrives fresh with each new message.]"
)

# Friendly names for the server-tool result families the chat uses. Any
# other ``*_tool_result`` type is reported under its own type name.
_SERVER_RESULT_LABELS = {
    "web_search_tool_result": "web search results",
    "web_fetch_tool_result": "fetched web pages",
}

OUTLINE_CATEGORY = "document outlines in edit results"

# Replaces a fetched page's text in saved history. It names the URL so the
# model can fetch the page again; the passages a reply quoted follow it
# (see QUOTED_PASSAGES_HEADER). Like STALE_OUTLINE_NOTE it never names the
# context block's header.
FETCHED_PAGE_NOTE = (
    "[Page text omitted from saved history so it is not re-sent with every "
    "later message. Fetched from: {url}. Fetch it again if its exact "
    "wording is needed.]"
)
# Every page note starts with this, whatever follows it. A note that
# carries quoted passages is longer than a bare one, so "no longer than its
# replacement" can no longer tell a trimmed page from an untrimmed one; the
# prefix does. Notes written by earlier builds (which ended by promising the
# citations kept the quotes) start with it too.
FETCHED_PAGE_NOTE_PREFIX = "[Page text omitted from saved history"
# The passages the replies quoted from a trimmed page, one per line after
# the note. They replace the citations that pointed into the page text.
QUOTED_PASSAGES_HEADER = "Passages the replies quoted from it:"
QUOTED_PASSAGES_LEFT_OUT = (
    "({count} quoted passage(s) not kept here; fetch the page again for them.)"
)
# Per page. A note that grew past this would start to cost what the page did.
QUOTED_PASSAGES_MAX_CHARS = 4_000
# ``research.resend_sanitizer`` rewrites a fetched PDF into a short
# plain-text note that starts with this. It says what it replaced (and how
# many pages), so it is left alone rather than overwritten by the page note,
# which can be the shorter of the two. A literal rather than an import keeps
# this module a leaf; ``test_fetched_page_elision`` pins that the PDF note
# really does start with it.
PDF_ELISION_NOTE_PREFIX = "[Fetched PDF content elided"
# Fetched URLs are capped at 250 characters by the API. The cap here only
# guards a hand-edited file from putting something enormous in a note.
_NOTE_URL_CHARS = 500

FETCHED_PAGE_CATEGORY = "page text in fetched web pages"


def _elide_outline(content: Any) -> tuple[Any, int]:
    """``(content without its outline, characters saved)``.

    Returns the content unchanged with ``0`` when there is nothing to take
    out: not a string, not a recognizable result, already elided, or an
    outline shorter than the note that would replace it (an empty document's
    outline is one short line — replacing it would GROW the history). That
    last rule is also what makes the function idempotent without special
    cases.
    """
    if not isinstance(content, str):
        return content, 0
    if content.lstrip().startswith("{"):
        try:
            data = json.loads(content)
        except ValueError:
            return content, 0
        if not isinstance(data, dict) or not isinstance(data.get("outline"), str):
            return content, 0
        data["outline"] = STALE_OUTLINE_NOTE
        elided = json.dumps(data, ensure_ascii=False)
    else:
        head, header, _outline = content.partition(REJECTED_BATCH_DOCUMENT_HEADER)
        if not header:
            return content, 0
        elided = head + "\n\n" + STALE_OUTLINE_NOTE
    saved = len(content) - len(elided)
    if saved <= 0:
        return content, 0
    return elided, saved


def _outline_tool_ids(messages: list[Any]) -> set[str]:
    ids: set[str] = set()
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_use"
                and block.get("name") in OUTLINE_BEARING_TOOLS
                and isinstance(block.get("id"), str)
            ):
                ids.add(block["id"])
    return ids


def elide_stale_outlines(messages: list[Any]) -> list[Any]:
    """Drop document outlines from saved edit results (copy-on-write).

    Returns the SAME list object when nothing needed removing, so a caller
    can tell whether it changed anything (``result is not messages``) and
    an unchanged history costs no copy. Changed messages are rebuilt;
    nothing given is ever mutated. Only committed history may be passed:
    inside a turn the outline is the model's id map between calls.
    """
    ids = _outline_tool_ids(messages)
    if not ids:
        return messages
    result: list[Any] | None = None
    for index, message in enumerate(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        new_content: list[Any] | None = None
        for block_index, block in enumerate(content):
            if not (
                isinstance(block, dict)
                and block.get("type") == "tool_result"
                and block.get("tool_use_id") in ids
            ):
                continue
            elided, saved = _elide_outline(block.get("content"))
            if saved <= 0:
                continue
            if new_content is None:
                new_content = list(content)
            new_content[block_index] = {**block, "content": elided}
        if new_content is not None:
            if result is None:
                result = list(messages)
            result[index] = {**message, "content": new_content}
    return messages if result is None else result


def count_stale_outlines(messages: list[Any]) -> int:
    """How many saved edit results still carry a removable outline."""
    ids = _outline_tool_ids(messages)
    count = 0
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_result"
                and block.get("tool_use_id") in ids
                and _elide_outline(block.get("content"))[1] > 0
            ):
                count += 1
    return count


def _note_url(url: Any) -> str:
    text = str(url or "").strip()
    return text[:_NOTE_URL_CHARS] if text else "an address the result did not record"


def _elide_page_text(block: Any, note: str | None = None) -> dict[str, Any] | None:
    """``block`` with its fetched page text replaced by ``note``, else None.

    None means there is nothing to take out: not a successful
    ``web_fetch_tool_result``, a document that is not plain text (a fetched
    PDF, which the PDF elision handles), the PDF elision's own note, a page
    already trimmed (its text starts with ``FETCHED_PAGE_NOTE_PREFIX``), or a
    page no longer than the bare note — replacing it would GROW the history.
    ``note`` defaults to that bare note; the trim passes one carrying the
    quoted passages, which :func:`_page_note` keeps shorter than the page.
    Only the text source's ``data`` changes; every other key of the block,
    the result (``url``, ``retrieved_at``) and the document (``title``,
    ``citations``) is kept as it was.
    """
    if not isinstance(block, dict) or block.get("type") != "web_fetch_tool_result":
        return None
    result = block.get("content")
    if not isinstance(result, dict) or result.get("type") != "web_fetch_result":
        return None
    document = result.get("content")
    if not isinstance(document, dict) or document.get("type") != "document":
        return None
    source = document.get("source")
    if not isinstance(source, dict) or source.get("type") != "text":
        return None
    data = source.get("data")
    if (
        not isinstance(data, str)
        or data.startswith(PDF_ELISION_NOTE_PREFIX)
        or data.startswith(FETCHED_PAGE_NOTE_PREFIX)
    ):
        return None
    if len(FETCHED_PAGE_NOTE.format(url=_note_url(result.get("url")))) >= len(data):
        return None
    if note is None:
        note = FETCHED_PAGE_NOTE.format(url=_note_url(result.get("url")))
    return {
        **block,
        "content": {
            **result,
            "content": {**document, "source": {**source, "data": note}},
        },
    }


def _quoted_passage(citation: dict[str, Any], data: str) -> str:
    """What a character-span citation quoted, on one line."""
    cited = citation.get("cited_text")
    if not isinstance(cited, str) or not cited.strip():
        cited = data[citation["start_char_index"]:citation["end_char_index"]]
    return " ".join(cited.split())


def _page_note(url: Any, passages: list[str], page_chars: int) -> str:
    """The note for one trimmed page: the bare note, then what was quoted.

    Always shorter than the page it replaces — the trim must only ever
    shrink the history — and the passages together stay within
    ``QUOTED_PASSAGES_MAX_CHARS``. Passages that do not fit are counted in a
    closing line rather than dropped without a word. When even that line
    would not fit, the bare note stands alone; it still says to fetch the
    page again for its exact wording.
    """
    base = FETCHED_PAGE_NOTE.format(url=_note_url(url))
    if not passages:
        return base
    kept: list[str] = []
    budget = QUOTED_PASSAGES_MAX_CHARS
    for passage in passages:
        line = f'- "{passage}"'
        if len(line) > budget:
            break
        kept.append(line)
        budget -= len(line)
    while True:
        left_out = len(passages) - len(kept)
        parts = [base]
        if kept:
            parts += [QUOTED_PASSAGES_HEADER, *kept]
        if left_out:
            parts.append(QUOTED_PASSAGES_LEFT_OUT.format(count=left_out))
        note = "\n".join(parts)
        if len(note) < page_chars:
            return note
        if not kept:
            return base
        kept.pop()


def elide_fetched_page_text(messages: list[Any]) -> list[Any]:
    """Drop fetched web-page text from saved history (copy-on-write).

    Every page this list holds is trimmed to a note, and every citation in
    the list that points into a trimmed page is removed, its passage folded
    into that page's note (the nearest trimmed page before the citation, if
    it fits more than one). A citation that also fits a page left in place
    keeps pointing where it did.

    Returns the SAME list object when nothing needed removing; changed
    messages are rebuilt and nothing given is ever mutated. Only committed
    history may be passed: within a turn the model is still reading the page
    it just fetched. Server tool results only ever sit in assistant
    messages, so only those are searched.
    """
    pages: dict[tuple[int, int], dict[str, Any]] = {}
    for message_index, message in enumerate(messages):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block_index, block in enumerate(content):
            if _elide_page_text(block) is not None:
                pages[(message_index, block_index)] = block
    if not pages:
        return messages

    documents = request_documents(messages)
    positions = [position for position, _document in documents]
    passages: dict[tuple[int, int], list[str]] = {position: [] for position in pages}
    folded: dict[tuple[int, int], set[int]] = {}
    for message_index, message in enumerate(messages):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block_index, block in enumerate(content):
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            citations = block.get("citations")
            if not isinstance(citations, list):
                continue
            before = bisect_left(positions, (message_index, block_index))
            for citation_index, citation in enumerate(citations):
                if not isinstance(citation, dict) or citation.get("type") != "char_location":
                    continue
                fitting = [
                    position
                    for position, document in documents[:before]
                    if citation_fits(citation, document)
                ]
                # Nothing here to point at: another turn's page, or a pointer
                # that was already broken. The request repair judges those.
                if not fitting or any(p not in pages for p in fitting):
                    continue
                target = fitting[-1]
                data = pages[target]["content"]["content"]["source"]["data"]
                passage = _quoted_passage(citation, data)
                if passage and passage not in passages[target]:
                    passages[target].append(passage)
                folded.setdefault((message_index, block_index), set()).add(citation_index)

    result = list(messages)
    rebuilt: dict[int, list[Any]] = {}

    def content_of(message_index: int) -> list[Any]:
        if message_index not in rebuilt:
            rebuilt[message_index] = list(messages[message_index]["content"])
        return rebuilt[message_index]

    for (message_index, block_index), block in pages.items():
        result_block = block["content"]
        note = _page_note(
            result_block.get("url"),
            passages[(message_index, block_index)],
            len(result_block["content"]["source"]["data"]),
        )
        content_of(message_index)[block_index] = _elide_page_text(block, note)
    for (message_index, block_index), dropped in folded.items():
        block = messages[message_index]["content"][block_index]
        kept = [c for i, c in enumerate(block["citations"]) if i not in dropped]
        repaired = dict(block)
        if kept:
            repaired["citations"] = kept
        else:
            repaired.pop("citations", None)
        content_of(message_index)[block_index] = repaired
    for message_index, new_content in rebuilt.items():
        result[message_index] = {**messages[message_index], "content": new_content}
    return result


def count_fetched_page_texts(messages: list[Any]) -> int:
    """How many saved web fetch results still carry removable page text."""
    count = 0
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        count += sum(1 for block in content if _elide_page_text(block) is not None)
    return count


def estimated_tokens(chars: int) -> int:
    return chars // CHARS_PER_TOKEN


def _size(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value)
    try:
        return len(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return len(str(value))


def history_composition(messages: list[Any]) -> dict[str, Any]:
    """What a history is made of, by category, in characters.

    Sizes and counts only: no text, no tool inputs, no URLs — safe for the
    diagnostics snapshot and a support bundle. Categories partition the
    history, so they sum to ``chars``. Serialized size is a proxy for
    tokens, labelled as an estimate: it overstates base64 and encrypted
    payloads (web search results carry encrypted content) and understates
    nothing the app controls.

    ``stale_outlines`` counts saved edit results that still carry a
    document outline — zero for anything committed by this build, which is
    what makes it a useful canary; ``tools/chat_history_profile.py`` uses it
    to show what the elision removes from files saved by earlier ones.
    ``fetched_page_texts`` counts fetched web pages that still carry their
    text: the same canary while the page-text trim is switched on, and
    simply the number of pages the history keeps while it is off (the
    default until its live canary passes).
    """
    tool_names: dict[str, str] = {}
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        for block in message.get("content") or []:
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_use"
                and isinstance(block.get("id"), str)
            ):
                tool_names[block["id"]] = str(block.get("name") or "unknown")

    totals: dict[str, list[int]] = {}

    def add(category: str, chars: int) -> None:
        entry = totals.setdefault(category, [0, 0])
        entry[0] += chars
        entry[1] += 1

    stale = 0
    fetched = 0
    counted = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        counted += 1
        role = str(message.get("role") or "unknown")
        content = message.get("content")
        if isinstance(content, str):
            add(f"{role} text", len(content))
            continue
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                add("other", _size(block))
                continue
            kind = block.get("type")
            if kind == "text":
                add(f"{role} text", len(block.get("text") or ""))
                if block.get("citations"):
                    add("citations", _size(block.get("citations")))
            elif kind == "tool_use":
                add(f"tool call: {block.get('name') or 'unknown'}",
                    _size(block.get("input")))
            elif kind == "tool_result":
                name = tool_names.get(block.get("tool_use_id"), "unknown")
                result = block.get("content")
                if name in OUTLINE_BEARING_TOOLS:
                    result, saved = _elide_outline(result)
                    if saved > 0:
                        stale += 1
                        add(OUTLINE_CATEGORY, saved)
                add(f"tool result: {name}", _size(result))
            elif kind == "server_tool_use":
                add(f"server tool call: {block.get('name') or 'unknown'}",
                    _size(block.get("input")))
            elif isinstance(kind, str) and kind.endswith("_tool_result"):
                result = block.get("content")
                # Same scope as the elision: server results live in
                # assistant messages, and only those are trimmed.
                elided = _elide_page_text(block) if role == "assistant" else None
                if elided is not None:
                    # Sized as serialized, both ways, so the two categories
                    # still add up to exactly what the block weighed.
                    trimmed = elided.get("content")
                    fetched += 1
                    add(FETCHED_PAGE_CATEGORY, _size(result) - _size(trimmed))
                    result = trimmed
                add(_SERVER_RESULT_LABELS.get(kind, kind), _size(result))
            else:
                add(f"other: {kind or 'untyped'}", _size(block))

    chars = sum(entry[0] for entry in totals.values())
    categories = [
        {
            "category": category,
            "chars": entry[0],
            "estimated_tokens": estimated_tokens(entry[0]),
            "blocks": entry[1],
        }
        for category, entry in sorted(
            totals.items(), key=lambda item: (-item[1][0], item[0])
        )
    ]
    return {
        "messages": counted,
        "chars": chars,
        "estimated_tokens": estimated_tokens(chars),
        "stale_outlines": stale,
        "fetched_page_texts": fetched,
        "categories": categories,
    }
