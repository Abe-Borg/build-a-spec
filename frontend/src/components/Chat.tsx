import { useEffect, useRef } from "react";
import type { ChatMessage } from "../types";
import MessageBubble from "./MessageBubble";
import Composer from "./Composer";

interface Props {
  messages: ChatMessage[];
  busy: boolean;
  onSend: (text: string) => void;
}

export default function Chat({ messages, busy, onSend }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const pinnedRef = useRef(true);

  // Stay pinned to the bottom while streaming unless the user scrolled up.
  useEffect(() => {
    const el = scrollRef.current;
    if (el && pinnedRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    pinnedRef.current =
      el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };

  return (
    <section className="flex min-w-[420px] flex-1 basis-[46%] flex-col border-r border-edge">
      <div
        ref={scrollRef}
        onScroll={onScroll}
        className="flex-1 overflow-y-auto px-5 py-6"
      >
        {messages.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center text-center">
            <p className="font-[family-name:var(--font-display)] text-2xl text-ink">
              What are we specifying?
            </p>
            <p className="mt-3 max-w-md text-sm leading-relaxed text-ink-dim">
              Tell me about the project — section, location, client — and
              I&apos;ll interview you through the rest while the spec takes
              shape. For example: “21 13 13 wet-pipe for a hyperscale campus
              in Council Bluffs, Iowa.”
            </p>
          </div>
        ) : (
          <div className="mx-auto flex max-w-3xl flex-col gap-5">
            {messages.map((m) => (
              <MessageBubble key={m.id} msg={m} />
            ))}
          </div>
        )}
      </div>
      <Composer disabled={busy} onSend={onSend} />
    </section>
  );
}
