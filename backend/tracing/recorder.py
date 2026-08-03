"""TraceRecorder — the tracing orchestrator.

Ported ≈verbatim from Claude-Spec-Critic ``src/tracing/recorder.py`` with
one subtraction: no ``findings.jsonl`` / ``record_finding_snapshot`` (the
Finding type belongs to the review pipeline; Build-a-Spec's terminal
artifacts are the project file and the export).

Owns the on-disk trace directory and a background writer thread. Public
methods (``open_span`` / ``close_span`` / ``add_event`` / ``prompt_ref``)
enqueue work; the writer drains, serializes to JSONL, flushes each line
(so a hard crash loses at most the line in flight), and fsyncs on
``stop()``. Public methods are safe from any thread; only the writer
touches file handles; a ContextVar carries the active span so ``span()``
nesting inherits its parent without plumbing. The source's thread-local
span stack is deliberately removed: Build-a-Spec's capture hooks open
spans on request threads and close them from daemon threads, where a
per-thread stack never pops and later spans on a reused threadpool
thread would inherit a stale parent — every hook passes its handle
explicitly instead. Every failure path logs and continues — tracing must
never sink the app.
"""
from __future__ import annotations

import contextvars
import copy
import hashlib
import json
import logging
import queue
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .config import LEVEL_DEEP, LEVEL_DEFAULT
from .redaction import redact_text, scrub_data
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

def current_span() -> SpanHandle | None:
    """Active SpanHandle from the ContextVar (set only by ``span()``)."""
    return _CURRENT_SPAN.get()


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


class _FlushBarrier:
    """Queue marker: set once every record enqueued before it is on disk."""

    __slots__ = ("event", "metadata_revision", "summary", "success")

    def __init__(
        self, summary: dict[str, Any], metadata_revision: int
    ) -> None:
        self.event = threading.Event()
        self.metadata_revision = metadata_revision
        self.summary = summary
        self.success = False

FILE_SPANS = "spans.jsonl"
FILE_EVENTS = "events.jsonl"
FILE_PROMPTS = "prompts.jsonl"
FILE_RUN_META = "run.json"

DEFAULT_TRACE_QUEUE_MAX_RECORDS = 10_000
DEFAULT_TRACE_QUEUE_MAX_BYTES = 32 * 1024 * 1024
_QUEUE_WARN_FRACTION = 0.8
_TRACE_SCHEMA_VERSION = 2
_META_CHECKPOINT_RECORDS = 100
_META_CHECKPOINT_SECONDS = 2.0
_TURN_USAGE_KEYS = frozenset(
    {
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_creation_1h_input_tokens",
        "cache_read_input_tokens",
        "thinking_tokens",
        "web_search_requests",
        "web_fetch_requests",
        "estimated_output_tokens",
    }
)
_FAILURE_EVENT_TYPES = frozenset({"client_error", "workspace_conflict"})
_FAILURE_STATUS_VALUES = frozenset({"error", "failed", "failure"})
_FAILURE_COUNT_FIELDS = frozenset(
    {
        "failure_count",
        "failures",
        "metadata_write_failures",
        "write_failures",
    }
)


class TraceRecorder:
    """One trace per ``run_id``; re-instantiating against the same dir appends."""

    def __init__(
        self,
        *,
        run_id: str,
        trace_dir: Path,
        capture_level: str,
        app_version: str = "",
        queue_max_records: int = DEFAULT_TRACE_QUEUE_MAX_RECORDS,
        queue_max_bytes: int = DEFAULT_TRACE_QUEUE_MAX_BYTES,
        max_run_bytes: int = 0,
    ) -> None:
        self._run_id = run_id
        self._trace_dir = Path(trace_dir)
        self._capture_level = (
            capture_level
            if capture_level in (LEVEL_DEFAULT, LEVEL_DEEP)
            else LEVEL_DEFAULT
        )
        self._app_version = app_version
        self._queue_max_records = max(1, int(queue_max_records))
        self._max_run_bytes = max(0, int(max_run_bytes))
        requested_queue_bytes = max(1, int(queue_max_bytes))
        self._queue_max_bytes = (
            min(requested_queue_bytes, self._max_run_bytes)
            if self._max_run_bytes > 0
            else requested_queue_bytes
        )
        self._data_bytes_written = 0

        # Data records are logically capped by count and serialized bytes.
        # Two tiny physical slots are reserved for one coalesced flush barrier
        # plus the shutdown sentinel, so saturation cannot deadlock controls.
        self._queue: queue.Queue = queue.Queue(
            maxsize=self._queue_max_records + 2
        )
        self._queued_data_records = 0
        self._queued_payload_bytes = 0
        self._control_lock = threading.Lock()
        self._outstanding_barrier: _FlushBarrier | None = None
        self._writer_thread: threading.Thread | None = None
        self._writer_alive = threading.Event()
        self._stopped = threading.Event()

        # One lock establishes enqueue/sequence/summary order.  A diagnostic
        # flush puts its barrier under this same lock, so the run.json summary
        # written at that barrier describes every preceding JSONL record.
        self._record_lock = threading.Lock()
        self._run_meta_write_lock = threading.Lock()
        self._record_seq = 0
        self._process_instance_id = ""
        self._summary = _empty_summary()
        # Every summary/self-health mutation advances this revision. Metadata
        # writers snapshot and acknowledge a specific revision so a mutation
        # that races a disk write remains dirty for the next idle checkpoint.
        self._metadata_revision = 0
        self._checkpointed_metadata_revision = 0
        self._writer_health = _empty_writer_health(
            queue_max_records=self._queue_max_records,
            queue_max_bytes=self._queue_max_bytes,
            max_run_bytes=self._max_run_bytes,
        )

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

    def writer_health(self) -> dict[str, Any]:
        """Return a bounded in-memory recorder-health snapshot."""
        with self._record_lock:
            return self._writer_health_snapshot_locked()

    # ---- lifecycle -----------------------------------------------------
    def start(
        self,
        *,
        model: str = "",
        module_id: str = "",
        environment: dict[str, Any] | None = None,
    ) -> None:
        """Spin up the writer thread and write the initial run.json.

        Safe to call again against the same dir — appends and records a
        ``resumed_at`` timestamp. ``environment`` (platform/python/frozen/
        models…) rides run.json so a trace identifies the machine that
        produced it without cross-referencing anything.
        """
        self._trace_dir.mkdir(parents=True, exist_ok=True)
        existing = self._read_existing_run_meta()
        now = time.time()
        self._process_instance_id = uuid.uuid4().hex
        self._data_bytes_written = self._existing_data_bytes()
        self._queue_warned = False
        self._metadata_revision = 1
        self._checkpointed_metadata_revision = 0
        self._writer_health = _empty_writer_health(
            state="starting",
            queue_max_records=self._queue_max_records,
            queue_max_bytes=self._queue_max_bytes,
            max_run_bytes=self._max_run_bytes,
            data_bytes_written=self._data_bytes_written,
        )
        if (
            self._max_run_bytes > 0
            and self._data_bytes_written >= self._max_run_bytes
        ):
            self._writer_health["storage_limit_reached"] = True
            self._writer_health["last_drop_reason"] = "run_byte_limit"
        if existing and isinstance(existing.get("summary"), dict):
            self._summary = _load_summary(existing["summary"])
        else:
            self._summary = _empty_summary(
                coverage="partial_since_upgrade" if existing else "complete"
            )
        self._record_seq = int(self._summary.get("records_enqueued") or 0)
        if existing:
            self._run_meta = existing
            resumes = list(self._run_meta.get("resumed_at") or [])
            resumes.append(now)
            self._run_meta["resumed_at"] = resumes
            self._run_meta["capture_level"] = self._capture_level
            self._run_meta["ended_at"] = None
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
        self._run_meta["trace_schema_version"] = _TRACE_SCHEMA_VERSION
        self._run_meta["process_instance_id"] = self._process_instance_id
        if environment is not None:
            self._run_meta["environment"] = dict(environment)
        instances = self._run_meta.get("process_instances")
        if not isinstance(instances, list):
            instances = []
        if existing and not instances:
            # Preserve enough correlation for a pre-schema-v2 lifetime.
            instances.append(
                {
                    "process_instance_id": "legacy",
                    "started_at": existing.get("started_at"),
                    "ended_at": existing.get("ended_at"),
                    "legacy": True,
                }
            )
        instance: dict[str, Any] = {
            "process_instance_id": self._process_instance_id,
            "started_at": now,
            "ended_at": None,
        }
        if environment and isinstance(environment.get("pid"), int):
            instance["pid"] = environment["pid"]
        instances.append(instance)
        self._run_meta["process_instances"] = instances
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
            with self._record_lock:
                if self._writer_health.get("state") != "failed":
                    self._writer_health["state"] = "running"
                    self._mark_metadata_dirty_locked()

    def stop(self, *, flush_timeout: float = 5.0) -> None:
        """Drain the writer queue and close files. Idempotent."""
        timeout = max(0.0, float(flush_timeout))
        with self._control_lock:
            with self._record_lock:
                if not self._stopped.is_set():
                    self._stopped.set()
                    ended_at = time.time()
                    self._run_meta["ended_at"] = ended_at
                    for instance in reversed(
                        self._run_meta.get("process_instances") or []
                    ):
                        if (
                            isinstance(instance, dict)
                            and instance.get("process_instance_id")
                            == self._process_instance_id
                        ):
                            instance["ended_at"] = ended_at
                            break
                    self._mark_metadata_dirty_locked()
                    if self._writer_health.get("state") != "failed":
                        self._writer_health["state"] = "draining"
                        self._mark_metadata_dirty_locked()
                    if (
                        self._writer_thread is not None
                        and self._writer_thread.is_alive()
                        and self._writer_alive.is_set()
                    ):
                        try:
                            self._queue.put_nowait(_SHUTDOWN_SENTINEL)
                        except queue.Full:
                            # The two reserved control slots make this an
                            # invariant violation, never a reason to block.
                            self._writer_health["state"] = "failed"
                            self._writer_health["fatal_error"] = (
                                "shutdown_control_queue_full"
                            )
                            self._mark_metadata_dirty_locked()
        if self._writer_thread is not None:
            self._writer_thread.join(timeout=timeout)
            if self._writer_thread.is_alive():
                drain_still_active = False
                with self._record_lock:
                    if self._writer_alive.is_set():
                        self._writer_health["drain_timed_out"] = True
                        self._writer_health["state"] = "drain_timeout"
                        self._mark_metadata_dirty_locked()
                        drain_still_active = True
                if drain_still_active:
                    _log.warning(
                        "Trace writer did not drain within %.1fs; %d items remain",
                        timeout,
                        self._queued_data_records,
                    )
        writer_still_alive = bool(
            self._writer_thread is not None and self._writer_thread.is_alive()
        )
        if not writer_still_alive:
            self._writer_alive.clear()
        with self._record_lock:
            if (
                not writer_still_alive
                and self._writer_health["state"] != "failed"
            ):
                self._writer_health["state"] = "stopped"
                self._mark_metadata_dirty_locked()
        try:
            self._write_run_meta_sync()
        except Exception as exc:  # noqa: BLE001
            _log.warning("Failed to update run.json on stop: %s", exc)

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
        with self._record_lock:
            self._mark_metadata_dirty_locked()
        return SpanHandle(
            span_id=span.span_id,
            kind=span.kind,
            started_at=span.started_at,
            parent_span_id=span.parent_span_id,
        )

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
        with self._record_lock:
            self._mark_metadata_dirty_locked()
        span.ended_at = time.time()
        span.status = status
        span.error = error
        if outputs:
            span.outputs.update(outputs)
        self._enqueue(FILE_SPANS, scrub_data(span.to_jsonl_dict()))

    def open_span_summaries(self) -> list[dict[str, Any]]:
        """Cheap snapshot of spans still open — the live-activity view."""
        with self._open_spans_lock:
            return [
                {
                    "span_id": span.span_id,
                    "kind": span.kind,
                    "name": span.name,
                    "started_at": span.started_at,
                    "parent_span_id": span.parent_span_id,
                }
                for span in self._open_spans.values()
            ]

    def add_event(self, handle: SpanHandle | None, type: str, **fields: Any) -> None:
        """Append one event; ``handle=None`` tags the event with the run id."""
        span_id = handle.span_id if handle is not None else self._run_id
        event = make_event(span_id=span_id, type=type, fields=fields)
        self._enqueue(FILE_EVENTS, scrub_data(event))

    def flush(self, timeout: float = 2.0) -> bool:
        """Wait until everything enqueued so far is written and flushed.

        The barrier the diagnostics bundle takes before copying the live
        run — without it a bundle grabbed right after an incident can miss
        the very events that explain it (they'd still be in the queue).
        Returns False on timeout or a dead writer; callers proceed either
        way (a partial record beats no record).
        """
        deadline = time.monotonic() + max(0.0, float(timeout))
        while True:
            if self._stopped.is_set():
                return self._terminal_flush_success()
            if (
                self._writer_thread is None
                or not self._writer_thread.is_alive()
            ):
                try:
                    self._write_run_meta_sync()
                except Exception:  # noqa: BLE001
                    pass
                return False

            pending: _FlushBarrier | None = None
            barrier: _FlushBarrier | None = None
            with self._control_lock:
                if self._stopped.is_set():
                    return self._terminal_flush_success()
                if (
                    self._writer_thread is None
                    or not self._writer_thread.is_alive()
                ):
                    return False
                outstanding = self._outstanding_barrier
                if outstanding is not None and not outstanding.event.is_set():
                    pending = outstanding
                else:
                    with self._record_lock:
                        barrier = _FlushBarrier(
                            copy.deepcopy(self._summary),
                            self._metadata_revision,
                        )
                        try:
                            self._queue.put_nowait(barrier)
                        except queue.Full:
                            # At most data_limit + one barrier can exist here;
                            # the second control slot is reserved for stop.
                            self._writer_health["fatal_error"] = (
                                "flush_control_queue_full"
                            )
                            self._mark_metadata_dirty_locked()
                            return False
                        self._outstanding_barrier = barrier

            remaining = max(0.0, deadline - time.monotonic())
            if pending is not None:
                if not pending.event.wait(remaining):
                    return False
                # The completed barrier predates this flush call's latest
                # records. Loop and install a fresh snapshot barrier.
                continue
            if barrier is None:
                return False
            if not barrier.event.wait(remaining):
                return False
            with self._control_lock:
                if self._outstanding_barrier is barrier:
                    self._outstanding_barrier = None
            return barrier.success

    def _terminal_flush_success(self) -> bool:
        """A stopped flag alone is not proof that its drain completed."""
        with self._record_lock:
            terminal = (
                not self._writer_alive.is_set()
                and self._writer_health.get("state") == "stopped"
                and self._queued_data_records == 0
                and self._queue.empty()
            )
        if not terminal:
            return False
        try:
            self._write_run_meta_sync()
        except Exception:  # noqa: BLE001
            return False
        return True

    def prompt_ref(self, kind: str, text: str) -> dict[str, Any]:
        """Content-hash reference (default) or inline text (deep).

        Credential-shaped substrings are redacted BEFORE hashing/storing:
        prompts carry whatever the user pasted into chat, and this is the
        one write path ``scrub_data`` does not cover (deliberately — its
        whole-string replacement would erase the entire prompt over one
        pasted key). Redact-then-hash keeps dedupe consistent with the
        stored text.
        """
        text = redact_text(text)
        if self._capture_level == LEVEL_DEEP:
            return {"inline": text}
        digest = hashlib.sha256(
            # ``replace`` conflates a lone surrogate with a literal ``?``.
            # ``surrogatepass`` gives every Python string a stable distinct
            # byte representation while JSONL stores the code point escaped.
            text.encode("utf-8", errors="surrogatepass")
        ).hexdigest()[:24]
        stored = False
        with self._prompt_seen_lock:
            if digest in self._prompt_seen:
                stored = True
            elif self._enqueue(
                FILE_PROMPTS, {"hash": digest, "kind": kind, "text": text}
            ):
                # A prompt is only considered stored after its write was
                # accepted. If backpressure rejects it, a later reference can
                # retry instead of permanently pointing at a missing record.
                self._prompt_seen.add(digest)
                stored = True
        return {"ref": digest, "kind": kind, "stored": stored}

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
    def _mark_metadata_dirty_locked(self) -> None:
        """Advance the in-memory metadata revision under ``_record_lock``."""
        self._metadata_revision += 1

    def _writer_health_snapshot_locked(
        self,
        *,
        thread_alive_override: bool | None = None,
        persisted_revision: int | None = None,
    ) -> dict[str, Any]:
        """Build recorder health while the caller holds ``_record_lock``."""
        writer_health = copy.deepcopy(self._writer_health)
        writer_health["queue_depth"] = self._queued_data_records
        writer_health["queue_payload_bytes"] = self._queued_payload_bytes
        writer_health["resident_payload_bytes"] = (
            self._queued_payload_bytes
            + int(writer_health.get("inflight_payload_bytes") or 0)
        )
        writer_health["control_queue_depth"] = max(
            0, self._queue.qsize() - self._queued_data_records
        )
        writer_health["thread_alive"] = (
            self._writer_alive.is_set()
            if thread_alive_override is None
            else bool(thread_alive_override)
        )
        with self._open_spans_lock:
            open_by_kind: dict[str, int] = {}
            for span in self._open_spans.values():
                _increment(open_by_kind, span.kind)
            writer_health["open_spans"] = len(self._open_spans)
            writer_health["open_spans_by_kind"] = open_by_kind
        checkpointed = (
            self._checkpointed_metadata_revision
            if persisted_revision is None
            else persisted_revision
        )
        writer_health["metadata_revision"] = self._metadata_revision
        writer_health["metadata_checkpointed_revision"] = checkpointed
        writer_health["metadata_dirty"] = (
            self._metadata_revision > checkpointed
        )
        writer_health["captured_at"] = time.time()
        return writer_health

    def _enqueue(self, filename: str, payload: dict[str, Any]) -> bool:
        should_warn = False
        queue_size = 0
        queue_bytes = 0
        with self._record_lock:
            if self._stopped.is_set():
                return False
            if (
                self._max_run_bytes > 0
                and self._data_bytes_written >= self._max_run_bytes
            ):
                self._writer_health["storage_limit_reached"] = True
                self._record_drop_locked(
                    "run_byte_limit", "run_byte_limit_drops"
                )
                return False
            if self._queued_data_records >= self._queue_max_records:
                self._record_drop_locked(
                    "queue_record_limit", "queue_count_drops"
                )
                return False
            # Distinct from domain-specific IDs such as a Final-QC run_id.
            record = dict(payload)
            next_sequence = self._record_seq + 1
            record["trace_run_id"] = self._run_id
            record["process_instance_id"] = self._process_instance_id
            record["record_seq"] = next_sequence
            try:
                line = json.dumps(
                    record,
                    ensure_ascii=True,
                    allow_nan=False,
                    default=_json_default,
                )
            except Exception:  # noqa: BLE001 - capture remains fail-open
                self._record_drop_locked(
                    "serialization_failure", "serialization_failures"
                )
                return False
            payload_bytes = len(line.encode("utf-8")) + 1
            projected_run_bytes = (
                self._data_bytes_written
                + self._queued_payload_bytes
                + int(self._writer_health.get("inflight_payload_bytes") or 0)
                + payload_bytes
            )
            if (
                self._max_run_bytes > 0
                and projected_run_bytes > self._max_run_bytes
            ):
                self._writer_health["storage_limit_reached"] = True
                self._record_drop_locked(
                    "run_byte_limit", "run_byte_limit_drops"
                )
                return False
            if (
                payload_bytes > self._queue_max_bytes
                or self._queued_payload_bytes
                + int(self._writer_health.get("inflight_payload_bytes") or 0)
                + payload_bytes
                > self._queue_max_bytes
            ):
                self._record_drop_locked(
                    "queue_byte_limit", "queue_byte_drops"
                )
                return False
            try:
                self._queue.put_nowait((filename, line, payload_bytes))
            except queue.Full:
                self._record_drop_locked(
                    "queue_record_limit", "queue_count_drops"
                )
                return False
            self._record_seq = next_sequence
            self._queued_data_records += 1
            self._queued_payload_bytes += payload_bytes
            self._update_summary_locked(filename, record)
            queue_size = self._queued_data_records
            queue_bytes = self._queued_payload_bytes + int(
                self._writer_health.get("inflight_payload_bytes") or 0
            )
            self._writer_health["queue_high_watermark"] = max(
                int(self._writer_health.get("queue_high_watermark") or 0),
                queue_size,
            )
            self._writer_health["queue_payload_high_watermark"] = max(
                int(
                    self._writer_health.get("queue_payload_high_watermark")
                    or 0
                ),
                queue_bytes,
            )
            self._mark_metadata_dirty_locked()
            should_warn = (
                not self._queue_warned
                and (
                    queue_size
                    >= max(
                        1,
                        int(
                            self._queue_max_records * _QUEUE_WARN_FRACTION
                        ),
                    )
                    or queue_bytes
                    >= max(
                        1,
                        int(self._queue_max_bytes * _QUEUE_WARN_FRACTION),
                    )
                )
            )
            if should_warn:
                self._queue_warned = True
        if should_warn:
            _log.warning(
                "Trace queue has %d pending writes (%d bytes) — writer may "
                "be behind",
                queue_size,
                queue_bytes,
            )
        return True

    def _record_drop_locked(self, reason: str, counter: str) -> None:
        self._writer_health["dropped_records"] = (
            int(self._writer_health.get("dropped_records") or 0) + 1
        )
        self._writer_health[counter] = (
            int(self._writer_health.get(counter) or 0) + 1
        )
        self._writer_health["last_drop_reason"] = reason
        self._mark_metadata_dirty_locked()

    def _update_summary_locked(
        self, filename: str, record: dict[str, Any]
    ) -> None:
        summary = self._summary
        summary["records_enqueued"] = int(summary["records_enqueued"]) + 1
        summary["last_recorded_at"] = time.time()
        if filename == FILE_EVENTS:
            summary["events_total"] = int(summary["events_total"]) + 1
            _increment(summary["events_by_type"], record.get("type"))
            status = record.get("status")
            if status is not None:
                _increment(summary["event_status_counts"], status)
            if record.get("type") == "api_request" and record.get("outcome_code"):
                _increment(
                    summary["request_outcome_counts"], record.get("outcome_code")
                )
            failed = _event_is_failure(record)
            if failed:
                summary["failed_events"] = int(summary["failed_events"]) + 1
                _increment(summary["failed_events_by_type"], record.get("type"))
        elif filename == FILE_SPANS:
            summary["spans_closed"] = int(summary["spans_closed"]) + 1
            _increment(summary["spans_by_kind"], record.get("kind"))
            _increment(summary["spans_by_status"], record.get("status"))
            if record.get("status") == STATUS_ERROR or record.get("error"):
                summary["failed_spans"] = int(summary["failed_spans"]) + 1
                _increment(summary["failed_spans_by_kind"], record.get("kind"))
            if record.get("kind") == "turn":
                outputs = record.get("outputs")
                usage = outputs.get("usage") if isinstance(outputs, dict) else None
                if isinstance(usage, dict):
                    totals = summary["turn_usage_totals"]
                    for key in _TURN_USAGE_KEYS:
                        value = usage.get(key)
                        if (
                            isinstance(value, (int, float))
                            and not isinstance(value, bool)
                            and value >= 0
                        ):
                            totals[key] = int(totals.get(key, 0)) + int(value)
                    if usage.get("usage_estimated") is True:
                        summary["includes_estimated_turn_usage"] = True
        elif filename == FILE_PROMPTS:
            summary["prompts_stored"] = int(summary["prompts_stored"]) + 1

    def _writer_loop(self) -> None:
        clean_shutdown = False
        records_since_checkpoint = 0
        last_checkpoint = time.monotonic()
        try:
            with self._open_writers() as writers:
                while True:
                    try:
                        item = self._queue.get(timeout=_META_CHECKPOINT_SECONDS)
                    except queue.Empty:
                        with self._record_lock:
                            metadata_dirty = (
                                self._metadata_revision
                                > self._checkpointed_metadata_revision
                            )
                        if records_since_checkpoint or metadata_dirty:
                            if self._checkpoint_run_meta():
                                records_since_checkpoint = 0
                                last_checkpoint = time.monotonic()
                        continue
                    if item is _SHUTDOWN_SENTINEL:
                        clean_shutdown = True
                        break
                    if isinstance(item, _FlushBarrier):
                        # Per-line flush means everything drained before
                        # this marker is already on disk. Its immutable
                        # enqueue-time summary excludes records behind it.
                        item.success = self._checkpoint_run_meta(
                            summary_override=item.summary,
                            checkpoint_revision_override=(
                                item.metadata_revision
                            ),
                        )
                        if item.success:
                            records_since_checkpoint = 0
                            last_checkpoint = time.monotonic()
                        item.event.set()
                        continue
                    filename, line, payload_bytes = item
                    with self._record_lock:
                        self._queued_data_records = max(
                            0, self._queued_data_records - 1
                        )
                        self._queued_payload_bytes = max(
                            0, self._queued_payload_bytes - payload_bytes
                        )
                        self._writer_health["inflight_payload_bytes"] = (
                            int(
                                self._writer_health.get(
                                    "inflight_payload_bytes"
                                )
                                or 0
                            )
                            + payload_bytes
                        )
                        self._mark_metadata_dirty_locked()
                    writer = writers.get(filename)
                    if writer is None:
                        with self._record_lock:
                            self._writer_health["inflight_payload_bytes"] = max(
                                0,
                                int(
                                    self._writer_health.get(
                                        "inflight_payload_bytes"
                                    )
                                    or 0
                                )
                                - payload_bytes,
                            )
                            self._record_drop_locked(
                                "unknown_trace_file", "unknown_file_drops"
                            )
                        _log.debug("Unknown trace file %s; dropping line", filename)
                        continue
                    try:
                        with self._record_lock:
                            exceeds_run_limit = (
                                self._max_run_bytes > 0
                                and self._data_bytes_written + payload_bytes
                                > self._max_run_bytes
                            )
                            if exceeds_run_limit:
                                self._writer_health[
                                    "storage_limit_reached"
                                ] = True
                                self._writer_health[
                                    "inflight_payload_bytes"
                                ] = max(
                                    0,
                                    int(
                                        self._writer_health.get(
                                            "inflight_payload_bytes"
                                        )
                                        or 0
                                    )
                                    - payload_bytes,
                                )
                                self._record_drop_locked(
                                    "run_byte_limit",
                                    "run_byte_limit_drops",
                                )
                        if exceeds_run_limit:
                            continue
                        writer.write(line)
                        writer.write("\n")
                        # Per-line flush so a hard kill loses at most the
                        # line in flight — traces exist to explain exactly
                        # those exits. fsync stays close-only (per-line
                        # fsync would grind a spinning disk).
                        writer.flush()
                        with self._record_lock:
                            self._writer_health["inflight_payload_bytes"] = max(
                                0,
                                int(
                                    self._writer_health.get(
                                        "inflight_payload_bytes"
                                    )
                                    or 0
                                )
                                - payload_bytes,
                            )
                            self._data_bytes_written += payload_bytes
                            self._writer_health["data_bytes_written"] = (
                                self._data_bytes_written
                            )
                            self._writer_health["records_written"] = (
                                int(self._writer_health.get("records_written") or 0)
                                + 1
                            )
                            self._writer_health["last_write_at"] = time.time()
                            self._mark_metadata_dirty_locked()
                        records_since_checkpoint += 1
                        if (
                            records_since_checkpoint >= _META_CHECKPOINT_RECORDS
                            or time.monotonic() - last_checkpoint
                            >= _META_CHECKPOINT_SECONDS
                        ):
                            if self._checkpoint_run_meta():
                                records_since_checkpoint = 0
                                last_checkpoint = time.monotonic()
                    except Exception as exc:  # noqa: BLE001
                        with self._record_lock:
                            self._writer_health["inflight_payload_bytes"] = max(
                                0,
                                int(
                                    self._writer_health.get(
                                        "inflight_payload_bytes"
                                    )
                                    or 0
                                )
                                - payload_bytes,
                            )
                            self._writer_health["write_failures"] = (
                                int(self._writer_health.get("write_failures") or 0)
                                + 1
                            )
                            self._record_drop_locked(
                                "write_failure", "write_failure_drops"
                            )
                        _log.warning(
                            "Failed to write trace line to %s: %s", filename, exc
                        )
        except Exception as exc:  # noqa: BLE001
            with self._record_lock:
                self._writer_health["state"] = "failed"
                self._writer_health["fatal_error"] = type(exc).__name__
                self._mark_metadata_dirty_locked()
            _log.error("Trace writer thread crashed: %s", exc, exc_info=True)
        finally:
            with self._record_lock:
                if clean_shutdown:
                    if self._writer_health.get("state") != "failed":
                        self._writer_health["state"] = "stopped"
                else:
                    self._writer_health["state"] = "failed"
                self._writer_alive.clear()
                self._mark_metadata_dirty_locked()
            self._checkpoint_run_meta(thread_alive_override=False)

    def _checkpoint_run_meta(
        self,
        *,
        summary_override: dict[str, Any] | None = None,
        thread_alive_override: bool | None = None,
        checkpoint_revision_override: int | None = None,
    ) -> bool:
        try:
            self._write_run_meta_sync(
                summary_override=summary_override,
                thread_alive_override=thread_alive_override,
                checkpoint_revision_override=checkpoint_revision_override,
            )
        except Exception as exc:  # noqa: BLE001
            with self._record_lock:
                self._writer_health["metadata_write_failures"] = (
                    int(self._writer_health.get("metadata_write_failures") or 0) + 1
                )
                self._mark_metadata_dirty_locked()
            _log.warning("Failed to checkpoint trace run metadata: %s", exc)
            return False
        return True

    @contextmanager
    def _open_writers(self) -> Iterator[dict[str, Any]]:
        handles: dict[str, Any] = {}
        try:
            handles[FILE_SPANS] = (self._trace_dir / FILE_SPANS).open(
                "a", encoding="utf-8", newline="\n"
            )
            handles[FILE_EVENTS] = (self._trace_dir / FILE_EVENTS).open(
                "a", encoding="utf-8", newline="\n"
            )
            if self._capture_level == LEVEL_DEFAULT:
                handles[FILE_PROMPTS] = (self._trace_dir / FILE_PROMPTS).open(
                    "a", encoding="utf-8", newline="\n"
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

    def _existing_data_bytes(self) -> int:
        total = 0
        for name in (FILE_SPANS, FILE_EVENTS, FILE_PROMPTS):
            path = self._trace_dir / name
            try:
                if not path.is_symlink():
                    total += max(0, path.stat().st_size)
            except OSError:
                continue
        return total

    def _read_existing_run_meta(self) -> dict[str, Any] | None:
        path = self._trace_dir / FILE_RUN_META
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            _log.debug("Could not parse existing run.json (%s); fresh start", exc)
            return None

    def _write_run_meta_sync(
        self,
        *,
        summary_override: dict[str, Any] | None = None,
        thread_alive_override: bool | None = None,
        checkpoint_revision_override: int | None = None,
    ) -> None:
        self._trace_dir.mkdir(parents=True, exist_ok=True)
        path = self._trace_dir / FILE_RUN_META
        suffix = self._process_instance_id[:12] or "initial"
        tmp = path.with_name(f".{path.name}.{suffix}.tmp")
        # Serialize the snapshot and replacement as one operation. Taking the
        # record snapshot *inside* this lock prevents a delayed older snapshot
        # from overwriting a newer checkpoint produced by another caller.
        with self._run_meta_write_lock:
            with self._record_lock:
                snapshot_revision = self._metadata_revision
                persisted_revision = (
                    snapshot_revision
                    if checkpoint_revision_override is None
                    else min(
                        snapshot_revision,
                        max(0, int(checkpoint_revision_override)),
                    )
                )
                meta = copy.deepcopy(self._run_meta)
                meta["summary"] = copy.deepcopy(
                    self._summary
                    if summary_override is None
                    else summary_override
                )
                meta["recorder_health"] = (
                    self._writer_health_snapshot_locked(
                        thread_alive_override=thread_alive_override,
                        persisted_revision=persisted_revision,
                    )
                )
            tmp.write_text(
                json.dumps(
                    scrub_data(meta), indent=2, allow_nan=False
                ),
                encoding="utf-8",
            )
            tmp.replace(path)
            with self._record_lock:
                # A mutation that raced the file write has a higher current
                # revision and therefore remains dirty for the next wake.
                self._checkpointed_metadata_revision = persisted_revision


def _json_default(obj: Any) -> Any:
    # ``scrub_data`` normally normalizes these before serialization. Keep the
    # fallback defensive without reintroducing raw dataclass/set/path values
    # after the credential-aware boundary.
    normalized = scrub_data(obj)
    if normalized is not obj:
        return normalized
    # Arbitrary repr() output is not a safe diagnostics boundary: custom
    # objects may include credentials or document text that bypassed the
    # structure-aware scrubber. Capture hooks should emit JSON-ready values;
    # retain an explicit marker for anything else.
    return "<unsupported-value>"


def _empty_summary(*, coverage: str = "complete") -> dict[str, Any]:
    return {
        "coverage": coverage,
        "records_enqueued": 0,
        "events_total": 0,
        "events_by_type": {},
        "event_status_counts": {},
        "request_outcome_counts": {},
        "failed_events": 0,
        "failed_events_by_type": {},
        "spans_closed": 0,
        "spans_by_kind": {},
        "spans_by_status": {},
        "failed_spans": 0,
        "failed_spans_by_kind": {},
        "prompts_stored": 0,
        "turn_usage_totals": {},
        "includes_estimated_turn_usage": False,
        "last_recorded_at": None,
    }


def _load_summary(value: dict[str, Any]) -> dict[str, Any]:
    """Load only the stable counter vocabulary from a prior run.json."""
    summary = _empty_summary(coverage=str(value.get("coverage") or "complete"))
    for key in (
        "records_enqueued",
        "events_total",
        "failed_events",
        "spans_closed",
        "failed_spans",
        "prompts_stored",
    ):
        raw = value.get(key)
        if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
            summary[key] = raw
    for key in (
        "events_by_type",
        "event_status_counts",
        "request_outcome_counts",
        "failed_events_by_type",
        "spans_by_kind",
        "spans_by_status",
        "failed_spans_by_kind",
    ):
        raw = value.get(key)
        if isinstance(raw, dict):
            summary[key] = {
                str(name)[:120]: count
                for name, count in raw.items()
                if isinstance(count, int)
                and not isinstance(count, bool)
                and count >= 0
            }
    usage = value.get("turn_usage_totals")
    if isinstance(usage, dict):
        summary["turn_usage_totals"] = {
            key: int(usage[key])
            for key in _TURN_USAGE_KEYS
            if isinstance(usage.get(key), (int, float))
            and not isinstance(usage.get(key), bool)
            and usage[key] >= 0
        }
    summary["includes_estimated_turn_usage"] = (
        value.get("includes_estimated_turn_usage") is True
    )
    last = value.get("last_recorded_at")
    if isinstance(last, (int, float)) and not isinstance(last, bool):
        summary["last_recorded_at"] = float(last)
    return summary


def _empty_writer_health(
    *,
    state: str = "not_started",
    queue_max_records: int = DEFAULT_TRACE_QUEUE_MAX_RECORDS,
    queue_max_bytes: int = DEFAULT_TRACE_QUEUE_MAX_BYTES,
    max_run_bytes: int = 0,
    data_bytes_written: int = 0,
) -> dict[str, Any]:
    return {
        "state": state,
        "records_written": 0,
        "write_failures": 0,
        "metadata_write_failures": 0,
        "dropped_records": 0,
        "queue_count_drops": 0,
        "queue_byte_drops": 0,
        "run_byte_limit_drops": 0,
        "serialization_failures": 0,
        "unknown_file_drops": 0,
        "write_failure_drops": 0,
        "last_drop_reason": None,
        "queue_max_records": queue_max_records,
        "queue_max_bytes": queue_max_bytes,
        "queue_high_watermark": 0,
        "queue_payload_high_watermark": 0,
        "queue_payload_bytes": 0,
        "resident_payload_bytes": 0,
        "inflight_payload_bytes": 0,
        "max_run_bytes": max_run_bytes,
        "storage_limit_scope": "jsonl_payload_bytes",
        "storage_limit_reached": False,
        "data_bytes_written": data_bytes_written,
        "last_write_at": None,
        "drain_timed_out": False,
    }


def _event_is_failure(record: dict[str, Any]) -> bool:
    """Classify failures from a closed, content-free diagnostic vocabulary."""
    if record.get("type") in _FAILURE_EVENT_TYPES:
        return True
    if record.get("ok") is False or bool(record.get("error")):
        return True
    status = record.get("status")
    if (
        isinstance(status, int)
        and not isinstance(status, bool)
        and status >= 400
    ):
        return True
    if isinstance(status, str) and status.lower() in _FAILURE_STATUS_VALUES:
        return True
    for key in _FAILURE_COUNT_FIELDS:
        value = record.get(key)
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value > 0
        ):
            return True
    return False


def _increment(counts: dict[str, int], raw_key: Any) -> None:
    key = str(raw_key if raw_key is not None else "<missing>")[:120]
    counts[key] = int(counts.get(key, 0)) + 1


# ---- module-level recorder singleton -----------------------------------
_RECORDER: TraceRecorder | None = None
_RECORDER_LOCK = threading.Lock()


def get_recorder() -> TraceRecorder | None:
    return _RECORDER


def set_recorder(recorder: TraceRecorder | None) -> None:
    global _RECORDER
    with _RECORDER_LOCK:
        _RECORDER = recorder
