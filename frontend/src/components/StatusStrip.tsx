import type { StreamStatus } from "../types";

/**
 * The "app is not frozen" guarantee: a shimmering, pulsing-dots line that
 * names what the model is doing right now. Rendered inside the streaming
 * assistant bubble and replaced on each status frame; hidden the moment
 * real text or a thinking summary starts flowing.
 */
const LABELS: Record<string, string> = {
  working: "Working…",
  thinking: "Thinking…",
  searching: "Searching the web…",
  fetching: "Reading a source…",
  drafting: "Writing to the document…",
  writing: "Writing…",
};

export default function StatusStrip({ status }: { status: StreamStatus }) {
  const label = LABELS[status.kind] ?? "Working…";
  const suffix =
    status.kind === "drafting" && status.progress_chars
      ? ` ${(status.progress_chars / 1000).toFixed(1)}k`
      : "";
  return (
    <div className="status-strip" aria-live="polite">
      <span className="status-dots" aria-hidden="true">
        <span />
        <span />
        <span />
      </span>
      <span className="status-shimmer">
        {label}
        {suffix}
      </span>
    </div>
  );
}
