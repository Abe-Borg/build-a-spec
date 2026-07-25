"""One-request live canary for the production Final-QC verifier schema.

This is intentionally opt-in and bounded. It does not run a Final QC report;
it submits one low-token Fable verifier request with the production strict
tool schema so provider-side request-shape regressions are caught separately
from hermetic fakes.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend import settings  # noqa: E402
from backend.api_key_store import key_status  # noqa: E402
from backend.llm.client import MissingApiKeyError, get_client  # noqa: E402
from backend.qc.engine import (  # noqa: E402
    _VERDICT_JSON_TAG,
    _parse,
    _verifier_system_prompt,
    _verifier_user_message,
)
from backend.qc.schema import (  # noqa: E402
    QC_LENSES,
    normalize_verdict,
    submit_qc_verdict_tool,
)
from backend.spec_modules import DEFAULT_MODULE  # noqa: E402


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run",
        action="store_true",
        help="Make the single paid provider request.",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=2048,
        help="Output-token ceiling for the one request (default: 2048).",
    )
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    status = key_status()
    print(
        "Configured API key: "
        f"{'yes' if status.get('present') else 'no'} "
        f"(source: {status.get('source', 'none')})."
    )
    if not args.run:
        print("No request sent. Pass --run to execute the paid canary.")
        return 0
    if not 256 <= args.max_tokens <= 4096:
        print("--max-tokens must be between 256 and 4096.", file=sys.stderr)
        return 2

    try:
        client = get_client()
    except MissingApiKeyError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    lens = next(
        item
        for item in QC_LENSES
        if item.lens_id == "coordination_consistency"
    )
    finding = {
        "title": "Live verifier schema canary",
        "severity": "low",
        "element_id": "pt1.a1.p1",
        "issue": "The canary provision uses an unresolved placeholder.",
        "rationale": "A complete replacement can remove the placeholder.",
        "source_urls": [],
        "proposed_ops": [
            {
                "action": "replace",
                "target_id": "pt1.a1.p1",
                "text": "Provide the coordinated acceptance requirement.",
                "status": "confirmed",
            }
        ],
    }
    verdict_tool = submit_qc_verdict_tool(model=settings.QC_MODEL)
    verdict_tool["cache_control"] = {"type": "ephemeral"}
    request = {
        "model": settings.QC_MODEL,
        "max_tokens": args.max_tokens,
        "system": [
            {
                "type": "text",
                "text": _verifier_system_prompt(DEFAULT_MODULE),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [
            {
                "role": "user",
                "content": _verifier_user_message(
                    finding,
                    lens,
                    (
                        "SECTION 21 13 13 - WET-PIPE SPRINKLER SYSTEMS\n"
                        "[id: pt1.a1.p1] Provide acceptance criteria [TBD]."
                    ),
                ),
            }
        ],
        "tools": [verdict_tool],
        "thinking": {"type": "adaptive"},
        # Keep this diagnostic inexpensive; the strict verifier schema is the
        # production artifact under test, not the quality of this toy verdict.
        "output_config": {"effort": "low"},
    }
    try:
        with client.messages.stream(**request) as stream:
            response = stream.get_final_message()
    except Exception as exc:  # noqa: BLE001 - CLI reports provider diagnostics
        print(f"Live verifier canary failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    payload = _parse(
        [response], "submit_qc_verdict", _VERDICT_JSON_TAG
    )
    if payload is None:
        print(
            "Provider accepted the strict schema and returned a model response, "
            "but the response contained no parseable verifier verdict.",
            file=sys.stderr,
        )
        return 1
    verdict = normalize_verdict(payload, has_proposed_ops=True)
    print(
        "Live verifier canary passed: provider accepted the strict schema and "
        "returned the required verdict fields "
        f"(upholds={verdict['upholds']}, "
        f"ops_adequate={verdict['ops_adequate']})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
