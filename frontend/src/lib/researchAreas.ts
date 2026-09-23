/**
 * The research drawer's area choice: what the user has picked for the next
 * round, and what pressing Research again therefore runs.
 *
 * Choosing registers; it never starts anything. The one control that starts
 * a round from a choice is Research again, and this is the one place that
 * decides what it sends — so the button's label, its tooltip and the request
 * cannot describe three different rounds. Pure: no React, no I/O; pinned by
 * tests/researchAreas.test.ts.
 */
import type { ResearchAreaView } from "../types";

/** The picked areas that still exist, in module declaration order. A choice
 *  outlives the poll that drew the picker, and the module can move under
 *  it: an id no longer declared drops out here rather than reaching a
 *  server that would refuse the whole round by naming it. */
export function chosenResearchAreas(
  areas: readonly ResearchAreaView[],
  picked: readonly string[],
): ResearchAreaView[] {
  const wanted = new Set(picked);
  return areas.filter((area) => wanted.has(area.dimension_id));
}

/** What Research again sends. */
export type ResearchAgainPlan =
  | { scope: "all" }
  | { scope: "selected"; dimensionIds: string[]; titles: string[] };

/** Nothing chosen, or every area chosen, is a full round: "selected" with
 *  all of them would run the same areas under a label that says otherwise.
 *  Only a true subset is sent as a selection, in module order. */
export function researchAgainPlan(
  areas: readonly ResearchAreaView[],
  picked: readonly string[],
): ResearchAgainPlan {
  const chosen = chosenResearchAreas(areas, picked);
  if (chosen.length === 0 || chosen.length === areas.length) {
    return { scope: "all" };
  }
  return {
    scope: "selected",
    dimensionIds: chosen.map((area) => area.dimension_id),
    titles: chosen.map((area) => area.title || area.dimension_id),
  };
}

/** Tick or untick one area. Order is irrelevant — the plan takes the
 *  module's — so a toggle only ever adds or removes that one id. */
export function toggleResearchArea(
  picked: readonly string[],
  dimensionId: string,
): string[] {
  return picked.includes(dimensionId)
    ? picked.filter((id) => id !== dimensionId)
    : [...picked, dimensionId];
}
