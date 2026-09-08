/**
 * The open-dialog stack — pure, so it is tested directly (the hook that
 * consumes it has no DOM harness; `tests/dialogs.test.ts` pins the wiring
 * at the source level).
 */
import assert from "node:assert/strict";
import test from "node:test";

import { createDialogStack } from "../src/lib/dialogStack.ts";

test("one open dialog is the top", () => {
  const stack = createDialogStack();
  const a = {};
  assert.equal(stack.isTop(a), false, "not yet entered");
  const leave = stack.enter(a);
  assert.equal(stack.isTop(a), true);
  assert.equal(stack.size(), 1);
  leave();
  assert.equal(stack.isTop(a), false);
  assert.equal(stack.size(), 0);
});

test("a dialog opened later takes the top, and leaving gives it back", () => {
  const stack = createDialogStack();
  const parent = {};
  const child = {};
  const leaveParent = stack.enter(parent);
  const leaveChild = stack.enter(child);
  assert.equal(stack.isTop(child), true);
  assert.equal(stack.isTop(parent), false, "the parent must not act while a child is up");
  leaveChild();
  assert.equal(stack.isTop(parent), true, "the parent is top again once the child leaves");
  leaveParent();
  assert.equal(stack.size(), 0);
});

test("leaving a dialog that is not on top removes that dialog, never the top", () => {
  // An out-of-order unmount (parent and child torn down in one commit, parent
  // cleanup first) must not pop the child's entry.
  const stack = createDialogStack();
  const parent = {};
  const child = {};
  const leaveParent = stack.enter(parent);
  stack.enter(child);
  leaveParent();
  assert.equal(stack.isTop(child), true);
  assert.equal(stack.size(), 1);
});

test("leaving twice is harmless", () => {
  const stack = createDialogStack();
  const a = {};
  const b = {};
  const leaveA = stack.enter(a);
  stack.enter(b);
  leaveA();
  leaveA();
  assert.equal(stack.isTop(b), true);
  assert.equal(stack.size(), 1);
});

test("the same token entered twice leaves the most recent entry first", () => {
  // A hook whose effect re-runs while open (its refs changed) enters again
  // before its previous cleanup would be visible; identity-based removal
  // keeps the count honest either way.
  const stack = createDialogStack();
  const a = {};
  const first = stack.enter(a);
  const second = stack.enter(a);
  assert.equal(stack.size(), 2);
  second();
  assert.equal(stack.isTop(a), true);
  first();
  assert.equal(stack.size(), 0);
});
