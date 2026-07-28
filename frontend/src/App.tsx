import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  ChatMessage,
  EditOp,
  Figure,
  FileLoading,
  Health,
  ImportNotice,
  ImportReport,
  ReferenceDocMeta,
  LintIssue,
  OpenItem,
  QcSnapshot,
  ReadinessPayload,
  ResearchSnapshot,
  SessionBundle,
  SourceCapabilitiesState,
  SpecDoc,
  StandardInfo,
  TutorialEvent,
  TemplateOrigin,
  UpdateCheckPayload,
  UsageSummary,
} from "./types";
import {
  applyQc,
  checkUnsaved,
  checkUpdate,
  deleteFigure,
  dismissQc,
  downloadProjectFile,
  draftFull,
  editDoc,
  getDoc,
  getDocCapabilities,
  getDocDiff,
  getHealth,
  getQcStatus,
  getReadiness,
  getResearchStatus,
  getSessionBundle,
  getUsage,
  importMaster,
  instantiateTemplate,
  uploadReference,
  deleteReference,
  installUpdate,
  loadProjectFile,
  redoDoc,
  resetSession,
  QcStartError,
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
import NewSessionDialog from "./components/NewSessionDialog";
import { sourceCapabilitiesPending } from "./lib/sourceCapabilities";
import {
  useOnboarding,
  type DrawerName,
  type OnboardingApi,
} from "./lib/useOnboarding";
import {
  formatProjectHeading,
  projectDiscipline,
} from "./lib/projectHeading";
import CloseDialog from "./components/CloseDialog";
import ConfirmDialog from "./components/ConfirmDialog";

let nextId = 0;
const newId = () => `m${++nextId}`;

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [busy, setBusy] = useState(false);
  // Manual document edits have their own lock. They must disable the paper's
  // mutation controls while the authoritative server response is in flight,
  // but they are not a chat turn and must never turn the composer's Stop
  // button on. The ref closes the same-tick double-submit gap before React can
  // render the state change (especially important for rapid drag/drop moves).
  const [manualEditBusy, setManualEditBusy] = useState(false);
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
  const [tutorialCloseBlocked, setTutorialCloseBlocked] = useState(false);
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [changedIds, setChangedIds] = useState<ReadonlySet<string>>(new Set());
  const [baselineIndex, setBaselineIndex] = useState<number | null>(null);
  const [importReport, setImportReport] = useState<ImportReport | null>(null);
  const [referenceDocs, setReferenceDocs] = useState<ReferenceDocMeta[]>([]);
  const [referenceBusy, setReferenceBusy] = useState(false);
  const [sourceAvailable, setSourceAvailable] = useState(false);
  const [preservationReady, setPreservationReady] = useState(false);
  const [sourceCapabilities, setSourceCapabilities] =
    useState<SourceCapabilitiesState | null>(null);
  const [templateOrigin, setTemplateOrigin] = useState<TemplateOrigin | null>(null);
  // Chat-authored figures (diagrams/schematics/tables), keyed for the bubbles.
  const [figures, setFigures] = useState<Figure[]>([]);
  const figuresById = useMemo(
    () => new Map(figures.map((f) => [f.fid, f])),
    [figures],
  );
  // Model-staged reply chips (Batch 9), shown above the composer. Cleared at
  // turn start; re-synced from the doc payload on every refresh (so a failed
  // turn's refresh restores the untouched pre-turn set).
  const [suggestions, setSuggestions] = useState<string[]>([]);
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
  const [newSessionOpen, setNewSessionOpen] = useState(false);
  const [templatesOnly, setTemplatesOnly] = useState(false);
  const [templateStarting, setTemplateStarting] = useState(false);
  // The in-app save-before-you-lose-it gate for the two paths that discard the
  // session (New session, Open project). Non-null = the prompt is open; the
  // pending action (and, for a load, its file) runs after Save/Discard. Null
  // when idle — the session-close window prompt is separate (closePromptOpen).
  const [saveGate, setSaveGate] = useState<
    | { kind: "new-session" }
    | { kind: "open-project"; file: File }
    | { kind: "start-template"; templateId: string }
    | null
  >(null);
  // A file upload in flight (master import / project open). Reading, parsing
  // and indexing a big master takes real seconds on the server, and until it
  // lands nothing in the panel changes — without this the app just looked
  // frozen. Non-null drives the panel's progress line and the disabled
  // Import/Open buttons; the ref is the double-submit guard (state updates
  // are async, a fast second click would slip past it).
  const [fileLoading, setFileLoading] = useState<FileLoading>(null);
  // What the panel says about the last import: a failure, or the content-loss
  // warnings of a lossy one. Both used to be announced in the chat, which put
  // machine-generated notices in the middle of the conversation; they now
  // report where the action was taken. Null for a clean import, which is the
  // normal case — the panel then says nothing about the import at all.
  const [importNotice, setImportNotice] = useState<ImportNotice>(null);
  const fileLoadingRef = useRef(false);
  const busyRef = useRef(false);
  const manualEditBusyRef = useRef(false);
  const researchFollowRef = useRef(false);
  const qcFollowRef = useRef(false);
  const onboardingRef = useRef<OnboardingApi | null>(null);
  // Every whole-session/tutorial transition advances this epoch. Read calls
  // and streams captured against an older workspace must never repaint the
  // newly hydrated document with discarded scenario state.
  const workspaceEpochRef = useRef(0);

  const refreshHealth = useCallback(() => {
    const epoch = workspaceEpochRef.current;
    getHealth()
      .then((value) => {
        if (workspaceEpochRef.current === epoch) setHealth(value);
      })
      .catch(() => {
        if (workspaceEpochRef.current === epoch) setHealth(null);
      });
  }, []);

  const refreshResearch = useCallback(() => {
    const epoch = workspaceEpochRef.current;
    getResearchStatus()
      .then((value) => {
        if (workspaceEpochRef.current === epoch) setResearch(value);
      })
      .catch(() => {
        if (workspaceEpochRef.current === epoch) setResearch(null);
      });
  }, []);

  const refreshDoc = useCallback(() => {
    const epoch = workspaceEpochRef.current;
    getDoc()
      .then((payload) => {
        if (workspaceEpochRef.current !== epoch) return;
        setDoc(payload.doc);
        setOpenItems(payload.open_questions);
        setLintIssues(payload.lint);
        setStandards(payload.standards);
        setProfileComplete(payload.profile_complete);
        setBaselineIndex(payload.baseline_index ?? null);
        setImportReport(payload.import_report ?? null);
        setSourceAvailable(payload.source_available ?? false);
        setPreservationReady(payload.preservation_ready ?? false);
        setSourceCapabilities(payload.source_capabilities ?? null);
        setTemplateOrigin(payload.template_origin ?? null);
        setFigures(payload.figures ?? []);
        setSuggestions(payload.suggested_prompts ?? []);
        setReferenceDocs(payload.reference_docs ?? []);
      })
      .catch(() => {
        if (workspaceEpochRef.current === epoch) setDoc(null);
      });
  }, []);

  const refreshQc = useCallback(() => {
    const epoch = workspaceEpochRef.current;
    getQcStatus()
      .then((value) => {
        if (workspaceEpochRef.current === epoch) setQc(value);
      })
      .catch(() => {
        if (workspaceEpochRef.current === epoch) setQc(null);
      });
  }, []);

  const refreshReadiness = useCallback(() => {
    const epoch = workspaceEpochRef.current;
    getReadiness()
      .then((value) => {
        if (workspaceEpochRef.current === epoch) setReadiness(value);
      })
      .catch(() => {
        if (workspaceEpochRef.current === epoch) setReadiness(null);
      });
  }, []);

  const refreshUsage = useCallback(() => {
    const epoch = workspaceEpochRef.current;
    getUsage()
      .then((value) => {
        if (workspaceEpochRef.current === epoch) setUsage(value);
      })
      .catch(() => {
        if (workspaceEpochRef.current === epoch) setUsage(null);
      });
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

  /**
   * Poll for the imported-source permission sweep while it is still running.
   *
   * The sweep probes every element against the real gate, so it happens in
   * the background and the report arrives as `pending` first. Editing
   * affordances stay disabled (fail-closed) until it lands; this is what
   * turns them back on. One cheap capabilities-only request per tick, then a
   * single full document refresh on the transition — polling `getDoc` would
   * rebuild the outline, lint and readiness plan every second for nothing.
   */
  useEffect(() => {
    if (!sourceCapabilitiesPending(sourceCapabilities)) return;
    let cancelled = false;
    let timer = 0;
    // A self-scheduling chain rather than setInterval: the sweep takes
    // minutes on a large master, so ticks must never stack up behind a slow
    // response — each one is scheduled only after the previous settles. The
    // backoff keeps a long wait cheap without making a short one feel slow.
    let delay = 750;
    const tick = () => {
      getDocCapabilities()
        .then((report) => {
          if (cancelled) return;
          if (report?.status === "pending") {
            delay = Math.min(delay * 1.5, 5000);
            timer = window.setTimeout(tick, delay);
            return;
          }
          // Settled. One writer for this state: refreshDoc re-reads the whole
          // payload (the document can have moved while the sweep ran) and
          // sets source_capabilities from the same response.
          refreshDoc();
          // QC freshness and readiness both compare against the imported-
          // source permissions, so while those were pending they answered
          // "stale" / "not ready". Nothing else re-asks them, so a project
          // opened with a retained master and a retained QC result would sit
          // on a wrong "re-run Final QC" until some other action refreshed.
          refreshQc();
          refreshReadiness();
        })
        .catch(() => {
          if (cancelled) return;
          delay = Math.min(delay * 1.5, 5000);
          timer = window.setTimeout(tick, delay);
        });
    };
    timer = window.setTimeout(tick, delay);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [sourceCapabilities, refreshDoc, refreshQc, refreshReadiness]);

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

  const onStartQc = useCallback(async (acknowledgeScopeMismatch = false) => {
    try {
      await startQc(acknowledgeScopeMismatch, {
        workspaceId: health?.workspace_id,
        generation: health?.generation,
      });
      addNote("Sent to Final QC — findings will appear in the Final QC panel.");
      void followQc();
    } catch (e) {
      setQc((prev) => ({
        ...prev,
        status: "failed",
        error: e instanceof Error ? e.message : String(e),
        events: prev?.events ?? [],
        module_section_compatibility:
          e instanceof QcStartError && e.moduleSectionCompatibility
            ? e.moduleSectionCompatibility
            : prev?.module_section_compatibility,
      }));
    }
  }, [followQc, addNote, health]);

  /** Stop Final QC while its worker preserves any completed paid activity. */
  const onStopQc = useCallback(async () => {
    try {
      await stopQc({
        workspaceId: health?.workspace_id,
        generation: health?.generation,
      });
    } catch {
      // Best-effort — the run may have already settled on its own.
    } finally {
      refreshQc();
      refreshReadiness();
    }
  }, [refreshQc, refreshReadiness, health]);

  // A page load during a running QC (or a resumed project) picks it back up.
  useEffect(() => {
    if (qc?.status === "running" || qc?.settling) void followQc();
  }, [qc?.status, qc?.settling, followQc]);

  // The native shell calls this when the user tries to close the window and
  // the session holds unsaved work; show the save-before-leaving dialog. The
  // shell has already vetoed the close and awaits a js_api call (or a stay).
  useEffect(() => {
    window.buildaspecRequestClose = (reason) => {
      if (reason === "tutorial-busy") {
        setTutorialCloseBlocked(true);
        return;
      }
      if (reason === "tutorial-restored") {
        void getSessionBundle()
          .then((session) => {
            onboardingRef.current?.acceptNativeRestore(session);
          })
          .finally(() => setClosePromptOpen(true));
        return;
      }
      setClosePromptOpen(true);
    };
    return () => {
      delete window.buildaspecRequestClose;
    };
  }, []);

  const onApplyQc = useCallback(
    async (findingIds: string[]) => {
      const epoch = workspaceEpochRef.current;
      try {
        const payload = await applyQc(findingIds, {
          workspaceId: health?.workspace_id,
          generation: health?.generation,
        });
        if (workspaceEpochRef.current !== epoch) return;
        applyDocPayload(payload);
        refreshQc();
        refreshReadiness();
        const outcomeLabels: Record<string, string> = {
          applied: "applied",
          already_applied: "not changed (already applied)",
          no_ops: "not changed (no validated mechanical fix)",
          stale: "not changed (operation no longer applied cleanly)",
          unknown: "not changed (finding not found)",
        };
        const lines = Object.entries(payload.outcomes).map(
          ([findingId, outcome]) =>
            `- ${findingId}: ${outcomeLabels[outcome] ?? outcome}`,
        );
        setMessages((prev) => [
          ...prev,
          {
            id: newId(),
            role: "assistant",
            text:
              "Final QC application result (also recorded in the QC report):\n" +
              (lines.length ? lines.join("\n") : "- No findings were selected."),
          },
        ]);
      } catch (e) {
        if (workspaceEpochRef.current !== epoch) return;
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
    [refreshQc, refreshReadiness, refreshDoc, health],
  );

  const onDismissQc = useCallback(
    async (findingId: string, reason: string) => {
      const epoch = workspaceEpochRef.current;
      try {
        const snapshot = await dismissQc(findingId, reason, {
          workspaceId: health?.workspace_id,
          generation: health?.generation,
        });
        if (workspaceEpochRef.current !== epoch) return;
        setQc(snapshot);
        refreshReadiness();
      } catch (e) {
        if (workspaceEpochRef.current !== epoch) return;
        refreshQc();
        setMessages((prev) => [
          ...prev,
          {
            id: newId(),
            role: "assistant",
            text: `Could not record the QC dismissal: ${
              e instanceof Error ? e.message : String(e)
            }`,
            error: true,
          },
        ]);
        throw e;
      }
    },
    [refreshQc, refreshReadiness, health],
  );

  const onImportMaster = useCallback(
    async (file: File) => {
      // One upload at a time: the endpoint rejects a second import anyway
      // (the document is no longer blank), and a queued duplicate would only
      // produce a confusing error after a long wait.
      if (fileLoadingRef.current) return;
      fileLoadingRef.current = true;
      setImportNotice(null);
      setFileLoading({ kind: "import", name: file.name });
      // Nothing is written to the chat for an import — not progress, not a
      // summary, not a failure. The chat is the conversation with the model;
      // an upload is a panel action, and the panel reports it (progress line,
      // button label, skeleton sheet, and the error slot below).
      try {
        const result = await importMaster(file);
        applyDocPayload(result);
        refreshReadiness();
        // A clean import says nothing at all. A lossy one still has to warn
        // loudly (the importer's keep-everything-warn-loudly rule) — as a
        // dismissible panel notice, not a permanent strip and not chat.
        const warnings =
          result.import_report?.warnings ?? result.warnings ?? [];
        if (warnings.length) {
          setImportNotice({ tone: "warn", name: file.name, lines: warnings });
        }
      } catch (e) {
        setImportNotice({
          tone: "error",
          name: file.name,
          lines: [e instanceof Error ? e.message : String(e)],
        });
      } finally {
        fileLoadingRef.current = false;
        setFileLoading(null);
      }
    },
    // applyDocPayload is stable in practice (defined per render but only
    // touches setters); listing setters here is unnecessary noise.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [refreshReadiness],
  );

  // Attaching background material is deliberately lighter than importing a
  // master: it never touches the document, so there is no blank-document
  // gate, no LoadingState sheet, and no doc payload to apply — just the list.
  // Nothing is written to the chat for an attachment, for the same reason an
  // import writes nothing: the chat is the conversation with the model, and an
  // upload is a panel action the panel reports. A clean attach says nothing at
  // all — the new row in the reference list is the confirmation. A lossy or
  // failed one still has to say so, in the same dismissible notice slot.
  const onAttachReference = useCallback(async (file: File) => {
    setReferenceBusy(true);
    setImportNotice(null);
    try {
      const { reference_docs, warnings } = await uploadReference(file, {
        workspaceId: health?.workspace_id,
        generation: health?.generation,
      });
      setReferenceDocs(reference_docs);
      if (warnings.length) {
        setImportNotice({
          tone: "warn",
          name: file.name,
          lines: warnings,
          title: `${warnings.length} note${
            warnings.length === 1 ? "" : "s"
          } attaching ${file.name}`,
        });
      }
    } catch (e) {
      setImportNotice({
        tone: "error",
        name: file.name,
        lines: [e instanceof Error ? e.message : String(e)],
        title: `Could not attach ${file.name}`,
      });
    } finally {
      setReferenceBusy(false);
    }
  }, [health]);

  const onRemoveReference = useCallback(async (rid: string) => {
    setReferenceBusy(true);
    try {
      const result = await deleteReference(rid, {
        workspaceId: health?.workspace_id,
        generation: health?.generation,
      });
      setReferenceDocs(result.reference_docs);
      setSuggestions(result.suggested_prompts);
      setFigures(result.figures);
    } catch (e) {
      setImportNotice({
        tone: "error",
        name: rid,
        lines: [e instanceof Error ? e.message : String(e)],
        title: "Could not remove that reference document",
      });
    } finally {
      setReferenceBusy(false);
    }
  }, [health]);

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
      await startResearch({
        workspaceId: health?.workspace_id,
        generation: health?.generation,
      });
      addNote("Started requirements research — progress in the Research panel.");
      void followResearch();
    } catch (e) {
      setResearch((prev) => ({
        status: "failed",
        error: e instanceof Error ? e.message : String(e),
        events: prev?.events ?? [],
      }));
    }
  }, [followResearch, addNote, health]);

  /** Stop the running research fan-out (confirmed in the drawer — loses progress). */
  const onStopResearch = useCallback(async () => {
    try {
      await stopResearch({
        workspaceId: health?.workspace_id,
        generation: health?.generation,
      });
    } catch {
      // Best-effort — the run may have already settled on its own.
    } finally {
      refreshResearch();
    }
  }, [refreshResearch, health]);

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

  // Resolves true only when the server completed the turn cleanly.
  const send = async (text: string): Promise<boolean> => {
    if (busyRef.current || manualEditBusyRef.current) return false;
    // A turn started mid-upload would be rejected by the import's own guard
    // once the file finished parsing — a 409 arriving long after the click,
    // blaming the upload. Refuse it here instead.
    if (fileLoadingRef.current) return false;
    busyRef.current = true;
    setBusy(true);
    setChangedIds(new Set());
    // Clear the suggestion bar the moment a message (typed or chip-clicked)
    // goes out; the turn re-populates it (or leaves it empty) as it streams.
    setSuggestions([]);
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
        } else if (evt.type === "suggested_prompts") {
          // Live-staged reply chips; the commit-authoritative value re-syncs
          // via refreshDoc on turn_complete (same list) or error (pre-turn).
          setSuggestions(evt.prompts);
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
    if (busyRef.current || manualEditBusyRef.current) return;
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
      const epoch = workspaceEpochRef.current;
      try {
        const remaining = await deleteFigure(fid, {
          workspaceId: health?.workspace_id,
          generation: health?.generation,
        });
        if (workspaceEpochRef.current === epoch) setFigures(remaining);
      } catch {
        refreshDoc();
      }
    },
    [health, refreshDoc],
  );

  /** Shared post-reset clear+refresh — every session-start path runs this. */
  const clearSessionState = () => {
    workspaceEpochRef.current += 1;
    setMessages([]);
    setOpenItems([]);
    setLintIssues([]);
    setStandards([]);
    setChangedIds(new Set());
    setFigures([]);
    setSuggestions([]);
    setReferenceDocs([]);
    setImportReport(null);
    setSourceAvailable(false);
    setPreservationReady(false);
    setSourceCapabilities(null);
    setTemplateOrigin(null);
    refreshDoc();
    refreshResearch();
    refreshQc();
    refreshReadiness();
  };

  /** Start a truly neutral session; chat establishes all project identity. */
  const startBlankSession = async () => {
    setNewSessionOpen(false);
    setTemplatesOnly(false);
    await resetSession({
      module_id: "generic",
      discipline: "",
      project_context: "",
    });
    clearSessionState();
    refreshHealth();
  };

  const applyDocPayload = (payload: {
    doc: SpecDoc;
    open_questions: OpenItem[];
    lint: LintIssue[];
    standards: StandardInfo[];
    profile_complete: boolean;
    baseline_index?: number | null;
    figures?: Figure[];
    suggested_prompts?: string[];
    reference_docs?: ReferenceDocMeta[];
    import_report?: ImportReport | null;
    source_available?: boolean;
    preservation_ready?: boolean;
    source_capabilities?: SourceCapabilitiesState | null;
    template_origin?: TemplateOrigin | null;
  }) => {
    setDoc(payload.doc);
    setOpenItems(payload.open_questions);
    setLintIssues(payload.lint);
    setStandards(payload.standards);
    setProfileComplete(payload.profile_complete);
    setBaselineIndex(payload.baseline_index ?? null);
    setImportReport(payload.import_report ?? null);
    setSourceAvailable(payload.source_available ?? false);
    setPreservationReady(payload.preservation_ready ?? false);
    setSourceCapabilities(payload.source_capabilities ?? null);
    setTemplateOrigin(payload.template_origin ?? null);
    setFigures(payload.figures ?? []);
    setSuggestions(payload.suggested_prompts ?? []);
    setReferenceDocs(payload.reference_docs ?? []);
    setChangedIds(new Set());
  };

  /** Hydrate a tutorial/template transition without assuming whether the
   * backend initially returns a flat payload or a nested doc_payload. */
  const applySessionBundle = (bundle: SessionBundle) => {
    workspaceEpochRef.current += 1;
    const merged = {
      ...bundle,
      ...(bundle.doc_payload ?? {}),
    } as SessionBundle;
    if (merged.doc) {
      applyDocPayload({
        doc: merged.doc,
        open_questions: merged.open_questions ?? [],
        lint: merged.lint ?? [],
        standards: merged.standards ?? [],
        profile_complete: merged.profile_complete ?? false,
        baseline_index: merged.baseline_index ?? null,
        figures: merged.figures ?? [],
        suggested_prompts: merged.suggested_prompts ?? [],
        reference_docs: merged.reference_docs ?? [],
        import_report: merged.import_report ?? null,
        source_available: merged.source_available ?? false,
        preservation_ready: merged.preservation_ready ?? false,
        source_capabilities: merged.source_capabilities ?? null,
        template_origin: merged.template_origin ?? null,
      });
    }
    const transcript = merged.chat ?? merged.messages;
    if (transcript) {
      const rebuilt: ChatMessage[] = transcript.map((message) => ({
        id: newId(),
        role: message.role,
        text: message.text,
      }));
      const assistantPositions = rebuilt
        .map((message, index) => (message.role === "assistant" ? index : -1))
        .filter((index) => index >= 0);
      for (const figure of merged.figures ?? []) {
        const at = assistantPositions[figure.message_index];
        if (at !== undefined) {
          rebuilt[at].figureIds = [
            ...(rebuilt[at].figureIds ?? []),
            figure.fid,
          ];
        }
      }
      setMessages(rebuilt);
    }
    if (merged.research) setResearch(merged.research);
    if (merged.qc) setQc(merged.qc);
    if (merged.readiness) setReadiness(merged.readiness);
    if (merged.usage) setUsage(merged.usage);
    if (merged.health) setHealth(merged.health);
    refreshHealth();
    refreshResearch();
    refreshQc();
    refreshReadiness();
    refreshUsage();
  };

  /** Apply normal stream events emitted by tutorial enrichment. */
  const applyTutorialEvent = (event: TutorialEvent) => {
    switch (event.type) {
      case "doc_patch":
        setDoc(event.doc);
        setChangedIds(new Set(event.ops.map((op) => op.id).filter(Boolean)));
        break;
      case "doc_snapshot":
        setDoc(event.doc);
        break;
      case "open_questions":
        setOpenItems(event.items);
        break;
      case "lint":
        setLintIssues(event.items);
        setStandards(event.standards);
        break;
      case "figure":
        setFigures((current) => [
          ...current.filter((figure) => figure.fid !== event.figure.fid),
          event.figure,
        ]);
        break;
      case "suggested_prompts":
        setSuggestions(event.prompts);
        break;
      case "turn_complete":
        refreshUsage();
        refreshReadiness();
        break;
    }
  };

  const onEditDoc = async (ops: EditOp[]) => {
    // A model turn, file load, or earlier manual mutation owns the server-side
    // tree. Refuse locally rather than queueing an edit against a stale sibling
    // position/version. The visible state disables follow-up controls; the ref
    // is the authoritative same-tick guard.
    if (
      ops.length === 0 ||
      busyRef.current ||
      manualEditBusyRef.current ||
      fileLoadingRef.current
    ) {
      return;
    }
    manualEditBusyRef.current = true;
    setManualEditBusy(true);
    const epoch = workspaceEpochRef.current;
    try {
      const payload = await editDoc(ops, {
        workspaceId: health?.workspace_id,
        generation: health?.generation,
      });
      if (workspaceEpochRef.current !== epoch) return;
      applyDocPayload(payload);
      refreshReadiness();
      refreshQc();
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
    } finally {
      manualEditBusyRef.current = false;
      setManualEditBusy(false);
    }
  };

  const onUndo = async () => {
    const epoch = workspaceEpochRef.current;
    const payload = await undoDoc({
      workspaceId: health?.workspace_id,
      generation: health?.generation,
    }).catch(() => null);
    if (payload && workspaceEpochRef.current === epoch) {
      applyDocPayload(payload);
      refreshReadiness();
      refreshQc();
    }
  };

  const onRedo = async () => {
    const epoch = workspaceEpochRef.current;
    const payload = await redoDoc({
      workspaceId: health?.workspace_id,
      generation: health?.generation,
    }).catch(() => null);
    if (payload && workspaceEpochRef.current === epoch) {
      applyDocPayload(payload);
      refreshReadiness();
      refreshQc();
    }
  };

  // --- Save gate: never discard a session's work without offering to save ---
  // New session and Open project both replace the whole session, so each routes
  // through this gate first (Save / Don't save / Cancel). The only way to lose
  // work becomes explicitly declining the save — the user's rule. Mirrors the
  // native window-close prompt, reusing the same predicate + save machinery.

  /** Native save (pywebview) or, in dev/browser, the download fallback.
   *  Resolves true once a file is actually written (false = cancelled). */
  const saveProjectFile = async (): Promise<boolean> => {
    const api = window.pywebview?.api;
    if (api?.save_project) {
      try {
        return !!(await api.save_project());
      } catch {
        return false;
      }
    }
    // No native bridge (dev/browser): download the project file. We can't
    // observe the browser's own save dialog, so a completed fetch counts as
    // done (and it's awaited, so the reset can't race the save payload).
    try {
      await downloadProjectFile();
      return true;
    } catch {
      return false;
    }
  };

  /** Does the session hold work worth saving? Authoritative server check
   *  (same predicate as the close prompt), with a local fallback if it fails. */
  const isUnsaved = async (): Promise<boolean> => {
    try {
      return await checkUnsaved();
    } catch {
      return messages.length > 0 || figures.length > 0 || hasContent;
    }
  };

  async function doInstantiateTemplate(templateId: string) {
    if (templateStarting) return;
    setTemplateStarting(true);
    try {
      const session = await instantiateTemplate(templateId);
      applySessionBundle(session);
      onboardingRef.current?.syncSessionIdentity(session);
      if (session.template_warning) {
        setImportNotice({
          tone: "warn",
          name: "Reusable template",
          title: "Template started with a compatibility fallback",
          lines: [session.template_warning],
        });
      }
      setNewSessionOpen(false);
      setTemplatesOnly(false);
    } catch (error) {
      setMessages((current) => [
        ...current,
        {
          id: newId(),
          role: "assistant",
          text: `Could not start that template: ${
            error instanceof Error ? error.message : String(error)
          }`,
          error: true,
        },
      ]);
    } finally {
      setTemplateStarting(false);
    }
  }

  const requestStartTemplate = async (templateId: string) => {
    // A tutorial scenario is explicitly disposable and the tutorial base is
    // retained server-side. Starting its ephemeral template must not invoke
    // the ordinary project-discard save gate (which intentionally rejects
    // saves from scenario scope).
    if (health?.workspace_scope === "scenario") {
      void doInstantiateTemplate(templateId);
      return;
    }
    if (await isUnsaved()) {
      setSaveGate({ kind: "start-template", templateId });
    } else {
      void doInstantiateTemplate(templateId);
    }
  };

  const openTemplateStudio = () => {
    setTemplatesOnly(true);
    setNewSessionOpen(true);
  };

  /** Run the pending session-discarding action once the gate resolves. */
  const runGate = (gate: NonNullable<typeof saveGate>) => {
    if (gate.kind === "new-session") {
      setTemplatesOnly(false);
      setNewSessionOpen(true);
    } else if (gate.kind === "open-project") void doLoadProject(gate.file);
    else void doInstantiateTemplate(gate.templateId);
  };

  const onGateSave = async () => {
    const gate = saveGate;
    if (!gate) return;
    const saved = await saveProjectFile();
    setSaveGate(null);
    // Proceed only once a file was written — a cancelled Save dialog keeps the
    // session, so a mis-click behind "Save" can never lose the work.
    if (saved) runGate(gate);
  };

  const onGateDiscard = () => {
    const gate = saveGate;
    setSaveGate(null);
    if (gate) runGate(gate);
  };

  /** Header "New session": offer to save, then show the start choices. */
  const requestNewSession = async () => {
    if (!(await onboarding.abort())) return;
    if (await isUnsaved()) setSaveGate({ kind: "new-session" });
    else {
      setTemplatesOnly(false);
      setNewSessionOpen(true);
    }
  };

  /** The actual project load (after the save gate). Rebuilds the transcript
   *  and re-inlines each figure by its stored message_index. */
  const doLoadProject = async (file: File) => {
    if (fileLoadingRef.current) return;
    fileLoadingRef.current = true;
    // A project carrying a master re-parses and re-indexes it server-side,
    // so opening one is as slow as importing — say so the same way. Any
    // import notice describes the document this replaces, so it goes.
    setImportNotice(null);
    setFileLoading({ kind: "open", name: file.name });
    try {
      const result = await loadProjectFile(file);
      workspaceEpochRef.current += 1;
      applyDocPayload(result);
      // The .baspec carries the original import's content-loss warnings, and
      // with the imported-DOCX banner gone this is the only place they can
      // still surface. Without it, reopening a project silently drops the
      // one honest signal that the extraction left something behind.
      const restored = result.import_report?.warnings ?? [];
      if (restored.length) {
        setImportNotice({
          tone: "warn",
          name: result.import_report?.filename ?? file.name,
          lines: restored,
        });
      }
      // A loaded project can restore legacy module/discipline compatibility
      // fields; resync health while the document remains heading-authoritative.
      refreshHealth();
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
    } finally {
      fileLoadingRef.current = false;
      setFileLoading(null);
    }
  };

  /** Open project (the panel's file picker): offer to save, then load. */
  const onLoadProject = async (file: File) => {
    if (!(await onboarding.abort())) return;
    if (await isUnsaved()) setSaveGate({ kind: "open-project", file });
    else void doLoadProject(file);
  };

  /**
   * Open a file through the native pywebview dialog, when the shell is
   * present. HTML `<input type="file">` is unreliable inside the webview
   * (the dialog opens and a file can be picked, but `input.files` arrives
   * empty, so Open/Import silently did nothing) — mirroring the native Save
   * dialog, the shell reads the picked file and hands its bytes back here.
   *
   * Resolves to a File when the user picked one, `null` when they cancelled,
   * or `undefined` when there is no native bridge — the caller then falls
   * back to the hidden HTML `<input type="file">` (dev / plain browser),
   * where the input works normally.
   */
  const nativeOpenFile = async (
    kind: "project" | "docx" | "reference" | "template",
  ): Promise<File | null | undefined> => {
    const api = window.pywebview?.api;
    if (!api?.open_file) return undefined; // browser/dev → HTML input
    try {
      const picked = await api.open_file(kind);
      if (!picked) return null; // cancelled
      const binary = atob(picked.data_b64);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
      return new File([bytes], picked.name);
    } catch {
      return null; // dialog/read error — treat as a no-op, never throw
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
    editDoc: onEditDoc,
    startResearch: () => void onStartResearch(),
    startQc: () => void onStartQc(),
    prefillComposer: onAskModel,
    openTemplates: openTemplateStudio,
    applySession: applySessionBundle,
    applyTutorialEvent,
    health,
    doc,
    hasContent,
  });
  onboardingRef.current = onboarding;
  const activeDiscipline = projectDiscipline(doc, health?.legacy_discipline);
  const projectHeading = formatProjectHeading(doc, health?.legacy_discipline);

  return (
    <div className="flex h-full flex-col">
      <Header
        health={health}
        projectHeading={projectHeading}
        busy={busy}
        update={update}
        usage={usage}
        onNewSession={() => void requestNewSession()}
        onOpenTemplates={openTemplateStudio}
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
        onStartTutorialAtChapter={(chapterId) => {
          setHelpTopic(null);
          onboarding.startAtChapter(chapterId);
        }}
        health={health}
      />
      <OnboardingOverlay
        ob={onboarding}
        doc={doc}
        busy={busy || manualEditBusy}
        hasContent={hasContent}
        profileComplete={profileComplete}
        researchStatus={research?.status ?? "idle"}
        qcStatus={qc?.status ?? "idle"}
        sourceAvailable={sourceAvailable}
        bumpDrawer={bumpDrawer}
      />
      <NewSessionDialog
        open={newSessionOpen}
        busy={busy || templateStarting}
        hasContent={hasContent}
        templatesOnly={templatesOnly}
        onCancel={() => {
          setNewSessionOpen(false);
          setTemplatesOnly(false);
        }}
        onBlankSlate={() => void startBlankSession()}
        onStartTemplate={(templateId) => void requestStartTemplate(templateId)}
        openTemplateFile={() => nativeOpenFile("template")}
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
            Your project comes back exactly as it was before the tour started
            — the same document, history, and version list — and this practice
            copy is discarded. You can restart the tour anytime from the{" "}
            <b className="text-ink">Tour</b> button in the header.
          </>
        }
        confirmLabel="End tour"
        cancelLabel="Continue tour"
        onConfirm={onboarding.end}
        onCancel={onboarding.cancelEnd}
      />
      <ConfirmDialog
        open={tutorialCloseBlocked}
        elevated
        title="Tutorial work is still settling"
        body="A chat, research, Final QC, import, or edit in the protected tutorial workspace is still active. Return to the app and stop it or let it finish before closing; the original project remains protected."
        confirmLabel="Return to tutorial"
        cancelLabel="Stay open"
        onConfirm={() => setTutorialCloseBlocked(false)}
        onCancel={() => setTutorialCloseBlocked(false)}
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
      {/* In-app save gate: New session / Open project both discard the
          session, so offer to save first (the user's "don't lose content"
          rule). Same 3-way dialog; copy switches on the pending action. */}
      <CloseDialog
        open={saveGate !== null}
        title={
          saveGate?.kind === "open-project"
            ? "Open a different project?"
            : saveGate?.kind === "start-template"
              ? "Start from this template?"
              : "Start a new session?"
        }
        body="You have unsaved work in this session. Save it to a project file first, or continue without saving — this can't be undone."
        saveLabel={
          saveGate?.kind === "open-project"
            ? "Save, then open"
            : saveGate?.kind === "start-template"
              ? "Save, then use template"
              : "Save, then start"
        }
        discardLabel={
          saveGate?.kind === "open-project"
            ? "Open without saving"
            : saveGate?.kind === "start-template"
              ? "Use template without saving"
              : "Start without saving"
        }
        onSave={onGateSave}
        onDiscard={onGateDiscard}
        onCancel={() => setSaveGate(null)}
      />
      <main className="flex min-h-0 flex-1">
        <Chat
          messages={messages}
          busy={busy}
          onSend={send}
          suggestions={suggestions}
          discipline={activeDiscipline}
          onStartOnboarding={onboarding.start}
          onStop={onStop}
          uploading={fileLoading !== null}
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
          importReport={importReport}
          referenceDocs={referenceDocs}
          onAttachReference={onAttachReference}
          onRemoveReference={onRemoveReference}
          referenceBusy={referenceBusy}
          sourceAvailable={sourceAvailable}
          preservationReady={preservationReady}
          sourceCapabilities={sourceCapabilities}
          templateOrigin={templateOrigin}
          tutorialActive={health?.workspace_scope !== undefined && health.workspace_scope !== "original"}
          busy={busy || manualEditBusy || referenceBusy}
          fileLoading={fileLoading}
          importNotice={importNotice}
          onDismissImportNotice={() => setImportNotice(null)}
          onUndo={onUndo}
          onRedo={onRedo}
          onSaveAsTemplate={openTemplateStudio}
          onEditDoc={onEditDoc}
          onLoadProject={onLoadProject}
          nativeOpenFile={nativeOpenFile}
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
