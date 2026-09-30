/**
 * Final QC's refusal fallback in the report modal (the 5.5 prompting upgrade,
 * P55-7). A streamed call the configured QC model declined can be answered by
 * the fallback model the API chooses; the record says so (`served_by_model`),
 * and the report states it on that record and once in Limitations, beside the
 * cost basis — the rescued usage is estimated at the configured model's rates.
 *
 * The helpers are tested directly. Their two sentences are pinned equal to the
 * Word memo's (`backend/spec_doc/docx_export.py`), read from the file (the
 * costChecks.test.ts idiom), and that the modal renders every record's
 * sentence through the helper is pinned at the source level (the modal has no
 * DOM harness — the chatPerf.test.ts idiom).
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  QC_FALLBACK_LIMITATION_TEMPLATE,
  QC_FALLBACK_RECORD_TEMPLATE,
  qcFallbackRecordNote,
  qcRefusalFallback,
  qcReportLimitations,
  qcServedByModels,
  type QcReportFinding,
  type QcReportLens,
  type QcReportResult,
  type QcReportVerdict,
} from "../src/lib/qcReport.ts";

const memo = readFileSync(
  new URL("../../backend/spec_doc/docx_export.py", import.meta.url),
  "utf8",
);
const modal = readFileSync(
  new URL("../src/components/QCReportModal.tsx", import.meta.url),
  "utf8",
);

const FALLBACK = "claude-opus-5";

function pythonLiteral(name: string): string {
  const match = memo.match(
    new RegExp(`^${name} = (?:\\(\\n((?:\\s+"[^\\n]*"\\n)+)\\)|("[^\\n]*"))`, "m"),
  );
  assert.ok(match, name);
  const body = match[1] ?? match[2];
  return [...body.matchAll(/"((?:[^"\\]|\\.)*)"/g)]
    .map((part) => JSON.parse(`"${part[1]}"`) as string)
    .join("");
}

function verdict(overrides: Partial<QcReportVerdict> = {}): QcReportVerdict {
  return {
    upholds: true,
    revised_severity: "",
    note: "upheld",
    status: "completed",
    reviewer_index: 1,
    ...overrides,
  } as QcReportVerdict;
}

function lens(overrides: Partial<QcReportLens> = {}): QcReportLens {
  return {
    lens_id: "code_compliance",
    title: "Code compliance",
    status: "completed",
    ...overrides,
  } as QcReportLens;
}

function result(overrides: Partial<QcReportResult> = {}): QcReportResult {
  return {
    schema_version: 4,
    protocol_version: "final-qc/4",
    run_id: "qc-run-fallback",
    execution_status: "complete",
    findings: [],
    refuted: [],
    disputed: [],
    inconclusive: [],
    lens_statuses: [],
    model: "claude-opus-5-5",
    input_fingerprint: "b".repeat(64),
    input_manifest: {},
    ...overrides,
  } as unknown as QcReportResult;
}

test("the modal states the Word memo's two sentences word for word", () => {
  assert.equal(QC_FALLBACK_RECORD_TEMPLATE, pythonLiteral("QC_FALLBACK_RECORD_TEMPLATE"));
  assert.equal(
    QC_FALLBACK_LIMITATION_TEMPLATE,
    pythonLiteral("QC_FALLBACK_LIMITATION_TEMPLATE"),
  );
});

test("a record the configured model answered says nothing", () => {
  assert.equal(qcFallbackRecordNote(lens()), "");
  assert.equal(qcFallbackRecordNote(verdict()), "");
  assert.equal(qcFallbackRecordNote(null), "");
});

test("a rescued record names the model that answered it", () => {
  assert.equal(
    qcFallbackRecordNote(verdict({ served_by_model: FALLBACK })),
    `Answered by ${FALLBACK} after a safety decline.`,
  );
});

test("anything that is not a model id is never printed as one", () => {
  for (const value of [7, "", "not a model!", ["claude-opus-5"], "a, <b>"]) {
    assert.deepEqual(qcServedByModels({ served_by_model: value }), []);
  }
  assert.deepEqual(qcServedByModels({ served_by_model: "b-model, a-model" }), [
    "a-model",
    "b-model",
  ]);
});

test("the limitation counts every rescued record and prices at the QC model", () => {
  const rescued = verdict({ served_by_model: FALLBACK });
  const report = result({
    lens_statuses: [lens({ served_by_model: FALLBACK }), lens({ lens_id: "completeness" })],
    findings: [{ verdicts: [rescued, verdict()] } as unknown as QcReportFinding],
    refuted: [{ verdicts: [rescued] } as unknown as QcReportFinding],
    consolidation: { served_by_model: FALLBACK } as unknown as QcReportResult["consolidation"],
  });
  const { count, limitation } = qcRefusalFallback(report);
  assert.equal(count, 4);
  assert.equal(
    limitation,
    `4 call(s) were answered by ${FALLBACK} after the configured model declined; ` +
      "their cost is estimated at claude-opus-5-5 rates.",
  );
  assert.ok(qcReportLimitations(report, false).includes(limitation));
});

test("an older or clean report discloses nothing, and a missing model degrades", () => {
  assert.deepEqual(qcRefusalFallback(result()), { count: 0, limitation: "" });
  const noModel = qcRefusalFallback(
    result({ model: "", lens_statuses: [lens({ served_by_model: FALLBACK })] }),
  );
  assert.match(noModel.limitation, /estimated at the configured model's rates\.$/);
});

test("the modal renders every record's sentence through the helper", () => {
  // Each field is shown exactly when its record has a sentence to show.
  for (const record of ["verdict", "lens", "report\\.consolidation"]) {
    assert.match(
      modal,
      new RegExp(
        `\\{qcFallbackRecordNote\\(${record}\\) && \\(\\s*<DataField label="Refusal fallback">\\{qcFallbackRecordNote\\(${record}\\)\\}</DataField>`,
      ),
      record,
    );
  }
  // The one sentence is the helper's, never a second wording in the modal.
  assert.doesNotMatch(modal, /after a safety decline/);
});
