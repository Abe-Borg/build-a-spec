/**
 * Session-start module picker (Batch 8): choose the spec module the next
 * session runs on — and, when the chosen module is the generic open-catalog
 * one, state the discipline that steers its drafting and research. Opens
 * from the Header's "New session" button; Cancel / backdrop / Escape keep
 * the current session untouched.
 */
import { useEffect, useState } from "react";
import type { ModuleInfo } from "../types";
import { DISCIPLINES } from "../lib/tour";
import { ModalShell, primaryBtn, quietBtn } from "./ModalShell";

interface Props {
  open: boolean;
  modules: ModuleInfo[];
  busy: boolean;
  currentModuleId?: string;
  onCancel: () => void;
  onConfirm: (moduleId: string, discipline: string) => void;
}

/** The active module when it's in the list, else the registry default. */
function preselect(modules: ModuleInfo[], currentModuleId?: string): string {
  if (currentModuleId && modules.some((m) => m.module_id === currentModuleId))
    return currentModuleId;
  return (
    modules.find((m) => m.default)?.module_id ?? modules[0]?.module_id ?? ""
  );
}

export default function ModulePickerDialog({
  open,
  modules,
  busy,
  currentModuleId,
  onCancel,
  onConfirm,
}: Props) {
  const [selectedId, setSelectedId] = useState("");
  const [discipline, setDiscipline] = useState("");

  // Re-initialize the selection each time the dialog opens.
  useEffect(() => {
    if (!open) return;
    setSelectedId(preselect(modules, currentModuleId));
    setDiscipline("");
  }, [open, modules, currentModuleId]);

  // Escape cancels (keeps the current session), matching ConfirmDialog.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  if (!open) return null;

  const selected = modules.find((m) => m.module_id === selectedId);
  const needsDiscipline = !!selected?.generic;
  const canConfirm =
    !busy && !!selected && !(needsDiscipline && !discipline.trim());
  const confirm = () => {
    if (!canConfirm || !selected) return;
    onConfirm(selected.module_id, needsDiscipline ? discipline.trim() : "");
  };

  return (
    <ModalShell title="Start a new session" onClose={onCancel} wide>
      <p className="text-sm leading-relaxed text-ink-dim">
        Pick the spec module for the new session. The current chat and
        document are cleared — Cancel keeps working where you are.
      </p>
      <div
        role="radiogroup"
        aria-label="Spec module"
        className="mt-3 flex flex-col gap-2"
      >
        {modules.map((m) => {
          const isSelected = m.module_id === selectedId;
          return (
            <button
              key={m.module_id}
              type="button"
              role="radio"
              aria-checked={isSelected}
              onClick={() => setSelectedId(m.module_id)}
              className={
                "w-full rounded-lg border px-3.5 py-2.5 text-left transition-colors " +
                (isSelected
                  ? "border-accent bg-accent/10"
                  : "border-edge bg-raised hover:border-accent/60")
              }
            >
              <span className="block text-sm font-medium text-ink">
                {m.display_name}
              </span>
              <span className="mt-0.5 block text-xs leading-snug text-ink-dim">
                {m.description}
              </span>
            </button>
          );
        })}
      </div>
      {needsDiscipline && (
        <div className="mt-4">
          <p className="text-sm leading-relaxed text-ink-dim">
            What discipline is this session for? It steers drafting and
            research from the first turn.
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            {DISCIPLINES.map((d) => (
              <button
                key={d}
                type="button"
                onClick={() => setDiscipline(d)}
                className={
                  discipline === d
                    ? "rounded-lg border border-accent bg-accent/10 px-3.5 py-1.5 text-sm text-accent transition-colors"
                    : quietBtn
                }
              >
                {d}
              </button>
            ))}
          </div>
          <input
            value={discipline}
            onChange={(e) => setDiscipline(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") confirm();
            }}
            placeholder="e.g. Electrical, Structural, Civil…"
            className="mt-2 w-full rounded-lg border border-edge bg-raised px-3 py-1.5 text-sm text-ink placeholder:text-ink-faint focus:border-accent focus:outline-none"
          />
        </div>
      )}
      <div className="mt-4 flex gap-2">
        <button
          onClick={confirm}
          disabled={!canConfirm}
          title={
            needsDiscipline && !discipline.trim()
              ? "State the discipline first — the generic module needs it"
              : undefined
          }
          className={primaryBtn}
        >
          Start new session
        </button>
        <button onClick={onCancel} className={quietBtn}>
          Cancel
        </button>
      </div>
    </ModalShell>
  );
}
