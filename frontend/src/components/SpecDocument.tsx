/**
 * SectionFormat rendering of the server-owned document tree on the paper
 * surface: PART headings, numbered articles, lettered/numbered paragraph
 * levels, provenance badges, inline [TBD] highlighting, and a tint on
 * blocks changed during the latest turn.
 */
import type { DocParagraph, DocPart, SpecDoc } from "../types";

const TBD_SPLIT = /(\[TBD:[^\]]*\])/g;

function TbdText({ text }: { text: string }) {
  const pieces = text.split(TBD_SPLIT);
  return (
    <>
      {pieces.map((piece, i) =>
        piece.startsWith("[TBD:") ? (
          <mark
            key={i}
            className="rounded-sm bg-[#f2e3b3] px-0.5 text-[#6d5310]"
          >
            {piece}
          </mark>
        ) : (
          <span key={i}>{piece}</span>
        ),
      )}
    </>
  );
}

function StatusBadge({ status }: { status: DocParagraph["status"] }) {
  if (status === "confirmed") return null;
  const styles =
    status === "assumed"
      ? "border-[#d4a04c]/60 bg-[#f6ead2] text-[#8a6414]"
      : "border-[#c65b4e]/50 bg-[#f7e2df] text-[#a03d31]";
  return (
    <span
      className={`ml-2 inline-block rounded border px-1 py-px align-middle text-[9px] font-semibold tracking-wide uppercase ${styles}`}
    >
      {status === "assumed" ? "assumed" : "needs input"}
    </span>
  );
}

function ParagraphNode({
  p,
  depth,
  changedIds,
}: {
  p: DocParagraph;
  depth: number;
  changedIds: ReadonlySet<string>;
}) {
  return (
    <>
      <div
        id={`el-${p.id}`}
        className={`flex gap-2 rounded px-1 py-0.5 ${
          changedIds.has(p.id) ? "changed-block" : ""
        }`}
        style={{ marginLeft: `${depth * 1.4}rem` }}
      >
        <span className="w-6 shrink-0 text-right">{p.label}</span>
        <span className="min-w-0 flex-1">
          <TbdText text={p.text} />
          <StatusBadge status={p.status} />
        </span>
      </div>
      {p.children.map((child) => (
        <ParagraphNode
          key={child.id}
          p={child}
          depth={depth + 1}
          changedIds={changedIds}
        />
      ))}
    </>
  );
}

function PartBlock({
  part,
  changedIds,
}: {
  part: DocPart;
  changedIds: ReadonlySet<string>;
}) {
  return (
    <div>
      <p className="text-[13px] font-semibold">{part.title}</p>
      {part.articles.length === 0 ? (
        <p className="mt-2 text-xs text-paper-dim italic">(No articles yet.)</p>
      ) : (
        <div className="mt-3 space-y-4">
          {part.articles.map((article) => (
            <div key={article.id} id={`el-${article.id}`}>
              <p
                className={`rounded px-1 text-[13px] font-semibold ${
                  changedIds.has(article.id) ? "changed-block" : ""
                }`}
              >
                {article.number}&nbsp;&nbsp;
                <span className="uppercase">{article.title}</span>
              </p>
              <div className="mt-1.5 space-y-1">
                {article.paragraphs.map((p) => (
                  <ParagraphNode
                    key={p.id}
                    p={p}
                    depth={0}
                    changedIds={changedIds}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function SpecDocument({
  doc,
  changedIds,
}: {
  doc: SpecDoc;
  changedIds: ReadonlySet<string>;
}) {
  return (
    <div className="mx-auto max-w-2xl rounded-xl border border-paper-edge bg-paper px-10 py-12 text-[13px] leading-relaxed text-paper-ink shadow-[0_2px_16px_rgba(0,0,0,0.25)]">
      <div id="el-sec" className="text-center">
        <p
          className={`rounded text-[13px] font-semibold tracking-wide ${
            changedIds.has("sec") ? "changed-block" : ""
          }`}
        >
          SECTION {doc.section.number || "[TBD]"}
        </p>
        <p
          className={`mt-1 rounded text-[13px] font-semibold tracking-wide uppercase ${
            changedIds.has("sec") ? "changed-block" : ""
          }`}
        >
          {doc.section.title || "[TBD: section title]"}
        </p>
      </div>

      <div className="mt-10 space-y-8">
        {doc.parts.map((part) => (
          <PartBlock key={part.id} part={part} changedIds={changedIds} />
        ))}
      </div>

      <p className="mt-10 text-center text-[13px] font-semibold tracking-wide">
        END OF SECTION {doc.section.number || ""}
      </p>
    </div>
  );
}
