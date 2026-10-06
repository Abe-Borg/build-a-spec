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
- The AI template generalization contract compares
  :func:`drafted_text_hits` by exact text, so a generalized starter can
  neither gain, lose nor swap a placeholder at any id.
- ``qc.apply.finding_fix_class`` re-runs :func:`drafted_edit_problems`, so
  a Final QC report retained from before this guard cannot apply a
  placeholder fix its stored ``ops_valid`` still vouches for.

The vocabulary is high-precision on purpose. A refused batch costs the model
a round, so the guard only matches what is never specification language.
Explanatory prose ("Virginia amends…", "This amendment applies…") is too
fuzzy to block on without trapping legitimate provisions in a rejection
loop; the prompt, the lint and Final QC handle that. The lint's half lives
at the end of this module (:data:`EXPLANATORY_PROSE_PATTERNS`,
:func:`reference_entry_problems`): it reports, it never refuses.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from .model import MODEL_STATUSES, SpecEditError

__all__ = [
    "DRAFTING_ONLY_PATTERNS",
    "EXPLANATORY_PROSE_PATTERNS",
    "MODEL_STATUSES",
    "PLACEHOLDER_PATTERNS",
    "REFERENCES_ARTICLE_RE",
    "REFERENCE_ENTRY_MAX_CHARS",
    "REFERENCE_ENTRY_PATTERNS",
    "TEMPLATE_MARKER_PATTERNS",
    "check_drafted_edits",
    "drafted_edit_problems",
    "drafted_text_hits",
    "explanatory_prose_hits",
    "has_placeholder",
    "reference_entry_problems",
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
    for _start, matched, label in _scan_spans(text, patterns):
        yield {"match": matched, "label": label}


def _scan_spans(
    text: str,
    patterns: Iterable[tuple[str, str]],
) -> Iterable[tuple[int, str, str]]:
    """:func:`scan_markers` with each hit's start offset, same claiming."""
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
            yield match.start(), match.group(0), label


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


# ---------------------------------------------------------------------------
# Advisory: explanatory prose and overlong REFERENCES entries (PR 2)
#
# The guard above blocks what is never specification language. What follows
# only REPORTS — through the lint, which the panel shows and every chat turn
# carries — because explanation is a matter of degree and a refused batch
# would trap a legitimate provision in a rejection loop. The phrase list is
# the owner's (2026-10-06), with three phrases narrowed so ordinary spec
# language stays clean: "governs" only when it governs something ("the more
# stringent requirement governs" is a directive), "incorporates" only in the
# adoption sense (a controller may incorporate a switch), and "consistent
# with the" only beside an edition, code or adoption. Each label is the
# advice the lint message gives.
# ---------------------------------------------------------------------------

_NARRATES = "narrates where the requirement comes from"
_REASON = "gives a reason — state only the requirement"
_APPLIES = "explains where it applies"
_TALKS = "talks about the document instead of directing"
_APP_TERMS = "uses the app's own bookkeeping terms"
_DESIGN_TEAM = "addresses the design team"
_HEDGES = "hedges — state the requirement"

#: Phrases that mark a provision explaining instead of directing. Matching
#: ignores case; earlier patterns claim their span first (``scan_markers``).
EXPLANATORY_PROSE_PATTERNS: tuple[tuple[str, str], ...] = (
    # Amendment and adoption narration.
    (r"(?i)\b(?:this|the)\s+amendment\b", _NARRATES),
    (r"(?i)\bamends\b", _NARRATES),
    (r"(?i)\bhas\s+adopted\b", _NARRATES),
    (r"(?i)\badopts\b", _NARRATES),
    (
        r"(?i)\bincorporat\w*\b[^.;]{0,80}?\b(?:codes?|standards?|editions?|"
        r"by\s+reference|I[BF]C)\b",
        _NARRATES,
    ),
    (r"(?i)\bby\s+reference\b", _NARRATES),
    (r"(?i)\bis\s+understood\s+to\b", _NARRATES),
    (r"(?i)\bcorroborat\w*", _NARRATES),
    (
        r"(?i)\bconsistent\s+with\s+the\b[^.;]{0,40}?\b(?:editions?|codes?|"
        r"adoptions?|amendments?)\b",
        _NARRATES,
    ),
    # Reasons and applicability.
    (r"(?i)\bbecause\b", _REASON),
    (r"(?i)\bin\s+order\s+to\b", "'in order to' gives a reason — state only the requirement"),
    (r"(?i)\bthe\s+intent\b", _REASON),
    (r"(?i)\b(?:is|are)\s+intended\s+to\b", _REASON),
    (r"(?i)(?<!for )\bthe\s+purpose\s+of\b", _REASON),
    (r"(?i)\bthis\s+ensures\b", _REASON),
    (r"(?i)\bgoverns\s+(?:the|this|each|all)\b", _APPLIES),
    (r"(?i)\bapplies\s+generally\b", _APPLIES),
    (r"(?i)\b(?:this|the)\s+(?:provision|requirement)\s+applies\b", _APPLIES),
    (r"(?i)\bapplies\s+to\s+this\s+project\b", _APPLIES),
    # Talking about the document.
    (r"(?i)\bthis\s+(?:provision|requirement|paragraph)\b", _TALKS),
    # The app's own bookkeeping terms.
    (r"(?i)\brecorded\s+for\s+this\s+project\b", _APP_TERMS),
    (r"(?i)\bedition\s+recorded\b", _APP_TERMS),
    (r"(?i)\brecorded\s+edition\b", _APP_TERMS),
    (r"(?i)\badoption\s+basis\b", _APP_TERMS),
    (r"(?i)\bbasis\s*:", _APP_TERMS),
    (r"(?i)\bresearch\s+items?\b", _APP_TERMS),
    (r"(?i)\bper\s+research\b", _APP_TERMS),
    (r"(?i)\bresearch\s+shows\b", _APP_TERMS),
    (r"(?i)\bproject\s+(?:profile|facts?)\b", _APP_TERMS),
    (r"(?i)\bopen\s+items?\b", _APP_TERMS),
    (r"(?i)\bmodel-proposed\b", _APP_TERMS),
    (r"(?i)\bunverified\b", _APP_TERMS),
    (r"(?i)\bdesign\s+baseline\b", _APP_TERMS),
    # Addressed to the design team.
    (r"(?i)\bthe\s+(?:specifier|designer|design\s+team|user)\b", _DESIGN_TEAM),
    # Hedging and non-mandatory language.
    (r"(?i)\bit\s+is\s+recommended\b", _HEDGES),
    (r"(?i)\bwe\s+recommend\b", _HEDGES),
    (r"(?i)\bshould\b", "'should' — use 'shall' or the imperative"),
    (r"(?i)\blikely\b", _HEDGES),
    (r"(?i)\bprobably\b", _HEDGES),
    (r"(?i)\bmay\s+need\s+to\b", _HEDGES),
    (
        r"(?i)\bpending\b",
        "'pending' — leave it only if it is a real condition of the work",
    ),
)

#: Words that put prose into a REFERENCES entry. Case-sensitive on purpose
#: except where marked: description is lower-case prose, while standard
#: titles are Title Case ("Wall Coverings", "Standard for … Primary …").
REFERENCE_ENTRY_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"(?i)\balso\s+referenced\b", "names a second standard"),
    (r"(?i)\bsee\s+also\b", "names a second standard"),
    (r"—(?=\s*[a-z])", "an em-dash description"),
    (r"\bcovering\b", "describes the standard"),
    (r"\bfor\s+general\b", "describes the standard"),
    (r"\bprimary\b", "describes the standard"),
    (r"\bper\s+the\b", "carries a project decision"),
    (r"\bspecified\s+for\b", "carries a project decision"),
    (r"\bvia\b", "carries adoption reasoning"),
    (r"(?i)\bbecause\b", "carries adoption reasoning"),
    (r"\brecorded\b", "carries adoption reasoning"),
    (r"\bincorporates\b", "carries adoption reasoning"),
    (r"\bunderstood\b", "carries adoption reasoning"),
    (r"\bcorroborated\b", "carries adoption reasoning"),
)

#: An entry longer than this is carrying more than a designation, a title
#: and an edition: the owner's NFPA 25 entry is ~115 characters, and long
#: ASTM titles run ~160.
REFERENCE_ENTRY_MAX_CHARS = 220

#: An article holding the section's reference standards.
REFERENCES_ARTICLE_RE = re.compile(
    r"\bREFERENCE(?:S|D\s+STANDARDS|\s+STANDARDS)\b", re.IGNORECASE
)

# Words that end in a period without ending a sentence ("No. 4", "U.S.").
_ABBREVIATIONS = frozenset(
    {
        "no", "nos", "inc", "std", "rev", "vol", "ed", "eds", "corp", "co",
        "ltd", "st", "sec", "pt", "pub", "div", "dept", "assn", "fig", "ch",
        "art", "app", "vs", "etc", "approx", "min", "max", "e.g", "i.e",
    }
)
_SENTENCE_BREAK_RE = re.compile(r"[.;](?=\s+[A-Z(\"“])")
_WORD_BEFORE_RE = re.compile(r"([A-Za-z.]+)$")


def explanatory_prose_hits(text: str) -> list[dict[str, str]]:
    """Every phrase in ``text`` that explains instead of directing.

    In reading order, so the lint message quotes them as the provision does.
    """
    if not isinstance(text, str) or not text:
        return []
    return [
        {"match": matched, "label": label}
        for _start, matched, label in sorted(
            _scan_spans(text, EXPLANATORY_PROSE_PATTERNS),
            key=lambda hit: hit[0],
        )
    ]


def _sentence_count(text: str) -> int:
    """Sentences in ``text``, not counting abbreviation periods."""
    breaks = 0
    for match in _SENTENCE_BREAK_RE.finditer(text):
        before = _WORD_BEFORE_RE.search(text[: match.start()])
        word = (before.group(1) if before else "").lower().rstrip(".")
        # "No.", a lone initial, or a dotted abbreviation ("U.S.", "e.g.").
        if match.group(0) == "." and (
            word in _ABBREVIATIONS
            or (len(word) == 1 and word.isalpha())
            or "." in word
        ):
            continue
        breaks += 1
    return breaks + 1 if text.strip() else 0


def reference_entry_problems(text: str) -> list[str]:
    """Why a REFERENCES entry carries more than designation, title, edition."""
    if not isinstance(text, str) or not text.strip():
        return []
    problems: list[str] = []
    if _sentence_count(text) > 1:
        problems.append("more than one sentence")
    if len(text.strip()) > REFERENCE_ENTRY_MAX_CHARS:
        problems.append(f"over {REFERENCE_ENTRY_MAX_CHARS} characters")
    grouped: dict[str, list[str]] = {}
    for hit in scan_markers(text, REFERENCE_ENTRY_PATTERNS):
        quoted = f"'{hit['match'].strip()}'"
        matches = grouped.setdefault(hit["label"], [])
        if quoted not in matches:
            matches.append(quoted)
    problems.extend(
        f"{label} ({', '.join(matches)})" for label, matches in grouped.items()
    )
    return problems
