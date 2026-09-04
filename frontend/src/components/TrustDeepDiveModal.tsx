/**
 * The "I'm not convinced" dossier — the long-form answer for a reader who
 * needs to know exactly where a provision came from and exactly what ran on
 * their behalf before they put their name on the output.
 *
 * Opened from the bottom of the "Why trust it?" help topic. Deliberately
 * long, deliberately specific, and written for a working AEC professional
 * rather than a software engineer: every mechanism is named, and every claim
 * here is one the reader can check against the app's own records (see the
 * closing "Check it yourself" section).
 *
 * Keep it honest. If the implementation changes, this changes with it — a
 * trust document that has drifted from the code is worse than none.
 */
import { useEffect, useRef, useState, type ReactNode } from "react";
import { SOURCE_OUTPUT_GUIDANCE } from "../lib/sourceOutputGuidance";
import { useDialogFocus } from "../lib/dialogFocus";

interface Props {
  open: boolean;
  onClose: () => void;
}

/** Left-rail contents. Ids double as scroll anchors. */
const SECTIONS: { id: string; label: string }[] = [
  { id: "summary", label: "The short answer" },
  { id: "origins", label: "Where the words come from" },
  { id: "models", label: "What “the model” is" },
  { id: "boundary", label: "What leaves your computer" },
  { id: "runtime", label: "What runs when you click" },
  { id: "authority", label: "What the model may touch" },
  { id: "grounded", label: "What “grounded” means" },
  { id: "deterministic", label: "The parts with no AI in them" },
  { id: "security", label: "Security & privacy" },
  { id: "money", label: "Money" },
  { id: "limits", label: "What this does not do" },
  { id: "verify", label: "Check it yourself" },
];

/* --------------------------------------------------------------------- */
/* Presentational helpers — same palette and idiom as HelpModal            */
/* --------------------------------------------------------------------- */

function Section({
  id,
  title,
  kicker,
  children,
}: {
  id: string;
  title: string;
  kicker?: string;
  children: ReactNode;
}) {
  return (
    <section id={id} className="scroll-mt-4 border-t border-edge pt-7 first:border-0 first:pt-0">
      {kicker && (
        <p className="text-[11px] font-medium uppercase tracking-wider text-accent">
          {kicker}
        </p>
      )}
      <h3 className="mt-1 font-[family-name:var(--font-display)] text-lg font-semibold text-ink">
        {title}
      </h3>
      <div className="mt-3 space-y-4">{children}</div>
    </section>
  );
}

function P({ children }: { children: ReactNode }) {
  return <p className="text-sm leading-relaxed text-ink-dim">{children}</p>;
}

function Tag({ children }: { children: ReactNode }) {
  return (
    <span className="rounded bg-raised px-1.5 py-0.5 text-[12px] text-ink">
      {children}
    </span>
  );
}

function Mono({ children }: { children: ReactNode }) {
  return (
    <span className="font-[family-name:var(--font-mono)] text-[12px] text-ink">
      {children}
    </span>
  );
}

function Bullets({ items }: { items: ReactNode[] }) {
  return (
    <ul className="space-y-2">
      {items.map((item, i) => (
        <li key={i} className="flex gap-2.5">
          <span className="mt-[7px] h-1.5 w-1.5 flex-none rounded-full bg-accent/70" />
          <div className="text-sm leading-relaxed text-ink-dim">{item}</div>
        </li>
      ))}
    </ul>
  );
}

/** A short, emphasised aside. `tone` shifts the rule colour, nothing else. */
function Note({
  title,
  tone = "accent",
  children,
}: {
  title?: string;
  tone?: "accent" | "warn" | "ok";
  children: ReactNode;
}) {
  const rule =
    tone === "warn"
      ? "border-l-warn"
      : tone === "ok"
        ? "border-l-ok"
        : "border-l-accent";
  return (
    <div className={`rounded-r-lg border-l-2 bg-raised/40 px-4 py-3 ${rule}`}>
      {title && (
        <p className="text-sm font-medium text-ink">{title}</p>
      )}
      <div className="text-sm leading-relaxed text-ink-dim">{children}</div>
    </div>
  );
}

/** Two-column "does / does not" contrast. */
function Contrast({
  leftTitle,
  left,
  rightTitle,
  right,
}: {
  leftTitle: string;
  left: ReactNode[];
  rightTitle: string;
  right: ReactNode[];
}) {
  const col = (title: string, items: ReactNode[], ok: boolean) => (
    <div className="rounded-xl border border-edge bg-raised/30 p-4">
      <h4 className={`text-sm font-medium ${ok ? "text-ok" : "text-err"}`}>
        {title}
      </h4>
      <ul className="mt-2.5 space-y-2">
        {items.map((item, i) => (
          <li key={i} className="flex gap-2 text-sm leading-relaxed text-ink-dim">
            <span className={`flex-none ${ok ? "text-ok" : "text-err"}`}>
              {ok ? "✓" : "✕"}
            </span>
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </div>
  );
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {col(leftTitle, left, true)}
      {col(rightTitle, right, false)}
    </div>
  );
}

/** A simple definition table. */
function Matrix({
  head,
  rows,
}: {
  head: string[];
  rows: ReactNode[][];
}) {
  return (
    <div className="overflow-x-auto rounded-xl border border-edge">
      <table className="w-full min-w-[34rem] border-collapse text-sm">
        <thead>
          <tr className="bg-raised/60 text-left">
            {head.map((h) => (
              <th
                key={h}
                className="border-b border-edge px-3 py-2 text-xs font-medium uppercase tracking-wide text-ink-faint"
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className="align-top">
              {row.map((cell, j) => (
                <td
                  key={j}
                  className={`border-b border-edge/60 px-3 py-2.5 leading-relaxed ${
                    j === 0 ? "text-ink" : "text-ink-dim"
                  }`}
                >
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * One user-triggered action, always answered in the same five terms so the
 * cards can be compared rather than merely read.
 */
function Runtime({
  n,
  title,
  trigger,
  runs,
  sent,
  model,
  bounds,
}: {
  n: number;
  title: string;
  trigger: ReactNode;
  runs: ReactNode;
  sent: ReactNode;
  model: ReactNode;
  bounds: ReactNode;
}) {
  const row = (label: string, value: ReactNode) => (
    <div className="flex flex-col gap-0.5 sm:flex-row sm:gap-3">
      <div className="w-32 flex-none pt-px text-[11px] font-medium uppercase tracking-wide text-ink-faint">
        {label}
      </div>
      <div className="text-sm leading-relaxed text-ink-dim">{value}</div>
    </div>
  );
  return (
    <div className="rounded-xl border border-edge bg-raised/30 p-4">
      <div className="flex items-baseline gap-2.5">
        <span className="flex h-5 w-5 flex-none items-center justify-center rounded-full bg-accent/15 text-[11px] font-semibold text-accent tabular-nums">
          {n}
        </span>
        <h4 className="font-[family-name:var(--font-display)] text-[15px] font-semibold text-ink">
          {title}
        </h4>
      </div>
      <div className="mt-3 space-y-2.5">
        {row("You do", trigger)}
        {row("What runs", runs)}
        {row("What is sent", sent)}
        {row("AI involved", model)}
        {row("Bounded by", bounds)}
      </div>
    </div>
  );
}

function Link({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="text-accent underline underline-offset-2 hover:text-accent-hover"
    >
      {children}
    </a>
  );
}

/* --------------------------------------------------------------------- */
/* The data-flow diagram (hand-authored inline SVG, no external assets)    */
/* --------------------------------------------------------------------- */

function DataFlowDiagram() {
  const label = {
    fill: "var(--color-ink)",
    fontSize: 12,
    fontFamily: "var(--font-body)",
  } as const;
  const small = {
    fill: "var(--color-ink-dim)",
    fontSize: 10.5,
    fontFamily: "var(--font-body)",
  } as const;
  const faint = {
    fill: "var(--color-ink-faint)",
    fontSize: 10,
    fontFamily: "var(--font-body)",
  } as const;

  return (
    <figure className="rounded-xl border border-edge bg-raised/30 p-4">
      <svg
        viewBox="0 0 720 268"
        className="w-full"
        role="img"
        aria-label="Data flow: your computer runs the app and holds every file; only model requests leave, over HTTPS, to the Anthropic API, which performs web searches on its own servers. A separate automatic update check contacts GitHub Releases for a version manifest."
      >
        <defs>
          <marker
            id="bas-arrow"
            markerWidth="9"
            markerHeight="9"
            refX="7"
            refY="4"
            orient="auto"
          >
            <path d="M0,0 L8,4 L0,8 z" fill="var(--color-accent)" />
          </marker>
          <marker
            id="bas-arrow-faint"
            markerWidth="9"
            markerHeight="9"
            refX="7"
            refY="4"
            orient="auto"
          >
            <path d="M0,0 L8,4 L0,8 z" fill="var(--color-ink-faint)" />
          </marker>
        </defs>

        {/* Your computer */}
        <rect
          x="6"
          y="18"
          width="268"
          height="196"
          rx="12"
          fill="var(--color-surface)"
          stroke="var(--color-ok)"
          strokeWidth="1.5"
        />
        <text x="20" y="42" {...label} fontWeight="600">
          Your computer
        </text>
        <text x="20" y="59" {...faint}>
          Everything below stays here
        </text>

        <rect
          x="20"
          y="72"
          width="240"
          height="32"
          rx="7"
          fill="var(--color-raised)"
          stroke="var(--color-edge)"
        />
        <text x="32" y="92" {...small}>
          Build-a-Spec · local server (127.0.0.1)
        </text>

        <rect
          x="20"
          y="114"
          width="240"
          height="86"
          rx="7"
          fill="var(--color-raised)"
          stroke="var(--color-edge)"
        />
        <text x="32" y="134" {...small}>
          Project files (.baspec) · exports
        </text>
        <text x="32" y="152" {...small}>
          Imported DOCX package bytes
        </text>
        <text x="32" y="170" {...small}>
          Reference files · figures
        </text>
        <text x="32" y="188" {...small}>
          API key · local traces &amp; logs
        </text>

        {/* Request / reply arrows */}
        <text x="280" y="86" {...faint}>
          HTTPS · your key
        </text>
        <line
          x1="278"
          y1="96"
          x2="392"
          y2="96"
          stroke="var(--color-accent)"
          strokeWidth="1.5"
          markerEnd="url(#bas-arrow)"
        />
        <text x="280" y="112" {...faint}>
          prompt + draft text
        </text>
        <line
          x1="392"
          y1="136"
          x2="278"
          y2="136"
          stroke="var(--color-accent)"
          strokeWidth="1.5"
          markerEnd="url(#bas-arrow)"
        />
        <text x="280" y="152" {...faint}>
          streamed reply
        </text>

        {/* Anthropic */}
        <rect
          x="398"
          y="48"
          width="170"
          height="118"
          rx="12"
          fill="var(--color-surface)"
          stroke="var(--color-accent)"
          strokeWidth="1.5"
        />
        <text x="412" y="72" {...label} fontWeight="600">
          Anthropic API
        </text>
        <text x="412" y="96" {...small}>
          Claude Sonnet 5
        </text>
        <text x="412" y="112" {...faint}>
          interview · drafting · research
        </text>
        <text x="412" y="136" {...small}>
          Claude Opus 5
        </text>
        <text x="412" y="152" {...faint}>
          Final QC only
        </text>

        {/* Web */}
        <line
          x1="570"
          y1="107"
          x2="588"
          y2="107"
          stroke="var(--color-accent)"
          strokeWidth="1.5"
          markerEnd="url(#bas-arrow)"
        />
        <rect
          x="592"
          y="72"
          width="126"
          height="70"
          rx="10"
          fill="var(--color-surface)"
          stroke="var(--color-edge)"
        />
        <text x="602" y="94" {...small}>
          Public web
        </text>
        <text x="602" y="112" {...faint}>
          searched and fetched
        </text>
        <text x="602" y="128" {...faint}>
          by Anthropic, not you
        </text>

        {/* Update check */}
        <line
          x1="120"
          y1="216"
          x2="120"
          y2="240"
          stroke="var(--color-ink-faint)"
          strokeWidth="1.2"
          strokeDasharray="4 4"
          markerEnd="url(#bas-arrow-faint)"
        />
        <text x="134" y="245" {...faint}>
          Automatic update check → GitHub Releases (version manifest only)
        </text>
      </svg>
      <figcaption className="mt-3 text-xs leading-relaxed text-ink-faint">
        There is no Build-a-Spec server, no account, and no cloud storage. The
        only routine outbound traffic is the model call you triggered.
      </figcaption>
    </figure>
  );
}

/* --------------------------------------------------------------------- */
/* Content                                                                */
/* --------------------------------------------------------------------- */

function Dossier() {
  return (
    <div className="space-y-7 pb-4">
      <Section id="summary" kicker="Start here" title="The short answer">
        <P>
          Build-a-Spec is a drafting instrument, not an authority. It produces a
          first draft and the apparatus to review it. Three commitments hold the
          rest of this document together:
        </P>
        <Bullets
          items={[
            <>
              <b className="text-ink">Every provision has a declared origin.</b>{" "}
              You said it, the discipline module supplied it, the model proposed
              it, or grounded research produced it — and each is labelled
              differently on the page and in the export.
            </>,
            <>
              <b className="text-ink">
                No model runs that you did not start — with one disclosed
                follow-up.
              </b>{" "}
              No auto-research, no silent re-draft, no scheduled job: every
              model call — and therefore every cent of spend — traces back to a
              message you sent or a button you pressed. The one automatic
              model turn is the <b className="text-ink">completion debrief</b>:
              when a research round or Final QC pass <em>you started</em>{" "}
              finishes, the chat sends itself one ordinary, visible turn that
              summarizes how the findings affect the draft and asks whether to
              proceed — it appears in the transcript like any message, can be
              stopped like any turn, never applies changes by itself, and can
              be switched off (BUILD_A_SPEC_AUTO_DEBRIEF=0). Beyond that, the
              only thing the app does on its own is check for a new version at
              startup, at most once a day; that request goes to GitHub, carries
              nothing about your project, and can be switched off.
            </>,
            <>
              <b className="text-ink">Every automated judgment is re-derivable.</b>{" "}
              Research items carry the URLs actually retrieved; QC findings carry
              every reviewer’s vote; and the app keeps a local trace of every
              request it made. The last section of this document tells you how to
              audit all of it.
            </>,
          ]}
        />
      </Section>

      <Section
        id="origins"
        kicker="Provenance"
        title="Where the words in your specification come from"
      >
        <P>
          Five origins, and only five. The app’s central discipline is that these
          never get mixed up silently.
        </P>
        <Matrix
          head={["Origin", "What it actually is", "How you can tell"]}
          rows={[
            [
              "You",
              "A fact you stated in chat, a form you filled in, or a proposal you explicitly approved.",
              <>
                Stamped <Tag>confirmed</Tag>.
              </>,
            ],
            [
              "The discipline module",
              "Human-written domain content compiled into the app: the section catalog, the interview playbook and each topic’s recommended default, the standards editions pinned as drafting defaults, and the discipline’s conventions. This is written by engineers into source code — it is not model output, and it is identical for every user on that module.",
              <>
                Stamped <Tag>assumed</Tag> when applied without your
                confirmation. Pinned editions carry written provenance in the
                repository.
              </>,
            ],
            [
              "The model’s own knowledge",
              "What Claude knows about specification practice, products, and standards from its training. Used for defaults the playbook does not cover, and for language.",
              <>
                Stamped <Tag>assumed</Tag>, and the model is instructed to say
                in one line what it assumed. Unresolvable values become{" "}
                <Mono>[TBD: …]</Mono> or <Tag>needs input</Tag> instead of an
                invented number.
              </>,
            ],
            [
              "Grounded research",
              "Findings from the Research run: live web retrieval against your jurisdiction, authority, client, and site, returned as structured items with citations.",
              <>
                A ◆ provenance chip on the provision linking to the research
                item. Items that could not be matched to a retrieved page are
                kept but marked <Mono>[UNVERIFIED]</Mono>.
              </>,
            ],
            [
              "Your own documents",
              "An imported office master, a reusable template, or an attached reference document (owner standard, basis of design, data sheet).",
              <>
                Master and template content lands stamped <Tag>imported</Tag>{" "}
                until reviewed. Reference documents never enter the
                specification at all.
              </>,
            ],
          ]}
        />
        <Note title="Things that are not happening" tone="ok">
          There is no hidden library of other firms’ specifications being mined
          for language. There is no vector database, no retrieval corpus, and no
          shared repository of user projects. Your project is not used to train a
          model — API traffic falls under Anthropic’s commercial terms, not the
          consumer product’s. Nothing you write is visible to another user of
          this application, because there is no other user: the app runs entirely
          on your machine.
        </Note>
      </Section>

      <Section id="models" kicker="The engine" title="What “the model” actually is">
        <P>
          Two Claude models, with strictly separated jobs. There is no model
          picker, because the routing is a design decision rather than a
          preference.
        </P>
        <Matrix
          head={["Job", "Model", "Why"]}
          rows={[
            [
              "Interview, drafting, research",
              <>
                Claude Sonnet 5 <Mono>(claude-sonnet-5)</Mono>
              </>,
              "Fast enough to hold a conversation while being strong enough to draft and to run the research fan-out. Reasoning effort is set to “high” for both the interview and research.",
            ],
            [
              "Final QC only",
              <>
                Claude Opus 5 <Mono>(claude-opus-5)</Mono>
              </>,
              "A stronger reasoning model than the one that drafts, chosen for a pass that has to catch what the drafter missed — it is measurably good at finding real defects without inventing them. It never touches the interview loop, and it is the only place a second model appears.",
            ],
          ]}
        />
        <P>
          Both run on Anthropic’s servers, not on your PC. Build-a-Spec itself is
          an ordinary Windows desktop program: it starts a small web server bound
          to <Mono>127.0.0.1</Mono> — your own machine, not reachable from your
          network — and displays its own interface in a native window. All the
          domain logic, your documents, and your files are local.
        </P>
        <P>
          Both models run with <b className="text-ink">adaptive thinking</b>: they
          reason internally before answering. Where the model streams a readable
          summary of that reasoning, the chat shows it in a collapsible block. It
          is a summary for your benefit and is never stored in your project file
          or sent back on the next turn.
        </P>
      </Section>

      <Section
        id="boundary"
        kicker="The boundary"
        title="What leaves your computer, and where it goes"
      >
        <DataFlowDiagram />
        <Contrast
          leftTitle="Leaves your machine"
          left={[
            <>
              <b className="text-ink">The content of each model request</b>: the
              system instructions, the conversation so far, and the PROJECT
              CONTEXT block — which includes the full current text of your draft
              section. Sent over TLS to Anthropic’s API and authenticated with
              your own API key. This is unavoidable: it is what the model reads
              in order to answer.
            </>,
            <>
              <b className="text-ink">Search terms and fetched pages</b>, during
              research, Final QC’s compliance lens, and mid-interview lookups.
              These run <em>on Anthropic’s servers</em>, not from your computer —
              the web sites you research never see your IP address.
            </>,
            <>
              <b className="text-ink">A version manifest request to GitHub.</b>{" "}
              This is the one request the app makes without being asked: it runs
              automatically at startup, at most once a day, and again whenever
              you press “Check for updates”. It sends nothing about your
              project, and it can be switched off entirely.
            </>,
          ]}
          rightTitle="Never leaves your machine"
          right={[
            <>
              <b className="text-ink">Your API key</b>, beyond the authorization
              header of your own API calls.
            </>,
            <>
              <b className="text-ink">Project files, exports, and the retained
              bytes of an imported DOCX.</b> Extracted provision text becomes
              part of the document the model reads; the file itself is never
              uploaded anywhere.
            </>,
            <>
              <b className="text-ink">Trace files</b>, which record every request
              the app made. They are written to your machine only.
            </>,
            <>
              <b className="text-ink">Telemetry, analytics, crash reports, usage
              statistics, licence checks.</b> There are none. The interface loads
              no scripts, fonts, or images from the internet.
            </>,
          ]}
        />
      </Section>

      <Section
        id="runtime"
        kicker="Runtime"
        title="What actually runs when you click"
      >
        <P>
          Every user-visible action, answered in the same five terms. Where an
          action involves no model at all, that is stated explicitly — it is
          often the most reassuring answer.
        </P>

        <Runtime
          n={1}
          title="You send a chat message (“a turn”)"
          trigger="Press Enter in the composer."
          runs={
            <>
              One streamed request to Anthropic. If the model decides to use a
              tool — edit the document, create a figure, read an attached
              reference, track or check off something it needs from you, run a
              web lookup — the app executes that tool locally,
              hands back the result, and the model continues in the same turn.
              Document edits stream into the panel as they are applied, which is
              why you watch the page fill in.
            </>
          }
          sent={
            <>
              Three parts: (a) a{" "}
              <b className="text-ink">fixed instruction block</b> built from the
              active discipline module — byte-identical every turn, so it is
              cached and billed at a fraction after the first call; (b) the{" "}
              <b className="text-ink">conversation so far</b>; and (c) a{" "}
              <b className="text-ink">PROJECT CONTEXT block</b> attached to your
              newest message, containing the current date and time from your own
              computer’s clock, the standards editions in effect, which
              project-profile fields are still missing, the imported-source
              boundary if any, the grounded research profile if any, the full
              text of every provision with its id and status, the deterministic
              lint report, the open-items list, the list of questions and
              decisions still waiting on you, a compact digest of the
              retained Final QC findings if a review has run (ids, severity,
              one-line issues, and whether each fix is verified — never the
              full report), and one-line stubs for figures
              and reference documents — never their contents.
            </>
          }
          model="Claude Sonnet 5, effort “high”."
          bounds={
            <>
              At most 8 web searches and 4 fetches per round; a hard ceiling of
              50 tool rounds as a runaway breaker no legitimate turn approaches.
              The turn is <b className="text-ink">atomic</b>: if anything fails,
              the document rolls back to its pre-turn state and the conversation
              is left untouched, so re-sending never duplicates work. On
              success, the whole turn commits as exactly one undo step. At commit
              the context block is stripped back out of stored history, so a
              stale snapshot of your document can never fossilize into the
              conversation.
            </>
          }
        />

        <Runtime
          n={2}
          title="“Draft the complete section”"
          trigger="Press the button in the document panel."
          runs={
            <>
              No separate drafting engine exists. The button fetches a fixed
              instruction written by the application (not by the model, and not
              editable from the interface), and sends it through the ordinary
              chat path as a visible message you can read in the transcript.
              Which instruction it fetches is decided by the application, not
              the model, from the document alone: a whole-section draft anchors
              on the section, the project type, and the country, so while any of
              the three is unrecorded the button sends an instruction that{" "}
              <b className="text-ink">asks you for them</b> and forbids drafting
              that turn. Both messages are visible in the transcript.
            </>
          }
          sent="Identical to a normal turn."
          model="Claude Sonnet 5 — one ordinary turn."
          bounds={
            <>
              Same atomicity as any turn: the entire first pass is{" "}
              <b className="text-ink">one undo step</b>. The instruction requires
              honest provenance stamping and explicitly forbids confirming a
              guess to look finished.
            </>
          }
        />

        <Runtime
          n={3}
          title="Research"
          trigger={
            <>
              Press <b className="text-ink">Start research</b>. The button is
              unavailable until city, state/province, country and client are
              recorded — the search results are worthless without them. Once a
              round has run, a second button appears offering to{" "}
              <b className="text-ink">retry only the areas that never
              completed</b>; the full re-run stays available beside it.
            </>
          }
          runs={
            <>
              Four independent agents run in parallel, one per dimension:{" "}
              <b className="text-ink">governing codes</b>,{" "}
              <b className="text-ink">authority-having-jurisdiction
              requirements</b>, <b className="text-ink">owner/client and insurer
              standards</b>, and{" "}
              <b className="text-ink">site and environmental factors</b> — or
              only the subset you asked to retry, in which case the others are
              not researched again at all. Each is
              a separate conversation with server-side web search and fetch
              enabled and your project’s own location attached to the search
              tool, so results are local rather than generic. Each must finish by
              submitting findings through a structured form — topic, category,
              requirement, authority, code reference, source URLs, confidence,
              notes — rather than as prose. The run outlives any chat turn; you
              can keep talking while it works.
            </>
          }
          sent={
            <>
              Today’s date, then the dimension’s instruction with your project
              profile substituted in, plus the discipline. On a second or later
              round each agent is also sent{" "}
              <b className="text-ink">the findings earlier rounds already
              established for its own dimension</b> — requirement, authority,
              code reference, date, and whether it was ever grounded — with an
              instruction to report only what is new, changed or corrected
              rather than derive it all again. Any{" "}
              <b className="text-ink">reference documents you have attached</b>{" "}
              are sent in full as well, so an agent researches whether what
              your owner requires is actually permitted here rather than
              re-deriving what the attached document already says. Your draft
              section is <b className="text-ink">not</b> sent — research looks
              outward, at the jurisdiction, not at your text.
            </>
          }
          model="Claude Sonnet 5, effort “high” — four concurrent long-running conversations."
          bounds={
            <>
              Per-dimension search budgets (up to 40 searches and 12 fetches for
              governing codes) with a hard 2× runaway ceiling; up to 16
              continuations when the API pauses a long-running turn; retries with
              backoff on transient failures, with the tokens each attempt burned
              still counted. Aggregators, Q&amp;A sites, other AI assistants’
              outputs, trade forums, and DIY content farms are blocked for both
              search and fetch. A single fetched page is capped so one enormous
              code page cannot swamp the run. One dimension failing never cancels
              the others — you get a profile flagged as partial; all four failing
              fails cleanly with nothing corrupted. Stopping discards the run,
              and the confirmation says so before you click. When a round
              completes, the chat automatically sends itself{" "}
              <b className="text-ink">one ordinary debrief turn</b> — visible
              in the transcript, billed like any turn, switchable off — that
              summarizes how the findings affect the draft, proposes changes
              without applying any, and asks whether to proceed.
            </>
          }
        />

        <Runtime
          n={4}
          title="Final QC"
          trigger="Press it and confirm the cost warning. Never automatic, never triggered by the model."
          runs={
            <>
              <b className="text-ink">A frozen copy</b> of the document is taken
              the instant you start, so a chat turn cannot change what is being
              reviewed mid-review. Then three phases:
              <br />
              <br />
              <b className="text-ink">1 · Five lenses, run concurrently</b>{" "}
              (eight calls in flight at a time, shared with stage 2). Five
              independent Opus 5 reviewers, none of which sees
              the others’ output: code &amp; standard compliance; PART 1/2/3
              coordination and
              consistency; completeness against scope and grounded research;
              enforceability and specification language; and provenance hygiene.
              Only the compliance lens gets web access — with a large search
              allowance and an explicit instruction to look the standard up
              rather than recall it, because article numbers are renumbered
              between editions. Each lens returns findings{" "}
              <em>and a record of the checks it ran</em>, including the ones that
              passed.
              <br />
              <br />
              <b className="text-ink">2 · Adversarial verification.</b> Every
              candidate finding is handed to a fresh panel of independent
              reviewers instructed to <em>refute</em> it — three for critical and
              high, two for medium and low. A finding survives only when{" "}
              <b className="text-ink">every seat upholds it</b>; a majority
              refuting kills it, and anything in between is recorded as{" "}
              <b className="text-ink">disputed</b> — a real disagreement that
              blocks issue readiness until you adjudicate it yourself
              (critical/high refutations additionally need at least one
              validated citation to count). Survivors
              take the median of the original and the upheld revised severities.
              This stage is submitted as one <em>batch</em> of independent
              requests, which the API prices at half. Nothing about the review
              changes; batched requests are not streamed, so the panel board
              reports how many seats have returned instead of showing each
              seat’s activity as it happens.
              Refuted findings are kept and shown in the report rather than
              quietly deleted. The verifiers also judge the proposed fix as
              untrusted input, and must reject one that is partial, ambiguous,
              contradictory, scope-changing, or that would create a new TBD.
              <br />
              <br />
              <b className="text-ink">3 · Fix validation — no model.</b> Each
              surviving fix is dry-run against a throwaway copy of the snapshot.
              A fix that does not actually apply is marked advisory rather than
              offered as a button.
            </>
          }
          sent={
            <>
              Today’s date, the full document rendering, the standards editions
              in effect, the grounded research profile, any{" "}
              <b className="text-ink">reference documents you have attached</b>{" "}
              in full, the discipline, and each lens’s brief. Then, per
              finding, the finding itself plus the document context the
              verifier needs — the verifier seats get the attached documents
              too, so a reviewer asked to refute “this contradicts the owner’s
              standard” can actually read the standard instead of dismissing it
              for want of evidence.
            </>
          }
          model={
            <>
              Claude Opus 5 — five lens calls plus two or three verifier calls{" "}
              <em>per finding</em>. Reasoning depth is set per stage: “high” for
              the lenses, which read the section cold and decide what is wrong
              with it, and “medium” for the verifier seats, which adjudicate one
              already-stated claim with the same document in front of them.
              This is still the most expensive action in the app. Your document
              is sent once per stage rather than once per call: the API caches
              it and every later call in that stage reads the cached copy at a
              tenth of the price.
            </>
          }
          bounds={
            <>
              A failed verifier seat makes its finding{" "}
              <b className="text-ink">inconclusive</b> — never counted as either
              confirmed or refuted — and marks the whole run partial. That holds
              on the batched path too: a seat the batch never returned a result
              for is recorded as failed rather than dropped, because a silently
              shortened panel could reach “upheld” on fewer seats than the
              severity demanded. A missing
              lens or a failed seat{" "}
              <b className="text-ink">blocks “ready to issue”</b> even when the
              surviving votes would pass. Findings are content-addressed: the id
              is a hash of everything a decision depends on, so dismissing one is
              remembered across re-runs, while a finding that has changed in
              substance gets a new id rather than inheriting your dismissal. The
              full record — run identity, document fingerprint, an input manifest
              of every material input, each lens’s exact search queries and
              retrieved sources, every verifier seat and vote, token usage,
              estimated cost, and a limitations list derived from recorded facts —
              downloads as a Word report and as JSON. When the run finishes,
              the chat automatically sends itself{" "}
              <b className="text-ink">one ordinary debrief turn</b> — visible,
              billed like any turn, switchable off — that summarizes the
              findings by severity, separates verified safe fixes from
              advisory and disputed ones, applies nothing, and asks whether
              you want to proceed.
            </>
          }
        />

        <Runtime
          n={5}
          title="Applying a QC fix"
          trigger={
            <>
              Tick findings in the QC drawer and press Apply — or approve them
              in chat after the debrief (“Yes — apply the verified safe
              fixes”), in which case the model passes the finding ids to the
              same local machinery inside an ordinary chat turn.
            </>
          }
          runs={
            <>
              Entirely local arithmetic either way. The accepted fixes are
              re-validated
              against the <b className="text-ink">current</b> document — not the
              snapshot they were written against — on an accumulating working
              copy, so the combined batch is guaranteed to replay. A fix whose
              target has since moved is reported <em>stale</em> and skipped,
              never half-applied. Two fixes claiming the same write location are
              rejected as a conflict <em>before</em> anything is mutated. The
              model never authors the fix content: chat approval applies the
              exact panel-verified operations, or nothing.
            </>
          }
          sent={
            <>
              From the panel: nothing — no network call is made. Through chat
              approval: the ordinary chat turn that carries your “yes” (the
              apply itself is still local).
            </>
          }
          model="None for the apply itself (the chat path spends its ordinary turn)."
          bounds={
            <>
              The whole accepted set commits as{" "}
              <b className="text-ink">one undo step</b> (with the rest of the
              turn, when applied through chat), and the audit report records
              the disposition either way. Chat can only apply after your
              explicit approval in that conversation, and only while the
              review still matches the document. Dismissing a finding
              requires a written reason, which is recorded in the audit
              report, and stays a panel action.
            </>
          }
        />

        <Runtime
          n={6}
          title="Importing an office master (.docx)"
          trigger="Choose a file from the document panel, into an empty document."
          runs={
            <>
              The upload is inspected as a ZIP/OPC package before anything is
              retained; a package that fails inspection is rejected atomically
              and never kept. What survives becomes{" "}
              <b className="text-ink">two separate artifacts</b>: the exact
              uploaded bytes, retained unchanged as a recovery source, and a
              normalized tree of the supported body content, which is what you
              edit. Tracked changes are read as the Accept-All view, with a
              warning. Every extracted block lands stamped{" "}
              <Tag>imported</Tag> until you review it.
              <p className="mt-2">
                The document keeps your formatting and stays fully editable at
                the same time. On export, every part of your package except the
                document body is copied through{" "}
                <b className="text-ink">byte for byte</b> — header, footer,
                styles, fonts, numbering, page setup. In the body, a provision
                you did not touch is copied out of your original character for
                character; one you edited keeps its style, font, size, indent
                and numbering, and only the words change. Character-level
                emphasis inside a sentence you rewrote is the one thing that
                may not survive.
              </p>
              <p className="mt-2">
                Tables, pictures, embedded objects and content controls are{" "}
                <b className="text-ink">preserved blocks</b>: they come through
                your export exactly as they arrived, so their contents cannot
                be retyped here. You can move them, delete them and mark them
                reviewed, and hovering the lock chip says why. Headers and
                footers are never rewritten at all — so if a preserved footer
                still carries the section number of the master you adapted, the
                issues list tells you, and the fix is in Word.
              </p>
            </>
          }
          sent={
            <>
              Nothing at import time. Afterwards, the{" "}
              <b className="text-ink">extracted text</b> is part of your document
              and travels in the context block like anything else you typed. The{" "}
              <b className="text-ink">file bytes never go anywhere</b> — the model
              cannot see them and cannot modify them.
            </>
          }
          model="None. Import, extraction, and permission analysis are deterministic code."
          bounds={
            <>
              Fail-closed throughout: ambiguity can remove a body-editing
              capability but can never create one, and a disabled control shows
              the server’s own reason rather than a guess written by the
              interface. Permission to change a review status never implies
              permission to rewrite Word body content. Exporting is an explicit
              choice among five clearly-labelled contracts — see the list below.
            </>
          }
        />

        <Runtime
          n={7}
          title="Attaching a reference document"
          trigger="Attach a Word file, PDF, text file, XML, or CSV as background."
          runs={
            <>
              The text is extracted (a PDF page by page, with{" "}
              <Mono>[page N]</Mono> markers so the model can cite where something
              came from) and nothing else is retained. It is{" "}
              <b className="text-ink">not</b> part of the specification: not in
              the document tree, not in lint, not in Compare, not in any export.
              It <b className="text-ink">is</b> read by the research agents and
              by every Final QC reviewer, in full — so an owner standard shapes
              what research goes looking for, and the review checks your draft
              against it. A PDF with no text layer is refused with the reason
              rather than attached as an empty document you would then be told it
              holds.
            </>
          }
          sent={
            <>
              In <b className="text-ink">chat</b>, only a one-line stub per
              document in each turn’s context (id, title, size). The body
              travels only when the model deliberately opens one with a tool,
              and is then removed from stored history — so a 40-page owner
              standard is not re-billed on every subsequent turn. Re-reading it
              later is expected and cheap to ask for. Research and Final QC are
              different: those are single bounded runs rather than an
              open-ended conversation, so they receive the text in full (capped
              at about 25,000 tokens across everything attached — each document
              gets an equal share, small ones are included whole and donate
              what they do not use, so no attached document is ever silently
              invisible, and any cut is disclosed in the report), written to the
              prompt cache once and re-read from there by every agent in the run.
            </>
          }
          model="Claude Sonnet 5, only when it chooses to open one during a turn you started."
          bounds={
            <>
              The model is instructed never to paste reference wording into a
              provision, and never to cite a reference as authority for a code
              requirement — a reference is evidence of what the{" "}
              <em>owner</em> wants, not of what a <em>code</em> requires.
            </>
          }
        />

        <Runtime
          n={8}
          title="A figure appears in the chat"
          trigger="The model offers a diagram, schematic, or schedule during a turn."
          runs={
            <>
              The drawing’s source is stored locally and rendered in the browser.
              Because model-authored markup is untrusted by definition, rendering
              passes through{" "}
              <b className="text-ink">three independent safety layers</b>: diagram
              text is treated as data with HTML labels disabled; every resulting
              SVG is sanitized against an SVG-specific allow-list that strips
              scripts, foreign objects, event handlers and{" "}
              <Mono>javascript:</Mono> URLs; and the sanitized result is displayed
              inside a sandboxed frame that can neither execute script, reach the
              application, nor load anything from the internet. Downloads are
              built in your browser from the already-sanitized string; the app
              never serves executable SVG.
            </>
          }
          sent={
            <>
              The figure’s source is <b className="text-ink">not</b> put back into
              later requests — only its id, kind, and title. A drawing therefore
              costs essentially nothing on every subsequent turn.
            </>
          }
          model="Claude Sonnet 5, within the turn you started."
          bounds="A figure is an exhibit. The model is instructed never to place a normative requirement only in a figure — enforceable words live in a provision."
        />

        <Runtime
          n={9}
          title="The suggested reply chips"
          trigger="They appear above the composer after a turn."
          runs={
            <>
              The model stages up to five short messages written in{" "}
              <em>your</em> voice. Clicking one sends that exact text as your
              message — nothing more. They cannot act, cannot start research or
              QC, and cannot edit the document. A turn that stages none clears the
              bar, which is how the row winds down as a section approaches
              issue-ready.
            </>
          }
          sent="A tiny payload of text, replaced wholesale each turn."
          model="Claude Sonnet 5, inside the turn."
          bounds="Panel actions — research, QC, export, undo, save — are deliberately excluded from what a chip may propose."
        />

        <Runtime
          n={10}
          title="Manual editing, the review queue, undo, and Compare"
          trigger="Edit on the page, walk the review queue, press undo/redo, open Compare."
          runs={
            <>
              Local, deterministic operations on your document. The review queue
              is computed directly from the document each time it renders, so it
              cannot drift from what the page shows. Every change is a version;
              undo and redo walk them. Compare runs the same diff engine the
              redline export uses, so what you read on screen and what Word shows
              are produced by the same code.
            </>
          }
          sent="Nothing. No network call is made."
          model="None."
          bounds="Manual edits are refused while a turn is streaming, so a person and the model can never write over each other."
        />

        <Runtime
          n={11}
          title="Exporting"
          trigger="Choose an option from the Export menu."
          runs={
            <>
              Deterministic document generation. Which guarantee you get is your
              explicit choice, and the options are never silently substituted for
              one another — if a source-preserving export cannot be proven safe,
              it is blocked rather than quietly downgraded to a regenerated file.
            </>
          }
          sent="Nothing. No network call is made."
          model="None."
          bounds={
            <>
              <div className="mt-1 space-y-2">
                {SOURCE_OUTPUT_GUIDANCE.map((item) => (
                  <div key={item.id}>
                    <span className="font-medium text-ink">{item.label}</span> —{" "}
                    {item.description}
                  </div>
                ))}
              </div>
            </>
          }
        />

        <Runtime
          n={12}
          title="The interactive tutorial"
          trigger="Start it from Help or the header."
          runs={
            <>
              It runs against the <b className="text-ink">bundled showcase</b> — a
              complete, pre-generated example specification shipped inside the
              application — in a protected practice workspace. Your own project is
              held aside untouched and comes back whenever the tour ends. Every
              tutorial fixture is bundled and deterministic: nothing in the tour
              makes a model call or spends anything on your behalf. The only paid
              actions inside it are the ordinary optional exercises (a live
              research run, Final QC) that you trigger and confirm yourself,
              exactly as outside the tour.
            </>
          }
          sent="Nothing, unless you yourself trigger an optional research or QC exercise."
          model="None — tutorial content is bundled."
          bounds="Ending the tour is always one click away, and it has exactly one outcome: your project returns exactly as it was, with the practice copy discarded. Nothing you do in the tutorial can replace it."
        />

        <Runtime
          n={13}
          title="Pressing Stop"
          trigger="Stop a streaming reply, a research run, or a QC run."
          runs={
            <>
              These behave differently on purpose.{" "}
              <b className="text-ink">Stopping a chat reply keeps what landed</b> —
              the request is closed immediately and the turn still commits, so the
              text and edits you already watched appear are yours (this is how the
              Claude app behaves, and a chat turn loses nothing by stopping).{" "}
              <b className="text-ink">Stopping research or QC discards the run</b>,
              and the confirmation dialog says so before you click.
            </>
          }
          sent="A local stop signal; nothing is sent to the model."
          model="None — it is a cancellation."
          bounds={
            <>
              Work that has not started its next network call bails immediately
              and for free; a call already in flight finishes and its result is
              thrown away. Money already committed to those calls is still
              counted in the meter, because it was really spent.
            </>
          }
        />
      </Section>

      <Section
        id="authority"
        kicker="Blast radius"
        title="What the model may touch — and what it categorically cannot"
      >
        <P>
          A language model here is not an operator with access to your computer.
          It can only do things by asking for one of a fixed, named set of tools,
          which the application executes on its behalf and validates first.
        </P>
        <Matrix
          head={["Tool", "What it can do", "Constraint"]}
          rows={[
            [
              "Edit the document",
              "Add, replace, delete, or reorder provisions; set a review status; record a standards edition or exclude a standard; record project profile and identity.",
              "Every batch is transactional — it applies wholly or not at all — versioned, and undoable. Reordering is limited to siblings: it never reparents content or moves it to another PART. An invalid batch becomes an error the model must correct, never a broken document.",
            ],
            [
              "Create a figure",
              "Produce a diagram, schematic, or table shown in chat.",
              "Sanitized and sandboxed at render time, as described above.",
            ],
            [
              "Suggest replies",
              "Stage up to five one-tap messages for you.",
              "Text only. Clicking one sends it as your message.",
            ],
            [
              "Read a reference document",
              "Open the text of a document you attached.",
              "Read-only; the text is removed from stored history afterwards.",
            ],
            [
              "Web search / web fetch",
              "Look something up mid-interview.",
              "Executed on Anthropic’s servers under a shared domain blocklist, capped per round, and the model is instructed never to paste retrieved content into a provision. The model calls both tools directly — no code-execution sandbox runs on its behalf, which is why each query and source URL is visible to you as it happens.",
            ],
          ]}
        />
        <Contrast
          leftTitle="Within its reach"
          left={[
            "Proposing, drafting, and revising specification text.",
            "Recording an edition — but only with a stated basis it must supply.",
            "Asking you questions and defaulting visibly when you defer.",
            "Looking something up on the public web through the two server-side tools.",
          ]}
          rightTitle="Outside its reach, structurally"
          right={[
            "Reading or writing any file on your computer.",
            "Running any program, script, or shell command.",
            "Reaching any network address of its own choosing.",
            "Touching the retained bytes of your imported DOCX.",
            "Starting research, starting QC, applying a QC fix, dismissing a finding, exporting, saving, or deleting anything.",
            <>
              Creating an <Tag>imported</Tag> status — only the app seeds those,
              so “reviewed” can never be faked by the thing being reviewed.
            </>,
            "Changing an edition silently, or without recording why.",
          ]}
        />
      </Section>

      <Section
        id="grounded"
        kicker="Epistemics"
        title="What “grounded” means — stated precisely"
      >
        <Note title="The exact claim">
          A research item is marked <b className="text-ink">grounded</b> when at
          least one URL it cites matches, after normalization, a URL the search or
          fetch tools <em>actually retrieved</em> inside that same agent’s
          conversation.
        </Note>
        <Bullets
          items={[
            <>
              <b className="text-ink">It proves retrieval, not truth.</b> It means
              the page exists and was read during the run. It does not certify
              that the page says what the item claims, nor that the page is
              correct. That is exactly why the link is shown: so you can open it.
            </>,
            <>
              <b className="text-ink">Ungrounded is not deleted.</b> An item the
              app could not match is kept, marked <Mono>[UNVERIFIED]</Mono>, and
              the drafting model is explicitly told to treat it as a lead rather
              than a fact. Discarding it would hide a lead; promoting it would be
              a lie.
            </>,
            <>
              <b className="text-ink">Adversarial verification kills noise, not
              error.</b> A QC finding surviving three refuters means three
              independent reviewers failed to knock it down. It does not make the
              finding true, and the report shows you the refuted ones too so you
              can disagree with the panel.
            </>,
            <>
              <b className="text-ink">Lint is exactly as smart as its rules.</b>{" "}
              It is deterministic pattern-matching over your own text — stale
              editions against the editions in effect, leftover placeholders,
              empty or duplicated articles, editions cited without a recorded
              basis. It is advisory: it never blocks an edit or a turn.
            </>,
            <>
              <b className="text-ink">Readiness is arithmetic, not judgment.</b>{" "}
              “Ready to issue” is a checklist with fixed rules: no open items, no
              unreviewed imported or assumed blocks, lint clean, research
              complete, a QC result matching the current document, complete QC
              coverage, and no open critical findings. Anything still waiting on
              you is listed too, but as an advisory line that does not gate the
              verdict. It never asks a model whether the section is good.
            </>,
          ]}
        />
      </Section>

      <Section
        id="deterministic"
        kicker="No AI here"
        title="The parts of this application with no model in them at all"
      >
        <P>
          Worth knowing, because it is a larger list than people expect. Each of
          these produces the same answer every time for the same input, and none
          of them makes a network call:
        </P>
        <Bullets
          items={[
            "The lint report, and the standards editions in effect.",
            "Version history, undo, and redo.",
            "The diff engine behind both Compare and the redline export.",
            "Display numbering (1.1 / A. / 1. / a.), which is derived from position rather than stored.",
            "The readiness checklist.",
            "The QC fix dry-run, conflict detection, and the apply batch.",
            "DOCX import, package inspection, source-preservation analysis, and the audit of a patched export.",
            "Every export writer.",
            "Project save and load, and the cost arithmetic.",
          ]}
        />
        <Note tone="ok">
          A practical consequence: with the network disconnected, everything in
          that list still works. You can open a project, edit it, lint it,
          compare versions, and export — you simply cannot ask the model
          anything.
        </Note>
      </Section>

      <Section id="security" kicker="Security" title="Security and privacy, mechanism by mechanism">
        <Matrix
          head={["Concern", "How it is handled"]}
          rows={[
            [
              "Your API key",
              "Stored in the operating system’s credential manager when one is available, otherwise in a permission-restricted file in the application’s configuration folder. An ANTHROPIC_API_KEY environment variable takes precedence and is never written to disk. The interface only ever displays a masked tail, never the key. “Test” uses a throwaway client and the cheapest authenticated call there is. “Remove” clears the credential store and every key file.",
            ],
            [
              "The local server",
              "Bound to 127.0.0.1 only. It is not reachable from your office network, and there is no remote administration surface.",
            ],
            [
              "The interface itself",
              "Loads nothing from the internet — no content-delivery scripts, no web fonts, no images, no analytics, no trackers. Everything is served by the application from your own disk.",
            ],
            [
              "Model-authored markup",
              "Treated as untrusted input at render time: strict-mode diagrams, SVG sanitization against an allow-list, and a sandboxed frame with a content-security policy that blocks every external load. The application never serves executable SVG.",
            ],
            [
              "Uploaded files",
              "Read under a size bound and inspected as a ZIP/OPC package before anything is retained. A package that fails inspection is rejected atomically. A .docx attached as a reference passes the same inspection as a master, because it is the same attack surface.",
            ],
            [
              "Trace files and the activity log",
              "Both are on by default and written only to your machine’s application-state folder, with credential-shaped strings redacted before trace or normal log text is written. Traces can contain draft text and prompts — that is what makes them useful as an audit record — while logs can include document titles, file paths, frontend error text, and exception context. Settings → Developer tools shows capture coverage and writer health and can save a bundle containing the current trace plus bounded prior-run context, logs, a manifest, and an incident index. Treat all of it as sensitive project data. Disable capture with the BUILD_A_SPEC_TRACE and BUILD_A_SPEC_LOG environment variables.",
            ],
            [
              "Project files",
              "Plain, readable JSON on your disk, holding the whole workspace: every version, the transcript, figures, references, research and QC records, and the retained source package.",
            ],
            [
              "Updates",
              "The version check runs automatically at startup, throttled to once a day — the app's only unprompted outbound request — and can be switched off entirely with the BUILD_A_SPEC_DISABLE_UPDATE_CHECK environment variable. It is HTTPS only, with a redirect-downgrade guard, and the installer is SHA-256 verified against the release manifest before it is ever launched. The application ships unsigned, so that hash is the integrity gate that matters.",
            ],
            [
              "Data handling at Anthropic",
              "Your requests are governed by Anthropic’s commercial API terms and privacy policy, linked below. The app is zero-data-retention eligible end to end: every model it uses, including the Final QC reviewer, is available under zero data retention, and the two web tools are configured to be invoked directly by the model rather than through Anthropic’s server-side code-execution sandbox — a mode that is not zero-retention eligible by default.",
            ],
          ]}
        />
      </Section>

      <Section id="money" kicker="Cost" title="Money — what you are actually paying for">
        <Bullets
          items={[
            <>
              <b className="text-ink">There is no subscription and no spend you
              did not start.</b> You are billed by Anthropic for your own API
              usage, under your own key. The one automatic model turn is the
              completion debrief after a research or Final QC run you launched
              (an ordinary chat turn, disclosed above, switchable off); beyond
              it the update check is the app’s only unprompted request, and it
              costs nothing.
            </>,
            <>
              <b className="text-ink">The fixed instruction block is cached.</b>{" "}
              After the first turn of a session it is billed at a fraction of the
              normal input rate; the settings panel shows what caching saved you
              this session.
            </>,
            <>
              <b className="text-ink">Rough relative weight</b>: an ordinary chat
              turn is cheap; a full-section draft is one large turn; a research
              run is four long agents in parallel; and{" "}
              <b className="text-ink">Final QC is by far the most expensive
              action</b> — a stronger model than the one that drafts, five
              lenses, and two to three additional reviewers for every finding.
            </>,
            <>
              <b className="text-ink">The meter is an estimate, computed
              locally</b> from published list prices, and is labelled as an
              estimate everywhere it appears. Your invoice is whatever Anthropic’s
              console says. The estimate deliberately uses standard rather than
              promotional rates so it never under-reports.
            </>,
            <>
              <b className="text-ink">Money spent on work that failed or was
              stopped is still real</b>, and the meter records it rather than
              quietly writing it off.
            </>,
            <>
              <b className="text-ink">One number in the table is estimated
              rather than counted</b>, and it is marked. Stopping a reply
              closes the request before Anthropic sends its final token
              tally, so the output you already received is measured from what
              arrived and shown as a separate <code>+N</code> addition. It is
              never folded into the reported output count — that figure stays
              exactly what the API said, so it can still be reconciled
              against your console.
            </>,
          ]}
        />
      </Section>

      <Section id="limits" kicker="Honesty" title="What this does not do">
        <P>
          Stated plainly, because a trust document that only lists strengths is
          marketing:
        </P>
        <Bullets
          items={[
            <>
              It is <b className="text-ink">not a licensed professional</b>. It
              does not seal or certify anything and it accepts no liability.
              Everything it produces is a draft for a qualified reviewer, and the
              export schedules every assumption precisely so that review is
              possible in one pass.
            </>,
            <>
              A grounded research item is{" "}
              <b className="text-ink">not a legal opinion on code adoption</b>.
              Adoption, amendments, and effective dates must be confirmed with the
              authority having jurisdiction.
            </>,
            <>
              <b className="text-ink">Final QC is a review, not a guarantee.</b>{" "}
              It can miss things, and a clean QC result is not a compliance
              certification. Its value is that it shows its work.
            </>,
            <>
              The model can be{" "}
              <b className="text-ink">confidently wrong</b> — about editions,
              article numbers, product listings, and manufacturers. That is
              precisely why editions must be recorded with a stated basis, why
              lint re-checks the draft against them, and why the compliance lens
              is required to look a standard up rather than recall it.
            </>,
            <>
              It is <b className="text-ink">not a Word editor</b>. An imported
              package is preserved, not made generally editable; headers, footers,
              tables, fields, styles, and layout are outside the boundary.
            </>,
            <>
              The redline of extracted provisions is{" "}
              <b className="text-ink">not a redline of your Word file</b>, and
              Reject All on it does not recreate your original upload. That is
              what the exact-original download is for.
            </>,
            <>
              Cost figures are estimates, and{" "}
              <b className="text-ink">nothing here works offline</b> that needs
              the model.
            </>,
          ]}
        />
      </Section>

      <Section
        id="verify"
        kicker="The point"
        title="Don’t take our word for it — check it yourself"
      >
        <P>
          Every claim above is checkable from inside the application or from your
          own disk. In rough order of effort:
        </P>
        <Bullets
          items={[
            <>
              <b className="text-ink">Read the request record.</b> Tracing is on
              by default and writes to your machine. Every model call, its
              prompts, its tool calls, and its results are there, with
              credential-shaped strings redacted. The application bundles a viewer
              for them.
            </>,
            <>
              <b className="text-ink">Export the Final QC audit as JSON.</b> It
              contains the run identity and document fingerprint, the manifest of
              every material input, each lens’s exact search queries and retrieved
              URLs, every verifier seat and how it voted, the raw proposed
              operations, every disposition you made, token usage, the pricing
              basis used for the estimate, and the derived limitations list.
              Nothing in the on-screen report is invented at display time.
            </>,
            <>
              <b className="text-ink">Open the research report and click the
              sources.</b> Then judge for yourself whether the page says what the
              item claims. If an item shows <Mono>[UNVERIFIED]</Mono>, the app is
              telling you it could not match a retrieved page — treat it
              accordingly.
            </>,
            <>
              <b className="text-ink">Open a project file in a text editor.</b>{" "}
              It is JSON: every version, the whole conversation, and the retained
              source. Nothing about your project is stored anywhere you cannot
              read.
            </>,
            <>
              <b className="text-ink">Hash the exact-original download</b> against
              your office master after an import. They are byte-identical, which
              is the strongest possible statement that the app did not quietly
              rewrite your file.
            </>,
            <>
              <b className="text-ink">Watch the meter</b> during a run, then
              compare it against your Anthropic console for the same period.
            </>,
            <>
              <b className="text-ink">Pull the network cable.</b> Everything in
              the “no AI here” list keeps working. That is the cleanest possible
              demonstration of which parts of this application actually depend on
              a model.
            </>,
            <>
              <b className="text-ink">Watch a firewall or proxy log.</b> The only
              routine destination is Anthropic’s API — including the one
              automatic completion-debrief turn after a research or Final QC
              run you launched. You will also see one
              GitHub request at startup — that is the version check described
              above, and it is the only connection the app makes that no
              action of yours set in motion.
            </>,
          ]}
        />
        <div className="rounded-xl border border-edge bg-raised/40 p-4">
          <h4 className="text-sm font-medium text-ink">Further reading</h4>
          <ul className="mt-2 space-y-1.5 text-sm text-ink-dim">
            <li>
              <Link href="https://www.anthropic.com/legal/privacy">
                Anthropic privacy policy
              </Link>{" "}
              — how the model provider handles API data.
            </li>
            <li>
              <Link href="https://trust.anthropic.com">
                Anthropic Trust Center
              </Link>{" "}
              — security posture, compliance, and subprocessors.
            </li>
            <li>
              <Link href="https://docs.claude.com">Claude API documentation</Link>{" "}
              — the models, the server-side web tools, and prompt caching
              described above.
            </li>
            <li>
              <Link href="https://github.com/Abe-Borg/build-a-spec/releases">
                Build-a-Spec releases
              </Link>{" "}
              — versions, release notes, and the manifest the updater verifies
              against.
            </li>
          </ul>
        </div>
        <Note title="Still not convinced?" tone="ok">
          Good. Use it the way it is designed to be used: draft with it, then walk
          the review queue and read every block it stamped{" "}
          <Tag>assumed</Tag>. The app’s entire argument is that it makes its own
          uncertainty cheap to find — not that you should stop looking.
        </Note>
      </Section>
    </div>
  );
}

/* --------------------------------------------------------------------- */
/* Modal shell                                                            */
/* --------------------------------------------------------------------- */

export default function TrustDeepDiveModal({ open, onClose }: Props) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const [active, setActive] = useState<string>(SECTIONS[0].id);

  // Initial focus, Escape, Tab containment, and focus restoration — the same
  // helper the QC dialogs use. Containment matters more here than usual: this
  // dialog stacks on the help dialog, and without it Shift+Tab reaches the
  // topic strip underneath, whose buttons change the topic and so close this
  // dialog out from under a keyboard user. HelpModal's own Escape handler is
  // disabled while `deepDive` is set, so exactly one dialog closes per press.
  useDialogFocus(open, dialogRef, closeButtonRef, onClose);

  // Scroll-spy for the contents rail. Rebuilt whenever the modal opens
  // because the sections only exist in the DOM while it is mounted.
  useEffect(() => {
    if (!open) return;
    const root = scrollRef.current;
    if (!root) return;
    setActive(SECTIONS[0].id);
    const seen = new Map<string, number>();
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          seen.set(entry.target.id, entry.intersectionRatio);
        }
        let best = "";
        let bestRatio = 0;
        for (const section of SECTIONS) {
          const ratio = seen.get(section.id) ?? 0;
          if (ratio > bestRatio) {
            best = section.id;
            bestRatio = ratio;
          }
        }
        if (best) setActive(best);
      },
      { root, threshold: [0, 0.25, 0.5, 1] }
    );
    for (const section of SECTIONS) {
      const el = root.querySelector(`#${section.id}`);
      if (el) observer.observe(el);
    }
    return () => observer.disconnect();
  }, [open]);

  if (!open) return null;

  const jump = (id: string) => {
    const el = scrollRef.current?.querySelector(`#${id}`);
    el?.scrollIntoView({ behavior: "smooth", block: "start" });
    setActive(id);
  };

  return (
    <div
      className="fixed inset-0 z-[60] flex items-start justify-center bg-black/60 p-4 pt-10"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="How Build-a-Spec actually works"
    >
      <div
        ref={dialogRef}
        tabIndex={-1}
        className="flex max-h-[88vh] w-full max-w-5xl flex-col overflow-hidden rounded-2xl border border-edge bg-surface shadow-2xl"
        onClick={(e) => e.stopPropagation()}
        data-capability="help.trust"
      >
        <div className="flex items-start justify-between gap-4 border-b border-edge px-6 py-3.5">
          <div>
            <h2 className="font-[family-name:var(--font-display)] text-lg font-semibold">
              How it actually works
            </h2>
            <p className="mt-0.5 text-xs text-ink-faint">
              The long answer: where every word comes from, what runs when you
              press each button, and how to verify all of it yourself.
            </p>
          </div>
          <button
            ref={closeButtonRef}
            onClick={onClose}
            className="rounded-lg px-2 py-1 text-ink-dim transition-colors hover:text-ink"
            title="Close"
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        <div className="flex min-h-0 flex-1">
          <nav
            aria-label="Contents"
            className="hidden w-60 flex-none overflow-y-auto border-r border-edge px-3 py-4 lg:block"
          >
            <p className="px-2 pb-2 text-[11px] font-medium uppercase tracking-wider text-ink-faint">
              Contents
            </p>
            <ul className="space-y-0.5">
              {SECTIONS.map((s, i) => (
                <li key={s.id}>
                  <button
                    onClick={() => jump(s.id)}
                    aria-current={active === s.id ? "true" : undefined}
                    className={`flex w-full gap-2 rounded-md px-2 py-1.5 text-left text-xs leading-snug transition-colors ${
                      active === s.id
                        ? "bg-accent/15 text-accent"
                        : "text-ink-dim hover:bg-raised hover:text-ink"
                    }`}
                  >
                    <span className="tabular-nums opacity-60">{i + 1}</span>
                    <span>{s.label}</span>
                  </button>
                </li>
              ))}
            </ul>
          </nav>

          <div ref={scrollRef} className="min-w-0 flex-1 overflow-y-auto px-6 py-5">
            <Dossier />
          </div>
        </div>
      </div>
    </div>
  );
}
