/**
 * The Word-files guide in Help quotes the app's own labels and messages, so
 * every quote is pinned to the file that shows it: renaming an Export menu
 * item, the import notice or a refusal fails here instead of leaving the
 * guide telling the user to look for something that is gone.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import * as guide from "../src/lib/wordFileGuidance.ts";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");

const help = read("../src/components/HelpModal.tsx");
const artifact = read("../src/components/ArtifactPanel.tsx");
const nextSection = read("../src/components/NextSectionDialog.tsx");
const importer = read("../../backend/spec_doc/importer.py");
const sourceRender = read("../../backend/spec_doc/source_render.py");
const app = read("../../backend/app.py");

// Everything the guide says, as one searchable string.
const guideText = JSON.stringify(guide);

/** A phrase the guide quotes, and the source that must still show it. */
const QUOTED: Array<[string, string, string]> = [
  ["Export Word - Tracked Changes ON", "ArtifactPanel.tsx", artifact],
  ["Redline on your original", "ArtifactPanel.tsx", artifact],
  ["Redline vs version…", "ArtifactPanel.tsx", artifact],
  ["Download exact original DOCX", "ArtifactPanel.tsx", artifact],
  ["Import Spec", "ArtifactPanel.tsx", artifact],
  ["Attach Document", "ArtifactPanel.tsx", artifact],
  ["Next section", "ArtifactPanel.tsx", artifact],
  ["Leave it unnamed", "NextSectionDialog.tsx", nextSection],
  ["The master carries pending tracked changes", "importer.py", importer],
  ["A provision you moved carries a comment", "source_render.py", sourceRender],
];

test("every label and message the guide quotes is still shown where the guide says", () => {
  for (const [phrase, where, source] of QUOTED) {
    assert.ok(guideText.includes(phrase), `the guide no longer quotes "${phrase}"`);
    assert.ok(source.includes(phrase), `"${phrase}" is gone from ${where}`);
  }
});

test("the behaviours the guide's warnings rest on are still there", () => {
  // "Redline on your original greyed out" is the warning sign for a file
  // with pending tracked changes: the payload refuses it by name, and the
  // menu item disables on that payload.
  assert.match(
    app,
    /def _preserved_redline_availability[\s\S]*?REDLINE_PENDING_REVISIONS/,
  );
  assert.match(artifact, /disabled=\{!preservedRedlineAvailable/);
  // "A section with content can't take a second import."
  assert.match(app, /if session\.doc\.doc\.has_body_content\(\):\s*return JSONResponse/);
  assert.match(app, /"The document already has content — a master "/);
  // "Redline vs version… (pick the version in Compare mode)".
  assert.match(artifact, /Enter compare mode and pick a version first/);
});

test("How to use renders the guide, ahead of the source-option definitions", () => {
  const body = /function HowToUse\(([\s\S]*?)\nfunction Workflows\(/.exec(help)?.[1];
  assert.ok(body, "HowToUse not found in HelpModal.tsx");
  const card = body.indexOf("<WordFilesGuide />");
  assert.ok(card >= 0, "How to use does not render the Word-files guide");
  assert.ok(card < body.indexOf("<SourceOutputGuide />"));
  assert.match(help, /from "\.\.\/lib\/wordFileGuidance"/);
  // The getting-started step points at it before anyone imports a master.
  assert.match(body.replace(/\s+/g, " "), /Clean it in Word first — see Working with Word files below/);
});

test("the guide keeps its four habits and its warning sign", () => {
  assert.deepEqual(
    guide.WORD_FILE_STAGES.map((stage) => stage.id),
    ["before-import", "import-once", "every-session", "editing"],
  );
  assert.equal(guide.WORD_FILE_DO.length, guide.WORD_FILE_DONT.length);
  const importOnce = guide.WORD_FILE_STAGES.find((stage) => stage.id === "import-once");
  assert.equal(importOnce?.note?.tone, "warn");
  assert.match(guide.WORD_FILES_SHORT_VERSION, /reopen the \.baspec/);
});
