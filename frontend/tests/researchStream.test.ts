import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { streamResearch } from "../src/lib/api.ts";
import type { ResearchEvent } from "../src/types.ts";
import { stubSseFetch } from "./sseStub.ts";

const frame = (seq: number, type: string) => ({
  seq,
  ts: "10:00:00",
  type,
  round: 1,
});

test("the research stream hands its abort signal to fetch", async (t) => {
  const { captured } = stubSseFetch(t, [frame(0, "research_started")]);
  const controller = new AbortController();

  for await (const event of streamResearch(controller.signal)) {
    assert.equal(event.type, "research_started");
    break;
  }

  assert.equal(captured.signal, controller.signal);
});

test("aborting the signal ends the research stream", async (t) => {
  // The workspace-epoch case: a session/tutorial/project transition aborts
  // the in-flight follower rather than leaving a second reader racing the
  // new one. Without the signal reaching fetch this never returns, because
  // the body has no natural end.
  stubSseFetch(t, [frame(0, "research_started")]);
  const controller = new AbortController();

  const seen: ResearchEvent[] = [];
  try {
    for await (const event of streamResearch(controller.signal)) {
      seen.push(event);
      controller.abort();
    }
  } catch {
    // An aborted body rejects its pending read; the follower's own
    // try/catch reads that as a transport close and probes for status.
  }

  assert.deepEqual(seen.map((event) => event.type), ["research_started"]);
});

test("breaking out of the stream releases the body", async (t) => {
  // Unwinding the generator alone used to release nothing: `readSse` had no
  // `finally`, so a reconnect or an epoch-change `break` left the browser
  // holding a body no one would ever read again.
  const { cancelled } = stubSseFetch(t, [
    frame(0, "research_started"),
    frame(1, "dimension_started"),
  ]);

  for await (const event of streamResearch()) {
    if (event.seq === 0) break;
  }

  await cancelled;
});

/* --- a new round gets its own follower (Codex review on PR #212) ---
 *
 * The follower guard used to be a boolean. A round started while the last
 * round's stream was still closing was refused a follower: the new call
 * returned because the old follower still held the guard, and the old
 * follower's final refresh then found `running` already on screen, so the
 * status effect never ran again. The round ran with no live board, and the
 * drawer sat on "Researching… (0/4)" after it had finished. The quickest way
 * to hit it is to Stop and press Research again at once: Stop's own refresh
 * shows the stopped state before the stream delivers it.
 *
 * App.tsx has no DOM harness, so these pin the wiring at the source level
 * (the qcStream.test.ts idiom). The race itself was reproduced and the fix
 * checked in a real browser; CLAUDE.md records how. */

const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");

/** The source between two markers, both required. */
function between(start: string, end: string): string {
  const from = app.indexOf(start);
  assert.ok(from >= 0, `${start} not found`);
  const to = app.indexOf(end, from);
  assert.ok(to > from, `${end} not found after ${start}`);
  return app.slice(from, to);
}

const follower = between(
  "const followResearch = useCallback(",
  "const onStartResearch = useCallback(",
);

test("a research follower serves one workspace and run, and a new run is never refused", () => {
  assert.match(
    follower,
    /const key = \{\s*workspace: workspaceEpochRef\.current,\s*run: researchRunEpochRef\.current,\s*\};/,
  );
  // Only a follower for exactly this pair makes a second call a no-op.
  assert.match(
    follower,
    /if \(active && active\.workspace === key\.workspace && active\.run === key\.run\) \{\s*return;\s*\}\s*researchFollowRef\.current = key;/,
  );
  // "Still current" means both epochs, not the workspace alone.
  assert.match(
    follower,
    /const current = \(\) =>\s*workspaceEpochRef\.current === key\.workspace &&\s*researchRunEpochRef\.current === key\.run;/,
  );
  assert.match(follower, /while \(reconnect && current\(\)\)/);
});

test("a superseded research follower puts nothing on screen and frees only its own slot", () => {
  // The check leads the loop body: ahead of the sentinel, and ahead of the
  // merge. A stopped round's last frame merged into a restarted round of the
  // same number would hold the new log's high-water mark above every refetch.
  const loop = follower.slice(
    follower.indexOf("for await (const evt of streamResearch(controller.signal))"),
  );
  const check = loop.indexOf("if (!current()) break;");
  assert.ok(check >= 0, "the loop never asks whether it is still current");
  assert.ok(check < loop.indexOf('evt.type === "stream_end"'));
  assert.ok(check < loop.indexOf("mergeResearchEvent("));
  // After the status probe too, before its answer can be accepted.
  assert.match(
    follower,
    /await getResearchStatus\(\);\s*if \(!current\(\)\) break;\s*const accepted = acceptResearchSnapshot/,
  );
  // The slot goes back only if it is still this follower's.
  assert.match(
    follower,
    /if \(researchFollowRef\.current === key\) \{\s*researchFollowRef\.current = null;\s*\}/,
  );
  assert.equal(
    (follower.match(/researchFollowRef\.current = null;/g) ?? []).length,
    1,
    "the slot must never be released unconditionally",
  );
});

test("a research start cuts the old stream loose before it shows running", () => {
  const start = between(
    "const onStartResearch = useCallback(",
    "const onStopResearch = useCallback(",
  );
  const bump = start.indexOf("researchRunEpochRef.current += 1;");
  const abort = start.indexOf("researchStreamRef.current?.abort();");
  const publish = start.indexOf('status: "running"');
  const follow = start.indexOf("void followResearch();");
  assert.ok(bump >= 0, "the start never moves to a new run");
  assert.ok(abort > bump, "the old stream is not cut after the move");
  assert.ok(publish > abort, "running goes on screen before the hand-off");
  assert.ok(follow > publish, "the new round is never followed");
  // Only an accepted start moves to a new run: a refused one leaves the
  // follower of whatever is still going alone.
  const refused = start.slice(start.indexOf("} catch (e) {"));
  assert.doesNotMatch(refused, /researchRunEpochRef/);
});
