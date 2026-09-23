/**
 * Harvest facts (Project workspace Phase 4): one paid, opt-in model call
 * that reads what this section settled and PROPOSES the project facts nobody
 * recorded — each with the line it rests on — for the user to accept or
 * reject. Nothing is recorded until they do.
 *
 * Four states, in order: the intro says what will be read and that it is a
 * paid call (nothing runs until Run is pressed — decision D3, "offered,
 * never forced"); running; the review sheet; done. The sheet is the point:
 * every proposal shows its quoted evidence (flagged when the quote is not
 * found verbatim in what was read), its source, and — when the server found
 * one — the problem that stops it being recorded as it stands. A proposal
 * with a problem starts unchecked; the user can fix it and tick it, and the
 * commit re-checks everything server-side. A commit the server refuses for
 * one proposal keeps the sheet (and its token) so a typo never costs another
 * paid call.
 *
 * Opened from the Project facts panel, from Next section's "Harvest first"
 * and from the Export menu's hint — it stacks over whatever opened it, so
 * closing it returns the user there. It never runs itself.
 */
import { useEffect, useRef, useState } from "react";

import type { HarvestCommitInput } from "../lib/api";
import { HarvestRequestError } from "../lib/api";
import {
  buildHarvestCommit,
  commitLabel,
  describeHarvestRead,
  draftOfProposal,
  formatHarvestCost,
  initialAccepted,
  type HarvestDraft,
} from "../lib/harvest";
import type {
  HarvestCommitResult,
  HarvestPreview,
  HarvestProposal,
  HarvestStatus,
} from "../types";
import { ModalShell, primaryBtn, quietBtn } from "./ModalShell";

type Phase =
  | { kind: "intro" }
  | { kind: "running" }
  | { kind: "review"; preview: HarvestPreview }
  | { kind: "failed"; message: string; code: string }
  | { kind: "done"; result: HarvestCommitResult; accepted: number };

const fieldClass =
  "w-full rounded border border-edge bg-bg px-1.5 py-1 text-[11px] text-ink outline-none focus:border-accent";
const selectClass =
  "rounded border border-edge bg-bg px-1 py-0.5 text-[10px] text-ink";

const SOURCE_LABEL: Record<HarvestProposal["source_kind"], string> = {
  user: "you said it",
  model: "a default you accepted",
  research: "research",
  reference: "attached document",
  qc: "Final QC",
};

function sourceLine(draft: HarvestDraft): string {
  const kind = draft.source_kind as HarvestProposal["source_kind"];
  const label = SOURCE_LABEL[kind] ?? kind;
  return draft.source_ref ? `${label} · ${draft.source_ref}` : label;
}

function scopeLine(draft: HarvestDraft): string {
  if (draft.scope === "section") {
    return draft.section ? `about section ${draft.section}` : "about one section";
  }
  return draft.scope === "discipline" ? "discipline-wide" : "project-wide";
}

function ProposalRow({
  proposal,
  draft,
  accepted,
  editing,
  error,
  disabled,
  onToggle,
  onEdit,
  onDraft,
}: {
  proposal: HarvestProposal;
  draft: HarvestDraft;
  accepted: boolean;
  editing: boolean;
  /** The server's reason from a refused commit, for this row. */
  error: string;
  disabled: boolean;
  onToggle: () => void;
  onEdit: () => void;
  onDraft: (draft: HarvestDraft) => void;
}) {
  const edited = Object.keys(draft).some(
    (key) =>
      draft[key as keyof HarvestDraft] !==
      draftOfProposal(proposal)[key as keyof HarvestDraft],
  );
  return (
    <li
      className={
        "rounded-md border px-2 py-1.5 " +
        (accepted ? "border-accent/50 bg-accent/5" : "border-edge/60")
      }
      data-proposal-index={proposal.index}
    >
      <div className="flex items-start gap-2">
        <input
          type="checkbox"
          className="mt-0.5"
          checked={accepted}
          disabled={disabled}
          onChange={onToggle}
          aria-label={`Accept: ${draft.statement}`}
        />
        <div className="min-w-0 flex-1">
          <span className="block text-xs text-ink">{draft.statement || "(no statement)"}</span>
          {draft.detail && (
            <span className="block text-[11px] text-ink-faint">{draft.detail}</span>
          )}
          <span className="mt-0.5 block text-[10px] text-ink-faint">
            {draft.status} · {scopeLine(draft)} · {sourceLine(draft)}
            {edited && " · edited"}
          </span>
          {proposal.evidence && (
            <blockquote className="mt-1 border-l-2 border-edge pl-2 text-[11px] italic text-ink-dim">
              “{proposal.evidence}”
              {!proposal.evidence_found && (
                <span
                  className="ml-1 not-italic text-warn"
                  title="The model was told to quote the line exactly; a quote not found in what it read is the first sign of a fact nobody settled"
                >
                  ⚠ not found verbatim in what was read — check it
                </span>
              )}
            </blockquote>
          )}
          {proposal.problem && !edited && !error && (
            <span className="mt-1 block text-[10px] text-warn">⚠ {proposal.problem}</span>
          )}
          {error && (
            <span role="alert" className="mt-1 block text-[10px] text-err">
              {error}
            </span>
          )}
          {editing ? (
            <div className="mt-1 space-y-1 rounded border border-edge/60 bg-bg/60 p-1.5">
              <textarea
                className={fieldClass + " resize-none"}
                rows={2}
                value={draft.statement}
                maxLength={240}
                onChange={(event) => onDraft({ ...draft, statement: event.target.value })}
                aria-label="Fact statement"
              />
              <input
                className={fieldClass}
                value={draft.detail}
                placeholder="Detail or basis (optional)"
                maxLength={600}
                onChange={(event) => onDraft({ ...draft, detail: event.target.value })}
                aria-label="Fact detail"
              />
              <div className="flex flex-wrap items-center gap-1.5 text-[10px] text-ink-faint">
                <select
                  className={selectClass}
                  value={draft.scope}
                  onChange={(event) => onDraft({ ...draft, scope: event.target.value })}
                  aria-label="Fact scope"
                >
                  <option value="project">project-wide</option>
                  <option value="discipline">discipline-wide</option>
                  <option value="section">one section</option>
                </select>
                {draft.scope === "section" && (
                  <input
                    className="w-24 rounded border border-edge bg-bg px-1 py-0.5 text-[10px] text-ink"
                    value={draft.section}
                    placeholder="21 13 13"
                    onChange={(event) => onDraft({ ...draft, section: event.target.value })}
                    aria-label="Section number the fact is about"
                  />
                )}
                <select
                  className={selectClass}
                  value={draft.status}
                  onChange={(event) => onDraft({ ...draft, status: event.target.value })}
                  aria-label="Fact status"
                >
                  <option value="confirmed">confirmed</option>
                  <option value="assumed">assumed</option>
                </select>
                <select
                  className={selectClass}
                  value={draft.source_kind}
                  onChange={(event) => onDraft({ ...draft, source_kind: event.target.value })}
                  aria-label="Fact source kind"
                >
                  <option value="user">you said it</option>
                  <option value="model">a default you accepted</option>
                  <option value="research">research</option>
                  <option value="reference">attached document</option>
                  <option value="qc">Final QC</option>
                </select>
                <input
                  className="w-28 rounded border border-edge bg-bg px-1 py-0.5 text-[10px] text-ink"
                  value={draft.source_ref}
                  placeholder="turn:3, r-…, ref-…"
                  maxLength={120}
                  onChange={(event) => onDraft({ ...draft, source_ref: event.target.value })}
                  aria-label="Fact source"
                  title="A reply (turn:N), a research finding (r-…), an attached document (ref-…) or a Final QC finding — or empty"
                />
              </div>
            </div>
          ) : (
            <button
              type="button"
              className="mt-0.5 text-[10px] text-ink-faint underline-offset-2 hover:text-accent hover:underline disabled:opacity-40"
              disabled={disabled}
              onClick={onEdit}
            >
              Edit
            </button>
          )}
        </div>
      </div>
    </li>
  );
}

export default function HarvestDialog({
  pending,
  onRun,
  onCommit,
  onClose,
}: {
  /** Replies since the last committed harvest — the intro's numbers. */
  pending: HarvestStatus | null;
  /** App's handler: the one paid call. Rejects with a HarvestRequestError. */
  onRun: () => Promise<HarvestPreview>;
  /** App's handler: records the accepted proposals and refreshes the facts,
   *  readiness and Final QC state. Rejects with a HarvestRequestError. */
  onCommit: (input: HarvestCommitInput) => Promise<HarvestCommitResult>;
  onClose: () => void;
}) {
  const [phase, setPhase] = useState<Phase>({ kind: "intro" });
  const [accepted, setAccepted] = useState<Set<number>>(new Set());
  const [drafts, setDrafts] = useState<Map<number, HarvestDraft>>(new Map());
  const [editing, setEditing] = useState<Set<number>>(new Set());
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});
  const [commitError, setCommitError] = useState("");
  const [committing, setCommitting] = useState(false);
  // The call keeps running server-side (and is billed) if the dialog closes
  // meanwhile; a late answer must not set state on a closed dialog.
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const run = async () => {
    setPhase({ kind: "running" });
    setCommitError("");
    setRowErrors({});
    try {
      const preview = await onRun();
      if (!mounted.current) return;
      setAccepted(initialAccepted(preview.proposals));
      setDrafts(new Map(preview.proposals.map((p) => [p.index, draftOfProposal(p)])));
      setEditing(new Set());
      setPhase({ kind: "review", preview });
    } catch (error) {
      if (!mounted.current) return;
      setPhase({
        kind: "failed",
        message: error instanceof Error ? error.message : String(error),
        code: error instanceof HarvestRequestError ? error.code : "",
      });
    }
  };

  const commit = async (preview: HarvestPreview) => {
    setCommitting(true);
    setCommitError("");
    setRowErrors({});
    const input = buildHarvestCommit(preview.token, preview.proposals, accepted, drafts);
    try {
      const result = await onCommit(input);
      if (!mounted.current) return;
      setPhase({ kind: "done", result, accepted: input.accepted.length });
    } catch (error) {
      if (!mounted.current) return;
      const code = error instanceof HarvestRequestError ? error.code : "";
      const message = error instanceof Error ? error.message : String(error);
      if (code === "harvest_expired" || code === "harvest_stale") {
        // The token is gone (or describes a project that moved on): the
        // sheet can no longer be recorded as it stands.
        setPhase({ kind: "failed", message, code });
      } else {
        // Fixable (a proposal the server refuses, a reply still streaming):
        // the sheet and its token stay, so the fix costs nothing.
        if (error instanceof HarvestRequestError) setRowErrors(error.errors);
        setCommitError(message);
      }
    } finally {
      if (mounted.current) setCommitting(false);
    }
  };

  const toggle = (index: number) =>
    setAccepted((current) => {
      const next = new Set(current);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });

  const replies = pending?.replies_since ?? 0;

  return (
    <ModalShell title="Harvest project facts" onClose={onClose} xwide>
      <div data-capability="project.facts-harvest">
        {phase.kind === "intro" && (
          <>
            <p className="text-sm leading-relaxed text-ink-dim">
              Reads{" "}
              {replies > 0 ? (
                <b className="text-ink">
                  the {replies === 1 ? "reply" : `${replies} replies`} since facts were
                  last harvested
                </b>
              ) : (
                "the conversation (no replies are waiting)"
              )}
              , every provision of the draft with its status, and the reasons you gave
              for dismissing Final QC findings — then proposes the project facts they
              settled that nobody recorded, each with the line it rests on. The next
              section of this project starts knowing what you accept.
            </p>
            <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-ink-dim">
              <li>
                Nothing is recorded until you accept it; you can edit or reject every
                proposal.
              </li>
              <li>
                A proposal citing a research finding, attached document or Final QC
                finding that does not exist cannot be recorded.
              </li>
              <li>
                It is one paid model call. It runs only when you press Run — never on
                its own.
              </li>
            </ul>
            <div className="mt-4 flex gap-2">
              <button
                type="button"
                className={primaryBtn}
                onClick={() => void run()}
                data-capability="project.facts-harvest"
              >
                Run the harvest
              </button>
              <button type="button" className={quietBtn} onClick={onClose}>
                Cancel
              </button>
            </div>
          </>
        )}

        {phase.kind === "running" && (
          <div className="flex items-center gap-2 py-3 text-sm text-ink-dim" aria-live="polite">
            <span className="status-dots" aria-hidden="true">
              <span />
              <span />
              <span />
            </span>
            <span className="status-shimmer">
              Reading {replies > 0 ? `${replies} ${replies === 1 ? "reply" : "replies"} and ` : ""}
              the draft…
            </span>
          </div>
        )}

        {phase.kind === "failed" && (
          <>
            <p role="alert" className="text-sm text-err">
              {phase.message}
            </p>
            <div className="mt-4 flex gap-2">
              {phase.code !== "tutorial_active" && phase.code !== "nothing_to_harvest" && (
                <button
                  type="button"
                  className={primaryBtn}
                  onClick={() => void run()}
                  title="Runs the paid call again"
                >
                  Run it again
                </button>
              )}
              <button type="button" className={quietBtn} onClick={onClose}>
                Close
              </button>
            </div>
          </>
        )}

        {phase.kind === "review" && (
          <>
            <p className="text-xs text-ink-dim">{describeHarvestRead(phase.preview)}</p>
            <p className="mt-0.5 text-[11px] text-ink-faint">
              {phase.preview.proposals.length === 0
                ? "Nothing new to record — every settled fact is already in the ledger, or nothing new was settled."
                : `${phase.preview.proposals.length} proposed`}
              {phase.preview.dropped_duplicates > 0 &&
                ` · ${phase.preview.dropped_duplicates} left out because a recorded fact already says ${
                  phase.preview.dropped_duplicates === 1 ? "it" : "them"
                }`}
              {formatHarvestCost(phase.preview.estimated_cost_usd) &&
                ` · this harvest cost ${formatHarvestCost(phase.preview.estimated_cost_usd)}`}
            </p>
            {phase.preview.proposals.length > 0 && (
              <ul className="mt-2 max-h-[55vh] space-y-1.5 overflow-y-auto pr-1">
                {phase.preview.proposals.map((proposal) => (
                  <ProposalRow
                    key={proposal.index}
                    proposal={proposal}
                    draft={drafts.get(proposal.index) ?? draftOfProposal(proposal)}
                    accepted={accepted.has(proposal.index)}
                    editing={editing.has(proposal.index)}
                    error={rowErrors[String(proposal.index)] ?? ""}
                    disabled={committing}
                    onToggle={() => toggle(proposal.index)}
                    onEdit={() =>
                      setEditing((current) => new Set([...current, proposal.index]))
                    }
                    onDraft={(draft) => {
                      setDrafts((current) => new Map(current).set(proposal.index, draft));
                      // The server's reason described the row as it was;
                      // once edited, the next commit is the judge again.
                      setRowErrors((current) => {
                        const key = String(proposal.index);
                        if (!(key in current)) return current;
                        const next = { ...current };
                        delete next[key];
                        return next;
                      });
                    }}
                  />
                ))}
              </ul>
            )}
            {commitError && (
              <p role="alert" className="mt-2 text-xs text-err">
                {commitError}
              </p>
            )}
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <button
                type="button"
                className={primaryBtn}
                disabled={committing}
                onClick={() => void commit(phase.preview)}
                title={
                  accepted.size === 0
                    ? "Records nothing, and marks these replies read so the next harvest starts after them"
                    : "Records the ticked proposals as project facts — one batch, all or nothing"
                }
              >
                {committing ? "Recording…" : commitLabel(accepted.size)}
              </button>
              <button type="button" className={quietBtn} onClick={onClose} disabled={committing}>
                Cancel
              </button>
              {phase.preview.proposals.length > 0 && (
                <span className="text-[11px] text-ink-faint">
                  {accepted.size} of {phase.preview.proposals.length} selected
                </span>
              )}
            </div>
          </>
        )}

        {phase.kind === "done" && (
          <>
            <p className="text-sm text-ink-dim" aria-live="polite">
              {phase.accepted === 0
                ? "Nothing recorded. These replies are marked read, so the next harvest starts after them."
                : `Recorded ${phase.result.recorded.length} ${
                    phase.result.recorded.length === 1 ? "fact" : "facts"
                  }${
                    phase.result.already_recorded.length
                      ? ` (${phase.result.already_recorded.length} already recorded)`
                      : ""
                  }. They are in the Project facts panel, and the next section of this project starts knowing them.`}
            </p>
            <div className="mt-4 flex gap-2">
              <button type="button" className={primaryBtn} onClick={onClose}>
                Close
              </button>
            </div>
          </>
        )}
      </div>
    </ModalShell>
  );
}
