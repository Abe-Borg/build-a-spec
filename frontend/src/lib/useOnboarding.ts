import { useCallback, useEffect, useRef, useState } from "react";
import type {
  EditOp,
  Health,
  SessionBundle,
  SpecDoc,
  TutorialEvent,
  TutorialSource,
} from "../types";
import {
  enrichTutorialWorkspace,
  finishTutorialScenario,
  getTutorialStatus,
  restoreTutorialWorkspace,
  startTutorialScenario,
  startTutorialWorkspace,
} from "./api";
import {
  clearOnboardingProgress,
  loadOnboardingProgress,
  markOnboardingCompleted,
  saveOnboardingProgress,
} from "./onboardingStorage";
import { TOUR, TOUR_VERSION, type TourAction } from "./tour";

export type DrawerName = "review" | "research" | "qc" | "openItems";

export type OnboardingPhase =
  | { kind: "idle" }
  | { kind: "source-choice" }
  | {
      kind: "enrichment-choice";
      source: TutorialSource;
      targetChunk: number;
      targetStep: number;
    }
  | {
      kind: "preparing";
      source: TutorialSource;
      stage: "starting" | "enriching" | "scenario" | "finishing";
      label?: string;
      targetChunk?: number;
      targetStep?: number;
      enrichmentMode?: "live" | "bundled";
      error: string | null;
    }
  | { kind: "touring"; chunk: number; step: number }
  | { kind: "chunk-break"; nextChunk: number }
  | { kind: "paused"; chunk: number; step: number };

export interface OnboardingCaps {
  editDoc: (ops: EditOp[]) => Promise<void>;
  startResearch: () => void;
  startQc: () => void;
  prefillComposer: (text: string) => void;
  openTemplates: () => void;
  applySession: (session: SessionBundle) => void;
  applyTutorialEvent: (event: TutorialEvent) => void;
  health: Health | null;
  doc: SpecDoc | null;
  hasContent: boolean;
}

export interface OnboardingApi {
  phase: OnboardingPhase;
  endConfirm: boolean;
  start: () => void;
  startAtChapter: (chapter: string | number) => void;
  chooseSource: (source: TutorialSource) => void;
  chooseLiveEnrichment: () => void;
  chooseBundledEnrichment: () => void;
  retryPrepare: () => void;
  ensureContent: () => void;
  advance: () => void;
  back: () => void;
  continueChunk: () => void;
  pause: () => void;
  resume: () => void;
  askQuestion: () => void;
  requestEnd: () => void;
  cancelEnd: () => void;
  end: () => void;
  /** Retry a restore that failed, or step back from one, without a prompt. */
  stayInTutorial: () => void;
  runStepAction: (action: TourAction) => void;
  /** Reconcile a session replacement initiated by another tutorial surface
   * (currently starting a template inside the disposable template scenario). */
  syncSessionIdentity: (session: SessionBundle) => void;
  /** Retire local tour state after native close restored the original. */
  acceptNativeRestore: (session: SessionBundle) => void;
  /** External teardown (new/open session) should not mark completion. */
  abort: () => Promise<boolean>;
}

interface WorkspaceState {
  tutorialId: string;
  workspaceId: number;
  generation: number;
  source: TutorialSource;
  activeScenario?: string;
}

const requestId = () =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `tutorial-${Date.now()}-${Math.random().toString(36).slice(2)}`;

export function useOnboarding(caps: OnboardingCaps): OnboardingApi {
  const [phase, setPhase] = useState<OnboardingPhase>({ kind: "idle" });
  const [endConfirm, setEndConfirm] = useState(false);
  const capsRef = useRef(caps);
  capsRef.current = caps;
  const phaseRef = useRef(phase);
  phaseRef.current = phase;
  const workspaceRef = useRef<WorkspaceState | null>(null);
  const enterChunkRef = useRef<((chunk: number, step?: number) => void) | null>(null);
  const restoreOriginalRef = useRef<
    ((opts: { completed: boolean }) => void) | null
  >(null);
  const pendingStartChunkRef = useRef(0);
  const runRef = useRef(0);
  const lastSourceRef = useRef<TutorialSource>("showcase");
  const startRequestRef = useRef<string | null>(null);
  // Where to put the user back when a restore fails. There is no longer a
  // modal to dismiss to, so the last visited step is the only honest landing.
  const lastStepRef = useRef<{ chunk: number; step: number }>({ chunk: 0, step: 0 });
  // Remembered so retrying a failed restore keeps the original request's claim
  // about whether the tour was actually completed.
  const pendingCompletedRef = useRef(false);

  const persist = useCallback((chunk: number, step: number, paused: boolean) => {
    lastStepRef.current = { chunk, step };
    const workspace = workspaceRef.current;
    if (
      !workspace ||
      workspace.workspaceId === undefined ||
      workspace.generation === undefined
    ) {
      return;
    }
    saveOnboardingProgress({
      version: TOUR_VERSION,
      tutorialId: workspace.tutorialId,
      workspaceId: workspace.workspaceId,
      generation: workspace.generation,
      source: workspace.source,
      chunk,
      step,
      paused,
    });
  }, []);

  // Resume after a reload whenever the server confirms a protected tutorial
  // workspace. Local progress supplies the exact step when available.
  useEffect(() => {
    const stored = loadOnboardingProgress(TOUR_VERSION);
    const run = runRef.current;
    let cancelled = false;
    void getTutorialStatus()
      .then((status) => {
        const storedLeaseMismatch =
          !stored ||
          status.tutorial_id !== stored.tutorialId ||
          status.workspace_id !== stored.workspaceId ||
          status.generation !== stored.generation;
        if (
          cancelled ||
          runRef.current !== run ||
          phaseRef.current.kind !== "idle" ||
          !status.active ||
          !status.tutorial_id ||
          status.workspace_id === undefined ||
          status.generation === undefined
        ) {
          if (!cancelled && stored) clearOnboardingProgress();
          return;
        }
        workspaceRef.current = {
          tutorialId: status.tutorial_id,
          workspaceId: status.workspace_id,
          generation: status.generation,
          source: status.source ?? stored?.source ?? "showcase",
          activeScenario:
            status.scope === "scenario"
              ? status.scenario_kind ?? status.chapter
              : undefined,
        };
        if (storedLeaseMismatch) {
          // A protected workspace survived a reload but the local record that
          // says WHERE the user was did not, so the tutorial cannot resume.
          // Ending it returns the retained project, which is the only outcome
          // there has ever been — so do it, rather than ask.
          clearOnboardingProgress();
          restoreOriginalRef.current?.({ completed: false });
          return;
        }
        const storedChunk = stored.chunk;
        const chunk = Math.min(storedChunk, TOUR.length - 1);
        setPhase({
          kind: "paused",
          chunk,
          step: Math.min(
            stored.step,
            TOUR[chunk].steps.length - 1,
          ),
        });
      })
      .catch(() => {
        if (stored) clearOnboardingProgress();
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const start = useCallback(() => {
    setEndConfirm(false);
    if (phaseRef.current.kind === "paused") {
      const current = phaseRef.current;
      runRef.current += 1;
      enterChunkRef.current?.(current.chunk, current.step);
      return;
    }
    pendingStartChunkRef.current = 0;
    startRequestRef.current = null;
    runRef.current += 1;
    setPhase({ kind: "source-choice" });
  }, [persist]);

  const startAtChapter = useCallback((chapter: string | number) => {
    const requested =
      typeof chapter === "number"
        ? chapter
        : TOUR.findIndex((candidate) => candidate.id === chapter);
    const chunk = Math.min(Math.max(requested, 0), TOUR.length - 1);
    pendingStartChunkRef.current = chunk;
    setEndConfirm(false);
    if (workspaceRef.current) {
      runRef.current += 1;
      enterChunkRef.current?.(chunk, 0);
      return;
    }
    runRef.current += 1;
    setPhase({ kind: "source-choice" });
  }, []);

  const enrich = useCallback(async (
    resume?: { chunk: number; step: number },
    mode: "live" | "bundled" = "live",
  ) => {
    const workspace = workspaceRef.current;
    if (!workspace) return;
    const run = runRef.current;
    setPhase({
      kind: "preparing",
      source: workspace.source,
      stage: "enriching",
      targetChunk: resume?.chunk,
      targetStep: resume?.step,
      enrichmentMode: mode,
      error: null,
    });
    try {
      for await (const event of enrichTutorialWorkspace({
        tutorialId: workspace.tutorialId,
        workspaceId: workspace.workspaceId,
        generation: workspace.generation,
        mode,
      })) {
        if (runRef.current !== run) return;
        if (event.type === "tutorial_fallback") {
          if (
            event.replaces_workspace_id !== workspace.workspaceId ||
            event.replaces_generation !== workspace.generation
          ) {
            continue;
          }
          const replacementWorkspaceId =
            event.workspace_id ?? event.session.workspace_id;
          if (replacementWorkspaceId === undefined) continue;
          capsRef.current.applySession(event.session);
          workspace.workspaceId = replacementWorkspaceId;
          workspace.generation =
            event.generation ?? event.session.generation ?? workspace.generation;
          workspace.source =
            event.source ?? event.session.tutorial_source ?? workspace.source;
          continue;
        }
        if (event.type === "tutorial_session") {
          capsRef.current.applySession(event.session);
          workspace.workspaceId =
            event.workspace_id ?? event.session.workspace_id ?? workspace.workspaceId;
          workspace.generation =
            event.generation ?? event.session.generation ?? workspace.generation;
          workspace.source =
            event.source ?? event.session.tutorial_source ?? workspace.source;
          continue;
        }
        if (
          event.workspace_id !== undefined &&
          workspace.workspaceId !== undefined &&
          event.workspace_id !== workspace.workspaceId
        ) {
          continue;
        }
        if (
          event.generation !== undefined &&
          workspace.generation !== undefined &&
          event.generation !== workspace.generation
        ) {
          continue;
        }
        if (event.workspace_id !== undefined) workspace.workspaceId = event.workspace_id;
        if (event.generation !== undefined) workspace.generation = event.generation;
        capsRef.current.applyTutorialEvent(event);
      }
      if (runRef.current !== run) return;
      const next = resume ?? { chunk: 0, step: 0 };
      if (enterChunkRef.current) enterChunkRef.current(next.chunk, next.step);
      else {
        setPhase({ kind: "touring", ...next });
        persist(next.chunk, next.step, false);
      }
    } catch (error) {
      if (runRef.current !== run) return;
      const status = await getTutorialStatus().catch(() => null);
      if (
        status?.active &&
        status.tutorial_id === workspace.tutorialId &&
        status.workspace_id !== undefined &&
        status.generation !== undefined &&
        status.session &&
        status.coverage?.ready
      ) {
        workspace.workspaceId = status.workspace_id;
        workspace.generation = status.generation;
        workspace.activeScenario =
          status.scope === "scenario" ? status.scenario_kind : undefined;
        capsRef.current.applySession(status.session);
        const next = resume ?? { chunk: 0, step: 0 };
        enterChunkRef.current?.(next.chunk, next.step);
        return;
      }
      setPhase({
        kind: "preparing",
        source: workspace.source,
        stage: "enriching",
        targetChunk: resume?.chunk,
        targetStep: resume?.step,
        enrichmentMode: mode,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }, [persist]);

  const chooseSource = useCallback(
    async (source: TutorialSource) => {
      const run = (runRef.current += 1);
      lastSourceRef.current = source;
      setPhase({ kind: "preparing", source, stage: "starting", error: null });
      try {
        const currentLease = capsRef.current.health;
        if (
          currentLease?.workspace_id === undefined ||
          currentLease.generation === undefined
        ) {
          throw new Error(
            "The current workspace identity is still loading. Try the tutorial again.",
          );
        }
        const pendingRequestId = startRequestRef.current ?? requestId();
        startRequestRef.current = pendingRequestId;
        const result = await startTutorialWorkspace({
          source,
          requestId: pendingRequestId,
          workspaceId: currentLease.workspace_id,
          generation: currentLease.generation,
        });
        if (runRef.current !== run) {
          await restoreTutorialWorkspace({
            tutorialId: result.tutorial_id,
            workspaceId: result.workspace_id,
            generation: result.generation,
          }).catch(() => undefined);
          return;
        }
        const workspace: WorkspaceState = {
          tutorialId: result.tutorial_id,
          workspaceId: result.workspace_id,
          generation: result.generation,
          source,
        };
        workspaceRef.current = workspace;
        startRequestRef.current = null;
        capsRef.current.applySession(result.session);
        const target = {
          chunk: pendingStartChunkRef.current,
          step: 0,
        };
        if (result.needs_enrichment && source === "current") {
          setPhase({
            kind: "enrichment-choice",
            source,
            targetChunk: target.chunk,
            targetStep: target.step,
          });
        }
        else if (result.needs_enrichment) await enrich(target, "live");
        else enterChunkRef.current?.(target.chunk, target.step);
      } catch (error) {
        if (runRef.current !== run) return;
        const status = await getTutorialStatus().catch(() => null);
        if (
          status?.active &&
          status.tutorial_id &&
          status.workspace_id !== undefined &&
          status.generation !== undefined &&
          status.session
        ) {
          const recovered: WorkspaceState = {
            tutorialId: status.tutorial_id,
            workspaceId: status.workspace_id,
            generation: status.generation,
            source: status.source ?? source,
            activeScenario:
              status.scope === "scenario" ? status.scenario_kind : undefined,
          };
          workspaceRef.current = recovered;
          startRequestRef.current = null;
          capsRef.current.applySession(status.session);
          const target = { chunk: pendingStartChunkRef.current, step: 0 };
          if (status.coverage?.ready) {
            enterChunkRef.current?.(target.chunk, target.step);
          } else if (recovered.source === "current") {
            setPhase({
              kind: "enrichment-choice",
              source: "current",
              targetChunk: target.chunk,
              targetStep: target.step,
            });
          } else {
            await enrich(target, "live");
          }
          return;
        }
        setPhase({
          kind: "preparing",
          source,
          stage: "starting",
          error: error instanceof Error ? error.message : String(error),
        });
      }
    },
    [enrich, persist],
  );

  const retryPrepare = useCallback(() => {
    const current = phaseRef.current;
    if (current.kind !== "preparing") return;
    if (workspaceRef.current && current.stage === "enriching") {
      void enrich({
        chunk: current.targetChunk ?? pendingStartChunkRef.current,
        step: current.targetStep ?? 0,
      }, current.enrichmentMode ?? "live");
    }
    else if (current.stage === "scenario" && current.targetChunk !== undefined) {
      enterChunkRef.current?.(current.targetChunk, current.targetStep ?? 0);
    }
    // A failed restore retries the restore. Falling through to chooseSource
    // here would start the whole tutorial over on the way OUT of it.
    else if (current.stage === "finishing") {
      restoreOriginalRef.current?.({ completed: pendingCompletedRef.current });
    }
    else void chooseSource(current.source);
  }, [chooseSource, enrich]);

  const ensureContent = useCallback(() => {
    const current = phaseRef.current;
    if (current.kind === "touring") {
      const readiness = TOUR[current.chunk].steps[current.step].readiness;
      if (
        readiness === "content" ||
        readiness === "rich-structure" ||
        readiness === "versioned"
      ) {
        if (workspaceRef.current?.source === "current") {
          setPhase({
            kind: "enrichment-choice",
            source: "current",
            targetChunk: current.chunk,
            targetStep: current.step,
          });
        }
        else void enrich({ chunk: current.chunk, step: current.step }, "live");
      }
    }
  }, [enrich]);

  const chooseEnrichment = useCallback(
    (mode: "live" | "bundled") => {
      const current = phaseRef.current;
      if (current.kind !== "enrichment-choice") return;
      void enrich(
        { chunk: current.targetChunk, step: current.targetStep },
        mode,
      );
    },
    [enrich],
  );

  const enterChunk = useCallback(
    async (chunk: number, step = 0) => {
      const workspace = workspaceRef.current;
      if (!workspace) {
        setPhase({ kind: "source-choice" });
        return;
      }
      const run = runRef.current;
      const desiredScenario = TOUR[chunk].scenario;
      if (workspace.activeScenario === desiredScenario) {
        setPhase({ kind: "touring", chunk, step });
        persist(chunk, step, false);
        return;
      }
      setPhase({
        kind: "preparing",
        source: workspace.source,
        stage: "scenario",
        label: TOUR[chunk].title,
        targetChunk: chunk,
        targetStep: step,
        error: null,
      });
      try {
        if (workspace.activeScenario) {
          const restored = await finishTutorialScenario({
            tutorialId: workspace.tutorialId,
            workspaceId: workspace.workspaceId,
            generation: workspace.generation,
          });
          if (runRef.current !== run) return;
          capsRef.current.applySession(restored);
          if (restored.workspace_id !== undefined) {
            workspace.workspaceId = restored.workspace_id;
          }
          if (restored.generation !== undefined) {
            workspace.generation = restored.generation;
          }
          workspace.activeScenario = undefined;
        }
        if (desiredScenario) {
          const scenario = await startTutorialScenario({
            tutorialId: workspace.tutorialId,
            workspaceId: workspace.workspaceId,
            generation: workspace.generation,
            chapter: desiredScenario,
          });
          if (runRef.current !== run) return;
          capsRef.current.applySession(scenario);
          if (scenario.workspace_id !== undefined) {
            workspace.workspaceId = scenario.workspace_id;
          }
          if (scenario.generation !== undefined) {
            workspace.generation = scenario.generation;
          }
          workspace.activeScenario = desiredScenario;
        }
        setPhase({ kind: "touring", chunk, step });
        persist(chunk, step, false);
      } catch (error) {
        if (runRef.current !== run) return;
        const status = await getTutorialStatus().catch(() => null);
        const changedLease =
          status?.active &&
          status.tutorial_id === workspace.tutorialId &&
          status.workspace_id !== undefined &&
          status.generation !== undefined &&
          (status.workspace_id !== workspace.workspaceId ||
            status.generation !== workspace.generation);
        if (changedLease && status?.session) {
          workspace.workspaceId = status.workspace_id!;
          workspace.generation = status.generation!;
          workspace.activeScenario =
            status.scope === "scenario" ? status.scenario_kind : undefined;
          capsRef.current.applySession(status.session);
          if (
            status.scope === "scenario" &&
            status.scenario_kind === desiredScenario
          ) {
            setPhase({ kind: "touring", chunk, step });
            persist(chunk, step, false);
            return;
          }
          if (status.scope === "tutorial") {
            void enterChunk(chunk, step);
            return;
          }
        }
        setPhase({
          kind: "preparing",
          source: workspace.source,
          stage: "scenario",
          label: TOUR[chunk].title,
          targetChunk: chunk,
          targetStep: step,
          error: error instanceof Error ? error.message : String(error),
        });
      }
    },
    [persist],
  );
  enterChunkRef.current = (chunk, step) => {
    void enterChunk(chunk, step);
  };

  const advance = useCallback(() => {
    const current = phaseRef.current;
    if (current.kind !== "touring") return;
    const chapter = TOUR[current.chunk];
    if (current.step + 1 < chapter.steps.length) {
      const next = { chunk: current.chunk, step: current.step + 1 };
      setPhase({ kind: "touring", ...next });
      persist(next.chunk, next.step, false);
    } else if (current.chunk + 1 < TOUR.length) {
      setPhase({ kind: "chunk-break", nextChunk: current.chunk + 1 });
      persist(current.chunk, current.step, false);
    } else {
      // The last step's Continue button says what it does, so the click is
      // the consent; finishing restores the project with nothing else asked.
      restoreOriginalRef.current?.({ completed: true });
    }
  }, [persist]);

  const back = useCallback(() => {
    const current = phaseRef.current;
    if (current.kind !== "touring") return;
    if (current.step > 0) {
      const step = current.step - 1;
      setPhase({ kind: "touring", chunk: current.chunk, step });
      persist(current.chunk, step, false);
    } else if (current.chunk > 0) {
      const chunk = current.chunk - 1;
      void enterChunk(chunk, TOUR[chunk].steps.length - 1);
    }
  }, [enterChunk, persist]);

  const continueChunk = useCallback(() => {
    const current = phaseRef.current;
    if (current.kind === "chunk-break") void enterChunk(current.nextChunk);
  }, [enterChunk]);

  const pause = useCallback(() => {
    const current = phaseRef.current;
    if (current.kind === "touring") {
      setPhase({ kind: "paused", chunk: current.chunk, step: current.step });
      persist(current.chunk, current.step, true);
    } else if (current.kind === "chunk-break") {
      setPhase({ kind: "paused", chunk: current.nextChunk, step: 0 });
      persist(current.nextChunk, 0, true);
    }
  }, [persist]);

  const resume = useCallback(() => {
    const current = phaseRef.current;
    if (current.kind === "paused") void enterChunk(current.chunk, current.step);
  }, [enterChunk]);

  const askQuestion = useCallback(() => {
    capsRef.current.prefillComposer("");
    pause();
  }, [pause]);

  const requestEnd = useCallback(() => setEndConfirm(true), []);
  const cancelEnd = useCallback(() => setEndConfirm(false), []);

  /**
   * The single exit from a tutorial workspace.
   *
   * Ending ALWAYS returns the exact retained pre-tutorial session — there is
   * no keep-the-practice-copy outcome and nothing to choose between, so the
   * user is never asked. The backend re-activates the very same SessionState
   * object it stashed at start, so the project comes back whole: document,
   * history, version list, runners and retained source bytes included.
   *
   * `completed` only sets the cosmetic "tour finished" flag. External teardown
   * (New session / Open project) and post-reload cleanup pass false — those
   * are not the user finishing the tutorial.
   */
  const restoreOriginal = useCallback(
    async ({ completed }: { completed: boolean }): Promise<boolean> => {
      const workspace = workspaceRef.current;
      setEndConfirm(false);
      pendingCompletedRef.current = completed;
      const settle = (session?: SessionBundle) => {
        if (session) capsRef.current.applySession(session);
        if (completed) markOnboardingCompleted();
        clearOnboardingProgress();
        workspaceRef.current = null;
        startRequestRef.current = null;
        runRef.current += 1;
        setPhase({ kind: "idle" });
      };
      if (!workspace) {
        runRef.current += 1;
        settle();
        return true;
      }
      // Bump before the first await so an in-flight enrich or scenario swap is
      // orphaned and cannot apply a tutorial payload over the restored project.
      const run = (runRef.current += 1);
      setPhase({
        kind: "preparing",
        source: workspace.source,
        stage: "finishing",
        error: null,
      });
      try {
        const transition = () =>
          restoreTutorialWorkspace({
            tutorialId: workspace.tutorialId,
            workspaceId: workspace.workspaceId,
            generation: workspace.generation,
          });
        let session: SessionBundle;
        try {
          session = await transition();
        } catch (firstError) {
          // A scenario may have settled after End was clicked. Reconcile its
          // authoritative lease once, then restore against it.
          const status = await getTutorialStatus().catch(() => null);
          if (status && !status.active) {
            // Someone already left the tutorial (a native close, or a restore
            // that raced this one). The original is live; that is the goal.
            settle(status.session ?? undefined);
            return true;
          }
          if (
            !status?.active ||
            status.tutorial_id !== workspace.tutorialId ||
            status.workspace_id === undefined ||
            status.generation === undefined
          ) {
            throw firstError;
          }
          workspace.workspaceId = status.workspace_id;
          workspace.generation = status.generation;
          workspace.activeScenario =
            status.scope === "scenario" ? status.scenario_kind : undefined;
          session = await transition();
        }
        // A newer restore already owns the terminal state; write nothing.
        if (runRef.current !== run) return true;
        settle(session);
        return true;
      } catch (error) {
        if (runRef.current !== run) return true;
        setPhase({
          kind: "preparing",
          source: workspace.source,
          stage: "finishing",
          error: error instanceof Error ? error.message : String(error),
        });
        return false;
      }
    },
    [],
  );
  restoreOriginalRef.current = (opts) => {
    void restoreOriginal(opts);
  };

  const end = useCallback(() => {
    void restoreOriginal({ completed: true });
  }, [restoreOriginal]);

  /** Leave a failed restore without retrying it; the tutorial is still live. */
  const stayInTutorial = useCallback(() => {
    const { chunk, step } = lastStepRef.current;
    if (!workspaceRef.current) {
      setPhase({ kind: "idle" });
      return;
    }
    setPhase({ kind: "paused", chunk, step });
  }, []);

  const runStepAction = useCallback(
    (action: TourAction) => {
      const current = capsRef.current;
      switch (action.kind) {
        case "profile-fill":
          void current.editDoc([
            {
              action: "set_project_profile",
              target_id: "sec",
              city: "Phoenix",
              state: "Arizona",
              country: "USA",
              client: "Tutorial Client",
            },
          ]);
          break;
        case "run-research":
          current.startResearch();
          break;
        case "run-qc":
          current.startQc();
          break;
        case "prefill-composer":
          current.prefillComposer(action.prefillText ?? "");
          pause();
          break;
        case "open-templates":
          current.openTemplates();
          pause();
          break;
      }
    },
    [pause],
  );

  const syncSessionIdentity = useCallback(
    (session: SessionBundle) => {
      const workspace = workspaceRef.current;
      if (!workspace) return;
      if (session.tutorial_id && session.tutorial_id !== workspace.tutorialId) return;
      if (session.workspace_id !== undefined) workspace.workspaceId = session.workspace_id;
      if (session.generation !== undefined) workspace.generation = session.generation;
      if (session.workspace_scope === "scenario") {
        workspace.activeScenario = session.scenario_kind ?? workspace.activeScenario;
      } else if (session.workspace_scope === "tutorial") {
        workspace.activeScenario = undefined;
      }
      const current = phaseRef.current;
      if (current.kind === "touring" || current.kind === "paused") {
        persist(current.chunk, current.step, current.kind === "paused");
      }
    },
    [persist],
  );

  const acceptNativeRestore = useCallback((session: SessionBundle) => {
    runRef.current += 1;
    clearOnboardingProgress();
    workspaceRef.current = null;
    startRequestRef.current = null;
    setEndConfirm(false);
    capsRef.current.applySession(session);
    setPhase({ kind: "idle" });
  }, []);

  const abort = useCallback(
    () => restoreOriginal({ completed: false }),
    [restoreOriginal],
  );

  return {
    phase,
    endConfirm,
    start,
    startAtChapter,
    chooseSource,
    chooseLiveEnrichment: () => chooseEnrichment("live"),
    chooseBundledEnrichment: () => chooseEnrichment("bundled"),
    retryPrepare,
    ensureContent,
    advance,
    back,
    continueChunk,
    pause,
    resume,
    askQuestion,
    requestEnd,
    cancelEnd,
    end,
    stayInTutorial,
    runStepAction,
    syncSessionIdentity,
    acceptNativeRestore,
    abort,
  };
}
