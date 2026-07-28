/**
 * The requirements-research surface under the document panel: the project
 * profile line (with an inline form so the user can fill it out directly at
 * any time, rather than only through chat), the launch button, live
 * per-dimension progress while a run streams, and the grounded-citations
 * list when it completes. The strip is always visible so the feature is
 * discoverable; the launch button stays disabled (with a hover tooltip
 * explaining why) until the profile is complete. Ungrounded items are
 * marked [UNVERIFIED]; process advisories are marked [PROCESS] and never
 * become spec text.
 *
 * The Phase 5 compliance-audit control moved out of here in Batch 4 — the
 * Final QC drawer supersedes it (its code_compliance + completeness lenses
 * cover the audit's ground and more).
 */
import { useEffect, useMemo, useRef, useState } from "react";
import type {
  EditOp,
  ResearchRunStatus,
  ResearchSnapshot,
  SpecDoc,
} from "../types";
import {
  ACTIVITY_LABELS,
  RETRY_REASONS,
  foldResearchBoard,
  labelFromId,
  trimUrl,
  type DimLive,
} from "../lib/researchAgents";
import AgentActivityModal from "./AgentActivityModal";
import ConfirmDialog from "./ConfirmDialog";
import ResearchReportModal from "./ResearchReportModal";
import Tip from "./Tip";

interface Props {
  doc: SpecDoc | null;
  profileComplete: boolean;
  research: ResearchSnapshot | null;
  busy: boolean;
  onStart: () => void;
  onStop: () => void;
  onEditDoc: (ops: EditOp[]) => void;
  /** Guided-tour "ensure open" (Batch 6): a bump expands the drawer. */
  openNonce?: number;
}

const statusLabel: Record<ResearchRunStatus, string> = {
  idle: "not run",
  running: "researching…",
  complete: "complete",
  failed: "failed",
};

/* --- The live agent board. Its fold + vocabulary live in
 *     lib/researchAgents, shared with AgentActivityModal (the full
 *     per-agent view a card click opens). --- */

function AgentCard({ dim, onOpen }: { dim: DimLive; onOpen: () => void }) {
  const dotClass =
    dim.state === "done"
      ? "bg-ok"
      : dim.state === "failed"
        ? "bg-err"
        : dim.state === "running"
          ? "agent-dot"
          : "bg-ink-faint";
  // A real <button> (keyboard/focus for free, the drawer-header idiom), so
  // every inner block is a span — a button allows no p/ul/li descendants.
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-haspopup="dialog"
      data-capability="research.agent-detail"
      title="View this agent's full activity"
      className="w-full rounded-lg border border-edge bg-surface/50 p-2.5 text-left transition-colors hover:border-accent/60"
    >
      <span className="flex items-baseline gap-2">
        <span
          className={`h-1.5 w-1.5 shrink-0 translate-y-[-1px] rounded-full ${dotClass}`}
          aria-hidden="true"
        />
        <span className="min-w-0 truncate text-[11px] font-medium text-ink-dim">
          {dim.title || labelFromId(dim.id)}
        </span>
        {(dim.searches > 0 || dim.fetches > 0) && dim.state === "running" && (
          <span className="ml-auto flex shrink-0 gap-1 text-[10px] text-ink-faint tabular-nums">
            <span key={`s${dim.searches}`} className="tally-flash">
              {dim.searches} {dim.searches === 1 ? "search" : "searches"}
            </span>
            <span aria-hidden="true">·</span>
            <span key={`f${dim.fetches}`} className="tally-flash">
              {dim.fetches} {dim.fetches === 1 ? "source" : "sources"}
            </span>
          </span>
        )}
      </span>
      {dim.state === "queued" && (
        <span className="mt-1 block text-[11px] text-ink-faint">
          Waiting for an agent…
        </span>
      )}
      {dim.state === "running" && !dim.retry && (
        <span
          className="mt-1 flex items-center gap-1.5 text-[11px] text-ink-dim"
          aria-live="polite"
        >
          <span className="status-dots" aria-hidden="true">
            <span />
            <span />
            <span />
          </span>
          <span className="status-shimmer">
            {ACTIVITY_LABELS[dim.activity] ?? "Starting…"}
          </span>
        </span>
      )}
      {dim.state === "running" && dim.retry && (
        <span className="mt-1 block text-[11px] text-warn">
          Retrying (attempt {dim.retry.attempt}/{dim.retry.max}) —{" "}
          {RETRY_REASONS[dim.retry.reason] ?? dim.retry.reason}…
        </span>
      )}
      {dim.state === "running" && dim.recent.length > 0 && (
        <span className="mt-1 block space-y-0.5">
          {dim.recent.map((r) => (
            <span
              key={r.seq}
              className="prompt-chip-in block truncate text-[11px] text-ink-faint"
              title={r.text}
            >
              {r.kind === "search" ? <>&ldquo;{r.text}&rdquo;</> : trimUrl(r.text)}
            </span>
          ))}
        </span>
      )}
      {dim.state === "done" && (
        <span className="mt-1 block text-[11px] text-ink-faint">
          ✓ {dim.itemCount} finding{dim.itemCount === 1 ? "" : "s"} ·{" "}
          {dim.groundedCount} grounded · {dim.billedSearches} search
          {dim.billedSearches === 1 ? "" : "es"} · {dim.billedFetches} fetch
          {dim.billedFetches === 1 ? "" : "es"}
        </span>
      )}
      {dim.state === "failed" && (
        <span className="mt-1 block text-[11px] text-err">
          ✗ {dim.error || "failed"}
        </span>
      )}
    </button>
  );
}

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

interface ProfileFormState {
  city: string;
  state: string;
  country: string;
  client: string;
}

function profileFormFromDoc(doc: SpecDoc | null): ProfileFormState {
  const p = doc?.project_profile;
  return {
    city: p?.city ?? "",
    state: p?.state_or_province ?? "",
    country: p?.country ?? "",
    client: p?.client_name ?? "",
  };
}

const profileInputClass =
  "rounded border border-edge bg-raised px-2 py-1 text-[11px] text-ink outline-none focus:border-accent disabled:opacity-40";

function ProjectProfileForm({
  doc,
  busy,
  onEditDoc,
}: {
  doc: SpecDoc | null;
  busy: boolean;
  onEditDoc: (ops: EditOp[]) => void;
}) {
  const [form, setForm] = useState<ProfileFormState>(() => profileFormFromDoc(doc));
  const [saving, setSaving] = useState(false);
  const allBlank =
    !form.city.trim() && !form.state.trim() && !form.country.trim() && !form.client.trim();

  const save = async () => {
    setSaving(true);
    try {
      await onEditDoc([
        {
          action: "set_project_profile",
          target_id: "sec",
          city: form.city.trim(),
          state: form.state.trim(),
          country: form.country.trim(),
          client: form.client.trim(),
        },
      ]);
    } finally {
      setSaving(false);
    }
  };

  const disabled = busy || saving;
  return (
    <div className="space-y-1.5 rounded border border-edge/70 bg-bg/40 p-2">
      <p className="text-[11px] font-medium tracking-wide text-ink-faint uppercase">
        Project profile
      </p>
      <div className="grid grid-cols-2 gap-1.5">
        <input
          className={profileInputClass}
          placeholder="City"
          value={form.city}
          disabled={disabled}
          onChange={(e) => setForm({ ...form, city: e.target.value })}
        />
        <input
          className={profileInputClass}
          placeholder="State / province"
          value={form.state}
          disabled={disabled}
          onChange={(e) => setForm({ ...form, state: e.target.value })}
        />
        <select
          className={profileInputClass}
          value={form.country}
          disabled={disabled}
          onChange={(e) => setForm({ ...form, country: e.target.value })}
        >
          <option value="">Country…</option>
          <option value="US">United States</option>
          <option value="CA">Canada</option>
        </select>
        <input
          className={profileInputClass}
          placeholder="Client / owner"
          value={form.client}
          disabled={disabled}
          onChange={(e) => setForm({ ...form, client: e.target.value })}
        />
      </div>
      <div className="flex items-center gap-2">
        <button
          className="rounded-md border border-edge bg-raised px-2 py-0.5 text-[11px] text-ink-dim transition-colors hover:border-accent hover:text-accent disabled:pointer-events-none disabled:opacity-40"
          onClick={() => void save()}
          disabled={disabled || allBlank}
        >
          {saving ? "Saving…" : "Save profile"}
        </button>
        <span className="text-[11px] text-ink-faint">
          Fill this out up front, or let it come up naturally in chat.
        </span>
      </div>
    </div>
  );
}

export default function ResearchDrawer({
  doc,
  profileComplete,
  research,
  busy,
  onStart,
  onStop,
  onEditDoc,
  openNonce,
}: Props) {
  const [expanded, setExpanded] = useState(false);
  // The tour opens the drawer by bumping the nonce; the user can still
  // collapse it freely — the tour never fights back.
  useEffect(() => {
    if (openNonce) setExpanded(true);
  }, [openNonce]);
  const [stopConfirmOpen, setStopConfirmOpen] = useState(false);
  const [reportOpen, setReportOpen] = useState(false);
  // Which agent's full activity modal is open; null = closed. Torn down
  // with the research state itself (reset / failed status refresh).
  const [agentDetailId, setAgentDetailId] = useState<string | null>(null);
  useEffect(() => {
    if (!research) setAgentDetailId(null);
  }, [research]);
  // Focus fallback for the modal: the opener card unmounts with the board
  // when the run completes mid-view, so closing then restores focus to the
  // always-rendered drawer toggle instead of dropping it on the body.
  const collapseToggleRef = useRef<HTMLButtonElement>(null);
  const status: ResearchRunStatus = research?.status ?? "idle";
  const running = status === "running";
  const items = research?.profile?.items ?? [];
  const grounded = items.filter((i) => i.grounded).length;
  const profile = profileLine(doc);
  // The live agent board, folded from the run's event stream. Also the
  // source of the done/total progress label — the log now carries chatty
  // live events, so "the last event" no longer implies a dimension event.
  const board = useMemo(
    () => foldResearchBoard(research?.events ?? []),
    [research?.events],
  );
  // Rounds accumulate: a further run adds to these findings, never
  // replaces them. Legacy payloads carry no rounds — treat a profile
  // without them as the one round it is.
  const rounds = research?.profile
    ? (research.profile.rounds ?? []).length || 1
    : 0;

  const startDisabled = !profileComplete || running || busy;
  const startTip = !profileComplete
    ? "Complete the project profile first — city, state, country, and client — via the form below or in chat."
    : running
      ? "Research is already running."
      : busy
        ? "Finish the current turn first."
        : rounds > 0
          ? `Run another round of research (uses your API key). It ADDS to the ${items.length} finding(s) you already have — nothing is replaced, and a requirement found again is confirmed in place.`
          : "Run grounded web research for this jurisdiction, AHJ, and client (uses your API key).";
  const startLabel = running
    ? board.total > 0
      ? `Researching… (${board.doneCount}/${board.total})`
      : "Research in progress…"
    : rounds > 0
      ? `Research again (round ${rounds + 1})`
      : "Research requirements";
  const startButtonClass = running
    ? "border-accent/40 bg-raised text-ink-dim"
    : rounds > 0 || startDisabled
      ? "border-edge bg-raised text-ink-dim hover:border-accent hover:text-accent disabled:opacity-40"
      : "border-accent/70 bg-accent/15 text-accent hover:bg-accent/25";

  return (
    <div
      className="border-t border-edge bg-bg/70 px-5 py-2"
      data-tour="research-drawer"
      data-capability="research.profile"
    >
      <div className="flex items-baseline gap-2">
        <button
          ref={collapseToggleRef}
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
            {rounds > 0 && ` · ${grounded}/${items.length} grounded`}
            {rounds > 1 && ` over ${rounds} rounds`}
            {running && board.total > 0 && (
              <span className="status-shimmer">
                {` · ${board.doneCount}/${board.total} agents · ${board.totalSearches} searches · ${board.totalFetches} sources`}
              </span>
            )}
          </span>
          <span className="ml-auto shrink-0">{expanded ? "▾" : "▸"}</span>
        </button>
        {research?.profile && !running && (
          <button
            className="shrink-0 rounded-md border border-edge bg-raised px-2 py-0.5 text-[11px] text-ink-dim transition-colors hover:border-accent hover:text-accent"
            onClick={() => setReportOpen(true)}
            data-capability="research.report"
            title="View the full research findings report"
          >
            View report
          </button>
        )}
        <Tip tip={startTip} className="shrink-0">
          <button
            className={`rounded-md border px-2 py-0.5 text-[11px] transition-colors disabled:pointer-events-none ${startButtonClass}`}
            onClick={onStart}
            disabled={startDisabled}
            data-tour="research-start"
            data-capability="research.run"
          >
            {running ? (
              <span className="status-shimmer">{startLabel}</span>
            ) : (
              startLabel
            )}
          </button>
        </Tip>
        {running && (
          <button
            className="shrink-0 rounded-md border border-edge bg-raised px-2 py-0.5 text-[11px] text-ink-dim transition-colors hover:border-err hover:text-err"
            onClick={() => setStopConfirmOpen(true)}
            data-capability="research.stop"
            title="Stop research"
          >
            Stop
          </button>
        )}
      </div>

      {status === "failed" && research?.error && (
        <p className="mt-1 text-[11px] text-err">{research.error}</p>
      )}

      <ConfirmDialog
        open={stopConfirmOpen}
        title="Stop research?"
        body={
          <p>
            This stops the requirements research now in progress.{" "}
            <strong className="text-ink">
              This round&apos;s progress will be lost
            </strong>{" "}
            — dimensions it has already completed won&apos;t be saved.
            {rounds > 0
              ? ` The ${items.length} finding(s) from earlier rounds stay exactly as they are.`
              : " You'll need to start over."}
          </p>
        }
        confirmLabel="Stop research"
        danger
        onConfirm={() => {
          setStopConfirmOpen(false);
          onStop();
        }}
        onCancel={() => setStopConfirmOpen(false)}
      />

      <ResearchReportModal
        open={reportOpen}
        profile={research?.profile}
        onClose={() => setReportOpen(false)}
      />

      <AgentActivityModal
        open={agentDetailId !== null}
        dimensionId={agentDetailId ?? ""}
        events={research?.events ?? []}
        runStatus={status}
        onClose={() => setAgentDetailId(null)}
        restoreFallbackRef={collapseToggleRef}
      />

      {expanded && (
        <div className="mt-1.5">
          <ProjectProfileForm doc={doc} busy={busy} onEditDoc={onEditDoc} />
        </div>
      )}

      {expanded && research && (
        <div className="mt-1.5 max-h-64 space-y-2 overflow-y-auto pb-1">
          {running && (
            <div className="space-y-1.5">
              {board.total === 0 && (
                <p
                  className="flex items-center gap-1.5 text-[11px] text-ink-dim"
                  role="status"
                >
                  <span className="status-dots" aria-hidden="true">
                    <span />
                    <span />
                    <span />
                  </span>
                  <span className="status-shimmer">Starting research…</span>
                </p>
              )}
              {board.dims.map((d) => (
                <AgentCard
                  key={d.id}
                  dim={d}
                  onOpen={() => setAgentDetailId(d.id)}
                />
              ))}
            </div>
          )}

          {research.profile && (
            <>
              <p className="text-[11px] text-ink-faint">
                {rounds > 1
                  ? `${rounds} rounds, latest ${research.profile.research_date}`
                  : `Researched ${research.profile.research_date}`}
                {research.profile.dimension_statuses.some(
                  (d) => d.status !== "completed",
                ) &&
                  ` — partial (${
                    research.profile.dimension_statuses.filter(
                      (d) => d.status !== "completed",
                    ).length
                  } dimension(s) never completed)`}
              </p>
              <ul className="space-y-1" data-capability="research.apply">
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
