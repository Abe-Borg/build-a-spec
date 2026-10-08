/**
 * The "why" chip beside an edited element — one definition for the panel.
 *
 * Every edit the assistant makes carries a brief reason (owner rule,
 * 2026-10-07; the engine refuses a batch without one on every op). The
 * server keeps them per element id on the document (`SpecDoc.edit_reasons`,
 * oldest first, the newest few), and the redline on the original says the
 * same in a Word comment — all but the reasons for a status or source-link
 * change (`SpecDoc.workflow_reasons` on the server), which never reaches
 * Word. The panel shows the whole trail on hover and the
 * newest inline under a block changed this turn. An element no model edit
 * touched — an import, a template starter, the user's own typing — has no
 * trail and shows nothing.
 */

export type EditReasonsById = Readonly<Record<string, string[]>>;

/** The element's reasons, oldest first; blanks and malformed rows dropped. */
export function editReasons(
  reasonsById: EditReasonsById | null | undefined,
  id: string,
): readonly string[] {
  const trail = reasonsById?.[id];
  if (!Array.isArray(trail)) return [];
  return trail.filter(
    (reason): reason is string => typeof reason === "string" && reason.trim() !== "",
  );
}

/** The newest reason, or "" when there is none. */
export function latestReason(reasons: readonly string[]): string {
  return reasons.length ? reasons[reasons.length - 1] : "";
}

/** The chip's `title`: the one reason, or the trail numbered oldest first. */
export function reasonChipTitle(reasons: readonly string[]): string {
  if (!reasons.length) return "";
  if (reasons.length === 1) return `Why this changed: ${reasons[0]}`;
  const lines = reasons.map((reason, index) => `${index + 1}. ${reason}`);
  return `Why this changed (oldest first):\n${lines.join("\n")}`;
}
