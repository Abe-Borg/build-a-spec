"""API-key scrubbing for trace artifacts.

Ported from Claude-Spec-Critic ``src/tracing/redaction.py``. The source
imports its credential patterns from the diagnostics module (not ported);
the patterns are inlined here instead — same shapes: full key prefixes
(``sk-ant-``, ``Bearer``, ``AKIA``) rather than any hex run, so false
positives stay rare. Document content is intentionally NOT scrubbed —
traces are local-only and the draft text is the point of the trace.
"""
from __future__ import annotations

import re
from typing import Any

_REDACTED = "<redacted>"

# Keys whose values are secrets regardless of shape.
_SECRET_KEY_PATTERN = re.compile(
    r"(?:api[_-]?key|authorization|password|secret|token|credential)",
    re.IGNORECASE,
)

# Credential-shaped values.
_SECRET_VALUE_PATTERNS = (
    re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{16,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)


def scrub_value(value: Any) -> Any:
    """Replace credential-shaped strings with ``"<redacted>"``."""
    if not isinstance(value, str):
        return value
    for pattern in _SECRET_VALUE_PATTERNS:
        if pattern.search(value):
            return _REDACTED
    return value


def scrub_data(data: Any, *, _depth: int = 0) -> Any:
    """Recursively scrub a JSON-ready structure (bounded at six levels)."""
    if _depth > 6:
        return repr(data)
    if isinstance(data, dict):
        out: dict = {}
        for key, value in data.items():
            if isinstance(key, str) and _SECRET_KEY_PATTERN.search(key):
                out[key] = _REDACTED
                continue
            out[key] = scrub_data(value, _depth=_depth + 1)
        return out
    if isinstance(data, list):
        return [scrub_data(v, _depth=_depth + 1) for v in data]
    if isinstance(data, tuple):
        return tuple(scrub_data(v, _depth=_depth + 1) for v in data)
    return scrub_value(data)
