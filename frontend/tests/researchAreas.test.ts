/**
 * The research area choice: what the user picked for the next round, and
 * what Research again therefore sends. Two halves:
 *
 * - the pure rules in `lib/researchAreas.ts` (what a choice means), and
 * - source-level pins on the drawer and on App's start handler, because
 *   neither component has a DOM harness (the strandedState.test.ts idiom).
 *
 * The defect this replaced: the picker's own "Research N selected areas"
 * button made choosing and starting one click, and a started round then
 * displayed as finished for its whole run (researchLive.test.ts pins that
 * half). Choosing now only registers; Research again runs the choice.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import {
  chosenResearchAreas,
  researchAgainPlan,
  toggleResearchArea,
} from "../src/lib/researchAreas.ts";
import type { ResearchAreaView } from "../src/types.ts";

const area = (dimension_id: string, title = ""): ResearchAreaView => ({
  dimension_id,
  title,
  required: true,
  optional_rationale: "",
  completed: true,
  recorded: true,
});

// Module declaration order — the order every plan must come back in.
const AREAS = [
  area("governing_codes", "Governing building and fire codes"),
  area("ahj_requirements", "Authority-having-jurisdiction requirements"),
  area("client_standards", "Owner / client and insurer standards"),
  area("site_environment", "Site and environmental factors"),
];

/* --- the rules --- */

test("nothing chosen is a full round", () => {
  assert.deepEqual(researchAgainPlan(AREAS, []), { scope: "all" });
});

test("a true subset runs only those areas, in module order", () => {
  // Picked back to front: the order must still be the module's, because the
  // server takes it from there anyway and a label listing the areas in
  // click order would describe a different round than the one that runs.
  const plan = researchAgainPlan(AREAS, ["site_environment", "governing_codes"]);
  assert.deepEqual(plan, {
    scope: "selected",
    dimensionIds: ["governing_codes", "site_environment"],
    titles: ["Governing building and fire codes", "Site and environmental factors"],
  });
});

test("every area chosen is a full round, not a selection of all of them", () => {
  const plan = researchAgainPlan(
    AREAS,
    AREAS.map((a) => a.dimension_id),
  );
  assert.deepEqual(plan, { scope: "all" });
});

test("an area the module no longer declares drops out of the choice", () => {
  // A choice outlives the poll that drew the picker. Sent as-is, an
  // undeclared id is refused by name and nothing runs.
  assert.deepEqual(
    chosenResearchAreas(AREAS, ["retired_area", "ahj_requirements"]).map(
      (a) => a.dimension_id,
    ),
    ["ahj_requirements"],
  );
  assert.deepEqual(researchAgainPlan(AREAS, ["retired_area"]), { scope: "all" });
});

test("a blank title falls back to the id, so the label never names nothing", () => {
  const plan = researchAgainPlan([area("a"), area("b", "B")], ["a"]);
  assert.equal(plan.scope, "selected");
  assert.deepEqual(plan.scope === "selected" && plan.titles, ["a"]);
});

test("a toggle adds or removes exactly the one area", () => {
  assert.deepEqual(toggleResearchArea([], "a"), ["a"]);
  assert.deepEqual(toggleResearchArea(["a", "b"], "a"), ["b"]);
  assert.deepEqual(toggleResearchArea(["a"], "b"), ["a", "b"]);
});

/* --- the drawer: choosing registers, Research again runs --- */

const here = dirname(fileURLToPath(import.meta.url));
const read = (...parts: string[]) =>
  readFileSync(join(here, "..", "src", ...parts), "utf8");

/** The source of the JSX block that opens at `marker` and closes with the
 *  matching `)}` of its `{cond && (` wrapper — enough for these pins. */
function blockAt(source: string, marker: string): string {
  const at = source.indexOf(marker);
  assert.ok(at >= 0, `${marker} not found`);
  const start = source.lastIndexOf("{", source.lastIndexOf("&& (", at));
  let depth = 0;
  for (let i = start; i < source.length; i += 1) {
    if (source[i] === "{") depth += 1;
    else if (source[i] === "}") {
      depth -= 1;
      if (depth === 0) return source.slice(start, i + 1);
    }
  }
  assert.fail(`${marker} block never closes`);
}

/** The body of `const <name> = async () => { … };`. */
function handlerBody(source: string, name: string): string {
  const start = source.indexOf(`const ${name} = `);
  assert.ok(start >= 0, `${name} not found`);
  const open = source.indexOf("{", source.indexOf("=>", start));
  let depth = 0;
  for (let i = open; i < source.length; i += 1) {
    if (source[i] === "{") depth += 1;
    else if (source[i] === "}") {
      depth -= 1;
      if (depth === 0) return source.slice(open, i + 1);
    }
  }
  assert.fail(`${name} body never closes`);
}

const drawer = read("components", "ResearchDrawer.tsx");

test("nothing in the area picker starts a round", () => {
  const picker = blockAt(drawer, 'data-testid="research-area-picker"');
  assert.doesNotMatch(picker, /onStart\(/, "the picker must never call onStart");
  assert.doesNotMatch(picker, /researchAgain\(/, "the picker must never run the round");
  // It says so where the user is choosing.
  assert.match(picker, /Nothing starts until you press Research again/);
  // Done keeps the choice: it only closes the list.
  assert.match(picker, /onClick=\{\(\) => setPickerOpen\(false\)\}/);
});

test("the choice stays visible with the list closed", () => {
  const summary = blockAt(drawer, 'data-testid="research-area-choice"');
  assert.match(summary, /runs when you press Research again/);
  assert.doesNotMatch(summary, /onStart\(/);
});

test("Research again runs the chosen areas, and clears them only once started", () => {
  const body = handlerBody(drawer, "researchAgain");
  assert.match(body, /onStart\("selected", plan\.dimensionIds\)/);
  assert.match(body, /onStart\("all"\)/);
  // Awaited, and cleared behind the server's answer: a refused start keeps
  // the choice for the retry.
  assert.match(body, /if \(started\) \{\s*setPicked\(\[\]\);/);
  // The main button is what calls it — the tour's research-start anchor.
  assert.match(
    drawer,
    /onClick=\{\(\) => void researchAgain\(\)\}\s*disabled=\{startDisabled\}\s*data-tour="research-start"/,
  );
  // One plan feeds the label, the tooltip and the click.
  assert.match(drawer, /const plan = researchAgainPlan\(areas, picked\);/);
});

test("choosing is never disabled by what would block a start", () => {
  const at = drawer.indexOf("{chooseLabel}");
  assert.ok(at >= 0);
  const button = drawer.slice(drawer.lastIndexOf("<button", at), at);
  assert.doesNotMatch(button, /disabled=/);
  assert.match(button, /aria-expanded=\{pickerOpen\}/);
});

test("a workspace transition drops the choice", () => {
  // Scoped to the effect's own branch: the calls also appear in the Clear
  // buttons and after a start, so a file-wide match would pass without it.
  const at = drawer.indexOf("if (!research) {");
  assert.ok(at >= 0);
  const open = drawer.indexOf("{", at);
  let depth = 0;
  let close = -1;
  for (let i = open; i < drawer.length; i += 1) {
    if (drawer[i] === "{") depth += 1;
    else if (drawer[i] === "}") {
      depth -= 1;
      if (depth === 0) {
        close = i;
        break;
      }
    }
  }
  const branch = drawer.slice(open, close + 1);
  assert.match(branch, /setPicked\(\[\]\);/);
  assert.match(branch, /setPickerOpen\(false\);/);
});

/* --- App's start handler --- */

test("the start handler guards re-entry, shows running at once, and says if it started", () => {
  const app = read("App.tsx");
  const at = app.indexOf("const onStartResearch = useCallback(");
  assert.ok(at >= 0);
  const handler = app.slice(at, app.indexOf("const onStopResearch", at));
  assert.match(handler, /Promise<boolean>/);
  assert.match(handler, /if \(researchStartingRef\.current\) return false;/);
  assert.match(handler, /finally \{\s*researchStartingRef\.current = false;\s*\}/);
  // Running the moment the server accepts: a fresh log, the findings kept.
  assert.match(
    handler,
    /replaceResearchSnapshot\(\{\s*status: "running",\s*error: "",\s*error_kind: "",\s*events: \[\],\s*profile: previous\?\.profile,\s*coverage: previous\?\.coverage,\s*\}\)/,
  );
  // A refused start keeps the findings on screen too.
  assert.match(handler, /profile: researchSnapshotRef\.current\?\.profile,/);
  assert.equal((handler.match(/return true;/g) ?? []).length, 1);
  // The panel's prop carries the answer through to the drawer.
  const panel = read("components", "ArtifactPanel.tsx");
  assert.match(
    panel,
    /onStartResearch: \(\s*scope\?: ResearchScope,\s*dimensionIds\?: string\[\],\s*\) => Promise<boolean>;/,
  );
});
