/**
 * Developer tools' "Cost self-checks" row (Tier 1 finish, CT-2): what the
 * runtime cost checks have decided about the continuation tail, one line per
 * engine, in plain words.
 *
 * The continuation tail is a cache breakpoint on a research or Final QC
 * request that resumes a paused search (BUILD_A_SPEC_CONTINUATION_CACHE). The
 * backend switches an engine's tail off for the rest of the app session when
 * the provider refuses it (CT-1) or once it has provably cost more than it
 * saved (CT-2), and measures what it saved on the requests it can read. The
 * reasons and the engines are pinned against `backend/cost_checks.py` by
 * `tests/costChecks.test.ts`, so one added on either side cannot drop out of
 * the other. Built for any snapshot: an older backend's, or a malformed one,
 * renders "not reported" rather than throwing.
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

/** The row's lines, never empty and never throwing. */
export function costCheckLines(
  checks: CostChecksSnapshot | null | undefined,
): string[] {
  const tail = isRecord(checks) ? checks.continuation_tail : undefined;
  if (!isRecord(tail)) return [NOT_REPORTED];
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
  if (engines.length === 0) return [NOT_REPORTED];
  // One switch serves both engines: when it is off, say that once.
  if (engines.every(([, entry]) => entry.setting_on === false)) {
    return ["Continuation tail: switched off in settings"];
  }
  return engines.map(([label, entry]) => engineLine(label, entry));
}

