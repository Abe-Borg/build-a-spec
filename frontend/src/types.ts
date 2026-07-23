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

export interface Health {
  status: string;
  app: string;
  version: string;
  model: string;
  api_key_present: boolean;
  module?: string;
  module_id?: string;
  /** Non-empty only while the generic open-catalog module is active (Batch 10). */
  discipline?: string;
}

/** One selectable spec module (Batch 10 session-start picker). */
export interface ModuleInfo {
  module_id: string;
  display_name: string;
  description: string;
  /** Open-catalog module — a session on it needs a stated discipline. */
  generic: boolean;
  default: boolean;
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
    | "replace"
    | "delete"
    | "set_status"
    | "set_standard_edition";
  id: string;
  target_id?: string;
  status?: BlockStatus;
  standard?: string;
  edition?: string;
  removed?: boolean;
}

/** A manual edit op sent to POST /api/doc/edit (WI2; set_project_profile added
 * for the panel's project-profile form and the tour's deterministic fill). */
export interface EditOp {
  action:
    | "replace"
    | "delete"
    | "set_status"
    | "add_paragraph"
    | "set_project_profile";
  target_id: string;
  text?: string;
  status?: BlockStatus;
  source_item_id?: string;
  /** set_project_profile fields (target_id must be "sec") — provide only
   * the ones being changed; an explicit empty string clears that field. */
  city?: string;
  state?: string;
  country?: string;
  client?: string;
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

/** One standard's edition in effect: module pin or jurisdiction override. */
export interface StandardInfo {
  name: string;
  edition: string;
  title: string;
  is_override: boolean;
  basis: string;
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

export interface ResearchProfileView {
  research_date: string;
  project: Record<string, string>;
  dimension_statuses: ResearchDimensionView[];
  items: ResearchItemView[];
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

export interface QcVerdict {
  upholds: boolean;
  revised_severity: string;
  note: string;
}

export interface QcFinding {
  finding_id: string;
  lens_id: string;
  severity: Severity;
  element_id: string;
  title: string;
  issue: string;
  rationale: string;
  source_urls: string[];
  accepted_sources: string[];
  grounded: boolean;
  proposed_ops: Record<string, unknown>[];
  ops_valid: boolean;
  ops_invalid_reason: string;
  verdicts: QcVerdict[];
  status: QcFindingStatus;
  dismiss_reason: string;
}

export interface QcLensStatus {
  lens_id: string;
  title: string;
  status: string;
  finding_count: number;
  grounded_count: number;
  error: string;
}

export interface QcResultView {
  summary: string;
  findings: QcFinding[];
  refuted: QcFinding[];
  lens_statuses: QcLensStatus[];
  started_at: string;
  finished_at: string;
  version_index: number;
  model: string;
  usage_totals: Record<string, number>;
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
}

export interface QcSnapshot {
  status: QcRunStatus;
  error: string;
  events: QcEvent[];
  result?: QcResultView;
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
      };
    };
    buildaspecRequestClose?: () => void;
  }
}
