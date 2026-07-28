/**
 * "What's new" — the release notes the user sees after the app updates.
 *
 * Two ways in, one component:
 *
 * - **Automatically, once per update.** `App` asks `/api/release-notes` on
 *   mount; anything unseen opens this. Dismissing marks the version seen, so
 *   it does not reappear next launch.
 * - **On demand, from Settings.** Same modal, `?all=true`, so the current
 *   version's notes are readable whenever the user wants them.
 *
 * The content is bundled with the build (`backend/release_notes.py`), not
 * fetched — a freshly-updated app can say what changed with no network at
 * all, which is the same posture the trust dossier promises everywhere else.
 *
 * Container conventions follow DeveloperToolsModal (z-[60] scrollable sheet,
 * `useDialogFocus` for Escape / Tab containment / focus restoration).
 */
import { useRef } from "react";
import { useDialogFocus } from "../lib/dialogFocus";
import type { ReleaseNote } from "../types";

export function WhatsNewModal({
  open,
  entries,
  onClose,
}: {
  open: boolean;
  entries: ReleaseNote[];
  onClose: () => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  useDialogFocus(open, containerRef, closeRef, onClose);

  if (!open || entries.length === 0) return null;

  const multiple = entries.length > 1;

  return (
    <div
      className="fixed inset-0 z-[60] flex items-start justify-center bg-black/60 p-4 pt-10"
      onClick={onClose}
    >
      <div
        ref={containerRef}
        role="dialog"
        aria-modal="true"
        aria-label="What's new"
        onClick={(e) => e.stopPropagation()}
        className="flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-edge bg-surface shadow-2xl"
      >
        <div className="flex items-start justify-between gap-4 border-b border-edge px-6 py-4">
          <div>
            <h2 className="font-[family-name:var(--font-display)] text-lg font-semibold">
              What&rsquo;s new
            </h2>
            <p className="mt-0.5 text-xs text-ink-dim">
              {multiple
                ? `${entries.length} updates since you were last here`
                : `Version ${entries[0].version} · ${entries[0].date}`}
            </p>
          </div>
          <button
            ref={closeRef}
            onClick={onClose}
            aria-label="Close"
            className="rounded-md px-2 py-0.5 text-ink-dim transition-colors hover:bg-raised hover:text-ink"
          >
            ✕
          </button>
        </div>

        <div className="min-h-0 flex-1 space-y-8 overflow-y-auto px-6 py-5">
          {entries.map((note) => (
            <article key={note.version} className="space-y-5">
              <header className="space-y-2">
                {multiple && (
                  <p className="text-[11px] font-medium uppercase tracking-wide text-ink-dim">
                    Version {note.version} · {note.date}
                  </p>
                )}
                <h3 className="font-[family-name:var(--font-display)] text-xl font-semibold text-ink">
                  {note.headline}
                </h3>
                <p className="text-sm leading-relaxed text-ink-dim">
                  {note.summary}
                </p>
              </header>

              {note.sections.map((section) => (
                <section key={section.title} className="space-y-3">
                  <h4 className="text-[11px] font-medium uppercase tracking-wide text-accent">
                    {section.title}
                  </h4>
                  <ul className="space-y-3">
                    {section.items.map((item) => (
                      <li
                        key={item.title}
                        className="rounded-xl border border-edge bg-raised/40 px-4 py-3"
                      >
                        <p className="text-sm font-medium text-ink">
                          {item.title}
                        </p>
                        <p className="mt-1 text-sm leading-relaxed text-ink-dim">
                          {item.body}
                        </p>
                      </li>
                    ))}
                  </ul>
                </section>
              ))}
            </article>
          ))}
        </div>

        <div className="flex items-center justify-between gap-3 border-t border-edge px-6 py-3">
          <p className="text-xs text-ink-faint">
            You can reopen this any time from Settings.
          </p>
          <button
            onClick={onClose}
            className="rounded-lg bg-accent px-4 py-1.5 text-sm text-white transition-colors hover:bg-accent-hover"
          >
            Got it
          </button>
        </div>
      </div>
    </div>
  );
}
