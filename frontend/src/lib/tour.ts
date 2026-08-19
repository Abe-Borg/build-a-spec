/**
 * Versioned guided-tutorial manifest.
 *
 * The tutorial teaches against the active tutorial SpecDoc.  Static shell
 * anchors and dynamic document resolvers live beside a typed capability list
 * so tests can prove that every shipped end-user capability is represented.
 */
import type { DocParagraph, SpecDoc } from "../types";
import type { EndUserCapabilityId } from "./capabilities";
import {
  SOURCE_CAPABILITY_GUIDANCE,
  SOURCE_OUTPUT_GUIDANCE,
  type SourceOutputGuidance,
} from "./sourceOutputGuidance";

/**
 * Bump whenever chapter/step ORDER changes.
 *
 * Resume records persist numeric chunk and step indexes and are accepted
 * whenever this matches, so inserting a chapter without bumping would resume
 * a saved tutorial at a different chapter than the one it was left at —
 * with instructions that no longer match the active scenario. A bump simply
 * discards stale records, which is the correct outcome.
 */
export const TOUR_VERSION = 5;

export interface StarterPrompt {
  label: string;
  sub?: string;
  kind: "onboarding" | "chat";
}

export function starterPrompts(discipline?: string): StarterPrompt[] {
  const known = discipline?.trim();
  return [
    { label: "New to this software, show me how to use this", kind: "onboarding" },
    {
      label: "What can Build-a-Spec do for me, and where do you need my judgment?",
      sub: "The two-minute pitch",
      kind: "chat",
    },
    {
      label: known
        ? `Start drafting my ${known} section — show me what you assume as you go.`
        : "Start drafting — I'll tell you the discipline, section, and project as we go.",
      sub: "Watch a section take shape immediately",
      kind: "chat",
    },
    {
      label:
        "Interview me about my project — one question at a time, with your recommended default for each. I'll say 'I don't know' when I don't.",
      sub: "The guided interview",
      kind: "chat",
    },
    {
      label:
        "I have an office master spec for this section — explain how importing and adapting it works here.",
      sub: "The other on-ramp",
      kind: "chat",
    },
  ];
}

/**
 * How a step's subject relates to spending, not to what the tour asks of you.
 *
 * The tour is a fixed track the user only watches, so `interactive` is gone:
 * no step hands out a control or waits on one. `optional` survives because the
 * distinction it draws is still real — the feature that step describes costs
 * money or consent to actually run, and the badge says so before the reader
 * goes looking for the button afterwards.
 */
export type TourCoverageMode = "optional" | "explanatory";
export type TourReadiness =
  | "content"
  | "rich-structure"
  | "versioned"
  | "research"
  | "imported"
  | "qc"
  | "template";

export type TourResolver =
  | "section-header"
  | "first-paragraph"
  | "first-assumed"
  | "first-needs-input"
  | "first-sourced"
  | "reorderable-article"
  | "reorderable-paragraph"
  | "first-imported";

export interface TourStep {
  id: string;
  capabilities: readonly EndUserCapabilityId[];
  mode: TourCoverageMode;
  /** Stable data-tour value; blank is permitted only for tutorial chrome. */
  anchor: string;
  resolve?: TourResolver;
  drawer?: "review" | "research" | "qc" | "openItems";
  readiness?: TourReadiness;
  title: string;
  body: string;
  details?: readonly SourceOutputGuidance[];
  placement?: "top" | "bottom" | "left" | "right";
  continueLabel?: string;
  optionalReason?: string;
}

export interface TourChunk {
  id: string;
  title: string;
  /** A server-built, temporary view needed to demonstrate this chapter. */
  scenario?: string;
  steps: readonly TourStep[];
}

export const TOUR: readonly TourChunk[] = [
  {
    id: "workspace",
    title: "Your real tutorial workspace",
    steps: [
      {
        id: "workspace-source",
        capabilities: ["tour.workspace", "tour.controls"],
        mode: "explanatory",
        anchor: "doc-panel",
        readiness: "content",
        placement: "left",
        title: "This is an actual specification",
        body:
          "The tutorial runs on the bundled showcase spec — a complete, pre-generated example section — in a protected practice workspace. Every block you see is real document state the app can edit, lint, and export. This is a fixed track: it walks the whole app in order, nothing is asked of you, and Continue moves on whenever you are ready. The card never blocks the app either, so you are free to read the document as you go. Your own project was set aside untouched when the tour began: step back or end at any time, and it comes straight back exactly as it was.",
      },
      {
        id: "identity",
        capabilities: ["session.identity"],
        mode: "explanatory",
        anchor: "project-heading",
        placement: "bottom",
        title: "Discipline and project identity",
        body:
          "Build-a-Spec supports any U.S. or Canadian discipline. The heading grows from discipline to Discipline · Project Type · City, Region as the conversation establishes facts, and follows document history when corrected.",
      },
      {
        id: "api-key",
        capabilities: ["session.api-key"],
        mode: "optional",
        anchor: "settings",
        placement: "bottom",
        title: "Your key, under your control",
        body:
          "Settings shows the key source and masked tail and lets you test, save, replace, or remove it. Environment-managed keys stay locked; local keys use the operating-system credential store when available.",
        optionalReason: "Never asks you to reveal or replace a working secret during the tour.",
      },
    ],
  },
  {
    id: "conversation",
    title: "Draft through conversation",
    steps: [
      {
        id: "interview",
        capabilities: ["chat.interview"],
        mode: "explanatory",
        anchor: "composer",
        placement: "top",
        title: "A practical interview",
        body:
          "Questions include a recommended default. “I don't know” is a valid answer: the model applies a defensible default and marks the resulting content assumed. Asking it to guide you turns an open question into concrete options with tradeoffs, and a typed correction is recorded the same way a direct answer is.",
      },
      {
        id: "suggestions",
        capabilities: ["chat.suggestions"],
        mode: "explanatory",
        anchor: "suggested-prompts",
        placement: "top",
        title: "Suggested replies are shortcuts",
        body:
          "Contextual chips are complete replies in your voice. Clicking one sends it as an ordinary message; ignoring them and typing something else does the same job. They disappear when there is nothing useful left to suggest.",
      },
      {
        id: "streaming",
        capabilities: ["chat.streaming", "chat.stop", "chat.thinking"],
        mode: "explanatory",
        anchor: "doc-panel",
        placement: "left",
        title: "The document is generated in front of you",
        body:
          "This paper is where the assistant's work lands: as a reply streams, document edits are applied batch by batch, changed blocks glow briefly, and scroll-follow stays with the work — the document builds dynamically rather than appearing all at once at the end. Chat, meanwhile, shows live thinking, searching, fetching, drafting, and drawing statuses, and expanding Thinking on a reply reads the reasoning summary as it streams (shown, never stored — absent from history, traces, and the project file). Stop preserves visible partial progress; a failed turn restores the prior document while its already-used tokens remain in usage.",
      },
      {
        id: "full-draft",
        capabilities: ["chat.full-draft"],
        mode: "explanatory",
        anchor: "draft-full",
        placement: "bottom",
        title: "Draft the whole section once",
        body:
          "On an empty or sparse document, this uses the profile, research, references, and interview decisions to draft all PARTs. The result is one undoable model turn; a disabled control states exactly which prerequisite is missing. A full draft anchors on the section, the project type, and the country — every provision it lays down inherits them — so while any of the three is unknown the click asks you about them instead of drafting blind, and drafting again once they are recorded produces the draft.",
      },
      {
        id: "quick-verify",
        capabilities: ["chat.web-verify"],
        mode: "optional",
        anchor: "composer",
        placement: "top",
        title: "Verify one fact without a full research run",
        body:
          "Asked to verify a current code, product, or jurisdiction fact, the assistant can search and fetch during the normal turn. This is separate from the systematic four-dimension Research workflow.",
        optionalReason: "A live check uses the user's API key and web tools.",
      },
    ],
  },
  {
    id: "blank-start",
    title: "Start from an empty page",
    scenario: "blank",
    steps: [
      {
        id: "starter-paths",
        capabilities: ["session.starters"],
        mode: "explanatory",
        anchor: "starter-prompts",
        placement: "right",
        title: "Four ways into the same document",
        body:
          "An empty chat offers these starter prompts: the two-minute pitch, immediate drafting, a guided interview, or an office master — above them sits the tutorial chip that opened this tour. Each one sends an ordinary chat message and locks you into nothing, and typing your own opening does exactly the same job. They are on screen right now because this practice page is empty; they clear the moment a conversation starts and return on every fresh session. While this tour is running all five are held inert, so nothing here sends a message or spends anything on your behalf.",
      },
      {
        id: "section-header",
        capabilities: ["document.section-header"],
        mode: "explanatory",
        anchor: "",
        resolve: "section-header",
        placement: "bottom",
        title: "Name the section",
        body:
          "An unnamed section reads SECTION [TBD]. Hovering the header reveals an inline edit that sets the number and title by hand; stating them in chat has the assistant record the same change. Either way it is one undoable version, and the number is what the export filename, the module scope check, and Final QC all read.",
      },
    ],
  },
  {
    id: "paper",
    title: "Work directly on the paper",
    scenario: "structural",
    steps: [
      {
        id: "structure",
        capabilities: ["document.structure", "document.provenance"],
        mode: "explanatory",
        anchor: "",
        resolve: "first-assumed",
        readiness: "rich-structure",
        placement: "left",
        title: "Structured content, honest provenance",
        body:
          "This visibly labeled disposable practice state is used only for the paper chapter and is discarded when you leave it. It contains a real three-PART tree plus intentional issues, including a temporarily blank header, so every structural, provenance, and lint behavior can be shown. Amber is assumed, red needs input, blue is imported, and confirmed is user-stated or approved.",
      },
      {
        id: "insert-edit",
        capabilities: ["document.insert", "document.edit"],
        mode: "explanatory",
        anchor: "",
        resolve: "first-paragraph",
        readiness: "rich-structure",
        placement: "left",
        title: "Insert, edit, confirm, and delete",
        body:
          "Between-block inserters add an article, provision, or subparagraph at any permitted position. Hovering real content reveals its edit and confirm affordances. Delete is two-step, and deleting an article warns that its complete subtree goes with it. A fifth paragraph level is intentionally unavailable.",
      },
      {
        id: "rearrange",
        capabilities: ["document.rearrange"],
        mode: "explanatory",
        anchor: "",
        resolve: "reorderable-paragraph",
        readiness: "rich-structure",
        placement: "left",
        title: "Rearrange without rebuilding",
        body:
          "Articles drag within a PART and paragraphs move among their siblings. The keyboard path is Space to pick up, Up/Down to move, Space or Enter to drop, Escape to cancel; arrow buttons are the fallback. IDs and subtrees stay intact and numbering recomputes; cross-PART moves and reparenting are deliberately blocked.",
      },
      {
        id: "open-items",
        capabilities: ["document.open-items"],
        mode: "explanatory",
        anchor: "open-items",
        drawer: "openItems",
        placement: "top",
        title: "Open decisions stay counted",
        body:
          "TBD markers and needs-input blocks collect in a jumpable inventory, remain scheduled in exports, and are one of the readiness checklist's gating conditions. Each entry links to the block that raised it.",
      },
      {
        id: "lint",
        capabilities: ["document.lint"],
        mode: "explanatory",
        anchor: "lint-issues",
        placement: "top",
        title: "Deterministic lint, running on every change",
        body:
          "This practice state contains real findings on purpose. Lint runs with no model call and no network: stale or unrecorded editions, unresolved placeholders, template markers, empty articles, duplicate article titles, and an unset section header. Each finding links to the block it came from. Lint is advisory — it never blocks an edit or a turn — but a clean report is required for issue readiness.",
      },
    ],
  },
  {
    id: "grounding",
    title: "Ground the project",
    steps: [
      {
        id: "profile",
        capabilities: ["research.profile"],
        mode: "explanatory",
        anchor: "research-drawer",
        drawer: "research",
        placement: "top",
        title: "Four explicit project facts",
        body:
          "City, state/province, country, and client are recorded either in chat or on this form, and can be corrected later — each change is one undoable version. Research stays locked until all four are present.",
      },
      {
        id: "standards",
        capabilities: ["standards.basis", "standards.manage"],
        mode: "explanatory",
        anchor: "standards-strip",
        placement: "top",
        title: "Standards are editable project decisions",
        body:
          "The strip shows the active edition basis and any jurisdiction overrides. Its per-row controls add a standard, change an edition against a stated basis, exclude or restore one, revert an override, or remove an added standard. Every change is versioned, undoable, and feeds research, drafting, and QC.",
      },
      {
        id: "research-run",
        capabilities: ["research.run", "research.stop", "research.agent-detail"],
        mode: "optional",
        anchor: "research-start",
        drawer: "research",
        placement: "top",
        title: "Systematic four-dimension research",
        body:
          "A deliberate run covers governing codes, AHJ requirements, owner/client/insurer standards, and site/environment conditions. Progress streams live, and clicking any agent's card opens its full activity log — every query, every source read, and any retries. Running it again appends a round rather than replacing anything. Stop, behind a confirmation, discards only the round in flight — every earlier round's findings stay in use, and the spend already committed is still metered.",
        optionalReason: "A live run takes several minutes and uses real API and web-search spend.",
      },
      {
        id: "research-report",
        capabilities: ["research.report", "research.apply"],
        mode: "explanatory",
        anchor: "research-drawer",
        drawer: "research",
        readiness: "research",
        placement: "top",
        title: "Evidence stays distinguishable",
        body:
          "Compact and full reports show dimension telemetry, confidence, retrieved sources, [UNVERIFIED] leads, and [PROCESS] requirements. Grounded items can create standards overrides and real provisions with ◆ source chips. Rounds accumulate: a requirement found again is confirmed in place rather than duplicated, so item ids stay stable for the chips already citing them, and once there is more than one round each item is dated by the round that last grounded it in a retrieved source. The report breaks out what each round added versus re-confirmed. Save/reopen preserves it; readiness detects when it is stale.",
      },
    ],
  },
  {
    id: "review",
    title: "Review and compare",
    scenario: "review",
    steps: [
      {
        id: "history",
        capabilities: ["history.undo-redo"],
        mode: "explanatory",
        anchor: "version-stepper",
        readiness: "versioned",
        placement: "bottom",
        title: "Every meaningful change is a version",
        body:
          "Model turns, manual edits, profile changes, and identity corrections each make one undoable version. The stepper walks backward and forward through them, and the complete history survives save and reopen.",
      },
      {
        id: "review-queue",
        capabilities: ["review.queue", "review.actions"],
        mode: "explanatory",
        anchor: "review-drawer",
        drawer: "review",
        placement: "top",
        title: "Clear assumptions and imports at speed",
        body:
          "The queue filters to All, Assumed, or Imported, jumps to each source block, and shows its research context. Keep, Edit, Delete, Ask, Skip, and Back are available by button or keyboard, and holding the article action confirms that article's remaining review blocks as one undo.",
      },
      {
        id: "compare",
        capabilities: ["history.compare"],
        mode: "explanatory",
        anchor: "compare",
        readiness: "versioned",
        placement: "bottom",
        title: "Compare any two versions",
        body:
          "Compare mode sets any prior version or the normalized import baseline against the current one, with word-level insertions and deletions, statistics, and provenance-status changes. The same semantic diff powers the Word redline.",
      },
    ],
  },
  {
    id: "media",
    title: "Figures and references",
    scenario: "references",
    steps: [
      {
        id: "figures",
        capabilities: ["figure.create", "figure.manage"],
        mode: "explanatory",
        anchor: "chat-pane",
        placement: "right",
        title: "Figures live with the conversation",
        body:
          "This chapter attaches one bundled example of each figure kind to the conversation: in normal work the assistant creates Mermaid diagrams, SVG schematics, and data tables inline as it drafts. Each card expands and minimizes, removes behind a confirmation, and downloads as SVG/PNG or, for a table, CSV. Figures persist in the project and return to the assistant message that created them.",
      },
      {
        id: "references",
        capabilities: ["reference.attach", "reference.use"],
        mode: "explanatory",
        anchor: "attach-reference",
        placement: "bottom",
        title: "Attach background without polluting the spec",
        body:
          "Up to 20 DOCX, PDF, TXT, XML, or CSV files attach here, within a shared 100,000-token budget the panel meters as they accumulate. Each row reports kind, extracted blocks, truncation, tracked-change Accept-All, PDF page markers, and tabular/XML structure. The assistant sees a one-line summary every turn and opens the full text only when it needs it, so a long standard does not inflate the cost of every later message. They save with the project but stay out of the spec, lint, diff, QC, readiness, and document export.",
      },
    ],
  },
  {
    id: "sources",
    title: "Master-spec import and source output",
    scenario: "import",
    steps: [
      {
        id: "master-import",
        capabilities: ["import.master"],
        mode: "explanatory",
        anchor: "import-master",
        placement: "bottom",
        title: "An office master is another real on-ramp",
        body:
          "A DOCX imports only into a blank spec. Its supported body content becomes imported provenance while the exact package is retained. Loading, extraction warnings, tracked-changes Accept-All, dismissible notes, and honest non-spec presentation make the limitations visible.",
      },
      {
        id: "source-permissions",
        capabilities: [
          "document.source-permissions",
          "import.source-output",
          "document.detach-source",
        ],
        mode: "explanatory",
        anchor: "",
        resolve: "first-imported",
        readiness: "imported",
        placement: "left",
        title: "Source preservation is proven per operation",
        body:
          `Imported controls use server-derived edit, delete, insert, and move permissions and fail closed while analysis is pending. Disabled controls state the exact reason. ${SOURCE_CAPABILITY_GUIDANCE} The exact original remains separately downloadable. When a package cannot be patched at all — tracked changes, macros, an embedded object, or enforced protection — the panel names that cause and offers Edit freely, which trades the byte-exact export for unrestricted editing while keeping the original downloadable.`,
        details: SOURCE_OUTPUT_GUIDANCE,
      },
    ],
  },
  {
    id: "qc",
    title: "Final QC and readiness",
    scenario: "qc",
    steps: [
      {
        id: "qc-run",
        capabilities: ["qc.preflight", "qc.run", "qc.stop"],
        mode: "optional",
        anchor: "qc-drawer",
        drawer: "qc",
        placement: "top",
        title: "A deliberate, expensive final pass",
        body:
          "The preflight estimates cost/time and requires confirmation; a curated-module mismatch requires acknowledgement. Five lenses run live and an adversarial verifier challenges candidates. Rerun anytime; Stop retains the paid partial audit and reports stale, partial, no-research, and latest-attempt states honestly.",
        optionalReason: "Final QC is the most expensive model workflow and requires explicit consent.",
      },
      {
        id: "qc-findings",
        capabilities: ["qc.findings", "qc.actions", "qc.remediation"],
        mode: "explanatory",
        anchor: "qc-drawer",
        drawer: "qc",
        readiness: "qc",
        placement: "top",
        title: "Findings become a guided remediation plan",
        body:
          "Open findings are separated into verified fixes ready to apply, project facts that need your decision, and items for professional review. Selected safe fixes preview their deduplication and conflicts before one undoable batch is confirmed; findings can also be applied and dismissed individually. TBD and assumption items prefill a focused chat request, while the complete rationale, evidence, inconclusive candidates, and refutations stay available.",
      },
      {
        id: "qc-report",
        capabilities: ["qc.report", "readiness.checklist"],
        mode: "explanatory",
        anchor: "readiness",
        drawer: "qc",
        readiness: "qc",
        placement: "top",
        title: "An auditable report and deterministic go/no-go",
        body:
          "The complete QC record is viewable in-app and downloadable as DOCX or JSON. Readiness makes no model call: it reflects open items, unreviewed blocks, lint, research currency, and Final QC status, and updates as you resolve them.",
      },
    ],
  },
  {
    id: "ship",
    title: "Export, save, and support",
    scenario: "project_roundtrip",
    steps: [
      {
        id: "export",
        capabilities: ["export.clean", "export.redline-source"],
        mode: "optional",
        anchor: "export",
        placement: "bottom",
        title: "Choose the output guarantee deliberately",
        body:
          "Clean DOCX uses automatic Word numbering and includes assumption/open-item schedules. Redline compares committed semantic versions. Imported projects distinguish normalized, proven source-preserving, and exact-original downloads; one is never silently substituted for another.",
        details: SOURCE_OUTPUT_GUIDANCE,
        optionalReason: "The tour points at the real menu but never downloads anything.",
      },
      {
        id: "save-open",
        capabilities: ["project.save-open", "session.unsaved-gates"],
        mode: "explanatory",
        anchor: "save",
        placement: "bottom",
        title: "One project file restores the whole workspace",
        body:
          "The workspace visible in this chapter was serialized to a real temporary .baspec package and restored through the production project loader. .baspec stores conversation, versions, identity, figures, suggestions, references, standards, import warnings and source, research, and QC. Save asks where the file goes the first time in a session and overwrites it every time after; Save as…, under the caret, writes a new one and re-points where Save writes. New, Open, and window close all offer Save / continue without saving / Cancel before discarding work.",
      },
      {
        id: "usage",
        capabilities: ["usage.details"],
        mode: "explanatory",
        anchor: "spend-pill",
        placement: "bottom",
        title: "Usage remains visible",
        body:
          "The pill estimates current session spend. Settings breaks down interview, research, Final QC, and template-creation input/output/cache/web usage, estimated cache savings, and the list-pricing caveat.",
      },
      {
        id: "developer-tools",
        capabilities: ["session.developer-tools"],
        mode: "explanatory",
        anchor: "settings",
        placement: "bottom",
        title: "Developer tools when something misbehaves",
        body:
          "Settings also opens Developer tools: environment and session state, the live activity log, this run's trace events, the trace-file viewer, and a one-click diagnostics bundle you can save when reporting a problem. Everything it shows is recorded on your machine only — the app keeps a detailed local record of every run, not just failures.",
      },
      {
        id: "help-updates",
        capabilities: ["help.topics", "help.trust", "updates.manage"],
        mode: "explanatory",
        anchor: "help-nav",
        placement: "bottom",
        title: "Help, the trust dossier, and updates",
        body:
          "The five Help topics cover the workflow in place, and can restart this tutorial or jump straight to any chapter. “Why trust it” links to a full dossier that answers, action by action, what runs on your machine, what leaves it, where each word came from, which parts involve no AI at all, and how to verify any of it yourself. About checks for a new version on demand and installs it from there, and the header carries the same offer; where the platform cannot run the installer both link to the releases page instead. After an update, release notes open by themselves, and Settings can reopen them any time.",
      },
    ],
  },
  {
    id: "templates",
    title: "Reuse good work",
    scenario: "template",
    steps: [
      {
        id: "template-create",
        capabilities: ["template.create"],
        mode: "explanatory",
        anchor: "save-template",
        placement: "bottom",
        title: "Turn this actual spec into a starter",
        body:
          "This control creates a named reusable template from the current tutorial spec. An exact copy previews before anything commits, and an AI-generalized version can be requested where available. The preview is server-produced document content, not tour decoration.",
      },
      {
        id: "template-use",
        capabilities: ["template.start", "template.import", "template.manage"],
        mode: "explanatory",
        anchor: "templates",
        placement: "bottom",
        title: "Built-in and personal reusable starters",
        body:
          "A built-in or personal template starts an independent spec; personal templates import and export as files, rename, take a description, and delete behind a confirmation. Missing curated modules are shown rather than silently substituted.",
      },
      {
        id: "finish",
        capabilities: ["tour.finish"],
        mode: "explanatory",
        anchor: "new-session",
        placement: "bottom",
        title: "Ending the tour returns you to your project",
        body:
          "There is one ending, and this is it. Your project comes back exactly as it was before the tour started — the same document, history, and version list — and this practice copy is discarded. Continue now, press End on any step, or start a new session or open a project from the header; every one of those puts your project back first. If you began blank, you get that blank session back. The tour itself spends nothing, but any model usage from work you did in the practice copy still counts toward your totals — and anything you made there can be saved from the panel before you finish.",
        continueLabel: "Finish and return to my project",
      },
    ],
  },
] as const;

function paragraphs(doc: SpecDoc | null): DocParagraph[] {
  if (!doc) return [];
  const found: DocParagraph[] = [];
  const visit = (items: DocParagraph[]) => {
    for (const item of items) {
      found.push(item);
      visit(item.children);
    }
  };
  for (const part of doc.parts) {
    for (const article of part.articles) visit(article.paragraphs);
  }
  return found;
}

const escapeCss = (value: string) =>
  typeof CSS !== "undefined" && CSS.escape
    ? CSS.escape(value)
    : value.replace(/[^a-zA-Z0-9_-]/g, (char) => `\\${char}`);

/** Resolve a spotlight against the actual current SpecDoc. */
export function anchorSelector(step: TourStep, doc: SpecDoc | null): string | null {
  let id: string | undefined;
  const allParagraphs = paragraphs(doc);
  switch (step.resolve) {
    case "section-header":
      id = "sec";
      break;
    case "first-paragraph":
      id = allParagraphs[0]?.id;
      break;
    case "first-assumed":
      id = allParagraphs.find((item) => item.status === "assumed")?.id;
      break;
    case "first-needs-input":
      id = allParagraphs.find((item) => item.status === "needs_input")?.id;
      break;
    case "first-sourced":
      id = allParagraphs.find((item) => !!item.source_item_id)?.id;
      break;
    case "first-imported":
      id = allParagraphs.find((item) => item.status === "imported")?.id;
      break;
    case "reorderable-article":
      id = doc?.parts.find((part) => part.articles.length > 1)?.articles[0]?.id;
      break;
    case "reorderable-paragraph": {
      const parent = allParagraphs.find((item) => item.children.length > 1);
      id = parent?.children[0]?.id;
      if (!id) {
        for (const part of doc?.parts ?? []) {
          const article = part.articles.find((item) => item.paragraphs.length > 1);
          if (article) {
            id = article.paragraphs[0].id;
            break;
          }
        }
      }
      break;
    }
  }
  if (id) return `#el-${escapeCss(id)}`;
  if (!step.anchor) return null;
  return `[data-tour="${step.anchor}"]`;
}

export function capabilityCoverage(): ReadonlySet<EndUserCapabilityId> {
  return new Set(
    TOUR.flatMap((chunk) => chunk.steps.flatMap((step) => step.capabilities)),
  );
}
