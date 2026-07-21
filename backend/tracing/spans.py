"""Span and event types for the tracing subsystem.

Ported ≈verbatim from Claude-Spec-Critic ``src/tracing/spans.py``; the
span-kind vocabulary is Build-a-Spec's surfaces (turn / tool dispatch /
research / audit / import) instead of the review pipeline's. Spans nest
via ``parent_span_id``; events stream to ``events.jsonl`` as they fire and
join back by ``span_id``.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

_SPAN_ID_LEN = 12


def new_span_id() -> str:
    return uuid.uuid4().hex[:_SPAN_ID_LEN]


# ---- Span kinds (Build-a-Spec surfaces) --------------------------------
KIND_SESSION = "session"
KIND_TURN = "turn"
KIND_API_CALL = "api_call"
KIND_TOOL_DISPATCH = "tool_dispatch"
KIND_RESEARCH = "research"
KIND_RESEARCH_DIMENSION = "research_dimension"
KIND_COMPLIANCE = "compliance"
KIND_QC = "qc"
KIND_IMPORT = "import"

# ---- Event types -------------------------------------------------------
EVENT_STREAM_CHUNK = "stream_chunk"  # deep level only
EVENT_TOOL_USE = "tool_use"
EVENT_DOC_PATCH = "doc_patch"
EVENT_RETRY = "retry"
EVENT_PAUSE_TURN = "pause_turn"
EVENT_RESEARCH_PROGRESS = "research_progress"
EVENT_NOTE = "note"

# ---- Span status -------------------------------------------------------
STATUS_RUNNING = "running"
STATUS_OK = "ok"
STATUS_ERROR = "error"


@dataclass
class AgentSpan:
    """One logical agent invocation (see the source module's contract)."""

    span_id: str
    kind: str
    name: str
    run_id: str
    started_at: float
    parent_span_id: str | None = None
    ended_at: float | None = None
    status: str = STATUS_RUNNING
    error: str | None = None
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_jsonl_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "run_id": self.run_id,
            "kind": self.kind,
            "name": self.name,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "status": self.status,
            "error": self.error,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "metadata": self.metadata,
        }


@dataclass
class SpanHandle:
    """Lightweight reference; recorder methods look up the span internally."""

    span_id: str
    kind: str
    started_at: float
    parent_span_id: str | None = None


def make_span(
    *,
    kind: str,
    name: str,
    run_id: str,
    parent_span_id: str | None = None,
    inputs: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> AgentSpan:
    return AgentSpan(
        span_id=new_span_id(),
        kind=kind,
        name=name,
        run_id=run_id,
        started_at=time.time(),
        parent_span_id=parent_span_id,
        inputs=dict(inputs or {}),
        metadata=dict(metadata or {}),
    )


def make_event(
    *, span_id: str, type: str, fields: dict[str, Any] | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ts": time.time(),
        "span_id": span_id,
        "type": type,
    }
    if fields:
        payload.update(fields)
    return payload
