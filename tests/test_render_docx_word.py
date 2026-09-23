from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
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


# ---------------------------------------------------------------------------
# Resolve mode: Accept All / Reject All + save as a new DOCX (the redline's
# real-Word judge — Redline on your original, Phase 2)
# ---------------------------------------------------------------------------


def _bridge_script() -> str:
    return renderer._AUTOMATION_SCRIPT.read_text(encoding="utf-8")


def test_resolve_mode_runs_behind_the_same_safety_contract():
    """Resolve mode is a branch INSIDE the owned, hidden, alerts-off,
    macros-off Word the render mode already proves — never a second
    activation with rules of its own."""
    script = _bridge_script()
    for requirement in (
        "$env:BUILD_A_SPEC_WORD_MODE",
        "$env:BUILD_A_SPEC_WORD_JOBS",
        "$env:BUILD_A_SPEC_WORD_RESULT",
        "Unknown Word resolve action",
        # Every job: opened read-only and kept off Recent Files...
        "[ref]$jobReadOnly",
        "$jobReadOnly = $true",
        "[ref]$jobAddToRecentFiles",
        "$jobAddToRecentFiles = $false",
        # ...in a window the owned WINWORD holds, or the whole batch stops.
        "$jobWindowProcessId -ne $wordProcessId",
        "$ownershipViolation = $true",
        # What Word read, the way the Reviewing Pane lists it.
        "$jobAuthors.Add([string]$jobRevision.Author)",
        # Accept All / Reject All Changes, whatever the markup view shows.
        "$document.AcceptAllRevisions()",
        "$document.RejectAllRevisions()",
        # Saved as a NEW .docx (Word's default format), off Recent Files.
        "$jobSaveFormat = 16",
        "[ref]$jobSaveAddToRecentFiles",
        "$jobSaveAddToRecentFiles = $false",
        # Closed without saving over anything.
        "$document.Close([ref]$closeSaveChanges)",
    ):
        assert requirement in script, requirement
    # One activation, one set of safety settings, shared by both modes.
    for shared in (
        "New-Object -ComObject Word.Application",
        "$word.Visible = $false",
        "$word.DisplayAlerts = 0",
        "$word.AutomationSecurity = 3",
        "Stop-OwnedWordProcess -WaitSeconds 10",
    ):
        assert script.count(shared) == 1, shared
    # Nothing is opened before alerts and macros are off.
    guarded = script.index("$word.AutomationSecurity = 3")
    opens = [
        index
        for index in range(len(script))
        if script.startswith("$documents.Open(", index)
    ]
    assert len(opens) == 2 and all(index > guarded for index in opens)
    # Accept/Reject never runs on a document the bridge did not first
    # prove is its own.
    assert script.index("$jobWindowProcessId -ne $wordProcessId") < script.index(
        "$document.AcceptAllRevisions()"
    )


def test_every_bridge_environment_names_its_own_mode(monkeypatch, tmp_path):
    """A render never inherits a resolve request from its parent environment,
    and a resolve never inherits a render's paths."""
    for name, value in (
        ("BUILD_A_SPEC_WORD_MODE", "resolve"),
        ("BUILD_A_SPEC_WORD_JOBS", "stale-jobs.json"),
        ("BUILD_A_SPEC_WORD_RESULT", "stale-result.json"),
        ("BUILD_A_SPEC_WORD_INPUT", "stale.docx"),
        ("BUILD_A_SPEC_WORD_PDF", "stale.pdf"),
    ):
        monkeypatch.setenv(name, value)

    render = renderer._word_environment(
        tmp_path / "in.docx",
        tmp_path / "out.pdf",
        tmp_path / "WINWORD.EXE",
        tmp_path / "owner.txt",
    )
    assert render["BUILD_A_SPEC_WORD_MODE"] == "render"
    assert "BUILD_A_SPEC_WORD_JOBS" not in render
    assert "BUILD_A_SPEC_WORD_RESULT" not in render

    resolve = renderer._resolve_environment(
        tmp_path / "jobs & more.json",
        tmp_path / "result 'quoted'.json",
        tmp_path / "Office 16" / "WINWORD.EXE",
        tmp_path / "owner.txt",
    )
    assert resolve["BUILD_A_SPEC_WORD_MODE"] == "resolve"
    assert resolve["BUILD_A_SPEC_WORD_JOBS"] == str(tmp_path / "jobs & more.json")
    assert resolve["BUILD_A_SPEC_WORD_RESULT"] == str(tmp_path / "result 'quoted'.json")
    assert "BUILD_A_SPEC_WORD_INPUT" not in resolve
    assert "BUILD_A_SPEC_WORD_PDF" not in resolve
    assert resolve["BUILD_A_SPEC_WORD_CLEANUP_ONLY"] == "0"
    assert len(resolve["BUILD_A_SPEC_WORD_TOKEN"]) == 32
    assert resolve["BUILD_A_SPEC_WORD_TOKEN"] != render["BUILD_A_SPEC_WORD_TOKEN"]


def _docx(tmp_path, name: str) -> Path:
    path = tmp_path / name
    path.write_bytes(b"PK input " + name.encode())
    return path


class _FakeResolveWord:
    """Stands in for the bridge: reads the job file it was handed, writes
    each output, and answers the way the PowerShell does (a BOM included)."""

    def __init__(self, *, edit=None):
        self.calls: list[dict] = []
        self.edit = edit

    def __call__(self, environment, *, timeout_seconds):
        self.calls.append({"environment": environment, "timeout": timeout_seconds})
        manifest = json.loads(
            Path(environment["BUILD_A_SPEC_WORD_JOBS"]).read_text(encoding="utf-8")
        )
        records = []
        for job in manifest["jobs"]:
            Path(job["output"]).write_bytes(b"PK " + job["action"].encode())
            records.append(
                {
                    "input": job["input"],
                    "output": job["output"],
                    "action": job["action"],
                    "ok": True,
                    "error": "",
                    "revisions_before": 0 if job["action"] == "resave" else 3,
                    "revisions_after": 0,
                    "authors": [] if job["action"] == "resave" else ["Build-a-Spec"],
                }
            )
        payload = {"word_version": "16.0", "word_build": "16.0.1234.5678", "jobs": records}
        if self.edit is not None:
            payload = self.edit(payload) or payload
        Path(environment["BUILD_A_SPEC_WORD_RESULT"]).write_text(
            json.dumps(payload), encoding="utf-8-sig"
        )
        return subprocess.CompletedProcess(
            args=["powershell"], returncode=0, stdout="Resolved", stderr=""
        )


def _resolve(tmp_path, jobs, **kwargs):
    return renderer.resolve_docx(
        jobs, word_executable=tmp_path / "WINWORD.EXE", **kwargs
    )


def test_resolve_hands_word_a_job_file_and_reads_its_answers(monkeypatch, tmp_path):
    fake = _FakeResolveWord()
    monkeypatch.setattr(renderer, "_run_powershell", fake)
    redline = _docx(tmp_path, "redline & copy.docx")
    reference = _docx(tmp_path, "reference.docx")
    jobs = [
        renderer.ResolveJob(redline, tmp_path / "accepted.docx", "accept"),
        renderer.ResolveJob(redline, tmp_path / "rejected.docx", "reject"),
        renderer.ResolveJob(reference, tmp_path / "resaved.docx", "resave"),
    ]

    resolution = _resolve(tmp_path, jobs, timeout_seconds=45)

    assert (resolution.word_version, resolution.word_build) == ("16.0", "16.0.1234.5678")
    assert [d.job.action for d in resolution.documents] == ["accept", "reject", "resave"]
    assert all(d.ok and d.error == "" for d in resolution.documents)
    assert resolution.documents[0].authors == ("Build-a-Spec",)
    assert resolution.documents[0].revisions_before == 3
    assert resolution.documents[2].authors == ()
    (call,) = fake.calls
    assert call["timeout"] == 45
    environment = call["environment"]
    assert environment["BUILD_A_SPEC_WORD_MODE"] == "resolve"
    # The paths travel in the job file, never on a command line.
    assert all(str(redline) not in value for value in environment.values())


def test_resolve_times_out_per_document(monkeypatch, tmp_path):
    fake = _FakeResolveWord()
    monkeypatch.setattr(renderer, "_run_powershell", fake)
    monkeypatch.setenv("BUILD_A_SPEC_WORD_TIMEOUT", "7")
    source = _docx(tmp_path, "source.docx")
    jobs = [
        renderer.ResolveJob(source, tmp_path / f"out-{index}.docx", "resave")
        for index in range(3)
    ]
    _resolve(tmp_path, jobs)
    assert fake.calls[0]["timeout"] == 21


def test_one_document_word_cannot_resolve_fails_only_its_job(monkeypatch, tmp_path):
    def break_second(payload):
        second = payload["jobs"][1]
        Path(second["output"]).unlink()
        second.update(ok=False, error="Word could not open the file.", revisions_before=-1)

    monkeypatch.setattr(renderer, "_run_powershell", _FakeResolveWord(edit=break_second))
    source = _docx(tmp_path, "source.docx")
    jobs = [
        renderer.ResolveJob(source, tmp_path / "a.docx", "accept"),
        renderer.ResolveJob(source, tmp_path / "b.docx", "reject"),
    ]
    first, second = _resolve(tmp_path, jobs).documents
    assert first.ok
    assert not second.ok and second.error == "Word could not open the file."


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda p: p["jobs"].reverse(), "answers a job it was not given"),
        (lambda p: p["jobs"].pop(), "does not answer every job"),
        (lambda p: Path(p["jobs"][0]["output"]).unlink(), "missing or empty"),
        (lambda p: p["jobs"][0].update(ok="yes"), "ok is not a boolean"),
        (lambda p: p["jobs"][0].update(revisions_after=-1), "incomplete"),
        (lambda p: p["jobs"][0].update(ok=False), "carries no reason"),
        (lambda p: p["jobs"][0].update(revisions_before=True), "count"),
        (lambda p: p["jobs"][0].update(authors=[3]), "authors are not text"),
        (lambda p: ["not", "an", "object"], "not an object"),
    ],
)
def test_resolve_trusts_nothing_word_says_without_a_check(
    monkeypatch, tmp_path, edit, message
):
    monkeypatch.setattr(renderer, "_run_powershell", _FakeResolveWord(edit=edit))
    source = _docx(tmp_path, "source.docx")
    jobs = [
        renderer.ResolveJob(source, tmp_path / "a.docx", "accept"),
        renderer.ResolveJob(source, tmp_path / "b.docx", "reject"),
    ]
    with pytest.raises(renderer.WordRendererError, match=message):
        _resolve(tmp_path, jobs)


def test_windows_powershell_shapes_of_one_job_are_read(monkeypatch, tmp_path):
    """PowerShell 5.1 may write a one-element array as its element, and a
    one-author list as a bare string."""

    def unwrap(payload):
        (record,) = payload["jobs"]
        record["authors"] = "Build-a-Spec"
        payload["jobs"] = record

    monkeypatch.setattr(renderer, "_run_powershell", _FakeResolveWord(edit=unwrap))
    source = _docx(tmp_path, "source.docx")
    (document,) = _resolve(
        tmp_path, [renderer.ResolveJob(source, tmp_path / "a.docx", "accept")]
    ).documents
    assert document.authors == ("Build-a-Spec",)


def test_a_bridge_that_writes_no_answer_is_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(
        renderer,
        "_run_powershell",
        lambda environment, timeout_seconds: subprocess.CompletedProcess(
            args=["powershell"], returncode=0, stdout="", stderr=""
        ),
    )
    source = _docx(tmp_path, "source.docx")
    with pytest.raises(renderer.WordRendererError, match="no readable resolve result"):
        _resolve(tmp_path, [renderer.ResolveJob(source, tmp_path / "a.docx", "accept")])


@pytest.mark.parametrize("failure", ["timeout", "exit"])
def test_resolve_failures_clean_up_the_owned_word(monkeypatch, tmp_path, failure):
    cleanup_calls = []

    def fail(environment, *, timeout_seconds):
        if failure == "timeout":
            raise subprocess.TimeoutExpired("powershell", timeout_seconds)
        return subprocess.CompletedProcess(
            args=["powershell"], returncode=1, stdout="", stderr="COM failed"
        )

    monkeypatch.setattr(renderer, "_run_powershell", fail)
    monkeypatch.setattr(
        renderer,
        "_cleanup_owned_word",
        lambda environment: cleanup_calls.append(environment) or "",
    )
    source = _docx(tmp_path, "source.docx")
    with pytest.raises(renderer.WordRendererError, match="timed out|COM failed"):
        _resolve(tmp_path, [renderer.ResolveJob(source, tmp_path / "a.docx", "accept")])
    assert len(cleanup_calls) == 1
    assert cleanup_calls[0]["BUILD_A_SPEC_WORD_MODE"] == "resolve"


def test_resolve_refuses_bad_jobs_before_word_starts(monkeypatch, tmp_path):
    fake = _FakeResolveWord()
    monkeypatch.setattr(renderer, "_run_powershell", fake)
    source = _docx(tmp_path, "source.docx")
    taken = _docx(tmp_path, "taken.docx")
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    bad = [
        ([renderer.ResolveJob(source, taken, "accept")], "already exists"),
        ([renderer.ResolveJob(source, tmp_path / "a.docx", "approve")], "Unknown resolve action"),
        ([renderer.ResolveJob(tmp_path / "missing.docx", tmp_path / "a.docx", "accept")], "does not exist"),
        ([renderer.ResolveJob(tmp_path / "notes.txt", tmp_path / "a.docx", "accept")], ".docx input only"),
        ([renderer.ResolveJob(source, tmp_path / "a.pdf", "accept")], "as .docx only"),
        ([renderer.ResolveJob(source, tmp_path / "no" / "a.docx", "accept")], "directory does not exist"),
        (
            [
                renderer.ResolveJob(source, tmp_path / "a.docx", "accept"),
                renderer.ResolveJob(source, tmp_path / "A.DOCX", "reject"),
            ],
            "Two resolve jobs",
        ),
        ([], "at least one job"),
    ]
    # An output that IS an input under Windows' case rules: a case-insensitive
    # filesystem already says it exists; on any other, the path comparison
    # (Windows-normalized) still refuses it.
    shouting = tmp_path / "SOURCE.DOCX"
    bad.append(
        (
            [renderer.ResolveJob(source, shouting, "accept")],
            "already exists" if shouting.exists() else "would overwrite an input",
        )
    )
    for jobs, message in bad:
        with pytest.raises(renderer.WordRendererError, match=message):
            _resolve(tmp_path, jobs)
    assert fake.calls == []
    assert taken.read_bytes() == b"PK input taken.docx"
    assert source.read_bytes() == b"PK input source.docx"


def test_resolve_cli_resolves_one_file(monkeypatch, tmp_path, capsys):
    calls = []

    def resolve(jobs, **kwargs):
        calls.append((jobs, kwargs))
        (job,) = jobs
        return renderer.WordResolution(
            word_version="16.0",
            word_build="16.0.1234.5678",
            documents=(
                renderer.ResolvedDocx(
                    job=job,
                    ok=True,
                    error="",
                    revisions_before=4,
                    revisions_after=0,
                    authors=("Build-a-Spec",),
                ),
            ),
        )

    monkeypatch.setattr(renderer, "resolve_docx", resolve)
    renderer.main(
        [str(tmp_path / "in.docx"), "--resolve", "reject", "--output", str(tmp_path / "out.docx")]
    )
    ((jobs, kwargs),) = calls
    assert jobs == [renderer.ResolveJob(tmp_path / "in.docx", tmp_path / "out.docx", "reject")]
    assert kwargs == {"verbose": False}
    out = capsys.readouterr().out
    assert "4 tracked change(s) by Build-a-Spec; 0 left after 'reject'" in out


def test_resolve_cli_reports_words_own_reason(monkeypatch, tmp_path, capsys):
    def resolve(jobs, **kwargs):
        (job,) = jobs
        return renderer.WordResolution(
            "16.0",
            "16.0.1",
            (renderer.ResolvedDocx(job, False, "The file is corrupt.", -1, -1, ()),),
        )

    monkeypatch.setattr(renderer, "resolve_docx", resolve)
    with pytest.raises(SystemExit) as exited:
        renderer.main(
            [str(tmp_path / "in.docx"), "--resolve", "accept", "--output", str(tmp_path / "o.docx")]
        )
    assert exited.value.code == 1
    assert "The file is corrupt." in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["in.docx", "--resolve", "accept"],
        ["in.docx", "--output", "out.docx"],
    ],
    ids=["resolve-without-output", "output-without-resolve"],
)
def test_resolve_cli_flags_come_as_a_pair(monkeypatch, argv):
    monkeypatch.setattr(renderer, "resolve_docx", lambda *a, **k: pytest.fail("ran"))
    monkeypatch.setattr(renderer, "render_docx", lambda *a, **k: pytest.fail("ran"))
    with pytest.raises(SystemExit) as exited:
        renderer.main(argv)
    assert exited.value.code == 2


#: Every PowerShell script the repo ships. Windows PowerShell 5.1 runs them;
#: CI's Linux runner has PowerShell 7, whose parser reads a superset.
_POWERSHELL_SCRIPTS = (
    renderer._AUTOMATION_SCRIPT,
    Path(__file__).parent / "fixtures" / "docx_corpus" / "generate_word_fixtures.ps1",
)
_WINDOWS_POWERSHELL_CHECK = r"""
$path = $env:BUILD_A_SPEC_PS_CHECK
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $path, [ref]$tokens, [ref]$parseErrors
)
$newer = @()
$newer += @($ast.FindAll({
    param($node)
    $node.GetType().Name -in @("TernaryExpressionAst", "PipelineChainAst")
}, $true) | ForEach-Object { $_.GetType().Name })
$newer += @($tokens | Where-Object {
    $_.Kind.ToString() -in @(
        "QuestionQuestion", "QuestionQuestionEquals", "QuestionDot",
        "QuestionLBracket", "AndAnd", "OrOr"
    )
} | ForEach-Object { $_.Kind.ToString() })
ConvertTo-Json -Compress -InputObject ([ordered]@{
    errors = @($parseErrors | ForEach-Object { $_.Message })
    newer = $newer
})
"""


@pytest.mark.parametrize("script", _POWERSHELL_SCRIPTS, ids=lambda path: path.name)
def test_the_powershell_scripts_parse_for_windows_powershell(script):
    """The bridge and the fixture producer never run in CI (they need Word),
    so a syntax error would first surface on the owner's machine. PowerShell
    7 parses a superset of 5.1: this also refuses the 7-only syntax (``??``,
    ``?.``, ternaries, ``&&``/``||`` chains) that 5.1 cannot run."""
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("PowerShell 7 (pwsh) is not installed; CI's Linux runner has it")
    completed = subprocess.run(
        [pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", _WINDOWS_POWERSHELL_CHECK],
        env={**os.environ, "BUILD_A_SPEC_PS_CHECK": str(script)},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["errors"] == []
    assert result["newer"] == []
