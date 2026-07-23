import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  ChatMessage,
  EditOp,
  Figure,
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
  deleteFigure,
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
  stopChat,
  stopQc,
  stopResearch,
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
import HelpModal, { type HelpTopic } from "./components/HelpModal";
import OnboardingOverlay from "./components/OnboardingOverlay";
import { useOnboarding, type DrawerName } from "./lib/useOnboarding";
import CloseDialog from "./components/CloseDialog";
import ConfirmDialog from "./components/ConfirmDialog";

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
  const [helpTopic, setHelpTopic] = useState<HelpTopic | null>(null);
  // Shown when the pywebview shell reports a window-close with unsaved work.
  const [closePromptOpen, setClosePromptOpen] = useState(false);
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [changedIds, setChangedIds] = useState<ReadonlySet<string>>(new Set());
  const [baselineIndex, setBaselineIndex] = useState<number | null>(null);
  // Chat-authored figures (diagrams/schematics/tables), keyed for the bubbles.
  const [figures, setFigures] = useState<Figure[]>([]);
  const figuresById = useMemo(
    () => new Map(figures.map((f) => [f.fid, f])),
    [figures],
  );
  // Composer prefill for the review queue's "Ask model" (WI2). The nonce
  // fires the composer's effect even when the same ref is asked twice.
  const [prefill, setPrefill] = useState({ text: "", nonce: 0 });
  // Guided-tour drawer-open nonces (Batch 6) — a bump expands that drawer.
  const [drawerNonces, setDrawerNonces] = useState({
    review: 0,
    research: 0,
    qc: 0,
    openItems: 0,
  });
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
        setFigures(payload.figures ?? []);
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

  /**
   * Append a terse workflow-event note to the chat (e.g. research / Final QC
   * kicked off). Not a turn, not a model message — a quiet, compact marker so
   * the user gets an acknowledgment in the chat without adding conversational
   * noise. Ephemeral, like the import/update acknowledgments.
   */
  const addNote = useCallback((text: string) => {
    setMessages((prev) => [
      ...prev,
      { id: newId(), role: "assistant", text, note: true },
    ]);
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
      addNote("Sent to Final QC — findings will appear in the Final QC panel.");
      void followQc();
    } catch (e) {
      setQc((prev) => ({
        status: "failed",
        error: e instanceof Error ? e.message : String(e),
        events: prev?.events ?? [],
      }));
    }
  }, [followQc, addNote]);

  /** Stop the running Final QC pass (confirmed in the drawer — loses progress). */
  const onStopQc = useCallback(async () => {
    try {
      await stopQc();
    } catch {
      // Best-effort — the run may have already settled on its own.
    } finally {
      refreshQc();
      refreshReadiness();
    }
  }, [refreshQc, refreshReadiness]);

  // A page load during a running QC (or a resumed project) picks it back up.
  useEffect(() => {
    if (qc?.status === "running") void followQc();
  }, [qc?.status, followQc]);

  // The native shell calls this when the user tries to close the window and
  // the session holds unsaved work; show the save-before-leaving dialog. The
  // shell has already vetoed the close and awaits a js_api call (or a stay).
  useEffect(() => {
    window.buildaspecRequestClose = () => setClosePromptOpen(true);
    return () => {
      delete window.buildaspecRequestClose;
    };
  }, []);

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
      addNote("Started requirements research — progress in the Research panel.");
      void followResearch();
    } catch (e) {
      setResearch((prev) => ({
        status: "failed",
        error: e instanceof Error ? e.message : String(e),
        events: prev?.events ?? [],
      }));
    }
  }, [followResearch, addNote]);

  /** Stop the running research fan-out (confirmed in the drawer — loses progress). */
  const onStopResearch = useCallback(async () => {
    try {
      await stopResearch();
    } catch {
      // Best-effort — the run may have already settled on its own.
    } finally {
      refreshResearch();
    }
  }, [refreshResearch]);

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

  /** Attach a just-created figure to the streaming assistant bubble so it
   *  renders inline beneath the text (and clears the transient status). */
  const attachFigureToLast = (fid: string) => {
    setMessages((prev) => {
      if (prev.length === 0) return prev;
      const next = [...prev];
      const last = next[next.length - 1];
      next[next.length - 1] = {
        ...last,
        figureIds: [...(last.figureIds ?? []), fid],
        status: null,
      };
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

  // Resolves true only when the server completed the turn cleanly — the
  // guided tour awaits this to advance past its demo-generation phase.
  // Existing callers ignore the value.
  const send = async (text: string): Promise<boolean> => {
    if (busyRef.current) return false;
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
    let failed = false;
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
        } else if (evt.type === "figure") {
          // A figure the model just created: add it to the session map and
          // pin it to the current assistant bubble for inline rendering.
          setFigures((prev) => [...prev, evt.figure]);
          attachFigureToLast(evt.figure.fid);
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
          failed = true;
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
      failed = true;
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
    return sawTerminalEvent && !failed;
  };

  /**
   * Stop the in-flight turn (Claude.ai-style). No confirmation — whatever
   * text/edits already landed stay; the turn ends through its own normal
   * `turn_complete`, same as if the model had finished on its own.
   */
  const onStop = async () => {
    try {
      await stopChat();
    } catch {
      // Best-effort — the turn may have already finished on its own.
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

  /** Remove a figure (the ✕ on a figure card). Resync on a 409/404. */
  const onDeleteFigure = useCallback(
    async (fid: string) => {
      try {
        setFigures(await deleteFigure(fid));
      } catch {
        refreshDoc();
      }
    },
    [refreshDoc],
  );

  const newSession = async () => {
    await resetSession();
    setMessages([]);
    setOpenItems([]);
    setLintIssues([]);
    setStandards([]);
    setChangedIds(new Set());
    setFigures([]);
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
    figures?: Figure[];
  }) => {
    setDoc(payload.doc);
    setOpenItems(payload.open_questions);
    setLintIssues(payload.lint);
    setStandards(payload.standards);
    setProfileComplete(payload.profile_complete);
    setBaselineIndex(payload.baseline_index ?? null);
    setFigures(payload.figures ?? []);
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
    // Loading a project is session-changing work — a mid-tour load kills
    // the tour (the runId guard keeps any in-flight step from advancing).
    onboarding.abort();
    try {
      const parsed: unknown = JSON.parse(await file.text());
      const result = await loadProject(parsed);
      applyDocPayload(result);
      refreshResearch();
      refreshQc();
      refreshReadiness();
      // Rebuild the transcript and re-inline each figure into the assistant
      // bubble that created it (matched by its stored message_index — the
      // ordinal among assistant bubbles).
      const rebuilt: ChatMessage[] = result.chat.map((m) => ({
        id: newId(),
        role: m.role,
        text: m.text,
      }));
      const assistantPositions = rebuilt
        .map((m, i) => (m.role === "assistant" ? i : -1))
        .filter((i) => i >= 0);
      for (const figure of result.figures ?? []) {
        const at = assistantPositions[figure.message_index];
        if (at !== undefined) {
          const msg = rebuilt[at];
          msg.figureIds = [...(msg.figureIds ?? []), figure.fid];
        }
      }
      setMessages(rebuilt);
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

  // --- Guided tour (Batch 6) ---
  const hasContent =
    !!doc &&
    (doc.section.number !== "" ||
      doc.section.title !== "" ||
      doc.parts.some((p) => p.articles.length > 0));

  const bumpDrawer = useCallback((name: DrawerName) => {
    setDrawerNonces((prev) => ({ ...prev, [name]: prev[name] + 1 }));
  }, []);

  const onboarding = useOnboarding({
    send,
    editDoc: onEditDoc,
    startResearch: onStartResearch,
    startQc: onStartQc,
    newSession,
    prefillComposer: onAskModel,
    health,
    doc,
    hasContent,
  });

  return (
    <div className="flex h-full flex-col">
      <Header
        health={health}
        busy={busy}
        update={update}
        usage={usage}
        onNewSession={() => {
          // The header button is session-changing work: kill the tour first
          // (the hook's own fresh-start path calls the raw newSession).
          onboarding.abort();
          void newSession();
        }}
        onStartTour={onboarding.start}
        onInstallUpdate={onInstallUpdate}
        onOpenSettings={() => {
          setSettingsOpen(true);
          refreshUsage();
        }}
        onOpenHelp={setHelpTopic}
      />
      {health && !health.api_key_present && (
        <ApiKeyBanner onSaved={refreshHealth} />
      )}
      <SettingsPanel
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        usage={usage}
        onKeyChange={refreshHealth}
      />
      <HelpModal
        topic={helpTopic}
        onClose={() => setHelpTopic(null)}
        onNavigate={setHelpTopic}
        health={health}
      />
      <OnboardingOverlay
        ob={onboarding}
        doc={doc}
        busy={busy}
        profileComplete={profileComplete}
        researchStatus={research?.status ?? "idle"}
        qcStatus={qc?.status ?? "idle"}
        hasContent={hasContent}
        bumpDrawer={bumpDrawer}
      />
      {/* Closing (✕ / backdrop) any tour popup confirms here first, so the
          guided tour is never dismissed by accident. Elevated above the
          overlay's own modals. */}
      <ConfirmDialog
        open={onboarding.endConfirm && onboarding.phase.kind !== "idle"}
        elevated
        danger
        title="End the guided tour?"
        body={
          <>
            You can restart it anytime from the{" "}
            <b className="text-ink">Tour</b> button in the header — the demo
            section stays on the page either way.
          </>
        }
        confirmLabel="End tour"
        cancelLabel="Continue tour"
        onConfirm={onboarding.abort}
        onCancel={onboarding.cancelEnd}
      />
      <CloseDialog
        open={closePromptOpen}
        onSave={() => {
          setClosePromptOpen(false);
          void window.pywebview?.api?.save_and_close?.();
        }}
        onDiscard={() => {
          setClosePromptOpen(false);
          void window.pywebview?.api?.discard_and_close?.();
        }}
        onCancel={() => setClosePromptOpen(false)}
      />
      <main className="flex min-h-0 flex-1">
        <Chat
          messages={messages}
          busy={busy}
          onSend={send}
          onStartOnboarding={onboarding.start}
          onStop={onStop}
          prefill={prefill}
          figuresById={figuresById}
          onDeleteFigure={onDeleteFigure}
        />
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
          onStopResearch={onStopResearch}
          onStartQc={onStartQc}
          onStopQc={onStopQc}
          onApplyQc={onApplyQc}
          onDismissQc={onDismissQc}
          onDraftFull={onDraftFull}
          onAskModel={onAskModel}
          onFetchDiff={getDocDiff}
          drawerNonces={drawerNonces}
        />
      </main>
    </div>
  );
}
