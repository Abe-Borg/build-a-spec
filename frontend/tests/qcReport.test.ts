import assert from "node:assert/strict";
import test from "node:test";

import {
  buildQcReportMetrics,
  collectQcOperationRecords,
  qcInconclusiveCandidates,
  qcPrimaryReport,
  qcReportExportUrl,
  qcReportLimitations,
  qcSubstantivelyRefutedCandidates,
  qcSurvivingCandidates,
  safeHttpUrl,
  verifierSeatCoverage,
  type QcReportFinding,
  type QcReportResult,
  type QcReportVerdict,
} from "../src/lib/qcReport.ts";

function verdict(
  reviewerIndex: number,
  overrides: Partial<QcReportVerdict> = {},
): QcReportVerdict {
  return {
    upholds: false,
    revised_severity: "",
    note: "not upheld",
    status: "completed",
    error: "",
    reviewer_index: reviewerIndex,
    search_queries: [],
    retrieved_sources: [],
    usage_totals: {},
    api_request_count: 1,
    model_response_count: 1,
    ...overrides,
  } as QcReportVerdict;
}

function finding(
  overrides: Partial<QcReportFinding> = {},
): QcReportFinding {
  return {
    finding_id: "qc-finding-1",
    lens_id: "completeness",
    severity: "high",
    original_severity: "high",
    element_id: "pt1.a1.p1",
    reviewed_ref: "1.1.A.1",
    reviewed_text: "Contractor shall coordinate the work.",
    element_resolved: true,
    title: "Coordination scope is incomplete",
    issue: "The coordination obligation does not identify interfaces.",
    rationale: "The reviewed text leaves the responsibility ambiguous.",
    source_urls: [],
    accepted_sources: [],
    grounded: false,
    source_checks: [],
    proposed_ops: [{ action: "replace", target_id: "pt1.a1.p1", text: "Revised" }],
    ops_valid: false,
    ops_invalid_reason: "Dry-run failed",
    verdicts: [],
    verification_outcome: "upheld",
    verification_panel_size: 3,
    verification_threshold: 2,
    status: "open",
    dismiss_reason: "",
    disposition_events: [],
    ...overrides,
  } as QcReportFinding;
}

function result(overrides: Partial<QcReportResult> = {}): QcReportResult {
  return {
    schema_version: 2,
    protocol_version: "final-qc/2",
    run_id: "qc-run-test",
    execution_status: "complete",
    summary: "Test report",
    findings: [],
    refuted: [],
    inconclusive: [],
    lens_statuses: [],
    started_at: "2026-07-24T12:00:00Z",
    finished_at: "2026-07-24T12:01:00Z",
    version_index: 1,
    version_fingerprint: "a".repeat(64),
    input_fingerprint: "b".repeat(64),
    input_manifest: {},
    model: "claude-fable-5",
    effort: "xhigh",
    max_tokens: 64000,
    duration_ms: 60000,
    usage_totals: {},
    estimated_cost_usd: 1.25,
    api_request_count: 0,
    model_response_count: 0,
    research_profile_present: true,
    dismissed_ids: [],
    ...overrides,
  } as QcReportResult;
}

test("report links only permit credential-free absolute HTTP(S) citations", () => {
  assert.equal(safeHttpUrl("javascript:alert(1)"), null);
  assert.equal(safeHttpUrl("data:text/html,<script>alert(1)</script>"), null);
  assert.equal(safeHttpUrl("file:///C:/secret.txt"), null);
  assert.equal(safeHttpUrl("https://user:pass@example.com/code"), null);
  assert.equal(safeHttpUrl("https://example.com/code"), "https://example.com/code");
});

test("report downloads pin the displayed run identity", () => {
  assert.equal(
    qcReportExportUrl("docx", "qc-run/a b"),
    "/api/qc/export?run_id=qc-run%2Fa%20b",
  );
  assert.equal(
    qcReportExportUrl("json", " qc-run-1 "),
    "/api/qc/export.json?run_id=qc-run-1",
  );
  assert.equal(qcReportExportUrl("json", ""), "/api/qc/export.json");
});

test("verifier coverage exposes duplicate indexes, failures, and missing seats", () => {
  const candidate = finding({
    verdicts: [
      {
        upholds: true,
        revised_severity: "",
        note: "supported",
        status: "completed",
        error: "",
        reviewer_index: 1,
        search_queries: [],
        retrieved_sources: [],
        usage_totals: {},
        api_request_count: 1,
        model_response_count: 1,
      },
      {
        upholds: false,
        revised_severity: "",
        note: "",
        status: "failed",
        error: "timeout",
        reviewer_index: 1,
        search_queries: [],
        retrieved_sources: [],
        usage_totals: {},
        api_request_count: 2,
        model_response_count: 1,
      },
    ],
  });
  assert.deepEqual(verifierSeatCoverage(candidate), {
    expected: 3,
    recorded: 2,
    completed: 1,
    failed: 1,
    missing: 2,
    invalidIndexRecords: 1,
    legacyUnnumbered: false,
  });
});

test("refuted operations are retained as not evaluated, never called invalid", () => {
  const surviving = finding({
    verdicts: [
      verdict(1, { upholds: true }),
      verdict(2, { upholds: true }),
      verdict(3, { upholds: false }),
    ],
  });
  const refuted = finding({
    finding_id: "qc-refuted-1",
    verification_outcome: "refuted",
    verdicts: [verdict(1), verdict(2), verdict(3)],
  });
  const operations = collectQcOperationRecords(
    result({ findings: [surviving], refuted: [refuted] }),
  );
  assert.equal(operations[0].validationStatus, "invalid");
  assert.equal(operations[1].validationStatus, "not_evaluated");
});

test("structurally incomplete refuted panels fail closed as inconclusive", () => {
  const structurallyIncomplete = finding({
    finding_id: "qc-refuted-with-failed-seat",
    verification_outcome: "refuted",
    verdicts: [
      verdict(1),
      verdict(2, { status: "failed", error: "verifier timeout" }),
      verdict(3),
    ],
  });
  const report = result({ refuted: [structurallyIncomplete] });
  const metrics = buildQcReportMetrics(report);
  const operations = collectQcOperationRecords(report);

  assert.equal(qcSubstantivelyRefutedCandidates(report).length, 0);
  assert.deepEqual(qcInconclusiveCandidates(report), [structurallyIncomplete]);
  assert.equal(metrics.refutedFindings, 0);
  assert.equal(metrics.inconclusiveFindings, 1);
  assert.equal(operations[0].findingKind, "inconclusive");
  assert.match(
    qcReportLimitations(report, false).join("\n"),
    /stored as substantively refuted[\s\S]*incomplete panel cannot supply a substantive refutation/i,
  );
});

test("missing and duplicate refuted verifier seats also fail closed", () => {
  const missingSeat = finding({
    finding_id: "qc-refuted-missing-seat",
    verification_outcome: "refuted",
    verdicts: [verdict(1), verdict(2)],
  });
  const duplicateSeat = finding({
    finding_id: "qc-refuted-duplicate-seat",
    verification_outcome: "refuted",
    verdicts: [verdict(1), verdict(1), verdict(3)],
  });
  const report = result({ refuted: [missingSeat, duplicateSeat] });

  assert.equal(qcSubstantivelyRefutedCandidates(report).length, 0);
  assert.deepEqual(qcInconclusiveCandidates(report), [missingSeat, duplicateSeat]);
  assert.equal(buildQcReportMetrics(report).inconclusiveFindings, 2);
});

test("malformed surviving panels never enter the action queue", () => {
  const failedSeat = finding({
    finding_id: "qc-survivor-failed-seat",
    verdicts: [
      verdict(1, { upholds: true }),
      verdict(2, { status: "failed", error: "timeout" }),
      verdict(3, { upholds: true }),
    ],
  });
  const missingSeat = finding({
    finding_id: "qc-survivor-missing-seat",
    verdicts: [verdict(1, { upholds: true }), verdict(2, { upholds: true })],
  });
  const duplicateSeat = finding({
    finding_id: "qc-survivor-duplicate-seat",
    verdicts: [
      verdict(1, { upholds: true }),
      verdict(1, { upholds: true }),
      verdict(3, { upholds: true }),
    ],
  });
  const report = result({ findings: [failedSeat, missingSeat, duplicateSeat] });

  assert.equal(qcSurvivingCandidates(report).length, 0);
  assert.deepEqual(qcInconclusiveCandidates(report), [
    failedSeat,
    missingSeat,
    duplicateSeat,
  ]);
  const metrics = buildQcReportMetrics(report);
  assert.equal(metrics.survivingFindings, 0);
  assert.equal(metrics.inconclusiveFindings, 3);
  assert.ok(
    collectQcOperationRecords(report).every(
      (record) =>
        record.findingKind === "inconclusive" &&
        record.validationStatus === "not_evaluated",
    ),
  );
});

test("backend-selected primary report precedes the retained action queue", () => {
  const retained = result({ run_id: "qc-run-retained" });
  const attempt = result({ run_id: "qc-run-latest-partial", execution_status: "partial" });

  assert.equal(qcPrimaryReport({ report: attempt, result: retained })?.run_id, attempt.run_id);
  assert.equal(qcPrimaryReport({ result: retained })?.run_id, retained.run_id);
  assert.equal(qcPrimaryReport(null), undefined);
});

test("infrastructure-inconclusive candidates stay separate from substantive refutations", () => {
  const inconclusive = finding({
    finding_id: "qc-inconclusive-1",
    verification_outcome: "inconclusive",
    verdicts: [
      {
        upholds: false,
        revised_severity: "",
        note: "",
        status: "failed",
        error: "upstream timeout",
        reviewer_index: 1,
        search_queries: [],
        retrieved_sources: [],
        usage_totals: {},
        api_request_count: 1,
        model_response_count: 0,
      },
    ],
  });
  const report = result({ inconclusive: [inconclusive] });
  const metrics = buildQcReportMetrics(report);
  const operations = collectQcOperationRecords(report);

  assert.equal(metrics.totalCandidates, 1);
  assert.equal(metrics.survivingFindings, 0);
  assert.equal(metrics.refutedFindings, 0);
  assert.equal(metrics.inconclusiveFindings, 1);
  assert.equal(metrics.inconclusiveSeverity.high, 1);
  assert.equal(metrics.unevaluatedInconclusiveOperations, 1);
  assert.equal(operations[0].findingKind, "inconclusive");
  assert.equal(operations[0].validationStatus, "not_evaluated");
  assert.match(
    qcReportLimitations(report, false).join("\n"),
    /infrastructure-inconclusive[\s\S]*no substantive uphold\/refute determination/i,
  );
});

test("legacy report records without an inconclusive bucket remain readable", () => {
  const legacy = result();
  delete (legacy as unknown as { inconclusive?: QcReportFinding[] }).inconclusive;
  const metrics = buildQcReportMetrics(legacy);
  assert.equal(metrics.totalCandidates, 0);
  assert.equal(metrics.inconclusiveFindings, 0);
});

test("legacy default_refuted records are normalized as infrastructure-inconclusive", () => {
  const legacyFailure = finding({
    finding_id: "qc-legacy-infrastructure-failure",
    verification_outcome: "default_refuted",
  });
  const report = result({ refuted: [legacyFailure] });
  const metrics = buildQcReportMetrics(report);
  const operations = collectQcOperationRecords(report);

  assert.equal(metrics.totalCandidates, 1);
  assert.equal(metrics.refutedFindings, 0);
  assert.equal(metrics.inconclusiveFindings, 1);
  assert.equal(qcSubstantivelyRefutedCandidates(report).length, 0);
  assert.deepEqual(qcInconclusiveCandidates(report), [legacyFailure]);
  assert.equal(operations[0].findingKind, "inconclusive");
  assert.match(
    qcReportLimitations(report, false).join("\n"),
    /historical .*default_refuted.*normalizes them to infrastructure-inconclusive/is,
  );
});

test("misbucketed inconclusive records never enter the action queue and are counted once", () => {
  const misbucketed = finding({
    finding_id: "qc-misbucketed-inconclusive",
    verification_outcome: "inconclusive",
  });
  const explicitCopy = { ...misbucketed };
  const report = result({
    findings: [misbucketed],
    inconclusive: [explicitCopy],
  });
  const metrics = buildQcReportMetrics(report);

  assert.equal(metrics.totalCandidates, 1);
  assert.equal(metrics.survivingFindings, 0);
  assert.equal(metrics.inconclusiveFindings, 1);
  assert.equal(qcSurvivingCandidates(report).length, 0);
  assert.deepEqual(qcInconclusiveCandidates(report), [explicitCopy]);
  assert.match(
    qcReportLimitations(report, false).join("\n"),
    /marked .*inconclusive.*stored outside the explicit inconclusive bucket/is,
  );
});

test("metrics count preserved failed seats and do not hide partial panels", () => {
  const candidate = finding({
    verdicts: [
      {
        upholds: true,
        revised_severity: "critical",
        note: "upheld",
        status: "completed",
        error: "",
        reviewer_index: 1,
        search_queries: ["applicable code section"],
        retrieved_sources: [],
        usage_totals: { input_tokens: 100 },
        api_request_count: 1,
        model_response_count: 1,
      },
      {
        upholds: false,
        revised_severity: "",
        note: "",
        status: "failed",
        error: "connection reset",
        reviewer_index: 2,
        search_queries: [],
        retrieved_sources: [],
        usage_totals: { input_tokens: 20 },
        api_request_count: 2,
        model_response_count: 1,
      },
    ],
  });
  const metrics = buildQcReportMetrics(
    result({ findings: [candidate], api_request_count: 3 }),
  );
  assert.equal(metrics.expectedVerifierSeats, 3);
  assert.equal(metrics.recordedVerifierSeats, 2);
  assert.equal(metrics.completedVerifierSeats, 1);
  assert.equal(metrics.failedVerifierSeats, 1);
  assert.equal(metrics.missingVerifierSeats, 1);
  assert.equal(metrics.upholdingVerdicts, 1);
  assert.equal(metrics.refutingVerdicts, 0);
  assert.equal(metrics.apiRequests, 3);
});

test("limitations identify a completed-looking register with a missing required lens", () => {
  const report = result({
    lens_statuses: [
      "code_compliance",
      "coordination_consistency",
      "completeness",
      "enforceability_language",
    ].map((lens_id) => ({
      lens_id,
      title: lens_id,
      status: "completed",
      finding_count: 0,
      grounded_count: 0,
      error: "",
    })) as QcReportResult["lens_statuses"],
  });
  assert.match(
    qcReportLimitations(report, false).join("\n"),
    /missing provenance_hygiene/i,
  );
});
