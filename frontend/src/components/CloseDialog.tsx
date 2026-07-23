import { useEffect } from "react";

interface Props {
  open: boolean;
  /** Save (write a project file), then run the caller's follow-up. */
  onSave: () => void;
  /** Proceed without saving. */
  onDiscard: () => void;
  /** Stay put — cancel the action. */
  onCancel: () => void;
  /** Heading (defaults to the window-close wording). */
  title?: string;
  /** Body copy (defaults to the window-close wording). */
  body?: string;
  /** Primary (save) button label. */
  saveLabel?: string;
  /** Discard button label. */
  discardLabel?: string;
}

/**
 * A three-way "save before you lose this?" prompt: Save · Discard · Cancel.
 * Two callers, same shape:
 *  - the native window-close (defaults) — the pywebview shell vetoes the close
 *    and calls `window.buildaspecRequestClose`; the choice returns through its
 *    `js_api` (save_and_close / discard_and_close) or cancels (stay);
 *  - the in-app New-session / Open-project gate (custom copy) — Save writes a
 *    file then proceeds, Discard proceeds unsaved, Cancel keeps the session.
 * Escape always cancels (safest — never lose work on a stray keypress).
 */
export default function CloseDialog({
  open,
  onSave,
  onDiscard,
  onCancel,
  title = "Save progress before leaving?",
  body = "You have unsaved progress in this session. Save it to a project file so you can reopen it later, or close without saving.",
  saveLabel = "Save & close",
  discardLabel = "Close without saving",
}: Props) {
  // Escape cancels the close (safest — never lose work on a stray keypress).
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 p-6"
      onClick={onCancel}
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div
        className="w-full max-w-md rounded-2xl border border-edge bg-surface shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-6 pt-5 pb-4">
          <h2 className="font-[family-name:var(--font-display)] text-lg font-semibold text-ink">
            {title}
          </h2>
          <p className="mt-2 text-sm leading-relaxed text-ink-dim">{body}</p>
        </div>
        <div className="flex items-center justify-end gap-2 border-t border-edge px-6 py-3">
          <button
            onClick={onCancel}
            className="rounded-lg px-3 py-1.5 text-sm text-ink-dim transition-colors hover:text-ink"
          >
            Cancel
          </button>
          <button
            onClick={onDiscard}
            className="rounded-lg border border-edge bg-raised px-3 py-1.5 text-sm text-ink-dim transition-colors hover:border-err hover:text-err"
          >
            {discardLabel}
          </button>
          <button
            onClick={onSave}
            autoFocus
            className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover"
          >
            {saveLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
