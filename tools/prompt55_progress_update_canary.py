"""One-turn live canary: does the interview's reply land AFTER its last tool call?

The 5.5 prompting upgrade, session P55-2 (``docs/plans/prompt55/``). On
Claude Sonnet 5.5, a note longer than a sentence or two that the model
writes BETWEEN tool calls comes back as a progress-update ``thinking``
block, not as ``text``. The chat collapses thinking into its "Thinking"
disclosure, and commit drops every thinking block, so a question or a brief
written before the ``suggest_prompts`` call could vanish from the chat
bubble, the saved conversation, the fact harvest and the model's own memory.
P55-2 tells the model to make every tool call first and write its reply
after the last one, where it stays a ``text`` block. This canary checks
whether that ordering holds on the real model.

It runs ONE real interview turn through the production engine
(``stream_user_turn``) on a fresh in-memory session with the default
(generic) module. The canned message establishes the section, country,
project type and client, and asks for the header, two PART 1 provisions and
the next questions, so the turn edits, stages suggested replies and asks.
Every request the engine builds is re-sent through the beta endpoint with
``thinking.display = "updates"`` (beta ``thinking-display-updates-2026-08-18``)
and ``max_tokens`` capped; everything else is the production request, byte
for byte. Under ``"updates"`` reasoning is hidden and every non-empty
thinking block IS a progress note, so the canary can tell the two apart.

It prints each round's blocks (type and length), the text of every progress
note (the conversation is synthetic), the usage, and a verdict:

- **pass** when the turn's last round ends normally with closing ``text`` of
  at least 80 characters that asks a question, and no progress note asks one;
- **fail** otherwise, naming the block that broke the rule.

A failed request (a 400, for example) or a refusal prints the error and
exits nonzero. The production app keeps ``THINKING_DISPLAY`` at
``summarized`` (decision D2 in the tracker): this canary only measures
whether ``"updates"`` would be worth a later change, and the ordering fix
does not wait on it.

Opt-in and bounded, like ``tools/fetch_elision_canary.py``: without
``--run`` nothing is sent. With ``--run`` it makes the requests of one turn
(typically three or four rounds) on the interview model with your stored
key, well under a dollar at list prices. Like any chat turn, it leaves a
trace in the trace folder when tracing is on.

Usage (Windows; runs as written in PowerShell and in Command Prompt):

    .\\.venv\\Scripts\\python tools\\prompt55_progress_update_canary.py --run
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend import settings  # noqa: E402
from backend.api_key_store import key_status  # noqa: E402
from backend.llm import conversation  # noqa: E402
from backend.llm.client import MissingApiKeyError, get_client  # noqa: E402

BETA = "thinking-display-updates-2026-08-18"
DEFAULT_MAX_TOKENS = 32_000
MIN_MAX_TOKENS = 4_096
MAX_MAX_TOKENS = 64_000
MIN_CLOSING_CHARS = 80

USER_MESSAGE = (
    "I'm writing the fire suppression specification for a new data center "
    "in Phoenix, Arizona, United States. The client is Northwind Cloud. This "
    "section is 21 13 13 Wet-Pipe Sprinkler Systems. Please set the section "
    "header, draft two provisions in PART 1, and then ask me the next "
    "questions you need answered."
)


@dataclass
class Round:
    """One request of the turn: what the engine built, what was sent, and
    what came back (or how it failed)."""

    request: dict[str, Any]
    sent: dict[str, Any]
    response: Any = None
    error: BaseException | None = None


def progress_update_request(
    production: dict[str, Any], *, max_tokens: int
) -> dict[str, Any]:
    """The production request, re-shaped for the progress-update beta.

    Pure. Three things differ from what the engine built, and nothing else:
    ``thinking.display`` is ``"updates"``, ``max_tokens`` is capped at
    ``max_tokens`` (never raised), and the beta rides ``betas``. The input
    is never mutated; the dicts it holds are shared, as the engine shares
    them between its own rounds.

    A production request can carry betas of its own in
    ``extra_headers["anthropic-beta"]`` (the preserved-thinking beta on a
    request the harness edited, the 5.5 prompting upgrade, P55-6). The SDK
    lets ``extra_headers`` override the header ``betas`` builds, so those
    betas move into ``betas`` beside this one — the header the request sends
    is the same, and the progress-update beta is not lost.
    """
    sent = dict(production)
    thinking = dict(production.get("thinking") or {"type": "adaptive"})
    thinking["display"] = "updates"
    sent["thinking"] = thinking
    requested = production.get("max_tokens")
    sent["max_tokens"] = (
        min(int(requested), max_tokens) if requested else max_tokens
    )
    betas = [BETA]
    headers = production.get("extra_headers")
    if headers:
        kept: dict[str, Any] = {}
        for key, value in dict(headers).items():
            if isinstance(key, str) and key.lower() == "anthropic-beta":
                for beta in str(value or "").split(","):
                    beta = beta.strip()
                    if beta and beta not in betas:
                        betas.append(beta)
            else:
                kept[key] = value
        if kept:
            sent["extra_headers"] = kept
        else:
            sent.pop("extra_headers", None)
    sent["betas"] = betas
    return sent


class _RecordingStream:
    """The engine's view of one stream: iterates and finalizes as the SDK
    stream does, and records the message the engine reads."""

    def __init__(self, stream: Any, round_: Round) -> None:
        self._stream = stream
        self._round = round_

    def __iter__(self):
        yield from self._stream

    def get_final_message(self) -> Any:
        message = self._stream.get_final_message()
        self._round.response = message
        return message

    @property
    def current_message_snapshot(self) -> Any:
        message = self._stream.current_message_snapshot
        self._round.response = message
        return message

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


class _RecordingManager:
    """Wraps the beta stream's context manager: the SDK sends the request
    when the context is entered, so a refused request is recorded here."""

    def __init__(self, manager: Any, round_: Round) -> None:
        self._manager = manager
        self._round = round_

    def __enter__(self) -> _RecordingStream:
        try:
            stream = self._manager.__enter__()
        except BaseException as exc:
            self._round.error = exc
            raise
        return _RecordingStream(stream, self._round)

    def __exit__(self, *exc: Any) -> Any:
        return self._manager.__exit__(*exc)


class ProgressUpdateClient:
    """Stands in for the Anthropic client inside ``stream_user_turn``.

    ``client.messages.stream(**kwargs)`` is the only call the chat engine
    makes; each one is re-sent as
    ``client.beta.messages.stream(**progress_update_request(kwargs), ...)``.
    After a request fails, nothing more is sent: the engine's own retry
    (its ``thinking.display`` degrade) gets the same error back, so a
    refused ``"updates"`` request is reported, never silently retried
    without it.
    """

    def __init__(self, client: Any, *, max_tokens: int) -> None:
        self._client = client
        self._max_tokens = max_tokens
        self.rounds: list[Round] = []
        # The engine's own ``error`` events: a turn can fail after its
        # requests succeeded (a stream that breaks mid-iteration, the tool
        # round ceiling), and that must not read as a verdict.
        self.turn_errors: list[str] = []
        self.messages = self

    @property
    def failure(self) -> BaseException | None:
        for round_ in self.rounds:
            if round_.error is not None:
                return round_.error
        return None

    def stream(self, **kwargs: Any) -> _RecordingManager:
        earlier = self.failure
        if earlier is not None:
            raise earlier
        round_ = Round(
            request=kwargs,
            sent=progress_update_request(kwargs, max_tokens=self._max_tokens),
        )
        self.rounds.append(round_)
        try:
            manager = self._client.beta.messages.stream(**round_.sent)
        except BaseException as exc:
            round_.error = exc
            raise
        return _RecordingManager(manager, round_)


# --- Reading a response ----------------------------------------------------


def _field(block: Any, name: str) -> Any:
    if isinstance(block, dict):
        return block.get(name)
    return getattr(block, name, None)


def _blocks(response: Any) -> list[Any]:
    return list(_field(response, "content") or [])


def _notes(round_: Round) -> list[tuple[int, str]]:
    """(block index, text) for every progress note in a round.

    Under ``display: "updates"`` reasoning comes back empty, so any
    ``thinking`` block with text is a progress note.
    """
    notes = []
    for index, block in enumerate(_blocks(round_.response)):
        if _field(block, "type") == "thinking":
            text = (_field(block, "thinking") or "").strip()
            if text:
                notes.append((index, text))
    return notes


def _closing_text(response: Any) -> tuple[str, str]:
    """The text after the round's last non-text block, and the type of the
    block that ends the round ("" when it has none)."""
    blocks = _blocks(response)
    if not blocks:
        return "", ""
    last_type = str(_field(blocks[-1], "type") or "")
    trailing: list[str] = []
    for block in reversed(blocks):
        if _field(block, "type") != "text":
            break
        trailing.append(_field(block, "text") or "")
    return "".join(reversed(trailing)).strip(), last_type


def _tool_calls(rounds: list[Round]) -> list[str]:
    return [
        str(_field(block, "name") or "")
        for round_ in rounds
        for block in _blocks(round_.response)
        if _field(block, "type") == "tool_use"
    ]


@dataclass
class Verdict:
    passed: bool
    reasons: list[str] = field(default_factory=list)


def verdict(rounds: list[Round]) -> Verdict:
    """Pass or fail, with a reason for every rule a block broke."""
    if not rounds:
        return Verdict(False, ["No request was made."])
    for number, round_ in enumerate(rounds, start=1):
        if round_.error is not None:
            return Verdict(
                False,
                [f"Round {number}'s request failed: {_describe_error(round_.error)}"],
            )
        if round_.response is None:
            return Verdict(False, [f"Round {number} returned no message."])
    last = rounds[-1].response
    stop = _field(last, "stop_reason")
    if stop == "refusal":
        return Verdict(False, [f"The model declined the turn ({_refusal(last)})."])

    reasons: list[str] = []
    for number, round_ in enumerate(rounds, start=1):
        for index, text in _notes(round_):
            if "?" in text:
                reasons.append(
                    f"Round {number}, block {index}: a progress note asks a "
                    "question, so it would reach the chat only as a "
                    "collapsed thinking line."
                )
    if stop != "end_turn":
        reasons.append(
            f"The turn's last round stopped on {stop!r}, not end_turn."
        )
    closing, last_type = _closing_text(last)
    if not closing:
        reasons.append(
            "The turn's last round ends with "
            + (f"a {last_type!r} block" if last_type else "no content")
            + ", not closing text."
        )
    else:
        if len(closing) < MIN_CLOSING_CHARS:
            reasons.append(
                f"The closing text is {len(closing)} characters, under "
                f"{MIN_CLOSING_CHARS}."
            )
        if "?" not in closing:
            reasons.append("The closing text asks no question.")
    return Verdict(not reasons, reasons)


def _refusal(response: Any) -> str:
    details = _field(response, "stop_details")
    category = _field(details, "category") if details is not None else None
    return f"category: {category}" if category else "no category named"


def _describe_error(exc: BaseException) -> str:
    status = getattr(exc, "status_code", None)
    return f"{type(exc).__name__}{f' ({status})' if status else ''}: {exc}"


# --- The report ------------------------------------------------------------


def _usage_line(response: Any) -> str:
    usage = _field(response, "usage")
    if usage is None:
        return "usage not reported"
    parts = []
    for label, name in (
        ("input", "input_tokens"),
        ("cache read", "cache_read_input_tokens"),
        ("cache write", "cache_creation_input_tokens"),
        ("output", "output_tokens"),
    ):
        value = _field(usage, name)
        if isinstance(value, int) and not isinstance(value, bool):
            parts.append(f"{label} {value:,}")
    return ", ".join(parts) or "usage not reported"


def report(rounds: list[Round]) -> list[str]:
    """The printable account of the turn, round by round."""
    lines: list[str] = []
    for number, round_ in enumerate(rounds, start=1):
        if round_.error is not None:
            lines.append(f"Round {number}: request failed: {_describe_error(round_.error)}")
            continue
        response = round_.response
        stop = _field(response, "stop_reason")
        lines.append(f"Round {number} (stop_reason={stop}; {_usage_line(response)}):")
        for index, block in enumerate(_blocks(response)):
            kind = str(_field(block, "type") or "?")
            if kind == "thinking":
                text = (_field(block, "thinking") or "").strip()
                label = f"progress note, {len(text)} chars" if text else "empty"
            elif kind == "text":
                label = f"{len((_field(block, 'text') or '').strip())} chars"
            elif kind in {"tool_use", "server_tool_use"}:
                label = str(_field(block, "name") or "")
            else:
                label = ""
            lines.append(f"  [{index}] {kind}{f' — {label}' if label else ''}")
        for index, text in _notes(round_):
            lines.append(f"  progress note [{index}]: {text}")
    calls = _tool_calls(rounds)
    lines.append(
        "Tool calls in order: " + (", ".join(calls) if calls else "none") + "."
    )
    if calls:
        where = (
            "the last tool call"
            if calls[-1] == "suggest_prompts"
            else "called, but not last"
            if "suggest_prompts" in calls
            else "not called"
        )
        lines.append(f"suggest_prompts: {where}.")
    if rounds and rounds[-1].response is not None:
        closing, _last = _closing_text(rounds[-1].response)
        if closing:
            lines.append(f"Closing text ({len(closing)} chars):")
            lines.append(closing)
    return lines


def run_turn(client: Any, *, max_tokens: int) -> ProgressUpdateClient:
    """Drive one production turn with ``client`` wrapped for the beta."""
    wrapped = ProgressUpdateClient(client, max_tokens=max_tokens)
    session = conversation.SessionState()
    original = conversation.get_client
    conversation.get_client = lambda: wrapped
    try:
        for event in conversation.stream_user_turn(
            session, USER_MESSAGE, allow_compaction=False
        ):
            if event.get("type") == "error":
                wrapped.turn_errors.append(str(event.get("message") or ""))
    finally:
        conversation.get_client = original
    return wrapped


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--run",
        action="store_true",
        help="Run the one paid interview turn.",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=DEFAULT_MAX_TOKENS,
        help=(
            "Output-token ceiling for each request of the turn "
            f"(default: {DEFAULT_MAX_TOKENS})."
        ),
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
    if not MIN_MAX_TOKENS <= args.max_tokens <= MAX_MAX_TOKENS:
        print(
            f"--max-tokens must be between {MIN_MAX_TOKENS} and "
            f"{MAX_MAX_TOKENS}.",
            file=sys.stderr,
        )
        return 2
    try:
        client = get_client()
    except MissingApiKeyError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(
        f"Running one interview turn on {settings.INTERVIEW_MODEL} with "
        f'thinking.display "updates" ({BETA}).'
    )
    wrapped = run_turn(client, max_tokens=args.max_tokens)
    for line in report(wrapped.rounds):
        print(line)
    result = verdict(wrapped.rounds)
    failure = wrapped.failure
    if failure is not None:
        print(
            f"Progress-update canary: a request failed: {_describe_error(failure)}",
            file=sys.stderr,
        )
        return 1
    if wrapped.turn_errors:
        print(
            "Progress-update canary: the turn failed: "
            + "; ".join(wrapped.turn_errors),
            file=sys.stderr,
        )
        return 1
    if result.passed:
        print(
            "Progress-update canary passed: the reply came after the last "
            "tool call, as closing text that asks the questions, and no "
            "progress note asked one. Record the result in the P55-2 As "
            "built (docs/plans/prompt55/PROMPT55_PLAN.md)."
        )
        return 0
    print("Progress-update canary FAILED:", file=sys.stderr)
    for reason in result.reasons:
        print(f"- {reason}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
