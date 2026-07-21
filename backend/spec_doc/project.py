"""JSON project files: save/resume for one interview session.

A project file bundles the conversation history (including tool-use and
tool-result blocks, so the model resumes with full drafting context) and
the document store's complete version history (so undo still works after
a resume). The chat transcript the UI shows is re-derived from history on
load — only text blocks, in order.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

PROJECT_KIND = "buildaspec-project"
PROJECT_FORMAT = 1


def save_project(
    history: list[dict[str, Any]],
    store,
    module_id: str = "",
    requirements_profile: dict[str, Any] | None = None,
    audit_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "kind": PROJECT_KIND,
        "format": PROJECT_FORMAT,
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "module_id": module_id,
        "history": history,
        "doc": store.to_dict(),
    }
    if requirements_profile:
        payload["requirements_profile"] = requirements_profile
    if audit_result:
        payload["audit_result"] = audit_result
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
    if not isinstance(data, dict) or data.get("kind") != PROJECT_KIND:
        raise ValueError("Not a Build-a-Spec project file.")
    if data.get("format") != PROJECT_FORMAT:
        raise ValueError(
            f"Unsupported project format {data.get('format')!r} "
            f"(this build reads format {PROJECT_FORMAT})."
        )
    history = data.get("history")
    if not isinstance(history, list) or not all(
        isinstance(m, dict)
        and m.get("role") in ("user", "assistant")
        and isinstance(m.get("content"), list)
        and all(isinstance(block, dict) for block in m["content"])
        for m in history
    ):
        raise ValueError("Malformed conversation history.")
    doc_data = data.get("doc")
    if not isinstance(doc_data, dict):
        raise ValueError("Malformed document history.")

    # Validate the doc fully before mutating the session.
    from .model import DocumentStore

    staging = DocumentStore()
    staging.load(doc_data)  # raises ValueError on bad snapshots

    session.history.clear()
    session.history.extend(history)
    session.doc.load(doc_data)
    # Module resolution degrades to the default on unknown/missing ids —
    # the same posture as Spec Critic's registry (a file from a build with
    # more modules still opens; the lint/prompt basis is then the default
    # module's, which the standards block makes visible, never silent).
    from ..spec_modules import get_module

    session.module = get_module(data.get("module_id"))
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
    # The meter is per-session; a resumed project starts its own count (the
    # prior session's spend lives in that session's traces, not this file).
    session.usage.reset()
    # Invalidate any turn that was still streaming against the old state.
    session.generation += 1
