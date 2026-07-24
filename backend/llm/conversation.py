"""Streaming conversation engine with document tool-use.

One synchronous generator per user turn: yields UI-ready event dicts that
the FastAPI layer serializes as Server-Sent Events. History and the
document store live on a :class:`SessionState` owned by the caller
(``backend.sessions``).

Context architecture (the "Sonnet unleashed" restructure, 2026-07-21)
---------------------------------------------------------------------
The system prompt is ONLY the stable module-rendered block, carrying
``cache_control`` — byte-identical across the whole session. Everything
session-varying (standards editions in effect, the research profile, the
FULL document text, the lint report, open items) rides a PROJECT CONTEXT
block spliced into the newest user message instead. That ordering is what
makes the conversation history a stable, cacheable prefix: a second cache
breakpoint rides the tail of the request's messages, so the growing
interview hits the prompt cache incrementally instead of re-billing every
token every turn. At commit the spliced context (and the turn's thinking
blocks, plus any fetched-PDF payloads) are stripped from the stored
history — each request carries exactly one, current, state block.

The model sees the ENTIRE document every turn — full paragraph text, ids,
statuses, provenance chips — never a truncated outline. Tool results still
carry the compact outline as an id map for mid-turn orientation.

Tool loop
---------
``apply_spec_edits`` plus the ``web_search``/``web_fetch`` server tools
(static config — byte-stable so the cached prefix never busts). A turn is
a continuation loop: stream a response; on ``tool_use``, apply the edits
transactionally (an invalid batch becomes an ``is_error`` tool result the
model can correct), emit a ``doc_patch`` event, send the tool results
back, and stream again; on ``pause_turn`` (long server-tool work), re-send
the assistant content per the pause contract and continue. Adaptive
thinking is stated explicitly (Sonnet 5 runs it by default) with the
effort level from settings; thinking blocks are preserved verbatim across
continuation rounds — the API requires them during tool use — and dropped
only at commit.

Turn atomicity is unchanged from Phase 2: history mutates and the document
turn commits (one undo snapshot per changed turn) only after a fully
successful turn. Every failure path yields one ``error`` event, rolls the
document back to its pre-turn state, and leaves history unchanged, so a
resend never duplicates anything.

User-initiated stop (``POST /api/chat/stop``) is deliberately NOT a failure
path: it sets ``SessionState.stop_requested``, which this loop checks
between streamed events and between rounds. Stopping closes the in-flight
request immediately (no draining the rest of the network stream) but takes
the SAME commit path as a normal turn — whatever text/edits landed before
the click are kept, exactly like Claude.ai's stop button — rather than the
rollback a genuine failure gets.
"""
from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

import anthropic

from .. import settings
from ..figures import CREATE_FIGURE_TOOL, FigureError, FigureStore
from ..spec_doc import (
    APPLY_SPEC_EDITS_TOOL,
    DocumentStore,
    SpecEditError,
    SpecSection,
    lint_document,
    open_questions,
    outline,
)
from ..spec_doc.model import apply_edits
from ..spec_doc.project import chat_transcript
from ..spec_doc.source_mapping import (
    SourceBodyMap,
    semantic_body_projection,
    semantic_body_projection_sha256,
)
from ..spec_doc.source_patch import (
    SourceCapabilityReport,
    SourcePatchContext,
    SourcePatchError,
    blocked_source_edit_capabilities,
    source_capability_summary,
    source_patch_readiness,
    validate_source_map_identity,
    validate_source_transition,
)
from ..compliance import AuditRunner
from ..qc import QCRunner
from ..research import ResearchRunner, research_context_block
from ..research.resend_sanitizer import (
    elide_all_pdf_sources,
    sanitize_messages_for_resend,
)
from ..research.schema import build_web_fetch_tool, build_web_search_tool
from ..suggestions import SUGGEST_PROMPTS_TOOL, SuggestError, validate_prompts
from ..tracing import capture as _trace
from ..spec_modules import SpecModule, get_module
from ..standards import standards_context_block
from ..usage_ledger import UsageLedger
from .client import MissingApiKeyError, get_client
from .prompts import (
    render_system_prompt,
    sanitize_discipline,
    sanitize_project_context,
)

# Ceiling on continuation rounds (tool dispatches + pause_turn resumes)
# within one user turn. This is a runaway circuit breaker, not a quality
# limit: each round can carry an arbitrarily large edit batch and a fresh
# web-tool allowance, so no legitimate turn gets anywhere near it — the
# failure mode it guards is a model resubmitting the same broken batch
# forever. Hitting it is treated as a failed (retry-safe) turn.
MAX_TOOL_ROUNDS = 50


def _chat_tools() -> list[dict[str, Any]]:
    """The interview tool list: document edits + figures + live web lookups
    + suggested replies.

    Static configuration on purpose — tools precede the system prompt in
    the cached prefix, so anything per-turn here (e.g. a profile-derived
    ``user_location``) would bust the prompt cache for the whole session.
    The model steers search locale through its query text instead.
    ``suggest_prompts`` is appended LAST so the existing tool bytes stay a
    stable cached prefix.
    """
    return [
        APPLY_SPEC_EDITS_TOOL,
        CREATE_FIGURE_TOOL,
        build_web_search_tool(max_uses=settings.CHAT_MAX_SEARCHES),
        build_web_fetch_tool(max_uses=settings.CHAT_MAX_FETCHES),
        SUGGEST_PROMPTS_TOOL,
    ]


@dataclass
class SessionState:
    """One conversation's accumulated state: history + document + module.

    ``generation`` increments whenever the session is replaced out from
    under a possibly-streaming turn (reset, project load); an in-flight
    turn compares it before touching the store or committing, so a zombie
    turn can never pollute the fresh session. ``module`` is the active
    :class:`SpecModule` — reset keeps it (a fresh session in the same
    discipline), project load resolves it from the file's ``module_id``.
    """

    history: list[dict[str, Any]] = field(default_factory=list)
    doc: DocumentStore = field(default_factory=DocumentStore)
    # Exact imported DOCX bytes are immutable recovery/preservation input.
    # Native .baspec persistence stores them as a separate bounded member;
    # they never enter the semantic tree or model context.
    source_docx_bytes: bytes | None = None
    source_docx_filename: str = ""
    # Immutable paragraph anchors into ``source_docx_bytes``.  This stays out
    # of the semantic tree/LLM context; project-container persistence uses its
    # strict ``to_dict`` / ``from_dict`` representation.
    source_docx_map: SourceBodyMap | None = None
    # Process-local derived indexes for ``source_docx_bytes``. This cache is
    # deliberately absent from semantic/project persistence and is rebuilt
    # from the exact source after import/load. ``repr=False`` avoids dumping
    # retained source bytes through dataclass diagnostics.
    source_patch_context: SourcePatchContext | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    # Memoized ``source_edit_capabilities`` result, keyed on the source,
    # baseline and current semantic body it was derived from. Purely derived
    # state: never serialized, cleared on reset/load with the source fields.
    _capability_cache: tuple[tuple[Any, ...], Any] | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    # Sanitized, JSON-safe import diagnostics do ride the project file so a
    # resumed session still tells the truth about normalization and loss.
    import_report: dict[str, Any] | None = None
    generation: int = 0
    module: SpecModule = field(default_factory=lambda: get_module(None))
    # Session-level discipline (Batch 10), meaningful only with an
    # open-catalog module — invariant: non-empty ⇒ the active module is
    # open-catalog, enforced at the two write sites (the reset endpoint and
    # project load). Like ``module``, reset keeps it (the session-start
    # picker always sends explicit values). Rendered into PROJECT CONTEXT
    # each turn, never the cached stable prompt.
    discipline: str = ""
    # Optional one-or-two-sentence project description from the session-start
    # picker (free text, sanitized). Purely primes the model — rendered into
    # PROJECT CONTEXT each turn, never the cached stable prompt. Unlike
    # ``discipline`` this is CLEARED on reset: it describes one specific
    # project, so it must not bleed into the next session.
    project_context: str = ""
    research: ResearchRunner = field(default_factory=ResearchRunner)
    audit: AuditRunner = field(default_factory=AuditRunner)
    # Final QC on Fable 5 (Batch 4). Replaced on reset/load like the other
    # runners so an in-flight run settles into the abandoned object.
    qc: QCRunner = field(default_factory=QCRunner)
    # Chat-authored figures (diagrams/schematics/tables). Like the document
    # store it is reset in place (never reassigned) so a zombie turn's
    # commit/rollback settles harmlessly against the cleared store.
    figures: FigureStore = field(default_factory=FigureStore)
    # Suggested-reply chips staged by the model (Batch 9). Turn-atomic,
    # latest-only: each committed turn REPLACES this with what it staged —
    # including [] when the tool was not called, which is how the bar winds
    # down as the section nears issue-ready. A failed turn leaves it
    # untouched (staging is a turn-local in stream_user_turn, not a store).
    suggested_prompts: list[str] = field(default_factory=list)
    # Session-scoped billed-usage meter (WI4). Reset/load clear it.
    usage: UsageLedger = field(default_factory=UsageLedger)
    # True while a model turn owns the document store (WI2). Manual edits are
    # rejected in this window — a mid-turn manual edit would be swept into the
    # streaming turn's commit or rollback.
    turn_active: bool = False
    # ``turn_active`` is part of the public session surface (several endpoint
    # guards read it), but claiming that flag must be atomic. Without a
    # private owner token two simultaneous /api/chat streams can both call
    # DocumentStore.begin_turn(); the second begin rolls the first provisional
    # turn back. The token also prevents a zombie generator invalidated by a
    # reset/load from releasing or rolling back a newer turn.
    _turn_state_lock: Any = field(
        default_factory=threading.RLock,
        repr=False,
        compare=False,
    )
    _active_turn_token: object | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    # Set by POST /api/chat/stop to ask the in-flight turn to stop generating.
    # Cleared at the start of every turn. Checked between streamed events and
    # between rounds; stopping commits whatever was produced so far (like
    # Claude.ai's stop button) rather than rolling back like a failure.
    stop_requested: threading.Event = field(default_factory=threading.Event)

    def claim_model_turn(self) -> tuple[object, int] | None:
        """Atomically claim the single streaming-turn slot.

        The returned generation is captured in the same critical section as
        the claim so a turn cannot accidentally adopt a reset/load that raced
        with its startup.
        """
        with self._turn_state_lock:
            if self.turn_active:
                return None
            # Clear the prior turn's signal before publishing the new owner.
            # Once the token is visible, a concurrent stop belongs to this
            # turn and must never be erased later during store startup.
            self.stop_requested.clear()
            token = object()
            self._active_turn_token = token
            self.turn_active = True
            return token, self.generation

    def release_model_turn(self, token: object) -> bool:
        """Release ``token`` iff it still owns the streaming-turn slot."""
        with self._turn_state_lock:
            if self._active_turn_token is not token:
                return False
            self._active_turn_token = None
            self.turn_active = False
            return True

    def request_model_stop(self) -> bool:
        """Atomically stop whichever model turn currently owns the session."""
        with self._turn_state_lock:
            if not self.turn_active or self._active_turn_token is None:
                return False
            self.stop_requested.set()
            return True

    def begin_model_turn_stores(self, token: object, generation: int) -> bool:
        """Begin both provisional stores if ``token`` is still current."""
        with self._turn_state_lock:
            if (
                self._active_turn_token is not token
                or self.generation != generation
            ):
                return False
            doc_started = False
            figure_started = False
            try:
                self.doc.begin_turn()
                doc_started = True
                self.figures.begin_turn()
                figure_started = True
            except Exception:
                if figure_started:
                    self.figures.rollback_turn()
                if doc_started:
                    self.doc.rollback_turn()
                raise
            return True

    def finalize_model_turn(self, token: object, *, committed: bool) -> bool:
        """Settle owned provisional stores and atomically release the slot."""
        with self._turn_state_lock:
            if self._active_turn_token is not token:
                return False
            if not committed:
                self.doc.rollback_turn()
                self.figures.rollback_turn()
            self._active_turn_token = None
            self.turn_active = False
            return True

    def invalidate_model_turn(self) -> None:
        """Invalidate any streaming owner and advance the session generation."""
        with self._turn_state_lock:
            self._active_turn_token = None
            self.turn_active = False
            self.generation += 1

    def add_usage_if_current(
        self,
        generation: int,
        category: str,
        usage: Any,
        *,
        count_turn: bool = False,
    ) -> bool:
        """Account background usage only for the session that launched it.

        Reset and project load replace background runners but cannot cancel a
        provider request already in flight.  A completion from that abandoned
        runner must not repopulate the freshly-cleared session usage ledger.
        """
        with self._turn_state_lock:
            if self.generation != generation:
                return False
            self.usage.add(category, usage, count_turn=count_turn)
            return True

    def delete_figure_if_idle(
        self,
        fid: str,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Atomically delete one figure without crossing a model turn."""
        with self._turn_state_lock:
            if self.turn_active:
                return "active", []
            if not self.figures.delete(fid):
                return "missing", []
            return "deleted", self.figures.snapshot()

    @contextmanager
    def session_state_guard(self) -> Iterator[None]:
        """Serialize non-model state access against model-turn ownership."""
        with self._turn_state_lock:
            yield

    @contextmanager
    def owned_model_turn_guard(
        self,
        token: object,
        generation: int,
    ) -> Iterator[bool]:
        """Lock live session state and report whether a turn still owns it."""
        with self._turn_state_lock:
            yield (
                self._active_turn_token is token
                and self.generation == generation
            )

    def ensure_source_patch_context(
        self,
        *,
        baseline: SpecSection | None = None,
    ) -> SourcePatchContext | None:
        """Return the session's immutable source index, building it once.

        Missing source artifacts remain the responsibility of the public
        readiness/transition gates, which already report ``source_unavailable``
        fail closed. A stale non-``None`` context is never silently replaced:
        the gate binds it to the current bytes/map/baseline and rejects any
        mismatch. Resolve the builder through its module so tests and future
        instrumentation have one stable construction seam.
        """
        baseline_index = self.doc.baseline_index
        if (
            self.source_docx_bytes is None
            or self.source_docx_map is None
            or isinstance(baseline_index, bool)
            or not isinstance(baseline_index, int)
            or not 0 <= baseline_index < len(self.doc.versions)
        ):
            # Legacy/source-less project loads can clear bytes and maps without
            # replacing the SessionState object. Never retain a cache after its
            # owning source artifacts or imported baseline disappear.
            self.source_patch_context = None
            return None
        if self.source_patch_context is None:
            from ..spec_doc import source_patch as source_patch_module

            resolved_baseline = baseline
            if resolved_baseline is None:
                resolved_baseline = SpecSection.from_dict(
                    self.doc.versions[baseline_index]
                )
            self.source_patch_context = (
                source_patch_module.build_source_patch_context(
                    source_bytes=self.source_docx_bytes,
                    source_map=self.source_docx_map,
                    baseline=resolved_baseline,
                )
            )
        return self.source_patch_context

    def apply_doc_edits(self, edits: Any) -> list[dict[str, Any]]:
        """The single guarded entry point for model and manual edit batches.

        Retained source artifacts constrain body edits only while the imported
        baseline is still on the active history branch. Undoing before the
        import and starting a fresh draft is allowed;
        ``DocumentStore.commit_turn`` then removes the abandoned baseline in
        its established way.
        """
        if self._active_source_scope():
            # P1b safety is a property of the complete proposed document, not
            # of an operation name in isolation. Build the batch on a deep
            # copy first, then prove that final state against the immutable
            # source package and imported baseline. The real store remains
            # untouched until both the ordinary edit validator and the DOCX
            # preservation gate have accepted the whole transaction.
            candidate, _candidate_ops = apply_edits(self.doc.doc, edits)
            # Metadata is workspace state, not source-body XML. Decide that
            # exemption from the complete before/after semantic projections,
            # never from action names, so a mixed batch still enters the full
            # source gate whenever its final body would differ.
            body_changed = semantic_body_projection(
                candidate
            ) != semantic_body_projection(self.doc.doc)
            if body_changed:
                baseline_index = self.doc.baseline_index
                try:
                    if (
                        isinstance(baseline_index, bool)
                        or not isinstance(baseline_index, int)
                        or not 0 <= baseline_index < len(self.doc.versions)
                    ):
                        raise SourcePatchError(
                            "source",
                            "baseline_unavailable",
                            "the imported semantic baseline is unavailable",
                        )
                    try:
                        baseline = SpecSection.from_dict(
                            self.doc.versions[baseline_index]
                        )
                    except (TypeError, ValueError) as baseline_exc:
                        raise SourcePatchError(
                            "source",
                            "baseline_unavailable",
                            "the imported semantic baseline is unavailable",
                        ) from baseline_exc
                    if (
                        not isinstance(self.source_docx_bytes, bytes)
                        or not isinstance(self.source_docx_map, SourceBodyMap)
                    ):
                        raise SourcePatchError(
                            "source",
                            "source_unavailable",
                            "the exact imported DOCX and source map are unavailable",
                        )
                    context = self.ensure_source_patch_context(baseline=baseline)
                    if context is None:
                        raise SourcePatchError(
                            "source",
                            "source_unavailable",
                            "the exact imported DOCX and source map are unavailable",
                        )
                    validate_source_transition(
                        source_bytes=self.source_docx_bytes,
                        source_map=self.source_docx_map,
                        baseline=baseline,
                        current=candidate,
                        context=context,
                    )
                except SourcePatchError as exc:
                    detail = exc.detail.rstrip(".")
                    raise SpecEditError(
                        f"Source-backed edit rejected for {exc.uid!r} "
                        f"[{exc.blocker}]: {detail}. Nothing was applied."
                    ) from exc
        return self.doc.apply_edits(edits)

    def _active_source_scope(self) -> bool:
        """Whether current body mutations require source preservation.

        A native source with either required artifact present remains active
        when its companion artifact is missing; that missing artifact must
        fail closed. A legacy JSON project intentionally has neither artifact
        and remains an ordinary semantic document. Undoing before a valid
        imported baseline also leaves the retained source only in the redo
        tail, where it does not constrain a fresh branch.
        """
        if self.source_docx_bytes is None and self.source_docx_map is None:
            return False
        baseline_index = self.doc.baseline_index
        # Committing a new branch from before the import intentionally clears
        # the baseline while retaining the original bytes for recovery. That
        # abandoned source must not constrain subsequent fresh-branch edits.
        if baseline_index is None:
            return False
        if (
            not isinstance(baseline_index, bool)
            and isinstance(baseline_index, int)
            and 0 <= baseline_index < len(self.doc.versions)
            and self.doc.index < baseline_index
        ):
            return False
        return True

    def source_edit_capabilities(self) -> SourceCapabilityReport | None:
        """Return current per-operation source permissions, or ``None``.

        ``None`` means the active branch is not source-backed: this is a fresh
        or legacy source-less document, or the imported baseline currently
        lives only in an undone redo tail. Once any retained source artifact
        and its imported baseline are active, every missing or mismatched
        companion artifact becomes an explicit all-body-operations denied
        report instead of silently restoring ordinary editing.

        The report is transient derived state. It is recomputed from the
        current semantic document so undo, redo, model edits, manual edits,
        and QC applications all receive current decisions, while the costly
        immutable package index is reused through ``source_patch_context``.

        Recomputation is memoized on the exact inputs the report is derived
        from — the retained source, the active baseline, and the current
        semantic body. The sweep probes every element against the real gate,
        so it is expensive in body size, and it sits on paths the UI hits
        repeatedly for one user action (the import response, every
        ``_doc_payload`` refresh, the readiness checklist, the QC snapshot,
        and every turn's PROJECT CONTEXT). Those calls all asked the same
        question of the same document and re-derived the same answer. Any
        change to the source, the baseline or the body text/order changes the
        key, so the cache can never outlive the state it describes; provenance
        metadata deliberately does not, exactly as
        ``semantic_body_projection`` excludes it from the source gate.
        """
        if not self._active_source_scope():
            return None

        try:
            projection = semantic_body_projection_sha256(self.doc.doc)
        except Exception:  # noqa: BLE001 - an unkeyable document is never cached
            return self._compute_source_edit_capabilities()
        # Identity, not declared identity: the retained bytes/map/context are
        # compared with ``is`` against the exact objects the cached report was
        # derived from, and the key holds them alive so identity stays sound.
        # Keying on the map's *claimed* source hash would miss a swapped or
        # mutated artifact, which must still fail closed.
        key = (
            self.source_docx_bytes,
            self.source_docx_map,
            self.source_patch_context,
            self.doc.baseline_index,
            self.doc.index,
            projection,
        )
        cached = self._capability_cache
        if cached is not None:
            cached_key, cached_report = cached
            if (
                cached_key[0] is key[0]
                and cached_key[1] is key[1]
                and cached_key[2] is key[2]
                and cached_key[3] == key[3]
                and cached_key[4] == key[4]
                and cached_key[5] == key[5]
            ):
                return cached_report

        report = self._compute_source_edit_capabilities()
        # Re-read the artifacts: computing the report can build and attach the
        # patch context, so the key must describe the state it actually used.
        self._capability_cache = (
            (
                self.source_docx_bytes,
                self.source_docx_map,
                self.source_patch_context,
                self.doc.baseline_index,
                self.doc.index,
                projection,
            ),
            report,
        )
        return report

    def _compute_source_edit_capabilities(
        self,
    ) -> SourceCapabilityReport | None:
        """Derive the capability report from scratch (see the caller)."""
        baseline_index = self.doc.baseline_index
        if baseline_index is None:
            return None
        if (
            isinstance(baseline_index, bool)
            or not isinstance(baseline_index, int)
            or not 0 <= baseline_index < len(self.doc.versions)
        ):
            return blocked_source_edit_capabilities(
                self.doc.doc,
                blocker="baseline_unavailable",
                message="the imported semantic baseline is unavailable",
            )
        try:
            baseline = SpecSection.from_dict(self.doc.versions[baseline_index])
        except (TypeError, ValueError):
            return blocked_source_edit_capabilities(
                self.doc.doc,
                blocker="baseline_unavailable",
                message="the imported semantic baseline is unavailable",
            )

        if (
            not isinstance(self.source_docx_bytes, bytes)
            or not isinstance(self.source_docx_map, SourceBodyMap)
        ):
            return blocked_source_edit_capabilities(
                self.doc.doc,
                blocker="source_unavailable",
                message=(
                    "the exact imported DOCX and source map are unavailable"
                ),
            )

        try:
            context = self.ensure_source_patch_context(baseline=baseline)
            if context is None:
                return blocked_source_edit_capabilities(
                    self.doc.doc,
                    blocker="source_unavailable",
                    message=(
                        "the exact imported DOCX and source map are unavailable"
                    ),
                )
            # The capability API consumes the source bytes retained inside the
            # immutable context. Bind that context to the session's *current*
            # source bytes first so stale cache state cannot grant permission.
            validate_source_map_identity(
                source_bytes=self.source_docx_bytes,
                source_map=self.source_docx_map,
                baseline=baseline,
                context=context,
            )
            from ..spec_doc import source_patch as source_patch_module

            return source_patch_module.source_edit_capabilities(
                context=context,
                source_map=self.source_docx_map,
                baseline=baseline,
                current=self.doc.doc,
            )
        except SourcePatchError as exc:
            return blocked_source_edit_capabilities(
                self.doc.doc,
                blocker=exc.blocker,
                message=exc.detail,
            )
        except Exception:  # noqa: BLE001 - capability analysis must fail closed
            # Capability reporting is advisory and runs on ordinary response
            # refresh paths. An unexpected probe/preflight failure must deny
            # body actions without taking down document recovery or bypassing
            # the real edit gate, which will still report its precise error
            # if a forged request is submitted.
            return blocked_source_edit_capabilities(
                self.doc.doc,
                blocker="output_validation_failed",
            )

    def source_export_readiness(self) -> dict[str, object]:
        """Current source-patch capability for API/UI integration."""
        baseline_index = self.doc.baseline_index
        if (
            baseline_index is None
            or not 0 <= baseline_index < len(self.doc.versions)
        ):
            return {
                "ready": False,
                "no_op": False,
                "changed_uids": [],
                "blockers": [
                    {
                        "uid": "source",
                        "blocker": "baseline_unavailable",
                        "message": "the imported semantic baseline is unavailable",
                    }
                ],
            }
        baseline = SpecSection.from_dict(self.doc.versions[baseline_index])
        try:
            context = self.ensure_source_patch_context(baseline=baseline)
        except SourcePatchError as exc:
            return {
                "ready": False,
                "no_op": False,
                "changed_uids": [],
                "blockers": [
                    {
                        "uid": exc.uid,
                        "blocker": exc.blocker,
                        "message": exc.detail,
                    }
                ],
                "mutation_blockers": [],
            }
        return source_patch_readiness(
            source_bytes=self.source_docx_bytes,
            source_map=self.source_docx_map,
            baseline=baseline,
            current=self.doc.doc,
            context=context,
        ).to_dict()

    def reset(
        self,
        *,
        module_id: str | None = None,
        discipline: str | None = None,
        project_context: str | None = None,
    ) -> None:
        # Keep a new stream from claiming the session while its stores are
        # only partially reset. Existing streams do not take this lock while
        # yielding; the generation/token invalidation below remains their
        # cancellation signal.
        with self._turn_state_lock:
            next_module = (
                get_module(module_id)
                if module_id is not None and module_id.strip()
                else self.module
            )
            next_discipline = (
                self.discipline
                if discipline is None
                else sanitize_discipline(discipline)
            )
            if not getattr(next_module, "open_catalog", False):
                next_discipline = ""
            next_project_context = (
                ""
                if project_context is None
                else sanitize_project_context(project_context)
            )
            self._reset_while_locked()
            self.module = next_module
            self.discipline = next_discipline
            self.project_context = next_project_context

    def _reset_while_locked(self) -> None:
        self.history.clear()
        self.doc.reset()
        self.source_docx_bytes = None
        self.source_docx_filename = ""
        self.source_docx_map = None
        self.source_patch_context = None
        self._capability_cache = None
        self.import_report = None
        # Per-project priming text does not survive a reset (see the field
        # comment). Module and discipline are kept; this is not.
        self.project_context = ""
        # Fresh runners: work still running against the old session
        # finishes into the abandoned objects (the zombie-turn pattern).
        self.research = ResearchRunner()
        self.audit = AuditRunner()
        self.qc = QCRunner()
        # In-place reset (see the field comment): a still-streaming zombie
        # turn holds this same object; clearing turn state neutralizes it.
        self.figures.reset()
        # Clear staged chips (commit is generation-guarded, so a zombie
        # turn can't repopulate the fresh session).
        self.suggested_prompts.clear()
        # The meter answers "what has THIS session spent" — a fresh session
        # starts at zero (the trace remains the permanent record).
        self.usage.reset()
        self._active_turn_token = None
        self.turn_active = False
        self.generation += 1


class _SessionInvalidated(RuntimeError):
    """The session was reset/replaced while this turn was still streaming."""


def _stable_system_blocks(session: SessionState) -> list[dict[str, Any]]:
    """The system prompt: ONLY the stable module block, cached.

    Nothing session-varying may render here (pinned by
    ``test_stable_system_prompt_is_cached_and_module_rendered``); the live
    state travels in the PROJECT CONTEXT block of the newest user message
    (:func:`_turn_context_text`), after the cacheable history prefix.
    """
    return [
        {
            "type": "text",
            "text": render_system_prompt(session.module),
            "cache_control": {"type": "ephemeral"},
        }
    ]


# project_profile dict key -> the label used in the PROJECT PROFILE block
# and in the model's follow-up questions.
_PROFILE_FIELD_LABELS: tuple[tuple[str, str], ...] = (
    ("city", "city"),
    ("state_or_province", "state/province"),
    ("country", "country"),
    ("client_name", "client"),
)


def _profile_status_block(project_profile: dict[str, str]) -> str:
    """Per-field PROJECT PROFILE status for the PROJECT CONTEXT block.

    Renders every turn — not only when the profile changes — so the model
    always knows what is still missing without relying on memory of
    earlier turns. ``project_identity`` is a non-defaultable playbook
    topic (no default to fall back on); this is what lets the model
    re-raise a still-missing field every so often instead of dropping it
    after a single unanswered ask, and lets it recognize a field the user
    just filled in from the panel form as already settled.
    """
    stored = project_profile or {}
    lines = ["PROJECT PROFILE (city, state/province, country, client):"]
    missing = []
    for key, label in _PROFILE_FIELD_LABELS:
        value = stored.get(key, "")
        lines.append(f"- {label}: {value or '[not yet recorded]'}")
        if not value:
            missing.append(label)
    if missing:
        lines.append(
            "Incomplete — missing " + ", ".join(missing) + ". This has no "
            "default: ask about the missing field(s) when it fits "
            "naturally, and raise it again in a later turn if it goes "
            "unanswered."
        )
    else:
        lines.append("Complete.")
    return "\n".join(lines)


def _source_editing_boundary_block(session: SessionState) -> str | None:
    """Render compact, server-derived source permissions for the model.

    This remains dynamic PROJECT CONTEXT rather than cached system-prompt
    content. The summary is advisory guidance; ``apply_doc_edits`` still
    validates every complete proposed final state through the source gate.
    """
    report = session.source_edit_capabilities()
    if report is None:
        return None
    return (
        "IMPORTED DOCX EDITING BOUNDARY (hard constraint):\n"
        + source_capability_summary(report, session.doc.doc)
    )


def _turn_context_text(session: SessionState) -> str:
    """The PROJECT CONTEXT block: everything live, rendered at turn start.

    Standards editions in effect, the project-profile status, the research
    profile (when one exists), the FULL document text with
    ids/statuses/provenance, the lint report, and the open-item list.
    Spliced ahead of the user's text in the newest user message and
    stripped again at commit — each request carries exactly one, current,
    state block, never a stale one.
    """
    doc = session.doc.doc
    parts = []
    # Session discipline (Batch 10): renders only for open-catalog sessions
    # (never for curated modules — their request bytes are unchanged).
    if session.discipline:
        parts.append(
            f"PROJECT DISCIPLINE: {session.discipline} (session-selected — "
            "governs section selection, conventions, terminology, and "
            "research)"
        )
    elif getattr(session.module, "open_catalog", False):
        parts.append(
            "PROJECT DISCIPLINE: [not yet stated] — ask the user what "
            "discipline this section is for before drafting domain content."
        )
    # Optional project-description primer (any module — not gated by
    # open_catalog); renders only when the user provided one at session start.
    if session.project_context:
        parts.append(
            "PROJECT DESCRIPTION (stated by the user at session start): "
            + session.project_context
        )
    parts += [
        standards_context_block(
            session.module.basis,
            doc.edition_overrides,
            doc.suppressed_standards,
        ),
        _profile_status_block(doc.project_profile),
    ]
    source_boundary = _source_editing_boundary_block(session)
    if source_boundary is not None:
        parts.append(source_boundary)
    research_profile = getattr(session.research, "profile_result", None)
    if research_profile is not None:
        block, _dropped = research_context_block(research_profile)
        parts.append(block)
    parts.append(
        "Current specification document (full text; element ids in "
        "[id: …], provenance chips as ◆item-id):\n"
        + outline(doc, max_text=None)
    )
    lint_items = lint_document(doc, session.module)
    if lint_items:
        lines = [
            "LINT REPORT (deterministic, advisory — stale-edition findings "
            "are drafting errors to fix):"
        ]
        for issue in lint_items:
            where = issue.get("ref") or issue.get("element_id") or ""
            lines.append(
                f"- [{issue.get('rule')}] {where}: {issue.get('message')} "
                f"(element {issue.get('element_id')})"
            )
        parts.append("\n".join(lines))
    open_items = open_questions(doc)
    if open_items:
        lines = ["OPEN ITEMS (resolve as answers arrive):"]
        for item in open_items:
            lines.append(
                f"- {item.get('ref')} [{item.get('kind')}] "
                f"{item.get('label')} (element {item.get('element_id')})"
            )
        parts.append("\n".join(lines))
    figure_stubs = session.figures.context_stubs()
    if figure_stubs:
        parts.append(figure_stubs)
    return (
        "=== PROJECT CONTEXT (current state — supersedes anything "
        "remembered from earlier turns) ===\n\n"
        + "\n\n".join(parts)
        + "\n\n=== END PROJECT CONTEXT ==="
    )


def _serialize(node: Any) -> Any:
    """Deep-serialize SDK content into plain JSON-able structures.

    Preserves EVERY block type verbatim — text (with citations), tool_use,
    thinking/redacted_thinking (empty ``thinking`` fields included, per the
    adaptive-thinking contract), server_tool_use, and the web tool result
    blocks — so continuation rounds can re-send exactly what the API
    returned. Pydantic models dump via ``model_dump``; test fakes
    (SimpleNamespace) fall back to ``vars()``.
    """
    if isinstance(node, dict):
        return {k: _serialize(v) for k, v in node.items()}
    if isinstance(node, (list, tuple)):
        return [_serialize(v) for v in node]
    dump = getattr(node, "model_dump", None)
    if callable(dump):
        try:
            return dump(mode="json", exclude_none=True)
        except Exception:  # noqa: BLE001 — fall through to attribute dump
            pass
    if hasattr(node, "__dict__") and not isinstance(node, type):
        return {k: _serialize(v) for k, v in vars(node).items()}
    return node


def _content_blocks_to_dicts(content: Any) -> list[dict[str, Any]]:
    """Serialize SDK content blocks into plain history dicts, verbatim."""
    blocks: list[dict[str, Any]] = []
    for block in content or []:
        serialized = _serialize(block)
        if isinstance(serialized, dict) and serialized.get("type"):
            blocks.append(serialized)
    return blocks


_TRANSIENT_BLOCK_TYPES = frozenset({"thinking", "redacted_thinking"})


def _elide_figure_tool_inputs(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Strip the heavy ``source``/``rows`` out of ``create_figure`` tool_use
    blocks in committed history (copy-on-write).

    The full figure markup already lives in the figure store; leaving it in
    the assistant's tool_use block would re-send it as cached history every
    later turn and balloon the project file. The model never needs the old
    source (a per-turn FIGURES stub tells it the figure exists) — mirrors the
    fetched-PDF elision. ``kind``/``title`` are kept so the call stays
    readable; the tool_result was already compact.
    """
    result: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") != "assistant":
            result.append(message)
            continue
        content = message.get("content") or []
        changed = False
        new_content: list[dict[str, Any]] = []
        for block in content:
            if (
                block.get("type") == "tool_use"
                and block.get("name") == "create_figure"
            ):
                inp = block.get("input") or {}
                new_content.append(
                    {
                        **block,
                        "input": {
                            "kind": inp.get("kind"),
                            "title": inp.get("title"),
                            "_elided": "source stored with the figure",
                        },
                    }
                )
                changed = True
            else:
                new_content.append(block)
        result.append(
            {**message, "content": new_content} if changed else message
        )
    return result


def _committed_messages(
    new_messages: list[dict[str, Any]], user_text: str
) -> list[dict[str, Any]]:
    """The turn's messages as history stores them: lean and current-free.

    - The first user message keeps ONLY the user's text (the PROJECT
      CONTEXT block would otherwise fossilize a stale document snapshot
      into every later request).
    - Thinking blocks drop — the adaptive-thinking contract only requires
      them within the turn that produced them.
    - Fetched-PDF payloads are elided wholesale (see
      :func:`elide_all_pdf_sources`); search results and citations stay.
    - ``create_figure`` tool inputs shed their heavy source (see
      :func:`_elide_figure_tool_inputs`) — the figure store holds it.
    """
    committed: list[dict[str, Any]] = [
        {"role": "user", "content": [{"type": "text", "text": user_text}]}
    ]
    for message in new_messages[1:]:
        if message.get("role") != "assistant":
            committed.append(message)
            continue
        content = [
            b
            for b in (message.get("content") or [])
            if b.get("type") not in _TRANSIENT_BLOCK_TYPES
        ]
        if not content:
            content = [{"type": "text", "text": "[Model reasoning omitted.]"}]
        committed.append({"role": "assistant", "content": content})
    return _elide_figure_tool_inputs(elide_all_pdf_sources(committed))


def _with_tail_cache_breakpoint(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Copy-on-write a cache breakpoint onto the request's last block.

    Marks the incremental prefix (tools + system are covered by the stable
    block's own breakpoint): each continuation round extends the previous
    round's cache; each new turn re-writes only the previous turn's
    exchange (the strip at commit shifts those bytes). Stored history is
    never mutated — the breakpoint rides a per-request copy.
    """
    if not messages:
        return messages
    last = messages[-1]
    content = last.get("content")
    if not isinstance(content, list) or not content:
        return messages
    tail = content[-1]
    if not isinstance(tail, dict) or tail.get("type") in _TRANSIENT_BLOCK_TYPES:
        return messages
    new_tail = dict(tail)
    new_tail["cache_control"] = {"type": "ephemeral"}
    return [
        *messages[:-1],
        {**last, "content": [*content[:-1], new_tail]},
    ]


def _merge_usage(totals: dict[str, int], usage: Any) -> None:
    """Accumulate one response's billed usage into the turn totals."""
    if usage is None:
        return
    for key in (
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    ):
        value = getattr(usage, key, None)
        if isinstance(value, (int, float)) and value:
            totals[key] = totals.get(key, 0) + int(value)
    details = getattr(usage, "output_tokens_details", None)
    thinking = getattr(details, "thinking_tokens", None) if details else None
    if isinstance(thinking, (int, float)) and thinking:
        totals["thinking_tokens"] = totals.get("thinking_tokens", 0) + int(
            thinking
        )
    server = getattr(usage, "server_tool_use", None)
    for key in ("web_search_requests", "web_fetch_requests"):
        value = getattr(server, key, None) if server else None
        if isinstance(value, (int, float)) and value:
            totals[key] = totals.get(key, 0) + int(value)


# --- Streaming-event translation (WI1: buttery-smooth streaming UX) ----------
#
# The interview streams raw SDK events instead of the text-only
# ``text_stream``, so the UI sees everything the model is doing the moment it
# happens: adaptive-thinking summaries, drafting progress on a long edit
# batch, and web lookups the instant they fire — never a silent pause, never
# a post-hoc "🔍 Searched…" chip that lands after the search is over. ``status``
# frames are transient UI hints (not persisted to history/traces/project
# files); ``text_delta``/``thinking_delta`` clear the strip.

# Runaway guard on drafting-progress frames: at most one per this interval,
# so a 40-op batch streams a handful of "drafting… 2.4k" pulses, not a flood.
_DRAFT_PROGRESS_INTERVAL_S = 0.25


def _safe_json(text: str) -> dict[str, Any]:
    """Parse an accumulated tool-input JSON fragment; ``{}`` on garbage."""
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _stream_events(stream: Any) -> Iterator[dict[str, Any]]:
    """Translate one round's raw stream events into UI event dicts.

    Yields ``status`` hints on block starts (thinking/writing/drafting/
    searching/fetching), ``text_delta``/``thinking_delta`` on content deltas,
    throttled drafting ``progress_chars`` as an edit batch's JSON streams,
    and LIVE ``web_search``/``web_fetch`` events the instant a server-tool
    block's input finishes (``content_block_stop``) — not derived after the
    round from the final message. Empty thinking deltas (``display:
    omitted``) are dropped so they don't prematurely clear the status strip.
    """
    json_buffers: dict[int, str] = {}
    block_kinds: dict[int, tuple[str, str]] = {}
    last_progress = time.monotonic()
    for event in stream:
        etype = getattr(event, "type", None)
        if etype == "content_block_start":
            block = getattr(event, "content_block", None)
            index = getattr(event, "index", 0)
            btype = getattr(block, "type", None) or ""
            bname = getattr(block, "name", "") or ""
            block_kinds[index] = (btype, bname)
            json_buffers[index] = ""
            if btype == "thinking":
                yield {"type": "status", "kind": "thinking"}
            elif btype == "text":
                yield {"type": "status", "kind": "writing"}
            elif btype == "tool_use" and bname == "apply_spec_edits":
                yield {"type": "status", "kind": "drafting", "progress_chars": 0}
            elif btype == "tool_use" and bname == "create_figure":
                yield {"type": "status", "kind": "drawing"}
            elif btype == "server_tool_use" and bname == "web_search":
                yield {"type": "status", "kind": "searching"}
            elif btype == "server_tool_use" and bname == "web_fetch":
                yield {"type": "status", "kind": "fetching"}
        elif etype == "content_block_delta":
            delta = getattr(event, "delta", None)
            dtype = getattr(delta, "type", None)
            index = getattr(event, "index", 0)
            if dtype == "text_delta":
                text = getattr(delta, "text", "") or ""
                if text:
                    yield {"type": "text_delta", "text": text}
            elif dtype == "thinking_delta":
                text = getattr(delta, "thinking", "") or ""
                if text:
                    yield {"type": "thinking_delta", "text": text}
            elif dtype == "input_json_delta":
                json_buffers[index] = json_buffers.get(index, "") + (
                    getattr(delta, "partial_json", "") or ""
                )
                if block_kinds.get(index) == ("tool_use", "apply_spec_edits"):
                    now = time.monotonic()
                    if now - last_progress >= _DRAFT_PROGRESS_INTERVAL_S:
                        last_progress = now
                        yield {
                            "type": "status",
                            "kind": "drafting",
                            "progress_chars": len(json_buffers[index]),
                        }
        elif etype == "content_block_stop":
            index = getattr(event, "index", 0)
            btype, bname = block_kinds.get(index, ("", ""))
            if btype != "server_tool_use":
                continue
            payload = _safe_json(json_buffers.get(index, ""))
            if bname == "web_search":
                yield {"type": "web_search", "query": str(payload.get("query", ""))}
            elif bname == "web_fetch":
                yield {"type": "web_fetch", "url": str(payload.get("url", ""))}


# thinking.display capability probe. Sonnet 5 accepts ``summarized``; a model
# or endpoint that rejects the ``display`` key 400s once, after which the
# whole process degrades to ``omitted`` (remembered, never re-probed). Reset
# between hermetic tests via :func:`reset_thinking_display_probe`.
_display_probe_disabled = False


def reset_thinking_display_probe() -> None:
    """Re-arm the thinking.display probe (tests; a fresh process)."""
    global _display_probe_disabled
    _display_probe_disabled = False


def _thinking_param() -> dict[str, Any]:
    """The adaptive-thinking request param, with display when supported."""
    thinking: dict[str, Any] = {"type": "adaptive"}
    if not _display_probe_disabled and settings.THINKING_DISPLAY == "summarized":
        thinking["display"] = "summarized"
    return thinking


def _enter_stream(
    client: Any, kwargs: dict[str, Any], trace_handle: Any = None
) -> tuple[Any, Any]:
    """Open + enter a message stream, degrading thinking.display once on 400.

    The request fires when the stream context is entered, so a rejected
    ``display`` key surfaces here; we retry the same round without it,
    remember the degrade for the process, and note it in the trace.
    """
    global _display_probe_disabled
    try:
        manager = client.messages.stream(**kwargs)
        return manager, manager.__enter__()
    except anthropic.BadRequestError:
        thinking = kwargs.get("thinking") or {}
        if _display_probe_disabled or "display" not in thinking:
            raise
        _display_probe_disabled = True
        _trace.note(
            trace_handle,
            "thinking.display rejected; degraded to omitted for this session",
        )
        kwargs = {**kwargs, "thinking": {"type": "adaptive"}}
        manager = client.messages.stream(**kwargs)
        return manager, manager.__enter__()


def _run_create_figure(
    session: SessionState,
    block: dict[str, Any],
    *,
    message_index: int,
    trace_handle: Any = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Execute one ``create_figure`` tool_use block.

    On success the tool result echoes only id/kind/title (NOT the source —
    the source never re-enters the model's context), and a ``figure`` UI
    event carries the full figure to the chat for inline rendering. A bad
    payload becomes an ``is_error`` result the model can correct.
    """
    try:
        figure = session.figures.create(
            block.get("input") or {}, message_index=message_index
        )
    except FigureError as exc:
        return (
            {
                "type": "tool_result",
                "tool_use_id": block.get("id"),
                "content": f"create_figure rejected (nothing was created): {exc}",
                "is_error": True,
            },
            [],
        )
    _trace.note(trace_handle, f"created figure {figure.fid} ({figure.kind})")
    result = {
        "type": "tool_result",
        "tool_use_id": block.get("id"),
        "content": json.dumps(
            {
                "created": {
                    "fid": figure.fid,
                    "kind": figure.kind,
                    "title": figure.title,
                }
            },
            ensure_ascii=False,
        ),
    }
    return result, [{"type": "figure", "figure": figure.to_dict()}]


def _run_suggest_prompts(
    block: dict[str, Any], trace_handle: Any = None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Execute one ``suggest_prompts`` tool_use block.

    The tool result is deliberately compact (``{"suggested": N}`` — token
    discipline); the validated list travels in the ``suggested_prompts`` UI
    event, which ``stream_user_turn`` also stages (committed only on turn
    success, replacing the previous set). A bad payload becomes an
    ``is_error`` result the model can correct — never a turn failure.
    """
    try:
        prompts = validate_prompts(block.get("input") or {})
    except SuggestError as exc:
        return (
            {
                "type": "tool_result",
                "tool_use_id": block.get("id"),
                "content": f"suggest_prompts rejected (nothing was staged): {exc}",
                "is_error": True,
            },
            [],
        )
    _trace.note(trace_handle, f"staged {len(prompts)} suggested prompt(s)")
    return (
        {
            "type": "tool_result",
            "tool_use_id": block.get("id"),
            "content": json.dumps({"suggested": len(prompts)}),
        },
        [{"type": "suggested_prompts", "prompts": prompts}],
    )


def _run_tool(
    session: SessionState,
    block: dict[str, Any],
    trace_handle: Any = None,
    *,
    message_index: int = 0,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Execute one (serialized) tool_use block.

    Returns ``(tool_result_block, ui_events)``. Tool failures become
    ``is_error`` results for the model to correct — they never abort the
    turn.
    """
    name = block.get("name")
    if name == "create_figure":
        return _run_create_figure(
            session, block, message_index=message_index, trace_handle=trace_handle
        )
    if name == "suggest_prompts":
        return _run_suggest_prompts(block, trace_handle)
    if name != "apply_spec_edits":
        return (
            {
                "type": "tool_result",
                "tool_use_id": block.get("id"),
                "content": f"Unknown tool: {name}",
                "is_error": True,
            },
            [],
        )
    edits = (block.get("input") or {}).get("edits")
    try:
        applied = session.apply_doc_edits(edits)
    except SpecEditError as exc:
        _trace.tool_dispatch(
            trace_handle,
            ops=len(edits) if isinstance(edits, list) else 0,
            ok=False,
            error=str(exc),
        )
        return (
            {
                "type": "tool_result",
                "tool_use_id": block.get("id"),
                "content": (
                    f"Edit batch rejected (nothing was applied): {exc}\n\n"
                    "Current specification document:\n"
                    + outline(session.doc.doc)
                ),
                "is_error": True,
            },
            [],
        )
    _trace.tool_dispatch(trace_handle, ops=len(applied), ok=True)
    result = {
        "type": "tool_result",
        "tool_use_id": block.get("id"),
        "content": json.dumps(
            {"applied": applied, "outline": outline(session.doc.doc)},
            ensure_ascii=False,
        ),
    }
    patch = {
        "type": "doc_patch",
        "ops": applied,
        "doc": session.doc.snapshot(),
    }
    return result, [patch]


def stream_user_turn(
    session: SessionState,
    user_text: str,
    *,
    model: str | None = None,
    max_tokens: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Run one user turn against the model, yielding UI event dicts.

    Event order: transient ``status`` hints (working/thinking/writing/
    drafting/searching/fetching) and ``thinking_delta`` summaries interleave
    with ``text_delta`` chunks across every continuation round; live
    ``web_search``/``web_fetch`` events fire the instant a server-tool call
    completes; ``doc_patch`` follows each applied edit batch and ``figure``
    each created figure; a ``suggested_prompts`` event carries the reply
    chips the model staged this turn. Then — on success — ``open_questions``
    and ``lint`` (if the document changed) and ``turn_complete``, which
    carries the turn's aggregated billed usage.
    ``status`` frames are transient UI hints, never persisted. Any failure
    yields a single ``error`` event, rolls the document back, and leaves
    history unchanged. A user-initiated stop (``session.stop_requested``) is
    not a failure: it ends the round loop early but still commits — history
    and the document keep whatever was produced before the click — and the
    turn still ends in a normal ``turn_complete``.
    """
    user_text = (user_text or "").strip()
    if not user_text:
        yield {"type": "error", "message": "Empty message."}
        return

    # The PROJECT CONTEXT renders once, at turn start: mid-turn document
    # changes reach the model through tool results, and a frozen block
    # keeps the request prefix byte-stable across continuation rounds.
    claim = session.claim_model_turn()
    if claim is None:
        yield {
            "type": "error",
            "message": "A model turn is already streaming.",
        }
        return
    turn_token, generation = claim

    try:
        context_text = _turn_context_text(session)
    except Exception as exc:  # noqa: BLE001 - startup is transactional
        session.release_model_turn(turn_token)
        yield {"type": "error", "message": f"Unexpected error: {exc}"}
        return
    new_messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": context_text},
                {"type": "text", "text": user_text},
            ],
        }
    ]
    # Ordinal of the assistant chat bubble this turn will become — used to
    # re-inline figures into the right bubble when a saved project reloads
    # (the transcript merges a turn's assistant text into one bubble, so all
    # of a turn's figures share this index).
    try:
        message_index = sum(
            1
            for entry in chat_transcript(session.history)
            if entry["role"] == "assistant"
        )
    except Exception as exc:  # noqa: BLE001 - startup is transactional
        session.release_model_turn(turn_token)
        yield {"type": "error", "message": f"Unexpected error: {exc}"}
        return

    try:
        stores_started = session.begin_model_turn_stores(
            turn_token,
            generation,
        )
        if not stores_started:
            yield {
                "type": "error",
                "message": "The session was reset while this turn was "
                "starting; the turn was discarded.",
            }
            return
        trace_handle = _trace.turn_start(
            model=model or settings.INTERVIEW_MODEL,
            history_len=len(session.history),
        )
    except Exception as exc:  # noqa: BLE001 - initialization is transactional
        session.finalize_model_turn(turn_token, committed=False)
        yield {"type": "error", "message": f"Unexpected error: {exc}"}
        return

    def check_session() -> None:
        with session.owned_model_turn_guard(
            turn_token,
            generation,
        ) as owns_turn:
            if not owns_turn:
                raise _SessionInvalidated(
                    "The session was reset while this turn was streaming; "
                    "the turn was discarded."
                )

    def request_kwargs() -> dict[str, Any]:
        messages = sanitize_messages_for_resend(
            list(session.history) + new_messages
        )
        return {
            "model": model or settings.INTERVIEW_MODEL,
            "max_tokens": max_tokens or settings.INTERVIEW_MAX_TOKENS,
            "system": _stable_system_blocks(session),
            "messages": _with_tail_cache_breakpoint(messages),
            "tools": _chat_tools(),
            "thinking": _thinking_param(),
            "output_config": {"effort": settings.INTERVIEW_EFFORT},
        }

    stop_reason: str | None = None
    doc_changed = False
    committed = False
    post_commit_events: list[dict[str, Any]] = []
    usage_totals: dict[str, int] = {}
    # Turn-local staging for suggested-reply chips: the dispatch loop records
    # each suggest_prompts call here (latest wins); a successful turn commits
    # it into session.suggested_prompts, a failed turn drops it with the
    # generator. Initializes to [] so a turn that never calls the tool
    # commits an empty set — that "no call = clear" rule is the wind-down.
    staged_suggestions: list[str] = []
    try:
        client = get_client()
        resumed_from_pause = False
        for _round in range(MAX_TOOL_ROUNDS):
            check_session()
            if session.stop_requested.is_set():
                # Caught between rounds (e.g. right after a tool dispatch, or
                # before round 0 even started) rather than mid-stream: end the
                # turn now with whatever prior rounds produced. The message
                # list must still end on an assistant turn — a dangling
                # tool_result (or, at round 0, nothing at all) would leave two
                # consecutive user-role messages once the next turn's message
                # is appended, which the API rejects.
                stop_reason = "user_stop"
                if new_messages[-1].get("role") != "assistant":
                    new_messages.append(
                        {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "[Generation stopped by user.]",
                                }
                            ],
                        }
                    )
                break
            # Never dead air between rounds: from send to first token there is
            # always a live status. A pause_turn resume keeps server work
            # visible as "searching" rather than a generic "working".
            yield {
                "type": "status",
                "kind": "searching" if resumed_from_pause else "working",
                "round": _round,
            }
            resumed_from_pause = False
            with session.owned_model_turn_guard(
                turn_token,
                generation,
            ) as owns_turn:
                if not owns_turn:
                    raise _SessionInvalidated(
                        "The session was reset while this turn was streaming; "
                        "the turn was discarded."
                    )
                request = request_kwargs()
            manager, stream = _enter_stream(
                client, request, trace_handle
            )
            stopped_mid_stream = False
            try:
                for ui_event in _stream_events(stream):
                    yield ui_event
                    if session.stop_requested.is_set():
                        stopped_mid_stream = True
                        break
                # A stop closes the request now — draining the rest via
                # get_final_message() would keep paying for tokens the UI
                # already stopped showing. current_message_snapshot holds
                # exactly what was accumulated from the events seen so far.
                final = (
                    stream.current_message_snapshot
                    if stopped_mid_stream
                    else stream.get_final_message()
                )
            finally:
                manager.__exit__(None, None, None)

            _merge_usage(usage_totals, getattr(final, "usage", None))
            content = _content_blocks_to_dicts(final.content)
            stop_reason = "user_stop" if stopped_mid_stream else final.stop_reason

            if stop_reason == "pause_turn":
                # Long server-tool work paused server-side: re-send the
                # assistant content verbatim (per the pause_turn contract,
                # thinking blocks included) and stream again — no
                # synthetic user turn, no tool results.
                new_messages.append({"role": "assistant", "content": content})
                resumed_from_pause = True
                continue
            if stop_reason != "tool_use":
                # A truncated response (max_tokens, or a user stop) can still
                # carry a tool_use block (whole or mid-input-JSON); committing
                # one without a tool_result would make every later request
                # invalid, so it's dropped. An empty/whitespace-only text
                # block left over from a stop clicked before any real content
                # arrived is dropped too, rather than committing blank text.
                fallback = (
                    "[Generation stopped by user.]"
                    if stop_reason == "user_stop"
                    else "[Response was cut off before completion.]"
                )
                content = [
                    b
                    for b in content
                    if b.get("type") != "tool_use"
                    and not (
                        b.get("type") == "text" and not b.get("text", "").strip()
                    )
                ] or [{"type": "text", "text": fallback}]
                new_messages.append({"role": "assistant", "content": content})
                break
            new_messages.append({"role": "assistant", "content": content})

            tool_results: list[dict[str, Any]] = []
            for block in content:
                if block.get("type") != "tool_use":
                    continue
                with session.owned_model_turn_guard(
                    turn_token,
                    generation,
                ) as owns_turn:
                    if not owns_turn:
                        raise _SessionInvalidated(
                            "The session was reset while this turn was "
                            "streaming; the turn was discarded."
                        )
                    result, ui_events = _run_tool(
                        session,
                        block,
                        trace_handle,
                        message_index=message_index,
                    )
                tool_results.append(result)
                for event in ui_events:
                    if event.get("type") == "suggested_prompts":
                        # Turn-local staging: committed on turn success only
                        # (latest call in a turn wins by reassignment; a
                        # failed turn drops this local untouched).
                        staged_suggestions = list(event["prompts"])
                    yield event
            new_messages.append({"role": "user", "content": tool_results})
        else:
            raise RuntimeError(
                f"Turn exceeded {MAX_TOOL_ROUNDS} tool rounds; aborted."
            )
    except MissingApiKeyError as exc:
        yield {"type": "error", "message": str(exc)}
        return
    except _SessionInvalidated as exc:
        # The fresh/loaded session must stay exactly as the user made it —
        # nothing was applied after the generation change.
        yield {"type": "error", "message": str(exc)}
        return
    except anthropic.APIStatusError as exc:
        yield {
            "type": "error",
            "message": f"Anthropic API error ({exc.status_code}): {exc.message}",
        }
        return
    except anthropic.APIConnectionError:
        yield {
            "type": "error",
            "message": "Could not reach the Anthropic API. Check your connection and try again.",
        }
        return
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI, never raised
        yield {"type": "error", "message": f"Unexpected error: {exc}"}
        return
    else:
        commit_invalidated = False
        with session.owned_model_turn_guard(
            turn_token,
            generation,
        ) as owns_turn:
            if not owns_turn:
                commit_invalidated = True
            else:
                session.history.extend(
                    _committed_messages(new_messages, user_text)
                )
                doc_changed = session.doc.commit_turn()
                session.figures.commit_turn()
                # Latest-only replace: whatever this turn staged (including
                # []) becomes the current chip set. Failure paths never reach
                # this commit block, so the previous list remains untouched.
                session.suggested_prompts = staged_suggestions
                committed = True
                if doc_changed:
                    # Freeze the completion payload before releasing turn
                    # ownership; a concurrent undo/reset/new turn must not
                    # make this older SSE stream describe newer live state.
                    post_commit_events = [
                        {"type": "doc_snapshot", "doc": session.doc.snapshot()},
                        {
                            "type": "open_questions",
                            "items": open_questions(session.doc.doc),
                        },
                        {
                            "type": "lint",
                            "items": lint_document(
                                session.doc.doc,
                                session.module,
                            ),
                            "standards": standards_payload(session),
                        },
                    ]
        if commit_invalidated:
            # Reset/load won the race after the last round: leave the fresh
            # session untouched and discard this turn.
            yield {
                "type": "error",
                "message": "The session was reset while this turn was "
                "streaming; the turn was discarded.",
            }
            return
    finally:
        # Runs on every exit — including GeneratorExit when the SSE client
        # disconnects mid-stream, which no except clause above can see.
        # Anything short of a committed turn rolls the document back.
        # The spend is real even on a failed turn — record it (unless a
        # reset/load raced in, whose fresh ledger must not inherit it).
        with session.owned_model_turn_guard(
            turn_token,
            generation,
        ) as owns_turn:
            if owns_turn:
                session.usage.add("interview", usage_totals, count_turn=True)
        # Failed/disconnected turns must settle immediately. A committed turn
        # retains logical ownership through its frozen document-tail events so
        # a newer model turn cannot start and then be overwritten in the UI by
        # this older stream. The committed path releases below.
        if not committed:
            session.finalize_model_turn(
                turn_token,
                committed=False,
            )
        if not committed:
            _trace.turn_end(
                trace_handle,
                stop_reason=stop_reason,
                doc_changed=False,
                usage=usage_totals,
                error="turn did not commit (failure or disconnect)",
            )
        else:
            _trace.turn_end(
                trace_handle,
                stop_reason=stop_reason,
                doc_changed=doc_changed,
                usage=usage_totals,
            )

    # doc_patch snapshots stream mid-turn, before the version commit; this
    # frozen batch carries the committed pointer and same-generation reports.
    # Reset/load may still invalidate an owner deliberately, so recheck before
    # every tail event and stop emitting as soon as that happens.
    try:
        for event in post_commit_events:
            with session.owned_model_turn_guard(
                turn_token,
                generation,
            ) as owns_turn:
                if not owns_turn:
                    return
            yield event
    finally:
        session.finalize_model_turn(
            turn_token,
            committed=True,
        )
    with session.session_state_guard():
        if session.generation != generation:
            return
    yield {
        "type": "turn_complete",
        "stop_reason": stop_reason,
        "usage": usage_totals,
    }


def standards_payload(session: SessionState) -> list[dict[str, Any]]:
    """UI-shaped list of the editions in effect (pins + overrides + adds).

    Live rows carry ``is_override``/``is_added``; standards the project has
    excluded are appended as ``is_suppressed`` rows (with the recorded
    reason and the pin's display edition/title) so the panel can show them
    struck-through with a Restore control.
    """
    from ..standards import effective_editions

    doc = session.doc.doc
    basis = session.module.basis
    rows: list[dict[str, Any]] = [
        {
            "name": eff.name,
            "edition": eff.edition,
            "title": eff.title,
            "is_override": eff.is_override,
            "is_added": eff.is_added,
            "basis": eff.basis,
            "is_suppressed": False,
            "reason": "",
        }
        for eff in effective_editions(
            basis, doc.edition_overrides, doc.suppressed_standards
        )
    ]
    for name, reason in sorted(doc.suppressed_standards.items()):
        pin = basis.standard(name)
        rows.append(
            {
                "name": pin.name if pin else name,
                "edition": pin.edition if pin else "",
                "title": pin.title if pin else "",
                "is_override": False,
                "is_added": False,
                "basis": "",
                "is_suppressed": True,
                "reason": reason,
            }
        )
    return rows
