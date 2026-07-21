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

## Phase 3 — Spec modules, pinned standards, live linting

**Goal:** discipline knowledge moves out of the hardcoded system prompt into
frozen, registry-validated `SpecModule` objects — Spec Critic's
`ReviewModule` architecture, pointed at authoring instead of review.

### Work items

1. **Port the standards machinery.** `Claude-Spec-Critic/src/core/code_cycles.py`
   → `backend/standards.py`: keep `StandardEdition` / `BaseCode` (frozen
   dataclasses with maintainer `source` provenance fields and the
   `edition_phrase` / `description` rendering helpers); drop the
   California-cycle-specific wiring. Every pinned edition must carry a
   `source`; entries not confirmed against a published adoption are prefixed
   `UNVERIFIED`, same convention as Spec Critic.
2. **New `backend/spec_modules/`** mirroring `src/modules/` in Spec Critic:
   - `base.py`: frozen `SpecModule` dataclass + import-time
     `validate_module_registry` (bad prompt slots / inconsistent vocabulary
     fail at startup, never mid-session).
   - `registry.py`: `AVAILABLE_MODULES` / `DEFAULT_MODULE` / `get_module`.
   - `hyperscale_fire.py`: the first module. Seed its content from
     `src/modules/datacenter_fire.py` (code basis, keywords, research
     dimensions — the research dimensions stay dormant until Phase 4).
3. **SpecModule contents (initial shape — refine during build):**
   module_id + display name; **section catalog** (21 13 13 wet-pipe first;
   siblings like dry-pipe, preaction, fire pumps, and common-work sections
   added incrementally); **interview playbook** (ordered topic graph, each
   topic with its recommended default and whether it is non-defaultable);
   **code basis + pinned `StandardEdition` collection** (NFPA 13-2025
   default; the exact pin list is authored during this phase with provenance
   sources, and jurisdiction-adopted earlier editions override once known —
   never silently); **drafting prompt slots** (system prompt becomes a
   template rendered from the module, and the pinned editions render the
   PART 1 REFERENCES article data); **detector vocabulary** for linting.
4. **Live linting.** Deterministic, no-API checks run on every document
   mutation, modeled on Spec Critic's preprocessor detectors
   (`src/input/preprocessor.py` is the vocabulary/pattern reference):
   unresolved `[TBD]`s and `needs_input` blocks (already tracked), empty
   articles, standard references that contradict the module's pinned
   editions (stale-edition detection), duplicate headings, placeholder/
   template markers. Surface as panel badges + an issues drawer; issues are
   advisory, never blocking.
5. **Tests:** registry validation failure modes (startup rejection), lint
   detector cases, REFERENCES-article generation from pins, module-rendered
   prompt snapshot tests.

### Spec Critic files to read/port
`src/modules/base.py`, `src/modules/registry.py`,
`src/modules/datacenter_fire.py`, `src/core/code_cycles.py`,
`src/input/preprocessor.py` (detector patterns only).

---

## Phase 4 — Research agents (AHJ / client / insurer)

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
   (seeded in Phase 3 from `datacenter_fire`) activates here — what to
   research and per-dimension search budgets are module data; the engine is
   domain-neutral.
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

## Phase 5 — Master import, compliance audit, ship

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
