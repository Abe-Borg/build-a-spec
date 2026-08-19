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

test("QC event merge orders a gap fill and drops a replayed seq", () => {
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
  const filled = snapshot;
  snapshot = mergeQcEvent(snapshot, {
    type: "lens_activity",
    seq: 2,
    lens_id: "code_compliance",
    kind: "writing",
  });
  assert.deepEqual(snapshot.events.map((event) => event.seq), [0, 1, 2]);
  // Seq 2 was assigned once by an append-only log, so the replay carries the
  // payload it carried the first time. First arrival wins, and the snapshot
  // is not rebuilt at all.
  assert.equal(snapshot, filled);
  assert.equal(snapshot.events[2].type, "lens_activity");
  if (snapshot.events[2].type === "lens_activity") {
    assert.equal(snapshot.events[2].kind, "thinking");
  }
});

test("a replayed frame returns the same snapshot and the same event array", () => {
  const log: QcEvent[] = [
    started(),
    { type: "lens_started", seq: 1, lens_id: "code_compliance" },
    { type: "lens_search", seq: 2, lens_id: "code_compliance", query: "NFPA 13" },
  ];
  const local: QcSnapshot = { status: "running", error: "", events: log };
  for (const frame of log) {
    const merged = mergeQcEvent(local, frame);
    assert.equal(merged, local, `seq ${frame.seq} should not rebuild the snapshot`);
    assert.equal(merged.events, log);
  }
});

test("replaying a dense QC log does bounded work per duplicate frame", () => {
  // Not a timing test — see the research equivalent. The Review Room's log is
  // the chattier of the two, so a per-frame scan hurts here first.
  const log: QcEvent[] = [
    started(),
    ...Array.from({ length: 400 }, (_, offset): QcEvent => ({
      type: "lens_activity",
      seq: offset + 1,
      lens_id: "code_compliance",
      kind: "searching",
    })),
  ];
  let reads = 0;
  const watched = new Proxy(log, {
    get(target, key, receiver) {
      reads += 1;
      return Reflect.get(target, key, receiver);
    },
  });
  const local: QcSnapshot = { status: "running", error: "", events: watched };

  let snapshot = mergeQcEvent(local, log[1]);
  const build = reads;
  assert.equal(snapshot, local);
  // One pass to build the index. The array iterator reads both an index and
  // `length` per step, hence the small multiple rather than exactly n.
  assert.ok(build <= log.length * 3, `the index build should be one pass, saw ${build}`);

  // Every chatty frame — the flood a reconnect replays — costs nothing at all.
  // The single qc_started frame is excluded deliberately: it settles run
  // identity before any sequence comparison, which reads the log once per
  // replay to find its run id. That is the ordering this chunk requires.
  for (const frame of log.slice(1)) snapshot = mergeQcEvent(snapshot, frame);
  assert.equal(reads - build, 0, "a duplicate frame must not touch the log");
  // The whole replay therefore costs one pass, not the ~n² a per-frame scan
  // of the log would have cost.
  assert.ok(reads < log.length ** 2);
  assert.equal(snapshot, local, "no duplicate may rebuild the snapshot");
  assert.equal(snapshot.events, watched, "no duplicate may rebuild the log");
});

test("a terminal frame from another run cannot end the run on screen", () => {
  // Sequence numbers restart at 0 per run, so a superseded run's terminal
  // frame collides with the live run's numbering. Before identity was checked
  // first, that frame flipped the live run to complete.
  const local: QcSnapshot = {
    status: "running",
    error: "",
    events: [
      started("run-2"),
      { type: "lens_started", seq: 1, lens_id: "code_compliance" },
    ],
  };
  const foreign = mergeQcEvent(local, {
    type: "qc_complete",
    seq: 1,
    run_id: "run-1",
    execution_status: "complete",
  });
  assert.equal(foreign, local);
  assert.equal(foreign.status, "running");

  // The live run's own terminal frame still lands.
  const settled = mergeQcEvent(local, {
    type: "qc_complete",
    seq: 2,
    run_id: "run-2",
    execution_status: "complete",
  });
  assert.equal(settled.status, "complete");
});

test("a live terminal frame survives a retained report from an older run", () => {
  // Dropping a frame takes the log's own run id, not qcSnapshotRunId's
  // retained-report fallback: a retained result says nothing about which run
  // is streaming, and reading the live run as foreign would swallow its
  // terminal frame permanently.
  const local: QcSnapshot = {
    status: "running",
    error: "",
    events: [started("run-2")],
    result: {
      run_id: "retained-run",
      execution_status: "complete",
    } as NonNullable<QcSnapshot["result"]>,
  };
  const settled = mergeQcEvent(local, {
    type: "qc_complete",
    seq: 1,
    run_id: "run-2",
    execution_status: "complete",
  });
  assert.equal(settled.status, "complete");
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

test("a split panel folds to disputed, distinct from inconclusive", () => {
  const state = foldQcLiveState([
    started(),
    {
      type: "verification_started",
      seq: 1,
      candidates: [
        {
          candidate_id: "candidate-1",
          title: "Contested",
          original_severity: "high",
          lens_id: "code_compliance",
          panel_size: 3,
          uphold_requires: 3,
          evidence_gated: true,
        },
      ],
      total_candidates: 1,
      total_seats: 3,
    },
    { type: "verifier_complete", seq: 2, candidate_id: "candidate-1", reviewer_index: 1, status: "completed", upholds: true, ops_adequate: false },
    { type: "verifier_complete", seq: 3, candidate_id: "candidate-1", reviewer_index: 2, status: "completed", upholds: true, ops_adequate: false },
    { type: "verifier_complete", seq: 4, candidate_id: "candidate-1", reviewer_index: 3, status: "completed", upholds: false },
    {
      type: "candidate_complete",
      seq: 5,
      candidate_id: "candidate-1",
      outcome: "disputed",
      dispute_reason: "split_panel",
      panel_size: 3,
      uphold_requires: 3,
      completed_seats: 3,
      upholds: 2,
    },
  ]);
  assert.equal(state.candidates[0].outcome, "disputed");
  assert.equal(state.candidates[0].disputeReason, "split_panel");
  // v4 sends uphold_requires; every seat must agree for a clean uphold.
  assert.equal(state.candidates[0].threshold, 3);
  assert.equal(state.resolved.length, 1);
});

test("an under-evidenced refutation folds with its own dispute reason", () => {
  const state = foldQcLiveState([
    started(),
    {
      type: "verification_started",
      seq: 1,
      candidates: [
        {
          candidate_id: "candidate-1",
          title: "Unevidenced",
          original_severity: "critical",
          lens_id: "code_compliance",
          panel_size: 3,
          uphold_requires: 3,
          evidence_gated: true,
        },
      ],
    },
    { type: "verifier_complete", seq: 2, candidate_id: "candidate-1", reviewer_index: 1, status: "completed", upholds: false },
    { type: "verifier_complete", seq: 3, candidate_id: "candidate-1", reviewer_index: 2, status: "completed", upholds: false },
    { type: "verifier_complete", seq: 4, candidate_id: "candidate-1", reviewer_index: 3, status: "completed", upholds: false },
    {
      type: "candidate_complete",
      seq: 5,
      candidate_id: "candidate-1",
      outcome: "disputed",
      dispute_reason: "insufficient_refutation_evidence",
      panel_size: 3,
      uphold_requires: 3,
      completed_seats: 3,
      upholds: 0,
    },
  ]);
  assert.equal(state.candidates[0].outcome, "disputed");
  assert.equal(
    state.candidates[0].disputeReason,
    "insufficient_refutation_evidence",
  );
});

test("disputed candidates are counted separately in the run totals", () => {
  const state = foldQcLiveState([
    started(),
    {
      type: "verification_complete",
      seq: 1,
      total_candidates: 3,
      total_seats: 7,
      completed_seats: 7,
      upheld: 1,
      refuted: 1,
      disputed: 1,
      inconclusive: 0,
    },
  ]);
  assert.equal(state.totals.disputed, 1);
  assert.equal(state.totals.refuted, 1);
  assert.equal(state.totals.upheld, 1);
});

test("an older log without uphold_requires still folds on threshold", () => {
  const state = foldQcLiveState([
    started(),
    {
      type: "verification_started",
      seq: 1,
      candidates: [
        { candidate_id: "candidate-1", title: "One", original_severity: "medium", lens_id: "completeness", panel_size: 2, threshold: 2 },
      ],
    },
  ]);
  assert.equal(state.candidates[0].threshold, 2);
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

// ---------------------------------------------------------------------------
// Chunk 5.2 — cross-lens candidate consolidation
// ---------------------------------------------------------------------------

test("consolidation is a named transition, never a fourth stage on the rail", () => {
  const live = foldQcLiveState([
    started(),
    { type: "consolidation_started", seq: 1, raw_candidate_count: 4, eligible_candidate_count: 3 },
  ]);
  assert.equal(live.phase, "consolidation");
  // The board still has exactly the three gates a reviewer can pass or fail.
  assert.deepEqual(
    live.stages.map((stage) => stage.id),
    ["lenses", "verification", "validation"],
  );
  // Lenses are done; panels have not started.
  assert.equal(live.stages[0].status, "complete");
  assert.equal(live.stages[1].status, "queued");
  // And the transition is announced rather than being silent.
  assert.match(live.liveMessage, /same defect/);
  assert.equal(live.consolidation.status, "running");
  assert.equal(live.consolidation.rawCandidates, 4);
  assert.equal(live.consolidation.eligibleCandidates, 3);
});

test("a completed grouping step reports what it bought", () => {
  const live = foldQcLiveState([
    started(),
    { type: "consolidation_started", seq: 1, raw_candidate_count: 4 },
    {
      type: "consolidation_complete",
      seq: 2,
      status: "complete",
      raw_candidate_count: 4,
      grouped_candidate_count: 2,
      panels_avoided: 2,
    },
  ]);
  assert.equal(live.consolidation.status, "complete");
  assert.equal(live.consolidation.rawCandidates, 4);
  assert.equal(live.consolidation.groupedCandidates, 2);
  assert.equal(live.consolidation.panelsAvoided, 2);
  assert.equal(live.consolidation.error, "");
});

test("a failed grouping step keeps its error and the run keeps going", () => {
  const live = foldQcLiveState([
    started(),
    { type: "consolidation_started", seq: 1, raw_candidate_count: 2 },
    {
      type: "consolidation_complete",
      seq: 2,
      status: "failed",
      raw_candidate_count: 2,
      grouped_candidate_count: 2,
      panels_avoided: 0,
      error: "element:pt1.a1.p1: unaccounted candidate 1.",
    },
    {
      type: "verification_started",
      seq: 3,
      candidates: [
        {
          candidate_id: "candidate-1",
          title: "First",
          original_severity: "medium",
          lens_id: "code_compliance",
          origin_count: 1,
          panel_size: 2,
          uphold_requires: 2,
        },
        {
          candidate_id: "candidate-2",
          title: "Second",
          original_severity: "medium",
          lens_id: "completeness",
          origin_count: 1,
          panel_size: 2,
          uphold_requires: 2,
        },
      ],
      total_candidates: 2,
      total_seats: 4,
    },
  ]);
  assert.equal(live.consolidation.status, "failed");
  assert.match(live.consolidation.error, /unaccounted candidate/);
  // The failure is confined to the grouping step: verification proceeds
  // with one panel per candidate, which is the pre-5.2 behaviour.
  assert.equal(live.phase, "verification");
  assert.equal(live.totals.candidates, 2);
  assert.equal(live.stages[1].status, "active");
});

test("the grouping call's own activity frames never surface as board noise", () => {
  const quiet = foldQcLiveState([
    started(),
    { type: "consolidation_started", seq: 1, raw_candidate_count: 2 },
  ]);
  const noisy = foldQcLiveState([
    started(),
    { type: "consolidation_started", seq: 1, raw_candidate_count: 2 },
    { type: "consolidation_activity", seq: 2, bucket_id: "element:pt1.a1.p1", kind: "thinking" },
    { type: "consolidation_search", seq: 3, bucket_id: "element:pt1.a1.p1", query: "x" },
    { type: "consolidation_fetch", seq: 4, bucket_id: "element:pt1.a1.p1", url: "https://x" },
  ]);
  assert.deepEqual(noisy.consolidation, quiet.consolidation);
  assert.equal(noisy.phase, "consolidation");
});

test("a run with no grouping step folds exactly as it always did", () => {
  const live = foldQcLiveState([
    started(),
    {
      type: "verification_started",
      seq: 1,
      candidates: [
        {
          candidate_id: "candidate-1",
          title: "Only",
          original_severity: "medium",
          lens_id: "code_compliance",
          panel_size: 2,
          uphold_requires: 2,
        },
      ],
      total_candidates: 1,
      total_seats: 2,
    },
  ]);
  assert.equal(live.consolidation.status, "");
  assert.equal(live.consolidation.panelsAvoided, 0);
  assert.equal(live.phase, "verification");
});

// --- Batched phase 2 --------------------------------------------------------
//
// Phase 2 on the Message Batches API costs half as much and cannot stream, so
// the board has to say what is happening without inventing per-seat motion.
// These pin the two halves of that: the transport is known from the roster,
// and progress is only ever the provider's own counts.

const batchRoster = (seq: number): QcEvent => ({
  type: "verification_started",
  seq,
  candidates: [
    {
      candidate_id: "candidate-1",
      title: "Batched finding",
      original_severity: "medium",
      lens_id: "code_compliance",
      panel_size: 2,
      uphold_requires: 2,
      rule: "final-qc/4",
      evidence_gated: false,
      outcomes: ["upheld", "disputed", "refuted", "inconclusive"],
    },
  ],
  total_candidates: 1,
  total_seats: 2,
  transport: "batch",
});

test("a batched roster marks the transport and a streamed one does not", () => {
  const batched = foldQcLiveState([started(), batchRoster(1)], {
    status: "running",
    error: "",
  });
  assert.equal(batched.transport, "batch");

  const streamed = foldQcLiveState(
    [
      started(),
      { ...(batchRoster(1) as Record<string, unknown>), transport: "stream" } as QcEvent,
    ],
    { status: "running", error: "" },
  );
  assert.equal(streamed.transport, "stream");
  assert.equal(streamed.batch, null);
});

test("batch progress carries submitted totals forward across polling frames", () => {
  // A polling frame reports live counts but no `submitted`/`total`. Resetting
  // those to zero between ticks would make the line read "0 of 0" mid-run,
  // which on the one transport that cannot show per-seat motion looks like a
  // stall rather than progress.
  const live = foldQcLiveState(
    [
      started(),
      batchRoster(1),
      {
        type: "verification_batch",
        seq: 2,
        status: "submitted",
        round: 1,
        submitted: 2,
        total: 2,
      },
      {
        type: "verification_batch",
        seq: 3,
        status: "polling",
        round: 1,
        processing: 1,
        succeeded: 1,
        errored: 0,
      },
    ] as QcEvent[],
    { status: "running", error: "" },
  );

  assert.equal(live.transport, "batch");
  assert.equal(live.batch?.status, "polling");
  assert.equal(live.batch?.submitted, 2);
  assert.equal(live.batch?.total, 2);
  assert.equal(live.batch?.succeeded, 1);
});

test("batch progress is phase-level, so a second round cannot shrink it", () => {
  // Round 2 carries only the seats that still need work (a pause_turn
  // continuation, a retry). Rendering that round's own `submitted` against
  // an earlier round's success count showed "2 of 1" mid-phase and finished
  // at "1 of 1" on a phase that had three seats.
  const frames: QcEvent[] = [
    started(),
    batchRoster(1),
    { type: "verification_batch", seq: 2, status: "submitted", round: 1, submitted: 3, total: 3, settled: 0 },
    { type: "verification_batch", seq: 3, status: "polling", round: 1, succeeded: 3, errored: 0, processing: 0, total: 3, settled: 0 },
    // One seat paused; the other two settled.
    { type: "verification_batch", seq: 4, status: "submitted", round: 2, submitted: 1, total: 3, settled: 2 },
  ];
  const midway = foldQcLiveState(frames, { status: "running", error: "" });
  assert.equal(midway.batch?.total, 3);
  assert.equal(midway.batch?.settled, 2);
  // Round 1's success count must not survive into round 2's window.
  assert.equal(midway.batch?.succeeded, 0);
  assert.equal(midway.batch?.returned, 2);
  assert.ok((midway.batch?.returned ?? 0) <= (midway.batch?.total ?? 0));

  const ended = foldQcLiveState(
    [
      ...frames,
      { type: "verification_batch", seq: 5, status: "polling", round: 2, succeeded: 1, errored: 0, processing: 0, total: 3, settled: 2 },
      { type: "verification_batch", seq: 6, status: "ended", total: 3, settled: 3 },
    ] as QcEvent[],
    { status: "running", error: "" },
  );
  assert.equal(ended.batch?.status, "ended");
  assert.equal(ended.batch?.total, 3);
  assert.equal(ended.batch?.settled, 3);
  assert.equal(ended.batch?.returned, 3);
});

test("a batched run still resolves its candidates from seat events", () => {
  // The seat cards are unchanged by the transport — only the activity frames
  // between `verifier_started` and `verifier_complete` are missing.
  const live = foldQcLiveState(
    [
      started(),
      batchRoster(1),
      { type: "verifier_started", seq: 2, candidate_id: "candidate-1", reviewer_index: 1 },
      { type: "verifier_started", seq: 3, candidate_id: "candidate-1", reviewer_index: 2 },
      {
        type: "verifier_complete",
        seq: 4,
        candidate_id: "candidate-1",
        reviewer_index: 1,
        status: "completed",
        upholds: true,
      },
      {
        type: "verifier_complete",
        seq: 5,
        candidate_id: "candidate-1",
        reviewer_index: 2,
        status: "completed",
        upholds: true,
      },
      {
        type: "candidate_complete",
        seq: 6,
        candidate_id: "candidate-1",
        outcome: "upheld",
        panel_size: 2,
        uphold_requires: 2,
        completed_seats: 2,
        upholds: 2,
      },
    ] as QcEvent[],
    { status: "running", error: "" },
  );

  assert.equal(live.candidates.length, 1);
  assert.equal(live.candidates[0].outcome, "upheld");
  assert.equal(live.candidates[0].seats.length, 2);
  // Both seats resolved, so the panel is in the Resolved group rather than
  // stuck "in review" — the failure mode a transport with no per-seat
  // activity would otherwise be indistinguishable from.
  assert.equal(live.resolved.length, 1);
  assert.equal(live.inReview.length, 0);
});
