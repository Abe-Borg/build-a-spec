import assert from "node:assert/strict";
import test from "node:test";

import {
  formatProjectHeading,
  projectDiscipline,
} from "../src/lib/projectHeading.ts";
import type { SpecDoc } from "../src/types.ts";

function doc(
  identity: SpecDoc["project_identity"] = {},
  profile: SpecDoc["project_profile"] = {},
): SpecDoc {
  return {
    section: { number: "", title: "" },
    parts: [],
    version: { index: 0, count: 1 },
    project_identity: identity,
    project_profile: profile,
  };
}

test("heading stays blank until discipline is established", () => {
  assert.equal(formatProjectHeading(null), "");
  assert.equal(
    formatProjectHeading(
      doc(
        { project_type: "Data Center" },
        { city: "Phoenix", state_or_province: "AZ" },
      ),
    ),
    "",
  );
});

test("heading shows only discipline until type and complete location exist", () => {
  assert.equal(
    formatProjectHeading(doc({ discipline: "Mechanical" })),
    "Mechanical",
  );
  assert.equal(
    formatProjectHeading(
      doc(
        { discipline: "Mechanical", project_type: "Data Center" },
        { city: "Phoenix" },
      ),
    ),
    "Mechanical",
  );
  assert.equal(
    formatProjectHeading(
      doc(
        { discipline: "Mechanical" },
        { city: "Phoenix", state_or_province: "AZ" },
      ),
    ),
    "Mechanical",
  );
});

test("heading renders discipline, facility type, and city/region", () => {
  assert.equal(
    formatProjectHeading(
      doc(
        { discipline: "Mechanical", project_type: "Data Center" },
        {
          city: "Phoenix",
          state_or_province: "AZ",
          country: "US",
        },
      ),
    ),
    "Mechanical · Data Center · Phoenix, AZ",
  );
});

test("legacy discipline is fallback only", () => {
  assert.equal(projectDiscipline(doc(), "Electrical"), "Electrical");
  assert.equal(
    projectDiscipline(doc({ discipline: "Structural" }), "Electrical"),
    "Structural",
  );
});
