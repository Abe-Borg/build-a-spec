import assert from "node:assert/strict";
import test from "node:test";

import { startResearch } from "../src/lib/api.ts";

/** Capture one request body from `startResearch`. */
async function bodyOf(
  t: { after: (fn: () => void) => void },
  run: () => Promise<void>,
): Promise<Record<string, unknown>> {
  const originalFetch = globalThis.fetch;
  let captured: RequestInit | undefined;
  t.after(() => {
    globalThis.fetch = originalFetch;
  });
  globalThis.fetch = async (_input, init) => {
    captured = init;
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  await run();
  return JSON.parse(String(captured?.body));
}

test("a selected round sends the areas the user picked", async (t) => {
  const body = await bodyOf(t, () =>
    startResearch({}, "selected", ["governing_codes", "site_environment"]),
  );
  assert.equal(body.scope, "selected");
  assert.deepEqual(body.dimension_ids, [
    "governing_codes",
    "site_environment",
  ]);
});

test("a full round never carries a stray selection", async (t) => {
  // The server refuses `dimension_ids` on any scope but "selected", and
  // deliberately so: silently ignoring the list is how a user pays for four
  // areas after picking one. The client must not send one at all.
  for (const scope of ["all", "gaps"] as const) {
    const body = await bodyOf(t, () =>
      startResearch({ workspaceId: 3, generation: 9 }, scope),
    );
    assert.equal(body.scope, scope);
    assert.ok(
      !("dimension_ids" in body),
      `${scope} must not carry a dimension list`,
    );
  }
});

test("an empty selection is still sent, so the server can refuse it", async (t) => {
  // Not silently upgraded to a full round on the client: the refusal and
  // its reason are the server's to give.
  const body = await bodyOf(t, () => startResearch({}, "selected", []));
  assert.deepEqual(body.dimension_ids, []);
});
