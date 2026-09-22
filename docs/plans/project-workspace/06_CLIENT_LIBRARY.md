# Phase 6 — Client library across projects (optional, D5)

**Status:** not started. **Depends on:** Phase 3 (the merge and the
provenance rules it fixes). D5 is ratified: deferred until a second project
for the same client actually exists; do not build this speculatively.

## Goal

A hyperscale client repeats across sites. The `client_standards` research
dimension, the client's standard attached as a reference document, and the
client-preference facts would be reusable across PROJECTS — but a brief is
a project, and a fact has no client scope. This phase lets a new project
for the same client start from what the last project learned about that
client, and nothing about the previous site.

## Design (to be detailed when the gate opens)

- `FACT_SCOPES` gains `client`, bound to `profile.client_name` the way
  `discipline` is bound to `project_identity.discipline` (recorded at
  `record()` from the session's profile, never from the payload; a fact
  scoped `client` with no client recorded is refused). `render_fact_lines`
  gains a "Client-wide" group rendered first; the merge (Phase 3) treats
  `client` as the widest scope.
- `ReferenceDoc` gains an optional `client_standard: bool` the panel can
  toggle; `ResearchItem` needs nothing — `dimension_id ==
  "client_standards"` is the selector.
- **Start a new project for this client** (New session → a fourth choice
  when the current session's profile has a client): seeds a fresh session
  carrying ONLY the client name, client-scoped facts, `client_standards`
  items (as a one-round profile, `section=""`), and references flagged as
  client standards. City/state/country, every other dimension, every other
  fact, the sections registry and the project id stay behind; the manifest
  card lists exactly what came and what did not. Implemented as a filter
  over `build_project_brief` → `start_from_brief` with a fresh
  `project_id`.
- No client file format: the source is a project brief or `.baspec`, read
  through the existing inspect route with a `client_only` flag.

## Tests, docs, release-note draft, deviations

To be written with the design when D5's gate opens. The record row stays
`not started` until then; Phase 7 does not wait for it.
