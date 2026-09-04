/**
 * "Waiting on you" — the questions, decisions and to-dos the model is
 * tracking for the user.
 *
 * Sits directly under Open items and is deliberately its sibling in shape,
 * not its twin in meaning: Open items are gaps in the SPEC ([TBD] markers,
 * needs-input blocks), these are gaps in what the model has been TOLD. The
 * list is model-authored; the user's side of it is the checkbox.
 *
 * Rendered whenever the list holds anything at all, open or settled — not
 * only while something is waiting — so the last check-off is visible
 * instead of the panel vanishing mid-animation.
 */
import { useEffect, useRef, useState } from "react";

import {
  followupAge,
  followupCounts,
  justResolvedIds,
  settledFollowups,
  waitingFollowups,
} from "../lib/followups";
import type { FollowUp } from "../types";

const kindDot: Record<FollowUp["kind"], string> = {
  decision: "bg-warn",
  question: "bg-accent",
  todo: "bg-ink-faint",
};

const kindLabel: Record<FollowUp["kind"], string> = {
  decision: "decision",
  question: "question",
  todo: "to-do",
};

export default function FollowUpsPanel({
  items,
  currentTurn,
  busy,
  openNonce,
  onSetStatus,
  onJump,
}: {
  items: FollowUp[];
  /** Assistant-bubble count, for "raised N replies ago". */
  currentTurn: number;
  busy: boolean;
  openNonce?: number;
  onSetStatus: (
    fid: string,
    status: "open" | "resolved",
    note?: string,
  ) => void;
  onJump: (elementId: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [doneOpen, setDoneOpen] = useState(false);
  const [noteFor, setNoteFor] = useState<string | null>(null);
  const [noteDraft, setNoteDraft] = useState("");
  // Ids mid-check-off, so the row can strike through and tick before it
  // moves to Done. Driven off a snapshot diff (see lib/followups) rather
  // than the click, so a model-side resolve animates identically.
  const [settling, setSettling] = useState<Set<string>>(new Set());
  const previous = useRef<FollowUp[] | null>(null);
  // The panel opens itself the first time something lands in it — an item
  // nobody sees is the exact failure this list exists to prevent. Once
  // only: a later collapse is the user's decision and is respected.
  const introduced = useRef(false);

  const { waiting, done } = followupCounts(items);

  // Two effects, deliberately: the first only ADDS, the second only clears.
  // Driving the clear off `items` instead stranded ids — the model's resolve
  // arrives as a streamed snapshot, then `turn_complete` installs an
  // authoritative one that carries no new transition, so the effect re-ran,
  // its cleanup cancelled the pending timer, and the early return armed no
  // replacement. Keying the timer to `settling` makes that unrepresentable:
  // a snapshot with nothing newly settled does not touch it at all.
  useEffect(() => {
    const moved = justResolvedIds(previous.current, items);
    previous.current = items;
    if (moved.size === 0) return;
    setSettling((current) => new Set([...current, ...moved]));
  }, [items]);

  useEffect(() => {
    if (settling.size === 0) return;
    const captured = settling;
    const timer = window.setTimeout(() => {
      setSettling((current) => {
        const next = new Set(current);
        for (const fid of captured) next.delete(fid);
        // Same set back when nothing changed, so this cannot re-arm itself.
        return next.size === current.size ? current : next;
      });
    }, 1100);
    return () => window.clearTimeout(timer);
  }, [settling]);

  useEffect(() => {
    if (introduced.current || items.length === 0) return;
    introduced.current = true;
    setExpanded(true);
  }, [items.length]);

  // The tour opens the panel by bumping the nonce; the user can still
  // collapse it freely — the tour never fights back.
  useEffect(() => {
    if (openNonce) setExpanded(true);
  }, [openNonce]);

  if (items.length === 0) return null;

  const settled = settledFollowups(items);
  const stillSettling = waiting > 0 || settling.size > 0;
  const summary = stillSettling
    ? `${waiting} waiting${done ? ` · ${done} done` : ""}`
    : `all ${done} done`;

  const submitNote = (fid: string) => {
    onSetStatus(fid, "resolved", noteDraft.trim());
    setNoteFor(null);
    setNoteDraft("");
  };

  return (
    <div
      className="border-t border-edge bg-bg/70 px-5 py-2"
      data-tour="followups"
      data-capability="followups.track"
    >
      <button
        className="flex w-full items-baseline gap-2 text-left text-[11px] text-ink-faint transition-colors hover:text-ink-dim"
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
        aria-controls="followups-list"
        title="Questions, decisions and to-dos the assistant is tracking for you"
      >
        <span className="shrink-0 font-medium tracking-wide uppercase">
          Waiting on you
        </span>
        <span key={waiting} className="truncate tally-flash">
          {summary}
        </span>
        <span className="ml-auto shrink-0">{expanded ? "▾" : "▸"}</span>
      </button>

      {expanded && (
        <div id="followups-list" className="mt-1.5">
          <ul className="max-h-56 space-y-1 overflow-y-auto">
            {waitingFollowups(items).map((item) => (
              <li key={item.fid} className="flex items-baseline gap-2 px-1 py-0.5">
                <button
                  className="mt-[1px] flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded border border-edge text-[9px] text-transparent transition-colors hover:border-accent hover:text-accent disabled:opacity-40"
                  onClick={() => onSetStatus(item.fid, "resolved")}
                  disabled={busy}
                  aria-label={`Mark "${item.title}" done`}
                  title={
                    busy
                      ? "The assistant is replying — try again in a moment"
                      : "Mark this done"
                  }
                >
                  ✓
                </button>
                <span
                  className={`h-1.5 w-1.5 shrink-0 translate-y-[-1px] rounded-full ${kindDot[item.kind]}`}
                />
                <span className="min-w-0 flex-1">
                  <span className="block text-xs text-ink">
                    {item.element_id ? (
                      <button
                        className="text-left underline decoration-dotted underline-offset-2 hover:text-accent"
                        onClick={() => onJump(item.element_id)}
                        title="Jump to the provision this is about"
                      >
                        {item.title}
                      </button>
                    ) : (
                      item.title
                    )}
                  </span>
                  {item.detail && (
                    <span className="block text-[11px] text-ink-faint">
                      {item.detail}
                    </span>
                  )}
                  <span className="block text-[10px] text-ink-faint">
                    {item.blocking && (
                      <span className="mr-1.5 font-medium text-warn">
                        ⚠ blocking
                      </span>
                    )}
                    {kindLabel[item.kind]} · {followupAge(item, currentTurn)}
                  </span>
                </span>
              </li>
            ))}
          </ul>

          {settled.length > 0 && (
            <div className="mt-1.5 border-t border-edge/60 pt-1.5">
              <button
                className="flex w-full items-baseline gap-2 text-left text-[10px] text-ink-faint transition-colors hover:text-ink-dim"
                onClick={() => setDoneOpen((value) => !value)}
                aria-expanded={doneOpen}
              >
                <span>Done ({settled.length})</span>
                <span className="ml-auto">{doneOpen ? "▾" : "▸"}</span>
              </button>
              {doneOpen && (
                <ul className="mt-1 max-h-40 space-y-1 overflow-y-auto">
                  {settled.map((item) => (
                    <li
                      key={item.fid}
                      className={`flex items-baseline gap-2 px-1 py-0.5 ${
                        settling.has(item.fid) ? "followup-settle" : ""
                      }`}
                    >
                      <span
                        className={`mt-[1px] flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded border border-accent/70 bg-accent/15 text-[9px] text-accent ${
                          settling.has(item.fid) ? "followup-check" : ""
                        }`}
                        aria-hidden="true"
                      >
                        ✓
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block text-xs text-ink-faint line-through">
                          {item.title}
                        </span>
                        {item.resolution && (
                          <span className="block text-[11px] text-ink-dim">
                            {item.resolution}
                          </span>
                        )}
                        {noteFor === item.fid ? (
                          <input
                            className="mt-1 w-full rounded border border-edge bg-bg px-1 py-0.5 text-[11px] text-ink outline-none focus:border-accent"
                            value={noteDraft}
                            autoFocus
                            placeholder="What did you decide?"
                            onChange={(event) => setNoteDraft(event.target.value)}
                            onKeyDown={(event) => {
                              if (event.key === "Enter") submitNote(item.fid);
                              if (event.key === "Escape") setNoteFor(null);
                            }}
                            onBlur={() => setNoteFor(null)}
                          />
                        ) : (
                          <span className="mt-0.5 flex gap-2 text-[10px]">
                            <button
                              className="text-ink-faint underline-offset-2 hover:text-accent hover:underline disabled:opacity-40"
                              onClick={() => {
                                setNoteFor(item.fid);
                                setNoteDraft(
                                  item.resolved_by === "user"
                                    ? ""
                                    : item.resolution,
                                );
                              }}
                              disabled={busy}
                            >
                              {item.resolved_by === "user" ? "Add a note" : "Edit note"}
                            </button>
                            <button
                              className="text-ink-faint underline-offset-2 hover:text-accent hover:underline disabled:opacity-40"
                              onClick={() => onSetStatus(item.fid, "open")}
                              disabled={busy}
                              title="Put this back on the waiting list"
                            >
                              ↺ Reopen
                            </button>
                          </span>
                        )}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
