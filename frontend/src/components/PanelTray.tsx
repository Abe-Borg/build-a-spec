/**
 * The panel tray: every collapsible panel under the paper, behind one bar.
 *
 * Hide folds the whole tray away and gives its height to the specification;
 * its bar stays, still counting what needs attention. Choose… leaves
 * out the panels the user never opens. Open, the tray never takes more than
 * half the panel's height — an opened panel scrolls inside it rather than
 * squeezing the paper. The rules and the vocabulary are in lib/panelTray.ts;
 * App keeps the layout (above the remount a new session does) and the
 * server remembers it between launches.
 *
 * Hidden panels stay MOUNTED, only out of view: each owns real state — the
 * review walk's cursor and draft text, a half-typed standard, the Final QC
 * accept-set — and a fold is a request for room, not a reason to lose it.
 */
import { useId, useState, type KeyboardEvent, type ReactNode } from "react";
import {
  PANEL_IDS,
  PANEL_LABELS,
  foldedAttention,
  type EffectivePanelTray,
  type PanelAttention,
  type PanelId,
} from "../lib/panelTray";
import Tip from "./Tip";

const TOUR_LOCK_TIP =
  "The guided tour keeps every panel open so each step can point at one. Your own layout comes back when the tour ends.";

interface Props {
  tray: EffectivePanelTray;
  attention: PanelAttention;
  /** One entry per panel, keyed by id — a Record, so the type itself refuses
   *  a tray that leaves a panel out. Rendered in PANEL_IDS order. */
  panels: Record<PanelId, ReactNode>;
  onFold: (folded: boolean) => void;
  onSetHidden: (id: PanelId, hidden: boolean) => void;
  onShowAll: () => void;
}

export default function PanelTray({
  tray,
  attention,
  panels,
  onFold,
  onSetHidden,
  onShowAll,
}: Props) {
  const listId = useId();
  const [menuOpen, setMenuOpen] = useState(false);
  const hiddenNames = PANEL_IDS.filter((id) => tray.hidden.has(id)).map(
    (id) => PANEL_LABELS[id],
  );
  const summary = tray.locked
    ? "all open during the guided tour"
    : tray.folded
      ? ["folded", ...foldedAttention(attention, tray.hidden)].join(" · ")
      : hiddenNames.length > 0
        ? `${hiddenNames.join(", ")} hidden`
        : "";
  const foldTip = tray.locked
    ? TOUR_LOCK_TIP
    : tray.folded
      ? "Show the panels again."
      : "Fold the panels away and give their height to the specification. Their counts stay on this bar, and the layout is remembered.";
  const closeOnEscape = (event: KeyboardEvent) => {
    if (event.key !== "Escape") return;
    // The house "already handled" signal (lib/dialogFocus.ts): nothing else
    // listening for this Escape acts on it too.
    event.preventDefault();
    setMenuOpen(false);
  };

  return (
    <section
      className={tray.ready ? "flex max-h-[50%] shrink-0 flex-col" : "hidden"}
      aria-label="Panels"
    >
      <div
        className="flex shrink-0 items-center gap-2 border-t border-edge bg-bg px-5 py-1"
        data-tour="panel-tray"
      >
        <Tip tip={foldTip} className="min-w-0 flex-1">
          <button
            type="button"
            className="flex min-w-0 flex-1 items-baseline gap-2 text-left text-[11px] text-ink-faint transition-colors hover:text-ink-dim disabled:pointer-events-none"
            onClick={() => onFold(!tray.folded)}
            disabled={tray.locked}
            aria-expanded={!tray.folded}
            aria-controls={listId}
            data-capability="document.panels"
          >
            <span className="shrink-0 font-medium tracking-wide uppercase">
              Panels
            </span>
            <span className="truncate">{summary}</span>
            <span className="ml-auto shrink-0">
              {tray.folded ? "Show ▴" : "Hide ▾"}
            </span>
          </button>
        </Tip>
        <div className="relative flex shrink-0" onKeyDown={closeOnEscape}>
          <Tip
            tip={
              tray.locked
                ? TOUR_LOCK_TIP
                : "Choose which panels appear here — leave out the ones you do not use."
            }
          >
            <button
              type="button"
              className="rounded-md border border-edge bg-raised px-2 py-px text-[11px] text-ink-dim transition-colors hover:border-accent hover:text-accent disabled:pointer-events-none disabled:opacity-40"
              onClick={() => setMenuOpen((open) => !open)}
              disabled={tray.locked}
              aria-haspopup="true"
              aria-expanded={menuOpen}
              data-capability="document.panels"
            >
              Choose…
            </button>
          </Tip>
          {menuOpen && !tray.locked && (
            <div
              role="group"
              aria-label="Panels shown in the tray"
              className="absolute bottom-full right-0 z-20 mb-1 w-64 rounded-md border border-edge bg-raised py-1 text-[11px] shadow-lg"
              onMouseLeave={() => setMenuOpen(false)}
            >
              <p className="px-3 pb-1 pt-0.5 text-[10px] leading-relaxed text-ink-faint">
                Show these panels. Each one appears only when it has something
                to show.
              </p>
              {PANEL_IDS.map((id) => (
                <label
                  key={id}
                  className="flex cursor-pointer items-center gap-2 px-3 py-1 text-ink-dim hover:bg-surface hover:text-ink"
                >
                  <input
                    type="checkbox"
                    className="h-3.5 w-3.5 shrink-0 accent-[var(--color-accent)]"
                    checked={!tray.hidden.has(id)}
                    onChange={(event) => onSetHidden(id, !event.target.checked)}
                  />
                  {PANEL_LABELS[id]}
                </label>
              ))}
              <div className="mt-1 border-t border-edge/60 px-3 pt-1.5">
                <button
                  type="button"
                  className="text-[11px] text-ink-dim hover:text-accent disabled:pointer-events-none disabled:opacity-40"
                  onClick={onShowAll}
                  disabled={tray.hidden.size === 0}
                >
                  Show all panels
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
      <div
        id={listId}
        className={tray.folded ? "hidden" : "min-h-0 overflow-y-auto"}
      >
        {PANEL_IDS.map((id) => (
          <div
            key={id}
            data-panel={id}
            className={tray.hidden.has(id) ? "hidden" : undefined}
          >
            {panels[id]}
          </div>
        ))}
      </div>
    </section>
  );
}
