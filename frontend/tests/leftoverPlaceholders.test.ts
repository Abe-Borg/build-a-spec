/**
 * PR 4 of the specification-voice rule (owner, 2026-10-06): the interface no
 * longer presents [TBD] or "needs input" as a way to work. Legacy content
 * still loads, renders, counts and can be resolved; nothing new is written.
 * Source pins only (the harvest.test.ts convention): no DOM, no network.
 */
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { PANEL_LABELS, foldedAttention } from "../src/lib/panelTray.ts";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");
const specDocument = read("../src/components/SpecDocument.tsx");
const artifact = read("../src/components/ArtifactPanel.tsx");
const tour = read("../src/lib/tour.ts");
const types = read("../src/types.ts");
const onboarding = read("../src/lib/useOnboarding.ts");
const app = read("../src/App.tsx");

function sourceFiles(directory: string): string[] {
  return readdirSync(directory).flatMap((name) => {
    const path = `${directory}/${name}`;
    if (statSync(path).isDirectory()) return sourceFiles(path);
    return /\.(tsx?|jsx?)$/.test(name) ? [path] : [];
  });
}

const productionUi = sourceFiles(fileURLToPath(new URL("../src/", import.meta.url)))
  .map((path) => readFileSync(path, "utf8"))
  .join("\n");

test("a blank section header shows a greyed hint, never a [TBD]", () => {
  // The export prints a blank number as a bare SECTION and a blank title
  // as an empty line (PR 3); the panel must not write what the spec can't.
  assert.doesNotMatch(specDocument, /"\[TBD\]"/);
  assert.doesNotMatch(specDocument, /"\[TBD: section title\]"/);
  assert.equal(
    [...specDocument.matchAll(/<HeaderPlaceholder>number not set<\/HeaderPlaceholder>/g)].length,
    2,
    "the live header and the compare view both use the hint for the number",
  );
  assert.equal(
    [...specDocument.matchAll(/<HeaderPlaceholder>title not set<\/HeaderPlaceholder>/g)].length,
    2,
    "the live header and the compare view both use the hint for the title",
  );
  const hint = /function HeaderPlaceholder[\s\S]*?\n\}/.exec(specDocument)?.[0];
  assert.ok(hint, "the hint component must exist");
  assert.match(hint, /text-paper-dim/);
  assert.match(hint, /normal-case/);
});

test("leftover [TBD] markers stay highlighted", () => {
  assert.match(specDocument, /const TBD_SPLIT = \/\(\\\[TBD:\[\^\\\]\]\*\\\]\)\/g;/);
  assert.match(specDocument, /<TbdText text=\{p\.text\} \/>/);
  assert.match(specDocument, /title="Leftover placeholder — rewrite this as a complete provision"/);
});

test("a legacy needs-input row reads as a leftover and switches in one click", () => {
  assert.match(specDocument, /label: "needs input · leftover"/);
  const row = /<RowActions[\s\S]*?onCancelDelete=/.exec(specDocument)?.[0];
  assert.ok(row, "the paragraph row must render RowActions");
  // ✓ Confirm now also covers needs_input; ≈ Mark assumed is needs_input's alone.
  assert.match(
    row,
    /canConfirm=\{\s*p\.status === "assumed" \|\|\s*p\.status === "imported" \|\|\s*p\.status === "needs_input"\s*\}/,
  );
  assert.match(row, /canMarkAssumed=\{p\.status === "needs_input"\}/);
  assert.match(
    row,
    /onMarkAssumed=\{\(\) => \{\s*submit\(\[\s*\{ action: "set_status", target_id: p\.id, status: "assumed" \},?\s*\]\);/,
  );
  const actions = /function RowActions[\s\S]*?\n\}\n/.exec(specDocument)?.[0];
  assert.ok(actions);
  assert.match(actions, /\{canMarkAssumed && \(/);
  assert.match(actions, /onClick=\{onMarkAssumed\}/);
  assert.match(actions, /disabled=\{busy \|\| !statusCapability\.allowed\}/);
});

test("the docs say what the panel's ✏️ does to a legacy row: it confirms it", () => {
  // PR #279 review (Codex P2): the README said retyping kept the badge, but
  // the pencil has always sent `confirmed` for text the user writes, on
  // every row. Pin the op and the sentence together so they cannot drift.
  const replaceOp = /const replaceOp: EditOp = \{[\s\S]*?\};/.exec(specDocument)?.[0];
  assert.ok(replaceOp, "the paragraph row must build its replace op");
  assert.match(replaceOp, /status: "confirmed"/);
  const readme = read("../../README.md").replace(/\s+/g, " ");
  assert.match(readme, /Rewriting its text with \*\*✏️\*\* still works and confirms it/);
  assert.doesNotMatch(readme, /keeps the badge until you switch it/);
});

test("no op the panel builds can stamp needs_input", () => {
  assert.match(types, /export type EditableStatus = Exclude<BlockStatus, "needs_input">;/);
  const editOp = /export interface EditOp \{[\s\S]*?\n\}/.exec(types)?.[0];
  assert.ok(editOp);
  assert.match(editOp, /status\?: EditableStatus;/);
  // Legacy content still renders: the display type keeps the status.
  assert.match(types, /export type BlockStatus = "confirmed" \| "assumed" \| "needs_input" \| "imported";/);
  assert.doesNotMatch(productionUi, /status:\s*"needs_input"/);
});

test("the open-items strip is the Leftover placeholders list", () => {
  assert.equal(PANEL_LABELS["open-items"], "Leftover placeholders");
  assert.deepEqual(
    foldedAttention({ review: 0, openItems: 1, waiting: 0, issues: 0 }, new Set()),
    ["1 leftover placeholder"],
  );
  const strip = /"open-items": openItems\.length > 0 && \([\s\S]*?\n {10}\),/.exec(artifact)?.[0];
  assert.ok(strip, "the strip still renders only while leftovers exist");
  assert.match(strip, /data-capability="document\.open-items"/);
  assert.match(strip, />\s*Leftover placeholders\s*</);
  assert.match(strip, /"needs-input block — "/);
  assert.match(strip, /"\[TBD\] marker — "/);
  assert.doesNotMatch(strip, /Open items|Unresolved provisions/);
  assert.doesNotMatch(artifact, /every\s+\[TBD\] stays tracked/);
});

test("the tour teaches gaps through Waiting on you, not an open-items chapter", () => {
  assert.doesNotMatch(tour, /id:\s*"open-items"/);
  assert.doesNotMatch(tour, /first-needs-input/);
  assert.doesNotMatch(tour, /SECTION \[TBD\]/);
  assert.doesNotMatch(tour, /red needs input,/);
  const step = /id:\s*"followups"[\s\S]*?\n {6}\}/.exec(tour)?.[0];
  assert.ok(step, "the Waiting on you step must exist");
  assert.match(step, /capabilities:\s*\["followups\.track", "document\.open-items"\]/);
  assert.match(step, /Leftover placeholders/);
  assert.match(step, /no \[TBD\], no placeholder, no note to you/);
  // The step it replaced was the tour's only "openItems" drawer: the
  // nonce plumbing went with it.
  assert.doesNotMatch(tour, /"openItems"/);
  assert.doesNotMatch(onboarding, /"openItems"/);
  assert.doesNotMatch(app, /openItems: 0/);
  assert.doesNotMatch(artifact, /drawerNonces\?\.openItems/);
  // Removing a step changes step order: stale resume records must drop.
  assert.match(tour, /export const TOUR_VERSION = 9;/);
});
