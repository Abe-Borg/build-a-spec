"""Owner-run, three-request diagnostic for QC's thinking-prefix recovery.

Without --run, no client is built and no request is sent. With --run, mint a
genuine signed thinking block on QC_MODEL, replay it unchanged as a control,
then edit an earlier user message and replay with production with_drop_block.
The control must report no transformations; the edited replay must explicitly
report thinking_dropped/prefix_binding_mismatch. HTTP success alone is not a
pass. This tests streaming Messages recovery with a synthetic prefix edit,
not a live oversized-PDF fetch or the Batches API.

At most three requests, SDK retries disabled, mint output capped by
--max-tokens (2048 by default) and each replay capped at 256 tokens. Thinking,
signatures, response text, and provider error bodies are never printed or saved.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend import settings  # noqa: E402
from backend.llm.client import (  # noqa: E402
    MissingApiKeyError,
    bounded_request_options,
    get_client,
)
from backend.research.resend_sanitizer import _to_plain_block  # noqa: E402
from backend.research.schema import (  # noqa: E402
    input_transformation_counts,
    with_drop_block,
)

_ASK = "What is 17*23? Think it through, then answer with just the number."
_EDITED_ASK = "What is 17*23? Answer with just the number."
_FOLLOW_UP = "Reply with the single word OK."
_PREFIX_DROP = "thinking_dropped/prefix_binding_mismatch"
_REPLAY_MAX_TOKENS = 256


def _stream(client, request: dict):
    with client.messages.stream(**request) as stream:
        return stream.get_final_message()


def _replay_accepted(response) -> bool:
    # The diagnostic is decided on input, before output generation. A
    # bounded replay may hit max_tokens; neither replay is a QC verdict.
    return response.stop_reason in {"end_turn", "max_tokens"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Send up to three paid provider requests.")
    parser.add_argument("--max-tokens", type=int, default=2048, help="Mint output ceiling, 256–4096 (default: 2048).")
    args = parser.parse_args(argv)
    if not 256 <= args.max_tokens <= 4096:
        print("--max-tokens must be between 256 and 4096.", file=sys.stderr)
        return 2
    if not args.run:
        print("No request sent. Only the owner runs the paid check with --run (up to three requests).")
        return 0

    stage = "client setup"
    try:
        client = get_client().with_options(**bounded_request_options(60.0))
        mint_request = {
            "model": settings.QC_MODEL,
            "max_tokens": args.max_tokens,
            "thinking": {"type": "adaptive"},
            # Matches the provider's tiny binding probe: a short prompt at
            # default effort may return no thinking, making the test moot.
            "output_config": {"effort": "max"},
            "messages": [{"role": "user", "content": _ASK}],
        }
        stage = "mint"
        minted = _stream(client, mint_request)
        content = [_to_plain_block(block) for block in minted.content]
        if minted.stop_reason != "end_turn" or not any(
            isinstance(block, dict) and block.get("type") == "thinking" and block.get("signature")
            for block in content
        ):
            print("Binding canary inconclusive: mint did not finish with a signed thinking block.", file=sys.stderr)
            return 1
        expected_drops = sum(
            isinstance(block, dict) and block.get("type") in {"thinking", "redacted_thinking"}
            for block in content
        )

        control_request = with_drop_block({
            **mint_request,
            "max_tokens": _REPLAY_MAX_TOKENS,
            "messages": [
                mint_request["messages"][0],
                {"role": "assistant", "content": content},
                {"role": "user", "content": _FOLLOW_UP},
            ],
        })
        stage = "unchanged control"
        control = _stream(client, control_request)
        if not _replay_accepted(control) or getattr(control, "input_transformations", None) != []:
            print("Binding canary inconclusive: unchanged control did not report an empty input_transformations array.", file=sys.stderr)
            return 1

        edited_request = {
            **control_request,
            "messages": [{"role": "user", "content": _EDITED_ASK}, *control_request["messages"][1:]],
        }
        stage = "edited replay"
        edited = _stream(client, edited_request)
        counts = input_transformation_counts(edited)
        if not _replay_accepted(edited) or counts != {_PREFIX_DROP: expected_drops}:
            print("Binding canary inconclusive: edited replay did not report the expected thinking_dropped/prefix_binding_mismatch count.", file=sys.stderr)
            return 1
        print(
            "Thinking binding canary passed: unchanged control dropped nothing; "
            f"edited replay reported thinking_dropped/prefix_binding_mismatch={expected_drops} "
            f"(model={settings.QC_MODEL}). "
            "Live PDF fetching and batch enforcement remain unverified."
        )
        return 0
    except MissingApiKeyError:
        print("No API key configured. Set one in the app or ANTHROPIC_API_KEY before the owner-run check.", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - diagnostics must not dump provider bodies or signatures
        print(f"Binding canary failed during {stage}: {type(exc).__name__}.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
