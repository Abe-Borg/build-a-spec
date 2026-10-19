/**
 * Edit scope — how far the assistant may change a section on its own
 * (2026-10-19). The definition the assistant and Final QC work from is
 * `backend/edit_scope.py`; this file holds only what the composer's Edits
 * switch and the document panel's badge say about it, and must not
 * contradict that list (frontend/tests/editScope.test.ts reads both).
 *
 * - `extensive` (the default): everything essential plus full latitude —
 *   rewording for clarity, reorganizing, the writing policy throughout.
 * - `essentials`: for a client who wants their own specification changed as
 *   little as possible — existing text changes only for an essential
 *   reason, scope gaps included.
 *
 * The choice is the section's own: the server keeps it, the project file
 * saves it, and Next section carries it (`POST /api/session/edit-scope`).
 */
import type { EditScope } from "../types";

export type { EditScope };

export const DEFAULT_EDIT_SCOPE: EditScope = "extensive";

/** The server's value, or the default for an older server or a bad value. */
export function parseEditScope(value: unknown): EditScope {
  return value === "essentials" ? "essentials" : DEFAULT_EDIT_SCOPE;
}

export interface EditScopeOption {
  id: EditScope;
  label: string;
  /** One line for the panel badge. */
  short: string;
  /** Hover text: what the assistant will and will not do in this mode. */
  detail: string;
}

export const EDIT_SCOPE_OPTIONS: readonly EditScopeOption[] = [
  {
    id: "essentials",
    label: "Essentials only",
    short: "Essential edits only",
    detail:
      "Change only what must change for this project: code and standard " +
      "conflicts, outdated editions, scope gaps (work the project needs " +
      "that the section does not cover), content that cannot apply to this " +
      "project, wrong project values, conflicts and broken references, " +
      "technical errors, explanatory prose and notes, and unissuable " +
      "leftovers. The client's wording, structure, article order and style " +
      "are kept — no rewording for clarity, no reorganizing, no tightening. " +
      "A compliant choice the assistant would make differently is asked " +
      "about, never edited. Anything you ask for in chat is still done.",
  },
  {
    id: "extensive",
    label: "Extensive",
    short: "Extensive edits allowed",
    detail:
      "Everything Essentials covers, scope gaps included, plus full " +
      "latitude: rewrite for clarity and enforceability, reorganize " +
      "articles, and apply the writing policy throughout.",
  },
] as const;

export function editScopeOption(scope: EditScope): EditScopeOption {
  return EDIT_SCOPE_OPTIONS.find((option) => option.id === scope) ?? EDIT_SCOPE_OPTIONS[1];
}
