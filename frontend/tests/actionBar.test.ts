/**
 * The document panel's action bar fits the pane.
 *
 * It holds more controls than one row of the narrow pane can take, so the
 * bar wraps BETWEEN groups that each stay on one line, and no label may
 * break. Measured in Chromium at the 1100 and 1440 window sizes (the PR
 * that added this pins the numbers); this suite pins the mechanism at the
 * source level, since there is no DOM harness.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const artifact = readFileSync(
  new URL("../src/components/ArtifactPanel.tsx", import.meta.url),
  "utf8",
);

const start = artifact.indexOf('data-testid="doc-action-bar"');
const end = artifact.indexOf("{fileLoading && (", start);
// The opening tag of the bar starts just before its test id.
const bar = artifact.slice(artifact.lastIndexOf("<div", start), end);

test("the bar wraps between groups that each stay on one line", () => {
  assert.ok(start > 0 && end > start, "the action bar is found");
  assert.match(bar, /^<div\s+className="flex flex-wrap items-center justify-end/);
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
