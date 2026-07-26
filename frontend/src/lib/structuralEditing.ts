import type { DocParagraph, SourceOperationCapability } from "../types";

/** The semantic model and exporter support four paragraph levels. */
export const MAX_PARAGRAPH_LEVELS = 4;

export interface EditSubmissionLock {
  current: boolean;
}

/** Synchronously claim one manual-edit slot and release it when work settles. */
export function submitExclusiveEdit(
  lock: EditSubmissionLock,
  submit: () => void | Promise<void>,
  onSettled: () => void,
): boolean {
  if (lock.current) return false;
  lock.current = true;
  let result: void | Promise<void>;
  try {
    result = submit();
  } catch {
    lock.current = false;
    onSettled();
    return false;
  }
  void Promise.resolve(result)
    .catch(() => undefined)
    .finally(() => {
      lock.current = false;
      onSettled();
    });
  return true;
}

function integerRange(lastPosition: number): number[] {
  if (!Number.isInteger(lastPosition) || lastPosition < 0) return [];
  return Array.from({ length: lastPosition + 1 }, (_, index) => index);
}

function explicitSourcePositions(
  capability: SourceOperationCapability,
): number[] {
  const positions = new Set<number>();
  const add = (position: number) => {
    if (Number.isInteger(position) && position >= 0) positions.add(position);
  };
  for (const position of capability.allowed_positions ?? []) add(position);
  for (const placement of capability.placements ?? []) {
    for (const position of placement.allowed_positions ?? []) add(position);
  }
  return [...positions].sort((a, b) => a - b);
}

/**
 * Return positions the editor may actually offer.
 *
 * Native documents expose every position. Source-backed documents use only
 * the server's explicit `allowed_positions`; min/max fields are deliberately
 * ignored because they are descriptive, not authority.
 */
export function editorAllowedPositions(
  capability: SourceOperationCapability,
  sourceExpected: boolean,
  lastPosition: number,
): number[] {
  if (!capability.allowed) return [];
  if (!sourceExpected) return integerRange(lastPosition);
  return explicitSourcePositions(capability).filter(
    (position) => position <= lastPosition,
  );
}

/** Resolve a sortable drop to the backend's final sibling index. */
export function siblingDropPosition(
  siblingIds: readonly string[],
  activeId: string,
  overId: string | null,
  allowedPositions: readonly number[],
): number | null {
  if (!overId || activeId === overId) return null;
  const activePosition = siblingIds.indexOf(activeId);
  const finalPosition = siblingIds.indexOf(overId);
  if (activePosition < 0 || finalPosition < 0) return null;
  return allowedPositions.includes(finalPosition) ? finalPosition : null;
}

/** Find the nearest server-authorized destination for arrow-key fallbacks. */
export function adjacentAllowedPosition(
  currentPosition: number,
  direction: "up" | "down",
  allowedPositions: readonly number[],
): number | null {
  if (direction === "up") {
    const candidates = allowedPositions.filter(
      (position) => position < currentPosition,
    );
    return candidates.length ? candidates[candidates.length - 1] : null;
  }
  return (
    allowedPositions.find((position) => position > currentPosition) ?? null
  );
}

export function hasAlternatePosition(
  currentPosition: number,
  allowedPositions: readonly number[],
): boolean {
  return allowedPositions.some((position) => position !== currentPosition);
}

export function canAddChildParagraph(depth: number): boolean {
  return Number.isInteger(depth) && depth >= 0 && depth + 1 < MAX_PARAGRAPH_LEVELS;
}

export function paragraphSubtreeSize(paragraphs: readonly DocParagraph[]): number {
  let count = 0;
  for (const paragraph of paragraphs) {
    count += 1 + paragraphSubtreeSize(paragraph.children);
  }
  return count;
}
