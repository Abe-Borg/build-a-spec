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
import { useState } from "react";
import type { DocParagraph, DocPart, EditOp, SpecDoc } from "../types";

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
  "rounded px-1 text-[12px] leading-none text-paper-dim transition-colors hover:text-paper-ink disabled:pointer-events-none disabled:opacity-30";

/** Hover toolbar for a paragraph: confirm / edit / delete. */
function RowActions({
  canConfirm,
  busy,
  confirming,
  onConfirm,
  onEdit,
  onDelete,
  onCancelDelete,
}: {
  canConfirm: boolean;
  busy: boolean;
  confirming: boolean;
  onConfirm: () => void;
  onEdit: () => void;
  onDelete: () => void;
  onCancelDelete: () => void;
}) {
  if (confirming) {
    return (
      <span className="ml-1 inline-flex shrink-0 items-center gap-1 text-[11px]">
        <span className="text-[#a03d31]">Delete?</span>
        <button className={actionBtn} onClick={onDelete} title="Confirm delete">
          ✓
        </button>
        <button className={actionBtn} onClick={onCancelDelete} title="Keep">
          ✕
        </button>
      </span>
    );
  }
  return (
    <span className="ml-1 hidden shrink-0 items-center gap-0.5 group-hover:inline-flex">
      {canConfirm && (
        <button
          className={actionBtn}
          onClick={onConfirm}
          disabled={busy}
          title="Confirm this block (mark reviewed)"
        >
          ✓
        </button>
      )}
      <button
        className={actionBtn}
        onClick={onEdit}
        disabled={busy}
        title="Edit this provision"
      >
        ✏️
      </button>
      <button
        className={actionBtn}
        onClick={onDelete}
        disabled={busy}
        title="Delete this provision"
      >
        🗑
      </button>
    </span>
  );
}

function ParagraphNode({
  p,
  depth,
  changedIds,
  sourceLookup,
  busy,
  onEdit,
}: {
  p: DocParagraph;
  depth: number;
  changedIds: ReadonlySet<string>;
  sourceLookup: ReadonlyMap<string, string>;
  busy: boolean;
  onEdit: (ops: EditOp[]) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(p.text);
  const [confirming, setConfirming] = useState(false);

  const startEdit = () => {
    setDraft(p.text);
    setEditing(true);
  };
  const save = () => {
    const text = draft.trim();
    setEditing(false);
    if (!text || text === p.text) return;
    // User-authored text is confirmed; preserve the research provenance.
    onEdit([
      {
        action: "replace",
        target_id: p.id,
        text,
        status: "confirmed",
        source_item_id: p.source_item_id,
      },
    ]);
  };

  return (
    <>
      <div
        id={`el-${p.id}`}
        className={`group flex gap-2 rounded px-1 py-0.5 ${
          changedIds.has(p.id) ? "changed-block" : ""
        }`}
        style={{ marginLeft: `${depth * 1.4}rem` }}
      >
        <span className="w-6 shrink-0 text-right">{p.label}</span>
        {editing ? (
          <span className="min-w-0 flex-1">
            <textarea
              autoFocus
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") setEditing(false);
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) save();
              }}
              rows={Math.min(6, draft.split("\n").length + 1)}
              className="w-full resize-y rounded border border-paper-edge bg-white/70 px-1.5 py-1 text-[13px] leading-relaxed text-paper-ink outline-none focus:border-[#c08457]"
            />
            <span className="mt-1 flex items-center gap-2 text-[11px] text-paper-dim">
              <button className={actionBtn} onClick={save} title="Save (Ctrl/Cmd+Enter)">
                Save
              </button>
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
            <RowActions
              canConfirm={p.status === "assumed" || p.status === "imported"}
              busy={busy}
              confirming={confirming}
              onConfirm={() =>
                onEdit([
                  { action: "set_status", target_id: p.id, status: "confirmed" },
                ])
              }
              onEdit={startEdit}
              onDelete={() => {
                if (confirming) {
                  setConfirming(false);
                  onEdit([{ action: "delete", target_id: p.id }]);
                } else {
                  setConfirming(true);
                }
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
  onEdit,
}: {
  id: string;
  number: string;
  title: string;
  changed: boolean;
  busy: boolean;
  onEdit: (ops: EditOp[]) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(title);
  if (editing) {
    const save = () => {
      const next = draft.trim();
      setEditing(false);
      if (next && next !== title) {
        onEdit([{ action: "replace", target_id: id, text: next }]);
      }
    };
    return (
      <p className="flex items-center gap-2 text-[13px] font-semibold">
        {number}&nbsp;&nbsp;
        <input
          autoFocus
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") setEditing(false);
            if (e.key === "Enter") save();
          }}
          className="flex-1 rounded border border-paper-edge bg-white/70 px-1.5 py-0.5 text-[13px] font-semibold uppercase text-paper-ink outline-none focus:border-[#c08457]"
        />
        <button className={actionBtn} onClick={save} title="Save (Enter)">
          Save
        </button>
      </p>
    );
  }
  return (
    <p
      className={`group flex items-center rounded px-1 text-[13px] font-semibold ${
        changed ? "changed-block" : ""
      }`}
    >
      {number}&nbsp;&nbsp;
      <span className="uppercase">{title}</span>
      <button
        className={`${actionBtn} ml-1 hidden group-hover:inline-block`}
        onClick={() => {
          setDraft(title);
          setEditing(true);
        }}
        disabled={busy}
        title="Edit article title"
      >
        ✏️
      </button>
    </p>
  );
}

function PartBlock({
  part,
  changedIds,
  sourceLookup,
  busy,
  onEdit,
}: {
  part: DocPart;
  changedIds: ReadonlySet<string>;
  sourceLookup: ReadonlyMap<string, string>;
  busy: boolean;
  onEdit: (ops: EditOp[]) => void;
}) {
  return (
    <div>
      <p className="text-[13px] font-semibold">{part.title}</p>
      {part.articles.length === 0 ? (
        <p className="mt-2 text-xs text-paper-dim italic">(No articles yet.)</p>
      ) : (
        <div className="mt-3 space-y-4">
          {part.articles.map((article) => (
            <div key={article.id} id={`el-${article.id}`}>
              <ArticleTitle
                id={article.id}
                number={article.number}
                title={article.title}
                changed={changedIds.has(article.id)}
                busy={busy}
                onEdit={onEdit}
              />
              <div className="mt-1.5 space-y-1">
                {article.paragraphs.map((p) => (
                  <ParagraphNode
                    key={p.id}
                    p={p}
                    depth={0}
                    changedIds={changedIds}
                    sourceLookup={sourceLookup}
                    busy={busy}
                    onEdit={onEdit}
                  />
                ))}
              </div>
            </div>
          ))}
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
  onEdit = () => {},
}: {
  doc: SpecDoc;
  changedIds: ReadonlySet<string>;
  sourceLookup?: ReadonlyMap<string, string>;
  busy?: boolean;
  onEdit?: (ops: EditOp[]) => void;
}) {
  return (
    <div className="mx-auto max-w-2xl rounded-xl border border-paper-edge bg-paper px-10 py-12 text-[13px] leading-relaxed text-paper-ink shadow-[0_2px_16px_rgba(0,0,0,0.25)]">
      <div id="el-sec" className="text-center">
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
