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
import {
  buildQueue,
  reviewCounts,
  type QueueEntry,
  type ReviewMode,
} from "../lib/reviewQueue";

interface Props {
  doc: SpecDoc | null;
  sourceLookup: ReadonlyMap<string, string>;
  busy: boolean;
  bodyEditingDisabled: boolean;
  // App's handler is async; the drawer awaits it for an in-flight lockout.
  onEditDoc: (ops: EditOp[]) => void | Promise<unknown>;
  onAskModel: (text: string) => void;
  onJump: (elementId: string) => void;
  /** Guided-tour "ensure open" (Batch 6): a bump expands the drawer. */
  openNonce?: number;
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
  bodyEditingDisabled,
  onEditDoc,
  onAskModel,
  onJump,
  openNonce,
}: Props) {
  const [expanded, setExpanded] = useState(false);
  // The tour opens the drawer by bumping the nonce; the user can still
  // collapse it freely — the tour never fights back.
  useEffect(() => {
    if (openNonce) setExpanded(true);
  }, [openNonce]);
  const [mode, setMode] = useState<ReviewMode>("all");
  const [cursor, setCursor] = useState(0);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  // The block being edited is SNAPSHOTTED at edit start — never re-read from
  // the live queue at save time, so an undo/redo or a completing turn that
  // shifts `current` under an open editor can't misdirect the write.
  const [editTarget, setEditTarget] = useState<QueueEntry | null>(null);
  const [holding, setHolding] = useState(false);
  const [tally, setTally] = useState({ confirmed: 0, edited: 0, deleted: 0 });
  // True while a manual edit's round-trip is in flight. `busy` only tracks a
  // model chat turn, and mutations don't advance the cursor themselves (the
  // doc round-trip does), so without this a second fast keypress would re-fire
  // on the still-stale `current`.
  const [pending, setPending] = useState(false);

  const walkerRef = useRef<HTMLDivElement>(null);
  const holdTimer = useRef<number | undefined>(undefined);

  const counts = reviewCounts(doc);
  const queue = useMemo(() => buildQueue(doc, mode), [doc, mode]);
  const safeCursor = queue.length ? Math.min(cursor, queue.length - 1) : 0;
  const current = queue[safeCursor];
  const docEmpty = !doc || doc.parts.every((p) => p.articles.length === 0);
  // Locked out from mutating while a model turn streams OR a manual edit is
  // still resolving. Navigation (skip/back) stays live.
  const locked = busy || pending;
  const bodyMutationLocked = locked || bodyEditingDisabled;

  const focusWalker = useCallback(() => {
    requestAnimationFrame(() => walkerRef.current?.focus());
  }, []);

  // Fire a mutation and hold the lockout until its round-trip settles (App's
  // handler catches its own errors, so this always resolves).
  const runEdit = useCallback(
    (ops: EditOp[]) => {
      setPending(true);
      Promise.resolve(onEditDoc(ops)).finally(() => setPending(false));
    },
    [onEditDoc],
  );

  // Focus the walker when the drawer opens so the keyboard flow works at once.
  useEffect(() => {
    if (expanded && queue.length) focusWalker();
  }, [expanded, queue.length, focusWalker]);

  // Reset the session tally + cursor whenever the document IDENTITY changes —
  // a new session, or loading a different project (populated OR empty). Keying
  // on empty alone leaked the previous project's tally across a non-empty load.
  const docKey = doc
    ? [
        doc.section.number,
        doc.section.title,
        doc.project_profile?.client_name ?? "",
      ].join("|")
    : "";
  const prevDocKey = useRef(docKey);
  useEffect(() => {
    if (prevDocKey.current !== docKey) {
      prevDocKey.current = docKey;
      setTally({ confirmed: 0, edited: 0, deleted: 0 });
      setCursor(0);
      setEditing(false);
      setEditTarget(null);
    }
  }, [docKey]);

  // Collapse the drawer when the document empties (new session / empty load).
  useEffect(() => {
    if (docEmpty) setExpanded(false);
  }, [docEmpty]);

  useEffect(() => () => window.clearTimeout(holdTimer.current), []);

  const changeMode = (next: ReviewMode) => {
    setMode(next);
    setCursor(0);
    setEditing(false);
    focusWalker();
  };

  // --- Actions (all no-ops while a turn streams or an edit is in flight) ----

  const confirm = useCallback(() => {
    if (locked || !current) return;
    runEdit([
      { action: "set_status", target_id: current.elementId, status: "confirmed" },
    ]);
    setTally((t) => ({ ...t, confirmed: t.confirmed + 1 }));
    setEditing(false);
    focusWalker();
  }, [locked, current, runEdit, focusWalker]);

  const remove = useCallback(() => {
    if (bodyMutationLocked || !current) return;
    runEdit([{ action: "delete", target_id: current.elementId }]);
    setTally((t) => ({ ...t, deleted: t.deleted + 1 }));
    setEditing(false);
    focusWalker();
  }, [bodyMutationLocked, current, runEdit, focusWalker]);

  const startEdit = useCallback(() => {
    if (bodyMutationLocked || !current) return;
    setDraft(current.text);
    setEditTarget(current); // snapshot the target — save writes to THIS block
    setEditing(true);
  }, [bodyMutationLocked, current]);

  const saveEdit = () => {
    // A model turn owns the tree (busy) or an edit is resolving (pending):
    // keep the draft open so nothing is lost — the user saves after it clears.
    if (bodyMutationLocked) return;
    const target = editTarget;
    const text = draft.trim();
    setEditing(false);
    setEditTarget(null);
    if (!target || !text) {
      focusWalker();
      return;
    }
    // Compare against the SNAPSHOTTED original, and write to the snapshotted
    // id — never to whatever `current` happens to be now.
    if (text !== target.text) {
      runEdit([
        {
          action: "replace",
          target_id: target.elementId,
          text,
          status: "confirmed",
          source_item_id: target.sourceItemId,
        },
      ]);
      setTally((t) => ({ ...t, edited: t.edited + 1 }));
    }
    focusWalker();
  };

  const ask = useCallback(() => {
    if (locked || !current) return;
    const snippet =
      current.text.length > 80
        ? `${current.text.slice(0, 80).trimEnd()}…`
        : current.text;
    onAskModel(`Regarding ${current.ref} "${snippet}": `);
    setEditing(false);
  }, [locked, current, onAskModel]);

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
    if (locked || articleGroup.length === 0) return;
    runEdit(
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
    if (locked || articleGroup.length < 2) return;
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
    if (e.repeat) return; // ignore OS key auto-repeat (double-fire guard)
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
    <div
      className="border-t border-edge bg-bg/70 px-5 py-2"
      data-tour="review-drawer"
    >
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
                      disabled={bodyMutationLocked}
                      title={
                        bodyEditingDisabled
                          ? "Body edits are disabled for this pass-through-only DOCX"
                          : locked
                          ? "Busy — save once the current action finishes"
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
                    <button className={actionKey} onClick={confirm} disabled={locked}>
                      <b>K</b>eep
                    </button>
                    <button
                      className={actionKey}
                      onClick={startEdit}
                      disabled={bodyMutationLocked}
                      title={
                        bodyEditingDisabled
                          ? "Body edits are disabled for this pass-through-only DOCX"
                          : undefined
                      }
                    >
                      <b>E</b>dit
                    </button>
                    <button
                      className={actionKey}
                      onClick={remove}
                      disabled={bodyMutationLocked}
                      title={
                        bodyEditingDisabled
                          ? "Body edits are disabled for this pass-through-only DOCX"
                          : undefined
                      }
                    >
                      <b>D</b>elete
                    </button>
                    <button className={actionKey} onClick={ask} disabled={locked}>
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
                      disabled={locked}
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
                    {bodyEditingDisabled ? (
                      <>
                        Pass-through-only source: body edit/delete are disabled.{" "}
                        <b>K</b> keep · <b>A</b> ask · <b>S</b>/→ skip · ← back
                      </>
                    ) : (
                      <>
                        Keys: <b>K</b> keep · <b>E</b> edit · <b>D</b> delete ·{" "}
                        <b>A</b> ask · <b>S</b>/→ skip · ← back
                      </>
                    )}
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
