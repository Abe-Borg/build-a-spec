"""Research request preflight and copy-on-write context recovery.

Token counting uses a supported equivalent at the provider's unbilled
endpoint, plus a reserve for server-tool overhead. It is a conservative
estimate, not billed usage (which can sum many iterations/cache reads). Raw web
results are elided for an oversized submission; extracted findings and the
original responses used for grounding and accounting stay intact.
"""
from __future__ import annotations

import dataclasses
import json
from typing import Any

from .grounding import (
    collect_fetch_evidence_detailed,
    collect_search_evidence_detailed,
)

# The counter does not accept server-tool declarations. Their serialized
# definitions are counted as text, with extra room for their hidden prompt
# framing. This is an estimate, separate from the engine's growth margin.
_SERVER_TOOL_OVERHEAD_TOKENS = 10_000


def _plain_node(node: Any) -> Any:
    dump = getattr(node, "model_dump", None)
    if callable(dump):
        return dump(mode="json", exclude_none=True)
    if dataclasses.is_dataclass(node) and not isinstance(node, type):
        return dataclasses.asdict(node)
    return vars(node)


def _counting_messages(messages: list[dict]) -> list[dict]:
    """Count replay content as text, with documents/images in native form.

    Server calls/results and signed thinking cannot depend on server-tool
    declarations at this endpoint. Serialize their full content into user
    text, including citations/signatures, without altering the paid request.
    Lift documents/images out of that text so base64 is counted as media,
    rather than potentially millions of text tokens. This also avoids
    dangling client calls or document citations in the counting-only view.
    """
    result = []
    for message in messages:
        plain = json.loads(json.dumps(message, default=_plain_node, ensure_ascii=False))
        media = []

        def lift(node: Any, lifted: list = media) -> Any:
            if isinstance(node, dict):
                if node.get("type") in {"document", "image"} and node.get("source"):
                    lifted.append(node)
                    return "[Media content counted separately]"
                return {key: lift(value) for key, value in node.items()}
            if isinstance(node, list):
                return [lift(value) for value in node]
            return node

        serialized = json.dumps(lift(plain), ensure_ascii=False)
        result.append({"role": "user", "content": [
            {"type": "text", "text": serialized}, *media,
        ]})
    return result


def count_request_tokens(client: Any, messages: list[dict], request: dict) -> int:
    """Estimate full input without sending unsupported server tools to count.

    The system and custom output schema are counted natively. Server-tool
    definitions and replay metadata are ordinary text in the counting-only
    view; their provider framing gets an additional conservative reserve.
    The stream's messages, tools, cache prefix and container are untouched.
    """
    kwargs = {
        key: value
        for key, value in request.items()
        if key in {
            "model", "system", "tools", "thinking", "output_config",
            "tool_choice", "cache_control", "extra_headers",
        }
    }
    server_tools = [tool for tool in request.get("tools", [])
                    if tool.get("type") not in (None, "custom")]
    kwargs["tools"] = [tool for tool in request.get("tools", []) if tool not in server_tools]
    # No signed replay blocks remain in this equivalent input, so it does
    # not need a thinking-prefix binding override either.
    if "thinking" in kwargs:
        kwargs["thinking"] = {key: value for key, value in kwargs["thinking"].items()
                              if key != "block_binding"}
    equivalent = _counting_messages(messages)
    if server_tools:
        equivalent.append({"role": "user", "content": [
            {"type": "text", "text": json.dumps(server_tools, ensure_ascii=False)},
        ]})
    counted = client.messages.count_tokens(messages=equivalent, **kwargs)
    tokens = counted.input_tokens
    if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0:
        raise ValueError("Research token counter returned an invalid input count.")
    return tokens + len(server_tools) * _SERVER_TOOL_OVERHEAD_TOKENS


def _field(node: Any, name: str) -> Any:
    return node.get(name) if isinstance(node, dict) else getattr(node, name, None)


def without_thinking(messages: list[dict]) -> list[dict]:
    """Convert visible research notes to text; omit signed/redacted blocks.

    A submission that turns thinking off (``disabled``, or ``between_tools``
    on Sonnet 5.5, which takes no ``block_binding``) also changes the tools,
    so signed thinking cannot be replayed under its original prefix. Keep
    its readable notes as ordinary input rather than throwing away extracted
    research. A submission that must keep adaptive thinking never calls
    this: it replays every block unchanged under ``drop_block``.
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
    whose results became notes. Thinking blocks pass through untouched: a
    submission that turned thinking off has already converted them, and one
    that kept adaptive thinking carries ``drop_block`` for this edit.
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
