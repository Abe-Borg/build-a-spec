import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  DEFAULT_QC_MODEL,
  DEFAULT_QC_VERIFIER_MODEL,
  qcModelLabel,
  qcRunsOnCopy,
  qcVerifierModelLabel,
} from "../src/lib/qcModel.ts";

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
  assert.doesNotMatch(code, /Sonnet 5(\.5)?/);
  assert.match(code, /qcModelLabel\(qcModel\)/);
  assert.match(code, /qcVerifierModelLabel\(qcVerifierModel\)/);
  assert.match(code, /qcRunsOnCopy\(qcModel, qcVerifierModel\)/);
});

test("the verifier seats default to Sonnet 5.5 and are named on their own", () => {
  // Owner decision, 2026-10-08: the lenses stay on Opus 5.5, the seats that
  // check each finding run on Sonnet 5.5 (`BUILD_A_SPEC_QC_VERIFIER_MODEL`).
  assert.equal(DEFAULT_QC_VERIFIER_MODEL, "claude-sonnet-5-5");
  assert.equal(qcVerifierModelLabel(undefined).name, "Claude Sonnet 5.5");
  assert.equal(qcVerifierModelLabel(" ").id, "claude-sonnet-5-5");
  assert.equal(qcVerifierModelLabel("claude-opus-5-5").name, "Claude Opus 5.5");
});

test("the consent line names both models only when they differ", () => {
  assert.equal(
    qcRunsOnCopy(undefined, undefined),
    "Runs on Claude Opus 5.5 — a stronger reviewer than the drafter; " +
      "Claude Sonnet 5.5 reviewers then check each finding.",
  );
  assert.equal(
    qcRunsOnCopy("claude-opus-5-5", "claude-opus-5-5"),
    "Runs on Claude Opus 5.5 — a stronger reviewer than the drafter.",
  );
  // A weaker lens model makes no strength claim, whatever the seats run on.
  assert.equal(
    qcRunsOnCopy("claude-sonnet-5", "claude-sonnet-5-5"),
    "Runs on Claude Sonnet 5; Claude Sonnet 5.5 reviewers then check each finding.",
  );
});
