import type { Health, UpdateCheckPayload, UsageSummary } from "../types";
import { HELP_TOPICS, type HelpTopic } from "./HelpModal";

interface Props {
  health: Health | null;
  busy: boolean;
  update: UpdateCheckPayload | null;
  usage: UsageSummary | null;
  onNewSession: () => void;
  onInstallUpdate: () => void;
  onOpenSettings: () => void;
  onOpenHelp: (topic: HelpTopic) => void;
  /** Restart the guided tour (Batch 6) — same entry guard as the chip. */
  onStartTour: () => void;
}

export default function Header({
  health,
  busy,
  update,
  usage,
  onNewSession,
  onInstallUpdate,
  onOpenSettings,
  onOpenHelp,
  onStartTour,
}: Props) {
  const spend = usage?.estimated_cost_usd.total ?? 0;
  const spendLabel = spend > 0 ? `≈ $${spend.toFixed(2)}` : "—";
  const updateAvailable =
    update?.status === "UPDATE_AVAILABLE" && !!update.version;
  return (
    <header className="flex items-center gap-4 border-b border-edge bg-surface px-5 py-3">
      <div className="flex flex-none items-baseline gap-3">
        <h1 className="font-[family-name:var(--font-display)] text-lg font-semibold tracking-tight">
          Build-a-Spec
        </h1>
        <span className="hidden text-xs text-ink-dim xl:inline">
          {health?.discipline
            ? `Generic — ${health.discipline}`
            : (health?.module ?? "Division 21 — Hyperscale Fire Suppression")}
        </span>
      </div>
      <span className="h-5 w-px flex-none bg-edge" aria-hidden="true" />
      <nav className="flex flex-none items-center gap-0.5">
        {HELP_TOPICS.map((t) => (
          <button
            key={t.id}
            onClick={() => onOpenHelp(t.id)}
            className="rounded-md px-2.5 py-1 text-xs text-ink-dim transition-colors hover:bg-raised hover:text-ink"
          >
            {t.label}
          </button>
        ))}
        <button
          onClick={onStartTour}
          title="Guided tour of the whole workflow, on a live demo spec"
          className="rounded-md px-2.5 py-1 text-xs text-ink-dim transition-colors hover:bg-raised hover:text-ink"
        >
          Tour
        </button>
      </nav>
      <div className="flex-1" />
      <div className="flex flex-none items-center gap-3">
        {updateAvailable &&
          (update?.platform_supported ? (
            <button
              onClick={onInstallUpdate}
              className="rounded-full border border-accent/60 bg-accent/15 px-3 py-1 text-xs text-accent transition-colors hover:bg-accent/25"
              title={update?.notes || "Download and install the update"}
            >
              v{update?.version} available — install
            </button>
          ) : (
            <a
              href={update?.releases_url}
              target="_blank"
              rel="noreferrer"
              className="rounded-full border border-accent/60 bg-accent/15 px-3 py-1 text-xs text-accent transition-colors hover:bg-accent/25"
              title="Open the releases page"
            >
              v{update?.version} available
            </a>
          ))}
        <button
          onClick={onOpenSettings}
          title="Estimated spend this session — click for the breakdown"
          data-tour="spend-pill"
          className="rounded-full border border-edge bg-raised px-3 py-1 text-xs text-ink-dim tabular-nums transition-colors hover:border-accent hover:text-accent"
        >
          {spendLabel}
          <span className="ml-1 text-ink-faint">this session</span>
        </button>
        {health && (
          <span className="flex items-center gap-2 rounded-full border border-edge bg-raised px-3 py-1 text-xs text-ink-dim">
            <span
              className={`h-2 w-2 rounded-full ${
                health.api_key_present ? "bg-ok" : "bg-warn"
              }`}
            />
            {health.model}
          </span>
        )}
        <button
          onClick={onNewSession}
          disabled={busy}
          data-tour="new-session"
          className="rounded-lg border border-edge bg-raised px-3 py-1.5 text-xs text-ink transition-colors hover:border-accent hover:text-accent disabled:pointer-events-none disabled:opacity-40"
        >
          New session
        </button>
        <button
          onClick={onOpenSettings}
          title="Settings"
          aria-label="Settings"
          data-tour="settings"
          className="flex h-8 w-8 items-center justify-center rounded-lg border border-edge bg-raised text-ink-dim transition-colors hover:border-accent hover:text-accent"
        >
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
          </svg>
        </button>
      </div>
    </header>
  );
}
