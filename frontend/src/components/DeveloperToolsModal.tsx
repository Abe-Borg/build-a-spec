import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  DiagnosticsActivity,
  DiagnosticsLog,
  DiagnosticsSnapshot,
  DiagnosticsTraces,
} from "../types";
import {
  getDiagnostics,
  getDiagnosticsActivity,
  getDiagnosticsLog,
  getDiagnosticsTraces,
} from "../lib/api";
import { useDialogFocus } from "../lib/dialogFocus";

interface Props {
  open: boolean;
  onClose: () => void;
}

/** 12345678 → "11.8 MB" */
function fmtBytes(n: number | undefined | null): string {
  if (!n) return "0 B";
  if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  if (n >= 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${n} B`;
}

function fmtClock(ts: number | null | undefined): string {
  if (typeof ts !== "number") return "—";
  const d = new Date(ts * 1000);
  const pad = (v: number) => String(v).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function fmtWhen(ts: number | null | undefined): string {
  return typeof ts === "number" ? new Date(ts * 1000).toLocaleString() : "—";
}

/** Compact `k=v` rendering of an event's own fields (ts/span_id/type cut). */
function eventFields(event: Record<string, unknown>): string {
  return Object.entries(event)
    .filter(([k]) => k !== "ts" && k !== "span_id" && k !== "type")
    .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
    .join("  ");
}

function Row({ name, value, mono }: { name: string; value: string; mono?: boolean }) {
  return (
    <div className="flex gap-3 py-0.5 text-xs">
      <span className="w-40 flex-none text-ink-faint">{name}</span>
      <span className={`min-w-0 break-all text-ink ${mono ? "font-mono text-[11px]" : ""}`}>
        {value}
      </span>
    </div>
  );
}

/**
 * The Settings → Developer tools diagnostics view. Read-only: everything it
 * shows is already recorded locally by the always-on logging/tracing layer;
 * this modal is just the window onto it, plus the bundle download. Manual
 * Refresh only — a modal being actively read doesn't need a poll timer.
 */
export default function DeveloperToolsModal({ open, onClose }: Props) {
  const [snapshot, setSnapshot] = useState<DiagnosticsSnapshot | null>(null);
  const [log, setLog] = useState<DiagnosticsLog | null>(null);
  const [traces, setTraces] = useState<DiagnosticsTraces | null>(null);
  const [activity, setActivity] = useState<DiagnosticsActivity | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [refreshing, setRefreshing] = useState(false);
  const [typeFilter, setTypeFilter] = useState<string>("");
  const [copied, setCopied] = useState<string>("");
  const containerRef = useRef<HTMLDivElement>(null);
  const refreshBtnRef = useRef<HTMLButtonElement>(null);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    const failures: string[] = [];
    const [snap, logTail, runList, recent] = await Promise.allSettled([
      getDiagnostics(),
      getDiagnosticsLog(400),
      getDiagnosticsTraces(),
      getDiagnosticsActivity(200),
    ]);
    if (snap.status === "fulfilled") setSnapshot(snap.value);
    else failures.push("snapshot");
    if (logTail.status === "fulfilled") setLog(logTail.value);
    else failures.push("activity log");
    if (runList.status === "fulfilled") setTraces(runList.value);
    else failures.push("trace list");
    if (recent.status === "fulfilled") setActivity(recent.value);
    else failures.push("recent activity");
    setErrors(failures);
    setRefreshing(false);
  }, []);

  useEffect(() => {
    if (open) {
      setTypeFilter("");
      setCopied("");
      void refresh();
    }
  }, [open, refresh]);

  useDialogFocus(open, containerRef, refreshBtnRef, onClose);

  const eventTypes = useMemo(
    () =>
      [...new Set((activity?.events ?? []).map((e) => e.type))].sort(),
    [activity],
  );
  const shownEvents = useMemo(() => {
    const events = activity?.events ?? [];
    const filtered = typeFilter
      ? events.filter((e) => e.type === typeFilter)
      : events;
    // Newest first — the question is always "what just happened?".
    return [...filtered].reverse();
  }, [activity, typeFilter]);

  if (!open) return null;

  const copy = async (label: string, text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(label);
      window.setTimeout(() => setCopied(""), 2000);
    } catch {
      // Clipboard unavailable (permissions) — silently do nothing.
    }
  };

  const openViewer = () => {
    const bridge = window.pywebview?.api?.open_in_browser;
    if (bridge) {
      void bridge("/api/trace/viewer");
    } else {
      window.open("/api/trace/viewer", "_blank", "noopener");
    }
  };

  const label = "text-[11px] font-medium tracking-wide text-ink-dim uppercase";
  const btn =
    "rounded-lg border border-edge bg-raised px-3 py-1.5 text-sm text-ink transition-colors hover:border-accent hover:text-accent disabled:pointer-events-none disabled:opacity-40";
  const app = snapshot?.app;
  const sess = snapshot?.session;

  return (
    <div
      className="fixed inset-0 z-[60] flex items-start justify-center bg-black/60 p-4 pt-10"
      onClick={onClose}
    >
      <div
        ref={containerRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label="Developer tools"
        className="flex max-h-[85vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-edge bg-surface shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between gap-3 border-b border-edge px-5 py-3">
          <div className="min-w-0">
            <h2 className="font-[family-name:var(--font-display)] text-lg font-semibold">
              Developer tools
            </h2>
            <p className="truncate text-xs text-ink-faint">
              Local diagnostics — logs, traces, and session state for
              troubleshooting.
            </p>
          </div>
          <div className="flex flex-none items-center gap-2">
            <button
              ref={refreshBtnRef}
              className={btn}
              onClick={() => void refresh()}
              disabled={refreshing}
            >
              {refreshing ? "Refreshing…" : "Refresh"}
            </button>
            <button
              onClick={onClose}
              className="rounded-lg px-2 py-1 text-ink-dim transition-colors hover:text-ink"
              title="Close"
            >
              ✕
            </button>
          </div>
        </div>

        <div className="min-h-0 flex-1 space-y-6 overflow-y-auto px-5 py-5">
          {errors.length > 0 && (
            <p className="text-xs text-err">
              Could not load: {errors.join(", ")}. Refresh to retry.
            </p>
          )}

          {/* --- Environment & app --- */}
          <section>
            <div className="flex items-center justify-between">
              <p className={label}>Environment</p>
              {snapshot && (
                <button
                  className="text-xs text-accent hover:underline"
                  onClick={() =>
                    void copy(
                      "snapshot",
                      JSON.stringify(snapshot, null, 2),
                    )
                  }
                >
                  {copied === "snapshot" ? "Copied ✓" : "Copy snapshot JSON"}
                </button>
              )}
            </div>
            {app ? (
              <div className="mt-2">
                <Row name="App" value={`${app.name} ${app.version}${app.frozen ? " (packaged)" : ""}${app.dev_mode ? " (dev mode)" : ""}`} />
                <Row name="Platform" value={`${app.platform} · Python ${app.python} · port ${app.port}`} />
                <Row name="Models" value={`interview ${app.models.interview} · research ${app.models.research} · QC ${app.models.qc}`} />
                <Row
                  name="API key"
                  value={
                    snapshot.key.present
                      ? `${snapshot.key.source} ${snapshot.key.masked}`
                      : "not configured"
                  }
                  mono
                />
                <Row
                  name="Tracing"
                  value={
                    snapshot.tracing.enabled
                      ? `on (${snapshot.tracing.level})${snapshot.tracing.run_id ? ` · run ${snapshot.tracing.run_id}` : ""}`
                      : "off (BUILD_A_SPEC_TRACE=0)"
                  }
                />
                <Row name="Trace folder" value={snapshot.tracing.run_dir ?? snapshot.tracing.root} mono />
                <Row
                  name="Activity log"
                  value={
                    snapshot.logging.enabled
                      ? `on (${snapshot.logging.level}) · ${fmtBytes(snapshot.logging.size_bytes)}`
                      : "off (BUILD_A_SPEC_LOG=0)"
                  }
                />
                {snapshot.logging.file && (
                  <Row name="Log file" value={snapshot.logging.file} mono />
                )}
              </div>
            ) : (
              <p className="mt-2 text-xs text-ink-faint">Not loaded.</p>
            )}
          </section>

          {/* --- Session state --- */}
          <section>
            <p className={label}>Session state</p>
            {sess && snapshot ? (
              <div className="mt-2">
                <Row
                  name="Workspace"
                  value={`${snapshot.workspace.scope} · id ${snapshot.workspace.workspace_id} · generation ${snapshot.workspace.generation}${snapshot.workspace.busy.length ? ` · busy: ${snapshot.workspace.busy.join(", ")}` : ""}`}
                />
                <Row
                  name="Document"
                  value={`version ${sess.doc_version_index + 1} of ${sess.doc_version_count}${sess.baseline_index !== null ? ` · master at ${sess.baseline_index}` : ""}${sess.doc_empty ? " · empty" : ""}`}
                />
                <Row
                  name="Module"
                  value={`${sess.module_id}${sess.discipline ? ` · ${sess.discipline}` : ""}`}
                />
                <Row
                  name="Contents"
                  value={`${sess.history_len} history messages · ${sess.figures} figures · ${sess.references} references · ${sess.suggested_prompts} suggested prompts`}
                />
                <Row
                  name="Flags"
                  value={`turn ${sess.turn_active ? "STREAMING" : "idle"} · ${sess.unsaved ? "unsaved work" : "nothing unsaved"}${sess.stop_requested ? " · stop requested" : ""}${sess.import_report_present ? " · import report" : ""}`}
                />
                {sess.source.retained && (
                  <Row
                    name="Imported source"
                    value={`${sess.source.filename || "(unnamed)"} · ${fmtBytes(sess.source.bytes)}`}
                  />
                )}
                <Row
                  name="Spend (est.)"
                  value={`$${snapshot.usage.estimated_cost_usd.total.toFixed(3)} across ${snapshot.usage.turns} turn${snapshot.usage.turns === 1 ? "" : "s"}`}
                />
              </div>
            ) : (
              <p className="mt-2 text-xs text-ink-faint">Not loaded.</p>
            )}
          </section>

          {/* --- Recent activity (trace events) --- */}
          <section>
            <div className="flex items-center justify-between gap-2">
              <p className={label}>Recent activity</p>
              {eventTypes.length > 0 && (
                <select
                  className="rounded-lg border border-edge bg-raised px-2 py-1 text-xs text-ink"
                  value={typeFilter}
                  onChange={(e) => setTypeFilter(e.target.value)}
                  title="Filter by event type"
                >
                  <option value="">all types</option>
                  {eventTypes.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
              )}
            </div>
            {activity?.enabled === false ? (
              <p className="mt-2 text-xs text-ink-faint">
                Tracing is off — no activity is being recorded. Remove{" "}
                <code>BUILD_A_SPEC_TRACE=0</code> to re-enable it.
              </p>
            ) : activity && shownEvents.length > 0 ? (
              <>
                {activity.spans.length > 0 && (
                  <p className="mt-2 text-xs text-warn">
                    In flight:{" "}
                    {activity.spans
                      .map((s) => `${s.kind} “${s.name}”`)
                      .join(", ")}
                  </p>
                )}
                <pre className="mt-2 max-h-56 overflow-auto rounded-lg border border-edge bg-bg p-2 font-mono text-[11px] leading-relaxed text-ink-dim">
                  {shownEvents
                    .map(
                      (e) =>
                        `${fmtClock(e.ts)}  ${e.type.padEnd(18)} ${eventFields(e)}`,
                    )
                    .join("\n")}
                </pre>
                <p className="mt-1 text-[11px] text-ink-faint">
                  Newest first — the most recent {shownEvents.length} recorded
                  event{shownEvents.length === 1 ? "" : "s"} of this run
                  {typeFilter ? ` (type ${typeFilter})` : ""}.
                </p>
              </>
            ) : (
              <p className="mt-2 text-xs text-ink-faint">
                No events recorded yet this run.
              </p>
            )}
          </section>

          {/* --- Activity log --- */}
          <section>
            <div className="flex items-center justify-between">
              <p className={label}>Activity log</p>
              {log && log.lines.length > 0 && (
                <button
                  className="text-xs text-accent hover:underline"
                  onClick={() => void copy("log", log.lines.join("\n"))}
                >
                  {copied === "log" ? "Copied ✓" : "Copy shown lines"}
                </button>
              )}
            </div>
            {log?.enabled === false ? (
              <p className="mt-2 text-xs text-ink-faint">
                Logging is off (<code>BUILD_A_SPEC_LOG=0</code>) — no log file
                is being written.
              </p>
            ) : log ? (
              <>
                <pre className="mt-2 max-h-56 overflow-auto rounded-lg border border-edge bg-bg p-2 font-mono text-[11px] leading-relaxed text-ink-dim">
                  {log.lines.length > 0
                    ? log.lines.join("\n")
                    : "(the log file is empty so far)"}
                </pre>
                {log.path && (
                  <p className="mt-1 break-all font-mono text-[11px] text-ink-faint">
                    {log.path} · {fmtBytes(log.size_bytes)}
                  </p>
                )}
              </>
            ) : (
              <p className="mt-2 text-xs text-ink-faint">Not loaded.</p>
            )}
          </section>

          {/* --- Trace files --- */}
          <section>
            <div className="flex items-center justify-between">
              <p className={label}>Trace files</p>
              <button className={btn} onClick={openViewer}>
                Open trace viewer
              </button>
            </div>
            {traces && traces.runs.length > 0 ? (
              <>
                <div className="mt-2 space-y-1">
                  {traces.runs.slice(0, 8).map((run) => (
                    <div
                      key={run.run_id}
                      className="flex items-baseline gap-2 text-xs"
                    >
                      <span className="min-w-0 break-all font-mono text-[11px] text-ink">
                        {run.run_id}
                      </span>
                      {run.current && (
                        <span className="flex-none rounded-full bg-accent/15 px-2 text-[10px] font-medium text-accent">
                          current
                        </span>
                      )}
                      <span className="flex-none text-ink-faint">
                        {fmtWhen(run.started_at)} · {fmtBytes(run.size_bytes)}
                      </span>
                    </div>
                  ))}
                </div>
                <p className="mt-2 break-all font-mono text-[11px] text-ink-faint">
                  {traces.root}
                </p>
                <p className="mt-1 text-[11px] text-ink-faint">
                  In the viewer, click “Open trace folder…” and pick one run
                  directory from the path above.
                </p>
              </>
            ) : (
              <p className="mt-2 text-xs text-ink-faint">
                No trace runs recorded yet
                {traces ? ` in ${traces.root}` : ""}.
              </p>
            )}
          </section>

          {/* --- Diagnostics bundle --- */}
          <section>
            <p className={label}>Diagnostics bundle</p>
            <p className="mt-2 text-xs text-ink-faint">
              One .zip with the snapshot above, the log files, and the current
              run&apos;s trace — everything a developer needs to reconstruct
              what happened. It contains your draft text and prompts (that is
              what makes it useful) and is generated locally and saved to your
              machine only — share it deliberately.
            </p>
            <a
              href="/api/diagnostics/bundle"
              download
              className="mt-2 inline-block rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white transition-opacity hover:opacity-90"
            >
              Download diagnostics bundle
            </a>
          </section>
        </div>
      </div>
    </div>
  );
}
