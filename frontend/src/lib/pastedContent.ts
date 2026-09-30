/**
 * Pasted text reaches the model marked as pasted (the 5.5 prompting upgrade,
 * P55-8; the Opus 5.5 guide's "Mark pasted text in user messages").
 *
 * A user pastes an owner's email, a code excerpt or another section's wording
 * into the composer, and that text can carry instructions the user did not
 * write. So each paste worth marking is sent inside a pair of tags carrying
 * one random id:
 *
 *     <pasted_content id="3f9a1c2e">
 *     …the pasted text, unchanged…
 *     </pasted_content id="3f9a1c2e">
 *
 * and the stable system prompt tells the model what the tags mean
 * (`_PASTED_CONTENT_POLICY` in backend/llm/prompts.py). The chat never shows
 * the tags: the user bubble renders `stripPastedContentTags(text)`, live and
 * after a reload.
 *
 * A paste is recorded as a RANGE of the message, never as a string to search
 * for later. The same words can already be in the message (typed, or pasted
 * before), and only the occurrence the paste inserted was pasted: wrapping the
 * first match would label the user's own words as pasted and leave the real
 * paste unmarked. Every edit moves the ranges; an edit that reaches into one
 * drops it, and that text is sent untagged — the same as before this module
 * existed, and never worse.
 *
 * Pure: no React, no DOM. The composer only calls these.
 */

/** A paste this long is marked even without a line break (decision D7). */
export const PASTE_MARK_MIN_CHARS = 120;

/** One recorded paste: where it sits in the composer's value, and the exact
 *  text it inserted there. Its end is `start + text.length`. */
export interface PastedRange {
  start: number;
  text: string;
}

/** A paste the clipboard announced but the textarea has not applied yet. */
export type PendingPaste = PastedRange;

const PASTE_ID = /^[0-9a-f]{8}$/;

/** The pair the send writes and the bubble removes. The id is 8 lowercase hex
 *  digits, and the closing tag carries the same id as the opening one. */
const PASTED_PAIR =
  /(\n?)<pasted_content id="([0-9a-f]{8})">\n([\s\S]*?)\n<\/pasted_content id="\2">(\n?)/g;

/** Line endings as a textarea holds them: `\n` only. */
export function foldLineEndings(text: string): string {
  return text.replace(/\r\n?/g, "\n");
}

/** Worth marking: a line break, or at least `PASTE_MARK_MIN_CHARS`
 *  characters (decision D7). Short inline pastes stay plain text. */
export function isWorthMarking(text: string): boolean {
  return text.includes("\n") || text.length >= PASTE_MARK_MIN_CHARS;
}

/** The pending record for a paste at `start` (the selection start when the
 *  paste landed), or null when the clipboard held no text. */
export function pendingPaste(start: number, clipboardText: string): PendingPaste | null {
  const text = foldLineEndings(clipboardText ?? "");
  if (!text || !Number.isInteger(start) || start < 0) return null;
  return { start, text };
}

/**
 * Move every recorded range through one edit of the composer's value.
 *
 * The edit is the span between the two values' common prefix and common
 * suffix (the prefix first, the suffix bounded so the two never overlap). A
 * range that ends at or before the edit's start stays; one that starts at or
 * after the edit's end moves by the length difference; one the edit reaches
 * into — a character deleted inside it, or text inserted strictly between two
 * of its characters — is dropped. So typing right before or right after a
 * paste leaves it whole. A prefix/suffix diff can place an edit later than it
 * really happened; it then drops a range rather than mislabel one.
 */
export function movePastedRanges(
  ranges: readonly PastedRange[],
  oldValue: string,
  newValue: string,
): PastedRange[] {
  if (ranges.length === 0 || oldValue === newValue) return [...ranges];
  const shorter = Math.min(oldValue.length, newValue.length);
  let prefix = 0;
  while (prefix < shorter && oldValue.charCodeAt(prefix) === newValue.charCodeAt(prefix)) {
    prefix += 1;
  }
  let suffix = 0;
  while (
    suffix < shorter - prefix &&
    oldValue.charCodeAt(oldValue.length - 1 - suffix) ===
      newValue.charCodeAt(newValue.length - 1 - suffix)
  ) {
    suffix += 1;
  }
  const editEnd = oldValue.length - suffix;
  const delta = newValue.length - oldValue.length;
  const moved: PastedRange[] = [];
  for (const range of ranges) {
    const end = range.start + range.text.length;
    if (end <= prefix) moved.push(range);
    else if (range.start >= editEnd) moved.push({ start: range.start + delta, text: range.text });
    // Otherwise the edit reached into it: the user changed pasted text.
  }
  return moved;
}

/**
 * Record a pending paste, if the value really holds it and it is worth
 * marking. Call it after `movePastedRanges` for the same change, then clear
 * the pending record either way.
 *
 * A recorded range the new paste overlaps is dropped: the diff can keep a
 * range whose characters now include pasted ones (a paste inside it that
 * repeated its own text), and a range is never kept when it might mislabel.
 */
export function adoptPaste(
  ranges: readonly PastedRange[],
  pending: PendingPaste | null,
  value: string,
): PastedRange[] {
  if (!pending) return [...ranges];
  const end = pending.start + pending.text.length;
  if (!isWorthMarking(pending.text) || value.slice(pending.start, end) !== pending.text) {
    return [...ranges];
  }
  const kept = ranges.filter(
    (range) => range.start + range.text.length <= pending.start || range.start >= end,
  );
  return [...kept, { start: pending.start, text: pending.text }].sort(
    (a, b) => a.start - b.start,
  );
}

/** A fresh paste id: 8 lowercase hex digits from `crypto.getRandomValues`. */
export function randomPasteId(): string {
  const bytes = new Uint8Array(4);
  globalThis.crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

/** An id that is well formed, unused in this message, and found nowhere in
 *  its text — so a closing tag can never occur inside what it closes. Null
 *  when `makeId` cannot supply one; that block is then sent untagged. */
function freshId(makeId: () => string, value: string, used: Set<string>): string | null {
  for (let attempt = 0; attempt < 16; attempt += 1) {
    const id = makeId();
    if (PASTE_ID.test(id) && !used.has(id) && !value.includes(id)) {
      used.add(id);
      return id;
    }
  }
  return null;
}

/**
 * The message as the composer sends it: the value trimmed, with every
 * recorded paste that still matches the value at its position wrapped in its
 * own pair of tags, each tag on its own line.
 *
 * A paste that the trim cuts into keeps only the part the message still
 * holds, and the rule of `isWorthMarking` applies to what is actually wrapped:
 * a paste whose only line break is trailing whitespace the trim removes goes
 * as plain text. Line breaks are added around a block only where it has
 * neighbours (never at the message's very start or end), always exactly one
 * on each such side, so `stripPastedContentTags` gives back the trimmed value
 * character for character.
 */
export function wrapPastedContent(
  value: string,
  ranges: readonly PastedRange[],
  makeId: () => string = randomPasteId,
): string {
  const body = value.trim();
  if (!body || ranges.length === 0) return body;
  const lead = value.length - value.trimStart().length;
  const bodyEnd = lead + body.length;
  const blocks = ranges
    .filter((range) => value.slice(range.start, range.start + range.text.length) === range.text)
    .sort((a, b) => a.start - b.start);
  const used = new Set<string>();
  let out = "";
  let cursor = 0;
  let lastEnd = -1;
  for (const range of blocks) {
    const end = range.start + range.text.length;
    if (range.start < lastEnd) continue; // never two blocks over one character
    const start = Math.max(range.start, lead);
    const stop = Math.min(end, bodyEnd);
    if (stop <= start) continue;
    const content = value.slice(start, stop);
    if (!isWorthMarking(content)) continue;
    const id = freshId(makeId, value, used);
    if (id === null) continue;
    lastEnd = end;
    const from = start - lead;
    const to = stop - lead;
    out +=
      body.slice(cursor, from) +
      (from > 0 ? "\n" : "") +
      `<pasted_content id="${id}">\n${content}\n</pasted_content id="${id}">` +
      (to < body.length ? "\n" : "");
    cursor = to;
  }
  return out + body.slice(cursor);
}

/**
 * The message as the user wrote it: every tag pair the send added, with the
 * one line break it added on each side, removed. Only pairs whose opening and
 * closing ids match are touched; anything else is left as the user wrote it.
 */
export function stripPastedContentTags(text: string): string {
  if (!text.includes("<pasted_content id=")) return text;
  return text.replace(PASTED_PAIR, (_pair, _before, _id, content: string) => content);
}
