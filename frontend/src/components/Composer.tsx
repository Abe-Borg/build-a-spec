import { useEffect, useRef, useState } from "react";
import {
  adoptPaste,
  movePastedRanges,
  pendingPaste,
  randomPasteId,
  wrapPastedContent,
  type PastedRange,
  type PendingPaste,
} from "../lib/pastedContent";
import { EDIT_SCOPE_OPTIONS, type EditScope } from "../lib/editScope";

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
  /** The section's edit scope (lib/editScope.ts): how far the assistant may
   *  change the specification on its own. Shown in the composer because it
   *  governs every turn sent from here. The change is saved on the server;
   *  a refusal rejects, and the composer says why. */
  editScope: EditScope;
  onChangeEditScope: (scope: EditScope) => Promise<void>;
  /** A guided tour is running: the practice copy is not the user's section,
   *  so the switch shows the mode but cannot change it. */
  editScopeLocked?: boolean;
}

export default function Composer({
  disabled,
  onSend,
  onStop,
  prefill,
  uploading = false,
  editScope,
  onChangeEditScope,
  editScopeLocked = false,
}: Props) {
  const [value, setValue] = useState("");
  const [scopeSaving, setScopeSaving] = useState(false);
  const [scopeError, setScopeError] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);
  // Pastes worth marking, recorded by position (lib/pastedContent.ts): the
  // send wraps each in <pasted_content> tags so the model knows the text came
  // from somewhere else. Refs, not state — they never change what renders.
  // `valueRef` is the value the ranges describe, so an edit is always
  // measured against the value it changed.
  const rangesRef = useRef<PastedRange[]>([]);
  const pendingRef = useRef<PendingPaste | null>(null);
  const valueRef = useRef("");

  /** Replace the whole value in code (a prefill, a sent message): every
   *  recorded paste goes with the text it described. */
  const replaceValue = (text: string) => {
    rangesRef.current = [];
    pendingRef.current = null;
    valueRef.current = text;
    setValue(text);
  };

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
    replaceValue(prefill.text);
    const el = ref.current;
    if (el) {
      el.focus();
      requestAnimationFrame(() => {
        const end = el.value.length;
        el.setSelectionRange(end, end);
      });
    }
  }, [prefill?.nonce]); // eslint-disable-line react-hooks/exhaustive-deps

  const chooseScope = (next: EditScope) => {
    if (next === editScope || scopeSaving) return;
    setScopeSaving(true);
    setScopeError("");
    onChangeEditScope(next)
      .catch((failure: unknown) =>
        setScopeError(failure instanceof Error ? failure.message : String(failure)),
      )
      .finally(() => setScopeSaving(false));
  };

  const send = () => {
    const current = valueRef.current;
    if (!current.trim() || disabled || uploading) return;
    // The model gets the pastes marked; the bubble strips the marks again
    // (MessageBubble renders stripPastedContentTags), so the chat shows the
    // message exactly as it was typed.
    const text = wrapPastedContent(current, rangesRef.current, randomPasteId);
    replaceValue("");
    onSend(text);
  };

  return (
    <div
      className="border-t border-edge bg-surface p-4"
      data-tour="composer"
      data-capability="chat.interview"
    >
      <div className="flex flex-col gap-1.5 rounded-2xl border border-edge bg-bg p-2 focus-within:border-accent/70">
        <textarea
          data-capability="chat.web-verify"
          ref={ref}
          rows={1}
          value={value}
          onPaste={(e) => {
            // Applied by the change that follows; recorded now, where the
            // selection still says where the paste lands.
            pendingRef.current = pendingPaste(
              e.currentTarget.selectionStart ?? 0,
              e.clipboardData.getData("text"),
            );
          }}
          onChange={(e) => {
            const next = e.target.value;
            const moved = movePastedRanges(rangesRef.current, valueRef.current, next);
            rangesRef.current = adoptPaste(moved, pendingRef.current, next);
            pendingRef.current = null;
            valueRef.current = next;
            setValue(next);
          }}
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
          className="max-h-[220px] w-full resize-none bg-transparent px-2 py-1.5 text-[0.925rem] leading-relaxed outline-none placeholder:text-ink-faint disabled:opacity-60"
        />
        <div className="flex items-center justify-between gap-2">
          <div
            role="radiogroup"
            aria-label="Edit scope"
            data-capability="chat.edit-scope"
            className="flex items-center gap-0.5 rounded-full border border-edge bg-surface p-0.5"
            title={
              editScopeLocked
                ? "The tour shows the mode; change it in your own section."
                : disabled
                  ? "The edit scope applies from the next message — finish or stop this turn first."
                  : "How far the assistant may change this section on its own. Saved with the section; Next section carries it."
            }
          >
            <span className="pl-2 pr-1 text-[10px] font-medium uppercase tracking-wide text-ink-faint">
              Edits
            </span>
            {EDIT_SCOPE_OPTIONS.map((option) => {
              const active = option.id === editScope;
              return (
                <button
                  key={option.id}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  disabled={disabled || editScopeLocked || scopeSaving}
                  onClick={() => chooseScope(option.id)}
                  title={option.detail}
                  className={`whitespace-nowrap rounded-full px-2.5 py-0.5 text-[11px] transition-colors disabled:opacity-50 ${
                    active
                      ? option.id === "essentials"
                        ? "bg-accent/20 font-medium text-accent"
                        : "bg-raised font-medium text-ink"
                      : "text-ink-dim hover:text-ink"
                  }`}
                >
                  {option.id === "essentials" ? "🔒 " : ""}
                  {option.label}
                </button>
              );
            })}
          </div>
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
      </div>
      {scopeError ? (
        <p className="mt-2 text-center text-[11px] text-err" role="alert">
          {scopeError}
        </p>
      ) : (
        <p className="mt-2 text-center text-[11px] text-ink-faint">
          Build-a-Spec drafts are advisory and require review by a licensed
          design professional.
        </p>
      )}
    </div>
  );
}
