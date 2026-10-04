"""Research request preflight and copy-on-write context recovery.

Token counting is the provider's unbilled endpoint, not billed usage (which
can sum many server-tool iterations and includes cache reads). Only raw web
results are elided for an oversized submission; extracted findings and the
original responses used for grounding and accounting stay intact.
"""
from __future__ import annotations

from typing import Any

from .grounding import (
    collect_fetch_evidence_detailed,
    collect_search_evidence_detailed,
)


def count_request_tokens(client: Any, messages: list[dict], request: dict) -> int:
    """Count the actual input, including the tools, system and cached prefix."""
    kwargs = {
        key: value
        for key, value in request.items()
        if key in {
            "model", "system", "tools", "thinking", "output_config",
            "tool_choice", "cache_control", "extra_headers",
        }
    }
    counted = client.messages.count_tokens(messages=messages, **kwargs)
    tokens = counted.input_tokens
    if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0:
        raise ValueError("Research token counter returned an invalid input count.")
    return tokens


def _field(node: Any, name: str) -> Any:
    return node.get(name) if isinstance(node, dict) else getattr(node, name, None)


def without_thinking(messages: list[dict]) -> list[dict]:
    """Convert visible research notes to text; omit signed/redacted blocks.

    Submission disables thinking and changes the tools, so signed thinking
    cannot be replayed under its original prefix. Keep its readable notes
    as ordinary input rather than throwing away extracted research.
    """
    result = list(messages)
    edited = False
    for index, message in enumerate(messages):
        content = message.get("content")
        if message.get("role") != "assistant" or not isinstance(content, list):
            continue
        kept = []
        changed = False
        for block in content:
            kind = _field(block, "type")
            if kind not in {"thinking", "redacted_thinking"}:
                kept.append(block)
                continue
            changed = True
            notes = _field(block, "thinking") if kind == "thinking" else None
            if notes:
                kept.append({"type": "text", "text": "[Earlier research notes]\n" + notes})
        if changed:
            edited = True
            result[index] = {**message, "content": kept or [
                {"type": "text", "text": "[Research thinking omitted before submission.]"}
            ]}
    return result if edited else messages


def elide_web_results(messages: list[dict], *, fetch_only: bool) -> list[dict]:
    """Replace raw web results with URL/title notes, preserving other blocks.

    The caller runs the resend sanitizer afterwards to drop server calls
    whose results became notes. Submission has already converted thinking.
    Originals are never mutated: grounding still reads their full evidence.
    """
    kinds = {"web_fetch_tool_result"}
    if not fetch_only:
        kinds.add("web_search_tool_result")
    edited = False
    result = list(messages)
    fetch_uses = {
        _field(block, "id"): block
        for message in messages if message.get("role") == "assistant"
        for block in message.get("content", [])
        if _field(block, "type") == "server_tool_use" and _field(block, "name") == "web_fetch"
    }
    for index, message in enumerate(messages):
        if message.get("role") != "assistant":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        replacement = []
        changed = False
        for block in content:
            if _field(block, "type") not in kinds:
                replacement.append(block)
                continue
            changed = True
            sources, _, _ = collect_search_evidence_detailed({"content": [block]})
            if _field(block, "type") == "web_fetch_tool_result":
                # A result need not echo its URL; its call may be in an
                # earlier paused response, so pair across the conversation.
                use = fetch_uses.get(_field(block, "tool_use_id"))
                sources, _, _ = collect_fetch_evidence_detailed({
                    "content": [use, block] if use is not None else [block]
                })
            note = "\n".join(
                f"{source.url}" + (f" — {source.title}" if source.title else "")
                for source in sources
            )
            replacement.append({
                "type": "text",
                "text": "[Raw source content omitted to fit the research "
                "submission. Retain only findings supported by the remaining "
                "conversation. Retrieved sources:]\n" + note,
            })
        if changed:
            edited = True
            result[index] = {**message, "content": replacement}
    if not edited:
        return messages
    # Citations can point into a removed document or search result. Keep
    # their passages/URLs as text, but do not replay invalid wire pointers.
    for index, message in enumerate(result):
        if message.get("role") != "assistant":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        replacement = []
        changed = False
        for block in content:
            citations = _field(block, "citations")
            if _field(block, "type") != "text" or not citations:
                replacement.append(block)
                continue
            changed = True
            notes = []
            for citation in citations:
                passage = _field(citation, "cited_text") or ""
                url = _field(citation, "url") or ""
                if passage or url:
                    notes.append(f"Previously cited passage: {passage} {url}".strip())
            replacement.append({
                "type": "text",
                "text": "\n".join([_field(block, "text") or "", *notes]),
            })
        if changed:
            result[index] = {**message, "content": replacement}
    return result
