import { useCallback, useEffect, useRef, useState } from "react";
import type { ChatMessage, Health } from "./types";
import { getHealth, resetSession, streamChat } from "./lib/api";
import Header from "./components/Header";
import ApiKeyBanner from "./components/ApiKeyBanner";
import Chat from "./components/Chat";
import ArtifactPanel from "./components/ArtifactPanel";

let nextId = 0;
const newId = () => `m${++nextId}`;

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);

  const refreshHealth = useCallback(() => {
    getHealth()
      .then(setHealth)
      .catch(() => setHealth(null));
  }, []);

  useEffect(() => {
    refreshHealth();
  }, [refreshHealth]);

  const updateLast = (patch: Partial<ChatMessage>) => {
    setMessages((prev) => {
      const next = [...prev];
      next[next.length - 1] = { ...next[next.length - 1], ...patch };
      return next;
    });
  };

  const appendToLast = (delta: string) => {
    setMessages((prev) => {
      const next = [...prev];
      const last = next[next.length - 1];
      next[next.length - 1] = { ...last, text: last.text + delta };
      return next;
    });
  };

  const send = async (text: string) => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setMessages((prev) => [
      ...prev,
      { id: newId(), role: "user", text },
      { id: newId(), role: "assistant", text: "", streaming: true },
    ]);

    try {
      for await (const evt of streamChat(text)) {
        if (evt.type === "text_delta") {
          appendToLast(evt.text);
        } else if (evt.type === "error") {
          updateLast({ text: evt.message, error: true, streaming: false });
        } else if (evt.type === "turn_complete") {
          updateLast({ streaming: false });
        }
      }
    } catch (e) {
      updateLast({
        text: e instanceof Error ? e.message : String(e),
        error: true,
      });
    } finally {
      updateLast({ streaming: false });
      busyRef.current = false;
      setBusy(false);
    }
  };

  const newSession = async () => {
    await resetSession();
    setMessages([]);
  };

  return (
    <div className="flex h-full flex-col">
      <Header health={health} onNewSession={newSession} />
      {health && !health.api_key_present && (
        <ApiKeyBanner onSaved={refreshHealth} />
      )}
      <main className="flex min-h-0 flex-1">
        <Chat messages={messages} busy={busy} onSend={send} />
        <ArtifactPanel />
      </main>
    </div>
  );
}
