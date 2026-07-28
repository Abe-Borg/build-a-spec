import assert from "node:assert/strict";
import test from "node:test";

import {
  RECENT_CAP,
  foldAgentDetail,
  foldResearchBoard,
  labelFromId,
  trimUrl,
} from "../src/lib/researchAgents.ts";
import type { ResearchEvent } from "../src/types.ts";

/** Build a merged event log the way the app holds one: seq-ascending, every
 *  event stamped with ts and round (overridable per entry). */
function log(
  entries: Array<[string, Partial<ResearchEvent>]>,
): ResearchEvent[] {
  return entries.map(([type, fields], i) => ({
    seq: i,
    ts: `12:00:${String(i).padStart(2, "0")}`,
    type,
    round: 1,
    ...fields,
  }));
}

const ROSTER: [string, Partial<ResearchEvent>] = [
  "research_started",
  {
    project: "Fire suppression · Data Hall 1",
    dimensions: ["governing_codes", "ahj_requirements"],
    dimension_titles: {
      governing_codes: "Governing building and fire codes",
      ahj_requirements: "AHJ requirements",
    },
  },
];

const LONG_QUERY =
  "a deliberately long query that the compact card would truncate but the " +
  "modal must keep byte-for-byte";

test("foldAgentDetail maps every event kind with full text and skips other dimensions", () => {
  const events = log([
    ROSTER,
    [
      "dimension_started",
      {
        dimension_id: "governing_codes",
        title: "Governing building and fire codes",
        max_searches: 24,
        max_fetches: 8,
      },
    ],
    ["dimension_activity", { dimension_id: "governing_codes", kind: "thinking" }],
    [
      "dimension_search",
      { dimension_id: "ahj_requirements", query: "other dimension's query" },
    ],
    ["dimension_search", { dimension_id: "governing_codes", query: LONG_QUERY }],
    [
      "dimension_fetch",
      {
        dimension_id: "governing_codes",
        url: "https://www.example.com/codes/nfpa-13/",
      },
    ],
    [
      "dimension_retry",
      {
        dimension_id: "governing_codes",
        attempt: 2,
        max_attempts: 3,
        reason: "rate_limit",
        backoff_s: 8,
      },
    ],
    [
      "dimension_complete",
      {
        dimension_id: "governing_codes",
        title: "Governing building and fire codes",
        item_count: 5,
        grounded_count: 4,
        web_search_requests: 9,
        web_fetch_requests: 3,
      },
    ],
  ]);
  const detail = foldAgentDetail(events, "governing_codes");
  assert.deepEqual(
    detail.timeline.map((t) => t.kind),
    ["started", "activity", "search", "fetch", "retry", "done"],
  );
  // Every row keeps its event's seq + ts (the feed's timestamps).
  for (const row of detail.timeline) {
    assert.equal(typeof row.seq, "number");
    assert.match(row.ts, /^12:00:\d\d$/);
  }
  const [startedRow, activity, search, fetch, retry, done] = detail.timeline;
  assert.deepEqual(startedRow, {
    seq: 1,
    ts: "12:00:01",
    kind: "started",
    maxSearches: 24,
    maxFetches: 8,
  });
  assert.equal(activity.kind === "activity" && activity.activity, "thinking");
  // The whole point of the modal: nothing is truncated.
  assert.equal(search.kind === "search" && search.query, LONG_QUERY);
  assert.equal(
    fetch.kind === "fetch" && fetch.url,
    "https://www.example.com/codes/nfpa-13/",
  );
  assert.deepEqual(retry, {
    seq: 6,
    ts: "12:00:06",
    kind: "retry",
    attempt: 2,
    max: 3,
    reason: "rate_limit",
    backoffS: 8,
  });
  assert.deepEqual(done, {
    seq: 7,
    ts: "12:00:07",
    kind: "done",
    itemCount: 5,
    groundedCount: 4,
    billedSearches: 9,
    billedFetches: 3,
  });
  // The other dimension's query never leaks in.
  assert.ok(
    detail.timeline.every(
      (t) => !(t.kind === "search" && t.query.includes("other dimension")),
    ),
  );
  // Budgets latch onto the summary too.
  assert.equal(detail.maxSearches, 24);
  assert.equal(detail.maxFetches, 8);
});

test("foldAgentDetail maps a failed dimension and tracks the latest round", () => {
  const events = log([
    ROSTER,
    [
      "dimension_started",
      { dimension_id: "governing_codes", max_searches: 24, max_fetches: 8 },
    ],
    [
      "dimension_failed",
      { dimension_id: "governing_codes", error: "boom", round: 2 },
    ],
  ]);
  const detail = foldAgentDetail(events, "governing_codes");
  const last = detail.timeline[detail.timeline.length - 1];
  assert.equal(last.kind === "failed" && last.error, "boom");
  assert.equal(detail.round, 2);
});

test("an empty id or an empty log folds to an empty detail", () => {
  // Empty log: nothing to find. Empty id: never adopts a dim even when the
  // roster exists.
  for (const detail of [
    foldAgentDetail([], "governing_codes"),
    foldAgentDetail(log([ROSTER]), ""),
  ]) {
    assert.equal(detail.dim, null);
    assert.deepEqual(detail.timeline, []);
    assert.equal(detail.maxSearches, 0);
    assert.equal(detail.maxFetches, 0);
  }
});

test("a queued dimension has roster state but no timeline; an unknown id has neither", () => {
  const events = log([ROSTER]);
  const queued = foldAgentDetail(events, "governing_codes");
  assert.equal(queued.dim?.state, "queued");
  assert.equal(queued.dim?.title, "Governing building and fire codes");
  assert.deepEqual(queued.timeline, []);
  const unknown = foldAgentDetail(events, "never_registered");
  assert.equal(unknown.dim, null);
  assert.deepEqual(unknown.timeline, []);
});

test("the modal's summary state is the board fold's state, verbatim", () => {
  const events = log([
    ROSTER,
    [
      "dimension_started",
      { dimension_id: "governing_codes", max_searches: 24, max_fetches: 8 },
    ],
    ["dimension_search", { dimension_id: "governing_codes", query: "q1" }],
    ["dimension_fetch", { dimension_id: "governing_codes", url: "https://a.example/x" }],
    ["dimension_activity", { dimension_id: "ahj_requirements", kind: "writing" }],
  ]);
  const detail = foldAgentDetail(events, "governing_codes");
  assert.deepEqual(
    detail.dim,
    foldResearchBoard(events).dims.find((d) => d.id === "governing_codes"),
  );
});

/* --- Regression pins for the board fold, now that it moved to lib --- */

test("the roster seeds every card with its title before any worker speaks", () => {
  const board = foldResearchBoard(log([ROSTER]));
  assert.equal(board.total, 2);
  assert.deepEqual(
    board.dims.map((d) => [d.id, d.title, d.state]),
    [
      ["governing_codes", "Governing building and fire codes", "queued"],
      ["ahj_requirements", "AHJ requirements", "queued"],
    ],
  );
});

test("recent queries cap at RECENT_CAP, newest first — the truncation the modal exists to undo", () => {
  const searches: Array<[string, Partial<ResearchEvent>]> = [
    "q1",
    "q2",
    "q3",
    "q4",
  ].map((q) => [
    "dimension_search",
    { dimension_id: "governing_codes", query: q },
  ]);
  const board = foldResearchBoard(log([ROSTER, ...searches]));
  const dim = board.dims.find((d) => d.id === "governing_codes");
  assert.equal(dim?.recent.length, RECENT_CAP);
  assert.deepEqual(
    dim?.recent.map((r) => r.text),
    ["q4", "q3", "q2"],
  );
  assert.equal(dim?.searches, 4);
});

test("a retry notice is cleared by the next activity, and terminal events adopt billed counts", () => {
  const base: Array<[string, Partial<ResearchEvent>]> = [
    ROSTER,
    ["dimension_started", { dimension_id: "governing_codes" }],
    [
      "dimension_retry",
      {
        dimension_id: "governing_codes",
        attempt: 1,
        max_attempts: 3,
        reason: "connection",
        backoff_s: 2,
      },
    ],
  ];
  const retried = foldResearchBoard(log(base)).dims[0];
  assert.deepEqual(retried.retry, { attempt: 1, max: 3, reason: "connection" });
  const resumed = foldResearchBoard(
    log([
      ...base,
      ["dimension_activity", { dimension_id: "governing_codes", kind: "thinking" }],
    ]),
  ).dims[0];
  assert.equal(resumed.retry, null);
  const done = foldResearchBoard(
    log([
      ...base,
      [
        "dimension_complete",
        {
          dimension_id: "governing_codes",
          item_count: 7,
          grounded_count: 6,
          web_search_requests: 11,
          web_fetch_requests: 4,
        },
      ],
    ]),
  ).dims[0];
  assert.equal(done.state, "done");
  assert.equal(done.retry, null);
  assert.deepEqual(
    [done.itemCount, done.groundedCount, done.billedSearches, done.billedFetches],
    [7, 6, 11, 4],
  );
});

test("trimUrl shows host/path and passes junk through; labelFromId title-cases", () => {
  assert.equal(trimUrl("https://www.example.com/codes/nfpa-13/"), "example.com/codes/nfpa-13");
  assert.equal(trimUrl("https://example.com/"), "example.com");
  assert.equal(trimUrl("not a url"), "not a url");
  assert.equal(labelFromId("governing_codes"), "Governing Codes");
});
