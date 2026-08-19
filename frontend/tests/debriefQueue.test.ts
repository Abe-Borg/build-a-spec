import assert from "node:assert/strict";
import test from "node:test";

import {
  emptyDebriefQueue,
  rememberDebrief,
  requeueDebrief,
  takeNextDebrief,
} from "../src/lib/debriefQueue.ts";
import type {
  DebriefGates,
  DebriefQueueState,
  PendingDebrief,
} from "../src/lib/debriefQueue.ts";

const entry = (over: Partial<PendingDebrief> = {}): PendingDebrief => ({
  kind: "research",
  token: "round-1",
  epoch: 1,
  ...over,
});

const gates = (over: Partial<DebriefGates> = {}): DebriefGates => ({
  epoch: 1,
  busy: false,
  manualEditBusy: false,
  fileLoading: false,
  tourActive: false,
  protectedWorkspace: false,
  autoDebrief: true,
  ...over,
});

test("a remembered completion fires once and never re-queues", () => {
  let state = rememberDebrief(emptyDebriefQueue(), entry());
  const take = takeNextDebrief(state, gates());
  assert.equal(take.next?.kind, "research");
  assert.equal(take.next?.token, "round-1");
  state = take.state;
  assert.equal(state.pending.length, 0);
  // An SSE reconnect replays the terminal frame: the fired token is inert.
  const replayed = rememberDebrief(state, entry());
  assert.equal(replayed, state);
  assert.equal(takeNextDebrief(replayed, gates()).next, null);
});

test("a newer completion of the same kind replaces the older", () => {
  let state = rememberDebrief(emptyDebriefQueue(), entry({ token: "round-1" }));
  state = rememberDebrief(state, entry({ token: "round-2" }));
  assert.equal(state.pending.length, 1);
  assert.equal(state.pending[0].token, "round-2");
});

test("remembering an identical pending entry returns the same state", () => {
  const state = rememberDebrief(emptyDebriefQueue(), entry());
  assert.equal(rememberDebrief(state, entry()), state);
});

test("a streaming turn, manual edit, or file load HOLDS the debrief", () => {
  const state = rememberDebrief(emptyDebriefQueue(), entry());
  for (const hold of [
    { busy: true },
    { manualEditBusy: true },
    { fileLoading: true },
  ]) {
    const take = takeNextDebrief(state, gates(hold));
    assert.equal(take.next, null);
    assert.equal(take.state, state); // identity: nothing changed
  }
  // Once the hold lifts, it fires.
  assert.equal(takeNextDebrief(state, gates()).next?.token, "round-1");
});

test("a requeued entry un-fires its token and fires again later", () => {
  let state = rememberDebrief(emptyDebriefQueue(), entry());
  const take = takeNextDebrief(state, gates());
  assert.equal(take.next?.token, "round-1");
  // The fire-time guards raced the gates: put it back.
  state = requeueDebrief(take.state, take.next!);
  assert.equal(state.pending.length, 1);
  assert.equal(state.fired.includes("research:round-1"), false);
  // Held while the guard persists, delivered on its falling edge.
  assert.equal(takeNextDebrief(state, gates({ manualEditBusy: true })).next, null);
  const retry = takeNextDebrief(state, gates());
  assert.equal(retry.next?.token, "round-1");
  // And once genuinely fired, the token is spent again.
  assert.equal(
    rememberDebrief(retry.state, entry()),
    retry.state,
  );
});

test("tutorial, protected workspace, and the off switch DROP silently", () => {
  for (const drop of [
    { tourActive: true },
    { protectedWorkspace: true },
    { autoDebrief: false },
  ]) {
    const state = rememberDebrief(emptyDebriefQueue(), entry());
    const take = takeNextDebrief(state, gates(drop));
    assert.equal(take.next, null);
    assert.equal(take.state.pending.length, 0);
    // Dropped, not fired: nothing was recorded as sent.
    assert.equal(take.state.fired.length, 0);
    // The cleared state is stable on the next flush.
    const again = takeNextDebrief(take.state, gates(drop));
    assert.equal(again.state, take.state);
  }
});

test("an entry from a previous workspace epoch dies at the flush", () => {
  const state = rememberDebrief(emptyDebriefQueue(), entry({ epoch: 1 }));
  const take = takeNextDebrief(state, gates({ epoch: 2 }));
  assert.equal(take.next, null);
  assert.equal(take.state.pending.length, 0);
});

test("research debriefs before qc when both are pending", () => {
  let state: DebriefQueueState = emptyDebriefQueue();
  state = rememberDebrief(state, entry({ kind: "qc", token: "qc-run-1" }));
  state = rememberDebrief(state, entry({ kind: "research", token: "round-3" }));
  const first = takeNextDebrief(state, gates());
  assert.equal(first.next?.kind, "research");
  const second = takeNextDebrief(first.state, gates());
  assert.equal(second.next?.kind, "qc");
  assert.equal(takeNextDebrief(second.state, gates()).next, null);
});

test("the fired ledger stays bounded", () => {
  let state: DebriefQueueState = emptyDebriefQueue();
  for (let i = 0; i < 20; i += 1) {
    state = rememberDebrief(state, entry({ token: `round-${i}` }));
    state = takeNextDebrief(state, gates()).state;
  }
  assert.ok(state.fired.length <= 16);
  assert.equal(state.fired.at(-1), "research:round-19");
});
