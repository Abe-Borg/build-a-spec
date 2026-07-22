import type {
  DocPayload,
  EditOp,
  Health,
  ImportResultPayload,
  KeyStatus,
  ModuleInfo,
  ProjectLoadResult,
  QcApplyResult,
  QcEvent,
  QcSnapshot,
  ReadinessPayload,
  ResearchEvent,
  ResearchSnapshot,
  SectionDiffPayload,
  StreamEvent,
  UpdateCheckPayload,
  UsageSummary,
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

/**
 * Reset the session. With `opts` (the Batch 8 module picker) the chosen
 * module/discipline ride a JSON body; without, the historical bodyless call
 * — reset keeps the active module + discipline (the onboarding tour's path).
 */
export async function resetSession(opts?: {
  module_id?: string;
  discipline?: string;
}): Promise<void> {
  if (opts) {
    await fetch("/api/session/reset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(opts),
    });
    return;
  }
  await fetch("/api/session/reset", { method: "POST" });
}

/** The selectable module registry (Batch 8 session-start picker). */
export async function getModules(): Promise<ModuleInfo[]> {
  const resp = await fetch("/api/modules");
  const data = await resp.json();
  if (!resp.ok || !data.ok) {
    throw new Error(data.error ?? `modules failed (${resp.status})`);
  }
  return data.modules ?? [];
}

/* --- API key management (WI3) --- */

export async function getKeyStatus(): Promise<KeyStatus> {
  const resp = await fetch("/api/key/status");
  if (!resp.ok) throw new Error(`key status ${resp.status}`);
  return resp.json();
}

export async function deleteKey(): Promise<KeyStatus> {
  const resp = await fetch("/api/key", { method: "DELETE" });
  const data = await resp.json();
  if (!resp.ok || !data.ok) {
    throw new Error(data.error ?? `delete failed (${resp.status})`);
  }
  return data;
}

/** This session's billed usage + estimated cost (WI4 meter). */
export async function getUsage(): Promise<UsageSummary> {
  const resp = await fetch("/api/usage");
  if (!resp.ok) throw new Error(`usage ${resp.status}`);
  return resp.json();
}

/** Validate a candidate (or the stored) key; never stores it. */
export async function testKey(
  apiKey?: string,
): Promise<{ ok: boolean; error?: string }> {
  const resp = await fetch("/api/key/test", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(apiKey ? { api_key: apiKey } : {}),
  });
  if (!resp.ok) return { ok: false, error: `test failed (${resp.status})` };
  return resp.json();
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

/** Version diff for the in-app compare view (Batch 5). cur defaults to head. */
export async function getDocDiff(
  base: number,
  cur?: number,
): Promise<SectionDiffPayload> {
  const query =
    cur === undefined ? `?base=${base}` : `?base=${base}&cur=${cur}`;
  const resp = await fetch(`/api/doc/diff${query}`);
  const data = await resp.json();
  if (!resp.ok || !data.ok) {
    throw new Error(data.error ?? `diff failed (${resp.status})`);
  }
  return data;
}

/** Apply a manual edit batch (WI2). 409 while a model turn streams. */
export async function editDoc(ops: EditOp[]): Promise<DocPayload> {
  const resp = await fetch("/api/doc/edit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ops }),
  });
  const data = await resp.json();
  if (!resp.ok || !data.ok) {
    throw new Error(data.error ?? `edit failed (${resp.status})`);
  }
  return data;
}

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

/**
 * Fetch the canned full-section draft directive (WI1). The caller sends the
 * returned text back through {@link streamChat} as a normal user turn, so the
 * draft pass rides the one chat pipeline. 409 while a turn or research runs.
 */
export async function draftFull(): Promise<string> {
  const resp = await fetch("/api/draft/full", { method: "POST" });
  const data = await resp.json();
  if (!resp.ok || !data.ok) {
    throw new Error(data.error ?? `draft failed (${resp.status})`);
  }
  return data.message as string;
}

/**
 * Fetch the guided-tour demo directive (Batch 6). The caller sends the
 * returned text back through {@link streamChat} as a normal user turn, so
 * the demo rides the one chat pipeline. 409 while a turn or research runs,
 * or when the document is not blank.
 */
export async function startOnboardingDemo(discipline: string): Promise<string> {
  const resp = await fetch("/api/onboarding/demo", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ discipline }),
  });
  const data = await resp.json();
  if (!resp.ok || !data.ok) {
    throw new Error(data.error ?? `demo failed (${resp.status})`);
  }
  return data.message as string;
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

/**
 * Stop the in-flight turn (Claude.ai-style stop button). Whatever text/edits
 * landed before this call still lands normally through that turn's own
 * `turn_complete` — this just asks the stream to end sooner. A 409 (no turn
 * streaming) means it likely already finished on its own; safe to ignore.
 */
export async function stopChat(): Promise<void> {
  const resp = await fetch("/api/chat/stop", { method: "POST" });
  if (!resp.ok && resp.status !== 409) {
    const data = await resp.json().catch(() => ({}));
    throw new Error(data.error ?? `stop failed (${resp.status})`);
  }
}

/* --- Research (Phase 4) --- */

export async function startResearch(): Promise<void> {
  const resp = await fetch("/api/research/start", { method: "POST" });
  const data = await resp.json();
  if (!resp.ok || !data.ok) {
    throw new Error(data.error ?? `research start failed (${resp.status})`);
  }
}

/** Stop the running research fan-out. Discards whatever it found so far. */
export async function stopResearch(): Promise<void> {
  const resp = await fetch("/api/research/stop", { method: "POST" });
  if (!resp.ok && resp.status !== 409) {
    const data = await resp.json().catch(() => ({}));
    throw new Error(data.error ?? `stop failed (${resp.status})`);
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

/* --- Master import + updates (Phase 5) --- */

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

/* --- Final QC on Fable 5 (Batch 4) --- */

export async function startQc(): Promise<void> {
  const resp = await fetch("/api/qc/start", { method: "POST" });
  const data = await resp.json();
  if (!resp.ok || !data.ok) {
    throw new Error(data.error ?? `QC start failed (${resp.status})`);
  }
}

/** Stop the running Final QC pass. Discards whatever it found so far. */
export async function stopQc(): Promise<void> {
  const resp = await fetch("/api/qc/stop", { method: "POST" });
  if (!resp.ok && resp.status !== 409) {
    const data = await resp.json().catch(() => ({}));
    throw new Error(data.error ?? `stop failed (${resp.status})`);
  }
}

export async function getQcStatus(): Promise<QcSnapshot> {
  const resp = await fetch("/api/qc/status");
  if (!resp.ok) throw new Error(`QC status ${resp.status}`);
  return resp.json();
}

/** Follow the active/last QC run's SSE stream until it closes. */
export async function* streamQc(): AsyncGenerator<QcEvent> {
  const resp = await fetch("/api/qc/stream");
  if (!resp.ok || !resp.body) return;
  yield* readSse<QcEvent>(resp);
}

/** Apply accepted findings' fixes as one undoable version. */
export async function applyQc(findingIds: string[]): Promise<QcApplyResult> {
  const resp = await fetch("/api/qc/apply", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ finding_ids: findingIds }),
  });
  const data = await resp.json();
  if (!resp.ok || !data.ok) {
    throw new Error(data.error ?? `QC apply failed (${resp.status})`);
  }
  return data;
}

/** Dismiss a finding (remembered across re-runs). */
export async function dismissQc(
  findingId: string,
  reason?: string,
): Promise<QcSnapshot> {
  const resp = await fetch("/api/qc/dismiss", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ finding_id: findingId, reason: reason ?? null }),
  });
  const data = await resp.json();
  if (!resp.ok || !data.ok) {
    throw new Error(data.error ?? `QC dismiss failed (${resp.status})`);
  }
  return data.qc as QcSnapshot;
}

/** The deterministic "can it go out the door" checklist. */
export async function getReadiness(): Promise<ReadinessPayload> {
  const resp = await fetch("/api/readiness");
  if (!resp.ok) throw new Error(`readiness ${resp.status}`);
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
