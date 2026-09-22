/**
 * "Project" — the project this section belongs to, and its other sections
 * (Project workspace Phase 2).
 *
 * A project lives in one folder: its .basproject beside one .baspec per
 * section (decision D1). The native shell discovers that folder from a file
 * it just saved or opened — `DocPayload.project_home`, never persisted — and
 * this panel lists the project's section registry (the session's link joined
 * with the brief on disk), marks the section open now, and opens any sibling
 * by NUMBER. It never holds or sends a path: the server resolves the number
 * to a file it refuses to find outside the folder.
 *
 * Sits directly above "Project facts" and borrows its shape: collapsible,
 * `openNonce` for the tour, and it renders only when there is something to
 * show — the session is linked to a project or has a home. The one extra
 * condition is the desktop shell: a browser session keeps the file relay
 * (Open / New section in an existing project) and shows no panel, because
 * it can never have a folder and the panel's promise would be a dead end
 * there. The tour is the exception — its practice copy is linked but lives
 * in no folder, and the panel is what the step points at.
 */
import { useEffect, useRef, useState } from "react";

import { projectSections } from "../lib/api";
import type {
  ProjectHome,
  ProjectLink,
  ProjectSectionRow,
  ProjectSectionsPayload,
} from "../types";

const smallBtn =
  "text-ink-faint underline-offset-2 hover:text-accent hover:underline disabled:opacity-40";

/** Whether the native shell's folder bridge exists. pywebview injects its
 *  api object asynchronously, so a first render can miss it; the effect in
 *  the panel re-checks on `pywebviewready`. */
const hasDesktopShell = () =>
  typeof window !== "undefined" && !!window.pywebview?.api?.bind_project_home;

function plural(count: number, word: string): string {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

function rowDetail(row: ProjectSectionRow): string {
  if (!row.in_registry) {
    return "not in the project brief yet — it joins when this section exports one";
  }
  const parts = [
    row.exported_at ? `exported ${row.exported_at.slice(0, 10)}` : "not exported",
    plural(row.fact_count, "fact"),
    plural(row.research_rounds, "research round"),
  ];
  return parts.join(" · ");
}

export default function ProjectPanel({
  link,
  home,
  currentNumber,
  busy,
  tutorialActive,
  refreshNonce,
  openNonce,
  onOpenSection,
  onNextSection,
}: {
  link: ProjectLink | null;
  home: ProjectHome | null;
  /** The open document's section number ("" while unnamed). */
  currentNumber: string;
  /** A turn is streaming, a file is loading, or another replacement runs. */
  busy: boolean;
  tutorialActive: boolean;
  /** Bumped by App after a save or a brief export, so the listing re-reads
   *  the folder (a save can move the section into — or out of — it). */
  refreshNonce: number;
  /** Tour "ensure open" nonce (the drawer idiom). */
  openNonce?: number;
  /** App runs the save gate, then the one-click open. */
  onOpenSection: (number: string) => void;
  /** Phase 1's dialog, owned by ArtifactPanel. */
  onNextSection: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [listing, setListing] = useState<ProjectSectionsPayload | null>(null);
  const [error, setError] = useState("");
  const [desktopShell, setDesktopShell] = useState(hasDesktopShell);
  // Newest request wins: a slow listing resolving after a newer one must not
  // repaint rows from before a save or an open.
  const request = useRef(0);
  // Opens itself the first time a home is known; a later collapse is the
  // user's decision and is respected (the Project facts precedent).
  const introduced = useRef(false);

  useEffect(() => {
    if (desktopShell) return;
    const ready = () => setDesktopShell(hasDesktopShell());
    window.addEventListener("pywebviewready", ready);
    return () => window.removeEventListener("pywebviewready", ready);
  }, [desktopShell]);

  useEffect(() => {
    if (openNonce) setExpanded(true);
  }, [openNonce]);

  useEffect(() => {
    if (introduced.current || !home) return;
    introduced.current = true;
    setExpanded(true);
  }, [home]);

  const visible = (link !== null || home !== null) && (desktopShell || tutorialActive);
  // What the listing depends on, by value: the doc payload hands down a new
  // link object on every refresh, and a refetch per payload would be a poll
  // in disguise. The panel refetches when the project, the folder or the
  // open section actually changes, after a save, and when the tour opens it.
  const dependsOn = JSON.stringify([link, home, currentNumber]);

  useEffect(() => {
    if (!visible) {
      setListing(null);
      return;
    }
    const id = ++request.current;
    projectSections()
      .then((payload) => {
        if (request.current !== id) return;
        setListing(payload);
        setError("");
      })
      .catch((failure: unknown) => {
        if (request.current !== id) return;
        setError(failure instanceof Error ? failure.message : String(failure));
      });
  }, [visible, dependsOn, refreshNonce, openNonce]);

  if (!visible) return null;

  const rows = listing?.sections ?? [];
  // The prop is authoritative (the doc payload, or the save that just ran);
  // the listing's copy can trail it by one fetch.
  const where = home;
  const name = listing?.project?.name || link?.name || "This project";
  const briefDate = listing?.brief_updated_at?.slice(0, 10) ?? "";
  const unnamed = !currentNumber.trim();
  const unregistered = listing?.unregistered ?? [];
  const warnings = listing?.warnings ?? [];

  return (
    <div
      className="border-t border-edge bg-bg/70 px-5 py-2"
      data-tour="project-panel"
      data-capability="project.sections"
    >
      <button
        className="flex w-full items-baseline gap-2 text-left text-[11px] text-ink-faint transition-colors hover:text-ink-dim"
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
        aria-controls="project-sections-list"
        title="The project this section belongs to, and its other sections"
      >
        <span className="shrink-0 font-medium tracking-wide uppercase">Project</span>
        <span className="truncate text-ink-dim">{name}</span>
        <span className="shrink-0">
          · {plural(rows.filter((row) => row.in_registry).length, "section")}
        </span>
        <span className="ml-auto shrink-0">{expanded ? "▾" : "▸"}</span>
      </button>

      {expanded && (
        <div id="project-sections-list" className="mt-1.5 space-y-1.5">
          {where ? (
            <p className="px-1 text-[10px] leading-relaxed text-ink-faint">
              Lives in <span className="break-all text-ink-dim">{where.folder}</span>
              {" · "}brief <span className="text-ink-dim">{where.brief_name}</span>
              {briefDate && ` · brief last updated ${briefDate}`}
            </p>
          ) : (
            <p className="px-1 text-[10px] leading-relaxed text-ink-faint">
              Not in a project folder — save this section beside its project brief to open
              its sections from here.
            </p>
          )}
          {unnamed && (
            <p className="px-1 text-[10px] text-ink-faint">
              This section has no number yet, so the list cannot mark it as the one you are in.
            </p>
          )}

          <ul className="max-h-64 space-y-0.5 overflow-y-auto">
            {rows.map((row) => (
              <li
                key={`${row.number}:${row.in_registry ? "r" : "c"}`}
                className={
                  "rounded px-1 py-1 " +
                  (row.is_current ? "border border-accent/40 bg-accent/10" : "")
                }
                data-section-number={row.number}
              >
                <div className="flex items-baseline gap-1.5 text-xs">
                  <span className="shrink-0 font-medium text-ink">{row.number}</span>
                  <span className="min-w-0 flex-1 truncate text-ink-dim">{row.title}</span>
                  {row.ready && (
                    <span
                      className="shrink-0 text-[10px] text-accent"
                      title="Exported as issue-ready"
                    >
                      ✓ ready
                    </span>
                  )}
                  <span className="shrink-0 text-[10px]">
                    {row.is_current ? (
                      <span className="rounded-full border border-accent/50 px-1.5 py-px text-accent">
                        current
                      </span>
                    ) : !where ? null : row.present ? (
                      !tutorialActive && (
                        <button
                          className="rounded border border-edge px-1.5 py-0.5 text-ink-dim hover:border-accent hover:text-accent disabled:opacity-40"
                          onClick={() => onOpenSection(row.number)}
                          disabled={busy}
                          title={
                            busy
                              ? "Wait for the current work to finish"
                              : `Open section ${row.number} from ${row.file_name} — you are offered to save this section first`
                          }
                          data-capability="project.open-section"
                        >
                          Open
                        </button>
                      )
                    ) : (
                      <span className="text-ink-faint" title={row.file_name || undefined}>
                        file not found beside the brief
                      </span>
                    )}
                  </span>
                </div>
                <span className="block text-[10px] text-ink-faint">{rowDetail(row)}</span>
              </li>
            ))}
            {rows.length === 0 && !error && (
              <li className="px-1 text-[11px] text-ink-faint">
                {listing ? "No sections recorded yet." : "Reading the project…"}
              </li>
            )}
          </ul>

          {unregistered.length > 0 && (
            <p
              className="px-1 text-[10px] text-ink-faint"
              title="Section files in the folder that the project brief does not list. Open them with the ordinary Open button."
            >
              Not in the registry: {unregistered.join(", ")}
            </p>
          )}
          {warnings.map((warning) => (
            <p key={warning} className="px-1 text-[10px] text-warn">
              ⚠ {warning}
            </p>
          ))}
          {error && (
            <p role="alert" className="px-1 text-[10px] text-err">
              {error}
            </p>
          )}

          {!tutorialActive && (
            <button
              className={"px-1 text-[10px] " + smallBtn}
              onClick={onNextSection}
              disabled={busy}
              title="Leave this section and open the next one of the same project — carrying the profile, editions, research, references and facts, never this conversation or document. You are offered to save first."
              data-capability="project.next-section"
            >
              Next section →
            </button>
          )}
        </div>
      )}
    </div>
  );
}
