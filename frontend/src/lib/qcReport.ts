import type {
  QcFinding,
  QcLensStatus,
  QcResultView,
  QcSnapshot,
  QcVerdict,
} from "../types";

/**
 * Pure presentation helpers for the audit-grade Final QC report.
 *
 * Keep this module free of React and browser globals. Besides making the
 * report component easier to read, that lets the accounting and safety rules
 * be exercised directly by Node tests.
 */

export const QC_SEVERITY_ORDER = [
  "critical",
  "high",
  "medium",
  "low",
] as const;

/** Schema-v2 protocol lenses; the persisted manifest is authoritative. */
export const QC_V2_LENS_IDS = [
  "code_compliance",
  "coordination_consistency",
  "completeness",
  "enforceability_language",
  "provenance_hygiene",
] as const;

/** Compatibility alias used by existing report consumers/tests. */
export const EXPECTED_QC_LENS_IDS = QC_V2_LENS_IDS;

export type KnownQcSeverity = (typeof QC_SEVERITY_ORDER)[number];
export type ReportSeverity = KnownQcSeverity | "unknown";

export interface QcRetrievedSourceRecord {
  url: string;
  title?: string;
  methods?: string[];
  normalized?: string;
  accepted?: boolean | null;
  reason?: string;
}

export interface QcReviewedCheckRecord {
  check: string;
  outcome: string;
  notes?: string;
  element_ids?: string[];
  source_urls?: string[];
  source_checks?: QcSourceCheckRecord[];
}

export interface QcSourceCheckRecord {
  url: string;
  normalized?: string;
  accepted?: boolean | null;
  reason?: string;
  title?: string;
  methods?: string[];
}

export interface QcDispositionEventRecord {
  action: string;
  at: string;
  reason?: string;
  document_version?: number;
  document_fingerprint?: string;
}

export type QcReportVerdict = QcVerdict & {
  status?: string;
  error?: string;
  reviewer_index?: number;
  search_queries?: string[];
  retrieved_sources?: QcRetrievedSourceRecord[];
  attempted_search_queries?: string[];
  attempted_sources?: QcRetrievedSourceRecord[];
  usage_totals?: Record<string, number>;
  estimated_cost_usd?: number;
  api_request_count?: number;
  model_response_count?: number;
};

export type QcReportFinding = Omit<QcFinding, "verdicts"> & {
  verdicts: QcReportVerdict[];
  original_severity?: string;
  reviewed_ref?: string;
  reviewed_text?: string;
  element_resolved?: boolean;
  source_checks?: QcSourceCheckRecord[];
  verification_outcome?: string;
  verification_threshold?: number;
  verification_panel_size?: number;
  disposition_events?: QcDispositionEventRecord[];
};

export type QcReportLens = QcLensStatus & {
  brief?: string;
  summary?: string;
  search_queries?: string[];
  retrieved_sources?: QcRetrievedSourceRecord[];
  attempted_search_queries?: string[];
  attempted_sources?: QcRetrievedSourceRecord[];
  usage_totals?: Record<string, number>;
  estimated_cost_usd?: number;
  reviewed_checks?: QcReviewedCheckRecord[];
  api_request_count?: number;
  model_response_count?: number;
};

export type QcReportResult = Omit<
  QcResultView,
  "findings" | "refuted" | "inconclusive" | "lens_statuses"
> & {
  findings: QcReportFinding[];
  refuted: QcReportFinding[];
  inconclusive: QcReportFinding[];
  lens_statuses: QcReportLens[];
  schema_version?: string | number;
  protocol_version?: string | number;
  run_id?: string;
  execution_status?: string;
  effort?: string;
  max_tokens?: number;
  duration_ms?: number;
  input_fingerprint?: string;
  input_manifest?: Record<string, unknown>;
  estimated_cost_usd?: number;
  cost_basis?: Record<string, unknown>;
  api_request_count?: number;
  model_response_count?: number;
};

export interface SeverityCounts {
  critical: number;
  high: number;
  medium: number;
  low: number;
  unknown: number;
}

export interface QcReportMetrics {
  totalCandidates: number;
  survivingFindings: number;
  refutedFindings: number;
  inconclusiveFindings: number;
  openFindings: number;
  appliedFindings: number;
  dismissedFindings: number;
  otherDispositionFindings: number;
  groundedFindings: number;
  ungroundedFindings: number;
  groundedCandidates: number;
  ungroundedCandidates: number;
  resolvedElementAnchors: number;
  unresolvedElementAnchors: number;
  unrecordedElementResolution: number;
  completedLenses: number;
  failedLenses: number;
  otherStatusLenses: number;
  totalLenses: number;
  expectedLenses: number;
  missingLenses: number;
  unexpectedLenses: number;
  duplicateLensRecords: number;
  lensesWithoutReviewedChecks: number;
  lensCoverageComplete: boolean;
  searchQueries: number;
  retrievedSourceRecords: number;
  evidenceTraceRecords: number;
  uniqueSourceUrls: number;
  sourceChecks: number;
  acceptedSourceChecks: number;
  rejectedSourceChecks: number;
  unclassifiedSourceChecks: number;
  verdicts: number;
  upholdingVerdicts: number;
  refutingVerdicts: number;
  verdictErrors: number;
  expectedVerifierSeats: number;
  recordedVerifierSeats: number;
  completedVerifierSeats: number;
  failedVerifierSeats: number;
  missingVerifierSeats: number;
  invalidVerifierSeatRecords: number;
  candidatesWithPanelSize: number;
  candidatesWithVerificationOutcome: number;
  proposedOperations: number;
  unevaluatedRefutedOperations: number;
  unevaluatedInconclusiveOperations: number;
  findingsWithValidOperations: number;
  findingsWithInvalidOperations: number;
  apiRequests: number;
  modelResponses: number;
  severity: SeverityCounts;
  survivingSeverity: SeverityCounts;
  refutedSeverity: SeverityCounts;
  inconclusiveSeverity: SeverityCounts;
}

export interface QcTraceRecord {
  kind: "query" | "source";
  stage:
    | "lens"
    | "lens_attempt"
    | "finding"
    | "verification"
    | "verification_attempt";
  ownerId: string;
  value: string;
  title?: string;
  methods?: string[];
  accepted?: boolean | null;
  reason?: string;
  originalValue?: string;
}

export interface QcOperationRecord {
  findingId: string;
  findingTitle: string;
  findingKind: "surviving" | "refuted" | "inconclusive";
  operationIndex: number;
  opsValid: boolean;
  invalidReason: string;
  validationStatus: "valid" | "invalid" | "not_evaluated";
  operation: Record<string, unknown>;
}

export interface QcVerifierSeatCoverage {
  expected: number | null;
  recorded: number;
  completed: number;
  failed: number;
  missing: number;
  invalidIndexRecords: number;
  legacyUnnumbered: boolean;
}

export interface QcLensCoverage {
  schemaV2: boolean;
  expectedIds: string[];
  recordedIds: string[];
  completedIds: string[];
  missingIds: string[];
  unexpectedIds: string[];
  duplicateIds: string[];
  duplicateRecordCount: number;
  withoutReviewedChecks: string[];
  complete: boolean;
}

function finiteNumber(value: unknown): number | undefined {
  if (typeof value !== "number" || !Number.isFinite(value)) return undefined;
  return value;
}

function schemaVersionNumber(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return undefined;
}

function arrayOrEmpty<T>(value: T[] | undefined): T[] {
  return Array.isArray(value) ? value : [];
}

/** Legacy seats had no status; a preserved substantive verdict counts complete. */
export function isCompletedVerifierSeat(verdict: QcReportVerdict): boolean {
  if (verdict.error) return false;
  const status = String(verdict.status ?? "").trim().toLowerCase();
  return (
    !status ||
    ["complete", "completed", "success", "succeeded"].includes(status)
  );
}

/** A preserved seat can exist without a usable verdict (failure/cancellation). */
export function isFailedVerifierSeat(verdict: QcReportVerdict): boolean {
  return !isCompletedVerifierSeat(verdict);
}

/** Reproduce the persisted panel-completeness rules without mutating data. */
export function verifierSeatCoverage(
  finding: QcReportFinding,
  schemaVersion?: unknown,
): QcVerifierSeatCoverage {
  const rawExpected = finiteNumber(finding.verification_panel_size);
  const expected =
    rawExpected != null && Number.isInteger(rawExpected) && rawExpected > 0
      ? rawExpected
      : null;
  const recorded = finding.verdicts.length;
  const failed = finding.verdicts.filter(isFailedVerifierSeat).length;
  if (expected == null) {
    const schema = schemaVersionNumber(schemaVersion);
    return {
      expected,
      recorded,
      completed: recorded - failed,
      failed,
      missing: 0,
      invalidIndexRecords: 0,
      legacyUnnumbered:
        (schema == null || schema < 2) &&
        finding.verdicts.length > 0 &&
        finding.verdicts.every(
          (verdict) => (finiteNumber(verdict.reviewer_index) ?? 0) === 0,
        ),
    };
  }

  const indexes = finding.verdicts.map(
    (verdict) => finiteNumber(verdict.reviewer_index) ?? 0,
  );
  const allUnnumbered = indexes.length > 0 && indexes.every((index) => index === 0);
  const schema = schemaVersionNumber(schemaVersion);
  const legacyUnnumbered = allUnnumbered && (schema == null || schema < 2);
  if (legacyUnnumbered) {
    return {
      expected,
      recorded,
      completed: finding.verdicts.filter(isCompletedVerifierSeat).length,
      failed,
      missing: Math.max(0, expected - recorded),
      invalidIndexRecords: Math.max(0, recorded - expected),
      legacyUnnumbered,
    };
  }
  const validIndexes = new Set(
    indexes.filter((index) => Number.isInteger(index) && index >= 1 && index <= expected),
  );
  const completedIndexes = new Set(
    indexes.filter(
      (index, recordIndex) =>
        Number.isInteger(index) &&
        index >= 1 &&
        index <= expected &&
        isCompletedVerifierSeat(finding.verdicts[recordIndex]),
    ),
  );
  return {
    expected,
    recorded,
    completed: completedIndexes.size,
    failed,
    missing: Math.max(0, expected - validIndexes.size),
    invalidIndexRecords: Math.max(0, recorded - validIndexes.size),
    legacyUnnumbered,
  };
}

/** Normalize a wire severity without ever coercing an unknown value to low. */
export function normalizeSeverity(value: unknown): ReportSeverity {
  if (typeof value !== "string") return "unknown";
  const normalized = value.trim().toLowerCase();
  return (QC_SEVERITY_ORDER as readonly string[]).includes(normalized)
    ? (normalized as KnownQcSeverity)
    : "unknown";
}

/** Human label that keeps the original future/invalid value visible. */
export function formatSeverity(value: unknown): string {
  const normalized = normalizeSeverity(value);
  if (normalized !== "unknown") {
    return normalized[0].toUpperCase() + normalized.slice(1);
  }
  const raw = typeof value === "string" ? value.trim() : "";
  if (raw.toLowerCase() === "unknown") return "Unknown";
  return raw ? `Unknown (${raw})` : "Unknown";
}

export function severityRank(value: unknown): number {
  const severity = normalizeSeverity(value);
  const rank = QC_SEVERITY_ORDER.indexOf(severity as KnownQcSeverity);
  return rank === -1 ? QC_SEVERITY_ORDER.length : rank;
}

export function sortFindingsBySeverity<T extends { severity: unknown }>(
  findings: readonly T[],
): T[] {
  return findings
    .map((finding, index) => ({ finding, index }))
    .sort(
      (a, b) =>
        severityRank(a.finding.severity) - severityRank(b.finding.severity) ||
        a.index - b.index,
    )
    .map(({ finding }) => finding);
}

export function countSeverities(
  findings: readonly { severity: unknown }[],
): SeverityCounts {
  const counts: SeverityCounts = {
    critical: 0,
    high: 0,
    medium: 0,
    low: 0,
    unknown: 0,
  };
  for (const finding of findings) counts[normalizeSeverity(finding.severity)] += 1;
  return counts;
}

export function groupFindingsBySeverity<T extends { severity: unknown }>(
  findings: readonly T[],
): { severity: ReportSeverity; findings: T[] }[] {
  const severities: readonly ReportSeverity[] = [
    ...QC_SEVERITY_ORDER,
    "unknown",
  ];
  const groups = new Map<ReportSeverity, T[]>(
    severities.map((severity): [ReportSeverity, T[]] => [severity, []]),
  );
  for (const finding of findings) {
    groups.get(normalizeSeverity(finding.severity))?.push(finding);
  }
  return severities
    .map((severity) => ({
      severity,
      findings: groups.get(severity) ?? [],
    }))
    .filter((group) => group.findings.length > 0);
}

/** Only absolute HTTP(S) URLs become clickable in the report. */
export function safeHttpUrl(value: unknown): string | null {
  if (typeof value !== "string" || !value.trim()) return null;
  try {
    const parsed = new URL(value.trim());
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return null;
    // Credentials are unnecessary for citations and are risky to expose or
    // transmit when a report link is clicked.
    if (parsed.username || parsed.password) return null;
    return parsed.href;
  } catch {
    return null;
  }
}

export function isSafeHttpUrl(value: unknown): boolean {
  return safeHttpUrl(value) !== null;
}

/** Pin a download to the report identity shown in the current snapshot. */
export function qcReportExportUrl(
  format: "docx" | "json",
  runId: unknown,
): string {
  const base = format === "json" ? "/api/qc/export.json" : "/api/qc/export";
  const normalized = typeof runId === "string" ? runId.trim() : "";
  return normalized ? `${base}?run_id=${encodeURIComponent(normalized)}` : base;
}

export function sourceHost(value: unknown): string {
  const safe = safeHttpUrl(value);
  if (!safe) return typeof value === "string" ? value : "";
  return new URL(safe).hostname.replace(/^www\./i, "");
}

export function formatInteger(value: unknown): string {
  const number = finiteNumber(value);
  return number == null ? "Not recorded" : Math.trunc(number).toLocaleString("en-US");
}

export function formatDecimal(value: unknown, maximumFractionDigits = 2): string {
  const number = finiteNumber(value);
  if (number == null) return "Not recorded";
  return number.toLocaleString("en-US", { maximumFractionDigits });
}

export function formatUsd(value: unknown): string {
  const number = finiteNumber(value);
  if (number == null) return "Not recorded";
  return `$${number.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  })} estimated`;
}

export function formatDuration(value: unknown): string {
  const number = finiteNumber(value);
  if (number == null || number < 0) return "Not recorded";
  if (number < 1_000) return `${Math.round(number).toLocaleString("en-US")} ms`;

  const totalSeconds = number / 1_000;
  if (totalSeconds < 60) {
    return `${totalSeconds.toLocaleString("en-US", {
      maximumFractionDigits: totalSeconds < 10 ? 2 : 1,
    })} s`;
  }

  const wholeSeconds = Math.round(totalSeconds);
  const hours = Math.floor(wholeSeconds / 3_600);
  const minutes = Math.floor((wholeSeconds % 3_600) / 60);
  const seconds = wholeSeconds % 60;
  const parts: string[] = [];
  if (hours) parts.push(`${hours} h`);
  if (minutes) parts.push(`${minutes} min`);
  if (seconds || parts.length === 0) parts.push(`${seconds} s`);
  return parts.join(" ");
}

/** Deterministic UTC display; invalid legacy values remain visible verbatim. */
export function formatTimestamp(value: unknown): string {
  if (typeof value !== "string" || !value.trim()) return "Not recorded";
  const raw = value.trim();
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return raw;
  return date.toISOString().replace("T", " ").replace(/\.000Z$/, " UTC");
}

export function formatFieldName(value: string): string {
  const words = value
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[_-]+/g, " ")
    .trim();
  return words
    .split(/\s+/)
    .filter(Boolean)
    .map((word) => word[0].toUpperCase() + word.slice(1))
    .join(" ");
}

/** Full, readable JSON. The replacer prevents unusual test fixtures crashing. */
export function formatJson(value: unknown): string {
  const seen = new WeakSet<object>();
  try {
    const rendered = JSON.stringify(
      value,
      (_key, item: unknown) => {
        if (typeof item === "bigint") return item.toString();
        if (item && typeof item === "object") {
          if (seen.has(item)) return "[Circular]";
          seen.add(item);
        }
        return item;
      },
      2,
    );
    return rendered === undefined ? String(value) : rendered;
  } catch {
    return String(value);
  }
}

export function describeOperation(operation: Record<string, unknown>): string {
  const action = String(operation.action ?? "unspecified action");
  const target = String(operation.target_id ?? operation.id ?? "section");
  return `${formatFieldName(action)} — ${target}`;
}

function resultFields(result: QcResultView | QcReportResult): QcReportResult {
  return result as QcReportResult;
}

function verificationOutcome(finding: QcReportFinding): string {
  return String(finding.verification_outcome ?? "").trim().toLowerCase();
}

function hasRecordedInfrastructureOutcome(finding: QcReportFinding): boolean {
  const outcome = verificationOutcome(finding);
  return outcome === "inconclusive" || outcome === "default_refuted";
}

/**
 * `default_refuted` was the legacy fail-closed label used when verifier
 * infrastructure did not produce a usable panel. It was not a substantive
 * refutation. Treat it, and any record already marked `inconclusive`, as
 * infrastructure-inconclusive regardless of the array where it was stored.
 */
export function isInfrastructureInconclusiveFinding(
  finding: QcReportFinding,
  schemaVersion?: unknown,
): boolean {
  if (hasRecordedInfrastructureOutcome(finding)) return true;
  const seats = verifierSeatCoverage(finding, schemaVersion);
  return (
    seats.failed > 0 ||
    (seats.expected != null &&
      (seats.completed < seats.expected ||
        seats.missing > 0 ||
        seats.invalidIndexRecords > 0))
  );
}

/** Backend-selected report takes precedence over the retained action queue. */
export function qcPrimaryReport(
  snapshot: Pick<QcSnapshot, "report" | "result"> | null | undefined,
): QcResultView | undefined {
  return snapshot?.report ?? snapshot?.result;
}

function findingIdentity(finding: QcReportFinding): string | null {
  const id = String(finding.finding_id ?? "").trim();
  return id ? `finding:${id}` : null;
}

function uniqueFindings(findings: QcReportFinding[]): QcReportFinding[] {
  const ids = new Set<string>();
  const objects = new Set<QcReportFinding>();
  return findings.filter((finding) => {
    const identity = findingIdentity(finding);
    if (identity) {
      if (ids.has(identity)) return false;
      ids.add(identity);
      return true;
    }
    if (objects.has(finding)) return false;
    objects.add(finding);
    return true;
  });
}

function findingMatchesBucket(
  finding: QcReportFinding,
  identities: Set<string>,
  objects: Set<QcReportFinding>,
): boolean {
  const identity = findingIdentity(finding);
  return identity ? identities.has(identity) : objects.has(finding);
}

function explicitInconclusiveIdentity(result: QcReportResult): {
  identities: Set<string>;
  objects: Set<QcReportFinding>;
} {
  const identities = new Set<string>();
  const objects = new Set<QcReportFinding>();
  for (const finding of arrayOrEmpty(result.inconclusive)) {
    const identity = findingIdentity(finding);
    if (identity) identities.add(identity);
    else objects.add(finding);
  }
  return { identities, objects };
}

/**
 * Effective actionable survivors. An infrastructure-inconclusive record is
 * excluded even if malformed or legacy data placed it in `findings`.
 */
export function qcSurvivingCandidates(
  rawResult: QcResultView | QcReportResult,
): QcReportFinding[] {
  const result = resultFields(rawResult);
  const explicit = explicitInconclusiveIdentity(result);
  return result.findings.filter(
    (finding) =>
      !isInfrastructureInconclusiveFinding(finding, result.schema_version) &&
      !findingMatchesBucket(finding, explicit.identities, explicit.objects),
  );
}

/** Effective substantive refutations, excluding all infrastructure failures. */
export function qcSubstantivelyRefutedCandidates(
  rawResult: QcResultView | QcReportResult,
): QcReportFinding[] {
  const result = resultFields(rawResult);
  const explicit = explicitInconclusiveIdentity(result);
  return result.refuted.filter(
    (finding) =>
      !isInfrastructureInconclusiveFinding(finding, result.schema_version) &&
      !findingMatchesBucket(finding, explicit.identities, explicit.objects),
  );
}

/**
 * Effective infrastructure-inconclusive collection. Explicit schema-v2
 * records take precedence; legacy/misbucketed records are appended and the
 * same candidate ID is counted only once.
 */
export function qcInconclusiveCandidates(
  rawResult: QcResultView | QcReportResult,
): QcReportFinding[] {
  const result = resultFields(rawResult);
  return uniqueFindings([
    ...arrayOrEmpty(result.inconclusive),
    ...result.findings.filter((finding) =>
      isInfrastructureInconclusiveFinding(finding, result.schema_version),
    ),
    ...result.refuted.filter((finding) =>
      isInfrastructureInconclusiveFinding(finding, result.schema_version),
    ),
  ]);
}

function allQcCandidates(result: QcReportResult): QcReportFinding[] {
  return qcSurvivingCandidates(result).concat(
    qcSubstantivelyRefutedCandidates(result),
    qcInconclusiveCandidates(result),
  );
}

function manifestLensIds(manifest: Record<string, unknown> | undefined): string[] {
  const configuration = manifest?.configuration;
  if (!configuration || typeof configuration !== "object" || Array.isArray(configuration)) {
    return [];
  }
  const lenses = (configuration as Record<string, unknown>).lenses;
  if (!Array.isArray(lenses)) return [];
  return lenses
    .map((lens) => {
      if (!lens || typeof lens !== "object" || Array.isArray(lens)) return "";
      const lensId = (lens as Record<string, unknown>).lens_id;
      return typeof lensId === "string" ? lensId.trim() : "";
    })
    .filter(Boolean);
}

/** Exact lens-set accounting used by metrics and run limitations. */
export function qcLensCoverage(
  rawResult: QcResultView | QcReportResult,
): QcLensCoverage {
  const result = resultFields(rawResult);
  const schema = schemaVersionNumber(result.schema_version);
  const schemaV2 = schema != null && schema >= 2;
  const configuredIds = manifestLensIds(result.input_manifest);
  const expectedIds =
    configuredIds.length > 0
      ? configuredIds
      : schemaV2
        ? [...QC_V2_LENS_IDS]
        : [];
  const recordedIds = result.lens_statuses.map((lens) => lens.lens_id);
  const recordedSet = new Set(recordedIds);
  const expectedSet = new Set(expectedIds);
  const counts = new Map<string, number>();
  for (const id of recordedIds) counts.set(id, (counts.get(id) ?? 0) + 1);
  const duplicateIds = [...counts.entries()]
    .filter(([, count]) => count > 1)
    .map(([id]) => id);
  const missingIds = [...expectedSet].filter((id) => !recordedSet.has(id));
  const unexpectedIds = expectedIds.length
    ? [...recordedSet].filter((id) => !expectedSet.has(id))
    : [];
  const completedIds = result.lens_statuses
    .filter((lens) => String(lens.status).trim().toLowerCase() === "completed")
    .map((lens) => lens.lens_id);
  const withoutReviewedChecks = schemaV2
    ? result.lens_statuses
        .filter((lens) => arrayOrEmpty(lens.reviewed_checks).length === 0)
        .map((lens) => lens.lens_id)
    : [];
  const strictIdentity = schemaV2 || configuredIds.length > 0;
  const identitiesComplete = strictIdentity
    ? expectedIds.length > 0 &&
      recordedIds.length === expectedIds.length &&
      duplicateIds.length === 0 &&
      missingIds.length === 0 &&
      unexpectedIds.length === 0
    : result.lens_statuses.length > 0;
  return {
    schemaV2,
    expectedIds,
    recordedIds,
    completedIds,
    missingIds,
    unexpectedIds,
    duplicateIds,
    duplicateRecordCount: recordedIds.length - recordedSet.size,
    withoutReviewedChecks,
    complete:
      identitiesComplete &&
      completedIds.length === recordedIds.length &&
      withoutReviewedChecks.length === 0,
  };
}

function requestCount(result: QcReportResult): number {
  const explicit = finiteNumber(result.api_request_count);
  if (explicit != null) return explicit;
  const lensRequests = result.lens_statuses.reduce(
    (sum, lens) =>
      sum +
      (finiteNumber(lens.api_request_count) ??
        finiteNumber(lens.usage_totals?.api_request_count) ??
        0),
    0,
  );
  const verifierRequests = allQcCandidates(result)
    .flatMap((finding) => finding.verdicts)
    .reduce(
      (sum, verdict) => sum + (finiteNumber(verdict.api_request_count) ?? 0),
      0,
    );
  return lensRequests + verifierRequests;
}

function responseCount(result: QcReportResult): number {
  const explicit = finiteNumber(result.model_response_count);
  if (explicit != null) return explicit;
  const lensResponses = result.lens_statuses.reduce(
    (sum, lens) =>
      sum +
      (finiteNumber(lens.model_response_count) ??
        finiteNumber(lens.usage_totals?.model_response_count) ??
        0),
    0,
  );
  const verifierResponses = allQcCandidates(result)
    .flatMap((finding) => finding.verdicts)
    .reduce(
      (sum, verdict) => sum + (finiteNumber(verdict.model_response_count) ?? 0),
      0,
    );
  return lensResponses + verifierResponses;
}

export function collectQcTraceRecords(
  rawResult: QcResultView | QcReportResult,
): QcTraceRecord[] {
  const result = resultFields(rawResult);
  const records: QcTraceRecord[] = [];

  for (const lens of result.lens_statuses) {
    for (const query of arrayOrEmpty(lens.search_queries)) {
      records.push({
        kind: "query",
        stage: "lens",
        ownerId: lens.lens_id,
        value: query,
      });
    }
    for (const source of arrayOrEmpty(lens.retrieved_sources)) {
      records.push({
        kind: "source",
        stage: "lens",
        ownerId: lens.lens_id,
        value: source.normalized || source.url,
        originalValue:
          source.normalized && source.normalized !== source.url
            ? source.url
            : undefined,
        title: source.title,
        methods: source.methods,
        accepted: source.accepted,
        reason: source.reason,
      });
    }
    for (const query of arrayOrEmpty(lens.attempted_search_queries)) {
      records.push({
        kind: "query",
        stage: "lens_attempt",
        ownerId: lens.lens_id,
        value: query,
      });
    }
    for (const source of arrayOrEmpty(lens.attempted_sources)) {
      records.push({
        kind: "source",
        stage: "lens_attempt",
        ownerId: lens.lens_id,
        value: source.normalized || source.url,
        originalValue:
          source.normalized && source.normalized !== source.url
            ? source.url
            : undefined,
        title: source.title,
        methods: source.methods,
        accepted: source.accepted,
        reason: source.reason,
      });
    }
    for (const [checkIndex, check] of arrayOrEmpty(lens.reviewed_checks).entries()) {
      const ownerId = `${lens.lens_id}/check-${checkIndex + 1}`;
      for (const url of arrayOrEmpty(check.source_urls)) {
        records.push({
          kind: "source",
          stage: "lens",
          ownerId,
          value: url,
        });
      }
      for (const source of arrayOrEmpty(check.source_checks)) {
        records.push({
          kind: "source",
          stage: "lens",
          ownerId,
          value: source.normalized || source.url,
          originalValue:
            source.normalized && source.normalized !== source.url
              ? source.url
              : undefined,
          title: source.title,
          methods: source.methods,
          accepted: source.accepted,
          reason: source.reason,
        });
      }
    }
  }

  for (const finding of allQcCandidates(result)) {
    for (const url of finding.source_urls) {
      records.push({
        kind: "source",
        stage: "finding",
        ownerId: finding.finding_id,
        value: url,
      });
    }
    for (const url of finding.accepted_sources) {
      records.push({
        kind: "source",
        stage: "finding",
        ownerId: finding.finding_id,
        value: url,
        accepted: true,
      });
    }
    for (const check of arrayOrEmpty(finding.source_checks)) {
      records.push({
        kind: "source",
        stage: "finding",
        ownerId: finding.finding_id,
        value: check.normalized || check.url,
        originalValue:
          check.normalized && check.normalized !== check.url
            ? check.url
            : undefined,
        title: check.title,
        accepted: check.accepted,
        reason: check.reason,
      });
    }
    for (const [reviewerOffset, verdict] of finding.verdicts.entries()) {
      const storedIndex = finiteNumber(verdict.reviewer_index);
      const ownerId =
        storedIndex != null && storedIndex > 0
          ? `${finding.finding_id}/reviewer-${storedIndex}`
          : `${finding.finding_id}/reviewer-record-${reviewerOffset + 1}-stored-${
              storedIndex ?? "unrecorded"
            }`;
      for (const query of arrayOrEmpty(verdict.search_queries)) {
        records.push({
          kind: "query",
          stage: "verification",
          ownerId,
          value: query,
        });
      }
      for (const source of arrayOrEmpty(verdict.retrieved_sources)) {
        records.push({
          kind: "source",
          stage: "verification",
          ownerId,
          value: source.normalized || source.url,
          originalValue:
            source.normalized && source.normalized !== source.url
              ? source.url
              : undefined,
          title: source.title,
          methods: source.methods,
          accepted: source.accepted,
          reason: source.reason,
        });
      }
      for (const query of arrayOrEmpty(verdict.attempted_search_queries)) {
        records.push({
          kind: "query",
          stage: "verification_attempt",
          ownerId,
          value: query,
        });
      }
      for (const source of arrayOrEmpty(verdict.attempted_sources)) {
        records.push({
          kind: "source",
          stage: "verification_attempt",
          ownerId,
          value: source.normalized || source.url,
          originalValue:
            source.normalized && source.normalized !== source.url
              ? source.url
              : undefined,
          title: source.title,
          methods: source.methods,
          accepted: source.accepted,
          reason: source.reason,
        });
      }
    }
  }
  return records;
}

export function collectQcOperationRecords(
  rawResult: QcResultView | QcReportResult,
): QcOperationRecord[] {
  const result = resultFields(rawResult);
  const records: QcOperationRecord[] = [];
  const append = (
    findings: QcReportFinding[],
    findingKind: "surviving" | "refuted" | "inconclusive",
  ) => {
    for (const finding of findings) {
      finding.proposed_ops.forEach((operation, operationIndex) => {
        records.push({
          findingId: finding.finding_id,
          findingTitle: finding.title,
          findingKind,
          operationIndex,
          opsValid: finding.ops_valid,
          invalidReason: finding.ops_invalid_reason,
          validationStatus:
            findingKind === "surviving"
              ? finding.ops_valid
                ? "valid"
                : "invalid"
              : "not_evaluated",
          operation,
        });
      });
    }
  };
  append(qcSurvivingCandidates(result), "surviving");
  append(qcSubstantivelyRefutedCandidates(result), "refuted");
  append(qcInconclusiveCandidates(result), "inconclusive");
  return records;
}

export function buildQcReportMetrics(
  rawResult: QcResultView | QcReportResult,
): QcReportMetrics {
  const result = resultFields(rawResult);
  const findings = qcSurvivingCandidates(result);
  const refuted = qcSubstantivelyRefutedCandidates(result);
  const inconclusive = qcInconclusiveCandidates(result);
  const candidates = findings.concat(refuted, inconclusive);
  const dispositions = findings.map((finding) =>
    String(finding.status ?? "").trim().toLowerCase(),
  );
  const sourceChecks = [
    ...candidates.flatMap((finding) => arrayOrEmpty(finding.source_checks)),
    ...result.lens_statuses.flatMap((lens) =>
      arrayOrEmpty(lens.reviewed_checks).flatMap((check) =>
        arrayOrEmpty(check.source_checks),
      ),
    ),
  ];
  const verdicts = candidates.flatMap((finding) => finding.verdicts);
  const seatCoverage = candidates.map((finding) =>
    verifierSeatCoverage(finding, result.schema_version),
  );
  const candidatesWithKnownPanel = seatCoverage.filter(
    (coverage) => coverage.expected != null,
  );
  const expectedVerifierSeats = candidatesWithKnownPanel.reduce(
    (sum, coverage) => sum + (coverage.expected ?? 0),
    0,
  );
  const missingVerifierSeats = seatCoverage.reduce(
    (sum, coverage) => sum + coverage.missing,
    0,
  );
  const trace = collectQcTraceRecords(result);
  const sourceUrls = new Set<string>();
  for (const candidate of candidates) {
    for (const url of candidate.source_urls) if (url) sourceUrls.add(url);
    for (const url of candidate.accepted_sources) if (url) sourceUrls.add(url);
  }
  for (const record of trace) {
    if (record.kind === "source" && record.value) sourceUrls.add(record.value);
  }
  const completedLenses = result.lens_statuses.filter(
    (lens) => String(lens.status).toLowerCase() === "completed",
  ).length;
  const failedLenses = result.lens_statuses.filter(
    (lens) => String(lens.status).toLowerCase() === "failed",
  ).length;
  const lensCoverage = qcLensCoverage(result);

  return {
    totalCandidates: candidates.length,
    survivingFindings: findings.length,
    refutedFindings: refuted.length,
    inconclusiveFindings: inconclusive.length,
    openFindings: dispositions.filter((status) => status === "open").length,
    appliedFindings: dispositions.filter((status) => status === "applied").length,
    dismissedFindings: dispositions.filter((status) => status === "dismissed").length,
    otherDispositionFindings: dispositions.filter(
      (status) => !["open", "applied", "dismissed"].includes(status),
    ).length,
    groundedFindings: findings.filter((finding) => finding.grounded).length,
    ungroundedFindings: findings.filter((finding) => !finding.grounded).length,
    groundedCandidates: candidates.filter((finding) => finding.grounded).length,
    ungroundedCandidates: candidates.filter((finding) => !finding.grounded).length,
    resolvedElementAnchors: candidates.filter(
      (finding) => finding.element_resolved === true,
    ).length,
    unresolvedElementAnchors: candidates.filter(
      (finding) => finding.element_resolved === false,
    ).length,
    unrecordedElementResolution: candidates.filter(
      (finding) => finding.element_resolved == null,
    ).length,
    completedLenses,
    failedLenses,
    otherStatusLenses:
      result.lens_statuses.length - completedLenses - failedLenses,
    totalLenses: result.lens_statuses.length,
    expectedLenses: lensCoverage.expectedIds.length,
    missingLenses: lensCoverage.missingIds.length,
    unexpectedLenses: lensCoverage.unexpectedIds.length,
    duplicateLensRecords: lensCoverage.duplicateRecordCount,
    lensesWithoutReviewedChecks: lensCoverage.withoutReviewedChecks.length,
    lensCoverageComplete: lensCoverage.complete,
    searchQueries: trace.filter((record) => record.kind === "query").length,
    retrievedSourceRecords:
      result.lens_statuses.reduce(
        (sum, lens) => sum + arrayOrEmpty(lens.retrieved_sources).length,
        0,
      ) +
      verdicts.reduce(
        (sum, verdict) => sum + arrayOrEmpty(verdict.retrieved_sources).length,
        0,
      ),
    evidenceTraceRecords: trace.length,
    uniqueSourceUrls: sourceUrls.size,
    sourceChecks: sourceChecks.length,
    acceptedSourceChecks: sourceChecks.filter((check) => check.accepted === true).length,
    rejectedSourceChecks: sourceChecks.filter((check) => check.accepted === false).length,
    unclassifiedSourceChecks: sourceChecks.filter((check) => check.accepted == null).length,
    verdicts: verdicts.length,
    upholdingVerdicts: verdicts.filter(
      (verdict) => isCompletedVerifierSeat(verdict) && verdict.upholds,
    ).length,
    refutingVerdicts: verdicts.filter(
      (verdict) => isCompletedVerifierSeat(verdict) && !verdict.upholds,
    ).length,
    verdictErrors: verdicts.filter(isFailedVerifierSeat).length,
    expectedVerifierSeats,
    recordedVerifierSeats: verdicts.length,
    completedVerifierSeats: seatCoverage.reduce(
      (sum, coverage) => sum + coverage.completed,
      0,
    ),
    failedVerifierSeats: seatCoverage.reduce(
      (sum, coverage) => sum + coverage.failed,
      0,
    ),
    missingVerifierSeats,
    invalidVerifierSeatRecords: seatCoverage.reduce(
      (sum, coverage) => sum + coverage.invalidIndexRecords,
      0,
    ),
    candidatesWithPanelSize: candidatesWithKnownPanel.length,
    candidatesWithVerificationOutcome: candidates.filter((finding) =>
      Boolean(finding.verification_outcome),
    ).length,
    proposedOperations: candidates.reduce(
      (sum, finding) => sum + finding.proposed_ops.length,
      0,
    ),
    unevaluatedRefutedOperations: refuted.reduce(
      (sum, finding) => sum + finding.proposed_ops.length,
      0,
    ),
    unevaluatedInconclusiveOperations: inconclusive.reduce(
      (sum, finding) => sum + finding.proposed_ops.length,
      0,
    ),
    findingsWithValidOperations: findings.filter(
      (finding) => finding.proposed_ops.length > 0 && finding.ops_valid,
    ).length,
    findingsWithInvalidOperations: findings.filter(
      (finding) => finding.proposed_ops.length > 0 && !finding.ops_valid,
    ).length,
    apiRequests: requestCount(result),
    modelResponses: responseCount(result),
    severity: countSeverities(candidates),
    survivingSeverity: countSeverities(findings),
    refutedSeverity: countSeverities(refuted),
    inconclusiveSeverity: countSeverities(inconclusive),
  };
}

/** Backward-friendly short alias for consumers and direct unit tests. */
export const computeQcMetrics = buildQcReportMetrics;

export function qcReportLimitations(
  rawResult: QcResultView | QcReportResult,
  stale: boolean,
): string[] {
  const result = resultFields(rawResult);
  const limitations: string[] = [
    "This is an automated advisory review. It does not replace the judgment, coordination, or seal of the responsible licensed design professional.",
    "The report records the document snapshot and evidence available during the run; codes, web pages, project conditions, and the document itself can change afterward.",
    "A proposed operation is a machine-authored remediation proposal, not proof that the change was applied or that the resulting document is compliant.",
  ];
  if (stale) {
    limitations.push(
      "The active review input differs from the captured snapshot. The change may be in the document, research, standards, module, model settings, or source policy; re-run Final QC before relying on findings or applying proposed operations.",
    );
  }
  if (!result.research_profile_present) {
    limitations.push(
      "No requirements-research profile was available to this run, so completeness and jurisdiction-specific coverage may be narrower.",
    );
  }
  const failedLenses = result.lens_statuses.filter(
    (lens) => String(lens.status).toLowerCase() !== "completed",
  );
  if (result.lens_statuses.length === 0) {
    limitations.push(
      "No lens execution records were preserved, so specialist coverage cannot be established from this result.",
    );
  } else if (failedLenses.length) {
    limitations.push(
      `${failedLenses.length} of ${result.lens_statuses.length} lens record(s) did not complete successfully; their errors are preserved in the lens records below.`,
    );
  }
  const lensCoverage = qcLensCoverage(result);
  if (
    lensCoverage.missingIds.length ||
    lensCoverage.duplicateRecordCount ||
    lensCoverage.unexpectedIds.length
  ) {
    limitations.push(
      `The lens register does not exactly match the configured protocol: ${
        lensCoverage.missingIds.length
          ? `missing ${lensCoverage.missingIds.join(", ")}`
          : "no configured lens is missing"
      }; ${lensCoverage.duplicateRecordCount} duplicate record(s); ${
        lensCoverage.unexpectedIds.length
          ? `unexpected ${lensCoverage.unexpectedIds.join(", ")}`
          : "no unexpected lens"
      }.`,
    );
  }
  if (lensCoverage.withoutReviewedChecks.length) {
    limitations.push(
      `${lensCoverage.withoutReviewedChecks.length} schema-v2 lens record(s) contain no reviewed-check ledger (${lensCoverage.withoutReviewedChecks.join(", ")}), so their substantive coverage is not auditable.`,
    );
  }
  const executionStatus = String(result.execution_status ?? "").toLowerCase();
  if (!executionStatus) {
    limitations.push(
      "No explicit execution status was recorded. This legacy result cannot establish run completeness from the top-level status alone; inspect lens and verifier-seat records individually.",
    );
  } else if (!["complete", "completed", "success", "succeeded"].includes(executionStatus)) {
    limitations.push(
      `The recorded execution status is “${result.execution_status}”; treat the report as potentially incomplete.`,
    );
  }
  if (!result.input_fingerprint) {
    limitations.push(
      "No full input fingerprint was recorded. Currency can be checked only against the legacy document identity, not independently against research, standards, module, model-setting, or source-policy inputs.",
    );
  }
  if (!result.input_manifest || Object.keys(result.input_manifest).length === 0) {
    limitations.push(
      "No complete input manifest was preserved, so the run’s full research, standards, module, and source-policy scope cannot be reconstructed from this record alone.",
    );
  }
  if (!result.version_fingerprint) {
    limitations.push(
      "No reviewed-document fingerprint was recorded; the version label alone is not a content-addressed identity.",
    );
  }
  const sourceChecks = [
    ...allQcCandidates(result)
      .flatMap((finding) => arrayOrEmpty(finding.source_checks)),
    ...result.lens_statuses.flatMap((lens) =>
      arrayOrEmpty(lens.reviewed_checks).flatMap((check) =>
        arrayOrEmpty(check.source_checks),
      ),
    ),
  ];
  const rejected = sourceChecks.filter((check) => check.accepted === false).length;
  if (rejected) {
    limitations.push(
      `${rejected} retrieved source check(s) were rejected. They remain in the evidence record for traceability but are not grounding support.`,
    );
  }
  const findings = qcSurvivingCandidates(result);
  const refuted = qcSubstantivelyRefutedCandidates(result);
  const inconclusive = qcInconclusiveCandidates(result);
  const candidates = findings.concat(refuted, inconclusive);
  if (inconclusive.length) {
    limitations.push(
      `${inconclusive.length} candidate(s) are infrastructure-inconclusive because the verification panel did not yield enough usable completed seats. They received no substantive uphold/refute determination and must not be interpreted as refuted findings.`,
    );
  }
  const normalizedDefaultRefuted = result.findings
    .concat(result.refuted)
    .filter(
      (finding) => verificationOutcome(finding) === "default_refuted",
    ).length;
  if (normalizedDefaultRefuted) {
    limitations.push(
      `${normalizedDefaultRefuted} legacy candidate record(s) used the historical "default_refuted" infrastructure-failure label outside the explicit inconclusive bucket. This report normalizes them to infrastructure-inconclusive; they are not counted or described as substantive refutations.`,
    );
  }
  const normalizedMisbucketed = result.findings
    .concat(result.refuted)
    .filter(
      (finding) => verificationOutcome(finding) === "inconclusive",
    ).length;
  if (normalizedMisbucketed) {
    limitations.push(
      `${normalizedMisbucketed} candidate record(s) marked "inconclusive" were stored outside the explicit inconclusive bucket. This report uses the recorded outcome as authoritative and excludes them from surviving and substantively refuted counts.`,
    );
  }
  const structurallyReclassified = result.refuted.filter(
    (finding) =>
      !hasRecordedInfrastructureOutcome(finding) &&
      isInfrastructureInconclusiveFinding(finding, result.schema_version),
  ).length;
  if (structurallyReclassified) {
    limitations.push(
      `${structurallyReclassified} candidate record(s) were stored as substantively refuted but contain failed, cancelled, missing, duplicate, or out-of-range verifier seats. This report fails closed and reclassifies them as infrastructure-inconclusive because an incomplete panel cannot supply a substantive refutation.`,
    );
  }
  const unknownSeverities = countSeverities(candidates).unknown;
  if (unknownSeverities) {
    limitations.push(
      `${unknownSeverities} candidate(s) use an unknown or future severity value. They are preserved in the report but cannot be ranked within the known critical/high/medium/low scale.`,
    );
  }
  const unresolvedAnchors = candidates.filter(
    (finding) => finding.element_resolved === false,
  ).length;
  if (unresolvedAnchors) {
    limitations.push(
      `${unresolvedAnchors} candidate(s) contain a model-supplied element anchor that did not resolve against the reviewed document. Their preserved references and excerpts require manual location verification.`,
    );
  }
  const failedSeats = candidates
    .flatMap((finding) => finding.verdicts)
    .filter(isFailedVerifierSeat).length;
  if (failedSeats) {
    limitations.push(
      `${failedSeats} verifier seat(s) have a non-completed status or error (including failures, cancellations, and timeouts) and did not supply a usable completed verdict.`,
    );
  }
  const panels = candidates.map((finding) =>
    verifierSeatCoverage(finding, result.schema_version),
  );
  const panelsWithKnownSize = panels.filter((panel) => panel.expected != null);
  const missingSeats = panels.reduce((sum, panel) => sum + panel.missing, 0);
  if (missingSeats) {
    limitations.push(
      `${missingSeats} expected verifier seat(s) have no preserved seat record. Verification-panel coverage is incomplete.`,
    );
  }
  const invalidSeatRecords = panels.reduce(
    (sum, panel) => sum + panel.invalidIndexRecords,
    0,
  );
  if (invalidSeatRecords) {
    limitations.push(
      `${invalidSeatRecords} verifier seat record(s) use a duplicate or out-of-range seat index. Verification-panel identity is incomplete even where the raw record count matches the expected size.`,
    );
  }
  const panelsWithoutKnownSize = candidates.length - panelsWithKnownSize.length;
  if (panelsWithoutKnownSize) {
    limitations.push(
      `The expected verification-panel size was not recorded for ${panelsWithoutKnownSize} candidate(s), so complete seat coverage cannot be independently confirmed for those candidates.`,
    );
  }
  if (finiteNumber(result.estimated_cost_usd) != null) {
    limitations.push(
      "The displayed dollar cost is an estimate derived from recorded usage and configured list pricing; the API provider’s billing record is authoritative.",
    );
  }
  return limitations;
}
