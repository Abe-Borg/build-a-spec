/**
 * Advisory lint issues + the standards editions in effect, rendered under
 * the document panel. Issues are deterministic (no API) and recomputed on
 * every document change; clicking one jumps to the offending block.
 * The standards strip is collapsed to a one-line summary; expanding it
 * lists every edition in effect, with jurisdiction overrides highlighted
 * and their recorded adoption basis shown.
 */
import { useState } from "react";
import type { LintIssue, StandardInfo } from "../types";

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
    <div className="max-h-44 overflow-y-auto border-t border-edge bg-bg/60 px-5 py-2.5">
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

export function StandardsStrip({ standards }: { standards: StandardInfo[] }) {
  const [expanded, setExpanded] = useState(false);
  if (standards.length === 0) return null;
  const overrides = standards.filter((s) => s.is_override);

  return (
    <div className="border-t border-edge bg-bg/80 px-5 py-2">
      <button
        className="flex w-full items-baseline gap-2 text-left text-[11px] text-ink-faint transition-colors hover:text-ink-dim"
        onClick={() => setExpanded((v) => !v)}
        title="Standards editions in effect (module defaults + jurisdiction overrides)"
      >
        <span className="font-medium tracking-wide uppercase">Standards</span>
        <span className="truncate">
          {standards.length} pinned edition
          {standards.length === 1 ? "" : "s"}
          {overrides.length > 0 &&
            ` · ${overrides.length} jurisdiction override${
              overrides.length === 1 ? "" : "s"
            }`}
        </span>
        <span className="ml-auto shrink-0">{expanded ? "▾" : "▸"}</span>
      </button>
      {expanded && (
        <ul className="mt-1.5 max-h-40 space-y-0.5 overflow-y-auto">
          {standards.map((s) => (
            <li
              key={s.name}
              className="flex items-baseline gap-2 px-1 text-[11px]"
              title={s.title || s.name}
            >
              <span className="shrink-0 font-medium text-ink-dim">
                {s.name}
              </span>
              <span
                className={
                  s.is_override
                    ? "font-semibold text-warn"
                    : "text-ink-faint"
                }
              >
                {s.edition}
              </span>
              {s.is_override && (
                <span className="truncate text-ink-faint italic">
                  override — {s.basis}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
