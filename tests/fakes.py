"""Scripted fake Anthropic streaming client (hermetic tests).

Grown from the Phase 1 fake (which spoke only text) to script whole
multi-round turns: each entry is a "turn" the next ``stream()`` call
replays — text chunks, final content blocks (text and/or tool_use), and a
stop reason. An entry that is an Exception instance is raised instead,
for failure-path tests. Mirrors the fake-client convention of Spec
Critic's suite.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any


def text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def tool_use_block(
    tool_id: str, name: str, tool_input: dict[str, Any]
) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=tool_id, name=name, input=tool_input)


def token_usage(
    *,
    input: int = 0,
    output: int = 0,
    cache_read: int = 0,
    cache_write: int = 0,
    thinking: int = 0,
    searches: int = 0,
    fetches: int = 0,
) -> SimpleNamespace:
    """A billed-usage object shaped for the ledger (WI4 cost meter)."""
    return SimpleNamespace(
        input_tokens=input,
        output_tokens=output,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_write,
        output_tokens_details=SimpleNamespace(thinking_tokens=thinking),
        server_tool_use=SimpleNamespace(
            web_search_requests=searches, web_fetch_requests=fetches
        ),
    )


def text_turn(
    chunks: list[str],
    stop_reason: str = "end_turn",
    *,
    usage: SimpleNamespace | None = None,
) -> SimpleNamespace:
    """A response that streams ``chunks`` and ends the turn."""
    return SimpleNamespace(
        chunks=list(chunks),
        content=[text_block("".join(chunks))],
        stop_reason=stop_reason,
        usage=usage,
    )


def tool_turn(
    chunks: list[str],
    tool_input: dict[str, Any],
    *,
    tool_id: str = "toolu_fake_1",
    name: str = "apply_spec_edits",
    stop_reason: str = "tool_use",
    usage: SimpleNamespace | None = None,
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
        chunks=list(chunks), content=content, stop_reason=stop_reason, usage=usage
    )


def thinking_block(thinking: str = "", signature: str = "sig-fake") -> SimpleNamespace:
    """An adaptive-thinking block (Sonnet 5 display "omitted" → empty text)."""
    return SimpleNamespace(type="thinking", thinking=thinking, signature=signature)


def chat_search_blocks(query: str, urls: list[str]) -> list[SimpleNamespace]:
    """A ``server_tool_use``(web_search) + result pair for the chat loop."""
    return [
        SimpleNamespace(
            type="server_tool_use",
            id="srvtoolu_fake",
            name="web_search",
            input={"query": query},
        ),
        search_result_block(urls),
    ]


# ---------------------------------------------------------------------------
# Raw stream events (WI1: the chat loop now iterates SDK events, not
# text_stream). Builders mirror the anthropic SDK's raw-event shapes the
# engine consumes; ``_synthesize_events`` derives a plausible default
# sequence from a scripted turn's content so existing tool/text/thinking
# turns stream correctly with no per-test wiring.
# ---------------------------------------------------------------------------


def block_start_event(index: int, block_type: str, name: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        type="content_block_start",
        index=index,
        content_block=SimpleNamespace(type=block_type, name=name),
    )


def text_delta_event(index: int, text: str) -> SimpleNamespace:
    return SimpleNamespace(
        type="content_block_delta",
        index=index,
        delta=SimpleNamespace(type="text_delta", text=text),
    )


def thinking_delta_event(index: int, text: str) -> SimpleNamespace:
    return SimpleNamespace(
        type="content_block_delta",
        index=index,
        delta=SimpleNamespace(type="thinking_delta", thinking=text),
    )


def input_json_delta_event(index: int, partial: str) -> SimpleNamespace:
    return SimpleNamespace(
        type="content_block_delta",
        index=index,
        delta=SimpleNamespace(type="input_json_delta", partial_json=partial),
    )


def block_stop_event(index: int) -> SimpleNamespace:
    return SimpleNamespace(type="content_block_stop", index=index)


_STREAMED_BLOCK_TYPES = ("text", "thinking", "tool_use", "server_tool_use")


def _synthesize_events(
    content: list[SimpleNamespace], chunks: list[str]
) -> list[SimpleNamespace]:
    """Build a plausible raw-event sequence for a scripted turn's content.

    Each streamable block gets start → delta(s) → stop; the turn's text
    ``chunks`` stream as the first text block's deltas (so existing tests
    that assert on streamed text keep passing), other text blocks stream
    their full text, thinking blocks their (possibly empty) thinking, and
    tool blocks their JSON input. Result blocks carry no stream events.
    """
    events: list[SimpleNamespace] = []
    chunks = list(chunks or [])
    used_chunks = False
    idx = 0
    for block in content:
        btype = getattr(block, "type", None)
        if btype not in _STREAMED_BLOCK_TYPES:
            continue
        name = getattr(block, "name", "") or ""
        events.append(block_start_event(idx, btype, name))
        if btype == "text":
            if chunks and not used_chunks:
                events.extend(text_delta_event(idx, c) for c in chunks)
                used_chunks = True
            else:
                text = getattr(block, "text", "") or ""
                if text:
                    events.append(text_delta_event(idx, text))
        elif btype == "thinking":
            thinking = getattr(block, "thinking", "") or ""
            if thinking:
                events.append(thinking_delta_event(idx, thinking))
        else:  # tool_use / server_tool_use
            tool_input = getattr(block, "input", None) or {}
            events.append(input_json_delta_event(idx, json.dumps(tool_input)))
        events.append(block_stop_event(idx))
        idx += 1
    return events


def raw_turn(
    content: list[SimpleNamespace],
    *,
    stop_reason: str,
    chunks: list[str] | None = None,
    events: list[SimpleNamespace] | None = None,
) -> SimpleNamespace:
    """A scripted response with arbitrary content blocks (thinking,
    server tools, pause_turn shapes) for the chat loop's fake client.

    ``events`` overrides the synthesized raw-event stream when a test needs
    a precise ordering (e.g. thinking → text → tool)."""
    return SimpleNamespace(
        chunks=list(chunks or []),
        content=list(content),
        stop_reason=stop_reason,
        events=events,
    )


def request_context_text(request: dict) -> str:
    """The PROJECT CONTEXT block of a captured chat request.

    The context is the FIRST text block of the turn's user message (the
    user's own text follows it) — the Sonnet-unleashed context placement.
    Returns "" when the request has no such block.
    """
    for message in request.get("messages", []):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict) and first.get("type") == "text":
                text = first.get("text", "")
                if "PROJECT CONTEXT" in text:
                    return text
    return ""


class _FakeStreamCtx:
    def __init__(self, turn: SimpleNamespace):
        self._turn = turn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        """Yield the turn's raw stream events (explicit or synthesized)."""
        events = getattr(self._turn, "events", None)
        if events is None:
            events = _synthesize_events(
                self._turn.content, getattr(self._turn, "chunks", [])
            )
        yield from events

    @property
    def text_stream(self):
        yield from self._turn.chunks

    def get_final_message(self):
        return SimpleNamespace(
            content=self._turn.content,
            stop_reason=self._turn.stop_reason,
            usage=getattr(self._turn, "usage", None),
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


def bad_request(message: str) -> Any:
    """A real ``anthropic.BadRequestError`` (status 400) for scripting the
    thinking.display capability degrade."""
    import anthropic
    import httpx

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(400, request=request)
    return anthropic.BadRequestError(message, response=response, body=None)


# ---------------------------------------------------------------------------
# Phase 4: research-shaped responses (web server tools + usage telemetry)
# ---------------------------------------------------------------------------


def search_result_block(urls: list[str]) -> SimpleNamespace:
    """A ``web_search_tool_result`` block whose results carry ``urls``."""
    return SimpleNamespace(
        type="web_search_tool_result",
        content=[
            SimpleNamespace(type="web_search_result", url=url, title=f"t:{url}")
            for url in urls
        ],
    )


def fetch_blocks(url: str) -> list[SimpleNamespace]:
    """A ``server_tool_use``(web_fetch) + result pair for ``url``."""
    return [
        SimpleNamespace(type="server_tool_use", name="web_fetch", input={"url": url}),
        SimpleNamespace(
            type="web_fetch_tool_result",
            content={"type": "web_fetch_result", "url": url},
        ),
    ]


def usage(
    searches: int = 0,
    fetches: int = 0,
    *,
    input: int = 0,
    output: int = 0,
    cache_read: int = 0,
    cache_write: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        input_tokens=input,
        output_tokens=output,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_write,
        server_tool_use=SimpleNamespace(
            web_search_requests=searches, web_fetch_requests=fetches
        ),
    )


def research_response(
    *,
    items: list[dict] | None = None,
    searched_urls: list[str] | None = None,
    extra_blocks: list[SimpleNamespace] | None = None,
    stop_reason: str = "tool_use",
    searches: int | None = None,
    fetches: int = 0,
    tokens: dict[str, int] | None = None,
    tool_name: str = "submit_requirements_research",
) -> SimpleNamespace:
    """A terminal research response: search results + the output tool call.

    ``items`` are raw payload item dicts (the engine normalizes them).
    ``searched_urls`` become one web_search_tool_result block. ``searches``
    defaults to len(searched_urls) so the usage telemetry stays coherent.
    """
    content: list[SimpleNamespace] = []
    if searched_urls:
        content.append(search_result_block(searched_urls))
    content.extend(extra_blocks or [])
    if items is not None:
        content.append(
            tool_use_block(
                "toolu_research",
                tool_name,
                {"summary": "", "items": items},
            )
        )
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        usage=usage(
            searches if searches is not None else len(searched_urls or []),
            fetches,
            **(tokens or {}),
        ),
    )


def pause_response(
    *, searched_urls: list[str] | None = None, searches: int | None = None
) -> SimpleNamespace:
    """A ``pause_turn`` response mid-research (server tools still running)."""
    content: list[SimpleNamespace] = []
    if searched_urls:
        content.append(search_result_block(searched_urls))
    return SimpleNamespace(
        content=content,
        stop_reason="pause_turn",
        usage=usage(searches if searches is not None else len(searched_urls or [])),
    )


class SequencedFakeClient:
    """Fake client whose scripted turns are keyed by dimension.

    The research fan-out runs dimensions on parallel threads, so a single
    shared pop-in-order queue (``FakeClient``) would interleave
    nondeterministically. This client inspects the request's first user
    message and pops from the matching dimension's own queue (matched by
    ``key`` substring). Thread-safe.
    """

    def __init__(self, scripts: dict[str, list]):
        import threading

        self._scripts = {k: list(v) for k, v in scripts.items()}
        self._lock = threading.Lock()
        self.requests: list[dict] = []
        self.messages = self  # client.messages.stream(...)

    def stream(self, **request):
        with self._lock:
            self.requests.append(request)
            first_user = ""
            for message in request.get("messages", []):
                if message.get("role") == "user":
                    content = message.get("content")
                    first_user = content if isinstance(content, str) else ""
                    break
            for key, queue in self._scripts.items():
                if key in first_user:
                    if not queue:
                        raise AssertionError(
                            f"Fake research client: no scripted turns left "
                            f"for {key!r}."
                        )
                    turn = queue.pop(0)
                    break
            else:
                raise AssertionError(
                    "Fake research client: no script matches the request "
                    f"({first_user[:80]!r})."
                )
        if isinstance(turn, Exception):
            raise turn
        return _FakeStreamCtx(
            SimpleNamespace(chunks=[], content=turn.content, stop_reason=turn.stop_reason)
        ) if not hasattr(turn, "usage") else _FakeResearchStreamCtx(turn)


class _FakeResearchStreamCtx:
    """Stream context that returns the scripted response object as-is
    (preserving ``usage`` — ``_FakeStreamCtx`` rebuilds and drops it)."""

    def __init__(self, response: SimpleNamespace):
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def text_stream(self):
        yield from ()

    def get_final_message(self):
        return self._response
