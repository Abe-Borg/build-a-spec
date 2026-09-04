import assert from "node:assert/strict";
import { test } from "node:test";

import {
  factCounts,
  factProvenance,
  factSeq,
  groupProjectFacts,
  justSupersededIds,
} from "../src/lib/projectFacts.ts";
import type { ProjectFact } from "../src/types.ts";

function fact(overrides: Partial<ProjectFact> & { pid: string }): ProjectFact {
  return {
    statement: "a fact",
    detail: "",
    scope: "project",
    section: "",
    status: "confirmed",
    source_kind: "user",
    source_ref: "",
    recorded_in: "21 13 13",
    recorded_at: "2026-09-04",
    superseded_by: "",
    supersede_reason: "",
    ...overrides,
  };
}

test("facts group by reach, and a section-scoped one is 'own' only for the section on screen", () => {
  const items = [
    fact({ pid: "pf-1" }),
    fact({ pid: "pf-2", scope: "discipline", status: "assumed" }),
    fact({ pid: "pf-3", scope: "section", section: "21 13 13" }),
    fact({ pid: "pf-4", scope: "section", section: "21 13 19" }),
    fact({ pid: "pf-5", status: "superseded", supersede_reason: "moot" }),
  ];
  const groups = groupProjectFacts(items, "21 13 19");
  assert.deepEqual(groups.project.map((f) => f.pid), ["pf-1"]);
  assert.deepEqual(groups.discipline.map((f) => f.pid), ["pf-2"]);
  assert.deepEqual(groups.other.map((f) => f.pid), ["pf-3"]);
  assert.deepEqual(groups.own.map((f) => f.pid), ["pf-4"]);
  assert.deepEqual(groups.superseded.map((f) => f.pid), ["pf-5"]);
  // No section number yet: every section-scoped fact is someone else's.
  const blank = groupProjectFacts(items, "");
  assert.deepEqual(blank.other.map((f) => f.pid), ["pf-3", "pf-4"]);
  assert.deepEqual(blank.own, []);
  // Whitespace in a section number never splits a group.
  assert.deepEqual(groupProjectFacts(items, " 21  13 19 ").own.map((f) => f.pid), ["pf-4"]);
});

test("within a group, confirmed facts lead and recording order breaks ties", () => {
  const items = [
    fact({ pid: "pf-3", status: "assumed" }),
    fact({ pid: "pf-2" }),
    fact({ pid: "pf-1", status: "assumed" }),
  ];
  assert.deepEqual(
    groupProjectFacts(items, "").project.map((f) => f.pid),
    ["pf-2", "pf-1", "pf-3"],
  );
  assert.equal(factSeq("pf-12"), 12);
  assert.equal(factSeq("junk"), 0);
});

test("counts split active facts by status and retired ones apart", () => {
  const items = [
    fact({ pid: "pf-1" }),
    fact({ pid: "pf-2", status: "assumed" }),
    fact({ pid: "pf-3", status: "superseded" }),
  ];
  assert.deepEqual(factCounts(items), { active: 2, confirmed: 1, assumed: 1, superseded: 1 });
  assert.deepEqual(factCounts([]), { active: 0, confirmed: 0, assumed: 0, superseded: 0 });
});

test("a newly retired fact is reported once, however it was retired", () => {
  const before = [fact({ pid: "pf-1" }), fact({ pid: "pf-2", status: "superseded" })];
  const after = [
    fact({ pid: "pf-1", status: "superseded" }),
    fact({ pid: "pf-2", status: "superseded" }),
  ];
  assert.deepEqual([...justSupersededIds(before, after)], ["pf-1"]);
  // The first render animates nothing: a restored project's retired
  // history is not a fresh transition.
  assert.equal(justSupersededIds(null, after).size, 0);
  assert.equal(justSupersededIds([], after).size, 0);
  // A fact that was never in the previous snapshot is not a transition.
  assert.equal(justSupersededIds([fact({ pid: "pf-9" })], after).size, 0);
});

test("the provenance line names reach, status, recording section and a non-model source", () => {
  assert.equal(
    factProvenance(fact({ pid: "pf-1", source_kind: "reference", source_ref: "ref-1" })),
    "project · confirmed · recorded in 21 13 13 · source: reference ref-1",
  );
  assert.equal(
    factProvenance(fact({ pid: "pf-2", scope: "section", section: "21 30 00", source_kind: "model", recorded_in: "" })),
    "section 21 30 00 · confirmed",
  );
});
