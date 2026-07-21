"""Research output-tool schema and web server-tool builders.

Ported from Claude-Spec-Critic: the ``submit_requirements_research`` tool
and its strict-mode schema from ``src/review/structured_schemas.py``
(research slice), and the ``web_search_20260209`` / ``web_fetch_20260209``
server-tool builders with the authoritative-domains blocklist from
``src/core/api_config.py``.

Conventions preserved from the source:

- Strict-mode schema subset: every property required, optionals nullable,
  no numerical constraints (confidence clamps at parse time).
- ``strict: true`` is attached only for models known to support structured
  outputs (a misconfigured model override degrades to a lenient tool, never
  an API rejection).
- Research sends NO ``tool_choice`` — the ``_20260209`` web server tools
  run dynamic filtering (programmatic tool calling under the hood) and the
  API rejects a forcing/parallel-disable tool_choice combined with it. The
  system prompt instructs the model to end its turn with the research
  tool; the tagged-JSON fallback stays reachable for text detours.
"""
from __future__ import annotations

from typing import Any

from .. import settings

RESEARCH_TOOL_NAME = "submit_requirements_research"

RESEARCH_ITEM_CATEGORIES: tuple[str, ...] = (
    "governing_code",
    "local_amendment",
    "referenced_standard",
    "ahj_requirement",
    "client_standard",
    "insurer_requirement",
    "site_environment",
)

# ``spec_requirement`` is content the specification must contain or match;
# ``process_advisory`` is a permit/schedule/process fact the project team
# must act on but which is not spec text. Unknown values coerce to
# ``spec_requirement`` at parse — the safe default (can only over-check).
RESEARCH_ACTIONABILITY_VALUES: tuple[str, ...] = (
    "spec_requirement",
    "process_advisory",
)

REQUIREMENTS_RESEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "items"],
    "properties": {
        "summary": {
            "type": "string",
            "description": (
                "Short narrative of what was researched and how well it "
                "grounded. Empty string is acceptable."
            ),
        },
        "items": {
            "type": "array",
            "description": "Zero or more discrete requirements or facts.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "topic",
                    "category",
                    "requirement",
                    "actionability",
                    "authority",
                    "code_reference",
                    "source_urls",
                    "confidence",
                    "notes",
                ],
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "Short label (a few words).",
                    },
                    "category": {
                        "type": "string",
                        "enum": list(RESEARCH_ITEM_CATEGORIES),
                        "description": "Requirement class.",
                    },
                    "requirement": {
                        "type": "string",
                        "description": (
                            "ONE discrete requirement or fact, stated so a "
                            "specification writer can act on it."
                        ),
                    },
                    "actionability": {
                        "type": "string",
                        "enum": list(RESEARCH_ACTIONABILITY_VALUES),
                        "description": (
                            "spec_requirement: content the specification "
                            "must contain or match. process_advisory: a "
                            "permit/schedule/process fact (fees, notice "
                            "periods, seasonal windows) the project team "
                            "must act on but which is not spec text."
                        ),
                    },
                    "authority": {
                        "type": ["string", "null"],
                        "description": "Who imposes it (agency, insurer, client).",
                    },
                    "code_reference": {
                        "type": ["string", "null"],
                        "description": "Code/standard section citation when one exists.",
                    },
                    "source_urls": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "URLs of sources retrieved in this conversation "
                            "that support the requirement. Never cite a URL "
                            "you did not actually retrieve."
                        ),
                    },
                    "confidence": {
                        "type": "number",
                        "description": (
                            "0..1 confidence. Use 0 for a requirement you "
                            "could not ground in retrieved sources (and "
                            "explain in notes) — never guess."
                        ),
                    },
                    "notes": {
                        "type": ["string", "null"],
                        "description": (
                            "Caveats: paywalled primary source, official "
                            "summary used instead, pending amendments, etc."
                        ),
                    },
                },
            },
        },
    },
}

# Models known to support strict structured outputs (mirrors Spec Critic's
# capability-whitelist posture without the full capability table): attach
# ``strict: true`` only for these; an env-overridden unknown model gets the
# lenient tool shape — a smaller safe request, never a 400.
_STRICT_CAPABLE_MODELS = frozenset(
    {settings.MODEL_SONNET_5, settings.MODEL_OPUS_48, settings.MODEL_FABLE_5}
)


def requirements_research_tool(*, model: str | None = None) -> dict[str, Any]:
    tool: dict[str, Any] = {
        "name": RESEARCH_TOOL_NAME,
        "description": (
            "After researching with web search/fetch, submit the structured "
            "requirements-research output for this dimension. Use this tool "
            "exactly once as the final step of your turn."
        ),
        "input_schema": REQUIREMENTS_RESEARCH_SCHEMA,
    }
    if model in _STRICT_CAPABLE_MODELS:
        tool["strict"] = True
    return tool


def extract_tool_use_block(response: object, tool_name: str) -> dict[str, Any] | None:
    """Return the input dict of the last ``tool_use`` block named ``tool_name``.

    Walks the response's content blocks (SDK objects or plain dicts) in
    reverse so the model's final call wins. Returns ``None`` when absent.
    """
    content = getattr(response, "content", None)
    if content is None and isinstance(response, dict):
        content = response.get("content")
    for block in reversed(list(content or [])):
        block_type = getattr(block, "type", None)
        if block_type is None and isinstance(block, dict):
            block_type = block.get("type")
        if block_type != "tool_use":
            continue
        name = getattr(block, "name", None)
        if name is None and isinstance(block, dict):
            name = block.get("name")
        if name != tool_name:
            continue
        tool_input = getattr(block, "input", None)
        if tool_input is None and isinstance(block, dict):
            tool_input = block.get("input")
        if isinstance(tool_input, dict):
            return tool_input
    return None


# ---------------------------------------------------------------------------
# Web server tools (ported from api_config.py)
# ---------------------------------------------------------------------------

# Domains excluded from research retrieval: aggregators, LLM-assistant
# outputs, trade forums, and DIY content farms are not authoritative for
# code-compliance facts. One policy for both tools — a domain we won't
# search is a domain we won't fetch.
WEB_BLOCKED_DOMAINS: tuple[str, ...] = (
    # Aggregators / Q&A
    "reddit.com", "quora.com", "medium.com",
    "stackexchange.com", "stackoverflow.com",
    "answers.yahoo.com", "fixya.com",
    # LLM-assistant outputs
    "chatgpt.com", "perplexity.ai", "openai.com", "gemini.google.com",
    "claude.ai", "you.com", "phind.com", "copilot.microsoft.com",
    "poe.com", "character.ai", "jasper.ai", "writesonic.com",
    # Trade forums (peer chatter, not authoritative for code compliance)
    "diychatroom.com", "forums.jlconline.com", "hvac-talk.com",
    "inspectionnews.net", "inspectorsforum.com", "contractortalk.com",
    # DIY / home-improvement / lead-gen content farms
    "doityourself.com", "homeadvisor.com", "thumbtack.com", "angi.com",
    "ehow.com", "wikihow.com", "about.com", "thespruce.com", "bobvila.com",
)

# Truncation ceiling on fetched-page content: big code-publisher pages can
# exceed 100k tokens of rendered text; cap so one fetch cannot blow the
# research input window.
WEB_FETCH_MAX_CONTENT_TOKENS = 50_000


def build_web_search_tool(
    *, max_uses: int, user_location: dict | None = None
) -> dict[str, Any]:
    """The ``web_search_20260209`` server-tool dict.

    ``user_location`` comes from ``ProjectProfile.web_search_user_location``
    so every research search runs as the project's own locale — the whole
    point of the phase.
    """
    tool: dict[str, Any] = {
        "type": "web_search_20260209",
        "name": "web_search",
        "blocked_domains": list(WEB_BLOCKED_DOMAINS),
        "max_uses": max_uses,
    }
    if user_location:
        tool["user_location"] = dict(user_location)
    return tool


def build_web_fetch_tool(*, max_uses: int) -> dict[str, Any]:
    """The ``web_fetch_20260209`` server-tool dict.

    Generally available — no ``anthropic-beta`` header (sending a retired
    beta value is rejected with HTTP 400). Citations enabled so cited URLs
    land in the grounding partition like search citations do.
    """
    return {
        "type": "web_fetch_20260209",
        "name": "web_fetch",
        "blocked_domains": list(WEB_BLOCKED_DOMAINS),
        "max_uses": max_uses,
        "citations": {"enabled": True},
        "max_content_tokens": WEB_FETCH_MAX_CONTENT_TOKENS,
    }
