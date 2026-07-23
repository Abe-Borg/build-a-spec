/**
 * Renders one chat-authored figure inline, with downloads.
 *
 * Diagrams (mermaid/svg) are sanitized to a static SVG string (see
 * lib/figures.ts) and shown inside a `sandbox=""` iframe — no script
 * execution, no reach into the pywebview bridge. Tables render as plain,
 * React-escaped HTML. SVG/PNG downloads are built client-side from the
 * sanitized SVG; CSV comes from the backend.
 */
import { memo, useEffect, useMemo, useRef, useState } from "react";
import type { Figure } from "../types";
import { figureCsvUrl } from "../lib/api";
import {
  buildSvgSrcDoc,
  downloadBlob,
  figureFilename,
  figureToSvg,
  svgAspectRatio,
  svgBlob,
  svgToPngBlob,
} from "../lib/figures";

const kindBadge: Record<Figure["kind"], string> = {
  mermaid: "diagram",
  svg: "schematic",
  table: "table",
};

const dlBtn =
  "rounded border border-edge bg-raised px-2 py-0.5 text-ink-dim transition-colors hover:border-accent hover:text-accent disabled:pointer-events-none disabled:opacity-40";

/** Sanitized SVG in a scriptless sandbox iframe, height tracked to its
 *  aspect ratio as the pane resizes. */
function SvgFrame({ svg, title }: { svg: string; title: string }) {
  const ref = useRef<HTMLIFrameElement>(null);
  const ratio = useMemo(() => svgAspectRatio(svg), [svg]);
  const srcDoc = useMemo(() => buildSvgSrcDoc(svg), [svg]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const resize = () => {
      const w = el.clientWidth;
      if (w > 0) el.style.height = `${Math.round(w / ratio)}px`;
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(el);
    return () => ro.disconnect();
  }, [ratio, srcDoc]);

  return (
    <div className="overflow-hidden rounded-md border border-paper-edge bg-white">
      <iframe
        ref={ref}
        title={title}
        sandbox=""
        srcDoc={srcDoc}
        loading="lazy"
        className="block w-full border-0"
        style={{ height: 240 }}
      />
    </div>
  );
}

function DataTable({ columns, rows }: { columns: string[]; rows: string[][] }) {
  return (
    <div className="overflow-x-auto rounded-md border border-edge">
      <table className="w-full border-collapse text-left text-xs">
        <thead>
          <tr className="bg-raised">
            {columns.map((col, i) => (
              <th
                key={i}
                className="border-b border-edge px-2.5 py-1.5 font-semibold text-ink"
              >
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, r) => (
            <tr key={r} className="odd:bg-surface/40">
              {row.map((cell, c) => (
                <td
                  key={c}
                  className="border-b border-edge/60 px-2.5 py-1 align-top text-ink-dim"
                >
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FigureCard({
  figure,
  onDelete,
}: {
  figure: Figure;
  onDelete?: (fid: string) => void;
}) {
  const isDiagram = figure.kind === "mermaid" || figure.kind === "svg";
  const [svg, setSvg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pngBusy, setPngBusy] = useState(false);
  // Minimize is a non-destructive view fold: the figure stays in the session
  // (and in every save) — only its rendered body/downloads collapse. Local
  // state, so it survives in-session re-syncs (the card is keyed by fid) and
  // resets naturally on New session / project load (the bubbles remount).
  const [collapsed, setCollapsed] = useState(false);
  // Two-step delete confirm, matching the doc-tree row 🗑 (SpecDocument).
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  // Resolve mermaid/svg to a sanitized SVG string once per figure.
  useEffect(() => {
    if (!isDiagram) return;
    let alive = true;
    setSvg(null);
    setError(null);
    figureToSvg(figure)
      .then((s) => alive && setSvg(s))
      .catch((e) => alive && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, [figure, isDiagram]);

  const downloadSvg = () => {
    if (svg) downloadBlob(svgBlob(svg), figureFilename(figure.title, "svg"));
  };
  const downloadPng = async () => {
    if (!svg) return;
    setPngBusy(true);
    try {
      downloadBlob(await svgToPngBlob(svg), figureFilename(figure.title, "png"));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPngBusy(false);
    }
  };

  return (
    <figure className="my-3 rounded-lg border border-edge bg-surface/60 p-3">
      <figcaption className="mb-2 flex items-start gap-2">
        <span className="mt-0.5 shrink-0 rounded bg-edge/60 px-1.5 py-px text-[10px] font-medium tracking-wide text-ink-faint uppercase">
          {kindBadge[figure.kind]}
        </span>
        <span className="flex-1 text-sm font-medium text-ink">{figure.title}</span>
        <button
          type="button"
          onClick={() => setCollapsed((c) => !c)}
          aria-expanded={!collapsed}
          title={collapsed ? "Expand this figure" : "Minimize this figure"}
          className="shrink-0 rounded px-1 text-ink-faint transition-colors hover:text-ink"
        >
          {collapsed ? "▸" : "▾"}
        </button>
        {onDelete &&
          (confirmingDelete ? (
            <span className="inline-flex shrink-0 items-center gap-1 text-[11px]">
              <span className="text-err">Remove?</span>
              <button
                type="button"
                onClick={() => {
                  setConfirmingDelete(false);
                  onDelete(figure.fid);
                }}
                title="Confirm — remove this figure"
                className="rounded px-1 text-ink-faint transition-colors hover:text-err"
              >
                ✓
              </button>
              <button
                type="button"
                onClick={() => setConfirmingDelete(false)}
                title="Keep this figure"
                className="rounded px-1 text-ink-faint transition-colors hover:text-ink"
              >
                ✕
              </button>
            </span>
          ) : (
            <button
              type="button"
              onClick={() => setConfirmingDelete(true)}
              title="Remove this figure"
              className="shrink-0 rounded px-1 text-ink-faint transition-colors hover:text-err"
            >
              ✕
            </button>
          ))}
      </figcaption>

      {collapsed ? (
        <p className="text-xs text-ink-faint italic">
          Minimized — click ▸ to expand.
        </p>
      ) : (
        <>
          {isDiagram ? (
            error ? (
              <div className="rounded-md border border-err/40 bg-err/10 p-3 text-xs text-err">
                Couldn&apos;t render this figure: {error}
              </div>
            ) : svg ? (
              <SvgFrame svg={svg} title={figure.alt_text || figure.title} />
            ) : (
              <div className="rounded-md border border-edge bg-raised p-6 text-center text-xs text-ink-faint">
                Rendering…
              </div>
            )
          ) : (
            <DataTable columns={figure.columns} rows={figure.rows} />
          )}

          {figure.caption && (
            <p className="mt-2 text-xs leading-relaxed text-ink-faint">
              {figure.caption}
            </p>
          )}

          <div className="mt-2 flex items-center gap-2 text-[11px]">
            <span className="text-ink-faint">Download</span>
            {isDiagram ? (
              <>
                <button
                  type="button"
                  className={dlBtn}
                  onClick={downloadSvg}
                  disabled={!svg}
                  title="Download as a vector SVG"
                >
                  SVG
                </button>
                <button
                  type="button"
                  className={dlBtn}
                  onClick={downloadPng}
                  disabled={!svg || pngBusy}
                  title="Download as a PNG image"
                >
                  {pngBusy ? "PNG…" : "PNG"}
                </button>
              </>
            ) : (
              <a
                className={dlBtn}
                href={figureCsvUrl(figure.fid)}
                download={figureFilename(figure.title, "csv")}
                title="Download the table as CSV"
              >
                CSV
              </a>
            )}
          </div>
        </>
      )}
    </figure>
  );
}

export default memo(FigureCard);
