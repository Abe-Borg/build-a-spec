import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const api = readFileSync(new URL("../src/lib/api.ts", import.meta.url), "utf8");
const types = readFileSync(new URL("../src/types.ts", import.meta.url), "utf8");

test("template API follows preview-then-commit and portable starter contract", () => {
  for (const route of [
    "/api/templates",
    "/api/templates/preview",
    "/api/templates/import",
    "/instantiate",
    "/export",
  ]) {
    assert.match(api, new RegExp(route.replaceAll("/", "\\/")));
  }
  assert.match(api, /preview_token/);
  assert.match(api, /ai_generalize/);
  assert.match(api, /Accept:\s*"text\/event-stream"/);
  assert.match(api, /template_preview/);
  assert.match(types, /diff\?: SectionDiffPayload/);
  assert.match(types, /source:\s*TemplateSource/);
  assert.match(types, /editable:\s*boolean/);
  assert.match(types, /module_available:\s*boolean/);
});

test("tutorial API carries idempotent workspace identity through every transition", () => {
  assert.match(api, /request_id/);
  assert.match(api, /tutorial_id/);
  assert.match(api, /workspace_id/);
  assert.match(api, /\/api\/tutorial\/scenario\/start/);
  assert.match(api, /\/api\/tutorial\/scenario\/finish/);
  assert.match(api, /\/api\/tutorial\/restore/);
  assert.match(api, /\/api\/tutorial\/keep/);
});
