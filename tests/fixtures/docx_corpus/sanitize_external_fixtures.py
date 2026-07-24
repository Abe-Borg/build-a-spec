"""Apply the documented privacy rewrite to external DOCX corpus fixtures.

Only ``docProps/core.xml`` is changed.  The raw ZIP rewriter preserves every
other local record, central-directory record, gap, comment, and trailing byte
from the producer output.
"""
from __future__ import annotations

import argparse
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from lxml import etree

from backend.spec_doc.raw_zip import replace_raw_zip_member


_CORE_PART = "docProps/core.xml"
_CP_NS = (
    "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
)
_DC_NS = "http://purl.org/dc/elements/1.1/"


def sanitize_core_properties(payload: bytes) -> bytes:
    with ZipFile(BytesIO(payload), "r") as archive:
        root = etree.fromstring(archive.read(_CORE_PART))

    replacements = (
        (f"{{{_DC_NS}}}creator", "Build-a-Spec Sanitized Corpus"),
        (f"{{{_CP_NS}}}lastModifiedBy", "Build-a-Spec Sanitized Corpus"),
        (f"{{{_DC_NS}}}title", "Sanitized real-producer DOCX corpus fixture"),
        (f"{{{_CP_NS}}}keywords", "sanitized interoperability fixture"),
    )
    for tag, value in replacements:
        node = root.find(tag)
        if node is None:
            node = etree.SubElement(root, tag)
        node.text = value

    core_xml = etree.tostring(
        root,
        encoding="UTF-8",
        xml_declaration=True,
        standalone=True,
    )
    return replace_raw_zip_member(
        payload,
        filename=_CORE_PART,
        payload=core_xml,
    )


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Sanitize DOCX core properties without canonicalizing its ZIP"
    )
    parser.add_argument("fixtures", nargs="+", type=Path)
    args = parser.parse_args()
    for fixture in args.fixtures:
        fixture.write_bytes(sanitize_core_properties(fixture.read_bytes()))


if __name__ == "__main__":
    _main()


__all__ = ["sanitize_core_properties"]
