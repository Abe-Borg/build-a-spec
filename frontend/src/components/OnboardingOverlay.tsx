import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import type {
  DocParagraph,
  QcRunStatus,
  ResearchRunStatus,
  SpecDoc,
} from "../types";
import { buildQueue } from "../lib/reviewQueue";
import { usePrefersReducedMotion } from "../lib/useSmoothText";
import { anchorSelector, TOUR, type TourStep } from "../lib/tour";
import type { DrawerName, OnboardingApi } from "../lib/useOnboarding";
import { ModalShell, primaryBtn, quietBtn } from "./ModalShell";

interface Props {
  ob: OnboardingApi;
  doc: SpecDoc | null;
  busy: boolean;
  hasContent: boolean;
  profileComplete: boolean;
  researchStatus: ResearchRunStatus;
  qcStatus: QcRunStatus;
  sourceAvailable: boolean;
  bumpDrawer: (name: DrawerName) => void;
}

interface Rect {
  top: number;
  left: number;
  width: number;
  height: number;
}

const CUTOUT_PAD = 6;

/**
 * Stable structural fingerprint used by the rearrangement exercise. Text and
 * numbering are deliberately excluded: completion means that a real sibling
 * order changed, not merely that the document received some other edit.
 */
export function documentOrderSignature(doc: SpecDoc | null): string {
  if (!doc) return "";
  const paragraphOrders: string[] = [];
  const visit = (parentId: string, paragraphs: DocParagraph[]) => {
    paragraphOrders.push(`${parentId}>${paragraphs.map((item) => item.id).join(",")}`);
    for (const paragraph of paragraphs) visit(paragraph.id, paragraph.children);
  };
  const articleOrders = doc.parts.map((part) => {
    for (const article of part.articles) visit(article.id, article.paragraphs);
    return `${part.id}>${part.articles.map((article) => article.id).join(",")}`;
  });
  return [...articleOrders, ...paragraphOrders].join("|");
}

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
    let timer = 0;
    let frame = 0;
    let observer: ResizeObserver | null = null;
    let cleanupTracking = () => {};
    const selector = anchorSelector(step, doc);

    const resolve = (attempt = 0) => {
      if (cancelled) return;
      let target: Element | null = null;
      try {
        target = selector ? document.querySelector(selector) : null;
      } catch {
        target = null;
      }
      if (!target) {
        if (attempt >= 13) {
          // Feature coverage never degrades into a context-free centered card.
          // Anchor remediation to the actual paper when the requested control
          // is unavailable in this UI state.
          target = document.querySelector('[data-tour="doc-panel"]');
          setMissing(true);
          if (!target) return;
        } else {
          timer = window.setTimeout(() => resolve(attempt + 1), 150);
          return;
        }
      }
      target.scrollIntoView({
        block: "center",
        behavior: reducedMotion ? "auto" : "smooth",
      });
      const commit = () => {
        const value = target!.getBoundingClientRect();
        setRect({
          top: value.top,
          left: value.left,
          width: value.width,
          height: value.height,
        });
      };
      const remeasure = () => {
        cancelAnimationFrame(frame);
        frame = requestAnimationFrame(commit);
      };
      frame = requestAnimationFrame(commit);
      window.addEventListener("resize", remeasure);
      window.addEventListener("scroll", remeasure, true);
      observer =
        typeof ResizeObserver === "undefined"
          ? null
          : new ResizeObserver(remeasure);
      observer?.observe(target);
      cleanupTracking = () => {
        window.removeEventListener("resize", remeasure);
        window.removeEventListener("scroll", remeasure, true);
      };
    };

    resolve();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      cancelAnimationFrame(frame);
      observer?.disconnect();
      cleanupTracking();
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
) {
  const margin = 12;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const fits = {
    top: rect.top - height - margin >= 8,
    bottom: rect.top + rect.height + margin + height <= vh - 8,
    left: rect.left - width - margin >= 8,
    right: rect.left + rect.width + margin + width <= vw - 8,
  };
  let side = placement ?? "bottom";
  if (!fits[side]) {
    const opposite = { top: "bottom", bottom: "top", left: "right", right: "left" }[
      side
    ] as NonNullable<TourStep["placement"]>;
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
  const vertical = side === "top" || side === "bottom";
  const top = vertical
    ? side === "top"
      ? rect.top - height - margin
      : rect.top + rect.height + margin
    : rect.top + rect.height / 2 - height / 2;
  const left = vertical
    ? rect.left + rect.width / 2 - width / 2
    : side === "left"
      ? rect.left - width - margin
      : rect.left + rect.width + margin;
  return {
    top: clamp(top, 8, vh - height - 8),
    left: clamp(left, 8, vw - width - 8),
  };
}

function clampOffset(
  offset: { dx: number; dy: number },
  base: { top: number; left: number },
  element: HTMLElement,
) {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  // When the card is taller/wider than the viewport allows, the naive
  // (8, viewport - size - 8) bounds invert (high < low). clamp() collapses
  // an inverted range to `low`, which would pin the card and make dragging a
  // no-op — so the low/high passed in here are sorted, giving the card room
  // to move across its whole natural range (e.g. dragging up to reveal a
  // clipped footer) instead of being stuck at the top.
  const verticalHigh = vh - element.offsetHeight - 8;
  const horizontalHigh = vw - element.offsetWidth - 8;
  const top = clamp(base.top + offset.dy, Math.min(8, verticalHigh), Math.max(8, verticalHigh));
  const left = clamp(base.left + offset.dx, Math.min(8, horizontalHigh), Math.max(8, horizontalHigh));
  return { dx: left - base.left, dy: top - base.top };
}

function AnchoredCard({
  rect,
  pending,
  placement,
  resetKey,
  children,
}: {
  rect: Rect | null;
  pending: boolean;
  placement: TourStep["placement"];
  resetKey: string;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState<{ top: number; left: number } | null>(null);
  // Manual nudge on top of the anchor-computed position, so a resize/scroll
  // remeasure (which recomputes `position` from scratch) still preserves the
  // user's drag rather than snapping back to the default placement.
  const [offset, setOffset] = useState({ dx: 0, dy: 0 });
  const dragRef = useRef<{
    pointerId: number;
    startX: number;
    startY: number;
    baseDx: number;
    baseDy: number;
  } | null>(null);

  useLayoutEffect(() => {
    const element = ref.current;
    if (!element || !rect) {
      setPosition(null);
      return;
    }
    const measure = () => {
      const next = placeBubble(rect, placement, element.offsetWidth, element.offsetHeight);
      setPosition(next);
      setOffset((current) => clampOffset(current, next, element));
    };
    measure();
    const observer =
      typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(element);
    return () => observer?.disconnect();
  }, [rect, placement]);

  // A new step (or a readiness flip that swaps the effective anchor) means a
  // different default position — any manual nudge from before belongs to the
  // previous anchor, not this one.
  useEffect(() => {
    setOffset({ dx: 0, dy: 0 });
    dragRef.current = null;
  }, [resetKey]);

  const handlePointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || dragRef.current || !position) return;
    const target = event.target;
    if (!(target instanceof HTMLElement) || !target.closest("[data-drag-handle]")) return;
    const element = ref.current;
    if (!element) return;
    dragRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      baseDx: offset.dx,
      baseDy: offset.dy,
    };
    element.setPointerCapture(event.pointerId);
  };

  const handlePointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    const element = ref.current;
    if (!drag || !element || !position || drag.pointerId !== event.pointerId) return;
    setOffset(
      clampOffset(
        {
          dx: drag.baseDx + (event.clientX - drag.startX),
          dy: drag.baseDy + (event.clientY - drag.startY),
        },
        position,
        element,
      ),
    );
  };

  const endDrag = (event: React.PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    dragRef.current = null;
    ref.current?.releasePointerCapture(event.pointerId);
  };

  const style = rect
    ? position
      ? { top: position.top + offset.dy, left: position.left + offset.dx }
      : { top: -9999, left: -9999 }
    : pending
      ? { top: -9999, left: -9999 }
      : { right: 16, bottom: 16 };
  return (
    <div
      ref={ref}
      role="dialog"
      aria-modal="false"
      data-capability="tour.controls"
      className={
        "pointer-events-auto fixed z-[65] w-[390px] max-w-[calc(100vw-16px)] rounded-xl border border-edge bg-surface p-4 shadow-2xl"
      }
      style={style}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
    >
      {children}
    </div>
  );
}

function readinessMet(
  step: TourStep,
  doc: SpecDoc | null,
  researchStatus: ResearchRunStatus,
  qcStatus: QcRunStatus,
  sourceAvailable: boolean,
) {
  if (!step.readiness) return true;
  const articles = doc?.parts.flatMap((part) => part.articles) ?? [];
  const paragraphs = articles.flatMap((article) => article.paragraphs);
  switch (step.readiness) {
    case "content":
      return !!doc && (!!doc.section.number || !!doc.section.title || articles.length > 0);
    case "rich-structure":
      return (
        !!doc &&
        doc.parts.length >= 3 &&
        doc.parts.some((part) => part.articles.length > 1) &&
        paragraphs.some((paragraph) => paragraph.children.length > 1)
      );
    case "versioned":
      return (doc?.version.count ?? 0) > 1;
    case "research":
      return researchStatus === "complete";
    case "imported":
      return sourceAvailable;
    case "qc":
      return qcStatus === "complete";
    case "template":
      return true;
  }
}

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
  if (step.actions.some((action) => action.kind === "profile-fill") && profileComplete) {
    return <p className="mt-3 text-xs text-ok">✓ Project profile is complete.</p>;
  }
  if (
    step.actions.some((action) => action.kind === "confirm-first") &&
    buildQueue(doc, "all").length === 0
  ) {
    return <p className="mt-3 text-xs text-ok">✓ The review queue is clear.</p>;
  }
  return (
    <div className="mt-3 space-y-2 border-t border-edge pt-3">
      {step.actions.map((action) => {
        const researchDone = action.kind === "run-research" && researchStatus === "complete";
        const qcDone = action.kind === "run-qc" && qcStatus === "complete";
        if (researchDone || qcDone) {
          return (
            <p key={action.kind} className="text-xs text-ok">
              ✓ {researchDone ? "Research is complete." : "Final QC is complete."}
            </p>
          );
        }
        const disabled =
          busy ||
          (action.kind === "run-research" && !profileComplete) ||
          (action.kind === "run-qc" && !hasContent);
        return (
          <div key={action.kind}>
            <button
              onClick={() => ob.runStepAction(action)}
              disabled={disabled}
              className={action.kind === "prefill-composer" ? quietBtn : primaryBtn}
            >
              {action.label}
            </button>
            {action.note && (
              <p className="mt-1 text-[11px] leading-snug text-ink-faint">{action.note}</p>
            )}
          </div>
        );
      })}
    </div>
  );
}

export default function OnboardingOverlay({
  ob,
  doc,
  busy,
  hasContent,
  profileComplete,
  researchStatus,
  qcStatus,
  sourceAvailable,
  bumpDrawer,
}: Props) {
  const { phase } = ob;
  const [keepConfirm, setKeepConfirm] = useState(false);
  const [rearrangeBaseline, setRearrangeBaseline] = useState<{
    key: string;
    signature: string;
  } | null>(null);
  const reducedMotion = usePrefersReducedMotion();
  const rawStep = phase.kind === "touring" ? TOUR[phase.chunk].steps[phase.step] : null;
  const ready = rawStep
    ? readinessMet(rawStep, doc, researchStatus, qcStatus, sourceAvailable)
    : true;
  // Missing fixture state is still explained beside the real document panel;
  // it never degrades into a disconnected synthetic card.
  const anchorStep = useMemo<TourStep | null>(
    () =>
      rawStep && !ready
        ? { ...rawStep, resolve: undefined, anchor: "doc-panel", placement: "left" }
        : rawStep,
    [rawStep, ready],
  );
  const { rect, missing } = useAnchorRect(anchorStep, doc, reducedMotion);
  const canBuildFixture =
    rawStep?.readiness === "content" ||
    rawStep?.readiness === "rich-structure" ||
    rawStep?.readiness === "versioned";
  const orderSignature = useMemo(() => documentOrderSignature(doc), [doc]);
  const rearrangeKey =
    phase.kind === "touring" && rawStep?.id === "rearrange"
      ? `${phase.chunk}:${phase.step}`
      : null;
  const rearrangeComplete =
    !!rearrangeKey &&
    rearrangeBaseline?.key === rearrangeKey &&
    rearrangeBaseline.signature !== orderSignature;

  useEffect(() => {
    if (!rearrangeKey) {
      setRearrangeBaseline(null);
      return;
    }
    setRearrangeBaseline((current) =>
      current?.key === rearrangeKey
        ? current
        : { key: rearrangeKey, signature: orderSignature },
    );
  }, [rearrangeKey, orderSignature]);

  useEffect(() => {
    if (rawStep?.drawer) bumpDrawer(rawStep.drawer);
  }, [rawStep, bumpDrawer]);

  useEffect(() => {
    if (phase.kind === "idle") return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || ob.endConfirm) return;
      if (phase.kind === "touring" || phase.kind === "chunk-break") ob.pause();
      else if (phase.kind === "source-choice") ob.requestEnd();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [phase.kind, ob]);

  useEffect(() => {
    if (phase.kind !== "completion") setKeepConfirm(false);
  }, [phase.kind]);

  if (phase.kind === "idle") return null;

  if (phase.kind === "source-choice") {
    return (
      <ModalShell title="Choose the spec for your full tutorial" onClose={ob.requestEnd} wide>
        <p className="text-sm leading-relaxed text-ink-dim">
          The tutorial always uses actual document content in a protected workspace. Your current
          project is copied, never edited in place. If it is sparse, the assistant fills the copy
          with the examples needed to demonstrate every feature.
        </p>
        <div className="mt-4 grid gap-2 sm:grid-cols-3">
          <button
            onClick={() => ob.chooseSource("current")}
            disabled={!hasContent}
            className={primaryBtn + " min-h-20 text-left disabled:opacity-40"}
            data-capability="tour.workspace"
          >
            <span className="block">Use my current spec</span>
            <span className="mt-1 block text-[11px] font-normal opacity-80">
              Work from a protected copy and restore the original later.
            </span>
          </button>
          <button
            onClick={() => ob.chooseSource("generated")}
            className={quietBtn + " min-h-20 text-left"}
            data-capability="tour.workspace"
          >
            <span className="block">Generate a tutorial spec with AI</span>
            <span className="mt-1 block text-[11px] font-normal text-ink-faint">
              Optional live generation using your API key, network tools, and real spend.
            </span>
          </button>
          <button
            onClick={() => ob.chooseSource("showcase")}
            className={quietBtn + " min-h-20 text-left"}
            data-capability="tour.workspace"
          >
            <span className="block">Use bundled showcase</span>
            <span className="mt-1 block text-[11px] font-normal text-ink-faint">
              Offline, pre-generated LLM-authored fallback with complete reusable content.
            </span>
          </button>
        </div>
        {!hasContent && (
          <p className="mt-2 text-[11px] text-ink-faint">
            There is no current spec content yet. Generate one live or use the bundled offline showcase.
          </p>
        )}
      </ModalShell>
    );
  }

  if (phase.kind === "enrichment-choice") {
    return (
      <ModalShell title="This protected copy needs more examples" onClose={ob.requestEnd} wide>
        <p className="text-sm leading-relaxed text-ink-dim">
          Your current spec is too sparse to demonstrate every feature. Existing wording, IDs,
          order, decisions, and source links will stay unchanged; additions exist only in the
          tutorial copy. Choose how to add the missing examples.
        </p>
        <div className="mt-4 grid gap-2 sm:grid-cols-2">
          <button onClick={ob.chooseLiveEnrichment} className={primaryBtn + " min-h-20 text-left"}>
            <span className="block">Enrich the copy with AI</span>
            <span className="mt-1 block text-[11px] font-normal opacity-80">
              Uses your API key, network access, and real billed model usage for this operation.
            </span>
          </button>
          <button onClick={ob.chooseBundledEnrichment} className={quietBtn + " min-h-20 text-left"}>
            <span className="block">Add bundled showcase examples</span>
            <span className="mt-1 block text-[11px] font-normal text-ink-faint">
              Offline and unbilled; merges pre-generated LLM-authored fixtures around your copy.
            </span>
          </button>
        </div>
      </ModalShell>
    );
  }

  if (phase.kind === "preparing") {
    const label =
      phase.stage === "starting"
        ? "Protecting the original and opening the tutorial copy…"
        : phase.stage === "enriching"
          ? "Building the missing real examples in the specification…"
          : phase.stage === "scenario"
            ? `Preparing the ${phase.label ?? "next"} chapter…`
            : "Restoring your workspace…";
    return (
      <ModalShell title={phase.error ? "The tutorial needs attention" : "Preparing actual content"} onClose={ob.requestEnd}>
        {phase.error ? (
          <>
            <p className="text-sm leading-relaxed text-err">{phase.error}</p>
            <div className="mt-4 flex gap-2">
              <button onClick={ob.retryPrepare} className={primaryBtn}>Retry</button>
              <button onClick={ob.requestEnd} className={quietBtn}>Choose what to restore</button>
            </div>
          </>
        ) : (
          <>
            <p className="text-sm leading-relaxed text-ink-dim">{label}</p>
            <div className="mt-4 h-1.5 overflow-hidden rounded-full bg-raised">
              <span className="block h-full w-1/2 animate-pulse rounded-full bg-accent" />
            </div>
            <p className="mt-2 text-[11px] text-ink-faint">
              Content streaming onto the paper is the same structured content the app edits and exports.
            </p>
          </>
        )}
      </ModalShell>
    );
  }

  if (phase.kind === "chunk-break") {
    const completed = TOUR[phase.nextChunk - 1];
    const next = TOUR[phase.nextChunk];
    return (
      <ModalShell
        title={`Chapter ${phase.nextChunk} of ${TOUR.length} done — ${completed.title}`}
        onClose={ob.requestEnd}
      >
        <div className="flex items-center gap-1.5" aria-hidden>
          {TOUR.map((chapter, index) => (
            <span
              key={chapter.id}
              className={`h-1.5 flex-1 rounded-full ${
                index < phase.nextChunk ? "bg-accent" : "bg-raised"
              }`}
            />
          ))}
        </div>
        <p className="mt-3 text-sm leading-relaxed text-ink-dim">
          Next: <b className="text-ink">{next.title}</b>. Pause to inspect the actual
          document or ask a question at any checkpoint.
        </p>
        <div className="mt-4 flex flex-wrap gap-2">
          <button onClick={ob.continueChunk} className={primaryBtn}>Continue</button>
          <button onClick={ob.askQuestion} className={quietBtn}>Ask a question</button>
          <button onClick={ob.pause} className={quietBtn}>Pause</button>
        </div>
      </ModalShell>
    );
  }

  if (phase.kind === "paused") {
    return (
      <div className="fixed bottom-5 left-5 z-[65] flex items-center gap-1.5">
        <button
          onClick={ob.resume}
          className="rounded-full border border-accent/60 bg-surface px-4 py-2 text-sm text-accent shadow-2xl hover:bg-accent/10"
        >
          ▶ Resume tutorial
        </button>
        <button
          onClick={ob.requestEnd}
          aria-label="End tutorial"
          className="flex h-8 w-8 items-center justify-center rounded-full border border-edge bg-surface text-ink-dim shadow-2xl"
        >
          ✕
        </button>
      </div>
    );
  }

  if (phase.kind === "completion") {
    return (
      <ModalShell title="What should happen to the tutorial workspace?" onClose={ob.requestEnd} wide>
        <div data-capability="tour.finish">
        {!keepConfirm ? (
          <p className="text-sm leading-relaxed text-ink-dim">
            Your original project is still protected. Return to it now, save the tutorial work
            first, or explicitly replace it with this practice project.
          </p>
        ) : (
          <div className="rounded-lg border border-warn/40 bg-warn/10 p-3">
            <h3 className="text-sm font-medium text-ink">Replace the original working project?</h3>
            <p className="mt-1 text-xs leading-relaxed text-ink-dim">
              Keeping the tutorial makes it the active project and releases the protected original.
              Save the original first, continue without saving it, or cancel this replacement.
            </p>
          </div>
        )}
        {phase.error && <p className="mt-3 text-sm text-err">{phase.error}</p>}
        {!keepConfirm ? (
          <div className="mt-4 grid gap-2">
            <button onClick={ob.chooseRestore} disabled={phase.saving} className={primaryBtn}>
              Return to my project
            </button>
            <button onClick={ob.saveCopyAndRestore} disabled={phase.saving} className={quietBtn}>
              Save tutorial copy, then return
            </button>
            <button onClick={() => setKeepConfirm(true)} disabled={phase.saving} className={quietBtn}>
              Replace my project with tutorial copy…
            </button>
          </div>
        ) : (
          <div className="mt-4 grid gap-2 sm:grid-cols-3">
            <button onClick={ob.saveOriginalAndKeep} disabled={phase.saving} className={primaryBtn}>
              Save original, then keep
            </button>
            <button onClick={ob.chooseKeep} disabled={phase.saving} className={quietBtn}>
              Keep without saving original
            </button>
            <button onClick={() => setKeepConfirm(false)} disabled={phase.saving} className={quietBtn}>
              Cancel
            </button>
          </div>
        )}
        {phase.saving && <p className="mt-3 text-xs text-ink-faint">Finishing safely…</p>}
        </div>
      </ModalShell>
    );
  }

  const chapter = TOUR[phase.chunk];
  const step = chapter.steps[phase.step];
  const atFirst = phase.chunk === 0 && phase.step === 0;
  return (
    <div className="pointer-events-none fixed inset-0 z-[60]">
      {rect && <Cutout rect={rect} reducedMotion={reducedMotion} />}
      <AnchoredCard
        rect={rect}
        pending={!missing}
        placement={anchorStep?.placement}
        resetKey={`${phase.chunk}:${phase.step}:${ready}`}
      >
        <CardHeader
          kicker={`Chapter ${phase.chunk + 1}/${TOUR.length} — ${chapter.title} · ${
            phase.step + 1
          }/${chapter.steps.length}`}
          title={step.title}
          onClose={ob.requestEnd}
        />
        <div className="mt-1 flex items-center gap-2">
          <span className="rounded-full border border-edge px-2 py-0.5 text-[10px] uppercase tracking-wide text-ink-faint">
            {step.mode}
          </span>
          {step.optionalReason && <span className="text-[10px] text-ink-faint">Optional: {step.optionalReason}</span>}
        </div>
        {ob.notice && (
          <div className="mt-2 rounded-lg border border-warn/40 bg-warn/10 p-2.5">
            <div className="flex items-start gap-2">
              <p className="flex-1 text-xs leading-relaxed text-ink-dim">{ob.notice}</p>
              <button
                onClick={ob.dismissNotice}
                aria-label="Dismiss tutorial notice"
                className="rounded px-1 text-ink-faint hover:bg-raised hover:text-ink"
              >
                ✕
              </button>
            </div>
          </div>
        )}
        {ready && !missing ? (
          <p className="mt-2 text-sm leading-relaxed text-ink-dim">{step.body}</p>
        ) : missing && ready ? (
          <div className="mt-2 rounded-lg border border-edge bg-raised p-3">
            <p className="text-sm leading-relaxed text-ink-dim">
              This control is not available in the current UI state. The tutorial has kept the
              explanation beside the real document instead of showing a pretend control. You can
              continue; no project change is required.
            </p>
          </div>
        ) : (
          <div className="mt-2 rounded-lg border border-warn/40 bg-warn/10 p-3">
            <p className="text-sm leading-relaxed text-ink-dim">{step.body}</p>
            <p className="text-sm leading-relaxed text-ink-dim">
              {step.readiness === "research"
                ? "There is no completed research result in this tutorial state. A live run is optional because it uses your API key and web tools. If you skipped it, the details above explain the workflow without claiming that research ran."
                : step.readiness === "qc"
                  ? "There is no completed Final QC result in this tutorial state. Final QC is optional and paid. If you declined the live run, the details above remain instructional; no findings or readiness state will be fabricated."
                  : step.readiness === "imported"
                    ? "The real imported-source scenario could not be prepared. Continue without it; the tutorial will not synthesize imported provenance or source-preservation permissions."
                    : "This protected workspace does not yet contain the real example required for this step."}
            </p>
            {canBuildFixture && (
              <button onClick={ob.ensureContent} className={primaryBtn + " mt-2"}>
                Build the missing example
              </button>
            )}
          </div>
        )}
        {ready && !missing && step.details && (
          <dl className="mt-3 space-y-1.5 border-t border-edge pt-2.5">
            {step.details.map((item) => (
              <div key={item.id} className="text-[11px] leading-snug">
                <dt className="inline font-medium text-ink">{item.label}: </dt>
                <dd className="inline text-ink-dim">{item.description}</dd>
              </div>
            ))}
          </dl>
        )}
        {ready && !missing && step.id === "rearrange" && (
          <div
            className={`mt-3 rounded-lg border p-3 ${
              rearrangeComplete
                ? "border-ok/40 bg-ok/10"
                : "border-accent/40 bg-accent/10"
            }`}
          >
            <p className="text-xs font-medium text-ink">
              {rearrangeComplete
                ? "✓ Sibling order changed — numbering recomputed on the paper."
                : "Move one article or paragraph now."}
            </p>
            <p className="mt-1 text-[11px] leading-snug text-ink-dim">
              Drag it, use Space plus Up/Down, or click an arrow. Its stable ID and complete
              subtree move together; Continue unlocks only after the order changes.
            </p>
          </div>
        )}
        {ready && !missing && (
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
        )}
        <div className="mt-4 flex flex-wrap items-center gap-2">
          {!atFirst && <button onClick={ob.back} className={quietBtn}>‹ Back</button>}
          <button
            onClick={ob.advance}
            disabled={busy || (step.id === "rearrange" && !rearrangeComplete)}
            className={primaryBtn}
            title={
              step.id === "rearrange" && !rearrangeComplete
                ? "Move one sibling article or paragraph to continue."
                : undefined
            }
          >
            {step.continueLabel ?? (step.mode === "optional" ? "Continue / skip" : "Continue")}
          </button>
          <button onClick={ob.pause} className={quietBtn}>Pause</button>
          <span className="flex-1" />
          <button onClick={ob.requestEnd} className={quietBtn}>End</button>
        </div>
      </AnchoredCard>
    </div>
  );
}

function CardHeader({ kicker, title, onClose }: { kicker: string; title: string; onClose: () => void }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div
        data-drag-handle
        title="Drag to move"
        className="min-w-0 flex-1 cursor-grab select-none touch-none active:cursor-grabbing"
      >
        <p className="text-[11px] uppercase tracking-wide text-ink-faint">{kicker}</p>
        <h3 className="mt-0.5 font-[family-name:var(--font-display)] text-base font-semibold text-ink">{title}</h3>
      </div>
      <button onClick={onClose} aria-label="End tutorial" className="rounded-md px-1.5 py-0.5 text-ink-dim hover:bg-raised hover:text-ink">✕</button>
    </div>
  );
}

function Cutout({ rect, reducedMotion }: { rect: Rect; reducedMotion: boolean }) {
  return (
    <div
      aria-hidden
      className="pointer-events-none fixed z-[61] rounded-[10px]"
      style={{
        top: rect.top - CUTOUT_PAD,
        left: rect.left - CUTOUT_PAD,
        width: rect.width + CUTOUT_PAD * 2,
        height: rect.height + CUTOUT_PAD * 2,
        boxShadow: "0 0 0 9999px rgba(0,0,0,.55), 0 0 0 2px var(--color-accent)",
        transition: reducedMotion
          ? "none"
          : "top .2s ease,left .2s ease,width .2s ease,height .2s ease",
      }}
    />
  );
}
