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

export const TOUR_VERSION = 2;

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
          "The tutorial is running in a protected copy of your current project, a newly AI-generated spec, or the bundled LLM-authored showcase you selected. Every block you see is real document state. Back, pause, ask a question, or end the tour at any time; your original is restorable at the finish.",
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
          "Start with the two-minute pitch, immediate drafting, a guided interview, or an office master. These starter prompts send ordinary chat messages; they never lock you into a workflow.",
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
        capabilities: ["chat.streaming", "chat.stop"],
        mode: "interactive",
        anchor: "chat-pane",
        placement: "right",
        title: "Watch work land live",
        body:
          "Thinking, searching, fetching, drafting, and drawing statuses appear in chat while document patches land on the paper. Changed blocks glow briefly and scroll-follow stays with the work. Stop preserves visible partial progress; a failed turn restores the prior document while its already-used tokens remain in usage.",
      },
      {
        id: "full-draft",
        capabilities: ["chat.full-draft"],
        mode: "interactive",
        anchor: "draft-full",
        placement: "bottom",
        title: "Draft the whole section once",
        body:
          "On an empty or sparse document, this uses the profile, research, references, and interview decisions to draft all PARTs. The result is one undoable model turn; a disabled control states exactly which prerequisite is missing.",
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
        id: "open-lint",
        capabilities: ["document.open-items", "document.lint"],
        mode: "interactive",
        anchor: "open-items",
        drawer: "openItems",
        placement: "top",
        title: "Open decisions and deterministic lint",
        body:
          "TBD and needs-input blocks collect in a jumpable inventory and remain scheduled in exports. Advisory lint jumps to stale or unrecorded editions, placeholders, template markers, empty articles, duplicate titles, and missing headers; lint never blocks an edit.",
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
        capabilities: ["research.run"],
        mode: "optional",
        anchor: "research-start",
        drawer: "research",
        placement: "top",
        title: "Systematic four-dimension research",
        body:
          "A deliberate run covers governing codes, AHJ requirements, owner/client/insurer standards, and site/environment conditions. Progress streams live; you can follow it, stop and discard the incomplete sweep, or rerun later.",
        optionalReason: "A live run takes several minutes and uses real API and web-search spend.",
        actions: [{ kind: "run-research", label: "Run research now" }],
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
          "Compact and full reports show dimension telemetry, confidence, retrieved sources, [UNVERIFIED] leads, and [PROCESS] requirements. Grounded items can create standards overrides and real provisions with ◆ source chips. Save/reopen preserves the report; readiness detects when it is stale.",
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
          "The assistant can create Mermaid diagrams, SVG schematics, and data tables inline. Expand or minimize them, confirm removal, and download SVG/PNG or table CSV. Figures persist in the project and return to the assistant message that created them.",
      },
      {
        id: "references",
        capabilities: ["reference.attach", "reference.use"],
        mode: "interactive",
        anchor: "attach-reference",
        placement: "bottom",
        title: "Attach background without polluting the spec",
        body:
          "Attach or remove up to 20 DOCX, PDF, TXT, XML, or CSV files. See kind, extracted blocks, truncation, tracked-change Accept-All, PDF page markers, and tabular/XML structure. The model reads them on request; they save with the project but stay out of the spec, lint, diff, QC, readiness, and document export.",
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
        capabilities: ["qc.preflight", "qc.run"],
        mode: "optional",
        anchor: "qc-drawer",
        drawer: "qc",
        placement: "top",
        title: "A deliberate, expensive final pass",
        body:
          "The preflight estimates cost/time and requires confirmation; a curated-module mismatch requires acknowledgement. Five lenses run live and an adversarial verifier challenges candidates. Rerun anytime; Stop retains the paid partial audit and reports stale, partial, no-research, and latest-attempt states honestly.",
        optionalReason: "Final QC is the most expensive model workflow and requires explicit consent.",
        actions: [{ kind: "run-qc", label: "Run Final QC now" }],
      },
      {
        id: "qc-findings",
        capabilities: ["qc.findings", "qc.actions"],
        mode: "interactive",
        anchor: "qc-drawer",
        drawer: "qc",
        readiness: "qc",
        placement: "top",
        title: "Findings carry evidence and disposition",
        body:
          "Work the severity queue, jump to the block, inspect rationale, sources, operation preview, and semantic/mechanical status. Apply one fix as one undo or hold to batch criticals. Dismissal requires a persisted reason; inconclusive and refuted candidates remain disclosed.",
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
        id: "help-updates",
        capabilities: ["help.topics", "updates.manage"],
        mode: "explanatory",
        anchor: "help-nav",
        placement: "bottom",
        title: "Help and updates are always available",
        body:
          "The five Help topics cover the workflow in place. About contains the manual update check and release information; the header shows an available update and supports installation when the current platform allows it.",
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
        title: "Choose what happens to the tutorial work",
        body:
          "Return to your protected project by default, or save a .baspec tutorial copy before returning. Replacing your project with the tutorial copy is intentionally last and requires a second confirmation plus the option to save the original first. If you began blank, Return restores that blank session.",
        continueLabel: "Choose what to keep",
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
