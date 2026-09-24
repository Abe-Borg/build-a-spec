/**
 * Developer tools' "Cost self-checks" row (Tier 1 finish, CT-2): what the
 * runtime cost checks decided about the continuation tail, per engine.
 *
 * The formatter is tested directly, state by state. Its reason vocabulary
 * and its engines are pinned against `backend/cost_checks.py`, read from the
 * file (the contextSizes.test.ts idiom), so a reason or an engine added on
 * one side cannot silently drop out of the other. That Developer tools
 * renders the row through the helper is pinned at the source level (the
 * modal has no DOM harness — the chatPerf.test.ts idiom).
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import type { ContinuationTailCheck, CostChecksSnapshot } from "../src/types.ts";
import {
  CHECK_REASON_TEXT,
  TAIL_ENGINE_LABELS,
  costCheckLines,
} from "../src/lib/costChecks.ts";

const backend = readFileSync(
  new URL("../../backend/cost_checks.py", import.meta.url),
  "utf8",
);
const modal = readFileSync(
  new URL("../src/components/DeveloperToolsModal.tsx", import.meta.url),
  "utf8",
);

function engine(overrides: Partial<ContinuationTailCheck> = {}): ContinuationTailCheck {
  return {
    setting_on: true,
    enabled: true,
    reason: "",
    detail: "",
    since: null,
    measured: 0,
    exact: 0,
    bound: 0,
    unmeasured: 0,
    saving_usd: 0,
    last_observed_at: null,
    ...overrides,
  };
}

function checks(
  research: Partial<ContinuationTailCheck> = {},
  qc: Partial<ContinuationTailCheck> = {},
): CostChecksSnapshot {
  return { continuation_tail: { research: engine(research), qc: engine(qc) } };
}

test("the switch off in settings is said once, for both engines", () => {
  assert.deepEqual(costCheckLines(checks({ setting_on: false }, { setting_on: false })), [
    "Continuation tail: switched off in settings",
  ]);
});

test("on and nothing measured yet", () => {
  assert.deepEqual(costCheckLines(checks()), [
    "Continuation tail, research: on · nothing measured yet",
    "Continuation tail, Final QC: on · nothing measured yet",
  ]);
});

test("on and measured: the counts, the estimate, and what could not be measured", () => {
  const [research, qc] = costCheckLines(
    checks(
      { measured: 7, exact: 2, bound: 5, unmeasured: 3, saving_usd: 0.31 },
      { unmeasured: 4 },
    ),
  );
  assert.equal(
    research,
    "Continuation tail, research: on · 7 measured (2 exact, 5 bound), est. saving $0.3100 · 3 not measurable",
  );
  assert.equal(qc, "Continuation tail, Final QC: on · 4 not measurable");
});

test("rejected: off for this session, in plain words", () => {
  const [, qc] = costCheckLines(
    checks({}, { enabled: false, reason: "rejected", detail: "BadRequestError: …" }),
  );
  assert.equal(qc, "Continuation tail, Final QC: off for this session — the provider rejected it");
});

test("unprofitable: off for this session, with the loss that proved it", () => {
  const [research] = costCheckLines(
    checks({
      enabled: false,
      reason: "unprofitable",
      measured: 6,
      bound: 6,
      saving_usd: -0.2892,
    }),
  );
  assert.equal(
    research,
    "Continuation tail, research: off for this session — it had cost more than it saved · 6 measured (0 exact, 6 bound), est. loss $0.2892",
  );
  // Requests already in flight when it latched still report back, so the
  // running estimate can end up a saving; the reason stays past tense.
  const [later] = costCheckLines(
    checks({ enabled: false, reason: "unprofitable", measured: 9, bound: 9, saving_usd: 0.01 }),
  );
  assert.equal(
    later,
    "Continuation tail, research: off for this session — it had cost more than it saved · 9 measured (0 exact, 9 bound), est. saving $0.0100",
  );
});

test("every reason the backend can set has words here, and no other", () => {
  const reasons = [...backend.matchAll(/^REASON_[A-Z_]+ = "([a-z_]+)"/gm)].map((m) => m[1]);
  assert.ok(reasons.length > 0, "no REASON_* constants found in backend/cost_checks.py");
  assert.deepEqual(Object.keys(CHECK_REASON_TEXT).sort(), [...reasons].sort());
});

test("every engine the backend checks has a name here, in its order", () => {
  const values = new Map(
    [...backend.matchAll(/^(ENGINE_[A-Z_]+) = "([a-z_]+)"/gm)].map((m) => [m[1], m[2]]),
  );
  const order = backend.match(/^TAIL_ENGINES = \(([^)]*)\)/m);
  assert.ok(order, "TAIL_ENGINES not found in backend/cost_checks.py");
  const engines = order[1]
    .split(",")
    .map((name) => name.trim())
    .filter(Boolean)
    .map((name) => values.get(name));
  assert.deepEqual(
    TAIL_ENGINE_LABELS.map(([id]) => id),
    engines,
  );
});

test("a missing, older or malformed snapshot renders 'not reported' and never throws", () => {
  for (const value of [undefined, null, {}, { continuation_tail: {} }, { continuation_tail: [] }]) {
    assert.deepEqual(costCheckLines(value as CostChecksSnapshot | undefined), ["not reported"]);
  }
  assert.deepEqual(
    costCheckLines({ continuation_tail: { research: "garbage" } } as unknown as CostChecksSnapshot),
    ["not reported"],
  );
  // CT-1's backend reported the latch but no counts.
  const older = {
    continuation_tail: {
      research: { setting_on: true, enabled: true, reason: "", detail: "", since: null },
      qc: { setting_on: true, enabled: false, reason: "rejected", detail: "", since: 1 },
    },
  } as CostChecksSnapshot;
  assert.deepEqual(costCheckLines(older), [
    "Continuation tail, research: on · measurement not reported",
    "Continuation tail, Final QC: off for this session — the provider rejected it · measurement not reported",
  ]);
  // A reason or an engine this build has never heard of still renders.
  const newer = {
    continuation_tail: {
      research: engine({ enabled: false, reason: "not_yet_named" }),
      chat: engine(),
    },
  } as CostChecksSnapshot;
  assert.deepEqual(costCheckLines(newer), [
    "Continuation tail, research: off for this session — switched off (not_yet_named)",
    "Continuation tail, chat: on · nothing measured yet",
  ]);
  const wrongTypes = {
    continuation_tail: { qc: { setting_on: "yes", enabled: "no", reason: 7, measured: "3" } },
  } as unknown as CostChecksSnapshot;
  assert.deepEqual(costCheckLines(wrongTypes), [
    "Continuation tail, Final QC: on · measurement not reported",
  ]);
});

test("Developer tools renders the row through the helper", () => {
  assert.match(modal, /from "\.\.\/lib\/costChecks"/);
  assert.match(modal, /costCheckLines\(snapshot\.cost_checks\)/);
  assert.match(modal, /name=\{index === 0 \? "Cost self-checks" : ""\}/);
});
