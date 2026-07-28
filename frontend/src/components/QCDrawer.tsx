/**
 * Final QC on Opus 5: one button, a fleet of Opus 5 reviewers,
 * an accept/dismiss fix queue, and an issue-readiness checklist.
 *
 * Idle → a "Send to Final QC" button + a cost expectation line + the
 * readiness checklist. The button never launches a run directly: because a
 * pass runs on Opus 5 (expensive) and takes minutes, it opens a confirmation
 * dialog that spells out what the pass does, why it costs, and why it's slow —
 * the user opts in explicitly. Running → the five lens rows with live status,
 * then a
 * "Verifying findings…" counter (fed by the SSE stream). Complete → findings
 * are triaged into ready fixes, project decisions, and professional review.
 * Ready fixes default selected, receive a free combined-batch preview, and
 * apply after one confirmation; every finding still exposes its rationale,
 * operation preview, individual Apply / Dismiss actions, and report trail.
 * Refuted and infrastructure-inconclusive candidates remain disclosed. A
 * staleness banner shows when the document has moved on from the version QC
 * reviewed.
 *
 * Reuses Batch 2's status machinery: a run takes minutes, and the drawer shows
 * lens-by-lens progress the whole time instead of leaving dead air.
 */
import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { RefObject } from "react";
import type {
  QcModuleSectionCompatibility,
  QcApplyPreviewBasis,
  QcApplyPreviewResult,
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
  qcOperationEvaluation,
  qcPrimaryReport,
  qcReportExportUrl,
  qcSubstantivelyRefutedCandidates,
  qcSurvivingCandidates,
  safeHttpUrl,
  type QcOperationEvaluation,
  type QcReportFinding,
} from "../lib/qcReport";
import {
  classifyQcRemediation,
  qcDecisionContextByElement,
  type QcRemediationBucket,
} from "../lib/qcRemediation";
import { useDialogFocus } from "../lib/dialogFocus";
import ConfirmDialog from "./ConfirmDialog";
import QCReportModal from "./QCReportModal";
import Tip from "./Tip";

interface Props {
  qc: QcSnapshot | null;
  readiness: ReadinessPayload | null;
  doc: SpecDoc | null;
  profileComplete: boolean;
  busy: boolean;
  sourceExpected: boolean;
  sourceCapabilities: SourceCapabilitiesState | null;
  usage: UsageSummary | null;
  onStart: (acknowledgeScopeMismatch: boolean) => void;
  onStop: () => void;
  onPreview: (findingIds: string[]) => Promise<QcApplyPreviewResult>;
  onApply: (
    findingIds: string[],
    previewBasis?: QcApplyPreviewBasis,
  ) => Promise<void>;
  onDismiss: (findingId: string, reason: string) => Promise<void>;
  onAskModel: (text: string) => void;
  onJump: (elementId: string) => void;
  /** Guided-tour "ensure open" (Batch 6): a bump expands the drawer. */
  openNonce?: number;
}

const QC_BUSY_MESSAGE = "Wait for the current action to finish.";

function qcOperationGate(
  finding: QcReportFinding,
  evaluation: QcOperationEvaluation,
): SourceOperationCapability {
  if (evaluation.actionable) return { allowed: true };
  if (evaluation.semanticStatus === "legacy_unrecorded") {
    return {
      allowed: false,
      blocker: "qc_schema_legacy",
      message:
        "Historical Final QC reports do not contain semantic fix verification and cannot supply an actionable queue. Re-run Final QC.",
    };
  }
  if (evaluation.semanticStatus === "rejected") {
    return {
      allowed: false,
      blocker: "qc_ops_semantic_rejected",
      message:
        evaluation.semanticReason ||
        "The verifier panel did not approve this proposed fix.",
    };
  }
  if (evaluation.semanticStatus === "not_evaluated") {
    return {
      allowed: false,
      blocker: "qc_ops_semantic_not_evaluated",
      message: evaluation.semanticReason || "This proposed fix was not semantically evaluated.",
    };
  }
  if (evaluation.semanticStatus === "not_proposed" || finding.proposed_ops.length === 0) {
    return {
      allowed: false,
      blocker: "qc_ops_missing",
      message: evaluation.semanticReason || "Final QC did not propose a fix for this finding.",
    };
  }
  return {
    allowed: false,
    blocker: "qc_ops_invalid",
    message:
      finding.ops_invalid_reason ||
      "The semantically approved fix did not pass deterministic operation validation.",
  };
}

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

function promptExcerpt(value: string | undefined, limit = 800): string {
  const normalized = (value ?? "").replace(/\s+/g, " ").trim();
  if (normalized.length <= limit) return normalized;
  return `${normalized.slice(0, limit - 1).trimEnd()}…`;
}

export default function QCDrawer({
  qc,
  readiness,
  doc,
  profileComplete,
  busy,
  sourceExpected,
  sourceCapabilities,
  usage,
  onStart,
  onStop,
  onPreview,
  onApply,
  onDismiss,
  onAskModel,
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
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [stopConfirmOpen, setStopConfirmOpen] = useState(false);
  const [reportOpen, setReportOpen] = useState(false);
  const [selectedReadyIds, setSelectedReadyIds] = useState<Set<string>>(
    () => new Set(),
  );
  const [batchPreview, setBatchPreview] =
    useState<QcApplyPreviewResult | null>(null);
  const [batchPreviewPending, setBatchPreviewPending] = useState(false);
  const [batchPreviewError, setBatchPreviewError] = useState("");
  const [applyPending, setApplyPending] = useState(false);
  const [dismissTarget, setDismissTarget] = useState<QcReportFinding | null>(null);
  const [dismissReason, setDismissReason] = useState("");
  const [dismissPending, setDismissPending] = useState(false);
  const [dismissError, setDismissError] = useState("");
  const drawerToggleRef = useRef<HTMLButtonElement>(null);
  const applyPendingRef = useRef(false);

  const status = qc?.status ?? "idle";
  const running = status === "running";
  const settling = qc?.settling ?? false;
  const interactionBusy = busy || settling || applyPending;
  const result = qc?.result;
  const moduleSectionCompatibility = qc?.module_section_compatibility;
  const primaryReport = qcPrimaryReport(qc);
  const findings = result ? qcSurvivingCandidates(result) : [];
  const refuted = result ? qcSubstantivelyRefutedCandidates(result) : [];
  const inconclusive = result ? qcInconclusiveCandidates(result) : [];
  const openFindings = findings.filter((f) => f.status === "open");
  const openCriticalCount = openFindings.filter(
    (f) => f.severity === "critical",
  ).length;
  const stale =
    result != null &&
    ((qc?.stale ?? false) ||
      (doc != null && result.version_index !== doc.version.index));
  const applyStateStale = result != null && (doc == null || stale);
  const auditComplete = result?.execution_status === "complete";
  const remediationAvailable = auditComplete && !applyStateStale;
  const findingOperationEvaluations = new Map(
    findings.map((finding) => [
      finding.finding_id,
      qcOperationEvaluation(finding, result?.schema_version),
    ]),
  );
  const findingDecisions = new Map(
    findings.map((finding) => {
      const evaluation = findingOperationEvaluations.get(finding.finding_id)!;
      const operationGate: SourceOperationCapability = !auditComplete
        ? {
            allowed: false,
            blocker: "qc_audit_incomplete",
            message:
              "This QC audit did not complete, so its proposed operations cannot be applied.",
          }
        : qcOperationGate(finding, evaluation);
      return [
        finding.finding_id,
        operationGate.allowed
          ? qcBatchDecision({
              finding,
              sourceCapabilities,
              sourceExpected,
              stale: applyStateStale,
            })
          : operationGate,
      ];
    }),
  );
  const decisionContexts = useMemo(
    () => qcDecisionContextByElement(doc),
    [doc],
  );
  const remediationEntries = openFindings.map((finding) => ({
    finding,
    classification: classifyQcRemediation(
      finding,
      findingDecisions.get(finding.finding_id)?.allowed === true,
      decisionContexts.get(finding.element_id),
    ),
  }));
  const readyEntries = remediationAvailable
    ? remediationEntries.filter(
        (entry) => entry.classification.bucket === "ready",
      )
    : [];
  const decisionEntries = remediationAvailable
    ? remediationEntries.filter(
        (entry) => entry.classification.bucket === "needs_decision",
      )
    : [];
  const manualEntries = remediationAvailable
    ? remediationEntries.filter(
        (entry) => entry.classification.bucket === "manual_review",
      )
    : [];
  const resolvedFindings = findings.filter(
    (finding) => finding.status !== "open",
  );
  const readyIdKey = readyEntries
    .map((entry) => entry.finding.finding_id)
    .join("\u001f");
  useEffect(() => {
    setSelectedReadyIds(
      new Set(readyEntries.map((entry) => entry.finding.finding_id)),
    );
    setBatchPreview(null);
    setBatchPreviewError("");
    // The key intentionally represents the complete eligible set. Individual
    // checkbox changes do not reset the user's selection.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [result?.run_id, readyIdKey]);
  const selectedReadyEntries = readyEntries.filter((entry) =>
    selectedReadyIds.has(entry.finding.finding_id),
  );
  const selectedOperationCount = selectedReadyEntries.reduce(
    (total, entry) => total + entry.finding.proposed_ops.length,
    0,
  );

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

  const previewSelectedFixes = async () => {
    if (
      interactionBusy ||
      applyPending ||
      batchPreviewPending ||
      selectedReadyEntries.length === 0
    ) {
      return;
    }
    const findingIds = selectedReadyEntries.map(
      (entry) => entry.finding.finding_id,
    );
    setBatchPreviewPending(true);
    setBatchPreviewError("");
    try {
      const preview = await onPreview(findingIds);
      setBatchPreview(preview);
    } catch (error) {
      setBatchPreviewError(
        error instanceof Error ? error.message : String(error),
      );
    } finally {
      setBatchPreviewPending(false);
    }
  };

  const applyFindingIds = async (
    findingIds: string[],
    previewBasis?: QcApplyPreviewBasis,
  ) => {
    if (interactionBusy || applyPendingRef.current || findingIds.length === 0) {
      return;
    }
    applyPendingRef.current = true;
    setApplyPending(true);
    try {
      await onApply([...findingIds], previewBasis);
    } finally {
      applyPendingRef.current = false;
      setApplyPending(false);
    }
  };

  const applyPreviewedFixes = () => {
    const preview = batchPreview;
    const findingIds = preview?.applyable_finding_ids ?? [];
    if (
      interactionBusy ||
      applyPending ||
      !preview ||
      findingIds.length === 0
    ) return;
    setBatchPreview(null);
    void applyFindingIds(findingIds, preview.basis);
  };

  const observedCost = usage?.estimated_cost_usd.by_category.qc;
  const costLine =
    observedCost && observedCost > 0
      ? `Runs on Claude Opus 5 — a stronger reviewer than the drafter. This session's QC: ≈ $${observedCost.toFixed(2)}.`
      : "Runs on Claude Opus 5 — a stronger reviewer than the drafter.";

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
  const startDisabled = !profileComplete || running || interactionBusy;
  // Carried by the <Tip> wrapper, not the button: a disabled button is inert
  // and the shared style adds `disabled:pointer-events-none`, so a native
  // title on it never fires — exactly when the explanation is most needed.
  const startTip = !profileComplete
    ? "Complete the project profile first — city, state, country, and client — before sending the section to Final QC."
    : settling
      ? "Stop requested; the paid partial audit record is still being preserved."
      : running
        ? "Final QC is already running."
        : interactionBusy
          ? QC_BUSY_MESSAGE
          : "Review what a pass costs and does, then confirm — runs the full lens fan-out + adversarial verification on Opus 5 (uses your API key)";

  // The start button opens the confirmation dialog; the run only fires once
  // the user confirms in it (Opus 5 is expensive and a pass takes minutes).
  const confirmStart = (acknowledgeScopeMismatch: boolean) => {
    setConfirmOpen(false);
    onStart(acknowledgeScopeMismatch);
  };

  const remediationSections: {
    id: QcRemediationBucket;
    title: string;
    description: string;
    entries: typeof remediationEntries;
    tone: string;
  }[] = [
    {
      id: "ready",
      title: "Ready to apply",
      description:
        "Reviewer-approved fixes that pass deterministic validation against the current specification.",
      entries: readyEntries,
      tone: "border-ok/40 bg-ok/5 text-ok",
    },
    {
      id: "needs_decision",
      title: "Needs your decision",
      description:
        "Unresolved project facts, [TBD] values, or assumptions. Supply the fact; the program can do the drafting.",
      entries: decisionEntries,
      tone: "border-warn/40 bg-warn/5 text-warn",
    },
    {
      id: "manual_review",
      title: "Manual professional review",
      description:
        "Findings without a safe executable fix. A qualified reviewer must decide the technical response.",
      entries: manualEntries,
      tone: "border-edge bg-bg/30 text-ink-dim",
    },
  ];

  return (
    <>
    <div
      className="border-t border-edge bg-bg/70 px-5 py-2"
      data-tour="qc-drawer"
      data-capability="qc.run"
    >
      <div className="flex items-baseline gap-2">
        <button
          data-capability="qc.preflight"
          ref={drawerToggleRef}
          className="flex min-w-0 flex-1 items-baseline gap-2 text-left text-[11px] text-ink-faint transition-colors hover:text-ink-dim"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          aria-controls="final-qc-drawer-body"
          title="Final QC — a fleet of Opus 5 reviewers before the section goes out the door"
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
                    (openCriticalCount ? ` · ${openCriticalCount} critical` : "") +
                    (inconclusive.length ? ` · ${inconclusive.length} inconclusive` : "")
                  : primaryReport
                    ? `${primaryReport.execution_status || "preserved"} report available · no action queue`
                  : readiness?.ready
                    ? "ready to issue"
                    : "not yet reviewed"}
          </span>
          <span className="ml-auto shrink-0">{expanded ? "▾" : "▸"}</span>
        </button>
        <Tip tip={startTip} className="shrink-0">
          <button
            className={`rounded-md border px-2 py-0.5 text-[11px] transition-colors disabled:pointer-events-none disabled:opacity-40 ${
              primaryReport
                ? "border-edge bg-raised text-ink-dim hover:border-accent hover:text-accent"
                : "border-accent/70 bg-accent/15 text-accent hover:bg-accent/25"
            }`}
            onClick={() => setConfirmOpen(true)}
            disabled={startDisabled}
          >
            {startLabel}
          </button>
        </Tip>
        {running && (
          <button
            className="shrink-0 rounded-md border border-edge bg-raised px-2 py-0.5 text-[11px] text-ink-dim transition-colors hover:border-err hover:text-err"
            onClick={() => setStopConfirmOpen(true)}
            data-capability="qc.stop"
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
            finding queue, and the spend already incurred is not
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
              data-capability="readiness.checklist"
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
            <div data-capability="qc.actions">
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

              {remediationAvailable && openFindings.length > 0 && (
                <section
                  className="rounded-lg border border-accent/35 bg-accent/5 p-2.5"
                  aria-labelledby="qc-remediation-title"
                  data-capability="qc.remediation"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <h3
                        id="qc-remediation-title"
                        className="text-[12px] font-semibold text-ink"
                      >
                        Guided remediation
                      </h3>
                      <p className="mt-0.5 text-[11px] leading-relaxed text-ink-dim">
                        You approve the changes; the program handles the exact
                        drafting, validation, application, and audit trail.
                      </p>
                    </div>
                    <span className="shrink-0 rounded-full border border-edge bg-bg/50 px-2 py-0.5 text-[9px] font-medium text-ink-faint">
                      {openFindings.length} open
                    </span>
                  </div>
                  <div className="mt-2 grid grid-cols-3 gap-1.5">
                    <RemediationCount
                      count={readyEntries.length}
                      label="Ready to apply"
                      tone="text-ok"
                    />
                    <RemediationCount
                      count={decisionEntries.length}
                      label="Needs your decision"
                      tone="text-warn"
                    />
                    <RemediationCount
                      count={manualEntries.length}
                      label="Professional review"
                      tone="text-ink-dim"
                    />
                  </div>
                  {readyEntries.length > 0 && (
                    <div className="mt-2 rounded border border-ok/30 bg-ok/5 p-2">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <p className="text-[11px] text-ink-dim">
                          <span className="font-medium text-ink">
                            {selectedReadyEntries.length} of {readyEntries.length}
                          </span>{" "}
                          ready fixes selected · {selectedOperationCount} proposed
                          operation{selectedOperationCount === 1 ? "" : "s"}
                        </p>
                        <button
                          className="text-[10px] text-accent hover:underline disabled:pointer-events-none disabled:opacity-40"
                          disabled={interactionBusy || batchPreviewPending}
                          onClick={() => {
                            setSelectedReadyIds(
                              selectedReadyEntries.length === readyEntries.length
                                ? new Set()
                                : new Set(
                                    readyEntries.map(
                                      (entry) => entry.finding.finding_id,
                                    ),
                                  ),
                            );
                            setBatchPreviewError("");
                          }}
                        >
                          {selectedReadyEntries.length === readyEntries.length
                            ? "Clear selection"
                            : "Select all ready"}
                        </button>
                      </div>
                      <button
                        className="mt-2 w-full rounded-md border border-ok/50 bg-ok/10 px-2 py-1.5 text-[11px] font-medium text-ok transition-colors hover:bg-ok/15 disabled:pointer-events-none disabled:opacity-40"
                        disabled={
                          interactionBusy ||
                          batchPreviewPending ||
                          selectedReadyEntries.length === 0
                        }
                        onClick={() => void previewSelectedFixes()}
                      >
                        {batchPreviewPending
                          ? "Checking the combined batch…"
                          : `Review and apply ${selectedReadyEntries.length} selected verified ${selectedReadyEntries.length === 1 ? "fix" : "fixes"}`}
                      </button>
                      <p className="mt-1 text-[10px] text-ink-faint">
                        Preview is free and makes no change. The server checks
                        duplicates and conflicts before you confirm one
                        undoable document version.
                      </p>
                    </div>
                  )}
                  {batchPreviewError && (
                    <p
                      role="alert"
                      className="mt-2 rounded border border-err/40 bg-err/10 px-2 py-1.5 text-[11px] text-err"
                    >
                      Could not preview this batch. Nothing was changed. {batchPreviewError}
                    </p>
                  )}
                </section>
              )}

              {/* The findings queue itself. It wraps the empty-state lines as
                  well as the remediation buckets so the capability always resolves
                  to something visible — a completed run with zero surviving
                  findings is a real, and reportable, outcome. */}
              <div data-capability="qc.findings">
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

              {!remediationAvailable && openFindings.length > 0 && (
                <section
                  className="mt-2 rounded-lg border border-warn/40 bg-warn/5 p-2"
                  aria-labelledby="qc-rerun-required"
                >
                  <h3
                    id="qc-rerun-required"
                    className="text-[11px] font-semibold text-warn"
                  >
                    Rerun required before remediation ({openFindings.length})
                  </h3>
                  <p className="mt-0.5 text-[10px] leading-relaxed text-ink-dim">
                    {auditComplete
                      ? "The specification or another review input changed after this run. These historical findings remain available for inspection, but they are not reclassified as manual-review work and cannot be applied until Final QC is rerun."
                      : "This review did not finish, so it cannot establish a safe remediation plan. These partial findings remain available for inspection; complete Final QC before classifying or applying them."}
                  </p>
                  <div className="mt-1.5 space-y-1.5">
                    {openFindings.map((f) => {
                      const decision = findingDecisions.get(f.finding_id)!;
                      return (
                        <FindingCard
                          key={f.finding_id}
                          finding={f}
                          busy={interactionBusy || !auditComplete}
                          decision={decision}
                          operationEvaluation={findingOperationEvaluations.get(f.finding_id)!}
                          remediationNote={
                            auditComplete
                              ? "Rerun Final QC against the current specification before deciding how to resolve this finding."
                              : "Complete Final QC before treating this finding as actionable."
                          }
                          open={!!openRationale[f.finding_id]}
                          onToggle={() =>
                            setOpenRationale((current) => ({
                              ...current,
                              [f.finding_id]: !current[f.finding_id],
                            }))
                          }
                          onApply={() => undefined}
                          onDismiss={() => {
                            setDismissTarget(f);
                            setDismissReason("");
                            setDismissError("");
                          }}
                          onJump={onJump}
                        />
                      );
                    })}
                  </div>
                </section>
              )}

              {remediationSections.map((section) => {
                if (section.entries.length === 0) return null;
                return (
                  <section
                    key={section.id}
                    className={`mt-2 rounded-lg border p-2 ${section.tone}`}
                    aria-labelledby={`qc-bucket-${section.id}`}
                  >
                    <div className="mb-1.5">
                      <h3
                        id={`qc-bucket-${section.id}`}
                        className="text-[11px] font-semibold"
                      >
                        {section.title} ({section.entries.length})
                      </h3>
                      <p className="mt-0.5 text-[10px] leading-relaxed opacity-85">
                        {section.description}
                      </p>
                    </div>
                    <div className="space-y-1.5">
                      {section.entries.map(({ finding: f, classification }) => {
                        const decision = findingDecisions.get(f.finding_id)!;
                        const isDecision = section.id === "needs_decision";
                        const remediationNote =
                          section.id === "ready"
                            ? "Approved by the verifier panel and validated against the current specification."
                            : isDecision
                              ? classification.decisionSignals.join("; ")
                              : decision.message;
                        return (
                          <FindingCard
                            key={f.finding_id}
                            finding={f}
                            busy={interactionBusy || batchPreviewPending}
                            decision={decision}
                            operationEvaluation={findingOperationEvaluations.get(f.finding_id)!}
                            remediationBucket={section.id}
                            remediationNote={remediationNote}
                            batchSelection={
                              section.id === "ready"
                                ? {
                                    selected: selectedReadyIds.has(f.finding_id),
                                    disabled: interactionBusy || batchPreviewPending,
                                    onChange: (selected) => {
                                      setSelectedReadyIds((current) => {
                                        const next = new Set(current);
                                        if (selected) next.add(f.finding_id);
                                        else next.delete(f.finding_id);
                                        return next;
                                      });
                                      setBatchPreviewError("");
                                    },
                                  }
                                : undefined
                            }
                            open={!!openRationale[f.finding_id]}
                            onToggle={() =>
                              setOpenRationale((m) => ({
                                ...m,
                                [f.finding_id]: !m[f.finding_id],
                              }))
                            }
                            onApply={() => {
                              if (interactionBusy || !decision.allowed) return;
                              void applyFindingIds([f.finding_id]);
                            }}
                            onDismiss={() => {
                              setDismissTarget(f);
                              setDismissReason("");
                              setDismissError("");
                            }}
                            onResolveInChat={
                              isDecision
                                ? () => {
                                    const target = f.element_id || "the section";
                                    const signal =
                                      classification.decisionSignals.join("; ");
                                    const currentProvision = promptExcerpt(
                                      decisionContexts.get(f.element_id)?.text,
                                    );
                                    const proposedChanges = f.proposed_ops
                                      .map((operation) => opPreview(operation))
                                      .join("; ");
                                    const evidence = [
                                      `Finding: ${f.title} (${f.finding_id})`,
                                      `Affected provision: ${target}`,
                                      `Issue identified by Final QC: ${promptExcerpt(f.issue)}`,
                                      `Review rationale: ${promptExcerpt(f.rationale)}`,
                                      currentProvision
                                        ? `Current provision text: ${currentProvision}`
                                        : "",
                                      proposedChanges
                                        ? `Proposed-operation context: ${promptExcerpt(proposedChanges)}`
                                        : "Proposed-operation context: Final QC did not supply an executable fix.",
                                      `Missing-decision signal: ${signal}`,
                                    ]
                                      .filter(Boolean)
                                      .join("\n");
                                    onAskModel(
                                      `Help me resolve this Final QC finding using the retained review evidence below.\n\n${evidence}\n\nAsk only for the missing project fact or confirmation; do not invent or silently default a value. Once I answer, update the specification to resolve the finding and briefly explain what changed and why.`,
                                    );
                                  }
                                : undefined
                            }
                            onJump={onJump}
                          />
                        );
                      })}
                    </div>
                  </section>
                );
              })}

              {resolvedFindings.length > 0 && (
                <details className="mt-2 rounded border border-edge bg-bg/20 p-2">
                  <summary className="cursor-pointer text-[11px] text-ink-faint hover:text-ink-dim">
                    Applied or dismissed findings ({resolvedFindings.length})
                  </summary>
                  <div className="mt-1.5 space-y-1.5">
                    {resolvedFindings.map((f) => (
                      <FindingCard
                        key={f.finding_id}
                        finding={f}
                        busy={interactionBusy}
                        decision={findingDecisions.get(f.finding_id)!}
                        operationEvaluation={findingOperationEvaluations.get(f.finding_id)!}
                        open={!!openRationale[f.finding_id]}
                        onToggle={() =>
                          setOpenRationale((m) => ({
                            ...m,
                            [f.finding_id]: !m[f.finding_id],
                          }))
                        }
                        onApply={() => undefined}
                        onDismiss={() => undefined}
                        onJump={onJump}
                      />
                    ))}
                  </div>
                </details>
              )}
              </div>

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
            </div>
          )}
        </div>
      )}
    </div>

    <ConfirmDialog
      open={batchPreview != null}
      title="Apply verified Final QC fixes?"
      body={
        batchPreview ? <BatchPreviewSummary preview={batchPreview} /> : null
      }
      confirmLabel={
        batchPreview?.applyable_finding_ids.length
          ? `Apply ${batchPreview.applyable_finding_ids.length} verified ${batchPreview.applyable_finding_ids.length === 1 ? "fix" : "fixes"}`
          : "No compatible fixes"
      }
      cancelLabel="Close"
      confirmDisabled={
        interactionBusy ||
        !batchPreview ||
        batchPreview.applyable_finding_ids.length === 0
      }
      onConfirm={applyPreviewedFixes}
      onCancel={() => setBatchPreview(null)}
    />

    {confirmOpen && (
      <ConfirmQCModal
        isRerun={!!primaryReport}
        costEstimate={costEstimate}
        busy={interactionBusy}
        moduleSectionCompatibility={moduleSectionCompatibility}
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

function RemediationCount({
  count,
  label,
  tone,
}: {
  count: number;
  label: string;
  tone: string;
}) {
  return (
    <div className="rounded border border-edge bg-bg/45 px-2 py-1.5 text-center">
      <p className={`text-base font-semibold leading-none ${tone}`}>{count}</p>
      <p className="mt-1 text-[9px] leading-tight text-ink-faint">{label}</p>
    </div>
  );
}

function BatchPreviewSummary({ preview }: { preview: QcApplyPreviewResult }) {
  const selected = preview.basis.selected_finding_ids.length;
  const applyable = preview.applyable_finding_ids.length;
  const excluded = preview.decisions.filter((decision) => !decision.applyable);
  const visibleApplyable = preview.decisions
    .filter((decision) => decision.applyable)
    .slice(0, 6);
  return (
    <div className="space-y-3">
      <p>
        The server checked all {selected} selected {selected === 1 ? "finding" : "findings"}.{" "}
        <strong className="text-ink">
          {applyable} {applyable === 1 ? "fix is" : "fixes are"} compatible
        </strong>{" "}
        and ready for one undoable document version.
      </p>
      <div className="grid grid-cols-3 gap-1.5 text-center text-[11px]">
        <div className="rounded border border-edge bg-bg/40 p-1.5">
          <strong className="block text-ink">
            {preview.operation_counts.proposed}
          </strong>
          proposed operations
        </div>
        <div className="rounded border border-edge bg-bg/40 p-1.5">
          <strong className="block text-ink">
            {preview.operation_counts.applyable}
          </strong>
          operations to apply
        </div>
        <div className="rounded border border-edge bg-bg/40 p-1.5">
          <strong className="block text-ink">
            {preview.operation_counts.duplicate}
          </strong>
          duplicates removed
        </div>
      </div>
      {visibleApplyable.length > 0 && (
        <div>
          <p className="text-[11px] font-semibold tracking-wide text-ink-faint uppercase">
            Will apply
          </p>
          <ul className="mt-1 max-h-32 space-y-1 overflow-y-auto text-[11px]">
            {visibleApplyable.map((decision) => (
              <li key={decision.finding_id} className="text-ink-dim">
                <span className="font-medium text-ok">
                  [{decision.severity}]
                </span>{" "}
                {decision.title || decision.finding_id}
              </li>
            ))}
          </ul>
          {applyable > visibleApplyable.length && (
            <p className="mt-1 text-[10px] text-ink-faint">
              …and {applyable - visibleApplyable.length} more selected fixes.
            </p>
          )}
        </div>
      )}
      {preview.applyable_operations.length > 0 && (
        <div>
          <p className="text-[11px] font-semibold tracking-wide text-ink-faint uppercase">
            Exact edits in the safe batch
          </p>
          <ul className="mt-1 max-h-28 space-y-1 overflow-y-auto rounded border border-edge bg-bg/35 p-1.5 text-[11px] text-ink-dim">
            {preview.applyable_operations.slice(0, 6).map((operation, index) => (
              <li key={`${index}-${JSON.stringify(operation)}`}>
                {opPreview(operation)}
              </li>
            ))}
          </ul>
          {preview.applyable_operations.length > 6 && (
            <p className="mt-1 text-[10px] text-ink-faint">
              …and {preview.applyable_operations.length - 6} more exact edits.
            </p>
          )}
        </div>
      )}
      {excluded.length > 0 && (
        <div className="rounded border border-warn/35 bg-warn/5 p-2">
          <p className="text-[11px] font-medium text-warn">
            {excluded.length} selected {excluded.length === 1 ? "finding is" : "findings are"} excluded from this application
          </p>
          <ul className="mt-1 max-h-28 space-y-1 overflow-y-auto text-[11px] text-ink-dim">
            {excluded.slice(0, 6).map((decision) => (
              <li key={decision.finding_id}>
                {decision.title || decision.finding_id}: {decision.reason}
              </li>
            ))}
          </ul>
          {excluded.length > 6 && (
            <p className="mt-1 text-[10px] text-ink-faint">
              …and {excluded.length - 6} more exclusions.
            </p>
          )}
        </div>
      )}
      {preview.conflicts.length > 0 && (
        <p className="rounded border border-err/35 bg-err/5 px-2 py-1.5 text-[11px] text-err">
          {preview.conflicts.length} operation {preview.conflicts.length === 1 ? "conflict was" : "conflicts were"} found. Every implicated finding is excluded; the program does not choose a winner for you.
        </p>
      )}
      <p className="text-[11px] text-ink-faint">
        The server will require this exact preview to remain current and will
        revalidate it at Apply time. The full report keeps the reason for every
        change. This action does not start another paid Final QC run; reruns
        always require a separate confirmation.
      </p>
    </div>
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
    <div
      className="flex flex-wrap items-center gap-1.5"
      data-capability="qc.report"
    >
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
  finding: QcReportFinding | null;
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
 * Pre-flight confirmation for a Final QC pass. A run is expensive (Opus 5,
 * dozens of calls) and slow (minutes), so this dialog states plainly what the
 * pass does, why it
 * costs, and why it takes a while, and makes the user opt in. Mirrors the
 * SettingsPanel overlay pattern (backdrop click / ✕ / Escape all cancel).
 */
function ConfirmQCModal({
  isRerun,
  costEstimate,
  busy,
  moduleSectionCompatibility,
  onConfirm,
  onCancel,
}: {
  isRerun: boolean;
  costEstimate: string;
  busy: boolean;
  moduleSectionCompatibility?: QcModuleSectionCompatibility;
  onConfirm: (acknowledgeScopeMismatch: boolean) => void;
  onCancel: () => void;
}) {
  const acknowledgementId = useId();
  const [scopeMismatchAcknowledged, setScopeMismatchAcknowledged] =
    useState(false);
  const compatibilityKey = JSON.stringify([
    moduleSectionCompatibility?.status ?? "",
    moduleSectionCompatibility?.section_number ?? "",
    moduleSectionCompatibility?.section_title ?? "",
    moduleSectionCompatibility?.module_id ?? "",
    moduleSectionCompatibility?.module_display_name ?? "",
    moduleSectionCompatibility?.message ?? "",
    ...(moduleSectionCompatibility?.allowed_sections ?? []).flatMap((section) => [
      section.number,
      section.title,
    ]),
  ]);
  const scopeMismatch = moduleSectionCompatibility?.status === "mismatch";

  // The acknowledgement is deliberately scoped to one visible preflight.
  // Closing the modal unmounts it; any changed comparison resets it in place.
  useEffect(() => {
    setScopeMismatchAcknowledged(false);
  }, [compatibilityKey]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  const sectionLabel =
    "text-[11px] font-semibold tracking-wide text-ink-faint uppercase";
  const canConfirm =
    !busy && (!scopeMismatch || scopeMismatchAcknowledged);

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
          {scopeMismatch && moduleSectionCompatibility && (
            <div
              className="rounded-xl border border-warn/60 bg-warn/10 px-3.5 py-3 text-warn"
              role="alert"
            >
              <p className="text-[11px] font-semibold tracking-wide uppercase">
                Section is outside the selected module catalog
              </p>
              <p className="mt-1.5 text-[12px] leading-relaxed">
                {moduleSectionCompatibility.message ||
                  `Section ${moduleSectionCompatibility.section_number || "(number not recorded)"} is not listed by ${moduleSectionCompatibility.module_display_name || moduleSectionCompatibility.module_id || "the selected module"}.`}
              </p>
              <dl className="mt-2 space-y-1 text-[11px]">
                <div>
                  <dt className="inline font-semibold">Current specification: </dt>
                  <dd className="inline">
                    {moduleSectionCompatibility.section_number || "Number not recorded"}
                    {moduleSectionCompatibility.section_title
                      ? ` — ${moduleSectionCompatibility.section_title}`
                      : ""}
                  </dd>
                </div>
                <div>
                  <dt className="inline font-semibold">Selected module: </dt>
                  <dd className="inline">
                    {moduleSectionCompatibility.module_display_name ||
                      moduleSectionCompatibility.module_id ||
                      "Not recorded"}
                  </dd>
                </div>
                <div>
                  <dt className="inline font-semibold">Catalog sections: </dt>
                  <dd className="inline">
                    {moduleSectionCompatibility.allowed_sections.length > 0
                      ? moduleSectionCompatibility.allowed_sections
                          .map(
                            (section) =>
                              `${section.number}${section.title ? ` — ${section.title}` : ""}`,
                          )
                          .join("; ")
                      : "None recorded"}
                  </dd>
                </div>
              </dl>
              <label
                htmlFor={acknowledgementId}
                className="mt-3 flex cursor-pointer items-start gap-2 rounded-lg border border-warn/50 bg-bg/30 px-2.5 py-2 text-[12px] font-medium text-ink"
              >
                <input
                  id={acknowledgementId}
                  type="checkbox"
                  checked={scopeMismatchAcknowledged}
                  onChange={(event) =>
                    setScopeMismatchAcknowledged(event.target.checked)
                  }
                  className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--color-accent)]"
                />
                <span>
                  I understand this specification is outside the selected
                  module&apos;s catalog and want Final QC to review it as-is.
                </span>
              </label>
            </div>
          )}

          <p>
            Final QC is a spare-no-expense review of the whole section before it
            goes out the door. It runs on{" "}
            <strong className="text-ink">Claude Opus 5</strong>, a stronger
            reasoning model than the Sonnet&nbsp;5 model that drafts — the
            point of the pass is to catch what the drafter missed.
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
              Opus&nbsp;5 costs more per token than the interview model, and a
              pass is not a single call — it&apos;s the five reviewers plus a
              panel of verifiers for every finding they raise. That adds up to
              dozens of model calls, billed to your own Anthropic API key. Your
              document is sent once per stage rather than once per call, which
              keeps most of that from being charged at full rate.
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
            onClick={() => onConfirm(scopeMismatch && scopeMismatchAcknowledged)}
            disabled={!canConfirm}
            title={
              scopeMismatch && !scopeMismatchAcknowledged
                ? "Acknowledge the section/module mismatch before running Final QC."
                : undefined
            }
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
  operationEvaluation,
  remediationBucket,
  remediationNote,
  batchSelection,
  open,
  onToggle,
  onApply,
  onDismiss,
  onResolveInChat,
  onJump,
}: {
  finding: QcReportFinding;
  busy: boolean;
  decision: SourceOperationCapability;
  operationEvaluation: QcOperationEvaluation;
  remediationBucket?: QcRemediationBucket;
  remediationNote?: string;
  batchSelection?: {
    selected: boolean;
    disabled: boolean;
    onChange: (selected: boolean) => void;
  };
  open: boolean;
  onToggle: () => void;
  onApply: () => void;
  onDismiss: () => void;
  onResolveInChat?: () => void;
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
  const semanticSummary =
    operationEvaluation.semanticStatus === "approved"
      ? "Verifier panel approved the proposed fix."
      : operationEvaluation.semanticStatus === "rejected"
        ? "Verifier panel rejected the proposed fix; this finding is advisory only."
        : operationEvaluation.semanticStatus === "not_proposed"
          ? "No fix was proposed for this finding."
          : operationEvaluation.semanticStatus === "not_evaluated"
            ? "The proposed fix was not semantically evaluated."
            : "Semantic fix verification was not recorded by this historical report.";
  const semanticTone =
    operationEvaluation.semanticStatus === "approved"
      ? "border-ok/35 bg-ok/5 text-ok"
      : operationEvaluation.semanticStatus === "rejected"
        ? "border-err/40 bg-err/10 text-err"
        : "border-warn/35 bg-warn/5 text-warn";
  const fixStateLabel =
    operationEvaluation.semanticStatus === "approved"
      ? operationEvaluation.mechanicalStatus === "valid"
        ? "fix ready"
        : operationEvaluation.mechanicalStatus === "invalid"
          ? "fix mechanically invalid"
          : "fix not mechanically evaluated"
      : operationEvaluation.semanticStatus === "rejected"
        ? "fix rejected"
        : operationEvaluation.semanticStatus === "not_proposed"
          ? "no automatic fix"
          : operationEvaluation.semanticStatus === "not_evaluated"
            ? "fix not evaluated"
          : "legacy fix record";
  const remediationTone =
    remediationBucket === "ready"
      ? "border-ok/30 bg-ok/5 text-ok"
      : remediationBucket === "needs_decision"
        ? "border-warn/35 bg-warn/5 text-warn"
        : "border-edge bg-bg/30 text-ink-dim";
  return (
    <div
      className={`rounded-lg border border-edge bg-surface/40 p-2 ${
        dimmed ? "opacity-55" : ""
      }`}
    >
      <div className="flex items-baseline gap-2">
        {batchSelection && (
          <input
            type="checkbox"
            className="h-3.5 w-3.5 shrink-0 accent-[var(--color-ok)]"
            checked={batchSelection.selected}
            disabled={batchSelection.disabled}
            onChange={(event) => batchSelection.onChange(event.target.checked)}
            aria-label={`Include ${finding.title} in the verified-fix batch`}
          />
        )}
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
        <span
          className={`shrink-0 rounded border px-1 py-px text-[9px] font-medium ${semanticTone}`}
        >
          {fixStateLabel}
        </span>
      </div>

      {remediationNote && (
        <p className={`mt-1 rounded border px-1.5 py-1 text-[10px] ${remediationTone}`}>
          {remediationBucket === "needs_decision"
            ? `Needs your decision because ${remediationNote}.`
            : remediationBucket === "manual_review"
              ? `Not automated: ${remediationNote}`
              : remediationNote}
        </p>
      )}

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
          {finding.proposed_ops.length > 0 && (
            <div className="rounded border border-edge/70 bg-bg/40 p-1.5">
              <p className="text-[10px] font-medium text-ink-faint uppercase">
                Proposed fix payload
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
          <div className={`rounded border px-2 py-1.5 text-[11px] ${semanticTone}`}>
            <p className="font-medium">{semanticSummary}</p>
            {operationEvaluation.semanticReason && (
              <p className="mt-0.5 whitespace-pre-wrap break-words opacity-90">
                {operationEvaluation.semanticReason}
              </p>
            )}
            {operationEvaluation.semanticStatus === "approved" && (
              <p className="mt-0.5 text-ink-dim">
                Mechanical validation:{" "}
                {operationEvaluation.mechanicalStatus === "valid"
                  ? "passed"
                  : operationEvaluation.mechanicalStatus === "invalid"
                    ? `failed${finding.ops_invalid_reason ? ` — ${finding.ops_invalid_reason}` : ""}`
                    : "not evaluated"}
              </p>
            )}
          </div>
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
          {onResolveInChat && (
            <button
              className={cardBtn}
              onClick={onResolveInChat}
              disabled={busy}
              title="Prefill the chat with a focused request for the missing project facts"
            >
              Resolve in chat
            </button>
          )}
          <button className={cardBtn} onClick={onDismiss} disabled={busy}>
            Dismiss
          </button>
        </div>
      )}
    </div>
  );
}
