import type { BlockStatus, SpecDoc } from "../types";
import type { QcReportFinding } from "./qcReport";

/**
 * Pure helpers for the compact remediation queue. The backend remains the
 * authority on whether an operation can actually be applied; these helpers
 * only organize that answer into a useful human workflow.
 */

export type QcRemediationBucket =
  | "ready"
  | "needs_decision"
  | "manual_review";

export interface QcDecisionContext {
  status?: BlockStatus;
  text?: string;
}

export interface QcRemediationClassification {
  bucket: QcRemediationBucket;
  decisionSignals: string[];
}

/** Index current paragraph state so decision triage can prefer structured
 * document provenance over language inferred from a finding. */
export function qcDecisionContextByElement(
  doc: SpecDoc | null,
): Map<string, QcDecisionContext> {
  const contexts = new Map<string, QcDecisionContext>();
  if (!doc) return contexts;

  const visit = (paragraphs: SpecDoc["parts"][number]["articles"][number]["paragraphs"]) => {
    for (const paragraph of paragraphs) {
      contexts.set(paragraph.id, {
        status: paragraph.status,
        text: paragraph.text,
      });
      visit(paragraph.children);
    }
  };

  for (const part of doc.parts) {
    for (const article of part.articles) visit(article.paragraphs);
  }
  return contexts;
}

const TBD_RE = /\[\s*TBD(?:\s*:|\s*\])/i;

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/**
 * Return narrowly scoped signals that a non-executable finding needs project
 * knowledge. Current paragraph status is strongest; explicit operation state
 * is the only fallback because the QC finding schema has no dedicated
 * `requires_project_input` field. Deliberately do not infer this from ordinary
 * title/issue prose, where words such as "assumption" are too ambiguous.
 */
export function qcFindingDecisionSignals(
  finding: QcReportFinding,
  context: QcDecisionContext = {},
): string[] {
  const signals: string[] = [];
  const add = (signal: string) => {
    if (!signals.includes(signal)) signals.push(signal);
  };

  if (context.status === "needs_input") {
    add("the current provision is marked needs input");
  } else if (context.status === "assumed") {
    add("the current provision is an unconfirmed assumption");
  }
  if (TBD_RE.test(context.text ?? "")) {
    add("the current provision contains a [TBD]");
  }

  for (const operation of finding.proposed_ops) {
    const proposedStatus = stringValue(operation.status).trim().toLowerCase();
    if (proposedStatus === "needs_input") {
      add("the proposed change would remain marked needs input");
    } else if (proposedStatus === "assumed") {
      add("the proposed change would remain an unconfirmed assumption");
    }
    if (TBD_RE.test(stringValue(operation.text))) {
      add("the proposed change contains a [TBD]");
    }
  }

  return signals;
}

/** Ready always wins: an approved/current operation can do the drafting even
 * when it resolves an assumption. Only non-actionable findings are split into
 * project decisions versus professional manual review. */
export function classifyQcRemediation(
  finding: QcReportFinding,
  actionable: boolean,
  context: QcDecisionContext = {},
): QcRemediationClassification {
  const decisionSignals = qcFindingDecisionSignals(finding, context);
  if (actionable) return { bucket: "ready", decisionSignals };
  if (decisionSignals.length > 0) {
    return { bucket: "needs_decision", decisionSignals };
  }
  return { bucket: "manual_review", decisionSignals };
}

export interface QcApplicationDigest {
  requested: number;
  applied: number;
  skipped: number;
  reasonCounts: Record<string, number>;
  text: string;
}

export interface QcApplicationFindingContext {
  title: string;
  issue?: string;
  severity: string;
  element_id: string;
}

const OUTCOME_REASONS: Record<string, string> = {
  already_applied: "were already applied",
  no_ops: "had no reviewer-approved, mechanically valid fix",
  stale: "no longer applied cleanly in the combined batch",
  unknown: "were not found in the retained QC result",
  not_open: "were no longer open findings",
};

function countLabel(count: number, singular: string, plural = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : plural}`;
}

function conciseReason(value: string | undefined, limit = 180): string {
  const normalized = (value ?? "").replace(/\s+/g, " ").trim();
  if (normalized.length <= limit) return normalized;
  return `${normalized.slice(0, limit - 1).trimEnd()}…`;
}

/** Build the user-facing receipt from authoritative backend outcomes. */
export function buildQcApplicationDigest(
  outcomes: Record<string, string>,
  findings: Record<string, QcApplicationFindingContext> = {},
): QcApplicationDigest {
  const values = Object.values(outcomes);
  const requested = values.length;
  const applied = values.filter((outcome) => outcome === "applied").length;
  const skipped = requested - applied;
  const reasonCounts: Record<string, number> = {};
  for (const outcome of values) {
    if (outcome === "applied") continue;
    reasonCounts[outcome] = (reasonCounts[outcome] ?? 0) + 1;
  }

  if (requested === 0) {
    return {
      requested,
      applied,
      skipped,
      reasonCounts,
      text: "Final QC improvements: no findings were selected, so the specification was not changed.",
    };
  }

  const lines = [
    `Final QC improvements: **${applied} of ${requested} selected ${requested === 1 ? "finding" : "findings"} applied; ${skipped} skipped.**`,
  ];
  if (applied > 0) {
    lines.push(
      "The compatible, reviewer-approved edits were applied in one undoable document version.",
    );
    const appliedIds = Object.entries(outcomes)
      .filter(([, outcome]) => outcome === "applied")
      .map(([findingId]) => findingId);
    const visibleApplied = appliedIds.slice(0, 12);
    lines.push("", "Applied from the review:");
    for (const findingId of visibleApplied) {
      const finding = findings[findingId];
      if (!finding) {
        lines.push(`- ${findingId}`);
        continue;
      }
      const severity = finding.severity
        ? `[${finding.severity.toUpperCase()}] `
        : "";
      const element = finding.element_id ? ` — ${finding.element_id}` : "";
      const reason = conciseReason(finding.issue);
      lines.push(
        `- ${severity}${finding.title}${element}${reason ? ` — Why: ${reason}` : ""}`,
      );
    }
    if (appliedIds.length > visibleApplied.length) {
      lines.push(
        `- …and ${appliedIds.length - visibleApplied.length} more (see the full report).`,
      );
    }
  } else {
    lines.push("The specification was not changed.");
  }

  if (skipped > 0) {
    lines.push("", "Why items were skipped:");
    for (const [outcome, count] of Object.entries(reasonCounts)) {
      const explanation =
        OUTCOME_REASONS[outcome] ?? `returned the server outcome \`${outcome}\``;
      lines.push(`- ${countLabel(count, "finding")} ${explanation}.`);
    }
  }

  lines.push(
    "",
    "The full per-finding disposition remains in the Final QC report.",
  );
  if (applied > 0) {
    lines.push(
      "Because the specification changed, this QC snapshot is now stale. No paid Final QC rerun was started; run it explicitly when the remaining decisions are resolved.",
    );
  }

  return { requested, applied, skipped, reasonCounts, text: lines.join("\n") };
}
