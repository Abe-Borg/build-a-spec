import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChatMessage } from "../types";

export default function MessageBubble({ msg }: { msg: ChatMessage }) {
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

  return (
    <div
      className={`md text-[0.925rem] ${msg.streaming ? "streaming-caret" : ""}`}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]}>
        {msg.text || (msg.streaming ? "" : "…")}
      </ReactMarkdown>
    </div>
  );
}
