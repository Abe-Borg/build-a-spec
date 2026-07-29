import assert from "node:assert/strict";
import test from "node:test";

import { streamResearch } from "../src/lib/api.ts";
import type { ResearchEvent } from "../src/types.ts";

/**
 * Stub `fetch` with an SSE body that emits `frames` and then stays open, so
 * a test can prove the consumer actually released it rather than waiting on
 * a stream that was about to end anyway.
 *
 * The stub wires `init.signal` to the body the way a real `fetch` does —
 * abort errors the pending read — because that wiring IS the behavior under
 * test. `cancelled` resolves when the reader is released.
 */
function stubSseFetch(
  t: { after: (fn: () => void) => void },
  frames: unknown[],
) {
  const originalFetch = globalThis.fetch;
  t.after(() => {
    globalThis.fetch = originalFetch;
  });

  const captured: { signal?: AbortSignal | null } = {};
  let markCancelled: () => void = () => undefined;
  const cancelled = new Promise<void>((resolve) => {
    markCancelled = resolve;
  });

  globalThis.fetch = async (_input, init) => {
    const signal = init?.signal ?? null;
    captured.signal = signal;
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        const encoder = new TextEncoder();
        for (const frame of frames) {
          controller.enqueue(
            encoder.encode(`data: ${JSON.stringify(frame)}\n\n`),
          );
        }
        // Deliberately never closed: a live research stream stays open for
        // the length of the run.
        signal?.addEventListener("abort", () => {
          try {
            controller.error(
              new DOMException("The operation was aborted.", "AbortError"),
            );
          } catch {
            // Already errored or closed.
          }
        });
      },
      cancel() {
        markCancelled();
      },
    });
    return new Response(body, {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    });
  };

  return { captured, cancelled };
}

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
