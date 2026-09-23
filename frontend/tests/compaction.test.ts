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

import { getCompactionSummary } from "../src/lib/api.ts";
import {
  compactTokens,
  condensedDividerIndex,
  condensedTurnsLabel,
  describeCompaction,
} from "../src/lib/compaction.ts";
import type { ChatMessage, CompactionFacts } from "../src/types.ts";

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
