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
                add(_SERVER_RESULT_LABELS.get(kind, kind), _size(block.get("content")))
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
        "categories": categories,
    }
