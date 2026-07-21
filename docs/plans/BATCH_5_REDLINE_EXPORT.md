## As built (2026-07-21)

**Status: shipped as v1.0.0.** All three work items landed; 221 backend tests
green (+26 new), frontend builds clean, version gate at 1.0.0.

What shipped, per the plan:

- **WI1 — diff engine (`backend/spec_doc/diffing.py`).** Pure
  `diff_sections(base, cur) -> SectionDiff`: uid join
  (unchanged/changed/inserted/deleted, deleted spliced at base position, moves
  unmarked), word-level `token_runs` (`re.findall(r'\S+\s*')` +
  `SequenceMatcher(autojunk=False)`, byte-exact reconstruction of both texts),
  `status_changes` for status-only deltas, `stats`. `SectionDiff`/`ElementDiff`/
  `DiffRun`/`StatusChange` dataclasses with `to_dict`. `DocumentStore.baseline_index`
  set by `adopt_imported`, cleared on truncation + reset, persisted through
  `to_dict`/`load` (old files tolerate absence → None).
- **WI2 — tracked-changes writer.** `build_docx(..., redline=SectionDiff,
  redline_date=None)` builds `w:ins`/`w:del`/`w:delText` + paragraph-mark
  ins/del with `docx.oxml`; sequential-unique `w:id`, author = `settings.APP_NAME`,
  ISO-8601 `w:date`. Clean body extracted to `_render_clean_body`, byte-identical
  to v0.9.0 (pinned). `GET /api/export/docx?redline=master|version&base=N`
  (400 no-baseline; ` - REDLINE` filename). The killer round-trip invariant is a
  test: Accept-All(redline) == clean(cur), Reject-All(redline) == clean(base).
- **WI3 — in-app diff view.** `GET /api/doc/diff?base=N[&cur=M]`; `types.ts`
  mirror; `ArtifactPanel` Compare toggle + base picker (Master pinned) + stat
  line + export menu; `SpecDocument` read-only diff render via a `diff` prop;
  diff CSS in `index.css`.

Deviations (each with why):

1. **Round-trip assertion compares resolved views, not raw text.** The plan's
   "reproduce the CURRENT document text exactly" is asserted as
   Accept-All(redline) == a clean export of cur (and Reject-All == clean base),
   because the clean export's `(Not used.)` line for empty parts is pre-existing
   behavior — comparing resolved Accept/Reject views is the honest invariant.
   Both the real `parse_master_docx` importer and a custom reject-all reader are
   exercised.
2. **Compare view lives inside `SpecDocument.tsx`** as a `DiffDocument`
   subcomponent gated by a `diff` prop (not a mutation of the editable
   renderer) — literally "SpecDocument renders diff mode", kept read-only.
3. **No vitest toolchain added.** The diff contract is pinned by the Python
   suite (`test_diffing.py`), and the frontend consumes the identical
   serialization, so compare-vs-export parity is inherent (matching the plan's
   steer for the review-queue contract in Batch 3).
4. **`baseline_index` in `_doc_payload`.** Added so the compare picker knows a
   master exists without first fetching a diff (the plan implied it; made explicit).

Manual QA still required before release (see the checklist handed back at
batch close): open a redline in **real Word** — reviewing pane shows
"Build-a-Spec" as author; Accept All yields the current doc; Reject All yields
the master; word-level edits read cleanly; deleted paragraphs collapse on
accept.

---

# Batch 5 — Redline export (real tracked changes) + in-app version diff

Ships as **v1.0.0** — the release milestone, because this is the single
most impressive artifact the app can hand to other people: a `.docx`
with genuine Word tracked changes (`w:ins`/`w:del`) showing exactly what
Build-a-Spec did to the office master. Spec reviewers live on redlines.
One diff engine powers both the export and an in-app version-compare
view.

Dependencies: none hard beyond v0.6.0 (stable element ids are the
enabling design), but sequence after Batch 4 per the roadmap.

---

## Why this is tractable here

Two v0.5/0.6 decisions make redlining almost easy:

1. **Stable element ids, never reused.** Every paragraph/article carries
   a uid minted once (`spec_doc/model.py`, monotonic per-parent
   counters; "a new edit after undo truncates the redo tail, so ids
   can't collide with an abandoned future"). Baseline element ↔ current
   element alignment is an id join, not a fuzzy match.
2. **The importer resolves masters to the Accept-All view** and
   `DocumentStore.adopt_imported` lands the import as one version. The
   baseline for "redline vs master" is simply that version's tree —
   already in `DocumentStore.versions` and already persisted in project
   files (undo survives resume, therefore so does the baseline).

---

## Work item 1 — The diff engine (`backend/spec_doc/diffing.py`)

Pure, deterministic, no model, no I/O. Two layers:

### Element alignment

`diff_sections(base: SpecSection, cur: SpecSection) -> SectionDiff`

Walk both trees; join elements by uid:
- uid in both → `changed` if normalized text differs (or
  article/section title differs), else `unchanged`. Status changes
  alone (e.g. assumed→confirmed) are NOT content changes — record them
  separately (`status_changes`) for the in-app view; they do not
  produce redline marks (Word redlines track text, and reviewers don't
  care about our provenance mechanics).
- uid only in cur → `inserted` (whole element).
- uid only in base → `deleted` (whole element), positioned in output at
  its base-order location relative to surviving siblings.
- Same uid, different position/parent → treat as `unchanged`/`changed`
  by text only. Decision (frozen): pure moves are NOT marked — display
  numbering is positional and recomputes anyway; marking moves as
  delete+insert would drown reviewers in noise for zero information.
  Document this in the module docstring; revisit only if a reviewer
  asks.
- The section header (number/title) diffs like any element.

### Text runs

For `changed` elements: token-level diff of base vs current text —
tokenize on whitespace KEEPING the whitespace attached
(`re.findall(r'\S+\s*')`), run `difflib.SequenceMatcher(None, a, b,
autojunk=False)`, map opcodes to runs
`[{op: "equal"|"ins"|"del", text}]`; merge adjacent same-op runs.
Word-level (not char-level) is the deliberate grain: char diffs
produce unreadable confetti in legal-style review.

```python
@dataclass
class ElementDiff: uid, kind, ref_base, ref_cur, runs | None,
                   base_text, cur_text
@dataclass
class SectionDiff: elements: list[ElementDiff]   # interleaved doc order
                   status_changes: list[...]
                   stats: {inserted, deleted, changed, unchanged}
```

`diff_sections` must accept ANY two versions (the engine does not know
about "the master") — versus-master is just `base = versions[baseline_
index]`.

### Baseline bookkeeping

`DocumentStore` gains `baseline_index: int | None` — set to the
post-import version index by `adopt_imported`; `None` for from-scratch
projects (UI then offers only version-to-version compare, plus
"vs empty" which is a valid all-insertions redline for a from-scratch
issue). Persist in the project file (`spec_doc/project.py`; tolerate
absence in old files → `None`). Reset clears it.

---

## Work item 2 — Tracked-changes `.docx` writer

Extend `spec_doc/docx_export.py` with `build_docx(..., redline:
SectionDiff | None = None)`. When `redline` is provided, body paragraphs
render from the diff instead of the plain tree:

- `unchanged` → normal runs (existing rendering path).
- `changed` → one paragraph; each diff run becomes a `w:r`: `ins` runs
  wrapped in `<w:ins w:id="N" w:author="Build-a-Spec"
  w:date="ISO8601">…</w:ins>`; `del` runs wrapped in `w:del` with the
  text in `<w:delText xml:space="preserve">` (NOT `w:t`) inside the
  run. Sequential unique `w:id` across the document.
- `inserted` elements → whole paragraph's runs inside `w:ins`; the
  paragraph MARK is also flagged inserted: `w:pPr/w:rPr/w:ins`.
- `deleted` elements → paragraph emitted at its base position, all runs
  in `w:del` with `w:delText`, paragraph mark flagged
  `w:pPr/w:rPr/w:del` (this is what makes Word treat the whole
  paragraph as deleted and collapse it on accept).
- Schedules (assumptions / imported / open items / compliance-or-QC
  closing) render normally, never redlined.

Implementation notes: python-docx has no tracked-changes API — build
the `w:ins`/`w:del` elements with `docx.oxml` (`OxmlElement`,
`qn("w:ins")` etc.); the importer and its tests already manufacture
`w:ins`/`w:del`/`w:delText` XML (`tests/test_importer.py`,
`test_tracked_changes_import_accept_all_view`) — mirror those shapes.
`VERIFY:` against ECMA-376 (and by opening in real Word): the
`w:date` format, that `w:author` renders in the reviewing pane, and the
paragraph-mark ins/del placement. `settings.APP_NAME` is the author
string.

**The killer invariant (make it a test):** importing the redlined
export back through `parse_master_docx` (which resolves to Accept-All)
must reproduce the CURRENT document text exactly; and a
"Reject-All" reading (keep `w:del` text, drop `w:ins`) must reproduce
the BASELINE text. The first is directly testable with existing
importer machinery; implement a tiny reject-all text extractor in the
test to cover the second.

### API

Extend `GET /api/export/docx` with query params `?redline=master` |
`?redline=version&base=N` (default absent → clean export, unchanged
behavior). 400 when `redline=master` and `baseline_index is None`
("This project has no imported master — choose a version to compare
against."). Filename gains ` - REDLINE` suffix.

---

## Work item 3 — In-app diff view

- `GET /api/doc/diff?base=N[&cur=M]` (cur defaults to current index) →
  serialized `SectionDiff`. Validation: indexes in range, base ≠ cur.
- Frontend: the version stepper in `ArtifactPanel.tsx` gains a
  "Compare" toggle. Compare mode opens a base picker — "Master
  (import)" pinned first when `baseline_index` exists, else version
  list — and `SpecDocument` renders diff mode: `ins` runs
  green/underline, `del` runs red/strikethrough (theme-consistent
  muted tones, `index.css`), inserted/deleted whole blocks with a left
  border + badge; status-change chips listed in a footer strip;
  stats line ("+12 provisions, −3, 9 edited") in the panel header.
  Read-only while in compare mode (editing affordances from Batch 2
  hidden); exit restores normal view. `types.ts` mirrors `SectionDiff`.
- Export menu (small dropdown replacing the single Export button):
  "Export clean", "Export redline vs master" (hidden without a
  baseline), "Export redline vs version…" (uses the compare picker's
  selection when in compare mode).

---

## Tests

- `diffing.py` unit coverage: identical trees → all unchanged, zero
  marks; pure insert; pure delete; text edit produces minimal
  word-level runs with whitespace preserved byte-exactly
  (`"".join(run.text for run in runs if op != "del") == cur_text` and
  the del-complement equals base_text — make both invariants explicit
  assertions in every case); nested paragraph insert/delete; changed
  article title; moved paragraph produces no marks; status-only change
  lands in `status_changes` not `elements`.
- Round-trip invariant (see above): master fixture → adapt via
  scripted ops → redline export → re-import Accept-All == current;
  reject-all extraction == baseline.
- XML assertions: exported bytes parsed with lxml — every `w:ins`/
  `w:del` has author/date/unique id; deleted paragraphs carry the
  paragraph-mark `w:del`; no `w:t` inside `w:del` runs (must be
  `w:delText`).
- API: param validation, no-baseline 400, clean export byte-identical
  to pre-batch behavior (regression guard on the default path).
- Project round-trip: `baseline_index` survives save/load; old project
  files without it load with `None`.
- Manual QA: open a redline in real Word — reviewing pane shows
  "Build-a-Spec" as author; Accept All yields the current doc;
  Reject All yields the master. This manual check is mandatory before
  release (note it in the PR).

## Docs & version

README v1.0.0 (lead with the redline story + a screenshot), CLAUDE.md
implemented notes + layout entries (`diffing.py`), config table
unchanged, version gate at 1.0.0. Cut the first Windows release per
`docs/RELEASE_WINDOWS.md` after this batch lands — that runbook is
already written.

## Acceptance criteria

1. Import a real office master, adapt it through the interview, export
   redline-vs-master, open in Word: a reviewer sees exactly the
   adaptation as tracked changes, Accept All == the issued text,
   Reject All == the master.
2. From-scratch projects can redline against any prior version or
   empty.
3. Compare mode in-app matches the exported redline run-for-run.
4. The clean export path is byte-stable vs v0.9.0 (no regression).
5. Suite green, build clean, docs current, version gate at 1.0.0.
