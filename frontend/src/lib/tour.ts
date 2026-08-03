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
 * a saved tutorial at a different chapter than the one it was paused in —
 * with instructions that no longer match the active scenario. A bump simply
 * discards stale records, which is the correct outcome.
 */
export const TOUR_VERSION = 4;

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

export type TourCoverageMode = "interactive" | "optional" | "explanatory";
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

export type TourActionKind =
  | "profile-fill"
  | "run-research"
  | "run-qc"
  | "prefill-composer"
  | "open-templates";

export interface TourAction {
  kind: TourActionKind;
  label: string;
  note?: string;
  prefillText?: string;
}

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
  actions?: readonly TourAction[];
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
        mode: "interactive",
        anchor: "doc-panel",
        readiness: "content",
        placement: "left",
        title: "This is an actual specification",
        body:
          "The tutorial runs on the bundled showcase spec — a complete, pre-generated example section — in a protected practice workspace. Every block you see is real document state the app can edit, lint, and export. Your own project was set aside untouched when the tour began: back, pause, ask a question, or end at any time, and it comes straight back exactly as it was.",
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
        id: "starter-paths",
        capabilities: ["session.starters"],
        mode: "explanatory",
        anchor: "chat-pane",
        placement: "right",
        title: "Four ways into the same document",
        body:
          "A fresh session's empty chat offers starter prompts: the two-minute pitch, immediate drafting, a guided interview, or an office master. They send ordinary chat messages and never lock you into a workflow — this showcase already has a conversation, so you will see them next time you start a session.",
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
        mode: "interactive",
        anchor: "composer",
        placement: "top",
        title: "A practical interview",
        body:
          "Questions include a recommended default. “I don't know” is valid: the model can use a defensible default and mark the resulting content assumed. Ask it to guide you, type a correction, or answer normally.",
        actions: [
          {
            kind: "prefill-composer",
            label: "Try a guided answer",
            prefillText: "I don't know — use your recommended default and show me what you assumed.",
            note: "Pauses the tour with the sentence in the composer; edit or send it normally.",
          },
        ],
      },
      {
        id: "suggestions",
        capabilities: ["chat.suggestions"],
        mode: "interactive",
        anchor: "suggested-prompts",
        placement: "top",
        title: "Suggested replies are shortcuts",
        body:
          "Contextual chips are complete replies in your voice. Click one to send, ignore them, or type your own response. They disappear when there is nothing useful to suggest.",
      },
      {
        id: "streaming",
        capabilities: ["chat.streaming", "chat.stop", "chat.thinking"],
        mode: "interactive",
        anchor: "doc-panel",
        placement: "left",
        title: "The document is generated in front of you",
        body:
          "This paper is where the assistant's work lands: as a reply streams, document edits are applied batch by batch, changed blocks glow briefly, and scroll-follow stays with the work — the document builds dynamically rather than appearing all at once at the end. Chat, meanwhile, shows live thinking, searching, fetching, drafting, and drawing statuses, and expanding Thinking on a reply reads the reasoning summary as it streams (shown, never stored — absent from history, traces, and the project file). Stop preserves visible partial progress; a failed turn restores the prior document while its already-used tokens remain in usage.",
      },
      {
        id: "full-draft",
        capabilities: ["chat.full-draft"],
        mode: "interactive",
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
          "Ask the assistant to verify a current code, product, or jurisdiction fact and it can search and fetch during the normal turn. This is separate from the systematic four-dimension Research workflow.",
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
        id: "section-header",
        capabilities: ["document.section-header"],
        mode: "interactive",
        anchor: "",
        resolve: "section-header",
        placement: "bottom",
        title: "Name the section",
        body:
          "An unnamed section reads SECTION [TBD]. Hover the header and edit it to set the number and title yourself, or state them in chat and the assistant records the same change. Either way it is one undoable version, and the number is what the export filename, the module scope check, and Final QC all read.",
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
        mode: "interactive",
        anchor: "",
        resolve: "first-paragraph",
        readiness: "rich-structure",
        placement: "left",
        title: "Insert, edit, confirm, and delete",
        body:
          "Add the first article or insert an article, provision, or subparagraph at a permitted position. Hover real content to edit or confirm it. Delete is two-step, and deleting an article warns that its complete subtree goes with it. A fifth paragraph level is intentionally unavailable.",
      },
      {
        id: "rearrange",
        capabilities: ["document.rearrange"],
        mode: "interactive",
        anchor: "",
        resolve: "reorderable-paragraph",
        readiness: "rich-structure",
        placement: "left",
        title: "Rearrange without rebuilding",
        body:
          "Drag articles within a PART or move paragraphs among their siblings. Keyboard: Space picks up, Up/Down moves, Space or Enter drops, Escape cancels. Arrow buttons are the fallback. IDs and subtrees stay intact and numbering recomputes; cross-PART moves and reparenting are deliberately blocked.",
      },
      {
        id: "open-items",
        capabilities: ["document.open-items"],
        mode: "interactive",
        anchor: "open-items",
        drawer: "openItems",
        placement: "top",
        title: "Open decisions stay counted",
        body:
          "TBD markers and needs-input blocks collect in a jumpable inventory, remain scheduled in exports, and are one of the readiness checklist's gating conditions. Click any entry to jump to the block that raised it.",
      },
      {
        id: "lint",
        capabilities: ["document.lint"],
        mode: "interactive",
        anchor: "lint-issues",
        placement: "top",
        title: "Deterministic lint, running on every change",
        body:
          "This practice state contains real findings on purpose. Lint runs with no model call and no network: stale or unrecorded editions, unresolved placeholders, template markers, empty articles, duplicate article titles, and an unset section header. Click one to jump to it. Lint is advisory — it never blocks an edit or a turn — but a clean report is required for issue readiness.",
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
        mode: "interactive",
        anchor: "research-drawer",
        drawer: "research",
        placement: "top",
        title: "Four explicit project facts",
        body:
          "City, state/province, country, and client can be entered in chat or here and corrected later. Research remains locked until the profile is complete.",
        actions: [
          {
            kind: "profile-fill",
            label: "Fill the showcase profile",
            note: "One undoable edit in the protected tutorial workspace.",
          },
        ],
      },
      {
        id: "standards",
        capabilities: ["standards.basis", "standards.manage"],
        mode: "interactive",
        anchor: "standards-strip",
        placement: "top",
        title: "Standards are editable project decisions",
        body:
          "See the active edition basis and jurisdiction overrides. Add a standard, change edition with a stated basis, exclude or restore one, revert an override, or remove one you added. Every change is versioned, undoable, and feeds research, drafting, and QC.",
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
        mode: "interactive",
        anchor: "version-stepper",
        readiness: "versioned",
        placement: "bottom",
        title: "Every meaningful change is a version",
        body:
          "Model turns, manual edits, profile changes, and identity corrections each make one undoable version. Step backward and forward; the complete history survives save and reopen.",
      },
      {
        id: "review-queue",
        capabilities: ["review.queue", "review.actions"],
        mode: "interactive",
        anchor: "review-drawer",
        drawer: "review",
        placement: "top",
        title: "Clear assumptions and imports at speed",
        body:
          "Filter All, Assumed, or Imported; jump to the source block and see research context. Keep, Edit, Delete, Ask, Skip, or Back by button or keyboard. Hold the article action to confirm its remaining review blocks as one undo.",
      },
      {
        id: "compare",
        capabilities: ["history.compare"],
        mode: "interactive",
        anchor: "compare",
        readiness: "versioned",
        placement: "bottom",
        title: "Compare any two versions",
        body:
          "Compare a prior version or normalized import baseline with word-level insertions/deletions, statistics, and provenance-status changes. The same semantic diff powers the Word redline.",
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
        mode: "interactive",
        anchor: "chat-pane",
        placement: "right",
        title: "Figures live with the conversation",
        body:
          "This chapter attaches one bundled example of each figure kind to the conversation: in normal work the assistant creates Mermaid diagrams, SVG schematics, and data tables inline as it drafts. Expand or minimize them, confirm removal, and download SVG/PNG or table CSV. Figures persist in the project and return to the assistant message that created them.",
      },
      {
        id: "references",
        capabilities: ["reference.attach", "reference.use"],
        mode: "interactive",
        anchor: "attach-reference",
        placement: "bottom",
        title: "Attach background without polluting the spec",
        body:
          "Attach or remove up to 20 DOCX, PDF, TXT, XML, or CSV files, within a shared 100,000-token budget the panel meters as you add them. See kind, extracted blocks, truncation, tracked-change Accept-All, PDF page markers, and tabular/XML structure. The assistant sees a one-line summary every turn and opens the full text only when it needs it, so a long standard does not inflate the cost of every later message. They save with the project but stay out of the spec, lint, diff, QC, readiness, and document export.",
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
        mode: "interactive",
        anchor: "import-master",
        placement: "bottom",
        title: "An office master is another real on-ramp",
        body:
          "Import a DOCX only into a blank spec. Its supported body content becomes imported provenance while the exact package is retained. Loading, extraction warnings, tracked-changes Accept-All, dismissible notes, and honest non-spec presentation make limitations visible.",
      },
      {
        id: "source-permissions",
        capabilities: ["document.source-permissions", "import.source-output"],
        mode: "explanatory",
        anchor: "",
        resolve: "first-imported",
        readiness: "imported",
        placement: "left",
        title: "Source preservation is proven per operation",
        body:
          `Imported controls use server-derived edit, delete, insert, and move permissions and fail closed while analysis is pending. Disabled controls state the exact reason. ${SOURCE_CAPABILITY_GUIDANCE} The exact original remains separately downloadable.`,
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
        mode: "interactive",
        anchor: "qc-drawer",
        drawer: "qc",
        readiness: "qc",
        placement: "top",
        title: "Findings become a guided remediation plan",
        body:
          "Open findings are separated into verified fixes ready to apply, project facts that need your decision, and items for professional review. Select safe fixes, preview deduplication and conflicts, then confirm one undoable batch; or apply and dismiss individually. TBD and assumption items can prefill a focused chat request while the complete rationale, evidence, inconclusive candidates, and refutations remain available.",
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
        optionalReason: "The tour shows the real menu; downloading a file is optional.",
      },
      {
        id: "save-open",
        capabilities: ["project.save-open", "session.unsaved-gates"],
        mode: "interactive",
        anchor: "save",
        placement: "bottom",
        title: "One project file restores the whole workspace",
        body:
          "The workspace visible in this chapter was serialized to a real temporary .baspec package and restored through the production project loader. .baspec stores conversation, versions, identity, figures, suggestions, references, standards, import warnings and source, research, and QC. New, Open, and window close all offer Save / continue without saving / Cancel before discarding work.",
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
          "The five Help topics cover the workflow in place, and can restart this tutorial or jump straight to any chapter. “Why trust it” links to a full dossier that answers, action by action, what runs on your machine, what leaves it, where each word came from, which parts involve no AI at all, and how to verify any of it yourself. The header shows an available update and installs it when the platform allows, or links to the releases page when it does not; after an update, release notes open by themselves, and Settings can reopen them any time.",
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
        mode: "interactive",
        anchor: "save-template",
        placement: "bottom",
        title: "Turn this actual spec into a starter",
        body:
          "Create a named reusable template from the current tutorial spec. Preview an exact copy before committing, or request an AI-generalized version when available. The preview is server-produced document content, not tour decoration.",
        actions: [{ kind: "open-templates", label: "Open template studio" }],
      },
      {
        id: "template-use",
        capabilities: ["template.start", "template.import", "template.manage"],
        mode: "interactive",
        anchor: "templates",
        placement: "bottom",
        title: "Built-in and personal reusable starters",
        body:
          "Start an independent spec from a built-in or personal template, import/export a personal template, rename or describe it, and delete it with confirmation. Missing curated modules are shown rather than silently substituted.",
      },
      {
        id: "finish",
        capabilities: ["tour.finish"],
        mode: "interactive",
        anchor: "new-session",
        placement: "bottom",
        title: "Ending the tour returns you to your project",
        body:
          "There is one ending, and this is it. Your project comes back exactly as it was before the tour started — the same document, history, and version list — and this practice copy is discarded. Continue now, press End on any step, or start a new session or open a project from the header; every one of those puts your project back first. If you began blank, you get that blank session back. Model usage you spent during the tour still counts toward your totals. To take tutorial work with you, use Save in the panel before you finish.",
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
