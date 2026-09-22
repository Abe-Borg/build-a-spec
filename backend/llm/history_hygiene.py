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
dropping one would re-point every later citation at the wrong page. Fetched
PDFs are not touched here; ``research.resend_sanitizer.elide_all_pdf_sources``
already turns each one into a short note at commit.

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
from typing import Any

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
# model can fetch the page again; the passages a reply quoted survive in
# that reply's citations (``cited_text``), which the API does not bill as
# input. Like STALE_OUTLINE_NOTE it never names the context block's header.
FETCHED_PAGE_NOTE = (
    "[Page text omitted from saved history so it is not re-sent with every "
    "later message. Fetched from: {url}. Fetch it again if its exact "
    "wording is needed; passages quoted from it stay in the replies' "
    "citations.]"
)
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


def _elide_page_text(block: Any) -> dict[str, Any] | None:
    """``block`` with its fetched page text replaced by the note, else None.

    None means there is nothing to take out: not a successful
    ``web_fetch_tool_result``, a document that is not plain text (a fetched
    PDF, which the PDF elision handles), the PDF elision's own note, or a
    page no longer than the note that would replace it — replacing it would
    GROW the history, and that rule is also what makes the elision
    idempotent (a note in place is exactly as long as its replacement).
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
    if not isinstance(data, str) or data.startswith(PDF_ELISION_NOTE_PREFIX):
        return None
    note = FETCHED_PAGE_NOTE.format(url=_note_url(result.get("url")))
    if len(note) >= len(data):
        return None
    return {
        **block,
        "content": {
            **result,
            "content": {**document, "source": {**source, "data": note}},
        },
    }


def elide_fetched_page_text(messages: list[Any]) -> list[Any]:
    """Drop fetched web-page text from saved history (copy-on-write).

    Returns the SAME list object when nothing needed removing; changed
    messages are rebuilt and nothing given is ever mutated. Only committed
    history may be passed: within a turn the model is still reading the page
    it just fetched. Server tool results only ever sit in assistant
    messages, so only those are searched.
    """
    result: list[Any] | None = None
    for index, message in enumerate(messages):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        new_content: list[Any] | None = None
        for block_index, block in enumerate(content):
            elided = _elide_page_text(block)
            if elided is None:
                continue
            if new_content is None:
                new_content = list(content)
            new_content[block_index] = elided
        if new_content is not None:
            if result is None:
                result = list(messages)
            result[index] = {**message, "content": new_content}
    return messages if result is None else result


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
    ``fetched_page_texts`` is the same canary for fetched web pages that
    still carry their text.
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
