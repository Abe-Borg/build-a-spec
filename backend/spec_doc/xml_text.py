"""XML 1.0-safe, information-preserving text for generated artifacts.

XML 1.0 cannot represent a handful of Unicode code points (including most
C0 controls and lone UTF-16 surrogates).  Rather than silently deleting those
characters from an audit artifact, render them as visible Python-style escape
sequences so a reviewer can still see exactly what the source contained.
"""
from __future__ import annotations

import copy
import re
import unicodedata
from dataclasses import fields, is_dataclass
from typing import Any, TypeVar


T = TypeVar("T")

_VISIBLE_ESCAPE_RE = re.compile(
    r"(?P<long>\\U[0-9A-Fa-f]{8})|(?P<short>\\u[0-9A-Fa-f]{4})"
)


def _is_xml_10_character(codepoint: int) -> bool:
    return (
        codepoint in (0x09, 0x0A, 0x0D)
        or 0x20 <= codepoint <= 0xD7FF
        or 0xE000 <= codepoint <= 0xFFFD
        or 0x10000 <= codepoint <= 0x10FFFF
    )


def _visible_escape(codepoint: int) -> str:
    if codepoint <= 0xFFFF:
        return f"\\u{codepoint:04X}"
    return f"\\U{codepoint:08X}"


def xml_safe_text(text: str) -> str:
    """Return *text* with XML 1.0-illegal code points visibly escaped."""
    if not isinstance(text, str):
        raise TypeError("xml_safe_text requires a string")

    safe: list[str] = []
    for char in text:
        codepoint = ord(char)
        if _is_xml_10_character(codepoint):
            safe.append(char)
        else:
            safe.append(_visible_escape(codepoint))
    return "".join(safe)


def filename_safe_text(text: str) -> str:
    """Escape raw control/XML-illegal characters before filename scrubbing."""
    if not isinstance(text, str):
        raise TypeError("filename_safe_text requires a string")
    return "".join(
        _visible_escape(ord(char))
        if not _is_xml_10_character(ord(char))
        or unicodedata.category(char) == "Cc"
        else char
        for char in text
    )


def _case_around_visible_escapes(text: str, transform: Any) -> str:
    """Apply a display case transform without changing escape tokens.

    Post-hoc repair is ambiguous when a short escape is immediately followed
    by a hexadecimal-looking character (``\\u0001BAD``).  Treating each
    visible escape as an opaque boundary preserves its canonical prefix and
    digits regardless of the surrounding text.
    """
    safe = xml_safe_text(text)
    output: list[str] = []
    position = 0
    for match in _VISIBLE_ESCAPE_RE.finditer(safe):
        output.append(transform(safe[position : match.start()]))
        token = match.group(0)
        if match.group("short") is not None:
            output.append(f"\\u{token[2:].upper()}")
        else:
            output.append(f"\\U{token[2:].upper()}")
        position = match.end()
    output.append(transform(safe[position:]))
    return "".join(output)


def xml_safe_upper(text: str) -> str:
    """Uppercase display text while retaining canonical visible escapes."""
    return _case_around_visible_escapes(text, str.upper)


def xml_safe_title(text: str) -> str:
    """Title-case display text while retaining canonical visible escapes."""
    return _case_around_visible_escapes(text, str.title)


def xml_safe_clone(value: T) -> T:
    """Clone a supported value graph while making every string XML-safe.

    DOCX export inputs are dataclass document/diff trees plus JSON-like audit
    records.  Cloning at the export boundary both covers every python-docx
    writer and guarantees that exporting never normalizes the caller's live
    document or persisted audit record in place.
    """
    if isinstance(value, str):
        return xml_safe_text(value)  # type: ignore[return-value]
    if isinstance(value, dict):
        # Preserve keys exactly: sanitizing an illegal-character key could
        # collide with a distinct, already-literal ``\\uXXXX`` key. Dynamic
        # keys are made safe when rendered; known schema keys are not output.
        return {
            copy.deepcopy(key): xml_safe_clone(item)
            for key, item in value.items()
        }  # type: ignore[return-value]
    if isinstance(value, list):
        return [xml_safe_clone(item) for item in value]  # type: ignore[return-value]
    if isinstance(value, tuple):
        items = [xml_safe_clone(item) for item in value]
        if hasattr(value, "_fields"):
            return type(value)(*items)  # type: ignore[return-value]
        return tuple(items)  # type: ignore[return-value]
    if isinstance(value, set):
        return type(value)(
            xml_safe_clone(item) for item in value
        )  # type: ignore[return-value]
    if is_dataclass(value) and not isinstance(value, type):
        clone = copy.copy(value)
        for field in fields(value):
            object.__setattr__(
                clone,
                field.name,
                xml_safe_clone(getattr(value, field.name)),
            )
        return clone
    return copy.deepcopy(value)


__all__ = [
    "filename_safe_text",
    "xml_safe_clone",
    "xml_safe_text",
    "xml_safe_title",
    "xml_safe_upper",
]
