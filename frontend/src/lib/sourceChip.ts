/**
 * The ◆ provenance chip's tooltip — one definition, two renderers.
 *
 * A provision's `source_item_id` names what it came from, and there are now
 * two kinds of origin: a grounded research item (`r-…`) and a reference
 * document the user attached (`ref-…`). Both are shown by the same chip, so
 * the wording has to come from one place — `SpecDocument` and `ReviewDrawer`
 * previously hardcoded "Research:" each, which would have labelled an owner
 * standard as a research finding.
 *
 * Kind is decided by the id prefix because that is what the backend mints:
 * `ReferenceDocStore.add` issues `ref-<n>` and research item ids are content
 * hashes prefixed `r-`. The `ref-` test therefore has to run FIRST — `r-` is
 * a prefix of `ref-`.
 */

export type SourceKind = "reference" | "research";

export function sourceKind(itemId: string): SourceKind {
  return itemId.startsWith("ref-") ? "reference" : "research";
}

/**
 * The chip's `title`, whether or not the origin is still resolvable.
 *
 * `lookup` misses when research has been re-run (item ids are re-minted) or
 * a reference has been detached, so the fallback names the kind and the id
 * rather than pretending the citation is broken — the tag is advisory and
 * has always been allowed to outlive what it points at.
 */
export function sourceChipTitle(
  itemId: string,
  lookup: ReadonlyMap<string, string>,
): string {
  const detail = lookup.get(itemId);
  if (sourceKind(itemId) === "reference") {
    return detail
      ? `Reference document: ${detail}`
      : `Reference document ${itemId} (no longer attached)`;
  }
  return detail
    ? `Research: ${detail}`
    : `Research item ${itemId} (re-run research to see details)`;
}
