import type { Health } from "../types";

interface Props {
  health: Health | null;
  busy: boolean;
  onNewSession: () => void;
}

export default function Header({ health, busy, onNewSession }: Props) {
  return (
    <header className="flex items-center justify-between border-b border-edge bg-surface px-5 py-3">
      <div className="flex items-baseline gap-3">
        <h1 className="font-[family-name:var(--font-display)] text-lg font-semibold tracking-tight">
          Build-a-Spec
        </h1>
        <span className="text-xs text-ink-dim">
          Division 21 — Hyperscale Fire Suppression
        </span>
      </div>
      <div className="flex items-center gap-3">
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
