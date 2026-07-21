"""Core :class:`SpecModule` type and registry validation.

Ported from Claude-Spec-Critic ``src/modules/base.py`` and repointed at
authoring: a **spec module** is one draftable domain configuration — the
section catalog, the interview playbook with its recommended defaults, the
pinned standards basis, the drafting prompt slots, and the lint vocabulary.
Like the review-side original, the module is a single frozen object picked
from a registry, so incoherent combinations are unrepresentable, and the
registry is validated at import time — a bad module definition fails app
startup, never mid-interview.

The prompt *protocol* (tool contract, provenance discipline, the
defaults-first interview policy) stays engine-owned in
``backend/llm/prompts.py``; the module supplies content slots the engine
renders into its template. ``research_dimensions`` are carried **dormant**
in Phase 3 — validated at registration (a typo'd placeholder fails startup
today, not when Phase 4 activates the research fan-out) but not yet
consumed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from ..standards import StandardsBasis, normalize_standard_name


def _coerce_tuple_fields(obj: object, field_names: tuple[str, ...]) -> None:
    """Coerce list-valued sequence fields on a frozen dataclass to tuples.

    Same posture as Spec Critic: config-shaped data arrives with lists;
    tuples keep the dataclass hashable. A bare string is rejected rather
    than silently iterated per-character.
    """
    for field_name in field_names:
        value = getattr(obj, field_name)
        if isinstance(value, str):
            raise TypeError(
                f"{type(obj).__name__}.{field_name} must be a sequence, "
                f"not a single string: {value!r}"
            )
        if not isinstance(value, tuple):
            object.__setattr__(obj, field_name, tuple(value))


# CSI MasterFormat section number: "21 13 13" (optionally "21 13 13.16").
_SECTION_NUMBER_RE = re.compile(r"^\d{2} \d{2} \d{2}(?:\.\d{2})?$")


@dataclass(frozen=True)
class SectionDef:
    """One section the module can author.

    Attributes:
        number: CSI MasterFormat number, e.g. ``"21 13 13"``.
        title: Section title, e.g. ``"Wet-Pipe Sprinkler Systems"``.
        scope_note: One-line drafting guidance (what belongs in this
            section vs its siblings). Rendered into the system prompt.
        playbook_depth: ``"full"`` when the interview playbook below was
            authored for this section; ``"catalog"`` when the model drafts
            it from the module's general knowledge, pins, and conventions.
    """

    number: str
    title: str
    scope_note: str = ""
    playbook_depth: str = "catalog"


@dataclass(frozen=True)
class InterviewTopic:
    """One ordered topic of the defaults-first interview.

    Attributes:
        topic_id: Stable id, unique within the module.
        title: Short topic label rendered into the playbook list.
        guidance: What to establish and how to ask (1–2 sentences).
        default: The recommended answer applied when the user defers
            ("I don't know" stamps the resulting blocks ``assumed``).
            Required unless ``non_defaultable``.
        non_defaultable: True for the interview's hard minimum — topics the
            model must get answered before relying on them (section,
            location/client, basic hazard picture). Such topics carry no
            default by construction.
    """

    topic_id: str
    title: str
    guidance: str
    default: str = ""
    non_defaultable: bool = False


@dataclass(frozen=True)
class ResearchDimension:
    """One axis of the Phase 4 requirements-research fan-out (dormant).

    Ported shape from Spec Critic ``src/modules/base.py``. The research
    engine arrives in Phase 4; carrying the dimensions now means the
    module's research brief is registration-validated from day one.
    ``prompt_template`` is a ``str.format`` template over the project
    profile placeholders (:data:`PROFILE_FORMAT_PLACEHOLDERS`) plus the
    module basis placeholders (``StandardsBasis.format_kwargs``).
    """

    dimension_id: str
    title: str
    prompt_template: str
    max_searches: int = 0
    max_fetches: int = 0


# Per-run project-identity placeholders a research template may reference
# (same contract as Spec Critic). Dummy values exist only for
# registration-time format checking — they never reach a real prompt.
PROFILE_FORMAT_PLACEHOLDERS: tuple[str, ...] = (
    "city",
    "state_or_province",
    "country",
    "client_name",
)

_DUMMY_PROFILE_FORMAT_KWARGS: dict[str, str] = {
    "city": "Springfield",
    "state_or_province": "Virginia",
    "country": "USA",
    "client_name": "ExampleCo",
}


@dataclass(frozen=True)
class SpecModule:
    """One draftable domain configuration.

    Attributes:
        module_id: Stable registry key (persisted into project files —
            treat a rename like a schema change).
        display_name: Human-readable name for the header / project files.
        description: One-line summary.
        basis: The pinned standards basis (:class:`StandardsBasis`).
            ``basis.label`` is registry-unique.
        section_catalog: Sections this module can author (first entry is
            the lead section the interview steers toward by default).
        interview_playbook: Ordered defaults-first topics.
        drafting_persona: First paragraph of the system prompt — who the
            drafter is and the project context.
        domain_conventions: Domain-specific spec conventions block
            (rendered after the engine's generic conventions). May
            reference the basis placeholders from
            ``StandardsBasis.format_kwargs`` — format-checked at
            registration.
        lint_extra_marker_patterns: Additional regex sources the lint
            engine's template-marker rule scans for (compiled
            case-insensitive; validated compilable at registration).
        research_dimensions: The Phase 4 requirements-research axes.
        research_persona: First line of the research system prompt (who
            the researcher is); the engine wraps it with the byte-stable
            research protocol block. Required non-empty iff the module
            ships research dimensions — a module cannot carry dead
            location-aware content (Spec Critic's D-2 rule).
    """

    module_id: str
    display_name: str
    description: str
    basis: StandardsBasis
    section_catalog: tuple[SectionDef, ...]
    interview_playbook: tuple[InterviewTopic, ...]
    drafting_persona: str
    domain_conventions: str
    lint_extra_marker_patterns: tuple[str, ...] = ()
    research_dimensions: tuple[ResearchDimension, ...] = ()
    research_persona: str = ""
    # First line of the compliance-audit system prompt. Same conditional
    # rule as research_persona: required iff the module ships research
    # dimensions (the audit evaluates against the researched profile).
    compliance_persona: str = ""

    def __post_init__(self) -> None:
        _coerce_tuple_fields(
            self,
            (
                "section_catalog",
                "interview_playbook",
                "lint_extra_marker_patterns",
                "research_dimensions",
            ),
        )

    def lead_section(self) -> SectionDef:
        return self.section_catalog[0]


def research_template_format_kwargs(basis: StandardsBasis) -> dict[str, str]:
    """Placeholders a research prompt template may reference (dummy profile)."""
    kwargs = basis.format_kwargs()
    kwargs.update(_DUMMY_PROFILE_FORMAT_KWARGS)
    return kwargs


def _validate_basis(module: SpecModule) -> None:
    basis = module.basis
    if not isinstance(basis, StandardsBasis):
        raise ValueError(
            f"SpecModule {module.module_id!r}: basis must be a StandardsBasis, "
            f"got {type(basis).__name__}"
        )
    if not basis.base_codes:
        raise ValueError(
            f"SpecModule {module.module_id!r}: basis pins no base codes"
        )
    keys = [code.key for code in basis.base_codes]
    if len(set(keys)) != len(keys) or not all(
        k and k.strip() and code.year and code.name
        for k, code in zip(keys, basis.base_codes)
    ):
        raise ValueError(
            f"SpecModule {module.module_id!r}: base codes need unique "
            f"non-empty keys, names, and years (got keys {keys})"
        )
    if not basis.standards:
        raise ValueError(
            f"SpecModule {module.module_id!r}: basis pins no standards"
        )
    seen: set[str] = set()
    for std in basis.standards:
        canonical = normalize_standard_name(std.name)
        if not canonical or not std.edition.strip():
            raise ValueError(
                f"SpecModule {module.module_id!r}: pinned standard needs a "
                f"non-empty name and edition (got {std.name!r} / "
                f"{std.edition!r})"
            )
        if canonical in seen:
            raise ValueError(
                f"SpecModule {module.module_id!r}: duplicate pinned standard "
                f"{std.name!r}"
            )
        seen.add(canonical)
        if not std.source.strip():
            raise ValueError(
                f"SpecModule {module.module_id!r}: pinned standard "
                f"{std.name!r} has no provenance source — document where the "
                "edition was confirmed (prefix UNVERIFIED if it wasn't)"
            )


def _validate_catalog(module: SpecModule) -> None:
    if not module.section_catalog:
        raise ValueError(
            f"SpecModule {module.module_id!r}: section catalog is empty"
        )
    seen: set[str] = set()
    for section in module.section_catalog:
        if not isinstance(section, SectionDef):
            raise ValueError(
                f"SpecModule {module.module_id!r}: catalog entries must be "
                f"SectionDef, got {type(section).__name__}"
            )
        if not _SECTION_NUMBER_RE.match(section.number):
            raise ValueError(
                f"SpecModule {module.module_id!r}: section number "
                f"{section.number!r} is not CSI-formatted ('21 13 13')"
            )
        if not section.title.strip():
            raise ValueError(
                f"SpecModule {module.module_id!r}: section {section.number} "
                "has an empty title"
            )
        if section.playbook_depth not in ("full", "catalog"):
            raise ValueError(
                f"SpecModule {module.module_id!r}: section {section.number} "
                f"has unknown playbook_depth {section.playbook_depth!r}"
            )
        if section.number in seen:
            raise ValueError(
                f"SpecModule {module.module_id!r}: duplicate catalog section "
                f"{section.number!r}"
            )
        seen.add(section.number)


def _validate_playbook(module: SpecModule) -> None:
    if not module.interview_playbook:
        raise ValueError(
            f"SpecModule {module.module_id!r}: interview playbook is empty"
        )
    seen: set[str] = set()
    for topic in module.interview_playbook:
        if not isinstance(topic, InterviewTopic):
            raise ValueError(
                f"SpecModule {module.module_id!r}: playbook entries must be "
                f"InterviewTopic, got {type(topic).__name__}"
            )
        if (
            not topic.topic_id
            or topic.topic_id != topic.topic_id.strip()
            or not topic.title.strip()
            or not topic.guidance.strip()
        ):
            raise ValueError(
                f"SpecModule {module.module_id!r}: playbook topic needs a "
                f"stripped non-empty topic_id, title, and guidance "
                f"(got id {topic.topic_id!r})"
            )
        if topic.topic_id in seen:
            raise ValueError(
                f"SpecModule {module.module_id!r}: duplicate playbook topic "
                f"{topic.topic_id!r}"
            )
        seen.add(topic.topic_id)
        # Defaults-first is structural: a defaultable topic MUST ship its
        # recommended default; a non-defaultable topic must not carry one
        # (it would tempt the model to stall-proof a question that has to
        # be answered).
        if topic.non_defaultable and topic.default.strip():
            raise ValueError(
                f"SpecModule {module.module_id!r}: topic {topic.topic_id!r} "
                "is non-defaultable and must not carry a default"
            )
        if not topic.non_defaultable and not topic.default.strip():
            raise ValueError(
                f"SpecModule {module.module_id!r}: topic {topic.topic_id!r} "
                "is defaultable and must carry its recommended default "
                "(defaults-first interview)"
            )


def _validate_prompt_slots(module: SpecModule) -> None:
    for field_name in ("drafting_persona", "domain_conventions"):
        value = getattr(module, field_name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"SpecModule {module.module_id!r} has an empty prompt slot: "
                f"{field_name}"
            )
    kwargs = module.basis.format_kwargs()
    try:
        module.domain_conventions.format(**kwargs)
    except Exception as exc:  # KeyError / IndexError / ValueError from format
        raise ValueError(
            f"SpecModule {module.module_id!r}: domain_conventions does not "
            f"format against its own basis ({exc!r}). Available "
            f"placeholders: {sorted(kwargs)}"
        ) from exc


def _validate_lint_patterns(module: SpecModule) -> None:
    for src in module.lint_extra_marker_patterns:
        try:
            re.compile(src, flags=re.IGNORECASE)
        except re.error as exc:
            raise ValueError(
                f"SpecModule {module.module_id!r}: lint_extra_marker_patterns "
                f"entry does not compile: {src!r} ({exc})"
            ) from exc


def _validate_research_dimensions(module: SpecModule) -> None:
    # D-2 conditional (ported posture): dimensions present ⇒ personas
    # required; no dimensions ⇒ personas must be empty (no dead content).
    for persona_field in ("research_persona", "compliance_persona"):
        value = getattr(module, persona_field)
        if module.research_dimensions and not value.strip():
            raise ValueError(
                f"SpecModule {module.module_id!r}: research_dimensions "
                f"require a non-empty {persona_field}"
            )
        if not module.research_dimensions and value.strip():
            raise ValueError(
                f"SpecModule {module.module_id!r}: {persona_field} must be "
                "empty when the module ships no research dimensions"
            )
    kwargs = research_template_format_kwargs(module.basis)
    seen: set[str] = set()
    for dim in module.research_dimensions:
        if not isinstance(dim, ResearchDimension):
            raise ValueError(
                f"SpecModule {module.module_id!r}: research_dimensions "
                f"entries must be ResearchDimension, got {type(dim).__name__}"
            )
        if (
            not dim.dimension_id
            or dim.dimension_id != dim.dimension_id.strip()
            or not dim.title.strip()
            or not dim.prompt_template.strip()
        ):
            raise ValueError(
                f"SpecModule {module.module_id!r}: research dimension needs "
                f"a stripped non-empty dimension_id, title, and "
                f"prompt_template (got id {dim.dimension_id!r})"
            )
        if dim.dimension_id in seen:
            raise ValueError(
                f"SpecModule {module.module_id!r}: duplicate research "
                f"dimension_id {dim.dimension_id!r}"
            )
        seen.add(dim.dimension_id)
        if dim.max_searches < 0 or dim.max_fetches < 0:
            raise ValueError(
                f"SpecModule {module.module_id!r}: research dimension "
                f"{dim.dimension_id!r} budgets must be non-negative"
            )
        try:
            dim.prompt_template.format(**kwargs)
        except Exception as exc:
            raise ValueError(
                f"SpecModule {module.module_id!r}: research dimension "
                f"{dim.dimension_id!r} prompt_template does not format "
                f"against the basis + profile placeholders ({exc!r}). "
                f"Available placeholders: {sorted(kwargs)}"
            ) from exc


def validate_module_registry(modules: Iterable[SpecModule]) -> None:
    """Fail fast (``ValueError``) on an inconsistent module registry.

    Runs at import time in :mod:`registry` — same posture as Spec Critic:
    a bad module definition breaks startup, never a session in progress.
    """
    seen_ids: set[str] = set()
    seen_labels: set[str] = set()
    for module in modules:
        if not module.module_id or module.module_id != module.module_id.strip():
            raise ValueError(
                f"SpecModule has an empty or unstripped module_id: "
                f"{module.module_id!r}"
            )
        if module.module_id in seen_ids:
            raise ValueError(
                f"Duplicate module_id in registry: {module.module_id!r}"
            )
        seen_ids.add(module.module_id)
        if not module.display_name.strip() or not module.description.strip():
            raise ValueError(
                f"SpecModule {module.module_id!r} needs a non-empty "
                "display_name and description"
            )
        label = (module.basis.label or "").strip() if module.basis else ""
        if not label:
            raise ValueError(
                f"SpecModule {module.module_id!r} pins no basis label"
            )
        if label in seen_labels:
            raise ValueError(
                f"Duplicate basis label {label!r} across modules — labels "
                "must be registry-unique"
            )
        seen_labels.add(label)

        _validate_basis(module)
        _validate_catalog(module)
        _validate_playbook(module)
        _validate_prompt_slots(module)
        _validate_lint_patterns(module)
        _validate_research_dimensions(module)
