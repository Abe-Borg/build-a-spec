import assert from "node:assert/strict";
import test from "node:test";

import {
  foldQcLiveState,
  isQcActiveSnapshot,
  isQcStopSettling,
  mergeQcEvent,
  qcRecapDisposition,
  reconcileQcSnapshot,
  reconcileQcSnapshotUpdate,
} from "../src/lib/qcLive.ts";
import type { QcEvent, QcSnapshot } from "../src/types.ts";

const started = (runId = "run-1", seq = 0): QcEvent => ({
  type: "qc_started",
  seq,
  run_id: runId,
  lenses: [
    { lens_id: "code_compliance", title: "Code compliance" },
    { lens_id: "coordination_consistency", title: "Coordination & consistency" },
    { lens_id: "completeness", title: "Scope completeness" },
    { lens_id: "enforceability_language", title: "Enforceability & language" },
    { lens_id: "provenance_hygiene", title: "Provenance hygiene" },
  ],
});

test("QC event merge deduplicates seq and orders replayed frames", () => {
  let snapshot: QcSnapshot | null = null;
  snapshot = mergeQcEvent(snapshot, started());
  snapshot = mergeQcEvent(snapshot, {
    type: "lens_activity",
    seq: 2,
    lens_id: "code_compliance",
    kind: "thinking",
  });
  snapshot = mergeQcEvent(snapshot, {
    type: "lens_started",
    seq: 1,
    lens_id: "code_compliance",
  });
  snapshot = mergeQcEvent(snapshot, {
    type: "lens_activity",
    seq: 2,
    lens_id: "code_compliance",
    kind: "writing",
  });
  assert.deepEqual(snapshot.events.map((event) => event.seq), [0, 1, 2]);
  assert.equal(snapshot.events[2].type, "lens_activity");
  if (snapshot.events[2].type === "lens_activity") {
    assert.equal(snapshot.events[2].kind, "writing");
  }
});

test("qc_started activates an idle snapshot and preserves a retained queue", () => {
  const retainedResult = {
    run_id: "retained-run",
    execution_status: "complete",
  } as NonNullable<QcSnapshot["result"]>;
  const snapshot = mergeQcEvent(
    {
      status: "idle",
      error: "old error",
      error_kind: "auth_error",
      settling: true,
      events: [],
      result: retainedResult,
    },
    started("run-1"),
  );
  assert.equal(snapshot.status, "running");
  assert.equal(snapshot.error, "");
  assert.equal(snapshot.error_kind, "");
  assert.equal(snapshot.settling, false);
  assert.equal(snapshot.result, retainedResult);
});

test("a new run resets only live fields and keeps the retained remediation result", () => {
  const retainedResult = {
    run_id: "retained-run",
    execution_status: "complete",
  } as NonNullable<QcSnapshot["result"]>;
  const previous: QcSnapshot = {
    status: "complete",
    error: "",
    events: [
      started("old-run"),
      { type: "qc_complete", seq: 1, run_id: "old-run", execution_status: "complete" },
    ],
    result: retainedResult,
  };
  const next = mergeQcEvent(previous, started("new-run"));
  assert.equal(next.status, "running");
  assert.equal(next.result, retainedResult);
  assert.deepEqual(next.events, [started("new-run")]);
});

test("terminal SSE events settle local lifecycle state without a status refresh", () => {
  let snapshot = mergeQcEvent(
    { status: "idle", error: "", events: [] },
    started(),
  );
  snapshot = mergeQcEvent(snapshot, {
    type: "qc_failed",
    seq: 1,
    error: "Stopped",
    settling: true,
  });
  assert.equal(snapshot.status, "failed");
  assert.equal(snapshot.settling, true);
  snapshot = mergeQcEvent(snapshot, {
    type: "qc_attempt_settled",
    seq: 2,
    run_id: "run-1",
    status: "cancelled",
  });
  assert.equal(snapshot.status, "failed");
  assert.equal(snapshot.settling, false);

  // An old replay cannot reopen settlement after the newer terminal frame.
  snapshot = mergeQcEvent(snapshot, {
    type: "qc_failed",
    seq: 1,
    error: "Stopped",
    settling: true,
  });
  assert.equal(snapshot.settling, false);

  let completed = mergeQcEvent(null, started("run-2"));
  completed = mergeQcEvent(completed, {
    type: "qc_complete",
    seq: 1,
    run_id: "run-2",
    execution_status: "complete",
  });
  assert.equal(completed.status, "complete");
  assert.equal(completed.settling, false);
});

test("QC snapshot reconciliation preserves same-run live frames and replaces another run", () => {
  const previous: QcSnapshot = {
    status: "complete",
    error: "",
    events: [
      started(),
      { type: "lens_started", seq: 1, lens_id: "code_compliance" },
      { type: "qc_complete", seq: 2, run_id: "run-1", execution_status: "complete" },
    ],
  };
  const staleFetch: QcSnapshot = {
    status: "running",
    error: "",
    events: [started(), { type: "lens_started", seq: 1, lens_id: "code_compliance" }],
  };
  const reconciled = reconcileQcSnapshot(previous, staleFetch);
  assert.equal(reconciled.status, "complete");
  assert.deepEqual(reconciled.events.map((event) => event.seq), [0, 1, 2]);

  const nextRun: QcSnapshot = {
    status: "running",
    error: "",
    events: [started("run-2")],
  };
  assert.deepEqual(reconcileQcSnapshot(previous, nextRun), nextRun);
});

test("same-run stale reconciliation rejects stale report and attempt fields wholesale", () => {
  const currentReport = {
    run_id: "run-1",
    execution_status: "complete",
  } as NonNullable<QcSnapshot["report"]>;
  const retainedResult = {
    run_id: "run-1",
    execution_status: "complete",
  } as NonNullable<QcSnapshot["result"]>;
  const previous: QcSnapshot = {
    status: "complete",
    error: "",
    events: [
      started(),
      { type: "lens_started", seq: 1, lens_id: "code_compliance" },
      { type: "qc_complete", seq: 2, run_id: "run-1", execution_status: "complete" },
    ],
    report: currentReport,
    result: retainedResult,
    latest_attempt: {
      run_id: "run-1",
      status: "complete",
      error: "",
      started_at: "start",
      finished_at: "finish",
      report_available: true,
    },
  };
  const staleReport = {
    run_id: "retained-old-run",
    execution_status: "complete",
  } as NonNullable<QcSnapshot["report"]>;
  const fetched: QcSnapshot = {
    status: "running",
    error: "",
    events: [started(), { type: "lens_started", seq: 1, lens_id: "code_compliance" }],
    report: staleReport,
    result: staleReport,
    latest_attempt: {
      run_id: "run-1",
      status: "running",
      error: "",
      started_at: "start",
      finished_at: "",
      report_available: false,
    },
  };
  const reconciled = reconcileQcSnapshot(previous, fetched);
  assert.equal(reconciled, previous);
  assert.equal(reconciled.report, currentReport);
  assert.equal(reconciled.latest_attempt?.status, "complete");
});

test("a delayed older-run auth snapshot cannot replace a newly streamed run", () => {
  const currentStart = {
    ...started("run-b"),
    // Real runner event stamps are deliberately time-only and cannot order
    // two runs that start within the same second.
    ts: "12:00:00",
  };
  const current: QcSnapshot = {
    status: "running",
    error: "",
    events: [currentStart],
  };
  const staleAuth: QcSnapshot = {
    status: "failed",
    error: "Authentication failed",
    error_kind: "auth_error",
    events: [
      {
        ...started("run-a"),
        ts: "12:00:00",
      },
      {
        type: "qc_failed",
        seq: 1,
        ts: "12:00:00",
        error: "Authentication failed",
        error_kind: "auth_error",
      },
    ],
  };
  const decision = reconcileQcSnapshotUpdate(current, staleAuth, {
    requestGeneration: 4,
    currentGeneration: 5,
  });
  assert.equal(decision.accepted, false);
  assert.equal(decision.snapshot, current);
  assert.equal(decision.snapshot.error_kind, undefined);

  const newer: QcSnapshot = {
    status: "running",
    error: "",
    events: [
      {
        ...started("run-c"),
        ts: "12:00:00",
      },
    ],
  };
  const newerDecision = reconcileQcSnapshotUpdate(current, newer, {
    requestGeneration: 5,
    currentGeneration: 5,
  });
  assert.equal(newerDecision.accepted, true);
  assert.equal(newerDecision.snapshot, newer);
});

test("QC snapshot reconciliation retains settlement until its terminal frame", () => {
  const previous: QcSnapshot = {
    status: "failed",
    error: "Stopped",
    settling: true,
    events: [started(), { type: "qc_failed", seq: 1, error: "Stopped" }],
  };
  const fetched: QcSnapshot = { ...previous, settling: false };
  assert.equal(reconcileQcSnapshot(previous, fetched).settling, true);
  const settled: QcSnapshot = {
    ...fetched,
    events: [
      ...fetched.events,
      { type: "qc_attempt_settled", seq: 2, run_id: "run-1", status: "cancelled" },
    ],
  };
  assert.equal(reconcileQcSnapshot(previous, settled).settling, false);
});

test("stop settlement is a terminal state, never an ordinary run", () => {
  // The four rows of the state contract. `settling` alone was true for the
  // whole of every normal run, which put a run-long "Stop requested —
  // finishing already-paid in-flight work" banner in front of anyone who
  // ran Final QC for the first time.
  assert.equal(
    isQcStopSettling({ status: "running", settling: false }),
    false,
    "normal active run",
  );
  assert.equal(
    isQcStopSettling({ status: "running", settling: true }),
    false,
    "a running snapshot is never settling, whatever the bit says",
  );
  assert.equal(
    isQcStopSettling({ status: "complete", settling: false }),
    false,
    "normal completed run",
  );
  assert.equal(
    isQcStopSettling({ status: "failed", settling: true }),
    true,
    "user stop won, worker still unwinding",
  );
  assert.equal(
    isQcStopSettling({ status: "failed", settling: false }),
    false,
    "stopped worker fully attached",
  );
  assert.equal(isQcStopSettling(null), false);

  // The active gate still covers both states — start/apply/dismiss must
  // stay blocked through a genuine settlement.
  assert.equal(isQcActiveSnapshot({ status: "running", settling: false }), true);
  assert.equal(isQcActiveSnapshot({ status: "failed", settling: true }), true);
  assert.equal(isQcActiveSnapshot({ status: "failed", settling: false }), false);
});

test("a normal running run folds to running, not settling", () => {
  const events: QcEvent[] = [started()];
  // A pre-fix backend (or a replayed stale frame) can still assert the bit
  // on a running snapshot; the Review Room must not believe it.
  const live = foldQcLiveState(events, {
    status: "running",
    settling: true,
    error: "",
  });
  assert.equal(live.runState, "running");
  assert.equal(live.settling, false);
});

test("a running snapshot clears an erroneous prior settling bit", () => {
  const previous: QcSnapshot = {
    status: "running",
    error: "",
    settling: true,
    events: [started()],
  };
  const fetched: QcSnapshot = { ...previous, settling: false };
  // Settlement is sticky only for a TERMINAL stopped attempt — otherwise one
  // bad bit would latch the drawer into stop language for the session.
  assert.equal(reconcileQcSnapshot(previous, fetched).settling, false);
});

test("a normal completion drops a stale running settling bit", () => {
  // The latch this guards: a pre-fix backend reports running+settling, then
  // the run completes normally. A normal terminal run emits no
  // `qc_attempt_settled`, so carrying the bit forward on the strength of the
  // *reconciled* status would leave the drawer in stop language, and active,
  // for good.
  const previous: QcSnapshot = {
    status: "running",
    error: "",
    settling: true,
    events: [started()],
  };
  for (const terminal of ["complete", "failed"] as const) {
    const fetched: QcSnapshot = {
      status: terminal,
      error: "",
      settling: false,
      events: [started(), { type: "qc_complete", seq: 1 }],
    };
    const merged = reconcileQcSnapshot(previous, fetched);
    assert.equal(merged.settling, false, terminal);
    assert.equal(isQcStopSettling(merged), false, terminal);
    assert.equal(isQcActiveSnapshot(merged), false, terminal);
  }
});

test("stopping preserves the active Review Room phase until settlement", () => {
  const events: QcEvent[] = [
    started(),
    {
      type: "verification_started",
      seq: 1,
      candidates: [],
      total_candidates: 0,
      total_seats: 0,
    },
    { type: "qc_failed", seq: 2, error: "Stopped" },
  ];

  assert.equal(
    foldQcLiveState(events, { status: "running", settling: false, error: "" })
      .phase,
    "verification",
  );
  const settling = foldQcLiveState(events, {
    status: "failed",
    settling: true,
    error: "Stopped",
  });
  assert.equal(settling.phase, "verification");
  assert.equal(settling.runState, "settling");
  assert.equal(
    foldQcLiveState(events, { status: "failed", settling: false, error: "Stopped" })
      .phase,
    "failed",
  );
});

test("live fold tracks real lens activity, retries, recent tools, and terminal totals", () => {
  const events: QcEvent[] = [
    started(),
    { type: "lens_started", seq: 1, lens_id: "code_compliance" },
    { type: "lens_retry", seq: 2, lens_id: "code_compliance", attempt: 1, max_attempts: 3, reason: "rate_limit" },
    { type: "lens_search", seq: 3, lens_id: "code_compliance", query: "query one" },
    { type: "lens_fetch", seq: 4, lens_id: "code_compliance", url: "https://example.com/a" },
    { type: "lens_search", seq: 5, lens_id: "code_compliance", query: "query two" },
    { type: "lens_fetch", seq: 6, lens_id: "code_compliance", url: "https://example.com/b" },
    {
      type: "lens_complete",
      seq: 7,
      lens_id: "code_compliance",
      reviewed_check_count: 8,
      candidate_count: 2,
      grounded_count: 1,
      search_count: 2,
      fetch_count: 2,
      request_count: 3,
    },
  ];
  const state = foldQcLiveState(events, { status: "running", error: "" });
  const lens = state.lenses.find((item) => item.id === "code_compliance");
  assert.equal(lens?.status, "completed");
  assert.equal(lens?.reviewedChecks, 8);
  assert.equal(lens?.candidates, 2);
  assert.equal(lens?.recent.length, 3);
  assert.equal(lens?.retry, null);
});

test("candidate outcomes wait for every dynamic verifier seat", () => {
  const base: QcEvent[] = [
    started(),
    {
      type: "verification_started",
      seq: 1,
      candidates: [
        { candidate_id: "candidate-1", title: "One", original_severity: "high", lens_id: "completeness", panel_size: 2, threshold: 2 },
      ],
      total_candidates: 1,
      total_seats: 2,
    },
    { type: "verifier_complete", seq: 2, candidate_id: "candidate-1", reviewer_index: 1, status: "completed", upholds: true, revised_severity: "high", ops_adequate: true },
    { type: "candidate_complete", seq: 3, candidate_id: "candidate-1", outcome: "upheld", panel_size: 2, threshold: 2, completed_seats: 2, upholds: 2 },
  ];
  assert.equal(foldQcLiveState(base).candidates[0].outcome, null);
  const complete = foldQcLiveState([
    ...base,
    { type: "verifier_complete", seq: 4, candidate_id: "candidate-1", reviewer_index: 2, status: "completed", upholds: true, revised_severity: "high", ops_adequate: true },
    { type: "candidate_complete", seq: 5, candidate_id: "candidate-1", outcome: "upheld", panel_size: 2, threshold: 2, completed_seats: 2, upholds: 2 },
  ]);
  assert.equal(complete.candidates[0].outcome, "upheld");
  assert.equal(complete.resolved.length, 1);
});

test("failed and cancelled seats resolve only as infrastructure-inconclusive", () => {
  const state = foldQcLiveState([
    started(),
    {
      type: "verification_started",
      seq: 1,
      candidates: [
        { candidate_id: "candidate-1", title: "One", original_severity: "medium", lens_id: "completeness", panel_size: 2, threshold: 2 },
      ],
    },
    { type: "verifier_complete", seq: 2, candidate_id: "candidate-1", reviewer_index: 1, status: "failed", error: "provider" },
    { type: "verifier_complete", seq: 3, candidate_id: "candidate-1", reviewer_index: 2, status: "cancelled", error: "cancelled" },
    { type: "candidate_complete", seq: 4, candidate_id: "candidate-1", outcome: "inconclusive", panel_size: 2, threshold: 2, completed_seats: 0, upholds: 0 },
  ]);
  assert.equal(state.candidates[0].outcome, "inconclusive");
  assert.deepEqual(state.candidates[0].seats.map((seat) => seat.status), ["failed", "cancelled"]);
});

test("zero-candidate verification and validation are explicit completed stages", () => {
  const state = foldQcLiveState([
    started(),
    { type: "verification_started", seq: 1, candidates: [], total_candidates: 0, total_seats: 0 },
    { type: "verification_complete", seq: 2, total_candidates: 0, total_seats: 0, completed_seats: 0, upheld: 0, refuted: 0, inconclusive: 0 },
    { type: "validation_started", seq: 3, total: 0 },
    { type: "validation_complete", seq: 4, total: 0, done: 0, safe_fix_count: 0, advisory_count: 0, manual_count: 0 },
    { type: "qc_complete", seq: 5, run_id: "run-1", execution_status: "complete", finding_count: 0, refuted_count: 0, inconclusive_count: 0 },
  ]);
  assert.equal(state.runState, "complete");
  assert.equal(state.stages[1].status, "complete");
  assert.equal(state.stages[2].status, "complete");
  assert.equal(state.totals.candidates, 0);
});

test("validation fold distinguishes safe, advisory, and manual outcomes", () => {
  const state = foldQcLiveState([
    started(),
    { type: "validation_started", seq: 1, total: 3 },
    { type: "validation_progress", seq: 2, candidate_id: "candidate-1", done: 1, total: 3, outcome: "safe_fix", ops_semantic_status: "approved", ops_valid: true },
    { type: "validation_progress", seq: 3, candidate_id: "candidate-2", done: 2, total: 3, outcome: "advisory", ops_semantic_status: "not_proposed", ops_valid: false },
    { type: "validation_progress", seq: 4, candidate_id: "candidate-3", done: 3, total: 3, outcome: "manual", ops_semantic_status: "rejected", ops_valid: false },
    { type: "validation_complete", seq: 5, total: 3, done: 3, safe_fix_count: 1, advisory_count: 1, manual_count: 1 },
  ]);
  assert.deepEqual(state.validation.map((item) => item.status), ["safe_fix", "advisory", "manual"]);
  assert.equal(state.totals.safeFixes, 1);
});

test("recap status follows the matching attempt and identifies a retained queue", () => {
  const completeReport = { run_id: "run-1", execution_status: "complete" };
  assert.deepEqual(qcRecapDisposition(completeReport), {
    status: "complete",
    title: "Review complete",
    retained: false,
    tone: "ok",
  });
  assert.deepEqual(
    qcRecapDisposition(completeReport, { run_id: "run-1", status: "cancelled" }),
    {
      status: "cancelled",
      title: "Review cancelled",
      retained: false,
      tone: "warn",
    },
  );
  assert.deepEqual(
    qcRecapDisposition(completeReport, { run_id: "run-2", status: "failed" }),
    {
      status: "complete",
      title: "Retained remediation run",
      retained: true,
      tone: "neutral",
    },
  );
  assert.equal(
    qcRecapDisposition(
      { run_id: "run-1", execution_status: "failed" },
      { run_id: "run-1", status: "failed" },
    ).title,
    "Review failed",
  );
});
