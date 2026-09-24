/**
 * Developer tools' "Cost self-checks" row (Tier 1 finish, CT-2 and WL-1):
 * what the runtime cost checks have decided, in plain words — the
 * continuation tail one line per engine, then the warm lead.
 *
 * The continuation tail is a cache breakpoint on a research or Final QC
 * request that resumes a paused search (BUILD_A_SPEC_CONTINUATION_CACHE). The
 * backend switches an engine's tail off for the rest of the app session when
 * the provider refuses it (CT-1) or once it has provably cost more than it
 * saved (CT-2), and measures what it saved on the requests it can read.
 *
 * The warm lead streams one seat of a large group of Final QC's batched
 * verifier seats first, so the batch can read the cache entry it wrote
 * (BUILD_A_SPEC_QC_BATCH_WARM_LEAD). After a batched phase that sent one, the
 * backend reads how many of the batched seats read the shared prefix, and
 * switches the lead off for the rest of the app session when the batch did
 * not read its copy or it cost more than it could have saved (WL-1).
 *
 * The reasons, the engines and the warm lead's verdicts are pinned against
 * `backend/cost_checks.py` by `tests/costChecks.test.ts`, so one added on
 * either side cannot drop out of the other. Built for any snapshot: an older
 * backend's, or a malformed one, renders "not reported" rather than
 * throwing; a backend with no warm-lead block renders no warm-lead line.
 */
import type { CostChecksSnapshot } from "../types";

/** The backend's engines (`TAIL_ENGINES`), in its order, with their names. */
export const TAIL_ENGINE_LABELS: ReadonlyArray<readonly [string, string]> = [
  ["research", "research"],
  ["qc", "Final QC"],
];

/** Why a check switched a saving off — the backend's closed vocabulary of
 *  `REASON_*` constants, each in plain words. Past tense, because the
 *  counts beside it keep running: requests already in flight when a latch
 *  is set still report back, so a later estimate can differ from the one
 *  that set it. */
export const CHECK_REASON_TEXT: Readonly<Record<string, string>> = {
  rejected: "the provider rejected it",
  unprofitable: "it had cost more than it saved",
  not_read: "the batch did not read the lead's copy",
};

/** What the warm lead's check concluded about one group of seats, beyond
 *  the two reasons above that switch it off — the backend's
 *  `WARM_LEAD_VERDICTS`, in plain words. */
export const WARM_LEAD_VERDICT_TEXT: Readonly<Record<string, string>> = {
  kept: "kept",
  too_few: "too few to judge",
  not_warm: "not judged: the batch went out before the lead's copy was ready",
};

const NOT_REPORTED = "not reported";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function finite(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** "est. saving $0.0860" or "est. loss $0.2892": a continuation saves
 *  fractions of a cent, so four places. */
function money(usd: number): string {
  return usd < 0
    ? `est. loss $${(-usd).toFixed(4)}`
    : `est. saving $${usd.toFixed(4)}`;
}

/** "7 measured (2 exact, 5 bound), est. saving $0.3100 · 3 not measurable",
 *  "nothing measured yet", or — from a backend that does not measure —
 *  "measurement not reported". */
function measurement(entry: Record<string, unknown>): string {
  const measured = finite(entry.measured);
  const exact = finite(entry.exact);
  const bound = finite(entry.bound);
  const unmeasured = finite(entry.unmeasured);
  const saving = finite(entry.saving_usd);
  if (
    measured === null ||
    exact === null ||
    bound === null ||
    unmeasured === null ||
    saving === null
  ) {
    return "measurement not reported";
  }
  const parts: string[] = [];
  if (measured > 0) {
    parts.push(`${measured} measured (${exact} exact, ${bound} bound), ${money(saving)}`);
  }
  if (unmeasured > 0) parts.push(`${unmeasured} not measurable`);
  return parts.length ? parts.join(" · ") : "nothing measured yet";
}

function engineLine(label: string, entry: Record<string, unknown>): string {
  const head = `Continuation tail, ${label}`;
  const measured = measurement(entry);
  const reason = typeof entry.reason === "string" ? entry.reason : "";
  if (reason || entry.enabled === false) {
    const why = reason
      ? (CHECK_REASON_TEXT[reason] ?? `switched off (${reason})`)
      : "switched off";
    const tail = measured === "nothing measured yet" ? "" : ` · ${measured}`;
    return `${head}: off for this session — ${why}${tail}`;
  }
  return `${head}: on · ${measured}`;
}

/** "12%": a share as a whole percentage. */
function percent(share: number): string {
  return `${Math.round(share * 100)}%`;
}

/** One group's phrase in the warm lead's "last check". */
function lineagePhrase(entry: unknown): string {
  if (!isRecord(entry)) return NOT_REPORTED;
  const seats = finite(entry.seats);
  const measured = finite(entry.measured);
  const kind = typeof entry.kind === "string" && entry.kind ? entry.kind : "";
  const verdict = typeof entry.verdict === "string" ? entry.verdict : "";
  if (seats === null || measured === null || !verdict) return NOT_REPORTED;
  const group = `${seats} ${kind ? `${kind} ` : ""}seats`;
  if (verdict === "not_warm") return `${group} — ${WARM_LEAD_VERDICT_TEXT.not_warm}`;
  const counted = `${group}, ${measured} measured`;
  if (verdict === "too_few") return `${counted} — ${WARM_LEAD_VERDICT_TEXT.too_few}`;
  const share = finite(entry.read_share);
  const read = share === null ? counted : `${counted}, ${percent(share)} read the shared copy`;
  if (verdict === "kept") {
    const breakEven = finite(entry.break_even_read_share);
    return breakEven === null
      ? read
      : `${read}; the lead pays when the batch alone would read under ${percent(breakEven)}`;
  }
  const why = CHECK_REASON_TEXT[verdict] ?? WARM_LEAD_VERDICT_TEXT[verdict] ?? verdict;
  return `${read} — ${why}`;
}

/** The group that set the latch, from the last check: the first judged
 *  with the latch's own reason. */
function latchedLineage(
  entry: Record<string, unknown>,
  reason: string,
): Record<string, unknown> | null {
  const check = entry.last_check;
  if (!isRecord(check) || !Array.isArray(check.lineages)) return null;
  for (const lineage of check.lineages) {
    if (isRecord(lineage) && lineage.verdict === reason) return lineage;
  }
  return null;
}

/** The warm lead's line, or none from a backend that does not report it. */
function warmLeadLines(checks: Record<string, unknown>): string[] {
  if (!("warm_lead" in checks)) return [];
  const entry = checks.warm_lead;
  if (!isRecord(entry)) return ["Warm lead: not reported"];
  if (entry.setting_on === false) return ["Warm lead: switched off in settings"];
  const head = "Warm lead (Final QC)";
  const reason = typeof entry.reason === "string" ? entry.reason : "";
  if (reason || entry.enabled === false) {
    const why = reason
      ? (CHECK_REASON_TEXT[reason] ?? `switched off (${reason})`)
      : "switched off";
    const lineage = reason ? latchedLineage(entry, reason) : null;
    let evidence = "";
    if (lineage && reason === "not_read") {
      const share = finite(lineage.read_share);
      if (share !== null) evidence = ` (${percent(share)})`;
    } else if (lineage && reason === "unprofitable") {
      const cost = finite(lineage.lead_cost_usd);
      if (cost !== null) evidence = ` (lead cost $${cost.toFixed(4)})`;
    }
    return [`${head}: off for this session — ${why}${evidence}`];
  }
  const check = entry.last_check;
  if (check === null || check === undefined) return [`${head}: on · nothing checked yet`];
  if (!isRecord(check) || !Array.isArray(check.lineages) || check.lineages.length === 0) {
    return [`${head}: on · last check not reported`];
  }
  return [`${head}: on · last check: ${check.lineages.map(lineagePhrase).join(" · ")}`];
}

/** The row's lines, never empty and never throwing. */
export function costCheckLines(
  checks: CostChecksSnapshot | null | undefined,
): string[] {
  if (!isRecord(checks)) return [NOT_REPORTED];
  const warm = warmLeadLines(checks);
  const tail = continuationTailLines(checks.continuation_tail);
  if (tail === null) {
    return warm.length ? ["Continuation tail: not reported", ...warm] : [NOT_REPORTED];
  }
  return [...tail, ...warm];
}

/** The continuation tail's lines, or null when they are not reported. */
function continuationTailLines(tail: unknown): string[] | null {
  if (!isRecord(tail)) return null;
  const known = new Set(TAIL_ENGINE_LABELS.map(([engine]) => engine));
  const engines: Array<readonly [string, Record<string, unknown>]> = [
    ...TAIL_ENGINE_LABELS.filter(([engine]) => isRecord(tail[engine])).map(
      ([engine, label]) => [label, tail[engine] as Record<string, unknown>] as const,
    ),
    // An engine a newer backend reports and this build does not know.
    ...Object.entries(tail)
      .filter(([engine, entry]) => !known.has(engine) && isRecord(entry))
      .map(([engine, entry]) => [engine, entry as Record<string, unknown>] as const),
  ];
  if (engines.length === 0) return null;
  // One switch serves both engines: when it is off, say that once.
  if (engines.every(([, entry]) => entry.setting_on === false)) {
    return ["Continuation tail: switched off in settings"];
  }
  return engines.map(([label, entry]) => engineLine(label, entry));
}

