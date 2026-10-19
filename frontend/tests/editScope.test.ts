/**
 * The edit scope (2026-10-19): Extensive, or Essentials only. The definition
 * the assistant and Final QC work from is backend/edit_scope.py; the
 * composer's switch and the panel's badge only describe it. Helper tests plus
 * source pins (the editReasons.test.ts convention): no DOM, no network.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  DEFAULT_EDIT_SCOPE,
  EDIT_SCOPE_OPTIONS,
  editScopeOption,
  parseEditScope,
} from "../src/lib/editScope.ts";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");
const backend = read("../../backend/edit_scope.py");
const composer = read("../src/components/Composer.tsx");
const panel = read("../src/components/ArtifactPanel.tsx");
const app = read("../src/App.tsx");
const api = read("../src/lib/api.ts");

test("an older server or a bad value reads as Extensive", () => {
  assert.equal(DEFAULT_EDIT_SCOPE, "extensive");
  assert.equal(parseEditScope("essentials"), "essentials");
  for (const value of [undefined, null, "", "Essentials", "minimal", 1]) {
    assert.equal(parseEditScope(value), "extensive");
  }
});

test("the switch offers the two modes the server knows, Essentials first", () => {
  assert.deepEqual(
    EDIT_SCOPE_OPTIONS.map((option) => option.id),
    ["essentials", "extensive"],
  );
  assert.match(backend, /EDIT_SCOPES = \(EXTENSIVE, ESSENTIALS\)/);
  assert.match(backend, /EXTENSIVE = "extensive"/);
  assert.match(backend, /ESSENTIALS = "essentials"/);
  assert.equal(editScopeOption("essentials").label, "Essentials only");
});

test("the Essentials hover names every essential category the backend lists", () => {
  const detail = editScopeOption("essentials").detail.toLowerCase();
  // One phrase per backend category key: the hover is the user's summary of
  // the list the assistant works from, so a new category must reach it.
  const phrases: Record<string, string> = {
    code_compliance: "code and standard conflicts",
    standards_references: "outdated editions",
    scope_gap: "scope gaps",
    inapplicable_content: "cannot apply to this project",
    project_values: "wrong project values",
    conflicts_references: "conflicts and broken references",
    technical_error: "technical errors",
    explanatory_prose: "explanatory prose and notes",
    unissuable: "unissuable leftovers",
  };
  const keys = [...backend.matchAll(/key="([a-z_]+)"/g)].map((match) => match[1]);
  assert.deepEqual(keys.sort(), Object.keys(phrases).sort());
  for (const phrase of Object.values(phrases)) assert.ok(detail.includes(phrase), phrase);
  assert.ok(detail.includes("asked about, never edited"));
});

test("the controls are real: saved through the server, no preview left", () => {
  assert.match(composer, /data-capability="chat\.edit-scope"/);
  assert.match(composer, /disabled=\{disabled \|\| editScopeLocked \|\| scopeSaving\}/);
  assert.doesNotMatch(composer, /preview/i);
  assert.doesNotMatch(panel, /EDIT_SCOPE_PREVIEW_NOTE|· preview/);
  assert.match(api, /"\/api\/session\/edit-scope"/);
  assert.match(app, /setEditScope\(await saveEditScope\(next\)\)/);
  // Every payload path resyncs it, and New session clears it.
  assert.equal([...app.matchAll(/setEditScope\(parseEditScope\(payload\.edit_scope\)\)/g)].length, 2);
  assert.match(app, /edit_scope: merged\.edit_scope/);
  assert.match(app, /setEditScope\(DEFAULT_EDIT_SCOPE\)/);
});
