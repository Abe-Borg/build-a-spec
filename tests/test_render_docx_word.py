from __future__ import annotations

import base64
import json
from pathlib import Path
import subprocess
import sys
from types import ModuleType

import pytest

from tools import render_docx_word as renderer


def test_powershell_bridge_encodes_hidden_sta_safety_contract(monkeypatch):
    # The command-encoding contract (flags + the encoded safety directives)
    # is platform-independent; only the executable *lookup* is Windows-only
    # (real powershell.exe lives under %SystemRoot%, absent on the Linux CI
    # runner). Stub just that lookup so the security contract is verified on
    # every platform instead of erroring where powershell.exe doesn't exist.
    fake_powershell = Path(
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    )
    monkeypatch.setattr(renderer, "_powershell_executable", lambda: fake_powershell)

    command = renderer._powershell_command()
    assert command[0] == str(fake_powershell)
    assert command[1:-1] == [
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-STA",
        "-WindowStyle",
        "Hidden",
        "-EncodedCommand",
    ]
    script = base64.b64decode(command[-1]).decode("utf-16-le")
    for requirement in (
        "$word.Visible = $false",
        "$word.DisplayAlerts = 0",
        "$word.AutomationSecurity = 3",
        "$document.Close([ref]$closeSaveChanges)",
        "$word.Quit([ref]$quitSaveChanges)",
        "[ref]$openReadOnly",
        "[ref]$addToRecentFiles",
        "GetWindowThreadProcessId",
        "$createdWordByCom = $true",
        "if ($createdWordByCom)",
        "$beforeWordIdentities.ContainsKey",
        "$documentWindowProcessId -ne $wordProcessId",
        "$env:BUILD_A_SPEC_WORD_TOKEN",
        "$actualExecutable",
        "$actualStart -ne $ownedStart",
        "Stop-OwnedWordProcess -WaitSeconds 10",
    ):
        assert requirement in script


def test_word_environment_passes_paths_without_command_interpolation(tmp_path):
    input_path = tmp_path / "input & document.docx"
    pdf_path = tmp_path / "output 'quoted'.pdf"
    word_path = tmp_path / "Office 16" / "WINWORD.EXE"
    ownership_path = tmp_path / "owner.txt"

    environment = renderer._word_environment(
        input_path,
        pdf_path,
        word_path,
        ownership_path,
    )

    assert environment["BUILD_A_SPEC_WORD_INPUT"] == str(input_path)
    assert environment["BUILD_A_SPEC_WORD_PDF"] == str(pdf_path)
    assert environment["BUILD_A_SPEC_WORD_EXECUTABLE"] == str(word_path)
    assert environment["BUILD_A_SPEC_WORD_OWNERSHIP"] == str(ownership_path)
    assert len(environment["BUILD_A_SPEC_WORD_TOKEN"]) == 32


def test_timeout_runs_owned_process_cleanup(monkeypatch, tmp_path):
    input_path = tmp_path / "input.docx"
    pdf_path = tmp_path / "output.pdf"
    word_path = tmp_path / "WINWORD.EXE"
    cleanup_calls = []

    def time_out(environment, *, timeout_seconds):
        raise subprocess.TimeoutExpired("powershell", timeout_seconds)

    monkeypatch.setattr(renderer, "_run_powershell", time_out)
    monkeypatch.setattr(
        renderer,
        "_cleanup_owned_word",
        lambda environment: cleanup_calls.append(environment) or "",
    )

    with pytest.raises(renderer.WordRendererError, match="timed out"):
        renderer._convert_with_word(
            input_path,
            pdf_path,
            word_executable=word_path,
            timeout_seconds=17,
            verbose=False,
        )

    assert len(cleanup_calls) == 1
    assert cleanup_calls[0]["BUILD_A_SPEC_WORD_OWNERSHIP"].endswith(
        "word-ownership.txt"
    )


def test_nonzero_word_exit_runs_owned_process_cleanup(monkeypatch, tmp_path):
    cleanup_calls = []
    monkeypatch.setattr(
        renderer,
        "_run_powershell",
        lambda environment, timeout_seconds: subprocess.CompletedProcess(
            args=["powershell"],
            returncode=1,
            stdout="",
            stderr="conversion failed",
        ),
    )
    monkeypatch.setattr(
        renderer,
        "_cleanup_owned_word",
        lambda environment: cleanup_calls.append(environment) or "",
    )

    with pytest.raises(renderer.WordRendererError, match="conversion failed"):
        renderer._convert_with_word(
            tmp_path / "input.docx",
            tmp_path / "output.pdf",
            word_executable=tmp_path / "WINWORD.EXE",
            timeout_seconds=17,
            verbose=False,
        )

    assert len(cleanup_calls) == 1


@pytest.mark.parametrize(
    ("record", "identity", "expected"),
    [
        (
            renderer._OwnedWordRecord("wrong", 41, 9001, r"C:\Office\WINWORD.EXE"),
            renderer._ProcessIdentity(41, 9001, r"C:\Office\WINWORD.EXE"),
            "token mismatch",
        ),
        (
            renderer._OwnedWordRecord("token", 41, 9001, r"C:\Office\WINWORD.EXE"),
            renderer._ProcessIdentity(42, 9001, r"C:\Office\WINWORD.EXE"),
            "PID mismatch",
        ),
        (
            renderer._OwnedWordRecord("token", 41, 9001, r"C:\Office\WINWORD.EXE"),
            renderer._ProcessIdentity(41, 9002, r"C:\Office\WINWORD.EXE"),
            "process creation-time mismatch",
        ),
        (
            renderer._OwnedWordRecord("token", 41, 9001, r"C:\Other\WINWORD.EXE"),
            renderer._ProcessIdentity(41, 9001, r"C:\Office\WINWORD.EXE"),
            "recorded executable mismatch",
        ),
        (
            renderer._OwnedWordRecord("token", 41, 9001, r"C:\Office\WINWORD.EXE"),
            renderer._ProcessIdentity(41, 9001, r"C:\Other\WINWORD.EXE"),
            "running executable mismatch",
        ),
    ],
)
def test_ownership_rejects_adversarial_identity(record, identity, expected):
    assert renderer._ownership_rejection(
        record,
        identity,
        expected_token="token",
        expected_executable=r"C:\Office\WINWORD.EXE",
    ) == expected


class _FakeProcessHandle:
    def __init__(self, identity):
        self.identity_value = identity
        self.terminated = False
        self.closed = False

    def identity(self):
        return self.identity_value

    def terminate_and_wait(self, timeout_seconds):
        assert timeout_seconds == 10
        self.terminated = True

    def close(self):
        self.closed = True


def _write_ownership_record(path, **overrides):
    record = {
        "token": "token",
        "pid": 41,
        "creation_time": 9001,
        "executable": r"C:\Office\WINWORD.EXE",
    }
    record.update(overrides)
    path.write_text(
        json.dumps(record),
        encoding="utf-8",
    )


def test_cleanup_terminates_only_exact_owned_identity(tmp_path):
    ownership_path = tmp_path / "owner.json"
    _write_ownership_record(ownership_path)
    handle = _FakeProcessHandle(
        renderer._ProcessIdentity(41, 9001, r"c:\office\winword.exe")
    )
    environment = {
        "BUILD_A_SPEC_WORD_OWNERSHIP": str(ownership_path),
        "BUILD_A_SPEC_WORD_TOKEN": "token",
        "BUILD_A_SPEC_WORD_EXECUTABLE": r"C:\Office\WINWORD.EXE",
    }

    assert renderer._cleanup_owned_word(
        environment,
        handle_factory=lambda pid: handle,
    ) == ""

    assert handle.terminated
    assert handle.closed
    assert not ownership_path.exists()


@pytest.mark.parametrize(
    ("record_overrides", "identity", "expected"),
    [
        (
            {"token": "attacker"},
            renderer._ProcessIdentity(41, 9001, r"C:\Office\WINWORD.EXE"),
            "token mismatch",
        ),
        (
            {},
            renderer._ProcessIdentity(42, 9001, r"C:\Office\WINWORD.EXE"),
            "PID mismatch",
        ),
        (
            {},
            renderer._ProcessIdentity(41, 9002, r"C:\Office\WINWORD.EXE"),
            "process creation-time mismatch",
        ),
        (
            {"executable": r"C:\Other\WINWORD.EXE"},
            renderer._ProcessIdentity(41, 9001, r"C:\Office\WINWORD.EXE"),
            "recorded executable mismatch",
        ),
        (
            {},
            renderer._ProcessIdentity(41, 9001, r"C:\Other\WINWORD.EXE"),
            "running executable mismatch",
        ),
    ],
)
def test_cleanup_rejects_mismatch_without_terminating(
    tmp_path,
    record_overrides,
    identity,
    expected,
):
    ownership_path = tmp_path / "owner.json"
    _write_ownership_record(ownership_path, **record_overrides)
    handle = _FakeProcessHandle(identity)
    environment = {
        "BUILD_A_SPEC_WORD_OWNERSHIP": str(ownership_path),
        "BUILD_A_SPEC_WORD_TOKEN": "token",
        "BUILD_A_SPEC_WORD_EXECUTABLE": r"C:\Office\WINWORD.EXE",
    }

    result = renderer._cleanup_owned_word(
        environment,
        handle_factory=lambda pid: handle,
    )

    assert expected in result
    assert not handle.terminated
    assert handle.closed is (
        expected not in {"token mismatch", "recorded executable mismatch"}
    )
    assert not ownership_path.exists()


class _FakeImage:
    def __init__(self, payload):
        self.payload = payload
        self.closed = False

    def save(self, path, *, format):
        assert format == "PNG"
        Path(path).write_bytes(self.payload)

    def close(self):
        self.closed = True


def test_rasterizer_writes_contiguous_pages_and_closes_images(
    monkeypatch,
    tmp_path,
):
    images = [_FakeImage(b"page one"), _FakeImage(b"page two")]
    calls = []
    fake_pdf2image = ModuleType("pdf2image")

    def convert_from_path(path, **kwargs):
        calls.append((path, kwargs))
        return images

    fake_pdf2image.convert_from_path = convert_from_path
    monkeypatch.setitem(sys.modules, "pdf2image", fake_pdf2image)
    pdf_path = tmp_path / "input.pdf"
    pdf_path.write_bytes(b"%PDF")

    pages = renderer._rasterize_pdf(pdf_path, tmp_path, dpi=168)

    assert [page.name for page in pages] == ["page-1.png", "page-2.png"]
    assert [page.read_bytes() for page in pages] == [b"page one", b"page two"]
    assert calls == [
        (
            str(pdf_path),
            {"dpi": 168, "fmt": "png", "thread_count": 8},
        )
    ]
    assert all(image.closed for image in images)


@pytest.mark.parametrize("emit_pdf", [False, True])
def test_publication_replaces_complete_set_and_removes_stale_pages(
    tmp_path,
    emit_pdf,
):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    for page_number in range(1, 4):
        (output_dir / f"page-{page_number}.png").write_bytes(
            f"old {page_number}".encode()
        )
    (output_dir / "source.pdf").write_bytes(b"old pdf")
    (output_dir / "keep.txt").write_bytes(b"unmanaged")
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source_pages = []
    for page_number in range(1, 3):
        page = source_dir / f"page-{page_number}.png"
        page.write_bytes(f"new {page_number}".encode())
        source_pages.append(page)
    source_pdf = source_dir / "source.pdf"
    source_pdf.write_bytes(b"new pdf")

    published = renderer._publish_rendered_outputs(
        source_pages,
        source_pdf,
        output_dir,
        stem="source",
        emit_pdf=emit_pdf,
    )

    assert [page.read_bytes() for page in published] == [b"new 1", b"new 2"]
    assert not (output_dir / "page-3.png").exists()
    assert (output_dir / "source.pdf").exists() is emit_pdf
    if emit_pdf:
        assert (output_dir / "source.pdf").read_bytes() == b"new pdf"
    assert (output_dir / "keep.txt").read_bytes() == b"unmanaged"
    assert not list(output_dir.glob(".build-a-spec-word-*"))


def test_publication_failure_restores_previous_managed_set(monkeypatch, tmp_path):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    expected = {
        "page-1.png": b"old 1",
        "page-2.png": b"old 2",
        "page-3.png": b"old 3",
        "source.pdf": b"old pdf",
    }
    for name, payload in expected.items():
        (output_dir / name).write_bytes(payload)
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source_pages = []
    for page_number in range(1, 3):
        page = source_dir / f"page-{page_number}.png"
        page.write_bytes(f"new {page_number}".encode())
        source_pages.append(page)
    source_pdf = source_dir / "source.pdf"
    source_pdf.write_bytes(b"new pdf")
    real_replace = renderer.os.replace
    failed = False

    def fail_second_page_once(source, destination):
        nonlocal failed
        source = Path(source)
        if (
            not failed
            and source.parent.name.endswith(".stage")
            and source.name == "page-2.png"
        ):
            failed = True
            raise OSError("injected publication failure")
        return real_replace(source, destination)

    monkeypatch.setattr(renderer.os, "replace", fail_second_page_once)

    with pytest.raises(renderer.WordRendererError, match="injected publication failure"):
        renderer._publish_rendered_outputs(
            source_pages,
            source_pdf,
            output_dir,
            stem="source",
            emit_pdf=True,
        )

    assert {
        name: (output_dir / name).read_bytes()
        for name in expected
    } == expected
    assert not list(output_dir.glob(".build-a-spec-word-*"))


@pytest.mark.parametrize("emit_pdf", [False, True])
def test_render_docx_orchestrates_word_pdf_and_pages(
    monkeypatch,
    tmp_path,
    emit_pdf,
):
    input_path = tmp_path / "source.docx"
    input_path.write_bytes(b"DOCX")
    output_dir = tmp_path / "rendered"
    word_path = tmp_path / "WINWORD.EXE"
    word_path.write_bytes(b"WORD")
    observed = {}

    def convert(input_docx, output_pdf, **kwargs):
        observed["convert"] = (input_docx, output_pdf, kwargs)
        output_pdf.write_bytes(b"%PDF word")

    def rasterize(pdf_path, destination, *, dpi):
        observed["rasterize"] = (pdf_path, destination, dpi)
        page = destination / "page-1.png"
        page.write_bytes(b"PNG")
        return (page,)

    monkeypatch.setattr(renderer, "_convert_with_word", convert)
    monkeypatch.setattr(renderer, "_rasterize_pdf", rasterize)

    pages = renderer.render_docx(
        input_path,
        output_dir,
        dpi=144,
        emit_pdf=emit_pdf,
        word_executable=word_path,
        timeout_seconds=30,
    )

    assert [page.name for page in pages] == ["page-1.png"]
    assert observed["convert"][0] == input_path
    assert observed["convert"][2]["word_executable"] == word_path
    assert observed["rasterize"][1].name == "raster"
    assert observed["rasterize"][2] == 144
    retained_pdf = output_dir / "source.pdf"
    assert retained_pdf.exists() is emit_pdf
    if emit_pdf:
        assert retained_pdf.read_bytes() == b"%PDF word"


def test_main_accepts_docx_render_harness_cli(monkeypatch, tmp_path, capsys):
    input_path = tmp_path / "input.docx"
    output_dir = tmp_path / "output"
    calls = []
    monkeypatch.setattr(
        renderer,
        "render_docx",
        lambda *args, **kwargs: calls.append((args, kwargs)) or (),
    )

    renderer.main(
        [
            str(input_path),
            "--output_dir",
            str(output_dir),
            "--dpi",
            "150",
            "--emit_pdf",
        ]
    )

    assert calls == [
        (
            (input_path, output_dir),
            {"dpi": 150, "emit_pdf": True, "verbose": False},
        )
    ]
    assert "Pages rendered to" in capsys.readouterr().out
