"""Server-tool use/result pairing — the invariant every outgoing request needs.

A ``server_tool_use`` block (web search, web fetch, code execution) is only
valid in a conversation when the matching result block is there too. Send an
assistant message containing a bare ``server_tool_use`` and the API rejects
the whole request — so one such block, committed once, poisons every later
turn of that session AND the saved project file. That is exactly what a stop
mid-search used to write: the stop truncation stripped dangling *client*
``tool_use`` blocks and left the server ones.

Two properties make this harder than a per-message filter:

- **A valid pair can straddle two assistant messages.** After a
  ``pause_turn`` the use lands in the paused message and its result arrives
  in the next one, so a filter that looks at one message at a time sees a
  "dangling" use that is nothing of the sort. The pairing is therefore
  computed across every message it is given.
- **The result families are open-ended.** ``web_search_tool_result`` and
  ``web_fetch_tool_result`` are today's; the code-execution families
  (``bash_code_execution_tool_result``,
  ``text_editor_code_execution_tool_result``) already exist, and more will
  follow. Rather than enumerate them, a use counts as paired when *anything*
  in the conversation references its id, and only blocks whose type ends in
  ``_tool_result`` are eligible to be dropped as orphans. Unknown block types
  are never touched.

Both rules err the same way: toward keeping content. Deleting a completed
result or a citation block to satisfy a pairing rule would destroy real
retrieval evidence; leaving an unrecognized block alone costs nothing.

Duck-typed over plain dicts and SDK block objects, and copy-on-write
throughout: unchanged messages come back as the *same objects*, so the
research and QC engines keep re-sending ``response.content`` verbatim as the
``pause_turn`` contract requires.
"""
from __future__ import annotations

from typing import Any

# A scrub can empty an assistant message (a round that produced nothing but
# an interrupted search). Empty content is itself invalid, so callers with a
# better phrase pass one in; this is the neutral fallback.
DEFAULT_PLACEHOLDER = "[Interrupted tool call removed from the transcript.]"

# The CLIENT tool-result type. It refers to a client ``tool_use`` block and
# is none of this module's business — excluded from the server-result suffix
# rule below, which would otherwise not match it anyway.
_CLIENT_TOOL_RESULT = "tool_result"
_SERVER_RESULT_SUFFIX = "_tool_result"
_SERVER_TOOL_USE = "server_tool_use"


def _get(node: Any, key: str) -> Any:
    if isinstance(node, dict):
        return node.get(key)
    return getattr(node, key, None)


def _block_type(block: Any) -> str:
    value = _get(block, "type")
    return value if isinstance(value, str) else ""


def is_server_result_block(block: Any) -> bool:
    """True for a server-tool result block of any recognized family."""
    block_type = _block_type(block)
    return (
        block_type != _CLIENT_TOOL_RESULT
        and block_type.endswith(_SERVER_RESULT_SUFFIX)
    )


def _referenced_tool_use_ids(messages: list[Any]) -> set[str]:
    """Every ``tool_use_id`` any block in ``messages`` points at.

    Deliberately type-blind. A server use is treated as paired when anything
    at all claims its id — including a block family this build has never
    heard of, and including an error-shaped result. Being generous here can
    only cause a dangling use to be *kept*; being strict could delete a
    completed one.
    """
    referenced: set[str] = set()
    for message in messages:
        for block in _get(message, "content") or []:
            value = _get(block, "tool_use_id")
            if isinstance(value, str) and value:
                referenced.add(value)
    return referenced


def without_unpaired_server_tool_uses(
    messages: list[Any], *, placeholder: str = DEFAULT_PLACEHOLDER
) -> list[Any]:
    """Drop unpaired ``server_tool_use`` blocks and orphaned server results.

    Pairing is computed across the WHOLE list, so a use and result split by
    a ``pause_turn`` stay together. Returns the same list object when there
    is nothing to remove — the common case by a wide margin.
    """
    referenced = _referenced_tool_use_ids(messages)

    retained_use_ids: set[str] = set()
    has_candidate = False
    for message in messages:
        for block in _get(message, "content") or []:
            if _block_type(block) == _SERVER_TOOL_USE:
                has_candidate = True
                block_id = _get(block, "id")
                if isinstance(block_id, str) and block_id in referenced:
                    retained_use_ids.add(block_id)
            elif is_server_result_block(block):
                has_candidate = True
    if not has_candidate:
        return messages

    def _keep(block: Any) -> bool:
        block_type = _block_type(block)
        if block_type == _SERVER_TOOL_USE:
            block_id = _get(block, "id")
            return isinstance(block_id, str) and block_id in retained_use_ids
        if is_server_result_block(block):
            target = _get(block, "tool_use_id")
            # A result with no id at all is unmappable, not orphaned —
            # unknown mappings are kept.
            if not isinstance(target, str) or not target:
                return True
            return target in retained_use_ids
        return True

    changed = False
    sanitized = list(messages)
    for index, message in enumerate(messages):
        content = _get(message, "content") or []
        kept = [block for block in content if _keep(block)]
        if len(kept) == len(content):
            continue
        if not kept:
            if _get(message, "role") != "assistant":
                # Emptying a non-assistant message would be a stranger
                # outcome than whatever we were about to fix. Leave it.
                continue
            kept = [{"type": "text", "text": placeholder}]
        changed = True
        if isinstance(message, dict):
            sanitized[index] = {**message, "content": kept}
        else:
            sanitized[index] = {"role": _get(message, "role"), "content": kept}
    return sanitized if changed else messages


def count_unpaired_server_tool_uses(messages: list[Any]) -> int:
    """How many ``server_tool_use`` blocks would be dropped. For diagnostics."""
    referenced = _referenced_tool_use_ids(messages)
    return sum(
        1
        for message in messages
        for block in (_get(message, "content") or [])
        if _block_type(block) == _SERVER_TOOL_USE
        and not (
            isinstance(_get(block, "id"), str) and _get(block, "id") in referenced
        )
    )
