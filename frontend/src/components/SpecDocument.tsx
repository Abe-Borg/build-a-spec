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
import {
  Fragment,
  createContext,
  useCallback,
  useContext,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type CSSProperties,
  type ReactNode,
} from "react";
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type Announcements,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import {
  sourceCapability,
  sourceCapabilityTitle,
  sourceEditOpDecision,
} from "../lib/sourceCapabilities";
import { sourceChipTitle } from "../lib/sourceChip";
import {
  adjacentAllowedPosition,
  canAddChildParagraph,
  editorAllowedPositions,
  hasAlternatePosition,
  paragraphSubtreeSize,
  siblingDropPosition,
  submitExclusiveEdit,
} from "../lib/structuralEditing";
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
  TemplateOrigin,
} from "../types";

const TBD_SPLIT = /(\[TBD:[^\]]*\])/g;
const TemplateSeedContext = createContext<{
  name: string;
  ids: ReadonlySet<string>;
}>({ name: "", ids: new Set() });

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

function StatusBadge({
  status,
  blockId,
}: {
  status: DocParagraph["status"];
  blockId: string;
}) {
  const templateSeed = useContext(TemplateSeedContext);
  const fromTemplate = status === "imported" && templateSeed.ids.has(blockId);
  const style = fromTemplate
    ? {
        css: "border-[#7d63ad]/50 bg-[#eee8f8] text-[#654892]",
        label: "template starter",
      }
    : badgeStyles[status];
  if (!style) return null;
  return (
    <span
      className={`ml-2 inline-block rounded border px-1 py-px align-middle text-[9px] font-semibold tracking-wide uppercase ${style.css}`}
      title={
        fromTemplate
          ? `Reusable starter: ${templateSeed.name || "personal template"}`
          : undefined
      }
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
  return (
    <span
      className="ml-1.5 inline-block cursor-help align-middle text-[10px] text-[#7a90b8]"
      title={sourceChipTitle(itemId, lookup)}
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

type SubmitEdit = (ops: EditOp[]) => boolean;

const dragInstructions = {
  draggable:
    "To pick up an item, press Space. Use the up and down arrow keys to move it among its siblings. Press Space again to drop it, or Escape to cancel.",
};

function sortableAnnouncements(
  itemLabel: string,
  ids: readonly string[],
  allowedPositionsFor: (id: string) => readonly number[],
): Announcements {
  const positionOf = (id: string | number) => ids.indexOf(String(id)) + 1;
  const destinationOf = (
    activeId: string | number,
    overId: string | number | null,
  ) =>
    siblingDropPosition(
      ids,
      String(activeId),
      overId === null ? null : String(overId),
      allowedPositionsFor(String(activeId)),
    );
  return {
    onDragStart({ active }) {
      return `Picked up ${itemLabel} at position ${positionOf(active.id)} of ${ids.length}.`;
    },
    onDragOver({ active, over }) {
      if (!over || active.id === over.id) return undefined;
      const destination = destinationOf(active.id, over.id);
      return destination === null
        ? `${itemLabel} cannot be moved to that sibling position.`
        : `${itemLabel} will move to position ${destination + 1} of ${ids.length}.`;
    },
    onDragEnd({ active, over }) {
      const destination = destinationOf(active.id, over?.id ?? null);
      return destination === null
        ? `${itemLabel} was not moved.`
        : `${itemLabel} was dropped at position ${destination + 1} of ${ids.length}.`;
    },
    onDragCancel() {
      return `${itemLabel} movement cancelled.`;
    },
  };
}

function SortableSiblingList({
  ids,
  itemLabel,
  busy,
  allowedPositionsFor,
  onMove,
  children,
}: {
  ids: readonly string[];
  itemLabel: string;
  busy: boolean;
  allowedPositionsFor: (id: string) => readonly number[];
  onMove: (id: string, position: number) => void;
  children: ReactNode;
}) {
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );
  const handleDragEnd = ({ active, over }: DragEndEvent) => {
    if (busy) return;
    const activeId = String(active.id);
    const overId = over ? String(over.id) : null;
    const position = siblingDropPosition(
      ids,
      activeId,
      overId,
      allowedPositionsFor(activeId),
    );
    if (position !== null) onMove(activeId, position);
  };
  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      accessibility={{
        screenReaderInstructions: dragInstructions,
        announcements: sortableAnnouncements(
          itemLabel,
          ids,
          allowedPositionsFor,
        ),
      }}
      onDragEnd={handleDragEnd}
    >
      <SortableContext items={[...ids]} strategy={verticalListSortingStrategy}>
        {children}
      </SortableContext>
    </DndContext>
  );
}

type SortableState = ReturnType<typeof useSortable>;

function sortableItemStyle(sortable: SortableState): CSSProperties {
  const transform = sortable.transform;
  return {
    transform: transform
      ? `translate3d(${transform.x}px, ${transform.y}px, 0) scaleX(${transform.scaleX}) scaleY(${transform.scaleY})`
      : undefined,
    transition: sortable.transition,
    position: "relative",
    zIndex: sortable.isDragging ? 10 : undefined,
  };
}

function DragHandle({
  sortable,
  disabled,
  label,
  title,
}: {
  sortable: SortableState;
  disabled: boolean;
  label: string;
  title: string;
}) {
  return (
    <span className="inline-flex" title={disabled ? title : undefined}>
      <button
        ref={sortable.setActivatorNodeRef}
        {...sortable.attributes}
        {...sortable.listeners}
        type="button"
        className="structure-drag-handle"
        data-capability="document.rearrange"
        disabled={disabled}
        aria-label={label}
        title={disabled ? undefined : title}
      >
        <span aria-hidden="true">⠿</span>
      </button>
    </span>
  );
}

/** Hover toolbar for a paragraph: confirm / edit / delete. */
function RowActions({
  canConfirm,
  busy,
  replaceCapability,
  deleteCapability,
  moveCapability,
  currentPosition,
  allowedMovePositions,
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
  replaceCapability: SourceOperationCapability;
  deleteCapability: SourceOperationCapability;
  moveCapability: SourceOperationCapability;
  currentPosition: number;
  allowedMovePositions: readonly number[];
  statusCapability: SourceOperationCapability;
  confirming: boolean;
  onConfirm: () => void;
  onEdit: () => void;
  onDelete: () => void;
  onMove: (position: number) => void;
  onCancelDelete: () => void;
}) {
  const moveUpPosition = adjacentAllowedPosition(
    currentPosition,
    "up",
    allowedMovePositions,
  );
  const moveDownPosition = adjacentAllowedPosition(
    currentPosition,
    "down",
    allowedMovePositions,
  );
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
    <span
      className="pointer-events-none ml-1 inline-flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover:pointer-events-auto group-hover:opacity-100 group-focus-within:pointer-events-auto group-focus-within:opacity-100"
      data-capability="document.edit"
    >
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
      <CapabilityButton
        className={actionBtn}
        onClick={() => {
          if (moveCapability.allowed && moveUpPosition !== null) {
            onMove(moveUpPosition);
          }
        }}
        disabled={busy || !moveCapability.allowed || moveUpPosition === null}
        title={
          !moveCapability.allowed
            ? moveTitle
            : moveUpPosition === null
              ? "No allowed position exists above this provision."
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
        disabled={busy || !moveCapability.allowed || moveDownPosition === null}
        title={
          !moveCapability.allowed
            ? moveTitle
            : moveDownPosition === null
              ? "No allowed position exists below this provision."
              : `Move to sibling position ${moveDownPosition + 1}`
        }
        aria-label="Move provision down"
      >
        ↓
      </CapabilityButton>
    </span>
  );
}

function ParagraphNode({
  p,
  depth,
  position,
  siblingCount,
  changedIds,
  sourceLookup,
  busy,
  sourceExpected,
  sourceCapabilities,
  onEdit,
}: {
  p: DocParagraph;
  depth: number;
  position: number;
  siblingCount: number;
  changedIds: ReadonlySet<string>;
  sourceLookup: ReadonlyMap<string, string>;
  busy: boolean;
  sourceExpected: boolean;
  sourceCapabilities: SourceCapabilitiesState | null;
  onEdit: SubmitEdit;
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
  const allowedMovePositions = editorAllowedPositions(
    moveCapability,
    sourceExpected,
    siblingCount - 1,
  );
  const canMove =
    !busy && hasAlternatePosition(position, allowedMovePositions);
  const sortable = useSortable({ id: p.id, disabled: !canMove });
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
    return onEdit(ops);
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
    <div
      ref={sortable.setNodeRef}
      className={`structure-sortable ${sortable.isDragging ? "is-dragging" : ""}`}
      style={sortableItemStyle(sortable)}
    >
      <div
        id={`el-${p.id}`}
        data-capability={
          sourceExpected
            ? "document.source-permissions"
            : "document.provenance"
        }
        className={`group flex gap-2 rounded px-1 py-0.5 ${
          changedIds.has(p.id) ? "changed-block" : ""
        } ${
          sourceExpected && !replaceCapability.allowed
            ? "border-l-2 border-paper-edge bg-paper-edge/15"
            : ""
        }`}
        style={{ marginLeft: `${depth * 1.4}rem` }}
      >
        <DragHandle
          sortable={sortable}
          disabled={!canMove}
          label={`Reorder provision ${p.label}`}
          title={
            !moveCapability.allowed
              ? sourceCapabilityTitle(moveCapability, "")
              : canMove
                ? "Drag to reorder among siblings. Keyboard: Space, arrow keys, Space."
                : "No other allowed sibling position is available."
          }
        />
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
            <StatusBadge status={p.status} blockId={p.id} />
            <SourceChip itemId={p.source_item_id} lookup={sourceLookup} />
            <ReadOnlyBadge
              capability={replaceCapability}
              sourceExpected={sourceExpected}
            />
            <RowActions
              canConfirm={p.status === "assumed" || p.status === "imported"}
              busy={busy}
              replaceCapability={replaceCapability}
              deleteCapability={deleteCapability}
              moveCapability={moveCapability}
              currentPosition={position}
              allowedMovePositions={allowedMovePositions}
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
      <ParagraphList
        paragraphs={p.children}
        parentId={p.id}
        depth={depth + 1}
        allowAdd={canAddChildParagraph(depth)}
        changedIds={changedIds}
        sourceLookup={sourceLookup}
        busy={busy}
        sourceExpected={sourceExpected}
        sourceCapabilities={sourceCapabilities}
        onEdit={onEdit}
      />
    </div>
  );
}

function ParagraphList({
  paragraphs,
  parentId,
  depth,
  allowAdd,
  changedIds,
  sourceLookup,
  busy,
  sourceExpected,
  sourceCapabilities,
  onEdit,
}: {
  paragraphs: DocParagraph[];
  parentId: string;
  depth: number;
  allowAdd: boolean;
  changedIds: ReadonlySet<string>;
  sourceLookup: ReadonlyMap<string, string>;
  busy: boolean;
  sourceExpected: boolean;
  sourceCapabilities: SourceCapabilitiesState | null;
  onEdit: SubmitEdit;
}) {
  const ids = paragraphs.map((paragraph) => paragraph.id);
  const addCapability = sourceCapability(
    sourceCapabilities,
    sourceExpected,
    parentId,
    "add_paragraph",
  );
  const allowedAddPositions = allowAdd
    ? editorAllowedPositions(
        addCapability,
        sourceExpected,
        paragraphs.length,
      )
    : [];
  const allowedAddSet = new Set(allowedAddPositions);
  const movePositionsFor = (id: string) => {
    const capability = sourceCapability(
      sourceCapabilities,
      sourceExpected,
      id,
      "move",
    );
    return editorAllowedPositions(
      capability,
      sourceExpected,
      paragraphs.length - 1,
    );
  };
  const addAt = (position: number) =>
    allowedAddSet.has(position) ? (
      <AddParagraphControl
        parentId={parentId}
        position={position}
        depth={depth}
        busy={busy}
        sourceExpected={sourceExpected}
        sourceCapabilities={sourceCapabilities}
        onEdit={onEdit}
      />
    ) : null;

  if (paragraphs.length === 0 && allowedAddPositions.length === 0) {
    if (!allowAdd || !sourceExpected || addCapability.allowed) return null;
    return (
      <div style={{ marginLeft: `${depth * 1.4 + 2}rem` }}>
        <CapabilityButton
          className={actionBtn}
          disabled
          title={sourceCapabilityTitle(addCapability, "")}
        >
          + Add subparagraph (read-only)
        </CapabilityButton>
      </div>
    );
  }

  return (
    <SortableSiblingList
      ids={ids}
      itemLabel={depth === 0 ? "provision" : "subparagraph"}
      busy={busy}
      allowedPositionsFor={movePositionsFor}
      onMove={(id, position) => {
        onEdit([{ action: "move", target_id: id, position }]);
      }}
    >
      <div className="space-y-1">
        {paragraphs.map((paragraph, position) => (
          <Fragment key={paragraph.id}>
            {addAt(position)}
            <ParagraphNode
              p={paragraph}
              depth={depth}
              position={position}
              siblingCount={paragraphs.length}
              changedIds={changedIds}
              sourceLookup={sourceLookup}
              busy={busy}
              sourceExpected={sourceExpected}
              sourceCapabilities={sourceCapabilities}
              onEdit={onEdit}
            />
          </Fragment>
        ))}
        {addAt(paragraphs.length)}
        {allowAdd && sourceExpected && !addCapability.allowed && (
          <div style={{ marginLeft: `${depth * 1.4 + 2}rem` }}>
            <CapabilityButton
              className={actionBtn}
              disabled
              title={sourceCapabilityTitle(addCapability, "")}
            >
              + Add {depth === 0 ? "provision" : "subparagraph"} (read-only)
            </CapabilityButton>
          </div>
        )}
      </div>
    </SortableSiblingList>
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
  leading,
  actions,
}: {
  id: string;
  number: string;
  title: string;
  changed: boolean;
  busy: boolean;
  sourceExpected: boolean;
  sourceCapabilities: SourceCapabilitiesState | null;
  onEdit: SubmitEdit;
  leading: ReactNode;
  actions: ReactNode;
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
      if (!busy && decision.allowed && onEdit([op])) {
        setEditing(false);
      }
    };
    return (
      <p className="flex items-center gap-2 text-[13px] font-semibold">
        {leading}
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
      {leading}
      {number}&nbsp;&nbsp;
      <span className="uppercase">{title}</span>
      <ReadOnlyBadge
        capability={replaceCapability}
        sourceExpected={sourceExpected}
      />
      <CapabilityButton
        className={`${actionBtn} ml-1 hidden group-hover:inline-block`}
        data-capability="document.edit"
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
      {actions}
    </p>
  );
}

function AddParagraphControl({
  parentId,
  position,
  depth,
  busy,
  sourceExpected,
  sourceCapabilities,
  onEdit,
}: {
  parentId: string;
  position: number;
  depth: number;
  busy: boolean;
  sourceExpected: boolean;
  sourceCapabilities: SourceCapabilitiesState | null;
  onEdit: SubmitEdit;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const decision = sourceEditOpDecision(
    sourceCapabilities,
    sourceExpected,
    {
      action: "add_paragraph",
      target_id: parentId,
      position,
      text: "New provision",
    },
  );
  const title = sourceCapabilityTitle(
    decision,
    `Add a ${depth === 0 ? "top-level provision" : "subparagraph"} at sibling position ${position + 1}`,
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
      target_id: parentId,
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
    if (onEdit([op])) setEditing(false);
  };

  if (editing) {
    return (
      <div
        className="my-1 rounded border border-dashed border-paper-edge bg-white/35 px-2 py-1"
        style={{ marginLeft: `${depth * 1.4 + 2}rem` }}
      >
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
          placeholder={depth === 0 ? "New top-level provision" : "New subparagraph"}
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
    <div
      className="my-0.5 flex items-center gap-1 text-[10px] text-paper-dim/80"
      data-capability="document.insert"
      style={{ marginLeft: `${depth * 1.4 + 2}rem` }}
    >
      <span className="h-px min-w-4 flex-1 bg-paper-edge/50" />
      <CapabilityButton
        className={`${actionBtn} whitespace-nowrap`}
        onClick={start}
        disabled={busy || !decision.allowed}
        title={title}
      >
        + Add {depth === 0 ? "provision" : "subparagraph"} here
      </CapabilityButton>
      <span className="h-px min-w-4 flex-1 bg-paper-edge/50" />
    </div>
  );
}

function ArticleBlock({
  article,
  position,
  siblingCount,
  changedIds,
  sourceLookup,
  busy,
  sourceExpected,
  sourceCapabilities,
  onEdit,
}: {
  article: DocArticle;
  position: number;
  siblingCount: number;
  changedIds: ReadonlySet<string>;
  sourceLookup: ReadonlyMap<string, string>;
  busy: boolean;
  sourceExpected: boolean;
  sourceCapabilities: SourceCapabilitiesState | null;
  onEdit: SubmitEdit;
}) {
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const moveCapability = sourceCapability(
    sourceCapabilities,
    sourceExpected,
    article.id,
    "move",
  );
  const deleteCapability = sourceCapability(
    sourceCapabilities,
    sourceExpected,
    article.id,
    "delete",
  );
  const allowedMovePositions = editorAllowedPositions(
    moveCapability,
    sourceExpected,
    siblingCount - 1,
  );
  const canMove =
    !busy && hasAlternatePosition(position, allowedMovePositions);
  const sortable = useSortable({ id: article.id, disabled: !canMove });
  const upPosition = adjacentAllowedPosition(
    position,
    "up",
    allowedMovePositions,
  );
  const downPosition = adjacentAllowedPosition(
    position,
    "down",
    allowedMovePositions,
  );
  const provisionCount = paragraphSubtreeSize(article.paragraphs);
  const moveTitle = sourceCapabilityTitle(moveCapability, "Move this article");
  const structureActions = confirmingDelete ? (
    <span className="ml-2 inline-flex items-center gap-1 text-[10px] font-normal">
      <span className="text-[#a03d31]">
        {provisionCount > 0
          ? `Delete article and ${provisionCount} provision${provisionCount === 1 ? "" : "s"}?`
          : "Delete article?"}
      </span>
      <CapabilityButton
        className={actionBtn}
        disabled={busy || !deleteCapability.allowed}
        title={sourceCapabilityTitle(deleteCapability, "Confirm delete")}
        onClick={() => {
          if (onEdit([{ action: "delete", target_id: article.id }])) {
            setConfirmingDelete(false);
          }
        }}
      >
        ✓
      </CapabilityButton>
      <button
        type="button"
        className={actionBtn}
        onClick={() => setConfirmingDelete(false)}
        title="Keep article"
      >
        ✕
      </button>
    </span>
  ) : (
    <span className="pointer-events-none ml-1 inline-flex items-center gap-0.5 opacity-0 transition-opacity group-hover:pointer-events-auto group-hover:opacity-100 group-focus-within:pointer-events-auto group-focus-within:opacity-100">
      <CapabilityButton
        className={actionBtn}
        data-capability="document.edit"
        disabled={busy || !deleteCapability.allowed}
        title={sourceCapabilityTitle(
          deleteCapability,
          provisionCount > 0
            ? `Delete article and its ${provisionCount} provisions`
            : "Delete article",
        )}
        onClick={() => setConfirmingDelete(true)}
        aria-label={`Delete article ${article.number}`}
      >
        🗑
      </CapabilityButton>
      <CapabilityButton
        className={actionBtn}
        disabled={busy || !moveCapability.allowed || upPosition === null}
        title={
          !moveCapability.allowed
            ? moveTitle
            : upPosition === null
              ? "No allowed position exists above this article."
              : `Move to article position ${upPosition + 1}`
        }
        onClick={() => {
          if (upPosition !== null) {
            onEdit([
              { action: "move", target_id: article.id, position: upPosition },
            ]);
          }
        }}
        aria-label={`Move article ${article.number} up`}
      >
        ↑
      </CapabilityButton>
      <CapabilityButton
        className={actionBtn}
        disabled={busy || !moveCapability.allowed || downPosition === null}
        title={
          !moveCapability.allowed
            ? moveTitle
            : downPosition === null
              ? "No allowed position exists below this article."
              : `Move to article position ${downPosition + 1}`
        }
        onClick={() => {
          if (downPosition !== null) {
            onEdit([
              { action: "move", target_id: article.id, position: downPosition },
            ]);
          }
        }}
        aria-label={`Move article ${article.number} down`}
      >
        ↓
      </CapabilityButton>
    </span>
  );

  return (
    <div
      ref={sortable.setNodeRef}
      id={`el-${article.id}`}
      data-capability="document.rearrange"
      className={`structure-sortable ${sortable.isDragging ? "is-dragging" : ""}`}
      style={sortableItemStyle(sortable)}
    >
      <ArticleTitle
        id={article.id}
        number={article.number}
        title={article.title}
        changed={changedIds.has(article.id)}
        busy={busy}
        sourceExpected={sourceExpected}
        sourceCapabilities={sourceCapabilities}
        onEdit={onEdit}
        leading={
          <DragHandle
            sortable={sortable}
            disabled={!canMove}
            label={`Reorder article ${article.number}`}
            title={
              !moveCapability.allowed
                ? sourceCapabilityTitle(moveCapability, "")
                : canMove
                  ? "Drag to reorder within this PART. Keyboard: Space, arrow keys, Space."
                  : "No other allowed article position is available."
            }
          />
        }
        actions={structureActions}
      />
      <div className="mt-1.5">
        <ParagraphList
          paragraphs={article.paragraphs}
          parentId={article.id}
          depth={0}
          allowAdd
          changedIds={changedIds}
          sourceLookup={sourceLookup}
          busy={busy}
          sourceExpected={sourceExpected}
          sourceCapabilities={sourceCapabilities}
          onEdit={onEdit}
        />
      </div>
    </div>
  );
}

function AddArticleControl({
  partId,
  position,
  busy,
  sourceExpected,
  sourceCapabilities,
  onEdit,
}: {
  partId: string;
  position: number;
  busy: boolean;
  sourceExpected: boolean;
  sourceCapabilities: SourceCapabilitiesState | null;
  onEdit: SubmitEdit;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const decision = sourceEditOpDecision(
    sourceCapabilities,
    sourceExpected,
    {
      action: "add_article",
      target_id: partId,
      position,
      text: "New article",
    },
  );
  const save = () => {
    const text = draft.trim();
    if (!text || busy) return;
    const op: EditOp = {
      action: "add_article",
      target_id: partId,
      position,
      text,
    };
    const currentDecision = sourceEditOpDecision(
      sourceCapabilities,
      sourceExpected,
      { ...op },
    );
    if (currentDecision.allowed && onEdit([op])) setEditing(false);
  };

  if (editing) {
    return (
      <div className="my-1 rounded border border-dashed border-paper-edge bg-white/35 px-2 py-1">
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
          placeholder="New article title"
          maxLength={200}
          className="w-full rounded border border-paper-edge bg-white/70 px-1.5 py-0.5 text-[12px] font-semibold uppercase text-paper-ink outline-none focus:border-[#c08457]"
        />
        <span className="mt-1 flex items-center gap-2 text-[11px] text-paper-dim">
          <CapabilityButton
            className={actionBtn}
            onClick={save}
            disabled={busy || !decision.allowed || !draft.trim()}
            title={sourceCapabilityTitle(decision, "Add article (Enter)")}
          >
            Add
          </CapabilityButton>
          <button
            type="button"
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
    <div
      className="my-1 flex items-center gap-1 text-[10px] text-paper-dim/80"
      data-capability="document.insert"
    >
      <span className="h-px min-w-4 flex-1 bg-paper-edge/50" />
      <CapabilityButton
        className={`${actionBtn} whitespace-nowrap`}
        onClick={() => {
          if (busy || !decision.allowed) return;
          setDraft("");
          setEditing(true);
        }}
        disabled={busy || !decision.allowed}
        title={sourceCapabilityTitle(
          decision,
          `Add an article at position ${position + 1}`,
        )}
      >
        + Add article here
      </CapabilityButton>
      <span className="h-px min-w-4 flex-1 bg-paper-edge/50" />
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
  onEdit: SubmitEdit;
}) {
  const replaceCapability = sourceCapability(
    sourceCapabilities,
    sourceExpected,
    part.id,
    "replace_text",
  );
  const addCapability = sourceCapability(
    sourceCapabilities,
    sourceExpected,
    part.id,
    "add_article",
  );
  const allowedAddPositions = editorAllowedPositions(
    addCapability,
    sourceExpected,
    part.articles.length,
  );
  const allowedAddSet = new Set(allowedAddPositions);
  const articleIds = part.articles.map((article) => article.id);
  const movePositionsFor = (id: string) =>
    editorAllowedPositions(
      sourceCapability(
        sourceCapabilities,
        sourceExpected,
        id,
        "move",
      ),
      sourceExpected,
      part.articles.length - 1,
    );
  const addAt = (position: number) =>
    allowedAddSet.has(position) ? (
      <AddArticleControl
        partId={part.id}
        position={position}
        busy={busy}
        sourceExpected={sourceExpected}
        sourceCapabilities={sourceCapabilities}
        onEdit={onEdit}
      />
    ) : null;
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
      <SortableSiblingList
        ids={articleIds}
        itemLabel="article"
        busy={busy}
        allowedPositionsFor={movePositionsFor}
        onMove={(id, position) => {
          onEdit([{ action: "move", target_id: id, position }]);
        }}
      >
        <div className="mt-3 space-y-4">
          {part.articles.length === 0 && allowedAddPositions.length === 0 && (
            <p className="text-xs text-paper-dim italic">(No articles yet.)</p>
          )}
          {part.articles.map((article, position) => (
            <Fragment key={article.id}>
              {addAt(position)}
              <ArticleBlock
                article={article}
                position={position}
                siblingCount={part.articles.length}
                changedIds={changedIds}
                sourceLookup={sourceLookup}
                busy={busy}
                sourceExpected={sourceExpected}
                sourceCapabilities={sourceCapabilities}
                onEdit={onEdit}
              />
            </Fragment>
          ))}
          {addAt(part.articles.length)}
          {sourceExpected && !addCapability.allowed && (
            <CapabilityButton
              className={actionBtn}
              disabled
              title={sourceCapabilityTitle(addCapability, "")}
            >
              + Add article (read-only)
            </CapabilityButton>
          )}
        </div>
      </SortableSiblingList>
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
    <div
      className="mx-auto max-w-2xl rounded-xl border border-paper-edge bg-paper px-10 py-12 text-[13px] leading-relaxed text-paper-ink shadow-[0_2px_16px_rgba(0,0,0,0.25)]"
      data-capability="document.structure"
    >
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

/**
 * The section number and title, editable in place.
 *
 * Both ride one `replace` on the `sec` target — the same op the model uses —
 * so this is a normal undoable version, not a special case. Mirrors
 * `ArticleTitle`: hover-revealed pencil, Enter saves, Escape cancels, and the
 * server's own denial sentence stays hoverable on a disabled control.
 *
 * Exported because a blank document renders `ArtifactPanel`'s `EmptyState`
 * instead of this component, and naming the section is exactly what you want
 * to do first on a blank page.
 */
export function SectionHeader({
  number,
  title,
  changed,
  capability,
  sourceExpected,
  busy,
  onEdit,
}: {
  number: string;
  title: string;
  changed: boolean;
  capability: SourceOperationCapability;
  sourceExpected: boolean;
  busy: boolean;
  onEdit: (ops: EditOp[]) => boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [draftNumber, setDraftNumber] = useState(number);
  const [draftTitle, setDraftTitle] = useState(title);

  const save = () => {
    const op: EditOp = {
      action: "replace",
      target_id: "sec",
      text: draftTitle.trim(),
      numbering: draftNumber.trim(),
    };
    // `capability` is already the server's `replace_text` verdict for `sec`,
    // which is exactly the operation this op performs — no second mapping
    // needed. The backend still validates the submitted value either way.
    if (!busy && capability.allowed && onEdit([op])) {
      setEditing(false);
    }
  };

  const headingClass = `rounded text-[13px] font-semibold tracking-wide ${
    changed ? "changed-block" : ""
  }`;

  if (editing) {
    return (
      <div className="mx-auto flex max-w-md flex-col items-stretch gap-1.5">
        <input
          autoFocus
          aria-label="Section number"
          placeholder="Section number (e.g. 21 13 13)"
          value={draftNumber}
          disabled={busy}
          onChange={(e) => setDraftNumber(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") setEditing(false);
            if (e.key === "Enter") {
              e.preventDefault();
              save();
            }
          }}
          className="rounded border border-paper-edge bg-white/70 px-1.5 py-0.5 text-center text-[13px] font-semibold tracking-wide text-paper-ink outline-none focus:border-[#c08457]"
        />
        <input
          aria-label="Section title"
          placeholder="Section title"
          value={draftTitle}
          disabled={busy}
          onChange={(e) => setDraftTitle(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") setEditing(false);
            if (e.key === "Enter") {
              e.preventDefault();
              save();
            }
          }}
          className="rounded border border-paper-edge bg-white/70 px-1.5 py-0.5 text-center text-[13px] font-semibold tracking-wide text-paper-ink uppercase outline-none focus:border-[#c08457]"
        />
        <div className="flex items-center justify-center gap-2">
          <CapabilityButton
            className={actionBtn}
            onClick={save}
            disabled={busy}
            title="Save (Enter)"
          >
            Save
          </CapabilityButton>
          <CapabilityButton
            className={actionBtn}
            onClick={() => setEditing(false)}
            disabled={busy}
            title="Cancel (Esc)"
          >
            Cancel
          </CapabilityButton>
        </div>
      </div>
    );
  }

  return (
    <div className="group">
      <p className={headingClass}>SECTION {number || "[TBD]"}</p>
      <p className={`mt-1 uppercase ${headingClass}`}>
        {title || "[TBD: section title]"}
        <ReadOnlyBadge capability={capability} sourceExpected={sourceExpected} />
        <CapabilityButton
          className={`${actionBtn} ml-1 hidden group-hover:inline-block`}
          onClick={() => {
            if (busy || !capability.allowed) return;
            setDraftNumber(number);
            setDraftTitle(title);
            setEditing(true);
          }}
          disabled={busy || !capability.allowed}
          title={sourceCapabilityTitle(
            capability,
            "Edit section number and title",
          )}
        >
          ✏️
        </CapabilityButton>
      </p>
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
  templateOrigin = null,
  onEdit = () => {},
  diff = null,
  unstructuredImport = false,
}: {
  doc: SpecDoc;
  changedIds: ReadonlySet<string>;
  sourceLookup?: ReadonlyMap<string, string>;
  busy?: boolean;
  sourceExpected?: boolean;
  sourceCapabilities?: SourceCapabilitiesState | null;
  templateOrigin?: TemplateOrigin | null;
  onEdit?: (ops: EditOp[]) => void | Promise<void>;
  diff?: SectionDiff | null;
  /** The import found no SectionFormat structure (see ImportReport). */
  unstructuredImport?: boolean;
}) {
  const editInFlightRef = useRef(false);
  const [editInFlight, setEditInFlight] = useState(false);
  const submitEdit = useCallback(
    (ops: EditOp[]): boolean => {
      if (busy) return false;
      setEditInFlight(true);
      return submitExclusiveEdit(
        editInFlightRef,
        () => onEdit(ops),
        () => setEditInFlight(false),
      );
    },
    [busy, onEdit],
  );
  if (diff) {
    return <DiffDocument diff={diff} />;
  }
  const sectionReplaceCapability = sourceCapability(
    sourceCapabilities,
    sourceExpected,
    "sec",
    "replace_text",
  );
  // An unstructured import has no header to show — printing "SECTION [TBD]"
  // over a memo asserts a section the file never had. The placeholders are
  // still right for a spec being drafted from scratch, and they come back the
  // moment a header exists, so this turns on the document's own state rather
  // than latching at import.
  const hasHeader = Boolean(doc.section.number || doc.section.title);
  const bareImport = unstructuredImport && !hasHeader;
  const editorBusy = busy || editInFlight;
  // Empty parts are pure scaffolding for such a document; once it is being
  // turned into a spec, "(No articles yet.)" is a useful drafting cue again.
  const visibleParts = bareImport
    ? doc.parts.filter((part) => part.articles.length > 0)
    : doc.parts;
  const templateSeed = {
    name: templateOrigin?.name ?? "",
    ids: new Set(templateOrigin?.seed_block_ids ?? []),
  };
  return (
    <TemplateSeedContext.Provider value={templateSeed}>
    <div className="mx-auto max-w-2xl rounded-xl border border-paper-edge bg-paper px-10 py-12 text-[13px] leading-relaxed text-paper-ink shadow-[0_2px_16px_rgba(0,0,0,0.25)]">
      {/* `el-sec` stays on whichever block renders — it is the tour's section
          anchor and the target of a header edit. */}
      {bareImport ? (
        <div
          id="el-sec"
          className="rounded border border-dashed border-paper-edge bg-paper-edge/10 px-3 py-2 text-[12px] leading-snug text-paper-dim"
        >
          <span className="font-semibold text-paper-ink">
            Imported document — no spec structure found.
          </span>{" "}
          No SECTION number, PART heading, or numbered article was found in
          this file, so none is shown. The content below is in document order
          under a placeholder article; your original file is kept exactly and
          can be downloaded unchanged. Ask the assistant to turn it into a spec
          section when you are ready.
        </div>
      ) : (
        <div
          id="el-sec"
          data-capability="document.section-header"
          className={`text-center ${
            sourceExpected && !sectionReplaceCapability.allowed
              ? "rounded border-l-2 border-paper-edge bg-paper-edge/15 py-1"
              : ""
          }`}
        >
          <SectionHeader
            number={doc.section.number}
            title={doc.section.title}
            changed={changedIds.has("sec")}
            capability={sectionReplaceCapability}
            sourceExpected={sourceExpected}
            busy={editorBusy}
            onEdit={submitEdit}
          />
        </div>
      )}

      <div className="mt-10 space-y-8">
        {visibleParts.map((part) => (
          <PartBlock
            key={part.id}
            part={part}
            changedIds={changedIds}
            sourceLookup={sourceLookup}
            busy={editorBusy}
            sourceExpected={sourceExpected}
            sourceCapabilities={sourceCapabilities}
            onEdit={submitEdit}
          />
        ))}
      </div>

      {!bareImport && (
        <p className="mt-10 text-center text-[13px] font-semibold tracking-wide">
          END OF SECTION {doc.section.number || ""}
        </p>
      )}
    </div>
    </TemplateSeedContext.Provider>
  );
}
