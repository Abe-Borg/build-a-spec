"""Semantic project payloads and legacy JSON save/resume compatibility.

A project file bundles the conversation history (including tool-use and
tool-result blocks, so the model resumes with full drafting context) and
the document store's complete version history (so undo still works after
a resume). Native ``.baspec`` packaging lives in ``project_package.py``; the
chat transcript the UI shows is re-derived from history on load—only text
blocks, in order.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

PROJECT_KIND = "buildaspec-project"
PROJECT_FORMAT = 1

MAX_SOURCE_DOCX_MAP_BYTES = 8 * 1024 * 1024
MAX_SOURCE_DOCX_MAP_DEPTH = 20
MAX_SOURCE_DOCX_MAP_NODES = 250_000
MAX_SOURCE_DOCX_MAP_KEY_CHARS = 256
MAX_SOURCE_DOCX_MAP_STRING_CHARS = 65_536

_EMBEDDED_SOURCE_KEYS = frozenset(
    {
        "sourcebytes",
        "sourcedocxbytes",
        "originalbytes",
        "sourcebase64",
        "sourcedocxbase64",
        "originalbase64",
    }
)


@dataclass(frozen=True)
class ValidatedProjectData:
    """Load-bearing project state validated without mutating a session."""

    project: dict[str, Any]
    history: list[dict[str, Any]]
    doc_data: dict[str, Any]
    import_report: dict[str, Any] | None
    source_map: dict[str, Any] | None


def sanitize_source_map(value: Any) -> dict[str, Any] | None:
    """Bound and clone optional source-to-OOXML locator metadata.

    Source-map details evolve independently from the format-1 semantic tree,
    so persistence deliberately validates JSON safety rather than interpreting
    locators.  Mapping-specific code must still validate source hashes,
    baseline digests, UID references, and locators before enabling a
    preservation export. Raw or base64 source bytes are forbidden here; they
    belong only in the fixed binary member of a ``.baspec`` package.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("Malformed source map.")

    nodes = 0
    active: set[int] = set()

    def clone(item: Any, depth: int) -> Any:
        nonlocal nodes
        nodes += 1
        if (
            nodes > MAX_SOURCE_DOCX_MAP_NODES
            or depth > MAX_SOURCE_DOCX_MAP_DEPTH
        ):
            raise ValueError("Source map exceeds its structural limits.")
        if item is None or isinstance(item, (str, bool, int)):
            if (
                isinstance(item, str)
                and len(item) > MAX_SOURCE_DOCX_MAP_STRING_CHARS
            ):
                raise ValueError("Source map contains an oversized string.")
            return item
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError("Source map contains a non-finite number.")
            return item
        if isinstance(item, bytes):
            raise ValueError("Source bytes cannot be embedded in project JSON.")
        if isinstance(item, (list, dict)):
            identity = id(item)
            if identity in active:
                raise ValueError("Source map contains a reference cycle.")
            active.add(identity)
            try:
                if isinstance(item, list):
                    return [clone(child, depth + 1) for child in item]
                result: dict[str, Any] = {}
                for key, child in item.items():
                    if (
                        not isinstance(key, str)
                        or not key
                        or len(key) > MAX_SOURCE_DOCX_MAP_KEY_CHARS
                        or any(ord(ch) < 32 or ord(ch) == 127 for ch in key)
                    ):
                        raise ValueError("Source map contains an invalid key.")
                    folded = re.sub(r"[^a-z0-9]", "", key.casefold())
                    if folded in _EMBEDDED_SOURCE_KEYS:
                        raise ValueError(
                            "Source bytes cannot be embedded in project JSON."
                        )
                    result[key] = clone(child, depth + 1)
                return result
            finally:
                active.remove(identity)
        raise ValueError("Source map contains unsupported data.")

    cloned = clone(value, 0)
    try:
        encoded = json.dumps(
            cloned, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:  # defensive
        raise ValueError("Malformed source map.") from exc
    if len(encoded) > MAX_SOURCE_DOCX_MAP_BYTES:
        raise ValueError("Source map exceeds its size limit.")
    # Mapping-specific validation is strict and canonicalizes away unknown
    # fields. Keeping this lazy avoids loading OOXML machinery for projects
    # that were authored from scratch and have no source map.
    from .source_mapping import SourceBodyMap

    try:
        return SourceBodyMap.from_dict(cloned).to_dict()
    except ValueError as exc:
        # Keep the compatibility boundary visible to callers. A project from
        # a newer build must fail closed as an unsupported format, rather than
        # looking like arbitrary corruption and inviting a lossy fallback.
        if str(exc) == "Unsupported source map format.":
            raise
        raise ValueError("Malformed source map.") from exc
    except (TypeError, KeyError) as exc:
        raise ValueError("Malformed source map.") from exc


def validate_project_data(data: Any) -> ValidatedProjectData:
    """Validate core project state and optional persistence metadata.

    This is the side-effect-free half of :func:`load_project`. Package code
    uses it before considering a source attachment, allowing callers to stage
    every check before committing a replacement session.
    """
    if not isinstance(data, dict) or data.get("kind") != PROJECT_KIND:
        raise ValueError("Not a Build-a-Spec project file.")
    if data.get("format") != PROJECT_FORMAT:
        raise ValueError(
            f"Unsupported project format {data.get('format')!r} "
            f"(this build reads format {PROJECT_FORMAT})."
        )
    history = data.get("history")
    if not isinstance(history, list) or not all(
        isinstance(message, dict)
        and message.get("role") in ("user", "assistant")
        and isinstance(message.get("content"), list)
        and all(isinstance(block, dict) for block in message["content"])
        for message in history
    ):
        raise ValueError("Malformed conversation history.")
    doc_data = data.get("doc")
    if not isinstance(doc_data, dict):
        raise ValueError("Malformed document history.")

    from .model import DocumentStore
    from .source_package import sanitize_import_report

    staging = DocumentStore()
    staging.load(doc_data)
    return ValidatedProjectData(
        project=data,
        history=history,
        doc_data=doc_data,
        import_report=sanitize_import_report(data.get("import_report")),
        source_map=sanitize_source_map(data.get("source_map")),
    )


def save_project(
    history: list[dict[str, Any]],
    store,
    module_id: str = "",
    requirements_profile: dict[str, Any] | None = None,
    audit_result: dict[str, Any] | None = None,
    qc_result: dict[str, Any] | None = None,
    qc_latest_attempt: dict[str, Any] | None = None,
    discipline: str = "",
    project_context: str = "",
    figures: dict[str, Any] | None = None,
    suggested_prompts: list[str] | None = None,
    import_report: dict[str, Any] | None = None,
    source_map: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "kind": PROJECT_KIND,
        "format": PROJECT_FORMAT,
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "module_id": module_id,
        "discipline": discipline,
        "project_context": project_context,
        "history": history,
        "doc": store.to_dict(),
    }
    if requirements_profile:
        payload["requirements_profile"] = requirements_profile
    if audit_result:
        payload["audit_result"] = audit_result
    if qc_result:
        payload["qc_result"] = qc_result
    if qc_latest_attempt:
        payload["qc_latest_attempt"] = qc_latest_attempt
    # Chat-authored figures ride the file the same way (optional field, no
    # format bump — old readers ignore it, new readers tolerate absence).
    if figures and figures.get("figures"):
        payload["figures"] = figures
    # Suggested-reply chips ride the file the same optional way (omitted when
    # empty, which is the common case once a section is finished).
    if suggested_prompts:
        payload["suggested_prompts"] = list(suggested_prompts)
    # The small, sanitized honesty trail stays in semantic JSON. Exact source
    # bytes live only in the separate binary member of a .baspec container.
    if import_report:
        from .source_package import sanitize_import_report

        safe_report = sanitize_import_report(import_report)
        if safe_report is not None:
            payload["import_report"] = safe_report
    if source_map is not None:
        safe_source_map = sanitize_source_map(source_map)
        if safe_source_map is not None:
            payload["source_map"] = safe_source_map
    return payload


def chat_transcript(history: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Reduce API-shaped history to displayable (role, text) turns.

    Tool plumbing (tool_use / tool_result blocks) is dropped, and the text
    on either side of a tool round merges into one assistant bubble —
    matching what the user saw stream in live.
    """
    transcript: list[dict[str, str]] = []
    for message in history:
        role = message.get("role")
        if role not in ("user", "assistant"):
            continue
        parts = [
            block.get("text", "")
            for block in (message.get("content") or [])
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        ]
        text = "\n\n".join(p for p in parts if p).strip()
        if not text:
            continue
        if transcript and transcript[-1]["role"] == role:
            transcript[-1]["text"] += "\n\n" + text
        else:
            transcript.append({"role": role, "text": text})
    return transcript


def load_project(data: Any, session) -> None:
    """Restore ``session`` (history + document store) from a project dict.

    Raises ``ValueError`` on anything malformed; the session is untouched
    unless the whole file validates.
    """
    staged = validate_project_data(data)
    history = staged.history
    doc_data = staged.doc_data
    restored_import_report = staged.import_report
    restored_source_docx_map = None
    if staged.source_map is not None:
        from .source_mapping import SourceBodyMap

        restored_source_docx_map = SourceBodyMap.from_dict(staged.source_map)

    # Parse and reconcile both QC records before touching the live session.
    # The retained successful result and the newer attempt form one state
    # machine; staging a complete runner also guarantees that an unexpected
    # parser failure cannot leave history/document from the incoming project
    # mixed with QC state from the old project.
    from ..qc import QCResult, QCRunner

    restored_qc_runner = QCRunner()
    restored_qc = QCResult.from_dict(data.get("qc_result"))
    if restored_qc is not None:
        restored_qc_runner.restore(restored_qc)
    restored_qc_runner.restore_attempt(data.get("qc_latest_attempt"))

    session.history.clear()
    session.history.extend(history)
    session.doc.load(doc_data)
    # Module resolution: a present-but-unknown id degrades to the current
    # default (the Spec Critic registry posture — a file from a build with more
    # modules still opens; the standards block makes the basis visible, never
    # silent). A MISSING/BLANK id, however, means a legacy file saved before
    # module ids existed, authored in the only module that then existed — the
    # fire module. Pin it explicitly so those files keep their original
    # prompt/standards/lint/research behavior even though the neutral default
    # is now the generic module.
    from ..spec_modules import get_module
    from ..spec_modules.hyperscale_fire import HYPERSCALE_FIRE

    raw_module_id = str(data.get("module_id") or "").strip()
    session.module = get_module(raw_module_id) if raw_module_id else HYPERSCALE_FIRE
    # Session discipline (Batch 10) rides beside module_id; sanitize the
    # untrusted string and enforce the invariant (non-empty only while an
    # open-catalog module is active — a curated module clears it). Old
    # files without the key degrade to "".
    from ..llm.prompts import sanitize_discipline, sanitize_project_context

    session.discipline = sanitize_discipline(str(data.get("discipline") or ""))
    if not getattr(session.module, "open_catalog", False):
        session.discipline = ""
    # Priming text rides beside discipline; applies to any module (not gated by
    # open_catalog). Old files without the key degrade to "".
    session.project_context = sanitize_project_context(
        str(data.get("project_context") or "")
    )
    # A completed research profile rides the project file; a malformed one
    # degrades to "not researched" rather than failing the load (the doc
    # and history are the load-bearing content).
    from ..compliance import AuditRunner
    from ..research import RequirementsProfile, ResearchRunner

    session.research = ResearchRunner()
    restored = RequirementsProfile.from_dict(data.get("requirements_profile"))
    if restored is not None:
        session.research.restore(restored)
    session.audit = AuditRunner()
    audit_result = data.get("audit_result")
    if isinstance(audit_result, dict) and audit_result.get("coverage"):
        session.audit.restore(audit_result)
    # A completed Final-QC result rides the project file the same way; a
    # malformed one degrades to "not run" rather than failing the load.
    session.qc = restored_qc_runner
    # Chat-authored figures ride the file too; a malformed block degrades to
    # "no figures" (load() resets then restores) rather than failing the load.
    session.figures.load(data.get("figures"))
    # Suggested-reply chips restore the same lenient way. Assign
    # UNCONDITIONALLY (load_project does not call session.reset()): loading
    # over a live session must not inherit the previous session's chips, so
    # an absent/malformed block resolves to [].
    from ..suggestions import restore_prompts

    session.suggested_prompts = restore_prompts(data.get("suggested_prompts"))
    # The meter is per-session; a resumed project starts its own count (the
    # prior session's spend lives in that session's traces, not this file).
    session.usage.reset()
    # Semantic/legacy JSON never contains source bytes. Clear them only after
    # the incoming project's load-bearing content validates. A .baspec caller
    # attaches its separately validated bytes after this semantic commit.
    session.source_docx_bytes = None
    session.source_docx_filename = ""
    if hasattr(session, "source_patch_context"):
        session.source_patch_context = None
    session.import_report = restored_import_report
    # Keep the assignment conditional so format-1 compatibility callers with
    # an older/lightweight session object continue to load unchanged.
    if hasattr(session, "source_docx_map"):
        session.source_docx_map = restored_source_docx_map
    # Invalidate any turn that was still streaming against the old state.
    session.invalidate_model_turn()
