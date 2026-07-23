"""Suggested reply chips: the ``suggest_prompts`` chat tool (Batch 9).

Each turn the model may stage up to five short, complete replies IN THE
USER'S VOICE — rendered as one-tap chips just above the chat composer.
Clicking a chip sends its text as the user's next message, so a chip is
always a sendable reply ("Use your recommended default", "Draft PART 2
now"), never a question or a fill-in-the-blank template.

Latest-only, turn-atomic semantics
----------------------------------
A committed turn REPLACES ``SessionState.suggested_prompts`` with whatever
the turn staged — including the empty set when the tool was not called.
That "no call = clear" rule is how the bar winds down to nothing as the
section nears issue-ready; an explicit empty list is equally valid (the
deliberate "nothing useful left to suggest" signal). A failed turn never
touches the committed list (staging is a turn-local in the conversation
loop, discarded with the turn).

Token posture
-------------
Unlike figures, the payload is tiny (<=5 strings of <=120 chars), so
nothing is elided: the ``tool_use`` input rides committed history verbatim
and the model sees last turn's chips naturally. No PROJECT CONTEXT stub, no
store. The tool RESULT stays compact (``{"suggested": N}``) all the same.
"""
from __future__ import annotations

from typing import Any

MAX_PROMPTS = 5
# Hard cap per chip; the tool description steers toward ~60 chars. A chip
# must read at a glance — anything longer belongs in the reply itself.
MAX_PROMPT_CHARS = 120


class SuggestError(ValueError):
    """A malformed ``suggest_prompts`` request. Reported to the model to fix."""


def validate_prompts(payload: Any) -> list[str]:
    """Validate a raw ``suggest_prompts`` tool input; return the cleaned list.

    Strict (model-facing): raises :class:`SuggestError`, surfaced as an
    ``is_error`` tool result the model self-corrects from — never a turn
    failure. An EMPTY list is valid (clears the bar). Internal whitespace
    folds to single spaces (chips are one-line UI); duplicates dedupe
    preserving order; the over-``MAX_PROMPTS`` check runs AFTER cleanup so a
    list that dedupes down to the cap passes.
    """
    if not isinstance(payload, dict):
        raise SuggestError("suggest_prompts: input must be an object.")
    raw = payload.get("prompts")
    if not isinstance(raw, list):
        raise SuggestError(
            "suggest_prompts: 'prompts' must be a list of strings "
            "(an empty list clears the bar)."
        )
    cleaned: list[str] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, str):
            raise SuggestError("suggest_prompts: every prompt must be a string.")
        text = " ".join(entry.split())
        if not text:
            raise SuggestError(
                "suggest_prompts: prompts must be non-empty — drop blank "
                "entries rather than sending them."
            )
        if len(text) > MAX_PROMPT_CHARS:
            raise SuggestError(
                f"suggest_prompts: a prompt is too long ({len(text)} > "
                f"{MAX_PROMPT_CHARS} chars) — chips must read at a glance; "
                "shorten it to one clause."
            )
        if text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    if len(cleaned) > MAX_PROMPTS:
        raise SuggestError(
            f"suggest_prompts: too many prompts ({len(cleaned)} > "
            f"{MAX_PROMPTS}). Send only the {MAX_PROMPTS} most useful."
        )
    return cleaned


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


# Lenient schema (the create_figure posture, NOT the research strict shape):
# validation lives in validate_prompts, and a bad payload becomes an
# is_error tool result the model corrects. The description is version-static
# — it precedes the system prompt in the cached prefix, so nothing
# session-varying may ever render into it.
SUGGEST_PROMPTS_TOOL: dict[str, Any] = {
    "name": "suggest_prompts",
    "description": (
        "Offer up to 5 one-tap reply chips shown just above the user's chat "
        "box. Each chip's label IS the message: clicking it sends that exact "
        "text as the user's next chat message, so every prompt must be a "
        "short, complete, sendable reply written in the USER'S voice — e.g. "
        '"Use your recommended default", "Draft PART 2 now", "Yes, ordinary '
        'hazard group 2", or a concrete value like "The ceiling height is '
        '32 ft" ONLY when that value is actually known from the '
        "conversation, profile, or research. Never a fill-in-the-blank "
        "template, never a question, never an instruction addressed to the "
        "user.\n"
        "\n"
        "Order matters: lead with direct answers to the questions you just "
        "asked (always include an accept-your-recommendation option and, "
        'when honest, "I don\'t know — use your default"), then add '
        "momentum moves if slots remain. Suggest only things sayable IN "
        "CHAT that you can act on next turn — research runs, Final QC, "
        "export, undo, and saving are panel buttons, not chat messages. "
        "Don't re-suggest what's already done or answered.\n"
        "\n"
        "Keep each prompt under 120 characters (hard limit) and aim for "
        "under ~60 — chips must read at a glance. Call at most once per "
        "turn, near the end of your reply. Each call REPLACES the previous "
        "set entirely, and a turn where you don't call this tool clears the "
        "bar — so as the section nears issue-ready, wind down to 1-2 chips "
        'or none. An empty prompts list is valid and means "nothing useful '
        'left to suggest".'
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "prompts": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "0-5 short reply chips in the user's voice, most useful "
                    "first. Empty list = clear the bar."
                ),
            },
        },
        "required": ["prompts"],
    },
}
