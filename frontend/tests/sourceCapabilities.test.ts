import assert from "node:assert/strict";
import test from "node:test";

import {
  QC_STALE_MESSAGE,
  SOURCE_CAPABILITIES_MISSING_MESSAGE,
  SOURCE_ELEMENT_MISSING_MESSAGE,
  SOURCE_OPERATION_MISSING_MESSAGE,
  qcBatchDecision,
  sourceAllowedPositions,
  sourceCapability,
  sourceCapabilitiesExpected,
  sourceCapabilityTitle,
  sourceEditOpDecision,
} from "../src/lib/sourceCapabilities.ts";
import type {
  SourceCapabilitiesState,
  SourceOperationCapability,
} from "../src/types.ts";

const headingMessage =
  "Source-preserving mode does not patch section, part, or article headings.";
const complexMessage =
  "This paragraph contains a field and cannot be patched safely.";

const headingDenied: SourceOperationCapability = {
  allowed: false,
  blocker: "heading_change",
  message: headingMessage,
};
const complexDenied: SourceOperationCapability = {
  allowed: false,
  blocker: "complex_paragraph",
  message: complexMessage,
};

const report: SourceCapabilitiesState = {
  status: "pass_through_only",
  elements: {
    sec: {
      replace_text: headingDenied,
      set_project_profile: { allowed: true },
      set_standard_edition: { allowed: true },
      set_standard_suppressed: { allowed: true },
    },
    "pt1.a1": {
      replace_text: headingDenied,
      add_paragraph: {
        allowed: true,
        // These extrema are informational and deliberately include a gap.
        minimum_position: 0,
        maximum_position: 5,
        placements: [
          { island_key: "island-a", allowed_positions: [0, 1] },
          { island_key: "island-b", allowed_positions: [4, 5] },
        ],
      },
    },
    "pt1.a1.p1": {
      replace_text: { allowed: true },
      delete: {
        allowed: false,
        blocker: "manual_label_structural_change",
        message: "Manual-label paragraphs cannot be deleted in source-preserving mode.",
      },
      move: {
        allowed: true,
        current_position: 1,
        minimum_position: 0,
        maximum_position: 4,
        allowed_positions: [0, 2, 4],
      },
      add_paragraph: complexDenied,
      set_status: { allowed: true },
      set_provenance: { allowed: true },
    },
    "pt1.a1.p2": {
      replace_text: complexDenied,
      delete: complexDenied,
      move: complexDenied,
      add_paragraph: complexDenied,
      set_status: { allowed: true },
      set_provenance: { allowed: true },
    },
  },
};

test("non-source documents retain normal editing when the report is null", () => {
  assert.equal(
    sourceCapability(null, false, "pt1.a1", "replace_text").allowed,
    true,
  );
  assert.equal(
    sourceEditOpDecision(null, false, {
      action: "future_semantic_operation",
      target_id: "anything",
    }).allowed,
    true,
  );
});

test("active-source detection distinguishes pre-import and legacy source-less history", () => {
  assert.equal(sourceCapabilitiesExpected(null, false, 1, 1), false);
  assert.equal(sourceCapabilitiesExpected(null, true, 1, 0), false);
  assert.equal(sourceCapabilitiesExpected(null, true, 1, 1), true);
  assert.equal(sourceCapabilitiesExpected(null, true, null, 1), false);
  assert.equal(sourceCapabilitiesExpected(report, false, 99, 0), true);
});

test("source-backed documents fail closed when report, element, or op is missing", () => {
  assert.deepEqual(sourceCapability(null, true, "pt1.a1", "replace_text"), {
    allowed: false,
    blocker: "capabilities_unavailable",
    message: SOURCE_CAPABILITIES_MISSING_MESSAGE,
  });
  assert.equal(
    sourceCapability(report, true, "pt9.a9", "replace_text").message,
    SOURCE_ELEMENT_MISSING_MESSAGE,
  );
  assert.equal(
    sourceCapability(report, true, "pt1.a1", "set_status").message,
    SOURCE_OPERATION_MISSING_MESSAGE,
  );
});

test("eligible paragraph text is enabled while an imported heading is denied", () => {
  assert.equal(
    sourceCapability(report, true, "pt1.a1.p1", "replace_text").allowed,
    true,
  );
  const decision = sourceCapability(report, true, "pt1.a1", "replace_text");
  assert.strictEqual(decision, headingDenied);
  assert.equal(sourceCapabilityTitle(decision, "Edit article title"), headingMessage);
});

test("manual-label structural actions can be denied independently of text", () => {
  assert.equal(
    sourceCapability(report, true, "pt1.a1.p1", "replace_text").allowed,
    true,
  );
  assert.equal(
    sourceCapability(report, true, "pt1.a1.p1", "delete").blocker,
    "manual_label_structural_change",
  );
});

test("status and provenance stay enabled while body text is read-only", () => {
  assert.strictEqual(
    sourceCapability(report, true, "pt1.a1.p2", "replace_text"),
    complexDenied,
  );
  assert.equal(
    sourceCapability(report, true, "pt1.a1.p2", "set_status").allowed,
    true,
  );
  assert.equal(
    sourceCapability(report, true, "pt1.a1.p2", "set_provenance").allowed,
    true,
  );
});

test("combined replace wire ops check text, status, and provenance permissions", () => {
  assert.equal(
    sourceEditOpDecision(report, true, {
      action: "replace",
      target_id: "pt1.a1.p1",
      text: "Changed",
      status: "confirmed",
      source_item_id: "r-123",
    }).allowed,
    true,
  );
  assert.strictEqual(
    sourceEditOpDecision(report, true, {
      action: "replace",
      target_id: "pt1.a1.p2",
      text: "Changed",
      status: "confirmed",
    }),
    complexDenied,
  );
});

test("move and add use explicit positions and never expand min/max ranges", () => {
  const move = sourceCapability(report, true, "pt1.a1.p1", "move");
  assert.deepEqual(sourceAllowedPositions(move), [0, 2, 4]);
  assert.equal(
    sourceEditOpDecision(report, true, {
      action: "move",
      target_id: "pt1.a1.p1",
      position: 2,
    }).allowed,
    true,
  );
  assert.equal(
    sourceEditOpDecision(report, true, {
      action: "move",
      target_id: "pt1.a1.p1",
      position: 3,
    }).blocker,
    "capability_position_denied",
  );
  assert.equal(
    sourceEditOpDecision(report, true, {
      action: "move",
      target_id: "pt1.a1.p1",
    }).blocker,
    "capability_position_invalid",
  );

  const add = sourceCapability(report, true, "pt1.a1", "add_paragraph");
  assert.deepEqual(sourceAllowedPositions(add), [0, 1, 4, 5]);
  assert.equal(
    sourceEditOpDecision(report, true, {
      action: "add_paragraph",
      target_id: "pt1.a1",
      text: "New provision",
      position: 4,
    }).allowed,
    true,
  );
  assert.equal(
    sourceEditOpDecision(report, true, {
      action: "add_paragraph",
      target_id: "pt1.a1",
      text: "New provision",
      position: 3,
    }).blocker,
    "capability_position_denied",
  );
  assert.equal(
    sourceEditOpDecision(report, true, {
      action: "add_paragraph",
      target_id: "pt1.a1",
      text: "Implicit append is not safe to infer",
    }).blocker,
    "capability_position_invalid",
  );
});

test("metadata-only QC remains applicable on pass-through-only sources", () => {
  const decision = qcBatchDecision({
    finding: {
      ops_valid: true,
      ops_invalid_reason: "",
      proposed_ops: [
        {
          action: "set_status",
          target_id: "pt1.a1.p2",
          status: "confirmed",
        },
        {
          action: "set_project_profile",
          target_id: "sec",
          city: "Seattle",
        },
      ],
    },
    sourceCapabilities: report,
    sourceExpected: true,
    stale: false,
  });
  assert.equal(decision.allowed, true);
});

test("QC batch decisions preserve server reasons and reject stale results", () => {
  const bodyDenied = qcBatchDecision({
    finding: {
      ops_valid: true,
      ops_invalid_reason: "",
      proposed_ops: [
        { action: "replace", target_id: "pt1.a1.p2", text: "Changed" },
      ],
    },
    sourceCapabilities: report,
    sourceExpected: true,
    stale: false,
  });
  assert.strictEqual(bodyDenied, complexDenied);
  assert.equal(bodyDenied.message, complexMessage);

  const stale = qcBatchDecision({
    finding: {
      ops_valid: true,
      ops_invalid_reason: "",
      proposed_ops: [
        { action: "set_status", target_id: "pt1.a1.p2", status: "confirmed" },
      ],
    },
    sourceCapabilities: report,
    sourceExpected: true,
    stale: true,
  });
  assert.equal(stale.blocker, "qc_stale");
  assert.equal(stale.message, QC_STALE_MESSAGE);

  const invalidReason = "Server dry-run rejected the combined fix.";
  assert.equal(
    qcBatchDecision({
      finding: {
        ops_valid: false,
        ops_invalid_reason: invalidReason,
        proposed_ops: [{ action: "set_status", target_id: "pt1.a1.p2" }],
      },
      sourceCapabilities: report,
      sourceExpected: true,
      stale: false,
    }).message,
    invalidReason,
  );
});
