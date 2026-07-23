"""Fail-closed, source-preserving DOCX export for imported specifications.

The source package is immutable.  A no-op returns its exact bytes.  A P1a
text edit clones every ZIP member and replaces only ``word/document.xml``;
within that part it changes only the text of the one ``w:t`` selected by an
import-time anchor.  No python-docx save occurs on this path.
"""
from __future__ import annotations

import copy
import hashlib
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
_W_R = f"{{{_W_NS}}}r"
_W_RPR = f"{{{_W_NS}}}rPr"
_W_T = f"{{{_W_NS}}}t"
_W_SECTPR = f"{{{_W_NS}}}sectPr"


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
class _PatchPlan:
    patches: tuple[_TextPatch, ...]

    @property
    def no_op(self) -> bool:
        return not self.patches

    @property
    def changed_uids(self) -> tuple[str, ...]:
        return tuple(patch.binding.uid for patch in self.patches)


def _xml_parser() -> etree.XMLParser:
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        remove_blank_text=False,
        recover=False,
    )


def _meaningful_children(element) -> list:
    return [child for child in element.iterchildren() if isinstance(child.tag, str)]


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


def _first_projection_difference(
    baseline: SpecSection,
    current: SpecSection,
) -> tuple[str, str] | None:
    """Return ``(uid, blocker)`` for a non-text P1a difference."""
    base_rows = semantic_body_projection(baseline)
    cur_rows = semantic_body_projection(current)
    for index in range(max(len(base_rows), len(cur_rows))):
        if index >= len(base_rows):
            return cur_rows[index][1], "structural_change"
        if index >= len(cur_rows):
            return base_rows[index][1], "structural_change"
        base = base_rows[index]
        cur = cur_rows[index]
        # kind / uid / parent define structure and order.
        if base[:3] != cur[:3]:
            return (cur[1] if cur else base[1]), "structural_change"
        if base[0] != "paragraph" and base[3:] != cur[3:]:
            return base[1], "heading_change"
    return None


def _changed_paragraph_texts(
    baseline: SpecSection,
    current: SpecSection,
) -> list[tuple[str, str, str]]:
    changed: list[tuple[str, str, str]] = []
    for base, cur in zip(
        semantic_body_projection(baseline),
        semantic_body_projection(current),
    ):
        if base[0] == "paragraph" and base[3] != cur[3]:
            changed.append((base[1], base[3], cur[3]))
    return changed


def _validate_text_for_single_word_node(uid: str, text: str) -> None:
    blocker = source_replacement_text_blocker(text)
    if blocker is not None:
        raise SourcePatchError(uid, blocker)


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
    such a document byte-for-byte even though P1a must not patch it.
    """
    _validated_source_identity(
        source_bytes=source_bytes,
        source_map=source_map,
        baseline=baseline,
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

    structure_problem = _first_projection_difference(baseline, current)
    if structure_problem is not None:
        raise SourcePatchError(*structure_problem)

    patches: list[_TextPatch] = []
    for uid, baseline_text, current_text in _changed_paragraph_texts(
        baseline, current
    ):
        binding = source_map.bindings.get(uid)
        if binding is None:
            raise SourcePatchError(uid, "unmapped_paragraph")
        if binding.baseline_text != baseline_text:
            raise SourcePatchError(
                uid,
                "baseline_mismatch",
                "the paragraph baseline does not match its source binding",
            )
        if not binding.editable:
            blocker = binding.blockers[0] if binding.blockers else "unmapped_paragraph"
            raise SourcePatchError(uid, blocker)
        children = _meaningful_children(body)
        if not 0 <= binding.body_child_index < len(children):
            raise SourcePatchError(uid, "body_anchor_mismatch")
        anchored_element = children[binding.body_child_index]
        expected = bind_source_paragraph(
            uid=uid,
            body_child_index=binding.body_child_index,
            element=anchored_element,
            source_visible_text=binding.source_visible_text,
            baseline_text=baseline_text,
        )
        if (
            expected.element_c14n_sha256 != binding.element_c14n_sha256
            or expected.text_span != binding.text_span
            or expected.blockers != binding.blockers
            or expected.emits_from_source != binding.emits_from_source
        ):
            raise SourcePatchError(
                uid,
                "source_map_mismatch",
                "the paragraph's persisted eligibility does not match its source XML",
            )
        _validate_text_for_single_word_node(uid, current_text)
        patches.append(_TextPatch(binding=binding, new_text=current_text))

    # Exact semantic no-ops return the original bytes even for signed,
    # protected, or revision-bearing sources.  Those features only block an
    # actual mutation; pass-through cannot invalidate or reinterpret them.
    if patches:
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
                patches[0].binding.uid,
                actual_global_blockers[0],
            )

    return _PatchPlan(tuple(patches)), document_xml, tree, body


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


def _audit_package_preservation(
    source_bytes: bytes,
    output_bytes: bytes,
    changed_body_indices: set[int],
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

    _source_tree, source_body = _parse_document_xml(source_parts[_DOCUMENT_PART])
    _output_tree, output_body = _parse_document_xml(output_parts[_DOCUMENT_PART])
    source_children = _meaningful_children(source_body)
    output_children = _meaningful_children(output_body)
    if len(source_children) != len(output_children):
        raise SourcePatchError(
            "source",
            "body_structure_changed",
            "the patched Word body gained or lost XML blocks",
        )
    for index, (before, after) in enumerate(zip(source_children, output_children)):
        if index in changed_body_indices:
            before_ppr = before.find(_W_PPR)
            after_ppr = after.find(_W_PPR)
            before_r = before.find(_W_R)
            after_r = after.find(_W_R)
            before_rpr = before_r.find(_W_RPR) if before_r is not None else None
            after_rpr = after_r.find(_W_RPR) if after_r is not None else None
            for old, new, label in (
                (before_ppr, after_ppr, "pPr"),
                (before_rpr, after_rpr, "rPr"),
            ):
                if (old is None) != (new is None) or (
                    old is not None
                    and canonical_element_sha256(old)
                    != canonical_element_sha256(new)
                ):
                    raise SourcePatchError(
                        "source",
                        "formatting_changed",
                        f"target paragraph {label} formatting changed",
                    )
        elif canonical_element_sha256(before) != canonical_element_sha256(after):
            raise SourcePatchError(
                "source",
                "untouched_body_changed",
                f"untouched body child {index} changed",
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
    """Return an exact source no-op or a clone with safe ``w:t`` patches."""
    plan, document_xml, tree, body = _validate_source_and_plan(
        source_bytes=source_bytes,
        source_map=source_map,
        baseline=baseline,
        current=current,
    )
    if plan.no_op:
        return source_bytes

    children = _meaningful_children(body)
    changed_indices: set[int] = set()
    for patch in plan.patches:
        binding = patch.binding
        element = children[binding.body_child_index]
        if element.tag != _W_P:
            raise SourcePatchError(binding.uid, "body_anchor_mismatch")
        if canonical_element_sha256(element) != binding.element_c14n_sha256:
            raise SourcePatchError(binding.uid, "body_anchor_mismatch")
        texts = element.findall(f".//{_W_T}")
        span = binding.text_span
        if span is None or span.text_node_ordinal >= len(texts):
            raise SourcePatchError(binding.uid, "text_anchor_mismatch")
        text_node = texts[span.text_node_ordinal]
        if (text_node.text or "") != span.source_node_text:
            raise SourcePatchError(binding.uid, "text_anchor_mismatch")
        text_node.text = span.prefix + patch.new_text + span.suffix
        changed_indices.add(binding.body_child_index)

    patched_xml = _serialize_tree(tree, document_xml)
    output = _clone_with_document_xml(source_bytes, patched_xml)
    _audit_package_preservation(source_bytes, output, changed_indices)
    return output


__all__ = [
    "SourcePatchError",
    "SourcePatchIssue",
    "SourcePatchReadiness",
    "build_source_preserving_docx",
    "source_patch_readiness",
    "validate_source_map_identity",
]
