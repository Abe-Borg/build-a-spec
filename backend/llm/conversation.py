"""Streaming conversation engine.

One synchronous generator per user turn: yields UI-ready event dicts
(``text_delta`` / ``turn_complete`` / ``error``) that the FastAPI layer
serializes as Server-Sent Events. History lives on a :class:`SessionState`
owned by the caller (``backend.sessions``).

Tool seam
---------
``_TOOLS`` is intentionally empty in Phase 1. Phase 2 registers the
document tools here (``apply_spec_edits``, ``update_open_questions``,
``launch_research``) and this loop grows the tool-use dispatch +
continuation pass, mirroring the streaming ``pause_turn`` continuation
pattern in Spec Critic's ``requirements_research.py`` / ``verifier.py``.

The user message is appended to history only after a successful turn, so a
failed call can be retried without duplicating the message.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator

import anthropic

from .. import settings
from .client import MissingApiKeyError, get_client
from .prompts import SYSTEM_PROMPT

# Phase 2: document tools register here.
_TOOLS: list[dict[str, Any]] = []


@dataclass
class SessionState:
    """One conversation's accumulated state."""

    history: list[dict[str, Any]] = field(default_factory=list)

    def reset(self) -> None:
        self.history.clear()


def _system_blocks() -> list[dict[str, Any]]:
    """System prompt as a cache-anchored block list.

    ``cache_control`` on the system block gives prompt-cache hits across
    the (growing) interview at no behavior cost — the same
    ``system_prompt_with_cache`` posture as Spec Critic.
    """
    return [
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def _content_blocks_to_dicts(content: Any) -> list[dict[str, Any]]:
    """Serialize SDK content blocks into plain history dicts."""
    blocks: list[dict[str, Any]] = []
    for block in content:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            blocks.append({"type": "text", "text": block.text})
        elif block_type == "tool_use":  # pragma: no cover - Phase 2
            blocks.append(
                {
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                }
            )
    return blocks


def stream_user_turn(
    session: SessionState,
    user_text: str,
    *,
    model: str | None = None,
    max_tokens: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Run one user turn against the model, yielding UI event dicts.

    Yields ``{"type": "text_delta", "text": ...}`` for each streamed chunk,
    then ``{"type": "turn_complete", "stop_reason": ...}``. Any failure
    yields a single ``{"type": "error", "message": ...}`` and leaves the
    session history unchanged.
    """
    user_text = (user_text or "").strip()
    if not user_text:
        yield {"type": "error", "message": "Empty message."}
        return

    messages = list(session.history) + [
        {"role": "user", "content": [{"type": "text", "text": user_text}]}
    ]

    request: dict[str, Any] = {
        "model": model or settings.INTERVIEW_MODEL,
        "max_tokens": max_tokens or settings.INTERVIEW_MAX_TOKENS,
        "system": _system_blocks(),
        "messages": messages,
    }
    if _TOOLS:  # pragma: no cover - Phase 2
        request["tools"] = _TOOLS

    try:
        client = get_client()
        with client.messages.stream(**request) as stream:
            for delta in stream.text_stream:
                if delta:
                    yield {"type": "text_delta", "text": delta}
            final = stream.get_final_message()
    except MissingApiKeyError as exc:
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

    session.history.append(
        {"role": "user", "content": [{"type": "text", "text": user_text}]}
    )
    session.history.append(
        {
            "role": "assistant",
            "content": _content_blocks_to_dicts(final.content),
        }
    )
    yield {"type": "turn_complete", "stop_reason": final.stop_reason}
