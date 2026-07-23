"""Deterministic, no-API lint of the SectionFormat tree.

Detector *logic* ported from Claude-Spec-Critic ``src/input/preprocessor.py``
(the stale-edition year patterns, the negation-suppression window with
sentence narrowing, span-based dedup, and the placeholder/template-marker
vocabulary), repointed from raw extracted text at the structured document
tree. The standard-name vocabulary comes from the module's pinned basis
merged with the session's recorded jurisdiction overrides
(``standards.effective_editions``), so the stale-edition rule always checks
against the editions actually in effect.

Issues are **advisory, never blocking** — they surface in the panel's
issues drawer and are recomputed on every document mutation (pure Python,
fast at document scale).

Rules (stable ids consumers can branch on):

- ``stale_edition`` — a standard cited at an edition contradicting the
  edition in effect (module pin or jurisdiction override).
- ``unrecorded_edition`` — (unpinned-basis modules only, Batch 9) a
  designation cited WITH an edition year while no edition is recorded for
  that standard at all — the no-pins posture's enforcement: record the
  edition and its basis via ``set_standard_edition``, or drop the year.
  Never active for a pinned module, so its lint output is unchanged.
- ``placeholder_marker`` — unresolved editorial placeholders beyond the
  first-class ``[TBD: ...]`` tracking: ``[INSERT ...]``, ``[VERIFY ...]``,
  ``___``, ``<VERIFY>``, ellipsis brackets, and module extras.
- ``template_marker`` — ``TODO:`` / ``FIXME`` / ``XXX`` / ``???`` /
  lorem-ipsum boilerplate.
- ``empty_article`` — an article heading with no paragraphs under it.
- ``duplicate_article_title`` — the same title twice within one part.
- ``missing_section_header`` — articles drafted while the section
  number/title is still unset (info-level).
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Iterable, Mapping

from ..standards import (
    EffectiveEdition,
    effective_editions,
    normalize_standard_name,
)
from .model import SpecSection, iter_paragraphs

RULE_STALE_EDITION = "stale_edition"
RULE_UNRECORDED_EDITION = "unrecorded_edition"
RULE_PLACEHOLDER = "placeholder_marker"
RULE_TEMPLATE_MARKER = "template_marker"
RULE_EMPTY_ARTICLE = "empty_article"
RULE_DUPLICATE_ARTICLE_TITLE = "duplicate_article_title"
RULE_MISSING_SECTION_HEADER = "missing_section_header"

# ---------------------------------------------------------------------------
# Text-scan vocabularies (ported patterns; [TBD: ...] is deliberately absent
# — it is first-class open-item tracking, not lint)
# ---------------------------------------------------------------------------

_PLACEHOLDER_PATTERNS: tuple[tuple[str, str], ...] = (
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

_TEMPLATE_MARKER_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bTODO\s*:", "TODO marker"),
    (r"\bTODO\b(?=\s+[A-Z])", "TODO marker"),
    (r"\bFIXME\b", "FIXME marker"),
    (r"\bXXX\b(?!\d|-)", "XXX marker"),
    (r"\?{3,}", "??? marker"),
    (r"(?i)\blorem\s+ipsum\b", "Lorem-ipsum boilerplate"),
)

# ---------------------------------------------------------------------------
# Stale-edition detection
# ---------------------------------------------------------------------------

# Real-world edition years for the standards this app deals in.
_YEAR = r"(19[5-9]\d|20\d{2})"


@lru_cache(maxsize=64)
def _edition_patterns_for(name: str) -> tuple[re.Pattern[str], ...]:
    """Compile the citation patterns that bind ``name`` to an edition year.

    Four engine shapes (year captured as group 1 in each): ``NFPA 13-2019``
    (also en/em dash), ``NFPA 13, 2019`` / ``NFPA 13 (2019)`` / ``NFPA 13
    2019``, ``2019 edition of NFPA 13``, and the REFERENCES-article line
    shape ``NFPA 13 - Standard for ... (2019 edition)`` — designation, a
    digit-free title gap, then ``<year> edition``. The digit-free gap keeps
    a designation list ("NFPA 13 and NFPA 14 (2024 edition)") from
    attributing the year across designations, and the negative lookahead
    after the name keeps ``NFPA 13`` from matching inside ``NFPA 13R`` or
    ``NFPA 130``.
    """
    esc = re.escape(name)
    guard = r"(?![0-9A-Za-z])"
    # Longest/most-specific shape first so it claims spans before the
    # generic adjacency patterns (span-dedup then drops their sub-matches)
    # — the same ordering convention as Spec Critic's preprocessor.
    return (
        re.compile(
            rf"\b{esc}{guard}[^.;\n\d]{{0,100}}\(?{_YEAR}\s+edition\b",
            flags=re.IGNORECASE,
        ),
        re.compile(
            rf"\b{esc}{guard}\s*[-–—]\s*{_YEAR}\b", flags=re.IGNORECASE
        ),
        re.compile(
            rf"\b{esc}{guard}[,\s(]\s*{_YEAR}\b", flags=re.IGNORECASE
        ),
        re.compile(
            rf"\b{_YEAR}\s+edition\s+of\s+{esc}{guard}", flags=re.IGNORECASE
        ),
    )


# Negation / historical qualifiers that suppress a stale-edition alert when
# they appear in the same sentence near the citation (ported verbatim in
# spirit from the preprocessor: small window, sentence-narrowed, bare "not"
# deliberately excluded).
_SUPPRESS_WINDOW = 80

_SUPPRESS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bpreviously\b", flags=re.IGNORECASE),
    re.compile(r"\bformerly\b", flags=re.IGNORECASE),
    re.compile(r"\bsuperseded\b", flags=re.IGNORECASE),
    re.compile(r"\bwithdrawn\b", flags=re.IGNORECASE),
    re.compile(r"\bobsolete\b", flags=re.IGNORECASE),
    re.compile(r"\bno\s+longer\b", flags=re.IGNORECASE),
    re.compile(r"\bprior\b", flags=re.IGNORECASE),
    re.compile(r"\bhistorical\b", flags=re.IGNORECASE),
    re.compile(
        r"\b(?:shall|will|does|do|is|are|was|were|must|may|can)\s+not\b",
        flags=re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:isn't|wasn't|aren't|weren't|won't|don't|doesn't|shan't|"
        r"mustn't|can't|cannot)\b",
        flags=re.IGNORECASE,
    ),
)


def _suppressed(text: str, start: int, end: int) -> bool:
    """True when a negation/historical keyword qualifies the citation."""
    pre = text[max(0, start - _SUPPRESS_WINDOW) : start]
    for term in (".", ";", "\n\n"):
        cut = pre.rfind(term)
        if cut >= 0:
            pre = pre[cut + len(term) :]
    post = text[end : end + _SUPPRESS_WINDOW]
    for term in (".", ";", "\n\n"):
        cut = post.find(term)
        if cut >= 0:
            post = post[:cut]
            break
    return any(
        pat.search(window)
        for window in (pre, post)
        if window.strip()
        for pat in _SUPPRESS_PATTERNS
    )


def _scan_editions(
    text: str, editions: tuple[EffectiveEdition, ...]
) -> Iterable[dict[str, str]]:
    """Yield stale-edition hits in ``text`` against the editions in effect."""
    seen_spans: list[tuple[int, int]] = []
    for eff in editions:
        expected = eff.edition.strip()
        if not expected:
            continue
        for pattern in _edition_patterns_for(eff.name):
            for match in pattern.finditer(text):
                year = match.group(1)
                if year == expected:
                    continue
                span = (match.start(), match.end())
                if any(s <= span[0] and span[1] <= e for s, e in seen_spans):
                    continue
                seen_spans.append(span)
                if _suppressed(text, *span):
                    continue
                basis_note = (
                    f" (jurisdiction override: {eff.basis})"
                    if eff.is_override
                    else " (module default)"
                )
                yield {
                    "match": match.group(0),
                    "message": (
                        f"{eff.name} cited at {year}, but the edition in "
                        f"effect is {expected}{basis_note}."
                    ),
                }


# ---------------------------------------------------------------------------
# Unrecorded-edition detection (unpinned-basis modules only, Batch 9)
# ---------------------------------------------------------------------------

# Publisher grammar for standards designations. Longest-first alternation so
# "CAN/ULC" claims before "ULC" before "UL", "AWWA" before "AWS". Compiled
# case-SENSITIVE deliberately: designations are acronym-styled in real spec
# text, and requiring caps keeps prose words from false-matching.
_DESIGNATION_PUBLISHERS: tuple[str, ...] = (
    "CAN/ULC", "CAN/CSA", "SMACNA", "ASHRAE", "IAPMO", "AWWA", "AMCA",
    "AHRI", "NEMA", "NFPA", "ASTM", "ANSI", "ASME", "IEEE", "SSPC", "ASSE",
    "AISC", "AISI", "ACI", "AWS", "CSA", "CTI", "ICC", "IEC", "ISA", "ISO",
    "MSS", "NSF", "PDI", "TIA", "ULC", "FM", "UL",
)

# Designation token: optional letter prefix, digits (dotted parts allowed),
# optional letter suffix, optional slash-companion ("A53/A53M"). It cannot
# swallow a trailing "-<year>" — dashes are not part of the token — so
# "NFPA 13-2019" discovers "NFPA 13" and leaves the year for the citation
# patterns; the trailing lookahead keeps "NFPA 13" from ending inside
# "NFPA 13R" or "NFPA 130".
_DESIGNATION_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(p) for p in _DESIGNATION_PUBLISHERS) + r")"
    r"[ -]?"
    r"[A-Z]{0,3}\d+(?:\.\d+)*[A-Z]{0,2}(?:/[A-Z]{0,3}\d+[A-Z]{0,2})?"
    r"(?![0-9A-Za-z])"
)


def _canonical_designation_forms(name: str) -> set[str]:
    """Canonical keys for matching a designation against recorded editions.

    Text writes "CAN/ULC-S524" where an override may be recorded as
    "CAN/ULC S524" (or vice versa) — treat hyphen and space forms as the
    same standard. Broadening the match errs toward "recorded" (fewer
    advisory warns), the safe direction.
    """
    return {
        normalize_standard_name(name),
        normalize_standard_name(name.replace("-", " ")),
    }


def _scan_unrecorded_editions(
    text: str, editions: tuple[EffectiveEdition, ...]
) -> Iterable[dict[str, str]]:
    """Yield year-cited designations with NO recorded edition at all.

    The complement of :func:`_scan_editions`: that rule checks names IN the
    effective set for a contradicting year; this one checks names ABSENT
    from the set for any year binding (same four citation shapes, same
    span-dedup and negation suppression). Only meaningful for an
    unpinned-basis module — the caller gates on ``module.basis.unpinned``.
    """
    known: set[str] = set()
    for eff in editions:
        known |= _canonical_designation_forms(eff.name)
    unrecorded: list[str] = []
    for match in _DESIGNATION_RE.finditer(text):
        raw = match.group(0).strip()
        if _canonical_designation_forms(raw) & known:
            continue
        if raw not in unrecorded:
            unrecorded.append(raw)
    seen_spans: list[tuple[int, int]] = []
    for raw in unrecorded:
        for pattern in _edition_patterns_for(raw):
            for match in pattern.finditer(text):
                year = match.group(1)
                span = (match.start(), match.end())
                if any(s <= span[0] and span[1] <= e for s, e in seen_spans):
                    continue
                seen_spans.append(span)
                if _suppressed(text, *span):
                    continue
                yield {
                    "match": match.group(0),
                    "message": (
                        f"{raw} cited at {year} but no edition is recorded "
                        "for it — record the edition and its basis with a "
                        "set_standard_edition operation (or drop the year "
                        "until one is established)."
                    ),
                }


def _scan_markers(
    text: str,
    patterns: Iterable[tuple[str, str]],
) -> Iterable[dict[str, str]]:
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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def lint_document(
    section: SpecSection,
    module: Any,
    overrides: Mapping[str, Mapping[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Lint the tree against ``module``; returns advisory issue dicts.

    ``overrides`` defaults to the section's own recorded
    ``edition_overrides``. Issue shape: ``{id, rule, severity, element_id,
    ref, message, match}`` — ids are stable for a given tree (rule +
    element + occurrence index), so the frontend can key on them.
    """
    if overrides is None:
        overrides = getattr(section, "edition_overrides", {}) or {}
    editions = effective_editions(module.basis, overrides)
    extra_markers = tuple(
        (src, "Template marker")
        for src in getattr(module, "lint_extra_marker_patterns", ())
    )

    issues: list[dict[str, Any]] = []
    counters: dict[tuple[str, str], int] = {}

    def add(
        rule: str,
        element_id: str,
        ref: str,
        message: str,
        match: str = "",
        severity: str = "warn",
    ) -> None:
        n = counters.get((rule, element_id), 0)
        counters[(rule, element_id)] = n + 1
        issues.append(
            {
                "id": f"{rule}:{element_id}:{n}",
                "rule": rule,
                "severity": severity,
                "element_id": element_id,
                "ref": ref,
                "message": message,
                "match": match,
            }
        )

    # The unrecorded-edition rule exists only for an unpinned basis (the
    # generic module) — a pinned module's lint output is unchanged by
    # construction (the scan below is never reached).
    unpinned = bool(getattr(module.basis, "unpinned", False))

    # --- per-paragraph text scans -----------------------------------------
    for _part, _article, paragraph, _depth, ref in iter_paragraphs(section):
        text = paragraph.text
        for hit in _scan_editions(text, editions):
            add(
                RULE_STALE_EDITION,
                paragraph.uid,
                ref,
                hit["message"],
                hit["match"],
            )
        if unpinned:
            for hit in _scan_unrecorded_editions(text, editions):
                add(
                    RULE_UNRECORDED_EDITION,
                    paragraph.uid,
                    ref,
                    hit["message"],
                    hit["match"],
                )
        for hit in _scan_markers(text, _PLACEHOLDER_PATTERNS):
            add(
                RULE_PLACEHOLDER,
                paragraph.uid,
                ref,
                f"Unresolved {hit['label'].lower()} — resolve or convert to "
                "a tracked [TBD: ...].",
                hit["match"],
            )
        for hit in _scan_markers(
            text, tuple(_TEMPLATE_MARKER_PATTERNS) + extra_markers
        ):
            add(
                RULE_TEMPLATE_MARKER,
                paragraph.uid,
                ref,
                f"{hit['label']} left in the draft.",
                hit["match"],
            )

    # --- structural checks -------------------------------------------------
    any_articles = False
    for part in section.parts:
        titles_seen: dict[str, str] = {}
        for a_idx, article in enumerate(part.articles):
            any_articles = True
            number = f"{part.number}.{a_idx + 1}"
            if not article.paragraphs:
                add(
                    RULE_EMPTY_ARTICLE,
                    article.uid,
                    number,
                    f"Article {number} {article.title} has no paragraphs.",
                )
            key = " ".join(article.title.split()).upper()
            if key and key in titles_seen:
                add(
                    RULE_DUPLICATE_ARTICLE_TITLE,
                    article.uid,
                    number,
                    f"Article title '{article.title}' duplicates "
                    f"{titles_seen[key]} in the same part.",
                )
            elif key:
                titles_seen[key] = number

    if any_articles and (not section.number or not section.title):
        add(
            RULE_MISSING_SECTION_HEADER,
            "sec",
            "—",
            "Section number/title not set while articles are being drafted.",
            severity="info",
        )

    return issues
