"""Specification voice: the model never writes placeholders or notes.

Owner rule (2026-10-06): nothing written for the user goes into the
specification — no ``[TBD: ...]``, no bracketed options, no reminders, no
explanation. The prompt states the rule; ``spec_doc.spec_voice`` enforces the
deterministic half on every batch the model drafts, and these tests pin both.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import sessions
from backend.app import _template_structure_contract, create_app
from backend.followups import TRACK_FOLLOWUPS_TOOL
from backend.llm.prompts import (
    ADAPT_IMPORTED_DIRECTIVE,
    FULL_DRAFT_DIRECTIVE,
    render_system_prompt,
)
from backend.qc.engine import QCFinding, _validate_ops
from backend.spec_doc.model import (
    APPLY_SPEC_EDITS_TOOL,
    RETIRED_STATUSES,
    STATUSES,
    SpecEditError,
    SpecSection,
    apply_edits,
)
from backend.spec_doc.spec_voice import (
    MODEL_STATUSES,
    check_drafted_edits,
    check_user_edits,
    drafted_edit_problems,
    drafted_text_hits,
    has_placeholder,
    retired_status_problems,
)
from backend.spec_modules.generic import GENERIC
from backend.spec_modules.hyperscale_fire import HYPERSCALE_FIRE
from backend.standards import standards_context_block
from tests.fakes import FakeClient, text_turn, tool_turn

CURATED_ROOT = Path(__file__).resolve().parents[1] / "backend" / "templates" / "curated"

# The owner's two examples of drafting that explained instead of directing,
# and the specification voice they should have been written in.
_WATERFLOW_SPEC_VOICE = (
    "Provide an approved audible device connected to each automatic "
    "sprinkler system, actuated by water flow equivalent to a single "
    "sprinkler of the smallest orifice size installed in the system, located "
    "on the exterior of the building in an approved location. Actuation of "
    "the automatic sprinkler system shall also actuate the building fire "
    "alarm system; refer to Section 21 10 00 for the waterflow alarm device "
    "and Section 28 31 00 for fire alarm system actuation."
)
_NFPA25_REFERENCE = (
    "NFPA 25, Standard for the Inspection, Testing, and Maintenance of "
    "Water-Based Fire Protection Systems, 2020 edition."
)


def _client() -> TestClient:
    return TestClient(create_app())


def _parse_sse(body: str) -> list[dict]:
    return [
        json.loads(line[len("data: "):])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


def _patch_client(monkeypatch, fake: FakeClient) -> None:
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)


# ---------------------------------------------------------------------------
# The vocabulary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Design density: [TBD: density] over the remote area.",
        "Design density: [TBD] over the remote area.",
        "Pipe schedule: TBD.",
        "Provide [Schedule 10] [Schedule 40] steel pipe.",
        "Manufacturer: [INSERT MANUFACTURER].",
        "Signage text: ________.",
        "TODO: confirm the riser count.",
        "Hazard classification to be determined by the Owner.",
        "Riser location to be confirmed.",
        "Specifier Note: retain for FM-insured projects.",
        "Note to the designer: verify the flow test date.",
        "Provide valves rated 175 psi [1200 kPa].",
    ],
)
def test_the_guard_finds_placeholders_options_markers_and_notes(text):
    assert drafted_text_hits(text), text
    assert has_placeholder(text)


@pytest.mark.parametrize(
    "text",
    [
        _WATERFLOW_SPEC_VOICE,
        _NFPA25_REFERENCE,
        "Comply with IBC §903.4.2 (Alarms).",
        "Install sprinkler systems in accordance with NFPA 13.",
        "Provide Schedule 10 pipe for 2-1/2 in. (DN 65) and larger.",
        "Base hydraulic calculations on a water flow test conducted not more "
        "than 12 months before submittal of working plans.",
        "Submit working plans and hydraulic calculations to the authority "
        "having jurisdiction before fabrication.",
        "Verify field dimensions before fabrication.",
    ],
)
def test_specification_language_passes_the_guard(text):
    assert drafted_text_hits(text) == []
    assert not has_placeholder(text)


def test_a_tbd_bracket_is_reported_once_under_its_own_label():
    hits = drafted_text_hits("Density [TBD: value] applies.")
    assert hits == [{"match": "[TBD: value]", "label": "TBD placeholder"}]


# ---------------------------------------------------------------------------
# The batch check
# ---------------------------------------------------------------------------


def test_model_batches_with_placeholder_text_are_refused_whole():
    edits = [
        {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"},
        {
            "action": "add_paragraph",
            "target_id": "pt1.a1",
            "text": "Design density: [TBD: density].",
            "status": "assumed",
        },
        {"action": "replace", "target_id": "sec", "text": "[TBD: SECTION TITLE]"},
        {
            "action": "set_standard_edition",
            "target_id": "sec",
            "standard": "FM DS 2-0",
            "edition": "2024",
            "basis": "user",
            "title": "[INSERT TITLE]",
        },
    ]
    with pytest.raises(SpecEditError) as caught:
        check_drafted_edits(edits)
    message = str(caught.value)
    assert "edit 2 (add_paragraph on pt1.a1)" in message
    assert "'[TBD: density]'" in message
    assert "edit 3 (replace on sec)" in message
    assert "edit 4 (set_standard_edition on sec)" in message
    assert "edit 1" not in message
    # The refusal says how to write it instead.
    assert "track_followups" in message
    assert "assumed" in message


@pytest.mark.parametrize("status", ["needs_input", "imported"])
def test_the_model_may_not_stamp_placeholder_or_starter_statuses(status):
    for op in (
        {"action": "add_paragraph", "target_id": "pt1.a1", "text": "Provide.", "status": status},
        {"action": "replace", "target_id": "pt1.a1.p1", "status": status},
        {"action": "set_status", "target_id": "pt1.a1.p1", "status": status},
    ):
        problems = drafted_edit_problems([op])
        assert len(problems) == 1 and f"'{status}'" in problems[0]


def test_ordinary_batches_pass_and_bad_shapes_are_left_to_apply_edits():
    check_drafted_edits(
        [
            {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"},
            {
                "action": "add_paragraph",
                "target_id": "pt1.a1",
                "text": _WATERFLOW_SPEC_VOICE,
                "status": "assumed",
            },
            {"action": "set_status", "target_id": "pt1.a1.p1", "status": "confirmed"},
            {"action": "move", "target_id": "pt1.a1", "position": 0},
            {"action": "delete", "target_id": "pt1.a1.p1"},
        ]
    )
    # Malformed shapes are apply_edits' to reject, in its own words.
    for garbage in (None, "edits", [], [None, 3, {"text": 5}]):
        assert drafted_edit_problems(garbage) == []


def test_every_status_the_model_may_not_stamp_has_a_refusal():
    from backend.spec_doc import spec_voice

    assert MODEL_STATUSES == ("confirmed", "assumed")
    assert set(spec_voice._STATUS_REFUSALS) == set(STATUSES) - set(MODEL_STATUSES)


# ---------------------------------------------------------------------------
# Where the guard runs — and where it does not
# ---------------------------------------------------------------------------


def test_a_chat_batch_carrying_a_tbd_is_rejected_and_the_model_self_corrects(monkeypatch):
    bad = {
        "edits": [
            {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"},
            {
                "action": "add_paragraph",
                "target_id": "pt1.a1",
                "text": "Design density: [TBD: density] over the remote area.",
                "status": "needs_input",
            },
        ]
    }
    good = {
        "edits": [
            {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"},
            {
                "action": "add_paragraph",
                "target_id": "pt1.a1",
                "text": "Design systems hydraulically for the occupancy hazard "
                "classifications indicated on the Drawings.",
                "status": "assumed",
            },
        ]
    }
    fake = FakeClient(
        [
            tool_turn([], bad, tool_id="toolu_bad"),
            tool_turn([], good, tool_id="toolu_good"),
            text_turn(["Drafted. What design density should govern?"]),
        ]
    )
    _patch_client(monkeypatch, fake)

    events = _parse_sse(_client().post("/api/chat", json={"message": "go"}).text)
    assert events[-1]["type"] == "turn_complete"
    # Only the corrected batch reached the document.
    assert len([e for e in events if e["type"] == "doc_patch"]) == 1

    history = sessions.get_session().history
    refusal = history[2]["content"][0]
    assert refusal["is_error"] is True
    assert "rejected (nothing was applied)" in refusal["content"]
    assert "[TBD: density]" in refusal["content"]
    assert "needs_input" in refusal["content"]

    doc = sessions.get_session().doc.doc
    paragraphs = doc.parts[0].articles[0].paragraphs
    assert len(doc.parts[0].articles) == 1 and len(paragraphs) == 1
    assert not has_placeholder(paragraphs[0].text)
    assert paragraphs[0].status == "assumed"


def test_the_users_own_typed_text_is_never_policed(monkeypatch):
    # What the user types is theirs: a hand-typed [TBD] lands (and is still
    # counted as a leftover). Only the retired status is refused.
    client = _client()
    resp = client.post(
        "/api/doc/edit",
        json={
            "ops": [
                {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"},
                {
                    "action": "add_paragraph",
                    "target_id": "pt1.a1",
                    "text": "Riser count: [TBD: confirm with owner].",
                    "status": "assumed",
                },
            ]
        },
    )
    assert resp.status_code == 200
    para = resp.json()["doc"]["parts"][0]["articles"][0]["paragraphs"][0]
    assert para["text"] == "Riser count: [TBD: confirm with owner]."
    assert para["status"] == "assumed"
    assert [item["kind"] for item in resp.json()["open_questions"]] == ["tbd"]


_SEED_ARTICLE = [
    {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"},
    {
        "action": "add_paragraph",
        "target_id": "pt1.a1",
        "text": "Provide sprinkler systems.",
        "status": "assumed",
    },
]


@pytest.mark.parametrize(
    "op",
    [
        {"action": "set_status", "target_id": "pt1.a1.p1", "status": "needs_input"},
        {
            "action": "replace",
            "target_id": "pt1.a1.p1",
            "text": "Provide sprinkler systems throughout.",
            "status": "needs_input",
        },
        {
            "action": "add_paragraph",
            "target_id": "pt1.a1",
            "text": "Coordinate risers.",
            "status": "needs_input",
        },
    ],
    ids=["set_status", "replace", "add_paragraph"],
)
def test_no_edit_may_stamp_needs_input_any_more(op):
    """PR 4: needs_input is retired for the user's own edits too.

    The panel never offered it; a hand-built request is refused whole, with
    the batch's other ops, and the document is left exactly as it was.
    """
    client = _client()
    assert client.post("/api/doc/edit", json={"ops": _SEED_ARTICLE}).status_code == 200
    before = client.get("/api/doc").json()["doc"]

    resp = client.post(
        "/api/doc/edit",
        json={
            "ops": [
                {"action": "add_article", "target_id": "pt2", "text": "PRODUCTS"},
                op,
            ]
        },
    )
    assert resp.status_code == 400
    error = resp.json()["error"]
    assert "needs_input" in error and "retired" in error
    assert "edit 2 (" in error
    assert "Waiting on you" in error
    assert client.get("/api/doc").json()["doc"] == before


def test_retired_status_problems_reads_only_what_would_stamp_a_status():
    assert retired_status_problems("not a list") == []
    assert retired_status_problems(
        [
            "not an op",
            {"action": "replace", "target_id": "pt1.a1.p1", "text": "Retyped."},
            {"action": "set_status", "target_id": "pt1.a1.p1", "status": "assumed"},
            {"action": "set_status", "target_id": "pt1.a1.p1", "status": "imported"},
        ]
    ) == []
    assert RETIRED_STATUSES == ("needs_input",)
    assert set(RETIRED_STATUSES) <= set(STATUSES)
    assert not set(RETIRED_STATUSES) & set(MODEL_STATUSES)
    with pytest.raises(SpecEditError, match="retired"):
        check_user_edits(
            [{"action": "set_status", "target_id": "x", "status": "needs_input"}]
        )


def _load_legacy_needs_input_project(client: TestClient) -> dict:
    """A project saved before 2026-10-06, still carrying a needs_input block."""
    assert client.post("/api/doc/edit", json={"ops": _SEED_ARTICLE}).status_code == 200
    project = json.loads(json.dumps(sessions.project_payload(sessions.get_session())))
    current = project["doc"]["versions"][project["doc"]["index"]]
    paragraph = current["parts"][0]["articles"][0]["paragraphs"][0]
    paragraph["text"] = "Riser count: [TBD: confirm with owner]."
    paragraph["status"] = "needs_input"
    client.post("/api/session/reset")
    resp = client.post("/api/project/load", json=project)
    assert resp.status_code == 200
    return resp.json()


@pytest.mark.parametrize("status", ["confirmed", "assumed"])
def test_a_legacy_needs_input_block_loads_counts_and_switches(status):
    client = _client()
    loaded = _load_legacy_needs_input_project(client)
    para = loaded["doc"]["parts"][0]["articles"][0]["paragraphs"][0]
    assert para["status"] == "needs_input"
    assert [item["kind"] for item in loaded["open_questions"]] == ["needs_input", "tbd"]
    checks = {c["id"]: c for c in client.get("/api/readiness").json()["checks"]}
    assert checks["no_open_items"]["ok"] is False
    assert "leftover placeholder" in checks["no_open_items"]["detail"]

    # Retyping the text without a status is not a new stamp: it keeps the
    # legacy status, and the edit lands.
    retyped = client.post(
        "/api/doc/edit",
        json={
            "ops": [
                {
                    "action": "replace",
                    "target_id": "pt1.a1.p1",
                    "text": "Riser count: as indicated on the Drawings.",
                }
            ]
        },
    )
    assert retyped.status_code == 200
    para = retyped.json()["doc"]["parts"][0]["articles"][0]["paragraphs"][0]
    assert para["status"] == "needs_input"
    assert [item["kind"] for item in retyped.json()["open_questions"]] == ["needs_input"]

    # The panel row's one click (✓ Confirm or ≈ Mark assumed).
    switched = client.post(
        "/api/doc/edit",
        json={"ops": [{"action": "set_status", "target_id": "pt1.a1.p1", "status": status}]},
    )
    assert switched.status_code == 200
    para = switched.json()["doc"]["parts"][0]["articles"][0]["paragraphs"][0]
    assert para["status"] == status
    assert switched.json()["open_questions"] == []
    checks = {c["id"]: c for c in client.get("/api/readiness").json()["checks"]}
    assert checks["no_open_items"]["ok"] is True
    assert "No leftover placeholders" in checks["no_open_items"]["detail"]


def test_a_qc_fix_that_would_insert_a_placeholder_is_never_a_safe_fix():
    section, _ = apply_edits(
        SpecSection.empty(),
        [
            {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"},
            {
                "action": "add_paragraph",
                "target_id": "pt1.a1",
                "text": "Provide sprinkler systems.",
            },
        ],
    )

    def finding(ops):
        return QCFinding(
            finding_id="qc-test",
            lens_id="completeness",
            severity="major",
            element_id="pt1.a1.p1",
            title="Density missing",
            issue="No design density.",
            rationale="NFPA 13.",
            proposed_ops=ops,
        )

    placeholder = finding(
        [
            {
                "action": "replace",
                "target_id": "pt1.a1.p1",
                "text": "Provide sprinkler systems at [TBD: density].",
            }
        ]
    )
    _validate_ops(placeholder, section)
    assert placeholder.ops_valid is False
    assert "[TBD: density]" in placeholder.ops_invalid_reason

    flagged = finding(
        [{"action": "set_status", "target_id": "pt1.a1.p1", "status": "needs_input"}]
    )
    _validate_ops(flagged, section)
    assert flagged.ops_valid is False

    directive = finding(
        [
            {
                "action": "replace",
                "target_id": "pt1.a1.p1",
                "text": "Provide sprinkler systems designed for the densities "
                "indicated on the Drawings.",
            }
        ]
    )
    _validate_ops(directive, section)
    assert directive.ops_valid is True


def test_a_retained_qc_fix_that_writes_a_placeholder_is_not_applyable():
    """A report saved before the guard existed can carry ops_valid=True."""
    from types import SimpleNamespace

    from backend.qc.apply import (
        FIX_CLASS_ADVISORY,
        FIX_CLASS_SAFE,
        finding_fix_class,
        select_apply_candidates,
    )

    def retained(finding_id, ops):
        return SimpleNamespace(
            finding_id=finding_id,
            ops_semantic_status="approved",
            ops_valid=True,
            proposed_ops=ops,
            status="open",
        )

    placeholder = retained(
        "qc-old-tbd",
        [{"action": "replace", "target_id": "pt1.a1.p1", "text": "Density: [TBD]."}],
    )
    flagged = retained(
        "qc-old-flag",
        [{"action": "set_status", "target_id": "pt1.a1.p1", "status": "needs_input"}],
    )
    directive = retained(
        "qc-directive",
        [{"action": "set_status", "target_id": "pt1.a1.p1", "status": "confirmed"}],
    )
    assert finding_fix_class(placeholder) == FIX_CLASS_ADVISORY
    assert finding_fix_class(flagged) == FIX_CLASS_ADVISORY
    assert finding_fix_class(directive) == FIX_CLASS_SAFE

    by_id = {f.finding_id: f for f in (placeholder, flagged, directive)}
    result = SimpleNamespace(finding=by_id.get)
    outcomes, skipped, eligible = select_apply_candidates(
        result, ["qc-old-tbd", "qc-old-flag", "qc-directive"]
    )
    assert outcomes == {"qc-old-tbd": "no_ops", "qc-old-flag": "no_ops"}
    assert [finding_id for finding_id, _reason, _note in skipped] == [
        "qc-old-tbd",
        "qc-old-flag",
    ]
    assert [finding_id for finding_id, _ops in eligible] == ["qc-directive"]


def test_ai_template_generalization_cannot_introduce_a_placeholder():
    section, _ = apply_edits(
        SpecSection.empty(),
        [
            {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"},
            {
                "action": "add_paragraph",
                "target_id": "pt1.a1",
                "text": "Provide sprinkler protection for the Acme campus.",
            },
        ],
    )
    neutral = copy.deepcopy(section)
    neutral.parts[0].articles[0].paragraphs[0].text = (
        "Provide sprinkler protection for the Project."
    )
    bracketed = copy.deepcopy(section)
    bracketed.parts[0].articles[0].paragraphs[0].text = (
        "Provide sprinkler protection for [INSERT PROJECT NAME]."
    )
    original = _template_structure_contract(section)
    assert _template_structure_contract(neutral) == original
    assert _template_structure_contract(bracketed) != original


def test_ai_template_generalization_keeps_each_placeholder_exactly():
    section, _ = apply_edits(
        SpecSection.empty(),
        [
            {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"},
            {
                "action": "add_paragraph",
                "target_id": "pt1.a1",
                "text": "Design density for the Acme campus: [TBD: design density].",
            },
        ],
    )

    def generalized(text):
        candidate = copy.deepcopy(section)
        candidate.parts[0].articles[0].paragraphs[0].text = text
        return _template_structure_contract(candidate)

    original = _template_structure_contract(section)
    # Generalizing the words around it is the point…
    assert generalized("Design density for the Project: [TBD: design density].") == original
    # …but the placeholder itself is the user's unresolved decision.
    for drift in (
        "Design density for the Project: [INSERT OWNER].",
        "Design density for the Project: [TBD: density].",
        "Design density for the Project: [TBD: design density] [TBD: area].",
        "Design density for the Project.",
    ):
        assert generalized(drift) != original, drift


# ---------------------------------------------------------------------------
# What the model is told
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module", [HYPERSCALE_FIRE, GENERIC], ids=lambda m: m.module_id)
def test_the_stable_prompt_teaches_specification_voice(module):
    prompt = render_system_prompt(module)
    assert "# Specification voice" in prompt
    # The owner's examples, both halves.
    assert "Virginia amends IBC" in prompt
    assert _WATERFLOW_SPEC_VOICE in prompt
    assert _NFPA25_REFERENCE in prompt
    assert "IBC §903.4.2 (Alarms)" in prompt
    # Unknowns are written around and asked about, never held in the text.
    assert "track_followups" in prompt
    assert "Mark any unresolved value inline" not in prompt
    assert "goes into the provision as [TBD" not in prompt
    # No playbook default tells the model to carry a placeholder.
    for topic in module.interview_playbook:
        assert not has_placeholder(topic.default or ""), topic.topic_id


def test_the_whole_section_directives_write_around_unknowns():
    for directive in (FULL_DRAFT_DIRECTIVE, ADAPT_IMPORTED_DIRECTIVE):
        assert "track_followups" in directive
        assert "[TBD: …] or needs_input" not in directive
    assert "never a [TBD]" in FULL_DRAFT_DIRECTIVE
    assert "specification voice" in FULL_DRAFT_DIRECTIVE


def test_the_tools_no_longer_ask_for_placeholders():
    description = APPLY_SPEC_EDITS_TOOL["description"]
    assert "Mark undecided values inline" not in description
    assert "track_followups" in description
    status = APPLY_SPEC_EDITS_TOOL["input_schema"]["properties"]["edits"]["items"][
        "properties"
    ]["status"]
    # The enum keeps every status so saved histories naming needs_input stay
    # valid input; the description and the guard restrict the model.
    assert status["enum"] == list(STATUSES)
    assert status["description"].startswith("confirmed or assumed.")
    assert "confirmed | assumed | needs_input" not in description
    assert "[TBD" not in TRACK_FOLLOWUPS_TOOL["description"]
    assert "never into the document" in TRACK_FOLLOWUPS_TOOL["description"]


@pytest.mark.parametrize("module", [HYPERSCALE_FIRE, GENERIC], ids=lambda m: m.module_id)
def test_the_edition_basis_is_marked_as_never_document_text(module):
    block = standards_context_block(
        module.basis,
        {"NFPA 25": {"edition": "2020", "basis": "2021 SFPC adopts the 2021 IFC"}},
    )
    assert "never write it into the document" in block
    assert "designation, full title, and edition" in block


def test_curated_starters_carry_no_placeholders_or_needs_input():
    for path in sorted(CURATED_ROOT.glob("*.bastemplate")):
        document = json.loads(path.read_text(encoding="utf-8"))["document"]
        section = SpecSection.from_dict(document)
        for part in section.parts:
            for article in part.articles:
                stack = list(article.paragraphs)
                while stack:
                    paragraph = stack.pop()
                    stack.extend(paragraph.children)
                    assert not has_placeholder(paragraph.text), (path.name, paragraph.uid)
                    assert paragraph.status != "needs_input", (path.name, paragraph.uid)
