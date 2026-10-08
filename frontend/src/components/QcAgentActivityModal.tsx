/**
 * The full live view behind a Final QC Review Room card: what one specialist
 * lens, or one reviewer on an adversarial panel, is doing and weighing.
 *
 * A lens opens on its assignment (the brief it was given, verbatim), then a
 * timestamped feed of its reasoning summaries, every search and source read,
 * any retries, and its result — plus the candidates it raised, each of which
 * opens its panel. A reviewer seat opens on the claim its panel is trying to
 * refute, how the panel decides, one tab per reviewer, and the selected
 * reviewer's vote with its one-line reasons above the same kind of feed.
 *
 * Everything is a pure fold over the run's merged event log
 * (`lib/qcLive.foldQcAgentTimeline`, beside the board's own
 * `foldQcLiveState`), so the modal can never disagree with the card that
 * opened it, streams while the run is live, and reads as a plain record once
 * it ends. Reasoning summaries are the provider's own `thinking.display:
 * "summarized"` text — never the raw chain of thought, which the API does
 * not return — and render as plain text. The chrome and focus handling
 * follow `AgentActivityModal`, the research board's equivalent.
 */
import { Fragment, useEffect, useMemo, useRef } from "react";
import type { ReactNode, RefObject } from "react";
import type { QcEvent } from "../types";
import { useDialogFocus } from "../lib/dialogFocus";
import { safeHttpUrl } from "../lib/qcReport";
import {
  foldQcAgentTimeline,
  type QcAgentTarget,
  type QcAgentTimelineEntry,
  type QcCandidateLiveState,
  type QcLensLiveState,
  type QcLiveState,
  type QcVerifierSeatLiveState,
} from "../lib/qcLive";

interface Props {
  open: boolean;
  /** null only while closed. */
  target: QcAgentTarget | null;
  /** The run's merged event log — the same array the board folds. */
  events: QcEvent[];
  /** The board's fold of that log, for statuses and context. */
  live: QcLiveState;
  /** Display names for who does the work, from health. */
  lensModelName: string;
  seatModelName: string;
  /** Move to another agent without closing (a reviewer tab, a raised
   *  candidate). */
  onSelect: (target: QcAgentTarget) => void;
  onClose: () => void;
  /** Focus restoration when the opener is gone — the Review Room unmounts
   *  when the run ends, so closing then would drop focus onto the body. */
  restoreFallbackRef?: RefObject<HTMLButtonElement>;
}

const PILL_CLASS: Record<string, string> = {
  queued: "bg-ink-faint/15 text-ink-dim",
  running: "bg-accent/15 text-accent",
  completed: "bg-ok/15 text-ok",
  failed: "bg-err/15 text-err",
  interrupted: "bg-warn/15 text-warn",
  upheld: "bg-accent/15 text-accent",
  refuted: "bg-ink-faint/15 text-ink-dim",
  disputed: "bg-warn/15 text-warn",
  inconclusive: "bg-warn/15 text-warn",
};

const RETRY_REASONS: Record<string, string> = {
  rate_limit: "rate limited",
  server_error: "provider error",
  connection: "connection dropped",
};

const DISPUTE_REASONS: Record<string, string> = {
  split_panel: "the reviewers split, so it comes to you",
  insufficient_refutation_evidence:
    "the refutation cited no validated evidence, so it comes to you",
};

function plural(count: number, one: string, many = `${one}s`): string {
  return `${count} ${count === 1 ? one : many}`;
}

/** `**bold**` spans inside one line of a reasoning summary. Plain text
 *  otherwise — the summary is model-authored and never rendered as markup. */
function inline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, index) =>
    part.startsWith("**") && part.endsWith("**") && part.length > 4 ? (
      <strong key={index} className="font-semibold text-ink">
        {part.slice(2, -2)}
      </strong>
    ) : (
      <Fragment key={index}>{part}</Fragment>
    ),
  );
}

function SummaryText({ text, open }: { text: string; open: boolean }) {
  const paragraphs = text.split(/\n\s*\n/).filter((part) => part.trim());
  return (
    <span className="block space-y-1.5">
      {paragraphs.map((paragraph, index) => (
        <span key={index} className="block whitespace-pre-wrap break-words">
          {inline(paragraph.trim())}
          {open && index === paragraphs.length - 1 && (
            <span className="status-shimmer"> …</span>
          )}
        </span>
      ))}
    </span>
  );
}

function SourceLink({ url }: { url: string }) {
  // Copied verbatim from the streamed web-tool input, so a non-HTTP or
  // credential-bearing value renders inert (the research modal's posture).
  const safe = safeHttpUrl(url);
  const label = url.replace(/^https?:\/\/(www\.)?/, "");
  return safe ? (
    <a
      href={safe}
      target="_blank"
      rel="noreferrer"
      className="text-accent hover:underline"
      title={url}
    >
      {label}
    </a>
  ) : (
    <span title={url}>{label}</span>
  );
}

function entryContent(entry: QcAgentTimelineEntry, seat: boolean): ReactNode {
  switch (entry.kind) {
    case "started":
      if (seat) return <span className="text-ink-dim">Reviewer started</span>;
      return (
        <span className="text-ink-dim">
          Specialist started
          {entry.maxSearches > 0 || entry.maxFetches > 0
            ? ` — may run up to ${plural(entry.maxSearches, "search", "searches")} and ${plural(entry.maxFetches, "source read")} per request`
            : " — reviews the document and its inputs, no web access"}
        </span>
      );
    case "activity":
      return (
        <span className="text-ink-faint italic">
          {entry.activity === "writing"
            ? seat
              ? "Writing the verdict…"
              : "Writing the review record…"
            : "Thinking (no reasoning summary was sent)"}
        </span>
      );
    case "thinking":
      return (
        <span className="block min-w-0 flex-1 rounded-md border-l-2 border-accent/40 bg-bg/40 py-1 pr-1 pl-2 text-ink-dim">
          <SummaryText text={entry.text} open={entry.open} />
          {entry.truncated && (
            <span className="mt-1 block text-[10px] text-ink-faint italic">
              The rest of this summary was not relayed (the live view keeps the
              first 24,000 characters per request).
            </span>
          )}
        </span>
      );
    case "search":
      return (
        <span className="text-ink-dim">
          <span className="mr-1 text-ink-faint" aria-hidden="true">⌕</span>
          Searched &ldquo;{entry.query}&rdquo;
        </span>
      );
    case "fetch":
      return (
        <span className="text-ink-dim">
          <span className="mr-1 text-ink-faint" aria-hidden="true">▤</span>
          Read <SourceLink url={entry.url} />
        </span>
      );
    case "retry":
      return (
        <span className="text-warn">
          Attempt {entry.retry.attempt}/{entry.retry.maxAttempts} failed —{" "}
          {RETRY_REASONS[entry.retry.reason] ?? (entry.retry.reason || "temporary failure")}
          {" · "}retrying in {entry.retry.backoffSeconds}s
        </span>
      );
    case "lens_done":
      return entry.failed ? (
        <span className="text-err">✗ Failed — {entry.error || "did not complete"}</span>
      ) : (
        <span className="text-ink-faint">
          ✓ Completed — {plural(entry.reviewedChecks, "check")} reviewed ·{" "}
          {plural(entry.candidates, "candidate")} raised · {entry.grounded} grounded
        </span>
      );
    case "verdict":
      if (entry.status !== "completed") {
        return (
          <span className={entry.status === "cancelled" ? "text-warn" : "text-err"}>
            ✗ {entry.status === "cancelled" ? "Cancelled" : "Failed"}
            {entry.error ? ` — ${entry.error}` : ""}
          </span>
        );
      }
      return (
        <span className={entry.upholds ? "text-accent" : "text-ink-dim"}>
          {entry.upholds ? "● Voted to uphold the finding" : "— Voted to refute the finding"}
        </span>
      );
  }
}

function Timeline({
  timeline,
  seat,
  emptyLabel,
}: {
  timeline: QcAgentTimelineEntry[];
  seat: boolean;
  emptyLabel: string;
}) {
  if (timeline.length === 0) {
    return <p className="text-[11px] text-ink-faint">{emptyLabel}</p>;
  }
  return (
    <ol className="space-y-1.5">
      {timeline.map((entry) => (
        <li key={`${entry.kind}-${entry.seq}`} className="prompt-chip-in flex gap-2 text-[11px] leading-relaxed">
          <span className="w-14 shrink-0 text-ink-faint tabular-nums">{entry.ts}</span>
          <span className="min-w-0 flex-1 break-words">{entryContent(entry, seat)}</span>
        </li>
      ))}
    </ol>
  );
}

function lensPill(lens: QcLensLiveState | undefined, runLive: boolean): string {
  const status = lens?.status ?? "queued";
  if (!runLive && (status === "running" || status === "queued")) return "interrupted";
  return status;
}

function seatPill(seat: QcVerifierSeatLiveState | undefined, runLive: boolean): {
  key: string;
  label: string;
} {
  const status = seat?.status ?? "queued";
  if (!runLive && (status === "active" || status === "queued")) {
    return { key: "interrupted", label: "interrupted" };
  }
  if (status === "upheld") return { key: "upheld", label: "upheld" };
  if (status === "not_upheld") return { key: "refuted", label: "refuted" };
  if (status === "failed") return { key: "failed", label: "failed" };
  if (status === "cancelled") return { key: "interrupted", label: "cancelled" };
  if (status === "active") return { key: "running", label: "reviewing" };
  return { key: "queued", label: "queued" };
}

function SeatVerdict({ seat }: { seat: QcVerifierSeatLiveState }) {
  if (seat.status !== "upheld" && seat.status !== "not_upheld") return null;
  const upheld = seat.status === "upheld";
  return (
    <div
      className={`rounded-lg border px-3 py-2 text-[11px] leading-relaxed ${
        upheld ? "border-accent/35 bg-accent/5" : "border-edge bg-bg/40"
      }`}
    >
      <p className={`font-semibold ${upheld ? "text-accent" : "text-ink-dim"}`}>
        {upheld
          ? "Upholds the finding — it survived this reviewer's refutation attempt"
          : "Refutes the finding"}
        {seat.revisedSeverity ? ` · would rate it ${seat.revisedSeverity}` : ""}
      </p>
      {seat.note && <p className="mt-1 text-ink-dim">{seat.note}</p>}
      <p className="mt-1.5 text-ink-faint">
        Proposed fix:{" "}
        <span className={seat.opsAdequate ? "text-ok" : "text-ink-dim"}>
          {seat.opsAdequate === true
            ? "approved as safe and complete"
            : seat.opsAdequate === false
              ? "not approved"
              : "not judged"}
        </span>
        {seat.opsNote ? ` — ${seat.opsNote}` : ""}
      </p>
    </div>
  );
}

function candidateOutcomeLabel(candidate: QcCandidateLiveState): string {
  if (candidate.outcome === "upheld") return "upheld";
  if (candidate.outcome === "refuted") return "refuted";
  if (candidate.outcome === "disputed") return "disputed";
  if (candidate.outcome === "inconclusive") return "inconclusive";
  return "in review";
}

export default function QcAgentActivityModal({
  open,
  target,
  events,
  live,
  lensModelName,
  seatModelName,
  onSelect,
  onClose,
  restoreFallbackRef,
}: Props) {
  const timeline = useMemo(
    () => (target ? foldQcAgentTimeline(events, target) : []),
    [events, target],
  );
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  useDialogFocus(open, dialogRef, closeButtonRef, onClose, restoreFallbackRef);

  const runLive = live.runState === "running";
  const isSeat = target?.kind === "seat";
  const lens =
    target?.kind === "lens"
      ? live.lenses.find((item) => item.id === target.lensId)
      : undefined;
  const candidate =
    target?.kind === "seat"
      ? live.candidates.find((item) => item.id === target.candidateId)
      : undefined;
  const seat =
    target?.kind === "seat"
      ? candidate?.seats.find((item) => item.index === target.reviewerIndex)
      : undefined;
  const agentLive = isSeat
    ? runLive && (seat?.status === "active" || seat?.status === "queued" || !seat)
    : runLive && (lens?.status === "running" || lens?.status === "queued" || !lens);

  // Follow-bottom while live (the research modal's pattern), keyed on the
  // feed's growth — a reasoning summary grows in place, so the entry count
  // alone would miss it.
  const growth = timeline.reduce(
    (total, entry) => total + 1 + (entry.kind === "thinking" ? entry.text.length : 0),
    0,
  );
  const targetKey = target
    ? target.kind === "lens"
      ? target.lensId
      : `${target.candidateId}#${target.reviewerIndex}`
    : "";
  const scrollRef = useRef<HTMLDivElement>(null);
  const pinnedRef = useRef(true);
  useEffect(() => {
    if (!open) return;
    pinnedRef.current = agentLive;
    const el = scrollRef.current;
    if (el) el.scrollTop = agentLive ? el.scrollHeight : 0;
    // On open and on a switch of agent only — mid-view state changes must
    // not yank the reader's scroll position.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, targetKey]);
  useEffect(() => {
    const el = scrollRef.current;
    if (el && pinnedRef.current) el.scrollTop = el.scrollHeight;
  }, [growth]);
  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };

  if (!open || !target) return null;

  const lensTitle = (id: string) =>
    live.lenses.find((item) => item.id === id)?.title || id;
  const title =
    target.kind === "seat"
      ? candidate?.title || target.candidateId
      : lens?.title || target.lensId;
  const raised =
    target.kind === "lens"
      ? live.candidates.filter((item) => item.lensId === target.lensId)
      : [];

  let pillKey: string;
  let pillLabel: string;
  if (target.kind === "seat") {
    const pill = seatPill(seat, runLive);
    pillKey = pill.key;
    pillLabel = `reviewer ${target.reviewerIndex} · ${pill.label}`;
  } else {
    pillKey = lensPill(lens, runLive);
    pillLabel = pillKey;
  }
  const currentActivity = isSeat ? seat?.activity : lens?.activity;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 p-6 pt-16"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={`${title} — ${isSeat ? "adversarial panel" : "specialist"} activity`}
    >
      <div
        ref={dialogRef}
        tabIndex={-1}
        className="flex max-h-[82vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-edge bg-surface shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-start justify-between gap-4 border-b border-edge px-6 py-4">
          <div className="min-w-0">
            <p className="text-[10px] font-semibold tracking-[0.14em] text-accent uppercase">
              {isSeat ? "Adversarial panel" : "Specialist lens"}
            </p>
            <h2 className="mt-0.5 font-[family-name:var(--font-display)] text-lg font-semibold text-ink">
              {title}
            </h2>
            <p className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-ink-faint">
              <span
                className={`rounded-full px-1.5 py-px text-[10px] font-medium ${PILL_CLASS[pillKey] ?? PILL_CLASS.queued}`}
              >
                {pillLabel}
              </span>
              {isSeat && candidate && (
                <>
                  <span>
                    panel: {candidateOutcomeLabel(candidate)}
                    {candidate.outcome === "disputed" && candidate.disputeReason
                      ? ` — ${DISPUTE_REASONS[candidate.disputeReason] ?? candidate.disputeReason}`
                      : ""}
                  </span>
                  <span>{seatModelName}</span>
                </>
              )}
              {!isSeat && <span>{lensModelName}</span>}
            </p>
            {agentLive && (currentActivity || (isSeat ? seat?.status === "active" : lens?.status === "running")) && (
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
                  {currentActivity === "searching"
                    ? "Searching…"
                    : currentActivity === "fetching"
                      ? "Reading a source…"
                      : currentActivity === "writing"
                        ? isSeat
                          ? "Writing the verdict…"
                          : "Writing the review record…"
                        : "Thinking…"}
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

        {/* Body */}
        <div
          ref={scrollRef}
          onScroll={onScroll}
          className="min-h-0 flex-1 space-y-4 overflow-y-auto px-6 py-4"
        >
          {!isSeat && (
            <>
              <section aria-label="What this specialist checks">
                <h3 className="mb-1 text-[10px] font-semibold tracking-wide text-ink-faint uppercase">
                  What this specialist checks
                </h3>
                {lens?.brief ? (
                  <p className="text-[11px] leading-relaxed whitespace-pre-wrap text-ink-dim">
                    {lens.brief}
                  </p>
                ) : (
                  <p className="text-[11px] text-ink-faint">
                    This run's log does not record the brief.
                  </p>
                )}
                {lens?.web !== null && lens?.web !== undefined && (
                  <p className="mt-1 text-[10px] text-ink-faint">
                    {lens.web
                      ? "May search the web and read sources to check a standard's actual text."
                      : "Reviews the specification and its inputs only — no web access."}
                  </p>
                )}
              </section>
              {raised.length > 0 && (
                <section aria-label="Raised for adversarial review">
                  <h3 className="mb-1 text-[10px] font-semibold tracking-wide text-ink-faint uppercase">
                    Raised for adversarial review
                  </h3>
                  <ul className="space-y-1">
                    {raised.map((item) => (
                      <li key={item.id}>
                        <button
                          type="button"
                          data-capability="qc.agent-detail"
                          onClick={() =>
                            onSelect({ kind: "seat", candidateId: item.id, reviewerIndex: 1 })
                          }
                          className="flex w-full items-baseline justify-between gap-2 rounded-md border border-edge/70 bg-bg/30 px-2 py-1 text-left text-[11px] text-ink-dim transition-colors hover:border-accent/60"
                          title="Open this candidate's adversarial panel"
                        >
                          <span className="min-w-0 truncate">{item.title}</span>
                          <span className="shrink-0 text-[10px] text-ink-faint">
                            {candidateOutcomeLabel(item)} →
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </section>
              )}
            </>
          )}

          {isSeat && candidate && (
            <>
              <section aria-label="The claim under review">
                <h3 className="mb-1 text-[10px] font-semibold tracking-wide text-ink-faint uppercase">
                  The claim under review
                </h3>
                <p className="text-[11px] leading-relaxed text-ink-dim">
                  {candidate.issue || candidate.title}
                </p>
                <p className="mt-1 text-[10px] text-ink-faint">
                  Raised by {lensTitle(candidate.lensId)}
                  {candidate.originalSeverity ? ` as ${candidate.originalSeverity}` : ""}
                  {candidate.elementId ? ` · about ${candidate.elementId}` : ""}
                </p>
              </section>
              <section aria-label="How this panel decides">
                <h3 className="mb-1 text-[10px] font-semibold tracking-wide text-ink-faint uppercase">
                  How this panel decides
                </h3>
                <p className="text-[11px] leading-relaxed text-ink-dim">
                  {plural(candidate.panelSize, "reviewer")} each try, independently,
                  to refute the claim — wrong on the facts, already handled
                  elsewhere in the document, out of this section's scope, or
                  trivial — and refute when unsure. It is upheld only if every
                  reviewer upholds it; a majority refutation refutes it; any other
                  split is disputed and comes to you.
                  {candidate.evidenceGated
                    ? " Because it was raised as critical or high, a refutation must also cite validated evidence, or the finding is disputed instead of dropped."
                    : ""}{" "}
                  Each reviewer also judges whether the proposed fix is safe and
                  complete.
                </p>
                {live.transport === "batch" && (
                  <p className="mt-1 text-[10px] text-ink-faint">
                    This run sent its reviewers through the batch transport, which
                    does not stream: their reasoning is not shown live, and votes
                    land as the batch returns.
                  </p>
                )}
              </section>
              <div
                className="flex flex-wrap gap-1"
                role="tablist"
                aria-label="Reviewers on this panel"
              >
                {candidate.seats.map((item) => {
                  const pill = seatPill(item, runLive);
                  const selected =
                    target.kind === "seat" && item.index === target.reviewerIndex;
                  return (
                    <button
                      key={item.index}
                      type="button"
                      role="tab"
                      aria-selected={selected}
                      data-capability="qc.agent-detail"
                      onClick={() =>
                        onSelect({
                          kind: "seat",
                          candidateId: candidate.id,
                          reviewerIndex: item.index,
                        })
                      }
                      className={`rounded-full border px-2 py-0.5 text-[10px] transition-colors ${
                        selected
                          ? "border-accent bg-accent/12 text-ink"
                          : "border-edge bg-bg/30 text-ink-dim hover:border-accent/60"
                      }`}
                    >
                      Reviewer {item.index} · {pill.label}
                    </button>
                  );
                })}
              </div>
              {seat && <SeatVerdict seat={seat} />}
              {seat && (seat.status === "failed" || seat.status === "cancelled") && seat.error && (
                <p className="text-[11px] text-err">{seat.error}</p>
              )}
            </>
          )}

          <section aria-label="Activity">
            <h3 className="mb-1.5 text-[10px] font-semibold tracking-wide text-ink-faint uppercase">
              {isSeat ? "This reviewer's activity" : "Activity"}
            </h3>
            <Timeline
              timeline={timeline}
              seat={isSeat}
              emptyLabel={
                isSeat
                  ? live.transport === "batch"
                    ? "Waiting for the batch to return this reviewer's vote."
                    : "Not started yet — waiting for a free reviewer slot."
                  : "Not started yet — waiting for a specialist slot."
              }
            />
            {!runLive &&
              (isSeat
                ? seat?.status === "active" || seat?.status === "queued"
                : lens?.status === "running" || lens?.status === "queued") && (
                <p className="mt-1.5 text-[11px] text-warn">
                  The run ended before this {isSeat ? "reviewer" : "specialist"} finished.
                </p>
              )}
          </section>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between gap-3 border-t border-edge px-6 py-3">
          <p className="text-[11px] text-ink-faint">
            {agentLive
              ? "Streaming live — reasoning summaries, searches and sources appear as they happen."
              : runLive
                ? `This ${isSeat ? "reviewer has voted" : "specialist has finished"}; the run is still going.`
                : "The full record for this run. Reasoning shown is the model's own summary."}
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
