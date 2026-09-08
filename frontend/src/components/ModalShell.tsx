/**
 * Shared modal shell + button class strings (Batch 10): extracted verbatim
 * from OnboardingOverlay so dialogs outside the tour (including new-session
 * choices) reuse the exact same shell. Behavior unchanged: z-[70],
 * backdrop click closes, title + ✕ header, optional `wide`.
 */
import { useRef, type ReactNode } from "react";
import { useDialogFocus } from "../lib/dialogFocus";

/**
 * The app's standard modal shell (SettingsPanel/HelpModal conventions).
 *
 * `marker` stamps `data-dialog` on the dialog root so a global key handler can
 * tell its OWN modal apart from one stacked over it — see the guided tour's
 * Escape handling in OnboardingOverlay. Purely an identifier; it changes
 * nothing for a consumer that omits it.
 *
 * Keyboard: `useDialogFocus` (Escape, Tab containment, focus restoration —
 * the same hook every other dialog uses). Escape calls `onEscape` when given,
 * else `onClose`; a consumer whose Escape means "go back a level first" (the
 * New-session dialog's template browser) passes the former. Initial focus
 * lands on the panel itself, not a button, so Enter does nothing a stray
 * keypress would regret.
 */
export function ModalShell({
  title,
  onClose,
  onEscape,
  children,
  wide,
  marker,
}: {
  title: string;
  onClose: () => void;
  onEscape?: () => void;
  children: ReactNode;
  wide?: boolean;
  marker?: string;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  // Always open: every consumer renders the shell conditionally.
  useDialogFocus(true, panelRef, panelRef, onEscape ?? onClose);
  return (
    <div
      className="fixed inset-0 z-[70] flex items-start justify-center bg-black/50 p-6 pt-24"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      data-dialog={marker}
    >
      <div
        ref={panelRef}
        tabIndex={-1}
        className={
          "w-full rounded-2xl border border-edge bg-surface shadow-2xl outline-none " +
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
