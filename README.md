# Build-a-Spec

**v1.5.0** — Conversational authoring of construction specification sections. You talk through the project with Claude; it interviews you, drafts CSI SectionFormat language incrementally, and builds the section live in a document panel beside the chat — the way artifacts work in the Claude app.

First curated domain: **Division 21 fire suppression for hyperscale data centers (USA)**, starting with wet-pipe sprinkler systems (21 13 13) and siblings. Since v1.5.0 a second, **generic module** drafts **any discipline, for projects anywhere in the USA or Canada** (no pinned editions — every standard edition is recorded per-project with its stated basis). The engine is domain-neutral; discipline knowledge lives in registry-validated **spec modules**, the same architecture as [Spec Critic](https://github.com/Abe-Borg/Claude-Spec-Critic)'s review modules.

Build-a-Spec is the drafting-side complement to Spec Critic: **Build-a-Spec writes specs through dialogue; Spec Critic reviews finished specs.** Large parts of this codebase are ports of Spec Critic's domain-neutral machinery (see "Relationship to Spec Critic" below).

## DOCX fidelity boundary (P1 source-preserving foundation)

> **Important:** Build-a-Spec edits a deliberately narrow semantic view of an
> imported `.docx`; it is not a general-purpose Word formatting editor.

- Import retains the exact validated DOCX as an immutable source artifact and
  separately extracts supported main-body content into Build-a-Spec's
  SectionFormat tree. Headers, footers, section/page layout, styles, fields,
  drawings, and other unsupported OOXML never become editable claims.
- **Export preserved DOCX** clones that source package and applies only edits
  the preservation gate can prove safe. P1 accepts unambiguous text replacement
  in a simple, directly mapped body paragraph. It preserves that paragraph's
  existing paragraph/run properties—including real `w:numPr` numbering—and
  leaves every other package member payload untouched.
- Unsafe mutations fail closed. Structural changes (add/delete/reorder),
  table-derived blocks, fields, hyperlinks, drawings, content controls,
  tracked-revision targets, complex multi-run formatting, and ambiguous
  mappings are refused in source-preserving mode. Signed, protected,
  revision-bearing, and active-content packages are pass-through-only: exact
  no-op export is allowed, but mutation is not. Build-a-Spec does not flatten
  the source as a fallback.
- A no-op source export—including status/profile/standards-only changes or an
  undo back to the imported baseline—returns the exact original DOCX bytes.
- Native `.baspec` project files carry the semantic project and the exact source
  DOCX together, with manifest hashes and bounded package validation. Legacy
  JSON projects still load, but are source-less and can only use normalized
  export.
- **Export normalized DOCX** remains an explicit separate choice. It generates a
  new document from the semantic tree and still uses positional display labels,
  not real Word automatic-numbering bindings. “Redline of extracted provisions”
  likewise remains a semantic redline, not a source-package redline.

This is the P1 foundation. Later preservation phases can expand the safe edit
islands (for example bounded add/delete/reorder) without expanding the product
into headers, footers, or general Word formatting.

## Current Status — v1.5.0 (Batch 10: Generic any-discipline module)

**Any discipline, any project type, anywhere in the USA and Canada.** The
"New session" button now opens a module picker: the curated hyperscale-fire
module, or **Generic — Any Discipline**, where you type your discipline
(chips suggest Fire Protection / Mechanical / Plumbing / Electrical; free
text welcome) and the app drafts to that discipline's conventions.

- **No pinned editions in generic mode — deliberately.** The generic module
  ships zero standards pins. Every edition enters through the existing
  `set_standard_edition` override with a **stated basis**: a grounded
  research item, your statement, or an honestly-labeled model proposal
  ("model-proposed, unverified"). Until an edition is recorded, the draft
  cites designations year-free, and a new lint rule (`unrecorded_edition`,
  active only in generic mode) flags any year citation with no recorded
  basis — which flows into the readiness checklist, so an issue-ready draft
  has a recorded basis for every cited edition.
- **Discipline is captured at session start** and threads everywhere: the
  drafting context, all four research dimensions (now
  discipline-parameterized and US/Canada-aware — provincial NBC/NFC
  adoption, CSA/ULC listings, metric units), and the Final-QC lens briefs.
  It persists in project files and shows in the header
  ("Generic — Electrical").
- **The curated module is untouched**: hyperscale fire suppression keeps
  its NFPA pins, playbook, and byte-identical behavior; it stays the
  default.

## Shipped in v1.4.0 (Batch 9: Dynamic suggested-prompts bar) and still current

**A row of one-tap reply chips sits just above the chat box, and the model
fills it fresh every turn.** After each reply, Claude may stage up to five
short prompts — direct answers to the question it just asked (its recommended
default, a plausible alternative, an "I don't know — use your default"), or
momentum moves like *"Draft PART 2 now."* Clicking a chip sends it as your
next message, so an interview is mostly tapping, not typing.

- **The model decides the set, every turn, via a new `suggest_prompts` chat
  tool.** It rides the one chat/tool loop and streams a live `suggested_prompts`
  event the instant it's called — the same thin-tool pattern as Batch 8's
  figures. Chips are always complete, sendable replies in your voice (never
  fill-in-the-blank templates, never panel-button actions like "Run research").
- **It winds down as the section finishes.** Not calling the tool clears the
  bar, so as open items resolve and the draft nears issue-ready the model
  naturally offers fewer chips — one or two, then none. An empty section of
  chips is a real signal, not a bug.
- **Turn-atomic and honest.** A committed turn replaces the set (a stopped
  turn keeps whatever it staged); a failed turn leaves the previous chips
  untouched and the bar restores itself on the next refresh. The current set
  rides the project file, so a saved-and-resumed session comes back with its
  chips. The guided-tour demo pass deliberately offers none — the tour drives
  what happens next.

> Note: the v1.2.0 (Batch 7: stop generation / research / QC) and v1.3.0
> (Batch 8: chat figures) status sections were never written into this README;
> both shipped and are described in `CLAUDE.md`. This is a pre-existing
> documentation gap, called out here rather than silently left implied.

## Shipped in v1.1.0 (Batch 6: Guided onboarding + starter prompts) and still current

**A first-time user goes from an empty chat to "I understand this whole app"
in about five minutes, on a live demo.** The empty chat now opens with five
starter-prompt chips; the first — *"New to this software, show me how to use
this"* — runs a guided tour of the entire workflow on a demo spec the model
drafts in front of you.

- **Discipline first, then a live demo.** The tour asks your discipline
  (Fire Protection & Suppression / Mechanical (HVAC) / Plumbing / Electrical /
  free-text other), fetches a server-owned demo directive
  (`POST /api/onboarding/demo` — the Batch 3 thin-directive pattern, 409
  unless the document is blank), and sends it through the ordinary chat
  path: the demo streams onto the paper like any real turn, deliberately
  small (one brief article per PART, plus one planted `[TBD: …]` and one
  needs-input block so the training has live open items to point at). A new
  stable-prompt policy ("Guided-tour demo pass") keeps the fire-module
  persona from steering a Plumbing demo back to sprinklers.
- **A scripted spotlight tour, in chunks.** 22 steps in 4 chunks — *Reading
  the page / Tell it about the project / Make it yours / Out the door* —
  cover every station of the workflow: statuses & provenance, open items,
  lint, standards, the spend meter, the defaults-first interview, profile,
  research, draft-full, inline edits, versions, the review queue, compare,
  master import, Final QC, readiness, export, save/open, settings. Each step
  dims the screen around the real control (a pointer-events-none box-shadow
  cutout — nothing is ever click-jailed) with a dismissible bubble beside
  it. Chunk breaks pause for *Continue / Ask a question / Start real work*.
- **"Do this for me" where input is needed.** The profile step records a
  demo profile deterministically through `POST /api/doc/edit`
  (`set_project_profile` — no tokens, one undo step) or prefills the
  composer so you type your own; the review step can confirm the first
  outstanding block for you. The research and Final QC steps offer real
  **"Run it now"** buttons with honest cost/time notes and a prominent skip
  (decided with Abraham 2026-07-22) — runs stream in their drawers while the
  tour continues.
- **Start fresh or keep it.** Leaving the tour for real work (mid-tour or at
  the end) asks: start fresh (session reset) or keep the demo as a scratch
  starting point. Starting the tour over a non-empty session hits an entry
  guard (save-first hint + fresh-start), and the endpoint's blank-document
  409 backstops it server-side.
- **Re-entry + polish.** A "Tour" button in the header restarts it any time.
  ✕ / Escape collapse the tour to a floating "Resume tour" pill — nothing is
  lost. The onboarding chip pulses until the first completion (the
  codebase's first, cosmetic-only, localStorage use). Reduced motion is
  honored throughout, and a missing anchor degrades to a centered bubble,
  never a hang.

## Shipped in v1.0.0 (Batch 5: Redline export + version diff) and still current

**A `.docx` with genuine Word tracked-change markup over Build-a-Spec's
normalized provision model.** One deterministic semantic diff engine powers
both the export and an in-app version-compare view. For imported files, its
baseline is the extracted SectionFormat tree — not the uploaded Word package.

- **Real tracked-change markup, explicitly scoped.** Export a redline of the
  imported extraction (or any prior version) and open it in Word: insertions,
  deletions, and word-level edits render as native `w:ins`/`w:del` revisions
  authored by "Build-a-Spec". Accept/Reject round-trip tests cover the
  normalized provision text, not source-package fidelity. Display labels such
  as `A.` and `1.1` are positional literal text rather than tracked Word list
  numbering, so they recompute to the generated view and do not represent the
  uploaded file's original numbering definitions.
- **Word-level, reviewer-grade diffs.** Text edits diff at the word (not
  character) grain — no unreadable confetti in legal-style review. Whole-block
  insertions and deletions flag the paragraph *mark* too, so Word collapses a
  deleted provision cleanly on accept. Pure moves are deliberately *not*
  marked (display numbering is positional and recomputes) — a reviewer sees
  real changes, not renumbering noise.
- **Compare mode, in the panel.** The version stepper gains a **Compare**
  toggle: pick the imported extraction baseline (pinned first when present),
  the blank start, or any prior version, and the paper surface renders the diff inline —
  green/underline insertions, red/strikethrough deletions, whole-block
  insert/delete badges, a provenance **status-changes** strip, and a
  `+N added / −M removed / K edited` stat line. It matches the exported
  redline run-for-run because both read the same diff.
- **Stable ids make semantic alignment deterministic.** Baseline↔current
  alignment is an id join on the never-reused element uids, not a text match — so a provision
  that only had its status confirmed shows as a status change, never a
  spurious edit. The imported-extraction version is remembered as the redline
  baseline and survives save/resume; this baseline contains normalized
  provision data, not the original DOCX package.
- **Export menu.** The single Export button becomes a small menu. P1 adds
  *Export preserved DOCX* as the primary imported-document path when the
  preservation gate is satisfied; *Export normalized DOCX*, *Redline of
  extracted provisions*, and *Redline vs version…* remain explicit semantic
  outputs. Fresh drafts use *Export clean*.

This is the **1.0 release milestone**. Cut the first Windows build per
`docs/RELEASE_WINDOWS.md` after this lands.

## Shipped in v0.9.0 (Batch 4: Final QC on Fable 5) and still current

**One button, a fleet of Fable 5 reviewers, an accept/dismiss fix queue, and
a signed-off QC memo.** The one place a model other than Sonnet 5 appears:
a user-triggered, spare-no-expense last quality-control pass before a section
goes out the door. The output isn't a report to read — it's a set of
*verified* findings, each with a ready-to-apply fix, in an accept/dismiss
queue.

- **Five lenses, in parallel, on the strongest model.** "Send to Final QC"
  fans out five independent Fable 5 reviews of the whole section: **code
  compliance** (verifies every citation/edition against the standards'
  *actual current content* via web search — the big search allowance),
  **coordination & consistency** (PART 1/2/3 alignment, dangling
  cross-references, terminology drift), **completeness** (versus the grounded
  research profile and conventional section scope), **enforceability &
  language** (imperative mood, measurable criteria, no "as required"), and
  **provenance hygiene** (risky `assumed` blocks, surviving TBD/imported,
  provisions citing `[UNVERIFIED]` items). One lens failing never cancels the
  others; all five failing fails clean.
- **Adversarial verification — no plausible-but-wrong noise reaches you.**
  Every candidate finding faces a panel of independent Fable 5 refuters
  prompted to *refute* it (2 for medium/low, 3 for critical/high). A tie goes
  to the refuters — only real, actionable defects survive; refuted findings
  are kept in a collapsed appendix for transparency, never shown as issues.
  This is the "as many agents as necessary" clause: total calls = 5 lenses +
  Σ panel sizes, with no cap on findings count (the runaway guards are
  per-call).
- **Accept the fix, or dismiss it — and dismiss decisions survive re-runs.**
  Each surviving finding whose fix is a clean mechanical edit carries
  `apply_spec_edits` ops, dry-run-validated against a document snapshot.
  **Apply fix** edits the document exactly as previewed, in **one undo step**
  (re-validated against the *current* doc first — a fix whose target moved is
  reported `stale` and skipped, never partially applied). **Dismiss** (with an
  optional reason) is remembered by content-addressed id, so a re-run that
  regenerates the same finding auto-marks it dismissed. An "Apply all
  criticals" press-and-hold handles the urgent set at once.
- **Issue readiness — the "can it go out the door" screenshot moment.** A
  deterministic checklist (no model call) at the top of the QC drawer goes
  green exactly when: no open items, no unreviewed imported/assumed blocks,
  lint clean, research complete, and QC current with no open criticals.
- **The QC memo a reviewer signs off on.** A standalone `.docx` export:
  project/section header, model + date + document version (with a staleness
  note when the draft has moved on), findings by severity with element refs /
  rationale / sources / disposition, and the refuted appendix. The main spec
  export's closing section now shows the QC summary when one exists (falling
  back to the compliance audit otherwise).
- **No dead air — a QC run takes minutes and shows it.** The drawer streams
  lens-by-lens status and a live "Verifying findings… (7/12)" counter over the
  same SSE machinery the research phase uses; the header spend ticker moves as
  the run streams.
- **Migration note:** the QC `code_compliance` + `completeness` lenses
  supersede the Phase 5 compliance audit. The audit button is retired from the
  UI (the Research drawer keeps research only); the `/api/audit/*` endpoints
  and runner remain (deprecated) so nothing breaks.

Shipped in v0.8.0 (Batch 3: full-section draft + the review queue) and still
current — **two on-ramps, one review surface.** Whether a section starts from a blank
page or from an imported office master, you now converge on the same place: a
complete draft, then a guided block-by-block walk to reviewed status.
From-scratch drafting is a first-class path, not the fallback.

- **"Draft full section" — the payoff of the no-limits work.** One accent
  button (in the panel header, offered while the page is empty or sparse) has
  Sonnet lay down the *entire* section in a single turn: every PART, every
  article the module conventionally carries, drafted from your interview
  answers, the project profile, the grounded research, and the standards
  editions in effect — provisions tagged with their research provenance,
  statuses stamped honestly (user-stated `confirmed`, defaults `assumed`,
  unknowns `[TBD]`/needs-input). It streams into the panel article by article
  (no dead air, no silent mega-batch), it's **one undo step**, and after it
  runs the interview pivots to refining what's on the page — exactly like
  gap-and-adapt does after an import. It rides the ordinary chat path (the
  directive appears as a visible user turn), so there's no second pipeline to
  trust. Once research completes and the page is still sparse, the button
  gives a one-time attention pulse.
- **The review queue — turn the assumptions schedule into a workflow.** A
  Review drawer under the panel shows the outstanding count ("Review 87") and
  walks every block that needs a human decision — `imported` blocks after a
  master import, `assumed` blocks after drafting — one at a time, in document
  order, at keyboard speed: **K**eep (confirm), **E**dit (rewrite → confirmed,
  research provenance preserved), **D**elete, **A**sk the model (prefills the
  chat with a targeted "Regarding 1.2.A …" so you just say what to change),
  **S**/→ skip, ← back. Each decision advances to the next block; the queue
  recomputes straight from the live document, so it survives undo, model
  edits, and resets with nothing to drift out of sync. A per-article
  press-and-hold confirms the rest of an article you've read in one undo
  step — the *only* bulk affordance, deliberately guarded; there is no
  document-wide "confirm everything". The outstanding count always matches the
  export's assumptions + imported schedules, so the queue empties exactly as
  the paper trail does.

Shipped in v0.7.0 (Batch 2: streaming UX, direct editing, settings, cost
meter) and still current:

- **Buttery-smooth streaming — no dead air, ever.** The chat loop now
  iterates the model's raw stream events and narrates all of them live: a
  shimmering status strip ("Thinking…", "Searching the web…", "Writing to
  the document…"), streamed adaptive-thinking summaries in a collapsible
  block, drafting progress on a long edit batch, and web-search chips that
  fire the instant the search runs — not a post-hoc chip after it's over.
  Text flows through a `requestAnimationFrame` typewriter with cheap
  markdown (settled prefix memoized, live tail plain), so a 2,000-word
  answer never re-parses itself dozens of times a second; scroll follows
  the bottom while you're pinned and stays put while you read history.
  Thinking summaries use `display: summarized` with a runtime degrade to
  `omitted` if a model rejects it.
- **Edit the document yourself.** Hover any provision for inline edit
  (✏️), one-click confirm of an assumed block (✓), or delete (🗑), plus
  editable article titles — a new `set_status` op and a transactional,
  undoable `POST /api/doc/edit` endpoint behind them, slammed shut (409)
  while a model turn owns the tree. Thanks to the full-document context,
  the model sees your edits on its next turn with no special plumbing.
- **A real settings panel.** A gear in the header opens key management that
  actually manages: it shows where your key resolves from (credential
  manager / key file / read-only env var) and a masked tail — never the
  key — and lets you replace it (test-then-save: authenticates before it
  stores, shows the API's rejection verbatim), remove it, or test it. Plus
  an About section with version, model, and a check-for-updates link.
- **Cost & usage meter.** A live `≈ $0.42 this session` ticker in the
  header opens a by-category usage table (tokens in/out, cache read/write,
  web searches, estimated dollars) with a "prompt caching saved ≈ $X"
  line. Estimates come from a verified list-pricing table (Sonnet 5 at the
  post-intro rate so the meter never under-reports); the trace files stay
  the exact record. Per-session — reset and project load zero it out.

Shipped in v0.6.0 ("Sonnet unleashed") and still current (project decision:
the app imposes **no quality limits on the model** — the only caps left are
runaway circuit breakers sized so no legitimate turn ever meets one):

- **The model sees the whole document, every turn.** The truncated outline
  is gone from the drafting context: a PROJECT CONTEXT block in each
  turn's user message carries the full text of every provision (ids,
  statuses, ◆research-provenance chips), the standards editions in
  effect, the research profile, the live lint report, and the open-item
  list. The model can no longer edit a paragraph it can't see — and it
  fixes its own stale citations and placeholders because the lint now
  talks to it, not just to you.
- **Prompt-cache restructure that pays for all of it.** The dynamic
  context used to sit between the cached system prompt and the message
  history, busting the cache for the whole history every doc-changing
  turn; now the system prompt is stable-only, live state rides the newest
  user message, and a second cache breakpoint on the message tail caches
  the growing interview incrementally. Strictly more context per turn,
  cheaper per turn.
- **Adaptive thinking, wired properly.** Requests state
  `thinking: adaptive` explicitly with effort knobs (interview `high`,
  research `xhigh`), and thinking blocks are preserved verbatim across
  tool-use continuation rounds as the API requires — the previous code
  dropped them, a latent 400 on real drafting turns. Output ceilings sit
  at the model max (128k tokens), so nothing the app controls truncates
  a draft.
- **Live web lookups in the interview.** The drafting model carries
  `web_search`/`web_fetch` (same authoritative-domains blocklist as the
  research phase) for mid-interview verification — a UL category, a
  manufacturer datasheet, a standard designation — with `pause_turn`
  continuation handling and inline 🔍 activity chips in the chat. The
  systematic research fan-out stays button-triggered.
- **Research budgets doubled** (per-dimension searches now 16–40, fetches
  8–12, continuation ceiling 16) and research runs at `xhigh` effort —
  background work where latency is free and quality is the point.
- **Usage telemetry groundwork.** Every turn aggregates its billed usage
  (input/output/cache/thinking tokens, web-tool requests) across all
  rounds into `turn_complete.usage` and the session trace — the raw
  material for the upcoming cost meter.
- Committed history stays lean: the per-turn context block, thinking
  blocks, and fetched-PDF payloads are stripped/elided at commit, so
  project files don't balloon and stale document snapshots never
  fossilize into the conversation.

Shipped in v0.5.0 (Phase 5) and still current:

- **Master-spec import (gap-and-adapt).** "Import master" extracts supported
  main-body content from an office master or previous-project `.docx` into the
  live SectionFormat tree. Structure is inferred from explicit labels and
  limited direct-numbering metadata; tables are flattened, whitespace is
  normalized, unsupported Word structures are omitted, and pending tracked
  changes are resolved to an Accept-All text view. Every extracted block enters
  with the fourth provenance status **`imported`** (badged blue and scheduled
  in the export until reviewed), and the interview pivots to adapting that
  normalized content. The import report records known warnings and skipped
  empty blocks, but it is not a proof that the full Word package was preserved.
- **Compliance audit.** One click audits the draft against the Phase 4 requirements profile, with Spec Critic's trust model intact: only **grounded** requirements control; `[UNVERIFIED]` items can at most earn a confirm-with-authority advisory; `[PROCESS]` items are excluded. Output: a coverage matrix (`represented / missing / contradicted / unclear`, every controlling requirement always classified — a skipped one reports `unclear`, never invisible) with evidence quotes + click-to-jump element ids, advisory findings, a staleness marker when the draft moves past the audited version, and a **compliance closing section in the `.docx` export**. Full multi-spec reviews still belong to Spec Critic.
- **Windows packaging + auto-update.** Spec Critic's release pipeline, cloned: PyInstaller one-folder build (`packaging/windows/build-a-spec.spec`, bundling the built frontend + pywebview/WebView2), Inno Setup installer with its own stable AppId, and the serverless GitHub-Releases updater — `latest.json` manifest fetched https-only (redirect-downgrade guarded), installer **SHA-256-verified before it ever runs**, once-a-day throttle, skip-this-version, and an update pill in the header. `docs/RELEASE_WINDOWS.md` is the runbook; `--version`/`--selfcheck` smoke-test the frozen exe; a version-consistency gate keeps settings/package.json/tag aligned (and runs in pytest).
- **Session tracing.** The ported Spec Critic tracing core (JSONL spans + events, background writer, credential redaction, prompt-hash dedup, deep mode) records turns, tool dispatches, research runs, audits, and imports — local-only, env-gated (`BUILD_A_SPEC_TRACE`, default on), with the bundled HTML viewer at `GET /api/trace/viewer`.

Shipped in v0.4.0 (Phase 4) and still current (the near-verbatim port of Spec Critic's requirements-research fan-out, pointed at drafting):

- **Project profile, conversationally.** As you state the project's city/state/country/client in the interview, the model records them with a `set_project_profile` operation (normalized against the ported US-state/CA-province tables, riding the same undo/save machinery as document text). A complete profile arms the research phase.
- **Grounded requirements research, on demand.** A "Research requirements" button in the panel launches four parallel streaming web-search agents — governing codes & amendments, AHJ requirements (including the water purveyor), client/insurer standards, site environment — each searching as the project's own locale, with pause-turn continuation, per-dimension search budgets, a 2× runaway ceiling, and a fetched-PDF elision guard so a 600-page code PDF can't 400 its own continuation. Research never auto-triggers: dozens of web searches are real spend, so you pull the trigger.
- **Citations or it didn't happen.** Every reported item is validated accepted-vs-cited: a URL the model cites must match one the server tools actually retrieved, or the item renders **[UNVERIFIED]** (kept as a lead, never a fact). Process/schedule facts render **[PROCESS]** and never become spec text. One dimension failing never cancels the others; partial profiles are flagged; total failure aborts clean. A **View report** button opens the full findings report in a modal — every agent's items grouped by dimension, with each dimension's completion status and search/fetch telemetry, requirement, authority, code reference, confidence, and grounded sources.
- **Research → drafting, closed loop.** The profile block joins the drafting context every turn (token-capped, trimmed lowest-confidence-first; the structured profile keeps everything). Provisions drafted from a research item carry its `source_item_id` — a ◆ chip in the panel answers "why is this paragraph here?" with the requirement and its accepted sources. When a grounded item establishes the jurisdiction's adopted edition, the model records a Phase 3 `set_standard_edition` override citing the item — and the lint immediately checks the draft against it.
- **Research results persist**: the profile rides the project file; a resumed project restores it into the panel drawer and the drafting context.

Shipped in v0.3.0 (Phase 3) and still current:

- **Spec modules.** Discipline knowledge moved out of the hardcoded system prompt into frozen, registry-validated `SpecModule` objects (`backend/spec_modules/`) — section catalog, defaults-first interview playbook (every defaultable topic ships its recommended default; the non-defaultable minimum is marked *must ask*), drafting prompt slots, lint vocabulary, and dormant Phase 4 research dimensions. A bad module definition fails at startup, never mid-session. First module: `hyperscale_fire` (Div 21, USA — 21 13 13 wet-pipe lead section with the full playbook; dry-pipe, preaction, fire pumps, water service, standpipes, common-work, and clean-agent sections in the catalog).
- **Pinned standards editions.** The module pins the current published editions as drafting defaults — **NFPA 13-2025** first among them, plus NFPA 14-2024, 20-2025, 22-2023, 24-2025, 25-2026, 72-2025, 75-2024, 76-2024, 291-2025, 2001-2025, 855-2026 over IBC/IFC 2024 model-code context. Every pin carries maintainer provenance (receipts in `docs/standards_provenance.md`). When you state the jurisdiction's adopted edition ("Loudoun County is on the 2021 VCC → NFPA 13-2019"), the model records it with a `set_standard_edition` operation — adoption basis required, never silent — and the override drives the REFERENCES article, the lint, and the export from then on. Overrides ride the same transactional/undo/save machinery as document text.
- **Live linting.** Deterministic, no-API checks run on every document mutation and render in an advisory issues drawer (click to jump): standard citations that contradict the editions in effect (with a negation-suppression window so "superseded by…" prose doesn't false-flag), unresolved placeholders (`[INSERT …]`, `___`) and template markers (`TODO:`, `FIXME`, lorem ipsum), empty articles, duplicate article titles, and a heads-up when drafting proceeds with the section header unset. A standards strip under the panel shows every edition in effect, overrides highlighted with their basis.

What worked before (Phase 2) and still does:

- Claude-desktop-style UI: streaming chat pane on the left, the **live specification document** on the right, warm dark theme.
- The model drafts exclusively through the `apply_spec_edits` tool into a server-owned SectionFormat tree (Section → PART 1/2/3 → articles → nested paragraphs, positional display labels `1.1` / `A.` / `1.` / `a.` / `1)`, stable element ids). Those labels are not Word automatic-numbering definitions. Edits are validated server-side and applied transactionally; each turn's changes stream into the panel as they happen, with changed blocks highlighted.
- Per-block provenance: `confirmed` / `assumed` / `needs_input`, badged in the panel. `[TBD: …]` markers and needs-input blocks are tracked as open items — listed under the panel (click to jump) and scheduled in the export.
- Defaults-first interview: every question carries a recommended answer; "I don't know" applies a defensible NFPA 13-2025 / hyperscale-norm default stamped `assumed`; guide-me mode turns open questions into concrete options with tradeoffs.
- Version stepper: one snapshot per turn that changed the document; undo/redo from the panel header.
- `.docx` export via python-docx — SectionFormat styling plus an **assumptions schedule** (every `assumed` block with its numbering, for one-pass senior review) and an open-items schedule.
- Project save/resume: a native `.baspec` package bundling the conversation,
  full document version history, import report, and exact source DOCX when one
  exists—undo and source-preserving export still work after resume. Legacy JSON
  projects remain load-compatible but do not contain source bytes.
- API key management: `ANTHROPIC_API_KEY` env var → OS credential manager (via `keyring`) → key file fallback, same posture as Spec Critic. A banner in the UI stores your key if none is found.
- Session reset, prompt-cached system prompt, hermetic test suite (no network, no key).

All five roadmap phases are shipped. What remains is real-world hardening: cutting the first Windows release from the runbook, growing sibling-section playbooks and modules, and tuning the import heuristics against your actual office masters.

## Architecture

```
main.py                  pywebview shell: starts the backend, opens the native window
backend/                 FastAPI + the conversation engine (Python 3.11+)
  app.py                 /api/health, /api/key, /api/session/reset, /api/chat (SSE),
                         /api/draft/full, /api/onboarding/demo,
                         /api/doc (+ undo/redo/edit/diff),
                         /api/export/docx (+ ?redline=master|version),
                         /api/import/master + /api/import/original,
                         /api/research/start|status|stream,
                         /api/qc/start|status|stream|apply|dismiss|export,
                         /api/readiness, /api/audit/* (deprecated),
                         /api/update/check|install,
                         /api/trace/viewer, /api/project/save + load/load-file
  qc/
    schema.py            QC lens definitions + submit_qc_findings/verdict strict
                         tools + finding/verdict normalization
    engine.py            run_final_qc: lens fan-out -> adversarial verification
                         -> ops validation -> QCResult  [pattern: research/engine.py]
    runner.py            session-bound QC lifecycle: daemon thread, event log,
                         SSE follow                       [pattern: research/runner.py]
  settings.py            models (interview + research), ports, env overrides,
                         frozen-app path resolution
  updates.py             GitHub-Releases manifest updater: https-only fetch,
                         SHA-256 verify, throttle/skip state [ported from Spec Critic]
  standards.py           StandardEdition/BaseCode/StandardsBasis pins + jurisdiction
                         edition overrides                       [ported from Spec Critic]
  project_profile.py     ProjectProfile: US/CA tables, normalization, search
                         locale, fingerprint                     [ported from Spec Critic]
  api_key_store.py       key resolution: env -> keyring -> file   [ported from Spec Critic]
  app_paths.py           platformdirs config locations            [ported from Spec Critic]
  sessions.py            active-session store (single session)
  spec_modules/
    base.py              frozen SpecModule + import-time registry validation
                                                                  [ported from Spec Critic]
    registry.py          AVAILABLE_MODULES / DEFAULT_MODULE / get_module
                                                                  [ported from Spec Critic]
    hyperscale_fire.py   the first module: catalog, playbook, NFPA pins, research
                         persona + dimensions      [seeded from Spec Critic datacenter_fire]
    generic.py           the any-discipline module (USA & Canada): unpinned basis,
                         open catalog, scaffold playbook, discipline-parameterized
                         research dimensions                       [Batch 10, native]
  research/
    engine.py            the fan-out: parallel streaming web-search dimensions,
                         pause_turn continuations, budget ceilings, grounding,
                         RequirementsProfile render + context trim
                                                                  [ported from Spec Critic]
    grounding.py         URL normalization, accepted-vs-cited validation, web-tool
                         evidence collectors                      [ported from Spec Critic]
    retry_policy.py      FailureClass taxonomy + backoff (realtime subset)
                                                                  [ported from Spec Critic]
    resend_sanitizer.py  fetched-PDF elision before continuation resume
                                                                  [ported from Spec Critic]
    schema.py            submit_requirements_research strict tool + web server-tool
                         builders + domain blocklist              [ported from Spec Critic]
    runner.py            session-bound background run: thread, event log, SSE follow
  compliance/            [deprecated — superseded by qc/; endpoints retained]
    checker.py           the audit call: controlling-set rules, coverage matrix,
                         strict tool + fallback              [ported from Spec Critic]
    runner.py            session-bound audit lifecycle (thread + status)
  tracing/
    recorder.py, spans.py, config.py, redaction.py
                         JSONL span/event recorder, env-gated, credential
                         scrubbing                           [ported from Spec Critic]
    capture.py           Build-a-Spec capture hooks (turns, tools, research,
                         audits, imports) — never raise
    viewer/trace_viewer.html  the bundled HTML trace viewer  [ported from Spec Critic]
  spec_doc/
    model.py             SectionFormat tree, stable ids (+ the `imported` status),
                         transactional edit ops (incl. set_standard_edition /
                         set_project_profile, source_item_id provenance),
                         per-turn version store (undo/redo), open-item extraction
    importer.py          master-.docx semantic body extraction + immutable
                         source mapping, fidelity accounting + warnings
                                                                  [ported from Spec Critic]
    source_mapping.py    conservative semantic-block ↔ OOXML-body bindings
    source_patch.py      fail-closed clone-and-patch preserved DOCX export
    linting.py           deterministic lint: stale editions, placeholders, structure
                                                                  [ported from Spec Critic]
    diffing.py           deterministic version diff (uid join, word-level runs,
                         status changes) powering the redline export + compare view
    docx_export.py       fresh normalized .docx rendering +
                         assumptions/imported/open-items
                         schedules + QC/compliance closing + the QC memo +
                         the tracked-changes (redline) body writer
    project.py           semantic project payload + legacy JSON compatibility
    project_package.py   bounded, hashed native .baspec container carrying the
                         project payload and optional exact source DOCX
  llm/
    client.py            Anthropic client factory (monkeypatch seam for tests)
    prompts.py           engine prompt protocol + module-rendered system prompt
                         + the full-draft and onboarding-demo directives
    conversation.py      streaming turn loop: apply_spec_edits dispatch,
                         web_search/web_fetch with pause_turn continuation,
                         adaptive thinking, the per-turn PROJECT CONTEXT
                         block (full document + lint + research), incremental
                         history caching, per-turn usage aggregation
frontend/                Vite + React + TypeScript + Tailwind v4
  src/App.tsx            state owner: chat + document + lint + research + QC +
                         readiness + update + SSE dispatch
  src/lib/api.ts         SSE parsing over fetch; doc/undo/edit/diff/draft-full/
                         onboarding-demo/project/research/import/qc/readiness/
                         update calls
  src/lib/reviewQueue.ts buildQueue(doc, mode): the review queue as a pure
                         document-order walk (port of iter_paragraphs)
  src/lib/tour.ts        the guided tour as pure data: starter prompts,
                         disciplines, 22 steps in 4 chunks, anchor resolvers
  src/lib/useOnboarding.ts  the tour's phase machine (runId zombie guard,
                         key-gate auto-advance, do-this-for-me dispatch)
  src/lib/onboardingStorage.ts  "tour completed" flag (cosmetic localStorage)
  src/components/        Chat (starter chips), MessageBubble (markdown),
                         Composer (ask-model prefill),
                         OnboardingOverlay (spotlight cutout + bubbles +
                         discipline/entry/work-choice dialogs + resume pill),
                         Header (spend ticker + update pill), ApiKeyBanner,
                         ArtifactPanel (stepper, Compare toggle + base picker,
                         export menu, import, "Draft full section", open items),
                         ReviewDrawer (keyboard review walk),
                         IssuesDrawer (lint + standards strip),
                         ResearchDrawer (profile + research),
                         QCDrawer (readiness checklist, lens progress, accept/dismiss
                         fix queue), SpecDocument (SectionFormat rendering + ◆ chips
                         + the read-only compare/diff render)
packaging/windows/       build-a-spec.spec (PyInstaller), installer.iss (Inno),
                         app_entry.py (--version/--selfcheck), make_manifest.py,
                         check_release_version.py       [cloned from Spec Critic]
docs/
  standards_provenance.md  receipts for every pinned edition
  RELEASE_WINDOWS.md       the release runbook
tests/                   hermetic pytest suite; fakes.py scripts multi-round
                         tool-use streaming turns + web-tool research responses
```

The backend serves the built frontend from `frontend/dist` in normal use; in development the Vite dev server proxies `/api` to the backend for hot reload.

## Requirements

- Windows 10/11 (WebView2 — preinstalled on current Windows), macOS, or Linux
- Python 3.11+
- Node 20+ (only to build or develop the frontend)
- An Anthropic API key

## Install (Windows, prebuilt)

Most users don't need any tooling. Download the latest
**BuildASpecSetup.exe** from the
[Releases page](https://github.com/Abe-Borg/build-a-spec/releases/latest)
and run it — Python, Node, and every dependency are bundled, and the
installer adds the Edge WebView2 runtime if your machine doesn't already
have it.

The app is not code-signed, so on first run Windows SmartScreen shows
"Windows protected your PC" → **More info → Run anyway**. Updates are
delivered in-app and SHA-256-verified before they install. Maintainers:
see [`docs/RELEASE_WINDOWS.md`](docs/RELEASE_WINDOWS.md) for how releases
are cut (a tag push builds and publishes the installer via GitHub Actions).

## Install & Run (from source, Windows)

```bat
:: 1. Python environment
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

:: 2. Build the UI once
cd frontend
npm install
npm run build
cd ..

:: 3. Launch
python main.py
```

A native window opens. If no API key is configured, enter one in the banner — it lands in Windows Credential Manager when `keyring` is installed, otherwise in a key file under your user config folder (`%APPDATA%\BuildASpec`). `ANTHROPIC_API_KEY` in the environment always wins and is never persisted.

### Development mode (hot reload)

Terminal 1:

```bat
.venv\Scripts\activate
set BUILD_A_SPEC_DEV=1
python main.py
```

Terminal 2:

```bat
cd frontend
npm run dev
```

The window loads the Vite dev server (localhost:5173), which proxies `/api` to the backend on 127.0.0.1:8756. Edit React code and it hot-reloads in place.

## Configuration

| Env var | Default | Effect |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | API key; overrides keyring/file, never persisted. |
| `BUILD_A_SPEC_INTERVIEW_MODEL` | `claude-sonnet-5` | Model for interview/drafting turns. |
| `BUILD_A_SPEC_MAX_TOKENS` | `128000` | Per-response output ceiling (defaults to the model max — no app limit). |
| `BUILD_A_SPEC_INTERVIEW_EFFORT` | `high` | Adaptive-thinking effort for interview turns (`low`/`medium`/`high`/`max`/`xhigh`). |
| `BUILD_A_SPEC_THINKING_DISPLAY` | `summarized` | Thinking-summary streaming: `summarized` streams a readable reasoning summary (the "see what the model is thinking" strip); `omitted` streams empty thinking. Degrades to `omitted` automatically if a model rejects the display key. |
| `BUILD_A_SPEC_CHAT_MAX_SEARCHES` | `8` | Interview web_search allowance per continuation round. |
| `BUILD_A_SPEC_CHAT_MAX_FETCHES` | `4` | Interview web_fetch allowance per continuation round. |
| `BUILD_A_SPEC_RESEARCH_MODEL` | `claude-sonnet-5` | Model for the research fan-out. |
| `BUILD_A_SPEC_RESEARCH_MAX_TOKENS` | `128000` | Per-dimension research output ceiling (model max). |
| `BUILD_A_SPEC_RESEARCH_EFFORT` | `xhigh` | Adaptive-thinking effort for research dimensions. |
| `BUILD_A_SPEC_QC_MODEL` | `claude-fable-5` | Model for the Final QC pass (the one non-Sonnet surface). |
| `BUILD_A_SPEC_QC_MAX_TOKENS` | `128000` | Per-call QC output ceiling (model max — no app limit). |
| `BUILD_A_SPEC_QC_EFFORT` | `xhigh` | Adaptive-thinking effort for QC lenses/verifiers. |
| `BUILD_A_SPEC_QC_VERIFIERS_STANDARD` | `2` | Verification panel size for medium/low findings. |
| `BUILD_A_SPEC_QC_VERIFIERS_CRITICAL` | `3` | Verification panel size for critical/high findings. |
| `BUILD_A_SPEC_QC_MAX_SEARCHES_COMPLIANCE` | `24` | web_search allowance for the code-compliance lens (runaway guard). |
| `BUILD_A_SPEC_QC_MAX_SEARCHES_LENS` | `8` | web_search allowance for the other lenses + verifiers. |
| `BUILD_A_SPEC_QC_MAX_FETCHES_COMPLIANCE` | `8` | web_fetch allowance for the code-compliance lens. |
| `BUILD_A_SPEC_QC_MAX_FETCHES_LENS` | `4` | web_fetch allowance for the other lenses + verifiers. |
| `BUILD_A_SPEC_PORT` | `8756` | Backend port (127.0.0.1 only). |
| `BUILD_A_SPEC_DEV` | off | Point the window at the Vite dev server. |
| `BUILD_A_SPEC_TRACE` | on | Session tracing (JSONL spans/events, local-only). `0` disables. |
| `BUILD_A_SPEC_TRACE_DEEP` | off | Inline prompts in traces (implies trace on). |
| `BUILD_A_SPEC_TRACE_DIR` | state dir | Where trace runs are written. |
| `BUILD_A_SPEC_UPDATE_URL` | GitHub latest | Override the update-manifest URL. |
| `BUILD_A_SPEC_DISABLE_UPDATE_CHECK` | off | Truthy disables update checks entirely. |

## Testing

Hermetic by default — no API key, no network. `tests/conftest.py` injects a placeholder key; API-touching tests monkeypatch a fake streaming client (the same convention as Spec Critic's suite).

```
.venv\Scripts\python -m pytest -q
```

## Relationship to Spec Critic

Decisions made at project start (2026-07): UI is **pywebview + React + FastAPI**; reusable Spec Critic code is **copied into this repo** (not a shared library); the first spec module is **hyperscale fire suppression, Division 21**; research agents land **immediately after** the core drafting loop is proven.

Ported so far (adapted, same design): `api_key_store.py`, `app_paths.py`, the hermetic-test fixture pattern, the model-id constants, the prompt-cache posture; in Phase 3 — `code_cycles.py` → `standards.py` (pinned editions with provenance; drives the REFERENCES article and the lint), `modules/base.py` + `registry.py` → `spec_modules/` (frozen modules, import-time registry validation), the `datacenter_fire.py` content seed, and the `preprocessor.py` detector logic → `spec_doc/linting.py` (span dedup, negation suppression, marker vocabulary); in Phase 4 — `project_profile.py` (≈verbatim), the `research/requirements_research.py` fan-out → `research/engine.py`, `source_grounding.py` + the verifier's evidence collectors → `research/grounding.py`, `retry_policy.py` (realtime subset), `resend_sanitizer.py` (fetched-PDF elision), and the research tool schema + web server-tool builders from `structured_schemas.py`/`api_config.py` → `research/schema.py`; in Phase 5 — `input/extractor.py`'s Accept-All tracked-changes and content-loss mechanics → `spec_doc/importer.py` (the SectionFormat tree builder on top is native), `compliance/compliance_checker.py`'s trust model → `compliance/checker.py`, `core/updates.py` → `updates.py` (≈verbatim), the `packaging/windows/` pipeline + release runbook (cloned, new AppId), and the `tracing/` core (recorder/spans/config/redaction ≈verbatim + the HTML viewer; `capture.py` is native). The port plan is complete — every planned Spec Critic reuse has landed.

## Roadmap

1. **Phase 1 — Shell.** Streaming interview chat, native window, key management, tests. *(Shipped in v0.1.0.)*
2. **Phase 2 — Living document.** Server-owned SectionFormat tree (Section → PART → article → paragraph) with stable element ids and per-block provenance (`confirmed` / `assumed` / `needs_input`); `apply_spec_edits` tool-use so drafts land in the panel, not chat; a defaults-first interview where "I don't know" is a valid answer — the model applies a defensible default and flags it, with assumptions badged in the panel and scheduled in the `.docx` export; change highlighting + version history; `.docx` export; save/resume project files. *(Shipped in v0.2.0.)*
3. **Phase 3 — Spec modules.** Registry-validated `SpecModule` (interview playbook, section catalog, code basis, pinned standards editions — NFPA 13-2025 default, jurisdiction-adopted editions override via `set_standard_edition` with the adoption basis recorded, never silently); live deterministic linting of the draft with an issues drawer and standards strip. *(Shipped in v0.3.0.)*
4. **Phase 4 — Research agents.** Port of the requirements-research fan-out: grounded web-search agents for governing codes, AHJ, client/insurer, and site environment, launched on demand from a conversationally-recorded project profile; accepted-vs-cited citation grounding; results in a panel drawer, spliced into drafting context, linked to provisions via `source_item_id`, and feeding jurisdiction edition overrides. *(Shipped in v0.4.0.)*
5. **Phase 5 — Ship.** Master-spec import with gap-and-adapt (imported provenance status, Accept-All tracked-changes handling), the compliance audit of the draft against the researched profile (coverage matrix + export closing section), Windows packaging/installer with the SHA-256-verified auto-updater, and session tracing with the bundled viewer. *(Shipped in v0.5.0.)*
6. **Post-ship batches (v0.6.0 → v1.0.0).** "Sonnet unleashed" no-limits context architecture (v0.6.0); streaming UX + manual editing + settings + cost meter (v0.7.0); full-section draft + keyboard review queue (v0.8.0); Final QC on Fable 5 with adversarial verification + accept/dismiss fix queue (v0.9.0); and the **1.0 release** — tracked-changes redline export over the normalized imported baseline or any semantic version, plus the in-app version-compare view, one diff engine behind both (v1.0.0).

Build-a-Spec is an AI-assisted drafting aid, not an authority. Its output is advisory and is not a substitute for review by a licensed design professional.
