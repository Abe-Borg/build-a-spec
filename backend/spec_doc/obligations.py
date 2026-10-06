"""Obligation anchors, and whether a relocation carries a provision intact.

The writing policy (:mod:`backend.writing_policy`) says a requirement keeps
every actor, condition, qualifier, value, unit, tag and acceptance criterion
when it is split or moved, and that a move across a PART is an add plus a
delete because ``move`` never reparents. A Final QC fix that does that is
applied as an exact operation set once the user approves it, so the parts of
that promise a machine can check are checked here, before the fix may be
called safe:

- every **anchor** of a carried provision — a value with its unit, a tag
  such as ``V1`` or ``FDC-1``, a standard designation, a section number —
  appears in the provisions that carry it (its **carriers**: the provisions
  the fix adds or retypes that take their wording from it), not merely
  somewhere in the document, where an unrelated provision could hold the
  same value;
- every carrier keeps its ``source_item_id`` and its status — relocation is
  editorial, so a confirmed provision is not quietly re-stamped assumed by
  an ``add_paragraph`` that omits ``status``, an assumed one is not
  promoted, and content still stamped imported is reviewed before it moves;
- a carried provision's subparagraphs are carried too. One fix cannot re-add
  them under the new provision, whose id the server assigns, so a provision
  with subparagraphs is effectively never relocatable in one fix — the
  finding stays advisory, which is what the brief asks for.

What this cannot check — whether a qualifier word ("each", "only", "except")
still limits the same requirement, whether a condition moved with its
obligation — stays with the verifier seats, who judge against the same
policy text.

Scope is deliberately narrow. The rules apply only to a fix that both
deletes content and adds or retypes content, and only to deleted provisions
whose words reappear in the new text (:data:`CARRIED_COVERAGE`): content a
fix removes outright — an inapplicable article replaced by a cross-reference
— is a scope decision the verifiers judge, not a relocation. A fix that
retypes a provision without deleting one (a split by ``replace`` plus
``add_paragraph``) is likewise left to the verifiers; that limit is recorded
in docs/writing-policy.md.

Chat edits are not checked here: the user watches them land and can undo,
and the document tool's contract is unchanged. Final QC fixes are the
pre-approved path, which is where an unattended loss would hide.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Iterator

from .linting import _DESIGNATION_RE
from .model import Article, Paragraph, SpecSection, iter_paragraphs

# A number as specifications write one: thousands separators, decimals, and
# fractions including mixed ones ("1-1/2"). Anything finer (ranges, ratios)
# still yields its pieces, which is all a presence check needs.
_NUMBER = r"\d+(?:,\d{3})*(?:\.\d+)?(?:-\d+/\d+|/\d+)?"

# Units a value is checked WITH. A closed list on purpose: "13 and" is not a
# quantity, and an open "number + next word" anchor would flag a perfectly
# good split of "NFPA 13 and the manufacturer's instructions".
_UNITS = (
    "psi", "psig", "psia", "kpa", "bar", "gpm", "gph", "lpm", "gal", "gallon",
    "gallons", "ft", "feet", "foot", "inch", "inches", "mm", "cm", "meter",
    "meters", "sq", "deg", "degrees", "hr", "hrs", "hour", "hours", "min",
    "minute", "minutes", "sec", "second", "seconds", "day", "days", "week",
    "weeks", "month", "months", "year", "years", "percent", "lb", "lbs",
    "pound", "pounds", "kg", "volt", "volts", "vac", "vdc", "hz", "kw", "hp",
    "db", "dba", "ohm", "ohms", "cfm", "fpm", "ft2", "sf",
)
# "in" and "m" alone read as prose too often ("1 in each room"); the
# abbreviated inch counts only with its period.
_UNIT_ALTERNATION = "|".join(
    re.escape(unit) for unit in sorted(_UNITS, key=len, reverse=True)
)
_QUANTITY_RE = re.compile(
    rf"(?<![\w./])({_NUMBER})\s*(?:-\s*)?(%|in\.|(?:{_UNIT_ALTERNATION})\b\.?)",
    re.IGNORECASE,
)
# Prefix units: "NPS 2", "DN 50".
_PREFIX_QUANTITY_RE = re.compile(rf"\b(NPS|DN)\s*({_NUMBER})(?![\d/])")
# A bare number of two or more digits, or any decimal or fraction — an
# edition year, a model number, a rating. Single digits are too common in
# prose ("two", list numbering) to mean anything on their own. A number that
# follows "Article"/"Paragraph" is a cross-reference, which an authorized
# relocation may legitimately renumber, so it is not an anchor.
_BARE_NUMBER_RE = re.compile(
    rf"(?<![\w./])(?<!Article )(?<!Articles )(?<!Paragraph )(?<!Paragraphs )"
    rf"({_NUMBER})(?![\w/])",
)
# A tag: one to five capitals, an optional hyphen, digits, an optional
# letter suffix — "V1", "FDC-1", "BFP-2A", "P101".
_TAG_RE = re.compile(r"\b[A-Z]{1,5}-?\d{1,4}[A-Z]{0,2}\b")
_SECTION_NUMBER_RE = re.compile(r"\b\d{2} \d{2} \d{2}(?:\.\d{2})?\b")

#: Share of a deleted provision's content words that must reappear in the
#: fix's new text for the provision to count as carried (relocated or split)
#: rather than removed.
CARRIED_COVERAGE = 0.6

_WORD_RE = re.compile(r"[a-z][a-z'-]{2,}")
_STOPWORDS = frozenset(
    "the and for with from that this these those shall each all any are "
    "not but its into onto per than then when where which while who whom "
    "has have had will was were been being such other provide provided "
    "accordance".split()
)


def _normalized(text: str) -> str:
    """Case-folded, whitespace-collapsed, "175-psi" read as "175 psi"."""
    folded = " ".join((text or "").split()).casefold()
    return re.sub(r"(?<=\d)\s*-\s*(?=[a-z%])", " ", folded)


def obligation_anchors(text: str) -> tuple[str, ...]:
    """The checkable anchors of one provision, in reading order, unique.

    Each anchor is returned normalized (:func:`_normalized`), so a presence
    check compares like with like.
    """
    found: list[tuple[int, str]] = []
    claimed: list[tuple[int, int]] = []

    def take(start: int, end: int, anchor: str) -> None:
        found.append((start, _normalized(anchor)))
        claimed.append((start, end))

    def free(start: int, end: int) -> bool:
        return not any(s < end and start < e for s, e in claimed)

    for match in _SECTION_NUMBER_RE.finditer(text):
        take(match.start(), match.end(), match.group(0))
    for match in _DESIGNATION_RE.finditer(text):
        if free(match.start(), match.end()):
            take(match.start(), match.end(), match.group(0))
    for match in _PREFIX_QUANTITY_RE.finditer(text):
        if free(match.start(), match.end()):
            take(match.start(), match.end(), f"{match.group(1)} {match.group(2)}")
    for match in _QUANTITY_RE.finditer(text):
        if free(match.start(), match.end()):
            unit = match.group(2).rstrip(".")
            take(match.start(), match.end(), f"{match.group(1)} {unit}")
    for match in _TAG_RE.finditer(text):
        if free(match.start(), match.end()):
            take(match.start(), match.end(), match.group(0))
    for match in _BARE_NUMBER_RE.finditer(text):
        number = match.group(1)
        if not free(match.start(1), match.end(1)):
            continue
        if len(number) >= 2 or not number.isdigit():
            take(match.start(1), match.end(1), number)
    ordered = [anchor for _start, anchor in sorted(found)]
    return tuple(dict.fromkeys(anchor for anchor in ordered if anchor))


def anchor_present(anchor: str, haystack: str) -> bool:
    """Whether a normalized ``anchor`` occurs, token-bounded, in ``haystack``.

    ``haystack`` must already be :func:`_normalized`.
    """
    pattern = r"(?<![a-z0-9])" + re.escape(anchor) + r"(?![a-z0-9])"
    return re.search(pattern, haystack) is not None


def _content_words(text: str) -> set[str]:
    return {
        word
        for word in _WORD_RE.findall((text or "").casefold())
        if word not in _STOPWORDS
    }


def _walk(paragraphs: Iterable[Paragraph]) -> Iterator[Paragraph]:
    for paragraph in paragraphs:
        yield paragraph
        yield from _walk(paragraph.children)


def _find_element(section: SpecSection, uid: str) -> Article | Paragraph | None:
    for part in section.parts:
        for article in part.articles:
            if article.uid == uid:
                return article
            for paragraph in _walk(article.paragraphs):
                if paragraph.uid == uid:
                    return paragraph
    return None


#: A touched provision carries a deleted one when at least this share of
#: either's content words is shared: most of a split piece's words come from
#: its source, and most of a relocated provision's words reappear in its copy.
CARRIER_SHARE = 0.5


def _carriers(paragraph: Paragraph, touched: list[Paragraph]) -> list[Paragraph]:
    """The touched provisions that take their wording from ``paragraph``.

    Falls back to every touched provision sharing any word when none clears
    :data:`CARRIER_SHARE` — a carried provision always has somewhere to be
    checked against.
    """
    words = _content_words(paragraph.text)
    shares = []
    for candidate in touched:
        theirs = _content_words(candidate.text)
        shared = len(words & theirs)
        if shared:
            shares.append(
                (
                    candidate,
                    max(shared / len(words), shared / len(theirs) if theirs else 0),
                )
            )
    strong = [candidate for candidate, share in shares if share >= CARRIER_SHARE]
    return strong or [candidate for candidate, _share in shares]


def relocation_problems(
    before: SpecSection, after: SpecSection, ops: Any
) -> list[str]:
    """Why an operation set does not carry relocated provisions intact.

    ``before`` is the snapshot the operations were proposed against and
    ``after`` the dry-run result. Empty when the set is not a relocation
    (see the module docstring) or carries everything it moves. Each problem
    is one short sentence naming the provision by its panel reference.
    """
    if not isinstance(ops, list):
        return []
    ops = [op for op in ops if isinstance(op, dict)]
    deleted_ids = [
        str(op.get("target_id") or "")
        for op in ops
        if op.get("action") == "delete"
    ]
    new_text_ops = [
        op
        for op in ops
        if isinstance(op.get("text"), str)
        and (
            op.get("action") == "add_paragraph"
            or (
                op.get("action") == "replace"
                and isinstance(
                    _find_element(before, str(op.get("target_id") or "")),
                    Paragraph,
                )
            )
        )
    ]
    if not deleted_ids or not new_text_ops:
        return []

    refs = {
        paragraph.uid: ref
        for _part, _article, paragraph, _depth, ref in iter_paragraphs(before)
    }
    deleted: list[Paragraph] = []
    for uid in deleted_ids:
        element = _find_element(before, uid)
        if isinstance(element, Article):
            deleted.extend(_walk(element.paragraphs))
        elif isinstance(element, Paragraph):
            deleted.extend(_walk([element]))
    deleted = list({paragraph.uid: paragraph for paragraph in deleted}.values())
    if not deleted:
        return []

    # What the fix wrote: provisions it added (ids the snapshot never had)
    # and provisions it retyped. Read from the dry-run result, so status and
    # source are what will actually land — an add_paragraph without a status
    # is stamped assumed there, exactly as it would be on apply.
    before_ids = {
        paragraph.uid
        for _part, _article, paragraph, _depth, _ref in iter_paragraphs(before)
    }
    retyped = {
        str(op.get("target_id") or "")
        for op in new_text_ops
        if op.get("action") == "replace"
    }
    touched = [
        paragraph
        for _part, _article, paragraph, _depth, _ref in iter_paragraphs(after)
        if paragraph.uid not in before_ids or paragraph.uid in retyped
    ]
    if not touched:
        return []
    new_words: set[str] = set()
    for paragraph in touched:
        new_words |= _content_words(paragraph.text)

    def carried(paragraph: Paragraph) -> bool:
        words = _content_words(paragraph.text)
        if not words:
            return False
        return len(words & new_words) / len(words) >= CARRIED_COVERAGE

    carried_ids = {paragraph.uid for paragraph in deleted if carried(paragraph)}
    if not carried_ids:
        return []

    problems: list[str] = []
    for paragraph in deleted:
        if paragraph.uid not in carried_ids:
            continue
        ref = refs.get(paragraph.uid, paragraph.uid)
        carriers = _carriers(paragraph, touched)
        carrier_text = _normalized("\n".join(c.text for c in carriers))
        lost = [
            anchor
            for anchor in obligation_anchors(paragraph.text)
            if not anchor_present(anchor, carrier_text)
        ]
        if lost:
            problems.append(
                f"{ref} loses {', '.join(repr(anchor) for anchor in lost)}"
            )
        if paragraph.source_item_id and any(
            carrier.source_item_id != paragraph.source_item_id
            for carrier in carriers
        ):
            problems.append(
                f"{ref} loses its source link {paragraph.source_item_id}"
            )
        changed = sorted(
            {carrier.status for carrier in carriers} - {paragraph.status}
        )
        if changed:
            problems.append(
                f"{ref} changes status from {paragraph.status} to "
                + " / ".join(changed)
                + (
                    " (review imported content before it moves)"
                    if paragraph.status == "imported"
                    else ""
                )
            )
        dropped_children = [
            child for child in paragraph.children if child.uid not in carried_ids
        ]
        if dropped_children:
            problems.append(
                f"{ref} moves without its subparagraphs; one fix cannot "
                "re-add them under the new provision"
            )
    return problems
