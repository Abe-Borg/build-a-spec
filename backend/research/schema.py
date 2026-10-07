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
- Research's web-tooled requests send NO ``tool_choice``. The system prompt
  instructs the model to end its turn with the research tool; the
  tagged-JSON fallback stays reachable for text detours. (Forcing one was
  impossible while the web tools ran dynamic filtering, which rejects a
  forcing/parallel-disable ``tool_choice``; ``WEB_TOOL_ALLOWED_CALLERS``
  lifts that constraint, but the behavior is deliberately unchanged — see
  below.) Only the final submission, which declares the output tool alone,
  forces it, and only on the models :func:`single_output_tool_kwargs` lists.

Both web server tools declare ``allowed_callers: ["direct"]`` — see
:data:`WEB_TOOL_ALLOWED_CALLERS` for why that is not the default and what
the default cost.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

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
    {
        settings.MODEL_SONNET_55,
        settings.MODEL_HAIKU_55,
        settings.MODEL_SONNET_5,
        settings.MODEL_OPUS_48,
        settings.MODEL_OPUS_5,
        settings.MODEL_OPUS_55,
        settings.MODEL_FABLE_5,
    }
)


# Forced tool choice under adaptive thinking is a separate capability from
# strict schemas. Only explicitly confirmed models belong here: Sonnet 5.5
# and Opus 5.5 reject forcing, and unknown overrides keep automatic choice.
# Haiku 5.5 accepts forcing but suppresses thinking when forced. Keep its
# output choice automatic so extraction can reason at the requested effort.
# See docs/as-built.md's "Single-shot outputs finish before they are accepted" note.
_FORCED_OUTPUT_TOOL_MODELS = frozenset(
    {settings.MODEL_SONNET_5, settings.MODEL_OPUS_5, settings.MODEL_FABLE_5}
)


def single_output_tool_kwargs(*, model: str, tool_name: str) -> dict[str, Any]:
    """Claude API options for an adaptive-thinking call with one output tool.

    Use only for single-shot extraction, or for a final request that
    declares the output tool alone (research's submission, whose thinking
    :func:`lowest_thinking` sets) — never for a request that also offers
    other tools. An unverified or incompatible model gets no extra request
    field: it runs on automatic choice, so its caller must check that the
    call was made.
    """
    if model not in _FORCED_OUTPUT_TOOL_MODELS:
        return {}
    return {"tool_choice": {"type": "tool", "name": tool_name}}


# The lowest thinking setting each model accepts, for a request that only
# writes up work already done (research's final submission). Anthropic's
# reference: Sonnet 5.5 rejects ``{"type": "disabled"}`` with a 400 and
# offers ``{"type": "between_tools"}`` instead, at effort ``high`` or below
# and with no other field inside ``thinking``; no other model accepts
# ``between_tools``. Sonnet 5 and Opus 4.8 accept ``disabled``; Opus 5 and
# Haiku 5.5 accept it only at effort ``high`` or below. Opus 5.5 and Fable
# reject both, as does anything unlisted here, which keeps the adaptive
# thinking the conversation
# already ran on — the one setting the model is known to accept.
_THINKING_OFF_EFFORTS = frozenset({"low", "medium", "high"})
_BETWEEN_TOOLS_MODELS = frozenset({settings.MODEL_SONNET_55})
_DISABLED_THINKING_MODELS = frozenset({settings.MODEL_SONNET_5, settings.MODEL_OPUS_48})
_DISABLED_THINKING_AT_HIGH_OR_BELOW = frozenset(
    {settings.MODEL_OPUS_5, settings.MODEL_HAIKU_55}
)


def lowest_thinking(*, model: str, effort: str) -> dict[str, str]:
    """The cheapest ``thinking`` value ``model`` accepts at ``effort``.

    ``{"type": "between_tools"}`` on Sonnet 5.5, ``{"type": "disabled"}``
    where the model still takes it, otherwise ``{"type": "adaptive"}``. A
    new dict each call. ``adaptive`` means thinking stays on: the caller
    must then send the conversation's thinking blocks back unchanged, and
    ask for invalidated ones to be dropped (:func:`with_drop_block`) when it
    changed the prefix. Neither of the other two may carry ``block_binding``.
    """
    if effort in _THINKING_OFF_EFFORTS and model in _BETWEEN_TOOLS_MODELS:
        return {"type": "between_tools"}
    if model in _DISABLED_THINKING_MODELS or (
        effort in _THINKING_OFF_EFFORTS
        and model in _DISABLED_THINKING_AT_HIGH_OR_BELOW
    ):
        return {"type": "disabled"}
    return {"type": "adaptive"}


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


def _block_field(block: object, field: str) -> Any:
    """One field of a content block, whether an SDK object or a plain dict."""
    value = getattr(block, field, None)
    if value is None and isinstance(block, dict):
        value = block.get(field)
    return value


def extract_tool_use_block(response: object, tool_name: str) -> dict[str, Any] | None:
    """Return the input dict of the last ``tool_use`` block named ``tool_name``.

    Walks the response's content blocks (SDK objects or plain dicts) in
    reverse so the model's final call wins. Returns ``None`` when absent.

    An exact name wins wherever it appears in the response. Only when no
    block matches exactly is a name that differs in letter case alone
    accepted (``str.casefold``), the last such block again winning: the
    Sonnet 5.5 prompting guide says the model occasionally calls a declared
    tool by a name that differs only in case, and to accept the call when
    the match is unambiguous. It is unambiguous here because every output
    tool this app declares (research, the three Final QC tools, the fact
    harvest, the template pass and the audit) is lowercase snake_case, each
    request declares exactly one of them, and no two differ only in case.
    The chat does NOT use this function: its tools are dispatched by exact
    name, and a mis-cased chat call gets an ``is_error`` result naming the
    right tool instead (``conversation._run_tool``).
    """
    content = getattr(response, "content", None)
    if content is None and isinstance(response, dict):
        content = response.get("content")
    wanted = tool_name.casefold()
    fallback: dict[str, Any] | None = None
    for block in reversed(list(content or [])):
        if _block_field(block, "type") != "tool_use":
            continue
        name = _block_field(block, "name")
        if not isinstance(name, str):
            continue
        tool_input = _block_field(block, "input")
        if not isinstance(tool_input, dict):
            continue
        if name == tool_name:
            return tool_input
        if fallback is None and name.casefold() == wanted:
            fallback = tool_input
    return fallback


def missing_output_tool_reply(
    response: object,
    *,
    reminder: str,
    wrong_tool: Callable[[str], str],
) -> dict[str, Any]:
    """The user turn that answers a fan-out reply which recorded nothing.

    A research area or a streamed Final QC call is recorded only by its
    output tool, and the Opus 5.5 prompting guide warns that a model can end
    an unattended turn with text instead of the tool call (the 5.5 prompting
    upgrade, P55-4). The engines answer such a turn — at most twice per
    conversation, each with its own wording — by appending the assistant
    content verbatim and then this ONE user message:

    - a reply holding no client ``tool_use`` block gets a single text block,
      ``reminder``;
    - a reply holding client ``tool_use`` blocks, none of which yielded the
      output tool's payload (a name the model invented), gets one
      ``is_error`` ``tool_result`` per call, each reading
      ``wrong_tool(name)``. The API requires a result for every call, so
      text must not replace them, and no text rides beside them: the Sonnet
      5.5 guide says harness text after tool results can read as a prompt
      injection.

    Server-tool blocks never need an answer (their results arrive in the
    same response), so only ``tool_use`` counts. Pure: the response is
    read, never changed. Shared by both engines as the one place the SHAPE
    is decided; the wording stays each engine's own.
    """
    content = getattr(response, "content", None)
    if content is None and isinstance(response, dict):
        content = response.get("content")
    results: list[dict[str, Any]] = []
    for block in content or []:
        if _block_field(block, "type") != "tool_use":
            continue
        name = _block_field(block, "name")
        results.append(
            {
                "type": "tool_result",
                "tool_use_id": _block_field(block, "id"),
                "is_error": True,
                "content": wrong_tool(name if isinstance(name, str) else ""),
            }
        )
    if results:
        return {"role": "user", "content": results}
    return {"role": "user", "content": [{"type": "text", "text": reminder}]}


# Preserved thinking (the 5.5 prompting upgrade, P55-6). Both 5.5 models bind
# each thinking block to the conversation prefix that produced it, and a
# request that replays a block after the harness EDITED an earlier part of
# that prefix is a 400 on an account created on or after 2026-08-31 — this
# app is bring-your-own-key, so the user's account age decides, not the
# owner's. The one edit the app makes mid-conversation is the resend
# sanitizer's (``sanitize_messages_for_resend``: a fetched PDF over the page
# limit, or an unpaired server-tool call), and the citation repair that can
# follow it in the chat. A request the harness edited asks the API to DROP
# the invalidated thinking blocks instead of failing; a request it did not
# edit carries none of this and is byte-identical to what it always was.
# Shared by the chat, research and streamed Final QC: small, pure helpers
# beside the extractor, the loops that call them stay each engine's own.
PRESERVED_THINKING_BETA = "thinking-binding-controls-2026-08-01"
_DROP_BLOCK = "drop_block"
_BETA_HEADER = "anthropic-beta"


def with_beta_header(extra_headers: Any, beta: str) -> dict[str, str]:
    """``extra_headers`` with ``beta`` merged into its ``anthropic-beta``.

    A new dict; the input is never mutated. An existing ``anthropic-beta``
    value (any spelling of the key's case) keeps its betas, comma-separated,
    and ``beta`` is appended once — so a second feature that needs its own
    beta on the same request (P55-7's fallback) merges rather than replaces.
    """
    headers: dict[str, str] = {}
    existing = ""
    for key, value in dict(extra_headers or {}).items():
        if isinstance(key, str) and key.lower() == _BETA_HEADER:
            existing = str(value or "")
            continue
        headers[key] = value
    betas = [part.strip() for part in existing.split(",") if part.strip()]
    if beta not in betas:
        betas.append(beta)
    headers[_BETA_HEADER] = ",".join(betas)
    return headers


def with_drop_block(request: dict[str, Any]) -> dict[str, Any]:
    """A copy of ``request`` that asks the API to drop invalidated thinking.

    ``thinking`` gains ``block_binding.prefix_mismatch_behavior: "drop_block"``
    (a new dict built from the one given — never the shared config a caller
    reuses for every request of a call) and ``extra_headers`` gains the
    preserved-thinking beta, merged with any beta already there. Sending
    ``block_binding`` without the beta is itself a 400, so the two always
    travel together. Every other key is the request as given.
    """
    sent = dict(request)
    thinking = dict(request.get("thinking") or {"type": "adaptive"})
    thinking["block_binding"] = {"prefix_mismatch_behavior": _DROP_BLOCK}
    sent["thinking"] = thinking
    sent["extra_headers"] = with_beta_header(
        request.get("extra_headers"), PRESERVED_THINKING_BETA
    )
    return sent


def input_transformation_counts(response: object) -> dict[str, int]:
    """How many entries of each ``type/reason`` a response's
    ``input_transformations`` holds — never a path, never any text.

    With the preserved-thinking beta every response carries the array
    (empty when nothing was dropped). Read defensively: a fake, an older SDK
    or a response without the beta has no such field, and the entries arrive
    as plain dicts on a GA response (the SDK keeps the unknown field) or as
    objects. Anything that is not a list of entries counts as nothing.
    """
    entries = getattr(response, "input_transformations", None)
    if entries is None and isinstance(response, dict):
        entries = response.get("input_transformations")
    if not isinstance(entries, (list, tuple)):
        return {}
    counts: dict[str, int] = {}
    for entry in entries:
        label = (
            f"{_transformation_token(_block_field(entry, 'type'))}/"
            f"{_transformation_token(_block_field(entry, 'reason'))}"
        )
        counts[label] = counts.get(label, 0) + 1
    return counts


_TRANSFORMATION_TOKEN = re.compile(r"[a-z][a-z0-9_]{0,63}")


def _transformation_token(value: Any) -> str:
    """A ``type`` or ``reason`` as it may be logged: the API's snake_case
    vocabulary (open — later checks add values), anything else ``other``."""
    if isinstance(value, str) and _TRANSFORMATION_TOKEN.fullmatch(value):
        return value
    return "other"


# The prompt-JSON fallback's bound. A real reply holds one tagged block,
# occasionally a draft followed by the final one; this caps the pairs of
# opening and closing tags one text is searched through, so a reply that
# somehow repeats a tag hundreds of times cannot cost a quadratic parse.
_TAGGED_JSON_MAX_ATTEMPTS = 64


def last_tagged_json_object(text: str, tag: str) -> dict[str, Any] | None:
    """The LAST complete ``<tag>{...}</tag>`` JSON object in ``text``.

    The tagged-JSON fallback every fan-out keeps for a text detour
    (``<research_json>``, ``<qc_json>``, ``<qc_verdict_json>``,
    ``<qc_consolidation_json>``, ``<compliance_json>``). The Sonnet 5.5
    prompting guide warns the model occasionally writes a draft before its
    final JSON, and to take neither everything from the first ``{`` to the
    last ``}`` nor the first block: the greedy pattern this replaces did
    the former, so a draft followed by the final answer parsed as nothing.

    Every opening tag is tried from the last to the first, each against the
    closing tags after it in order; the first candidate that is exactly one
    JSON object (whitespace aside) wins. So the final block wins over a
    draft, a final block that does not parse falls back to the draft that
    does, a draft left unclosed does not swallow the final block, and braces
    nested inside the JSON are fine. Returns ``None`` when nothing parses.
    """
    if not text or not tag:
        return None
    opening = f"<{tag}>"
    if opening not in text:
        return None
    closing = f"</{tag}>"
    starts = [m.end() for m in re.finditer(re.escape(opening), text)]
    ends = [m.start() for m in re.finditer(re.escape(closing), text)]
    attempts = 0
    for start in reversed(starts):
        for end in ends:
            if end < start:
                continue
            attempts += 1
            if attempts > _TAGGED_JSON_MAX_ATTEMPTS:
                return None
            candidate = text[start:end].strip()
            if not (candidate.startswith("{") and candidate.endswith("}")):
                continue
            try:
                value = json.loads(candidate)
            except (ValueError, RecursionError):
                continue
            if isinstance(value, dict):
                return value
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

# The model invokes both web tools DIRECTLY. Left unset, the ``_20260209``
# versions default to the code-execution caller ("dynamic filtering"), which
# runs a server-side code-execution container under the hood. That default
# cost this app three things, all of them observed in production:
#
# 1. Reliability. Resuming a ``pause_turn`` with a pending code-execution-
#    called tool use requires the response's provider container id on the
#    continuation request. Neither fan-out sent one, so a paused dimension
#    died on a nonretryable 400 (two dimensions lost in the reviewed run).
# 2. Visibility. A code-execution caller does not stream per-search
#    ``input_json_delta``, so the live query/URL labels the research board
#    and the QC Review Room render had nothing to show.
# 3. ZDR. Dynamic filtering is not zero-data-retention-eligible by default,
#    while the app's trust dossier claims every part of it is.
#
# Direct callers remove all three at the source. Re-enabling dynamic
# filtering is an owner decision, not a tuning pass: it must land together
# with container propagation on every continuation path AND a re-qualified
# ZDR claim in the same change.
#
# One-time cost of setting it: the tool bytes changed, so every previously
# cached prefix (tools render first, ahead of system and messages) stops
# matching once and is rewritten. Expected, per cache lineage.
WEB_TOOL_ALLOWED_CALLERS: tuple[str, ...] = ("direct",)


def build_web_search_tool(
    *, max_uses: int, user_location: dict | None = None
) -> dict[str, Any]:
    """The ``web_search_20260209`` server-tool dict.

    ``user_location`` comes from ``ProjectProfile.web_search_user_location``
    so every research search runs as the project's own locale — the whole
    point of the phase. ``allowed_callers`` pins direct invocation
    (:data:`WEB_TOOL_ALLOWED_CALLERS`).
    """
    tool: dict[str, Any] = {
        "type": "web_search_20260209",
        "name": "web_search",
        "allowed_callers": list(WEB_TOOL_ALLOWED_CALLERS),
        "blocked_domains": list(WEB_BLOCKED_DOMAINS),
        "max_uses": max_uses,
    }
    if user_location:
        tool["user_location"] = dict(user_location)
    return tool


def build_web_fetch_tool(
    *, max_uses: int, max_content_tokens: int = WEB_FETCH_MAX_CONTENT_TOKENS
) -> dict[str, Any]:
    """The ``web_fetch_20260209`` server-tool dict.

    Generally available — no ``anthropic-beta`` header (sending a retired
    beta value is rejected with HTTP 400). Citations enabled so cited URLs
    land in the grounding partition like search citations do.
    ``allowed_callers`` pins direct invocation
    (:data:`WEB_TOOL_ALLOWED_CALLERS`).
    ``max_content_tokens`` defaults to the research-sized page ceiling;
    focused callers can request a smaller page budget.
    """
    return {
        "type": "web_fetch_20260209",
        "name": "web_fetch",
        "allowed_callers": list(WEB_TOOL_ALLOWED_CALLERS),
        "blocked_domains": list(WEB_BLOCKED_DOMAINS),
        "max_uses": max_uses,
        "citations": {"enabled": True},
        "max_content_tokens": max_content_tokens,
    }
