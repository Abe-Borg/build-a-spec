"""Run the canonical DOCX renderer with a narrow Windows URI fix.

The documents-skill renderer owns the CLI and rendering implementation.  This
entrypoint only fixes the malformed LibreOffice profile argument produced when
that renderer joins ``file://`` to a Windows drive path.  Point
``BUILD_A_SPEC_CANONICAL_DOCX_RENDERER`` at the canonical ``render_docx.py`` and
use this file as ``BUILD_A_SPEC_DOCX_RENDERER``.
"""
from __future__ import annotations

from functools import wraps
import importlib.util
import ntpath
import os
from pathlib import Path
import re
import sys
from types import ModuleType
from typing import Mapping
from urllib.parse import quote


CANONICAL_RENDERER_ENV = "BUILD_A_SPEC_CANONICAL_DOCX_RENDERER"
_USER_INSTALLATION_PREFIX = "-env:UserInstallation=file://"
_WINDOWS_DRIVE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_PATCH_MARKER = "__build_a_spec_windows_profile_uri_compat__"


class RendererCompatibilityError(RuntimeError):
    """The configured canonical renderer cannot be wrapped safely."""


def normalize_windows_profile_uri_argument(argument: str) -> str:
    """Return a valid file URI for the canonical renderer's Windows profile.

    Other command arguments, already-valid URIs, POSIX paths, and UNC URIs are
    returned byte-for-byte unchanged.
    """

    if not argument.startswith(_USER_INSTALLATION_PREFIX):
        return argument
    profile_path = argument[len(_USER_INSTALLATION_PREFIX) :]
    if not _WINDOWS_DRIVE_PATH.match(profile_path):
        return argument

    normalized_path = profile_path.replace("\\", "/")
    return (
        "-env:UserInstallation=file:///"
        + quote(normalized_path, safe="/:")
    )


def _is_soffice_command(command: list[str]) -> bool:
    if not command:
        return False
    executable = ntpath.basename(str(command[0])).casefold()
    return executable in {"soffice", "soffice.exe"}


def _normalize_soffice_command(command: list[str]) -> None:
    """Normalize only matching profile arguments on a soffice command."""

    if not _is_soffice_command(command):
        return
    for index, argument in enumerate(command):
        if isinstance(argument, str):
            command[index] = normalize_windows_profile_uri_argument(argument)


def install_windows_profile_uri_compat(renderer: ModuleType) -> None:
    """Patch the canonical renderer's single subprocess seam in place."""

    renderer_main = getattr(renderer, "main", None)
    run_command = getattr(renderer, "_run_cmd", None)
    if not callable(renderer_main) or not callable(run_command):
        raise RendererCompatibilityError(
            "The canonical renderer must expose callable main() and _run_cmd(); "
            "its interface may have changed."
        )
    if getattr(run_command, _PATCH_MARKER, False):
        return

    @wraps(run_command)
    def run_command_with_windows_profile_uri(
        command: list[str], env: dict, verbose: bool
    ):
        # Mutate the ephemeral command list so the canonical renderer's own
        # verbose/debug logging reports the command that actually ran.
        _normalize_soffice_command(command)
        return run_command(command, env=env, verbose=verbose)

    setattr(run_command_with_windows_profile_uri, _PATCH_MARKER, True)
    renderer._run_cmd = run_command_with_windows_profile_uri


def configured_canonical_renderer(
    environ: Mapping[str, str] | None = None,
) -> Path:
    values = os.environ if environ is None else environ
    raw_path = values.get(CANONICAL_RENDERER_ENV, "").strip()
    if not raw_path:
        raise RendererCompatibilityError(
            f"Set {CANONICAL_RENDERER_ENV} to the documents-skill render_docx.py."
        )

    renderer_path = Path(raw_path).expanduser().resolve()
    if not renderer_path.is_file():
        raise RendererCompatibilityError(
            f"Configured canonical DOCX renderer does not exist: {renderer_path}"
        )
    if renderer_path == Path(__file__).resolve():
        raise RendererCompatibilityError(
            f"{CANONICAL_RENDERER_ENV} must not point back to this wrapper."
        )
    return renderer_path


def _load_canonical_renderer(renderer_path: Path) -> ModuleType:
    module_name = "_build_a_spec_canonical_docx_renderer"
    spec = importlib.util.spec_from_file_location(module_name, renderer_path)
    if spec is None or spec.loader is None:
        raise RendererCompatibilityError(
            f"Could not load canonical DOCX renderer: {renderer_path}"
        )

    renderer = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = renderer
    renderer_dir = str(renderer_path.parent)
    sys.path.insert(0, renderer_dir)
    try:
        spec.loader.exec_module(renderer)
    except Exception:
        if sys.modules.get(module_name) is renderer:
            del sys.modules[module_name]
        raise
    finally:
        if sys.path and sys.path[0] == renderer_dir:
            sys.path.pop(0)
        else:  # Defensive if the canonical module changed sys.path itself.
            try:
                sys.path.remove(renderer_dir)
            except ValueError:
                pass
    return renderer


def main() -> None:
    """Delegate the unchanged command line to the configured renderer."""

    try:
        renderer_path = configured_canonical_renderer()
        renderer = _load_canonical_renderer(renderer_path)
        install_windows_profile_uri_compat(renderer)
    except RendererCompatibilityError as exc:
        raise SystemExit(f"DOCX renderer compatibility error: {exc}") from exc

    renderer.main()


if __name__ == "__main__":
    main()
