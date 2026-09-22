import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { DEFAULT_QC_MODEL, qcModelLabel } from "../src/lib/qcModel.ts";

test("the default QC model is Opus 5.5 and claims to out-reason the drafter", () => {
  assert.equal(DEFAULT_QC_MODEL, "claude-opus-5-5");
  assert.deepEqual(qcModelLabel(undefined), {
    id: "claude-opus-5-5",
    name: "Claude Opus 5.5",
    strongerThanDrafter: true,
  });
  assert.equal(qcModelLabel("  ").name, "Claude Opus 5.5");
});

test("an override is named as configured, and only a stronger model claims it", () => {
  assert.equal(qcModelLabel("claude-opus-5").name, "Claude Opus 5");
  assert.equal(qcModelLabel("claude-sonnet-5").strongerThanDrafter, false);
  const unknown = qcModelLabel("claude-something-new");
  assert.equal(unknown.name, "claude-something-new");
  assert.equal(unknown.strongerThanDrafter, false);
});

test("the QC drawer's consent copy never hardcodes a model name", () => {
  const src = readFileSync(
    new URL("../src/components/QCDrawer.tsx", import.meta.url),
    "utf8",
  );
  const code = src
    .split("\n")
    .filter((line) => !/^\s*(\*|\/\/|\/\*)/.test(line))
    .join("\n");
  assert.doesNotMatch(code, /Opus 5(\.5)?/);
  assert.match(code, /qcModelLabel\(qcModel\)/);
});
