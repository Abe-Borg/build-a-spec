/**
 * Every edit the assistant makes carries a brief reason (owner rule,
 * 2026-10-07). The server keeps the trail per element on the document; the
 * panel shows it as a "why" chip on every edited element and prints the
 * newest reason under a block changed this turn. Helper tests plus source
 * pins (the leftoverPlaceholders.test.ts convention): no DOM, no network.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { editReasons, latestReason, reasonChipTitle } from "../src/lib/editReasons.ts";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");
const specDocument = read("../src/components/SpecDocument.tsx");
const types = read("../src/types.ts");

test("the trail is read per element, blanks dropped, newest last", () => {
  const byId = {
    "pt1.a1.p1": ["Playbook default for OH2 data halls", "  ", "User changed 0.20 to 0.30 gpm/sq ft"],
    "pt1.a1": ["User asked for a SUMMARY article"],
  };
  assert.deepEqual(editReasons(byId, "pt1.a1.p1"), [
    "Playbook default for OH2 data halls",
    "User changed 0.20 to 0.30 gpm/sq ft",
  ]);
  assert.equal(latestReason(editReasons(byId, "pt1.a1.p1")), "User changed 0.20 to 0.30 gpm/sq ft");
  assert.deepEqual(editReasons(byId, "pt1.a9"), []);
  assert.deepEqual(editReasons(undefined, "pt1.a1"), []);
  assert.deepEqual(editReasons({ "pt1.a1": "not a list" as unknown as string[] }, "pt1.a1"), []);
  assert.equal(latestReason([]), "");
});

test("the chip title is the one reason, or the trail numbered oldest first", () => {
  assert.equal(reasonChipTitle([]), "");
  assert.equal(reasonChipTitle(["User asked for it"]), "Why this changed: User asked for it");
  assert.equal(
    reasonChipTitle(["First draft", "Edition corrected to 2025"]),
    "Why this changed (oldest first):\n1. First draft\n2. Edition corrected to 2025",
  );
});

test("the document type carries the trail and an op carries its reason", () => {
  assert.match(types, /edit_reasons\?: Record<string, string\[\]>;/);
  // DocOp (a doc_patch echo) and EditOp (a manual edit) each take a reason.
  assert.equal([...types.matchAll(/^\s+reason\?: string;/gm)].length >= 2, true);
});

test("the panel shows the chip on provisions, article titles and the header", () => {
  // One context at the document root, read by each renderer.
  assert.match(specDocument, /const EditReasonsContext = createContext<Readonly<Record<string, string\[\]>>>\(\{\}\);/);
  assert.match(specDocument, /<EditReasonsContext\.Provider value=\{doc\.edit_reasons \?\? NO_REASONS\}>/);
  // A provision: chip beside the ◆ source chip, newest reason under a changed block.
  assert.match(specDocument, /<SourceChip itemId=\{p\.source_item_id\} lookup=\{sourceLookup\} \/>\s*<ReasonChip reasons=\{reasons\} \/>/);
  assert.match(specDocument, /<ChangedReason reasons=\{reasons\} changed=\{changedIds\.has\(p\.id\)\} \/>/);
  // An article title and the section header.
  assert.match(specDocument, /<span className="uppercase">\{title\}<\/span>\s*<ReasonChip reasons=\{reasons\} \/>/);
  assert.match(specDocument, /title not set<\/HeaderPlaceholder>\}\s*<ReasonChip reasons=\{reasons\} \/>/);
  // The chip is a hover title, not a control: no capability contract to join.
  const chip = /function ReasonChip\([\s\S]*?\n\}/.exec(specDocument)?.[0] ?? "";
  assert.match(chip, /title=\{reasonChipTitle\(reasons\)\}/);
  assert.doesNotMatch(chip, /onClick|data-capability/);
});
