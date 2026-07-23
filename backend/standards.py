"""Pinned standards editions and the code basis behind a spec module.

Ported from Claude-Spec-Critic ``src/core/code_cycles.py`` (the
``StandardEdition`` / ``BaseCode`` / ``CodeCycle`` machinery), adapted for
authoring and with the California-cycle wiring dropped. Two changes of
purpose from the review-side original:

- **Editions here are drafting defaults**, not adoption records. The module
  pins the *current published edition* of each standard (NFPA 13-2025 first
  among them); a jurisdiction's adopted earlier edition **overrides** a pin
  once the user (or, in a later phase, grounded research) states it — never
  silently, always with the adoption basis recorded alongside.
- ``StandardEdition`` gains a ``title`` field so the PART 1 REFERENCES
  article can be drafted verbatim from the pin list (designation, full
  title, edition).

Maintenance conventions (same as Spec Critic): every pin carries a
``source`` documenting where the edition was confirmed; entries whose
source begins with ``UNVERIFIED`` have not been checked against a published
listing and must be verified before they are relied on. Receipts live in
``docs/standards_provenance.md``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping


def normalize_standard_name(name: str) -> str:
    """Canonical form used to key edition overrides: collapsed spaces, upper.

    ``"nfpa  13"`` → ``"NFPA 13"``. Standards designations are
    acronym-styled, so uppercasing is lossless in practice; matching between
    pins and overrides is case-insensitive as a result.
    """
    return re.sub(r"\s+", " ", (name or "").strip()).upper()


@dataclass(frozen=True)
class StandardEdition:
    """One referenced standard pinned at a drafting-default edition.

    Attributes:
        name: The designation, e.g. ``"NFPA 13"``.
        edition: The pinned edition (the current published edition unless
            noted), e.g. ``"2025"``.
        title: Full standard title for REFERENCES-article rendering, e.g.
            ``"Standard for the Installation of Sprinkler Systems"``.
        note: Short descriptor rendered to the model when present.
        source: Maintainer provenance — where the edition was confirmed.
            NOT rendered into any prompt. Prefix with ``UNVERIFIED`` when
            the edition has not been checked against a published listing.
    """

    name: str
    edition: str
    title: str = ""
    note: str = ""
    source: str = ""

    @property
    def edition_phrase(self) -> str:
        """``"2025"`` or ``"2025 (note)"`` — the edition without the name."""
        if self.note:
            return f"{self.edition} ({self.note})"
        return self.edition

    @property
    def description(self) -> str:
        """One-line description, e.g. ``"NFPA 13 2025"``."""
        return f"{self.name} {self.edition_phrase}"

    @property
    def reference_line(self) -> str:
        """REFERENCES-article rendering: designation, title, edition."""
        if self.title:
            return f"{self.name} - {self.title} ({self.edition} edition)"
        return f"{self.name} ({self.edition} edition)"

    @property
    def is_verified(self) -> bool:
        """True when ``source`` documents a confirmed edition."""
        return bool(self.source) and not self.source.upper().startswith(
            "UNVERIFIED"
        )


@dataclass(frozen=True)
class BaseCode:
    """One model/base code giving the module its code context.

    Attributes:
        key: Stable template-placeholder id (``"ibc"``) usable in module
            prompt templates as ``{ibc}``.
        name: Display designation (``"IBC"``).
        year: Edition year in the pinned basis (``"2024"``).
        source: Maintainer provenance, never rendered into a prompt.
    """

    key: str
    name: str
    year: str
    source: str = ""


@dataclass(frozen=True)
class StandardsBasis:
    """A module's code basis: base model codes plus pinned standard editions.

    ``label`` is registry-unique across modules (validated at import in
    ``spec_modules.registry``). All collections are tuples so the frozen
    dataclass stays hashable.

    ``unpinned=True`` marks a deliberately pinless basis (the generic
    any-discipline module): the module ships no default editions, and every
    edition in effect enters as a per-project override recorded through
    ``set_standard_edition`` with a stated basis. Registration enforces the
    coherence matrix — an unpinned basis must pin nothing; a pinned basis
    must pin at least one base code and one standard.
    """

    label: str
    base_codes: tuple[BaseCode, ...] = ()
    standards: tuple[StandardEdition, ...] = field(default_factory=tuple)
    unpinned: bool = False

    def code_year(self, key: str) -> str:
        for code in self.base_codes:
            if code.key == key:
                return code.year
        return ""

    def standard(self, name: str) -> StandardEdition | None:
        """Return the pinned edition for ``name`` (case-insensitive) or None."""
        canonical = normalize_standard_name(name)
        for std in self.standards:
            if normalize_standard_name(std.name) == canonical:
                return std
        return None

    def unverified_standards(self) -> tuple[StandardEdition, ...]:
        return tuple(std for std in self.standards if not std.is_verified)

    def format_kwargs(self) -> dict[str, str]:
        """Placeholders module templates may reference.

        One per base code (keyed by ``BaseCode.key``) plus
        ``pinned_standards`` (the inline description phrase).
        """
        kwargs = {code.key: code.year for code in self.base_codes}
        kwargs["pinned_standards"] = (
            ", ".join(std.description for std in self.standards if std.edition)
            or "current editions"
        )
        return kwargs


# ---------------------------------------------------------------------------
# Effective editions: module pins + jurisdiction overrides
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EffectiveEdition:
    """One standard's edition in effect for the session.

    Either a module default (``is_override=False``, empty ``basis``) or a
    jurisdiction-adopted override recorded through ``set_standard_edition``
    (``basis`` carries the stated adoption, e.g. ``"2021 VCC / Loudoun
    County amendments"``).
    """

    name: str
    edition: str
    title: str = ""
    note: str = ""
    is_override: bool = False
    basis: str = ""

    @property
    def reference_line(self) -> str:
        if self.title:
            return f"{self.name} - {self.title} ({self.edition} edition)"
        return f"{self.name} ({self.edition} edition)"


def effective_editions(
    basis: StandardsBasis, overrides: Mapping[str, Mapping[str, str]] | None
) -> tuple[EffectiveEdition, ...]:
    """Merge module pins with recorded jurisdiction overrides.

    ``overrides`` maps a canonical standard name (see
    :func:`normalize_standard_name`) to ``{"edition": ..., "basis": ...}``
    — the shape stored on ``SpecSection.edition_overrides``. An override
    for a pinned standard replaces its edition; an override naming a
    standard the module does not pin appends a new entry (jurisdictions can
    invoke standards the module didn't anticipate). Pins keep declaration
    order; unpinned overrides follow in sorted-name order.
    """
    overrides = dict(overrides or {})
    result: list[EffectiveEdition] = []
    consumed: set[str] = set()
    for std in basis.standards:
        canonical = normalize_standard_name(std.name)
        override = overrides.get(canonical)
        if override:
            consumed.add(canonical)
            result.append(
                EffectiveEdition(
                    name=std.name,
                    edition=str(override.get("edition", "")),
                    title=std.title,
                    note=std.note,
                    is_override=True,
                    basis=str(override.get("basis", "")),
                )
            )
        else:
            result.append(
                EffectiveEdition(
                    name=std.name,
                    edition=std.edition,
                    title=std.title,
                    note=std.note,
                )
            )
    for canonical in sorted(set(overrides) - consumed):
        override = overrides[canonical]
        result.append(
            EffectiveEdition(
                name=canonical,
                edition=str(override.get("edition", "")),
                is_override=True,
                basis=str(override.get("basis", "")),
            )
        )
    return tuple(result)


def standards_context_block(
    basis: StandardsBasis, overrides: Mapping[str, Mapping[str, str]] | None
) -> str:
    """The dynamic system-prompt block naming the editions in effect.

    Lives OUTSIDE the cached prompt prefix (overrides can change any turn).
    One line per standard; overrides carry their stated adoption basis so
    the model can cite it — a jurisdiction edition is never in effect
    silently.

    An **unpinned** basis renders its own posture: the module pins no
    default editions, so the block lists only recorded overrides and states
    the mandatory-basis rule (every edition enters via
    ``set_standard_edition`` with a stated basis; designations are cited
    year-free until then).
    """
    if basis.unpinned:
        lines = [
            "Standards editions in effect for this project (this module "
            "pins NO default editions — every edition is recorded "
            "per-project):"
        ]
        editions = effective_editions(basis, overrides)
        if not editions:
            lines.append("- (none recorded yet)")
        for eff in editions:
            line = f"- {eff.name}: {eff.edition}"
            if eff.note:
                line += f" ({eff.note})"
            line += f" — recorded (basis: {eff.basis})"
            lines.append(line)
        lines.append(
            "This module ships no pinned standards. Before citing any "
            "standard at a specific edition in document text, record that "
            "edition with a set_standard_edition operation carrying a "
            "stated basis — a grounded research item id, the user's "
            "statement, or an honestly-labeled model proposal (e.g. "
            '"model-proposed, unverified — current published edition per '
            'training data"). Until an edition is recorded, cite the '
            "designation without an edition year. Draft the PART 1 "
            "REFERENCES article from the recorded editions."
        )
        return "\n".join(lines)
    lines = ["Standards editions in effect for this project:"]
    for eff in effective_editions(basis, overrides):
        line = f"- {eff.name}: {eff.edition}"
        if eff.note:
            line += f" ({eff.note})"
        if eff.is_override:
            line += f" — jurisdiction-adopted override (basis: {eff.basis})"
        else:
            line += " — module default (current published edition)"
        lines.append(line)
    lines.append(
        "Draft the PART 1 REFERENCES article from these editions. If the "
        "user states a jurisdiction-adopted edition that differs, record it "
        "with a set_standard_edition operation (adoption basis required) "
        "before drafting to it."
    )
    return "\n".join(lines)


def references_article_lines(
    basis: StandardsBasis,
    overrides: Mapping[str, Mapping[str, str]] | None,
    names: Iterable[str] | None = None,
) -> list[str]:
    """REFERENCES-article data: one rendered line per standard in effect.

    ``names`` optionally restricts (and orders) the output to specific
    designations — a wet-pipe section cites a narrower set than the module
    pins. Unknown names are skipped rather than invented.
    """
    editions = effective_editions(basis, overrides)
    if names is None:
        return [eff.reference_line for eff in editions]
    by_name: dict[str, EffectiveEdition] = {
        normalize_standard_name(eff.name): eff for eff in editions
    }
    result = []
    for name in names:
        eff = by_name.get(normalize_standard_name(name))
        if eff is not None:
            result.append(eff.reference_line)
    return result


def validate_overrides_shape(overrides: Any) -> dict[str, dict[str, str]]:
    """Validate/normalize a persisted ``edition_overrides`` mapping.

    Used by document deserialization (project files are untrusted). Raises
    ``ValueError`` on anything malformed; returns a clean
    ``{canonical_name: {"edition": ..., "basis": ...}}`` dict.
    """
    if overrides in (None, {}):
        return {}
    if not isinstance(overrides, dict):
        raise ValueError("edition_overrides must be an object")
    clean: dict[str, dict[str, str]] = {}
    for name, value in overrides.items():
        canonical = normalize_standard_name(str(name))
        if not canonical:
            raise ValueError("edition_overrides key must be a standard name")
        if not isinstance(value, dict):
            raise ValueError(f"edition_overrides[{name!r}] must be an object")
        edition = str(value.get("edition", "")).strip()
        basis = str(value.get("basis", "")).strip()
        if not edition or not basis:
            raise ValueError(
                f"edition_overrides[{name!r}] needs a non-empty edition and "
                "basis (adoptions are never recorded silently)"
            )
        clean[canonical] = {"edition": edition, "basis": basis}
    return clean
