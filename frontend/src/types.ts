export type Role = "user" | "assistant";

export interface ChatMessage {
  id: string;
  role: Role;
  text: string;
  streaming?: boolean;
  error?: boolean;
  /** Transient live status (WI1); cleared once text/thinking flows. */
  status?: StreamStatus | null;
  /** Accumulated adaptive-thinking summary (WI1), shown collapsed. */
  thinking?: string;
  /** Ids of figures the model created in this assistant turn (rendered
   * inline beneath the text). Resolved against the session figure map. */
  figureIds?: string[];
  /** Terse workflow-event acknowledgment (e.g. research / Final QC kicked
   *  off) — rendered as a compact centered marker, not a model message, so
   *  these never crowd the conversation. */
  note?: boolean;
}

/** Transient streaming status kinds (WI1 status strip). */
export type StatusKind =
  | "working"
  | "thinking"
  | "writing"
  | "drafting"
  | "searching"
  | "fetching"
  | "drawing";

export interface StreamStatus {
  kind: StatusKind;
  round?: number;
  progress_chars?: number;
}

/** An upload the app is waiting on: a master DOCX import or a project open.
 *  Non-null while the request is in flight, so the panel can say so instead
 *  of sitting silently on an unchanged document. */
export type FileLoading = {
  kind: "import" | "open";
  name: string;
} | null;

/** What the panel has to say about the last import, reported next to the
 *  action that produced it rather than as a fabricated chat message. A clean
 *  import produces none: `error` when it failed, `warn` when it succeeded but
 *  could not carry some content across. */
/**
 * A dismissible panel notice about a file the user handed over. Uploads are
 * panel actions, so they report in the panel, never in the chat. `title`
 * overrides the default import wording (a reference attachment is not an
 * import); omitting it leaves the import instance exactly as it was.
 */
export type ImportNotice = {
  tone: "error" | "warn";
  name: string;
  lines: string[];
  title?: string;
} | null;

export interface Health {
  status: string;
  app: string;
  version: string;
  model: string;
  api_key_present: boolean;
  module?: string;
  module_id?: string;
  /** Effective discipline: document identity first, legacy session fallback. */
  discipline?: string;
  /** Stable old-project fallback; never mirrors document identity. */
  legacy_discipline?: string;
  /** Legacy optional project-description primer. */
  project_context?: string;
  /** Lease identity for every operation that can switch tutorial workspaces. */
  workspace_id: number;
  workspace_scope: "original" | "tutorial" | "scenario";
  generation: number;
  /** Whether completed research/QC runs auto-send a debrief chat turn.
   *  Optional: an older backend simply doesn't send it (treated as on). */
  auto_debrief?: boolean;
}

/** API-key resolution status (WI3 settings panel). Never carries the key. */
export interface KeyStatus {
  present: boolean;
  source: "env" | "keyring" | "file" | "none";
  masked: string;
  env_locked?: boolean;
}

/** Session-scoped billed-usage snapshot (WI4 cost meter). */
export interface UsageSummary {
  categories: Record<string, Record<string, number>>;
  totals: Record<string, number>;
  turns: number;
  estimated_cost_usd: {
    by_category: Record<string, number>;
    total: number;
  };
  cache_saved_usd: number;
  /**
   * True when any bucket carries `estimated_output_tokens` — output from a
   * turn the user stopped, which the provider never reported a final count
   * for. Every other counter is provider-reported; the estimate is a
   * separate addition, never blended into `output_tokens`. Derived from the
   * counter server-side, so the flag and the number cannot disagree.
   */
  includes_estimated_output?: boolean;
  /**
   * Context gauge, not spend: the Anthropic-counted conversation size after
   * the last committed chat turn (system prompt + tools + history + project
   * context + the retained reply), against the model's context window. null
   * until a turn commits (fresh session, reset, or a just-loaded project).
   * A turn stopped mid-stream makes this an upper estimate for that turn.
   */
  context?: { tokens: number; window: number } | null;
}

/* --- Document model (mirrors backend/spec_doc/model.py serialization) --- */

export type BlockStatus = "confirmed" | "assumed" | "needs_input" | "imported";

export interface DocParagraph {
  id: string;
  label: string;
  text: string;
  status: BlockStatus;
  /** Optional research-item provenance (r-… id from the profile). */
  source_item_id: string;
  /**
   * Non-empty when this block is a read-only projection of preserved Word
   * content — a table, a picture, an embedded object, a content control.
   * The export emits the original block verbatim, so its content is not
   * ours to retype; it can still be moved, deleted and marked reviewed.
   * Server-owned: the reason code and its prose both come from the backend
   * (`model.LOCK_REASONS`), never restated here.
   */
  locked?: string;
  locked_message?: string;
  children: DocParagraph[];
}

export interface DocArticle {
  id: string;
  number: string;
  title: string;
  paragraphs: DocParagraph[];
}

export interface DocPart {
  id: string;
  number: number;
  title: string;
  articles: DocArticle[];
}

export interface SpecDoc {
  section: { number: string; title: string };
  parts: DocPart[];
  version: { index: number; count: number };
  edition_overrides?: Record<string, { edition: string; basis: string }>;
  project_identity?: {
    discipline?: string;
    /** Facility/use, e.g. Data Center or Hospital. */
    project_type?: string;
  };
  project_profile?: {
    city?: string;
    state_or_province?: string;
    country?: string;
    client_name?: string;
  };
}

/** An applied edit op echoed in a doc_patch (ids are server-assigned). */
export interface DocOp {
  action:
    | "add_article"
    | "add_paragraph"
    | "move"
    | "replace"
    | "delete"
    | "set_status"
    | "set_project_identity"
    | "set_project_profile"
    | "set_standard_edition"
    | "set_standard_suppressed";
  id: string;
  target_id?: string;
  status?: BlockStatus;
  standard?: string;
  edition?: string;
  removed?: boolean;
  suppressed?: boolean;
  restored?: boolean;
  position?: number;
  previous_position?: number;
}

/** A manual edit op sent to POST /api/doc/edit. */
export interface EditOp {
  action:
    | "add_article"
    | "replace"
    | "delete"
    | "set_status"
    | "add_paragraph"
    | "move"
    | "set_project_identity"
    | "set_project_profile"
    | "set_standard_edition"
    | "set_standard_suppressed";
  target_id: string;
  text?: string;
  status?: BlockStatus;
  source_item_id?: string;
  /** replace on target_id "sec": the section number (e.g. "21 13 13").
   * `text` carries the section title on the same op. */
  numbering?: string;
  /** add_article / add_paragraph: optional insertion index; move: required
   * final index among the target article or paragraph's existing siblings. */
  position?: number;
  /** set_project_identity fields (target_id must be "sec"). */
  discipline?: string;
  project_type?: string;
  /** set_project_profile fields (target_id must be "sec") — provide only
   * the ones being changed; an explicit empty string clears that field. */
  city?: string;
  state?: string;
  country?: string;
  client?: string;
  /** set_standard_edition / set_standard_suppressed fields (target_id "sec").
   * standard = the designation; edition = "" removes an override/added
   * standard; basis = the reason (required to add/change an edition,
   * optional to exclude); title = full title for an added standard;
   * suppressed = true to exclude, false to restore. */
  standard?: string;
  edition?: string;
  basis?: string;
  title?: string;
  suppressed?: boolean;
}

export interface OpenItem {
  id: string;
  element_id: string;
  ref: string;
  kind: "tbd" | "needs_input";
  label: string;
}

/** One deterministic lint finding (advisory, never blocking). */
export interface LintIssue {
  id: string;
  rule: string;
  severity: "warn" | "info";
  element_id: string;
  ref: string;
  message: string;
  match: string;
}

/** One standard's edition in effect: module pin, jurisdiction override, a
 * user-added standard (is_added), or an excluded one (is_suppressed, appended
 * so the panel can show it struck-through with a Restore control). */
export interface StandardInfo {
  name: string;
  edition: string;
  title: string;
  is_override: boolean;
  is_added: boolean;
  basis: string;
  is_suppressed: boolean;
  reason: string;
}

/** A chat-authored figure (mirrors backend/figures.py serialization). Source
 * is model-authored and always sanitized at render — see lib/figures.ts. */
export type FigureKind = "mermaid" | "svg" | "table";

export interface Figure {
  fid: string;
  kind: FigureKind;
  title: string;
  caption: string;
  alt_text: string;
  /** Mermaid text or SVG markup (kinds mermaid/svg); "" for a table. */
  source: string;
  /** Table header cells + body rows (kind table). */
  columns: string[];
  rows: string[][];
  created_at: string;
  /** Assistant-bubble ordinal that created it (for reload re-inlining). */
  message_index: number;
}

/**
 * Import-fidelity accounting for the current session. An imported DOCX is
 * normalized into Build-a-Spec's semantic body model; this report keeps that
 * lossy boundary visible everywhere the document payload is refreshed.
 */
export interface ImportReport {
  filename: string;
  sha256: string;
  size_bytes: number;
  zip_member_count: number;
  zip_uncompressed_bytes: number;
  imported_block_count: number;
  skipped_empty_count: number;
  warnings: string[];
  tracked_changes_detected: boolean;
  /**
   * False when the import found no SECTION number, PART heading, or numbered
   * article — i.e. the spec scaffolding around the content is the importer's,
   * not the file's. Projects saved before shape detection omit it; treat a
   * missing value as true so their presentation is unchanged.
   */
  spec_shape_detected?: boolean;
  fidelity_notice: string;
}

export type SourcePreservationStatus =
  | "ready"
  | "pass_through_only"
  | "blocked"
  | "unavailable";

export interface SourcePreservationBlocker {
  uid: string;
  blocker: string;
  message: string;
}

/** Source export readiness is distinct from permission to mutate DOCX body XML. */
export interface SourcePreservationState {
  status: SourcePreservationStatus;
  source_export_ready: boolean;
  exact_original_available: boolean;
  /** "bounded" never implies that every imported block is editable. */
  body_editing: "bounded" | "disabled";
  no_op: boolean;
  changed_uids: string[];
  blockers: SourcePreservationBlocker[];
}

/** Operations whose imported-DOCX safety is decided by the server. */
export type SourceCapabilityOperation =
  | "replace_text"
  | "delete"
  | "move"
  | "add_article"
  | "add_paragraph"
  | "set_status"
  | "set_provenance"
  | "set_project_identity"
  | "set_project_profile"
  | "set_standard_edition"
  | "set_standard_suppressed";

/** One independently safe insertion island and its exact sibling positions. */
export interface SourceCapabilityPlacement {
  island_key: string;
  allowed_positions: number[];
  /** Present only when every position in the inclusive range is allowed. */
  minimum_position?: number;
  /** Present only when every position in the inclusive range is allowed. */
  maximum_position?: number;
}

/** A server-derived decision for one operation on one semantic element. */
export interface SourceOperationCapability {
  allowed: boolean;
  blocker?: string;
  message?: string;
  island_key?: string;
  current_position?: number;
  /** Informational only; clients must use allowed_positions, not infer a range. */
  minimum_position?: number;
  /** Informational only; clients must use allowed_positions, not infer a range. */
  maximum_position?: number;
  allowed_positions?: number[];
  placements?: SourceCapabilityPlacement[];
}

/** The wire payload is a direct operation map, not an `operations` wrapper. */
export interface SourceElementCapabilities {
  [operation: string]: SourceOperationCapability | undefined;
}

/**
 * Capability reports carry one status the preservation payload cannot:
 * `pending`, meaning the server's per-element sweep for this document state
 * is still running. It denies every body operation exactly like `blocked`
 * — an underived permission is never granted — but it is temporary, so the
 * UI says "checking" rather than "read-only".
 */
export type SourceCapabilityStatus = SourcePreservationStatus | "pending";

/** Per-element imported-source permissions, recomputed for each document state. */
/**
 * One package-wide reason the whole imported document is frozen.
 *
 * Both strings are server-authored. `message` is the existing denial
 * vocabulary; `remedy` is the user-actionable next step. Render them
 * verbatim — never restate a denial in client prose (see
 * lib/sourceCapabilities.ts).
 */
export interface SourceCapabilityCause {
  blocker: string;
  message: string;
  remedy: string;
}

export interface SourceCapabilitiesState {
  status: SourceCapabilityStatus;
  elements: Record<string, SourceElementCapabilities>;
  /**
   * Empty on a `ready` report, and on a `pending` one — a sweep still
   * running is not a fault in the user's file. Optional so a payload from an
   * older backend still parses.
   */
  causes?: SourceCapabilityCause[];
}

/**
 * One attached reference document, as the API reports it. The body is
 * deliberately absent: it is read by the model through its own tool, never
 * shipped with the document payload.
 */
export interface ReferenceDocMeta {
  rid: string;
  filename: string;
  title: string;
  /** Characters of extracted text BEFORE any truncation. */
  char_count: number;
  block_count: number;
  truncated: boolean;
  /** Word only: the file carried pending revisions and was read Accept-All. */
  tracked_changes: boolean;
  added_at: string;
  /** Which extractor read it: docx | pdf | txt | xml | csv. */
  kind: string;
  /** That kind's display name — "Word", "PDF", "CSV". */
  kind_label: string;
  excerpt: string;
  /** Anthropic-counted tokens contributed to the 100,000-token attachment cap. */
  token_count: number;
}

export interface TemplateOrigin {
  template_id: string;
  name: string;
  seed_block_ids: string[];
}

/** The three facts a whole-section draft anchors on. */
export type DraftPrerequisiteId = "section" | "project_type" | "country";

export interface DraftRequirement {
  id: DraftPrerequisiteId;
  /** Human phrasing for the tooltip ("the project country"). */
  label: string;
  satisfied: boolean;
  /** Resolved display value; "" when unsatisfied. */
  value: string;
}

/**
 * What "Draft full section" still needs before it drafts rather than asks.
 *
 * Server-derived — the panel renders it, the endpoint decides with it. The
 * frontend must never recompute this from `doc.project_identity` /
 * `doc.project_profile`: a second derivation is free to tell the user the
 * button will draft when the click is about to ask.
 */
export interface DraftPrerequisites {
  ready: boolean;
  missing: DraftPrerequisiteId[];
  requirements: DraftRequirement[];
}

/** Where a session saves itself: an absolute local path plus its basename
 *  (the part a toolbar button can actually show). */
export interface SaveTarget {
  path: string;
  name: string;
}

/**
 * What the native shell reports back from a save.
 *
 * `cancelled` is separate from `error` on purpose: backing out of a Save
 * dialog is a decision and the UI stays quiet about it, while a write that
 * failed has to say so. Collapsed into one falsy value they are the same
 * event, and one of them deserves a red line and the other does not.
 */
export interface SaveProjectResult {
  ok: boolean;
  cancelled: boolean;
  error: string;
  target: string;
  name: string;
}

/**
 * A save attempt as the app sees it, whichever path performed it — the
 * native shell's overwrite/dialog, a tutorial copy download, or the dev
 * browser's download. `ok` is the only thing the save gate acts on: nothing
 * that replaces a session proceeds without a file actually written.
 */
export interface SaveOutcome {
  ok: boolean;
  cancelled: boolean;
  error: string;
}

export interface DocPayload {
  /** Authoritative active-workspace lease after this snapshot/mutation. */
  workspace_id: number;
  workspace_scope: "original" | "tutorial" | "scenario";
  generation: number;
  doc: SpecDoc;
  open_questions: OpenItem[];
  lint: LintIssue[];
  standards: StandardInfo[];
  profile_complete: boolean;
  /** Full-draft gate: section, project type, and country. */
  draft_prerequisites: DraftPrerequisites;
  research_status: ResearchRunStatus;
  /**
   * The file this session already saved itself to, or null when it never
   * has. Server-owned, and the panel's Save button is drawn off it: null
   * means Save asks where; a target means Save overwrites that file and the
   * dialog moves behind "Save as…". Never inferred locally — a reset clears
   * it server-side, and a button that had kept its own copy would silently
   * overwrite the project that was just discarded.
   */
  project_save_target: SaveTarget | null;
  /** Imported-master version index (Batch 5); null for from-scratch. */
  baseline_index: number | null;
  /** Chat-authored figures (diagrams/schematics/tables); [] when none. */
  figures: Figure[];
  /** Suggested reply chips staged by the model (Batch 9); [] when none. */
  suggested_prompts: string[];
  /** Attached reference documents (metadata only); [] when none. */
  reference_docs: ReferenceDocMeta[];
  /** Import fidelity/recovery state; null for a from-scratch document. */
  import_report: ImportReport | null;
  /** True when this active session has an exact attached source DOCX. */
  source_available: boolean;
  /**
   * The document gave up source preservation ("Edit freely").
   *
   * Explicit because it is not inferable: detaching KEEPS the source bytes
   * and the imported baseline — that is what leaves the exact original
   * downloadable and redline vs master working — while `source_capabilities`
   * goes null. That combination is byte-identical to a source-backed
   * document whose report has not arrived, so a client that infers scope
   * from the retained artifacts locks a document the user just unlocked.
   */
  source_detached: boolean;
  /** True when edits can be exported by cloning and narrowly patching the source. */
  preservation_ready: boolean;
  /** Detailed imported-source capability state; null for from-scratch documents. */
  source_preservation: SourcePreservationState | null;
  /** Per-operation source-edit permissions; null when no source package exists. */
  source_capabilities: SourceCapabilitiesState | null;
  /** Reusable-starter provenance; independent from office-master source state. */
  template_origin: TemplateOrigin | null;
}

/* --- Version diff / redline (Batch 5, mirrors backend/spec_doc/diffing.py) --- */

export interface DiffRun {
  op: "equal" | "ins" | "del";
  text: string;
}

export interface ElementDiff {
  uid: string;
  node_type: "section" | "part" | "article" | "paragraph";
  kind: "unchanged" | "changed" | "inserted" | "deleted";
  depth: number;
  label: string;
  ref_base: string;
  ref_cur: string;
  base_text: string;
  cur_text: string;
  runs: DiffRun[] | null;
  number_base: string;
  number_cur: string;
}

export interface DiffStatusChange {
  uid: string;
  ref: string;
  status_base: string;
  status_cur: string;
}

export interface SectionDiff {
  elements: ElementDiff[];
  status_changes: DiffStatusChange[];
  stats: { inserted: number; deleted: number; changed: number; unchanged: number };
}

export interface SectionDiffPayload extends SectionDiff {
  ok: boolean;
  base_index: number;
  cur_index: number;
  baseline_index: number | null;
}

/* --- Research (Phase 4) --- */

export type ResearchRunStatus = "idle" | "running" | "complete" | "failed";

/** What the `stream_end` sentinel can say. Closed on purpose: the follower
 *  decides whether to stop or reconnect by switching over this union with a
 *  `never` fallback (`lib/researchLive.classifyResearchStreamEnd`), so a new
 *  status cannot be added without someone deciding what it means for
 *  reconnection. `superseded` means a newer run took the runner over and
 *  this stream ended without draining. */
export type ResearchStreamEndStatus = ResearchRunStatus | "superseded";

export interface ResearchEvent {
  seq: number;
  ts: string;
  type: string;
  dimension_id?: string;
  title?: string;
  item_count?: number;
  grounded_count?: number;
  error?: string;
  done?: number;
  total?: number;
  project?: string;
  /** On the `stream_end` sentinel only. */
  status?: ResearchStreamEndStatus;
  restored?: boolean;
  /** 1-based research round this event belongs to (rounds append). */
  round?: number;
  /** On `research_complete`: this round's own contribution, where
   *  `item_count` is the cumulative total the session now holds. */
  round_item_count?: number;
  new_item_count?: number;
  repeat_item_count?: number;
  /** On `research_started`: the dimension id roster, plus id → human title
   *  so the live board seeds real names before any worker has emitted. A
   *  scoped round rosters only the dimensions it runs. */
  dimensions?: string[];
  dimension_titles?: Record<string, string>;
  /** On `research_started`: how many dimensions the module declares, so a
   *  scoped round can say "2 of 4 areas" without a second fetch. */
  declared_dimension_count?: number;
  /** On `dimension_started`: the dimension's web-tool budgets. */
  max_searches?: number;
  max_fetches?: number;
  /** On `dimension_activity`: what that agent is doing right now. */
  kind?: "thinking" | "searching" | "fetching" | "writing";
  /** On `dimension_search` / `dimension_fetch`: the live query / URL. */
  query?: string;
  url?: string;
  /** On `dimension_retry`: which attempt just failed (1-based), the
   *  attempt ceiling, the failure class, and the backoff before retrying. */
  attempt?: number;
  max_attempts?: number;
  reason?: string;
  backoff_s?: number;
  /** On `dimension_complete` / `dimension_failed`: billed web-tool
   *  request counts for the dimension. */
  web_search_requests?: number;
  web_fetch_requests?: number;
}

export interface ResearchItemView {
  item_id: string;
  dimension_id: string;
  topic: string;
  category: string;
  requirement: string;
  authority: string;
  code_reference: string;
  accepted_sources: string[];
  grounded: boolean;
  confidence: number;
  actionability: string;
  notes: string;
  /** Date of the round that last confirmed this item, and the 1-based
   *  round that first found it. Empty/0 on profiles saved before research
   *  rounds accumulated. */
  research_date?: string;
  round_index?: number;
}

export interface ResearchDimensionView {
  dimension_id: string;
  /** Human title of the research dimension/agent (e.g. "Governing building
   *  and fire codes"); empty on legacy profiles saved before it was stored. */
  title: string;
  status: string;
  item_count: number;
  grounded_count: number;
  web_search_requests: number;
  web_fetch_requests: number;
  error: string;
}

/** One research pass's own record. Pressing Research again appends a
 *  round rather than replacing the profile, so this is what a single round
 *  did: its per-dimension outcome (unmerged — a dimension may have failed
 *  here after succeeding earlier) and how much of it was new. */
export interface ResearchRoundView {
  round_index: number;
  research_date: string;
  dimension_statuses: ResearchDimensionView[];
  new_items: number;
  repeat_items: number;
}

export interface ResearchProfileView {
  /** The latest round's date. */
  research_date: string;
  project: Record<string, string>;
  /** Cumulative across every round. */
  dimension_statuses: ResearchDimensionView[];
  items: ResearchItemView[];
  /** Each round's own record, oldest first. Absent on legacy payloads. */
  rounds?: ResearchRoundView[];
}

/** One declared research area that has never completed in any round. */
export interface ResearchCoverageGap {
  dimension_id: string;
  title: string;
  /** False only for a dimension the module declares optional, with a stated
   *  rationale — those never block issue readiness. */
  required: boolean;
}

/** How the module's declared research areas line up with what ran.
 *
 *  Derived server-side by the same `research_coverage` join readiness uses.
 *  The drawer must never recompute it from `dimension_statuses`: a second
 *  derivation is free to offer a retry the start endpoint would refuse. */
export interface ResearchCoverageView {
  total: number;
  completed: string[];
  gaps: ResearchCoverageGap[];
}

/** Which declared dimensions a round runs. `gaps` is resolved server-side
 *  to the areas that never completed; `all` is every declared dimension. */
export type ResearchScope = "all" | "gaps";

export interface ResearchSnapshot {
  status: ResearchRunStatus;
  error: string;
  error_kind?: "auth_error" | "";
  events: ResearchEvent[];
  profile?: ResearchProfileView;
  /** Absent on a snapshot built locally from SSE frames alone (the merge
   *  carries the last fetched value forward); refreshed at every milestone. */
  coverage?: ResearchCoverageView;
}

/* --- Master import + compliance audit + updates (Phase 5) --- */

export interface ImportResultPayload extends DocPayload {
  ok: boolean;
  warnings: string[];
  imported_block_count: number;
  skipped_empty_count: number;
  tracked_changes_detected: boolean;
}

export type AuditCoverageStatus =
  | "represented"
  | "missing"
  | "contradicted"
  | "unclear";

export interface AuditCoverageEntry {
  requirement_id: string;
  status: AuditCoverageStatus;
  evidence_quote: string;
  element_id: string;
  note: string;
}

export interface AuditFinding {
  severity: "critical" | "high" | "medium" | "low";
  requirement_id: string;
  element_id: string;
  issue: string;
  suggestion: string;
}

export interface AuditResult {
  summary: string;
  coverage: AuditCoverageEntry[];
  findings: AuditFinding[];
  audited_at: string;
  version_index: number;
}

export interface AuditSnapshot {
  status: ResearchRunStatus;
  error: string;
  result?: AuditResult;
}

/* --- Final QC (Batch 4) --- */

export type QcRunStatus = "idle" | "running" | "complete" | "failed";
export type Severity = "critical" | "high" | "medium" | "low";
export type QcFindingStatus = "open" | "applied" | "dismissed";
export type QcOpsSemanticStatus =
  | "not_proposed"
  | "not_evaluated"
  | "approved"
  | "rejected";
export type QcModuleSectionCompatibilityStatus =
  | "match"
  | "mismatch"
  | "unknown"
  | "not_applicable";

export interface QcModuleSectionCompatibility {
  status: QcModuleSectionCompatibilityStatus;
  section_number: string;
  section_title: string;
  module_id: string;
  module_display_name: string;
  allowed_sections: { number: string; title: string }[];
  message: string;
}

export interface QcSourceRecord {
  url: string;
  title: string;
  methods: string[];
  normalized: string;
  accepted: boolean | null;
  reason: string;
}

export interface QcReviewedCheck {
  check: string;
  outcome: "passed" | "finding" | "not_applicable" | string;
  notes: string;
  element_ids: string[];
  source_urls: string[];
  source_checks: QcSourceRecord[];
}

export interface QcDispositionEvent {
  action: string;
  at: string;
  reason: string;
  document_version?: number;
  document_fingerprint: string;
}

export interface QcVerdict {
  upholds: boolean;
  revised_severity: string | null;
  note: string;
  ops_adequate: boolean;
  ops_note: string;
  status: "completed" | "failed" | "cancelled" | string;
  error: string;
  reviewer_index: number;
  search_queries: string[];
  retrieved_sources: QcSourceRecord[];
  attempted_search_queries?: string[];
  attempted_sources?: QcSourceRecord[];
  usage_totals: Record<string, number>;
  estimated_cost_usd?: number;
  api_request_count: number;
  model_response_count: number;
}

export interface QcFinding {
  finding_id: string;
  lens_id: string;
  severity: Severity;
  element_id: string;
  title: string;
  issue: string;
  rationale: string;
  original_severity: string;
  reviewed_ref: string;
  reviewed_text: string;
  element_resolved: boolean;
  source_urls: string[];
  accepted_sources: string[];
  grounded: boolean;
  source_checks: QcSourceRecord[];
  proposed_ops: Record<string, unknown>[];
  ops_semantic_status: QcOpsSemanticStatus;
  ops_semantic_reason: string;
  ops_valid: boolean;
  ops_invalid_reason: string;
  verdicts: QcVerdict[];
  verification_outcome:
    | "upheld"
    | "refuted"
    /** v4: a COMPLETE panel that did not agree. Distinct from
     *  `inconclusive` (infrastructure failure) — escalates to a human. */
    | "disputed"
    | "default_refuted"
    | "inconclusive"
    | string;
  verification_panel_size: number;
  verification_threshold: number;
  /** v4 rule identity. Empty on v3 and older records, which recorded only
   *  the integer threshold their own strict-majority rule used. */
  verification_rule?: string;
  /** Why a candidate is disputed: "split_panel", or
   *  "insufficient_refutation_evidence" when a critical/high refutation
   *  cited nothing that validated. Empty for every other outcome. */
  dispute_reason?: string;
  /** Chunk 5.2: content-addressed ids of the ORIGINAL lens claims this
   *  candidate covers. Stable references — the immutable records live once,
   *  in `consolidation.origins`; resolve with `qcCandidateOrigins`. Length
   *  > 1 means several lenses raised one defect and shared a panel. Absent
   *  or single on a report produced with consolidation off. */
  candidate_origins?: string[];
  /** How this candidate's `proposed_ops` were arrived at when it covers
   *  more than one original claim. */
  ops_source?: QcOpsSource;
  status: QcFindingStatus;
  dismiss_reason: string;
  disposition_events: QcDispositionEvent[];
}

/** Provenance of a consolidated candidate's proposed operations. */
export type QcOpsSource =
  /** Single lens claim; its operations, unchanged. */
  | "original"
  /** Every contributing lens that proposed operations proposed the same. */
  | "identical"
  /** One synthesized set, which the verifier panel had to approve. */
  | "reconciled"
  /** Members disagreed and nothing was reconciled: advisory only, and a
   *  human picks among the alternatives on the original claims. */
  | "unreconciled"
  /** Nobody proposed a mechanical fix. */
  | "none"
  | string;

/** One lens's original claim, frozen before consolidation touched it. */
export interface QcCandidateOrigin {
  origin_id: string;
  candidate_index: number;
  candidate_id: string;
  lens_id: string;
  severity: string;
  element_id: string;
  title: string;
  issue: string;
  rationale: string;
  source_urls: string[];
  accepted_sources: string[];
  grounded: boolean;
  source_checks?: QcSourceRecord[];
  proposed_ops: Record<string, unknown>[];
}

/** One emitted group: which originals it covers, and how it was decided. */
export interface QcConsolidationGroup {
  group_index: number;
  candidate_id: string;
  origin_ids: string[];
  element_id: string;
  severity: string;
  bucket_id: string;
  canonical_title: string;
  canonical_issue: string;
  canonical_rationale: string;
  grouping_rationale: string;
  ops_source: QcOpsSource;
  proposed_ops: Record<string, unknown>[];
}

/** The persisted cross-lens grouping record (Chunk 5.2).
 *
 *  `status` describes the GROUPING STEP alone, never the QC run: a failed
 *  grouping still produced a complete partition (all singletons) and the
 *  run continued untouched. */
export interface QcConsolidation {
  status: "complete" | "skipped" | "failed" | string;
  error: string;
  fallback_reason: string;
  origins: QcCandidateOrigin[];
  groups: QcConsolidationGroup[];
  usage_totals: Record<string, number>;
  estimated_cost_usd: number;
  api_request_count: number;
  model_response_count: number;
  raw_candidate_count?: number;
  grouped_candidate_count?: number;
  panels_avoided?: number;
}

export interface QcLensStatus {
  lens_id: string;
  title: string;
  status: string;
  brief: string;
  summary: string;
  finding_count: number;
  grounded_count: number;
  reviewed_checks: QcReviewedCheck[];
  search_queries: string[];
  retrieved_sources: QcSourceRecord[];
  attempted_search_queries?: string[];
  attempted_sources?: QcSourceRecord[];
  usage_totals: Record<string, number>;
  estimated_cost_usd?: number;
  api_request_count: number;
  model_response_count: number;
  error: string;
}

export interface QcResultView {
  schema_version: number;
  protocol_version: string;
  run_id: string;
  execution_status: "complete" | "partial" | string;
  summary: string;
  findings: QcFinding[];
  refuted: QcFinding[];
  /** v4: fully reviewed candidates whose panel disagreed. Blocks audit
   *  completeness until a human dispositions each one; never auto-applied.
   *  Absent on v3 and older records, which had no disputed outcome. */
  disputed?: QcFinding[];
  /** Candidates lacking enough completed verifier seats for a substantive
   *  uphold/refute decision. Infrastructure-inconclusive, never open issues. */
  inconclusive: QcFinding[];
  lens_statuses: QcLensStatus[];
  /** Absent on a report produced with consolidation off, and on every v4
   *  report written before the step existed. `input_manifest.configuration
   *  .consolidation_enabled` is what says which of those it is. */
  consolidation?: QcConsolidation | null;
  started_at: string;
  finished_at: string;
  version_index: number;
  version_fingerprint?: string;
  input_fingerprint: string;
  input_manifest: Record<string, unknown>;
  model: string;
  effort: string;
  /** Local date the run supplied to every lens and verifier seat, which
   *  drives their edition-currency judgements. Optional: absent on every
   *  pre-1.8.0 record, and never derivable from `started_at` (UTC). */
  context_date?: string;
  max_tokens: number;
  duration_ms: number;
  usage_totals: Record<string, number>;
  estimated_cost_usd: number;
  cost_basis?: Record<string, unknown>;
  api_request_count: number;
  model_response_count: number;
  research_profile_present: boolean;
  dismissed_ids: string[];
}

/** One candidate as introduced at the start of adversarial verification.
 * `candidate_id` is run-local UI identity; it never replaces the final,
 * content-addressed `finding_id` written to the audit report. */
export interface QcCandidateRosterEntry {
  candidate_id: string;
  title: string;
  original_severity: string;
  lens_id: string;
  /** How many original lens claims this candidate stands for. > 1 means
   *  consolidation gave several lenses' claims one shared panel. Absent on
   *  a replayed pre-5.2 log, where every candidate was one claim. */
  origin_count?: number;
  panel_size: number;
  /** v4: seats that must uphold for a clean uphold — always the panel size,
   *  since v4 upholds only unanimously. */
  uphold_requires?: number;
  /** v3's integer bar. Retained so a replayed older log still folds. */
  threshold?: number;
  /** The v4 rule that will adjudicate this panel. */
  rule?: string;
  /** True when a refutation of this candidate needs validated evidence
   *  (critical/high only). */
  evidence_gated?: boolean;
  outcomes?: string[];
}

export type QcWorkerActivityKind =
  | "thinking"
  | "searching"
  | "fetching"
  | "writing";

export type QcValidationOutcome = "safe_fix" | "advisory" | "manual";

/** Logged frames carry seq/ts. The terminal `stream_end` sentinel is emitted
 * by the SSE iterator rather than retained in the log, so those fields are
 * deliberately optional at the union boundary. */
interface QcEventBase {
  seq?: number;
  ts?: string;
}

interface QcLensEventBase extends QcEventBase {
  lens_id: string;
}

interface QcVerifierEventBase extends QcEventBase {
  candidate_id: string;
  reviewer_index: number;
}

/** Every Final QC frame the current and legacy backends can emit. Keeping the
 * discriminator closed makes live-state folding exhaustive without treating
 * model- or tool-authored payload text as displayable UI. */
export type QcEvent =
  | (QcEventBase & {
      type: "qc_started";
      run_id: string;
      protocol_version?: string;
      lenses: { lens_id: string; title: string }[];
      research_profile_present?: boolean;
    })
  | (QcLensEventBase & {
      type: "lens_started";
      title?: string;
      max_searches?: number;
      max_fetches?: number;
    })
  | (QcLensEventBase & {
      type: "lens_activity";
      kind?: QcWorkerActivityKind;
    })
  | (QcLensEventBase & { type: "lens_search"; query?: string })
  | (QcLensEventBase & { type: "lens_fetch"; url?: string })
  | (QcLensEventBase & {
      type: "lens_retry";
      attempt?: number;
      max_attempts?: number;
      reason?: string;
      backoff_s?: number;
    })
  | (QcLensEventBase & {
      type: "lens_complete" | "lens_failed";
      title?: string;
      finding_count?: number;
      grounded_count?: number;
      reviewed_check_count?: number;
      candidate_count?: number;
      search_count?: number;
      fetch_count?: number;
      request_count?: number;
      error?: string;
      done?: number;
      total?: number;
    })
  | (QcEventBase & {
      type: "consolidation_started";
      raw_candidate_count?: number;
      bucket_count?: number;
      eligible_bucket_count?: number;
      eligible_candidate_count?: number;
    })
  | (QcEventBase & {
      type: "consolidation_activity";
      bucket_id: string;
      kind?: QcWorkerActivityKind;
    })
  | (QcEventBase & { type: "consolidation_search"; bucket_id: string; query?: string })
  | (QcEventBase & { type: "consolidation_fetch"; bucket_id: string; url?: string })
  | (QcEventBase & {
      type: "consolidation_retry";
      bucket_id: string;
      attempt?: number;
      max_attempts?: number;
      reason?: string;
      backoff_s?: number;
    })
  | (QcEventBase & {
      type: "consolidation_complete";
      status?: "complete" | "skipped" | "failed" | string;
      raw_candidate_count?: number;
      grouped_candidate_count?: number;
      panels_avoided?: number;
      error?: string;
    })
  | (QcEventBase & {
      /** Legacy aggregate progress retained for old/replayed event logs. */
      type: "verify_progress";
      done?: number;
      total?: number;
    })
  | (QcEventBase & {
      type: "verification_started";
      candidates: QcCandidateRosterEntry[];
      total_candidates?: number;
      total_seats?: number;
      max_workers?: number;
      /** How phase 2 is being executed. "batch" submits every seat to the
       *  Message Batches API at half price and therefore emits no per-seat
       *  activity frames; "stream" is the live-relay path. The review is
       *  identical either way — this only tells the board what kind of
       *  progress it can honestly show. */
      transport?: "batch" | "stream";
    })
  /** Progress of one batched verification round, reported from the
   *  provider's own request_counts rather than inferred. */
  | (QcEventBase & {
      type: "verification_batch";
      status: "submitted" | "polling" | "ended" | "cancelled" | "timeout" | "failed";
      round?: number;
      batch_id?: string;
      submitted?: number;
      settled?: number;
      total?: number;
      processing?: number;
      succeeded?: number;
      errored?: number;
      canceled?: number;
      expired?: number;
      error?: string;
    })
  | (QcVerifierEventBase & { type: "verifier_started" })
  | (QcVerifierEventBase & {
      type: "verifier_activity";
      kind?: QcWorkerActivityKind;
    })
  | (QcVerifierEventBase & { type: "verifier_search"; query?: string })
  | (QcVerifierEventBase & { type: "verifier_fetch"; url?: string })
  | (QcVerifierEventBase & {
      type: "verifier_retry";
      attempt?: number;
      max_attempts?: number;
      reason?: string;
      backoff_s?: number;
    })
  | (QcVerifierEventBase & {
      type: "verifier_complete";
      status: "completed" | "failed" | "cancelled" | string;
      error?: string;
      /** Present only for a completed seat. */
      upholds?: boolean;
      revised_severity?: string | null;
      ops_adequate?: boolean;
    })
  | (QcEventBase & {
      type: "candidate_complete";
      candidate_id: string;
      outcome: "upheld" | "refuted" | "disputed" | "inconclusive";
      /** Set only when `outcome` is "disputed". */
      dispute_reason?: string;
      panel_size?: number;
      /** v4 replaces the integer `threshold`: an uphold needs every seat. */
      uphold_requires?: number;
      threshold?: number;
      completed_seats?: number;
      upholds?: number;
    })
  | (QcEventBase & {
      type: "verification_complete";
      total_candidates?: number;
      total_seats?: number;
      completed_seats?: number;
      upheld?: number;
      refuted?: number;
      disputed?: number;
      inconclusive?: number;
    })
  | (QcEventBase & { type: "validation_started"; total?: number })
  | (QcEventBase & {
      type: "validation_progress";
      candidate_id: string;
      done?: number;
      total?: number;
      outcome?: QcValidationOutcome;
      ops_semantic_status?: QcOpsSemanticStatus | string;
      ops_valid?: boolean;
      reason?: string;
    })
  | (QcEventBase & {
      type: "validation_complete";
      total?: number;
      done?: number;
      safe_fix_count?: number;
      advisory_count?: number;
      manual_count?: number;
    })
  | (QcEventBase & {
      type: "qc_complete";
      run_id?: string;
      execution_status?: string;
      finding_count?: number;
      refuted_count?: number;
      disputed_count?: number;
      inconclusive_count?: number;
      open_criticals?: number;
      restored?: boolean;
    })
  | (QcEventBase & {
      type: "qc_failed";
      error?: string;
      error_kind?: "auth_error" | "";
      /** Stop won the runner race, but already-paid work is still attaching. */
      settling?: boolean;
    })
  | (QcEventBase & {
      type: "qc_attempt_settled";
      run_id?: string;
      status?: string;
      execution_status?: string;
      report_available?: boolean;
      finding_count?: number;
      refuted_count?: number;
      disputed_count?: number;
      inconclusive_count?: number;
    })
  | (QcEventBase & {
      type: "stream_end";
      status?: QcRunStatus | "superseded";
      run_id?: string;
    });

export interface QcSnapshot {
  status: QcRunStatus;
  error: string;
  error_kind?: "auth_error" | "";
  /** Stop was requested but the worker is still attaching paid activity. */
  settling?: boolean;
  events: QcEvent[];
  /** Retained result that owns the actionable apply/dismiss queue. */
  result?: QcResultView;
  stale?: boolean;
  /** Backend-selected primary audit report: latest attempt report when
   * available, otherwise the retained actionable result. */
  report?: QcResultView;
  report_stale?: boolean;
  report_is_latest_attempt?: boolean;
  latest_attempt?: QcAttemptSnapshot | null;
  /** Advisory comparison between the active section and a curated module's
   * closed catalog. A mismatch requires explicit acknowledgement to run QC. */
  module_section_compatibility?: QcModuleSectionCompatibility;
}

export interface QcAttemptSnapshot {
  run_id: string;
  status: string;
  error: string;
  error_kind?: "auth_error" | "";
  started_at: string;
  finished_at: string;
  report_available: boolean;
}

export interface QcApplyResult extends DocPayload {
  ok: boolean;
  outcomes: Record<string, string>;
}

export interface QcApplyPreviewBasis {
  workspace_id: number;
  generation: number;
  run_id: string;
  input_fingerprint: string;
  document_version: number;
  document_fingerprint: string;
  result_fingerprint: string;
  selected_finding_ids: string[];
  binding_fingerprint: string;
}

export type QcApplyPreviewOutcome =
  | "applyable"
  | "unknown"
  | "no_ops"
  | "already_applied"
  | "not_open"
  | "conflict"
  | "stale"
  | "source_blocked";

export interface QcApplyPreviewDecision {
  finding_id: string;
  title: string;
  severity: string;
  status: string;
  outcome: QcApplyPreviewOutcome;
  reason_code: string;
  reason: string;
  applyable: boolean;
  proposed_operation_count: number;
  apply_operation_count: number;
  duplicate_operation_count: number;
  conflicts_with: string[];
}

export interface QcApplyPreviewResult {
  ok: true;
  basis: QcApplyPreviewBasis;
  decisions: QcApplyPreviewDecision[];
  operation_counts: {
    proposed: number;
    unique: number;
    duplicate: number;
    applyable: number;
  };
  deduplications: {
    operation: Record<string, unknown>;
    finding_ids: string[];
    occurrence_count: number;
  }[];
  conflicts: {
    write_keys: string[];
    finding_ids: string[];
    operations: Record<string, unknown>[];
  }[];
  applyable_finding_ids: string[];
  applyable_operations: Record<string, unknown>[];
}

/* --- Issue readiness checklist (Batch 4) --- */

export interface ReadinessCheck {
  id: string;
  ok: boolean;
  detail: string;
  /** Shown but does not gate `ready`. */
  advisory: boolean;
  /** This check RESTATES others rather than adding a fact —
   *  `qc_audit_complete` is the conjunction of `qc_execution_complete` and
   *  `no_open_qc_findings`. It still gates (so it is not `advisory`), but a
   *  surface listing "what is blocking issue" must exclude it or one defect
   *  reads as two blockers with identical detail. */
  derived?: boolean;
}

export interface ReadinessPayload {
  checks: ReadinessCheck[];
  ready: boolean;
}

export interface UpdateCheckPayload {
  status: string;
  current: string;
  releases_url?: string;
  platform_supported?: boolean;
  version?: string;
  notes?: string;
  error?: string;
  /** True when the answer came from the last check rather than the network. */
  cached?: boolean;
}

export interface ReleaseItem {
  title: string;
  body: string;
}

export interface ReleaseSection {
  title: string;
  items: ReleaseItem[];
}

export interface ReleaseNote {
  version: string;
  date: string;
  headline: string;
  summary: string;
  sections: ReleaseSection[];
}

export interface ReleaseNotesPayload {
  ok: boolean;
  current: string;
  last_seen: string;
  /** True only on the launch check, when there is something unseen to show. */
  pending: boolean;
  entries: ReleaseNote[];
}

export interface ProjectLoadResult extends DocPayload {
  chat: { role: Role; text: string }[];
}

/* --- Reusable spec starters (templates) --- */

export type TemplateSource = "curated" | "personal";

export interface TemplateSummary {
  id: string;
  name: string;
  description: string;
  source: TemplateSource;
  module_id: string;
  discipline: string;
  project_type: string;
  section_number: string;
  section_title: string;
  article_count: number;
  paragraph_count: number;
  updated_at?: string;
  preview: string;
  editable: boolean;
  module_available: boolean;
}

export interface TemplatePreviewResult {
  ok: boolean;
  preview_token: string;
  template: TemplateSummary;
  document: SpecDoc;
  diff?: SectionDiffPayload;
  /** AI-generalization may be accepted asynchronously by older servers. */
  status?: "complete" | "running" | "failed" | string;
  error?: string;
}

/** Session hydration returned by tutorial and template transitions. */
export type SessionBundle = Partial<DocPayload> & {
  doc?: SpecDoc;
  chat?: { role: Role; text: string }[];
  messages?: { role: Role; text: string }[];
  workspace_id?: number;
  workspace_scope?: "original" | "tutorial" | "scenario";
  generation?: number;
  tutorial_id?: string;
  scenario_kind?: string;
  tutorial_source?: TutorialSource;
  research?: ResearchSnapshot;
  qc?: QcSnapshot;
  readiness?: ReadinessPayload;
  usage?: UsageSummary;
  health?: Health;
  /** Warning returned while instantiating a template (for example, generic
   * module fallback when its original curated module is not installed). */
  template_warning?: string;
  /** Defensive compatibility with an initially nested implementation. */
  doc_payload?: Partial<DocPayload>;
};

/* --- Full guided tutorial workspace --- */

/** The bundled showcase is the tutorial's only source. */
export type TutorialSource = "showcase";

export interface TutorialCoverage {
  ready: boolean;
  gaps: string[];
  anchors: Record<string, string>;
  counts: Record<string, number>;
  doc_version: number;
}

export interface TutorialStatusPayload {
  ok: boolean;
  active: boolean;
  tutorial_id?: string;
  workspace_id?: number;
  generation?: number;
  scope?: "original" | "tutorial" | "scenario";
  source?: TutorialSource;
  coverage?: TutorialCoverage;
  chapter?: string;
  scenario_kind?: string;
  session?: SessionBundle;
}

export interface TutorialStartPayload {
  ok: boolean;
  tutorial_id: string;
  workspace_id: number;
  generation: number;
  source: TutorialSource;
  coverage?: TutorialCoverage;
  session: SessionBundle;
}

/** Aggregated billed usage for one turn (all continuation rounds). */
export interface TurnUsage {
  input_tokens?: number;
  output_tokens?: number;
  cache_creation_input_tokens?: number;
  cache_read_input_tokens?: number;
  thinking_tokens?: number;
  web_search_requests?: number;
  web_fetch_requests?: number;
}

export type StreamEvent =
  | { type: "text_delta"; text: string }
  | { type: "thinking_delta"; text: string }
  | {
      type: "status";
      kind: StatusKind;
      round?: number;
      progress_chars?: number;
    }
  | { type: "web_search"; query: string }
  | { type: "web_fetch"; url: string }
  | { type: "figure"; figure: Figure }
  | { type: "suggested_prompts"; prompts: string[] }
  | { type: "qc_dispositions"; outcomes: Record<string, string> }
  | { type: "doc_patch"; ops: DocOp[]; doc: SpecDoc }
  | { type: "doc_snapshot"; doc: SpecDoc }
  | { type: "open_questions"; items: OpenItem[] }
  | { type: "lint"; items: LintIssue[]; standards: StandardInfo[] }
  | { type: "turn_complete"; stop_reason: string | null; usage?: TurnUsage }
  | { type: "error"; message: string; kind?: "auth_error" };

// --- Developer tools / diagnostics ------------------------------------------

/** `GET /api/diagnostics` — environment + session snapshot. */
export interface DiagnosticsSnapshot {
  ok: boolean;
  schema_version: number;
  generated_at: number;
  process: {
    diagnostic_run_id: string;
    pid: number;
    parent_pid: number;
    started_at: number;
    uptime_seconds: number;
    capture_state: string;
    thread_count: number;
    timezone: string | null;
    utc_offset: string;
    current_run_marker: Record<string, unknown> | null;
    previous_run_marker: Record<string, unknown> | null;
  };
  server?: {
    host: string;
    bound_port: number;
    boot_nonce_fingerprint: string;
  };
  app: {
    name: string;
    version: string;
    platform: string;
    python: string;
    frozen: boolean;
    dev_mode: boolean;
    port: number;
    models: { interview: string; research: string; qc: string };
  };
  tracing: {
    enabled: boolean;
    initialization_attempted: boolean;
    initialization_succeeded: boolean | null;
    capture_active: boolean;
    startup_failure: {
      exception_type: string;
      last_failed_at: number;
      count: number;
    } | null;
    level: string;
    root: string;
    run_id?: string;
    run_dir?: string;
    retention_policy: {
      max_runs: number;
      max_age_days: number;
      max_bytes: number;
    };
    metadata_flush_complete?: boolean;
    metadata_size_bytes?: number;
    metadata_omitted_reason?: string;
    trace_schema_version?: number;
    process_instance_id?: string;
    summary?: {
      coverage?: string;
      records_enqueued?: number;
      events_total?: number;
      failed_events?: number;
      spans_closed?: number;
      failed_spans?: number;
      prompts_stored?: number;
      includes_estimated_turn_usage?: boolean;
      last_recorded_at?: number | null;
      events_by_type: Record<string, number>;
      event_status_counts: Record<string, number>;
      request_outcome_counts: Record<string, number>;
      spans_by_kind: Record<string, number>;
      spans_by_status: Record<string, number>;
      turn_usage_totals: Record<string, number>;
    };
    recorder_health?: {
      state?: string;
      records_written?: number;
      dropped_records?: number;
      queue_count_drops?: number;
      queue_byte_drops?: number;
      run_byte_limit_drops?: number;
      serialization_failures?: number;
      unknown_file_drops?: number;
      write_failure_drops?: number;
      last_drop_reason?: string | null;
      write_failures?: number;
      metadata_write_failures?: number;
      queue_depth?: number;
      control_queue_depth?: number;
      queue_max_records?: number;
      queue_high_watermark?: number;
      queue_max_bytes?: number;
      queue_payload_bytes?: number;
      resident_payload_bytes?: number;
      inflight_payload_bytes?: number;
      queue_payload_high_watermark?: number;
      max_run_bytes?: number;
      data_bytes_written?: number;
      storage_limit_scope?: string;
      storage_limit_reached?: boolean;
      metadata_revision?: number;
      metadata_checkpointed_revision?: number;
      metadata_dirty?: boolean;
      thread_alive?: boolean;
      open_spans?: number;
      drain_timed_out?: boolean;
      fatal_error?: string | null;
      last_write_at?: number | null;
      captured_at?: number;
      open_spans_by_kind: Record<string, number>;
    };
  };
  logging: {
    enabled: boolean;
    initialization_attempted: boolean;
    initialization_succeeded: boolean | null;
    handler_attached: boolean;
    capture_active: boolean;
    initialization_failure_type: string | null;
    initialization_failure_count: number;
    initialization_last_failure_at: number | null;
    level: string;
    dir: string;
    run_dir: string;
    run_id: string;
    retention_policy: {
      max_runs: number;
      max_age_days: number;
      max_bytes: number;
    };
    last_retention: Record<string, unknown>;
    file?: string;
    size_bytes?: number;
  };
  key: { present: boolean; source: string; masked: string; env_locked?: boolean };
  workspace: {
    workspace_id: number;
    scope: string;
    generation: number;
    busy: string[];
  };
  session: {
    history_len: number;
    doc_version_index: number;
    doc_version_count: number;
    baseline_index: number | null;
    doc_empty: boolean;
    document_shape: {
      parts: number;
      articles: number;
      paragraphs: number;
      maximum_paragraph_depth: number;
    };
    can_undo: boolean;
    can_redo: boolean;
    turn_transaction_open: boolean;
    turn_transaction_dirty: boolean;
    figures: number;
    references: number;
    suggested_prompts: number;
    turn_active: boolean;
    stop_requested: boolean;
    last_context_tokens: number | null;
    unsaved: boolean;
    import_report_present: boolean;
    module_id: string;
    discipline: string;
    research: {
      status: string;
      worker_alive: boolean;
      event_count: number;
      active_round: number;
      error_present: boolean;
      error_kind: string | null;
      rounds: number;
      dimension_count: number;
      incomplete_dimensions: unknown[];
    };
    audit: {
      status: string;
      worker_alive: boolean;
      error_present: boolean;
      result_present: boolean;
      version_index: number | null;
      findings: number;
    };
    qc: {
      status: string;
      worker_alive: boolean;
      worker_settled: boolean;
      event_count: number;
      error_present: boolean;
      error_kind: string | null;
      retained_result: Record<string, unknown> | null;
      latest_attempt: Record<string, unknown>;
    };
    import: {
      present: boolean;
      filename?: string;
      sha256?: string;
      size_bytes?: number;
      zip_member_count?: number;
      zip_uncompressed_bytes?: number;
      imported_block_count?: number;
      skipped_empty_count?: number;
      tracked_changes_detected?: boolean;
      spec_shape_detected?: boolean;
      fidelity_notice?: string;
      warning_count: number;
      warning_code_counts: Record<string, number>;
      warning_evidence: unknown[];
      warning_evidence_truncated: number;
    };
    source: {
      retained: boolean;
      filename: string;
      bytes: number;
      map_present: boolean;
      patch_context_present: boolean;
      capability_cache_present: boolean;
      capability_analysis_running: boolean;
      capability_analysis_queued: boolean;
      capabilities_status: string | null;
      capabilities_snapshot_source: string;
      edit_blockers: Record<string, number>;
      global_edit_blockers: {
        causes: string[];
        denial_counts: Record<string, number>;
        origins: Record<string, string[]>;
      };
      per_operation_edit_blockers: Record<string, number>;
      capability_operation_counts: {
        elements: number;
        total: number;
        allowed: number;
        denied: number;
      };
    };
  };
  usage: UsageSummary;
}

/** `GET /api/diagnostics/log` — activity-log tail. */
export interface DiagnosticsLog {
  ok: boolean;
  enabled: boolean;
  path: string | null;
  size_bytes: number;
  lines: string[];
}

export interface DiagnosticsTraceRun {
  run_id: string;
  started_at: number | null;
  ended_at: number | null;
  current: boolean;
  owner_pid: number | null;
  owner_process_alive: boolean;
  size_bytes: number;
  files: Record<string, number>;
}

/** `GET /api/diagnostics/traces` — run inventory, newest first. */
export interface DiagnosticsTraces {
  ok: boolean;
  root: string;
  runs: DiagnosticsTraceRun[];
}

/** One events.jsonl record: ts/span_id/type plus event-specific fields. */
export interface DiagnosticsEvent {
  ts: number;
  span_id: string;
  type: string;
  [key: string]: unknown;
}

export interface DiagnosticsOpenSpan {
  span_id: string;
  kind: string;
  name: string;
  started_at: number;
  parent_span_id: string | null;
}

/** `GET /api/diagnostics/activity` — current run's recent events. */
export interface DiagnosticsActivity {
  ok: boolean;
  enabled: boolean;
  run_id?: string;
  events: DiagnosticsEvent[];
  spans: DiagnosticsOpenSpan[];
}

/**
 * Native bridge surfaced by the pywebview shell (undefined in a plain
 * browser / dev). `pywebview.api` exposes the close controller's methods;
 * `buildaspecRequestClose` is the hook the shell calls when the user tries
 * to close the window so the app can offer to save first.
 */
declare global {
  interface Window {
    pywebview?: {
      api?: {
        save_and_close?: () => Promise<void>;
        discard_and_close?: () => Promise<void>;
        /** Save without closing: the panel's Save button and the
         *  New-session / Open-project save gate. Asks where to save the
         *  first time this session is saved, then overwrites that file
         *  silently on every later save. */
        save_project?: () => Promise<SaveProjectResult>;
        /** "Save as…": always ask where, and make that the file later saves
         *  overwrite. Only offered once a target exists — before the first
         *  save, plain Save already asks. */
        save_project_as?: () => Promise<SaveProjectResult>;
        /** Native Open dialog (the panel's Open / Import buttons). HTML file
         *  inputs don't reliably deliver bytes to JS in the webview, so the
         *  shell reads the picked file and returns it for the normal upload
         *  path. Resolves to the file's name + base64 bytes, or null when the
         *  dialog was cancelled. `kind` picks the file filter. */
        open_file?: (
          kind: "project" | "docx" | "reference" | "template",
        ) => Promise<{ name: string; data_b64: string } | null>;
        /** Native Save dialog for a portable reusable starter. */
        save_template?: (templateId: string) => Promise<boolean>;
        /** Opens a URL in the user's default system browser instead of
         *  navigating the app window itself. Resolves true if a browser was
         *  launched; false for a rejected (non-http/https) or malformed URL.
         *  Also how Developer tools opens the trace viewer (an app-served
         *  localhost URL) — the shell has no reliable target=_blank. */
        open_external_link?: (url: string) => Promise<boolean>;
      };
    };
    buildaspecRequestClose?: (
      reason?: "tutorial-busy" | "tutorial-restored",
    ) => void;
  }
}
