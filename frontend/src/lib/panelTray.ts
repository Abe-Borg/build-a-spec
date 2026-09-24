/**
 * The document panel's panel tray: the collapsible panels stacked under the
 * paper (Review, Research, Final QC, Issues, Open items, Waiting on you,
 * Project, Project facts, Standards, Documents).
 *
 * Stacked, they can take the whole panel — ten bars run to ~360px before a
 * single one is opened, and two that open themselves when something first
 * lands in them left the specification a 48px strip at the default window
 * size. So the user can fold the whole tray away in one click, and leave out
 * the panels they do not use; `ArtifactPanel` also caps the tray at half the
 * panel's height, so an opened panel scrolls inside the tray instead of
 * squeezing the paper.
 *
 * This module owns the vocabulary and the pure rules. The server stores the
 * layout as opaque short ids (`backend/ui_preferences.py` — on disk, because
 * the packaged app's WebView keeps no browser storage between launches) and
 * this module drops any id it does not know, so adding a panel is a change to
 * PANEL_IDS and to the tray's slots, never to the server.
 */
import type { UiPreferencesPayload } from "../types";

/** Every panel, in the order the tray stacks them. */
export const PANEL_IDS = [
  "review",
  "research",
  "qc",
  "issues",
  "open-items",
  "followups",
  "project",
  "project-facts",
  "standards",
  "documents",
] as const;

export type PanelId = (typeof PANEL_IDS)[number];

/** The name each panel's own bar shows, so the menu and the bar agree. */
export const PANEL_LABELS: Record<PanelId, string> = {
  review: "Review",
  research: "Research",
  qc: "Final QC",
  issues: "Issues",
  "open-items": "Open items",
  followups: "Waiting on you",
  project: "Project",
  "project-facts": "Project facts",
  standards: "Standards",
  documents: "Documents",
};

/** The user's layout choice, as App keeps it. */
export interface PanelTrayPrefs {
  /** The whole tray is folded away, so the paper has the panel's height. */
  folded: boolean;
  /** Panels left out of the tray, in PANEL_IDS order, each once. */
  hidden: readonly PanelId[];
}

export const DEFAULT_PANEL_TRAY: PanelTrayPrefs = { folded: false, hidden: [] };

const KNOWN = new Set<string>(PANEL_IDS);

export function isPanelId(value: unknown): value is PanelId {
  return typeof value === "string" && KNOWN.has(value);
}

/** Known ids only, each once, in stacking order. */
function normalizeHidden(values: Iterable<unknown>): PanelId[] {
  const wanted = new Set<PanelId>();
  for (const value of values) if (isPanelId(value)) wanted.add(value);
  return PANEL_IDS.filter((id) => wanted.has(id));
}

/**
 * Read what the server returned. An id this build does not know (a panel a
 * newer build added, or a hand-edited file) is dropped rather than trusted,
 * and only a real boolean folds the tray.
 */
export function panelTrayFromApi(
  raw: Partial<Record<keyof UiPreferencesPayload, unknown>> | null | undefined,
): PanelTrayPrefs {
  return {
    folded: raw?.panels_folded === true,
    hidden: Array.isArray(raw?.hidden_panels)
      ? normalizeHidden(raw.hidden_panels)
      : [],
  };
}

export function panelTrayToApi(prefs: PanelTrayPrefs): UiPreferencesPayload {
  return {
    panels_folded: prefs.folded,
    hidden_panels: normalizeHidden(prefs.hidden),
  };
}

export function setTrayFolded(
  prefs: PanelTrayPrefs,
  folded: boolean,
): PanelTrayPrefs {
  return { ...prefs, folded };
}

/** Leave one panel out of the tray, or put it back. */
export function setPanelHidden(
  prefs: PanelTrayPrefs,
  id: PanelId,
  hidden: boolean,
): PanelTrayPrefs {
  const next = new Set(prefs.hidden);
  if (hidden) next.add(id);
  else next.delete(id);
  return { ...prefs, hidden: normalizeHidden(next) };
}

export function showEveryPanel(prefs: PanelTrayPrefs): PanelTrayPrefs {
  return { ...prefs, hidden: [] };
}

/** What the tray renders from the stored choice. */
export interface EffectivePanelTray {
  /** False until the saved layout has been read: the tray stays out of view
   *  rather than flashing open and then folding for a user who folded it. */
  ready: boolean;
  folded: boolean;
  hidden: ReadonlySet<PanelId>;
  /** The guided tour owns the layout: every panel is open, the controls are
   *  disabled, and the stored choice comes back when the tour ends. */
  locked: boolean;
}

/**
 * The layout to render. While the tour runs every panel is shown whatever
 * the user chose: its steps spotlight panels, and a panel hidden with
 * `display: none` is still FOUND by the tour's anchor lookup — it would
 * spotlight a zero-size box at the corner of the window rather than degrade
 * to the honest "not available" card.
 */
export function effectivePanelTray(
  prefs: PanelTrayPrefs | null,
  locked: boolean,
): EffectivePanelTray {
  if (locked) {
    return { ready: true, folded: false, hidden: new Set(), locked: true };
  }
  if (!prefs) {
    return { ready: false, folded: false, hidden: new Set(), locked: false };
  }
  return {
    ready: true,
    folded: prefs.folded,
    hidden: new Set(prefs.hidden),
    locked: false,
  };
}

/** The counts a folded tray still shows, so folding hides no signal. */
export interface PanelAttention {
  /** Imported + assumed blocks still to review. */
  review: number;
  openItems: number;
  /** Open "Waiting on you" items. */
  waiting: number;
  /** Advisory lint issues. */
  issues: number;
}

/**
 * The phrases a folded tray shows: each non-zero count whose panel the user
 * has not left out, in stacking order. A panel left out is a choice already
 * made about that panel; a folded tray is only a request for room.
 */
export function foldedAttention(
  attention: PanelAttention,
  hidden: ReadonlySet<PanelId>,
): string[] {
  const phrases: string[] = [];
  const add = (id: PanelId, count: number, phrase: string) => {
    if (count > 0 && !hidden.has(id)) phrases.push(`${count} ${phrase}`);
  };
  add("review", attention.review, "to review");
  add("issues", attention.issues, attention.issues === 1 ? "issue" : "issues");
  add(
    "open-items",
    attention.openItems,
    attention.openItems === 1 ? "open item" : "open items",
  );
  add("followups", attention.waiting, "waiting on you");
  return phrases;
}
