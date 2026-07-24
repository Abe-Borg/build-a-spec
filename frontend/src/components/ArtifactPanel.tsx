/**
 * The live document panel: SectionFormat rendering of the server-owned
 * tree, a per-turn version stepper (undo/redo), export / save / open
 * actions, and the open-items list ([TBD] markers + needs-input blocks).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  EditOp,
  ImportReport,
  LintIssue,
  OpenItem,
  QcSnapshot,
  ReadinessPayload,
  ResearchSnapshot,
  SectionDiff,
  SectionDiffPayload,
  SourceCapabilitiesState,
  SourcePreservationState,
  SpecDoc,
  StandardInfo,
  UsageSummary,
} from "../types";
import IssuesDrawer, { StandardsStrip } from "./IssuesDrawer";
import QCDrawer from "./QCDrawer";
import ResearchDrawer from "./ResearchDrawer";
import ReviewDrawer from "./ReviewDrawer";
import SpecDocument from "./SpecDocument";
import { sourceCapabilitiesExpected } from "../lib/sourceCapabilities";
import Tip from "./Tip";
import ConfirmDialog from "./ConfirmDialog";

interface Props {
  doc: SpecDoc | null;
  openItems: OpenItem[];
  lintIssues: LintIssue[];
  standards: StandardInfo[];
  profileComplete: boolean;
  research: ResearchSnapshot | null;
  qc: QcSnapshot | null;
  readiness: ReadinessPayload | null;
  usage: UsageSummary | null;
  changedIds: ReadonlySet<string>;
  baselineIndex: number | null;
  importReport: ImportReport | null;
  sourceAvailable: boolean;
  preservationReady: boolean;
  sourcePreservation: SourcePreservationState | null;
  sourceCapabilities: SourceCapabilitiesState | null;
  busy: boolean;
  onUndo: () => void;
  onRedo: () => void;
  onEditDoc: (ops: EditOp[]) => void;
  onLoadProject: (file: File) => void;
  onImportMaster: (file: File) => void;
  onStartResearch: () => void;
  onStopResearch: () => void;
  onStartQc: () => void;
  onStopQc: () => void;
  onApplyQc: (findingIds: string[]) => void;
  onDismissQc: (findingId: string, reason: string) => Promise<void>;
  onDraftFull: () => void;
  onAskModel: (text: string) => void;
  onFetchDiff: (base: number, cur?: number) => Promise<SectionDiffPayload>;
  /** Guided-tour "ensure open" nonces (Batch 6), one per drawer. */
  drawerNonces?: {
    review: number;
    research: number;
    qc: number;
    openItems: number;
  };
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
  lintIssues,
  standards,
  profileComplete,
  research,
  qc,
  readiness,
  usage,
  changedIds,
  baselineIndex,
  importReport,
  sourceAvailable,
  preservationReady,
  sourcePreservation,
  sourceCapabilities,
  busy,
  onUndo,
  onRedo,
  onEditDoc,
  onLoadProject,
  onImportMaster,
  onStartResearch,
  onStopResearch,
  onStartQc,
  onStopQc,
  onApplyQc,
  onDismissQc,
  onDraftFull,
  onAskModel,
  onFetchDiff,
  drawerNonces,
}: Props) {
  const fileRef = useRef<HTMLInputElement>(null);
  const importRef = useRef<HTMLInputElement>(null);
  const [pendingImport, setPendingImport] = useState<File | null>(null);
  // Open-items list collapses like the Review / Final QC drawers; the count
  // stays visible in the bar, so nothing is lost at a glance when collapsed.
  const [openItemsExpanded, setOpenItemsExpanded] = useState(false);
  // The tour opens the list by bumping the nonce (same idiom as the drawers).
  const openItemsNonce = drawerNonces?.openItems ?? 0;
  useEffect(() => {
    if (openItemsNonce) setOpenItemsExpanded(true);
  }, [openItemsNonce]);
  // item_id -> short tooltip text for the paper's source chips.
  const sourceLookup = useMemo(() => {
    const map = new Map<string, string>();
    for (const item of research?.profile?.items ?? []) {
      const sources = item.accepted_sources.length
        ? ` — ${item.accepted_sources.join(", ")}`
        : " — [UNVERIFIED]";
      map.set(item.item_id, `${item.requirement}${sources}`);
    }
    return map;
  }, [research]);
  const version = doc?.version ?? { index: 0, count: 1 };
  const hasContent =
    !!doc &&
    (doc.section.number !== "" ||
      doc.section.title !== "" ||
      doc.parts.some((p) => p.articles.length > 0));
  const importedMode = importReport !== null || baselineIndex !== null;
  const passThroughOnly =
    sourcePreservation?.status === "pass_through_only";
  // Retained source bytes may live only in an undone redo tail. Match the
  // backend's active-branch boundary so that pre-import history remains an
  // ordinary source-less document while an active imported branch fails
  // closed if its transient report is unavailable.
  const activeSourceExpected = sourceCapabilitiesExpected(
    sourceCapabilities,
    sourceAvailable,
    baselineIndex,
    version.index,
  );
  const bodyEditingDisabled =
    activeSourceExpected && sourceCapabilities?.status !== "ready";

  // Full-draft affordance (WI1): offered while the document is empty-or-sparse
  // (fewer than 3 articles) — past that, a wholesale draft is the wrong tool.
  // A one-time attention pulse once research has landed and the page is sparse.
  const articleCount =
    doc?.parts.reduce((n, p) => n + p.articles.length, 0) ?? 0;
  const isSparse = articleCount < 3;
  const draftPulse = isSparse && research?.status === "complete";
  // Kept visible (never hidden) so the feature is discoverable, but a wholesale
  // draft is the wrong tool once the section has real content.
  const draftDisabled = busy || !isSparse || bodyEditingDisabled;
  const draftSourceReason =
    sourceCapabilities?.elements.sec?.replace_text?.message;
  const draftTip = bodyEditingDisabled
    ? draftSourceReason
      ? `Body drafting is disabled: ${draftSourceReason}`
      : "Body drafting is disabled because imported-source permissions are unavailable."
    : !isSparse
      ? `The section already has ${articleCount} article${
        articleCount === 1 ? "" : "s"
      } — a one-pass full draft is for starting from an empty or sparse section. Edit inline or ask the model to extend it.`
      : busy
        ? "Finish the current turn first."
        : "Draft the complete section in one pass — every PART and article, stamped from what's known so far. One click to undo.";

  // --- Compare (diff) mode (Batch 5) ---
  const curIndex = version.index;
  const versionCount = version.count;
  const [compareMode, setCompareMode] = useState(false);
  const [compareBase, setCompareBase] = useState<number | null>(null);
  const [diff, setDiff] = useState<SectionDiff | null>(null);
  const [diffError, setDiffError] = useState<string | null>(null);
  const [exportMenuOpen, setExportMenuOpen] = useState(false);
  const diffSeq = useRef(0);

  // Compare is a transient view of (base → current). Any version change
  // (edit, undo/redo) OR a streaming turn invalidates the diff — leave
  // compare mode so a stale diff is never shown.
  useEffect(() => {
    setCompareMode(false);
    setExportMenuOpen(false);
  }, [curIndex, versionCount, busy]);

  // Base-version options: master pinned first, then each other version. The
  // current version is never an option (comparing a version to itself is a
  // no-op the server rejects).
  const baseOptions = useMemo(() => {
    const opts: { value: number; label: string }[] = [];
    if (baselineIndex !== null && baselineIndex !== curIndex) {
      opts.push({
        value: baselineIndex,
        label: `Imported extraction · v${baselineIndex + 1}`,
      });
    }
    for (let i = 0; i < versionCount; i += 1) {
      if (i === curIndex || i === baselineIndex) continue;
      opts.push({
        value: i,
        label: i === 0 ? "Blank start · v1" : `Version v${i + 1}`,
      });
    }
    return opts;
  }, [baselineIndex, curIndex, versionCount]);

  const loadDiff = useCallback(
    async (base: number) => {
      const seq = (diffSeq.current += 1);
      setCompareBase(base);
      setDiff(null);
      setDiffError(null);
      try {
        const payload = await onFetchDiff(base, curIndex);
        if (diffSeq.current === seq) setDiff(payload); // ignore stale responses
      } catch (e) {
        if (diffSeq.current === seq) {
          setDiffError(e instanceof Error ? e.message : String(e));
        }
      }
    },
    [onFetchDiff, curIndex],
  );

  const enterCompare = () => {
    // Never default to the current index (would be a base==cur 400). Prefer
    // the master, else the first valid option (e.g. at index 0 there is no
    // "previous" version, so fall back to the next one).
    const preferred =
      baselineIndex !== null && baselineIndex !== curIndex
        ? baselineIndex
        : baseOptions[0]?.value;
    if (preferred === undefined) return;
    setCompareMode(true);
    void loadDiff(preferred);
  };

  const canCompare = versionCount > 1 || baselineIndex !== null;

  const scrollToElement = (elementId: string) => {
    document
      .getElementById(`el-${elementId}`)
      ?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  const actionButton =
    "rounded-md border border-edge bg-raised px-2 py-1 text-[11px] text-ink-dim transition-colors hover:border-accent hover:text-accent disabled:pointer-events-none disabled:opacity-40";

  return (
    <aside
      className="flex min-w-[420px] flex-1 basis-[54%] flex-col bg-surface"
      data-tour="doc-panel"
    >
      <div className="flex items-center justify-between gap-3 border-b border-edge px-5 py-2.5">
        <div className="flex min-w-0 items-center gap-2.5">
          <span className="flex items-center gap-2 text-xs font-medium tracking-wide text-ink-dim uppercase">
            Specification
            {lintIssues.length > 0 && (
              <span
                className="rounded-full border border-warn/50 bg-warn/15 px-1.5 py-px text-[10px] font-semibold text-warn normal-case"
                title="Advisory lint issues — see the Issues drawer below"
              >
                ⚠ {lintIssues.length}
              </span>
            )}
          </span>
          <Tip tip={draftTip} className="shrink-0">
            <button
              className={`rounded-md bg-accent px-2.5 py-1 text-[11px] font-medium text-white transition-colors hover:bg-accent-hover disabled:pointer-events-none disabled:opacity-40 ${
                draftPulse ? "draft-pulse" : ""
              }`}
              onClick={onDraftFull}
              disabled={draftDisabled}
              data-tour="draft-full"
            >
              ✨ Draft full section
            </button>
          </Tip>
        </div>
        <div className="flex items-center gap-1.5">
          <span
            className="flex items-center gap-1.5"
            data-tour="version-stepper"
          >
            <button
              className={actionButton}
              onClick={onUndo}
              disabled={busy || compareMode || version.index === 0}
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
              disabled={busy || compareMode || version.index >= version.count - 1}
              title="Step forward one version"
            >
              ›
            </button>
          </span>
          <Tip
            tip={
              !canCompare
                ? "Compare needs a prior version or an imported extraction — make an edit or import a DOCX first."
                : busy
                  ? "Finish the current turn first."
                  : "Compare the current version against the extracted import baseline or a prior version."
            }
          >
            <button
              className={
                actionButton + (compareMode ? " border-accent text-accent" : "")
              }
              onClick={() =>
                compareMode ? setCompareMode(false) : enterCompare()
              }
              disabled={busy || !canCompare}
              data-tour="compare"
            >
              {compareMode ? "Exit compare" : "Compare"}
            </button>
          </Tip>
          <span className="mx-1 h-4 w-px bg-edge" />
          {/* Export menu (Batch 5): generated DOCX, or tracked changes over
              the normalized provision tree / a chosen version. Downloads are disabled
              while a turn streams — mid-turn the live doc holds provisional
              edits and only committed versions are downloadable. */}
          <div className="relative" data-tour="export">
            <button
              className={
                actionButton +
                (hasContent && !busy ? "" : " pointer-events-none opacity-40")
              }
              onClick={() => setExportMenuOpen((open) => !open)}
              disabled={!hasContent || busy}
              title="Export the section as .docx"
            >
              Export ▾
            </button>
            {exportMenuOpen && (
              <div
                className="absolute right-0 z-20 mt-1 w-72 rounded-md border border-edge bg-raised py-1 text-[11px] shadow-lg"
                onMouseLeave={() => setExportMenuOpen(false)}
              >
                {importedMode ? (
                  <>
                    {preservationReady ? (
                      <a
                        className="block px-3 py-1.5 font-medium text-accent hover:bg-surface hover:text-accent-hover"
                        href="/api/export/docx?mode=source"
                        download
                        onClick={() => setExportMenuOpen(false)}
                        title={
                          passThroughOnly
                            ? "Return the retained source DOCX exactly; body edits are disabled for this package"
                            : "Clone the original DOCX and apply only verified body edits, including bounded structural edits in eligible isolated Word-list islands"
                        }
                      >
                        {passThroughOnly
                          ? "Export exact original DOCX"
                          : "Export preserved DOCX"}
                      </a>
                    ) : (
                      <span
                        className="block cursor-default px-3 py-1.5 text-ink-faint"
                        title="This project has no usable source package, or its edits exceed the source-preserving boundary"
                      >
                        Export preserved DOCX unavailable
                      </span>
                    )}
                    <a
                      className="block px-3 py-1.5 text-ink-dim hover:bg-surface hover:text-ink"
                      href="/api/export/docx?mode=normalized"
                      download
                      onClick={() => setExportMenuOpen(false)}
                      title="Generate a new DOCX from extracted content; source Word formatting and layout are not preserved"
                    >
                      Export normalized DOCX
                    </a>
                  </>
                ) : (
                  <a
                    className="block px-3 py-1.5 text-ink-dim hover:bg-surface hover:text-ink"
                    href="/api/export/docx?mode=normalized"
                    download
                    onClick={() => setExportMenuOpen(false)}
                    title="Generate a clean DOCX with the assumptions / open-items schedules"
                  >
                    Export clean
                  </a>
                )}
                {baselineIndex !== null ? (
                  <a
                    className="block px-3 py-1.5 text-ink-dim hover:bg-surface hover:text-ink"
                    href="/api/export/docx?redline=master"
                    download
                    onClick={() => setExportMenuOpen(false)}
                    title="Tracked changes over the normalized provision text; this is not a redline of the original DOCX package"
                  >
                    Redline of extracted provisions
                  </a>
                ) : (
                  <span
                    className="block cursor-default px-3 py-1.5 text-ink-faint"
                    title="Import an office DOCX first; the redline compares normalized extracted provisions, not the original Word package"
                  >
                    Redline of extracted provisions
                  </span>
                )}
                {compareMode && compareBase !== null ? (
                  <a
                    className="block px-3 py-1.5 text-ink-dim hover:bg-surface hover:text-ink"
                    href={`/api/export/docx?redline=version&base=${compareBase}`}
                    download
                    onClick={() => setExportMenuOpen(false)}
                    title="Tracked-changes .docx vs the version selected in compare mode"
                  >
                    Redline vs version…
                  </a>
                ) : (
                  <span
                    className="block cursor-default px-3 py-1.5 text-ink-faint"
                    title="Enter compare mode and pick a version first"
                  >
                    Redline vs version…
                  </span>
                )}
                {(qc?.report ?? qc?.result) && (
                  <>
                    <span className="my-1 block border-t border-edge" />
                    <a
                      className="block px-3 py-1.5 font-medium text-accent hover:bg-surface hover:text-accent-hover"
                      href="/api/qc/export"
                      download
                      onClick={() => setExportMenuOpen(false)}
                      title="Complete human-readable Final QC report with findings, evidence, verification, and disposition history"
                    >
                      Final QC report (DOCX)
                    </a>
                    <a
                      className="block px-3 py-1.5 text-ink-dim hover:bg-surface hover:text-ink"
                      href="/api/qc/export.json"
                      download
                      onClick={() => setExportMenuOpen(false)}
                      title="Complete machine-readable Final QC record"
                    >
                      Final QC record (JSON)
                    </a>
                  </>
                )}
              </div>
            )}
          </div>
          <a
            className={
              actionButton + (busy ? " pointer-events-none opacity-40" : "")
            }
            href={busy ? undefined : "/api/project/save"}
            aria-disabled={busy}
            download
            title="Save the project, including its exact source DOCX when available, as .baspec"
            data-tour="save"
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
            accept=".baspec,.json,application/json,application/zip"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) onLoadProject(file);
              e.target.value = "";
            }}
          />
          <Tip
            tip={
              hasContent
                ? "Import needs a blank document — start a new session first (New session)."
                : busy
                  ? "Finish the current turn first."
                  : "Import supported body content while retaining the exact source package for narrowly scoped, source-preserving export."
            }
          >
            <button
              className={actionButton}
              onClick={() => importRef.current?.click()}
              disabled={busy || hasContent}
              data-tour="import-master"
            >
              Import master
            </button>
          </Tip>
          <input
            ref={importRef}
            type="file"
            accept=".docx"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) setPendingImport(file);
              e.target.value = "";
            }}
          />
        </div>
      </div>

      {importedMode && (
        <div
          className="border-b border-warn/40 bg-warn/10 px-5 py-3 text-[11px] leading-relaxed text-ink-dim"
          role="status"
        >
          <div className="flex flex-wrap items-start justify-between gap-x-5 gap-y-2">
            <div className="min-w-0 flex-1">
              <p className="font-semibold text-warn">
                {passThroughOnly
                  ? "Imported DOCX — pass-through only"
                  : preservationReady
                    ? "Imported DOCX — source-preserving mode"
                    : sourceAvailable
                      ? "Imported DOCX — preservation currently blocked"
                      : "Imported DOCX — normalized-content mode"}
              </p>
              <p className="mt-0.5">
                {sourceAvailable
                  ? passThroughOnly
                    ? "The exact original is retained and remains available byte-for-byte. This package contains features that make any DOCX body mutation unsafe, so body edit and fix controls are disabled. Use normalized export only when you intentionally want a newly generated document."
                    : preservationReady
                    ? importReport?.fidelity_notice ||
                      "Build-a-Spec retained the exact source package. Preserved export patches verified simple body text and permits bounded add, delete, or reorder only in eligible flat body islands with isolated direct Word list bindings. Other structural or complex-format edits are refused."
                    : "The exact source is retained, but the current document state cannot be represented inside the source-preserving boundary. Restore a compatible version or choose normalized export explicitly."
                  : "This source-less legacy project contains only the normalized semantic extraction. Preserved export is unavailable."}
              </p>
              {passThroughOnly && sourcePreservation.blockers.length > 0 && (
                <p className="mt-1 text-ink-faint">
                  Mutation blocked because:{" "}
                  {sourcePreservation.blockers
                    .map((blocker) => blocker.message)
                    .join("; ")}
                  .
                </p>
              )}
              {importReport ? (
                <>
                  <p className="mt-1 text-ink-faint">
                    {importReport.imported_block_count} provisions imported
                    from{" "}
                    <span className="font-medium text-ink-dim">
                      {importReport.filename}
                    </span>
                    ; {importReport.skipped_empty_count} empty body block
                    {importReport.skipped_empty_count === 1 ? "" : "s"}{" "}
                    skipped
                    {importReport.warnings.length
                      ? `; ${importReport.warnings.length} import note${
                          importReport.warnings.length === 1 ? "" : "s"
                        }`
                      : ""}
                    .
                  </p>
                  {importReport.tracked_changes_detected && (
                    <p className="mt-1 text-ink-faint">
                      Tracked changes were detected and resolved to their
                      Accept-All text view during extraction.
                    </p>
                  )}
                </>
              ) : (
                <p className="mt-1 text-ink-faint">
                  This legacy project has no detailed import-fidelity report.
                </p>
              )}
              {(importReport?.warnings.length ?? 0) > 0 && (
                <details className="mt-1.5">
                  <summary className="cursor-pointer text-ink-dim hover:text-ink">
                    Review import notes
                  </summary>
                  <ul className="mt-1 list-disc space-y-0.5 pl-5 text-ink-faint">
                    {importReport?.warnings.map((warning, index) => (
                      <li key={`${index}-${warning}`}>{warning}</li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
            <div className="shrink-0 text-right">
              {sourceAvailable ? (
                <a
                  className="font-medium text-accent hover:text-accent-hover"
                  href="/api/import/original"
                  download
                >
                  Download original upload
                </a>
              ) : (
                <p className="font-medium text-warn">
                  Original upload unavailable in this session
                </p>
              )}
              <p className="mt-0.5 max-w-56 text-ink-faint">
                {sourceAvailable
                  ? "The exact original is carried by native .baspec saves."
                  : "Legacy source-less projects can only use normalized export."}
              </p>
            </div>
          </div>
        </div>
      )}

      <ConfirmDialog
        open={pendingImport !== null}
        danger
        title="Import with a protected source copy?"
        body={
          <div className="space-y-2">
            <p>
              Build-a-Spec will extract supported body text from{" "}
              <b className="text-ink">{pendingImport?.name}</b> into its own
              SectionFormat model and retain an exact, immutable source copy.
            </p>
            <p>
              Preserved export clones that source and can replace text in
              verified simple body paragraphs. Add, delete, and reorder are
              limited to proven flat body islands with isolated direct Word
              list bindings. Headers, footers, numbering definitions, styles,
              section layout, and unrelated package parts remain untouched.
              Unsupported edits are refused instead of flattening the file.
            </p>
            <p className="text-ink-faint">
              A separate normalized export remains available when you
              intentionally want a newly generated document. Native .baspec
              saves carry the exact source copy with the project.
            </p>
          </div>
        }
        confirmLabel="Import DOCX"
        cancelLabel="Cancel"
        onConfirm={() => {
          const file = pendingImport;
          setPendingImport(null);
          if (file) onImportMaster(file);
        }}
        onCancel={() => setPendingImport(null)}
      />

      {compareMode && (
        <div className="flex flex-wrap items-center gap-3 border-b border-edge bg-bg/40 px-5 py-2 text-[11px]">
          <span className="font-medium tracking-wide text-ink-dim uppercase">
            Comparing
          </span>
          <select
            className="rounded border border-edge bg-raised px-2 py-1 text-[11px] text-ink"
            value={compareBase ?? ""}
            onChange={(e) => void loadDiff(Number(e.target.value))}
          >
            {baseOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <span className="text-ink-faint">→ current v{curIndex + 1}</span>
          {diff && (
            <span className="flex items-center gap-2 text-ink-dim tabular-nums">
              <span className="text-ok">+{diff.stats.inserted} added</span>
              <span className="text-err">−{diff.stats.deleted} removed</span>
              <span>{diff.stats.changed} edited</span>
              {diff.status_changes.length > 0 && (
                <span className="text-ink-faint">
                  · {diff.status_changes.length} status
                </span>
              )}
            </span>
          )}
          {diffError && <span className="text-err">{diffError}</span>}
          {!diff && !diffError && <span className="text-ink-faint">Loading…</span>}
        </div>
      )}

      <div className="flex-1 overflow-y-auto p-6">
        {compareMode ? (
          diff && doc ? (
            <SpecDocument doc={doc} changedIds={changedIds} diff={diff} />
          ) : (
            <div className="mx-auto max-w-2xl text-center text-sm text-ink-faint">
              {diffError ?? "Loading comparison…"}
            </div>
          )
        ) : hasContent && doc ? (
          <SpecDocument
            doc={doc}
            changedIds={changedIds}
            sourceLookup={sourceLookup}
            busy={busy}
            sourceExpected={activeSourceExpected}
            sourceCapabilities={sourceCapabilities}
            onEdit={onEditDoc}
          />
        ) : (
          <EmptyState />
        )}
      </div>

      <ReviewDrawer
        doc={doc}
        sourceLookup={sourceLookup}
        busy={busy}
        sourceExpected={activeSourceExpected}
        sourceCapabilities={sourceCapabilities}
        onEditDoc={onEditDoc}
        onAskModel={onAskModel}
        onJump={scrollToElement}
        openNonce={drawerNonces?.review}
      />

      <ResearchDrawer
        doc={doc}
        profileComplete={profileComplete}
        research={research}
        busy={busy}
        onStart={onStartResearch}
        onStop={onStopResearch}
        onEditDoc={onEditDoc}
        openNonce={drawerNonces?.research}
      />

      <QCDrawer
        qc={qc}
        readiness={readiness}
        doc={doc}
        busy={busy}
        sourceExpected={activeSourceExpected}
        sourceCapabilities={sourceCapabilities}
        usage={usage}
        onStart={onStartQc}
        onStop={onStopQc}
        onApply={onApplyQc}
        onDismiss={onDismissQc}
        onJump={scrollToElement}
        openNonce={drawerNonces?.qc}
      />

      <IssuesDrawer issues={lintIssues} onJump={scrollToElement} />

      {openItems.length > 0 && (
        <div
          className="border-t border-edge bg-bg/70 px-5 py-2"
          data-tour="open-items"
        >
          <button
            className="flex w-full items-baseline gap-2 text-left text-[11px] text-ink-faint transition-colors hover:text-ink-dim"
            onClick={() => setOpenItemsExpanded((v) => !v)}
            title="Unresolved provisions — [TBD] markers and needs-input blocks"
          >
            <span className="shrink-0 font-medium tracking-wide uppercase">
              Open items
            </span>
            <span className="truncate">
              {openItems.length} unresolved
            </span>
            <span className="ml-auto shrink-0">
              {openItemsExpanded ? "▾" : "▸"}
            </span>
          </button>
          {openItemsExpanded && (
            <ul className="mt-1.5 max-h-44 space-y-1 overflow-y-auto">
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
          )}
        </div>
      )}

      <StandardsStrip standards={standards} onEditDoc={onEditDoc} busy={busy} />
    </aside>
  );
}
