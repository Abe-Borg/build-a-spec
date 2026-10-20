import { useLayoutEffect, useRef, useState } from "react";

import {
  SHOWN_SCOPE_BADGE_LEVELS,
  pickScopeBadgeLevel,
  type ScopeBadgeLevel,
} from "./actionBarFit";

/**
 * Measures the document panel's action bar and picks the 🔒 edit-scope
 * badge's wording (actionBarFit.ts has the rule). `barRef` goes on the bar,
 * `badgeRef` on the visible badge (kept mounted while `hidden`, so its group
 * can still be found), and `measureRef` on an invisible, absolutely
 * positioned span inside the bar holding one copy of the badge per shown
 * level, each marked `data-level`: absolute children take no part in the
 * bar's flex layout, so every level is measured without being shown.
 *
 * Measures once before paint, then again whenever the bar or one of its
 * groups changes size — a window resize, a lint count, "Saving…", Exit
 * compare. The re-measure waits for the next animation frame: setting the
 * level resizes the badge's group, and doing that inside the observer's
 * callback would raise the browser's "ResizeObserver loop" error, which
 * clientLog reports as an app error. The pick never depends on the level
 * shown, so a re-measure after a change lands on the same level.
 */
export function useScopeBadgeLevel(active: boolean) {
  const barRef = useRef<HTMLDivElement>(null);
  const badgeRef = useRef<HTMLSpanElement>(null);
  const measureRef = useRef<HTMLSpanElement>(null);
  const [level, setLevel] = useState<ScopeBadgeLevel>("full");

  useLayoutEffect(() => {
    const bar = barRef.current;
    const twins = measureRef.current;
    if (!active || !bar || !twins) {
      setLevel("full");
      return;
    }
    const measure = () => {
      const badge = badgeRef.current;
      if (!badge) return;
      const style = getComputedStyle(bar);
      const groups = Array.from(bar.children).filter((child) => child !== twins);
      const badgeGroup = groups.findIndex((group) => group.contains(badge));
      // The gap the badge brings into its group beside its neighbours.
      const innerGap =
        badgeGroup >= 0
          ? parseFloat(getComputedStyle(groups[badgeGroup]).columnGap) || 0
          : 0;
      const badgeCost: Record<ScopeBadgeLevel, number> = {
        full: 0,
        compact: 0,
        icon: 0,
        hidden: 0,
      };
      for (const each of SHOWN_SCOPE_BADGE_LEVELS) {
        const twin = twins.querySelector(`[data-level="${each}"]`);
        const width = twin ? twin.getBoundingClientRect().width : 0;
        badgeCost[each] = width > 0 ? width + innerGap : 0;
      }
      const shownWidth = badge.getBoundingClientRect().width;
      const next = pickScopeBadgeLevel({
        groups: groups.map((group) => group.getBoundingClientRect().width),
        badgeGroup,
        badgeCostNow: shownWidth > 0 ? shownWidth + innerGap : 0,
        badgeCost,
        gap: parseFloat(style.columnGap) || 0,
        available:
          bar.clientWidth -
          (parseFloat(style.paddingLeft) || 0) -
          (parseFloat(style.paddingRight) || 0),
      });
      setLevel((prev) => (prev === next ? prev : next));
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    let frame = 0;
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(measure);
    });
    observer.observe(bar);
    for (const child of Array.from(bar.children)) observer.observe(child);
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, [active]);

  return { barRef, badgeRef, measureRef, level };
}
