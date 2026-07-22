import { memo, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChatMessage, Figure } from "../types";
import { splitStableTail, useSmoothText } from "../lib/useSmoothText";
import FigureCard from "./FigureCard";
import StatusStrip from "./StatusStrip";

/** Figures the model created in this turn, rendered inline beneath the text.
 *  Ids that no longer resolve (e.g. a rolled-back turn) are simply skipped. */
function AttachedFigures({
  ids,
  figuresById,
  onDelete,
}: {
  ids?: string[];
  figuresById?: Map<string, Figure>;
  onDelete?: (fid: string) => void;
}) {
  if (!ids?.length || !figuresById) return null;
  const figures = ids
    .map((id) => figuresById.get(id))
    .filter((f): f is Figure => !!f);
  if (!figures.length) return null;
  return (
    <>
      {figures.map((figure) => (
        <FigureCard key={figure.fid} figure={figure} onDelete={onDelete} />
      ))}
    </>
  );
}

/** Memoized markdown for the settled prefix — re-parses only when the prefix
 *  grows past another paragraph break, not on every animation frame. */
const StablePrefix = memo(function StablePrefix({ text }: { text: string }) {
  if (!text) return null;
  return <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>;
});

/** Collapsible adaptive-thinking summary, muted + italic, distinct from the
 *  answer. Auto-expands while the model thinks with nothing written yet, then
 *  collapses once real text starts (still expandable afterward). */
function ThinkingBlock({
  text,
  autoExpand,
}: {
  text: string;
  autoExpand: boolean;
}) {
  const [open, setOpen] = useState(autoExpand);
  const wasAuto = useRef(autoExpand);
  useEffect(() => {
    if (wasAuto.current && !autoExpand) setOpen(false);
    wasAuto.current = autoExpand;
  }, [autoExpand]);

  return (
    <div className="mb-2">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1 text-[11px] font-medium tracking-wide text-ink-faint uppercase transition-colors hover:text-ink-dim"
      >
        <span className="text-[9px]">{open ? "▾" : "▸"}</span>
        Thinking
      </button>
      {open && (
        <div className="mt-1 border-l-2 border-edge pl-3 text-[0.82rem] leading-relaxed text-ink-faint italic whitespace-pre-wrap">
          {text}
        </div>
      )}
    </div>
  );
}

export default function MessageBubble({
  msg,
  figuresById,
  onDeleteFigure,
}: {
  msg: ChatMessage;
  figuresById?: Map<string, Figure>;
  onDeleteFigure?: (fid: string) => void;
}) {
  const streaming = !!msg.streaming;
  const smoothed = useSmoothText(msg.text, streaming);

  // A workflow-event note (research / Final QC started): a compact, muted,
  // centered marker — clearly an event, not the model speaking, and quiet
  // enough to not read as chat noise.
  if (msg.note) {
    return (
      <div className="flex justify-center">
        <span className="rounded-full border border-edge bg-surface/50 px-3 py-1 text-[11px] text-ink-faint">
          {msg.text}
        </span>
      </div>
    );
  }

  if (msg.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-md border border-edge bg-raised px-4 py-2.5 text-[0.925rem] leading-relaxed whitespace-pre-wrap">
          {msg.text}
        </div>
      </div>
    );
  }

  if (msg.error) {
    return (
      <div className="rounded-xl border border-err/40 bg-err/10 px-4 py-3 text-sm text-err">
        {msg.text}
      </div>
    );
  }

  if (streaming) {
    const [prefix, tail] = splitStableTail(smoothed);
    const hasBody = smoothed.length > 0;
    return (
      <div className="md text-[0.925rem]">
        {msg.thinking && (
          <ThinkingBlock text={msg.thinking} autoExpand={!hasBody} />
        )}
        {msg.status && <StatusStrip status={msg.status} />}
        <StablePrefix text={prefix} />
        {tail ? (
          <span className="streaming-caret whitespace-pre-wrap">{tail}</span>
        ) : (
          !hasBody && !msg.status && !msg.thinking && (
            <span className="streaming-caret" />
          )
        )}
        <AttachedFigures
          ids={msg.figureIds}
          figuresById={figuresById}
          onDelete={onDeleteFigure}
        />
      </div>
    );
  }

  return (
    <div className="md text-[0.925rem]">
      {msg.thinking && <ThinkingBlock text={msg.thinking} autoExpand={false} />}
      <ReactMarkdown remarkPlugins={[remarkGfm]}>
        {msg.text || "…"}
      </ReactMarkdown>
      <AttachedFigures
        ids={msg.figureIds}
        figuresById={figuresById}
        onDelete={onDeleteFigure}
      />
    </div>
  );
}
