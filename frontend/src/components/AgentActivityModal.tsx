/**
 * The full live view behind a research agent card: everything the compact
 * board card truncates — the complete query history, every source URL, the
 * web-tool budgets, and each retry with its backoff — as a chronological,
 * timestamped feed that streams while the run is live and reads as a plain
 * record afterward. The data is a pure fold over the same merged event log
 * the board uses (lib/researchAgents), so the modal can never disagree
 * with the card that opened it. Chrome follows ResearchReportModal; the
 * keyboard wiring follows QCReportModal (useDialogFocus).
 */
import { useEffect, useMemo, useRef } from "react";
import type { RefObject } from "react";
import type { ResearchEvent, ResearchRunStatus } from "../types";
import { useDialogFocus } from "../lib/dialogFocus";
import { safeHttpUrl } from "../lib/qcReport";
import {
  ACTIVITY_LABELS,
  RETRY_REASONS,
  foldAgentDetail,
  labelFromId,
  trimUrl,
  type AgentTimelineEntry,
} from "../lib/researchAgents";

interface Props {
  open: boolean;
  /** "" only while closed. */
  dimensionId: string;
  /** The run's merged event log — the same array the board folds. */
  events: ResearchEvent[];
  /** Run-level status: the run can end while the modal is open. */
  runStatus: ResearchRunStatus;
  onClose: () => void;
  /** Focus restoration when the opener card is gone — the board unmounts
   *  when the run completes mid-view, so closing then would otherwise drop
   *  keyboard focus onto the document body. */
  restoreFallbackRef?: RefObject<HTMLButtonElement>;
}

/** Header status pill. "interrupted" is derived, not an agent state: the
 *  run ended (stopped/failed) before this agent reached a terminal event —
 *  never claim "running" for a dead run. */
const PILLS: Record<string, { label: string; cls: string }> = {
  queued: { label: "queued", cls: "bg-ink-faint/15 text-ink-dim" },
  running: { label: "running", cls: "bg-accent/15 text-accent" },
  done: { label: "completed", cls: "bg-ok/15 text-ok" },
  failed: { label: "failed", cls: "bg-err/15 text-err" },
  interrupted: { label: "interrupted", cls: "bg-warn/15 text-warn" },
};

function entryContent(entry: AgentTimelineEntry) {
  switch (entry.kind) {
    case "started":
      return (
        <span className="min-w-0 break-words text-ink-dim">
          Agent started
          {entry.maxSearches > 0 || entry.maxFetches > 0
            ? ` — budget ${entry.maxSearches} searches · ${entry.maxFetches} fetches`
            : ""}
        </span>
      );
    case "activity":
      return (
        <span className="min-w-0 break-words text-ink-dim">
          {ACTIVITY_LABELS[entry.activity] ?? entry.activity}
        </span>
      );
    case "search":
      return (
        <span className="min-w-0 break-words text-ink-dim">
          Searched &ldquo;{entry.query}&rdquo;
        </span>
      );
    case "fetch": {
      // The URL is copied verbatim from the streamed web-tool input, so a
      // non-HTTP or credential-bearing value renders inert, never as a
      // clickable link (the QC report-source posture).
      const safe = safeHttpUrl(entry.url);
      return (
        <span className="min-w-0 break-words text-ink-dim">
          Read{" "}
          {safe ? (
            <a
              href={safe}
              target="_blank"
              rel="noreferrer"
              className="text-accent hover:underline"
              title={entry.url}
            >
              {trimUrl(entry.url)}
            </a>
          ) : (
            <span title={entry.url}>{trimUrl(entry.url)}</span>
          )}
        </span>
      );
    }
    case "retry":
      return (
        <span className="min-w-0 break-words text-warn">
          Attempt {entry.attempt}/{entry.max} failed —{" "}
          {RETRY_REASONS[entry.reason] ?? entry.reason} · retrying in{" "}
          {entry.backoffS}s
        </span>
      );
    case "done":
      return (
        <span className="min-w-0 break-words text-ink-faint">
          ✓ Completed — {entry.itemCount} finding
          {entry.itemCount === 1 ? "" : "s"} · {entry.groundedCount} grounded ·{" "}
          {entry.billedSearches} search
          {entry.billedSearches === 1 ? "" : "es"} · {entry.billedFetches} fetch
          {entry.billedFetches === 1 ? "" : "es"} billed
        </span>
      );
    case "failed":
      return (
        <span className="min-w-0 break-words text-err">
          ✗ Failed — {entry.error || "failed"}
        </span>
      );
  }
}

export default function AgentActivityModal({
  open,
  dimensionId,
  events,
  runStatus,
  onClose,
  restoreFallbackRef,
}: Props) {
  // Live updates for free: mergeResearchEvent appends into the same array
  // identity chain, so this memo re-folds exactly once per SSE commit.
  const detail = useMemo(
    () => foldAgentDetail(events, dimensionId),
    [events, dimensionId],
  );
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  useDialogFocus(open, dialogRef, closeButtonRef, onClose, restoreFallbackRef);

  const state = detail.dim?.state ?? "queued";
  const live = runStatus === "running" && state !== "done" && state !== "failed";
  const interrupted = !live && state !== "done" && state !== "failed";

  // Follow-bottom (Chat.tsx's pinned-scroll pattern, minus its rAF loop —
  // this feed only grows on React commits). Opening a live feed starts
  // pinned at the newest entry; a finished log reads top-down.
  const scrollRef = useRef<HTMLDivElement>(null);
  const pinnedRef = useRef(true);
  useEffect(() => {
    if (!open) return;
    pinnedRef.current = live;
    const el = scrollRef.current;
    if (el) el.scrollTop = live ? el.scrollHeight : 0;
    // Deliberately on open/close only — mid-view state changes must not
    // yank the reader's scroll position.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
  useEffect(() => {
    const el = scrollRef.current;
    if (el && pinnedRef.current) el.scrollTop = el.scrollHeight;
  }, [detail.timeline.length]);
  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };

  if (!open) return null;

  const dim = detail.dim;
  const title = dim?.title || labelFromId(dimensionId);
  const pill = PILLS[interrupted ? "interrupted" : state];

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 p-6 pt-16"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={`${title} — agent activity`}
    >
      <div
        ref={dialogRef}
        tabIndex={-1}
        className="flex max-h-[80vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-edge bg-surface shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header: title + status pill + state-dependent stats + close */}
        <div className="flex items-start justify-between gap-4 border-b border-edge px-6 py-4">
          <div className="min-w-0">
            <h2 className="font-[family-name:var(--font-display)] text-lg font-semibold text-ink">
              {title}
            </h2>
            <p className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-ink-faint">
              <span
                className={`rounded-full px-1.5 py-px text-[10px] font-medium ${pill.cls}`}
              >
                {pill.label}
              </span>
              {detail.round > 0 && <span>round {detail.round}</span>}
            </p>
            <p className="mt-1 text-[11px] text-ink-faint tabular-nums">
              {state === "queued" && "Waiting for an agent…"}
              {state === "running" && dim && (
                <>
                  {dim.searches} search{dim.searches === 1 ? "" : "es"}
                  {detail.maxSearches > 0 && ` of ${detail.maxSearches}`}
                  {" · "}
                  {dim.fetches} source{dim.fetches === 1 ? "" : "s"}
                  {detail.maxFetches > 0 && ` of ${detail.maxFetches}`}
                </>
              )}
              {state === "done" && dim && (
                <>
                  ✓ {dim.itemCount} finding{dim.itemCount === 1 ? "" : "s"} ·{" "}
                  {dim.groundedCount} grounded · {dim.billedSearches} search
                  {dim.billedSearches === 1 ? "" : "es"} · {dim.billedFetches}{" "}
                  fetch{dim.billedFetches === 1 ? "" : "es"}
                </>
              )}
              {state === "failed" && dim && (
                <span className="text-err">✗ {dim.error || "failed"}</span>
              )}
            </p>
            {live && state === "running" && (
              <p
                className="mt-1.5 flex items-center gap-1.5 text-[11px] text-ink-dim"
                aria-live="polite"
              >
                <span className="status-dots" aria-hidden="true">
                  <span />
                  <span />
                  <span />
                </span>
                <span className="status-shimmer">
                  {ACTIVITY_LABELS[dim?.activity ?? ""] ?? "Starting…"}
                </span>
              </p>
            )}
          </div>
          <button
            ref={closeButtonRef}
            onClick={onClose}
            className="shrink-0 rounded-lg px-2 py-1 text-ink-dim transition-colors hover:text-ink"
            title="Close"
            aria-label="Close agent activity"
          >
            ✕
          </button>
        </div>

        {/* Body: the full chronological activity feed */}
        <div
          ref={scrollRef}
          onScroll={onScroll}
          className="min-h-0 flex-1 overflow-y-auto px-6 py-3"
        >
          {detail.timeline.length === 0 && !interrupted && (
            <p className="text-[11px] text-ink-faint">
              No activity yet — waiting for an agent to pick this dimension
              up.
            </p>
          )}
          <ol className="space-y-1">
            {detail.timeline.map((entry) => (
              <li
                key={entry.seq}
                className="prompt-chip-in flex gap-2 text-[11px]"
              >
                <span className="shrink-0 text-ink-faint tabular-nums">
                  {entry.ts}
                </span>
                {entryContent(entry)}
              </li>
            ))}
          </ol>
          {interrupted && (
            <p className="mt-1 text-[11px] text-warn">
              Run ended before this agent finished.
            </p>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between gap-3 border-t border-edge px-6 py-3">
          <p className="text-[11px] text-ink-faint">
            {live
              ? "Streaming live — this log updates as the agent works."
              : "Full activity log for this agent's latest round."}
          </p>
          <button
            onClick={onClose}
            className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
