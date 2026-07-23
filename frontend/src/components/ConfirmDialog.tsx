import { useEffect, type ReactNode } from "react";

interface Props {
  open: boolean;
  title: string;
  body: ReactNode;
  confirmLabel: string;
  cancelLabel?: string;
  /** Red confirm button, for destructive/lossy actions (e.g. stopping a run). */
  danger?: boolean;
  /** Render above the guided-tour overlay (z-80) instead of the default z-60. */
  elevated?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * Small, generic "are you sure" dialog — backdrop click / Escape both cancel,
 * matching CloseDialog's pattern. Used for confirmations whose body is a
 * sentence or two (stopping research/QC); the more elaborate Final-QC launch
 * confirmation stays its own purpose-built modal.
 */
export default function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel,
  cancelLabel = "Cancel",
  danger = false,
  elevated = false,
  onConfirm,
  onCancel,
}: Props) {
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
      className={
        "fixed inset-0 flex items-center justify-center bg-black/50 p-6 " +
        (elevated ? "z-[80]" : "z-[60]")
      }
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
          <div className="mt-2 text-sm leading-relaxed text-ink-dim">{body}</div>
        </div>
        <div className="flex items-center justify-end gap-2 border-t border-edge px-6 py-3">
          <button
            onClick={onCancel}
            className="rounded-lg px-3 py-1.5 text-sm text-ink-dim transition-colors hover:text-ink"
          >
            {cancelLabel}
          </button>
          <button
            onClick={onConfirm}
            autoFocus
            className={`rounded-lg px-3 py-1.5 text-sm font-medium text-white transition-colors ${
              danger
                ? "bg-err hover:bg-err/85"
                : "bg-accent hover:bg-accent-hover"
            }`}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
