"""Drive a private Microsoft Word instance on Windows: render, or resolve.

Word automation runs in a hidden STA Windows PowerShell process.  That bridge
records and verifies the new WINWORD process before opening a document, so
cleanup can never intentionally quit or kill a pre-existing Word instance.

Two modes share that bridge and every one of its safety rules:

* **render** (:func:`render_docx`, the default CLI): one DOCX to page PNGs
  for the visual-regression suite.  PDF rasterization stays in the bundled
  Python runtime via pdf2image/Pillow.
* **resolve** (:func:`resolve_docx`, ``--resolve``): real Word opens each
  DOCX read-only, runs Accept All or Reject All of its tracked changes (or
  neither — a plain re-save), and saves the result as a NEW DOCX.  This is
  the redline on your original's real-Word judge
  (``tests/test_redline_word_judge.py``, Redline on your original, Phase 2).
  It needs no rasterizer, so it runs from the repo virtual environment.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import json
import ntpath
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
from typing import Protocol, Sequence


WORD_EXECUTABLE_ENV = "BUILD_A_SPEC_WORD_EXECUTABLE"
WORD_TIMEOUT_ENV = "BUILD_A_SPEC_WORD_TIMEOUT"
DEFAULT_WORD_EXECUTABLE = Path(
    r"C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE"
)
DEFAULT_WORD_TIMEOUT_SECONDS = 120
_AUTOMATION_SCRIPT = Path(__file__).with_name("render_docx_word_automation.ps1")
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_PAGE_OUTPUT = re.compile(r"^page-(\d+)\.png$", re.IGNORECASE)
#: What resolve mode may do to a document before saving it as a new DOCX:
#: Review > Accept All Changes, Reject All Changes, or nothing (a plain
#: re-save, which gives the judge Word's own serialization of a reference).
RESOLVE_ACTIONS = ("accept", "reject", "resave")
#: Every environment variable the bridge reads, so a render never inherits a
#: resolve request from its parent environment and vice versa.
_MODE_VARIABLES = (
    "BUILD_A_SPEC_WORD_MODE",
    "BUILD_A_SPEC_WORD_INPUT",
    "BUILD_A_SPEC_WORD_PDF",
    "BUILD_A_SPEC_WORD_JOBS",
    "BUILD_A_SPEC_WORD_RESULT",
)


class WordRendererError(RuntimeError):
    """Word conversion or PDF rasterization failed."""


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Render DOCX to page PNGs using an isolated Microsoft Word instance, "
            "or (--resolve) have that instance accept or reject every tracked "
            "change and save the result as a new DOCX."
        )
    )
    parser.add_argument("input_path", help="Path to the input DOCX file.")
    parser.add_argument(
        "--output_dir",
        default=None,
        help="Output directory; defaults to a directory beside the DOCX.",
    )
    parser.add_argument(
        "--dpi",
        type=_positive_int,
        default=144,
        help="PDF rasterization DPI (default 144).",
    )
    parser.add_argument(
        "--emit_pdf",
        action="store_true",
        help="Also retain <input_stem>.pdf in the output directory.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print captured Word automation diagnostics.",
    )
    parser.add_argument(
        "--resolve",
        choices=RESOLVE_ACTIONS,
        default=None,
        help=(
            "Instead of rendering: Accept All or Reject All of the input's "
            "tracked changes in Word (or 'resave' for neither) and save the "
            "result as the --output DOCX."
        ),
    )
    parser.add_argument(
        "--output",
        default=None,
        help="The new DOCX --resolve writes; it must not exist yet.",
    )
    return parser


def _configured_word_executable() -> Path:
    configured = os.environ.get(WORD_EXECUTABLE_ENV, "").strip()
    path = Path(configured).expanduser() if configured else DEFAULT_WORD_EXECUTABLE
    path = path.resolve()
    if not path.is_file():
        raise WordRendererError(
            f"Microsoft Word executable does not exist: {path}. "
            f"Set {WORD_EXECUTABLE_ENV} when Word is installed elsewhere."
        )
    return path


def _configured_word_timeout() -> int:
    raw = os.environ.get(WORD_TIMEOUT_ENV, "").strip()
    if not raw:
        return DEFAULT_WORD_TIMEOUT_SECONDS
    try:
        return _positive_int(raw)
    except argparse.ArgumentTypeError as exc:
        raise WordRendererError(f"{WORD_TIMEOUT_ENV} {exc}") from exc


def _powershell_executable() -> Path:
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    path = (
        Path(system_root)
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    ).resolve()
    if not path.is_file():
        raise WordRendererError(f"Windows PowerShell 5.1 was not found: {path}")
    return path


def _powershell_command() -> list[str]:
    if not _AUTOMATION_SCRIPT.is_file():
        raise WordRendererError(f"Word automation bridge is missing: {_AUTOMATION_SCRIPT}")
    script = _AUTOMATION_SCRIPT.read_text(encoding="utf-8")
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return [
        str(_powershell_executable()),
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-STA",
        "-WindowStyle",
        "Hidden",
        "-EncodedCommand",
        encoded,
    ]


def _run_powershell(
    environment: dict[str, str],
    *,
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _powershell_command(),
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        timeout=timeout_seconds,
        creationflags=_CREATE_NO_WINDOW,
    )


def _diagnostics(completed: subprocess.CompletedProcess[str]) -> str:
    return "\n".join(
        value.strip()
        for value in (completed.stdout, completed.stderr)
        if value and value.strip()
    )


@dataclass(frozen=True)
class _OwnedWordRecord:
    token: str
    pid: int
    creation_time: int
    executable: str


@dataclass(frozen=True)
class _ProcessIdentity:
    pid: int
    creation_time: int
    executable: str


class _OwnedProcessHandle(Protocol):
    def identity(self) -> _ProcessIdentity: ...

    def terminate_and_wait(self, timeout_seconds: int) -> None: ...

    def close(self) -> None: ...


def _load_ownership_record(path: Path) -> _OwnedWordRecord:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        token = payload["token"]
        pid = payload["pid"]
        creation_time = payload["creation_time"]
        executable = payload["executable"]
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise WordRendererError(f"invalid Word ownership record: {exc}") from exc
    if not isinstance(token, str) or not token:
        raise WordRendererError("invalid Word ownership record token")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise WordRendererError("invalid Word ownership record PID")
    if (
        not isinstance(creation_time, int)
        or isinstance(creation_time, bool)
        or creation_time <= 0
    ):
        raise WordRendererError("invalid Word ownership record creation time")
    if not isinstance(executable, str) or not executable:
        raise WordRendererError("invalid Word ownership record executable")
    return _OwnedWordRecord(token, pid, creation_time, executable)


def _normalized_windows_path(path: str | Path) -> str:
    return ntpath.normcase(ntpath.normpath(str(path)))


def _ownership_rejection(
    record: _OwnedWordRecord,
    identity: _ProcessIdentity,
    *,
    expected_token: str,
    expected_executable: str | Path,
) -> str | None:
    record_rejection = _ownership_record_rejection(
        record,
        expected_token=expected_token,
        expected_executable=expected_executable,
    )
    if record_rejection:
        return record_rejection
    expected_path = _normalized_windows_path(expected_executable)
    if record.pid != identity.pid:
        return "PID mismatch"
    if record.creation_time != identity.creation_time:
        return "process creation-time mismatch"
    if _normalized_windows_path(identity.executable) != expected_path:
        return "running executable mismatch"
    return None


def _ownership_record_rejection(
    record: _OwnedWordRecord,
    *,
    expected_token: str,
    expected_executable: str | Path,
) -> str | None:
    expected_path = _normalized_windows_path(expected_executable)
    if not secrets.compare_digest(record.token, expected_token):
        return "token mismatch"
    if _normalized_windows_path(record.executable) != expected_path:
        return "recorded executable mismatch"
    return None


class _WindowsProcessHandle:
    """One validated Windows handle used for both inspection and termination."""

    _PROCESS_TERMINATE = 0x0001
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _SYNCHRONIZE = 0x00100000
    _WAIT_OBJECT_0 = 0
    _WAIT_TIMEOUT = 258

    def __init__(self, pid: int):
        if os.name != "nt":
            raise OSError("Word process cleanup is available only on Windows")
        self._pid = pid
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._configure_api()
        access = (
            self._PROCESS_TERMINATE
            | self._PROCESS_QUERY_LIMITED_INFORMATION
            | self._SYNCHRONIZE
        )
        self._handle = self._kernel32.OpenProcess(access, False, pid)
        if not self._handle:
            error = ctypes.get_last_error()
            if error in {87, 1168}:
                raise ProcessLookupError(pid)
            raise OSError(error, f"OpenProcess failed for PID {pid}")

    def _configure_api(self) -> None:
        kernel32 = self._kernel32
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        ]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateProcess.restype = wintypes.BOOL
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

    def identity(self) -> _ProcessIdentity:
        created = wintypes.FILETIME()
        exited = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if not self._kernel32.GetProcessTimes(
            self._handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            error = ctypes.get_last_error()
            raise OSError(error, f"GetProcessTimes failed for PID {self._pid}")
        creation_time = (created.dwHighDateTime << 32) | created.dwLowDateTime

        capacity = 32768
        executable = ctypes.create_unicode_buffer(capacity)
        size = wintypes.DWORD(capacity)
        if not self._kernel32.QueryFullProcessImageNameW(
            self._handle,
            0,
            executable,
            ctypes.byref(size),
        ):
            error = ctypes.get_last_error()
            raise OSError(
                error,
                f"QueryFullProcessImageNameW failed for PID {self._pid}",
            )
        return _ProcessIdentity(
            pid=self._pid,
            creation_time=creation_time,
            executable=executable.value,
        )

    def terminate_and_wait(self, timeout_seconds: int) -> None:
        if not self._kernel32.TerminateProcess(self._handle, 1):
            error = ctypes.get_last_error()
            raise OSError(error, f"TerminateProcess failed for PID {self._pid}")
        result = self._kernel32.WaitForSingleObject(
            self._handle,
            timeout_seconds * 1000,
        )
        if result == self._WAIT_TIMEOUT:
            raise TimeoutError(f"WINWORD PID {self._pid} did not exit")
        if result != self._WAIT_OBJECT_0:
            error = ctypes.get_last_error()
            raise OSError(error, f"WaitForSingleObject failed for PID {self._pid}")

    def close(self) -> None:
        handle, self._handle = getattr(self, "_handle", None), None
        if handle:
            self._kernel32.CloseHandle(handle)


def _cleanup_owned_word(
    environment: dict[str, str],
    *,
    handle_factory=None,
) -> str:
    ownership_path = Path(environment["BUILD_A_SPEC_WORD_OWNERSHIP"])
    if not ownership_path.is_file():
        return ""
    pending_path = Path(str(ownership_path) + ".pending")
    handle: _OwnedProcessHandle | None = None
    try:
        record = _load_ownership_record(ownership_path)
        record_rejection = _ownership_record_rejection(
            record,
            expected_token=environment["BUILD_A_SPEC_WORD_TOKEN"],
            expected_executable=environment["BUILD_A_SPEC_WORD_EXECUTABLE"],
        )
        if record_rejection:
            return f"owned Word cleanup rejected the record: {record_rejection}"
        factory = handle_factory or _WindowsProcessHandle
        handle = factory(record.pid)
        identity = handle.identity()
        rejection = _ownership_rejection(
            record,
            identity,
            expected_token=environment["BUILD_A_SPEC_WORD_TOKEN"],
            expected_executable=environment["BUILD_A_SPEC_WORD_EXECUTABLE"],
        )
        if rejection:
            return f"owned Word cleanup rejected the process: {rejection}"
        handle.terminate_and_wait(10)
        return ""
    except ProcessLookupError:
        return ""
    except (OSError, TimeoutError, WordRendererError) as exc:
        return f"owned Word cleanup could not complete: {exc}"
    finally:
        if handle is not None:
            handle.close()
        ownership_path.unlink(missing_ok=True)
        pending_path.unlink(missing_ok=True)


def _word_environment(
    input_path: Path,
    pdf_path: Path,
    word_executable: Path,
    ownership_path: Path,
) -> dict[str, str]:
    environment = _bridge_environment()
    environment.update(
        {
            "BUILD_A_SPEC_WORD_MODE": "render",
            "BUILD_A_SPEC_WORD_INPUT": str(input_path),
            "BUILD_A_SPEC_WORD_PDF": str(pdf_path),
            "BUILD_A_SPEC_WORD_EXECUTABLE": str(word_executable),
            "BUILD_A_SPEC_WORD_OWNERSHIP": str(ownership_path),
            "BUILD_A_SPEC_WORD_TOKEN": secrets.token_hex(16),
            "BUILD_A_SPEC_WORD_CLEANUP_ONLY": "0",
        }
    )
    return environment


def _bridge_environment() -> dict[str, str]:
    """The parent environment minus every variable that picks a mode or its
    paths — each call names exactly the ones it means."""
    environment = dict(os.environ)
    for name in _MODE_VARIABLES:
        environment.pop(name, None)
    return environment


def _resolve_environment(
    jobs_path: Path,
    result_path: Path,
    word_executable: Path,
    ownership_path: Path,
) -> dict[str, str]:
    environment = _bridge_environment()
    environment.update(
        {
            "BUILD_A_SPEC_WORD_MODE": "resolve",
            "BUILD_A_SPEC_WORD_JOBS": str(jobs_path),
            "BUILD_A_SPEC_WORD_RESULT": str(result_path),
            "BUILD_A_SPEC_WORD_EXECUTABLE": str(word_executable),
            "BUILD_A_SPEC_WORD_OWNERSHIP": str(ownership_path),
            "BUILD_A_SPEC_WORD_TOKEN": secrets.token_hex(16),
            "BUILD_A_SPEC_WORD_CLEANUP_ONLY": "0",
        }
    )
    return environment


def _convert_with_word(
    input_path: Path,
    pdf_path: Path,
    *,
    word_executable: Path,
    timeout_seconds: int,
    verbose: bool,
) -> None:
    ownership_path = pdf_path.with_name("word-ownership.txt")
    environment = _word_environment(
        input_path,
        pdf_path,
        word_executable,
        ownership_path,
    )
    _run_owned_word(environment, timeout_seconds=timeout_seconds, verbose=verbose)
    if not pdf_path.is_file() or pdf_path.stat().st_size <= 0:
        raise WordRendererError("Word automation did not produce a non-empty PDF.")


def _run_owned_word(
    environment: dict[str, str],
    *,
    timeout_seconds: int,
    verbose: bool,
) -> None:
    """Run the bridge once; on every failure path, clean up the Word it
    proved it started (and only that one)."""
    ownership_path = Path(environment["BUILD_A_SPEC_WORD_OWNERSHIP"])
    try:
        completed = _run_powershell(environment, timeout_seconds=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        cleanup = _cleanup_owned_word(environment)
        suffix = f"; {cleanup}" if cleanup else ""
        raise WordRendererError(
            f"Word automation timed out after {timeout_seconds} seconds{suffix}"
        ) from exc
    except OSError as exc:
        cleanup = _cleanup_owned_word(environment)
        suffix = f"; {cleanup}" if cleanup else ""
        raise WordRendererError(f"Word automation could not start: {exc}{suffix}") from exc

    diagnostics = _diagnostics(completed)
    if verbose and diagnostics:
        print(diagnostics)
    if completed.returncode != 0:
        cleanup = _cleanup_owned_word(environment)
        suffix = f"\nCleanup: {cleanup}" if cleanup else ""
        raise WordRendererError(
            f"Word automation exited {completed.returncode}.\n{diagnostics}{suffix}"
        )
    if ownership_path.exists():
        cleanup = _cleanup_owned_word(environment)
        if cleanup:
            raise WordRendererError(cleanup)


def _rasterize_pdf(pdf_path: Path, output_dir: Path, *, dpi: int) -> tuple[Path, ...]:
    try:
        from pdf2image import convert_from_path
    except ImportError as exc:
        raise WordRendererError(
            "The configured renderer Python must provide pdf2image and Pillow."
        ) from exc

    try:
        images = convert_from_path(str(pdf_path), dpi=dpi, fmt="png", thread_count=8)
    except Exception as exc:
        raise WordRendererError(f"PDF rasterization failed: {exc}") from exc
    if not images:
        raise WordRendererError("PDF rasterization produced no pages.")

    pages: list[Path] = []
    try:
        for page_number, image in enumerate(images, start=1):
            page_path = output_dir / f"page-{page_number}.png"
            temporary_path = output_dir / f".page-{page_number}.png.tmp"
            image.save(temporary_path, format="PNG")
            os.replace(temporary_path, page_path)
            pages.append(page_path)
    finally:
        for image in images:
            close = getattr(image, "close", None)
            if callable(close):
                close()
    return tuple(pages)


def _managed_output_paths(output_dir: Path, *, stem: str) -> tuple[Path, ...]:
    managed = []
    retained_pdf_name = f"{stem}.pdf"
    for candidate in output_dir.iterdir():
        if not candidate.is_file():
            continue
        if _PAGE_OUTPUT.fullmatch(candidate.name) or (
            candidate.name.casefold() == retained_pdf_name.casefold()
        ):
            managed.append(candidate)
    return tuple(sorted(managed, key=lambda path: path.name.casefold()))


def _publish_rendered_outputs(
    source_pages: Sequence[Path],
    source_pdf: Path,
    output_dir: Path,
    *,
    stem: str,
    emit_pdf: bool,
) -> tuple[Path, ...]:
    """Replace the complete managed render set, restoring it on any failure."""

    transaction = secrets.token_hex(8)
    stage_dir = output_dir / f".build-a-spec-word-{transaction}.stage"
    backup_dir = output_dir / f".build-a-spec-word-{transaction}.backup"
    staged_names = [f"page-{index}.png" for index in range(1, len(source_pages) + 1)]
    if emit_pdf:
        staged_names.append(f"{stem}.pdf")
    published: list[Path] = []
    backed_up: list[Path] = []
    rollback_errors: list[str] = []

    try:
        stage_dir.mkdir()
        backup_dir.mkdir()
        for source_page, target_name in zip(source_pages, staged_names):
            if not source_page.is_file():
                raise WordRendererError(f"Rendered page is missing: {source_page}")
            shutil.copy2(source_page, stage_dir / target_name)
        if emit_pdf:
            if not source_pdf.is_file() or source_pdf.stat().st_size <= 0:
                raise WordRendererError(f"Rendered PDF is missing: {source_pdf}")
            shutil.copy2(source_pdf, stage_dir / f"{stem}.pdf")

        for existing in _managed_output_paths(output_dir, stem=stem):
            os.replace(existing, backup_dir / existing.name)
            backed_up.append(existing)
        for target_name in staged_names:
            target = output_dir / target_name
            os.replace(stage_dir / target_name, target)
            published.append(target)
    except Exception as exc:
        for target in reversed(published):
            try:
                if target.exists():
                    os.replace(target, stage_dir / target.name)
            except OSError as rollback_exc:
                rollback_errors.append(f"remove {target.name}: {rollback_exc}")
        for original in reversed(backed_up):
            try:
                backup = backup_dir / original.name
                if backup.exists():
                    os.replace(backup, original)
            except OSError as rollback_exc:
                rollback_errors.append(f"restore {original.name}: {rollback_exc}")
        detail = f"Render publication failed: {exc}"
        if rollback_errors:
            detail += "; rollback errors: " + "; ".join(rollback_errors)
        raise WordRendererError(detail) from exc
    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)
        shutil.rmtree(backup_dir, ignore_errors=True)

    return tuple(output_dir / f"page-{index}.png" for index in range(1, len(source_pages) + 1))


def render_docx(
    input_path: Path,
    output_dir: Path,
    *,
    dpi: int,
    emit_pdf: bool,
    verbose: bool = False,
    word_executable: Path | None = None,
    timeout_seconds: int | None = None,
) -> tuple[Path, ...]:
    input_path = input_path.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    if not input_path.is_file():
        raise WordRendererError(f"Input DOCX does not exist: {input_path}")
    if input_path.suffix.casefold() != ".docx":
        raise WordRendererError(f"Word renderer accepts .docx input only: {input_path}")
    output_dir.mkdir(parents=True, exist_ok=True)
    word_executable = word_executable or _configured_word_executable()
    timeout_seconds = timeout_seconds or _configured_word_timeout()

    with tempfile.TemporaryDirectory(prefix="build_a_spec_word_") as temp_dir:
        temporary_root = Path(temp_dir)
        temporary_pdf = temporary_root / f"{input_path.stem}.pdf"
        raster_dir = temporary_root / "raster"
        raster_dir.mkdir()
        _convert_with_word(
            input_path,
            temporary_pdf,
            word_executable=word_executable,
            timeout_seconds=timeout_seconds,
            verbose=verbose,
        )
        staged_pages = _rasterize_pdf(temporary_pdf, raster_dir, dpi=dpi)
        return _publish_rendered_outputs(
            staged_pages,
            temporary_pdf,
            output_dir,
            stem=input_path.stem,
            emit_pdf=emit_pdf,
        )


@dataclass(frozen=True)
class ResolveJob:
    """One document for resolve mode: Word opens ``input_path`` read-only,
    applies ``action`` (one of :data:`RESOLVE_ACTIONS`) and saves the result
    as the NEW file ``output_path``."""

    input_path: Path
    output_path: Path
    action: str


@dataclass(frozen=True)
class ResolvedDocx:
    """What Word did with one :class:`ResolveJob`.

    ``revisions_before`` is how many tracked changes Word read in the
    document and ``authors`` their distinct authors — what the Reviewing
    Pane lists; ``revisions_after`` is how many were left once the action
    ran. ``error`` is Word's own message when it could not open, resolve or
    save this document; the rest of the batch still ran."""

    job: ResolveJob
    ok: bool
    error: str
    revisions_before: int
    revisions_after: int
    authors: tuple[str, ...]


@dataclass(frozen=True)
class WordResolution:
    """One owned Word's answers for a batch, in job order."""

    word_version: str
    word_build: str
    documents: tuple[ResolvedDocx, ...]


def _validated_jobs(jobs: Sequence[ResolveJob]) -> tuple[ResolveJob, ...]:
    validated: list[ResolveJob] = []
    outputs: set[str] = set()
    inputs: set[str] = set()
    for job in jobs:
        if job.action not in RESOLVE_ACTIONS:
            raise WordRendererError(
                f"Unknown resolve action {job.action!r}; "
                f"expected one of {', '.join(RESOLVE_ACTIONS)}"
            )
        input_path = Path(job.input_path).expanduser().resolve()
        output_path = Path(job.output_path).expanduser().resolve()
        if not input_path.is_file():
            raise WordRendererError(f"Input DOCX does not exist: {input_path}")
        if input_path.suffix.casefold() != ".docx":
            raise WordRendererError(f"Word resolves .docx input only: {input_path}")
        if output_path.suffix.casefold() != ".docx":
            raise WordRendererError(f"Word saves resolved output as .docx only: {output_path}")
        if not output_path.parent.is_dir():
            raise WordRendererError(f"Output directory does not exist: {output_path.parent}")
        # Word would overwrite silently (alerts are off): refuse instead, so
        # nothing the caller did not mean to replace is ever replaced.
        if output_path.exists():
            raise WordRendererError(f"Resolve output already exists: {output_path}")
        key = _normalized_windows_path(output_path)
        if key in outputs:
            raise WordRendererError(f"Two resolve jobs write {output_path}")
        outputs.add(key)
        inputs.add(_normalized_windows_path(input_path))
        validated.append(ResolveJob(input_path, output_path, job.action))
    if not validated:
        raise WordRendererError("Word resolve needs at least one job")
    if outputs & inputs:
        raise WordRendererError("A resolve job's output would overwrite an input")
    return tuple(validated)


def _record_count(value) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < -1:
        raise WordRendererError(f"invalid Word resolve result count: {value!r}")
    return value


def _load_resolution(
    result_path: Path,
    jobs: tuple[ResolveJob, ...],
) -> WordResolution:
    """Read the bridge's result file, trusting nothing it says without a
    check against the jobs it was given and the files on disk."""
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise WordRendererError(
            f"Word automation wrote no readable resolve result: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise WordRendererError("invalid Word resolve result: not an object")
    records = payload.get("jobs")
    if isinstance(records, dict):
        # Windows PowerShell 5.1 can serialize a one-element array as the
        # element itself.
        records = [records]
    if not isinstance(records, list) or len(records) != len(jobs):
        raise WordRendererError(
            "invalid Word resolve result: it does not answer every job"
        )
    documents: list[ResolvedDocx] = []
    for job, record in zip(jobs, records):
        if not isinstance(record, dict):
            raise WordRendererError("invalid Word resolve result: a job is not an object")
        if (
            _normalized_windows_path(str(record.get("input", "")))
            != _normalized_windows_path(job.input_path)
            or _normalized_windows_path(str(record.get("output", "")))
            != _normalized_windows_path(job.output_path)
            or record.get("action") != job.action
        ):
            raise WordRendererError(
                "invalid Word resolve result: it answers a job it was not given"
            )
        ok = record.get("ok")
        if not isinstance(ok, bool):
            raise WordRendererError("invalid Word resolve result: ok is not a boolean")
        error = record.get("error") or ""
        if not isinstance(error, str):
            raise WordRendererError("invalid Word resolve result: error is not text")
        authors = record.get("authors") or []
        if isinstance(authors, str):
            authors = [authors]
        if not isinstance(authors, list) or not all(
            isinstance(author, str) for author in authors
        ):
            raise WordRendererError("invalid Word resolve result: authors are not text")
        before = _record_count(record.get("revisions_before"))
        after = _record_count(record.get("revisions_after"))
        if ok:
            if before < 0 or after < 0 or error:
                raise WordRendererError(
                    "invalid Word resolve result: a finished job is incomplete"
                )
            if not job.output_path.is_file() or job.output_path.stat().st_size <= 0:
                raise WordRendererError(
                    f"Word reported {job.output_path.name} saved, but it is "
                    "missing or empty"
                )
        elif not error:
            raise WordRendererError(
                "invalid Word resolve result: a failed job carries no reason"
            )
        documents.append(
            ResolvedDocx(
                job=job,
                ok=ok,
                error=error,
                revisions_before=before,
                revisions_after=after,
                authors=tuple(sorted(set(authors))),
            )
        )
    return WordResolution(
        word_version=str(payload.get("word_version") or ""),
        word_build=str(payload.get("word_build") or ""),
        documents=tuple(documents),
    )


def resolve_docx(
    jobs: Sequence[ResolveJob],
    *,
    word_executable: Path | None = None,
    timeout_seconds: int | None = None,
    verbose: bool = False,
) -> WordResolution:
    """Have ONE owned, hidden Word resolve every job, in order.

    Each input is opened read-only and never added to Recent Files; Word
    runs Accept All Changes, Reject All Changes, or neither, then saves the
    result as the job's new output DOCX (Word's default format, also kept off
    Recent Files). A document Word cannot open, resolve or save fails only
    its own job (:attr:`ResolvedDocx.error`); a timeout, an ownership
    failure or anything else fails the batch — and every failure path cleans
    up the Word this process proved it started, never another.

    ``timeout_seconds`` bounds the whole batch. It defaults to the
    configured per-document timeout (``BUILD_A_SPEC_WORD_TIMEOUT``, 120 s)
    times the number of jobs: a ceiling for a hung Word, not a target.
    """
    validated = _validated_jobs(jobs)
    word_executable = word_executable or _configured_word_executable()
    timeout_seconds = timeout_seconds or _configured_word_timeout() * len(validated)
    with tempfile.TemporaryDirectory(prefix="build_a_spec_word_") as temp_dir:
        root = Path(temp_dir)
        jobs_path = root / "resolve-jobs.json"
        result_path = root / "resolve-result.json"
        jobs_path.write_text(
            json.dumps(
                {
                    "jobs": [
                        {
                            "input": str(job.input_path),
                            "output": str(job.output_path),
                            "action": job.action,
                        }
                        for job in validated
                    ]
                }
            ),
            encoding="utf-8",
        )
        environment = _resolve_environment(
            jobs_path,
            result_path,
            word_executable,
            root / "word-ownership.txt",
        )
        _run_owned_word(environment, timeout_seconds=timeout_seconds, verbose=verbose)
        return _load_resolution(result_path, validated)


def _resolve_main(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.output is None:
        parser.error("--resolve needs --output (the new DOCX to write)")
    job = ResolveJob(Path(args.input_path), Path(args.output), args.resolve)
    try:
        resolution = resolve_docx([job], verbose=args.verbose)
    except WordRendererError as exc:
        parser.exit(1, f"Word renderer error: {exc}\n")
    (document,) = resolution.documents
    if not document.ok:
        parser.exit(
            1,
            f"Word could not {args.resolve} {document.job.input_path.name}: "
            f"{document.error}\n",
        )
    authors = ", ".join(document.authors) or "no author"
    print(
        f"Word {resolution.word_build or resolution.word_version} read "
        f"{document.revisions_before} tracked change(s) by {authors}; "
        f"{document.revisions_after} left after '{args.resolve}'. "
        f"Saved {document.job.output_path}"
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.resolve is not None:
        _resolve_main(parser, args)
        return
    if args.output is not None:
        parser.error("--output is only used with --resolve")
    input_path = Path(args.input_path)
    output_dir = (
        Path(args.output_dir)
        if args.output_dir is not None
        else input_path.expanduser().resolve().with_suffix("")
    )
    try:
        render_docx(
            input_path,
            output_dir,
            dpi=args.dpi,
            emit_pdf=args.emit_pdf,
            verbose=args.verbose,
        )
    except WordRendererError as exc:
        parser.exit(1, f"Word renderer error: {exc}\n")
    print(f"Pages rendered to {output_dir.expanduser().resolve()}")


if __name__ == "__main__":
    main()
