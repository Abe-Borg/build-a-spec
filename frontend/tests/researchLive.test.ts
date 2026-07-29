import assert from "node:assert/strict";
import test from "node:test";

import {
  RESEARCH_MILESTONE_TYPES,
  classifyResearchStreamEnd,
  isResearchActiveSnapshot,
  maxResearchEventSeq,
  mergeResearchEvent,
  reconcileResearchSnapshot,
  reconcileResearchSnapshotUpdate,
  researchSnapshotRound,
} from "../src/lib/researchLive.ts";
import type {
  ResearchEvent,
  ResearchProfileView,
  ResearchSnapshot,
} from "../src/types.ts";

const evt = (
  seq: number,
  type: string,
  extra: Partial<ResearchEvent> = {},
): ResearchEvent => ({
  seq,
  ts: "10:00:00",
  type,
  round: 1,
  ...extra,
});

const started = (round = 1): ResearchEvent =>
  evt(0, "research_started", {
    round,
    dimensions: ["governing_codes"],
    dimension_titles: { governing_codes: "Governing codes" },
  });

const running = (events: ResearchEvent[]): ResearchSnapshot => ({
  status: "running",
  error: "",
  events,
});

const profile = (itemCount: number): ResearchProfileView =>
  ({ item_count: itemCount } as ResearchProfileView);

/* --- merge --- */

test("merge appends in order and dedupes a replayed seq", () => {
  let snapshot: ResearchSnapshot | null = null;
  snapshot = mergeResearchEvent(snapshot, started());
  snapshot = mergeResearchEvent(snapshot, evt(2, "dimension_search", { query: "b" }));
  snapshot = mergeResearchEvent(snapshot, evt(1, "dimension_started"));
  snapshot = mergeResearchEvent(snapshot, evt(2, "dimension_search", { query: "c" }));
  assert.deepEqual(snapshot.events.map((e) => e.seq), [0, 1, 2]);
  assert.equal(snapshot.events[2].query, "c");
});

test("merge never writes status, error or profile", () => {
  const base: ResearchSnapshot = {
    status: "complete",
    error: "boom",
    events: [started()],
    profile: profile(4),
  };
  const merged = mergeResearchEvent(base, evt(1, "dimension_complete"));
  assert.equal(merged.status, "complete");
  assert.equal(merged.error, "boom");
  assert.equal(merged.profile?.item_count, 4);
});

test("a replayed research_started for the live round does not empty the board", () => {
  // The reconnect this chunk introduces replays the round from seq 0, so
  // resetting on every research_started would blank the board on every
  // transport hiccup and rebuild it from the replay.
  let snapshot = mergeResearchEvent(null, started());
  snapshot = mergeResearchEvent(snapshot, evt(1, "dimension_started"));
  snapshot = mergeResearchEvent(snapshot, evt(2, "dimension_search", { query: "a" }));
  const replayed = mergeResearchEvent(snapshot, started());
  assert.deepEqual(replayed.events.map((e) => e.seq), [0, 1, 2]);
});

test("a research_started for a different round resets the log", () => {
  let snapshot = mergeResearchEvent(null, started(1));
  snapshot = mergeResearchEvent(snapshot, evt(1, "dimension_started"));
  const next = mergeResearchEvent(snapshot, started(2));
  assert.deepEqual(next.events.map((e) => e.round), [2]);
});

test("a restart of the SAME round number after a stop resets the log", () => {
  // A stopped round is never adopted into the profile, so the runner
  // numbers the next one identically. Round identity cannot see that; the
  // terminal local status can.
  let snapshot = mergeResearchEvent(null, started(2));
  snapshot = mergeResearchEvent(snapshot, evt(1, "dimension_started"));
  snapshot = mergeResearchEvent(snapshot, evt(2, "research_failed", {
    error: "Stopped by user — this round's progress was discarded.",
  }));
  const stopped: ResearchSnapshot = { ...snapshot, status: "failed" };
  const restarted = mergeResearchEvent(stopped, started(2));
  assert.deepEqual(restarted.events.map((e) => e.seq), [0]);
});

test("merge ignores the stream_end sentinel", () => {
  const snapshot = mergeResearchEvent(running([started()]), {
    type: "stream_end",
    status: "complete",
  } as unknown as ResearchEvent);
  assert.deepEqual(snapshot.events.map((e) => e.seq), [0]);
});

/* --- watermark + identity helpers --- */

test("watermark is the max seq, not the length", () => {
  assert.equal(maxResearchEventSeq([]), -1);
  assert.equal(maxResearchEventSeq([evt(0, "a"), evt(7, "b"), evt(3, "c")]), 7);
});

test("round identity reads the first event that declares one", () => {
  assert.equal(researchSnapshotRound(null), 0);
  assert.equal(researchSnapshotRound(running([])), 0);
  assert.equal(researchSnapshotRound(running([started(3)])), 3);
});

test("only a running snapshot is active", () => {
  assert.equal(isResearchActiveSnapshot(null), false);
  assert.equal(isResearchActiveSnapshot(running([])), true);
  assert.equal(isResearchActiveSnapshot({ ...running([]), status: "idle" }), false);
  assert.equal(isResearchActiveSnapshot({ ...running([]), status: "complete" }), false);
  assert.equal(isResearchActiveSnapshot({ ...running([]), status: "failed" }), false);
});

/* --- stream_end classification --- */

test("stream_end classification decides reconnect for every status", () => {
  assert.equal(classifyResearchStreamEnd("complete"), "terminal");
  assert.equal(classifyResearchStreamEnd("failed"), "terminal");
  assert.equal(classifyResearchStreamEnd("superseded"), "superseded");
  assert.equal(classifyResearchStreamEnd("running"), "interrupted");
  assert.equal(classifyResearchStreamEnd("idle"), "interrupted");
  assert.equal(classifyResearchStreamEnd(undefined), "interrupted");
});

/* --- reconcile --- */

test("a stale same-round fetch is rejected whole, status and profile included", () => {
  // The bug this replaces: the old reconcile kept the longer local log but
  // still adopted the fetch's status/error/profile, so a pre-terminal
  // response could report `running` over a finished round and drop the
  // profile that had just been adopted.
  const local: ResearchSnapshot = {
    status: "complete",
    error: "",
    events: [started(), evt(1, "dimension_complete"), evt(2, "research_complete")],
    profile: profile(9),
  };
  const stale: ResearchSnapshot = {
    status: "running",
    error: "",
    events: [started()],
  };
  const decision = reconcileResearchSnapshotUpdate(local, stale);
  assert.equal(decision.accepted, false);
  assert.equal(decision.snapshot, local);
  assert.equal(decision.snapshot.status, "complete");
  assert.equal(decision.snapshot.profile?.item_count, 9);
});

test("the same race on a later round is rejected too", () => {
  const local: ResearchSnapshot = {
    status: "complete",
    error: "",
    events: [started(4), evt(5, "research_complete", { round: 4 })],
    profile: profile(12),
  };
  const stale = running([started(4)]);
  const decision = reconcileResearchSnapshotUpdate(local, stale);
  assert.equal(decision.accepted, false);
  assert.equal(decision.snapshot.status, "complete");
});

test("restarting a stopped round is settled by the merge, not by reconcile", () => {
  // Reconcile CANNOT tell "round 2 restarted" from "a late fetch from the
  // middle of round 2" — same round, shorter fetched log, both times. The
  // merge's reset is what makes the restart safe, and it runs first: the
  // new round's research_started frame arrives before the milestone refetch
  // it triggers, so by the time a fetch lands the local log is the new
  // round's and its watermark comparison is against the right log.
  const stopped: ResearchSnapshot = {
    status: "failed",
    error: "Stopped by user — this round's progress was discarded.",
    events: [
      started(2),
      evt(1, "dimension_started", { round: 2 }),
      evt(2, "dimension_search", { round: 2, query: "x" }),
      evt(3, "research_failed", { round: 2 }),
    ],
    profile: profile(7),
  };
  const afterFrame = mergeResearchEvent(stopped, started(2));
  assert.deepEqual(afterFrame.events.map((e) => e.seq), [0]);

  const fresh: ResearchSnapshot = {
    status: "running",
    error: "",
    events: [started(2), evt(1, "dimension_started", { round: 2 })],
    profile: profile(7),
  };
  const decision = reconcileResearchSnapshotUpdate(afterFrame, fresh);
  assert.equal(decision.accepted, true);
  assert.equal(decision.snapshot.status, "running");
  assert.deepEqual(decision.snapshot.events.map((e) => e.seq), [0, 1]);
});

test("a fetch that reaches further is adopted", () => {
  const local = running([started(), evt(1, "dimension_started")]);
  const fetched: ResearchSnapshot = {
    status: "complete",
    error: "",
    events: [started(), evt(1, "dimension_started"), evt(2, "research_complete")],
    profile: profile(3),
  };
  const decision = reconcileResearchSnapshotUpdate(local, fetched);
  assert.equal(decision.accepted, true);
  assert.equal(decision.snapshot.status, "complete");
  assert.equal(decision.snapshot.profile?.item_count, 3);
  assert.deepEqual(decision.snapshot.events.map((e) => e.seq), [0, 1, 2]);
});

test("a different round replaces wholesale", () => {
  const local: ResearchSnapshot = {
    status: "complete",
    error: "",
    events: [started(1), evt(1, "research_complete")],
    profile: profile(5),
  };
  const fetched = running([started(2)]);
  const decision = reconcileResearchSnapshotUpdate(local, fetched);
  assert.equal(decision.accepted, true);
  assert.equal(decision.snapshot.status, "running");
  assert.equal(decision.snapshot.profile, undefined);
  assert.equal(researchSnapshotRound(decision.snapshot), 2);
});

test("an equal watermark cannot regress lifecycle state", () => {
  const events = [started(), evt(1, "research_complete")];
  const local: ResearchSnapshot = {
    status: "complete",
    error: "",
    events,
    profile: profile(6),
  };
  const peer: ResearchSnapshot = { status: "running", error: "", events };
  const decision = reconcileResearchSnapshotUpdate(local, peer);
  assert.equal(decision.accepted, true);
  assert.equal(decision.snapshot.status, "complete");
  assert.equal(decision.snapshot.profile?.item_count, 6);
});

test("an equal watermark still adopts a genuine advance", () => {
  const events = [started(), evt(1, "research_complete")];
  const local = { ...running(events), profile: undefined };
  const peer: ResearchSnapshot = {
    status: "complete",
    error: "",
    events,
    profile: profile(2),
  };
  const decision = reconcileResearchSnapshotUpdate(local, peer);
  assert.equal(decision.accepted, true);
  assert.equal(decision.snapshot.status, "complete");
  assert.equal(decision.snapshot.profile?.item_count, 2);
});

test("a response from an older generation is rejected with no side effects", () => {
  const local = running([started(), evt(1, "dimension_started")]);
  const fetched: ResearchSnapshot = {
    status: "failed",
    error: "no API key",
    error_kind: "auth_error",
    events: [started(), evt(1, "dimension_started"), evt(2, "research_failed")],
  };
  const decision = reconcileResearchSnapshotUpdate(local, fetched, {
    requestGeneration: 1,
    currentGeneration: 2,
  });
  assert.equal(decision.accepted, false);
  assert.equal(decision.snapshot, local);
  assert.equal(decision.snapshot.status, "running");
});

test("a current-generation response is accepted normally", () => {
  const local = running([started()]);
  const fetched: ResearchSnapshot = {
    status: "complete",
    error: "",
    events: [started(), evt(1, "research_complete")],
  };
  const decision = reconcileResearchSnapshotUpdate(local, fetched, {
    requestGeneration: 3,
    currentGeneration: 3,
  });
  assert.equal(decision.accepted, true);
  assert.equal(decision.snapshot.status, "complete");
});

test("the first snapshot of a session is always adopted", () => {
  const fetched = running([started()]);
  const decision = reconcileResearchSnapshotUpdate(null, fetched);
  assert.equal(decision.accepted, true);
  assert.equal(decision.snapshot, fetched);
});

test("a round-less snapshot on either side adopts the fetch", () => {
  const local = running([evt(0, "research_started", { round: undefined })]);
  const fetched: ResearchSnapshot = {
    status: "complete",
    error: "",
    events: [],
    profile: profile(1),
  };
  assert.equal(reconcileResearchSnapshot(local, fetched), fetched);
});

test("a shorter same-round log from ANOTHER workspace still reads as stale", () => {
  // Round number is the reconcile identity, and it says nothing across
  // workspaces: two projects both sit at round 1, and a RESTORED project's
  // whole log is one research_complete at seq 0 (ResearchRunner.restore
  // empties events first). So opening project B over researched project A
  // arrives here indistinguishable from a late fetch, and is rejected —
  // permanently, since a restored run never streams. Reconcile cannot fix
  // that; the workspace transition has to clear the snapshot, which is what
  // the next test checks.
  const projectA: ResearchSnapshot = {
    status: "complete",
    error: "",
    events: [started(1), evt(1, "dimension_complete"), evt(2, "research_complete")],
    profile: profile(31),
  };
  const projectB: ResearchSnapshot = {
    status: "complete",
    error: "",
    events: [evt(0, "research_complete", { round: 1, restored: true })],
    profile: profile(4),
  };
  const notCleared = reconcileResearchSnapshotUpdate(projectA, projectB);
  assert.equal(notCleared.accepted, false);
  assert.equal(notCleared.snapshot.profile?.item_count, 31);

  // Cleared first, it adopts — this is the whole mechanism.
  const cleared = reconcileResearchSnapshotUpdate(null, projectB);
  assert.equal(cleared.accepted, true);
  assert.equal(cleared.snapshot.profile?.item_count, 4);
});

test("every workspace transition clears the research snapshot", async () => {
  // Cross-file, so no unit test can reach it: the guarantee above lives in
  // App.tsx's advanceWorkspaceEpoch, the one helper all three transitions
  // call. Asserted against the source text for the same reason tour.test.ts
  // scans production files — the alternative is a browser-driven suite this
  // repo deliberately does not have.
  const { readFile } = await import("node:fs/promises");
  const source = await readFile(new URL("../src/App.tsx", import.meta.url), "utf8");

  const body = source.match(
    /const advanceWorkspaceEpoch = useCallback\(\(\) => \{([\s\S]*?)\n {2}\}, \[/,
  );
  assert.ok(body, "advanceWorkspaceEpoch should be a useCallback in App.tsx");
  assert.match(body[1], /replaceResearchSnapshot\(null\)/);
  assert.match(body[1], /workspaceEpochRef\.current \+= 1/);
  assert.match(body[1], /researchStreamRef\.current\?\.abort\(\)/);

  // …and nothing bumps the epoch behind its back.
  const bumps = source.match(/workspaceEpochRef\.current \+= 1/g) ?? [];
  assert.equal(bumps.length, 1, "the epoch may only be advanced by the helper");
});

test("milestone set is exactly the five snapshot-worthy frames", () => {
  assert.deepEqual([...RESEARCH_MILESTONE_TYPES].sort(), [
    "dimension_complete",
    "dimension_failed",
    "research_complete",
    "research_failed",
    "research_started",
  ]);
  assert.equal(RESEARCH_MILESTONE_TYPES.has("dimension_search"), false);
  assert.equal(RESEARCH_MILESTONE_TYPES.has("dimension_activity"), false);
});
