/**
 * The review queue (Batch 3, WI2): a document-order walk over the blocks that
 * need a human decision — `imported` blocks after a master import, `assumed`
 * blocks after drafting.
 *
 * A pure function of the doc snapshot the app already holds, so it needs no
 * backend query and no drawer-local list to drift out of sync: it recomputes
 * from every fresh doc payload (undo, model edits, resets all flow through).
 * It is a straight port of the backend `iter_paragraphs` document-order
 * contract (parts → articles → paragraphs, depth-first), pinned by
 * `test_iter_paragraphs_document_order_is_the_review_queue_contract`.
 */
import type { DocParagraph, SpecDoc } from "../types";

/** Which outstanding blocks the walk surfaces. */
export type ReviewMode = "all" | "imported" | "assumptions";

/** The statuses a reviewer walks. `confirmed` / `needs_input` are not here:
 *  confirmed is done, needs_input is an open item (answered via the chat). */
const REVIEWABLE = new Set(["imported", "assumed"]);

export interface QueueEntry {
  /** The paragraph's stable element id (the mutation target). */
  elementId: string;
  /** The owning article's element id — the "confirm remaining" grouping key. */
  articleId: string;
  /** Human numbering path, e.g. `1.2.A.1` (matches the backend `ref`). */
  ref: string;
  /** The owning article's title, for context in the drawer. */
  articleTitle: string;
  text: string;
  status: DocParagraph["status"];
  sourceItemId: string;
}

/** Strip trailing SectionFormat punctuation from a label: `A.`→`A`, `1)`→`1`. */
function stripLabel(label: string): string {
  return label.replace(/[.)]+$/, "");
}

/** Walk one article's paragraph tree depth-first, collecting reviewables. */
function walkParagraphs(
  paragraphs: DocParagraph[],
  prefix: string,
  articleId: string,
  articleTitle: string,
  out: QueueEntry[],
): void {
  for (const p of paragraphs) {
    const ref = `${prefix}.${stripLabel(p.label)}`;
    if (REVIEWABLE.has(p.status)) {
      out.push({
        elementId: p.id,
        articleId,
        ref,
        articleTitle,
        text: p.text,
        status: p.status,
        sourceItemId: p.source_item_id,
      });
    }
    walkParagraphs(p.children, ref, articleId, articleTitle, out);
  }
}

/**
 * Build the review queue for `doc` in `mode`. `imported` / `assumptions`
 * filter to that one status; `all` lists imported blocks first, then assumed,
 * each in document order — the order the export's schedules use.
 */
export function buildQueue(doc: SpecDoc | null, mode: ReviewMode): QueueEntry[] {
  if (!doc) return [];
  const all: QueueEntry[] = [];
  for (const part of doc.parts) {
    for (const article of part.articles) {
      walkParagraphs(
        article.paragraphs,
        article.number,
        article.id,
        article.title,
        all,
      );
    }
  }
  if (mode === "imported") return all.filter((e) => e.status === "imported");
  if (mode === "assumptions") return all.filter((e) => e.status === "assumed");
  return [
    ...all.filter((e) => e.status === "imported"),
    ...all.filter((e) => e.status === "assumed"),
  ];
}

/** Outstanding-review counts by kind (the panel badge + the export schedules). */
export function reviewCounts(doc: SpecDoc | null): {
  imported: number;
  assumed: number;
  total: number;
} {
  const all = buildQueue(doc, "all");
  const imported = all.filter((e) => e.status === "imported").length;
  const assumed = all.filter((e) => e.status === "assumed").length;
  return { imported, assumed, total: all.length };
}
