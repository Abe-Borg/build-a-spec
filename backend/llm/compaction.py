"""Condense a long chat conversation, and read condensed turns back.

Compaction plan Phase 3 (``docs/plans/CHAT_HISTORY_COMPACTION_2026-09-22.md``).
Phases 1–2 keep machine payloads (stale outlines, fetched page text) out of
committed history. What remains is conversation, and a long enough session
still outgrows the model's context window: past it every request fails with
"prompt is too long", and the saved project carries that into every later
session. This module holds the parts of the answer that need no engine:

* :class:`CompactionRecord` — a model-written summary standing in for the
  oldest turns. ``session.history`` is never edited: the record is a VIEW
  the request builder sends (the summary plus every turn from
  ``keep_from`` on). The saved transcript, the harvest's turn numbering and
  figure placement all keep reading the full history.
* :func:`compacted_view` — that view, copy-on-write.
* :func:`summary_instruction` / :func:`extract_summary` — what the summary
  call is asked for, and the checks its reply must pass before it is used.
* recall — ``recall_conversation`` searches or re-reads condensed turns
  word for word, so a detail the summary dropped is one call away.
* :class:`CompactionRunner` — one background summary at a time per session
  object, abandoned (not cancelled) on reset or load, like the other runners.

The request itself — a fork of the chat request, so it reads the prefix the
last turn cached — is built in ``conversation.py``, which owns the chat's
system prompt, tools and cache breakpoints. This module stays a leaf so
``project.py`` can import it to persist the record.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

COMPACTION_RECORD_VERSION = 1

# The frame the summary rides in, and the frame recall results ride in. Both
# tags are made inert inside the text they frame (and inside each other):
# the summary quotes the user, and a recalled turn is the user's own words,
# so either could contain something that closes its frame early.
SUMMARY_FRAME_TAG = "earlier_conversation_summary"
RECALL_FRAME_TAG = "recalled_conversation"
LEDGERS_FRAME_TAG = "ledgers"

# The sections every summary must carry, in order. The first seven carry
# Anthropic's six retention items for client-side compaction (the
# claude-api skill, ``shared/model-migration.md``), adapted to spec work —
# exact values get a section of their own, because a paraphrased edition or
# flow rate is the detail a spec cannot afford to lose; the last is owner
# decision D4 — the decisions the project facts ledger is missing, which the
# fact harvest can later offer as suggestions.
SUMMARY_HEADINGS = (
    "Problems and how they were handled",
    "Options raised and set aside",
    "Decisions and reasons",
    "Preferences, constraints and corrections",
    "Where things stand",
    "Open and promised",
    "Exact details",
    "Decisions the ledgers are missing",
)

# A summary longer than this is a runaway, not a summary: rejected, and the
# conversation keeps its full view until the next attempt.
MAX_SUMMARY_CHARS = 200_000
# The excerpt of the first kept message the instruction quotes, so the model
# can find where its summary must stop.
_EXCERPT_CHARS = 160

# Estimating tokens from serialized characters. The app has no local
# tokenizer; the provider's own count of each turn's request calibrates the
# ratio per session (see ``calibrated_tokens_per_char``). Until the first
# turn has been measured, 3.5 characters per token — above the app's usual
# len/4, because Sonnet 5's tokenizer runs above it — errs toward
# condensing early rather than late.
DEFAULT_TOKENS_PER_CHAR = 1 / 3.5
_MIN_TOKENS_PER_CHAR = 1 / 8
_MAX_TOKENS_PER_CHAR = 1 / 1.5

# Routine condensing skips a cut that would condense less than this share
# of the conversation: when the kept turns ARE most of it, a summary saves
# little and still costs a cache rewrite. The backstop ignores this — any
# reduction helps there.
MIN_CONDENSE_FRACTION = 0.25

# Recall limits: enough to answer "what exactly did we say about X", small
# enough that a recall never becomes the next context problem.
RECALL_MAX_MATCHES = 5
RECALL_SNIPPET_CHARS = 700
RECALL_MAX_TURNS = 6
RECALL_MAX_CHARS = 60_000
RECALL_MAX_QUERY_CHARS = 500
# A page of a long turn ends at the last whitespace within this many
# characters of its limit, so a number or a quoted wording is never split
# across two pages (a hard cut only when there is no whitespace that close).
RECALL_PAGE_BOUNDARY_LOOKBACK = 200

RECALL_ELIDED_NOTE = (
    "[Recalled conversation text omitted from saved history: it is a copy of "
    "earlier turns, which are kept in full. Call recall_conversation again "
    "if the exact wording is needed.]"
)

RECALL_CONVERSATION_TOOL: dict[str, Any] = {
    "name": "recall_conversation",
    "description": (
        "Read earlier turns of THIS conversation that are no longer in your "
        "context word for word. Once a conversation grows very long, its "
        "oldest turns are condensed into the summary at the start of the "
        "conversation (the earlier_conversation_summary block); nothing is "
        "deleted, and this tool reads those turns back.\n"
        "\n"
        "Call it before relying on an exact detail from before the summary "
        "— a number, a quoted wording, the reason for a decision, an option "
        "the user ruled out — rather than trusting the summary's "
        "paraphrase. Pass `query` (a few keywords) to find the turns that "
        "mention something, or `turns` ([first, last], at most 6 turns) to "
        "read those turns word for word. A turn too long to show in one call "
        "ends with a note naming the `offset` that reads on: pass it with "
        "that one turn. Results show what the user and you said; tool calls "
        "are named, not reproduced. Early in a session nothing has been "
        "condensed and this has nothing to return."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Keywords to search the condensed turns for.",
            },
            "turns": {
                "type": "array",
                "items": {"type": "integer"},
                "description": (
                    "The first and last turn number to read word for word, "
                    "e.g. [12, 14]; [12] reads one turn."
                ),
            },
            "offset": {
                "type": "integer",
                "description": (
                    "With a single turn in `turns`: start reading that turn "
                    "at this character. The note ending a partly shown turn "
                    "(or a search match in a long turn) gives the number."
                ),
            },
        },
    },
}


class CompactionError(RuntimeError):
    """A summary call that produced nothing usable.

    ``code`` is a closed, telemetry-safe token (never provider text) for the
    trace and Developer tools; the message is for the log.
    """

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# Turns
# ---------------------------------------------------------------------------


def _content_list(message: Any) -> list[Any]:
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return list(content) if isinstance(content, list) else []


def message_text(message: Any) -> str:
    """A message's text blocks, joined — what the user or the model said."""
    return "\n\n".join(
        block.get("text", "")
        for block in _content_list(message)
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
        and block.get("text")
    ).strip()


def is_typed_user_message(message: Any) -> bool:
    """A message the user typed, as opposed to a tool-result message.

    Tool results also travel as user-role messages; a typed turn is the one
    carrying a text block (the rule ``delete_reference_if_idle`` uses too).
    """
    return (
        isinstance(message, dict)
        and message.get("role") == "user"
        and any(
            isinstance(block, dict) and block.get("type") == "text"
            for block in _content_list(message)
        )
    )


def turn_starts(history: list[Any]) -> list[int]:
    """History indexes of every typed user message: turn N starts at [N-1]."""
    return [i for i, message in enumerate(history) if is_typed_user_message(message)]


def transcript_digest(history: list[Any], keep_from: int) -> str:
    """Identity of the condensed part of a history.

    Over each message's role and TEXT only, so the deterministic elisions of
    machine payloads (outlines, fetched pages, PDFs, reference bodies) —
    which never touch text blocks — cannot make a valid record look stale,
    while anything that changes the conversation itself does: a reference
    delete truncates history without a generation bump, and a summary of
    turns that are no longer there must never be adopted.
    """
    projection = [
        [str(message.get("role") or ""), message_text(message)]
        for message in history[:keep_from]
        if isinstance(message, dict)
    ]
    payload = json.dumps(
        {"keep_from": keep_from, "messages": projection},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def cut_for(history: list[Any], keep_turns: int) -> tuple[int, int] | None:
    """``(keep_from, covers_turns)`` keeping the last ``keep_turns`` turns.

    ``None`` when the history has no turn to condense before them.
    """
    starts = turn_starts(history)
    if keep_turns < 1 or len(starts) <= keep_turns:
        return None
    return starts[-keep_turns], len(starts) - keep_turns


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class CompactionRecord:
    """A summary standing in for turns ``1..covers_turns``.

    ``keep_from`` indexes ``session.history``: the message there (a typed
    user message) and everything after it are sent word for word. The
    record is only ever REPLACED by a later one covering more turns, and it
    is dropped whenever the history it describes stops being a prefix of
    the live one (``digest``).
    """

    summary: str
    keep_from: int
    covers_turns: int
    digest: str
    created_at: str
    model: str
    tokens_before: int
    tokens_after: int
    trigger: str = "background"
    version: int = COMPACTION_RECORD_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "summary": self.summary,
            "keep_from": self.keep_from,
            "covers_turns": self.covers_turns,
            "digest": self.digest,
            "created_at": self.created_at,
            "model": self.model,
            "tokens_before": self.tokens_before,
            "tokens_after": self.tokens_after,
            "trigger": self.trigger,
        }

    @classmethod
    def from_dict(cls, data: Any, history: list[Any]) -> "CompactionRecord | None":
        """Rebuild a saved record, or ``None`` when it does not fit ``history``.

        Lenient like every optional project key: anything malformed, from a
        newer record version, or describing a history this one no longer
        starts with loads as "no compaction" — the conversation simply runs
        on its full view — never as an error.
        """
        if not isinstance(data, dict):
            return None
        version = data.get("version")
        if version != COMPACTION_RECORD_VERSION:
            return None
        summary = data.get("summary")
        keep_from = data.get("keep_from")
        covers = data.get("covers_turns")
        digest = data.get("digest")
        if (
            not isinstance(summary, str)
            or not summary.strip()
            or len(summary) > MAX_SUMMARY_CHARS
            or isinstance(keep_from, bool)
            or not isinstance(keep_from, int)
            or isinstance(covers, bool)
            or not isinstance(covers, int)
            or not isinstance(digest, str)
        ):
            return None
        record = cls(
            summary=summary,
            keep_from=keep_from,
            covers_turns=covers,
            digest=digest,
            created_at=str(data.get("created_at") or ""),
            model=str(data.get("model") or ""),
            tokens_before=_nonnegative_int(data.get("tokens_before")),
            tokens_after=_nonnegative_int(data.get("tokens_after")),
            trigger=str(data.get("trigger") or "background"),
        )
        return record if record.fits(history) else None

    def fits(self, history: list[Any]) -> bool:
        """Whether this record still describes the prefix of ``history``."""
        if not (0 < self.keep_from < len(history)):
            return False
        if not is_typed_user_message(history[self.keep_from]):
            return False
        starts = turn_starts(history)
        if self.covers_turns != sum(1 for start in starts if start < self.keep_from):
            return False
        return transcript_digest(history, self.keep_from) == self.digest


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def compaction_payload(record: CompactionRecord | None) -> dict[str, Any] | None:
    """What the UI needs to draw the divider — never the summary text.

    The summary can run to tens of thousands of characters, and this rides
    every document payload; the text is fetched only when the user asks to
    read it (``GET /api/chat/compaction``).
    """
    if record is None:
        return None
    return {
        "covers_turns": record.covers_turns,
        "created_at": record.created_at,
        "tokens_before": record.tokens_before,
        "tokens_after": record.tokens_after,
        "trigger": record.trigger,
        "summary_chars": len(record.summary),
    }


# ---------------------------------------------------------------------------
# The view
# ---------------------------------------------------------------------------

# The chat engine's own marker pattern, with a leading ``(?<!=)``. Without
# it, a long run of ``=`` that never completes a marker is re-scanned from
# every position inside the run (20,000 of them cost ~16 s — recorded as
# found-not-fixed for ``conversation._CONTEXT_BOUNDARY_PATTERN`` in Project
# workspace Phase 5A). The lookbehind changes no match: the leftmost match
# always begins at a run's first ``=``. It matters more here than there,
# because what this module frames is a whole summary or up to six recalled
# turns of the user's own text.
_CONTEXT_BOUNDARY_PATTERN = re.compile(
    r"(?<!=)={2,}\s*(?:END\s+)?PROJECT\s+CONTEXT\b[^\n=]*={2,}", re.IGNORECASE
)
_FRAME_TAG_PATTERN = re.compile(
    r"<\s*(/?)\s*("
    + "|".join((SUMMARY_FRAME_TAG, RECALL_FRAME_TAG, LEDGERS_FRAME_TAG))
    + r")\b[^>]*>",
    re.IGNORECASE,
)


def neutralize_compaction_frames(text: str) -> str:
    """Make every frame this module writes inert inside ``text``.

    The summary quotes the user and a recalled turn IS the user's text, so
    either can hold a string that would close its frame early, or forge the
    PROJECT CONTEXT markers every request relies on. Disclosed rather than
    deleted, the ``reference_docs`` posture.
    """
    text = _FRAME_TAG_PATTERN.sub(
        lambda m: f"[escaped tag: {m.group(1)}{m.group(2)}]", text
    )
    return _CONTEXT_BOUNDARY_PATTERN.sub(
        lambda m: f"[escaped marker: {' '.join(m.group(0).strip('= ').split())}]",
        text,
    )


def _turn_range(first: int, last: int) -> str:
    return str(first) if first == last else f"{first}–{last}"


@dataclass(frozen=True)
class ViewSpec:
    """How one request's view is cut: what is left out, and what stands in.

    ``keep_from`` indexes ``session.history``; ``preface`` is the text block
    placed ahead of the first message that IS sent. ``hidden_turns`` is how
    many turns the view leaves out — what ``recall_conversation`` may read.
    """

    keep_from: int
    hidden_turns: int
    preface: str
    kind: str  # "summary" | "truncated"


def summary_frame(record: CompactionRecord) -> str:
    covers = _turn_range(1, record.covers_turns)
    return (
        f'<{SUMMARY_FRAME_TAG} turns="{covers}">\n'
        f"{neutralize_compaction_frames(record.summary)}\n"
        f"</{SUMMARY_FRAME_TAG}>"
    )


def view_spec_for(record: CompactionRecord | None) -> ViewSpec | None:
    if record is None:
        return None
    covers = _turn_range(1, record.covers_turns)
    date = record.created_at[:10] or "an earlier date"
    preface = (
        summary_frame(record)
        + "\n\n"
        + f"The summary above stands in for turns {covers} of this "
        f"conversation, condensed on {date} so the conversation stays within "
        "the model's context window. It was written from the conversation as "
        "it stood then: wherever it and the PROJECT CONTEXT in the newest "
        "message differ, the PROJECT CONTEXT is current. The full text of "
        "those turns is kept — before relying on an exact detail from them "
        "(a number, a wording, the reason for a decision), call "
        "recall_conversation to read it word for word."
    )
    return ViewSpec(
        keep_from=record.keep_from,
        hidden_turns=record.covers_turns,
        preface=preface,
        kind="summary",
    )


def truncated_view_spec(
    history: list[Any], keep_from: int, record: CompactionRecord | None
) -> ViewSpec:
    """The last-resort view: the oldest turns left out, and said so.

    Used only when a request would not fit the context window and no
    summary could be made. The turns are not gone — the note points the
    model at ``recall_conversation`` — and nothing about the session
    changes: the next turn tries to condense again.
    """
    starts = turn_starts(history)
    hidden = sum(1 for start in starts if start < keep_from)
    parts: list[str] = []
    first_omitted = 1
    if record is not None and record.keep_from <= keep_from:
        parts.append(summary_frame(record))
        first_omitted = record.covers_turns + 1
    if hidden >= first_omitted:
        one = hidden == first_omitted
        parts.append(
            f"[{'Turn' if one else 'Turns'} {_turn_range(first_omitted, hidden)} "
            f"of this conversation {'is' if one else 'are'} left out of this "
            "request so it fits within the model's context window. Nothing was "
            "deleted: call recall_conversation to read "
            f"{'it' if one else 'any of them'} before relying on what "
            f"{'it' if one else 'they'} said.]"
        )
    return ViewSpec(
        keep_from=keep_from,
        hidden_turns=hidden,
        preface="\n\n".join(parts),
        kind="truncated",
    )


def _with_leading_text(message: dict[str, Any], text: str) -> dict[str, Any]:
    return {
        **message,
        "content": [{"type": "text", "text": text}, *_content_list(message)],
    }


def compacted_view(
    history: list[Any], spec: ViewSpec | None
) -> tuple[list[Any], str | None]:
    """The history as a request sends it, and any preface left to place.

    No spec returns ``history`` itself — the same list object, so a session
    that never condensed builds a byte-identical request. Otherwise the
    first kept message gets the preface as a separate leading text block
    (copy-on-write; roles still alternate and the request still opens with
    a user message). When the spec keeps nothing (the last-resort view of a
    conversation whose every turn had to be left out) the view is empty and
    the preface is returned for the caller to put ahead of the new turn.
    """
    if spec is None:
        return history, None
    tail = history[spec.keep_from:]
    if not spec.preface:
        return list(tail), None
    if not tail:
        return [], spec.preface
    return [_with_leading_text(tail[0], spec.preface), *tail[1:]], None


def with_preface(message: dict[str, Any], preface: str) -> dict[str, Any]:
    """``message`` with ``preface`` as its leading text block (a copy)."""
    return _with_leading_text(message, preface)


# ---------------------------------------------------------------------------
# Sizing
# ---------------------------------------------------------------------------


def message_chars(message: Any) -> int:
    """Serialized size of one message — the unit every estimate here uses."""
    try:
        return len(
            json.dumps(
                message, ensure_ascii=False, default=str, separators=(",", ":")
            )
        )
    except (TypeError, ValueError):
        return len(str(message))


def estimate_tokens(chars: int, tokens_per_char: float | None) -> int:
    ratio = tokens_per_char if tokens_per_char else DEFAULT_TOKENS_PER_CHAR
    return int(chars * ratio)


def calibrated_tokens_per_char(tokens: int | None, chars: int) -> float | None:
    """Tokens per serialized character, from one request the provider counted.

    Clamped to a plausible band, so one odd measurement (an empty reply, a
    usage-less test fake) cannot swing every later estimate.
    """
    if not tokens or tokens <= 0 or chars <= 0:
        return None
    return min(_MAX_TOKENS_PER_CHAR, max(_MIN_TOKENS_PER_CHAR, tokens / chars))


# ---------------------------------------------------------------------------
# The summary call's instruction, and the checks on its reply
# ---------------------------------------------------------------------------


def _excerpt(text: str) -> str:
    folded = " ".join(text.split())
    if len(folded) <= _EXCERPT_CHARS:
        return folded
    return folded[:_EXCERPT_CHARS].rstrip() + "…"


def summary_instruction(
    *,
    first_kept_text: str,
    kept_turns: int,
    re_compaction: bool,
    facts_block: str,
    followups_block: str,
) -> str:
    """The final user message of the summary call.

    Anthropic's recommended client-side compaction prompt (the six retention
    items) adapted to spec work: keep decisions and why, what was ruled out
    and why, exact values, how the user works and the corrections they made,
    where things stand and what was promised — and never restate what
    arrives fresh with every message (the document, facts, follow-ups,
    research, QC), which would only go stale. The ledgers ride along so the
    summary can point at them by id and name what they are missing (D4).
    The closing sentence is load-bearing: the call keeps the chat's tools so
    it reads the chat's cache, and without it the model occasionally calls
    one instead of writing.
    """
    headings = "\n".join(f"## {heading}" for heading in SUMMARY_HEADINGS)
    carry = (
        " The conversation opens with a summary of even earlier turns "
        f"(inside <{SUMMARY_FRAME_TAG}>); carry everything it records into "
        "yours, because yours replaces it."
        if re_compaction
        else ""
    )
    ledgers = "\n\n".join(
        block
        for block in (
            facts_block.strip() or "No project facts are recorded yet.",
            followups_block.strip() or "Nothing is waiting on the user.",
        )
    )
    return (
        "[Condense request from the app — not a message from the user.]\n\n"
        "This conversation is about to be condensed so it stays within the "
        "model's context window. Write the summary that will stand in for "
        "its earlier part.\n\n"
        "What to summarize: everything BEFORE the user message that begins "
        f"\"{neutralize_compaction_frames(_excerpt(first_kept_text))}\". That "
        f"message and the rest of the conversation after it — the last "
        f"{kept_turns} exchange{'s' if kept_turns != 1 else ''} — stay in "
        "the context word for word after your summary, so do not restate "
        f"anything from that point on.{carry}\n\n"
        "Put the summary inside <summary></summary> tags, under exactly "
        "these headings, in this order, each on its own line (write \"None.\" "
        "under a heading with nothing to record):\n"
        f"{headings}\n\n"
        "Be sure to preserve: (1) any difficulties or problems that came up, "
        "and how they were handled or resolved; (2) any possibilities, "
        "options, or approaches that were raised, tried, or set aside, and "
        "why; (3) anything that was asked for, decided, agreed, ruled out, or "
        "established as a preference, constraint, or boundary — stated "
        "exactly; (4) exactly where things stand now — what has been "
        "covered, settled, or completed so far; (5) anything still open, "
        "unresolved, promised, or expected to happen next; (6) specific "
        "details that would be hard to reconstruct — names, numbers, dates, "
        "section numbers, standards and their editions, exact wording, links "
        "or references — kept exactly. Be complete on these even at the cost "
        "of length; keep everything else concise. Weight the two voices "
        "differently: keep what the user said, asked for, shared, or "
        "established carefully and close to their own words; your own "
        "explanations and reasoning can be condensed much further, to what "
        "they concluded or produced — as long as nothing in the six items "
        "above is dropped.\n\n"
        "Do not restate the specification document, the project facts, the "
        "items waiting on the user, the research findings or the Final QC "
        "review: each arrives fresh with every message, and a copy here "
        "would go stale. Point at a project fact or a waiting item by its id "
        "(for example pf-3 or fu-2) instead. Under \"Decisions the ledgers "
        "are missing\", list each decision or established fact from the part "
        "you are summarizing that the project facts below do not already "
        "record, one per line, each starting with the turn it was settled in "
        "(for example \"- turn 12: the AHJ adopted NFPA 13-2022\"; turns are "
        "numbered from 1, one per message the user sent); write \"None.\" if "
        "there are none.\n\n"
        f"<{LEDGERS_FRAME_TAG}>\n{neutralize_compaction_frames(ledgers)}\n"
        f"</{LEDGERS_FRAME_TAG}>\n\n"
        "Do not call any tools while writing this summary; respond with text "
        "only."
    )


_SUMMARY_TAG_PATTERN = re.compile(
    r"<summary>(.*?)(?:</summary>|\Z)", re.IGNORECASE | re.DOTALL
)


def _heading_pattern(heading: str) -> re.Pattern[str]:
    words = r"\s+".join(re.escape(word) for word in heading.split())
    return re.compile(
        rf"^[ \t]*#{{1,6}}[ \t]*{words}[ \t]*:?[ \t]*$",
        re.IGNORECASE | re.MULTILINE,
    )


_HEADING_PATTERNS = tuple(_heading_pattern(h) for h in SUMMARY_HEADINGS)


def extract_summary(content: list[dict[str, Any]], stop_reason: str) -> str:
    """The summary text from a reply, or a :class:`CompactionError`.

    Checked before anything is adopted: a declined, cut-off or tool-calling
    reply, a missing ``<summary>``, an empty or runaway one, or one missing
    a required heading is refused, and the conversation keeps its full view
    until the next attempt.
    """
    if stop_reason == "refusal":
        raise CompactionError(
            "The model declined to summarize the conversation.", code="refused"
        )
    if any(
        isinstance(block, dict)
        and block.get("type") in ("tool_use", "server_tool_use")
        for block in content
    ):
        raise CompactionError(
            "The summary call used a tool instead of writing.", code="tool_call"
        )
    if stop_reason not in ("end_turn", "stop_sequence"):
        raise CompactionError(
            f"The summary call ended early ({stop_reason or 'no stop reason'}).",
            code="incomplete",
        )
    text = "\n".join(
        str(block.get("text") or "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )
    match = _SUMMARY_TAG_PATTERN.search(text)
    if match is None:
        raise CompactionError(
            "The reply carried no <summary> block.", code="no_summary"
        )
    summary = match.group(1).strip()
    if not summary:
        raise CompactionError("The summary was empty.", code="empty")
    if len(summary) > MAX_SUMMARY_CHARS:
        raise CompactionError(
            f"The summary ran to {len(summary):,} characters.", code="too_long"
        )
    missing = [
        heading
        for heading, pattern in zip(SUMMARY_HEADINGS, _HEADING_PATTERNS)
        if pattern.search(summary) is None
    ]
    if missing:
        raise CompactionError(
            "The summary is missing required sections: " + ", ".join(missing),
            code="missing_headings",
        )
    return summary


# ---------------------------------------------------------------------------
# Recall
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecallTurn:
    number: int
    user: str
    assistant: str
    tools: tuple[tuple[str, int], ...]

    def searchable(self) -> str:
        return f"{self.user}\n{self.assistant}\n" + " ".join(
            name for name, _ in self.tools
        )


_USER_LABEL = "User: "
_ASSISTANT_LABEL = "\n\nAssistant: "


def _turn_body(turn: RecallTurn) -> str:
    """The text a read shows for one turn; ``offset`` counts characters of
    this (a search's offsets are computed against the same labels)."""
    return f"{_USER_LABEL}{turn.user}{_ASSISTANT_LABEL}{turn.assistant or '(no text)'}"


def _page_end(body: str, start: int, limit: int) -> int:
    """Where a page of ``body`` that starts at ``start`` stops.

    The whole rest when it fits in ``limit``; otherwise just after the last
    whitespace within ``RECALL_PAGE_BOUNDARY_LOOKBACK`` characters of the
    limit, so the next page starts on a whole word — a hard cut only when no
    whitespace is that close. Always past ``start``, so reading on from the
    returned offset always makes progress.
    """
    end = start + limit
    if end >= len(body):
        return len(body)
    lowest = max(start + 1, end - RECALL_PAGE_BOUNDARY_LOOKBACK)
    for index in range(end - 1, lowest - 1, -1):
        if body[index].isspace():
            return index + 1
    return end


def recall_turns(history: list[Any], hidden_turns: int) -> list[RecallTurn]:
    """Turns ``1..hidden_turns`` of ``history``, reduced to what was said."""
    starts = turn_starts(history)
    turns: list[RecallTurn] = []
    for number, start in enumerate(starts[:hidden_turns], start=1):
        end = starts[number] if number < len(starts) else len(history)
        replies: list[str] = []
        tools: Counter[str] = Counter()
        for message in history[start + 1:end]:
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            text = message_text(message)
            if text:
                replies.append(text)
            for block in _content_list(message):
                if isinstance(block, dict) and block.get("type") in (
                    "tool_use",
                    "server_tool_use",
                ):
                    tools[str(block.get("name") or "unknown")] += 1
        turns.append(
            RecallTurn(
                number=number,
                user=message_text(history[start]),
                assistant="\n\n".join(replies),
                tools=tuple(sorted(tools.items())),
            )
        )
    return turns


_STOPWORDS = frozenset(
    "a an and are as at be but by did do for from had has have i in is it its "
    "of on or our so that the their them then there these they this to was we "
    "were what when which who why will with you your".split()
)
_TERM_PATTERN = re.compile(r"[\w][\w.\-/]*")


def _query_terms(query: str) -> list[str]:
    seen: list[str] = []
    for term in _TERM_PATTERN.findall(query.casefold()):
        term = term.strip(".-/")
        if not term or term in _STOPWORDS or term in seen:
            continue
        if len(term) < 2 and not term.isdigit():
            continue
        seen.append(term)
    return seen


def _term_pattern(term: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w]){re.escape(term)}(?![\w])")


def _snippet(text: str, patterns: list[re.Pattern[str]]) -> tuple[int, str]:
    """``(start, snippet)``: the passage of ``text`` around its first match,
    and where in ``text`` it begins. Positions come from the case-folded
    text, so they are exact unless folding changes the text's length (a
    few non-ASCII letters) — near, then, and a page read from there is
    ``RECALL_MAX_CHARS`` long."""
    folded = text.casefold()
    position = min(
        (match.start() for p in patterns for match in [p.search(folded)] if match),
        default=0,
    )
    half = RECALL_SNIPPET_CHARS // 2
    start = max(0, position - half)
    end = min(len(text), start + RECALL_SNIPPET_CHARS)
    start = max(0, end - RECALL_SNIPPET_CHARS)
    snippet = " ".join(text[start:end].split())
    return start, (
        ("…" if start > 0 else "") + snippet + ("…" if end < len(text) else "")
    )


def _tools_line(turn: RecallTurn) -> str:
    if not turn.tools:
        return ""
    named = ", ".join(
        f"{name}" + (f" ×{count}" if count > 1 else "") for name, count in turn.tools
    )
    return f"[Tools used: {named}]"


def _search(turns: list[RecallTurn], query: str) -> str:
    terms = _query_terms(query)
    if not terms:
        return "The query has no searchable words; pass a few keywords."
    patterns = [_term_pattern(term) for term in terms]
    phrase = " ".join(query.casefold().split())
    scored: list[tuple[int, int, RecallTurn]] = []
    for turn in turns:
        haystack = turn.searchable().casefold()
        hits = [min(5, len(p.findall(haystack))) for p in patterns]
        matched = sum(1 for count in hits if count)
        if not matched:
            continue
        score = sum(hits) + 3 * matched + (5 if phrase and phrase in haystack else 0)
        scored.append((score, turn.number, turn))
    if not scored:
        return (
            f"No condensed turn mentions {query!r}. Try other keywords, or read "
            "turns by number."
        )
    scored.sort(key=lambda entry: (-entry[0], entry[1]))
    lines = [
        f"Search for {query!r} in the condensed turns "
        f"(1–{turns[-1].number}): {len(scored)} matching turn"
        f"{'s' if len(scored) != 1 else ''}"
        + (f", best {RECALL_MAX_MATCHES} shown" if len(scored) > RECALL_MAX_MATCHES else "")
        + "."
    ]
    paged = False
    for _score, number, turn in scored[:RECALL_MAX_MATCHES]:
        lines.append("")
        lines.append(f"--- Turn {number} ---")
        # A turn longer than one read: name where each passage starts, so
        # the model reads that page instead of paging from the beginning.
        long_turn = len(_turn_body(turn)) > RECALL_MAX_CHARS
        for label, text, at in (
            ("User", turn.user, len(_USER_LABEL)),
            (
                "Assistant",
                turn.assistant,
                len(_USER_LABEL) + len(turn.user) + len(_ASSISTANT_LABEL),
            ),
        ):
            if text and any(p.search(text.casefold()) for p in patterns):
                start, snippet = _snippet(text, patterns)
                where = f" (offset {at + start})" if long_turn else ""
                paged = paged or long_turn
                lines.append(f"{label}{where}: {snippet}")
        if not any(
            text and any(p.search(text.casefold()) for p in patterns)
            for text in (turn.user, turn.assistant)
        ):
            tools = _tools_line(turn)
            if tools:
                lines.append(tools)
    lines.append("")
    lines.append(
        "Call recall_conversation with turns [N, N] to read a whole turn word "
        "for word."
        + (
            " A long turn shows an offset beside its passage: pass it with "
            "turns [N] to start reading there."
            if paged
            else ""
        )
    )
    return "\n".join(lines)


def _read(
    turns: list[RecallTurn], first: int, last: int, offset: int = 0
) -> str:
    """Turns ``first..last`` word for word, within ``RECALL_MAX_CHARS``.

    The budget is shared evenly, so a long turn in a range is shown in
    part; one turn read alone gets all of it, from ``offset`` on. A turn
    shown in part says which characters it shows and ends with the exact
    call that reads on — a turn of any length can be read to its end.
    """
    chosen = [turn for turn in turns if first <= turn.number <= last]
    budget = RECALL_MAX_CHARS // max(1, len(chosen))
    lines = [
        f"Turns {_turn_range(first, last)} of this conversation, word for word "
        "(tool calls are named, not reproduced):"
    ]
    for turn in chosen:
        body = _turn_body(turn)
        start = offset if len(chosen) == 1 else 0
        end = _page_end(body, start, budget)
        lines.append("")
        if start == 0 and end == len(body):
            lines.append(f"--- Turn {turn.number} ---")
            lines.append(body)
        else:
            lines.append(
                f"--- Turn {turn.number} (characters {start + 1:,}–{end:,} of "
                f"{len(body):,}) ---"
            )
            lines.append(body[start:end].rstrip())
            if end < len(body):
                lines.append(
                    f"[… {len(body) - end:,} more characters of turn "
                    f"{turn.number} not shown — call recall_conversation with "
                    f"turns [{turn.number}] and offset {end} to read on.]"
                )
        tools = _tools_line(turn)
        if tools:
            lines.append(tools)
    return "\n".join(lines)


def recall_result(
    history: list[Any], hidden_turns: int, tool_input: Any
) -> tuple[str, bool]:
    """``(text, is_error)`` for one ``recall_conversation`` call.

    A mistake the model can correct (nothing condensed yet, a bad range,
    neither argument) is an ``is_error`` result naming the fix — never a
    turn failure. The text is framed and made inert, like every other piece
    of the user's own words the app hands back to the model.
    """
    arguments = tool_input if isinstance(tool_input, dict) else {}
    if hidden_turns <= 0:
        return (
            "recall_conversation: nothing in this conversation has been "
            "condensed — every earlier turn is already in your context.",
            True,
        )
    turns = recall_turns(history, hidden_turns)
    if not turns:
        return ("recall_conversation: there are no condensed turns to read.", True)
    query = arguments.get("query")
    requested = arguments.get("turns")
    offset = arguments.get("offset")
    if requested is not None and query:
        return (
            "recall_conversation: pass either `query` or `turns`, not both.",
            True,
        )
    if offset is not None and (
        isinstance(offset, bool) or not isinstance(offset, int) or offset < 0
    ):
        return (
            "recall_conversation: `offset` must be a whole number of "
            "characters, 0 or more.",
            True,
        )
    offset = offset or 0
    last_available = turns[-1].number
    if requested is not None:
        if (
            not isinstance(requested, list)
            or not 1 <= len(requested) <= 2
            or any(isinstance(n, bool) or not isinstance(n, int) for n in requested)
        ):
            return (
                "recall_conversation: `turns` must be [first, last] (or [n] for "
                "one turn) with whole turn numbers.",
                True,
            )
        first, last = requested[0], requested[-1]
        if first > last:
            first, last = last, first
        if first < 1 or last > last_available:
            return (
                f"recall_conversation: the condensed turns are 1–{last_available}; "
                f"turns {first}–{last} are not all among them (later turns are "
                "already in your context word for word).",
                True,
            )
        if last - first + 1 > RECALL_MAX_TURNS:
            return (
                f"recall_conversation: read at most {RECALL_MAX_TURNS} turns per "
                "call; split the range.",
                True,
            )
        if offset and first != last:
            return (
                "recall_conversation: `offset` reads on within one turn; pass "
                "it with a single turn, e.g. turns [12].",
                True,
            )
        if offset:
            length = len(_turn_body(turns[first - 1]))
            if offset >= length:
                return (
                    f"recall_conversation: turn {first} is {length:,} "
                    f"characters long; `offset` must be below {length}.",
                    True,
                )
        body = _read(turns, first, last, offset)
    elif isinstance(query, str) and query.strip():
        if offset:
            return (
                "recall_conversation: `offset` reads on within one turn; pass "
                "it with `turns`, not with `query`.",
                True,
            )
        if len(query) > RECALL_MAX_QUERY_CHARS:
            return (
                f"recall_conversation: keep the query under "
                f"{RECALL_MAX_QUERY_CHARS} characters.",
                True,
            )
        body = _search(turns, query.strip())
    else:
        return (
            "recall_conversation: pass `query` (keywords to search for) or "
            "`turns` ([first, last]).",
            True,
        )
    return (
        f"<{RECALL_FRAME_TAG}>\n{neutralize_compaction_frames(body)}\n"
        f"</{RECALL_FRAME_TAG}>",
        False,
    )


def elide_recall_results(messages: list[Any]) -> list[Any]:
    """Drop recalled text from COMMITTED history (copy-on-write).

    A recall result is a copy of turns the history already keeps in full, so
    committing it would store the same words twice and re-send them on every
    later turn. The ``tool_use`` stays (it is just the query), and an
    ``is_error`` result stays too — a correction is small and worth keeping.
    Returns the same list object when nothing changed.
    """
    recall_ids = {
        block.get("id")
        for message in messages
        if isinstance(message, dict) and message.get("role") == "assistant"
        for block in _content_list(message)
        if isinstance(block, dict)
        and block.get("type") == "tool_use"
        and block.get("name") == RECALL_CONVERSATION_TOOL["name"]
    }
    recall_ids.discard(None)
    if not recall_ids:
        return messages
    result: list[Any] = []
    changed_any = False
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "user":
            result.append(message)
            continue
        content = message.get("content")
        if not isinstance(content, list):
            result.append(message)
            continue
        changed = False
        new_content: list[Any] = []
        for block in content:
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_result"
                and block.get("tool_use_id") in recall_ids
                and not block.get("is_error")
                and block.get("content") != RECALL_ELIDED_NOTE
            ):
                new_content.append({**block, "content": RECALL_ELIDED_NOTE})
                changed = True
            else:
                new_content.append(block)
        if changed:
            changed_any = True
            result.append({**message, "content": new_content})
        else:
            result.append(message)
    return result if changed_any else messages


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------


class CompactionRunner:
    """At most one summary per session object, and its outcome.

    ``running`` while a call is in flight, ``ready`` while a finished summary
    waits to be adopted, ``failed`` after a refusal or error (with a backoff
    measured in committed turns before the next try). A reset or project load
    replaces the session's runner, so a call still in flight settles into
    the abandoned object — the zombie-runner pattern — and the generation it
    was started for keeps its result from ever reaching the new session.
    """

    _BACKOFF_CAP_TURNS = 16

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._done.set()
        self.status = "idle"
        self.generation: int | None = None
        self.trigger = ""
        self.result: CompactionRecord | None = None
        self.error = ""
        self.error_kind = ""
        self.attempts = 0
        self.failures = 0
        self.retry_after_turns = 0
        self.started_at = ""
        self.finished_at = ""

    def busy(self) -> bool:
        return self.status in ("running", "ready")

    def eligible(self, typed_turns: int) -> bool:
        """Free to start, and past any backoff from an earlier failure."""
        return not self.busy() and typed_turns >= self.retry_after_turns

    def claim(self, *, generation: int, trigger: str) -> bool:
        """Mark a summary as running; False when one already is (or waits)."""
        with self._lock:
            if self.busy():
                return False
            self.status = "running"
            self.generation = generation
            self.trigger = trigger
            self.result = None
            self.error = ""
            self.error_kind = ""
            self.attempts += 1
            self.started_at = now_iso()
            self.finished_at = ""
            self._done.clear()
            return True

    def settle(
        self,
        record: CompactionRecord | None,
        *,
        error: str = "",
        error_kind: str = "",
        typed_turns: int = 0,
        notify: bool = True,
    ) -> None:
        """Record the outcome. ``notify=False`` leaves waiters blocked so
        the background worker can run its adoption callback first — a
        waiter that woke before adoption would see a summary that is ready
        but not yet in the session."""
        with self._lock:
            self.finished_at = now_iso()
            if record is not None:
                self.status = "ready"
                self.result = record
                self.failures = 0
                self.retry_after_turns = 0
            else:
                self.status = "failed"
                self.result = None
                self.error = error
                self.error_kind = error_kind or "error"
                self.failures += 1
                self.retry_after_turns = typed_turns + min(
                    2 ** (self.failures - 1), self._BACKOFF_CAP_TURNS
                )
        if notify:
            self._done.set()

    def start(
        self,
        *,
        generation: int,
        trigger: str,
        typed_turns: int,
        job: Callable[[], CompactionRecord],
        on_done: Callable[["CompactionRunner"], None] | None = None,
    ) -> bool:
        """Run ``job`` on a daemon thread; False when a summary is already
        running or waiting to be adopted."""
        if not self.claim(generation=generation, trigger=trigger):
            return False

        def work() -> None:
            record: CompactionRecord | None = None
            error = ""
            kind = ""
            try:
                record = job()
            except CompactionError as exc:
                error, kind = str(exc), exc.code
            except Exception as exc:  # noqa: BLE001 - a background call never raises
                error, kind = f"{type(exc).__name__}: {exc}", "error"
            self.settle(
                record,
                error=error,
                error_kind=kind,
                typed_turns=typed_turns,
                notify=False,
            )
            try:
                if on_done is not None:
                    on_done(self)
            except Exception:  # noqa: BLE001 - adoption is best-effort
                pass
            finally:
                self._done.set()

        threading.Thread(
            target=work, name="chat-compaction", daemon=True
        ).start()
        return True

    def take_ready(self, generation: int) -> CompactionRecord | None:
        """Hand over a finished summary, once, if it belongs to ``generation``."""
        with self._lock:
            if self.status != "ready":
                return None
            record = self.result
            self.result = None
            self.status = "idle"
            return record if self.generation == generation else None

    def wait(self, timeout: float | None = None) -> bool:
        return self._done.wait(timeout)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "status": self.status,
                "trigger": self.trigger,
                "attempts": self.attempts,
                "failures": self.failures,
                "error_kind": self.error_kind,
                "retry_after_turns": self.retry_after_turns,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
            }
