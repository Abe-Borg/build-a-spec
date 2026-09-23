/**
 * The condensed-conversation divider (compaction plan Phase 3).
 *
 * The pure rules are tested directly; the wiring — App carrying the record
 * through every payload path, Chat drawing the divider as a sibling of the
 * memoized bubbles — is pinned at the source level (no DOM harness; the
 * chatPerf.test.ts idiom).
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import {
  deleteReference,
  getCompactionStatus,
  getCompactionSummary,
} from "../src/lib/api.ts";
import {
  COMPACTION_POLL_FIRST_MS,
  COMPACTION_POLL_MAX_MS,
  compactTokens,
  condensedDividerIndex,
  condensedTurnsLabel,
  describeCompaction,
  followCompactionStatus,
} from "../src/lib/compaction.ts";
import type {
  ChatMessage,
  CompactionFacts,
  CompactionInfo,
  CompactionStatus,
} from "../src/types.ts";

const here = dirname(fileURLToPath(import.meta.url));
const read = (...parts: string[]) =>
  readFileSync(join(here, "..", "src", ...parts), "utf8");

let nextId = 0;
const user = (text: string): ChatMessage => ({
  id: `u${nextId++}`,
  role: "user",
  text,
});
const reply = (text: string, extra: Partial<ChatMessage> = {}): ChatMessage => ({
  id: `a${nextId++}`,
  role: "assistant",
  text,
  ...extra,
});

test("the divider sits before the first turn still sent word for word", () => {
  const messages = [
    user("one"),
    reply("r1"),
    user("two"),
    reply("r2"),
    user("three"),
    reply("r3"),
  ];
  // Turns 1–2 condensed: the divider goes before turn 3's message.
  assert.equal(condensedDividerIndex(messages, 2), 4);
  assert.equal(condensedDividerIndex(messages, 1), 2);
});

test("notes and failed turns are not turns — the server never saved them", () => {
  const messages = [
    user("one"),
    reply("r1"),
    reply("Research started", { note: true }),
    user("a turn that failed"),
    reply("Unexpected error", { error: true }),
    user("two"),
    reply("r2"),
    user("three"),
    reply("r3"),
  ];
  // The failed message rolled back server-side, so "two" is turn 2 and
  // "three" is turn 3: covering two turns puts the divider before "three".
  assert.equal(condensedDividerIndex(messages, 2), 7);
});

test("no divider when nothing is condensed or the cut is not on screen", () => {
  const messages = [user("one"), reply("r1"), user("two"), reply("r2")];
  assert.equal(condensedDividerIndex(messages, 0), -1);
  assert.equal(condensedDividerIndex(messages, 2), -1);
  assert.equal(condensedDividerIndex(messages, Number.NaN), -1);
  assert.equal(condensedDividerIndex([], 3), -1);
});

test("the labels read the way the divider and sheet use them", () => {
  assert.equal(condensedTurnsLabel(1), "turn 1");
  assert.equal(condensedTurnsLabel(40), "turns 1–40");
  assert.equal(compactTokens(612_345), "612k");
  assert.equal(compactTokens(40_250), "40.3k");
  assert.equal(compactTokens(900), "900");
  assert.equal(compactTokens(0), "0");
  assert.equal(compactTokens(Number.NaN), "0");
});

const facts = (overrides: Partial<CompactionFacts> = {}): CompactionFacts => ({
  enabled: false,
  threshold_tokens: 600_000,
  keep_turns: 3,
  active: false,
  covers_turns: 0,
  keep_from: null,
  created_at: "",
  trigger: "",
  tokens_before: 0,
  tokens_after: 0,
  summary_chars: 0,
  tokens_per_char: null,
  runner: null,
  ...overrides,
});

test("Developer tools describes condensing without any summary text", () => {
  assert.equal(
    describeCompaction(facts()),
    "not condensed · routine condensing off (backstop only)",
  );
  const active = describeCompaction(
    facts({
      enabled: true,
      active: true,
      covers_turns: 40,
      keep_from: 80,
      created_at: "2026-09-23T10:00:00+00:00",
      trigger: "background",
      tokens_before: 612_000,
      tokens_after: 41_000,
      summary_chars: 18_000,
      tokens_per_char: 0.2857,
      runner: {
        status: "failed",
        trigger: "background",
        attempts: 2,
        failures: 1,
        error_kind: "missing_headings",
        retry_after_turns: 44,
        started_at: "",
        finished_at: "",
      },
    }),
  );
  assert.equal(
    active,
    "turns 1–40 condensed (background, 2026-09-23) · 612k → 41.0k tokens · " +
      "summary 18,000 chars · routine condensing at 600k, keeping 3 turns · " +
      "last summary failed (missing_headings), next try from turn 44 · " +
      "0.286 tokens/char",
  );
  const running = describeCompaction(
    facts({
      runner: {
        status: "running",
        trigger: "backstop",
        attempts: 1,
        failures: 0,
        error_kind: "",
        retry_after_turns: 0,
        started_at: "",
        finished_at: "",
      },
    }),
  );
  assert.match(running, /a summary is being written/);
});

test("the summary is fetched on demand, and a refusal says why", async (t) => {
  const originalFetch = globalThis.fetch;
  t.after(() => {
    globalThis.fetch = originalFetch;
  });
  let url = "";
  globalThis.fetch = async (input) => {
    url = String(input);
    return new Response(
      JSON.stringify({
        ok: true,
        compaction: { covers_turns: 4, summary: "## Exact details\n42 gpm" },
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  };
  const record = await getCompactionSummary();
  assert.equal(url, "/api/chat/compaction");
  assert.equal(record.covers_turns, 4);
  assert.match(record.summary, /42 gpm/);

  globalThis.fetch = async () =>
    new Response(
      JSON.stringify({
        ok: false,
        error: "This conversation has not been condensed.",
      }),
      { status: 404, headers: { "Content-Type": "application/json" } },
    );
  await assert.rejects(getCompactionSummary(), /has not been condensed/);
});

test("a reference delete hands back the record it left, or none", async (t) => {
  // A delete that cuts history can drop the summary of the turns it cut; the
  // response says what is left so the divider does not outlive its record.
  const originalFetch = globalThis.fetch;
  t.after(() => {
    globalThis.fetch = originalFetch;
  });
  const kept = {
    covers_turns: 2,
    created_at: "2026-09-23T00:00:00Z",
    tokens_before: 600_000,
    tokens_after: 90_000,
    trigger: "background",
    summary_chars: 4_000,
  };
  const respond = (compaction: unknown) => async () =>
    new Response(
      JSON.stringify({
        ok: true,
        reference_docs: [],
        suggested_prompts: [],
        figures: [],
        ...(compaction === undefined ? {} : { compaction }),
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );

  globalThis.fetch = respond(kept);
  assert.deepEqual((await deleteReference("ref-1")).compaction, kept);
  globalThis.fetch = respond(null);
  assert.equal((await deleteReference("ref-1")).compaction, null);
  // An older server that never sent the key reads as nothing condensed.
  globalThis.fetch = respond(undefined);
  assert.equal((await deleteReference("ref-1")).compaction, null);
});

// --- Asking until a background summary lands -------------------------------

const RECORD: CompactionInfo = {
  covers_turns: 12,
  created_at: "2026-09-23T00:00:00Z",
  tokens_before: 610_000,
  tokens_after: 95_000,
  trigger: "background",
  summary_chars: 9_000,
};

/** A timer the test drives by hand: nothing runs until `fire()`. */
function manualClock() {
  const queue: { run: () => void; ms: number; id: number }[] = [];
  let next = 0;
  return {
    schedule: (run: () => void, ms: number) => {
      next += 1;
      queue.push({ run, ms, id: next });
      return next;
    },
    cancel: (id: unknown) => {
      const at = queue.findIndex((entry) => entry.id === id);
      if (at >= 0) queue.splice(at, 1);
    },
    waiting: () => queue.length,
    /** Run the next scheduled ask and let its answer settle. */
    async fire(): Promise<number> {
      const entry = queue.shift();
      assert.ok(entry, "nothing was scheduled");
      entry.run();
      await new Promise<void>((resolve) => setImmediate(resolve));
      return entry.ms;
    },
  };
}

test("it asks until the summary lands, then hands the record over once", async () => {
  const clock = manualClock();
  const answers: CompactionStatus[] = [
    { pending: true, compaction: null },
    { pending: true, compaction: null },
    { pending: false, compaction: RECORD },
  ];
  let asked = 0;
  const settled: (CompactionInfo | null)[] = [];
  followCompactionStatus({
    fetchStatus: async () => answers[asked++],
    isCurrent: () => true,
    onSettled: (record) => settled.push(record),
    schedule: clock.schedule,
    cancel: clock.cancel,
  });

  // Nothing is asked at once — the summary has only just started.
  assert.equal(asked, 0);
  const waits = [await clock.fire(), await clock.fire(), await clock.fire()];
  assert.equal(waits[0], COMPACTION_POLL_FIRST_MS);
  assert.ok(waits[1] > waits[0] && waits[2] > waits[1], "the asks slow down");
  // A "pending" answer changes nothing on screen; the settled one lands once.
  assert.deepEqual(settled, [RECORD]);
  assert.equal(clock.waiting(), 0, "no ask after it settled");
});

test("the asks slow down to a cap, and a failed summary settles too", async () => {
  const clock = manualClock();
  let asked = 0;
  const settled: (CompactionInfo | null)[] = [];
  followCompactionStatus({
    fetchStatus: async () =>
      ++asked < 12 ? { pending: true, compaction: null } : { pending: false, compaction: null },
    isCurrent: () => true,
    onSettled: (record) => settled.push(record),
    schedule: clock.schedule,
    cancel: clock.cancel,
  });
  let longest = 0;
  while (clock.waiting()) longest = Math.max(longest, await clock.fire());
  assert.equal(longest, COMPACTION_POLL_MAX_MS);
  // A refused summary is "not pending" with no record: the asking stops.
  assert.deepEqual(settled, [null]);
});

test("a dropped ask keeps what is on screen and asks again", async () => {
  const clock = manualClock();
  let asked = 0;
  const settled: (CompactionInfo | null)[] = [];
  followCompactionStatus({
    fetchStatus: async () => {
      asked += 1;
      if (asked === 1) throw new Error("network");
      return { pending: false, compaction: RECORD };
    },
    isCurrent: () => true,
    onSettled: (record) => settled.push(record),
    schedule: clock.schedule,
    cancel: clock.cancel,
  });
  await clock.fire();
  assert.deepEqual(settled, [], "a failure is not an answer");
  assert.equal(clock.waiting(), 1, "and it asks again");
  await clock.fire();
  assert.deepEqual(settled, [RECORD]);
});

test("stopping, or a workspace that moved on, drops the answer", async () => {
  // Stopped before the first ask: nothing is ever asked.
  let clock = manualClock();
  let asked = 0;
  const stopEarly = followCompactionStatus({
    fetchStatus: async () => {
      asked += 1;
      return { pending: false, compaction: RECORD };
    },
    isCurrent: () => true,
    onSettled: () => assert.fail("a stopped follow must not apply"),
    schedule: clock.schedule,
    cancel: clock.cancel,
  });
  stopEarly();
  assert.equal(clock.waiting(), 0);
  assert.equal(asked, 0);

  // Stopped while an ask is in flight (a turn started): its answer is dropped.
  clock = manualClock();
  let answer!: (status: CompactionStatus) => void;
  const stopMidAsk = followCompactionStatus({
    fetchStatus: () => new Promise<CompactionStatus>((resolve) => (answer = resolve)),
    isCurrent: () => true,
    onSettled: () => assert.fail("an answer after stop must not apply"),
    schedule: clock.schedule,
    cancel: clock.cancel,
  });
  await clock.fire();
  stopMidAsk();
  answer({ pending: false, compaction: RECORD });
  await new Promise<void>((resolve) => setImmediate(resolve));
  assert.equal(clock.waiting(), 0);

  // The workspace changed (a new session, a project opened) mid-ask.
  clock = manualClock();
  let current = true;
  followCompactionStatus({
    fetchStatus: async () => {
      current = false;
      return { pending: false, compaction: RECORD };
    },
    isCurrent: () => current,
    onSettled: () => assert.fail("another workspace's record must not apply"),
    schedule: clock.schedule,
    cancel: clock.cancel,
  });
  await clock.fire();
  assert.equal(clock.waiting(), 0, "and the asking stops");
});

test("the status client reads the two fields and reports a refusal", async (t) => {
  const originalFetch = globalThis.fetch;
  t.after(() => {
    globalThis.fetch = originalFetch;
  });
  let url = "";
  globalThis.fetch = async (input) => {
    url = String(input);
    return new Response(
      JSON.stringify({ ok: true, pending: true, compaction: null }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  };
  assert.deepEqual(await getCompactionStatus(), { pending: true, compaction: null });
  assert.equal(url, "/api/chat/compaction/status");

  globalThis.fetch = async () =>
    new Response(JSON.stringify({ ok: true, pending: false, compaction: RECORD }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  assert.deepEqual(await getCompactionStatus(), { pending: false, compaction: RECORD });

  globalThis.fetch = async () =>
    new Response(JSON.stringify({ ok: false, error: "stale workspace" }), {
      status: 409,
      headers: { "Content-Type": "application/json" },
    });
  await assert.rejects(getCompactionStatus(), /stale workspace/);
});

test("App asks while a summary is pending and no turn is streaming", () => {
  const app = read("App.tsx");
  const at = app.indexOf("followCompactionStatus({");
  assert.ok(at > 0, "App follows the status route");
  const effect = app.slice(app.lastIndexOf("useEffect(() => {", at), at + 500);
  // Only while one is on its way, and never during a turn — a turn re-syncs
  // the divider itself at its start and its end.
  assert.match(effect, /if \(!compactionPending \|\| busy\) return;/);
  assert.match(effect, /fetchStatus: getCompactionStatus/);
  assert.match(effect, /isCurrent: \(\) => workspaceEpochRef\.current === epoch/);
  assert.match(effect, /setCompaction\(record\);\s*setCompactionPending\(false\);/);
  assert.match(effect, /\}, \[compactionPending, busy\]\);/);
  // Every payload path carries the flag the effect keys on.
  const refreshDoc = app.slice(
    app.indexOf("const refreshDoc = useCallback("),
    app.indexOf("const refreshDoc = useCallback(") + 1600,
  );
  assert.match(refreshDoc, /setCompactionPending\(payload\.compaction_pending \?\? false\)/);
  const apply = app.slice(
    app.indexOf("const applyDocPayload = ("),
    app.indexOf("const applySessionBundle = "),
  );
  assert.match(apply, /setCompactionPending\(payload\.compaction_pending \?\? false\)/);
  const clear = app.slice(
    app.indexOf("const clearSessionState = () => {"),
    app.indexOf("const startBlankSession = "),
  );
  assert.match(clear, /setCompactionPending\(false\)/);
});

test("App carries the record through every payload path", () => {
  const app = read("App.tsx");
  // The turn's own event, the doc refresh, the payload apply, the session
  // bundle, and the new-session clear — a path that forgot it would keep
  // the previous chat's divider, or never show a new one.
  assert.match(app, /evt\.type === "compaction"\) \{\s*[\s\S]*?setCompaction\(evt\.compaction\)/);
  const refreshDoc = app.slice(
    app.indexOf("const refreshDoc = useCallback("),
    app.indexOf("const refreshDoc = useCallback(") + 1600,
  );
  assert.match(refreshDoc, /setCompaction\(payload\.compaction \?\? null\)/);
  const apply = app.slice(
    app.indexOf("const applyDocPayload = ("),
    app.indexOf("const applySessionBundle = "),
  );
  assert.match(apply, /compaction\?: CompactionInfo \| null;/);
  assert.match(apply, /setCompaction\(payload\.compaction \?\? null\)/);
  const clear = app.slice(
    app.indexOf("const clearSessionState = () => {"),
    app.indexOf("const startBlankSession = "),
  );
  assert.match(clear, /setCompaction\(null\)/);
  // A reference delete can drop the record (it truncates history at the turn
  // that first read the document): the handler applies what the server kept.
  const remove = app.slice(
    app.indexOf("const onRemoveReference = useCallback("),
    app.indexOf("const onRemoveReference = useCallback(") + 900,
  );
  assert.match(remove, /setCompaction\(result\.compaction\)/);
  assert.match(app, /compaction=\{compaction\}/);
});

test("Chat draws the divider beside the bubbles, never on them", () => {
  const chat = read("components", "Chat.tsx");
  // A sibling inside a keyed Fragment: a prop on the memoized bubble would
  // re-render every bubble whenever the record moved.
  assert.match(chat, /<Fragment key=\{m\.id\}>/);
  assert.match(chat, /index === dividerAt && compaction \?/);
  assert.match(chat, /<CondensedDivider compaction=\{compaction\} onView=\{openSummary\} \/>/);
  const usage = /<MessageBubble\b[\s\S]*?\/>/.exec(chat)?.[0] ?? "";
  assert.doesNotMatch(usage, /compaction/);
  assert.doesNotMatch(usage, /key=/, "the key moved to the Fragment");

  const divider = read("components", "CondensedDivider.tsx");
  assert.match(divider, /data-capability="chat\.condensed"/);
  // The sheet reads the summary from the server, never from a prop: the
  // payload carries only sizes.
  assert.match(divider, /getCompactionSummary\(\)/);
  assert.doesNotMatch(read("types.ts").slice(
    read("types.ts").indexOf("export interface CompactionInfo"),
    read("types.ts").indexOf("export interface CompactionSummary"),
  ), /summary: string/);
});
