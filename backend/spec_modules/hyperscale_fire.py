"""The hyperscale data-center fire-suppression authoring module (Div 21, USA).

Content seeded from Claude-Spec-Critic ``src/modules/datacenter_fire.py``
(the review-side sibling of this module) — code basis, domain vocabulary,
and the research dimensions — repointed at *authoring*: instead of severity
anchors and reviewer personas, this module carries the section catalog, the
defaults-first interview playbook, and the drafting prompt slots.

**Edition posture (differs from the Spec Critic module deliberately):** the
review module pins the editions the 2024 I-codes *reference* (NFPA 13-2022
et al.) because it audits specs against a stated code basis. This authoring
module pins the **current published editions** (NFPA 13-2025 first among
them) as drafting defaults, per the frozen project decision: NFPA 13-2025
is the default, and a jurisdiction-adopted earlier edition overrides it
once known — never silently, recorded with its adoption basis through the
``set_standard_edition`` operation. Every pin below was verified against
publisher/retailer listings in July 2026; receipts in
``docs/standards_provenance.md``.

``research_dimensions`` + ``research_persona`` drive the Phase 4
requirements-research fan-out (``backend/research/``): four grounded
web-search dimensions researching the project's jurisdiction, AHJ, client,
and site once the interview records a complete project profile.
"""
from __future__ import annotations

from ..standards import BaseCode, StandardEdition, StandardsBasis
from .base import InterviewTopic, ResearchDimension, SectionDef, SpecModule

# ---------------------------------------------------------------------------
# Standards basis: current published editions (verified 2026-07), with the
# 2024 I-codes as model-code context. See docs/standards_provenance.md for
# the per-entry receipts. NOTE (verified 2026-07): NFPA 13D/13R/24/291 were
# NOT consolidated into NFPA 13-2025 — each has its own current edition;
# NFPA 24-2025 remains a separate pin.
# ---------------------------------------------------------------------------

_VERIFIED = "verified 2026-07 (web)"

HYPERSCALE_FIRE_BASIS = StandardsBasis(
    label="hyperscale-fire-current",
    base_codes=(
        # Model-code context only — the governing state/local adoption is
        # per-project data (stated by the user, or researched in Phase 4).
        BaseCode(
            "ibc", "IBC", "2024",
            source="ICC 2024 International Building Code — current published "
            "edition (2027 in development)",
        ),
        BaseCode(
            "ifc", "IFC", "2024",
            source="ICC 2024 International Fire Code — current published "
            "edition",
        ),
    ),
    standards=(
        StandardEdition(
            "NFPA 13", "2025",
            title="Standard for the Installation of Sprinkler Systems",
            source=f"{_VERIFIED}: nfpa.org product listing, UpCodes, NFSA "
            "TechNotes 2024-07 (issued fall 2024). "
            "See docs/standards_provenance.md.",
        ),
        StandardEdition(
            "NFPA 14", "2024",
            title="Standard for the Installation of Standpipe and Hose "
            "Systems",
            source=f"{_VERIFIED}: NFSA TechNotes 2024-10 'Updates to NFPA "
            "14' + publisher listings.",
        ),
        StandardEdition(
            "NFPA 20", "2025",
            title="Standard for the Installation of Stationary Pumps for "
            "Fire Protection",
            source=f"{_VERIFIED}: NFPA 20-2025 publisher/preview listings.",
        ),
        StandardEdition(
            "NFPA 22", "2023",
            title="Standard for Water Tanks for Private Fire Protection",
            source=f"{_VERIFIED}: nfpa.org blog 2024-10 + "
            "link.nfpa.org/all-publications/22/2023; no newer edition found "
            "2026-07.",
        ),
        StandardEdition(
            "NFPA 24", "2025",
            title="Standard for the Installation of Private Fire Service "
            "Mains and Their Appurtenances",
            source=f"{_VERIFIED}: NFPA catalog + ANSI webstore NFPA 24-2025. "
            "Separate standard — NOT consolidated into NFPA 13-2025.",
        ),
        StandardEdition(
            "NFPA 25", "2026",
            title="Standard for the Inspection, Testing, and Maintenance of "
            "Water-Based Fire Protection Systems",
            source=f"{_VERIFIED}: QRFS 'NFPA 25 2026 Edition' + "
            "Amazon/AtHomePrep listings (issued fall 2025).",
        ),
        StandardEdition(
            "NFPA 72", "2025",
            title="National Fire Alarm and Signaling Code",
            source=f"{_VERIFIED}: nfpa.org product page NFPA 72-2025.",
        ),
        StandardEdition(
            "NFPA 75", "2024",
            title="Standard for the Fire Protection of Information "
            "Technology Equipment",
            source=f"{_VERIFIED}: ANSI webstore + UpCodes NFPA 75-2024. "
            "Owner-invoked rather than code-mandated.",
        ),
        StandardEdition(
            "NFPA 76", "2024",
            title="Standard for the Fire Protection of Telecommunications "
            "Facilities",
            source=f"{_VERIFIED}: ANSI webstore + UpCodes NFPA 76-2024. "
            "Owner-invoked rather than code-mandated.",
        ),
        StandardEdition(
            "NFPA 291", "2025",
            title="Recommended Practice for Water Flow Testing and Marking "
            "of Hydrants",
            source=f"{_VERIFIED}: ANSI webstore NFPA 291-2025.",
        ),
        StandardEdition(
            "NFPA 2001", "2025",
            title="Standard on Clean Agent Fire Extinguishing Systems",
            source=f"{_VERIFIED}: ANSI webstore + publisher listings "
            "NFPA 2001-2025.",
        ),
        StandardEdition(
            "NFPA 855", "2026",
            title="Standard for the Installation of Stationary Energy "
            "Storage Systems",
            source=f"{_VERIFIED}: Telgian / ICC shop / Energy-Storage.News "
            "NFPA 855-2026.",
        ),
    ),
)


# ---------------------------------------------------------------------------
# Section catalog. The lead section (first entry) carries the full playbook;
# siblings are draftable from the module's conventions, pins, and the
# model's domain knowledge, gaining dedicated playbooks incrementally.
# ---------------------------------------------------------------------------

_SECTION_CATALOG = (
    SectionDef(
        "21 13 13", "Wet-Pipe Sprinkler Systems",
        scope_note=(
            "Wet-pipe protection for data hall support spaces, "
            "administration, corridors, and loading docks. Preaction "
            "protection of white space and electrical rooms belongs to "
            "21 13 19, not here."
        ),
        playbook_depth="full",
    ),
    SectionDef(
        "21 13 16", "Dry-Pipe Sprinkler Systems",
        scope_note=(
            "Dry-pipe protection of unheated areas (loading docks, "
            "generator yards, exterior canopies). Address low-point drains, "
            "air/nitrogen supply, and delivery-time compliance."
        ),
    ),
    SectionDef(
        "21 13 19", "Preaction Sprinkler Systems",
        scope_note=(
            "Single- or double-interlock preaction for data halls and "
            "electrical rooms. Detection/releasing interfaces are "
            "coordinated with (not specified in) Division 28."
        ),
    ),
    SectionDef(
        "21 30 00", "Fire Pumps",
        scope_note=(
            "Electric or diesel fire pumps, controllers, and accessories "
            "per NFPA 20. Address redundancy (N+1 where the owner requires "
            "it), churn/flow test provisions, and driver fuel/power."
        ),
    ),
    SectionDef(
        "21 11 00", "Facility Fire-Suppression Water-Service Piping",
        scope_note=(
            "Private fire service mains, backflow prevention, hydrants, and "
            "FDCs per NFPA 24 — the site-side supply feeding risers and "
            "pumps; coordinate the utility point of connection with civil."
        ),
    ),
    SectionDef(
        "21 12 00", "Fire-Suppression Standpipes",
        scope_note=(
            "Class I standpipes in stair towers and at roof steps per "
            "NFPA 14; coordinate pressure zones with the pump section."
        ),
    ),
    SectionDef(
        "21 05 00", "Common Work Results for Fire Suppression",
        scope_note=(
            "Division-wide requirements: quality assurance, licensing, "
            "seismic bracing design responsibility, penetrations, "
            "identification, and coordination drawings."
        ),
    ),
    SectionDef(
        "21 22 00", "Clean-Agent Fire-Extinguishing Systems",
        scope_note=(
            "Owner-driven clean-agent protection (NFPA 2001) of specific "
            "rooms where invoked; verify the owner basis before including."
        ),
    ),
)


# ---------------------------------------------------------------------------
# Defaults-first interview playbook (lead section: 21 13 13). Every
# defaultable topic carries its recommended default — "I don't know" applies
# it and stamps the blocks `assumed`. Non-defaultable topics are the
# interview's hard minimum.
# ---------------------------------------------------------------------------

_INTERVIEW_PLAYBOOK = (
    InterviewTopic(
        "section_selection", "Section being written",
        guidance=(
            "Establish which catalog section is being authored; set the "
            "section header immediately once known."
        ),
        non_defaultable=True,
    ),
    InterviewTopic(
        "project_identity", "Project location and client",
        guidance=(
            "City/state, client/owner, and campus or building type. Drives "
            "jurisdiction (adopted codes), seismic, freeze exposure, and "
            "client-standard applicability."
        ),
        non_defaultable=True,
    ),
    InterviewTopic(
        "hazard_picture", "Basic hazard picture",
        guidance=(
            "What the wet-pipe system protects in this building: which "
            "spaces are wet-protected vs preaction (white space, electrical "
            "rooms) vs unprotected/other. Without this the section scope "
            "cannot stand."
        ),
        non_defaultable=True,
    ),
    InterviewTopic(
        "ahj_insurer", "AHJ and insurer involvement",
        guidance=(
            "Identify the fire-code AHJ and whether FM Global (or another "
            "insurer) reviews the project — FM involvement pulls FM data "
            "sheets into the design basis."
        ),
        default=(
            "Municipal fire marshal as AHJ under the locally adopted fire "
            "code; no FM Global involvement — NFPA 13-2025 governs design."
        ),
    ),
    InterviewTopic(
        "occupancy_classification", "Occupancy classification and density",
        guidance=(
            "Classify each wet-protected space per NFPA 13-2025 and state "
            "the design density/area."
        ),
        default=(
            "Light Hazard (0.10 gpm/sq ft over 1,500 sq ft) for "
            "administrative and office areas; Ordinary Hazard Group 1 "
            "(0.15/1,500) for mechanical/electrical support spaces and "
            "corridors; Ordinary Hazard Group 2 (0.20/1,500) for loading "
            "docks and storage-adjacent areas, per NFPA 13-2025 occupancy "
            "classifications."
        ),
    ),
    InterviewTopic(
        "water_supply", "Water supply basis",
        guidance=(
            "Flow test data (static/residual/flow, date, location) or its "
            "absence; municipal vs tank-and-pump supply."
        ),
        default=(
            "Municipal supply assumed adequate pending a current flow test "
            "— carry '[TBD: flow test data]' as a needs_input block "
            "(hydraulic calculations cannot finalize without it)."
        ),
    ),
    InterviewTopic(
        "seismic", "Seismic protection",
        guidance=(
            "Whether sway bracing applies and who designs it (delegated "
            "design is the norm)."
        ),
        default=(
            "Seismic protection per NFPA 13-2025 Chapter 18 where the "
            "project's seismic design category requires it; bracing design "
            "delegated to the sprinkler contractor's engineer — '[TBD: "
            "seismic design category]' until the structural basis is known."
        ),
    ),
    InterviewTopic(
        "pipe_materials", "Pipe materials and joining",
        guidance=(
            "Materials by size range and joining methods; hyperscale norms "
            "favor steel throughout."
        ),
        default=(
            "Black steel per ASTM A53/A135/A795: Schedule 40 threaded for "
            "2 in. and smaller, Schedule 10 roll-grooved for 2-1/2 in. and "
            "larger; grooved couplings and fittings; no CPVC; galvanized "
            "only where the owner standard requires it for specific "
            "exposures."
        ),
    ),
    InterviewTopic(
        "sprinklers", "Sprinkler types and finishes",
        guidance="Response type, K-factors, coverage, and finishes by space.",
        default=(
            "Quick-response sprinklers throughout; K5.6 upright/pendent in "
            "exposed areas, concealed pendents in finished ceilings; "
            "standard-coverage; intermediate-temperature where near heat "
            "sources; corrosion-resistant heads in exterior/damp locations."
        ),
    ),
    InterviewTopic(
        "valves_monitoring", "Valves, monitoring, and zoning",
        guidance=(
            "Control valve supervision, waterflow zoning, and the Division "
            "28 monitoring interface."
        ),
        default=(
            "Listed indicating control valves with tamper switches at each "
            "zone; vane-type waterflow switches per floor/zone; all "
            "supervision and alarm monitoring by the Division 28 fire alarm "
            "system (coordinate, do not specify panels here)."
        ),
    ),
    InterviewTopic(
        "inspector_test_drains", "Test and drain provisions",
        guidance="Inspector's test connections, main/auxiliary drains.",
        default=(
            "Combination inspector's-test-and-drain assemblies at each "
            "zone, discharging to safe locations; main drain at each riser "
            "sized per NFPA 13-2025."
        ),
    ),
    InterviewTopic(
        "submittals_qa", "Submittals and quality assurance",
        guidance=(
            "Working plans, hydraulic calculations, product data, and "
            "installer qualifications."
        ),
        default=(
            "Working plans and hydraulic calculations per NFPA 13-2025 "
            "prepared under a NICET Level III/IV layout technician; "
            "licensed fire sprinkler contractor; product data for all "
            "listed components; AHJ approval before fabrication."
        ),
    ),
    InterviewTopic(
        "testing_acceptance", "Testing and acceptance",
        guidance="Hydrostatic testing, flushing, and acceptance documentation.",
        default=(
            "Hydrostatic test at 200 psi for 2 hours (or 50 psi above "
            "static where higher) per NFPA 13-2025; underground flushing "
            "per NFPA 24-2025 documented before connection; completed "
            "contractor's material and test certificates at closeout."
        ),
    ),
    InterviewTopic(
        "closeout", "Closeout and spares",
        guidance="Spare sprinklers, O&M data, and owner ITM handoff.",
        default=(
            "Spare sprinkler cabinet stocked per NFPA 13-2025; O&M data "
            "and as-builts at closeout; owner inspection, testing, and "
            "maintenance obligations per NFPA 25-2026 referenced for "
            "information."
        ),
    ),
)


_DRAFTING_PERSONA = (
    "You are Build-a-Spec, an expert construction-specification writer "
    "helping a fire-sprinkler designer author CSI SectionFormat Division 21 "
    "fire-suppression specification sections for hyperscale data-center "
    "projects in the USA through focused dialogue."
)


_DOMAIN_CONVENTIONS = """\
- Model-code context is IBC {ibc} / IFC {ifc}; the governing adoption is the project jurisdiction's and is per-project data — never assert a state or local adoption you have not been given.
- Data-center realities: preaction protection over white space and electrical rooms where the owner requires it (its own section — 21 13 19); VESDA/aspirating detection and releasing interlocks belong to Division 28 (coordinate, don't specify them here); seismic bracing per NFPA 13-2025 Chapter 18 with delegated design; nitrogen inerting or corrosion monitoring commonly required on dry/preaction piping; FM Global data sheets join the design basis when the project is FM-insured.
- Phased fit-out is normal: distinguish core-and-shell scope from tenant/fit-out scope when the user indicates phasing.
- BESS rooms trigger NFPA 855 coordination; clean-agent scope (NFPA 2001) only where the owner invokes it.
- Water supply interfaces: municipal point of connection, backflow prevention, and private mains live in 21 11 00 / civil scope — reference, don't duplicate."""


# ---------------------------------------------------------------------------
# Phase 4 research: persona + dimensions, adapted from Spec Critic's
# ``datacenter_fire`` module (US-focused: this authoring module targets USA
# projects; Canadian support would arrive as its own module). The persona is
# the first line of the research system prompt; the engine wraps it with the
# byte-stable research protocol block.
# ---------------------------------------------------------------------------

_RESEARCH_PERSONA = (
    "You are a fire-protection code-research assistant for hyperscale "
    "data-center projects in the USA. You research jurisdiction-specific "
    "code adoptions, local amendments, authority-having-jurisdiction "
    "requirements, and owner/client design standards so a specification "
    "author can draft to them. You report only requirements you can support "
    "with sources you actually retrieved, and you clearly separate verified "
    "facts from industry practice."
)

# Adapted from Spec Critic datacenter_fire._COMPLIANCE_PERSONA for the
# single-section drafting audit.
_COMPLIANCE_PERSONA = (
    "You are a code-compliance reviewer for hyperscale data-center "
    "fire-suppression specifications. You evaluate whether a draft "
    "Division 21 section correctly represents the project's governing "
    "codes, local amendments, AHJ requirements, and client standards as "
    "researched for this project."
)

_RESEARCH_DIMENSIONS = (
    ResearchDimension(
        dimension_id="governing_codes",
        title="Governing building and fire codes",
        max_searches=24,
        max_fetches=8,
        prompt_template=(
            "Determine the governing building and fire codes for a new "
            "hyperscale data-center project in {city}, {state_or_province}, "
            "{country}. Identify: (a) the state building and fire code "
            "editions currently in force and their model-code basis (IBC/IFC "
            "year) with effective dates; (b) any municipal or county "
            "amendments adopted by {city} affecting fire suppression, fire "
            "pumps, water supply, or fire alarm; (c) the editions of NFPA "
            "13, 14, 20, 22, 24, 25, and 72 referenced by that adoption, "
            "including any state amendments; (d) licensing requirements for "
            "sprinkler contractors or design professionals the "
            "specifications must reflect; (e) the fire/operations code "
            "applicable to the completed facility and the ITM standard "
            "editions it references (these frequently differ from the "
            "building code's referenced editions); (f) retrieve the "
            "adopting instrument's referenced-standards table itself and "
            "report the edition year for each standard the specifications "
            "cite — do not infer editions from the model-code year; (g) the "
            "current published edition of each standard, so drafting can "
            "distinguish the legal minimum from current-edition practice. "
            "Prefer official adoption sources: the state fire marshal or "
            "building-code agency and the municipal code of {city}."
        ),
    ),
    ResearchDimension(
        dimension_id="ahj_requirements",
        title="Authority-having-jurisdiction requirements",
        max_searches=20,
        max_fetches=6,
        prompt_template=(
            "Identify every authority having jurisdiction over fire "
            "protection for a data-center project in {city}, "
            "{state_or_province}, {country} — assume multiplicity (fire "
            "marshal, building department, water purveyor) — and any "
            "published requirements construction specifications should "
            "reflect: plan submittal and shop-drawing requirements for "
            "sprinkler, fire pump, and standpipe work; hydrant flow test "
            "and water-supply data requirements including permits and "
            "seasonal windows; required witnessed acceptance tests; fire "
            "department connection and access requirements; local policies "
            "on pre-action systems, aspirating smoke detection, or "
            "clean-agent systems; and closeout ITM documentation the AHJ "
            "requires. Treat the water purveyor as its own authority: "
            "engineering-seal requirements for service drawings, metering "
            "rules for fire lines, backflow device class and tester "
            "registration, main flushing/disinfection sign-off, and any "
            "water-allocation constraints affecting data centers. Mark "
            "process/schedule facts as process advisories rather than spec "
            "requirements."
        ),
    ),
    ResearchDimension(
        dimension_id="client_standards",
        title="Owner / client and insurer standards",
        max_searches=12,
        max_fetches=4,
        prompt_template=(
            "First determine who reviews risk for {client_name} projects — "
            "FM Global, a named risk consultancy, or self-insurance — since "
            "this decides whether FM data sheets are mandatory or "
            "benchmark-only. Then identify published design and "
            "construction standards of {client_name} that apply to "
            "data-center fire protection: public compliance or "
            "trust-center documentation describing fire protection; public "
            "planning/permit filings for {client_name} data-center "
            "campuses (including in {city}) with fire-protection "
            "specifics; which FM data sheets are commonly invoked for data "
            "centers when FM applies; known {client_name} requirements or "
            "preferences for pre-action versus wet systems, aspirating "
            "smoke detection, clean-agent systems, and BESS protection; "
            "and sustainability programs {client_name} pursues that affect "
            "fire-protection specifications. Report only what you can "
            "ground in retrievable sources; where owner standards are "
            "confidential, say so explicitly rather than guessing."
        ),
    ),
    ResearchDimension(
        dimension_id="site_environment",
        title="Site and environmental factors",
        max_searches=8,
        max_fetches=4,
        prompt_template=(
            "Identify site and environmental factors for {city}, "
            "{state_or_province}, {country} that fire-suppression "
            "specifications must account for: the seismic design category "
            "under the ASCE 7 edition referenced by the governing building "
            "code, including the official hazard-lookup tool for the "
            "location; freeze exposure that would require dry-pipe, "
            "pre-action, or antifreeze protection in unheated areas, with "
            "January design temperatures from the code's climatic data; "
            "minimum burial/frost-cover depth for water mains per the "
            "local utility or code; municipal water-supply reliability and "
            "published static/residual pressure ranges, and whether "
            "on-site fire-water storage is commonly required; water-use or "
            "drought regulations affecting fire-protection water storage "
            "and discharge testing; and any current municipal or regional "
            "actions on water allocation for data centers that affect "
            "fire-water supply or storage decisions."
        ),
    ),
)


HYPERSCALE_FIRE = SpecModule(
    module_id="hyperscale_fire",
    display_name="Hyperscale Data Center — Fire Suppression (Div 21, USA)",
    description=(
        "Authors Division 21 fire-suppression sections for hyperscale "
        "data-center projects in the USA, NFPA 13-2025 basis, wet-pipe "
        "(21 13 13) lead section."
    ),
    basis=HYPERSCALE_FIRE_BASIS,
    section_catalog=_SECTION_CATALOG,
    interview_playbook=_INTERVIEW_PLAYBOOK,
    drafting_persona=_DRAFTING_PERSONA,
    domain_conventions=_DOMAIN_CONVENTIONS,
    lint_extra_marker_patterns=(
        # Owner-master remnants seen in hyperscale packages.
        r"\bXXXX+\b",
        r"\[\s*PROJECT\s+NAME\s*\]",
        r"\[\s*OWNER\s*\]",
    ),
    research_dimensions=_RESEARCH_DIMENSIONS,
    research_persona=_RESEARCH_PERSONA,
    compliance_persona=_COMPLIANCE_PERSONA,
)
