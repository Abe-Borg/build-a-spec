import type {
  DiagnosticsActivity,
  DiagnosticsLog,
  DiagnosticsSnapshot,
  DiagnosticsTraces,
  DocPayload,
  EditOp,
  Figure,
  Health,
  ImportResultPayload,
  KeyStatus,
  ProjectLoadResult,
  QcApplyPreviewBasis,
  QcApplyPreviewResult,
  QcApplyResult,
  QcEvent,
  QcModuleSectionCompatibility,
  QcSnapshot,
  ReadinessPayload,
  ReferenceDocMeta,
  ResearchEvent,
  ResearchSnapshot,
  SessionBundle,
  SectionDiffPayload,
  SourceCapabilitiesState,
  StreamEvent,
  TemplatePreviewResult,
  TemplateSummary,
  TutorialStartPayload,
  TutorialStatusPayload,
  ReleaseNotesPayload,
  UpdateCheckPayload,
  UsageSummary,
} from "../types";
// The .ts extension is required: `npm test` runs node --test over the
// sources and Node's resolver needs a real extension on a value import
// (see lib/eventSeqIndex.ts).
import { qcReportExportUrl } from "./qcReport.ts";

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
 * Reset the session. The new-session UI sends an explicit neutral module and
 * empty context; the bodyless form remains available to older callers.
 */
export async function resetSession(opts?: {
  module_id?: string;
  discipline?: string;
  project_context?: string;
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

/**
 * Whether the session holds unsaved work (any chat history, document content,
 * or chat-authored figure). The in-app New-session / Open-project save gate
 * calls this so it matches the native window-close prompt's predicate.
 */
export async function checkUnsaved(): Promise<boolean> {
  const resp = await fetch("/api/session/unsaved");
  if (!resp.ok) throw new Error(`unsaved ${resp.status}`);
  const data = await resp.json();
  return !!data.unsaved;
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

export async function getSessionBundle(): Promise<SessionBundle> {
  const resp = await fetch("/api/session/bundle");
  if (!resp.ok) throw new Error(`session bundle ${resp.status}`);
  return resp.json();
}

/**
 * Just the imported-source permission report.
 *
 * The per-element sweep runs in the background after an import or a body
 * change, so the panel polls this until the status stops being `pending`.
 * Deliberately narrower than `getDoc`, whose payload also rebuilds the
 * outline, the lint report and the source-readiness plan.
 */
export async function getDocCapabilities(): Promise<SourceCapabilitiesState | null> {
  const resp = await fetch("/api/doc/capabilities");
  if (!resp.ok) throw new Error(`capabilities ${resp.status}`);
  return (await resp.json()).source_capabilities ?? null;
}

/* --- Chat-authored figures (diagrams / schematics / tables) --- */

/** Snapshot of the session's figures (also carried on every DocPayload). */
export async function getFigures(): Promise<Figure[]> {
  const resp = await fetch("/api/figures");
  if (!resp.ok) throw new Error(`figures ${resp.status}`);
  return (await resp.json()).figures as Figure[];
}

/** Delete a figure. 409 while a turn streams; returns the remaining figures. */
export async function deleteFigure(
  fid: string,
  lease: WorkspaceLeaseInput = {},
): Promise<Figure[]> {
  const resp = await fetch(`/api/figure/${encodeURIComponent(fid)}`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      workspace_id: lease.workspaceId,
      generation: lease.generation,
    }),
  });
  const data = await resp.json();
  if (!resp.ok || !data.ok) {
    throw new Error(data.error ?? `delete failed (${resp.status})`);
  }
  return data.figures as Figure[];
}

/** URL for a table figure's CSV download (server-rendered, text/csv). */
export function figureCsvUrl(fid: string): string {
  return `/api/figure/${encodeURIComponent(fid)}/csv`;
}

/** Step the document one version back/forward; null when at the end stop. */
interface WorkspaceLeaseInput {
  workspaceId?: number;
  generation?: number;
}

async function stepDoc(
  direction: "undo" | "redo",
  lease: WorkspaceLeaseInput = {},
): Promise<DocPayload | null> {
  const resp = await fetch(`/api/doc/${direction}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      workspace_id: lease.workspaceId,
      generation: lease.generation,
    }),
  });
  if (resp.status === 409) return null;
  const data = await resp.json();
  if (!resp.ok || !data.ok) {
    throw new Error(data.error ?? `${direction} failed (${resp.status})`);
  }
  return data;
}

export const undoDoc = (lease?: WorkspaceLeaseInput) => stepDoc("undo", lease);
export const redoDoc = (lease?: WorkspaceLeaseInput) => stepDoc("redo", lease);

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
export async function editDoc(
  ops: EditOp[],
  lease: WorkspaceLeaseInput = {},
): Promise<DocPayload> {
  const resp = await fetch("/api/doc/edit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ops,
      workspace_id: lease.workspaceId,
      generation: lease.generation,
    }),
  });
  const data = await resp.json();
  if (!resp.ok || !data.ok) {
    throw new Error(data.error ?? `edit failed (${resp.status})`);
  }
  return data;
}

/** Restore a session from a native .baspec package or legacy JSON project. */
export async function loadProjectFile(file: File): Promise<ProjectLoadResult> {
  const body = new FormData();
  body.append("file", file);
  const resp = await fetch("/api/project/load-file", {
    method: "POST",
    body,
  });
  const data = await resp.json();
  if (!resp.ok || !data.ok) {
    throw new Error(data.error ?? `load failed (${resp.status})`);
  }
  return data;
}

/**
 * Browser/dev fallback for the in-app save gate: download the project file.
 * The native pywebview Save dialog (`window.pywebview.api.save_project`) is
 * preferred when the bridge is present; this is only reached in a plain
 * browser (dev mode). The payload is fetched (awaited) BEFORE the caller
 * proceeds to reset/load, so a fast reset can't race the save and capture an
 * already-cleared session. The server names the file via Content-Disposition.
 */
export async function downloadProjectFile(): Promise<void> {
  const resp = await fetch("/api/project/save");
  if (!resp.ok) throw new Error(`save failed (${resp.status})`);
  const blob = await resp.blob();
  const cd = resp.headers.get("Content-Disposition") ?? "";
  const match = /filename="?([^"]+)"?/.exec(cd);
  const filename = match?.[1] ?? "buildaspec-project.json";
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Defer revocation: some browsers consume the object URL asynchronously, so
  // revoking synchronously after click() can cancel the download — which would
  // let the caller reset/load and lose the session with no saved file. Mirrors
  // downloadBlob() in lib/figures.ts.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Read SSE frames off a fetch Response body and yield parsed JSON. */
async function* readSse<T>(
  resp: Response,
  signal?: AbortSignal,
): AsyncGenerator<T> {
  if (!resp.body) return;
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
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
  } finally {
    // Reached on normal end, on an abort, AND when a consumer breaks out of
    // its `for await` — which is the case that used to leak, since unwinding
    // this generator released nothing. Cancelling an already-aborted reader
    // is a no-op, so the `signal` check is only to keep that path quiet.
    if (!signal?.aborted) {
      await reader.cancel().catch(() => undefined);
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

/* --- Full guided tutorial workspace --- */

export async function getTutorialStatus(): Promise<TutorialStatusPayload> {
  const resp = await fetch("/api/tutorial/status");
  const data = await resp.json();
  if (!resp.ok || !data.ok) {
    throw new Error(data.error ?? `tutorial status failed (${resp.status})`);
  }
  return data;
}

export async function startTutorialWorkspace(
  args: {
    requestId: string;
    workspaceId: number;
    generation: number;
  },
): Promise<TutorialStartPayload> {
  // The bundled showcase is the tutorial's only source. It is stated
  // explicitly so a mismatched server refuses loudly instead of guessing.
  const resp = await fetch("/api/tutorial/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      request_id: args.requestId,
      source: "showcase",
      workspace_id: args.workspaceId,
      generation: args.generation,
    }),
  });
  const data = await resp.json();
  if (!resp.ok || !data.ok) {
    throw new Error(data.error ?? `tutorial start failed (${resp.status})`);
  }
  return data;
}

async function tutorialTransition(
  path: string,
  body: Record<string, unknown>,
): Promise<SessionBundle> {
  const resp = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await resp.json();
  if (!resp.ok || !data.ok) {
    throw new Error(data.error ?? `tutorial transition failed (${resp.status})`);
  }
  return (data.session ?? data) as SessionBundle;
}

export const startTutorialScenario = (args: {
  tutorialId: string;
  workspaceId?: number;
  generation?: number;
  chapter: string;
}) =>
  tutorialTransition("/api/tutorial/scenario/start", {
    tutorial_id: args.tutorialId,
    workspace_id: args.workspaceId,
    generation: args.generation,
    chapter: args.chapter,
  });

export const finishTutorialScenario = (args: {
  tutorialId: string;
  workspaceId?: number;
  generation?: number;
}) =>
  tutorialTransition("/api/tutorial/scenario/finish", {
    tutorial_id: args.tutorialId,
    workspace_id: args.workspaceId,
    generation: args.generation,
  });

export const restoreTutorialWorkspace = (args: {
  tutorialId: string;
  workspaceId?: number;
  generation?: number;
}) =>
  tutorialTransition("/api/tutorial/restore", {
    tutorial_id: args.tutorialId,
    workspace_id: args.workspaceId,
    generation: args.generation,
  });

/* --- Reusable spec starters (templates) --- */

export async function listTemplates(): Promise<{
  templates: TemplateSummary[];
  invalidPersonalCount: number;
}> {
  const resp = await fetch("/api/templates");
  const data = await resp.json();
  if (!resp.ok || !data.ok) {
    throw new Error(data.error ?? `template list failed (${resp.status})`);
  }
  return {
    templates: data.templates ?? [],
    invalidPersonalCount: data.invalid_personal_count ?? 0,
  };
}

export async function previewTemplate(args: {
  name: string;
  description?: string;
  mode: "exact" | "ai_generalize";
}): Promise<TemplatePreviewResult> {
  const resp = await fetch("/api/templates/preview", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify(args),
  });
  if (!resp.ok) {
    const data = await resp.json().catch(() => ({}));
    throw new Error(data.error ?? `template preview failed (${resp.status})`);
  }
  if (!(resp.headers.get("Content-Type") ?? "").includes("text/event-stream")) {
    const data = await resp.json();
    if (!data.ok) throw new Error(data.error ?? "template preview failed");
    return data;
  }
  let preview: TemplatePreviewResult | null = null;
  for await (const event of readSse<
    | { type: "template_status"; stage: string }
    | { type: "template_preview"; preview: TemplatePreviewResult }
    | { type: "error"; message: string }
  >(resp)) {
    if (event.type === "error") throw new Error(event.message);
    if (event.type === "template_preview") preview = event.preview;
  }
  if (!preview) throw new Error("The template preview stream ended without a result.");
  return preview;
}

export async function commitTemplate(
  previewToken: string,
): Promise<TemplateSummary> {
  const resp = await fetch("/api/templates", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ preview_token: previewToken }),
  });
  const data = await resp.json();
  if (!resp.ok || !data.ok) {
    throw new Error(data.error ?? `template save failed (${resp.status})`);
  }
  return (data.template ?? data) as TemplateSummary;
}

export async function updateTemplate(
  id: string,
  fields: { name?: string; description?: string },
): Promise<TemplateSummary> {
  const resp = await fetch(`/api/templates/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(fields),
  });
  const data = await resp.json();
  if (!resp.ok || !data.ok) {
    throw new Error(data.error ?? `template update failed (${resp.status})`);
  }
  return (data.template ?? data) as TemplateSummary;
}

export async function deleteTemplate(id: string): Promise<void> {
  const resp = await fetch(`/api/templates/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  if (!resp.ok && resp.status !== 204) {
    const data = await resp.json().catch(() => ({}));
    throw new Error(data.error ?? `template delete failed (${resp.status})`);
  }
}

export async function importTemplate(file: File): Promise<TemplateSummary> {
  const body = new FormData();
  body.append("file", file);
  const resp = await fetch("/api/templates/import", { method: "POST", body });
  const data = await resp.json();
  if (!resp.ok || !data.ok) {
    throw new Error(data.error ?? `template import failed (${resp.status})`);
  }
  return (data.template ?? data) as TemplateSummary;
}

export const templateExportUrl = (id: string) =>
  `/api/templates/${encodeURIComponent(id)}/export`;

export async function instantiateTemplate(id: string): Promise<SessionBundle> {
  const resp = await fetch(
    `/api/templates/${encodeURIComponent(id)}/instantiate`,
    { method: "POST" },
  );
  const data = await resp.json();
  if (!resp.ok || !data.ok) {
    throw new Error(data.error ?? `template start failed (${resp.status})`);
  }
  const session = (data.session ?? data) as SessionBundle;
  if (typeof data.warning === "string" && data.warning) {
    session.template_warning = data.warning;
  }
  return session;
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

export async function startResearch(
  lease: WorkspaceLeaseInput = {},
): Promise<void> {
  const resp = await fetch("/api/research/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      workspace_id: lease.workspaceId,
      generation: lease.generation,
    }),
  });
  const data = await resp.json();
  if (!resp.ok || !data.ok) {
    throw new Error(data.error ?? `research start failed (${resp.status})`);
  }
}

/** Stop the running research fan-out. Discards whatever it found so far. */
export async function stopResearch(
  lease: WorkspaceLeaseInput = {},
): Promise<void> {
  const resp = await fetch("/api/research/stop", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      workspace_id: lease.workspaceId,
      generation: lease.generation,
    }),
  });
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

/**
 * Follow the active/last research run's SSE stream until it closes.
 *
 * `signal` aborts the underlying response so a superseded follower or a
 * workspace transition releases the connection immediately. Breaking out of
 * the `for await` alone does not: it unwinds this generator, but nothing
 * cancels the body the browser is still reading.
 */
export async function* streamResearch(
  signal?: AbortSignal,
): AsyncGenerator<ResearchEvent> {
  const resp = await fetch("/api/research/stream", { signal });
  if (!resp.ok || !resp.body) return;
  yield* readSse<ResearchEvent>(resp, signal);
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

/* --- Reference documents (read-only background material) --- */

export async function uploadReference(
  file: File,
  lease: WorkspaceLeaseInput = {},
): Promise<{ reference_docs: ReferenceDocMeta[]; warnings: string[] }> {
  const form = new FormData();
  form.append("file", file);
  const query = new URLSearchParams();
  if (lease.workspaceId !== undefined) {
    query.set("workspace_id", String(lease.workspaceId));
  }
  if (lease.generation !== undefined) {
    query.set("generation", String(lease.generation));
  }
  const suffix = query.size ? `?${query}` : "";
  const resp = await fetch(`/api/reference/upload${suffix}`, {
    method: "POST",
    body: form,
  });
  const data = await resp.json();
  if (!resp.ok || !data.ok) {
    throw new Error(data.error ?? `attach failed (${resp.status})`);
  }
  return { reference_docs: data.reference_docs, warnings: data.warnings ?? [] };
}

export async function deleteReference(
  rid: string,
  lease: WorkspaceLeaseInput = {},
): Promise<{
  reference_docs: ReferenceDocMeta[];
  suggested_prompts: string[];
  figures: Figure[];
}> {
  const resp = await fetch(`/api/reference/${encodeURIComponent(rid)}`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      workspace_id: lease.workspaceId,
      generation: lease.generation,
    }),
  });
  const data = await resp.json();
  if (!resp.ok || !data.ok) {
    throw new Error(data.error ?? `remove failed (${resp.status})`);
  }
  return {
    reference_docs: data.reference_docs,
    suggested_prompts: data.suggested_prompts ?? [],
    figures: data.figures ?? [],
  };
}

/* --- Final QC on Opus 5 --- */

export class QcStartError extends Error {
  readonly status: number;
  readonly code?: string;
  readonly moduleSectionCompatibility?: QcModuleSectionCompatibility;

  constructor(
    message: string,
    status: number,
    code?: string,
    moduleSectionCompatibility?: QcModuleSectionCompatibility,
  ) {
    super(message);
    this.name = "QcStartError";
    this.status = status;
    this.code = code;
    this.moduleSectionCompatibility = moduleSectionCompatibility;
  }
}

export async function startQc(
  acknowledgeScopeMismatch = false,
  lease: WorkspaceLeaseInput = {},
): Promise<void> {
  const resp = await fetch("/api/qc/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      acknowledge_scope_mismatch: acknowledgeScopeMismatch,
      workspace_id: lease.workspaceId,
      generation: lease.generation,
    }),
  });
  const data = await resp.json();
  if (!resp.ok || !data.ok) {
    throw new QcStartError(
      data.error ?? `QC start failed (${resp.status})`,
      resp.status,
      typeof data.code === "string" ? data.code : undefined,
      data.module_section_compatibility as
        | QcModuleSectionCompatibility
        | undefined,
    );
  }
}

/** Request a stop; the worker may continue briefly to preserve paid partial activity. */
export async function stopQc(
  lease: WorkspaceLeaseInput = {},
): Promise<void> {
  const resp = await fetch("/api/qc/stop", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      workspace_id: lease.workspaceId,
      generation: lease.generation,
    }),
  });
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
export async function applyQc(
  findingIds: string[],
  lease: WorkspaceLeaseInput = {},
  previewBasis?: QcApplyPreviewBasis,
): Promise<QcApplyResult> {
  const resp = await fetch("/api/qc/apply", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      finding_ids: findingIds,
      workspace_id: lease.workspaceId,
      generation: lease.generation,
      preview_basis: previewBasis,
    }),
  });
  const data = await resp.json();
  if (!resp.ok || !data.ok) {
    throw new Error(data.error ?? `QC apply failed (${resp.status})`);
  }
  return data;
}

/** Preview a combined QC batch without mutating the document. */
export async function previewQcApply(
  findingIds: string[],
  lease: WorkspaceLeaseInput = {},
): Promise<QcApplyPreviewResult> {
  const resp = await fetch("/api/qc/apply/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      finding_ids: findingIds,
      workspace_id: lease.workspaceId,
      generation: lease.generation,
    }),
  });
  const data = await resp.json();
  if (!resp.ok || !data.ok) {
    throw new Error(data.error ?? `QC apply preview failed (${resp.status})`);
  }
  return data;
}

/**
 * Download a Final QC report artifact, surfacing failures instead of losing
 * them. A bare `<a download>` gives no feedback at all when the server
 * answers 409 (report selection changed) or 500 — in the native shell the
 * click just looks dead. Fetch first so an error's exact server message can
 * be shown beside the button, then hand the bytes to the same anchor-click
 * save `downloadProjectFile` uses. The server names the file via
 * Content-Disposition; `runId` pins the request to the report identity shown
 * in the snapshot (the server rejects a changed selection).
 */
export async function downloadQcReport(
  format: "docx" | "json",
  runId: unknown,
): Promise<void> {
  const resp = await fetch(qcReportExportUrl(format, runId));
  if (!resp.ok) {
    let message = "";
    try {
      const data = await resp.json();
      message = typeof data?.error === "string" ? data.error : "";
    } catch {
      // A non-JSON failure body still gets the status-code fallback.
    }
    throw new Error(message || `QC report download failed (${resp.status})`);
  }
  const blob = await resp.blob();
  const cd = resp.headers.get("Content-Disposition") ?? "";
  const match = /filename="?([^";]+)"?/.exec(cd);
  const filename =
    match?.[1] ?? (format === "json" ? "final-qc-report.json" : "final-qc-report.docx");
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Deferred revocation, same as downloadProjectFile / lib/figures.downloadBlob:
  // revoking synchronously after click() can cancel the download.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Dismiss a finding (remembered across re-runs). */
export async function dismissQc(
  findingId: string,
  reason: string,
  lease: WorkspaceLeaseInput = {},
): Promise<QcSnapshot> {
  const resp = await fetch("/api/qc/dismiss", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      finding_id: findingId,
      reason,
      workspace_id: lease.workspaceId,
      generation: lease.generation,
    }),
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

// --- Developer tools / diagnostics -----------------------------------------

/** Environment + session snapshot for the Developer tools modal. */
export async function getDiagnostics(): Promise<DiagnosticsSnapshot> {
  const resp = await fetch("/api/diagnostics");
  if (!resp.ok) throw new Error(`diagnostics ${resp.status}`);
  return resp.json();
}

/** Tail of the local activity log. */
export async function getDiagnosticsLog(tail = 500): Promise<DiagnosticsLog> {
  const resp = await fetch(`/api/diagnostics/log?tail=${tail}`);
  if (!resp.ok) throw new Error(`diagnostics log ${resp.status}`);
  return resp.json();
}

/** Trace-run inventory (newest first). */
export async function getDiagnosticsTraces(): Promise<DiagnosticsTraces> {
  const resp = await fetch("/api/diagnostics/traces");
  if (!resp.ok) throw new Error(`diagnostics traces ${resp.status}`);
  return resp.json();
}

/** Recent trace events + open spans of the current run. */
export async function getDiagnosticsActivity(
  tail = 200,
): Promise<DiagnosticsActivity> {
  const resp = await fetch(`/api/diagnostics/activity?tail=${tail}`);
  if (!resp.ok) throw new Error(`diagnostics activity ${resp.status}`);
  return resp.json();
}

export async function checkUpdate(force = false): Promise<UpdateCheckPayload> {
  const resp = await fetch(`/api/update/check${force ? "?force=true" : ""}`);
  if (!resp.ok) throw new Error(`update check ${resp.status}`);
  return resp.json();
}

/**
 * Release notes bundled with this build.
 *
 * Default: only what the user has not seen (drives the one-time What's-new
 * modal after an update). `all` forces the current version's entry, which is
 * what the Settings button asks for.
 */
export async function getReleaseNotes(
  all = false,
): Promise<ReleaseNotesPayload> {
  const resp = await fetch(`/api/release-notes${all ? "?all=true" : ""}`);
  if (!resp.ok) throw new Error(`release notes ${resp.status}`);
  return resp.json();
}

export async function markReleaseNotesSeen(): Promise<void> {
  await fetch("/api/release-notes/seen", { method: "POST" });
}

export async function installUpdate(): Promise<void> {
  const resp = await fetch("/api/update/install", { method: "POST" });
  const data = await resp.json();
  if (!resp.ok || !data.ok) {
    throw new Error(data.error ?? `update install failed (${resp.status})`);
  }
}
