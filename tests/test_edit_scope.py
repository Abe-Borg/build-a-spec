"""The edit scope: Extensive, or Essentials only (owner request, 2026-10-19).

Some clients want their own specification changed as little as possible.
``backend/edit_scope.py`` is the one definition of what "essential" means;
the chat's stable prompt, the turn's PROJECT CONTEXT, the adapt pass and
Final QC all render it. What is pinned here:

1. the definition is versioned and its hash pinned, carries the owner's
   rulings (scope gaps, inapplicable content and explanatory prose are
   essential; a compliant choice is asked about, never edited), and
   sanitizes to Extensive;
2. the chat: the stable prompt carries "# Edit scope" once whatever the
   mode, and the mode rides one PROJECT CONTEXT line, so switching it moves
   neither the system prompt nor the project block;
3. the adapt pass records its scope after the constant, so the pass still
   runs at the whole-section effort;
4. the section: the route sets it (and refuses an unknown scope and a tour),
   the doc payload carries it, the project file writes it only when
   Essentials, a load replaces it, and Next section carries it;
5. Final QC: under Essentials every lens and seat system prompt carries the
   scope and the manifest records it; under Extensive the prompts and the
   fingerprint are what they were before the mode existed; and a review
   made under one scope reads stale under the other.
"""
from __future__ import annotations

import hashlib

import pytest

from backend import edit_scope, sessions, settings
from backend.edit_scope import (
    CORE_SHA256,
    EDIT_SCOPE_VERSION,
    ESSENTIAL_CATEGORIES,
    ESSENTIALS,
    EXTENSIVE,
    core_text,
    directive_line,
    drafting_block,
    manifest_facts,
    review_block,
    sanitize_edit_scope,
    turn_line,
)
from backend.llm.conversation import SessionState, _turn_context, turn_effort
from backend.llm.prompts import (
    ADAPT_IMPORTED_DIRECTIVE,
    adapt_imported_directive,
    draft_prerequisites,
    render_system_prompt,
)
from backend.qc import engine
from backend.qc.engine import (
    _lens_system_prompt,
    _verifier_system_prompt,
    build_qc_input_manifest,
    qc_input_fingerprint,
    run_final_qc,
)
from backend.spec_doc.model import iter_paragraphs
from backend.spec_doc.project import load_project, save_project
from backend.spec_doc.project_package import parse_project_file
from backend.spec_modules import DEFAULT_MODULE
from backend.spec_modules.hyperscale_fire import HYPERSCALE_FIRE
from tests.fakes import SequencedFakeClient
from tests.test_project_brief import _client, _rich_session
from tests.test_qc_batch_verification import _one_finding_scripts
from tests.test_qc_verifier_standards import _seat_requests, _store

# Bump EDIT_SCOPE_VERSION whenever this changes: the hash is in every
# Essentials review's manifest, so an edit to the definition reads those
# reviews stale, and should do so on purpose.
_PINNED_CORE_SHA256 = "d90af77a1c52c40f3a62e71bb9d5e37e28e815a6dc4254761efb3db4b4660169"


# ---------------------------------------------------------------------------
# The definition
# ---------------------------------------------------------------------------


def test_the_definition_is_versioned_and_pinned():
    assert EDIT_SCOPE_VERSION == 1
    assert CORE_SHA256 == _PINNED_CORE_SHA256
    assert CORE_SHA256 == hashlib.sha256(core_text().encode("utf-8")).hexdigest()


def test_the_owners_rulings_are_in_the_definition():
    """2026-10-19: scope gaps, content that cannot apply, and explanatory
    prose and notes are essential; a compliant choice the assistant would
    make differently is asked about, never edited."""
    keys = [category.key for category in ESSENTIAL_CATEGORIES]
    assert len(keys) == len(set(keys))
    for key in ("scope_gap", "inapplicable_content", "explanatory_prose"):
        assert key in keys
    text = " ".join(core_text().split())
    assert "Scope gap:" in text
    assert "Inapplicable content:" in text
    assert "a note to the specifier, the design team or the editor, removed" in text
    assert "A compliant choice you would make differently is not a defect: raise it as a question, never edit it." in text
    assert "Content that only seems unnecessary is asked about, never deleted." in text
    block = " ".join(drafting_block().split())
    assert "track_followups (not blocking)" in block
    assert "never what the user asks" in block


@pytest.mark.parametrize(
    "value,expected",
    [
        ("essentials", ESSENTIALS),
        ("extensive", EXTENSIVE),
        ("Essentials", EXTENSIVE),
        ("", EXTENSIVE),
        (None, EXTENSIVE),
        (1, EXTENSIVE),
        (["essentials"], EXTENSIVE),
    ],
)
def test_a_bad_value_degrades_to_extensive(value, expected):
    assert sanitize_edit_scope(value) == expected


def test_extensive_adds_nothing_to_a_review_and_essentials_frames_it():
    assert review_block(EXTENSIVE) == ""
    assert review_block(EXTENSIVE, verifier=True) == ""
    assert manifest_facts(EXTENSIVE) is None
    lens = review_block(ESSENTIALS)
    seat = review_block(ESSENTIALS, verifier=True)
    assert lens.startswith('<edit_scope mode="essentials" version="edit-scope/1">')
    assert core_text() in lens and core_text() in seat
    assert "Refute a finding whose defect is not essential" in seat
    assert "Refute a finding whose defect is not essential" not in lens
    assert manifest_facts(ESSENTIALS)["core_sha256"] == CORE_SHA256


# ---------------------------------------------------------------------------
# The chat
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module", [HYPERSCALE_FIRE, DEFAULT_MODULE])
def test_the_stable_prompt_carries_the_rules_once(module):
    prompt = render_system_prompt(module)
    assert prompt.count("# Edit scope") == 1
    assert drafting_block() in prompt
    # After the writing policy it qualifies, before the interview policy.
    assert prompt.index("# Specification writing policy") < prompt.index("# Edit scope")
    assert prompt.index("# Edit scope") < prompt.index("# Interview policy")


def test_the_mode_rides_one_project_context_line_and_moves_no_cache():
    session = SessionState()
    extensive = _turn_context(session)
    session.edit_scope = ESSENTIALS
    essentials = _turn_context(session)

    assert turn_line(EXTENSIVE) in extensive.turn_text
    assert turn_line(ESSENTIALS) not in extensive.turn_text
    assert turn_line(ESSENTIALS) in essentials.turn_text
    assert essentials.turn_text.count("EDIT SCOPE:") == 1
    # Only the per-turn block changes: the cached blocks are untouched.
    assert essentials.project_block == extensive.project_block
    assert essentials.turn_text.replace(
        turn_line(ESSENTIALS), turn_line(EXTENSIVE)
    ) == extensive.turn_text


def _ready():
    return draft_prerequisites(
        section_number="21 13 13",
        section_title="WET-PIPE SPRINKLER SYSTEMS",
        project_type="Data Center",
        country="US",
    )


@pytest.mark.parametrize("scope", [EXTENSIVE, ESSENTIALS])
def test_the_adapt_pass_records_its_scope_and_keeps_its_effort(scope):
    message = adapt_imported_directive(_ready(), scope)
    assert message.startswith(ADAPT_IMPORTED_DIRECTIVE)
    assert message.endswith(directive_line(scope))
    assert turn_effort(message) == settings.DRAFT_PASS_EFFORT
    if scope == ESSENTIALS:
        assert "Essentials only" in message
        assert "open each edit's reason with its essential category" in message


def test_the_adapt_route_hands_back_the_sections_scope():
    client = _client()
    session = _rich_session(client)
    marked = 0
    for _part, _article, paragraph, _depth, _ref in iter_paragraphs(session.doc.doc):
        paragraph.status = "imported"
        marked += 1
    assert marked
    session.doc.doc.number = "21 13 13"
    session.doc.doc.title = "WET-PIPE SPRINKLER SYSTEMS"
    session.edit_scope = ESSENTIALS
    resp = client.post("/api/draft/adapt")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ready"] is True, body["missing"]
    assert body["message"].endswith(directive_line(ESSENTIALS))


# ---------------------------------------------------------------------------
# The section
# ---------------------------------------------------------------------------


def test_the_route_sets_the_scope_and_the_payload_carries_it():
    client = _client()
    session = sessions.get_session()
    assert session.edit_scope == EXTENSIVE
    assert client.get("/api/doc").json()["edit_scope"] == EXTENSIVE

    resp = client.post("/api/session/edit-scope", json={"scope": "essentials"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True, "edit_scope": "essentials"}
    assert session.edit_scope == ESSENTIALS
    assert client.get("/api/doc").json()["edit_scope"] == ESSENTIALS

    for bad in ("", "Essentials", "minimal"):
        refused = client.post("/api/session/edit-scope", json={"scope": bad})
        assert refused.status_code == 400
    assert session.edit_scope == ESSENTIALS

    back = client.post("/api/session/edit-scope", json={"scope": "extensive"})
    assert back.status_code == 200 and session.edit_scope == EXTENSIVE


def test_the_route_refuses_a_tour():
    client = _client()
    original = sessions.get_workspace()
    started = client.post(
        "/api/tutorial/start",
        json={
            "request_id": "edit-scope-refuses-in-a-tour",
            "source": "showcase",
            "workspace_id": original.workspace_id,
            "generation": original.generation,
        },
    )
    assert started.status_code == 200, started.text
    try:
        resp = client.post("/api/session/edit-scope", json={"scope": "essentials"})
        assert resp.status_code == 409 and resp.json()["code"] == "tutorial_active"
    finally:
        sessions.reset_session()


def test_the_project_file_writes_the_scope_only_when_essentials():
    live = SessionState()
    assert "edit_scope" not in parse_project_file(sessions.project_package(live)[0]).project

    live.edit_scope = ESSENTIALS
    project = parse_project_file(sessions.project_package(live)[0]).project
    assert project["edit_scope"] == ESSENTIALS

    reloaded = SessionState()
    load_project(project, reloaded)
    assert reloaded.edit_scope == ESSENTIALS

    # A load replaces the scope rather than inheriting it.
    extensive_file = save_project([], SessionState().doc, DEFAULT_MODULE.module_id)
    assert "edit_scope" not in extensive_file
    load_project(extensive_file, reloaded)
    assert reloaded.edit_scope == EXTENSIVE

    # A malformed value loads as Extensive rather than failing the load.
    tampered = dict(project, edit_scope="everything")
    load_project(tampered, reloaded)
    assert reloaded.edit_scope == EXTENSIVE


def test_next_section_carries_the_scope():
    client = _client()
    _rich_session(client)
    client.post("/api/session/edit-scope", json={"scope": "essentials"})
    resp = client.post(
        "/api/project/next-section", json={"number": "21 30 00", "title": "Fire Pumps"}
    )
    assert resp.status_code == 200, resp.text
    assert sessions.get_session().edit_scope == ESSENTIALS
    assert resp.json()["session"]["edit_scope"] == ESSENTIALS


def test_a_reset_starts_extensive():
    client = _client()
    client.post("/api/session/edit-scope", json={"scope": "essentials"})
    sessions.reset_session()
    assert sessions.get_session().edit_scope == EXTENSIVE


# ---------------------------------------------------------------------------
# Final QC
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module", [HYPERSCALE_FIRE, DEFAULT_MODULE])
def test_extensive_qc_prompts_are_byte_identical_to_before(module):
    """The default argument is Extensive, which is what every caller sent
    before the mode existed: nothing is added."""
    assert _lens_system_prompt(module, EXTENSIVE) == _lens_system_prompt(module)
    assert "<edit_scope" not in _lens_system_prompt(module)
    assert "<edit_scope" not in _verifier_system_prompt(module)


@pytest.mark.parametrize("module", [HYPERSCALE_FIRE, DEFAULT_MODULE])
def test_essentials_qc_prompts_carry_the_scope_after_the_writing_policy(module):
    lens = _lens_system_prompt(module, ESSENTIALS)
    seat = _verifier_system_prompt(module, ESSENTIALS)
    assert review_block(ESSENTIALS) in lens
    assert review_block(ESSENTIALS, verifier=True) in seat
    for prompt in (lens, seat):
        assert prompt.count("<edit_scope") == 1
        assert prompt.index("</writing_policy>") < prompt.index("<edit_scope")


def _system_text(params: dict) -> str:
    system = params.get("system")
    if isinstance(system, str):
        return system
    return "".join(block.get("text", "") for block in system or [])


def _run(client, *, scope, store, batch=False):
    return run_final_qc(
        store.doc,
        None,
        HYPERSCALE_FIRE,
        client,
        model=settings.QC_MODEL,
        max_tokens=4096,
        version_index=store.index,
        started_at="2026-10-19T10:00:00-07:00",
        finished_at="2026-10-19T10:01:00-07:00",
        run_id="qc-edit-scope-test",
        batch_verification=batch,
        refusal_fallback=False,
        edit_scope=scope,
    )


@pytest.mark.parametrize("batch", [False, True])
def test_every_lens_and_seat_reviews_under_essentials(batch):
    store = _store()
    client = SequencedFakeClient(_one_finding_scripts())
    result = _run(client, scope=ESSENTIALS, store=store, batch=batch)

    seats = _seat_requests(client)
    lenses = [request for request in client.requests if request not in seats]
    assert len(seats) == 2 and lenses
    for params in lenses:
        system = _system_text(params)
        if "submit_qc_findings" in system:
            assert review_block(ESSENTIALS) in system
    for params in seats:
        assert review_block(ESSENTIALS, verifier=True) in _system_text(params)
    assert result.input_manifest["edit_scope"] == manifest_facts(ESSENTIALS)


def test_an_extensive_run_sends_no_scope_and_records_none():
    store = _store()
    client = SequencedFakeClient(_one_finding_scripts())
    result = _run(client, scope=EXTENSIVE, store=store)
    assert all("<edit_scope" not in _system_text(params) for params in client.requests)
    assert "edit_scope" not in result.input_manifest


def test_an_extensive_manifest_keeps_its_fingerprint():
    """Every review made before the mode existed was an Extensive review;
    the key is left out, so its fingerprint does not move."""
    store = _store()
    kwargs = dict(
        version_index=store.index,
        model=settings.QC_MODEL,
        max_tokens=4096,
    )
    before = build_qc_input_manifest(store.doc, None, HYPERSCALE_FIRE, **kwargs)
    extensive = build_qc_input_manifest(
        store.doc, None, HYPERSCALE_FIRE, edit_scope=EXTENSIVE, **kwargs
    )
    essentials = build_qc_input_manifest(
        store.doc, None, HYPERSCALE_FIRE, edit_scope=ESSENTIALS, **kwargs
    )
    assert qc_input_fingerprint(before) == qc_input_fingerprint(extensive)
    assert qc_input_fingerprint(essentials) != qc_input_fingerprint(extensive)


def test_a_review_reads_stale_under_the_other_scope(monkeypatch):
    monkeypatch.setattr(settings, "QC_BATCH_VERIFICATION", False)
    store = _store()
    essentials = _run(
        SequencedFakeClient(_one_finding_scripts()), scope=ESSENTIALS, store=store
    )
    extensive = _run(
        SequencedFakeClient(_one_finding_scripts()), scope=EXTENSIVE, store=store
    )
    args = (store.index, store.doc, None, HYPERSCALE_FIRE)
    assert essentials.matches_inputs(*args, edit_scope=ESSENTIALS)
    assert not essentials.matches_inputs(*args, edit_scope=EXTENSIVE)
    assert extensive.matches_inputs(*args, edit_scope=EXTENSIVE)
    assert extensive.matches_inputs(*args)
    assert not extensive.matches_inputs(*args, edit_scope=ESSENTIALS)


def test_the_apply_gate_reads_the_live_scope():
    """The panel's Apply, apply_qc_fixes, readiness's qc_current and the
    chat's QC block all ask matches_current_inputs: it must pass the
    section's live scope."""
    from backend.qc import apply as qc_apply

    seen: dict[str, str] = {}

    class _Result:
        def matches_inputs(self, *args, **kwargs):
            seen["edit_scope"] = kwargs.get("edit_scope")
            return True

    session = SessionState()
    session.edit_scope = ESSENTIALS
    qc_apply.matches_current_inputs(session, _Result(), source_guard=None)
    assert seen["edit_scope"] == ESSENTIALS


def test_the_module_reads_no_session_state():
    """The definition is a leaf: nothing session-varying may render into the
    stable prompt it feeds."""
    source = open(edit_scope.__file__, encoding="utf-8").read()
    assert "from ." not in source and "import backend" not in source


def test_an_essentials_review_survives_the_project_file(monkeypatch):
    """The manifest's optional key must round-trip through the saved report:
    a strict loader that refused an unknown key would drop the review."""
    monkeypatch.setattr(settings, "QC_BATCH_VERIFICATION", False)
    store = _store()
    result = _run(
        SequencedFakeClient(_one_finding_scripts()), scope=ESSENTIALS, store=store
    )
    restored = engine.QCResult.from_dict(result.to_dict())
    assert restored.input_manifest["edit_scope"] == manifest_facts(ESSENTIALS)
    assert restored.input_fingerprint == result.input_fingerprint
    args = (store.index, store.doc, None, HYPERSCALE_FIRE)
    assert restored.matches_inputs(*args, edit_scope=ESSENTIALS)
    assert not restored.matches_inputs(*args, edit_scope=EXTENSIVE)
