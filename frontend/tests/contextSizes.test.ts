/**
 * Developer tools' "Context makeup" row: what one turn's PROJECT CONTEXT
 * carried (Project workspace Phase 5A).
 *
 * The owner reads this row to decide whether Phase 5's relevance trim is worth
 * building (the research block routinely past ~40k estimated tokens), so the
 * research figure must always be there and must say when the cap trimmed it.
 * The formatter is tested directly; that the label table covers every block
 * the backend measures is pinned against `conversation.CONTEXT_SIZE_KEYS`, and
 * that Developer tools renders the row through the helper is pinned at the
 * source level (the modal has no DOM harness — the qcSessionCost.test.ts
 * idiom). Expected numbers are formatted with `toLocaleString()` too, so the
 * tests hold whatever locale the runner uses.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import type { ContextSizes } from "../src/types.ts";
import { CONTEXT_BLOCK_LABELS, contextMakeup } from "../src/lib/contextSizes.ts";

const conversation = readFileSync(
  new URL("../../backend/llm/conversation.py", import.meta.url),
  "utf8",
);
const modal = readFileSync(
  new URL("../src/components/DeveloperToolsModal.tsx", import.meta.url),
  "utf8",
);

function sizes(overrides: Partial<ContextSizes> = {}): ContextSizes {
  return {
    research: 0,
    research_dropped_items: 0,
    facts: 0,
    sections: 0,
    references: 0,
    document: 0,
    lint: 0,
    open_items: 0,
    qc_review: 0,
    other: 0,
    total: 0,
    ...overrides,
  };
}

const n = (value: number) => value.toLocaleString();

test("the total leads and research is always stated next", () => {
  const row = contextMakeup(
    sizes({ research: 38_214, document: 90_000, other: 1_406, total: 129_620 }),
  );
  const parts = row.split(" · ");
  assert.equal(parts[0], `~${n(129_620)} tokens (est.)`);
  // Research comes first even when another block is bigger: it is the reading.
  assert.equal(parts[1], `research ${n(38_214)}`);
  assert.equal(parts[2], `document ${n(90_000)}`);
});

test("a trimmed research block says how many findings the cap left out", () => {
  const row = contextMakeup(
    sizes({ research: 100_000, research_dropped_items: 1_200, total: 101_000 }),
  );
  assert.match(row, new RegExp(`research ${n(100_000)} \\(${n(1_200)} findings trimmed at the cap\\)`));
  // An untrimmed block claims no trim.
  assert.doesNotMatch(contextMakeup(sizes({ research: 5_000, total: 6_000 })), /trimmed/);
});

test("a session with no research profile says so rather than showing 0", () => {
  const row = contextMakeup(sizes({ document: 800, total: 1_000, other: 200 }));
  assert.equal(row.split(" · ")[1], "no research profile");
});

test("the other blocks follow largest first, empty ones are left out, and other trails", () => {
  const row = contextMakeup(
    sizes({
      research: 10,
      lint: 1_203,
      qc_review: 2_101,
      document: 21_402,
      facts: 812,
      references: 0,
      other: 5_000,
      total: 30_528,
    }),
  );
  assert.deepEqual(row.split(" · ").slice(2), [
    `document ${n(21_402)}`,
    `Final QC review ${n(2_101)}`,
    `lint ${n(1_203)}`,
    `facts ${n(812)}`,
    `other ${n(5_000)}`,
  ]);
  assert.doesNotMatch(row, /reference stubs/);
});

test("every block the backend measures has a label here", () => {
  const match = conversation.match(
    /CONTEXT_SIZE_KEYS: tuple\[str, \.\.\.\] = \(([\s\S]*?)\)/,
  );
  assert.ok(match, "CONTEXT_SIZE_KEYS not found in backend/llm/conversation.py");
  const backend = [...match[1].matchAll(/"([a-z_]+)"/g)].map((m) => m[1]);
  // research leads, other trails, and neither the count nor the total is a block.
  const special = new Set(["research", "research_dropped_items", "other", "total"]);
  assert.deepEqual(
    CONTEXT_BLOCK_LABELS.map(([key]) => key),
    backend.filter((key) => !special.has(key)),
  );
});

test("Developer tools renders the row through the helper", () => {
  assert.match(modal, /name="Context makeup"/);
  assert.match(modal, /contextMakeup\(sess\.last_context_sizes\)/);
  assert.match(modal, /from "\.\.\/lib\/contextSizes"/);
});
