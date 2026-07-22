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
import { useEffect, useState } from "react";
import type {
  EditOp,
  ResearchRunStatus,
  ResearchSnapshot,
  SpecDoc,
} from "../types";
import ConfirmDialog from "./ConfirmDialog";
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
  const status: ResearchRunStatus = research?.status ?? "idle";
  const running = status === "running";
  const items = research?.profile?.items ?? [];
  const grounded = items.filter((i) => i.grounded).length;
  const profile = profileLine(doc);
  const lastEvent = research?.events[research.events.length - 1];

  const startDisabled = !profileComplete || running || busy;
  const startTip = !profileComplete
    ? "Complete the project profile first — city, state, country, and client — via the form below or in chat."
    : running
      ? "Research is already running."
      : busy
        ? "Finish the current turn first."
        : "Run grounded web research for this jurisdiction, AHJ, and client (uses your API key).";
  const startLabel = running
    ? lastEvent?.done != null
      ? `Researching… (${lastEvent.done}/${lastEvent.total})`
      : "Research in progress…"
    : status === "complete"
      ? "Re-research"
      : "Research requirements";

  return (
    <div
      className="border-t border-edge bg-bg/70 px-5 py-2"
      data-tour="research-drawer"
    >
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
        <Tip tip={startTip} className="shrink-0">
          <button
            className={`rounded-md border bg-raised px-2 py-0.5 text-[11px] transition-colors disabled:pointer-events-none ${
              running
                ? "border-accent/40 text-ink-dim"
                : "border-edge text-ink-dim hover:border-accent hover:text-accent disabled:opacity-40"
            }`}
            onClick={onStart}
            disabled={startDisabled}
            data-tour="research-start"
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
              Any progress made so far will be lost
            </strong>{" "}
            — completed dimensions won&apos;t be saved, and you&apos;ll need
            to start over.
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

      {expanded && (
        <div className="mt-1.5">
          <ProjectProfileForm doc={doc} busy={busy} onEditDoc={onEditDoc} />
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
