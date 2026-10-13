"""Research items carry the passage they quote and the condition they apply
under (the prompt-review follow-up, item 7, 2026-10-13).

A research item said what a requirement is in the worker's own words, and
grounding proved only that a cited URL was retrieved. Each item now carries
``quote`` (copied word for word from a fetched page), ``quote_source`` (the
accepted source whose fetched text was found to contain it — decided by the
app inside the round, never by the worker) and ``applies_when`` (the
condition under which it governs). Pinned here:

- the schema asks for both fields, nullable, and the parse folds and clamps;
- the check reads only the item's own ACCEPTED sources' fetched text,
  tolerates copying artefacts and marked gaps, and refuses paraphrase,
  search-only sources and pages the item did not cite;
- a profile saved before the fields keeps its bytes and its fingerprint,
  and renders byte for byte as before; the fields render only when set;
- the cross-round merge only strengthens, and moves a quote with its source;
- every reader — the chat's render, the established-facts brief, the
  research drawer's view, the redline comment — says what it should, and a
  quote that was not found is never presented as the source's words.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from backend.llm.prompts import render_system_prompt
from backend.project_profile import ProjectProfile
from backend.qc.schema import QC_LENSES
from backend.redline_basis import _research_paragraphs
from backend.research import RequirementsProfile, run_requirements_research
from backend.research.engine import (
    APPLIES_WHEN_MAX_CHARS,
    QUOTE_MAX_CHARS,
    QUOTE_NOT_FOUND_LABEL,
    DimensionStatus,
    ResearchItem,
    _confirm_item,
    _established_fact_line,
    _items_from_payload,
    _render_item_line,
    build_research_system_prompt,
    profile_fingerprint,
)
from backend.research.quotes import (
    fetched_page_texts,
    quote_found_in,
    verify_item_quotes,
)
from backend.research.runner import _profile_view
from backend.research.schema import REQUIREMENTS_RESEARCH_SCHEMA
from backend.spec_modules.generic import GENERIC
from backend.spec_modules.hyperscale_fire import HYPERSCALE_FIRE
from tests.fakes import SequencedFakeClient, fetch_blocks, research_response
from tests.test_reference_docs import _pdf_bytes

PAGE = (
    "903.3.1.1 NFPA 13 sprinkler systems. Where the provisions of this code "
    "require that a building or portion thereof be equipped throughout with "
    "an automatic sprinkler system in accordance with this section, "
    "sprinklers shall be installed throughout in accordance with NFPA 13."
)
QUOTE = (
    "sprinklers shall be installed throughout in accordance with NFPA 13."
)


def _item(**overrides) -> ResearchItem:
    values = dict(
        item_id="r-0123456789ab",
        dimension_id="governing_codes",
        topic="Sprinklers",
        category="governing_code",
        requirement="Sprinklers throughout per NFPA 13.",
        authority="State",
        source_urls=["https://a.gov/doc"],
        accepted_sources=["https://a.gov/doc"],
        grounded=True,
        confidence=0.8,
    )
    values.update(overrides)
    return ResearchItem(**values)


# ---------------------------------------------------------------------------
# Schema and parse
# ---------------------------------------------------------------------------


def test_the_schema_asks_for_a_quote_and_a_condition_nullable():
    item = REQUIREMENTS_RESEARCH_SCHEMA["properties"]["items"]["items"]
    assert {"quote", "applies_when"} <= set(item["required"])
    for name in ("quote", "applies_when"):
        prop = item["properties"][name]
        assert prop["type"] == ["string", "null"]
        # The strict-schema convention: no length constraints (clamped at
        # parse time instead).
        assert "maxLength" not in prop
    assert "word for word" in item["properties"]["quote"]["description"]
    assert "search result alone cannot be quoted" in item["properties"]["quote"]["description"]


def test_the_parse_folds_clamps_and_reads_null_as_empty():
    long_quote = "word " * 200
    items = _items_from_payload(
        {
            "items": [
                {
                    "category": "governing_code",
                    "requirement": "A.",
                    "quote": "  Sprinklers shall\n be installed.  ",
                    "applies_when": "only where\tthe AHJ adopted the 2024 IFC",
                },
                {"category": "governing_code", "requirement": "B.", "quote": None,
                 "applies_when": None},
                {"category": "governing_code", "requirement": "C.",
                 "quote": long_quote, "applies_when": "x " * 400},
            ]
        },
        "governing_codes",
    )
    assert items[0].quote == "Sprinklers shall be installed."
    assert items[0].applies_when == "only where the AHJ adopted the 2024 IFC"
    assert items[0].quote_source == ""  # never the worker's to set
    assert (items[1].quote, items[1].applies_when) == ("", "")
    assert len(items[2].quote) == QUOTE_MAX_CHARS and items[2].quote.endswith("…")
    assert len(items[2].applies_when) == APPLIES_WHEN_MAX_CHARS
    # The id is still the content hash of (dimension, category, requirement).
    plain = _items_from_payload(
        {"items": [{"category": "governing_code", "requirement": "A."}]},
        "governing_codes",
    )
    assert plain[0].item_id == items[0].item_id


# ---------------------------------------------------------------------------
# The match
# ---------------------------------------------------------------------------


def test_a_quote_matches_through_copying_artefacts():
    assert quote_found_in(QUOTE, PAGE)
    # Case, whitespace, curly quotes and dashes, markdown emphasis.
    assert quote_found_in("SPRINKLERS  shall be\ninstalled throughout", PAGE)
    assert quote_found_in(f"“{QUOTE}”", PAGE)
    assert quote_found_in(
        "the term “automatic sprinkler system” means a system",
        'In this code the term "automatic sprinkler system" means a system of pipes.',
    )
    assert quote_found_in("a 2–3 hour rating applies here", "A 2-3 hour **rating** applies here.")
    # A marked gap: each fragment, in order.
    assert quote_found_in(
        "Where the provisions of this code require ... sprinklers shall be installed",
        PAGE,
    )
    assert quote_found_in(
        "Where the provisions of this code require … sprinklers shall be installed",
        PAGE,
    )


def test_a_paraphrase_a_reordering_or_a_scrap_is_not_found():
    assert not quote_found_in("Sprinklers must be installed throughout.", PAGE)
    assert not quote_found_in(
        "sprinklers shall be installed throughout ... Where the provisions of this code",
        PAGE,
    )
    # Too short to be a quote: it is on every page about sprinklers.
    assert not quote_found_in("sprinklers", PAGE)
    assert not quote_found_in("", PAGE)
    assert not quote_found_in(QUOTE, "")


def test_fetched_page_text_reads_text_and_pdf_and_skips_errors():
    pdf = _pdf_bytes([["Sprinklers shall be installed throughout in"], ["accordance with NFPA 13."]])
    responses = [
        SimpleNamespace(
            content=[
                *fetch_blocks("https://a.gov/doc", text=PAGE),
                *fetch_blocks("https://b.gov/code.pdf", pdf=pdf),
                SimpleNamespace(
                    type="web_fetch_tool_result",
                    tool_use_id="srvtoolu_err",
                    content={"type": "web_fetch_tool_result_error", "error_code": "url_not_accessible"},
                ),
                *fetch_blocks("https://c.gov/empty"),  # no document
            ]
        )
    ]
    pages = fetched_page_texts(responses)
    assert set(pages) == {"https://a.gov/doc", "https://b.gov/code.pdf"}
    assert quote_found_in(QUOTE, pages["https://b.gov/code.pdf"])


def test_a_fetch_split_by_a_pause_is_paired_across_responses():
    use, result = fetch_blocks("https://a.gov/doc", text=PAGE, echo_url=False)
    pages = fetched_page_texts(
        [SimpleNamespace(content=[use]), SimpleNamespace(content=[result])]
    )
    assert pages == {"https://a.gov/doc": PAGE}


def test_a_redirected_fetch_is_found_under_either_url():
    """Grounding accepts the URL asked for and the URL the result reports;
    the page text answers to both (Codex review on PR #324)."""
    use, result = fetch_blocks("https://a.gov/old-path", text=PAGE)
    result.content["url"] = "https://www.a.gov/new-path"
    responses = [SimpleNamespace(content=[use, result])]
    pages = fetched_page_texts(responses)
    assert set(pages) == {"https://a.gov/old-path", "https://www.a.gov/new-path"}
    for cited in ("https://a.gov/old-path", "https://www.a.gov/new-path"):
        item = _item(source_urls=[cited], accepted_sources=[cited], quote=QUOTE)
        verify_item_quotes([item], responses)
        assert item.quote_source == cited


def test_an_unreadable_pdf_is_simply_not_checked():
    responses = [SimpleNamespace(content=fetch_blocks("https://b.gov/x.pdf", pdf=b"not a pdf"))]
    assert fetched_page_texts(responses) == {}


def test_only_the_items_own_accepted_sources_count():
    responses = [
        SimpleNamespace(
            content=[
                *fetch_blocks("https://a.gov/doc", text="Nothing relevant on this page at all."),
                *fetch_blocks("https://b.gov/other", text=PAGE),
            ]
        )
    ]
    cites_a = _item()
    cites_b = _item(
        item_id="r-bbbbbbbbbbbb",
        source_urls=["https://b.gov/other"],
        accepted_sources=["https://b.gov/other"],
    )
    stale = _item(item_id="r-cccccccccccc", quote_source="https://a.gov/doc")
    for item in (cites_a, cites_b):
        item.quote = QUOTE
    verify_item_quotes([cites_a, cites_b, stale], responses)
    # The page that holds it was fetched, but cites_a never cited it.
    assert cites_a.quote_source == ""
    assert cites_b.quote_source == "https://b.gov/other"
    # No quote, no source — whatever a record claimed before.
    assert stale.quote_source == ""


# ---------------------------------------------------------------------------
# End to end through a research round
# ---------------------------------------------------------------------------


def _raw(requirement: str, urls: list[str], **overrides) -> dict:
    raw = {
        "topic": "topic",
        "category": "governing_code",
        "requirement": requirement,
        "actionability": "spec_requirement",
        "authority": "State",
        "code_reference": "",
        "applies_when": None,
        "source_urls": urls,
        "quote": None,
        "confidence": 0.8,
        "notes": "",
    }
    raw.update(overrides)
    return raw


def test_a_round_checks_each_quote_against_what_it_fetched():
    keys = {
        "governing_codes": "governing building and fire codes",
        "ahj_requirements": "authority having jurisdiction",
        "client_standards": "who reviews risk",
        "site_environment": "site and environmental factors",
    }
    scripts = {
        key: [research_response(items=[], searched_urls=["https://x.gov"])]
        for key in keys.values()
    }
    scripts[keys["governing_codes"]] = [
        research_response(
            items=[
                _raw("Found.", ["https://a.gov/doc"], quote=QUOTE),
                _raw("Fetched, not cited.", ["https://s.gov"], quote="Some other text here entirely, not on s.gov at all."),
                _raw("Search only.", ["https://s.gov"], quote=QUOTE),
                _raw(
                    "Conditional.",
                    ["https://a.gov/doc"],
                    applies_when="only where the AHJ has adopted the 2024 IFC",
                ),
            ],
            searched_urls=["https://s.gov"],
            extra_blocks=[
                *fetch_blocks("https://a.gov/doc", text=PAGE),
                *fetch_blocks(
                    "https://b.gov/other",
                    text="Some other text here entirely, not on s.gov at all.",
                ),
            ],
            fetches=2,
        )
    ]
    profile = run_requirements_research(
        HYPERSCALE_FIRE,
        ProjectProfile("Ashburn", "VA", "USA", "ExampleCo"),
        SequencedFakeClient(scripts),
        model="claude-sonnet-5",
        max_tokens=4096,
    )
    by_text = {item.requirement: item for item in profile.items}
    assert by_text["Found."].quote_source == "https://a.gov/doc"
    assert by_text["Found."].quote_found
    # Its text was fetched, but on a page this item never cited.
    assert by_text["Fetched, not cited."].quote_source == ""
    # A search result's text is encrypted: grounded, but nothing to check.
    assert by_text["Search only."].grounded
    assert by_text["Search only."].quote_source == ""
    assert by_text["Conditional."].applies_when == (
        "only where the AHJ has adopted the 2024 IFC"
    )
    # The round's record survives a save and a load.
    reloaded = RequirementsProfile.from_dict(json.loads(json.dumps(profile.to_dict())))
    assert reloaded.to_dict() == profile.to_dict()


# ---------------------------------------------------------------------------
# Bytes: a profile from before the fields
# ---------------------------------------------------------------------------


_LEGACY_ITEM = {
    "item_id": "r-0123456789ab",
    "dimension_id": "governing_codes",
    "topic": "Sprinklers",
    "category": "governing_code",
    "requirement": "Sprinklers throughout per NFPA 13.",
    "authority": "State",
    "code_reference": "",
    "source_urls": ["https://a.gov/doc"],
    "accepted_sources": ["https://a.gov/doc"],
    "grounded": True,
    "confidence": 0.8,
    "actionability": "spec_requirement",
    "notes": "",
    "research_date": "2026-10-01",
    "round_index": 1,
}


def _legacy_profile_dict() -> dict:
    profile = RequirementsProfile(
        items=[ResearchItem(**_LEGACY_ITEM)],
        dimension_statuses=[
            DimensionStatus(dimension_id="governing_codes", status="completed")
        ],
        research_date="2026-10-01",
    )
    return profile.to_dict()


def test_a_profile_from_before_the_fields_keeps_its_bytes_and_fingerprint():
    saved = _legacy_profile_dict()
    assert saved["items"] == [_LEGACY_ITEM]  # no new keys, in the old order
    loaded = RequirementsProfile.from_dict(json.loads(json.dumps(saved)))
    # (A profile without rounds gains its synthesized first round on load —
    # older behaviour, untouched here; the items are what item 7 could move.)
    assert json.dumps(loaded.to_dict()["items"]) == json.dumps([_LEGACY_ITEM])
    again = RequirementsProfile.from_dict(json.loads(json.dumps(loaded.to_dict())))
    assert json.dumps(again.to_dict()) == json.dumps(loaded.to_dict())
    assert profile_fingerprint(again) == profile_fingerprint(loaded)
    # Its render is exactly what it was.
    assert _render_item_line(loaded.items[0]) == (
        "- [r-0123456789ab] Sprinklers throughout per NFPA 13. "
        "(Authority: State; Sources: https://a.gov/doc; confidence 80%)"
    )
    assert _established_fact_line(loaded.items[0]) == (
        "- Sprinklers throughout per NFPA 13. (Authority: State; as of 2026-10-01)"
    )


def test_the_fields_serialize_only_when_set_and_load_checked():
    saved = _legacy_profile_dict()
    saved["items"][0].update(
        quote=QUOTE, quote_source="https://a.gov/doc", applies_when="only for new work"
    )
    loaded = RequirementsProfile.from_dict(saved).items[0]
    assert (loaded.quote, loaded.quote_source, loaded.applies_when) == (
        QUOTE, "https://a.gov/doc", "only for new work"
    )
    # A found-on claim naming a URL the item never accepted is dropped, and
    # the quote kept as not found.
    forged = _legacy_profile_dict()
    forged["items"][0].update(quote=QUOTE, quote_source="https://elsewhere.example")
    item = RequirementsProfile.from_dict(forged).items[0]
    assert (item.quote, item.quote_source) == (QUOTE, "")
    # A source with no quote is nothing.
    orphan = _legacy_profile_dict()
    orphan["items"][0].update(quote_source="https://a.gov/doc")
    item = RequirementsProfile.from_dict(orphan).items[0]
    assert item.quote_source == ""
    assert "quote_source" not in RequirementsProfile.from_dict(orphan).to_dict()["items"][0]


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def test_the_render_carries_the_condition_and_says_where_the_quote_was_found():
    found = _item(quote=QUOTE, quote_source="https://a.gov/doc", applies_when="only for new work")
    lines = _render_item_line(found).split("\n")
    assert lines[0] == (
        "- [r-0123456789ab] Sprinklers throughout per NFPA 13. (Authority: State; "
        "Applies when: only for new work; Sources: https://a.gov/doc; confidence 80%)"
    )
    assert lines[1] == f'    Quote (found on https://a.gov/doc): "{QUOTE}"'
    unfound = _item(quote=QUOTE)
    assert _render_item_line(unfound).split("\n")[1] == (
        f'    Quote ({QUOTE_NOT_FOUND_LABEL}): "{QUOTE}"'
    )
    assert "Applies when: only for new work" in _established_fact_line(found)
    # The brief stays compact: no quote in it.
    assert QUOTE not in _established_fact_line(found)


# ---------------------------------------------------------------------------
# Merge across rounds
# ---------------------------------------------------------------------------


def test_the_merge_moves_a_quote_with_its_source_and_only_strengthens():
    unfound = _item(quote="Prior words that were never found anywhere at all.")
    found = _item(quote=QUOTE, quote_source="https://a.gov/doc")
    blank = _item()

    merged = _confirm_item(unfound, found)
    assert (merged.quote, merged.quote_source) == (QUOTE, "https://a.gov/doc")
    # A found prior stays, even against another found quote.
    other_found = _item(
        quote="Another passage found on another accepted page entirely.",
        quote_source="https://a.gov/doc",
    )
    merged = _confirm_item(found, other_found)
    assert (merged.quote, merged.quote_source) == (QUOTE, "https://a.gov/doc")
    # A found prior is never replaced by an unfound fresh one.
    merged = _confirm_item(found, unfound)
    assert (merged.quote, merged.quote_source) == (QUOTE, "https://a.gov/doc")
    # A blank fills in; an unfound prior beats an unfound fresh.
    assert _confirm_item(blank, unfound).quote == unfound.quote
    assert _confirm_item(unfound, _item(quote="Fresh unfound words here.")).quote == unfound.quote
    # The condition: the prior's unless blank.
    assert _confirm_item(_item(applies_when="A"), _item(applies_when="B")).applies_when == "A"
    assert _confirm_item(blank, _item(applies_when="B")).applies_when == "B"


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------


def test_the_drawer_view_carries_the_fields():
    profile = RequirementsProfile(
        items=[_item(quote=QUOTE, quote_source="https://a.gov/doc", applies_when="only for new work")]
    )
    view = _profile_view(profile)["items"][0]
    assert view["quote"] == QUOTE
    assert view["quote_source"] == "https://a.gov/doc"
    assert view["applies_when"] == "only for new work"


def _paragraph_texts(item: ResearchItem) -> list[str]:
    return [p[0] for p in _research_paragraphs(item) if isinstance(p[0], str)]


def test_the_redline_shows_a_found_quote_and_never_an_unfound_one():
    found = _paragraph_texts(
        _item(quote=QUOTE, quote_source="https://a.gov/doc", applies_when="only for new work")
    )
    assert "Applies when: only for new work" in found
    assert f'Source text: "{QUOTE}"' in found
    unfound = _paragraph_texts(_item(quote=QUOTE))
    assert not any(QUOTE in text for text in unfound)
    # An item from before the fields says what it said.
    legacy = _paragraph_texts(_item())
    assert not any(t.startswith(("Applies when", "Source text")) for t in legacy)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


def test_the_research_protocol_asks_for_both_fields():
    text = " ".join(build_research_system_prompt(HYPERSCALE_FIRE).split())
    assert "applies_when states the condition under which the requirement governs" in text
    assert "Never leave a condition only in notes." in text
    assert "quote copies, word for word" in text
    assert "a search result alone cannot be quoted" in text
    assert "never write one from memory" in text


def test_the_governing_codes_briefs_put_the_condition_in_applies_when():
    for module in (HYPERSCALE_FIRE, GENERIC):
        dimension = next(
            d for d in module.research_dimensions if d.dimension_id == "governing_codes"
        )
        assert "with the condition in applies_when" in " ".join(
                dimension.prompt_template.split()
            )


def test_the_chat_reads_a_condition_as_scope_and_a_quote_by_its_label():
    prompt = render_system_prompt(HYPERSCALE_FIRE)
    assert 'An item may carry "Applies when: …"' in prompt
    assert "Draft to it only where the project's facts meet that condition" in prompt
    assert '"found on <url>" means the app found that passage on a page the research fetched' in prompt
    assert '"not found on a fetched page" means nobody could check it' in prompt
    assert "never present it as the source's words" in prompt
    assert QUOTE_NOT_FOUND_LABEL in prompt


def test_final_qc_reads_conditions_and_quotes():
    briefs = {lens.lens_id: " ".join(lens.brief.split()) for lens in QC_LENSES}
    assert "A line carrying 'Applies when' governs only where the project meets that condition" in briefs["completeness"]
    assert "missing, unless an item in <open_followups> already tracks it" in briefs["completeness"]
    assert "carries a Quote found on a fetched page, those are the source's words" in briefs["code_compliance"]
    assert "A quote marked not found was never checked" in briefs["code_compliance"]

