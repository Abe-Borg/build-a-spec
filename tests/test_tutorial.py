"""Actual-content tutorial workspace and coverage contract tests."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import sessions
from backend.app import _prepare_master_import, create_app
from backend.llm.client import MissingApiKeyError
from backend.llm.conversation import SessionState
from backend.sessions import SessionManager, WorkspaceBusyError, WorkspaceConflictError
from backend.spec_doc.docx_export import build_docx
from backend.spec_doc import lint_document
from backend.spec_doc.model import SpecSection, iter_paragraphs
from backend.tutorial import (
    analyze_tutorial_coverage,
    blank_practice_copy,
    build_showcase_session,
    detached_practice_copy,
    media_practice_copy,
    reference_practice_copy,
    repair_tutorial_copy,
    review_practice_copy,
    structural_practice_copy,
    validate_tutorial_enrichment,
)
from backend.templates import TemplateCatalog
from backend.spec_doc.project_package import parse_project_package
from tests.fakes import FakeClient, text_turn, tool_turn


def _replace_first(session: SessionState, suffix: str) -> None:
    first = next(iter(iter_paragraphs(session.doc.doc)))[2]
    session.doc.begin_turn()
    session.doc.apply_edits(
        [
            {
                "action": "replace",
                "target_id": first.uid,
                "text": first.text + suffix,
                "status": first.status,
            }
        ]
    )
    session.doc.commit_turn()


def test_bundled_llm_authored_showcase_satisfies_real_content_fixtures():
    session = build_showcase_session()
    coverage = analyze_tutorial_coverage(session)

    assert coverage.ready is True
    assert coverage.gaps == ()
    assert coverage.counts["articles"] >= 2
    assert coverage.counts["paragraphs"] >= 8
    assert coverage.counts["versions"] >= 2
    assert {
        "first_paragraph",
        "first_assumed",
        "first_needs_input",
        "tbd_paragraph",
        "nested_paragraph",
        "article_move_source",
        "article_move_target",
        "paragraph_move_source",
        "paragraph_move_target",
    } <= set(coverage.anchors)

    live_ids = {p.uid for _pt, _a, p, _d, _r in iter_paragraphs(session.doc.doc)}
    article_ids = {
        article.uid for part in session.doc.doc.parts for article in part.articles
    }
    assert coverage.anchors["first_paragraph"] in live_ids
    assert coverage.anchors["paragraph_move_source"] in live_ids
    assert coverage.anchors["article_move_source"] in article_ids
    assert session.research.status == "idle"
    assert session.qc.status == "idle"
    assert session.source_docx_bytes is None
    assistant_count = sum(
        1 for message in session.history if message.get("role") == "assistant"
    )
    assert assistant_count == 1
    # Figures no longer come from tutorial start — Chapter 6 (media_practice_copy)
    # generates them live when the tour reaches that chapter, not before.
    assert session.figures.snapshot() == []
    assert session.template_origin is None


def test_sparse_current_spec_reports_conditions_not_article_count_only():
    sparse = SessionState()
    sparse.doc.doc = SpecSection.empty()
    coverage = analyze_tutorial_coverage(sparse)

    assert coverage.ready is False
    assert {
        "section_number",
        "section_title",
        "substantive_content",
        "all_parts",
        "article_siblings",
        "paragraph_siblings",
        "four_paragraph_levels",
        "assumed_content",
        "needs_input_content",
        "tbd_content",
        "version_history",
        "suggested_prompts",
    } == set(coverage.gaps)


def test_restore_returns_the_exact_original_object_and_merges_only_usage_delta():
    manager = SessionManager()
    original = manager.current().session
    original.history.append(
        {"role": "user", "content": [{"type": "text", "text": "Original work"}]}
    )
    original.usage.add("interview", {"input_tokens": 10}, count_turn=True)
    before_doc = copy.deepcopy(original.doc.to_dict())
    before_history = copy.deepcopy(original.history)

    tutorial_lease = manager.begin_tutorial(request_id="restore-contract")
    assert tutorial_lease.session is not original
    tutorial_lease.session.history.append(
        {"role": "assistant", "content": [{"type": "text", "text": "Tutorial only"}]}
    )
    tutorial_lease.session.usage.add(
        "template", {"input_tokens": 4, "output_tokens": 2}, count_turn=True
    )
    restored = manager.finish_tutorial(
        tutorial_lease.workspace_id, disposition="restore"
    )

    assert restored.scope == "original"
    assert restored.session is original
    assert original.doc.to_dict() == before_doc
    assert original.history == before_history
    usage = original.usage.snapshot()
    assert usage["categories"]["interview"]["input_tokens"] == 10
    assert usage["categories"]["template"] == {
        "input_tokens": 4,
        "output_tokens": 2,
    }
    assert usage["turns"] == 2


def test_rich_source_backed_restore_preserves_every_original_store_exactly():
    manager = SessionManager()
    original = manager.current().session
    showcase = build_showcase_session()
    source_bytes = build_docx(showcase.doc.doc)
    imported, report, context = _prepare_master_import(source_bytes, "rich-source.docx")
    original.doc.adopt_imported(imported.section)
    original.history = copy.deepcopy(showcase.history)
    # build_showcase_session() no longer seeds figures (Chapter 6 generates
    # them live); seed one directly so the store-independence assertions
    # below still have a figure to exercise.
    original.figures.create(
        {"kind": "table", "title": "Original figure", "columns": ["A"], "rows": [["1"]]},
        message_index=0,
    )
    referenced = reference_practice_copy(showcase)
    original.references.load(referenced.references.to_dict())
    original.suggested_prompts = list(showcase.suggested_prompts)
    original.module = showcase.module
    original.discipline = "Building enclosure"
    original.project_context = "Distinctive original primer"
    original.source_docx_bytes = source_bytes
    original.source_docx_filename = "rich-source.docx"
    original.source_docx_map = imported.source_map
    original.source_patch_context = context
    original.import_report = copy.deepcopy(report)
    original.research.status = "failed"
    original.research.error = "retained research failure"
    original.research.events = [{"seq": 0, "type": "research_failed"}]
    original.audit.status = "failed"
    original.audit.error = "retained audit failure"
    original.qc.status = "failed"
    original.qc.error = "retained QC failure"
    original.qc.events = [{"seq": 0, "type": "qc_failed"}]
    original.usage.add("interview", {"input_tokens": 9}, count_turn=True)

    def stable_payload(session):
        payload = copy.deepcopy(sessions.project_payload(session))
        payload.pop("saved_at", None)
        return payload

    before = stable_payload(original)
    before_runner_state = (
        original.research.status,
        original.research.error,
        copy.deepcopy(original.research.events),
        original.audit.status,
        original.audit.error,
        original.qc.status,
        original.qc.error,
        copy.deepcopy(original.qc.events),
    )
    source_objects = (
        original.source_docx_bytes,
        original.source_docx_map,
        original.source_patch_context,
    )

    tutorial = manager.begin_tutorial(request_id="rich-restore-contract")
    assert tutorial.session is not original
    assert tutorial.session.doc is not original.doc
    assert tutorial.session.figures is not original.figures
    assert tutorial.session.references is not original.references
    assert tutorial.session.history is not original.history
    assert (
        tutorial.session.source_docx_bytes,
        tutorial.session.source_docx_map,
        tutorial.session.source_patch_context,
    ) == source_objects
    _replace_first(tutorial.session, " Tutorial mutation.")
    tutorial.session.history.append(
        {"role": "user", "content": [{"type": "text", "text": "tutorial only"}]}
    )
    tutorial.session.figures.delete(tutorial.session.figures.figures[0].fid)
    tutorial.session.references.delete(tutorial.session.references.docs[0].rid)
    tutorial.session.suggested_prompts = ["tutorial only"]

    scenario = manager.push_scenario(tutorial.workspace_id, kind="review")
    _replace_first(scenario.session, " Scenario mutation.")
    scenario.session.usage.add(
        "template", {"input_tokens": 3, "output_tokens": 2}, count_turn=True
    )
    restored = manager.force_restore_original()

    assert restored.session is original
    assert stable_payload(original) == before
    assert (
        original.research.status,
        original.research.error,
        original.research.events,
        original.audit.status,
        original.audit.error,
        original.qc.status,
        original.qc.error,
        original.qc.events,
    ) == before_runner_state
    assert (
        original.source_docx_bytes,
        original.source_docx_map,
        original.source_patch_context,
    ) == source_objects
    usage = original.usage.snapshot()
    assert usage["categories"]["interview"]["input_tokens"] == 9
    assert usage["categories"]["template"] == {
        "input_tokens": 3,
        "output_tokens": 2,
    }


def test_scenario_never_nests_and_returns_to_the_exact_tutorial_base():
    manager = SessionManager()
    tutorial = manager.begin_tutorial(
        staged_session=build_showcase_session(), request_id="scenario-contract"
    )
    tutorial_object = tutorial.session
    scenario = manager.push_scenario(tutorial.workspace_id, kind="structural")
    assert scenario.session is not tutorial_object
    _replace_first(scenario.session, " Scenario-only edit.")

    with pytest.raises(WorkspaceConflictError, match="tutorial workspace"):
        manager.push_scenario(scenario.workspace_id, kind="template")

    returned = manager.pop_scenario(scenario.workspace_id)
    assert returned.scope == "tutorial"
    assert returned.session is tutorial_object
    assert "Scenario-only edit." not in str(returned.session.doc.to_dict())


def test_workspace_id_lease_rejects_late_work_and_keep_promotes_tutorial_object():
    manager = SessionManager()
    original_lease = manager.current()
    tutorial_lease = manager.begin_tutorial(
        staged_session=build_showcase_session(), request_id="lease-contract"
    )

    with pytest.raises(WorkspaceConflictError, match="workspace changed"):
        manager.assert_active(original_lease)
    with pytest.raises(WorkspaceConflictError, match="inactive tutorial workspace"):
        with manager.active_write(original_lease.workspace_id):
            pass

    kept_object = tutorial_lease.session
    kept = manager.finish_tutorial(tutorial_lease.workspace_id, disposition="keep")
    assert kept.scope == "original"
    assert kept.session is kept_object


def test_generation_lease_rejects_delayed_work_after_in_place_replacement():
    manager = SessionManager()
    lease = manager.current()
    lease.session.invalidate_model_turn()

    # The object and workspace are unchanged, but a reset/load/template start
    # has made any delayed result captured by this request stale.
    manager.assert_active(lease)
    with pytest.raises(WorkspaceConflictError, match="session was replaced"):
        manager.assert_fresh(lease)


def test_tutorial_start_is_blocked_while_an_upload_or_edit_lease_is_active():
    manager = SessionManager()
    current = manager.current()
    with manager.active_write(current.workspace_id):
        with pytest.raises(WorkspaceBusyError, match="edit or upload"):
            manager.begin_tutorial(request_id="must-wait")
    assert manager.current().scope == "original"


def test_tutorial_start_requires_a_fresh_lease_but_same_request_is_idempotent():
    client = TestClient(create_app())
    original = sessions.get_workspace()
    body = {
        "request_id": "idempotent-start-contract",
        "source": "showcase",
        "workspace_id": original.workspace_id,
        "generation": original.generation,
    }

    first = client.post("/api/tutorial/start", json=body)
    assert first.status_code == 200
    first_payload = first.json()

    retry = client.post("/api/tutorial/start", json=body)
    assert retry.status_code == 200
    assert retry.json()["tutorial_id"] == first_payload["tutorial_id"]
    assert retry.json()["workspace_id"] == first_payload["workspace_id"]

    stale = client.post(
        "/api/tutorial/start",
        json={**body, "request_id": "different-request"},
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "stale_workspace"


def test_detached_structural_practice_clears_every_source_claim():
    showcase = build_showcase_session()
    source_bytes = build_docx(showcase.doc.doc)
    imported, report, context = _prepare_master_import(
        source_bytes, "tutorial-source.docx"
    )
    source = SessionState()
    source.doc.adopt_imported(imported.section)
    source.source_docx_bytes = source_bytes
    source.source_docx_filename = "tutorial-source.docx"
    source.source_docx_map = imported.source_map
    source.source_patch_context = context
    source.import_report = report
    for version in source.doc.versions:
        parsed = SpecSection.from_dict(version)
        for _pt, _article, paragraph, _depth, _reference in iter_paragraphs(parsed):
            paragraph.source_item_id = "source-block"
        version.clear()
        version.update(parsed.to_dict())
    source.doc.doc = SpecSection.from_dict(source.doc.versions[source.doc.index])
    detached = detached_practice_copy(source)
    assert detached.source_docx_bytes is None
    assert detached.source_docx_filename == ""
    assert detached.source_docx_map is None
    assert detached.source_patch_context is None
    assert detached.import_report is None
    assert detached.doc.baseline_index is None
    for version in detached.doc.versions:
        parsed = SpecSection.from_dict(version)
        assert all(
            not paragraph.source_item_id
            for _pt, _article, paragraph, _depth, _reference in iter_paragraphs(parsed)
        )


def test_incomplete_live_enrichment_atomically_falls_back_to_showcase(monkeypatch):
    monkeypatch.setattr("backend.app.stream_user_turn", lambda _session, _message: iter(()))
    current = sessions.get_session()
    current.doc.begin_turn()
    article_id = current.doc.apply_edits(
        [
            {
                "action": "replace",
                "target_id": "sec",
                "numbering": "09 91 23",
                "text": "DISTINCTIVE USER SECTION",
            },
            {
                "action": "add_article",
                "target_id": "pt1",
                "text": "USER ARTICLE — KEEP EXACTLY",
            },
        ]
    )[1]["id"]
    paragraph_id = current.doc.apply_edits(
        [
            {
                "action": "add_paragraph",
                "target_id": article_id,
                "text": "Distinctive user-authored wording that the fallback must preserve exactly.",
                "status": "confirmed",
            }
        ]
    )[0]["id"]
    current.doc.commit_turn()
    client = TestClient(create_app())
    original = sessions.get_workspace()
    started = client.post(
        "/api/tutorial/start",
        json={
            "request_id": "fallback-contract",
            "source": "current",
            "workspace_id": original.workspace_id,
            "generation": original.generation,
        },
    ).json()
    assert started["needs_enrichment"] is True

    response = client.post(
        "/api/tutorial/enrich",
        json={
            "tutorial_id": started["tutorial_id"],
            "workspace_id": started["session"]["workspace_id"],
            "generation": started["session"]["generation"],
        },
    )
    assert response.status_code == 200
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    fallback = next(event for event in events if event["type"] == "tutorial_fallback")
    assert fallback["coverage"]["ready"] is True
    assert fallback["session"]["workspace_scope"] == "tutorial"
    assert fallback["workspace_id"] != started["session"]["workspace_id"]
    repaired = SpecSection.from_dict(fallback["session"]["doc"])
    assert repaired.number == "09 91 23"
    assert repaired.title == "DISTINCTIVE USER SECTION"
    kept_article = next(
        article
        for part in repaired.parts
        for article in part.articles
        if article.uid == article_id
    )
    assert kept_article.title == "USER ARTICLE — KEEP EXACTLY"
    assert kept_article.paragraphs[0].uid == paragraph_id
    assert kept_article.paragraphs[0].text == (
        "Distinctive user-authored wording that the fallback must preserve exactly."
    )
    assert kept_article.paragraphs[0].status == "confirmed"
    # The bundled fallback no longer creates figures either — Chapter 6
    # generates them live when the tour reaches it.
    assert fallback["session"]["figures"] == []
    assert fallback["source"] == "current"


def test_generated_live_failure_is_disclosed_as_bundled_showcase(monkeypatch):
    monkeypatch.setattr("backend.app.stream_user_turn", lambda _s, _m: iter(()))
    client = TestClient(create_app())
    original = sessions.get_workspace()
    started = client.post(
        "/api/tutorial/start",
        json={
            "request_id": "generated-fallback-source",
            "source": "generated",
            "workspace_id": original.workspace_id,
            "generation": original.generation,
        },
    ).json()
    response = client.post(
        "/api/tutorial/enrich",
        json={
            "tutorial_id": started["tutorial_id"],
            "workspace_id": started["workspace_id"],
            "generation": started["generation"],
            "mode": "live",
        },
    )
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    fallback = next(event for event in events if event["type"] == "tutorial_fallback")
    assert fallback["source"] == "showcase"
    assert "bundled LLM-authored" in fallback["message"]
    assert client.get("/api/tutorial/status").json()["source"] == "showcase"


def test_successful_live_enrichment_finishes_with_authoritative_session(monkeypatch):
    def complete_enrichment(session, message):
        repaired = repair_tutorial_copy(session)
        # A real successful stream_user_turn call always appends at least one
        # user/assistant pair to history; repair_tutorial_copy no longer does
        # this itself (it stopped seeding figures, which was the only thing
        # that used to inject synthetic history here), so this fake mimics
        # that real-turn effect directly instead of relying on it.
        session.history = repaired.history + [
            {"role": "user", "content": [{"type": "text", "text": message}]},
            {"role": "assistant", "content": [{"type": "text", "text": "Enriched."}]},
        ]
        session.doc = repaired.doc
        session.figures = repaired.figures
        session.suggested_prompts = repaired.suggested_prompts
        return iter(())

    monkeypatch.setattr("backend.app.stream_user_turn", complete_enrichment)
    client = TestClient(create_app())
    original = sessions.get_workspace()
    started = client.post(
        "/api/tutorial/start",
        json={
            "request_id": "live-session-hydration",
            "source": "generated",
            "workspace_id": original.workspace_id,
            "generation": original.generation,
        },
    ).json()
    response = client.post(
        "/api/tutorial/enrich",
        json={
            "tutorial_id": started["tutorial_id"],
            "workspace_id": started["workspace_id"],
            "generation": started["generation"],
            "mode": "live",
        },
    )
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    hydrated = next(event for event in events if event["type"] == "tutorial_session")
    assert hydrated["source"] == "generated"
    assert len(hydrated["session"]["chat"]) >= 2
    # repair_tutorial_copy no longer creates figures either — Chapter 6
    # generates them live when the tour reaches it.
    assert hydrated["session"]["figures"] == []


def test_bundled_repair_preserves_existing_content_and_creates_no_figures():
    source = SessionState()
    source.history.append(
        {"role": "user", "content": [{"type": "text", "text": "Keep this."}]}
    )
    before_history = copy.deepcopy(source.history)
    repaired = repair_tutorial_copy(source)

    # No synthetic history injection happens here any more — figures are no
    # longer created by repair_tutorial_copy at all.
    assert repaired.history == before_history
    assert analyze_tutorial_coverage(repaired).ready is True
    assert repaired.figures.snapshot() == []


def test_review_practice_has_truthful_template_starter_imported_item():
    source = repair_tutorial_copy(SessionState())
    source.template_origin = None
    scenario = review_practice_copy(source)
    imported = [
        paragraph
        for _part, _article, paragraph, _depth, _ref in iter_paragraphs(
            scenario.doc.doc
        )
        if paragraph.status == "imported"
    ]
    assert len(imported) == 1
    assert scenario.template_origin is not None
    assert imported[0].uid in scenario.template_origin["seed_block_ids"]
    assert scenario.source_docx_bytes is None


def test_reference_scenario_uses_all_real_extractors_without_touching_the_spec():
    tutorial = build_showcase_session()
    before = copy.deepcopy(tutorial.doc.to_dict())
    scenario = reference_practice_copy(tutorial)

    assert scenario.doc.to_dict() == before
    metadata = scenario.references.snapshot()
    assert {item["kind"] for item in metadata} == {"docx", "pdf", "txt", "xml", "csv"}
    assert all(item["block_count"] > 0 for item in metadata)
    pdf = next(item for item in metadata if item["kind"] == "pdf")
    assert "[page 1]" in pdf["excerpt"]
    assert tutorial.references.snapshot() == []


def test_media_practice_copy_live_success_backfills_missing_kinds_and_hides_directive(
    monkeypatch,
):
    tutorial = build_showcase_session()
    before_doc = copy.deepcopy(tutorial.doc.to_dict())
    live_figure = {
        "kind": "mermaid",
        "title": "Live Coordination Sequence",
        "source": "flowchart LR\n  A[Start] --> B[Verify]",
    }
    monkeypatch.setattr(
        "backend.llm.conversation.get_client",
        lambda: FakeClient(
            [
                tool_turn(
                    ["Here is a figure. "],
                    live_figure,
                    tool_id="toolu_live_fig",
                    name="create_figure",
                ),
                text_turn(["Done — tutorial-only examples above."]),
            ]
        ),
    )

    scenario = media_practice_copy(tutorial)

    assert scenario.doc.to_dict() == before_doc
    figures = scenario.figures.snapshot()
    assert {figure["kind"] for figure in figures} == {"mermaid", "svg", "table"}
    live = next(
        figure for figure in figures if figure["title"] == "Live Coordination Sequence"
    )
    assert live["kind"] == "mermaid"
    # The missing kinds (svg/table) were backfilled by the bundled fixtures
    # rather than the whole live attempt being discarded.
    assert {figure["title"] for figure in figures} == {
        "Live Coordination Sequence",
        "Tutorial Review Status Key",
        "Tutorial Review Checklist",
    }
    assert {item["kind"] for item in scenario.references.snapshot()} == {
        "docx",
        "pdf",
        "txt",
        "xml",
        "csv",
    }
    # The raw internal directive never leaks into the visible transcript.
    assert "TUTORIAL WORKSPACE FIGURES" not in json.dumps(scenario.history)


def test_media_practice_copy_falls_back_to_bundled_fixtures_without_api_key(
    monkeypatch,
):
    tutorial = build_showcase_session()
    before_doc = copy.deepcopy(tutorial.doc.to_dict())

    def _no_key():
        raise MissingApiKeyError("no key configured")

    monkeypatch.setattr("backend.llm.conversation.get_client", _no_key)

    scenario = media_practice_copy(tutorial)

    assert scenario.doc.to_dict() == before_doc
    assert {figure["kind"] for figure in scenario.figures.snapshot()} == {
        "mermaid",
        "svg",
        "table",
    }
    assert {figure["title"] for figure in scenario.figures.snapshot()} == {
        "Tutorial Coordination Flow",
        "Tutorial Review Status Key",
        "Tutorial Review Checklist",
    }
    assert {item["kind"] for item in scenario.references.snapshot()} == {
        "docx",
        "pdf",
        "txt",
        "xml",
        "csv",
    }


def test_media_practice_copy_falls_back_when_model_creates_no_figure(monkeypatch):
    tutorial = build_showcase_session()
    before_doc = copy.deepcopy(tutorial.doc.to_dict())
    monkeypatch.setattr(
        "backend.llm.conversation.get_client",
        lambda: FakeClient([text_turn(["Nothing to add here."])]),
    )

    scenario = media_practice_copy(tutorial)

    assert scenario.doc.to_dict() == before_doc
    assert {figure["kind"] for figure in scenario.figures.snapshot()} == {
        "mermaid",
        "svg",
        "table",
    }


def test_media_practice_copy_discards_attempt_that_touches_existing_content(
    monkeypatch,
):
    tutorial = build_showcase_session()
    before_doc = copy.deepcopy(tutorial.doc.to_dict())
    first = next(iter(iter_paragraphs(tutorial.doc.doc)))[2]
    monkeypatch.setattr(
        "backend.llm.conversation.get_client",
        lambda: FakeClient(
            [
                tool_turn(
                    ["Updating... "],
                    {
                        "edits": [
                            {
                                "action": "replace",
                                "target_id": first.uid,
                                "text": "HIJACKED existing content",
                                "status": first.status,
                            }
                        ]
                    },
                    tool_id="toolu_hijack",
                ),
                text_turn(["Done."]),
            ]
        ),
    )

    scenario = media_practice_copy(tutorial)

    # The hijacked edit does not survive — the whole attempt is discarded.
    assert scenario.doc.to_dict() == before_doc
    assert {figure["kind"] for figure in scenario.figures.snapshot()} == {
        "mermaid",
        "svg",
        "table",
    }


def test_push_scenario_rejects_a_second_request_before_the_first_pays_for_its_build():
    """A second, overlapping scenario/start must never start its own build.

    push_scenario's build= defers construction until after every guard
    passes, precisely so an in-flight (possibly billed, e.g.
    media_practice_copy) build reserves the slot BEFORE paying for
    anything. This proves the reservation — not just the eventual
    push_scenario() call — blocks a race, using a real background thread
    (the same blocking-fake-plus-release-event technique used elsewhere in
    this suite for start/restart races).
    """
    import threading

    manager = SessionManager()
    manager.begin_tutorial(request_id="scenario-race-contract")
    workspace_id = manager.current().workspace_id

    entered_build = threading.Event()
    release_build = threading.Event()

    def _slow_build(_tutorial):
        entered_build.set()
        release_build.wait(timeout=5)
        return SessionState()

    first_result = []
    thread = threading.Thread(
        target=lambda: first_result.append(
            manager.push_scenario(workspace_id, kind="references", build=_slow_build)
        )
    )
    thread.start()
    assert entered_build.wait(timeout=5)

    second_build_called = False

    def _second_build(_tutorial):
        nonlocal second_build_called
        second_build_called = True
        return SessionState()

    with pytest.raises(WorkspaceBusyError):
        manager.push_scenario(workspace_id, kind="references", build=_second_build)
    assert second_build_called is False

    release_build.set()
    thread.join(timeout=5)
    assert first_result[0].scope == "scenario"


def test_enrichment_validator_rejects_rewriting_existing_user_content():
    before = build_showcase_session().doc.doc
    after = SpecSection.from_dict(before.to_dict())
    first = next(iter(iter_paragraphs(after)))[2]
    first.text += " silently rewritten"
    valid, reasons = validate_tutorial_enrichment(before, after)
    assert valid is False
    assert any(first.uid in reason for reason in reasons)


def test_enrichment_validator_rejects_reordering_or_metadata_rewrites():
    before = build_showcase_session().doc.doc

    reordered_articles = SpecSection.from_dict(before.to_dict())
    reordered_articles.parts[0].articles[:2] = reversed(
        reordered_articles.parts[0].articles[:2]
    )
    valid, reasons = validate_tutorial_enrichment(before, reordered_articles)
    assert valid is False
    assert any("article" in reason for reason in reasons)

    reordered_paragraphs = SpecSection.from_dict(before.to_dict())
    article = next(
        article
        for part in reordered_paragraphs.parts
        for article in part.articles
        if len(article.paragraphs) >= 2
    )
    article.paragraphs[:2] = reversed(article.paragraphs[:2])
    valid, reasons = validate_tutorial_enrichment(before, reordered_paragraphs)
    assert valid is False
    assert any("provision" in reason for reason in reasons)

    changed_metadata = SpecSection.from_dict(before.to_dict())
    changed_metadata.project_profile = {
        "city": "Changed",
        "state_or_province": "State",
        "country": "Country",
        "client_name": "Client",
    }
    valid, reasons = validate_tutorial_enrichment(before, changed_metadata)
    assert valid is False
    assert any("project_profile" in reason for reason in reasons)


def test_structural_practice_uses_real_document_state_for_every_lint_rule():
    tutorial = build_showcase_session()
    scenario = structural_practice_copy(tutorial)
    rules = {item["rule"] for item in lint_document(scenario.doc.doc, scenario.module)}
    assert rules == {
        "stale_edition",
        "unrecorded_edition",
        "placeholder_marker",
        "template_marker",
        "empty_article",
        "duplicate_article_title",
        "missing_section_header",
    }
    assert tutorial.doc.doc.number
    assert not scenario.doc.doc.number


def test_chapter_scenarios_exercise_production_round_trips_and_restore_base(
    tmp_path, monkeypatch
):
    curated = (
        Path(__file__).resolve().parents[1]
        / "backend"
        / "templates"
        / "curated"
    )
    monkeypatch.setattr(
        "backend.templates._CATALOG",
        TemplateCatalog(personal_root=tmp_path / "templates", curated_root=curated),
    )
    # The "references" case now also generates Chapter 6's figures live
    # (media_practice_copy) — keep this hermetic, same as every other
    # tutorial test that exercises a model call.
    monkeypatch.setattr(
        "backend.tutorial.stream_user_turn", lambda _session, _message: iter(())
    )
    client = TestClient(create_app())
    original = sessions.get_workspace()
    started = client.post(
        "/api/tutorial/start",
        json={
            "request_id": "scenario-api-contract",
            "source": "showcase",
            "workspace_id": original.workspace_id,
            "generation": original.generation,
        },
    ).json()
    tutorial_id = started["tutorial_id"]
    current = started["session"]
    base_doc = current["doc"]

    cases = [
        ("blank", "blank"),
        ("review", "review"),
        ("references", "references"),
        ("import", "import"),
        ("template", "template"),
        ("project save", "project_roundtrip"),
    ]
    for chapter, expected_kind in cases:
        response = client.post(
            "/api/tutorial/scenario/start",
            json={
                "tutorial_id": tutorial_id,
                "workspace_id": current["workspace_id"],
                "generation": current["generation"],
                "chapter": chapter,
            },
        )
        assert response.status_code == 200, response.text
        scenario = response.json()["session"]
        assert scenario["workspace_scope"] == "scenario"
        assert scenario["scenario_kind"] == expected_kind
        if expected_kind == "blank":
            # The from-scratch on-ramp's own state: the panel's empty-state
            # controls only render when there is genuinely nothing here.
            assert scenario["doc"]["section"]["number"] == ""
            assert scenario["doc"]["section"]["title"] == ""
            assert all(
                part["articles"] == [] for part in scenario["doc"]["parts"]
            )
            assert scenario["doc"] != base_doc
            assert scenario["source_available"] is False
            assert scenario["baseline_index"] is None
        elif expected_kind == "references":
            assert {item["kind"] for item in scenario["reference_docs"]} == {
                "docx",
                "pdf",
                "txt",
                "xml",
                "csv",
            }
            # The stubbed model turn produces no figures, so the bundled
            # fallback backfills all three kinds; the document is untouched.
            assert scenario["doc"] == base_doc
            assert {f["kind"] for f in scenario["figures"]} == {
                "mermaid",
                "svg",
                "table",
            }
        elif expected_kind == "review":
            seed_ids = set(scenario["template_origin"]["seed_block_ids"])
            reviewable = {
                paragraph["id"]
                for part in scenario["doc"]["parts"]
                for article in part["articles"]
                for paragraph in article["paragraphs"]
                if paragraph["status"] == "imported"
            }
            assert seed_ids & reviewable
        elif expected_kind == "import":
            assert scenario["source_available"] is True
            assert scenario["baseline_index"] is not None
        elif expected_kind == "template":
            assert scenario["template_origin"]["seed_block_ids"]
            assert scenario["baseline_index"] is None
            assert scenario["doc"] != base_doc
            saved_base = client.get("/api/project/save?scope=tutorial")
            assert saved_base.status_code == 200
            saved_project = parse_project_package(saved_base.content).project
            saved_doc = saved_project["doc"]
            assert saved_doc["versions"][saved_doc["index"]] == {
                key: value for key, value in base_doc.items() if key != "version"
            }
            assert saved_project.get("template_origin") is None
        else:
            assert scenario["doc"] == base_doc

        response = client.post(
            "/api/tutorial/scenario/finish",
            json={
                "tutorial_id": tutorial_id,
                "workspace_id": scenario["workspace_id"],
                "generation": scenario["generation"],
            },
        )
        assert response.status_code == 200, response.text
        current = response.json()["session"]
        assert current["workspace_scope"] == "tutorial"
        assert current["doc"] == base_doc

    restored = client.post(
        "/api/tutorial/restore",
        json={
            "tutorial_id": tutorial_id,
            "workspace_id": current["workspace_id"],
            "generation": current["generation"],
        },
    )
    assert restored.status_code == 200, restored.text


def test_replace_completion_can_save_the_protected_original_first():
    original = sessions.get_session()
    original.history.append(
        {"role": "user", "content": [{"type": "text", "text": "retain me"}]}
    )
    client = TestClient(create_app())
    original_lease = sessions.get_workspace()
    started = client.post(
        "/api/tutorial/start",
        json={
            "request_id": "save-original-contract",
            "source": "showcase",
            "workspace_id": original_lease.workspace_id,
            "generation": original_lease.generation,
        },
    ).json()

    blocked = client.get("/api/project/save")
    assert blocked.status_code == 409
    saved = client.get("/api/project/save?scope=original")
    assert saved.status_code == 200
    parsed = parse_project_package(saved.content)
    assert parsed.project["history"][0]["content"][0]["text"] == "retain me"

    session = started["session"]
    kept = client.post(
        "/api/tutorial/keep",
        json={
            "tutorial_id": started["tutorial_id"],
            "workspace_id": session["workspace_id"],
            "generation": session["generation"],
        },
    )
    assert kept.status_code == 200
    assert "retain me" not in str(sessions.get_session().history)


def test_blank_practice_copy_is_empty_but_keeps_the_drafting_identity():
    source = build_showcase_session()
    source.discipline = "Plumbing"
    source.project_context = "tutorial primer"

    blank = blank_practice_copy(source)

    assert blank.doc.doc.is_empty()
    assert blank.doc.doc.number == ""
    assert blank.doc.doc.title == ""
    assert blank.history == []
    assert blank.figures.figures == []
    # Module, discipline and primer ride along so the heading and the drafting
    # context stay coherent on the empty page.
    assert blank.module is source.module
    assert blank.discipline == "Plumbing"
    assert blank.project_context == "tutorial primer"
    # The source it was built from is untouched.
    assert not source.doc.doc.is_empty()


def test_an_unmapped_chapter_name_does_not_silently_start_a_practice_fixture():
    """The chapter->kind chain is an ordered substring match with a catch-all.

    A new chunk whose scenario name has no branch falls through to
    ``structural``, which blanks the section header and seeds duplicate
    articles — in the wrong chapter, with no error. This pins the two names
    that must not collide with that fallback.
    """
    client = TestClient(create_app())
    original = sessions.get_workspace()
    started = client.post(
        "/api/tutorial/start",
        json={
            "request_id": "chapter-mapping-contract",
            "source": "showcase",
            "workspace_id": original.workspace_id,
            "generation": original.generation,
        },
    ).json()
    tutorial_id = started["tutorial_id"]
    current = started["session"]

    for chapter, expected_kind in (("blank", "blank"), ("structural", "structural")):
        scenario = client.post(
            "/api/tutorial/scenario/start",
            json={
                "tutorial_id": tutorial_id,
                "workspace_id": current["workspace_id"],
                "generation": current["generation"],
                "chapter": chapter,
            },
        ).json()["session"]
        assert scenario["scenario_kind"] == expected_kind
        current = client.post(
            "/api/tutorial/scenario/finish",
            json={
                "tutorial_id": tutorial_id,
                "workspace_id": scenario["workspace_id"],
                "generation": scenario["generation"],
            },
        ).json()["session"]
