import assert from "node:assert/strict";
import test from "node:test";

import {
  applyQc,
  QcStartError,
  previewQcApply,
  startQc,
} from "../src/lib/api.ts";

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

test("Final QC apply preview posts the selected batch and workspace lease", async (t) => {
  const originalFetch = globalThis.fetch;
  let capturedInput: RequestInfo | URL | undefined;
  let capturedInit: RequestInit | undefined;
  t.after(() => {
    globalThis.fetch = originalFetch;
  });
  const preview = {
    ok: true as const,
    basis: {
      workspace_id: 7,
      generation: 11,
      run_id: "qc-run-preview",
      input_fingerprint: "a".repeat(64),
      document_version: 3,
      document_fingerprint: "b".repeat(64),
      result_fingerprint: "c".repeat(64),
      selected_finding_ids: ["qc-a", "qc-b"],
      binding_fingerprint: "d".repeat(64),
    },
    decisions: [],
    operation_counts: { proposed: 0, unique: 0, duplicate: 0, applyable: 0 },
    deduplications: [],
    conflicts: [],
    applyable_finding_ids: [],
    applyable_operations: [],
  };
  globalThis.fetch = async (input, init) => {
    capturedInput = input;
    capturedInit = init;
    return new Response(JSON.stringify(preview), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };

  assert.deepEqual(
    await previewQcApply(["qc-a", "qc-b"], {
      workspaceId: 7,
      generation: 11,
    }),
    preview,
  );
  assert.equal(capturedInput, "/api/qc/apply/preview");
  assert.equal(capturedInit?.method, "POST");
  assert.deepEqual(JSON.parse(String(capturedInit?.body)), {
    finding_ids: ["qc-a", "qc-b"],
    workspace_id: 7,
    generation: 11,
  });
});

test("Final QC apply preview surfaces the server's fail-closed reason", async (t) => {
  const originalFetch = globalThis.fetch;
  t.after(() => {
    globalThis.fetch = originalFetch;
  });
  globalThis.fetch = async () =>
    new Response(
      JSON.stringify({
        ok: false,
        error: "Final QC is stale; request a fresh preview.",
      }),
      { status: 409, headers: { "Content-Type": "application/json" } },
    );

  await assert.rejects(
    previewQcApply(["qc-a"]),
    /Final QC is stale; request a fresh preview\./,
  );
});

test("confirmed Final QC apply sends the exact preview basis", async (t) => {
  const originalFetch = globalThis.fetch;
  let capturedBody: Record<string, unknown> | undefined;
  t.after(() => {
    globalThis.fetch = originalFetch;
  });
  const basis = {
    workspace_id: 7,
    generation: 11,
    run_id: "qc-run-preview",
    input_fingerprint: "a".repeat(64),
    document_version: 3,
    document_fingerprint: "b".repeat(64),
    result_fingerprint: "c".repeat(64),
    selected_finding_ids: ["qc-a", "qc-b"],
    binding_fingerprint: "d".repeat(64),
  };
  globalThis.fetch = async (_input, init) => {
    capturedBody = JSON.parse(String(init?.body));
    return new Response(
      JSON.stringify({
        ok: true,
        outcomes: { "qc-a": "applied" },
        section: { number: "", title: "" },
        parts: [],
        version: { index: 1, count: 2 },
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  };

  await applyQc(
    ["qc-a"],
    { workspaceId: 7, generation: 11 },
    basis,
  );
  assert.deepEqual(capturedBody, {
    finding_ids: ["qc-a"],
    workspace_id: 7,
    generation: 11,
    preview_basis: basis,
  });
});
