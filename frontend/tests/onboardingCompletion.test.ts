/**
 * The guided tour's completion outlives the launch.
 *
 * The packaged app's WebView keeps no browser storage between launches
 * (pywebview's private mode, a fresh port every time), so completion is kept
 * by the server (`/api/ui/onboarding`). The store's rules run directly; the
 * wiring is pinned at the source level, the way the rest of this suite pins
 * components (there is no DOM harness).
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  getOnboardingCompletion,
  saveOnboardingCompletion,
} from "../src/lib/api.ts";
import {
  ONBOARDING_COMPLETION_VERSION,
  completedFromApi,
  createOnboardingCompletionStore,
} from "../src/lib/onboardingCompletion.ts";

const read = (path: string) =>
  readFileSync(new URL(`../src/${path}`, import.meta.url), "utf8");
const chat = read("components/Chat.tsx");
const app = read("App.tsx");
const hook = read("lib/useOnboarding.ts");
const storage = read("lib/onboardingStorage.ts");

/** A read the test resolves or rejects by hand, and a write it records. */
function fakeIo() {
  let resolveRead: (payload: unknown) => void = () => {};
  let rejectRead: (error: unknown) => void = () => {};
  const writes: number[] = [];
  let reads = 0;
  let failWrite = false;
  return {
    io: {
      read: () => {
        reads += 1;
        return new Promise<unknown>((resolve, reject) => {
          resolveRead = resolve;
          rejectRead = reject;
        });
      },
      write: async (version: number) => {
        writes.push(version);
        if (failWrite) throw new Error("disk full");
      },
    },
    resolveRead: (payload: unknown) => resolveRead(payload),
    rejectRead: (error: unknown) => rejectRead(error),
    writes,
    reads: () => reads,
    failWrites: () => {
      failWrite = true;
    },
  };
}

test("only the current version, as a real number, counts as finished", () => {
  assert.equal(
    completedFromApi({ completed_version: ONBOARDING_COMPLETION_VERSION }),
    true,
  );
  for (const payload of [
    null,
    undefined,
    7,
    "2",
    {},
    { completed_version: null },
    { completed_version: String(ONBOARDING_COMPLETION_VERSION) },
    { completed_version: ONBOARDING_COMPLETION_VERSION - 1 },
    { completed_version: ONBOARDING_COMPLETION_VERSION + 1 },
    { completed_version: true },
  ]) {
    assert.equal(completedFromApi(payload), false, JSON.stringify(payload));
  }
});

test("unknown until read, then the saved answer; the read happens once", async () => {
  const fake = fakeIo();
  const store = createOnboardingCompletionStore(fake.io);
  let notified = 0;
  store.subscribe(() => {
    notified += 1;
  });
  assert.equal(store.get(), null);
  const loading = store.load();
  store.load();
  assert.equal(fake.reads(), 1);
  fake.resolveRead({ ok: true, completed_version: ONBOARDING_COMPLETION_VERSION });
  await loading;
  assert.equal(store.get(), true);
  assert.equal(notified, 1);
});

test("a failed read is 'not completed', never stuck unknown", async () => {
  const fake = fakeIo();
  const store = createOnboardingCompletionStore(fake.io);
  const loading = store.load();
  fake.rejectRead(new Error("ui onboarding 500"));
  await loading;
  assert.equal(store.get(), false);
});

test("finishing the tour shows at once and is saved for the next launch", async () => {
  const fake = fakeIo();
  const store = createOnboardingCompletionStore(fake.io);
  const loading = store.load();
  fake.resolveRead({ ok: true, completed_version: null });
  await loading;
  assert.equal(store.get(), false);
  store.markCompleted();
  assert.equal(store.get(), true);
  assert.deepEqual(fake.writes, [ONBOARDING_COMPLETION_VERSION]);
});

test("a read that lands after the tour finished does not undo it", async () => {
  const fake = fakeIo();
  const store = createOnboardingCompletionStore(fake.io);
  const loading = store.load();
  store.markCompleted();
  // The read began before the finish and answers with the old file.
  fake.resolveRead({ ok: true, completed_version: null });
  await loading;
  assert.equal(store.get(), true);
});

test("a failed save keeps this launch's answer and says nothing loud", async (t) => {
  const fake = fakeIo();
  fake.failWrites();
  const store = createOnboardingCompletionStore(fake.io);
  const errors = t.mock.method(console, "error", () => {});
  const debug = t.mock.method(console, "debug", () => {});
  store.markCompleted();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(store.get(), true);
  assert.equal(errors.mock.callCount(), 0);
  assert.equal(debug.mock.callCount(), 1);
});

test("finishing waits for the save to settle before it resolves", async () => {
  let finishWrite: () => void = () => {};
  const store = createOnboardingCompletionStore({
    read: async () => ({ completed_version: null }),
    write: () =>
      new Promise<void>((resolve) => {
        finishWrite = resolve;
      }),
  });
  let settled = false;
  const done = store.markCompleted().then(() => {
    settled = true;
  });
  // Shown at once, but not settled until the file is written: the tour's
  // ending waits on this so a window closed right after Finish cannot abort
  // the save (Codex, PR #218).
  assert.equal(store.get(), true);
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(settled, false);
  finishWrite();
  await done;
  assert.equal(settled, true);
});

test("a hung save cannot strand the finishing card", async () => {
  const store = createOnboardingCompletionStore(
    {
      read: async () => ({ completed_version: null }),
      write: () => new Promise<void>(() => {}),
    },
    20,
  );
  // Raced against a timer of the test's own, so an unbounded wait fails
  // here instead of leaving a promise the runner would merely cancel.
  let testTimer: ReturnType<typeof setTimeout> | undefined;
  const winner = await Promise.race([
    store.markCompleted().then(() => "bounded"),
    new Promise<string>((resolve) => {
      testTimer = setTimeout(() => resolve("stranded"), 2000);
    }),
  ]);
  clearTimeout(testTimer);
  assert.equal(winner, "bounded");
  assert.equal(store.get(), true);
});

test("the tour's ending starts the save first and waits on it before going idle", () => {
  // Started before the restore request, so it runs in parallel with it.
  const runRestore = hook.indexOf("const runRestore = useCallback(");
  const start = hook.indexOf("const completionSaved = completed", runRestore);
  assert.ok(runRestore > 0 && start > runRestore);
  assert.ok(start < hook.indexOf("restoreTutorialWorkspace({", runRestore));
  assert.match(hook, /\? markOnboardingCompleted\(\)\s*:\s*Promise\.resolve\(\)/);
  // Every path that settles waits for it first.
  assert.match(
    hook,
    /await completionSaved;\s*\n\s*if \(runRef\.current !== idleRun\) return true;\s*\n\s*settle\(\);/,
  );
  assert.match(
    hook,
    /await completionSaved;\s*\n(\s*\/\/[^\n]*\n)*\s*if \(runRef\.current !== run\) return true;\s*\n\s*settle\(session\);/,
  );
  assert.match(
    hook,
    /if \(!restored\) throw firstError;\s*\n\s*await completionSaved;/,
  );
  const settle = hook.slice(
    hook.indexOf("const settle = "),
    hook.indexOf('setPhase({ kind: "idle" });'),
  );
  assert.doesNotMatch(settle, /markOnboardingCompleted/);
});

test("the client reads and writes /api/ui/onboarding", async () => {
  const originalFetch = globalThis.fetch;
  const calls: { input: RequestInfo | URL; init?: RequestInit }[] = [];
  let status = 200;
  globalThis.fetch = async (input, init) => {
    calls.push({ input, init });
    return new Response(JSON.stringify({ ok: true, completed_version: 2 }), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    assert.deepEqual(await getOnboardingCompletion(), {
      ok: true,
      completed_version: 2,
    });
    assert.equal(calls[0].input, "/api/ui/onboarding");
    await saveOnboardingCompletion(2);
    assert.equal(calls[1].input, "/api/ui/onboarding");
    assert.equal(calls[1].init?.method, "PUT");
    assert.deepEqual(JSON.parse(String(calls[1].init?.body)), {
      completed_version: 2,
    });
    // Its own route: never the panel layout's, which is replaced whole.
    assert.ok(calls.every((call) => call.input !== "/api/ui/preferences"));
    status = 500;
    await assert.rejects(getOnboardingCompletion());
    await assert.rejects(saveOnboardingCompletion(2));
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("the wiring: App reads it once, the chip holds still while unknown", () => {
  // Nothing reads completion from browser storage any more.
  assert.doesNotMatch(chat, /localStorage|hasCompletedOnboarding/);
  assert.doesNotMatch(storage, /onboarding-completed|markOnboardingCompleted/);
  // App owns it (the chat pane remounts per session) and loads it once.
  assert.match(
    app,
    /useSyncExternalStore\(\s*onboardingCompletion\.subscribe,\s*onboardingCompletion\.get,?\s*\)/,
  );
  assert.match(app, /onboardingCompletion\.load\(\)/);
  assert.match(app, /tourCompleted=\{tourCompleted\}/);
  // The tour's one ending marks it through the server-backed store.
  assert.match(hook, /import \{ markOnboardingCompleted \} from "\.\/onboardingCompletion"/);
  // Unknown: no pulse, and the subtitle's line reserved but invisible.
  assert.match(chat, /tourActive \|\| toured \|\| !tourKnown \? "" : "chip-pulse"/);
  assert.match(chat, /tourActive \|\| tourKnown \? "" : "invisible"/);
  assert.match(chat, /const tourKnown = tourCompleted !== null/);
  // A returning user sees the "again" line.
  assert.match(chat, /Take the full interactive tutorial again/);
});

test("the one-shot v1 invitation is retired, not left to read dead storage", () => {
  for (const source of [app, chat, storage]) {
    assert.doesNotMatch(source, /consumeTutorialUpdateInvitation|tutorialUpdated/);
  }
  assert.doesNotMatch(chat, /Full tutorial updated/);
});
