/**
 * Figure rendering + download helpers — and the security boundary for
 * model-authored visual content.
 *
 * Figure `source` (SVG markup, or Mermaid text the library turns into SVG) is
 * UNTRUSTED: the model wrote it. This app runs inside a pywebview shell that
 * exposes a native bridge (`window.pywebview.api`), so an injection here is
 * worse than a plain-web XSS. Three independent layers contain it:
 *
 *   1. Mermaid runs with `securityLevel: 'strict'` (+ htmlLabels off), so
 *      diagram text is treated as data, never markup — its SVG output carries
 *      no scripts or click handlers.
 *   2. Every SVG (Mermaid output OR a raw `svg` figure) is passed through
 *      DOMPurify with the SVG profile, which strips <script>, event handlers,
 *      <foreignObject> (an HTML/script escape hatch), and javascript: URIs.
 *   3. The sanitized SVG is displayed inside a `sandbox=""` iframe (no
 *      allow-scripts → no script execution, no same-origin → no reach into
 *      the parent/bridge) whose srcdoc carries a strict CSP
 *      (`default-src 'none'`) that blocks any external resource load.
 *
 * Downloads (SVG/PNG) are produced client-side from the SANITIZED string —
 * the server never serves executable SVG. CSV downloads come from the
 * backend (`/api/figure/{fid}/csv`), which only ever emits `text/csv`.
 */
import DOMPurify from "dompurify";
import type { Figure } from "../types";

/** DOMPurify with the SVG profile + belt-and-suspenders tag removals. */
export function sanitizeSvg(markup: string): string {
  return DOMPurify.sanitize(markup, {
    USE_PROFILES: { svg: true, svgFilters: true },
    // The SVG profile already blocks these; naming them makes the intent
    // explicit and survives a profile change. <foreignObject> is the classic
    // SVG→HTML→script escape hatch; <script> is obvious.
    FORBID_TAGS: ["script", "foreignObject"],
    ADD_ATTR: ["viewBox", "preserveAspectRatio"],
  });
}

/** Wrap sanitized SVG in a scriptless, network-denied HTML document for the
 *  sandbox iframe. Figures render on white ("on paper"), legible in any theme. */
export function buildSvgSrcDoc(svg: string): string {
  return (
    `<!doctype html><html><head><meta charset="utf-8">` +
    `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; ` +
    `img-src data:; style-src 'unsafe-inline'; font-src data:;">` +
    `<style>html,body{margin:0;padding:0;background:#fff}` +
    `svg{max-width:100%;height:auto;display:block;margin:0 auto}</style>` +
    `</head><body>${svg}</body></html>`
  );
}

// --- Mermaid (lazy-loaded; large, and not every session needs it) ----------

let mermaidReady: Promise<typeof import("mermaid").default> | null = null;

function getMermaid() {
  if (!mermaidReady) {
    mermaidReady = import("mermaid").then((mod) => {
      mod.default.initialize({
        startOnLoad: false,
        securityLevel: "strict",
        theme: "default",
        // htmlLabels must be OFF at the TOP level (not just under flowchart)
        // to make Mermaid emit SVG <text> labels instead of <foreignObject>
        // HTML. DOMPurify's SVG profile hardens foreignObject away (a known
        // mXSS vector), which would otherwise strip every diagram label.
        htmlLabels: false,
        flowchart: { htmlLabels: false },
      });
      return mod.default;
    });
  }
  return mermaidReady;
}

let mermaidSeq = 0;

/** Render Mermaid text to a SANITIZED SVG string. Throws on invalid syntax. */
export async function renderMermaidToSvg(source: string): Promise<string> {
  const mermaid = await getMermaid();
  const id = `bas-mmd-${(mermaidSeq += 1)}`;
  const { svg } = await mermaid.render(id, source);
  return sanitizeSvg(svg);
}

/** Resolve a figure to its sanitized SVG (mermaid → render, svg → sanitize). */
export async function figureToSvg(figure: Figure): Promise<string> {
  if (figure.kind === "mermaid") return renderMermaidToSvg(figure.source);
  if (figure.kind === "svg") return sanitizeSvg(figure.source);
  throw new Error(`Figure kind ${figure.kind} has no SVG.`);
}

// --- Sizing + rasterization ------------------------------------------------

/** Best-effort intrinsic pixel size from width/height attrs or the viewBox. */
export function svgPixelSize(svg: string): { width: number; height: number } {
  let width = 0;
  let height = 0;
  const viewBox = svg.match(
    /viewBox\s*=\s*["']\s*[-\d.]+[ ,]+[-\d.]+[ ,]+([\d.]+)[ ,]+([\d.]+)/i,
  );
  if (viewBox) {
    width = parseFloat(viewBox[1]);
    height = parseFloat(viewBox[2]);
  }
  const w = svg.match(/\bwidth\s*=\s*["']([\d.]+)(?:px)?["']/i);
  const h = svg.match(/\bheight\s*=\s*["']([\d.]+)(?:px)?["']/i);
  if (w) width = parseFloat(w[1]) || width;
  if (h) height = parseFloat(h[1]) || height;
  return { width: width || 800, height: height || 600 };
}

/** width / height, clamped to a sane range so a degenerate SVG can't produce a
 *  1px-tall or absurdly tall iframe. */
export function svgAspectRatio(svg: string): number {
  const { width, height } = svgPixelSize(svg);
  const ratio = width / height;
  if (!isFinite(ratio) || ratio <= 0) return 4 / 3;
  return Math.min(6, Math.max(0.2, ratio));
}

/** Force explicit width/height on the root <svg> so an <img> loads it at a
 *  real intrinsic size before we rasterize (some SVGs only set a viewBox). */
function withPixelSize(svg: string, width: number, height: number): string {
  return svg.replace(/<svg\b([^>]*)>/i, (_m, attrs: string) => {
    const cleaned = attrs.replace(
      /\s(?:width|height)\s*=\s*["'][^"']*["']/gi,
      "",
    );
    return `<svg${cleaned} width="${width}" height="${height}">`;
  });
}

/** Rasterize sanitized SVG to a PNG Blob via an offscreen canvas.
 *  The SVG has no external refs (sanitized) so the canvas is never tainted. */
export async function svgToPngBlob(svg: string, scale = 2): Promise<Blob> {
  const { width, height } = svgPixelSize(svg);
  const sized = withPixelSize(svg, width, height);
  const url =
    "data:image/svg+xml;charset=utf-8," + encodeURIComponent(sized);

  const img = new Image();
  await new Promise<void>((resolve, reject) => {
    img.onload = () => resolve();
    img.onerror = () =>
      reject(new Error("Could not render the figure to an image."));
    img.src = url;
  });

  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(width * scale));
  canvas.height = Math.max(1, Math.round(height * scale));
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Canvas is unavailable for PNG export.");
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(img, 0, 0, canvas.width, canvas.height);

  return await new Promise<Blob>((resolve, reject) => {
    canvas.toBlob(
      (blob) => (blob ? resolve(blob) : reject(new Error("PNG encoding failed."))),
      "image/png",
    );
  });
}

// --- Downloads -------------------------------------------------------------

export function svgBlob(svg: string): Blob {
  return new Blob([svg], { type: "image/svg+xml;charset=utf-8" });
}

/** Trigger a browser download of a Blob under `filename`. */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** A filesystem-safe filename from a figure title + extension. */
export function figureFilename(title: string, ext: string): string {
  const base =
    (title || "figure")
      .replace(/[^\w .-]+/g, "_")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, 80) || "figure";
  return `${base}.${ext}`;
}
