import type {
  AuditSnapshot,
  DocPayload,
  Health,
  ImportResultPayload,
  ProjectLoadResult,
  ResearchEvent,
  ResearchSnapshot,
  StreamEvent,
  UpdateCheckPayload,
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

/** Read SSE frames off a fetch Response body and yield parsed JSON. */
async function* readSse<T>(resp: Response): AsyncGenerator<T> {
  if (!resp.body) return;
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
            yield JSON.parse(line.slice(6)) as T;
          } catch {
            // Malformed frame — skip rather than kill the stream.
          }
        }
      }
    }
  }
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
  yield* readSse<StreamEvent>(resp);
}

/* --- Research (Phase 4) --- */

export async function startResearch(): Promise<void> {
  const resp = await fetch("/api/research/start", { method: "POST" });
  const data = await resp.json();
  if (!resp.ok || !data.ok) {
    throw new Error(data.error ?? `research start failed (${resp.status})`);
  }
}

export async function getResearchStatus(): Promise<ResearchSnapshot> {
  const resp = await fetch("/api/research/status");
  if (!resp.ok) throw new Error(`research status ${resp.status}`);
  return resp.json();
}

/** Follow the active/last research run's SSE stream until it closes. */
export async function* streamResearch(): AsyncGenerator<ResearchEvent> {
  const resp = await fetch("/api/research/stream");
  if (!resp.ok || !resp.body) return;
  yield* readSse<ResearchEvent>(resp);
}

/* --- Master import + compliance audit + updates (Phase 5) --- */

export async function importMaster(file: File): Promise<ImportResultPayload> {
  const form = new FormData();
  form.append("file", file);
  const resp = await fetch("/api/import/master", {
    method: "POST",
    body: form,
  });
  const data = await resp.json();
  if (!resp.ok || !data.ok) {
    throw new Error(data.error ?? `import failed (${resp.status})`);
  }
  return data;
}

export async function startAudit(): Promise<void> {
  const resp = await fetch("/api/audit/start", { method: "POST" });
  const data = await resp.json();
  if (!resp.ok || !data.ok) {
    throw new Error(data.error ?? `audit start failed (${resp.status})`);
  }
}

export async function getAuditStatus(): Promise<AuditSnapshot> {
  const resp = await fetch("/api/audit/status");
  if (!resp.ok) throw new Error(`audit status ${resp.status}`);
  return resp.json();
}

export async function checkUpdate(force = false): Promise<UpdateCheckPayload> {
  const resp = await fetch(`/api/update/check${force ? "?force=true" : ""}`);
  if (!resp.ok) throw new Error(`update check ${resp.status}`);
  return resp.json();
}

export async function installUpdate(): Promise<void> {
  const resp = await fetch("/api/update/install", { method: "POST" });
  const data = await resp.json();
  if (!resp.ok || !data.ok) {
    throw new Error(data.error ?? `update install failed (${resp.status})`);
  }
}
