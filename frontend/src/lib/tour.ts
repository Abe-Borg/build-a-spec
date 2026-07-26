import type { SpecDoc } from "../types";
import {
  SOURCE_CAPABILITY_GUIDANCE,
  SOURCE_OUTPUT_GUIDANCE,
  type SourceOutputGuidance,
} from "./sourceOutputGuidance";

export interface StarterPrompt {
  label: string;
  sub?: string;
  kind: "onboarding" | "chat";
}

export function starterPrompts(discipline?: string): StarterPrompt[] {
  const known = discipline?.trim();
  return [
    {
      label: "New to this software, show me how to use this",
      kind: "onboarding",
    },
    {
      label:
        "What can Build-a-Spec do for me, and where do you need my judgment?",
      sub: "The two-minute pitch",
      kind: "chat",
    },
    {
      label: known
        ? "Start drafting my " +
          known +
          " section — show me what you assume as you go."
        : "Start drafting — I'll tell you the discipline, section, and project as we go.",
      sub: "Watch a section take shape immediately",
      kind: "chat",
    },
    {
      label:
        "Interview me about my project — one question at a time, with your " +
        "recommended default for each. I'll say 'I don't know' when I don't.",
      sub: "The guided interview",
      kind: "chat",
    },
    {
      label:
        "I have an office master spec for this section — explain how importing " +
        "and adapting it works here.",
      sub: "The other on-ramp",
      kind: "chat",
    },
  ];
}

export interface TourStep {
  id: string;
  /** A data-tour value. Empty means a centered explanatory card. */
  anchor: string;
  drawer?: "review" | "research" | "qc" | "openItems";
  title: string;
  body: string;
  details?: readonly SourceOutputGuidance[];
  placement?: "top" | "bottom" | "left" | "right";
  continueLabel?: string;
}

export interface TourChunk {
  id: string;
  title: string;
  steps: TourStep[];
}

export const TOUR: readonly TourChunk[] = [
  {
    id: "reading",
    title: "Reading the page",
    steps: [
      {
        id: "panes",
        anchor: "chat-pane",
        placement: "right",
        title: "Two panes, one document",
        body:
          "The left side is the conversation. The paper on the right is the " +
          "real specification: model drafting lands there as structured, " +
          "addressable content rather than as spec text in chat.",
      },
      {
        id: "project-heading",
        anchor: "project-heading",
        placement: "bottom",
        title: "Project context at a glance",
        body:
          "This heading stays blank until the discipline is known. It shows " +
          "only the discipline until the facility type and city/region are " +
          "also established, then expands to Discipline · Project Type · " +
          "City, Region. Chat corrections and document versions update it.",
      },
      {
        id: "document",
        anchor: "doc-panel",
        placement: "left",
        title: "The specification document",
        body:
          "Section number, title, articles, and provisions form a structured " +
          "CSI SectionFormat document. On an empty session this panel is ready " +
          "for either conversation-led drafting or a master-spec import.",
      },
      {
        id: "statuses",
        anchor: "doc-panel",
        placement: "left",
        title: "Every block wears its provenance",
        body:
          "Amber assumed is a defensible default awaiting review; red " +
          "needs-input cannot stand without a decision; blue imported is " +
          "master content awaiting project review. Confirmed content is the " +
          "material the user has stated or approved.",
      },
      {
        id: "open-items",
        anchor: "open-items",
        drawer: "openItems",
        placement: "top",
        title: "Nothing unresolved can hide",
        body:
          "TBD markers and needs-input blocks collect here and remain visible " +
          "in exports until resolved. The drawer is a navigable inventory of " +
          "decisions, not a second copy of the document.",
      },
      {
        id: "lint",
        anchor: "lint-issues",
        placement: "top",
        title: "Advisory lint",
        body:
          "Deterministic checks for stale editions, placeholders, and empty " +
          "or duplicate structure recompute after every change. They remain " +
          "advisory and never silently block an edit.",
      },
      {
        id: "standards",
        anchor: "standards-strip",
        placement: "top",
        title: "Standards editions in effect",
        body:
          "The active basis and any jurisdiction-adopted overrides stay " +
          "visible. Overrides require a stated adoption basis and are never " +
          "recorded silently.",
      },
      {
        id: "spend",
        anchor: "spend-pill",
        placement: "bottom",
        title: "Live usage meter",
        body:
          "Estimated session spend is visible here. Settings contains the " +
          "full breakdown by interview, research, audit, and Final QC.",
      },
    ],
  },
  {
    id: "project",
    title: "Tell it about the project",
    steps: [
      {
        id: "interview",
        anchor: "composer",
        placement: "top",
        title: "Real work is an interview",
        body:
          "Questions arrive with a recommended answer, and “I don't know” is " +
          "a valid response: the model can apply a defensible default and " +
          "mark it assumed instead of stalling the draft.",
      },
      {
        id: "suggested-replies",
        anchor: "composer",
        placement: "top",
        title: "Suggested replies",
        body:
          "When a useful next move is clear, contextual reply chips can appear " +
          "above the composer. They are optional shortcuts; normal typed " +
          "conversation always remains available outside the tour.",
      },
      {
        id: "profile",
        anchor: "research-drawer",
        drawer: "research",
        placement: "top",
        title: "The project profile",
        body:
          "Research uses four explicit facts: city, state/province, country, " +
          "and client. Conversation can record them, and the same values are " +
          "visible in the Research panel for later review or correction.",
      },
      {
        id: "research",
        anchor: "research-start",
        drawer: "research",
        placement: "top",
        title: "Grounded requirements research",
        body:
          "A deliberate web sweep covers governing codes, AHJ rules, client " +
          "standards, and site conditions. Retrieved evidence is distinguished " +
          "from unverified leads, and grounded items can carry into provisions.",
      },
      {
        id: "draft-full",
        anchor: "draft-full",
        placement: "bottom",
        title: "One-pass full draft",
        body:
          "On an empty or sparse document, this control can draft the complete " +
          "section from the profile, research, and interview decisions. " +
          "Unavailable controls explain what prerequisite is missing.",
      },
    ],
  },
  {
    id: "yours",
    title: "Make it yours",
    steps: [
      {
        id: "inline-edit",
        anchor: "doc-panel",
        placement: "left",
        title: "Edit directly on the paper",
        body:
          "Document blocks expose direct edit, confirm, delete, and structural " +
          "controls when permitted. Imported DOCX content uses server-derived " +
          "permissions so unsupported source mutations remain disabled.",
      },
      {
        id: "versions",
        anchor: "version-stepper",
        placement: "bottom",
        title: "Every change is a version",
        body:
          "Each model turn and manual edit commits one undoable version. The " +
          "history survives project save and reopen, including project identity " +
          "and profile changes.",
      },
      {
        id: "review",
        anchor: "review-drawer",
        drawer: "review",
        placement: "top",
        title: "The review queue",
        body:
          "The queue walks assumed and imported blocks in document order for " +
          "fast confirmation, editing, deletion, or follow-up with the model.",
      },
      {
        id: "compare",
        anchor: "compare",
        placement: "bottom",
        title: "Compare any two versions",
        body:
          "Insertions and deletions use the same semantic diff engine that " +
          "produces the Word redline export. Imported baselines represent the " +
          "normalized provision extraction.",
      },
      {
        id: "import",
        anchor: "import-master",
        placement: "bottom",
        title: "The other on-ramp",
        body:
          "A supported office DOCX can seed a blank document with imported " +
          "provenance. The exact source package remains available while only " +
          "server-proven edits enter the source-preserving path. " +
          SOURCE_CAPABILITY_GUIDANCE,
      },
    ],
  },
  {
    id: "ship",
    title: "Out the door",
    steps: [
      {
        id: "qc",
        anchor: "qc-drawer",
        drawer: "qc",
        placement: "top",
        title: "Final QC",
        body:
          "Five review lenses examine the finished draft, and candidate " +
          "findings face adversarial verification before any ready-to-apply " +
          "fix is offered.",
      },
      {
        id: "readiness",
        anchor: "readiness",
        drawer: "qc",
        placement: "top",
        title: "The out-the-door checklist",
        body:
          "The deterministic checklist covers open items, unreviewed blocks, " +
          "lint, research currency, and Final QC status without another model " +
          "call.",
      },
      {
        id: "export",
        anchor: "export",
        placement: "bottom",
        title: "Export guarantees",
        body:
          "Imported projects expose distinct output guarantees so a " +
          "source-preserving result is never silently substituted with a " +
          "normalized one.",
        details: SOURCE_OUTPUT_GUIDANCE,
      },
      {
        id: "save-open",
        anchor: "save",
        placement: "bottom",
        title: "Projects are one file",
        body:
          "A .baspec project carries conversation, versions, identity, " +
          "research, QC, and the exact imported source when present. Opening " +
          "it restores the working state.",
      },
      {
        id: "settings",
        anchor: "settings",
        placement: "bottom",
        title: "Settings",
        body:
          "Settings contains key management, the usage breakdown, and app " +
          "updates. API keys use the operating system credential store when " +
          "available.",
      },
      {
        id: "finish",
        anchor: "new-session",
        placement: "bottom",
        title: "Start the next project",
        body:
          "New session offers a blank slate now. Built-in templates and loading " +
          "a personal template are visible but disabled while those features " +
          "are in development.",
        continueLabel: "Finish",
      },
    ],
  },
];

/** CSS selector for a stable tour anchor, or null for a centered card. */
export function anchorSelector(
  step: TourStep,
  _doc: SpecDoc | null,
): string | null {
  if (!step.anchor) return null;
  return '[data-tour="' + step.anchor + '"]';
}
