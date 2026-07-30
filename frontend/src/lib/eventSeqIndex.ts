/**
 * The sequence index both live followers dedupe replay against.
 *
 * Research and Final QC each follow an append-only, sequence-numbered runner
 * log, and every reconnect replays that log from seq 0. The duplicate test
 * therefore runs once per replayed frame, so it has to be a lookup rather
 * than a scan: asking `maxEventSeq(...)` or `Array.find(...)` per frame is
 * itself O(n), which kept a reconnect quadratic in the log it was recovering.
 *
 * The index is `{min, max, missing}` — the span of sequences present, plus
 * the ones absent from inside it. A log the server produced is dense, so
 * `missing` is empty and can be SHARED by the next index instead of copied;
 * that is what makes the append path O(1) rather than trading one O(n) scan
 * for one O(n) `Set` copy.
 *
 * Memoized per events ARRAY, which is the identity of a log: nothing in this
 * app mutates an event array (every merge and reconcile builds a new one), so
 * an entry can never describe an array that has since changed, and an index
 * derived for a new array leaves the old array's own entry correct. Merging
 * onto an older snapshot is therefore still safe — it costs one rebuild, not
 * a wrong answer.
 *
 * NOTE on how this module is imported: `qcLive.ts` and `researchLive.ts` name
 * it as `"./eventSeqIndex.ts"`, WITH the extension, which is not the house
 * style for sibling imports. It has to be. `npm test` runs `node --test`
 * directly over the `.ts` sources, and Node's ESM resolver requires a real
 * file extension on a relative specifier — the other extensionless sibling
 * imports in `src/lib` are either `import type` (erased before Node sees
 * them) or live in modules no test loads. `allowImportingTsExtensions` in
 * `tsconfig.json` is what lets `tsc --noEmit` accept it; Vite resolves it
 * unchanged. Any future value import between two `src/` modules that a test
 * file loads needs the same extension.
 */

/** What both logs' frames carry. `seq` is absent only on the unlogged stream
 *  sentinel and on legacy frames that predate sequencing. */
interface SequencedFrame {
  seq?: number;
}

export interface EventSeqIndex {
  /** Lowest sequence present, or -1 when the log has no numbered frame. */
  readonly min: number;
  /** Highest sequence present — the watermark. -1 when there is none. */
  readonly max: number;
  /** Sequences missing from inside `(min, max)`. Empty for a dense log. */
  readonly missing: ReadonlySet<number>;
}

const NO_GAPS: ReadonlySet<number> = new Set<number>();

const EMPTY_INDEX: EventSeqIndex = { min: -1, max: -1, missing: NO_GAPS };

const indexes = new WeakMap<object, EventSeqIndex>();

function build(frames: readonly SequencedFrame[]): EventSeqIndex {
  let min = -1;
  let max = -1;
  const present = new Set<number>();
  for (const frame of frames) {
    const seq = frame.seq;
    if (typeof seq !== "number") continue;
    present.add(seq);
    if (min < 0 || seq < min) min = seq;
    if (seq > max) max = seq;
  }
  if (max < 0) return EMPTY_INDEX;
  // Duplicate sequences collapse in the set, so this also detects density on
  // a log that carries the same frame twice.
  if (present.size === max - min + 1) return { min, max, missing: NO_GAPS };
  const missing = new Set<number>();
  for (let seq = min + 1; seq < max; seq += 1) {
    if (!present.has(seq)) missing.add(seq);
  }
  return { min, max, missing };
}

/** The index of a log, built once per array. */
export function eventSeqIndex(
  frames: readonly SequencedFrame[],
): EventSeqIndex {
  const cached = indexes.get(frames);
  if (cached) return cached;
  const built = build(frames);
  indexes.set(frames, built);
  return built;
}

/** Is this sequence already in the log? The direct lookup this module exists
 *  for. A non-finite sequence is never a duplicate — it cannot have been
 *  indexed, so it belongs on the out-of-order path with the unnumbered
 *  frames rather than being silently swallowed as a replay. */
export function hasEventSeq(index: EventSeqIndex, seq: number): boolean {
  if (index.max < 0 || !Number.isFinite(seq)) return false;
  if (seq < index.min || seq > index.max) return false;
  return !index.missing.has(seq);
}

/**
 * Register the index of `frames` — the array formed by appending a frame
 * whose `seq` is beyond `previous.max` — derived from `previous` rather than
 * rebuilt from the array.
 *
 * Deriving is the whole point: the caller has already established where the
 * new sequence sits, so the next duplicate test costs nothing. In the dense
 * case the gap set is shared, not copied.
 */
export function rememberAppendedSeq(
  frames: readonly SequencedFrame[],
  previous: EventSeqIndex,
  seq: number,
): EventSeqIndex {
  let next: EventSeqIndex;
  if (previous.max < 0) {
    next = { min: seq, max: seq, missing: NO_GAPS };
  } else if (seq === previous.max + 1) {
    next = { min: previous.min, max: seq, missing: previous.missing };
  } else {
    // A jump past the watermark leaves a hole behind it, so a later frame
    // filling that hole is a genuine insert and not a replay.
    const missing = new Set(previous.missing);
    for (let absent = previous.max + 1; absent < seq; absent += 1) {
      missing.add(absent);
    }
    next = { min: previous.min, max: seq, missing };
  }
  indexes.set(frames, next);
  return next;
}
