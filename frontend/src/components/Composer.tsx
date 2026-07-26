import { useEffect, useRef, useState } from "react";

interface Props {
  disabled: boolean;
  onSend: (text: string) => void;
  /** Stop the in-flight turn (shown in place of Send while streaming). */
  onStop: () => void;
  /** External prefill (WI2 "Ask model"): sets the text and focuses. The
   *  nonce fires the effect even when the same text is requested twice. */
  prefill?: { text: string; nonce: number };
  /** A master import / project open is in flight. A turn started now would
   *  make the upload fail its own guard once it finished parsing, so sending
   *  is held — say so rather than swallowing the click. Distinct from
   *  `disabled`, which means a turn is streaming and offers Stop. */
  uploading?: boolean;
}

export default function Composer({
  disabled,
  onSend,
  onStop,
  prefill,
  uploading = false,
}: Props) {
  const [value, setValue] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  // Auto-grow up to ~9 lines.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 220)}px`;
  }, [value]);

  // Prefill the composer from a review-queue "Ask model" and drop the caret
  // at the end so the user just types what to change.
  useEffect(() => {
    if (!prefill || prefill.nonce === 0) return;
    setValue(prefill.text);
    const el = ref.current;
    if (el) {
      el.focus();
      requestAnimationFrame(() => {
        const end = el.value.length;
        el.setSelectionRange(end, end);
      });
    }
  }, [prefill?.nonce]); // eslint-disable-line react-hooks/exhaustive-deps

  const send = () => {
    const text = value.trim();
    if (!text || disabled || uploading) return;
    setValue("");
    onSend(text);
  };

  return (
    <div
      className="border-t border-edge bg-surface p-4"
      data-tour="composer"
      data-capability="chat.interview"
    >
      <div className="flex items-end gap-2 rounded-2xl border border-edge bg-bg p-2 focus-within:border-accent/70">
        <textarea
          data-capability="chat.web-verify"
          ref={ref}
          rows={1}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          disabled={uploading}
          placeholder={
            uploading
              ? "Reading the file… sending is held until it finishes."
              : "Describe the project, or answer the last question… (Enter to send)"
          }
          className="max-h-[220px] flex-1 resize-none bg-transparent px-2 py-1.5 text-[0.925rem] leading-relaxed outline-none placeholder:text-ink-faint disabled:opacity-60"
        />
        {disabled ? (
          <button
            onClick={onStop}
            data-capability="chat.stop"
            title="Stop generating"
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent text-white transition-colors hover:bg-accent-hover"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
              <rect x="5" y="5" width="14" height="14" rx="2.5" />
            </svg>
          </button>
        ) : (
          <button
            onClick={send}
            disabled={!value.trim() || uploading}
            title={uploading ? "Waiting for the file to finish loading" : "Send"}
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent text-white transition-colors hover:bg-accent-hover disabled:opacity-30"
          >
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.4"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M12 19V5" />
              <path d="m5 12 7-7 7 7" />
            </svg>
          </button>
        )}
      </div>
      <p className="mt-2 text-center text-[11px] text-ink-faint">
        Build-a-Spec drafts are advisory and require review by a licensed
        design professional.
      </p>
    </div>
  );
}
