/**
 * Developer tools' "Cost self-checks" row (Tier 1 finish, CT-2 and WL-1):
 * what the runtime cost checks decided about the continuation tail, per
 * engine, and about the warm lead.
 *
 * The formatter is tested directly, state by state. Its reason vocabulary,
 * its engines and the warm lead's verdicts are pinned against
 * `backend/cost_checks.py`, read from the file (the contextSizes.test.ts
 * idiom), so one added on one side cannot silently drop out of the other.
 * That Developer tools renders the row through the helper is pinned at the
 * source level (the modal has no DOM harness — the chatPerf.test.ts idiom).
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import type {
  ContinuationTailCheck,
  CostChecksSnapshot,
  WarmLeadCheck,
  WarmLeadLineageCheck,
} from "../src/types.ts";
import {
  CHECK_REASON_TEXT,
  TAIL_ENGINE_LABELS,
  WARM_LEAD_VERDICT_TEXT,
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

// ---------------------------------------------------------------------------
// The warm lead (WL-1)
// ---------------------------------------------------------------------------

const TAIL_LINES = [
  "Continuation tail, research: on · nothing measured yet",
  "Continuation tail, Final QC: on · nothing measured yet",
];

function lineage(overrides: Partial<WarmLeadLineageCheck> = {}): WarmLeadLineageCheck {
  return {
    kind: "no-web",
    seats: 24,
    measured: 22,
    unmeasured: 1,
    read_share: 1,
    prefix_tokens: 40000,
    lead_cost_usd: 0.40028,
    break_even_read_share: 0.7118,
    verdict: "kept",
    ...overrides,
  };
}

function warmLead(overrides: Partial<WarmLeadCheck> = {}): WarmLeadCheck {
  return {
    setting_on: true,
    enabled: true,
    reason: "",
    detail: "",
    since: null,
    last_check: null,
    ...overrides,
  };
}

function withWarmLead(overrides: Partial<WarmLeadCheck> = {}): CostChecksSnapshot {
  return { ...checks(), warm_lead: warmLead(overrides) };
}

function warmLine(overrides: Partial<WarmLeadCheck> = {}): string {
  const lines = costCheckLines(withWarmLead(overrides));
  assert.deepEqual(lines.slice(0, 2), TAIL_LINES);
  assert.equal(lines.length, 3);
  return lines[2];
}

test("the warm lead switched off in settings is said once, after the tail", () => {
  assert.equal(warmLine({ setting_on: false }), "Warm lead: switched off in settings");
});

test("the warm lead on, before any phase was checked", () => {
  assert.equal(warmLine(), "Warm lead (Final QC): on · nothing checked yet");
});

test("the warm lead on, with the last check's numbers in plain words", () => {
  assert.equal(
    warmLine({ last_check: { at: 1, lineages: [lineage()] } }),
    "Warm lead (Final QC): on · last check: 24 no-web seats, 22 measured, 100% read the shared copy; the lead pays when the batch alone would read under 71%",
  );
});

test("each group the last check judged, in its own words", () => {
  const line = warmLine({
    last_check: {
      at: 1,
      lineages: [
        lineage({ kind: "web-tooled", seats: 10, measured: 0, unmeasured: 9, verdict: "too_few", read_share: null, break_even_read_share: null }),
        lineage({ seats: 12, verdict: "not_warm", read_share: 0 }),
        lineage({ seats: 30, measured: 29, read_share: 0.9655, break_even_read_share: null }),
      ],
    },
  });
  assert.equal(
    line,
    "Warm lead (Final QC): on · last check: " +
      "10 web-tooled seats, 0 measured — too few to judge · " +
      "12 no-web seats — not judged: the batch went out before the lead's copy was ready · " +
      "30 no-web seats, 29 measured, 97% read the shared copy",
  );
});

test("the batch did not read the lead's copy: off, with the share that proved it", () => {
  assert.equal(
    warmLine({
      enabled: false,
      reason: "not_read",
      detail: "The batch read the shared prefix on 3 of 25 …",
      since: 1,
      last_check: {
        at: 1,
        lineages: [lineage({ verdict: "not_read", read_share: 0.12, break_even_read_share: -0.05 })],
      },
    }),
    "Warm lead (Final QC): off for this session — the batch did not read the lead's copy (12%)",
  );
});

test("an unprofitable lead: off, with what it cost", () => {
  assert.equal(
    warmLine({
      enabled: false,
      reason: "unprofitable",
      since: 1,
      last_check: {
        at: 1,
        lineages: [
          lineage({ verdict: "kept" }),
          lineage({ verdict: "unprofitable", lead_cost_usd: 1.45628, break_even_read_share: -0.1224 }),
        ],
      },
    }),
    "Warm lead (Final QC): off for this session — it had cost more than it saved (lead cost $1.4563)",
  );
  // Switched off with no check to show for it (the latch set directly, or a
  // later check that no longer names the group): the reason alone.
  assert.equal(
    warmLine({ enabled: false, reason: "not_read", last_check: null }),
    "Warm lead (Final QC): off for this session — the batch did not read the lead's copy",
  );
});

test("a warm-lead block that is malformed, or from an older backend, never throws", () => {
  // No block at all (CT-2's backend): no warm-lead line.
  assert.deepEqual(costCheckLines(checks()), TAIL_LINES);
  assert.deepEqual(costCheckLines({ ...checks(), warm_lead: "garbage" } as unknown as CostChecksSnapshot), [
    ...TAIL_LINES,
    "Warm lead: not reported",
  ]);
  assert.equal(
    warmLine({ last_check: { at: 1, lineages: [] } }),
    "Warm lead (Final QC): on · last check not reported",
  );
  assert.equal(
    warmLine({ last_check: "garbage" as unknown as WarmLeadCheck["last_check"] }),
    "Warm lead (Final QC): on · last check not reported",
  );
  assert.equal(
    warmLine({
      last_check: {
        at: 1,
        lineages: ["garbage", lineage({ verdict: "a_new_verdict" })] as unknown as WarmLeadLineageCheck[],
      },
    }),
    "Warm lead (Final QC): on · last check: not reported · 24 no-web seats, 22 measured, 100% read the shared copy — a_new_verdict",
  );
  assert.equal(
    warmLine({ enabled: false, reason: "a_new_reason" }),
    "Warm lead (Final QC): off for this session — switched off (a_new_reason)",
  );
  // A backend that reports the warm lead but no continuation tail.
  assert.deepEqual(costCheckLines({ warm_lead: warmLead() }), [
    "Continuation tail: not reported",
    "Warm lead (Final QC): on · nothing checked yet",
  ]);
});

test("every verdict the warm lead's check can reach has words here, and no other", () => {
  const constants = new Map(
    [...backend.matchAll(/^((?:VERDICT|REASON)_[A-Z_]+) = "([a-z_]+)"/gm)].map((m) => [m[1], m[2]]),
  );
  const tuple = backend.match(/^WARM_LEAD_VERDICTS = \(([^)]*)\)/m);
  assert.ok(tuple, "WARM_LEAD_VERDICTS not found in backend/cost_checks.py");
  const verdicts = tuple[1]
    .split(",")
    .map((name) => name.trim())
    .filter(Boolean)
    .map((name) => constants.get(name));
  assert.ok(verdicts.length > 0 && verdicts.every(Boolean), `unresolved verdicts: ${tuple[1]}`);
  // Each has words: its own phrase, or the reason it switches the lead off.
  for (const verdict of verdicts) {
    assert.ok(
      verdict! in WARM_LEAD_VERDICT_TEXT || verdict! in CHECK_REASON_TEXT,
      `no words for the verdict ${verdict}`,
    );
  }
  // And no phrase here names a verdict the backend cannot reach.
  for (const verdict of Object.keys(WARM_LEAD_VERDICT_TEXT)) {
    assert.ok(verdicts.includes(verdict), `${verdict} is not a backend verdict`);
  }
});
