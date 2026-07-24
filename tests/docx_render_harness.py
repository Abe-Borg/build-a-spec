"""Reusable optional DOCX renderer and same-renderer visual assertions.

The harness intentionally knows nothing about Build-a-Spec document semantics.
Tests provide DOCX bytes, while this module owns renderer configuration,
deterministic page discovery, raster comparison, and useful diagnostics.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Mapping


RENDERER_ENV = "BUILD_A_SPEC_DOCX_RENDERER"
RENDER_PYTHON_ENV = "BUILD_A_SPEC_RENDER_PYTHON"
RENDER_DPI_ENV = "BUILD_A_SPEC_RENDER_DPI"
RENDER_TIMEOUT_ENV = "BUILD_A_SPEC_RENDER_TIMEOUT"
RENDER_EXTRA_PATH_ENV = "BUILD_A_SPEC_RENDER_EXTRA_PATH"
RENDERER_SKIP_REASON = (
    "Set BUILD_A_SPEC_DOCX_RENDERER to a compatible documents-skill renderer "
    "entrypoint to run optional DOCX visual regressions"
)
_PAGE_NAME = re.compile(r"^page-(\d+)\.png$")


def renderer_is_configured(environ: Mapping[str, str] | None = None) -> bool:
    values = os.environ if environ is None else environ
    return bool(values.get(RENDERER_ENV, "").strip())


def _positive_int(raw: str, *, name: str, default: int) -> int:
    if not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise AssertionError(f"{name} must be a positive integer, got {raw!r}") from exc
    if value <= 0:
        raise AssertionError(f"{name} must be a positive integer, got {value}")
    return value


@dataclass(frozen=True)
class RendererConfig:
    script: Path
    python: str
    dpi: int = 144
    timeout_seconds: int = 240
    emit_pdf: bool = True
    extra_path: tuple[str, ...] = ()

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "RendererConfig":
        values = os.environ if environ is None else environ
        raw_script = values.get(RENDERER_ENV, "").strip()
        if not raw_script:
            raise AssertionError(RENDERER_SKIP_REASON)
        script = Path(raw_script).expanduser().resolve()
        if not script.is_file():
            raise AssertionError(f"Configured DOCX renderer does not exist: {script}")

        render_python = values.get(RENDER_PYTHON_ENV, "").strip() or sys.executable
        dpi = _positive_int(
            values.get(RENDER_DPI_ENV, ""),
            name=RENDER_DPI_ENV,
            default=144,
        )
        timeout = _positive_int(
            values.get(RENDER_TIMEOUT_ENV, ""),
            name=RENDER_TIMEOUT_ENV,
            default=240,
        )

        extra_path = [
            entry
            for entry in values.get(RENDER_EXTRA_PATH_ENV, "").split(os.pathsep)
            if entry
        ]
        # The bundled Windows runtime keeps Poppler DLLs beside the native
        # package rather than the Python executable.  Add that directory when
        # present; this is harmless for repo-local or non-Windows runtimes.
        python_path = Path(render_python).expanduser()
        try:
            dependencies = python_path.resolve().parent.parent
            bundled_poppler = (
                dependencies / "native" / "poppler" / "Library" / "bin"
            )
            if bundled_poppler.is_dir():
                extra_path.append(str(bundled_poppler))
        except OSError:
            pass

        return cls(
            script=script,
            python=render_python,
            dpi=dpi,
            timeout_seconds=timeout,
            extra_path=tuple(dict.fromkeys(extra_path)),
        )


@dataclass(frozen=True)
class RenderedDocx:
    docx_path: Path
    output_dir: Path
    pages: tuple[Path, ...]
    pdf_path: Path | None


@dataclass(frozen=True)
class PageDiff:
    page_number: int
    before_path: Path
    after_path: Path
    size: tuple[int, int]
    bbox: tuple[int, int, int, int] | None
    changed_pixels: int

    @property
    def changed(self) -> bool:
        return self.bbox is not None

    @property
    def changed_pixel_fraction(self) -> float:
        width, height = self.size
        return self.changed_pixels / (width * height)

    @property
    def bbox_area_fraction(self) -> float:
        if self.bbox is None:
            return 0.0
        left, top, right, bottom = self.bbox
        width, height = self.size
        return ((right - left) * (bottom - top)) / (width * height)


@dataclass(frozen=True)
class VisualComparison:
    pages: tuple[PageDiff, ...]

    @property
    def changed_page_numbers(self) -> tuple[int, ...]:
        return tuple(page.page_number for page in self.pages if page.changed)

    def assert_changed_pages(self, expected: set[int]) -> None:
        actual = set(self.changed_page_numbers)
        if actual != expected:
            raise AssertionError(
                f"Expected visual changes on pages {sorted(expected)}, "
                f"found {sorted(actual)}"
            )

    def assert_page_furniture_unchanged(
        self,
        *,
        top_fraction: float = 0.10,
        bottom_fraction: float = 0.10,
    ) -> None:
        if not 0 < top_fraction < 0.5 or not 0 < bottom_fraction < 0.5:
            raise ValueError("page-furniture bands must be between 0 and 0.5")
        image, image_chops = _pillow_modules()
        for page in self.pages:
            width, height = page.size
            top_end = int(height * top_fraction)
            bottom_start = int(height * (1 - bottom_fraction))
            with image.open(page.before_path) as before_source:
                before = before_source.convert("RGB")
            with image.open(page.after_path) as after_source:
                after = after_source.convert("RGB")
            try:
                top_diff = image_chops.difference(
                    before.crop((0, 0, width, top_end)),
                    after.crop((0, 0, width, top_end)),
                ).getbbox()
                bottom_diff = image_chops.difference(
                    before.crop((0, bottom_start, width, height)),
                    after.crop((0, bottom_start, width, height)),
                ).getbbox()
            finally:
                before.close()
                after.close()
            if top_diff is not None or bottom_diff is not None:
                raise AssertionError(
                    f"Page furniture changed on page {page.page_number}: "
                    f"top={top_diff}, bottom={bottom_diff}"
                )

    def assert_changes_within_body(
        self,
        *,
        top_fraction: float = 0.10,
        bottom_fraction: float = 0.90,
    ) -> None:
        for page in self.pages:
            if page.bbox is None:
                continue
            _left, top, _right, bottom = page.bbox
            _width, height = page.size
            if top < int(height * top_fraction) or bottom > int(
                height * bottom_fraction
            ):
                raise AssertionError(
                    f"Visual change escaped the body band on page "
                    f"{page.page_number}: bbox={page.bbox}, size={page.size}"
                )

    def assert_diff_budget(
        self,
        *,
        max_changed_pixel_fraction: float,
        max_bbox_area_fraction: float | None = None,
    ) -> None:
        for page in self.pages:
            if page.changed_pixel_fraction > max_changed_pixel_fraction:
                raise AssertionError(
                    f"Page {page.page_number} changed-pixel fraction "
                    f"{page.changed_pixel_fraction:.5f} exceeds "
                    f"{max_changed_pixel_fraction:.5f}; bbox={page.bbox}"
                )
            if (
                max_bbox_area_fraction is not None
                and page.bbox_area_fraction > max_bbox_area_fraction
            ):
                raise AssertionError(
                    f"Page {page.page_number} diff-bbox fraction "
                    f"{page.bbox_area_fraction:.5f} exceeds "
                    f"{max_bbox_area_fraction:.5f}; bbox={page.bbox}"
                )


class DocxRenderHarness:
    def __init__(self, config: RendererConfig):
        self.config = config

    @classmethod
    def from_environment(cls) -> "DocxRenderHarness":
        return cls(RendererConfig.from_environment())

    def _environment(self) -> dict[str, str]:
        environment = dict(os.environ)
        if self.config.extra_path:
            current = environment.get("PATH", "")
            environment["PATH"] = os.pathsep.join(
                [*self.config.extra_path, current]
            )
        return environment

    def render_bytes(
        self,
        payload: bytes,
        *,
        work_dir: Path,
        stem: str,
    ) -> RenderedDocx:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", stem):
            raise ValueError(f"Unsafe render stem: {stem!r}")
        work_dir.mkdir(parents=True, exist_ok=True)
        docx_path = work_dir / f"{stem}.docx"
        output_dir = work_dir / f"{stem}-render"
        if output_dir.exists() and any(output_dir.iterdir()):
            raise AssertionError(f"Render output directory is not empty: {output_dir}")
        output_dir.mkdir(parents=True, exist_ok=True)
        docx_path.write_bytes(payload)

        command = [
            self.config.python,
            str(self.config.script),
            str(docx_path),
            "--output_dir",
            str(output_dir),
            "--dpi",
            str(self.config.dpi),
        ]
        if self.config.emit_pdf:
            command.append("--emit_pdf")
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.config.timeout_seconds,
                check=False,
                env=self._environment(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AssertionError(
                f"DOCX renderer failed to start or timed out for {docx_path}: {exc}"
            ) from exc
        diagnostics = "\n".join(
            part for part in (completed.stdout, completed.stderr) if part
        )
        if completed.returncode != 0:
            raise AssertionError(
                f"DOCX renderer exited {completed.returncode} for {docx_path}\n"
                f"{diagnostics}"
            )

        numbered_pages: list[tuple[int, Path]] = []
        for candidate in output_dir.glob("page-*.png"):
            match = _PAGE_NAME.match(candidate.name)
            if match:
                numbered_pages.append((int(match.group(1)), candidate))
        numbered_pages.sort(key=lambda item: item[0])
        page_numbers = [number for number, _path in numbered_pages]
        expected_numbers = list(range(1, len(numbered_pages) + 1))
        if not numbered_pages or page_numbers != expected_numbers:
            raise AssertionError(
                f"Renderer did not produce a contiguous page set for {docx_path}: "
                f"{page_numbers}; output:\n{diagnostics}"
            )

        pdf_path = output_dir / f"{stem}.pdf"
        if self.config.emit_pdf and not pdf_path.is_file():
            raise AssertionError(
                f"Renderer did not retain the requested PDF for {docx_path}"
            )
        return RenderedDocx(
            docx_path=docx_path,
            output_dir=output_dir,
            pages=tuple(path for _number, path in numbered_pages),
            pdf_path=pdf_path if self.config.emit_pdf else None,
        )

    def compare(
        self,
        before: RenderedDocx,
        after: RenderedDocx,
    ) -> VisualComparison:
        if len(before.pages) != len(after.pages):
            raise AssertionError(
                f"Rendered page count changed: {len(before.pages)} -> "
                f"{len(after.pages)}"
            )
        image, image_chops = _pillow_modules()
        comparisons: list[PageDiff] = []
        for page_number, (before_path, after_path) in enumerate(
            zip(before.pages, after.pages),
            start=1,
        ):
            with image.open(before_path) as before_source:
                before_image = before_source.convert("RGB")
            with image.open(after_path) as after_source:
                after_image = after_source.convert("RGB")
            try:
                if after_image.size != before_image.size:
                    raise AssertionError(
                        f"Rendered page {page_number} geometry changed: "
                        f"{before_image.size} -> {after_image.size}"
                    )
                difference = image_chops.difference(before_image, after_image)
                bbox = difference.getbbox()
                histogram = difference.convert("L").histogram()
                changed_pixels = sum(histogram[1:])
                comparisons.append(
                    PageDiff(
                        page_number=page_number,
                        before_path=before_path,
                        after_path=after_path,
                        size=before_image.size,
                        bbox=bbox,
                        changed_pixels=changed_pixels,
                    )
                )
            finally:
                before_image.close()
                after_image.close()
        return VisualComparison(tuple(comparisons))

    @staticmethod
    def assert_pages_have_ink(
        rendered: RenderedDocx,
        *,
        minimum_fraction: float = 0.0005,
    ) -> None:
        image, _image_chops = _pillow_modules()
        for page_number, page_path in enumerate(rendered.pages, start=1):
            with image.open(page_path) as source:
                grayscale = source.convert("L")
            try:
                histogram = grayscale.histogram()
                nonwhite = sum(histogram[:250])
                fraction = nonwhite / (grayscale.width * grayscale.height)
            finally:
                grayscale.close()
            if fraction < minimum_fraction:
                raise AssertionError(
                    f"Rendered page {page_number} appears blank: "
                    f"ink fraction {fraction:.6f}"
                )

    @staticmethod
    def extract_pdf_text(rendered: RenderedDocx) -> str:
        if rendered.pdf_path is None:
            raise AssertionError("PDF text extraction requires emit_pdf=True")
        try:
            from pypdf import PdfReader
        except ImportError as exc:  # pragma: no cover - optional renderer gate
            raise AssertionError("pypdf is required for rendered PDF assertions") from exc
        reader = PdfReader(str(rendered.pdf_path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)


def _pillow_modules():
    try:
        from PIL import Image, ImageChops
    except ImportError as exc:  # pragma: no cover - optional renderer gate
        raise AssertionError("Pillow is required for DOCX raster comparisons") from exc
    return Image, ImageChops


__all__ = [
    "DocxRenderHarness",
    "PageDiff",
    "RenderedDocx",
    "RendererConfig",
    "RENDERER_SKIP_REASON",
    "VisualComparison",
    "renderer_is_configured",
]
