"""Reusable semantic specification templates.

Templates intentionally contain one canonical :class:`SpecSection` and a
small amount of drafting-basis metadata.  They are not project backups and
never contain chat, research, QC, references, source Word packages, or usage.
"""
from __future__ import annotations

import copy
import json
import os
import re
import threading
import time
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .app_paths import template_library_dir
from .spec_doc.model import SpecSection, iter_paragraphs
from .spec_modules import AVAILABLE_MODULES, DEFAULT_MODULE

TEMPLATE_KIND = "buildaspec-spec-template"
TEMPLATE_FORMAT = 1
TEMPLATE_MEDIA_TYPE = "application/vnd.buildaspec.template+json"
TEMPLATE_EXTENSION = ".bastemplate"
MAX_TEMPLATE_BYTES = 16 * 1024 * 1024
MAX_PERSONAL_TEMPLATES = 200
MAX_PREVIEW_CACHE_BYTES = 32 * 1024 * 1024
PREVIEW_TTL_SECONDS = 15 * 60
MAX_NAME_CHARS = 80
MAX_DESCRIPTION_CHARS = 240

_TOP_LEVEL_FIELDS = {
    "kind",
    "format",
    "id",
    "name",
    "description",
    "created_at",
    "updated_at",
    "module_id",
    "document",
}
_PERSONAL_ID_RE = re.compile(r"personal:[0-9a-f]{32}\Z")
_CURATED_ID_RE = re.compile(r"curated:[a-z0-9][a-z0-9-]{0,79}\Z")


class TemplateError(ValueError):
    """A safe user-facing template validation or catalog failure."""


class TemplateNotFoundError(TemplateError):
    pass


class TemplateImmutableError(TemplateError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise TemplateError(f"Duplicate JSON field {key!r}.")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise TemplateError(f"Non-finite JSON value {value!r} is not allowed.")


def _parse_json_bytes(data: bytes) -> dict[str, Any]:
    if len(data) > MAX_TEMPLATE_BYTES:
        raise TemplateError(
            f"Template exceeds the {MAX_TEMPLATE_BYTES // (1024 * 1024)} MiB limit."
        )
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TemplateError("Template must be UTF-8 JSON.") from exc
    try:
        parsed = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except TemplateError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError, RecursionError) as exc:
        raise TemplateError("Template is not valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise TemplateError("Template root must be a JSON object.")
    return parsed


def _read_path_bytes(path: Path) -> bytes:
    """Read one template without ever allocating beyond its public limit."""
    try:
        if path.stat().st_size > MAX_TEMPLATE_BYTES:
            raise TemplateError(
                f"Template exceeds the {MAX_TEMPLATE_BYTES // (1024 * 1024)} MiB limit."
            )
        with path.open("rb") as handle:
            data = handle.read(MAX_TEMPLATE_BYTES + 1)
    except OSError as exc:
        raise TemplateError("Template could not be read.") from exc
    if len(data) > MAX_TEMPLATE_BYTES:
        raise TemplateError(
            f"Template exceeds the {MAX_TEMPLATE_BYTES // (1024 * 1024)} MiB limit."
        )
    return data


def _clean_line(value: Any, *, field: str, limit: int, required: bool) -> str:
    if not isinstance(value, str):
        raise TemplateError(f"{field} must be text.")
    normalized = unicodedata.normalize("NFC", value)
    if "\n" in normalized or "\r" in normalized:
        raise TemplateError(f"{field} must fit on one line.")
    normalized = " ".join(normalized.split())
    if required and not normalized:
        raise TemplateError(f"{field} is required.")
    if len(normalized) > limit:
        raise TemplateError(f"{field} must be {limit} characters or fewer.")
    return normalized


def _validate_timestamp(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise TemplateError(f"{field} must be an ISO-8601 timestamp.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TemplateError(f"{field} must be an ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None:
        raise TemplateError(f"{field} must include a timezone.")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def _canonical_document(raw: Any, *, rebase_statuses: bool) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise TemplateError("document must be a specification object.")
    try:
        section = SpecSection.from_dict(copy.deepcopy(raw))
    except (ValueError, TypeError, RecursionError) as exc:
        raise TemplateError(str(exc)) from exc
    if not section.has_body_content():
        raise TemplateError("A template needs a section heading or body content.")

    # Project-specific decision state is never a reusable drafting basis.
    section.project_profile = {}
    section.edition_overrides = {}
    section.suppressed_standards = {}
    for _part, _article, paragraph, _depth, _ref in iter_paragraphs(section):
        paragraph.source_item_id = ""
        if rebase_statuses and paragraph.status != "needs_input":
            paragraph.status = "imported"
    canonical = section.to_dict()
    # Round-trip once more so canonicalization itself cannot forge counters.
    SpecSection.from_dict(canonical)
    return canonical


def canonical_template(
    raw: dict[str, Any],
    *,
    expected_source: str | None = None,
    rebase_statuses: bool = False,
) -> dict[str, Any]:
    unknown = set(raw) - _TOP_LEVEL_FIELDS
    missing = _TOP_LEVEL_FIELDS - set(raw)
    if unknown or missing:
        detail = []
        if unknown:
            detail.append(f"unknown fields: {sorted(unknown)}")
        if missing:
            detail.append(f"missing fields: {sorted(missing)}")
        raise TemplateError("Malformed template (" + "; ".join(detail) + ").")
    if raw.get("kind") != TEMPLATE_KIND:
        raise TemplateError("Not a Build-a-Spec specification template.")
    if raw.get("format") != TEMPLATE_FORMAT:
        raise TemplateError(
            f"Unsupported template format {raw.get('format')!r}; this build reads format {TEMPLATE_FORMAT}."
        )
    template_id = raw.get("id")
    if not isinstance(template_id, str):
        raise TemplateError("Template id is malformed.")
    if expected_source == "personal" and not _PERSONAL_ID_RE.fullmatch(template_id):
        raise TemplateError("Personal template id is malformed.")
    if expected_source == "curated" and not _CURATED_ID_RE.fullmatch(template_id):
        raise TemplateError("Curated template id is malformed.")
    if expected_source is None and not (
        _PERSONAL_ID_RE.fullmatch(template_id)
        or _CURATED_ID_RE.fullmatch(template_id)
    ):
        raise TemplateError("Template id is malformed.")
    name = _clean_line(
        raw.get("name"), field="Template name", limit=MAX_NAME_CHARS, required=True
    )
    description = _clean_line(
        raw.get("description"),
        field="Template description",
        limit=MAX_DESCRIPTION_CHARS,
        required=False,
    )
    module_id = raw.get("module_id")
    if (
        not isinstance(module_id, str)
        or not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", module_id)
    ):
        raise TemplateError("module_id is malformed.")
    created_at = _validate_timestamp(raw.get("created_at"), "created_at")
    updated_at = _validate_timestamp(raw.get("updated_at"), "updated_at")
    if updated_at < created_at:
        raise TemplateError("updated_at cannot be earlier than created_at.")
    canonical_document = _canonical_document(
        raw.get("document"), rebase_statuses=rebase_statuses
    )
    if not rebase_statuses and raw.get("document") != canonical_document:
        raise TemplateError(
            "Template document is not canonical or contains unsupported fields."
        )
    return {
        "kind": TEMPLATE_KIND,
        "format": TEMPLATE_FORMAT,
        "id": template_id,
        "name": name,
        "description": description,
        "created_at": created_at,
        "updated_at": updated_at,
        "module_id": module_id.strip(),
        "document": canonical_document,
    }


def template_bytes(template: dict[str, Any]) -> bytes:
    payload = (
        json.dumps(template, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    ).encode("utf-8")
    if len(payload) > MAX_TEMPLATE_BYTES:
        raise TemplateError(
            f"Template exceeds the {MAX_TEMPLATE_BYTES // (1024 * 1024)} MiB limit."
        )
    return payload


def _article_count(section: SpecSection) -> int:
    return sum(len(part.articles) for part in section.parts)


def _paragraph_ids(section: SpecSection) -> list[str]:
    return [p.uid for _part, _article, p, _depth, _ref in iter_paragraphs(section)]


def template_summary(template: dict[str, Any], source: str) -> dict[str, Any]:
    section = SpecSection.from_dict(template["document"])
    identity = section.project_identity
    paragraph_ids = _paragraph_ids(section)
    module_id = template["module_id"]
    return {
        "id": template["id"],
        "source": source,
        "editable": source == "personal",
        "name": template["name"],
        "description": template["description"],
        "module_id": module_id,
        "module_available": module_id in AVAILABLE_MODULES,
        "discipline": identity.get("discipline", ""),
        "project_type": identity.get("project_type", ""),
        "section_number": section.number,
        "section_title": section.title,
        "article_count": _article_count(section),
        "paragraph_count": len(paragraph_ids),
        "updated_at": template["updated_at"],
        "preview": "\n".join(
            " ".join(p.text.split())[:120]
            for _pt, _a, p, _d, _r in iter_paragraphs(section)
            if p.text.strip()
        )[:362]
        or section.title,
    }


@dataclass(frozen=True)
class TemplateList:
    templates: list[dict[str, Any]]
    invalid_personal_count: int


class TemplateCatalog:
    """Thread-safe curated + personal template catalog."""

    def __init__(
        self,
        *,
        personal_root: Path | None = None,
        curated_root: Path | None = None,
    ) -> None:
        self.personal_root = (personal_root or template_library_dir()).resolve()
        self.personal_root.mkdir(parents=True, exist_ok=True)
        self.curated_root = curated_root or (
            Path(__file__).resolve().parent / "templates" / "curated"
        )
        self._lock = threading.RLock()
        self._previews: dict[str, dict[str, Any]] = {}
        self._validate_curated()

    def _validate_curated(self) -> None:
        if not self.curated_root.exists():
            return
        seen: set[str] = set()
        for path in sorted(self.curated_root.glob(f"*{TEMPLATE_EXTENSION}")):
            template = canonical_template(
                _parse_json_bytes(_read_path_bytes(path)), expected_source="curated"
            )
            if template["id"] in seen:
                raise RuntimeError(f"Duplicate curated template id {template['id']!r}.")
            seen.add(template["id"])

    def _personal_path(self, template_id: str) -> Path:
        if not _PERSONAL_ID_RE.fullmatch(template_id):
            raise TemplateNotFoundError("Template not found.")
        filename = template_id.split(":", 1)[1] + TEMPLATE_EXTENSION
        path = self.personal_root / filename
        resolved_parent = path.parent.resolve()
        if resolved_parent != self.personal_root:
            raise TemplateError("Template path escaped the library.")
        if path.is_symlink():
            raise TemplateError("Symbolic links are not allowed in the template library.")
        return path

    def _iter_curated(self) -> Iterable[dict[str, Any]]:
        if not self.curated_root.exists():
            return []
        return [
            canonical_template(
                _parse_json_bytes(_read_path_bytes(path)), expected_source="curated"
            )
            for path in sorted(self.curated_root.glob(f"*{TEMPLATE_EXTENSION}"))
        ]

    def _iter_personal(self) -> tuple[list[dict[str, Any]], int]:
        templates: list[dict[str, Any]] = []
        invalid = 0
        for path in sorted(self.personal_root.glob(f"*{TEMPLATE_EXTENSION}")):
            try:
                if path.is_symlink():
                    raise TemplateError("Symbolic link.")
                template = canonical_template(
                    _parse_json_bytes(_read_path_bytes(path)), expected_source="personal"
                )
                if self._personal_path(template["id"]) != path:
                    raise TemplateError("Template id does not match its filename.")
                templates.append(template)
            except (OSError, TemplateError):
                invalid += 1
        return templates, invalid

    def list(self) -> TemplateList:
        with self._lock:
            curated = list(self._iter_curated())
            personal_summaries: list[dict[str, Any]] = []
            invalid = 0
            for path in sorted(self.personal_root.glob(f"*{TEMPLATE_EXTENSION}")):
                try:
                    if path.is_symlink():
                        raise TemplateError("Symbolic link.")
                    item = canonical_template(
                        _parse_json_bytes(_read_path_bytes(path)),
                        expected_source="personal",
                    )
                    if self._personal_path(item["id"]) != path:
                        raise TemplateError("Template id does not match its filename.")
                    personal_summaries.append(template_summary(item, "personal"))
                except (OSError, TemplateError, RecursionError):
                    invalid += 1
            personal_summaries.sort(
                key=lambda item: (item["name"].casefold(), item["id"])
            )
            return TemplateList(
                [
                    *(template_summary(item, "curated") for item in curated),
                    *personal_summaries,
                ],
                invalid,
            )

    def get(self, template_id: str) -> tuple[dict[str, Any], str]:
        with self._lock:
            if _CURATED_ID_RE.fullmatch(template_id):
                for item in self._iter_curated():
                    if item["id"] == template_id:
                        return item, "curated"
                raise TemplateNotFoundError("Template not found.")
            path = self._personal_path(template_id)
            if not path.is_file():
                raise TemplateNotFoundError("Template not found.")
            return (
                canonical_template(
                    _parse_json_bytes(_read_path_bytes(path)), expected_source="personal"
                ),
                "personal",
            )

    def preview(
        self,
        section: SpecSection,
        *,
        name: str,
        description: str = "",
        module_id: str,
        document_override: dict[str, Any] | None = None,
        binding: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        clean_name = _clean_line(
            name, field="Template name", limit=MAX_NAME_CHARS, required=True
        )
        clean_description = _clean_line(
            description,
            field="Template description",
            limit=MAX_DESCRIPTION_CHARS,
            required=False,
        )
        created = _now()
        raw = {
            "kind": TEMPLATE_KIND,
            "format": TEMPLATE_FORMAT,
            "id": "personal:" + uuid.uuid4().hex,
            "name": clean_name,
            "description": clean_description,
            "created_at": created,
            "updated_at": created,
            "module_id": module_id,
            "document": (
                document_override
                if document_override is not None
                else section.to_dict()
            ),
        }
        template = canonical_template(
            raw, expected_source="personal", rebase_statuses=True
        )
        token = uuid.uuid4().hex
        with self._lock:
            now = time.monotonic()
            for old_token, record in list(self._previews.items()):
                if now - float(record.get("created", now)) > PREVIEW_TTL_SECONDS:
                    self._previews.pop(old_token, None)
            payload_size = len(template_bytes(template))
            while self._previews and (
                len(self._previews) >= 16
                or sum(int(item.get("size", 0)) for item in self._previews.values())
                + payload_size
                > MAX_PREVIEW_CACHE_BYTES
            ):
                self._previews.pop(next(iter(self._previews)))
            # Validate the write-size bound before making the preview
            # confirmable; commit cannot later fail merely because of size.
            template_bytes(template)
            self._previews[token] = {
                "template": template,
                "binding": copy.deepcopy(binding or {}),
                "created": now,
                "size": payload_size,
            }
        return token, copy.deepcopy(template)

    def _atomic_write(self, path: Path, payload: bytes) -> None:
        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temp.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        except OSError as exc:
            raise TemplateError(
                "The template could not be saved atomically. Check available disk space and permissions."
            ) from exc
        finally:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass

    def commit_preview(
        self, token: str, *, binding: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        with self._lock:
            record = self._previews.pop(token, None)
            if record is None:
                raise TemplateError("Template preview expired; create a new preview.")
            if record["binding"] != (binding or {}):
                raise TemplateError(
                    "The project changed after this preview; create a fresh preview."
                )
            template = record["template"]
            existing_count = sum(
                1
                for path in self.personal_root.glob(f"*{TEMPLATE_EXTENSION}")
                if path.is_file() and not path.is_symlink()
            )
            if existing_count >= MAX_PERSONAL_TEMPLATES:
                raise TemplateError(
                    f"The personal template library is limited to {MAX_PERSONAL_TEMPLATES} templates."
                )
            path = self._personal_path(template["id"])
            self._atomic_write(path, template_bytes(template))
            return template_summary(template, "personal")

    def import_bytes(self, data: bytes) -> dict[str, Any]:
        parsed = _parse_json_bytes(data)
        strict = canonical_template(parsed, rebase_statuses=False)
        incoming = canonical_template(strict, rebase_statuses=True)
        with self._lock:
            existing_count = sum(
                1
                for path in self.personal_root.glob(f"*{TEMPLATE_EXTENSION}")
                if path.is_file() and not path.is_symlink()
            )
            if existing_count >= MAX_PERSONAL_TEMPLATES:
                raise TemplateError(
                    f"The personal template library is limited to {MAX_PERSONAL_TEMPLATES} templates."
                )
            incoming["id"] = "personal:" + uuid.uuid4().hex
            # Import creates a new local library object. Rebase both times so
            # a valid portable template authored in a future clock domain
            # cannot be written with updated_at earlier than created_at.
            imported_at = _now()
            incoming["created_at"] = imported_at
            incoming["updated_at"] = imported_at
            path = self._personal_path(incoming["id"])
            self._atomic_write(path, template_bytes(incoming))
            return template_summary(incoming, "personal")

    def update(self, template_id: str, *, name: Any, description: Any) -> dict[str, Any]:
        if _CURATED_ID_RE.fullmatch(template_id):
            raise TemplateImmutableError("Built-in templates cannot be changed.")
        with self._lock:
            template, _source = self.get(template_id)
            template["name"] = _clean_line(
                name, field="Template name", limit=MAX_NAME_CHARS, required=True
            )
            template["description"] = _clean_line(
                description,
                field="Template description",
                limit=MAX_DESCRIPTION_CHARS,
                required=False,
            )
            template["updated_at"] = _now()
            path = self._personal_path(template_id)
            self._atomic_write(path, template_bytes(template))
            return template_summary(template, "personal")

    def delete(self, template_id: str) -> None:
        if _CURATED_ID_RE.fullmatch(template_id):
            raise TemplateImmutableError("Built-in templates cannot be deleted.")
        with self._lock:
            path = self._personal_path(template_id)
            if not path.is_file():
                raise TemplateNotFoundError("Template not found.")
            path.unlink()

    def export(self, template_id: str) -> tuple[bytes, str]:
        template, _source = self.get(template_id)
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", template["name"]).strip("-.")
        return template_bytes(template), (safe or "buildaspec-template") + TEMPLATE_EXTENSION

    def instantiate(self, template_id: str, session: Any) -> dict[str, Any]:
        template, source = self.get(template_id)
        return self._instantiate_template(template, source, session)

    def instantiate_preview(
        self,
        token: str,
        session: Any,
        *,
        binding: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Instantiate a validated preview without writing it to the library."""
        with self._lock:
            record = self._previews.pop(token, None)
            if record is None:
                raise TemplateError("Template preview expired; create a new preview.")
            if record["binding"] != (binding or {}):
                raise TemplateError(
                    "The project changed after this preview; create a fresh preview."
                )
            template = record["template"]
        return self._instantiate_template(template, "personal", session)

    @staticmethod
    def _instantiate_template(
        template: dict[str, Any], source: str, session: Any
    ) -> dict[str, Any]:
        section = SpecSection.from_dict(copy.deepcopy(template["document"]))
        requested_module = template["module_id"]
        module = AVAILABLE_MODULES.get(requested_module, DEFAULT_MODULE)
        warning = ""
        if requested_module and requested_module not in AVAILABLE_MODULES:
            warning = (
                f"Template module {requested_module!r} is not installed; "
                f"using {DEFAULT_MODULE.display_name}."
            )
        seed_ids = _paragraph_ids(section)
        session.start_from_template(
            section,
            module_id=module.module_id,
            template_id=template["id"],
            template_name=template["name"],
            seed_block_ids=seed_ids,
        )
        return {
            "template": template_summary(template, source),
            "warning": warning,
            "template_origin": copy.deepcopy(session.template_origin),
        }


_CATALOG: TemplateCatalog | None = None
_CATALOG_LOCK = threading.Lock()


def get_template_catalog() -> TemplateCatalog:
    global _CATALOG
    with _CATALOG_LOCK:
        if _CATALOG is None:
            _CATALOG = TemplateCatalog()
        return _CATALOG


def reset_template_catalog_for_tests() -> None:
    global _CATALOG
    with _CATALOG_LOCK:
        _CATALOG = None
