"""Real Word as the judge of the redline on your original.

Redline on your original, Phase 2 (``docs/plans/REDLINE_ON_ORIGINAL_
2026-09-22.md``). The export promises that Accept All gives *Export Word
(keeps your formatting)* and Reject All gives the upload back, and proves
both before it hands a file over — with its own XML resolver
(``backend/spec_doc/revisions.py``). This module lets REAL Microsoft Word
resolve the same files, through the hidden-Word automation's resolve mode
(``tools/render_docx_word.resolve_docx``), and compares what Word saved.

**The comparison is symmetric.** A Word save rewrites more than it resolves
(formatting spelled its own way, table-look flags expanded, hyperlinks given
a history flag, …), so each side goes through the same Word: the redline
resolved by Word (Accept All or Reject All, then Save As) is compared with
the reference RE-SAVED by Word (the formatted export, or the upload). The two
then differ only by what a Word save writes on its own —
:data:`WORD_SAVE_TOLERANCES`, removed from BOTH sides — and the canonical
comparison the app's own self-check uses
(:func:`backend.spec_doc.revisions.first_difference`) does the rest.

**The tolerances live here, never in the app.** The self-check compares the
app's own XML with the app's own XML and applies none of them
(``tests/test_word_judge.py`` pins that). Each one is named, justified and
tested; anything else Word adds is a difference the judge reports. A new
tolerance needs evidence from a real run, a reason in the table and a test.

Nothing here needs Word: the cases, the comparison and the report are pure,
and a resolver is injected, so ``tests/test_word_judge.py`` drives the whole
pipeline on any platform with a fake Word. Only
``tests/test_redline_word_judge.py`` — gated on :data:`JUDGE_ENV` — hands
the files to the real one.
"""
from __future__ import annotations

import copy
import io
import json
import os
import re
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

from lxml import etree

from backend.spec_doc.importer import parse_master_docx
from backend.spec_doc.model import apply_edits
from backend.spec_doc.revisions import (
    Difference,
    canonical_body,
    first_difference,
    has_revisions,
)
from backend.spec_doc.source_render import (
    SourceRedlineError,
    render_preserving_docx,
    render_preserving_redline,
)
from tests.docx_fidelity_helpers import rewrite_zip_members

JUDGE_ENV = "BUILD_A_SPEC_WORD_JUDGE"
JUDGE_DIR_ENV = "BUILD_A_SPEC_WORD_JUDGE_DIR"
JUDGE_SKIP_REASON = (
    "Set BUILD_A_SPEC_WORD_JUDGE=1 on Windows with Microsoft Word installed to "
    "have real Word resolve the redline on your original"
)
#: The author and date every judged redline carries — the production author
#: (Decision 5), and a fixed date so a rerun writes the same files.
JUDGE_AUTHOR = "Build-a-Spec"
JUDGE_DATE = "2026-09-23T12:00:00Z"
#: Where the judge writes its report and every file it hands Word, unless
#: :data:`JUDGE_DIR_ENV` says otherwise (``/artifacts/`` is gitignored).
DEFAULT_JUDGE_DIR = Path(__file__).resolve().parent.parent / "artifacts" / "word-judge"

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W_BODY = f"{{{_W}}}body"
_W_P = f"{{{_W}}}p"
_W_T = f"{{{_W}}}t"
_W_DEL_TEXT = f"{{{_W}}}delText"
_W_BOOKMARK_START = f"{{{_W}}}bookmarkStart"
_W_NAME = f"{{{_W}}}name"
_W_PPR = f"{{{_W}}}pPr"
_W_RPR = f"{{{_W}}}rPr"
_REVISION_WRAPPERS = frozenset(
    f"{{{_W}}}{name}" for name in ("ins", "del", "moveFrom", "moveTo")
)

# ---------------------------------------------------------------------------
# What a Word save writes on its own
# ---------------------------------------------------------------------------

#: Revision save ids: which Word editing session last touched a paragraph,
#: run, row or section. Every save stamps its own session's ids.
RSID_ATTRIBUTES = frozenset(
    f"{{{_W}}}{name}"
    for name in (
        "rsidR",
        "rsidRPr",
        "rsidRDefault",
        "rsidP",
        "rsidDel",
        "rsidTr",
        "rsidSect",
    )
)
PROOFING_MARK = f"{{{_W}}}proofErr"
LAST_RENDERED_PAGE_BREAK = f"{{{_W}}}lastRenderedPageBreak"
GO_BACK_BOOKMARK = "_GoBack"

#: Every tolerance the judge applies, and why it says nothing about the
#: document. Removed from BOTH sides before comparing; nothing else is.
WORD_SAVE_TOLERANCES: Mapping[str, str] = {
    "rsid attributes": (
        "w:rsidR, w:rsidRPr, w:rsidRDefault, w:rsidP, w:rsidDel, w:rsidTr and "
        "w:rsidSect identify the Word editing session that last touched a "
        "paragraph, run, row or section. Each save stamps its own session's "
        "ids, so two saves of the same content carry different ones."
    ),
    "w:proofErr": (
        "A spelling or grammar mark where Word's proofing last flagged a "
        "word. It holds no content, and where Word puts it depends on "
        "whether proofing ran before the save."
    ),
    "w:lastRenderedPageBreak": (
        "Where the page broke the last time Word laid the document out — a "
        "layout cache Word writes on save. It holds no content and moves "
        "with pagination; a real page break (w:br) is still compared."
    ),
    "the _GoBack bookmark": (
        "A hidden bookmark Word writes at the last edit position (the "
        "Shift+F5 target), so it appears, or moves, whenever a save follows "
        "an edit such as Accept All. It is navigation state, not content; "
        "every other bookmark, hidden or not, is still compared."
    ),
}


def _remove(element) -> None:
    """Remove ``element``, keeping any tail text on the tree."""
    parent = element.getparent()
    if parent is None:
        return
    tail = element.tail
    if tail:
        previous = element.getprevious()
        if previous is not None:
            previous.tail = (previous.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail
    parent.remove(element)


def word_body(payload: bytes):
    """The ``w:body`` of a DOCX, as plain lxml."""
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
    body = root.find(_W_BODY)
    if body is None:
        raise AssertionError("word/document.xml has no body")
    return body


def strip_word_save_markup(body):
    """A copy of ``body`` without what a Word save writes on its own (the
    first three :data:`WORD_SAVE_TOLERANCES`; the bookmark is excluded by
    name in :func:`body_difference`)."""
    stripped = copy.deepcopy(body)
    for element in list(stripped.iter(PROOFING_MARK, LAST_RENDERED_PAGE_BREAK)):
        _remove(element)
    for element in stripped.iter():
        if not isinstance(element.tag, str):
            continue
        for name in RSID_ATTRIBUTES.intersection(element.attrib):
            del element.attrib[name]
    return stripped


def _judged(payload: bytes):
    return strip_word_save_markup(word_body(payload))


def bookmark_names(payload: bytes) -> set[str]:
    return {
        start.get(_W_NAME, "")
        for start in word_body(payload).iter(_W_BOOKMARK_START)
    }


def body_difference(
    resolved,
    reference,
    *,
    exclude_bookmarks=frozenset(),
) -> Difference | None:
    """``None`` when two Word-saved bodies agree up to
    :data:`WORD_SAVE_TOLERANCES` — all four applied here, to both sides — else
    where they first disagree. The one comparison every verdict goes
    through."""
    return first_difference(
        strip_word_save_markup(resolved),
        strip_word_save_markup(reference),
        exclude_bookmarks=frozenset(exclude_bookmarks) | {GO_BACK_BOOKMARK},
    )


def word_difference(
    resolved: bytes,
    reference: bytes,
    *,
    exclude_bookmarks=frozenset(),
) -> Difference | None:
    """:func:`body_difference` of two Word-saved files."""
    return body_difference(
        word_body(resolved), word_body(reference), exclude_bookmarks=exclude_bookmarks
    )


def render_canonical(node, *, limit: int = 60) -> str:
    """A canonical body child as indented pseudo-XML, for the report."""
    lines: list[str] = []

    def walk(item, depth: int) -> None:
        if len(lines) >= limit:
            return
        tag, attributes, children, text = item
        name = etree.QName(tag).localname
        rendered = " ".join(
            f'{etree.QName(key).localname}="{value}"' for key, value in attributes
        )
        head = "  " * depth + f"<{name}{' ' + rendered if rendered else ''}>"
        lines.append(head + (f" {text!r}" if text else ""))
        for child in children:
            walk(child, depth + 1)

    walk(node, 0)
    if len(lines) >= limit:
        lines.append("  …")
    return "\n".join(lines)


def describe_difference(
    resolved: bytes,
    reference: bytes,
    difference: Difference,
    *,
    exclude_bookmarks=frozenset(),
) -> dict:
    """Where two Word-saved bodies disagree, with both sides rendered — the
    fixtures are placeholder text, so the report may carry it."""
    names = frozenset(exclude_bookmarks) | {GO_BACK_BOOKMARK}
    left = canonical_body(_judged(resolved), exclude_bookmarks=names)
    right = canonical_body(_judged(reference), exclude_bookmarks=names)

    def side(nodes):
        if difference.index < len(nodes):
            return render_canonical(nodes[difference.index])
        return "(nothing: the body ends before this child)"

    return {
        **difference.to_dict(),
        "resolved_by_word": side(left),
        "reference_resaved_by_word": side(right),
    }


def with_body(payload: bytes, body) -> bytes:
    """``payload`` with its ``w:body`` replaced by ``body``."""
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
    root.replace(root.find(_W_BODY), copy.deepcopy(body))
    document_xml = etree.tostring(
        root, xml_declaration=True, encoding="UTF-8", standalone=True
    )
    return rewrite_zip_members(
        payload, replacements={"word/document.xml": document_xml}
    )


def judge_is_configured(environ: Mapping[str, str] | None = None) -> bool:
    values = os.environ if environ is None else environ
    return values.get(JUDGE_ENV, "").strip().casefold() in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# The cases: targeted markup shapes, and the corpus sweep
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JudgeGroup:
    """One Word batch: every case built from one upload."""

    group_id: str
    upload: Callable[[Path], bytes]
    edits: tuple[tuple[str, Callable], ...]

    @property
    def slug(self) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]+", "-", self.group_id).strip("-")


@dataclass(frozen=True)
class JudgeCase:
    """A redline the app produced (so its own self-check already passed),
    with the formatted export Accept All must give."""

    name: str
    redline: bytes
    clean: bytes
    #: The bookmarks a moved copy carries — the one Reject-All limit (D-6).
    moved_bookmarks: frozenset[str]
    stats: dict


@dataclass(frozen=True)
class RefusedCase:
    """An edit mix the redline refused by name — nothing for Word to judge.
    A structural reason is a legitimate answer; any other one means the
    app's own self-check failed, which :func:`judge_batch` reports."""

    name: str
    reason: str


def structural_refusals() -> frozenset[str]:
    """The refusals a real master can legitimately earn — the corpus sweep's
    own set, so the judge and the sweep agree on what is not a failure."""
    from tests.test_redline_original import STRUCTURAL_REFUSALS

    return STRUCTURAL_REFUSALS


@dataclass(frozen=True)
class JudgeBatch:
    group: JudgeGroup
    upload: bytes
    cases: tuple[JudgeCase | RefusedCase, ...]

    @property
    def rendered(self) -> tuple[JudgeCase, ...]:
        return tuple(case for case in self.cases if isinstance(case, JudgeCase))


def _moved_bookmarks(redline: bytes) -> frozenset[str]:
    """Bookmarks inside content a ``w:ins`` or ``w:moveTo`` wraps: on a
    moved copy."""
    names = set()
    for start in word_body(redline).iter(_W_BOOKMARK_START):
        for ancestor in start.iterancestors():
            if (
                ancestor.tag in (f"{{{_W}}}ins", f"{{{_W}}}moveTo")
                and ancestor.getparent() is not None
                and ancestor.getparent().tag != _W_RPR
            ):
                names.add(start.get(_W_NAME, ""))
                break
    return frozenset(names)


def build_judge_cases(group: JudgeGroup, workspace: Path) -> JudgeBatch:
    """Import the group's upload once and render every edit's redline and
    formatted export — exactly as the app does, author and all, and with the
    switch the export route passes (``settings.REDLINE_NATIVE_MOVES``, read
    per call): Word judges the file a user would get."""
    from backend import settings

    workspace.mkdir(parents=True, exist_ok=True)
    upload = group.upload(workspace)
    master = workspace / "upload.docx"
    master.write_bytes(upload)
    imported = parse_master_docx(master)
    cases: list[JudgeCase | RefusedCase] = []
    for name, edit in group.edits:
        section = edit(imported.section)
        stats: dict = {}
        try:
            redline = render_preserving_redline(
                source_bytes=upload,
                format_map=imported.format_map,
                baseline=imported.section,
                current=section,
                author=JUDGE_AUTHOR,
                date=JUDGE_DATE,
                stats=stats,
                native_moves=settings.REDLINE_NATIVE_MOVES,
            )
        except SourceRedlineError as exc:
            cases.append(RefusedCase(name, exc.reason))
            continue
        clean = render_preserving_docx(
            source_bytes=upload, format_map=imported.format_map, current=section
        )
        cases.append(
            JudgeCase(
                name=name,
                redline=redline,
                clean=clean,
                moved_bookmarks=_moved_bookmarks(redline),
                stats={
                    key: value
                    for key, value in stats.items()
                    if isinstance(value, (int, dict))
                },
            )
        )
    return JudgeBatch(group=group, upload=upload, cases=tuple(cases))


def _edit(*ops_from_section):
    """An edit from op builders, each given the section as edited so far."""

    def edit(section):
        for build in ops_from_section:
            section, _ = apply_edits(section, [build(section)])
        return section

    return edit


def _article(section, index: int = 0, part: int = 0):
    return section.parts[part].articles[index]


def _provision(section, index: int = 0, *, article: int = 0):
    return _article(section, article).paragraphs[index]


def _replace(index: int, text: str, *, article: int = 0):
    return lambda s: {
        "action": "replace",
        "target_id": _provision(s, index, article=article).uid,
        "text": text,
    }


def _delete_provision(index: int, *, article: int = 0):
    return lambda s: {
        "action": "delete",
        "target_id": _provision(s, index, article=article).uid,
    }


def _add_provision(position, text: str, *, article: int = 0):
    def build(section):
        target = _article(section, article)
        where = len(target.paragraphs) if position == "end" else position
        return {
            "action": "add_paragraph",
            "target_id": target.uid,
            "position": where,
            "text": text,
        }

    return build


def _move_provision(index: int, position: int, *, article: int = 0):
    return lambda s: {
        "action": "move",
        "target_id": _provision(s, index, article=article).uid,
        "position": position,
    }


def _locked_block(kind: str, *, article: int = 0):
    def find(section):
        return next(
            p for p in _article(section, article).paragraphs if p.locked == kind
        )

    return find


def _targeted_groups() -> tuple[JudgeGroup, ...]:
    """The markup shapes the redline writes, each on the master that makes
    it: word-level splices (plain, inside bold, at run boundaries), typed
    letters, Word numbering, whole provisions and articles and tables
    deleted, moves (plain, with children, with a bookmark, a table, across
    section breaks), emptied section-break holders (and their numbering
    cancel), hyperlinks, a field, the fallback, the untrackable last
    paragraph, a leading page break, front matter, a picture, a bold mark,
    row properties, and Track Changes already on — and Word's own "Moved"
    marks (Phase 2, PR B): one move, a move with children, a block of
    siblings, two named moves, a move beside an edit, a move holding a
    link, a moved bookmark, a style-numbered master and a move a section
    break forced."""
    from tests import test_redline_original as matrix
    from tests.test_import_office_master import _office_master
    from tests.test_preserving_export import (
        _break_master,
        _master_bytes,
        _not_used_master,
        _typed_letter_master,
    )

    def upload(builder, *args, **kwargs):
        return lambda _workspace: builder(*args, **kwargs)

    reword = "Section includes seismic isolation for mechanical equipment."
    groups = [
        JudgeGroup(
            "targeted/styled-master",
            upload(_master_bytes),
            (
                ("one-word-edit", _edit(_replace(0, reword))),
                (
                    "insert-at-start",
                    _edit(
                        _replace(
                            0,
                            "Also Section includes vibration isolation for "
                            "mechanical equipment.",
                        )
                    ),
                ),
                (
                    "delete-table",
                    _edit(
                        lambda s: {
                            "action": "delete",
                            "target_id": _locked_block("table", article=1)(s).uid,
                        }
                    ),
                ),
                (
                    "renumber-header-line",
                    _edit(
                        lambda s: {
                            "action": "replace",
                            "target_id": "sec",
                            "text": "VIBRATION AND SEISMIC CONTROLS",
                            "numbering": "23 05 49",
                        }
                    ),
                ),
                ("move-with-its-spacer", _edit(_move_provision(1, 0))),
                ("delete-with-its-spacer", _edit(_delete_provision(1))),
            ),
        ),
        JudgeGroup(
            "targeted/tracking-already-on",
            upload(matrix._tracking_on_master),
            (("one-word-edit", _edit(_replace(0, "Changed."))),),
        ),
        JudgeGroup(
            "targeted/typed-letters",
            upload(_typed_letter_master),
            (
                ("edit-inside-bold", _edit(_replace(0, reword))),
                (
                    "delete-at-run-boundary",
                    _edit(_replace(0, "Section includes for mechanical equipment.")),
                ),
                ("reletter", _edit(_add_provision(0, "Provide seismic restraints."))),
            ),
        ),
        JudgeGroup(
            "targeted/word-numbered",
            upload(matrix._numbered_master),
            (
                (
                    "insert-and-delete",
                    _edit(
                        _delete_provision(1),
                        _add_provision(0, "Provide seismic restraints."),
                    ),
                ),
            ),
        ),
        JudgeGroup(
            "targeted/nested",
            upload(matrix._nested_master),
            (
                ("delete-parent-with-children", _edit(_delete_provision(0))),
                (
                    "delete-article",
                    _edit(
                        lambda s: {
                            "action": "delete",
                            "target_id": _article(s, 1).uid,
                        }
                    ),
                ),
                ("reorder-provisions", _edit(_move_provision(1, 0))),
                (
                    "reorder-article",
                    _edit(
                        lambda s: {
                            "action": "move",
                            "target_id": _article(s, 1).uid,
                            "position": 0,
                        }
                    ),
                ),
                (
                    "reorder-and-edit",
                    _edit(
                        _move_provision(1, 0),
                        _replace(0, "Related requirements are in Division 22."),
                    ),
                ),
            ),
        ),
        JudgeGroup(
            "targeted/heavy-first",
            upload(matrix._heavy_first_master),
            (("move-parent-below-siblings", _edit(_move_provision(0, 2))),),
        ),
        JudgeGroup(
            "targeted/not-used-part",
            upload(_not_used_master),
            (
                (
                    "fill-not-used-part",
                    _edit(
                        lambda s: {
                            "action": "add_article",
                            "target_id": "pt2",
                            "text": "ISOLATORS",
                        }
                    ),
                ),
            ),
        ),
        JudgeGroup(
            "targeted/break-paragraph",
            upload(_break_master, held=False),
            (
                (
                    "delete-below-break",
                    _edit(
                        lambda s: {
                            "action": "delete",
                            "target_id": _article(s, 1).uid,
                        }
                    ),
                ),
            ),
        ),
        JudgeGroup(
            "targeted/break-holder",
            upload(_break_master, held=True),
            (
                ("delete-holder", _edit(_delete_provision(1))),
                ("move-holder", _edit(_move_provision(1, 0))),
            ),
        ),
        JudgeGroup(
            "targeted/numbered-break-holder",
            upload(matrix._numbered_holder_master),
            (("delete-holder", _edit(_delete_provision(1))),),
        ),
        JudgeGroup(
            "targeted/fallback",
            upload(matrix._symbol_master),
            (("rewrite-whole", _edit(_replace(0, "See Section 23 05 00."))),),
        ),
        JudgeGroup(
            "targeted/links",
            upload(matrix._linked_master),
            (
                (
                    "reletter-with-link",
                    _edit(_add_provision(0, "Provide seismic restraints.")),
                ),
                (
                    "edit-inside-link",
                    _edit(
                        _replace(
                            0,
                            "Section includes vibration isolation per the owner "
                            "standard for mechanical equipment.",
                        )
                    ),
                ),
                (
                    "words-beside-link",
                    _edit(
                        _replace(
                            0,
                            "Section includes vibration isolation per the client "
                            "standard and NFPA 13 for mechanical equipment.",
                        )
                    ),
                ),
                (
                    "delete-link-words",
                    _edit(
                        _replace(
                            0,
                            "Section includes vibration isolation per mechanical "
                            "equipment.",
                        )
                    ),
                ),
            ),
        ),
        JudgeGroup(
            "targeted/link-with-bookmark",
            upload(matrix._linked_master, bookmark=True),
            (
                (
                    "replacement-into-link",
                    _edit(
                        _replace(
                            0,
                            "Section includes vibration isolation under client "
                            "standard for mechanical equipment.",
                        )
                    ),
                ),
            ),
        ),
        JudgeGroup(
            "targeted/page-field",
            upload(matrix._page_field_master),
            (("rewrite-fielded", _edit(_replace(0, "See Part 3."))),),
        ),
        JudgeGroup(
            "targeted/office-master",
            upload(_office_master),
            (
                (
                    "edit-beside-front-matter",
                    _edit(
                        lambda s: {
                            "action": "replace",
                            "target_id": next(
                                p
                                for part in s.parts
                                for article in part.articles
                                for p in article.paragraphs
                                if p.text == "Install per NFPA 13."
                            ).uid,
                            "text": "Install per NFPA 13-2025.",
                        }
                    ),
                ),
            ),
        ),
        JudgeGroup(
            "targeted/last-numbered",
            upload(matrix._formatted_to_the_end_master, "numbered"),
            (
                ("delete-last", _edit(lambda s: _delete_provision(-1)(s))),
                (
                    "append-last",
                    _edit(_add_provision("end", "Provide seismic restraints.")),
                ),
            ),
        ),
        JudgeGroup(
            "targeted/last-page-break",
            upload(matrix._formatted_to_the_end_master, "page-break"),
            (
                ("delete-last", _edit(lambda s: _delete_provision(-1)(s))),
                (
                    "append-last",
                    _edit(_add_provision("end", "Provide seismic restraints.")),
                ),
            ),
        ),
        JudgeGroup(
            "targeted/last-plain",
            upload(matrix._plain_last_master),
            (("delete-last", _edit(_delete_provision(1))),),
        ),
        JudgeGroup(
            "targeted/leading-page-break",
            upload(matrix._break_page_master),
            (
                (
                    "prepend-after-break",
                    _edit(_replace(0, "Also Section includes vibration isolation.")),
                ),
            ),
        ),
        JudgeGroup(
            "targeted/two-breaks",
            upload(matrix._two_break_master),
            (
                (
                    "reverse-across-breaks",
                    _edit(_move_provision(2, 0), lambda s: _move_provision(1, 2)(s)),
                ),
            ),
        ),
        JudgeGroup(
            "targeted/schedule-first",
            upload(matrix._schedule_first_master),
            (
                (
                    "move-table-below",
                    _edit(
                        lambda s: {
                            "action": "move",
                            "target_id": _locked_block("table")(s).uid,
                            "position": len(_article(s).paragraphs) - 1,
                        }
                    ),
                ),
            ),
        ),
        JudgeGroup(
            "targeted/picture",
            upload(matrix._picture_master),
            (
                (
                    "delete-picture",
                    _edit(
                        lambda s: {
                            "action": "delete",
                            "target_id": _locked_block("image")(s).uid,
                        }
                    ),
                ),
            ),
        ),
        JudgeGroup(
            "targeted/bold-mark",
            upload(matrix._bold_mark_master),
            (("delete-bold-mark", _edit(_delete_provision(1))),),
        ),
        JudgeGroup(
            "targeted/row-properties",
            upload(matrix._row_properties_master),
            (
                (
                    "delete-table",
                    _edit(
                        lambda s: {
                            "action": "delete",
                            "target_id": _locked_block("table")(s).uid,
                        }
                    ),
                ),
            ),
        ),
        JudgeGroup(
            "targeted/bookmarked",
            upload(matrix._bookmarked_master),
            (("move-bookmarked", _edit(_move_provision(1, 0))),),
        ),
        # Word's own "Moved" marks (Phase 2, PR B): every native shape, on
        # masters Word numbers itself — a typed letter would reletter a moved
        # provision, an edit that keeps the Phase 1 rendering.
        JudgeGroup(
            "targeted/native-moves",
            upload(matrix._numbered_family_master),
            (
                ("move-one", _edit(_move_provision(4, 0))),
                ("move-with-children", _edit(_move_provision(0, 2))),
                (
                    "move-block-of-siblings",
                    _edit(
                        _move_provision(3, 0),
                        lambda s: _move_provision(len(_article(s).paragraphs) - 1, 1)(s),
                    ),
                ),
                (
                    "two-separate-moves",
                    _edit(_move_provision(4, 0), _move_provision(2, 4)),
                ),
                (
                    "move-beside-an-edit",
                    _edit(
                        _move_provision(4, 0),
                        _replace(2, "Related requirements are in Division 22."),
                    ),
                ),
            ),
        ),
        JudgeGroup(
            "targeted/native-move-link",
            upload(matrix._numbered_family_master, link=True),
            (("move-with-link", _edit(_move_provision(2, 0))),),
        ),
        JudgeGroup(
            "targeted/native-move-bookmark",
            upload(matrix._numbered_family_master, bookmark=True),
            (("move-bookmarked", _edit(_move_provision(1, 0))),),
        ),
        JudgeGroup(
            "targeted/native-move-style-numbered",
            upload(matrix._style_numbered_master),
            (("move-one", _edit(_move_provision(1, 0))),),
        ),
        JudgeGroup(
            "targeted/native-move-two-breaks",
            upload(matrix._numbered_two_break_master),
            (
                (
                    "reverse-across-breaks",
                    _edit(_move_provision(2, 0), lambda s: _move_provision(1, 2)(s)),
                ),
            ),
        ),
    ]
    return tuple(groups)


def _corpus_groups() -> tuple[JudgeGroup, ...]:
    """Every corpus master under the corpus sweep's own edit mixes
    (``tests/test_redline_original.py``), so Word resolves exactly the
    redlines that suite proves."""
    from tests.docx_corpus import build_case, corpus_cases
    from tests.test_redline_original import CORPUS_SWEEP_SEEDS, corpus_sweep_edits

    groups = []
    for case in corpus_cases():
        groups.append(
            JudgeGroup(
                f"corpus/{case.case_id}",
                (lambda workspace, case=case: build_case(case, workspace)),
                tuple(
                    (
                        f"seed-{seed}",
                        (
                            lambda section, case_id=case.case_id, seed=seed: (
                                corpus_sweep_edits(case_id, section, seed)
                            )
                        ),
                    )
                    for seed in CORPUS_SWEEP_SEEDS
                ),
            )
        )
    return tuple(groups)


def judge_groups() -> tuple[JudgeGroup, ...]:
    return (*_targeted_groups(), *_corpus_groups())


# ---------------------------------------------------------------------------
# Judging one batch
# ---------------------------------------------------------------------------

Resolver = Callable[..., object]


@dataclass
class GroupVerdict:
    """What the judge found for one batch: a report entry per case, and the
    one-line failures pytest prints."""

    group_id: str
    cases: list[dict] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    word_version: str = ""
    word_build: str = ""


def _job_file(workspace: Path, name: str) -> Path:
    return workspace / name


def judge_batch(batch: JudgeBatch, workspace: Path, *, resolve: Resolver) -> GroupVerdict:
    """Hand the batch to one Word (``resolve`` is
    :func:`tools.render_docx_word.resolve_docx` or a fake) and judge every
    case: Word must open each file, read every change as Build-a-Spec's,
    leave no change behind, and — up to what its own save writes — Accept All
    must give the formatted export and Reject All the upload."""
    from tools.render_docx_word import ResolveJob

    verdict = GroupVerdict(batch.group.group_id)
    structural = structural_refusals()
    for case in batch.cases:
        if not isinstance(case, RefusedCase):
            continue
        entry = {"case": case.name, "status": "refused", "refusal": case.reason}
        if case.reason not in structural:
            problem = (
                f"the app refused it for {case.reason!r}, not a structural reason: "
                "its own self-check failed before Word saw the file"
            )
            entry.update(status="error", problems=[problem])
            verdict.failures.append(f"{batch.group.group_id} {case.name}: {problem}")
        verdict.cases.append(entry)
    if not batch.rendered:
        return verdict

    workspace.mkdir(parents=True, exist_ok=True)
    upload = _job_file(workspace, "upload.docx")
    upload.write_bytes(batch.upload)
    upload_word = _job_file(workspace, "upload.word.docx")
    jobs = [ResolveJob(upload, upload_word, "resave")]
    files: dict[str, dict[str, Path]] = {}
    for case in batch.rendered:
        redline = _job_file(workspace, f"{case.name}.redline.docx")
        formatted = _job_file(workspace, f"{case.name}.formatted.docx")
        redline.write_bytes(case.redline)
        formatted.write_bytes(case.clean)
        files[case.name] = {
            "redline": redline,
            "formatted": formatted,
            "formatted_word": _job_file(workspace, f"{case.name}.formatted.word.docx"),
            "accepted_word": _job_file(workspace, f"{case.name}.accepted.word.docx"),
            "rejected_word": _job_file(workspace, f"{case.name}.rejected.word.docx"),
        }
        jobs += [
            ResolveJob(formatted, files[case.name]["formatted_word"], "resave"),
            ResolveJob(redline, files[case.name]["accepted_word"], "accept"),
            ResolveJob(redline, files[case.name]["rejected_word"], "reject"),
        ]

    resolution = resolve(jobs)
    verdict.word_version = resolution.word_version
    verdict.word_build = resolution.word_build
    by_output = {document.job.output_path.name: document for document in resolution.documents}
    upload_result = by_output[upload_word.name]
    for case in batch.rendered:
        entry = _judge_case(
            case,
            upload_result,
            {role: by_output.get(path.name) for role, path in files[case.name].items()},
            files[case.name],
        )
        verdict.cases.append(entry)
        for problem in entry["problems"]:
            verdict.failures.append(f"{batch.group.group_id} {case.name}: {problem}")
    return verdict


def _judge_case(case: JudgeCase, upload_result, results: dict, paths: dict) -> dict:
    problems: list[str] = []
    entry: dict = {
        "case": case.name,
        "status": "pass",
        "redline": case.stats.get("redline", {}),
        "moved_bookmarks": sorted(case.moved_bookmarks),
        "files": {role: path.name for role, path in paths.items()},
        "problems": problems,
    }
    word_results = {
        "upload": upload_result,
        "formatted": results["formatted_word"],
        "accept": results["accepted_word"],
        "reject": results["rejected_word"],
    }
    for role, result in word_results.items():
        if result is None or not result.ok:
            reason = "no answer" if result is None else result.error
            problems.append(f"Word could not {role if role in ('accept', 'reject') else 'save'} the {role} file: {reason}")
    if problems:
        entry["status"] = "error"
        return entry

    accepted, rejected = word_results["accept"], word_results["reject"]
    entry["word"] = {
        "revisions_read": accepted.revisions_before,
        "authors": list(accepted.authors),
    }
    for result in (accepted, rejected):
        if result.revisions_after:
            problems.append(
                f"{result.revisions_after} tracked change(s) were still there after "
                f"Word's '{result.job.action}'"
            )
        if result.revisions_before and set(result.authors) != {JUDGE_AUTHOR}:
            problems.append(
                f"Word read the changes as authored by {sorted(result.authors)}, "
                f"not {JUDGE_AUTHOR!r}"
            )
    accepted_bytes = paths["accepted_word"].read_bytes()
    rejected_bytes = paths["rejected_word"].read_bytes()
    formatted_bytes = paths["formatted_word"].read_bytes()
    upload_bytes = upload_result.job.output_path.read_bytes()
    for action, payload in (("accept", accepted_bytes), ("reject", rejected_bytes)):
        if has_revisions(_judged(payload)):
            problems.append(f"the file Word saved after '{action}' still carries revision markup")

    accept_difference = word_difference(accepted_bytes, formatted_bytes)
    if accept_difference is not None:
        entry["accept"] = describe_difference(accepted_bytes, formatted_bytes, accept_difference)
        problems.append(
            "Accept All in Word differs from the formatted export at body child "
            f"{accept_difference.index} ({accept_difference.left or '-'} vs "
            f"{accept_difference.right or '-'}, at {accept_difference.path or '-'})"
        )

    lost = (bookmark_names(upload_bytes) - bookmark_names(rejected_bytes)) - {
        GO_BACK_BOOKMARK
    }
    entry["lost_bookmarks"] = sorted(lost)
    unexpected = lost - case.moved_bookmarks
    if unexpected:
        problems.append(
            f"Reject All in Word lost bookmark(s) {sorted(unexpected)} that no move carried"
        )
    reject_difference = word_difference(
        rejected_bytes, upload_bytes, exclude_bookmarks=lost
    )
    if reject_difference is not None:
        entry["reject"] = describe_difference(
            rejected_bytes, upload_bytes, reject_difference, exclude_bookmarks=lost
        )
        problems.append(
            "Reject All in Word differs from the upload at body child "
            f"{reject_difference.index} ({reject_difference.left or '-'} vs "
            f"{reject_difference.right or '-'}, at {reject_difference.path or '-'})"
        )
    if problems:
        entry["status"] = "fail"
    return entry


# ---------------------------------------------------------------------------
# Word's own tracked moves (the Word-saved sample, once the corpus has it)
# ---------------------------------------------------------------------------

#: The corpus case holding Word's own tracked moves (PR A's producer recipe,
#: ``tests/fixtures/docx_corpus/generate_word_fixtures.ps1 -Recipes
#: TrackedMove``), and what the recipe wrote into it.
TRACKED_MOVE_CASE_ID = "actual_word_16_tracked_move"
TRACKED_MOVE_AUTHOR = "Build-a-Spec Corpus"
TRACKED_MOVE_BOOKMARK = "Placeholder_Moved_Anchor"


def _paragraph_text(paragraph) -> str:
    return "".join(t.text or "" for t in paragraph.iter(_W_T, _W_DEL_TEXT))


def bookmark_places(payload: bytes, name: str) -> list[dict]:
    """Where bookmark ``name`` starts: the text of its paragraph, and which
    revision wrappers (and paragraph-mark flag) enclose it."""
    places = []
    for start in word_body(payload).iter(_W_BOOKMARK_START):
        if start.get(_W_NAME) != name:
            continue
        paragraph = next((a for a in start.iterancestors() if a.tag == _W_P), None)
        wrappers = [
            etree.QName(a).localname
            for a in start.iterancestors()
            if a.tag in _REVISION_WRAPPERS
        ]
        mark = None
        if paragraph is not None:
            properties = paragraph.find(_W_PPR)
            flags = properties.find(_W_RPR) if properties is not None else None
            if flags is not None:
                mark = next(
                    (
                        etree.QName(c).localname
                        for c in flags
                        if isinstance(c.tag, str) and c.tag in _REVISION_WRAPPERS
                    ),
                    None,
                )
        places.append(
            {
                "paragraph_text": _paragraph_text(paragraph) if paragraph is not None else "",
                "inside": wrappers,
                "paragraph_mark": mark,
            }
        )
    return places


def judge_tracked_move_sample(sample: bytes, workspace: Path, *, resolve: Resolver) -> dict:
    """Word's own tracked moves, resolved by Word and by the app's oracle.

    The oracle (``revisions.accept_all``/``reject_all``) already resolves
    ``w:moveFrom``/``w:moveTo``; Phase 2's writer will lean on it. Here Word
    accepts and rejects a file Word itself wrote, and the oracle's answers —
    re-saved by the same Word — must match. Where the moved bookmark lands
    each way is recorded as evidence for Phase 2's D-6 decision."""
    from backend.spec_doc.revisions import accept_all, reject_all
    from tools.render_docx_word import ResolveJob

    workspace.mkdir(parents=True, exist_ok=True)
    source = workspace / "sample.docx"
    source.write_bytes(sample)
    body = word_body(sample)
    oracle_accept = workspace / "sample.oracle-accepted.docx"
    oracle_reject = workspace / "sample.oracle-rejected.docx"
    oracle_accept.write_bytes(with_body(sample, accept_all(body)))
    oracle_reject.write_bytes(with_body(sample, reject_all(body)))
    outputs = {
        "accept": workspace / "sample.accepted.word.docx",
        "reject": workspace / "sample.rejected.word.docx",
        "oracle_accept": workspace / "sample.oracle-accepted.word.docx",
        "oracle_reject": workspace / "sample.oracle-rejected.word.docx",
    }
    resolution = resolve(
        [
            ResolveJob(source, outputs["accept"], "accept"),
            ResolveJob(source, outputs["reject"], "reject"),
            ResolveJob(oracle_accept, outputs["oracle_accept"], "resave"),
            ResolveJob(oracle_reject, outputs["oracle_reject"], "resave"),
        ]
    )
    problems: list[str] = []
    results = {
        role: next(d for d in resolution.documents if d.job.output_path == path)
        for role, path in outputs.items()
    }
    for role, result in results.items():
        if not result.ok:
            problems.append(f"Word could not handle {role}: {result.error}")
    report: dict = {
        "case": TRACKED_MOVE_CASE_ID,
        "word_version": resolution.word_version,
        "word_build": resolution.word_build,
        "problems": problems,
    }
    if problems:
        return report
    accepted, rejected = results["accept"], results["reject"]
    report["revisions_read"] = accepted.revisions_before
    report["authors"] = list(accepted.authors)
    if not accepted.revisions_before:
        problems.append("Word read no tracked changes in its own tracked-move sample")
    if set(accepted.authors) != {TRACKED_MOVE_AUTHOR}:
        problems.append(f"Word read the sample's authors as {sorted(accepted.authors)}")
    for result in (accepted, rejected):
        if result.revisions_after:
            problems.append(
                f"{result.revisions_after} tracked change(s) left after '{result.job.action}'"
            )
    payloads = {role: path.read_bytes() for role, path in outputs.items()}
    for action, reference in (("accept", "oracle_accept"), ("reject", "oracle_reject")):
        difference = word_difference(payloads[action], payloads[reference])
        if difference is not None:
            report[action] = describe_difference(
                payloads[action], payloads[reference], difference
            )
            problems.append(
                f"Word's {action} of its own moves differs from the oracle's at body "
                f"child {difference.index} ({difference.left or '-'} vs "
                f"{difference.right or '-'}, at {difference.path or '-'})"
            )
    report["bookmark"] = {
        "in_sample": bookmark_places(sample, TRACKED_MOVE_BOOKMARK),
        "after_word_accept": bookmark_places(payloads["accept"], TRACKED_MOVE_BOOKMARK),
        "after_word_reject": bookmark_places(payloads["reject"], TRACKED_MOVE_BOOKMARK),
        "after_oracle_accept": bookmark_places(
            payloads["oracle_accept"], TRACKED_MOVE_BOOKMARK
        ),
        "after_oracle_reject": bookmark_places(
            payloads["oracle_reject"], TRACKED_MOVE_BOOKMARK
        ),
    }
    return report


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


class JudgeReport:
    """``report.json``: rewritten after every batch, so a run that stops
    part way still says what it found. It carries the fixtures' placeholder
    text and file names — never a path."""

    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        run_dir = root / f"run-{stamp}"
        suffix = 1
        while run_dir.exists():
            suffix += 1
            run_dir = root / f"run-{stamp}-{suffix}"
        run_dir.mkdir()
        self.run_dir = run_dir
        self.path = root / "report.json"
        self.data: dict = {
            "schema": 1,
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "run": run_dir.name,
            "word": {},
            "tolerances": dict(WORD_SAVE_TOLERANCES),
            "groups": {},
            "tracked_move_sample": None,
        }
        self._started = time.monotonic()

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "JudgeReport":
        values = os.environ if environ is None else environ
        configured = values.get(JUDGE_DIR_ENV, "").strip()
        return cls(Path(configured).expanduser().resolve() if configured else DEFAULT_JUDGE_DIR)

    def record_word(self, version: str, build: str) -> None:
        if version or build:
            self.data["word"] = {"version": version, "build": build}

    def record_group(self, verdict: GroupVerdict) -> None:
        self.record_word(verdict.word_version, verdict.word_build)
        self.data["groups"][verdict.group_id] = {
            "cases": verdict.cases,
            "failures": len(verdict.failures),
        }
        self.write()

    def record_sample(self, sample: dict) -> None:
        self.record_word(sample.get("word_version", ""), sample.get("word_build", ""))
        self.data["tracked_move_sample"] = sample
        self.write()

    def write(self) -> None:
        cases = [
            case for group in self.data["groups"].values() for case in group["cases"]
        ]
        self.data["summary"] = {
            status: sum(1 for case in cases if case.get("status") == status)
            for status in ("pass", "fail", "error", "refused")
        }
        self.data["elapsed_seconds"] = round(time.monotonic() - self._started, 1)
        text = json.dumps(self.data, indent=2, sort_keys=False) + "\n"
        for target in (self.path, self.run_dir / "report.json"):
            target.write_text(text, encoding="utf-8")


__all__ = [
    "DEFAULT_JUDGE_DIR",
    "GO_BACK_BOOKMARK",
    "JUDGE_AUTHOR",
    "JUDGE_DATE",
    "JUDGE_DIR_ENV",
    "JUDGE_ENV",
    "JUDGE_SKIP_REASON",
    "LAST_RENDERED_PAGE_BREAK",
    "PROOFING_MARK",
    "RSID_ATTRIBUTES",
    "TRACKED_MOVE_AUTHOR",
    "TRACKED_MOVE_BOOKMARK",
    "TRACKED_MOVE_CASE_ID",
    "WORD_SAVE_TOLERANCES",
    "GroupVerdict",
    "JudgeBatch",
    "JudgeCase",
    "JudgeGroup",
    "JudgeReport",
    "RefusedCase",
    "bookmark_names",
    "body_difference",
    "bookmark_places",
    "build_judge_cases",
    "describe_difference",
    "judge_batch",
    "judge_groups",
    "judge_is_configured",
    "judge_tracked_move_sample",
    "render_canonical",
    "strip_word_save_markup",
    "structural_refusals",
    "with_body",
    "word_body",
    "word_difference",
]
