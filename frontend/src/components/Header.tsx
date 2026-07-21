import type { Health, UpdateCheckPayload } from "../types";

interface Props {
  health: Health | null;
  busy: boolean;
  update: UpdateCheckPayload | null;
  onNewSession: () => void;
  onInstallUpdate: () => void;
}

export default function Header({
  health,
  busy,
  update,
  onNewSession,
  onInstallUpdate,
}: Props) {
  const updateAvailable =
    update?.status === "UPDATE_AVAILABLE" && !!update.version;
  return (
    <header className="flex items-center justify-between border-b border-edge bg-surface px-5 py-3">
      <div className="flex items-baseline gap-3">
        <h1 className="font-[family-name:var(--font-display)] text-lg font-semibold tracking-tight">
          Build-a-Spec
        </h1>
        <span className="text-xs text-ink-dim">
          {health?.module ?? "Division 21 — Hyperscale Fire Suppression"}
        </span>
      </div>
      <div className="flex items-center gap-3">
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
          className="rounded-lg border border-edge bg-raised px-3 py-1.5 text-xs text-ink transition-colors hover:border-accent hover:text-accent disabled:pointer-events-none disabled:opacity-40"
        >
          New session
        </button>
      </div>
    </header>
  );
}
