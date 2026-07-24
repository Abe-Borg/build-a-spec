/**
 * Final QC on Fable 5 (Batch 4): one button, a fleet of Fable 5 reviewers,
 * an accept/dismiss fix queue, and an issue-readiness checklist.
 *
 * Idle → a "Send to Final QC" button + a cost expectation line + the
 * readiness checklist. The button never launches a run directly: because a
 * pass runs on Fable 5 (expensive) and takes minutes, it opens a confirmation
 * dialog that spells out what the pass does, why it costs, and why it's slow —
 * the user opts in explicitly. Running → the five lens rows with live status,
 * then a
 * "Verifying findings…" counter (fed by the SSE stream). Complete → findings
 * grouped by severity, each with a jump-to-element ref, collapsible
 * rationale, an Apply fix (with an ops preview) / Dismiss action, an "Apply
 * all criticals" hold-to-confirm, and the refuted findings collapsed for
 * transparency, while infrastructure-inconclusive candidates stay in their
 * own non-actionable warning bucket. A staleness banner shows when the
 * document has moved on from the version QC reviewed.
 *
 * Reuses Batch 2's status machinery (no dead air — a QC run takes minutes and
 * the drawer shows lens-by-lens progress the whole time) and Batch 3's
 * hold-to-confirm affordance.
 */
import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { RefObject } from "react";
import type {
  QcFinding,
  QcSnapshot,
  ReadinessPayload,
  Severity,
  SourceCapabilitiesState,
  SourceOperationCapability,
  SpecDoc,
  UsageSummary,
} from "../types";
import {
  qcBatchDecision,
  sourceCapabilityTitle,
} from "../lib/sourceCapabilities";
import {
  qcInconclusiveCandidates,
  qcPrimaryReport,
  qcReportExportUrl,
  qcSubstantivelyRefutedCandidates,
  qcSurvivingCandidates,
  safeHttpUrl,
} from "../lib/qcReport";
import { useDialogFocus } from "../lib/dialogFocus";
import ConfirmDialog from "./ConfirmDialog";
import QCReportModal from "./QCReportModal";

interface Props {
  qc: QcSnapshot | null;
  readiness: ReadinessPayload | null;
  doc: SpecDoc | null;
  busy: boolean;
  sourceExpected: boolean;
  sourceCapabilities: SourceCapabilitiesState | null;
  usage: UsageSummary | null;
  onStart: () => void;
  onStop: () => void;
  onApply: (findingIds: string[]) => void;
  onDismiss: (findingId: string, reason: string) => Promise<void>;
  onJump: (elementId: string) => void;
  /** Guided-tour "ensure open" (Batch 6): a bump expands the drawer. */
  openNonce?: number;
}

const HOLD_MS = 800;
const SEVERITY_ORDER: Severity[] = ["critical", "high", "medium", "low"];
const QC_BUSY_MESSAGE = "Wait for the current action to finish.";

const sevChip: Record<Severity, string> = {
  critical: "border-err/70 bg-err/25 text-err",
  high: "border-err/50 bg-err/12 text-err",
  medium: "border-warn/50 bg-warn/15 text-warn",
  low: "border-ink-faint/50 bg-ink-faint/10 text-ink-faint",
};

const LENS_ORDER = [
  "code_compliance",
  "coordination_consistency",
  "completeness",
  "enforceability_language",
  "provenance_hygiene",
];

function opPreview(op: Record<string, unknown>): string {
  const action = String(op.action ?? "");
  const target = String(op.target_id ?? "");
  const text = op.text != null ? String(op.text) : "";
  const trimmed = text.length > 80 ? `${text.slice(0, 80)}…` : text;
  if (action === "delete") return `delete ${target}`;
  if (action === "set_status")
    return `mark ${target} → ${String(op.status ?? "")}`;
  if (action === "set_standard_edition")
    return `${String(op.standard ?? "")} → ${String(op.edition ?? "")}`;
  if (trimmed) return `${action} ${target}: “${trimmed}”`;
  return `${action} ${target}`;
}

export default function QCDrawer({
  qc,
  readiness,
  doc,
  busy,
  sourceExpected,
  sourceCapabilities,
  usage,
  onStart,
  onStop,
  onApply,
  onDismiss,
  onJump,
  openNonce,
}: Props) {
  const [expanded, setExpanded] = useState(false);
  // The tour opens the drawer by bumping the nonce; the user can still
  // collapse it freely — the tour never fights back.
  useEffect(() => {
    if (openNonce) setExpanded(true);
  }, [openNonce]);
  const [openRationale, setOpenRationale] = useState<Record<string, boolean>>({});
  const [showRefuted, setShowRefuted] = useState(false);
  const [showInconclusive, setShowInconclusive] = useState(false);
  const [holding, setHolding] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [stopConfirmOpen, setStopConfirmOpen] = useState(false);
  const [reportOpen, setReportOpen] = useState(false);
  const [dismissTarget, setDismissTarget] = useState<QcFinding | null>(null);
  const [dismissReason, setDismissReason] = useState("");
  const [dismissPending, setDismissPending] = useState(false);
  const [dismissError, setDismissError] = useState("");
  const drawerToggleRef = useRef<HTMLButtonElement>(null);
  const holdTimer = useRef<number | undefined>(undefined);
  const latestApplyAll = useRef({
    busy: true,
    findingIds: [] as string[],
    onApply,
  });
  useEffect(() => () => window.clearTimeout(holdTimer.current), []);

  const status = qc?.status ?? "idle";
  const running = status === "running";
  const settling = qc?.settling ?? false;
  const interactionBusy = busy || settling;
  const result = qc?.result;
  const primaryReport = qcPrimaryReport(qc);
  const findings = result ? qcSurvivingCandidates(result) : [];
  const refuted = result ? qcSubstantivelyRefutedCandidates(result) : [];
  const inconclusive = result ? qcInconclusiveCandidates(result) : [];
  const openFindings = findings.filter((f) => f.status === "open");
  const openCriticals = openFindings.filter((f) => f.severity === "critical");
  const stale =
    result != null &&
    ((qc?.stale ?? false) ||
      (doc != null && result.version_index !== doc.version.index));
  const applyStateStale = result != null && (doc == null || stale);
  const findingDecisions = new Map(
    findings.map((finding) => [
      finding.finding_id,
      qcBatchDecision({
        finding,
        sourceCapabilities,
        sourceExpected,
        stale: applyStateStale,
      }),
    ]),
  );
  const applicableCriticals = openCriticals.filter(
    (finding) => findingDecisions.get(finding.finding_id)?.allowed,
  );
  const firstCriticalDenial = openCriticals
    .map((finding) => findingDecisions.get(finding.finding_id))
    .find((decision) => decision && !decision.allowed);
  latestApplyAll.current = {
    busy: interactionBusy,
    findingIds: applicableCriticals.map((finding) => finding.finding_id),
    onApply,
  };

  // Live lens + verify progress from the SSE event log.
  const { lensState, verify } = useMemo(() => {
    const state: Record<string, "pending" | "done" | "failed"> = {};
    for (const id of LENS_ORDER) state[id] = "pending";
    let v: { done: number; total: number } | null = null;
    for (const e of qc?.events ?? []) {
      if (e.type === "lens_complete" && e.lens_id) state[e.lens_id] = "done";
      if (e.type === "lens_failed" && e.lens_id) state[e.lens_id] = "failed";
      if (e.type === "verify_progress" && e.total != null)
        v = { done: e.done ?? 0, total: e.total };
    }
    return { lensState: state, verify: v };
  }, [qc?.events]);

  const startHoldApplyCriticals = () => {
    if (interactionBusy || applicableCriticals.length === 0) return;
    setHolding(true);
    holdTimer.current = window.setTimeout(() => {
      setHolding(false);
      const latest = latestApplyAll.current;
      if (latest.busy || latest.findingIds.length === 0) return;
      latest.onApply([...latest.findingIds]);
    }, HOLD_MS);
  };
  const cancelHold = () => {
    setHolding(false);
    window.clearTimeout(holdTimer.current);
  };

  const observedCost = usage?.estimated_cost_usd.by_category.qc;
  const costLine =
    observedCost && observedCost > 0
      ? `Runs on Claude Fable 5 — the strongest model. This session's QC: ≈ $${observedCost.toFixed(2)}.`
      : "Runs on Claude Fable 5 — the strongest model. Typically a few dollars per pass.";

  // Cost-focused line for the confirmation dialog (the model name is already
  // stated there). A re-run folds the session's prior QC spend in.
  const costEstimate =
    observedCost && observedCost > 0
      ? `This session's Final QC has cost ≈ $${observedCost.toFixed(2)} so far; expect a few dollars for another pass.`
      : "Expect a few dollars per pass.";

  const startLabel = settling
    ? "Preserving report…"
    : running
      ? "Reviewing…"
    : primaryReport
      ? "Re-run Final QC"
      : "Send to Final QC";

  const applyAllDisabled = interactionBusy || applicableCriticals.length === 0;
  const applyAllTitle =
    applicableCriticals.length === 0 && firstCriticalDenial
      ? sourceCapabilityTitle(
          firstCriticalDenial,
          "Apply currently applicable critical findings",
        )
      : settling
        ? "Stop requested; wait while paid Final QC activity is preserved."
        : busy
        ? QC_BUSY_MESSAGE
        : "Press and hold to apply only the critical findings that are currently applicable; the server validates the combined batch again.";

  // The start button opens the confirmation dialog; the run only fires once
  // the user confirms in it (Fable 5 is expensive and a pass takes minutes).
  const confirmStart = () => {
    setConfirmOpen(false);
    onStart();
  };

  return (
    <>
    <div
      className="border-t border-edge bg-bg/70 px-5 py-2"
      data-tour="qc-drawer"
    >
      <div className="flex items-baseline gap-2">
        <button
          ref={drawerToggleRef}
          className="flex min-w-0 flex-1 items-baseline gap-2 text-left text-[11px] text-ink-faint transition-colors hover:text-ink-dim"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          aria-controls="final-qc-drawer-body"
          title="Final QC — a fleet of Fable 5 reviewers before the section goes out the door"
        >
          <span className="shrink-0 font-medium tracking-wide uppercase">
            Final QC
          </span>
          <span className="truncate">
            {settling
              ? "stop requested · preserving paid activity…"
              : running && verify
              ? `verifying findings… (${verify.done}/${verify.total})`
              : running
                ? "reviewing…"
                : result
                  ? `${openFindings.length} open finding${openFindings.length === 1 ? "" : "s"}` +
                    (openCriticals.length ? ` · ${openCriticals.length} critical` : "") +
                    (inconclusive.length ? ` · ${inconclusive.length} inconclusive` : "")
                  : primaryReport
                    ? `${primaryReport.execution_status || "preserved"} report available · no action queue`
                  : readiness?.ready
                    ? "ready to issue"
                    : "not yet reviewed"}
          </span>
          <span className="ml-auto shrink-0">{expanded ? "▾" : "▸"}</span>
        </button>
        <button
          className={`shrink-0 rounded-md border px-2 py-0.5 text-[11px] transition-colors disabled:pointer-events-none disabled:opacity-40 ${
            primaryReport
              ? "border-edge bg-raised text-ink-dim hover:border-accent hover:text-accent"
              : "border-accent/70 bg-accent/15 text-accent hover:bg-accent/25"
          }`}
          onClick={() => setConfirmOpen(true)}
          disabled={running || interactionBusy}
          title={settling ? "Stop requested; the paid partial audit record is still being preserved." : "Review what a pass costs and does, then confirm — runs the full lens fan-out + adversarial verification on Fable 5 (uses your API key)"}
        >
          {startLabel}
        </button>
        {running && (
          <button
            className="shrink-0 rounded-md border border-edge bg-raised px-2 py-0.5 text-[11px] text-ink-dim transition-colors hover:border-err hover:text-err"
            onClick={() => setStopConfirmOpen(true)}
            title="Stop Final QC"
          >
            Stop
          </button>
        )}
      </div>

      {settling && (
        <p
          className="status-shimmer mt-1 rounded border border-warn/40 bg-warn/10 px-2 py-1 text-[11px] font-medium text-warn"
          role="status"
        >
          Stop requested; preserving completed and billed Final QC activity…
          Re-run and disposition controls will unlock after the worker settles.
        </p>
      )}

      {status === "failed" && qc?.error && !settling && (
        <p className="mt-1 text-[11px] text-err">Final QC: {qc.error}</p>
      )}

      <ConfirmDialog
        open={stopConfirmOpen}
        title="Stop Final QC?"
        body={
          <p>
            This stops the Final QC pass now in progress.{" "}
            <strong className="text-ink">
              Completed and billed activity will be preserved in a partial,
              read-only audit report when the worker can return it
            </strong>{" "}
            — it cannot establish issue readiness or create an actionable
            finding queue, and the Fable 5 spend already incurred is not
            refunded. You&apos;ll need a complete re-run before issue.
          </p>
        }
        confirmLabel="Stop Final QC"
        danger
        onConfirm={() => {
          setStopConfirmOpen(false);
          onStop();
        }}
        onCancel={() => setStopConfirmOpen(false)}
      />

      {expanded && (
        <div
          id="final-qc-drawer-body"
          className="mt-1.5 max-h-[28rem] space-y-3 overflow-y-auto pb-1"
        >
          {/* Issue readiness */}
          {readiness && (
            <div
              className="rounded-lg border border-edge bg-surface/50 p-2.5"
              data-tour="readiness"
            >
              <p className="flex items-center gap-2 text-[11px] font-medium tracking-wide text-ink-dim uppercase">
                Issue readiness
                <span
                  className={`rounded-full px-1.5 py-px text-[9px] font-semibold normal-case ${
                    readiness.ready
                      ? "bg-ok/20 text-ok"
                      : "bg-warn/15 text-warn"
                  }`}
                >
                  {readiness.ready ? "ready ✓" : "not ready"}
                </span>
              </p>
              <ul className="mt-1.5 space-y-0.5">
                {readiness.checks.map((c) => (
                  <li
                    key={c.id}
                    className="flex items-baseline gap-2 text-[11px] text-ink-dim"
                  >
                    <span
                      className={`h-1.5 w-1.5 shrink-0 translate-y-[-1px] rounded-full ${
                        c.ok ? "bg-ok" : c.advisory ? "bg-ink-faint" : "bg-warn"
                      }`}
                    />
                    <span className="min-w-0">
                      {c.detail}
                      {c.advisory && !c.ok && (
                        <span className="text-ink-faint"> (advisory)</span>
                      )}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Cost expectation (idle only) */}
          {status !== "running" && !settling && !result && (
            <p className="text-[11px] text-ink-faint italic">{costLine}</p>
          )}

          {/* Running: lens rows + verify counter */}
          {running && (
            <div className="rounded-lg border border-edge bg-surface/50 p-2.5">
              <ul className="space-y-0.5">
                {LENS_ORDER.map((id) => {
                  const s = lensState[id];
                  return (
                    <li
                      key={id}
                      className="flex items-baseline gap-2 text-[11px] text-ink-dim"
                    >
                      <span
                        className={`h-1.5 w-1.5 shrink-0 translate-y-[-1px] rounded-full ${
                          s === "done"
                            ? "bg-ok"
                            : s === "failed"
                              ? "bg-err"
                              : "animate-pulse bg-accent/60"
                        }`}
                      />
                      <span>{id.replace(/_/g, " ")}</span>
                      <span className="text-ink-faint">
                        {s === "done" ? "✓" : s === "failed" ? "failed" : "…"}
                      </span>
                    </li>
                  );
                })}
              </ul>
              {verify && (
                <p className="status-shimmer mt-1.5 text-[11px]">
                  Verifying findings… ({verify.done}/{verify.total})
                </p>
              )}
            </div>
          )}

          {primaryReport && !running && !result && (
            <div className="rounded-lg border border-warn/40 bg-warn/10 p-2.5 text-[11px] text-warn">
              <p className="leading-relaxed">
                A paid {primaryReport.execution_status || "partial"} audit
                report was preserved for run {primaryReport.run_id || "ID not recorded"},
                but there is no retained actionable finding queue. Review or
                download that exact report before re-running.
              </p>
              <div className="mt-2">
                <QCReportActions
                  reportRunId={primaryReport.run_id}
                  onView={() => setReportOpen(true)}
                />
              </div>
            </div>
          )}

          {/* Complete: findings */}
          {result && !running && (
            <>
              {stale && (
                <p className="rounded border border-warn/40 bg-warn/10 px-2 py-1 text-[11px] font-medium text-warn">
                  The document has changed since this QC ran (v
                  {result.version_index + 1}). Re-run Final QC before relying on
                  it.
                </p>
              )}
              {!result.research_profile_present && (
                <p className="text-[11px] text-ink-faint italic">
                  No research profile was present — run requirements research
                  first for full completeness coverage.
                </p>
              )}
              {result.execution_status !== "complete" && (
                <p className="rounded border border-warn/40 bg-warn/10 px-2 py-1 text-[11px] font-medium text-warn">
                  This is a partial QC record. One or more lens checks or
                  verifier seats did not complete; it cannot establish issue
                  readiness.
                </p>
              )}
              {qc?.latest_attempt?.run_id &&
                qc.latest_attempt.run_id !== result.run_id && (
                  <p className="rounded border border-err/40 bg-err/10 px-2 py-1 text-[11px] font-medium text-err">
                    Latest attempt {qc.latest_attempt.run_id} is{" "}
                    {qc.latest_attempt.status || "not recorded"}. The action
                    queue below remains retained run {result.run_id || "ID not recorded"};
                    the report controls target backend-selected run {primaryReport?.run_id || result.run_id || "ID not recorded"}.
                    {qc.latest_attempt.error
                      ? ` ${qc.latest_attempt.error}`
                      : ""}
                  </p>
                )}
              <QCReportActions
                reportRunId={primaryReport?.run_id ?? result.run_id}
                onView={() => setReportOpen(true)}
              />
              {result.summary && (
                <p className="text-[11px] text-ink-dim">{result.summary}</p>
              )}

              {openCriticals.length >= 2 && (
                <span
                  className="block w-full"
                  title={applyAllDisabled ? applyAllTitle : undefined}
                >
                  <button
                    className="relative w-full overflow-hidden rounded-md border border-err/50 bg-err/10 px-2 py-1 text-[11px] text-err transition-colors hover:border-err disabled:pointer-events-none disabled:opacity-40"
                    onPointerDown={startHoldApplyCriticals}
                    onPointerUp={cancelHold}
                    onPointerLeave={cancelHold}
                    onPointerCancel={cancelHold}
                    disabled={applyAllDisabled}
                    title={applyAllDisabled ? undefined : applyAllTitle}
                  >
                    <span
                      className="absolute inset-y-0 left-0 bg-err/25"
                      style={{
                        width: holding ? "100%" : "0%",
                        transition: holding
                          ? `width ${HOLD_MS}ms linear`
                          : "width 120ms ease-out",
                      }}
                    />
                    <span className="relative">
                      {holding
                        ? "Keep holding…"
                        : `Hold to apply ${applicableCriticals.length} currently applicable critical${applicableCriticals.length === 1 ? "" : "s"}`}
                    </span>
                  </button>
                </span>
              )}

              {openFindings.length === 0 && findings.length === 0 && inconclusive.length === 0 && (
                <p className="text-[11px] text-ok">
                  No findings survived adversarial verification. Review the
                  full report for lens coverage and substantively refuted candidates.
                </p>
              )}
              {openFindings.length === 0 && findings.length === 0 && inconclusive.length > 0 && (
                <p className="rounded border border-warn/40 bg-warn/10 px-2 py-1 text-[11px] text-warn">
                  No findings survived into the action queue, but {inconclusive.length} candidate{inconclusive.length === 1 ? " is" : "s are"} infrastructure-inconclusive. {inconclusive.length === 1 ? "It has" : "They have"} no substantive uphold/refute determination; inspect the failed or cancelled verifier seats in the full report.
                </p>
              )}
              {openFindings.length === 0 && findings.length > 0 && (
                <p className="text-[11px] text-ok">
                  No open findings — every surviving finding is applied or
                  dismissed. Review the disposition trail in the full report.
                </p>
              )}

              {SEVERITY_ORDER.map((sev) => {
                const band = findings.filter((f) => f.severity === sev);
                if (band.length === 0) return null;
                return (
                  <div key={sev} className="space-y-1.5">
                    <p className="text-[10px] font-semibold tracking-wide text-ink-faint uppercase">
                      {sev} ({band.length})
                    </p>
                    {band.map((f) => (
                      <FindingCard
                        key={f.finding_id}
                        finding={f}
                        busy={interactionBusy}
                        decision={findingDecisions.get(f.finding_id)!}
                        open={!!openRationale[f.finding_id]}
                        onToggle={() =>
                          setOpenRationale((m) => ({
                            ...m,
                            [f.finding_id]: !m[f.finding_id],
                          }))
                        }
                        onApply={() => {
                          const decision = qcBatchDecision({
                            finding: f,
                            sourceCapabilities,
                            sourceExpected,
                            stale: applyStateStale,
                          });
                          if (interactionBusy || !decision.allowed) return;
                          onApply([f.finding_id]);
                        }}
                        onDismiss={() => {
                          setDismissTarget(f);
                          setDismissReason("");
                          setDismissError("");
                        }}
                        onJump={onJump}
                      />
                    ))}
                  </div>
                );
              })}

              {inconclusive.length > 0 && (
                <div className="rounded border border-warn/35 bg-warn/5 px-2 py-1.5">
                  <button
                    className="text-left text-[11px] font-medium text-warn hover:underline"
                    onClick={() => setShowInconclusive((value) => !value)}
                    aria-expanded={showInconclusive}
                    aria-controls="qc-inconclusive-candidates"
                  >
                    {showInconclusive ? "▾" : "▸"} Infrastructure-inconclusive (
                    {inconclusive.length}) — no substantive refutation
                  </button>
                  {showInconclusive && (
                    <ul id="qc-inconclusive-candidates" className="mt-1.5 space-y-1">
                      {inconclusive.map((finding) => (
                        <li
                          key={finding.finding_id}
                          className="text-[11px] text-ink-dim"
                        >
                          <span className="font-medium text-warn">[{finding.severity}]</span>{" "}
                          {finding.title} — verification infrastructure incomplete
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}

              {refuted.length > 0 && (
                <div>
                  <button
                    className="text-[11px] text-ink-faint hover:text-ink-dim"
                    onClick={() => setShowRefuted((v) => !v)}
                    aria-expanded={showRefuted}
                    aria-controls="qc-refuted-candidates"
                  >
                    {showRefuted ? "▾" : "▸"} Substantively refuted in verification (
                    {refuted.length}) — not open issues
                  </button>
                  {showRefuted && (
                    <ul id="qc-refuted-candidates" className="mt-1 space-y-0.5">
                      {refuted.map((f) => (
                        <li
                          key={f.finding_id}
                          className="text-[11px] text-ink-faint line-through"
                        >
                          [{f.severity}] {f.title}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>

    {confirmOpen && (
      <ConfirmQCModal
        isRerun={!!primaryReport}
        costEstimate={costEstimate}
        busy={interactionBusy}
        onConfirm={confirmStart}
        onCancel={() => setConfirmOpen(false)}
      />
    )}
    <QCReportModal
      open={reportOpen}
      snapshot={qc}
      retainedStale={stale}
      currentVersion={doc?.version.index}
      readiness={readiness}
      onClose={() => setReportOpen(false)}
    />
    <DismissQCModal
      finding={dismissTarget}
      reason={dismissReason}
      busy={interactionBusy}
      pending={dismissPending}
      error={dismissError}
      restoreFocusRef={drawerToggleRef}
      onReasonChange={(value) => {
        setDismissReason(value);
        if (dismissError) setDismissError("");
      }}
      onConfirm={async () => {
        if (!dismissTarget || !dismissReason.trim()) return;
        setDismissPending(true);
        setDismissError("");
        try {
          await onDismiss(dismissTarget.finding_id, dismissReason.trim());
          setDismissTarget(null);
          setDismissReason("");
        } catch (error) {
          setDismissError(
            error instanceof Error ? error.message : String(error),
          );
        } finally {
          setDismissPending(false);
        }
      }}
      onCancel={() => {
        if (dismissPending) return;
        setDismissTarget(null);
        setDismissReason("");
        setDismissError("");
      }}
    />
    </>
  );
}

function QCReportActions({
  reportRunId,
  onView,
}: {
  reportRunId: string | undefined;
  onView: () => void;
}) {
  const target = reportRunId || "ID not recorded";
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <button
        className="rounded-md border border-accent/60 bg-accent/10 px-2 py-1 text-[11px] font-medium text-accent transition-colors hover:bg-accent/20"
        onClick={onView}
        aria-haspopup="dialog"
      >
        View full QC report
      </button>
      <a
        className="rounded-md border border-edge bg-raised px-2 py-1 text-[11px] text-ink-dim transition-colors hover:border-accent hover:text-accent"
        href={qcReportExportUrl("docx", reportRunId)}
        download
        title={`Download the backend-selected human-readable report (snapshot target ${target})`}
      >
        Download DOCX
      </a>
      <a
        className="rounded-md border border-edge bg-raised px-2 py-1 text-[11px] text-ink-dim transition-colors hover:border-accent hover:text-accent"
        href={qcReportExportUrl("json", reportRunId)}
        download
        title={`Download the backend-selected machine-readable report (snapshot target ${target})`}
      >
        Download JSON
      </a>
      <span className="break-all font-mono text-[9px] text-ink-faint">
        snapshot report target: {target}
      </span>
    </div>
  );
}

function DismissQCModal({
  finding,
  reason,
  busy,
  pending,
  error,
  restoreFocusRef,
  onReasonChange,
  onConfirm,
  onCancel,
}: {
  finding: QcFinding | null;
  reason: string;
  busy: boolean;
  pending: boolean;
  error: string;
  restoreFocusRef: RefObject<HTMLElement>;
  onReasonChange: (value: string) => void;
  onConfirm: () => void | Promise<void>;
  onCancel: () => void;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  useDialogFocus(Boolean(finding), dialogRef, reasonRef, () => {
    if (!pending) onCancel();
  }, restoreFocusRef);

  if (!finding) return null;
  const canConfirm = !busy && !pending && reason.trim().length > 0;
  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-black/50 p-6"
      onClick={() => {
        if (!pending) onCancel();
      }}
    >
      <div
        ref={dialogRef}
        tabIndex={-1}
        className="w-full max-w-lg rounded-2xl border border-edge bg-surface shadow-2xl"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="dismiss-qc-title"
        aria-describedby="dismiss-qc-description"
        aria-busy={pending}
      >
        <div className="px-6 pt-5 pb-4">
          <h2 id="dismiss-qc-title" className="font-[family-name:var(--font-display)] text-lg font-semibold text-ink">
            Dismiss QC finding?
          </h2>
          <p id="dismiss-qc-description" className="mt-2 text-sm leading-relaxed text-ink-dim">
            <span className="font-medium text-ink">{finding.title}</span> will
            remain in the report as dismissed. Record why so another reviewer
            can audit the decision later.
          </p>
          <label className="mt-4 block text-[11px] font-semibold tracking-wide text-ink-faint uppercase">
            Dismissal rationale (required)
            <textarea
              ref={reasonRef}
              rows={4}
              value={reason}
              onChange={(event) => onReasonChange(event.target.value)}
              disabled={pending}
              aria-invalid={Boolean(error)}
              aria-describedby={error ? "dismiss-qc-error" : undefined}
              placeholder="Explain why the finding is accepted as-is, out of scope, or otherwise not being applied."
              className="mt-1.5 w-full resize-y rounded-lg border border-edge bg-bg px-3 py-2 text-sm font-normal tracking-normal text-ink outline-none normal-case focus:border-accent"
            />
          </label>
          {error && (
            <p
              id="dismiss-qc-error"
              role="alert"
              className="mt-2 whitespace-pre-wrap break-words rounded border border-err/40 bg-err/10 px-2.5 py-2 text-[11px] text-err"
            >
              Dismissal was not recorded. Your rationale is preserved so you
              can retry. {error}
            </p>
          )}
        </div>
        <div className="flex items-center justify-end gap-2 border-t border-edge px-6 py-3">
          <button
            className="rounded-lg px-3 py-1.5 text-sm text-ink-dim transition-colors hover:text-ink"
            onClick={onCancel}
            disabled={pending}
          >
            Cancel
          </button>
          <button
            className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:pointer-events-none disabled:opacity-40"
            disabled={!canConfirm}
            onClick={() => void onConfirm()}
          >
            {pending ? "Recording…" : "Record dismissal"}
          </button>
        </div>
      </div>
    </div>
  );
}

/**
 * Pre-flight confirmation for a Final QC pass. A run is expensive (Fable 5)
 * and slow (minutes), so this dialog states plainly what the pass does, why it
 * costs, and why it takes a while, and makes the user opt in. Mirrors the
 * SettingsPanel overlay pattern (backdrop click / ✕ / Escape all cancel).
 */
function ConfirmQCModal({
  isRerun,
  costEstimate,
  busy,
  onConfirm,
  onCancel,
}: {
  isRerun: boolean;
  costEstimate: string;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  const sectionLabel =
    "text-[11px] font-semibold tracking-wide text-ink-faint uppercase";

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 p-6 pt-16"
      onClick={onCancel}
      role="dialog"
      aria-modal="true"
    >
      <div
        className="w-full max-w-lg overflow-hidden rounded-2xl border border-edge bg-surface shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-edge px-5 py-3">
          <h2 className="font-[family-name:var(--font-display)] text-lg font-semibold">
            {isRerun ? "Re-run Final QC?" : "Run Final QC?"}
          </h2>
          <button
            onClick={onCancel}
            className="rounded-lg px-2 py-1 text-ink-dim transition-colors hover:text-ink"
            title="Cancel"
          >
            ✕
          </button>
        </div>

        <div className="max-h-[70vh] space-y-4 overflow-y-auto px-5 py-5 text-[13px] leading-relaxed text-ink-dim">
          <p>
            Final QC is a spare-no-expense review of the whole section before it
            goes out the door. It runs on{" "}
            <strong className="text-ink">Claude Fable 5</strong>, the most
            capable model — not the Sonnet&nbsp;5 model that runs the interview.
          </p>

          <div className="space-y-1.5">
            <p className={sectionLabel}>What it does</p>
            <ul className="list-disc space-y-1 pl-5">
              <li>
                Five independent reviewers read the entire draft in parallel —
                code compliance, coordination &amp; consistency, completeness,
                enforceability language, and provenance hygiene.
              </li>
              <li>
                The code-compliance reviewer searches the web to check standards
                against their current published text.
              </li>
              <li>
                Every finding is then re-checked by an adversarial verification
                panel, so weak or unfounded findings are filtered out before you
                see them.
              </li>
            </ul>
          </div>

          <div className="space-y-1.5">
            <p className={sectionLabel}>Why it&apos;s expensive</p>
            <p>
              Fable&nbsp;5 costs several times more per token than the interview
              model, and a pass is not a single call — it&apos;s the five
              reviewers plus a panel of verifiers for every finding they raise,
              each reasoning at the highest effort. That adds up to dozens of
              model calls, billed to your own Anthropic API key.
            </p>
            <p className="text-ink-faint italic">{costEstimate}</p>
          </div>

          <div className="space-y-1.5">
            <p className={sectionLabel}>Why it takes a while</p>
            <p>
              The reviewers reason deeply (and one searches the web live), then
              every finding goes through verification. A pass usually takes
              several minutes. You can keep working while it runs — progress
              shows lens by lens.
            </p>
          </div>
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-edge px-5 py-3">
          <button
            className="rounded-lg border border-edge bg-raised px-3 py-1.5 text-sm text-ink-dim transition-colors hover:border-ink-faint hover:text-ink"
            onClick={onCancel}
          >
            Cancel
          </button>
          <button
            className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:pointer-events-none disabled:opacity-40"
            onClick={onConfirm}
            disabled={busy}
          >
            {isRerun ? "Re-run Final QC" : "Run Final QC"}
          </button>
        </div>
      </div>
    </div>
  );
}

function FindingCard({
  finding,
  busy,
  decision,
  open,
  onToggle,
  onApply,
  onDismiss,
  onJump,
}: {
  finding: QcFinding;
  busy: boolean;
  decision: SourceOperationCapability;
  open: boolean;
  onToggle: () => void;
  onApply: () => void;
  onDismiss: () => void;
  onJump: (elementId: string) => void;
}) {
  const rationaleId = useId();
  const dimmed = finding.status !== "open";
  const cardBtn =
    "rounded-md border border-edge bg-raised px-2 py-0.5 text-[11px] text-ink-dim transition-colors hover:border-accent hover:text-accent disabled:pointer-events-none disabled:opacity-40";
  const applyLocked = busy || !decision.allowed;
  const applyTitle = !decision.allowed
    ? sourceCapabilityTitle(decision, "Apply this fix")
    : busy
      ? QC_BUSY_MESSAGE
      : "Apply this fix to the document (one undo step)";
  return (
    <div
      className={`rounded-lg border border-edge bg-surface/40 p-2 ${
        dimmed ? "opacity-55" : ""
      }`}
    >
      <div className="flex items-baseline gap-2">
        <span
          className={`shrink-0 rounded border px-1 py-px text-[9px] font-semibold uppercase ${sevChip[finding.severity]}`}
        >
          {finding.severity}
        </span>
        {finding.element_id ? (
          <button
            className="shrink-0 font-medium text-ink tabular-nums hover:text-accent"
            onClick={() => onJump(finding.element_id)}
            title="Jump to this provision"
          >
            {finding.element_id}
          </button>
        ) : (
          <span className="shrink-0 text-[10px] text-ink-faint uppercase">
            section
          </span>
        )}
        <span className="min-w-0 flex-1 truncate text-[12px] text-ink-dim">
          {finding.title}
        </span>
        {finding.status !== "open" && (
          <span className="shrink-0 text-[10px] text-ink-faint uppercase">
            {finding.status}
          </span>
        )}
      </div>

      <p className="mt-1 text-[11px] text-ink-dim">{finding.issue}</p>

      <button
        className="mt-1 text-[10px] text-ink-faint hover:text-ink-dim"
        onClick={onToggle}
        aria-expanded={open}
        aria-controls={rationaleId}
      >
        {open ? "▾ hide rationale" : "▸ rationale"}
      </button>
      {open && (
        <div id={rationaleId} className="mt-1 space-y-1">
          <p className="text-[11px] text-ink-faint">{finding.rationale}</p>
          {finding.accepted_sources.length > 0 && (
            <p className="text-[11px]">
              {finding.accepted_sources.map((url, index) => {
                const safe = safeHttpUrl(url);
                return safe ? (
                  <a
                    key={`${url}-${index}`}
                    href={safe}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="mr-1 text-accent hover:underline"
                    title={url}
                  >
                    [src]
                  </a>
                ) : (
                  <span
                    key={`${url}-${index}`}
                    className="mr-1 break-all text-warn"
                    title="Stored source is not a safe credential-free HTTP(S) URL and is intentionally not clickable"
                  >
                    [unsafe source: {url || "empty value"}]
                  </span>
                );
              })}
            </p>
          )}
          {finding.source_urls.length > 0 &&
            finding.accepted_sources.length === 0 && (
              <p className="text-[11px] text-warn">
                [UNVERIFIED] cited but not retrieved
              </p>
            )}
          {finding.ops_valid && finding.proposed_ops.length > 0 && (
            <div className="rounded border border-edge/70 bg-bg/40 p-1.5">
              <p className="text-[10px] font-medium text-ink-faint uppercase">
                Proposed fix
              </p>
              <ul className="mt-0.5 space-y-0.5">
                {finding.proposed_ops.map((op, i) => (
                  <li key={i} className="text-[11px] text-ink-dim">
                    {opPreview(op)}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {finding.dismiss_reason && (
            <p className="text-[11px] text-ink-faint italic">
              Dismissed: {finding.dismiss_reason}
            </p>
          )}
        </div>
      )}

      {finding.status === "open" && (
        <div className="mt-1.5 flex items-center gap-1.5">
          <span title={applyLocked ? applyTitle : undefined}>
            <button
              className={cardBtn}
              onClick={() => {
                if (applyLocked) return;
                onApply();
              }}
              disabled={applyLocked}
              title={applyLocked ? undefined : applyTitle}
            >
              Apply fix
            </button>
          </span>
          <button className={cardBtn} onClick={onDismiss} disabled={busy}>
            Dismiss
          </button>
        </div>
      )}
    </div>
  );
}
