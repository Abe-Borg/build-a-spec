/**
 * A research finding carries the passage it quotes and the condition it
 * applies under (the prompt-review follow-up, item 7, 2026-10-13). The
 * server decides whether the quote was found on a fetched page; the report
 * shows that, and never shows an unfound quote as the source's words.
 * Helper tests plus source pins: no DOM, no network.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { QUOTE_NOT_FOUND, appliesWhen, researchQuote } from "../src/lib/researchQuote.ts";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");
const report = read("../src/components/ResearchReportModal.tsx");
const types = read("../src/types.ts");
const engine = read("../../backend/research/engine.py");

test("a found quote names the host it was found on", () => {
  const view = researchQuote({
    quote: "  Sprinklers shall be installed throughout.  ",
    quote_source: "https://www.example.gov/code/903",
  });
  assert.deepEqual(view, {
    text: "Sprinklers shall be installed throughout.",
    found: true,
    source: "https://www.example.gov/code/903",
    label: "Found on example.gov",
  });
});

test("a quote without a source reads as not found, never as the page", () => {
  const view = researchQuote({ quote: "Sprinklers shall be installed.", quote_source: "" });
  assert.equal(view?.found, false);
  assert.equal(view?.label, QUOTE_NOT_FOUND);
  assert.equal(researchQuote({ quote: "Text", quote_source: undefined })?.found, false);
});

test("no quote, no view; an older item's missing fields read as empty", () => {
  assert.equal(researchQuote({ quote: "", quote_source: "https://a.example" }), null);
  assert.equal(researchQuote({}), null);
  assert.equal(appliesWhen({}), "");
  assert.equal(appliesWhen({ applies_when: "  only where the AHJ adopted the 2024 IFC " }),
    "only where the AHJ adopted the 2024 IFC");
});

test("the not-found words match the backend's render", () => {
  const match = engine.match(/QUOTE_NOT_FOUND_LABEL = "([^"]+)"/);
  assert.ok(match, "QUOTE_NOT_FOUND_LABEL is defined in backend/research/engine.py");
  assert.equal(QUOTE_NOT_FOUND.toLowerCase(), match![1].toLowerCase());
});

test("the report renders the condition and the quote with its label", () => {
  assert.match(types, /applies_when\?: string;/);
  assert.match(types, /quote\?: string;/);
  assert.match(types, /quote_source\?: string;/);
  assert.match(report, /researchQuote\(item\)/);
  assert.match(report, /Applies when:/);
  assert.match(report, /\(\{quote\.label\}\)/);
});
