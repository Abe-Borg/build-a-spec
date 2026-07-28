"""Deterministic write-set planning for applying Final-QC fixes.

QC findings are reviewed independently, but users may accept several at once.
This module turns their operations into one deduplicated batch and rejects
different operations that claim the same logical write location before any
document or audit state is mutated.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any, Iterable

from ..spec_doc.model import SpecSection
from ..standards import normalize_standard_name


@dataclass(frozen=True)
class _WriteKey:
    scope: str
    resource: str
    field: str

    def overlaps(self, other: "_WriteKey") -> bool:
        if self.scope == other.scope and self.resource == other.resource:
            return (
                self.field == other.field
                or self.field == "*"
                or other.field == "*"
            )
        # Replacing/deleting an entire hierarchical element also claims its
        # descendant elements and its child collection. This catches batches
        # such as "add a paragraph to article X" plus "delete article X",
        # regardless of operation order.
        for whole, candidate in ((self, other), (other, self)):
            if whole.scope != "element" or whole.field != "*":
                continue
            if candidate.scope not in ("element", "collection"):
                continue
            if (
                candidate.resource == whole.resource
                or candidate.resource.startswith(f"{whole.resource}.")
            ):
                return True
        return False

    def overlap_label(self, other: "_WriteKey") -> str:
        if self.scope == other.scope and self.resource == other.resource:
            field = self.field if self.field == other.field else "*"
            return f"{self.scope}:{self.resource}:{field}"
        whole = self if self.scope == "element" and self.field == "*" else other
        return f"element:{whole.resource}:*"


@dataclass
class _PlannedOperation:
    operation: dict[str, Any]
    identity: str
    finding_ids: list[str]
    write_keys: tuple[_WriteKey, ...]


_ADDITION_ACTIONS = frozenset({"add_article", "add_paragraph"})


def _is_compatible_addition_overlap(
    left: _PlannedOperation,
    right: _PlannedOperation,
    left_key: _WriteKey,
    right_key: _WriteKey,
    standalone_finding_ids: set[str],
) -> bool:
    """Whether an overlap is only two independent appends to one collection.

    Distinct standalone, positionless additions do not overwrite an existing
    member. Requiring every owner to contain only this one unique operation is
    important: a multi-operation finding may target the generated id it
    expects its own add to receive, which another same-parent add could shift.
    Explicit insertion positions remain conflicts for the same ordering
    reason. Keep the collection key itself so either append also still
    conflicts with a delete or move in the same collection.
    """
    left_action = str(left.operation.get("action") or "")
    right_action = str(right.operation.get("action") or "")
    return bool(
        left_action == right_action
        and left_action in _ADDITION_ACTIONS
        and all(
            finding_id in standalone_finding_ids
            for finding_id in (*left.finding_ids, *right.finding_ids)
        )
        and str(left.operation.get("target_id") or "")
        == str(right.operation.get("target_id") or "")
        and "position" not in left.operation
        and "position" not in right.operation
        and left_key.scope == right_key.scope == "collection"
        and left_key.resource == right_key.resource
        and left_key.field == right_key.field == "members"
    )


@dataclass(frozen=True)
class QCOperationBatch:
    """A unique apply batch plus any conflicts that make it unsafe."""

    operations: tuple[dict[str, Any], ...]
    finding_ids: tuple[str, ...]
    conflicts: tuple[dict[str, Any], ...]


def canonical_qc_operation(operation: dict[str, Any]) -> dict[str, Any]:
    # Tool-schema null placeholders are operationally equivalent to omitted
    # optional fields (and the QC parser normally drops them).  Removing them
    # here makes dedupe stable for restored/manual test records as well.
    return {
        key: copy.deepcopy(value)
        for key, value in operation.items()
        if value is not None
    }


def qc_operation_identity(operation: dict[str, Any]) -> str:
    """Return the stable identity used for exact-operation deduplication."""
    return json.dumps(
        canonical_qc_operation(operation),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _parent_collection(section: SpecSection, target_id: str) -> str:
    """Return the stable id of the collection containing ``target_id``."""
    for part in section.parts:
        for article in part.articles:
            if article.uid == target_id:
                return part.uid

            def walk(paragraphs, parent_id: str) -> str:
                for paragraph in paragraphs:
                    if paragraph.uid == target_id:
                        return parent_id
                    nested = walk(paragraph.children, paragraph.uid)
                    if nested:
                        return nested
                return ""

            parent = walk(article.paragraphs, article.uid)
            if parent:
                return parent
    # Valid QC operations have already been dry-run.  Keep a deterministic
    # fallback for malformed historical/test data without guessing a parent.
    if "." in target_id:
        return target_id.rsplit(".", 1)[0]
    return ""


def _write_keys(
    section: SpecSection, operation: dict[str, Any]
) -> tuple[_WriteKey, ...]:
    action = str(operation.get("action") or "")
    target_id = str(operation.get("target_id") or "")

    if action in ("set_standard_edition", "set_standard_suppressed"):
        standard = normalize_standard_name(
            str(operation.get("standard") or "")
        )
        setting = (
            "edition"
            if action == "set_standard_edition"
            else "suppression"
        )
        return (_WriteKey("standard", standard, setting),)

    if action == "set_status":
        return (_WriteKey("element", target_id, "status"),)

    if action == "replace":
        return (_WriteKey("element", target_id, "*"),)

    if action == "delete":
        keys = [_WriteKey("element", target_id, "*")]
        parent = _parent_collection(section, target_id)
        if parent:
            keys.append(_WriteKey("collection", parent, "members"))
        return tuple(keys)

    if action == "move":
        keys = [_WriteKey("element", target_id, "location")]
        source = _parent_collection(section, target_id)
        if source:
            keys.append(_WriteKey("collection", source, "members"))
        # The current edit vocabulary permits same-parent moves only.  Honor
        # a future explicit destination conservatively without changing that
        # validator contract.
        destination = str(
            operation.get("destination_id")
            or operation.get("new_parent_id")
            or source
        )
        if destination and destination != source:
            keys.append(_WriteKey("collection", destination, "members"))
        return tuple(keys)

    if action in ("add_article", "add_paragraph"):
        return (_WriteKey("collection", target_id, "members"),)

    # QC excludes project-profile writes and validates the action allow-list
    # before this layer. A private identity key keeps unexpected legacy data
    # deterministic without letting it collide with a valid operation.
    return (
        _WriteKey("operation", qc_operation_identity(operation), "*"),
    )


def plan_qc_operation_batch(
    section: SpecSection,
    findings: Iterable[tuple[str, list[dict[str, Any]]]],
) -> QCOperationBatch:
    """Deduplicate identical operations and report write-set conflicts.

    ``findings`` is ordered as selected by the caller.  The first occurrence
    of an identical operation establishes execution order; every owning
    finding is retained so all are marked applied after that single write.
    """
    planned_by_identity: dict[str, _PlannedOperation] = {}
    finding_operation_identities: dict[str, set[str]] = {}
    finding_ids: list[str] = []
    for finding_id, operations in findings:
        if finding_id not in finding_ids:
            finding_ids.append(finding_id)
        identities = finding_operation_identities.setdefault(finding_id, set())
        for raw_operation in operations:
            operation = canonical_qc_operation(raw_operation)
            identity = qc_operation_identity(operation)
            identities.add(identity)
            existing = planned_by_identity.get(identity)
            if existing is not None:
                if finding_id not in existing.finding_ids:
                    existing.finding_ids.append(finding_id)
                continue
            planned_by_identity[identity] = _PlannedOperation(
                operation=operation,
                identity=identity,
                finding_ids=[finding_id],
                write_keys=_write_keys(section, operation),
            )

    planned = list(planned_by_identity.values())
    standalone_finding_ids = {
        finding_id
        for finding_id, identities in finding_operation_identities.items()
        if len(identities) == 1
    }
    conflicts: list[dict[str, Any]] = []
    for left_index, left in enumerate(planned):
        for right in planned[left_index + 1 :]:
            overlaps = sorted(
                {
                    left_key.overlap_label(right_key)
                    for left_key in left.write_keys
                    for right_key in right.write_keys
                    if left_key.overlaps(right_key)
                    and not _is_compatible_addition_overlap(
                        left,
                        right,
                        left_key,
                        right_key,
                        standalone_finding_ids,
                    )
                }
            )
            if not overlaps:
                continue
            involved = list(
                dict.fromkeys([*left.finding_ids, *right.finding_ids])
            )
            conflicts.append(
                {
                    "write_keys": overlaps,
                    "finding_ids": involved,
                    "operations": [
                        copy.deepcopy(left.operation),
                        copy.deepcopy(right.operation),
                    ],
                }
            )

    return QCOperationBatch(
        operations=tuple(
            copy.deepcopy(item.operation) for item in planned
        ),
        finding_ids=tuple(finding_ids),
        conflicts=tuple(conflicts),
    )


__all__ = [
    "QCOperationBatch",
    "canonical_qc_operation",
    "plan_qc_operation_batch",
    "qc_operation_identity",
]
