/**
 * The chat stops doing work per frame — pinned at the source level (no DOM
 * harness; the strandedState.test.ts idiom).
 *
 * Two mechanisms, each a few lines a later edit could quietly undo:
 * `React.memo(MessageBubble)`, which only works while the three props Chat
 * hands it keep a stable identity, and the follow-bottom effect in Chat, which
 * used to read scrollHeight and write scrollTop on every animation frame for
 * the whole turn and now runs only when the content actually grows.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const here = dirname(fileURLToPath(import.meta.url));
const read = (...parts: string[]) =>
  readFileSync(join(here, "..", "src", ...parts), "utf8");

const bubble = read("components", "MessageBubble.tsx");
const chat = read("components", "Chat.tsx");
const app = read("App.tsx");

test("MessageBubble is memoized, and the props that keep the memo effective are stable", () => {
  assert.match(bubble, /export default memo\(MessageBubble\)/);
  assert.doesNotMatch(bubble, /export default function MessageBubble/);
  // One plugin array, not a fresh literal per render.
  assert.match(bubble, /const REMARK_PLUGINS = \[remarkGfm\]/);
  assert.doesNotMatch(bubble, /remarkPlugins=\{\[remarkGfm\]\}/);

  // Chat passes pass-through props only: no inline arrow or object here, or
  // every bubble re-renders on every Chat render regardless of the memo.
  const usage = /<MessageBubble\b[\s\S]*?\/>/.exec(chat)?.[0];
  assert.ok(usage, "Chat renders MessageBubble");
  assert.doesNotMatch(usage, /=\{\(/, "an inline arrow prop defeats the memo");
  assert.doesNotMatch(usage, /=\{\{/, "an inline object prop defeats the memo");
  assert.doesNotMatch(usage, /=\{new /, "a per-render Map/Set prop defeats the memo");
  assert.match(usage, /figuresById=\{figuresById\}/);
  assert.match(usage, /onDeleteFigure=\{onDeleteFigure\}/);

  // And App owns those two with a stable identity.
  assert.match(app, /const figuresById = useMemo\(/);
  assert.match(app, /const onDeleteFigure = useCallback\(/);
});

test("Chat follows the bottom when content grows, not on every animation frame", () => {
  assert.match(chat, /new ResizeObserver\(/);
  assert.doesNotMatch(chat, /requestAnimationFrame\(function follow/);
  assert.doesNotMatch(chat, /requestAnimationFrame\(/, "no per-frame loop in Chat");
  // Both the viewport and the bubble wrapper are observed: growth AND shrink.
  assert.match(chat, /observer\.observe\(el\)/);
  assert.match(chat, /observer\.observe\(contentRef\.current\)/);
  assert.match(chat, /ref=\{contentRef\}/);
  // The reader's hand-off is untouched: pinned within 80 px of the bottom,
  // never yanking scroll from someone reading.
  assert.match(chat, /pinnedRef\.current =\s*\n?\s*el\.scrollHeight - el\.scrollTop - el\.clientHeight < 80/);
  assert.match(chat, /if \(pinnedRef\.current\) el\.scrollTop = el\.scrollHeight/);
  assert.match(chat, /overflowAnchor: "none"/);
  // The bubble wrapper kept its capability anchor (the tour contract reads it).
  assert.match(chat, /data-capability="figure\.create"/);
  // Without ResizeObserver the commit-time pin still runs.
  assert.match(chat, /typeof ResizeObserver === "undefined"/);
});
