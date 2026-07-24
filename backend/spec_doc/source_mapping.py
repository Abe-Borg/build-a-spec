"""Immutable anchors from the semantic spec tree back to an imported DOCX.

P1's preservation path deliberately keeps these anchors *outside* the
``SpecSection`` model.  They describe the exact, immutable source package,
not content the model is allowed to author, and therefore must not ride the
LLM context or ordinary semantic version snapshots.

Only direct body paragraphs receive bindings. A binding is text-editable when
its complete visible text lives in one ordinary ``w:t`` inside one ordinary
``w:r``. Paragraph/run properties may be present or absent, but no other
inline or range markup is accepted. Everything else remains mapped so the
exporter can name the unsupported element precisely and fail closed.
"""
from __future__ import annotations

import hashlib
import json
import posixpath
import re
import zipfile
from dataclasses import dataclass
from io import BytesIO
from types import MappingProxyType
from typing import Any, Mapping, Sequence
from urllib.parse import unquote, urlsplit

from lxml import etree

from .model import Paragraph, SpecSection

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_STRICT_W_NS = "http://purl.oclc.org/ooxml/wordprocessingml/main"
_W14_NS = "http://schemas.microsoft.com/office/word/2010/wordml"
_WML_NAMESPACES = frozenset(
    {
        _W_NS,
        _STRICT_W_NS,
    }
)
_W_P = f"{{{_W_NS}}}p"
_W_PPR = f"{{{_W_NS}}}pPr"
_W_R = f"{{{_W_NS}}}r"
_W_RPR = f"{{{_W_NS}}}rPr"
_W_T = f"{{{_W_NS}}}t"
_W_SECTPR = f"{{{_W_NS}}}sectPr"
_W14_PARA_ID = f"{{{_W14_NS}}}paraId"

_OPC_RELATIONSHIP_NAMESPACES = frozenset(
    {
        "http://schemas.openxmlformats.org/package/2006/relationships",
    }
)
_OPC_CONTENT_TYPE_NAMESPACES = frozenset(
    {
        "http://schemas.openxmlformats.org/package/2006/content-types",
    }
)
_SETTINGS_RELATIONSHIP_TYPES = frozenset(
    {
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings",
        "http://purl.oclc.org/ooxml/officeDocument/relationships/settings",
    }
)
_SIGNATURE_RELATIONSHIP_TYPES = frozenset(
    {
        "http://schemas.openxmlformats.org/package/2006/relationships/digital-signature/origin",
        "http://schemas.openxmlformats.org/package/2006/relationships/digital-signature/signature",
        "http://schemas.openxmlformats.org/package/2006/relationships/digital-signature/certificate",
    }
)
_ACTIVE_RELATIONSHIP_TYPES = frozenset(
    {
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/control",
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject",
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/package",
        "http://purl.oclc.org/ooxml/officeDocument/relationships/control",
        "http://purl.oclc.org/ooxml/officeDocument/relationships/oleObject",
        "http://purl.oclc.org/ooxml/officeDocument/relationships/package",
        "http://schemas.microsoft.com/office/2006/relationships/activeXControl",
        "http://schemas.microsoft.com/office/2006/relationships/activeXControlBinary",
        "http://schemas.microsoft.com/office/2006/relationships/vbaProject",
        "http://schemas.microsoft.com/office/2006/relationships/wordVbaData",
    }
)
_SIGNATURE_CONTENT_TYPES = frozenset(
    {
        "application/vnd.openxmlformats-package.digital-signature-certificate",
        "application/vnd.openxmlformats-package.digital-signature-origin",
        "application/vnd.openxmlformats-package.digital-signature-xmlsignature+xml",
    }
)
_ACTIVE_CONTENT_TYPES = frozenset(
    {
        "application/vnd.ms-office.activex",
        "application/vnd.ms-office.activex+xml",
        "application/vnd.ms-office.vbaproject",
        "application/vnd.ms-office.vbaprojectsignature",
        "application/vnd.ms-word.document.macroenabled.main+xml",
        "application/vnd.ms-word.template.macroenabledtemplate.main+xml",
        "application/vnd.ms-word.vbadata+xml",
        "application/vnd.openxmlformats-officedocument.oleobject",
        "application/vnd.openxmlformats-officedocument.package",
    }
)
_SETTINGS_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"
)

_REVISION_LOCAL_NAMES = frozenset(
    {
        "ins",
        "del",
        "moveFrom",
        "moveTo",
        "moveFromRangeStart",
        "moveFromRangeEnd",
        "moveToRangeStart",
        "moveToRangeEnd",
        "customXmlInsRangeStart",
        "customXmlInsRangeEnd",
        "customXmlDelRangeStart",
        "customXmlDelRangeEnd",
        "customXmlMoveFromRangeStart",
        "customXmlMoveFromRangeEnd",
        "customXmlMoveToRangeStart",
        "customXmlMoveToRangeEnd",
        "customXmlPrChange",
        "pPrChange",
        "rPrChange",
        "sectPrChange",
        "tblPrChange",
        "tblPrExChange",
        "tblGridChange",
        "trPrChange",
        "tcPrChange",
        "numberingChange",
        "cellIns",
        "cellDel",
        "cellMerge",
    }
)
_OFFICE_2010_REVISION_LOCAL_NAMES = frozenset(
    {
        "conflictIns",
        "conflictDel",
        "customXmlConflictInsRangeStart",
        "customXmlConflictInsRangeEnd",
        "customXmlConflictDelRangeStart",
        "customXmlConflictDelRangeEnd",
    }
)
_REVISION_BEARING_RELATIONSHIP_SUFFIXES = frozenset(
    {
        "/officedocument",
        "/glossarydocument",
        "/header",
        "/footer",
        "/footnotes",
        "/endnotes",
        "/comments",
        "/styles",
        "/numbering",
        "/settings",
    }
)
_MAX_REVISION_SCAN_BYTES = 64 * 1024 * 1024
_MAX_OPC_DISCOVERY_BYTES = 64 * 1024 * 1024
_ACTIVE_MEMBER_MARKERS = (
    "/activex/",
    "/embeddings/",
    "/oleobject",
    "vbaproject.bin",
)
_SOURCE_MAP_KIND = "buildaspec-source-map"
_SOURCE_MAP_FORMAT = 1
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MALFORMED_PERCENT_ESCAPE_RE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_ENCODED_SEPARATOR_RE = re.compile(r"%(?:2[fF]|5[cC])")
_DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:")


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
    """The only ``w:t`` slice source-preserving mode may replace.

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

    The existing importer deliberately normalizes whitespace. Text edits only
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
    root = etree.fromstring(data, parser=parser)
    if root.getroottree().docinfo.doctype:
        raise ValueError("DTD-bearing OPC XML is not accepted.")
    return root


def _namespace_name(tag: Any) -> str:
    if not isinstance(tag, str) or not tag.startswith("{") or "}" not in tag:
        return ""
    return tag[1:].split("}", 1)[0]


@dataclass(frozen=True)
class _OpcRelationship:
    source_part: str
    rel_type: str
    target: str
    external: bool


@dataclass(frozen=True)
class _OpcDiscovery:
    relationships: tuple[_OpcRelationship, ...]
    content_type_overrides: Mapping[str, str]
    content_type_defaults: Mapping[str, str]
    declared_content_types: frozenset[str]

    def content_type_for(self, part_name: str) -> str | None:
        override = self.content_type_overrides.get(part_name)
        if override is not None:
            return override
        leaf = part_name.rsplit("/", 1)[-1]
        if "." not in leaf:
            return None
        return self.content_type_defaults.get(leaf.rsplit(".", 1)[-1].casefold())


def _relationship_source_part(member_name: str) -> str | None:
    """Return the OPC source part represented by one ``.rels`` member."""
    if member_name == "_rels/.rels":
        return ""
    directory, filename = posixpath.split(member_name)
    parent, rels_dir = posixpath.split(directory)
    if rels_dir != "_rels" or not filename.endswith(".rels"):
        return None
    source_leaf = filename[: -len(".rels")]
    if not source_leaf:
        return None
    return posixpath.join(parent, source_leaf) if parent else source_leaf


def _resolve_internal_target(source_part: str, target: str) -> str:
    """Resolve an internal OPC relationship target to a safe ZIP member."""
    if not isinstance(target, str) or not target.strip() or "\\" in target:
        raise ValueError("Malformed internal OPC relationship target.")
    target = target.strip()
    # ``urllib.parse.unquote`` deliberately leaves malformed percent escapes
    # unchanged.  OPC targets are URI references, so accepting a literal
    # trailing ``%`` or non-hex escape would let different consumers resolve
    # the same relationship differently.
    if _MALFORMED_PERCENT_ESCAPE_RE.search(target):
        raise ValueError("Malformed internal OPC relationship target.")
    if _ENCODED_SEPARATOR_RE.search(target):
        raise ValueError("Malformed internal OPC relationship target.")
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("Malformed internal OPC relationship target.")
    try:
        target_path = unquote(parsed.path, errors="strict")
    except UnicodeError as exc:
        raise ValueError("Malformed internal OPC relationship target.") from exc
    raw_segments = parsed.path.split("/")
    decoded_segments = target_path.split("/")
    if any(
        "%" in raw_segment and decoded_segment in {".", ".."}
        for raw_segment, decoded_segment in zip(raw_segments, decoded_segments)
    ):
        raise ValueError("Malformed internal OPC relationship target.")
    if (
        "\\" in target_path
        or any(
            ord(character) < 32 or ord(character) == 127
            for character in target_path
        )
        or _DRIVE_PREFIX_RE.match(target_path.lstrip("/"))
    ):
        raise ValueError("Malformed internal OPC relationship target.")
    if target_path.startswith("/"):
        candidate = target_path.lstrip("/")
    else:
        candidate = posixpath.join(posixpath.dirname(source_part), target_path)
    normalized = posixpath.normpath(candidate)
    if (
        normalized in {"", ".", ".."}
        or normalized.startswith("../")
        or normalized.startswith("/")
        or any(part in {"", ".", ".."} for part in normalized.split("/"))
    ):
        raise ValueError("Malformed internal OPC relationship target.")
    return normalized


def _content_type_part_name(value: str) -> str:
    if not isinstance(value, str) or not value.startswith("/"):
        raise ValueError("Malformed OPC content-type part name.")
    return _resolve_internal_target("", value)


def _validate_relationship_target_uri(target: str) -> str:
    """Return one unambiguous URI-reference spelling for any relationship."""
    if not isinstance(target, str) or not target.strip():
        raise ValueError("Malformed OPC relationship target.")
    target = target.strip()
    if (
        _MALFORMED_PERCENT_ESCAPE_RE.search(target)
        or "\\" in target
        or any(character.isspace() for character in target)
    ):
        raise ValueError("Malformed OPC relationship target.")
    try:
        urlsplit(target)
        decoded = unquote(target, errors="strict")
    except (UnicodeError, ValueError) as exc:
        raise ValueError("Malformed OPC relationship target.") from exc
    if "\\" in decoded or any(
        ord(character) < 32 or ord(character) == 127 for character in decoded
    ):
        raise ValueError("Malformed OPC relationship target.")
    return target


def _parse_content_types(archive: zipfile.ZipFile) -> tuple[
    Mapping[str, str], Mapping[str, str], frozenset[str]
]:
    root = _parse_xml(archive.read("[Content_Types].xml"))
    if (
        _local_name(root.tag) != "Types"
        or _namespace_name(root.tag) not in _OPC_CONTENT_TYPE_NAMESPACES
    ):
        raise ValueError("Malformed OPC content-types part.")
    overrides: dict[str, str] = {}
    defaults: dict[str, str] = {}
    declared: set[str] = set()
    for child in root:
        if not isinstance(child.tag, str):
            continue
        local = _local_name(child.tag)
        if _namespace_name(child.tag) not in _OPC_CONTENT_TYPE_NAMESPACES:
            # Markup from another namespace cannot declare an OPC part type.
            continue
        content_type = child.get("ContentType")
        if not isinstance(content_type, str) or not content_type.strip():
            raise ValueError("Malformed OPC content-type declaration.")
        normalized_type = content_type.strip().casefold()
        if local == "Override":
            part_name = _content_type_part_name(child.get("PartName"))
            if part_name in overrides:
                raise ValueError("Duplicate OPC content-type override.")
            overrides[part_name] = normalized_type
        elif local == "Default":
            extension = child.get("Extension")
            if (
                not isinstance(extension, str)
                or not extension.strip()
                or any(character in extension for character in "/\\.")
            ):
                raise ValueError("Malformed OPC content-type default.")
            extension = extension.strip().casefold()
            if extension in defaults:
                raise ValueError("Duplicate OPC content-type default.")
            defaults[extension] = normalized_type
        else:
            raise ValueError("Malformed OPC content-types part.")
        declared.add(normalized_type)
    return MappingProxyType(overrides), MappingProxyType(defaults), frozenset(declared)


def _parse_relationship_part(
    archive: zipfile.ZipFile,
    member_name: str,
    source_part: str,
) -> tuple[_OpcRelationship, ...]:
    root = _parse_xml(archive.read(member_name))
    if (
        _local_name(root.tag) != "Relationships"
        or _namespace_name(root.tag) not in _OPC_RELATIONSHIP_NAMESPACES
    ):
        raise ValueError("Malformed OPC relationships part.")
    relationships: list[_OpcRelationship] = []
    seen_ids: set[str] = set()
    for child in root:
        if not isinstance(child.tag, str):
            continue
        if _namespace_name(child.tag) not in _OPC_RELATIONSHIP_NAMESPACES:
            # Foreign extension markup is not an OPC relationship.
            continue
        if _local_name(child.tag) != "Relationship":
            raise ValueError("Malformed OPC relationships part.")
        rel_id = child.get("Id")
        rel_type = child.get("Type")
        target = child.get("Target")
        target_mode = child.get("TargetMode", "Internal")
        normalized_rel_id = rel_id.strip() if isinstance(rel_id, str) else ""
        if (
            not isinstance(rel_id, str)
            or not normalized_rel_id
            or rel_id != normalized_rel_id
            or normalized_rel_id in seen_ids
            or not isinstance(rel_type, str)
            or not rel_type.strip()
            or not isinstance(target, str)
            or not target.strip()
            or not isinstance(target_mode, str)
            or target_mode.casefold() not in {"internal", "external"}
        ):
            raise ValueError("Malformed OPC relationship.")
        seen_ids.add(normalized_rel_id)
        normalized_target = _validate_relationship_target_uri(target)
        external = target_mode.casefold() == "external"
        if not external:
            # Prove every internal target is a safe package path even when the
            # relationship type is not one Build-a-Spec otherwise interprets.
            _resolve_internal_target(source_part, normalized_target)
        relationships.append(
            _OpcRelationship(
                source_part=source_part,
                rel_type=rel_type.strip(),
                target=normalized_target,
                external=external,
            )
        )
    return tuple(relationships)


def _discover_opc(archive: zipfile.ZipFile) -> _OpcDiscovery:
    relationship_parts = [
        (info, source_part)
        for info in archive.infolist()
        if (source_part := _relationship_source_part(info.filename)) is not None
    ]
    discovery_bytes = sum(info.file_size for info, _source in relationship_parts)
    try:
        content_types_info = archive.getinfo("[Content_Types].xml")
    except KeyError as exc:
        raise ValueError("The OPC content-types part is unavailable.") from exc
    discovery_bytes += content_types_info.file_size
    if discovery_bytes > _MAX_OPC_DISCOVERY_BYTES:
        raise ValueError("OPC relationship discovery exceeds its safety limit.")

    overrides, defaults, declared = _parse_content_types(archive)
    relationships: list[_OpcRelationship] = []
    for info, source_part in relationship_parts:
        relationships.extend(
            _parse_relationship_part(archive, info.filename, source_part)
        )
    return _OpcDiscovery(
        relationships=tuple(relationships),
        content_type_overrides=overrides,
        content_type_defaults=defaults,
        declared_content_types=declared,
    )


def _on_off_value(element) -> bool | None:
    element_namespace = _namespace_name(element.tag)
    value_attributes = [
        (name, value)
        for name, value in element.attrib.items()
        if _local_name(name) == "val"
    ]
    if not value_attributes:
        return True
    if len(value_attributes) != 1:
        return None
    name, value = value_attributes[0]
    if _namespace_name(name) != element_namespace:
        return None
    normalized = value.strip().casefold()
    if normalized in {"1", "on", "true"}:
        return True
    if normalized in {"0", "off", "false"}:
        return False
    return None


def _settings_blockers(
    archive: zipfile.ZipFile,
    discovery: _OpcDiscovery,
) -> tuple[str, ...]:
    relationships = [
        relationship
        for relationship in discovery.relationships
        if relationship.source_part == "word/document.xml"
        and relationship.rel_type in _SETTINGS_RELATIONSHIP_TYPES
    ]
    if not relationships:
        return ()
    if len(relationships) != 1 or relationships[0].external:
        return ("unsafe_settings_xml",)
    relationship = relationships[0]
    try:
        target = _resolve_internal_target(
            relationship.source_part,
            relationship.target,
        )
        if target not in archive.namelist():
            raise ValueError("The related settings part is unavailable.")
        if discovery.content_type_for(target) != _SETTINGS_CONTENT_TYPE.casefold():
            raise ValueError("The related settings part has the wrong content type.")
        settings_root = _parse_xml(archive.read(target))
        if (
            _local_name(settings_root.tag) != "settings"
            or _namespace_name(settings_root.tag) not in _WML_NAMESPACES
        ):
            raise ValueError("The related settings part has the wrong root.")
    except (
        KeyError,
        RuntimeError,
        zipfile.BadZipFile,
        etree.XMLSyntaxError,
        ValueError,
    ):
        return ("unsafe_settings_xml",)

    blockers: list[str] = []
    settings_elements = [
        element
        for element in settings_root.iter()
        if isinstance(element.tag, str)
        and _namespace_name(element.tag) in _WML_NAMESPACES
    ]
    if any(
        _local_name(element.tag) in {"documentProtection", "writeProtection"}
        for element in settings_elements
    ):
        blockers.append("document_protection")
    track_revisions = [
        element
        for element in settings_elements
        if _local_name(element.tag) == "trackRevisions"
    ]
    if len(track_revisions) > 1:
        blockers.append("unsafe_settings_xml")
    elif track_revisions:
        enabled = _on_off_value(track_revisions[0])
        if enabled is None:
            blockers.append("unsafe_settings_xml")
        elif enabled:
            blockers.append("tracked_changes")
    return tuple(blockers)


def _is_wordprocessing_xml_content_type(content_type: str | None) -> bool:
    if content_type is None or not content_type.endswith("+xml"):
        return False
    return content_type.startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml."
    ) or content_type.startswith("application/vnd.ms-word.")


def _is_revision_element(element) -> bool:
    namespace = _namespace_name(element.tag)
    local_name = _local_name(element.tag)
    if namespace in _WML_NAMESPACES:
        return local_name in _REVISION_LOCAL_NAMES
    if namespace == _W14_NS:
        return local_name in _OFFICE_2010_REVISION_LOCAL_NAMES
    return False


def _revision_related_parts(
    archive: zipfile.ZipFile,
    discovery: _OpcDiscovery,
) -> tuple[set[str], bool]:
    """Return relationship-proven Word XML candidates and typing failures.

    OPC part names are opaque URI paths: a header can legally be named
    ``review-header.dat``. Relationship type and effective content type, not
    a filename suffix, establish whether it can carry Word revisions.
    """
    members = set(archive.namelist())
    related = {"word/document.xml"}
    malformed = False
    for relationship in discovery.relationships:
        is_revision_bearing = any(
            relationship.rel_type.casefold().endswith(suffix)
            for suffix in _REVISION_BEARING_RELATIONSHIP_SUFFIXES
        )
        if not is_revision_bearing:
            continue
        if relationship.external:
            malformed = True
            continue
        try:
            target = _resolve_internal_target(
                relationship.source_part,
                relationship.target,
            )
        except ValueError:
            malformed = True
            continue
        if target not in members:
            malformed = True
            continue
        related.add(target)
        if not _is_wordprocessing_xml_content_type(
            discovery.content_type_for(target)
        ):
            malformed = True
    return related, malformed


def _body_has_non_whitespace_character_data(root) -> bool:
    bodies = [
        element
        for element in root.iter()
        if _namespace_name(element.tag) in _WML_NAMESPACES
        and _local_name(element.tag) == "body"
    ]
    if len(bodies) != 1:
        return False
    body = bodies[0]
    if any(character not in " \t\r\n" for character in body.text or ""):
        return True
    return any(
        any(character not in " \t\r\n" for character in child.tail or "")
        for child in body
    )


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

    # OPC semantics come from relationships and content types, not customary
    # filenames.  A valid package may place settings, signatures, controls, or
    # embedded objects at another safe part name.  Conversely, an orphan file
    # at a familiar name is not authoritative.  Discovery failure makes the
    # package pass-through-only rather than guessing that mutation is safe.
    discovery: _OpcDiscovery | None = None
    try:
        discovery = _discover_opc(archive)
    except (
        KeyError,
        RuntimeError,
        NotImplementedError,
        zipfile.BadZipFile,
        etree.XMLSyntaxError,
        UnicodeError,
        ValueError,
    ):
        blockers.append("unsafe_relationship_scan")
    if discovery is not None:
        relationship_types = {
            relationship.rel_type for relationship in discovery.relationships
        }
        if relationship_types & _SIGNATURE_RELATIONSHIP_TYPES or (
            discovery.declared_content_types & _SIGNATURE_CONTENT_TYPES
        ):
            blockers.append("signed_package")
        if relationship_types & _ACTIVE_RELATIONSHIP_TYPES or (
            discovery.declared_content_types & _ACTIVE_CONTENT_TYPES
        ):
            blockers.append("active_content")
        blockers.extend(_settings_blockers(archive, discovery))

    # Pending revisions can live in headers, footers, notes, styles, or
    # numbering as well as document.xml. Part names are not authoritative in
    # OPC, so use each part's effective WordprocessingML content type. If OPC
    # discovery itself failed, the package is already pass-through-only; the
    # conventional-path fallback still supplies the most useful diagnostic.
    if discovery is not None:
        relationship_parts, malformed_relationship_part = (
            _revision_related_parts(archive, discovery)
        )
        if malformed_relationship_part:
            blockers.append("unsafe_revision_scan")
        revision_parts = [
            info
            for info in archive.infolist()
            if not info.is_dir()
            and (
                info.filename in relationship_parts
                or _is_wordprocessing_xml_content_type(
                    discovery.content_type_for(info.filename)
                )
            )
        ]
    else:
        revision_parts = [
            info
            for info in archive.infolist()
            if not info.is_dir()
            and info.filename.casefold().startswith("word/")
            and info.filename.casefold().endswith(".xml")
        ]
    if sum(info.file_size for info in revision_parts) > _MAX_REVISION_SCAN_BYTES:
        blockers.append("unsafe_revision_scan")
    else:
        for info in revision_parts:
            try:
                payload = (
                    document_xml
                    if info.filename == "word/document.xml"
                    else archive.read(info)
                )
                root = _parse_xml(payload)
            except (etree.XMLSyntaxError, ValueError, RuntimeError, zipfile.BadZipFile):
                blockers.append(
                    "unsafe_document_xml"
                    if info.filename == "word/document.xml"
                    else "unsafe_revision_scan"
                )
                continue
            if (
                info.filename == "word/document.xml"
                and _body_has_non_whitespace_character_data(root)
            ):
                blockers.append("unsafe_document_xml")
            if any(_is_revision_element(element) for element in root.iter()):
                blockers.append("tracked_changes")
                break

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
    "ambiguous_structural_insert": (
        "the new provision is not unambiguously inside one surviving "
        "numbered source island"
    ),
    "ambiguous_structural_template": (
        "the numbered island has inconsistent paragraph or run formatting, "
        "so there is no unambiguous template for a new provision"
    ),
    "automatic_numbering_required": (
        "structural edits require a genuine direct Word-numbered source provision"
    ),
    "complex_paragraph_markup": "the source paragraph contains unsupported paragraph-level markup",
    "complex_run_markup": "the source paragraph contains multiple or unsupported inline runs",
    "cross_island_move": (
        "provisions cannot move across numbered-island, parent, or opaque-body "
        "boundaries"
    ),
    "cross_parent_structural_change": "provisions cannot be reparented or moved between articles",
    "document_protection": "the source document is protected",
    "heading_change": "source-preserving mode does not patch section, part, or article headings",
    "invalid_xml_character": "the replacement contains a character XML cannot represent",
    "manual_label_structural_change": (
        "literal source labels cannot be safely renumbered after a structural edit"
    ),
    "mixed_numbering_island": "the candidate island mixes Word numbering definitions or levels",
    "nested_structural_change": (
        "P1b does not add, remove, move, or reparent nested provision subtrees"
    ),
    "noncontiguous_structural_island": (
        "the candidate provisions are not contiguous direct Word body siblings"
    ),
    "numbering_instance_not_isolated": (
        "the Word numbering instance is also referenced outside the candidate "
        "island, so a local edit could renumber preserved content"
    ),
    "not_direct_body_paragraph": "the provision is not a direct body paragraph",
    "noncontiguous_visible_text": "the provision text is not one contiguous Word text node",
    "normalized_text_not_exact_slice": (
        "the normalized provision is not one exact source-text slice"
    ),
    "section_break_paragraph": "the paragraph carries section layout properties",
    "signed_package": "editing would invalidate the source package's digital signature",
    "structural_change": (
        "the requested body structure is outside a proven-safe Word-numbered island"
    ),
    "table_projection": "the provision is a read-only projection of a preserved table",
    "tracked_changes": "the source contains pending tracked changes",
    "unsafe_relationship_scan": (
        "OPC relationships or content types could not be inspected safely"
    ),
    "unsafe_revision_scan": "Word revision-bearing XML parts could not be inspected safely",
    "unsafe_structural_island": (
        "the structural edit crosses content outside one proven-safe numbered "
        "body island"
    ),
    "unsupported_text_control": "tabs and line breaks require unsupported Word run markup",
    "unsafe_document_xml": "the main Word document XML is unsafe or malformed",
    "unsafe_settings_xml": "the Word settings XML is unsafe or malformed",
    "unsupported_source_text_lexical_form": (
        "the source text uses CDATA or embedded lexical markup that cannot be "
        "patched byte-for-byte"
    ),
    "unsupported_source_xml_encoding": (
        "source-preserving mutation currently supports only UTF-8 Word XML"
    ),
    "unsupported_raw_zip_layout": (
        "the source ZIP layout cannot be rebuilt without changing unrelated "
        "raw package records"
    ),
}


def source_blocker_message(blocker: str) -> str:
    return _BLOCKER_MESSAGES.get(blocker, blocker.replace("_", " "))


def source_replacement_text_blocker(text: str) -> str | None:
    """Return the blocker for text that cannot live in one ordinary ``w:t``."""
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
    "semantic_body_projection",
    "semantic_body_projection_sha256",
    "source_blocker_message",
    "source_replacement_text_blocker",
]
