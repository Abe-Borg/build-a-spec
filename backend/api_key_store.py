"""Loading and storing the Anthropic API key.

Ported from Spec Critic's ``src/core/api_key_store.py`` with one addition:
:func:`save_api_key`, so the web UI can store a key the same way Spec
Critic's GUI field does. Resolution order:

1. ``ANTHROPIC_API_KEY`` environment variable (never persisted, wins always).
2. OS keyring, when the optional ``keyring`` package is present and working.
3. Plaintext fallback file in the platform config dir / next to the exe.

Every failure path is swallowed to an empty string so the caller decides how
to surface a missing key to the user.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

from .app_paths import api_key_paths, app_config_dir, API_KEY_FILENAME

# Keyring is optional. On headless CI / minimal Linux installs the import or
# the first call can fail; we swallow every failure so the file fallback
# always works.
try:  # pragma: no cover - import path depends on optional dependency
    import keyring as _keyring  # type: ignore

    _KEYRING_AVAILABLE = True
except Exception:  # pragma: no cover - keyring not installed
    _keyring = None
    _KEYRING_AVAILABLE = False

_KEYRING_SERVICE = "BuildASpec"
_KEYRING_USERNAME = "anthropic_api_key"


def _keyring_get() -> str:
    if not _KEYRING_AVAILABLE or _keyring is None:
        return ""
    try:
        value = _keyring.get_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
    except Exception:
        return ""
    return (value or "").strip()


def _keyring_set(value: str) -> bool:
    if not _KEYRING_AVAILABLE or _keyring is None:
        return False
    try:
        _keyring.set_password(_KEYRING_SERVICE, _KEYRING_USERNAME, value)
        return True
    except Exception:
        return False


def _restrict_permissions(path: Path) -> None:
    """Best-effort tighten of file permissions to owner-only (0600).

    POSIX-only; on Windows ``os.chmod`` only toggles the read-only bit so we
    skip it there. Failures are swallowed — better to load the key on a
    quirky filesystem than fail the run over a permission tweak.
    """
    if os.name != "posix":
        return
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def load_api_key() -> str:
    """Resolve the Anthropic API key: env var, then keyring, then file."""
    from_env = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if from_env:
        return from_env
    from_keyring = _keyring_get()
    if from_keyring:
        return from_keyring
    for path in api_key_paths():
        if not path.exists():
            continue
        try:
            value = path.read_text(encoding="utf-8").strip()
        except Exception:
            continue
        if value:
            _restrict_permissions(path)
            return value
    return ""


def save_api_key(value: str) -> str:
    """Persist the key; returns where it landed (``"keyring"`` / ``"file"``).

    Keyring first (credential-manager storage beats plaintext); the config-dir
    file is the fallback. Raises ``OSError`` only when the file write itself
    fails after the keyring was unavailable.
    """
    value = (value or "").strip()
    if not value:
        raise ValueError("API key is empty")
    if _keyring_set(value):
        return "keyring"
    path = app_config_dir() / API_KEY_FILENAME
    path.write_text(value, encoding="utf-8")
    _restrict_permissions(path)
    return "file"
