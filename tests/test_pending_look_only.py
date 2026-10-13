"""A "pending" hold point is a look, not a defect (owner decision, 2026-10-13).

The explanatory-prose lint flags "pending" for a look only, and the prompt
tells the model to leave it where it is a real condition of the work ("Do not
fabricate piping pending acceptance of shop drawings"). But every lint issue
held the ``lint_clean`` readiness check and none can be dismissed, so a
section with a legitimate hold point could never read ready. Option A: a
provision whose ONLY explanatory-prose hit is "pending" still appears in the
lint (the panel, the payload, the model's LINT REPORT), marked as a look
(``look_only``, severity info), and does not hold readiness. Anything else in
the same provision — a reason, a hedge — makes it a breach like any other.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.llm.prompts import render_system_prompt
from backend.spec_doc.linting import (
    RULE_EXPLANATORY_PROSE,
    holds_readiness,
    lint_document,
)
from backend.spec_doc.model import SpecSection, apply_edits
from backend.spec_doc.spec_voice import (
    PENDING_LOOK_LABEL,
    explanatory_prose_hits,
    look_only_prose,
)
from backend.spec_modules.generic import GENERIC
from backend.spec_modules.hyperscale_fire import HYPERSCALE_FIRE
from tests.fakes import FakeClient, request_context_text, text_turn

HOLD_POINT = "Do not fabricate piping pending acceptance of shop drawings."
HOLD_POINT_WITH_REASON = (
    "Do not fabricate piping pending acceptance of shop drawings because "
    "field changes are costly."
)
HEDGE = "The Contractor should provide spare sprinkler heads."

_ISSUE_KEYS = {"id", "rule", "severity", "element_id", "ref", "message", "match"}


def _flat(text: str) -> str:
    return " ".join(text.split())


def _ops(*texts: str) -> list[dict]:
    ops = [
        {
            "action": "replace",
            "target_id": "sec",
            "text": "WET-PIPE SPRINKLER SYSTEMS",
            "numbering": "21 13 13",
        },
        {"action": "add_article", "target_id": "pt3", "text": "INSTALLATION"},
    ]
    ops += [
        {"action": "add_paragraph", "target_id": "pt3.a1", "text": text}
        for text in texts
    ]
    return ops


def _prose_issues(*texts: str) -> list[dict]:
    section, _ = apply_edits(SpecSection.empty(), _ops(*texts))
    return [
        issue
        for issue in lint_document(section, HYPERSCALE_FIRE)
        if issue["rule"] == RULE_EXPLANATORY_PROSE
    ]


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------


def test_only_a_pending_alone_is_a_look():
    assert look_only_prose(explanatory_prose_hits(HOLD_POINT))
    assert not look_only_prose(explanatory_prose_hits(HOLD_POINT_WITH_REASON))
    assert not look_only_prose(explanatory_prose_hits(HEDGE))
    assert not look_only_prose([])
    # Two "pending"s are still only a look.
    assert look_only_prose(
        explanatory_prose_hits("Hold work pending review and pending approval.")
    )
    (hit,) = explanatory_prose_hits(HOLD_POINT)
    assert hit["label"] == PENDING_LOOK_LABEL


def test_a_hold_point_is_reported_as_a_look_that_does_not_hold_readiness():
    (issue,) = _prose_issues(HOLD_POINT)
    assert issue["look_only"] is True
    assert issue["severity"] == "info"
    assert not holds_readiness(issue)
    assert issue["match"].lower() == "pending"
    message = _flat(issue["message"])
    assert "Check 'pending'" in message
    assert "it does not hold issue readiness" in message
    assert "Rewrite it as the requirement alone" not in message


def test_pending_beside_a_reason_is_a_breach():
    (issue,) = _prose_issues(HOLD_POINT_WITH_REASON)
    assert "look_only" not in issue
    assert issue["severity"] == "warn"
    assert holds_readiness(issue)
    assert "Rewrite it as the requirement alone" in issue["message"]
    assert "'because'" in issue["message"] and "'pending'" in issue["message"]


def test_every_other_issue_keeps_its_shape():
    (issue,) = _prose_issues(HEDGE)
    assert set(issue) == _ISSUE_KEYS
    assert holds_readiness(issue)


# ---------------------------------------------------------------------------
# Readiness, the payload and the model's report
# ---------------------------------------------------------------------------


def _client_with(*texts: str) -> tuple[TestClient, list[dict]]:
    client = TestClient(create_app())
    edited = client.post("/api/doc/edit", json={"ops": _ops(*texts)})
    assert edited.status_code == 200, edited.text
    return client, edited.json()["lint"]


def _lint_check(client: TestClient) -> dict:
    readiness = client.get("/api/readiness").json()
    return next(c for c in readiness["checks"] if c["id"] == "lint_clean")


def test_a_hold_point_alone_leaves_lint_clean_and_stays_visible():
    client, lint = _client_with(HOLD_POINT)
    # Still shown: the panel and the payload carry it as a look.
    assert [issue["rule"] for issue in lint] == [RULE_EXPLANATORY_PROSE]
    assert lint[0]["look_only"] is True
    check = _lint_check(client)
    assert check["ok"] is True and check["advisory"] is False
    assert check["detail"] == (
        "Lint clean apart from 1 'pending' look(s) left, which do not hold "
        "readiness."
    )


def test_a_real_issue_beside_a_hold_point_still_blocks():
    client, lint = _client_with(HOLD_POINT, HEDGE)
    assert len(lint) == 2
    check = _lint_check(client)
    assert check["ok"] is False
    assert check["detail"] == (
        "1 lint issue(s) to fix. 1 'pending' look(s) left, which do not hold "
        "readiness."
    )


def test_the_model_reads_which_finding_is_a_look(monkeypatch):
    client, _ = _client_with(HOLD_POINT, HEDGE)
    fake = FakeClient([text_turn(["Noted."])])
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 200
    context = _flat(request_context_text(fake.messages.requests[0]))
    assert (
        "each issue holds issue readiness until fixed, except one marked "
        "'look only'" in context
    )
    assert "[explanatory_prose, look only]" in context
    # The hedge is an ordinary issue.
    assert context.count("[explanatory_prose]") == 1


def test_the_lint_policy_says_a_hold_point_stays_and_does_not_hold_readiness():
    for module in (GENERIC, HYPERSCALE_FIRE):
        prompt = _flat(render_system_prompt(module))
        assert (
            '"pending" alone is flagged for a look only (marked "look only" in '
            "the report): leave it where it is a real condition of the work, "
            'such as a hold point ("pending acceptance of shop drawings"), and '
            "it does not hold issue readiness." in prompt
        )
