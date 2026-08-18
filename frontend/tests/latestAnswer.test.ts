import { test } from "node:test";
import assert from "node:assert/strict";

import { createLatestAnswer } from "../src/lib/latestAnswer.ts";

test("a stale answer never overwrites a newer one, whichever arrives first", () => {
  const gate = createLatestAnswer<string>();
  const seen: string[] = [];
  const apply = (v: string) => seen.push(v);

  // The launch check is issued first, the forced check second.
  const launch = gate.next();
  const forced = gate.next();

  // The forced check comes back first and shows the install control.
  assert.equal(gate.accept(forced, "UPDATE_AVAILABLE", apply), true);
  // The launch check's slow, stale answer must not erase it.
  assert.equal(gate.accept(launch, "THROTTLED", apply), false);

  assert.deepEqual(seen, ["UPDATE_AVAILABLE"]);
});

test("the ordinary order still applies both answers", () => {
  const gate = createLatestAnswer<string>();
  const seen: string[] = [];
  const apply = (v: string) => seen.push(v);

  const launch = gate.next();
  assert.equal(gate.accept(launch, "THROTTLED", apply), true);
  const forced = gate.next();
  assert.equal(gate.accept(forced, "UPDATE_AVAILABLE", apply), true);

  assert.deepEqual(seen, ["THROTTLED", "UPDATE_AVAILABLE"]);
});

test("a failure is an answer too — a stale one is still dropped", () => {
  const gate = createLatestAnswer<string | null>();
  const seen: (string | null)[] = [];
  const apply = (v: string | null) => seen.push(v);

  const launch = gate.next();
  const forced = gate.next();
  gate.accept(forced, "UPDATE_AVAILABLE", apply);
  // The launch fetch times out; clearing state here is the regression.
  assert.equal(gate.accept(launch, null, apply), false);

  assert.deepEqual(seen, ["UPDATE_AVAILABLE"]);
});

test("each request answers once and later requests keep winning", () => {
  const gate = createLatestAnswer<number>();
  const seen: number[] = [];
  const apply = (v: number) => seen.push(v);

  const a = gate.next();
  const b = gate.next();
  const c = gate.next();
  assert.equal(gate.accept(c, 3, apply), true);
  assert.equal(gate.accept(a, 1, apply), false);
  assert.equal(gate.accept(b, 2, apply), false);
  // A request issued after the winner still applies.
  assert.equal(gate.accept(gate.next(), 4, apply), true);

  assert.deepEqual(seen, [3, 4]);
});
