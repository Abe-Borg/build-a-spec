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
 * transparency. A staleness banner shows when the document has moved on from
 * the version QC reviewed.
 *
 * Reuses Batch 2's status machinery (no dead air — a QC run takes minutes and
 * the drawer shows lens-by-lens progress the whole time) and Batch 3's
 * hold-to-confirm affordance.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import type {
  QcFinding,
  QcSnapshot,
  ReadinessPayload,
  Severity,
  SpecDoc,
  UsageSummary,
} from "../types";
import ConfirmDialog from "./ConfirmDialog";

interface Props {
  qc: QcSnapshot | null;
  readiness: ReadinessPayload | null;
  doc: SpecDoc | null;
  busy: boolean;
  usage: UsageSummary | null;
  onStart: () => void;
  onStop: () => void;
  onApply: (findingIds: string[]) => void;
  onDismiss: (findingId: string, reason?: string) => void;
  onJump: (elementId: string) => void;
  /** Guided-tour "ensure open" (Batch 6): a bump expands the drawer. */
  openNonce?: number;
}

const HOLD_MS = 800;
const SEVERITY_ORDER: Severity[] = ["critical", "high", "medium", "low"];

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
  const [holding, setHolding] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [stopConfirmOpen, setStopConfirmOpen] = useState(false);
  const holdTimer = useRef<number | undefined>(undefined);

  const status = qc?.status ?? "idle";
  const running = status === "running";
  const result = qc?.result;
  const findings = result?.findings ?? [];
  const openFindings = findings.filter((f) => f.status === "open");
  const openCriticals = openFindings.filter((f) => f.severity === "critical");
  const stale =
    result != null && doc != null && result.version_index !== doc.version.index;

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
    if (busy || openCriticals.length === 0) return;
    setHolding(true);
    holdTimer.current = window.setTimeout(() => {
      setHolding(false);
      onApply(openCriticals.filter((f) => f.ops_valid).map((f) => f.finding_id));
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

  const startLabel = running
    ? "Reviewing…"
    : result
      ? "Re-run Final QC"
      : "Send to Final QC";

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
          className="flex min-w-0 flex-1 items-baseline gap-2 text-left text-[11px] text-ink-faint transition-colors hover:text-ink-dim"
          onClick={() => setExpanded((v) => !v)}
          title="Final QC — a fleet of Fable 5 reviewers before the section goes out the door"
        >
          <span className="shrink-0 font-medium tracking-wide uppercase">
            Final QC
          </span>
          <span className="truncate">
            {running && verify
              ? `verifying findings… (${verify.done}/${verify.total})`
              : running
                ? "reviewing…"
                : result
                  ? `${openFindings.length} open finding${openFindings.length === 1 ? "" : "s"}` +
                    (openCriticals.length ? ` · ${openCriticals.length} critical` : "")
                  : readiness?.ready
                    ? "ready to issue"
                    : "not yet reviewed"}
          </span>
          <span className="ml-auto shrink-0">{expanded ? "▾" : "▸"}</span>
        </button>
        <button
          className={`shrink-0 rounded-md border px-2 py-0.5 text-[11px] transition-colors disabled:pointer-events-none disabled:opacity-40 ${
            result
              ? "border-edge bg-raised text-ink-dim hover:border-accent hover:text-accent"
              : "border-accent/70 bg-accent/15 text-accent hover:bg-accent/25"
          }`}
          onClick={() => setConfirmOpen(true)}
          disabled={running || busy}
          title="Review what a pass costs and does, then confirm — runs the full lens fan-out + adversarial verification on Fable 5 (uses your API key)"
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

      {status === "failed" && qc?.error && (
        <p className="mt-1 text-[11px] text-err">Final QC: {qc.error}</p>
      )}

      <ConfirmDialog
        open={stopConfirmOpen}
        title="Stop Final QC?"
        body={
          <p>
            This stops the Final QC pass now in progress.{" "}
            <strong className="text-ink">
              Any progress made so far will be lost
            </strong>{" "}
            — findings already reviewed won&apos;t be saved, and the Fable 5
            spend already incurred is not refunded. You&apos;ll need to
            re-run the whole pass.
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
        <div className="mt-1.5 max-h-[28rem] space-y-3 overflow-y-auto pb-1">
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
          {status !== "running" && !result && (
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
              {result.summary && (
                <p className="text-[11px] text-ink-dim">{result.summary}</p>
              )}

              {openCriticals.length >= 2 && (
                <button
                  className="relative w-full overflow-hidden rounded-md border border-err/50 bg-err/10 px-2 py-1 text-[11px] text-err transition-colors hover:border-err disabled:pointer-events-none disabled:opacity-40"
                  onPointerDown={startHoldApplyCriticals}
                  onPointerUp={cancelHold}
                  onPointerLeave={cancelHold}
                  onPointerCancel={cancelHold}
                  disabled={busy}
                  title="Press and hold to apply every critical finding with a valid fix — one undo step"
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
                      : `Hold to apply all ${openCriticals.length} criticals`}
                  </span>
                </button>
              )}

              {openFindings.length === 0 && (
                <p className="text-[11px] text-ok">
                  No open findings — everything is applied or dismissed. ✓
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
                        busy={busy}
                        open={!!openRationale[f.finding_id]}
                        onToggle={() =>
                          setOpenRationale((m) => ({
                            ...m,
                            [f.finding_id]: !m[f.finding_id],
                          }))
                        }
                        onApply={() => onApply([f.finding_id])}
                        onDismiss={() => onDismiss(f.finding_id)}
                        onJump={onJump}
                      />
                    ))}
                  </div>
                );
              })}

              {result.refuted.length > 0 && (
                <div>
                  <button
                    className="text-[11px] text-ink-faint hover:text-ink-dim"
                    onClick={() => setShowRefuted((v) => !v)}
                  >
                    {showRefuted ? "▾" : "▸"} Refuted in verification (
                    {result.refuted.length}) — not open issues
                  </button>
                  {showRefuted && (
                    <ul className="mt-1 space-y-0.5">
                      {result.refuted.map((f) => (
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
        isRerun={!!result}
        costEstimate={costEstimate}
        busy={busy}
        onConfirm={confirmStart}
        onCancel={() => setConfirmOpen(false)}
      />
    )}
    </>
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
  open,
  onToggle,
  onApply,
  onDismiss,
  onJump,
}: {
  finding: QcFinding;
  busy: boolean;
  open: boolean;
  onToggle: () => void;
  onApply: () => void;
  onDismiss: () => void;
  onJump: (elementId: string) => void;
}) {
  const dimmed = finding.status !== "open";
  const cardBtn =
    "rounded-md border border-edge bg-raised px-2 py-0.5 text-[11px] text-ink-dim transition-colors hover:border-accent hover:text-accent disabled:pointer-events-none disabled:opacity-40";
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
      >
        {open ? "▾ hide rationale" : "▸ rationale"}
      </button>
      {open && (
        <div className="mt-1 space-y-1">
          <p className="text-[11px] text-ink-faint">{finding.rationale}</p>
          {finding.accepted_sources.length > 0 && (
            <p className="text-[11px]">
              {finding.accepted_sources.map((url) => (
                <a
                  key={url}
                  href={url}
                  target="_blank"
                  rel="noreferrer"
                  className="mr-1 text-accent hover:underline"
                  title={url}
                >
                  [src]
                </a>
              ))}
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
          <button
            className={cardBtn}
            onClick={onApply}
            disabled={busy || !finding.ops_valid || finding.proposed_ops.length === 0}
            title={
              finding.ops_valid && finding.proposed_ops.length > 0
                ? "Apply this fix to the document (one undo step)"
                : finding.ops_invalid_reason || "No mechanical fix — advisory only"
            }
          >
            Apply fix
          </button>
          <button className={cardBtn} onClick={onDismiss} disabled={busy}>
            Dismiss
          </button>
        </div>
      )}
    </div>
  );
}
