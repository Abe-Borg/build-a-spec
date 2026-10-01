// On-screen receipt lines for a project brief. The brief file, the seed and
// the model context keep every word; these formatters only decide what a
// glance at the dialog shows.

import type { ProjectBriefManifest } from "../types";

type Research = NonNullable<ProjectBriefManifest["research"]>;
type Facts = ProjectBriefManifest["facts"];

/** Designation and year. The adoption-basis essay starts at the first " (". */
export function editionDesignation(standard: string): string {
  const cut = standard.indexOf(" (");
  return (cut === -1 ? standard : standard.slice(0, cut)).trim();
}

/** "NFPA 13 — 2019; NFPA 14 — 2022 (2)". Empty when nothing names itself. */
export function editionsReceipt(standards: readonly string[]): string {
  const names = standards
    .map(editionDesignation)
    .filter((name) => name.length > 0);
  if (names.length === 0) return "";
  return `${names.join("; ")} (${names.length})`;
}

function count(n: number, singular: string, plural = `${singular}s`): string {
  return `${n} ${n === 1 ? singular : plural}`;
}

/** "154 findings, 117 grounded, 4 of 4 areas, 1 round". */
export function researchReceipt(research: Research): string {
  const declared = research.dimensions_declared ?? research.dimensions_recorded;
  return [
    count(research.items, "finding"),
    `${research.grounded} grounded`,
    `${research.dimensions_completed} of ${declared} ${
      declared === 1 ? "area" : "areas"
    }`,
    count(research.rounds, "round"),
  ].join(", ");
}

/** "11 active, 10 confirmed, 1 assumed, 1 retired" — zeros included. */
export function factsReceipt(facts: Facts): string {
  return [
    `${facts.active} active`,
    `${facts.confirmed} confirmed`,
    `${facts.assumed} assumed`,
    `${facts.superseded} retired`,
  ].join(", ");
}
