/**
 * A research finding's quoted passage and its condition (the prompt-review
 * follow-up, item 7, 2026-10-13). The worker copies the sentence or two that
 * state a requirement from a page it fetched; the server checks that passage
 * against the fetched text and records the source it was found on
 * (`quote_source`), or nothing when it was not found. A quote that was not
 * found is the research's claim about a page, never shown as the page.
 * Pure helpers, no DOM: `frontend/tests/researchQuote.test.ts`.
 */
import type { ResearchItemView } from "../types";

export interface ResearchQuoteView {
  text: string;
  found: boolean;
  /** The source URL it was found on; "" when not found. */
  source: string;
  label: string;
}

/** Words every surface uses for a quote the check did not find. The
 *  backend's render says the same (`QUOTE_NOT_FOUND_LABEL`). */
export const QUOTE_NOT_FOUND = "Not found on a fetched page";

function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

export function researchQuote(
  item: Pick<ResearchItemView, "quote" | "quote_source">,
): ResearchQuoteView | null {
  const text = (item.quote ?? "").trim();
  if (!text) return null;
  const source = (item.quote_source ?? "").trim();
  const found = source !== "";
  return {
    text,
    found,
    source,
    label: found ? `Found on ${hostOf(source)}` : QUOTE_NOT_FOUND,
  };
}

/** The condition under which the finding governs, or "" when unconditional
 *  (or found before the field existed). */
export function appliesWhen(item: Pick<ResearchItemView, "applies_when">): string {
  return (item.applies_when ?? "").trim();
}
