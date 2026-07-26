import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import type { SpecDoc } from "../types";
import { usePrefersReducedMotion } from "../lib/useSmoothText";
import { anchorSelector, TOUR, type TourStep } from "../lib/tour";
import type { DrawerName, OnboardingApi } from "../lib/useOnboarding";
import { ModalShell, primaryBtn, quietBtn } from "./ModalShell";

interface Props {
  ob: OnboardingApi;
  doc: SpecDoc | null;
  bumpDrawer: (name: DrawerName) => void;
}

interface Rect {
  top: number;
  left: number;
  width: number;
  height: number;
}

const CUTOUT_PAD = 6;

function useAnchorRect(
  step: TourStep | null,
  doc: SpecDoc | null,
  reducedMotion: boolean,
): { rect: Rect | null; missing: boolean } {
  const [rect, setRect] = useState<Rect | null>(null);
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    setRect(null);
    setMissing(false);
    if (!step) return;

    let cancelled = false;
    let retryTimer = 0;
    let frame = 0;
    let observer: ResizeObserver | null = null;
    const cleanupRef = { current: () => {} };
    const selector = anchorSelector(step, doc);

    const commit = (target: Element) => {
      if (cancelled) return;
      const value = target.getBoundingClientRect();
      setRect({
        top: value.top,
        left: value.left,
        width: value.width,
        height: value.height,
      });
    };

    const resolve = (attempt = 0) => {
      if (cancelled) return;
      const target = selector ? document.querySelector(selector) : null;
      if (!target) {
        if (attempt >= 13) {
          setMissing(true);
          return;
        }
        retryTimer = window.setTimeout(() => resolve(attempt + 1), 150);
        return;
      }

      target.scrollIntoView({
        block: "center",
        behavior: reducedMotion ? "auto" : "smooth",
      });
      frame = requestAnimationFrame(() => commit(target));
      const remeasure = () => {
        cancelAnimationFrame(frame);
        frame = requestAnimationFrame(() => commit(target));
      };
      window.addEventListener("resize", remeasure);
      window.addEventListener("scroll", remeasure, true);
      observer =
        typeof ResizeObserver === "undefined"
          ? null
          : new ResizeObserver(remeasure);
      observer?.observe(target);

      const cleanupTracking = () => {
        window.removeEventListener("resize", remeasure);
        window.removeEventListener("scroll", remeasure, true);
      };
      cleanupRef.current = cleanupTracking;
    };

    resolve();
    return () => {
      cancelled = true;
      window.clearTimeout(retryTimer);
      cancelAnimationFrame(frame);
      observer?.disconnect();
      cleanupRef.current();
    };
  }, [step, doc?.version.index, doc?.version.count, reducedMotion]);

  return { rect, missing };
}

const clamp = (value: number, low: number, high: number) =>
  Math.min(Math.max(value, low), Math.max(low, high));

function placeBubble(
  rect: Rect,
  placement: TourStep["placement"],
  width: number,
  height: number,
): { top: number; left: number } {
  const margin = 12;
  const viewportWidth = window.innerWidth;
  const viewportHeight = window.innerHeight;
  const fits = {
    top: rect.top - height - margin >= 8,
    bottom: rect.top + rect.height + margin + height <= viewportHeight - 8,
    left: rect.left - width - margin >= 8,
    right: rect.left + rect.width + margin + width <= viewportWidth - 8,
  };
  let side = placement ?? "bottom";
  if (!fits[side]) {
    const opposite = {
      top: "bottom",
      bottom: "top",
      left: "right",
      right: "left",
    }[side] as NonNullable<TourStep["placement"]>;
    side = fits[opposite]
      ? opposite
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
    top =
      side === "top"
        ? rect.top - height - margin
        : rect.top + rect.height + margin;
    left = rect.left + rect.width / 2 - width / 2;
  } else {
    top = rect.top + rect.height / 2 - height / 2;
    left =
      side === "left"
        ? rect.left - width - margin
        : rect.left + rect.width + margin;
  }
  return {
    top: clamp(top, 8, viewportHeight - height - 8),
    left: clamp(left, 8, viewportWidth - width - 8),
  };
}

function AnchoredCard({
  rect,
  pending,
  placement,
  children,
}: {
  rect: Rect | null;
  pending: boolean;
  placement: TourStep["placement"];
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState<{ top: number; left: number } | null>(
    null,
  );

  useLayoutEffect(() => {
    const element = ref.current;
    if (!element || !rect) {
      setPosition(null);
      return;
    }
    setPosition(
      placeBubble(rect, placement, element.offsetWidth, element.offsetHeight),
    );
  }, [rect, placement]);

  const style = rect
    ? (position ?? { top: -9999, left: -9999 })
    : pending
      ? { top: -9999, left: -9999 }
      : undefined;
  return (
    <div
      ref={ref}
      role="dialog"
      aria-modal="true"
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

export default function OnboardingOverlay({ ob, doc, bumpDrawer }: Props) {
  const { phase } = ob;
  const reducedMotion = usePrefersReducedMotion();
  const step =
    phase.kind === "touring" ? TOUR[phase.chunk].steps[phase.step] : null;
  const { rect, missing } = useAnchorRect(step, doc, reducedMotion);

  useEffect(() => {
    if (step?.drawer) bumpDrawer(step.drawer);
  }, [step, bumpDrawer]);

  useEffect(() => {
    if (phase.kind === "idle") return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !ob.endConfirm) ob.requestEnd();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [phase.kind, ob]);

  if (phase.kind === "idle") return null;

  if (phase.kind === "chunk-break") {
    const completed = TOUR[phase.nextChunk - 1];
    const next = TOUR[phase.nextChunk];
    return (
      <ModalShell
        title={
          "Part " +
          phase.nextChunk +
          " of " +
          TOUR.length +
          " done — " +
          completed.title
        }
        onClose={ob.requestEnd}
      >
        <div className="flex items-center gap-1.5" aria-hidden>
          {TOUR.map((chunk, index) => (
            <span
              key={chunk.id}
              className={
                "h-1.5 flex-1 rounded-full " +
                (index < phase.nextChunk ? "bg-accent" : "bg-raised")
              }
            />
          ))}
        </div>
        <p className="mt-3 text-sm leading-relaxed text-ink-dim">
          Next up: <b className="text-ink">{next.title}</b>.
        </p>
        <div className="mt-4 flex items-center gap-2">
          <button onClick={ob.continueChunk} className={primaryBtn}>
            Continue — {next.title}
          </button>
          <span className="flex-1" />
          <button onClick={ob.requestEnd} className={quietBtn}>
            End tour
          </button>
        </div>
      </ModalShell>
    );
  }

  const chunk = TOUR[phase.chunk];
  const active = chunk.steps[phase.step];
  const atFirst = phase.chunk === 0 && phase.step === 0;
  return (
    <>
      <div
        className={
          "fixed inset-0 z-[60] cursor-default " +
          (rect ? "bg-transparent" : "bg-black/55")
        }
        aria-hidden
      />
      {rect && <Cutout rect={rect} reducedMotion={reducedMotion} />}
      <AnchoredCard
        rect={rect}
        pending={!missing}
        placement={active.placement}
      >
        <CardHeader
          kicker={
            "Part " +
            (phase.chunk + 1) +
            " of " +
            TOUR.length +
            " — " +
            chunk.title +
            " · step " +
            (phase.step + 1) +
            "/" +
            chunk.steps.length
          }
          title={active.title}
          onClose={ob.requestEnd}
        />
        <p className="mt-1 text-sm leading-relaxed text-ink-dim">
          {active.body}
        </p>
        {active.details && (
          <dl className="mt-3 space-y-1.5 border-t border-edge pt-2.5">
            {active.details.map((item) => (
              <div key={item.id} className="text-[11px] leading-snug">
                <dt className="inline font-medium text-ink">{item.label}: </dt>
                <dd className="inline text-ink-dim">{item.description}</dd>
              </div>
            ))}
          </dl>
        )}
        <div className="mt-4 flex items-center gap-2">
          {!atFirst && (
            <button onClick={ob.back} className={quietBtn}>
              ‹ Back
            </button>
          )}
          <button onClick={ob.advance} className={primaryBtn}>
            {active.continueLabel ?? "Continue"}
          </button>
          <span className="flex-1" />
          <button onClick={ob.requestEnd} className={quietBtn}>
            End tour
          </button>
        </div>
      </AnchoredCard>
    </>
  );
}

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
        aria-label="End tour"
        className="rounded-md px-1.5 py-0.5 text-ink-dim transition-colors hover:bg-raised hover:text-ink"
      >
        ✕
      </button>
    </div>
  );
}

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
      className="pointer-events-none fixed z-[61] rounded-[10px]"
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
