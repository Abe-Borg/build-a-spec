import { useEffect, useRef, useState } from "react";

/** True when the user asked for reduced motion (live-updates on change). */
function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(
    () =>
      typeof window !== "undefined" &&
      !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches,
  );
  useEffect(() => {
    const mq = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    if (!mq) return;
    const on = () => setReduced(mq.matches);
    mq.addEventListener?.("change", on);
    return () => mq.removeEventListener?.("change", on);
  }, []);
  return reduced;
}

/**
 * Typewriter smoothing for streamed text. Deltas arrive in network bursts;
 * this drains toward the latest `target` a few characters per animation
 * frame so the text flows instead of jumping in chunks. The catch-up rate
 * scales with the backlog (`ceil(backlog / 30)`, min 2) so it never lags the
 * stream by more than ~half a second. Flushes instantly when `active` goes
 * false (turn end / unmount) and when `prefers-reduced-motion` is set.
 */
export function useSmoothText(target: string, active: boolean): string {
  const reduced = usePrefersReducedMotion();
  const [displayed, setDisplayed] = useState(target);
  const targetRef = useRef(target);
  const displayedRef = useRef(target);
  targetRef.current = target;

  // Snap (no animation) when reduced-motion is on, the stream has ended, or
  // the target shrank out from under us (a fresh message reusing the hook).
  const snap = reduced || !active || target.length < displayedRef.current.length;

  useEffect(() => {
    if (snap) {
      displayedRef.current = targetRef.current;
      setDisplayed(targetRef.current);
      return;
    }
    // One persistent rAF loop reads the ref each frame, so new deltas are
    // picked up without restarting the loop.
    let raf = requestAnimationFrame(function tick() {
      const t = targetRef.current;
      const cur = displayedRef.current;
      if (cur.length < t.length) {
        const step = Math.max(2, Math.ceil((t.length - cur.length) / 30));
        const next = t.slice(0, cur.length + step);
        displayedRef.current = next;
        setDisplayed(next);
      }
      raf = requestAnimationFrame(tick);
    });
    return () => cancelAnimationFrame(raf);
  }, [snap]);

  return displayed;
}

/** Split streamed markdown at the last paragraph break: `[stablePrefix, liveTail]`. */
export function splitStableTail(text: string): [string, string] {
  const idx = text.lastIndexOf("\n\n");
  if (idx === -1) return ["", text];
  return [text.slice(0, idx + 2), text.slice(idx + 2)];
}
