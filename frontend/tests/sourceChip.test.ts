import assert from "node:assert/strict";
import { test } from "node:test";

import { sourceChipTitle, sourceKind } from "../src/lib/sourceChip.ts";

test("the chip decides the origin kind by id prefix, reference before research", () => {
  // `r-` is a prefix of `ref-`: the reference test has to run first.
  assert.equal(sourceKind("ref-1"), "reference");
  assert.equal(sourceKind("r-9f3a"), "research");
  assert.equal(sourceKind("pf-4"), "fact");
  assert.equal(sourceKind("anything-else"), "research");
});

test("a resolvable origin shows its detail and an unresolvable one names the kind and id", () => {
  const lookup = new Map([
    ["ref-1", "Owner fire protection standard (PDF)"],
    ["pf-4", "Data halls are Ordinary Hazard Group 2."],
    ["r-9f3a", "VCC 2021 governs. — https://example.test/vcc"],
  ]);
  assert.equal(sourceChipTitle("ref-1", lookup), "Reference document: Owner fire protection standard (PDF)");
  assert.equal(sourceChipTitle("pf-4", lookup), "Project fact: Data halls are Ordinary Hazard Group 2.");
  assert.equal(sourceChipTitle("r-9f3a", lookup), "Research: VCC 2021 governs. — https://example.test/vcc");
  const empty = new Map<string, string>();
  assert.equal(sourceChipTitle("ref-2", empty), "Reference document ref-2 (no longer attached)");
  assert.equal(sourceChipTitle("pf-7", empty), "Project fact pf-7 (no longer recorded)");
  assert.equal(sourceChipTitle("r-0", empty), "Research item r-0 (re-run research to see details)");
});
