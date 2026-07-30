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
block spliced into the newest user message instead. At commit that spliced
context (and the turn's thinking blocks, plus any fetched-PDF payloads)
are stripped from the stored history — each request carries exactly one,
current, state block.

Cache layout (three breakpoints, one TTL)
-----------------------------------------
1. the stable system block, which also closes the tools+system prefix;
2. the **committed-history boundary** — the last block of the last message
   in ``session.history``; and
3. the tail of the full request, covering the fresh PROJECT CONTEXT and
   any continuation rounds.

Breakpoint 2 is the one that makes caching roll. A tail breakpoint alone
cannot: the entry it writes is keyed on a prefix that ENDS with that
turn's PROJECT CONTEXT, and commit strips exactly those bytes, so the next
turn's prefix diverges where the context block used to be and nothing
matches — the whole conversation re-bills as fresh input, every turn. The
committed boundary is keyed on the stripped, re-sent form instead, so each
turn's entry is a byte-prefix of the next turn's request: turn N+1 reads
everything turn N wrote and writes only the newest exchange.

All three share one TTL (``settings.CHAT_CACHE_TTL``, one hour by
default). That is not a preference — the provider requires longer-lived
entries to precede shorter-lived ones in prompt order, and a mixed-TTL
request is a nonretryable 400. Stored history never carries
``cache_control``; the annotations ride a per-request copy.

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
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

import anthropic

from .. import settings
from ..figures import CREATE_FIGURE_TOOL, FigureError, FigureStore
from ..reference_docs import (
    READ_REFERENCE_DOC_TOOL,
    ReferenceDocStore,
    TurnReferenceBudget,
)
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
    CAPABILITY_STATUS_PENDING,
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
from ..research.grounding import response_container_id
from .server_tool_pairing import (
    without_unpaired_server_tool_uses as _without_unpaired_server_tool_uses,
)
from ..research.resend_sanitizer import (
    elide_all_pdf_sources,
    sanitize_messages_for_resend,
)
from ..research.schema import build_web_fetch_tool, build_web_search_tool
from ..runtime_context import date_context_block
from ..suggestions import SUGGEST_PROMPTS_TOOL, SuggestError, validate_prompts
from ..tracing import capture as _trace
from ..spec_modules import SpecModule, get_module
from ..standards import standards_context_block
from ..usage_ledger import (
    ESTIMATED_OUTPUT_TOKENS_KEY,
    USAGE_ESTIMATED_KEY,
    UsageLedger,
)
from .client import AUTH_ERROR_MESSAGE, MissingApiKeyError, get_client
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
    ``suggest_prompts`` and then ``read_reference_doc`` are appended LAST, in
    that order, so each addition leaves the existing tool bytes intact as a
    stable cached prefix.

    The two web tools come from the shared builders, which pin
    ``allowed_callers: ["direct"]`` (see
    ``research/schema.WEB_TOOL_ALLOWED_CALLERS``) — the interview, research,
    and Final QC all invoke them the same way.
    """
    return [
        APPLY_SPEC_EDITS_TOOL,
        CREATE_FIGURE_TOOL,
        build_web_search_tool(max_uses=settings.CHAT_MAX_SEARCHES),
        build_web_fetch_tool(max_uses=settings.CHAT_MAX_FETCHES),
        SUGGEST_PROMPTS_TOOL,
        READ_REFERENCE_DOC_TOOL,
    ]


def _same_capability_state(a: tuple[Any, ...], b: tuple[Any, ...]) -> bool:
    """Whether two capability state keys describe the same document state.

    ``(source_bytes, source_map, baseline_index, projection)``. The retained
    artifacts are compared by IDENTITY, not by their declared hashes: keying
    on the source map's *claimed* source hash would miss a swapped or mutated
    artifact, which must still fail closed. Holding the objects in the key
    keeps that identity sound for as long as the key lives.
    """
    return (
        a[0] is b[0]
        and a[1] is b[1]
        and a[2] == b[2]
        and a[3] == b[3]
    )


class _CapabilityWarm:
    """One in-flight background capability sweep and the state it analyzes.

    The sweep probes every element against the real gate, so it is expensive
    in body size (see ``SessionState.source_edit_capabilities``). Running it
    on a background thread keeps it off the request paths that used to wait
    on it — most importantly the first frame of a chat turn.
    """

    __slots__ = ("key", "section", "done")

    def __init__(self, key: tuple[Any, ...], section: SpecSection) -> None:
        self.key = key
        # The immutable tree this sweep analyzes. Reading the live document
        # instead would let a streaming turn's provisional edits be swept and
        # then, if that turn rolled back, published under this key.
        self.section = section
        self.done = threading.Event()


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
    # The fail-closed report served while a sweep is running, memoized on the
    # same state key so the panel's poll does not rebuild a per-element map
    # every second. Derived state, like the memo above.
    _pending_capability_cache: tuple[tuple[Any, ...], Any] | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    # The background sweep that fills ``_capability_cache`` for a state the
    # memo does not cover yet, and the single state queued behind it (newest
    # request wins — see ``start_capability_warm``). Derived, process-local,
    # never serialized; cleared with the memo on reset/load.
    _capability_warm: "_CapabilityWarm | None" = field(
        default=None,
        repr=False,
        compare=False,
    )
    _capability_warm_next: Any = field(default=None, repr=False, compare=False)
    _capability_warm_lock: Any = field(
        default_factory=threading.Lock,
        repr=False,
        compare=False,
    )
    # Sanitized, JSON-safe import diagnostics do ride the project file so a
    # resumed session still tells the truth about normalization and loss.
    import_report: dict[str, Any] | None = None
    # Optional provenance for a native document seeded from a reusable
    # template.  This is intentionally separate from source-DOCX provenance:
    # template content is reviewable starter material, but never enables the
    # source-preserving export/edit path.
    template_origin: dict[str, Any] | None = None
    generation: int = 0
    module: SpecModule = field(default_factory=lambda: get_module(None))
    # Legacy session-level discipline, meaningful only with an open-catalog
    # module. New work records versioned ``doc.project_identity`` instead;
    # this remains the fallback for old project files and API clients.
    discipline: str = ""
    # Optional legacy project primer (free text, sanitized). The current UI no
    # longer collects it, but old project files/API clients remain compatible.
    # It is cleared on reset so one project's description cannot bleed into
    # the next.
    project_context: str = ""
    research: ResearchRunner = field(default_factory=ResearchRunner)
    audit: AuditRunner = field(default_factory=AuditRunner)
    # Final QC on Opus 5. Replaced on reset/load like the other
    # runners so an in-flight run settles into the abandoned object.
    qc: QCRunner = field(default_factory=QCRunner)
    # Chat-authored figures (diagrams/schematics/tables). Like the document
    # store it is reset in place (never reassigned) so a zombie turn's
    # commit/rollback settles harmlessly against the cleared store.
    figures: FigureStore = field(default_factory=FigureStore)
    # User-attached reference documents the model reads FROM (never edits).
    # Not turn-atomic — they arrive through REST, not through a turn — so
    # this needs no begin/commit/rollback, only an in-place reset.
    references: ReferenceDocStore = field(default_factory=ReferenceDocStore)
    # Suggested-reply chips staged by the model (Batch 9). Turn-atomic,
    # latest-only: each committed turn REPLACES this with what it staged —
    # including [] when the tool was not called, which is how the bar winds
    # down as the section nears issue-ready. A failed turn leaves it
    # untouched (staging is a turn-local in stream_user_turn, not a store).
    suggested_prompts: list[str] = field(default_factory=list)
    # Session-scoped billed-usage meter (WI4). Reset/load clear it.
    usage: UsageLedger = field(default_factory=UsageLedger)
    # Context gauge, not spend (which is why it lives here and not in the
    # ledger — the ledger's snapshot/merge tutorial plumbing is additive and
    # would corrupt a gauge): the Anthropic-counted conversation size after
    # the last committed turn — its final request's full prompt (input +
    # cache write + cache read) plus that reply's retained non-thinking
    # output. None until a turn commits; cleared by reset and project load.
    # Written only inside the generation-guarded commit block, so a zombie
    # turn can never populate a fresh session.
    last_context_tokens: int | None = None
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

    def import_is_unstructured(self) -> bool:
        """True when the ACTIVE document came from an import with no
        SectionFormat structure.

        The document tree is SectionFormat regardless — the whole edit / lint
        / diff / QC machinery is typed to it. This says only that the section
        header, the three parts, and the synthetic article around the content
        were supplied by the importer rather than by the file, so the app must
        not present them as the document's own. A session with no import, or a
        legacy project written before shape detection, reads False.

        Scoped to the imported baseline still being live, because the verdict
        describes *that* tree. Undoing past the import and editing truncates
        it — ``DocumentStore.commit_turn`` drops ``baseline_index`` when the
        version it points at is abandoned — and what remains is a from-scratch
        draft that must get the normal spec presentation back: its ``[TBD]``
        header placeholders, its missing-header lint, and no claim to the
        model that it was ever a non-spec upload. ``import_report`` itself is
        deliberately retained through all of that as the session's honesty
        trail, so it cannot answer this question alone.
        """
        report = self.import_report
        if not isinstance(report, dict):
            return False
        if report.get("spec_shape_detected") is not False:
            return False
        # Same liveness test as sessions._portable_source_attachment.
        baseline_index = self.doc.baseline_index
        if isinstance(baseline_index, bool) or not isinstance(
            baseline_index, int
        ):
            return False
        return 0 <= baseline_index < len(self.doc.versions)

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

    def delete_reference_if_idle(
        self, rid: str
    ) -> tuple[str, list[dict[str, Any]]]:
        """Delete a reference and forget every turn that could remember it.

        Reference bodies are elided after a turn, but the assistant's answer
        can still summarize or quote what it read.  Once a user removes an
        attachment, keeping that answer (or any later turn that saw it) in
        model history would therefore keep the document in model context.
        Truncate at the user turn that first successfully read the reference;
        the UI may retain its already-rendered transcript, while subsequent
        model requests and saved projects cannot reintroduce that material.
        """
        with self._turn_state_lock:
            if self.turn_active:
                return "active", []
            if self.references.get(rid) is None:
                return "missing", []

            successful_read_ids = {
                block.get("tool_use_id")
                for message in self.history
                if message.get("role") == "user"
                for block in (message.get("content") or [])
                if block.get("type") == "tool_result"
                and not block.get("is_error")
            }
            first_read: int | None = None
            for index, message in enumerate(self.history):
                if message.get("role") != "assistant":
                    continue
                if any(
                    block.get("type") == "tool_use"
                    and block.get("name") == "read_reference_doc"
                    and (block.get("input") or {}).get("ref_id") == rid
                    and block.get("id") in successful_read_ids
                    for block in (message.get("content") or [])
                ):
                    first_read = index
                    break

            if first_read is not None:
                # Tool-result messages also have role=user.  A real user turn
                # is the nearest preceding user message with a text block.
                turn_start = 0
                for index in range(first_read - 1, -1, -1):
                    message = self.history[index]
                    if message.get("role") == "user" and any(
                        block.get("type") == "text"
                        for block in (message.get("content") or [])
                    ):
                        turn_start = index
                        break
                discarded_message_index = sum(
                    1
                    for entry in chat_transcript(self.history[:turn_start])
                    if entry["role"] == "assistant"
                )
                del self.history[turn_start:]
                # Both are model-authored context from the discarded tail.
                # Keeping either would leak reference-derived material into
                # the UI/save payload, and stale figure message indices could
                # later attach an old figure to an unrelated chat bubble.
                self.suggested_prompts.clear()
                self.figures.delete_from_message_index(
                    discarded_message_index
                )

            self.references.delete(rid)
            return "deleted", self.references.snapshot()

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

    def validate_source_backed_candidate(
        self,
        candidate: SpecSection,
        *,
        current: SpecSection | None = None,
    ) -> None:
        """Read-only source-preservation gate for a complete candidate.

        ``apply_doc_edits`` and Final-QC remediation preview share this exact
        combined-document validation. Supplying ``current`` lets a caller
        validate an immutable snapshot built outside the live store; the
        caller remains responsible for a coherence check before using the
        answer.
        """
        if not self._active_source_scope():
            return
        before = current if current is not None else self.doc.doc
        # Metadata is workspace state, not source-body XML. Decide that
        # exemption from the complete before/after semantic projections,
        # never from action names, so a mixed batch still enters the full
        # source gate whenever its final body would differ.
        body_changed = semantic_body_projection(
            candidate
        ) != semantic_body_projection(before)
        if not body_changed:
            return
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
            self.validate_source_backed_candidate(candidate)
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

    def source_edit_capabilities(
        self,
        *,
        block: bool = False,
    ) -> SourceCapabilityReport | None:
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
        key, so the cache can never outlive the state it describes; a change
        to provenance metadata deliberately does not, exactly as
        ``semantic_body_projection`` excludes it from the source gate itself.

        A memo MISS no longer sweeps inline. The sweep is O(document) work
        per probe and runs ~5 probes per paragraph, so on a large imported
        master it takes minutes; every caller that waited on it made the
        whole app look hung — most visibly ``_source_editing_boundary_block``,
        which runs before a chat turn emits its first SSE frame. A miss now
        starts (or joins) ONE background sweep for that state and returns a
        ``pending`` report immediately. Pending denies every body operation,
        so the fail-closed rule is kept: an uncomputed permission can only
        make the UI more restrictive, never more permissive. Nothing is
        authorized by this report in any case —
        :meth:`apply_doc_edits` re-validates every complete proposed final
        state through the real source gate.

        ``block=True`` is for the callers that ACT on the answer rather than
        display it — starting a Final QC run, applying its fixes, and its two
        exports — which must reason from real permissions. It joins an
        in-flight warm rather than sweeping the same document twice. Those
        endpoints call it through ``app._settle_source_capabilities`` before
        taking the session guard, so no lock is ever held across a sweep.
        """
        if not self._active_source_scope():
            return None
        state_key = self._capability_lookup_key()
        if state_key is None:
            # An unkeyable document is never cached and never warmed; derive
            # it inline so the answer is still correct.
            return self._compute_source_edit_capabilities()

        hit = self._capability_memo_hit(state_key)
        if hit is not None:
            return hit

        if not block:
            self.start_capability_warm()
            return self._pending_capability_report(state_key)

        # Blocking caller: join an in-flight warm for this exact state rather
        # than sweeping the same document twice, then re-read the memo it
        # published. A warm that finished against a state which has since
        # moved leaves the memo untouched, so the fall-through below still
        # derives the current answer.
        self.capability_warm_settled(state_key=state_key)
        hit = self._capability_memo_hit(state_key)
        if hit is not None:
            return hit
        work = self._capability_work()
        if work is None:
            return self._compute_source_edit_capabilities()
        return self._sweep_and_publish(*work)

    def _capability_memo_hit(
        self,
        state_key: tuple[Any, ...],
    ) -> SourceCapabilityReport | None:
        """The memoized report for exactly ``state_key``, else ``None``.

        The stored key carries ``source_patch_context`` as well, compared by
        identity against the live one: the sweep builds and attaches it on
        first use, so a report derived against a different context must not
        be reused. See :func:`_same_capability_state` for why the retained
        artifacts are compared by identity rather than by declared hash.

        The version index is deliberately NOT part of the key. A metadata
        edit (``set_status``/``set_provenance``) commits a new version while
        leaving the semantic body — and therefore every source capability —
        untouched, and confirming blocks one at a time is exactly what the
        review walk does on an imported master. Keying on the index made each
        of those confirmations pay for a full sweep. What the report actually
        depends on is covered: ``_active_source_scope`` rejects a branch that
        undid past the import, ``baseline_index`` pins which version is the
        baseline, and ``DocumentStore`` never rewrites a retained version in
        place (``commit_turn`` only appends, truncates the redo tail, and
        drops ``baseline_index`` when that truncation would discard the
        version it points at).
        """
        cached = self._capability_cache
        if cached is None:
            return None
        cached_key, cached_report = cached
        if cached_key[2] is not self.source_patch_context:
            return None
        stored_state = (
            cached_key[0],
            cached_key[1],
            cached_key[3],
            cached_key[4],
        )
        return cached_report if _same_capability_state(stored_state, state_key) else None

    def _pending_capability_report(
        self,
        state_key: tuple[Any, ...],
    ) -> SourceCapabilityReport:
        """The fail-closed report served while the sweep is still running.

        Memoized on the same state key as the real report. It is a complete
        per-element map, so rebuilding it on every reader — the panel polls
        once a second while pending — would allocate thousands of records per
        request and steal CPU from the sweep being waited on. It depends only
        on the document's structure, which the key already pins.
        """
        cached = self._pending_capability_cache
        if cached is not None and _same_capability_state(cached[0], state_key):
            return cached[1]
        report = blocked_source_edit_capabilities(
            self.doc.doc,
            blocker="capabilities_pending",
            status=CAPABILITY_STATUS_PENDING,
        )
        self._pending_capability_cache = (state_key, report)
        return report

    def _sweep_and_publish(
        self,
        state_key: tuple[Any, ...],
        section: SpecSection | None = None,
    ) -> SourceCapabilityReport | None:
        """Run one full sweep of ``section`` and memoize it if it still fits.

        ``section`` is the tree ``state_key`` describes; the sweep analyzes
        exactly that, so the published report can never describe a different
        one. The guard below then answers a separate question: is that state
        still the live state, i.e. is it worth caching?
        """
        source_bytes, source_map, baseline_index, projection = state_key
        report = self._compute_source_edit_capabilities(section)

        # Read paths are not serialized against a streaming turn (``GET
        # /api/doc`` takes no session guard), so a long sweep can be overtaken
        # by a model turn's provisional edits. Publish only when the state the
        # sweep analyzed is still the live one; otherwise return the report
        # but leave the cache alone, so a subsequent rollback cannot resurrect
        # it against a document it was not derived from.
        # ``source_patch_context`` is read after the sweep on purpose — the
        # sweep builds and attaches it on first use.
        try:
            settled = semantic_body_projection_sha256(self.doc.doc)
        except Exception:  # noqa: BLE001 - unverifiable state is never cached
            return report
        if (
            settled == projection
            and self.source_docx_bytes is source_bytes
            and self.source_docx_map is source_map
            and self.doc.baseline_index == baseline_index
        ):
            self._capability_cache = (
                (
                    source_bytes,
                    source_map,
                    self.source_patch_context,
                    baseline_index,
                    projection,
                ),
                report,
            )
        return report

    def start_capability_warm(self) -> None:
        """Ensure a background sweep is running for the current state.

        **At most one sweep runs at a time.** The sweep cannot be interrupted
        — it is one opaque call into the source gate — so a superseded one
        would run to completion regardless, and its result is guaranteed to
        be discarded by the publish guard. Starting a thread per state would
        therefore stack minutes-long, useless O(n²) work: a streaming turn
        that commits ten provisional bodies while the panel polls once a
        second would leave ten sweeps fighting over one GIL, slowing the only
        one that can still publish. Instead a newer request replaces the
        *pending* one and is picked up when the running sweep finishes, so
        superseded states are skipped rather than swept.

        Idempotent for the same state, so the "one sweep per document state"
        invariant holds no matter how many readers ask. Called by the readers
        themselves on a memo miss, and directly by the import handler so the
        sweep starts while the response is still being written.
        """
        work = self._capability_work()
        if work is None:
            return
        state_key, section = work
        with self._capability_warm_lock:
            warm = self._capability_warm
            if warm is not None and not warm.done.is_set():
                # A sweep is in flight. Queue this state (newest wins) unless
                # it is the one already being swept.
                if not _same_capability_state(warm.key, state_key):
                    self._capability_warm_next = work
                return
            self._start_capability_warm_locked(work)

    def _start_capability_warm_locked(
        self,
        work: tuple[tuple[Any, ...], SpecSection],
    ) -> None:
        """Publish a new warm and its thread. Caller holds the warm lock."""
        state_key, section = work
        warm = _CapabilityWarm(state_key, section)
        self._capability_warm = warm
        self._capability_warm_next = None
        threading.Thread(
            target=self._run_capability_warm,
            args=(warm,),
            name="bas-capability-warm",
            daemon=True,
        ).start()

    def _capability_lookup_key(self) -> tuple[Any, ...] | None:
        """The live state's key, for memo lookups only — no tree copy.

        A reader only needs this to ask "is there already a report for what
        the document is right now". A sweep needs more (see
        ``_capability_work``): the tree it analyzes must be pinned, or the
        key it publishes under could describe a different one.
        """
        if not self._active_source_scope():
            return None
        try:
            projection = semantic_body_projection_sha256(self.doc.doc)
        except Exception:  # noqa: BLE001 - an unkeyable document is never cached
            return None
        return (
            self.source_docx_bytes,
            self.source_docx_map,
            self.doc.baseline_index,
            projection,
        )

    def _capability_work(
        self,
    ) -> tuple[tuple[Any, ...], SpecSection] | None:
        """A tree to sweep and the key that exactly describes it.

        Both come from ONE snapshot, so the key can never describe a
        different tree than the one analyzed — the snapshot is taken first
        and the projection hashed from it, not from the live document.
        """
        if not self._active_source_scope():
            return None
        section = SpecSection.from_dict(self.doc.doc.to_dict())
        try:
            projection = semantic_body_projection_sha256(section)
        except Exception:  # noqa: BLE001 - an unkeyable document is never warmed
            return None
        return (
            (
                self.source_docx_bytes,
                self.source_docx_map,
                self.doc.baseline_index,
                projection,
            ),
            section,
        )

    def _run_capability_warm(self, warm: "_CapabilityWarm") -> None:
        """Body of the background sweep thread; never raises."""
        try:
            self._sweep_and_publish(warm.key, warm.section)
        except Exception:  # noqa: BLE001 - a warm failure must not kill the app
            # ``_compute_source_edit_capabilities`` already fails closed for
            # every analysis error; anything reaching here is unexpected and
            # simply leaves the memo empty, so the next reader warms again.
            pass
        finally:
            # Release waiters first, then pick up whatever queued behind this
            # sweep. Setting ``done`` last would let a waiter observe the
            # replacement warm and wait on the wrong sweep.
            warm.done.set()
            with self._capability_warm_lock:
                queued = self._capability_warm_next
                if queued is not None and self._capability_warm is warm:
                    self._start_capability_warm_locked(queued)

    def capability_warm_settled(
        self,
        timeout: float | None = None,
        *,
        state_key: tuple[Any, ...] | None = None,
    ) -> bool:
        """Wait for the in-flight sweep to finish. False on timeout.

        With ``state_key``, waits only if the running sweep covers that exact
        state (the blocking readers' path — there is nothing to gain from
        waiting on a sweep of a document state nobody asked about). Without
        it, waits for whatever is running, which is what a caller that just
        wants a quiet session needs.
        """
        with self._capability_warm_lock:
            warm = self._capability_warm
            if warm is None or (
                state_key is not None
                and not _same_capability_state(warm.key, state_key)
            ):
                return True
        return warm.done.wait(timeout)

    def _compute_source_edit_capabilities(
        self,
        current: SpecSection | None = None,
    ) -> SourceCapabilityReport | None:
        """Derive the capability report from scratch (see the caller).

        ``current`` is the exact tree to analyze. A background sweep passes
        the immutable snapshot its key describes: reading the live document
        instead would let a streaming turn's provisional edits be analyzed
        and then, if that turn rolled back, published under the ORIGINAL key
        — a report describing a tree that never committed, memoized against
        one that did. This is the same anti-mutation snapshot the audit and
        Final QC passes take, for the same reason.
        """
        analyzed = self.doc.doc if current is None else current
        baseline_index = self.doc.baseline_index
        if baseline_index is None:
            return None
        if (
            isinstance(baseline_index, bool)
            or not isinstance(baseline_index, int)
            or not 0 <= baseline_index < len(self.doc.versions)
        ):
            return blocked_source_edit_capabilities(
                analyzed,
                blocker="baseline_unavailable",
                message="the imported semantic baseline is unavailable",
            )
        try:
            baseline = SpecSection.from_dict(self.doc.versions[baseline_index])
        except (TypeError, ValueError):
            return blocked_source_edit_capabilities(
                analyzed,
                blocker="baseline_unavailable",
                message="the imported semantic baseline is unavailable",
            )

        if (
            not isinstance(self.source_docx_bytes, bytes)
            or not isinstance(self.source_docx_map, SourceBodyMap)
        ):
            return blocked_source_edit_capabilities(
                analyzed,
                blocker="source_unavailable",
                message=(
                    "the exact imported DOCX and source map are unavailable"
                ),
            )

        try:
            context = self.ensure_source_patch_context(baseline=baseline)
            if context is None:
                return blocked_source_edit_capabilities(
                    analyzed,
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
                current=analyzed,
            )
        except SourcePatchError as exc:
            return blocked_source_edit_capabilities(
                analyzed,
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
                analyzed,
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
        self._pending_capability_cache = None
        # A warm still sweeping the old session settles into the abandoned
        # object (the zombie-runner pattern); its publish guard already
        # refuses to write a memo whose source artifacts have been cleared.
        # Dropping the queued state stops it starting a successor, too.
        self._capability_warm = None
        self._capability_warm_next = None
        self.import_report = None
        self.template_origin = None
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
        # Attached reference material belongs to the project that was open,
        # not to the app — a fresh session starts with none. Reset in place
        # for the same reason as the figure store.
        self.references.reset()
        # Clear staged chips (commit is generation-guarded, so a zombie
        # turn can't repopulate the fresh session).
        self.suggested_prompts.clear()
        # The meter answers "what has THIS session spent" — a fresh session
        # starts at zero (the trace remains the permanent record).
        self.usage.reset()
        # The context gauge describes the conversation being discarded.
        self.last_context_tokens = None
        self._active_turn_token = None
        self.turn_active = False
        self.generation += 1

    def start_from_template(
        self,
        section: SpecSection,
        *,
        module_id: str,
        template_id: str,
        template_name: str,
        seed_block_ids: list[str],
    ) -> None:
        """Atomically replace this session with an independent template seed."""
        with self._turn_state_lock:
            self._reset_while_locked()
            self.module = get_module(module_id)
            self.discipline = ""
            self.project_context = ""
            self.doc.seed_template(section)
            self.template_origin = {
                "template_id": template_id,
                "name": template_name,
                "seed_block_ids": list(seed_block_ids),
            }


class _SessionInvalidated(RuntimeError):
    """The session was reset/replaced while this turn was still streaming."""


def _stable_system_blocks(session: SessionState) -> list[dict[str, Any]]:
    """The system prompt: ONLY the stable module block, cached.

    Nothing session-varying may render here (pinned by
    ``test_stable_system_prompt_is_cached_and_module_rendered``); the live
    state travels in the PROJECT CONTEXT block of the newest user message
    (:func:`_turn_context_text`), after the cacheable history prefix.

    This breakpoint also closes the tools prefix, since tools render ahead
    of system — which is why the request needs no separate tool
    breakpoint. It carries the same TTL as every message breakpoint: a
    request that mixes TTLs violates the provider's longest-lived-first
    ordering rule and is rejected outright (see :func:`_cache_control`).
    """
    return [
        {
            "type": "text",
            "text": render_system_prompt(session.module),
            "cache_control": _cache_control(settings.CHAT_CACHE_TTL),
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


def effective_discipline(session: SessionState) -> str:
    """Versioned project discipline, falling back to legacy session state."""
    identity = getattr(session.doc.doc, "project_identity", {}) or {}
    current = str(identity.get("discipline", "") or "").strip()
    return current or session.discipline


def _project_identity_block(session: SessionState) -> str:
    """Compact identity status the model must maintain as context develops."""
    identity = getattr(session.doc.doc, "project_identity", {}) or {}
    discipline = effective_discipline(session)
    project_type = str(identity.get("project_type", "") or "").strip()
    return "\n".join(
        [
            "PROJECT IDENTITY (record with set_project_identity only when "
            "established):",
            f"- PROJECT DISCIPLINE: {discipline or '[not yet determined]'}",
            "- PROJECT TYPE (facility/use): "
            + (project_type or "[not yet determined]"),
            "When the user or clear document context establishes or corrects "
            "either value, record it immediately. Do not infer prematurely.",
        ]
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
    unstructured = session.import_is_unstructured()
    # First, because everything below it is dated: the editions in effect,
    # the research profile's as-of stamps, and the model's own judgement
    # about which edition is current all depend on knowing what "now" is.
    # It renders here rather than in the stable system prompt for the usual
    # reason (that block is cached and must not vary), and it costs nothing
    # to repeat: the whole context block is stripped again at commit.
    parts = [
        date_context_block(with_time=True),
        _project_identity_block(session),
    ]
    if (
        getattr(session.module, "open_catalog", False)
        and not effective_discipline(session)
    ):
        parts.append(
            "The discipline is not yet known — ask the user what "
            "discipline this section is for before drafting domain content."
        )
    # Optional project-description primer (any module — not gated by
    # open_catalog); renders only when the user provided one at session start.
    if session.project_context:
        parts.append(
            "PROJECT DESCRIPTION (stated by the user at session start): "
            + session.project_context
        )
    if session.template_origin:
        parts.append(
            "TEMPLATE STARTER: This native, source-less specification was "
            f"seeded from {session.template_origin.get('name', 'a reusable template')!r}. "
            "Imported-status blocks here mean template starter content awaiting "
            "project review; they do not have a retained Word source and must "
            "not be described as extracted from an office master."
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
    # Without this the outline below reads as a spec with an unset header, and
    # the model reliably "fixes" it by inventing a section number for a file
    # that was never a spec section.
    if unstructured:
        parts.append(
            "IMPORTED DOCUMENT IS NOT A SPEC SECTION: the file the user "
            "imported carried no SECTION number, PART heading, or numbered "
            "article. The section header, the three PART headings, and the "
            "placeholder article in the outline below are this app's "
            "scaffolding, NOT content from their document — do not describe "
            "them as something the file contained, and do not invent a "
            "section number or title to fill the gap. Ask what the user wants "
            "to do with this content: turn it into a spec section (then agree "
            "the section number/title with them first), mine it for "
            "requirements, or keep it as reference. Their original file is "
            "retained exactly and can still be downloaded unchanged."
        )
    parts.append(
        "Current specification document (full text; element ids in "
        "[id: …], provenance chips as ◆item-id):\n"
        + outline(doc, max_text=None)
    )
    lint_items = lint_document(
        doc, session.module, unstructured_import=unstructured
    )
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
    # Stubs only — the bodies arrive through read_reference_doc so they are
    # billed when the model actually opens one, not on every turn.
    reference_stubs = session.references.context_stubs()
    if reference_stubs:
        parts.append(reference_stubs)
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


def _elide_reference_tool_results(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Drop ``read_reference_doc`` bodies from committed history (COW).

    A reference document is large and the store already holds it verbatim, so
    leaving the text in history would re-bill it on every later turn and bloat
    the project file — the same reasoning as fetched-PDF elision, and the
    reason the tool description tells the model to simply read it again when
    it needs it. The tool_use block is left alone: it is just an id, and
    keeping it makes the transcript readable.

    Unlike the figure elision this has to match a ``tool_result`` back to the
    call that produced it, so the assistant's ``tool_use`` ids are collected
    first and the user-role results filtered against them.
    """
    read_ids = {
        block.get("id")
        for message in messages
        if message.get("role") == "assistant"
        for block in (message.get("content") or [])
        if block.get("type") == "tool_use"
        and block.get("name") == "read_reference_doc"
    }
    read_ids.discard(None)
    if not read_ids:
        return messages

    result: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") == "assistant":
            result.append(message)
            continue
        content = message.get("content") or []
        changed = False
        new_content: list[dict[str, Any]] = []
        for block in content:
            if (
                block.get("type") == "tool_result"
                and block.get("tool_use_id") in read_ids
                and not block.get("is_error")
            ):
                new_content.append(
                    {
                        **block,
                        "content": (
                            "[Reference document text omitted from history "
                            "to keep it out of every later request. It is "
                            "unchanged in the session — call "
                            "read_reference_doc again to re-read it.]"
                        ),
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
    - ``read_reference_doc`` tool results shed the document body (see
      :func:`_elide_reference_tool_results`) — the reference store holds it.
    - Unpaired ``server_tool_use`` blocks and orphaned server results are
      removed (see :func:`_without_unpaired_server_tool_uses`). The stop and
      truncation paths already scrub before they append, so this is the
      final invariant guard over the whole turn rather than the fix: nothing
      reaches ``session.history`` — and therefore nothing reaches the saved
      project file — that would make every later request invalid.
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
    return _without_unpaired_server_tool_uses(
        _elide_reference_tool_results(
            _elide_figure_tool_inputs(elide_all_pdf_sources(committed))
        )
    )


def _cache_control(cache_ttl: str) -> dict[str, Any]:
    """One breakpoint value. A fresh dict per call — callers own it.

    Copy-adapted from ``qc.engine._cache_control`` rather than imported:
    the two channels build entirely different requests and the copy-not-
    import posture keeps a QC-private helper from becoming chat's API.

    The API requires longer-lived cache entries to appear before
    shorter-lived ones in prompt order (tools -> system -> messages), so a
    request that marks system at the 5-minute default and the user turn at
    ``1h`` is rejected outright — not degraded, not uncached.

    This request therefore runs NON-INCREASING: system and the
    committed-history boundary carry ``settings.CHAT_CACHE_TTL``, and only
    the tail may differ, pinned to the shortest supported TTL
    (``settings.CHAT_TAIL_CACHE_TTL``). Because the tail is the last
    breakpoint and can never outlive the ones before it, no setting can
    produce an invalid order — pinned by
    ``test_no_setting_can_build_an_out_of_order_request``.
    """
    control: dict[str, Any] = {"type": "ephemeral"}
    if cache_ttl:
        control["ttl"] = cache_ttl
    return control


def _committed_history_boundary(
    history_len: int, raw_len: int, sanitized_len: int
) -> int:
    """Index of the last message belonging to the committed-history prefix.

    ``-1`` when there is no such message to mark. Sanitization replaces
    messages positionally and never adds or drops one (an emptied
    assistant message becomes a placeholder rather than disappearing), so
    the boundary is plain index arithmetic — but it is CHECKED rather than
    assumed. If a future sanitizer ever changes the count, this returns
    ``-1`` and the request falls back to the tail breakpoint alone: one
    missed cache read, instead of a breakpoint silently annotating the
    wrong message and splitting the prefix in the wrong place.
    """
    if history_len <= 0 or sanitized_len != raw_len:
        return -1
    return min(history_len, sanitized_len) - 1


def _with_cache_breakpoints(
    messages: list[dict[str, Any]],
    *,
    committed_boundary: int,
    cache_ttl: str,
    tail_cache_ttl: str,
) -> list[dict[str, Any]]:
    """Copy-on-write the request's cache breakpoints onto its messages.

    Two of the request's three breakpoints live here (the third is the
    stable system block, which also closes the tools+system prefix ahead
    of it):

    * **the committed-history boundary** — the last block of the last
      message in ``session.history``. This is the rolling one: every turn
      it advances by one exchange, so turn N+1 READS the whole prefix turn
      N wrote and only the newest exchange is written fresh. Without it a
      request has no breakpoint between the system block and the very end,
      so the entire conversation re-bills as uncached input every turn —
      which is what shipped before this.
    * **the tail** — the last block of the whole request, covering the
      fresh PROJECT CONTEXT and any continuation rounds. A continuation
      extends the previous round's entry rather than rewriting it.

    They carry DIFFERENT lifetimes, and that asymmetry is the point. The
    boundary is read by the next user turn, minutes or hours later, so it
    takes ``cache_ttl``. The tail is keyed on bytes commit is about to
    strip, so no later turn can ever read it — its only readers are this
    turn's continuation rounds, seconds apart. It therefore takes
    ``tail_cache_ttl`` (the shortest supported), paying 1.25x input to
    write instead of 2.0x for a lifetime nothing uses. On a block the size
    of the whole document that is the difference every turn.

    The two never collide: they are the last blocks of different messages,
    and when the boundary resolves to the same message as the tail the
    index set collapses to ONE annotation — which then takes the boundary's
    longer TTL, since a merged breakpoint is doing the boundary's job.

    The prefix these mark is NOT byte-identical to what was streamed —
    commit strips the PROJECT CONTEXT block, thinking blocks, and fetched
    PDF payloads — so the boundary caches the *committed* form. That is
    the form every later turn re-sends, which is exactly what makes it
    worth caching.

    Residual limitation, deliberately not worked around: a breakpoint
    looks back at most 20 content blocks for a prior entry. A single turn
    that appends more than 20 blocks (a long tool-heavy round) can push
    the previous turn's entry out of that window, and the read is missed
    for that turn — it re-writes and the next turn caches normally again.
    Adding interior breakpoints to cover it would spend the request's
    remaining budget on a rare case.

    Stored history is never mutated: the annotations ride a per-request
    copy, which is what keeps ``cache_control`` out of saved projects.
    """
    if not messages:
        return messages
    # TTL per marked message. The tail is registered FIRST so that a
    # boundary resolving to the same message overwrites it with the longer
    # lifetime: a merged breakpoint is serving the boundary's cross-turn
    # role, and under-living it would throw away the read it exists for.
    ttl_by_index = {len(messages) - 1: tail_cache_ttl}
    if 0 <= committed_boundary < len(messages):
        ttl_by_index[committed_boundary] = cache_ttl
    out = list(messages)
    for index in sorted(ttl_by_index):
        message = out[index]
        content = message.get("content")
        if not isinstance(content, list) or not content:
            continue
        tail = content[-1]
        if (
            not isinstance(tail, dict)
            or tail.get("type") in _TRANSIENT_BLOCK_TYPES
        ):
            continue
        new_tail = {
            **tail,
            "cache_control": _cache_control(ttl_by_index[index]),
        }
        out[index] = {**message, "content": [*content[:-1], new_tail]}
    return out


def _merge_usage(totals: dict[str, int], usage: Any) -> None:
    """Accumulate one response's billed usage into the turn totals.

    The chat loop aggregates across continuation rounds itself rather than
    calling ``usage_ledger.usage_to_dict``, so the provider's per-TTL cache
    subtotal has to be read here too — otherwise every one-hour cache write
    an interview turn makes is priced at the five-minute rate.
    ``cache_creation_1h_input_tokens`` is a SLICE of
    ``cache_creation_input_tokens``, never an addition to it.
    """
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
    creation = getattr(usage, "cache_creation", None)
    one_hour = (
        getattr(creation, "ephemeral_1h_input_tokens", None)
        if creation is not None
        else None
    )
    if isinstance(one_hour, (int, float)) and one_hour:
        totals["cache_creation_1h_input_tokens"] = (
            totals.get("cache_creation_1h_input_tokens", 0) + int(one_hour)
        )
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


def _context_tokens(usage: Any) -> int | None:
    """Exact rendered-prompt size of ONE request: fresh input + cache write
    + cache read tokens (Anthropic-counted — their sum is the full prompt).

    Unlike the cumulative ``_merge_usage`` totals, this is a per-request
    gauge; the caller pairs the LAST round's value with that round's
    retained output (``_retained_output_tokens``) as the session context
    size. None when the usage object carries none of the three fields (the
    test fakes default ``usage=None``).
    """
    if usage is None:
        return None
    total = 0
    seen = False
    for key in (
        "input_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    ):
        value = getattr(usage, key, None)
        if isinstance(value, (int, float)):
            total += int(value)
            seen = True
    return total if seen else None


def _retained_output_tokens(usage: Any) -> int:
    """The response tokens that persist into history: output minus thinking.

    The final round's reply is committed AFTER the request the gauge
    measures, so without this the gauge would omit the newest assistant
    message. Thinking tokens are subtracted because thinking blocks are
    stripped at commit and never re-billed on later requests.
    """
    if usage is None:
        return 0
    output = getattr(usage, "output_tokens", None)
    if not isinstance(output, (int, float)):
        return 0
    details = getattr(usage, "output_tokens_details", None)
    thinking = getattr(details, "thinking_tokens", None) if details else None
    if isinstance(thinking, (int, float)):
        return max(0, int(output) - int(thinking))
    return max(0, int(output))


# --- Stopped-turn output estimation ------------------------------------------
#
# Closing a stream the instant the user clicks Stop is the whole point of
# ``current_message_snapshot`` (draining it would keep paying for tokens the
# UI already stopped showing) — but the provider's final ``message_delta``,
# which carries the authoritative output count, is exactly what gets skipped.
# The snapshot therefore reports whatever ``message_start`` announced, which
# is a placeholder of a handful of tokens no matter how much text arrived.
# Under-reporting a stopped turn by hundreds of tokens is the one thing a
# spend meter must not do quietly.
#
# So the accumulated content is measured and the shortfall recorded — in its
# OWN counter, never blended into ``output_tokens``. That separation is a
# frozen decision of this remediation program, and it is load-bearing:
# ``output_tokens`` is the one number that can be reconciled against a
# provider invoice, and a heuristic mixed into it would corrupt exactly the
# field an auditor trusts. See ``ESTIMATED_OUTPUT_TOKENS_KEY``.

# ``ESTIMATED_OUTPUT_TOKENS_KEY`` / ``USAGE_ESTIMATED_KEY`` live in
# :mod:`backend.usage_ledger`, which owns the usage-key vocabulary and is
# where the counter is priced.

# Characters per token. Deliberately coarse: the SDK exposes no running
# output count, and a real tokenizer is neither available offline nor worth
# a dependency for a number the UI labels an estimate either way. English
# prose runs ~4 chars/token, so this under-counts dense JSON slightly and
# over-counts whitespace-heavy text slightly.
_CHARS_PER_TOKEN = 4


def _model_authored_chars(content: list[dict[str, Any]]) -> int:
    """Characters the MODEL produced in one round's accumulated content.

    Only blocks the model generated are counted, because only those were
    billed as output. Tool RESULTS (client, web-search, web-fetch) are
    provider- or app-supplied and bill as input on the following request —
    counting them would inflate a stopped turn by whole retrieved pages.

    A thinking block contributes its summary text but not its ``signature``:
    that is an opaque provider attestation, not generated prose, and its
    length tracks nothing the user was billed for.
    """
    total = 0
    for block in content:
        block_type = block.get("type")
        if block_type == "text":
            total += len(block.get("text") or "")
        elif block_type == "thinking":
            total += len(block.get("thinking") or "")
        elif block_type in ("tool_use", "server_tool_use"):
            # A stop most often lands mid tool-input JSON, and that partial
            # JSON is real output the model streamed.
            total += len(block.get("name") or "")
            raw_input = block.get("input")
            if raw_input is not None:
                try:
                    total += len(json.dumps(raw_input, ensure_ascii=False))
                except (TypeError, ValueError):
                    total += len(str(raw_input))
    return total


def estimated_output_shortfall(
    content: list[dict[str, Any]], reported_output: int
) -> int:
    """Output tokens a stopped round produced BEYOND the provider's count.

    ``max(0, estimate - reported)`` — so when the provider's number already
    exceeds the heuristic (a short reply, or a stop that still caught the
    final delta) this contributes nothing and the recorded usage stays
    purely provider-reported. The estimate can only ever ADD to a total,
    never revise the provider's figure downward.
    """
    estimate = -(-_model_authored_chars(content) // _CHARS_PER_TOKEN)
    return max(0, estimate - max(0, reported_output))


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


def _start_input(block: Any) -> dict[str, Any]:
    """A COPY of a start frame's already-complete tool input, or ``{}``.

    A server tool invoked through the code-execution caller can deliver its
    whole input on ``content_block_start`` and stream no
    ``input_json_delta`` frames at all — which used to leave the chat chip
    saying "Searching the web…" with nothing in it. Both web tools pin
    ``allowed_callers: ["direct"]`` (``research/schema.py``), so this is the
    fallback for any future code-execution-called tool or provider-side
    shape drift, not the expected path.

    Copied, never retained by reference: the SDK accumulates into the block
    object as the stream advances.

    The ``isinstance`` test is load-bearing on the real SDK path, not just
    against a sloppy fake. ``ServerToolUseBlock.input`` is declared
    ``Dict[str, object]``, but raw stream events are built with
    ``construct_type_unchecked`` — no validation — so a non-mapping value
    arrives untouched, and ``dict("…")`` raises.
    """
    value = getattr(block, "input", None)
    return dict(value) if isinstance(value, Mapping) else {}


def _stream_events(stream: Any) -> Iterator[dict[str, Any]]:
    """Translate one round's raw stream events into UI event dicts.

    Yields ``status`` hints on block starts (thinking/writing/drafting/
    searching/fetching), ``text_delta``/``thinking_delta`` on content deltas,
    throttled drafting ``progress_chars`` as an edit batch's JSON streams,
    and LIVE ``web_search``/``web_fetch`` events the instant a server-tool
    block's input finishes (``content_block_stop``) — not derived after the
    round from the final message. Empty thinking deltas (``display:
    omitted``) are dropped so they don't prematurely clear the status strip.

    A server-tool input arrives either as streamed ``input_json_delta``
    frames (the direct-caller shape both web tools pin) or complete on the
    start frame (the code-execution caller); both are tracked, the streamed
    deltas win when a stream supplies both, and every index is dropped at
    ``content_block_stop`` so a long turn never retains a finished edit
    batch's JSON. A block whose input is missing entirely still emits its
    chip with empty text — the round DID search, and saying so with no
    label beats saying nothing.

    Per-event parsing is defensive: a frame this relay cannot decode is
    skipped, never a turn failure (the research/QC relays adapted from this
    one have always said so; here it was implicit and one non-string
    ``partial_json`` away from being untrue).

    The boundary is exact and deliberate. The SDK accumulates and snapshots
    each raw frame INSIDE ``next(stream)``, before yielding it
    (``anthropic/lib/streaming/_messages.py::__stream__``), so anything that
    breaks the SDK's own accumulator raises during iteration and is not
    caught here. That is correct: an unparseable stream is a failed request
    and must reach the caller's error/retry path exactly as a
    ``get_final_message()`` failure does. What the SDK does NOT do is
    validate raw event shapes (``construct_type_unchecked``), so frames it
    happily hands over can still be nonsense — that is this relay's job.
    """
    json_buffers: dict[int, str] = {}
    start_inputs: dict[int, dict[str, Any]] = {}
    block_kinds: dict[int, tuple[str, str]] = {}
    last_progress = time.monotonic()
    for event in stream:
        try:
            etype = getattr(event, "type", None)
            if etype == "content_block_start":
                block = getattr(event, "content_block", None)
                index = getattr(event, "index", 0)
                btype = getattr(block, "type", None) or ""
                bname = getattr(block, "name", "") or ""
                block_kinds[index] = (btype, bname)
                json_buffers[index] = ""
                if btype == "server_tool_use":
                    started = _start_input(block)
                    if started:
                        start_inputs[index] = started
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
                btype, bname = block_kinds.pop(index, ("", ""))
                streamed = _safe_json(json_buffers.pop(index, ""))
                started = start_inputs.pop(index, {})
                if btype != "server_tool_use":
                    continue
                payload = streamed or started
                if bname == "web_search":
                    yield {"type": "web_search", "query": str(payload.get("query", ""))}
                elif bname == "web_fetch":
                    yield {"type": "web_fetch", "url": str(payload.get("url", ""))}
        except Exception:  # noqa: BLE001 — a malformed frame never fails a turn
            continue


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


def _run_read_reference_doc(
    session: SessionState,
    block: dict[str, Any],
    trace_handle: Any = None,
    *,
    budget: TurnReferenceBudget | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Execute one ``read_reference_doc`` tool_use block.

    The body is returned in full for this turn and elided from committed
    history afterwards (see :func:`_elide_reference_tool_results`), so the
    model pays for it when it asks rather than on every later turn. An
    unknown id becomes an ``is_error`` result listing what is attached, so
    the model can correct itself instead of failing the turn.

    ``budget`` bounds the reference text this ONE turn may pull in across all
    of its calls. Elision runs at commit, so every body read this turn is
    still in the continuation request; without a cumulative ceiling a model
    comparing several large attachments could overrun the context window and
    fail the turn on an opaque API error. Overrunning the budget is reported
    the same correctable way as a bad id.
    """
    ref_id = str((block.get("input") or {}).get("ref_id") or "").strip()
    doc = session.references.get(ref_id) if ref_id else None
    if doc is None:
        available = ", ".join(d.rid for d in session.references.docs)
        detail = (
            f"Attached documents: {available}."
            if available
            else "No reference documents are attached to this session."
        )
        return (
            {
                "type": "tool_result",
                "tool_use_id": block.get("id"),
                "content": (
                    f"read_reference_doc: no reference document with id "
                    f"{ref_id!r}. {detail}"
                ),
                "is_error": True,
            },
            [],
        )
    if budget is not None and not budget.take(len(doc.text)):
        _trace.note(
            trace_handle,
            f"reference {doc.rid} refused: turn budget spent "
            f"({budget.spent}/{budget.limit} chars)",
        )
        return (
            {
                "type": "tool_result",
                "tool_use_id": block.get("id"),
                "content": (
                    f"read_reference_doc: this turn has already read "
                    f"{budget.spent:,} characters of reference material and "
                    f"{doc.rid} ({len(doc.text):,} characters) does not fit in "
                    f"the {budget.limit:,}-character per-turn budget. Nothing "
                    "was read. Work with the documents you have already read "
                    "this turn, or reply and read this one next turn."
                ),
                "is_error": True,
            },
            [],
        )
    _trace.note(trace_handle, f"read reference {doc.rid} ({doc.char_count} chars)")
    header = (
        f"Reference document {doc.rid} — \"{doc.title}\" "
        f"({doc.kind_label()}, from {doc.filename}). Background material, "
        f"not spec text: draft "
        f"provisions in your own specification language, never paste from "
        f"this.\n\n"
    )
    return (
        {
            "type": "tool_result",
            "tool_use_id": block.get("id"),
            "content": header + doc.text,
        },
        [],
    )


def _run_tool(
    session: SessionState,
    block: dict[str, Any],
    trace_handle: Any = None,
    *,
    message_index: int = 0,
    reference_budget: TurnReferenceBudget | None = None,
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
    if name == "read_reference_doc":
        return _run_read_reference_doc(
            session, block, trace_handle, budget=reference_budget
        )
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
        # Prompt material for the turn: hash-refs at the default capture
        # level (the stable system block costs one prompts.jsonl entry per
        # app run), inline text in deep mode. Observation only — the
        # request payload is built independently below.
        _trace.turn_prompts(
            trace_handle,
            system_text="\n\n".join(
                str(block.get("text", ""))
                for block in _stable_system_blocks(session)
            ),
            context_text=context_text,
            user_text=user_text,
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

    def request_kwargs(container_id: str = "") -> dict[str, Any]:
        """Build one round's request.

        ``container_id`` is passed in rather than closed over: it is
        turn-local state that changes between rounds, and a closure over it
        would quietly bind whatever the variable happened to hold when the
        request was later built — a real hazard once Phase 6 moves request
        construction outside the turn-state lock.
        """
        history = list(session.history)
        raw = history + new_messages
        messages = sanitize_messages_for_resend(raw)
        kwargs: dict[str, Any] = {
            "model": model or settings.INTERVIEW_MODEL,
            "max_tokens": max_tokens or settings.INTERVIEW_MAX_TOKENS,
            "system": _stable_system_blocks(session),
            "messages": _with_cache_breakpoints(
                messages,
                committed_boundary=_committed_history_boundary(
                    len(history), len(raw), len(messages)
                ),
                cache_ttl=settings.CHAT_CACHE_TTL,
                tail_cache_ttl=settings.CHAT_TAIL_CACHE_TTL,
            ),
            "tools": _chat_tools(),
            "thinking": _thinking_param(),
            "output_config": {"effort": settings.INTERVIEW_EFFORT},
        }
        if container_id:
            # Top level only — never inside system, tools, or messages. A
            # pending code-execution-called server tool can only be resumed
            # inside the container it started in.
            kwargs["container"] = container_id
        return kwargs

    stop_reason: str | None = None
    # The provider continuation container for THIS turn, if the model's
    # server-tool work ever runs in one. Turn-local by construction: a new
    # ``stream_user_turn`` starts a new conversation and a fresh "", so the
    # reset needs no bookkeeping. Later rounds of this turn — a pause_turn
    # resume, or a continuation after a client tool_result — reuse it. It
    # never enters the message list, committed history, prompts, traces, or
    # the project file. With ``allowed_callers: ["direct"]`` on both web
    # tools none is expected; see ``research/grounding.response_container_id``.
    container_id = ""
    doc_changed = False
    committed = False
    post_commit_events: list[dict[str, Any]] = []
    usage_totals: dict[str, int] = {}
    # Per-round overwrite (NOT a sum): after the loop this holds the final
    # round's rendered-prompt size PLUS its retained reply — the size of the
    # conversation the turn commits. Earlier rounds' outputs are already
    # inside later rounds' input counts, so only the final round's pair
    # matters. Committed beside the doc/figures so a failed turn (history
    # rolled back) leaves the previous measurement standing.
    last_round_context: int | None = None
    # Turn-local staging for suggested-reply chips: the dispatch loop records
    # each suggest_prompts call here (latest wins); a successful turn commits
    # it into session.suggested_prompts, a failed turn drops it with the
    # generator. Initializes to [] so a turn that never calls the tool
    # commits an empty set — that "no call = clear" rule is the wind-down.
    staged_suggestions: list[str] = []
    # Bounds the reference text this turn can pull into its request across
    # every read_reference_doc call (commit-time elision does not help the
    # continuation rounds). Turn-local, discarded with the turn.
    reference_budget = TurnReferenceBudget()
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
                # A stop caught between rounds can land right after a paused
                # response whose server tools never came back — the use is
                # in the trailing assistant message with no result anywhere.
                new_messages[:] = _without_unpaired_server_tool_uses(
                    new_messages, placeholder="[Generation stopped by user.]"
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
                request = request_kwargs(container_id)
            round_started = time.perf_counter()
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

            # Refresh before the round branches: whether this round pauses,
            # dispatches a client tool, or ends the turn, the next request
            # in it must carry the container. Latest nonblank wins — a
            # response that omits the field has not revoked it. The stopped
            # path reads the snapshot for the same reason it reads its
            # content: it is what actually arrived.
            container_id = response_container_id(final) or container_id

            _merge_usage(usage_totals, getattr(final, "usage", None))
            final_usage = getattr(final, "usage", None)
            content = _content_blocks_to_dicts(final.content)
            stop_reason = "user_stop" if stopped_mid_stream else final.stop_reason

            # A stopped round skipped the provider's final usage delta, so
            # measure what actually arrived and record the shortfall in its
            # own counter. Only here: a round that ended normally carries an
            # exact provider count, which always wins.
            shortfall = 0
            if stopped_mid_stream:
                reported = getattr(final_usage, "output_tokens", None)
                shortfall = estimated_output_shortfall(
                    content,
                    int(reported) if isinstance(reported, (int, float)) else 0,
                )
                if shortfall:
                    usage_totals[ESTIMATED_OUTPUT_TOKENS_KEY] = (
                        usage_totals.get(ESTIMATED_OUTPUT_TOKENS_KEY, 0)
                        + shortfall
                    )
                    usage_totals[USAGE_ESTIMATED_KEY] = True

            round_context = _context_tokens(final_usage)
            if round_context is not None:
                # The gauge pairs this request's prompt with the reply that
                # is about to be committed, so a stopped round's estimated
                # output belongs in it too — making the gauge an UPPER
                # estimate for that one turn rather than a silent undercount.
                last_round_context = (
                    round_context
                    + _retained_output_tokens(final_usage)
                    + shortfall
                )

            # One round_end trace event per streaming round — which round
            # stalled, where the tokens went — including pause_turn rounds.
            round_usage: dict[str, int] = {}
            _merge_usage(round_usage, final_usage)
            if shortfall:
                round_usage[ESTIMATED_OUTPUT_TOKENS_KEY] = shortfall
                round_usage[USAGE_ESTIMATED_KEY] = True
            _trace.turn_round(
                trace_handle,
                round_index=_round,
                stop_reason=stop_reason,
                duration_ms=int((time.perf_counter() - round_started) * 1000),
                usage=round_usage,
                tool_uses=sum(
                    1 for b in content if b.get("type") == "tool_use"
                ),
                web_searches=sum(
                    1
                    for b in content
                    if b.get("type") == "server_tool_use"
                    and b.get("name") == "web_search"
                ),
                web_fetches=sum(
                    1
                    for b in content
                    if b.get("type") == "server_tool_use"
                    and b.get("name") == "web_fetch"
                ),
            )

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
                # The same cut can leave a SERVER tool call unanswered —
                # stopping while the UI said "Searching the web…" is the
                # ordinary way to produce one. Scrubbed turn-wide, because a
                # pause_turn may have put the use in an earlier message and
                # its result would have arrived in this one.
                new_messages[:] = _without_unpaired_server_tool_uses(
                    new_messages, placeholder=fallback
                )
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
                        reference_budget=reference_budget,
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
    except anthropic.AuthenticationError:
        yield {
            "type": "error",
            "message": AUTH_ERROR_MESSAGE,
            "kind": "auth_error",
        }
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
                if last_round_context is not None:
                    # The context gauge: prompt size of this turn's final
                    # request. A turn whose rounds carried no usage (many
                    # test scripts) keeps the previous measurement.
                    session.last_context_tokens = last_round_context
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
                                unstructured_import=(
                                    session.import_is_unstructured()
                                ),
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
