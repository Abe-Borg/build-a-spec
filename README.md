# Build-a-Spec

**v0.1.0 (Phase 1)** — Conversational authoring of construction specification sections. You talk through the project with Claude; it interviews you, drafts CSI SectionFormat language incrementally, and (from Phase 2 on) builds the section live in a document panel beside the chat — the way artifacts work in the Claude app.

First target domain: **Division 21 fire suppression for hyperscale data centers (USA)**, starting with wet-pipe sprinkler systems (21 13 13) and siblings. The engine is domain-neutral; discipline knowledge will live in registry-validated spec modules, the same architecture as [Spec Critic](https://github.com/Abe-Borg/Claude-Spec-Critic)'s review modules.

Build-a-Spec is the drafting-side complement to Spec Critic: **Build-a-Spec writes specs through dialogue; Spec Critic reviews finished specs.** Large parts of this codebase are ports of Spec Critic's domain-neutral machinery (see "Relationship to Spec Critic" below).

## Current Status — Phase 1 shell

What works today:

- Claude-desktop-style UI: streaming chat pane on the left, specification document panel on the right (placeholder skeleton in this phase), warm dark theme.
- Real streaming interview loop against the Anthropic API (Sonnet 5 by default) with a Division 21 hyperscale fire-suppression system prompt. Drafted spec language arrives in fenced blocks in chat for now.
- API key management: `ANTHROPIC_API_KEY` env var → OS credential manager (via `keyring`) → key file fallback, same posture as Spec Critic. A banner in the UI stores your key if none is found.
- Session reset, prompt-cached system prompt, hermetic test suite (no network, no key).

Not yet wired (next phases, in order): the server-owned document model with `apply_spec_edits` tool-use patching into the live panel, `.docx` export, the spec-module registry with pinned standards editions, live deterministic linting ([TBD] tracking, stale-edition detection), and the AHJ/client requirements-research agents.

## Architecture

```
main.py                  pywebview shell: starts the backend, opens the native window
backend/                 FastAPI + the conversation engine (Python 3.11+)
  app.py                 /api/health, /api/key, /api/session/reset, /api/chat (SSE)
  settings.py            models, ports, env overrides
  api_key_store.py       key resolution: env -> keyring -> file   [ported from Spec Critic]
  app_paths.py           platformdirs config locations            [ported from Spec Critic]
  sessions.py            active-session store (single session in Phase 1)
  llm/
    client.py            Anthropic client factory (monkeypatch seam for tests)
    prompts.py           Phase 1 interviewer system prompt (Div 21 hyperscale)
    conversation.py      streaming turn loop; tool seam for Phase 2 document tools
frontend/                Vite + React + TypeScript + Tailwind v4
  src/App.tsx            layout: header, chat, artifact panel
  src/lib/api.ts         SSE parsing over fetch
  src/components/        Chat, MessageBubble (markdown), Composer, ArtifactPanel,
                         Header, ApiKeyBanner
tests/                   hermetic pytest suite with a fake Anthropic streaming client
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
| `BUILD_A_SPEC_PORT` | `8756` | Backend port (127.0.0.1 only). |
| `BUILD_A_SPEC_DEV` | off | Point the window at the Vite dev server. |

## Testing

Hermetic by default — no API key, no network. `tests/conftest.py` injects a placeholder key; API-touching tests monkeypatch a fake streaming client (the same convention as Spec Critic's suite).

```
.venv\Scripts\python -m pytest -q
```

## Relationship to Spec Critic

Decisions made at project start (2026-07): UI is **pywebview + React + FastAPI**; reusable Spec Critic code is **copied into this repo** (not a shared library); the first spec module is **hyperscale fire suppression, Division 21**; research agents land **immediately after** the core drafting loop is proven.

Ported so far (adapted, same design): `api_key_store.py`, `app_paths.py`, the hermetic-test fixture pattern, the model-id constants, and the prompt-cache posture. Planned ports as their phases arrive: `project_profile.py`, `code_cycles.py` (`StandardEdition` pins drive the PART 1 REFERENCES article), the `research/` requirements fan-out (the AHJ/client agents), verification retry + source grounding, the `tracing/` package, and the Windows packaging + auto-updater pipeline (PyInstaller + Inno Setup).

## Roadmap

1. **Phase 1 — Shell (this release).** Streaming interview chat, native window, key management, tests.
2. **Phase 2 — Living document.** Server-owned SectionFormat tree (Section → PART → article → paragraph) with stable element ids and per-block provenance (`confirmed` / `assumed` / `needs_input`); `apply_spec_edits` tool-use so drafts land in the panel, not chat; a defaults-first interview where "I don't know" is a valid answer — the model applies a defensible default and flags it, with assumptions badged in the panel and scheduled in the `.docx` export; change highlighting + version history; `.docx` export; save/resume project files.
3. **Phase 3 — Spec modules.** Registry-validated `SpecModule` (interview playbook, section catalog, code basis, pinned standards editions — NFPA 13-2025 default, jurisdiction-adopted editions respected); live deterministic linting of the draft.
4. **Phase 4 — Research agents.** Port of the requirements-research fan-out: grounded web-search agents for AHJ, client, and insurer requirements with citations surfaced in chat and folded into drafting context.
5. **Phase 5 — Ship.** Master-spec import as a starting point, packaging/installer/auto-updater, and a compliance audit of the finished draft against the researched requirements profile.

Build-a-Spec is an AI-assisted drafting aid, not an authority. Its output is advisory and is not a substitute for review by a licensed design professional.
