"""Immutable anchors from the semantic spec tree back to an imported DOCX.

P1's preservation path deliberately keeps these anchors *outside* the
``SpecSection`` model.  They describe the exact, immutable source package,
not content the model is allowed to author, and therefore must not ride the
LLM context or ordinary semantic version snapshots.

Only direct body paragraphs receive bindings.  A binding is editable in P1a
when its complete visible text lives in one ordinary ``w:t`` inside one
ordinary ``w:r``.  Paragraph/run properties may be present or absent, but no
other inline or range markup is accepted.  Everything else remains mapped so
the exporter can name the unsupported element precisely and fail closed.
"""
from __future__ import annotations

import hashlib
import json
import re
import zipfile
from dataclasses import dataclass
from io import BytesIO
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from lxml import etree

from .model import Article, Paragraph, SpecEditError, SpecSection

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W14_NS = "http://schemas.microsoft.com/office/word/2010/wordml"
_W_P = f"{{{_W_NS}}}p"
_W_PPR = f"{{{_W_NS}}}pPr"
_W_R = f"{{{_W_NS}}}r"
_W_RPR = f"{{{_W_NS}}}rPr"
_W_T = f"{{{_W_NS}}}t"
_W_SECTPR = f"{{{_W_NS}}}sectPr"
_W_DOCUMENT_PROTECTION = f"{{{_W_NS}}}documentProtection"
_W14_PARA_ID = f"{{{_W14_NS}}}paraId"

_REVISION_LOCAL_NAMES = frozenset({"ins", "del", "moveFrom", "moveTo"})
_ACTIVE_MEMBER_MARKERS = (
    "/activex/",
    "/embeddings/",
    "/oleobject",
    "vbaproject.bin",
)
_SOURCE_MAP_KIND = "buildaspec-source-map"
_SOURCE_MAP_FORMAT = 1
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _local_name(tag: Any) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def canonical_element_bytes(element) -> bytes:
    """Exclusive C14N for stable source-anchor and preservation audits."""
    return etree.tostring(
        element,
        method="c14n",
        exclusive=True,
        with_comments=False,
    )


def canonical_element_sha256(element) -> str:
    return hashlib.sha256(canonical_element_bytes(element)).hexdigest()


@dataclass(frozen=True)
class SourceTextSpan:
    """The only ``w:t`` slice P1a may replace.

    ``source_node_text`` is the complete original text node.  ``start`` and
    ``end`` select the semantic provision text while leaving a literal manual
    label (for example ``"A. "``) and any trailing source text untouched.
    """

    text_node_ordinal: int
    start: int
    end: int
    source_node_text: str

    @property
    def prefix(self) -> str:
        return self.source_node_text[: self.start]

    @property
    def suffix(self) -> str:
        return self.source_node_text[self.end :]


@dataclass(frozen=True)
class SourceParagraphBinding:
    """One semantic paragraph's immutable source-body anchor."""

    uid: str
    body_child_index: int
    element_c14n_sha256: str
    para_id: str
    source_visible_text: str
    baseline_text: str
    text_span: SourceTextSpan | None
    blockers: tuple[str, ...] = ()
    emits_from_source: bool = True

    @property
    def editable(self) -> bool:
        return (
            self.emits_from_source
            and self.text_span is not None
            and not self.blockers
        )


@dataclass(frozen=True)
class SourceBodyBlock:
    """One direct child of ``w:body``, including opaque layout blocks."""

    body_child_index: int
    tag: str
    element_c14n_sha256: str
    para_id: str = ""


@dataclass(frozen=True)
class SourceBodyMap:
    """Immutable source-package identity, body inventory, and UID anchors."""

    source_sha256: str
    document_xml_sha256: str
    baseline_projection_sha256: str
    body_child_count: int
    body_blocks: tuple[SourceBodyBlock, ...]
    bindings: Mapping[str, SourceParagraphBinding]
    global_blockers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # ``frozen=True`` does not freeze a nested dict.  A mapping proxy keeps
        # the source contract immutable throughout undo/redo and model turns.
        object.__setattr__(
            self,
            "bindings",
            MappingProxyType(dict(self.bindings)),
        )

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe project-container representation (never source bytes)."""
        return {
            "kind": _SOURCE_MAP_KIND,
            "format": _SOURCE_MAP_FORMAT,
            "source_sha256": self.source_sha256,
            "document_xml_sha256": self.document_xml_sha256,
            "baseline_projection_sha256": self.baseline_projection_sha256,
            "body_child_count": self.body_child_count,
            "body_blocks": [
                {
                    "body_child_index": block.body_child_index,
                    "tag": block.tag,
                    "element_c14n_sha256": block.element_c14n_sha256,
                    "para_id": block.para_id,
                }
                for block in self.body_blocks
            ],
            "bindings": {
                uid: {
                    "uid": binding.uid,
                    "body_child_index": binding.body_child_index,
                    "element_c14n_sha256": binding.element_c14n_sha256,
                    "para_id": binding.para_id,
                    "source_visible_text": binding.source_visible_text,
                    "baseline_text": binding.baseline_text,
                    "text_span": (
                        {
                            "text_node_ordinal": binding.text_span.text_node_ordinal,
                            "start": binding.text_span.start,
                            "end": binding.text_span.end,
                            "source_node_text": binding.text_span.source_node_text,
                        }
                        if binding.text_span is not None
                        else None
                    ),
                    "blockers": list(binding.blockers),
                    "emits_from_source": binding.emits_from_source,
                }
                for uid, binding in self.bindings.items()
            },
            "global_blockers": list(self.global_blockers),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "SourceBodyMap":
        """Strictly restore untrusted source-map JSON.

        Container-level size/depth limits belong to the project loader.  This
        method validates mapping-specific identities, indices, hashes, spans,
        and UID consistency before any source export can use them.
        """
        if not isinstance(value, dict):
            raise ValueError("Malformed source map.")
        if (
            value.get("kind") != _SOURCE_MAP_KIND
            or value.get("format") != _SOURCE_MAP_FORMAT
        ):
            raise ValueError("Unsupported source map format.")

        def sha(name: str) -> str:
            result = value.get(name)
            if not isinstance(result, str) or not _SHA256_RE.fullmatch(result):
                raise ValueError(f"Malformed source map {name}.")
            return result

        count = value.get("body_child_count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("Malformed source map body_child_count.")
        blocks_value = value.get("body_blocks")
        bindings_value = value.get("bindings")
        globals_value = value.get("global_blockers", [])
        if (
            not isinstance(blocks_value, list)
            or len(blocks_value) != count
            or not isinstance(bindings_value, dict)
            or not isinstance(globals_value, list)
        ):
            raise ValueError("Malformed source map inventory.")

        blocks: list[SourceBodyBlock] = []
        seen_indices: set[int] = set()
        for raw in blocks_value:
            if not isinstance(raw, dict):
                raise ValueError("Malformed source body block.")
            index = raw.get("body_child_index")
            tag = raw.get("tag")
            digest = raw.get("element_c14n_sha256")
            para_id = raw.get("para_id", "")
            if (
                isinstance(index, bool)
                or not isinstance(index, int)
                or not 0 <= index < count
                or index in seen_indices
                or not isinstance(tag, str)
                or not tag
                or not isinstance(digest, str)
                or not _SHA256_RE.fullmatch(digest)
                or not isinstance(para_id, str)
            ):
                raise ValueError("Malformed source body block.")
            seen_indices.add(index)
            blocks.append(
                SourceBodyBlock(index, tag, digest, para_id)
            )
        if seen_indices != set(range(count)):
            raise ValueError("Source body block indices are not contiguous.")
        blocks.sort(key=lambda block: block.body_child_index)

        bindings: dict[str, SourceParagraphBinding] = {}
        for key, raw in bindings_value.items():
            if not isinstance(key, str) or not key or not isinstance(raw, dict):
                raise ValueError("Malformed source paragraph binding.")
            uid = raw.get("uid")
            index = raw.get("body_child_index")
            digest = raw.get("element_c14n_sha256")
            para_id = raw.get("para_id", "")
            visible = raw.get("source_visible_text")
            baseline = raw.get("baseline_text")
            blockers_value = raw.get("blockers", [])
            emits = raw.get("emits_from_source")
            if (
                uid != key
                or isinstance(index, bool)
                or not isinstance(index, int)
                or not 0 <= index < count
                or not isinstance(digest, str)
                or not _SHA256_RE.fullmatch(digest)
                or not isinstance(para_id, str)
                or not isinstance(visible, str)
                or not isinstance(baseline, str)
                or not isinstance(blockers_value, list)
                or not all(
                    isinstance(item, str) and 0 < len(item) <= 100
                    for item in blockers_value
                )
                or not isinstance(emits, bool)
            ):
                raise ValueError("Malformed source paragraph binding.")

            span_value = raw.get("text_span")
            span: SourceTextSpan | None
            if span_value is None:
                span = None
            elif isinstance(span_value, dict):
                ordinal = span_value.get("text_node_ordinal")
                start = span_value.get("start")
                end = span_value.get("end")
                node_text = span_value.get("source_node_text")
                if (
                    isinstance(ordinal, bool)
                    or not isinstance(ordinal, int)
                    or ordinal < 0
                    or isinstance(start, bool)
                    or not isinstance(start, int)
                    or isinstance(end, bool)
                    or not isinstance(end, int)
                    or not isinstance(node_text, str)
                    or not 0 <= start <= end <= len(node_text)
                ):
                    raise ValueError("Malformed source text span.")
                span = SourceTextSpan(ordinal, start, end, node_text)
            else:
                raise ValueError("Malformed source text span.")
            bindings[key] = SourceParagraphBinding(
                uid=uid,
                body_child_index=index,
                element_c14n_sha256=digest,
                para_id=para_id,
                source_visible_text=visible,
                baseline_text=baseline,
                text_span=span,
                blockers=tuple(blockers_value),
                emits_from_source=emits,
            )

        blocks_by_index = {
            block.body_child_index: block for block in blocks
        }
        for uid, binding in bindings.items():
            block = blocks_by_index[binding.body_child_index]
            if binding.element_c14n_sha256 != block.element_c14n_sha256:
                raise ValueError(
                    f"Source binding {uid!r} does not match its body-block digest."
                )
            if binding.emits_from_source:
                if block.tag != "p" or binding.para_id != block.para_id:
                    raise ValueError(
                        f"Source binding {uid!r} is not anchored to its paragraph."
                    )
                if binding.text_span is not None:
                    span = binding.text_span
                    if (
                        binding.blockers
                        or binding.source_visible_text != span.source_node_text
                        or span.source_node_text[span.start : span.end]
                        != binding.baseline_text
                    ):
                        raise ValueError(
                            f"Source binding {uid!r} has an inconsistent text span."
                        )
                elif not binding.blockers:
                    raise ValueError(
                        f"Source binding {uid!r} has no text span or blocker."
                    )
            elif (
                block.tag != "tbl"
                or binding.para_id
                or binding.text_span is not None
                or binding.blockers != ("table_projection",)
            ):
                raise ValueError(
                    f"Source binding {uid!r} is not a valid table projection."
                )

        if not all(
            isinstance(item, str) and 0 < len(item) <= 100
            for item in globals_value
        ):
            raise ValueError("Malformed source map blockers.")
        return cls(
            source_sha256=sha("source_sha256"),
            document_xml_sha256=sha("document_xml_sha256"),
            baseline_projection_sha256=sha("baseline_projection_sha256"),
            body_child_count=count,
            body_blocks=tuple(blocks),
            bindings=bindings,
            global_blockers=tuple(globals_value),
        )


def semantic_body_projection(section: SpecSection) -> tuple[tuple[str, ...], ...]:
    """Visible semantic body content/order, excluding provenance metadata.

    Status, research provenance, standards, and project-profile fields do not
    alter the Word body and therefore intentionally do not affect the source
    no-op decision.
    """
    rows: list[tuple[str, ...]] = [
        ("section", "sec", "", section.number, section.title)
    ]
    for part in section.parts:
        rows.append(("part", part.uid, "sec", part.title))
        for article in part.articles:
            rows.append(("article", article.uid, part.uid, article.title))

            def walk(paragraphs: list[Paragraph], parent_uid: str) -> None:
                for paragraph in paragraphs:
                    rows.append(
                        (
                            "paragraph",
                            paragraph.uid,
                            parent_uid,
                            paragraph.text,
                        )
                    )
                    walk(paragraph.children, paragraph.uid)

            walk(article.paragraphs, article.uid)
    return tuple(rows)


def semantic_body_projection_sha256(section: SpecSection) -> str:
    encoded = json.dumps(
        semantic_body_projection(section),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _unique_semantic_span(source_text: str, semantic_text: str) -> SourceTextSpan | None:
    """Return a unique exact substring span, or ``None`` after normalization.

    The existing importer deliberately normalizes whitespace.  P1a edits only
    when the normalized semantic value still corresponds to one exact source
    slice; guessing an offset would risk changing a literal label or formatted
    content outside the provision.
    """
    start = source_text.find(semantic_text)
    if start < 0 or start != source_text.rfind(semantic_text):
        return None
    return SourceTextSpan(
        text_node_ordinal=0,
        start=start,
        end=start + len(semantic_text),
        source_node_text=source_text,
    )


def bind_source_paragraph(
    *,
    uid: str,
    body_child_index: int,
    element,
    source_visible_text: str,
    baseline_text: str,
) -> SourceParagraphBinding:
    """Classify and bind one semantic paragraph to a direct source ``w:p``."""
    blockers: list[str] = []
    span: SourceTextSpan | None = None

    parent = element.getparent()
    if element.tag != _W_P or parent is None or _local_name(parent.tag) != "body":
        blockers.append("not_direct_body_paragraph")
    else:
        children = [child for child in element if isinstance(child.tag, str)]
        p_prs = [child for child in children if child.tag == _W_PPR]
        runs = [child for child in children if child.tag == _W_R]
        if (
            len(p_prs) > 1
            or any(child.tag not in {_W_PPR, _W_R} for child in children)
            or len(runs) != 1
            or (p_prs and children[0].tag != _W_PPR)
        ):
            blockers.append("complex_paragraph_markup")
        elif p_prs and p_prs[0].find(_W_SECTPR) is not None:
            blockers.append("section_break_paragraph")
        else:
            run_children = [
                child for child in runs[0] if isinstance(child.tag, str)
            ]
            r_prs = [child for child in run_children if child.tag == _W_RPR]
            texts = [child for child in run_children if child.tag == _W_T]
            if (
                len(r_prs) > 1
                or any(child.tag not in {_W_RPR, _W_T} for child in run_children)
                or len(texts) != 1
                or (r_prs and run_children[0].tag != _W_RPR)
            ):
                blockers.append("complex_run_markup")
            else:
                node_text = texts[0].text or ""
                if source_visible_text != node_text:
                    blockers.append("noncontiguous_visible_text")
                else:
                    span = _unique_semantic_span(node_text, baseline_text)
                    if span is None:
                        blockers.append("normalized_text_not_exact_slice")

    return SourceParagraphBinding(
        uid=uid,
        body_child_index=body_child_index,
        element_c14n_sha256=canonical_element_sha256(element),
        para_id=str(element.attrib.get(_W14_PARA_ID, "")),
        source_visible_text=source_visible_text,
        baseline_text=baseline_text,
        text_span=span,
        blockers=tuple(blockers),
    )


def bind_opaque_projection(
    *,
    uid: str,
    body_child_index: int,
    element,
    source_visible_text: str,
    baseline_text: str,
    blocker: str = "table_projection",
) -> SourceParagraphBinding:
    """Bind extracted text that must never be emitted beside its source block."""
    return SourceParagraphBinding(
        uid=uid,
        body_child_index=body_child_index,
        element_c14n_sha256=canonical_element_sha256(element),
        para_id="",
        source_visible_text=source_visible_text,
        baseline_text=baseline_text,
        text_span=None,
        blockers=(blocker,),
        emits_from_source=False,
    )


def _parse_xml(data: bytes):
    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        remove_blank_text=False,
        recover=False,
    )
    return etree.fromstring(data, parser=parser)


def _global_source_blockers(
    archive: zipfile.ZipFile,
    document_xml: bytes,
) -> tuple[str, ...]:
    blockers: list[str] = []
    folded_names = [name.casefold() for name in archive.namelist()]
    if any(
        name.startswith("_xmlsignatures/") or name.endswith("origin.sigs")
        for name in folded_names
    ):
        blockers.append("signed_package")
    if any(
        marker in f"/{name}"
        for name in folded_names
        for marker in _ACTIVE_MEMBER_MARKERS
    ):
        blockers.append("active_content")

    try:
        root = _parse_xml(document_xml)
        if any(_local_name(el.tag) in _REVISION_LOCAL_NAMES for el in root.iter()):
            blockers.append("tracked_changes")
    except (etree.XMLSyntaxError, ValueError):
        blockers.append("unsafe_document_xml")

    if "word/settings.xml" in archive.namelist():
        try:
            settings_root = _parse_xml(archive.read("word/settings.xml"))
            if (
                settings_root.find(f".//{_W_DOCUMENT_PROTECTION}") is not None
                or any(
                    _local_name(el.tag) == "documentProtection"
                    for el in settings_root.iter()
                )
            ):
                blockers.append("document_protection")
        except (etree.XMLSyntaxError, ValueError, zipfile.BadZipFile):
            blockers.append("unsafe_settings_xml")

    # Preserve first-seen order while avoiding duplicated diagnostics.
    return tuple(dict.fromkeys(blockers))


def detect_global_source_blockers(source_bytes: bytes) -> tuple[str, ...]:
    """Recompute package-wide mutation blockers from untrusted source bytes."""
    if not isinstance(source_bytes, bytes):
        raise TypeError("source_bytes must be bytes")
    try:
        with zipfile.ZipFile(BytesIO(source_bytes), "r") as archive:
            document_xml = archive.read("word/document.xml")
            return _global_source_blockers(archive, document_xml)
    except (KeyError, zipfile.BadZipFile, RuntimeError) as exc:
        raise ValueError("Could not inspect source mutation blockers.") from exc


def build_source_body_map(
    *,
    source_bytes: bytes,
    document,
    section: SpecSection,
    bindings: Sequence[SourceParagraphBinding],
) -> SourceBodyMap:
    """Freeze package identity, every body block, and all paragraph anchors."""
    if not isinstance(source_bytes, bytes):
        raise TypeError("source_bytes must be bytes")
    try:
        with zipfile.ZipFile(BytesIO(source_bytes), "r") as archive:
            document_xml = archive.read("word/document.xml")
            global_blockers = _global_source_blockers(archive, document_xml)
    except (KeyError, zipfile.BadZipFile, RuntimeError) as exc:
        raise ValueError("Could not map the source DOCX package.") from exc

    body = document.element.body
    body_children = [
        element
        for element in body.iterchildren()
        if isinstance(element.tag, str)
    ]
    blocks = tuple(
        SourceBodyBlock(
            body_child_index=index,
            tag=_local_name(element.tag),
            element_c14n_sha256=canonical_element_sha256(element),
            para_id=str(element.attrib.get(_W14_PARA_ID, "")),
        )
        for index, element in enumerate(body_children)
    )
    binding_map: dict[str, SourceParagraphBinding] = {}
    for binding in bindings:
        if binding.uid in binding_map:
            raise ValueError(f"Duplicate source binding for {binding.uid!r}.")
        binding_map[binding.uid] = binding

    paragraph_uids = {
        row[1]
        for row in semantic_body_projection(section)
        if row[0] == "paragraph"
    }
    if set(binding_map) != paragraph_uids:
        missing = sorted(paragraph_uids - set(binding_map))
        extra = sorted(set(binding_map) - paragraph_uids)
        raise ValueError(
            "Source paragraph map does not match imported content "
            f"(missing={missing}, extra={extra})."
        )

    return SourceBodyMap(
        source_sha256=hashlib.sha256(source_bytes).hexdigest(),
        document_xml_sha256=hashlib.sha256(document_xml).hexdigest(),
        baseline_projection_sha256=semantic_body_projection_sha256(section),
        body_child_count=len(blocks),
        body_blocks=blocks,
        bindings=binding_map,
        global_blockers=global_blockers,
    )


_BLOCKER_MESSAGES = {
    "active_content": "the source package contains macros, ActiveX, or embedded active content",
    "complex_paragraph_markup": "the source paragraph contains unsupported paragraph-level markup",
    "complex_run_markup": "the source paragraph contains multiple or unsupported inline runs",
    "document_protection": "the source document is protected",
    "heading_change": "P1a does not patch section or article headings",
    "invalid_xml_character": "the replacement contains a character XML cannot represent",
    "not_direct_body_paragraph": "the provision is not a direct body paragraph",
    "noncontiguous_visible_text": "the provision text is not one contiguous Word text node",
    "normalized_text_not_exact_slice": "the normalized provision is not one exact source-text slice",
    "section_break_paragraph": "the paragraph carries section layout properties",
    "signed_package": "editing would invalidate the source package's digital signature",
    "structural_change": "P1a does not add, delete, move, or reparent source content",
    "table_projection": "the provision is a read-only projection of a preserved table",
    "tracked_changes": "the source contains pending tracked changes",
    "unsupported_text_control": "tabs and line breaks require unsupported Word run markup",
    "unsafe_document_xml": "the main Word document XML is unsafe or malformed",
    "unsafe_settings_xml": "the Word settings XML is unsafe or malformed",
}


def source_blocker_message(blocker: str) -> str:
    return _BLOCKER_MESSAGES.get(blocker, blocker.replace("_", " "))


def source_replacement_text_blocker(text: str) -> str | None:
    """Return the P1a blocker for text that cannot live in one ``w:t``."""
    if any(character in text for character in "\t\r\n"):
        return "unsupported_text_control"
    for character in text:
        value = ord(character)
        if not (
            0x20 <= value <= 0xD7FF
            or 0xE000 <= value <= 0xFFFD
            or 0x10000 <= value <= 0x10FFFF
        ):
            return "invalid_xml_character"
    return None


def _paragraphs_by_uid(section: SpecSection) -> dict[str, Paragraph]:
    found: dict[str, Paragraph] = {}
    for part in section.parts:
        for article in part.articles:
            stack = list(article.paragraphs)
            while stack:
                paragraph = stack.pop()
                found[paragraph.uid] = paragraph
                stack.extend(paragraph.children)
    return found


def _articles_by_uid(section: SpecSection) -> dict[str, Article]:
    return {
        article.uid: article
        for part in section.parts
        for article in part.articles
    }


def _reject_source_edit(uid: str, blocker: str) -> None:
    raise SpecEditError(
        f"Source-backed edit rejected for {uid!r} [{blocker}]: "
        f"{source_blocker_message(blocker)}. Nothing was applied."
    )


def guard_source_edits(
    section: SpecSection,
    edits: Any,
    source_map: SourceBodyMap,
) -> None:
    """Fail closed before a P1a-incompatible edit reaches ``DocumentStore``.

    Malformed/unknown operations are left for the existing model validator so
    its established error contract remains authoritative.  This guard only
    rejects otherwise meaningful operations that would make a source-backed
    export impossible.
    """
    if not isinstance(edits, list):
        return
    paragraphs = _paragraphs_by_uid(section)
    articles = _articles_by_uid(section)

    for op in edits:
        if not isinstance(op, dict):
            continue
        action = op.get("action")
        target = op.get("target_id")
        uid = target if isinstance(target, str) and target else "unknown"

        # Metadata/provenance-only operations never touch the Word body.
        if action in {
            "set_status",
            "set_standard_edition",
            "set_standard_suppressed",
            "set_project_profile",
        }:
            continue

        if action in {"add_article", "add_paragraph", "delete", "move"}:
            _reject_source_edit(uid, "structural_change")

        if action != "replace":
            continue

        if uid == "sec":
            title_changes = "text" in op and str(op.get("text", "")).strip() != section.title
            number_changes = (
                "numbering" in op
                and str(op.get("numbering", "")).strip() != section.number
            )
            if title_changes or number_changes:
                _reject_source_edit(uid, "heading_change")
            continue

        article = articles.get(uid)
        if article is not None:
            if "text" in op and str(op.get("text", "")).strip() != article.title:
                _reject_source_edit(uid, "heading_change")
            continue

        paragraph = paragraphs.get(uid)
        if paragraph is None or "text" not in op:
            # Let model.py report nonexistent/wrong-kind targets and validate
            # status/source_item_id-only replacements.
            continue
        raw_text = op.get("text")
        if not isinstance(raw_text, str):
            continue
        if raw_text.strip() == paragraph.text:
            continue

        text_blocker = source_replacement_text_blocker(raw_text.strip())
        if text_blocker is not None:
            _reject_source_edit(uid, text_blocker)

        for blocker in source_map.global_blockers:
            _reject_source_edit(uid, blocker)
        binding = source_map.bindings.get(uid)
        if binding is None:
            _reject_source_edit(uid, "unmapped_paragraph")
        if not binding.editable:
            _reject_source_edit(
                uid,
                binding.blockers[0] if binding.blockers else "unmapped_paragraph",
            )


__all__ = [
    "SourceBodyBlock",
    "SourceBodyMap",
    "SourceParagraphBinding",
    "SourceTextSpan",
    "bind_opaque_projection",
    "bind_source_paragraph",
    "build_source_body_map",
    "canonical_element_bytes",
    "canonical_element_sha256",
    "detect_global_source_blockers",
    "guard_source_edits",
    "semantic_body_projection",
    "semantic_body_projection_sha256",
    "source_blocker_message",
    "source_replacement_text_blocker",
]
