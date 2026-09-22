# Phase 2 — The project has a home: a project folder and a Project panel

**Status:** not started. **Depends on:** Phase 1 (complete, v1.20.0). D1 is
ratified: a project is a folder.

## Goal

A project lives in one folder: `<Project>.basproject` beside one `.baspec`
per section. The app discovers the folder from any of its files, shows the
project's sections in a panel, and lets the user open one or start the next
from that list — the "swap in a section" the ask names — without a file
dialog. Desktop shell only; a browser/dev session (no shell bridge) keeps
today's Open / New-section file relay and shows no panel.

## Current code (what this phase builds on — verified 2026-09-22)

- `SessionState.save_target: str` (`backend/llm/conversation.py`) is the
  precedent for a **local-only, never-persisted path** on the session:
  cleared by `_reset_while_locked` and by `load_project`, written only by
  `sessions.remember_project_save_target(session, path, generation=)`,
  projected to the frontend by `sessions.project_save_target(session)` as
  `{"path", "name"}` on `_doc_payload["project_save_target"]`.
- The native shell (`main.py`, `_CloseController`) is the only thing that
  knows paths: `_save_project_file` writes the `.baspec` and binds the target
  through `_bind_save_target`; `open_file(kind)` shows the Open dialog and
  returns `{"name", "data_b64"}` — the frontend uploads those bytes to
  `POST /api/project/load-file`, which stages on a worker thread
  (`_stage_project_load`) and commits under one guard (`_commit_load`).
  `_OPEN_FILE_TYPES_BY_KIND` maps a `kind` to a dialog filter.
- `session.project_link` (persisted, sanitized by
  `spec_doc.project.sanitize_project_link` against `_PROJECT_LINK_KEYS`)
  carries `project_id`, `name`, `brief_updated_at`, `seeded_from`,
  `research_rounds_at_seed`, `sections[]` (each `sanitize_section_record`:
  number, title, module_id, discipline, article_titles, ready, exported_at,
  `file_name` — a BASENAME, fact_count, research_rounds).
- `project_brief.parse_brief_json` / `parse_project_brief` read a
  `.basproject`; `brief.project_id` is the join key. `build_project_brief`
  and `_build_brief_locked` (app.py) are the writers; Phase 1's
  `sections_drafted(session)` reads the registry.
- `ProjectFactsPanel.tsx` is the panel idiom (collapsible, `openNonce`,
  renders only when it has something to show); `ArtifactPanel.tsx` mounts it
  and passes `projectLink`. `App.tsx` owns the save gate
  (`saveGate` union + `runGate`) and `doLoadProject(file)`.
- `tests/test_session_wipe.py` requires every `SessionState` field to be
  declared wiped (`_STATE_PROBES`) or kept (`_RESET_KEEPS`).
- The tutorial's `structural` scenario (`backend/tutorial.py`,
  `structural_practice_copy`) seeds the fixtures the "Work directly on the
  paper" chapter points at; a step's anchor must exist as a `data-tour`
  attribute in production UI (`frontend/tests/tour.test.ts`).

## Design

### 2.1 `SessionState.project_home` — local-only, discovered, never persisted

```python
# backend/llm/conversation.py, beside save_target
project_home: dict[str, str] | None = None
# {"folder": <abs dir>, "brief_path": <abs file>, "brief_name": <basename>,
#  "project_id": <32 hex>}
```

- Cleared wherever `save_target` is cleared (`_reset_while_locked`,
  `load_project`). Declared in `tests/test_session_wipe.py::_STATE_PROBES`.
- Never in `project_payload`, never in a `.baspec`, never in a brief. The
  brief's own `sections[].file_name` (basename) is the only file reference
  that persists, and it was already there.
- Set by ONE function, `sessions.discover_project_home(session, anchor_path)`
  (new, in `backend/sessions.py`): given the absolute path of a `.baspec`
  that was just saved or opened, list `*.basproject` in `dirname(anchor)`
  (no recursion, no symlinks — `Path.is_symlink()` refused, the retention
  module's posture), parse each with `parse_brief_json` (size-capped) and
  keep the FIRST whose `project_id` equals `session.project_link["project_id"]`.
  No link on the session → no home (a section that never exported or seeded
  belongs to no project yet). No match → `None`. Any parse error on one
  candidate is skipped, never raised. Pure disk read; call it OUTSIDE the
  session guard and assign under it, generation-checked exactly like
  `remember_project_save_target`.
- Two call sites, both in the shell (`main.py`): at the end of a successful
  `_save_project_file` (after `_bind_save_target`), and in a new js_api
  method `bind_project_home(token)` (2.2). Both run on the js_api thread, not
  the UI thread, so a folder listing there is fine.

### 2.2 The shell resolves paths; the frontend asks by token or by name

- `open_file(kind)` gains a `token` in its return value:
  `{"name", "data_b64", "token"}` where `token` is an opaque `uuid4().hex`
  mapped to the chosen path in `_CloseController._recent_opens` (bounded to
  the last 8, oldest evicted). Existing callers ignore the extra key.
- New js_api `bind_project_home(token) -> {"ok", "home": {folder, brief_name}
  | None}`: resolves the token (unknown → `ok: False`), runs
  `discover_project_home`, and assigns under `session_state_guard()` with the
  generation sampled BEFORE the dialog (the same guard that makes a save
  target refuse a replacement session). The frontend calls it right after a
  successful `/api/project/load-file` when the file came from `open_file`.
- `POST /api/project/open-section {number}` (new, `app.py`): the server
  resolves `number` → `record["file_name"]` from the registry (link first,
  then the on-disk brief read from `project_home["brief_path"]`), builds
  `path = os.path.join(home["folder"], file_name)` and REFUSES anything whose
  `os.path.realpath` is not inside `realpath(folder)` (404
  `section_file_missing` when absent, 400 `outside_project_folder` when it
  escapes — a registry entry is data from a file people share). It then
  reads the bytes and runs the exact `load-file` path: extract the body of
  `project_load_file` into `_load_project_bytes(entry_lease, payload,
  *, trace_mode)` so both routes share staging, the commit guard, the
  generation check and the `_doc_payload` offload. After the commit, the
  home carries over (same folder, same project) — re-assign it under the
  same guard since `load_project` clears it. Refusals: tutorial 409, busy
  409 (`busy_reasons`), no home 409 `no_project_home`.
- `POST /api/project/next-section` (Phase 1) carries the home across the
  seed for the same reason: `start_from_brief` resets the session; the route
  re-assigns `project_home` after the seed, inside the same guard.
- `GET /api/project/sections` (new, read-only): `{ok, home: {folder,
  brief_name} | null, project: {project_id, name} | null, current_number,
  sections: [{...record, present: bool, is_current: bool}], unregistered:
  [basename...]}` — the registry (link ∪ on-disk brief, link wins per number
  because it is newer) joined with `os.path.exists` per `file_name`, plus
  `.baspec` files in the folder that no record names (sections saved before
  the project existed, or saved under a new name). Reads the brief file on a
  worker thread (`run_in_threadpool`) if the route is `async`; simplest is a
  plain `def` (already a worker thread) doing the small JSON read inline.
  Never under the session guard for the disk read; the link snapshot is
  taken under it.
- `_doc_payload` gains `project_home: {folder, brief_name} | null`, the
  `project_save_target` precedent (the panel's header line and the "Update
  project brief" affordance Phase 3 adds both read it).

### 2.3 The Project panel

`frontend/src/components/ProjectPanel.tsx`, mounted by `ArtifactPanel`
directly above `ProjectFactsPanel`:

- Renders when `projectLink` is non-null OR `projectHome` is non-null;
  otherwise nothing (the `ProjectFactsPanel` rule).
- Header: project name, then where it lives (`home.folder`, the
  `saveTarget.path` precedent for showing an absolute path the shell owns),
  or "Not in a project folder — save this section beside its project brief
  to see its sections here" when linked but homeless.
- Rows from `GET /api/project/sections`: number, title, `ready` ✓, exported
  date, facts / rounds recorded, and one of: **current** (highlighted, no
  button), **Open** (present), "file not found beside the brief" (absent,
  no button). Unregistered `.baspec` files list under a "Not in the
  registry" line with no action (opening them is the ordinary Open button).
- **Next section →** at the bottom reuses Phase 1's dialog (`onNextSection`
  prop from `ArtifactPanel`'s existing state).
- **Open** → `App.requestOpenSection(number)`: save gate (`saveGate` kind
  `open-section`, the `open-project` copy), then `openSection(number)` →
  `applySessionBundle`, `discardPaneState`, the `doLoadProject` idiom for
  notices. Disabled while `busy || fileLoading`. Hidden in a tour
  (`tutorialActive`).
- Refetch on: mount, `project_link`/`project_home` change in the doc
  payload, a successful save (`onSaveProject` resolves), and a panel
  `openNonce`. No polling.
- Capabilities: `project.sections` (the panel root), `project.open-section`
  (the Open button). One new tour step in the "Work directly on the paper"
  chapter right after `project-facts`, anchor `project-panel`, mode
  `explanatory`. **The structural practice copy must seed a
  `project_link`** (two registry records, one of them the practice section)
  so the panel is on screen in the tour; bundled and deterministic, no home
  (the tour never touches disk). `TOUR_VERSION` 7 → 8 (a step added
  mid-chapter).

### 2.4 What Phase 2 deliberately does not do

- It does not write the brief. The on-disk `.basproject` is read, never
  modified, until Phase 3's merge exists. The panel's header says "Brief
  last updated <date>" from the file so staleness is visible.
- It does not watch the folder. A refetch happens on the events above; a
  file added by hand shows on the next one.
- No project-level "New project" flow: a project is born the first time a
  section exports a brief (Phase 1's Next-section does this implicitly).

## Files

- `backend/llm/conversation.py` — `project_home` field; clear sites.
- `backend/sessions.py` — `discover_project_home`, `project_home_payload`.
- `backend/app.py` — `_load_project_bytes` extraction; `GET
  /api/project/sections`; `POST /api/project/open-section`;
  `project_home` on `_doc_payload`; the home carry-over in
  `next-section`.
- `main.py` — `open_file` token; `bind_project_home`; discovery after save.
- `backend/tutorial.py` — `structural_practice_copy` seeds a link.
- `frontend/src/components/ProjectPanel.tsx` (new), `ArtifactPanel.tsx`,
  `App.tsx` (gate kind, `requestOpenSection`, `bind_project_home` call after
  a native open), `lib/api.ts` (`projectSections`, `openSection`),
  `types.ts` (`ProjectHome`, `ProjectSectionsPayload`), `capabilities.ts`,
  `tour.ts`, `HelpModal.tsx` (the next-section recipe gains the panel),
  `TrustDeepDiveModal.tsx` (the brief card: the folder is read locally).

## Tests

`tests/test_project_home.py` (new):

- `test_discovery_needs_a_link_and_a_matching_brief` — no link → None; a
  brief with another id → None; the matching one wins; a symlinked
  candidate is skipped; a malformed candidate is skipped and the next one
  still matches (tmp_path fixtures, real files).
- `test_a_home_is_never_persisted` — save the project (`project_package`),
  reload it into a fresh session: `project_home is None`; the brief bytes
  never contain the folder.
- `test_the_sections_route_joins_registry_and_folder` — two records, one
  file present, one absent, one unregistered `.baspec`; `present` flags and
  `unregistered` list; link wins over the disk brief per number.
- `test_open_section_runs_the_real_load_path` — end to end: home set, open
  by number, the loaded session equals `load-file` on the same bytes (state
  projection, the Phase 1 idiom), the home survives the load.
- `test_open_section_refuses_escapes_and_absences` — `file_name` of
  `../other.baspec` → 400; a missing file → 404; no home → 409; a tour →
  409; busy → 409; nothing replaced on every refusal.
- `test_next_section_keeps_the_home` — seed with a home set; the new
  session still has it.
- `test_session_wipe` sweep — `project_home` declared in `_STATE_PROBES`.
- `tests/test_close_prompt.py` — `bind_project_home` with an unknown token
  → `ok: False`; a stale generation → no assignment (the
  `remember_project_save_target` test's shape); `open_file` returns a token
  and the map is bounded.
- `tests/test_tutorial.py` — the structural copy carries a link with two
  records and no home.

Frontend: `frontend/tests/projectPanel.test.ts` (source-level, the
`nextSection.test.ts` idiom) — Open runs the save gate before `openSection`;
the gate resumes `open-section`; the panel is hidden in a tour and declares
both capabilities; `tour.test.ts`'s anchor and capability contracts.

## Docs

- `README.md`: a subsection under "## Project workspace (in progress)"
  (create the heading if Phase 2 lands first) — the folder convention, what
  the panel shows, what the browser does instead.
- `CLAUDE.md`: an implemented-notes section ("The project has a home")
  before "Source-of-truth pointers"; Layout entries for `sessions.py`,
  `app.py`, `main.py`, the new component and test file.
- `docs/RELEASE_WINDOWS.md`: QA rows — save beside a brief and see the
  panel; open a sibling by number; a moved folder still resolves; a
  browser session shows no panel.

## Release-note draft (Phase 7 copies from here)

- **Your project has a home.** Keep a project's brief and its section files
  in one folder and the app finds them: a Project panel lists every section
  the project has drafted, marks the one you are in, and opens any other in
  one click — offering to save first. Start the next section from the same
  panel. Nothing about the folder is written into any file, so the folder
  can move or be shared whole.

## Deviations from the plan

(Record each as-built deviation here, dated, with the reason.)
