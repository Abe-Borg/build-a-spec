/**
 * Audit-grade, read-only Final QC report.
 *
 * QCDrawer remains the compact action queue. This modal exposes the durable
 * record behind it: run identity, methodology, lens work papers, every source
 * check and verifier record, unabridged proposed operations, disposition
 * history, usage, substantively refuted candidates, infrastructure-
 * inconclusive candidates, and limitations.
 */
import { useRef } from "react";
import type { ReactNode } from "react";
import type { QcSnapshot, ReadinessPayload } from "../types";
import { useDialogFocus } from "../lib/dialogFocus";
import {
  buildQcReportMetrics,
  collectQcOperationRecords,
  collectQcTraceRecords,
  formatDecimal,
  formatDuration,
  formatFieldName,
  formatInteger,
  formatJson,
  formatSeverity,
  formatTimestamp,
  formatUsd,
  groupFindingsBySeverity,
  isFailedVerifierSeat,
  normalizeSeverity,
  qcInconclusiveCandidates,
  qcLensCoverage,
  qcPrimaryReport,
  qcReportExportUrl,
  qcReportLimitations,
  qcSubstantivelyRefutedCandidates,
  qcSurvivingCandidates,
  safeHttpUrl,
  verifierSeatCoverage,
  type QcReportFinding,
  type QcReportLens,
  type QcReportResult,
  type QcReportVerdict,
  type QcRetrievedSourceRecord,
  type QcSourceCheckRecord,
} from "../lib/qcReport";

interface Props {
  open: boolean;
  snapshot: QcSnapshot | null | undefined;
  retainedStale: boolean;
  currentVersion: number | null | undefined;
  readiness: ReadinessPayload | null | undefined;
  onClose: () => void;
}

const NOT_RECORDED = "Not recorded";

const severityTone = {
  critical: "border-err/70 bg-err/20 text-err",
  high: "border-err/50 bg-err/10 text-err",
  medium: "border-warn/50 bg-warn/15 text-warn",
  low: "border-ink-faint/50 bg-ink-faint/10 text-ink-dim",
  unknown: "border-accent/40 bg-accent/10 text-accent",
} as const;

function recorded(value: unknown): string {
  if (value == null) return NOT_RECORDED;
  if (typeof value === "string") return value.trim() || NOT_RECORDED;
  return String(value);
}

function versionLabel(index: number | null | undefined): string {
  return typeof index === "number" && Number.isFinite(index)
    ? `v${index + 1} (stored index ${index})`
    : NOT_RECORDED;
}

function runDuration(result: QcReportResult): number | undefined {
  if (typeof result.duration_ms === "number" && Number.isFinite(result.duration_ms)) {
    return result.duration_ms;
  }
  const started = Date.parse(result.started_at);
  const finished = Date.parse(result.finished_at);
  return Number.isFinite(started) && Number.isFinite(finished) && finished >= started
    ? finished - started
    : undefined;
}

function statusTone(status: unknown): string {
  const value = String(status ?? "").toLowerCase();
  if (["complete", "completed", "success", "succeeded", "accepted", "pass", "passed"].includes(value)) {
    return "border-ok/40 bg-ok/10 text-ok";
  }
  if (["failed", "error", "rejected", "refuted", "critical"].includes(value)) {
    return "border-err/40 bg-err/10 text-err";
  }
  if (["partial", "warning", "warn", "stale", "dismissed"].includes(value)) {
    return "border-warn/40 bg-warn/10 text-warn";
  }
  return "border-edge bg-raised text-ink-dim";
}

function Pill({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <span
      className={`inline-flex rounded-full border px-2 py-0.5 text-[10px] font-semibold ${className}`}
    >
      {children}
    </span>
  );
}

function Section({
  number,
  title,
  description,
  children,
}: {
  number: string;
  title: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <section className="rounded-xl border border-edge bg-bg/35 px-4 py-4">
      <div className="mb-3 border-b border-edge/60 pb-2.5">
        <p className="text-[10px] font-semibold tracking-[0.16em] text-accent uppercase">
          Section {number}
        </p>
        <h3 className="mt-0.5 font-[family-name:var(--font-display)] text-[15px] font-semibold text-ink">
          {title}
        </h3>
        {description && (
          <p className="mt-1 max-w-4xl text-[11px] leading-relaxed text-ink-faint">
            {description}
          </p>
        )}
      </div>
      {children}
    </section>
  );
}

function DataField({ label, children, mono = false }: { label: string; children: ReactNode; mono?: boolean }) {
  return (
    <div className="min-w-0 rounded-lg border border-edge/60 bg-surface/50 px-3 py-2">
      <dt className="text-[9px] font-semibold tracking-wide text-ink-faint uppercase">
        {label}
      </dt>
      <dd
        className={`mt-0.5 whitespace-pre-wrap break-words text-[11px] leading-relaxed text-ink-dim ${
          mono ? "font-mono break-all" : ""
        }`}
      >
        {children}
      </dd>
    </div>
  );
}

function EmptyRecord({ children = "None recorded." }: { children?: ReactNode }) {
  return (
    <p className="rounded-lg border border-dashed border-edge px-3 py-2 text-[11px] text-ink-faint italic">
      {children}
    </p>
  );
}

function SourceValue({
  url,
  title,
  methods,
  accepted,
  reason,
  originalUrl,
}: {
  url: string;
  title?: string;
  methods?: string[];
  accepted?: boolean | null;
  reason?: string;
  originalUrl?: string;
}) {
  const safe = safeHttpUrl(url);
  return (
    <div className="min-w-0 rounded-lg border border-edge/60 bg-surface/40 px-2.5 py-2">
      <div className="flex flex-wrap items-center gap-1.5">
        {accepted !== undefined && (
          <Pill className={accepted === true ? "border-ok/40 bg-ok/10 text-ok" : accepted === false ? "border-err/40 bg-err/10 text-err" : "border-edge bg-raised text-ink-faint"}>
            {accepted === true ? "accepted" : accepted === false ? "rejected" : "no citation decision"}
          </Pill>
        )}
        {title && <span className="text-[11px] font-medium text-ink">{title}</span>}
        {!safe && <Pill className="border-warn/40 bg-warn/10 text-warn">not a safe HTTP(S) link</Pill>}
      </div>
      {safe ? (
        <a
          href={safe}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-1 block break-all font-mono text-[10px] leading-relaxed text-accent hover:underline"
        >
          {url}
        </a>
      ) : (
        <p className="mt-1 break-all font-mono text-[10px] leading-relaxed text-ink-faint">
          {url || "(empty source value)"}
        </p>
      )}
      {methods && methods.length > 0 && (
        <p className="mt-1 text-[10px] text-ink-faint">
          Retrieval method{methods.length === 1 ? "" : "s"}: {methods.join(", ")}
        </p>
      )}
      {originalUrl && originalUrl !== url && (
        <p className="mt-1 break-all font-mono text-[10px] leading-relaxed text-ink-faint">
          Original URL: {originalUrl}
        </p>
      )}
      {reason && (
        <p className="mt-1 whitespace-pre-wrap break-words text-[10px] leading-relaxed text-ink-faint">
          Reason: {reason}
        </p>
      )}
    </div>
  );
}

function SourceList({
  sources,
  empty,
}: {
  sources: QcRetrievedSourceRecord[] | undefined;
  empty: string;
}) {
  if (!sources || sources.length === 0) return <EmptyRecord>{empty}</EmptyRecord>;
  return (
    <div className="space-y-1.5">
      {sources.map((source, index) => (
        <SourceValue
          key={`${source.url}-${index}`}
          url={source.normalized || source.url}
          title={source.title}
          methods={source.methods}
          accepted={source.accepted}
          reason={source.reason}
          originalUrl={source.normalized && source.normalized !== source.url ? source.url : undefined}
        />
      ))}
    </div>
  );
}

function StringList({ values, empty }: { values: string[] | undefined; empty: string }) {
  if (!values || values.length === 0) return <EmptyRecord>{empty}</EmptyRecord>;
  return (
    <ol className="space-y-1.5">
      {values.map((value, index) => (
        <li
          key={`${value}-${index}`}
          className="flex min-w-0 gap-2 rounded-lg border border-edge/60 bg-surface/40 px-2.5 py-2 text-[11px] leading-relaxed text-ink-dim"
        >
          <span className="shrink-0 font-mono text-[10px] text-ink-faint">{index + 1}.</span>
          <span className="min-w-0 whitespace-pre-wrap break-words">{value}</span>
        </li>
      ))}
    </ol>
  );
}

function UsageTable({ usage, empty = "No usage counters were recorded." }: { usage: Record<string, number> | undefined; empty?: string }) {
  const entries = Object.entries(usage ?? {}).sort(([a], [b]) => a.localeCompare(b));
  if (entries.length === 0) return <EmptyRecord>{empty}</EmptyRecord>;
  return (
    <div className="overflow-x-auto rounded-lg border border-edge/60">
      <table className="w-full min-w-[28rem] border-collapse text-left text-[11px]">
        <thead className="bg-raised text-[9px] tracking-wide text-ink-faint uppercase">
          <tr>
            <th className="border-b border-edge px-2.5 py-1.5 font-semibold">Counter</th>
            <th className="border-b border-edge px-2.5 py-1.5 text-right font-semibold">Recorded value</th>
          </tr>
        </thead>
        <tbody>
          {entries.map(([key, value]) => (
            <tr key={key} className="border-b border-edge/40 last:border-0">
              <td className="px-2.5 py-1.5 text-ink-dim">
                {formatFieldName(key)} <span className="font-mono text-[9px] text-ink-faint">({key})</span>
              </td>
              <td className="px-2.5 py-1.5 text-right font-mono text-ink">{formatDecimal(value, 4)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function JsonBlock({ value, label }: { value: unknown; label: string }) {
  return (
    <div className="min-w-0 overflow-hidden rounded-lg border border-edge/60 bg-[#0f1116]">
      <p className="border-b border-white/10 px-3 py-1.5 text-[9px] font-semibold tracking-wide text-white/50 uppercase">
        {label}
      </p>
      <pre className="max-w-full overflow-x-auto whitespace-pre-wrap break-words px-3 py-2.5 font-mono text-[10px] leading-relaxed text-white/75">
        {formatJson(value)}
      </pre>
    </div>
  );
}

function ReviewedChecks({ lens }: { lens: QcReportLens }) {
  const checks = lens.reviewed_checks ?? [];
  if (checks.length === 0) return <EmptyRecord>No explicit reviewed-check ledger was recorded for this lens.</EmptyRecord>;
  return (
    <ol className="space-y-2">
      {checks.map((check, index) => (
        <li key={`${check.check}-${index}`} className="rounded-lg border border-edge/60 bg-surface/40 px-3 py-2.5">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <p className="whitespace-pre-wrap break-words text-[11px] font-medium leading-relaxed text-ink">
              {index + 1}. {check.check}
            </p>
            <Pill className={statusTone(check.outcome)}>{recorded(check.outcome)}</Pill>
          </div>
          {check.notes && (
            <p className="mt-1 whitespace-pre-wrap break-words text-[11px] leading-relaxed text-ink-faint">
              {check.notes}
            </p>
          )}
          {check.element_ids && check.element_ids.length > 0 && (
            <p className="mt-1.5 break-words text-[10px] text-ink-faint">
              Element IDs: <span className="font-mono text-ink-dim">{check.element_ids.join(", ")}</span>
            </p>
          )}
          {check.source_urls && check.source_urls.length > 0 && (
            <div className="mt-2 space-y-1.5">
              {check.source_urls.map((url, sourceIndex) => (
                <SourceValue key={`${url}-${sourceIndex}`} url={url} />
              ))}
            </div>
          )}
          {check.source_checks && check.source_checks.length > 0 && (
            <div className="mt-2">
              <p className="mb-1.5 text-[9px] font-semibold tracking-wide text-ink-faint uppercase">
                Normalized source checks ({check.source_checks.length})
              </p>
              <SourceChecks checks={check.source_checks} />
            </div>
          )}
        </li>
      ))}
    </ol>
  );
}

function SourceChecks({ checks }: { checks: QcSourceCheckRecord[] | undefined }) {
  if (!checks || checks.length === 0) {
    return <EmptyRecord>No normalized source-check ledger was recorded for this finding.</EmptyRecord>;
  }
  return (
    <div className="space-y-1.5">
      {checks.map((check, index) => (
        <SourceValue
          key={`${check.url}-${index}`}
          url={check.normalized || check.url}
          title={check.title}
          methods={check.methods}
          accepted={check.accepted}
          reason={check.reason}
          originalUrl={check.normalized && check.normalized !== check.url ? check.url : undefined}
        />
      ))}
    </div>
  );
}

function VerdictRecord({ verdict, index }: { verdict: QcReportVerdict; index: number }) {
  const storedReviewerIndex = verdict.reviewer_index;
  const reviewerLabel =
    typeof storedReviewerIndex === "number" && storedReviewerIndex > 0
      ? `Reviewer seat ${storedReviewerIndex}`
      : `Reviewer record ${index + 1} (legacy/unindexed)`;
  const failedSeat = isFailedVerifierSeat(verdict);
  return (
    <div className="rounded-lg border border-edge/70 bg-bg/40 px-3 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-[11px] font-semibold text-ink">{reviewerLabel}</p>
        <Pill className={failedSeat ? "border-err/40 bg-err/10 text-err" : verdict.upholds ? "border-err/40 bg-err/10 text-err" : "border-ok/40 bg-ok/10 text-ok"}>
          {failedSeat ? "non-completed verifier seat" : verdict.upholds ? "upholds candidate" : "does not uphold"}
        </Pill>
        {verdict.status && <Pill className={statusTone(verdict.status)}>{verdict.status}</Pill>}
      </div>
      <dl className="mt-2 grid grid-cols-1 gap-1.5 sm:grid-cols-2 lg:grid-cols-5">
        <DataField label="Stored reviewer index">{formatInteger(storedReviewerIndex)}</DataField>
        <DataField label="Revised severity">{verdict.revised_severity ? formatSeverity(verdict.revised_severity) : "Kept original / none recorded"}</DataField>
        <DataField label="API requests">{formatInteger(verdict.api_request_count)}</DataField>
        <DataField label="Model responses">{formatInteger(verdict.model_response_count)}</DataField>
        <DataField label="Estimated seat cost">{formatUsd(verdict.estimated_cost_usd)}</DataField>
      </dl>
      <div className="mt-2">
        <p className="text-[9px] font-semibold tracking-wide text-ink-faint uppercase">Reviewer note</p>
        <p className="mt-0.5 whitespace-pre-wrap break-words text-[11px] leading-relaxed text-ink-dim">
          {verdict.note || NOT_RECORDED}
        </p>
      </div>
      {verdict.error && (
        <p className="mt-2 whitespace-pre-wrap break-words rounded border border-err/40 bg-err/10 px-2 py-1.5 text-[11px] text-err">
          Error: {verdict.error}
        </p>
      )}
      <div className="mt-3 grid grid-cols-1 gap-3 lg:grid-cols-2">
        <div>
          <p className="mb-1.5 text-[9px] font-semibold tracking-wide text-ink-faint uppercase">Verification searches</p>
          <StringList values={verdict.search_queries} empty="No verification search queries were recorded for this reviewer." />
        </div>
        <div>
          <p className="mb-1.5 text-[9px] font-semibold tracking-wide text-ink-faint uppercase">Verification retrievals</p>
          <SourceList sources={verdict.retrieved_sources} empty="No verification source retrievals were recorded for this reviewer." />
        </div>
      </div>
      <div className="mt-3 grid grid-cols-1 gap-3 lg:grid-cols-2">
        <div>
          <p className="mb-1.5 text-[9px] font-semibold tracking-wide text-ink-faint uppercase">All billed-attempt searches</p>
          <StringList values={verdict.attempted_search_queries} empty="No separate billed-attempt query ledger was recorded." />
        </div>
        <div>
          <p className="mb-1.5 text-[9px] font-semibold tracking-wide text-ink-faint uppercase">All billed-attempt sources</p>
          <SourceList sources={verdict.attempted_sources} empty="No separate billed-attempt source ledger was recorded." />
        </div>
      </div>
      <div className="mt-3">
        <p className="mb-1.5 text-[9px] font-semibold tracking-wide text-ink-faint uppercase">Reviewer usage</p>
        <UsageTable usage={verdict.usage_totals} empty="No per-reviewer usage counters were recorded." />
      </div>
    </div>
  );
}

function FindingRecord({
  finding,
  index,
  kind,
  schemaVersion,
}: {
  finding: QcReportFinding;
  index: number;
  kind: "surviving" | "refuted" | "inconclusive";
  schemaVersion: unknown;
}) {
  const severity = normalizeSeverity(finding.severity);
  const operations = finding.proposed_ops ?? [];
  const verdicts = finding.verdicts ?? [];
  const events = finding.disposition_events ?? [];
  const seats = verifierSeatCoverage(finding, schemaVersion);
  return (
    <article className={`rounded-xl border bg-surface/55 px-4 py-4 shadow-sm ${kind === "inconclusive" ? "border-warn/60" : "border-edge"}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-[9px] font-semibold tracking-[0.14em] text-ink-faint uppercase">
            {kind === "refuted"
              ? "Substantively refuted candidate"
              : kind === "inconclusive"
                ? "Infrastructure-inconclusive candidate"
                : "Verified finding"} {index + 1}
          </p>
          <h4 className="mt-1 whitespace-pre-wrap break-words text-[13px] font-semibold leading-relaxed text-ink">
            {finding.title || "Untitled finding"}
          </h4>
        </div>
        <div className="flex flex-wrap justify-end gap-1.5">
          <Pill className={severityTone[severity]}>{formatSeverity(finding.severity)}</Pill>
          {kind === "refuted" ? (
            <Pill className="border-ok/40 bg-ok/10 text-ok">not upheld · not an open issue</Pill>
          ) : kind === "inconclusive" ? (
            <Pill className="border-warn/50 bg-warn/10 text-warn">inconclusive · no substantive refutation</Pill>
          ) : (
            <Pill className={statusTone(finding.status)}>{recorded(finding.status)}</Pill>
          )}
          <Pill className={finding.grounded ? "border-ok/40 bg-ok/10 text-ok" : "border-warn/40 bg-warn/10 text-warn"}>
            {finding.grounded ? "grounded" : "not grounded"}
          </Pill>
          {finding.element_resolved === false && (
            <Pill className="border-err/50 bg-err/10 text-err">unresolved model-supplied anchor</Pill>
          )}
        </div>
      </div>

      <dl className="mt-3 grid grid-cols-1 gap-1.5 sm:grid-cols-2 lg:grid-cols-4">
        <DataField label="Finding ID" mono>{recorded(finding.finding_id)}</DataField>
        <DataField label="Lens ID" mono>{recorded(finding.lens_id)}</DataField>
        <DataField label="Element ID" mono>{finding.element_id || "Section-level"}</DataField>
        <DataField label="Reviewed reference">{recorded(finding.reviewed_ref)}</DataField>
        <DataField label="Element anchor resolution">
          {finding.element_resolved === true ? "Resolved to the reviewed document" : finding.element_resolved === false ? "Unresolved model-supplied anchor" : NOT_RECORDED}
        </DataField>
        <DataField label="Original severity">{finding.original_severity ? formatSeverity(finding.original_severity) : NOT_RECORDED}</DataField>
        <DataField label="Verification outcome">{recorded(finding.verification_outcome)}</DataField>
        <DataField label="Verification threshold">{formatDecimal(finding.verification_threshold, 4)}</DataField>
        <DataField label="Disposition">
          {kind === "refuted"
            ? `Not applicable — candidate was substantively refuted (stored field: ${recorded(finding.status)})`
            : kind === "inconclusive"
              ? `Not applicable — verification was infrastructure-inconclusive (stored field: ${recorded(finding.status)})`
              : recorded(finding.status)}
        </DataField>
        <DataField label="Operation validation">{kind === "refuted" ? "Not evaluated — candidate was substantively refuted" : kind === "inconclusive" ? "Not evaluated — verification was infrastructure-inconclusive" : operations.length === 0 ? "No operation proposed" : finding.ops_valid ? "Valid" : "Invalid"}</DataField>
        <DataField label="Verifier seats completed / expected">
          {seats.completed.toLocaleString("en-US")} / {seats.expected == null ? "expected not recorded" : seats.expected.toLocaleString("en-US")}
        </DataField>
        <DataField label="Preserved verifier seat records">
          {seats.recorded.toLocaleString("en-US")} total · {seats.failed.toLocaleString("en-US")} failed · {seats.missing.toLocaleString("en-US")} missing · {seats.invalidIndexRecords.toLocaleString("en-US")} invalid/duplicate index
        </DataField>
      </dl>

      <div className={`mt-3 rounded-lg border px-3 py-2.5 ${finding.element_resolved === false ? "border-err/45 bg-err/10" : "border-edge/60 bg-bg/35"}`}>
        <p className={`text-[9px] font-semibold tracking-wide uppercase ${finding.element_resolved === false ? "text-err" : "text-ink-faint"}`}>
          Exact reviewed location and excerpt
        </p>
        <p className="mt-1 text-[10px] text-ink-faint">
          Reference: <span className="font-mono text-ink-dim">{finding.reviewed_ref || finding.element_id || "section-level / not recorded"}</span>
        </p>
        <p className="mt-1 whitespace-pre-wrap break-words text-[11px] leading-relaxed text-ink-dim">
          {finding.reviewed_text || "No reviewed excerpt was preserved."}
        </p>
        {finding.element_resolved === false && (
          <p className="mt-1.5 text-[10px] leading-relaxed text-err">
            The model supplied an element anchor that did not resolve against the reviewed document snapshot. Treat the location as unverified and use the preserved excerpt to investigate manually.
          </p>
        )}
      </div>

      <div className="mt-3 grid grid-cols-1 gap-3 lg:grid-cols-2">
        <div className="rounded-lg border border-edge/60 bg-bg/35 px-3 py-2.5">
          <p className="text-[9px] font-semibold tracking-wide text-ink-faint uppercase">Issue</p>
          <p className="mt-1 whitespace-pre-wrap break-words text-[11px] leading-relaxed text-ink-dim">
            {finding.issue || NOT_RECORDED}
          </p>
        </div>
        <div className="rounded-lg border border-edge/60 bg-bg/35 px-3 py-2.5">
          <p className="text-[9px] font-semibold tracking-wide text-ink-faint uppercase">Rationale</p>
          <p className="mt-1 whitespace-pre-wrap break-words text-[11px] leading-relaxed text-ink-dim">
            {finding.rationale || NOT_RECORDED}
          </p>
        </div>
      </div>

      {(finding.dismiss_reason ||
        (kind === "surviving" && finding.ops_invalid_reason)) && (
        <div className="mt-3 space-y-1.5">
          {finding.dismiss_reason && (
            <p className="whitespace-pre-wrap break-words rounded border border-warn/40 bg-warn/10 px-2.5 py-2 text-[11px] text-warn">
              Dismissal reason: {finding.dismiss_reason}
            </p>
          )}
          {kind === "surviving" && finding.ops_invalid_reason && (
            <p className="whitespace-pre-wrap break-words rounded border border-err/40 bg-err/10 px-2.5 py-2 text-[11px] text-err">
              Operation validation detail: {finding.ops_invalid_reason}
            </p>
          )}
        </div>
      )}

      <div className="mt-4">
        <h5 className="text-[10px] font-semibold tracking-wide text-ink-dim uppercase">Evidence record</h5>
        <div className="mt-2 grid grid-cols-1 gap-3 lg:grid-cols-3">
          <div>
            <p className="mb-1.5 text-[9px] font-semibold tracking-wide text-ink-faint uppercase">Candidate citations ({finding.source_urls.length})</p>
            {finding.source_urls.length > 0 ? (
              <div className="space-y-1.5">
                {finding.source_urls.map((url, sourceIndex) => <SourceValue key={`${url}-${sourceIndex}`} url={url} />)}
              </div>
            ) : <EmptyRecord>No candidate citations recorded.</EmptyRecord>}
          </div>
          <div>
            <p className="mb-1.5 text-[9px] font-semibold tracking-wide text-ink-faint uppercase">Accepted grounding sources ({finding.accepted_sources.length})</p>
            {finding.accepted_sources.length > 0 ? (
              <div className="space-y-1.5">
                {finding.accepted_sources.map((url, sourceIndex) => <SourceValue key={`${url}-${sourceIndex}`} url={url} accepted />)}
              </div>
            ) : <EmptyRecord>No accepted grounding sources recorded.</EmptyRecord>}
          </div>
          <div>
            <p className="mb-1.5 text-[9px] font-semibold tracking-wide text-ink-faint uppercase">Source acceptance checks ({finding.source_checks?.length ?? 0})</p>
            <SourceChecks checks={finding.source_checks} />
          </div>
        </div>
      </div>

      <div className="mt-4">
        <h5 className="text-[10px] font-semibold tracking-wide text-ink-dim uppercase">
          Adversarial verifier records ({verdicts.length})
        </h5>
        <div className="mt-2 space-y-2">
          {verdicts.length > 0 ? verdicts.map((verdict, verdictIndex) => (
            <VerdictRecord key={`${verdict.reviewer_index ?? verdictIndex}-${verdictIndex}`} verdict={verdict} index={verdictIndex} />
          )) : <EmptyRecord>No individual verifier records were preserved for this finding.</EmptyRecord>}
        </div>
      </div>

      <div className="mt-4">
        <h5 className="text-[10px] font-semibold tracking-wide text-ink-dim uppercase">
          Full proposed operations ({operations.length})
        </h5>
        <div className="mt-2 space-y-2">
          {operations.length > 0 ? operations.map((operation, operationIndex) => (
            <JsonBlock key={operationIndex} value={operation} label={`Operation ${operationIndex + 1} of ${operations.length}`} />
          )) : <EmptyRecord>{kind === "refuted" ? "No operation payload was recorded; operation validation was not run because the candidate was substantively refuted." : kind === "inconclusive" ? "No operation payload was recorded; operation validation was not run because verification was infrastructure-inconclusive." : "No mechanical operation was proposed."}</EmptyRecord>}
        </div>
      </div>

      <div className="mt-4">
        <h5 className="text-[10px] font-semibold tracking-wide text-ink-dim uppercase">
          Disposition history ({events.length})
        </h5>
        {events.length > 0 ? (
          <ol className="mt-2 space-y-1.5">
            {events.map((event, eventIndex) => (
              <li key={`${event.action}-${event.at}-${eventIndex}`} className="rounded-lg border border-edge/60 bg-bg/35 px-3 py-2 text-[11px] text-ink-dim">
                <div className="flex flex-wrap items-center gap-2">
                  <Pill className={statusTone(event.action)}>{recorded(event.action)}</Pill>
                  <span>{formatTimestamp(event.at)}</span>
                  {event.document_version != null && <span className="font-mono text-[10px] text-ink-faint">stored document version: {event.document_version}</span>}
                </div>
                {event.reason && <p className="mt-1 whitespace-pre-wrap break-words text-ink-faint">Reason: {event.reason}</p>}
                {event.document_fingerprint && <p className="mt-1 break-all font-mono text-[10px] text-ink-faint">Document fingerprint: {event.document_fingerprint}</p>}
              </li>
            ))}
          </ol>
        ) : <div className="mt-2"><EmptyRecord>No disposition events were recorded.</EmptyRecord></div>}
      </div>
    </article>
  );
}

function LensRecord({ lens, index }: { lens: QcReportLens; index: number }) {
  return (
    <article className="rounded-xl border border-edge bg-surface/55 px-4 py-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[9px] font-semibold tracking-[0.14em] text-ink-faint uppercase">Lens {index + 1}</p>
          <h4 className="mt-1 whitespace-pre-wrap break-words text-[13px] font-semibold text-ink">
            {lens.title || formatFieldName(lens.lens_id)}
          </h4>
          <p className="mt-0.5 break-all font-mono text-[9px] text-ink-faint">{lens.lens_id}</p>
        </div>
        <Pill className={statusTone(lens.status)}>{recorded(lens.status)}</Pill>
      </div>
      <dl className="mt-3 grid grid-cols-1 gap-1.5 sm:grid-cols-2 lg:grid-cols-6">
        <DataField label="Candidates raised">{formatInteger(lens.finding_count)}</DataField>
        <DataField label="Grounded candidates">{formatInteger(lens.grounded_count)}</DataField>
        <DataField label="Reviewed checks">{formatInteger(lens.reviewed_checks?.length)}</DataField>
        <DataField label="API requests">{formatInteger(lens.api_request_count)}</DataField>
        <DataField label="Model responses">{formatInteger(lens.model_response_count)}</DataField>
        <DataField label="Estimated lens cost">{formatUsd(lens.estimated_cost_usd)}</DataField>
      </dl>
      <div className="mt-3 grid grid-cols-1 gap-3 lg:grid-cols-2">
        <div className="rounded-lg border border-edge/60 bg-bg/35 px-3 py-2.5">
          <p className="text-[9px] font-semibold tracking-wide text-ink-faint uppercase">Assigned brief</p>
          <p className="mt-1 whitespace-pre-wrap break-words text-[11px] leading-relaxed text-ink-dim">{lens.brief || NOT_RECORDED}</p>
        </div>
        <div className="rounded-lg border border-edge/60 bg-bg/35 px-3 py-2.5">
          <p className="text-[9px] font-semibold tracking-wide text-ink-faint uppercase">Lens summary</p>
          <p className="mt-1 whitespace-pre-wrap break-words text-[11px] leading-relaxed text-ink-dim">{lens.summary || NOT_RECORDED}</p>
        </div>
      </div>
      {lens.error && (
        <p className="mt-3 whitespace-pre-wrap break-words rounded border border-err/40 bg-err/10 px-2.5 py-2 text-[11px] text-err">
          Lens error: {lens.error}
        </p>
      )}
      <div className="mt-4">
        <p className="mb-1.5 text-[9px] font-semibold tracking-wide text-ink-faint uppercase">Check-by-check work ledger</p>
        <ReviewedChecks lens={lens} />
      </div>
      <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-2">
        <div>
          <p className="mb-1.5 text-[9px] font-semibold tracking-wide text-ink-faint uppercase">Search queries ({lens.search_queries?.length ?? 0})</p>
          <StringList values={lens.search_queries} empty="No lens search queries were recorded." />
        </div>
        <div>
          <p className="mb-1.5 text-[9px] font-semibold tracking-wide text-ink-faint uppercase">Retrieved sources ({lens.retrieved_sources?.length ?? 0})</p>
          <SourceList sources={lens.retrieved_sources} empty="No lens retrieval records were recorded." />
        </div>
      </div>
      <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-2">
        <div>
          <p className="mb-1.5 text-[9px] font-semibold tracking-wide text-ink-faint uppercase">All billed-attempt search queries ({lens.attempted_search_queries?.length ?? 0})</p>
          <StringList values={lens.attempted_search_queries} empty="No separate billed-attempt query ledger was recorded." />
        </div>
        <div>
          <p className="mb-1.5 text-[9px] font-semibold tracking-wide text-ink-faint uppercase">All billed-attempt sources ({lens.attempted_sources?.length ?? 0})</p>
          <SourceList sources={lens.attempted_sources} empty="No separate billed-attempt source ledger was recorded." />
        </div>
      </div>
      <div className="mt-4">
        <p className="mb-1.5 text-[9px] font-semibold tracking-wide text-ink-faint uppercase">Lens usage totals</p>
        <UsageTable usage={lens.usage_totals} empty="No per-lens usage totals were recorded." />
      </div>
    </article>
  );
}

function ReadinessRecord({ readiness }: { readiness: ReadinessPayload | null | undefined }) {
  if (!readiness) {
    return <EmptyRecord>No issue-readiness snapshot was supplied with this report view.</EmptyRecord>;
  }
  return (
    <div className="rounded-lg border border-edge/60 bg-surface/45 px-3 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-[11px] font-semibold text-ink">Current issue-readiness snapshot</p>
        <Pill className={readiness.ready ? "border-ok/40 bg-ok/10 text-ok" : "border-warn/40 bg-warn/10 text-warn"}>
          {readiness.ready ? "ready" : "not ready"}
        </Pill>
      </div>
      <p className="mt-1 text-[10px] leading-relaxed text-ink-faint">
        This checklist reflects current application state. The run identity below records the document snapshot actually reviewed.
      </p>
      <ul className="mt-2 space-y-1.5">
        {readiness.checks.map((check) => (
          <li key={check.id} className="flex min-w-0 items-start gap-2 rounded border border-edge/50 bg-bg/30 px-2.5 py-2">
            <span className={`mt-1 h-1.5 w-1.5 shrink-0 rounded-full ${check.ok ? "bg-ok" : check.advisory ? "bg-ink-faint" : "bg-warn"}`} />
            <div className="min-w-0 flex-1">
              <p className="whitespace-pre-wrap break-words text-[11px] leading-relaxed text-ink-dim">{check.detail}</p>
              <p className="mt-0.5 break-all font-mono text-[9px] text-ink-faint">
                {check.id} · {check.ok ? "passed" : "not passed"}{check.advisory ? " · advisory" : " · required"}
              </p>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Stat({ label, value, detail }: { label: string; value: ReactNode; detail?: string }) {
  return (
    <div className="rounded-lg border border-edge/60 bg-surface/50 px-3 py-2.5">
      <p className="text-[9px] font-semibold tracking-wide text-ink-faint uppercase">{label}</p>
      <p className="mt-0.5 text-lg font-semibold tabular-nums text-ink">{value}</p>
      {detail && <p className="mt-0.5 text-[10px] leading-relaxed text-ink-faint">{detail}</p>}
    </div>
  );
}

export default function QCReportModal({
  open,
  snapshot,
  retainedStale,
  currentVersion,
  readiness,
  onClose,
}: Props) {
  const selectedReport = qcPrimaryReport(snapshot);
  const latestAttempt = snapshot?.latest_attempt;
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  useDialogFocus(
    open && Boolean(selectedReport),
    dialogRef,
    closeButtonRef,
    onClose,
  );

  if (!open || !selectedReport) return null;

  const report = selectedReport as QcReportResult;
  const retainedRunId = snapshot?.result?.run_id;
  const reportDiffersFromRetained = Boolean(
    retainedRunId && retainedRunId !== report.run_id,
  );
  const reportHasNoRetainedQueue = !retainedRunId;
  const latestAttemptDiffersFromReport = Boolean(
    latestAttempt?.run_id && latestAttempt.run_id !== report.run_id,
  );
  const reportSelectionSource = snapshot?.report_is_latest_attempt
    ? "latest attempt report"
    : "retained report fallback";
  const selectedReportStale =
    snapshot?.report_stale ??
    (snapshot?.report && snapshot.report.run_id !== retainedRunId
      ? true
      : retainedStale);
  const versionMismatch =
    typeof currentVersion === "number" &&
    Number.isFinite(currentVersion) &&
    currentVersion !== report.version_index;
  const reportIsStale = selectedReportStale || versionMismatch;
  const metrics = buildQcReportMetrics(report);
  const findings = qcSurvivingCandidates(report);
  const refuted = qcSubstantivelyRefutedCandidates(report);
  const inconclusive = qcInconclusiveCandidates(report);
  const lensCoverage = qcLensCoverage(report);
  const traceRecords = collectQcTraceRecords(report);
  const operationRecords = collectQcOperationRecords(report);
  const limitations = qcReportLimitations(report, reportIsStale);
  const duration = runDuration(report);
  const executionStatus = report.execution_status || "not recorded (legacy result)";
  const schemaV2 = Number(report.schema_version) >= 2;
  const auditCoverageIncomplete =
    report.execution_status !== "complete" ||
    !metrics.lensCoverageComplete ||
    metrics.failedVerifierSeats > 0 ||
    metrics.missingVerifierSeats > 0 ||
    metrics.invalidVerifierSeatRecords > 0 ||
    metrics.inconclusiveFindings > 0 ||
    (schemaV2 && metrics.candidatesWithPanelSize < metrics.totalCandidates);
  const inputCurrencyLabel = reportIsStale
    ? "stale review input"
    : report.input_fingerprint
      ? "current review input"
      : "legacy document-only match";

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 p-3 pt-6 sm:p-6 sm:pt-10"
      onClick={onClose}
    >
      <div
        ref={dialogRef}
        tabIndex={-1}
        className="flex max-h-[92vh] w-full max-w-6xl flex-col overflow-hidden rounded-2xl border border-edge bg-surface shadow-2xl"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="qc-report-title"
        aria-describedby="qc-report-description"
      >
        <header className="shrink-0 border-b border-edge bg-surface px-4 py-4 sm:px-6">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <p className="text-[10px] font-semibold tracking-[0.16em] text-accent uppercase">Complete audit record</p>
              <h2 id="qc-report-title" className="mt-0.5 font-[family-name:var(--font-display)] text-xl font-semibold text-ink">
                Final QC report
              </h2>
              <p id="qc-report-description" className="mt-1 max-w-3xl text-[11px] leading-relaxed text-ink-faint">
                The full work record behind the compact Final QC queue: what was reviewed, what each lens and verifier did, the evidence retrieved, the operations proposed, and every recorded limitation.
              </p>
              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                <Pill className={reportIsStale || !report.input_fingerprint ? "border-warn/50 bg-warn/10 text-warn" : "border-ok/40 bg-ok/10 text-ok"}>
                  {inputCurrencyLabel}
                </Pill>
                <Pill className={statusTone(executionStatus)}>{executionStatus}</Pill>
                <Pill className="border-edge bg-raised text-ink-dim">{report.model || "model not recorded"}</Pill>
                <Pill className="border-edge bg-raised text-ink-dim">run {report.run_id || "ID not recorded"}</Pill>
              </div>
            </div>
            <button
              ref={closeButtonRef}
              onClick={onClose}
              className="shrink-0 rounded-lg px-2 py-1 text-lg leading-none text-ink-dim transition-colors hover:bg-raised hover:text-ink"
              title="Close report"
              aria-label="Close Final QC report"
            >
              ×
            </button>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <a
              href={qcReportExportUrl("docx", report.run_id)}
              download
              title={`Download the backend-selected Word report; snapshot target is run ${report.run_id || "ID not recorded"}`}
              className="rounded-lg bg-accent px-3 py-1.5 text-[11px] font-semibold text-white transition-colors hover:bg-accent-hover"
            >
              Download Word report
            </a>
            <a
              href={qcReportExportUrl("json", report.run_id)}
              download
              title={`Download the backend-selected JSON report; snapshot target is run ${report.run_id || "ID not recorded"}`}
              className="rounded-lg border border-edge bg-raised px-3 py-1.5 text-[11px] font-semibold text-ink-dim transition-colors hover:border-accent hover:text-accent"
            >
              Download JSON record
            </a>
            <span className="text-[10px] text-ink-faint">This view and the download controls target backend-selected report run <span className="font-mono">{report.run_id || "ID not recorded"}</span> for this snapshot. When a run ID is available, each download is pinned to it and the server rejects a changed selection; every artifact also embeds its authoritative run ID. JSON is the unabridged machine-readable record.</span>
          </div>
        </header>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-4 sm:px-6">
          <div className="rounded-xl border border-accent/35 bg-accent/5 px-4 py-3 text-[11px] leading-relaxed text-ink-dim">
            <strong>Exact run targets:</strong> displayed primary report: <span className="font-mono">{report.run_id || "ID not recorded"}</span> ({reportSelectionSource}); actionable retained queue: <span className="font-mono">{retainedRunId || "none"}</span>; latest attempt: <span className="font-mono">{latestAttempt?.run_id || "not recorded"}</span>. Downloads are pinned to the displayed run when its ID is available and identify the selected run inside the artifact.
          </div>
          {(reportDiffersFromRetained || reportHasNoRetainedQueue) && (
            <div className="rounded-xl border border-warn/45 bg-warn/10 px-4 py-3 text-[11px] leading-relaxed text-warn">
              <strong>Paid attempt report selected:</strong> this view is showing run {report.run_id || "ID not recorded"} ({report.execution_status || "status not recorded"}) because the backend preserved it as the primary audit report. {retainedRunId ? `Apply and dismiss actions remain bound to retained queue run ${retainedRunId}; this report is read-only.` : "No retained actionable queue exists; this preserved report is read-only."}
            </div>
          )}
          {latestAttemptDiffersFromReport && (
            <div className="rounded-xl border border-err/45 bg-err/10 px-4 py-3 text-[11px] leading-relaxed text-err">
              <strong>Latest attempt differs from the displayed primary report:</strong>{" "}
              {latestAttempt?.run_id} is {latestAttempt?.status || "not recorded"}.
              {latestAttempt?.error ? ` ${latestAttempt.error}` : ""} It started {formatTimestamp(latestAttempt?.started_at)} and finished {formatTimestamp(latestAttempt?.finished_at)}. {latestAttempt?.report_available ? "The snapshot says an attempt report is available; a mismatch with the selected report requires refreshing status before reliance." : "No report was preserved for that attempt, so this view and the exports fall back to the retained report while preserving the failed-attempt metadata."} Issue readiness remains blocked until a current complete run succeeds.
            </div>
          )}
          {reportIsStale && (
            <div className="rounded-xl border border-warn/50 bg-warn/10 px-4 py-3 text-[11px] leading-relaxed text-warn">
              <strong>Stale review input:</strong> the active review input no longer matches the snapshot captured for this run. That can reflect a document-version change ({versionLabel(report.version_index)} reviewed; {versionLabel(currentVersion)} current) or a change to research, standards, module, or source-policy inputs. Preserve this record for traceability, but re-run Final QC before relying on it or applying its proposed operations.
            </div>
          )}
          {auditCoverageIncomplete && (
            <div className="rounded-xl border border-err/45 bg-err/10 px-4 py-3 text-[11px] leading-relaxed text-err">
              <strong>Partial audit coverage:</strong> this record cannot establish issue readiness. Recorded execution status: {executionStatus}. Lens coverage: {metrics.completedLenses}/{metrics.expectedLenses || metrics.totalLenses || "?"} completed/configured ({metrics.missingLenses} missing, {metrics.duplicateLensRecords} duplicate, {metrics.unexpectedLenses} unexpected, {metrics.lensesWithoutReviewedChecks} without a check ledger). Verifier coverage: {metrics.completedVerifierSeats}/{metrics.expectedVerifierSeats || "?"} completed/expected ({metrics.failedVerifierSeats} non-completed, {metrics.missingVerifierSeats} missing, {metrics.invalidVerifierSeatRecords} invalid or duplicate index). Infrastructure-inconclusive candidates: {metrics.inconclusiveFindings}; these received no substantive refutation.
            </div>
          )}

          <Section
            number="01"
            title="Executive result and current readiness"
            description="The result returned by the QC run, followed by the application’s current deterministic issue-readiness checks."
          >
            <div className="rounded-lg border border-edge/60 bg-surface/45 px-3 py-3">
              <p className="text-[9px] font-semibold tracking-wide text-ink-faint uppercase">Run summary</p>
              <p className="mt-1 whitespace-pre-wrap break-words text-[12px] leading-relaxed text-ink-dim">
                {report.summary || "No executive summary was recorded."}
              </p>
            </div>
            <div className="mt-3 grid grid-cols-2 gap-2 md:grid-cols-4">
              <Stat label="Candidates reviewed" value={metrics.totalCandidates.toLocaleString("en-US")} detail={`${metrics.survivingFindings} survived · ${metrics.refutedFindings} substantively refuted · ${metrics.inconclusiveFindings} infrastructure-inconclusive`} />
              <Stat label="Open findings" value={metrics.openFindings.toLocaleString("en-US")} detail={`${metrics.appliedFindings} applied · ${metrics.dismissedFindings} dismissed`} />
              <Stat label="Lens completion" value={`${metrics.completedLenses}/${metrics.expectedLenses || metrics.totalLenses || "?"}`} detail={`${metrics.totalLenses} records · ${metrics.missingLenses} missing · ${metrics.duplicateLensRecords} duplicate · ${metrics.unexpectedLenses} unexpected`} />
              <Stat label="Grounded survivors" value={`${metrics.groundedFindings}/${metrics.survivingFindings}`} detail={`${metrics.ungroundedFindings} survivor(s) not grounded`} />
            </div>
            <div className="mt-3"><ReadinessRecord readiness={readiness} /></div>
          </Section>

          <Section
            number="02"
            title="Run, protocol, and document identity"
            description="These values identify the exact execution and input snapshot. Full fingerprints are shown so the record can be compared without relying on labels alone."
          >
            <dl className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-4">
              <DataField label="Schema version">{recorded(report.schema_version)}</DataField>
              <DataField label="Protocol version">{recorded(report.protocol_version)}</DataField>
              <DataField label="Run ID" mono>{recorded(report.run_id)}</DataField>
              <DataField label="Primary report selection">{reportSelectionSource}</DataField>
              <DataField label="Retained action-queue run ID" mono>{recorded(retainedRunId)}</DataField>
              <DataField label="Execution status">{executionStatus}</DataField>
              <DataField label="Model">{recorded(report.model)}</DataField>
              <DataField label="Effort">{recorded(report.effort)}</DataField>
              <DataField label="Maximum output tokens">{formatInteger(report.max_tokens)}</DataField>
              <DataField label="Recorded duration">{formatDuration(duration)}</DataField>
              <DataField label="Started at">{formatTimestamp(report.started_at)}</DataField>
              <DataField label="Finished at">{formatTimestamp(report.finished_at)}</DataField>
              <DataField label="Reviewed document version">{versionLabel(report.version_index)}</DataField>
              <DataField label="Current document version">{versionLabel(currentVersion)}</DataField>
              <DataField label="Reviewed document fingerprint" mono>{recorded(report.version_fingerprint)}</DataField>
              <DataField label="QC input fingerprint" mono>{recorded(report.input_fingerprint)}</DataField>
              <DataField label="Requirements research present">{report.research_profile_present ? "Yes" : "No"}</DataField>
              <DataField label="Remembered dismissed IDs">{report.dismissed_ids.length.toLocaleString("en-US")}</DataField>
              <DataField label="Latest attempt run ID" mono>{recorded(latestAttempt?.run_id)}</DataField>
              <DataField label="Latest attempt status">{recorded(latestAttempt?.status)}</DataField>
              <DataField label="Latest attempt started">{formatTimestamp(latestAttempt?.started_at)}</DataField>
              <DataField label="Latest attempt finished">{formatTimestamp(latestAttempt?.finished_at)}</DataField>
              <DataField label="Latest attempt report preserved">{latestAttempt ? latestAttempt.report_available ? "Yes" : "No" : NOT_RECORDED}</DataField>
              <DataField label="Latest attempt error">{recorded(latestAttempt?.error)}</DataField>
            </dl>
            {report.dismissed_ids.length > 0 && (
              <div className="mt-3">
                <p className="mb-1.5 text-[9px] font-semibold tracking-wide text-ink-faint uppercase">Content-addressed dismissed finding IDs</p>
                <StringList values={report.dismissed_ids} empty="No remembered dismissed IDs." />
              </div>
            )}
          </Section>

          <Section
            number="03"
            title="Methodology, scope, and input manifest"
            description="A plain-language account of the recorded pipeline. The manifest below is emitted in full, retaining the machine-readable scope supplied to the run."
          >
            <ol className="grid grid-cols-1 gap-2 md:grid-cols-2">
              {[
                ["Snapshot and identity", "The application captured the selected document version, a content fingerprint, the active module/project inputs, and whether requirements research was available."],
                ["Parallel specialist lenses", "Each recorded lens received a defined brief and reviewed the whole input from its specialty. Lens check ledgers, searches, retrievals, summaries, errors, and usage appear in Section 05."],
                ["Evidence qualification", "Candidate citations were retrieved and checked. Accepted and rejected source checks are both retained; rejected records provide traceability but do not count as grounding."],
                ["Adversarial verification", "Candidate findings were sent to recorded reviewer seats. Each available verdict, note, revised severity, search, retrieval, error, and usage counter appears with the finding."],
                ["Outcome separation and operation validation", "Candidates are separated into surviving findings, substantively refuted candidates, and infrastructure-inconclusive candidates. Only surviving findings can enter the action queue. Proposed document operations are mechanically validated when eligible; unabridged JSON remains visible for every bucket."],
                ["Human disposition", "Open, applied, and dismissed states are kept separate from verification. Where supported by the record schema, later disposition events include time, reason, and document identity."],
              ].map(([title, body], index) => (
                <li key={title} className="rounded-lg border border-edge/60 bg-surface/45 px-3 py-2.5">
                  <p className="text-[11px] font-semibold text-ink">{index + 1}. {title}</p>
                  <p className="mt-1 text-[10px] leading-relaxed text-ink-faint">{body}</p>
                </li>
              ))}
            </ol>
            <div className="mt-3">
              {report.input_manifest ? (
                <JsonBlock value={report.input_manifest} label="Complete input manifest" />
              ) : (
                <EmptyRecord>No input manifest was recorded. This is expected for legacy results created before audit-grade capture.</EmptyRecord>
              )}
            </div>
          </Section>

          <Section
            number="04"
            title="Measured coverage, evidence, verification, and dispositions"
            description="Counts are derived from the full result, keeping surviving, substantively refuted, and infrastructure-inconclusive candidates distinct. Unknown values are never folded into “low” or dropped."
          >
            <div className="grid grid-cols-2 gap-2 md:grid-cols-4 lg:grid-cols-6">
              <Stat label="Search queries" value={metrics.searchQueries.toLocaleString("en-US")} />
              <Stat label="Retrieved source records" value={metrics.retrievedSourceRecords.toLocaleString("en-US")} />
              <Stat label="Unique source values" value={metrics.uniqueSourceUrls.toLocaleString("en-US")} detail={`${metrics.evidenceTraceRecords} total query/source trace entries`} />
              <Stat label="Source checks" value={metrics.sourceChecks.toLocaleString("en-US")} detail={`${metrics.acceptedSourceChecks} accepted · ${metrics.rejectedSourceChecks} rejected · ${metrics.unclassifiedSourceChecks} unclassified`} />
              <Stat label="Verifier records" value={metrics.verdicts.toLocaleString("en-US")} detail={`Completed-seat votes: ${metrics.upholdingVerdicts} uphold · ${metrics.refutingVerdicts} do not uphold`} />
              <Stat label="Proposed operations" value={metrics.proposedOperations.toLocaleString("en-US")} detail={`${metrics.findingsWithValidOperations} surviving candidate(s) valid · ${metrics.findingsWithInvalidOperations} invalid · ${metrics.unevaluatedRefutedOperations} refuted and ${metrics.unevaluatedInconclusiveOperations} inconclusive operation(s) not evaluated`} />
              <Stat label="API requests" value={metrics.apiRequests.toLocaleString("en-US")} />
              <Stat label="Model responses" value={metrics.modelResponses.toLocaleString("en-US")} />
              <Stat label="Verifier errors" value={metrics.verdictErrors.toLocaleString("en-US")} />
              <Stat label="Verifier seat coverage" value={`${metrics.completedVerifierSeats}/${metrics.expectedVerifierSeats || "?"}`} detail={`${metrics.recordedVerifierSeats} preserved · ${metrics.failedVerifierSeats} failed · ${metrics.missingVerifierSeats} missing · ${metrics.invalidVerifierSeatRecords} invalid index`} />
              <Stat label="Verification outcomes" value={`${metrics.candidatesWithVerificationOutcome}/${metrics.totalCandidates}`} detail={`${metrics.survivingFindings} upheld · ${metrics.refutedFindings} substantively refuted · ${metrics.inconclusiveFindings} infrastructure-inconclusive`} />
              <Stat label="Grounded candidates" value={`${metrics.groundedCandidates}/${metrics.totalCandidates}`} />
              <Stat label="Resolved element anchors" value={`${metrics.resolvedElementAnchors}/${metrics.totalCandidates}`} detail={`${metrics.unresolvedElementAnchors} unresolved · ${metrics.unrecordedElementResolution} legacy/unrecorded`} />
              <Stat label="Other dispositions" value={metrics.otherDispositionFindings.toLocaleString("en-US")} detail="Surviving findings outside open/applied/dismissed" />
            </div>
            <div className="mt-3 overflow-x-auto rounded-lg border border-edge/60">
              <table className="w-full min-w-[44rem] border-collapse text-left text-[11px]">
                <thead className="bg-raised text-[9px] tracking-wide text-ink-faint uppercase">
                  <tr>
                    <th className="border-b border-edge px-3 py-2 font-semibold">Severity</th>
                    <th className="border-b border-edge px-3 py-2 text-right font-semibold">All candidates</th>
                    <th className="border-b border-edge px-3 py-2 text-right font-semibold">Survived</th>
                    <th className="border-b border-edge px-3 py-2 text-right font-semibold">Substantively refuted</th>
                    <th className="border-b border-edge px-3 py-2 text-right font-semibold">Infrastructure-inconclusive</th>
                  </tr>
                </thead>
                <tbody>
                  {(["critical", "high", "medium", "low", "unknown"] as const).map((severity) => (
                    <tr key={severity} className="border-b border-edge/40 last:border-0">
                      <td className="px-3 py-2"><Pill className={severityTone[severity]}>{formatSeverity(severity)}</Pill></td>
                      <td className="px-3 py-2 text-right font-mono text-ink">{metrics.severity[severity]}</td>
                      <td className="px-3 py-2 text-right font-mono text-ink">{metrics.survivingSeverity[severity]}</td>
                      <td className="px-3 py-2 text-right font-mono text-ink">{metrics.refutedSeverity[severity]}</td>
                      <td className="px-3 py-2 text-right font-mono text-ink">{metrics.inconclusiveSeverity[severity]}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Section>

          <Section
            number="05"
            title={`Lens work papers (${report.lens_statuses.length})`}
            description="Every lens is shown in server order, including failures and lenses that produced no findings. Briefs, check ledgers, queries, retrievals, and usage are not collapsed."
          >
            {!lensCoverage.complete && (
              <div className="mb-3 rounded-lg border border-warn/45 bg-warn/10 px-3 py-2.5 text-[11px] leading-relaxed text-warn">
                Configured lens IDs: {lensCoverage.expectedIds.length ? lensCoverage.expectedIds.join(", ") : "not recorded"}. Recorded lens IDs: {lensCoverage.recordedIds.length ? lensCoverage.recordedIds.join(", ") : "none"}. Missing: {lensCoverage.missingIds.length ? lensCoverage.missingIds.join(", ") : "none"}; unexpected: {lensCoverage.unexpectedIds.length ? lensCoverage.unexpectedIds.join(", ") : "none"}; duplicated: {lensCoverage.duplicateIds.length ? lensCoverage.duplicateIds.join(", ") : "none"}; without reviewed checks: {lensCoverage.withoutReviewedChecks.length ? lensCoverage.withoutReviewedChecks.join(", ") : "none"}.
              </div>
            )}
            <div className="space-y-3">
              {report.lens_statuses.length > 0
                ? report.lens_statuses.map((lens, index) => <LensRecord key={`${lens.lens_id}-${index}`} lens={lens} index={index} />)
                : <EmptyRecord>No lens execution records were preserved.</EmptyRecord>}
            </div>
          </Section>

          <Section
            number="06"
            title={`Surviving findings (${findings.length})`}
            description="These candidates survived the aggregate verification decision. A finding may still be open, applied, or dismissed; disposition is not the same as verification."
          >
            {findings.length > 0 ? (
              <div className="space-y-4">
                {groupFindingsBySeverity(findings).map((group) => (
                  <div key={group.severity}>
                    <div className="mb-2 flex items-center gap-2">
                      <Pill className={severityTone[group.severity]}>{formatSeverity(group.severity)}</Pill>
                      <span className="text-[10px] text-ink-faint">{group.findings.length} finding{group.findings.length === 1 ? "" : "s"}</span>
                    </div>
                    <div className="space-y-3">
                      {group.findings.map((finding) => (
                        <FindingRecord key={finding.finding_id} finding={finding} index={findings.indexOf(finding)} kind="surviving" schemaVersion={report.schema_version} />
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            ) : <EmptyRecord>No candidate finding survived verification.</EmptyRecord>}
          </Section>

          <Section
            number="07"
            title={`Infrastructure-inconclusive candidate appendix (${inconclusive.length})`}
            description="These candidates did not receive enough usable completed verifier seats for a substantive determination. They are not open findings, but they are also not refuted; their full failed/cancelled seat evidence remains visible for follow-up."
          >
            {inconclusive.length > 0 ? (
              <div className="space-y-3">
                {inconclusive.map((finding, index) => (
                  <FindingRecord key={`${finding.finding_id}-${index}`} finding={finding} index={index} kind="inconclusive" schemaVersion={report.schema_version} />
                ))}
              </div>
            ) : <EmptyRecord>No candidates were infrastructure-inconclusive.</EmptyRecord>}
          </Section>

          <Section
            number="08"
            title={`Substantively refuted candidate appendix (${refuted.length})`}
            description="These candidates received a substantive verification decision that did not uphold them. They are not open issues, but their complete issue, evidence, verifier, operation, and disposition records remain visible for auditability."
          >
            {refuted.length > 0 ? (
              <div className="space-y-3">
                {refuted.map((finding, index) => (
                  <FindingRecord key={`${finding.finding_id}-${index}`} finding={finding} index={index} kind="refuted" schemaVersion={report.schema_version} />
                ))}
              </div>
            ) : <EmptyRecord>No candidate findings were substantively refuted.</EmptyRecord>}
          </Section>

          <Section
            number="09"
            title={`Complete proposed-operation register (${operationRecords.length})`}
            description="A consolidated, unabridged index of every proposed operation from surviving, substantively refuted, and infrastructure-inconclusive records. This repeats operation payloads intentionally so they can be audited without hunting through findings."
          >
            {operationRecords.length > 0 ? (
              <ol className="space-y-3">
                {operationRecords.map((record, index) => (
                  <li key={`${record.findingId}-${record.operationIndex}-${index}`} className="rounded-lg border border-edge/60 bg-surface/45 px-3 py-3">
                    <div className="mb-2 flex flex-wrap items-center gap-2 text-[10px] text-ink-faint">
                      <span className="font-semibold text-ink">Operation {index + 1}</span>
                      <Pill className={record.findingKind === "surviving" ? "border-ok/40 bg-ok/10 text-ok" : record.findingKind === "inconclusive" ? "border-warn/50 bg-warn/10 text-warn" : "border-edge bg-raised text-ink-dim"}>
                        {record.findingKind === "inconclusive" ? "infrastructure-inconclusive" : record.findingKind}
                      </Pill>
                      <Pill className={record.validationStatus === "valid" ? "border-ok/40 bg-ok/10 text-ok" : record.validationStatus === "invalid" ? "border-err/40 bg-err/10 text-err" : "border-warn/40 bg-warn/10 text-warn"}>
                        {record.validationStatus === "valid" ? "operation set valid" : record.validationStatus === "invalid" ? "operation set invalid" : record.findingKind === "inconclusive" ? "not evaluated — verification infrastructure-inconclusive" : "not evaluated — candidate substantively refuted"}
                      </Pill>
                      <span className="break-all font-mono">{record.findingId}</span>
                    </div>
                    <p className="mb-2 whitespace-pre-wrap break-words text-[11px] text-ink-dim">{record.findingTitle || "Untitled finding"}</p>
                    {record.validationStatus === "invalid" && record.invalidReason && <p className="mb-2 whitespace-pre-wrap break-words rounded border border-err/40 bg-err/10 px-2 py-1.5 text-[10px] text-err">Validation detail: {record.invalidReason}</p>}
                    <JsonBlock value={record.operation} label={`Finding operation ${record.operationIndex + 1}`} />
                  </li>
                ))}
              </ol>
            ) : <EmptyRecord>No proposed operations were recorded in any candidate bucket.</EmptyRecord>}
          </Section>

          <Section
            number="10"
            title={`Research and evidence trace index (${traceRecords.length})`}
            description="Every recorded lens query/retrieval, reviewed-check citation decision, finding source check, and verifier query/retrieval in execution order. Repeated values remain repeated because each occurrence documents separate work."
          >
            {traceRecords.length > 0 ? (
              <ol className="space-y-1.5">
                {traceRecords.map((record, index) => (
                  <li key={`${record.stage}-${record.ownerId}-${record.kind}-${index}`} className="rounded-lg border border-edge/60 bg-surface/45 px-3 py-2.5">
                    <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
                      <span className="font-mono text-[9px] text-ink-faint">{index + 1}</span>
                      <Pill className="border-edge bg-raised text-ink-dim">{record.stage}</Pill>
                      <Pill className={record.kind === "query" ? "border-accent/40 bg-accent/10 text-accent" : "border-ok/40 bg-ok/10 text-ok"}>{record.kind}</Pill>
                      <span className="break-all font-mono text-[9px] text-ink-faint">owner: {record.ownerId}</span>
                    </div>
                    {record.kind === "query" ? (
                      <p className="whitespace-pre-wrap break-words text-[11px] leading-relaxed text-ink-dim">{record.value}</p>
                    ) : (
                      <SourceValue url={record.value} title={record.title} methods={record.methods} accepted={record.accepted} reason={record.reason} originalUrl={record.originalValue} />
                    )}
                  </li>
                ))}
              </ol>
            ) : <EmptyRecord>No search or retrieval trace records were preserved.</EmptyRecord>}
          </Section>

          <Section
            number="11"
            title="Usage, request accounting, and estimated cost"
            description="Recorded counters are displayed by their exact wire keys. Cost is explicitly labeled as an estimate; provider billing remains authoritative."
          >
            <dl className="mb-3 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-4">
              <DataField label="Estimated run cost">{formatUsd(report.estimated_cost_usd)}</DataField>
              <DataField label="API request count">{formatInteger(report.api_request_count ?? metrics.apiRequests)}</DataField>
              <DataField label="Model response count">{formatInteger(report.model_response_count ?? metrics.modelResponses)}</DataField>
              <DataField label="Elapsed duration">{formatDuration(duration)}</DataField>
            </dl>
            <UsageTable usage={report.usage_totals} empty="No aggregate usage totals were recorded." />
            {report.cost_basis && Object.keys(report.cost_basis).length > 0 ? (
              <div className="mt-3">
                <JsonBlock value={report.cost_basis} label="Saved pricing basis used for this estimate" />
              </div>
            ) : (
              <div className="mt-3"><EmptyRecord>No saved pricing-rate snapshot was recorded; provider billing remains authoritative.</EmptyRecord></div>
            )}
          </Section>

          <Section
            number="12"
            title={`Limitations and reliance conditions (${limitations.length})`}
            description="These conditions define what this automated report can and cannot establish. Run-specific limitations are added from staleness, research, lens, verifier, evidence, and execution records."
          >
            <ol className="space-y-2">
              {limitations.map((limitation, index) => (
                <li key={`${limitation}-${index}`} className="flex gap-2 rounded-lg border border-warn/35 bg-warn/5 px-3 py-2.5 text-[11px] leading-relaxed text-ink-dim">
                  <span className="shrink-0 font-mono text-[10px] text-warn">{index + 1}.</span>
                  <span className="whitespace-pre-wrap break-words">{limitation}</span>
                </li>
              ))}
            </ol>
          </Section>
        </div>

        <footer className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-t border-edge bg-surface px-4 py-3 sm:px-6">
          <p className="max-w-3xl text-[10px] leading-relaxed text-ink-faint">
            This report is read-only. Apply or dismiss findings from the Final QC queue; those actions remain subject to server validation and document-version checks.
          </p>
          <button
            onClick={onClose}
            className="rounded-lg bg-accent px-4 py-1.5 text-[12px] font-semibold text-white transition-colors hover:bg-accent-hover"
          >
            Done
          </button>
        </footer>
      </div>
    </div>
  );
}
