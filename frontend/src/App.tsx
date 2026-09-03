import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  ChatMessage,
  DraftPrerequisites,
  EditOp,
  Figure,
  FileLoading,
  Health,
  ImportNotice,
  ImportReport,
  ReferenceDocMeta,
  LintIssue,
  OpenItem,
  QcApplyPreviewBasis,
  QcApplyPreviewResult,
  QcSnapshot,
  ReadinessPayload,
  ResearchScope,
  ResearchSnapshot,
  SaveOutcome,
  SaveTarget,
  SessionBundle,
  SourceCapabilitiesState,
  SpecDoc,
  StandardInfo,
  TemplateOrigin,
  ReleaseNote,
  UpdateCheckPayload,
  UsageSummary,
  OpenInWordResult,
} from "./types";
import {
  applyQc,
  checkUnsaved,
  checkUpdate,
  getReleaseNotes,
  markReleaseNotesSeen,
  previewQcApply,
  deleteFigure,
  dismissQc,
  downloadProjectFile,
  draftAdapt,
  draftFull,
  detachSource,
  editDoc,
  fetchQcDebrief,
  fetchResearchDebrief,
  getDoc,
  getDocCapabilitiesStatus,
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
import { createLatestAnswer } from "./lib/latestAnswer";
import {
  emptyDebriefQueue,
  rememberDebrief,
  requeueDebrief,
  takeNextDebrief,
} from "./lib/debriefQueue";
import type { DebriefKind, DebriefQueueState } from "./lib/debriefQueue";
import Header from "./components/Header";
import ApiKeyBanner from "./components/ApiKeyBanner";
import Chat from "./components/Chat";
import ArtifactPanel from "./components/ArtifactPanel";
import SettingsPanel from "./components/SettingsPanel";
import { WhatsNewModal } from "./components/WhatsNewModal";
import HelpModal, { type HelpTopic } from "./components/HelpModal";
import OnboardingOverlay from "./components/OnboardingOverlay";
import NewSessionDialog from "./components/NewSessionDialog";
import { sourceCapabilitiesPending } from "./lib/sourceCapabilities";
import { installExternalLinkHandler } from "./lib/externalLinks";
import { consumeTutorialUpdateInvitation } from "./lib/onboardingStorage";
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
import { buildQcApplicationDigest } from "./lib/qcRemediation";
import {
  QC_MILESTONE_TYPES,
  isQcActiveSnapshot,
  mergeQcEvent,
  qcSnapshotRunId,
  reconcileQcSnapshotUpdate,
} from "./lib/qcLive";
import {
  RESEARCH_MILESTONE_TYPES,
  classifyResearchStreamEnd,
  isResearchActiveSnapshot,
  mergeResearchEvent,
  reconcileResearchSnapshotUpdate,
  researchSnapshotRound,
  type ResearchStreamOutcome,
} from "./lib/researchLive";

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
  // "Edit freely": the confirm gate and the in-flight request.
  const [detachConfirmOpen, setDetachConfirmOpen] = useState(false);
  const [detaching, setDetaching] = useState(false);
  const detachingRef = useRef(false);
  const [doc, setDoc] = useState<SpecDoc | null>(null);
  const [openItems, setOpenItems] = useState<OpenItem[]>([]);
  const [lintIssues, setLintIssues] = useState<LintIssue[]>([]);
  const [standards, setStandards] = useState<StandardInfo[]>([]);
  const [profileComplete, setProfileComplete] = useState(false);
  // What "Draft full section" still needs (section / project type / country).
  // Server-derived and never recomputed here — the panel's tooltip and the
  // endpoint's decision must not be able to disagree. Null until first load.
  const [draftPrereqs, setDraftPrereqs] =
    useState<DraftPrerequisites | null>(null);
  const [research, setResearch] = useState<ResearchSnapshot | null>(null);
  const [qc, setQc] = useState<QcSnapshot | null>(null);
  const [readiness, setReadiness] = useState<ReadinessPayload | null>(null);
  const [update, setUpdate] = useState<UpdateCheckPayload | null>(null);
  // Two callers write this: the throttled check at launch, and a forced one
  // from Help. They race — the launch fetch can still be in flight (a slow
  // manifest request waits out an 8s timeout) when the user runs a forced
  // check, and its late THROTTLED/ERROR answer would erase the install
  // control the forced one just produced. The latch orders them by REQUEST,
  // and lives in a ref because the loser can resolve before React commits
  // the winner. Its rule is pinned in tests/latestAnswer.test.ts.
  const updateAnswers = useRef(createLatestAnswer<UpdateCheckPayload | null>());
  // The install request outlives the download; the ref is the double-submit
  // guard (a state update may not commit before a second click lands).
  const [installing, setInstalling] = useState(false);
  const [installError, setInstallError] = useState<string | null>(null);
  const installingRef = useRef(false);
  // Release notes for a version the user has not been shown yet. Non-empty
  // opens the What's-new modal — on mount that means "the app just updated",
  // and from Settings it is an explicit request.
  const [whatsNew, setWhatsNew] = useState<ReleaseNote[]>([]);
  const [settingsOpen, setSettingsOpen] = useState(false);
  // A gentle nudge shown the moment chat, research, or Final QC fails
  // because the stored API key is invalid/expired (never the raw 401 text).
  const [apiKeyErrorOpen, setApiKeyErrorOpen] = useState(false);
  const [helpTopic, setHelpTopic] = useState<HelpTopic | null>(null);
  // Shown when the pywebview shell reports a window-close with unsaved work.
  const [closePromptOpen, setClosePromptOpen] = useState(false);
  const [tutorialCloseBlocked, setTutorialCloseBlocked] = useState(false);
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [changedIds, setChangedIds] = useState<ReadonlySet<string>>(new Set());
  const [baselineIndex, setBaselineIndex] = useState<number | null>(null);
  // The file this session already saved itself to, or null when it never has
  // — which is the whole difference between Save asking where and Save just
  // overwriting. Server-owned (it rides every doc payload): a reset clears it
  // there, so a stale copy kept here could never offer to overwrite the
  // project that was just discarded.
  const [saveTarget, setSaveTarget] = useState<SaveTarget | null>(null);
  const [importReport, setImportReport] = useState<ImportReport | null>(null);
  const [referenceDocs, setReferenceDocs] = useState<ReferenceDocMeta[]>([]);
  const [referenceBusy, setReferenceBusy] = useState(false);
  const [sourceAvailable, setSourceAvailable] = useState(false);
  // Server-owned, never inferred: detaching KEEPS the source bytes and the
  // baseline, so the retained artifacts look identical to an attached
  // document whose capability report has not arrived.
  const [sourceDetached, setSourceDetached] = useState(false);
  const [preservationReady, setPreservationReady] = useState(false);
  // Whether the formatting-preserving export can run (server-derived: it
  // needs the retained original AND its formatting map). Drives the Export
  // menu's primary entry for an imported document.
  const [preservedExportAvailable, setPreservedExportAvailable] =
    useState(false);
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
  // Consumed once per app launch, and owned here rather than in Chat because
  // the chat pane remounts on a new session (see sessionNonce below) — read
  // there, starting a session would quietly retire the notice.
  const [tutorialUpdated] = useState(consumeTutorialUpdateInvitation);
  // Bumped when the session is replaced wholesale, and used as the React key
  // of both panes so their whole subtree is discarded rather than reused.
  // Component-local state below App is otherwise unreachable from the wipe
  // (see clearSessionState), and it holds real content: a fetched compare
  // diff, the review walk's draft text, a half-entered standard, the QC
  // accept-set, the project-profile form, the composer's unsent message.
  // Each pane prefixes it into its own key: the two are static JSX siblings,
  // which React reconciles as an implicit children array, so a bare shared
  // value is a duplicate key in that array — and `clientLog` forwards the
  // resulting console warning to the diagnostics endpoint.
  const [sessionNonce, setSessionNonce] = useState(0);
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
  // Sweep progress from the capabilities poll, for the pending strip —
  // "checking permissions: 412 of 1,500 blocks" instead of anonymous dots
  // for minutes. Null outside a pending sweep.
  const [capabilityProgress, setCapabilityProgress] = useState<{
    done: number;
    total: number;
  } | null>(null);
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
  // Dedup for the gentle auth-error modal (chat, research, QC): a failed
  // snapshot fires it once, not once per poll tick. Checked and cleared
  // inline inside refreshResearch/refreshQc's own callback (never inside a
  // useEffect keyed on the snapshot's fields) — two consecutive fast auth
  // failures can produce byte-identical status/error_kind values, and a
  // ref write alone never causes React to re-run an effect, so an
  // effect-dependency-based reset can silently never re-fire.
  const researchAuthHandledRef = useRef(false);
  const qcAuthHandledRef = useRef(false);
  // Synchronous QC identity for stream/fetch races. React state may batch,
  // but a stale milestone response must be compared with every frame already
  // accepted in this tick before it can drive UI or auth side effects.
  const qcSnapshotRef = useRef<QcSnapshot | null>(null);
  const qcRefreshGenerationRef = useRef(0);
  // The same pair for research, for the same reason: the follower's loop
  // makes decisions between renders (reconnect? accept this fetch?) and must
  // never read a React state value that has not committed yet.
  const researchSnapshotRef = useRef<ResearchSnapshot | null>(null);
  const researchRefreshGenerationRef = useRef(0);
  // The in-flight research stream, so a workspace transition can release the
  // connection instead of leaving a second reader racing the new one.
  const researchStreamRef = useRef<AbortController | null>(null);
  const onboardingRef = useRef<OnboardingApi | null>(null);
  // Every whole-session/tutorial transition advances this epoch. Read calls
  // and streams captured against an older workspace must never repaint the
  // newly hydrated document with discarded scenario state.
  const workspaceEpochRef = useRef(0);
  // Mutation leases are synchronization state, not presentation state. React
  // may not commit a health update before the user's next click, so every
  // accepted health/document/session payload updates this ref synchronously.
  const workspaceLeaseRef = useRef<
    Pick<Health, "workspace_id" | "workspace_scope" | "generation"> | null
  >(null);

  const replaceQcSnapshot = useCallback((snapshot: QcSnapshot | null) => {
    qcSnapshotRef.current = snapshot;
    setQc(snapshot);
  }, []);

  const replaceResearchSnapshot = useCallback(
    (snapshot: ResearchSnapshot | null) => {
      researchSnapshotRef.current = snapshot;
      setResearch(snapshot);
    },
    [],
  );

  /** Advance the workspace epoch and cut loose everything still bound to the
   *  old one. Every session/tutorial/project transition goes through here so
   *  none of this can be forgotten at a new call site.
   *
   *  Clearing the research snapshot is part of the transition, not tidiness.
   *  Research's reconcile identity is the ROUND NUMBER, which says nothing
   *  across workspaces: two projects both sit at round 1, and a restored
   *  project's whole log is a single `research_complete` at seq 0
   *  (`ResearchRunner.restore` empties `events` first). Left pointing at the
   *  outgoing project, the same-round watermark rule would read the incoming
   *  snapshot as stale and reject it — permanently, because a restored run
   *  never streams and nothing else would reset the ref. Clearing IS the
   *  identity reset; reconcile then sees `previous === null` and adopts.
   *  (QC needs no equivalent: its run ids are UUIDs, so two workspaces never
   *  compare as the same run.)
   *
   *  Declared after `replaceResearchSnapshot` deliberately — a `useCallback`
   *  dependency array is evaluated at render time, in declaration order, so
   *  naming a later `const` here is a first-render TDZ crash. */
  /** Completion-debrief queue (research/QC → one auto-sent chat turn).
   *  A ref, not state: the follower loops and the flush effect read and
   *  swap it between renders; `debriefNonce` is the render-visible tick
   *  that re-runs the flush effect when the queue gains an entry or an
   *  attempt settles. */
  const debriefQueueRef = useRef<DebriefQueueState>(emptyDebriefQueue());
  const debriefInFlightRef = useRef(false);
  const [debriefNonce, setDebriefNonce] = useState(0);

  const advanceWorkspaceEpoch = useCallback(() => {
    workspaceEpochRef.current += 1;
    researchStreamRef.current?.abort();
    replaceResearchSnapshot(null);
    // A debrief describes the outgoing workspace; none may survive into the
    // next one — and the fired ledger resets with it, so a loaded project's
    // own round numbers can never collide with tokens the previous session
    // already spent.
    debriefQueueRef.current = emptyDebriefQueue();
  }, [replaceResearchSnapshot]);

  const adoptWorkspaceLease = useCallback(
    (
      payload: Partial<
        Pick<Health, "workspace_id" | "workspace_scope" | "generation">
      >,
    ) => {
      if (
        typeof payload.workspace_id !== "number" ||
        payload.workspace_scope === undefined ||
        typeof payload.generation !== "number"
      ) {
        return true;
      }
      const current = workspaceLeaseRef.current;
      if (
        current !== null &&
        (payload.workspace_id < current.workspace_id ||
          (payload.workspace_id === current.workspace_id &&
            payload.generation < current.generation))
      ) {
        return false;
      }
      workspaceLeaseRef.current = {
        workspace_id: payload.workspace_id,
        workspace_scope: payload.workspace_scope,
        generation: payload.generation,
      };
      return true;
    },
    [],
  );

  const currentWorkspaceLease = useCallback(() => {
    const lease = workspaceLeaseRef.current;
    return {
      workspaceId: lease?.workspace_id,
      generation: lease?.generation,
    };
  }, []);

  const refreshHealth = useCallback(() => {
    const epoch = workspaceEpochRef.current;
    getHealth()
      .then((value) => {
        if (workspaceEpochRef.current === epoch) {
          if (!adoptWorkspaceLease(value)) return;
          setHealth(value);
        }
      })
      .catch(() => {
        if (workspaceEpochRef.current === epoch) setHealth(null);
      });
  }, [adoptWorkspaceLease]);

  const acceptResearchSnapshot = useCallback((
    value: ResearchSnapshot,
    requestGeneration = researchRefreshGenerationRef.current,
  ) => {
    const decision = reconcileResearchSnapshotUpdate(
      researchSnapshotRef.current,
      value,
      {
        requestGeneration,
        currentGeneration: researchRefreshGenerationRef.current,
      },
    );
    if (!decision.accepted) return null;
    const accepted = decision.snapshot;
    replaceResearchSnapshot(accepted);
    // Runs on every ACCEPTED fetch, not gated by React's effect-dependency
    // diffing — so a second fast auth failure (identical status/error_kind
    // to the first) still opens the modal, as long as onStartResearch
    // cleared the ref for this attempt. A rejected stale response drives
    // nothing: its `failed` may belong to a round the user already left.
    if (accepted.status !== "failed") {
      researchAuthHandledRef.current = false;
    } else if (
      accepted.error_kind === "auth_error" &&
      !researchAuthHandledRef.current
    ) {
      researchAuthHandledRef.current = true;
      setApiKeyErrorOpen(true);
    }
    return accepted;
  }, [replaceResearchSnapshot]);

  const refreshResearch = useCallback(() => {
    const epoch = workspaceEpochRef.current;
    const requestGeneration = researchRefreshGenerationRef.current;
    getResearchStatus()
      .then((value) => {
        if (workspaceEpochRef.current !== epoch) return;
        acceptResearchSnapshot(value, requestGeneration);
      })
      .catch(() => {
        // A transient milestone fetch must not erase the locally merged
        // board — the follower reconciles again at the next milestone or
        // when the stream ends. (This used to null the snapshot, which
        // stranded a live run on one dropped poll.)
      });
  }, [acceptResearchSnapshot]);

  const refreshDoc = useCallback(() => {
    const epoch = workspaceEpochRef.current;
    getDoc()
      .then((payload) => {
        if (workspaceEpochRef.current !== epoch) return;
        if (!adoptWorkspaceLease(payload)) return;
        setDoc(payload.doc);
        setOpenItems(payload.open_questions);
        setLintIssues(payload.lint);
        setStandards(payload.standards);
        setProfileComplete(payload.profile_complete);
        setDraftPrereqs(payload.draft_prerequisites ?? null);
        setBaselineIndex(payload.baseline_index ?? null);
        setImportReport(payload.import_report ?? null);
        setSourceAvailable(payload.source_available ?? false);
        setSourceDetached(payload.source_detached ?? false);
        setPreservationReady(payload.preservation_ready ?? false);
    setPreservedExportAvailable(payload.preserved_export_available ?? false);
        setSourceCapabilities(payload.source_capabilities ?? null);
        setTemplateOrigin(payload.template_origin ?? null);
        setFigures(payload.figures ?? []);
        setSuggestions(payload.suggested_prompts ?? []);
        setReferenceDocs(payload.reference_docs ?? []);
      })
      .catch(() => {
        if (workspaceEpochRef.current === epoch) setDoc(null);
      });
  }, [adoptWorkspaceLease]);

  const acceptQcSnapshot = useCallback((
    value: QcSnapshot,
    requestGeneration = qcRefreshGenerationRef.current,
  ) => {
    const decision = reconcileQcSnapshotUpdate(qcSnapshotRef.current, value, {
      requestGeneration,
      currentGeneration: qcRefreshGenerationRef.current,
    });
    if (!decision.accepted) return null;
    const accepted = decision.snapshot;
    replaceQcSnapshot(accepted);
    // Authentication handling runs for every fetch, not from an effect: two
    // consecutive failures can otherwise have identical React dependencies.
    if (accepted.status !== "failed") {
      qcAuthHandledRef.current = false;
    } else {
      const kind = accepted.error_kind || accepted.latest_attempt?.error_kind;
      if (kind === "auth_error" && !qcAuthHandledRef.current) {
        qcAuthHandledRef.current = true;
        setApiKeyErrorOpen(true);
      }
    }
    return accepted;
  }, [replaceQcSnapshot]);

  const refreshQc = useCallback(() => {
    const epoch = workspaceEpochRef.current;
    const requestGeneration = qcRefreshGenerationRef.current;
    getQcStatus()
      .then((value) => {
        if (workspaceEpochRef.current !== epoch) return;
        acceptQcSnapshot(value, requestGeneration);
      })
      .catch(() => {
        // A transient milestone fetch must not erase the locally merged board.
        // The stream follower will reconcile again at the next milestone/end.
      });
  }, [acceptQcSnapshot]);

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

  /**
   * Ask the server about updates and keep only the newest answer.
   *
   * Returns the payload so a caller (Help → About) can phrase its own
   * message, while the app owns the state the header renders from — one
   * answer, one owner, no second copy to fall out of step.
   */
  const runUpdateCheck = useCallback(
    async (force: boolean): Promise<UpdateCheckPayload> => {
      const gate = updateAnswers.current;
      const rank = gate.next();
      try {
        const payload = await checkUpdate(force);
        gate.accept(rank, payload, setUpdate);
        return payload;
      } catch (e) {
        gate.accept(rank, null, setUpdate);
        throw e;
      }
    },
    [],
  );

  useEffect(() => {
    refreshHealth();
    refreshDoc();
    refreshResearch();
    refreshQc();
    refreshReadiness();
    refreshUsage();
    // Throttled auto-check (server enforces once a day); failures ignored.
    // Sequenced, so a slow response cannot land on top of a forced check
    // the user ran from Help while this one was still outstanding.
    void runUpdateCheck(false).catch(() => {});
    // Did this launch follow an update? The server decides (a fresh install
    // gets nothing); anything pending opens the What's-new modal once.
    getReleaseNotes()
      .then((payload) => {
        if (payload.pending) setWhatsNew(payload.entries);
      })
      .catch(() => {});
  }, [
    refreshHealth,
    refreshDoc,
    refreshResearch,
    refreshQc,
    refreshReadiness,
    refreshUsage,
    runUpdateCheck,
  ]);

  // Every external link (chat citations, report sources, the trust dossier,
  // the update banner, ...) opens in the system browser, never inside the
  // native window — one delegated listener covers every renderer.
  useEffect(() => installExternalLinkHandler(), []);

  /** Settings → "What's new": this version's notes, seen or not. */
  const openReleaseNotes = useCallback(() => {
    getReleaseNotes(true)
      .then((payload) => setWhatsNew(payload.entries))
      .catch(() => {});
  }, []);

  /**
   * Close the modal and remember it. Recording the version on every close —
   * including one opened deliberately from Settings — is the honest reading:
   * the user has now seen these notes either way, and the alternative would
   * re-open them unprompted on the next launch.
   */
  const dismissReleaseNotes = useCallback(() => {
    setWhatsNew([]);
    markReleaseNotesSeen().catch(() => {});
  }, []);

  const bumpDrawer = useCallback((name: DrawerName) => {
    setDrawerNonces((previous) => ({
      ...previous,
      [name]: previous[name] + 1,
    }));
  }, []);

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
      // status_only: the tick reads status + progress and nothing else, so
      // it must not ship the multi-MB per-element map once per tick for the
      // whole (possibly minutes-long) sweep.
      getDocCapabilitiesStatus()
        .then((report) => {
          if (cancelled) return;
          if (report?.status === "pending") {
            setCapabilityProgress(report.progress ?? null);
            delay = Math.min(delay * 1.5, 5000);
            timer = window.setTimeout(tick, delay);
            return;
          }
          // Settled. One writer for this state: refreshDoc re-reads the whole
          // payload (the document can have moved while the sweep ran) and
          // sets source_capabilities from the same response.
          setCapabilityProgress(null);
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

  /** Follow the QC run's SSE stream. Chatty worker frames merge locally;
   * authoritative snapshots are fetched only at milestones/end. If transport
   * closes before an active run settles, reconnect to the replayable log. */
  /** Remember a LIVE run completion for the auto-debrief. Only the follower
   *  loops call this — a project-load restore never enters them, so a
   *  restored result can never auto-spend a turn. Replays (an SSE reconnect
   *  replays the terminal frame) dedupe inside the queue by token. */
  const rememberCompletionDebrief = useCallback(
    (kind: DebriefKind, token: string) => {
      const next = rememberDebrief(debriefQueueRef.current, {
        kind,
        token,
        epoch: workspaceEpochRef.current,
      });
      if (next !== debriefQueueRef.current) {
        debriefQueueRef.current = next;
        setDebriefNonce((n) => n + 1);
      }
    },
    [],
  );

  const followQc = useCallback(async () => {
    if (qcFollowRef.current) return;
    qcFollowRef.current = true;
    const epoch = workspaceEpochRef.current;
    try {
      let reconnect = true;
      while (reconnect && workspaceEpochRef.current === epoch) {
        reconnect = false;
        let streamStatus: string | undefined;
        try {
          for await (const event of streamQc()) {
            if (event.type === "stream_end") {
              streamStatus = event.status;
              continue;
            }
            if (workspaceEpochRef.current !== epoch) break;
            if (
              event.type === "qc_started" &&
              qcSnapshotRunId(qcSnapshotRef.current) !== event.run_id
            ) {
              qcRefreshGenerationRef.current += 1;
            }
            replaceQcSnapshot(mergeQcEvent(qcSnapshotRef.current, event));
            if (QC_MILESTONE_TYPES.has(event.type)) {
              refreshQc();
              refreshUsage();
            }
            // A live run finishing (complete OR partial — the runner emits
            // qc_complete for both) queues the auto-debrief chat turn.
            // qc_failed and a stop never do.
            if (event.type === "qc_complete") {
              rememberCompletionDebrief("qc", event.run_id ?? "latest");
            }
          }
        } catch {
          // The status probe below decides whether this transport close needs
          // a reconnect or the server has genuinely settled.
        }

        if (
          workspaceEpochRef.current !== epoch ||
          streamStatus === "superseded" ||
          streamStatus === "complete" ||
          streamStatus === "failed"
        ) {
          break;
        }
        try {
          const requestGeneration = qcRefreshGenerationRef.current;
          const latest = await getQcStatus();
          if (workspaceEpochRef.current !== epoch) break;
          const accepted = acceptQcSnapshot(latest, requestGeneration);
          reconnect = isQcActiveSnapshot(accepted ?? qcSnapshotRef.current);
        } catch {
          // A transient status failure should not strand a locally active run
          // after the stream transport closes. Keep following until an
          // authoritative terminal snapshot or terminal sentinel arrives.
          reconnect = isQcActiveSnapshot(qcSnapshotRef.current);
        }
        if (reconnect) {
          await new Promise<void>((resolve) => window.setTimeout(resolve, 500));
        }
      }
    } finally {
      qcFollowRef.current = false;
      refreshQc();
      refreshReadiness();
      refreshUsage();
    }
  }, [
    acceptQcSnapshot,
    refreshQc,
    refreshReadiness,
    refreshUsage,
    rememberCompletionDebrief,
    replaceQcSnapshot,
  ]);

  const onStartQc = useCallback(async (acknowledgeScopeMismatch = false) => {
    try {
      await startQc(acknowledgeScopeMismatch, currentWorkspaceLease());
      // Clear the auth-modal dedup ref for this fresh attempt — see its
      // declaration comment: refreshQc (not an effect) is what actually
      // reopens the modal, so this reset is read on the very next poll.
      qcAuthHandledRef.current = false;
      // Invalidate every status request that began before this successful
      // start. Run ids are intentionally opaque and event seq restarts at 0.
      qcRefreshGenerationRef.current += 1;
      // A confirmed run is the show. Expand once now; if the user later
      // collapses the drawer, no status effect opens it again.
      bumpDrawer("qc");
      addNote("Sent to Final QC — findings will appear in the Final QC panel.");
      void followQc();
    } catch (e) {
      replaceQcSnapshot({
        ...qcSnapshotRef.current,
        status: "failed",
        error: e instanceof Error ? e.message : String(e),
        events: qcSnapshotRef.current?.events ?? [],
        module_section_compatibility:
          e instanceof QcStartError && e.moduleSectionCompatibility
            ? e.moduleSectionCompatibility
            : qcSnapshotRef.current?.module_section_compatibility,
      });
    }
  }, [followQc, addNote, bumpDrawer, replaceQcSnapshot, currentWorkspaceLease]);

  /** Stop Final QC while its worker preserves any completed paid activity. */
  const onStopQc = useCallback(async () => {
    try {
      await stopQc(currentWorkspaceLease());
    } catch {
      // Best-effort — the run may have already settled on its own.
    } finally {
      refreshQc();
      refreshReadiness();
    }
  }, [refreshQc, refreshReadiness, currentWorkspaceLease]);

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

  const onPreviewQc = useCallback(
    (findingIds: string[]): Promise<QcApplyPreviewResult> =>
      previewQcApply(findingIds, currentWorkspaceLease()),
    [currentWorkspaceLease],
  );

  const onApplyQc = useCallback(
    async (
      findingIds: string[],
      previewBasis?: QcApplyPreviewBasis,
    ) => {
      const epoch = workspaceEpochRef.current;
      const findingContext = Object.fromEntries(
        (qc?.result?.findings ?? []).map((finding) => [
          finding.finding_id,
          {
            title: finding.title,
            issue: finding.issue,
            severity: finding.severity,
            element_id: finding.element_id,
          },
        ]),
      );
      try {
        const payload = await applyQc(
          findingIds,
          currentWorkspaceLease(),
          previewBasis,
        );
        if (workspaceEpochRef.current !== epoch) return;
        if (!applyDocPayload(payload)) return;
        refreshQc();
        refreshReadiness();
        const digest = buildQcApplicationDigest(
          payload.outcomes,
          findingContext,
        );
        setMessages((prev) => [
          ...prev,
          {
            id: newId(),
            role: "assistant",
            text: digest.text,
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
            text: `Could not confirm the Final QC application result. The document was refreshed; review it before retrying: ${
              e instanceof Error ? e.message : String(e)
            }`,
            error: true,
          },
        ]);
      }
    },
    // applyDocPayload is stable in practice; listing it is noise.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [refreshQc, refreshReadiness, refreshDoc, qc, currentWorkspaceLease],
  );

  const onDismissQc = useCallback(
    async (findingId: string, reason: string) => {
      const epoch = workspaceEpochRef.current;
      try {
        const snapshot = await dismissQc(
          findingId,
          reason,
          currentWorkspaceLease(),
        );
        if (workspaceEpochRef.current !== epoch) return;
        replaceQcSnapshot(snapshot);
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
    [refreshQc, refreshReadiness, replaceQcSnapshot, currentWorkspaceLease],
  );

  /**
   * Open the section in Word through the native shell: the export is
   * written to a temporary file and handed to the default .docx app. The
   * app cannot draw Word's layout in its panel; this is how the real
   * formatting is seen. Resolves to the shell's result in every case — a
   * missing bridge (plain browser) is reported, never thrown.
   */
  const onOpenInWord = useCallback(
    async (mode: "preserved" | "normalized"): Promise<OpenInWordResult> => {
      const api = window.pywebview?.api;
      if (!api?.open_in_word) {
        return {
          ok: false,
          error: "Open in Word is available in the desktop app only.",
          path: "",
          name: "",
        };
      }
      try {
        return await api.open_in_word(mode);
      } catch (e) {
        return {
          ok: false,
          error: e instanceof Error ? e.message : String(e),
          path: "",
          name: "",
        };
      }
    },
    [],
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
        if (!applyDocPayload(result)) return;
        // Import advances the original session generation. Keep the full
        // presentation Health object on the same accepted lease immediately
        // so a tutorial click cannot issue the now-stale pre-import lease;
        // then refresh the remaining health fields authoritatively.
        setHealth((current) =>
          current === null
            ? current
            : {
                ...current,
                workspace_id: result.workspace_id,
                workspace_scope: result.workspace_scope,
                generation: result.generation,
              },
        );
        refreshHealth();
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
    [refreshHealth, refreshReadiness],
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
      const { reference_docs, warnings } = await uploadReference(
        file,
        currentWorkspaceLease(),
      );
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
  }, [currentWorkspaceLease]);

  const onRemoveReference = useCallback(async (rid: string) => {
    setReferenceBusy(true);
    try {
      const result = await deleteReference(rid, currentWorkspaceLease());
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
  }, [currentWorkspaceLease]);

  /**
   * Download + verify + launch the installer.
   *
   * The request runs for as long as the download takes, so the pending
   * state is not decoration: without it a click on a slow connection looks
   * like a control that does nothing, which is exactly how a working
   * installer gets reported as broken. The failure is surfaced twice on
   * purpose — inline wherever it was pressed, and in the chat, which is
   * what remains visible after a dialog closes.
   */
  const onInstallUpdate = useCallback(async () => {
    if (installingRef.current) return;
    installingRef.current = true;
    setInstalling(true);
    setInstallError(null);
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
      const message = e instanceof Error ? e.message : String(e);
      setInstallError(message);
      setMessages((prev) => [
        ...prev,
        {
          id: newId(),
          role: "assistant",
          text: `Update failed: ${message}`,
          error: true,
        },
      ]);
    } finally {
      installingRef.current = false;
      setInstalling(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** Follow the SSE stream of a running research. Live events merge into
   *  the local snapshot the moment they arrive (the agent board repaints
   *  per frame); the authoritative snapshot is refetched only on milestone
   *  events and when the stream ends. If transport closes before the run
   *  settles, reconnect to the replayable log — the same loop `followQc`
   *  runs, and the reason a 30-minute run used to freeze mid-board with no
   *  way back short of a reload. */
  const followResearch = useCallback(async () => {
    if (researchFollowRef.current) return;
    researchFollowRef.current = true;
    const epoch = workspaceEpochRef.current;
    const controller = new AbortController();
    researchStreamRef.current = controller;
    try {
      let reconnect = true;
      while (reconnect && workspaceEpochRef.current === epoch) {
        reconnect = false;
        let outcome: ResearchStreamOutcome = "interrupted";
        try {
          for await (const evt of streamResearch(controller.signal)) {
            if (evt.type === "stream_end") {
              outcome = classifyResearchStreamEnd(evt.status);
              continue;
            }
            if (workspaceEpochRef.current !== epoch) break;
            // A `research_started` for a different round is a new world:
            // invalidate any refresh still in flight for the old one before
            // this frame's own milestone refetch goes out. (A restart of the
            // SAME round number — the runner reuses it after a stop — is
            // covered by the bump in onStartResearch.)
            if (
              evt.type === "research_started" &&
              typeof evt.round === "number" &&
              evt.round > 0 &&
              researchSnapshotRound(researchSnapshotRef.current) !== evt.round
            ) {
              researchRefreshGenerationRef.current += 1;
            }
            replaceResearchSnapshot(
              mergeResearchEvent(researchSnapshotRef.current, evt),
            );
            if (RESEARCH_MILESTONE_TYPES.has(evt.type)) refreshResearch();
            // Only a live round SUCCEEDING queues the auto-debrief —
            // research_failed (which includes a stop) never does.
            if (evt.type === "research_complete") {
              rememberCompletionDebrief(
                "research",
                `round-${evt.round ?? 0}`,
              );
            }
          }
        } catch {
          // The status probe below decides whether this transport close
          // needs a reconnect or the server has genuinely settled.
        }

        if (
          workspaceEpochRef.current !== epoch ||
          controller.signal.aborted ||
          outcome === "terminal" ||
          outcome === "superseded"
        ) {
          break;
        }
        try {
          const requestGeneration = researchRefreshGenerationRef.current;
          const latest = await getResearchStatus();
          if (workspaceEpochRef.current !== epoch) break;
          const accepted = acceptResearchSnapshot(latest, requestGeneration);
          reconnect = isResearchActiveSnapshot(
            accepted ?? researchSnapshotRef.current,
          );
        } catch {
          // A transient status failure should not strand a locally active
          // run after the transport closed. Keep following until an
          // authoritative terminal snapshot or terminal sentinel arrives.
          reconnect = isResearchActiveSnapshot(researchSnapshotRef.current);
        }
        if (reconnect) {
          await new Promise<void>((resolve) => window.setTimeout(resolve, 500));
        }
      }
    } finally {
      if (researchStreamRef.current === controller) {
        researchStreamRef.current = null;
      }
      researchFollowRef.current = false;
      refreshResearch();
      refreshUsage();
    }
  }, [
    acceptResearchSnapshot,
    refreshResearch,
    refreshUsage,
    rememberCompletionDebrief,
    replaceResearchSnapshot,
  ]);

  const onStartResearch = useCallback(async (scope: ResearchScope = "all") => {
    try {
      await startResearch(currentWorkspaceLease(), scope);
      // Clear the auth-modal dedup ref for this fresh attempt — see
      // researchAuthHandledRef's declaration comment: refreshResearch (not
      // an effect) is what actually reopens the modal.
      researchAuthHandledRef.current = false;
      // A new round invalidates every refresh already in flight. This is
      // the bump that covers a restart of the SAME round number, which the
      // runner produces whenever the previous round was stopped or failed
      // (it numbers from profile_result.round_count, and a discarded round
      // never advances it) — so round identity alone cannot see it.
      researchRefreshGenerationRef.current += 1;
      // Open the drawer onto the live agent board — the run is the show.
      bumpDrawer("research");
      addNote(
        scope === "gaps"
          ? "Retrying the incomplete research areas — progress in the Research panel."
          : "Started requirements research — progress in the Research panel.",
      );
      void followResearch();
    } catch (e) {
      replaceResearchSnapshot({
        status: "failed",
        error: e instanceof Error ? e.message : String(e),
        events: researchSnapshotRef.current?.events ?? [],
        // Coverage is what the drawer's retry control is built from; a
        // refused start has not changed it, so carry it rather than
        // dropping the control until the next successful poll.
        coverage: researchSnapshotRef.current?.coverage,
      });
    }
  }, [
    followResearch,
    addNote,
    bumpDrawer,
    replaceResearchSnapshot,
    currentWorkspaceLease,
  ]);

  /** Stop the running research fan-out (confirmed in the drawer — loses progress). */
  const onStopResearch = useCallback(async () => {
    try {
      await stopResearch(currentWorkspaceLease());
    } catch {
      // Best-effort — the run may have already settled on its own.
    } finally {
      refreshResearch();
    }
  }, [refreshResearch, currentWorkspaceLease]);

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
        } else if (evt.type === "qc_dispositions") {
          // apply_qc_fixes committed dispositions with this turn — pull the
          // fresh finding statuses into the drawer/report immediately.
          refreshQc();
          refreshReadiness();
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
          if (evt.kind === "auth_error") setApiKeyErrorOpen(true);
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

  /**
   * Run the full-draft click as a normal turn.
   *
   * The server decides which turn it is: the draft directive when the
   * section, project type, and country are known, otherwise a turn that
   * collects the missing ones. Both are ordinary user messages on the one
   * chat path, so nothing here branches — the message the user sees in the
   * transcript says which they got.
   */
  const onDraftFull = async () => {
    if (busyRef.current || manualEditBusyRef.current) return;
    try {
      const { message } = await draftFull();
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

  /** The import counterpart of onDraftFull: one click walks the whole
   *  imported starter against this project (gap-and-adapt), riding the same
   *  ordinary chat path. */
  const onDraftAdapt = async () => {
    if (busyRef.current || manualEditBusyRef.current) return;
    try {
      const { message } = await draftAdapt();
      await send(message);
    } catch (e) {
      setMessages((prev) => [
        ...prev,
        {
          id: newId(),
          role: "assistant",
          text: `Could not start the adapt pass: ${
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
        const remaining = await deleteFigure(fid, currentWorkspaceLease());
        if (workspaceEpochRef.current === epoch) setFigures(remaining);
      } catch {
        refreshDoc();
      }
    },
    [refreshDoc, currentWorkspaceLease],
  );

  /** The active workspace is a tutorial one, so the session on screen is a
   *  protected practice copy rather than the user's own project. Unknown
   *  health reads as "not protected", matching requestStartTemplate: the
   *  conservative answer for a failed health fetch is to treat the session
   *  as the user's real work. */
  const inProtectedWorkspace =
    health?.workspace_scope !== undefined &&
    health.workspace_scope !== "original";

  /** Discard everything the panes own, for a whole-session replacement.
   *
   *  App can clear its own state, but the panel, the drawers and the composer
   *  own plenty of theirs — a half-typed standards edit, the review walk's
   *  cursor and draft text, the compare view's fetched diff, the QC accept-set
   *  and dismiss rationale, the project-profile form, the unsent composer
   *  message. None of it is reachable from here, and the list grows with every
   *  feature, so remounting the subtree is the only form of this that stays
   *  true on its own.
   *
   *  Called by the three replacements the save gate protects — New session,
   *  Open project, Start from template — and deliberately NOT by tutorial
   *  transitions, which swap a disposable practice copy the tour is still
   *  driving. That line is not arbitrary: a scenario template start bypasses
   *  the save gate for exactly the same reason (see requestStartTemplate).
   */
  const discardPaneState = () => {
    setSessionNonce((n) => n + 1);
    // Drawer-open nonces travel as props, and each drawer opens itself when
    // its nonce is non-zero — which a fresh mount evaluates too. Left as they
    // were, every drawer the old session opened would spring open on the new
    // one. Zero is the "nobody has asked" value the effects already guard on.
    setDrawerNonces({ review: 0, research: 0, qc: 0, openItems: 0 });
    // A composer prefill staged by the old session's review queue. Nonce 0 is
    // what makes the remounted Composer ignore it instead of re-prefilling.
    setPrefill({ text: "", nonce: 0 });
  };

  /** Shared post-reset clear+refresh — every session-start path runs this.
   *
   *  The server has already wiped its half (``SessionState.reset``); this is
   *  the client's, and it clears SYNCHRONOUSLY rather than waiting for the
   *  refetches below. Two reasons, and the second is the one that matters:
   *  a refetch lands a frame or more later, so anything left set keeps the
   *  previous project on screen in the meantime — and `refreshResearch` /
   *  `refreshQc` deliberately do NOTHING on a failed fetch (a dropped poll
   *  must not erase a live run's board), so one failed request would leave
   *  the old project's findings sitting there indefinitely.
   */
  const clearSessionState = () => {
    // Cuts loose the research stream and snapshot bound to the old workspace.
    advanceWorkspaceEpoch();
    discardPaneState();
    setMessages([]);
    setDoc(null);
    setOpenItems([]);
    setLintIssues([]);
    setStandards([]);
    setProfileComplete(false);
    // The old session's identity is gone; the refreshDoc below re-derives the
    // gate. Until it lands, null reads as "not yet known", never as "ready".
    setDraftPrereqs(null);
    setChangedIds(new Set());
    // The outgoing session's file. The refreshDoc below re-reads the server's
    // (cleared) answer; until it lands, null is the safe reading — Save asks.
    setSaveTarget(null);
    setBaselineIndex(null);
    setFigures([]);
    setSuggestions([]);
    setReferenceDocs([]);
    setImportReport(null);
    // The notice describes the import (or reference/template failure) of the
    // document being discarded — same reasoning as the project-open path.
    setImportNotice(null);
    setSourceAvailable(false);
    setSourceDetached(false);
    setPreservationReady(false);
    setSourceCapabilities(null);
    setTemplateOrigin(null);
    // Findings, quoted provision text and spend from the previous project.
    // Research is already cleared by advanceWorkspaceEpoch (its reconcile
    // identity is the round number, so clearing IS the identity reset); QC
    // is not, because run ids are UUIDs and it needs no identity reset — but
    // it still has a report on screen that belongs to the old document.
    replaceQcSnapshot(null);
    setReadiness(null);
    // The previous session's meter (spend pill + context gauge) must not
    // linger over a fresh session; clear immediately, then refetch the
    // server's zeroed snapshot.
    setUsage(null);
    refreshDoc();
    refreshUsage();
    refreshResearch();
    refreshQc();
    refreshReadiness();
  };

  /** Start a truly neutral session; chat establishes all project identity. */
  const startBlankSession = async () => {
    setNewSessionOpen(false);
    setTemplatesOnly(false);
    const lease = await resetSession({
      module_id: "generic",
      discipline: "",
      project_context: "",
    });
    if (!adoptWorkspaceLease(lease)) return;
    clearSessionState();
    refreshHealth();
  };

  const applyDocPayload = (payload: {
    workspace_id?: number;
    workspace_scope?: "original" | "tutorial" | "scenario";
    generation?: number;
    doc: SpecDoc;
    open_questions: OpenItem[];
    lint: LintIssue[];
    standards: StandardInfo[];
    profile_complete: boolean;
    draft_prerequisites?: DraftPrerequisites | null;
    project_save_target?: SaveTarget | null;
    baseline_index?: number | null;
    figures?: Figure[];
    suggested_prompts?: string[];
    reference_docs?: ReferenceDocMeta[];
    import_report?: ImportReport | null;
    source_available?: boolean;
    source_detached?: boolean;
    preservation_ready?: boolean;
    preserved_export_available?: boolean;
    source_capabilities?: SourceCapabilitiesState | null;
    template_origin?: TemplateOrigin | null;
  }): boolean => {
    if (!adoptWorkspaceLease(payload)) return false;
    setDoc(payload.doc);
    setOpenItems(payload.open_questions);
    setLintIssues(payload.lint);
    setStandards(payload.standards);
    setProfileComplete(payload.profile_complete);
    setDraftPrereqs(payload.draft_prerequisites ?? null);
    setSaveTarget(payload.project_save_target ?? null);
    setBaselineIndex(payload.baseline_index ?? null);
    setImportReport(payload.import_report ?? null);
    setSourceAvailable(payload.source_available ?? false);
    setSourceDetached(payload.source_detached ?? false);
    setPreservationReady(payload.preservation_ready ?? false);
    setPreservedExportAvailable(payload.preserved_export_available ?? false);
    setSourceCapabilities(payload.source_capabilities ?? null);
    setTemplateOrigin(payload.template_origin ?? null);
    setFigures(payload.figures ?? []);
    setSuggestions(payload.suggested_prompts ?? []);
    setReferenceDocs(payload.reference_docs ?? []);
    setChangedIds(new Set());
    return true;
  };

  /** Hydrate a tutorial/template transition without assuming whether the
   * backend initially returns a flat payload or a nested doc_payload. */
  const applySessionBundle = (bundle: SessionBundle): boolean => {
    const merged = {
      ...bundle,
      ...(bundle.doc_payload ?? {}),
    } as SessionBundle;
    // A superseded transition must not advance the epoch or repaint any of
    // the document, transcript, runner, or health state it carried.
    if (!adoptWorkspaceLease(merged)) return false;
    advanceWorkspaceEpoch();
    if (merged.doc) {
      const accepted = applyDocPayload({
        workspace_id: merged.workspace_id,
        workspace_scope: merged.workspace_scope,
        generation: merged.generation,
        doc: merged.doc,
        open_questions: merged.open_questions ?? [],
        lint: merged.lint ?? [],
        standards: merged.standards ?? [],
        profile_complete: merged.profile_complete ?? false,
        draft_prerequisites: merged.draft_prerequisites ?? null,
        project_save_target: merged.project_save_target ?? null,
        baseline_index: merged.baseline_index ?? null,
        figures: merged.figures ?? [],
        suggested_prompts: merged.suggested_prompts ?? [],
        reference_docs: merged.reference_docs ?? [],
        import_report: merged.import_report ?? null,
        source_available: merged.source_available ?? false,
        // Forwarded explicitly, like every other source field: the import
        // tutorial's practice copy arrives detached, and a bundle that
        // dropped the flag hydrated it as "source-backed, report missing" —
        // every editing control disabled on the one copy that exists to be
        // edited (Codex, PR #145). tests/sessionBundle.test.ts pins that
        // this mapping names every field applyDocPayload accepts.
        source_detached: merged.source_detached ?? false,
        preservation_ready: merged.preservation_ready ?? false,
        preserved_export_available:
          merged.preserved_export_available ?? false,
        source_capabilities: merged.source_capabilities ?? null,
        template_origin: merged.template_origin ?? null,
      });
      if (!accepted) return false;
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
    if (merged.research) replaceResearchSnapshot(merged.research);
    if (merged.qc) replaceQcSnapshot(merged.qc);
    if (merged.readiness) setReadiness(merged.readiness);
    if (merged.usage) setUsage(merged.usage);
    if (merged.health) {
      if (adoptWorkspaceLease(merged.health)) setHealth(merged.health);
    }
    refreshHealth();
    refreshResearch();
    refreshQc();
    refreshReadiness();
    refreshUsage();
    return true;
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
      const payload = await editDoc(ops, currentWorkspaceLease());
      if (workspaceEpochRef.current !== epoch) return;
      if (!applyDocPayload(payload)) return;
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

  /**
   * Give up source-preserving export so this imported document can be edited.
   *
   * Gated behind a confirm because it is one-way for this document: the
   * export contract changes from "a byte-exact patched copy of your upload"
   * to "a normalized Word file". Nothing is lost — the exact original stays
   * downloadable and redline vs master keeps working — but that is a promise
   * the user should make deliberately, not discover afterwards.
   */
  const doDetachSource = async () => {
    setDetachConfirmOpen(false);
    if (detachingRef.current || busyRef.current || fileLoadingRef.current) return;
    detachingRef.current = true;
    setDetaching(true);
    const epoch = workspaceEpochRef.current;
    try {
      const payload = await detachSource(currentWorkspaceLease());
      if (workspaceEpochRef.current !== epoch) return;
      if (!applyDocPayload(payload)) return;
      refreshReadiness();
      refreshQc();
    } catch (e) {
      refreshDoc();
      setMessages((prev) => [
        ...prev,
        {
          id: newId(),
          role: "assistant",
          text: `Could not switch to free editing: ${
            e instanceof Error ? e.message : String(e)
          }`,
          error: true,
        },
      ]);
    } finally {
      detachingRef.current = false;
      setDetaching(false);
    }
  };

  const onUndo = async () => {
    const epoch = workspaceEpochRef.current;
    const payload = await undoDoc(currentWorkspaceLease()).catch(() => null);
    if (payload && workspaceEpochRef.current === epoch) {
      if (!applyDocPayload(payload)) return;
      refreshReadiness();
      refreshQc();
    }
  };

  const onRedo = async () => {
    const epoch = workspaceEpochRef.current;
    const payload = await redoDoc(currentWorkspaceLease()).catch(() => null);
    if (payload && workspaceEpochRef.current === epoch) {
      if (!applyDocPayload(payload)) return;
      refreshReadiness();
      refreshQc();
    }
  };

  // --- Save gate: never discard a session's work without offering to save ---
  // New session and Open project both replace the whole session, so each routes
  // through this gate first (Save / Don't save / Cancel). The only way to lose
  // work becomes explicitly declining the save — the user's rule. Mirrors the
  // native window-close prompt, reusing the same predicate + save machinery.

  /** Save the session to a file: the panel's Save button, and the gate in
   *  front of every action that replaces the session.
   *
   *  Native shell (pywebview): the FIRST save of a session asks where, and
   *  every later one overwrites that file silently — `saveAs` is how the
   *  dialog comes back. The shell owns which of the two happens, because the
   *  target it acts on is session state the server clears on reset/load; this
   *  side only reports the answer back into `saveTarget` so the button can
   *  draw itself. Tutorial workspaces and the dev browser have no such target
   *  and download instead (see `downloadProjectFile`).
   *
   *  Resolves `ok` only once a file was actually written — the save gate
   *  proceeds to its reset/load on nothing less.
   */
  const saveProjectFile = async (
    options?: { saveAs?: boolean },
  ): Promise<SaveOutcome> => {
    // A tutorial workspace is a disposable practice copy the native save
    // deliberately refuses, and the panel's Save must still hand the user
    // their copy. Downloading it establishes no target (a browser download
    // cannot overwrite in place), so this never turns the button into the
    // overwrite form — which is right: the copy is not the user's project.
    if (inProtectedWorkspace) {
      try {
        await downloadProjectFile("tutorial");
        return { ok: true, cancelled: false, error: "" };
      } catch (e) {
        return {
          ok: false,
          cancelled: false,
          error: e instanceof Error ? e.message : String(e),
        };
      }
    }
    const api = window.pywebview?.api;
    if (api?.save_project) {
      try {
        // Called through the api object rather than a detached reference —
        // the bridge is the shell's, and how it binds its own methods is not
        // this side's assumption to make.
        const result =
          options?.saveAs && api.save_project_as
            ? await api.save_project_as()
            : await api.save_project();
        if (result?.ok) {
          setSaveTarget(
            result.target ? { path: result.target, name: result.name } : null,
          );
        }
        return {
          ok: !!result?.ok,
          cancelled: !!result?.cancelled,
          // The shell owns the wording of its own refusals — a second copy
          // here would be free to describe a failure it never saw.
          error: result?.error ?? "",
        };
      } catch {
        return {
          ok: false,
          cancelled: false,
          error: "The save could not be completed.",
        };
      }
    }
    // No native bridge (dev/browser): download the project file. We can't
    // observe the browser's own save dialog, so a completed fetch counts as
    // done (and it's awaited, so the reset can't race the save payload).
    try {
      await downloadProjectFile();
      return { ok: true, cancelled: false, error: "" };
    } catch (e) {
      return {
        ok: false,
        cancelled: false,
        error: e instanceof Error ? e.message : String(e),
      };
    }
  };

  /** The panel's Save / Save as… — the same machinery as the gate, minus the
   *  session-replacing action behind it. */
  const onSaveProject = (saveAs?: boolean) => saveProjectFile({ saveAs });

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
      if (!applySessionBundle(session)) return;
      // A template started from the ordinary app replaces the user's whole
      // session, so the panes go with it. One started inside the tutorial is
      // a disposable practice copy, and the tour is still driving the drawer
      // nonces this would reset — the same distinction requestStartTemplate
      // draws when it skips the save gate for a scenario.
      if (!inProtectedWorkspace) discardPaneState();
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
    if (saved.ok) runGate(gate);
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
      if (!adoptWorkspaceLease(result)) return;
      advanceWorkspaceEpoch();
      // Opening a project replaces the whole session, so the panes must not
      // carry the outgoing one's compare diff, review draft, QC accept-set or
      // half-typed forms into it. Never reached inside the tutorial —
      // onLoadProject ends the tour first.
      discardPaneState();
      if (!applyDocPayload(result)) return;
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

  const onboarding = useOnboarding({
    applySession: applySessionBundle,
    health,
  });
  onboardingRef.current = onboarding;

  /** Flush the completion-debrief queue: the auto-sent chat turn that briefs
   *  the user the moment research / Final QC finishes. Gates live in the
   *  pure queue helper (lib/debriefQueue.ts); this effect only samples them
   *  at fire time and drives the ordinary send() — the debrief is a normal,
   *  visible user turn on the one chat path. Every skip is silent: a
   *  refused or failed debrief fetch must never surface as an error over a
   *  run that just completed fine. Re-runs on busy's falling edge, so a
   *  debrief held behind a streaming turn (or behind the debrief turn
   *  itself) fires as soon as the composer unlocks. */
  useEffect(() => {
    if (debriefInFlightRef.current) return;
    if (!health) return; // unknown health HOLDS (never drops) the queue
    const { state, next } = takeNextDebrief(debriefQueueRef.current, {
      epoch: workspaceEpochRef.current,
      busy,
      manualEditBusy,
      fileLoading: fileLoading !== null,
      tourActive: onboarding.phase.kind !== "idle",
      protectedWorkspace: inProtectedWorkspace,
      autoDebrief: health.auto_debrief !== false,
    });
    if (state !== debriefQueueRef.current) debriefQueueRef.current = state;
    if (!next) return;
    // The render-time gates can lag the synchronous guards send() lives by
    // (state commits a render behind the refs). A popped entry that send()
    // would decline must be REQUEUED, never eaten — the blocking state's own
    // falling edge is a dependency, so it retries without spinning.
    const guardsBusy = () =>
      busyRef.current || manualEditBusyRef.current || fileLoadingRef.current;
    if (guardsBusy()) {
      debriefQueueRef.current = requeueDebrief(debriefQueueRef.current, next);
      return;
    }
    debriefInFlightRef.current = true;
    void (async () => {
      try {
        const message =
          next.kind === "research"
            ? await fetchResearchDebrief()
            : await fetchQcDebrief();
        if (workspaceEpochRef.current !== next.epoch) return;
        if (guardsBusy()) {
          // A guard engaged while the directive was being fetched.
          debriefQueueRef.current = requeueDebrief(
            debriefQueueRef.current,
            next,
          );
          return;
        }
        await send(message);
      } catch (err) {
        // eslint-disable-next-line no-console
        console.debug("completion debrief skipped:", err);
      } finally {
        debriefInFlightRef.current = false;
        setDebriefNonce((n) => n + 1);
      }
    })();
    // send() is deliberately not a dependency: it is redefined every render
    // and the effect only needs whichever instance is current when it fires.
  }, [busy, manualEditBusy, fileLoading, debriefNonce, health, inProtectedWorkspace, onboarding.phase.kind]);
  const activeDiscipline = projectDiscipline(doc, health?.legacy_discipline);
  const projectHeading = formatProjectHeading(doc, health?.legacy_discipline);

  return (
    <div className="flex h-full flex-col">
      <Header
        health={health}
        projectHeading={projectHeading}
        busy={busy}
        update={update}
        installingUpdate={installing}
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
        onShowReleaseNotes={openReleaseNotes}
      />
      <WhatsNewModal
        open={whatsNew.length > 0}
        entries={whatsNew}
        onClose={dismissReleaseNotes}
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
        update={update}
        installing={installing}
        installError={installError}
        onCheckUpdate={() => runUpdateCheck(true)}
        onInstallUpdate={onInstallUpdate}
      />
      <OnboardingOverlay
        ob={onboarding}
        doc={doc}
        busy={busy || manualEditBusy}
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
        open={detachConfirmOpen}
        title="Edit this document freely?"
        body={
          <>
            Right now Build-a-Spec exports this section as a byte-exact copy
            of your upload, changing only the words it is allowed to patch.
            That promise is why most of the document is read-only.
            <p className="mt-2">
              Switching to free editing lets you change anything — headings,
              structure, articles — and exports a{" "}
              <b className="text-ink">normalized</b> Word file in
              Build-a-Spec's formatting instead of your original's.
            </p>
            <p className="mt-2">
              Your original upload stays downloadable, and “Redline vs master”
              still shows what changed. This applies to this document only and{" "}
              <b className="text-ink">cannot be undone</b> — to go back, import
              the file again.
            </p>
          </>
        }
        confirmLabel="Edit freely"
        cancelLabel="Keep original formatting"
        onConfirm={doDetachSource}
        onCancel={() => setDetachConfirmOpen(false)}
      />
      <ConfirmDialog
        open={apiKeyErrorOpen}
        title="Your API key needs attention"
        body={
          <>
            Your Anthropic API key appears to be invalid or has expired, so
            that last request couldn't go through.
            <ol className="mt-2 list-decimal space-y-1 pl-5">
              <li>
                Open{" "}
                <a
                  href="https://console.anthropic.com/settings/keys"
                  className="text-accent underline"
                >
                  the Anthropic Console
                </a>{" "}
                and generate a new key (or check that the existing one is
                still active).
              </li>
              <li>Paste it into Settings here, and it'll be used right away.</li>
            </ol>
          </>
        }
        confirmLabel="Open Settings"
        cancelLabel="Dismiss"
        onConfirm={() => {
          setApiKeyErrorOpen(false);
          setSettingsOpen(true);
        }}
        onCancel={() => setApiKeyErrorOpen(false)}
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
          key={`chat-${sessionNonce}`}
          messages={messages}
          busy={busy}
          onSend={send}
          suggestions={suggestions}
          discipline={activeDiscipline}
          onStartOnboarding={onboarding.start}
          tourActive={onboarding.phase.kind !== "idle"}
          tutorialUpdated={tutorialUpdated}
          onStop={onStop}
          uploading={fileLoading !== null}
          prefill={prefill}
          figuresById={figuresById}
          onDeleteFigure={onDeleteFigure}
        />
        <ArtifactPanel
          key={`panel-${sessionNonce}`}
          doc={doc}
          openItems={openItems}
          lintIssues={lintIssues}
          standards={standards}
          profileComplete={profileComplete}
          draftPrerequisites={draftPrereqs}
          research={research}
          qc={qc}
          readiness={readiness}
          usage={usage}
          changedIds={changedIds}
          saveTarget={saveTarget}
          onSaveProject={onSaveProject}
          baselineIndex={baselineIndex}
          importReport={importReport}
          referenceDocs={referenceDocs}
          onAttachReference={onAttachReference}
          onRemoveReference={onRemoveReference}
          referenceBusy={referenceBusy}
          sourceAvailable={sourceAvailable}
          sourceDetached={sourceDetached}
          preservationReady={preservationReady}
          preservedExportAvailable={preservedExportAvailable}
          onOpenInWord={onOpenInWord}
          sourceCapabilities={sourceCapabilities}
          templateOrigin={templateOrigin}
          tutorialActive={inProtectedWorkspace}
          busy={busy || manualEditBusy || referenceBusy}
          fileLoading={fileLoading}
          importNotice={importNotice}
          onDismissImportNotice={() => setImportNotice(null)}
          onDetachSource={() => setDetachConfirmOpen(true)}
          detaching={detaching}
          onUndo={onUndo}
          onRedo={onRedo}
          onSaveAsTemplate={openTemplateStudio}
          onEditDoc={onEditDoc}
          onLoadProject={onLoadProject}
          nativeOpenFile={nativeOpenFile}
          onImportMaster={onImportMaster}
          capabilityProgress={capabilityProgress}
          onStartResearch={onStartResearch}
          onStopResearch={onStopResearch}
          onStartQc={onStartQc}
          onStopQc={onStopQc}
          onPreviewQc={onPreviewQc}
          onApplyQc={onApplyQc}
          onDismissQc={onDismissQc}
          onDraftFull={onDraftFull}
          onDraftAdapt={onDraftAdapt}
          onAskModel={onAskModel}
          onFetchDiff={getDocDiff}
          drawerNonces={drawerNonces}
        />
      </main>
    </div>
  );
}
