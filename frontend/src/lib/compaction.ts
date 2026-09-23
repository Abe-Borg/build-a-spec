/**
 * The condensed-conversation divider's pure rules (compaction plan Phase 3).
 *
 * The transcript on screen is never condensed — only the model's view of it
 * is. The divider marks where that view was cut: everything above it is
 * represented by the summary (which the user can read), everything below is
 * still sent word for word.
 */
import type { ChatMessage, CompactionFacts } from "../types";

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
