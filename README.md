# Build-a-Spec

**v0.5.0 (Phase 5)** — Conversational authoring of construction specification sections. You talk through the project with Claude; it interviews you, drafts CSI SectionFormat language incrementally, and builds the section live in a document panel beside the chat — the way artifacts work in the Claude app.

First target domain: **Division 21 fire suppression for hyperscale data centers (USA)**, starting with wet-pipe sprinkler systems (21 13 13) and siblings. The engine is domain-neutral; discipline knowledge lives in registry-validated **spec modules**, the same architecture as [Spec Critic](https://github.com/Abe-Borg/Claude-Spec-Critic)'s review modules.

Build-a-Spec is the drafting-side complement to Spec Critic: **Build-a-Spec writes specs through dialogue; Spec Critic reviews finished specs.** Large parts of this codebase are ports of Spec Critic's domain-neutral machinery (see "Relationship to Spec Critic" below).

## Current Status — Phase 5, master import + compliance audit + ship

New in this release:

- **Master-spec import (gap-and-adapt).** "Import master" ingests an office master or previous-project `.docx` into the live tree — SectionFormat structure parsed from explicit labels *or* Word auto-numbering, tables flattened with warnings, and pending tracked changes resolved to the **Accept-All view** (the text that would actually issue), all mechanics ported from Spec Critic's extractor. Every block enters with a fourth provenance status, **`imported`** (badged blue, scheduled in the export until reviewed), and the interview pivots to gap-and-adapt: walking the master against this project article by article — confirm, adapt, or delete — instead of drafting from zero. Nothing is ever silently dropped; every parse guess lands in the import warnings.
- **Compliance audit.** One click audits the draft against the Phase 4 requirements profile, with Spec Critic's trust model intact: only **grounded** requirements control; `[UNVERIFIED]` items can at most earn a confirm-with-authority advisory; `[PROCESS]` items are excluded. Output: a coverage matrix (`represented / missing / contradicted / unclear`, every controlling requirement always classified — a skipped one reports `unclear`, never invisible) with evidence quotes + click-to-jump element ids, advisory findings, a staleness marker when the draft moves past the audited version, and a **compliance closing section in the `.docx` export**. Full multi-spec reviews still belong to Spec Critic.
- **Windows packaging + auto-update.** Spec Critic's release pipeline, cloned: PyInstaller one-folder build (`packaging/windows/build-a-spec.spec`, bundling the built frontend + pywebview/WebView2), Inno Setup installer with its own stable AppId, and the serverless GitHub-Releases updater — `latest.json` manifest fetched https-only (redirect-downgrade guarded), installer **SHA-256-verified before it ever runs**, once-a-day throttle, skip-this-version, and an update pill in the header. `docs/RELEASE_WINDOWS.md` is the runbook; `--version`/`--selfcheck` smoke-test the frozen exe; a version-consistency gate keeps settings/package.json/tag aligned (and runs in pytest).
- **Session tracing.** The ported Spec Critic tracing core (JSONL spans + events, background writer, credential redaction, prompt-hash dedup, deep mode) records turns, tool dispatches, research runs, audits, and imports — local-only, env-gated (`BUILD_A_SPEC_TRACE`, default on), with the bundled HTML viewer at `GET /api/trace/viewer`.

Shipped in v0.4.0 (Phase 4) and still current (the near-verbatim port of Spec Critic's requirements-research fan-out, pointed at drafting):

- **Project profile, conversationally.** As you state the project's city/state/country/client in the interview, the model records them with a `set_project_profile` operation (normalized against the ported US-state/CA-province tables, riding the same undo/save machinery as document text). A complete profile arms the research phase.
- **Grounded requirements research, on demand.** A "Research requirements" button in the panel launches four parallel streaming web-search agents — governing codes & amendments, AHJ requirements (including the water purveyor), client/insurer standards, site environment — each searching as the project's own locale, with pause-turn continuation, per-dimension search budgets, a 2× runaway ceiling, and a fetched-PDF elision guard so a 600-page code PDF can't 400 its own continuation. Research never auto-triggers: dozens of web searches are real spend, so you pull the trigger.
- **Citations or it didn't happen.** Every reported item is validated accepted-vs-cited: a URL the model cites must match one the server tools actually retrieved, or the item renders **[UNVERIFIED]** (kept as a lead, never a fact). Process/schedule facts render **[PROCESS]** and never become spec text. One dimension failing never cancels the others; partial profiles are flagged; total failure aborts clean.
- **Research → drafting, closed loop.** The profile block joins the drafting context every turn (token-capped, trimmed lowest-confidence-first; the structured profile keeps everything). Provisions drafted from a research item carry its `source_item_id` — a ◆ chip in the panel answers "why is this paragraph here?" with the requirement and its accepted sources. When a grounded item establishes the jurisdiction's adopted edition, the model records a Phase 3 `set_standard_edition` override citing the item — and the lint immediately checks the draft against it.
- **Research results persist**: the profile rides the project file; a resumed project restores it into the panel drawer and the drafting context.

Shipped in v0.3.0 (Phase 3) and still current:

- **Spec modules.** Discipline knowledge moved out of the hardcoded system prompt into frozen, registry-validated `SpecModule` objects (`backend/spec_modules/`) — section catalog, defaults-first interview playbook (every defaultable topic ships its recommended default; the non-defaultable minimum is marked *must ask*), drafting prompt slots, lint vocabulary, and dormant Phase 4 research dimensions. A bad module definition fails at startup, never mid-session. First module: `hyperscale_fire` (Div 21, USA — 21 13 13 wet-pipe lead section with the full playbook; dry-pipe, preaction, fire pumps, water service, standpipes, common-work, and clean-agent sections in the catalog).
- **Pinned standards editions.** The module pins the current published editions as drafting defaults — **NFPA 13-2025** first among them, plus NFPA 14-2024, 20-2025, 22-2023, 24-2025, 25-2026, 72-2025, 75-2024, 76-2024, 291-2025, 2001-2025, 855-2026 over IBC/IFC 2024 model-code context. Every pin carries maintainer provenance (receipts in `docs/standards_provenance.md`). When you state the jurisdiction's adopted edition ("Loudoun County is on the 2021 VCC → NFPA 13-2019"), the model records it with a `set_standard_edition` operation — adoption basis required, never silent — and the override drives the REFERENCES article, the lint, and the export from then on. Overrides ride the same transactional/undo/save machinery as document text.
- **Live linting.** Deterministic, no-API checks run on every document mutation and render in an advisory issues drawer (click to jump): standard citations that contradict the editions in effect (with a negation-suppression window so "superseded by…" prose doesn't false-flag), unresolved placeholders (`[INSERT …]`, `___`) and template markers (`TODO:`, `FIXME`, lorem ipsum), empty articles, duplicate article titles, and a heads-up when drafting proceeds with the section header unset. A standards strip under the panel shows every edition in effect, overrides highlighted with their basis.

What worked before (Phase 2) and still does:

- Claude-desktop-style UI: streaming chat pane on the left, the **live specification document** on the right, warm dark theme.
- The model drafts exclusively through the `apply_spec_edits` tool into a server-owned SectionFormat tree (Section → PART 1/2/3 → articles → nested paragraphs, automatic 1.1 / A. / 1. / a. / 1) numbering, stable element ids). Edits are validated server-side and applied transactionally; each turn's changes stream into the panel as they happen, with changed blocks highlighted.
- Per-block provenance: `confirmed` / `assumed` / `needs_input`, badged in the panel. `[TBD: …]` markers and needs-input blocks are tracked as open items — listed under the panel (click to jump) and scheduled in the export.
- Defaults-first interview: every question carries a recommended answer; "I don't know" applies a defensible NFPA 13-2025 / hyperscale-norm default stamped `assumed`; guide-me mode turns open questions into concrete options with tradeoffs.
- Version stepper: one snapshot per turn that changed the document; undo/redo from the panel header.
- `.docx` export via python-docx — SectionFormat styling plus an **assumptions schedule** (every `assumed` block with its numbering, for one-pass senior review) and an open-items schedule.
- Project save/resume: a JSON file bundling the conversation (with tool history) and the full document version history — undo still works after a resume.
- API key management: `ANTHROPIC_API_KEY` env var → OS credential manager (via `keyring`) → key file fallback, same posture as Spec Critic. A banner in the UI stores your key if none is found.
- Session reset, prompt-cached system prompt, hermetic test suite (no network, no key).

All five roadmap phases are shipped. What remains is real-world hardening: cutting the first Windows release from the runbook, growing sibling-section playbooks and modules, and tuning the import heuristics against your actual office masters.

## Architecture

```
main.py                  pywebview shell: starts the backend, opens the native window
backend/                 FastAPI + the conversation engine (Python 3.11+)
  app.py                 /api/health, /api/key, /api/session/reset, /api/chat (SSE),
                         /api/doc (+ undo/redo), /api/export/docx,
                         /api/import/master, /api/research/start|status|stream,
                         /api/audit/start|status, /api/update/check|install,
                         /api/trace/viewer, /api/project/save + load
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
  compliance/
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
    importer.py          master-.docx import: Accept-All tracked-changes text,
                         structure heuristics (labels + auto-numbering), warnings
                                                                  [ported from Spec Critic]
    linting.py           deterministic lint: stale editions, placeholders, structure
                                                                  [ported from Spec Critic]
    docx_export.py       .docx rendering + assumptions/imported/open-items
                         schedules + compliance closing section
    project.py           JSON project files: save/resume, chat transcript, module id,
                         requirements profile, audit result
  llm/
    client.py            Anthropic client factory (monkeypatch seam for tests)
    prompts.py           engine prompt protocol + module-rendered system prompt
    conversation.py      streaming turn loop with apply_spec_edits dispatch +
                         continuation rounds; lint event; research-profile splice
frontend/                Vite + React + TypeScript + Tailwind v4
  src/App.tsx            state owner: chat + document + lint + research + audit +
                         update + SSE dispatch
  src/lib/api.ts         SSE parsing over fetch; doc/undo/project/research/
                         import/audit/update calls
  src/components/        Chat, MessageBubble (markdown), Composer,
                         Header (update pill), ApiKeyBanner,
                         ArtifactPanel (stepper, export, import, open items),
                         IssuesDrawer (lint + standards strip),
                         ResearchDrawer (profile, research, audit coverage),
                         SpecDocument (SectionFormat rendering + badges + ◆ chips)
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
| `BUILD_A_SPEC_MAX_TOKENS` | `8192` | Per-turn output cap. |
| `BUILD_A_SPEC_RESEARCH_MODEL` | `claude-sonnet-5` | Model for the research fan-out. |
| `BUILD_A_SPEC_RESEARCH_MAX_TOKENS` | `8192` | Per-dimension research output cap. |
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
5. **Phase 5 — Ship (this release).** Master-spec import with gap-and-adapt (imported provenance status, Accept-All tracked-changes handling), the compliance audit of the draft against the researched profile (coverage matrix + export closing section), Windows packaging/installer with the SHA-256-verified auto-updater, and session tracing with the bundled viewer.

Build-a-Spec is an AI-assisted drafting aid, not an authority. Its output is advisory and is not a substitute for review by a licensed design professional.
