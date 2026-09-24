/**
 * The panel tray: fold every panel under the paper away, leave some out,
 * and never let the open tray take more than half the panel.
 *
 * The pure rules run directly; the wiring is pinned at the source level, the
 * way the rest of this suite pins components (there is no DOM harness).
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { getUiPreferences, saveUiPreferences } from "../src/lib/api.ts";
import {
  DEFAULT_PANEL_TRAY,
  PANEL_IDS,
  PANEL_LABELS,
  effectivePanelTray,
  foldedAttention,
  panelTrayFromApi,
  panelTrayToApi,
  setPanelHidden,
  setTrayFolded,
  showEveryPanel,
} from "../src/lib/panelTray.ts";

const read = (path: string) =>
  readFileSync(new URL(`../src/${path}`, import.meta.url), "utf8");
const tray = read("components/PanelTray.tsx");
const artifact = read("components/ArtifactPanel.tsx");
const app = read("App.tsx");

test("the saved layout is read strictly: unknown ids dropped, only a real boolean folds", () => {
  assert.deepEqual(panelTrayFromApi(null), DEFAULT_PANEL_TRAY);
  assert.deepEqual(panelTrayFromApi(undefined), DEFAULT_PANEL_TRAY);
  assert.deepEqual(
    panelTrayFromApi({
      panels_folded: true,
      // Out of order, repeated, and one a newer build might have added.
      hidden_panels: ["documents", "review", "documents", "something-new", 7],
    }),
    { folded: true, hidden: ["review", "documents"] },
  );
  assert.equal(panelTrayFromApi({ panels_folded: "true" }).folded, false);
  assert.deepEqual(panelTrayFromApi({ hidden_panels: "review" }).hidden, []);
});

test("the layout round-trips to the server's shape in stacking order", () => {
  const prefs = { folded: true, hidden: ["standards", "qc"] as const };
  assert.deepEqual(panelTrayToApi(prefs), {
    panels_folded: true,
    hidden_panels: ["qc", "standards"],
  });
  assert.deepEqual(panelTrayFromApi(panelTrayToApi(prefs)), {
    folded: true,
    hidden: ["qc", "standards"],
  });
});

test("folding keeps the per-panel choice, and per-panel changes keep the fold", () => {
  let prefs = setPanelHidden(DEFAULT_PANEL_TRAY, "documents", true);
  prefs = setPanelHidden(prefs, "review", true);
  prefs = setPanelHidden(prefs, "review", true);
  assert.deepEqual(prefs.hidden, ["review", "documents"]);
  // Hide-all is a fold, not a list of every panel: unfolding must bring back
  // exactly the tray the user had chosen, not reset it.
  const folded = setTrayFolded(prefs, true);
  assert.deepEqual(folded, { folded: true, hidden: ["review", "documents"] });
  assert.deepEqual(setTrayFolded(folded, false).hidden, ["review", "documents"]);
  assert.deepEqual(setPanelHidden(folded, "review", false), {
    folded: true,
    hidden: ["documents"],
  });
  assert.deepEqual(showEveryPanel(folded), { folded: true, hidden: [] });
});

test("the tour owns the layout while it runs, and nothing shows before it is read", () => {
  const chosen = { folded: true, hidden: ["qc", "issues"] as const };
  const locked = effectivePanelTray(chosen, true);
  assert.deepEqual(
    { ...locked, hidden: [...locked.hidden] },
    { ready: true, folded: false, hidden: [], locked: true },
  );
  // A tour that starts before the layout loads still shows every panel.
  assert.equal(effectivePanelTray(null, true).ready, true);
  const unread = effectivePanelTray(null, false);
  assert.equal(unread.ready, false);
  const mine = effectivePanelTray(chosen, false);
  assert.equal(mine.folded, true);
  assert.deepEqual([...mine.hidden].sort(), ["issues", "qc"]);
  assert.equal(mine.locked, false);
});

test("a folded bar still counts what needs attention, minus panels left out", () => {
  const counts = { review: 13, openItems: 1, waiting: 2, issues: 7 };
  assert.deepEqual(foldedAttention(counts, new Set()), [
    "13 to review",
    "7 issues",
    "1 open item",
    "2 waiting on you",
  ]);
  assert.deepEqual(
    foldedAttention({ review: 0, openItems: 4, waiting: 0, issues: 1 }, new Set()),
    ["1 issue", "4 open items"],
  );
  assert.deepEqual(foldedAttention(counts, new Set(["review", "followups"])), [
    "7 issues",
    "1 open item",
  ]);
});

test("every panel has the name its own bar shows", () => {
  assert.deepEqual(Object.keys(PANEL_LABELS).sort(), [...PANEL_IDS].sort());
  assert.equal(PANEL_LABELS.qc, "Final QC");
  assert.equal(PANEL_LABELS.followups, "Waiting on you");
});

test("the API client reads and writes the layout on its own route", async (t) => {
  const originalFetch = globalThis.fetch;
  t.after(() => {
    globalThis.fetch = originalFetch;
  });
  const calls: { input: RequestInfo | URL; init?: RequestInit }[] = [];
  let status = 200;
  globalThis.fetch = async (input, init) => {
    calls.push({ input, init });
    return new Response(
      JSON.stringify({ ok: true, panels_folded: true, hidden_panels: ["qc"] }),
      { status, headers: { "Content-Type": "application/json" } },
    );
  };
  assert.deepEqual(await getUiPreferences(), {
    ok: true,
    panels_folded: true,
    hidden_panels: ["qc"],
  });
  assert.equal(calls[0].input, "/api/ui/preferences");
  await saveUiPreferences({ panels_folded: false, hidden_panels: ["review"] });
  assert.equal(calls[1].input, "/api/ui/preferences");
  assert.equal(calls[1].init?.method, "PUT");
  assert.deepEqual(JSON.parse(String(calls[1].init?.body)), {
    panels_folded: false,
    hidden_panels: ["review"],
  });
  status = 500;
  await assert.rejects(getUiPreferences());
  await assert.rejects(
    saveUiPreferences({ panels_folded: true, hidden_panels: [] }),
  );
});

test("the tray caps its height, scrolls inside, and hides without unmounting", () => {
  // Half the panel at most, with its own scroller under a fixed bar.
  assert.match(tray, /"flex max-h-\[50%\] shrink-0 flex-col"/);
  assert.match(tray, /tray\.folded \? "hidden" : "min-h-0 overflow-y-auto"/);
  // Every panel id gets a slot, and a hidden panel is display:none — never
  // unmounted, so the review walk, a half-typed standard and the QC
  // accept-set survive a fold.
  assert.match(tray, /PANEL_IDS\.map\(\(id\) => \(/);
  assert.match(tray, /className=\{tray\.hidden\.has\(id\) \? "hidden" : undefined\}/);
  assert.match(tray, /\{panels\[id\]\}/);
  assert.doesNotMatch(tray, /tray\.hidden\.has\(id\) &&|!tray\.hidden\.has\(id\) &&/);
  // The whole tray waits for the saved layout rather than flashing open.
  assert.match(tray, /tray\.ready \?/);
  // Both controls declare the capability and stand down during the tour.
  assert.equal([...tray.matchAll(/data-capability="document\.panels"/g)].length, 2);
  assert.equal([...tray.matchAll(/disabled=\{tray\.locked\}/g)].length, 2);
  assert.match(tray, /data-tour="panel-tray"/);
});

test("every panel under the paper lives in the tray, and the modals do not", () => {
  const start = artifact.indexOf("<PanelTray");
  assert.ok(start > 0, "ArtifactPanel must render the tray");
  const end = artifact.indexOf("\n      />", start);
  const inside = artifact.slice(start, end);
  const outside = artifact.slice(0, start) + artifact.slice(end);
  for (const tag of [
    "<ReviewDrawer",
    "<ResearchDrawer",
    "<QCDrawer",
    "<IssuesDrawer",
    'data-tour="open-items"',
    "<FollowUpsPanel",
    "<ProjectPanel",
    "<ProjectFactsPanel",
    "<StandardsStrip",
    "<ReferenceDocumentsStrip",
  ]) {
    assert.ok(inside.includes(tag), `${tag} must sit inside the tray`);
    assert.ok(!outside.includes(tag), `${tag} must not also render outside it`);
  }
  // Modals opened from the action bar and the Export menu must never be
  // hidden by a fold, so they render beside the tray, not in it.
  for (const tag of ["<NextSectionDialog", "<HarvestDialog"]) {
    assert.ok(!inside.includes(tag), `${tag} must not sit inside the tray`);
    assert.ok(outside.includes(tag), `${tag} must still render`);
  }
});

test("App keeps the layout above the per-session remount and saves it in order", () => {
  // App, not ArtifactPanel: the panel remounts on every new session.
  assert.match(app, /useState<PanelTrayPrefs \| null>\(null\)/);
  assert.doesNotMatch(artifact, /getUiPreferences|saveUiPreferences/);
  assert.match(app, /getUiPreferences\(\)/);
  // One save at a time, in click order.
  assert.match(
    app,
    /panelTraySaves\.current = panelTraySaves\.current\.then\(\(\) =>\s*saveUiPreferences\(panelTrayToApi\(next\)\)/,
  );
  // The tour owns it while it runs, including a reloaded protected workspace.
  assert.match(
    app,
    /panelTrayLocked=\{\s*onboarding\.phase\.kind !== "idle" \|\| inProtectedWorkspace\s*\}/,
  );
});
