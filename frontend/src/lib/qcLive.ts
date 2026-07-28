import type {
  QcAttemptSnapshot,
  QcCandidateRosterEntry,
  QcEvent,
  QcResultView,
  QcRunStatus,
  QcSnapshot,
  QcValidationOutcome,
  QcWorkerActivityKind,
} from "../types";

export const QC_LENS_CATALOG = [
  { id: "code_compliance", title: "Code compliance" },
  { id: "coordination_consistency", title: "Coordination & consistency" },
  { id: "completeness", title: "Scope completeness" },
  { id: "enforceability_language", title: "Enforceability & language" },
  { id: "provenance_hygiene", title: "Provenance hygiene" },
] as const;

/** Frames worth reconciling with the authoritative status payload. Chatty
 * worker activity stays local; report/status transitions never do. */
export const QC_MILESTONE_TYPES: ReadonlySet<QcEvent["type"]> = new Set([
  "qc_started",
  "lens_complete",
  "lens_failed",
  "qc_complete",
  "qc_failed",
  "qc_attempt_settled",
]);

const RECENT_CAP = 3;

export type QcLivePhase =
  | "idle"
  | "lenses"
  | "verification"
  | "validation"
  | "complete"
  | "failed";

export type QcStageStatus = "queued" | "active" | "complete" | "failed";
export type QcLensLiveStatus = "queued" | "running" | "completed" | "failed";
export type QcVerifierSeatStatus =
  | "queued"
  | "active"
  | "upheld"
  | "not_upheld"
  | "failed"
  | "cancelled";

export interface QcLiveRecentItem {
  kind: "search" | "fetch";
  text: string;
  seq: number;
}

export interface QcLiveRetry {
  attempt: number;
  maxAttempts: number;
  reason: string;
  backoffSeconds: number;
}

export interface QcLensLiveState {
  id: string;
  title: string;
  status: QcLensLiveStatus;
  activity: QcWorkerActivityKind | "";
  recent: QcLiveRecentItem[];
  retry: QcLiveRetry | null;
  searches: number;
  fetches: number;
  reviewedChecks: number;
  candidates: number;
  grounded: number;
  requests: number;
  error: string;
}

export interface QcVerifierSeatLiveState {
  index: number;
  status: QcVerifierSeatStatus;
  activity: QcWorkerActivityKind | "";
  recent: QcLiveRecentItem[];
  retry: QcLiveRetry | null;
  revisedSeverity: string | null;
  opsAdequate: boolean | null;
  error: string;
}

export interface QcCandidateLiveState {
  id: string;
  title: string;
  originalSeverity: string;
  lensId: string;
  panelSize: number;
  threshold: number;
  seats: QcVerifierSeatLiveState[];
  outcome: "upheld" | "refuted" | "inconclusive" | null;
  upholds: number;
}

export type QcValidationLiveStatus =
  | "queued"
  | "running"
  | QcValidationOutcome;

export interface QcValidationLiveState {
  candidateId: string;
  title: string;
  status: QcValidationLiveStatus;
  semanticStatus: string;
  opsValid: boolean | null;
  reason: string;
}

export interface QcStageLiveState {
  id: "lenses" | "verification" | "validation";
  label: string;
  status: QcStageStatus;
  done: number;
  total: number;
}

export interface QcLiveState {
  runId: string;
  phase: QcLivePhase;
  runState:
    | "idle"
    | "running"
    | "settling"
    | "complete"
    | "partial"
    | "failed"
    | "cancelled";
  settling: boolean;
  lenses: QcLensLiveState[];
  candidates: QcCandidateLiveState[];
  inReview: QcCandidateLiveState[];
  waiting: QcCandidateLiveState[];
  resolved: QcCandidateLiveState[];
  validation: QcValidationLiveState[];
  stages: QcStageLiveState[];
  totals: {
    lensesCompleted: number;
    candidates: number;
    seats: number;
    completedSeats: number;
    upheld: number;
    refuted: number;
    inconclusive: number;
    validations: number;
    validationsDone: number;
    safeFixes: number;
    advisory: number;
    manual: number;
  };
  liveMessage: string;
  error: string;
}

function eventRunId(events: readonly QcEvent[]): string {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (event.type === "qc_started" && event.run_id) return event.run_id;
    if (
      (event.type === "qc_complete" || event.type === "qc_attempt_settled") &&
      event.run_id
    ) {
      return event.run_id;
    }
  }
  return "";
}

export function qcSnapshotRunId(snapshot: QcSnapshot | null | undefined): string {
  if (!snapshot) return "";
  return (
    eventRunId(snapshot.events ?? []) ||
    snapshot.latest_attempt?.run_id ||
    snapshot.report?.run_id ||
    snapshot.result?.run_id ||
    ""
  );
}

function normalizedEvents(events: readonly QcEvent[]): QcEvent[] {
  const numbered = new Map<number, QcEvent>();
  const unnumbered: QcEvent[] = [];
  for (const event of events) {
    if (event.type === "stream_end") continue;
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

function maxEventSeq(events: readonly QcEvent[]): number {
  let maximum = -1;
  for (const event of events) {
    if (typeof event.seq === "number") maximum = Math.max(maximum, event.seq);
  }
  return maximum;
}

function applyLifecycleEvent(base: QcSnapshot, event: QcEvent): QcSnapshot {
  switch (event.type) {
    case "qc_started":
      return {
        ...base,
        status: "running",
        error: "",
        error_kind: "",
        settling: false,
      };
    case "qc_complete":
      return {
        ...base,
        status: "complete",
        error: "",
        error_kind: "",
        settling: false,
      };
    case "qc_failed":
      return {
        ...base,
        status: "failed",
        error: event.error ?? base.error,
        error_kind: event.error_kind ?? base.error_kind,
        settling: event.settling ?? false,
      };
    case "qc_attempt_settled":
      return {
        ...base,
        status: event.status === "complete" ? "complete" : "failed",
        settling: false,
      };
    default:
      return base;
  }
}

/** Merge one SSE frame into the append-only local log. Replayed frames replace
 * their same-seq predecessor; a genuinely new qc_started frame resets the
 * previous run. The unlogged stream sentinel is intentionally ignored. */
export function mergeQcEvent(
  previous: QcSnapshot | null,
  event: QcEvent,
): QcSnapshot {
  const base: QcSnapshot = previous ?? {
    status: "running",
    error: "",
    events: [],
  };
  if (event.type === "stream_end") return base;
  const previousMaxSeq = maxEventSeq(base.events);
  if (event.type === "qc_started") {
    const previousRunId = qcSnapshotRunId(base);
    if (previousRunId && previousRunId !== event.run_id) {
      return applyLifecycleEvent({ ...base, events: [event] }, event);
    }
  }
  const last = base.events[base.events.length - 1];
  let merged: QcSnapshot;
  if (
    typeof event.seq === "number" &&
    (!last || typeof last.seq !== "number" || event.seq > last.seq)
  ) {
    merged = { ...base, events: [...base.events, event] };
  } else {
    merged = { ...base, events: normalizedEvents([...base.events, event]) };
  }
  // Replayed or out-of-order lifecycle frames must not roll a newer local
  // terminal state backward. Unnumbered legacy frames are applied because
  // they cannot participate in seq ordering.
  const advancesLifecycle =
    typeof event.seq !== "number" || event.seq > previousMaxSeq;
  return advancesLifecycle ? applyLifecycleEvent(merged, event) : merged;
}

function statusRank(status: QcRunStatus): number {
  if (status === "idle") return 0;
  if (status === "running") return 1;
  return 2;
}

/** Reconcile an async status fetch without dropping newer local frames from
 * the same run. A different run is a different world and replaces wholesale.
 * For a same-run stale fetch, terminal status and stop settlement cannot march
 * backward even though all other report fields remain server-owned. */
export interface QcSnapshotReconciliation {
  snapshot: QcSnapshot;
  /** False means the response was provably older and no side effects (such
   * as auth-modal dedupe changes) may be driven from it. */
  accepted: boolean;
}

export interface QcRefreshGeneration {
  requestGeneration: number;
  currentGeneration: number;
}

export function reconcileQcSnapshotUpdate(
  previous: QcSnapshot | null,
  fetched: QcSnapshot,
  generation?: QcRefreshGeneration,
): QcSnapshotReconciliation {
  if (
    generation &&
    generation.requestGeneration !== generation.currentGeneration
  ) {
    return { snapshot: previous ?? fetched, accepted: false };
  }
  if (!previous) return { snapshot: fetched, accepted: true };
  const previousRunId = qcSnapshotRunId(previous);
  const fetchedRunId = qcSnapshotRunId(fetched);
  if (!previousRunId || !fetchedRunId) {
    return { snapshot: fetched, accepted: true };
  }
  if (previousRunId !== fetchedRunId) {
    return { snapshot: fetched, accepted: true };
  }

  // Runner snapshots are atomic: any newer terminal report or attempt state
  // is published with its event. A same-run response whose event watermark is
  // older than the local log is therefore wholly stale, including its report,
  // retained result, latest-attempt metadata, and compatibility fields.
  if (maxEventSeq(fetched.events ?? []) < maxEventSeq(previous.events)) {
    return { snapshot: previous, accepted: false };
  }

  const events = normalizedEvents([...(fetched.events ?? []), ...previous.events]);
  const fetchedSettled = events.some((event) => event.type === "qc_attempt_settled");
  const keepPreviousStatus = statusRank(previous.status) > statusRank(fetched.status);
  return {
    accepted: true,
    snapshot: {
      ...fetched,
      status: keepPreviousStatus ? previous.status : fetched.status,
      error: keepPreviousStatus ? previous.error : fetched.error,
      error_kind: keepPreviousStatus ? previous.error_kind : fetched.error_kind,
      settling: fetchedSettled
        ? false
        : Boolean(fetched.settling || previous.settling),
      events,
    },
  };
}

export function reconcileQcSnapshot(
  previous: QcSnapshot | null,
  fetched: QcSnapshot,
): QcSnapshot {
  return reconcileQcSnapshotUpdate(previous, fetched).snapshot;
}

export function isQcActiveSnapshot(
  snapshot: QcSnapshot | null | undefined,
): boolean {
  return snapshot?.status === "running" || snapshot?.settling === true;
}

export interface QcRecapDisposition {
  status: string;
  title: string;
  retained: boolean;
  tone: "ok" | "warn" | "error" | "neutral";
}

/** Name a settled recap without confusing the latest attempt with an older
 * retained remediation queue. Attempt status wins for a matching run, so a
 * stop-won attempt cannot be presented as complete merely because its paid
 * worker managed to assemble a complete report. */
export function qcRecapDisposition(
  report: Pick<QcResultView, "run_id" | "execution_status">,
  attempt?: Pick<QcAttemptSnapshot, "run_id" | "status"> | null,
): QcRecapDisposition {
  const retained = Boolean(
    attempt?.run_id && report.run_id && attempt.run_id !== report.run_id,
  );
  const status = String(
    (retained ? report.execution_status : attempt?.status) ||
      report.execution_status ||
      "unknown",
  ).toLowerCase();
  if (retained) {
    return {
      status,
      title: "Retained remediation run",
      retained: true,
      tone: "neutral",
    };
  }
  if (status === "complete") {
    return { status, title: "Review complete", retained: false, tone: "ok" };
  }
  if (status === "partial") {
    return {
      status,
      title: "Partial review preserved",
      retained: false,
      tone: "warn",
    };
  }
  if (status === "cancelled") {
    return {
      status,
      title: "Review cancelled",
      retained: false,
      tone: "warn",
    };
  }
  if (status === "failed") {
    return { status, title: "Review failed", retained: false, tone: "error" };
  }
  return {
    status,
    title: "Review record preserved",
    retained: false,
    tone: "neutral",
  };
}

function retryFrom(event: {
  attempt?: number;
  max_attempts?: number;
  reason?: string;
  backoff_s?: number;
}): QcLiveRetry {
  return {
    attempt: event.attempt ?? 0,
    maxAttempts: event.max_attempts ?? 0,
    reason: event.reason ?? "",
    backoffSeconds: event.backoff_s ?? 0,
  };
}

function recentItem(
  kind: QcLiveRecentItem["kind"],
  text: string | undefined,
  event: QcEvent,
): QcLiveRecentItem {
  return { kind, text: text ?? "", seq: event.seq ?? -1 };
}

function blankLens(id: string, title = ""): QcLensLiveState {
  return {
    id,
    title,
    status: "queued",
    activity: "",
    recent: [],
    retry: null,
    searches: 0,
    fetches: 0,
    reviewedChecks: 0,
    candidates: 0,
    grounded: 0,
    requests: 0,
    error: "",
  };
}

function blankSeat(index: number): QcVerifierSeatLiveState {
  return {
    index,
    status: "queued",
    activity: "",
    recent: [],
    retry: null,
    revisedSeverity: null,
    opsAdequate: null,
    error: "",
  };
}

function blankCandidate(entry: QcCandidateRosterEntry): QcCandidateLiveState & {
  reportedOutcome: QcCandidateLiveState["outcome"];
} {
  return {
    id: entry.candidate_id,
    title: entry.title,
    originalSeverity: entry.original_severity,
    lensId: entry.lens_id,
    panelSize: entry.panel_size,
    threshold: entry.threshold,
    seats: Array.from({ length: entry.panel_size }, (_, index) =>
      blankSeat(index + 1),
    ),
    outcome: null,
    reportedOutcome: null,
    upholds: 0,
  };
}

function seatIsAccountedFor(seat: QcVerifierSeatLiveState): boolean {
  return (
    seat.status === "upheld" ||
    seat.status === "not_upheld" ||
    seat.status === "failed" ||
    seat.status === "cancelled"
  );
}

function titleFromId(id: string): string {
  return id
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((word) => word[0].toUpperCase() + word.slice(1))
    .join(" ");
}

/** Fold a complete (possibly replayed or out-of-order) event log into the
 * presentation model used by the Review Room. No timers, network, or React
 * state participate, which keeps phase/outcome semantics directly testable. */
export function foldQcLiveState(
  rawEvents: readonly QcEvent[],
  snapshot?: Pick<QcSnapshot, "status" | "settling" | "error">,
): QcLiveState {
  const events = normalizedEvents(rawEvents);
  const lenses = new Map<string, QcLensLiveState>();
  for (const lens of QC_LENS_CATALOG) {
    lenses.set(lens.id, blankLens(lens.id, lens.title));
  }
  type MutableCandidate = ReturnType<typeof blankCandidate>;
  const candidates = new Map<string, MutableCandidate>();
  const validation = new Map<string, QcValidationLiveState>();
  let runId = "";
  let phase: QcLivePhase = events.length ? "lenses" : "idle";
  let runState: QcLiveState["runState"] = events.length ? "running" : "idle";
  let error = snapshot?.error ?? "";
  let validationStarted = false;
  let validationDone = false;
  let verificationTotals = {
    candidates: 0,
    seats: 0,
    completedSeats: 0,
    upheld: 0,
    refuted: 0,
    inconclusive: 0,
  };
  let validationTotals = {
    total: 0,
    done: 0,
    safeFixes: 0,
    advisory: 0,
    manual: 0,
  };
  let legacyVerificationDone = 0;
  let legacyVerificationTotal = 0;

  const ensureLens = (id: string, title = ""): QcLensLiveState => {
    let lens = lenses.get(id);
    if (!lens) {
      lens = blankLens(id, title || titleFromId(id));
      lenses.set(id, lens);
    } else if (title) {
      lens.title = title;
    }
    return lens;
  };
  const ensureCandidate = (id: string): MutableCandidate => {
    let candidate = candidates.get(id);
    if (!candidate) {
      candidate = blankCandidate({
        candidate_id: id,
        title: id,
        original_severity: "",
        lens_id: "",
        panel_size: 0,
        threshold: 0,
      });
      candidates.set(id, candidate);
    }
    return candidate;
  };
  const ensureSeat = (
    candidate: MutableCandidate,
    reviewerIndex: number,
  ): QcVerifierSeatLiveState => {
    let seat = candidate.seats.find((item) => item.index === reviewerIndex);
    if (!seat) {
      seat = blankSeat(reviewerIndex);
      candidate.seats.push(seat);
      candidate.seats.sort((left, right) => left.index - right.index);
      candidate.panelSize = Math.max(candidate.panelSize, reviewerIndex);
    }
    return seat;
  };

  for (const event of events) {
    switch (event.type) {
      case "qc_started":
        runId = event.run_id;
        phase = "lenses";
        runState = "running";
        for (const entry of event.lenses ?? []) {
          ensureLens(entry.lens_id, entry.title);
        }
        break;
      case "lens_started": {
        const lens = ensureLens(event.lens_id, event.title);
        lens.status = "running";
        lens.error = "";
        break;
      }
      case "lens_activity": {
        const lens = ensureLens(event.lens_id);
        lens.status = "running";
        lens.activity = event.kind ?? "";
        lens.retry = null;
        break;
      }
      case "lens_search": {
        const lens = ensureLens(event.lens_id);
        lens.status = "running";
        lens.activity = "searching";
        lens.retry = null;
        lens.searches += 1;
        lens.recent = [recentItem("search", event.query, event), ...lens.recent].slice(
          0,
          RECENT_CAP,
        );
        break;
      }
      case "lens_fetch": {
        const lens = ensureLens(event.lens_id);
        lens.status = "running";
        lens.activity = "fetching";
        lens.retry = null;
        lens.fetches += 1;
        lens.recent = [recentItem("fetch", event.url, event), ...lens.recent].slice(
          0,
          RECENT_CAP,
        );
        break;
      }
      case "lens_retry": {
        const lens = ensureLens(event.lens_id);
        lens.status = "running";
        lens.activity = "";
        lens.retry = retryFrom(event);
        break;
      }
      case "lens_complete":
      case "lens_failed": {
        const lens = ensureLens(event.lens_id, event.title);
        lens.status = event.type === "lens_complete" ? "completed" : "failed";
        lens.activity = "";
        lens.retry = null;
        lens.reviewedChecks = event.reviewed_check_count ?? lens.reviewedChecks;
        lens.candidates =
          event.candidate_count ?? event.finding_count ?? lens.candidates;
        lens.grounded = event.grounded_count ?? lens.grounded;
        lens.searches = event.search_count ?? lens.searches;
        lens.fetches = event.fetch_count ?? lens.fetches;
        lens.requests = event.request_count ?? lens.requests;
        lens.error = event.error ?? "";
        break;
      }
      case "verify_progress":
        legacyVerificationDone = event.done ?? legacyVerificationDone;
        legacyVerificationTotal = event.total ?? legacyVerificationTotal;
        if (legacyVerificationTotal > 0 || event.total === 0) phase = "verification";
        break;
      case "verification_started":
        phase = "verification";
        for (const entry of event.candidates ?? []) {
          candidates.set(entry.candidate_id, blankCandidate(entry));
        }
        verificationTotals.candidates =
          event.total_candidates ?? event.candidates.length;
        verificationTotals.seats =
          event.total_seats ??
          event.candidates.reduce((total, item) => total + item.panel_size, 0);
        break;
      case "verifier_started": {
        const seat = ensureSeat(
          ensureCandidate(event.candidate_id),
          event.reviewer_index,
        );
        seat.status = "active";
        seat.error = "";
        break;
      }
      case "verifier_activity": {
        const seat = ensureSeat(
          ensureCandidate(event.candidate_id),
          event.reviewer_index,
        );
        seat.status = "active";
        seat.activity = event.kind ?? "";
        seat.retry = null;
        break;
      }
      case "verifier_search": {
        const seat = ensureSeat(
          ensureCandidate(event.candidate_id),
          event.reviewer_index,
        );
        seat.status = "active";
        seat.activity = "searching";
        seat.retry = null;
        seat.recent = [recentItem("search", event.query, event), ...seat.recent].slice(
          0,
          RECENT_CAP,
        );
        break;
      }
      case "verifier_fetch": {
        const seat = ensureSeat(
          ensureCandidate(event.candidate_id),
          event.reviewer_index,
        );
        seat.status = "active";
        seat.activity = "fetching";
        seat.retry = null;
        seat.recent = [recentItem("fetch", event.url, event), ...seat.recent].slice(
          0,
          RECENT_CAP,
        );
        break;
      }
      case "verifier_retry": {
        const seat = ensureSeat(
          ensureCandidate(event.candidate_id),
          event.reviewer_index,
        );
        seat.status = "active";
        seat.activity = "";
        seat.retry = retryFrom(event);
        break;
      }
      case "verifier_complete": {
        const candidate = ensureCandidate(event.candidate_id);
        const seat = ensureSeat(candidate, event.reviewer_index);
        if (event.status === "completed") {
          seat.status = event.upholds ? "upheld" : "not_upheld";
        } else if (event.status === "cancelled") {
          seat.status = "cancelled";
        } else {
          seat.status = "failed";
        }
        seat.activity = "";
        seat.retry = null;
        seat.revisedSeverity = event.revised_severity ?? null;
        seat.opsAdequate =
          event.status === "completed" ? (event.ops_adequate ?? null) : null;
        seat.error = event.error ?? "";
        break;
      }
      case "candidate_complete": {
        const candidate = ensureCandidate(event.candidate_id);
        candidate.panelSize = event.panel_size ?? candidate.panelSize;
        candidate.threshold = event.threshold ?? candidate.threshold;
        candidate.reportedOutcome = event.outcome;
        candidate.upholds = event.upholds ?? candidate.upholds;
        break;
      }
      case "verification_complete":
        verificationTotals = {
          candidates: event.total_candidates ?? candidates.size,
          seats: event.total_seats ?? verificationTotals.seats,
          completedSeats: event.completed_seats ?? 0,
          upheld: event.upheld ?? 0,
          refuted: event.refuted ?? 0,
          inconclusive: event.inconclusive ?? 0,
        };
        break;
      case "validation_started":
        phase = "validation";
        validationStarted = true;
        validationTotals.total = event.total ?? 0;
        for (const candidate of candidates.values()) {
          if (candidate.reportedOutcome === "upheld") {
            validation.set(candidate.id, {
              candidateId: candidate.id,
              title: candidate.title,
              status: "queued",
              semanticStatus: "",
              opsValid: null,
              reason: "",
            });
          }
        }
        break;
      case "validation_progress": {
        const candidate = ensureCandidate(event.candidate_id);
        validation.set(event.candidate_id, {
          candidateId: event.candidate_id,
          title: candidate.title,
          status: event.outcome ?? "running",
          semanticStatus: event.ops_semantic_status ?? "",
          opsValid: event.ops_valid ?? null,
          reason: event.reason ?? "",
        });
        validationTotals.done = event.done ?? validationTotals.done;
        validationTotals.total = event.total ?? validationTotals.total;
        break;
      }
      case "validation_complete":
        validationDone = true;
        validationTotals = {
          total: event.total ?? validationTotals.total,
          done: event.done ?? validationTotals.done,
          safeFixes: event.safe_fix_count ?? 0,
          advisory: event.advisory_count ?? 0,
          manual: event.manual_count ?? 0,
        };
        break;
      case "qc_complete":
        runId = event.run_id ?? runId;
        phase = "complete";
        runState = event.execution_status === "partial" ? "partial" : "complete";
        verificationTotals.candidates =
          verificationTotals.candidates ||
          (event.finding_count ?? 0) +
            (event.refuted_count ?? 0) +
            (event.inconclusive_count ?? 0);
        verificationTotals.upheld =
          verificationTotals.upheld || event.finding_count || 0;
        verificationTotals.refuted =
          verificationTotals.refuted || event.refuted_count || 0;
        verificationTotals.inconclusive =
          verificationTotals.inconclusive || event.inconclusive_count || 0;
        break;
      case "qc_failed":
        runState = snapshot?.settling ? "settling" : "failed";
        error = event.error ?? error;
        break;
      case "qc_attempt_settled":
        runId = event.run_id ?? runId;
        if (event.status === "cancelled") runState = "cancelled";
        else if (event.execution_status === "partial") runState = "partial";
        else if (event.status === "complete") runState = "complete";
        else runState = "failed";
        phase = runState === "failed" || runState === "cancelled" ? "failed" : "complete";
        break;
      case "stream_end":
        break;
    }
  }

  const candidateList = [...candidates.values()].map((candidate) => {
    const accounted = candidate.seats.filter(seatIsAccountedFor).length;
    candidate.outcome =
      candidate.panelSize > 0 && accounted >= candidate.panelSize
        ? candidate.reportedOutcome
        : null;
    return candidate as QcCandidateLiveState;
  });
  const resolved = candidateList.filter((candidate) => candidate.outcome !== null);
  const inReview = candidateList.filter(
    (candidate) =>
      candidate.outcome === null &&
      candidate.seats.some((seat) => seat.status !== "queued"),
  );
  const waiting = candidateList.filter(
    (candidate) =>
      candidate.outcome === null &&
      candidate.seats.every((seat) => seat.status === "queued"),
  );
  const lensList = [...lenses.values()];
  const lensesFinished = lensList.filter(
    (lens) => lens.status === "completed" || lens.status === "failed",
  ).length;
  const lensesCompleted = lensList.filter(
    (lens) => lens.status === "completed",
  ).length;

  if (snapshot?.settling) runState = "settling";
  else if (snapshot?.status === "running" && runState === "idle") {
    runState = "running";
    phase = "lenses";
  }
  else if (snapshot?.status === "failed") {
    if (runState === "running") runState = "failed";
    phase = "failed";
  }
  const settling = runState === "settling";

  const candidateTotal =
    verificationTotals.candidates || legacyVerificationTotal || candidateList.length;
  const resolvedTotal =
    verificationTotals.upheld +
      verificationTotals.refuted +
      verificationTotals.inconclusive ||
    legacyVerificationDone ||
    resolved.length;
  const terminal = phase === "complete" || phase === "failed";
  const lensStageStatus: QcStageStatus =
    phase === "lenses"
      ? "active"
      : lensesFinished > 0 || phase !== "idle"
        ? lensList.some((lens) => lens.status === "failed") && terminal
          ? "failed"
          : "complete"
        : "queued";
  const verificationStageStatus: QcStageStatus =
    phase === "verification"
      ? "active"
      : phase === "validation" || phase === "complete"
        ? "complete"
        : phase === "failed" && candidateTotal > 0
          ? "failed"
          : "queued";
  const validationStageStatus: QcStageStatus =
    phase === "validation"
      ? "active"
      : validationDone || phase === "complete"
        ? "complete"
        : phase === "failed" && validationStarted
          ? "failed"
          : "queued";
  const stages: QcStageLiveState[] = [
    {
      id: "lenses",
      label: "Specialist lenses",
      status: lensStageStatus,
      done: lensesFinished,
      total: lensList.length,
    },
    {
      id: "verification",
      label: "Adversarial panels",
      status: verificationStageStatus,
      done: resolvedTotal,
      total: candidateTotal,
    },
    {
      id: "validation",
      label: "Fix validation",
      status: validationStageStatus,
      done: validationTotals.done,
      total: validationTotals.total,
    },
  ];

  let liveMessage = "Final QC has not started.";
  if (settling) {
    liveMessage = "Stop requested. Finishing already-paid in-flight work.";
  } else if (phase === "lenses") {
    liveMessage = `Specialist review: ${lensesFinished} of ${lensList.length} lenses finished.`;
  } else if (phase === "verification") {
    liveMessage = `Adversarial verification: ${resolvedTotal} of ${candidateTotal} candidates resolved.`;
  } else if (phase === "validation") {
    liveMessage = `Local fix validation: ${validationTotals.done} of ${validationTotals.total} candidates checked.`;
  } else if (phase === "complete") {
    liveMessage = `Final QC complete: ${verificationTotals.upheld} upheld, ${verificationTotals.refuted} refuted, ${validationTotals.safeFixes} safe fixes.`;
  } else if (phase === "failed") {
    liveMessage = runState === "cancelled" ? "Final QC cancelled." : "Final QC failed.";
  }

  return {
    runId,
    phase,
    runState,
    settling,
    lenses: lensList,
    candidates: candidateList,
    inReview,
    waiting,
    resolved,
    validation: [...validation.values()],
    stages,
    totals: {
      lensesCompleted,
      candidates: candidateTotal,
      seats: verificationTotals.seats,
      completedSeats: verificationTotals.completedSeats,
      upheld: verificationTotals.upheld,
      refuted: verificationTotals.refuted,
      inconclusive: verificationTotals.inconclusive,
      validations: validationTotals.total,
      validationsDone: validationTotals.done,
      safeFixes: validationTotals.safeFixes,
      advisory: validationTotals.advisory,
      manual: validationTotals.manual,
    },
    liveMessage,
    error,
  };
}
