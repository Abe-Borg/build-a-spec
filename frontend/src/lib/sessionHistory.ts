/**
 * Developer tools' "Session history" row and the diagnostics bundle link.
 *
 * A section is worked on across many app launches; each launch keeps its own
 * log folder and trace run. The backend tags every launch that opened or
 * saved the section with the section's id, keeps those launches past the
 * age and count limits while the section is in use, and the bundle collects
 * every one still on disk (`backend/session_history.py`). This line says how
 * much of that history exists, in plain words.
 *
 * Built for any snapshot: an older backend's (no block) renders nothing, so
 * the modal can call it unconditionally.
 */
import type { SessionHistoryFacts } from "../types";

export const DIAGNOSTICS_BUNDLE_URL = "/api/diagnostics/bundle";

/** The bundle link, with or without the earlier launches' prompt text. */
export function diagnosticsBundleUrl(includePrompts: boolean): string {
  return includePrompts
    ? `${DIAGNOSTICS_BUNDLE_URL}?include_prompts=true`
    : DIAGNOSTICS_BUNDLE_URL;
}

function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

function day(ts: number | null | undefined): string {
  return typeof ts === "number"
    ? new Date(ts * 1000).toLocaleDateString()
    : "";
}

function megabytes(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} B`;
}

/** The row's lines: what the file remembers, then what is on disk. */
export function sessionHistoryLines(
  facts: SessionHistoryFacts | null | undefined,
): string[] {
  if (!facts || typeof facts.visits_recorded !== "number") return [];
  const since = day(facts.created_at ?? facts.first_visit_at);
  const remembered =
    `${plural(facts.visits_recorded, "visit")} recorded` +
    (facts.visits_dropped ? ` (+${facts.visits_dropped} older not kept)` : "") +
    (since ? ` since ${since}` : "") +
    ` · ${plural(facts.turns_total, "turn")}` +
    ` · $${facts.estimated_cost_usd_total.toFixed(3)} est.`;
  const earlier =
    facts.earlier_trace_runs_on_disk + facts.earlier_log_runs_on_disk;
  const onDisk = earlier
    ? `earlier launches on disk: ${plural(facts.earlier_trace_runs_on_disk, "trace run")}, ` +
      `${plural(facts.earlier_log_runs_on_disk, "log folder")} (${megabytes(facts.earlier_runs_bytes_on_disk)})`
    : "no earlier launches on disk";
  const kept = facts.history_days
    ? `kept while used within ${facts.history_days} days`
    : "retention protection off";
  const tagged = facts.current_launch_tagged
    ? "this launch tagged"
    : "this launch not tagged until you open or save";
  return [remembered, `${onDisk} · ${kept} · ${tagged}`];
}
