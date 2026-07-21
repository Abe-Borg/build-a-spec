export type Role = "user" | "assistant";

export interface ChatMessage {
  id: string;
  role: Role;
  text: string;
  streaming?: boolean;
  error?: boolean;
}

export interface Health {
  status: string;
  app: string;
  version: string;
  model: string;
  api_key_present: boolean;
  module?: string;
  module_id?: string;
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
    | "set_standard_edition";
  id: string;
  target_id?: string;
  status?: BlockStatus;
  standard?: string;
  edition?: string;
  removed?: boolean;
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

export interface DocPayload {
  doc: SpecDoc;
  open_questions: OpenItem[];
  lint: LintIssue[];
  standards: StandardInfo[];
  profile_complete: boolean;
  research_status: ResearchRunStatus;
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

export type StreamEvent =
  | { type: "text_delta"; text: string }
  | { type: "doc_patch"; ops: DocOp[]; doc: SpecDoc }
  | { type: "doc_snapshot"; doc: SpecDoc }
  | { type: "open_questions"; items: OpenItem[] }
  | { type: "lint"; items: LintIssue[]; standards: StandardInfo[] }
  | { type: "turn_complete"; stop_reason: string | null }
  | { type: "error"; message: string };
