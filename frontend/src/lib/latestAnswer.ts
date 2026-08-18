/** "Newest request wins" for state two callers race to write.
 *
 * The update check has two callers — the throttled one at launch and a
 * forced one from Help — and they resolve in whatever order the network
 * decides. A launch check that waits out its 8s manifest timeout can land
 * *after* a forced check the user ran meanwhile, and its stale
 * THROTTLED/ERROR answer would erase the install control the forced one had
 * just produced.
 *
 * Ordering by REQUEST rather than by arrival is what makes that correct in
 * both directions: the forced check is issued second, so it outranks the
 * launch check whichever one comes back first.
 *
 * Extracted from the component so the rule can be pinned directly — the
 * race it prevents is timing-dependent and nothing else in the suite would
 * notice it regressing.
 */
export interface LatestAnswer<T> {
  /** Claim the next rank. Call once per request, at issue time. */
  next(): number;
  /** Apply `value` only if no newer request has already answered. */
  accept(rank: number, value: T, apply: (value: T) => void): boolean;
}

export function createLatestAnswer<T>(): LatestAnswer<T> {
  let issued = 0;
  let applied = 0;
  return {
    next: () => ++issued,
    accept(rank, value, apply) {
      // `<` not `<=`: a request applies its own answer once, and a retry of
      // the same rank is not a thing — ranks are claimed, never reused.
      if (rank < applied) return false;
      applied = rank;
      apply(value);
      return true;
    },
  };
}
