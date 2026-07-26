import { useCallback, useRef, useState } from "react";
import { markOnboardingCompleted } from "./onboardingStorage";
import { TOUR } from "./tour";

export type DrawerName = "review" | "research" | "qc" | "openItems";

export type OnboardingPhase =
  | { kind: "idle" }
  | { kind: "touring"; chunk: number; step: number }
  | { kind: "chunk-break"; nextChunk: number };

export interface OnboardingApi {
  phase: OnboardingPhase;
  endConfirm: boolean;
  start: () => void;
  advance: () => void;
  back: () => void;
  continueChunk: () => void;
  requestEnd: () => void;
  cancelEnd: () => void;
  /** Explicit user exit: records the tour as seen. */
  end: () => void;
  /** External teardown (new/open session): does not mark completion. */
  abort: () => void;
}

export function useOnboarding(): OnboardingApi {
  const [phase, setPhase] = useState<OnboardingPhase>({ kind: "idle" });
  const [endConfirm, setEndConfirm] = useState(false);
  const phaseRef = useRef(phase);
  phaseRef.current = phase;

  const start = useCallback(() => {
    setEndConfirm(false);
    setPhase({ kind: "touring", chunk: 0, step: 0 });
  }, []);

  const finish = useCallback(() => {
    markOnboardingCompleted();
    setEndConfirm(false);
    setPhase({ kind: "idle" });
  }, []);

  const advance = useCallback(() => {
    const current = phaseRef.current;
    if (current.kind !== "touring") return;
    const chunk = TOUR[current.chunk];
    if (current.step + 1 < chunk.steps.length) {
      setPhase({
        kind: "touring",
        chunk: current.chunk,
        step: current.step + 1,
      });
    } else if (current.chunk + 1 < TOUR.length) {
      setPhase({ kind: "chunk-break", nextChunk: current.chunk + 1 });
    } else {
      finish();
    }
  }, [finish]);

  const back = useCallback(() => {
    const current = phaseRef.current;
    if (current.kind !== "touring") return;
    if (current.step > 0) {
      setPhase({
        kind: "touring",
        chunk: current.chunk,
        step: current.step - 1,
      });
    } else if (current.chunk > 0) {
      setPhase({
        kind: "touring",
        chunk: current.chunk - 1,
        step: TOUR[current.chunk - 1].steps.length - 1,
      });
    }
  }, []);

  const continueChunk = useCallback(() => {
    const current = phaseRef.current;
    if (current.kind === "chunk-break") {
      setPhase({ kind: "touring", chunk: current.nextChunk, step: 0 });
    }
  }, []);

  const requestEnd = useCallback(() => setEndConfirm(true), []);
  const cancelEnd = useCallback(() => setEndConfirm(false), []);
  const abort = useCallback(() => {
    setEndConfirm(false);
    setPhase({ kind: "idle" });
  }, []);

  return {
    phase,
    endConfirm,
    start,
    advance,
    back,
    continueChunk,
    requestEnd,
    cancelEnd,
    end: finish,
    abort,
  };
}
