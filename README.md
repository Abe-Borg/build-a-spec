# Build-a-Spec

**v1.7.0** — Conversational authoring of construction specification sections. You talk through the project with Claude; it interviews you, drafts CSI SectionFormat language incrementally, and builds the section live in a document panel beside the chat — the way artifacts work in the Claude app.

First curated domain: **Division 21 fire suppression for hyperscale data centers (USA)**, starting with wet-pipe sprinkler systems (21 13 13) and siblings. Since v1.5.0 a second, **generic module** drafts **any discipline, for projects anywhere in the USA or Canada** (no pinned editions — every standard edition is recorded per-project with its stated basis). The engine is domain-neutral; discipline knowledge lives in registry-validated **spec modules**, the same architecture as [Spec Critic](https://github.com/Abe-Borg/Claude-Spec-Critic)'s review modules.

Build-a-Spec is the drafting-side complement to Spec Critic: **Build-a-Spec writes specs through dialogue; Spec Critic reviews finished specs.** Large parts of this codebase are ports of Spec Critic's domain-neutral machinery (see "Relationship to Spec Critic" below).

## DOCX fidelity boundary

> **Important:** Build-a-Spec edits a deliberately narrow semantic view of an
> imported `.docx`; it is not a general-purpose Word editor.

Import retains the exact validated upload as an immutable source artifact and
separately extracts supported main-body content into the SectionFormat tree.
Headers, footers, page/section layout, styles, fields, drawings, content
controls, and other opaque OOXML are preserved but do not become editable
claims.

Because that tree is always SectionFormat, a file that is not a spec section
gets wrapped in structure it never had. The import says so plainly and the app
stops drawing the parts your file did not contain — see the status section
below. If you only want the assistant to *read* a document rather than edit
it, attach it as a reference instead of importing it; reference documents sit
outside all of the contracts below.

The export choices have different contracts:

| Choice | Contract |
|---|---|
| **Exact original** | Returns the retained upload byte-for-byte. A semantic no-op through source mode returns these same bytes. |
| **Source-preserving patched DOCX** | Starts from the retained package and applies only a final-state patch proven safe. Unchanged payloads and local records remain exact; ZIP metadata changes only for the replacement and required offsets. There is no normalized fallback. |
| **Normalized DOCX** | Generates a new DOCX from the semantic tree, with genuine Word automatic numbering. It makes no source-package fidelity claim. |
| **Normalized redline** | Generates Word tracked-change markup between two semantic versions. It is not a redline of the uploaded package and does not author revisions into that source. |
| **Pass-through-only document** | Keeps exact-original/no-op download available while disabling source-backed body mutation. Metadata and status operations may remain available. |

Simple mapped text and bounded add/delete/reorder inside a proven, isolated,
directly Word-numbered body island are the only source-backed mutation surface.
Ambiguity always narrows that surface. Signed, protected, revision-bearing,
active-content, unsupported-encoding, and unsupported-raw-ZIP-layout packages
that are safe enough to retain remain pass-through-only; uploads that fail the
initial package-safety boundary are rejected atomically. Build-a-Spec never
flattens or normalizes an imported package as an implicit recovery path.

Full Strict OOXML semantic import is a current compatibility limitation. The
package scanner recognizes Strict relationship and Word namespaces for safety
checks, but a fully Strict `word/document.xml` main part is rejected atomically
rather than retained, converted, or partially imported.

Native `.baspec` files carry the semantic project and the exact source DOCX as
separate, hashed members. Derived lexical byte indexes and patch contexts are
recomputed after load, not persisted. Legacy JSON projects remain loadable but
contain no source bytes and therefore offer normalized export only.

See [DOCX fidelity and compatibility](docs/DOCX_FIDELITY.md) for the complete
export, API payload, blocker-code, persistence, diagnostics, and test-fixture
contracts.

## Current Status — direct structural editing

The paper is now a practical outline editor as well as a model-authored
artifact. Once a native project has content on the page — from the interview,
a full-section draft, a template, or a master import — add articles, top-level
provisions, and subparagraphs directly through the four SectionFormat
provision levels. New user-authored provisions are confirmed, and article
deletion warns before removing its full provision subtree.

- **Reorder by grip or keyboard.** Articles move only within their PART;
  provisions move only among siblings under their current article or parent.
  Drag the grip, or focus it and use Space/Enter, Up/Down, and Escape. Existing
  up/down controls remain as a fallback. A move keeps stable IDs and carries
  the whole descendant subtree while positional numbering updates.
- **The safety boundary stays visible.** There is no PART reordering,
  cross-PART move, reparenting, promotion, or demotion. An imported DOCX still
  bound to its source does not permit article or nested structural edits; only
  flat Word-numbered edits and exact sibling positions explicitly authorized by
  the server are offered. A disabled control shows the server's reason, and no
  structural action silently switches the project to normalized-only output.
  **Edit freely** (v1.9.0) is the deliberate way past that boundary — it drops
  the byte-exact export promise for one document and restores the ordinary
  editor; see below.

## Current Status — non-spec uploads: honest framing + reference documents

**Uploading something that is not a spec section no longer dresses it as
one.** Import always builds a SectionFormat tree — that is what makes editing,
lint, compare, Final QC, and export work — but the app used to draw the
scaffolding around your content as though the file had come with it: a
`SECTION [TBD]` header, three PART headings, and a lint finding telling you to
name a section your memo does not have.

- **The import now says what it found.** If a file carries no SECTION number,
  PART heading, or numbered article, the import leads with that in plain
  words, the panel shows a short explanation instead of an invented header,
  empty parts are hidden, and the bogus "missing section header" finding is
  gone. Everything else still applies — a stale edition citation is worth
  flagging in any document. Your original file is retained exactly and
  downloads unchanged, as always.
- **It is a view, not a decision.** The moment a section number and title
  exist — you set them, or you ask the assistant to turn the content into a
  spec section — the normal spec presentation comes back. Nothing is
  suppressed permanently, and a genuine master is unaffected.

**Attach reference documents instead of importing them.** There is now a third
way to hand the app a file: **Attach reference** in the document panel,
for an owner's design standard, a basis-of-design narrative, a product data
sheet, a previous project's section, or meeting notes.

- **Whatever format you already have it in.** Word (`.docx`), PDF, plain text,
  XML, and CSV. A PDF is read page by page and the assistant can tell you
  which page something came from; a CSV or XML keeps its rows and tags,
  because for a schedule or an export that structure *is* the content. A
  scanned PDF with no text layer is refused with the reason rather than
  attached as something the assistant cannot actually read.
- **Background, never the spec.** A reference document is never added to the
  section, never edited, and never appears in lint, compare, Final QC,
  readiness, or any export. It is material for the assistant to read.
- **Read on request, so it stays cheap.** The assistant sees a one-line
  summary of each attachment every turn and opens the full text only when it
  actually needs it — so attaching a long standard does not inflate the cost
  of every later message. Ask it to "use the attached standard" and it will.
- **It is your content, so it is saved.** Attachments ride the project file
  and count as unsaved work, so a session whose only work is an attached
  standard still offers to save before you start over.
- Attach at any point in a session (unlike a master import, which needs a
  blank document), remove one with the ✕, up to 20 per session.

## Shipped in v1.9.0 (Edit freely, and a tour that just shows you)

**An imported Word section is no longer something you can only look at.**
Source mode's restrictions all serve one promise — that the `.docx` you
export is a byte-exact clone of the one you uploaded with only approved text
slices changed. **Edit freely** drops that promise for a document and gives
the ordinary editor back.

- **Edit freely.** One confirmed, one-way decision per document. Headings,
  structure, new articles — everything the editor normally offers. The
  retained upload, source map and baseline are all **kept**, so *Download
  original upload* still returns the exact bytes you sent and *Redline vs
  master* still works; only the byte-exact export claim goes, and the
  document exports normalized like any native one. Re-importing the file
  starts over.
- **A locked import says why.** Some packages can never be patched in place
  — tracked changes, macros or an embedded object, a digital signature,
  Restrict Editing. The report now names the package-wide cause once, with
  the remedy, instead of leaving every control greyed out unexplained.
  Tracked changes is the one to watch: the import shows the Accept-All view,
  so the file looks clean while still being frozen.
- **Numbered outlines keep their shape.** `w:numPr`'s `ilvl` was read as an
  absolute outline depth. It is an indent level *within a numbering
  definition*, so a master whose list starts at level 1 — ordinary in an
  office template — came through with every sibling article nested under the
  first one, plus synthetic `IMPORTED CONTENT` articles. The clamp is now
  remembered for the rest of the article, scoped per article, so documents
  that already parsed correctly parse byte-identically.

**A full draft asks before it writes.** `POST /api/draft/full` used to hand
back the drafting directive unconditionally, so a click on a blank session
produced a confident whole document built on nothing. Three prerequisites —
section number and title, project type, country — now gate it: they decide
what the document *is*, what every defaulted provision is defended by, and
which code family and units apply. A missing one returns **200 with
`ready: false`** and a directive that *collects* the gaps (defaults-first,
"I don't know" a real answer) and is forbidden from drafting that turn. The
click is always honored; city, state and client remain research
prerequisites, not drafting ones.

**A second research round stops paying for the first.** Rounds appended
their findings but the *request* was unchanged, so round 2 cost about what
round 1 cost and mostly re-derived it. Each dimension is now briefed on what
is already established for it and told to report only what is NEW, CHANGED
or CORRECTED — and to re-verify anything still `[UNVERIFIED]`. `POST
/api/research/start` also takes `scope: "gaps"`, which runs only the
dimensions that never completed. A brief only ever describes the project
being researched *now*: correct the city, jurisdiction or client and the
next round is not briefed at all.

**Everything else.** The Final QC report downloads no longer wait out the
imported-source permission sweep (minutes, silently, right after applying a
fix) and live only on the Final QC surfaces; a failed compliance audit bills
what it spent; the guided tour is a fixed passive track with no pausing, no
homework and no model call anywhere in it; "New session" clears the panes as
well as the server state; and the local API has a front door — see below.

**The loopback server is authenticated.** Each launch pre-binds an exclusive
OS-assigned ephemeral port and mints a boot nonce plus an API token. The
window receives the nonce in a URL fragment (never sent to the server, never
logged), exchanges it once at `/api/bootstrap` for an in-memory header token
and an HttpOnly/Strict cookie. Three paths stay token-free — `/api/bootstrap`
itself (guarded by the boot nonce instead), `/api/health` (the identity probe
the frontend needs *before* it holds a token; it returns a hash of the nonce,
never the nonce) and the static `/api/trace/viewer` — and every other `/api/*`
call must present a credential. Host and Origin are allowlisted, cookie-only
mutations must prove same-origin, and defensive headers plus a restrictive
CSP ride every response. Trace and log storage is now bounded by age, count
and bytes — never pruning the current run, a live process's run, or recent
unclean-shutdown evidence.

## Shipped in v1.7.0 (Release notes in the app)

The app tells you what changed when it updates, instead of leaving the notes
on a web page you never visit.

- **A "What's new" screen after an update.** The first launch of a new
  version opens the release notes for it, grouped by theme. Dismiss it and
  it stays dismissed; **Settings → What's new** reopens it any time.
- **The notes ship inside the build.** They are not fetched — a freshly
  updated app can show them with no network at all, which keeps the
  "nothing reaches the internet except model work and the disclosed update
  check" claim in the trust dossier true.
- **One source of truth.** `backend/release_notes.py` renders three
  surfaces: the in-app modal, the `notes` field in `latest.json` (what an
  app that has *not* updated yet shows you about the pending version), and
  the GitHub Release body. A version with no notes entry fails the test
  suite and the release workflow, so a release can't ship with an empty
  What's-new screen.
- **A fresh install is not shown a back catalogue.** Only an upgrade opens
  the notes; the app tells the two apart by whether it had ever run on the
  machine before.

Writing them is step 1 of the release runbook — see
[docs/RELEASE_WINDOWS.md](docs/RELEASE_WINDOWS.md).

## Shipped in v1.5.0 (Batch 10: Generic any-discipline module)

**Any discipline, any project type, anywhere in the USA and Canada.** A new
session starts from a blank, generic document, from a built-in or personal
template, or from a template file you load (see *Reusable templates* above).
The compatibility module APIs remain available for old projects and template
seeding, but the dialog does not ask the user to select a discipline or
describe the project.

- **No pinned editions in generic mode — deliberately.** The generic module
  ships zero standards pins. Every edition enters through the existing
  `set_standard_edition` override with a **stated basis**: a grounded
  research item, your statement, or an honestly-labeled model proposal
  ("model-proposed, unverified"). Until an edition is recorded, the draft
  cites designations year-free, and a new lint rule (`unrecorded_edition`,
  active only in generic mode) flags any year citation with no recorded
  basis — which flows into the readiness checklist, so an issue-ready draft
  has a recorded basis for every cited edition.
- **Project identity is learned from the work itself.** Once conversation or
  clear document context establishes the discipline and facility/use type, the
  model records them as versioned `project_identity` metadata. Discipline
  threads through the drafting context, all four research dimensions (now
  discipline-parameterized and US/Canada-aware — provincial NBC/NFC
  adoption, CSA/ULC listings, metric units), and the Final-QC lens briefs.
  The legacy saved-project discipline is only a fallback when identity is
  absent. The header stays blank until discipline is known, then shows the
  discipline alone until project type plus city/region are complete; afterward
  it reads `Discipline · Project Type · City, Region`.
- **The curated module remains compatible**: hyperscale fire suppression keeps
  its NFPA pins and playbook for existing projects and future templates.

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
  chips.

> Note: the v1.2.0 (Batch 7: stop generation / research / QC) and v1.3.0
> (Batch 8: chat figures) status sections were never written into this README;
> both shipped and are described in `CLAUDE.md`. This is a pre-existing
> documentation gap, called out here rather than silently left implied.

## Reusable templates

**Turn a finished section into a starting point.** Save the current spec as a
named template, then start any future project from it — yours, or one of the
built-in starters.

- **Two ways to create one.** *Exact* keeps your wording verbatim. *AI
  generalize* has the model rewrite project-specific language into reusable
  language and clears the project profile, edition overrides, and research
  provenance — then shows you **a diff of exactly what it changed, before
  anything is saved**. If the model alters structure, ids, or resolves an
  open decision, the preview is rejected outright and nothing is written.
- **They are files.** Export a template to a `.bastemplate` and hand it to a
  colleague; import theirs. Rename, re-describe, or delete your own; built-in
  starters are read-only.
- **Template content is labeled.** A section seeded from a template badges
  those blocks as a template starter, so nobody mistakes reusable boilerplate
  for a decision made about this project. It is not Word-source provenance
  and never unlocks source-preserving export.

## Set the section number and title yourself

The section header is editable in place: hover it, click the pencil, type the
number and title, press Enter. It is one undoable version and writes the same
operation the assistant would — so you can name a section without asking, or
ask and have it named. (An imported file with no spec structure deliberately
does not offer this: it has no section number, and inventing one is exactly
what the honest-framing rule above exists to prevent.)

## The guided tutorial — an actual specification, not a slideshow

**The tutorial teaches against real document state.** It runs on the bundled
showcase — a complete, pre-generated example section — in a protected
practice workspace, with no choices to make and no model calls to pay for:
your own project is set aside untouched the moment the tour starts, and
however you end it, it comes back exactly as it was.

- **Eleven chapters over a real workspace.** Start from an empty page ·
  the paper · grounding · review and compare · figures and references ·
  master import and source output · Final QC and readiness · export, save
  and support · templates — plus the workspace and conversation chapters
  that open it. Several chapters swap in a purpose-built practice copy built
  by **production code paths**: the import chapter really imports a DOCX, the
  save chapter really round-trips a `.baspec`, the references chapter really
  attaches five files through the real extractors.
- **It is a fixed track, and you are a passive observer.** The tour hands out
  no controls of its own and waits on nothing — it walks the app in order, and
  Continue moves on whenever you are ready. Steps are marked *optional* or
  *explanatory*; optional marks the ones whose subject spends money — a live
  research run, a Final QC pass — so you know before you go looking for the
  button. The spotlight still leaves every real control clickable, so you are
  free to read the document as you go; the tour simply never requires it.
  **Nothing is ever fabricated:** if there is no completed
  research or QC result, the step says exactly that rather than inventing
  findings or a readiness state.
- **Every feature is covered, and that is enforced by a test.** UI controls
  carry a capability id, tutorial steps reference the same ids, and the test
  suite asserts the two sets are equal in both directions — so a feature
  cannot ship untaught, and a tutorial step cannot describe a control that
  does not exist. Step anchors are checked against the real DOM attributes
  too.
- **There is one ending: your project comes back.** End the tour from any
  step, finish the last chapter, start a new session, open a project, or close
  the window — every one of those returns your original exactly as it was,
  same document, same history, same version list. One short confirmation, then
  it happens; nothing to choose and nothing to lose. Want the practice work
  too? Save it from the panel before you finish.
- **A guided run, start to finish.** The tour cannot be parked half-done
  holding your project aside — it either runs or ends and gives your session
  back, and every card and checkpoint carries an End. Step cards never block
  the app, so you can read the document as you go, and reloading picks the
  tour back up where you left it — but only when
  the server agrees the same protected workspace is still live. Help restarts
  it or jumps straight to any named chapter. Reduced motion is honored.

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

## Shipped in v0.9.0 (Batch 4: Final QC) and still current

**One button, a fleet of Opus 5 reviewers, a full audit-grade report, and a
compact accept/dismiss action queue.** The one place a model other than Sonnet
5 appears: a user-triggered last quality-control pass before a section goes
out the door. The report is a first-class product surface, not
an incidental memo: it shows what was reviewed, which evidence was retrieved,
how every candidate finding was challenged, what the run could not establish,
and exactly what happened to every proposed fix. The compact queue remains
beside it for fast remediation without making the user mine the report for
actions.

- **Five lenses, in parallel, on a stronger reviewer than the drafter.**
  "Send to Final QC" fans out five independent Opus 5 reviews of the whole
  section: **code
  compliance** (verifies every citation/edition against the standards'
  *actual current content* via web search — the big search allowance),
  **coordination & consistency** (PART 1/2/3 alignment, dangling
  cross-references, terminology drift), **completeness** (versus the grounded
  research profile and conventional section scope), **enforceability &
  language** (imperative mood, measurable criteria, no "as required"), and
  **provenance hygiene** (risky `assumed` blocks, surviving TBD/imported,
  provisions citing `[UNVERIFIED]` items). One lens failing never cancels the
  others; all five failing fails clean.
- **One defect buys one panel, not one per lens that noticed it.** Five
  reviewers reading one document routinely raise the same defect in
  different words, and each variant used to buy its own verification panel.
  Between review and verification, near-duplicate candidates that describe
  the *same actionable defect at the same element* are consolidated and
  adjudicated once. Grouping is confined to candidates that share a write
  scope (section-level candidates additionally need a shared retrieved
  source), every original claim is kept verbatim in the report under
  "Original lens claims", and where the lenses proposed different fixes the
  panel approves one reconciled remediation — or the finding stays advisory
  with the alternatives listed for you to choose. Any failure of the step
  falls back to a panel per candidate: it can cost more, it can never lose
  a finding. A `duplicate_provision` lint flags near-identical sibling
  provisions if one ever reaches the document by any route.
- **The report identifies the exact run and exact input.** It records report
  schema/protocol versions, a unique run id, reviewed document version and
  content/input fingerprints, input manifest, model, effective QC
  configuration, start/finish timestamps and duration, research-profile
  availability, and current/stale status. A later document edit cannot be
  mistaken for the version that was reviewed. New reports use schema `4` and
  protocol `final-qc/4`; earlier reports remain readable and exportable as
  historical evidence, but cannot provide an actionable fix queue — a
  schema-3 report was adjudicated under the superseded panel rule, so it is
  kept as the record of what was decided and never re-scored under the
  current one.
- **Each lens leaves an observable work record, including failures.** The
  report keeps the lens title, completion status or error, summary, explicit
  coverage checks and outcomes, search queries, retrieved sources, finding and
  grounding counts, and billed usage. Checks that passed or were not
  applicable remain visible; a zero-finding lens is therefore not a blank
  assertion that everything was fine. These are concise records of work and
  results, not private model chain-of-thought.
  Accepted final-attempt queries/retrievals are kept separate from all billed
  attempts, so failed fetches and evidence from an abandoned retry remain
  visible for cost/accountability without being allowed to ground a finding.
- **Adversarial verification is seat-by-seat and auditable.** Every candidate
  finding faces a panel of independent Opus 5 refuters prompted to *refute*
  it (2 for medium/low, 3 for critical/high). The report preserves every
  expected verifier seat, including its success, verdict, severity revision,
  proposed-fix adequacy decision and note, usage, or failure. **A finding is
  upheld only when the whole panel agrees.** A majority refutation refutes it;
  any other split — including 2-of-3 upholding a critical — is marked
  *disputed* and escalated to you rather than rounded to a yes or a no,
  because disagreement about a severe finding is itself worth knowing. And a
  refutation of a critical or high finding only counts when a refuting
  reviewer cites something that checks out: a page it actually retrieved, or
  a place in your own document. Running a search that found nothing is not
  evidence. Raising a panel size therefore raises scrutiny — under the old
  majority rule the extra critical seat quietly made refutation *easier*.
  A fix is executable only when every expected seat completes,
  upholds the finding, and approves the complete operation payload. A surviving
  finding without unanimous fix approval stays visible and advisory. A failed
  or cancelled seat remains visible and makes the candidate infrastructure-
  inconclusive; missing infrastructure is never presented as substantive
  evidence against a finding.
  This is the "as many agents as necessary" clause: total calls = 5 lenses +
  Σ panel sizes, with no cap on findings count (the runaway guards are
  per-call). The verifier queue keeps at most `BUILD_A_SPEC_QC_MAX_WORKERS`
  provider requests in flight (default 8);
  one immediate, nonretryable shared request-shape rejection opens a phase
  circuit breaker, preserves zero-request records for every unstarted seat, and
  settles the attempt partial instead of repeating the same bad request across
  the whole panel.
- **Sources are traceable one by one.** For every check and finding, the report
  distinguishes URLs the lens cited from URLs the run actually retrieved and
  accepted after grounding validation. It preserves source-level grounding
  results and the query/retrieval trail, so a reviewer can reopen the evidence
  and see which claims remain ungrounded rather than relying on a single
  aggregate "grounded" badge.
- **Every finding carries its whole adjudication history.** Surviving findings
  show their lens and element anchor (or explicit section-level scope), the
  saved reference/text from the reviewed snapshot and whether the model's
  anchor actually resolved, issue, rationale, original severity, every
  verifier record, and final computed severity. An unresolved anchor remains
  visible as a limitation rather than being presented as a verified location.
  Refuted candidates receive the same treatment in a detailed appendix: the
  original case, source grounding, complete panel record, and the reason they
  did not reach the action queue. A separate infrastructure-inconclusive
  appendix retains candidates with failed or cancelled seats without calling
  them refuted. No candidate is deleted from the audit trail.
- **Accept the fix, or dismiss it — and dismiss decisions survive re-runs.**
  The report preserves the full proposed `apply_spec_edits` operation payload,
  unanimous semantic-approval result, snapshot dry-run result and validation
  error, if any. Only semantically approved operations enter deterministic and
  source-preservation validation; `ops_valid` means both checks passed.
  **Apply fix** edits the document exactly as previewed, in **one undo step**
  after validation against the current document; a moved target is recorded
  `stale` and skipped, never partially applied. Multi-finding requests
  deduplicate identical operations, but reject different operations that claim
  the same deterministic write target with a structured `409` before mutating
  anything. **Dismiss** requires and preserves a reviewer rationale
  (blank/whitespace reasons are rejected) and is
  remembered by content-addressed id, so a re-run that regenerates the same
  finding auto-marks it dismissed. Open, applied, dismissed, advisory,
  invalid, no-op, and stale outcomes remain in the report. Individual actions
  remain available when a reviewer wants to handle one finding at a time.
- **Guided remediation turns the audit into a short decision workflow.** Open
  findings are separated into fixes that are ready to apply, explicit
  TBD/assumption items that need a project decision, and findings that still
  require professional review. Users can select the ready fixes once, request
  a server-authoritative no-change preview, and confirm the conflict-free set
  as one undoable document version. The preview de-duplicates identical
  operations and excludes every finding involved in a competing write instead
  of guessing which edit should win. The confirmation is bound to that exact
  preview, and source-backed projects pass the imported-Word preservation gate
  before a fix is advertised as safe. Decision items can prefill the chat with
  the finding, affected provision, review rationale, current text, and missing
  fact so the user supplies the knowledge while the assistant does the
  drafting. After application, chat records a concise finding-by-finding
  change receipt and explains skipped outcomes; the paid Final QC rerun
  remains an explicit user choice.
- **Module scope is checked without rewriting the specification.** For curated
  modules with a closed section catalog, import, QC status, and QC start compare
  the normalized specification section number with that catalog. A mismatch is
  an advisory import warning and appears in the QC cost confirmation. Running
  remains allowed after a dedicated acknowledgement; the server enforces the
  acknowledgement with the stable `module_section_mismatch` conflict code and
  never changes the selected module or specification automatically.
- **Issue readiness — the "can it go out the door" screenshot moment.** A
  deterministic checklist (no model call) at the top of the QC drawer goes
  green exactly when: no open items, no unreviewed imported/assumed blocks,
  lint clean, research complete, QC current, and the audit contract complete
  with no open criticals. "Research complete" means every research area the
  module declares has actually completed — a run that finished with some
  areas failed blocks readiness and names which ones, because absent findings
  are not the same as an area that was checked and found nothing. Pressing
  Research again re-runs every area and appends what it finds, so a retry
  costs a full round but never loses what earlier rounds already established. Freshness and audit sufficiency are shown as
  separate checks: users can tell whether a failure means stale inputs, a
  failed/latest attempt, legacy data, incomplete lens/verifier coverage, or an
  unresolved critical. Any lens failure or missing or failed verifier seat
  makes the report explicitly partial and blocks readiness; a partial panel
  never converts incomplete execution into a full sign-off. A disputed
  candidate blocks readiness too, until you either address it or dismiss it
  with a reason — it is not a defect in the review, it is the review telling
  you the reviewers disagreed.
- **Read it in-app or file the same record.** **View full report** opens the
  complete report in the app while the drawer keeps a compact, severity-sorted
  action queue. Word (`.docx`) and machine-readable JSON downloads carry the
  run/input identity, configuration, per-lens work, verifier seats,
  source-grounding detail, findings and fix operations, refuted appendix,
  usage, estimated cost, limitations, coverage status, and staleness warning.
  Download controls pin the run id displayed in the snapshot; if another run
  changes the backend selection before the click completes, the server rejects
  the request instead of silently returning a different report.
  The Word document is the human sign-off artifact, ending with a reviewer
  checklist and signature page; JSON is the lossless audit and
  downstream-integration artifact. The software records the review but does
  not itself approve or seal the specification. The main spec export includes
  the QC closing only when the export-time `qc_current` and
  `qc_audit_complete` readiness checks both pass; otherwise it omits that
  closing and falls back to the compliance audit when one exists.
- **A failed rerun never erases either side of the history.** The latest
  attempt has its own run id, status, timing, error and report/activity record;
  the last successful report is retained separately. Readiness turns red when
  the latest attempt is running, cancelled, partial, or failed. Both identities
  travel through project save/load and exports, preventing an older success
  from being presented as the latest paid review. Runner completion, project
  persistence, readiness, and both downloads consume one lock-coherent audit
  snapshot, so report bytes cannot mix old results with new attempt metadata.
  Partial/failed records restore only as read-only attempt evidence, never as
  the actionable retained queue; apply and dismiss independently recheck a
  current audit-complete result and remain locked while a stopped worker is
  still settling billable output.
- **Spend and limits are part of the evidence.** The report breaks out billed
  token/cache/search usage, API/model response counts, and estimated cost for
  the run, with attribution to lenses and verifier seats where captured. It
  saves the exact pricing-rate snapshot and fallback basis used for the
  estimate — including both cache-write rates and a note on how the
  provider's one-hour subtotal is charged — and a report saved before that
  snapshot grew its one-hour rate keeps the four-rate basis it was actually
  priced under rather than being rewritten. It also states material
  limitations — such as absent research context, failed calls, incomplete web
  retrieval or grounding, and document staleness — so the user can tell the
  difference between "no defect found" and "not fully checked." Research
  coverage is stated three ways rather than as a bare "present: Yes": no
  profile, complete, or partial with the count and the names of the areas that
  never completed, marked when they were required for issue readiness. The
  Word report and the in-app report read the same captured record, so they
  cannot disagree — and a report already exported keeps its own facts even
  after later research fills the gap.
- **No dead air — Final QC runs in an inline Review Room.** Starting QC opens
  the drawer once and replaces the readiness checklist with a compact,
  truthful three-stage rail: **Specialist lenses → Adversarial panels → Local
  fix validation**. The specification stays visible. Five responsive lens
  cards move from queued to running to settled using real provider-stream
  activity, search, fetch and retry events; each card keeps at most three
  recent query/source labels (display-only, never transient links) and settles
  into its actual reviewed-check, candidate, grounding, request and tool
  counts. There is no timer-driven or synthetic progress.
- **Adversarial review is visible seat by seat.** The verification phase begins
  with the complete candidate roster, using run-local ids such as
  `candidate-1` without changing a finding's durable content-addressed id.
  Candidates are grouped as in review, waiting and resolved; every row names
  its severity and originating lens and shows each expected verifier as
  queued, active, upheld, not upheld, failed or cancelled. A panel becomes
  **Upheld**, **Refuted** or **Inconclusive** only after every expected seat is
  accounted for. An empty roster still advances honestly through verification
  and validation instead of making phases disappear.
- **Fix validation is the real local dry run.** Upheld candidates proceed to
  the existing deterministic/source-preservation checks, with live local
  progress distinguishing safe fixes from advisory or manual outcomes. When
  the attempt completes, the board resolves into a concise recap — completed
  lenses, reviewed candidates, upheld/refuted/inconclusive totals and safe
  fixes — followed by the existing remediation queue and user-opened full
  report controls.
- **The live contract is additive and stays on the existing channel.**
  `/api/qc/status` remains the authoritative snapshot and `/api/qc/stream`
  remains the SSE feed. Lens activity adds `lens_started`, `lens_activity`,
  `lens_search`, `lens_fetch` and `lens_retry`; panels add
  `verification_started`, `verifier_started`, `verifier_activity`,
  `verifier_search`, `verifier_fetch`, `verifier_retry`,
  `verifier_complete`, `candidate_complete` and `verification_complete`; local
  checks add `validation_started`, `validation_progress` and
  `validation_complete`. Existing terminal events, billing, retry and
  cancellation rules remain intact, including the exact `stream_end`
  sentinel. Events expose observable work and outcomes, never prompts, notes,
  token text or hidden reasoning.
- **Chatty streams do not make the UI race itself.** The client appends events
  locally by `seq`, deduplicates replayed frames, reconciles same-run snapshots
  without replacing a longer local log, refetches full status/report data only
  at milestones and stream completion, and reconnects an unexpectedly closed
  follower while the authoritative attempt is still running or settling.
  After Stop, the board remains visible under a clear “finishing already-paid
  in-flight work” notice and actions stay locked until
  `qc_attempt_settled`. The stop-emitted `qc_failed` frame carries
  `settling: true`, so the client can preserve that truthful state even if a
  milestone refresh fails. Stale responses and events from a superseded run
  cannot overwrite the current report or leak into the next attempt: the
  client advances a request generation at every successful/new streamed run
  and ignores every side effect (including authentication prompts) from an
  older generation's response.
- **Motion and announcements remain accessible.** The Review Room reuses the
  research board's warm surfaces, breathing agent dot, shimmer, tally flash
  and entry motion, with reduced-motion fallbacks for every animation. Text
  accompanies every status symbol, card grids follow drawer/container width,
  and one aggregate polite live region announces progress without stealing
  focus, scrolling the document or flooding a screen reader.
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
- **It never drafts blind.** A whole-section draft anchors on three facts —
  the **CSI section** (number and title), the **project type** (facility/use),
  and the **country** — and every defaulted provision it lays down inherits
  them. The section decides what the document *is*; the project type is what
  the defaults are defended by; the country picks the code family and the
  units (I-codes/NFPA/UL and inch-pound, or NBC-NFC/CSA/ULC and SI). So while
  any of the three is unrecorded the button sends a turn that **asks you for
  exactly those**, defaults-first with a recommendation for each, and is
  forbidden from drafting that turn; answer, then draft. The tooltip names
  what is still needed before you click. City, state, and client are
  deliberately *not* prerequisites — they refine a draft rather than decide
  its shape (the full profile is the *research* gate).
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
  Cache **writes are priced per TTL class**: a five-minute entry costs
  1.25× input to create, a one-hour entry 2×. The provider reports the
  one-hour count inside the cache-creation total, so the meter charges the
  two rates over disjoint slices — the subtotal is never billed twice, and
  never billed at the cheaper rate just because it is nested. **Work you
  did not get is still work you paid for**: a research round where every
  dimension failed, and one you stopped part-way, both land in the meter
  even though their findings are discarded. Nothing is quietly unbilled
  just because it did not produce a result. Stopping a chat reply is the
  one case where a *token count* is itself estimated — closing the stream
  skips the provider's final tally — so that part is measured from what
  arrived and shown as a separate `+N` addition, never folded into the
  reported output. Every other number in the table is provider-reported.

Shipped in v0.6.0 ("Sonnet unleashed") and still current (project decision:
the app imposes **no quality limits on the model** — the only caps left are
runaway circuit breakers sized so no legitimate turn ever meets one):

- **The model sees the whole document, every turn.** The truncated outline
  is gone from the drafting context: a PROJECT CONTEXT block in each
  turn's user message carries the current date and time, the full text of
  every provision (ids, statuses, ◆research-provenance chips), the
  standards editions in effect, the research profile, the live lint
  report, and the open-item list. The model can no longer edit a paragraph
  it can't see — and it fixes its own stale citations and placeholders
  because the lint now talks to it, not just to you.
- **It also knows what day it is.** A model has no clock, so without being
  told it judges "is this the current edition?" against training data that
  gets staler every month — and codes revise on multi-year cycles. Every
  chat turn, every research dimension, and every Final QC reviewer is now
  given the real date off your machine, plus the standing instruction to
  treat a plausibly-superseded edition as a question to raise rather than
  something to quietly redraft around.
- **Prompt-cache restructure that pays for all of it.** The dynamic
  context used to sit between the cached system prompt and the message
  history, busting the cache for the whole history every doc-changing
  turn; now the system prompt is stable-only and live state rides the
  newest user message. Strictly more context per turn, cheaper per turn.
  A later pass finished the job: a tail breakpoint alone still could not
  cache *across* turns, because the entry it wrote ended with that turn's
  live context and commit strips exactly those bytes. Chat requests now
  also carry a **rolling breakpoint on the committed-history boundary**,
  so each turn reads everything the previous turn wrote and pays only for
  the newest exchange — at a one-hour lifetime, since an interview turn
  is a person reading and typing. The request tail keeps a short-lived
  entry instead: it can only ever be read by continuation rounds inside
  the same turn, so buying it an hour would be paying for a lifetime
  nothing uses.
- **Adaptive thinking, wired properly.** Requests state
  `thinking: adaptive` explicitly with effort knobs (interview `high`,
  research `high` — see below), and thinking blocks are preserved verbatim
  across tool-use continuation rounds as the API requires — the previous
  code dropped them, a latent 400 on real drafting turns. Output ceilings
  sit at the model max (128k tokens), so nothing the app controls
  truncates a draft.
- **Live web lookups in the interview.** The drafting model carries
  `web_search`/`web_fetch` (same authoritative-domains blocklist as the
  research phase) for mid-interview verification — a UL category, a
  manufacturer datasheet, a standard designation — with `pause_turn`
  continuation handling and inline 🔍 activity chips in the chat. The
  systematic research fan-out stays button-triggered. Both tools declare
  `allowed_callers: ["direct"]`: the model invokes them itself, with no
  server-side code-execution container in the loop. That is what keeps the
  per-search inputs streaming (the 🔍 chips and the research agent board are
  built from them), keeps a paused turn resumable without a provider
  container id, and keeps the whole app zero-data-retention eligible — one
  declaration in `research/schema.py` covers the interview, the research
  fan-out, and Final QC alike.
- **Research budgets doubled** (per-dimension searches now 16–40, fetches
  8–12, continuation ceiling 16) and research runs at `high` effort —
  background work where latency is free and quality is the point.
  (Dialed back from `xhigh` on 2026-07-28: research fans out 4 concurrent
  per-dimension calls, so `xhigh`'s extra reasoning depth was compounding
  across all of them and driving up cost — see the config table below.)
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
- **Session tracing.** The ported Spec Critic tracing core (JSONL spans + events, background writer, credential redaction, prompt-hash dedup, deep mode) records turns — now with per-round detail and prompt material — plus every REST request and state-changing action (edits, exports, project saves/loads, QC dispositions, stops, key changes, frontend errors), research runs, audits, Final QC, and imports. Every record carries a run/process identity and monotonic sequence; requests carry a correlation id, stable outcome code, timing, and workspace generation before/after. The live run metadata checkpoints capture counts by event/span/request outcome, token totals, queue count/byte high-water marks, categorized drops, write failures, active-run storage, and open spans, so the diagnostic system reports its own gaps. Runs are local-only, env-gated (`BUILD_A_SPEC_TRACE`, default on), storage-bounded by age/count/bytes (with the byte ceiling also preventing one active run's JSONL payload from growing without bound), and viewable through the self-contained HTML viewer at `GET /api/trace/viewer` (no network, dynamic event filters).
- **Always-on activity log + Developer tools.** Every launch writes a rotating local log beneath its own `<log-root>/process-<uuid>/` directory (`BUILD_A_SPEC_LOG`, default on: requests, errors with tracebacks, crashes via `faulthandler` and exception hooks, an unclean-shutdown marker) — the only place output survives in the packaged windowed build, where stdout/stderr go to devnull. Credential-shaped substrings are redacted from normal messages and exception text before file formatting. Historical log runs are storage-bounded by age/count/bytes without pruning the current launch, another live process, or recent unclean-shutdown evidence. **Settings → Developer tools** shows process/server identity, document shape and generation, import evidence, research/audit/QC worker state, trace coverage and writer health, recent activity, the log tail, retention results, and the trace-run list. Its one-click **diagnostics bundle** contains the point-in-time snapshot, the current launch's bounded log rotations, read-only/redacted legacy flat logs, the flushed current trace, bounded event/span tails from up to three completed prior runs, an exact inclusion/truncation manifest, and a time-ordered recent-incident index; live sibling runs are identified but never copied. The artifacts are local-only but may contain draft text, prompts, document titles, file paths, and error context; treat both folders and every exported bundle as sensitive project data.

Shipped in v0.4.0 (Phase 4) and still current (the near-verbatim port of Spec Critic's requirements-research fan-out, pointed at drafting):

- **Project profile, conversationally.** As you state the project's city/state/country/client in the interview, the model records them with a `set_project_profile` operation (normalized against the ported US-state/CA-province tables, riding the same undo/save machinery as document text). A complete profile arms the research phase.
- **Grounded requirements research, on demand.** A "Research requirements" button in the panel launches four parallel streaming web-search agents — governing codes & amendments, AHJ requirements (including the water purveyor), client/insurer standards, site environment — each searching as the project's own locale, with pause-turn continuation, per-dimension search budgets, a 2× runaway ceiling, and a fetched-PDF elision guard so a 600-page code PDF can't 400 its own continuation. Research never auto-triggers: dozens of web searches are real spend, so you pull the trigger. **You watch it work, live**: the panel opens onto a per-agent board where each of the four agents narrates in real time — what it's doing ("Searching the web…", "Reading a source…"), the actual search queries and source URLs as they happen, running search/source counts, and retry notices — until each card settles into its findings summary.
- **Rounds append; nothing is overwritten.** Run research again — after the interview turns up a new concern, or to retry a dimension that failed — and the new findings are added to what you already have. A requirement found again is confirmed in place (citations union, grounding and confidence take the better of the two) rather than duplicated, so item ids stay stable for the ◆ provenance chips your provisions already cite. Once a session has more than one round, every finding carries the date of the round that last **grounded** it in a retrieved source — a round that merely re-states an item without grounding it confirms nothing and does not re-date it — and the report breaks out what each round added versus re-confirmed. A round that fails or is stopped costs only that round; the earlier ones are untouched, and the message says so. Rounds survive save/resume and keep counting.
- **Citations or it didn't happen.** Every reported item is validated accepted-vs-cited: a URL the model cites must match one the server tools actually retrieved, or the item renders **[UNVERIFIED]** (kept as a lead, never a fact). Process/schedule facts render **[PROCESS]** and never become spec text. One dimension failing never cancels the others; partial profiles are flagged; total failure aborts clean. A **View report** button opens the full findings report in a modal — every agent's items grouped by dimension, with each dimension's completion status and search/fetch telemetry, requirement, authority, code reference, confidence, and grounded sources.
- **Research → drafting, closed loop.** The profile block joins the drafting context every turn (token-capped, trimmed lowest-confidence-first; the structured profile keeps everything). Provisions drafted from a research item carry its `source_item_id` — a ◆ chip in the panel answers "why is this paragraph here?" with the requirement and its accepted sources. When a grounded item establishes the jurisdiction's adopted edition, the model records a Phase 3 `set_standard_edition` override citing the item — and the lint immediately checks the draft against it.
- **Research results persist**: the profile rides the project file; a resumed project restores it into the panel drawer and the drafting context.

Shipped in v0.3.0 (Phase 3) and still current:

- **Spec modules.** Discipline knowledge moved out of the hardcoded system prompt into frozen, registry-validated `SpecModule` objects (`backend/spec_modules/`) — section catalog, defaults-first interview playbook (every defaultable topic ships its recommended default; the non-defaultable minimum is marked *must ask*), drafting prompt slots, lint vocabulary, and dormant Phase 4 research dimensions. A bad module definition fails at startup, never mid-session. First module: `hyperscale_fire` (Div 21, USA — 21 13 13 wet-pipe lead section with the full playbook; dry-pipe, preaction, fire pumps, water service, standpipes, common-work, and clean-agent sections in the catalog).
- **Pinned standards editions.** The module pins the current published editions as drafting defaults — **NFPA 13-2025** first among them, plus NFPA 14-2024, 20-2025, 22-2023, 24-2025, 25-2026, 72-2025, 75-2024, 76-2024, 291-2025, 2001-2025, 855-2026 over IBC/IFC 2024 model-code context. Every pin carries maintainer provenance (receipts in `docs/standards_provenance.md`). When you state the jurisdiction's adopted edition ("Loudoun County is on the 2021 VCC → NFPA 13-2019"), the model records it with a `set_standard_edition` operation — adoption basis required, never silent — and the override drives the REFERENCES article, the lint, and the export from then on. Overrides ride the same transactional/undo/save machinery as document text.
- **Live linting.** Deterministic, no-API checks run on every document mutation and render in an advisory issues drawer (click to jump): standard citations that contradict the editions in effect (with a negation-suppression window so "superseded by…" prose doesn't false-flag), unresolved placeholders (`[INSERT …]`, `___`) and template markers (`TODO:`, `FIXME`, lorem ipsum), empty articles, duplicate article titles, and a heads-up when drafting proceeds with the section header unset. A standards strip under the panel shows every edition in effect, overrides highlighted with their basis.

What worked before (Phase 2) and still does:

- Claude-desktop-style UI: streaming chat pane on the left, the **live specification document** on the right, warm dark theme.
- The model drafts exclusively through the `apply_spec_edits` tool into a server-owned SectionFormat tree (Section → PART 1/2/3 → articles → nested paragraphs, positional display labels `1.1` / `A.` / `1.` / `a.` / `1)`, stable element ids). Those semantic labels are not themselves Word numbering definitions; clean normalized export renders them with genuine Word automatic numbering. Edits are validated server-side and applied transactionally; each turn's changes stream into the panel as they happen, with changed blocks highlighted.
- Per-block provenance: `confirmed` / `assumed` / `needs_input`, badged in the panel. `[TBD: …]` markers and needs-input blocks are tracked as open items — listed under the panel (click to jump) and scheduled in the export.
- Defaults-first interview: every question carries a recommended answer; "I don't know" applies a defensible NFPA 13-2025 / hyperscale-norm default stamped `assumed`; guide-me mode turns open questions into concrete options with tradeoffs.
- Version stepper: one snapshot per turn that changed the document; undo/redo from the panel header.
- `.docx` export via python-docx — SectionFormat styling plus an **assumptions schedule** (every `assumed` block with its numbering, for one-pass senior review) and an open-items schedule.
- Project save/resume: a native `.baspec` package bundling the conversation,
  full document version history, import report, and exact source DOCX when one
  exists—undo and source-preserving export still work after resume. Legacy JSON
  projects remain load-compatible but do not contain source bytes.
- Save behaves like a save button: the first save of a session asks for a name
  and a folder, and every save after that overwrites that file in place with no
  dialog (**Save as…** appears under a caret beside Save once there is a file to
  say "as" against, and re-points where Save writes). The target belongs to the
  session — starting a new session or opening another project clears it, so the
  next Save asks again rather than silently overwriting the project you left.
- API key management: `ANTHROPIC_API_KEY` env var → OS credential manager (via `keyring`) → key file fallback, same posture as Spec Critic. A banner in the UI stores your key if none is found.
- Session reset, prompt-cached system prompt, hermetic test suite (no network, no key).

All five roadmap phases are shipped. What remains is real-world hardening: cutting the first Windows release from the runbook, growing sibling-section playbooks and modules, and tuning the import heuristics against your actual office masters.

## Architecture

```
main.py                  pywebview shell: starts the backend, opens the native window
backend/                 FastAPI + the conversation engine (Python 3.11+)
  app.py                 /api/health, /api/key, /api/session/reset, /api/chat (SSE),
                         /api/draft/full,
                         /api/doc (+ undo/redo/edit/diff/capabilities),
                         /api/export/docx (+ ?redline=master|version),
                         /api/import/master + /api/import/original,
                         /api/research/start|status|stream,
                         /api/qc/start|status|stream|apply|dismiss|export +
                         /api/qc/export.json,
                         /api/readiness, /api/audit/* (deprecated),
                         /api/templates (+preview/import/{id}/export/
                         {id}/instantiate),
                         /api/tutorial/status|start|scenario/*|restore,
                         /api/reference/upload + /api/references,
                         /api/figures + /api/figure/{fid}/csv,
                         /api/session/unsaved|bundle, /api/usage,
                         /api/update/check|install,
                         /api/trace/viewer, /api/project/save + load/load-file
  templates.py           TemplateCatalog: curated + personal libraries, the
                         preview→commit create flow (Exact / AI-generalize),
                         import/export/instantiate
  tutorial.py            tutorial coverage analysis, the bundled showcase (the
                         tour's only source), and the per-chapter practice-copy
                         builders — all deterministic, no model calls
  qc/
    schema.py            QC lens definitions + submit_qc_findings/consolidation/
                         verdict strict tools + observable reviewed-check and
                         finding/verdict normalization (never chain-of-thought)
    engine.py            run_final_qc: lens fan-out -> cross-lens consolidation
                         -> adversarial verification
                         -> ops validation -> audit-grade QCResult with versioned
                         input/run identity, evidence and seat telemetry; raw
                         provider streams relay observable lens/verifier activity
                                                        [pattern: research/engine.py]
    runner.py            session-bound QC lifecycle: daemon thread, event log,
                         run-token-isolated SSE follow + exact stream_end
                                                        [pattern: research/runner.py]
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
  diagnostics.py         always-on rotating activity log + crash capture
                         (faulthandler, exception hooks, unclean-shutdown
                         marker) + the /api/diagnostics* snapshot/tail/
                         trace-list/bundle helpers behind Developer tools
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
                         builders + domain blocklist + the direct-caller pin
                         (WEB_TOOL_ALLOWED_CALLERS) shared by chat/research/QC
                                                                  [ported from Spec Critic]
    runner.py            session-bound background run: thread, event log, SSE follow
  compliance/            [deprecated — superseded by qc/; endpoints retained]
    checker.py           the audit call: controlling-set rules, coverage matrix,
                         strict tool + fallback              [ported from Spec Critic]
    runner.py            session-bound audit lifecycle (thread + status)
  tracing/
    recorder.py, spans.py, config.py, redaction.py
                         JSONL span/event recorder, env-gated, credential
                         scrubbing                           [ported from Spec Critic]
    capture.py           Build-a-Spec capture hooks (turns + rounds + prompts,
                         tools, app events, research, audits, QC, imports) —
                         never raise
    viewer/trace_viewer.html  self-contained HTML trace viewer (native rewrite —
                         no network, dynamic event filters, prompt-ref resolution)
  spec_doc/
    model.py             SectionFormat tree, stable ids (+ the `imported` status),
                         transactional edit ops (incl. set_standard_edition /
                         set_project_profile, source_item_id provenance),
                         per-turn version store (undo/redo), open-item extraction
    source_package.py    bounded DOCX upload + defensive ZIP/OPC inspection
    importer.py          master-.docx semantic body extraction + immutable
                         source mapping, fidelity accounting + warnings
                                                                  [ported from Spec Critic]
    source_mapping.py    conservative semantic-block ↔ OOXML-body bindings
                         + canonical source blocker messages
    xml_lexical.py       encoding-aware lexical index + byte-local XML patches
    raw_zip.py           strict raw-record clone + document-part replacement
    source_audit.py      bounded package-preservation comparison
    source_patch.py      capability probes + shared final-state gate +
                         fail-closed clone-and-patch preserved DOCX export
    linting.py           deterministic lint: stale editions, placeholders, structure
                                                                  [ported from Spec Critic]
    diffing.py           deterministic version diff (uid join, word-level runs,
                         status changes) powering the redline export + compare view
    docx_export.py       fresh normalized .docx rendering +
                         assumptions/imported/open-items
                         schedules + QC/compliance closing + the full Final QC
                         Word report +
                         the tracked-changes (redline) body writer
    project.py           semantic project payload + legacy JSON compatibility
    project_package.py   bounded, hashed native .baspec container carrying the
                         project payload and optional exact source DOCX
  llm/
    client.py            Anthropic client factory (monkeypatch seam for tests)
    prompts.py           engine prompt protocol + module-rendered system prompt
                         + the full-draft directive
    conversation.py      streaming turn loop: apply_spec_edits dispatch,
                         web_search/web_fetch with pause_turn continuation,
                         adaptive thinking, the per-turn PROJECT CONTEXT
                         block (full document + lint + research), incremental
                         history caching, per-turn usage aggregation
frontend/                Vite + React + TypeScript + Tailwind v4
  src/App.tsx            state owner: chat + document + lint + research + QC +
                         readiness + update + SSE dispatch
  src/lib/api.ts         SSE parsing over fetch; doc/undo/edit/diff/draft-full/
                         project/research/import/qc/readiness/
                         update calls
  src/lib/reviewQueue.ts buildQueue(doc, mode): the review queue as a pure
                         document-order walk (port of iter_paragraphs)
  src/lib/qcReport.ts    pure Final QC report formatting, coverage (incl. the
                         captured partial-research verdict shared with Word),
                         source-link,
                         operation, usage, and limitations helpers
  src/lib/qcLive.ts      typed live-event merge, same-run snapshot reconciliation,
                         milestone policy, and pure three-stage Review Room fold
  src/lib/researchLive.ts  the research follower's sibling of qcLive: merge by
                         seq, watermark staleness, stream-end classification
  src/lib/eventSeqIndex.ts  the sequence index both followers dedupe replay
                         against, so reconnecting costs one pass, not a scan
                         per frame
  src/lib/capabilities.ts  the end-user capability vocabulary; the tutorial
                         covers it and a test enforces both directions
  src/lib/tour.ts        the versioned tutorial manifest: starter prompts,
                         chapters (with backend scenarios), steps, anchors,
                         document resolvers, readiness, and step actions
  src/lib/useOnboarding.ts  tutorial lifecycle: showcase workspace start,
                         scenario swap, chapter jump, restore, resume persistence
  src/lib/onboardingStorage.ts  "tour completed" flag + the resume record
  src/components/        Chat (starter chips), MessageBubble (markdown),
                         Composer (ask-model prefill),
                         OnboardingOverlay (spotlight + step cards + finish
                         choices; real controls stay interactive),
                         Header (spend ticker + update pill), ApiKeyBanner,
                         ArtifactPanel (stepper, Compare toggle + base picker,
                         export menu, import, "Draft full section", open items),
                         ReviewDrawer (keyboard review walk),
                         IssuesDrawer (lint + standards strip),
                         ResearchDrawer (profile + research),
                         QCDrawer (three-stage Final QC Review Room, readiness,
                         run recap, compact accept/dismiss fix queue),
                         QCReportModal (complete
                         audit-grade in-app report + Word/JSON downloads),
                         SpecDocument (SectionFormat rendering + ◆ chips
                         + the read-only compare/diff render)
packaging/windows/       build-a-spec.spec (PyInstaller), installer.iss (Inno),
                         app_entry.py (--version/--selfcheck), make_manifest.py,
                         check_release_version.py       [cloned from Spec Critic]
docs/
  DOCX_FIDELITY.md         export/API/blocker/compatibility contract
  DOCX_FIDELITY_CORPUS.md  fixture provenance and evidence boundary
  DOCX_RENDERER_WINDOWS.md optional Word/LibreOffice visual-test setup
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
| `BUILD_A_SPEC_CHAT_CACHE_TTL` | `1h` | Prompt-cache lifetime for a chat request's *cross-turn* breakpoints — the system block and the committed-history boundary (`5m` or `1h`). One hour by default because an interview turn is a person reading and typing, which routinely outlives 5 minutes, and a lapsed entry is re-written at full price rather than read at 0.1×. The request tail is always written at the shortest TTL and is not configurable: its entry is keyed on context that is stripped at commit, so nothing after this turn can read it. An unsupported value logs a warning and falls back to the default. |
| `BUILD_A_SPEC_CHAT_MAX_SEARCHES` | `8` | Interview web_search allowance per continuation round. |
| `BUILD_A_SPEC_CHAT_MAX_FETCHES` | `4` | Interview web_fetch allowance per continuation round. |
| `BUILD_A_SPEC_RESEARCH_MODEL` | `claude-sonnet-5` | Model for the research fan-out. |
| `BUILD_A_SPEC_RESEARCH_MAX_TOKENS` | `128000` | Per-dimension research output ceiling (model max). |
| `BUILD_A_SPEC_RESEARCH_EFFORT` | `high` | Adaptive-thinking effort for research dimensions (dialed back from `xhigh` on 2026-07-28 — cost). |
| `BUILD_A_SPEC_QC_MODEL` | `claude-opus-5` | Model for the Final QC pass (the one non-Sonnet surface). |
| `BUILD_A_SPEC_QC_MAX_TOKENS` | `128000` | Per-call QC output ceiling (model max — no app limit). |
| `BUILD_A_SPEC_QC_EFFORT` | `high` | Adaptive-thinking effort for QC lenses/verifiers. |
| `BUILD_A_SPEC_QC_MAX_WORKERS` | `8` | Concurrent QC calls in flight (lenses share the pool with verifiers). |
| `BUILD_A_SPEC_QC_VERIFIERS_STANDARD` | `2` | Verification panel size for medium/low findings. |
| `BUILD_A_SPEC_QC_VERIFIERS_CRITICAL` | `3` | Verification panel size for critical/high findings. |
| `BUILD_A_SPEC_QC_CONSOLIDATION` | `1` | Group near-duplicate lens findings about one defect onto a shared verifier panel. Off reviews every raw candidate separately (the pre-5.2 behaviour, and the fallback every failure path already takes). |
| `BUILD_A_SPEC_QC_CONSOLIDATION_MAX_BUCKET` | `25` | Runaway guard on one grouping call's input; a larger bucket falls back to separate panels and records why. |
| `BUILD_A_SPEC_QC_MAX_SEARCHES_COMPLIANCE` | `24` | web_search allowance for the code-compliance lens (runaway guard). |
| `BUILD_A_SPEC_QC_MAX_SEARCHES_LENS` | `8` | web_search allowance for the other lenses + verifiers. |
| `BUILD_A_SPEC_QC_MAX_FETCHES_COMPLIANCE` | `8` | web_fetch allowance for the code-compliance lens. |
| `BUILD_A_SPEC_QC_MAX_FETCHES_LENS` | `4` | web_fetch allowance for the other lenses + verifiers. |
| `BUILD_A_SPEC_PORT` | `8756` | Fixed loopback backend port used only in Vite development. Packaged/browser production pre-binds an exclusive OS-assigned ephemeral loopback port per launch. |
| `BUILD_A_SPEC_DEV` | off | Point the window at the Vite dev server. |
| `BUILD_A_SPEC_TRACE` | on | Session tracing (JSONL spans/events, local-only). Traces may contain document text; treat them as sensitive project data. `0` disables. |
| `BUILD_A_SPEC_TRACE_DEEP` | off | Inline prompts in traces (implies trace on and is especially sensitive). |
| `BUILD_A_SPEC_TRACE_DIR` | state dir | Where trace runs are written. |
| `BUILD_A_SPEC_TRACE_MAX_RUNS` | `100` | Maximum retained trace runs; oldest eligible runs are pruned. `0` disables this one ceiling. |
| `BUILD_A_SPEC_TRACE_MAX_AGE_DAYS` | `30` | Maximum trace-run age in days. `0` disables this one ceiling. |
| `BUILD_A_SPEC_TRACE_MAX_MIB` | `512` | Trace-retention target applied at recorder startup, and a hard cap for each active run's JSONL payload. Live runs are never pruned, so aggregate storage may temporarily exceed the target. `0` disables both byte limits. |
| `BUILD_A_SPEC_LOG` | on | The rotating activity log (requests, errors, crashes; local-only, beside the traces). `0` disables. |
| `BUILD_A_SPEC_LOG_LEVEL` | DEBUG | Log level for the activity log (chatty third-party loggers stay tamed regardless). |
| `BUILD_A_SPEC_LOG_DIR` | state dir | Parent root for per-launch log directories. Legacy flat files remain read-only. |
| `BUILD_A_SPEC_LOG_MAX_RUNS` | `50` | Maximum retained per-launch log runs. `0` disables this one ceiling. |
| `BUILD_A_SPEC_LOG_MAX_AGE_DAYS` | `30` | Maximum eligible log-run age in days. `0` disables this one ceiling. |
| `BUILD_A_SPEC_LOG_MAX_MIB` | `256` | Aggregate per-launch log storage ceiling in MiB. `0` disables this one ceiling. Current/live runs and recent unclean-shutdown evidence are protected; invalid or negative settings use the default. |
| `BUILD_A_SPEC_UPDATE_URL` | GitHub latest | Override the update-manifest URL. |
| `BUILD_A_SPEC_DISABLE_UPDATE_CHECK` | off | Truthy disables update checks entirely. |

## Testing

Hermetic by default — no API key, no network. `tests/conftest.py` injects a placeholder key; API-touching tests monkeypatch a fake streaming client (the same convention as Spec Critic's suite).

```
venv\Scripts\python -m pytest -q
```

The live Final QC suite uses scripted streams to cover activity/search/fetch
relay, retries and malformed frames; complete and empty candidate rosters;
verifier-seat and validation outcomes; SSE replay, stop settlement and
superseded-run isolation. Frontend unit tests exercise the pure event fold,
`seq` deduplication and snapshot reconciliation without relying on animation
timing. UI changes must also pass `npm test` and `npm run build`; Review Room
visual checks include the 420px document-pane minimum, wider layouts, keyboard
navigation and reduced motion.

The paid provider-schema smoke test is separate and explicitly opt-in; it
sends one low-token QC verifier request and never runs a full Final QC:

```
venv\Scripts\python tools\qc_verifier_canary.py --run
```

Without `--run`, the command only reports whether a key is configured.

The DOCX fidelity contract, fixture layers, frontend checks, and release
verification commands are documented in
[`docs/DOCX_FIDELITY.md`](docs/DOCX_FIDELITY.md). Renderer-backed visual tests
must be reported by the renderer/version actually exercised; their absence is
not equivalent to a visual pass.

## Relationship to Spec Critic

Decisions made at project start (2026-07): UI is **pywebview + React + FastAPI**; reusable Spec Critic code is **copied into this repo** (not a shared library); the first spec module is **hyperscale fire suppression, Division 21**; research agents land **immediately after** the core drafting loop is proven.

Ported so far (adapted, same design): `api_key_store.py`, `app_paths.py`, the hermetic-test fixture pattern, the model-id constants, the prompt-cache posture; in Phase 3 — `code_cycles.py` → `standards.py` (pinned editions with provenance; drives the REFERENCES article and the lint), `modules/base.py` + `registry.py` → `spec_modules/` (frozen modules, import-time registry validation), the `datacenter_fire.py` content seed, and the `preprocessor.py` detector logic → `spec_doc/linting.py` (span dedup, negation suppression, marker vocabulary); in Phase 4 — `project_profile.py` (≈verbatim), the `research/requirements_research.py` fan-out → `research/engine.py`, `source_grounding.py` + the verifier's evidence collectors → `research/grounding.py`, `retry_policy.py` (realtime subset), `resend_sanitizer.py` (fetched-PDF elision), and the research tool schema + web server-tool builders from `structured_schemas.py`/`api_config.py` → `research/schema.py`; in Phase 5 — `input/extractor.py`'s Accept-All tracked-changes and content-loss mechanics → `spec_doc/importer.py` (the SectionFormat tree builder on top is native), `compliance/compliance_checker.py`'s trust model → `compliance/checker.py`, `core/updates.py` → `updates.py` (≈verbatim), the `packaging/windows/` pipeline + release runbook (cloned, new AppId), and the `tracing/` core (recorder/spans/config/redaction ≈verbatim + the HTML viewer; `capture.py` is native). The port plan is complete — every planned Spec Critic reuse has landed.

## Roadmap

1. **Phase 1 — Shell.** Streaming interview chat, native window, key management, tests. *(Shipped in v0.1.0.)*
2. **Phase 2 — Living document.** Server-owned SectionFormat tree (Section → PART → article → paragraph) with stable element ids and per-block provenance (`confirmed` / `assumed` / `needs_input`); `apply_spec_edits` tool-use so drafts land in the panel, not chat; a defaults-first interview where "I don't know" is a valid answer — the model applies a defensible default and flags it, with assumptions badged in the panel and scheduled in the `.docx` export; change highlighting + version history; `.docx` export; save/resume project files. *(Shipped in v0.2.0.)*
3. **Phase 3 — Spec modules.** Registry-validated `SpecModule` (interview playbook, section catalog, code basis, pinned standards editions — NFPA 13-2025 default, jurisdiction-adopted editions override via `set_standard_edition` with the adoption basis recorded, never silently); live deterministic linting of the draft with an issues drawer and standards strip. *(Shipped in v0.3.0.)*
4. **Phase 4 — Research agents.** Port of the requirements-research fan-out: grounded web-search agents for governing codes, AHJ, client/insurer, and site environment, launched on demand from a conversationally-recorded project profile; accepted-vs-cited citation grounding; results in a panel drawer, spliced into drafting context, linked to provisions via `source_item_id`, and feeding jurisdiction edition overrides. *(Shipped in v0.4.0.)*
5. **Phase 5 — Ship.** Master-spec import with gap-and-adapt (imported provenance status, Accept-All tracked-changes handling), the compliance audit of the draft against the researched profile (coverage matrix + export closing section), Windows packaging/installer with the SHA-256-verified auto-updater, and session tracing with the bundled viewer. *(Shipped in v0.5.0.)*
6. **Post-ship batches (v0.6.0 → v1.0.0).** "Sonnet unleashed" no-limits context architecture (v0.6.0); streaming UX + manual editing + settings + cost meter (v0.7.0); full-section draft + keyboard review queue (v0.8.0); Final QC with adversarial verification, a full audit-grade in-app/Word/JSON report, and a compact accept/dismiss action queue (v0.9.0); and the **1.0 release** — tracked-changes redline export over the normalized imported baseline or any semantic version, plus the in-app version-compare view, one diff engine behind both (v1.0.0).

Build-a-Spec is an AI-assisted drafting aid, not an authority. Its output is advisory and is not a substitute for review by a licensed design professional.

## License

Released under the [MIT License](LICENSE).

Copyright (c) 2026 [Abraham Borg](https://github.com/Abe-Borg) ·
[LinkedIn](https://www.linkedin.com/in/abrahamborg/)
