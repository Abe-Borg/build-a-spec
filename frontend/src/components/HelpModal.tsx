import { useEffect, useState, type ReactNode } from "react";
import type { Health, UpdateCheckPayload } from "../types";
import { TOUR } from "../lib/tour";
import {
  SOURCE_CAPABILITY_GUIDANCE,
  SOURCE_OUTPUT_GUIDANCE,
} from "../lib/sourceOutputGuidance";
import TrustDeepDiveModal from "./TrustDeepDiveModal";

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
  onStartTutorialAtChapter: (chapterId: string) => void;
  health: Health | null;
  /** The app's current answer, so About offers the same install the header does. */
  update: UpdateCheckPayload | null;
  installing: boolean;
  installError: string | null;
  /** Runs a forced check. The app owns the answer; this dialog only reads it. */
  onCheckUpdate: () => Promise<UpdateCheckPayload>;
  onInstallUpdate: () => void;
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

/** The five similar-looking, but intentionally distinct, source concepts. */
function SourceOutputGuide() {
  return (
    <div className="rounded-xl border border-edge bg-raised/40 p-4">
      <h4 className="font-[family-name:var(--font-display)] text-[15px] font-semibold text-ink">
        Know what each source option means
      </h4>
      <p className="mt-1 text-xs leading-relaxed text-ink-faint">
        Importing creates a semantic working document without changing the
        retained upload. These labels are different guarantees, not synonyms.
      </p>
      <dl className="mt-3 space-y-2.5">
        {SOURCE_OUTPUT_GUIDANCE.map((item) => (
          <div key={item.id}>
            <dt className="text-sm font-medium text-ink">{item.label}</dt>
            <dd className="mt-0.5 text-xs leading-relaxed text-ink-dim">
              {item.description}
            </dd>
          </div>
        ))}
      </dl>
      <p className="mt-3 border-t border-edge pt-3 text-xs leading-relaxed text-ink-dim">
        <span className="font-medium text-ink">Server capabilities:</span>{" "}
        {SOURCE_CAPABILITY_GUIDANCE}
      </p>
    </div>
  );
}

/* --- per-topic content --- */

function HowToUse({
  onStartTutorialAtChapter,
}: {
  onStartTutorialAtChapter: (chapterId: string) => void;
}) {
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
                code-based default and stamps the block{" "}
                <Tag>assumed</Tag> for later review.
              </>
            ),
          },
          {
            t: "Pick a starting point",
            d: (
              <>
                A blank page, a reusable template (bundled or your own), or an
                office master <Tag>.docx</Tag>. Template and master content
                lands stamped <Tag>imported</Tag> until you review it. For a
                master, Build-a-Spec keeps an immutable source copy and enables
                each imported-body action only when the server can prove that
                exact operation is safe. A disabled control shows the reason;
                review status and project metadata can remain editable even
                when body text is read-only.
              </>
            ),
          },
          {
            t: "Attach anything it should read",
            d: (
              <>
                An owner’s design standard, a basis-of-design narrative, a data
                sheet, a previous section — as <Tag>.docx</Tag>,{" "}
                <Tag>.pdf</Tag>, <Tag>.txt</Tag>, <Tag>.xml</Tag>, or{" "}
                <Tag>.csv</Tag>. Reference documents are background the model
                reads on demand; they never become part of the specification.
              </>
            ),
          },
          {
            t: "Run Research",
            d: "Once the project profile is complete, start Research to ground the requirements in the governing codes, AHJ amendments, and client standards for your jurisdiction.",
          },
          {
            t: "Draft the section",
            d: "Hit “Draft full section” to lay down every PART and article in one pass, or build it through chat. A full draft anchors on the section, the project type, and the country, so if any of the three is still unrecorded the button asks you about them first — answer, then draft. Once there is content on the page, the inline structure controls let you add and rearrange articles by hand.",
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
            d: "Choose the guarantee you need: the exact original; your imported file rebuilt with the new content (headers, footers, fonts, styles and page setup kept); a Build-a-Spec styled DOCX; or a normalized redline. Open in Word writes the rebuilt file to a temporary location and opens it in Word.",
          },
          {
            t: "Save, and keep the good work",
            d: (
              <>
                Save a <Tag>.baspec</Tag> project to restore the whole
                workspace later — document, versions, conversation, figures,
                references, research and QC records, and any retained source
                package. The first save of a session asks where it goes; every
                save after that overwrites that file, and <Tag>Save as…</Tag>
                beside Save writes a new one. Turn a finished section into a
                reusable template from the New session dialog, or export a{" "}
                <Tag>.basproject</Tag> project brief so the project's next
                section starts with its profile, editions, research,
                references and recorded facts already in hand.
              </>
            ),
          },
        ]}
      />
      <SourceOutputGuide />
      {/* A second, richer entry point into the tutorial than the header
          button: this one can start at any named chapter. */}
      <section
        className="rounded-xl border border-edge bg-raised/50 p-4"
        data-capability="help.topics tour.controls"
      >
        <h3 className="text-sm font-medium text-ink">Full guided tutorial</h3>
        <p className="mt-1 text-xs leading-relaxed text-ink-dim">
          A fixed track through every feature, shown against the complete bundled showcase
          spec in a protected workspace — nothing is asked of you, and your own project is
          set aside untouched and comes back when you finish. Start at the beginning or
          jump to a named chapter.
        </p>
        <button
          onClick={() => onStartTutorialAtChapter(TOUR[0].id)}
          className="mt-3 rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-accent-hover"
        >
          Restart full tutorial
        </button>
        <div className="mt-3 flex flex-wrap gap-1.5" aria-label="Tutorial chapters">
          {TOUR.map((chapter, index) => (
            <button
              key={chapter.id}
              onClick={() => onStartTutorialAtChapter(chapter.id)}
              className="rounded-md border border-edge bg-surface px-2 py-1 text-[11px] text-ink-dim hover:border-accent hover:text-accent"
              title={`Start at chapter ${index + 1}: ${chapter.title}`}
            >
              {index + 1}. {chapter.title}
            </button>
          ))}
        </div>
      </section>
      <p className="text-xs text-ink-faint">
        Every phase — research, QC, export — is something you trigger. The one
        automatic model turn is the debrief the chat sends itself when a
        research or Final QC run you started finishes — visible, stoppable,
        and switchable off.
      </p>
    </div>
  );
}

function Workflows() {
  return (
    <div className="space-y-4">
      <Lead>
        Several on-ramps converge on one review surface. Pick the recipe that
        matches what you’re starting with.
      </Lead>
      <Recipe
        title="From a blank page"
        tagline="Greenfield — no existing master to work from."
        steps={[
          "Add your key, then tell Claude the project basics.",
          "Run Research to ground the requirements for the jurisdiction.",
          "Use “Draft full section” for a complete first pass, then build out the hierarchy yourself with the inline structure controls.",
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
          "Source-preserving controls are enabled per block and operation. Verified simple text edits and bounded numbered-island structure can remain available while headings, tables, fields, hyperlinks, content controls, and complex runs stay read-only; hover a disabled action for the server's reason.",
          "Review status, research provenance, standards, and project metadata remain editable when the imported Word body is pass-through-only.",
          "Send to Final QC.",
          "Choose the exact-original download for unchanged upload bytes; Export Word (keeps your formatting) to get your file back with the new content; or intentionally choose the Build-a-Spec styled DOCX / normalized redline for the semantic view.",
        ]}
      />
      <Recipe
        title="From a reusable template"
        tagline="A native starter — your own past work, or a bundled one."
        steps={[
          "New session → browse the bundled starters and your personal library, preview one, and start from it.",
          "Template content lands imported, exactly like a master, so the same gap-and-adapt review applies — but there is no Word source package, so every editing control is available from the first turn.",
          "Tell Claude the project; it walks the starter article by article against this project's facts.",
          "Save a finished section back to the library from the New session dialog, or export a .bastemplate file to hand to a colleague.",
        ]}
      />
      <Recipe
        title="Start the next section of the same project"
        tagline="Carry the paid work forward — never the conversation."
        steps={[
          "In the finished section, Export → Export project brief. The .basproject carries the project profile and type, the recorded edition overrides, every research round, the attached reference documents (their full text), the established project facts, and a registry of the sections drafted so far — not the chat, the document, figures, or the Final QC report.",
          "New session → New section in an existing project → pick that file (or the finished section's .baspec directly — the brief is built from it). Read the manifest card, set the discipline if it differs, then start with a blank page or pair the brief with a template.",
          "The new section opens with the profile filled, the standards strip showing the carried editions, the references attached, the Project facts panel seeded, and the readiness checklist passing research as carried from the earlier section. Import a master afterwards if the section starts from one; the setup survives.",
          "Press Research once you have named the section: the round is briefed on everything already established and looks only for what is new, changed, or wrong here. Facts you settle in this section are recorded in the same panel, so the third section starts further along still.",
        ]}
      />
      <Recipe
        title="Draft against an owner's standard"
        tagline="Reference documents — read from, never edited."
        steps={[
          "Attach the owner standard, basis of design, or data sheet (.docx, .pdf, .txt, .xml, or .csv) from the document panel.",
          "Say what it is and what you want done with it. The model opens it on demand rather than being fed it every turn.",
          "It extracts requirements and drafts them in spec language — it never pastes the attachment's wording into a provision, and never cites it as authority for a code requirement.",
          "For a PDF, ask where something came from: the extracted text carries [page N] markers it can cite back to you.",
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
          "Use the inline add, ✏️, ✓, and 🗑 affordances on eligible blocks to create, edit, confirm, or delete without going through chat. Paragraphs can nest through four provision levels (A., 1., a., 1)); the editor refuses a fifth level.",
          "Reorder an article or provision only among its current siblings: drag its grip, or focus the grip and press Space/Enter to pick it up, Up/Down to move it, Space/Enter to drop, and Escape to cancel. The up/down buttons remain available as a fallback.",
          "For an imported DOCX, article changes and nested structure remain disabled. Only server-proven flat Word-numbered provisions and exact allowed positions are offered; hover a disabled control for the server's reason.",
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
            d: "The interview and drafting run on Claude Sonnet 5. Final QC runs a fleet of Claude Opus 5 reviewers — the one place a second model appears.",
          },
          {
            t: "Domain knowledge lives in spec modules",
            d: "Discipline expertise — catalog, playbook, standards pins, research dimensions — is packaged in registry-validated modules. One works across any discipline in the USA and Canada; others are deeply curated for a single domain. The engine itself is domain-neutral.",
          },
          {
            t: "Grounded research, not guesswork",
            d: "The Research phase fans out web searches across governing codes, AHJ requirements, client standards, and site environment, then grounds each finding against the pages the tools actually retrieved.",
          },
          {
            t: "Most of the app is not AI at all",
            d: "Lint, version history, the diff behind Compare and the redline, display numbering, the readiness checklist, the QC fix dry-run, DOCX import and source analysis, and every export writer are deterministic code — same input, same answer, no network call.",
          },
          {
            t: "Pinned standards editions",
            d: "Current published editions are the drafting default; a jurisdiction’s adopted earlier edition overrides only with the adoption basis stated — never silently.",
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
            t: "Background material stays out of the spec",
            d: "Attached reference documents are read on demand through a tool and never enter the document tree, lint, Compare, QC, or any export. Figures are exhibits that render in the chat — the enforceable words always live in a provision.",
          },
          {
            t: "Good work is reusable",
            d: "Any section can become a .bastemplate: one canonical document plus drafting-basis metadata, stored in your personal library and shareable as a file. Templates deliberately carry no conversation, research, QC, references, or Word source package.",
          },
          {
            t: "Direct structure editing stays bounded",
            d: "Add articles, provisions, and subparagraphs directly on the paper, up to the four existing provision levels. Grip reordering is sibling-only: it never moves content to another PART, reparents it, promotes or demotes it, or changes stable element IDs.",
          },
          {
            t: "Imported DOCX files have a narrow boundary",
            d: `Build-a-Spec retains an immutable source package and maps supported main-body content into a semantic tree. ${SOURCE_CAPABILITY_GUIDANCE} The final-state validator checks every submitted edit, and exact-original download remains available even when all Word-body mutations are disabled.`,
          },
          {
            t: "Numbering depends on export mode",
            d: "Fresh and normalized exports generate genuine Word numbering definitions and bindings. A source-preserving patched export retains the source's existing numbering; bounded structural edits are allowed only inside a server-proven numbered island, where Word's own list numbering handles the new order.",
          },
        ]}
      />
    </div>
  );
}

function WhyTrustIt({ onDeepDive }: { onDeepDive: () => void }) {
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
            d: "Every candidate finding faces a panel of independent Opus 5 refuters. A tie goes to the refuters, so plausible-but-wrong noise never reaches you — only real, actionable defects survive. Refuted findings stay in the report rather than being quietly deleted.",
          },
          {
            t: "The checks that gate a section aren’t model output",
            d: "Lint, version history, the diff behind Compare and the redline, the QC fix dry-run, source-preservation analysis, every export writer, and the readiness checklist are deterministic code. Same input, same answer, every time — and none of them makes a network call.",
          },
          {
            t: "The redline scope is explicit",
            d: "The imported redline compares Build-a-Spec's normalized provision tree with its extracted baseline. It does not compare headers, footers, styles, tables, layout, or other original DOCX package content, and Reject All does not recreate the uploaded master.",
          },
          {
            t: "Standards carry receipts",
            d: "Every pinned edition has documented provenance; jurisdiction overrides always state their adoption basis, and a module that pins nothing records every edition with the basis it came from.",
          },
          {
            t: "No model runs you did not set in motion",
            d: "Research, Final QC, drafting, and export all start with a click of yours. The one automatic follow-up is the completion debrief: when a run you started finishes, the chat sends itself a single visible turn that summarizes the findings and asks whether to proceed — it never applies changes by itself and can be switched off. Beyond that, the app's only unprompted request is the daily version check to GitHub, which carries nothing about your project and can also be switched off.",
          },
          {
            t: "Your key and spend stay in view",
            d: "The API key lives in your OS credential manager and is sent nowhere but the Anthropic API. A live meter shows the estimated cost as you go.",
          },
        ]}
      />
      <div className="rounded-xl border border-edge bg-raised/40 p-4">
        <h3 className="text-sm font-medium text-ink">Not convinced?</h3>
        <p className="mt-1 text-xs leading-relaxed text-ink-dim">
          Fair. The points above are claims — here is the mechanism behind each
          one, action by action, plus how to audit any of it yourself.
        </p>
        <button
          onClick={onDeepDive}
          data-capability="help.trust"
          className="mt-3 rounded-lg border border-accent/60 bg-accent/10 px-3 py-1.5 text-xs font-medium text-accent transition-colors hover:bg-accent/20"
        >
          I’m not convinced — show me exactly what runs →
        </button>
      </div>
    </div>
  );
}

function About({
  health,
  update,
  installing,
  installError,
  onCheckUpdate,
  onInstallUpdate,
}: {
  health: Health | null;
  update: UpdateCheckPayload | null;
  installing: boolean;
  installError: string | null;
  onCheckUpdate: () => Promise<UpdateCheckPayload>;
  onInstallUpdate: () => void;
}) {
  const [updateMsg, setUpdateMsg] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  /**
   * A forced check, run through the app rather than fetched here.
   *
   * That hand-off is the whole point. This panel used to hold the result in
   * its own state and tell the user to "see the header to install" — but the
   * header renders the app's copy, which comes from the throttled check at
   * launch. Whenever that one was throttled, errored, or simply ran before
   * the release existed, the header showed nothing, and the one control that
   * had just confirmed an update pointed at an empty corner of the screen.
   * The app also sequences the two callers, so a slow launch check cannot
   * land on top of this answer afterwards.
   */
  const runUpdateCheck = async () => {
    setChecking(true);
    setUpdateMsg("Checking…");
    try {
      const r = await onCheckUpdate();
      if (r.status === "UPDATE_AVAILABLE" && r.version) {
        // The install row below renders it — a message would be redundant.
        setUpdateMsg(null);
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

  const available = update?.status === "UPDATE_AVAILABLE" && !!update.version;

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
          <dd className="text-ink">Claude Opus 5</dd>
        </div>
        <div className="flex gap-3">
          <dt className="w-28 flex-none text-ink-faint">Scope</dt>
          <dd className="text-ink">
            Any discipline, USA &amp; Canada
          </dd>
        </div>
      </dl>
      <Lead>
        Sibling to{" "}
        <span className="text-ink">Spec Critic</span>: Build-a-Spec writes specs
        through dialogue; Spec Critic reviews finished specs.
      </Lead>
      <div className="space-y-3 pt-1">
        <div className="flex flex-wrap items-center gap-3">
          <button
            onClick={runUpdateCheck}
            disabled={checking}
            data-capability="updates.manage"
            className="rounded-lg border border-edge bg-raised px-3 py-1.5 text-sm text-ink transition-colors hover:border-accent hover:text-accent disabled:pointer-events-none disabled:opacity-40"
          >
            Check for updates
          </button>
          {updateMsg && (
            <span className="text-xs text-ink-faint">{updateMsg}</span>
          )}
        </div>
        {available && (
          <div className="rounded-xl border border-accent/50 bg-accent/10 p-4">
            <p className="text-sm font-medium text-ink">
              Version {update?.version} is available.
            </p>
            {update?.notes && (
              <p className="mt-1 whitespace-pre-line text-xs leading-relaxed text-ink-dim">
                {update.notes}
              </p>
            )}
            {update?.platform_supported ? (
              <>
                <button
                  onClick={onInstallUpdate}
                  disabled={installing}
                  data-capability="updates.manage"
                  className="mt-3 rounded-lg border border-accent/60 bg-accent/15 px-3 py-1.5 text-sm font-medium text-accent transition-colors hover:bg-accent/25 disabled:pointer-events-none disabled:opacity-60"
                >
                  {installing
                    ? "Downloading the installer…"
                    : `Install v${update?.version}`}
                </button>
                <p className="mt-2 text-xs text-ink-faint">
                  Downloads the installer over https, checks it against the
                  SHA-256 in the release manifest, and only then runs it. The
                  app closes so the installer can replace it. Large downloads
                  take a while — the button stays busy until it is verified.
                </p>
              </>
            ) : (
              <>
                <a
                  href={update?.releases_url}
                  target="_blank"
                  rel="noreferrer"
                  data-capability="updates.manage"
                  className="mt-3 inline-block rounded-lg border border-accent/60 bg-accent/15 px-3 py-1.5 text-sm font-medium text-accent transition-colors hover:bg-accent/25"
                >
                  Open the releases page
                </a>
                <p className="mt-2 text-xs text-ink-faint">
                  The installer is a Windows build, so it cannot be launched
                  from here on this platform.
                </p>
              </>
            )}
            {installError && (
              <p className="mt-2 text-xs text-danger">
                Update failed: {installError}
              </p>
            )}
          </div>
        )}
      </div>
      <div className="space-y-1.5 border-t border-edge pt-4 text-xs text-ink-faint">
        <p>© 2026 Abraham Borg. Source-available under the PolyForm Shield License 1.0.0.</p>
        <p className="flex flex-wrap gap-x-4 gap-y-1">
          <a
            href="https://www.linkedin.com/in/abrahamborg/"
            target="_blank"
            rel="noopener noreferrer"
            className="text-accent underline underline-offset-2 hover:text-accent-hover"
          >
            LinkedIn
          </a>
          <a
            href="https://github.com/Abe-Borg"
            target="_blank"
            rel="noopener noreferrer"
            className="text-accent underline underline-offset-2 hover:text-accent-hover"
          >
            GitHub
          </a>
        </p>
      </div>
    </div>
  );
}

type UpdateControls = {
  update: UpdateCheckPayload | null;
  installing: boolean;
  installError: string | null;
  onCheckUpdate: () => Promise<UpdateCheckPayload>;
  onInstallUpdate: () => void;
};

function Body({
  topic,
  health,
  onStartTutorialAtChapter,
  onDeepDive,
  updates,
}: {
  topic: HelpTopic;
  health: Health | null;
  onStartTutorialAtChapter: (chapterId: string) => void;
  onDeepDive: () => void;
  updates: UpdateControls;
}) {
  switch (topic) {
    case "how-to-use":
      return <HowToUse onStartTutorialAtChapter={onStartTutorialAtChapter} />;
    case "workflows":
      return <Workflows />;
    case "how-it-works":
      return <HowItWorks />;
    case "why-trust-it":
      return <WhyTrustIt onDeepDive={onDeepDive} />;
    case "about":
      return <About health={health} {...updates} />;
  }
}

export default function HelpModal({
  topic,
  onClose,
  onNavigate,
  onStartTutorialAtChapter,
  health,
  update,
  installing,
  installError,
  onCheckUpdate,
  onInstallUpdate,
}: Props) {
  // The "I'm not convinced" dossier stacks above this dialog. Kept as local
  // state so leaving the topic (or closing help entirely) drops it too.
  const [deepDive, setDeepDive] = useState(false);
  useEffect(() => {
    if (topic !== "why-trust-it") setDeepDive(false);
  }, [topic]);

  // Close on Escape while open — but yield to the dossier while it is up, so
  // one Escape closes one dialog. Two guards, because one is not enough:
  //
  //  - `deepDive` keeps this listener off entirely while the child dialog owns
  //    the keyboard. That is the intent.
  //  - `defaultPrevented` handles the race that intent alone misses. The
  //    dossier's handler is on `document`, ours is on `window`, and React
  //    flushes its close synchronously inside that native handler — so this
  //    effect has already re-run and re-attached by the time the SAME keydown
  //    finishes bubbling, and would close the help dialog too. The dossier
  //    calls preventDefault() before closing, so "already handled" is the
  //    reliable signal.
  useEffect(() => {
    if (!topic || deepDive) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !e.defaultPrevented) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [topic, deepDive, onClose]);

  if (!topic) return null;

  return (
    <>
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
        data-capability="help.topics"
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
          <Body
            topic={topic}
            health={health}
            onStartTutorialAtChapter={onStartTutorialAtChapter}
            onDeepDive={() => setDeepDive(true)}
            updates={{
              update,
              installing,
              installError,
              onCheckUpdate,
              onInstallUpdate,
            }}
          />
        </div>
      </div>
    </div>
    {/* Sibling, not a child: a click on the dossier's own backdrop must
        close only the dossier, never the help dialog underneath it. */}
    <TrustDeepDiveModal open={deepDive} onClose={() => setDeepDive(false)} />
    </>
  );
}
