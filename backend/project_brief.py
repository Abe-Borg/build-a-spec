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

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from . import settings
from .project_facts import ProjectFact
from .project_profile import ProjectProfile
from .reference_docs import MAX_REFERENCE_DOCS, MAX_REFERENCE_TOKENS, ReferenceDoc
from .research.engine import RequirementsProfile
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
        """The section record that exported this brief last (the list is
        upserted in export order, so the newest is the last entry)."""
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
    own_rounds = [
        r for r in rounds if getattr(r, "section", "") == number and number
    ]
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
        "research_rounds": (
            len(own_rounds) if own_rounds else (len(rounds) if profile is not None else 0)
        ),
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
    record by number.
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

    sections = [dict(s) for s in (link or {}).get("sections", []) or []]
    own = section_record(session, ready=ready, exported_at=now)
    replaced = False
    for index, existing in enumerate(sections):
        if existing.get("number") == own["number"]:
            sections[index] = own
            replaced = True
            break
    if not replaced:
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
        raise ProjectBriefError(
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
        record = doc.to_dict()
        record["content_fingerprint"] = _fingerprint(doc.text)
        claimed = entry.get("content_fingerprint")
        if isinstance(claimed, str) and claimed and claimed != record["content_fingerprint"]:
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
