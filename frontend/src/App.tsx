import { useCallback, useEffect, useRef, useState } from "react";
import type {
  ChatMessage,
  EditOp,
  Health,
  LintIssue,
  OpenItem,
  QcSnapshot,
  ReadinessPayload,
  ResearchSnapshot,
  SpecDoc,
  StandardInfo,
  UpdateCheckPayload,
  UsageSummary,
} from "./types";
import {
  applyQc,
  checkUpdate,
  dismissQc,
  draftFull,
  editDoc,
  getDoc,
  getDocDiff,
  getHealth,
  getQcStatus,
  getReadiness,
  getResearchStatus,
  getUsage,
  importMaster,
  installUpdate,
  loadProject,
  redoDoc,
  resetSession,
  startQc,
  startResearch,
  streamChat,
  streamQc,
  streamResearch,
  undoDoc,
} from "./lib/api";
import Header from "./components/Header";
import ApiKeyBanner from "./components/ApiKeyBanner";
import Chat from "./components/Chat";
import ArtifactPanel from "./components/ArtifactPanel";
import SettingsPanel from "./components/SettingsPanel";

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
  const [qc, setQc] = useState<QcSnapshot | null>(null);
  const [readiness, setReadiness] = useState<ReadinessPayload | null>(null);
  const [update, setUpdate] = useState<UpdateCheckPayload | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [changedIds, setChangedIds] = useState<ReadonlySet<string>>(new Set());
  const [baselineIndex, setBaselineIndex] = useState<number | null>(null);
  // Composer prefill for the review queue's "Ask model" (WI2). The nonce
  // fires the composer's effect even when the same ref is asked twice.
  const [prefill, setPrefill] = useState({ text: "", nonce: 0 });
  const busyRef = useRef(false);
  const researchFollowRef = useRef(false);
  const qcFollowRef = useRef(false);

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
        setBaselineIndex(payload.baseline_index ?? null);
      })
      .catch(() => setDoc(null));
  }, []);

  const refreshQc = useCallback(() => {
    getQcStatus()
      .then(setQc)
      .catch(() => setQc(null));
  }, []);

  const refreshReadiness = useCallback(() => {
    getReadiness()
      .then(setReadiness)
      .catch(() => setReadiness(null));
  }, []);

  const refreshUsage = useCallback(() => {
    getUsage()
      .then(setUsage)
      .catch(() => setUsage(null));
  }, []);

  useEffect(() => {
    refreshHealth();
    refreshDoc();
    refreshResearch();
    refreshQc();
    refreshReadiness();
    refreshUsage();
    // Throttled auto-check (server enforces once a day); failures ignored.
    checkUpdate().then(setUpdate).catch(() => setUpdate(null));
  }, [
    refreshHealth,
    refreshDoc,
    refreshResearch,
    refreshQc,
    refreshReadiness,
    refreshUsage,
  ]);

  /** Follow the QC run's SSE stream, snapshotting + metering as it streams. */
  const followQc = useCallback(async () => {
    if (qcFollowRef.current) return;
    qcFollowRef.current = true;
    try {
      for await (const _evt of streamQc()) {
        // The snapshot endpoint is authoritative and cheap for a local app;
        // refresh on each event so lens/verify progress lands live (no dead air).
        refreshQc();
        refreshUsage();
      }
    } finally {
      qcFollowRef.current = false;
      refreshQc();
      refreshReadiness();
      refreshUsage();
    }
  }, [refreshQc, refreshReadiness, refreshUsage]);

  const onStartQc = useCallback(async () => {
    try {
      await startQc();
      void followQc();
    } catch (e) {
      setQc((prev) => ({
        status: "failed",
        error: e instanceof Error ? e.message : String(e),
        events: prev?.events ?? [],
      }));
    }
  }, [followQc]);

  // A page load during a running QC (or a resumed project) picks it back up.
  useEffect(() => {
    if (qc?.status === "running") void followQc();
  }, [qc?.status, followQc]);

  const onApplyQc = useCallback(
    async (findingIds: string[]) => {
      try {
        const payload = await applyQc(findingIds);
        applyDocPayload(payload);
        refreshQc();
        refreshReadiness();
      } catch (e) {
        refreshDoc();
        setMessages((prev) => [
          ...prev,
          {
            id: newId(),
            role: "assistant",
            text: `Could not apply the fix: ${
              e instanceof Error ? e.message : String(e)
            }`,
            error: true,
          },
        ]);
      }
    },
    // applyDocPayload is stable in practice; listing it is noise.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [refreshQc, refreshReadiness, refreshDoc],
  );

  const onDismissQc = useCallback(
    async (findingId: string, reason?: string) => {
      try {
        const snapshot = await dismissQc(findingId, reason);
        setQc(snapshot);
        refreshReadiness();
      } catch {
        refreshQc();
      }
    },
    [refreshQc, refreshReadiness],
  );

  const onImportMaster = useCallback(
    async (file: File) => {
      try {
        const result = await importMaster(file);
        applyDocPayload(result);
        refreshReadiness();
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
      refreshUsage();
    }
  }, [refreshResearch, refreshUsage]);

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

  /** Append streamed body text and clear the transient status strip. */
  const appendToLast = (delta: string) => {
    setMessages((prev) => {
      if (prev.length === 0) return prev;
      const next = [...prev];
      const last = next[next.length - 1];
      next[next.length - 1] = { ...last, text: last.text + delta, status: null };
      return next;
    });
  };

  /** Append a streamed adaptive-thinking summary; clears the status strip. */
  const appendThinkingToLast = (delta: string) => {
    setMessages((prev) => {
      if (prev.length === 0) return prev;
      const next = [...prev];
      const last = next[next.length - 1];
      next[next.length - 1] = {
        ...last,
        thinking: (last.thinking ?? "") + delta,
        status: null,
      };
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
        } else if (evt.type === "thinking_delta") {
          appendThinkingToLast(evt.text);
        } else if (evt.type === "status") {
          // A "writing" hint just means text is imminent — clear the strip
          // (text_delta arrives immediately after). Everything else shows.
          updateLast({
            status:
              evt.kind === "writing"
                ? null
                : { kind: evt.kind, round: evt.round, progress_chars: evt.progress_chars },
          });
        } else if (evt.type === "web_search") {
          // Surface live web activity inline in the streaming message.
          appendToLast(`\n\n*🔍 Searched the web: "${evt.query}"*\n\n`);
        } else if (evt.type === "web_fetch") {
          appendToLast(`\n\n*📄 Reading: ${evt.url}*\n\n`);
        } else if (evt.type === "doc_patch") {
          setDoc(evt.doc);
          const changed = evt.ops
            .filter((op) => op.action !== "delete")
            .map((op) => op.id);
          setChangedIds((prev) => new Set([...prev, ...changed]));
          // Nudge the doc panel to the first changed block as it lands.
          if (changed.length > 0) {
            const first = changed[0];
            requestAnimationFrame(() =>
              document
                .getElementById(`el-${first}`)
                ?.scrollIntoView({ block: "nearest", behavior: "smooth" }),
            );
          }
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
          updateLast({
            text: evt.message,
            error: true,
            streaming: false,
            status: null,
          });
          // A failed turn rolled the document back server-side — but the
          // spend was real, so refresh the meter too.
          refreshDoc();
          refreshUsage();
          setChangedIds(new Set());
        } else if (evt.type === "turn_complete") {
          sawTerminalEvent = true;
          updateLast({ streaming: false, status: null });
          // Profile completeness may have changed (set_project_profile);
          // the snapshot endpoint is authoritative and cheap. A doc-changing
          // turn also moves the readiness gate (and can stale a QC result).
          refreshDoc();
          refreshUsage();
          refreshReadiness();
        }
        // Unknown event types are ignored so an older/newer backend never
        // crashes the UI.
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
      updateLast({ streaming: false, status: null });
      busyRef.current = false;
      setBusy(false);
    }
  };

  /** Fetch the canned full-draft directive and send it as a normal turn. */
  const onDraftFull = async () => {
    if (busyRef.current) return;
    try {
      const message = await draftFull();
      await send(message);
    } catch (e) {
      setMessages((prev) => [
        ...prev,
        {
          id: newId(),
          role: "assistant",
          text: `Could not start the full draft: ${
            e instanceof Error ? e.message : String(e)
          }`,
          error: true,
        },
      ]);
    }
  };

  /** Prefill the composer from a review-queue "Ask model" and focus it. */
  const onAskModel = (text: string) => {
    setPrefill((p) => ({ text, nonce: p.nonce + 1 }));
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
    refreshQc();
    refreshReadiness();
  };

  const applyDocPayload = (payload: {
    doc: SpecDoc;
    open_questions: OpenItem[];
    lint: LintIssue[];
    standards: StandardInfo[];
    profile_complete: boolean;
    baseline_index?: number | null;
  }) => {
    setDoc(payload.doc);
    setOpenItems(payload.open_questions);
    setLintIssues(payload.lint);
    setStandards(payload.standards);
    setProfileComplete(payload.profile_complete);
    setBaselineIndex(payload.baseline_index ?? null);
    setChangedIds(new Set());
  };

  const onEditDoc = async (ops: EditOp[]) => {
    try {
      const payload = await editDoc(ops);
      applyDocPayload(payload);
      refreshReadiness();
      // Flash the blocks the user just touched (deletes have nothing to flash).
      const touched = ops
        .filter((op) => op.action !== "delete")
        .map((op) => op.target_id);
      if (touched.length) setChangedIds(new Set(touched));
    } catch (e) {
      // 409 (a turn is streaming) or 400 (bad op): resync and surface it.
      refreshDoc();
      setMessages((prev) => [
        ...prev,
        {
          id: newId(),
          role: "assistant",
          text: `Edit not applied: ${e instanceof Error ? e.message : String(e)}`,
          error: true,
        },
      ]);
    }
  };

  const onUndo = async () => {
    const payload = await undoDoc().catch(() => null);
    if (payload) {
      applyDocPayload(payload);
      refreshReadiness();
    }
  };

  const onRedo = async () => {
    const payload = await redoDoc().catch(() => null);
    if (payload) {
      applyDocPayload(payload);
      refreshReadiness();
    }
  };

  const onLoadProject = async (file: File) => {
    try {
      const parsed: unknown = JSON.parse(await file.text());
      const result = await loadProject(parsed);
      applyDocPayload(result);
      refreshResearch();
      refreshQc();
      refreshReadiness();
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
        usage={usage}
        onNewSession={newSession}
        onInstallUpdate={onInstallUpdate}
        onOpenSettings={() => {
          setSettingsOpen(true);
          refreshUsage();
        }}
      />
      {health && !health.api_key_present && (
        <ApiKeyBanner onSaved={refreshHealth} />
      )}
      <SettingsPanel
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        health={health}
        usage={usage}
        onKeyChange={refreshHealth}
      />
      <main className="flex min-h-0 flex-1">
        <Chat messages={messages} busy={busy} onSend={send} prefill={prefill} />
        <ArtifactPanel
          doc={doc}
          openItems={openItems}
          lintIssues={lintIssues}
          standards={standards}
          profileComplete={profileComplete}
          research={research}
          qc={qc}
          readiness={readiness}
          usage={usage}
          changedIds={changedIds}
          baselineIndex={baselineIndex}
          busy={busy}
          onUndo={onUndo}
          onRedo={onRedo}
          onEditDoc={onEditDoc}
          onLoadProject={onLoadProject}
          onImportMaster={onImportMaster}
          onStartResearch={onStartResearch}
          onStartQc={onStartQc}
          onApplyQc={onApplyQc}
          onDismissQc={onDismissQc}
          onDraftFull={onDraftFull}
          onAskModel={onAskModel}
          onFetchDiff={getDocDiff}
        />
      </main>
    </div>
  );
}
