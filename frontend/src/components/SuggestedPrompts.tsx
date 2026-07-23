interface Props {
  prompts: string[];
  busy: boolean;
  /**
   * Send-immediately (the starter-chip pattern): the chip label IS the
   * message. For a prefill-composer variant instead, swap this one call site
   * for the App-owned prefill setter — the {text, nonce} plumbing exists.
   */
  onSend: (text: string) => void;
}

/**
 * One-tap reply chips staged by the model each turn (Batch 9). Rendered
 * between the chat scroll region and the composer; hidden entirely when the
 * model staged nothing (its way of winding the bar down as a section nears
 * issue-ready). Chips are disabled while a turn streams.
 */
export default function SuggestedPrompts({ prompts, busy, onSend }: Props) {
  if (prompts.length === 0) return null;
  return (
    <div role="group" aria-label="Suggested replies" className="px-5 pb-2">
      <div className="mx-auto flex max-w-3xl flex-wrap items-center gap-2">
        <span aria-hidden className="text-xs text-ink-faint">
          ✦
        </span>
        {prompts.map((p) => (
          <button
            key={p}
            type="button"
            onClick={() => onSend(p)}
            disabled={busy}
            title={p}
            className="prompt-chip-in max-w-full truncate rounded-full border border-accent/50 bg-accent/10 px-3 py-1 text-xs text-ink transition-colors hover:border-accent hover:bg-accent/15 disabled:pointer-events-none disabled:opacity-40"
          >
            {p}
          </button>
        ))}
      </div>
    </div>
  );
}
