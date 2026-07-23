/**
 * SectionFormat rendering of the server-owned document tree on the paper
 * surface: PART headings, numbered articles, lettered/numbered paragraph
 * levels, provenance badges, inline [TBD] highlighting, and a tint on
 * blocks changed during the latest turn.
 *
 * WI2 adds direct manual editing: hover a paragraph or article title to
 * reveal ✏️ (inline edit), ✓ (confirm an assumed/imported block), and 🗑
 * (delete). All affordances are disabled while a model turn streams.
 */
import { Fragment, useState, type ButtonHTMLAttributes } from "react";
import {
  sourceAllowedPositions,
  sourceCapability,
  sourceCapabilityTitle,
  sourceEditOpDecision,
} from "../lib/sourceCapabilities";
import type {
  DiffRun,
  DocArticle,
  DocParagraph,
  DocPart,
  EditOp,
  ElementDiff,
  SectionDiff,
  SourceCapabilitiesState,
  SourceOperationCapability,
  SpecDoc,
} from "../types";

const TBD_SPLIT = /(\[TBD:[^\]]*\])/g;

function TbdText({ text }: { text: string }) {
  const pieces = text.split(TBD_SPLIT);
  return (
    <>
      {pieces.map((piece, i) =>
        piece.startsWith("[TBD:") ? (
          <mark
            key={i}
            className="rounded-sm bg-[#f2e3b3] px-0.5 text-[#6d5310]"
          >
            {piece}
          </mark>
        ) : (
          <span key={i}>{piece}</span>
        ),
      )}
    </>
  );
}

const badgeStyles: Record<string, { css: string; label: string }> = {
  assumed: {
    css: "border-[#d4a04c]/60 bg-[#f6ead2] text-[#8a6414]",
    label: "assumed",
  },
  needs_input: {
    css: "border-[#c65b4e]/50 bg-[#f7e2df] text-[#a03d31]",
    label: "needs input",
  },
  imported: {
    css: "border-[#5b7db8]/50 bg-[#e3eaf6] text-[#3a5a94]",
    label: "imported",
  },
};

function StatusBadge({ status }: { status: DocParagraph["status"] }) {
  const style = badgeStyles[status];
  if (!style) return null;
  return (
    <span
      className={`ml-2 inline-block rounded border px-1 py-px align-middle text-[9px] font-semibold tracking-wide uppercase ${style.css}`}
    >
      {style.label}
    </span>
  );
}

function SourceChip({
  itemId,
  lookup,
}: {
  itemId: string;
  lookup: ReadonlyMap<string, string>;
}) {
  if (!itemId) return null;
  const tooltip = lookup.get(itemId);
  return (
    <span
      className="ml-1.5 inline-block cursor-help align-middle text-[10px] text-[#7a90b8]"
      title={
        tooltip
          ? `Research: ${tooltip}`
          : `Research item ${itemId} (re-run research to see details)`
      }
    >
      ◆
    </span>
  );
}

const actionBtn =
  "rounded px-1 text-[12px] leading-none text-paper-dim transition-colors hover:text-paper-ink disabled:opacity-30";

/** Keep exact server denial titles hoverable on natively disabled buttons. */
function CapabilityButton({
  disabled,
  title,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <span className="inline-flex" title={disabled ? title : undefined}>
      <button
        {...props}
        disabled={disabled}
        title={disabled ? undefined : title}
      />
    </span>
  );
}

function ReadOnlyBadge({
  capability,
  sourceExpected,
}: {
  capability: SourceOperationCapability;
  sourceExpected: boolean;
}) {
  if (!sourceExpected || capability.allowed) return null;
  return (
    <span
      className="ml-2 inline-block rounded border border-paper-edge bg-paper-edge/35 px-1 py-px align-middle text-[9px] font-semibold tracking-wide text-paper-dim uppercase"
      title={sourceCapabilityTitle(capability, "")}
    >
      read-only
    </span>
  );
}

function moveTarget(
  capability: SourceOperationCapability,
  direction: "up" | "down",
): number | null {
  const current = capability.current_position;
  if (typeof current !== "number" || !Number.isInteger(current)) return null;
  const positions = sourceAllowedPositions(capability);
  if (direction === "up") {
    const candidates = positions.filter((position) => position < current);
    return candidates.length ? candidates[candidates.length - 1] : null;
  }
  return positions.find((position) => position > current) ?? null;
}

/** Hover toolbar for a paragraph: confirm / edit / delete. */
function RowActions({
  canConfirm,
  busy,
  sourceExpected,
  replaceCapability,
  deleteCapability,
  moveCapability,
  statusCapability,
  confirming,
  onConfirm,
  onEdit,
  onDelete,
  onMove,
  onCancelDelete,
}: {
  canConfirm: boolean;
  busy: boolean;
  sourceExpected: boolean;
  replaceCapability: SourceOperationCapability;
  deleteCapability: SourceOperationCapability;
  moveCapability: SourceOperationCapability;
  statusCapability: SourceOperationCapability;
  confirming: boolean;
  onConfirm: () => void;
  onEdit: () => void;
  onDelete: () => void;
  onMove: (position: number) => void;
  onCancelDelete: () => void;
}) {
  const moveUpPosition = moveTarget(moveCapability, "up");
  const moveDownPosition = moveTarget(moveCapability, "down");
  const moveTitle = sourceCapabilityTitle(
    moveCapability,
    "Move this provision",
  );
  if (confirming) {
    return (
      <span className="ml-1 inline-flex shrink-0 items-center gap-1 text-[11px]">
        <span className="text-[#a03d31]">Delete?</span>
        <CapabilityButton
          className={actionBtn}
          onClick={onDelete}
          disabled={busy || !deleteCapability.allowed}
          title={sourceCapabilityTitle(deleteCapability, "Confirm delete")}
        >
          ✓
        </CapabilityButton>
        <button className={actionBtn} onClick={onCancelDelete} title="Keep">
          ✕
        </button>
      </span>
    );
  }
  return (
    <span className="ml-1 hidden shrink-0 items-center gap-0.5 group-hover:inline-flex">
      {canConfirm && (
        <CapabilityButton
          className={actionBtn}
          onClick={onConfirm}
          disabled={busy || !statusCapability.allowed}
          title={sourceCapabilityTitle(
            statusCapability,
            "Confirm this block (mark reviewed)",
          )}
        >
          ✓
        </CapabilityButton>
      )}
      <CapabilityButton
        className={actionBtn}
        onClick={onEdit}
        disabled={busy || !replaceCapability.allowed}
        title={sourceCapabilityTitle(replaceCapability, "Edit this provision")}
      >
        ✏️
      </CapabilityButton>
      <CapabilityButton
        className={actionBtn}
        onClick={onDelete}
        disabled={busy || !deleteCapability.allowed}
        title={sourceCapabilityTitle(deleteCapability, "Delete this provision")}
      >
        🗑
      </CapabilityButton>
      {sourceExpected && (
        <>
          <CapabilityButton
            className={actionBtn}
            onClick={() => {
              if (moveCapability.allowed && moveUpPosition !== null) {
                onMove(moveUpPosition);
              }
            }}
            disabled={
              busy || !moveCapability.allowed || moveUpPosition === null
            }
            title={
              !moveCapability.allowed
                ? moveTitle
                : moveUpPosition === null
                  ? "No server-authorized position exists above this provision."
                  : `Move to sibling position ${moveUpPosition + 1}`
            }
            aria-label="Move provision up"
          >
            ↑
          </CapabilityButton>
          <CapabilityButton
            className={actionBtn}
            onClick={() => {
              if (moveCapability.allowed && moveDownPosition !== null) {
                onMove(moveDownPosition);
              }
            }}
            disabled={
              busy || !moveCapability.allowed || moveDownPosition === null
            }
            title={
              !moveCapability.allowed
                ? moveTitle
                : moveDownPosition === null
                  ? "No server-authorized position exists below this provision."
                  : `Move to sibling position ${moveDownPosition + 1}`
            }
            aria-label="Move provision down"
          >
            ↓
          </CapabilityButton>
        </>
      )}
    </span>
  );
}

function ParagraphNode({
  p,
  depth,
  changedIds,
  sourceLookup,
  busy,
  sourceExpected,
  sourceCapabilities,
  onEdit,
}: {
  p: DocParagraph;
  depth: number;
  changedIds: ReadonlySet<string>;
  sourceLookup: ReadonlyMap<string, string>;
  busy: boolean;
  sourceExpected: boolean;
  sourceCapabilities: SourceCapabilitiesState | null;
  onEdit: (ops: EditOp[]) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(p.text);
  const [confirming, setConfirming] = useState(false);

  const replaceOp: EditOp = {
    action: "replace",
    target_id: p.id,
    text: p.text,
    status: "confirmed",
    source_item_id: p.source_item_id,
  };
  const replaceCapability = sourceEditOpDecision(
    sourceCapabilities,
    sourceExpected,
    { ...replaceOp },
  );
  const deleteCapability = sourceCapability(
    sourceCapabilities,
    sourceExpected,
    p.id,
    "delete",
  );
  const moveCapability = sourceCapability(
    sourceCapabilities,
    sourceExpected,
    p.id,
    "move",
  );
  const statusCapability = sourceCapability(
    sourceCapabilities,
    sourceExpected,
    p.id,
    "set_status",
  );

  const submit = (ops: EditOp[]): boolean => {
    if (busy) return false;
    for (const op of ops) {
      const decision = sourceEditOpDecision(
        sourceCapabilities,
        sourceExpected,
        { ...op },
      );
      if (!decision.allowed) return false;
    }
    onEdit(ops);
    return true;
  };

  const startEdit = () => {
    if (busy || !replaceCapability.allowed) return;
    setDraft(p.text);
    setEditing(true);
  };
  const save = () => {
    const text = draft.trim();
    if (!text || text === p.text) {
      setEditing(false);
      return;
    }
    // User-authored text is confirmed; preserve the research provenance.
    if (
      submit([
        {
          ...replaceOp,
          text,
        },
      ])
    ) {
      setEditing(false);
    }
  };

  return (
    <>
      <div
        id={`el-${p.id}`}
        className={`group flex gap-2 rounded px-1 py-0.5 ${
          changedIds.has(p.id) ? "changed-block" : ""
        } ${
          sourceExpected && !replaceCapability.allowed
            ? "border-l-2 border-paper-edge bg-paper-edge/15"
            : ""
        }`}
        style={{ marginLeft: `${depth * 1.4}rem` }}
      >
        <span className="w-6 shrink-0 text-right">{p.label}</span>
        {editing ? (
          <span className="min-w-0 flex-1">
            <textarea
              autoFocus
              value={draft}
              disabled={busy || !replaceCapability.allowed}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") setEditing(false);
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                  e.preventDefault();
                  save();
                }
              }}
              rows={Math.min(6, draft.split("\n").length + 1)}
              className="w-full resize-y rounded border border-paper-edge bg-white/70 px-1.5 py-1 text-[13px] leading-relaxed text-paper-ink outline-none focus:border-[#c08457]"
            />
            <span className="mt-1 flex items-center gap-2 text-[11px] text-paper-dim">
              <CapabilityButton
                className={actionBtn}
                onClick={save}
                disabled={busy || !replaceCapability.allowed}
                title={sourceCapabilityTitle(
                  replaceCapability,
                  "Save (Ctrl/Cmd+Enter)",
                )}
              >
                Save
              </CapabilityButton>
              <button
                className={actionBtn}
                onClick={() => setEditing(false)}
                title="Cancel (Esc)"
              >
                Cancel
              </button>
              <span className="text-paper-dim/70">Ctrl/Cmd+Enter to save · Esc to cancel</span>
            </span>
          </span>
        ) : (
          <span className="min-w-0 flex-1">
            <TbdText text={p.text} />
            <StatusBadge status={p.status} />
            <SourceChip itemId={p.source_item_id} lookup={sourceLookup} />
            <ReadOnlyBadge
              capability={replaceCapability}
              sourceExpected={sourceExpected}
            />
            <RowActions
              canConfirm={p.status === "assumed" || p.status === "imported"}
              busy={busy}
              sourceExpected={sourceExpected}
              replaceCapability={replaceCapability}
              deleteCapability={deleteCapability}
              moveCapability={moveCapability}
              statusCapability={statusCapability}
              confirming={confirming}
              onConfirm={() => {
                submit([
                  { action: "set_status", target_id: p.id, status: "confirmed" },
                ]);
              }}
              onEdit={startEdit}
              onDelete={() => {
                if (busy || !deleteCapability.allowed) return;
                if (confirming) {
                  if (submit([{ action: "delete", target_id: p.id }])) {
                    setConfirming(false);
                  }
                } else {
                  setConfirming(true);
                }
              }}
              onMove={(position) => {
                submit([{ action: "move", target_id: p.id, position }]);
              }}
              onCancelDelete={() => setConfirming(false)}
            />
          </span>
        )}
      </div>
      {p.children.map((child) => (
        <ParagraphNode
          key={child.id}
          p={child}
          depth={depth + 1}
          changedIds={changedIds}
          sourceLookup={sourceLookup}
          busy={busy}
          sourceExpected={sourceExpected}
          sourceCapabilities={sourceCapabilities}
          onEdit={onEdit}
        />
      ))}
    </>
  );
}

function ArticleTitle({
  id,
  number,
  title,
  changed,
  busy,
  sourceExpected,
  sourceCapabilities,
  onEdit,
}: {
  id: string;
  number: string;
  title: string;
  changed: boolean;
  busy: boolean;
  sourceExpected: boolean;
  sourceCapabilities: SourceCapabilitiesState | null;
  onEdit: (ops: EditOp[]) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(title);
  const replaceCapability = sourceCapability(
    sourceCapabilities,
    sourceExpected,
    id,
    "replace_text",
  );
  if (editing) {
    const save = () => {
      const next = draft.trim();
      if (!next || next === title) {
        setEditing(false);
        return;
      }
      const op: EditOp = {
        action: "replace",
        target_id: id,
        text: next,
      };
      const decision = sourceEditOpDecision(
        sourceCapabilities,
        sourceExpected,
        { ...op },
      );
      if (!busy && decision.allowed) {
        onEdit([op]);
        setEditing(false);
      }
    };
    return (
      <p className="flex items-center gap-2 text-[13px] font-semibold">
        {number}&nbsp;&nbsp;
        <input
          autoFocus
          value={draft}
          disabled={busy || !replaceCapability.allowed}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") setEditing(false);
            if (e.key === "Enter") {
              e.preventDefault();
              save();
            }
          }}
          className="flex-1 rounded border border-paper-edge bg-white/70 px-1.5 py-0.5 text-[13px] font-semibold uppercase text-paper-ink outline-none focus:border-[#c08457]"
        />
        <CapabilityButton
          className={actionBtn}
          onClick={save}
          disabled={busy || !replaceCapability.allowed}
          title={sourceCapabilityTitle(replaceCapability, "Save (Enter)")}
        >
          Save
        </CapabilityButton>
      </p>
    );
  }
  return (
    <p
      className={`group flex items-center rounded px-1 text-[13px] font-semibold ${
        changed ? "changed-block" : ""
      } ${
        sourceExpected && !replaceCapability.allowed
          ? "border-l-2 border-paper-edge bg-paper-edge/15"
          : ""
      }`}
    >
      {number}&nbsp;&nbsp;
      <span className="uppercase">{title}</span>
      <ReadOnlyBadge
        capability={replaceCapability}
        sourceExpected={sourceExpected}
      />
      <CapabilityButton
        className={`${actionBtn} ml-1 hidden group-hover:inline-block`}
        onClick={() => {
          if (busy || !replaceCapability.allowed) return;
          setDraft(title);
          setEditing(true);
        }}
        disabled={busy || !replaceCapability.allowed}
        title={sourceCapabilityTitle(
          replaceCapability,
          "Edit article title",
        )}
      >
        ✏️
      </CapabilityButton>
    </p>
  );
}

function AddParagraphControl({
  articleId,
  position,
  busy,
  sourceExpected,
  sourceCapabilities,
  onEdit,
}: {
  articleId: string;
  position: number;
  busy: boolean;
  sourceExpected: boolean;
  sourceCapabilities: SourceCapabilitiesState | null;
  onEdit: (ops: EditOp[]) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const decision = sourceEditOpDecision(
    sourceCapabilities,
    sourceExpected,
    {
      action: "add_paragraph",
      target_id: articleId,
      position,
      text: "New provision",
    },
  );
  const title = sourceCapabilityTitle(
    decision,
    `Add a top-level provision at sibling position ${position + 1}`,
  );

  const start = () => {
    if (busy || !decision.allowed) return;
    setDraft("");
    setEditing(true);
  };
  const save = () => {
    const text = draft.trim();
    if (!text) return;
    const op: EditOp = {
      action: "add_paragraph",
      target_id: articleId,
      position,
      text,
      status: "confirmed",
    };
    const currentDecision = sourceEditOpDecision(
      sourceCapabilities,
      sourceExpected,
      { ...op },
    );
    if (busy || !currentDecision.allowed) return;
    onEdit([op]);
    setEditing(false);
  };

  if (editing) {
    return (
      <div className="my-1 ml-8 rounded border border-dashed border-paper-edge bg-white/35 px-2 py-1">
        <input
          autoFocus
          value={draft}
          disabled={busy || !decision.allowed}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Escape") setEditing(false);
            if (event.key === "Enter") {
              event.preventDefault();
              save();
            }
          }}
          placeholder="New top-level provision"
          className="w-full rounded border border-paper-edge bg-white/70 px-1.5 py-0.5 text-[12px] text-paper-ink outline-none focus:border-[#c08457]"
        />
        <span className="mt-1 flex items-center gap-2 text-[11px] text-paper-dim">
          <CapabilityButton
            className={actionBtn}
            onClick={save}
            disabled={busy || !decision.allowed || !draft.trim()}
            title={sourceCapabilityTitle(decision, "Add provision (Enter)")}
          >
            Add
          </CapabilityButton>
          <button
            className={actionBtn}
            onClick={() => setEditing(false)}
            title="Cancel (Esc)"
          >
            Cancel
          </button>
        </span>
      </div>
    );
  }

  return (
    <div className="my-0.5 ml-8 flex items-center gap-1 text-[10px] text-paper-dim/80">
      <span className="h-px min-w-4 flex-1 bg-paper-edge/50" />
      <CapabilityButton
        className={`${actionBtn} whitespace-nowrap`}
        onClick={start}
        disabled={busy || !decision.allowed}
        title={title}
      >
        + Add provision here
      </CapabilityButton>
      <span className="h-px min-w-4 flex-1 bg-paper-edge/50" />
    </div>
  );
}

function ArticleBlock({
  article,
  changedIds,
  sourceLookup,
  busy,
  sourceExpected,
  sourceCapabilities,
  onEdit,
}: {
  article: DocArticle;
  changedIds: ReadonlySet<string>;
  sourceLookup: ReadonlyMap<string, string>;
  busy: boolean;
  sourceExpected: boolean;
  sourceCapabilities: SourceCapabilitiesState | null;
  onEdit: (ops: EditOp[]) => void;
}) {
  const addCapability = sourceCapability(
    sourceCapabilities,
    sourceExpected,
    article.id,
    "add_paragraph",
  );
  const allowedPositions = new Set(sourceAllowedPositions(addCapability));
  const addAt = (position: number) =>
    allowedPositions.has(position) ? (
      <AddParagraphControl
        articleId={article.id}
        position={position}
        busy={busy}
        sourceExpected={sourceExpected}
        sourceCapabilities={sourceCapabilities}
        onEdit={onEdit}
      />
    ) : null;

  return (
    <div id={`el-${article.id}`}>
      <ArticleTitle
        id={article.id}
        number={article.number}
        title={article.title}
        changed={changedIds.has(article.id)}
        busy={busy}
        sourceExpected={sourceExpected}
        sourceCapabilities={sourceCapabilities}
        onEdit={onEdit}
      />
      <div className="mt-1.5 space-y-1">
        {article.paragraphs.map((paragraph, position) => (
          <Fragment key={paragraph.id}>
            {addAt(position)}
            <ParagraphNode
              p={paragraph}
              depth={0}
              changedIds={changedIds}
              sourceLookup={sourceLookup}
              busy={busy}
              sourceExpected={sourceExpected}
              sourceCapabilities={sourceCapabilities}
              onEdit={onEdit}
            />
          </Fragment>
        ))}
        {addAt(article.paragraphs.length)}
        {sourceExpected && !addCapability.allowed && (
          <div className="ml-8 mt-1">
            <CapabilityButton
              className={actionBtn}
              disabled
              title={sourceCapabilityTitle(addCapability, "")}
            >
              + Add top-level provision (read-only)
            </CapabilityButton>
          </div>
        )}
      </div>
    </div>
  );
}

function PartBlock({
  part,
  changedIds,
  sourceLookup,
  busy,
  sourceExpected,
  sourceCapabilities,
  onEdit,
}: {
  part: DocPart;
  changedIds: ReadonlySet<string>;
  sourceLookup: ReadonlyMap<string, string>;
  busy: boolean;
  sourceExpected: boolean;
  sourceCapabilities: SourceCapabilitiesState | null;
  onEdit: (ops: EditOp[]) => void;
}) {
  const replaceCapability = sourceCapability(
    sourceCapabilities,
    sourceExpected,
    part.id,
    "replace_text",
  );
  return (
    <div>
      <p
        className={`text-[13px] font-semibold ${
          sourceExpected && !replaceCapability.allowed
            ? "border-l-2 border-paper-edge bg-paper-edge/15 pl-1"
            : ""
        }`}
      >
        {part.title}
        <ReadOnlyBadge
          capability={replaceCapability}
          sourceExpected={sourceExpected}
        />
      </p>
      {part.articles.length === 0 ? (
        <p className="mt-2 text-xs text-paper-dim italic">(No articles yet.)</p>
      ) : (
        <div className="mt-3 space-y-4">
          {part.articles.map((article) => (
            <ArticleBlock
              key={article.id}
              article={article}
              changedIds={changedIds}
              sourceLookup={sourceLookup}
              busy={busy}
              sourceExpected={sourceExpected}
              sourceCapabilities={sourceCapabilities}
              onEdit={onEdit}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Compare (diff) mode — read-only render of a SectionDiff (Batch 5).  */
/* ins runs green/underline, del runs red/strikethrough; inserted and  */
/* deleted whole blocks carry a left border + badge.                   */
/* ------------------------------------------------------------------ */

function DiffRunSpans({ runs }: { runs: DiffRun[] }) {
  return (
    <>
      {runs.map((run, i) =>
        run.op === "equal" ? (
          <span key={i}>{run.text}</span>
        ) : (
          <span key={i} className={run.op === "ins" ? "diff-ins" : "diff-del"}>
            {run.text}
          </span>
        ),
      )}
    </>
  );
}

/** The text of one element rendered per its change kind. */
function DiffText({ e, upper = false }: { e: ElementDiff; upper?: boolean }) {
  const cls = upper ? "uppercase" : undefined;
  if (e.kind === "inserted") {
    return <span className={`diff-ins ${cls ?? ""}`}>{e.cur_text}</span>;
  }
  if (e.kind === "deleted") {
    return <span className={`diff-del ${cls ?? ""}`}>{e.base_text}</span>;
  }
  if (e.kind === "changed" && e.runs) {
    return (
      <span className={cls}>
        <DiffRunSpans runs={e.runs} />
      </span>
    );
  }
  return <span className={cls}>{e.cur_text}</span>;
}

function DiffBadge({ kind }: { kind: ElementDiff["kind"] }) {
  if (kind === "inserted")
    return (
      <span className="ml-2 rounded border border-[#5f7d33]/50 bg-[#e7f0d8] px-1 py-px align-middle text-[9px] font-semibold tracking-wide text-[#4a6327] uppercase">
        new
      </span>
    );
  if (kind === "deleted")
    return (
      <span className="ml-2 rounded border border-[#b23b32]/50 bg-[#f6ded9] px-1 py-px align-middle text-[9px] font-semibold tracking-wide text-[#8f2f27] uppercase">
        removed
      </span>
    );
  return null;
}

function diffBlockClass(kind: ElementDiff["kind"]): string {
  if (kind === "inserted") return "diff-block-ins";
  if (kind === "deleted") return "diff-block-del";
  return "";
}

function DiffElementRow({ e }: { e: ElementDiff }) {
  if (e.node_type === "section") {
    const numberChanged = e.number_base !== e.number_cur;
    return (
      <div className="text-center">
        <p className="rounded text-[13px] font-semibold tracking-wide">
          SECTION{" "}
          {numberChanged ? (
            <>
              {e.number_base && <span className="diff-del">{e.number_base}</span>}
              {e.number_base && e.number_cur ? " " : ""}
              {e.number_cur && <span className="diff-ins">{e.number_cur}</span>}
            </>
          ) : (
            e.number_cur || "[TBD]"
          )}
        </p>
        <p className="mt-1 rounded text-[13px] font-semibold tracking-wide uppercase">
          {e.kind === "changed" && e.runs ? (
            <DiffRunSpans runs={e.runs} />
          ) : (
            e.cur_text || "[TBD: section title]"
          )}
        </p>
      </div>
    );
  }
  if (e.node_type === "part") {
    return <p className="mt-8 text-[13px] font-semibold">{e.cur_text}</p>;
  }
  if (e.node_type === "article") {
    const number = e.ref_cur || e.ref_base;
    return (
      <p
        className={`mt-3 flex items-baseline gap-2 rounded px-1 text-[13px] font-semibold ${diffBlockClass(
          e.kind,
        )}`}
      >
        <span>{number}</span>
        <span>
          <DiffText e={e} upper />
        </span>
        <DiffBadge kind={e.kind} />
      </p>
    );
  }
  // paragraph
  return (
    <div
      className={`mt-1 flex gap-2 rounded px-1 py-0.5 ${diffBlockClass(e.kind)}`}
      style={{ marginLeft: `${e.depth * 1.4}rem` }}
    >
      <span className="w-6 shrink-0 text-right">{e.label}</span>
      <span className="min-w-0 flex-1">
        <DiffText e={e} />
        <DiffBadge kind={e.kind} />
      </span>
    </div>
  );
}

const statusLabels: Record<string, string> = {
  confirmed: "confirmed",
  assumed: "assumed",
  needs_input: "needs input",
  imported: "imported",
};

function DiffDocument({ diff }: { diff: SectionDiff }) {
  const section = diff.elements.find((e) => e.node_type === "section");
  const sectionNumber = section?.number_cur ?? "";
  return (
    <div className="mx-auto max-w-2xl rounded-xl border border-paper-edge bg-paper px-10 py-12 text-[13px] leading-relaxed text-paper-ink shadow-[0_2px_16px_rgba(0,0,0,0.25)]">
      {diff.elements.map((e, i) => (
        <DiffElementRow key={`${e.uid}-${e.kind}-${i}`} e={e} />
      ))}
      <p className="mt-10 text-center text-[13px] font-semibold tracking-wide">
        END OF SECTION {sectionNumber}
      </p>

      {diff.status_changes.length > 0 && (
        <div className="mt-8 border-t border-paper-edge pt-3">
          <p className="text-[11px] font-medium tracking-wide text-paper-dim uppercase">
            Status changes ({diff.status_changes.length})
          </p>
          <ul className="mt-1.5 flex flex-wrap gap-1.5">
            {diff.status_changes.map((sc) => (
              <li
                key={sc.uid}
                className="rounded border border-paper-edge bg-white/60 px-1.5 py-0.5 text-[11px] text-paper-dim"
                title="Provenance status changed (not a text edit — no redline mark)"
              >
                <span className="font-medium text-paper-ink tabular-nums">
                  {sc.ref}
                </span>{" "}
                {statusLabels[sc.status_base] ?? sc.status_base} →{" "}
                {statusLabels[sc.status_cur] ?? sc.status_cur}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export default function SpecDocument({
  doc,
  changedIds,
  sourceLookup = new Map(),
  busy = false,
  sourceExpected = false,
  sourceCapabilities = null,
  onEdit = () => {},
  diff = null,
}: {
  doc: SpecDoc;
  changedIds: ReadonlySet<string>;
  sourceLookup?: ReadonlyMap<string, string>;
  busy?: boolean;
  sourceExpected?: boolean;
  sourceCapabilities?: SourceCapabilitiesState | null;
  onEdit?: (ops: EditOp[]) => void;
  diff?: SectionDiff | null;
}) {
  if (diff) {
    return <DiffDocument diff={diff} />;
  }
  const sectionReplaceCapability = sourceCapability(
    sourceCapabilities,
    sourceExpected,
    "sec",
    "replace_text",
  );
  return (
    <div className="mx-auto max-w-2xl rounded-xl border border-paper-edge bg-paper px-10 py-12 text-[13px] leading-relaxed text-paper-ink shadow-[0_2px_16px_rgba(0,0,0,0.25)]">
      <div
        id="el-sec"
        className={`text-center ${
          sourceExpected && !sectionReplaceCapability.allowed
            ? "rounded border-l-2 border-paper-edge bg-paper-edge/15 py-1"
            : ""
        }`}
      >
        <p
          className={`rounded text-[13px] font-semibold tracking-wide ${
            changedIds.has("sec") ? "changed-block" : ""
          }`}
        >
          SECTION {doc.section.number || "[TBD]"}
        </p>
        <p
          className={`mt-1 rounded text-[13px] font-semibold tracking-wide uppercase ${
            changedIds.has("sec") ? "changed-block" : ""
          }`}
        >
          {doc.section.title || "[TBD: section title]"}
          <ReadOnlyBadge
            capability={sectionReplaceCapability}
            sourceExpected={sourceExpected}
          />
        </p>
      </div>

      <div className="mt-10 space-y-8">
        {doc.parts.map((part) => (
          <PartBlock
            key={part.id}
            part={part}
            changedIds={changedIds}
            sourceLookup={sourceLookup}
            busy={busy}
            sourceExpected={sourceExpected}
            sourceCapabilities={sourceCapabilities}
            onEdit={onEdit}
          />
        ))}
      </div>

      <p className="mt-10 text-center text-[13px] font-semibold tracking-wide">
        END OF SECTION {doc.section.number || ""}
      </p>
    </div>
  );
}
