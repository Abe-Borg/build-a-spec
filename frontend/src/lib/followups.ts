/**
 * Pure helpers for the "Waiting on you" panel.
 *
 * Extracted rather than inlined for the `eventSeqIndex.ts` reason: the
 * check-off transition is the piece whose regression nothing else in the
 * suite would notice — a silent failure to animate reads as "the app didn't
 * do anything", which is exactly the complaint this feature answers.
 */
import type { FollowUp } from "../types";

/**
 * The ids that just moved from open to resolved.
 *
 * Driven off a diff of two snapshots rather than the SSE event, so it is
 * true however the change arrived — the model resolving one mid-turn, the
 * user ticking one off, or a doc-payload refresh landing after either.
 * A first render (no previous list) reports nothing: everything would look
 * newly resolved, and animating a restored project's whole history on load
 * is noise, not feedback.
 */
export function justResolvedIds(
  previous: readonly FollowUp[] | null,
  next: readonly FollowUp[],
): Set<string> {
  const moved = new Set<string>();
  if (!previous || previous.length === 0) return moved;
  const before = new Map(previous.map((item) => [item.fid, item.status]));
  for (const item of next) {
    if (item.status === "resolved" && before.get(item.fid) === "open") {
      moved.add(item.fid);
    }
  }
  return moved;
}

/** Waiting items, blocking first, then oldest — the order the model raises them in. */
export function waitingFollowups(items: readonly FollowUp[]): FollowUp[] {
  return items
    .filter((item) => item.status === "open")
    .sort((a, b) => {
      if (a.blocking !== b.blocking) return a.blocking ? -1 : 1;
      if (a.raised_turn !== b.raised_turn) return a.raised_turn - b.raised_turn;
      return seqOf(a) - seqOf(b);
    });
}

/** Settled items, most recently checked off first. */
export function settledFollowups(items: readonly FollowUp[]): FollowUp[] {
  return items
    .filter((item) => item.status === "resolved")
    .sort((a, b) => seqOf(b) - seqOf(a));
}

export function followupCounts(items: readonly FollowUp[]): {
  waiting: number;
  done: number;
} {
  let waiting = 0;
  let done = 0;
  for (const item of items) {
    if (item.status === "open") waiting += 1;
    else done += 1;
  }
  return { waiting, done };
}

/** Age in replies, as the panel says it. */
export function followupAge(item: FollowUp, currentTurn: number): string {
  const replies = currentTurn - item.raised_turn;
  if (replies <= 0) return "just now";
  if (replies === 1) return "1 reply ago";
  return `${replies} replies ago`;
}

function seqOf(item: FollowUp): number {
  const tail = item.fid.split("-").pop() ?? "";
  const value = Number.parseInt(tail, 10);
  return Number.isFinite(value) ? value : 0;
}
