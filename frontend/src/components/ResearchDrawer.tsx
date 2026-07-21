/**
 * The requirements-research surface under the document panel: the project
 * profile line, the launch button (enabled once the interview records a
 * complete profile), live per-dimension progress while a run streams, and
 * the grounded-citations list when it completes. Ungrounded items are
 * marked [UNVERIFIED]; process advisories are marked [PROCESS] and never
 * become spec text.
 */
import { useState } from "react";
import type {
  AuditCoverageStatus,
  AuditSnapshot,
  ResearchRunStatus,
  ResearchSnapshot,
  SpecDoc,
} from "../types";

interface Props {
  doc: SpecDoc | null;
  profileComplete: boolean;
  research: ResearchSnapshot | null;
  audit: AuditSnapshot | null;
  canAudit: boolean;
  busy: boolean;
  onStart: () => void;
  onStartAudit: () => void;
  onJump: (elementId: string) => void;
}

const statusLabel: Record<ResearchRunStatus, string> = {
  idle: "not run",
  running: "researching…",
  complete: "complete",
  failed: "failed",
};

function profileLine(doc: SpecDoc | null): string {
  const p = doc?.project_profile;
  if (!p || Object.keys(p).length === 0) return "";
  const where = [p.city, p.state_or_province, p.country]
    .filter(Boolean)
    .join(", ");
  return [where, p.client_name ? `Client: ${p.client_name}` : ""]
    .filter(Boolean)
    .join(" — ");
}

const coverageChip: Record<AuditCoverageStatus, string> = {
  represented: "border-ok/50 bg-ok/15 text-ok",
  missing: "border-err/50 bg-err/15 text-err",
  contradicted: "border-err/50 bg-err/15 text-err",
  unclear: "border-warn/50 bg-warn/15 text-warn",
};

export default function ResearchDrawer({
  doc,
  profileComplete,
  research,
  audit,
  canAudit,
  busy,
  onStart,
  onStartAudit,
  onJump,
}: Props) {
  const [expanded, setExpanded] = useState(false);
  const status: ResearchRunStatus = research?.status ?? "idle";
  const running = status === "running";
  const items = research?.profile?.items ?? [];
  const grounded = items.filter((i) => i.grounded).length;
  const profile = profileLine(doc);
  const lastEvent = research?.events[research.events.length - 1];
  const auditStatus = audit?.status ?? "idle";
  const auditResult = audit?.result;
  const auditStale =
    auditResult != null &&
    doc != null &&
    auditResult.version_index !== doc.version.index;

  if (!profile && status === "idle") return null;

  return (
    <div className="border-t border-edge bg-bg/70 px-5 py-2">
      <div className="flex items-baseline gap-2">
        <button
          className="flex min-w-0 flex-1 items-baseline gap-2 text-left text-[11px] text-ink-faint transition-colors hover:text-ink-dim"
          onClick={() => setExpanded((v) => !v)}
          title="Project profile and grounded requirements research"
        >
          <span className="shrink-0 font-medium tracking-wide uppercase">
            Research
          </span>
          <span className="truncate">
            {profile || "profile pending"}
            {" · "}
            {statusLabel[status]}
            {status === "complete" &&
              ` · ${grounded}/${items.length} grounded`}
            {running && lastEvent?.done != null &&
              ` (${lastEvent.done}/${lastEvent.total} dimensions)`}
          </span>
          <span className="ml-auto shrink-0">{expanded ? "▾" : "▸"}</span>
        </button>
        <button
          className="shrink-0 rounded-md border border-edge bg-raised px-2 py-0.5 text-[11px] text-ink-dim transition-colors hover:border-accent hover:text-accent disabled:pointer-events-none disabled:opacity-40"
          onClick={onStart}
          disabled={!profileComplete || running || busy}
          title={
            profileComplete
              ? "Run grounded web research for this jurisdiction, AHJ, and client (uses your API key)"
              : "Complete the project profile in the interview first (city, state, country, client)"
          }
        >
          {status === "complete" ? "Re-research" : "Research requirements"}
        </button>
        <button
          className="shrink-0 rounded-md border border-edge bg-raised px-2 py-0.5 text-[11px] text-ink-dim transition-colors hover:border-accent hover:text-accent disabled:pointer-events-none disabled:opacity-40"
          onClick={onStartAudit}
          disabled={!canAudit || auditStatus === "running" || busy}
          title={
            canAudit
              ? "Audit the draft against the researched requirements (uses your API key)"
              : "Needs a completed research run and a non-empty draft"
          }
        >
          {auditStatus === "running"
            ? "Auditing…"
            : auditResult
              ? "Re-audit"
              : "Audit draft"}
        </button>
      </div>

      {status === "failed" && research?.error && (
        <p className="mt-1 text-[11px] text-err">{research.error}</p>
      )}
      {auditStatus === "failed" && audit?.error && (
        <p className="mt-1 text-[11px] text-err">Audit: {audit.error}</p>
      )}

      {auditResult && (
        <div className="mt-1.5 border-t border-edge/60 pt-1.5">
          <p className="text-[11px] text-ink-faint">
            Compliance audit — {auditResult.audited_at} (v
            {auditResult.version_index + 1})
            {auditStale && (
              <span className="ml-1 font-semibold text-warn">
                · stale (draft has changed — re-audit)
              </span>
            )}
          </p>
          <ul className="mt-1 max-h-40 space-y-0.5 overflow-y-auto">
            {auditResult.coverage.map((entry) => (
              <li
                key={entry.requirement_id}
                className="flex items-baseline gap-2 text-[11px]"
              >
                <span
                  className={`shrink-0 rounded border px-1 py-px text-[9px] font-semibold uppercase ${coverageChip[entry.status]}`}
                >
                  {entry.status}
                </span>
                <button
                  className="min-w-0 truncate text-left text-ink-dim hover:text-ink"
                  onClick={() =>
                    entry.element_id && onJump(entry.element_id)
                  }
                  title={entry.evidence_quote || entry.note}
                >
                  [{entry.requirement_id}] {entry.note || entry.evidence_quote}
                </button>
              </li>
            ))}
            {auditResult.findings.map((finding, i) => (
              <li
                key={`f-${i}`}
                className="flex items-baseline gap-2 text-[11px]"
              >
                <span className="shrink-0 rounded border border-err/50 bg-err/15 px-1 py-px text-[9px] font-semibold text-err uppercase">
                  {finding.severity}
                </span>
                <button
                  className="min-w-0 truncate text-left text-ink-dim hover:text-ink"
                  onClick={() =>
                    finding.element_id && onJump(finding.element_id)
                  }
                  title={finding.suggestion || finding.issue}
                >
                  {finding.issue}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {expanded && research && (
        <div className="mt-1.5 max-h-64 space-y-2 overflow-y-auto pb-1">
          {running && (
            <ul className="space-y-0.5">
              {research.events.map((e) => (
                <li key={e.seq} className="text-[11px] text-ink-faint">
                  {e.ts}{" "}
                  {e.type === "research_started" &&
                    `Researching ${e.project ?? ""}…`}
                  {e.type === "dimension_complete" &&
                    `✓ ${e.title ?? e.dimension_id}: ${e.item_count} item(s), ${e.grounded_count} grounded`}
                  {e.type === "dimension_failed" &&
                    `✗ ${e.title ?? e.dimension_id}: ${e.error}`}
                </li>
              ))}
            </ul>
          )}

          {research.profile && (
            <>
              <p className="text-[11px] text-ink-faint">
                Researched {research.profile.research_date}
                {research.profile.dimension_statuses.some(
                  (d) => d.status !== "completed",
                ) &&
                  ` — partial (${
                    research.profile.dimension_statuses.filter(
                      (d) => d.status !== "completed",
                    ).length
                  } dimension(s) failed)`}
              </p>
              <ul className="space-y-1">
                {items.map((item) => (
                  <li key={item.item_id} className="text-[11px]">
                    <div className="flex items-baseline gap-2">
                      <span
                        className={`h-1.5 w-1.5 shrink-0 translate-y-[-1px] rounded-full ${
                          item.grounded ? "bg-ok" : "bg-warn"
                        }`}
                        title={item.grounded ? "Grounded" : "Unverified"}
                      />
                      <span className="min-w-0 text-ink-dim">
                        {!item.grounded && (
                          <span className="font-semibold text-warn">
                            [UNVERIFIED]{" "}
                          </span>
                        )}
                        {item.actionability === "process_advisory" && (
                          <span className="font-semibold text-ink-faint">
                            [PROCESS]{" "}
                          </span>
                        )}
                        {item.requirement}
                        {item.authority && (
                          <span className="text-ink-faint">
                            {" "}
                            — {item.authority}
                          </span>
                        )}
                        {item.accepted_sources.map((url) => (
                          <a
                            key={url}
                            href={url}
                            target="_blank"
                            rel="noreferrer"
                            className="ml-1 text-accent hover:underline"
                            title={url}
                          >
                            [src]
                          </a>
                        ))}
                      </span>
                    </div>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
    </div>
  );
}
