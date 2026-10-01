"""Prepare the primary tracked Word export without resaving the DOCX package.

Revision-bearing uploads get an export-only accepted baseline matching the
import view, with remapped body origins. After ``source_render`` redlines and
checks the body, a second pass enables recording in the settings part. Both
passes use the raw ZIP rewriter and preserve every unrelated record.
"""
from __future__ import annotations

import hashlib
import posixpath
import zipfile
from dataclasses import replace
from io import BytesIO

from docx.oxml import parse_xml
from docx.oxml.ns import qn
from lxml import etree

from .raw_zip import rewrite_raw_zip_members
from .revisions import accept_all, has_revisions
from .source_format import NO_ORIGIN, SourceFormatMap

_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_SETTINGS_REL = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings"
)
_CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_SETTINGS_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"
)
_RELS_PART = "word/_rels/document.xml.rels"
_TYPES_PART = "[Content_Types].xml"
# CT_Settings schema order: trackRevisions follows revisionView and precedes
# doNotTrackMoves, documentProtection, and the remaining document settings.
_BEFORE_TRACKING = frozenset(
    qn(f"w:{name}")
    for name in (
        "writeProtection", "view", "zoom", "removePersonalInformation",
        "removeDateAndTime", "doNotDisplayPageBoundaries", "displayBackgroundShape",
        "printPostScriptOverText", "printFractionalCharacterWidth", "printFormsData",
        "embedTrueTypeFonts", "embedSystemFonts", "saveSubsetFonts", "saveFormsData",
        "mirrorMargins", "alignBordersAndEdges", "bordersDoNotSurroundHeader",
        "bordersDoNotSurroundFooter", "gutterAtTop", "hideSpellingErrors",
        "hideGrammaticalErrors", "activeWritingStyle", "proofState", "formsDesign",
        "attachedTemplate", "linkStyles", "stylePaneFormatFilter", "stylePaneSortMethod",
        "documentType", "mailMerge", "revisionView",
    )
)


def _xml(element) -> bytes:
    return etree.tostring(element, encoding="UTF-8", xml_declaration=True)


class TrackingExportError(ValueError):
    """A primary export could not establish its baseline or enable tracking."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def accepted_revision_baseline(
    source_bytes: bytes, format_map: SourceFormatMap,
) -> tuple[bytes, SourceFormatMap]:
    """The accepted import view as an export-only baseline, with origins remapped.

    Imports read accepted text. Pending revisions in an office master must
    therefore be resolved on a copy before tracking the app's subsequent edits.
    Accept current properties in every story, including headers and footers;
    the retained original and its map are never modified.
    """
    try:
        return _accepted_revision_baseline(source_bytes, format_map)
    except (ValueError, KeyError, etree.XMLSyntaxError, zipfile.BadZipFile) as exc:
        raise TrackingExportError(
            "accepted_revision_baseline_unavailable",
            "Could not prepare the accepted Word revision baseline: " + str(exc),
        ) from exc


def _accepted_revision_baseline(
    source_bytes: bytes, format_map: SourceFormatMap,
) -> tuple[bytes, SourceFormatMap]:
    if not format_map.matches(source_bytes):
        raise ValueError("The formatting map does not match the Word original.")
    replacements: dict[str, bytes] = {}
    origins: dict[int, int] | None = None
    accepted_child_count = format_map.body_child_count
    with zipfile.ZipFile(BytesIO(source_bytes)) as archive:
        for name in archive.namelist():
            if not (name.startswith("word/") and name.endswith(".xml")):
                continue
            root = parse_xml(archive.read(name))
            if not has_revisions(root):
                continue
            if name == "word/document.xml":
                body = root.find(qn("w:body"))
                if body is None:
                    raise ValueError("The Word original has no document body.")
                for index, child in enumerate(child for child in body if isinstance(child.tag, str)):
                    child.set("basOriginIndex", str(index))
            resolved = accept_all(root)
            if has_revisions(resolved):
                raise ValueError("Existing Word revisions could not be fully resolved.")
            if name == "word/document.xml":
                body = resolved.find(qn("w:body"))
                origins = {}
                children = [child for child in body if isinstance(child.tag, str)]
                accepted_child_count = len(children)
                for index, child in enumerate(children):
                    origin = child.attrib.pop("basOriginIndex", None)
                    if origin is not None:
                        origins[int(origin)] = index
                for anchor in format_map.anchors:
                    if anchor.locked and anchor.origin_index >= 0 and anchor.origin_index not in origins:
                        raise ValueError(
                            "Existing revisions remove a mapped body element. Accept "
                            "or reject those changes in Word and import it again."
                        )
            replacements[name] = _xml(resolved)
    if not replacements:
        return source_bytes, format_map
    accepted = rewrite_raw_zip_members(source_bytes, replacements=replacements)
    mapped = replace(
        format_map,
        document_sha256=hashlib.sha256(accepted).hexdigest(),
        body_child_count=accepted_child_count,
        anchors=tuple(
            # Accepting a deleted paragraph mark can merge its paragraph
            # into the next. The semantic importer retained those as two
            # provisions; the missing one is now a new paragraph, tracked
            # by the ordinary insertion path, while Reject All restores
            # the accepted Word paragraph. Never guess for an opaque block.
            replace(anchor, origin_index=origins.get(anchor.origin_index, NO_ORIGIN))
            if origins is not None and anchor.origin_index >= 0 else anchor
            for anchor in format_map.anchors
        ),
    )
    return accepted, mapped


def enable_track_changes(payload: bytes) -> bytes:
    """Enable future edits in Word; existing edits must already be revisions.

    Follow the document's settings relationship, including nonstandard part
    names. A missing settings part gets only the tracking switch, so default
    template settings cannot change the uploaded document's appearance.
    """
    try:
        return _enable_track_changes(payload)
    except (ValueError, KeyError, etree.XMLSyntaxError, zipfile.BadZipFile) as exc:
        raise TrackingExportError(
            "tracking_settings_unavailable", "Could not enable Word Track Changes: " + str(exc),
        ) from exc


def _enable_track_changes(payload: bytes) -> bytes:
    replacements: dict[str, bytes] = {}
    additions: list[tuple[str, bytes]] = []
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        names = set(archive.namelist())
        relationships = (
            parse_xml(archive.read(_RELS_PART))
            if _RELS_PART in names
            else etree.Element(f"{{{_REL_NS}}}Relationships", nsmap={None: _REL_NS})
        )
        settings_rel = next(
            (rel for rel in relationships if rel.get("Type") == _SETTINGS_REL), None
        )
        if settings_rel is not None:
            if settings_rel.get("TargetMode") == "External":
                raise ValueError("The Word settings relationship is external.")
            target = settings_rel.get("Target", "")
            part = posixpath.normpath(posixpath.join("word", target)).lstrip("/")
            settings = parse_xml(archive.read(part))
            if settings.tag != qn("w:settings"):
                raise ValueError("The Word settings part has an unexpected root.")
        else:
            part = "word/settings.xml"
            if part in names:
                raise ValueError("The Word settings part is not registered.")
            settings = etree.Element(qn("w:settings"), nsmap={"w": _W_NS})
            used_ids = {rel.get("Id") for rel in relationships}
            number = 1
            while f"rId{number}" in used_ids:
                number += 1
            etree.SubElement(
                relationships, f"{{{_REL_NS}}}Relationship",
                Id=f"rId{number}", Type=_SETTINGS_REL, Target="settings.xml",
            )
            if _RELS_PART in names:
                replacements[_RELS_PART] = _xml(relationships)
            else:
                additions.append((_RELS_PART, _xml(relationships)))
            types = parse_xml(archive.read(_TYPES_PART))
            if _RELS_PART not in names and not any(
                child.get("Extension") == "rels" for child in types
            ):
                etree.SubElement(
                    types, f"{{{_CT_NS}}}Default", Extension="rels",
                    ContentType="application/vnd.openxmlformats-package.relationships+xml",
                )
            etree.SubElement(
                types, f"{{{_CT_NS}}}Override",
                PartName=f"/{part}", ContentType=_SETTINGS_TYPE,
            )
            replacements[_TYPES_PART] = _xml(types)

        tracking = settings.find(qn("w:trackRevisions"))
        if tracking is None:
            tracking = etree.Element(qn("w:trackRevisions"))
            position = next(
                (i for i, child in enumerate(settings)
                 if isinstance(child.tag, str) and child.tag not in _BEFORE_TRACKING),
                len(settings),
            )
            settings.insert(position, tracking)
        tracking.set(qn("w:val"), "true")
        if part in names:
            replacements[part] = _xml(settings)
        else:
            additions.append((part, _xml(settings)))
    return rewrite_raw_zip_members(payload, replacements=replacements, additions=additions)
