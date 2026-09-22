/**
 * Next section in one click (v1.20.0).
 *
 * The project brief that shipped in v1.17.0 was a file relay: export the
 * .basproject from the finished section, New session → New section in an
 * existing project → pick the file. This dialog is that flow with the file
 * removed — the server builds the brief in memory from the session being
 * left behind and seeds the next section in one transaction
 * (`POST /api/project/next-section`). The save gate runs first in App (the
 * section being left is the user's to save); this dialog only collects the
 * choice and hands it up.
 *
 * What it offers: the module's sibling catalog with the sections the project
 * already drafted greyed (shown, never hidden — a list that silently omits
 * 21 13 13 reads as a module that never offered it), a typed header for a
 * section the catalog does not list (the only option on the generic module,
 * which declares none), and "leave it unnamed" for a section that will start
 * from an office master — a named page counts as content and the master
 * import refuses it, so the choice is made here rather than discovered at
 * the Import button.
 */
import { useEffect, useState } from "react";
import type { NextSectionOptions, NextSectionRequest, ProjectBriefManifest } from "../types";
import { nextSectionOptions } from "../lib/api";
import { ModalShell, primaryBtn, quietBtn } from "./ModalShell";

const fieldClass =
  "w-full rounded-md border border-edge bg-raised px-2 py-1 text-sm text-ink placeholder:text-ink-faint focus:border-accent focus:outline-none";

type Choice = { kind: "catalog"; number: string } | { kind: "custom" } | { kind: "unnamed" };

/** What the brief would carry, one line per asset — this dialog's receipt,
 *  shown BEFORE the seed so a missing research profile or a reference past
 *  the cap is a decision rather than a surprise. */
function BriefContents({ manifest }: { manifest: ProjectBriefManifest }) {
  const research = manifest.research;
  const rows: [string, string][] = [
    ["Project", manifest.name || "Untitled project"],
    [
      "Profile",
      manifest.profile.line
        ? `${manifest.profile.line}${manifest.profile.complete ? "" : " (incomplete)"}`
        : "not recorded",
    ],
    ["Project type", manifest.project_type || "not recorded"],
    [
      "Editions",
      manifest.edition_overrides.count
        ? manifest.edition_overrides.standards.join("; ")
        : "none recorded",
    ],
    [
      "Research",
      research
        ? `${research.items} finding${research.items === 1 ? "" : "s"} over ${research.rounds} round${
            research.rounds === 1 ? "" : "s"
          }, last ${research.last_research_date || "—"}`
        : "none",
    ],
    [
      "References",
      manifest.references.length
        ? `${manifest.references.length} document${
            manifest.references.length === 1 ? "" : "s"
          } (${manifest.reference_tokens.toLocaleString()} tokens of text): ${manifest.references
            .map((doc) => doc.title)
            .join("; ")}`
        : "none",
    ],
    [
      "Facts",
      `${manifest.facts.active} active${
        manifest.facts.superseded ? `, ${manifest.facts.superseded} retired` : ""
      }`,
    ],
    [
      "Sections",
      manifest.sections.map((s) => `${s.number} ${s.title}`.trim()).join("; ") ||
        "this one",
    ],
  ];
  return (
    <div className="mt-3 rounded-lg border border-edge bg-raised/50 p-3">
      <dl className="grid gap-x-3 gap-y-1 text-xs sm:grid-cols-[7rem_1fr]">
        {rows.map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="text-ink-faint">{label}</dt>
            <dd className="min-w-0 break-words text-ink-dim">{value}</dd>
          </div>
        ))}
      </dl>
      {manifest.warnings.length > 0 && (
        <ul className="mt-2 space-y-1 border-t border-edge/60 pt-2 text-[11px] text-warn">
          {manifest.warnings.map((warning) => (
            <li key={warning}>⚠ {warning}</li>
          ))}
        </ul>
      )}
    </div>
  );
}


export default function NextSectionDialog({
  onClose,
  onStart,
}: {
  onClose: () => void;
  /** Hand the choice up; App runs the save gate and the request. */
  onStart: (opts: NextSectionRequest) => void;
}) {
  const [options, setOptions] = useState<NextSectionOptions | null>(null);
  const [error, setError] = useState("");
  const [choice, setChoice] = useState<Choice | null>(null);
  const [number, setNumber] = useState("");
  const [title, setTitle] = useState("");
  const [discipline, setDiscipline] = useState("");

  useEffect(() => {
    let cancelled = false;
    nextSectionOptions()
      .then((loaded) => {
        if (cancelled) return;
        setOptions(loaded);
        setDiscipline(loaded.discipline);
        const firstOpen = loaded.catalog.find((entry) => !entry.done);
        setChoice(
          firstOpen
            ? { kind: "catalog", number: firstOpen.number }
            : loaded.open_catalog
              ? { kind: "custom" }
              : { kind: "unnamed" },
        );
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  /** A typed number the project already drafted: the catalog greys those,
   *  and the typed path must not be a way around it — the server refuses
   *  it too (409 section_already_drafted), so this is the early, visible
   *  half of one rule. Whitespace-folded the way the server folds it. */
  const fold = (value: string) => value.trim().split(/\s+/).join(" ");
  const typedIsDrafted =
    choice?.kind === "custom" &&
    !!options &&
    fold(number) !== "" &&
    options.done.includes(fold(number));

  const header = (): { number: string; title: string } => {
    if (!options || !choice) return { number: "", title: "" };
    if (choice.kind === "catalog") {
      const entry = options.catalog.find((c) => c.number === choice.number);
      return entry ? { number: entry.number, title: entry.title } : { number: "", title: "" };
    }
    if (choice.kind === "custom") return { number: number.trim(), title: title.trim() };
    return { number: "", title: "" };
  };

  const start = () => {
    const picked = header();
    onStart({
      number: picked.number,
      title: picked.title,
      discipline: discipline.trim() || undefined,
    });
  };

  const current = options?.current;
  const currentLabel = current
    ? `${current.number} ${current.title}`.trim() || "this unnamed section"
    : "";

  return (
    <ModalShell title="Start the next section of this project" onClose={onClose} wide>
      <div data-capability="project.next-section">
        {error ? (
          <p role="alert" className="text-xs text-err">
            {error}
          </p>
        ) : !options ? (
          <p className="text-xs text-ink-faint">Reading what this project has settled…</p>
        ) : (
          <>
            <p className="text-sm leading-relaxed text-ink-dim">
              Leaves <b className="text-ink">{currentLabel}</b> behind and opens a
              new section carrying the project profile, the recorded editions,
              every research round, the attached references and the recorded
              facts — never this conversation or this document. You are offered
              to save first.
            </p>

            <fieldset className="mt-3">
              <legend className="text-xs text-ink-dim">Which section?</legend>
              {options.catalog.length > 0 && (
                <ul className="mt-1 max-h-56 space-y-1 overflow-y-auto pr-1">
                  {options.catalog.map((entry) => (
                    <li key={entry.number}>
                      <label
                        className={
                          "flex items-start gap-2 rounded-md border px-2 py-1.5 text-xs " +
                          (entry.done
                            ? "cursor-default border-edge/40 text-ink-faint"
                            : choice?.kind === "catalog" && choice.number === entry.number
                              ? "cursor-pointer border-accent bg-accent/10 text-ink"
                              : "cursor-pointer border-edge text-ink-dim hover:border-accent")
                        }
                        title={entry.scope_note}
                      >
                        <input
                          type="radio"
                          name="next-section"
                          className="mt-0.5"
                          disabled={entry.done}
                          checked={choice?.kind === "catalog" && choice.number === entry.number}
                          onChange={() => setChoice({ kind: "catalog", number: entry.number })}
                        />
                        <span className="min-w-0 flex-1">
                          <span className="font-medium">{entry.number}</span> {entry.title}
                          {entry.done && (
                            <span className="ml-1 text-[10px] uppercase tracking-wide">
                              already drafted
                            </span>
                          )}
                        </span>
                      </label>
                    </li>
                  ))}
                </ul>
              )}
              <label
                className={
                  "mt-1 flex items-start gap-2 rounded-md border px-2 py-1.5 text-xs " +
                  (choice?.kind === "custom"
                    ? "border-accent bg-accent/10 text-ink"
                    : "border-edge text-ink-dim hover:border-accent")
                }
              >
                <input
                  type="radio"
                  name="next-section"
                  className="mt-0.5"
                  checked={choice?.kind === "custom"}
                  onChange={() => setChoice({ kind: "custom" })}
                />
                <span className="min-w-0 flex-1">
                  <span className="font-medium">Another section</span>
                  {choice?.kind === "custom" && (
                    <span className="mt-1.5 grid gap-1.5 sm:grid-cols-[7rem_1fr]">
                      <input
                        value={number}
                        onChange={(event) => setNumber(event.target.value)}
                        placeholder="21 30 00"
                        className={fieldClass}
                        aria-label="Section number"
                        maxLength={40}
                      />
                      <input
                        value={title}
                        onChange={(event) => setTitle(event.target.value)}
                        placeholder="Fire Pumps"
                        className={fieldClass}
                        aria-label="Section title"
                        maxLength={160}
                      />
                    </span>
                  )}
                  {typedIsDrafted && (
                    <span role="alert" className="mt-1 block text-[11px] text-warn">
                      Section {fold(number)} is already drafted in this project.
                      Open that section instead, or pick a different number.
                    </span>
                  )}
                </span>
              </label>
              <label
                className={
                  "mt-1 flex items-start gap-2 rounded-md border px-2 py-1.5 text-xs " +
                  (choice?.kind === "unnamed"
                    ? "border-accent bg-accent/10 text-ink"
                    : "border-edge text-ink-dim hover:border-accent")
                }
                title="A named page counts as content, and importing an office master needs an empty one"
              >
                <input
                  type="radio"
                  name="next-section"
                  className="mt-0.5"
                  checked={choice?.kind === "unnamed"}
                  onChange={() => setChoice({ kind: "unnamed" })}
                />
                <span className="min-w-0 flex-1">
                  <span className="font-medium">Leave it unnamed</span> — I will import an
                  office master, or name it in chat
                </span>
              </label>
            </fieldset>

            <label className="mt-3 block text-xs text-ink-dim">
              Discipline for the new section
              <input
                value={discipline}
                onChange={(event) => setDiscipline(event.target.value)}
                placeholder="Fire Suppression"
                className={fieldClass + " mt-1"}
                aria-label="Discipline for the new section"
              />
            </label>

            <BriefContents manifest={options.manifest} />
            <p className="mt-2 text-[11px] text-ink-faint">
              To pair the new section with a template instead, use New session →
              New section in an existing project.
            </p>

            <div className="mt-4 flex gap-2">
              <button
                type="button"
                className={primaryBtn}
                onClick={start}
                disabled={
                  !choice ||
                  typedIsDrafted ||
                  (choice.kind === "custom" && !number.trim() && !title.trim())
                }
                data-capability="project.next-section"
              >
                Start the next section
              </button>
              <button type="button" className={quietBtn} onClick={onClose}>
                Cancel
              </button>
            </div>
          </>
        )}
      </div>
    </ModalShell>
  );
}
