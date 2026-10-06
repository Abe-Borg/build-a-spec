/**
 * Developer tools' "Context makeup" row (Project workspace Phase 5A): the
 * last committed turn's session context, block by block.
 *
 * Since C1 that context is two blocks. The slow-changing one (the research
 * profile, the project's other sections, the session's description) rides
 * the system prompt behind its own one-hour cache breakpoint, so a turn that
 * finds it unchanged reads it from the cache instead of writing it. The rest
 * (the PROJECT CONTEXT: document, lint, open items, facts, the Final QC
 * review…) is rewritten on every turn — a cache WRITE, never a read. The row
 * leads with the total and how much of it was cached, and marks each block
 * that rides the cached one, so it says what each turn carries and where.
 * Its research share is the reading Phase 5's relevance trim is gated on,
 * which is why the row always states it, first, whatever its size.
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

/** The blocks that ride the cached project block rather than the per-turn
 *  PROJECT CONTEXT — `conversation.CACHED_CONTEXT_BLOCKS`, pinned by
 *  `tests/contextSizes.test.ts`. */
export const CACHED_CONTEXT_BLOCKS: ReadonlySet<keyof ContextSizes> = new Set<
  keyof ContextSizes
>(["research", "sections"]);

const cachedMark = (key: keyof ContextSizes) =>
  CACHED_CONTEXT_BLOCKS.has(key) ? " cached" : "";

/** "~65,712 tokens (est.; ~38,900 cached across turns) · research 38,214
 *  cached (12 findings trimmed at the cap) · document 21,402 · … · other
 *  1,406". Same len/4 estimate as the History makeup row; a block that
 *  rendered nothing is left out, and so is the cached share when no project
 *  block was sent. */
export function contextMakeup(s: ContextSizes): string {
  const lead = s.project_block > 0
    ? `~${s.total.toLocaleString()} tokens (est.; ~${s.project_block.toLocaleString()} cached across turns)`
    : `~${s.total.toLocaleString()} tokens (est.)`;
  const research = s.research
    ? `research ${s.research.toLocaleString()}${cachedMark("research")}${
        s.research_dropped_items
          ? ` (${s.research_dropped_items.toLocaleString()} findings trimmed at the cap)`
          : ""
      }`
    : "no research profile";
  const named = CONTEXT_BLOCK_LABELS.map(([key, label]) => [key, label, s[key]] as const)
    .filter(([, , tokens]) => tokens > 0)
    .sort((a, b) => b[2] - a[2])
    .map(([key, label, tokens]) => `${label} ${tokens.toLocaleString()}${cachedMark(key)}`);
  const other = s.other > 0 ? [`other ${s.other.toLocaleString()}`] : [];
  return [lead, research, ...named, ...other].join(" · ");
}
