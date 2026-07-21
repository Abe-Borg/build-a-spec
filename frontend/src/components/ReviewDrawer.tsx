/**
 * The review queue (Batch 3, WI2): a guided, keyboard-speed walk over every
 * block that needs a human decision — `imported` blocks after a master
 * import, `assumed` blocks after drafting. One element at a time, with
 * keep / edit / delete / ask-the-model / skip actions and a press-and-hold
 * "confirm the rest of this article" affordance.
 *
 * The queue derives from the doc snapshot the app already holds
 * (`buildQueue`) and recomputes from every fresh doc payload, so it survives
 * undo, model edits, and resets with no drawer-owned copy to drift. All
 * mutations go through the Batch 2 `POST /api/doc/edit` surface (via
 * `onEditDoc`); "ask the model" goes through the normal chat channel
 * (`onAskModel`). Mirrors the busy lockout of the paper's inline editing.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { EditOp, SpecDoc } from "../types";
import { buildQueue, reviewCounts, type ReviewMode } from "../lib/reviewQueue";

interface Props {
  doc: SpecDoc | null;
  sourceLookup: ReadonlyMap<string, string>;
  busy: boolean;
  onEditDoc: (ops: EditOp[]) => void;
  onAskModel: (text: string) => void;
  onJump: (elementId: string) => void;
}

const HOLD_MS = 800;

const badgeStyle: Record<string, string> = {
  assumed: "border-warn/50 bg-warn/15 text-warn",
  imported: "border-[#5b7db8]/60 bg-[#5b7db8]/15 text-[#7a90b8]",
};

const modeLabel: Record<ReviewMode, string> = {
  all: "All",
  imported: "Imported",
  assumptions: "Assumed",
};

export default function ReviewDrawer({
  doc,
  sourceLookup,
  busy,
  onEditDoc,
  onAskModel,
  onJump,
}: Props) {
  const [expanded, setExpanded] = useState(false);
  const [mode, setMode] = useState<ReviewMode>("all");
  const [cursor, setCursor] = useState(0);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [holding, setHolding] = useState(false);
  const [tally, setTally] = useState({ confirmed: 0, edited: 0, deleted: 0 });

  const walkerRef = useRef<HTMLDivElement>(null);
  const holdTimer = useRef<number | undefined>(undefined);

  const counts = reviewCounts(doc);
  const queue = useMemo(() => buildQueue(doc, mode), [doc, mode]);
  const safeCursor = queue.length ? Math.min(cursor, queue.length - 1) : 0;
  const current = queue[safeCursor];
  const docEmpty = !doc || doc.parts.every((p) => p.articles.length === 0);

  const focusWalker = useCallback(() => {
    requestAnimationFrame(() => walkerRef.current?.focus());
  }, []);

  // Focus the walker when the drawer opens so the keyboard flow works at once.
  useEffect(() => {
    if (expanded && queue.length) focusWalker();
  }, [expanded, queue.length, focusWalker]);

  // A fresh/emptied document (new session, empty project load) resets the
  // session tallies and collapses the drawer — no stale "reviewed" panel.
  useEffect(() => {
    if (docEmpty) {
      setTally({ confirmed: 0, edited: 0, deleted: 0 });
      setCursor(0);
      setExpanded(false);
    }
  }, [docEmpty]);

  useEffect(() => () => window.clearTimeout(holdTimer.current), []);

  const changeMode = (next: ReviewMode) => {
    setMode(next);
    setCursor(0);
    setEditing(false);
    focusWalker();
  };

  // --- Actions (all no-ops while a model turn owns the tree) ---------------

  const confirm = useCallback(() => {
    if (busy || !current) return;
    onEditDoc([
      { action: "set_status", target_id: current.elementId, status: "confirmed" },
    ]);
    setTally((t) => ({ ...t, confirmed: t.confirmed + 1 }));
    setEditing(false);
    focusWalker();
  }, [busy, current, onEditDoc, focusWalker]);

  const remove = useCallback(() => {
    if (busy || !current) return;
    onEditDoc([{ action: "delete", target_id: current.elementId }]);
    setTally((t) => ({ ...t, deleted: t.deleted + 1 }));
    setEditing(false);
    focusWalker();
  }, [busy, current, onEditDoc, focusWalker]);

  const startEdit = useCallback(() => {
    if (busy || !current) return;
    setDraft(current.text);
    setEditing(true);
  }, [busy, current]);

  const saveEdit = () => {
    // A model turn owns the tree: the backend would 409 this edit. Keep the
    // draft open (don't clear edit mode, don't count it) so nothing is lost —
    // the user saves once the turn finishes.
    if (busy) return;
    const text = draft.trim();
    setEditing(false);
    if (!current || !text) {
      focusWalker();
      return;
    }
    if (text !== current.text) {
      onEditDoc([
        {
          action: "replace",
          target_id: current.elementId,
          text,
          status: "confirmed",
          source_item_id: current.sourceItemId,
        },
      ]);
      setTally((t) => ({ ...t, edited: t.edited + 1 }));
    }
    focusWalker();
  };

  const ask = useCallback(() => {
    if (busy || !current) return;
    const snippet =
      current.text.length > 80
        ? `${current.text.slice(0, 80).trimEnd()}…`
        : current.text;
    onAskModel(`Regarding ${current.ref} "${snippet}": `);
    setEditing(false);
  }, [busy, current, onAskModel]);

  const skip = useCallback(() => {
    setEditing(false);
    setCursor((c) => Math.min(c + 1, queue.length - 1));
    focusWalker();
  }, [queue.length, focusWalker]);

  const prev = useCallback(() => {
    setEditing(false);
    setCursor((c) => Math.max(c - 1, 0));
    focusWalker();
  }, [focusWalker]);

  // Outstanding blocks in the current entry's article (this mode).
  const articleGroup = current
    ? queue.filter((e) => e.articleId === current.articleId)
    : [];

  const confirmArticle = () => {
    if (busy || articleGroup.length === 0) return;
    onEditDoc(
      articleGroup.map((e) => ({
        action: "set_status" as const,
        target_id: e.elementId,
        status: "confirmed" as const,
      })),
    );
    setTally((t) => ({ ...t, confirmed: t.confirmed + articleGroup.length }));
    setEditing(false);
    focusWalker();
  };

  const startHold = () => {
    if (busy || articleGroup.length < 2) return;
    setHolding(true);
    holdTimer.current = window.setTimeout(() => {
      setHolding(false);
      confirmArticle();
    }, HOLD_MS);
  };
  const cancelHold = () => {
    setHolding(false);
    window.clearTimeout(holdTimer.current);
  };

  // --- Keyboard on the walker container ------------------------------------

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (editing) return; // the edit textarea owns its keys
    const tag = (e.target as HTMLElement).tagName;
    if (tag === "BUTTON" || tag === "TEXTAREA" || tag === "INPUT") return;
    const k = e.key;
    if (k === "k" || k === "Enter") {
      e.preventDefault();
      confirm();
    } else if (k === "e") {
      e.preventDefault();
      startEdit();
    } else if (k === "d") {
      e.preventDefault();
      remove();
    } else if (k === "a") {
      e.preventDefault();
      ask();
    } else if (k === "s" || k === "ArrowRight") {
      e.preventDefault();
      skip();
    } else if (k === "ArrowLeft") {
      e.preventDefault();
      prev();
    }
  };

  // The bar hides until there is something to review (or a session tally to
  // show after finishing) — no noise on a blank document.
  const touched = tally.confirmed + tally.edited + tally.deleted;
  if (counts.total === 0 && touched === 0) return null;

  const barSummary =
    counts.total === 0
      ? "all reviewed ✓"
      : `${counts.total} to review` +
        (counts.imported && counts.assumed
          ? ` · ${counts.imported} imported, ${counts.assumed} assumed`
          : "");

  const chip =
    "rounded px-1.5 py-0.5 text-[10px] font-medium transition-colors";
  const actionKey =
    "rounded-md border border-edge bg-raised px-2 py-1 text-[11px] text-ink-dim transition-colors hover:border-accent hover:text-accent disabled:pointer-events-none disabled:opacity-40";

  return (
    <div className="border-t border-edge bg-bg/70 px-5 py-2">
      <div className="flex items-baseline gap-2">
        <button
          className="flex min-w-0 flex-1 items-baseline gap-2 text-left text-[11px] text-ink-faint transition-colors hover:text-ink-dim"
          onClick={() => setExpanded((v) => !v)}
          title="Walk the blocks that need review — imported and assumed provisions"
        >
          <span className="shrink-0 font-medium tracking-wide uppercase">
            Review
          </span>
          <span className="truncate">{barSummary}</span>
          <span className="ml-auto shrink-0">{expanded ? "▾" : "▸"}</span>
        </button>
        {counts.total > 0 && (
          <button
            className="shrink-0 rounded-md border border-accent/70 bg-accent/15 px-2 py-0.5 text-[11px] font-medium text-accent transition-colors hover:bg-accent/25"
            onClick={() => setExpanded(true)}
            title="Open the review walker"
          >
            Review {counts.total}
          </button>
        )}
      </div>

      {expanded && (
        <div
          ref={walkerRef}
          tabIndex={0}
          onKeyDown={onKeyDown}
          className="mt-2 rounded-lg border border-edge bg-surface/60 p-3 outline-none focus:border-accent/50"
        >
          {/* Mode filter */}
          <div className="mb-2 flex items-center gap-1.5">
            {(["all", "imported", "assumptions"] as ReviewMode[]).map((m) => {
              const n =
                m === "all"
                  ? counts.total
                  : m === "imported"
                    ? counts.imported
                    : counts.assumed;
              const active = mode === m;
              return (
                <button
                  key={m}
                  className={`${chip} ${
                    active
                      ? "bg-accent/20 text-accent"
                      : "text-ink-faint hover:text-ink-dim"
                  }`}
                  onClick={() => changeMode(m)}
                >
                  {modeLabel[m]} {n}
                </button>
              );
            })}
            <span className="ml-auto text-[10px] text-ink-faint tabular-nums">
              {queue.length ? `${safeCursor + 1} of ${queue.length}` : "0 of 0"}
            </span>
          </div>

          {current ? (
            <>
              <div className="flex items-baseline gap-2">
                <button
                  className="shrink-0 font-medium text-ink tabular-nums hover:text-accent"
                  onClick={() => onJump(current.elementId)}
                  title="Jump to this provision in the document"
                >
                  {current.ref}
                </button>
                <span
                  className={`shrink-0 rounded border px-1 py-px text-[9px] font-semibold uppercase ${
                    badgeStyle[current.status] ?? ""
                  }`}
                >
                  {current.status}
                </span>
                {current.sourceItemId && (
                  <span
                    className="shrink-0 cursor-help text-[11px] text-[#7a90b8]"
                    title={
                      sourceLookup.get(current.sourceItemId)
                        ? `Research: ${sourceLookup.get(current.sourceItemId)}`
                        : `Research item ${current.sourceItemId}`
                    }
                  >
                    ◆
                  </span>
                )}
                <span className="min-w-0 truncate text-[11px] text-ink-faint">
                  {current.articleTitle}
                </span>
              </div>

              {editing ? (
                <div className="mt-2">
                  <textarea
                    autoFocus
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Escape") {
                        setEditing(false);
                        focusWalker();
                      }
                      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) saveEdit();
                    }}
                    rows={Math.min(8, draft.split("\n").length + 2)}
                    className="w-full resize-y rounded border border-edge bg-bg px-2 py-1.5 text-[12px] leading-relaxed text-ink outline-none focus:border-accent/60"
                  />
                  <div className="mt-1.5 flex items-center gap-2 text-[11px]">
                    <button
                      className={actionKey}
                      onClick={saveEdit}
                      disabled={busy}
                      title={
                        busy
                          ? "A model turn is streaming — save once it finishes"
                          : "Save (⌘/Ctrl+Enter)"
                      }
                    >
                      Save (⌘/Ctrl+Enter)
                    </button>
                    <button
                      className={actionKey}
                      onClick={() => {
                        setEditing(false);
                        focusWalker();
                      }}
                    >
                      Cancel (Esc)
                    </button>
                  </div>
                </div>
              ) : (
                <p className="mt-1.5 max-h-40 overflow-y-auto text-[12px] leading-relaxed whitespace-pre-wrap text-ink-dim">
                  {current.text}
                </p>
              )}

              {!editing && (
                <>
                  <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
                    <button className={actionKey} onClick={confirm} disabled={busy}>
                      <b>K</b>eep
                    </button>
                    <button className={actionKey} onClick={startEdit} disabled={busy}>
                      <b>E</b>dit
                    </button>
                    <button className={actionKey} onClick={remove} disabled={busy}>
                      <b>D</b>elete
                    </button>
                    <button className={actionKey} onClick={ask} disabled={busy}>
                      <b>A</b>sk model
                    </button>
                    <button className={actionKey} onClick={skip}>
                      <b>S</b>kip →
                    </button>
                  </div>

                  {articleGroup.length >= 2 && (
                    <button
                      className="relative mt-2 w-full overflow-hidden rounded-md border border-edge bg-raised px-2 py-1 text-[11px] text-ink-dim transition-colors hover:border-accent/60 disabled:pointer-events-none disabled:opacity-40"
                      onPointerDown={startHold}
                      onPointerUp={cancelHold}
                      onPointerLeave={cancelHold}
                      onPointerCancel={cancelHold}
                      disabled={busy}
                      title="Press and hold to confirm every outstanding block in this article — one undo step"
                    >
                      <span
                        className="absolute inset-y-0 left-0 bg-accent/25"
                        style={{
                          width: holding ? "100%" : "0%",
                          transition: holding
                            ? `width ${HOLD_MS}ms linear`
                            : "width 120ms ease-out",
                        }}
                      />
                      <span className="relative">
                        {holding
                          ? "Keep holding…"
                          : `Hold to confirm remaining ${articleGroup.length} in “${current.articleTitle}”`}
                      </span>
                    </button>
                  )}

                  <p className="mt-2 text-[10px] text-ink-faint">
                    Keys: <b>K</b> keep · <b>E</b> edit · <b>D</b> delete ·{" "}
                    <b>A</b> ask · <b>S</b>/→ skip · ← back
                  </p>
                </>
              )}
            </>
          ) : (
            <div className="py-3 text-center">
              <p className="text-[12px] font-medium text-ok">
                {counts.total === 0
                  ? "Nothing left to review ✓"
                  : `No ${modeLabel[mode].toLowerCase()} blocks left ✓`}
              </p>
              {touched > 0 && (
                <p className="mt-1 text-[11px] text-ink-faint">
                  This session: {tally.confirmed} confirmed · {tally.edited}{" "}
                  edited · {tally.deleted} deleted
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
