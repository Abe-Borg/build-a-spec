/**
 * Developer tools' "Resource pressure" row: was an agent starved while it
 * ran? The backend's resource pressure ledger (`backend/resource_pressure.py`)
 * records, for every research area, Final QC lens, grouping call and
 * verifier seat, and every chat turn, what it waited for or ran out of —
 * each retry after a 429/5xx/dropped connection with its backoff, the
 * SDK's own silent retries, a batched request that expired, the search,
 * fetch, continuation, reminder, tool-round and batch ceilings, the
 * context window's reserve, clip and elision, a reply cut at max_tokens, a
 * long wait for a pool worker, and a staggered launch's lead that never
 * started streaming inside the bound. Any of those on an agent means it
 * was starved; a run with a starved agent is starved.
 *
 * This renders that record in plain words: one verdict line for the
 * session, then one line per starved run the ledger kept (newest first),
 * naming the agents and what each one met, then one line on the SDK's own
 * retries. The pressure vocabulary, the engines and the outcomes are
 * pinned against the backend file by `tests/resourcePressure.test.ts`, so
 * one added on either side cannot drop out of the other. Built for any
 * snapshot: an older backend's, or a malformed one, renders "not reported"
 * rather than throwing.
 */
import type {
  PressureAgent,
  PressureRun,
  PressureTotals,
  ResourcePressureSnapshot,
} from "../types";

/** The backend's engines (`ENGINES`), in its order, with their names and
 *  the word for their agents. */
export const ENGINE_LABELS: ReadonlyArray<readonly [string, string, string]> = [
  ["research", "Research", "areas"],
  ["qc", "Final QC", "calls"],
  ["chat", "Chat", "turns"],
];

/** The backend's pressure kinds (`KIND_*`), each in plain words: what the
 *  agent met. Past tense, said of the agent. */
export const PRESSURE_KIND_TEXT: Readonly<Record<string, string>> = {
  rate_limit: "rate limited",
  server_error: "met a provider error or overload",
  connection: "lost its connection",
  sdk_retry: "retried by the SDK",
  batch_expired: "expired in the batch before it ran",
  search_ceiling: "hit the search ceiling",
  fetch_ceiling: "hit the fetch ceiling",
  continuation_ceiling: "hit the continuation ceiling",
  reminder_ceiling: "hit the reminder ceiling",
  tool_rounds_exhausted: "hit the tool-round ceiling",
  batch_round_ceiling: "hit the batch round ceiling",
  batch_wall_clock: "hit the batch wall-clock ceiling",
  context_reserve: "ran out of context window",
  near_window_clip: "clipped to one fetch per request by the context window",
  submission_elided: "dropped raw sources to fit its submission",
  prompt_too_long: "sent a request the window would not take, then a shorter one",
  output_truncated: "cut at max_tokens",
  queued: "waited for a worker",
  warm_wait_timeout: "waited the whole bound for its lead",
};

/** The backend's agent outcomes (`OUTCOME_*`), as a suffix on an agent that
 *  did not complete. */
export const OUTCOME_TEXT: Readonly<Record<string, string>> = {
  running: "still running",
  completed: "completed",
  failed: "failed",
  cancelled: "cancelled",
  interrupted: "ended without reporting",
};

const NOT_REPORTED = "not reported";
const MAX_RUN_LINES = 6;
const MAX_AGENTS_PER_LINE = 4;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function finite(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function count(value: unknown): number {
  const n = finite(value);
  return n === null ? 0 : n;
}

/** "45 s", "2.5 s", "0.4 s": seconds from milliseconds, short. */
function seconds(ms: number): string {
  const s = ms / 1000;
  return `${s >= 10 ? Math.round(s) : Math.round(s * 10) / 10} s`;
}

function engineOf(id: string): readonly [string, string, string] {
  return ENGINE_LABELS.find(([engine]) => engine === id) ?? [id, id, "agents"];
}

/** One agent's phrase: "governing_codes — rate limited ×2 (backoff 15 s),
 *  hit the search ceiling · failed (rate_limit)". */
function agentPhrase(id: string, agent: PressureAgent): string {
  const counts = isRecord(agent.pressure_counts) ? agent.pressure_counts : {};
  const parts: string[] = [];
  const backoff = finite(agent.backoff_s) ?? 0;
  let backoffSaid = false;
  for (const [kind, raw] of Object.entries(counts)) {
    const n = count(raw);
    if (n <= 0) continue;
    let text = PRESSURE_KIND_TEXT[kind] ?? `met ${kind}`;
    if (n > 1) text += ` ×${n}`;
    if (
      !backoffSaid &&
      backoff > 0 &&
      (kind === "rate_limit" || kind === "server_error" || kind === "connection")
    ) {
      text += ` (backoff ${seconds(backoff * 1000)})`;
      backoffSaid = true;
    } else if (kind === "queued" && finite(agent.queued_ms) !== null) {
      text += ` (${seconds(agent.queued_ms as number)})`;
    } else if (kind === "warm_wait_timeout" && finite(agent.warm_wait_ms) !== null) {
      text += ` (${seconds(agent.warm_wait_ms as number)})`;
    } else if (kind === "sdk_retry" && (finite(agent.sdk_sleep_s) ?? 0) > 0) {
      text += ` (${seconds((agent.sdk_sleep_s as number) * 1000)})`;
    }
    parts.push(text);
  }
  const outcome = typeof agent.outcome === "string" ? agent.outcome : "";
  let tail = "";
  if (outcome && outcome !== "completed") {
    const word = OUTCOME_TEXT[outcome] ?? outcome;
    const kind = typeof agent.error_kind === "string" && agent.error_kind ? ` (${agent.error_kind})` : "";
    tail = ` · ${word}${kind}`;
  }
  return `${id} — ${parts.length ? parts.join(", ") : "starved"}${tail}`;
}

/** One starved run's line, agents bounded. */
function runLine(run: PressureRun): string {
  const [, label, unit] = engineOf(String(run.engine));
  const agents = isRecord(run.agents) ? run.agents : {};
  const starved = Object.entries(agents).filter(
    ([, agent]) => isRecord(agent) && agent.starved === true,
  ) as Array<[string, PressureAgent]>;
  const named = starved.slice(0, MAX_AGENTS_PER_LINE).map(([id, agent]) => agentPhrase(id, agent));
  if (starved.length > MAX_AGENTS_PER_LINE) {
    named.push(`+${starved.length - MAX_AGENTS_PER_LINE} more`);
  }
  const running = run.status === "running" ? ", running" : "";
  const head = `${label} ${String(run.label ?? "")}${running}: ${count(run.starved_agents)} of ${count(run.agents_total)} ${unit} starved`;
  return named.length ? `${head} — ${named.join("; ")}` : head;
}

/** The session verdict: "No agent was starved this session — Research 8
 *  areas over 2 runs · Chat 12 turns" or "Starved this session — Research 1
 *  of 8 areas over 2 runs · Chat 2 of 12 turns". The backend's totals count
 *  ENDED runs only (a run's numbers join them when it ends), while
 *  `runs_recorded` counts every run begun — so a run still in progress is
 *  folded in from `runs` here, or the verdict would say "0 calls over 1
 *  run" above a line naming that run's starved call (Codex, PR #282). */
function verdictLine(totals: Record<string, unknown>, runs: PressureRun[]): string | null {
  const phrases: string[] = [];
  let starvedTotal = 0;
  let recorded = 0;
  const known = new Set(ENGINE_LABELS.map(([engine]) => engine));
  const ordered: string[] = [
    ...ENGINE_LABELS.map(([engine]) => engine).filter((engine) => isRecord(totals[engine])),
    ...Object.keys(totals).filter((engine) => !known.has(engine) && isRecord(totals[engine])),
  ];
  for (const engine of ordered) {
    const entry = totals[engine] as Partial<PressureTotals>;
    const runsRecorded = count(entry.runs_recorded);
    if (runsRecorded === 0) continue;
    recorded += runsRecorded;
    const [, label, unit] = engineOf(engine);
    const live = runs.filter((run) => run.engine === engine && run.status === "running");
    const agents =
      count(entry.agents) + live.reduce((sum, run) => sum + count(run.agents_total), 0);
    const starved =
      count(entry.starved_agents) + live.reduce((sum, run) => sum + count(run.starved_agents), 0);
    starvedTotal += starved;
    const body = starved > 0 ? `${starved} of ${agents} ${unit}` : `${agents} ${unit}`;
    const runWord = ` over ${runsRecorded} ${runsRecorded === 1 ? "run" : "runs"}`;
    phrases.push(`${label} ${body}${engine === "chat" ? "" : runWord}`);
  }
  if (recorded === 0) return null;
  const head = starvedTotal > 0 ? "Starved this session" : "No agent was starved this session";
  return `${head} — ${phrases.join(" · ")}`;
}

/** The SDK's own retries, which the app never sees as failures. */
function sdkLine(snapshot: Record<string, unknown>): string | null {
  const perRequest = finite(snapshot.sdk_retries_per_request);
  const observer = snapshot.sdk_retry_observer;
  if (perRequest === null && !isRecord(observer)) return null;
  const parts: string[] = [];
  if (perRequest !== null) {
    parts.push(`up to ${perRequest} per request before a failure reaches the app`);
  }
  if (isRecord(observer)) {
    if (observer.attached !== true) parts.push("observer not attached");
    else if (observer.listening === false) parts.push("observer muted (log level above INFO)");
    else parts.push("observer listening");
    const stray = count(observer.unattributed_retries);
    if (stray > 0) {
      const slept = finite(observer.unattributed_sleep_s) ?? 0;
      parts.push(`${stray} seen outside any agent${slept > 0 ? ` (${seconds(slept * 1000)})` : ""}`);
    }
  }
  return `SDK retries: ${parts.join(" · ")}`;
}

/** The row's lines, never empty and never throwing. */
export function resourcePressureLines(
  snapshot: ResourcePressureSnapshot | null | undefined,
): string[] {
  if (!isRecord(snapshot)) return [NOT_REPORTED];
  const totals = isRecord(snapshot.totals) ? snapshot.totals : null;
  const runs = Array.isArray(snapshot.runs) ? snapshot.runs.filter(isRecord) : null;
  if (totals === null || runs === null) return [NOT_REPORTED];
  const lines: string[] = [];
  const typedRuns = runs as unknown as PressureRun[];
  const verdict = verdictLine(totals, typedRuns);
  lines.push(verdict ?? "nothing recorded yet");
  const starvedRuns = typedRuns.filter((run) => run.starved === true);
  for (const run of starvedRuns.slice(0, MAX_RUN_LINES)) lines.push(runLine(run));
  if (starvedRuns.length > MAX_RUN_LINES) {
    lines.push(`+${starvedRuns.length - MAX_RUN_LINES} more starved runs in the snapshot JSON`);
  }
  const sdk = sdkLine(snapshot);
  if (sdk) lines.push(sdk);
  return lines;
}
