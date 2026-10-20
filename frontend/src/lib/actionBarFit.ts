/**
 * The document panel's action bar stays on one row when it can
 * (2026-10-20).
 *
 * The bar is four groups that never shrink and wrap BETWEEN one another
 * (actionBar.test.ts). v1.28.0 added the 🔒 Essential edits only badge to
 * the draft group, about 135px with its gap, which pushed the bar's one-row
 * width from ~880px to ~1,015px: windows that had held the bar on one row
 * wrapped the file buttons onto a second line in the corner, with the room
 * they needed left empty beside the draft action. The badge is the one
 * piece that can say less, so it takes the most descriptive wording that
 * costs the bar no extra line: `full` ("🔒 Essential edits only"), then
 * `compact` ("🔒 Essentials"), then `icon` (the lock alone; its hover and
 * screen-reader text keep the words), then `hidden` — the composer's Edits
 * switch, always on screen beside the panel, still shows the mode, and a
 * bar that wraps for a transient label ("Saved ✓") would jump the whole
 * document down and back. Where nothing the badge can do holds one row,
 * the bar wraps anyway and the badge says everything again.
 *
 * Pure functions over measured widths, so the rule is testable without a
 * DOM; `useScopeBadgeLevel` (useScopeBadgeLevel.ts) does the measuring.
 */

export const SCOPE_BADGE_LEVELS = ["full", "compact", "icon", "hidden"] as const;
export type ScopeBadgeLevel = (typeof SCOPE_BADGE_LEVELS)[number];

/** The levels that show the badge, each measured off-screen. */
export const SHOWN_SCOPE_BADGE_LEVELS = ["full", "compact", "icon"] as const;

/** A line counts as fitting only with this much room to spare, so a width
 *  that rounds differently in the browser never wraps the level picked for
 *  one row. */
export const FIT_SLACK_PX = 1;

/**
 * Lines a wrapping flex row takes, the browser's way for items that never
 * shrink: each item joins the current line when it fits beside what is
 * there (with the gap), otherwise it starts the next line — and an item
 * wider than the whole row still takes a line of its own.
 */
export function flexLineCount(
  widths: readonly number[],
  gap: number,
  available: number,
): number {
  let lines = 0;
  let used = 0;
  for (const width of widths) {
    if (lines > 0 && used + gap + width <= available - FIT_SLACK_PX) {
      used += gap + width;
    } else {
      lines += 1;
      used = width;
    }
  }
  return lines;
}

export interface ActionBarMeasure {
  /** Each group's width as rendered now, in bar order. */
  groups: readonly number[];
  /** Index of the group that holds the badge. */
  badgeGroup: number;
  /** What the badge adds to its group's width now: its own width plus the
   *  group's gap beside it, or 0 while hidden. */
  badgeCostNow: number;
  /** What it would add at each level, measured off-screen the same way;
   *  `hidden` adds nothing. */
  badgeCost: Readonly<Record<ScopeBadgeLevel, number>>;
  /** The bar's column gap. */
  gap: number;
  /** The bar's content width (client width less its padding). */
  available: number;
}

/**
 * The most descriptive badge level whose bar takes no more lines than the
 * most compact one would. Anything unmeasured (a hidden panel, a zero-width
 * bar) keeps the full wording.
 */
export function pickScopeBadgeLevel(measure: ActionBarMeasure): ScopeBadgeLevel {
  const { groups, badgeGroup, badgeCostNow, badgeCost, gap, available } = measure;
  const measured =
    available > 0 &&
    badgeGroup >= 0 &&
    badgeGroup < groups.length &&
    SHOWN_SCOPE_BADGE_LEVELS.every((level) => badgeCost[level] > 0);
  if (!measured) return "full";
  const linesAt = SCOPE_BADGE_LEVELS.map((level) =>
    flexLineCount(
      groups.map((width, index) =>
        index === badgeGroup ? width - badgeCostNow + badgeCost[level] : width,
      ),
      gap,
      available,
    ),
  );
  const fewest = Math.min(...linesAt);
  return SCOPE_BADGE_LEVELS[linesAt.indexOf(fewest)];
}
