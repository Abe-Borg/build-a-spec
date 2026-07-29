/**
 * Research's live-state layer: the pure helpers the SSE follower and the
 * authoritative status refetch share.
 *
 * Deliberately shaped like `qcLive.ts`, its older sibling — same
 * merge-by-seq / reconcile-with-generation / is-active vocabulary — because
 * the two followers now run the same loop and a reader who has understood
 * one should not have to re-derive the other. What differs is identity:
 * Final QC has a `run_id`, research has a 1-based `round`, and a round
 * number is NOT unique on its own (a stopped round is never adopted into
 * the profile, so `ResearchRunner.start` renumbers the next one to the same
 * value). Where that matters, the run's lifecycle state carries the rest of
 * the identity — see `mergeResearchEvent`.
 *
 * Nothing here touches React or the network: every decision the follower
 * makes between renders is a function of (previous snapshot, new input).
 */
import type {
  ResearchEvent,
  ResearchSnapshot,
  ResearchStreamEndStatus,
} from "../types";

/** The research SSE events whose arrival warrants a full authoritative
 *  snapshot refetch (status flips, profile adoption, round metadata). The
 *  chatty live events between them — per-agent activity, searches, fetches,
 *  retries — merge locally instead: refetching the whole snapshot (full
 *  event log + full profile) per frame was O(frames × payload). */
export const RESEARCH_MILESTONE_TYPES: ReadonlySet<string> = new Set([
  "research_started",
  "dimension_complete",
  "dimension_failed",
  "research_complete",
  "research_failed",
]);

/** How a `stream_end` sentinel should be read. `terminal` and `superseded`
 *  both mean stop following; only `interrupted` reconnects. */
export type ResearchStreamOutcome = "terminal" | "superseded" | "interrupted";

/** Classify the sentinel with the compiler's help rather than by matching
 *  strings at the call site. The `never` arm is the point: adding a status
 *  to `ResearchStreamEndStatus` without deciding what the follower should
 *  do about it becomes a type error instead of a silent reconnect. */
export function classifyResearchStreamEnd(
  status: ResearchStreamEndStatus | undefined,
): ResearchStreamOutcome {
  if (status === undefined) return "interrupted";
  switch (status) {
    case "complete":
    case "failed":
      return "terminal";
    case "superseded":
      return "superseded";
    case "running":
    case "idle":
      // The server closed the stream while the run was still going (its
      // follow generator has a wall-clock ceiling). Reconnect.
      return "interrupted";
    default: {
      const exhaustive: never = status;
      return exhaustive;
    }
  }
}

function isStreamEnd(event: ResearchEvent): boolean {
  return event.type === "stream_end";
}

/** Highest sequence number present, or -1 for an empty/sentinel-only log.
 *  The watermark, not the length: a log can only be compared with another
 *  by how far into the server's append-only array it reaches. */
export function maxResearchEventSeq(events: readonly ResearchEvent[]): number {
  let maximum = -1;
  for (const event of events) {
    if (typeof event.seq === "number") maximum = Math.max(maximum, event.seq);
  }
  return maximum;
}

/** The 1-based round this log belongs to, or 0 when it says nothing. Every
 *  event in a round carries the same number, so the first one that has it
 *  answers for the log. */
export function researchEventsRound(events: readonly ResearchEvent[]): number {
  for (const event of events) {
    if (typeof event.round === "number" && event.round > 0) return event.round;
  }
  return 0;
}

export function researchSnapshotRound(
  snapshot: ResearchSnapshot | null | undefined,
): number {
  return researchEventsRound(snapshot?.events ?? []);
}

/** An active run is one the follower must keep following. */
export function isResearchActiveSnapshot(
  snapshot: ResearchSnapshot | null | undefined,
): boolean {
  return snapshot?.status === "running";
}

function normalizedResearchEvents(
  events: readonly ResearchEvent[],
): ResearchEvent[] {
  const numbered = new Map<number, ResearchEvent>();
  const unnumbered: ResearchEvent[] = [];
  for (const event of events) {
    if (isStreamEnd(event)) continue;
    if (typeof event.seq === "number") numbered.set(event.seq, event);
    else unnumbered.push(event);
  }
  return [
    ...[...numbered.entries()]
      .sort(([left], [right]) => left - right)
      .map(([, event]) => event),
    ...unnumbered,
  ];
}

/** Does this `research_started` frame begin a genuinely new run, or is it
 *  the replay of the one already on screen?
 *
 *  It matters now that the follower reconnects: every reconnect replays the
 *  round's log from seq 0, so `research_started` arrives again for the run
 *  in progress, and resetting on it unconditionally would empty the board
 *  and rebuild it from the replay on every transport hiccup.
 *
 *  A different round is obviously new. So is a `research_started` that
 *  arrives while the local snapshot is NOT running — a terminal snapshot
 *  means the previous round ended, and the next one reuses its number
 *  whenever the previous was stopped or failed (the runner numbers from
 *  `profile_result.round_count`, which a discarded round never advances).
 *  That second clause is what keeps "stop round 2, start round 2 again"
 *  from merging two logs into one. */
function startsNewResearchRun(
  base: ResearchSnapshot,
  event: ResearchEvent,
): boolean {
  if (base.events.length === 0) return true;
  if (base.status !== "running") return true;
  const round = researchEventsRound(base.events);
  const incoming = typeof event.round === "number" ? event.round : 0;
  return round !== 0 && incoming !== 0 && round !== incoming;
}

/** Merge one streamed research event into the local snapshot's event log.
 *  Never touches status/error/profile — those stay snapshot-owned (the
 *  authoritative refetch is generation- and epoch-guarded). A genuinely new
 *  `research_started` restarts the local log (the server clears it per
 *  round); replay overlap after a reconnect dedupes by seq. */
export function mergeResearchEvent(
  previous: ResearchSnapshot | null,
  event: ResearchEvent,
): ResearchSnapshot {
  const base: ResearchSnapshot =
    previous ?? { status: "running", error: "", events: [] };
  if (isStreamEnd(event)) return base;
  if (event.type === "research_started" && startsNewResearchRun(base, event)) {
    return { ...base, events: [event] };
  }
  const events = base.events;
  const last = events[events.length - 1];
  if (!last || event.seq > last.seq) {
    return { ...base, events: [...events, event] };
  }
  return { ...base, events: normalizedResearchEvents([...events, event]) };
}

/** A refresh's identity. `requestGeneration` is read when the fetch is
 *  issued; a response whose generation no longer matches belongs to a run
 *  the user has already moved past and is dropped without side effects. */
export interface ResearchRefreshGeneration {
  requestGeneration: number;
  currentGeneration: number;
}

export interface ResearchSnapshotReconciliation {
  snapshot: ResearchSnapshot;
  /** False means the response was provably older or superseded, and NO
   *  side effect (auth modal, reconnect decision) may be driven from it. */
  accepted: boolean;
}

function statusRank(status: ResearchSnapshot["status"]): number {
  if (status === "idle") return 0;
  if (status === "running") return 1;
  return 2;
}

/**
 * Adopt a fetched snapshot without letting it march the live board backward.
 *
 * The refetch is asynchronous and is kicked off by a milestone frame — worst
 * case `research_started`, the exact moment all four workers burst their
 * first events — so its response can capture an EARLIER server log yet
 * resolve AFTER later SSE frames merged locally.
 *
 * Three outcomes:
 *
 * - **Stale generation** → rejected outright. The user started a new round;
 *   nothing this response says is about the run on screen.
 * - **Different round** (or either side round-less) → adopted wholesale. It
 *   is a genuinely newer world, not a late copy of this one.
 * - **Same round** → compare WATERMARKS, not lengths. A fetch that reaches
 *   less far into the server's append-only array is wholly stale — its
 *   status, error and profile included, which is the bug this replaces: the
 *   old code kept the longer local log but still took the fetch's status,
 *   so a pre-terminal response could report `running` over a finished run
 *   and drop the profile it had just adopted. At an EQUAL watermark the
 *   response is a peer, not a predecessor: everything is taken from it
 *   except a lifecycle state it would regress (a terminal status must not
 *   become `running` again, and an adopted profile must not vanish).
 *
 * A RESTARTED round is deliberately not a case here, and cannot be made
 * one: "stop round 2, start round 2 again" and "a late fetch from the
 * middle of round 2" are the same triple — same round, shorter fetched
 * log — and a rule that adopted the first would re-open the second, which
 * is the bug this function exists to fix. (Tried it; the tests said no.)
 * Two mechanisms outside this function keep the restart honest, and both
 * run BEFORE any refresh for the new round can resolve: `onStartResearch`
 * bumps the refresh generation, which rejects everything already in
 * flight, and `mergeResearchEvent` resets the log on the new round's
 * `research_started` frame, which always precedes the milestone refetch
 * that frame triggers. By the time a fetch for the new round lands, the
 * local log is the new round's.
 */
export function reconcileResearchSnapshotUpdate(
  previous: ResearchSnapshot | null,
  fetched: ResearchSnapshot,
  generation?: ResearchRefreshGeneration,
): ResearchSnapshotReconciliation {
  if (
    generation &&
    generation.requestGeneration !== generation.currentGeneration
  ) {
    return { snapshot: previous ?? fetched, accepted: false };
  }
  if (!previous) return { snapshot: fetched, accepted: true };

  const previousEvents = previous.events ?? [];
  const fetchedEvents = fetched.events ?? [];
  const previousRound = researchEventsRound(previousEvents);
  const fetchedRound = researchEventsRound(fetchedEvents);
  if (!previousRound || !fetchedRound || previousRound !== fetchedRound) {
    return { snapshot: fetched, accepted: true };
  }

  const previousMax = maxResearchEventSeq(previousEvents);
  const fetchedMax = maxResearchEventSeq(fetchedEvents);
  if (fetchedMax < previousMax) {
    return { snapshot: previous, accepted: false };
  }

  const events = normalizedResearchEvents([...fetchedEvents, ...previousEvents]);
  const keepPreviousStatus =
    fetchedMax === previousMax &&
    statusRank(previous.status) > statusRank(fetched.status);
  // A profile is only ever ADOPTED, never withdrawn, so a peer response
  // that lacks one is answering from before the adoption landed.
  const keepPreviousProfile =
    fetchedMax === previousMax && !fetched.profile && Boolean(previous.profile);
  return {
    accepted: true,
    snapshot: {
      ...fetched,
      status: keepPreviousStatus ? previous.status : fetched.status,
      error: keepPreviousStatus ? previous.error : fetched.error,
      error_kind: keepPreviousStatus ? previous.error_kind : fetched.error_kind,
      profile: keepPreviousProfile ? previous.profile : fetched.profile,
      events,
    },
  };
}

export function reconcileResearchSnapshot(
  previous: ResearchSnapshot | null,
  fetched: ResearchSnapshot,
): ResearchSnapshot {
  return reconcileResearchSnapshotUpdate(previous, fetched).snapshot;
}
