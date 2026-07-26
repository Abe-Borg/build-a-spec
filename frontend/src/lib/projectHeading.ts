import type { SpecDoc } from "../types";

const clean = (value?: string) => value?.trim() ?? "";

/** Versioned discipline, with the old session field as a load fallback. */
export function projectDiscipline(
  doc: SpecDoc | null,
  legacyDiscipline?: string,
): string {
  return clean(doc?.project_identity?.discipline) || clean(legacyDiscipline);
}

/**
 * Compact context line beside the app title.
 *
 * The extra context is intentionally all-or-nothing: until facility type and
 * a city/region location are both known, showing only the discipline avoids a
 * misleading half-identified project.
 */
export function formatProjectHeading(
  doc: SpecDoc | null,
  legacyDiscipline?: string,
): string {
  const discipline = projectDiscipline(doc, legacyDiscipline);
  if (!discipline) return "";

  const projectType = clean(doc?.project_identity?.project_type);
  const city = clean(doc?.project_profile?.city);
  const region = clean(doc?.project_profile?.state_or_province);
  if (!projectType || !city || !region) return discipline;

  return [discipline, projectType, city + ", " + region].join(" · ");
}
