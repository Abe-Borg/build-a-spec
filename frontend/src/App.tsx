import { useCallback, useEffect, useRef, useState } from "react";
import type {
  AuditSnapshot,
  ChatMessage,
  Health,
  LintIssue,
  OpenItem,
  ResearchSnapshot,
  SpecDoc,
  StandardInfo,
  UpdateCheckPayload,
} from "./types";
import {
  checkUpdate,
  getAuditStatus,
  getDoc,
  getHealth,
  getResearchStatus,
  importMaster,
  installUpdate,
  loadProject,
  redoDoc,
  resetSession,
  startAudit,
  startResearch,
  streamChat,
  streamResearch,
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
  const [lintIssues, setLintIssues] = useState<LintIssue[]>([]);
  const [standards, setStandards] = useState<StandardInfo[]>([]);
  const [profileComplete, setProfileComplete] = useState(false);
  const [research, setResearch] = useState<ResearchSnapshot | null>(null);
  const [audit, setAudit] = useState<AuditSnapshot | null>(null);
  const [update, setUpdate] = useState<UpdateCheckPayload | null>(null);
  const [changedIds, setChangedIds] = useState<ReadonlySet<string>>(new Set());
  const busyRef = useRef(false);
  const researchFollowRef = useRef(false);
  const auditPollRef = useRef(false);

  const refreshHealth = useCallback(() => {
    getHealth()
      .then(setHealth)
      .catch(() => setHealth(null));
  }, []);

  const refreshResearch = useCallback(() => {
    getResearchStatus()
      .then(setResearch)
      .catch(() => setResearch(null));
  }, []);

  const refreshDoc = useCallback(() => {
    getDoc()
      .then((payload) => {
        setDoc(payload.doc);
        setOpenItems(payload.open_questions);
        setLintIssues(payload.lint);
        setStandards(payload.standards);
        setProfileComplete(payload.profile_complete);
      })
      .catch(() => setDoc(null));
  }, []);

  const refreshAudit = useCallback(() => {
    getAuditStatus()
      .then(setAudit)
      .catch(() => setAudit(null));
  }, []);

  useEffect(() => {
    refreshHealth();
    refreshDoc();
    refreshResearch();
    refreshAudit();
    // Throttled auto-check (server enforces once a day); failures ignored.
    checkUpdate().then(setUpdate).catch(() => setUpdate(null));
  }, [refreshHealth, refreshDoc, refreshResearch, refreshAudit]);

  /** Poll the audit while one runs (single call, ~a minute). */
  const pollAudit = useCallback(async () => {
    if (auditPollRef.current) return;
    auditPollRef.current = true;
    try {
      for (let i = 0; i < 600; i += 1) {
        const snapshot = await getAuditStatus();
        setAudit(snapshot);
        if (snapshot.status !== "running") break;
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
    } catch {
      // Snapshot errors surface as a null audit; the button re-enables.
    } finally {
      auditPollRef.current = false;
    }
  }, []);

  const onStartAudit = useCallback(async () => {
    try {
      await startAudit();
      void pollAudit();
    } catch (e) {
      setAudit({
        status: "failed",
        error: e instanceof Error ? e.message : String(e),
      });
    }
  }, [pollAudit]);

  useEffect(() => {
    if (audit?.status === "running") void pollAudit();
  }, [audit?.status, pollAudit]);

  const onImportMaster = useCallback(
    async (file: File) => {
      try {
        const result = await importMaster(file);
        applyDocPayload(result);
        const warningLines = result.warnings.length
          ? "\n\nImport notes:\n" +
            result.warnings.map((w) => `- ${w}`).join("\n")
          : "";
        setMessages((prev) => [
          ...prev,
          {
            id: newId(),
            role: "assistant",
            text:
              `Imported ${result.imported_block_count} provisions from the ` +
              `master — every block is stamped *imported* until we review ` +
              `it for this project. Tell me about the project and I'll ` +
              `walk the master article by article.` +
              warningLines,
          },
        ]);
      } catch (e) {
        setMessages((prev) => [
          ...prev,
          {
            id: newId(),
            role: "assistant",
            text: `Import failed: ${
              e instanceof Error ? e.message : String(e)
            }`,
            error: true,
          },
        ]);
      }
    },
    // applyDocPayload is stable in practice (defined per render but only
    // touches setters); listing setMessages deps is unnecessary noise.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const onInstallUpdate = useCallback(async () => {
    try {
      await installUpdate();
      setMessages((prev) => [
        ...prev,
        {
          id: newId(),
          role: "assistant",
          text: "The installer is running — the app will close to update.",
        },
      ]);
    } catch (e) {
      setMessages((prev) => [
        ...prev,
        {
          id: newId(),
          role: "assistant",
          text: `Update failed: ${e instanceof Error ? e.message : String(e)}`,
          error: true,
        },
      ]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** Follow the SSE stream of a running research, snapshotting as it goes. */
  const followResearch = useCallback(async () => {
    if (researchFollowRef.current) return;
    researchFollowRef.current = true;
    try {
      for await (const _evt of streamResearch()) {
        // Events carry deltas; the snapshot endpoint is authoritative and
        // cheap for a local app — refresh on each frame.
        refreshResearch();
      }
    } finally {
      researchFollowRef.current = false;
      refreshResearch();
    }
  }, [refreshResearch]);

  const onStartResearch = useCallback(async () => {
    try {
      await startResearch();
      void followResearch();
    } catch (e) {
      setResearch((prev) => ({
        status: "failed",
        error: e instanceof Error ? e.message : String(e),
        events: prev?.events ?? [],
      }));
    }
  }, [followResearch]);

  // A page load during a running research (or a resumed project) picks the
  // stream back up.
  useEffect(() => {
    if (research?.status === "running") void followResearch();
  }, [research?.status, followResearch]);

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

    // Set once the server ends the turn (turn_complete or error). A stream
    // that dies without one — network drop, fetch abort, backend restart —
    // was rolled back server-side, so the panel must resync.
    let sawTerminalEvent = false;
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
        } else if (evt.type === "lint") {
          setLintIssues(evt.items);
          setStandards(evt.standards);
        } else if (evt.type === "error") {
          sawTerminalEvent = true;
          updateLast({ text: evt.message, error: true, streaming: false });
          // A failed turn rolled the document back server-side.
          refreshDoc();
          setChangedIds(new Set());
        } else if (evt.type === "turn_complete") {
          sawTerminalEvent = true;
          updateLast({ streaming: false });
          // Profile completeness may have changed (set_project_profile);
          // the snapshot endpoint is authoritative and cheap.
          refreshDoc();
        }
      }
    } catch (e) {
      updateLast({
        text: e instanceof Error ? e.message : String(e),
        error: true,
      });
    } finally {
      if (!sawTerminalEvent) {
        // Drop the optimistic patches from the aborted turn.
        refreshDoc();
        setChangedIds(new Set());
      }
      updateLast({ streaming: false });
      busyRef.current = false;
      setBusy(false);
    }
  };

  const newSession = async () => {
    await resetSession();
    setMessages([]);
    setOpenItems([]);
    setLintIssues([]);
    setStandards([]);
    setChangedIds(new Set());
    refreshDoc();
    refreshResearch();
    refreshAudit();
  };

  const applyDocPayload = (payload: {
    doc: SpecDoc;
    open_questions: OpenItem[];
    lint: LintIssue[];
    standards: StandardInfo[];
    profile_complete: boolean;
  }) => {
    setDoc(payload.doc);
    setOpenItems(payload.open_questions);
    setLintIssues(payload.lint);
    setStandards(payload.standards);
    setProfileComplete(payload.profile_complete);
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
      refreshResearch();
      refreshAudit();
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
      <Header
        health={health}
        busy={busy}
        update={update}
        onNewSession={newSession}
        onInstallUpdate={onInstallUpdate}
      />
      {health && !health.api_key_present && (
        <ApiKeyBanner onSaved={refreshHealth} />
      )}
      <main className="flex min-h-0 flex-1">
        <Chat messages={messages} busy={busy} onSend={send} />
        <ArtifactPanel
          doc={doc}
          openItems={openItems}
          lintIssues={lintIssues}
          standards={standards}
          profileComplete={profileComplete}
          research={research}
          audit={audit}
          changedIds={changedIds}
          busy={busy}
          onUndo={onUndo}
          onRedo={onRedo}
          onLoadProject={onLoadProject}
          onImportMaster={onImportMaster}
          onStartResearch={onStartResearch}
          onStartAudit={onStartAudit}
        />
      </main>
    </div>
  );
}
