/**
 * The live document panel: SectionFormat rendering of the server-owned
 * tree, a per-turn version stepper (undo/redo), export / save / open
 * actions, and the open-items list ([TBD] markers + needs-input blocks).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  DraftPrerequisites,
  EditOp,
  FileLoading,
  ImportNotice,
  ImportReport,
  LintIssue,
  OpenItem,
  QcApplyPreviewBasis,
  QcApplyPreviewResult,
  QcSnapshot,
  ReadinessPayload,
  ReferenceDocMeta,
  ResearchSnapshot,
  SectionDiff,
  SectionDiffPayload,
  SourceCapabilitiesState,
  SpecDoc,
  StandardInfo,
  TemplateOrigin,
  UsageSummary,
} from "../types";
import IssuesDrawer, { StandardsStrip } from "./IssuesDrawer";
import QCDrawer from "./QCDrawer";
import ResearchDrawer from "./ResearchDrawer";
import ReviewDrawer from "./ReviewDrawer";
import SpecDocument, { SectionHeader } from "./SpecDocument";
import {
  sourceCapability,
  sourceCapabilitiesExpected,
  sourceCapabilitiesPending,
} from "../lib/sourceCapabilities";
import Tip from "./Tip";

interface Props {
  doc: SpecDoc | null;
  openItems: OpenItem[];
  lintIssues: LintIssue[];
  standards: StandardInfo[];
  profileComplete: boolean;
  /** Full-draft gate (section / project type / country); null until loaded. */
  draftPrerequisites: DraftPrerequisites | null;
  research: ResearchSnapshot | null;
  qc: QcSnapshot | null;
  readiness: ReadinessPayload | null;
  usage: UsageSummary | null;
  changedIds: ReadonlySet<string>;
  baselineIndex: number | null;
  importReport: ImportReport | null;
  sourceAvailable: boolean;
  preservationReady: boolean;
  sourceCapabilities: SourceCapabilitiesState | null;
  templateOrigin: TemplateOrigin | null;
  tutorialActive: boolean;
  busy: boolean;
  /** A master import / project open the server is still working through.
   *  Drives the progress line and keeps both file actions disabled. */
  fileLoading?: FileLoading;
  /** What to say about the last import; null for a clean one. */
  importNotice?: ImportNotice;
  onDismissImportNotice?: () => void;
  onUndo: () => void;
  onRedo: () => void;
  onSaveAsTemplate: () => void;
  onEditDoc: (ops: EditOp[]) => void;
  onLoadProject: (file: File) => void;
  /** Native pywebview Open dialog (Open / Import). Resolves to a File when
   *  the user picked one, null when cancelled, or undefined when there is no
   *  native bridge — the caller then falls back to the hidden file input. */
  nativeOpenFile: (
    kind: "project" | "docx" | "reference",
  ) => Promise<File | null | undefined>;
  onImportMaster: (file: File) => void;
  referenceDocs: ReferenceDocMeta[];
  onAttachReference: (file: File) => void;
  onRemoveReference: (rid: string) => void;
  referenceBusy: boolean;
  onStartResearch: () => void;
  onStopResearch: () => void;
  onStartQc: (acknowledgeScopeMismatch?: boolean) => void;
  onStopQc: () => void;
  onPreviewQc: (findingIds: string[]) => Promise<QcApplyPreviewResult>;
  onApplyQc: (
    findingIds: string[],
    previewBasis?: QcApplyPreviewBasis,
  ) => Promise<void>;
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

/** The paper while a file is being read server-side. Same sheet as the empty
 *  state, so the panel reads as "filling in", with skeleton lines sweeping to
 *  say the work is live rather than stalled. */
function LoadingState({ fileLoading }: { fileLoading: NonNullable<FileLoading> }) {
  const importing = fileLoading.kind === "import";
  return (
    <div
      className="mx-auto max-w-2xl rounded-xl border border-paper-edge bg-paper px-10 py-12 text-paper-ink shadow-[0_2px_16px_rgba(0,0,0,0.25)]"
      role="status"
      aria-live="polite"
    >
      <div className="text-center">
        <p className="text-[13px] font-semibold tracking-wide">
          {importing ? "READING THE MASTER" : "OPENING THE PROJECT"}
        </p>
        <p className="mt-1 truncate text-[13px] font-semibold tracking-wide text-paper-dim">
          {fileLoading.name}
        </p>
      </div>

      <div className="mt-10 space-y-8 select-none" aria-hidden="true">
        {["PART 1 - GENERAL", "PART 2 - PRODUCTS", "PART 3 - EXECUTION"].map(
          (part, partIndex) => (
            <div key={part}>
              <p className="text-[13px] font-semibold">{part}</p>
              <div className="mt-3 space-y-2.5">
                {[11, 9, 10].map((width, lineIndex) => (
                  <div
                    key={width}
                    className="skeleton-line h-2 rounded bg-paper-edge/80"
                    style={{
                      width: `${(width / 12) * 100}%`,
                      animationDelay: `${(partIndex * 3 + lineIndex) * 0.12}s`,
                    }}
                  />
                ))}
              </div>
            </div>
          ),
        )}
      </div>

      <p className="mt-12 text-center text-xs leading-relaxed text-paper-dim">
        {importing
          ? "Extracting supported body content and indexing the exact source package. Every imported block lands stamped imported until it is reviewed."
          : "Restoring the document, its history and any attached source package."}
      </p>
    </div>
  );
}

function EmptyState({
  doc,
  busy,
  sourceExpected,
  sourceCapabilities,
  unstructuredImport,
  onEditDoc,
}: {
  doc: SpecDoc;
  busy: boolean;
  sourceExpected: boolean;
  sourceCapabilities: SourceCapabilitiesState | null;
  unstructuredImport: boolean;
  onEditDoc: (ops: EditOp[]) => void;
}) {
  const sectionReplaceCapability = sourceCapability(
    sourceCapabilities,
    sourceExpected,
    "sec",
    "replace_text",
  );

  return (
    <div className="mx-auto max-w-2xl rounded-xl border border-paper-edge bg-paper px-10 py-12 text-paper-ink shadow-[0_2px_16px_rgba(0,0,0,0.25)]">
      {/* A blank document renders here, not in SpecDocument — so the header
          editor has to live in both places, or naming the section would be
          impossible on the one page where you most want to do it. An
          unstructured import is the exception: it has no section number and
          inventing one is what the honest-framing rule exists to prevent. */}
      {unstructuredImport ? (
        <div id="el-sec" className="text-center">
          <p className="text-[13px] font-semibold tracking-wide">SECTION</p>
          <p className="mt-1 text-[13px] font-semibold tracking-wide text-paper-dim">
            — awaiting the interview —
          </p>
        </div>
      ) : (
        <div
          id="el-sec"
          data-capability="document.section-header"
          className="text-center"
        >
          <SectionHeader
            number={doc.section.number}
            title={doc.section.title}
            changed={false}
            capability={sectionReplaceCapability}
            sourceExpected={sourceExpected}
            busy={busy}
            onEdit={(ops) => {
              onEditDoc(ops);
              return true;
            }}
          />
          <p className="mt-1 text-[11px] text-paper-dim">
            Name it here, or just tell the assistant — either records the same
            change.
          </p>
        </div>
      )}

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
        Use the interview and Draft full section to build the document, or
        import an office master to start from. Changes appear in place and every
        [TBD] stays tracked.
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
  draftPrerequisites,
  research,
  qc,
  readiness,
  usage,
  changedIds,
  baselineIndex,
  importReport,
  sourceAvailable,
  preservationReady,
  sourceCapabilities,
  templateOrigin,
  tutorialActive,
  busy,
  fileLoading = null,
  importNotice = null,
  onDismissImportNotice,
  onUndo,
  onRedo,
  onSaveAsTemplate,
  onEditDoc,
  onLoadProject,
  nativeOpenFile,
  onImportMaster,
  referenceDocs,
  onAttachReference,
  onRemoveReference,
  referenceBusy,
  onStartResearch,
  onStopResearch,
  onStartQc,
  onStopQc,
  onPreviewQc,
  onApplyQc,
  onDismissQc,
  onDraftFull,
  onAskModel,
  onFetchDiff,
  drawerNonces,
}: Props) {
  const fileRef = useRef<HTMLInputElement>(null);
  const importRef = useRef<HTMLInputElement>(null);
  const referenceRef = useRef<HTMLInputElement>(null);
  // Open / Import: prefer the native pywebview dialog (the HTML file input
  // silently yields no bytes inside the webview); fall back to the hidden
  // <input type="file"> in a plain browser (undefined = no native bridge).
  // Import goes straight to onImportMaster — the confirmation modal was
  // removed on master, so both paths import directly.
  const handleOpenClick = async () => {
    if (fileLoading) return;
    const file = await nativeOpenFile("project");
    if (file === undefined) fileRef.current?.click();
    else if (file) onLoadProject(file);
  };
  const handleImportClick = async () => {
    if (fileLoading) return;
    const file = await nativeOpenFile("docx");
    if (file === undefined) importRef.current?.click();
    else if (file) onImportMaster(file);
  };
  // Attaching reference material takes the same native-first path, but has no
  // blank-document precondition: it never touches the spec. Its own dialog
  // kind, because an attachment is not Word-only — the "docx" filter would
  // hide every PDF/text/XML/CSV in the packaged app's picker.
  const handleAttachClick = async () => {
    if (referenceBusy) return;
    const file = await nativeOpenFile("reference");
    if (file === undefined) referenceRef.current?.click();
    else if (file) onAttachReference(file);
  };
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
  // Only an explicit false suppresses the spec chrome: projects saved before
  // shape detection omit the field and keep their original presentation.
  const unstructuredImport = importReport?.spec_shape_detected === false;
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
  // Denied because the answer is not derived yet, as opposed to denied
  // because the package forbids it. Same restriction, different sentence.
  const capabilitiesPending =
    activeSourceExpected && sourceCapabilitiesPending(sourceCapabilities);
  // The server's own sentence for the pending denial, so the strip below, the
  // draft tooltip, and every per-row control in SpecDocument all say the same
  // thing (lib/sourceCapabilities.ts: never add client prose to a denial).
  const pendingReason =
    sourceCapabilities?.elements.sec?.replace_text?.message ??
    "Imported-source permissions for this document state are still being checked.";

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
  // A whole-section draft anchors on the section, the project type, and the
  // country, so the click collects whatever is missing before it drafts. The
  // report is the server's — never recomputed here, or the tooltip could
  // promise a draft the endpoint is about to turn into questions.
  const draftNeeds =
    draftPrerequisites?.ready === false
      ? draftPrerequisites.requirements
        .filter((r) => !r.satisfied)
        .map((r) => r.label)
      : [];
  const draftNeedsPhrase =
    draftNeeds.length > 1
      ? `${draftNeeds.slice(0, -1).join(", ")} and ${
        draftNeeds[draftNeeds.length - 1]
      }`
      : (draftNeeds[0] ?? "");
  const draftNeedsPronoun = draftNeeds.length === 1 ? "it" : "them";
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
        : draftNeeds.length > 0
          ? `Needs ${draftNeedsPhrase} first — every provision a full draft lays down inherits ${draftNeedsPronoun}. Clicking asks you about ${draftNeedsPronoun}; then draft.`
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
      data-capability="document.structure"
    >
      <div className="flex items-center justify-between gap-3 border-b border-edge px-5 py-2.5">
        <div className="flex min-w-0 items-center gap-2.5">
          {lintIssues.length > 0 && (
            <span
              className="rounded-full border border-warn/50 bg-warn/15 px-1.5 py-px text-[10px] font-semibold text-warn normal-case"
              title="Advisory lint issues — see the Issues drawer below"
            >
              ⚠ {lintIssues.length}
            </span>
          )}
          <Tip tip={draftTip} className="shrink-0">
            <button
              className={`rounded-md bg-accent px-2.5 py-1 text-[11px] font-medium text-white transition-colors hover:bg-accent-hover disabled:pointer-events-none disabled:opacity-40 ${
                draftPulse ? "draft-pulse" : ""
              }`}
              onClick={onDraftFull}
              disabled={draftDisabled}
              data-tour="draft-full"
              data-capability="chat.full-draft"
            >
              ✨ Draft full section
            </button>
          </Tip>
        </div>
        <div className="flex items-center gap-1.5">
          <span
            className="flex items-center gap-1.5"
            data-tour="version-stepper"
            data-capability="history.undo-redo"
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
              data-capability="history.compare"
            >
              {compareMode ? "Exit compare" : "Compare"}
            </button>
          </Tip>
          <span className="mx-1 h-4 w-px bg-edge" />
          {/* Export menu (Batch 5): generated DOCX, or tracked changes over
              the normalized provision tree / a chosen version. Downloads are disabled
              while a turn streams — mid-turn the live doc holds provisional
              edits and only committed versions are downloadable. */}
          <div
            className="relative"
            data-tour="export"
            data-capability="export.clean"
          >
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
                data-capability="export.redline-source"
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
                        title="Clone the original DOCX and apply only verified body edits, including bounded structural edits in eligible isolated Word-list islands"
                      >
                        Export preserved DOCX
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
                    {sourceAvailable ? (
                      <a
                        className="block px-3 py-1.5 text-ink-dim hover:bg-surface hover:text-ink"
                        href="/api/import/original"
                        download
                        onClick={() => setExportMenuOpen(false)}
                        title="Download the exact DOCX package that was imported, unchanged"
                        data-capability="import.source-output"
                      >
                        Download exact original DOCX
                      </a>
                    ) : (
                      <span
                        className="block cursor-default px-3 py-1.5 text-ink-faint"
                        title="This imported project does not contain a recoverable exact source package"
                      >
                        Exact original DOCX unavailable
                      </span>
                    )}
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
                {/* The Final QC report downloads deliberately do NOT appear
                    here: they live only in the Final QC surfaces (QCDrawer +
                    QCReportModal), beside the run identity they are pinned
                    to. This menu exports the SPECIFICATION. */}
              </div>
            )}
          </div>
          <a
            className={
              actionButton + (busy ? " pointer-events-none opacity-40" : "")
            }
            href={
              busy
                ? undefined
                : tutorialActive
                  ? "/api/project/save?scope=tutorial"
                  : "/api/project/save"
            }
            aria-disabled={busy}
            download
            title="Save the project, including its exact source DOCX when available, as .baspec"
            data-tour="save"
            data-capability="project.save-open"
          >
            Save
          </a>
          <button
            className={actionButton}
            onClick={onSaveAsTemplate}
            disabled={busy || !hasContent}
            title={
              hasContent
                ? "Open the template studio to turn this spec into a reusable starter"
                : "Add spec content before creating a template"
            }
            data-tour="save-template"
            data-capability="template.create"
          >
            Save as Template
          </button>
          <button
            className={actionButton}
            onClick={handleOpenClick}
            data-capability="project.save-open"
            disabled={busy || !!fileLoading || tutorialActive}
            title={
              tutorialActive
                ? "This chapter already uses a real temporary .baspec round trip; return to your project before opening another file."
                : "Open a saved project file"
            }
          >
            {fileLoading?.kind === "open" ? "Opening…" : "Open"}
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
              fileLoading
                ? "Reading the file — this can take a few seconds for a long master."
                : hasContent
                  ? "Import needs a blank document — start a new session first (New session)."
                  : busy
                    ? "Finish the current turn first."
                    : "Import supported body content while retaining the exact source package for narrowly scoped, source-preserving export."
            }
          >
            <button
              className={actionButton}
              onClick={handleImportClick}
              disabled={busy || hasContent || !!fileLoading}
              data-tour="import-master"
              data-capability="import.master"
            >
              {fileLoading?.kind === "import" ? "Importing…" : "Import Spec"}
            </button>
          </Tip>
          <input
            ref={importRef}
            type="file"
            accept=".docx"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) onImportMaster(file);
              e.target.value = "";
            }}
          />
          <Tip tip="Attach a .docx, .pdf, .txt, .xml, or .csv as background for the assistant to read — an owner's design standard, a basis-of-design narrative, a data sheet, an equipment schedule. It is never added to the spec and never edited.">
            <button
              className={actionButton}
              onClick={handleAttachClick}
              disabled={referenceBusy}
              data-tour="attach-reference"
              data-capability="reference.attach"
            >
              {referenceBusy ? "Attaching…" : "Attach Document"}
            </button>
          </Tip>
          <input
            ref={referenceRef}
            type="file"
            accept=".docx,.pdf,.txt,.xml,.csv"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) onAttachReference(file);
              e.target.value = "";
            }}
          />
        </div>
      </div>

      {fileLoading && (
        <div
          className="flex items-start gap-3 border-b border-accent/30 bg-accent/[0.06] px-5 py-3"
          role="status"
          aria-live="polite"
          data-testid="file-loading"
        >
          <span className="status-dots mt-1.5" aria-hidden="true">
            <span />
            <span />
            <span />
          </span>
          <div className="min-w-0">
            <p className="truncate text-xs font-semibold">
              <span className="status-shimmer">
                {fileLoading.kind === "import" ? "Importing" : "Opening"}{" "}
                {fileLoading.name}
              </span>
            </p>
            <p className="mt-0.5 text-[11px] leading-relaxed text-ink-dim">
              Extracting body content and indexing the source package. A long
              master can take several seconds; the rest of the app keeps
              working while this finishes.
            </p>
          </div>
        </div>
      )}

      {/* Whatever the last import has to say — a failure, or the content it
          could not carry across — reported beside the buttons that started
          it. Both used to go into the chat, which put machine-written notices
          in the middle of the conversation. A clean import shows nothing. */}
      {importNotice && (
        <div
          className={
            "flex items-start justify-between gap-3 border-b px-5 py-3 " +
            (importNotice.tone === "error"
              ? "border-err/40 bg-err/10"
              : "border-warn/40 bg-warn/10")
          }
          role={importNotice.tone === "error" ? "alert" : "status"}
          data-testid="import-notice"
        >
          <details className="min-w-0" open={importNotice.tone === "error"}>
            <summary
              className={
                "cursor-pointer truncate text-xs font-semibold " +
                (importNotice.tone === "error" ? "text-err" : "text-warn")
              }
            >
              {importNotice.title ??
                (importNotice.tone === "error"
                  ? `Import failed — ${importNotice.name}`
                  : `${importNotice.lines.length} import note${
                      importNotice.lines.length === 1 ? "" : "s"
                    } — content the extraction could not carry across`)}
            </summary>
            <ul className="mt-1.5 list-disc space-y-0.5 pl-5 text-[11px] leading-relaxed text-ink-dim">
              {importNotice.lines.map((line, index) => (
                <li key={`${index}-${line}`}>{line}</li>
              ))}
            </ul>
          </details>
          <button
            className="shrink-0 text-[11px] text-ink-faint hover:text-ink"
            onClick={onDismissImportNotice}
            title="Dismiss"
          >
            ✕
          </button>
        </div>
      )}

      {/* The per-element permission sweep runs in the background after an
          import or a body change; body edits stay disabled until it lands
          (fail-closed), so say that it is working rather than leaving every
          control inertly greyed out. */}
      {capabilitiesPending && (
        <div
          className="flex items-center gap-2 border-b border-edge bg-bg/40 px-5 py-2 text-[11px] text-ink-dim"
          role="status"
          aria-live="polite"
        >
          <span className="status-dots" aria-hidden="true">
            <span />
            <span />
            <span />
          </span>
          <span>{pendingReason}</span>
        </div>
      )}

      {compareMode && (
        <div
          className="flex flex-wrap items-center gap-3 border-b border-edge bg-bg/40 px-5 py-2 text-[11px]"
          data-capability="history.compare"
        >
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
            templateOrigin={templateOrigin}
            onEdit={onEditDoc}
            unstructuredImport={unstructuredImport}
          />
        ) : fileLoading ? (
          <LoadingState fileLoading={fileLoading} />
        ) : doc ? (
          <EmptyState
            doc={doc}
            busy={busy}
            sourceExpected={activeSourceExpected}
            sourceCapabilities={sourceCapabilities}
            unstructuredImport={unstructuredImport}
            onEditDoc={onEditDoc}
          />
        ) : (
          <div className="mx-auto max-w-2xl text-center text-sm text-ink-faint">
            Loading document…
          </div>
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
        profileComplete={profileComplete}
        busy={busy}
        sourceExpected={activeSourceExpected}
        sourceCapabilities={sourceCapabilities}
        usage={usage}
        onStart={onStartQc}
        onStop={onStopQc}
        onPreview={onPreviewQc}
        onApply={onApplyQc}
        onDismiss={onDismissQc}
        onAskModel={onAskModel}
        onJump={scrollToElement}
        openNonce={drawerNonces?.qc}
      />

      <IssuesDrawer issues={lintIssues} onJump={scrollToElement} />

      {openItems.length > 0 && (
        <div
          className="border-t border-edge bg-bg/70 px-5 py-2"
          data-tour="open-items"
          data-capability="document.open-items"
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
      <ReferenceDocumentsStrip
        documents={referenceDocs}
        busy={referenceBusy}
        onRemove={onRemoveReference}
      />
    </aside>
  );
}

/** Cumulative cap on attached reference documents — keep in sync with
 * MAX_REFERENCE_TOKENS in backend/reference_docs.py (the enforcing side). */
const MAX_REFERENCE_TOKENS = 100_000;

function ReferenceDocumentsStrip({
  documents,
  busy,
  onRemove,
}: {
  documents: ReferenceDocMeta[];
  busy: boolean;
  onRemove: (rid: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  if (documents.length === 0) return null;
  const totalTokens = documents.reduce((sum, doc) => sum + doc.token_count, 0);
  return (
    <div
      className="border-t border-line bg-surface-2/40 px-5 py-2.5"
      data-capability="reference.use"
    >
      <button
        className="flex w-full items-center gap-2 text-left text-[11px] text-ink-faint hover:text-ink-dim"
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
      >
        <span className="font-medium tracking-wide uppercase">Documents</span>
        <span>{documents.length}</span>
        <span className="ml-auto tabular-nums">
          {totalTokens.toLocaleString()} / {MAX_REFERENCE_TOKENS.toLocaleString()}{" "}
          tokens
        </span>
        <span>{expanded ? "▾" : "▸"}</span>
      </button>
      {expanded && (
        <ul className="mt-2 max-h-44 space-y-1.5 overflow-y-auto">
          {documents.map((doc) => (
            <li
              key={doc.rid}
              className="flex items-start justify-between gap-3 text-[11px] leading-relaxed"
            >
              <div className="min-w-0">
                <p className="truncate font-medium text-ink-dim">{doc.title}</p>
                <p className="text-ink-faint">
                  {doc.kind_label} · {doc.token_count.toLocaleString()} tokens
                  {doc.truncated && (
                    <span className="text-warn"> · truncated</span>
                  )}
                  {doc.tracked_changes && <span> · Accept-All view</span>}
                </p>
              </div>
              <button
                className="shrink-0 text-ink-faint hover:text-danger"
                onClick={() => onRemove(doc.rid)}
                disabled={busy}
                title={`Remove ${doc.title}`}
                aria-label={`Remove ${doc.title}`}
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
