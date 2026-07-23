/**
 * The guided-tour state machine (Batch 6). App owns the app data; this hook
 * owns only the tour's phase and the transitions between phases. All of
 * App's capabilities arrive per render and are stored in a latest-ref, so
 * the machine never closes over stale state (StrictMode-safe), and every
 * await re-checks an internal run counter — the frontend mirror of the
 * backend's `generation` zombie-turn guard — so an aborted tour can never
 * advance from a completing await.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import type { EditOp, Health, SpecDoc } from "../types";
import { startOnboardingDemo } from "./api";
import { buildQueue } from "./reviewQueue";
import { markOnboardingCompleted } from "./onboardingStorage";
import { DEMO_PROFILE, TOUR, type TourAction } from "./tour";

export type DrawerName = "review" | "research" | "qc" | "openItems";

export type OnboardingPhase =
  | { kind: "idle" }
  | { kind: "entry-guard" }
  | { kind: "discipline" }
  | { kind: "key-gate"; discipline: string }
  | { kind: "generating"; discipline: string; error: string | null }
  | { kind: "touring"; chunk: number; step: number }
  | { kind: "chunk-break"; nextChunk: number }
  | { kind: "paused"; chunk: number; step: number }
  | { kind: "work-choice"; resume: { chunk: number; step: number } | null };

/** Everything the tour can do to the app, provided by App each render. */
export interface OnboardingCaps {
  /** App.send — resolves true when the turn completed cleanly. */
  send: (text: string) => Promise<boolean>;
  editDoc: (ops: EditOp[]) => Promise<void>;
  startResearch: () => void;
  startQc: () => void;
  /** App.newSession WITHOUT an abort() call inside (see App wiring). */
  newSession: () => Promise<void>;
  prefillComposer: (text: string) => void;
  health: Health | null;
  doc: SpecDoc | null;
  hasContent: boolean;
}

export interface OnboardingApi {
  phase: OnboardingPhase;
  /**
   * Close (✕ / backdrop) on any tour popup opens an end-or-continue
   * confirmation instead of silently exiting; true while it's showing.
   */
  endConfirm: boolean;
  /** Open the end-or-continue confirmation (wired to every popup's close). */
  requestEnd: () => void;
  /** Dismiss the confirmation and stay on the current popup ("Continue"). */
  cancelEnd: () => void;
  /** The chip / Header entry point (runs the has-content entry guard). */
  start: () => void;
  confirmFreshStart: () => void;
  chooseDiscipline: (discipline: string) => void;
  retryGenerate: () => void;
  advance: () => void;
  back: () => void;
  continueChunk: () => void;
  pause: () => void;
  resume: () => void;
  askQuestion: () => void;
  startRealWork: () => void;
  backToTour: () => void;
  chooseFresh: () => void;
  chooseKeep: () => void;
  runStepAction: (action: TourAction) => void;
  /** Kill the tour outright (session reset / project load / explicit exit). */
  abort: () => void;
}

export function useOnboarding(caps: OnboardingCaps): OnboardingApi {
  const [phase, setPhase] = useState<OnboardingPhase>({ kind: "idle" });
  // Whether the "End the tour or continue?" confirmation is showing. Orthogonal
  // to `phase`: opening it never changes the underlying popup, so "Continue"
  // just drops it and the user is exactly where they were.
  const [endConfirm, setEndConfirm] = useState(false);
  const capsRef = useRef(caps);
  capsRef.current = caps;
  const phaseRef = useRef(phase);
  phaseRef.current = phase;
  // Bumped by abort()/start(); in-flight async work bails when it changed.
  const runRef = useRef(0);
  // Collapses a StrictMode double-invoke of the key-gate effect.
  const generatingRef = useRef(false);

  const abort = useCallback(() => {
    runRef.current += 1;
    generatingRef.current = false;
    setEndConfirm(false);
    setPhase({ kind: "idle" });
  }, []);

  const start = useCallback(() => {
    runRef.current += 1;
    generatingRef.current = false;
    setEndConfirm(false);
    setPhase(
      capsRef.current.hasContent ? { kind: "entry-guard" } : { kind: "discipline" },
    );
  }, []);

  const requestEnd = useCallback(() => setEndConfirm(true), []);
  const cancelEnd = useCallback(() => setEndConfirm(false), []);

  const generate = useCallback(async (discipline: string) => {
    if (generatingRef.current) return;
    generatingRef.current = true;
    const run = runRef.current;
    setPhase({ kind: "generating", discipline, error: null });
    try {
      const message = await startOnboardingDemo(discipline);
      if (runRef.current !== run) return;
      const ok = await capsRef.current.send(message);
      if (runRef.current !== run) return;
      if (ok) {
        setPhase({ kind: "touring", chunk: 0, step: 0 });
      } else {
        setPhase({
          kind: "generating",
          discipline,
          error:
            "The demo turn failed and the document was rolled back — " +
            "it's safe to retry.",
        });
      }
    } catch (e) {
      if (runRef.current !== run) return;
      setPhase({
        kind: "generating",
        discipline,
        error: e instanceof Error ? e.message : String(e),
      });
    } finally {
      if (runRef.current === run) generatingRef.current = false;
    }
  }, []);

  const confirmFreshStart = useCallback(async () => {
    const run = runRef.current;
    await capsRef.current.newSession();
    if (runRef.current !== run) return;
    setPhase({ kind: "discipline" });
  }, []);

  const chooseDiscipline = useCallback(
    (discipline: string) => {
      if (!capsRef.current.health?.api_key_present) {
        setPhase({ kind: "key-gate", discipline });
        return;
      }
      void generate(discipline);
    },
    [generate],
  );

  // The key-gate auto-advance: the ApiKeyBanner's save already refreshes
  // health, so a fresh key flips api_key_present and the demo starts on
  // its own. generatingRef makes a doubled effect invocation a no-op.
  const keyPresent = caps.health?.api_key_present ?? false;
  useEffect(() => {
    const p = phaseRef.current;
    if (p.kind === "key-gate" && keyPresent) void generate(p.discipline);
  }, [keyPresent, generate]);

  const retryGenerate = useCallback(() => {
    const p = phaseRef.current;
    if (p.kind === "generating" && p.error) void generate(p.discipline);
  }, [generate]);

  const advance = useCallback(() => {
    const p = phaseRef.current;
    if (p.kind !== "touring") return;
    const chunk = TOUR[p.chunk];
    if (p.step + 1 < chunk.steps.length) {
      setPhase({ kind: "touring", chunk: p.chunk, step: p.step + 1 });
    } else if (p.chunk + 1 < TOUR.length) {
      setPhase({ kind: "chunk-break", nextChunk: p.chunk + 1 });
    } else {
      setPhase({ kind: "work-choice", resume: null });
    }
  }, []);

  const back = useCallback(() => {
    const p = phaseRef.current;
    if (p.kind !== "touring") return;
    if (p.step > 0) {
      setPhase({ kind: "touring", chunk: p.chunk, step: p.step - 1 });
    } else if (p.chunk > 0) {
      setPhase({
        kind: "touring",
        chunk: p.chunk - 1,
        step: TOUR[p.chunk - 1].steps.length - 1,
      });
    }
  }, []);

  const continueChunk = useCallback(() => {
    const p = phaseRef.current;
    if (p.kind === "chunk-break") {
      setPhase({ kind: "touring", chunk: p.nextChunk, step: 0 });
    }
  }, []);

  const pause = useCallback(() => {
    const p = phaseRef.current;
    if (p.kind === "touring") {
      setPhase({ kind: "paused", chunk: p.chunk, step: p.step });
    } else if (p.kind === "chunk-break") {
      setPhase({ kind: "paused", chunk: p.nextChunk, step: 0 });
    }
  }, []);

  const resume = useCallback(() => {
    const p = phaseRef.current;
    if (p.kind === "paused") {
      setPhase({ kind: "touring", chunk: p.chunk, step: p.step });
    }
  }, []);

  /** "Ask a question": focus the composer and step aside until resumed. */
  const askQuestion = useCallback(() => {
    capsRef.current.prefillComposer("");
    pause();
  }, [pause]);

  const startRealWork = useCallback(() => {
    const p = phaseRef.current;
    const resumePoint =
      p.kind === "touring"
        ? { chunk: p.chunk, step: p.step }
        : p.kind === "chunk-break"
          ? { chunk: p.nextChunk, step: 0 }
          : p.kind === "paused"
            ? { chunk: p.chunk, step: p.step }
            : null;
    setPhase({ kind: "work-choice", resume: resumePoint });
  }, []);

  const backToTour = useCallback(() => {
    const p = phaseRef.current;
    if (p.kind === "work-choice" && p.resume) {
      setPhase({ kind: "touring", ...p.resume });
    }
  }, []);

  const chooseFresh = useCallback(async () => {
    markOnboardingCompleted();
    const run = (runRef.current += 1);
    setPhase({ kind: "idle" });
    await capsRef.current.newSession();
    void run; // phase already terminal; the reset needs no follow-up here
  }, []);

  const chooseKeep = useCallback(() => {
    markOnboardingCompleted();
    setPhase({ kind: "idle" });
  }, []);

  const runStepAction = useCallback((action: TourAction) => {
    const c = capsRef.current;
    switch (action.kind) {
      case "profile-fill":
        void c.editDoc([
          { action: "set_project_profile", target_id: "sec", ...DEMO_PROFILE },
        ]);
        break;
      case "confirm-first": {
        const entry = buildQueue(c.doc, "all")[0];
        if (entry) {
          void c.editDoc([
            {
              action: "set_status",
              target_id: entry.elementId,
              status: "confirmed",
            },
          ]);
        }
        break;
      }
      case "run-research":
        c.startResearch();
        break;
      case "run-qc":
        c.startQc();
        break;
      case "prefill-composer":
        c.prefillComposer(action.prefillText ?? "");
        pause();
        break;
    }
  }, [pause]);

  return {
    phase,
    endConfirm,
    requestEnd,
    cancelEnd,
    start,
    confirmFreshStart,
    chooseDiscipline,
    retryGenerate,
    advance,
    back,
    continueChunk,
    pause,
    resume,
    askQuestion,
    startRealWork,
    backToTour,
    chooseFresh,
    chooseKeep,
    runStepAction,
    abort,
  };
}
