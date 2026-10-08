/**
 * The review queue's refs are the backend's refs, character for character.
 *
 * `buildQueue` numbers from the serialized labels, the backend from the tree
 * (`model.sibling_refs`). Both read `tests/fixtures/review_queue_refs.json`,
 * which the backend suite regenerates-or-fails from `iter_paragraphs` — so a
 * rule changed on one side alone turns one of the two suites red. The case
 * that matters is a preserved block: it takes no letter, and its own ref must
 * never be a provision's.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { askModelPrefill, buildQueue, siblingRefs } from "../src/lib/reviewQueue.ts";
import type { DocParagraph, SpecDoc } from "../src/types";

const here = dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(
  readFileSync(
    join(here, "..", "..", "tests", "fixtures", "review_queue_refs.json"),
    "utf8",
  ),
) as { doc: SpecDoc; rows: [string, string, string][] };

/** Every paragraph's ref, in document order, as the queue computes them. */
function allRefs(doc: SpecDoc): [string, string, string][] {
  const out: [string, string, string][] = [];
  const walk = (paragraphs: DocParagraph[], prefix: string) => {
    const refs = siblingRefs(paragraphs, prefix);
    paragraphs.forEach((p, i) => {
      out.push([refs[i], p.id, p.status]);
      walk(p.children, refs[i]);
    });
  };
  for (const part of doc.parts) {
    for (const article of part.articles) walk(article.paragraphs, article.number);
  }
  return out;
}

test("every ref the frontend builds is the backend's ref", () => {
  assert.deepEqual(allRefs(fixture.doc), fixture.rows);
});

test("a preserved block takes no letter and never shares a provision's ref", () => {
  const refs = fixture.rows.map((row) => row[0]);
  assert.ok(refs.includes("1.1 [preserved table after A]"));
  assert.ok(refs.includes("1.1 [preserved table 2 after A]"));
  assert.ok(refs.includes("1.1 [preserved table before A]"));
  assert.ok(refs.includes("1.1.B [preserved embedded object after 1]"));
  assert.ok(refs.includes("1.2 [preserved table]"));
  // The provision after three preserved blocks is still B.
  assert.ok(refs.includes("1.1.B"));
  assert.ok(!refs.includes("1.1.E"));
  assert.equal(new Set(refs).size, refs.length);
});

test("the queue carries those refs, imported first, then assumed", () => {
  const reviewable = fixture.rows.filter(
    ([, , status]) => status === "imported" || status === "assumed",
  );
  const expected = [
    ...reviewable.filter(([, , s]) => s === "imported"),
    ...reviewable.filter(([, , s]) => s === "assumed"),
  ].map(([ref, id]) => [ref, id]);
  assert.deepEqual(
    buildQueue(fixture.doc, "all").map((entry) => [entry.ref, entry.elementId]),
    expected,
  );
});

test("Ask model quotes the whole provision, however long", () => {
  // It used to clip at 80 characters with an ellipsis, so a long provision
  // reached the chat cut short.
  const text =
    "Provide a hydraulically calculated wet-pipe sprinkler system for the " +
    "data hall, designed to the density and remote area stated in Article " +
    "1.4, with the system demand plotted against the flow test adjusted " +
    "for a 10 psi safety margin.";
  assert.ok(text.length > 200);
  const prefill = askModelPrefill({ ref: "2.3.B", text });
  assert.equal(prefill, `Regarding 2.3.B "${text}": `);
  assert.ok(!prefill.includes("…"));
});
