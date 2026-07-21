"""The SectionFormat document tree and its edit machinery.

One :class:`SpecSection` per project: three fixed parts (PART 1 - GENERAL /
PART 2 - PRODUCTS / PART 3 - EXECUTION) holding articles holding nested
paragraphs. Element ids are **stable**: every container carries a
monotonic sequence counter, so ``pt1.a2.p3`` never changes meaning when
siblings are inserted or deleted — display numbering (1.1 / A. / 1. / a. /
1)) is derived from position at serialization time instead.

Edits arrive as ``apply_spec_edits`` op lists and are applied
**transactionally**: the batch runs against a working copy and the live
tree is swapped only if every op validates, so a failed batch is a no-op
the model can retry after reading the error.

Provenance is per paragraph: ``confirmed`` (user-supplied or approved),
``assumed`` (model default, audited later via the export's assumptions
schedule), ``needs_input`` (placeholder awaiting an answer). ``[TBD: ...]``
markers inside paragraph text are tracked as first-class open items
alongside ``needs_input`` blocks.

The id scheme is a generative cousin of Spec Critic's stable review ids
(``p7`` / ``t0r2``), grown hierarchical for a mutable tree.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Iterator

from ..project_profile import (
    ProjectProfile,
    normalize_country,
    normalize_state_or_province,
)
from ..standards import normalize_standard_name, validate_overrides_shape

STATUSES = ("confirmed", "assumed", "needs_input", "imported")
# An op that omits status gets "assumed": over-flagging for the reviewer is
# safer than silently confirming a model guess. "imported" (Phase 5) marks
# master-spec content not yet reviewed against this project — the
# gap-and-adapt interview upgrades it to confirmed/assumed (or deletes it)
# article by article; remaining imported blocks are scheduled in the export.
DEFAULT_STATUS = "assumed"

PART_TITLES = ("PART 1 - GENERAL", "PART 2 - PRODUCTS", "PART 3 - EXECUTION")

# A. -> 1. -> a. -> 1)  (SectionFormat paragraph levels)
MAX_PARAGRAPH_DEPTH = 4

TBD_RE = re.compile(r"\[TBD:\s*([^\]]*)\]")


class SpecEditError(ValueError):
    """A malformed or inapplicable edit op. The whole batch is rejected."""


# ---------------------------------------------------------------------------
# Tree nodes
# ---------------------------------------------------------------------------


@dataclass
class Paragraph:
    uid: str
    text: str
    status: str = DEFAULT_STATUS
    children: list["Paragraph"] = field(default_factory=list)
    next_seq: int = 1  # id counter for children; never rewinds on delete
    # Optional provenance link to the research item that motivated this
    # block (Phase 4): a ``r-…`` RequirementsProfile item id. Advisory —
    # the panel renders a citation chip; nothing validates existence
    # (research can be re-run, items re-minted).
    source_item_id: str = ""


@dataclass
class Article:
    uid: str
    title: str
    paragraphs: list[Paragraph] = field(default_factory=list)
    next_seq: int = 1


@dataclass
class Part:
    uid: str  # pt1 / pt2 / pt3, fixed
    number: int  # 1 / 2 / 3
    title: str
    articles: list[Article] = field(default_factory=list)
    next_seq: int = 1


@dataclass
class SpecSection:
    number: str = ""  # e.g. "21 13 13"
    title: str = ""  # e.g. "WET-PIPE SPRINKLER SYSTEMS"
    parts: list[Part] = field(default_factory=list)
    # Jurisdiction-adopted standard editions recorded for this project:
    # {canonical name: {"edition": "2019", "basis": "2021 VCC ..."}}. Part
    # of the tree on purpose — overrides ride the same transactional
    # apply / per-turn versioning / undo / project-file machinery as text.
    edition_overrides: dict[str, dict[str, str]] = field(default_factory=dict)
    # Project identity recorded through set_project_profile (Phase 4):
    # {"city", "state_or_province", "country", "client_name"} — the
    # ProjectProfile dict shape. On the tree for the same reason as
    # edition_overrides: transactional, undoable, persisted for free.
    project_profile: dict[str, str] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> "SpecSection":
        return cls(
            parts=[
                Part(uid=f"pt{i}", number=i, title=PART_TITLES[i - 1])
                for i in (1, 2, 3)
            ]
        )

    def is_empty(self) -> bool:
        return (
            not self.number
            and not self.title
            and not self.edition_overrides
            and not self.project_profile
            and not any(part.articles for part in self.parts)
        )

    # -- serialization ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "section": {"number": self.number, "title": self.title},
            "parts": [_part_to_dict(part) for part in self.parts],
            "edition_overrides": copy.deepcopy(self.edition_overrides),
            "project_profile": dict(self.project_profile),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SpecSection":
        try:
            section = data["section"]
            parts = [_part_from_dict(p) for p in data["parts"]]
            if [p.uid for p in parts] != ["pt1", "pt2", "pt3"]:
                raise ValueError("expected exactly parts pt1, pt2, pt3")
            overrides = validate_overrides_shape(
                data.get("edition_overrides")
            )
            profile = _validate_profile_shape(data.get("project_profile"))
            result = cls(
                number=str(section.get("number", "")),
                title=str(section.get("title", "")),
                parts=parts,
                edition_overrides=overrides,
                project_profile=profile,
            )
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError(f"Malformed document data: {exc}") from exc
        except ValueError as exc:
            raise ValueError(f"Malformed document data: {exc}") from exc
        _check_integrity(result)
        return result


_PROFILE_FIELDS = ("city", "state_or_province", "country", "client_name")


def _validate_profile_shape(data: Any) -> dict[str, str]:
    """Validate/normalize a persisted ``project_profile`` mapping."""
    if data in (None, {}):
        return {}
    if not isinstance(data, dict):
        raise ValueError("project_profile must be an object")
    clean: dict[str, str] = {}
    for key in _PROFILE_FIELDS:
        value = data.get(key, "")
        if not isinstance(value, str):
            raise ValueError(f"project_profile.{key} must be a string")
        if value.strip():
            clean[key] = value.strip()
    unknown = set(data) - set(_PROFILE_FIELDS)
    if unknown:
        raise ValueError(
            f"project_profile has unknown fields: {sorted(unknown)}"
        )
    return clean


def _paragraph_label(depth: int, index: int) -> str:
    """SectionFormat label for a paragraph at ``depth``, 0-based ``index``."""
    if depth == 0:  # A. B. ... Z. AA. AB. ...
        letters = ""
        i = index
        while True:
            letters = chr(ord("A") + i % 26) + letters
            i = i // 26 - 1
            if i < 0:
                break
        return f"{letters}."
    if depth == 1:
        return f"{index + 1}."
    if depth == 2:
        letters = ""
        i = index
        while True:
            letters = chr(ord("a") + i % 26) + letters
            i = i // 26 - 1
            if i < 0:
                break
        return f"{letters}."
    return f"{index + 1})"


def _paragraph_to_dict(p: Paragraph, depth: int, index: int) -> dict[str, Any]:
    return {
        "id": p.uid,
        "label": _paragraph_label(depth, index),
        "text": p.text,
        "status": p.status,
        "source_item_id": p.source_item_id,
        "children": [
            _paragraph_to_dict(c, depth + 1, i) for i, c in enumerate(p.children)
        ],
        "seq": p.next_seq,
    }


def _article_to_dict(a: Article, part_number: int, index: int) -> dict[str, Any]:
    return {
        "id": a.uid,
        "number": f"{part_number}.{index + 1}",
        "title": a.title,
        "paragraphs": [
            _paragraph_to_dict(p, 0, i) for i, p in enumerate(a.paragraphs)
        ],
        "seq": a.next_seq,
    }


def _part_to_dict(part: Part) -> dict[str, Any]:
    return {
        "id": part.uid,
        "number": part.number,
        "title": part.title,
        "articles": [
            _article_to_dict(a, part.number, i)
            for i, a in enumerate(part.articles)
        ],
        "seq": part.next_seq,
    }


def _paragraph_from_dict(data: dict[str, Any]) -> Paragraph:
    status = data.get("status", DEFAULT_STATUS)
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    return Paragraph(
        uid=str(data["id"]),
        text=str(data.get("text", "")),
        status=status,
        children=[_paragraph_from_dict(c) for c in data.get("children", [])],
        next_seq=int(data.get("seq", 1)),
        source_item_id=str(data.get("source_item_id", "") or ""),
    )


def _article_from_dict(data: dict[str, Any]) -> Article:
    return Article(
        uid=str(data["id"]),
        title=str(data.get("title", "")),
        paragraphs=[_paragraph_from_dict(p) for p in data.get("paragraphs", [])],
        next_seq=int(data.get("seq", 1)),
    )


def _part_from_dict(data: dict[str, Any]) -> Part:
    return Part(
        uid=str(data["id"]),
        number=int(data["number"]),
        title=str(data.get("title", "")),
        articles=[_article_from_dict(a) for a in data.get("articles", [])],
        next_seq=int(data.get("seq", 1)),
    )


def _check_integrity(section: SpecSection) -> None:
    """Reject trees that violate the id/depth invariants.

    Internally-produced snapshots always pass; this guards the untrusted
    path (loaded project files), where forged or inconsistent data —
    duplicate ids, sequence counters behind existing children, over-deep
    nesting — would let future edits mint colliding ids or target the
    wrong element.
    """
    seen: set[str] = set()

    def claim(uid: str) -> None:
        if uid in seen:
            raise ValueError(f"Malformed document data: duplicate id {uid!r}")
        seen.add(uid)

    def check_paragraphs(
        owner_uid: str, paragraphs: list[Paragraph], next_seq: int, depth: int
    ) -> None:
        if paragraphs and depth >= MAX_PARAGRAPH_DEPTH:
            raise ValueError(
                "Malformed document data: paragraph nesting exceeds "
                f"{MAX_PARAGRAPH_DEPTH} levels under {owner_uid!r}"
            )
        max_seq = 0
        for p in paragraphs:
            match = re.fullmatch(rf"{re.escape(owner_uid)}\.p(\d+)", p.uid)
            if not match:
                raise ValueError(
                    f"Malformed document data: id {p.uid!r} is not a "
                    f"child of {owner_uid!r}"
                )
            claim(p.uid)
            max_seq = max(max_seq, int(match.group(1)))
            check_paragraphs(p.uid, p.children, p.next_seq, depth + 1)
        if next_seq <= max_seq:
            raise ValueError(
                f"Malformed document data: sequence counter of {owner_uid!r} "
                "is behind its children"
            )

    for part in section.parts:
        claim(part.uid)
        max_article_seq = 0
        for article in part.articles:
            match = re.fullmatch(rf"{re.escape(part.uid)}\.a(\d+)", article.uid)
            if not match:
                raise ValueError(
                    f"Malformed document data: id {article.uid!r} is not a "
                    f"child of {part.uid!r}"
                )
            claim(article.uid)
            max_article_seq = max(max_article_seq, int(match.group(1)))
            check_paragraphs(article.uid, article.paragraphs, article.next_seq, 0)
        if part.next_seq <= max_article_seq:
            raise ValueError(
                f"Malformed document data: sequence counter of {part.uid!r} "
                "is behind its children"
            )


# ---------------------------------------------------------------------------
# Lookup / traversal
# ---------------------------------------------------------------------------


def _find(section: SpecSection, uid: str) -> Any:
    """Return the node with ``uid`` (Part / Article / Paragraph) or None."""
    for part in section.parts:
        if part.uid == uid:
            return part
        for article in part.articles:
            if article.uid == uid:
                return article
            stack = list(article.paragraphs)
            while stack:
                p = stack.pop()
                if p.uid == uid:
                    return p
                stack.extend(p.children)
    return None


def _find_paragraph_context(
    section: SpecSection, uid: str
) -> tuple[list[Paragraph], Paragraph, int] | None:
    """Return (owning sibling list, paragraph, depth) for a paragraph uid."""
    for part in section.parts:
        for article in part.articles:
            stack: list[tuple[list[Paragraph], Paragraph, int]] = [
                (article.paragraphs, p, 0) for p in article.paragraphs
            ]
            while stack:
                siblings, p, depth = stack.pop()
                if p.uid == uid:
                    return siblings, p, depth
                stack.extend((p.children, c, depth + 1) for c in p.children)
    return None


def iter_paragraphs(
    section: SpecSection,
) -> Iterator[tuple[Part, Article, Paragraph, int, str]]:
    """Yield (part, article, paragraph, depth, ref) in document order.

    ``ref`` is the human numbering path, e.g. ``1.2.B.1.a`` — the article
    number followed by each paragraph label with punctuation stripped.
    """
    for part in section.parts:
        for a_idx, article in enumerate(part.articles):
            number = f"{part.number}.{a_idx + 1}"

            def walk(
                paragraphs: list[Paragraph], depth: int, prefix: str
            ) -> Iterator[tuple[Part, Article, Paragraph, int, str]]:
                for i, p in enumerate(paragraphs):
                    label = _paragraph_label(depth, i).rstrip(".)")
                    ref = f"{prefix}.{label}"
                    yield part, article, p, depth, ref
                    yield from walk(p.children, depth + 1, ref)

            yield from walk(article.paragraphs, 0, number)


def open_questions(section: SpecSection) -> list[dict[str, Any]]:
    """Derive the open-item list: ``[TBD: ...]`` markers + needs_input blocks."""
    items: list[dict[str, Any]] = []
    for _part, _article, p, _depth, ref in iter_paragraphs(section):
        if p.status == "needs_input":
            items.append(
                {
                    "id": f"{p.uid}#needs_input",
                    "element_id": p.uid,
                    "ref": ref,
                    "kind": "needs_input",
                    "label": p.text.strip()[:160],
                }
            )
        for i, match in enumerate(TBD_RE.finditer(p.text)):
            items.append(
                {
                    "id": f"{p.uid}#tbd{i}",
                    "element_id": p.uid,
                    "ref": ref,
                    "kind": "tbd",
                    "label": match.group(1).strip() or "unspecified",
                }
            )
    return items


def outline(section: SpecSection, *, max_text: int | None = 160) -> str:
    """Plain-text rendering with ids — the model's map of the doc.

    ``max_text`` truncates each paragraph (the compact form used in tool
    results); ``None`` renders every paragraph's full text — the form the
    per-turn PROJECT CONTEXT block carries so the model always sees the
    entire document it is editing. Paragraphs drafted from a research item
    carry a ``◆item-id`` provenance chip next to the status.
    """
    if section.is_empty():
        return "(document is empty — no section header or articles yet)"
    lines = [
        f"SECTION {section.number or '[not set]'} — "
        f"{section.title or '[not set]'}  [id: sec]"
    ]
    for part in section.parts:
        lines.append(f"{part.title}  [id: {part.uid}]")
        if not part.articles:
            lines.append("  (no articles)")
            continue
        for a_idx, article in enumerate(part.articles):
            lines.append(
                f"  {part.number}.{a_idx + 1} {article.title}"
                f"  [id: {article.uid}]"
            )

            def walk(paragraphs: list[Paragraph], depth: int) -> None:
                for i, p in enumerate(paragraphs):
                    text = " ".join(p.text.split())
                    if max_text is not None and len(text) > max_text:
                        text = text[: max_text - 1] + "…"
                    indent = "    " + "  " * depth
                    label = _paragraph_label(depth, i)
                    source = (
                        f" ◆{p.source_item_id}" if p.source_item_id else ""
                    )
                    lines.append(
                        f"{indent}{label} ({p.status}{source}) {text}"
                        f"  [id: {p.uid}]"
                    )
                    walk(p.children, depth + 1)

            walk(article.paragraphs, 0)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Edit ops
# ---------------------------------------------------------------------------

_ACTIONS = (
    "add_article",
    "add_paragraph",
    "replace",
    "delete",
    "set_standard_edition",
    "set_project_profile",
)

# set_project_profile op field -> stored ProjectProfile field.
_PROFILE_OP_FIELDS = {
    "city": "city",
    "state": "state_or_province",
    "country": "country",
    "client": "client_name",
}


def _require_text(op: dict[str, Any], what: str) -> str:
    text = op.get("text")
    if not isinstance(text, str) or not text.strip():
        raise SpecEditError(f"{op['action']}: non-empty 'text' ({what}) is required.")
    return text.strip()


def _opt_status(op: dict[str, Any]) -> str | None:
    status = op.get("status")
    if status is None:
        return None
    if status not in STATUSES:
        raise SpecEditError(
            f"Unknown status {status!r}; expected one of {', '.join(STATUSES)}."
        )
    return status


def _opt_source_item_id(op: dict[str, Any]) -> str | None:
    """Optional research-item provenance; ``None`` = not supplied."""
    value = op.get("source_item_id")
    if value is None:
        return None
    if not isinstance(value, str):
        raise SpecEditError("'source_item_id' must be a string.")
    return value.strip()


def _insert(items: list[Any], item: Any, position: Any) -> None:
    if position is None:
        items.append(item)
        return
    if not isinstance(position, int) or isinstance(position, bool):
        raise SpecEditError("'position' must be an integer index (0-based).")
    items.insert(max(0, min(position, len(items))), item)


def _paragraph_depth(section: SpecSection, uid: str) -> int:
    ctx = _find_paragraph_context(section, uid)
    assert ctx is not None
    return ctx[2]


def _apply_one(section: SpecSection, op: dict[str, Any]) -> dict[str, Any]:
    """Apply a single validated op to ``section``; return the applied record."""
    if not isinstance(op, dict):
        raise SpecEditError("Each edit must be an object.")
    action = op.get("action")
    if action not in _ACTIONS:
        raise SpecEditError(
            f"Unknown action {action!r}; expected one of {', '.join(_ACTIONS)}."
        )
    target_id = op.get("target_id")
    if not isinstance(target_id, str) or not target_id:
        raise SpecEditError(f"{action}: 'target_id' is required.")

    # -- jurisdiction edition overrides: section-level metadata ------------
    if action == "set_standard_edition":
        if target_id != "sec":
            raise SpecEditError(
                "set_standard_edition: target_id must be 'sec' (overrides "
                "are section-level metadata)."
            )
        standard = normalize_standard_name(str(op.get("standard") or ""))
        if not standard:
            raise SpecEditError(
                "set_standard_edition: non-empty 'standard' (the "
                "designation, e.g. 'NFPA 13') is required."
            )
        edition = str(op.get("edition") or "").strip()
        if not edition:
            # Empty/omitted edition removes a recorded override (reverting
            # to the module default).
            if standard not in section.edition_overrides:
                raise SpecEditError(
                    f"set_standard_edition: no override recorded for "
                    f"{standard!r} to remove."
                )
            del section.edition_overrides[standard]
            return {
                "action": "set_standard_edition",
                "id": "sec",
                "standard": standard,
                "removed": True,
            }
        if len(edition) > 20:
            raise SpecEditError(
                "set_standard_edition: 'edition' looks malformed "
                "(20 chars max, e.g. '2019')."
            )
        basis = str(op.get("basis") or "").strip()
        if not basis:
            raise SpecEditError(
                "set_standard_edition: non-empty 'basis' is required — "
                "state the adoption that makes this edition govern (e.g. "
                "'2021 VCC per user, Loudoun County VA'). Never record an "
                "edition override silently."
            )
        section.edition_overrides[standard] = {
            "edition": edition,
            "basis": basis,
        }
        return {
            "action": "set_standard_edition",
            "id": "sec",
            "standard": standard,
            "edition": edition,
        }

    # -- project profile: section-level metadata ---------------------------
    if action == "set_project_profile":
        if target_id != "sec":
            raise SpecEditError(
                "set_project_profile: target_id must be 'sec' (the profile "
                "is section-level metadata)."
            )
        provided = {
            op_field: op[op_field]
            for op_field in _PROFILE_OP_FIELDS
            if op_field in op and op[op_field] is not None
        }
        if not provided:
            raise SpecEditError(
                "set_project_profile: provide at least one of 'city', "
                "'state', 'country', 'client'."
            )
        updated = dict(section.project_profile)
        for op_field, raw in provided.items():
            if not isinstance(raw, str):
                raise SpecEditError(
                    f"set_project_profile: '{op_field}' must be a string."
                )
            stored_key = _PROFILE_OP_FIELDS[op_field]
            value = raw.strip()
            if not value:
                # Explicit empty string clears the field.
                updated.pop(stored_key, None)
                continue
            if op_field == "country":
                normalized = normalize_country(value)
                if not normalized:
                    raise SpecEditError(
                        f"set_project_profile: unrecognized country {value!r} "
                        "(this build supports US and Canada — e.g. 'USA')."
                    )
                value = normalized
            elif op_field == "state":
                value = normalize_state_or_province(value)
            updated[stored_key] = value
        section.project_profile = updated
        profile = ProjectProfile.from_dict(updated)
        return {
            "action": "set_project_profile",
            "id": "sec",
            "complete": bool(profile and profile.is_complete()),
        }

    # -- section header: replace target "sec" ------------------------------
    if target_id == "sec":
        if action != "replace":
            raise SpecEditError(
                "Target 'sec' only supports 'replace' (set the section "
                "title via 'text' and/or the section number via 'numbering')."
            )
        title = op.get("text")
        number = op.get("numbering")
        if title is None and number is None:
            raise SpecEditError(
                "replace sec: provide 'text' (section title) and/or "
                "'numbering' (section number, e.g. '21 13 13')."
            )
        if title is not None:
            section.title = str(title).strip()
        if number is not None:
            section.number = str(number).strip()
        return {"action": "replace", "id": "sec"}

    node = _find(section, target_id)
    if node is None:
        raise SpecEditError(
            f"{action}: no element with id {target_id!r}. Check the current "
            "document outline for valid ids."
        )

    if action == "add_article":
        if not isinstance(node, Part):
            raise SpecEditError(
                "add_article: target must be a part id (pt1, pt2, or pt3)."
            )
        title = _require_text(op, "article title")
        article = Article(uid=f"{node.uid}.a{node.next_seq}", title=title)
        node.next_seq += 1
        _insert(node.articles, article, op.get("position"))
        return {"action": "add_article", "id": article.uid, "target_id": target_id}

    if action == "add_paragraph":
        text = _require_text(op, "paragraph text")
        status = _opt_status(op) or DEFAULT_STATUS
        if isinstance(node, Article):
            parent_seq_owner = node
            siblings = node.paragraphs
        elif isinstance(node, Paragraph):
            if _paragraph_depth(section, node.uid) + 1 >= MAX_PARAGRAPH_DEPTH:
                raise SpecEditError(
                    "add_paragraph: maximum nesting depth reached "
                    f"({MAX_PARAGRAPH_DEPTH} levels: A. / 1. / a. / 1))."
                )
            parent_seq_owner = node
            siblings = node.children
        else:
            raise SpecEditError(
                "add_paragraph: target must be an article id (to add a "
                "top-level paragraph) or a paragraph id (to nest under it)."
            )
        paragraph = Paragraph(
            uid=f"{node.uid}.p{parent_seq_owner.next_seq}",
            text=text,
            status=status,
            source_item_id=_opt_source_item_id(op) or "",
        )
        parent_seq_owner.next_seq += 1
        _insert(siblings, paragraph, op.get("position"))
        return {
            "action": "add_paragraph",
            "id": paragraph.uid,
            "target_id": target_id,
            "status": status,
        }

    if action == "replace":
        if isinstance(node, Article):
            node.title = _require_text(op, "article title")
            return {"action": "replace", "id": node.uid}
        if isinstance(node, Paragraph):
            text = op.get("text")
            status = _opt_status(op)
            source_item_id = _opt_source_item_id(op)
            if text is None and status is None and source_item_id is None:
                raise SpecEditError(
                    "replace: provide 'text', 'status', and/or "
                    "'source_item_id' for a paragraph."
                )
            if text is not None:
                if not isinstance(text, str) or not text.strip():
                    raise SpecEditError("replace: 'text' must be non-empty.")
                node.text = text.strip()
            if status is not None:
                node.status = status
            if source_item_id is not None:
                # Empty string clears the provenance link.
                node.source_item_id = source_item_id
            return {"action": "replace", "id": node.uid, "status": node.status}
        raise SpecEditError("replace: target must be an article or paragraph.")

    # action == "delete"
    if isinstance(node, Article):
        for part in section.parts:
            if node in part.articles:
                part.articles.remove(node)
                return {"action": "delete", "id": node.uid}
    if isinstance(node, Paragraph):
        ctx = _find_paragraph_context(section, node.uid)
        assert ctx is not None
        ctx[0].remove(node)
        return {"action": "delete", "id": node.uid}
    raise SpecEditError("delete: target must be an article or paragraph.")


def apply_edits(
    section: SpecSection, edits: Any
) -> tuple[SpecSection, list[dict[str, Any]]]:
    """Apply an op batch transactionally.

    Returns ``(new_section, applied_ops)``. Raises :class:`SpecEditError`
    without touching ``section`` if any op is invalid.
    """
    if not isinstance(edits, list) or not edits:
        raise SpecEditError("'edits' must be a non-empty list of operations.")
    candidate = copy.deepcopy(section)
    applied = [_apply_one(candidate, op) for op in edits]
    return candidate, applied


# ---------------------------------------------------------------------------
# Store: live doc + per-turn version history
# ---------------------------------------------------------------------------


class DocumentStore:
    """The session's document plus its per-turn snapshot history.

    ``versions[0]`` is the empty document; every turn that changed the doc
    appends one snapshot at commit. Undo/redo move ``index`` along that
    list; a new edit after undo truncates the redo tail (so ids can never
    collide with an abandoned future). Turn semantics mirror the
    conversation-history invariant: mutations during a turn are provisional
    until :meth:`commit_turn`, and :meth:`rollback_turn` restores the
    pre-turn tree on any failure so a resend is safe.
    """

    def __init__(self) -> None:
        self.doc = SpecSection.empty()
        self.versions: list[dict[str, Any]] = [self.doc.to_dict()]
        self.index = 0
        self._turn_backup: dict[str, Any] | None = None
        self._dirty = False

    # -- snapshots ----------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        data = self.doc.to_dict()
        data["version"] = {"index": self.index, "count": len(self.versions)}
        return data

    # -- turn lifecycle -----------------------------------------------------

    def begin_turn(self) -> None:
        if self._turn_backup is not None:
            # A previous turn never completed (abandoned mid-stream) —
            # restore its pre-turn tree before starting fresh.
            self.rollback_turn()
        self._turn_backup = self.doc.to_dict()
        self._dirty = False

    def apply_edits(self, edits: Any) -> list[dict[str, Any]]:
        self.doc, applied = apply_edits(self.doc, edits)
        self._dirty = True
        return applied

    def commit_turn(self) -> bool:
        """Snapshot the turn's changes; returns whether the doc changed."""
        changed = self._dirty
        if changed:
            del self.versions[self.index + 1 :]
            self.versions.append(self.doc.to_dict())
            self.index += 1
        self._turn_backup = None
        self._dirty = False
        return changed

    def adopt_imported(self, section: SpecSection) -> None:
        """Adopt a master-spec import as the document, as one version.

        The caller (the import endpoint) enforces that the current document
        is empty — an import is a *starting point*, never a merge. The
        empty version stays at index 0, so one undo steps back to the
        blank page. Refuses mid-turn adoption (an in-flight turn owns the
        tree until it commits or rolls back).
        """
        if self._turn_backup is not None:
            raise ValueError("Cannot import while a turn is in progress.")
        # Validate before adopting anything (from_dict runs integrity).
        snapshot = section.to_dict()
        SpecSection.from_dict(snapshot)
        self.doc = section
        del self.versions[self.index + 1 :]
        self.versions.append(snapshot)
        self.index += 1
        self._dirty = False

    def rollback_turn(self) -> None:
        if self._turn_backup is not None:
            self.doc = SpecSection.from_dict(self._turn_backup)
        self._turn_backup = None
        self._dirty = False

    # -- version stepper ----------------------------------------------------

    def can_undo(self) -> bool:
        return self.index > 0

    def can_redo(self) -> bool:
        return self.index < len(self.versions) - 1

    def undo(self) -> bool:
        if not self.can_undo():
            return False
        self.index -= 1
        self.doc = SpecSection.from_dict(self.versions[self.index])
        return True

    def redo(self) -> bool:
        if not self.can_redo():
            return False
        self.index += 1
        self.doc = SpecSection.from_dict(self.versions[self.index])
        return True

    # -- persistence --------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {"versions": self.versions, "index": self.index}

    def load(self, data: dict[str, Any]) -> None:
        versions = data.get("versions")
        index = data.get("index")
        if (
            not isinstance(versions, list)
            or not versions
            or not isinstance(index, int)
            or not 0 <= index < len(versions)
        ):
            raise ValueError("Malformed document history.")
        # Validate every snapshot before adopting any of it.
        parsed = [SpecSection.from_dict(v) for v in versions]
        self.versions = versions
        self.index = index
        self.doc = parsed[index]
        self._turn_backup = None
        self._dirty = False

    def reset(self) -> None:
        self.__init__()


# ---------------------------------------------------------------------------
# Tool definition (registered in conversation._TOOLS)
# ---------------------------------------------------------------------------

APPLY_SPEC_EDITS_TOOL: dict[str, Any] = {
    "name": "apply_spec_edits",
    "description": (
        "Apply a batch of edits to the live specification document shown "
        "beside the chat. The whole batch is transactional: if any "
        "operation is invalid, none are applied and the error tells you "
        "why — fix the batch and call again.\n"
        "\n"
        "Element ids are stable and hierarchical: parts are pt1/pt2/pt3 "
        "(fixed); articles are like pt1.a2; paragraphs are like pt1.a2.p3 "
        "(nested: pt1.a2.p3.p1). New ids are assigned by the server and "
        "returned in the result. Display numbering (1.1, A., 1., a., 1)) "
        "is derived from position automatically — the optional 'numbering' "
        "field is only used when setting the section number.\n"
        "\n"
        "Operations:\n"
        "- add_article: target_id = a part id; text = the article title.\n"
        "- add_paragraph: target_id = an article id (top-level paragraph) "
        "or a paragraph id (nested subparagraph, max 4 levels); text = the "
        "provision text; status = confirmed | assumed | needs_input "
        "(defaults to assumed).\n"
        "- replace: target_id = an article (text = new title), a paragraph "
        "(text and/or status), or 'sec' to set the section header (text = "
        "section title, numbering = section number like '21 13 13').\n"
        "- delete: target_id = an article or paragraph id.\n"
        "- set_standard_edition: target_id = 'sec'; standard = the "
        "designation (e.g. 'NFPA 13'); edition = the jurisdiction-adopted "
        "edition (e.g. '2019'); basis = the stated adoption that makes it "
        "govern (required — overrides are never recorded silently). Use "
        "ONLY when the user states the adoption or a grounded research "
        "item provides it (then cite the item id in the basis, e.g. "
        "'research r-1a2b3c4d5e6f: 2021 VCC') — never from your own "
        "assumption. Omit 'edition' to remove a recorded override and "
        "revert to the module default. The editions in effect are listed "
        "in your context; linting checks the draft against them.\n"
        "- set_project_profile: target_id = 'sec'; record the project "
        "identity as the user states it — city, state (name or 2-letter "
        "code), country ('USA'/'Canada'), client. Provide only the fields "
        "stated; an explicit empty string clears a field. A complete "
        "profile (all four) enables the requirements-research phase.\n"
        "- position (optional, add ops): 0-based insertion index among the "
        "target's children; omit to append.\n"
        "- source_item_id (optional, add_paragraph/replace): when a "
        "research profile item motivates the provision, its item id "
        "(r-...) — the panel then shows the citation. Empty string on "
        "replace clears it.\n"
        "\n"
        "Mark undecided values inline as [TBD: short description] — they "
        "are tracked as open items until resolved."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "edits": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": list(_ACTIONS),
                        },
                        "target_id": {"type": "string"},
                        "position": {"type": "integer"},
                        "text": {"type": "string"},
                        "numbering": {"type": "string"},
                        "status": {
                            "type": "string",
                            "enum": list(STATUSES),
                        },
                        "standard": {"type": "string"},
                        "edition": {"type": "string"},
                        "basis": {"type": "string"},
                        "city": {"type": "string"},
                        "state": {"type": "string"},
                        "country": {"type": "string"},
                        "client": {"type": "string"},
                        "source_item_id": {"type": "string"},
                    },
                    "required": ["action", "target_id"],
                },
            }
        },
        "required": ["edits"],
    },
}
