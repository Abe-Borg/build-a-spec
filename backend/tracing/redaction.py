"""API-key scrubbing for trace artifacts.

Ported from Claude-Spec-Critic ``src/tracing/redaction.py``. The source
imports its credential patterns from the diagnostics module (not ported);
the patterns are inlined here instead — same shapes: full key prefixes
(``sk-ant-``, ``Bearer``, ``AKIA``) rather than any hex run, so false
positives stay rare. Document content is intentionally NOT scrubbed —
traces are local-only and the draft text is the point of the trace.
"""
from __future__ import annotations

from dataclasses import fields, is_dataclass
import hashlib
import math
import os
import re
from typing import Any

_REDACTED = "<redacted>"
_MAX_DEPTH = 6
_MAX_DEPTH_MARKER = "<max-depth>"
_UNSUPPORTED_PATH_MARKER = "<unsupported-path>"

# Keys whose values are secrets regardless of shape. ``token(?!s)`` is a
# Build-a-Spec deviation from the ported pattern: the bare ``token``
# alternation also matched every usage-count key (``input_tokens``,
# ``output_tokens``, ``thinking_tokens`` …), silently redacting billed
# usage out of every turn span — counts are not secrets, auth tokens
# (``auth_token``, ``Authorization`` …) still are.
_SECRET_KEY_PATTERN = re.compile(
    r"(?:api[_-]?key|authorization|password|secret|token(?!s)|credential)",
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
    if isinstance(value, float) and not math.isfinite(value):
        if math.isnan(value):
            return "<non-finite:NaN>"
        return (
            "<non-finite:+Infinity>"
            if value > 0
            else "<non-finite:-Infinity>"
        )
    if not isinstance(value, str):
        return value
    for pattern in _SECRET_VALUE_PATTERNS:
        if pattern.search(value):
            return _REDACTED
    return value


def redact_text(text: str) -> str:
    """Substring-level credential redaction for free text (prompt capture).

    Unlike :func:`scrub_value` — which replaces a whole matching string and
    would erase an entire prompt over one pasted key — this replaces only
    the credential-shaped spans and keeps the surrounding text readable.
    """
    if not isinstance(text, str):
        return text
    for pattern in _SECRET_VALUE_PATTERNS:
        text = pattern.sub(_REDACTED, text)
    return text


def _path_text(value: os.PathLike[Any]) -> str:
    """Return a JSON-safe path string without trusting ``__str__``/``repr``."""
    try:
        return os.fsdecode(os.fspath(value))
    except Exception:  # noqa: BLE001 - tracing must remain fail-open
        return _UNSUPPORTED_PATH_MARKER


def _redacted_mapping_key(
    key: str, source: dict[Any, Any], output: dict[Any, Any]
) -> str:
    """Return a deterministic placeholder that cannot overwrite another key."""
    digest = hashlib.sha256(
        key.encode("utf-8", errors="surrogatepass")
    ).hexdigest()[:12]
    base = f"<redacted-key:{digest}>"
    candidate = base
    suffix = 2
    # Also reserve literal keys later in ``source`` so iteration order cannot
    # let a user-supplied placeholder overwrite a redacted dynamic key.
    while candidate in source or candidate in output:
        candidate = f"<redacted-key:{digest}:{suffix}>"
        suffix += 1
    return candidate


def _normalized_mapping_key(
    key: str, source: dict[Any, Any], output: dict[Any, Any]
) -> str:
    """Avoid overwriting a literal key after normalizing a non-string key."""
    if key not in source and key not in output:
        return key
    digest = hashlib.sha256(
        key.encode("utf-8", errors="surrogatepass")
    ).hexdigest()[:12]
    candidate = f"<normalized-key:{digest}>"
    suffix = 2
    while candidate in source or candidate in output:
        candidate = f"<normalized-key:{digest}:{suffix}>"
        suffix += 1
    return candidate


def scrub_data(data: Any, *, _depth: int = 0) -> Any:
    """Recursively normalize and scrub a value graph (bounded at six levels)."""
    if _depth > _MAX_DEPTH:
        # Never call repr() here.  A deeply nested credential-bearing object
        # (or a custom object's repr) would bypass both key and value pattern
        # redaction and land verbatim in a support bundle.  Scalars can still
        # pass through the normal value scrubber; containers/objects are
        # represented by a deterministic, non-sensitive truncation marker.
        if isinstance(data, (str, int, float, bool, type(None))):
            return scrub_value(data)
        return _MAX_DEPTH_MARKER
    if is_dataclass(data) and not isinstance(data, type):
        # Normalize before JSON's ``default=`` hook so field names and nested
        # values still pass through the same credential-aware dict handling.
        return scrub_data(
            {field.name: getattr(data, field.name) for field in fields(data)},
            _depth=_depth,
        )
    if isinstance(data, os.PathLike):
        return scrub_value(_path_text(data))
    if isinstance(data, dict):
        out: dict = {}
        for key, value in data.items():
            safe_key = key
            policy_key = key
            key_is_credential = False
            if isinstance(key, float):
                safe_key = scrub_value(key)
                if isinstance(safe_key, str):
                    safe_key = _normalized_mapping_key(safe_key, data, out)
            elif isinstance(key, os.PathLike):
                policy_key = _path_text(key)
                safe_key = scrub_value(policy_key)
                if safe_key != _REDACTED:
                    safe_key = _normalized_mapping_key(safe_key, data, out)
            if isinstance(policy_key, str) and scrub_value(policy_key) == _REDACTED:
                key_is_credential = True
                # A credential can arrive as a dynamic map key rather than a
                # value. Keep distinct keys distinct without retaining their
                # text or collapsing records onto one placeholder.
                safe_key = _redacted_mapping_key(policy_key, data, out)
            if (
                isinstance(policy_key, str)
                and _SECRET_KEY_PATTERN.search(policy_key)
                and (
                    not key_is_credential
                    or _SECRET_KEY_PATTERN.match(policy_key) is not None
                )
            ):
                # A conventional field named ``api_key`` or ``password`` has
                # a secret value. A credential-shaped *dynamic map key* is a
                # different case: the key itself was replaced above, while
                # its unrelated value remains useful. If the key begins with
                # a conventional label (for example ``Authorization:``), the
                # associated value is still secret and is redacted too.
                out[safe_key] = _REDACTED
                continue
            out[safe_key] = scrub_data(value, _depth=_depth + 1)
        return out
    if isinstance(data, list):
        return [scrub_data(v, _depth=_depth + 1) for v in data]
    if isinstance(data, tuple):
        return tuple(scrub_data(v, _depth=_depth + 1) for v in data)
    if isinstance(data, (set, frozenset)):
        # Sets have no semantic order. A list avoids heterogeneous ``sorted``
        # failures while ensuring every member is recursively redacted first.
        return [scrub_data(v, _depth=_depth + 1) for v in data]
    return scrub_value(data)
