import assert from "node:assert/strict";
import test from "node:test";

import {
  MAX_PARAGRAPH_LEVELS,
  adjacentAllowedPosition,
  canAddChildParagraph,
  editorAllowedPositions,
  hasAlternatePosition,
  paragraphSubtreeSize,
  siblingDropPosition,
  submitExclusiveEdit,
} from "../src/lib/structuralEditing.ts";
import type { DocParagraph, SourceOperationCapability } from "../src/types.ts";

test("native structural edits expose every valid sibling position", () => {
  const allowed: SourceOperationCapability = { allowed: true };
  assert.deepEqual(editorAllowedPositions(allowed, false, 3), [0, 1, 2, 3]);
  assert.deepEqual(editorAllowedPositions(allowed, false, -1), []);
});

test("source-backed edits use exact positions and ignore descriptive ranges", () => {
  const capability: SourceOperationCapability = {
    allowed: true,
    minimum_position: 0,
    maximum_position: 6,
    allowed_positions: [0, 4, 9],
    placements: [{ island_key: "safe", allowed_positions: [2, 4] }],
  };
  assert.deepEqual(editorAllowedPositions(capability, true, 6), [0, 2, 4]);
  assert.deepEqual(
    editorAllowedPositions({ allowed: false, allowed_positions: [0] }, true, 1),
    [],
  );
});

test("sortable drops resolve to one final index only for valid sibling moves", () => {
  const ids = ["a", "b", "c"];
  assert.equal(siblingDropPosition(ids, "a", "c", [0, 1, 2]), 2);
  assert.equal(siblingDropPosition(ids, "a", "a", [0, 1, 2]), null);
  assert.equal(siblingDropPosition(ids, "a", null, [0, 1, 2]), null);
  assert.equal(siblingDropPosition(ids, "a", "other-parent", [0, 1, 2]), null);
  assert.equal(siblingDropPosition(ids, "a", "b", [0, 2]), null);
});

test("manual edits remain single-flight until the authoritative request settles", async () => {
  let release!: () => void;
  let requests = 0;
  const lock = { current: false };
  const request = () => {
    requests += 1;
    return new Promise<void>((resolve) => {
      release = resolve;
    });
  };
  assert.equal(submitExclusiveEdit(lock, request, () => {}), true);
  assert.equal(submitExclusiveEdit(lock, request, () => {}), false);
  assert.equal(requests, 1);
  release();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(lock.current, false);
});

test("arrow fallbacks choose the nearest exact authorized destination", () => {
  const positions = [0, 2, 4];
  assert.equal(adjacentAllowedPosition(2, "up", positions), 0);
  assert.equal(adjacentAllowedPosition(2, "down", positions), 4);
  assert.equal(adjacentAllowedPosition(0, "up", positions), null);
  assert.equal(hasAlternatePosition(2, [2]), false);
  assert.equal(hasAlternatePosition(2, positions), true);
});

test("subparagraph creation stops at four levels", () => {
  assert.equal(MAX_PARAGRAPH_LEVELS, 4);
  assert.equal(canAddChildParagraph(0), true);
  assert.equal(canAddChildParagraph(2), true);
  assert.equal(canAddChildParagraph(3), false);
  assert.equal(canAddChildParagraph(4), false);
});

test("article delete warnings count the complete provision subtree", () => {
  const leaf = (id: string): DocParagraph => ({
    id,
    label: "",
    text: id,
    status: "confirmed",
    source_item_id: "",
    children: [],
  });
  const paragraphs = [
    { ...leaf("p1"), children: [{ ...leaf("p1.1"), children: [leaf("p1.1.1")] }] },
    leaf("p2"),
  ];
  assert.equal(paragraphSubtreeSize(paragraphs), 4);
});
