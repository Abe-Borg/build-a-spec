/**
 * Thin client for the server-owned imported-DOCX capability contract.
 *
 * This module translates edit-operation wire names, but never recreates the
 * preservation policy. A source-backed document fails closed when its report,
 * element, or operation entry is missing. Documents without a retained source
 * package keep the ordinary semantic editor behavior.
 */
import type {
  QcFinding,
  SourceCapabilitiesState,
  SourceCapabilityOperation,
  SourceOperationCapability,
} from "../types";

export const SOURCE_CAPABILITIES_MISSING_MESSAGE =
  "Source edit permissions are unavailable. Refresh the document before trying this action.";

export const SOURCE_ELEMENT_MISSING_MESSAGE =
  "This imported-source element has no current server permission record. Refresh the document before trying this action.";

export const SOURCE_OPERATION_MISSING_MESSAGE =
  "The server did not authorize this operation for the imported-source element.";

export const QC_STALE_MESSAGE =
  "The document has changed since this Final QC ran. Re-run Final QC before applying fixes.";

const ALLOWED_WITHOUT_SOURCE: SourceOperationCapability = Object.freeze({
  allowed: true,
});

/** Match the server's active imported-history boundary without freezing legacy JSON. */
export function sourceCapabilitiesExpected(
  report: SourceCapabilitiesState | null,
  sourceAvailable: boolean,
  baselineIndex: number | null,
  versionIndex: number,
): boolean {
  if (report !== null) return true;
  return (
    sourceAvailable &&
    baselineIndex !== null &&
    Number.isInteger(baselineIndex) &&
    Number.isInteger(versionIndex) &&
    versionIndex >= baselineIndex
  );
}

function denied(blocker: string, message: string): SourceOperationCapability {
  return { allowed: false, blocker, message };
}

/**
 * Look up one server decision.
 *
 * `sourceExpected` must describe an active imported history branch (including
 * a blocked report when required source artifacts are missing). A null report
 * is normal for a fresh/source-less or pre-import undo state, but is an unsafe
 * omission while an imported baseline is active.
 */
export function sourceCapability(
  report: SourceCapabilitiesState | null,
  sourceExpected: boolean,
  uid: string,
  operation: SourceCapabilityOperation,
): SourceOperationCapability {
  if (!sourceExpected) return ALLOWED_WITHOUT_SOURCE;
  if (!report) {
    return denied("capabilities_unavailable", SOURCE_CAPABILITIES_MISSING_MESSAGE);
  }
  const element = report.elements[uid];
  if (!element) {
    return denied("capability_element_missing", SOURCE_ELEMENT_MISSING_MESSAGE);
  }
  const capability = element[operation];
  if (!capability) {
    return denied("capability_operation_missing", SOURCE_OPERATION_MISSING_MESSAGE);
  }
  return capability;
}

/** Return the exact server reason for a denial, without adding client prose. */
export function sourceCapabilityTitle(
  capability: SourceOperationCapability,
  allowedTitle: string,
): string {
  return capability.allowed
    ? allowedTitle
    : (capability.message ?? SOURCE_OPERATION_MISSING_MESSAGE);
}

/**
 * Flatten explicit positions from the operation and any disjoint placements.
 * Ranges are intentionally ignored: they are informational, never authority.
 */
export function sourceAllowedPositions(
  capability: SourceOperationCapability,
): number[] {
  const positions = new Set<number>();
  const add = (position: number) => {
    if (Number.isInteger(position) && position >= 0) positions.add(position);
  };
  for (const position of capability.allowed_positions ?? []) add(position);
  for (const placement of capability.placements ?? []) {
    for (const position of placement.allowed_positions ?? []) add(position);
  }
  return [...positions].sort((a, b) => a - b);
}

type WireEditOp = Readonly<Record<string, unknown>>;

function hasOwn(op: WireEditOp, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(op, key);
}

function mappedOperations(
  op: WireEditOp,
): SourceCapabilityOperation[] | null {
  switch (op.action) {
    case "replace": {
      const operations: SourceCapabilityOperation[] = [];
      if (hasOwn(op, "text") || hasOwn(op, "numbering")) {
        operations.push("replace_text");
      }
      if (hasOwn(op, "status")) operations.push("set_status");
      if (hasOwn(op, "source_item_id")) operations.push("set_provenance");
      return operations.length ? operations : null;
    }
    case "delete":
      return ["delete"];
    case "move":
      return ["move"];
    case "add_paragraph":
      // Status/provenance describe the new node and are covered by the server's
      // candidate-add probe; there is no new UID to query before submission.
      return ["add_paragraph"];
    case "set_status":
      return ["set_status"];
    case "set_provenance":
      return ["set_provenance"];
    case "set_project_profile":
      return ["set_project_profile"];
    case "set_standard_edition":
      return ["set_standard_edition"];
    case "set_standard_suppressed":
      return ["set_standard_suppressed"];
    default:
      return null;
  }
}

function invalidWireOperation(message: string): SourceOperationCapability {
  return denied("capability_operation_unrecognized", message);
}

/**
 * Decide whether one already-formed edit op is compatible with the current
 * source report. The backend remains authoritative for the submitted value and
 * the final combined state.
 */
export function sourceEditOpDecision(
  report: SourceCapabilitiesState | null,
  sourceExpected: boolean,
  op: WireEditOp,
): SourceOperationCapability {
  if (!sourceExpected) return ALLOWED_WITHOUT_SOURCE;

  const targetId = typeof op.target_id === "string" ? op.target_id : "";
  if (!targetId) {
    return invalidWireOperation(
      "The imported-source operation has no valid target element.",
    );
  }
  const operations = mappedOperations(op);
  if (!operations) {
    return invalidWireOperation(
      "The imported-source operation is not present in the server capability contract.",
    );
  }

  let allowed: SourceOperationCapability = ALLOWED_WITHOUT_SOURCE;
  for (const operation of operations) {
    const capability = sourceCapability(
      report,
      sourceExpected,
      targetId,
      operation,
    );
    if (!capability.allowed) return capability;
    allowed = capability;
  }

  const action = op.action;
  if (action === "move" || action === "add_paragraph") {
    const position = op.position;
    if (
      typeof position !== "number" ||
      !Number.isInteger(position) ||
      position < 0
    ) {
      return denied(
        "capability_position_invalid",
        "The imported-source operation does not specify a valid sibling position.",
      );
    }
    if (!sourceAllowedPositions(allowed).includes(position)) {
      return denied(
        "capability_position_denied",
        `The server did not authorize sibling position ${position} for this imported-source operation.`,
      );
    }
  }

  return allowed;
}

export interface QcBatchDecisionInput {
  finding: Pick<
    QcFinding,
    "ops_valid" | "ops_invalid_reason" | "proposed_ops"
  >;
  sourceCapabilities: SourceCapabilitiesState | null;
  sourceExpected: boolean;
  stale: boolean;
}

/**
 * Decide whether a finding's complete proposed-op batch may be offered now.
 * Each real application still goes through the server's combined final-state
 * validator; this helper only suppresses actions already known to be invalid.
 */
export function qcBatchDecision({
  finding,
  sourceCapabilities,
  sourceExpected,
  stale,
}: QcBatchDecisionInput): SourceOperationCapability {
  if (stale) return denied("qc_stale", QC_STALE_MESSAGE);
  if (!finding.ops_valid) {
    return denied(
      "qc_ops_invalid",
      finding.ops_invalid_reason || "Final QC did not produce a valid mechanical fix.",
    );
  }
  if (finding.proposed_ops.length === 0) {
    return denied("qc_ops_missing", "Final QC did not produce a mechanical fix.");
  }
  for (const op of finding.proposed_ops) {
    const decision = sourceEditOpDecision(
      sourceCapabilities,
      sourceExpected,
      op,
    );
    if (!decision.allowed) return decision;
  }
  return ALLOWED_WITHOUT_SOURCE;
}
