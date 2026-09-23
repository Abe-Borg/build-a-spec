"""Word comments on the redline on your original (Phase 3 of the redline plan).

Every tracked change in the redline on your original that has a recorded
basis — the research finding behind it, the attached document behind it, or
the Final QC fix that made it — carries a Word comment from "Build-a-Spec"
saying what the change rests on, with its source web links. A reviewer sees
why each change was made without opening the app.

This module is the WRITER half and it only ever ADDS:

* **What it adds to the body:** for each comment, a ``w:commentRangeStart``
  first after the ``w:pPr`` of the first paragraph it covers, and a
  ``w:commentRangeEnd`` plus a plain, untracked ``w:commentReference`` run at
  the end of the last one — direct children of the paragraph, never inside a
  ``w:ins`` / ``w:del`` / ``w:moveFrom`` / ``w:moveTo``, so both Accept All and
  Reject All leave every comment anchored.
* **What it adds to the package:** the comments part (new, or the upload's
  own with Build-a-Spec's comments appended), its relationships part (for the
  links), and — only when the comments part is new — the relationship that
  registers it in ``word/_rels/document.xml.rels`` and its content-type
  ``Override`` in ``[Content_Types].xml``. ``word/styles.xml`` is never
  touched: a comment references ``CommentText`` / ``CommentReference`` /
  ``Hyperlink`` only when the upload defines them, and uses direct
  formatting otherwise. Word 2013+'s extension parts (``commentsExtended``,
  ``commentsIds``, ``commentsExtensible``, ``people``) are left as they are:
  their entries are all optional ([MS-DOCX]), so Build-a-Spec's comments get
  none.

It runs AFTER the redline builder and after the D-7 self-check, on copies of
the body the self-check proved, and it proves itself before anything is
written (:class:`CommentPassError` names the check that failed): removing
exactly its anchors from the final body gives back that proved body element
for element; every part it modified, minus its additions, is the upload's
part; every comment id it placed pairs one ``w:comment`` with exactly one
start, end and reference. The caller then audits the whole package. Any
failure means the redline is handed over WITHOUT comments — comments never
turn an export that works into a refusal.

What a comment SAYS is decided elsewhere (``backend/redline_basis.py``,
which owns the wording) and arrives here as data (:class:`CommentBasis`);
which changes get one is decided here, from the redline's own records
(:class:`CommentSite`).
"""
from __future__ import annotations

import copy
import io
import posixpath
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from docx.oxml.ns import qn
from lxml import etree

from .xml_text import xml_safe_text

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
COMMENTS_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"
)
HYPERLINK_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
)
STYLES_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles"
)
COMMENTS_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"
)
RELS_CONTENT_TYPE = "application/vnd.openxmlformats-package.relationships+xml"
#: The initials Word shows beside a comment's author.
COMMENT_INITIALS = "BAS"

_CONTENT_TYPES_PART = "[Content_Types].xml"
_DOCUMENT_RELS = "word/_rels/document.xml.rels"
_NEW_COMMENTS_PART = "word/comments.xml"

_W_P = qn("w:p")
_W_PPR = qn("w:pPr")
_W_R = qn("w:r")
_W_RPR = qn("w:rPr")
_W_T = qn("w:t")
_W_ID = qn("w:id")
_W_COMMENT = qn("w:comment")
_W_COMMENTS = qn("w:comments")
_W_RANGE_START = qn("w:commentRangeStart")
_W_RANGE_END = qn("w:commentRangeEnd")
_W_REFERENCE = qn("w:commentReference")
_ANCHOR_TAGS = (_W_RANGE_START, _W_RANGE_END, _W_REFERENCE)
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"

#: What the redline shows for an element (the site of a possible comment).
CHANGE_INSERTED = "inserted"
CHANGE_EDITED = "edited"
CHANGE_DELETED = "deleted"
CHANGE_MOVED = "moved"
CHANGES = frozenset({CHANGE_INSERTED, CHANGE_EDITED, CHANGE_DELETED, CHANGE_MOVED})

#: Why a changed element carries no comment — a closed vocabulary the export
#: event counts (``redline.comments.skipped``). Counts only, never text.
SKIP_NO_BASIS = "no_basis"
SKIP_LOCKED = "locked"
SKIP_UNRESOLVED = "unresolved_source"
COMMENT_SKIP_REASONS = frozenset({SKIP_NO_BASIS, SKIP_LOCKED, SKIP_UNRESOLVED})

#: Why the redline was handed over without its comments — a closed
#: vocabulary (``redline.comments.fallback``). Each names the check that
#: failed; none of them is a refusal.
FALLBACK_PARTS = "parts_unreadable"
FALLBACK_BODY_CHECK = "body_check"
FALLBACK_PART_CHECK = "part_check"
FALLBACK_PAIRING_CHECK = "pairing_check"
FALLBACK_PACKAGE_CHECK = "package_check"
FALLBACK_ERROR = "error"
COMMENT_FALLBACK_REASONS = frozenset(
    {
        FALLBACK_PARTS,
        FALLBACK_BODY_CHECK,
        FALLBACK_PART_CHECK,
        FALLBACK_PAIRING_CHECK,
        FALLBACK_PACKAGE_CHECK,
        FALLBACK_ERROR,
    }
)


class CommentPassError(Exception):
    """The comments pass could not prove its own output; ``reason`` is one of
    :data:`COMMENT_FALLBACK_REASONS`. The redline goes out without comments."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class CommentLink:
    """A web link in a comment: ``label`` is what the reader sees."""

    url: str
    label: str


#: A comment paragraph: plain text and links, in order.
CommentParagraph = tuple["str | CommentLink", ...]


@dataclass(frozen=True)
class CommentBasis:
    """What one element's change rests on, as comment paragraphs.

    ``qc`` — the Final QC fixes that still hold at the element; ``research``
    — the research item or attached document its ``source_item_id`` names;
    ``unresolved_source`` — it names one that resolves to nothing."""

    qc: tuple[CommentParagraph, ...] = ()
    research: tuple[CommentParagraph, ...] = ()
    unresolved_source: bool = False


@dataclass(frozen=True)
class CommentSite:
    """An element the redline marks as changed.

    ``text_changed`` is False for a provision whose own words are what they
    were at the import (a relettered one, a pure move): a research basis
    speaks for NEW words, so it applies only when there are some."""

    uid: str
    change: str
    text_changed: bool = True
    locked: bool = False


def safe_link_url(url: object) -> str | None:
    """``url`` when it may become a clickable hyperlink: http or https, a
    host, and nothing that could break out of an attribute. Anything else
    stays plain text."""
    if not isinstance(url, str):
        return None
    candidate = url.strip()
    if not candidate or any(ord(c) < 33 or ord(c) == 127 for c in candidate):
        return None
    try:
        parts = urlsplit(candidate)
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        return None
    return candidate


@dataclass
class _Planned:
    indexes: list[int]
    paragraphs: tuple[CommentParagraph, ...]


def plan_comments(
    body: Sequence,
    sites: Sequence[CommentSite | None],
    bases: Mapping[str, CommentBasis],
) -> tuple[list[_Planned], dict[str, int], int]:
    """Which changes get which comment: ``(groups, skipped, covered)``.

    A changed element carries the QC bases that still hold at it, plus —
    only when it has new words (inserted, or edited / moved with its text
    changed) — its research or attached-document basis. A locked block
    (table, picture, …) never does. Consecutive changed paragraphs whose
    comments would read the same share ONE comment spanning them, when
    nothing but those paragraphs sits between them.
    """
    skipped: dict[str, int] = {}
    candidates: list[tuple[int, tuple[CommentParagraph, ...]]] = []
    for index, site in enumerate(sites):
        if site is None or not site.uid or site.change not in CHANGES:
            continue
        if site.change == CHANGE_EDITED and not site.text_changed:
            continue  # relettered, not reworded: not a change of substance
        element = body[index]
        basis = bases.get(site.uid, CommentBasis())
        if site.locked or element.tag != _W_P:
            skipped[SKIP_LOCKED] = skipped.get(SKIP_LOCKED, 0) + 1
            continue
        research_applies = site.change == CHANGE_INSERTED or (
            site.change in (CHANGE_EDITED, CHANGE_MOVED) and site.text_changed
        )
        paragraphs = tuple(basis.qc) + (
            tuple(basis.research) if research_applies else ()
        )
        if not paragraphs:
            reason = (
                SKIP_UNRESOLVED
                if basis.unresolved_source and research_applies
                else SKIP_NO_BASIS
            )
            skipped[reason] = skipped.get(reason, 0) + 1
            continue
        candidates.append((index, paragraphs))
    groups: list[_Planned] = []
    for index, paragraphs in candidates:
        if (
            groups
            and groups[-1].indexes[-1] == index - 1
            and groups[-1].paragraphs == paragraphs
        ):
            groups[-1].indexes.append(index)
        else:
            groups.append(_Planned([index], paragraphs))
    return groups, skipped, len(candidates)


# ---------------------------------------------------------------------------
# The package: what the upload holds, and what the pass adds to it
# ---------------------------------------------------------------------------


def _parse(data: bytes):
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    return etree.fromstring(data, parser)


def _canonical(root) -> bytes:
    return etree.tostring(root, method="c14n")


def _serialize(root) -> bytes:
    return etree.tostring(
        root, xml_declaration=True, encoding="UTF-8", standalone=True
    )


def _rels_name(part: str) -> str:
    folder, name = posixpath.split(part)
    return posixpath.join(folder, "_rels", name + ".rels")


@dataclass
class _Package:
    names: dict[str, str]  # casefolded member name -> the stored name
    archive: zipfile.ZipFile

    def stored(self, name: str) -> str | None:
        return self.names.get(name.casefold())

    def read(self, name: str) -> bytes:
        stored = self.stored(name)
        if stored is None:
            raise KeyError(name)
        return self.archive.read(stored)


@dataclass
class _Styles:
    comment_text: str = ""
    comment_reference: str = ""
    hyperlink: str = ""


def _read_styles(package: _Package, document_rels) -> _Styles:
    """The upload's own ids for the comment and hyperlink styles, when it
    defines them (by id, or by Word's built-in name). Read, never written."""
    target = "styles.xml"
    for rel in document_rels:
        if rel.get("Type") == STYLES_REL_TYPE and rel.get("TargetMode") != "External":
            target = rel.get("Target", target)
    name = posixpath.normpath(posixpath.join("word", target.lstrip("/")))
    if target.startswith("/"):
        name = target.lstrip("/")
    if package.stored(name) is None:
        return _Styles()
    try:
        root = _parse(package.read(name))
    except (etree.XMLSyntaxError, KeyError, ValueError):
        return _Styles()
    found = _Styles()
    wanted = {
        "comment_text": ("paragraph", "CommentText", "annotation text"),
        "comment_reference": ("character", "CommentReference", "annotation reference"),
        "hyperlink": ("character", "Hyperlink", "hyperlink"),
    }
    for style in root.iter(qn("w:style")):
        style_id = style.get(qn("w:styleId")) or ""
        kind = style.get(qn("w:type")) or ""
        name_element = style.find(qn("w:name"))
        style_name = (name_element.get(qn("w:val")) if name_element is not None else "") or ""
        for attribute, (want_kind, want_id, want_name) in wanted.items():
            if getattr(found, attribute) or kind != want_kind or not style_id:
                continue
            if style_id == want_id or style_name.casefold() == want_name:
                setattr(found, attribute, style_id)
    return found


def _unique_rel_id(taken: set[str], stem: str) -> str:
    number = 1
    while f"{stem}{number}" in taken:
        number += 1
    rel_id = f"{stem}{number}"
    taken.add(rel_id)
    return rel_id


@dataclass
class CommentedPackage:
    """The comments pass's output: the final body, and the members to write
    beside ``word/document.xml`` — rewritten (``replacements``) and new
    (``additions``, in order)."""

    body: list
    replacements: dict[str, bytes] = field(default_factory=dict)
    additions: list[tuple[str, bytes]] = field(default_factory=list)
    comment_ids: list[str] = field(default_factory=list)


def _text_run(text: str, properties=None):
    run = etree.Element(_W_R)
    if properties is not None:
        run.append(properties)
    t = etree.SubElement(run, _W_T)
    t.text = xml_safe_text(text)
    t.set(_XML_SPACE, "preserve")
    return run


#: Word's own comment styles, as direct formatting for a master that does
#: not define them: comment text 10 pt (half-points), the reference mark 8 pt,
#: a link Word's hyperlink blue, underlined. A reference to an undefined
#: style would be conformant (no style is applied) but would lose all three.
_COMMENT_TEXT_SIZE = "20"
_REFERENCE_SIZE = "16"
_LINK_COLOR = "0563C1"


def _run_properties(style_id: str = "", *, size: str = "", link: bool = False):
    """A ``w:rPr`` in schema order (rStyle … color, sz, szCs … u), or None
    when there is nothing to say."""
    if not (style_id or size or link):
        return None
    properties = etree.Element(_W_RPR)
    if style_id:
        etree.SubElement(properties, qn("w:rStyle")).set(qn("w:val"), style_id)
    if link:
        etree.SubElement(properties, qn("w:color")).set(qn("w:val"), _LINK_COLOR)
    if size:
        etree.SubElement(properties, qn("w:sz")).set(qn("w:val"), size)
        etree.SubElement(properties, qn("w:szCs")).set(qn("w:val"), size)
    if link:
        etree.SubElement(properties, qn("w:u")).set(qn("w:val"), "single")
    return properties


def _paragraph_properties(styles: _Styles):
    properties = etree.Element(_W_PPR)
    if styles.comment_text:
        etree.SubElement(properties, qn("w:pStyle")).set(
            qn("w:val"), styles.comment_text
        )
    else:
        spacing = etree.SubElement(properties, qn("w:spacing"))
        spacing.set(qn("w:line"), "240")
        spacing.set(qn("w:lineRule"), "auto")
    return properties


def _text_properties(styles: _Styles):
    """A comment's own text: its paragraph style carries the size, or the
    run does."""
    return None if styles.comment_text else _run_properties(size=_COMMENT_TEXT_SIZE)


def _reference_properties(styles: _Styles):
    """The annotation reference mark, in the comment and in the body."""
    if styles.comment_reference:
        return _run_properties(styles.comment_reference)
    return _run_properties(size=_REFERENCE_SIZE)


def _hyperlink_properties(styles: _Styles):
    size = "" if styles.comment_text else _COMMENT_TEXT_SIZE
    if styles.hyperlink:
        return _run_properties(styles.hyperlink, size=size)
    return _run_properties(size=size, link=True)


def _comment_element(
    comment_id: str,
    paragraphs: tuple[CommentParagraph, ...],
    *,
    author: str,
    date: str,
    styles: _Styles,
    link_rel,
):
    comment = etree.Element(_W_COMMENT, nsmap={"w": W_NS, "r": R_NS})
    comment.set(_W_ID, comment_id)
    comment.set(qn("w:author"), xml_safe_text(author))
    comment.set(qn("w:date"), xml_safe_text(date))
    comment.set(qn("w:initials"), COMMENT_INITIALS)
    for number, segments in enumerate(paragraphs):
        paragraph = etree.SubElement(comment, _W_P)
        paragraph.append(_paragraph_properties(styles))
        if number == 0:
            # Word's own shape: the comment's first paragraph opens with the
            # annotation reference mark.
            mark = etree.SubElement(paragraph, _W_R)
            reference_properties = _reference_properties(styles)
            if reference_properties is not None:
                mark.append(reference_properties)
            etree.SubElement(mark, qn("w:annotationRef"))
        for segment in segments:
            if isinstance(segment, CommentLink):
                url = safe_link_url(segment.url)
                label = segment.label or segment.url
                if url is None:
                    paragraph.append(_text_run(label, _text_properties(styles)))
                    continue
                link = etree.SubElement(paragraph, qn("w:hyperlink"))
                link.set(f"{{{R_NS}}}id", link_rel(url))
                link.set(qn("w:history"), "1")
                link.append(_text_run(label, _hyperlink_properties(styles)))
            elif segment:
                paragraph.append(_text_run(str(segment), _text_properties(styles)))
    return comment


def _insert_anchors(first, last, comment_id: str, styles: _Styles) -> None:
    start = etree.Element(_W_RANGE_START)
    start.set(_W_ID, comment_id)
    properties = first.find(_W_PPR)
    first.insert(0 if properties is None else first.index(properties) + 1, start)
    end = etree.Element(_W_RANGE_END)
    end.set(_W_ID, comment_id)
    last.append(end)
    run = etree.SubElement(last, _W_R)
    reference_properties = _reference_properties(styles)
    if reference_properties is not None:
        run.append(reference_properties)
    etree.SubElement(run, _W_REFERENCE).set(_W_ID, comment_id)


def strip_comment_anchors(element, comment_ids) -> None:
    """Remove, in place, exactly the anchors of ``comment_ids``: their range
    starts and ends, and each reference run the pass added (a run holding
    nothing but the reference and its properties)."""
    ids = set(comment_ids)
    for node in list(element.iter(*_ANCHOR_TAGS)):
        if node.get(_W_ID) not in ids:
            continue
        parent = node.getparent()
        if node.tag == _W_REFERENCE and parent is not None and parent.tag == _W_R:
            others = [
                c for c in parent if isinstance(c.tag, str) and c.tag not in (_W_RPR, _W_REFERENCE)
            ]
            if not others and parent.getparent() is not None:
                parent.getparent().remove(parent)
                continue
        if parent is not None:
            parent.remove(node)


def _check_body(final: list, proved: list, ids: list[str]) -> None:
    """Removing exactly the pass's anchors gives back the proved body."""
    if len(final) != len(proved):
        raise CommentPassError(FALLBACK_BODY_CHECK)
    for after, before in zip(final, proved):
        stripped = copy.deepcopy(after)
        strip_comment_anchors(stripped, ids)
        if _canonical(stripped) != _canonical(before):
            raise CommentPassError(FALLBACK_BODY_CHECK)


def _check_pairing(final: list, comments_root, ids: list[str]) -> None:
    """Every id the pass placed pairs one ``w:comment`` with exactly one
    range start, range end and reference, in that order, each a direct child
    of a paragraph and never inside a tracked change."""
    order: dict[str, list[str]] = {i: [] for i in ids}
    for element in final:
        for node in element.iter(*_ANCHOR_TAGS):
            comment_id = node.get(_W_ID)
            if comment_id not in order:
                continue
            # A direct child of a paragraph is outside every tracked change:
            # w:ins / w:del / w:moveFrom / w:moveTo hold runs, never
            # paragraphs, so no separate wrapper test is needed.
            anchor = node if node.tag != _W_REFERENCE else node.getparent()
            if anchor is None or anchor.getparent() is None or anchor.getparent().tag != _W_P:
                raise CommentPassError(FALLBACK_PAIRING_CHECK)
            order[comment_id].append(node.tag)
    for tags in order.values():
        if tags != [_W_RANGE_START, _W_RANGE_END, _W_REFERENCE]:
            raise CommentPassError(FALLBACK_PAIRING_CHECK)
    counts: dict[str, int] = {}
    for comment in comments_root.iter(_W_COMMENT):
        counts[comment.get(_W_ID)] = counts.get(comment.get(_W_ID), 0) + 1
    if any(counts.get(i) != 1 for i in ids):
        raise CommentPassError(FALLBACK_PAIRING_CHECK)


def _without(root, predicate):
    trimmed = copy.deepcopy(root)
    for node in [n for n in trimmed if isinstance(n.tag, str) and predicate(n)]:
        trimmed.remove(node)
    return trimmed


def add_comments(
    proved: list,
    sites: Sequence[CommentSite | None],
    bases: Mapping[str, CommentBasis],
    *,
    source_bytes: bytes,
    next_id,
    author: str,
    date: str,
    stats: dict,
) -> CommentedPackage | None:
    """Comment the redline's changes. ``proved`` is the body the self-check
    proved (never modified — the pass works on copies); ``next_id()`` mints
    an id from the redline's own counter, above every id in the package.

    Returns ``None`` when no change has a basis to comment (the redline is
    written exactly as without the pass). Fills ``stats`` with ``added``,
    ``elements`` and ``skipped``. Raises :class:`CommentPassError` when its
    own output cannot be proved."""
    groups, skipped, covered = plan_comments(proved, sites, bases)
    stats["skipped"] = dict(skipped)
    stats["elements"] = 0
    stats["added"] = 0
    if not groups:
        return None
    try:
        archive = zipfile.ZipFile(io.BytesIO(source_bytes))
    except zipfile.BadZipFile as exc:
        raise CommentPassError(FALLBACK_PARTS) from exc
    with archive:
        package = _Package({n.casefold(): n for n in archive.namelist()}, archive)
        try:
            result = _build(
                proved, groups, package, next_id=next_id, author=author, date=date
            )
        except (etree.XMLSyntaxError, KeyError, ValueError) as exc:
            raise CommentPassError(FALLBACK_PARTS) from exc
    stats["added"] = len(result.comment_ids)
    stats["elements"] = covered
    return result


def _build(proved, groups, package: _Package, *, next_id, author, date):
    types_root = _parse(package.read(_CONTENT_TYPES_PART))
    has_document_rels = package.stored(_DOCUMENT_RELS) is not None
    document_rels = (
        _parse(package.read(_DOCUMENT_RELS))
        if has_document_rels
        else etree.Element(f"{{{PKG_REL_NS}}}Relationships", nsmap={None: PKG_REL_NS})
    )
    existing_part = None
    for rel in document_rels:
        if rel.get("Type") == COMMENTS_REL_TYPE:
            if rel.get("TargetMode") == "External" or existing_part is not None:
                raise CommentPassError(FALLBACK_PARTS)
            target = rel.get("Target", "")
            existing_part = (
                target.lstrip("/")
                if target.startswith("/")
                else posixpath.normpath(posixpath.join("word", target))
            )
    styles = _read_styles(package, document_rels)

    if existing_part is not None:
        if package.stored(existing_part) is None:
            raise CommentPassError(FALLBACK_PARTS)
        comments_name = package.stored(existing_part)
        comments_upload = package.read(comments_name)
        comments_root = _parse(comments_upload)
        if comments_root.tag != _W_COMMENTS:
            raise CommentPassError(FALLBACK_PARTS)
    else:
        comments_name = _NEW_COMMENTS_PART
        if package.stored(comments_name) is not None:
            # A comments part nothing registers: extending an orphan, or
            # shadowing it, would both be guesses.
            raise CommentPassError(FALLBACK_PARTS)
        comments_upload = None
        comments_root = etree.Element(_W_COMMENTS, nsmap={"w": W_NS, "r": R_NS})
    comments_rels_name = _rels_name(comments_name)
    stored_comments_rels = package.stored(comments_rels_name)
    if stored_comments_rels is not None:
        comments_rels_name = stored_comments_rels
        comments_rels_upload = package.read(comments_rels_name)
        comments_rels = _parse(comments_rels_upload)
    else:
        comments_rels_upload = None
        comments_rels = etree.Element(
            f"{{{PKG_REL_NS}}}Relationships", nsmap={None: PKG_REL_NS}
        )
    taken_rel_ids = {rel.get("Id", "") for rel in comments_rels}
    added_rel_ids: list[str] = []
    link_ids: dict[str, str] = {}

    def link_rel(url: str) -> str:
        if url not in link_ids:
            rel_id = _unique_rel_id(taken_rel_ids, "rIdBas")
            relationship = etree.SubElement(
                comments_rels, f"{{{PKG_REL_NS}}}Relationship"
            )
            relationship.set("Id", rel_id)
            relationship.set("Type", HYPERLINK_REL_TYPE)
            relationship.set("Target", xml_safe_text(url))
            relationship.set("TargetMode", "External")
            link_ids[url] = rel_id
            added_rel_ids.append(rel_id)
        return link_ids[url]

    body = [copy.deepcopy(element) for element in proved]
    ids: list[str] = []
    for group in groups:
        comment_id = str(next_id())
        ids.append(comment_id)
        _insert_anchors(body[group.indexes[0]], body[group.indexes[-1]], comment_id, styles)
        comments_root.append(
            _comment_element(
                comment_id,
                group.paragraphs,
                author=author,
                date=date,
                styles=styles,
                link_rel=link_rel,
            )
        )

    result = CommentedPackage(body=body, comment_ids=ids)
    comments_bytes = _serialize(comments_root)
    if comments_upload is None:
        result.additions.append((comments_name, comments_bytes))
    else:
        result.replacements[comments_name] = comments_bytes
    if added_rel_ids:
        rels_bytes = _serialize(comments_rels)
        if comments_rels_upload is None:
            result.additions.append((comments_rels_name, rels_bytes))
        else:
            result.replacements[comments_rels_name] = rels_bytes

    added_types: list[str] = []
    document_rel_id = ""
    if comments_upload is None:
        document_rel_id = _unique_rel_id(
            {rel.get("Id", "") for rel in document_rels}, "rIdBasComments"
        )
        relationship = etree.SubElement(
            document_rels, f"{{{PKG_REL_NS}}}Relationship"
        )
        relationship.set("Id", document_rel_id)
        relationship.set("Type", COMMENTS_REL_TYPE)
        relationship.set(
            "Target", posixpath.relpath(comments_name, "word")
        )
        document_rels_bytes = _serialize(document_rels)
        if has_document_rels:
            result.replacements[package.stored(_DOCUMENT_RELS)] = document_rels_bytes
        else:
            result.additions.append((_DOCUMENT_RELS, document_rels_bytes))
        added_types.append("/" + comments_name)
    if comments_rels_upload is None and added_rel_ids:
        defaults = {
            (d.get("Extension") or "").casefold()
            for d in types_root.iter(f"{{{CT_NS}}}Default")
        }
        if "rels" not in defaults:
            added_types.append("/" + comments_rels_name)
    if not has_document_rels:
        defaults = {
            (d.get("Extension") or "").casefold()
            for d in types_root.iter(f"{{{CT_NS}}}Default")
        }
        if "rels" not in defaults:
            added_types.append("/" + _DOCUMENT_RELS)
    overridden = {
        (o.get("PartName") or "").casefold()
        for o in types_root.iter(f"{{{CT_NS}}}Override")
    }
    for part_name in added_types:
        if part_name.casefold() in overridden:
            raise CommentPassError(FALLBACK_PARTS)
        override = etree.SubElement(types_root, f"{{{CT_NS}}}Override")
        override.set("PartName", part_name)
        override.set(
            "ContentType",
            COMMENTS_CONTENT_TYPE if part_name == "/" + comments_name else RELS_CONTENT_TYPE,
        )
    if added_types:
        result.replacements[package.stored(_CONTENT_TYPES_PART)] = _serialize(types_root)

    # -- the pass proves its own output before anyone writes it ---------------
    _check_body(body, proved, ids)
    _check_pairing(body, comments_root, ids)
    ours = set(ids)
    checks = [
        (
            comments_upload,
            comments_bytes,
            lambda n: n.tag == _W_COMMENT and n.get(_W_ID) in ours,
        ),
    ]
    if added_rel_ids:
        rel_set = set(added_rel_ids)
        checks.append(
            (
                comments_rels_upload,
                _serialize(comments_rels),
                lambda n: n.get("Id") in rel_set,
            )
        )
    if document_rel_id and has_document_rels:
        checks.append(
            (
                package.read(_DOCUMENT_RELS),
                result.replacements[package.stored(_DOCUMENT_RELS)],
                lambda n: n.get("Id") == document_rel_id,
            )
        )
    if added_types:
        added_parts = {name.casefold() for name in added_types}
        checks.append(
            (
                package.read(_CONTENT_TYPES_PART),
                result.replacements[package.stored(_CONTENT_TYPES_PART)],
                lambda n: n.tag == f"{{{CT_NS}}}Override"
                and (n.get("PartName") or "").casefold() in added_parts,
            )
        )
    for upload, written, ours_only in checks:
        _check_part(upload, written, ours_only)
    return result


def _check_part(upload: bytes | None, written: bytes, ours_only) -> None:
    """A part the pass writes is the upload's plus its own additions:
    removing every child ``ours_only`` claims gives the upload back,
    canonically. A new part holds nothing but the pass's own additions."""
    root = _parse(written)
    if upload is None:
        if any(isinstance(n.tag, str) and not ours_only(n) for n in root):
            raise CommentPassError(FALLBACK_PART_CHECK)
        return
    if _canonical(_without(root, ours_only)) != _canonical(_parse(upload)):
        raise CommentPassError(FALLBACK_PART_CHECK)


__all__ = [
    "CHANGES",
    "CHANGE_DELETED",
    "CHANGE_EDITED",
    "CHANGE_INSERTED",
    "CHANGE_MOVED",
    "COMMENT_FALLBACK_REASONS",
    "COMMENT_INITIALS",
    "COMMENT_SKIP_REASONS",
    "CommentBasis",
    "CommentLink",
    "CommentPassError",
    "CommentSite",
    "CommentedPackage",
    "add_comments",
    "plan_comments",
    "safe_link_url",
    "strip_comment_anchors",
]
