/**
 * The research findings report: a roomy, scrollable modal presenting what the
 * requirements-research fan-out found, grouped by dimension (one per research
 * "agent"). Each dimension shows its completion status + telemetry (findings,
 * grounded count, searches/fetches) and its items in full — requirement,
 * authority, code reference, category, confidence, notes, and grounded
 * sources. The Research drawer shows a terse inline list; this is the full
 * read-through view. Read-only — the same profile is already in the chat
 * model's per-turn context, so nothing here mutates the spec.
 */
import { useEffect } from "react";
import type {
  ResearchDimensionView,
  ResearchItemView,
  ResearchProfileView,
} from "../types";

interface Props {
  open: boolean;
  profile: ResearchProfileView | null | undefined;
  onClose: () => void;
}

/** Prefer the stored dimension title; legacy profiles saved before it existed
 *  fall back to a title-cased id so headings never read as raw snake_case. */
function dimensionLabel(dim: ResearchDimensionView): string {
  if (dim.title) return dim.title;
  return dim.dimension_id
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join(" ");
}

function projectLine(project: Record<string, string> | undefined): string {
  if (!project) return "";
  const where = [project.city, project.state_or_province, project.country]
    .filter(Boolean)
    .join(", ");
  const client = project.client_name ? `Client: ${project.client_name}` : "";
  return [where, client].filter(Boolean).join(" — ");
}

/** Show a citation as its bare host — the full URL rides the title/href. */
function sourceHost(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

function ItemRow({ item }: { item: ResearchItemView }) {
  const details: string[] = [];
  if (item.authority) details.push(item.authority);
  if (item.code_reference) details.push(item.code_reference);
  if (item.category) details.push(item.category.replace(/_/g, " "));
  details.push(`confidence ${Math.round(item.confidence * 100)}%`);
  return (
    <li className="rounded border border-edge/60 bg-bg/40 px-2.5 py-1.5">
      <div className="flex items-baseline gap-2">
        <span
          className={`mt-1 h-1.5 w-1.5 shrink-0 rounded-full ${
            item.grounded ? "bg-ok" : "bg-warn"
          }`}
          title={item.grounded ? "Grounded in a retrieved source" : "Unverified"}
        />
        <div className="min-w-0 flex-1">
          <p className="text-[12px] text-ink-dim">
            {!item.grounded && (
              <span className="font-semibold text-warn">[UNVERIFIED] </span>
            )}
            {item.actionability === "process_advisory" && (
              <span className="font-semibold text-ink-faint">[PROCESS] </span>
            )}
            {item.requirement}
          </p>
          <p className="mt-0.5 text-[11px] text-ink-faint">
            {details.join(" · ")}
          </p>
          {item.notes && (
            <p className="mt-0.5 text-[11px] text-ink-faint italic">
              {item.notes}
            </p>
          )}
          {item.accepted_sources.length > 0 && (
            <p className="mt-0.5 flex flex-wrap gap-x-2 gap-y-0.5">
              {item.accepted_sources.map((url) => (
                <a
                  key={url}
                  href={url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-[11px] text-accent hover:underline"
                  title={url}
                >
                  {sourceHost(url)}
                </a>
              ))}
            </p>
          )}
        </div>
      </div>
    </li>
  );
}

function DimensionSection({
  dim,
  items,
}: {
  dim: ResearchDimensionView;
  items: ResearchItemView[];
}) {
  const failed = dim.status !== "completed";
  const telemetry = [
    `${items.length} finding${items.length === 1 ? "" : "s"}`,
    `${items.filter((i) => i.grounded).length} grounded`,
  ];
  if (dim.web_search_requests)
    telemetry.push(`${dim.web_search_requests} searches`);
  if (dim.web_fetch_requests) telemetry.push(`${dim.web_fetch_requests} fetches`);

  return (
    <section>
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <h3 className="text-[13px] font-semibold text-ink">
          {dimensionLabel(dim)}
        </h3>
        <span
          className={`rounded-full px-1.5 py-px text-[10px] font-medium ${
            failed ? "bg-err/15 text-err" : "bg-ok/15 text-ok"
          }`}
        >
          {failed ? "failed" : "completed"}
        </span>
        <span className="text-[11px] text-ink-faint">
          {telemetry.join(" · ")}
        </span>
      </div>
      {failed && dim.error && (
        <p className="mt-1 text-[11px] text-err">{dim.error}</p>
      )}
      {items.length > 0 ? (
        <ul className="mt-1.5 space-y-1">
          {items.map((item) => (
            <ItemRow key={item.item_id} item={item} />
          ))}
        </ul>
      ) : (
        !failed && (
          <p className="mt-1 text-[11px] text-ink-faint">
            No discrete findings from this dimension.
          </p>
        )
      )}
    </section>
  );
}

export default function ResearchReportModal({ open, profile, onClose }: Props) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open || !profile) return null;

  const items = profile.items ?? [];
  const dims = profile.dimension_statuses ?? [];
  const groundedTotal = items.filter((i) => i.grounded).length;
  const completedDims = dims.filter((d) => d.status === "completed").length;
  const failedDims = dims.length - completedDims;

  // Group items by dimension in module-declaration order (the order
  // dimension_statuses arrives in). Any item whose dimension isn't listed
  // falls into a trailing "Other" bucket so nothing is silently dropped.
  const known = new Set(dims.map((d) => d.dimension_id));
  const orphanItems = items.filter((i) => !known.has(i.dimension_id));

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 p-6 pt-16"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="Research findings report"
    >
      <div
        className="flex max-h-[80vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-edge bg-surface shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header: title + summary + close */}
        <div className="flex items-start justify-between gap-4 border-b border-edge px-6 py-4">
          <div className="min-w-0">
            <h2 className="font-[family-name:var(--font-display)] text-lg font-semibold text-ink">
              Research findings report
            </h2>
            {projectLine(profile.project) && (
              <p className="mt-1 truncate text-[12px] text-ink-dim">
                {projectLine(profile.project)}
              </p>
            )}
            <p className="mt-0.5 text-[11px] text-ink-faint">
              Researched {profile.research_date || "—"} · {completedDims}/
              {dims.length} dimension{dims.length === 1 ? "" : "s"} completed
              {failedDims > 0 && ` (${failedDims} failed)`} · {items.length}{" "}
              finding{items.length === 1 ? "" : "s"}, {groundedTotal} grounded
            </p>
          </div>
          <button
            onClick={onClose}
            className="shrink-0 rounded-lg px-2 py-1 text-ink-dim transition-colors hover:text-ink"
            title="Close"
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        {/* Legend */}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-edge/60 px-6 py-2 text-[11px] text-ink-faint">
          <span className="flex items-center gap-1.5">
            <span className="h-1.5 w-1.5 rounded-full bg-ok" />
            grounded in a retrieved source
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-1.5 w-1.5 rounded-full bg-warn" />
            <span className="font-semibold text-warn">[UNVERIFIED]</span> — a
            lead, not a fact
          </span>
          <span>
            <span className="font-semibold text-ink-faint">[PROCESS]</span> —
            team advisory, never spec text
          </span>
        </div>

        {/* Body */}
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-6 py-4">
          {dims.map((dim) => (
            <DimensionSection
              key={dim.dimension_id}
              dim={dim}
              items={items.filter((i) => i.dimension_id === dim.dimension_id)}
            />
          ))}
          {orphanItems.length > 0 && (
            <DimensionSection
              dim={{
                dimension_id: "other",
                title: "Other findings",
                status: "completed",
                item_count: orphanItems.length,
                grounded_count: orphanItems.filter((i) => i.grounded).length,
                web_search_requests: 0,
                web_fetch_requests: 0,
                error: "",
              }}
              items={orphanItems}
            />
          )}
          {items.length === 0 && completedDims === 0 && (
            <p className="text-[12px] text-ink-faint">
              No findings were produced.
            </p>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between gap-3 border-t border-edge px-6 py-3">
          <p className="text-[11px] text-ink-faint">
            These findings are in the chat model&apos;s context on every turn.
          </p>
          <button
            onClick={onClose}
            className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
