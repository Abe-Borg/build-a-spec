import assert from "node:assert/strict";
import { test } from "node:test";

import {
  followupAge,
  followupCounts,
  justResolvedIds,
  settledFollowups,
  waitingFollowups,
} from "../src/lib/followups.ts";
import type { FollowUp } from "../src/types.ts";

function item(overrides: Partial<FollowUp> & { fid: string }): FollowUp {
  return {
    kind: "question",
    title: "a question",
    detail: "",
    blocking: false,
    element_id: "",
    status: "open",
    resolution: "",
    resolved_by: "",
    raised_turn: 0,
    created_at: "",
    resolved_at: "",
    ...overrides,
  };
}

test("a newly resolved item is reported, an already-resolved one is not", () => {
  const before = [item({ fid: "fu-1" }), item({ fid: "fu-2", status: "resolved" })];
  const after = [
    item({ fid: "fu-1", status: "resolved" }),
    item({ fid: "fu-2", status: "resolved" }),
  ];
  assert.deepEqual([...justResolvedIds(before, after)], ["fu-1"]);
});

test("the first render animates nothing", () => {
  // A restored project arrives with its whole settled history at once;
  // replaying every check-off on load would be noise, not feedback.
  const loaded = [item({ fid: "fu-1", status: "resolved" })];
  assert.equal(justResolvedIds(null, loaded).size, 0);
  assert.equal(justResolvedIds([], loaded).size, 0);
});

test("reopening an item is not a check-off", () => {
  const before = [item({ fid: "fu-1", status: "resolved" })];
  const after = [item({ fid: "fu-1" })];
  assert.equal(justResolvedIds(before, after).size, 0);
});

test("an item that vanished between snapshots is not reported", () => {
  // The store trims its oldest settled items; a dropped id must not read as
  // a fresh check-off on the next payload.
  const before = [item({ fid: "fu-1" }), item({ fid: "fu-2" })];
  const after = [item({ fid: "fu-2", status: "resolved" })];
  assert.deepEqual([...justResolvedIds(before, after)], ["fu-2"]);
});

test("waiting items sort blocking first, then oldest", () => {
  const items = [
    item({ fid: "fu-3", raised_turn: 1 }),
    item({ fid: "fu-1", raised_turn: 5, blocking: true }),
    item({ fid: "fu-2", raised_turn: 0 }),
    item({ fid: "fu-4", status: "resolved" }),
  ];
  assert.deepEqual(
    waitingFollowups(items).map((entry) => entry.fid),
    ["fu-1", "fu-2", "fu-3"],
  );
});

test("settled items list most recently checked off first", () => {
  const items = [
    item({ fid: "fu-1", status: "resolved" }),
    item({ fid: "fu-5", status: "resolved" }),
    item({ fid: "fu-2" }),
  ];
  assert.deepEqual(
    settledFollowups(items).map((entry) => entry.fid),
    ["fu-5", "fu-1"],
  );
});

test("counts split the list by status", () => {
  assert.deepEqual(followupCounts([]), { waiting: 0, done: 0 });
  assert.deepEqual(
    followupCounts([
      item({ fid: "fu-1" }),
      item({ fid: "fu-2", status: "resolved" }),
      item({ fid: "fu-3" }),
    ]),
    { waiting: 2, done: 1 },
  );
});

test("age reads in replies, and never goes negative", () => {
  assert.equal(followupAge(item({ fid: "fu-1", raised_turn: 4 }), 4), "just now");
  assert.equal(followupAge(item({ fid: "fu-1", raised_turn: 9 }), 4), "just now");
  assert.equal(followupAge(item({ fid: "fu-1", raised_turn: 3 }), 4), "1 reply ago");
  assert.equal(followupAge(item({ fid: "fu-1", raised_turn: 0 }), 4), "4 replies ago");
});
