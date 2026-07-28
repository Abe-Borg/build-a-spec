/**
 * Shared vocabulary and pure folds for the live research-agent surfaces:
 * the per-agent board in ResearchDrawer and the per-agent activity modal
 * (AgentActivityModal). Both fold the same merged SSE event log, so the
 * card a user clicks and the modal that opens can never disagree — the
 * modal's summary state IS the board fold's, and its timeline is the full
 * history the compact card truncates. Pure data, no React, no I/O; pinned
 * by tests/researchAgents.test.ts.
 */
import type { ResearchEvent } from "../types";

/** What each research agent shows while it works (StatusStrip vocabulary). */
export const ACTIVITY_LABELS: Record<string, string> = {
  thinking: "Thinking…",
  searching: "Searching the web…",
  fetching: "Reading a source…",
  writing: "Writing up findings…",
};

export const RETRY_REASONS: Record<string, string> = {
  rate_limit: "rate limited",
  server_error: "provider error",
  connection: "connection dropped",
};

/** Most-recent live queries/URLs shown per agent card. */
export const RECENT_CAP = 3;

export interface DimLive {
  id: string;
  title: string;
  state: "queued" | "running" | "done" | "failed";
  /** Last dimension_activity kind; "" before the first block streams. */
  activity: string;
  /** Live tick counts (events seen), vs the billed totals on completion. */
  searches: number;
  fetches: number;
  /** Newest-first live queries/URLs, capped at RECENT_CAP. */
  recent: { kind: "search" | "fetch"; text: string; seq: number }[];
  retry: { attempt: number; max: number; reason: string } | null;
  itemCount: number;
  groundedCount: number;
  billedSearches: number;
  billedFetches: number;
  error: string;
}

export interface ResearchBoard {
  dims: DimLive[];
  doneCount: number;
  total: number;
  totalSearches: number;
  totalFetches: number;
}

/** Legacy fallback so a card never reads as raw snake_case (mirrors the
 *  report modal's dimensionLabel). */
export function labelFromId(id: string): string {
  return id
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join(" ");
}

/** Show a fetched URL as host/path; the full URL rides the title. */
export function trimUrl(url: string): string {
  try {
    const u = new URL(url);
    const host = u.hostname.replace(/^www\./, "");
    const path = u.pathname.replace(/\/$/, "");
    return path && path !== "/" ? `${host}${path}` : host;
  } catch {
    return url;
  }
}

/** Fold the run's event log into per-agent live state. Events interleave
 *  freely across dimensions (four workers), but each carries its
 *  dimension_id and a dimension's terminal event follows its live ones, so
 *  a single forward pass is exact. The roster seeds from research_started
 *  (ids + titles), so all cards exist from the first frame. */
export function foldResearchBoard(events: ResearchEvent[]): ResearchBoard {
  const dims = new Map<string, DimLive>();
  const ensure = (id: string, title?: string): DimLive => {
    let dim = dims.get(id);
    if (!dim) {
      dim = {
        id,
        title: "",
        state: "queued",
        activity: "",
        searches: 0,
        fetches: 0,
        recent: [],
        retry: null,
        itemCount: 0,
        groundedCount: 0,
        billedSearches: 0,
        billedFetches: 0,
        error: "",
      };
      dims.set(id, dim);
    }
    if (title && !dim.title) dim.title = title;
    return dim;
  };
  for (const e of events) {
    if (e.type === "research_started") {
      for (const id of e.dimensions ?? []) ensure(id, e.dimension_titles?.[id]);
      continue;
    }
    if (!e.dimension_id) continue;
    switch (e.type) {
      case "dimension_started": {
        const dim = ensure(e.dimension_id, e.title);
        dim.state = "running";
        break;
      }
      case "dimension_activity": {
        const dim = ensure(e.dimension_id);
        dim.activity = e.kind ?? "";
        dim.retry = null;
        break;
      }
      case "dimension_search": {
        const dim = ensure(e.dimension_id);
        dim.searches += 1;
        dim.activity = "searching";
        dim.retry = null;
        dim.recent = [
          { kind: "search" as const, text: e.query ?? "", seq: e.seq },
          ...dim.recent,
        ].slice(0, RECENT_CAP);
        break;
      }
      case "dimension_fetch": {
        const dim = ensure(e.dimension_id);
        dim.fetches += 1;
        dim.activity = "fetching";
        dim.retry = null;
        dim.recent = [
          { kind: "fetch" as const, text: e.url ?? "", seq: e.seq },
          ...dim.recent,
        ].slice(0, RECENT_CAP);
        break;
      }
      case "dimension_retry": {
        const dim = ensure(e.dimension_id);
        dim.retry = {
          attempt: e.attempt ?? 0,
          max: e.max_attempts ?? 0,
          reason: e.reason ?? "",
        };
        dim.activity = "";
        break;
      }
      case "dimension_complete":
      case "dimension_failed": {
        const dim = ensure(e.dimension_id, e.title);
        dim.state = e.type === "dimension_complete" ? "done" : "failed";
        dim.itemCount = e.item_count ?? 0;
        dim.groundedCount = e.grounded_count ?? 0;
        dim.billedSearches = e.web_search_requests ?? 0;
        dim.billedFetches = e.web_fetch_requests ?? 0;
        dim.error = e.error ?? "";
        dim.retry = null;
        break;
      }
    }
  }
  const list = [...dims.values()];
  return {
    dims: list,
    doneCount: list.filter((d) => d.state === "done" || d.state === "failed")
      .length,
    total: list.length,
    totalSearches: list.reduce((n, d) => n + d.searches, 0),
    totalFetches: list.reduce((n, d) => n + d.fetches, 0),
  };
}

/** One row of an agent's full activity feed (the modal's timeline). */
export type AgentTimelineEntry = { seq: number; ts: string } & (
  | { kind: "started"; maxSearches: number; maxFetches: number }
  | { kind: "activity"; activity: string }
  | { kind: "search"; query: string }
  | { kind: "fetch"; url: string }
  | {
      kind: "retry";
      attempt: number;
      max: number;
      reason: string;
      backoffS: number;
    }
  | {
      kind: "done";
      itemCount: number;
      groundedCount: number;
      billedSearches: number;
      billedFetches: number;
    }
  | { kind: "failed"; error: string }
);

export interface AgentDetail {
  /** The board card's folded state for this dimension; null until the
   *  roster (or any of the dimension's own events) names the id. */
  dim: DimLive | null;
  /** 1-based round of the current log; 0 before any event carries one. */
  round: number;
  /** Web-tool budgets from dimension_started; 0 = not yet announced. */
  maxSearches: number;
  maxFetches: number;
  /** Chronological — the merged log is already seq-ascending. */
  timeline: AgentTimelineEntry[];
}

/** Fold ONE dimension's full history out of the run's event log —
 *  everything the compact board card truncates (the complete query and URL
 *  history, every retry with its backoff, the web-tool budgets). `dim`
 *  deliberately reuses foldResearchBoard so the modal header and the card
 *  the user clicked share one state machine, never a re-derivation. */
export function foldAgentDetail(
  events: ResearchEvent[],
  dimensionId: string,
): AgentDetail {
  const detail: AgentDetail = {
    dim: null,
    round: 0,
    maxSearches: 0,
    maxFetches: 0,
    timeline: [],
  };
  if (!dimensionId) return detail;
  detail.dim =
    foldResearchBoard(events).dims.find((d) => d.id === dimensionId) ?? null;
  for (const e of events) {
    if (e.round) detail.round = e.round;
    if (e.dimension_id !== dimensionId) continue;
    const base = { seq: e.seq, ts: e.ts };
    switch (e.type) {
      case "dimension_started":
        detail.maxSearches = e.max_searches ?? 0;
        detail.maxFetches = e.max_fetches ?? 0;
        detail.timeline.push({
          ...base,
          kind: "started",
          maxSearches: e.max_searches ?? 0,
          maxFetches: e.max_fetches ?? 0,
        });
        break;
      case "dimension_activity":
        detail.timeline.push({
          ...base,
          kind: "activity",
          activity: e.kind ?? "",
        });
        break;
      case "dimension_search":
        detail.timeline.push({ ...base, kind: "search", query: e.query ?? "" });
        break;
      case "dimension_fetch":
        detail.timeline.push({ ...base, kind: "fetch", url: e.url ?? "" });
        break;
      case "dimension_retry":
        detail.timeline.push({
          ...base,
          kind: "retry",
          attempt: e.attempt ?? 0,
          max: e.max_attempts ?? 0,
          reason: e.reason ?? "",
          backoffS: e.backoff_s ?? 0,
        });
        break;
      case "dimension_complete":
        detail.timeline.push({
          ...base,
          kind: "done",
          itemCount: e.item_count ?? 0,
          groundedCount: e.grounded_count ?? 0,
          billedSearches: e.web_search_requests ?? 0,
          billedFetches: e.web_fetch_requests ?? 0,
        });
        break;
      case "dimension_failed":
        detail.timeline.push({ ...base, kind: "failed", error: e.error ?? "" });
        break;
    }
  }
  return detail;
}
