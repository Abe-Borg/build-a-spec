/**
 * The condensed-conversation divider's pure rules (compaction plan Phase 3).
 *
 * The transcript on screen is never condensed — only the model's view of it
 * is. The divider marks where that view was cut: everything above it is
 * represented by the summary (which the user can read), everything below is
 * still sent word for word.
 */
import type {
  ChatMessage,
  CompactionFacts,
  CompactionInfo,
  CompactionStatus,
} from "../types";

/**
 * Where the divider goes: before the user message that starts turn
 * `coversTurns + 1`, or -1 when that turn is not on screen.
 *
 * Turns are counted the way the server counts them — one per message the
 * user sent that reached the saved history. A note (a research/QC marker)
 * is not a turn, and neither is a message whose reply failed: a failed turn
 * rolls back server-side, so it never became part of the history the
 * summary numbers.
 */
export function condensedDividerIndex(
  messages: ChatMessage[],
  coversTurns: number,
): number {
  if (!Number.isFinite(coversTurns) || coversTurns < 1) return -1;
  let turns = 0;
  for (let i = 0; i < messages.length; i++) {
    const message = messages[i];
    if (message.role !== "user" || message.note) continue;
    const reply = messages[i + 1];
    if (reply && reply.role === "assistant" && reply.error) continue;
    turns += 1;
    if (turns === coversTurns + 1) return i;
  }
  return -1;
}

/** "turn 1" or "turns 1–40". */
export function condensedTurnsLabel(coversTurns: number): string {
  return coversTurns <= 1 ? "turn 1" : `turns 1–${coversTurns}`;
}

/** "600k" / "12.3k" / "900" — the same compact form the usage table uses. */
export function compactTokens(tokens: number): string {
  if (!Number.isFinite(tokens) || tokens <= 0) return "0";
  if (tokens >= 100_000) return `${Math.round(tokens / 1000)}k`;
  if (tokens >= 1000) return `${(tokens / 1000).toFixed(1)}k`;
  return String(Math.round(tokens));
}

/**
 * Developer tools' one-line account of condensing: what is condensed, how
 * routine condensing is set, and what the background runner last did.
 */
export function describeCompaction(facts: CompactionFacts): string {
  const parts: string[] = [];
  if (facts.active) {
    const when = facts.created_at.slice(0, 10);
    parts.push(
      `${condensedTurnsLabel(facts.covers_turns)} condensed` +
        ` (${facts.trigger || "unknown"}${when ? `, ${when}` : ""})`,
    );
    parts.push(
      `${compactTokens(facts.tokens_before)} → ${compactTokens(facts.tokens_after)} tokens`,
    );
    parts.push(`summary ${facts.summary_chars.toLocaleString()} chars`);
  } else {
    parts.push("not condensed");
  }
  parts.push(
    facts.enabled
      ? `routine condensing at ${compactTokens(facts.threshold_tokens)}, keeping ${facts.keep_turns} turn${facts.keep_turns === 1 ? "" : "s"}`
      : "routine condensing off (backstop only)",
  );
  const runner = facts.runner;
  if (runner && runner.status === "running") {
    parts.push("a summary is being written");
  } else if (runner && runner.status === "failed") {
    const retry =
      runner.retry_after_turns > 0
        ? `, next try from turn ${runner.retry_after_turns}`
        : "";
    parts.push(`last summary failed (${runner.error_kind || "error"})${retry}`);
  }
  if (facts.tokens_per_char !== null && facts.tokens_per_char > 0) {
    parts.push(`${facts.tokens_per_char.toFixed(3)} tokens/char`);
  }
  return parts.join(" · ");
}

/** The first wait before asking whether a background summary has landed,
 *  and the longest wait between asks. A summary takes a minute or a few; the
 *  status route is two small fields, so a few asks a minute cost nothing. */
export const COMPACTION_POLL_FIRST_MS = 3_000;
export const COMPACTION_POLL_MAX_MS = 20_000;

/** The wait after `previous`: half as long again, up to the cap. */
export function nextCompactionPollDelay(previous: number): number {
  return Math.min(Math.round(previous * 1.5), COMPACTION_POLL_MAX_MS);
}

export interface CompactionFollowOptions {
  /** One ask of the status route. */
  fetchStatus: () => Promise<CompactionStatus>;
  /** Called once, with the record as it stands, when the summary has
   *  settled — landed, failed, or been dropped. */
  onSettled: (compaction: CompactionInfo | null) => void;
  /** False once the workspace this follow was started for has moved on: an
   *  answer that arrives after that is dropped, and the asking stops. */
  isCurrent: () => boolean;
  /** The timer, injectable for tests. */
  schedule?: (run: () => void, ms: number) => unknown;
  cancel?: (handle: unknown) => void;
}

/**
 * Ask until a background summary settles, then hand its record over once.
 *
 * A routine summary is written while the user reads the reply and usually
 * lands after that turn's stream has closed, so no stream event can carry
 * it. Only a settled answer is applied: an answer that still says "pending"
 * changes nothing on screen, so an ask that raced a newer refresh cannot
 * put an older record back. A failed ask keeps what is on screen and asks
 * again. Returns the stop function — an effect's cleanup — after which
 * nothing is asked or applied.
 */
export function followCompactionStatus(
  options: CompactionFollowOptions,
): () => void {
  const schedule =
    options.schedule ?? ((run: () => void, ms: number) => setTimeout(run, ms));
  const cancel =
    options.cancel ??
    ((handle: unknown) =>
      clearTimeout(handle as ReturnType<typeof setTimeout>));
  let stopped = false;
  let delay = COMPACTION_POLL_FIRST_MS;
  let handle: unknown = undefined;

  const ask = async (): Promise<void> => {
    handle = undefined;
    if (stopped) return;
    let status: CompactionStatus | null = null;
    try {
      status = await options.fetchStatus();
    } catch {
      status = null;
    }
    if (stopped) return;
    if (!options.isCurrent()) {
      stopped = true;
      return;
    }
    if (status && !status.pending) {
      stopped = true;
      options.onSettled(status.compaction);
      return;
    }
    delay = nextCompactionPollDelay(delay);
    handle = schedule(() => void ask(), delay);
  };

  handle = schedule(() => void ask(), delay);
  return () => {
    stopped = true;
    if (handle !== undefined) cancel(handle);
    handle = undefined;
  };
}
