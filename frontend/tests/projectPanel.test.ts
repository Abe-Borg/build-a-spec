/**
 * The Project panel (Project workspace Phase 2) — pinned at the source level,
 * the nextSection.test.ts idiom: App.tsx and the panel have no DOM harness,
 * and what matters here is wiring a later edit could quietly undo.
 *
 * 1. Open runs the save gate BEFORE the swap. The section being left behind
 *    is the user's work; opening a sibling without the Save / Open without
 *    saving / Cancel prompt is the content loss the gate exists to prevent.
 * 2. The gate's resume path knows the new kind, and says what it does.
 * 3. The panel never sends a path. It asks by section NUMBER; the server
 *    resolves the number to a file it refuses to find outside the folder.
 * 4. The panel is desktop-only (a browser can never have a folder) except
 *    in the tour, and its actions are hidden there — a practice copy is not
 *    a project, and the tour is a track the user only watches.
 * 5. The folder binding after a native open, and the home a save reports,
 *    are adopted only for the session they describe.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const here = dirname(fileURLToPath(import.meta.url));
const read = (...parts: string[]) =>
  readFileSync(join(here, "..", "src", ...parts), "utf8");

const app = read("App.tsx");
const panel = read("components", "ProjectPanel.tsx");
const artifact = read("components", "ArtifactPanel.tsx");
const api = read("lib", "api.ts");
const tour = read("lib", "tour.ts");

test("Open on a row runs the save gate before the section is swapped", () => {
  const request = /const requestOpenSection = async[\s\S]*?\n  \};/.exec(app)?.[0];
  assert.ok(request, "requestOpenSection must exist");
  assert.match(request, /if \(await isUnsaved\(\)\) setSaveGate\(\{ kind: "open-section", number \}\)/);
  assert.match(request, /else void doOpenSection\(number\);/);
  // The panel hands the number to the gated request, never to the swap.
  assert.match(app, /onOpenSection=\{\(number\) => void requestOpenSection\(number\)\}/);
  assert.doesNotMatch(app, /onOpenSection=\{[^}]*doOpenSection/);
});

test("the gate resumes an open-section and names it", () => {
  const runGate = /const runGate = \([\s\S]*?\n  \};/.exec(app)?.[0];
  assert.ok(runGate, "runGate must exist");
  assert.match(runGate, /gate\.kind === "open-section"\) void doOpenSection\(gate\.number\)/);
  assert.match(app, /\{ kind: "open-section"; number: string \}/);
  assert.match(app, /"Open another section of this project\?"/);
  assert.match(
    app,
    /saveGate\?\.kind === "open-project" \|\| saveGate\?\.kind === "open-section"\s*\? "Save, then open"/,
  );
});

test("the swap is the ordinary project load, applied the one way", () => {
  const open = /const doOpenSection = async[\s\S]*?\n  \};/.exec(app)?.[0];
  assert.ok(open, "doOpenSection must exist");
  assert.match(open, /if \(fileLoadingRef\.current\) return;/);
  assert.match(open, /await openSection\(number\)/);
  assert.match(open, /applyLoadedProject\(result, `Section \$\{number\}`\)/);
  // Open project and the panel's Open share one apply path.
  const load = /const doLoadProject = async[\s\S]*?\n  \};/.exec(app)?.[0];
  assert.ok(load, "doLoadProject must exist");
  assert.match(load, /applyLoadedProject\(result, file\.name\)/);
});

test("the panel asks by number and never holds a path to send", () => {
  const fn = /export async function openSection[\s\S]*?\n\}/.exec(api)?.[0];
  assert.ok(fn, "openSection must exist");
  assert.match(fn, /fetch\("\/api\/project\/open-section", \{\s*method: "POST"/);
  assert.match(fn, /body: JSON\.stringify\(\{ number \}\)/);
  assert.match(panel, /onOpenSection\(row\.number\)/);
  assert.doesNotMatch(panel, /onOpenSection\([^)]*(folder|file_name|path)/);
  const sections = /export async function projectSections[\s\S]*?\n\}/.exec(api)?.[0];
  assert.ok(sections);
  assert.match(sections, /fetch\("\/api\/project\/sections"\)/);
});

test("the panel is desktop-only outside the tour, and inert inside it", () => {
  assert.match(
    panel,
    /const visible = \(link !== null \|\| home !== null\) && \(desktopShell \|\| tutorialActive\);/,
  );
  assert.match(panel, /window\.pywebview\?\.api\?\.bind_project_home/);
  assert.match(panel, /"pywebviewready"/);
  // Its root is the tour's anchor and declares the panel's capability.
  assert.match(panel, /data-tour="project-panel"/);
  assert.match(panel, /data-capability="project\.sections"/);
  // Open is hidden in a tour and locked while anything runs.
  const open = /!tutorialActive && \(\s*<button[\s\S]*?Open\s*<\/button>\s*\)/.exec(panel)?.[0];
  assert.ok(open, "the Open button must be gated on tutorialActive");
  assert.match(open, /data-capability="project\.open-section"/);
  assert.match(open, /disabled=\{busy\}/);
  // An Open only exists for a present file in a known folder.
  assert.match(panel, /!where \? null : row\.present \?/);
  // Next section → from the panel is the same dialog, hidden in a tour too.
  const next = /\{!tutorialActive && \(\s*<button[\s\S]*?Next section →[\s\S]*?<\/button>\s*\)\}/.exec(
    panel,
  )?.[0];
  assert.ok(next, "the panel's Next section → must be gated on tutorialActive");
  assert.match(next, /data-capability="project\.next-section"/);
  assert.match(artifact, /onNextSection=\{\(\) => setNextSectionOpen\(true\)\}/);
  assert.match(artifact, /busy=\{busy \|\| !!fileLoading\}/);
});

test("a native open binds its folder only for the session its load produced", () => {
  // The shell's token rides the File; the path never reaches this side.
  assert.match(app, /const nativeOpenTokens = new WeakMap<File, string>\(\);/);
  assert.match(app, /if \(picked\.token\) nativeOpenTokens\.set\(file, picked\.token\);/);
  const load = /const doLoadProject = async[\s\S]*?\n  \};/.exec(app)?.[0];
  assert.ok(load);
  // Bound AFTER the load is applied, with the generation the load reported.
  assert.match(
    load,
    /if \(!applyLoadedProject\(result, file\.name\)\) return;[\s\S]*?const token = nativeOpenTokens\.get\(file\);\s*if \(token\) await bindNativeProjectHome\(token, result\.generation\);/,
  );
  const bind = /const bindNativeProjectHome = async[\s\S]*?\n  \};/.exec(app)?.[0];
  assert.ok(bind, "bindNativeProjectHome must exist");
  assert.match(bind, /api\.bind_project_home\(token, generation\)/);
  assert.match(bind, /if \(workspaceEpochRef\.current !== epoch \|\| !bound\?\.ok\) return;/);
});

test("a save's home is adopted only beside a bound target", () => {
  const save = /const saveProjectFile = async[\s\S]*?\n  \};/.exec(app)?.[0];
  assert.ok(save, "saveProjectFile must exist");
  assert.match(save, /if \(result\.target\) setProjectHome\(result\.home \?\? null\);/);
  assert.match(save, /setProjectSectionsNonce\(\(n\) => n \+ 1\)/);
  // A brief export can make the home too; the payload is re-read, not guessed.
  const brief = /const saveProjectBrief = async[\s\S]*?\n  \};/.exec(app)?.[0];
  assert.ok(brief, "saveProjectBrief must exist");
  assert.match(brief, /refreshDoc\(\);/);
  // And a whole-session replacement forgets the outgoing folder at once.
  const clear = /const clearSessionState = \(\) => \{[\s\S]*?\n  \};/.exec(app)?.[0];
  assert.ok(clear, "clearSessionState must exist");
  assert.match(clear, /setProjectHome\(null\);/);
});

test("the tour step points at the panel inside the paper chapter", () => {
  const chapter = /id:\s*"paper"[\s\S]*?\n\s{2}\},/.exec(tour)?.[0];
  assert.ok(chapter, "the paper chapter must exist");
  const step = /id:\s*"project-panel"[\s\S]*?\n\s{6}\}/.exec(chapter)?.[0];
  assert.ok(step, "the project-panel step must sit in the paper chapter");
  assert.match(step, /anchor:\s*"project-panel"/);
  assert.match(step, /drawer:\s*"projectPanel"/);
  assert.match(step, /mode:\s*"explanatory"/);
  assert.match(step, /"project\.sections", "project\.open-section"/);
  // Right after the project-facts step, as the spec places it.
  assert.ok(chapter.indexOf('id: "project-facts"') < chapter.indexOf('id: "project-panel"'));
  assert.match(tour, /TOUR_VERSION = 8;/);
});
