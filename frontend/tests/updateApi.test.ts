import assert from "node:assert/strict";
import test from "node:test";

import { UpdateInstallError, installUpdate } from "../src/lib/api.ts";

function stubFetch(
  t: { after: (fn: () => void) => void },
  status: number,
  body: Record<string, unknown>,
) {
  const originalFetch = globalThis.fetch;
  const captured: { input?: RequestInfo | URL; init?: RequestInit } = {};
  t.after(() => {
    globalThis.fetch = originalFetch;
  });
  globalThis.fetch = async (input, init) => {
    captured.input = input;
    captured.init = init;
    return new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  };
  return captured;
}

test("installing an update always posts the explicit unsaved-work acknowledgement", async (t) => {
  const captured = stubFetch(t, 200, { ok: true, version: "9.9.9" });

  await installUpdate();
  assert.equal(captured.input, "/api/update/install");
  assert.equal(captured.init?.method, "POST");
  assert.deepEqual(JSON.parse(String(captured.init?.body)), {
    acknowledge_unsaved: false,
  });

  await installUpdate(true);
  assert.deepEqual(JSON.parse(String(captured.init?.body)), {
    acknowledge_unsaved: true,
  });
});

test("a refused install surfaces the server's code, so the app can ask instead of fail", async (t) => {
  stubFetch(t, 409, {
    ok: false,
    code: "unsaved_progress",
    error: "This session has unsaved work.",
  });

  await assert.rejects(installUpdate(), (error: unknown) => {
    assert.ok(error instanceof UpdateInstallError);
    assert.equal(error.status, 409);
    assert.equal(error.code, "unsaved_progress");
    assert.equal(error.message, "This session has unsaved work.");
    return true;
  });
});

test("a plain failure carries no code and keeps the server's message", async (t) => {
  stubFetch(t, 502, { ok: false, error: "connection reset by peer" });

  await assert.rejects(installUpdate(true), (error: unknown) => {
    assert.ok(error instanceof UpdateInstallError);
    assert.equal(error.status, 502);
    assert.equal(error.code, undefined);
    assert.equal(error.message, "connection reset by peer");
    return true;
  });
});
