# DOCX fidelity corpus

This is the provenance-explicit, sanitized DOCX corpus for structural,
source-preservation, and producer-interoperability testing. It supplies
evidence for the contracts in [DOCX_FIDELITY.md](DOCX_FIDELITY.md); it does not
expand the supported edit surface by itself. The committed source of truth is:

- `tests/fixtures/docx_corpus/manifest.json` — provenance, category coverage,
  checksums, and expected source-preservation behavior.
- `tests/docx_corpus.py` — reproducible generation and materialization recipes.
- `tests/test_docx_corpus.py` — package, provenance, determinism, blocker,
  mutation, privacy, and exact-no-op assertions.
- `tests/fixtures/docx_corpus/external/` — immutable, checksum-pinned outputs
  actually saved by Word or LibreOffice from placeholder-only source material.
- `generate_word_fixtures.ps1` and `sanitize_external_fixtures.py` — the Word
  producer workflow (including the TrackedMove recipe, below) and narrow
  post-save core-properties privacy rewrite.

The repository does not check in binaries for reproducible synthetic recipes.
It does check in immutable external-producer fixtures because their
application-specific ZIP/package output is the evidence under test. To
materialize the complete resolved corpus locally:

```powershell
& '.\.venv\Scripts\python.exe' -m tests.docx_corpus .\artifacts\docx-corpus
```

The output directory receives every `.docx` plus a resolved `manifest.json`
containing byte size and SHA-256 for that generation.

## Provenance boundary

Every source document contains only placeholder project data, fixed synthetic
authors/dates, deterministic generated imagery, and `example.invalid` links.
The Word-like, LibreOffice-like, older-conversion-like, and
consultant-template-like recipes exercise metadata and package shapes; they are
never represented as real producer artifacts.

This distinction matters: changing `docProps/app.xml` or compatibility metadata
does not prove that an application can open, lay out, or resave the package.
Those profiles are useful structural coverage, not interoperability evidence.

The manifest also contains immutable `external_sanitized` cases. Such a case
uses `fixture` instead of `recipe`, records structured producer, production,
sanitization, and privacy-review details, and includes a required `sha256`. The
loader verifies that checksum and copies the fixture byte-for-byte.

Four external fixtures were produced on July 23, 2026:

- Microsoft Word `16.0.20131.20152` saved a rich DOCX and a
  consultant-template-shaped DOCX from synthetic sources.
- The same Word build saved a synthetic source as a real binary Word 97–2003
  `.doc`, reopened it, and converted it back to DOCX.
- LibreOffice `26.2.4.2` converted a sanitized source to ODT and then saved it as
  DOCX with the Office Open XML Text filter.

Word wrote the local Office user into `docProps/core.xml`. The committed Word
fixtures therefore received one documented raw-member rewrite of that part.
Every other local record, central-directory record, gap, comment, and trailing
byte remains from the Word output. The LibreOffice fixture required no
post-producer member rewrite. All four packages passed the decompressed privacy
scan plus raw ZIP-envelope surface scanning before their checksums were pinned.

## Word's own tracked moves (the TrackedMove recipe)

Redline on your original, Phase 2 writes Word's native "Moved" marks, and
settles how against a file Word itself saved with tracked moves. The producer
script has a fourth recipe for it, `TrackedMove`. It opens the synthetic
`tracked_move_source` case — a placeholder spec whose second provision is
wrapped in a bookmark — turns on Track Changes and Track Moves, cuts and
pastes two whole paragraphs (one plain, one carrying the bookmark), and saves
the result as `microsoft-word-16-tracked-move.docx`.

A tracked change records the Word user name as its author **in
`word/document.xml`**, which `sanitize_external_fixtures.py` never rewrites
(it touches `docProps/core.xml` only). So the recipe:

- sets a placeholder Office identity (`Build-a-Spec Corpus`, initials `BASC`)
  and "Always use these values regardless of sign in to Office" before it
  opens anything, and restores your own name, initials and setting in a
  `finally` block whatever happens (if a run is killed outright, check them in
  Word under File > Options > General; the next run warns if it finds the
  placeholder still set);
- checks what Word recorded **before saving anything**: every change by the
  placeholder, both cut-and-pastes recorded as moves (moved-from and moved-to
  revisions, not a deletion and an insertion), the bookmark still there;
- then scans every XML part of the saved package: no local Office or Windows
  identity (as a whole word), no user-folder path, every `w:author` and
  `w15:author` the placeholder, and no signed-in account's presence
  information in `word/people.xml`. Any finding deletes the file.

Your identity is compared and restored, and never printed. The recipe uses
the clipboard (a cut and a paste is how Word records a move), and refuses to
run while any Word window is open, like the other recipes.

Independently of the recipe, `tests/test_docx_corpus.py` checks every corpus
package against a **positive** list of placeholder identities, wherever a
producer records a person (a change's or comment's author and initials,
`word/people.xml`, the core properties): a real name fails however it is
spelled. It also pins that the recipe's text names the same paragraphs,
bookmark and placeholder as the Python side, and runs the recipe's own
package scan — lifted out of the script — under PowerShell 7 against
hand-built packages.

To produce it (Windows, Microsoft Word, every Word window closed):

```powershell
.\.venv\Scripts\python -m tests.docx_corpus .\artifacts\docx-corpus-source
powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\fixtures\docx_corpus\generate_word_fixtures.ps1 -SourceRoot .\artifacts\docx-corpus-source -OutputRoot .\artifacts\word-fixtures -Recipes TrackedMove
.\.venv\Scripts\python -m tests.fixtures.docx_corpus.sanitize_external_fixtures .\artifacts\word-fixtures\microsoft-word-16-tracked-move.docx
Copy-Item .\artifacts\word-fixtures\microsoft-word-16-tracked-move.docx .\tests\fixtures\docx_corpus\external\
```

`-Recipes` names only the fixtures to produce: every committed fixture is
pinned by its SHA-256, so producing one again changes its bytes. Run the
sanitizer as a module from the repo root, as above; run as a script it
cannot import the app. The fixture joins the manifest as
`actual_word_16_tracked_move` with its checksum pinned, expecting what any
package with pending tracked changes gets: importable as the Accept-All view,
exact-original only (`tracked_changes`), and refused by the redline on your
original (`pending_revisions`). `tests/test_docx_corpus.py` already proves a
hand-built Word-shaped sample meets exactly that entry.

## Current coverage

The manifest covers:

- Word-like rich page furniture: multipage content, default/first/even headers
  and footers, PAGE field, images, tables, custom styles, SDT, custom XML, and
  custom properties.
- LibreOffice-like and Word 2007/older-conversion-like metadata profiles.
- A sanitized consultant-template-like package.
- Portrait and landscape sections with different margins and section breaks.
- Direct custom numbering and a proven numbered structural island.
- A numbering definition relocated through valid OPC relationships.
- UTF-8 BOM/single-quoted declaration/CRLF/comment/PI lexical variation.
- A valid UTF-16 document that must remain pass-through-only with
  `unsupported_source_xml_encoding`.
- Referenced deterministic PNG media larger than two MiB.
- Archive comments and a well-formed private ZIP extra field.
- Manually wired comments OOXML in opaque appendix content.
- Manually wired footnote and endnote parts with separator notes, matched body
  references, relationships, content types, and referential-integrity checks.
- A placeholder spec with a bookmarked provision: the source of the
  TrackedMove recipe, and an ordinary ready case in its own right.
- Actual Microsoft Word package output for rich and consultant-template-shaped
  placeholder documents.
- An actual Word 97–2003 binary `.doc` round-trip and DOCX conversion.
- Actual LibreOffice ODT-to-DOCX output.

All cases must:

1. be valid OPC/DOCX packages and reopen through `python-docx`;
2. import through Build-a-Spec with a typed source map;
3. produce the blocker/mode declared in the manifest;
4. return the exact original bytes for a semantic no-op;
5. rebuild byte-identically from the same local recipe, or match the pinned
   checksum for an external fixture;
6. keep every untouched OPC member and ZIP-envelope field stable through a
   representative supported mutation; and
7. contain no local usernames, workspace paths, client data, or proprietary
   template content, and name only placeholder identities wherever a producer
   records a person.

## Adversarial complements

The manifest corpus emphasizes provenance, representative package shapes, and
repeatable renderer evidence. Smaller generated fixtures exercise malformed or
near-limit boundaries without checking hostile binaries into the repository:

- `tests/test_chunk8_opc_adversarial.py`: malformed relationship targets,
  duplicate declarations/IDs, mixed-case MIME types, strict namespaces,
  foreign revision-like names, conflict revisions, and external relationships;
- `tests/test_chunk8_raw_zip_paths.py`: overlap, duplicate central-directory
  names, normalized aliases, encoded traversal/separators, and exact retained
  no-op behavior;
- `tests/test_chunk8_lexical_adversarial.py`: comments/PIs, direct body data,
  encodings, entities, and long text nodes;
- `tests/test_chunk8_limits_history.py`: exact upload/member/package limits and
  long retained histories; and
- `tests/test_chunk8_stress_concurrency.py`: model/QC/manual/history/reset race
  barriers and atomicity.

These generated cases follow the same result taxonomy as the manifest corpus:
safe supported mutation, safe exact pass-through-only retention, or atomic
import rejection. “Import rejected” and “pass-through-only” are intentionally
different outcomes and must not be conflated.

## Deliberate gaps

The following evidence is still not available locally and is not implied by
this corpus:

- sanitized real consultant or client templates;
- real client-origin documents or proprietary consultant content;
- producer-authored notes or review comments from a privacy-reviewed project
  document;
- the broader sanitized document diversity needed for release confidence.

New real-producer samples must be separate cases with their actual
producer/version, production method, modified-part list, privacy review, and
sanitization procedure recorded; synthetic cases must not be relabeled.

Fixture content and local render traces are not privacy-safe diagnostics. Never
publish document text, raw OOXML, source packages, paths, or producer-local
metadata as telemetry. Optional aggregate diagnostics are limited to coarse
status/blocker counts as defined in [DOCX_FIDELITY.md](DOCX_FIDELITY.md).

## Safety rule

Corpus expansion may reveal that a package is unsupported. The correct result
is a precise blocker and exact-original availability. Tests or implementation
must never weaken source-preservation blockers merely to make a new sample
editable.
