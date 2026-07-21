import type {
  DocPayload,
  Health,
  ProjectLoadResult,
  StreamEvent,
} from "../types";

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

export async function getDoc(): Promise<DocPayload> {
  const resp = await fetch("/api/doc");
  if (!resp.ok) throw new Error(`doc ${resp.status}`);
  return resp.json();
}

/** Step the document one version back/forward; null when at the end stop. */
async function stepDoc(direction: "undo" | "redo"): Promise<DocPayload | null> {
  const resp = await fetch(`/api/doc/${direction}`, { method: "POST" });
  if (resp.status === 409) return null;
  const data = await resp.json();
  if (!resp.ok || !data.ok) {
    throw new Error(data.error ?? `${direction} failed (${resp.status})`);
  }
  return data;
}

export const undoDoc = () => stepDoc("undo");
export const redoDoc = () => stepDoc("redo");

/** Restore a session from a parsed project file. */
export async function loadProject(
  project: unknown,
): Promise<ProjectLoadResult> {
  const resp = await fetch("/api/project/load", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(project),
  });
  const data = await resp.json();
  if (!resp.ok || !data.ok) {
    throw new Error(data.error ?? `load failed (${resp.status})`);
  }
  return data;
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
