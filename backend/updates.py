"""Self-update check + installer download for the Windows desktop build.

Ported ≈verbatim from Claude-Spec-Critic ``src/core/updates.py`` — the
same free, serverless pipeline: each GitHub Release carries a tiny
``latest.json`` manifest and the installer (``BuildASpecSetup.exe``); the
installed app fetches the manifest (throttled to once a day, or on
demand), compares versions, and offers the download — which is
**SHA-256-verified against the manifest before it is ever launched**. The
app ships unsigned (SmartScreen warns on first run), so the manifest hash
over an https-only channel is the integrity gate that matters.

Design constraints preserved from the source:

- **Pure and self-contained**: standard library only; the caller supplies
  the current version string.
- **Non-fatal**: :func:`check_for_update` never raises.
- **Injectable seams**: the fetcher, the download opener, and the clock
  are parameters, so tests drive the whole flow hermetically.

Env overrides (the ``BUILD_A_SPEC_*`` convention):

    BUILD_A_SPEC_UPDATE_URL            — override the manifest URL.
    BUILD_A_SPEC_DISABLE_UPDATE_CHECK  — truthy turns the check off.
    BUILD_A_SPEC_UPDATE_STATE_PATH     — override the throttle-state file.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

GITHUB_OWNER = "Abe-Borg"
GITHUB_REPO = "build-a-spec"

# GitHub serves the newest published, non-prerelease release's assets from
# the stable ``releases/latest/download/<asset>`` path.
_DEFAULT_MANIFEST_URL = (
    f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}"
    "/releases/latest/download/latest.json"
)
_RELEASES_PAGE_URL = (
    f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
)

ENV_UPDATE_URL = "BUILD_A_SPEC_UPDATE_URL"
ENV_DISABLE = "BUILD_A_SPEC_DISABLE_UPDATE_CHECK"
ENV_STATE_PATH = "BUILD_A_SPEC_UPDATE_STATE_PATH"

_DISABLE_TOKENS = frozenset({"0", "false", "no", "off"})

DEFAULT_MANIFEST_TIMEOUT = 8.0
DEFAULT_DOWNLOAD_TIMEOUT = 60.0
MAX_MANIFEST_BYTES = 64 * 1024
_USER_AGENT = "BuildASpec-Updater"

STATUS_UP_TO_DATE = "UP_TO_DATE"
STATUS_UPDATE_AVAILABLE = "UPDATE_AVAILABLE"
STATUS_DISABLED = "DISABLED"
STATUS_ERROR = "ERROR"

STATE_FILENAME = "update_check.json"
DEFAULT_MIN_INTERVAL_DAYS = 1

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:rc(\d+))?$")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_INSTALLER_FALLBACK_NAME = "BuildASpecSetup.exe"


class UpdateError(Exception):
    """A recoverable update problem (bad manifest, checksum mismatch, ...)."""


@dataclass(frozen=True)
class UpdateInfo:
    """A validated update descriptor parsed from ``latest.json``."""

    version: str
    url: str
    sha256: str
    notes: str = ""
    published_at: str = ""


@dataclass(frozen=True)
class UpdateCheckResult:
    """Outcome of a single update check. Never raised, always returned."""

    status: str
    current: str
    info: UpdateInfo | None = None
    error: str | None = None

    @property
    def update_available(self) -> bool:
        return self.status == STATUS_UPDATE_AVAILABLE and self.info is not None


# --------------------------------------------------------------------------
# Version comparison
# --------------------------------------------------------------------------


def parse_version(value: str) -> tuple[int, int, int, tuple[int, int]]:
    """Parse ``MAJOR.MINOR.PATCH[rcN]`` into a sortable key.

    A final release sorts after every rc of the same x.y.z (rc rank
    ``(0, N)``; final ``(1, 0)``). Raises ``ValueError`` outside the
    grammar so a garbage manifest version can never masquerade as newer.
    """
    match = _VERSION_RE.match(value.strip())
    if not match:
        raise ValueError(f"unrecognized version string: {value!r}")
    major, minor, patch = (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
    )
    rc = match.group(4)
    pre = (0, int(rc)) if rc is not None else (1, 0)
    return (major, minor, patch, pre)


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


# --------------------------------------------------------------------------
# Manifest URL / disable policy
# --------------------------------------------------------------------------


def manifest_url() -> str:
    override = os.environ.get(ENV_UPDATE_URL)
    if override and override.strip():
        return override.strip()
    return _DEFAULT_MANIFEST_URL


def releases_page_url() -> str:
    return _RELEASES_PAGE_URL


def installer_platform_supported() -> bool:
    """The release asset is a Windows ``.exe`` — gate offers on platform."""
    return sys.platform.startswith("win")


def update_check_disabled() -> bool:
    raw = os.environ.get(ENV_DISABLE)
    if raw is None:
        return False
    val = raw.strip().lower()
    return val != "" and val not in _DISABLE_TOKENS


# --------------------------------------------------------------------------
# Manifest parsing / fetching
# --------------------------------------------------------------------------


def parse_manifest(payload: dict) -> UpdateInfo:
    """Validate a decoded ``latest.json`` payload.

    Security invariants: version must match the grammar; ``url`` must be
    https (never fetch an installer over plaintext); ``sha256`` must be 64
    hex chars (the gate that authenticates the binary before launch).
    """
    if not isinstance(payload, dict):
        raise UpdateError("manifest is not a JSON object")

    version = str(payload.get("version", "")).strip()
    if not version:
        raise UpdateError("manifest is missing 'version'")
    try:
        parse_version(version)
    except ValueError as exc:
        raise UpdateError(f"manifest version is malformed: {exc}") from exc

    url = str(payload.get("url", "")).strip()
    if not url:
        raise UpdateError("manifest is missing 'url'")
    if not url.lower().startswith("https://"):
        raise UpdateError("manifest 'url' must be https")

    sha256 = str(payload.get("sha256", "")).strip().lower()
    if not _SHA256_RE.match(sha256):
        raise UpdateError("manifest 'sha256' must be 64 hex characters")

    return UpdateInfo(
        version=version,
        url=url,
        sha256=sha256,
        notes=str(payload.get("notes", "") or ""),
        published_at=str(payload.get("published_at", "") or ""),
    )


def _require_https_final_url(resp, what: str) -> None:
    """Raise unless ``resp``'s post-redirect URL is still https."""
    final = getattr(resp, "url", None)
    if final is None:
        getter = getattr(resp, "geturl", None)
        if callable(getter):
            try:
                final = getter()
            except Exception:
                final = None
    if final is not None and not str(final).lower().startswith("https://"):
        raise UpdateError(
            f"{what} request was redirected to a non-https URL; refusing "
            "to trust it"
        )


def fetch_manifest(url: str, *, timeout: float = DEFAULT_MANIFEST_TIMEOUT) -> dict:
    """GET ``url`` as JSON with a size cap, https-only end to end.

    The manifest is the root of trust — the installer's authenticating
    sha256 comes FROM it — so both the first hop and any redirect target
    must be https.
    """
    if not url.lower().startswith("https://"):
        raise UpdateError(
            "refusing to fetch the update manifest over a non-https URL"
        )
    request = urllib.request.Request(
        url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310
        _require_https_final_url(resp, "update manifest")
        raw = resp.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise UpdateError("update manifest is unexpectedly large; refusing to parse")
    return json.loads(raw.decode("utf-8"))


def check_for_update(
    current: str,
    *,
    url: str | None = None,
    fetcher: Callable[..., dict] | None = None,
    timeout: float = DEFAULT_MANIFEST_TIMEOUT,
) -> UpdateCheckResult:
    """Fetch the manifest and compare to ``current``. Never raises."""
    if update_check_disabled():
        return UpdateCheckResult(status=STATUS_DISABLED, current=current)

    fetch = fetcher or fetch_manifest
    target = url or manifest_url()
    try:
        payload = fetch(target, timeout=timeout)
        info = parse_manifest(payload)
        newer = is_newer(info.version, current)
    except Exception as exc:  # noqa: BLE001 — best-effort, never fatal
        return UpdateCheckResult(
            status=STATUS_ERROR, current=current, error=str(exc)
        )

    if newer:
        return UpdateCheckResult(
            status=STATUS_UPDATE_AVAILABLE, current=current, info=info
        )
    return UpdateCheckResult(status=STATUS_UP_TO_DATE, current=current, info=info)


# --------------------------------------------------------------------------
# Download + integrity + launch
# --------------------------------------------------------------------------


def verify_sha256(path: str | Path, expected: str, *, chunk: int = 1 << 20) -> str:
    """Stream-hash ``path``; raise :class:`UpdateError` on mismatch."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            digest.update(block)
    actual = digest.hexdigest()
    if actual.lower() != expected.strip().lower():
        raise UpdateError(
            f"downloaded installer failed integrity check "
            f"(expected {expected}, got {actual})"
        )
    return actual


def _installer_filename(url: str) -> str:
    """Safe basename-only ``.exe`` filename derived from the URL."""
    tail = url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    name = os.path.basename(tail)
    if not name or not name.lower().endswith(".exe"):
        return _INSTALLER_FALLBACK_NAME
    return name


def _open_url(url: str, *, timeout: float):
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    resp = urllib.request.urlopen(request, timeout=timeout)  # noqa: S310
    try:
        _require_https_final_url(resp, "installer download")
    except BaseException:
        try:
            resp.close()
        except Exception:
            pass
        raise
    return resp


def download_installer(
    info: UpdateInfo,
    dest_dir: str | Path,
    *,
    opener: Callable[..., object] | None = None,
    progress: Callable[[int, int], None] | None = None,
    timeout: float = DEFAULT_DOWNLOAD_TIMEOUT,
    chunk: int = 1 << 16,
) -> Path:
    """Download + SHA-256-verify the installer; atomic promote on success.

    Streams to a ``.part`` temp file, promoted with ``os.replace`` only
    after the hash matches — the final path never holds a partial or
    failed file, and any failure removes the temp file.
    """
    if not info.url.lower().startswith("https://"):
        raise UpdateError("refusing to download an installer over a non-https URL")

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / _installer_filename(info.url)
    part = dest.with_name(dest.name + ".part")

    open_fn = opener or _open_url
    digest = hashlib.sha256()
    downloaded = 0
    try:
        with open_fn(info.url, timeout=timeout) as resp:
            total = _content_length(resp)
            with open(part, "wb") as fh:
                while True:
                    buf = resp.read(chunk)
                    if not buf:
                        break
                    fh.write(buf)
                    digest.update(buf)
                    downloaded += len(buf)
                    if progress is not None:
                        progress(downloaded, total)
        actual = digest.hexdigest()
        if actual.lower() != info.sha256.lower():
            raise UpdateError(
                f"downloaded installer failed integrity check "
                f"(expected {info.sha256}, got {actual})"
            )
        os.replace(part, dest)
    except BaseException:
        try:
            part.unlink()
        except OSError:
            pass
        raise
    return dest


def _content_length(resp) -> int:
    getter = getattr(resp, "getheader", None)
    raw = None
    if callable(getter):
        raw = getter("Content-Length")
    if raw is None:
        headers = getattr(resp, "headers", None)
        if headers is not None and hasattr(headers, "get"):
            raw = headers.get("Content-Length")
    try:
        return int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        return 0


def spawn_installer(path: str | Path) -> None:
    """Launch the verified installer detached; the caller then exits."""
    path = Path(path)
    if sys.platform.startswith("win"):
        os.startfile(str(path))  # type: ignore[attr-defined]  # noqa: S606
    else:  # pragma: no cover — the shipped product is Windows-only
        subprocess.Popen([str(path)])  # noqa: S603


# --------------------------------------------------------------------------
# Throttle state (once-a-day auto-check + "skip this version")
# --------------------------------------------------------------------------


def _state_dir() -> Path:
    """The app config dir (platformdirs) — same home as the key file."""
    from .app_paths import app_config_dir

    return app_config_dir()


def default_state_path() -> Path:
    override = os.environ.get(ENV_STATE_PATH)
    if override and override.strip():
        return Path(os.path.expanduser(os.path.expandvars(override.strip())))
    return _state_dir() / STATE_FILENAME


def default_download_dir() -> Path:
    return _state_dir() / "updates"


def load_state(path: str | Path) -> dict:
    try:
        raw = Path(path).read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(path: str | Path, state: dict) -> None:
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass


def should_auto_check(
    state: dict,
    *,
    now: datetime,
    min_interval_days: int = DEFAULT_MIN_INTERVAL_DAYS,
) -> bool:
    last = state.get("last_check")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(str(last))
    except ValueError:
        return True
    return (now - last_dt) >= timedelta(days=min_interval_days)


def record_check(state: dict, *, now: datetime) -> dict:
    state["last_check"] = now.isoformat()
    return state


def version_is_skipped(state: dict, version: str) -> bool:
    return state.get("skipped_version") == version


def mark_skipped(state: dict, version: str) -> dict:
    state["skipped_version"] = version
    return state
