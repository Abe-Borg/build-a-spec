"""Scripted fake Anthropic streaming client (hermetic tests).

Grown from the Phase 1 fake (which spoke only text) to script whole
multi-round turns: each entry is a "turn" the next ``stream()`` call
replays — text chunks, final content blocks (text and/or tool_use), and a
stop reason. An entry that is an Exception instance is raised instead,
for failure-path tests. Mirrors the fake-client convention of Spec
Critic's suite.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any


def text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def tool_use_block(
    tool_id: str, name: str, tool_input: dict[str, Any]
) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=tool_id, name=name, input=tool_input)


def text_turn(chunks: list[str], stop_reason: str = "end_turn") -> SimpleNamespace:
    """A response that streams ``chunks`` and ends the turn."""
    return SimpleNamespace(
        chunks=list(chunks),
        content=[text_block("".join(chunks))],
        stop_reason=stop_reason,
    )


def tool_turn(
    chunks: list[str],
    tool_input: dict[str, Any],
    *,
    tool_id: str = "toolu_fake_1",
    name: str = "apply_spec_edits",
    stop_reason: str = "tool_use",
) -> SimpleNamespace:
    """A response that streams ``chunks`` then requests a tool call.

    ``stop_reason`` other than ``tool_use`` (e.g. ``max_tokens``) simulates
    a response truncated mid-tool-call.
    """
    content: list[SimpleNamespace] = []
    text = "".join(chunks)
    if text:
        content.append(text_block(text))
    content.append(tool_use_block(tool_id, name, tool_input))
    return SimpleNamespace(
        chunks=list(chunks), content=content, stop_reason=stop_reason
    )


class _FakeStreamCtx:
    def __init__(self, turn: SimpleNamespace):
        self._turn = turn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def text_stream(self):
        yield from self._turn.chunks

    def get_final_message(self):
        return SimpleNamespace(
            content=self._turn.content, stop_reason=self._turn.stop_reason
        )


class _FakeMessages:
    def __init__(self, turns: list[Any]):
        self._turns = list(turns)
        self.requests: list[dict[str, Any]] = []

    def stream(self, **request):
        self.requests.append(request)
        if not self._turns:
            raise AssertionError("Fake client got more requests than scripted turns.")
        turn = self._turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        return _FakeStreamCtx(turn)

    @property
    def last_request(self) -> dict[str, Any] | None:
        return self.requests[-1] if self.requests else None


class FakeClient:
    """``FakeClient([...turns...])`` — turns from :func:`text_turn` /
    :func:`tool_turn`, or Exception instances to raise on that round."""

    def __init__(self, turns: list[Any]):
        self.messages = _FakeMessages(turns)
