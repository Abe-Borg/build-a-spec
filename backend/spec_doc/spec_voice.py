"""Specification voice: what model-drafted text may never carry.

The document is the specification and nothing else. Everything in it is read
by the Contractor as a requirement, so it is never the place to hold a spot
for an unknown, leave a note for the user, or offer a choice. Until
2026-10-06 the engine did the opposite on purpose: the prompt told the model
to mark every unresolved value inline as ``[TBD: ...]`` and to stamp
placeholder provisions ``needs_input``, and the export scheduled them as open
items. The owner's standing rule replaced that design (a missing value is
written around and asked about in the "Waiting on you" panel instead), and
this module is the deterministic half of the replacement — the prompt says
the rule, this enforces it.

Three consumers share one vocabulary:

- :func:`check_drafted_edits` guards every edit batch the MODEL writes
  (``apply_spec_edits`` in chat, and Final QC's proposed fixes at
  validation). A batch whose new text carries a placeholder, a bracketed
  option, a template marker or a note to the specifier — or whose status is
  one the model may not set — is refused as a whole with a message that
  says how to write it instead. The user's own panel edits never pass
  through it: what the user types is theirs.
- ``linting`` reuses :data:`PLACEHOLDER_PATTERNS`,
  :data:`TEMPLATE_MARKER_PATTERNS` and :func:`scan_markers` for its
  advisory rules, which also cover text the guard never sees (an imported
  office master, a legacy project).
- The AI template generalization contract uses :func:`has_placeholder`, so a
  generalized starter can neither gain nor lose a placeholder at any id.

The vocabulary is high-precision on purpose. A refused batch costs the model
a round, so the guard only matches what is never specification language.
Explanatory prose ("Virginia amends…", "This amendment applies…") is too
fuzzy to block on without trapping legitimate provisions in a rejection
loop; the prompt, the lint and Final QC handle that.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from .model import MODEL_STATUSES, SpecEditError

__all__ = [
    "DRAFTING_ONLY_PATTERNS",
    "MODEL_STATUSES",
    "PLACEHOLDER_PATTERNS",
    "TEMPLATE_MARKER_PATTERNS",
    "check_drafted_edits",
    "drafted_edit_problems",
    "drafted_text_hits",
    "has_placeholder",
    "scan_markers",
]

# ---------------------------------------------------------------------------
# Vocabularies — (regex, label). Detector patterns ported from
# Claude-Spec-Critic ``src/input/preprocessor.py`` (via ``linting``); the
# drafting-only additions are this app's.
# ---------------------------------------------------------------------------

#: Unresolved editorial placeholders beyond ``[TBD: ...]`` (which the
#: document model tracks as an open item in its own right).
PLACEHOLDER_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"(?i)\[\s*INSERT[^\]]*\]", "INSERT placeholder"),
    (r"(?i)\[\s*VERIFY[^\]]*\]", "VERIFY placeholder"),
    (r"(?i)\[\s*EDIT[^\]]*\]", "EDIT placeholder"),
    (r"(?i)\[\s*SELECT[^\]]*\]", "SELECT placeholder"),
    (r"(?i)\[\s*COORDINATE[^\]]*\]", "COORDINATE placeholder"),
    (r"(?i)\[\s*OPTION[^\]]*\]", "OPTION placeholder"),
    (r"(?i)<\s*VERIFY[^>]*>", "VERIFY tag"),
    (r"(?i)<\s*INSERT[^>]*>", "INSERT tag"),
    (r"_{3,}", "Underscore placeholder"),
    (r"\[\s*\.\.\.\s*\]", "Ellipsis placeholder"),
)

#: Drafting leftovers: to-do markers and boilerplate filler.
TEMPLATE_MARKER_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bTODO\s*:", "TODO marker"),
    (r"\bTODO\b(?=\s+[A-Z])", "TODO marker"),
    (r"\bFIXME\b", "FIXME marker"),
    (r"\bXXX\b(?!\d|-)", "XXX marker"),
    (r"\?{3,}", "??? marker"),
    (r"(?i)\blorem\s+ipsum\b", "Lorem-ipsum boilerplate"),
)

#: What the drafting guard refuses on top of the two shared vocabularies.
#: Order matters: :func:`scan_markers` lets an earlier match claim its span,
#: so the specific TBD label wins over the generic bracket catch-all.
DRAFTING_ONLY_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"(?i)\[\s*TB[DC]\b[^\]]*\]", "TBD placeholder"),
    (r"\bTB[DC]\b", "TBD marker"),
    (
        r"(?i)\bto\s+be\s+(?:determined|confirmed|decided)\b",
        "'to be determined' placeholder",
    ),
    (
        r"(?i)\b(?:specifier|editor|designer)(?:'s|s')?\s+notes?\b",
        "Note to the specifier",
    ),
    (
        r"(?i)\bnotes?\s+to\s+(?:the\s+)?(?:specifier|editor|designer|"
        r"engineer|user|reviewer)s?\b",
        "Note to the specifier",
    ),
    # Square brackets are how a master offers the specifier a choice
    # ("[Schedule 10] [Schedule 40]"); a finished provision makes the choice.
    # Unit conversions and titles take parentheses.
    (r"\[[^\]\n]*[A-Za-z][^\]\n]*\]", "Bracketed option or placeholder"),
)

_DRAFTED_TEXT_PATTERNS: tuple[tuple[str, str], ...] = (
    DRAFTING_ONLY_PATTERNS[:2]
    + PLACEHOLDER_PATTERNS
    + TEMPLATE_MARKER_PATTERNS
    + DRAFTING_ONLY_PATTERNS[2:]
)


def scan_markers(
    text: str,
    patterns: Iterable[tuple[str, str]],
) -> Iterable[dict[str, str]]:
    """Yield ``{"match", "label"}`` per hit, earlier patterns claiming spans.

    A match wholly inside a span an earlier pattern already claimed is
    skipped, so one placeholder is reported once, under its most specific
    label. An uncompilable pattern (a module's extra) is ignored.
    """
    seen_spans: list[tuple[int, int]] = []
    for source, label in patterns:
        try:
            compiled = re.compile(source)
        except re.error:
            continue
        for match in compiled.finditer(text):
            span = (match.start(), match.end())
            if any(s <= span[0] and span[1] <= e for s, e in seen_spans):
                continue
            seen_spans.append(span)
            yield {"match": match.group(0), "label": label}


def drafted_text_hits(text: str) -> list[dict[str, str]]:
    """Everything in ``text`` a drafted provision may not carry."""
    if not isinstance(text, str) or not text:
        return []
    return list(scan_markers(text, _DRAFTED_TEXT_PATTERNS))


def has_placeholder(text: str) -> bool:
    """True when ``text`` holds a placeholder, an option, a marker or a note."""
    return bool(drafted_text_hits(text))


# Which op fields become document text. ``title`` on set_standard_edition is
# the standard's full title, which the REFERENCES article prints.
_TEXT_FIELDS: dict[str, tuple[str, ...]] = {
    "add_article": ("text",),
    "add_paragraph": ("text",),
    "replace": ("text",),
    "set_standard_edition": ("title",),
}

# Every status the model may not stamp needs a refusal that says why
# (pinned against ``model.STATUSES`` in tests/test_spec_voice.py).
_STATUS_REFUSALS = {
    "needs_input": (
        "status 'needs_input' is set only by the user in the panel — stamp "
        "the provision assumed and ask with track_followups instead"
    ),
    "imported": (
        "status 'imported' marks starter content the app seeds — you never "
        "set it"
    ),
}

_MAX_QUOTE = 80


def _quote(value: str) -> str:
    value = " ".join(value.split())
    if len(value) > _MAX_QUOTE:
        value = value[: _MAX_QUOTE - 1] + "…"
    return f"'{value}'"


def drafted_edit_problems(edits: Any) -> list[str]:
    """One line per refused op, naming what it would have written.

    Pure and shape-tolerant: anything that is not a list of op objects, and
    any field of the wrong type, is left for ``apply_edits`` to reject in its
    own words — this check only reads what would land in the document.
    """
    if not isinstance(edits, list):
        return []
    problems: list[str] = []
    for index, op in enumerate(edits, start=1):
        if not isinstance(op, dict):
            continue
        action = op.get("action")
        where = f"edit {index} ({action} on {op.get('target_id')})"
        for field in _TEXT_FIELDS.get(action, ()):
            for hit in drafted_text_hits(op.get(field)):
                problems.append(
                    f"- {where}: {_quote(hit['match'])} — {hit['label'].lower()}"
                )
        status = op.get("status")
        if isinstance(status, str) and status in _STATUS_REFUSALS:
            problems.append(f"- {where}: {_STATUS_REFUSALS[status]}")
    return problems


def check_drafted_edits(edits: Any) -> None:
    """Refuse a model-drafted batch that would put non-spec text in the document.

    Raises :class:`SpecEditError` (the batch is all-or-nothing, like every
    other edit refusal) with the offending ops and how to write them instead.
    """
    problems = drafted_edit_problems(edits)
    if not problems:
        return
    raise SpecEditError(
        "this batch would write text that does not belong in a "
        "specification:\n"
        + "\n".join(problems)
        + "\nThe document carries specification requirements and nothing "
        "else — no placeholders, bracketed options, blanks, to-do markers, "
        "or notes to the user. Where a value is missing, write the provision "
        "so it stands complete without it (a performance requirement, a "
        "reference to the Drawings or to a submittal, or leave that clause "
        "out), stamp it assumed, and ask the user with track_followups, "
        "setting element_id to that provision. Write unit conversions and "
        "titles in parentheses, not square brackets."
    )
