"""Project briefs: build, serialize, parse, manifest, seed, and the routes.

The contract under test is the one the owner set: a brief carries the
project-level assets (profile, identity, edition overrides, the whole
research profile, attached references, established facts, the section
registry) and NEVER the conversation or the document. Seeding is one atomic
transaction that leaves a session at the maturity the previous section ended
at; the readiness checklist says research was carried rather than run here.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend import sessions
from backend.app import create_app
from backend.llm.conversation import SessionState
from backend.project_brief import (
    MAX_PROJECT_BRIEF_BYTES,
    PROJECT_BRIEF_KIND,
    ProjectBrief,
    ProjectBriefError,
    brief_bytes,
    brief_filename,
    brief_from_sibling_project,
    brief_manifest,
    build_project_brief,
    parse_project_brief,
    project_sections_block,
)
from backend.project_profile import ProjectProfile
from backend.reference_docs import MAX_REFERENCE_TOKENS
from backend.research.engine import (
    DimensionStatus,
    RequirementsProfile,
    ResearchItem,
    append_research_round,
)
from backend.spec_doc import SpecSection
from tests.fakes import FakeClient, request_context_text, text_turn

PROFILE = ProjectProfile("Ashburn", "VA", "US", "Client X")

SETUP_OPS = [
    {
        "action": "set_project_profile",
        "target_id": "sec",
        "city": "Ashburn",
        "state": "VA",
        "country": "USA",
        "client": "Client X",
    },
    {
        "action": "set_project_identity",
        "target_id": "sec",
        "project_type": "Data Center",
        "discipline": "Fire Suppression",
    },
    {
        "action": "replace",
        "target_id": "sec",
        "text": "Wet-Pipe Sprinkler Systems",
        "numbering": "21 13 13",
    },
    {
        "action": "set_standard_edition",
        "target_id": "sec",
        "standard": "NFPA 13",
        "edition": "2022",
        "basis": "Loudoun County adopted the 2021 VCC (research r-1)",
    },
    {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"},
    {"action": "add_article", "target_id": "pt2", "text": "PIPING"},
    {
        "action": "add_paragraph",
        "target_id": "pt2.a1",
        "text": "Provide Schedule 40 black steel pipe.",
        "status": "confirmed",
    },
]


def _client() -> TestClient:
    return TestClient(create_app())


def _item(item_id: str, requirement: str, **overrides) -> ResearchItem:
    fields = {
        "topic": "topic",
        "authority": "Loudoun County",
        "code_reference": "VCC 2021",
        "source_urls": ["https://example.test/vcc"],
        "accepted_sources": ["https://example.test/vcc"],
        "grounded": True,
        "confidence": 0.9,
        "actionability": "spec_requirement",
        "notes": "",
    }
    fields.update(overrides)
    return ResearchItem(
        item_id=item_id,
        dimension_id="governing_codes",
        category="governing_code",
        requirement=requirement,
        **fields,  # type: ignore[arg-type]
    )


def _research(project: ProjectProfile = PROFILE) -> RequirementsProfile:
    fresh = RequirementsProfile(
        items=[_item("r-1", "Loudoun County adopted the 2021 VCC.")],
        dimension_statuses=[
            DimensionStatus(
                dimension_id="governing_codes",
                status="completed",
                title="Governing building and fire codes",
                item_count=1,
                grounded_count=1,
            )
        ],
        research_date="2026-08-02",
        project=project.to_dict(),
    )
    return append_research_round(None, fresh)


def _rich_session(client: TestClient) -> SessionState:
    """A section-1 session holding one of everything a brief carries."""
    resp = client.post("/api/doc/edit", json={"ops": SETUP_OPS})
    assert resp.status_code == 200, resp.text
    session = sessions.get_session()
    session.research.restore(_research())
    session.references.add(
        filename="owner-standard.pdf",
        text="[page 1] The owner requires 30 minutes of water supply.",
        block_count=1,
        title="Owner fire protection standard",
        kind="pdf",
        token_count=500,
    )
    session.facts.apply(
        {
            "record": [
                {
                    "statement": "Data halls are Ordinary Hazard Group 2.",
                    "status": "confirmed",
                    "source_kind": "user",
                },
                {
                    "statement": "Water supply duration is 30 minutes.",
                    "status": "confirmed",
                    "source_kind": "reference",
                    "source_ref": "ref-1",
                },
                {"statement": "Retired fact."},
            ],
            "supersede": [],
        },
        recorded_in="21 13 13",
        recorded_at="2026-09-04",
    )
    session.facts.supersede("pf-3", "moot")
    session.history.append({"role": "user", "content": [{"type": "text", "text": "hi"}]})
    session.history.append(
        {"role": "assistant", "content": [{"type": "text", "text": "hello"}]}
    )
    session.doc.doc.suppressed_standards = {"NFPA 20": "pumps are in 21 30 00"}
    return session


# ---------------------------------------------------------------------------
# Build / serialize / parse
# ---------------------------------------------------------------------------


def test_build_serialize_parse_round_trip_is_lossless():
    client = _client()
    session = _rich_session(client)
    brief = build_project_brief(session, ready=False)
    parsed = parse_project_brief(brief_bytes(brief))
    assert parsed.to_dict() == brief.to_dict()
    assert parsed.warnings == []
    assert brief.profile == PROFILE.to_dict()
    assert brief.project_type == "Data Center"
    assert brief.edition_overrides["NFPA 13"]["edition"] == "2022"
    assert brief.edition_overrides["NFPA 13"]["basis"].startswith("Loudoun County")
    assert brief.research_profile["rounds"][0]["round_index"] == 1
    assert [d["title"] for d in brief.reference_docs] == ["Owner fire protection standard"]
    assert len(brief.reference_docs[0]["content_fingerprint"]) == 64
    assert [f["pid"] for f in brief.facts] == ["pf-1", "pf-2", "pf-3"]
    assert brief.facts[2]["status"] == "superseded"  # the trail travels
    assert brief.name == "Client X · Data Center · Ashburn, Virginia"
    assert brief_filename(brief) == "buildaspec-project-client-x-data-center-ashburn-virginia.basproject"
    assert len(brief.project_id) == 32


def test_the_brief_never_carries_the_conversation_the_document_or_exclusions():
    client = _client()
    session = _rich_session(client)
    payload = build_project_brief(session, ready=True).to_dict()
    assert set(payload) == {
        "kind", "format", "project_id", "name", "created_at", "updated_at",
        "app_version", "profile", "project_type", "edition_overrides",
        "research_profile", "reference_docs", "facts", "sections",
    }
    serialized = json.dumps(payload)
    assert "Provide Schedule 40" not in serialized  # no provision text
    assert "hello" not in serialized  # no transcript
    assert "suppressed" not in serialized and "NFPA 20" not in serialized
    [record] = payload["sections"]
    assert record["number"] == "21 13 13"
    assert record["title"] == "Wet-Pipe Sprinkler Systems"
    assert record["article_titles"] == ["SUMMARY", "PIPING"]
    assert record["ready"] is True
    assert record["module_id"] == session.module.module_id
    assert record["discipline"] == "Fire Suppression"
    assert record["fact_count"] == 2
    assert record["research_rounds"] == 1


def test_a_linked_session_keeps_its_project_id_and_upserts_its_section():
    client = _client()
    session = _rich_session(client)
    session.project_link = {
        "project_id": "c" * 32,
        "name": "Campus X",
        "brief_updated_at": "2026-09-01T00:00:00+00:00",
        "seeded_from": ["21 05 00"],
        "research_rounds_at_seed": 1,
        "sections": [
            {"number": "21 05 00", "title": "Common Work Results", "article_titles": ["SUMMARY"]},
            {"number": "21 13 13", "title": "old title", "article_titles": []},
        ],
    }
    brief = build_project_brief(session, ready=False)
    assert brief.project_id == "c" * 32
    assert brief.name == "Campus X"
    assert [s["number"] for s in brief.sections] == ["21 05 00", "21 13 13"]
    assert brief.sections[1]["title"] == "Wet-Pipe Sprinkler Systems"
    assert brief.sections[1]["article_titles"] == ["SUMMARY", "PIPING"]


def test_the_manifest_counts_and_warns():
    client = _client()
    session = _rich_session(client)
    manifest = brief_manifest(build_project_brief(session, ready=False))
    assert manifest["profile"]["line"] == "Ashburn, Virginia, USA — Client: Client X"
    assert manifest["profile"]["complete"] is True
    assert manifest["project_type"] == "Data Center"
    assert manifest["module_available"] is True
    assert manifest["discipline"] == "Fire Suppression"
    assert manifest["edition_overrides"]["count"] == 1
    assert manifest["edition_overrides"]["standards"][0].startswith("NFPA 13 — 2022")
    assert manifest["research"]["items"] == 1
    assert manifest["research"]["grounded"] == 1
    assert manifest["research"]["rounds"] == 1
    assert manifest["research"]["last_research_date"] == "2026-08-02"
    assert manifest["references"][0]["token_count"] == 500
    assert manifest["references"][0]["carried"] is True
    assert manifest["reference_tokens"] == 500
    assert manifest["facts"] == {"active": 2, "confirmed": 2, "assumed": 0, "superseded": 1}
    assert manifest["sections"][0]["number"] == "21 13 13"
    assert manifest["warnings"] == []


def test_the_manifest_names_what_a_seed_would_have_to_do():
    client = _client()
    session = _rich_session(client)
    brief = build_project_brief(session, ready=False)
    brief.profile["city"] = "Leesburg"  # edited after research ran
    brief.profile["client_name"] = ""  # and now incomplete
    brief.reference_docs.append(
        {**brief.reference_docs[0], "rid": "ref-2", "title": "Huge", "token_count": MAX_REFERENCE_TOKENS}
    )
    brief.sections[0]["module_id"] = "not_installed"
    warnings = brief_manifest(brief)["warnings"]
    assert any("incomplete" in w for w in warnings)
    assert any("edited after the research" in w for w in warnings)
    assert any("beyond the session cap" in w and "Huge" in w for w in warnings)
    assert any("not installed" in w for w in warnings)


def _minimal_brief() -> dict:
    return {
        "kind": PROJECT_BRIEF_KIND,
        "format": 1,
        "project_id": "a" * 32,
        "name": "Campus X",
        "created_at": "2026-09-04T00:00:00+00:00",
        "updated_at": "2026-09-04T00:00:00+00:00",
        "app_version": "1.17.0",
        "profile": PROFILE.to_dict(),
        "project_type": "Data Center",
        "edition_overrides": {},
        "research_profile": None,
        "reference_docs": [],
        "facts": [],
        "sections": [],
    }


def test_parse_refuses_what_is_not_a_brief():
    with pytest.raises(ProjectBriefError, match="16 MiB"):
        parse_project_brief(b"{" + b" " * MAX_PROJECT_BRIEF_BYTES + b"}")
    with pytest.raises(ProjectBriefError, match="not valid JSON"):
        parse_project_brief(b"{nope")
    with pytest.raises(ProjectBriefError, match="Not a Build-a-Spec project brief"):
        parse_project_brief(json.dumps({**_minimal_brief(), "kind": "buildaspec-project"}).encode())
    with pytest.raises(ProjectBriefError, match="format"):
        parse_project_brief(json.dumps({**_minimal_brief(), "format": 2}).encode())
    with pytest.raises(ProjectBriefError, match="unknown field"):
        parse_project_brief(json.dumps({**_minimal_brief(), "history": []}).encode())
    with pytest.raises(ProjectBriefError, match="Duplicate JSON field"):
        parse_project_brief(b'{"kind": "x", "kind": "y"}')
    with pytest.raises(ProjectBriefError, match="Non-finite"):
        parse_project_brief(b'{"kind": NaN}')
    with pytest.raises(ProjectBriefError, match="project id"):
        parse_project_brief(json.dumps({**_minimal_brief(), "project_id": "short"}).encode())
    with pytest.raises(ProjectBriefError, match="Malformed standards"):
        parse_project_brief(
            json.dumps({**_minimal_brief(), "edition_overrides": {"NFPA 13": {"edition": "2022"}}}).encode()
        )
    with pytest.raises(ProjectBriefError, match="root must be"):
        parse_project_brief(b"[]")


def test_parse_degrades_what_it_can_and_says_so():
    data = _minimal_brief()
    data["research_profile"] = {"garbage": True}
    data["reference_docs"] = [
        "not a dict",
        {"title": "no rid"},
        {
            "rid": "ref-1",
            "filename": "a.pdf",
            "title": "Owner standard",
            "text": "edited text",
            "char_count": 11,
            "block_count": 1,
            "kind": "pdf",
            "token_count": 3,
            "content_fingerprint": "0" * 64,
        },
    ]
    data["facts"] = [
        {"pid": "pf-1", "statement": "Kept."},
        {"pid": "pf-1", "statement": "Duplicate id."},
        {"pid": "nope", "statement": "Bad id."},
        7,
    ]
    data["sections"] = [{"number": "21 05 00"}, {"bogus": 1}, "x"]
    brief = parse_project_brief(json.dumps(data).encode())
    assert brief.research_profile is None
    assert [d["rid"] for d in brief.reference_docs] == ["ref-1"]
    assert [f["pid"] for f in brief.facts] == ["pf-1"]
    assert [s["number"] for s in brief.sections] == ["21 05 00"]
    joined = " ".join(brief.warnings)
    assert "research profile could not be read" in joined
    assert "malformed reference document" in joined
    assert "does not match its recorded fingerprint" in joined
    assert "malformed project fact" in joined
    assert "malformed section record" in joined


def test_a_brief_can_be_built_straight_from_a_sibling_section_file():
    client = _client()
    session = _rich_session(client)
    package = sessions.project_package_bytes(session)
    brief = brief_from_sibling_project(package)
    assert brief.profile == PROFILE.to_dict()
    assert brief.research_profile["rounds"][0]["round_index"] == 1
    assert [f["pid"] for f in brief.facts] == ["pf-1", "pf-2", "pf-3"]
    assert brief.sections[0]["number"] == "21 13 13"
    assert brief.sections[0]["ready"] is False
    assert any("Built from section 21 13 13" in w for w in brief.warnings)
    # The live session was never touched.
    assert sessions.get_session() is session
    assert session.history[-1]["content"][0]["text"] == "hello"
    with pytest.raises(ValueError):
        brief_from_sibling_project(b"not a project")


# ---------------------------------------------------------------------------
# The seed transaction
# ---------------------------------------------------------------------------


def test_start_from_brief_seeds_everything_atomically():
    client = _client()
    brief = build_project_brief(_rich_session(client), ready=True)
    sessions.reset_session()
    session = sessions.get_session()
    generation = session.generation

    report = session.start_from_brief(
        brief, module_id=brief.newest_section["module_id"], discipline="Fire Suppression"
    )

    assert session.generation == generation + 1
    doc = session.doc.doc
    # Version 0 IS the project setup, on an empty body.
    assert session.doc.index == 0 and len(session.doc.versions) == 1
    assert not doc.has_body_content()
    assert not doc.is_empty()
    assert doc.project_profile == PROFILE.to_dict()
    assert doc.project_identity == {"project_type": "Data Center", "discipline": "Fire Suppression"}
    assert doc.edition_overrides["NFPA 13"]["basis"].startswith("Loudoun County")
    assert doc.suppressed_standards == {}
    assert doc.number == "" and doc.title == ""
    # Research is the rounds already run; the next press is round 2.
    assert session.research.status == "complete"
    assert session.research.profile_result.round_count == 1
    assert session.research.profile_result.item("r-1") is not None
    # References keep their ids; facts keep theirs and their statuses.
    assert [d.rid for d in session.references.docs] == ["ref-1"]
    assert "30 minutes of water supply" in session.references.docs[0].text
    assert [(f.pid, f.status) for f in session.facts.items] == [
        ("pf-1", "confirmed"), ("pf-2", "confirmed"), ("pf-3", "superseded"),
    ]
    assert session.facts.get("pf-1").recorded_in == "21 13 13"
    assert session.history == []
    link = session.project_link
    assert link["project_id"] == brief.project_id
    assert link["seeded_from"] == ["21 13 13"]
    assert link["research_rounds_at_seed"] == 1
    assert link["sections"][0]["number"] == "21 13 13"
    assert report["research_rounds"] == 1 and report["research_items"] == 1
    assert report["references_restored"] == 1 and report["references_dropped"] == []
    assert report["facts_restored"] == 3
    assert report["edition_overrides"] == 1
    assert report["warnings"] == []
    assert sessions.has_unsaved_progress(session)
    # A new fact keeps counting from where the carried ledger left off.
    fact, _ = session.facts.record({"statement": "New here."}, recorded_in="", recorded_at="")
    assert fact.pid == "pf-4"
    # The reference store keeps counting too.
    added = session.references.add(filename="x.txt", text="x", block_count=1, kind="txt")
    assert added.rid == "ref-2"


def test_the_seed_drops_references_past_the_cap_and_says_which():
    client = _client()
    brief = build_project_brief(_rich_session(client), ready=False)
    brief.reference_docs.append(
        {**brief.reference_docs[0], "rid": "ref-2", "title": "Huge standard", "token_count": MAX_REFERENCE_TOKENS}
    )
    sessions.reset_session()
    session = sessions.get_session()
    report = session.start_from_brief(brief, module_id="", discipline="")
    assert [d.rid for d in session.references.docs] == ["ref-1"]
    assert report["references_dropped"] == ["Huge standard"]
    assert any("Huge standard" in w for w in report["warnings"])
    assert any("not installed" not in w for w in report["warnings"])


def test_a_template_pairs_with_the_brief_and_the_briefs_setup_wins():
    client = _client()
    brief = build_project_brief(_rich_session(client), ready=False)
    template = SpecSection.empty()
    template.number = "21 13 13"
    template.title = "Starter"
    template.project_profile = ProjectProfile("Elsewhere", "TX", "US", "Other Co").to_dict()
    template.edition_overrides = {"NFPA 13": {"edition": "2019", "basis": "the template's"}}
    sessions.reset_session()
    session = sessions.get_session()
    report = session.start_from_brief(
        brief,
        module_id="generic",
        discipline="Fire Suppression",
        template_section=template,
        template_origin={"template_id": "curated:x", "name": "Starter", "seed_block_ids": []},
    )
    doc = session.doc.doc
    assert doc.title == "Starter"
    assert doc.project_profile == PROFILE.to_dict()
    assert doc.edition_overrides["NFPA 13"]["edition"] == "2022"
    assert session.template_origin["name"] == "Starter"
    assert report["template"]["name"] == "Starter"
    assert session.module.module_id == "generic"


def test_an_unknown_module_degrades_to_the_default_with_a_warning():
    client = _client()
    brief = build_project_brief(_rich_session(client), ready=False)
    sessions.reset_session()
    session = sessions.get_session()
    report = session.start_from_brief(brief, module_id="not_installed", discipline="")
    assert session.module.module_id == report["module_id"]
    assert any("not installed" in w for w in report["warnings"])


def test_a_master_import_after_a_seed_keeps_the_project_setup():
    client = _client()
    brief = build_project_brief(_rich_session(client), ready=False)
    sessions.reset_session()
    session = sessions.get_session()
    session.start_from_brief(brief, module_id="", discipline="")
    imported = SpecSection.empty()
    imported.number = "21 30 00"
    imported.title = "Fire Pumps"
    session.doc.adopt_imported(imported)
    doc = session.doc.doc
    assert doc.number == "21 30 00"
    assert doc.project_profile == PROFILE.to_dict()
    assert doc.edition_overrides["NFPA 13"]["edition"] == "2022"


def test_reset_clears_the_link_and_the_ledger():
    client = _client()
    brief = build_project_brief(_rich_session(client), ready=False)
    sessions.reset_session()
    session = sessions.get_session()
    session.start_from_brief(brief, module_id="", discipline="")
    session.reset()
    assert session.project_link is None
    assert session.facts.items == []
    assert session.references.docs == []
    assert session.research.profile_result is None


# ---------------------------------------------------------------------------
# The PROJECT SECTIONS block
# ---------------------------------------------------------------------------


def _link(*records: dict) -> dict:
    return {
        "project_id": "d" * 32,
        "name": "Campus X",
        "brief_updated_at": "2026-09-04T00:00:00+00:00",
        "seeded_from": [r["number"] for r in records],
        "research_rounds_at_seed": 0,
        "sections": list(records),
    }


def test_the_sections_block_lists_only_other_sections():
    assert project_sections_block(None, "21 13 19") == ""
    only_self = _link({"number": "21 13 19", "title": "Preaction", "article_titles": ["A"]})
    assert project_sections_block(only_self, "21 13 19") == ""
    link = _link(
        {
            "number": "21 13 13",
            "title": "Wet-Pipe Sprinkler Systems",
            "article_titles": ["SUMMARY", "REFERENCES"],
            "ready": True,
            "exported_at": "2026-09-04T10:00:00+00:00",
        },
        {"number": "21 13 19", "title": "Preaction", "article_titles": ["A"]},
        {"number": "21 13 16", "title": "Dry-Pipe Sprinkler Systems", "article_titles": []},
    )
    block = project_sections_block(link, "21 13 19")
    lines = block.splitlines()
    assert lines[0].startswith('PROJECT SECTIONS (sections of this project drafted so far, from its project brief "Campus X"')
    assert "this session is section 21 13 19" in lines[0]
    assert lines[1] == "- 21 13 13 Wet-Pipe Sprinkler Systems — articles: SUMMARY; REFERENCES (issue-ready; exported 2026-09-04)"
    assert lines[2] == "- 21 13 16 Dry-Pipe Sprinkler Systems (in progress)"
    assert "21 13 19" not in "\n".join(lines[1:-1])
    assert lines[-1].startswith("A provision that belongs to a listed section is cross-referenced")
    unnumbered = project_sections_block(link, "")
    assert "this session's section is not yet numbered" in unnumbered
    assert "Preaction" in unnumbered  # every section is "other" when unnumbered


def test_the_sections_block_trims_articles_first_then_sections():
    records = [
        {"number": f"21 {i:02d} 00", "title": f"Section {i}", "article_titles": [f"ARTICLE {j}" for j in range(20)]}
        for i in range(1, 15)
    ]
    link = _link(*records)
    block = project_sections_block(link, "21 99 00", max_tokens=400)
    assert "ARTICLE" not in block
    assert "(Article lists omitted for length.)" in block or "omitted here for length" in block
    tight = project_sections_block(link, "21 99 00", max_tokens=120)
    assert "further section(s) omitted here for length" in tight
    assert "- 21 01 00 Section 1" in tight


def test_the_sections_block_reaches_the_turn_but_never_the_cached_prompt(monkeypatch):
    client = _client()
    session = sessions.get_session()
    session.project_link = _link(
        {"number": "21 13 13", "title": "Wet-Pipe Sprinkler Systems", "article_titles": ["SUMMARY"]}
    )
    fake = FakeClient([text_turn(["ok"])])
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)
    client.post("/api/chat", json={"message": "hi"})
    request = fake.messages.last_request
    context = request_context_text(request)
    assert "PROJECT SECTIONS" in context
    assert "21 13 13 Wet-Pipe Sprinkler Systems" in context
    assert context.index("PROJECT SECTIONS") < context.index("Current specification document")
    assert "Wet-Pipe Sprinkler Systems" not in json.dumps(request["system"])
    assert "PROJECT SECTIONS" not in json.dumps(sessions.get_session().history)
