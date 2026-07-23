import { useEffect, useState, type ReactNode } from "react";
import type { Health } from "../types";
import { checkUpdate } from "../lib/api";

/** The five info dialogs reachable from the header help nav. */
export type HelpTopic =
  | "how-to-use"
  | "workflows"
  | "how-it-works"
  | "why-trust-it"
  | "about";

/** Single source of truth for the header buttons and the in-modal tab strip. */
export const HELP_TOPICS: { id: HelpTopic; label: string }[] = [
  { id: "how-to-use", label: "How to use" },
  { id: "workflows", label: "Workflows" },
  { id: "how-it-works", label: "How it works" },
  { id: "why-trust-it", label: "Why trust it" },
  { id: "about", label: "About" },
];

const TITLES: Record<HelpTopic, string> = {
  "how-to-use": "How to use Build-a-Spec",
  workflows: "Typical workflows",
  "how-it-works": "How it works",
  "why-trust-it": "Why trust it?",
  about: "About",
};

interface Props {
  topic: HelpTopic | null;
  onClose: () => void;
  onNavigate: (topic: HelpTopic) => void;
  health: Health | null;
}

/* --- small presentational helpers, all on the existing palette --- */

function Lead({ children }: { children: ReactNode }) {
  return <p className="text-sm leading-relaxed text-ink-dim">{children}</p>;
}

function Tag({ children }: { children: ReactNode }) {
  return (
    <span className="rounded bg-raised px-1.5 py-0.5 text-[12px] text-ink">
      {children}
    </span>
  );
}

/** Numbered, badge-led steps (getting started / workflow recipes). */
function Steps({ items }: { items: { t: string; d?: ReactNode }[] }) {
  return (
    <ol className="mt-3 space-y-3">
      {items.map((s, i) => (
        <li key={i} className="flex gap-3">
          <span className="mt-0.5 flex h-5 w-5 flex-none items-center justify-center rounded-full bg-accent/15 text-[11px] font-semibold text-accent tabular-nums">
            {i + 1}
          </span>
          <div className="text-sm leading-relaxed text-ink-dim">
            <span className="font-medium text-ink">{s.t}</span>
            {s.d ? <> — {s.d}</> : null}
          </div>
        </li>
      ))}
    </ol>
  );
}

/** Bulleted feature/point list. */
function Points({ items }: { items: { t: string; d?: ReactNode }[] }) {
  return (
    <ul className="mt-3 space-y-3">
      {items.map((p, i) => (
        <li key={i} className="flex gap-2.5">
          <span className="mt-[7px] h-1.5 w-1.5 flex-none rounded-full bg-accent" />
          <div className="text-sm leading-relaxed text-ink-dim">
            <span className="font-medium text-ink">{p.t}</span>
            {p.d ? <> — {p.d}</> : null}
          </div>
        </li>
      ))}
    </ul>
  );
}

/** A titled workflow recipe card. */
function Recipe({
  title,
  tagline,
  steps,
}: {
  title: string;
  tagline: string;
  steps: ReactNode[];
}) {
  return (
    <div className="rounded-xl border border-edge bg-raised/40 p-4">
      <h4 className="font-[family-name:var(--font-display)] text-[15px] font-semibold text-ink">
        {title}
      </h4>
      <p className="mt-0.5 text-xs text-ink-faint">{tagline}</p>
      <ol className="mt-3 space-y-1.5">
        {steps.map((s, i) => (
          <li key={i} className="flex gap-2.5 text-sm text-ink-dim">
            <span className="text-ink-faint tabular-nums">{i + 1}.</span>
            <span className="leading-relaxed">{s}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

/* --- per-topic content --- */

function HowToUse() {
  return (
    <div className="space-y-4">
      <Lead>
        Build-a-Spec is a conversation. You talk through the project; Claude
        interviews you, drafts CSI SectionFormat language, and builds the
        section live in the panel on the right. A typical first pass:
      </Lead>
      <Steps
        items={[
          {
            t: "Add your Anthropic API key",
            d: (
              <>
                Click the gear (Settings) and paste your{" "}
                <span className="font-mono text-xs">sk-ant-…</span> key. It is
                stored in your OS credential manager and sent nowhere but the
                Anthropic API.
              </>
            ),
          },
          {
            t: "Tell Claude about the project",
            d: (
              <>
                Section, building location, client, hazard basics. Not sure
                about a detail? Say “I don’t know” — Claude applies a defensible
                NFPA&nbsp;13-2025 default and stamps the block{" "}
                <Tag>assumed</Tag> for later review.
              </>
            ),
          },
          {
            t: "Start from a master, or a blank page",
            d: (
              <>
                Optionally extract supported body content from an office master{" "}
                <Tag>.docx</Tag> to adapt — every block lands stamped{" "}
                <Tag>imported</Tag> until you review it — or draft the section
                from scratch. Build-a-Spec keeps an immutable source copy and
                only permits source-preserving edits it can prove are safe.
              </>
            ),
          },
          {
            t: "Run Research",
            d: "Once the project profile is complete, start Research to ground the requirements in the governing codes, AHJ amendments, and client standards for your jurisdiction.",
          },
          {
            t: "Draft the section",
            d: "Hit “Draft full section” to lay down every PART and article in one pass, or build it up article by article through chat.",
          },
          {
            t: "Walk the Review queue",
            d: (
              <>
                Step through every <Tag>assumed</Tag> / <Tag>imported</Tag>{" "}
                block at keyboard speed: <b>K</b>eep, <b>E</b>dit, <b>D</b>elete,
                or <b>A</b>sk the model.
              </>
            ),
          },
          {
            t: "Send to Final QC",
            d: "A spare-no-expense, adversarially-verified review that hands back verified findings, each with a ready-to-apply fix.",
          },
          {
            t: "Export",
            d: "Imported drafts can clone the source DOCX and patch verified simple body text, leaving headers, footers, numbering, styles, and unrelated package parts alone. Normalized DOCX and extracted-provision redline exports remain separate, explicit choices.",
          },
        ]}
      />
      <p className="text-xs text-ink-faint">
        Every phase — research, QC, export — is something you trigger. Nothing
        runs on its own.
      </p>
    </div>
  );
}

function Workflows() {
  return (
    <div className="space-y-4">
      <Lead>
        Two on-ramps converge on one review surface. Pick the recipe that
        matches what you’re starting with.
      </Lead>
      <Recipe
        title="From a blank page"
        tagline="Greenfield — no existing master to work from."
        steps={[
          "Add your key, then tell Claude the project basics.",
          "Run Research to ground the requirements for the jurisdiction.",
          "“Draft full section” for a complete first pass.",
          "Walk the Review queue, confirming or editing each assumption.",
          "Final QC, apply the fixes, export a clean .docx.",
        ]}
      />
      <Recipe
        title="From an office master"
        tagline="Adapt body content inside a deliberately narrow preservation boundary."
        steps={[
          "Import the master .docx after reviewing the preservation boundary — every extracted block lands imported.",
          "Save a native .baspec project; it carries the exact source DOCX with the semantic document and conversation.",
          "Tell Claude the project; it walks the extracted content article by article, adapting each block.",
          "P1 source-preserving mode accepts verified simple body-paragraph text replacements and refuses unsafe structural or complex-format edits.",
          "Send to Final QC.",
          "Choose Export preserved DOCX to clone-and-patch the master, or intentionally choose normalized DOCX / extracted-provision redline for the semantic view.",
        ]}
      />
      <Recipe
        title="Extracted-provision redline"
        tagline="Review content changes inside Build-a-Spec's normalized model."
        steps={[
          "Use Compare in the panel to diff any version against the imported extraction or a prior version.",
          "Export “Redline of extracted provisions” for Word tracked changes over normalized provision text. It is not a redline of the uploaded DOCX and cannot restore that file with Reject All.",
        ]}
      />
      <Recipe
        title="Quick question or spot edit"
        tagline="You don’t have to draft the whole section."
        steps={[
          "Ask about a single provision, a code citation, or an edition — Claude answers in chat and can edit just that block.",
          "Use the inline ✏️ / ✓ / 🗑 affordances on any block to edit, confirm, or delete it without going through chat.",
        ]}
      />
    </div>
  );
}

function HowItWorks() {
  return (
    <div className="space-y-4">
      <Lead>
        A chat pane on the left, a live document on the right — the way
        artifacts work in the Claude app. Under the hood:
      </Lead>
      <Points
        items={[
          {
            t: "Talk, and the document builds itself",
            d: "You describe the section; Claude edits a structured CSI SectionFormat document with tools, and every edit streams into the paper panel as it happens.",
          },
          {
            t: "Two models, one job each",
            d: "The interview and drafting run on Claude Sonnet 5. Final QC runs a fleet of Claude Fable 5 reviewers — the one place a second model appears.",
          },
          {
            t: "Domain knowledge lives in spec modules",
            d: "Discipline expertise — catalog, playbook, standards pins, research dimensions — is packaged in registry-validated modules. The first is Division 21 hyperscale fire suppression; the engine itself is domain-neutral.",
          },
          {
            t: "Grounded research, not guesswork",
            d: "The Research phase fans out web searches across governing codes, AHJ requirements, client standards, and site environment, then grounds each finding against the pages the tools actually retrieved.",
          },
          {
            t: "Pinned standards editions",
            d: "NFPA 13-2025 (current) is the drafting default; a jurisdiction’s adopted earlier edition overrides only with the adoption basis stated — never silently.",
          },
          {
            t: "Honest provenance on every provision",
            d: (
              <>
                Each block carries a status — <Tag>confirmed</Tag>,{" "}
                <Tag>assumed</Tag>, <Tag>needs input</Tag>, or{" "}
                <Tag>imported</Tag> — plus a link to the research item behind it.
                The export schedules every assumption and unreviewed block.
              </>
            ),
          },
          {
            t: "Imported DOCX files have a narrow boundary",
            d: "Build-a-Spec retains an immutable source package and maps supported main-body content into a semantic tree. Source-preserving export clones the package and replaces text only in unambiguous, simple body paragraphs; unsafe edits are refused. Headers, footers, numbering, styles, relationships, and unrelated package parts are never regenerated in this mode.",
          },
          {
            t: "Numbering depends on export mode",
            d: "Fresh and normalized exports still write calculated labels such as A., 1., a., and 1) as visible text, not real Word list bindings. A source-preserved export leaves the imported paragraph's existing Word numbering properties untouched, so it does not break numbering that was already present.",
          },
        ]}
      />
    </div>
  );
}

function WhyTrustIt() {
  return (
    <div className="space-y-4">
      <Lead>
        The whole design assumes a senior reviewer will check the output — so it
        never hides a guess and never claims more than it can show.
      </Lead>
      <Points
        items={[
          {
            t: "Nothing is silently guessed",
            d: (
              <>
                Every model assumption is stamped <Tag>assumed</Tag> and
                scheduled in the export, so a reviewer audits each guess in one
                pass. Over-flagging beats quietly confirming a guess.
              </>
            ),
          },
          {
            t: "Research is grounded to real sources",
            d: (
              <>
                A requirement is marked grounded only when a cited URL matches a
                page the web tools actually fetched; unverifiable leads are kept
                but tagged <Tag>[UNVERIFIED]</Tag> and never treated as fact.
              </>
            ),
          },
          {
            t: "QC findings are adversarially verified",
            d: "Every candidate finding faces a panel of independent Fable 5 refuters. A tie goes to the refuters, so plausible-but-wrong noise never reaches you — only real, actionable defects survive.",
          },
          {
            t: "The redline scope is explicit",
            d: "The imported redline compares Build-a-Spec's normalized provision tree with its extracted baseline. It does not compare headers, footers, styles, tables, layout, or other original DOCX package content, and Reject All does not recreate the uploaded master.",
          },
          {
            t: "Standards carry receipts",
            d: "Every pinned edition has documented provenance; jurisdiction overrides always state their adoption basis.",
          },
          {
            t: "Your key and spend stay in view",
            d: "The API key lives in your OS credential manager and is sent nowhere but the Anthropic API. A live meter shows the estimated cost as you go, and no phase runs without you triggering it.",
          },
        ]}
      />
    </div>
  );
}

function About({ health }: { health: Health | null }) {
  const [updateMsg, setUpdateMsg] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  const runUpdateCheck = async () => {
    setChecking(true);
    setUpdateMsg("Checking…");
    try {
      const r = await checkUpdate(true);
      if (r.status === "UPDATE_AVAILABLE" && r.version) {
        setUpdateMsg(`v${r.version} available — see the header to install.`);
      } else if (r.error) {
        setUpdateMsg(`Check failed: ${r.error}`);
      } else {
        setUpdateMsg("You’re on the latest version.");
      }
    } catch {
      setUpdateMsg("Update check failed.");
    }
    setChecking(false);
  };

  return (
    <div className="space-y-4">
      <div className="flex items-baseline gap-3">
        <h3 className="font-[family-name:var(--font-display)] text-xl font-semibold text-ink">
          Build-a-Spec
        </h3>
        <span className="text-sm text-ink-dim">
          v{health?.version ?? "…"}
        </span>
      </div>
      <Lead>
        Conversational authoring of construction specification sections. You
        talk through the project with Claude; it interviews you, drafts CSI
        SectionFormat language incrementally, and builds the section live in a
        document panel beside the chat.
      </Lead>
      <dl className="space-y-2 rounded-xl border border-edge bg-raised/40 p-4 text-sm">
        <div className="flex gap-3">
          <dt className="w-28 flex-none text-ink-faint">Interview</dt>
          <dd className="text-ink">{health?.model ?? "Claude Sonnet 5"}</dd>
        </div>
        <div className="flex gap-3">
          <dt className="w-28 flex-none text-ink-faint">Final QC</dt>
          <dd className="text-ink">Claude Fable 5</dd>
        </div>
        <div className="flex gap-3">
          <dt className="w-28 flex-none text-ink-faint">First domain</dt>
          <dd className="text-ink">
            Division 21 fire suppression for hyperscale data centers (USA)
          </dd>
        </div>
      </dl>
      <Lead>
        Sibling to{" "}
        <span className="text-ink">Spec Critic</span>: Build-a-Spec writes specs
        through dialogue; Spec Critic reviews finished specs.
      </Lead>
      <div className="flex flex-wrap items-center gap-3 pt-1">
        <button
          onClick={runUpdateCheck}
          disabled={checking}
          className="rounded-lg border border-edge bg-raised px-3 py-1.5 text-sm text-ink transition-colors hover:border-accent hover:text-accent disabled:pointer-events-none disabled:opacity-40"
        >
          Check for updates
        </button>
        {updateMsg && <span className="text-xs text-ink-faint">{updateMsg}</span>}
      </div>
    </div>
  );
}

function Body({ topic, health }: { topic: HelpTopic; health: Health | null }) {
  switch (topic) {
    case "how-to-use":
      return <HowToUse />;
    case "workflows":
      return <Workflows />;
    case "how-it-works":
      return <HowItWorks />;
    case "why-trust-it":
      return <WhyTrustIt />;
    case "about":
      return <About health={health} />;
  }
}

export default function HelpModal({
  topic,
  onClose,
  onNavigate,
  health,
}: Props) {
  // Close on Escape while open.
  useEffect(() => {
    if (!topic) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [topic, onClose]);

  if (!topic) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 p-6 pt-16"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={TITLES[topic]}
    >
      <div
        className="flex max-h-[80vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-edge bg-surface shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header: title + close */}
        <div className="flex items-center justify-between border-b border-edge px-6 py-3">
          <h2 className="font-[family-name:var(--font-display)] text-lg font-semibold">
            {TITLES[topic]}
          </h2>
          <button
            onClick={onClose}
            className="rounded-lg px-2 py-1 text-ink-dim transition-colors hover:text-ink"
            title="Close"
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        {/* Tab strip — jump between topics without reopening */}
        <div className="flex flex-wrap gap-1 border-b border-edge px-4 py-2">
          {HELP_TOPICS.map((t) => {
            const active = t.id === topic;
            return (
              <button
                key={t.id}
                onClick={() => onNavigate(t.id)}
                aria-current={active ? "page" : undefined}
                className={`rounded-md px-2.5 py-1 text-xs transition-colors ${
                  active
                    ? "bg-accent/15 text-accent"
                    : "text-ink-dim hover:bg-raised hover:text-ink"
                }`}
              >
                {t.label}
              </button>
            );
          })}
        </div>

        {/* Scrollable body */}
        <div className="overflow-y-auto px-6 py-5">
          <Body topic={topic} health={health} />
        </div>
      </div>
    </div>
  );
}
