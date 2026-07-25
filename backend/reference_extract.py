"""Turning an attached reference file into the text the model reads.

This is the one place a reference upload's bytes become text. ``.docx`` was
the first supported type and its extraction stays with the importer (it shares
the Accept-All tracked-changes resolution and table flattening a master import
needed); every other supported type is read here.

Supported kinds, and why each is read the way it is
--------------------------------------------------
``docx``  Word body text in document order with tracked changes resolved to
          the Accept-All view — delegated to
          :func:`backend.spec_doc.importer.extract_reference_text`, behind the
          same bounded ZIP/OPC inspection a master import gets, because an
          attachment is the same attack surface. Nothing is retained but the
          text.
``pdf``   Per-page text through ``pypdf`` (already a dependency — the research
          resend sanitizer counts pages with it). Pages carry a ``[page N]``
          marker so the model can tell the user where something came from. A
          PDF with no text layer at all is refused *with the reason* rather
          than attached as an empty document: silently accepting a scan the
          model cannot read is the one outcome worse than a clear error.
``txt``   Decoded and normalized, nothing else.
``csv`` / ``xml``
          Also decoded verbatim: for these two the structure *is* the content
          — a CSV's rows and an XML's tags are exactly what makes the file
          worth reading — so nothing is parsed away or flattened.

Everything here is pure CPU over the uploaded bytes and must run on a worker
thread; see the event-loop rule in ``backend/app.py``.

Token posture is unchanged and set by ``reference_docs``: extracted text is
bounded (``MAX_TEXT_CHARS``), never rides the per-turn PROJECT CONTEXT, and is
elided from committed history after the turn that read it. This module only
decides what the text *is*.
"""
from __future__ import annotations

import codecs
import io
import tempfile
from pathlib import Path

from .spec_doc.importer import ReferenceExtraction, extract_reference_text
from .spec_doc.source_package import (
    inspect_docx_package,
    sanitize_source_filename,
)

# Extension → kind. Insertion order is the order the supported types are
# listed to the user, so the rejection message can never drift from the set
# actually accepted.
REFERENCE_KINDS: dict[str, str] = {
    ".docx": "docx",
    ".pdf": "pdf",
    ".txt": "txt",
    ".xml": "xml",
    ".csv": "csv",
}

# Display names for the panel list and the model's context stub. The stub says
# what kind of file a reference is because it changes how the model should read
# it — a CSV is data, a PDF carries page markers, a Word standard is prose.
REFERENCE_KIND_LABELS: dict[str, str] = {
    "docx": "Word",
    "pdf": "PDF",
    "txt": "text",
    "xml": "XML",
    "csv": "CSV",
}

# Runaway breaker, not a quality limit (the MAX_TOOL_ROUNDS posture): the
# upload is already bounded at 25 MiB, but a text-only PDF that size can run
# to thousands of pages and text extraction is the slow part. Sized well past
# any real design standard; hitting it is reported, never silent.
MAX_PDF_PAGES = 1_000

# Runs of blank lines longer than this collapse. Blank lines are structure in a
# text file (paragraph breaks, record separators), so they are kept — but a
# file with fifty blank lines between sections would spend real tokens on
# nothing.
MAX_CONSECUTIVE_BLANK_LINES = 1

# C0 controls have no meaning in extracted reference text; tabs and newlines
# do. DEL goes too. Anything left would only confuse the model or the panel.
_CONTROL_TRANSLATION = {
    code: None
    for code in list(range(0x20)) + [0x7F]
    if code not in (0x09, 0x0A)
}


class ReferenceExtractError(ValueError):
    """The attachment could not be read as text.

    A ``ValueError``, like ``MasterImportError`` and ``SourcePackageError``, so
    the upload endpoint's existing 400 mapping covers every supported type.
    """


def supported_extensions_phrase() -> str:
    """``.docx, .pdf, .txt, .xml, or .csv`` — for user-facing messages."""
    extensions = list(REFERENCE_KINDS)
    return ", ".join(extensions[:-1]) + f", or {extensions[-1]}"


def reference_kind_for_filename(filename: str) -> str | None:
    """The kind an upload will be read as, or ``None`` when unsupported.

    Extension-based on purpose: the browser's reported content type is not
    trustworthy, and every extractor validates the bytes it actually gets
    (a ``.pdf`` that is not a PDF is refused by the PDF reader, a ``.docx``
    that is not a package by the OPC inspection).
    """
    lowered = (filename or "").lower()
    for extension, kind in REFERENCE_KINDS.items():
        if lowered.endswith(extension):
            return kind
    return None


def reference_extension_for_kind(kind: str) -> str:
    """``"pdf"`` → ``".pdf"``. Empty for an unknown kind."""
    for extension, known in REFERENCE_KINDS.items():
        if known == kind:
            return extension
    return ""


def sanitize_reference_filename(filename: str, *, kind: str) -> str:
    """A path-free display name that keeps the attachment's own type.

    The shared sanitizer defaults to the imported-master case and appends
    ``.docx`` to anything else, which would rename ``acme.pdf`` and then send
    it to the wrong extractor.
    """
    extension = reference_extension_for_kind(kind) or ".txt"
    return sanitize_source_filename(
        filename, extension=extension, fallback=f"attachment{extension}"
    )


def extract_reference_document(
    source_bytes: bytes, *, filename: str
) -> ReferenceExtraction:
    """Read an attached file's text. Raises :class:`ValueError` subclasses.

    Blocking: seconds of CPU on a long PDF or Word document. Callers must be
    on a worker thread.
    """
    kind = reference_kind_for_filename(filename)
    if kind is None:
        raise ReferenceExtractError(
            f"That file type is not supported as a reference. Attach a "
            f"{supported_extensions_phrase()} file."
        )
    if kind == "docx":
        return _extract_docx(source_bytes)
    if kind == "pdf":
        return _extract_pdf(source_bytes)
    return _extract_plain_text(source_bytes, kind=kind)


# ---------------------------------------------------------------------------
# Word
# ---------------------------------------------------------------------------


def _extract_docx(source_bytes: bytes) -> ReferenceExtraction:
    """The same package inspection a master import gets, then body text.

    Nothing is retained: a reference document has no source package, no
    preservation map, and no export path, so the bytes are dropped as soon as
    the text is out.
    """
    inspect_docx_package(source_bytes)
    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as handle:
        handle.write(source_bytes)
        temp_path = Path(handle.name)
    try:
        return extract_reference_text(temp_path)
    finally:
        temp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


def _pdf_reader(source_bytes: bytes):
    """Open a PDF, unlocking an owner-password-only file if possible."""
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(source_bytes), strict=False)
    except Exception as exc:  # noqa: BLE001 — pypdf raises broadly on junk
        raise ReferenceExtractError(
            "That file could not be read as a PDF."
        ) from exc
    try:
        encrypted = bool(reader.is_encrypted)
    except Exception:  # noqa: BLE001
        encrypted = False
    if encrypted:
        # A PDF restricted only by an owner password (print/copy permissions)
        # opens with an empty user password, which is the common case for a
        # circulated standard. A real user password cannot be guessed.
        try:
            unlocked = bool(reader.decrypt(""))
        except Exception:  # noqa: BLE001
            unlocked = False
        if not unlocked:
            raise ReferenceExtractError(
                "That PDF is password-protected, so its text cannot be read. "
                "Attach an unprotected copy."
            )
    return reader


def _extract_pdf(source_bytes: bytes) -> ReferenceExtraction:
    reader = _pdf_reader(source_bytes)
    try:
        total_pages = len(reader.pages)
    except Exception as exc:  # noqa: BLE001
        raise ReferenceExtractError(
            "That PDF's page structure could not be read."
        ) from exc

    read_pages = min(total_pages, MAX_PDF_PAGES)
    lines: list[str] = []
    block_count = 0
    empty_pages = 0
    failed_pages = 0
    for index in range(read_pages):
        try:
            page_text = reader.pages[index].extract_text() or ""
        except Exception:  # noqa: BLE001 — one bad page is not a bad file
            failed_pages += 1
            continue
        page_lines = [
            collapsed
            for collapsed in (" ".join(raw.split()) for raw in page_text.splitlines())
            if collapsed
        ]
        if not page_lines:
            empty_pages += 1
            continue
        # The page marker is why a PDF reference can be cited back to the user
        # as "page 12 of the owner's standard". It is not a text block.
        lines.append(f"[page {index + 1}]")
        lines.extend(page_lines)
        block_count += len(page_lines)

    if not block_count:
        raise ReferenceExtractError(
            "No text could be read from that PDF. A scanned PDF is images of "
            "pages with no text layer — run OCR on it, or attach the content "
            "in another format."
        )

    warnings: list[str] = []
    if total_pages > read_pages:
        skipped = total_pages - read_pages
        note = (
            f"[... {skipped:,} further page(s) of this PDF were not read "
            f"(limit {MAX_PDF_PAGES:,} pages).]"
        )
        lines.append(note)
        warnings.append(
            f"Only the first {read_pages:,} of {total_pages:,} pages were "
            f"read. The model is told the rest was not."
        )
    if failed_pages:
        warnings.append(
            f"{failed_pages} page(s) could not be decoded and were skipped."
        )
    if empty_pages:
        warnings.append(
            f"{empty_pages} of {read_pages:,} page(s) held no extractable "
            "text (scanned images or drawings)."
        )
    return ReferenceExtraction(
        text="\n".join(lines),
        block_count=block_count,
        tracked_changes=False,
        kind="pdf",
        warnings=tuple(warnings),
    )


# ---------------------------------------------------------------------------
# Plain text, CSV, XML
# ---------------------------------------------------------------------------


def _decode(source_bytes: bytes) -> tuple[str, str]:
    """Decode uploaded text, returning ``(text, encoding label)``.

    A ladder rather than a single attempt: Windows is the primary platform,
    where a hand-saved ``.txt`` or a spreadsheet's ``.csv`` export is often
    UTF-16 (Excel) or Windows-1252 rather than UTF-8. Latin-1 is the last
    resort because it cannot fail — a mojibake character beats refusing a
    file the user can plainly read.
    """
    if source_bytes.startswith(
        (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)
    ):
        try:
            return source_bytes.decode("utf-16"), "UTF-16"
        except UnicodeDecodeError:
            pass
    for encoding, label in (("utf-8-sig", "UTF-8"), ("cp1252", "Windows-1252")):
        try:
            return source_bytes.decode(encoding), label
        except UnicodeDecodeError:
            continue
    return source_bytes.decode("latin-1"), "Latin-1"


def _extract_plain_text(source_bytes: bytes, *, kind: str) -> ReferenceExtraction:
    text, encoding = _decode(source_bytes)
    if "\x00" in text:
        # Reached by a binary file renamed to .txt/.csv/.xml. The decode
        # ladder ends in Latin-1, which never raises, so this is the only
        # place binary content can be caught.
        raise ReferenceExtractError(
            f"That file does not contain readable text (it looks like "
            f"binary data with a .{kind} name)."
        )
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.translate(_CONTROL_TRANSLATION)

    lines: list[str] = []
    block_count = 0
    blank_run = 0
    for raw in text.split("\n"):
        line = raw.rstrip()
        if line.strip():
            blank_run = 0
            block_count += 1
            lines.append(line)
            continue
        blank_run += 1
        if blank_run <= MAX_CONSECUTIVE_BLANK_LINES and lines:
            lines.append("")
    while lines and not lines[-1]:
        lines.pop()

    warnings: list[str] = []
    if encoding != "UTF-8":
        warnings.append(
            f"The file was not UTF-8; it was read as {encoding}. Check the "
            "excerpt if it contains unusual characters."
        )
    return ReferenceExtraction(
        text="\n".join(lines),
        block_count=block_count,
        tracked_changes=False,
        kind=kind,
        warnings=tuple(warnings),
    )
