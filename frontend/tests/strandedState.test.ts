/**
 * State a failure must not strand — pinned at the source level, because
 * none of these components has a DOM harness and `App.tsx` never will
 * (the sessionBundle.test.ts / tour.test.ts idiom).
 *
 * Three defects of one class: a `busy` flag released only on the happy
 * path (Settings), a timer with no unmount cleanup (the review walk's PART
 * hold), and a poll whose failure blanked the screen (readiness, usage).
 * Each fix is a few lines that a later edit could quietly undo; these
 * tests are what would notice.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const here = dirname(fileURLToPath(import.meta.url));
const read = (...parts: string[]) =>
  readFileSync(join(here, "..", "src", ...parts), "utf8");

/** The body of `const <name> = async () => { … };` (or `useCallback(() => {`). */
function handlerBody(source: string, name: string): string {
  const start = source.indexOf(`const ${name} = `);
  assert.ok(start >= 0, `${name} not found`);
  const open = source.indexOf("{", start);
  let depth = 0;
  for (let i = open; i < source.length; i += 1) {
    if (source[i] === "{") depth += 1;
    else if (source[i] === "}") {
      depth -= 1;
      if (depth === 0) return source.slice(open, i + 1);
    }
  }
  assert.fail(`${name} body never closes`);
}

test("every Settings key handler releases busy on every exit", () => {
  const settings = read("components", "SettingsPanel.tsx");
  for (const name of ["test", "saveAfterTest", "remove"]) {
    const body = handlerBody(settings, name);
    assert.match(body, /setBusy\(true\)/, `${name} never sets busy`);
    // The release lives in a `finally`, not on the happy path: `testKey`
    // never throws on a non-2xx, so the only way it REJECTS is a backend
    // that is gone — exactly when a stranded `busy` would freeze the panel
    // for the rest of the app's life (it is an early return while closed,
    // not an unmount).
    assert.match(
      body,
      /finally \{\s*setBusy\(false\);\s*\}/,
      `${name} does not release busy in a finally`,
    );
    assert.equal(
      (body.match(/setBusy\(false\)/g) ?? []).length,
      1,
      `${name} releases busy somewhere other than its finally`,
    );
    // No await outside the try: the first statement after `try {` must come
    // before the first `await`.
    const firstAwait = body.indexOf("await ");
    const tryStart = body.indexOf("try {");
    assert.ok(firstAwait > tryStart && tryStart >= 0, `${name} awaits outside its try`);
  }
});

test("a failed key request says what failed, not 'Key rejected'", () => {
  const settings = read("components", "SettingsPanel.tsx");
  assert.match(handlerBody(settings, "test"), /label: "Could not test the key"/);
  assert.match(handlerBody(settings, "remove"), /label: "Could not remove the key"/);
  assert.match(settings, /testResult\.label \?\? "Key rejected"/);
});

test("both review-walk hold timers are cleared on unmount", () => {
  const drawer = read("components", "ReviewDrawer.tsx");
  for (const timer of ["holdTimer", "holdPartTimer"]) {
    assert.match(
      drawer,
      new RegExp(
        `useEffect\\(\\(\\) => \\(\\) => window\\.clearTimeout\\(${timer}\\.current\\), \\[\\]\\)`,
      ),
      `${timer} has no unmount cleanup — a drawer unmounted mid-hold fires a real bulk edit`,
    );
  }
});

test("readiness and usage keep their last-good answer on a dropped poll", () => {
  const app = read("App.tsx");
  for (const [name, setter] of [
    ["refreshReadiness", "setReadiness"],
    ["refreshUsage", "setUsage"],
  ] as const) {
    const body = handlerBody(app, name);
    assert.doesNotMatch(
      body,
      new RegExp(`${setter}\\(null\\)`),
      `${name} blanks the screen on a failed fetch`,
    );
    // Ordered by request, and a failure claims its rank (`drop`), so an
    // older success still in flight cannot land on top of a newer answer.
    assert.match(body, /\.next\(\)/, `${name} claims no rank`);
    assert.match(body, /\.accept\(rank, value, /, `${name} applies outside the latch`);
    assert.match(body, /\.drop\(rank\)/, `${name} does not claim its rank on failure`);
  }
  // The deliberate blanking on a NEW session is a different thing and stays.
  assert.match(handlerBody(app, "clearSessionState"), /setReadiness\(null\)/);
  assert.match(handlerBody(app, "clearSessionState"), /setUsage\(null\)/);
});
