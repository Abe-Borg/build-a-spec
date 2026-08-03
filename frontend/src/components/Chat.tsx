import { useEffect, useRef, useState } from "react";
import type { ChatMessage, Figure } from "../types";
import { starterPrompts } from "../lib/tour";
import {
  consumeTutorialUpdateInvitation,
  hasCompletedOnboarding,
} from "../lib/onboardingStorage";
import MessageBubble from "./MessageBubble";
import Composer from "./Composer";
import SuggestedPrompts from "./SuggestedPrompts";

interface Props {
  messages: ChatMessage[];
  busy: boolean;
  onSend: (text: string) => void;
  /** Model-staged reply chips (Batch 9), shown above the composer. */
  suggestions: string[];
  /** Active discipline (generic open-catalog sessions) — tailors starter chips. */
  discipline?: string;
  /** Start the full interactive guided tutorial — the onboarding starter chip. */
  onStartOnboarding: () => void;
  /**
   * A guided tutorial is already running. The tutorial's own blank-page
   * chapter puts these chips on screen, so the launcher among them would
   * otherwise invite a click that restarts the tour the user is taking.
   */
  tourActive?: boolean;
  /** Stop the in-flight turn, forwarded to the composer. */
  onStop: () => void;
  /** A file upload is in flight; the composer holds sending and says so. */
  uploading?: boolean;
  /** WI2 "Ask model" prefill, forwarded to the composer. */
  prefill?: { text: string; nonce: number };
  /** Session figures, keyed by id, for inline rendering in the bubbles. */
  figuresById?: Map<string, Figure>;
  /** Remove a figure (forwarded to each figure card). */
  onDeleteFigure?: (fid: string) => void;
}

export default function Chat({
  messages,
  busy,
  onSend,
  suggestions,
  discipline,
  onStartOnboarding,
  tourActive,
  onStop,
  uploading,
  prefill,
  figuresById,
  onDeleteFigure,
}: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const pinnedRef = useRef(true);

  // Stay pinned to the bottom on new messages unless the user scrolled up.
  // The suggestions bar appearing/growing shrinks the scroll viewport, so
  // re-pin on it too (respecting pinnedRef — never yanking a reader).
  useEffect(() => {
    const el = scrollRef.current;
    if (el && pinnedRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages, suggestions]);

  // While a turn streams, the smoothed text grows between message updates
  // (via requestAnimationFrame inside the bubble), so follow the bottom on
  // every frame — but only while pinned, never yanking scroll from a reader.
  useEffect(() => {
    if (!busy) return;
    let raf = requestAnimationFrame(function follow() {
      const el = scrollRef.current;
      if (el && pinnedRef.current) el.scrollTop = el.scrollHeight;
      raf = requestAnimationFrame(follow);
    });
    return () => cancelAnimationFrame(raf);
  }, [busy]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    pinnedRef.current =
      el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };

  const toured = hasCompletedOnboarding();
  const [tutorialUpdated] = useState(consumeTutorialUpdateInvitation);

  return (
    <section
      className="flex min-w-[420px] flex-1 basis-[46%] flex-col border-r border-edge"
      data-tour="chat-pane"
      data-capability="chat.streaming"
    >
      <div
        ref={scrollRef}
        onScroll={onScroll}
        style={{ overflowAnchor: "none" }}
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
              shape. Or start from one of these:
            </p>
            <div
              className="mt-6 flex w-full max-w-md flex-col gap-2 text-left"
              data-tour="starter-prompts"
              data-capability="session.starters"
            >
              {starterPrompts(discipline).map((p) =>
                p.kind === "onboarding" ? (
                  <button
                    key={p.label}
                    onClick={onStartOnboarding}
                    disabled={busy || tourActive}
                    className={`rounded-xl border border-accent/50 bg-accent/10 px-4 py-2.5 transition-colors hover:border-accent hover:bg-accent/15 disabled:pointer-events-none disabled:opacity-40 ${
                      tourActive || (toured && !tutorialUpdated)
                        ? ""
                        : "chip-pulse"
                    }`}
                  >
                    <span className="block text-sm text-ink">
                      🧭 {p.label}
                    </span>
                    <span className="mt-0.5 block text-[11px] text-ink-faint">
                      {tourActive
                        ? "You are taking it right now"
                        : tutorialUpdated
                          ? "Full tutorial updated — see every feature"
                          : toured
                            ? "Take the full interactive tutorial again"
                            : "Full interactive tutorial · uses an actual spec"}
                    </span>
                  </button>
                ) : (
                  <button
                    key={p.label}
                    onClick={() => onSend(p.label)}
                    disabled={busy}
                    className="rounded-xl border border-edge bg-surface px-4 py-2.5 transition-colors hover:border-accent/70 hover:bg-raised disabled:pointer-events-none disabled:opacity-40"
                  >
                    <span className="block text-sm leading-snug text-ink-dim">
                      {p.label}
                    </span>
                    {p.sub && (
                      <span className="mt-0.5 block text-[11px] text-ink-faint">
                        {p.sub}
                      </span>
                    )}
                  </button>
                ),
              )}
            </div>
          </div>
        ) : (
          <div
            className="mx-auto flex max-w-3xl flex-col gap-5"
            data-capability="figure.create"
          >
            {messages.map((m) => (
              <MessageBubble
                key={m.id}
                msg={m}
                figuresById={figuresById}
                onDeleteFigure={onDeleteFigure}
              />
            ))}
          </div>
        )}
      </div>
      <SuggestedPrompts
        prompts={suggestions}
        busy={busy}
        onSend={onSend}
      />
      <Composer
        disabled={busy}
        onSend={onSend}
        onStop={onStop}
        uploading={uploading}
        prefill={prefill}
      />
    </section>
  );
}
