/**
 * The condensed-conversation divider and its "View summary" sheet
 * (compaction plan Phase 3).
 *
 * Once a conversation outgrows what is worth re-sending every turn, its
 * oldest turns are condensed into a summary that the model is sent instead.
 * The transcript on screen — and in the saved project — is never touched:
 * this divider only marks where the model's view was cut, and the sheet
 * shows exactly what the model now reads for the turns above it.
 */
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { CompactionInfo, CompactionSummary } from "../types";
import { getCompactionSummary } from "../lib/api";
import { compactTokens, condensedTurnsLabel } from "../lib/compaction";
import { ModalShell } from "./ModalShell";

const REMARK_PLUGINS = [remarkGfm];

function condensedOn(createdAt: string): string {
  const day = createdAt.slice(0, 10);
  return day ? ` on ${day}` : "";
}

export function CondensedDivider({
  compaction,
  onView,
}: {
  compaction: CompactionInfo;
  onView: () => void;
}) {
  const turns = condensedTurnsLabel(compaction.covers_turns);
  return (
    <div
      className="flex items-center gap-3 py-1 text-[11px] text-ink-faint"
      data-capability="chat.condensed"
      title={
        `The model is sent a summary of ${turns} instead of their full ` +
        "text, and can still look up a turn's exact words when it needs a " +
        "detail. Nothing was deleted: this transcript and the saved project " +
        `keep every turn. Condensed${condensedOn(compaction.created_at)}.`
      }
    >
      <span className="h-px flex-1 bg-edge" aria-hidden="true" />
      <span className="shrink-0">
        Above: {turns}, condensed for the model
        {" · "}
        <button
          type="button"
          onClick={onView}
          className="text-accent underline-offset-2 hover:underline"
        >
          View summary
        </button>
      </span>
      <span className="h-px flex-1 bg-edge" aria-hidden="true" />
    </div>
  );
}

/**
 * The summary itself, fetched when the sheet opens (the document payload
 * carries only its sizes). A newer summary replacing the one being read
 * re-fetches, so the sheet never shows text the model is no longer sent.
 */
export function CondensedSummaryModal({
  compaction,
  onClose,
}: {
  compaction: CompactionInfo;
  onClose: () => void;
}) {
  const [record, setRecord] = useState<CompactionSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const request = useRef(0);

  useEffect(() => {
    const mine = ++request.current;
    setRecord(null);
    setError(null);
    getCompactionSummary()
      .then((value) => {
        if (request.current === mine) setRecord(value);
      })
      .catch((e: unknown) => {
        if (request.current !== mine) return;
        setError(e instanceof Error ? e.message : String(e));
      });
  }, [compaction.created_at, compaction.covers_turns]);

  const shown = record ?? compaction;
  const turns = condensedTurnsLabel(shown.covers_turns);
  return (
    <ModalShell title="Condensed conversation" onClose={onClose} xwide>
      <p className="text-sm leading-relaxed text-ink-dim">
        The model reads this summary in place of {turns}
        {condensedOn(shown.created_at)} — about{" "}
        {compactTokens(shown.tokens_before)} tokens of conversation became
        about {compactTokens(shown.tokens_after)}. It can still look up the
        exact words of any of those turns when it needs a detail. Nothing was
        deleted: the transcript and the saved project keep every turn.
      </p>
      {shown.trigger === "backstop" && (
        <p className="mt-2 text-xs leading-relaxed text-ink-faint">
          Condensed at the moment a message would not have fit in the
          model&apos;s context window.
        </p>
      )}
      <div className="mt-4 max-h-[60vh] overflow-y-auto rounded-xl border border-edge bg-raised/40 px-4 py-3">
        {error ? (
          <p className="text-sm text-err">Could not load the summary: {error}</p>
        ) : record ? (
          <div className="md text-[0.9rem]">
            <ReactMarkdown remarkPlugins={REMARK_PLUGINS}>
              {record.summary}
            </ReactMarkdown>
          </div>
        ) : (
          <p className="text-sm text-ink-faint">Loading the summary…</p>
        )}
      </div>
    </ModalShell>
  );
}
