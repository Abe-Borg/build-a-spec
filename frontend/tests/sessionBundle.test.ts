/**
 * A session bundle (tutorial start, scenario swap, template start) hydrates
 * the document through the same `applyDocPayload` an ordinary payload uses,
 * but it does so through an EXPLICIT field mapping in `applySessionBundle`.
 * A field added to the payload and forgotten in the mapping is silently
 * defaulted — which is how the import tutorial's detached practice copy
 * arrived with `source_detached: false` and every editing control disabled
 * (Codex review, PR #145).
 *
 * This is a text-level contract, like the capability-coverage test: every
 * field name `applyDocPayload` declares in its parameter type must be
 * spelled out in the object `applySessionBundle` passes it. It reads the
 * source rather than rendering React, in keeping with the no-vitest rule.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const here = dirname(fileURLToPath(import.meta.url));
const app = readFileSync(join(here, "..", "src", "App.tsx"), "utf8");

function payloadFields(): string[] {
  const start = app.indexOf("const applyDocPayload = (payload: {");
  assert.ok(start >= 0, "applyDocPayload parameter type not found");
  const end = app.indexOf("}): boolean =>", start);
  assert.ok(end > start, "applyDocPayload parameter type end not found");
  const body = app.slice(start, end);
  const names = [...body.matchAll(/^\s+([a-z_]+)\??:/gm)].map((m) => m[1]);
  assert.ok(names.length > 10, `unexpectedly few payload fields: ${names}`);
  return names;
}

function bundleMappingFields(): string[] {
  const start = app.indexOf("const applySessionBundle = ");
  assert.ok(start >= 0, "applySessionBundle not found");
  const call = app.indexOf("applyDocPayload({", start);
  assert.ok(call > start, "applySessionBundle does not call applyDocPayload");
  const end = app.indexOf("});", call);
  const body = app.slice(call, end);
  return [...body.matchAll(/^\s+([a-z_]+):/gm)].map((m) => m[1]);
}

test("a session bundle forwards every field applyDocPayload accepts", () => {
  const expected = payloadFields();
  const forwarded = new Set(bundleMappingFields());
  const missing = expected.filter((name) => !forwarded.has(name));
  assert.deepEqual(
    missing,
    [],
    `applySessionBundle drops payload field(s): ${missing.join(", ")}`,
  );
});

test("the detached flag in particular is forwarded, not defaulted", () => {
  assert.ok(
    bundleMappingFields().includes("source_detached"),
    "source_detached must be mapped from the bundle",
  );
});
