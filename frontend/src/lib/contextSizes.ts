/**
 * Developer tools' "Context makeup" row (Project workspace Phase 5A): the
 * last committed turn's PROJECT CONTEXT block, block by block.
 *
 * That block is rewritten on every turn — a cache WRITE, never a read — so it
 * is what a later section pays for on every message it sends. Its research
 * share is the reading Phase 5's relevance trim is gated on, which is why the
 * row always states it, first, whatever its size.
 */
import type { ContextSizes } from "../types";

/** The blocks after research, with their display labels, in the backend's
 *  declaration order (the tie-break between two blocks of the same size).
 *  Every block `conversation.CONTEXT_SIZE_KEYS` measures has an entry except
 *  research, which leads, and the remainder, which trails — pinned by
 *  `tests/contextSizes.test.ts`, so a block added later cannot silently drop
 *  out of the row. */
export const CONTEXT_BLOCK_LABELS: ReadonlyArray<
  readonly [keyof ContextSizes, string]
> = [
  ["facts", "facts"],
  ["sections", "sections"],
  ["references", "reference stubs"],
  ["document", "document"],
  ["lint", "lint"],
  ["open_items", "open items"],
  ["qc_review", "Final QC review"],
];

/** "~65,712 tokens (est.) · research 38,214 (12 findings trimmed at the cap)
 *  · document 21,402 · … · other 1,406". Same len/4 estimate as the History
 *  makeup row; a block that rendered nothing is left out. */
export function contextMakeup(s: ContextSizes): string {
  const research = s.research
    ? `research ${s.research.toLocaleString()}${
        s.research_dropped_items
          ? ` (${s.research_dropped_items.toLocaleString()} findings trimmed at the cap)`
          : ""
      }`
    : "no research profile";
  const named = CONTEXT_BLOCK_LABELS.map(([key, label]) => [label, s[key]] as const)
    .filter(([, tokens]) => tokens > 0)
    .sort((a, b) => b[1] - a[1])
    .map(([label, tokens]) => `${label} ${tokens.toLocaleString()}`);
  const other = s.other > 0 ? [`other ${s.other.toLocaleString()}`] : [];
  return [`~${s.total.toLocaleString()} tokens (est.)`, research, ...named, ...other].join(" · ");
}
