import { useCallback, useEffect, useRef, useState } from "react";
import type { ChatMessage, Health, OpenItem, SpecDoc } from "./types";
import {
  getDoc,
  getHealth,
  loadProject,
  redoDoc,
  resetSession,
  streamChat,
  undoDoc,
} from "./lib/api";
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
  const [doc, setDoc] = useState<SpecDoc | null>(null);
  const [openItems, setOpenItems] = useState<OpenItem[]>([]);
  const [changedIds, setChangedIds] = useState<ReadonlySet<string>>(new Set());
  const busyRef = useRef(false);

  const refreshHealth = useCallback(() => {
    getHealth()
      .then(setHealth)
      .catch(() => setHealth(null));
  }, []);

  const refreshDoc = useCallback(() => {
    getDoc()
      .then((payload) => {
        setDoc(payload.doc);
        setOpenItems(payload.open_questions);
      })
      .catch(() => setDoc(null));
  }, []);

  useEffect(() => {
    refreshHealth();
    refreshDoc();
  }, [refreshHealth, refreshDoc]);

  const updateLast = (patch: Partial<ChatMessage>) => {
    setMessages((prev) => {
      if (prev.length === 0) return prev;
      const next = [...prev];
      next[next.length - 1] = { ...next[next.length - 1], ...patch };
      return next;
    });
  };

  const appendToLast = (delta: string) => {
    setMessages((prev) => {
      if (prev.length === 0) return prev;
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
    setChangedIds(new Set());
    setMessages((prev) => [
      ...prev,
      { id: newId(), role: "user", text },
      { id: newId(), role: "assistant", text: "", streaming: true },
    ]);

    try {
      for await (const evt of streamChat(text)) {
        if (evt.type === "text_delta") {
          appendToLast(evt.text);
        } else if (evt.type === "doc_patch") {
          setDoc(evt.doc);
          setChangedIds((prev) => {
            const next = new Set(prev);
            for (const op of evt.ops) {
              if (op.action !== "delete") next.add(op.id);
            }
            return next;
          });
        } else if (evt.type === "doc_snapshot") {
          // The committed tree after a changed turn (correct version pointer).
          setDoc(evt.doc);
        } else if (evt.type === "open_questions") {
          setOpenItems(evt.items);
        } else if (evt.type === "error") {
          updateLast({ text: evt.message, error: true, streaming: false });
          // A failed turn rolled the document back server-side.
          refreshDoc();
          setChangedIds(new Set());
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
    setOpenItems([]);
    setChangedIds(new Set());
    refreshDoc();
  };

  const applyDocPayload = (payload: {
    doc: SpecDoc;
    open_questions: OpenItem[];
  }) => {
    setDoc(payload.doc);
    setOpenItems(payload.open_questions);
    setChangedIds(new Set());
  };

  const onUndo = async () => {
    const payload = await undoDoc().catch(() => null);
    if (payload) applyDocPayload(payload);
  };

  const onRedo = async () => {
    const payload = await redoDoc().catch(() => null);
    if (payload) applyDocPayload(payload);
  };

  const onLoadProject = async (file: File) => {
    try {
      const parsed: unknown = JSON.parse(await file.text());
      const result = await loadProject(parsed);
      applyDocPayload(result);
      setMessages(
        result.chat.map((m) => ({ id: newId(), role: m.role, text: m.text })),
      );
    } catch (e) {
      setMessages((prev) => [
        ...prev,
        {
          id: newId(),
          role: "assistant",
          text: `Could not open that project file: ${
            e instanceof Error ? e.message : String(e)
          }`,
          error: true,
        },
      ]);
    }
  };

  return (
    <div className="flex h-full flex-col">
      <Header health={health} busy={busy} onNewSession={newSession} />
      {health && !health.api_key_present && (
        <ApiKeyBanner onSaved={refreshHealth} />
      )}
      <main className="flex min-h-0 flex-1">
        <Chat messages={messages} busy={busy} onSend={send} />
        <ArtifactPanel
          doc={doc}
          openItems={openItems}
          changedIds={changedIds}
          busy={busy}
          onUndo={onUndo}
          onRedo={onRedo}
          onLoadProject={onLoadProject}
        />
      </main>
    </div>
  );
}
