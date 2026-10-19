/**
 * Edit scope — how much latitude the assistant has to change a specification.
 *
 * UI PREVIEW ONLY. Nothing here reaches the backend yet: the choice lives in
 * App state, is not saved in the project file, and does not change the
 * prompt, the edit tool, Final QC or the adapt pass. It exists so the owner
 * can judge the controls before the behavior is built.
 *
 * - `extensive`: today's behavior — the assistant may rewrite, reorganize and
 *   tighten the whole section under the writing policy.
 * - `essentials`: for a client who wants their own specification touched as
 *   little as possible — only what must change for this project to be
 *   correct; their wording, structure and order are kept.
 */
export type EditScope = "extensive" | "essentials";

export const DEFAULT_EDIT_SCOPE: EditScope = "extensive";

export interface EditScopeOption {
  id: EditScope;
  label: string;
  /** One line for the chip and the panel badge. */
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
      "conflicts, outdated editions, project-specific values, and outright " +
      "errors. The client's wording, structure, article order and style are " +
      "kept — no rewording for clarity, no reorganizing, no tightening.",
  },
  {
    id: "extensive",
    label: "Extensive",
    short: "Extensive edits allowed",
    detail:
      "Full latitude: rewrite for clarity and enforceability, reorganize " +
      "articles, apply the writing policy throughout, and fill gaps.",
  },
] as const;

export function editScopeOption(scope: EditScope): EditScopeOption {
  return EDIT_SCOPE_OPTIONS.find((option) => option.id === scope) ?? EDIT_SCOPE_OPTIONS[1];
}
