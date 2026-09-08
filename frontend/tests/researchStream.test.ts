import assert from "node:assert/strict";
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
