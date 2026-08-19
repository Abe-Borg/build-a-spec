"""Starting a new session leaves nothing of the old one behind.

``SessionState.reset()`` is the wipe: "New session" (the blank-slate choice
in ``NewSessionDialog``) is ``POST /api/session/reset``, and after it the
user is entitled to a session that holds none of the previous project's
work — no transcript, no document or version history, no imported master,
no figures, no attached references, no research or Final QC findings, and a
spend meter back at zero.

The interesting test here is not any one of those (each is a line in
``_reset_while_locked``, and a line is easy to write). It is
:func:`test_every_session_field_is_classified_as_wiped_or_kept`: every
field on the dataclass must be named either as state the reset restores or
as one of the few it deliberately keeps. A field added to ``SessionState``
without that decision being made fails this module, which is the only way
"everything gets wiped" survives the next feature that adds a store.
"""
from __future__ import annotations

import dataclasses
import json

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.llm.conversation import SessionState
from backend.research.engine import RequirementsProfile
from backend.spec_doc.source_mapping import SourceBodyMap


# How to read a field's *state* for the fresh-vs-reset comparison. Object
# identity is not the question — reset installs new store/runner instances
# on purpose — so composite fields project through their own serialization
# or snapshot, and everything else compares by value.
_STATE_PROBES = {
    "history": lambda s: list(s.history),
    "doc": lambda s: s.doc.to_dict(),
    "source_docx_bytes": lambda s: s.source_docx_bytes,
    "source_docx_filename": lambda s: s.source_docx_filename,
    "source_docx_map": lambda s: s.source_docx_map,
    "source_patch_context": lambda s: s.source_patch_context,
    "_capability_cache": lambda s: s._capability_cache,
    "_pending_capability_cache": lambda s: s._pending_capability_cache,
    "_capability_warm": lambda s: s._capability_warm,
    "_capability_warm_next": lambda s: s._capability_warm_next,
    "import_report": lambda s: s.import_report,
    "template_origin": lambda s: s.template_origin,
    "project_context": lambda s: s.project_context,
    "research": lambda s: (s.research.snapshot(), s.research.profile_result),
    "audit": lambda s: (s.audit.snapshot(), s.audit.result),
    "qc": lambda s: (
        s.qc.audit_record_snapshot(),
        sorted(s.qc.remembered_dismissals()),
    ),
    "figures": lambda s: s.figures.to_dict(),
    "references": lambda s: s.references.to_dict(),
    "suggested_prompts": lambda s: list(s.suggested_prompts),
    "usage": lambda s: s.usage.snapshot(),
    "last_context_tokens": lambda s: s.last_context_tokens,
    "turn_active": lambda s: s.turn_active,
    "_active_turn_token": lambda s: s._active_turn_token,
    "stop_requested": lambda s: s.stop_requested.is_set(),
    "save_target": lambda s: s.save_target,
}

# Fields a reset deliberately does NOT restore, each with the reason it is
# not a leak. Adding a name here is a decision, which is the point of making
# it explicit rather than letting an unclassified field pass silently.
_RESET_KEEPS = {
    # The invalidation signal itself: it MUST advance, so an in-flight turn
    # from the discarded session discards its own work instead of committing
    # into the fresh one.
    "generation",
    # Documented app semantics (see the SessionState docstring): a reset
    # keeps the active module and discipline — "a fresh session in the same
    # discipline". The blank-slate path does not rely on that; it passes
    # ``module_id="generic", discipline=""`` explicitly.
    "module",
    "discipline",
    # Synchronization primitives. They carry no session data, and replacing
    # a lock a caller may be holding is how you lose a session, not wipe it.
    "_capability_warm_lock",
    "_turn_state_lock",
}


def _probe(session: SessionState) -> dict[str, object]:
    return {name: read(session) for name, read in _STATE_PROBES.items()}


def _dirty(session: SessionState) -> None:
    """Give every wiped field a value a fresh session would never have.

    Written directly rather than driven through the API on purpose: this
    module tests the wipe, so what matters is that each field is provably
    non-default going in, not the route that got it there.
    """
    session.history.append({"role": "user", "content": "draft 21 13 13"})
    session.doc.begin_turn()
    session.doc.apply_edits(
        [{"action": "add_article", "target_id": "pt1", "text": "SUMMARY"}]
    )
    session.doc.commit_turn()
    session.source_docx_bytes = b"PK\x03\x04 pretend master"
    session.source_docx_filename = "office-master.docx"
    session.source_docx_map = SourceBodyMap(
        source_sha256="a" * 64,
        document_xml_sha256="b" * 64,
        baseline_projection_sha256="c" * 64,
        body_child_count=0,
        body_blocks=(),
        bindings={},
    )
    session.source_patch_context = object()
    session._capability_cache = (("state",), object())
    session._pending_capability_cache = (("state",), object())
    session._capability_warm = object()
    session._capability_warm_next = ("queued",)
    session.import_report = {"filename": "office-master.docx", "warnings": []}
    session.template_origin = {"template_id": "tpl-1", "name": "Starter"}
    session.project_context = "an old project's primer"
    session.research.profile_result = RequirementsProfile(
        items=[], research_date="2026-07-01"
    )
    session.audit.restore({"summary": "audited"})
    session.qc.restore_attempt(
        {
            "run_id": "qc-run-from-the-old-project",
            "status": "failed",
            "error": "the reviewer could not be reached",
            "error_kind": "auth_error",
        }
    )
    session.figures.create(
        {
            "kind": "table",
            "title": "Sprinkler schedule",
            "columns": ["Area", "Density"],
            "rows": [["Data hall", "0.30 gpm/sf"]],
        },
        message_index=0,
    )
    session.references.add(
        filename="owner-standard.pdf",
        text="[page 1] Owner standard.",
        block_count=1,
        kind="pdf",
    )
    session.suggested_prompts.append("It's a hyperscale data center.")
    session.usage.add("interview", {"input_tokens": 100, "output_tokens": 20})
    session.last_context_tokens = 142_000
    session.turn_active = True
    session._active_turn_token = object()
    session.stop_requested.set()
    session.save_target = "/home/abe/projects/the-previous-project.baspec"


def test_every_session_field_is_classified_as_wiped_or_kept():
    """A new SessionState field must be declared wiped or deliberately kept.

    This is the guarantee the individual assertions below cannot give: they
    only cover the state that existed when they were written. A store added
    for a future feature lands here first, and the author has to say which
    it is.
    """
    declared = {field.name for field in dataclasses.fields(SessionState)}
    classified = set(_STATE_PROBES) | _RESET_KEEPS
    assert declared - classified == set(), (
        "new SessionState field(s) are neither wiped by reset nor on the "
        "deliberate keep-list — add a probe to _STATE_PROBES (and the clear "
        "to _reset_while_locked) or justify the keep in _RESET_KEEPS"
    )
    assert classified - declared == set(), (
        "_STATE_PROBES/_RESET_KEEPS name field(s) SessionState no longer has"
    )


def test_reset_restores_every_wiped_field_to_its_fresh_value():
    fresh = SessionState()
    used = SessionState()
    _dirty(used)

    # The fixture is only meaningful if it actually dirtied everything.
    before = _probe(used)
    baseline = _probe(fresh)
    unchanged = [name for name in _STATE_PROBES if before[name] == baseline[name]]
    assert not unchanged, f"_dirty left these at their fresh value: {unchanged}"

    used.reset()

    assert _probe(used) == baseline


def test_the_wipe_survives_a_module_or_discipline_switch():
    """The blank-slate path passes a reset body; it must wipe just as hard."""
    fresh = SessionState()
    used = SessionState()
    _dirty(used)

    used.reset(module_id="generic", discipline="", project_context="")

    assert _probe(used) == _probe(fresh)
    assert used.module.module_id == "generic"
    assert used.discipline == ""


def test_a_stop_aimed_at_the_discarded_turn_does_not_survive():
    """Reset clears the stop flag rather than leaving it to the next claim.

    ``claim_model_turn`` clears it too, so this was not reachable as a bug —
    but "a reset leaves no session state behind" is the property, and it
    should hold at the reset, not one hop away.
    """
    session = SessionState()
    session.stop_requested.set()

    session.reset()

    assert not session.stop_requested.is_set()


def test_reset_through_the_endpoint_wipes_the_live_session():
    """End to end: the route the New-session dialog actually calls."""
    from backend import sessions

    client = TestClient(create_app())
    session = sessions.get_session()
    _dirty(session)
    session.turn_active = False  # the route's own guard is tested elsewhere

    resp = client.post(
        "/api/session/reset",
        json={"module_id": "generic", "discipline": "", "project_context": ""},
    )
    assert resp.status_code == 200

    assert _probe(sessions.get_session()) == _probe(SessionState())
    # And the surface the panel reads agrees: nothing of the old project.
    payload = client.get("/api/doc").json()
    assert "SUMMARY" not in json.dumps(payload["doc"])
    assert payload["figures"] == []
    assert payload["reference_docs"] == []
    assert payload["suggested_prompts"] == []
    assert payload["import_report"] is None
    assert payload["template_origin"] is None
    assert client.get("/api/usage").json()["totals"] == {}


def test_a_wiped_session_no_longer_offers_to_save():
    """`has_unsaved_progress` is the save gate's predicate — nothing left."""
    from backend import sessions

    session = SessionState()
    _dirty(session)
    assert sessions.has_unsaved_progress(session)

    session.reset()

    assert not sessions.has_unsaved_progress(session)
