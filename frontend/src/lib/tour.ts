/**
 * The guided tour (Batch 6): starter prompts, the discipline picker's
 * options, and the tour itself — chunks of steps, each step anchored to a
 * real UI element and carrying its bubble copy and optional actions.
 *
 * Pure data + pure resolvers over the doc snapshot (the `reviewQueue.ts`
 * convention): no React, no fetches. The state machine lives in
 * `useOnboarding.ts`; the rendering in `OnboardingOverlay.tsx`.
 */
import type { SpecDoc, DocParagraph } from "../types";
import { buildQueue } from "./reviewQueue";

/* --- Starter prompts (the empty-chat chips) --- */

export interface StarterPrompt {
  /** The chip text. For `kind: "chat"` it is sent verbatim as the message. */
  label: string;
  /** Faint sub-line under the label (the onboarding chip's is dynamic). */
  sub?: string;
  kind: "onboarding" | "chat";
}

/**
 * The empty-chat starter chips, tailored to the session's discipline. Only the
 * "start drafting" chip is discipline-specific; the rest are discipline-neutral
 * on-ramps. When no discipline is set (neutral boot, or a curated module that
 * implies its own), the drafting chip is generic and the model asks. The
 * project-description primer the user may enter in the picker is deliberately
 * NOT embedded here — the model receives it via PROJECT CONTEXT, so clicking
 * the drafting chip already yields project-specific output.
 */
export function starterPrompts(discipline?: string): StarterPrompt[] {
  const d = discipline?.trim();
  return [
    {
      // Verbatim by request — the onboarding entry point.
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
      label: d
        ? `Start drafting my ${d} section — show me what you assume as you go.`
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

/* --- Discipline picker --- */

export const DISCIPLINES: readonly string[] = [
  "Fire Protection & Suppression",
  "Mechanical (HVAC)",
  "Plumbing",
  "Electrical",
];

/** The deterministic demo profile the "Do this for me" step records. */
export const DEMO_PROFILE = {
  city: "Phoenix",
  state: "Arizona",
  country: "USA",
  client: "Demo Client (tour)",
} as const;

/** Composer prefill for the profile step's "I'll type it" alternative. */
export const PROFILE_PREFILL =
  "The project is in <city>, <state>, <country>, and the client is <client>.";

/* --- Tour structure --- */

export type TourActionKind =
  | "profile-fill" // POST /api/doc/edit set_project_profile with DEMO_PROFILE
  | "confirm-first" // confirm the first outstanding review-queue entry
  | "run-research" // launch the real research run (user-approved cost)
  | "run-qc" // launch the real Final QC run (user-approved cost)
  | "prefill-composer"; // pause the tour and put text in the composer

export interface TourAction {
  kind: TourActionKind;
  label: string;
  /** Small-print honesty line under the button (cost / time / effect). */
  note?: string;
  /** For `prefill-composer`. */
  prefillText?: string;
}

export interface TourStep {
  id: string;
  /**
   * Anchor: a `data-tour` attribute value, or a literal `el-…` DOM id
   * (the document panel's stable element ids). Empty string = centered
   * card with no spotlight.
   */
  anchor: string;
  /** Resolve the anchor from the doc snapshot instead (overrides anchor). */
  resolve?: "first-assumed" | "first-paragraph";
  /** Drawer that must be open for the anchor to exist (openNonce bump). */
  drawer?: "review" | "research" | "qc" | "openItems";
  title: string;
  body: string;
  /** Preferred bubble side; the overlay flips it when out of room. */
  placement?: "top" | "bottom" | "left" | "right";
  /** Label for the Continue button (e.g. "Skip for now" on run-it steps). */
  continueLabel?: string;
  actions?: TourAction[];
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
          "This side is the conversation — you answer, ask, decide. The " +
          "paper on the right is the real specification: the model edits " +
          "it through a document tool and never writes spec text in chat.",
      },
      {
        id: "header",
        anchor: "el-sec",
        placement: "left",
        title: "The section header",
        body:
          "The number and title were set by the model with the same edit " +
          "operation it uses for every provision. Nothing on the paper is " +
          "decoration — every block is real, addressable content.",
      },
      {
        id: "statuses",
        anchor: "doc-panel",
        resolve: "first-assumed",
        placement: "left",
        title: "Every block wears its provenance",
        body:
          "Amber assumed is a defensible default you haven't confirmed; " +
          "red needs-input can't stand without you; blue imported is " +
          "master content awaiting review. You've told the model nothing " +
          "yet, so the demo is almost all assumed — honest over-flagging " +
          "is the point. Hover the block: ✓ confirm, ✏️ edit and 🗑 delete " +
          "appear.",
      },
      {
        id: "open-items",
        anchor: "open-items",
        drawer: "openItems",
        placement: "top",
        title: "Nothing unresolved can hide",
        body:
          "The demo deliberately left one [TBD: …] marker and one " +
          "needs-input block; they're tracked here — and scheduled in the " +
          "export — until resolved. Click an item to jump to its block.",
      },
      {
        id: "lint",
        anchor: "lint-issues",
        placement: "top",
        title: "Advisory lint",
        body:
          "Deterministic checks — stale standard editions, placeholders, " +
          "empty articles — recomputed after every change. Advisory only: " +
          "lint never blocks an edit, and the model fixes findings as it " +
          "touches the affected blocks. A clean demo may show none.",
      },
      {
        id: "standards",
        anchor: "standards-strip",
        placement: "top",
        title: "Standards editions in effect",
        body:
          "Drafting defaults to current published editions. When your " +
          "jurisdiction has adopted an earlier one, the model records an " +
          "override with the stated adoption as its basis — never silently.",
      },
      {
        id: "spend",
        anchor: "spend-pill",
        placement: "bottom",
        title: "Real tokens, live meter",
        body:
          "That demo ran on your API key — a few cents. This pill " +
          "estimates session spend as you go; the full breakdown lives in " +
          "Settings.",
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
          "Every question arrives with a recommended answer, and \"I " +
          "don't know\" is a first-class reply — the model applies the " +
          "default and stamps the block assumed. Ask it to guide you and " +
          "open questions become concrete options with plain tradeoffs.",
      },
      {
        id: "profile",
        anchor: "research-drawer",
        drawer: "research",
        placement: "top",
        title: "The project profile",
        body:
          "Research needs four facts: city, state, country, client. " +
          "Mention them in chat and the model records them, fill the form " +
          "right here in the drawer — or take the demo shortcut below and " +
          "watch the research button unlock.",
        actions: [
          {
            kind: "profile-fill",
            label: "Do this for me",
            note:
              "Records a demo profile — Phoenix, Arizona, USA, " +
              "Demo Client (tour) — as one undoable edit.",
          },
          {
            kind: "prefill-composer",
            label: "I'll type it",
            prefillText: PROFILE_PREFILL,
            note: "Puts a fill-in sentence in the composer; the tour waits.",
          },
        ],
      },
      {
        id: "research",
        anchor: "research-start",
        drawer: "research",
        placement: "top",
        title: "Grounded requirements research",
        body:
          "A systematic web sweep of governing codes, AHJ rules, and " +
          "client standards for your jurisdiction. Findings cite pages " +
          "the tools actually retrieved; anything unverifiable is marked " +
          "a lead, not a fact — and grounded items feed the draft with " +
          "◆ provenance chips.",
        continueLabel: "Skip for now",
        actions: [
          {
            kind: "run-research",
            label: "Run it now",
            note:
              "Real run on your API key — several minutes and real " +
              "spend (watch the meter). The tour continues while it runs.",
          },
        ],
      },
      {
        id: "draft-full",
        anchor: "draft-full",
        placement: "bottom",
        title: "One-pass full draft",
        body:
          "On an empty or sparse page this button drafts the entire " +
          "section from everything known — profile, research, your " +
          "answers. The demo already has its articles, so it's resting; " +
          "hover it — disabled controls here always explain themselves.",
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
        resolve: "first-paragraph",
        placement: "left",
        title: "Edit directly on the paper",
        body:
          "Hover any block: ✏️ rewrites it (stamped confirmed), ✓ " +
          "confirms it as-is, 🗑 deletes it. No chat round-trip — the " +
          "model simply sees the result next turn. Try confirming this one.",
      },
      {
        id: "versions",
        anchor: "version-stepper",
        placement: "bottom",
        title: "Every change is a version",
        body:
          "Each model turn and each manual edit commits one undoable " +
          "version. Step back with ‹ and forward with › — nothing is " +
          "ever lost, and the history survives project save and reopen.",
      },
      {
        id: "review",
        anchor: "review-drawer",
        drawer: "review",
        placement: "top",
        title: "The review queue",
        body:
          "Walks every assumed and imported block in document order at " +
          "keyboard speed — K keep, E edit, D delete, A ask the model " +
          "about it. This is how a senior reviewer clears a draft's " +
          "guesses in one pass.",
        actions: [
          {
            kind: "confirm-first",
            label: "Do this for me",
            note: "Confirms the first outstanding block (one undo step).",
          },
        ],
      },
      {
        id: "compare",
        anchor: "compare",
        placement: "bottom",
        title: "Compare any two versions",
        body:
          "Insertions green, deletions struck — the same diff engine " +
          "that produces the Word redline export. With an imported " +
          "DOCX, the baseline is the normalized provision extraction, " +
          "not the original Word package.",
      },
      {
        id: "import",
        anchor: "import-master",
        placement: "bottom",
        title: "The other on-ramp",
        body:
          "Instead of drafting from scratch, extract supported body " +
          "content from an office .docx into a blank session: every " +
          "block lands stamped imported and the interview pivots to " +
          "adapting it. The exact source package is retained, and only " +
          "verified simple body-text edits are allowed through the " +
          "source-preserving path. It's disabled now " +
          "because this session has content.",
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
        title: "Final QC on Fable 5",
        body:
          "Five review lenses fan out over the finished draft, every " +
          "finding faces an adversarial verification panel, and " +
          "survivors arrive with ready-to-apply fixes. The deliberate, " +
          "expensive last pass before a section ships.",
        continueLabel: "Skip for now",
        actions: [
          {
            kind: "run-qc",
            label: "Run it now",
            note:
              "The most expensive feature — Fable 5, several minutes of " +
              "real spend. On this tiny demo it's purely a demonstration.",
          },
        ],
      },
      {
        id: "readiness",
        anchor: "readiness",
        drawer: "qc",
        placement: "top",
        title: "The out-the-door checklist",
        body:
          "Deterministic — no model call: open items, unreviewed blocks, " +
          "lint, research currency, QC status. When these are green, the " +
          "section is defensible.",
      },
      {
        id: "export",
        anchor: "export",
        placement: "bottom",
        title: "Export",
        body:
          "A clean .docx with the assumptions and open-items schedules — " +
          "or, after import, a preserved DOCX that clones the master and " +
          "patches only verified simple body text. Normalized DOCX and the " +
          "extracted-provision redline remain explicit separate choices.",
      },
      {
        id: "save-open",
        anchor: "save",
        placement: "bottom",
        title: "Projects are one file",
        body:
          "Save writes the whole session — conversation, every version, " +
          "research, QC, and the exact imported source when present — to " +
          "one .baspec file. Open resumes it exactly, undo and source-export " +
          "state included.",
      },
      {
        id: "settings",
        anchor: "settings",
        placement: "bottom",
        title: "Settings",
        body:
          "Key management (stored in your OS credential manager), the " +
          "usage breakdown behind the spend pill, and app updates.",
      },
      {
        id: "finish",
        anchor: "new-session",
        placement: "bottom",
        title: "That's the whole loop",
        body:
          "Interview → research → draft → review → QC → export. New " +
          "session gives you a blank page whenever you're ready — which " +
          "is exactly the next choice.",
        continueLabel: "Finish",
      },
    ],
  },
];

/* --- Pure resolvers over the doc snapshot --- */

function firstParagraph(doc: SpecDoc | null): DocParagraph | null {
  if (!doc) return null;
  for (const part of doc.parts) {
    for (const article of part.articles) {
      if (article.paragraphs.length > 0) return article.paragraphs[0];
    }
  }
  return null;
}

/**
 * The CSS selector for a step's anchor, or null when it can't exist yet.
 * Dynamic steps resolve against the doc snapshot; `el-…` anchors are the
 * document panel's stable DOM ids; everything else is a data-tour value.
 */
export function anchorSelector(step: TourStep, doc: SpecDoc | null): string | null {
  if (step.resolve === "first-assumed") {
    const entry = buildQueue(doc, "assumptions")[0];
    return entry ? `#el-${CSS.escape(entry.elementId)}` : null;
  }
  if (step.resolve === "first-paragraph") {
    const p = firstParagraph(doc);
    return p ? `#el-${CSS.escape(p.id)}` : null;
  }
  if (!step.anchor) return null;
  if (step.anchor.startsWith("el-")) return `#${CSS.escape(step.anchor)}`;
  return `[data-tour="${step.anchor}"]`;
}
