/**
 * Advisory lint issues + the standards editions in effect, rendered under
 * the document panel. Issues are deterministic (no API) and recomputed on
 * every document change; clicking one jumps to the offending block.
 * The standards strip is collapsed to a one-line summary; expanding it
 * lists every edition in effect, with jurisdiction overrides highlighted
 * and their recorded adoption basis shown. From the expanded strip the user
 * can curate the list for this project — add a standard the module doesn't
 * pin, change an edition (with a stated basis), or exclude/restore any
 * standard (including a built-in pin). Every change rides POST /api/doc/edit
 * (one undoable version), the same manual-edit path the project-profile form
 * uses; the strip re-renders from the refreshed standards payload.
 */
import { useState } from "react";
import type { EditOp, LintIssue, StandardInfo } from "../types";

const severityDot: Record<LintIssue["severity"], string> = {
  warn: "bg-warn",
  info: "bg-ink-faint",
};

export default function IssuesDrawer({
  issues,
  onJump,
}: {
  issues: LintIssue[];
  onJump: (elementId: string) => void;
}) {
  if (issues.length === 0) return null;
  return (
    <div
      className="max-h-44 overflow-y-auto border-t border-edge bg-bg/60 px-5 py-2.5"
      data-tour="lint-issues"
    >
      <p className="text-[11px] font-medium tracking-wide text-ink-dim uppercase">
        Issues ({issues.length}) — advisory
      </p>
      <ul className="mt-1.5 space-y-1">
        {issues.map((issue) => (
          <li key={issue.id}>
            <button
              className="flex w-full items-baseline gap-2 rounded px-1 py-0.5 text-left text-xs text-ink-dim transition-colors hover:bg-raised hover:text-ink"
              onClick={() => onJump(issue.element_id)}
              title={issue.match ? `Matched: ${issue.match}` : issue.rule}
            >
              <span
                className={`h-1.5 w-1.5 shrink-0 translate-y-[-1px] rounded-full ${severityDot[issue.severity]}`}
              />
              <span className="shrink-0 font-medium text-ink tabular-nums">
                {issue.ref}
              </span>
              <span className="truncate">{issue.message}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

type RowKind = "default" | "override" | "added" | "suppressed";

function rowKind(s: StandardInfo): RowKind {
  if (s.is_suppressed) return "suppressed";
  if (s.is_added) return "added";
  if (s.is_override) return "override";
  return "default";
}

const stdInput =
  "min-w-0 rounded border border-edge bg-raised px-1.5 py-0.5 text-[11px] text-ink outline-none focus:border-accent disabled:opacity-40";
const stdBtn =
  "shrink-0 rounded px-1.5 py-0.5 text-[10px] text-ink-faint transition-colors hover:bg-raised hover:text-ink disabled:pointer-events-none disabled:opacity-40";
const EXCLUDE_REASONS = ["Not applicable to this project", "Out of scope"];

/** Active inline editor within the strip (only one at a time). */
type Editor =
  | { mode: "edition"; name: string; isAdded: boolean; title: string }
  | { mode: "exclude"; name: string }
  | { mode: "add" };

export function StandardsStrip({
  standards,
  onEditDoc,
  busy,
}: {
  standards: StandardInfo[];
  onEditDoc: (ops: EditOp[]) => void;
  busy: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const [editor, setEditor] = useState<Editor | null>(null);
  const [edition, setEdition] = useState("");
  const [reason, setReason] = useState("");
  const [addForm, setAddForm] = useState({
    name: "",
    edition: "",
    title: "",
    basis: "",
  });
  const [saving, setSaving] = useState(false);

  if (standards.length === 0) return null;

  const live = standards.filter((s) => !s.is_suppressed);
  const overrides = live.filter((s) => s.is_override && !s.is_added);
  const added = live.filter((s) => s.is_added);
  const suppressed = standards.filter((s) => s.is_suppressed);
  const disabled = busy || saving;

  const closeEditor = () => {
    setEditor(null);
    setEdition("");
    setReason("");
    setAddForm({ name: "", edition: "", title: "", basis: "" });
  };

  const submit = async (ops: EditOp[]) => {
    setSaving(true);
    try {
      await onEditDoc(ops);
      closeEditor();
    } finally {
      setSaving(false);
    }
  };

  const openEdition = (s: StandardInfo) => {
    setEditor({
      mode: "edition",
      name: s.name,
      isAdded: s.is_added,
      title: s.title,
    });
    setEdition(s.edition);
    setReason("");
  };
  const openExclude = (s: StandardInfo) => {
    setEditor({ mode: "exclude", name: s.name });
    setReason("");
  };

  const saveEdition = () => {
    if (editor?.mode !== "edition") return;
    if (!edition.trim() || !reason.trim()) return;
    const op: EditOp = {
      action: "set_standard_edition",
      target_id: "sec",
      standard: editor.name,
      edition: edition.trim(),
      basis: reason.trim(),
    };
    if (editor.isAdded && editor.title) op.title = editor.title;
    void submit([op]);
  };
  const confirmExclude = () => {
    if (editor?.mode !== "exclude") return;
    void submit([
      {
        action: "set_standard_suppressed",
        target_id: "sec",
        standard: editor.name,
        suppressed: true,
        basis: reason.trim(),
      },
    ]);
  };
  const removeOverride = (name: string) =>
    void submit([
      {
        action: "set_standard_edition",
        target_id: "sec",
        standard: name,
        edition: "",
      },
    ]);
  const restore = (name: string) =>
    void submit([
      {
        action: "set_standard_suppressed",
        target_id: "sec",
        standard: name,
        suppressed: false,
      },
    ]);
  const saveAdd = () => {
    if (!addForm.name.trim() || !addForm.edition.trim() || !addForm.basis.trim())
      return;
    const op: EditOp = {
      action: "set_standard_edition",
      target_id: "sec",
      standard: addForm.name.trim(),
      edition: addForm.edition.trim(),
      basis: addForm.basis.trim(),
    };
    if (addForm.title.trim()) op.title = addForm.title.trim();
    void submit([op]);
  };

  const summaryBits = [
    `${live.length} in effect`,
    overrides.length > 0 &&
      `${overrides.length} override${overrides.length === 1 ? "" : "s"}`,
    added.length > 0 && `${added.length} added`,
    suppressed.length > 0 && `${suppressed.length} excluded`,
  ].filter(Boolean);

  return (
    <div
      className="border-t border-edge bg-bg/80 px-5 py-2"
      data-tour="standards-strip"
    >
      <button
        className="flex w-full items-baseline gap-2 text-left text-[11px] text-ink-faint transition-colors hover:text-ink-dim"
        onClick={() => setExpanded((v) => !v)}
        title="Standards editions in effect — add, change, or exclude standards for this project"
      >
        <span className="font-medium tracking-wide uppercase">Standards</span>
        <span className="truncate">{summaryBits.join(" · ")}</span>
        <span className="ml-auto shrink-0">{expanded ? "▾" : "▸"}</span>
      </button>
      {expanded && (
        <div className="mt-1.5">
          <ul className="max-h-52 space-y-0.5 overflow-y-auto">
            {standards.map((s) => {
              const kind = rowKind(s);
              const isEditing =
                editor?.mode === "edition" && editor.name === s.name;
              const isExcluding =
                editor?.mode === "exclude" && editor.name === s.name;
              return (
                <li key={`${s.name}-${kind}`} className="px-1 text-[11px]">
                  {isEditing ? (
                    <div className="flex flex-wrap items-center gap-1.5 py-0.5">
                      <span className="shrink-0 font-medium text-ink-dim">
                        {s.name}
                      </span>
                      <input
                        className={`${stdInput} w-16`}
                        placeholder="Edition"
                        value={edition}
                        disabled={disabled}
                        autoFocus
                        onChange={(e) => setEdition(e.target.value)}
                      />
                      <input
                        className={`${stdInput} flex-1`}
                        placeholder="Reason / adoption basis (required)"
                        value={reason}
                        disabled={disabled}
                        onChange={(e) => setReason(e.target.value)}
                        onKeyDown={(e) => e.key === "Enter" && saveEdition()}
                      />
                      <button
                        className={stdBtn}
                        onClick={saveEdition}
                        disabled={disabled || !edition.trim() || !reason.trim()}
                      >
                        {saving ? "Saving…" : "Save"}
                      </button>
                      <button
                        className={stdBtn}
                        onClick={closeEditor}
                        disabled={disabled}
                      >
                        Cancel
                      </button>
                    </div>
                  ) : isExcluding ? (
                    <div className="flex flex-wrap items-center gap-1.5 py-0.5">
                      <span className="shrink-0 font-medium text-ink-dim">
                        Exclude {s.name}?
                      </span>
                      <input
                        className={`${stdInput} flex-1`}
                        placeholder="Reason (optional)"
                        value={reason}
                        disabled={disabled}
                        autoFocus
                        onChange={(e) => setReason(e.target.value)}
                        onKeyDown={(e) => e.key === "Enter" && confirmExclude()}
                      />
                      {EXCLUDE_REASONS.map((r) => (
                        <button
                          key={r}
                          className={stdBtn}
                          onClick={() => setReason(r)}
                          disabled={disabled}
                          title={`Use reason: ${r}`}
                        >
                          {r}
                        </button>
                      ))}
                      <button
                        className={`${stdBtn} text-warn hover:text-warn`}
                        onClick={confirmExclude}
                        disabled={disabled}
                      >
                        {saving ? "…" : "Exclude"}
                      </button>
                      <button
                        className={stdBtn}
                        onClick={closeEditor}
                        disabled={disabled}
                      >
                        Cancel
                      </button>
                    </div>
                  ) : (
                    <div
                      className="group flex items-baseline gap-2 py-0.5"
                      title={s.title || s.name}
                    >
                      <span
                        className={`shrink-0 font-medium ${
                          kind === "suppressed"
                            ? "text-ink-faint line-through"
                            : "text-ink-dim"
                        }`}
                      >
                        {s.name}
                      </span>
                      <span
                        className={
                          kind === "suppressed"
                            ? "text-ink-faint line-through"
                            : kind === "override"
                              ? "font-semibold text-warn"
                              : kind === "added"
                                ? "font-semibold text-accent"
                                : "text-ink-faint"
                        }
                      >
                        {s.edition || "—"}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-ink-faint italic">
                        {kind === "override" && `override — ${s.basis}`}
                        {kind === "added" &&
                          `added${s.basis ? ` — ${s.basis}` : ""}`}
                        {kind === "suppressed" &&
                          `excluded${s.reason ? ` — ${s.reason}` : ""}`}
                      </span>
                      <span className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
                        {kind === "suppressed" ? (
                          <button
                            className={stdBtn}
                            onClick={() => restore(s.name)}
                            disabled={disabled}
                          >
                            Restore
                          </button>
                        ) : (
                          <>
                            <button
                              className={stdBtn}
                              onClick={() => openEdition(s)}
                              disabled={disabled}
                            >
                              Edit edition
                            </button>
                            {kind === "default" && (
                              <button
                                className={stdBtn}
                                onClick={() => openExclude(s)}
                                disabled={disabled}
                              >
                                Exclude
                              </button>
                            )}
                            {kind === "override" && (
                              <button
                                className={stdBtn}
                                onClick={() => removeOverride(s.name)}
                                disabled={disabled}
                                title="Drop the override and revert to the module default edition"
                              >
                                Revert to default
                              </button>
                            )}
                            {kind === "added" && (
                              <button
                                className={stdBtn}
                                onClick={() => removeOverride(s.name)}
                                disabled={disabled}
                                title="Remove this added standard"
                              >
                                Remove
                              </button>
                            )}
                          </>
                        )}
                      </span>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
          {editor?.mode === "add" ? (
            <div className="mt-1.5 space-y-1.5 rounded border border-edge/70 bg-bg/40 p-2">
              <p className="text-[11px] font-medium tracking-wide text-ink-faint uppercase">
                Add a standard
              </p>
              <div className="flex flex-wrap items-center gap-1.5">
                <input
                  className={`${stdInput} w-28`}
                  placeholder="Designation"
                  value={addForm.name}
                  disabled={disabled}
                  autoFocus
                  onChange={(e) =>
                    setAddForm({ ...addForm, name: e.target.value })
                  }
                />
                <input
                  className={`${stdInput} w-16`}
                  placeholder="Edition"
                  value={addForm.edition}
                  disabled={disabled}
                  onChange={(e) =>
                    setAddForm({ ...addForm, edition: e.target.value })
                  }
                />
                <input
                  className={`${stdInput} flex-1`}
                  placeholder="Full title (optional)"
                  value={addForm.title}
                  disabled={disabled}
                  onChange={(e) =>
                    setAddForm({ ...addForm, title: e.target.value })
                  }
                />
              </div>
              <input
                className={`${stdInput} w-full`}
                placeholder="Why it applies (required)"
                value={addForm.basis}
                disabled={disabled}
                onChange={(e) =>
                  setAddForm({ ...addForm, basis: e.target.value })
                }
                onKeyDown={(e) => e.key === "Enter" && saveAdd()}
              />
              <div className="flex items-center gap-2">
                <button
                  className="rounded-md border border-edge bg-raised px-2 py-0.5 text-[11px] text-ink-dim transition-colors hover:border-accent hover:text-accent disabled:pointer-events-none disabled:opacity-40"
                  onClick={saveAdd}
                  disabled={
                    disabled ||
                    !addForm.name.trim() ||
                    !addForm.edition.trim() ||
                    !addForm.basis.trim()
                  }
                >
                  {saving ? "Saving…" : "Add standard"}
                </button>
                <button
                  className={stdBtn}
                  onClick={closeEditor}
                  disabled={disabled}
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <button
              className="mt-1.5 rounded-md border border-edge bg-raised px-2 py-0.5 text-[11px] text-ink-dim transition-colors hover:border-accent hover:text-accent disabled:pointer-events-none disabled:opacity-40"
              onClick={() => {
                closeEditor();
                setEditor({ mode: "add" });
              }}
              disabled={disabled}
            >
              + Add standard
            </button>
          )}
        </div>
      )}
    </div>
  );
}
