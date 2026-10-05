/**
 * Paid harvest work belongs to the session, not the modal's visibility.
 * Pure lifecycle rules plus component wiring pins: no DOM harness and no
 * network/model calls. Pins connect the rules to the actual click, closing,
 * focus and session-replacement paths (the harvest.test.ts convention).
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import {
  beginHarvestRun,
  canOpenHarvest,
  closeHarvestPhase,
  harvestActivity,
  harvestDoorHint,
  type HarvestPhase,
} from "../src/lib/harvestLifecycle.ts";
import type { HarvestCommitResult, HarvestPreview, HarvestStatus } from "../src/types.ts";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");
const dialog = read("../src/components/HarvestDialog.tsx");
const artifact = read("../src/components/ArtifactPanel.tsx");
const panel = read("../src/components/ProjectFactsPanel.tsx");
const app = read("../src/App.tsx");

const sheet: HarvestPreview = {
  ok: true, token: "paid-sheet", proposals: [], dropped_duplicates: 0,
  transcript_truncated: false, turns_read: 1, turns_dropped: 0,
  first_turn: 1, last_turn: 1, replies_total: 1, since_bubble: 0,
  provisions: 0, dismissals: 0, usage: {}, estimated_cost_usd: 0.04,
};
const empty: HarvestStatus = {
  replies_since: 0, last_bubble: 1, replies_total: 1, harvestable: false,
};
const done: HarvestCommitResult = {
  ok: true, project_facts: [], harvest: empty, recorded: [], already_recorded: [],
};

test("closing and reopening an in-flight harvest keeps running and refuses another Run", () => {
  const running = beginHarvestRun({ kind: "intro" });
  assert.deepEqual(running, { kind: "running" });
  assert.ok(running);
  for (let visit = 0; visit < 3; visit++) {
    assert.strictEqual(closeHarvestPhase(running), running);
    assert.equal(harvestActivity(running), "running");
    assert.equal(beginHarvestRun(running), null, "neither reopening nor a double click starts another call");
  }
});

test("closing a preview keeps its paid token, including a sheet proposing zero facts", () => {
  const review: HarvestPhase = { kind: "review", preview: sheet };
  const reopened = closeHarvestPhase(review);
  assert.strictEqual(reopened, review, "no reinitialization of a ready sheet");
  assert.equal(harvestActivity(reopened), "ready");
  assert.equal(beginHarvestRun(reopened), null, "review never offers another paid run");
  assert.equal(reopened.kind === "review" && reopened.preview.token, "paid-sheet");
});

test("a closed failure stays visible on return; retry remains a deliberate click", () => {
  for (const code of ["harvest_refused", "harvest_stale", "harvest_expired", ""]) {
    const failure: HarvestPhase = { kind: "failed", code, message: "Try later" };
    assert.strictEqual(closeHarvestPhase(failure), failure);
    assert.equal(harvestActivity(failure), "idle");
    assert.deepEqual(beginHarvestRun(failure), { kind: "running" });
  }
  for (const code of ["tutorial_active", "nothing_to_harvest"]) {
    assert.equal(beginHarvestRun({ kind: "failed", code, message: "Cannot run" }), null);
  }
});

test("closing after a successful commit releases its consumed token for the next visit", () => {
  const committed: HarvestPhase = { kind: "done", result: done, accepted: 0 };
  assert.equal(beginHarvestRun(committed), null);
  assert.equal(harvestActivity(committed), "idle");
  assert.deepEqual(closeHarvestPhase(committed), { kind: "intro" });
  assert.deepEqual(beginHarvestRun(closeHarvestPhase(committed)), { kind: "running" });
});

test("running/ready hints take precedence over unread replies at every door", () => {
  const unread = { ...empty, replies_since: 4, last_bubble: 0, replies_total: 4, harvestable: true };
  for (const status of [null, empty, unread]) {
    assert.equal(harvestDoorHint(status, "running"), "Fact harvest is still running");
    assert.equal(harvestDoorHint(status, "ready"), "Fact harvest is ready to review");
  }
  assert.equal(harvestDoorHint(null, "idle"), "");
  assert.equal(harvestDoorHint(empty, "idle"), "");
  assert.equal(harvestDoorHint(unread, "idle"), "4 replies not yet harvested for facts");
});

test("retained work can be reopened when no new material is harvestable", () => {
  for (const status of [null, empty]) {
    assert.equal(canOpenHarvest(status, "idle"), false);
    assert.equal(canOpenHarvest(status, "running"), true);
    assert.equal(canOpenHarvest(status, "ready"), true);
  }
  assert.equal(canOpenHarvest({ ...empty, harvestable: true }, "idle"), true);
});

test("closing removes only the shell; the session's state owner stays mounted", () => {
  assert.match(artifact, /\{!tutorialActive && \(\s*<HarvestDialog\s+open=\{harvestOpen\}/);
  assert.doesNotMatch(artifact, /harvestOpen && !tutorialActive/);
  assert.match(artifact, /onClose=\{\(\) => setHarvestOpen\(false\)\}/);
  assert.match(dialog, /if \(!open\) return null;\s*return \(\s*<ModalShell/);
  // Retain edited rows/selections/errors as well as the token. There is no
  // visibility effect resetting them, and the mount guard isn't keyed to open.
  assert.match(dialog, /mounted\.current = true;[\s\S]*?mounted\.current = false;[\s\S]*?\}, \[\]\);/);
  const effects = dialog.match(/useEffect\(\(\) => \{[\s\S]*?\}, \[[^\]]*\]\);/g) ?? [];
  assert.equal(effects.length, 2);
  for (const effect of effects) {
    assert.doesNotMatch(effect, /setPhase|setAccepted|setDrafts|setEditing|setRowErrors|setCommitError|\bopen\b/);
  }
});

test("Run stores its guard synchronously before awaiting the paid handler", () => {
  assert.match(dialog, /const phaseRef = useRef<HarvestPhase>\(phase\)/);
  assert.match(dialog, /const setPhase = \(next: HarvestPhase\) => \{\s*phaseRef\.current = next;\s*renderPhase\(next\);/);
  assert.match(dialog, /const run = async \(\) => \{\s*const running = beginHarvestRun\(phaseRef\.current\);\s*if \(!running\) return;\s*setPhase\(running\);[\s\S]*?await onRun\(\)/);
});

test("a hidden dialog adopts success and failure; a replaced session drops both", () => {
  const run = dialog.slice(dialog.indexOf("const run = async"), dialog.indexOf("const commit = async"));
  assert.match(run, /const preview = await onRun\(\);\s*if \(!mounted\.current\) return;\s*setAccepted/);
  assert.match(run, /setPhase\(\{ kind: "review", preview \}\)/);
  assert.match(run, /catch \(error\) \{\s*if \(!mounted\.current\) return;\s*setPhase/);
  assert.doesNotMatch(run, /\bopen\b|onClose|AbortController/);
  const commit = dialog.slice(dialog.indexOf("const commit = async"), dialog.indexOf("const toggle ="));
  assert.match(commit, /const result = await onCommit\(input\);\s*if \(!mounted\.current\) return;/);
  assert.match(commit, /catch \(error\) \{\s*if \(!mounted\.current\) return;/);
});

test("new sessions and project loads discard the retained sheet's entire owner", () => {
  assert.match(app, /<ArtifactPanel\s+key=\{`panel-\$\{sessionNonce\}`\}/);
  const discard = app.slice(app.indexOf("const discardPaneState ="), app.indexOf("const clearSessionState ="));
  assert.match(discard, /setSessionNonce\(\(n\) => n \+ 1\)/);
  const clear = app.slice(app.indexOf("const clearSessionState ="), app.indexOf("const applyDocPayload ="));
  assert.match(clear, /discardPaneState\(\)/);
  const load = app.slice(app.indexOf("const applyLoadedProject ="), app.indexOf("const bindNativeProjectHome ="));
  assert.match(load, /discardPaneState\(\);\s*if \(!applyDocPayload\(result\)\)/);
  // All reset handlers still pass through the shared clear boundary.
  const reset = app.slice(app.indexOf("const startBlankSession ="), app.indexOf("const applyDocPayload ="));
  assert.match(reset, /clearSessionState\(\)/);
});

test("all doors announce the retained activity, and the facts door still opens during work", () => {
  assert.match(dialog, /onActivityChange\(harvestActivity\(phase\)\)/);
  assert.match(artifact, /onActivityChange=\{setHarvestActivity\}/);
  assert.match(artifact, /tutorialActive \? "" : harvestDoorHint\(harvestStatus, harvestActivity\)/);
  assert.match(artifact, /harvestActivity=\{harvestActivity\}/);
  assert.match(artifact, /harvestHint=\{pendingHarvest\}/);
  assert.match(artifact, /\{pendingHarvest\} — Harvest first…/);
  assert.match(panel, /harvestAvailable \? harvestDoorHint\(harvest, harvestActivity\) : ""/);
  assert.match(panel, /const harvestable = harvestAvailable && canOpenHarvest\(harvest, harvestActivity\)/);
  assert.match(panel, /if \(items\.length === 0 && link === null && !harvestable\) return null/);
  assert.match(panel, /disabled=\{\(busy && harvestActivity === "idle"\) \|\| !harvestable\}\s*onClick=\{onHarvest\}/);
});

test("all closing routes stay available mid-run and use the retaining close rule", () => {
  assert.match(dialog, /const close = \(\) => \{\s*setPhase\(closeHarvestPhase\(phaseRef\.current\)\);\s*onClose\(\);/);
  assert.match(dialog, /<ModalShell title="Harvest project facts" onClose=\{close\} xwide>/);
  const running = dialog.slice(dialog.indexOf('{phase.kind === "running" &&'), dialog.indexOf('{phase.kind === "failed" &&'));
  assert.match(running, /You can close this while it runs/);
  assert.match(running, /<button[^>]*onClick=\{close\}>\s*Close/);
  assert.doesNotMatch(running, /disabled|onRun|void run/);
  // Escape, backdrop, and the header X continue to call the shared close.
  const shell = read("../src/components/ModalShell.tsx");
  assert.match(shell, /useDialogFocus\(true, panelRef, panelRef, onEscape \?\? onClose\)/);
  assert.match(shell, /onClick=\{onClose\}\s*role="dialog"/);
  assert.match(shell, /onClick=\{onClose\}\s*aria-label="Close"/);
});

test("discarding a retained preview is explicit and never runs or commits anything", () => {
  assert.match(dialog, /onClick=\{\(\) => setPhase\(\{ kind: "intro" \}\)\}[\s\S]*?Discard preview/);
  const close = dialog.slice(dialog.indexOf("const close ="), dialog.indexOf("const run ="));
  assert.doesNotMatch(close, /setAccepted|setDrafts|onRun|onCommit|kind: "intro"/);
});

test("npm test includes the harvest lifecycle regressions", () => {
  assert.ok(JSON.parse(read("../package.json")).scripts.test.includes("tests/harvestLifecycle.test.ts"));
});
