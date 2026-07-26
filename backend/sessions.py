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
from typing import Any, Iterator, Literal

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
        self._transitioning = False
        self._tutorial_usage_before: dict[str, Any] | None = None
        self._scenario_usage_before: dict[str, Any] | None = None

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

    def original_for_save(self) -> SessionState:
        """Return the retained original only while a tutorial owns it."""
        with self._lock:
            if self._scope == "original" or self._original is None:
                raise WorkspaceConflictError("No protected original project is retained.")
            return self._original

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
            if self._transitioning:
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
            reasons = self._busy_reasons(self._active)
            if reasons:
                raise WorkspaceBusyError(reasons)
            self._transitioning = True
            original = self._active
            activation = self._workspace_id
        try:
            tutorial = staged_session or clone_session_for_tutorial(original)
        except Exception:
            with self._lock:
                self._transitioning = False
            raise
        with self._lock:
            if self._workspace_id != activation or self._active is not original:
                self._transitioning = False
                raise WorkspaceConflictError("The workspace changed during tutorial setup.")
            self._original = original
            self._tutorial = tutorial
            self._tutorial_id = uuid.uuid4().hex
            self._tutorial_request_id = request_id
            self._tutorial_source = source
            self._tutorial_usage_before = tutorial.usage.snapshot()
            self._activate(tutorial, "tutorial")
            self._transitioning = False
            return self.current()

    def push_scenario(
        self,
        expected_workspace_id: int,
        *,
        kind: str,
        staged_session: SessionState | None = None,
    ) -> WorkspaceLease:
        allowed = {
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
            reasons = self._busy_reasons(self._active)
            if reasons:
                raise WorkspaceBusyError(reasons)
            self._transitioning = True
            tutorial = self._active
            activation = self._workspace_id
        try:
            scenario = staged_session or clone_session_for_tutorial(tutorial)
        except Exception:
            with self._lock:
                self._transitioning = False
            raise
        with self._lock:
            if self._workspace_id != activation or self._active is not tutorial:
                self._transitioning = False
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
            self._transitioning = False
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

    def finish_tutorial(
        self, expected_workspace_id: int, *, disposition: Literal["restore", "keep"]
    ) -> WorkspaceLease:
        with self._lock:
            self._check_expected(expected_workspace_id)
            if self._scope != "tutorial" or self._tutorial is None:
                raise WorkspaceConflictError(
                    "Finish the active scenario before resolving the tutorial."
                )
            if self._active_writes:
                raise WorkspaceBusyError(["another edit or upload"])
            reasons = self._busy_reasons(self._active)
            if reasons:
                raise WorkspaceBusyError(reasons)
            tutorial = self._tutorial
            if disposition == "restore":
                if self._original is None:
                    raise WorkspaceConflictError("The original workspace is unavailable.")
                original = self._original
                original.usage.merge_delta(
                    self._tutorial_usage_before or {}, tutorial.usage.snapshot()
                )
                tutorial.invalidate_model_turn()
                active = original
            else:
                active = tutorial
            self._original = None
            self._tutorial = None
            self._scenario = None
            self._tutorial_id = None
            self._tutorial_request_id = None
            self._tutorial_source = None
            self._scenario_kind = None
            self._tutorial_usage_before = None
            self._scenario_usage_before = None
            self._activate(active, "original")
            return self.current()

    def force_restore_original(self) -> WorkspaceLease:
        with self._lock:
            if self._scope == "original":
                return self.current()
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
            self._transitioning = False
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
            if self._transitioning:
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
    _manager.force_restore_original().session.reset()


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
