"""The domain-neutral general authoring module (any CSI section, any discipline).

Build-a-Spec was seeded with one specialized module (``hyperscale_fire``,
Division 21). This module is its opposite: a domain-neutral configuration that
authors **any** CSI SectionFormat section in **any** discipline. It carries no
fixed section catalog and no pinned standards basis — the section is whatever the
user names (set free-form on the section header), and the standards in effect come
from the section's discipline, the AHJ, and any ``set_standard_edition`` overrides
the user or grounded research records.

It is the default module (``registry.DEFAULT_MODULE``): a fresh session is not
boxed into any one discipline's section list. The specialized ``hyperscale_fire``
module remains registered and selectable for its deep NFPA basis, playbook, and
research; this module is what a user reaches for to draft, say, a Division 22
plumbing section or a Division 26 electrical section without leaving the app.

Design notes:
- ``basis`` pins nothing (``StandardsBasis(label=...)`` with empty base codes and
  standards). ``standards.standards_context_block`` renders gracefully with no
  pins; the model drafts the PART 1 REFERENCES article from the discipline's own
  standards and records adopted editions via ``set_standard_edition``.
- ``section_catalog`` is empty — there is no fixed list. The prompt's catalog
  renderer treats an empty catalog as "author whatever section the user names."
- ``interview_playbook`` is the generic CSI SectionFormat spine (section identity,
  project identity, scope, references, submittals/QA, products, execution,
  closeout), defaults-first like every module.
- ``research_dimensions`` are generic and template the **section being authored**
  (``{section_number}`` / ``{section_title}``) plus the project profile, so a
  research run targets the user's actual discipline instead of a hardcoded one.
"""
from __future__ import annotations

from ..standards import StandardsBasis
from .base import InterviewTopic, ResearchDimension, SpecModule

# ---------------------------------------------------------------------------
# Standards basis: intentionally empty. No base codes, no pinned standards —
# the discipline and the jurisdiction decide what governs, and the user (or
# grounded research) records adopted editions through set_standard_edition.
# ---------------------------------------------------------------------------

GENERAL_BASIS = StandardsBasis(label="general-open")


# ---------------------------------------------------------------------------
# No fixed section catalog: this module authors any section the user names.
# ---------------------------------------------------------------------------

_SECTION_CATALOG: tuple = ()


# ---------------------------------------------------------------------------
# Generic defaults-first interview playbook — the CSI SectionFormat spine that
# applies to any discipline. Section identity and project identity are the
# non-defaultable minimum; every other topic carries a defensible default that
# "I don't know" applies and stamps `assumed`.
# ---------------------------------------------------------------------------

_INTERVIEW_PLAYBOOK = (
    InterviewTopic(
        "section_selection", "Section being written",
        guidance=(
            "Establish which CSI section is being authored — its MasterFormat "
            "number and title — and set the section header (replace on 'sec') "
            "immediately once known. If the user names only a discipline or a "
            "system, propose the conventional MasterFormat number and title."
        ),
        non_defaultable=True,
    ),
    InterviewTopic(
        "project_identity", "Project and client",
        guidance=(
            "Project name/type, location (city/state/country), and "
            "client/owner — who the section is for and where it will be "
            "built. Drives jurisdiction, adopted codes, and client standards."
        ),
        non_defaultable=True,
    ),
    InterviewTopic(
        "scope_summary", "Scope of work",
        guidance=(
            "What this section covers versus adjacent sections — the systems, "
            "products, and work included and explicitly excluded."
        ),
        default=(
            "Draft a PART 1 SUMMARY article stating the section's scope and "
            "related sections, inferred from the section title and the "
            "project description; stamp assumed until confirmed."
        ),
    ),
    InterviewTopic(
        "references_standards", "Reference standards",
        guidance=(
            "The codes and industry standards this section cites — the PART 1 "
            "REFERENCES article."
        ),
        default=(
            "Cite the codes and standards conventional for this section's "
            "discipline at their current published editions; record any "
            "jurisdiction-adopted edition with set_standard_edition, stating "
            "the adoption as its basis."
        ),
    ),
    InterviewTopic(
        "submittals_qa", "Submittals and quality assurance",
        guidance=(
            "Required submittals (product data, shop drawings, samples) and "
            "quality-assurance provisions (qualifications, certifications, "
            "mock-ups) for PART 1."
        ),
        default=(
            "Standard submittals (product data, shop drawings) and quality "
            "assurance (manufacturer and installer qualifications, applicable "
            "listings) for this product class; stamp assumed."
        ),
    ),
    InterviewTopic(
        "products_basis", "Products and materials",
        guidance=(
            "The materials, equipment, and basis-of-design for PART 2 — by "
            "performance and reference standard where possible."
        ),
        default=(
            "Specify products by performance and reference standard with an "
            "'or equal' provision, covering the materials typical for this "
            "section; stamp assumed."
        ),
    ),
    InterviewTopic(
        "execution_requirements", "Execution",
        guidance=(
            "Installation, examination, and field-quality provisions for "
            "PART 3."
        ),
        default=(
            "Install per the manufacturer's instructions and the referenced "
            "standards; include examination, installation, and field quality "
            "control articles; stamp assumed."
        ),
    ),
    InterviewTopic(
        "closeout", "Closeout",
        guidance=(
            "Closeout submittals, operation and maintenance data, warranties, "
            "and demonstration."
        ),
        default=(
            "Standard closeout for this product class — operation and "
            "maintenance manuals, warranty, and demonstration; stamp assumed."
        ),
    ),
)


_DRAFTING_PERSONA = (
    "You are Build-a-Spec, an expert construction-specification writer. You "
    "author CSI SectionFormat specification sections in whatever discipline "
    "and section the user is working on — mechanical, plumbing, electrical, "
    "fire protection, architectural, structural, civil, or any other. "
    "Establish the section from the user (its MasterFormat number and title), "
    "then draft to the conventions, codes, and standards of that discipline "
    "through focused dialogue. You are not limited to any fixed catalog of "
    "sections; author whatever section the user needs."
)


# No basis placeholders here — the general basis pins nothing, so the
# conventions must format cleanly against an empty basis (see
# base._validate_prompt_slots). Keep the text free of literal braces.
_DOMAIN_CONVENTIONS = """\
- Match the terminology, reference standards, and structure conventional for the section's discipline and MasterFormat division.
- Cite standards at their current published editions unless a jurisdiction-adopted edition has been recorded; never assert a code adoption you have not been given.
- Coordinate scope with the adjacent sections in the same division — state inclusions and exclusions explicitly rather than duplicating a neighboring section.
- Use the three-part SectionFormat structure appropriate to the discipline; not every article applies to every section, so include what the work requires and omit what it does not."""


# ---------------------------------------------------------------------------
# Generic research: persona + dimensions that template the section being
# authored ({section_number} / {section_title}) plus the project profile, so a
# run targets the user's actual discipline. The engine threads the live section
# header into the template kwargs at run start (research/engine.py); the
# section_number/section_title placeholders are registration-validated against
# base._DUMMY_PROFILE_FORMAT_KWARGS.
# ---------------------------------------------------------------------------

_RESEARCH_PERSONA = (
    "You are a construction code-research assistant. You research the codes, "
    "standards, authority-having-jurisdiction requirements, and owner/client "
    "design standards that govern a specification section so its author can "
    "draft to them. You report only requirements you can support with sources "
    "you actually retrieved, and you clearly separate verified facts from "
    "general industry practice."
)

_COMPLIANCE_PERSONA = (
    "You are a code-compliance reviewer for construction specification "
    "sections. You evaluate whether a draft section correctly represents the "
    "project's governing codes, local amendments, authority-having-"
    "jurisdiction requirements, and client standards as researched for this "
    "project and discipline."
)

_RESEARCH_DIMENSIONS = (
    ResearchDimension(
        dimension_id="governing_codes",
        title="Governing codes and standards",
        max_searches=32,
        max_fetches=10,
        prompt_template=(
            "Determine the governing codes and industry standards for CSI "
            "Section {section_number} {section_title} on a project in {city}, "
            "{state_or_province}, {country}. Identify: (a) the building, "
            "mechanical, plumbing, electrical, energy, or fire code editions "
            "in force for this discipline and their model-code basis with "
            "effective dates; (b) any state, county, or municipal amendments "
            "affecting this section's work; (c) the industry standards (e.g. "
            "ASTM, ASHRAE, ASME, NFPA, UL, ANSI, IEEE, or the discipline's own "
            "bodies) the specifications should cite and the edition each is "
            "adopted at — retrieve the adopting instrument's referenced-"
            "standards table rather than inferring editions from the model-"
            "code year; (d) the current published edition of each such "
            "standard so drafting can distinguish the legal minimum from "
            "current-edition practice; (e) licensing or certification "
            "requirements for the trades or design professionals the section "
            "must reflect. Prefer official adoption sources: the state "
            "building-code agency and the municipal code of {city}."
        ),
    ),
    ResearchDimension(
        dimension_id="ahj_requirements",
        title="Authority-having-jurisdiction requirements",
        max_searches=24,
        max_fetches=8,
        prompt_template=(
            "Identify every authority having jurisdiction over the work of "
            "CSI Section {section_number} {section_title} for a project in "
            "{city}, {state_or_province}, {country} — assume multiplicity "
            "(building department, discipline inspector, utility purveyor, "
            "fire marshal where relevant) — and any published requirements "
            "the construction specifications should reflect: plan-submittal "
            "and shop-drawing requirements, permit and inspection "
            "requirements, required witnessed or acceptance tests, connection "
            "and access requirements, and closeout documentation the AHJ "
            "requires. Mark process/schedule facts as process advisories "
            "rather than spec requirements."
        ),
    ),
    ResearchDimension(
        dimension_id="client_standards",
        title="Owner / client standards",
        max_searches=20,
        max_fetches=8,
        prompt_template=(
            "Identify published design and construction standards of "
            "{client_name} that apply to the work of CSI Section "
            "{section_number} {section_title}: public compliance, "
            "trust-center, or sustainability documentation describing "
            "requirements for this discipline; public planning or permit "
            "filings for {client_name} projects (including in {city}) with "
            "specifics relevant to this section; and any known {client_name} "
            "preferences, basis-of-design products, or performance standards "
            "for this scope. Report only what you can ground in retrievable "
            "sources; where owner standards are confidential, say so "
            "explicitly rather than guessing."
        ),
    ),
    ResearchDimension(
        dimension_id="site_environment",
        title="Site and environmental factors",
        max_searches=16,
        max_fetches=6,
        prompt_template=(
            "Identify site and environmental factors for {city}, "
            "{state_or_province}, {country} that the work of CSI Section "
            "{section_number} {section_title} must account for: climate, "
            "seismic, or exposure conditions the governing code ties to this "
            "discipline (with the official hazard-lookup or climatic-data "
            "source); utility service characteristics relevant to the "
            "section; and any local environmental or resource regulations "
            "that affect this section's materials, equipment, or testing. "
            "Report the governing code editions and official data sources you "
            "retrieve, and mark unverifiable leads plainly."
        ),
    ),
)


GENERAL = SpecModule(
    module_id="general",
    display_name="General — Any CSI Section",
    description=(
        "Authors any CSI SectionFormat section in any discipline. No fixed "
        "section catalog and no pinned standards basis — the section, its "
        "discipline, and its standards are whatever the project requires."
    ),
    basis=GENERAL_BASIS,
    section_catalog=_SECTION_CATALOG,
    interview_playbook=_INTERVIEW_PLAYBOOK,
    drafting_persona=_DRAFTING_PERSONA,
    domain_conventions=_DOMAIN_CONVENTIONS,
    research_dimensions=_RESEARCH_DIMENSIONS,
    research_persona=_RESEARCH_PERSONA,
    compliance_persona=_COMPLIANCE_PERSONA,
)
