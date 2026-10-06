/**
 * Developer tools' "Resource pressure" row: was an agent starved while it
 * ran? The formatter is tested directly, state by state, and its
 * vocabulary — the pressure kinds, the engines and the outcomes — is
 * pinned against `backend/resource_pressure.py`, read from the file (the
 * costChecks.test.ts idiom), so one added on either side cannot silently
 * drop out of the other. That Developer tools renders the row through the
 * helper is pinned at the source level (the modal has no DOM harness).
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import type {
  PressureAgent,
  PressureRun,
  PressureTotals,
  ResourcePressureSnapshot,
} from "../src/types.ts";
import {
  ENGINE_LABELS,
  OUTCOME_TEXT,
  PRESSURE_KIND_TEXT,
  resourcePressureLines,
} from "../src/lib/resourcePressure.ts";

const backend = readFileSync(
  new URL("../../backend/resource_pressure.py", import.meta.url),
  "utf8",
);
const modal = readFileSync(
  new URL("../src/components/DeveloperToolsModal.tsx", import.meta.url),
  "utf8",
);

function agent(overrides: Partial<PressureAgent> = {}): PressureAgent {
  return {
    kind: "dimension",
    outcome: "completed",
    error_kind: "",
    attempts: 1,
    started_at: 1,
    ended_at: 2,
    duration_ms: 1000,
    queued_ms: 3,
    warm_wait_outcome: "",
    warm_wait_ms: null,
    warm_lead: "",
    backoff_s: 0,
    sdk_retries: 0,
    sdk_sleep_s: 0,
    starved: false,
    pressure_counts: {},
    ...overrides,
  };
}

function run(overrides: Partial<PressureRun> = {}): PressureRun {
  const agents = overrides.agents ?? {};
  const starved = Object.values(agents).filter((a) => a.starved).length;
  return {
    engine: "research",
    run_id: "research-1",
    label: "round 1",
    status: "ended",
    started_at: 1,
    ended_at: 2,
    duration_ms: 1000,
    starved: starved > 0,
    starved_agents: starved,
    agents_total: Object.keys(agents).length,
    agents_running: 0,
    agents_completed: Object.keys(agents).length,
    agents_failed: 0,
    agents_cancelled: 0,
    agents_interrupted: 0,
    agents_dropped: 0,
    pressure_counts: {},
    backoff_s: 0,
    sdk_retries: 0,
    queued_max_ms: 0,
    batch_rounds: 0,
    batch_wait_ms: 0,
    events: [],
    events_dropped: 0,
    ...overrides,
    agents,
  };
}

function totals(overrides: Partial<PressureTotals> = {}): PressureTotals {
  return {
    runs_recorded: 0,
    runs_kept: 0,
    runs_ended: 0,
    runs_starved: 0,
    agents: 0,
    starved_agents: 0,
    backoff_s: 0,
    sdk_retries: 0,
    pressure_counts: {},
    ...overrides,
  };
}

function snapshot(overrides: Partial<ResourcePressureSnapshot> = {}): ResourcePressureSnapshot {
  return {
    schema_version: 1,
    sdk_retries_per_request: 2,
    queue_pressure_min_ms: 1000,
    max_runs_per_engine: 8,
    sdk_retry_observer: {
      attached: true,
      logger: "anthropic._base_client",
      listening: true,
      unattributed_retries: 0,
      unattributed_sleep_s: 0,
    },
    totals: { research: totals(), qc: totals(), chat: totals() },
    runs: [],
    ...overrides,
  };
}

test("nothing recorded yet, with the SDK line", () => {
  assert.deepEqual(resourcePressureLines(snapshot()), [
    "nothing recorded yet",
    "SDK retries: up to 2 per request before a failure reaches the app · observer listening",
  ]);
});

test("a clean session says so, engine by engine, in the backend's order", () => {
  const lines = resourcePressureLines(
    snapshot({
      totals: {
        research: totals({ runs_recorded: 2, runs_ended: 2, agents: 8 }),
        qc: totals({ runs_recorded: 1, runs_ended: 1, agents: 14 }),
        chat: totals({ runs_recorded: 12, runs_ended: 12, agents: 12 }),
      },
    }),
  );
  assert.equal(
    lines[0],
    "No agent was starved this session — Research 8 areas over 2 runs · Final QC 14 calls over 1 run · Chat 12 turns",
  );
  assert.equal(lines.length, 2);
});

test("a starved run names its agents and what each one met", () => {
  const starvedRun = run({
    label: "round 2",
    backoff_s: 15,
    pressure_counts: { rate_limit: 2, search_ceiling: 1, warm_wait_timeout: 1 },
    agents: {
      governing_codes: agent({
        starved: true,
        attempts: 3,
        backoff_s: 15,
        pressure_counts: { rate_limit: 2, search_ceiling: 1 },
      }),
      ahj_requirements: agent({
        starved: true,
        warm_wait_outcome: "timeout",
        warm_wait_ms: 45_000,
        warm_lead: "governing_codes",
        pressure_counts: { warm_wait_timeout: 1 },
      }),
      client_standards: agent({ warm_wait_outcome: "warm", warm_wait_ms: 1200 }),
    },
  });
  const lines = resourcePressureLines(
    snapshot({
      totals: {
        research: totals({ runs_recorded: 2, runs_ended: 2, runs_starved: 1, agents: 7, starved_agents: 2 }),
        qc: totals(),
        chat: totals({ runs_recorded: 3, runs_ended: 3, agents: 3 }),
      },
      runs: [starvedRun],
    }),
  );
  assert.deepEqual(lines, [
    "Starved this session — Research 2 of 7 areas over 2 runs · Chat 3 turns",
    "Research round 2: 2 of 3 areas starved — governing_codes — rate limited ×2 (backoff 15 s), hit the search ceiling; ahj_requirements — waited the whole bound for its lead (45 s)",
    "SDK retries: up to 2 per request before a failure reaches the app · observer listening",
  ]);
});

test("an agent that did not complete says how it ended, and a running run says so", () => {
  const lines = resourcePressureLines(
    snapshot({
      totals: {
        research: totals(),
        qc: totals({ runs_recorded: 1 }),
        chat: totals({ runs_recorded: 1, runs_ended: 1, agents: 1, starved_agents: 1 }),
      },
      runs: [
        run({
          engine: "qc",
          label: "Final QC",
          status: "running",
          agents: {
            "seat-0-0": agent({
              kind: "verifier",
              starved: true,
              outcome: "failed",
              error_kind: "connection",
              pressure_counts: { batch_expired: 1 },
            }),
            "seat-0-1": agent({ kind: "verifier", outcome: "running" }),
          },
        }),
        run({
          engine: "chat",
          label: "turn",
          agents: {
            turn: agent({
              kind: "turn",
              starved: true,
              outcome: "failed",
              error_kind: "rate_limit",
              pressure_counts: { rate_limit: 1 },
            }),
          },
        }),
      ],
    }),
  );
  assert.equal(lines[0], "Starved this session — Final QC 0 calls over 1 run · Chat 1 of 1 turns");
  assert.equal(
    lines[1],
    "Final QC Final QC, running: 1 of 2 calls starved — seat-0-0 — expired in the batch before it ran · failed (connection)",
  );
  assert.equal(
    lines[2],
    "Chat turn: 1 of 1 turns starved — turn — rate limited · failed (rate_limit)",
  );
});

test("long lists are bounded: four agents a line, six runs a row", () => {
  const agents: Record<string, PressureAgent> = {};
  for (let i = 0; i < 6; i += 1) {
    agents[`seat-0-${i}`] = agent({ kind: "verifier", starved: true, pressure_counts: { queued: 1 }, queued_ms: 2500 });
  }
  const runs = Array.from({ length: 8 }, (_, i) =>
    run({ engine: "qc", label: `run ${i}`, run_id: `qc-${i}`, agents }),
  );
  const lines = resourcePressureLines(
    snapshot({
      totals: { research: totals(), qc: totals({ runs_recorded: 8, runs_ended: 8, agents: 48, starved_agents: 48 }), chat: totals() },
      runs,
    }),
  );
  assert.equal(lines.length, 1 + 6 + 1 + 1);
  assert.match(lines[1], /^Final QC run 0: 6 of 6 calls starved — seat-0-0 — waited for a worker \(2\.5 s\); .*; \+2 more$/);
  assert.equal(lines[7], "+2 more starved runs in the snapshot JSON");
});

test("the SDK line discloses a muted observer and stray retries", () => {
  const lines = resourcePressureLines(
    snapshot({
      sdk_retry_observer: {
        attached: true,
        logger: "anthropic._base_client",
        listening: false,
        unattributed_retries: 3,
        unattributed_sleep_s: 4.5,
      },
    }),
  );
  assert.equal(
    lines[1],
    "SDK retries: up to 2 per request before a failure reaches the app · observer muted (log level above INFO) · 3 seen outside any agent (4.5 s)",
  );
});

test("every pressure kind the backend can record has words here, and no other", () => {
  const kinds = [...backend.matchAll(/^KIND_[A-Z_]+ = "([a-z_]+)"/gm)].map((m) => m[1]);
  assert.ok(kinds.length > 0, "no KIND_* constants found in backend/resource_pressure.py");
  assert.deepEqual(Object.keys(PRESSURE_KIND_TEXT).sort(), [...kinds].sort());
});

test("every outcome the backend can set has words here, and no other", () => {
  const outcomes = [...backend.matchAll(/^OUTCOME_[A-Z_]+ = "([a-z_]+)"/gm)].map((m) => m[1]);
  assert.ok(outcomes.length > 0, "no OUTCOME_* constants found in backend/resource_pressure.py");
  assert.deepEqual(Object.keys(OUTCOME_TEXT).sort(), [...outcomes].sort());
});

test("every engine the backend records has a name here, in its order", () => {
  const values = new Map(
    [...backend.matchAll(/^(ENGINE_[A-Z_]+) = "([a-z_]+)"/gm)].map((m) => [m[1], m[2]]),
  );
  const order = backend.match(/^ENGINES = \(([^)]*)\)/m);
  assert.ok(order, "ENGINES not found in backend/resource_pressure.py");
  const engines = order[1]
    .split(",")
    .map((name) => name.trim())
    .filter(Boolean)
    .map((name) => values.get(name));
  assert.deepEqual(
    ENGINE_LABELS.map(([id]) => id),
    engines,
  );
});

test("a missing, older or malformed snapshot renders 'not reported' and never throws", () => {
  for (const value of [undefined, null, {}, { totals: {} }, { runs: [] }, { totals: [], runs: {} }]) {
    assert.deepEqual(
      resourcePressureLines(value as ResourcePressureSnapshot | undefined),
      ["not reported"],
    );
  }
  // Unknown kinds, engines and outcomes from a newer backend still render.
  const newer = resourcePressureLines(
    snapshot({
      totals: { research: totals(), qc: totals(), chat: totals(), batch: totals({ runs_recorded: 1, agents: 2, starved_agents: 1 }) },
      runs: [
        run({
          engine: "batch",
          label: "job 1",
          agents: { a: agent({ starved: true, outcome: "paused", pressure_counts: { not_yet_named: 2 } }) },
        }),
      ],
    } as unknown as ResourcePressureSnapshot),
  );
  assert.equal(newer[0], "Starved this session — batch 1 of 2 agents over 1 run");
  assert.equal(newer[1], "batch job 1: 1 of 1 agents starved — a — met not_yet_named ×2 · paused");
  // Wrong types inside a run never throw.
  const wrongTypes = resourcePressureLines({
    totals: { research: { runs_recorded: "2", agents: null } },
    runs: [{ engine: 7, starved: true, agents: "nope", starved_agents: "1" }],
  } as unknown as ResourcePressureSnapshot);
  assert.equal(wrongTypes[0], "nothing recorded yet");
  assert.equal(wrongTypes[1], "7 : 0 of 0 agents starved");
});

test("Developer tools renders the row through the helper", () => {
  assert.match(modal, /from "\.\.\/lib\/resourcePressure"/);
  assert.match(modal, /resourcePressureLines\(snapshot\?\.resource_pressure\)/);
  assert.match(modal, /name=\{index === 0 \? "Resource pressure" : ""\}/);
});
