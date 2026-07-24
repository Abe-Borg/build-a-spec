"""Fail-closed, source-preserving DOCX export for imported specifications.

The source package is immutable.  A no-op returns its exact bytes.  Safe text
edits replace only an import-anchored ``w:t``.  P1b additionally permits a
small structural surface: flat, leaf provisions may be added, removed, or
reordered *within* one contiguous direct-body island backed by genuine Word
numbering.  Everything is derived from the final baseline/current projection;
an operation log is neither trusted nor required.

No python-docx save occurs on this path.  Every non-document ZIP member stays
byte-identical, and a post-write audit proves the final body is exactly the
planned sequence of untouched, moved, patched, or minimally-created blocks.
"""
from __future__ import annotations

import copy
import hashlib
import posixpath
import re
import urllib.parse
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from types import MappingProxyType
from typing import Mapping

from lxml import etree

from .model import Paragraph, STATUSES, SpecEditError, SpecSection, apply_edits
from .raw_zip import (
    RawZipArchive,
    RawZipError,
    audit_raw_zip_replacement,
    parse_raw_zip_archive,
    replace_document_xml_raw,
)
from .source_mapping import (
    SourceBodyMap,
    SourceParagraphBinding,
    bind_source_paragraph,
    canonical_element_bytes,
    canonical_element_sha256,
    detect_global_source_blockers,
    semantic_body_projection,
    semantic_body_projection_sha256,
    source_blocker_message,
    source_replacement_text_blocker,
)
from .source_audit import (
    SourceAuditError,
    audit_package_preservation_streaming,
)
from .source_package import SourcePackageError, inspect_docx_package
from .xml_lexical import (
    SourceXmlIndex,
    XmlByteSpan,
    XmlElementByteSpan,
    XmlLexicalError,
    XmlPatch,
    apply_xml_patches,
    build_source_xml_index,
    decoded_slice_byte_span,
    encode_word_text,
    xml_gap_is_whitespace,
)

_DOCUMENT_PART = "word/document.xml"
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W_BODY = f"{{{_W_NS}}}body"
_W_P = f"{{{_W_NS}}}p"
_W_PPR = f"{{{_W_NS}}}pPr"
_W_NUMPR = f"{{{_W_NS}}}numPr"
_W_ILVL = f"{{{_W_NS}}}ilvl"
_W_NUMID = f"{{{_W_NS}}}numId"
_W_VAL = f"{{{_W_NS}}}val"
_W_NUMBERING = f"{{{_W_NS}}}numbering"
_W_NUM = f"{{{_W_NS}}}num"
_W_ABSTRACT_NUM_ID = f"{{{_W_NS}}}abstractNumId"
_W_ABSTRACT_NUM = f"{{{_W_NS}}}abstractNum"
_W_NUMSTYLE_LINK = f"{{{_W_NS}}}numStyleLink"
_W_STYLE_LINK = f"{{{_W_NS}}}styleLink"
_W_LVL = f"{{{_W_NS}}}lvl"
_W_NUMFMT = f"{{{_W_NS}}}numFmt"
_W_LVLTEXT = f"{{{_W_NS}}}lvlText"
_W_START = f"{{{_W_NS}}}start"
_W_START_OVERRIDE = f"{{{_W_NS}}}startOverride"
_W_R = f"{{{_W_NS}}}r"
_W_RPR = f"{{{_W_NS}}}rPr"
_W_T = f"{{{_W_NS}}}t"
_W_SECTPR = f"{{{_W_NS}}}sectPr"
_W14_NS = "http://schemas.microsoft.com/office/word/2010/wordml"
_DOCUMENT_RELS_PART = "word/_rels/document.xml.rels"
_CONTENT_TYPES_PART = "[Content_Types].xml"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_RELATIONSHIPS = f"{{{_REL_NS}}}Relationships"
_RELATIONSHIP = f"{{{_REL_NS}}}Relationship"
_CT_TYPES = f"{{{_CT_NS}}}Types"
_CT_OVERRIDE = f"{{{_CT_NS}}}Override"
_CT_DEFAULT = f"{{{_CT_NS}}}Default"
_NUMBERING_REL_TYPES = frozenset(
    {
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering",
        "http://purl.oclc.org/ooxml/officeDocument/relationships/numbering",
    }
)
_NUMBERING_CONTENT_TYPES = frozenset(
    {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml",
    }
)
_AUTOMATIC_NUMBER_FORMATS = frozenset(
    {
        "bullet",
        "decimal",
        "decimalZero",
        "lowerLetter",
        "lowerRoman",
        "upperLetter",
        "upperRoman",
    }
)
_ON_OFF_ATTRIBUTES = frozenset({"val"})
_SAFE_PPR_LEAF_ATTRIBUTES: dict[str, frozenset[str]] = {
    "adjustRightInd": _ON_OFF_ATTRIBUTES,
    "autoSpaceDE": _ON_OFF_ATTRIBUTES,
    "autoSpaceDN": _ON_OFF_ATTRIBUTES,
    "bidi": _ON_OFF_ATTRIBUTES,
    "contextualSpacing": _ON_OFF_ATTRIBUTES,
    "ind": frozenset(
        {
            "end",
            "endChars",
            "firstLine",
            "firstLineChars",
            "hanging",
            "hangingChars",
            "left",
            "leftChars",
            "right",
            "rightChars",
            "start",
            "startChars",
        }
    ),
    "jc": frozenset({"val"}),
    "keepLines": _ON_OFF_ATTRIBUTES,
    "keepNext": _ON_OFF_ATTRIBUTES,
    "kinsoku": _ON_OFF_ATTRIBUTES,
    "mirrorIndents": _ON_OFF_ATTRIBUTES,
    "outlineLvl": frozenset({"val"}),
    "overflowPunct": _ON_OFF_ATTRIBUTES,
    "pageBreakBefore": _ON_OFF_ATTRIBUTES,
    "pStyle": frozenset({"val"}),
    "shd": frozenset(
        {
            "color",
            "fill",
            "themeColor",
            "themeFill",
            "themeFillShade",
            "themeFillTint",
            "themeShade",
            "themeTint",
            "val",
        }
    ),
    "snapToGrid": _ON_OFF_ATTRIBUTES,
    "spacing": frozenset(
        {
            "after",
            "afterAutospacing",
            "afterLines",
            "before",
            "beforeAutospacing",
            "beforeLines",
            "line",
            "lineRule",
        }
    ),
    "suppressAutoHyphens": _ON_OFF_ATTRIBUTES,
    "suppressLineNumbers": _ON_OFF_ATTRIBUTES,
    "suppressOverlap": _ON_OFF_ATTRIBUTES,
    "textAlignment": frozenset({"val"}),
    "textDirection": frozenset({"val"}),
    "topLinePunct": _ON_OFF_ATTRIBUTES,
    "widowControl": _ON_OFF_ATTRIBUTES,
    "wordWrap": _ON_OFF_ATTRIBUTES,
}
_SAFE_RPR_LEAF_ATTRIBUTES: dict[str, frozenset[str]] = {
    "b": _ON_OFF_ATTRIBUTES,
    "bCs": _ON_OFF_ATTRIBUTES,
    "bdr": frozenset(
        {
            "color",
            "frame",
            "shadow",
            "space",
            "sz",
            "themeColor",
            "themeShade",
            "themeTint",
            "val",
        }
    ),
    "caps": _ON_OFF_ATTRIBUTES,
    "color": frozenset(
        {"themeColor", "themeShade", "themeTint", "val"}
    ),
    "cs": _ON_OFF_ATTRIBUTES,
    "dstrike": _ON_OFF_ATTRIBUTES,
    "effect": frozenset({"val"}),
    "em": frozenset({"val"}),
    "emboss": _ON_OFF_ATTRIBUTES,
    "highlight": frozenset({"val"}),
    "i": _ON_OFF_ATTRIBUTES,
    "iCs": _ON_OFF_ATTRIBUTES,
    "imprint": _ON_OFF_ATTRIBUTES,
    "kern": frozenset({"val"}),
    "lang": frozenset({"bidi", "eastAsia", "val"}),
    "noProof": _ON_OFF_ATTRIBUTES,
    "oMath": _ON_OFF_ATTRIBUTES,
    "outline": _ON_OFF_ATTRIBUTES,
    "position": frozenset({"val"}),
    "rFonts": frozenset(
        {
            "ascii",
            "asciiTheme",
            "cs",
            "cstheme",
            "eastAsia",
            "eastAsiaTheme",
            "hAnsi",
            "hAnsiTheme",
            "hint",
        }
    ),
    "rStyle": frozenset({"val"}),
    "rtl": _ON_OFF_ATTRIBUTES,
    "shadow": _ON_OFF_ATTRIBUTES,
    "shd": _SAFE_PPR_LEAF_ATTRIBUTES["shd"],
    "smallCaps": _ON_OFF_ATTRIBUTES,
    "snapToGrid": _ON_OFF_ATTRIBUTES,
    "spacing": frozenset({"val"}),
    "specVanish": _ON_OFF_ATTRIBUTES,
    "strike": _ON_OFF_ATTRIBUTES,
    "sz": frozenset({"val"}),
    "szCs": frozenset({"val"}),
    "u": frozenset(
        {"color", "themeColor", "themeShade", "themeTint", "val"}
    ),
    "vanish": _ON_OFF_ATTRIBUTES,
    "vertAlign": frozenset({"val"}),
    "w": frozenset({"val"}),
    "webHidden": _ON_OFF_ATTRIBUTES,
}


class SourcePatchError(ValueError):
    """A requested export cannot be represented without fidelity risk."""

    def __init__(self, uid: str, blocker: str, detail: str | None = None) -> None:
        self.uid = uid
        self.blocker = blocker
        self.detail = detail or source_blocker_message(blocker)
        super().__init__(
            f"Source-preserving export blocked for {uid!r} [{blocker}]: "
            f"{self.detail}."
        )


@dataclass(frozen=True)
class SourcePatchIssue:
    uid: str
    blocker: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "uid": self.uid,
            "blocker": self.blocker,
            "message": self.message,
        }


@dataclass(frozen=True)
class SourcePatchReadiness:
    ready: bool
    no_op: bool
    changed_uids: tuple[str, ...] = ()
    blockers: tuple[SourcePatchIssue, ...] = ()
    mutation_blockers: tuple[SourcePatchIssue, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "no_op": self.no_op,
            "changed_uids": list(self.changed_uids),
            "blockers": [blocker.to_dict() for blocker in self.blockers],
            "mutation_blockers": [
                blocker.to_dict() for blocker in self.mutation_blockers
            ],
        }


@dataclass(frozen=True, slots=True)
class SourceCapabilityPlacement:
    """One unambiguous insertion island and its exact sibling positions."""

    island_key: str
    allowed_positions: tuple[int, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed_positions", tuple(self.allowed_positions))

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "island_key": self.island_key,
            "allowed_positions": list(self.allowed_positions),
        }
        if self.allowed_positions:
            minimum = min(self.allowed_positions)
            maximum = max(self.allowed_positions)
            if self.allowed_positions == tuple(range(minimum, maximum + 1)):
                result["minimum_position"] = minimum
                result["maximum_position"] = maximum
        return result


@dataclass(frozen=True, slots=True)
class SourceOperationCapability:
    """Server-derived permission for one semantic operation on one element."""

    allowed: bool
    blocker: str | None = None
    message: str | None = None
    island_key: str | None = None
    current_position: int | None = None
    minimum_position: int | None = None
    maximum_position: int | None = None
    allowed_positions: tuple[int, ...] = ()
    placements: tuple[SourceCapabilityPlacement, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed_positions", tuple(self.allowed_positions))
        object.__setattr__(self, "placements", tuple(self.placements))

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {"allowed": self.allowed}
        if self.blocker is not None:
            result["blocker"] = self.blocker
        if self.message is not None:
            result["message"] = self.message
        if self.island_key is not None:
            result["island_key"] = self.island_key
        if self.current_position is not None:
            result["current_position"] = self.current_position
        if self.minimum_position is not None:
            result["minimum_position"] = self.minimum_position
        if self.maximum_position is not None:
            result["maximum_position"] = self.maximum_position
        if self.allowed_positions:
            result["allowed_positions"] = list(self.allowed_positions)
        if self.placements:
            result["placements"] = [item.to_dict() for item in self.placements]
        return result


@dataclass(frozen=True, slots=True)
class SourceElementCapabilities:
    """Deeply immutable operation map for one current semantic element."""

    operations: Mapping[str, SourceOperationCapability]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "operations",
            MappingProxyType(dict(self.operations)),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            operation: self.operations[operation].to_dict()
            for operation in sorted(self.operations)
        }


@dataclass(frozen=True, slots=True)
class SourceCapabilityReport:
    """Immutable per-element source-preservation capability contract."""

    status: str
    elements: Mapping[str, SourceElementCapabilities]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "elements",
            MappingProxyType(dict(self.elements)),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "elements": {
                uid: self.elements[uid].to_dict()
                for uid in sorted(self.elements)
            },
        }


@dataclass(frozen=True, slots=True)
class SourcePackageMemberInventory:
    """Immutable ZIP metadata captured while the source is indexed once."""

    filename: str
    file_size: int
    compress_size: int
    crc32: int
    compression_method: int
    flags: int


@dataclass(frozen=True, slots=True)
class SourceParagraphTemplate:
    """Proven immutable fragments needed to synthesize one minimal paragraph."""

    body_child_index: int
    word_prefix: bytes
    namespace_declarations: bytes
    p_pr_bytes: bytes
    r_pr_bytes: bytes


@dataclass(frozen=True, slots=True)
class SourceBodyInventoryItem:
    """Source-only semantic facts retained without keeping an lxml node."""

    body_child_index: int
    expanded_name: str
    element_c14n_sha256: str
    element_c14n: bytes = field(repr=False)
    immediately_follows_previous: bool = False
    direct_numbering: tuple[str, str, str] | None = None
    ppr_signature: str = ""
    rpr_signature: str = ""


@dataclass(frozen=True, slots=True)
class SourcePatchContext:
    """Deeply immutable indexes and identity facts for one retained source.

    The context is process-local derived state. It is never serialized into a
    ``.baspec`` file and never retains a mutable lxml tree. Callers must still
    pass source bytes, source map, and baseline to every public gate; those
    inputs are checked against this context before any cached fact is trusted.
    """

    source_sha256: str
    source_bytes: bytes = field(repr=False)
    source_map: SourceBodyMap = field(repr=False)
    baseline_projection_sha256: str
    document_xml: bytes = field(repr=False)
    document_xml_sha256: str
    xml_index: SourceXmlIndex | None = field(repr=False)
    global_blockers: tuple[str, ...]
    runtime_mutation_issues: tuple[SourcePatchIssue, ...]
    numbering_levels: frozenset[tuple[str, str]]
    numbering_usage_counts: Mapping[str, int]
    body_inventory: tuple[SourceBodyInventoryItem, ...]
    paragraph_templates: Mapping[int, SourceParagraphTemplate]
    package_inventory: tuple[SourcePackageMemberInventory, ...]
    document_tag: str
    document_attributes: tuple[tuple[str, str], ...]
    document_namespace_bindings: tuple[tuple[str, str], ...]
    body_attributes: tuple[tuple[str, str], ...]
    body_has_non_whitespace_direct_character_data: bool
    non_body_c14n_sha256: tuple[str, ...]
    raw_zip_archive: RawZipArchive | None = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "numbering_usage_counts",
            MappingProxyType(dict(self.numbering_usage_counts)),
        )
        object.__setattr__(
            self,
            "paragraph_templates",
            MappingProxyType(dict(self.paragraph_templates)),
        )


@dataclass(frozen=True)
class _TextPatch:
    binding: SourceParagraphBinding
    new_text: str


@dataclass(frozen=True)
class _ProjectedParagraph:
    uid: str
    parent_uid: str
    text: str
    depth: int
    child_uids: tuple[str, ...]


@dataclass(frozen=True)
class _NumberedMember:
    binding: SourceParagraphBinding
    num_id: str
    ilvl: str
    signature: str
    ppr_signature: str
    rpr_signature: str


@dataclass(frozen=True)
class _NumberedIsland:
    key: str
    article_uid: str
    segment: int
    members: tuple[_NumberedMember, ...]

    @property
    def start_index(self) -> int:
        return self.members[0].binding.body_child_index

    @property
    def end_index(self) -> int:
        return self.members[-1].binding.body_child_index


@dataclass(frozen=True)
class _DesiredParagraph:
    uid: str
    text: str
    binding: SourceParagraphBinding | None
    template: SourceParagraphBinding | None = None


@dataclass(frozen=True)
class _IslandPatch:
    island: _NumberedIsland
    desired: tuple[_DesiredParagraph, ...]


@dataclass(frozen=True)
class _IslandBytePatch:
    island_key: str
    source_span: XmlByteSpan
    desired_elements: tuple[bytes, ...]
    original_gaps: tuple[bytes, ...]
    changed_uids: tuple[str, ...]

    @property
    def replacement(self) -> bytes:
        """Assign desired elements to source slots without rewriting gaps."""
        slot_count = len(self.original_gaps) + 1
        slots: list[list[bytes]] = [[] for _index in range(slot_count)]
        if len(self.desired_elements) <= slot_count:
            for index, element in enumerate(self.desired_elements):
                slots[index].append(element)
        else:
            for index in range(slot_count - 1):
                slots[index].append(self.desired_elements[index])
            slots[-1].extend(self.desired_elements[slot_count - 1 :])
        output: list[bytes] = []
        for index, slot in enumerate(slots):
            output.extend(slot)
            if index < len(self.original_gaps):
                output.append(self.original_gaps[index])
        return b"".join(output)


@dataclass(frozen=True)
class _PatchPlan:
    text_patches: tuple[_TextPatch, ...]
    island_patches: tuple[_IslandPatch, ...]
    structural_changed_uids: tuple[str, ...] = ()

    @property
    def no_op(self) -> bool:
        return not self.text_patches and not self.island_patches

    @property
    def changed_uids(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                [patch.binding.uid for patch in self.text_patches]
                + list(self.structural_changed_uids)
            )
        )


@dataclass(frozen=True, slots=True)
class _ValidatedPatch:
    plan: _PatchPlan
    context: SourcePatchContext
    lexically_patched_xml: bytes | None = None
    prepared_output: bytes | None = None


@dataclass(frozen=True, slots=True)
class _BoundInputs:
    """One identity-bound ``(source_bytes, source_map, baseline)`` triple.

    Everything here is derived purely from inputs the capability sweep holds
    fixed for its whole run, so binding once and reusing it is equivalent to
    re-deriving it per probe — just not quadratic. Deliberately private: the
    public gates keep validating from scratch on every call.
    """

    context: SourcePatchContext
    baseline_projection: tuple[tuple[str, ...], ...]
    baseline_paragraphs: dict[str, "_ProjectedParagraph"]
    baseline_children: dict[str, tuple[str, ...]]
    baseline_headings: tuple[tuple[str, ...], ...]
    # Exactly what _build_numbered_islands returns for this baseline.
    islands: tuple[
        dict[str, "_NumberedIsland"],
        dict[str, "_NumberedIsland"],
        dict[str, tuple["_NumberedIsland", ...]],
        dict[str, str],
    ]


def _xml_parser() -> etree.XMLParser:
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        remove_blank_text=False,
        recover=False,
    )


def _meaningful_children(element) -> list:
    return [child for child in element.iterchildren() if isinstance(child.tag, str)]


def _has_non_whitespace_direct_character_data(element) -> bool:
    if any(character not in " \t\r\n" for character in element.text or ""):
        return True
    return any(
        any(character not in " \t\r\n" for character in child.tail or "")
        for child in element.iterchildren()
    )


def _read_document_xml(source_bytes: bytes) -> bytes:
    try:
        with zipfile.ZipFile(BytesIO(source_bytes), "r") as archive:
            return archive.read(_DOCUMENT_PART)
    except (KeyError, zipfile.BadZipFile, RuntimeError) as exc:
        raise SourcePatchError(
            "source",
            "unsafe_package",
            "the source DOCX main document part could not be read",
        ) from exc


def _read_document_xml_and_inventory(
    source_bytes: bytes,
) -> tuple[bytes, tuple[SourcePackageMemberInventory, ...]]:
    try:
        with zipfile.ZipFile(BytesIO(source_bytes), "r") as archive:
            infos = archive.infolist()
            document_xml = archive.read(_DOCUMENT_PART)
            inventory = tuple(
                SourcePackageMemberInventory(
                    filename=info.filename,
                    file_size=info.file_size,
                    compress_size=info.compress_size,
                    crc32=info.CRC,
                    compression_method=info.compress_type,
                    flags=info.flag_bits,
                )
                for info in infos
            )
    except (KeyError, zipfile.BadZipFile, RuntimeError) as exc:
        raise SourcePatchError(
            "source",
            "unsafe_package",
            "the source DOCX package inventory could not be indexed",
        ) from exc
    return document_xml, inventory


def _parse_document_xml(document_xml: bytes):
    try:
        tree = etree.parse(BytesIO(document_xml), parser=_xml_parser())
    except (etree.XMLSyntaxError, ValueError) as exc:
        raise SourcePatchError(
            "source",
            "unsafe_document_xml",
            "the source main document XML is malformed",
        ) from exc
    if tree.docinfo.doctype:
        raise SourcePatchError(
            "source",
            "unsafe_document_xml",
            "DTD-bearing Word XML is not accepted on the patch path",
        )
    root = tree.getroot()
    bodies = root.findall(f".//{_W_BODY}")
    if len(bodies) != 1:
        raise SourcePatchError(
            "source",
            "unsupported_word_namespace",
            "the package does not contain exactly one supported Word body",
        )
    return tree, bodies[0]


def _projection_paragraphs(
    section: SpecSection,
) -> tuple[
    dict[str, _ProjectedParagraph],
    dict[str, tuple[str, ...]],
    tuple[tuple[str, ...], ...],
]:
    """Return paragraph identity/tree state plus immutable heading rows."""
    rows = semantic_body_projection(section)
    paragraph_rows = {row[1]: row for row in rows if row[0] == "paragraph"}
    children: dict[str, list[str]] = {}
    for row in paragraph_rows.values():
        children.setdefault(row[2], []).append(row[1])

    depth_cache: dict[str, int] = {}

    def depth(uid: str, active: set[str] | None = None) -> int:
        if uid in depth_cache:
            return depth_cache[uid]
        active = set() if active is None else active
        if uid in active:
            raise SourcePatchError(
                uid, "structural_change", "the paragraph tree is cyclic"
            )
        active.add(uid)
        parent_uid = paragraph_rows[uid][2]
        value = 0 if parent_uid not in paragraph_rows else depth(parent_uid, active) + 1
        active.remove(uid)
        depth_cache[uid] = value
        return value

    projected = {
        uid: _ProjectedParagraph(
            uid=uid,
            parent_uid=row[2],
            text=row[3],
            depth=depth(uid),
            child_uids=tuple(children.get(uid, ())),
        )
        for uid, row in paragraph_rows.items()
    }
    return (
        projected,
        {parent: tuple(uids) for parent, uids in children.items()},
        tuple(row for row in rows if row[0] != "paragraph"),
    )


def _validate_fixed_projection(
    baseline: SpecSection,
    current: SpecSection,
    *,
    bound_inputs: "_BoundInputs | None" = None,
) -> tuple[
    dict[str, _ProjectedParagraph],
    dict[str, tuple[str, ...]],
    dict[str, _ProjectedParagraph],
    dict[str, tuple[str, ...]],
]:
    """Reject every body-structure change outside flat article provisions."""
    if bound_inputs is not None:
        base = bound_inputs.baseline_paragraphs
        base_children = bound_inputs.baseline_children
        base_headings = bound_inputs.baseline_headings
    else:
        base, base_children, base_headings = _projection_paragraphs(baseline)
    cur, cur_children, cur_headings = _projection_paragraphs(current)
    if base_headings != cur_headings:
        cur_by_identity = {(row[0], row[1], row[2]): row for row in cur_headings}
        for row in base_headings:
            other = cur_by_identity.get((row[0], row[1], row[2]))
            if other is not None and row[3:] != other[3:]:
                raise SourcePatchError(row[1], "heading_change")
        base_identities = {(row[0], row[1], row[2]) for row in base_headings}
        cur_identities = {(row[0], row[1], row[2]) for row in cur_headings}
        changed_identities = base_identities ^ cur_identities
        uid = next((identity[1] for identity in changed_identities), "sec")
        raise SourcePatchError(uid, "structural_change")

    base_uids = set(base)
    cur_uids = set(cur)
    for uid in sorted(base_uids & cur_uids):
        before = base[uid]
        after = cur[uid]
        if before.parent_uid != after.parent_uid or before.depth != after.depth:
            raise SourcePatchError(uid, "cross_parent_structural_change")

    # Nested content remains text-editable under P1a's ordinary anchor rules,
    # but its membership, order, and parentage are outside P1b's structure
    # surface.  Likewise, a top-level paragraph that owns a subtree is a fixed
    # barrier and may not be deleted or moved as a hidden multi-block unit.
    for uid in sorted(base_uids - cur_uids):
        before = base[uid]
        if before.depth or before.child_uids:
            raise SourcePatchError(uid, "nested_structural_change")
    for uid in sorted(cur_uids - base_uids):
        after = cur[uid]
        if after.depth or after.child_uids:
            raise SourcePatchError(uid, "nested_structural_change")
    paragraph_parents = {
        paragraph.parent_uid
        for paragraph in (*base.values(), *cur.values())
        if paragraph.depth > 0
    }
    for parent_uid in paragraph_parents:
        if base_children.get(parent_uid, ()) != cur_children.get(parent_uid, ()):
            raise SourcePatchError(parent_uid, "nested_structural_change")
    return base, base_children, cur, cur_children


def _validate_text_for_single_word_node(uid: str, text: str) -> None:
    if not text or text != text.strip():
        raise SourcePatchError(
            uid,
            "unsupported_edge_whitespace",
            "source-preserving text must be non-empty and cannot start or end "
            "with whitespace that would require changing w:t xml:space metadata",
        )
    blocker = source_replacement_text_blocker(text)
    if blocker is not None:
        raise SourcePatchError(uid, blocker)


def _try_parse_xml_part(payload: bytes):
    try:
        tree = etree.parse(BytesIO(payload), parser=_xml_parser())
    except (etree.XMLSyntaxError, ValueError):
        return None
    if tree.docinfo.doctype:
        return None
    return tree


def _resolve_internal_part_target(source_part: str, target: str | None) -> str | None:
    """Resolve one OPC relationship target without allowing URI ambiguity."""
    if not target:
        return None
    parsed = urllib.parse.urlsplit(target)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        return None
    decoded = urllib.parse.unquote(parsed.path)
    if not decoded or "\\" in decoded or "\x00" in decoded:
        return None
    if decoded.startswith("/"):
        resolved = posixpath.normpath(decoded.lstrip("/"))
    else:
        resolved = posixpath.normpath(
            posixpath.join(posixpath.dirname(source_part), decoded)
        )
    if resolved in {"", ".", ".."} or resolved.startswith("../"):
        return None
    return resolved


def _effective_content_type(content_types_root, part_name: str) -> str | None:
    overrides: list[str] = []
    for node in content_types_root.findall(_CT_OVERRIDE):
        raw_name = node.get("PartName")
        if not raw_name or not raw_name.startswith("/"):
            continue
        decoded = urllib.parse.unquote(raw_name.lstrip("/"))
        normalized = posixpath.normpath(decoded)
        if normalized == part_name:
            content_type = node.get("ContentType")
            if content_type:
                overrides.append(content_type.strip().casefold())
    if len(overrides) > 1:
        return None
    if overrides:
        return overrides[0]

    extension = part_name.rsplit(".", 1)[-1].casefold() if "." in part_name else ""
    defaults = [
        node.get("ContentType", "").strip().casefold()
        for node in content_types_root.findall(_CT_DEFAULT)
        if node.get("Extension", "").casefold() == extension
        and node.get("ContentType")
    ]
    return defaults[0] if len(defaults) == 1 else None


def _related_numbering_part(
    archive: zipfile.ZipFile,
) -> tuple[str, bytes, object] | None:
    """Return the one wired, correctly typed numbering part, if proven."""
    try:
        rels_tree = _try_parse_xml_part(archive.read(_DOCUMENT_RELS_PART))
        content_types_tree = _try_parse_xml_part(archive.read(_CONTENT_TYPES_PART))
    except (KeyError, RuntimeError, zipfile.BadZipFile):
        return None
    if (
        rels_tree is None
        or rels_tree.getroot().tag != _RELATIONSHIPS
        or content_types_tree is None
        or content_types_tree.getroot().tag != _CT_TYPES
    ):
        return None

    relationships = [
        node
        for node in rels_tree.getroot().findall(_RELATIONSHIP)
        if node.get("Type") in _NUMBERING_REL_TYPES
    ]
    if len(relationships) != 1:
        return None
    relationship = relationships[0]
    if relationship.get("TargetMode", "Internal") != "Internal":
        return None
    part_name = _resolve_internal_part_target(
        _DOCUMENT_PART, relationship.get("Target")
    )
    if part_name is None:
        return None
    if (
        _effective_content_type(content_types_tree.getroot(), part_name)
        not in _NUMBERING_CONTENT_TYPES
    ):
        return None
    try:
        return part_name, archive.read(part_name), content_types_tree.getroot()
    except (KeyError, RuntimeError, zipfile.BadZipFile):
        return None


def _numbering_usage_counts(
    archive: zipfile.ZipFile,
    *,
    numbering_part: str,
    content_types_root,
) -> dict[str, int] | None:
    """Count every explicit use of each numbering instance outside its part.

    A ``numId`` is document-wide state. Even an otherwise untouched paragraph
    in a table, SDT, header, style, or later body run can visibly renumber after
    a local insertion/deletion. Structural editing therefore requires the
    candidate island to own every explicit reference to its numbering instance.
    """
    counts: dict[str, int] = {}
    for info in archive.infolist():
        name = info.filename
        content_type = _effective_content_type(content_types_root, name)
        is_wordprocessing_xml = (
            content_type is not None
            and content_type.endswith("+xml")
            and (
                content_type.startswith(
                    "application/vnd.openxmlformats-officedocument."
                    "wordprocessingml."
                )
                or content_type.startswith("application/vnd.ms-word.")
            )
        )
        if (
            info.is_dir()
            or name == numbering_part
            or (name != _DOCUMENT_PART and not is_wordprocessing_xml)
        ):
            continue
        try:
            tree = _try_parse_xml_part(archive.read(info))
        except (RuntimeError, zipfile.BadZipFile):
            return None
        if tree is None:
            return None
        for num_pr in tree.getroot().iter(_W_NUMPR):
            for num_id_node in num_pr.findall(_W_NUMID):
                value = num_id_node.get(_W_VAL)
                try:
                    number = int(value) if value is not None else 0
                except ValueError:
                    continue
                if number > 0:
                    key = str(number)
                    counts[key] = counts.get(key, 0) + 1
    return counts


def _numbering_context(
    source_bytes: bytes,
) -> tuple[frozenset[tuple[str, str]], dict[str, int]]:
    """Resolve wired auto-numbering levels and document-wide instance uses."""
    try:
        archive = zipfile.ZipFile(BytesIO(source_bytes), "r")
    except (RuntimeError, zipfile.BadZipFile):
        return frozenset(), {}
    try:
        related = _related_numbering_part(archive)
        if related is None:
            return frozenset(), {}
        numbering_part, numbering_xml, content_types_root = related
        tree = _try_parse_xml_part(numbering_xml)
        usage_counts = _numbering_usage_counts(
            archive,
            numbering_part=numbering_part,
            content_types_root=content_types_root,
        )
    finally:
        archive.close()
    if (
        tree is None
        or tree.getroot().tag != _W_NUMBERING
        or usage_counts is None
    ):
        return frozenset(), {}

    def canonical_nonnegative_integer(
        value: str | None,
        maximum: int | None = None,
    ) -> str | None:
        if value is None:
            return None
        try:
            number = int(value)
        except ValueError:
            return None
        if number < 0 or (maximum is not None and number > maximum):
            return None
        return str(number)

    def valid_decimal_leaf(node) -> bool:
        if (
            set(node.attrib) != {_W_VAL}
            or _meaningful_children(node)
            or (node.text or "").strip()
        ):
            return False
        return canonical_nonnegative_integer(node.get(_W_VAL), 2_147_483_647) is not None

    def usable_level(level) -> str | None:
        ilvl = canonical_nonnegative_integer(level.get(_W_ILVL), 8)
        starts = level.findall(_W_START)
        formats = level.findall(_W_NUMFMT)
        level_texts = level.findall(_W_LVLTEXT)
        if (
            ilvl is None
            or len(starts) > 1
            or (starts and not valid_decimal_leaf(starts[0]))
            or len(formats) != 1
            or len(level_texts) != 1
            or set(formats[0].attrib) != {_W_VAL}
            or set(level_texts[0].attrib) != {_W_VAL}
            or _meaningful_children(formats[0])
            or _meaningful_children(level_texts[0])
        ):
            return None
        number_format = formats[0].get(_W_VAL, "").strip()
        level_text = level_texts[0].get(_W_VAL, "")
        if number_format not in _AUTOMATIC_NUMBER_FORMATS or not level_text:
            return None
        placeholders = re.findall(r"%([1-9])", level_text)
        if "%" in re.sub(r"%[1-9]", "", level_text):
            return None
        if number_format == "bullet":
            if placeholders:
                return None
        elif str(int(ilvl) + 1) not in placeholders:
            return None
        return ilvl

    abstract_levels: dict[str, set[str]] = {}
    seen_abstract_ids: set[str] = set()
    duplicate_abstract_ids: set[str] = set()
    for abstract in tree.getroot().findall(_W_ABSTRACT_NUM):
        abstract_id = canonical_nonnegative_integer(
            abstract.get(_W_ABSTRACT_NUM_ID)
        )
        if abstract_id is None:
            continue
        if abstract_id in seen_abstract_ids:
            duplicate_abstract_ids.add(abstract_id)
            continue
        seen_abstract_ids.add(abstract_id)
        if (
            abstract.find(_W_NUMSTYLE_LINK) is not None
            or abstract.find(_W_STYLE_LINK) is not None
        ):
            continue
        levels: set[str] = set()
        seen_raw_levels: set[str] = set()
        duplicate_level = False
        for level in abstract.findall(_W_LVL):
            ilvl = usable_level(level)
            raw_ilvl = canonical_nonnegative_integer(level.get(_W_ILVL), 8)
            if raw_ilvl is not None and raw_ilvl in seen_raw_levels:
                duplicate_level = True
            if raw_ilvl is not None:
                seen_raw_levels.add(raw_ilvl)
            if ilvl is not None:
                levels.add(ilvl)
        if not duplicate_level:
            abstract_levels[abstract_id] = levels
    for duplicate_id in duplicate_abstract_ids:
        abstract_levels.pop(duplicate_id, None)

    resolved: set[tuple[str, str]] = set()
    numbering_instances: dict[str, object] = {}
    duplicate_num_ids: set[str] = set()
    for numbering_instance in tree.getroot().findall(_W_NUM):
        num_id = canonical_nonnegative_integer(numbering_instance.get(_W_NUMID))
        if num_id is None or int(num_id) <= 0:
            continue
        if num_id in numbering_instances:
            duplicate_num_ids.add(num_id)
            continue
        numbering_instances[num_id] = numbering_instance

    for num_id, numbering_instance in numbering_instances.items():
        if num_id in duplicate_num_ids:
            continue
        instance_children = _meaningful_children(numbering_instance)
        if any(
            child.tag
            not in {_W_ABSTRACT_NUM_ID, f"{{{_W_NS}}}lvlOverride"}
            for child in instance_children
        ):
            continue
        abstract_refs = numbering_instance.findall(_W_ABSTRACT_NUM_ID)
        if (
            len(abstract_refs) != 1
            or set(abstract_refs[0].attrib) != {_W_VAL}
            or _meaningful_children(abstract_refs[0])
        ):
            continue
        abstract_id = canonical_nonnegative_integer(abstract_refs[0].get(_W_VAL))
        if abstract_id not in abstract_levels:
            continue
        usable = set(abstract_levels[abstract_id])
        seen_overrides: set[str] = set()
        malformed_override = False
        for override in numbering_instance.findall(f"{{{_W_NS}}}lvlOverride"):
            override_ilvl = canonical_nonnegative_integer(
                override.get(_W_ILVL), 8
            )
            override_children = _meaningful_children(override)
            override_tags = [child.tag for child in override_children]
            if (
                override_ilvl is None
                or override_ilvl in seen_overrides
                or set(override.attrib) != {_W_ILVL}
                or override_tags
                not in (
                    [_W_START_OVERRIDE],
                    [_W_LVL],
                    [_W_START_OVERRIDE, _W_LVL],
                )
            ):
                malformed_override = True
                break
            seen_overrides.add(override_ilvl)
            start_overrides = override.findall(_W_START_OVERRIDE)
            if start_overrides and not valid_decimal_leaf(start_overrides[0]):
                malformed_override = True
                break
            local_levels = override.findall(_W_LVL)
            if local_levels:
                local_ilvl = usable_level(local_levels[0])
                if local_ilvl != override_ilvl:
                    malformed_override = True
                    break
                usable.add(override_ilvl)
        if malformed_override:
            continue
        resolved.update((num_id, ilvl) for ilvl in usable)
    return frozenset(resolved), usage_counts


def _direct_numbering_signature(
    element,
    defined_levels: frozenset[tuple[str, str]],
) -> tuple[str, str, str] | None:
    """Return an exact safe direct-numPr signature, never a style guess."""
    p_prs = element.findall(_W_PPR)
    if len(p_prs) != 1:
        return None
    num_prs = p_prs[0].findall(_W_NUMPR)
    if len(num_prs) != 1:
        return None
    num_pr = num_prs[0]
    children = _meaningful_children(num_pr)
    if [child.tag for child in children] != [_W_ILVL, _W_NUMID]:
        return None
    ilvl_node, num_id_node = children
    if set(ilvl_node.attrib) != {_W_VAL} or set(num_id_node.attrib) != {_W_VAL}:
        return None
    if _meaningful_children(ilvl_node) or _meaningful_children(num_id_node):
        return None
    ilvl = ilvl_node.get(_W_VAL, "")
    num_id = num_id_node.get(_W_VAL, "")
    try:
        ilvl_value = int(ilvl)
        num_id_value = int(num_id)
    except ValueError:
        return None
    if not 0 <= ilvl_value <= 8 or num_id_value <= 0:
        return None
    canonical_num_id = str(num_id_value)
    canonical_ilvl = str(ilvl_value)
    if (canonical_num_id, canonical_ilvl) not in defined_levels:
        return None
    return canonical_num_id, canonical_ilvl, canonical_element_sha256(num_pr)


def _property_signatures(element) -> tuple[str, str]:
    """Canonical local formatting signatures used to prove clone choice."""
    p_pr = element.find(_W_PPR)
    run = element.find(_W_R)
    r_pr = run.find(_W_RPR) if run is not None else None
    return (
        canonical_element_sha256(p_pr) if p_pr is not None else "",
        canonical_element_sha256(r_pr) if r_pr is not None else "",
    )


def _eligible_numbered_member(
    paragraph: _ProjectedParagraph,
    binding: SourceParagraphBinding | None,
    body_inventory: tuple[SourceBodyInventoryItem, ...],
) -> _NumberedMember | None:
    if (
        paragraph.depth != 0
        or paragraph.child_uids
        or binding is None
        or not binding.editable
        or binding.text_span is None
        or binding.text_span.prefix
        or binding.text_span.suffix
        or not 0 <= binding.body_child_index < len(body_inventory)
    ):
        return None
    item = body_inventory[binding.body_child_index]
    if item.expanded_name != _W_P or item.direct_numbering is None:
        return None
    num_id, ilvl, signature = item.direct_numbering
    return _NumberedMember(
        binding=binding,
        num_id=num_id,
        ilvl=ilvl,
        signature=signature,
        ppr_signature=item.ppr_signature,
        rpr_signature=item.rpr_signature,
    )


def build_source_patch_context(
    *,
    source_bytes: bytes,
    source_map: SourceBodyMap,
    baseline: SpecSection,
) -> SourcePatchContext:
    """Index and bind one immutable source package exactly once."""
    if not isinstance(source_bytes, bytes):
        raise SourcePatchError(
            "source",
            "source_unavailable",
            "the exact imported DOCX bytes are unavailable",
        )
    try:
        package_info = inspect_docx_package(source_bytes)
    except (SourcePackageError, TypeError, ValueError) as exc:
        raise SourcePatchError("source", "unsafe_package", str(exc)) from exc
    if hashlib.sha256(source_bytes).hexdigest() != source_map.source_sha256:
        raise SourcePatchError(
            "source",
            "source_hash_mismatch",
            "the retained bytes do not match the imported source map",
        )
    if (
        semantic_body_projection_sha256(baseline)
        != source_map.baseline_projection_sha256
    ):
        raise SourcePatchError(
            "source",
            "baseline_mismatch",
            "the semantic master baseline no longer matches the source map",
        )

    document_xml, package_inventory = _read_document_xml_and_inventory(
        source_bytes
    )
    if hashlib.sha256(document_xml).hexdigest() != source_map.document_xml_sha256:
        raise SourcePatchError(
            "source",
            "document_hash_mismatch",
            "word/document.xml no longer matches its import-time identity",
        )
    tree, body = _parse_document_xml(document_xml)
    children = _meaningful_children(body)
    if len(children) != source_map.body_child_count:
        raise SourcePatchError(
            "source", "body_anchor_mismatch", "the source body child count changed"
        )
    for block in source_map.body_blocks:
        if not 0 <= block.body_child_index < len(children):
            raise SourcePatchError(
                "source", "body_anchor_mismatch", "a source body anchor is out of range"
            )
        element = children[block.body_child_index]
        if (
            element.tag.rsplit("}", 1)[-1] != block.tag
            or canonical_element_sha256(element) != block.element_c14n_sha256
        ):
            raise SourcePatchError(
                "source",
                "body_anchor_mismatch",
                f"body child {block.body_child_index} no longer matches its anchor",
            )

    baseline_paragraph_uids = {
        row[1]
        for row in semantic_body_projection(baseline)
        if row[0] == "paragraph"
    }
    if set(source_map.bindings) != baseline_paragraph_uids:
        raise SourcePatchError(
            "source",
            "source_map_mismatch",
            "the paragraph bindings do not match the imported baseline",
        )
    baseline_text_by_uid = {
        row[1]: row[3]
        for row in semantic_body_projection(baseline)
        if row[0] == "paragraph"
    }
    for uid, binding in source_map.bindings.items():
        if not 0 <= binding.body_child_index < len(children):
            raise SourcePatchError(uid, "body_anchor_mismatch")
        element = children[binding.body_child_index]
        if canonical_element_sha256(element) != binding.element_c14n_sha256:
            raise SourcePatchError(uid, "body_anchor_mismatch")
        if binding.baseline_text != baseline_text_by_uid[uid]:
            raise SourcePatchError(uid, "baseline_mismatch")
        if binding.emits_from_source:
            expected = bind_source_paragraph(
                uid=uid,
                body_child_index=binding.body_child_index,
                element=element,
                source_visible_text=binding.source_visible_text,
                baseline_text=binding.baseline_text,
            )
            if expected != binding:
                raise SourcePatchError(
                    uid,
                    "source_map_mismatch",
                    "the paragraph binding does not match its source XML",
                )
        elif (
            element.tag.rsplit("}", 1)[-1] != "tbl"
            or binding.text_span is not None
            or binding.para_id
            or binding.blockers != ("table_projection",)
        ):
            raise SourcePatchError(
                uid,
                "source_map_mismatch",
                "the opaque projection binding is not a preserved table row",
            )
    try:
        actual_global_blockers = detect_global_source_blockers(source_bytes)
    except (TypeError, ValueError) as exc:
        raise SourcePatchError(
            "source",
            "unsafe_package",
            "source mutation blockers could not be verified",
        ) from exc
    if actual_global_blockers != source_map.global_blockers:
        raise SourcePatchError(
            "source",
            "source_map_mismatch",
            "persisted mutation blockers do not match the source package",
        )

    numbering_levels, numbering_usage_counts = _numbering_context(source_bytes)
    body_inventory: list[SourceBodyInventoryItem] = []
    previous = None
    for body_child_index, element in enumerate(children):
        canonical = canonical_element_bytes(element)
        numbering = _direct_numbering_signature(element, numbering_levels)
        ppr_signature, rpr_signature = _property_signatures(element)
        body_inventory.append(
            SourceBodyInventoryItem(
                body_child_index=body_child_index,
                expanded_name=element.tag,
                element_c14n_sha256=hashlib.sha256(canonical).hexdigest(),
                element_c14n=canonical,
                immediately_follows_previous=(
                    previous is not None and previous.getnext() is element
                ),
                direct_numbering=numbering,
                ppr_signature=ppr_signature,
                rpr_signature=rpr_signature,
            )
        )
        previous = element

    runtime_mutation_issues: list[SourcePatchIssue] = []
    xml_index: SourceXmlIndex | None
    try:
        xml_index = build_source_xml_index(document_xml, validated_tree=tree)
    except XmlLexicalError as exc:
        xml_index = None
        runtime_mutation_issues.append(
            SourcePatchIssue("source", exc.blocker, exc.detail)
        )

    raw_zip_archive: RawZipArchive | None
    if package_info.integrity_ambiguous:
        raw_zip_archive = None
        runtime_mutation_issues.append(
            SourcePatchIssue(
                "source",
                "unsupported_raw_zip_layout",
                "a retained ZIP member failed decompression or CRC validation",
            )
        )
    else:
        try:
            raw_zip_archive = parse_raw_zip_archive(
                source_bytes,
                mutable_member=_DOCUMENT_PART,
            )
        except RawZipError as exc:
            raw_zip_archive = None
            runtime_mutation_issues.append(
                SourcePatchIssue("source", exc.blocker, exc.detail)
            )

    paragraph_templates: dict[int, SourceParagraphTemplate] = {}
    if xml_index is not None:
        for body_child_index, element in enumerate(children):
            if element.tag != _W_P:
                continue
            try:
                paragraph_templates[body_child_index] = (
                    _extract_paragraph_template(
                        document_xml=document_xml,
                        index=xml_index,
                        body_child_index=body_child_index,
                        template_element=element,
                        uid="source",
                    )
                )
            except SourcePatchError:
                # A paragraph can remain movable verbatim even when its local
                # formatting is too complex to stamp onto a new paragraph.
                continue

    root = tree.getroot()
    non_body = [
        child
        for child in _meaningful_children(root)
        if child is not body
    ]
    return SourcePatchContext(
        source_sha256=source_map.source_sha256,
        source_bytes=source_bytes,
        source_map=source_map,
        baseline_projection_sha256=source_map.baseline_projection_sha256,
        document_xml=document_xml,
        document_xml_sha256=source_map.document_xml_sha256,
        xml_index=xml_index,
        global_blockers=actual_global_blockers,
        runtime_mutation_issues=tuple(runtime_mutation_issues),
        numbering_levels=numbering_levels,
        numbering_usage_counts=numbering_usage_counts,
        body_inventory=tuple(body_inventory),
        paragraph_templates=paragraph_templates,
        package_inventory=package_inventory,
        document_tag=root.tag,
        document_attributes=tuple(sorted(root.attrib.items())),
        document_namespace_bindings=tuple(
            sorted(
                ((prefix or "", uri) for prefix, uri in root.nsmap.items()),
                key=lambda item: item[0],
            )
        ),
        body_attributes=tuple(sorted(body.attrib.items())),
        body_has_non_whitespace_direct_character_data=(
            _has_non_whitespace_direct_character_data(body)
        ),
        non_body_c14n_sha256=tuple(
            canonical_element_sha256(child) for child in non_body
        ),
        raw_zip_archive=raw_zip_archive,
    )


def _context_for_inputs(
    *,
    source_bytes: bytes,
    source_map: SourceBodyMap,
    baseline: SpecSection,
    context: SourcePatchContext | None,
) -> SourcePatchContext:
    """Return a bound context, rejecting stale supplied cache state."""
    if context is None:
        return build_source_patch_context(
            source_bytes=source_bytes,
            source_map=source_map,
            baseline=baseline,
        )
    if not isinstance(context, SourcePatchContext):
        raise SourcePatchError(
            "source",
            "source_map_mismatch",
            "the supplied source context has an invalid type",
        )
    actual_source_sha256 = (
        context.source_sha256
        if source_bytes is context.source_bytes
        else hashlib.sha256(source_bytes).hexdigest()
    )
    if (
        actual_source_sha256 != source_map.source_sha256
        or context.source_sha256 != actual_source_sha256
    ):
        raise SourcePatchError(
            "source",
            "source_hash_mismatch",
            "the retained bytes do not match the cached source context",
        )
    if source_map != context.source_map:
        raise SourcePatchError(
            "source",
            "source_map_mismatch",
            "the source map does not match the cached source context",
        )
    baseline_sha256 = semantic_body_projection_sha256(baseline)
    if (
        baseline_sha256 != source_map.baseline_projection_sha256
        or context.baseline_projection_sha256 != baseline_sha256
    ):
        raise SourcePatchError(
            "source",
            "baseline_mismatch",
            "the semantic master baseline does not match the cached source context",
        )
    if context.document_xml_sha256 != source_map.document_xml_sha256:
        raise SourcePatchError(
            "source",
            "document_hash_mismatch",
            "word/document.xml does not match the cached source context",
        )
    if context.global_blockers != source_map.global_blockers:
        raise SourcePatchError(
            "source",
            "source_map_mismatch",
            "cached mutation blockers do not match the source map",
        )
    if len(context.body_inventory) != source_map.body_child_count:
        raise SourcePatchError("source", "body_anchor_mismatch")
    for block, item in zip(source_map.body_blocks, context.body_inventory):
        if (
            block.body_child_index != item.body_child_index
            or block.tag != item.expanded_name.rsplit("}", 1)[-1]
            or block.element_c14n_sha256 != item.element_c14n_sha256
        ):
            raise SourcePatchError("source", "body_anchor_mismatch")
    return context


def validate_source_map_identity(
    *,
    source_bytes: bytes,
    source_map: SourceBodyMap,
    baseline: SpecSection,
    context: SourcePatchContext | None = None,
) -> None:
    """Public read-only identity gate for project-container restoration.

    Mutation blockers such as signatures, protection, and pending revisions
    are intentionally not errors here: a valid project may retain and return
    such a document byte-for-byte even though mutation remains blocked.
    """
    _context_for_inputs(
        source_bytes=source_bytes,
        source_map=source_map,
        baseline=baseline,
        context=context,
    )


def _structural_member_blocker(
    uid: str,
    baseline: dict[str, _ProjectedParagraph],
    source_map: SourceBodyMap,
) -> str:
    paragraph = baseline.get(uid)
    if paragraph is not None and (paragraph.depth or paragraph.child_uids):
        return "nested_structural_change"
    binding = source_map.bindings.get(uid)
    if binding is None:
        return "unmapped_paragraph"
    if binding.blockers:
        return binding.blockers[0]
    if binding.text_span is not None and (
        binding.text_span.prefix or binding.text_span.suffix
    ):
        return "manual_label_structural_change"
    return "automatic_numbering_required"


def _build_numbered_islands(
    *,
    context: SourcePatchContext,
    source_map: SourceBodyMap,
    baseline: dict[str, _ProjectedParagraph],
    base_children: dict[str, tuple[str, ...]],
) -> tuple[
    dict[str, _NumberedIsland],
    dict[str, _NumberedIsland],
    dict[str, tuple[_NumberedIsland, ...]],
    dict[str, str],
]:
    eligible = {
        uid: member
        for uid, paragraph in baseline.items()
        if (
            member := _eligible_numbered_member(
                paragraph,
                source_map.bindings.get(uid),
                context.body_inventory,
            )
        )
        is not None
    }
    by_key: dict[str, _NumberedIsland] = {}
    by_uid: dict[str, _NumberedIsland] = {}
    by_article: dict[str, list[_NumberedIsland]] = {}
    diagnostics: dict[str, str] = {}

    for article_uid, sibling_uids in base_children.items():
        # Only article-owned (depth-zero) sequences participate. Paragraph
        # parent sequences were already frozen by _validate_fixed_projection.
        if not sibling_uids or any(
            baseline[uid].depth != 0 for uid in sibling_uids if uid in baseline
        ):
            continue
        pending: list[_NumberedMember] = []
        raw_islands: list[tuple[_NumberedMember, ...]] = []

        def finish() -> None:
            nonlocal pending
            if not pending:
                return
            # One numbered paragraph is not evidence of a stable auto-
            # increment run and is too easy to confuse with a boundary style.
            # P1b requires an actual contiguous baseline sequence.
            if len(pending) < 2:
                pending = []
                return
            raw_islands.append(tuple(pending))
            pending = []

        for uid in sibling_uids:
            member = eligible.get(uid)
            if member is None:
                finish()
                continue
            if pending:
                previous = pending[-1]
                current_item = context.body_inventory[
                    member.binding.body_child_index
                ]
                contiguous = (
                    previous.binding.body_child_index + 1
                    == member.binding.body_child_index
                    and current_item.immediately_follows_previous
                )
                if not contiguous or previous.signature != member.signature:
                    blocker = (
                        "noncontiguous_structural_island"
                        if not contiguous and previous.signature == member.signature
                        else "mixed_numbering_island"
                    )
                    diagnostics.setdefault(previous.binding.uid, blocker)
                    diagnostics.setdefault(member.binding.uid, blocker)
                    finish()
            pending.append(member)
        finish()

        isolated_islands: list[tuple[_NumberedMember, ...]] = []
        for raw_island in raw_islands:
            num_id = raw_island[0].num_id
            if context.numbering_usage_counts.get(num_id, 0) != len(raw_island):
                for numbered_member in raw_island:
                    diagnostics.setdefault(
                        numbered_member.binding.uid,
                        "numbering_instance_not_isolated",
                    )
                continue
            isolated_islands.append(raw_island)
        raw_islands = isolated_islands

        registered_uids = {
            member.binding.uid
            for raw_island in raw_islands
            for member in raw_island
        }
        segment = 0
        raw_by_first = {
            raw_island[0].binding.uid: raw_island
            for raw_island in raw_islands
        }
        for uid in sibling_uids:
            raw_island = raw_by_first.get(uid)
            if raw_island is not None:
                island = _NumberedIsland(
                    key=uid,
                    article_uid=article_uid,
                    segment=segment,
                    members=raw_island,
                )
                by_key[island.key] = island
                by_article.setdefault(article_uid, []).append(island)
                for numbered_member in raw_island:
                    by_uid[numbered_member.binding.uid] = island
            elif uid not in registered_uids:
                segment += 1

    return (
        by_key,
        by_uid,
        {article: tuple(islands) for article, islands in by_article.items()},
        diagnostics,
    )


def _plan_projection_changes(
    *,
    context: SourcePatchContext,
    source_map: SourceBodyMap,
    baseline_section: SpecSection,
    current_section: SpecSection,
    bound_inputs: _BoundInputs | None = None,
) -> _PatchPlan:
    baseline, base_children, current, current_children = _validate_fixed_projection(
        baseline_section,
        current_section,
        bound_inputs=bound_inputs,
    )
    if bound_inputs is not None:
        # Islands are a function of (context, source_map, baseline) only.
        _islands_by_key, island_by_uid, islands_by_article, diagnostics = (
            bound_inputs.islands
        )
    else:
        _islands_by_key, island_by_uid, islands_by_article, diagnostics = _build_numbered_islands(
            context=context,
            source_map=source_map,
            baseline=baseline,
            base_children=base_children,
        )

    desired_by_island: dict[str, list[_DesiredParagraph]] = {
        island.key: []
        for islands in islands_by_article.values()
        for island in islands
    }
    added_uids = set(current) - set(baseline)

    def article_diagnostic(article_uid: str, uid: str) -> str | None:
        if uid in diagnostics:
            return diagnostics[uid]
        return next(
            (
                diagnostics[item]
                for item in base_children.get(article_uid, ())
                if item in diagnostics
            ),
            None,
        )

    article_uids = {
        paragraph.parent_uid
        for paragraph in (*baseline.values(), *current.values())
        if paragraph.depth == 0
    }
    for article_uid in sorted(article_uids):
        before = base_children.get(article_uid, ())
        after = current_children.get(article_uid, ())
        fixed_before = [uid for uid in before if uid not in island_by_uid]
        fixed_after = [uid for uid in after if uid in baseline and uid not in island_by_uid]
        if fixed_before != fixed_after:
            for uid in fixed_before:
                if uid not in fixed_after:
                    raise SourcePatchError(
                        uid,
                        article_diagnostic(article_uid, uid)
                        or _structural_member_blocker(uid, baseline, source_map),
                    )
            raise SourcePatchError(
                fixed_after[0] if fixed_after else article_uid,
                article_diagnostic(article_uid, article_uid)
                or "unsafe_structural_island",
            )

        island_order_by_segment: dict[int, list[str]] = {}
        for island in islands_by_article.get(article_uid, ()):
            island_order_by_segment.setdefault(island.segment, []).append(island.key)

        fixed_set = set(fixed_before)
        fixed_cursor = 0
        segment = 0
        segment_items: dict[int, list[str]] = {}
        for uid in after:
            if uid in fixed_set:
                if fixed_cursor >= len(fixed_before) or uid != fixed_before[fixed_cursor]:
                    raise SourcePatchError(uid, "unsafe_structural_island")
                fixed_cursor += 1
                segment += 1
                continue
            segment_items.setdefault(segment, []).append(uid)

        for segment_id, items in segment_items.items():
            available = island_order_by_segment.get(segment_id, [])
            known_keys: list[str | None] = []
            for uid in items:
                if uid in added_uids:
                    known_keys.append(None)
                    continue
                island = island_by_uid.get(uid)
                if island is None:
                    raise SourcePatchError(
                        uid,
                        _structural_member_blocker(uid, baseline, source_map),
                    )
                if island.segment != segment_id or island.article_uid != article_uid:
                    raise SourcePatchError(
                        uid,
                        article_diagnostic(article_uid, uid) or "cross_island_move",
                    )
                known_keys.append(island.key)

            known_non_null = [key for key in known_keys if key is not None]
            compressed = [
                key
                for index, key in enumerate(known_non_null)
                if index == 0 or key != known_non_null[index - 1]
            ]
            available_positions = {key: index for index, key in enumerate(available)}
            numeric_order = [available_positions.get(key, -1) for key in compressed]
            if (
                any(index < 0 for index in numeric_order)
                or numeric_order != sorted(set(numeric_order))
            ):
                uid = next(
                    (item for item, key in zip(items, known_keys) if key is not None),
                    article_uid,
                )
                raise SourcePatchError(
                    uid,
                    article_diagnostic(article_uid, uid) or "cross_island_move",
                )

            for index, uid in enumerate(items):
                key = known_keys[index]
                if key is None:
                    left = next(
                        (known_keys[pos] for pos in range(index - 1, -1, -1) if known_keys[pos]),
                        None,
                    )
                    right = next(
                        (
                            known_keys[pos]
                            for pos in range(index + 1, len(known_keys))
                            if known_keys[pos]
                        ),
                        None,
                    )
                    if left is not None and right is not None and left == right:
                        key = left
                    elif left is not None and right is None and available and left == available[-1]:
                        key = left
                    elif right is not None and left is None and available and right == available[0]:
                        key = right
                    else:
                        raise SourcePatchError(uid, "ambiguous_structural_insert")
                    _validate_text_for_single_word_node(uid, current[uid].text)
                    # The island is filled below after clone-template proof.
                    desired_by_island[key].append(
                        _DesiredParagraph(uid=uid, text=current[uid].text, binding=None)
                    )
                else:
                    binding = source_map.bindings[uid]
                    desired_by_island[key].append(
                        _DesiredParagraph(
                            uid=uid,
                            text=current[uid].text,
                            binding=binding,
                        )
                    )

    island_patches: list[_IslandPatch] = []
    structural_uids: list[str] = []
    for islands in islands_by_article.values():
        for island in islands:
            desired = desired_by_island[island.key]
            original_uids = tuple(member.binding.uid for member in island.members)
            desired_uids = tuple(item.uid for item in desired)
            if original_uids == desired_uids:
                continue
            if any(item.binding is None for item in desired):
                if (
                    len({member.ppr_signature for member in island.members}) != 1
                    or len({member.rpr_signature for member in island.members}) != 1
                ):
                    new_uid = next(item.uid for item in desired if item.binding is None)
                    raise SourcePatchError(new_uid, "ambiguous_structural_template")
                template = island.members[0].binding
                desired = [
                    (
                        _DesiredParagraph(
                            uid=item.uid,
                            text=item.text,
                            binding=None,
                            template=template,
                        )
                        if item.binding is None
                        else item
                    )
                    for item in desired
                ]
            island_patches.append(_IslandPatch(island=island, desired=tuple(desired)))
            structural_uids.extend((*original_uids, *desired_uids))

    # A current paragraph absent from the baseline must have been assigned to
    # exactly one numbered island above.  This catches additions to an empty
    # article or between only opaque/manual content.
    assigned_additions = {
        item.uid
        for desired in desired_by_island.values()
        for item in desired
        if item.binding is None
    }
    unassigned = sorted(added_uids - assigned_additions)
    if unassigned:
        raise SourcePatchError(unassigned[0], "unsafe_structural_island")

    if (
        island_patches
        and context.body_has_non_whitespace_direct_character_data
    ):
        raise SourcePatchError(
            island_patches[0].island.key,
            "unsafe_structural_island",
            "the source body contains non-whitespace direct character content",
        )

    text_patches: list[_TextPatch] = []
    current_uids = [
        row[1]
        for row in semantic_body_projection(current_section)
        if row[0] == "paragraph"
    ]
    for uid in current_uids:
        if uid not in baseline or uid in added_uids:
            continue
        if baseline[uid].text == current[uid].text:
            continue
        binding = source_map.bindings.get(uid)
        if binding is None:
            raise SourcePatchError(uid, "unmapped_paragraph")
        if not binding.editable:
            raise SourcePatchError(
                uid,
                binding.blockers[0] if binding.blockers else "unmapped_paragraph",
            )
        _validate_text_for_single_word_node(uid, current[uid].text)
        text_patches.append(_TextPatch(binding=binding, new_text=current[uid].text))

    return _PatchPlan(
        text_patches=tuple(text_patches),
        island_patches=tuple(island_patches),
        structural_changed_uids=tuple(dict.fromkeys(structural_uids)),
    )


def _validate_source_and_plan(
    *,
    source_bytes: bytes,
    source_map: SourceBodyMap,
    baseline: SpecSection,
    current: SpecSection,
    context: SourcePatchContext | None = None,
    bound_inputs: _BoundInputs | None = None,
) -> _ValidatedPatch:
    """Prove one complete candidate document against the immutable source.

    ``bound_inputs`` is the capability sweep's re-entry path: identity binding
    and the baseline projection are properties of ``(source_bytes, source_map,
    baseline)``, which the sweep holds fixed across every probe. Re-deriving
    them per probe made the sweep quadratic in body size (each probe re-walked
    the baseline tree, re-hashed its projection, compared the whole source map,
    and re-checked every body anchor). The caller binds once and passes the
    result; every other entry point still validates from scratch.
    """
    if bound_inputs is not None:
        bound_context = bound_inputs.context
        baseline_projection = bound_inputs.baseline_projection
    else:
        bound_context = _context_for_inputs(
            source_bytes=source_bytes,
            source_map=source_map,
            baseline=baseline,
            context=context,
        )
        baseline_projection = semantic_body_projection(baseline)

    current_projection = semantic_body_projection(current)
    mutation_blocker = (
        bound_context.global_blockers[0]
        if bound_context.global_blockers
        else (
            bound_context.runtime_mutation_issues[0].blocker
            if bound_context.runtime_mutation_issues
            else None
        )
    )
    if baseline_projection != current_projection and mutation_blocker is not None:
        changed_uid = "source"
        for index in range(max(len(baseline_projection), len(current_projection))):
            before = (
                baseline_projection[index]
                if index < len(baseline_projection)
                else None
            )
            after = (
                current_projection[index]
                if index < len(current_projection)
                else None
            )
            if before != after:
                changed_uid = (after or before or ("", "source"))[1]
                break
        detail = next(
            (
                issue.message
                for issue in bound_context.runtime_mutation_issues
                if issue.blocker == mutation_blocker
            ),
            None,
        )
        raise SourcePatchError(changed_uid, mutation_blocker, detail)

    plan = _plan_projection_changes(
        context=bound_context,
        source_map=source_map,
        baseline_section=baseline,
        current_section=current,
        bound_inputs=bound_inputs,
    )

    # Exact semantic no-ops return the original bytes even for signed,
    # protected, or revision-bearing sources.  Those features only block an
    # actual mutation; pass-through cannot invalidate or reinterpret them.
    if not plan.no_op:
        if bound_context.global_blockers:
            raise SourcePatchError(
                plan.changed_uids[0] if plan.changed_uids else "source",
                bound_context.global_blockers[0],
            )
        if bound_context.runtime_mutation_issues:
            issue = bound_context.runtime_mutation_issues[0]
            raise SourcePatchError(
                plan.changed_uids[0] if plan.changed_uids else "source",
                issue.blocker,
                issue.message,
            )

    lexically_patched_xml: bytes | None = None
    prepared_output: bytes | None = None
    if not plan.no_op:
        # Lexical synthesis is part of the final-state gate, not merely an
        # export implementation detail.  A namespace or raw-template ambiguity
        # therefore rejects model, manual, QC, and restored-history candidates
        # before any session state can be committed.
        lexically_patched_xml = _lexically_patch_document(
            context=bound_context,
            plan=plan,
        )
        # The raw package rebuild is also part of the final-state gate.  This
        # proves the candidate still fits the deliberately narrow ZIP32 layout
        # before a manual, model, QC, or restored-history change is committed.
        # Reuse the audited bytes during export so validation and export cannot
        # diverge or perform the rebuild twice.
        try:
            prepared_output = replace_document_xml_raw(
                source_bytes,
                lexically_patched_xml,
                source_archive=bound_context.raw_zip_archive,
            )
        except RawZipError as exc:
            raise SourcePatchError(
                plan.changed_uids[0] if plan.changed_uids else "source",
                "output_validation_failed",
                f"the raw ZIP rebuild preflight failed: {exc.detail}",
            ) from exc

    return _ValidatedPatch(
        plan=plan,
        context=bound_context,
        lexically_patched_xml=lexically_patched_xml,
        prepared_output=prepared_output,
    )


def validate_source_transition(
    *,
    source_bytes: bytes,
    source_map: SourceBodyMap,
    baseline: SpecSection,
    current: SpecSection,
    context: SourcePatchContext | None = None,
) -> None:
    """Raise the exact fail-closed blocker for a proposed final document.

    Session/model integration should validate a transactionally-built
    candidate with this gate before committing it.  In particular, callers
    must not authorize structural operations from their action names alone.
    """
    _validate_source_and_plan(
        source_bytes=source_bytes,
        source_map=source_map,
        baseline=baseline,
        current=current,
        context=context,
    )


def source_patch_readiness(
    *,
    source_bytes: bytes | None,
    source_map: SourceBodyMap | None,
    baseline: SpecSection,
    current: SpecSection,
    context: SourcePatchContext | None = None,
) -> SourcePatchReadiness:
    """Non-throwing readiness report for API/UI integration."""
    if source_bytes is None or source_map is None:
        message = "the exact imported DOCX and source map are unavailable"
        issue = SourcePatchIssue("source", "source_unavailable", message)
        return SourcePatchReadiness(False, False, blockers=(issue,))
    try:
        validated = _validate_source_and_plan(
            source_bytes=source_bytes,
            source_map=source_map,
            baseline=baseline,
            current=current,
            context=context,
        )
    except SourcePatchError as exc:
        issue = SourcePatchIssue(exc.uid, exc.blocker, exc.detail)
        return SourcePatchReadiness(False, False, blockers=(issue,))
    return SourcePatchReadiness(
        True,
        validated.plan.no_op,
        changed_uids=validated.plan.changed_uids,
        mutation_blockers=validated.context.runtime_mutation_issues,
    )

def _allowed_capability(
    *,
    island_key: str | None = None,
    current_position: int | None = None,
    allowed_positions: tuple[int, ...] = (),
    placements: tuple[SourceCapabilityPlacement, ...] = (),
) -> SourceOperationCapability:
    minimum: int | None = None
    maximum: int | None = None
    if allowed_positions:
        candidate_minimum = min(allowed_positions)
        candidate_maximum = max(allowed_positions)
        if allowed_positions == tuple(
            range(candidate_minimum, candidate_maximum + 1)
        ):
            minimum = candidate_minimum
            maximum = candidate_maximum
    return SourceOperationCapability(
        True,
        island_key=island_key,
        current_position=current_position,
        minimum_position=minimum,
        maximum_position=maximum,
        allowed_positions=allowed_positions,
        placements=placements,
    )


def _denied_capability(
    blocker: str,
    message: str | None = None,
) -> SourceOperationCapability:
    return SourceOperationCapability(
        False,
        blocker=blocker,
        message=message or source_blocker_message(blocker),
    )


def _capability_from_error(error: SourcePatchError) -> SourceOperationCapability:
    return _denied_capability(error.blocker, error.detail)


def _semantic_elements(section: SpecSection) -> tuple[tuple[str, str], ...]:
    """Return every current semantic element in stable document order."""
    elements: list[tuple[str, str]] = [("sec", "section")]
    for part in section.parts:
        elements.append((part.uid, "part"))
        for article in part.articles:
            elements.append((article.uid, "article"))

            def walk(paragraphs) -> None:
                for paragraph in paragraphs:
                    elements.append((paragraph.uid, "paragraph"))
                    walk(paragraph.children)

            walk(article.paragraphs)
    return tuple(elements)


def _blocked_element_operations(
    kind: str,
    *,
    blocker: str,
    message: str,
) -> SourceElementCapabilities:
    denied = _denied_capability(blocker, message)
    if kind == "section":
        operations = {
            "replace_text": denied,
            "set_project_profile": _allowed_capability(),
            "set_standard_edition": _allowed_capability(),
            "set_standard_suppressed": _allowed_capability(),
        }
    elif kind == "part":
        operations = {"replace_text": denied}
    elif kind == "article":
        operations = {
            "replace_text": denied,
            "add_paragraph": denied,
            "delete": denied,
        }
    else:
        operations = {
            "replace_text": denied,
            "delete": denied,
            "move": denied,
            "add_paragraph": denied,
            "set_status": _allowed_capability(),
            "set_provenance": _allowed_capability(),
        }
    return SourceElementCapabilities(operations)


def blocked_source_edit_capabilities(
    current: SpecSection,
    *,
    blocker: str,
    message: str | None = None,
    status: str = "blocked",
) -> SourceCapabilityReport:
    """Build a fail-closed report when required source analysis is unavailable.

    Body operations carry the exact server issue. Workspace-only metadata stays
    available because it does not alter the retained Word body.
    """
    detail = message or source_blocker_message(blocker)
    return SourceCapabilityReport(
        status,
        {
            uid: _blocked_element_operations(
                kind,
                blocker=blocker,
                message=detail,
            )
            for uid, kind in _semantic_elements(current)
        },
    )


def _run_capability_probe(
    *,
    context: SourcePatchContext,
    source_map: SourceBodyMap,
    baseline: SpecSection,
    candidate: SpecSection,
    bound_inputs: _BoundInputs | None = None,
) -> tuple[_ValidatedPatch | None, SourcePatchError | None]:
    try:
        validated = _validate_source_and_plan(
            source_bytes=context.source_bytes,
            source_map=source_map,
            baseline=baseline,
            current=candidate,
            context=context,
            bound_inputs=bound_inputs,
        )
    except SourcePatchError as exc:
        return None, exc
    return validated, None


def _safe_probe_text(current: str) -> str:
    probe = "Source capability probe"
    return "Source capability probe alternate" if current == probe else probe


def _heading_probe_candidate(current: SpecSection, uid: str) -> SpecSection:
    candidate = copy.deepcopy(current)
    if uid == "sec":
        candidate.title = _safe_probe_text(candidate.title)
        return candidate
    for part in candidate.parts:
        if part.uid == uid:
            part.title = _safe_probe_text(part.title)
            return candidate
        for article in part.articles:
            if article.uid == uid:
                article.title = _safe_probe_text(article.title)
                return candidate
    raise AssertionError(f"capability heading {uid!r} is not in the document")


def _probe_heading_capability(
    *,
    uid: str,
    context: SourcePatchContext,
    source_map: SourceBodyMap,
    baseline: SpecSection,
    current: SpecSection,
    bound_inputs: _BoundInputs | None = None,
) -> SourceOperationCapability:
    _validated, error = _run_capability_probe(
        context=context,
        source_map=source_map,
        baseline=baseline,
        candidate=_heading_probe_candidate(current, uid),
        bound_inputs=bound_inputs,
    )
    return _capability_from_error(error) if error else _allowed_capability()


def _probe_edit_capability(
    *,
    operation: dict[str, object],
    context: SourcePatchContext,
    source_map: SourceBodyMap,
    baseline: SpecSection,
    current: SpecSection,
    bound_inputs: _BoundInputs | None = None,
) -> SourceOperationCapability:
    candidate, _applied = apply_edits(current, [operation])
    _validated, error = _run_capability_probe(
        context=context,
        source_map=source_map,
        baseline=baseline,
        candidate=candidate,
        bound_inputs=bound_inputs,
    )
    return _capability_from_error(error) if error else _allowed_capability()


def _island_key_for_uid(
    validated: _ValidatedPatch,
    uid: str,
) -> str | None:
    for patch in validated.plan.island_patches:
        original_uids = {member.binding.uid for member in patch.island.members}
        desired_uids = {item.uid for item in patch.desired}
        if uid in original_uids or uid in desired_uids:
            return patch.island.key
    return None


def _probe_add_capability(
    *,
    target_uid: str,
    position_count: int,
    context: SourcePatchContext,
    source_map: SourceBodyMap,
    baseline: SpecSection,
    current: SpecSection,
    bound_inputs: _BoundInputs | None = None,
) -> SourceOperationCapability:
    positions_by_island: dict[str, list[int]] = {}
    first_error: SourcePatchError | None = None
    for position in range(position_count + 1):
        try:
            candidate, applied = apply_edits(
                current,
                [
                    {
                        "action": "add_paragraph",
                        "target_id": target_uid,
                        "position": position,
                        "text": "Source capability probe",
                        "status": "assumed",
                    }
                ],
            )
        except SpecEditError:
            if first_error is None:
                first_error = SourcePatchError(
                    target_uid,
                    "nested_structural_change",
                )
            continue
        new_uid = str(applied[0]["id"])
        validated, error = _run_capability_probe(
            context=context,
            source_map=source_map,
            baseline=baseline,
            candidate=candidate,
            bound_inputs=bound_inputs,
        )
        if error is not None:
            if first_error is None:
                first_error = error
            continue
        assert validated is not None
        island_key = _island_key_for_uid(validated, new_uid)
        if island_key is None:  # pragma: no cover - successful-plan invariant
            raise AssertionError("a safe addition has no structural island")
        positions_by_island.setdefault(island_key, []).append(position)

    if not positions_by_island:
        if first_error is None:  # pragma: no cover - every probe has an outcome
            first_error = SourcePatchError(target_uid, "unsafe_structural_island")
        return _capability_from_error(first_error)

    placements = tuple(
        SourceCapabilityPlacement(key, tuple(positions))
        for key, positions in sorted(
            positions_by_island.items(),
            key=lambda item: (item[1][0], item[0]),
        )
    )
    if len(placements) == 1:
        placement = placements[0]
        return _allowed_capability(
            island_key=placement.island_key,
            allowed_positions=placement.allowed_positions,
            placements=placements,
        )
    return _allowed_capability(placements=placements)


def _probe_move_capability(
    *,
    uid: str,
    current_position: int,
    sibling_count: int,
    current_island_key: str | None,
    fallback_blocker: str,
    fallback_message: str | None,
    context: SourcePatchContext,
    source_map: SourceBodyMap,
    baseline: SpecSection,
    current: SpecSection,
    bound_inputs: _BoundInputs | None = None,
) -> SourceOperationCapability:
    allowed_positions: list[int] = []
    island_keys: set[str] = set()
    first_error: SourcePatchError | None = None
    for position in range(sibling_count):
        if position == current_position:
            continue
        candidate, _applied = apply_edits(
            current,
            [{"action": "move", "target_id": uid, "position": position}],
        )
        validated, error = _run_capability_probe(
            context=context,
            source_map=source_map,
            baseline=baseline,
            candidate=candidate,
            bound_inputs=bound_inputs,
        )
        if error is not None:
            if first_error is None:
                first_error = error
            continue
        assert validated is not None
        allowed_positions.append(position)
        island_key = _island_key_for_uid(validated, uid) or current_island_key
        if island_key is not None:
            island_keys.add(island_key)

    if not allowed_positions:
        if first_error is not None:
            return _capability_from_error(first_error)
        return _denied_capability(fallback_blocker, fallback_message)
    if len(island_keys) > 1:  # pragma: no cover - planner forbids this
        raise AssertionError("one move capability crossed structural islands")
    island_key = next(iter(island_keys), current_island_key)
    return _allowed_capability(
        island_key=island_key,
        current_position=current_position,
        allowed_positions=tuple(allowed_positions),
    )


def _paragraph_nodes(section: SpecSection) -> dict[str, Paragraph]:
    nodes: dict[str, Paragraph] = {}
    for part in section.parts:
        for article in part.articles:
            stack = list(reversed(article.paragraphs))
            while stack:
                paragraph = stack.pop()
                nodes[paragraph.uid] = paragraph
                stack.extend(reversed(paragraph.children))
    return nodes


def source_edit_capabilities(
    *,
    context: SourcePatchContext,
    source_map: SourceBodyMap,
    baseline: SpecSection,
    current: SpecSection,
) -> SourceCapabilityReport:
    """Derive per-element permissions by probing the authoritative final gate.

    Every probe is transactionally built on a deep copy by ``apply_edits`` (or
    an equivalent heading-only copy), and every body candidate completes the
    same lexical XML and raw-ZIP preflight as a real request. The supplied
    immutable context is identity-bound once and reused for every probe.
    """
    bound_context = _context_for_inputs(
        source_bytes=context.source_bytes,
        source_map=source_map,
        baseline=baseline,
        context=context,
    )
    # Bind the fixed inputs once for the whole sweep. Every probe below runs
    # the same authoritative gate on the same source/map/baseline; only the
    # candidate differs. Re-deriving the baseline projection, its paragraph
    # tree and the numbered islands inside every probe made the sweep
    # quadratic in body size for no added assurance (see
    # _validate_source_and_plan and _plan_projection_changes).
    baseline_paragraphs, baseline_children, baseline_headings = (
        _projection_paragraphs(baseline)
    )
    islands = _build_numbered_islands(
        context=bound_context,
        source_map=source_map,
        baseline=baseline_paragraphs,
        base_children=baseline_children,
    )
    bound_inputs = _BoundInputs(
        context=bound_context,
        baseline_projection=semantic_body_projection(baseline),
        baseline_paragraphs=baseline_paragraphs,
        baseline_children=baseline_children,
        baseline_headings=baseline_headings,
        islands=islands,
    )
    current_validated = _validate_source_and_plan(
        source_bytes=bound_context.source_bytes,
        source_map=source_map,
        baseline=baseline,
        current=current,
        context=bound_context,
        bound_inputs=bound_inputs,
    )
    status = (
        "pass_through_only"
        if bound_context.global_blockers
        or bound_context.runtime_mutation_issues
        else "ready"
    )

    current_paragraphs, current_children, _current_headings = (
        _projection_paragraphs(current)
    )
    current_nodes = _paragraph_nodes(current)
    _by_key, baseline_island_by_uid, _by_article, _diagnostics = islands
    current_island_by_uid = {
        uid: island.key for uid, island in baseline_island_by_uid.items()
    }
    for patch in current_validated.plan.island_patches:
        for item in patch.desired:
            current_island_by_uid[item.uid] = patch.island.key

    mutation_blocker: str | None = None
    mutation_message: str | None = None
    if bound_context.global_blockers:
        mutation_blocker = bound_context.global_blockers[0]
        mutation_message = source_blocker_message(mutation_blocker)
    elif bound_context.runtime_mutation_issues:
        runtime_issue = bound_context.runtime_mutation_issues[0]
        mutation_blocker = runtime_issue.blocker
        mutation_message = runtime_issue.message

    elements: dict[str, SourceElementCapabilities] = {}
    elements["sec"] = SourceElementCapabilities(
        {
            "replace_text": _probe_heading_capability(
                uid="sec",
                context=bound_context,
                source_map=source_map,
                baseline=baseline,
                current=current,
                bound_inputs=bound_inputs,
            ),
            "set_project_profile": _allowed_capability(),
            "set_standard_edition": _allowed_capability(),
            "set_standard_suppressed": _allowed_capability(),
        }
    )

    for part in current.parts:
        elements[part.uid] = SourceElementCapabilities(
            {
                "replace_text": _probe_heading_capability(
                    uid=part.uid,
                    context=bound_context,
                    source_map=source_map,
                    baseline=baseline,
                    current=current,
                    bound_inputs=bound_inputs,
                )
            }
        )
        for article in part.articles:
            elements[article.uid] = SourceElementCapabilities(
                {
                    "replace_text": _probe_heading_capability(
                        uid=article.uid,
                        context=bound_context,
                        source_map=source_map,
                        baseline=baseline,
                        current=current,
                        bound_inputs=bound_inputs,
                    ),
                    "add_paragraph": _probe_add_capability(
                        target_uid=article.uid,
                        position_count=len(article.paragraphs),
                        context=bound_context,
                        source_map=source_map,
                        baseline=baseline,
                        current=current,
                        bound_inputs=bound_inputs,
                    ),
                    "delete": _probe_edit_capability(
                        operation={"action": "delete", "target_id": article.uid},
                        context=bound_context,
                        source_map=source_map,
                        baseline=baseline,
                        current=current,
                        bound_inputs=bound_inputs,
                    ),
                }
            )

    for uid, paragraph in current_paragraphs.items():
        siblings = current_children.get(paragraph.parent_uid, ())
        current_position = siblings.index(uid)
        if mutation_blocker is not None:
            fallback_blocker = mutation_blocker
            fallback_message = mutation_message
        elif uid in baseline_paragraphs:
            fallback_blocker = _structural_member_blocker(
                uid,
                baseline_paragraphs,
                source_map,
            )
            fallback_message = None
        else:
            fallback_blocker = "unsafe_structural_island"
            fallback_message = None

        paragraph_node = current_nodes[uid]
        next_status = next(
            status_name
            for status_name in STATUSES
            if status_name != paragraph_node.status
        )
        provenance_probe = (
            "source-capability-probe-alternate"
            if paragraph_node.source_item_id == "source-capability-probe"
            else "source-capability-probe"
        )
        delete_capability = _probe_edit_capability(
            operation={"action": "delete", "target_id": uid},
            context=bound_context,
            source_map=source_map,
            baseline=baseline,
            current=current,
            bound_inputs=bound_inputs,
        )
        if delete_capability.allowed:
            delete_capability = _allowed_capability(
                island_key=current_island_by_uid.get(uid)
            )
        elements[uid] = SourceElementCapabilities(
            {
                "replace_text": _probe_edit_capability(
                    operation={
                        "action": "replace",
                        "target_id": uid,
                        "text": _safe_probe_text(paragraph.text),
                    },
                    context=bound_context,
                    source_map=source_map,
                    baseline=baseline,
                    current=current,
                    bound_inputs=bound_inputs,
                ),
                "delete": delete_capability,
                "move": _probe_move_capability(
                    uid=uid,
                    current_position=current_position,
                    sibling_count=len(siblings),
                    current_island_key=current_island_by_uid.get(uid),
                    fallback_blocker=fallback_blocker,
                    fallback_message=fallback_message,
                    context=bound_context,
                    source_map=source_map,
                    baseline=baseline,
                    current=current,
                    bound_inputs=bound_inputs,
                ),
                "add_paragraph": _probe_add_capability(
                    target_uid=uid,
                    position_count=len(current_children.get(uid, ())),
                    context=bound_context,
                    source_map=source_map,
                    baseline=baseline,
                    current=current,
                    bound_inputs=bound_inputs,
                ),
                "set_status": _probe_edit_capability(
                    operation={
                        "action": "set_status",
                        "target_id": uid,
                        "status": next_status,
                    },
                    context=bound_context,
                    source_map=source_map,
                    baseline=baseline,
                    current=current,
                    bound_inputs=bound_inputs,
                ),
                "set_provenance": _probe_edit_capability(
                    operation={
                        "action": "replace",
                        "target_id": uid,
                        "source_item_id": provenance_probe,
                    },
                    context=bound_context,
                    source_map=source_map,
                    baseline=baseline,
                    current=current,
                    bound_inputs=bound_inputs,
                ),
            }
        )

    return SourceCapabilityReport(status, elements)


def source_capability_summary(
    report: SourceCapabilityReport,
    current: SpecSection,
) -> str:
    """Render compact model/QC guidance without OOXML or package internals."""
    paragraph_order = [
        row[1]
        for row in semantic_body_projection(current)
        if row[0] == "paragraph"
    ]
    text_editable = [
        uid
        for uid in paragraph_order
        if report.elements.get(uid)
        and report.elements[uid].operations.get("replace_text")
        and report.elements[uid].operations["replace_text"].allowed
    ]
    islands: dict[str, list[str]] = {}
    for uid in paragraph_order:
        element = report.elements.get(uid)
        if element is None:
            continue
        for operation_name in ("move", "delete"):
            operation = element.operations.get(operation_name)
            if operation and operation.allowed and operation.island_key:
                members = islands.setdefault(operation.island_key, [])
                if uid not in members:
                    members.append(uid)

    lines = ["Source-preserving body permissions:"]
    lines.append(
        "- Text-editable IDs: "
        + (", ".join(text_editable) if text_editable else "none")
    )
    if islands:
        for key, members in islands.items():
            lines.append(f"- Structural island {key}: {', '.join(members)}")
    else:
        lines.append("- Structural islands: none")
    for uid, element in report.elements.items():
        add = element.operations.get("add_paragraph")
        if add is None or not add.allowed:
            continue
        for placement in add.placements:
            positions = ", ".join(str(item) for item in placement.allowed_positions)
            lines.append(
                f"- Add positions for {uid} in island {placement.island_key}: "
                f"{positions}"
            )
    if report.status == "pass_through_only":
        lines.append("- Imported body mutation is pass-through-only.")
    lines.extend(
        [
            "- All other imported body IDs are read-only.",
            "- Status, research provenance, and project metadata may still be changed.",
            "- Every proposed final state is validated server-side.",
        ]
    )
    return "\n".join(lines)


def _serialize_tree(tree, original_xml: bytes) -> bytes:
    encoding = tree.docinfo.encoding or "UTF-8"
    has_declaration = original_xml.lstrip().startswith(b"<?xml")
    return etree.tostring(
        tree,
        encoding=encoding,
        xml_declaration=has_declaration,
        pretty_print=False,
    )


def _element_with_text_patch(
    source_element,
    binding: SourceParagraphBinding,
    new_text: str,
):
    """Clone one anchored paragraph and replace precisely its approved span."""
    element = copy.deepcopy(source_element)
    if (
        element.tag != _W_P
        or canonical_element_sha256(source_element)
        != binding.element_c14n_sha256
    ):
        raise SourcePatchError(binding.uid, "body_anchor_mismatch")
    texts = element.findall(f".//{_W_T}")
    span = binding.text_span
    if span is None or span.text_node_ordinal >= len(texts):
        raise SourcePatchError(binding.uid, "text_anchor_mismatch")
    text_node = texts[span.text_node_ordinal]
    if (text_node.text or "") != span.source_node_text:
        raise SourcePatchError(binding.uid, "text_anchor_mismatch")
    text_node.text = span.prefix + new_text + span.suffix
    return element


def _lexical_text_patch_manifest(
    *,
    document_xml: bytes,
    index: SourceXmlIndex,
    text_patches: tuple[_TextPatch, ...],
) -> tuple[XmlPatch, ...]:
    manifest: list[XmlPatch] = []
    for patch in text_patches:
        binding = patch.binding
        span = binding.text_span
        if span is None:
            raise SourcePatchError(binding.uid, "text_anchor_mismatch")
        try:
            body_child = index.body_child(binding.body_child_index)
            if body_child.expanded_name != _W_P:
                raise XmlLexicalError(
                    "body_anchor_mismatch",
                    "the mapped lexical body child is not a Word paragraph",
                )
            text_node = index.word_text(
                binding.body_child_index, span.text_node_ordinal
            )
            if not text_node.mutable_content:
                raise XmlLexicalError(
                    text_node.blocker
                    or "unsupported_source_text_lexical_form",
                    "the mapped Word text uses CDATA or embedded lexical markup",
                )
            if (
                text_node.decoded_text != span.source_node_text
                or span.source_node_text[span.start : span.end]
                != binding.baseline_text
            ):
                raise XmlLexicalError(
                    "text_anchor_mismatch",
                    "the mapped decoded Word text no longer matches its source anchor",
                )
            raw_span = decoded_slice_byte_span(
                document_xml, text_node, span.start, span.end
            )
            replacement = encode_word_text(
                patch.new_text,
                raw_prefix=document_xml[
                    text_node.content_span.start : raw_span.start
                ],
                raw_suffix=document_xml[
                    raw_span.end : text_node.content_span.end
                ],
            )
        except XmlLexicalError as exc:
            raise SourcePatchError(binding.uid, exc.blocker, exc.detail) from exc
        manifest.append(
            XmlPatch(
                start=raw_span.start,
                end=raw_span.end,
                replacement=replacement,
                uid=binding.uid,
                reason="replace_text",
            )
        )
    return tuple(manifest)


def _element_record_for_body_child(
    index: SourceXmlIndex,
    body_child_index: int,
) -> XmlElementByteSpan:
    child = index.body_child(body_child_index)
    return index.element_for_span(child.element_span)


def _single_direct_child(
    index: SourceXmlIndex,
    parent: XmlElementByteSpan,
    expanded_name: str,
    *,
    uid: str,
    required: bool,
) -> XmlElementByteSpan | None:
    matches = [
        child
        for child in index.direct_children(parent)
        if child.expanded_name == expanded_name
    ]
    if len(matches) > 1 or (required and len(matches) != 1):
        raise SourcePatchError(uid, "ambiguous_structural_template")
    return matches[0] if matches else None


def _lexical_prefix(lexical_name: bytes, expected_local: bytes) -> bytes:
    pieces = lexical_name.split(b":")
    if len(pieces) == 1 and pieces[0] == expected_local:
        return b""
    if len(pieces) == 2 and pieces[0] and pieces[1] == expected_local:
        return pieces[0]
    raise XmlLexicalError(
        "ambiguous_structural_template",
        "the template paragraph uses an ambiguous lexical Word name",
    )


def _prefixed_name(prefix: bytes, local: bytes) -> bytes:
    return prefix + b":" + local if prefix else local


def _required_namespace_declarations(
    *,
    index: SourceXmlIndex,
    paragraph: XmlElementByteSpan,
    p_pr: XmlElementByteSpan,
    r_pr: XmlElementByteSpan | None,
    word_prefix: bytes,
    uid: str,
) -> bytes:
    body_bindings = dict(index.body_namespace_bindings)
    paragraph_local = dict(paragraph.local_namespace_bindings)
    paragraph_external = dict(paragraph.external_namespace_bindings)
    try:
        prefix_text = word_prefix.decode("utf-8")
    except UnicodeDecodeError as exc:  # pragma: no cover - scanner proves UTF-8
        raise SourcePatchError(uid, "ambiguous_structural_template") from exc

    word_uri = paragraph_local.get(
        prefix_text,
        paragraph_external.get(prefix_text, body_bindings.get(prefix_text, "")),
    )
    if word_uri != _W_NS:
        raise SourcePatchError(
            uid,
            "ambiguous_structural_template",
            "the template paragraph's lexical prefix is not bound to WordprocessingML",
        )

    required: dict[str, str] = {prefix_text: word_uri}
    for element in (p_pr, r_pr):
        if element is None:
            continue
        for prefix, uri in element.external_namespace_bindings:
            if prefix == "xml" and uri == "http://www.w3.org/XML/1998/namespace":
                continue
            previous = required.get(prefix)
            if previous is not None and previous != uri:
                raise SourcePatchError(
                    uid,
                    "ambiguous_structural_template",
                    "the template formatting requires conflicting namespace bindings",
                )
            required[prefix] = uri

    declarations: list[bytes] = []
    for prefix, uri in sorted(required.items()):
        if body_bindings.get(prefix) == uri:
            continue
        if not uri:
            raise SourcePatchError(uid, "ambiguous_structural_template")
        try:
            encoded_uri = encode_word_text(uri).replace(b'"', b"&quot;")
            encoded_prefix = prefix.encode("utf-8")
        except (UnicodeEncodeError, XmlLexicalError) as exc:
            raise SourcePatchError(
                uid,
                "ambiguous_structural_template",
                "the template namespace binding cannot be reproduced safely",
            ) from exc
        attribute_name = (
            b"xmlns" if not encoded_prefix else b"xmlns:" + encoded_prefix
        )
        declarations.append(b" " + attribute_name + b'="' + encoded_uri + b'"')
    return b"".join(declarations)


def _expanded_name_parts(name: str) -> tuple[str, str]:
    if name.startswith("{") and "}" in name:
        namespace, local = name[1:].split("}", 1)
        return namespace, local
    return "", name


def _xml_s_only(value: str | None) -> bool:
    return not value or all(character in " \t\r\n" for character in value)


def _validate_synthesis_wrapper_attributes(element, *, uid: str) -> None:
    """Permit only source identity/session attributes that are discarded.

    A new paragraph never copies its source paragraph/run start tags.  Known
    volatile Word identity attributes may therefore be omitted deliberately;
    any other wrapper attribute could affect formatting or behavior and makes
    the clone template ambiguous.
    """
    for name in element.attrib:
        namespace, local = _expanded_name_parts(name)
        if namespace == _W_NS and local.startswith("rsid"):
            continue
        if namespace == _W14_NS and local in {"paraId", "textId"}:
            continue
        raise SourcePatchError(
            uid,
            "ambiguous_structural_template",
            "the numbered template has a paragraph or run attribute that "
            "cannot be inherited safely",
        )


def _validate_synthesis_property(
    element,
    record: XmlElementByteSpan,
    *,
    uid: str,
) -> None:
    """Prove an exact raw pPr/rPr fragment is safe to duplicate.

    The source is not schema-validated, so a denylist would silently accept
    future or crafted WordprocessingML behavior.  Only a deliberately small
    set of ordinary formatting properties is cloneable.  Unknown properties,
    nested behavior, and unknown attributes remain moveable as part of an
    existing paragraph but cannot be stamped onto a new paragraph.
    """
    if record.contains_special_markup:
        raise SourcePatchError(
            uid,
            "ambiguous_structural_template",
            "the numbered template formatting contains comments, processing "
            "instructions, or CDATA",
        )

    root_namespace, root_local = _expanded_name_parts(element.tag)
    if root_namespace != _W_NS or root_local not in {"pPr", "rPr"}:
        raise SourcePatchError(uid, "ambiguous_structural_template")
    if element.attrib or not _xml_s_only(element.text):
        raise SourcePatchError(uid, "ambiguous_structural_template")

    allowed_properties = (
        _SAFE_PPR_LEAF_ATTRIBUTES
        if root_local == "pPr"
        else _SAFE_RPR_LEAF_ATTRIBUTES
    )
    seen: set[str] = set()
    for item in element.iterchildren():
        if not isinstance(item.tag, str) or not _xml_s_only(item.tail):
            raise SourcePatchError(uid, "ambiguous_structural_template")
        namespace, local = _expanded_name_parts(item.tag)
        if namespace != _W_NS or local in seen:
            raise SourcePatchError(
                uid,
                "ambiguous_structural_template",
                "the numbered template contains duplicate or unknown formatting",
            )
        seen.add(local)

        if root_local == "pPr" and local == "numPr":
            if item.attrib or not _xml_s_only(item.text):
                raise SourcePatchError(uid, "ambiguous_structural_template")
            children = list(item.iterchildren())
            if [child.tag for child in children] != [_W_ILVL, _W_NUMID]:
                raise SourcePatchError(uid, "ambiguous_structural_template")
            for child in children:
                if (
                    list(child.iterchildren())
                    or not _xml_s_only(child.text)
                    or not _xml_s_only(child.tail)
                    or set(child.attrib) != {_W_VAL}
                ):
                    raise SourcePatchError(uid, "ambiguous_structural_template")
            continue

        allowed_attributes = allowed_properties.get(local)
        if allowed_attributes is None or list(item.iterchildren()):
            raise SourcePatchError(
                uid,
                "ambiguous_structural_template",
                "the numbered template formatting is outside the proven clone set",
            )
        if not _xml_s_only(item.text):
            raise SourcePatchError(uid, "ambiguous_structural_template")
        for attribute in item.attrib:
            attribute_namespace, attribute_local = _expanded_name_parts(attribute)
            if (
                attribute_namespace != _W_NS
                or attribute_local not in allowed_attributes
            ):
                raise SourcePatchError(
                    uid,
                    "ambiguous_structural_template",
                    "the numbered template formatting has an unknown attribute",
                )


def _validate_synthesis_template_shape(
    *,
    paragraph_element,
    run_element,
    text_element,
    paragraph_record: XmlElementByteSpan,
    p_pr_element,
    p_pr_record: XmlElementByteSpan,
    r_pr_element,
    r_pr_record: XmlElementByteSpan | None,
    uid: str,
) -> None:
    if paragraph_record.contains_special_markup:
        raise SourcePatchError(
            uid,
            "ambiguous_structural_template",
            "the numbered template contains comments, processing instructions, "
            "or CDATA",
        )
    _validate_synthesis_wrapper_attributes(paragraph_element, uid=uid)
    _validate_synthesis_wrapper_attributes(run_element, uid=uid)
    if text_element.attrib:
        raise SourcePatchError(
            uid,
            "ambiguous_structural_template",
            "the numbered template text carries attributes that a new text node "
            "cannot safely inherit",
        )
    if not _xml_s_only(paragraph_element.text) or not _xml_s_only(run_element.text):
        raise SourcePatchError(uid, "ambiguous_structural_template")
    if any(
        not _xml_s_only(child.tail)
        for child in paragraph_element.iterchildren()
    ) or any(not _xml_s_only(child.tail) for child in run_element.iterchildren()):
        raise SourcePatchError(uid, "ambiguous_structural_template")
    _validate_synthesis_property(p_pr_element, p_pr_record, uid=uid)
    if r_pr_element is not None:
        if r_pr_record is None:  # pragma: no cover - caller invariant
            raise SourcePatchError(uid, "ambiguous_structural_template")
        _validate_synthesis_property(r_pr_element, r_pr_record, uid=uid)


def _extract_paragraph_template(
    *,
    document_xml: bytes,
    index: SourceXmlIndex,
    body_child_index: int,
    template_element,
    uid: str,
) -> SourceParagraphTemplate:
    paragraph = _element_record_for_body_child(
        index, body_child_index
    )
    if paragraph.expanded_name != _W_P:
        raise SourcePatchError(uid, "ambiguous_structural_template")
    p_pr = _single_direct_child(
        index, paragraph, _W_PPR, uid=uid, required=True
    )
    run = _single_direct_child(
        index, paragraph, _W_R, uid=uid, required=True
    )
    assert p_pr is not None and run is not None
    r_pr = _single_direct_child(
        index, run, _W_RPR, uid=uid, required=False
    )
    text_record = _single_direct_child(
        index, run, _W_T, uid=uid, required=True
    )
    paragraph_children = index.direct_children(paragraph)
    run_children = index.direct_children(run)
    expected_run_names = [_W_RPR, _W_T] if r_pr is not None else [_W_T]
    if (
        [child.expanded_name for child in paragraph_children] != [_W_PPR, _W_R]
        or [child.expanded_name for child in run_children] != expected_run_names
        or text_record is None
    ):
        raise SourcePatchError(uid, "ambiguous_structural_template")
    semantic_children = _meaningful_children(template_element)
    if [child.tag for child in semantic_children] != [_W_PPR, _W_R]:
        raise SourcePatchError(uid, "ambiguous_structural_template")
    p_pr_element, run_element = semantic_children
    semantic_run_children = _meaningful_children(run_element)
    expected_semantic_names = [_W_RPR, _W_T] if r_pr is not None else [_W_T]
    if [child.tag for child in semantic_run_children] != expected_semantic_names:
        raise SourcePatchError(uid, "ambiguous_structural_template")
    r_pr_element = semantic_run_children[0] if r_pr is not None else None
    text_element = semantic_run_children[-1]
    _validate_synthesis_template_shape(
        paragraph_element=template_element,
        run_element=run_element,
        text_element=text_element,
        paragraph_record=paragraph,
        p_pr_element=p_pr_element,
        p_pr_record=p_pr,
        r_pr_element=r_pr_element,
        r_pr_record=r_pr,
        uid=uid,
    )
    try:
        word_prefix = _lexical_prefix(paragraph.lexical_name, b"p")
        declarations = _required_namespace_declarations(
            index=index,
            paragraph=paragraph,
            p_pr=p_pr,
            r_pr=r_pr,
            word_prefix=word_prefix,
            uid=uid,
        )
    except XmlLexicalError as exc:
        raise SourcePatchError(uid, exc.blocker, exc.detail) from exc

    p_pr_bytes = document_xml[p_pr.element_span.start : p_pr.element_span.end]
    r_pr_bytes = (
        document_xml[r_pr.element_span.start : r_pr.element_span.end]
        if r_pr is not None
        else b""
    )
    return SourceParagraphTemplate(
        body_child_index=body_child_index,
        word_prefix=word_prefix,
        namespace_declarations=declarations,
        p_pr_bytes=p_pr_bytes,
        r_pr_bytes=r_pr_bytes,
    )


def _minimal_numbered_paragraph_bytes(
    *,
    template: SourceParagraphTemplate,
    uid: str,
    text: str,
) -> bytes:
    _validate_text_for_single_word_node(uid, text)
    try:
        encoded_text = encode_word_text(text)
    except XmlLexicalError as exc:
        raise SourcePatchError(uid, exc.blocker, exc.detail) from exc
    word_prefix = template.word_prefix
    p_name = _prefixed_name(word_prefix, b"p")
    r_name = _prefixed_name(word_prefix, b"r")
    t_name = _prefixed_name(word_prefix, b"t")
    return b"".join(
        (
            b"<",
            p_name,
            template.namespace_declarations,
            b">",
            template.p_pr_bytes,
            b"<",
            r_name,
            b">",
            template.r_pr_bytes,
            b"<",
            t_name,
            b">",
            encoded_text,
            b"</",
            t_name,
            b"></",
            r_name,
            b"></",
            p_name,
            b">",
        )
    )


def _retained_paragraph_bytes(
    *,
    document_xml: bytes,
    index: SourceXmlIndex,
    binding: SourceParagraphBinding,
    text_patch: XmlPatch | None,
) -> bytes:
    child = index.body_child(binding.body_child_index)
    raw = document_xml[child.element_span.start : child.element_span.end]
    if text_patch is None:
        return raw
    if not (
        child.element_span.start <= text_patch.start
        <= text_patch.end <= child.element_span.end
    ):
        raise SourcePatchError(binding.uid, "text_anchor_mismatch")
    relative = XmlPatch(
        start=text_patch.start - child.element_span.start,
        end=text_patch.end - child.element_span.start,
        replacement=text_patch.replacement,
        uid=text_patch.uid,
        reason=text_patch.reason,
    )
    try:
        return apply_xml_patches(raw, (relative,))
    except XmlLexicalError as exc:
        raise SourcePatchError(binding.uid, exc.blocker, exc.detail) from exc


def _build_island_byte_patch(
    *,
    document_xml: bytes,
    index: SourceXmlIndex,
    island_patch: _IslandPatch,
    text_by_uid: dict[str, XmlPatch],
    paragraph_templates: Mapping[int, SourceParagraphTemplate],
) -> _IslandBytePatch:
    island = island_patch.island
    expected_indices = list(range(island.start_index, island.end_index + 1))
    member_indices = [
        member.binding.body_child_index for member in island.members
    ]
    if member_indices != expected_indices:
        raise SourcePatchError(island.key, "unsafe_structural_island")
    source_children = [index.body_child(item) for item in expected_indices]
    if any(child.expanded_name != _W_P for child in source_children):
        raise SourcePatchError(island.key, "unsafe_structural_island")

    gaps: list[bytes] = []
    for body_child_index in expected_indices[:-1]:
        gap_span = index.body_gaps[body_child_index + 1]
        gap = document_xml[gap_span.start : gap_span.end]
        try:
            gap_is_whitespace = xml_gap_is_whitespace(document_xml, gap_span)
        except XmlLexicalError as exc:
            raise SourcePatchError(
                island.key,
                "unsafe_structural_island",
                "the numbered island contains ambiguous lexical gap content",
            ) from exc
        if not gap_is_whitespace:
            raise SourcePatchError(
                island.key,
                "unsafe_structural_island",
                "the numbered island contains non-whitespace lexical gap content",
            )
        gaps.append(gap)

    island_uids = {member.binding.uid for member in island.members}
    desired_bytes: list[bytes] = []
    for desired in island_patch.desired:
        if desired.binding is not None:
            if desired.uid not in island_uids:
                raise SourcePatchError(desired.uid, "cross_island_move")
            desired_bytes.append(
                _retained_paragraph_bytes(
                    document_xml=document_xml,
                    index=index,
                    binding=desired.binding,
                    text_patch=text_by_uid.get(desired.uid),
                )
            )
        else:
            if desired.template is None:
                raise SourcePatchError(
                    desired.uid, "ambiguous_structural_template"
                )
            template = paragraph_templates.get(
                desired.template.body_child_index
            )
            if template is None:
                raise SourcePatchError(
                    desired.uid, "ambiguous_structural_template"
                )
            desired_bytes.append(
                _minimal_numbered_paragraph_bytes(
                    template=template,
                    uid=desired.uid,
                    text=desired.text,
                )
            )

    return _IslandBytePatch(
        island_key=island.key,
        source_span=XmlByteSpan(
            source_children[0].element_span.start,
            source_children[-1].element_span.end,
        ),
        desired_elements=tuple(desired_bytes),
        original_gaps=tuple(gaps),
        changed_uids=tuple(
            dict.fromkeys(
                [member.binding.uid for member in island.members]
                + [desired.uid for desired in island_patch.desired]
            )
        ),
    )


def _lexically_patch_document(
    *,
    context: SourcePatchContext,
    plan: _PatchPlan,
) -> bytes:
    """Compose text and structural changes from immutable source bytes."""
    if plan.no_op:
        raise ValueError("lexical patching requires a non-empty plan")
    first_uid = plan.changed_uids[0] if plan.changed_uids else "source"
    document_xml = context.document_xml
    try:
        index = context.xml_index
        if index is None:
            issue = next(
                (
                    item
                    for item in context.runtime_mutation_issues
                    if item.blocker != "unsupported_raw_zip_layout"
                ),
                None,
            )
            raise SourcePatchError(
                first_uid,
                issue.blocker if issue is not None else "unsafe_document_xml",
                issue.message if issue is not None else None,
            )
        text_manifest = _lexical_text_patch_manifest(
            document_xml=document_xml,
            index=index,
            text_patches=plan.text_patches,
        )
    except XmlLexicalError as exc:
        raise SourcePatchError(first_uid, exc.blocker, exc.detail) from exc

    text_by_uid = {patch.uid: patch for patch in text_manifest}
    if len(text_by_uid) != len(text_manifest):
        raise SourcePatchError(
            first_uid,
            "overlapping_xml_patches",
            "more than one text patch targets the same paragraph",
        )
    top_level: list[XmlPatch] = []
    nested_text_uids: set[str] = set()
    for island_patch in plan.island_patches:
        byte_patch = _build_island_byte_patch(
            document_xml=document_xml,
            index=index,
            island_patch=island_patch,
            text_by_uid=text_by_uid,
            paragraph_templates=context.paragraph_templates,
        )
        nested_text_uids.update(
            desired.uid
            for desired in island_patch.desired
            if desired.binding is not None and desired.uid in text_by_uid
        )
        top_level.append(
            XmlPatch(
                start=byte_patch.source_span.start,
                end=byte_patch.source_span.end,
                replacement=byte_patch.replacement,
                uid=byte_patch.island_key,
                reason="structural_island",
            )
        )
    top_level.extend(
        patch for patch in text_manifest if patch.uid not in nested_text_uids
    )

    try:
        patched_xml = apply_xml_patches(document_xml, top_level)
    except XmlLexicalError as exc:
        raise SourcePatchError(first_uid, exc.blocker, exc.detail) from exc
    # Byte locality is necessary but not sufficient.  Reparse the composed
    # document before it is placed into a package; the package audit later
    # independently proves its semantic body against the same final-state plan.
    patched_tree, _patched_body = _parse_document_xml(patched_xml)
    try:
        build_source_xml_index(patched_xml, validated_tree=patched_tree)
    except XmlLexicalError as exc:
        raise SourcePatchError(
            first_uid,
            "output_validation_failed",
            "the composed Word XML did not pass the independent lexical index",
        ) from exc
    _audit_document_xml_preservation(context, patched_xml, plan)
    return patched_xml


def _minimal_numbered_paragraph(template_element, uid: str, text: str):
    """Create only pPr + one run's rPr + one text node from a safe template."""
    _validate_text_for_single_word_node(uid, text)
    p_pr = template_element.find(_W_PPR)
    run = template_element.find(_W_R)
    if p_pr is None or run is None:
        raise SourcePatchError(uid, "ambiguous_structural_template")
    # Give the detached audit copy the same lexical Word prefix selected by
    # the source template. Exclusive C14N intentionally treats prefix choice
    # as significant even when expanded names are identical.
    paragraph = etree.Element(
        _W_P,
        nsmap={template_element.prefix: _W_NS},
    )
    paragraph.append(copy.deepcopy(p_pr))
    new_run = etree.SubElement(paragraph, _W_R)
    r_pr = run.find(_W_RPR)
    if r_pr is not None:
        new_run.append(copy.deepcopy(r_pr))
    text_node = etree.SubElement(new_run, _W_T)
    text_node.text = text
    return paragraph


def _inventory_element(item: SourceBodyInventoryItem):
    try:
        return etree.fromstring(item.element_c14n, parser=_xml_parser())
    except (etree.XMLSyntaxError, ValueError) as exc:  # pragma: no cover - builder proof
        raise SourcePatchError(
            "source",
            "output_validation_failed",
            "a cached source body fingerprint could not be reconstructed",
        ) from exc


def _expected_body_element_hashes(
    context: SourcePatchContext,
    plan: _PatchPlan,
) -> list[str]:
    """Independently materialize the only body sequence the plan permits."""
    text_by_uid = {
        patch.binding.uid: patch.new_text for patch in plan.text_patches
    }
    text_binding_by_index = {
        patch.binding.body_child_index: patch.binding
        for patch in plan.text_patches
    }
    islands_by_start = {
        patch.island.start_index: patch for patch in plan.island_patches
    }
    expected: list[str] = []
    index = 0
    while index < len(context.body_inventory):
        island_patch = islands_by_start.get(index)
        if island_patch is not None:
            for desired in island_patch.desired:
                if desired.binding is not None:
                    item = context.body_inventory[
                        desired.binding.body_child_index
                    ]
                    if desired.uid in text_by_uid:
                        element = _element_with_text_patch(
                            _inventory_element(item),
                            desired.binding,
                            text_by_uid[desired.uid],
                        )
                        expected.append(canonical_element_sha256(element))
                    else:
                        expected.append(item.element_c14n_sha256)
                else:
                    if desired.template is None:
                        raise SourcePatchError(desired.uid, "ambiguous_structural_template")
                    template_item = context.body_inventory[
                        desired.template.body_child_index
                    ]
                    expected_element = _minimal_numbered_paragraph(
                        _inventory_element(template_item),
                        desired.uid,
                        desired.text,
                    )
                    expected.append(canonical_element_sha256(expected_element))
            index = island_patch.island.end_index + 1
            continue
        binding = text_binding_by_index.get(index)
        if binding is not None:
            expected_element = _element_with_text_patch(
                _inventory_element(context.body_inventory[index]),
                binding,
                text_by_uid[binding.uid],
            )
            expected.append(canonical_element_sha256(expected_element))
        else:
            expected.append(
                context.body_inventory[index].element_c14n_sha256
            )
        index += 1
    return expected


def _audit_document_xml_preservation(
    context: SourcePatchContext,
    output_document_xml: bytes,
    plan: _PatchPlan,
) -> None:
    """Prove the composed main part is exactly the plan's semantic result.

    This runs inside transition validation as well as after package cloning, so
    manual, model, QC, history-restoration, and download paths share the same
    final-state gate.
    """
    output_tree, output_body = _parse_document_xml(output_document_xml)
    output_root = output_tree.getroot()
    if (
        context.document_tag != output_root.tag
        or context.document_attributes != tuple(sorted(output_root.attrib.items()))
        or context.document_namespace_bindings
        != tuple(
            sorted(
                (
                    (prefix or "", uri)
                    for prefix, uri in output_root.nsmap.items()
                ),
                key=lambda item: item[0],
            )
        )
        or context.body_attributes != tuple(sorted(output_body.attrib.items()))
    ):
        raise SourcePatchError(
            "source",
            "out_of_scope_document_xml_changed",
            "document or body metadata outside the edit surface changed",
        )
    output_non_body = [
        child
        for child in _meaningful_children(output_root)
        if child is not output_body
    ]
    if context.non_body_c14n_sha256 != tuple(
        canonical_element_sha256(child) for child in output_non_body
    ):
        raise SourcePatchError(
            "source",
            "out_of_scope_document_xml_changed",
            "XML outside the Word body changed",
        )

    output_children = _meaningful_children(output_body)
    expected_hashes = _expected_body_element_hashes(context, plan)
    if len(expected_hashes) != len(output_children):
        raise SourcePatchError(
            "source",
            "body_structure_changed",
            "the patched Word body does not have the planned block count",
        )
    for index, (expected_hash, actual) in enumerate(
        zip(expected_hashes, output_children)
    ):
        if expected_hash != canonical_element_sha256(actual):
            raise SourcePatchError(
                "source",
                "unexpected_body_change",
                f"body child {index} differs from the fail-closed patch plan",
            )
    if output_children and output_children[-1].tag != _W_SECTPR:
        # Word permits a body without a final sectPr, but if one exists it must
        # remain last. The source inventory comparison above preserves the
        # no-sectPr case; this catches an accidentally displaced one.
        sect_indices = [
            index
            for index, child in enumerate(output_children)
            if child.tag == _W_SECTPR
        ]
        if sect_indices:
            raise SourcePatchError(
                "source",
                "section_properties_moved",
                "final Word section properties are no longer the last body child",
            )


def _audit_package_preservation(
    context: SourcePatchContext,
    output_bytes: bytes,
    plan: _PatchPlan,
    *,
    expected_document_xml: bytes,
) -> None:
    try:
        inspect_docx_package(output_bytes)
        audit_package_preservation_streaming(
            context.source_bytes,
            output_bytes,
            expected_document_xml=expected_document_xml,
            document_part=_DOCUMENT_PART,
        )
    except SourceAuditError as exc:
        raise SourcePatchError("source", exc.blocker, exc.detail) from exc
    except (SourcePackageError, TypeError, ValueError) as exc:
        raise SourcePatchError(
            "source",
            "output_validation_failed",
            "the patched DOCX failed package validation",
        ) from exc

    try:
        audit_raw_zip_replacement(
            context.source_bytes,
            output_bytes,
            filename=_DOCUMENT_PART,
            expected_payload=expected_document_xml,
            source_archive=context.raw_zip_archive,
        )
    except RawZipError as exc:
        raise SourcePatchError(
            "source",
            "output_validation_failed",
            f"the raw ZIP preservation audit failed: {exc.detail}",
        ) from exc

    _audit_document_xml_preservation(
        context,
        expected_document_xml,
        plan,
    )


def build_source_preserving_docx(
    *,
    source_bytes: bytes,
    source_map: SourceBodyMap,
    baseline: SpecSection,
    current: SpecSection,
    context: SourcePatchContext | None = None,
) -> bytes:
    """Return an exact no-op or a source clone containing only the safe plan."""
    validated = _validate_source_and_plan(
        source_bytes=source_bytes,
        source_map=source_map,
        baseline=baseline,
        current=current,
        context=context,
    )
    plan = validated.plan
    if plan.no_op:
        return source_bytes

    lexically_patched_xml = validated.lexically_patched_xml
    prepared_output = validated.prepared_output
    if lexically_patched_xml is None:  # pragma: no cover - plan invariant
        raise SourcePatchError(
            "source",
            "output_validation_failed",
            "the source patch plan has no lexical XML result",
        )
    if prepared_output is None:  # pragma: no cover - plan invariant
        raise SourcePatchError(
            "source",
            "output_validation_failed",
            "the source patch plan has no prepared raw ZIP result",
        )
    output = prepared_output
    _audit_package_preservation(
        validated.context,
        output,
        plan,
        expected_document_xml=lexically_patched_xml,
    )
    return output


__all__ = [
    "SourceBodyInventoryItem",
    "SourceCapabilityPlacement",
    "SourceCapabilityReport",
    "SourceElementCapabilities",
    "SourceOperationCapability",
    "SourcePackageMemberInventory",
    "SourceParagraphTemplate",
    "SourcePatchContext",
    "SourcePatchError",
    "SourcePatchIssue",
    "SourcePatchReadiness",
    "blocked_source_edit_capabilities",
    "build_source_patch_context",
    "build_source_preserving_docx",
    "source_capability_summary",
    "source_edit_capabilities",
    "source_patch_readiness",
    "validate_source_transition",
    "validate_source_map_identity",
]
