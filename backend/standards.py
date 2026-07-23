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
    """

    label: str
    base_codes: tuple[BaseCode, ...] = ()
    standards: tuple[StandardEdition, ...] = field(default_factory=tuple)

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
    County amendments"``). ``is_added`` marks an override naming a standard
    the module does not pin — a user-added standard, rendered as "added for
    this project" rather than a jurisdiction adoption.
    """

    name: str
    edition: str
    title: str = ""
    note: str = ""
    is_override: bool = False
    basis: str = ""
    is_added: bool = False

    @property
    def reference_line(self) -> str:
        if self.title:
            return f"{self.name} - {self.title} ({self.edition} edition)"
        return f"{self.name} ({self.edition} edition)"


def effective_editions(
    basis: StandardsBasis,
    overrides: Mapping[str, Mapping[str, str]] | None,
    suppressed: Mapping[str, str] | None = None,
) -> tuple[EffectiveEdition, ...]:
    """Merge module pins with recorded jurisdiction overrides.

    ``overrides`` maps a canonical standard name (see
    :func:`normalize_standard_name`) to ``{"edition": ..., "basis": ...}``
    (optionally ``"title"`` for a user-added standard) — the shape stored on
    ``SpecSection.edition_overrides``. An override for a pinned standard
    replaces its edition; an override naming a standard the module does not
    pin appends a new entry marked ``is_added`` (jurisdictions — or the user
    — can invoke standards the module didn't anticipate). Pins keep
    declaration order; added standards follow in sorted-name order.

    ``suppressed`` maps a canonical name to a reason; a suppressed name is
    dropped entirely (a pinned standard the project excludes, or an added
    one). Suppression is checked **before** the override, so it wins and is
    non-destructive: a dormant override on a suppressed pin returns intact
    when the suppression is removed. Everything returned is in effect —
    suppressed entries are simply absent.
    """
    overrides = dict(overrides or {})
    suppressed_set = {
        normalize_standard_name(name) for name in (suppressed or {})
    }
    result: list[EffectiveEdition] = []
    consumed: set[str] = set()
    for std in basis.standards:
        canonical = normalize_standard_name(std.name)
        if canonical in suppressed_set:
            continue
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
    for canonical in sorted(set(overrides) - consumed - suppressed_set):
        override = overrides[canonical]
        result.append(
            EffectiveEdition(
                name=canonical,
                edition=str(override.get("edition", "")),
                title=str(override.get("title", "")),
                is_override=True,
                basis=str(override.get("basis", "")),
                is_added=True,
            )
        )
    return tuple(result)


def standards_context_block(
    basis: StandardsBasis,
    overrides: Mapping[str, Mapping[str, str]] | None,
    suppressed: Mapping[str, str] | None = None,
) -> str:
    """The dynamic system-prompt block naming the editions in effect.

    Lives OUTSIDE the cached prompt prefix (overrides can change any turn).
    One line per standard; overrides carry their stated adoption basis so
    the model can cite it — a jurisdiction edition is never in effect
    silently. A standard the user added for this project is labelled as
    such; standards the project intentionally excludes are listed at the end
    so the model does not reintroduce them into REFERENCES.
    """
    lines = ["Standards editions in effect for this project:"]
    for eff in effective_editions(basis, overrides, suppressed):
        line = f"- {eff.name}: {eff.edition}"
        if eff.note:
            line += f" ({eff.note})"
        if eff.is_added:
            line += f" — added for this project (basis: {eff.basis})"
        elif eff.is_override:
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
    suppressed_map = dict(suppressed or {})
    if suppressed_map:
        excluded = "; ".join(
            f"{name}{f' — {reason}' if reason else ''}"
            for name, reason in sorted(suppressed_map.items())
        )
        lines.append(
            "Intentionally excluded from this project (do not reintroduce "
            f"into REFERENCES): {excluded}."
        )
    return "\n".join(lines)


def references_article_lines(
    basis: StandardsBasis,
    overrides: Mapping[str, Mapping[str, str]] | None,
    names: Iterable[str] | None = None,
    suppressed: Mapping[str, str] | None = None,
) -> list[str]:
    """REFERENCES-article data: one rendered line per standard in effect.

    ``names`` optionally restricts (and orders) the output to specific
    designations — a wet-pipe section cites a narrower set than the module
    pins. Unknown names are skipped rather than invented. ``suppressed``
    excludes standards the project has dropped.
    """
    editions = effective_editions(basis, overrides, suppressed)
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
        entry = {"edition": edition, "basis": basis}
        # Optional full title for a user-added standard (pins carry their own
        # title; overrides on pins inherit it, so it is stored only when set).
        title = str(value.get("title", "")).strip()
        if title:
            entry["title"] = title
        clean[canonical] = entry
    return clean


def validate_suppressed_shape(suppressed: Any) -> dict[str, str]:
    """Validate/normalize a persisted ``suppressed_standards`` mapping.

    Maps a canonical standard name (see :func:`normalize_standard_name`) to
    a reason. Suppressing a standard is a scope decision — it does not apply
    to this project, so it drops out of the editions in effect (module pins
    included). Unlike an edition override, the reason is **optional**:
    excluding a standard is a one-click call, so an empty reason is allowed.
    Used by document deserialization (project files are untrusted); raises
    ``ValueError`` on anything malformed; returns a clean
    ``{canonical_name: reason}`` dict.
    """
    if suppressed in (None, {}):
        return {}
    if not isinstance(suppressed, dict):
        raise ValueError("suppressed_standards must be an object")
    clean: dict[str, str] = {}
    for name, value in suppressed.items():
        canonical = normalize_standard_name(str(name))
        if not canonical:
            raise ValueError("suppressed_standards key must be a standard name")
        if not isinstance(value, str):
            raise ValueError(
                f"suppressed_standards[{name!r}] reason must be a string"
            )
        clean[canonical] = value.strip()
    return clean
