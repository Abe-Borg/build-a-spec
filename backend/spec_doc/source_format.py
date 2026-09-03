"""The import → export formatting contract for a preserved Word master.

Build-a-Spec's older source-preserving mode PATCHED the uploaded package in
place and had to prove every byte outside an approved text slice was
untouched. That promise was so strong it left almost nothing editable: on a
clean master, three of twenty-seven body operations.

This module backs the promise that replaced it. The deal is now:

    Everything except the body of ``word/document.xml`` is carried through
    byte-for-byte — headers, footers, styles, theme, fonts, numbering
    definitions, page setup. Inside the body, a provision you did not touch
    is emitted as a byte-identical clone of its source element, a provision
    you edited keeps its paragraph and run properties, and preserved blocks
    (tables, pictures, embedded objects, content controls) are emitted
    verbatim. What you may change is the *content and its order*.

So the retained source bytes ARE the format store: nothing here copies
formatting out of the package, it only records WHERE each semantic element
came from and HOW its label was expressed. :mod:`backend.spec_doc.source_render`
reads that back at export time.

Label kinds are the one genuinely non-obvious recording. The importer strips
a literal ``A.`` / ``1.`` / ``a.`` / ``1)`` label off a manually labelled
provision (``importer._LEVEL_RES``), because Build-a-Spec numbers positionally
so an inserted provision renumbers its siblings for free. An auto-numbered
master carries no such text at all — the label lives in ``w:numPr`` and Word
renders it. Those two cases need opposite treatment on the way out, and the
literal itself never needs storing: ``model._paragraph_label`` regenerates
exactly the four forms the importer recognizes.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

#: The label was Word's own (``w:numPr``). Word renders it; we must not
#: write one into the text or it would render twice.
LABEL_AUTO = "auto"
#: The importer stripped a literal text label; the export writes the
#: positionally regenerated one back.
LABEL_MANUAL = "manual"
#: The text is verbatim — no label was recognized, so none is added.
LABEL_NONE = "none"

LABEL_KINDS = frozenset({LABEL_AUTO, LABEL_MANUAL, LABEL_NONE})

#: The section-header TITLE, when the master put it on its own line. A
#: compound key rather than a second field on the section anchor, because it
#: is a second source element whose formatting the export needs
#: independently (a centered title under a bold number line is ordinary).
SECTION_TITLE_UID = "sec:title"

#: A semantic element that has no source element behind it (added in the app
#: after import). The export clones a formatting template from its nearest
#: kin instead.
NO_ORIGIN = -1

#: Where the section identity was read from at import. A header LINE in the
#: body is anchored and reproduced by the export; a cover page's
#: ``Section Number:`` field (FRONT_MATTER) and the page header/footer
#: (CHROME) are content the export carries through verbatim anyway, so no
#: header element is synthesized for them — one is already printed.
HEADER_SOURCE_LINE = "line"
HEADER_SOURCE_FRONT_MATTER = "front_matter"
HEADER_SOURCE_CHROME = "chrome"
HEADER_SOURCES = frozenset(
    {"", HEADER_SOURCE_LINE, HEADER_SOURCE_FRONT_MATTER, HEADER_SOURCE_CHROME}
)


@dataclass(frozen=True)
class FormatAnchor:
    """Where one semantic element came from, and how its label was written."""

    uid: str
    origin_index: int = NO_ORIGIN
    label_kind: str = LABEL_NONE
    locked: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "origin": self.origin_index,
            "label": self.label_kind,
            "locked": self.locked,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "FormatAnchor":
        if not isinstance(data, dict):
            raise ValueError("format anchor must be an object")
        uid = str(data.get("uid", "") or "")
        if not uid:
            raise ValueError("format anchor requires a uid")
        try:
            origin = int(data.get("origin", NO_ORIGIN))
        except (TypeError, ValueError):
            origin = NO_ORIGIN
        label = str(data.get("label", LABEL_NONE) or LABEL_NONE)
        if label not in LABEL_KINDS:
            # Fail SOFT, and toward the safest of the three: writing no label
            # can only under-decorate a provision, while guessing ``manual``
            # on an auto-numbered one renders the label twice.
            label = LABEL_NONE
        return cls(
            uid=uid,
            origin_index=origin if origin >= 0 else NO_ORIGIN,
            label_kind=label,
            locked=str(data.get("locked", "") or ""),
        )


@dataclass(frozen=True)
class SourceFormatMap:
    """Every semantic element's origin, bound to the package it describes.

    ``document_sha256`` is over the retained upload. It is an identity
    binding, not a security control: a map read beside different bytes
    describes body indexes that no longer mean anything, and the export
    refuses rather than cloning formatting from whatever now sits at that
    index.
    """

    document_sha256: str
    body_child_count: int
    anchors: tuple[FormatAnchor, ...] = ()
    #: Readable text from the package's headers and footers, captured once at
    #: import. They are never rewritten — that is the contract — but a spec
    #: footer conventionally carries the section identifier ("23 05 48 - 1"),
    #: which is exactly what changes when a master is adapted. Retaining the
    #: text lets the app SAY so rather than let a stale number reach an issued
    #: deliverable. Deliberately not an editing surface.
    header_footer_text: tuple[str, ...] = ()
    #: The section number and title as the import read them. The export
    #: reproduces the header ELEMENT verbatim while both still match, because
    #: the header line has several legitimate forms — "SECTION 23 05 48", a
    #: bare "23 05 48 — TITLE", a number and title on separate lines — and
    #: reconstructing one canonical form would rewrite the firm's header on a
    #: no-op export. Once the user changes the section identity there is
    #: nothing to reproduce and the canonical form is written instead.
    section_number: str = ""
    section_title: str = ""
    #: Readable text of the body blocks before the first PART/article
    #: heading — a cover page, a revision history, a table of contents. Like
    #: the header/footer text: evidence for the stale-identifier lint, never
    #: an editing surface; the export carries the blocks through verbatim.
    front_matter_text: tuple[str, ...] = ()
    #: One of the HEADER_SOURCE_* values, or "" when nothing stated the
    #: identity. The renderer synthesizes a header only for "".
    header_source: str = ""

    def anchor(self, uid: str) -> FormatAnchor | None:
        return self._by_uid.get(uid)

    def preserved_chrome(self) -> tuple[str, ...]:
        """Every preserved line the stale-identifier lint should read."""
        return tuple(self.header_footer_text) + tuple(self.front_matter_text)

    @property
    def _by_uid(self) -> dict[str, FormatAnchor]:
        cached = getattr(self, "_uid_cache", None)
        if cached is None:
            cached = {anchor.uid: anchor for anchor in self.anchors}
            object.__setattr__(self, "_uid_cache", cached)
        return cached

    def matches(self, source_bytes: bytes) -> bool:
        return (
            isinstance(source_bytes, bytes)
            and hashlib.sha256(source_bytes).hexdigest() == self.document_sha256
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "buildaspec-source-format",
            "format": 1,
            "document_sha256": self.document_sha256,
            "body_child_count": self.body_child_count,
            "anchors": [anchor.to_dict() for anchor in self.anchors],
            "header_footer_text": list(self.header_footer_text),
            "section_number": self.section_number,
            "section_title": self.section_title,
            "front_matter_text": list(self.front_matter_text),
            "header_source": self.header_source,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "SourceFormatMap":
        if not isinstance(data, dict):
            raise ValueError("source format map must be an object")
        digest = str(data.get("document_sha256", "") or "")
        if len(digest) != 64:
            raise ValueError("source format map requires a document digest")
        raw_anchors = data.get("anchors")
        if not isinstance(raw_anchors, list):
            raise ValueError("source format map requires an anchor list")
        anchors = tuple(FormatAnchor.from_dict(item) for item in raw_anchors)
        seen: set[str] = set()
        for anchor in anchors:
            if anchor.uid in seen:
                raise ValueError(f"duplicate format anchor for {anchor.uid!r}")
            seen.add(anchor.uid)
        try:
            count = int(data.get("body_child_count", 0))
        except (TypeError, ValueError):
            raise ValueError("source format map requires a body child count")
        raw_chrome = data.get("header_footer_text")
        chrome = (
            tuple(str(item) for item in raw_chrome if isinstance(item, str))
            if isinstance(raw_chrome, list)
            else ()
        )
        raw_front = data.get("front_matter_text")
        front_matter = (
            tuple(str(item) for item in raw_front if isinstance(item, str))
            if isinstance(raw_front, list)
            else ()
        )
        header_source = str(data.get("header_source", "") or "")
        if header_source not in HEADER_SOURCES:
            # Fail soft toward "a header line exists": that only ever makes
            # the export reproduce a header it can find an anchor for.
            header_source = ""
        return cls(
            document_sha256=digest,
            body_child_count=max(0, count),
            anchors=anchors,
            header_footer_text=chrome,
            section_number=str(data.get("section_number", "") or ""),
            section_title=str(data.get("section_title", "") or ""),
            front_matter_text=front_matter,
            header_source=header_source,
        )


def build_format_map(
    *,
    source_bytes: bytes,
    body_child_count: int,
    anchors: list[FormatAnchor],
    header_footer_text: tuple[str, ...] = (),
    section_number: str = "",
    section_title: str = "",
    front_matter_text: tuple[str, ...] = (),
    header_source: str = "",
) -> SourceFormatMap:
    """Freeze the anchors captured during one import against its upload.

    One anchor per uid, first wins: ``from_dict`` refuses a duplicate, and a
    map that could not be read back would fail every later project save.
    """
    if not isinstance(source_bytes, bytes):
        raise TypeError("source_bytes must be bytes")
    unique: list[FormatAnchor] = []
    seen: set[str] = set()
    for anchor in anchors:
        if anchor.uid in seen:
            continue
        seen.add(anchor.uid)
        unique.append(anchor)
    if header_source not in HEADER_SOURCES:
        header_source = ""
    return SourceFormatMap(
        document_sha256=hashlib.sha256(source_bytes).hexdigest(),
        body_child_count=max(0, int(body_child_count)),
        anchors=tuple(unique),
        header_footer_text=tuple(header_footer_text),
        section_number=section_number,
        section_title=section_title,
        front_matter_text=tuple(front_matter_text),
        header_source=header_source,
    )


__all__ = [
    "FormatAnchor",
    "HEADER_SOURCES",
    "HEADER_SOURCE_CHROME",
    "HEADER_SOURCE_FRONT_MATTER",
    "HEADER_SOURCE_LINE",
    "LABEL_AUTO",
    "LABEL_KINDS",
    "LABEL_MANUAL",
    "LABEL_NONE",
    "NO_ORIGIN",
    "SECTION_TITLE_UID",
    "SourceFormatMap",
    "build_format_map",
]
