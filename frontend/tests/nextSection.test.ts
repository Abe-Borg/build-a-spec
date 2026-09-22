/**
 * Next section in one click (v1.20.0) — pinned at the source level, the
 * strandedState.test.ts idiom: App.tsx has no DOM harness, and the three
 * things that matter here are wiring a later edit could quietly undo.
 *
 * 1. The save gate runs BEFORE the seed. The section being left behind is
 *    the user's work; a click that replaced it without the Save / Continue
 *    without saving / Cancel prompt is the content-loss the gate exists to
 *    prevent.
 * 2. The gate's resume path knows the new kind. A gate kind nobody resumes
 *    is a dialog whose "Save, then start" saves and then does nothing.
 * 3. The dialog offers "leave it unnamed": a named page counts as content
 *    and the master import refuses it, so the choice must be made here.
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
const panel = read("components", "ArtifactPanel.tsx");
const dialog = read("components", "NextSectionDialog.tsx");
const api = read("lib", "api.ts");

test("the next-section click runs the save gate before the seed", () => {
  const request = /const requestStartNextSection = async[\s\S]*?\n  \};/.exec(app)?.[0];
  assert.ok(request, "requestStartNextSection must exist");
  assert.match(request, /if \(await isUnsaved\(\)\)/);
  assert.match(request, /setSaveGate\(\{ kind: "next-section", opts \}\)/);
  assert.match(request, /else \{\s*void doStartNextSection\(opts\);/);
  // The panel hands the choice to the gated request, never to the seed.
  assert.match(app, /onStartNextSection=\{\(opts\) => void requestStartNextSection\(opts\)\}/);
  assert.doesNotMatch(app, /onStartNextSection=\{[^}]*doStartNextSection/);
});

test("the gate resumes a next-section start once the user has chosen", () => {
  const runGate = /const runGate = \([\s\S]*?\n  \};/.exec(app)?.[0];
  assert.ok(runGate, "runGate must exist");
  assert.match(runGate, /gate\.kind === "next-section"[\s\S]*?void doStartNextSection\(gate\.opts\)/);
  // The union carries the kind, so a missing branch is a type error too.
  assert.match(app, /\{ kind: "next-section"; opts: NextSectionRequest \}/);
  // And the prompt names the action rather than falling through to "new session".
  assert.match(app, /saveGate\?\.kind === "start-brief" \|\| saveGate\?\.kind === "next-section"/);
});

test("the panel button is hidden in a tour and declares its capability", () => {
  const button = /\{!tutorialActive && \(\s*<button[\s\S]*?Next section →[\s\S]*?<\/button>\s*\)\}/.exec(panel)?.[0];
  assert.ok(button, "the Next section button must be gated on tutorialActive");
  assert.match(button, /data-capability="project\.next-section"/);
  assert.match(button, /disabled=\{busy \|\| !!fileLoading\}/);
});

test("the dialog offers a named pick, a typed header, and an unnamed page", () => {
  assert.match(dialog, /kind: "catalog"/);
  assert.match(dialog, /kind: "custom"/);
  assert.match(dialog, /kind: "unnamed"/);
  // A drafted section is shown greyed, never dropped from the list.
  assert.match(dialog, /already drafted/);
  assert.match(dialog, /disabled=\{entry\.done\}/);
  // The unnamed option says why it exists.
  assert.match(dialog, /importing an office master needs an empty one/);
  // Closing the dialog hands the choice up; it never calls the API itself.
  assert.doesNotMatch(dialog, /startNextSection\(/);
  assert.match(dialog, /nextSectionOptions\(\)/);
});

test("a typed number the project already drafted cannot be started", () => {
  // The catalog greys drafted sections; the typed path must not be a way
  // around it. The server refuses it too (409 section_already_drafted) —
  // this is the visible half of one rule, not the only half.
  assert.match(dialog, /options\.done\.includes\(fold\(number\)\)/);
  assert.match(dialog, /disabled=\{\s*!choice \|\|\s*typedIsDrafted \|\|/);
  assert.match(dialog, /is already drafted in this project/);
});

test("the client posts JSON to the one route and reads back the bundle", () => {
  const fn = /export async function startNextSection[\s\S]*?\n\}/.exec(api)?.[0];
  assert.ok(fn);
  assert.match(fn, /fetch\("\/api\/project\/next-section", \{\s*method: "POST"/);
  assert.match(fn, /template_id: opts\.templateId \?\? ""/);
  assert.match(fn, /session\.seed = data\.seed as SeedReport/);
});
