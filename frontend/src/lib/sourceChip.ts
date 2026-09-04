/**
 * The ◆ provenance chip's tooltip — one definition, two renderers.
 *
 * A provision's `source_item_id` names what it came from, and there are now
 * three kinds of origin: a grounded research item (`r-…`), a reference
 * document the user attached (`ref-…`), and an established project fact
 * (`pf-…`, v1.17.0). All are shown by the same chip, so the wording has to
 * come from one place — `SpecDocument` and `ReviewDrawer` previously
 * hardcoded "Research:" each, which would have labelled an owner standard as
 * a research finding.
 *
 * Kind is decided by the id prefix because that is what the backend mints:
 * `ReferenceDocStore.add` issues `ref-<n>`, `ProjectFactStore` issues
 * `pf-<n>`, and research item ids are content hashes prefixed `r-`. The
 * `ref-` test therefore has to run BEFORE the research fallback — `r-` is a
 * prefix of `ref-`; `pf-` shares no prefix with either, so its position is
 * only a matter of reading order.
 */

export type SourceKind = "reference" | "fact" | "research";

export function sourceKind(itemId: string): SourceKind {
  if (itemId.startsWith("ref-")) return "reference";
  if (itemId.startsWith("pf-")) return "fact";
  return "research";
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
  const kind = sourceKind(itemId);
  if (kind === "reference") {
    return detail
      ? `Reference document: ${detail}`
      : `Reference document ${itemId} (no longer attached)`;
  }
  if (kind === "fact") {
    return detail
      ? `Project fact: ${detail}`
      : `Project fact ${itemId} (no longer recorded)`;
  }
  return detail
    ? `Research: ${detail}`
    : `Research item ${itemId} (re-run research to see details)`;
}
