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

function fmtDuration(seconds: number | null | undefined): string {
  if (typeof seconds !== "number") return "not available";
  const whole = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const secs = whole % 60;
  return hours ? `${hours}h ${minutes}m` : minutes ? `${minutes}m ${secs}s` : `${secs}s`;
}

function fmtCounts(values: Record<string, number> | undefined): string {
  if (!values) return "none";
  const entries = Object.entries(values).filter(([, count]) => count > 0);
  return entries.length
    ? entries.map(([name, count]) => `${name} ${count}`).join(" / ")
    : "none";
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
    // The shared external-link bridge (the shell has no reliable
    // target=_blank); the viewer is app-served, so the app's own origin
    // is the right absolute URL in both the shell and dev.
    const url = new URL("/api/trace/viewer", window.location.origin).toString();
    const bridge = window.pywebview?.api?.open_external_link;
    if (bridge) {
      void bridge(url);
    } else {
      window.open(url, "_blank", "noopener");
    }
  };

  const label = "text-[11px] font-medium tracking-wide text-ink-dim uppercase";
  const btn =
    "rounded-lg border border-edge bg-raised px-3 py-1.5 text-sm text-ink transition-colors hover:border-accent hover:text-accent disabled:pointer-events-none disabled:opacity-40";
  const app = snapshot?.app;
  const sess = snapshot?.session;
  const process = snapshot?.process;
  const trace = snapshot?.tracing;
  const traceSummary = trace?.summary;
  const recorder = trace?.recorder_health;
  const logRetention = snapshot?.logging.retention_policy;
  const lastLogRetention = snapshot?.logging.last_retention;

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
                {process && (
                  <Row
                    name="Process"
                    value={`PID ${process.pid} (parent ${process.parent_pid}) / up ${fmtDuration(process.uptime_seconds)} / ${process.thread_count} threads / ${process.timezone ?? "local time"} ${process.utc_offset}`}
                  />
                )}
                {process && (
                  <Row name="Diagnostic run" value={process.diagnostic_run_id} mono />
                )}
                {snapshot.server && (
                  <Row
                    name="Bound server"
                    value={`${snapshot.server.host}:${snapshot.server.bound_port}`}
                    mono
                  />
                )}
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
                    !snapshot.tracing.enabled
                      ? "off (BUILD_A_SPEC_TRACE=0)"
                      : snapshot.tracing.capture_active
                        ? `on (${snapshot.tracing.level})${snapshot.tracing.run_id ? ` · run ${snapshot.tracing.run_id}` : ""}`
                        : recorder?.state === "failed"
                          ? `FAILED (writer ${recorder.fatal_error ?? "unknown"})`
                        : snapshot.tracing.initialization_succeeded === false
                          ? `FAILED (${snapshot.tracing.startup_failure?.exception_type ?? "unknown"})`
                          : `inactive (${recorder?.state ?? "not initialized"})`
                  }
                />
                {snapshot.tracing.startup_failure && (
                  <Row
                    name="Trace startup failure"
                    value={`${snapshot.tracing.startup_failure.exception_type} / ${snapshot.tracing.startup_failure.count} attempt${snapshot.tracing.startup_failure.count === 1 ? "" : "s"} / ${fmtWhen(snapshot.tracing.startup_failure.last_failed_at)}`}
                    mono
                  />
                )}
                <Row name="Trace folder" value={snapshot.tracing.run_dir ?? snapshot.tracing.root} mono />
                <Row
                  name="Activity log"
                  value={
                    !snapshot.logging.enabled
                      ? "off (BUILD_A_SPEC_LOG=0)"
                      : snapshot.logging.capture_active
                        ? `on (${snapshot.logging.level}) · ${fmtBytes(snapshot.logging.size_bytes)}`
                        : snapshot.logging.initialization_succeeded === false
                          ? `FAILED (${snapshot.logging.initialization_failure_type ?? "unknown"})`
                          : "configured, not initialized"
                  }
                />
                {snapshot.logging.initialization_failure_type && (
                  <Row
                    name="Log startup failure"
                    value={`${snapshot.logging.initialization_failure_type} / ${snapshot.logging.initialization_failure_count} attempt${snapshot.logging.initialization_failure_count === 1 ? "" : "s"}${snapshot.logging.initialization_last_failure_at ? ` / ${fmtWhen(snapshot.logging.initialization_last_failure_at)}` : ""}`}
                    mono
                  />
                )}
                {snapshot.logging.file && (
                  <Row name="Log file" value={snapshot.logging.file} mono />
                )}
                <Row
                  name="Log run folder"
                  value={snapshot.logging.run_dir}
                  mono
                />
                {logRetention && (
                  <Row
                    name="Log retention"
                    value={`${logRetention.max_runs || "unlimited"} runs / ${logRetention.max_age_days || "unlimited"} days / ${logRetention.max_bytes ? fmtBytes(logRetention.max_bytes) : "unlimited bytes"}`}
                  />
                )}
                {lastLogRetention && Object.keys(lastLogRetention).length > 0 && (
                  <Row
                    name="Last log cleanup"
                    value={`${Number(lastLogRetention.removed_runs ?? 0)} runs removed / ${Number(lastLogRetention.protected_runs ?? 0)} protected / ${Number(lastLogRetention.failures ?? 0)} failures`}
                  />
                )}
              </div>
            ) : (
              <p className="mt-2 text-xs text-ink-faint">Not loaded.</p>
            )}
          </section>

          {/* --- Trace integrity and coverage --- */}
          <section>
            <p className={label}>Trace integrity</p>
            {trace ? (
              <div className="mt-2">
                <Row
                  name="Capture"
                  value={
                    !trace.enabled
                      ? "off"
                      : trace.capture_active
                        ? `schema ${trace.trace_schema_version ?? "unknown"} / ${traceSummary?.coverage ?? "unknown coverage"} / metadata flush ${trace.metadata_flush_complete ? "complete" : "incomplete"}`
                        : recorder?.state === "failed"
                          ? `writer failed: ${recorder.fatal_error ?? "unknown"}`
                        : trace.initialization_succeeded === false
                          ? `startup failed: ${trace.startup_failure?.exception_type ?? "unknown"}`
                          : `inactive: ${recorder?.state ?? "not initialized"}`
                  }
                />
                <Row
                  name="Writer"
                  value={
                    recorder
                      ? `${recorder.state ?? "unknown"} / thread ${recorder.thread_alive ? "alive" : "not alive"} / ${recorder.records_written ?? 0} written / ${recorder.dropped_records ?? 0} dropped / ${(recorder.write_failures ?? 0) + (recorder.metadata_write_failures ?? 0)} write failures`
                      : "not active"
                  }
                />
                {recorder && (
                  <Row
                    name="Writer queue"
                    value={`${recorder.queue_depth ?? 0} / ${recorder.queue_max_records ?? "?"} records / ${fmtBytes(recorder.resident_payload_bytes)} / ${fmtBytes(recorder.queue_max_bytes)} resident / high-water ${recorder.queue_high_watermark ?? 0} records, ${fmtBytes(recorder.queue_payload_high_watermark)} / ${recorder.open_spans ?? 0} open spans${recorder.drain_timed_out ? " / drain timed out" : ""}`}
                  />
                )}
                {recorder && (
                  <Row
                    name="Active-run storage"
                    value={`${fmtBytes(recorder.data_bytes_written)} written / ${recorder.max_run_bytes ? fmtBytes(recorder.max_run_bytes) : "unlimited"}${recorder.storage_limit_reached ? " / limit affected capture" : ""}`}
                  />
                )}
                {recorder && (
                  <Row
                    name="Health checkpoint"
                    value={`${recorder.metadata_checkpointed_revision ?? 0} / ${recorder.metadata_revision ?? 0} persisted${recorder.metadata_dirty ? " / pending checkpoint" : ""}`}
                  />
                )}
                {recorder && (recorder.dropped_records ?? 0) > 0 && (
                  <Row
                    name="Capture gaps"
                    value={`${recorder.dropped_records ?? 0} total / queue count ${recorder.queue_count_drops ?? 0} / queue bytes ${recorder.queue_byte_drops ?? 0} / run cap ${recorder.run_byte_limit_drops ?? 0} / serialization ${recorder.serialization_failures ?? 0} / unknown file ${recorder.unknown_file_drops ?? 0} / write ${recorder.write_failure_drops ?? 0}${recorder.last_drop_reason ? ` / last: ${recorder.last_drop_reason}` : ""}`}
                  />
                )}
                {recorder?.fatal_error && (
                  <Row name="Writer fatal error" value={recorder.fatal_error} mono />
                )}
                {traceSummary && (
                  <Row
                    name="Coverage totals"
                    value={`${traceSummary.events_total ?? 0} events (${traceSummary.failed_events ?? 0} failed) / ${traceSummary.spans_closed ?? 0} spans (${traceSummary.failed_spans ?? 0} failed) / ${traceSummary.prompts_stored ?? 0} prompts`}
                  />
                )}
                {traceSummary && (
                  <Row
                    name="Request outcomes"
                    value={fmtCounts(traceSummary.request_outcome_counts)}
                  />
                )}
                <Row
                  name="Retention"
                  value={`${trace.retention_policy.max_runs || "unlimited"} runs / ${trace.retention_policy.max_age_days || "unlimited"} days / ${trace.retention_policy.max_bytes ? fmtBytes(trace.retention_policy.max_bytes) : "unlimited bytes"}`}
                />
                {traceSummary?.last_recorded_at && (
                  <Row name="Last trace record" value={fmtWhen(traceSummary.last_recorded_at)} />
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
                  name="Document shape"
                  value={`${sess.document_shape.parts} parts / ${sess.document_shape.articles} articles / ${sess.document_shape.paragraphs} paragraphs / max depth ${sess.document_shape.maximum_paragraph_depth}`}
                />
                <Row
                  name="Edit state"
                  value={`undo ${sess.can_undo ? "yes" : "no"} / redo ${sess.can_redo ? "yes" : "no"} / transaction ${sess.turn_transaction_open ? (sess.turn_transaction_dirty ? "open/dirty" : "open/clean") : "closed"}`}
                />
                <Row
                  name="Module"
                  value={`${sess.module_id}${sess.discipline ? ` · ${sess.discipline}` : ""}`}
                />
                <Row
                  name="Contents"
                  value={`${sess.history_len} history messages · ${sess.figures} figures · ${sess.references} references · ${sess.suggested_prompts} suggested prompts · ${sess.followups?.open ?? 0} waiting on you`}
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
                {sess.source.retained && (
                  <Row
                    name="Source editing"
                    value={`${sess.source.capabilities_status ?? "not analyzed"} / ${sess.source.capability_operation_counts.allowed} allowed / ${sess.source.capability_operation_counts.denied} denied${sess.source.global_edit_blockers.causes.length ? ` / global: ${sess.source.global_edit_blockers.causes.join(", ")}` : ""}`}
                  />
                )}
                {sess.source.retained && (
                  <Row
                    name="Capability evidence"
                    value={sess.source.capabilities_snapshot_source}
                    mono
                  />
                )}
                <Row
                  name="Context gauge"
                  value={sess.last_context_tokens === null ? "not measured" : `${sess.last_context_tokens.toLocaleString()} tokens`}
                />
                <Row
                  name="Spend (est.)"
                  value={`$${snapshot.usage.estimated_cost_usd.total.toFixed(3)} across ${snapshot.usage.turns} turn${snapshot.usage.turns === 1 ? "" : "s"}`}
                />
              </div>
            ) : (
              <p className="mt-2 text-xs text-ink-faint">Not loaded.</p>
            )}
          </section>

          {/* --- Long-running engines and import evidence --- */}
          <section>
            <p className={label}>Engine state</p>
            {sess ? (
              <div className="mt-2">
                <Row
                  name="Research"
                  value={`${sess.research.status} / worker ${sess.research.worker_alive ? "alive" : "idle"} / round ${sess.research.active_round} / ${sess.research.event_count} events / ${sess.research.incomplete_dimensions.length} incomplete dimensions${sess.research.error_present ? ` / error ${sess.research.error_kind ?? "unclassified"}` : ""}`}
                />
                <Row
                  name="Audit"
                  value={`${sess.audit.status} / worker ${sess.audit.worker_alive ? "alive" : "idle"} / ${sess.audit.findings} findings${sess.audit.error_present ? " / error present" : ""}`}
                />
                <Row
                  name="Final QC"
                  value={`${sess.qc.status} / worker ${sess.qc.worker_alive ? "alive" : "idle"}/${sess.qc.worker_settled ? "settled" : "settling"} / ${sess.qc.event_count} events${sess.qc.error_present ? ` / error ${sess.qc.error_kind ?? "unclassified"}` : ""}`}
                />
              </div>
            ) : (
              <p className="mt-2 text-xs text-ink-faint">Not loaded.</p>
            )}
          </section>

          {sess?.import.present && (
            <section>
              <p className={label}>Last import evidence</p>
              <div className="mt-2">
                <Row
                  name="Source"
                  value={`${sess.import.filename ?? "(unnamed)"} / ${fmtBytes(sess.import.size_bytes)} / SHA-256 ${sess.import.sha256 ?? "unavailable"}`}
                  mono
                />
                <Row
                  name="Package"
                  value={`${sess.import.zip_member_count ?? 0} ZIP members / ${fmtBytes(sess.import.zip_uncompressed_bytes)} expanded / ${sess.import.imported_block_count ?? 0} blocks imported / ${sess.import.skipped_empty_count ?? 0} empty skipped`}
                />
                <Row
                  name="Detection"
                  value={`tracked changes ${sess.import.tracked_changes_detected ? "yes" : "no"} / specification shape ${sess.import.spec_shape_detected ? "yes" : "no"}`}
                />
                <Row
                  name="Warning codes"
                  value={`${fmtCounts(sess.import.warning_code_counts)}${sess.import.warning_evidence_truncated ? ` / ${sess.import.warning_evidence_truncated} evidence records omitted` : ""}`}
                />
              </div>
            </section>
          )}

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
                      {!run.current && run.owner_process_alive && (
                        <span className="flex-none rounded-full bg-amber-500/15 px-2 text-[10px] font-medium text-amber-700 dark:text-amber-300">
                          live process {run.owner_pid ?? "?"}
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
              One .zip with the snapshot above, this launch&apos;s bounded log
              rotations, read-only legacy flat logs, the current trace through
              a flush barrier, and bounded tails from up to three completed
              prior runs. Live sibling processes are identified but never
              copied. An exact inclusion manifest and time-ordered incident
              index state what was captured. It can contain draft text,
              prompts, document titles, file paths, and error context.
              Credential-shaped strings are redacted; the bundle is generated
              locally and saved to your
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
