"""One-request live canary for fetched-page elision in saved chat history.

Phase 2 of the chat-history compaction plan
(``docs/plans/CHAT_HISTORY_COMPACTION_2026-09-22.md``) drops the text of
every page the chat fetched when a turn is saved, keeping the page's URL,
title and retrieval time. Chat fetches carry citations, so a reply that
quoted the page carries ``char_location`` citations whose character offsets
point into the ORIGINAL text. The first run of this canary (2026-09-23)
showed the API checks those offsets against the document the citation lands
on: it refused the saved shape with ``Start index 2406 is beyond document
length 257``. The trim was reworked, so a trimmed page's citations are now
removed and the passages they quoted kept in the page's note. This sends
that new saved shape once and says what the provider did with it. A saved
history the provider rejected would fail every later message in that
project, which is why the trim stays off until this passes.

Built from production code, not a hand-made copy: the conversation passes
through ``conversation._committed_messages`` (the same commit transform a
real turn takes), the request carries ``conversation._chat_tools()`` (the
web fetch tool with citations on) and goes through
``sanitize_messages_for_resend`` and ``repair_document_citations`` like
every chat request. The trim ships
switched off (``settings.ELIDE_FETCHED_PAGE_TEXT``, since 1.21.0) until
this canary passes, so the commit transform is called with the trim forced
on: what is under test is the shape that switch would produce. The system
prompt is a single short line, adaptive thinking runs at ``low`` effort and
the output ceiling is small, because the question is whether the history is
accepted, not what the model writes.

Opt-in and bounded, like ``tools/qc_verifier_canary.py``: without ``--run``
nothing is sent. With ``--run`` it makes ONE request on the interview model.
``--control`` sends the same conversation WITHOUT the elision instead — one
more request, only worth making if the main run is rejected, to tell "the
elided page is refused" apart from "this synthetic conversation is refused".

Usage (Windows; runs as written in PowerShell and in Command Prompt):

    .\\.venv\\Scripts\\python tools\\fetch_elision_canary.py --run
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend import settings  # noqa: E402
from backend.api_key_store import key_status  # noqa: E402
from backend.llm.client import MissingApiKeyError, get_client  # noqa: E402
from backend.llm.citations import repair_document_citations  # noqa: E402
from backend.llm.conversation import (  # noqa: E402
    _chat_tools,
    _committed_messages,
)
from backend.llm.history_hygiene import FETCHED_PAGE_NOTE_PREFIX  # noqa: E402
from backend.research.resend_sanitizer import (  # noqa: E402
    sanitize_messages_for_resend,
)

_URL = "https://example.com/build-a-spec/fetch-elision-canary"
_TITLE = "Build-a-Spec fetch elision canary page"
# The shape of a real server-tool id; nothing about it is special.
_USE_ID = "srvtoolu_01BuildASpecFetchCanary01"
_ASK = f"Read {_URL} and tell me the canary value it gives."
_FOLLOW_UP = "Thanks. Reply with the single word OK."
_CITED = "The canary value for this test page is forty-two."

# Synthetic and deliberately neutral: it states nothing about any code or
# standard. Long enough that the elision note replaces it, and the cited
# sentence sits far past the end of that note, where the first run's
# citation broke.
_PAGE = (
    "This is a synthetic test page. Build-a-Spec's fetch elision canary "
    "places it in a saved conversation to check how the Messages API treats "
    "a citation into a fetched page whose text has since been replaced.\n\n"
    + "".join(
        f"Paragraph {n}. This sentence is filler text that exists only to "
        "give the page some length, so that the citation below points well "
        "past the end of the short note that replaces the page.\n"
        for n in range(1, 13)
    )
    + f"\n{_CITED}\n\nEnd of the synthetic test page.\n"
)


@dataclass(frozen=True)
class CanaryRequest:
    """The request the canary sends, and the facts it reports about it."""

    request: dict[str, Any]
    page_chars: int
    saved_chars: int
    cited_start: int
    cited_end: int
    control: bool


def _conversation() -> tuple[list[dict[str, Any]], int, int]:
    """One committed turn: the user's ask, a fetch, and a cited reply."""
    start = _PAGE.index(_CITED)
    end = start + len(_CITED)
    turn = [
        # _committed_messages rebuilds the first user message from the
        # user's own text; its content here is never used.
        {"role": "user", "content": [{"type": "text", "text": _ASK}]},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "I'll read that page."},
                {
                    "type": "server_tool_use",
                    "id": _USE_ID,
                    "name": "web_fetch",
                    "input": {"url": _URL},
                },
                {
                    "type": "web_fetch_tool_result",
                    "tool_use_id": _USE_ID,
                    "content": {
                        "type": "web_fetch_result",
                        "url": _URL,
                        "content": {
                            "type": "document",
                            "source": {
                                "type": "text",
                                "media_type": "text/plain",
                                "data": _PAGE,
                            },
                            "title": _TITLE,
                            "citations": {"enabled": True},
                        },
                        "retrieved_at": "2026-09-22T12:00:00Z",
                    },
                },
                {"type": "text", "text": "The page says "},
                {
                    "type": "text",
                    "text": "the canary value is forty-two",
                    "citations": [
                        {
                            "type": "char_location",
                            "cited_text": _PAGE[start:end],
                            "document_index": 0,
                            "document_title": _TITLE,
                            "start_char_index": start,
                            "end_char_index": end,
                        }
                    ],
                },
                {"type": "text", "text": "."},
            ],
        },
    ]
    return turn, start, end


def _page_data(messages: list[dict[str, Any]]) -> str:
    for message in messages:
        for block in message.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "web_fetch_tool_result":
                return block["content"]["content"]["source"]["data"]
    raise AssertionError("The canary conversation lost its fetched page.")


def _citation_count(messages: list[dict[str, Any]]) -> int:
    return sum(
        len(block.get("citations") or [])
        for message in messages
        for block in message.get("content") or []
        if isinstance(block, dict)
    )


def build_request(*, max_tokens: int, control: bool = False) -> CanaryRequest:
    """The one request, built from the production commit transform.

    Pure: no key, no client, no network. ``control`` keeps the page text.
    The page-text trim is forced on whatever the setting says, because the
    canary exists to decide whether that setting can be turned on.
    """
    turn, start, end = _conversation()
    if control:
        committed = [
            {"role": "user", "content": [{"type": "text", "text": _ASK}]},
            turn[1],
        ]
    else:
        committed = _committed_messages(turn, _ASK, elide_fetched_pages=True)
        # The canary is only meaningful if the elision really ran, and ran
        # the way a saved turn now does: the page replaced by its note, the
        # quoted sentence kept in the note, the citation into the old text
        # gone. Fail loudly rather than send any other shape as a pass.
        saved = _page_data(committed)
        if not saved.startswith(FETCHED_PAGE_NOTE_PREFIX) or saved == _PAGE:
            raise AssertionError(
                "The commit transform did not replace the fetched page text."
            )
        if _CITED not in saved or _citation_count(committed):
            raise AssertionError(
                "The commit transform did not fold the reply's citation into "
                "the page note."
            )
    # The same two passes every chat request takes, in the same order.
    messages = repair_document_citations(
        sanitize_messages_for_resend(
            [
                *committed,
                {"role": "user", "content": [{"type": "text", "text": _FOLLOW_UP}]},
            ]
        )
    )
    sent = _page_data(messages)
    request = {
        "model": settings.INTERVIEW_MODEL,
        "max_tokens": max_tokens,
        "system": [
            {
                "type": "text",
                "text": (
                    "You are a helpful assistant. Answer the user's last "
                    "message briefly."
                ),
            }
        ],
        "messages": messages,
        "tools": _chat_tools(),
        "thinking": {"type": "adaptive"},
        # Keep the diagnostic inexpensive: the history's acceptance is under
        # test, not the quality of the answer.
        "output_config": {"effort": "low"},
    }
    return CanaryRequest(
        request=request,
        page_chars=len(_PAGE),
        saved_chars=len(sent),
        cited_start=start,
        cited_end=end,
        control=control,
    )


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--run",
        action="store_true",
        help="Make the single paid provider request.",
    )
    parser.add_argument(
        "--control",
        action="store_true",
        help=(
            "Send the conversation WITHOUT the elision (one more request, "
            "only useful when the main run is rejected)."
        ),
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=1024,
        help="Output-token ceiling for the one request (default: 1024).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    status = key_status()
    print(
        "Configured API key: "
        f"{'yes' if status.get('present') else 'no'} "
        f"(source: {status.get('source', 'none')})."
    )
    if not args.run:
        print("No request sent. Pass --run to execute the paid canary.")
        return 0
    if not 256 <= args.max_tokens <= 4096:
        print("--max-tokens must be between 256 and 4096.", file=sys.stderr)
        return 2

    built = build_request(max_tokens=args.max_tokens, control=args.control)
    if built.control:
        detail = (
            "control (page text kept); the reply cites characters "
            f"{built.cited_start}-{built.cited_end} of it."
        )
    else:
        detail = (
            f"elided page; the saved page is {built.saved_chars:,} of "
            f"{built.page_chars:,} characters, and the reply's citation into "
            f"characters {built.cited_start}-{built.cited_end} of the original "
            "was replaced by that passage, kept in the page's note."
        )
    print(f"Sending one request to {built.request['model']}: {detail}")
    try:
        client = get_client()
    except MissingApiKeyError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    try:
        with client.messages.stream(**built.request) as stream:
            response = stream.get_final_message()
    except Exception as exc:  # noqa: BLE001 - CLI reports provider diagnostics
        status_code = getattr(exc, "status_code", None)
        print(
            f"Fetch elision canary: the request failed: {type(exc).__name__}"
            f"{f' ({status_code})' if status_code else ''}: {exc}",
            file=sys.stderr,
        )
        if status_code == 400 and not built.control:
            print(
                "The provider REFUSED a saved conversation whose fetched page "
                "text was replaced by a note carrying the passage its reply "
                "quoted. Keep the page-text trim switched off "
                "(BUILD_A_SPEC_ELIDE_FETCHED_PAGES) until this is resolved. "
                "Run again with --control to check whether the same "
                "conversation is accepted with its page text intact.",
                file=sys.stderr,
            )
        elif status_code != 400:
            print(
                "This failure says nothing about the elision; fix the cause "
                "and run the canary again.",
                file=sys.stderr,
            )
        return 1

    stop = getattr(response, "stop_reason", None)
    if built.control:
        print(
            "Control passed: the provider accepted the conversation with its "
            f"page text intact (stop_reason={stop})."
        )
    else:
        print(
            "Fetch elision canary passed: the provider accepted a saved "
            "conversation whose fetched page text was replaced by a note "
            "carrying the passage its reply quoted "
            f"(stop_reason={stop}). Record this in the plan's Phase 2 section; "
            "the page-text trim's default can then be switched on."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
