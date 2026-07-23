"""The generic any-discipline authoring module (USA & Canada).

Native Build-a-Spec (Batch 10) — not a Spec Critic port. Where
``hyperscale_fire`` encodes one curated domain (fixed catalog, pinned
current editions with receipts, a fire-specific playbook), this module
encodes the *absence* of curation honestly:

- **Unpinned basis.** No default editions ship with the module. Every
  referenced-standard edition enters per-project through the
  ``set_standard_edition`` operation with a mandatory stated basis — a
  grounded research item id, the user's statement, or an honestly-labeled
  model proposal ("model-proposed, unverified"). Until recorded,
  designations are cited without edition years. The ``unrecorded_edition``
  lint rule (active only for unpinned modules) plus the readiness
  checklist's lint-clean gate enforce the posture without blocking a turn.
- **Open catalog.** No fixed section list; the model establishes the
  MasterFormat section from the session's stated discipline (captured by
  the session-start picker, rendered into PROJECT CONTEXT — never this
  module's cached stable prompt).
- **Region-aware, both countries.** USA: I-codes as model basis with
  state/local adoption as per-project data. Canada: NBC/NFC as provincially
  adopted (or the province's own code), CSA/ULC standards and ULC listings,
  SI units. The project profile layer already normalizes both.

The interview playbook is a discipline-agnostic scaffold. Defaultable
topics carry META-defaults — "propose the discipline-standard practice as
the recommended answer" — because the defensible default for a generic
module IS the model's domain knowledge, applied visibly and stamped
``assumed`` for the reviewer, per the defaults-first policy.
"""
from __future__ import annotations

from ..standards import StandardsBasis
from .base import InterviewTopic, ResearchDimension, SpecModule

# ---------------------------------------------------------------------------
# Standards basis: deliberately unpinned. There is nothing to receipt in
# docs/standards_provenance.md — the absence of pins is the design (every
# edition in effect is recorded per-project with its stated basis).
# ---------------------------------------------------------------------------

GENERIC_BASIS = StandardsBasis(label="generic-unpinned", unpinned=True)


# ---------------------------------------------------------------------------
# Defaults-first interview playbook — a discipline-agnostic scaffold.
# Non-defaultable topics are the hard minimum for ANY section; defaultable
# topics carry META-defaults that lean on the model's discipline knowledge,
# applied visibly and stamped `assumed`.
# ---------------------------------------------------------------------------

_INTERVIEW_PLAYBOOK = (
    InterviewTopic(
        "section_selection", "Section being written",
        guidance=(
            "Establish the CSI MasterFormat section number and title from "
            "the stated discipline and scope; propose the 2-3 most likely "
            "sections with one-line scope distinctions when unsure. Set the "
            "section header immediately once known."
        ),
        non_defaultable=True,
    ),
    InterviewTopic(
        "project_identity", "Project location and client",
        guidance=(
            "City, state or province, country (USA or Canada), and "
            "client/owner. Drives the governing code adoption, climatic and "
            "seismic exposure, units convention, and the listing regime "
            "(UL/FM in the USA, ULC in Canada)."
        ),
        non_defaultable=True,
    ),
    InterviewTopic(
        "scope_basis", "Section scope on this project",
        guidance=(
            "What this section covers on THIS project: the systems, "
            "assemblies, or work results in scope, the building/project "
            "type they serve, and what belongs to sibling sections instead. "
            "Without this the section scope cannot stand."
        ),
        non_defaultable=True,
    ),
    InterviewTopic(
        "regulatory_context", "Governing codes and review authorities",
        guidance=(
            "The adopted construction code governing this work and any "
            "insurer or third-party review. USA: the state/local adoption "
            "of the model codes; Canada: NBC/NFC as provincially adopted or "
            "the province's own code."
        ),
        default=(
            "The municipal building department (and specialty inspection "
            "authorities the discipline conventionally answers to) under "
            "the jurisdiction's currently adopted construction code; no "
            "insurer design review. Record the specific adoption via "
            "set_standard_edition once the user or grounded research "
            "states it."
        ),
    ),
    InterviewTopic(
        "standards_editions", "Referenced standards and editions",
        guidance=(
            "Which standards this section cites and at what editions. This "
            "module pins no defaults: an edition exists for this project "
            "only once recorded with set_standard_edition and a stated "
            "basis."
        ),
        default=(
            "Cite the discipline's conventional standards by designation "
            "WITHOUT edition years until an edition is recorded. When the "
            "user defers, either keep designations year-free or record a "
            "model-proposed edition with the basis honestly labeled "
            "'model-proposed, unverified' — never cite a year in document "
            "text with no recorded basis."
        ),
    ),
    InterviewTopic(
        "system_design_criteria", "System selection and design criteria",
        guidance=(
            "The system/assembly selections and the performance criteria "
            "that size or govern them for this project type."
        ),
        default=(
            "Propose the discipline-standard system selection and design "
            "criteria for the stated project type as the recommended "
            "answer; on deferral, apply it and stamp the blocks assumed, "
            "marking values that genuinely need project data as "
            "[TBD: ...]."
        ),
    ),
    InterviewTopic(
        "products_materials", "Products and materials",
        guidance=(
            "Material grades, product types, and certification/listing "
            "requirements by application."
        ),
        default=(
            "Discipline-standard materials specified by performance and "
            "by the discipline's conventional certification or listing "
            "requirements (UL/FM in the USA, ULC/CSA in Canada, as "
            "applicable); name manufacturers only when the user supplies "
            "them."
        ),
    ),
    InterviewTopic(
        "execution_installation", "Installation and workmanship",
        guidance=(
            "Installation methods, workmanship, tolerances, and "
            "coordination requirements for PART 3."
        ),
        default=(
            "Discipline-standard installation and workmanship requirements "
            "per the governing referenced standards and manufacturer "
            "instructions, with the discipline's conventional coordination "
            "and protection provisions."
        ),
    ),
    InterviewTopic(
        "quality_submittals", "Submittals and quality assurance",
        guidance=(
            "Submittal list, installer qualifications, and quality-control "
            "provisions."
        ),
        default=(
            "Product data for specified items; shop drawings where the "
            "discipline conventionally requires them; installer "
            "qualifications per the discipline's licensing/certification "
            "norms; field quality control per the referenced standards; "
            "authority approvals before fabrication where conventional."
        ),
    ),
    InterviewTopic(
        "testing_acceptance", "Testing and acceptance",
        guidance=(
            "Acceptance testing, commissioning interfaces, and the "
            "documentation that closes the work out."
        ),
        default=(
            "The discipline's conventional acceptance tests, witnessed "
            "where authorities conventionally require, with completed test "
            "reports and certificates submitted at closeout."
        ),
    ),
    InterviewTopic(
        "closeout", "Closeout",
        guidance="O&M data, record documents, warranties, and spares.",
        default=(
            "Operation and maintenance data and record documents at "
            "closeout; the discipline's conventional warranty; spare "
            "materials only where conventional for the discipline."
        ),
    ),
    InterviewTopic(
        "units_language", "Units convention",
        guidance=(
            "Inch-pound vs SI. Follows the project country unless the "
            "client's practice differs."
        ),
        default=(
            "USA projects: inch-pound units. Canada projects: SI (metric) "
            "units, hard metric unless the client's practice is soft "
            "conversion. State the convention once and apply it "
            "consistently."
        ),
    ),
)


_DRAFTING_PERSONA = (
    "You are Build-a-Spec, an expert construction-specification writer "
    "helping a design professional author CSI SectionFormat specification "
    "sections in any discipline, for building and infrastructure projects "
    "in the USA and Canada, through focused dialogue. The session's "
    "discipline is stated in the PROJECT DISCIPLINE line of the PROJECT "
    "CONTEXT block each turn — draft to that discipline's conventions, "
    "terminology, and conventional section structure."
)


_DOMAIN_CONVENTIONS = """\
- This module pins NO standard editions. Every referenced-standard edition enters through set_standard_edition with a mandatory stated basis: a grounded research item id, the user's statement, or an honestly-labeled model proposal such as "model-proposed, unverified — current published edition per training data". Never cite an edition year in document text without a recorded entry; until one exists, cite designations year-free. The lint flags unrecorded edition years — treat those findings as drafting errors.
- USA projects: the I-codes are model-code context only — the governing state or local adoption (and any specialty code for the discipline) is per-project data; never assert an adoption you have not been given or grounded. Listings conventionally UL/FM; units inch-pound.
- Canada projects: the National Building Code and National Fire Code of Canada apply as provincially adopted (several provinces publish their own codes on the NBC basis); CSA and ULC standards and ULC listings conventionally replace many US counterparts; units SI (metric). Never map a US standard to its Canadian counterpart silently — say what you substituted and why.
- The stated discipline governs section selection, vocabulary, and conventional article structure. Where the discipline is outside your deepest specialty, draft plainly and competently from its published conventions, verify designations and listings with a quick web lookup rather than recalling them, and over-flag with assumed status.
- The PART 1 REFERENCES article lists the recorded editions (designation, title, edition); standards cited elsewhere without a recorded edition appear in REFERENCES by designation and title only, without a year.
- Respect sibling-section boundaries for the discipline: coordinate related scope, reference rather than duplicate, and say which section a boundary item belongs to."""


# ---------------------------------------------------------------------------
# Research: discipline-agnostic personas; the discipline reaches each
# dimension through the {discipline} template placeholder (threaded by the
# engine from the session), never through the persona.
# ---------------------------------------------------------------------------

_RESEARCH_PERSONA = (
    "You are a construction code-and-standards research assistant "
    "supporting specification authoring across all design disciplines for "
    "building and infrastructure projects in the USA and Canada. You "
    "research jurisdiction-specific code adoptions and amendments, "
    "authority-having-jurisdiction requirements, client design standards, "
    "and site conditions so a specification author can draft to them. You "
    "report only requirements you can support with sources you actually "
    "retrieved, and you clearly separate verified facts from industry "
    "practice."
)

_COMPLIANCE_PERSONA = (
    "You are a code-compliance reviewer for construction specifications in "
    "any design discipline. You evaluate whether a draft section correctly "
    "represents the project's governing codes, local amendments, AHJ "
    "requirements, and client standards as researched for this project, in "
    "the discipline stated for the session."
)

_RESEARCH_DIMENSIONS = (
    ResearchDimension(
        dimension_id="governing_codes",
        title="Governing construction codes",
        max_searches=40,
        max_fetches=12,
        prompt_template=(
            "Determine the governing construction codes for {discipline} "
            "work on a project in {city}, {state_or_province}, {country}. "
            "For a USA project: (a) the state or local building code "
            "editions currently in force and their model-code basis "
            "(I-code year) with effective dates, plus any specialty code "
            "governing {discipline} work as adopted; (b) municipal or "
            "county amendments adopted by {city} affecting {discipline}. "
            "For a Canada project: the provincial or territorial "
            "construction code in force (the National Building Code and "
            "National Fire Code of Canada as provincially adopted, or the "
            "province's own code) and its edition, plus municipal bylaws "
            "affecting {discipline}. In both countries: (c) retrieve the "
            "adopting instrument's referenced-standards table itself and "
            "report the edition year for each standard that {discipline} "
            "specifications conventionally cite — do not infer editions "
            "from the model-code year; (d) the current published edition "
            "of each such standard, so drafting can distinguish the legal "
            "minimum from current-edition practice; (e) licensing or "
            "professional-seal requirements for {discipline} designers or "
            "installers the specifications must reflect. Prefer official "
            "adoption sources: the state or provincial code agency and the "
            "municipal code of {city}."
        ),
    ),
    ResearchDimension(
        dimension_id="ahj_requirements",
        title="Authority-having-jurisdiction requirements",
        max_searches=32,
        max_fetches=10,
        prompt_template=(
            "Identify every authority having jurisdiction over "
            "{discipline} work for a project in {city}, "
            "{state_or_province}, {country} — assume multiplicity "
            "(building department, specialty inspection authorities, the "
            "fire marshal where the discipline touches life safety, and "
            "utility purveyors as their own authorities) — and any "
            "published requirements construction specifications should "
            "reflect: plan submittal and shop-drawing requirements for "
            "{discipline} work; permits and required inspections; "
            "witnessed acceptance or commissioning tests; utility "
            "connection, metering, and interconnection or backflow rules "
            "where {discipline} interfaces a utility; and closeout "
            "documentation the AHJ requires. Mark process/schedule facts "
            "as process advisories rather than spec requirements."
        ),
    ),
    ResearchDimension(
        dimension_id="client_standards",
        title="Owner / client and insurer standards",
        max_searches=24,
        max_fetches=8,
        prompt_template=(
            "First determine whether {client_name} publishes design and "
            "construction standards, master specifications, or an insurer "
            "review posture (FM Global or a named risk consultancy) that "
            "applies to {discipline} work. Then identify published "
            "{client_name} standards, guidelines, or public "
            "planning/permit filings affecting {discipline} for projects "
            "like the one in {city}; known {client_name} preferences for "
            "systems, products, or manufacturers in {discipline}; and "
            "sustainability programs {client_name} pursues that affect "
            "{discipline} specifications. Report only what you can ground "
            "in retrievable sources; where client standards are "
            "confidential, say so explicitly rather than guessing."
        ),
    ),
    ResearchDimension(
        dimension_id="site_environment",
        title="Site and environmental factors",
        max_searches=16,
        max_fetches=8,
        prompt_template=(
            "Identify site and environmental factors for {city}, "
            "{state_or_province}, {country} that {discipline} "
            "specifications must account for: the seismic design "
            "parameters under the hazard data referenced by the governing "
            "code (ASCE 7 in the USA; the National Building Code's "
            "climatic and seismic data in Canada), including the official "
            "lookup tool for the location; climatic design data bearing "
            "on {discipline} (winter/summer design temperatures, wind, "
            "snow, rain, freeze exposure, frost depth); utility service "
            "reliability and characteristics where {discipline} depends "
            "on them; and local environmental or water-use regulations "
            "affecting {discipline} work. Prefer the governing code's own "
            "climatic tables and official hazard tools."
        ),
    ),
)


GENERIC = SpecModule(
    module_id="generic",
    display_name="Generic — Any Discipline (USA & Canada)",
    description=(
        "Authors CSI SectionFormat sections in any discipline for "
        "projects in the USA and Canada. Pins no standard editions — "
        "every edition in effect is recorded per-project with its stated "
        "basis."
    ),
    basis=GENERIC_BASIS,
    section_catalog=(),
    interview_playbook=_INTERVIEW_PLAYBOOK,
    drafting_persona=_DRAFTING_PERSONA,
    domain_conventions=_DOMAIN_CONVENTIONS,
    lint_extra_marker_patterns=(
        # Owner-master remnants — discipline-agnostic, shared with the
        # hyperscale module's vocabulary.
        r"\bXXXX+\b",
        r"\[\s*PROJECT\s+NAME\s*\]",
        r"\[\s*OWNER\s*\]",
    ),
    research_dimensions=_RESEARCH_DIMENSIONS,
    research_persona=_RESEARCH_PERSONA,
    compliance_persona=_COMPLIANCE_PERSONA,
    open_catalog=True,
)
