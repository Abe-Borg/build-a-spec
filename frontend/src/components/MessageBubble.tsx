import { memo, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChatMessage } from "../types";
import { splitStableTail, useSmoothText } from "../lib/useSmoothText";
import StatusStrip from "./StatusStrip";

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

export default function MessageBubble({ msg }: { msg: ChatMessage }) {
  const streaming = !!msg.streaming;
  const smoothed = useSmoothText(msg.text, streaming);

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
      </div>
    );
  }

  return (
    <div className="md text-[0.925rem]">
      {msg.thinking && <ThinkingBlock text={msg.thinking} autoExpand={false} />}
      <ReactMarkdown remarkPlugins={[remarkGfm]}>
        {msg.text || "…"}
      </ReactMarkdown>
    </div>
  );
}
