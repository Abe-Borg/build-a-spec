/**
 * "Project facts" — what the project has established, recorded so the NEXT
 * section of the same project starts knowing it.
 *
 * Sits under "Waiting on you" and is deliberately its sibling in shape, not
 * its twin in meaning: follow-ups are what the model still needs from the
 * user; these are settled inputs — the edition the authority confirmed, an
 * owner standard, the water-supply basis, what another section specifies.
 * The model records them as they are settled (record_project_facts), reads
 * them every turn, and the research and Final QC teams are briefed with
 * them; a project brief carries them into the next section. The user's side
 * is Add / Edit / Retire here, all through the same store.
 *
 * Rendered whenever the ledger holds anything, or the session is linked to
 * a project (a seeded section with no facts yet still shows where it came
 * from and the Add form).
 */
import { useEffect, useRef, useState } from "react";

import type { ProjectFactInput } from "../lib/api";
import {
  factCounts,
  factProvenance,
  groupProjectFacts,
  justSupersededIds,
} from "../lib/projectFacts";
import type { ProjectFact, ProjectLink } from "../types";

type Scope = ProjectFact["scope"];
type ActiveStatus = "confirmed" | "assumed";

interface FactDraft {
  statement: string;
  detail: string;
  scope: Scope;
  section: string;
  status: ActiveStatus;
}

const emptyDraft = (section: string): FactDraft => ({
  statement: "",
  detail: "",
  scope: "project",
  section,
  status: "confirmed",
});

const draftOf = (fact: ProjectFact): FactDraft => ({
  statement: fact.statement,
  detail: fact.detail,
  scope: fact.scope,
  section: fact.section,
  status: fact.status === "assumed" ? "assumed" : "confirmed",
});

const toInput = (draft: FactDraft): ProjectFactInput => ({
  statement: draft.statement.trim(),
  detail: draft.detail.trim(),
  scope: draft.scope,
  section: draft.scope === "section" ? draft.section.trim() : "",
  status: draft.status,
});

const fieldClass =
  "w-full rounded border border-edge bg-bg px-1.5 py-1 text-[11px] text-ink outline-none focus:border-accent";
const smallBtn =
  "text-ink-faint underline-offset-2 hover:text-accent hover:underline disabled:opacity-40";

const statusChip: Record<ActiveStatus, string> = {
  confirmed: "border-accent/50 bg-accent/10 text-accent",
  assumed: "border-warn/50 bg-warn/10 text-warn",
};

const GROUP_LABELS = {
  project: "Project-wide",
  discipline: "Discipline-wide",
  otherDisciplines: "Coordination facts from other disciplines",
  other: "Coordination facts from other sections",
  own: "This section",
} as const;

function FactForm({
  draft,
  setDraft,
  busy,
  submitLabel,
  currentDiscipline,
  onSubmit,
  onCancel,
}: {
  draft: FactDraft;
  setDraft: (draft: FactDraft) => void;
  busy: boolean;
  submitLabel: string;
  /** What a discipline-scoped fact will be bound to (the server decides). */
  currentDiscipline: string;
  onSubmit: () => void;
  onCancel: () => void;
}) {
  return (
    <form
      className="mt-1 space-y-1 rounded border border-edge/60 bg-bg/60 p-1.5"
      onSubmit={(event) => {
        event.preventDefault();
        if (draft.statement.trim()) onSubmit();
      }}
    >
      <textarea
        className={fieldClass + " resize-none"}
        rows={2}
        value={draft.statement}
        placeholder="One fact, stated so a stranger could act on it"
        maxLength={240}
        autoFocus
        onChange={(event) => setDraft({ ...draft, statement: event.target.value })}
        onKeyDown={(event) => {
          if (event.key === "Escape") onCancel();
        }}
        aria-label="Fact statement"
      />
      <input
        className={fieldClass}
        value={draft.detail}
        placeholder="Detail or basis (optional)"
        maxLength={600}
        onChange={(event) => setDraft({ ...draft, detail: event.target.value })}
        aria-label="Fact detail"
      />
      <div className="flex flex-wrap items-center gap-1.5 text-[10px] text-ink-faint">
        <select
          className="rounded border border-edge bg-bg px-1 py-0.5 text-[10px] text-ink"
          value={draft.scope}
          onChange={(event) => setDraft({ ...draft, scope: event.target.value as Scope })}
          aria-label="Fact scope"
          title="How far the fact reaches: every section of the project, every section of this discipline, or one section other sections coordinate with"
        >
          <option value="project">project-wide</option>
          <option value="discipline">discipline-wide</option>
          <option value="section">one section</option>
        </select>
        {draft.scope === "discipline" && (
          <span
            className="text-[10px] text-ink-faint"
            title="A discipline-wide fact is bound to this session's discipline; other disciplines' sections read it as coordination information"
          >
            {currentDiscipline
              ? `applies to ${currentDiscipline}`
              : "no discipline recorded for this section — the fact will not be bound to one"}
          </span>
        )}
        {draft.scope === "section" && (
          <input
            className="w-24 rounded border border-edge bg-bg px-1 py-0.5 text-[10px] text-ink"
            value={draft.section}
            placeholder="21 13 13"
            onChange={(event) => setDraft({ ...draft, section: event.target.value })}
            aria-label="Section number the fact is about"
          />
        )}
        <select
          className="rounded border border-edge bg-bg px-1 py-0.5 text-[10px] text-ink"
          value={draft.status}
          onChange={(event) =>
            setDraft({ ...draft, status: event.target.value as ActiveStatus })
          }
          aria-label="Fact status"
          title="Confirmed: stated by you or established by a grounded source. Assumed: a working default nobody has contested."
        >
          <option value="confirmed">confirmed</option>
          <option value="assumed">assumed</option>
        </select>
        <span className="flex-1" />
        <button
          type="submit"
          className="rounded border border-accent/60 bg-accent/10 px-1.5 py-0.5 text-accent disabled:opacity-40"
          disabled={busy || !draft.statement.trim()}
        >
          {submitLabel}
        </button>
        <button type="button" className={smallBtn} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

export default function ProjectFactsPanel({
  items,
  link,
  currentSection,
  currentDiscipline,
  busy,
  openNonce,
  onAdd,
  onUpdate,
  onSupersede,
}: {
  items: ProjectFact[];
  link: ProjectLink | null;
  /** The document's section number, for the "this section" group. */
  currentSection: string;
  /** The document's discipline: names the discipline-wide group and files
   *  another discipline's facts as coordination information. */
  currentDiscipline: string;
  busy: boolean;
  openNonce?: number;
  /** Each resolves to null on success, or the server's reason to show. */
  onAdd: (fact: ProjectFactInput) => Promise<string | null>;
  onUpdate: (pid: string, changes: Partial<ProjectFactInput>) => Promise<string | null>;
  onSupersede: (
    pid: string,
    reason: string,
    replacement?: ProjectFactInput,
  ) => Promise<string | null>;
}) {
  const [expanded, setExpanded] = useState(false);
  const [retiredOpen, setRetiredOpen] = useState(false);
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState<FactDraft>(() => emptyDraft(currentSection));
  const [editing, setEditing] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState<FactDraft>(() => emptyDraft(""));
  const [retiring, setRetiring] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [replacement, setReplacement] = useState("");
  const [error, setError] = useState("");
  // Ids mid-retire, so the row can strike through before it moves to the
  // retired list. Driven off a snapshot diff (see lib/projectFacts) rather
  // than the click, so a model-side supersede animates identically.
  const [settling, setSettling] = useState<Set<string>>(new Set());
  const previous = useRef<ProjectFact[] | null>(null);
  // Opens itself the first time something lands in it; once only — a later
  // collapse is the user's decision and is respected.
  const introduced = useRef(false);

  const counts = factCounts(items);
  const groups = groupProjectFacts(items, currentSection, currentDiscipline);

  // Two effects, deliberately (the FollowUpsPanel lesson): the first only
  // ADDS to the settling set, the second only clears it, keyed to itself.
  useEffect(() => {
    const moved = justSupersededIds(previous.current, items);
    previous.current = items;
    if (moved.size === 0) return;
    setSettling((current) => new Set([...current, ...moved]));
  }, [items]);

  useEffect(() => {
    if (settling.size === 0) return;
    const captured = settling;
    const timer = window.setTimeout(() => {
      setSettling((current) => {
        const next = new Set(current);
        for (const pid of captured) next.delete(pid);
        return next.size === current.size ? current : next;
      });
    }, 1100);
    return () => window.clearTimeout(timer);
  }, [settling]);

  useEffect(() => {
    if (introduced.current || items.length === 0) return;
    introduced.current = true;
    setExpanded(true);
  }, [items.length]);

  useEffect(() => {
    if (openNonce) setExpanded(true);
  }, [openNonce]);

  if (items.length === 0 && link === null) return null;

  const summary =
    counts.active === 0
      ? counts.superseded
        ? `none active · ${counts.superseded} retired`
        : "none recorded yet"
      : `${counts.active} recorded${counts.assumed ? ` · ${counts.assumed} assumed` : ""}${
          counts.superseded ? ` · ${counts.superseded} retired` : ""
        }`;
  const seededFrom = link?.seeded_from ?? [];

  const finish = async (task: Promise<string | null>) => {
    const message = await task;
    setError(message ?? "");
    return message === null;
  };

  const submitAdd = async () => {
    if (await finish(onAdd(toInput(draft)))) {
      setAdding(false);
      setDraft(emptyDraft(currentSection));
    }
  };

  const submitEdit = async (pid: string) => {
    if (await finish(onUpdate(pid, toInput(editDraft)))) setEditing(null);
  };

  const submitRetire = async (pid: string) => {
    const replacementInput = replacement.trim()
      ? { statement: replacement.trim() }
      : undefined;
    if (await finish(onSupersede(pid, reason.trim(), replacementInput))) {
      setRetiring(null);
      setReason("");
      setReplacement("");
    }
  };

  const renderRow = (fact: ProjectFact) => {
    const status: ActiveStatus = fact.status === "assumed" ? "assumed" : "confirmed";
    return (
      <li key={fact.pid} className="px-1 py-1" data-fact-id={fact.pid}>
        {editing === fact.pid ? (
          <FactForm
            draft={editDraft}
            setDraft={setEditDraft}
            busy={busy}
            submitLabel="Save"
            currentDiscipline={currentDiscipline}
            onSubmit={() => void submitEdit(fact.pid)}
            onCancel={() => setEditing(null)}
          />
        ) : (
          <>
            <div className="flex items-baseline gap-1.5">
              <span
                className={`shrink-0 rounded-full border px-1.5 py-px text-[9px] font-medium ${statusChip[status]}`}
              >
                {status}
              </span>
              <span className="min-w-0 flex-1 text-xs text-ink">{fact.statement}</span>
            </div>
            {fact.detail && (
              <span className="block pl-0.5 text-[11px] text-ink-faint">{fact.detail}</span>
            )}
            <span className="block pl-0.5 text-[10px] text-ink-faint">
              {fact.pid} · {factProvenance(fact)}
            </span>
            {retiring === fact.pid ? (
              <form
                className="mt-1 space-y-1 rounded border border-edge/60 bg-bg/60 p-1.5"
                onSubmit={(event) => {
                  event.preventDefault();
                  void submitRetire(fact.pid);
                }}
              >
                <input
                  className={fieldClass}
                  value={reason}
                  placeholder="Why is this no longer true? (kept with the retired fact)"
                  maxLength={300}
                  autoFocus
                  onChange={(event) => setReason(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Escape") setRetiring(null);
                  }}
                  aria-label="Reason for retiring the fact"
                />
                <input
                  className={fieldClass}
                  value={replacement}
                  placeholder="Replacement statement (optional — recorded as a new fact)"
                  maxLength={240}
                  onChange={(event) => setReplacement(event.target.value)}
                  aria-label="Replacement fact"
                />
                <div className="flex gap-2 text-[10px]">
                  <button
                    type="submit"
                    className="rounded border border-warn/60 bg-warn/10 px-1.5 py-0.5 text-warn disabled:opacity-40"
                    disabled={busy}
                  >
                    Retire
                  </button>
                  <button type="button" className={smallBtn} onClick={() => setRetiring(null)}>
                    Cancel
                  </button>
                </div>
              </form>
            ) : (
              <span className="mt-0.5 flex gap-2 pl-0.5 text-[10px]">
                <button
                  className={smallBtn}
                  disabled={busy}
                  onClick={() => {
                    setEditDraft(draftOf(fact));
                    setEditing(fact.pid);
                    setRetiring(null);
                    setError("");
                  }}
                  title={busy ? "The assistant is replying — try again in a moment" : "Edit this fact"}
                >
                  Edit
                </button>
                <button
                  className={smallBtn}
                  disabled={busy}
                  onClick={() => {
                    setRetiring(fact.pid);
                    setEditing(null);
                    setReason("");
                    setReplacement("");
                    setError("");
                  }}
                  title={
                    busy
                      ? "The assistant is replying — try again in a moment"
                      : "Retire this fact — it stays in the record with your reason"
                  }
                >
                  Retire…
                </button>
              </span>
            )}
          </>
        )}
      </li>
    );
  };

  const renderGroup = (key: keyof typeof GROUP_LABELS, facts: ProjectFact[]) => {
    if (facts.length === 0) return null;
    const label =
      key === "own" && currentSection
        ? `${GROUP_LABELS.own} (${currentSection})`
        : key === "discipline" && currentDiscipline
          ? `${GROUP_LABELS.discipline} (${currentDiscipline})`
          : GROUP_LABELS[key];
    return (
      <div key={key} className="mt-1">
        <span className="block px-1 text-[10px] font-medium uppercase tracking-wide text-ink-faint">
          {label}
        </span>
        <ul className="space-y-0.5">{facts.map(renderRow)}</ul>
      </div>
    );
  };

  return (
    <div
      className="border-t border-edge bg-bg/70 px-5 py-2"
      data-tour="project-facts"
      data-capability="project.facts"
    >
      <button
        className="flex w-full items-baseline gap-2 text-left text-[11px] text-ink-faint transition-colors hover:text-ink-dim"
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
        aria-controls="project-facts-list"
        title="Facts the project has established — carried into the next section through the project brief"
      >
        <span className="shrink-0 font-medium tracking-wide uppercase">Project facts</span>
        <span key={counts.active} className="truncate tally-flash">
          {summary}
        </span>
        {link && (
          <span
            className="truncate text-ink-faint"
            title={
              seededFrom.length
                ? `This section was started from the project brief of section ${seededFrom.join(", ")}`
                : "This section has exported a project brief"
            }
          >
            · {link.name}
            {seededFrom.length > 0 && ` · seeded from ${seededFrom.join(", ")}`}
          </span>
        )}
        <span className="ml-auto shrink-0">{expanded ? "▾" : "▸"}</span>
      </button>

      {expanded && (
        <div id="project-facts-list" className="mt-1.5">
          <div className="max-h-64 overflow-y-auto">
            {renderGroup("project", groups.project)}
            {renderGroup("discipline", groups.discipline)}
            {renderGroup("otherDisciplines", groups.otherDisciplines)}
            {renderGroup("other", groups.other)}
            {renderGroup("own", groups.own)}
            {counts.active === 0 && !adding && (
              <p className="px-1 text-[11px] text-ink-faint">
                Nothing recorded yet. The assistant records facts as they are settled; you can add
                one by hand.
              </p>
            )}
          </div>

          {adding ? (
            <FactForm
              draft={draft}
              setDraft={setDraft}
              busy={busy}
              submitLabel="Record"
              currentDiscipline={currentDiscipline}
              onSubmit={() => void submitAdd()}
              onCancel={() => {
                setAdding(false);
                setError("");
              }}
            />
          ) : (
            <button
              className={"mt-1 px-1 text-[10px] " + smallBtn}
              disabled={busy}
              onClick={() => {
                setAdding(true);
                setEditing(null);
                setRetiring(null);
                setDraft(emptyDraft(currentSection));
                setError("");
              }}
              title={
                busy
                  ? "The assistant is replying — try again in a moment"
                  : "Record a fact by hand (source: you)"
              }
            >
              + Add a fact
            </button>
          )}

          {error && (
            <p role="alert" className="mt-1 px-1 text-[10px] text-err">
              {error}
            </p>
          )}

          {groups.superseded.length > 0 && (
            <div className="mt-1.5 border-t border-edge/60 pt-1.5">
              <button
                className="flex w-full items-baseline gap-2 text-left text-[10px] text-ink-faint transition-colors hover:text-ink-dim"
                onClick={() => setRetiredOpen((value) => !value)}
                aria-expanded={retiredOpen}
              >
                <span>Retired ({groups.superseded.length})</span>
                <span className="ml-auto">{retiredOpen ? "▾" : "▸"}</span>
              </button>
              {retiredOpen && (
                <ul className="mt-1 max-h-40 space-y-1 overflow-y-auto">
                  {groups.superseded.map((fact) => (
                    <li
                      key={fact.pid}
                      className={`px-1 py-0.5 ${settling.has(fact.pid) ? "followup-settle" : ""}`}
                    >
                      <span className="block text-xs text-ink-faint line-through">
                        {fact.statement}
                      </span>
                      <span className="block text-[10px] text-ink-dim">
                        {fact.pid}
                        {fact.supersede_reason && ` · ${fact.supersede_reason}`}
                        {fact.superseded_by && ` · replaced by ${fact.superseded_by}`}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
