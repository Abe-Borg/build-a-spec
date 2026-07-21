"""Streaming conversation engine with document tool-use.

One synchronous generator per user turn: yields UI-ready event dicts that
the FastAPI layer serializes as Server-Sent Events. History and the
document store live on a :class:`SessionState` owned by the caller
(``backend.sessions``).

Phase 2 tool loop
-----------------
``_TOOLS`` registers ``apply_spec_edits``. A turn is a continuation loop
(the streaming continuation pattern from Spec Critic's
``requirements_research.py``): stream a response; if it stops with
``tool_use``, apply the edits to the session's :class:`DocumentStore`
(transactionally — an invalid batch becomes an ``is_error`` tool result
the model can correct), emit a ``doc_patch`` event, send the tool results
back, and stream again — until the model ends the turn or the round
budget runs out.

Turn atomicity covers both stores: history mutates and the document turn
commits (one undo snapshot per changed turn) only after a fully
successful turn. Every failure path yields one ``error`` event, rolls the
document back to its pre-turn state, and leaves history unchanged, so a
resend never duplicates anything.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterator

import anthropic

from .. import settings
from ..spec_doc import (
    APPLY_SPEC_EDITS_TOOL,
    DocumentStore,
    SpecEditError,
    open_questions,
    outline,
)
from .client import MissingApiKeyError, get_client
from .prompts import SYSTEM_PROMPT

_TOOLS: list[dict[str, Any]] = [APPLY_SPEC_EDITS_TOOL]

# Ceiling on tool-use continuation rounds within one user turn. Each round
# can carry an arbitrarily large edit batch, so a well-prompted model never
# gets near this; hitting it is treated as a failed (retry-safe) turn.
MAX_TOOL_ROUNDS = 10


@dataclass
class SessionState:
    """One conversation's accumulated state: history + document.

    ``generation`` increments whenever the session is replaced out from
    under a possibly-streaming turn (reset, project load); an in-flight
    turn compares it before touching the store or committing, so a zombie
    turn can never pollute the fresh session.
    """

    history: list[dict[str, Any]] = field(default_factory=list)
    doc: DocumentStore = field(default_factory=DocumentStore)
    generation: int = 0

    def reset(self) -> None:
        self.history.clear()
        self.doc.reset()
        self.generation += 1


class _SessionInvalidated(RuntimeError):
    """The session was reset/replaced while this turn was still streaming."""


def _system_blocks(session: SessionState) -> list[dict[str, Any]]:
    """System prompt blocks: stable cached prompt + live document outline.

    ``cache_control`` sits on the stable block, so the growing interview
    still hits the prompt cache; the outline block after it changes per
    turn and is intentionally outside the cached prefix. The outline keeps
    the model's map of element ids current even after undo/redo or a
    project resume.
    """
    return [
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": "Current specification document:\n" + outline(session.doc.doc),
        },
    ]


def _content_blocks_to_dicts(content: Any) -> list[dict[str, Any]]:
    """Serialize SDK content blocks into plain history dicts."""
    blocks: list[dict[str, Any]] = []
    for block in content:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            blocks.append({"type": "text", "text": block.text})
        elif block_type == "tool_use":
            blocks.append(
                {
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                }
            )
    return blocks


def _run_tool(
    session: SessionState, block: Any
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Execute one tool_use block.

    Returns ``(tool_result_block, ui_events)``. Tool failures become
    ``is_error`` results for the model to correct — they never abort the
    turn.
    """
    if block.name != "apply_spec_edits":
        return (
            {
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": f"Unknown tool: {block.name}",
                "is_error": True,
            },
            [],
        )
    edits = (block.input or {}).get("edits")
    try:
        applied = session.doc.apply_edits(edits)
    except SpecEditError as exc:
        return (
            {
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": (
                    f"Edit batch rejected (nothing was applied): {exc}\n\n"
                    "Current specification document:\n"
                    + outline(session.doc.doc)
                ),
                "is_error": True,
            },
            [],
        )
    result = {
        "type": "tool_result",
        "tool_use_id": block.id,
        "content": json.dumps(
            {"applied": applied, "outline": outline(session.doc.doc)},
            ensure_ascii=False,
        ),
    }
    patch = {
        "type": "doc_patch",
        "ops": applied,
        "doc": session.doc.snapshot(),
    }
    return result, [patch]


def stream_user_turn(
    session: SessionState,
    user_text: str,
    *,
    model: str | None = None,
    max_tokens: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Run one user turn against the model, yielding UI event dicts.

    Event order: ``text_delta`` chunks (across all continuation rounds)
    interleaved with ``doc_patch`` after each applied edit batch, then —
    on success — ``open_questions`` (if the document changed) and
    ``turn_complete``. Any failure yields a single ``error`` event, rolls
    the document back, and leaves history unchanged.
    """
    user_text = (user_text or "").strip()
    if not user_text:
        yield {"type": "error", "message": "Empty message."}
        return

    new_messages: list[dict[str, Any]] = [
        {"role": "user", "content": [{"type": "text", "text": user_text}]}
    ]
    session.doc.begin_turn()
    generation = session.generation

    def check_session() -> None:
        if session.generation != generation:
            raise _SessionInvalidated(
                "The session was reset while this turn was streaming; "
                "the turn was discarded."
            )

    def request_kwargs() -> dict[str, Any]:
        return {
            "model": model or settings.INTERVIEW_MODEL,
            "max_tokens": max_tokens or settings.INTERVIEW_MAX_TOKENS,
            "system": _system_blocks(session),
            "messages": list(session.history) + new_messages,
            "tools": _TOOLS,
        }

    stop_reason: str | None = None
    doc_changed = False
    committed = False
    try:
        client = get_client()
        for _round in range(MAX_TOOL_ROUNDS):
            check_session()
            with client.messages.stream(**request_kwargs()) as stream:
                for delta in stream.text_stream:
                    if delta:
                        yield {"type": "text_delta", "text": delta}
                final = stream.get_final_message()

            content = _content_blocks_to_dicts(final.content)
            stop_reason = final.stop_reason
            if stop_reason != "tool_use":
                # A truncated response (e.g. max_tokens) can still carry
                # tool_use blocks; committing one without a tool_result
                # would make every later request invalid. Keep the text.
                content = [b for b in content if b["type"] != "tool_use"] or [
                    {"type": "text", "text": "[Response was cut off before completion.]"}
                ]
                new_messages.append({"role": "assistant", "content": content})
                break
            new_messages.append({"role": "assistant", "content": content})

            tool_results: list[dict[str, Any]] = []
            for block in final.content:
                if getattr(block, "type", None) != "tool_use":
                    continue
                check_session()
                result, ui_events = _run_tool(session, block)
                tool_results.append(result)
                for event in ui_events:
                    yield event
            new_messages.append({"role": "user", "content": tool_results})
        else:
            raise RuntimeError(
                f"Turn exceeded {MAX_TOOL_ROUNDS} tool rounds; aborted."
            )
    except MissingApiKeyError as exc:
        yield {"type": "error", "message": str(exc)}
        return
    except _SessionInvalidated as exc:
        # The fresh/loaded session must stay exactly as the user made it —
        # nothing was applied after the generation change.
        yield {"type": "error", "message": str(exc)}
        return
    except anthropic.APIStatusError as exc:
        yield {
            "type": "error",
            "message": f"Anthropic API error ({exc.status_code}): {exc.message}",
        }
        return
    except anthropic.APIConnectionError:
        yield {
            "type": "error",
            "message": "Could not reach the Anthropic API. Check your connection and try again.",
        }
        return
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI, never raised
        yield {"type": "error", "message": f"Unexpected error: {exc}"}
        return
    else:
        if session.generation != generation:
            # Reset/load won the race after the last round: leave the
            # fresh session untouched and discard this turn.
            yield {
                "type": "error",
                "message": "The session was reset while this turn was "
                "streaming; the turn was discarded.",
            }
            return
        session.history.extend(new_messages)
        doc_changed = session.doc.commit_turn()
        committed = True
    finally:
        # Runs on every exit — including GeneratorExit when the SSE client
        # disconnects mid-stream, which no except clause above can see.
        # Anything short of a committed turn rolls the document back.
        if not committed:
            session.doc.rollback_turn()

    if doc_changed:
        # doc_patch snapshots stream mid-turn, before the version commit;
        # this snapshot carries the committed version pointer.
        yield {"type": "doc_snapshot", "doc": session.doc.snapshot()}
        yield {
            "type": "open_questions",
            "items": open_questions(session.doc.doc),
        }
    yield {"type": "turn_complete", "stop_reason": stop_reason}
