/**
 * Developer tools' "Session history" row and the bundle link (session
 * history, 2026-10-09). The formatter is tested directly; the fact names
 * and the bundle's query parameter are pinned against the backend source
 * (the costChecks.test.ts idiom), so a rename on either side fails here
 * rather than rendering "undefined" or silently dropping the option. That
 * the modal renders the row and the link through these helpers is pinned
 * at the source level (the modal has no DOM harness).
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import type { SessionHistoryFacts } from "../src/types.ts";
import {
  DIAGNOSTICS_BUNDLE_URL,
  diagnosticsBundleUrl,
  sessionHistoryLines,
} from "../src/lib/sessionHistory.ts";

const read = (path: string) =>
  readFileSync(new URL(path, import.meta.url), "utf8");
const diagnostics = read("../../backend/diagnostics.py");
const history = read("../../backend/session_history.py");
const app = read("../../backend/app.py");
const types = read("../src/types.ts");
const modal = read("../src/components/DeveloperToolsModal.tsx");

function facts(overrides: Partial<SessionHistoryFacts> = {}): SessionHistoryFacts {
  return {
    session_uid: "a".repeat(32),
    created_at: null,
    visits_recorded: 4,
    visits_dropped: 0,
    launches_recorded: 4,
    first_visit_at: null,
    turns_total: 37,
    estimated_cost_usd_total: 12.3456,
    visit_began: "opened",
    visit_started_at: null,
    current_launch_tagged: true,
    earlier_trace_runs_on_disk: 3,
    earlier_log_runs_on_disk: 1,
    earlier_runs_bytes_on_disk: 5 * 1024 * 1024,
    history_days: 90,
    ...overrides,
  };
}

test("no block from an older backend renders no row", () => {
  assert.deepEqual(sessionHistoryLines(undefined), []);
  assert.deepEqual(sessionHistoryLines(null), []);
});

test("the row says what the file recorded and what is still on disk", () => {
  const [remembered, onDisk] = sessionHistoryLines(facts());
  assert.equal(remembered, "4 visits recorded · 37 turns · $12.346 est.");
  assert.equal(
    onDisk,
    "earlier launches on disk: 3 trace runs, 1 log folder (5.0 MB) · " +
      "kept while used within 90 days · this launch tagged",
  );
});

test("dropped visits, an untagged launch and protection off are said", () => {
  const [remembered, onDisk] = sessionHistoryLines(
    facts({
      visits_recorded: 1,
      visits_dropped: 2,
      turns_total: 1,
      earlier_trace_runs_on_disk: 0,
      earlier_log_runs_on_disk: 0,
      current_launch_tagged: false,
      history_days: 0,
    }),
  );
  assert.match(remembered, /^1 visit recorded \(\+2 older not kept\) · 1 turn ·/);
  assert.equal(
    onDisk,
    "no earlier launches on disk · retention protection off · " +
      "this launch not tagged until you open or save",
  );
});

test("the bundle link asks for earlier prompts only when ticked", () => {
  assert.equal(diagnosticsBundleUrl(false), DIAGNOSTICS_BUNDLE_URL);
  assert.equal(
    diagnosticsBundleUrl(true),
    `${DIAGNOSTICS_BUNDLE_URL}?include_prompts=true`,
  );
  // The route's parameter is the one the link sends.
  assert.match(
    app,
    /@app\.get\("\/api\/diagnostics\/bundle"[\s\S]{0,120}def diagnostics_bundle\(include_prompts: bool = False\)/,
  );
});

test("every fact the type names is one the backend writes", () => {
  const body = /export interface SessionHistoryFacts \{([\s\S]*?)\n\}/.exec(types)?.[1];
  assert.ok(body, "SessionHistoryFacts must be declared");
  const keys = [...body.matchAll(/^\s+([a-z_]+)\??:/gm)].map((m) => m[1]);
  assert.ok(keys.length >= 10);
  for (const key of keys) {
    assert.ok(
      diagnostics.includes(`"${key}"`) || history.includes(`"${key}"`),
      `backend never writes session_history.${key}`,
    );
  }
});

test("Developer tools renders the row and the link through the helpers", () => {
  assert.match(modal, /sessionHistoryLines\(sess\.session_history\)/);
  assert.match(modal, /href=\{diagnosticsBundleUrl\(includePrompts\)\}/);
  assert.doesNotMatch(modal, /href="\/api\/diagnostics\/bundle"/);
});
