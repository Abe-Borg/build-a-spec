/**
 * The live document panel. Phase 1 renders the SectionFormat skeleton as an
 * empty state; Phase 2 wires it to the server-owned document tree and
 * streams `apply_spec_edits` patches in with change highlighting and a
 * version stepper.
 */
export default function ArtifactPanel() {
  return (
    <aside className="flex min-w-[420px] flex-1 basis-[54%] flex-col bg-surface">
      <div className="flex items-center justify-between border-b border-edge px-5 py-2.5">
        <span className="text-xs font-medium tracking-wide text-ink-dim uppercase">
          Specification
        </span>
        <span className="rounded-full border border-edge px-2.5 py-0.5 text-[11px] text-ink-faint">
          Live document — next milestone
        </span>
      </div>

      <div className="flex-1 overflow-y-auto p-6">
        <div className="mx-auto max-w-2xl rounded-xl border border-paper-edge bg-paper px-10 py-12 text-paper-ink shadow-[0_2px_16px_rgba(0,0,0,0.25)]">
          <div className="text-center">
            <p className="text-[13px] font-semibold tracking-wide">
              SECTION 21 13 13
            </p>
            <p className="mt-1 text-[13px] font-semibold tracking-wide">
              WET-PIPE SPRINKLER SYSTEMS
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
            appear and update in place, with changes highlighted and every
            [TBD] tracked until it&apos;s resolved.
          </p>
        </div>
      </div>
    </aside>
  );
}
