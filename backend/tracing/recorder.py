"""TraceRecorder — the tracing orchestrator.

Ported ≈verbatim from Claude-Spec-Critic ``src/tracing/recorder.py`` with
one subtraction: no ``findings.jsonl`` / ``record_finding_snapshot`` (the
Finding type belongs to the review pipeline; Build-a-Spec's terminal
artifacts are the project file and the export).

Owns the on-disk trace directory and a background writer thread. Public
methods (``open_span`` / ``close_span`` / ``add_event`` / ``prompt_ref``)
enqueue work; the writer drains, serializes to JSONL, and fsyncs on
``stop()``. Public methods are safe from any thread; only the writer
touches file handles; a ContextVar plus a thread-local stack carry the
active span so nested capture sites inherit their parent without plumbing.
Every failure path logs and continues — tracing must never sink the app.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import queue
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Iterator

from .config import LEVEL_DEEP, LEVEL_DEFAULT
from .redaction import scrub_data
from .spans import (
    STATUS_ERROR,
    STATUS_OK,
    AgentSpan,
    SpanHandle,
    make_event,
    make_span,
)

_log = logging.getLogger(__name__)

_CURRENT_SPAN: contextvars.ContextVar[SpanHandle | None] = contextvars.ContextVar(
    "build_a_spec_current_span", default=None
)

_THREAD_SPAN_STACK = threading.local()


def _stack() -> list[SpanHandle]:
    stack = getattr(_THREAD_SPAN_STACK, "spans", None)
    if stack is None:
        stack = []
        _THREAD_SPAN_STACK.spans = stack
    return stack


def current_span() -> SpanHandle | None:
    """Active SpanHandle: ContextVar first, thread-local stack fallback."""
    ctx_value = _CURRENT_SPAN.get()
    if ctx_value is not None:
        return ctx_value
    stack = _stack()
    return stack[-1] if stack else None


def bind_to_current_context(fn):
    """Wrap ``fn`` to run in a snapshot of the current context.

    ``ThreadPoolExecutor.submit`` does not propagate contextvars — wrap
    submitted callables so workers see the submitter's span.
    """
    ctx = contextvars.copy_context()

    def wrapper(*args, **kwargs):
        return ctx.run(fn, *args, **kwargs)

    wrapper.__name__ = getattr(fn, "__name__", "wrapped")
    wrapper.__doc__ = getattr(fn, "__doc__", None)
    return wrapper


_SHUTDOWN_SENTINEL = object()

FILE_SPANS = "spans.jsonl"
FILE_EVENTS = "events.jsonl"
FILE_PROMPTS = "prompts.jsonl"
FILE_RUN_META = "run.json"

_QUEUE_WARN_THRESHOLD = 100_000


class TraceRecorder:
    """One trace per ``run_id``; re-instantiating against the same dir appends."""

    def __init__(
        self,
        *,
        run_id: str,
        trace_dir: Path,
        capture_level: str,
        app_version: str = "",
    ) -> None:
        self._run_id = run_id
        self._trace_dir = Path(trace_dir)
        self._capture_level = (
            capture_level
            if capture_level in (LEVEL_DEFAULT, LEVEL_DEEP)
            else LEVEL_DEFAULT
        )
        self._app_version = app_version

        self._queue: queue.Queue = queue.Queue()
        self._writer_thread: threading.Thread | None = None
        self._writer_alive = threading.Event()
        self._stopped = threading.Event()

        self._open_spans: dict[str, AgentSpan] = {}
        self._open_spans_lock = threading.Lock()

        self._prompt_seen: set[str] = set()
        self._prompt_seen_lock = threading.Lock()

        self._queue_warned = False
        self._run_meta: dict[str, Any] = {}

    # ---- properties ----------------------------------------------------
    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def trace_dir(self) -> Path:
        return self._trace_dir

    @property
    def capture_level(self) -> str:
        return self._capture_level

    @property
    def is_deep(self) -> bool:
        return self._capture_level == LEVEL_DEEP

    # ---- lifecycle -----------------------------------------------------
    def start(self, *, model: str = "", module_id: str = "") -> None:
        """Spin up the writer thread and write the initial run.json.

        Safe to call again against the same dir — appends and records a
        ``resumed_at`` timestamp.
        """
        self._trace_dir.mkdir(parents=True, exist_ok=True)
        existing = self._read_existing_run_meta()
        now = time.time()
        if existing:
            self._run_meta = existing
            resumes = list(self._run_meta.get("resumed_at") or [])
            resumes.append(now)
            self._run_meta["resumed_at"] = resumes
            self._run_meta["capture_level"] = self._capture_level
        else:
            self._run_meta = {
                "run_id": self._run_id,
                "started_at": now,
                "ended_at": None,
                "model": model,
                "module_id": module_id,
                "capture_level": self._capture_level,
                "app_version": self._app_version,
                "resumed_at": [],
            }
        self._write_run_meta_sync()

        if self._writer_thread is None or not self._writer_thread.is_alive():
            self._writer_alive.set()
            self._stopped.clear()
            self._writer_thread = threading.Thread(
                target=self._writer_loop,
                name=f"build-a-spec-trace-writer-{self._run_id}",
                daemon=True,
            )
            self._writer_thread.start()

    def stop(self, *, flush_timeout: float = 5.0) -> None:
        """Drain the writer queue and close files. Idempotent."""
        if self._stopped.is_set():
            return
        self._stopped.set()
        self._run_meta["ended_at"] = time.time()
        try:
            self._write_run_meta_sync()
        except Exception as exc:  # noqa: BLE001
            _log.warning("Failed to update run.json on stop: %s", exc)

        self._queue.put(_SHUTDOWN_SENTINEL)
        if self._writer_thread is not None:
            self._writer_thread.join(timeout=flush_timeout)
            if self._writer_thread.is_alive():
                _log.warning(
                    "Trace writer did not drain within %.1fs; %d items remain",
                    flush_timeout,
                    self._queue.qsize(),
                )
        self._writer_alive.clear()

    # ---- public capture surface ----------------------------------------
    def open_span(
        self,
        kind: str,
        name: str,
        *,
        parent: SpanHandle | None = None,
        inputs: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SpanHandle:
        if parent is None:
            parent = current_span()
        span = make_span(
            kind=kind,
            name=name,
            run_id=self._run_id,
            parent_span_id=parent.span_id if parent else None,
            inputs=inputs,
            metadata=metadata,
        )
        with self._open_spans_lock:
            self._open_spans[span.span_id] = span
        handle = SpanHandle(
            span_id=span.span_id,
            kind=span.kind,
            started_at=span.started_at,
            parent_span_id=span.parent_span_id,
        )
        _stack().append(handle)
        return handle

    def close_span(
        self,
        handle: SpanHandle,
        *,
        outputs: dict[str, Any] | None = None,
        status: str = STATUS_OK,
        error: str | None = None,
    ) -> None:
        with self._open_spans_lock:
            span = self._open_spans.pop(handle.span_id, None)
        if span is None:
            _log.debug("close_span on unknown span_id=%s", handle.span_id)
            return
        span.ended_at = time.time()
        span.status = status
        span.error = error
        if outputs:
            span.outputs.update(outputs)
        # Lenient stack pop: prefer LIFO, tolerate out-of-order closes.
        stack = _stack()
        for i in range(len(stack) - 1, -1, -1):
            if stack[i].span_id == handle.span_id:
                stack.pop(i)
                break
        self._enqueue(FILE_SPANS, scrub_data(span.to_jsonl_dict()))

    def add_event(self, handle: SpanHandle | None, type: str, **fields: Any) -> None:
        """Append one event; ``handle=None`` tags the event with the run id."""
        span_id = handle.span_id if handle is not None else self._run_id
        event = make_event(span_id=span_id, type=type, fields=fields)
        self._enqueue(FILE_EVENTS, scrub_data(event))

    def prompt_ref(self, kind: str, text: str) -> dict[str, Any]:
        """Content-hash reference (default) or inline text (deep)."""
        if self._capture_level == LEVEL_DEEP:
            return {"inline": text}
        digest = hashlib.sha256(
            text.encode("utf-8", errors="replace")
        ).hexdigest()[:24]
        with self._prompt_seen_lock:
            already = digest in self._prompt_seen
            if not already:
                self._prompt_seen.add(digest)
        if not already:
            self._enqueue(FILE_PROMPTS, {"hash": digest, "kind": kind, "text": text})
        return {"ref": digest, "kind": kind}

    @contextmanager
    def span(
        self,
        kind: str,
        name: str,
        *,
        parent: SpanHandle | None = None,
        inputs: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Iterator[SpanHandle]:
        """Context manager that auto-closes the span and threads ContextVar."""
        handle = self.open_span(
            kind, name, parent=parent, inputs=inputs, metadata=metadata
        )
        token = _CURRENT_SPAN.set(handle)
        try:
            yield handle
        except Exception as exc:
            self.close_span(handle, status=STATUS_ERROR, error=str(exc))
            _CURRENT_SPAN.reset(token)
            raise
        else:
            self.close_span(handle, status=STATUS_OK)
            _CURRENT_SPAN.reset(token)

    # ---- internals -----------------------------------------------------
    def _enqueue(self, filename: str, payload: dict[str, Any]) -> None:
        if self._stopped.is_set():
            return
        self._queue.put((filename, payload))
        if not self._queue_warned and self._queue.qsize() > _QUEUE_WARN_THRESHOLD:
            self._queue_warned = True
            _log.warning(
                "Trace queue has %d pending writes — writer may be behind",
                self._queue.qsize(),
            )

    def _writer_loop(self) -> None:
        try:
            with self._open_writers() as writers:
                while True:
                    item = self._queue.get()
                    if item is _SHUTDOWN_SENTINEL:
                        break
                    filename, payload = item
                    writer = writers.get(filename)
                    if writer is None:
                        _log.debug("Unknown trace file %s; dropping line", filename)
                        continue
                    try:
                        line = json.dumps(
                            payload, ensure_ascii=False, default=_json_default
                        )
                        writer.write(line)
                        writer.write("\n")
                    except Exception as exc:  # noqa: BLE001
                        _log.warning(
                            "Failed to write trace line to %s: %s", filename, exc
                        )
        except Exception as exc:  # noqa: BLE001
            _log.error("Trace writer thread crashed: %s", exc, exc_info=True)
        finally:
            self._writer_alive.clear()

    @contextmanager
    def _open_writers(self) -> Iterator[dict[str, Any]]:
        handles: dict[str, Any] = {}
        try:
            handles[FILE_SPANS] = (self._trace_dir / FILE_SPANS).open(
                "a", encoding="utf-8"
            )
            handles[FILE_EVENTS] = (self._trace_dir / FILE_EVENTS).open(
                "a", encoding="utf-8"
            )
            if self._capture_level == LEVEL_DEFAULT:
                handles[FILE_PROMPTS] = (self._trace_dir / FILE_PROMPTS).open(
                    "a", encoding="utf-8"
                )
            yield handles
        finally:
            for fh in handles.values():
                try:
                    fh.flush()
                    import os

                    os.fsync(fh.fileno())
                except Exception:  # noqa: BLE001
                    pass
                try:
                    fh.close()
                except Exception:  # noqa: BLE001
                    pass

    def _read_existing_run_meta(self) -> dict[str, Any] | None:
        path = self._trace_dir / FILE_RUN_META
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            _log.debug("Could not parse existing run.json (%s); fresh start", exc)
            return None

    def _write_run_meta_sync(self) -> None:
        self._trace_dir.mkdir(parents=True, exist_ok=True)
        path = self._trace_dir / FILE_RUN_META
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(scrub_data(self._run_meta), indent=2), encoding="utf-8"
        )
        tmp.replace(path)


def _json_default(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    if isinstance(obj, (set, frozenset)):
        return sorted(obj)
    if isinstance(obj, Path):
        return str(obj)
    return repr(obj)


# ---- module-level recorder singleton -----------------------------------
_RECORDER: TraceRecorder | None = None
_RECORDER_LOCK = threading.Lock()


def get_recorder() -> TraceRecorder | None:
    return _RECORDER


def set_recorder(recorder: TraceRecorder | None) -> None:
    global _RECORDER
    with _RECORDER_LOCK:
        _RECORDER = recorder
