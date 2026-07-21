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


def save_project(history: list[dict[str, Any]], store) -> dict[str, Any]:
    return {
        "kind": PROJECT_KIND,
        "format": PROJECT_FORMAT,
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "history": history,
        "doc": store.to_dict(),
    }


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
    # Invalidate any turn that was still streaming against the old state.
    session.generation += 1
