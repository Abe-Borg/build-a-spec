/**
 * Shared modal shell + button class strings (Batch 9): extracted verbatim
 * from OnboardingOverlay so dialogs outside the tour (the session-start
 * module picker) reuse the exact same shell. Behavior unchanged: z-[70],
 * backdrop click closes, title + ✕ header, optional `wide`.
 */
import { type ReactNode } from "react";

/** The app's standard modal shell (SettingsPanel/HelpModal conventions). */
export function ModalShell({
  title,
  onClose,
  children,
  wide,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
}) {
  return (
    <div
      className="fixed inset-0 z-[70] flex items-start justify-center bg-black/50 p-6 pt-24"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
    >
      <div
        className={
          "w-full rounded-2xl border border-edge bg-surface shadow-2xl " +
          (wide ? "max-w-lg" : "max-w-md")
        }
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-edge px-5 py-3">
          <h2 className="font-[family-name:var(--font-display)] text-base font-semibold">
            {title}
          </h2>
          <button
            onClick={onClose}
            aria-label="Close"
            className="rounded-md px-2 py-0.5 text-ink-dim transition-colors hover:bg-raised hover:text-ink"
          >
            ✕
          </button>
        </div>
        <div className="px-5 py-4">{children}</div>
      </div>
    </div>
  );
}

const primaryBtn =
  "rounded-lg bg-accent px-3.5 py-1.5 text-sm text-white transition-colors " +
  "hover:bg-accent-hover disabled:pointer-events-none disabled:opacity-40";
const quietBtn =
  "rounded-lg border border-edge bg-raised px-3.5 py-1.5 text-sm text-ink " +
  "transition-colors hover:border-accent hover:text-accent " +
  "disabled:pointer-events-none disabled:opacity-40";

export { primaryBtn, quietBtn };
