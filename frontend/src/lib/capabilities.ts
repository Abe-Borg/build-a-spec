/**
 * Stable, end-user capability ids.
 *
 * UI controls use these ids through `data-capability`; the tutorial manifest
 * references the same ids.  Keeping the vocabulary in one place turns the
 * tour into a coverage contract instead of a second, inevitably stale list of
 * product claims.
 */
export const END_USER_CAPABILITIES = [
  "tour.workspace",
  "tour.controls",
  "tour.finish",
  "session.api-key",
  "session.developer-tools",
  "session.identity",
  "session.starters",
  "template.create",
  "template.start",
  "template.import",
  "template.manage",
  "chat.interview",
  "chat.suggestions",
  "chat.streaming",
  "chat.stop",
  "chat.thinking",
  "chat.full-draft",
  "chat.web-verify",
  "document.structure",
  "document.provenance",
  "document.section-header",
  "document.first-article",
  "document.insert",
  "document.edit",
  "document.rearrange",
  "document.source-permissions",
  "document.open-items",
  "document.lint",
  "standards.basis",
  "standards.manage",
  "research.profile",
  "research.run",
  "research.stop",
  "research.report",
  "research.apply",
  "history.undo-redo",
  "review.queue",
  "review.actions",
  "history.compare",
  "figure.create",
  "figure.manage",
  "import.master",
  "import.source-output",
  "reference.attach",
  "reference.use",
  "qc.preflight",
  "qc.run",
  "qc.stop",
  "qc.findings",
  "qc.actions",
  "qc.remediation",
  "qc.report",
  "readiness.checklist",
  "export.clean",
  "export.redline-source",
  "project.save-open",
  "usage.details",
  "help.topics",
  "help.trust",
  "updates.manage",
  "session.unsaved-gates",
] as const;

export type EndUserCapabilityId = (typeof END_USER_CAPABILITIES)[number];

/** Typed helper for React's data-capability attribute. */
export const capability = <T extends EndUserCapabilityId>(id: T): T => id;
