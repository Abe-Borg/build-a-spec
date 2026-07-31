"""Session store and portable project serialization.

The local app has one active conversation, so this is one module-level session
behind a tiny accessor. Semantic state is JSON; portable ``.baspec`` files can
also carry the exact imported DOCX as a separate binary member.
"""
from __future__ import annotations

import copy
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Literal

from .llm.conversation import SessionState, effective_discipline
from .spec_doc.project import load_project, save_project
from .spec_doc.project_package import ProjectPackageError, build_project_package

WorkspaceScope = Literal["original", "tutorial", "scenario"]


class WorkspaceConflictError(RuntimeError):
    pass


class WorkspaceBusyError(WorkspaceConflictError):
    def __init__(self, reasons: list[str]) -> None:
        self.reasons = tuple(reasons)
        super().__init__("The workspace is busy: " + ", ".join(reasons) + ".")


@dataclass(frozen=True, slots=True)
class WorkspaceLease:
    workspace_id: int
    scope: WorkspaceScope
    session: SessionState
    generation: int
    tutorial_id: str | None = None
    scenario_kind: str | None = None
    tutorial_source: str | None = None


class SessionManager:
    """Own the active session and bounded disposable tutorial workspaces."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._active = SessionState()
        self._scope: WorkspaceScope = "original"
        self._next_workspace_id = 2
        self._workspace_id = 1
        self._original: SessionState | None = None
        self._tutorial: SessionState | None = None
        self._scenario: SessionState | None = None
        self._tutorial_id: str | None = None
        self._tutorial_request_id: str | None = None
        self._tutorial_source: str | None = None
        self._scenario_kind: str | None = None
        self._active_writes = 0
        # Who holds the workspace-transition slot, not merely whether someone
        # does. A bare boolean was clearable by anyone, so a tutorial finish
        # or a forced restore could release a reservation whose (possibly
        # billed) build was still running outside the lock — stranding that
        # build's usage merge on a session about to be discarded.
        self._transition_owner: object | None = None
        self._tutorial_usage_before: dict[str, Any] | None = None
        self._scenario_usage_before: dict[str, Any] | None = None

    # -- transition ownership ------------------------------------------------

    def _begin_transition_locked(self) -> object:
        """Reserve the transition slot and return the token that OWNS it.

        Callers check :meth:`_transition_active_locked` themselves first, so
        that "another tutorial transition" keeps its place among the other
        busy reasons; the raise here is the backstop that makes the slot
        un-double-bookable by a future caller that forgets.
        """
        if self._transition_owner is not None:
            raise WorkspaceBusyError(["another tutorial transition"])
        owner = object()
        self._transition_owner = owner
        return owner

    def _finish_transition_locked(self, owner: object) -> None:
        """Release the slot only if ``owner`` still holds it.

        A build that lost its workspace — or was abandoned outright — has
        nothing left to release, and must not clear a reservation some later
        transition has since taken.
        """
        if self._transition_owner is owner:
            self._transition_owner = None

    def _transition_active_locked(self) -> bool:
        return self._transition_owner is not None

    def _activate(self, session: SessionState, scope: WorkspaceScope) -> None:
        self._active = session
        self._scope = scope
        self._workspace_id = self._next_workspace_id
        self._next_workspace_id += 1

    def current(self) -> WorkspaceLease:
        with self._lock:
            return WorkspaceLease(
                self._workspace_id,
                self._scope,
                self._active,
                self._active.generation,
                self._tutorial_id,
                self._scenario_kind,
                self._tutorial_source,
            )

    def tutorial_for_save(self) -> SessionState:
        """Return the stable tutorial base, never a disposable scenario."""
        with self._lock:
            if self._scope == "original" or self._tutorial is None:
                raise WorkspaceConflictError("No tutorial project is retained.")
            return self._tutorial

    def assert_active(self, lease: WorkspaceLease) -> None:
        with self._lock:
            if (
                lease.workspace_id != self._workspace_id
                or lease.session is not self._active
            ):
                raise WorkspaceConflictError(
                    "The active workspace changed while this operation was running."
                )

    def assert_fresh(self, lease: WorkspaceLease) -> None:
        """Reject delayed work from either an old workspace or generation."""
        self.assert_active(lease)
        with self._lock:
            if lease.generation != self._active.generation:
                raise WorkspaceConflictError(
                    "The session was replaced while this operation was running."
                )

    def _check_expected(self, expected_workspace_id: int | None) -> None:
        if (
            expected_workspace_id is not None
            and expected_workspace_id != self._workspace_id
        ):
            raise WorkspaceConflictError(
                "This request belongs to an inactive tutorial workspace."
            )

    @contextmanager
    def active_write(
        self, expected_workspace_id: int | None = None
    ) -> Iterator[WorkspaceLease]:
        with self._lock:
            self._check_expected(expected_workspace_id)
            if self._transition_active_locked():
                raise WorkspaceConflictError("A workspace transition is in progress.")
            lease = self.current()
            self._active_writes += 1
        try:
            yield lease
            self.assert_active(lease)
        finally:
            with self._lock:
                self._active_writes = max(0, self._active_writes - 1)

    @staticmethod
    def _busy_reasons(session: SessionState) -> list[str]:
        reasons: list[str] = []
        if session.turn_active:
            reasons.append("chat")
        if getattr(session.research, "status", "idle") == "running" or bool(
            getattr(session.research, "is_settling", False)
        ):
            reasons.append("research")
        if getattr(session.audit, "status", "idle") == "running" or bool(
            getattr(session.audit, "is_settling", False)
        ):
            reasons.append("audit")
        if getattr(session.qc, "status", "idle") == "running" or bool(
            getattr(session.qc, "is_settling", False)
        ):
            reasons.append("final QC")
        return reasons

    def begin_tutorial(
        self,
        expected_workspace_id: int | None = None,
        *,
        expected_generation: int | None = None,
        staged_session: SessionState | None = None,
        request_id: str | None = None,
        source: str | None = None,
    ) -> WorkspaceLease:
        with self._lock:
            if (
                self._scope == "tutorial"
                and request_id
                and request_id == self._tutorial_request_id
            ):
                return self.current()
            self._check_expected(expected_workspace_id)
            if (
                expected_generation is not None
                and expected_generation != self._active.generation
            ):
                raise WorkspaceConflictError(
                    "This request belongs to an earlier session generation."
                )
            if self._scope != "original":
                raise WorkspaceConflictError("A tutorial is already active.")
            if self._active_writes:
                raise WorkspaceBusyError(["another edit or upload"])
            if self._transition_active_locked():
                raise WorkspaceBusyError(["another tutorial transition"])
            reasons = self._busy_reasons(self._active)
            if reasons:
                raise WorkspaceBusyError(reasons)
            owner = self._begin_transition_locked()
            original = self._active
            activation = self._workspace_id
        try:
            tutorial = staged_session or clone_session_for_tutorial(original)
        except Exception:
            with self._lock:
                self._finish_transition_locked(owner)
            raise
        with self._lock:
            # Ownership is checked FIRST and is the authority: a hard reset
            # revokes the reservation without moving the scope (activation
            # happens below, so an in-flight setup still reads `original`),
            # and the workspace-identity checks alone would not notice.
            if (
                self._transition_owner is not owner
                or self._workspace_id != activation
                or self._active is not original
            ):
                self._finish_transition_locked(owner)
                raise WorkspaceConflictError("The workspace changed during tutorial setup.")
            self._original = original
            self._tutorial = tutorial
            self._tutorial_id = uuid.uuid4().hex
            self._tutorial_request_id = request_id
            self._tutorial_source = source
            self._tutorial_usage_before = tutorial.usage.snapshot()
            self._activate(tutorial, "tutorial")
            self._finish_transition_locked(owner)
            return self.current()

    def push_scenario(
        self,
        expected_workspace_id: int,
        *,
        kind: str,
        staged_session: SessionState | None = None,
        build: Callable[[SessionState], SessionState] | None = None,
    ) -> WorkspaceLease:
        """Reserve the scenario slot, then build it.

        ``build`` (an alternative to a pre-built ``staged_session``) defers
        construction until AFTER every guard below passes — the same
        reserve-then-build ordering already used when neither is given
        (the ``clone_session_for_tutorial`` fallback). This matters once a
        scenario's construction can be expensive or paid (e.g. a live model
        call): computing it eagerly, outside this method, would let two
        overlapping requests both pay for it before either discovered the
        slot was already taken.
        """
        allowed = {
            "blank",
            "structural",
            "review",
            "import",
            "template",
            "project_roundtrip",
            "references",
            "research",
            "qc",
        }
        if kind not in allowed:
            raise WorkspaceConflictError("Unknown tutorial scenario kind.")
        with self._lock:
            self._check_expected(expected_workspace_id)
            if self._scope != "tutorial" or self._scenario is not None:
                raise WorkspaceConflictError(
                    "A scenario can only start from the tutorial workspace."
                )
            if self._active_writes:
                raise WorkspaceBusyError(["another edit or upload"])
            if self._transition_active_locked():
                raise WorkspaceBusyError(["another tutorial transition"])
            reasons = self._busy_reasons(self._active)
            if reasons:
                raise WorkspaceBusyError(reasons)
            owner = self._begin_transition_locked()
            tutorial = self._active
            activation = self._workspace_id
        try:
            if staged_session is not None:
                scenario = staged_session
            elif build is not None:
                scenario = build(tutorial)
            else:
                scenario = clone_session_for_tutorial(tutorial)
        except Exception:
            with self._lock:
                self._finish_transition_locked(owner)
            raise
        with self._lock:
            # Same rule as begin_tutorial: a revoked reservation is a lost
            # right to commit, checked before the workspace identity.
            if (
                self._transition_owner is not owner
                or self._workspace_id != activation
                or self._active is not tutorial
            ):
                self._finish_transition_locked(owner)
                raise WorkspaceConflictError("The workspace changed during scenario setup.")
            # Scenario construction may intentionally pass through production
            # import/template/project loaders, all of which start a fresh
            # usage ledger. The meter remains cumulative across the tutorial;
            # only the scenario's later positive delta is merged back.
            scenario.usage.load_snapshot(tutorial.usage.snapshot())
            self._scenario = scenario
            self._scenario_kind = kind
            self._scenario_usage_before = scenario.usage.snapshot()
            self._activate(scenario, "scenario")
            self._finish_transition_locked(owner)
            return self.current()

    def pop_scenario(self, expected_workspace_id: int) -> WorkspaceLease:
        with self._lock:
            self._check_expected(expected_workspace_id)
            if self._scope != "scenario" or self._tutorial is None:
                raise WorkspaceConflictError("No tutorial scenario is active.")
            if self._active_writes:
                raise WorkspaceBusyError(["another edit or upload"])
            reasons = self._busy_reasons(self._active)
            if reasons:
                raise WorkspaceBusyError(reasons)
            scenario = self._active
            tutorial = self._tutorial
            before = self._scenario_usage_before or {}
            tutorial.usage.merge_delta(before, scenario.usage.snapshot())
            scenario.invalidate_model_turn()
            self._scenario = None
            self._scenario_kind = None
            self._scenario_usage_before = None
            self._activate(tutorial, "tutorial")
            return self.current()

    def replace_tutorial(
        self,
        expected_workspace_id: int,
        staged_session: SessionState,
        *,
        source: str | None = None,
    ) -> WorkspaceLease:
        """Replace an incomplete tutorial copy with a validated repair.

        This is used only after enrichment has stopped and failed its fixture
        validation.  Spend already incurred stays additive, while the
        retained original and the original tutorial-usage baseline remain
        untouched for exact restoration accounting.
        """
        with self._lock:
            self._check_expected(expected_workspace_id)
            if self._scope != "tutorial" or self._tutorial is None:
                raise WorkspaceConflictError(
                    "Only the active tutorial copy can be repaired."
                )
            if self._active_writes:
                raise WorkspaceBusyError(["another edit or upload"])
            if self._transition_active_locked():
                # A scenario build in flight is reading THIS tutorial session
                # and will merge its spend onto it; swapping it out now would
                # send that merge to an object nobody holds any more.
                raise WorkspaceBusyError(["another tutorial transition"])
            reasons = self._busy_reasons(self._active)
            if reasons:
                raise WorkspaceBusyError(reasons)
            old = self._active
            staged_session.usage.load_snapshot(old.usage.snapshot())
            old.invalidate_model_turn()
            self._tutorial = staged_session
            if source is not None:
                self._tutorial_source = source
            self._activate(staged_session, "tutorial")
            return self.current()

    def finish_tutorial(self, expected_workspace_id: int) -> WorkspaceLease:
        """End the tutorial the only way it can end: restore the original.

        The retained original object is re-activated as-is — the same identity,
        not a copy — so the user's project comes back whole and every reference
        held elsewhere keeps working. The tutorial copy is discarded; only its
        paid usage delta carries over, because that spend was real.
        """
        with self._lock:
            self._check_expected(expected_workspace_id)
            if self._scope != "tutorial" or self._tutorial is None:
                raise WorkspaceConflictError(
                    "Finish the active scenario before resolving the tutorial."
                )
            if self._active_writes:
                raise WorkspaceBusyError(["another edit or upload"])
            if self._transition_active_locked():
                # Refuse rather than discard: a scenario build running outside
                # the lock merges its (already billed) usage onto this
                # tutorial session when it returns, and finishing first would
                # drop the object that merge lands on.
                raise WorkspaceBusyError(["another tutorial transition"])
            reasons = self._busy_reasons(self._active)
            if reasons:
                raise WorkspaceBusyError(reasons)
            tutorial = self._tutorial
            if self._original is None:
                raise WorkspaceConflictError("The original workspace is unavailable.")
            original = self._original
            original.usage.merge_delta(
                self._tutorial_usage_before or {}, tutorial.usage.snapshot()
            )
            tutorial.invalidate_model_turn()
            self._original = None
            self._tutorial = None
            self._scenario = None
            self._tutorial_id = None
            self._tutorial_request_id = None
            self._tutorial_source = None
            self._scenario_kind = None
            self._tutorial_usage_before = None
            self._scenario_usage_before = None
            self._activate(original, "original")
            return self.current()

    def force_restore_original(
        self, *, abandon_transition: bool = False
    ) -> WorkspaceLease:
        """Restore the original workspace, refusing an in-flight transition.

        "Force" is about the tutorial's own state machine, not about other
        people's reservations: a scenario build running outside the lock
        merges its already-billed usage onto the tutorial session when it
        returns, so restoring first would strand that spend.

        ``abandon_transition`` is the teardown escape (see
        :func:`reset_session`) and is only safe because the reservation is
        owned: clearing the slot revokes the pending build's authority to
        commit (both builders re-check ownership), while its own release
        finds it no longer owns anything and clears nothing — so a
        transition that starts afterwards keeps its reservation.
        """
        with self._lock:
            if abandon_transition:
                # Revoked BEFORE the scope check, deliberately. An in-flight
                # begin_tutorial has not activated anything yet, so the scope
                # is still `original` — returning early there would leave the
                # reservation held and let that build commit a tutorial on
                # top of the session this reset just cleared.
                self._transition_owner = None
            if self._scope == "original":
                return self.current()
            if self._transition_active_locked():
                raise WorkspaceBusyError(["another tutorial transition"])
            if self._original is None or self._tutorial is None:
                raise WorkspaceConflictError("The original workspace is unavailable.")
            if self._scenario is not None:
                self._tutorial.usage.merge_delta(
                    self._scenario_usage_before or {}, self._scenario.usage.snapshot()
                )
                self._scenario.invalidate_model_turn()
            self._original.usage.merge_delta(
                self._tutorial_usage_before or {}, self._tutorial.usage.snapshot()
            )
            self._tutorial.invalidate_model_turn()
            original = self._original
            self._original = None
            self._tutorial = None
            self._scenario = None
            self._tutorial_id = None
            self._tutorial_request_id = None
            self._tutorial_source = None
            self._scenario_kind = None
            self._tutorial_usage_before = None
            self._scenario_usage_before = None
            self._active_writes = 0
            self._transition_owner = None
            self._activate(original, "original")
            return self.current()

    def restore_original_for_native_close(self) -> WorkspaceLease:
        """Restore only after every tutorial operation has fully settled.

        Native close is synchronous.  Abandoning a paid runner here would
        leave it writing to an unreachable scenario if the later save prompt
        were cancelled, so close must be vetoed until the user stops the run
        or it settles normally.
        """
        with self._lock:
            if self._scope == "original":
                return self.current()
            reasons = self._busy_reasons(self._active)
            if self._active_writes:
                reasons.append("another edit or upload")
            if self._transition_active_locked():
                reasons.append("workspace transition")
            if reasons:
                raise WorkspaceBusyError(reasons)
            return self.force_restore_original()


_manager = SessionManager()


def get_session() -> SessionState:
    return _manager.current().session


def get_workspace() -> WorkspaceLease:
    return _manager.current()


def workspace_manager() -> SessionManager:
    return _manager


def active_write(
    expected_workspace_id: int | None = None,
) -> Iterator[WorkspaceLease]:
    return _manager.active_write(expected_workspace_id)


def reset_session() -> None:
    """Hard-reset the manager to a fresh original session.

    A teardown/emergency primitive, NOT the user-facing New session — that
    is ``POST /api/session/reset``, which refuses outside the original scope
    entirely. This one abandons an in-flight tutorial transition rather than
    refusing, so a reservation leaked by a crashed build cannot wedge the
    process; abandoning is safe because the slot is owned (see
    :meth:`SessionManager.force_restore_original`).
    """
    _manager.force_restore_original(abandon_transition=True).session.reset()


def busy_reasons(session: SessionState) -> list[str]:
    """Public roll-up of what is running (["chat", "research", …])."""
    return SessionManager._busy_reasons(session)


def has_unsaved_progress(session: SessionState) -> bool:
    """True when the session holds work worth saving before it is discarded.

    Any conversation history, document content, chat-authored figure,
    attached reference document, or imported source counts. Deliberately
    coarse — there is no since-last-save dirty flag, so a fresh, untouched
    session never prompts, and anything else always offers the save. Figures
    and reference documents are counted explicitly so a session whose only
    work is a diagram or an attached standard still offers to save (neither
    merely rides the chat history — a reference document can be attached
    before a single message is sent, and the New-session / Open save gate
    depends on this being true).
    """
    return (
        bool(session.history)
        or not session.doc.doc.is_empty()
        or bool(session.figures.figures)
        or bool(session.references.docs)
        or session.import_report is not None
        or session.source_docx_bytes is not None
    )


def _portable_source_attachment(
    session: SessionState,
) -> tuple[bytes | None, str, Any | None]:
    """Return source artifacts only while the imported baseline still exists.

    Undoing before import keeps the redo tail (and therefore its baseline), so
    the attachment remains portable. Committing a new branch truncates that
    tail and ``DocumentStore`` clears ``baseline_index``; at that point source
    bytes/map can no longer be proven against this project and must not be
    written into a package that would reject itself on load.
    """
    baseline_index = session.doc.baseline_index
    if (
        isinstance(baseline_index, bool)
        or not isinstance(baseline_index, int)
        or not 0 <= baseline_index < len(session.doc.versions)
    ):
        return None, "", None
    return (
        session.source_docx_bytes,
        session.source_docx_filename,
        getattr(session, "source_docx_map", None),
    )


def project_payload(session: SessionState) -> dict[str, Any]:
    """The semantic JSON payload embedded in a portable project.

    Legacy JSON loading consumes this shape directly. Native/browser saves
    wrap it through :func:`project_package_bytes` so exact source bytes remain
    a separate member.
    """
    research_profile = session.research.profile_result
    source_bytes, _source_filename, source_docx_map = (
        _portable_source_attachment(session)
    )
    if source_bytes is None:
        source_docx_map = None
    source_map_payload = (
        source_docx_map.to_dict()
        if source_docx_map is not None and hasattr(source_docx_map, "to_dict")
        else source_docx_map
    )
    qc_record = session.qc.audit_record_snapshot()
    return save_project(
        session.history,
        session.doc,
        session.module.module_id,
        requirements_profile=(
            research_profile.to_dict() if research_profile else None
        ),
        audit_result=session.audit.result,
        qc_result=qc_record["result"],
        qc_latest_attempt=qc_record["latest_attempt"],
        # Keep the legacy top-level field populated for older builds while
        # the versioned document identity is authoritative in this build.
        discipline=effective_discipline(session),
        project_context=session.project_context,
        figures=session.figures.to_dict(),
        suggested_prompts=list(session.suggested_prompts),
        reference_docs=session.references.to_dict(),
        import_report=session.import_report,
        source_map=source_map_payload,
        template_origin=session.template_origin,
    )


def project_package_bytes(session: SessionState) -> bytes:
    """Return the portable ``.baspec`` representation of ``session``.

    Single source of truth for both the API download and native save-on-close.
    The exact imported source is a distinct binary ZIP member; it is never
    encoded into or mixed with the semantic project JSON.
    """
    source_bytes, source_filename, _source_docx_map = (
        _portable_source_attachment(session)
    )
    try:
        source_patch_context = (
            session.ensure_source_patch_context()
            if source_bytes is not None
            else None
        )
    except (TypeError, ValueError) as exc:
        raise ProjectPackageError(
            f"The retained source context could not be validated: {exc}"
        ) from exc
    return build_project_package(
        project_payload(session),
        source_docx_bytes=source_bytes,
        source_docx_filename=source_filename,
        source_patch_context=source_patch_context,
    )


def project_default_stem(session: SessionState) -> str:
    """Section-derived stem used by the portable project filename."""
    return session.doc.doc.number.replace(" ", "") or "draft"


def project_default_filename(session: SessionState) -> str:
    """Timestamped filename for a saved project.

    ``buildaspec-<stem>-<YYYY-MM-DD-HHMMSS>.baspec`` (UTC). Single source of
    truth for both the ``/api/project/save`` download and the native
    save-on-close path. The time component (not just the date) is
    deliberate: two saves of the same section on the same day still need
    distinct names, or the native Save dialog would default to the prior
    save's filename and risk silently overwriting it.
    """
    stem = project_default_stem(session)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    return f"buildaspec-{stem}-{stamp}.baspec"


def clone_session_for_tutorial(source: SessionState) -> SessionState:
    """Create a detached semantic clone while retaining the original object.

    The project payload supplies independent mutable stores. Exact source bytes
    and the validated source indexes are immutable inputs and can be shared.
    The original is never reset or reconstructed when the tutorial finishes.
    """
    with source.session_state_guard():
        payload = copy.deepcopy(project_payload(source))
        source_bytes = source.source_docx_bytes
        source_filename = source.source_docx_filename
        source_map = source.source_docx_map
        source_context = source.source_patch_context
        usage = source.usage.snapshot()
    clone = SessionState()
    load_project(payload, clone)
    clone.source_docx_bytes = source_bytes
    clone.source_docx_filename = source_filename
    clone.source_docx_map = source_map
    clone.source_patch_context = source_context
    clone.usage.load_snapshot(usage)
    return clone
