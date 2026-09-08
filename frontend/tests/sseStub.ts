/**
 * Stub `fetch` with an SSE body that emits `frames` and then stays open, so
 * a test can prove the consumer actually released it rather than waiting on
 * a stream that was about to end anyway.
 *
 * The stub wires `init.signal` to the body the way a real `fetch` does —
 * abort errors the pending read — because that wiring IS the behavior under
 * test. `cancelled` resolves when the reader is released.
 *
 * Shared by the research and Final QC stream tests: one correctness-critical
 * primitive, and two byte-identical copies would drift unnoticed.
 */
export function stubSseFetch(
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
        // Deliberately never closed: a live run's stream stays open for the
        // length of the run.
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
