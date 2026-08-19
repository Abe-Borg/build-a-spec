/**
 * The completion-debrief queue: pure state behind the auto-sent chat turn
 * that fires when a research round or a Final QC run completes.
 *
 * The follower loops observe a live terminal frame and REMEMBER a debrief;
 * a flush effect later asks whether one may fire. Splitting remember from
 * take is what makes the gates testable without React: dedupe against SSE
 * replays (a reconnect replays the whole run log, terminal frame included),
 * latest-wins per kind, stale-epoch entries dropped, and the fire-time
 * gates — the tutorial and protected workspaces never auto-spend, the
 * operator switch wins, and a streaming turn or file load merely HOLDS the
 * entry for the next flush rather than dropping it.
 *
 * Everything is immutable-in/immutable-out; an unchanged answer returns the
 * SAME state object so React effects can bail on identity.
 */

export type DebriefKind = "research" | "qc";

export interface PendingDebrief {
  kind: DebriefKind;
  /** Dedupe token — research `round-N`, QC the run id. An SSE reconnect
   *  replays the run's terminal frame; a token that already queued or fired
   *  must not queue a second debrief. */
  token: string;
  /** Workspace epoch when the completion was observed. A reset, project
   *  load, or tutorial swap bumps the epoch, and a debrief about the
   *  previous session must die with it. */
  epoch: number;
}

export interface DebriefQueueState {
  /** At most one entry per kind; a newer completion replaces the older. */
  pending: PendingDebrief[];
  /** `kind:token` keys already queued-and-fired (bounded, newest last). */
  fired: string[];
}

export interface DebriefGates {
  epoch: number;
  busy: boolean;
  /** A manual document edit is awaiting /api/doc/edit — send() declines
   *  while it does, so a debrief popped now would be lost. Holds, exactly
   *  like busy. */
  manualEditBusy: boolean;
  fileLoading: boolean;
  tourActive: boolean;
  protectedWorkspace: boolean;
  /** health.auto_debrief !== false — unknown health reads as NOT allowed
   *  (the caller passes false until a full health payload arrives). */
  autoDebrief: boolean;
}

const FIRED_CAP = 16;

export function emptyDebriefQueue(): DebriefQueueState {
  return { pending: [], fired: [] };
}

function key(entry: { kind: DebriefKind; token: string }): string {
  return `${entry.kind}:${entry.token}`;
}

/** Remember a live completion. Replays and repeats return the SAME state. */
export function rememberDebrief(
  state: DebriefQueueState,
  entry: PendingDebrief,
): DebriefQueueState {
  if (state.fired.includes(key(entry))) return state;
  const existing = state.pending.find((p) => p.kind === entry.kind);
  if (
    existing &&
    existing.token === entry.token &&
    existing.epoch === entry.epoch
  ) {
    return state;
  }
  return {
    pending: [...state.pending.filter((p) => p.kind !== entry.kind), entry],
    fired: state.fired,
  };
}

export interface DebriefTake {
  state: DebriefQueueState;
  /** The debrief to fire now, or null (held or nothing eligible). */
  next: PendingDebrief | null;
}

/**
 * Decide what (if anything) fires now. Drops stale-epoch entries always;
 * drops everything when the switch is off or the session is in a tutorial /
 * protected workspace (those completions must land silently in the panels);
 * HOLDS everything while a turn streams or a file loads; otherwise pops
 * research before qc (the runners are mutually exclusive, so both pending
 * at once only happens behind a held turn) and marks it fired.
 */
export function takeNextDebrief(
  state: DebriefQueueState,
  gates: DebriefGates,
): DebriefTake {
  let pending = state.pending;
  const fresh = pending.filter((p) => p.epoch === gates.epoch);
  if (fresh.length !== pending.length) pending = fresh;
  if (!gates.autoDebrief || gates.tourActive || gates.protectedWorkspace) {
    if (pending.length === 0 && pending === state.pending) {
      return { state, next: null };
    }
    return { state: { pending: [], fired: state.fired }, next: null };
  }
  if (pending.length === 0) {
    return {
      state: pending === state.pending ? state : { ...state, pending },
      next: null,
    };
  }
  if (gates.busy || gates.manualEditBusy || gates.fileLoading) {
    return {
      state: pending === state.pending ? state : { ...state, pending },
      next: null,
    };
  }
  const next =
    pending.find((p) => p.kind === "research") ??
    pending.find((p) => p.kind === "qc")!;
  return {
    state: {
      pending: pending.filter((p) => p !== next),
      fired: [...state.fired, key(next)].slice(-FIRED_CAP),
    },
    next,
  };
}

/**
 * Put a taken entry back — the fire-time guards raced the render-time gates
 * (send() would have declined and silently eaten the debrief). Un-fires the
 * token and restores the pending slot; the blocking condition's own falling
 * edge (busy / manual edit / file load are all flush-effect dependencies)
 * retries it, so requeueing never spins.
 */
export function requeueDebrief(
  state: DebriefQueueState,
  entry: PendingDebrief,
): DebriefQueueState {
  return {
    pending: [...state.pending.filter((p) => p.kind !== entry.kind), entry],
    fired: state.fired.filter((k) => k !== key(entry)),
  };
}
