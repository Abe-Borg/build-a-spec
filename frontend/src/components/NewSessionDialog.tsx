import { useEffect } from "react";
import { ModalShell, primaryBtn, quietBtn } from "./ModalShell";

interface Props {
  open: boolean;
  busy: boolean;
  onCancel: () => void;
  onBlankSlate: () => void;
}

export default function NewSessionDialog({
  open,
  busy,
  onCancel,
  onBlankSlate,
}: Props) {
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  if (!open) return null;

  return (
    <ModalShell title="Start a new session" onClose={onCancel} wide>
      <p className="text-sm leading-relaxed text-ink-dim">
        Choose how you want to begin. Starting a new session clears the
        current chat and document.
      </p>

      <div className="mt-4 grid gap-2">
        <button
          type="button"
          onClick={onBlankSlate}
          disabled={busy}
          className={primaryBtn + " w-full py-2.5 text-left"}
        >
          Start with a blank slate
        </button>
        <button
          type="button"
          disabled
          className={quietBtn + " w-full py-2.5 text-left"}
        >
          Start from a template
        </button>
        <button
          type="button"
          disabled
          className={quietBtn + " w-full py-2.5 text-left"}
        >
          Load your own template
        </button>
      </div>

      <p className="mt-2 text-[11px] leading-snug text-ink-faint">
        Template features are in development and will be available in a future
        update.
      </p>

      <div className="mt-4">
        <button type="button" onClick={onCancel} className={quietBtn}>
          Cancel
        </button>
      </div>
    </ModalShell>
  );
}
