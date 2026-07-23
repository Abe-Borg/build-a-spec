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
from dataclasses import dataclass
from io import BytesIO

from lxml import etree

from .model import SpecSection
from .source_mapping import (
    SourceBodyMap,
    SourceParagraphBinding,
    bind_source_paragraph,
    canonical_element_sha256,
    detect_global_source_blockers,
    semantic_body_projection,
    semantic_body_projection_sha256,
    source_blocker_message,
    source_replacement_text_blocker,
)
from .source_package import SourcePackageError, inspect_docx_package

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

    def to_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "no_op": self.no_op,
            "changed_uids": list(self.changed_uids),
            "blockers": [blocker.to_dict() for blocker in self.blockers],
        }


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
    if (element.text or "").strip():
        return True
    return any((child.tail or "").strip() for child in element.iterchildren())


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
) -> tuple[
    dict[str, _ProjectedParagraph],
    dict[str, tuple[str, ...]],
    dict[str, _ProjectedParagraph],
    dict[str, tuple[str, ...]],
]:
    """Reject every body-structure change outside flat article provisions."""
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
    children: list,
    defined_levels: frozenset[tuple[str, str]],
) -> _NumberedMember | None:
    if (
        paragraph.depth != 0
        or paragraph.child_uids
        or binding is None
        or not binding.editable
        or binding.text_span is None
        or binding.text_span.prefix
        or binding.text_span.suffix
        or not 0 <= binding.body_child_index < len(children)
    ):
        return None
    element = children[binding.body_child_index]
    if element.tag != _W_P:
        return None
    numbering = _direct_numbering_signature(element, defined_levels)
    if numbering is None:
        return None
    num_id, ilvl, signature = numbering
    ppr_signature, rpr_signature = _property_signatures(element)
    return _NumberedMember(
        binding=binding,
        num_id=num_id,
        ilvl=ilvl,
        signature=signature,
        ppr_signature=ppr_signature,
        rpr_signature=rpr_signature,
    )


def _validated_source_identity(
    *,
    source_bytes: bytes,
    source_map: SourceBodyMap,
    baseline: SpecSection,
) -> tuple[bytes, object, object]:
    """Validate source/map/baseline identity without applying edit policy."""
    if not isinstance(source_bytes, bytes):
        raise SourcePatchError(
            "source",
            "source_unavailable",
            "the exact imported DOCX bytes are unavailable",
        )
    try:
        inspect_docx_package(source_bytes)
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

    document_xml = _read_document_xml(source_bytes)
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
    return document_xml, tree, body


def validate_source_map_identity(
    *,
    source_bytes: bytes,
    source_map: SourceBodyMap,
    baseline: SpecSection,
) -> None:
    """Public read-only identity gate for project-container restoration.

    Mutation blockers such as signatures, protection, and pending revisions
    are intentionally not errors here: a valid project may retain and return
    such a document byte-for-byte even though mutation remains blocked.
    """
    _validated_source_identity(
        source_bytes=source_bytes,
        source_map=source_map,
        baseline=baseline,
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
    source_bytes: bytes,
    source_map: SourceBodyMap,
    baseline: dict[str, _ProjectedParagraph],
    base_children: dict[str, tuple[str, ...]],
    body_children: list,
) -> tuple[
    dict[str, _NumberedIsland],
    dict[str, _NumberedIsland],
    dict[str, tuple[_NumberedIsland, ...]],
    dict[str, str],
]:
    defined_levels, usage_counts = _numbering_context(source_bytes)
    eligible = {
        uid: member
        for uid, paragraph in baseline.items()
        if (
            member := _eligible_numbered_member(
                paragraph,
                source_map.bindings.get(uid),
                body_children,
                defined_levels,
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
                previous_element = body_children[
                    previous.binding.body_child_index
                ]
                current_element = body_children[member.binding.body_child_index]
                contiguous = (
                    previous.binding.body_child_index + 1
                    == member.binding.body_child_index
                    and previous_element.getnext() is current_element
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
            if usage_counts.get(num_id, 0) != len(raw_island):
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
    source_bytes: bytes,
    source_map: SourceBodyMap,
    baseline_section: SpecSection,
    current_section: SpecSection,
    body,
) -> _PatchPlan:
    baseline, base_children, current, current_children = _validate_fixed_projection(
        baseline_section, current_section
    )
    body_children = _meaningful_children(body)
    _islands_by_key, island_by_uid, islands_by_article, diagnostics = _build_numbered_islands(
        source_bytes=source_bytes,
        source_map=source_map,
        baseline=baseline,
        base_children=base_children,
        body_children=body_children,
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

    if island_patches and (
        len(list(body.iterchildren())) != len(body_children)
        or _has_non_whitespace_direct_character_data(body)
    ):
        raise SourcePatchError(
            island_patches[0].island.key,
            "unsafe_structural_island",
            "the source body contains unmapped character, comment, or "
            "processing-node content",
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
) -> tuple[_PatchPlan, bytes, object, object]:
    document_xml, tree, body = _validated_source_identity(
        source_bytes=source_bytes,
        source_map=source_map,
        baseline=baseline,
    )

    baseline_projection = semantic_body_projection(baseline)
    current_projection = semantic_body_projection(current)
    if baseline_projection != current_projection and source_map.global_blockers:
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
        raise SourcePatchError(changed_uid, source_map.global_blockers[0])

    plan = _plan_projection_changes(
        source_bytes=source_bytes,
        source_map=source_map,
        baseline_section=baseline,
        current_section=current,
        body=body,
    )

    # Exact semantic no-ops return the original bytes even for signed,
    # protected, or revision-bearing sources.  Those features only block an
    # actual mutation; pass-through cannot invalidate or reinterpret them.
    if not plan.no_op:
        try:
            actual_global_blockers = detect_global_source_blockers(source_bytes)
        except (TypeError, ValueError) as exc:
            raise SourcePatchError(
                "source",
                "unsafe_package",
                "source mutation blockers could not be verified",
            ) from exc
        if actual_global_blockers:
            raise SourcePatchError(
                plan.changed_uids[0] if plan.changed_uids else "source",
                actual_global_blockers[0],
            )

    return plan, document_xml, tree, body


def validate_source_transition(
    *,
    source_bytes: bytes,
    source_map: SourceBodyMap,
    baseline: SpecSection,
    current: SpecSection,
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
    )


def source_patch_readiness(
    *,
    source_bytes: bytes | None,
    source_map: SourceBodyMap | None,
    baseline: SpecSection,
    current: SpecSection,
) -> SourcePatchReadiness:
    """Non-throwing readiness report for API/UI integration."""
    if source_bytes is None or source_map is None:
        message = "the exact imported DOCX and source map are unavailable"
        issue = SourcePatchIssue("source", "source_unavailable", message)
        return SourcePatchReadiness(False, False, blockers=(issue,))
    try:
        plan, _xml, _tree, _body = _validate_source_and_plan(
            source_bytes=source_bytes,
            source_map=source_map,
            baseline=baseline,
            current=current,
        )
    except SourcePatchError as exc:
        issue = SourcePatchIssue(exc.uid, exc.blocker, exc.detail)
        return SourcePatchReadiness(False, False, blockers=(issue,))
    return SourcePatchReadiness(
        True,
        plan.no_op,
        changed_uids=plan.changed_uids,
    )


def _clone_with_document_xml(source_bytes: bytes, document_xml: bytes) -> bytes:
    output = BytesIO()
    try:
        with zipfile.ZipFile(BytesIO(source_bytes), "r") as source:
            with zipfile.ZipFile(output, "w") as destination:
                destination.comment = source.comment
                for info in source.infolist():
                    payload = (
                        document_xml
                        if info.filename == _DOCUMENT_PART
                        else source.read(info)
                    )
                    destination.writestr(copy.copy(info), payload)
    except (OSError, RuntimeError, zipfile.BadZipFile, NotImplementedError) as exc:
        raise SourcePatchError(
            "source",
            "package_clone_failed",
            "the source DOCX could not be cloned safely",
        ) from exc
    return output.getvalue()


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


def _minimal_numbered_paragraph(template_element, uid: str, text: str):
    """Create only pPr + one run's rPr + one text node from a safe template."""
    _validate_text_for_single_word_node(uid, text)
    p_pr = template_element.find(_W_PPR)
    run = template_element.find(_W_R)
    if p_pr is None or run is None:
        raise SourcePatchError(uid, "ambiguous_structural_template")
    # Give the detached audit copy the same stable Word prefix it receives
    # under document.xml; exclusive C14N intentionally treats prefix choice
    # as significant even when expanded names are identical.
    paragraph = etree.Element(_W_P, nsmap={"w": _W_NS})
    paragraph.append(copy.deepcopy(p_pr))
    new_run = etree.SubElement(paragraph, _W_R)
    r_pr = run.find(_W_RPR)
    if r_pr is not None:
        new_run.append(copy.deepcopy(r_pr))
    text_node = etree.SubElement(new_run, _W_T)
    text_node.text = text
    return paragraph


def _expected_body_elements(
    source_children: list,
    plan: _PatchPlan,
) -> list:
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
    expected: list = []
    index = 0
    while index < len(source_children):
        island_patch = islands_by_start.get(index)
        if island_patch is not None:
            for desired in island_patch.desired:
                if desired.binding is not None:
                    source_element = source_children[desired.binding.body_child_index]
                    if desired.uid in text_by_uid:
                        expected.append(
                            _element_with_text_patch(
                                source_element,
                                desired.binding,
                                text_by_uid[desired.uid],
                            )
                        )
                    else:
                        expected.append(copy.deepcopy(source_element))
                else:
                    if desired.template is None:
                        raise SourcePatchError(desired.uid, "ambiguous_structural_template")
                    template = source_children[desired.template.body_child_index]
                    expected.append(
                        _minimal_numbered_paragraph(template, desired.uid, desired.text)
                    )
            index = island_patch.island.end_index + 1
            continue
        binding = text_binding_by_index.get(index)
        if binding is not None:
            expected.append(
                _element_with_text_patch(
                    source_children[index], binding, text_by_uid[binding.uid]
                )
            )
        else:
            expected.append(copy.deepcopy(source_children[index]))
        index += 1
    return expected


def _audit_package_preservation(
    source_bytes: bytes,
    output_bytes: bytes,
    plan: _PatchPlan,
) -> None:
    try:
        inspect_docx_package(output_bytes)
        with zipfile.ZipFile(BytesIO(source_bytes), "r") as source:
            source_names = source.namelist()
            source_parts = {name: source.read(name) for name in source_names}
        with zipfile.ZipFile(BytesIO(output_bytes), "r") as output:
            output_names = output.namelist()
            output_parts = {name: output.read(name) for name in output_names}
    except (SourcePackageError, KeyError, RuntimeError, zipfile.BadZipFile) as exc:
        raise SourcePatchError(
            "source",
            "output_validation_failed",
            "the patched DOCX failed package validation",
        ) from exc
    if source_names != output_names:
        raise SourcePatchError(
            "source",
            "part_inventory_changed",
            "the patched package member inventory changed",
        )
    for name in source_names:
        if name != _DOCUMENT_PART and source_parts[name] != output_parts[name]:
            raise SourcePatchError(
                "source",
                "out_of_scope_part_changed",
                f"out-of-scope part {name!r} changed",
            )

    source_tree, source_body = _parse_document_xml(source_parts[_DOCUMENT_PART])
    output_tree, output_body = _parse_document_xml(output_parts[_DOCUMENT_PART])
    source_root = source_tree.getroot()
    output_root = output_tree.getroot()
    if (
        source_root.tag != output_root.tag
        or source_root.attrib != output_root.attrib
        or source_root.nsmap != output_root.nsmap
        or source_body.attrib != output_body.attrib
    ):
        raise SourcePatchError(
            "source",
            "out_of_scope_document_xml_changed",
            "document or body metadata outside the edit surface changed",
        )
    source_non_body = [
        child
        for child in _meaningful_children(source_root)
        if child is not source_body
    ]
    output_non_body = [
        child
        for child in _meaningful_children(output_root)
        if child is not output_body
    ]
    if len(source_non_body) != len(output_non_body) or any(
        canonical_element_sha256(before) != canonical_element_sha256(after)
        for before, after in zip(source_non_body, output_non_body)
    ):
        raise SourcePatchError(
            "source",
            "out_of_scope_document_xml_changed",
            "XML outside the Word body changed",
        )

    source_children = _meaningful_children(source_body)
    output_children = _meaningful_children(output_body)
    expected_children = _expected_body_elements(source_children, plan)
    if len(expected_children) != len(output_children):
        raise SourcePatchError(
            "source",
            "body_structure_changed",
            "the patched Word body does not have the planned block count",
        )
    for index, (expected, actual) in enumerate(zip(expected_children, output_children)):
        if canonical_element_sha256(expected) != canonical_element_sha256(actual):
            raise SourcePatchError(
                "source",
                "unexpected_body_change",
                f"body child {index} differs from the fail-closed patch plan",
            )
    if output_children and output_children[-1].tag != _W_SECTPR:
        # Word permits a body without a final sectPr, but if one exists it must
        # remain last.  The source inventory comparison above preserves the
        # no-sectPr case; this catches an accidentally displaced one.
        sect_indices = [
            index for index, child in enumerate(output_children) if child.tag == _W_SECTPR
        ]
        if sect_indices:
            raise SourcePatchError(
                "source",
                "section_properties_moved",
                "final Word section properties are no longer the last body child",
            )


def build_source_preserving_docx(
    *,
    source_bytes: bytes,
    source_map: SourceBodyMap,
    baseline: SpecSection,
    current: SpecSection,
) -> bytes:
    """Return an exact no-op or a source clone containing only the safe plan."""
    plan, document_xml, tree, body = _validate_source_and_plan(
        source_bytes=source_bytes,
        source_map=source_map,
        baseline=baseline,
        current=current,
    )
    if plan.no_op:
        return source_bytes

    source_children = _meaningful_children(body)
    for patch in plan.text_patches:
        binding = patch.binding
        original = source_children[binding.body_child_index]
        patched = _element_with_text_patch(original, binding, patch.new_text)
        parent_index = body.index(original)
        patched.tail = original.tail
        body.remove(original)
        body.insert(parent_index, patched)
        source_children[binding.body_child_index] = patched

    # Work from the end of the body toward the start so earlier source
    # insertion positions remain stable. Element references, not live numeric
    # offsets, carry retained paragraphs through a reorder.
    for island_patch in sorted(
        plan.island_patches,
        key=lambda patch: patch.island.start_index,
        reverse=True,
    ):
        original_elements = [
            source_children[member.binding.body_child_index]
            for member in island_patch.island.members
        ]
        insertion_index = body.index(original_elements[0])
        desired_elements: list = []
        for desired in island_patch.desired:
            if desired.binding is not None:
                desired_elements.append(
                    source_children[desired.binding.body_child_index]
                )
            else:
                if desired.template is None:
                    raise SourcePatchError(desired.uid, "ambiguous_structural_template")
                template_element = source_children[desired.template.body_child_index]
                desired_elements.append(
                    _minimal_numbered_paragraph(
                        template_element,
                        desired.uid,
                        desired.text,
                    )
                )
        for element in original_elements:
            body.remove(element)
        for offset, element in enumerate(desired_elements):
            body.insert(insertion_index + offset, element)

    patched_xml = _serialize_tree(tree, document_xml)
    output = _clone_with_document_xml(source_bytes, patched_xml)
    _audit_package_preservation(source_bytes, output, plan)
    return output


__all__ = [
    "SourcePatchError",
    "SourcePatchIssue",
    "SourcePatchReadiness",
    "build_source_preserving_docx",
    "source_patch_readiness",
    "validate_source_transition",
    "validate_source_map_identity",
]
