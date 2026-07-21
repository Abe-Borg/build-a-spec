# ROADMAP.md — Detailed plans for Phases 3–5

`README.md` → Roadmap holds the one-paragraph summaries; this file holds the
working detail. These plans were written **before Phase 2 completed** — at
each phase boundary, generate a `PHASE_N_KICKOFF.md` from the as-built state
(the way `PHASE_2_KICKOFF.md` was produced) and revise the affected plan here
rather than trusting it blindly. CLAUDE.md remains the source of truth for
conventions and frozen decisions.

**Session requirement for all three phases:** connect BOTH folders —
`C:\Github-Repos\build-a-spec` and `C:\Github-Repos\Claude-Spec-Critic`.
These phases port code out of Spec Critic (copy-and-adapt, provenance noted
in module docstrings, never cross-repo imports). Phase 2 has no hard
Spec Critic dependency; 3–5 do.

---

## Phase 3 — Spec modules, pinned standards, live linting ✅ SHIPPED (v0.3.0)

**As built (2026-07-21).** All five work items landed as planned; details
in `CLAUDE.md` → "Phase 3 — implemented notes". Deviations from the
original sketch worth knowing when reading the code:

- **Edition overrides became a document op.** `set_standard_edition`
  (target `sec`; `standard` + `edition` + required `basis`; empty edition
  removes) rather than any UI form — overrides live on the tree
  (`SpecSection.edition_overrides`), so they ride the existing
  transactional apply / undo / project-file machinery for free. The model
  records one only when the user states the adoption; Phase 4 research
  will become the second legitimate source.
- **Pins = current published editions** (NFPA 13-2025, 14-2024, 20-2025,
  22-2023, 24-2025, 25-2026, 72-2025, 75-2024, 76-2024, 291-2025,
  2001-2025, 855-2026; IBC/IFC 2024 as model-code context), verified
  2026-07 with receipts in `docs/standards_provenance.md`. Deliberately
  NOT Spec Critic's I-code-referenced editions — drafting defaults to
  current, jurisdiction overrides once known. (Checked: the NFPA
  13D/13R/24/291-into-13 consolidation did not happen.)
- **"Detector vocabulary" shrank to `lint_extra_marker_patterns`.** The
  stale-edition vocabulary falls out of the pins themselves (per-name
  citation patterns incl. the REFERENCES-line shape), so the module only
  contributes extra marker regexes. The Spec Critic year/abbreviation
  vocabulary wasn't needed.
- Catalog: 21 13 13 carries the full playbook (`playbook_depth="full"`);
  21 13 16 / 21 13 19 / 21 30 00 / 21 11 00 / 21 12 00 / 21 05 00 /
  21 22 00 are catalog-depth entries the model drafts from conventions +
  pins.
- Frontend surfaces: ⚠ count badge in the panel header, an Issues drawer
  (click-to-jump), and a collapsible Standards strip (overrides
  highlighted with basis) — `IssuesDrawer.tsx`.

78 hermetic tests green; suite grew from 28 (test_standards,
test_spec_modules, test_linting + API/model extensions).

---

## Phase 4 — Research agents (AHJ / client / insurer) ✅ SHIPPED (v0.4.0)

**As built (2026-07-21).** The near-verbatim port landed; details in
`CLAUDE.md` → "Phase 4 — implemented notes". Deviations from the sketch
below worth knowing when reading the code:

- **Profile capture is conversational, not a form** — the
  `set_project_profile` op on the tree (model records it as the user
  states location/client; undo/save ride along). The ported US/CA tables
  still back normalization; no React form was built.
- **Research is button-triggered, never auto** — real spend stays a user
  decision. `POST /api/research/start` + `GET /api/research/status` +
  `GET /api/research/stream` (SSE replay-and-follow with a `stream_end`
  sentinel), run on a session-bound daemon thread whose results a
  reset/load abandons safely.
- **Results render in a panel drawer, not chat cards** — consistent with
  the Phase 3 issues/standards strips; grounded items link their accepted
  sources, `[UNVERIFIED]`/`[PROCESS]` marked.
- **The splice targets the dynamic system block** (no "Project Context"
  textarea exists here): `research_context_block` trims whole items
  lowest-confidence-first under a ~16k-token estimate; the structured
  profile is never trimmed.
- **Provenance is `source_item_id`** on add_paragraph/replace → ◆ chips
  in the paper; research-grounded adoptions become `set_standard_edition`
  overrides citing the item id (prompt-driven, no new machinery).
- `corpus_signals.py` was evaluated and **skipped** (no corpus until
  Phase 5 master import). Thinking/effort config deliberately not ported.
- New dependency: `pypdf` (fetched-PDF elision on continuation resume).

Suite grew 78 → 99 hermetic tests (engine fan-out/grounding/budgets,
profile normalization, API lifecycle, provenance + research round-trips);
the fan-out is faked per-dimension via `SequencedFakeClient` so parallel
workers stay deterministic.

### Original sketch (for reference)

**Goal:** grounded web research answers the questions the user shouldn't
have to — jurisdiction adoptions and amendments, AHJ requirements, client
and insurer standards, site environment — with citations, folded into
drafting context. This is the near-verbatim port of Spec Critic's
requirements-research fan-out.

### Work items

1. **Port `src/core/project_profile.py`** (≈ verbatim): city /
   state-or-province / country / client with normalization,
   `web_search_user_location()` (steers the server-side `web_search` tool),
   `jurisdiction_fingerprint()` (cache keying). UI: a project-profile form
   using the ported US state / CA province tables; profile persists in the
   project file.
2. **Port `src/research/`** → `backend/research/`: `run_requirements_research`
   fan-out (parallel per-dimension streaming web-search calls with
   `pause_turn` continuation), `RequirementsProfile` / `ResearchItem` /
   `DimensionStatus`, accepted-vs-cited URL grounding, and
   `splice_profile_into_context` (token-capped merge). **Porting note:**
   `requirements_research.py` imports helper functions from
   `verification/verifier.py` (evidence collectors, stop-reason
   classification) and `verification/retry_policy.py` + `source_grounding.py`
   — extract the needed helpers into a small `backend/research/grounding.py`
   during the port rather than dragging the whole verifier over.
3. **Dimensions come from the module.** `SpecModule.research_dimensions`
   (authored in Phase 3 on `hyperscale_fire`: governing_codes /
   ahj_requirements / client_standards / site_environment, already
   registration-validated against the profile placeholders) activates here
   — what to research and per-dimension search budgets are module data;
   the engine is domain-neutral. **Research findings become the second
   legitimate source for `set_standard_edition` overrides** (grounded
   adoption facts, cited basis) — the op and its plumbing already exist.
4. **Orchestration + UX.** Research triggers when the profile completes (or
   on demand). Runs on a background thread; progress streams as SSE events
   (`research_started` / `research_progress` / `research_complete`) rendered
   as status cards in chat ("Researching Loudoun County AHJ requirements…")
   with a citations drawer. Failure policy ports as-is: one dimension
   failing never cancels the others; partial profiles are flagged; total
   failure aborts cleanly with nothing corrupted.
5. **Grounding → drafting provenance.** Ungrounded items render
   `[UNVERIFIED]`, grounded items carry accepted citation URLs. The drafting
   prompt is instructed to prefer profile facts over training priors and to
   reference the driving `ResearchItem` when one motivates a block — giving
   blocks a `source_item_id` so the panel can answer "why is this paragraph
   here?" with a citation.
6. **Tests:** fake streaming fan-out fixtures, partial-failure and all-fail
   paths, grounding accept/reject cases, splice token cap, profile
   round-trip through the project file.

### Spec Critic files to read/port
`src/core/project_profile.py`, `src/research/requirements_research.py`,
`src/research/corpus_signals.py` (evaluate — may not apply without a spec
corpus), `src/verification/retry_policy.py`,
`src/verification/source_grounding.py`, `src/review/structured_schemas.py`
(the research tool schema), and the research-dimension definitions inside
`src/modules/datacenter_fire.py`.

---

## Phase 5 — Master import, compliance audit, ship ✅ SHIPPED (v0.5.0)

**As built (2026-07-21).** All five work items landed (tracing included);
details in `CLAUDE.md` → "Phase 5 — implemented notes". Deviations from
the sketch worth knowing:

- The imported provenance status is named **`imported`** (the "exact
  status name decided at build time" decision); import is allowed only
  into an empty document and lands as one undoable version.
- The extractor port took the *mechanics* (Accept-All tracked changes,
  content-loss warning, revision detection) — the SectionFormat tree
  builder is native, since Spec Critic extracts flat text for review
  while drafting needs the hierarchy. `extraction_cache.py` was not
  ported (one-shot imports cache nothing).
- The compliance audit is a single-call, single-section pass (no
  chunking machinery needed) with the trust model preserved exactly;
  coverage is always complete — a requirement the model skipped reports
  `unclear` rather than disappearing. Results stamp the audited version
  index so staleness is visible in the drawer and the export.
- The updater state lives in the platformdirs config dir (with the key
  file) rather than a home dot-dir. The Inno AppId GUID is
  Build-a-Spec's own; the version-consistency release gate runs inside
  pytest, not just at release time.
- Tracing shipped as the scoped port (recorder core + native capture
  hooks + bundled viewer at `/api/trace/viewer`), one app-lifetime
  trace per launch.
- New dependency: `python-multipart` (the import upload). PyInstaller
  stays a build-time-only install, documented in the runbook.

Suite grew 99 → 132 hermetic tests. What the roadmap leaves for
post-1.0 life: cutting the first actual release from
`docs/RELEASE_WINDOWS.md` on a Windows box (PyInstaller/ISCC cannot run
in the build container), sibling-section playbooks, more modules, and
import-heuristic tuning against real office masters.

### Original sketch (for reference)

**Goal:** meet real workflow (nobody drafts from a blank page), close the
loop against the researched requirements, and ship a Windows installer.

### Work items

1. **Master-spec import.** Port `src/input/extractor.py` (and
   `extraction_cache.py` if useful) to ingest an office master or previous
   project `.docx` and parse it into the document tree with stable element
   ids. Imported blocks enter with a distinct provenance (imported-not-yet-
   reviewed — exact status name decided at build time) and the interview
   pivots to gap-and-adapt mode: walk the master against this project's
   profile, confirming/adapting article by article instead of drafting from
   zero.
2. **Compliance audit.** A post-drafting pass modeled on Spec Critic's
   `src/compliance/compliance_checker.py`: check the draft against the
   Phase 4 `RequirementsProfile` and report each controlling requirement as
   represented / contradicted / unclear / missing (advisory items excluded).
   Results render in the issues drawer and as a closing section in the
   `.docx` export. This is the in-app little sibling of "run it through Spec
   Critic when done" — full reviews still belong to Spec Critic.
3. **Packaging + auto-update.** Clone Spec Critic's pipeline: PyInstaller
   one-folder + Inno Setup installer, GitHub Releases distribution,
   SHA-256-verified auto-updater (`src/core/updates.py`,
   `packaging/windows/`, `docs/RELEASE_WINDOWS.md` is the runbook). Bundle
   `frontend/dist`; document the WebView2 dependency (preinstalled on
   current Windows).
4. **Tracing (opportunistic).** `src/tracing/` is domain-neutral and can be
   ported in any phase if session forensics are wanted sooner; if it hasn't
   landed by now, port it here (spans/events JSONL + the HTML viewer) so
   shipped-app behavior is reconstructable.
5. **Tests:** import round-trip goldens (docx → tree → docx), compliance
   pass against a fake profile, updater manifest/hash verification
   (`tests/test_updates.py` in Spec Critic has the patterns).

### Spec Critic files to read/port
`src/input/extractor.py`, `src/input/extraction_cache.py`,
`src/compliance/compliance_checker.py`, `src/core/updates.py`,
`packaging/windows/*`, `docs/RELEASE_WINDOWS.md`, `src/tracing/*`,
`tests/test_updates.py`.

---

## Cross-phase conventions

- At each phase boundary: update this file against what was actually built,
  regenerate a `PHASE_N_KICKOFF.md`, and keep `README.md` / `CLAUDE.md` /
  `requirements.txt` current (standing preference).
- Copy-and-adapt only — no cross-repo imports; every ported module's
  docstring names its Spec Critic source file.
- Hermetic tests throughout; verify `pytest` + `npm run build` before
  delivering; commit changed files back to `C:\Github-Repos\build-a-spec`.
- NFPA 13 default edition stays 2025 until a jurisdiction's adopted edition
  says otherwise — and the override is always stated, never silent.
