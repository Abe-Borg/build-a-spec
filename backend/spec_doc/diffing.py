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
  emitted once, at its cur position with its **cur-position label**. Display
  numbering (A. / 1.1 / a.) is positional and recomputes anyway, so marking a
  move — or the renumbering a survivor undergoes when a sibling is
  inserted/deleted — as delete+insert would drown a reviewer in noise for zero
  information. The consequence, by design: the redline export's Accept-All
  reproduces the current document exactly (current numbering included), while
  Reject-All reproduces the baseline's provision *text* with numbering
  positional (a shifted survivor keeps its current label, not its base one).
  Revisit only if a reviewer asks for label-faithful Reject-All (which would
  require Word auto-numbering, not literal labels).
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

**Move detection is an opt-in** (``detect_moves=True``; Redline on your
original, Phase 1). The redline on the user's own file cannot follow the
"moves are not marked" rule: its Reject All must give back the upload's
ORDER, so a reorder has to be a tracked change. Within each sibling list,
the survivors whose relative order held are the heaviest increasing
subsequence of their base positions taken in current order — the LONGEST,
with ties going to the one that keeps the most elements in place (an
element weighs its whole subtree). Every other survivor is MOVED: its row
at the current position carries ``moved="to"``, and a ``moved="from"`` copy
of its base subtree (kind ``deleted``) sits where it used to be, after the
nearest preceding survivor that did not move. A moved element's whole
subtree moves with it. With the flag off nothing about the output changes
— the compare view and the normalized redline are pinned byte-identical.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from .model import (
    Article,
    Paragraph,
    SpecSection,
    _paragraph_label,
    iter_paragraphs,
    labelled_paragraphs,
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
    #: Move detection only (``detect_moves=True``): ``"to"`` on every row of
    #: a moved element's subtree at its current position, ``"from"`` on the
    #: rows of the copy left where it was. Empty otherwise — and then absent
    #: from :meth:`to_dict`, so a diff without move detection serializes
    #: exactly as it always has.
    moved: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = {
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
        if self.moved:
            payload["moved"] = self.moved
        return payload


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
    #: Move detection only: the uid of every moved subtree's root, in
    #: current document order. ``None`` when moves were not detected.
    moved: list[str] | None = None

    def has_changes(self) -> bool:
        return bool(
            self.stats["inserted"]
            or self.stats["deleted"]
            or self.stats["changed"]
            or self.stats.get("moved")
            or self.status_changes
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "elements": [element.to_dict() for element in self.elements],
            "status_changes": [sc.to_dict() for sc in self.status_changes],
            "stats": dict(self.stats),
        }
        if self.moved is not None:
            payload["moved"] = list(self.moved)
        return payload


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


def heaviest_increasing_subsequence(
    keys: Sequence[int], weights: Sequence[int]
) -> list[int]:
    """Positions (in order) of the subsequence of ``keys`` that is strictly
    increasing and has the largest total ``weight``.

    O(n log n): a Fenwick tree over the key ranks holds, per rank, the best
    chain ending there as ``(weight, position)``. Ties go to the LATER
    position — for the chain's end and for every link — so a swap keeps the
    element that now sits lower and reports the one that moved up, the way
    a reader describes "moved B up". Weights must be positive.
    """
    count = len(keys)
    if count == 0:
        return []
    ranks = {key: rank for rank, key in enumerate(sorted(set(keys)), start=1)}
    size = len(ranks)
    tree: list[tuple[int, int]] = [(0, -1)] * (size + 1)
    best = [0] * count
    parent = [-1] * count
    for index in range(count):
        rank = ranks[keys[index]]
        found = (0, -1)
        probe = rank - 1
        while probe > 0:
            if tree[probe] > found:
                found = tree[probe]
            probe -= probe & -probe
        best[index] = found[0] + weights[index]
        parent[index] = found[1]
        value = (best[index], index)
        probe = rank
        while probe <= size:
            if value > tree[probe]:
                tree[probe] = value
            probe += probe & -probe
    end = max(range(count), key=lambda index: (best[index], index))
    chain = []
    while end != -1:
        chain.append(end)
        end = parent[end]
    return chain[::-1]


def _subtree_size(node: Any) -> int:
    children = getattr(node, "paragraphs", None)
    if children is None:
        children = getattr(node, "children", [])
    return 1 + sum(_subtree_size(child) for child in children)


def _stable_survivors(base_nodes: list[Any], cur_nodes: list[Any]) -> set[str]:
    """The survivors whose relative order held: the longest increasing
    subsequence of their base positions in current order, ties to the one
    keeping the most elements (whole subtrees) in place."""
    base_index = {node.uid: i for i, node in enumerate(base_nodes)}
    survivors = [node for node in cur_nodes if node.uid in base_index]
    if not survivors:
        return set()
    sizes = [_subtree_size(node) for node in survivors]
    heavy = sum(sizes) + 1  # length first, subtree weight only on ties
    chain = heaviest_increasing_subsequence(
        [base_index[node.uid] for node in survivors],
        [heavy + size for size in sizes],
    )
    return {survivors[i].uid for i in chain}


def _merge_by_uid(
    base_nodes: list[Any],
    cur_nodes: list[Any],
    stable: set[str] | None = None,
) -> list[tuple[str, Any, Any, int, int]]:
    """Merge two sibling lists into one ordered walk.

    Each entry is ``(role, cur_node, base_node, cur_index, base_index)`` with
    ``role`` in ``both`` / ``inserted`` / ``deleted``. Survivors and inserts
    follow cur order; deleted (base-only) nodes are spliced in right after
    the surviving base node that precedes them (or at the front). ``index``
    fields are raw sibling positions, ``-1`` when not applicable; display
    letters come from ``_letters``, which skips preserved blocks.

    With ``stable`` (move detection), a survivor outside it is MOVED: a
    ``moved_to`` entry at its current position and a ``moved_from`` entry
    spliced at its base position exactly like a deleted node — after the
    nearest preceding survivor that did not move.
    """
    base_by_uid = {node.uid: (i, node) for i, node in enumerate(base_nodes)}
    cur_uids = {node.uid for node in cur_nodes}
    anchors = cur_uids if stable is None else stable & cur_uids

    def base_role(node: Any) -> str:
        return "moved_from" if node.uid in cur_uids else "deleted"

    leading_deleted: list[tuple[int, Any]] = []
    trailing_deleted: dict[str, list[tuple[int, Any]]] = {}
    last_survivor: str | None = None
    for i, node in enumerate(base_nodes):
        if node.uid in anchors:
            last_survivor = node.uid
            trailing_deleted.setdefault(last_survivor, [])
        elif last_survivor is None:
            leading_deleted.append((i, node))
        else:
            trailing_deleted[last_survivor].append((i, node))

    out: list[tuple[str, Any, Any, int, int]] = []
    for base_index, node in leading_deleted:
        out.append((base_role(node), None, node, -1, base_index))
    for cur_index, node in enumerate(cur_nodes):
        match = base_by_uid.get(node.uid)
        if match is not None and node.uid in anchors:
            base_index, base_node = match
            out.append(("both", node, base_node, cur_index, base_index))
            for del_index, del_node in trailing_deleted.get(node.uid, []):
                out.append((base_role(del_node), None, del_node, -1, del_index))
        elif match is not None:
            base_index, base_node = match
            out.append(("moved_to", node, base_node, cur_index, base_index))
        else:
            out.append(("inserted", node, None, cur_index, -1))
    return out


# ---------------------------------------------------------------------------
# Recursive diff
# ---------------------------------------------------------------------------


@dataclass
class _Walk:
    """What the recursive diff threads through: the refs, the output, and
    the move-detection state."""

    base_refs: dict[str, str]
    cur_refs: dict[str, str]
    elements: list[ElementDiff]
    status_changes: list[StatusChange]
    detect_moves: bool = False
    moved: list[str] = field(default_factory=list)

    def stable(self, base_nodes: list[Any], cur_nodes: list[Any], moved: str):
        """The stable set for one sibling list, or ``None`` to detect no
        moves (flag off, or inside a subtree that already moved whole)."""
        if not self.detect_moves or moved:
            return None
        return _stable_survivors(base_nodes, cur_nodes)


def _letters(depth: int, nodes: list[Paragraph]) -> dict[str, str]:
    """uid -> display letter, numbered the way the panel numbers them.

    ``labelled_paragraphs`` skips a preserved block (it takes no letter and
    shifts no sibling), so after a preserved table the compare view and the
    redline say "B." where the panel does — counting every sibling said
    "C." (Redline on your original, Phase 0).
    """
    return {
        node.uid: "" if position < 0 else _paragraph_label(depth, position)
        for node, position in labelled_paragraphs(nodes)
    }


def _paragraph_row(
    walk: _Walk,
    cur_node: Paragraph,
    base_node: Paragraph,
    depth: int,
    label: str,
    moved: str,
) -> None:
    uid = cur_node.uid
    if _norm(base_node.text) != _norm(cur_node.text):
        walk.elements.append(
            ElementDiff(
                uid=uid,
                node_type="paragraph",
                kind="changed",
                depth=depth,
                label=label,
                ref_base=walk.base_refs.get(uid, ""),
                ref_cur=walk.cur_refs.get(uid, ""),
                base_text=base_node.text,
                cur_text=cur_node.text,
                runs=token_runs(base_node.text, cur_node.text),
                moved=moved,
            )
        )
        return
    walk.elements.append(
        ElementDiff(
            uid=uid,
            node_type="paragraph",
            kind="unchanged",
            depth=depth,
            label=label,
            ref_base=walk.base_refs.get(uid, ""),
            ref_cur=walk.cur_refs.get(uid, ""),
            base_text=base_node.text,
            cur_text=cur_node.text,
            moved=moved,
        )
    )
    if base_node.status != cur_node.status:
        walk.status_changes.append(
            StatusChange(
                uid=uid,
                ref=walk.cur_refs.get(uid, ""),
                status_base=base_node.status,
                status_cur=cur_node.status,
            )
        )


def _diff_paragraphs(
    base_nodes: list[Paragraph],
    cur_nodes: list[Paragraph],
    depth: int,
    walk: _Walk,
    moved: str = "",
) -> None:
    cur_letters = _letters(depth, cur_nodes)
    base_letters = _letters(depth, base_nodes)
    stable = walk.stable(base_nodes, cur_nodes, moved)
    for role, cur_node, base_node, _cur_index, _base_index in _merge_by_uid(
        base_nodes, cur_nodes, stable
    ):
        if role in ("both", "moved_to"):
            row_moved = moved or ("to" if role == "moved_to" else "")
            if role == "moved_to" and not moved:
                walk.moved.append(cur_node.uid)
            _paragraph_row(
                walk, cur_node, base_node, depth, cur_letters[cur_node.uid], row_moved
            )
            _diff_paragraphs(
                base_node.children, cur_node.children, depth + 1, walk, row_moved
            )
        elif role == "inserted":
            uid = cur_node.uid
            walk.elements.append(
                ElementDiff(
                    uid=uid,
                    node_type="paragraph",
                    kind="inserted",
                    depth=depth,
                    label=cur_letters[uid],
                    ref_cur=walk.cur_refs.get(uid, ""),
                    cur_text=cur_node.text,
                    moved=moved,
                )
            )
            _diff_paragraphs([], cur_node.children, depth + 1, walk, moved)
        else:  # deleted / moved_from
            uid = base_node.uid
            # The old copy of a move is "from" all the way down; a deletion
            # INSIDE a moved subtree belongs to that subtree's own diff and
            # inherits its flag.
            row_moved = "from" if role == "moved_from" else moved
            walk.elements.append(
                ElementDiff(
                    uid=uid,
                    node_type="paragraph",
                    kind="deleted",
                    depth=depth,
                    label=base_letters[uid],
                    ref_base=walk.base_refs.get(uid, ""),
                    base_text=base_node.text,
                    moved=row_moved,
                )
            )
            _diff_paragraphs(base_node.children, [], depth + 1, walk, row_moved)


def _diff_articles(
    base_articles: list[Article],
    cur_articles: list[Article],
    walk: _Walk,
) -> None:
    stable = walk.stable(base_articles, cur_articles, "")
    for role, cur_node, base_node, _cur_index, _base_index in _merge_by_uid(
        base_articles, cur_articles, stable
    ):
        if role in ("both", "moved_to"):
            uid = cur_node.uid
            moved = "to" if role == "moved_to" else ""
            if moved:
                walk.moved.append(uid)
            changed = base_node.title != cur_node.title
            walk.elements.append(
                ElementDiff(
                    uid=uid,
                    node_type="article",
                    kind="changed" if changed else "unchanged",
                    label=walk.cur_refs.get(uid, ""),
                    ref_base=walk.base_refs.get(uid, ""),
                    ref_cur=walk.cur_refs.get(uid, ""),
                    base_text=base_node.title,
                    cur_text=cur_node.title,
                    runs=(
                        token_runs(base_node.title, cur_node.title)
                        if changed
                        else None
                    ),
                    moved=moved,
                )
            )
            _diff_paragraphs(
                base_node.paragraphs, cur_node.paragraphs, 0, walk, moved
            )
        elif role == "inserted":
            uid = cur_node.uid
            walk.elements.append(
                ElementDiff(
                    uid=uid,
                    node_type="article",
                    kind="inserted",
                    label=walk.cur_refs.get(uid, ""),
                    ref_cur=walk.cur_refs.get(uid, ""),
                    cur_text=cur_node.title,
                )
            )
            _diff_paragraphs([], cur_node.paragraphs, 0, walk)
        else:  # deleted / moved_from
            uid = base_node.uid
            moved = "from" if role == "moved_from" else ""
            walk.elements.append(
                ElementDiff(
                    uid=uid,
                    node_type="article",
                    kind="deleted",
                    label=walk.base_refs.get(uid, ""),
                    ref_base=walk.base_refs.get(uid, ""),
                    base_text=base_node.title,
                    moved=moved,
                )
            )
            _diff_paragraphs(base_node.paragraphs, [], 0, walk, moved)


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


def diff_sections(
    base: SpecSection, cur: SpecSection, *, detect_moves: bool = False
) -> SectionDiff:
    """Diff any two section trees into a flat, document-ordered SectionDiff.

    ``detect_moves`` (off by default) reports reordered survivors as moves;
    see the module docstring. Off, the result is exactly what it has always
    been.
    """
    walk = _Walk(
        base_refs=_element_refs(base),
        cur_refs=_element_refs(cur),
        elements=[],
        status_changes=[],
        detect_moves=detect_moves,
    )
    walk.elements.append(_section_element(base, cur))

    # Parts are fixed (pt1/pt2/pt3), always present and same order — a
    # structural heading, never a diff subject. Articles merge within each.
    for base_part, cur_part in zip(base.parts, cur.parts):
        walk.elements.append(
            ElementDiff(
                uid=cur_part.uid,
                node_type="part",
                kind="unchanged",
                base_text=cur_part.title,
                cur_text=cur_part.title,
            )
        )
        _diff_articles(base_part.articles, cur_part.articles, walk)

    stats = {"inserted": 0, "deleted": 0, "changed": 0, "unchanged": 0}
    for element in walk.elements:
        if element.node_type == "part":
            continue  # structural, never counted
        if element.moved == "from":
            continue  # the old copy of a move is not a deletion
        stats[element.kind] += 1
    if detect_moves:
        stats["moved"] = len(walk.moved)
    return SectionDiff(
        elements=walk.elements,
        status_changes=walk.status_changes,
        stats=stats,
        moved=list(walk.moved) if detect_moves else None,
    )
