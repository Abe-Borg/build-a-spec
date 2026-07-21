import type { Health, StreamEvent } from "../types";

export async function getHealth(): Promise<Health> {
  const resp = await fetch("/api/health");
  if (!resp.ok) throw new Error(`health ${resp.status}`);
  return resp.json();
}

export async function saveApiKey(apiKey: string): Promise<void> {
  const resp = await fetch("/api/key", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: apiKey }),
  });
  const data = await resp.json();
  if (!resp.ok || !data.ok) {
    throw new Error(data.error ?? `save key failed (${resp.status})`);
  }
}

export async function resetSession(): Promise<void> {
  await fetch("/api/session/reset", { method: "POST" });
}

/** POST /api/chat and yield parsed SSE events as they arrive. */
export async function* streamChat(
  message: string,
): AsyncGenerator<StreamEvent> {
  const resp = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
  if (!resp.ok || !resp.body) {
    yield {
      type: "error",
      message: `The backend refused the request (${resp.status}).`,
    };
    return;
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let sep;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      for (const line of frame.split("\n")) {
        if (line.startsWith("data: ")) {
          try {
            yield JSON.parse(line.slice(6)) as StreamEvent;
          } catch {
            // Malformed frame — skip rather than kill the stream.
          }
        }
      }
    }
  }
}
