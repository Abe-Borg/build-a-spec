/**
 * The Final QC drawer reports the whole session's QC spend.
 *
 * The verification phase is submitted through the Message Batches API by
 * default and meters into its own `qc_batched` bucket, because those tokens
 * are billed at half price and one bucket can only carry one rate. That phase
 * is roughly nine calls in ten, so the drawer's single-category read reported
 * a small fraction of what a pass had cost.
 *
 * The pure sum is tested directly; the drawer has no DOM harness, so that it
 * actually calls the shared helper is pinned at the source level (the
 * chatPerf.test.ts / strandedState.test.ts idiom).
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import type { UsageSummary } from "../src/types.ts";
import { QC_SPEND_CATEGORIES, qcSessionCost } from "../src/lib/qcReport.ts";

const here = dirname(fileURLToPath(import.meta.url));
const drawer = readFileSync(
  join(here, "..", "src", "components", "QCDrawer.tsx"),
  "utf8",
);

function usage(byCategory: Record<string, number>): UsageSummary {
  return {
    categories: {},
    totals: {},
    turns: 0,
    estimated_cost_usd: {
      by_category: byCategory,
      total: Object.values(byCategory).reduce((sum, n) => sum + n, 0),
    },
    cache_saved_usd: 0,
  };
}

test("the streamed phase alone is reported", () => {
  assert.equal(qcSessionCost(usage({ qc: 1.25 })), 1.25);
});

test("the batched phase alone is reported", () => {
  // The regression: batching is the default, so a session whose only QC spend
  // is batched used to display nothing at all.
  assert.equal(qcSessionCost(usage({ qc_batched: 3.5 })), 3.5);
});

test("both QC phases are summed, and nothing else is", () => {
  const spend = usage({
    qc: 1.5,
    qc_batched: 2.25,
    interview: 10,
    research: 20,
    audit: 30,
    template: 40,
  });
  assert.equal(qcSessionCost(spend), 3.75);
});

test("an empty or absent meter is zero, never NaN", () => {
  for (const value of [null, undefined]) {
    const cost = qcSessionCost(value);
    assert.equal(cost, 0);
    assert.ok(Number.isFinite(cost));
  }
  assert.equal(qcSessionCost(usage({})), 0);
  assert.equal(qcSessionCost(usage({ qc: 0, qc_batched: 0 })), 0);
});

test("a malformed entry cannot render as NaN", () => {
  // The payload is JSON from the app's own backend, but a surface that
  // formats with toFixed turns one bad number into "$NaN" for the session.
  const broken = usage({ qc: 2 });
  const byCategory = broken.estimated_cost_usd.by_category as Record<
    string,
    unknown
  >;
  byCategory.qc_batched = "1.50";
  assert.equal(qcSessionCost(broken), 2);

  byCategory.qc_batched = Number.NaN;
  assert.equal(qcSessionCost(broken), 2);

  const missingBlock = { estimated_cost_usd: undefined } as unknown as UsageSummary;
  assert.equal(qcSessionCost(missingBlock), 0);
});

test("the category list is exactly the two QC meter buckets", () => {
  // `usage_ledger._category_models` names these two for Final QC; interview,
  // research, audit and template are other work and must not be folded in.
  assert.deepEqual([...QC_SPEND_CATEGORIES], ["qc", "qc_batched"]);
});

test("the drawer reads the shared helper, not one category", () => {
  assert.match(drawer, /const observedCost = qcSessionCost\(usage\)/);
  // The defect, in the exact shape it shipped in.
  assert.doesNotMatch(drawer, /by_category\.qc\b/);
  assert.doesNotMatch(drawer, /by_category\[/);

  // One derivation feeding both surfaces: the drawer's own line and the
  // launch confirmation must never disagree about what a pass has cost.
  const uses = drawer.match(/observedCost/g) ?? [];
  assert.ok(uses.length >= 4, "both cost surfaces read the same value");
});
