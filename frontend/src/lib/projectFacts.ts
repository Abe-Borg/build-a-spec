/**
 * Pure helpers for the "Project facts" panel.
 *
 * Extracted for the `lib/followups.ts` reason: the grouping is what the
 * panel and the model's context block both promise ("project-wide, then
 * discipline-wide, then other sections' coordination facts, then this
 * section's own"), and the retire transition is the piece nothing else in
 * the suite would notice regressing.
 */
import type { ProjectFact } from "../types";

export interface ProjectFactGroups {
  /** Facts that hold for every section of the project. */
  project: ProjectFact[];
  /** Facts that hold for every section of this discipline (or of a
   *  discipline nothing proves foreign: an unbound fact, or a session with
   *  no discipline of its own to compare against). */
  discipline: ProjectFact[];
  /** Discipline-wide facts recorded by ANOTHER discipline of the project —
   *  coordination information, never inputs this section drafts to. */
  otherDisciplines: ProjectFact[];
  /** Coordination facts recorded about OTHER sections of the project. */
  other: ProjectFact[];
  /** Section-scoped facts about the section on screen. */
  own: ProjectFact[];
  /** Retired facts, newest first, with their reasons. */
  superseded: ProjectFact[];
}

const normalize = (value: string) => value.split(/\s+/).filter(Boolean).join(" ");

/** How two discipline names compare: whitespace-folded and case-folded, the
 *  server's `discipline_key`. Anything beyond spelling is the user's naming
 *  and is shown, not guessed at. */
const disciplineKey = (value: string) => normalize(value || "").toLowerCase();

/** The numeric tail of a `pf-N` id — the recording order. */
export function factSeq(pid: string): number {
  const tail = pid.split("-").pop() ?? "";
  const value = Number.parseInt(tail, 10);
  return Number.isFinite(value) ? value : 0;
}

function byStatusThenSeq(a: ProjectFact, b: ProjectFact): number {
  if (a.status !== b.status) return a.status === "confirmed" ? -1 : 1;
  return factSeq(a.pid) - factSeq(b.pid);
}

/**
 * Group the ledger the way the panel draws it and the model reads it.
 *
 * A section-scoped fact belongs to "this section" only when its section
 * number matches the document's; with no section number yet, every
 * section-scoped fact is coordination information from elsewhere. A
 * discipline-scoped fact is another discipline's only when BOTH names are
 * known and differ — an unbound fact, or a session with no discipline of its
 * own, stays discipline-wide because nothing proves it foreign. The same
 * rules `ProjectFactStore.context_block` applies server-side, so the panel
 * and the PROJECT CONTEXT block can never disagree about a fact's group.
 */
export function groupProjectFacts(
  items: readonly ProjectFact[],
  currentSection: string,
  currentDiscipline = "",
): ProjectFactGroups {
  const current = normalize(currentSection || "");
  const discipline = disciplineKey(currentDiscipline);
  const groups: ProjectFactGroups = {
    project: [],
    discipline: [],
    otherDisciplines: [],
    other: [],
    own: [],
    superseded: [],
  };
  for (const fact of items) {
    if (fact.status === "superseded") {
      groups.superseded.push(fact);
    } else if (fact.scope === "discipline") {
      const bound = disciplineKey(fact.discipline || "");
      if (bound && discipline && bound !== discipline) groups.otherDisciplines.push(fact);
      else groups.discipline.push(fact);
    } else if (fact.scope === "section") {
      const section = normalize(fact.section || "");
      if (current && section === current) groups.own.push(fact);
      else groups.other.push(fact);
    } else {
      groups.project.push(fact);
    }
  }
  groups.project.sort(byStatusThenSeq);
  groups.discipline.sort(byStatusThenSeq);
  groups.otherDisciplines.sort(byStatusThenSeq);
  groups.other.sort(byStatusThenSeq);
  groups.own.sort(byStatusThenSeq);
  groups.superseded.sort((a, b) => factSeq(b.pid) - factSeq(a.pid));
  return groups;
}

export function factCounts(items: readonly ProjectFact[]): {
  active: number;
  confirmed: number;
  assumed: number;
  superseded: number;
} {
  let confirmed = 0;
  let assumed = 0;
  let superseded = 0;
  for (const fact of items) {
    if (fact.status === "confirmed") confirmed += 1;
    else if (fact.status === "assumed") assumed += 1;
    else superseded += 1;
  }
  return { active: confirmed + assumed, confirmed, assumed, superseded };
}

/**
 * The ids that just moved from active to superseded.
 *
 * A snapshot diff, not a click: a fact the model retired mid-turn animates
 * exactly like one the user retired from the panel. A first render (no
 * previous list) reports nothing — a restored project's retired history is
 * not a fresh transition.
 */
export function justSupersededIds(
  previous: readonly ProjectFact[] | null,
  next: readonly ProjectFact[],
): Set<string> {
  const moved = new Set<string>();
  if (!previous || previous.length === 0) return moved;
  const before = new Map(previous.map((fact) => [fact.pid, fact.status]));
  for (const fact of next) {
    const was = before.get(fact.pid);
    if (fact.status === "superseded" && was !== undefined && was !== "superseded") {
      moved.add(fact.pid);
    }
  }
  return moved;
}

/** "project · confirmed · recorded in 21 13 13" — the row's provenance line. */
export function factProvenance(fact: ProjectFact): string {
  const scope =
    fact.scope === "section" && fact.section
      ? `section ${fact.section}`
      : fact.scope === "discipline" && fact.discipline
        ? `discipline ${fact.discipline}`
        : fact.scope;
  const parts = [scope, fact.status];
  if (fact.recorded_in) parts.push(`recorded in ${fact.recorded_in}`);
  if (fact.source_kind && fact.source_kind !== "model") {
    parts.push(
      fact.source_ref ? `source: ${fact.source_kind} ${fact.source_ref}` : `source: ${fact.source_kind}`,
    );
  }
  return parts.join(" · ");
}
