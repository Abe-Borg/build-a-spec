"""Project briefs: carry a project's paid knowledge into its next section.

A project has many spec sections and each one is its own session. A project
brief (``.basproject``) is the structured, deliberately partial handoff from
one section to the next: the project profile and type, the edition overrides
the jurisdiction adopted (basis verbatim), the whole requirements-research
profile (every round — the next section's first Research press becomes a
briefed round through ``established_facts_for``), the attached reference
documents, the established project facts, and a registry of the sections
drafted so far. It carries NEITHER the conversation NOR the document: those
are bloat, and stale, and the owner's constraint was that they must not
travel.

Why a file and not a summary
----------------------------
Every asset above already has a serialization boundary and a bloat-safe way
of reaching the model (a capped research block, reference stubs read on
demand, a capped facts block). A model-written summary of the previous
session would be an unverifiable paragraph the next session treats as fact.
The brief is a structured record with provenance, so what is carried is
exactly what was recorded, and nothing the model made up on the way out.

Building is a pure read; seeding is one transaction
--------------------------------------------------
:func:`build_project_brief` reads a session under the caller's guard and
touches nothing. :meth:`SessionState.start_from_brief` (in
``llm/conversation.py``, beside ``start_from_template``) is the seed: reset,
then install every carried asset under one lock acquisition, so a stale
turn can never observe a half-seeded session.

The link
--------
A seeded session (or one that exported a brief) carries ``project_link`` —
the project id and name, the sections the brief listed, and how many
research rounds arrived with the seed. It is a sanitized provenance marker
like ``template_origin``, persisted in the ``.baspec`` and cleared by a
reset; it is what the PROJECT SECTIONS context block and the carried-research
readiness disclosure read. It is never a source of authority.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Iterable

from . import settings
from .project_facts import (
    FactsMergeRefused,
    ProjectFact,
    merge_facts,
)
from .project_profile import ProjectProfile
from .reference_docs import (
    MAX_REFERENCE_DOCS,
    MAX_REFERENCE_TOKENS,
    ReferenceDoc,
    ReferenceDocError,
    rebound_reference_doc,
)
from .research.engine import RequirementsProfile, merge_research_profiles
from .spec_doc.project import (
    MAX_LINK_SECTIONS,
    sanitize_project_link,
    sanitize_section_record,
)
from .spec_modules.registry import AVAILABLE_MODULES
from .standards import validate_overrides_shape

PROJECT_BRIEF_KIND = "buildaspec-project-brief"
PROJECT_BRIEF_FORMAT = 1
PROJECT_BRIEF_EXTENSION = ".basproject"
PROJECT_BRIEF_MEDIA_TYPE = "application/vnd.buildaspec.project-brief+json"
# The templates precedent: a brief is JSON, and 16 MiB is far past anything
# a real project produces (the reference-document text is the bulk, and that
# is bounded by MAX_REFERENCE_TOKENS).
MAX_PROJECT_BRIEF_BYTES = 16 * 1024 * 1024
# Estimated tokens (len // 4) the PROJECT SECTIONS block may spend per turn.
SECTIONS_CONTEXT_MAX_TOKENS = 3_000

_BRIEF_KEYS = {
    "kind",
    "format",
    "project_id",
    "name",
    "created_at",
    "updated_at",
    "app_version",
    "profile",
    "project_type",
    "edition_overrides",
    "research_profile",
    "reference_docs",
    "facts",
    "sections",
}
_PROJECT_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class ProjectBriefError(ValueError):
    """A file that is not a usable project brief. Reported to the user as a 400."""


class ProjectBriefTooLargeError(ProjectBriefError):
    """A brief past :data:`MAX_PROJECT_BRIEF_BYTES`. Still a ``ProjectBriefError``
    (every parse-side ``except`` keeps working) but the routes answer 413."""


class ProjectBriefMismatchError(ProjectBriefError):
    """Two briefs of different projects (Project workspace Phase 3). Never
    merged: the caller decides — the shell asks before it replaces the other
    project's file, and the save-time refresh reports and writes nothing."""


class ProjectBriefMergeRefused(ProjectBriefError):
    """A merge that cannot be written without deleting something or breaking
    an identity (past the active-fact cap, a duplicate reference id). Carries
    the partial report; the caller reports it and never writes."""

    def __init__(self, message: str, report: "MergeReport | None" = None) -> None:
        super().__init__(message)
        self.report = report


def _now() -> str:
    """A brief's and a registry record's timestamp.

    Microseconds since Project workspace Phase 3: ``updated_at`` is how a
    section recognises the exact brief it last agreed with
    (``project_link.brief_updated_at``), and at second resolution a brief
    rewritten within the same second as a sync read as unchanged. The
    ISO-8601 form still sorts chronologically against a seconds-only stamp
    an older build wrote ("…:14+00:00" < "…:14.5+00:00" < "…:15+00:00").
    """
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _one_line(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def _fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class ProjectBrief:
    """The parsed / built brief. ``warnings`` is in-memory only."""

    project_id: str
    name: str
    created_at: str
    updated_at: str
    app_version: str
    profile: dict[str, str] = field(default_factory=dict)
    project_type: str = ""
    edition_overrides: dict[str, dict[str, str]] = field(default_factory=dict)
    research_profile: dict[str, Any] | None = None
    reference_docs: list[dict[str, Any]] = field(default_factory=list)
    facts: list[dict[str, Any]] = field(default_factory=list)
    sections: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": PROJECT_BRIEF_KIND,
            "format": PROJECT_BRIEF_FORMAT,
            "project_id": self.project_id,
            "name": self.name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "app_version": self.app_version,
            "profile": dict(self.profile),
            "project_type": self.project_type,
            "edition_overrides": {
                name: dict(entry) for name, entry in self.edition_overrides.items()
            },
            "research_profile": self.research_profile,
            "reference_docs": [dict(doc) for doc in self.reference_docs],
            "facts": [dict(fact) for fact in self.facts],
            "sections": [dict(record) for record in self.sections],
        }

    @property
    def newest_section(self) -> dict[str, Any] | None:
        """The section record that exported this brief last.

        The registry is kept in EXPORT order: ``build_project_brief`` removes a
        section's earlier record and appends the fresh one, so a re-export of
        section 1 after section 2 makes section 1 the newest again — which is
        what the seed's module/discipline defaults and the manifest read from
        here mean by "newest".
        """
        return self.sections[-1] if self.sections else None


# ---------------------------------------------------------------------------
# Building from a session (pure read)
# ---------------------------------------------------------------------------


def _article_titles(doc: Any) -> list[str]:
    titles: list[str] = []
    for part in getattr(doc, "parts", []) or []:
        for article in getattr(part, "articles", []) or []:
            title = " ".join(str(getattr(article, "title", "") or "").split())
            if title:
                titles.append(title[:160])
    return titles


def section_record(session: Any, *, ready: bool, exported_at: str) -> dict[str, Any]:
    """This session's entry in the brief's section registry."""
    doc = session.doc.doc
    number = " ".join((doc.number or "").split())
    identity = getattr(doc, "project_identity", {}) or {}
    profile = session.research.profile_result
    rounds = list(getattr(profile, "rounds", []) or []) if profile is not None else []
    # The rounds THIS section ran are the ones appended since it was seeded:
    # a seeded session's link records how many rounds it inherited, rounds
    # only ever append (a failed or stopped round is never adopted), and a
    # session never seeded inherited none. Counting by the per-round section
    # stamp instead would misattribute in both directions — a seeded section
    # that has not researched yet would claim the carried rounds through the
    # legacy "no stamp matches, count them all" fallback, and a section
    # renumbered mid-project would disown the rounds it ran under its old
    # number. Pre-1.17 rounds carry no stamp at all, and this needs none.
    link = session.project_link if isinstance(session.project_link, dict) else None
    carried = int((link or {}).get("research_rounds_at_seed", 0) or 0)
    own_round_count = max(0, len(rounds) - max(0, carried))
    record = {
        "number": number or "(unnumbered)",
        "title": " ".join((doc.title or "").split())[:160],
        "module_id": session.module.module_id,
        "discipline": " ".join(str(identity.get("discipline", "") or "").split())[:80],
        "article_titles": _article_titles(doc),
        "ready": bool(ready),
        "exported_at": exported_at,
        "file_name": os.path.basename(getattr(session, "save_target", "") or ""),
        "fact_count": sum(
            1 for f in session.facts.active() if f.recorded_in == number
        ),
        "research_rounds": own_round_count,
    }
    return sanitize_section_record(record) or record


def _brief_name(profile: dict[str, str], project_type: str, fallback: str) -> str:
    parsed = ProjectProfile.from_dict(profile)
    parts: list[str] = []
    if parsed is not None and parsed.client_name:
        parts.append(parsed.client_name)
    if project_type:
        parts.append(project_type)
    if parsed is not None and (parsed.city or parsed.state_or_province):
        place = ", ".join(p for p in (parsed.city, parsed.state_display) if p)
        parts.append(place)
    return " · ".join(parts)[:160] or fallback[:160] or "Untitled project"


def build_project_brief(session: Any, *, ready: bool) -> ProjectBrief:
    """Everything project-level this session holds, as a brief. Pure read.

    The caller holds ``session_state_guard()`` so the snapshot is coherent;
    nothing here writes. A session already linked to a project keeps that
    project's id and folds the link's section registry in, upserting its own
    record by number — REMOVED from its old position and appended, so the
    registry stays in export order and ``newest_section`` (the last entry) is
    always the section that exported last. Replacing it in place would leave
    a stale section "newest" whenever an earlier section re-exports.
    """
    now = _now()
    doc = session.doc.doc
    link = session.project_link if isinstance(session.project_link, dict) else None
    project_id = (link or {}).get("project_id") or uuid.uuid4().hex
    profile_dict = dict(getattr(doc, "project_profile", {}) or {})
    identity = getattr(doc, "project_identity", {}) or {}
    project_type = " ".join(str(identity.get("project_type", "") or "").split())[:120]

    research = session.research.profile_result
    research_dict = research.to_dict() if research is not None else None

    references: list[dict[str, Any]] = []
    for ref in session.references.docs:
        entry = ref.to_dict()
        entry["content_fingerprint"] = _fingerprint(ref.text)
        references.append(entry)

    own = section_record(session, ready=ready, exported_at=now)
    sections = [
        dict(s)
        for s in (link or {}).get("sections", []) or []
        if s.get("number") != own["number"]
    ]
    sections.append(own)
    sections = sections[-MAX_LINK_SECTIONS:]

    created = min(
        (s.get("exported_at") for s in sections if s.get("exported_at")),
        default=now,
    )
    fallback_name = " ".join(
        p for p in (doc.number or "", doc.title or "") if p
    ).strip()
    return ProjectBrief(
        project_id=project_id,
        name=(link or {}).get("name") or _brief_name(profile_dict, project_type, fallback_name),
        created_at=created,
        updated_at=now,
        app_version=settings.VERSION,
        profile=profile_dict,
        project_type=project_type,
        edition_overrides={
            name: dict(entry)
            for name, entry in (getattr(doc, "edition_overrides", {}) or {}).items()
        },
        research_profile=research_dict,
        reference_docs=references,
        facts=session.facts.snapshot(),
        sections=sections,
    )


# ---------------------------------------------------------------------------
# The Next-section dialog (v1.20.0): what the project has drafted, and what
# the module still offers
# ---------------------------------------------------------------------------

# Bounds for a user-typed section header on the Next-section path. The
# registry sanitizer (spec_doc.project.sanitize_section_record) bounds the
# same two fields at the same widths, so a section named here is a section
# the registry can record.
MAX_NEXT_SECTION_NUMBER_CHARS = 40
MAX_NEXT_SECTION_TITLE_CHARS = 160


def _fold(value: Any) -> str:
    return " ".join(str(value or "").split())


def sections_drafted(session: Any) -> list[str]:
    """Section numbers this project already has, in registry order with the
    open section last: the link's registry (what the brief listed, plus
    anything a later export upserted) and this session's own number when it
    has one. Deduplicated, whitespace-folded, never ``"(unnumbered)"`` — an
    unnumbered section is not a section the catalog can exclude."""
    link = session.project_link if isinstance(session.project_link, dict) else {}
    numbers: list[str] = []
    for record in link.get("sections", []) or []:
        number = _fold((record or {}).get("number"))
        if number and number != "(unnumbered)" and number not in numbers:
            numbers.append(number)
    own = _fold(getattr(session.doc.doc, "number", ""))
    if own and own not in numbers:
        numbers.append(own)
    return numbers


def next_section_catalog(module: Any, done_numbers: Iterable[str]) -> list[dict[str, Any]]:
    """The module's sibling catalog as the dialog lists it: every declared
    section, in declaration order, flagged ``done`` when the project already
    drafted it. Flagged rather than filtered so the dialog can SHOW what is
    done — a list that silently omits 21 13 13 reads as a module that never
    offered it. An open-catalog module (generic) declares nothing and gets
    ``[]``; the dialog then offers the typed header alone."""
    done = {_fold(n) for n in done_numbers}
    return [
        {
            "number": entry.number,
            "title": entry.title,
            "scope_note": entry.scope_note,
            "done": _fold(entry.number) in done,
        }
        for entry in getattr(module, "section_catalog", ()) or ()
    ]


def clean_next_section_header(number: Any, title: Any) -> tuple[str, str]:
    """Whitespace-fold and bound a typed section header; ``ProjectBriefError``
    past the bounds, so the route answers 400 in the brief's own words."""
    folded_number = _fold(number)
    folded_title = _fold(title)
    if len(folded_number) > MAX_NEXT_SECTION_NUMBER_CHARS:
        raise ProjectBriefError(
            f"The section number is too long ({len(folded_number)} > "
            f"{MAX_NEXT_SECTION_NUMBER_CHARS} characters)."
        )
    if len(folded_title) > MAX_NEXT_SECTION_TITLE_CHARS:
        raise ProjectBriefError(
            f"The section title is too long ({len(folded_title)} > "
            f"{MAX_NEXT_SECTION_TITLE_CHARS} characters)."
        )
    return folded_number, folded_title


# ---------------------------------------------------------------------------
# The project folder (Project workspace Phase 2): a brief read FROM DISK
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BriefOnDisk:
    """The identity and section registry of a ``.basproject`` read from disk.

    Only what folder discovery and the Project panel need: the project id
    (the join key), its name, when it was last written, and the registry.
    ``parse_project_brief`` would also rebuild the research profile and
    re-bound every reference body — megabytes of work per panel refresh for
    fields nothing here renders — so this reads the envelope and sanitizes
    the registry the way ``parse_project_brief`` does, and stops.
    """

    path: str
    project_id: str
    name: str
    updated_at: str
    sections: tuple[dict[str, Any], ...]


def read_brief_on_disk(path: str) -> BriefOnDisk | None:
    """``path`` as a project brief, or ``None`` — never raises.

    Refused, silently (a folder is shared data, and one bad file must never
    stop the rest of it being read): a symlink (the retention module's
    posture — links are never followed), anything that is not a regular
    file, a file past :data:`MAX_PROJECT_BRIEF_BYTES` (judged from ``stat``
    before a byte is read), anything that is not a brief of this format with
    a valid project id. A malformed section record is dropped rather than
    failing the brief, as ``parse_project_brief`` drops it.
    """
    try:
        if os.path.islink(path) or not os.path.isfile(path):
            return None
        if os.path.getsize(path) > MAX_PROJECT_BRIEF_BYTES:
            return None
        with open(path, "rb") as handle:
            data = handle.read(MAX_PROJECT_BRIEF_BYTES + 1)
        parsed = parse_brief_json(data)
    except (OSError, ValueError):
        # ProjectBriefError is a ValueError: a bad encoding, bad JSON, a
        # duplicate key, a non-finite number, or past the cap mid-read.
        return None
    if parsed.get("kind") != PROJECT_BRIEF_KIND:
        return None
    if parsed.get("format") != PROJECT_BRIEF_FORMAT:
        return None
    project_id = str(parsed.get("project_id", "") or "").strip()
    if not _PROJECT_ID_RE.fullmatch(project_id):
        return None
    raw_sections = parsed.get("sections") or []
    sections: list[dict[str, Any]] = []
    if isinstance(raw_sections, list):
        for entry in raw_sections[:MAX_LINK_SECTIONS]:
            record = sanitize_section_record(entry)
            if record is not None:
                sections.append(record)
    return BriefOnDisk(
        path=path,
        project_id=project_id,
        name=_one_line(parsed.get("name"), 160) or "Untitled project",
        updated_at=_one_line(parsed.get("updated_at"), 40),
        sections=tuple(sections),
    )


def merge_section_registries(
    primary: Iterable[dict[str, Any]],
    secondary: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Two section registries as one, joined by (whitespace-folded) number.

    ``primary`` is the session's link, ``secondary`` the brief on disk. Per
    number the record with the LATER ``exported_at`` wins — the stamps are
    ISO-8601 UTC strings written by one clock format, so they compare
    lexically — and a tie goes to ``primary``: the link is what this session
    last agreed with. A winner with no ``file_name`` borrows the other
    record's, so a section exported before it was ever saved still resolves
    to the file a later export named. Order is ``primary``'s, then the
    ``secondary``-only records in theirs. Pure: never mutates its inputs.
    """
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for source_rank, records in ((0, primary), (1, secondary)):
        for record in records or []:
            if not isinstance(record, dict):
                continue
            number = _fold(record.get("number"))
            if not number or number == "(unnumbered)":
                continue
            candidate = dict(record)
            existing = merged.get(number)
            if existing is None:
                merged[number] = candidate
                order.append(number)
                continue
            # Only a strictly later export from the secondary registry wins.
            later = str(candidate.get("exported_at") or "") > str(
                existing.get("exported_at") or ""
            )
            winner, other = (candidate, existing) if later and source_rank else (existing, candidate)
            if not winner.get("file_name") and other.get("file_name"):
                winner = {**winner, "file_name": other["file_name"]}
            merged[number] = winner
    return [merged[number] for number in order]


def brief_bytes(brief: ProjectBrief) -> bytes:
    payload = json.dumps(
        brief.to_dict(), ensure_ascii=False, indent=2, allow_nan=False
    ).encode("utf-8")
    if len(payload) > MAX_PROJECT_BRIEF_BYTES:
        raise ProjectBriefError(
            "The project brief exceeds the "
            f"{MAX_PROJECT_BRIEF_BYTES // (1024 * 1024)} MiB limit."
        )
    return payload


def brief_filename(brief: ProjectBrief) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", brief.name.lower()).strip("-")[:60] or "brief"
    return f"buildaspec-project-{slug}{PROJECT_BRIEF_EXTENSION}"


# ---------------------------------------------------------------------------
# Parsing (untrusted input)
# ---------------------------------------------------------------------------


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProjectBriefError(f"Duplicate JSON field {key!r}.")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ProjectBriefError(f"Non-finite JSON value {value!r} is not allowed.")


def parse_brief_json(data: bytes) -> dict[str, Any]:
    """Size-capped, duplicate-key- and NaN-rejecting JSON decode (the
    templates posture, copy-adapted because the messages differ)."""
    if len(data) > MAX_PROJECT_BRIEF_BYTES:
        raise ProjectBriefTooLargeError(
            "The project brief exceeds the "
            f"{MAX_PROJECT_BRIEF_BYTES // (1024 * 1024)} MiB limit."
        )
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProjectBriefError("A project brief must be UTF-8 JSON.") from exc
    try:
        parsed = json.loads(
            text, object_pairs_hook=_unique_object, parse_constant=_reject_constant
        )
    except ProjectBriefError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError, RecursionError) as exc:
        raise ProjectBriefError("The project brief is not valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise ProjectBriefError("The project brief's root must be a JSON object.")
    return parsed


def parse_project_brief(data: bytes) -> ProjectBrief:
    """Validate a ``.basproject`` upload into a :class:`ProjectBrief`.

    Wrong kind, format, or shape raises :class:`ProjectBriefError`. A field
    that can degrade — an unparseable research profile, a malformed
    reference document or fact — is dropped with a warning the manifest
    shows, because a brief with one bad attachment is still a brief.
    """
    parsed = parse_brief_json(data)
    if parsed.get("kind") != PROJECT_BRIEF_KIND:
        raise ProjectBriefError("Not a Build-a-Spec project brief.")
    if parsed.get("format") != PROJECT_BRIEF_FORMAT:
        raise ProjectBriefError(
            f"Unsupported project brief format {parsed.get('format')!r} "
            f"(this build reads format {PROJECT_BRIEF_FORMAT})."
        )
    unknown = set(parsed) - _BRIEF_KEYS
    if unknown:
        raise ProjectBriefError(
            f"The project brief has unknown field(s): {', '.join(sorted(unknown))}."
        )
    project_id = str(parsed.get("project_id", "") or "").strip()
    if not _PROJECT_ID_RE.fullmatch(project_id):
        raise ProjectBriefError("The project brief has no valid project id.")

    warnings: list[str] = []

    raw_profile = parsed.get("profile")
    profile = ProjectProfile.from_dict(raw_profile)
    if profile is None:
        profile_dict: dict[str, str] = {}
        if raw_profile not in (None, {}):
            warnings.append("The project profile could not be read and was dropped.")
    else:
        profile_dict = profile.to_dict()

    try:
        overrides = validate_overrides_shape(parsed.get("edition_overrides"))
    except ValueError as exc:
        raise ProjectBriefError(f"Malformed standards editions: {exc}") from exc

    research_raw = parsed.get("research_profile")
    research_dict: dict[str, Any] | None = None
    if research_raw is not None:
        restored = (
            RequirementsProfile.from_dict(research_raw)
            if isinstance(research_raw, dict)
            else None
        )
        if restored is None:
            warnings.append(
                "The research profile could not be read and was dropped; "
                "the new section starts without it."
            )
        else:
            research_dict = restored.to_dict()

    references: list[dict[str, Any]] = []
    raw_refs = parsed.get("reference_docs") or []
    if not isinstance(raw_refs, list):
        raise ProjectBriefError("Malformed reference documents.")
    for entry in raw_refs:
        if not isinstance(entry, dict):
            warnings.append("A malformed reference document entry was dropped.")
            continue
        payload = {k: v for k, v in entry.items() if k != "content_fingerprint"}
        try:
            doc = ReferenceDoc.from_dict(payload)
        except (KeyError, ValueError, TypeError):
            warnings.append("A malformed reference document entry was dropped.")
            continue
        # The fingerprint is checked against the text AS WRITTEN, before the
        # bounds are re-imposed: a legitimate export always matches, and a
        # body this app would have truncated can only have been edited in.
        claimed = entry.get("content_fingerprint")
        written_fingerprint = _fingerprint(doc.text)
        # A serialized record is a claim about text the store never prepared
        # (a hand-edited brief can carry a body past MAX_TEXT_CHARS under a
        # tiny token count, and the carry cap and both fan-out allocations
        # read the COUNT). Re-impose the bounds, and say so.
        try:
            warnings.extend(rebound_reference_doc(doc))
        except ReferenceDocError:
            warnings.append("A malformed reference document entry was dropped.")
            continue
        record = doc.to_dict()
        record["content_fingerprint"] = _fingerprint(doc.text)
        if isinstance(claimed, str) and claimed and claimed != written_fingerprint:
            warnings.append(
                f"Reference document {doc.title!r} does not match its recorded "
                "fingerprint; its text was edited after export."
            )
        if len(references) >= MAX_REFERENCE_DOCS:
            warnings.append(
                f"Reference document {doc.title!r} was dropped: the brief carries "
                f"more than {MAX_REFERENCE_DOCS} documents."
            )
            continue
        references.append(record)

    facts: list[dict[str, Any]] = []
    raw_facts = parsed.get("facts") or []
    if not isinstance(raw_facts, list):
        raise ProjectBriefError("Malformed project facts.")
    seen: set[str] = set()
    for entry in raw_facts:
        if not isinstance(entry, dict):
            warnings.append("A malformed project fact was dropped.")
            continue
        try:
            fact = ProjectFact.from_dict(entry)
        except (ValueError, KeyError, TypeError):
            warnings.append("A malformed project fact was dropped.")
            continue
        if fact.pid in seen:
            continue
        seen.add(fact.pid)
        facts.append(fact.to_dict())

    sections: list[dict[str, Any]] = []
    raw_sections = parsed.get("sections") or []
    if not isinstance(raw_sections, list):
        raise ProjectBriefError("Malformed section registry.")
    for entry in raw_sections[:MAX_LINK_SECTIONS]:
        record = sanitize_section_record(entry)
        if record is None:
            warnings.append("A malformed section record was dropped.")
            continue
        sections.append(record)

    return ProjectBrief(
        project_id=project_id,
        name=_one_line(parsed.get("name"), 160) or "Untitled project",
        created_at=_one_line(parsed.get("created_at"), 40),
        updated_at=_one_line(parsed.get("updated_at"), 40),
        app_version=_one_line(parsed.get("app_version"), 40),
        profile=profile_dict,
        project_type=_one_line(parsed.get("project_type"), 120),
        edition_overrides=overrides,
        research_profile=research_dict,
        reference_docs=references,
        facts=facts,
        sections=sections,
        warnings=warnings,
    )


def brief_from_sibling_project(data: bytes) -> ProjectBrief:
    """The ``.baspec`` shortcut: build a brief straight from a sibling section.

    The file is loaded into a THROWAWAY session (the ``_stage_project_load``
    posture — the live session is never touched) and the brief is built from
    that. ``ProjectPackageError`` / ``ValueError`` propagate for the route to
    map. The import is lazy because ``llm.conversation`` imports this module
    for the PROJECT SECTIONS block.
    """
    from .llm.conversation import SessionState
    from .spec_doc.project import load_project
    from .spec_doc.project_package import parse_project_file

    parsed = parse_project_file(data)
    staged = SessionState()
    load_project(parsed.project, staged)
    brief = build_project_brief(staged, ready=False)
    own = brief.newest_section or {}
    brief.warnings.append(
        f"Built from section {own.get('number') or '(unnumbered)'}'s project "
        "file rather than an exported brief; its readiness was not assessed."
    )
    return brief


# ---------------------------------------------------------------------------
# The write-back merge (Project workspace Phase 3): the brief is a living file
# ---------------------------------------------------------------------------
#
# A section seeded from a brief is a FORK: facts, rounds and references added
# in one section reach another only through the brief, and a brief export
# used to OVERWRITE the file — losing whatever another section had written
# into it since. ``merge_project_brief`` is the one join, used three ways
# (a save refreshing the brief beside it, an export onto an existing brief,
# and a section pulling what its siblings added): append-only, id-joined,
# idempotent. Nothing a merge touches is ever deleted — a round is replayed,
# a document is kept by its content, a fact is confirmed, retired with a
# reason, or folded as superseded into the one it duplicates.

# The project-profile fields a merge compares, with the words a report uses.
_PROFILE_FIELD_LABELS = {
    "city": "city",
    "state_or_province": "state or province",
    "country": "country",
    "client_name": "client",
}


@dataclass
class MergeReport:
    """What :func:`merge_project_brief` did — the routes serialize it.

    ``setup`` lists every project-setup difference found (profile fields, the
    project type, an edition recorded differently): each names both values
    and which side is kept. ``conflicts`` are the disagreements a person has
    to resolve (the D4 edition conflicts and the fact scope conflicts);
    ``warnings`` is everything else worth saying. ``changed`` says whether the
    merged brief differs from ``existing`` at all; ``assets_changed`` whether
    its research, references or facts do — what a pull would install.
    """

    research: dict[str, int] = field(default_factory=dict)
    references: dict[str, Any] = field(default_factory=dict)
    facts: dict[str, Any] = field(default_factory=dict)
    sections: dict[str, int] = field(default_factory=dict)
    setup: list[dict[str, str]] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    changed: bool = False
    assets_changed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "research": dict(self.research),
            "references": dict(self.references),
            "facts": dict(self.facts),
            "sections": dict(self.sections),
            "setup": [dict(entry) for entry in self.setup],
            "conflicts": list(self.conflicts),
            "warnings": list(self.warnings),
            "changed": self.changed,
            "assets_changed": self.assets_changed,
        }


def _reference_fingerprint(doc: dict[str, Any]) -> str:
    claimed = doc.get("content_fingerprint")
    if isinstance(claimed, str) and len(claimed) == 64:
        return claimed
    return _fingerprint(str(doc.get("text", "") or ""))


def _rid_tail(rid: Any) -> int | None:
    text = str(rid or "")
    if not text.startswith("ref-"):
        return None
    tail = text[4:]
    return int(tail) if tail.isdigit() else None


def _merge_reference_docs(
    existing: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
    *,
    mint_floor: int | None,
) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, Any]]:
    """Join two reference lists by CONTENT; returns ``(docs, rid_map, counts)``.

    A document already present (same ``content_fingerprint``) keeps its
    existing rid, and the incoming rid maps onto it. A new document keeps
    its own rid unless another document here already holds it — then it is
    re-minted past the highest rid — or, with ``mint_floor`` (a pull into a
    session's store), is always minted from the store's own counter so an id
    a deleted document once held is never handed to a different one. A
    duplicate rid on the existing side is a hand-edited file and refused:
    two documents answering to one id is exactly the state the re-minting
    exists to prevent. The session cap is applied by the caller.
    """
    # Either side: on a pull the brief file is the INCOMING side, and two of
    # its documents answering to one id would leave a fact that cites that
    # id pointing at whichever the rid map happened to see first.
    for side in (existing, incoming):
        rids = [str(doc.get("rid", "")) for doc in side]
        if len(set(rids)) != len(rids):
            raise ProjectBriefMergeRefused(
                "The project brief lists two reference documents under one id; "
                "it was edited by hand. Nothing was merged."
            )
    docs: list[dict[str, Any]] = []
    by_content: dict[str, str] = {}
    used: set[str] = set()
    highest = 0
    for doc in existing:
        record = dict(doc)
        record["content_fingerprint"] = _reference_fingerprint(doc)
        docs.append(record)
        by_content.setdefault(record["content_fingerprint"], str(record.get("rid", "")))
        used.add(str(record.get("rid", "")))
        tail = _rid_tail(record.get("rid"))
        if tail is not None:
            highest = max(highest, tail)
    next_tail = max(highest + 1, mint_floor or 1)
    rid_map: dict[str, str] = {}
    counts = {"added": 0, "already_present": 0, "re_minted": 0}
    for doc in incoming:
        own_rid = str(doc.get("rid", ""))
        fingerprint = _reference_fingerprint(doc)
        if fingerprint in by_content:
            rid_map.setdefault(own_rid, by_content[fingerprint])
            counts["already_present"] += 1
            continue
        rid = own_rid
        if mint_floor is not None or rid in used or _rid_tail(rid) is None:
            rid = f"ref-{next_tail}"
            next_tail += 1
        if rid != own_rid:
            counts["re_minted"] += 1
        used.add(rid)
        next_tail = max(next_tail, (_rid_tail(rid) or 0) + 1)
        rid_map.setdefault(own_rid, rid)
        by_content[fingerprint] = rid
        docs.append({**dict(doc), "rid": rid, "content_fingerprint": fingerprint})
        counts["added"] += 1
    return docs, rid_map, counts


def _cap_added_reference_docs(
    docs: list[dict[str, Any]], *, already_held: int
) -> tuple[list[dict[str, Any]], list[str]]:
    """The session cap, applied to what a merge ADDS and never to what the
    extended side already holds.

    ``docs`` is ``_merge_reference_docs``' output: the ``already_held``
    documents first, then the new ones. Every held document is kept whatever
    the totals say — a merge is append-only, and a legacy file already past
    the token cap must not lose an attachment because something new arrived
    — and a new document lands only while the count and the tokens stay
    inside ``MAX_REFERENCE_DOCS`` / ``MAX_REFERENCE_TOKENS``. The titles of
    the new documents that do not fit are returned, to be named.
    """
    kept = [dict(doc) for doc in docs[:already_held]]
    total = sum(int(doc.get("token_count", 0) or 0) for doc in kept)
    dropped: list[str] = []
    for doc in docs[already_held:]:
        tokens = int(doc.get("token_count", 0) or 0)
        if len(kept) >= MAX_REFERENCE_DOCS or total + tokens > MAX_REFERENCE_TOKENS:
            dropped.append(str(doc.get("title") or doc.get("rid") or "document"))
            continue
        total += tokens
        kept.append(dict(doc))
    return kept, dropped


def _unless_only_restamped(
    record: dict[str, Any], held: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """``record``, or the held record for its number when the two differ only
    by ``exported_at`` (see the registry step of :func:`merge_project_brief`)."""
    previous = held.get(_fold(record.get("number")))
    if previous is None:
        return record
    unstamped = {key: value for key, value in record.items() if key != "exported_at"}
    held_unstamped = {
        key: value for key, value in previous.items() if key != "exported_at"
    }
    return dict(previous) if unstamped == held_unstamped else record


def _section_label(brief: ProjectBrief, fallback: str) -> str:
    newest = brief.newest_section or {}
    number = _fold(newest.get("number"))
    if number and number != "(unnumbered)":
        return f"section {number}"
    return fallback


def _edition_conflict_fact(
    standard: str,
    brief_entry: dict[str, str],
    section_entry: dict[str, str],
    *,
    section_label: str,
    section_number: str,
    recorded_at: str,
    index: int,
) -> dict[str, Any]:
    """D4: an edition recorded differently by two sections is a coordination
    defect, so it becomes a project fact a person has to resolve.

    The statement names the two editions in sorted order and nothing about
    which side holds which — so every merge that meets this disagreement
    produces the SAME statement (the duplicate key): a refresh from either
    section, and a pull the other way. Without that, "newest export wins"
    would flip the brief's own edition on each section's save and word the
    conflict the other way round each time, recording one disagreement as
    two facts. Who records what, and on which basis, rides in ``detail`` (a
    statement is bounded at 240 characters and a basis is free text). The
    uid is derived from the statement for the same reason: one
    disagreement, one fact.
    """
    editions = sorted(
        {str(brief_entry.get("edition", "")), str(section_entry.get("edition", ""))}
    )
    statement = (
        f"Sections disagree on the {standard} edition: {' and '.join(editions)} "
        "are both recorded. Resolve before issue."
    )
    detail = (
        f"The project brief records {brief_entry.get('edition', '')} "
        f"(basis: {brief_entry.get('basis', '') or 'none recorded'}); "
        f"{section_label} records {section_entry.get('edition', '')} "
        f"(basis: {section_entry.get('basis', '') or 'none recorded'})."
    )
    return {
        # A placeholder the merge always re-mints; unique per conflict (a
        # ledger restore drops a repeated pid) and never numeric, so it can
        # never be read as a real fact's sequence number.
        "pid": f"pf-conflict{index}",
        "statement": statement,
        "detail": detail,
        "scope": "project",
        "status": "assumed",
        "source_kind": "brief",
        "source_ref": f"project brief; {section_label}",
        "recorded_in": section_number,
        "recorded_at": recorded_at,
        "uid": hashlib.sha256(statement.encode("utf-8")).hexdigest()[:32],
    }


def _brief_content(brief: ProjectBrief) -> dict[str, Any]:
    content = brief.to_dict()
    content.pop("updated_at", None)
    content.pop("app_version", None)
    return content


def merge_project_brief(
    existing: ProjectBrief,
    incoming: ProjectBrief,
    *,
    section_side: str = "incoming",
    apply_setup: bool = True,
    fact_pid_floor: int = 1,
    reference_mint_floor: int | None = None,
    now: str | None = None,
) -> tuple[ProjectBrief, MergeReport]:
    """Join ``incoming`` into ``existing``. Pure and deterministic; never
    mutates either. Returns ``(merged, report)``.

    ``existing`` is the side being extended — the brief file on a refresh or
    an export onto an existing file, the section's own brief on a pull —
    and keeps its ids: its pids, its rids, its research rounds' numbers.
    ``section_side`` says which side is the SECTION (``"incoming"`` or
    ``"existing"``), so a report and the D4 conflict fact name the two sides
    the same way whichever direction the merge ran.

    - Research: :func:`merge_research_profiles` (unseen rounds replayed).
    - References: by content (``_merge_reference_docs``), then the session
      cap; drops are named.
    - Facts: :func:`project_facts.merge_facts`, with every incoming ``ref-N``
      rewritten through the reference map and the D4 conflict facts appended.
    - Profile and project type: an empty value never erases a recorded one;
      where both sides record a value, the newest export's wins (D4) and the
      difference is reported naming both. With ``apply_setup=False`` (a pull
      — the document is the section's own) nothing is applied, only
      reported. Edition overrides: a union; one standard recorded at two
      editions is a warning AND a project fact (D4).
    - Sections registry: joined by number, newest ``exported_at`` wins
      (``merge_section_registries``), kept in export order.

    Idempotent: merging the result with the same ``incoming`` again returns
    it unchanged, ``updated_at`` included — nothing new means nothing to
    write. Raises :class:`ProjectBriefMismatchError` for two projects and
    :class:`ProjectBriefMergeRefused` for a merge that would have to delete
    or duplicate to proceed.
    """
    if existing.project_id != incoming.project_id:
        raise ProjectBriefMismatchError(
            "These are briefs of two different projects; they were not merged."
        )
    stamp = now or _now()
    report = MergeReport()
    section_is_incoming = section_side != "existing"
    brief_side, section_brief = (
        (existing, incoming) if section_is_incoming else (incoming, existing)
    )
    section_label = _section_label(section_brief, "this section")
    section_number = _fold((section_brief.newest_section or {}).get("number"))
    if section_number == "(unnumbered)":
        section_number = ""
    newest = incoming if (incoming.updated_at or "") > (existing.updated_at or "") else existing
    older = existing if newest is incoming else incoming
    kept_label = "the section" if newest is section_brief else "the project brief"

    # -- research -------------------------------------------------------------
    base_profile = (
        RequirementsProfile.from_dict(existing.research_profile)
        if existing.research_profile is not None
        else None
    )
    other_profile = (
        RequirementsProfile.from_dict(incoming.research_profile)
        if incoming.research_profile is not None
        else None
    )
    merged_profile, research_report = merge_research_profiles(base_profile, other_profile)
    research_dict = (
        existing.research_profile
        if merged_profile is base_profile
        else (merged_profile.to_dict() if merged_profile is not None else None)
    )
    report.research = research_report.to_dict()
    report.warnings.extend(research_report.notes())

    # -- references -------------------------------------------------------------
    reference_docs, rid_map, reference_counts = _merge_reference_docs(
        list(existing.reference_docs),
        list(incoming.reference_docs),
        mint_floor=reference_mint_floor,
    )
    kept_docs, dropped = _cap_added_reference_docs(
        reference_docs, already_held=len(existing.reference_docs)
    )
    if dropped:
        reference_counts["added"] = max(0, reference_counts["added"] - len(dropped))
    report.references = {**reference_counts, "dropped": list(dropped)}
    if dropped:
        report.warnings.append(
            "Reference document(s) past the attachment cap were not carried: "
            + ", ".join(dropped)
        )

    # -- setup: profile, project type, edition overrides ------------------------
    profile = dict(existing.profile)
    base_parsed = ProjectProfile.from_dict(existing.profile)
    other_parsed = ProjectProfile.from_dict(incoming.profile)
    base_fields = base_parsed.to_dict() if base_parsed is not None else {}
    other_fields = other_parsed.to_dict() if other_parsed is not None else {}
    newest_fields = other_fields if newest is incoming else base_fields
    older_fields = base_fields if newest is incoming else other_fields
    brief_fields = base_fields if section_is_incoming else other_fields
    section_fields = other_fields if section_is_incoming else base_fields
    for key, label in _PROFILE_FIELD_LABELS.items():
        brief_value = brief_fields.get(key, "")
        section_value = section_fields.get(key, "")
        if brief_value and section_value and brief_value != section_value:
            report.setup.append(
                {
                    "kind": "profile",
                    "field": key,
                    "label": label,
                    "brief": brief_value,
                    "section": section_value,
                    "kept": kept_label if apply_setup else "the section",
                }
            )
    if apply_setup and (newest_fields or older_fields):
        # Field by field: the newest export's value where it records one, the
        # older side's where it does not — an empty field never erases a
        # recorded one (nothing is deleted by a merge). Replaced only when a
        # value actually moved, so a brief whose profile is merely shaped
        # differently (a session-built brief omits blank fields) is left
        # byte-identical.
        combined = {
            key: newest_fields.get(key, "") or older_fields.get(key, "")
            for key in _PROFILE_FIELD_LABELS
        }
        before = {key: base_fields.get(key, "") for key in _PROFILE_FIELD_LABELS}
        if combined != before:
            profile = combined

    project_type = existing.project_type
    brief_type = brief_side.project_type
    section_type = section_brief.project_type
    if brief_type and section_type and brief_type != section_type:
        report.setup.append(
            {
                "kind": "project_type",
                "field": "project_type",
                "label": "project type",
                "brief": brief_type,
                "section": section_type,
                "kept": kept_label if apply_setup else "the section",
            }
        )
    if apply_setup:
        project_type = newest.project_type or older.project_type

    overrides = {name: dict(entry) for name, entry in existing.edition_overrides.items()}
    conflict_facts: list[dict[str, Any]] = []
    recorded_at = stamp[:10]
    for name, entry in brief_side.edition_overrides.items():
        other_entry = section_brief.edition_overrides.get(name)
        if other_entry is None or other_entry.get("edition") == entry.get("edition"):
            continue
        report.setup.append(
            {
                "kind": "edition",
                "field": name,
                "label": f"{name} edition",
                "brief": str(entry.get("edition", "")),
                "section": str(other_entry.get("edition", "")),
                "kept": kept_label if apply_setup else "the section",
            }
        )
        conflict_facts.append(
            _edition_conflict_fact(
                name,
                entry,
                other_entry,
                section_label=section_label,
                section_number=section_number,
                recorded_at=recorded_at,
                index=len(conflict_facts),
            )
        )
        report.conflicts.append(
            f"The project brief records {name} {entry.get('edition', '')}; "
            f"{section_label} records {other_entry.get('edition', '')}. "
            "Recorded as a project fact to resolve before issue."
        )
    if apply_setup:
        for name, entry in incoming.edition_overrides.items():
            if name not in overrides or newest is incoming:
                overrides[name] = dict(entry)
    for difference in report.setup:
        if difference["kind"] == "edition":
            continue
        where = (
            f"the brief keeps {difference['kept']}'s value, the newest export"
            if apply_setup
            else "this section keeps its own — change it here if the brief is right"
        )
        report.warnings.append(
            f"The {difference['label']} differs: the project brief records "
            f"{difference['brief']!r}; {section_label} records "
            f"{difference['section']!r} ({where})."
        )

    # -- facts ------------------------------------------------------------------
    kept_rids = {str(doc.get("rid", "")) for doc in kept_docs}
    before_rids = {str(doc.get("rid", "")) for doc in existing.reference_docs}
    merged_items = {
        item.item_id for item in (merged_profile.items if merged_profile else [])
    }
    before_items = {
        item.item_id for item in (base_profile.items if base_profile else [])
    }

    def resolves(kind: str, ref: str) -> bool:
        return ref in (kept_rids if kind == "reference" else merged_items)

    def resolved_before(kind: str, ref: str) -> bool:
        return ref in (before_rids if kind == "reference" else before_items)

    try:
        facts, facts_report = merge_facts(
            list(existing.facts),
            [*incoming.facts, *conflict_facts],
            rid_map=rid_map,
            section_of_incoming=(
                section_number if section_is_incoming else "the project brief"
            ),
            resolves=resolves,
            resolved_before=resolved_before,
            pid_floor=fact_pid_floor,
        )
    except FactsMergeRefused as exc:
        report.facts = exc.report.to_dict()
        raise ProjectBriefMergeRefused(str(exc), report) from exc
    report.facts = facts_report.to_dict()
    report.conflicts.extend(facts_report.conflicts)
    if facts_report.refs_unresolved:
        report.warnings.append(
            f"{facts_report.refs_unresolved} carried fact(s) cited a source the "
            "merged brief does not hold; each now names the section that "
            "recorded it instead."
        )

    # -- sections registry ------------------------------------------------------
    before_numbers = {_fold(s.get("number")): s for s in existing.sections}
    # A section rebuilds its own record with a fresh ``exported_at`` on every
    # save; a record that differs from the one on file ONLY by that stamp is
    # not news, so the file's copy is kept — otherwise every save of an
    # unchanged section would rewrite the brief (and reorder its registry)
    # for nothing, and a sibling syncing the folder would see a change that
    # is not one.
    incoming_sections = [
        _unless_only_restamped(record, before_numbers)
        for record in incoming.sections
    ]
    joined = merge_section_registries(existing.sections, incoming_sections)
    ordered = sorted(
        enumerate(joined), key=lambda pair: (str(pair[1].get("exported_at") or ""), pair[0])
    )
    sections = [record for _index, record in ordered][-MAX_LINK_SECTIONS:]
    report.sections = {
        "added": sum(1 for s in sections if _fold(s.get("number")) not in before_numbers),
        "updated": sum(
            1
            for s in sections
            if _fold(s.get("number")) in before_numbers
            and before_numbers[_fold(s.get("number"))] != s
        ),
    }

    created = min(
        (value for value in (existing.created_at, incoming.created_at) if value),
        default=existing.created_at,
    )
    merged = ProjectBrief(
        project_id=existing.project_id,
        name=existing.name or incoming.name,
        created_at=created,
        updated_at=existing.updated_at,
        app_version=existing.app_version,
        profile=profile,
        project_type=project_type,
        edition_overrides=overrides,
        research_profile=research_dict,
        reference_docs=[dict(doc) for doc in kept_docs],
        facts=facts,
        sections=[dict(record) for record in sections],
    )
    report.assets_changed = (
        merged.research_profile != existing.research_profile
        or merged.reference_docs != [dict(doc) for doc in existing.reference_docs]
        or merged.facts != [dict(fact) for fact in existing.facts]
    )
    report.changed = _brief_content(merged) != _brief_content(existing)
    if not report.changed:
        # Nothing new: the existing brief, untouched — its updated_at too,
        # which is what makes a repeated merge (and a save that brought
        # nothing) a no-op a sibling section never sees as a change.
        return replace(copy.deepcopy(existing), warnings=[]), report
    merged.updated_at = stamp
    merged.app_version = settings.VERSION
    return merged, report


def write_brief_atomically(
    path: str, payload: bytes, *, prefix: str = ".buildaspec-brief-"
) -> None:
    """Replace ``path`` with ``payload`` atomically, or leave it untouched.

    The shell's ``_atomic_write_target`` idiom, moved here so the refresh
    route and the native shell share one implementation (the shell's own
    saves go through it too): a temporary file in the SAME folder, flushed
    and fsynced, then ``os.replace`` — so a crash or a full disk leaves the
    old file byte-identical rather than half-written. Raises ``OSError`` (or
    ``TypeError`` / ``ValueError`` for a path the platform cannot express);
    the temporary file never outlives a failure.
    """
    temp_path: str | None = None
    try:
        target_path = os.path.abspath(os.fspath(path))
        target_dir = os.path.dirname(target_path)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=target_dir,
            prefix=prefix,
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = handle.name
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target_path)
        temp_path = None
    finally:
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def read_brief_file(path: str) -> ProjectBrief:
    """Read and parse the brief at ``path`` — bounded, never following a link.

    Raises ``ProjectBriefError`` (unreadable, not a brief, past the cap) or
    ``OSError`` (gone, unreadable). Worker threads only: it is disk I/O and a
    full parse.
    """
    if os.path.islink(path):
        raise ProjectBriefError("The project brief is a link; links are never followed.")
    if os.path.getsize(path) > MAX_PROJECT_BRIEF_BYTES:
        raise ProjectBriefTooLargeError(
            "The project brief exceeds the "
            f"{MAX_PROJECT_BRIEF_BYTES // (1024 * 1024)} MiB limit."
        )
    with open(path, "rb") as handle:
        data = handle.read(MAX_PROJECT_BRIEF_BYTES + 1)
    return parse_project_brief(data)


# ---------------------------------------------------------------------------
# Manifest (what the dialog and the export confirm show)
# ---------------------------------------------------------------------------


def within_reference_cap(
    docs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Split carried reference documents into the ones that fit the session
    cap, in order, and the titles of the ones that do not."""
    kept: list[dict[str, Any]] = []
    dropped: list[str] = []
    total = 0
    for doc in docs:
        tokens = int(doc.get("token_count", 0) or 0)
        if len(kept) >= MAX_REFERENCE_DOCS or total + tokens > MAX_REFERENCE_TOKENS:
            dropped.append(str(doc.get("title") or doc.get("rid") or "document"))
            continue
        total += tokens
        kept.append(doc)
    return kept, dropped


def brief_manifest(brief: ProjectBrief) -> dict[str, Any]:
    """Counts and names for the New-section dialog and the export confirm.

    Warnings are the brief's own plus what a seed would have to do: drop a
    reference document past the cap, run unbriefed research rounds because
    the profile was edited after the research, or fall back to the default
    module because the brief's is not installed.
    """
    warnings = list(brief.warnings)
    profile = ProjectProfile.from_dict(brief.profile)
    profile_line = ""
    complete = False
    if profile is not None:
        complete = profile.is_complete()
        parts = [
            p for p in (profile.city, profile.state_display, profile.country_display) if p
        ]
        profile_line = ", ".join(parts)
        if profile.client_name:
            profile_line = (
                f"{profile_line} — Client: {profile.client_name}"
                if profile_line
                else f"Client: {profile.client_name}"
            )
    if not complete:
        warnings.append(
            "The project profile is incomplete; research cannot start until "
            "city, state, country and client are all recorded."
        )

    newest = brief.newest_section or {}
    module_id = str(newest.get("module_id") or "")
    module = AVAILABLE_MODULES.get(module_id) if module_id else None
    if module_id and module is None:
        warnings.append(
            f"The brief's module {module_id!r} is not installed; the new section "
            "will use the default module."
        )

    research: dict[str, Any] | None = None
    if brief.research_profile is not None:
        restored = RequirementsProfile.from_dict(brief.research_profile)
        if restored is not None:
            research = {
                "items": len(restored.items),
                "grounded": len(restored.grounded_items()),
                "rounds": restored.round_count,
                "dimensions_completed": restored.completed_dimensions,
                "dimensions_recorded": len(restored.dimension_statuses),
                "dimensions_declared": (
                    len(module.research_dimensions) if module is not None else None
                ),
                "last_research_date": restored.research_date,
                "sections": sorted(
                    {
                        getattr(r, "section", "")
                        for r in restored.rounds
                        if getattr(r, "section", "")
                    }
                ),
            }
            recorded_project = ProjectProfile.from_dict(restored.project)
            if (
                profile is not None
                and recorded_project is not None
                and recorded_project.to_dict() != profile.to_dict()
            ):
                warnings.append(
                    "The project profile was edited after the research ran, so a "
                    "new research round will not be briefed with the earlier "
                    "findings until the profile matches again."
                )

    kept, dropped = within_reference_cap(brief.reference_docs)
    if dropped:
        warnings.append(
            "Reference document(s) beyond the session cap will not be carried: "
            + ", ".join(dropped)
        )

    facts = [ProjectFact.from_dict(f) for f in brief.facts]
    return {
        "project_id": brief.project_id,
        "name": brief.name,
        "created_at": brief.created_at,
        "updated_at": brief.updated_at,
        "app_version": brief.app_version,
        "profile": {**brief.profile, "line": profile_line, "complete": complete},
        "project_type": brief.project_type,
        "module_id": module_id,
        "module_available": module is not None,
        "discipline": str(newest.get("discipline") or ""),
        "edition_overrides": {
            "count": len(brief.edition_overrides),
            "standards": [
                f"{name} — {entry.get('edition', '')}"
                + (f" ({entry['basis']})" if entry.get("basis") else "")
                for name, entry in brief.edition_overrides.items()
            ],
        },
        "research": research,
        "references": [
            {
                "rid": doc.get("rid", ""),
                "title": doc.get("title", ""),
                "kind": doc.get("kind", "docx"),
                "token_count": int(doc.get("token_count", 0) or 0),
                "truncated": bool(doc.get("truncated", False)),
                "carried": doc in kept,
            }
            for doc in brief.reference_docs
        ],
        "reference_tokens": sum(int(d.get("token_count", 0) or 0) for d in kept),
        "facts": {
            "active": sum(1 for f in facts if f.active),
            "confirmed": sum(1 for f in facts if f.status == "confirmed"),
            "assumed": sum(1 for f in facts if f.status == "assumed"),
            "superseded": sum(1 for f in facts if not f.active),
        },
        "sections": [dict(s) for s in brief.sections],
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# The PROJECT SECTIONS context block
# ---------------------------------------------------------------------------


def _estimate_tokens(text: str) -> int:
    return len(text) // 4


def _section_line(record: dict[str, Any], *, with_articles: bool) -> str:
    number = record.get("number", "")
    title = record.get("title", "")
    state = "issue-ready" if record.get("ready") else "in progress"
    exported = record.get("exported_at", "")
    tail = f"{state}; exported {exported[:10]}" if exported else state
    head = f"- {number} {title}".rstrip()
    articles = record.get("article_titles") or []
    if with_articles and articles:
        return f"{head} — articles: {'; '.join(articles)} ({tail})"
    return f"{head} ({tail})"


def project_sections_block(
    link: dict[str, Any] | None,
    current_section_number: str,
    *,
    max_tokens: int = SECTIONS_CONTEXT_MAX_TOKENS,
) -> str:
    """The other sections of this project, for the PROJECT CONTEXT block.

    ``""`` without a link or when no OTHER section is listed, so an unlinked
    session builds a byte-identical request. Under the cap the article lists
    go first (titles only — never provision text), then trailing sections,
    with a disclosed count.
    """
    if not isinstance(link, dict):
        return ""
    sanitized = sanitize_project_link(link)
    if sanitized is None:
        return ""
    current = " ".join((current_section_number or "").split())
    others = [
        record
        for record in sanitized.get("sections", [])
        if record.get("number") and record.get("number") != current
    ]
    if not others:
        return ""
    where = (
        f"this session is section {current}"
        if current
        else "this session's section is not yet numbered"
    )
    name = sanitized.get("name") or "this project"
    header = (
        "PROJECT SECTIONS (sections of this project drafted so far, from its "
        f'project brief "{name}"; coordinate scope with them and do not '
        f"duplicate their provisions — {where}):"
    )
    footer = (
        "A provision that belongs to a listed section is cross-referenced "
        '("as specified in Section 21 13 13"), never restated here.'
    )
    with_articles = True
    lines = [_section_line(r, with_articles=True) for r in others]
    budget = max_tokens - _estimate_tokens(header) - _estimate_tokens(footer)
    if sum(_estimate_tokens(line) for line in lines) > budget:
        with_articles = False
        lines = [_section_line(r, with_articles=False) for r in others]
    omitted = 0
    while len(lines) > 1 and sum(_estimate_tokens(line) for line in lines) > budget:
        lines.pop()
        omitted += 1
    if omitted:
        lines.append(
            f"- ({omitted} further section(s) omitted here for length; they are "
            "listed in the Project facts panel.)"
        )
    if not with_articles and not omitted:
        lines.append("(Article lists omitted for length.)")
    return "\n".join([header, *lines, footer])
