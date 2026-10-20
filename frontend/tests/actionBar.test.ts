/**
 * The document panel's action bar fits the pane.
 *
 * It holds more controls than one row of the narrow pane can take, so the
 * bar wraps BETWEEN groups that each stay on one line, and no label may
 * break. Measured in Chromium at the 1100 and 1440 window sizes (the PR
 * that added this pins the numbers); this suite pins the mechanism at the
 * source level, since there is no DOM harness.
 *
 * Since 2026-10-20 the 🔒 edit-scope badge shortens, and as a last step
 * steps aside, rather than wrap a bar that would otherwise hold one row
 * (lib/actionBarFit.ts): its rule is tested here on the widths measured in
 * Chromium, and its wiring is pinned at the source level.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  FIT_SLACK_PX,
  SCOPE_BADGE_LEVELS,
  SHOWN_SCOPE_BADGE_LEVELS,
  flexLineCount,
  pickScopeBadgeLevel,
  type ActionBarMeasure,
  type ScopeBadgeLevel,
} from "../src/lib/actionBarFit.ts";
import { editScopeOption } from "../src/lib/editScope.ts";

const artifact = readFileSync(
  new URL("../src/components/ArtifactPanel.tsx", import.meta.url),
  "utf8",
);
const hook = readFileSync(
  new URL("../src/lib/useScopeBadgeLevel.ts", import.meta.url),
  "utf8",
);

const start = artifact.indexOf('data-testid="doc-action-bar"');
const end = artifact.indexOf("{fileLoading && (", start);
// The opening tag of the bar starts just before its test id.
const bar = artifact.slice(artifact.lastIndexOf("<div", start), end);

test("the bar wraps between groups that each stay on one line", () => {
  assert.ok(start > 0 && end > start, "the action bar is found");
  assert.match(
    bar,
    /^<div\s+ref=\{scopeBadge\.barRef\}\s+className="relative flex flex-wrap items-center justify-end/,
  );
  // The draft action keeps the left edge; the other three groups follow it.
  assert.match(bar, /className="mr-auto flex shrink-0 items-center/);
  assert.equal(
    bar.match(/<div className="flex shrink-0 items-center gap-1\.5">/g)?.length,
    3,
    "history, outputs and files are one unshrinkable group each",
  );
  // A shrinkable group is what let the draft button slide under the stepper.
  assert.doesNotMatch(bar, /min-w-0/);
});

test("no label in the bar can break onto a second line", () => {
  assert.match(
    artifact,
    /const actionButton =\s*"whitespace-nowrap /,
    "every bar button shares the nowrap action class",
  );
  const drafts = bar.match(/className=\{?[`"]whitespace-nowrap rounded-md bg-accent/g);
  assert.equal(drafts?.length, 2, "both draft buttons are nowrap");
  assert.match(bar, /className="whitespace-nowrap rounded-full/, "the lint badge");
});

test("every tour anchor and capability stays in the bar", () => {
  for (const anchor of [
    "draft-full",
    "version-stepper",
    "compare",
    "export",
    "save",
    "import-master",
    "attach-reference",
  ]) {
    assert.ok(bar.includes(`data-tour="${anchor}"`), anchor);
  }
  for (const capability of [
    "chat.full-draft",
    "chat.adapt-imported",
    "history.undo-redo",
    "history.compare",
    "export.clean",
    "project.next-section",
    "project.save-open",
    "import.master",
    "reference.attach",
  ]) {
    assert.ok(bar.includes(`data-capability="${capability}"`), capability);
  }
});

// Widths measured in Chromium against the built app with an imported master
// under Essentials (one-digit lint count, no Save caret): the four groups at
// the full badge, and what the badge adds to the draft group at each level,
// its 10px gap included.
const MEASURED_GROUPS = [323.06, 156.58, 163.31, 296.84];
const MEASURED_COST: Record<ScopeBadgeLevel, number> = {
  full: 136.13,
  compact: 92.08,
  icon: 40.48,
  hidden: 0,
};
const atPane = (pane: number, shown: ScopeBadgeLevel = "full"): ActionBarMeasure => ({
  // The groups as rendered with `shown`, so the pick is tested from every
  // starting level, not only from the full one.
  groups: MEASURED_GROUPS.map((width, index) =>
    index === 0 ? width - MEASURED_COST.full + MEASURED_COST[shown] : width,
  ),
  badgeGroup: 0,
  badgeCostNow: MEASURED_COST[shown],
  badgeCost: MEASURED_COST,
  gap: 12,
  available: pane - 40,
});

test("the line count breaks like a wrapping flex row", () => {
  assert.equal(flexLineCount([], 12, 500), 0);
  assert.equal(flexLineCount([100, 100, 100], 12, 400), 1);
  // 100 + 12 + 100 + 12 + 100 = 324: one line only with the slack to spare.
  assert.equal(flexLineCount([100, 100, 100], 12, 324 + FIT_SLACK_PX), 1);
  assert.equal(flexLineCount([100, 100, 100], 12, 324), 2);
  assert.equal(flexLineCount([300, 300, 300], 12, 650), 2);
  // An item wider than the row still takes a line of its own, and the next
  // item starts another.
  assert.equal(flexLineCount([900, 50], 12, 500), 2);
  assert.equal(flexLineCount([50, 900, 50], 12, 500), 3);
});

test("the badge says as much as one row has room for, in order", () => {
  assert.deepEqual([...SCOPE_BADGE_LEVELS], ["full", "compact", "icon", "hidden"]);
  assert.deepEqual([...SHOWN_SCOPE_BADGE_LEVELS], ["full", "compact", "icon"]);
  // Panes of the 1920, 1860, 1760 and 1700 windows (54% of the width).
  const cases: [number, ScopeBadgeLevel][] = [
    [1037, "full"],
    [1004, "compact"],
    [950, "icon"],
    [918, "hidden"],
  ];
  for (const [pane, expected] of cases) {
    for (const shown of SCOPE_BADGE_LEVELS) {
      assert.equal(pickScopeBadgeLevel(atPane(pane, shown)), expected, `${pane} from ${shown}`);
    }
  }
});

test("where the bar wraps anyway, the badge keeps every word", () => {
  // The 1440 and 1100 windows: two lines at every level, so nothing is
  // gained by saying less.
  for (const pane of [778, 594]) {
    for (const shown of SCOPE_BADGE_LEVELS) {
      assert.equal(pickScopeBadgeLevel(atPane(pane, shown)), "full", `${pane} from ${shown}`);
    }
  }
});

test("a bar that was never measured keeps the full wording", () => {
  assert.equal(pickScopeBadgeLevel({ ...atPane(918), available: 0 }), "full");
  assert.equal(pickScopeBadgeLevel({ ...atPane(918), badgeGroup: -1 }), "full");
  assert.equal(
    pickScopeBadgeLevel({ ...atPane(918), badgeCost: { ...MEASURED_COST, icon: 0 } }),
    "full",
  );
});

test("the badge's wording comes from the scope option, and the hover keeps every word", () => {
  assert.equal(editScopeOption("essentials").short, "Essential edits only");
  assert.equal(editScopeOption("essentials").compact, "Essentials");
  assert.match(artifact, /\? `🔒 \$\{scopeOption\.short\}`/);
  assert.match(artifact, /\? `🔒 \$\{scopeOption\.compact\}`/);
  assert.match(
    bar,
    /title=\{`\$\{scopeOption\.short\}\. \$\{scopeOption\.detail\} Change it beside the chat's Send button\.`\}/,
  );
  // Short of full, the screen-reader text still says it all.
  assert.match(bar, /<span className="sr-only">\{scopeOption\.short\}<\/span>/);
});

test("the badge is measured off-screen and stays mounted while hidden", () => {
  assert.match(bar, /ref=\{scopeBadge\.badgeRef\}/);
  assert.match(bar, /scopeBadge\.level === "hidden" \? " hidden" : ""/);
  // One copy per shown level, invisible and out of the flex layout, and out
  // of the accessibility tree.
  assert.match(
    bar,
    /ref=\{scopeBadge\.measureRef\}\s+aria-hidden="true"\s+className="pointer-events-none invisible absolute left-0 top-0 flex w-max"/,
  );
  assert.match(bar, /SHOWN_SCOPE_BADGE_LEVELS\.map\(\(level\) => \(/);
  assert.match(bar, /data-level=\{level\} className=\{scopeBadgeClass\}/);
  assert.match(artifact, /useScopeBadgeLevel\(editScope === "essentials"\)/);
});

test("a resize re-measures on the next frame, never inside the observer", () => {
  // Setting the level inside the callback would raise the browser's
  // "ResizeObserver loop" error, which clientLog reports as an app error.
  assert.match(hook, /new ResizeObserver\(\(\) => \{\s+cancelAnimationFrame\(frame\);\s+frame = requestAnimationFrame\(measure\);\s+\}\)/);
  assert.match(hook, /useLayoutEffect\(/);
  assert.match(hook, /observer\.observe\(bar\);\s+for \(const child of Array\.from\(bar\.children\)\) observer\.observe\(child\);/);
  assert.match(hook, /setLevel\(\(prev\) => \(prev === next \? prev : next\)\)/);
});
