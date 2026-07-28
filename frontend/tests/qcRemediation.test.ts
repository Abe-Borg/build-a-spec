import assert from "node:assert/strict";
import test from "node:test";

import {
  buildQcApplicationDigest,
  classifyQcRemediation,
  qcDecisionContextByElement,
  qcFindingDecisionSignals,
} from "../src/lib/qcRemediation.ts";
import type { QcReportFinding } from "../src/lib/qcReport.ts";
import type { SpecDoc } from "../src/types.ts";

function finding(
  overrides: Partial<QcReportFinding> = {},
): QcReportFinding {
  return {
    finding_id: "qc-remediation-1",
    title: "Resolve the project requirement",
    issue: "The project requirement needs a defensible response.",
    proposed_ops: [],
    ...overrides,
  } as QcReportFinding;
}

function nestedDoc(): SpecDoc {
  return {
    section: { number: "21 13 13", title: "Wet-Pipe Sprinkler Systems" },
    version: { index: 2, count: 3 },
    parts: [
      {
        id: "pt1",
        number: 1,
        title: "GENERAL",
        articles: [
          {
            id: "pt1.a1",
            number: "1.1",
            title: "SUMMARY",
            paragraphs: [
              {
                id: "pt1.a1.p1",
                label: "A.",
                text: "Coordinate the complete system.",
                status: "confirmed",
                source_item_id: "",
                children: [
                  {
                    id: "pt1.a1.p1.c1",
                    label: "1.",
                    text: "[ TBD : confirm the utility service ].",
                    status: "needs_input",
                    source_item_id: "",
                    children: [],
                  },
                ],
              },
              {
                id: "pt1.a1.p2",
                label: "B.",
                text: "Use the assumed design criterion.",
                status: "assumed",
                source_item_id: "",
                children: [],
              },
            ],
          },
        ],
      },
    ],
  };
}

test("decision context indexes nested provisions without inventing section context", () => {
  assert.deepEqual([...qcDecisionContextByElement(null)], []);

  const contexts = qcDecisionContextByElement(nestedDoc());
  assert.equal(contexts.size, 3);
  assert.deepEqual(contexts.get("pt1.a1.p1"), {
    status: "confirmed",
    text: "Coordinate the complete system.",
  });
  assert.deepEqual(contexts.get("pt1.a1.p1.c1"), {
    status: "needs_input",
    text: "[ TBD : confirm the utility service ].",
  });
  assert.equal(contexts.has("sec"), false);
  assert.equal(contexts.has("pt1.a1"), false);
});

test("bucket classification uses only explicit project-decision signals", () => {
  const ordinaryReview = finding({
    title: "Confirm coordination assumption",
    issue: "A professional must decide whether this is enforceable.",
  });
  assert.deepEqual(qcFindingDecisionSignals(ordinaryReview), []);
  assert.deepEqual(classifyQcRemediation(ordinaryReview, false), {
    bucket: "manual_review",
    decisionSignals: [],
  });

  const unresolved = finding({
    proposed_ops: [
      {
        action: "replace",
        target_id: "pt1.a1.p1.c1",
        text: "Retain [TBD: utility service configuration].",
        status: "needs_input",
      },
      {
        action: "set_status",
        target_id: "pt1.a1.p2",
        status: "assumed",
      },
    ],
  });
  const signals = qcFindingDecisionSignals(unresolved, {
    status: "needs_input",
    text: "[TBD]",
  });
  assert.deepEqual(signals, [
    "the current provision is marked needs input",
    "the current provision contains a [TBD]",
    "the proposed change would remain marked needs input",
    "the proposed change contains a [TBD]",
    "the proposed change would remain an unconfirmed assumption",
  ]);
  assert.deepEqual(classifyQcRemediation(unresolved, false, {
    status: "needs_input",
    text: "[TBD]",
  }), {
    bucket: "needs_decision",
    decisionSignals: signals,
  });
});

test("an actionable verified fix remains ready even on an unresolved provision", () => {
  const candidate = finding({
    proposed_ops: [
      {
        action: "replace",
        target_id: "pt1.a1.p2",
        text: "Confirmed project criterion.",
        status: "confirmed",
      },
    ],
  });
  assert.deepEqual(
    classifyQcRemediation(candidate, true, {
      status: "assumed",
      text: "[TBD: prior value]",
    }),
    {
      bucket: "ready",
      decisionSignals: [
        "the current provision is an unconfirmed assumption",
        "the current provision contains a [TBD]",
      ],
    },
  );
});

test("application digest reports authoritative outcomes and finding context", () => {
  const digest = buildQcApplicationDigest(
    {
      "qc-a": "applied",
      "qc-b": "no_ops",
      "qc-c": "not_open",
      "qc-d": "unknown",
      "qc-e": "future_outcome",
    },
    {
      "qc-a": {
        title: "Correct the service requirement",
        issue: "The existing service requirement contradicts the verified project criterion.",
        severity: "critical",
        element_id: "pt1.a1.p1",
      },
    },
  );

  assert.equal(digest.requested, 5);
  assert.equal(digest.applied, 1);
  assert.equal(digest.skipped, 4);
  assert.deepEqual(digest.reasonCounts, {
    no_ops: 1,
    not_open: 1,
    unknown: 1,
    future_outcome: 1,
  });
  assert.match(digest.text, /1 of 5 selected findings applied; 4 skipped/i);
  assert.match(digest.text, /\[CRITICAL\] Correct the service requirement/);
  assert.match(digest.text, /pt1\.a1\.p1/);
  assert.match(digest.text, /Why: The existing service requirement contradicts/i);
  assert.match(digest.text, /no reviewer-approved, mechanically valid fix/i);
  assert.match(digest.text, /no longer open findings/i);
  assert.match(digest.text, /not found in the retained QC result/i);
  assert.match(digest.text, /`future_outcome`/);
  assert.match(digest.text, /one undoable document version/i);
  assert.match(digest.text, /No paid Final QC rerun was started/i);
});

test("application digest handles empty and large batches without hiding totals", () => {
  const empty = buildQcApplicationDigest({});
  assert.deepEqual(
    {
      requested: empty.requested,
      applied: empty.applied,
      skipped: empty.skipped,
      reasonCounts: empty.reasonCounts,
    },
    { requested: 0, applied: 0, skipped: 0, reasonCounts: {} },
  );
  assert.match(empty.text, /no findings were selected/i);
  assert.doesNotMatch(empty.text, /paid Final QC rerun/i);

  const outcomes: Record<string, string> = {};
  const contexts: Record<
    string,
    { title: string; severity: string; element_id: string }
  > = {};
  for (let index = 1; index <= 14; index += 1) {
    const id = `qc-${index}`;
    outcomes[id] = "applied";
    contexts[id] = {
      title: `Applied finding ${index}`,
      severity: "low",
      element_id: `pt1.a1.p${index}`,
    };
  }
  const large = buildQcApplicationDigest(outcomes, contexts);
  assert.equal(large.applied, 14);
  assert.match(large.text, /Applied finding 12/);
  assert.doesNotMatch(large.text, /Applied finding 13/);
  assert.match(large.text, /and 2 more/i);
});
