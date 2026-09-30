"""The interview writes its reply after its last tool call (P55-2).

The 5.5 prompting upgrade (its plan is retired; CLAUDE.md "The reply comes
after the last tool call" is the record), findings F1 and F7.
On Claude Sonnet 5.5, a note of more than a sentence or two written BETWEEN
tool calls comes back as a progress-update ``thinking`` block: the chat
collapses it and commit drops it. So the stable prompt and every directive
that stages reply chips now say the same thing — make every tool call first,
``suggest_prompts`` last, and write the reply after it, where it stays a
``text`` block. And the web-lookup policy carries the Sonnet 5.5 guide's
"even when you feel confident" sentence without the old line that read as
"don't search".
"""
from __future__ import annotations

import pytest

from backend.llm import prompts
from backend.llm.prompts import (
    ADAPT_IMPORTED_DIRECTIVE,
    FULL_DRAFT_DIRECTIVE,
    QC_DEBRIEF_DIRECTIVE,
    RESEARCH_DEBRIEF_DIRECTIVE,
    QcDebriefFacts,
    ResearchDebriefFacts,
    adapt_imported_directive,
    adapt_prerequisites_directive,
    draft_prerequisites,
    draft_prerequisites_directive,
    full_draft_directive,
    qc_debrief_directive,
    render_system_prompt,
    research_debrief_directive,
)
from backend.spec_modules.registry import AVAILABLE_MODULES
from backend.suggestions import SUGGEST_PROMPTS_TOOL

_ORDER = prompts._REPLY_AFTER_TOOL_CALLS


def _modules():
    return list(AVAILABLE_MODULES.values())


# --- The stable prompt ---------------------------------------------------


@pytest.mark.parametrize("module", _modules(), ids=lambda m: m.module_id)
def test_the_stable_prompt_makes_tool_calls_first_and_replies_last(module):
    prompt = render_system_prompt(module)
    # P55-2.1: tool calls first, the reply after the final one, and why.
    assert "2. Make every tool call the turn needs first" in prompt
    assert "3. After your final tool call, write your reply" in prompt
    assert "Write the reply last, after the final tool call" in prompt
    assert "brief, collapsed progress line" in prompt
    assert "not kept in the conversation" in prompt
    assert "A short progress note between tool calls is still welcome." in prompt
    # The old step 3 put the reply in chat with no order at all.
    assert "3. In chat, briefly say what changed" not in prompt


@pytest.mark.parametrize("module", _modules(), ids=lambda m: m.module_id)
def test_suggest_prompts_is_the_last_tool_call_before_the_closing_message(module):
    prompt = render_system_prompt(module)
    # P55-2.2, in the step list and in the policy that owns the tool.
    assert "and suggest_prompts last of all" in prompt
    assert (
        "Call it at most once per turn, as your LAST tool call, just before "
        "your closing message"
    ) in prompt
    assert "near the end of your reply" not in prompt
    assert "once your questions for the turn are on the table" not in prompt
    # The chips answer what the closing message is about to ask, not
    # questions already written out before the call.
    assert "when your closing message asks questions, lead with direct" in prompt
    assert "when you asked questions this turn" not in prompt


def test_the_suggest_prompts_tool_description_says_the_same_order():
    """The tool's own description rides ahead of the system prompt; left on
    "near the end of your reply", it would contradict the policy."""
    description = SUGGEST_PROMPTS_TOOL["description"]
    assert "as your LAST tool call, and write your closing message after it" in (
        description
    )
    assert "the questions your closing message asks" in description
    assert "near the end of your reply" not in description
    assert "questions you just asked" not in description


@pytest.mark.parametrize("module", _modules(), ids=lambda m: m.module_id)
def test_the_web_lookup_policy_checks_specifics_even_when_confident(module):
    prompt = render_system_prompt(module)
    # P55-2.4: the guide's sentence, adapted to the domain.
    assert "Use them to check specifics that may have changed since your training" in (
        prompt
    )
    assert "even when you feel confident" in prompt
    assert "A fact about to go into a provision is always worth a quick check." in (
        prompt
    )
    # The Research button still owns the systematic sweep ...
    assert "point the user at the Research button" in prompt
    # ... but the line that read as "don't search" is gone.
    assert "Quick lookups are NOT the requirements-research phase" not in prompt
    assert "piecemeal" not in prompt


@pytest.mark.parametrize("module", _modules(), ids=lambda m: m.module_id)
def test_the_stable_prompt_stays_module_deterministic(module):
    """The cache rule: the new wording is module-stable text, rendered the
    same way every time, with nothing session-varying in it."""
    assert render_system_prompt(module) == render_system_prompt(module)
    assert "PROJECT CONTEXT ===" not in render_system_prompt(module)


# --- Every directive that stages reply chips ------------------------------


def _ready():
    return draft_prerequisites(
        section_number="21 13 13",
        section_title="WET-PIPE SPRINKLER SYSTEMS",
        project_type="Data Center",
        country="US",
    )


def _research(new: int, repeat: int) -> ResearchDebriefFacts:
    return ResearchDebriefFacts(
        round_index=2,
        new_items=new,
        repeat_items=repeat,
        cumulative_items=new + repeat,
        grounded_items=new,
        areas_run=("Governing codes",),
    )


def _qc(status: str, *, open_findings: int = 0, disputed: int = 0) -> QcDebriefFacts:
    return QcDebriefFacts(
        execution_status=status,
        open_criticals=0,
        open_findings=open_findings,
        open_disputed=disputed,
        safe_fixes=open_findings,
        advisory=0,
        applied=0,
        dismissed=0,
        refuted=0,
        inconclusive=0,
        failed_lenses=("Completeness",) if status != "complete" else (),
    )


_DIRECTIVES = {
    "full draft (constant)": lambda: FULL_DRAFT_DIRECTIVE,
    "full draft (anchored)": lambda: full_draft_directive(_ready()),
    "adapt (constant)": lambda: ADAPT_IMPORTED_DIRECTIVE,
    "adapt (anchored)": lambda: adapt_imported_directive(_ready()),
    "full-draft prerequisites, one missing": lambda: draft_prerequisites_directive(
        draft_prerequisites(project_type="Hospital", country="US")
    ),
    "full-draft prerequisites, all missing": lambda: draft_prerequisites_directive(
        draft_prerequisites()
    ),
    "adapt prerequisites": lambda: adapt_prerequisites_directive(
        draft_prerequisites(section_number="21 13 13", section_title="WET-PIPE")
    ),
    "research debrief (constant)": lambda: RESEARCH_DEBRIEF_DIRECTIVE,
    "research debrief (full)": lambda: research_debrief_directive(_research(3, 1)),
    "research debrief (nothing new, re-confirmed)": lambda: (
        research_debrief_directive(_research(0, 2))
    ),
    "research debrief (nothing found at all)": lambda: research_debrief_directive(
        _research(0, 0)
    ),
    "QC debrief (constant)": lambda: QC_DEBRIEF_DIRECTIVE,
    "QC debrief (findings)": lambda: qc_debrief_directive(
        _qc("complete", open_findings=2, disputed=1)
    ),
    "QC debrief (clean)": lambda: qc_debrief_directive(_qc("complete")),
    "QC debrief (partial)": lambda: qc_debrief_directive(_qc("partial")),
    "QC debrief (cancelled)": lambda: qc_debrief_directive(_qc("cancelled")),
}


@pytest.mark.parametrize("build", _DIRECTIVES.values(), ids=_DIRECTIVES.keys())
def test_every_chip_staging_directive_stages_first_and_closes_after(build):
    """P55-2.3: each directive asks for suggested replies AND says they come
    before the closing message, in the one shared sentence."""
    text = build()
    assert "suggested replies" in text
    assert _ORDER in text
    assert "closing message" in text


def test_the_shared_ordering_sentence_says_what_the_prompt_says():
    assert _ORDER == (
        "Order matters: make every tool call first, with the suggested "
        "replies as the last one, and write your whole reply to me after "
        "them, as your closing message."
    )


@pytest.mark.parametrize(
    "text",
    [RESEARCH_DEBRIEF_DIRECTIVE, QC_DEBRIEF_DIRECTIVE],
    ids=["research", "qc"],
)
def test_a_debrief_brief_is_the_closing_message(text):
    """A debrief's whole brief — the part most at risk of arriving as a
    collapsed progress note — is named as the closing message."""
    assert "The whole brief is that closing message." in text
    assert "end the brief by asking whether I want" in text
    # The old order put the question first and the chips as an afterthought.
    assert "Close by asking whether I want" not in text


@pytest.mark.parametrize(
    "text",
    [FULL_DRAFT_DIRECTIVE, ADAPT_IMPORTED_DIRECTIVE],
    ids=["full draft", "adapt"],
)
def test_a_whole_section_pass_closes_after_its_last_edit(text):
    assert "When the last edit is in, stage suggested replies" in text
    assert "When you're done, give me a short summary" not in text
