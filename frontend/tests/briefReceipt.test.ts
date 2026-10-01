/**
 * On-screen receipt lines for a project brief. The brief file, the seed and
 * the model context keep every word; these formatters only decide what a
 * glance at the two dialogs shows.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import type { ProjectBriefManifest } from "../src/types.ts";
import {
  editionDesignation,
  editionsReceipt,
  factsReceipt,
  researchReceipt,
} from "../src/lib/briefReceipt.ts";

type Research = NonNullable<ProjectBriefManifest["research"]>;

function research(overrides: Partial<Research> = {}): Research {
  return {
    items: 154,
    grounded: 117,
    rounds: 1,
    dimensions_completed: 4,
    dimensions_recorded: 4,
    dimensions_declared: 4,
    last_research_date: "2026-08-02",
    sections: ["21 13 13"],
    ...overrides,
  };
}

test("an edition stops at the year", () => {
  assert.equal(
    editionDesignation(
      "NFPA 13 — 2019 (research r-abc corroborated, consistent with the 2018 IBC, incorporated by reference)",
    ),
    "NFPA 13 — 2019",
  );
  assert.equal(editionDesignation("NFPA 14 — 2022"), "NFPA 14 — 2022");
});

test("an alias and a reapproval suffix survive the basis cut", () => {
  // The first " (" is the alias, not the basis. Cutting there drops the year.
  assert.equal(
    editionDesignation("NFPA 70 (NEC) — 2023 (basis)"),
    "NFPA 70 (NEC) — 2023",
  );
  assert.equal(
    editionDesignation(
      "NFPA 13 — 2016 (R2019) (AHJ adopted the 2016 edition with a reapproval)",
    ),
    "NFPA 13 — 2016 (R2019)",
  );
  assert.equal(
    editionDesignation("NFPA 13 — 2019 (see NFPA 13 (2016) section 8)"),
    "NFPA 13 — 2019",
  );
  assert.equal(
    editionsReceipt([
      "NFPA 70 (NEC) — 2023 (basis)",
      "NFPA 13 — 2016 (R2019) (AHJ adopted the 2016 edition with a reapproval)",
    ]),
    "NFPA 70 (NEC) — 2023; NFPA 13 — 2016 (R2019) (2)",
  );
});

test("editions join as designations and a count", () => {
  assert.equal(
    editionsReceipt([
      "NFPA 13 — 2019 (research r-abc corroborated, incorporated by reference)",
      "NFPA 14 — 2022",
    ]),
    "NFPA 13 — 2019; NFPA 14 — 2022 (2)",
  );
  assert.equal(editionsReceipt([]), "");
  assert.equal(editionsReceipt(["   ", " (basis only)"]), "");
});

test("research is counts, and the date stays off the line", () => {
  const line = researchReceipt(research());
  assert.equal(line, "154 findings, 117 grounded, 4 of 4 areas, 1 round");
  assert.equal(line.includes("2026-08-02"), false);
  assert.equal(
    researchReceipt(
      research({
        items: 1,
        grounded: 1,
        rounds: 1,
        dimensions_completed: 1,
        dimensions_recorded: 1,
        dimensions_declared: 1,
      }),
    ),
    "1 finding, 1 grounded, 1 of 1 area, 1 round",
  );
  assert.equal(
    researchReceipt(
      research({
        items: 2,
        dimensions_completed: 1,
        dimensions_recorded: 4,
        dimensions_declared: null,
      }),
    ),
    "2 findings, 117 grounded, 1 of 4 areas, 1 round",
  );
});

test("facts name every count, zeros included", () => {
  assert.equal(
    factsReceipt({ active: 11, confirmed: 10, assumed: 1, superseded: 1 }),
    "11 active, 10 confirmed, 1 assumed, 1 retired",
  );
  assert.equal(
    factsReceipt({ active: 0, confirmed: 0, assumed: 0, superseded: 0 }),
    "0 active, 0 confirmed, 0 assumed, 0 retired",
  );
});

test("both receipts render through the helpers and stay one line", () => {
  for (const file of [
    "../src/components/NewSessionDialog.tsx",
    "../src/components/NextSectionDialog.tsx",
  ]) {
    const source = readFileSync(new URL(file, import.meta.url), "utf8");
    assert.match(source, /from "\.\.\/lib\/briefReceipt"/);
    assert.match(source, /editionsReceipt\(/);
    assert.match(source, /researchReceipt\(/);
    assert.match(source, /factsReceipt\(/);
    assert.match(source, /truncate text-ink-dim/);
    assert.match(source, /title=\{value\}/);
    assert.doesNotMatch(source, /last researched/);
    assert.doesNotMatch(source, /standards\.join/);
  }
});
