"""Suggested reply chips: the reply's ``<suggested_replies>`` block (Batch 9).

Each turn the model may stage up to five short, complete replies IN THE
USER'S VOICE — rendered as one-tap chips just above the chat composer.
Clicking a chip sends its text as the user's next message, so a chip is
always a sendable reply ("Use your recommended default", "Draft PART 2
now"), never a question or a fill-in-the-blank template.

Where the chips travel (since the chips-in-the-reply change, 2026-10-06)
-----------------------------------------------------------------------
They used to be a ``suggest_prompts`` tool call. On Claude Sonnet 5.5 the
reply has to come AFTER the last tool call (P55-2), so every turn paid one
extra request whose only new content was ``{"suggested": N}`` — a full
re-read of the request for nothing. Now the model ends its closing message
with ONE fixed, tagged block::

    <suggested_replies>["Use your recommended default", "Draft PART 2 now"]</suggested_replies>

— a JSON array of strings — and the server reads it off the reply:

- :class:`ReplyChipFilter` holds back a text block's markup while it
  streams (from the opening tag onward, plus any trailing fragment that
  could still become one), so the raw block never flashes in the chat; the
  chat relay feeds it every text delta and emits ``suggested_prompts`` when
  a block closes and validates.
- :func:`parse_reply_chips` validates with :func:`validate_prompts` — the
  same rules the tool had. A malformed block cannot be corrected without
  the round this change removes, so it means "no chips this turn" (logged),
  never a turn failure.
- A complete block stays in the committed reply text, verbatim: the model
  sees exactly what it offered last turn (the tool's input did the same
  job), and every past reply ending in the block keeps the format in front
  of it. Everything that SHOWS or MINES committed text strips it with
  :func:`strip_reply_chips` — the transcript the chat rebuilds on load (and
  therefore the fact harvest and the reply digests built on it) and the
  condensed conversation's recall.
- A block that never closes (a user stop, a ``max_tokens`` cut) is dropped:
  nothing is staged, and :func:`strip_unclosed_reply_chips` removes the
  fragment before commit so history never carries half a block.

The grammar is exact and the same in all three places — complete blocks
anywhere are removed, an unclosed one removes everything after its opening
tag — so the streamed text, the committed text and the rebuilt transcript
cannot disagree. :func:`neutralize_reply_chip_tags` makes the tag inert in
the data channels the app already frames (the PROJECT CONTEXT and project
background, a read reference document, the condensed-conversation frames),
so retrieved or imported text cannot plant a block for the model to echo.

Latest-only, turn-atomic semantics
----------------------------------
A committed turn REPLACES ``SessionState.suggested_prompts`` with whatever
the turn staged — including the empty set when the reply carried no block.
That "no block = clear" rule is how the bar winds down to nothing as the
section nears issue-ready; an explicit empty array is equally valid (the
deliberate "nothing useful left to suggest" signal). A failed turn never
touches the committed list (staging is a turn-local in the conversation
loop, discarded with the turn). Within one turn the latest valid block
wins; a rejected one stages nothing and leaves an earlier one standing.

The retired tool
----------------
``SUGGEST_PROMPTS_TOOL`` stays declared, with a description that says not
to call it, because saved projects carry historical ``suggest_prompts``
``tool_use`` blocks and the API reference does not establish that a history
naming an undeclared tool validates. A call stages nothing and answers with
an ``is_error`` result naming the block. Removal is scheduled for a later
release (``docs/as-built.md``, "Suggested replies ride the reply").
"""
from __future__ import annotations

import json
import re
from typing import Any

MAX_PROMPTS = 5
# Hard cap per chip; the policy steers toward ~60 chars. A chip must read at
# a glance — anything longer belongs in the reply itself.
MAX_PROMPT_CHARS = 120

# The block the model ends its closing message with. One fixed spelling,
# matched exactly (case-sensitive, no attributes): the stable prompt teaches
# exactly this, and an exact match is what lets the relay, the commit and
# the transcript agree byte for byte. The name is specific enough that no
# specification, user message or attached document plausibly carries it,
# and the data channels neutralize it anyway (see
# :func:`neutralize_reply_chip_tags`).
REPLY_CHIPS_TAG = "suggested_replies"
REPLY_CHIPS_OPEN = f"<{REPLY_CHIPS_TAG}>"
REPLY_CHIPS_CLOSE = f"</{REPLY_CHIPS_TAG}>"


class SuggestError(ValueError):
    """A malformed set of suggested replies. Logged; nothing is staged."""


def validate_prompts(payload: Any) -> list[str]:
    """Validate ``{"prompts": [...]}``; return the cleaned list.

    Strict: raises :class:`SuggestError` on anything off-shape. A reply's
    block arrives here as ``{"prompts": <its parsed JSON>}`` (see
    :func:`parse_reply_chips`), so the rules are the ones the retired tool
    enforced. An EMPTY list is valid (clears the bar). Internal whitespace
    folds to single spaces (chips are one-line UI); duplicates dedupe
    preserving order; the over-``MAX_PROMPTS`` check runs AFTER cleanup so a
    list that dedupes down to the cap passes.
    """
    if not isinstance(payload, dict):
        raise SuggestError("suggested replies: input must be an object.")
    raw = payload.get("prompts")
    if not isinstance(raw, list):
        raise SuggestError(
            "suggested replies: 'prompts' must be a list of strings "
            "(an empty list clears the bar)."
        )
    cleaned: list[str] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, str):
            raise SuggestError("suggested replies: every reply must be a string.")
        text = " ".join(entry.split())
        if not text:
            raise SuggestError(
                "suggested replies: replies must be non-empty — drop blank "
                "entries rather than sending them."
            )
        if len(text) > MAX_PROMPT_CHARS:
            raise SuggestError(
                f"suggested replies: a reply is too long ({len(text)} > "
                f"{MAX_PROMPT_CHARS} chars) — chips must read at a glance; "
                "shorten it to one clause."
            )
        if text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    if len(cleaned) > MAX_PROMPTS:
        raise SuggestError(
            f"suggested replies: too many ({len(cleaned)} > {MAX_PROMPTS}). "
            f"Send only the {MAX_PROMPTS} most useful."
        )
    return cleaned


def parse_reply_chips(inner: str) -> list[str]:
    """The cleaned chips a reply's block carries, or :class:`SuggestError`.

    ``inner`` is the text between the opening and closing tags: a JSON array
    of strings, surrounded by any whitespace. Never lenient beyond
    :func:`validate_prompts` — a block the model cannot be told about is
    better dropped whole than half-guessed.
    """
    try:
        value = json.loads(inner)
    except ValueError as exc:
        raise SuggestError(
            f"suggested replies: the block is not a JSON array ({exc})."
        ) from None
    if not isinstance(value, list):
        raise SuggestError("suggested replies: the block must hold a JSON array.")
    return validate_prompts({"prompts": value})


def _unclosed_block_start(text: str) -> int | None:
    """Where an unclosed block starts in ``text``, scanning blocks in order.

    The relay's own walk (:class:`ReplyChipFilter`): from each opening tag,
    the FIRST closing tag after it ends the block; an opening tag with no
    closing tag after it opens a block that never ends.
    """
    position = 0
    while True:
        start = text.find(REPLY_CHIPS_OPEN, position)
        if start < 0:
            return None
        end = text.find(REPLY_CHIPS_CLOSE, start + len(REPLY_CHIPS_OPEN))
        if end < 0:
            return start
        position = end + len(REPLY_CHIPS_CLOSE)


def strip_reply_chips(text: str) -> str:
    """``text`` as the chat shows it: every block removed.

    Complete blocks anywhere go, and an unclosed one takes everything after
    its opening tag with it — exactly what :class:`ReplyChipFilter` released
    while the text streamed, so a reloaded transcript matches the live one.
    The whitespace a block leaves behind is left for the caller (the
    transcript trims each reply).
    """
    if REPLY_CHIPS_OPEN not in text:
        return text
    kept: list[str] = []
    position = 0
    while True:
        start = text.find(REPLY_CHIPS_OPEN, position)
        if start < 0:
            kept.append(text[position:])
            break
        kept.append(text[position:start])
        end = text.find(REPLY_CHIPS_CLOSE, start + len(REPLY_CHIPS_OPEN))
        if end < 0:
            break
        position = end + len(REPLY_CHIPS_CLOSE)
    return "".join(kept)


def strip_unclosed_reply_chips(text: str) -> str:
    """``text`` without a block that never closed (and the space before it).

    Commit's cleanup: a complete block is kept verbatim — it is how the
    model sees what it offered — but a fragment cut off by a stop or a
    ``max_tokens`` limit is not something the model said, and the chat never
    showed it. May return an empty string; the caller drops an empty text
    block (the API refuses whitespace-only text).
    """
    start = _unclosed_block_start(text)
    return text if start is None else text[:start].rstrip()


def _held_tag_prefix(text: str) -> int:
    """How many trailing characters of ``text`` could still begin the
    opening tag — held until the next delta decides."""
    longest = min(len(text), len(REPLY_CHIPS_OPEN) - 1)
    for size in range(longest, 0, -1):
        if text.endswith(REPLY_CHIPS_OPEN[:size]):
            return size
    return 0


class ReplyChipFilter:
    """One streaming text block's chip markup, held back as it arrives.

    :meth:`feed` takes each text delta and returns the text that is safe to
    show now plus the inner text of every block the delta completed;
    :meth:`finish` (at the block's ``content_block_stop``) releases a held
    tail that never became a tag, or reports a block that never closed.
    Concatenating everything shown equals :func:`strip_reply_chips` of the
    whole text, however the text was split into deltas.
    """

    __slots__ = ("_pending", "_inside", "_scan_from")

    def __init__(self) -> None:
        # Outside a block: at most a tag-sized tail that may still become the
        # opening tag. Inside: the block so far, opening tag included.
        self._pending = ""
        self._inside = False
        # Inside a block, where the next search for the closing tag starts —
        # so a long unclosed block is scanned once, not once per delta.
        self._scan_from = 0

    @property
    def holding(self) -> bool:
        """Whether a block is open (text is being held, not shown)."""
        return self._inside

    def feed(self, text: str) -> tuple[str, list[str]]:
        self._pending += text
        shown: list[str] = []
        blocks: list[str] = []
        while True:
            if not self._inside:
                start = self._pending.find(REPLY_CHIPS_OPEN)
                if start < 0:
                    cut = len(self._pending) - _held_tag_prefix(self._pending)
                    shown.append(self._pending[:cut])
                    self._pending = self._pending[cut:]
                    break
                shown.append(self._pending[:start])
                self._pending = self._pending[start:]
                self._inside = True
                self._scan_from = len(REPLY_CHIPS_OPEN)
                continue
            end = self._pending.find(REPLY_CHIPS_CLOSE, self._scan_from)
            if end < 0:
                self._scan_from = max(
                    len(REPLY_CHIPS_OPEN),
                    len(self._pending) - len(REPLY_CHIPS_CLOSE) + 1,
                )
                break
            blocks.append(self._pending[len(REPLY_CHIPS_OPEN):end])
            self._pending = self._pending[end + len(REPLY_CHIPS_CLOSE):]
            self._inside = False
        return "".join(shown), blocks

    def finish(self) -> tuple[str, bool]:
        """``(text still to show, whether an unclosed block was dropped)``."""
        rest, unclosed = ("", True) if self._inside else (self._pending, False)
        self._pending = ""
        self._inside = False
        self._scan_from = 0
        return rest, unclosed


# The tag in any spelling a reader might take for it — any case, inner
# whitespace, a closing slash, stray attributes — made inert where a data
# channel is framed. Disclosed rather than deleted, the
# ``reference_docs.neutralize_reference_delimiters`` posture. The attribute
# run is bounded, and stops at ``<``: it runs over every turn's context, and
# an unbounded ``[^>]*`` would rescan to the end of the text from every
# unclosed ``<suggested_replies`` (the quadratic trap the context markers'
# own pattern was fixed for).
REPLY_CHIPS_TAG_PATTERN = re.compile(
    r"<\s*(/?)\s*" + REPLY_CHIPS_TAG + r"\b[^<>]{0,64}>", re.IGNORECASE
)


def neutralize_reply_chip_tags(text: str) -> str:
    """Make the reply-chip tag inert inside framed data."""
    return REPLY_CHIPS_TAG_PATTERN.sub(
        lambda m: f"[escaped tag: {m.group(1)}{REPLY_CHIPS_TAG}]", text
    )


def restore_prompts(raw: Any) -> list[str]:
    """Lenient loader for a project file's ``suggested_prompts`` block.

    Malformed data degrades to ``[]`` (the ``FigureStore.load`` posture —
    the document and history are the load-bearing content, chips are
    cosmetic): salvageable string entries are kept, cleaned, deduped, and
    capped; everything else is dropped silently.
    """
    if not isinstance(raw, list):
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, str):
            continue
        text = " ".join(entry.split())
        if not text or len(text) > MAX_PROMPT_CHARS or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned[:MAX_PROMPTS]


# RETIRED (see the module docstring): kept declared, at its old position in
# the tool list, only so histories that called it stay valid requests. The
# description is version-static — it precedes the system prompt in the
# cached prefix, so nothing session-varying may ever render into it. The
# schema is unchanged from the live tool, so a saved call still matches it.
SUGGEST_PROMPTS_TOOL: dict[str, Any] = {
    "name": "suggest_prompts",
    "description": (
        "Retired — do not call this tool. Suggested replies now go at the "
        "very end of your closing message, as a "
        f"{REPLY_CHIPS_OPEN}[...]{REPLY_CHIPS_CLOSE} block (see "
        '"Suggested replies" in the system prompt). This entry remains only '
        "so that earlier conversations that called it stay valid; a call "
        "stages nothing."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "prompts": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Unused: this tool is retired.",
            },
        },
        "required": ["prompts"],
    },
}

# The is_error text a call to the retired tool receives.
RETIRED_TOOL_MESSAGE = (
    "suggest_prompts is retired — nothing was staged. Put your suggested "
    "replies at the very end of your closing message instead, as "
    f'{REPLY_CHIPS_OPEN}["…", "…"]{REPLY_CHIPS_CLOSE}.'
)
