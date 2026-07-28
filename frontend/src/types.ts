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
  workspace_id?: number;
  workspace_scope?: "original" | "tutorial" | "scenario";
  generation?: number;
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
   * Context gauge, not spend: the Anthropic-counted conversation size after
   * the last committed chat turn (system prompt + tools + history + project
   * context + the retained reply), against the model's context window. null
   * until a turn commits (fresh session, reset, or a just-loaded project).
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
export interface SourceCapabilitiesState {
  status: SourceCapabilityStatus;
  elements: Record<string, SourceElementCapabilities>;
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

export interface DocPayload {
  doc: SpecDoc;
  open_questions: OpenItem[];
  lint: LintIssue[];
  standards: StandardInfo[];
  profile_complete: boolean;
  research_status: ResearchRunStatus;
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
  status?: ResearchRunStatus;
  restored?: boolean;
  /** 1-based research round this event belongs to (rounds append). */
  round?: number;
  /** On `research_complete`: this round's own contribution, where
   *  `item_count` is the cumulative total the session now holds. */
  round_item_count?: number;
  new_item_count?: number;
  repeat_item_count?: number;
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

export interface ResearchSnapshot {
  status: ResearchRunStatus;
  error: string;
  events: ResearchEvent[];
  profile?: ResearchProfileView;
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
    | "default_refuted"
    | "inconclusive"
    | string;
  verification_panel_size: number;
  verification_threshold: number;
  status: QcFindingStatus;
  dismiss_reason: string;
  disposition_events: QcDispositionEvent[];
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
  /** Candidates lacking enough completed verifier seats for a substantive
   *  uphold/refute decision. Infrastructure-inconclusive, never open issues. */
  inconclusive: QcFinding[];
  lens_statuses: QcLensStatus[];
  started_at: string;
  finished_at: string;
  version_index: number;
  version_fingerprint?: string;
  input_fingerprint: string;
  input_manifest: Record<string, unknown>;
  model: string;
  effort: string;
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

export interface QcEvent {
  seq: number;
  ts: string;
  type: string;
  lens_id?: string;
  title?: string;
  finding_count?: number;
  grounded_count?: number;
  error?: string;
  done?: number;
  total?: number;
  lenses?: { lens_id: string; title: string }[];
  open_criticals?: number;
  status?: QcRunStatus;
  run_id?: string;
  protocol_version?: string;
  execution_status?: string;
}

export interface QcSnapshot {
  status: QcRunStatus;
  error: string;
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
  started_at: string;
  finished_at: string;
  report_available: boolean;
}

export interface QcApplyResult extends DocPayload {
  ok: boolean;
  outcomes: Record<string, string>;
}

/* --- Issue readiness checklist (Batch 4) --- */

export interface ReadinessCheck {
  id: string;
  ok: boolean;
  detail: string;
  advisory: boolean;
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
  tutorial_source?: "current" | "generated" | "showcase";
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

export type TutorialSource = "current" | "generated" | "showcase";

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
  needs_enrichment: boolean;
  coverage?: TutorialCoverage;
  session: SessionBundle;
}

interface TutorialEventMeta {
  workspace_id?: number;
  generation?: number;
}

export type TutorialEvent =
  | (StreamEvent & TutorialEventMeta)
  | (TutorialEventMeta & {
      type: "tutorial_coverage";
      coverage: TutorialCoverage;
    })
  | (TutorialEventMeta & {
      type: "tutorial_fallback";
      /** Identity of the lease being replaced; must match the caller's
       * expected workspace before the replacement session is accepted. */
      replaces_workspace_id?: number;
      replaces_generation?: number;
      reason?: string;
      source?: TutorialSource;
      session: SessionBundle;
      coverage?: TutorialCoverage;
    })
  | (TutorialEventMeta & {
      type: "tutorial_session";
      source?: TutorialSource;
      session: SessionBundle;
    });

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
  | { type: "doc_patch"; ops: DocOp[]; doc: SpecDoc }
  | { type: "doc_snapshot"; doc: SpecDoc }
  | { type: "open_questions"; items: OpenItem[] }
  | { type: "lint"; items: LintIssue[]; standards: StandardInfo[] }
  | { type: "turn_complete"; stop_reason: string | null; usage?: TurnUsage }
  | { type: "error"; message: string };

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
        /** In-app save without closing (the New-session / Open-project save
         *  gate); resolves true when a file was written, false if the native
         *  Save dialog was cancelled. */
        save_project?: () => Promise<boolean>;
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
         *  launched; false for a rejected (non-http/https) or malformed URL. */
        open_external_link?: (url: string) => Promise<boolean>;
      };
    };
    buildaspecRequestClose?: (
      reason?: "tutorial-busy" | "tutorial-restored",
    ) => void;
  }
}
