import assert from "node:assert/strict";
import test from "node:test";

import { QcStartError, startQc } from "../src/lib/api.ts";

test("Final QC start always posts the explicit scope-mismatch acknowledgement", async (t) => {
  const originalFetch = globalThis.fetch;
  let capturedInput: RequestInfo | URL | undefined;
  let capturedInit: RequestInit | undefined;
  t.after(() => {
    globalThis.fetch = originalFetch;
  });
  globalThis.fetch = async (input, init) => {
    capturedInput = input;
    capturedInit = init;
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };

  await startQc(true);

  assert.equal(capturedInput, "/api/qc/start");
  assert.equal(capturedInit?.method, "POST");
  assert.deepEqual(JSON.parse(String(capturedInit?.body)), {
    acknowledge_scope_mismatch: true,
  });

  await startQc();
  assert.deepEqual(JSON.parse(String(capturedInit?.body)), {
    acknowledge_scope_mismatch: false,
  });
});

test("Final QC start preserves structured mismatch details from a 409", async (t) => {
  const originalFetch = globalThis.fetch;
  t.after(() => {
    globalThis.fetch = originalFetch;
  });
  const compatibility = {
    status: "mismatch" as const,
    section_number: "23 74 13",
    section_title: "Packaged Outdoor Air-Handling Units",
    module_id: "hyperscale_fire",
    module_display_name: "Hyperscale Data Center — Fire Suppression",
    allowed_sections: [{ number: "21 13 13", title: "Wet-Pipe Sprinkler Systems" }],
    message: "The current section is outside this module's catalog.",
  };
  globalThis.fetch = async () =>
    new Response(
      JSON.stringify({
        ok: false,
        error: "Acknowledge the section/module mismatch before starting Final QC.",
        code: "module_section_mismatch",
        module_section_compatibility: compatibility,
      }),
      { status: 409, headers: { "Content-Type": "application/json" } },
    );

  await assert.rejects(
    startQc(false),
    (error: unknown) => {
      assert.ok(error instanceof QcStartError);
      assert.equal(error.status, 409);
      assert.equal(error.code, "module_section_mismatch");
      assert.deepEqual(error.moduleSectionCompatibility, compatibility);
      return true;
    },
  );
});
