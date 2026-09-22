/**
 * Pure helpers for the Project panel's write-back controls (Project
 * workspace Phase 3): what Update project brief did, what Pull project
 * changes would bring and did bring, and which lines of a merge report a
 * pull must show.
 *
 * The server owns every sentence about a DIFFERENCE (a profile field, an
 * edition two sections disagree on) — those arrive verbatim in
 * `report.conflicts` / `report.warnings`, and a second wording here would be
 * free to describe a difference the server never found. This module only
 * counts.
 */
import type { MergeReport, PullSummary } from "../types";

function plural(count: number, word: string): string {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

/** "2 facts, 1 research round and 1 document" — empty parts dropped. */
function joinCounts(parts: [number, string][]): string {
  const named = parts.filter(([count]) => count > 0).map(([count, word]) => plural(count, word));
  if (named.length <= 1) return named.join("");
  return `${named.slice(0, -1).join(", ")} and ${named[named.length - 1]}`;
}

/** The one line Update project brief leaves behind. */
export function describeBriefRefresh(report: MergeReport | null, written: boolean): string {
  if (!written || !report) {
    return "The project brief already had everything this section holds.";
  }
  const added = joinCounts([
    [report.facts.added, "fact"],
    [report.research.rounds_added, "research round"],
    [report.references.added, "document"],
  ]);
  const disagreements = report.setup.filter((entry) => entry.kind === "edition").length;
  return (
    (added ? `Project brief updated: added ${added}.` : "Project brief updated.") +
    (disagreements
      ? ` ${plural(disagreements, "edition disagreement")} recorded as project facts to resolve.`
      : "")
  );
}

/** The Pull offer: what the project brief holds that this section lacks. */
export function describePullOffer(summary: PullSummary | null | undefined): string {
  const counted = summary
    ? joinCounts([
        [summary.facts, "fact change"],
        [summary.rounds, "research round"],
        [summary.references, "document"],
      ])
    : "";
  return counted
    ? `Other sections added ${counted} since this section last synced.`
    : "The project brief holds work this section has not pulled yet.";
}

/**
 * The one line a pull leaves behind (the chat marker reuses it). `facts`
 * counts every fact the pull added OR changed in place (an edit, a
 * retirement, a fold) — the same count the offer showed, so it says "fact
 * change" too: a pull that only edited facts is not "nothing new".
 */
export function describePull(installed: {
  rounds: number;
  references: number;
  facts: number;
}): string {
  const brought = joinCounts([
    [installed.facts, "fact change"],
    [installed.rounds, "research round"],
    [installed.references, "document"],
  ]);
  return brought
    ? `Pulled project changes: ${brought}.`
    : "Pulled project changes: nothing new to bring in.";
}

/**
 * What a pull must SHOW rather than apply: the disagreements a person has to
 * resolve, then every other warning — each verbatim from the server, each
 * once. Empty when there is nothing to review.
 */
export function pullNoticeLines(report: MergeReport | null | undefined): string[] {
  if (!report) return [];
  const lines: string[] = [];
  for (const line of [...report.conflicts, ...report.warnings]) {
    if (line && !lines.includes(line)) lines.push(line);
  }
  return lines;
}
