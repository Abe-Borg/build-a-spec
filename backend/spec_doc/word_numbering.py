"""Deterministic Word numbering for normalized SectionFormat exports.

The source-preserving exporter never imports this module: it continues to
clone and patch the immutable source package.  Normalized redlines likewise
retain their established literal-label/tracked-change representation.  This
helper is only for a clean document rebuilt from the semantic tree.
"""
from __future__ import annotations

from dataclasses import dataclass

from docx.document import Document as DocxDocument
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph as DocxParagraph


_LEVELS = (
    # numFmt, lvlText
    ("upperLetter", "%1."),
    ("decimal", "%2."),
    ("lowerLetter", "%3."),
    ("decimal", "%4)"),
)
_INDENT_DXA = 648  # 0.45 inch, matching the established clean-export layout.
_ABSTRACT_NAME = "Build-a-Spec SectionFormat Provisions"


def _decimal_child(tag: str, value: int):
    child = OxmlElement(tag)
    child.set(qn("w:val"), str(value))
    return child


def _string_child(tag: str, value: str):
    child = OxmlElement(tag)
    child.set(qn("w:val"), value)
    return child


def _level(ilvl: int, number_format: str, level_text: str):
    """Build one schema-ordered ``w:lvl`` definition."""
    level = OxmlElement("w:lvl")
    level.set(qn("w:ilvl"), str(ilvl))
    level.append(_decimal_child("w:start", 1))
    level.append(_string_child("w:numFmt", number_format))
    # Do not emit w:lvlRestart for the ordinary immediate-parent behavior.
    # Its specified omission semantics already restart a child after the
    # preceding level.  Word 16 ignores our otherwise-valid child definitions
    # when the redundant explicit value is present, while LibreOffice accepts
    # them, so omission is the interoperable representation of the same rule.
    level.append(_string_child("w:suff", "tab"))
    level.append(_string_child("w:lvlText", level_text))
    level.append(_string_child("w:lvlJc", "left"))

    left = _INDENT_DXA * (ilvl + 1)
    paragraph_properties = OxmlElement("w:pPr")
    tabs = OxmlElement("w:tabs")
    tab = OxmlElement("w:tab")
    tab.set(qn("w:val"), "num")
    tab.set(qn("w:pos"), str(left))
    tabs.append(tab)
    paragraph_properties.append(tabs)
    indent = OxmlElement("w:ind")
    indent.set(qn("w:left"), str(left))
    indent.set(qn("w:hanging"), str(_INDENT_DXA))
    paragraph_properties.append(indent)
    level.append(paragraph_properties)
    return level


def _abstract_numbering(abstract_num_id: int):
    abstract = OxmlElement("w:abstractNum")
    abstract.set(qn("w:abstractNumId"), str(abstract_num_id))
    # Fixed identifiers avoid the random values emitted by Word while still
    # giving the definition a stable identity when a document is reopened.
    abstract.append(_string_child("w:nsid", "42535043"))
    abstract.append(_string_child("w:multiLevelType", "multilevel"))
    abstract.append(_string_child("w:tmpl", "53464336"))
    abstract.append(_string_child("w:name", _ABSTRACT_NAME))
    for ilvl, (number_format, level_text) in enumerate(_LEVELS):
        abstract.append(_level(ilvl, number_format, level_text))
    return abstract


@dataclass
class SectionFormatNumbering:
    """One shared abstract definition with one concrete list per article."""

    document: DocxDocument

    def __post_init__(self) -> None:
        numbering = self.document.part.numbering_part.element
        abstract_ids = [
            int(item.get(qn("w:abstractNumId")))
            for item in numbering.findall(qn("w:abstractNum"))
        ]
        self._numbering = numbering
        self._abstract_num_id = max(abstract_ids, default=-1) + 1
        abstract = _abstract_numbering(self._abstract_num_id)
        first_num = numbering.find(qn("w:num"))
        if first_num is None:
            numbering.append(abstract)
        else:
            numbering.insert(numbering.index(first_num), abstract)

    def new_article(self) -> int:
        """Mint a fresh level-0-at-1 numbering instance for one article."""
        instance = self._numbering.add_num(self._abstract_num_id)
        override = OxmlElement("w:lvlOverride")
        override.set(qn("w:ilvl"), "0")
        override.append(_decimal_child("w:startOverride", 1))
        instance.append(override)
        return int(instance.get(qn("w:numId")))

    @staticmethod
    def apply(paragraph: DocxParagraph, *, num_id: int, level: int) -> None:
        """Attach direct numbering; the run remains semantic text only."""
        if not 0 <= level < len(_LEVELS):
            raise ValueError(f"SectionFormat numbering level out of range: {level}")
        properties = paragraph._p.get_or_add_pPr()
        numbering_properties = properties.get_or_add_numPr()
        numbering_properties.get_or_add_ilvl().val = level
        numbering_properties.get_or_add_numId().val = num_id


__all__ = ["SectionFormatNumbering"]
