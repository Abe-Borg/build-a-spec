"""Advisory section/module compatibility checks for Final QC.

The specification is always the authority: this module never changes the
selected module or document.  A closed, curated module can still provide a
useful warning when the active section number is outside its catalog.  Open
catalog modules deliberately opt out.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from ..spec_doc.model import SpecSection
from ..spec_modules import SpecModule


_SECTION_SEPARATORS_RE = re.compile(r"[\s.\-\u2010-\u2015]+")


def normalize_section_number(value: Any) -> str:
    """Return a canonical MasterFormat number, or ``""`` when unknown.

    Comparison is intentionally number-only.  Unicode normalization and
    common visual separators are ignored, but titles or other prose are not
    stripped from a malformed field and therefore cannot create a match.
    Six digits normalize to ``NN NN NN`` and an optional final two digits to
    ``NN NN NN.NN``.
    """
    if not isinstance(value, str):
        return ""
    cleaned = unicodedata.normalize("NFKC", value).strip()
    if not cleaned:
        return ""
    if re.search(r"[^0-9\s.\-\u2010-\u2015]", cleaned):
        return ""
    digits = _SECTION_SEPARATORS_RE.sub("", cleaned)
    if not digits.isdigit() or len(digits) not in (6, 8):
        return ""
    base = f"{digits[0:2]} {digits[2:4]} {digits[4:6]}"
    return f"{base}.{digits[6:8]}" if len(digits) == 8 else base


def module_section_compatibility(
    section: SpecSection, module: SpecModule
) -> dict[str, Any]:
    """Describe exact normalized section-number compatibility.

    The returned object is API-ready and has one stable shape for every
    outcome so the import warning, QC status, and QC start gate all consume
    the same decision.
    """
    raw_number = str(getattr(section, "number", "") or "").strip()
    section_title = str(getattr(section, "title", "") or "").strip()
    allowed_sections = [
        {"number": item.number, "title": item.title}
        for item in module.section_catalog
    ]
    base: dict[str, Any] = {
        "status": "not_applicable",
        "section_number": raw_number,
        "section_title": section_title,
        "module_id": module.module_id,
        "module_display_name": module.display_name,
        "allowed_sections": allowed_sections,
        "message": "",
    }

    # Only a curated module with a closed, non-empty catalog participates.
    if module.open_catalog or not module.section_catalog:
        base["message"] = (
            "The active module uses an open section catalog; no section-number "
            "compatibility check applies."
        )
        return base

    normalized = normalize_section_number(raw_number)
    if not normalized:
        base["status"] = "unknown"
        base["message"] = (
            f"The specification section number is not set or recognizable, so "
            f"it cannot be compared with the {module.display_name} catalog."
        )
        return base

    # Present the canonical form to every consumer once it can be parsed.
    base["section_number"] = normalized
    allowed_numbers = {
        normalize_section_number(item.number) for item in module.section_catalog
    }
    if normalized in allowed_numbers:
        base["status"] = "match"
        base["message"] = (
            f"Section {normalized} is included in the {module.display_name} "
            "catalog."
        )
        return base

    allowed_display = ", ".join(
        f"{item.number} — {item.title}" for item in module.section_catalog
    )
    base["status"] = "mismatch"
    base["message"] = (
        f"Section {normalized}"
        f"{f' — {section_title}' if section_title else ''} is outside the "
        f"{module.display_name} catalog ({allowed_display}). Final QC may still "
        "run after you explicitly acknowledge this scope mismatch."
    )
    return base


__all__ = ["module_section_compatibility", "normalize_section_number"]
