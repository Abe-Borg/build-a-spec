from __future__ import annotations

import sys
from types import ModuleType

import pytest

from tools import render_docx_windows_compat as compat


@pytest.mark.parametrize(
    ("argument", "expected"),
    [
        (
            r"-env:UserInstallation=file://C:\Users\A B\profile#1",
            "-env:UserInstallation=file:///C:/Users/A%20B/profile%231",
        ),
        (
            "-env:UserInstallation=file://D:/render/profile",
            "-env:UserInstallation=file:///D:/render/profile",
        ),
    ],
)
def test_normalizes_canonical_windows_profile_argument(argument, expected):
    assert compat.normalize_windows_profile_uri_argument(argument) == expected


@pytest.mark.parametrize(
    "argument",
    [
        "--headless",
        "-env:UserInstallation=file:///C:/already-valid",
        "-env:UserInstallation=file:///tmp/profile",
        r"-env:UserInstallation=file://server\share\profile",
        r"-env:Other=file://C:\render\profile",
    ],
)
def test_leaves_nonmatching_arguments_unchanged(argument):
    assert compat.normalize_windows_profile_uri_argument(argument) == argument


def test_patch_changes_only_soffice_profile_argument():
    calls = []
    command_ids = []
    sentinel = object()

    def original(command, env, verbose):
        command_ids.append(id(command))
        calls.append((list(command), env, verbose))
        return sentinel

    renderer = ModuleType("fake_renderer")
    renderer.main = lambda: None
    renderer._run_cmd = original
    compat.install_windows_profile_uri_compat(renderer)

    environment = {"HOME": r"C:\render profile"}
    soffice_command = [
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"-env:UserInstallation=file://C:\render profile",
        "--outdir",
        r"C:\output folder",
    ]
    assert renderer._run_cmd(soffice_command, environment, True) is sentinel
    assert command_ids[-1] == id(soffice_command)
    assert soffice_command[1] == (
        "-env:UserInstallation=file:///C:/render%20profile"
    )
    assert calls[-1] == (
        [
            r"C:\Program Files\LibreOffice\program\soffice.exe",
            "-env:UserInstallation=file:///C:/render%20profile",
            "--outdir",
            r"C:\output folder",
        ],
        environment,
        True,
    )

    non_soffice_command = [
        "helper.exe",
        r"-env:UserInstallation=file://C:\render profile",
    ]
    renderer._run_cmd(non_soffice_command, environment, False)
    assert calls[-1][0] == non_soffice_command


def test_patch_is_idempotent():
    renderer = ModuleType("fake_renderer")
    renderer.main = lambda: None
    renderer._run_cmd = lambda command, env, verbose: None

    compat.install_windows_profile_uri_compat(renderer)
    first_patch = renderer._run_cmd
    compat.install_windows_profile_uri_compat(renderer)

    assert renderer._run_cmd is first_patch


def test_patch_rejects_changed_canonical_interface():
    renderer = ModuleType("fake_renderer")
    renderer.main = lambda: None

    with pytest.raises(compat.RendererCompatibilityError, match="_run_cmd"):
        compat.install_windows_profile_uri_compat(renderer)


def test_main_preserves_canonical_cli(monkeypatch, tmp_path):
    canonical_path = tmp_path / "render_docx.py"
    canonical_path.write_text("# loader is replaced in this test\n", encoding="utf-8")
    observed_argv = []
    renderer = ModuleType("fake_renderer")
    renderer._run_cmd = lambda command, env, verbose: None
    renderer.main = lambda: observed_argv.extend(sys.argv)

    monkeypatch.setenv(compat.CANONICAL_RENDERER_ENV, str(canonical_path))
    monkeypatch.setattr(compat, "_load_canonical_renderer", lambda path: renderer)
    expected_argv = [
        str(compat.__file__),
        "input.docx",
        "--output_dir",
        "rendered",
        "--dpi",
        "144",
        "--emit_pdf",
        "--verbose",
    ]
    monkeypatch.setattr(sys, "argv", expected_argv)

    compat.main()

    assert observed_argv == expected_argv
