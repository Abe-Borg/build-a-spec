import type { ReactNode } from "react";

/**
 * Hover-tooltip wrapper that works even when the wrapped control is disabled.
 *
 * We keep action buttons visible-but-disabled (rather than hiding them) so the
 * feature is discoverable, and the disabled button needs to explain WHY it's
 * unavailable on hover. But a native `title` does not fire on a `disabled`
 * <button>: the element is inert and our shared style adds
 * `disabled:pointer-events-none`, so it never receives the hover at all.
 *
 * Carrying the title on this span fixes both states with one source of truth:
 *  - disabled child (pointer-events:none) → the hover falls through to this
 *    span, which shows the title;
 *  - enabled child with no title of its own → the browser shows the nearest
 *    ancestor's title, i.e. this span.
 *
 * So the wrapped button should NOT set its own `title`; pass the message here.
 */
export default function Tip({
  tip,
  className,
  children,
}: {
  tip: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <span title={tip} className={`inline-flex ${className ?? ""}`.trim()}>
      {children}
    </span>
  );
}
