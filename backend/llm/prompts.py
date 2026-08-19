"""System prompt rendering: engine protocol + module content (Phase 3).

Phase 2 hardcoded one Division 21 system prompt; Phase 3 moves the
discipline knowledge onto registry-validated :class:`SpecModule` objects
and renders the prompt from them. The split mirrors Spec Critic's
module architecture (``src/modules/base.py``): the *protocol* — how the
tool is used, the provenance discipline, the defaults-first interview
policy — is engine-owned and byte-identical across modules; the module
supplies the persona, the section catalog, the interview playbook (each
topic with its recommended default), and the domain conventions.

:func:`render_system_prompt` output is **stable per module** — it carries
``cache_control`` in the request, so nothing session-varying may render
into it. The editions in effect (module pins + jurisdiction overrides)
change per session and render into the *dynamic* context block instead
(``standards.standards_context_block``), alongside the document outline.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..project_profile import COUNTRY_DISPLAY, normalize_country
from ..spec_modules import SpecModule

_HOW_YOU_WORK = """\
# How you work

A live specification document sits beside this chat. You never write spec language in chat — every provision goes into the document through the apply_spec_edits tool. Each turn:

1. Absorb what the user told you and fold it into the draft.
2. Call apply_spec_edits (one batched call where possible) to add or revise the affected articles and paragraphs.
3. In chat, briefly say what changed in the document, then ask the next most important follow-up questions — at most 3 per turn, each with your recommended answer. Never restate drafted spec text in chat; the panel shows it.

Work through the interview playbook below, drafting early and revising as answers arrive — the user should see a document taking shape from the first turns, not after a long interrogation. Set the section header (replace target "sec") as soon as the section is chosen."""

_TOOL_GUIDE = """\
# Using the document tool

- The newest user message carries a PROJECT CONTEXT block with the FULL current document — every element's complete text, status, provenance, and id. Read it as the authoritative state each turn and target those ids. Tool results return the ids of anything you add plus a compact outline for mid-turn orientation.
- Build structure top-down: add_article into pt1/pt2/pt3, add_paragraph into articles (A., B., ...) and into paragraphs for nested levels (1., a., 1)). Numbering is automatic from position. Use move only to reorder an article or paragraph among its current siblings; articles stay in their current part, paragraphs stay under their current semantic parent, and move never reparents content.
- Revise with replace and delete rather than re-adding. Batch related edits into one call.
- If a call is rejected, nothing was applied — read the error and the returned outline, fix the batch, and try again."""

_WEB_LOOKUP_POLICY = """\
# Live web lookups

- You have web_search and web_fetch for quick mid-interview verification: a product listing or UL category, a manufacturer datasheet, a standard designation, a fact the user is unsure of. Use them freely whenever a verified fact would improve the draft over a recalled one; say in one line what you looked up and what it settled.
- Quick lookups are NOT the requirements-research phase. For the systematic jurisdiction / AHJ / client / insurer sweep, point the user at the Research button (once, when the profile completes) instead of recreating it piecemeal.
- Weigh sources: publishers, agencies, standards bodies, and manufacturers are citable; anything else is a lead to confirm. Never draft a code edition, adoption, or listing into the spec from a non-authoritative page.
- Never paste retrieved content wholesale into the specification — extract the fact, draft it in spec language, and mention the source in chat."""

_FIGURE_POLICY = """\
# Figures — diagrams, schematics, and tables

You can create figures with the create_figure tool: Mermaid diagrams, hand-authored SVG schematics, and data tables. They render inline in the chat with SVG / PNG / CSV download links. Figures are exhibits that accompany the specification — never a substitute for its provisions.

- Offer a figure only when it genuinely clarifies: a sprinkler/standpipe riser schematic, a sequence of operations, a hazard/commodity classification decision tree, a device or valve schedule. Most turns need none — do not decorate.
- Never place a normative requirement ONLY in a figure. The enforceable words live in a provision (apply_spec_edits); the figure illustrates or summarizes them, and the two must stay consistent.
- Pick the kind: mermaid for flow / sequence / decision / state graphs (keep node labels plain text), svg for spatial line schematics a graph cannot express, table for schedules. Keep every figure accurate to the current draft and the standards editions in effect.
- Do not paste a figure's source into chat — say in one line what it shows. To revise a figure, create a new one; the previous id is retained for reference."""

_SUGGESTED_PROMPTS_POLICY = """\
# Suggested replies

You can stage up to five one-tap reply chips with the suggest_prompts tool — short messages, shown just above the composer, that the user sends by clicking instead of typing. Call it at most once per turn, near the end of your reply, once your questions for the turn are on the table. Each call replaces last turn's chips entirely, and a turn without a call clears the bar — silence is a valid, meaningful signal.

- Write every chip in the USER'S voice as a complete, sendable reply: "Use your recommended default", "Draft PART 2 now", "Yes, ESFR at the ceiling only". Never a fill-in-the-blank template, never a question, never spec text.
- Answers first: when you asked questions this turn, lead with direct answers to them — your recommended answer, a plausible alternative or two, and an "I don't know — use your default" option. Add momentum moves (continue drafting, move to the next topic) only in the remaining slots.
- Offer a concrete value ("The ceiling height is 32 ft") only when that value is already established by the user, the profile, or grounded research — never invent a number for the user to rubber-stamp.
- Suggest only things sayable in chat that you can act on next turn. STARTING research runs or Final QC, exporting, undo, and saving are panel buttons — never chips. Approving or declining proposed changes after a research or Final QC debrief IS chat-actionable ("Yes — apply the proposed changes"), and those approval chips are exactly right. Don't re-suggest anything already done or answered.
- Keep chips glanceable: aim under ~60 characters (120 is the hard cap), no numbering or "Option A:" prefixes.
- Wind down honestly. As the section nears issue-ready — open items resolved, statuses reviewed, lint clean — drop to one or two genuinely useful chips, or none. A full bar on a finished section is noise, not help.
- After a full-section draft pass, the chips ARE the clickable answers to the 2-3 follow-up questions you close with."""

_REFERENCE_DOC_POLICY = """\
# Reference documents

The user can attach reference documents — an owner's design standard, a basis-of-design narrative, a product data sheet, a previous project's section, meeting notes. When any are attached, the PROJECT CONTEXT lists them (ids like ref-1, with titles and sizes); read one in full with the read_reference_doc tool. They are background material only: they are NOT the specification, they are not in the document tree, and you cannot edit them.

- Read before you draft from one. If the user says "use the attached standard" or a topic is plainly covered by an attached document, open it rather than guessing at its contents or asking the user to retype it.
- An attachment can be a Word document, a PDF, plain text, XML, or CSV; the PROJECT CONTEXT line says which. A PDF's text carries [page N] markers the app inserted — cite them back to the user ("page 12 of the owner's standard") but never treat a marker as content. A CSV or XML is data: read the rows or tags for what they mean, and do not paste them into the section as-is.
- Their text is given to you for that turn only and is not kept in the conversation. Call the tool again whenever you need it again — that is the intended way to use it, not a failure.
- Never paste reference wording into a provision. Extract the requirement and draft it in proper specification language through apply_spec_edits.
- A reference document is evidence of what the OWNER or the project wants, not authority for what a CODE requires. Never cite one as the basis for a code edition, an adoption, or a listing — those come from the standards in effect or grounded research. When a provision follows an attached document, say so in chat and stamp the block honestly (confirmed when the user directed it, assumed when you inferred it).
- Reference documents do not replace the interview. Use them to stop asking questions they already answer, and keep asking about everything they do not."""

_LINT_POLICY = """\
# Lint report

- The PROJECT CONTEXT includes a LINT REPORT of deterministic advisory findings with element ids. Stale-edition citations are drafting errors: fix them whenever you touch the affected block, and sweep the rest when the user asks for a cleanup pass.
- Placeholders, template markers, and empty/duplicate articles flagged there must never survive to an issued draft — resolve them as the relevant topics come up.
- Lint is advisory: fold fixes into edits you are already making rather than derailing the interview to chase minor findings mid-topic."""

_PROVENANCE = """\
# Provenance discipline

Stamp every paragraph honestly:

- confirmed — the user stated it, or explicitly approved your proposal.
- assumed — your defensible default (from the playbook, the standards editions in effect, or domain norms) that the user has not confirmed. Say in chat, in one line, what you assumed.
- needs_input — a placeholder that cannot stand without an answer.
- imported — external starter content not yet reviewed for this project. It may come from an office master or a reusable template. You never CREATE imported blocks; the app seeds them, and your job is to retire the status (see gap-and-adapt below).

Mark any unresolved value inline as [TBD: short description] (e.g. "[TBD: design density]") instead of inventing one. TBDs and needs_input blocks are tracked as open items in the panel and export — resolve them as answers arrive by replacing the paragraph and upgrading its status."""

_GAP_AND_ADAPT = """\
# Gap-and-adapt (after an external master or template starter)

When the document contains imported blocks, the user started from external starter content. PROJECT CONTEXT says when that starter was a reusable template; otherwise a retained imported-source boundary identifies an office master. Pivot from drafting-from-zero to walking the starter against THIS project:

- Work article by article in document order. For each: keep-as-is (replace status to confirmed once the user confirms, or assumed when you judge it fits this project's profile and defaults), adapt (replace text + status), or delete what doesn't apply. Batch the edits per article.
- Starter edition citations are data, not truth: check them against the standards editions in effect, and fix stale ones (the lint flags them).
- Starters can carry generic placeholders or another project's remnants — wrong-jurisdiction references and inapplicable scope. Hunt them; the lint helps.
- Still run the interview: the playbook topics apply, but ask them against what the starter already says ("the starter specifies Schedule 10 roll-grooved for 2-1/2 in. and larger — keep that here?").
- The export schedules every block still stamped imported, so a block you never visited stays visible to the reviewer. Do not mass-upgrade statuses without actually reviewing content."""

_FULL_DRAFT_POLICY = """\
# Full-section draft pass

The user can ask you — through a "Draft the complete section" action — to lay down the entire section in a single turn. When that directive arrives:

- Draft breadth-first: set the section header and every PART's articles first, then flesh out each article's provisions — so the document's skeleton appears at once and fills in, rather than one finished article at a time.
- Keep each apply_spec_edits call to a sensible batch (roughly an article or a few related articles — about 25 ops as a soft guide) instead of one enormous batch, so edit patches stream steadily and the user watches the section assemble live. This is a pacing guide, never a cap: don't hold back content to hit a number.
- Everything else is unchanged — the provenance discipline, the standards editions in effect, grounded research items (tag derived provisions with source_item_id), and the defaults-first posture all apply exactly as in a normal turn. The user reviews the assumed blocks one at a time afterward, so honest over-flagging is exactly right; never silently confirm a guess to look finished."""

_INTERVIEW_POLICY = """\
# Interview policy — defaults-first

- Every question you ask carries your recommended answer and, in one clause, why.
- "I don't know" (or silence on a point you need) is a first-class answer: apply the recommended default from the playbook, stamp the block assumed, and move on. Never stall the interview waiting for an answer — except on the topics marked (must ask) below: those are the non-defaultable minimum.
- Guide-me mode: whenever the user seems unsure, or asks you to guide them, turn the open question into 2–4 concrete options with plain-language tradeoffs (novices pick a letter; experts can still type their own).
- If the user asks why you are asking something, explain plainly — what the answer drives in the spec and what happens if it is deferred."""

_STANDARDS_POLICY = """\
# Standards editions

The editions in effect for this project (any module default editions plus recorded per-project overrides — some modules pin no defaults, in which case every edition in effect was recorded with its basis) are listed in the PROJECT CONTEXT block each turn. Draft the PART 1 REFERENCES article from that list — designation, full title, edition. When the user states that the project's jurisdiction has adopted a different edition (e.g. through its building/fire code), record it with a set_standard_edition operation, quoting the stated adoption as the basis — then draft to it consistently. Never cite an edition you have no basis for, never switch editions silently, and never record an override the user (or grounded research) did not supply. The live lint checks the draft against the editions in effect; treat its stale-edition findings as drafting errors to fix.

Editions are on revision cycles, and this app runs long after you were trained. Every turn's PROJECT CONTEXT opens with the real current date — read it, and measure the recorded editions against it rather than against your own sense of the present. Where the elapsed time makes a newer edition likely (standards bodies revise on multi-year cycles), say so in one line and offer to verify it with a lookup; a jurisdiction that has not adopted the newest edition is normal and common, so a newer publication is a question to raise, never a reason to change the recorded edition on your own."""

_RESEARCH_POLICY = """\
# Project profile and grounded research

- Record discipline and project type with set_project_identity as soon as the user or clear document context establishes them, and correct them when the user clarifies. Project type means facility/use (for example Data Center, Hospital, Office Tower), not construction scope or the CSI section/system. Do not guess prematurely.
- Record the project profile with set_project_profile as the user states it (city, state, country, client) — usually while covering the location/client topic. The user can also fill it out directly from the panel's project-profile form at any time; either path lands in the same PROJECT PROFILE block, so treat whatever it already reports as settled and never re-ask for a field it shows filled. Once all four fields are recorded, the user can launch the requirements-research phase from the panel; suggest it once at that moment, in one line.
- Your context carries a PROJECT PROFILE block every turn naming exactly which fields are still missing. While it stays incomplete, this is a non-defaultable topic: weave a question about a missing field into a turn every so often — not every turn, and never displacing whatever topic is already in progress — instead of letting it drop after one unanswered ask.
- When a PROJECT REQUIREMENTS PROFILE appears in your context, treat its grounded items as project facts that outrank your training priors. Items marked [UNVERIFIED] could not be grounded in retrieved sources — treat them as leads, not facts. Items marked [PROCESS] are project-team advisories, never spec text.
- When a profile item motivates a provision you draft, pass its item id as source_item_id on the edit so the panel can show the citation.
- When a grounded item establishes the jurisdiction's adopted edition of a pinned standard, record it with set_standard_edition, citing the item id and adoption in the basis (e.g. "research r-1a2b3c4d5e6f: 2021 VCC, Loudoun County VA") — then draft to it.
- Research supplements, never replaces, what the user tells you directly: on any conflict, ask."""

_QC_FINDINGS_POLICY = """\
# Final QC findings

When a Final QC review has run, your context carries a FINAL QC REVIEW block: the retained result's open findings (with their ids), open disputed candidates, and whether the review is still CURRENT against the document. Treat it as the review's record, not your own judgement — you may agree or disagree in chat, but never restate a finding as your own discovery.

- NEVER apply QC fixes unprompted. Applying requires the user's explicit approval IN THIS CONVERSATION ("yes, apply them", an approval chip, a named subset). An earlier general instruction, or your own confidence, is not approval.
- When the user approves, apply the verified safe fixes with ONE apply_qc_fixes call carrying every approved finding id, and make it the FIRST action of that turn — any document edit earlier in the turn makes the review stale and the tool will refuse. Other edits the user asked for come after it.
- apply_qc_fixes executes each finding's exact panel-verified operations and records the audit disposition — never re-type a safe fix through apply_spec_edits; that would leave the finding open in the audit trail.
- Findings marked advisory have no panel-approved operations. Summarize what they need; draft a remedy through ordinary apply_spec_edits only when the user asks, and say plainly that the finding itself stays open until Final QC is re-run or the user dismisses it in the panel.
- Disputed, refuted, and inconclusive candidates are NEVER applyable. A disputed candidate needs the user's own adjudication in the Final QC panel; present both sides when asked, recommend if you have a view, and leave the disposition to them.
- Dismissing a finding happens in the Final QC panel with a written reason — offer that path when the user wants to set one aside; you cannot dismiss for them.
- When the block says the review is STALE, describe findings as the last review's record, propose nothing for automatic application, and note that re-running Final QC is how they get re-verified."""

_SPEC_CONVENTIONS_ENGINE = """\
# Spec conventions

- CSI SectionFormat three-part structure: PART 1 - GENERAL, PART 2 - PRODUCTS, PART 3 - EXECUTION, with standard article numbering (1.1, 1.2 / 2.1 / 3.1) and lettered paragraphs (A., B., C.) with numbered subparagraphs.
- Imperative, terse specification language ("Provide...", "Install...", "Submit..."). No narrative prose inside the spec."""

_CLOSING = """\
Never fabricate project facts, code adoptions, or client standards — ask, or default visibly with an assumed stamp."""


# The canned user message the "Draft full section" action (Batch 3, WI1)
# sends through the normal chat path — it appears in chat as a visible,
# honest user turn and rides the ordinary tool loop, undo, and rollback.
# Server-owned (not the frontend) so the obligations stay versioned with
# the engine. The complementary stable-prompt policy is ``_FULL_DRAFT_POLICY``.
FULL_DRAFT_DIRECTIVE = """\
Draft the COMPLETE section now — the full first pass, top to bottom.

- Lay down every PART and every article this section conventionally carries (per the section catalog where this module carries one — otherwise per the discipline's conventional section structure — and the interview playbook), plus anything the project's known facts call for. Structure first, then flesh each article out.
- Use everything already established: my interview answers, the project profile, the standards editions in effect, and the grounded research items. Draft to them — and when a provision derives from a research item, tag it with that item's source_item_id.
- Stamp provenance honestly: confirmed only for what I've actually stated or approved; assumed for your defensible playbook / standards / domain defaults (say in one line what you assumed); [TBD: …] or needs_input for anything that genuinely can't be defaulted yet. Over-flag rather than silently guess — I'll walk the assumptions afterward.
- Keep each apply_spec_edits call to a sensible size (an article or a few related articles) so the document assembles visibly as you go, not in one silent mega-batch at the end.
- When you're done, give me a short summary in chat plus the 2–3 highest-value follow-up questions."""


# --- Full-draft prerequisites ---------------------------------------------
#
# The minimum the app must know before a whole-section draft is worth
# running. A full draft lays down every PART and article and stamps
# provenance across all of it, so a wrong section number, an unknown
# facility type, or an unknown country is not one bad line — it is a whole
# document produced confidently, which the user then has to walk block by
# block to unpick. Three questions first is far cheaper than that.
#
# Why exactly these three:
# - SECTION decides what the document *is*: its number, title, and scope
#   boundary against the sibling sections it must not duplicate.
# - PROJECT TYPE (facility/use) is what every *defaulted* provision is
#   defended by. A data center and a hospital take different defaults out
#   of the same standard, and a full draft is mostly defaults.
# - COUNTRY selects the code family and the units — US I-codes/NFPA/UL and
#   inch-pound, or Canadian NBC/NFC/CSA/ULC and SI. Wrong here invalidates
#   the REFERENCES article and every provision drafted to it.
#
# City, state/province, and client are deliberately NOT prerequisites: they
# refine a draft rather than decide its shape, and the defaults-first
# interview can carry a first pass without them. The full profile is a
# research prerequisite (``profile_complete``), which is a separate gate.
DRAFT_PREREQUISITE_IDS = ("section", "project_type", "country")

_DRAFT_PREREQUISITE_LABELS = {
    "section": "the CSI section number and title",
    "project_type": "the project type (facility or use)",
    "country": "the project country",
}

# What the model should ask for, per missing fact: the question's substance
# plus the operation that records the answer. Phrased for a user message —
# this whole directive is sent as the user's own turn.
_DRAFT_PREREQUISITE_ASKS = {
    "section": (
        "Which CSI section this is — number and title. Recommend the most "
        "likely one for my discipline and scope, with one or two "
        "alternatives if it is genuinely ambiguous. Record it with a "
        "replace on \"sec\" carrying both the number and the title."
    ),
    "project_type": (
        "The project type — the facility or use (for example Data Center, "
        "Hospital, K-12 School, Office Tower), not the construction scope "
        "and not the section. This is what your defaults get defended by, "
        "so say in one clause what it changes. Record it with "
        "set_project_identity."
    ),
    "country": (
        "The country — United States or Canada. It picks the code family "
        "(I-codes / NFPA / UL versus NBC-NFC / CSA / ULC) and the units, "
        "so I do not want it guessed. Record it with set_project_profile, "
        "along with city, state or province, and client if I give them."
    ),
}


@dataclass(frozen=True)
class DraftPrerequisites:
    """What the app knows — and still needs — before a full-section draft.

    Values are the resolved display forms (``country`` is the country's
    display name, not its code), so every surface renders the same string.
    ``missing`` holds :data:`DRAFT_PREREQUISITE_IDS` entries in that fixed
    order, so the tooltip, the directive, and the API payload can never
    list the same gaps in a different sequence.
    """

    section: str
    project_type: str
    country: str
    missing: tuple[str, ...]

    @property
    def ready(self) -> bool:
        """True when a full-section draft has its minimum anchor facts."""
        return not self.missing

    def missing_labels(self) -> tuple[str, ...]:
        return tuple(_DRAFT_PREREQUISITE_LABELS[key] for key in self.missing)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for ``_doc_payload`` and the draft endpoint.

        ``requirements`` is a list of records rather than parallel
        id/label/value arrays: the frontend renders label beside value, and
        parallel arrays are exactly the shape that falls out of alignment.
        """
        values = {
            "section": self.section,
            "project_type": self.project_type,
            "country": self.country,
        }
        return {
            "ready": self.ready,
            "missing": list(self.missing),
            "requirements": [
                {
                    "id": key,
                    "label": _DRAFT_PREREQUISITE_LABELS[key],
                    "satisfied": key not in self.missing,
                    "value": values[key],
                }
                for key in DRAFT_PREREQUISITE_IDS
            ],
        }


def draft_prerequisites(
    *,
    section_number: str = "",
    section_title: str = "",
    project_type: str = "",
    country: str = "",
) -> DraftPrerequisites:
    """Derive the full-draft gate from raw document identity values.

    Pure — values in, report out — so the gate stays testable and lives
    beside the directives whose wording depends on it.

    A section needs BOTH its number and its title: the ops that set it
    (the model's ``replace`` on ``sec`` and the panel's header edit) always
    write the pair, so half of one is a half-finished header rather than a
    usable anchor. The country must fold to a code this app actually
    supports — ``set_project_profile`` refuses anything else, but a
    hand-edited or legacy project file can still carry free text, and
    drafting a US section against an unrecognized jurisdiction is exactly
    the confident-wrong-document failure this gate exists to prevent.
    """
    number = " ".join((section_number or "").split())
    title = " ".join((section_title or "").split())
    facility = " ".join((project_type or "").split())
    country_code = normalize_country(country or "")

    missing = []
    if not (number and title):
        missing.append("section")
    if not facility:
        missing.append("project_type")
    if not country_code:
        missing.append("country")

    return DraftPrerequisites(
        section=f"{number} {title}".strip(),
        project_type=facility,
        country=COUNTRY_DISPLAY.get(country_code, ""),
        missing=tuple(key for key in DRAFT_PREREQUISITE_IDS if key in missing),
    )


def _established_lines(prereqs: DraftPrerequisites) -> list[str]:
    labels = {
        "section": "SECTION",
        "project_type": "PROJECT TYPE (facility/use)",
        "country": "COUNTRY",
    }
    values = {
        "section": prereqs.section,
        "project_type": prereqs.project_type,
        "country": prereqs.country,
    }
    return [
        f"- {labels[key]}: {values[key]}"
        for key in DRAFT_PREREQUISITE_IDS
        if key not in prereqs.missing
    ]


def full_draft_directive(prereqs: DraftPrerequisites) -> str:
    """The full-draft user message, anchored on the established facts.

    :data:`FULL_DRAFT_DIRECTIVE` carries the obligations and is appended to
    rather than rewritten, so its wording stays one versioned constant. The
    anchor restates what the whole draft is being built on — the turn's
    PROJECT CONTEXT carries the same identity, but naming it *in the
    directive* makes the instruction self-contained and leaves an honest
    record in the transcript of exactly what the draft was told to assume.
    """
    return "\n\n".join(
        [
            FULL_DRAFT_DIRECTIVE,
            "\n".join(
                [
                    "Draft to these established facts — they are settled, "
                    "not open questions, so do not re-ask them:",
                    *_established_lines(prereqs),
                ]
            ),
        ]
    )


def draft_prerequisites_directive(prereqs: DraftPrerequisites) -> str:
    """The user message sent when a full draft is asked for too early.

    The click is honored rather than refused: instead of drafting blind (or
    bouncing the user with an error and no next step), the app sends a turn
    that collects exactly the missing facts and nothing else. It follows
    the same defaults-first posture as the rest of the interview — every
    question carries a recommendation and "I don't know" is a real answer —
    and it names what is ALREADY known so the model cannot burn the turn
    re-asking settled questions.
    """
    one = len(prereqs.missing) == 1
    noun = "one more thing" if one else f"{len(prereqs.missing)} things"
    pronoun = "it" if one else "them"
    lines = [
        f"Before you draft the full section, pin down {noun} with me — a "
        f"whole-section draft anchors on {pronoun}, and every defaulted "
        "provision inherits whatever we get wrong here.",
        "",
        *(f"- {_DRAFT_PREREQUISITE_ASKS[key]}" for key in prereqs.missing),
    ]
    established = _established_lines(prereqs)
    if established:
        lines += [
            "",
            "Already established — take these as settled and do not "
            "re-ask them:",
            *established,
        ]
    ask = (
        "Ask me about it in this turn"
        if one
        else "Ask me about all of them together in this one turn"
    )
    lines += [
        "",
        f"{ask}, with your recommended answer and a one-clause reason. "
        "\"I don't know\" is a real answer from me: take your recommendation "
        "as the default and stamp whatever it drives assumed. Stage the "
        "likely answers as suggested replies so I can pick one.",
        "",
        "Record each answer with its operation the moment I give it. Do NOT "
        "draft the section in this turn — once "
        + ("it is" if one else "these are")
        + " recorded, tell me the full draft is ready and I will run it.",
    ]
    return "\n".join(lines)


# Legacy session-discipline and session-primer bounds. New sessions learn
# discipline through versioned project identity; older project files still
# load their top-level discipline through these sanitizers.
_MAX_DISCIPLINE_LEN = 80
# Room for a sentence or two of legacy project-priming context. The current UI
# no longer collects it, but older project files and API clients can supply it.
_MAX_PROJECT_CONTEXT_LEN = 400

def sanitize_discipline(discipline: str) -> str:
    """Collapse free-text discipline input to one bounded line.

    Whitespace folding neutralizes newline injection into prompt/directive
    structure; the cap keeps a pasted paragraph from bloating a turn. Empty
    (or whitespace-only) input stays empty — callers choose their own
    fallback. Retained for the legacy session-level discipline on reset and
    project load.
    """
    return " ".join((discipline or "").split())[:_MAX_DISCIPLINE_LEN].strip()


def sanitize_project_context(text: str) -> str:
    """Collapse the free-text project description to one bounded line.

    Same newline-injection guard as :func:`sanitize_discipline` (folds all
    whitespace so the primer can't forge prompt structure), with a larger cap
    since it holds a sentence or two rather than a single label. Empty stays
    empty. Shared by the reset endpoint and project load.
    """
    return " ".join((text or "").split())[:_MAX_PROJECT_CONTEXT_LEN].strip()


def _render_catalog(module: SpecModule) -> str:
    if not module.section_catalog and module.open_catalog:
        return "\n".join(
            [
                "# Section catalog",
                "",
                "This module carries an OPEN catalog — it can author any "
                "CSI MasterFormat section:",
                "",
                "- Establish the MasterFormat section number and title from "
                "the user's stated discipline and scope; when unsure, "
                "propose the 2-3 most likely sections with one-line scope "
                "distinctions and let the user pick.",
                "- Set the section header immediately once chosen (replace "
                "on \"sec\" with the number and title).",
                "- Respect conventional sibling-section boundaries for the "
                "discipline: coordinate with related sections, never "
                "duplicate their scope.",
            ]
        )
    lines = [
        "# Section catalog",
        "",
        "Sections this module authors (steer toward the first unless the "
        "user names another):",
        "",
    ]
    for section in module.section_catalog:
        line = f"- {section.number} {section.title}"
        if section.scope_note:
            line += f" — {section.scope_note}"
        lines.append(line)
    return "\n".join(lines)


def _render_playbook(module: SpecModule) -> str:
    lines = [
        "# Interview playbook",
        "",
        "Ordered topics for the lead section. Defaultable topics carry the "
        "recommended default to apply (stamped assumed) when the user "
        "defers; (must ask) topics have no default and require an answer:",
        "",
    ]
    for i, topic in enumerate(module.interview_playbook, start=1):
        if topic.non_defaultable:
            lines.append(
                f"{i}. {topic.title} (must ask) — {topic.guidance}"
            )
        else:
            lines.append(
                f"{i}. {topic.title} — {topic.guidance} "
                f"Default: {topic.default}"
            )
    return "\n".join(lines)


def render_system_prompt(module: SpecModule) -> str:
    """The stable (cacheable) system prompt for ``module``.

    Deterministic per module: persona, engine protocol blocks, catalog,
    playbook, and conventions. Session-varying facts (editions in effect,
    document outline) belong to the dynamic context block, not here.
    """
    conventions = _SPEC_CONVENTIONS_ENGINE + "\n" + (
        module.domain_conventions.format(**module.basis.format_kwargs())
    )
    return "\n\n".join(
        [
            module.drafting_persona,
            _HOW_YOU_WORK,
            _TOOL_GUIDE,
            _PROVENANCE,
            _INTERVIEW_POLICY,
            _STANDARDS_POLICY,
            _WEB_LOOKUP_POLICY,
            _FIGURE_POLICY,
            _SUGGESTED_PROMPTS_POLICY,
            _REFERENCE_DOC_POLICY,
            _LINT_POLICY,
            _RESEARCH_POLICY,
            _QC_FINDINGS_POLICY,
            _GAP_AND_ADAPT,
            _FULL_DRAFT_POLICY,
            _render_catalog(module),
            _render_playbook(module),
            conventions,
            _CLOSING,
        ]
    )
