import assert from "node:assert/strict";
import test from "node:test";

import {
  eventSeqIndex,
  hasEventSeq,
  rememberAppendedSeq,
} from "../src/lib/eventSeqIndex.ts";

const frames = (...seqs: number[]) => seqs.map((seq) => ({ seq }));

test("a dense log indexes its span and carries no gap set", () => {
  const index = eventSeqIndex(frames(0, 1, 2, 3));
  assert.equal(index.min, 0);
  assert.equal(index.max, 3);
  assert.equal(index.missing.size, 0);
  for (const seq of [0, 1, 2, 3]) assert.equal(hasEventSeq(index, seq), true);
  assert.equal(hasEventSeq(index, 4), false);
});

test("an empty or unnumbered log holds nothing", () => {
  const empty = eventSeqIndex([]);
  assert.equal(empty.max, -1);
  assert.equal(hasEventSeq(empty, 0), false);

  const unnumbered = eventSeqIndex([{}, {}]);
  assert.equal(unnumbered.max, -1);
  assert.equal(hasEventSeq(unnumbered, 0), false);
});

test("a gap is absent, not a duplicate", () => {
  const index = eventSeqIndex(frames(0, 1, 4));
  assert.deepEqual([...index.missing].sort(), [2, 3]);
  assert.equal(hasEventSeq(index, 2), false);
  assert.equal(hasEventSeq(index, 3), false);
  assert.equal(hasEventSeq(index, 4), true);
});

test("a log that does not start at zero reports below its floor as absent", () => {
  // Nothing below `min` was ever indexed, so it cannot be a replay.
  const index = eventSeqIndex(frames(7, 8));
  assert.equal(index.min, 7);
  assert.equal(hasEventSeq(index, 6), false);
  assert.equal(hasEventSeq(index, 7), true);
});

test("a repeated sequence still reads as dense", () => {
  const index = eventSeqIndex(frames(0, 1, 1, 2));
  assert.equal(index.missing.size, 0);
  assert.equal(hasEventSeq(index, 1), true);
});

test("a non-finite sequence is never a duplicate", () => {
  const index = eventSeqIndex(frames(0, 1));
  assert.equal(hasEventSeq(index, Number.NaN), false);
  assert.equal(hasEventSeq(index, Number.POSITIVE_INFINITY), false);
});

test("the index is memoized per array, not rebuilt per call", () => {
  const log = frames(0, 1, 2);
  assert.equal(eventSeqIndex(log), eventSeqIndex(log));
});

test("a dense append derives in place and shares the gap set", () => {
  const log = frames(0, 1, 2);
  const index = eventSeqIndex(log);
  const next = [...log, { seq: 3 }];
  const derived = rememberAppendedSeq(next, index, 3);
  assert.equal(derived.max, 3);
  assert.equal(derived.min, 0);
  // Shared, not copied — copying a set per append is the same O(n) per frame
  // the index exists to remove.
  assert.equal(derived.missing, index.missing);
  assert.equal(eventSeqIndex(next), derived);
  assert.equal(hasEventSeq(derived, 3), true);
});

test("an append past the watermark records the hole it left behind", () => {
  const log = frames(0, 1);
  const index = eventSeqIndex(log);
  const next = [...log, { seq: 5 }];
  const derived = rememberAppendedSeq(next, index, 5);
  assert.deepEqual([...derived.missing].sort(), [2, 3, 4]);
  assert.equal(hasEventSeq(derived, 3), false);
  assert.equal(hasEventSeq(derived, 5), true);
});

test("the first append to an empty log seeds the span", () => {
  const next = frames(9);
  const derived = rememberAppendedSeq(next, eventSeqIndex([]), 9);
  assert.equal(derived.min, 9);
  assert.equal(derived.max, 9);
  assert.equal(hasEventSeq(derived, 9), true);
});

test("deriving a new array's index leaves the old array's index correct", () => {
  // Merging onto an older snapshot must cost a rebuild, never a wrong answer,
  // so no derivation may mutate an index another array is still keyed to.
  const log = frames(0, 1);
  const index = eventSeqIndex(log);
  rememberAppendedSeq([...log, { seq: 2 }], index, 2);
  assert.equal(eventSeqIndex(log).max, 1);
  assert.equal(hasEventSeq(eventSeqIndex(log), 2), false);
});
