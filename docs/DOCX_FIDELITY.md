# DOCX fidelity and compatibility

This document is the canonical contract for imported DOCX preservation. It
covers the product boundary, export modes, API fields, blocker codes,
persistence compatibility, diagnostics, and fixture expectations.

## Product boundary

Build-a-Spec is a construction-specification authoring tool, not a general
Word editor. Import creates two deliberately separate artifacts:

1. the exact validated DOCX bytes, retained as an immutable recovery source;
2. a normalized SectionFormat tree containing only supported semantic body
   content.

The source package remains authoritative for source-preserving export. The
semantic tree remains authoritative for editing, normalized export, compare,
and normalized redline. Extraction does not make headers, footers, tables,
fields, hyperlinks, drawings, content controls, styles, page layout, or
arbitrary OOXML editable.

The governing invariant is fail-closed: ambiguity may remove a body-editing
capability, but it must never create one. Build-a-Spec never silently converts
a failed source-preserving export into a normalized document.

An upload that fails the initial bounded ZIP/OPC safety checks is rejected
atomically and is not retained. A package that is safe enough to retain and
extract, but whose encoding, revisions, relationships, or raw ZIP layout
cannot be mutated safely, is retained as pass-through-only.

Full Strict OOXML semantic import is not supported. Package-level inspection
recognizes Strict relationship and Word namespaces so safety and revision
scans do not mistake them for malformed Transitional markup, but a fully
Strict `word/document.xml` main part is rejected atomically before any source
state is retained. It is not converted to Transitional OOXML. Supporting that
semantic dialect is a future compatibility feature, not part of the bounded
source-patching surface.

## Five distinct user-visible contracts

| Contract | API selection | Package basis | Guarantee |
|---|---|---|---|
| Exact original | `GET /api/import/original` | Immutable imported bytes | The response is byte-for-byte identical to the retained upload. |
| Exact source no-op | `GET /api/export/docx?mode=source` when the semantic body matches the imported baseline | Immutable imported bytes | Returns the exact same bytes, without rebuilding XML or ZIP. Status, provenance, standards, project-profile, and other metadata-only changes do not make this a body mutation. |
| Source-preserving patched DOCX | `GET /api/export/docx?mode=source` after a proven-safe body change | Clone of the imported package | Only approved `word/document.xml` text slices or numbered-island paragraph spans change. Unchanged member payloads, local records, inter-record gaps, archive comment, and trailing bytes remain exact. Central-directory records change only for the replacement metadata and required local-header offsets. The proposed output is independently audited before return. |
| Normalized DOCX | `GET /api/export/docx?mode=normalized` | Current SectionFormat tree | Generates a new DOCX with Build-a-Spec styles, schedules, and genuine Word automatic numbering. It does not preserve source-package formatting or opaque parts. Fresh projects default to this mode. |
| Normalized redline | `GET /api/export/docx?redline=master` or `GET /api/export/docx?redline=version&base=N` | Semantic baseline/version and current SectionFormat tree | Generates a new DOCX containing Word `w:ins`/`w:del` markup. It is a semantic provision redline, not a source-package redline. It never adds tracked changes to the retained source. |

Redline display labels remain positional literal text so a move or a preceding
deletion does not create misleading tracked-numbering noise. Accept-All is
text-faithful to the current semantic document, and Reject-All is text-faithful
to the selected semantic baseline. Clean normalized exports, in contrast, use
genuine Word numbering definitions and `w:numPr` bindings.

`pass_through_only` is a document state, not a sixth export format. In that
state exact-original download and exact source no-op remain available, while
source-backed body mutations are disabled. Status, research provenance,
standards, project-profile, and other workspace metadata may remain editable
because they do not alter the retained Word body. A normalized export remains
an explicit, separate reconstruction choice and must not be described as
preserving the source.

## Source-backed edit surface

Source-preserving text replacement is allowed only when one semantic provision
maps to one exact, contiguous source text slice in one ordinary Word text node.
The surrounding paragraph and run markup remains untouched. Tabs, line breaks,
illegal XML characters, unsupported `xml:space` transitions, CDATA, embedded
lexical markup, complex runs, and ambiguous anchors fail closed.

Add, delete, and reorder are allowed only inside a proven structural island:

- direct children of one Word body and one semantic article;
- flat leaf paragraphs, contiguous in the source body;
- at least two source members;
- one genuine direct Word-numbering definition and level;
- an isolated `numId` not used outside the island;
- no opaque barrier, nested content, section break, or manually rendered label;
- for insertion, a surviving source anchor and one unambiguous, allow-listed
  `w:pPr`/`w:rPr` template.

Existing paragraphs move as complete raw OOXML elements. A new paragraph is a
minimal element derived only from the proven local template. Structural edits
never cross a parent, island, table, field, hyperlink, content control, or
other opaque boundary.

Every manual, model, and QC body mutation is checked as a proposed final state
before commit. Undo and redo restore snapshots that passed that gate when they
were created. Native project load independently revalidates every retained
source-backed history version against the exact attached source before the
live session is replaced. Capability reports are UI guidance, not
authorization; forged or stale requests still pass through the final-state
gate.

## API contract

### Document payload

`GET /api/doc` and successful import, edit, undo, redo, project-load, and QC
apply responses include these source-specific fields:

```json
{
  "source_available": true,
  "preservation_ready": true,
  "source_preservation": {
    "status": "ready",
    "source_export_ready": true,
    "exact_original_available": true,
    "body_editing": "bounded",
    "no_op": true,
    "changed_uids": [],
    "blockers": []
  },
  "source_capabilities": {
    "status": "ready",
    "elements": {
      "pt1.a1.p1": {
        "replace_text": {"allowed": true},
        "move": {
          "allowed": true,
          "island_key": "pt1.a1.p1",
          "current_position": 0,
          "minimum_position": 1,
          "maximum_position": 2,
          "allowed_positions": [1, 2]
        }
      }
    }
  }
}
```

Fresh projects return `null` for `source_preservation` and
`source_capabilities`. A resumed legacy import has no source capability lock:
`source_capabilities` is `null`, while `source_preservation` reports
`unavailable` because its historical import report/baseline exists without
source bytes. The legacy `preservation_ready` boolean is retained for
compatibility. It means the *current state* can be returned through source
mode; it does not mean body editing is allowed. In particular, a
pass-through-only no-op reports `preservation_ready: true` and
`body_editing: "disabled"`.

`source_preservation.status` has four values:

| Status | Meaning |
|---|---|
| `ready` | Source export is currently valid and a bounded body-editing surface may exist. Consult per-operation capabilities. |
| `pass_through_only` | Exact source no-op is valid, but source-backed body editing is disabled by a package-wide or runtime mutation blocker. |
| `blocked` | Source bytes exist, but the current semantic/source state cannot be exported through source mode. |
| `unavailable` | The session represents an import but no exact source artifact is available, as with a resumed legacy JSON project. |

`source_export_ready` answers whether the current state can be downloaded in
source mode. `exact_original_available` answers whether retained source bytes
exist. `no_op` is based only on the semantic Word-body projection; workspace
metadata does not change it. `changed_uids` identifies semantic body elements
in the approved patch plan. Each blocker has `uid`, stable `blocker` code, and
canonical server `message`.

### Per-operation capabilities

`source_capabilities` is `null` outside an active imported-source scope.
Otherwise its `status` is independent of `source_preservation.status` and has
three values:

| Status | Meaning |
|---|---|
| `ready` | Source capability analysis completed without a package-wide mutation blocker. Individual body operations may still be denied; consult each operation record. |
| `pass_through_only` | Exact source no-op can remain available, but a package-wide or runtime blocker denies source-body mutations. |
| `blocked` | Required source identity, mapping, baseline, or capability analysis is unavailable or invalid. Body operations fail closed. |

`source_capabilities.elements[uid][operation]` is server-derived and has this
shape:

```json
{
  "allowed": false,
  "blocker": "complex_paragraph_markup",
  "message": "the source paragraph contains unsupported paragraph-level markup"
}
```

Allowed structural operations may also carry `island_key`,
`current_position`, exact `allowed_positions`, and one or more `placements`.
A placement contains an `island_key` and exact sibling positions; contiguous
positions additionally expose `minimum_position` and `maximum_position` for
compatibility. Consumers must use the exact positions and must not infer safe
gaps from a min/max range.

Capability operations include `replace_text`, `add_paragraph`, `delete`, and
`move` for body elements where semantically relevant. Paragraph status and
provenance operations and section-level project/standards metadata can remain
allowed when body operations are blocked.

### Import, export, and project endpoints

| Endpoint | Contract |
|---|---|
| `POST /api/import/master` | Bounded, atomic DOCX import. On success, returns import counts/warnings plus the full document payload. A failed import leaves the live session unchanged. |
| `GET /api/import/original` | Exact retained source with `Cache-Control: no-store`. Returns 409 for a source-less resumed legacy import and 404 when no import exists. |
| `GET /api/export/docx` | Imported projects default to `mode=source`; fresh projects default to normalized. It never silently falls back from source mode. |
| `GET /api/export/docx?mode=source` | Exact no-op or audited source patch. A blocked request returns 409. It cannot be combined with `redline`. |
| `GET /api/export/docx?mode=normalized` | Explicit normalized reconstruction. |
| `GET /api/export/docx?redline=master` | Normalized redline against the imported semantic baseline. Requires an imported baseline. |
| `GET /api/export/docx?redline=version&base=N` | Normalized redline against a retained semantic version. |
| `GET /api/project/save` | Native `.baspec` package. An imported project includes exact source bytes and a typed source map. |
| `POST /api/project/load` | Legacy format-1 JSON compatibility endpoint. The loaded project is source-less. |
| `POST /api/project/load-file` | Loads native `.baspec` or current legacy JSON after complete side-effect-free validation, then replaces session state atomically. |

Source-preserving failures include a bracketed code in their human-readable
error, for example `...[complex_paragraph_markup]: ...`. API clients should use
`source_preservation.blockers` and `source_capabilities` for decisions rather
than parsing error prose.

### Bounded package limits

The import boundary accepts values at the stated ceiling and rejects the next
byte/member:

| Boundary | Current ceiling |
|---|---:|
| Compressed DOCX upload | 25 MiB |
| DOCX members | 5,000 |
| One uncompressed DOCX member | 100 MiB |
| Total uncompressed DOCX members | 250 MiB |
| Compressed `.baspec` upload | 96 MiB |
| `.baspec` members | 3 fixed members |
| `.baspec` manifest | 64 KiB |
| `.baspec` semantic project JSON | 64 MiB |
| Total uncompressed `.baspec` members | 96 MiB |

These limits bound compressed uploads, decompression, member count, and project
history independently. Unsafe paths, encryption, duplicate fixed project
members, undeclared project members, and required OPC parts that cannot be read
are hard import errors, not pass-through-only states.

## Blocker codes

The canonical user message table lives in
`backend/spec_doc/source_mapping.py:source_blocker_message`. Do not create an
independent policy or message table in the frontend or API layer. The code is
the stable machine value; the accompanying message is the server-owned user
explanation.

### Package-wide and runtime mutation blockers

| Code | Meaning |
|---|---|
| `active_content` | Macros, ActiveX, OLE, or other embedded active content is present. |
| `document_protection` | Word document protection is enabled. |
| `signed_package` | Mutation would invalidate a package signature. |
| `tracked_changes` | Pending Word revisions or revision-bearing related parts are present. Build-a-Spec does not author into that revision graph. |
| `unsafe_relationship_scan` | OPC relationships or content types cannot be inspected unambiguously. |
| `unsafe_revision_scan` | Revision-bearing Word parts cannot be inspected unambiguously. |
| `unsafe_document_xml` | The main document XML is malformed or lexically unsafe. |
| `unsafe_settings_xml` | Settings XML is malformed or unsafe. |
| `unsupported_word_namespace` | The main document does not expose exactly one supported Word body namespace. |
| `unsupported_source_xml_encoding` | Source mutation supports UTF-8 Word XML only; the source is not transcoded. |
| `unsupported_raw_zip_layout` | The ZIP can be retained exactly but cannot be rebuilt without risking unrelated raw records. |
| `unsafe_package` | The retained package cannot be safely read or indexed for mutation. |

### Text and element blockers

| Code | Meaning |
|---|---|
| `heading_change` | Source mode does not patch section, part, or article headings. |
| `table_projection` | The element is a read-only semantic projection of an opaque table. |
| `complex_paragraph_markup` | Paragraph-level markup is outside the supported text-patch shape. |
| `complex_run_markup` | Multiple or unsupported inline runs make the text slice ambiguous. |
| `not_direct_body_paragraph` | The provision is not a direct body paragraph. |
| `section_break_paragraph` | The paragraph carries section-layout properties. |
| `noncontiguous_visible_text` | Visible provision text is not one contiguous Word text node. |
| `normalized_text_not_exact_slice` | Extracted text is not one exact source-text slice. |
| `unsupported_source_text_lexical_form` | CDATA or embedded lexical markup prevents a byte-local patch. |
| `unsupported_text_control` | A tab or line break would require unsupported run markup. |
| `unsupported_edge_whitespace` | Leading/trailing whitespace would require changing `xml:space` metadata. |
| `invalid_xml_character` | Replacement text contains a character XML cannot represent. |
| `unmapped_paragraph` | No validated source binding exists for the semantic paragraph. |

### Structural blockers

| Code | Meaning |
|---|---|
| `automatic_numbering_required` | Structural editing requires genuine direct Word numbering. |
| `ambiguous_structural_insert` | An insertion is not unambiguously inside one surviving numbered island. |
| `ambiguous_structural_template` | No single allow-listed paragraph/run template can be proven for synthesis. |
| `cross_island_move` | A move crosses an island, parent, or opaque boundary. |
| `cross_parent_structural_change` | A provision is reparented or moved between articles. |
| `manual_label_structural_change` | Literal labels cannot be renumbered safely. |
| `mixed_numbering_island` | Candidate members use different numbering definitions or levels. |
| `nested_structural_change` | Nested subtrees are being added, removed, moved, or reparented. |
| `noncontiguous_structural_island` | Candidate paragraphs are not contiguous direct body siblings. |
| `numbering_instance_not_isolated` | The `numId` is used outside the candidate island. |
| `structural_change` | The requested structure lies outside the proven structural surface. |
| `unsafe_structural_island` | The proposed final state crosses content outside one safe island. |

### Identity and output-audit blockers

These normally indicate stale, corrupted, incompatible, or internally
inconsistent source state rather than an ordinary unsupported user action.
They must fail closed and must never trigger normalized fallback.

| Code | Meaning |
|---|---|
| `source_unavailable` / `baseline_unavailable` | Required exact source bytes, source map, or imported semantic baseline is absent. |
| `source_hash_mismatch` / `document_hash_mismatch` | Retained bytes no longer match import-time identity. |
| `source_map_mismatch` / `baseline_mismatch` | Persisted mapping, cached context, or semantic baseline no longer agrees. |
| `body_anchor_mismatch` / `text_anchor_mismatch` | An immutable body or text anchor no longer resolves exactly. |
| `invalid_xml_patch` | A composed lexical byte-patch record has invalid bounds, types, or replacement data. |
| `overlapping_xml_patches` | Two proposed lexical patches overlap or target the same unsupported region. |
| `out_of_scope_document_xml_changed` | Document/body metadata or XML outside the approved body surface changed. |
| `body_structure_changed` / `unexpected_body_change` | The composed body differs from the approved final-state plan. |
| `section_properties_moved` | Final Word section properties no longer occupy their required terminal position. |
| `part_inventory_changed` / `out_of_scope_part_changed` | Package members or an opaque member payload changed. |
| `unexpected_document_xml` | The cloned package does not contain the approved lexical XML result. |
| `output_validation_failed` | The independent XML, package, or raw-ZIP output audit failed. |

When adding a blocker, update the canonical server message, this reference,
capability tests, and the relevant final-state/export test together. Never
weaken a blocker merely to make a new fixture editable.

## Persistence and compatibility

The current native package is `.baspec` package format 1. Its fixed members
are:

```text
manifest.json
project.json
source/original.docx    # optional; present for a source-backed import
```

The manifest records size and SHA-256 for the semantic project and optional
source. The semantic project is project format 1. A source-backed project also
contains source-map format 1 in `project.json`; raw source bytes are forbidden
inside that JSON.

Compatibility rules:

- keep package, project, and source-map format 1 while the serialized contract
  remains compatible;
- continue reading current P1/P1b `.baspec` files and current legacy format-1
  JSON projects;
- reject unsupported future format numbers explicitly and atomically;
- never ignore or silently drop a declared exact source member;
- validate source bytes, manifest hashes, source map, imported baseline, and
  every retained history version before replacing the live session;
- never serialize `SourcePatchContext`, lxml trees, raw lexical byte offsets,
  or a raw-ZIP index;
- recompute lexical indexes, raw-ZIP indexes, and the immutable patch context
  from retained source bytes after load, then identity-check them against the
  source map and baseline;
- if a future source-map schema is genuinely incompatible, bump its format and
  add an explicit reader/migration path for format 1 rather than reinterpreting
  old fields.

Legacy JSON contains no binary source member. Its semantic history can still
load and use the ordinary semantic edit/normalized-export path, but
exact-original and source-preserving export are unavailable.

## Developer architecture

The preservation path is intentionally one directional pipeline:

```text
bounded upload
  -> ZIP/OPC safety inspection
  -> semantic extraction + immutable source map
  -> derived SourcePatchContext
  -> per-operation capability probes
  -> authoritative final-state validation
  -> lexical word/document.xml patch
  -> raw ZIP clone/replacement
  -> decompressed-package + raw-record + semantic output audits
  -> response
```

Important modules:

- `backend/spec_doc/source_package.py`: upload limits, safe package inspection,
  exact-source import report, and required OPC checks.
- `backend/spec_doc/importer.py`: semantic extraction and source binding.
- `backend/spec_doc/source_mapping.py`: serialized source-map format, global
  blockers, paragraph bindings, and canonical blocker messages.
- `backend/spec_doc/xml_lexical.py`: encoding-aware lexical index and byte-local
  XML patches.
- `backend/spec_doc/raw_zip.py`: strict raw-record indexing and replacement.
- `backend/spec_doc/source_audit.py`: bounded decompressed-package comparison.
- `backend/spec_doc/source_patch.py`: derived context, capability probes,
  authoritative final-state gate, patch composition, and output audits.
- `backend/spec_doc/project.py` and `project_package.py`: semantic compatibility,
  bounded native package, and atomic load validation.
- `backend/llm/conversation.py` and `backend/app.py`: shared turn/session
  ownership so manual, model, QC, history, reset, import, and load cannot race
  around the source gate.

`SourcePatchContext` is immutable, derived, process-local cache state. Public
gates still receive source bytes, source map, and baseline and reject stale or
mismatched context. Capability probing builds disposable candidates and must
not mutate source bytes, current document, baseline, context, session history,
or version position.

## Privacy-safe diagnostics

There is no remote fidelity telemetry requirement. If optional diagnostics are
added, they may aggregate only coarse data such as app version, preservation
status, blocker code, and count. A privacy-safe diagnostic must never include:

- document, chat, finding, or replacement text;
- raw, escaped, normalized, or reconstructed OOXML;
- DOCX or `.baspec` bytes;
- source-map spans, raw ZIP records, relationships, or package member payloads;
- filenames, filesystem paths, client/project identifiers, URLs, element IDs,
  prompt content, or stable document hashes.

Apply suppression before data leaves the fidelity boundary; post-collection
redaction is not an acceptable substitute. Aggregation must not include free
form exception details because those may contain package names or document
content.

The existing local session trace is a separate developer-forensics feature and
is **not** privacy-safe telemetry. Default and especially deep traces can
contain document text or prompts. Treat trace directories as sensitive project
data: do not upload, attach, or share them without an explicit content review.

## Test fixtures and release evidence

The fixture layers have different evidentiary value:

- `tests/docx_fidelity_helpers.py` builds small, deterministic package shapes
  for source-map, lexical, numbering, relationship, and raw-ZIP tests.
- `tests/test_source_preserving_export.py`,
  `test_source_structural_export.py`, `test_source_xml_lexical.py`,
  `test_raw_zip_clone.py`, `test_source_global_blockers.py`, and
  `test_source_capabilities.py` cover the core fidelity contract.
- `tests/test_project_package.py` and source-history/context tests cover
  persistence, identity, compatibility, and atomic failure.
- `tests/test_chunk8_opc_adversarial.py`,
  `test_chunk8_raw_zip_paths.py`, `test_chunk8_lexical_adversarial.py`,
  `test_chunk8_limits_history.py`, and `test_chunk8_stress_concurrency.py`
  cover adversarial package ambiguity, exact limits, long histories, and
  concurrent lifecycle activity.
- `tests/fixtures/docx_corpus/manifest.json` plus `tests/docx_corpus.py` provide
  the provenance-explicit structural corpus described in
  [DOCX_FIDELITY_CORPUS.md](DOCX_FIDELITY_CORPUS.md).
- `tests/test_docx_visual_regression.py` provides optional renderer-backed
  evidence; setup is documented in
  [DOCX_RENDERER_WINDOWS.md](DOCX_RENDERER_WINDOWS.md).

Synthetic metadata profiles are structural fixtures, not proof that a named
producer opened or rendered the file. An external fixture counts as producer
evidence only when the manifest records its real producer/version, production
method, checksum, modified parts, sanitization, and privacy review. Never add
client-origin or proprietary content merely to broaden coverage.

For release closure, run the complete backend suite, frontend capability tests,
and production frontend build. Materialize and validate the DOCX corpus. Run
Word and/or LibreOffice visual regression when those renderers are available,
and report exactly which renderer/version was exercised; do not imply visual
verification that did not occur.

```powershell
& '.\venv\Scripts\python.exe' -m pytest -q -p no:cacheprovider
Push-Location .\frontend
npm test
npm run build
Pop-Location
& '.\venv\Scripts\python.exe' -m tests.docx_corpus .\artifacts\docx-corpus
```

The required result is not that every fixture becomes editable. The required
result is that every supported mutation stays within its proven byte surface,
every ambiguity narrows to pass-through-only, and exact-original recovery is
never silently lost.
