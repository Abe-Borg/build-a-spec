/**
 * The live document panel: SectionFormat rendering of the server-owned
 * tree, a per-turn version stepper (undo/redo), export / save / open
 * actions, and the open-items list ([TBD] markers + needs-input blocks).
 */
import { useRef } from "react";
import type { OpenItem, SpecDoc } from "../types";
import SpecDocument from "./SpecDocument";

interface Props {
  doc: SpecDoc | null;
  openItems: OpenItem[];
  changedIds: ReadonlySet<string>;
  busy: boolean;
  onUndo: () => void;
  onRedo: () => void;
  onLoadProject: (file: File) => void;
}

function EmptyState() {
  return (
    <div className="mx-auto max-w-2xl rounded-xl border border-paper-edge bg-paper px-10 py-12 text-paper-ink shadow-[0_2px_16px_rgba(0,0,0,0.25)]">
      <div className="text-center">
        <p className="text-[13px] font-semibold tracking-wide">SECTION</p>
        <p className="mt-1 text-[13px] font-semibold tracking-wide text-paper-dim">
          — awaiting the interview —
        </p>
      </div>

      <div className="mt-10 space-y-8 select-none">
        {["PART 1 - GENERAL", "PART 2 - PRODUCTS", "PART 3 - EXECUTION"].map(
          (part) => (
            <div key={part}>
              <p className="text-[13px] font-semibold">{part}</p>
              <div className="mt-3 space-y-2.5">
                <div className="h-2 w-11/12 rounded bg-paper-edge/80" />
                <div className="h-2 w-9/12 rounded bg-paper-edge/70" />
                <div className="h-2 w-10/12 rounded bg-paper-edge/60" />
              </div>
            </div>
          ),
        )}
      </div>

      <p className="mt-12 text-center text-xs leading-relaxed text-paper-dim">
        Your section builds here as the interview progresses — articles
        appear and update in place, with changes highlighted and every [TBD]
        tracked until it&apos;s resolved.
      </p>
    </div>
  );
}

const kindDot: Record<OpenItem["kind"], string> = {
  tbd: "bg-warn",
  needs_input: "bg-err",
};

export default function ArtifactPanel({
  doc,
  openItems,
  changedIds,
  busy,
  onUndo,
  onRedo,
  onLoadProject,
}: Props) {
  const fileRef = useRef<HTMLInputElement>(null);
  const version = doc?.version ?? { index: 0, count: 1 };
  const hasContent =
    !!doc &&
    (doc.section.number !== "" ||
      doc.section.title !== "" ||
      doc.parts.some((p) => p.articles.length > 0));

  const scrollToElement = (elementId: string) => {
    document
      .getElementById(`el-${elementId}`)
      ?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  const actionButton =
    "rounded-md border border-edge bg-raised px-2 py-1 text-[11px] text-ink-dim transition-colors hover:border-accent hover:text-accent disabled:pointer-events-none disabled:opacity-40";

  return (
    <aside className="flex min-w-[420px] flex-1 basis-[54%] flex-col bg-surface">
      <div className="flex items-center justify-between gap-3 border-b border-edge px-5 py-2.5">
        <span className="text-xs font-medium tracking-wide text-ink-dim uppercase">
          Specification
        </span>
        <div className="flex items-center gap-1.5">
          <button
            className={actionButton}
            onClick={onUndo}
            disabled={busy || version.index === 0}
            title="Step back one version"
          >
            ‹
          </button>
          <span className="px-0.5 text-[11px] text-ink-faint tabular-nums">
            v{version.index + 1}/{version.count}
          </span>
          <button
            className={actionButton}
            onClick={onRedo}
            disabled={busy || version.index >= version.count - 1}
            title="Step forward one version"
          >
            ›
          </button>
          <span className="mx-1 h-4 w-px bg-edge" />
          <a
            className={actionButton + (hasContent ? "" : " pointer-events-none opacity-40")}
            href="/api/export/docx"
            download
            title="Export the section as .docx with the assumptions schedule"
          >
            Export .docx
          </a>
          <a
            className={actionButton}
            href="/api/project/save"
            download
            title="Save the project (conversation + document) as JSON"
          >
            Save
          </a>
          <button
            className={actionButton}
            onClick={() => fileRef.current?.click()}
            disabled={busy}
            title="Open a saved project file"
          >
            Open
          </button>
          <input
            ref={fileRef}
            type="file"
            accept=".json,application/json"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) onLoadProject(file);
              e.target.value = "";
            }}
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-6">
        {hasContent && doc ? (
          <SpecDocument doc={doc} changedIds={changedIds} />
        ) : (
          <EmptyState />
        )}
      </div>

      {openItems.length > 0 && (
        <div className="max-h-44 overflow-y-auto border-t border-edge bg-bg/60 px-5 py-2.5">
          <p className="text-[11px] font-medium tracking-wide text-ink-dim uppercase">
            Open items ({openItems.length})
          </p>
          <ul className="mt-1.5 space-y-1">
            {openItems.map((item) => (
              <li key={item.id}>
                <button
                  className="flex w-full items-baseline gap-2 rounded px-1 py-0.5 text-left text-xs text-ink-dim transition-colors hover:bg-raised hover:text-ink"
                  onClick={() => scrollToElement(item.element_id)}
                  title="Jump to this provision"
                >
                  <span
                    className={`h-1.5 w-1.5 shrink-0 translate-y-[-1px] rounded-full ${kindDot[item.kind]}`}
                  />
                  <span className="shrink-0 font-medium text-ink tabular-nums">
                    {item.ref}
                  </span>
                  <span className="truncate">
                    {item.kind === "needs_input" ? "needs input — " : "TBD — "}
                    {item.label}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </aside>
  );
}
