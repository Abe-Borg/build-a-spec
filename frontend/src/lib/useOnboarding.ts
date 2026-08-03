import { useCallback, useEffect, useRef, useState } from "react";
import type { EditOp, Health, SessionBundle, SpecDoc } from "../types";
import {
  finishTutorialScenario,
  getSessionBundle,
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
  | {
      kind: "preparing";
      stage: "starting" | "scenario" | "finishing";
      label?: string;
      targetChunk?: number;
      targetStep?: number;
      error: string | null;
    }
  | { kind: "touring"; chunk: number; step: number }
  | { kind: "chunk-break"; nextChunk: number };

export interface OnboardingCaps {
  editDoc: (ops: EditOp[]) => Promise<void>;
  startResearch: () => void;
  startQc: () => void;
  prefillComposer: (text: string) => void;
  openTemplates: () => void;
  /** Apply only if its workspace lease is not superseded. */
  applySession: (session: SessionBundle) => boolean;
  health: Health | null;
  doc: SpecDoc | null;
  hasContent: boolean;
}

export interface OnboardingApi {
  phase: OnboardingPhase;
  endConfirm: boolean;
  start: () => void;
  startAtChapter: (chapter: string | number) => void;
  retryPrepare: () => void;
  advance: () => void;
  back: () => void;
  continueChunk: () => void;
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
  const startRequestRef = useRef<string | null>(null);
  // True while a /api/tutorial/start request is in flight — see beginShowcase.
  const startInFlightRef = useRef(false);
  // Where to put the user back when a restore fails. There is no longer a
  // modal to dismiss to, so the last visited step is the only honest landing.
  // It is a step of the RUNNING tour: the tutorial has no suspended state to
  // land in — see the phase union above.
  const lastStepRef = useRef<{ chunk: number; step: number }>({ chunk: 0, step: 0 });
  // Remembered so retrying a failed restore keeps the original request's claim
  // about whether the tour was actually completed.
  const pendingCompletedRef = useRef(false);
  // Serializes restores — see restoreOriginal.
  const restoreInFlightRef = useRef<Promise<boolean> | null>(null);

  const persist = useCallback((chunk: number, step: number) => {
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
      chunk,
      step,
    });
  }, []);

  // Resume after a reload whenever the server confirms a protected tutorial
  // workspace. Local progress supplies the exact step when available, and the
  // tour re-enters it directly: the tutorial runs start to finish, so there is
  // no suspended state to land in and nothing to click before it continues.
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
        // enterChunk, not setPhase: the reload may land on a chapter whose
        // scenario the server is no longer holding, and only enterChunk knows
        // how to swap one back in.
        enterChunkRef.current?.(
          chunk,
          Math.min(stored.step, TOUR[chunk].steps.length - 1),
        );
      })
      .catch(() => {
        if (stored) clearOnboardingProgress();
      });
    return () => {
      cancelled = true;
    };
  }, []);

  /**
   * Open the protected tutorial workspace on the bundled showcase.
   *
   * The showcase is the tutorial's only source — there is no chooser, no
   * copy of the current project, and no live generation. The user's session
   * is set aside untouched and restored on every ending.
   */
  const beginShowcase = useCallback(async () => {
    // A second click while a start is in flight JOINS it rather than racing
    // it: the in-flight request keeps its idempotency key, and the resolved
    // start reads pendingStartChunkRef, so a caller that only moved the
    // target chapter has already had its effect. Firing a second request
    // here would mint a fresh key and bump the run, orphaning the first
    // start into restoring the very workspace its successor was adopting
    // (Codex review, PR #113).
    if (startInFlightRef.current) return;
    startInFlightRef.current = true;
    const run = (runRef.current += 1);
    setPhase({ kind: "preparing", stage: "starting", error: null });
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
      workspaceRef.current = {
        tutorialId: result.tutorial_id,
        workspaceId: result.workspace_id,
        generation: result.generation,
      };
      startRequestRef.current = null;
      capsRef.current.applySession(result.session);
      enterChunkRef.current?.(pendingStartChunkRef.current, 0);
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
        // A protected workspace already exists (an earlier start raced or
        // partially completed) — adopt it rather than failing the tour.
        workspaceRef.current = {
          tutorialId: status.tutorial_id,
          workspaceId: status.workspace_id,
          generation: status.generation,
          activeScenario:
            status.scope === "scenario" ? status.scenario_kind : undefined,
        };
        startRequestRef.current = null;
        capsRef.current.applySession(status.session);
        enterChunkRef.current?.(pendingStartChunkRef.current, 0);
        return;
      }
      setPhase({
        kind: "preparing",
        stage: "starting",
        error: error instanceof Error ? error.message : String(error),
      });
    } finally {
      startInFlightRef.current = false;
    }
  }, []);

  const start = useCallback(() => {
    setEndConfirm(false);
    // Already holding the protected workspace: the tour is on screen, or a
    // scenario for it is being prepared, so there is nothing left to start.
    // Falling through would re-run beginShowcase, whose backend refuses the
    // second begin_tutorial — and the conflict path then ADOPTS the live
    // tutorial and re-enters at chapter 1, finishing the active scenario and
    // throwing away the user's place in the tour. `startAtChapter` has always
    // guarded on this ref; `start` did not, and the starter chips put the
    // launcher on screen inside the tour (Codex review, PR #117).
    // Every ending clears the ref, so the tour can always be started again.
    if (workspaceRef.current) return;
    // The pending request id is deliberately NOT reset here: a repeated
    // click reuses it, so the backend's idempotent begin_tutorial folds
    // both into one transition. It clears when a start succeeds or the
    // tour ends.
    pendingStartChunkRef.current = 0;
    void beginShowcase();
  }, [beginShowcase]);

  const startAtChapter = useCallback(
    (chapter: string | number) => {
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
      void beginShowcase();
    },
    [beginShowcase],
  );

  const retryPrepare = useCallback(() => {
    const current = phaseRef.current;
    if (current.kind !== "preparing") return;
    if (current.stage === "scenario" && current.targetChunk !== undefined) {
      enterChunkRef.current?.(current.targetChunk, current.targetStep ?? 0);
    }
    // A failed restore retries the restore. Falling through to a fresh start
    // here would start the whole tutorial over on the way OUT of it.
    else if (current.stage === "finishing") {
      restoreOriginalRef.current?.({ completed: pendingCompletedRef.current });
    }
    else void beginShowcase();
  }, [beginShowcase]);

  const enterChunk = useCallback(
    async (chunk: number, step = 0) => {
      const workspace = workspaceRef.current;
      if (!workspace) {
        void beginShowcase();
        return;
      }
      const run = runRef.current;
      const desiredScenario = TOUR[chunk].scenario;
      if (workspace.activeScenario === desiredScenario) {
        setPhase({ kind: "touring", chunk, step });
        persist(chunk, step);
        return;
      }
      setPhase({
        kind: "preparing",
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
        persist(chunk, step);
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
            persist(chunk, step);
            return;
          }
          if (status.scope === "tutorial") {
            void enterChunk(chunk, step);
            return;
          }
        }
        setPhase({
          kind: "preparing",
          stage: "scenario",
          label: TOUR[chunk].title,
          targetChunk: chunk,
          targetStep: step,
          error: error instanceof Error ? error.message : String(error),
        });
      }
    },
    [beginShowcase, persist],
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
      persist(next.chunk, next.step);
    } else if (current.chunk + 1 < TOUR.length) {
      setPhase({ kind: "chunk-break", nextChunk: current.chunk + 1 });
      persist(current.chunk, current.step);
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
      persist(current.chunk, step);
    } else if (current.chunk > 0) {
      const chunk = current.chunk - 1;
      void enterChunk(chunk, TOUR[chunk].steps.length - 1);
    }
  }, [enterChunk, persist]);

  const continueChunk = useCallback(() => {
    const current = phaseRef.current;
    if (current.kind === "chunk-break") void enterChunk(current.nextChunk);
  }, [enterChunk]);

  const requestEnd = useCallback(() => setEndConfirm(true), []);
  const cancelEnd = useCallback(() => setEndConfirm(false), []);

  const runRestore = useCallback(
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
      // Bump before the first await so an in-flight scenario swap is
      // orphaned and cannot apply a tutorial payload over the restored project.
      const run = (runRef.current += 1);
      setPhase({
        kind: "preparing",
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
            // that raced this one). The goal state is reached — but the status
            // payload carries no session once the tutorial is gone, so it
            // cannot rehydrate us. Fetch the authoritative bundle instead;
            // settling without one would close the overlay while the panel
            // still rendered the discarded practice copy, which is exactly the
            // confusion this whole path exists to prevent. If even that fails
            // we fall through to the retryable error card rather than show a
            // stale document as though it were the user's project.
            const restored = await getSessionBundle().catch(() => null);
            if (!restored) throw firstError;
            // Same guard as the success path below, and it is not redundant
            // here: acceptNativeRestore is not a restore call, so it slips
            // past the in-flight serialization and can apply newer state
            // while this fetch is still open. Settling anyway would overwrite
            // it with a bundle read before it existed.
            if (runRef.current !== run) return true;
            settle(restored);
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
          stage: "finishing",
          error: error instanceof Error ? error.message : String(error),
        });
        return false;
      }
    },
    [],
  );

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
   *
   * Restores are serialized. Every caller wants the same outcome, so a second
   * one — a double-clicked End, or an abort landing on top of an in-flight
   * finish — joins the running attempt rather than firing a request that would
   * 409 against the workspace the first attempt just finished.
   */
  const restoreOriginal = useCallback(
    ({ completed }: { completed: boolean }): Promise<boolean> => {
      const inFlight = restoreInFlightRef.current;
      if (inFlight) return inFlight;
      const attempt = runRestore({ completed });
      restoreInFlightRef.current = attempt;
      const clear = () => {
        if (restoreInFlightRef.current === attempt) {
          restoreInFlightRef.current = null;
        }
      };
      attempt.then(clear, clear);
      return attempt;
    },
    [runRestore],
  );
  restoreOriginalRef.current = (opts) => {
    void restoreOriginal(opts);
  };

  const end = useCallback(() => {
    void restoreOriginal({ completed: true });
  }, [restoreOriginal]);

  /**
   * Leave a failed restore without retrying it; the tutorial is still live.
   *
   * The tour resumes at the last visited step rather than suspending — a
   * restore that failed changed nothing, so the guided run simply continues
   * from where End was clicked. The scenario the workspace still holds is the
   * one that step was prepared against, so this needs no swap.
   */
  const stayInTutorial = useCallback(() => {
    const { chunk, step } = lastStepRef.current;
    if (!workspaceRef.current) {
      setPhase({ kind: "idle" });
      return;
    }
    setPhase({ kind: "touring", chunk, step });
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
        // Neither of these suspends the tour. The step card is non-blocking
        // (the overlay root is pointer-events-none), so the composer stays
        // usable underneath it, and the template studio simply renders over
        // it until it is closed.
        case "prefill-composer":
          current.prefillComposer(action.prefillText ?? "");
          break;
        case "open-templates":
          current.openTemplates();
          break;
      }
    },
    [],
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
      if (current.kind === "touring") persist(current.chunk, current.step);
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
    retryPrepare,
    advance,
    back,
    continueChunk,
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
