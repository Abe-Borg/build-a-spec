import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  ProjectBriefInspection,
  ProjectBriefManifest,
  TemplatePreviewResult,
  TemplateSummary,
} from "../types";
import {
  commitTemplate,
  deleteTemplate,
  importTemplate,
  inspectProjectBrief,
  listTemplates,
  previewTemplate,
  templateExportUrl,
  updateTemplate,
} from "../lib/api";
import { ModalShell, primaryBtn, quietBtn } from "./ModalShell";

interface Props {
  open: boolean;
  busy: boolean;
  hasContent: boolean;
  templatesOnly?: boolean;
  onCancel: () => void;
  onBlankSlate: () => void;
  onStartTemplate: (templateId: string) => void;
  openTemplateFile: () => Promise<File | null | undefined>;
  /** Seed the next section of a project from its brief (or a sibling
   *  section's .baspec), optionally paired with a template. */
  onStartBrief: (
    file: File,
    opts: { discipline?: string; templateId?: string },
  ) => void;
  openBriefFile: () => Promise<File | null | undefined>;
}

type View = "start" | "browse" | "create" | "preview" | "manage" | "brief";

const fieldClass =
  "w-full rounded-lg border border-edge bg-raised px-3 py-2 text-sm text-ink placeholder:text-ink-faint focus:border-accent focus:outline-none";

/** What a brief holds, before anything is replaced. One row per asset the
 *  seed installs, so a user can see a missing research profile or a dropped
 *  reference document BEFORE starting rather than in a chat note after. */
function BriefManifestCard({ manifest }: { manifest: ProjectBriefManifest }) {
  const research = manifest.research;
  const rows: [string, string][] = [
    ["Project", manifest.name || "Untitled project"],
    [
      "Profile",
      manifest.profile.line
        ? `${manifest.profile.line}${manifest.profile.complete ? "" : " (incomplete)"}`
        : "not recorded",
    ],
    ["Project type", manifest.project_type || "not recorded"],
    [
      "Module",
      `${manifest.module_id || "default"}${
        manifest.module_available ? "" : " (not installed — the default module will be used)"
      }`,
    ],
    [
      "Editions",
      manifest.edition_overrides.count
        ? manifest.edition_overrides.standards.join("; ")
        : "none recorded",
    ],
    [
      "Research",
      research
        ? `${research.items} finding${research.items === 1 ? "" : "s"} (${research.grounded} grounded) over ${research.rounds} round${
            research.rounds === 1 ? "" : "s"
          }; ${research.dimensions_completed} of ${
            research.dimensions_declared ?? research.dimensions_recorded
          } areas complete; last researched ${research.last_research_date || "—"}`
        : "none — the new section starts unresearched",
    ],
    [
      "References",
      manifest.references.length
        ? manifest.references
            .map(
              (doc) =>
                `${doc.title} (${doc.kind}, ${doc.token_count.toLocaleString()} tokens${
                  doc.carried ? "" : " — not carried"
                })`,
            )
            .join("; ")
        : "none",
    ],
    [
      "Facts",
      `${manifest.facts.active} active (${manifest.facts.confirmed} confirmed, ${manifest.facts.assumed} assumed)${
        manifest.facts.superseded ? `, ${manifest.facts.superseded} retired` : ""
      }`,
    ],
    [
      "Sections so far",
      manifest.sections.length
        ? manifest.sections.map((s) => `${s.number} ${s.title}`.trim()).join("; ")
        : "none recorded",
    ],
  ];
  return (
    <div className="mt-3 rounded-lg border border-edge bg-raised/50 p-3">
      <dl className="grid gap-x-3 gap-y-1 text-xs sm:grid-cols-[8rem_1fr]">
        {rows.map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="text-ink-faint">{label}</dt>
            <dd className="min-w-0 break-words text-ink-dim">{value}</dd>
          </div>
        ))}
      </dl>
      {manifest.warnings.length > 0 && (
        <ul className="mt-2 space-y-1 border-t border-edge/60 pt-2 text-[11px] text-warn">
          {manifest.warnings.map((warning) => (
            <li key={warning}>⚠ {warning}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function NewSessionDialog({
  open,
  busy,
  hasContent,
  templatesOnly = false,
  onCancel,
  onBlankSlate,
  onStartTemplate,
  openTemplateFile,
  onStartBrief,
  openBriefFile,
}: Props) {
  const [view, setView] = useState<View>(templatesOnly ? "browse" : "start");
  // A project brief read for the New-section flow: the file, what the server
  // found in it, and the discipline the new section will use. Kept while the
  // user browses templates to pair with it; cleared on Escape / Cancel.
  const [brief, setBrief] = useState<{
    file: File;
    inspection: ProjectBriefInspection;
  } | null>(null);
  const [briefDiscipline, setBriefDiscipline] = useState("");
  const briefRef = useRef<HTMLInputElement>(null);
  const [templates, setTemplates] = useState<TemplateSummary[]>([]);
  const [templateFilter, setTemplateFilter] = useState("");
  const [invalidCount, setInvalidCount] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<TemplateSummary | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [mode, setMode] = useState<"exact" | "ai_generalize">("exact");
  const [preview, setPreview] = useState<TemplatePreviewResult | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const importRef = useRef<HTMLInputElement>(null);
  const previewRunRef = useRef(0);
  const filteredTemplates = useMemo(() => {
    const query = templateFilter.trim().toLocaleLowerCase();
    if (!query) return templates;
    return templates.filter((template) =>
      [
        template.name,
        template.description,
        template.discipline,
        template.project_type,
        template.section_number,
        template.section_title,
      ]
        .join(" ")
        .toLocaleLowerCase()
        .includes(query),
    );
  }, [templateFilter, templates]);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await listTemplates();
      setTemplates(result.templates);
      setInvalidCount(result.invalidPersonalCount);
      setSelected((current) =>
        current ? result.templates.find((item) => item.id === current.id) ?? null : null,
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!open) {
      previewRunRef.current += 1;
      return;
    }
    setView(templatesOnly ? "browse" : "start");
    setError(null);
    setPreview(null);
    setSelected(null);
    setTemplateFilter("");
    setConfirmDelete(false);
    setBrief(null);
    setBriefDiscipline("");
    void refresh();
  }, [open, templatesOnly, refresh]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        previewRunRef.current += 1;
        if (view !== (templatesOnly ? "browse" : "start")) {
          setView(templatesOnly ? "browse" : "start");
          setSelected(null);
          setConfirmDelete(false);
          setBrief(null);
        } else onCancel();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onCancel, templatesOnly, view]);

  if (!open) return null;

  const run = async (task: () => Promise<void>) => {
    setLoading(true);
    setError(null);
    try {
      await task();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  };

  const beginCreate = () => {
    previewRunRef.current += 1;
    setName("");
    setDescription("");
    setMode("exact");
    setPreview(null);
    setView("create");
  };

  const importFile = (file: File) =>
    run(async () => {
      const created = await importTemplate(file);
      await refresh();
      setSelected(created);
      setView("manage");
    });

  const chooseImportFile = async () => {
    const file = await openTemplateFile();
    if (file === undefined) importRef.current?.click();
    else if (file) void importFile(file);
  };

  // Read the brief without touching the session, then show what it holds
  // before anything is replaced — the file is the only thing kept.
  const inspectBrief = (file: File) =>
    run(async () => {
      const inspection = await inspectProjectBrief(file);
      setBrief({ file, inspection });
      setBriefDiscipline(inspection.manifest.discipline ?? "");
      setView("brief");
    });

  const chooseBriefFile = async () => {
    const file = await openBriefFile();
    if (file === undefined) briefRef.current?.click();
    else if (file) void inspectBrief(file);
  };

  const startBrief = (templateId?: string) => {
    if (!brief) return;
    onStartBrief(brief.file, {
      discipline: briefDiscipline.trim() || undefined,
      templateId,
    });
  };

  const title =
    view === "start"
      ? "Start a new session"
      : view === "brief"
        ? "New section in an existing project"
      : view === "create"
        ? "Create a reusable template"
        : view === "preview"
          ? "Preview the reusable starter"
          : view === "manage"
            ? "Manage template"
            : templatesOnly
              ? "Template studio"
              : "Start from a template";

  return (
    <ModalShell
      title={title}
      onClose={() => {
        previewRunRef.current += 1;
        onCancel();
      }}
      wide
    >
      <div data-tour="template-dialog" data-capability="template.manage">
        {view === "start" && (
          <>
            <p className="text-sm leading-relaxed text-ink-dim">
              Choose a blank page or create an independent working spec from a reusable starter.
            </p>
            <div className="mt-4 grid gap-2">
              <button
                type="button"
                onClick={onBlankSlate}
                disabled={busy || loading}
                className={primaryBtn + " w-full py-2.5 text-left"}
              >
                Start with a blank slate
              </button>
              <button
                type="button"
                onClick={() => setView("browse")}
                disabled={busy || loading}
                className={quietBtn + " w-full py-2.5 text-left"}
                data-capability="template.start"
              >
                Start from a built-in or personal template
              </button>
              <button
                type="button"
                onClick={() => void chooseImportFile()}
                disabled={busy || loading}
                className={quietBtn + " w-full py-2.5 text-left"}
                data-capability="template.import"
              >
                Load your own template file
              </button>
              <button
                type="button"
                onClick={() => void chooseBriefFile()}
                disabled={busy || loading}
                className={quietBtn + " w-full py-2.5 text-left"}
                data-capability="project.brief-start"
                title="Start the next section of a project you have already drafted a section for: its profile, edition overrides, research, attached references and recorded facts come with you — the conversation and the document do not"
              >
                New section in an existing project
                <span className="block text-[11px] font-normal text-ink-faint">
                  From a project brief (.basproject) or a finished section's .baspec file
                </span>
              </button>
            </div>
          </>
        )}

        {view === "brief" && brief && (
          <>
            <p className="text-sm leading-relaxed text-ink-dim">
              {brief.inspection.source === "project"
                ? "Read from that section's project file. "
                : "Read from the project brief. "}
              This is what the next section starts with. The conversation, the
              document, figures, the Final QC report and any imported Word source
              stay with the section that made them; excluded standards are a
              per-section decision and do not carry either.
            </p>
            <BriefManifestCard manifest={brief.inspection.manifest} />
            <label className="mt-3 block text-xs text-ink-dim">
              Discipline for the new section
              <input
                value={briefDiscipline}
                onChange={(event) => setBriefDiscipline(event.target.value)}
                placeholder="Fire Suppression"
                className={fieldClass + " mt-1"}
                aria-label="Discipline for the new section"
              />
            </label>
            <div className="mt-4 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => startBrief()}
                disabled={busy || loading}
                className={primaryBtn}
              >
                Start with a blank page
              </button>
              <button
                type="button"
                onClick={() => setView("browse")}
                disabled={busy || loading}
                className={quietBtn}
                title="The template supplies the document body; the brief supplies the project's profile, editions, research, references and facts"
              >
                Start from a template…
              </button>
              <button
                type="button"
                onClick={() => {
                  setBrief(null);
                  setView("start");
                }}
                className={quietBtn}
              >
                ‹ Start choices
              </button>
            </div>
          </>
        )}

        {view === "browse" && (
          <>
            {brief && (
              <p className="mb-3 rounded-lg border border-accent/40 bg-accent/10 px-3 py-2 text-xs text-ink-dim">
                Pairing with project brief{" "}
                <b className="text-ink">{brief.inspection.manifest.name}</b>: the
                template you start supplies the document body; the brief supplies the
                project's profile, editions, research, references and facts.{" "}
                <button
                  type="button"
                  className="underline underline-offset-2 hover:text-accent"
                  onClick={() => setView("brief")}
                >
                  Back to the brief
                </button>
              </p>
            )}
            <div className="flex flex-wrap items-center gap-2">
              <p className="min-w-0 flex-1 text-sm text-ink-dim">
                Templates seed a new, independent specification. Your template stays reusable.
              </p>
              {hasContent && (
                <button
                  type="button"
                  onClick={beginCreate}
                  className={primaryBtn}
                  data-capability="template.create"
                >
                  Create from current spec
                </button>
              )}
              <button type="button" onClick={() => void chooseImportFile()} className={quietBtn}>
                Import…
              </button>
            </div>
            {invalidCount > 0 && (
              <p className="mt-3 rounded-lg border border-warn/40 bg-warn/10 px-3 py-2 text-xs text-warn">
                {invalidCount} personal template{invalidCount === 1 ? "" : "s"} could not be loaded.
                The files were left untouched.
              </p>
            )}
            <label className="mt-3 block text-xs text-ink-dim">
              Find a template
              <input
                type="search"
                value={templateFilter}
                onChange={(event) => setTemplateFilter(event.target.value)}
                placeholder="Name, discipline, project type, or section"
                className={fieldClass + " mt-1"}
                aria-label="Filter templates"
              />
            </label>
            <div className="mt-4 max-h-[52vh] space-y-2 overflow-y-auto pr-1">
              {loading && templates.length === 0 ? (
                <p className="py-8 text-center text-sm text-ink-faint">Loading templates…</p>
              ) : templates.length === 0 ? (
                <p className="rounded-lg border border-dashed border-edge p-5 text-center text-sm text-ink-faint">
                  No reusable starters yet. Create one from the current spec or import one.
                </p>
              ) : filteredTemplates.length === 0 ? (
                <p className="rounded-lg border border-dashed border-edge p-5 text-center text-sm text-ink-faint">
                  No templates match “{templateFilter.trim()}”. Try a name, discipline, project
                  type, or section.
                </p>
              ) : (
                filteredTemplates.map((template) => (
                  <article
                    key={template.id}
                    className="rounded-lg border border-edge bg-raised/50 p-3"
                    data-template-id={template.id}
                  >
                    <div className="flex items-start gap-3">
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <h3 className="font-medium text-ink">{template.name}</h3>
                          <span className="rounded-full border border-edge px-1.5 py-0.5 text-[9px] uppercase tracking-wide text-ink-faint">
                            {template.source}
                          </span>
                          {!template.module_available && (
                            <span className="rounded-full border border-warn/50 px-1.5 py-0.5 text-[9px] text-warn">
                              module unavailable — starts with generic fallback
                            </span>
                          )}
                        </div>
                        {template.description && (
                          <p className="mt-1 text-xs leading-relaxed text-ink-dim">{template.description}</p>
                        )}
                        <p className="mt-1 text-[11px] text-ink-faint">
                          {[template.discipline, template.section_number, template.section_title]
                            .filter(Boolean)
                            .join(" · ") || "General specification starter"}
                          {` · ${template.article_count} articles · ${template.paragraph_count} provisions`}
                        </p>
                        {template.preview && (
                          <p className="mt-2 line-clamp-2 text-[11px] leading-relaxed text-ink-faint">
                            {template.preview}
                          </p>
                        )}
                      </div>
                      <div className="flex shrink-0 flex-col gap-1.5">
                        <button
                          type="button"
                          onClick={() =>
                            brief ? startBrief(template.id) : onStartTemplate(template.id)
                          }
                          disabled={busy || loading}
                          className={primaryBtn}
                        >
                          {brief ? "Start with the brief" : "Start"}
                        </button>
                        {template.editable && (
                          <button
                            type="button"
                            onClick={() => {
                              setSelected(template);
                              setName(template.name);
                              setDescription(template.description);
                              setConfirmDelete(false);
                              setView("manage");
                            }}
                            className={quietBtn}
                          >
                            Manage
                          </button>
                        )}
                      </div>
                    </div>
                  </article>
                ))
              )}
            </div>
          </>
        )}

        {view === "create" && (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              if (!name.trim()) return;
              const previewRun = ++previewRunRef.current;
              void run(async () => {
                const result = await previewTemplate({
                  name: name.trim(),
                  description: description.trim(),
                  mode,
                });
                if (previewRunRef.current !== previewRun) return;
                setPreview(result);
                setView("preview");
              });
            }}
          >
            <p className="text-sm leading-relaxed text-ink-dim">
              Preview first. Nothing becomes reusable until you approve the server-produced starter.
            </p>
            <label className="mt-4 block text-xs text-ink-dim">
              Name
              <input
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="Office fire-suppression starter"
                className={fieldClass + " mt-1"}
                autoFocus
              />
            </label>
            <label className="mt-3 block text-xs text-ink-dim">
              Description
              <textarea
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                placeholder="When this starter is useful"
                rows={3}
                className={fieldClass + " mt-1 resize-none"}
              />
            </label>
            <fieldset className="mt-3">
              <legend className="text-xs text-ink-dim">Template content</legend>
              <label className="mt-1 flex gap-2 rounded-lg border border-edge p-2.5 text-xs text-ink-dim">
                <input
                  type="radio"
                  checked={mode === "exact"}
                  onChange={() => setMode("exact")}
                />
                <span><b className="text-ink">Exact starter</b> — preserve the body wording as shown, including any client, site, or project wording that is still present.</span>
              </label>
              <label className="mt-1 flex gap-2 rounded-lg border border-edge p-2.5 text-xs text-ink-dim">
                <input
                  type="radio"
                  checked={mode === "ai_generalize"}
                  onChange={() => setMode("ai_generalize")}
                />
                <span><b className="text-ink">AI-generalized</b> — remove project-specific details and preview the proposed starter. This live operation uses your API key, network access, and billed model usage.</span>
              </label>
            </fieldset>
            <div className="mt-4 flex gap-2">
              <button type="submit" disabled={!name.trim() || loading} className={primaryBtn}>
                {loading ? "Building preview…" : "Preview template"}
              </button>
              <button
                type="button"
                onClick={() => {
                  previewRunRef.current += 1;
                  setView("browse");
                }}
                className={quietBtn}
              >
                Cancel
              </button>
            </div>
          </form>
        )}

        {view === "preview" && preview && (
          <>
            {preview.status === "running" ? (
              <div className="rounded-lg border border-edge bg-raised p-4">
                <p className="text-sm text-ink">Generalizing the starter…</p>
                <p className="mt-1 text-xs text-ink-faint">
                  The source spec is unchanged. Close this dialog safely if you do not want to wait.
                </p>
              </div>
            ) : preview.status === "failed" || preview.error ? (
              <p className="rounded-lg border border-err/40 bg-err/10 p-3 text-sm text-err">
                {preview.error || "The generalized preview failed."}
              </p>
            ) : (
              <>
                <div className="max-h-[46vh] overflow-y-auto rounded-lg border border-paper-edge bg-paper p-5 text-paper-ink">
                  <p className="text-center text-xs tracking-wide">
                    SECTION {preview.document.section.number}
                  </p>
                  <h3 className="mt-1 text-center font-semibold uppercase">
                    {preview.document.section.title || preview.template.name}
                  </h3>
                  {preview.document.parts.map((part) => (
                    <div key={part.id} className="mt-4">
                      <p className="text-xs font-semibold">PART {part.number} - {part.title}</p>
                      {part.articles.slice(0, 3).map((article) => (
                        <div key={article.id} className="mt-2 text-xs">
                          <b>{article.number} {article.title}</b>
                          {article.paragraphs[0] && (
                            <p className="mt-0.5 text-[11px] opacity-75">{article.paragraphs[0].text}</p>
                          )}
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
                <p className="mt-2 text-[11px] text-ink-faint">
                  {preview.template.article_count} articles · {preview.template.paragraph_count} provisions
                </p>
                {preview.diff && (
                  <section
                    className="mt-3 rounded-lg border border-edge bg-raised/60 p-3"
                    aria-label="Changes made while generalizing the template"
                  >
                    <h4 className="text-xs font-medium text-ink">
                      What AI generalization changed
                    </h4>
                    <p className="mt-1 text-[11px] text-ink-faint">
                      {preview.diff.stats.changed} changed · {preview.diff.stats.inserted} inserted · {preview.diff.stats.deleted} removed
                    </p>
                    <ul className="mt-2 max-h-28 space-y-1 overflow-y-auto text-[11px] text-ink-dim">
                      {preview.diff.elements
                        .filter((element) => element.kind !== "unchanged")
                        .slice(0, 8)
                        .map((element) => (
                          <li key={`${element.uid}-${element.kind}`}>
                            <b className="text-ink">{element.label || element.ref_cur || element.ref_base}</b>{" "}
                            <span className="text-ink-faint">({element.kind})</span>
                            {element.base_text && element.cur_text && element.base_text !== element.cur_text && (
                              <span className="block truncate text-ink-faint" title={`${element.base_text} → ${element.cur_text}`}>
                                {element.base_text} → {element.cur_text}
                              </span>
                            )}
                          </li>
                        ))}
                    </ul>
                  </section>
                )}
              </>
            )}
            <div className="mt-4 flex gap-2">
              <button
                type="button"
                disabled={loading || preview.status === "running" || !preview.preview_token}
                onClick={() =>
                  void run(async () => {
                    const created = await commitTemplate(preview.preview_token);
                    await refresh();
                    setSelected(created);
                    setName(created.name);
                    setDescription(created.description);
                    setView("manage");
                  })
                }
                className={primaryBtn}
              >
                Save reusable template
              </button>
              <button type="button" onClick={() => setView("create")} className={quietBtn}>Back</button>
            </div>
          </>
        )}

        {view === "manage" && selected && (
          <>
            <p className="text-sm text-ink-dim">
              Rename or describe this personal template, export a portable copy, or delete it.
            </p>
            <label className="mt-4 block text-xs text-ink-dim">
              Name
              <input value={name} onChange={(event) => setName(event.target.value)} className={fieldClass + " mt-1"} />
            </label>
            <label className="mt-3 block text-xs text-ink-dim">
              Description
              <textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={3} className={fieldClass + " mt-1 resize-none"} />
            </label>
            <p className="mt-2 text-[11px] text-ink-faint">
              {selected.section_number} {selected.section_title} · {selected.article_count} articles · {selected.paragraph_count} provisions
            </p>
            <div className="mt-4 flex flex-wrap gap-2">
              <button
                type="button"
                disabled={!name.trim() || loading}
                onClick={() =>
                  void run(async () => {
                    const updated = await updateTemplate(selected.id, {
                      name: name.trim(),
                      description: description.trim(),
                    });
                    setSelected(updated);
                    await refresh();
                  })
                }
                className={primaryBtn}
              >
                Save details
              </button>
              <a
                href={templateExportUrl(selected.id)}
                download
                onClick={(event) => {
                  const nativeSave = window.pywebview?.api?.save_template;
                  if (!nativeSave) return;
                  event.preventDefault();
                  void nativeSave(selected.id);
                }}
                className={quietBtn}
                data-capability="template.manage"
              >
                Export template
              </a>
              {!confirmDelete ? (
                <button type="button" onClick={() => setConfirmDelete(true)} className={quietBtn}>
                  Delete…
                </button>
              ) : (
                <>
                  <button
                    type="button"
                    onClick={() =>
                      void run(async () => {
                        await deleteTemplate(selected.id);
                        await refresh();
                        setSelected(null);
                        setView("browse");
                      })
                    }
                    className="rounded-md border border-err/60 bg-err/10 px-2.5 py-1.5 text-xs text-err"
                  >
                    Confirm delete
                  </button>
                  <button type="button" onClick={() => setConfirmDelete(false)} className={quietBtn}>Keep</button>
                </>
              )}
            </div>
          </>
        )}

        {error && (
          <p role="alert" className="mt-3 rounded-lg border border-err/40 bg-err/10 px-3 py-2 text-xs text-err">
            {error}
          </p>
        )}

        <input
          ref={briefRef}
          type="file"
          accept=".basproject,.baspec,application/vnd.buildaspec.project-brief+json"
          className="hidden"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void inspectBrief(file);
            event.target.value = "";
          }}
        />
        <input
          ref={importRef}
          type="file"
          accept=".bastemplate,application/vnd.buildaspec.template+json,application/json"
          className="hidden"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void importFile(file);
            event.target.value = "";
          }}
        />

        <div className="mt-4 flex items-center gap-2 border-t border-edge pt-3">
          {view !== (templatesOnly ? "browse" : "start") && view !== "preview" && view !== "create" && view !== "brief" && (
            <button type="button" onClick={() => setView("browse")} className={quietBtn}>‹ All templates</button>
          )}
          {view === "browse" && !templatesOnly && (
            <button
              type="button"
              onClick={() => setView(brief ? "brief" : "start")}
              className={quietBtn}
            >
              {brief ? "‹ Back to the brief" : "‹ Start choices"}
            </button>
          )}
          <span className="flex-1" />
          <button
            type="button"
            onClick={() => {
              previewRunRef.current += 1;
              onCancel();
            }}
            className={quietBtn}
          >
            Close
          </button>
        </div>
      </div>
    </ModalShell>
  );
}
