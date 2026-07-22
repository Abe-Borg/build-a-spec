/**
 * The guided tour's entire surface (Batch 6): entry-guard / discipline /
 * work-choice dialogs, the key-gate and generating bubbles, the spotlight
 * cutout + step bubble, chunk-break cards, and the paused "Resume tour"
 * pill. Renders per `useOnboarding`'s phase; null when idle.
 *
 * Deliberate posture: NO click shield. The dim is painted by a box-shadow
 * (never hit-testable) on a pointer-events-none div, so every control in
 * the app stays usable at every step — the tour directs attention, it
 * doesn't jail the user.
 */
import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import type { QcRunStatus, ResearchRunStatus, SpecDoc } from "../types";
import { usePrefersReducedMotion } from "../lib/useSmoothText";
import { buildQueue } from "../lib/reviewQueue";
import {
  anchorSelector,
  DISCIPLINES,
  TOUR,
  type TourAction,
  type TourStep,
} from "../lib/tour";
import type { DrawerName, OnboardingApi } from "../lib/useOnboarding";
import { ModalShell, primaryBtn, quietBtn } from "./ModalShell";

interface Props {
  ob: OnboardingApi;
  doc: SpecDoc | null;
  busy: boolean;
  profileComplete: boolean;
  researchStatus: ResearchRunStatus;
  qcStatus: QcRunStatus;
  hasContent: boolean;
  bumpDrawer: (name: DrawerName) => void;
}

// Synthetic steps for the pre-tour phases — module constants so their
// identity is stable across renders (the anchor effect keys on the step).
const KEY_GATE_STEP: TourStep = {
  id: "key-gate",
  anchor: "key-banner",
  placement: "bottom",
  title: "",
  body: "",
};
const GENERATING_STEP: TourStep = {
  id: "generating",
  anchor: "doc-panel",
  placement: "left",
  title: "",
  body: "",
};

interface Rect {
  top: number;
  left: number;
  width: number;
  height: number;
}

const CUTOUT_PAD = 6;

/* --- Anchor resolution + live tracking ------------------------------------ */

/**
 * Resolve a step's anchor element and track its viewport rect: retry while
 * the element doesn't exist yet (drawer contents render a frame after the
 * openNonce bump), scroll it into view, wait for the rect to settle, then
 * follow resize / any-container scroll / element resize. `missing` flips
 * after ~2s of retries — the bubble then renders centered with no cutout.
 */
function useAnchorRect(
  step: TourStep | null,
  doc: SpecDoc | null,
  reducedMotion: boolean,
): { rect: Rect | null; missing: boolean } {
  const [rect, setRect] = useState<Rect | null>(null);
  const [missing, setMissing] = useState(false);
  const docRef = useRef(doc);
  docRef.current = doc;
  // Re-resolve on committed version changes (undo/redo can delete the
  // anchored element) — not on every transient doc object identity change.
  const docVersion = doc ? `${doc.version.index}/${doc.version.count}` : "";

  useEffect(() => {
    setRect(null);
    setMissing(false);
    if (!step) return;
    let cancelled = false;
    const cleanups: (() => void)[] = [];
    const selector = anchorSelector(step, docRef.current);

    const commit = (r: DOMRect) => {
      if (cancelled) return;
      setRect({ top: r.top, left: r.left, width: r.width, height: r.height });
    };

    const follow = (target: Element) => {
      let raf = 0;
      const remeasure = () => {
        cancelAnimationFrame(raf);
        raf = requestAnimationFrame(() =>
          commit(target.getBoundingClientRect()),
        );
      };
      window.addEventListener("resize", remeasure);
      // Capture phase catches the panel's inner scroll containers too.
      window.addEventListener("scroll", remeasure, true);
      const ro =
        typeof ResizeObserver !== "undefined"
          ? new ResizeObserver(remeasure)
          : null;
      ro?.observe(target);
      cleanups.push(() => {
        cancelAnimationFrame(raf);
        window.removeEventListener("resize", remeasure);
        window.removeEventListener("scroll", remeasure, true);
        ro?.disconnect();
      });
    };

    const settleThenFollow = (target: Element) => {
      (target as HTMLElement).scrollIntoView({
        block: "center",
        behavior: reducedMotion ? "auto" : "smooth",
      });
      let prev: DOMRect | null = null;
      const started = performance.now();
      let raf = requestAnimationFrame(function settle() {
        if (cancelled) return;
        const r = target.getBoundingClientRect();
        const stable =
          prev !== null &&
          Math.abs(r.top - prev.top) < 0.5 &&
          Math.abs(r.left - prev.left) < 0.5;
        if (stable || performance.now() - started > 650) {
          commit(r);
          follow(target);
          return;
        }
        prev = r;
        raf = requestAnimationFrame(settle);
      });
      cleanups.push(() => cancelAnimationFrame(raf));
    };

    let tries = 0;
    const tryResolve = () => {
      if (cancelled) return;
      const found = selector ? document.querySelector(selector) : null;
      if (found) {
        settleThenFollow(found);
        return;
      }
      tries += 1;
      if (tries >= 14) {
        setMissing(true);
        return;
      }
      const t = window.setTimeout(tryResolve, 150);
      cleanups.push(() => clearTimeout(t));
    };
    tryResolve();

    return () => {
      cancelled = true;
      cleanups.forEach((fn) => fn());
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, docVersion, reducedMotion]);

  return { rect, missing };
}

/* --- Positioning ----------------------------------------------------------- */

const clamp = (v: number, lo: number, hi: number) =>
  Math.min(Math.max(v, lo), Math.max(lo, hi));

function placeBubble(
  rect: Rect,
  placement: TourStep["placement"],
  bw: number,
  bh: number,
): { top: number; left: number } {
  const M = 12;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const fits = {
    top: rect.top - bh - M >= 8,
    bottom: rect.top + rect.height + M + bh <= vh - 8,
    left: rect.left - bw - M >= 8,
    right: rect.left + rect.width + M + bw <= vw - 8,
  };
  let side = placement ?? "bottom";
  if (!fits[side]) {
    const flip = { top: "bottom", bottom: "top", left: "right", right: "left" }[
      side
    ] as NonNullable<TourStep["placement"]>;
    side = fits[flip]
      ? flip
      : fits.bottom
        ? "bottom"
        : fits.top
          ? "top"
          : fits.right
            ? "right"
            : "left";
  }
  let top: number;
  let left: number;
  if (side === "top" || side === "bottom") {
    top = side === "top" ? rect.top - bh - M : rect.top + rect.height + M;
    left = rect.left + rect.width / 2 - bw / 2;
  } else {
    top = rect.top + rect.height / 2 - bh / 2;
    left = side === "left" ? rect.left - bw - M : rect.left + rect.width + M;
  }
  return { top: clamp(top, 8, vh - bh - 8), left: clamp(left, 8, vw - bw - 8) };
}

/**
 * A fixed card positioned next to `rect`. While the anchor is still being
 * resolved (`pending`) it stays offscreen — no flash of a centered card;
 * with no rect and no pending resolution it centers (the missing-anchor
 * fallback: the copy still teaches even when the control isn't findable).
 */
function AnchoredCard({
  rect,
  pending,
  placement,
  children,
}: {
  rect: Rect | null;
  pending?: boolean;
  placement: TourStep["placement"];
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (!rect) {
      setPos(null);
      return;
    }
    setPos(placeBubble(rect, placement, el.offsetWidth, el.offsetHeight));
  }, [rect, placement, children]);

  const offscreen = { top: -9999, left: -9999 };
  const style = rect
    ? (pos ?? offscreen) // measured next layout pass
    : pending
      ? offscreen
      : undefined; // centered fallback via classes
  return (
    <div
      ref={ref}
      role="dialog"
      aria-modal="false"
      className={
        "fixed z-[65] w-[360px] max-w-[calc(100vw-16px)] rounded-xl border " +
        "border-edge bg-surface p-4 shadow-2xl" +
        (style ? "" : " left-1/2 top-1/3 -translate-x-1/2")
      }
      style={style}
    >
      {children}
    </div>
  );
}

/* --- Step action buttons ----------------------------------------------------- */

function StepActions({
  step,
  ob,
  doc,
  busy,
  profileComplete,
  researchStatus,
  qcStatus,
  hasContent,
}: {
  step: TourStep;
  ob: OnboardingApi;
  doc: SpecDoc | null;
  busy: boolean;
  profileComplete: boolean;
  researchStatus: ResearchRunStatus;
  qcStatus: QcRunStatus;
  hasContent: boolean;
}) {
  if (!step.actions?.length) return null;

  // A satisfied step replaces its buttons with one done-line.
  if (step.id === "profile" && profileComplete) {
    return (
      <p className="mt-3 text-xs text-ok">
        ✓ Profile complete — the research button just unlocked.
      </p>
    );
  }
  if (
    step.actions.some((a) => a.kind === "confirm-first") &&
    buildQueue(doc, "all").length === 0
  ) {
    return (
      <p className="mt-3 text-xs text-ok">
        ✓ Nothing outstanding — the queue is clear.
      </p>
    );
  }

  const render = (action: TourAction) => {
    let disabled = busy;
    let title = busy ? "Wait for the current turn to finish" : undefined;
    let doneLine: string | null = null;
    if (action.kind === "prefill-composer") {
      disabled = false;
      title = undefined;
    } else if (action.kind === "run-research") {
      if (researchStatus === "running") {
        doneLine = "⏳ Research is running — progress streams in the drawer.";
      } else if (researchStatus === "complete") {
        doneLine = "✓ Research complete — grounded items are in the drawer.";
      } else if (!profileComplete) {
        disabled = true;
        title = "Complete the project profile first (previous step)";
      }
    } else if (action.kind === "run-qc") {
      if (qcStatus === "running") {
        doneLine =
          "⏳ Final QC is running — lens progress streams in the drawer.";
      } else if (qcStatus === "complete") {
        doneLine = "✓ QC run complete — findings are in the drawer.";
      } else if (!hasContent) {
        disabled = true;
        title = "QC needs a non-empty draft";
      }
    }

    if (doneLine) {
      return (
        <p key={action.kind} className="text-xs text-ok">
          {doneLine}
        </p>
      );
    }
    return (
      <div key={action.kind}>
        <button
          onClick={() => ob.runStepAction(action)}
          disabled={disabled}
          title={title}
          className={action.kind === "prefill-composer" ? quietBtn : primaryBtn}
        >
          {action.label}
        </button>
        {action.note && (
          <p className="mt-1 text-[11px] leading-snug text-ink-faint">
            {action.note}
          </p>
        )}
      </div>
    );
  };

  return (
    <div className="mt-3 flex flex-col gap-2">{step.actions.map(render)}</div>
  );
}

/* --- The overlay ------------------------------------------------------------- */

export default function OnboardingOverlay({
  ob,
  doc,
  busy,
  profileComplete,
  researchStatus,
  qcStatus,
  hasContent,
  bumpDrawer,
}: Props) {
  const { phase } = ob;
  const reducedMotion = usePrefersReducedMotion();
  const [otherDiscipline, setOtherDiscipline] = useState("");

  const touringStep =
    phase.kind === "touring" ? TOUR[phase.chunk].steps[phase.step] : null;
  // The key-gate and generating phases reuse the anchor machinery through
  // synthetic steps (spotlight on the banner; plain card on the panel).
  const syntheticStep: TourStep | null =
    phase.kind === "key-gate"
      ? KEY_GATE_STEP
      : phase.kind === "generating" && !phase.error
        ? GENERATING_STEP
        : null;
  const activeStep = touringStep ?? syntheticStep;
  const { rect, missing } = useAnchorRect(activeStep, doc, reducedMotion);

  // Ensure the step's drawer is open (idempotent — a doubled StrictMode
  // invocation just bumps the nonce twice).
  useEffect(() => {
    if (touringStep?.drawer) bumpDrawer(touringStep.drawer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [touringStep]);

  // Escape: pause a live step / back out of a dialog. Never marks complete.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (phase.kind === "touring" || phase.kind === "chunk-break") ob.pause();
      else if (
        phase.kind === "entry-guard" ||
        phase.kind === "discipline" ||
        phase.kind === "key-gate"
      )
        ob.abort();
      else if (phase.kind === "work-choice")
        phase.resume ? ob.backToTour() : ob.abort();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [phase, ob]);

  if (phase.kind === "idle") return null;

  /* Entry guard: the tour needs a blank session. */
  if (phase.kind === "entry-guard") {
    return (
      <ModalShell title="Take the guided tour" onClose={ob.abort}>
        <p className="text-sm leading-relaxed text-ink-dim">
          The tour drafts a small demo spec into a blank session, and this
          session already has content. Start fresh to run it — and if the
          current work matters, use the panel&apos;s <b>Save</b> button
          first; a saved project reopens exactly as it was.
        </p>
        <div className="mt-4 flex gap-2">
          <button onClick={ob.confirmFreshStart} className={primaryBtn}>
            Start fresh &amp; begin
          </button>
          <button onClick={ob.abort} className={quietBtn}>
            Cancel
          </button>
        </div>
      </ModalShell>
    );
  }

  /* Discipline picker — before anything is generated. */
  if (phase.kind === "discipline") {
    return (
      <ModalShell title="What's your discipline?" onClose={ob.abort} wide>
        <p className="text-sm leading-relaxed text-ink-dim">
          The tour opens by drafting a short demo spec section in your
          discipline — a live teaching prop, not a real deliverable.
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          {DISCIPLINES.map((d) => (
            <button
              key={d}
              onClick={() => ob.chooseDiscipline(d)}
              className={quietBtn}
            >
              {d}
            </button>
          ))}
        </div>
        <div className="mt-3 flex gap-2">
          <input
            value={otherDiscipline}
            onChange={(e) => setOtherDiscipline(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && otherDiscipline.trim())
                ob.chooseDiscipline(otherDiscipline.trim());
            }}
            placeholder="Other — e.g. Structural, Civil…"
            className="min-w-0 flex-1 rounded-lg border border-edge bg-raised px-3 py-1.5 text-sm text-ink placeholder:text-ink-faint focus:border-accent focus:outline-none"
          />
          <button
            onClick={() => ob.chooseDiscipline(otherDiscipline.trim())}
            disabled={!otherDiscipline.trim()}
            className={primaryBtn}
          >
            Start
          </button>
        </div>
      </ModalShell>
    );
  }

  /* Key gate: spotlight the banner; auto-advances when the key lands. */
  if (phase.kind === "key-gate") {
    return (
      <>
        {rect && <Cutout rect={rect} reducedMotion={reducedMotion} />}
        <AnchoredCard rect={rect} pending={!missing} placement="bottom">
          <CardHeader
            kicker="Before the demo"
            title="First: your Anthropic API key"
            onClose={ob.abort}
          />
          <p className="mt-1 text-sm leading-relaxed text-ink-dim">
            The demo drafts with your key — paste it in the banner above.
            It&apos;s stored in your OS credential manager and sent nowhere
            but Anthropic. The tour continues automatically once it&apos;s
            saved.
          </p>
        </AnchoredCard>
      </>
    );
  }

  /* Generating: narrate the live stream (no dim — watch both panes). */
  if (phase.kind === "generating") {
    if (phase.error) {
      return (
        <ModalShell title="The demo didn't start" onClose={ob.abort}>
          <p className="text-sm leading-relaxed text-err">{phase.error}</p>
          <div className="mt-4 flex gap-2">
            <button onClick={ob.retryGenerate} className={primaryBtn}>
              Retry
            </button>
            <button onClick={ob.abort} className={quietBtn}>
              Exit tour
            </button>
          </div>
        </ModalShell>
      );
    }
    return (
      <AnchoredCard rect={rect} pending={!missing} placement="left">
        <CardHeader
          kicker="Drafting the demo"
          title={`A small ${phase.discipline} section`}
          onClose={ob.abort}
        />
        <p className="mt-1 text-sm leading-relaxed text-ink-dim">
          Watch the paper on the right — each edit lands live as the model
          drafts, and its running commentary streams in the chat. The tour
          begins the moment the demo finishes.
        </p>
      </AnchoredCard>
    );
  }

  /* Chunk break: pause point between chunks. */
  if (phase.kind === "chunk-break") {
    const doneChunk = TOUR[phase.nextChunk - 1];
    const nextChunk = TOUR[phase.nextChunk];
    return (
      <ModalShell
        title={`Part ${phase.nextChunk} of ${TOUR.length} done — ${doneChunk.title}`}
        onClose={ob.pause}
      >
        <div className="flex items-center gap-1.5" aria-hidden>
          {TOUR.map((c, i) => (
            <span
              key={c.id}
              className={
                "h-1.5 flex-1 rounded-full " +
                (i < phase.nextChunk ? "bg-accent" : "bg-raised")
              }
            />
          ))}
        </div>
        <p className="mt-3 text-sm leading-relaxed text-ink-dim">
          Next up: <b className="text-ink">{nextChunk.title}</b>. Or pause
          here — ask a question in the chat, or start real work now.
        </p>
        <div className="mt-4 flex flex-wrap gap-2">
          <button onClick={ob.continueChunk} className={primaryBtn}>
            Continue — {nextChunk.title}
          </button>
          <button onClick={ob.askQuestion} className={quietBtn}>
            Ask a question
          </button>
          <button onClick={ob.startRealWork} className={quietBtn}>
            Start real work
          </button>
        </div>
      </ModalShell>
    );
  }

  /* Paused: the floating resume pill. */
  if (phase.kind === "paused") {
    return (
      <div className="fixed bottom-5 left-5 z-[65] flex items-center gap-1.5">
        <button
          onClick={ob.resume}
          className="rounded-full border border-accent/60 bg-surface px-4 py-2 text-sm text-accent shadow-2xl transition-colors hover:bg-accent/10"
        >
          ▶ Resume tour
        </button>
        <button
          onClick={ob.startRealWork}
          aria-label="End tour"
          title="End the tour"
          className="flex h-8 w-8 items-center justify-center rounded-full border border-edge bg-surface text-ink-dim shadow-2xl transition-colors hover:border-accent hover:text-accent"
        >
          ✕
        </button>
      </div>
    );
  }

  /* Work choice: start fresh vs keep the demo. */
  if (phase.kind === "work-choice") {
    return (
      <ModalShell
        title="Start real work"
        onClose={phase.resume ? ob.backToTour : ob.abort}
      >
        <p className="text-sm leading-relaxed text-ink-dim">
          Keep the demo as a scratch starting point, or clear everything for
          a blank page — the starter prompts will be waiting.
        </p>
        <div className="mt-4 flex flex-wrap gap-2">
          <button onClick={ob.chooseFresh} className={primaryBtn}>
            Start fresh
          </button>
          <button onClick={ob.chooseKeep} className={quietBtn}>
            Keep current progress
          </button>
          {phase.resume && (
            <button onClick={ob.backToTour} className={quietBtn}>
              Back to the tour
            </button>
          )}
        </div>
      </ModalShell>
    );
  }

  /* Touring: spotlight + step bubble. */
  const chunk = TOUR[phase.chunk];
  const step = chunk.steps[phase.step];
  const atFirst = phase.chunk === 0 && phase.step === 0;
  return (
    <>
      {rect && <Cutout rect={rect} reducedMotion={reducedMotion} />}
      <AnchoredCard rect={rect} pending={!missing} placement={step.placement}>
        <CardHeader
          kicker={`Part ${phase.chunk + 1} of ${TOUR.length} — ${chunk.title} · step ${
            phase.step + 1
          }/${chunk.steps.length}`}
          title={step.title}
          onClose={ob.pause}
        />
        <p className="mt-1 text-sm leading-relaxed text-ink-dim">{step.body}</p>
        <StepActions
          step={step}
          ob={ob}
          doc={doc}
          busy={busy}
          profileComplete={profileComplete}
          researchStatus={researchStatus}
          qcStatus={qcStatus}
          hasContent={hasContent}
        />
        <div className="mt-4 flex items-center gap-2">
          {!atFirst && (
            <button onClick={ob.back} className={quietBtn}>
              ‹ Back
            </button>
          )}
          <button onClick={ob.advance} className={primaryBtn}>
            {step.continueLabel ?? "Continue"}
          </button>
          <span className="flex-1" />
          <button
            onClick={ob.startRealWork}
            className="text-xs text-ink-faint underline-offset-2 transition-colors hover:text-ink hover:underline"
          >
            Skip tour
          </button>
        </div>
      </AnchoredCard>
    </>
  );
}

/* --- Small shared pieces ------------------------------------------------------ */

function CardHeader({
  kicker,
  title,
  onClose,
}: {
  kicker: string;
  title: string;
  onClose: () => void;
}) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div>
        <p className="text-[11px] uppercase tracking-wide text-ink-faint">
          {kicker}
        </p>
        <h3 className="mt-0.5 font-[family-name:var(--font-display)] text-base font-semibold text-ink">
          {title}
        </h3>
      </div>
      <button
        onClick={onClose}
        aria-label="Dismiss"
        className="rounded-md px-1.5 py-0.5 text-ink-dim transition-colors hover:bg-raised hover:text-ink"
      >
        ✕
      </button>
    </div>
  );
}

/** The spotlight: a pointer-events-none ring whose shadow paints the dim. */
function Cutout({
  rect,
  reducedMotion,
}: {
  rect: Rect;
  reducedMotion: boolean;
}) {
  return (
    <div
      aria-hidden
      className="pointer-events-none fixed z-[60] rounded-[10px]"
      style={{
        top: rect.top - CUTOUT_PAD,
        left: rect.left - CUTOUT_PAD,
        width: rect.width + CUTOUT_PAD * 2,
        height: rect.height + CUTOUT_PAD * 2,
        boxShadow:
          "0 0 0 9999px rgba(0, 0, 0, 0.55), 0 0 0 2px var(--color-accent)",
        transition: reducedMotion
          ? "none"
          : "top 0.2s ease, left 0.2s ease, width 0.2s ease, height 0.2s ease",
      }}
    />
  );
}
