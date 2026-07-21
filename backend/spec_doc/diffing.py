"""Deterministic version diff over two :class:`SpecSection` trees.

Batch 5 (v1.0.0). Pure, no model, no I/O: ``diff_sections(base, cur)``
joins the two trees by **stable element uid** (never a fuzzy text match —
the whole point of the monotonic-id scheme in ``model.py``) and produces a
flat, document-ordered list of :class:`ElementDiff` rows that both the
tracked-changes ``.docx`` writer and the in-app compare view render from.

Design decisions (frozen in the batch plan):

- Alignment is by uid: in both → ``unchanged`` / ``changed`` (by normalized
  text); cur-only → ``inserted``; base-only → ``deleted`` (positioned at its
  base-order location relative to surviving siblings).
- **Pure moves are NOT marked.** A uid present in both trees at a different
  position is classified by text only (``unchanged``/``changed``); it is
  emitted once, at its cur position. Display numbering is positional and
  recomputes anyway, so marking a move as delete+insert would drown a
  reviewer in noise for zero information. Revisit only if a reviewer asks.
- **Status changes are not content changes.** A block whose text is
  identical but whose provenance status moved (e.g. ``assumed`` →
  ``confirmed``) lands in ``status_changes`` for the in-app view; it never
  produces a redline mark (Word tracks text, and reviewers do not care about
  our provenance mechanics).
- Text runs are **word-level**, not char-level: tokenize keeping trailing
  whitespace attached (``re.findall(r'\\S+\\s*')``) and diff the token lists
  with :class:`difflib.SequenceMatcher`. Char diffs produce unreadable
  confetti in legal-style review. The whitespace-attached tokenization makes
  the reconstruction byte-exact: joining a changed element's non-``del`` runs
  reproduces ``cur_text`` and joining its non-``ins`` runs reproduces
  ``base_text`` (stored provision text is always stripped, so no leading
  whitespace is lost).

``diff_sections`` knows nothing about "the master" — versus-master is just
``base = versions[baseline_index]`` and versus-empty is ``base =
SpecSection.empty()`` (a valid all-insertions redline for a from-scratch
issue).
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Any

from .model import (
    Article,
    Paragraph,
    SpecSection,
    _paragraph_label,
    iter_paragraphs,
)

_TOKEN_RE = re.compile(r"\S+\s*")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class DiffRun:
    """One word-level run inside a ``changed`` element's text."""

    op: str  # "equal" | "ins" | "del"
    text: str

    def to_dict(self) -> dict[str, str]:
        return {"op": self.op, "text": self.text}


@dataclass
class ElementDiff:
    """One renderable row of the diff, in merged document order.

    ``node_type`` picks the renderer (section header / part heading / article
    title / paragraph); ``kind`` is the change class. ``runs`` carries the
    word-level token diff for ``changed`` elements only (``None`` otherwise;
    ``inserted``/``deleted`` are whole-block marks). ``base_text``/``cur_text``
    hold the paragraph text, article title, or section title depending on the
    node type; ``number_base``/``number_cur`` carry the section number for the
    section header row.
    """

    uid: str
    node_type: str  # "section" | "part" | "article" | "paragraph"
    kind: str  # "unchanged" | "changed" | "inserted" | "deleted"
    depth: int = 0
    label: str = ""  # "A." / "1." for a paragraph, "1.1" for an article
    ref_base: str = ""
    ref_cur: str = ""
    base_text: str = ""
    cur_text: str = ""
    runs: list[DiffRun] | None = None
    number_base: str = ""
    number_cur: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "node_type": self.node_type,
            "kind": self.kind,
            "depth": self.depth,
            "label": self.label,
            "ref_base": self.ref_base,
            "ref_cur": self.ref_cur,
            "base_text": self.base_text,
            "cur_text": self.cur_text,
            "runs": (
                [run.to_dict() for run in self.runs]
                if self.runs is not None
                else None
            ),
            "number_base": self.number_base,
            "number_cur": self.number_cur,
        }


@dataclass
class StatusChange:
    """A block whose text is unchanged but whose provenance status moved."""

    uid: str
    ref: str
    status_base: str
    status_cur: str

    def to_dict(self) -> dict[str, str]:
        return {
            "uid": self.uid,
            "ref": self.ref,
            "status_base": self.status_base,
            "status_cur": self.status_cur,
        }


@dataclass
class SectionDiff:
    elements: list[ElementDiff] = field(default_factory=list)
    status_changes: list[StatusChange] = field(default_factory=list)
    stats: dict[str, int] = field(
        default_factory=lambda: {
            "inserted": 0,
            "deleted": 0,
            "changed": 0,
            "unchanged": 0,
        }
    )

    def has_changes(self) -> bool:
        return bool(
            self.stats["inserted"]
            or self.stats["deleted"]
            or self.stats["changed"]
            or self.status_changes
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "elements": [element.to_dict() for element in self.elements],
            "status_changes": [sc.to_dict() for sc in self.status_changes],
            "stats": dict(self.stats),
        }


# ---------------------------------------------------------------------------
# Text runs
# ---------------------------------------------------------------------------


def _tokens(text: str) -> list[str]:
    """Whitespace-attached word tokens: ``"a  b"`` -> ``["a  ", "b"]``."""
    return _TOKEN_RE.findall(text)


def _merge_runs(runs: list[DiffRun]) -> list[DiffRun]:
    """Drop empty-text runs and coalesce adjacent same-op runs."""
    merged: list[DiffRun] = []
    for run in runs:
        if not run.text:
            continue
        if merged and merged[-1].op == run.op:
            merged[-1] = DiffRun(run.op, merged[-1].text + run.text)
        else:
            merged.append(DiffRun(run.op, run.text))
    return merged


def token_runs(base_text: str, cur_text: str) -> list[DiffRun]:
    """Word-level diff of two texts into ``equal``/``ins``/``del`` runs.

    Invariant (stored texts are stripped, so tokenization is loss-free):
    ``"".join(r.text for r in runs if r.op != "del") == cur_text`` and the
    ``!= "ins"`` complement equals ``base_text``.
    """
    a = _tokens(base_text)
    b = _tokens(cur_text)
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    runs: list[DiffRun] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            runs.append(DiffRun("equal", "".join(a[i1:i2])))
        elif tag == "delete":
            runs.append(DiffRun("del", "".join(a[i1:i2])))
        elif tag == "insert":
            runs.append(DiffRun("ins", "".join(b[j1:j2])))
        else:  # replace
            runs.append(DiffRun("del", "".join(a[i1:i2])))
            runs.append(DiffRun("ins", "".join(b[j1:j2])))
    return _merge_runs(runs)


def _norm(text: str) -> str:
    """Whitespace-normalized comparison key (a pure reflow is not a change)."""
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# Element refs (uid -> human numbering path) for both trees
# ---------------------------------------------------------------------------


def _element_refs(section: SpecSection) -> dict[str, str]:
    refs: dict[str, str] = {"sec": "sec"}
    for part in section.parts:
        for a_idx, article in enumerate(part.articles):
            refs[article.uid] = f"{part.number}.{a_idx + 1}"
    for _part, _article, paragraph, _depth, ref in iter_paragraphs(section):
        refs[paragraph.uid] = ref
    return refs


# ---------------------------------------------------------------------------
# Sibling merge (uid join preserving cur order, deleted spliced at base pos)
# ---------------------------------------------------------------------------


def _merge_by_uid(
    base_nodes: list[Any], cur_nodes: list[Any]
) -> list[tuple[str, Any, Any, int, int]]:
    """Merge two sibling lists into one ordered walk.

    Each entry is ``(role, cur_node, base_node, cur_index, base_index)`` with
    ``role`` in ``both`` / ``inserted`` / ``deleted``. Survivors and inserts
    follow cur order; deleted (base-only) nodes are spliced in right after
    the surviving base node that precedes them (or at the front). ``index``
    fields are ``-1`` when not applicable and drive positional labels.
    """
    base_by_uid = {node.uid: (i, node) for i, node in enumerate(base_nodes)}
    cur_uids = {node.uid for node in cur_nodes}

    leading_deleted: list[tuple[int, Any]] = []
    trailing_deleted: dict[str, list[tuple[int, Any]]] = {}
    last_survivor: str | None = None
    for i, node in enumerate(base_nodes):
        if node.uid in cur_uids:
            last_survivor = node.uid
            trailing_deleted.setdefault(last_survivor, [])
        elif last_survivor is None:
            leading_deleted.append((i, node))
        else:
            trailing_deleted[last_survivor].append((i, node))

    out: list[tuple[str, Any, Any, int, int]] = []
    for base_index, node in leading_deleted:
        out.append(("deleted", None, node, -1, base_index))
    for cur_index, node in enumerate(cur_nodes):
        match = base_by_uid.get(node.uid)
        if match is not None:
            base_index, base_node = match
            out.append(("both", node, base_node, cur_index, base_index))
            for del_index, del_node in trailing_deleted.get(node.uid, []):
                out.append(("deleted", None, del_node, -1, del_index))
        else:
            out.append(("inserted", node, None, cur_index, -1))
    return out


# ---------------------------------------------------------------------------
# Recursive diff
# ---------------------------------------------------------------------------


def _diff_paragraphs(
    base_nodes: list[Paragraph],
    cur_nodes: list[Paragraph],
    depth: int,
    base_refs: dict[str, str],
    cur_refs: dict[str, str],
    elements: list[ElementDiff],
    status_changes: list[StatusChange],
) -> None:
    for role, cur_node, base_node, cur_index, base_index in _merge_by_uid(
        base_nodes, cur_nodes
    ):
        if role == "both":
            uid = cur_node.uid
            label = _paragraph_label(depth, cur_index)
            if _norm(base_node.text) != _norm(cur_node.text):
                elements.append(
                    ElementDiff(
                        uid=uid,
                        node_type="paragraph",
                        kind="changed",
                        depth=depth,
                        label=label,
                        ref_base=base_refs.get(uid, ""),
                        ref_cur=cur_refs.get(uid, ""),
                        base_text=base_node.text,
                        cur_text=cur_node.text,
                        runs=token_runs(base_node.text, cur_node.text),
                    )
                )
            else:
                elements.append(
                    ElementDiff(
                        uid=uid,
                        node_type="paragraph",
                        kind="unchanged",
                        depth=depth,
                        label=label,
                        ref_base=base_refs.get(uid, ""),
                        ref_cur=cur_refs.get(uid, ""),
                        base_text=base_node.text,
                        cur_text=cur_node.text,
                    )
                )
                if base_node.status != cur_node.status:
                    status_changes.append(
                        StatusChange(
                            uid=uid,
                            ref=cur_refs.get(uid, ""),
                            status_base=base_node.status,
                            status_cur=cur_node.status,
                        )
                    )
            _diff_paragraphs(
                base_node.children,
                cur_node.children,
                depth + 1,
                base_refs,
                cur_refs,
                elements,
                status_changes,
            )
        elif role == "inserted":
            uid = cur_node.uid
            elements.append(
                ElementDiff(
                    uid=uid,
                    node_type="paragraph",
                    kind="inserted",
                    depth=depth,
                    label=_paragraph_label(depth, cur_index),
                    ref_cur=cur_refs.get(uid, ""),
                    cur_text=cur_node.text,
                )
            )
            _diff_paragraphs(
                [],
                cur_node.children,
                depth + 1,
                base_refs,
                cur_refs,
                elements,
                status_changes,
            )
        else:  # deleted
            uid = base_node.uid
            elements.append(
                ElementDiff(
                    uid=uid,
                    node_type="paragraph",
                    kind="deleted",
                    depth=depth,
                    label=_paragraph_label(depth, base_index),
                    ref_base=base_refs.get(uid, ""),
                    base_text=base_node.text,
                )
            )
            _diff_paragraphs(
                base_node.children,
                [],
                depth + 1,
                base_refs,
                cur_refs,
                elements,
                status_changes,
            )


def _diff_articles(
    base_articles: list[Article],
    cur_articles: list[Article],
    base_refs: dict[str, str],
    cur_refs: dict[str, str],
    elements: list[ElementDiff],
    status_changes: list[StatusChange],
) -> None:
    for role, cur_node, base_node, _cur_index, _base_index in _merge_by_uid(
        base_articles, cur_articles
    ):
        if role == "both":
            uid = cur_node.uid
            changed = base_node.title != cur_node.title
            elements.append(
                ElementDiff(
                    uid=uid,
                    node_type="article",
                    kind="changed" if changed else "unchanged",
                    label=cur_refs.get(uid, ""),
                    ref_base=base_refs.get(uid, ""),
                    ref_cur=cur_refs.get(uid, ""),
                    base_text=base_node.title,
                    cur_text=cur_node.title,
                    runs=(
                        token_runs(base_node.title, cur_node.title)
                        if changed
                        else None
                    ),
                )
            )
            _diff_paragraphs(
                base_node.paragraphs,
                cur_node.paragraphs,
                0,
                base_refs,
                cur_refs,
                elements,
                status_changes,
            )
        elif role == "inserted":
            uid = cur_node.uid
            elements.append(
                ElementDiff(
                    uid=uid,
                    node_type="article",
                    kind="inserted",
                    label=cur_refs.get(uid, ""),
                    ref_cur=cur_refs.get(uid, ""),
                    cur_text=cur_node.title,
                )
            )
            _diff_paragraphs(
                [],
                cur_node.paragraphs,
                0,
                base_refs,
                cur_refs,
                elements,
                status_changes,
            )
        else:  # deleted
            uid = base_node.uid
            elements.append(
                ElementDiff(
                    uid=uid,
                    node_type="article",
                    kind="deleted",
                    label=base_refs.get(uid, ""),
                    ref_base=base_refs.get(uid, ""),
                    base_text=base_node.title,
                )
            )
            _diff_paragraphs(
                base_node.paragraphs,
                [],
                0,
                base_refs,
                cur_refs,
                elements,
                status_changes,
            )


def _section_element(base: SpecSection, cur: SpecSection) -> ElementDiff:
    changed = base.number != cur.number or base.title != cur.title
    return ElementDiff(
        uid="sec",
        node_type="section",
        kind="changed" if changed else "unchanged",
        ref_base="sec",
        ref_cur="sec",
        base_text=base.title,
        cur_text=cur.title,
        runs=(
            token_runs(base.title, cur.title)
            if base.title != cur.title
            else None
        ),
        number_base=base.number,
        number_cur=cur.number,
    )


def diff_sections(base: SpecSection, cur: SpecSection) -> SectionDiff:
    """Diff any two section trees into a flat, document-ordered SectionDiff."""
    elements: list[ElementDiff] = []
    status_changes: list[StatusChange] = []
    base_refs = _element_refs(base)
    cur_refs = _element_refs(cur)

    elements.append(_section_element(base, cur))

    # Parts are fixed (pt1/pt2/pt3), always present and same order — a
    # structural heading, never a diff subject. Articles merge within each.
    for base_part, cur_part in zip(base.parts, cur.parts):
        elements.append(
            ElementDiff(
                uid=cur_part.uid,
                node_type="part",
                kind="unchanged",
                base_text=cur_part.title,
                cur_text=cur_part.title,
            )
        )
        _diff_articles(
            base_part.articles,
            cur_part.articles,
            base_refs,
            cur_refs,
            elements,
            status_changes,
        )

    stats = {"inserted": 0, "deleted": 0, "changed": 0, "unchanged": 0}
    for element in elements:
        if element.node_type == "part":
            continue  # structural, never counted
        stats[element.kind] += 1
    return SectionDiff(
        elements=elements, status_changes=status_changes, stats=stats
    )
