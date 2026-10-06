"""Writing-policy evaluation: fixtures, the assessor, and an offline report.

The shared writing policy (backend/writing_policy.py) says where a
requirement goes and that it keeps its meaning when it moves. This tool
measures that against the reviewed cases in
tests/fixtures/writing_policy/placement_cases.json:

- ``assess`` scores one document against a case's reviewed expected
  outcome: each obligation present, in its PART, with all its terms; no
  anchor of the corrected text lost (a value with its unit, a tag, a
  designation); nothing invented; nothing that must stay absent; no
  placeholder or explanatory prose in the changed text. The hermetic tests
  run it on every case's seed (the "before"), its reviewed edit (the
  "after"), and its deliberately lossy edits, which it must catch.
- The default command prints an OFFLINE report and sends nothing: prompt
  sizes and policy hashes against the baseline recorded before the policy
  landed (tools/writing_policy_baseline.json), the Final QC system prompts,
  every case's before/after assessment, and the lint over the cases and the
  curated templates.

Model behaviour — whether a live drafting turn or Final QC lens actually
places, preserves and judges as the policy says, and what it costs in
tokens, cache reads and writes, and latency — needs paid requests. Only the
owner runs those, from an owner-run mode added on its own; no session does.
Until then the quality and runtime trade-offs of the policy are unmeasured,
and the report says so rather than estimating them.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend import writing_policy  # noqa: E402
from backend.llm import conversation  # noqa: E402
from backend.llm.prompts import render_system_prompt  # noqa: E402
from backend.qc import engine as qc_engine  # noqa: E402
from backend.spec_doc.linting import lint_document  # noqa: E402
from backend.spec_doc.model import SpecSection, apply_edits, iter_paragraphs  # noqa: E402
from backend.spec_doc.obligations import (  # noqa: E402
    _normalized,
    anchor_present,
    obligation_anchors,
)
from backend.spec_doc.spec_voice import (  # noqa: E402
    drafted_text_hits,
    explanatory_prose_hits,
)
from backend.spec_modules import AVAILABLE_MODULES, get_module  # noqa: E402

CASES_PATH = _ROOT / "tests" / "fixtures" / "writing_policy" / "placement_cases.json"
BASELINE_PATH = Path(__file__).resolve().with_name("writing_policy_baseline.json")
CURATED_TEMPLATES = _ROOT / "backend" / "templates" / "curated"

UNMEASURED = (
    "Live drafting and Final QC behaviour (instruction adherence, lost or "
    "invented obligations in model output, false-positive placement "
    "findings, safe-fix validity) and runtime cost (input tokens, cache "
    "reads and writes, latency) need paid requests. None was made: these "
    "remain unmeasured."
)


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    """One reviewed placement or preservation case."""

    raw: dict[str, Any]

    @property
    def case_id(self) -> str:
        return str(self.raw["id"])

    @property
    def focus(self) -> tuple[str, ...]:
        return tuple(self.raw.get("focus") or ())

    @property
    def expected(self) -> dict[str, Any]:
        return self.raw.get("expected") or {}


def load_cases(path: Path = CASES_PATH) -> list[Case]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("format") != 1:
        raise ValueError("Unknown placement-case format.")
    return [Case(raw) for raw in data["cases"]]


def apply_ops(section: SpecSection, ops: list[dict[str, Any]]) -> SpecSection:
    """``section`` after ``ops``; an empty list returns a copy unchanged."""
    if not ops:
        return copy.deepcopy(section)
    result, _applied = apply_edits(section, ops)
    return result


def seed_section(case: Case) -> SpecSection:
    """The case's starting document (its "before")."""
    return apply_ops(SpecSection.empty(), list(case.raw["seed"]))


def reviewed_section(case: Case) -> SpecSection:
    """The case's reviewed expected outcome (its "after")."""
    return apply_ops(seed_section(case), list(case.raw.get("reviewed_edit") or []))


def lossy_ops(case: Case, lossy: dict[str, Any]) -> list[dict[str, Any]]:
    """A lossy variant's operations: the reviewed edit, altered."""
    serialized = json.dumps(case.raw.get("reviewed_edit") or [], ensure_ascii=False)
    for old, new in (lossy.get("replace_in_reviewed") or {}).items():
        if old not in serialized:
            raise ValueError(f"{case.case_id}/{lossy['id']}: {old!r} not in reviewed_edit")
        serialized = serialized.replace(old, new)
    return json.loads(serialized) + list(lossy.get("append_ops") or [])


# ---------------------------------------------------------------------------
# The assessor
# ---------------------------------------------------------------------------


def _paragraph_texts(section: SpecSection) -> dict[str, tuple[int, str, str]]:
    """``{uid: (part number, ref, text)}`` for every provision."""
    return {
        paragraph.uid: (part.number, ref, paragraph.text)
        for part, _article, paragraph, _depth, ref in iter_paragraphs(section)
    }


def _document_haystack(section: SpecSection) -> str:
    texts = [
        article.title for part in section.parts for article in part.articles
    ] + [text for _part, _ref, text in _paragraph_texts(section).values()]
    return _normalized("\n".join(texts))


def _anchors(texts: list[str]) -> list[str]:
    return list(
        dict.fromkeys(anchor for text in texts for anchor in obligation_anchors(text))
    )


def assess(case: Case, before: SpecSection, after: SpecSection) -> dict[str, Any]:
    """Score ``after`` against the case's reviewed expected outcome.

    ``failures`` names each miss as ``obligation:<id>``, ``absent:<id>``,
    ``lost``, ``invented``, ``empty_parts`` or ``voice``; ``passed`` is true
    when there are none. ``before`` is the document the instruction was
    given against: lost anchors are read from its focus provisions (all of
    it when the case names none), invented ones are anchors ``after`` holds
    that ``before`` did not.
    """
    expected = case.expected
    before_paragraphs = _paragraph_texts(before)
    after_paragraphs = _paragraph_texts(after)
    failures: list[str] = []

    obligations = []
    for obligation in expected.get("obligations") or []:
        terms = [term.casefold() for term in obligation["all_of"]]
        holders = [
            (part, ref)
            for part, ref, text in after_paragraphs.values()
            if all(term in _normalized(text) for term in terms)
        ]
        in_part = [ref for part, ref in holders if part == obligation["part"]]
        obligations.append(
            {
                "id": obligation["id"],
                "part": obligation["part"],
                "found_at": [ref for _part, ref in holders],
                "ok": bool(in_part),
            }
        )
        if not in_part:
            failures.append(f"obligation:{obligation['id']}")

    absent = []
    for rule in expected.get("absent") or []:
        terms = [term.casefold() for term in rule["any_of"]]
        hits = [
            ref
            for part, ref, text in after_paragraphs.values()
            if part == rule["part"] and any(term in _normalized(text) for term in terms)
        ]
        absent.append({"id": rule["id"], "found_at": hits, "ok": not hits})
        if hits:
            failures.append(f"absent:{rule['id']}")

    focus_texts = [
        before_paragraphs[uid][2] for uid in case.focus if uid in before_paragraphs
    ] or [text for _part, _ref, text in before_paragraphs.values()]
    haystack = _document_haystack(after)
    lost = [anchor for anchor in _anchors(focus_texts) if not anchor_present(anchor, haystack)]
    if lost:
        failures.append("lost")

    before_haystack = _document_haystack(before)
    invented = [
        anchor
        for anchor in _anchors([text for _part, _ref, text in after_paragraphs.values()])
        if not anchor_present(anchor, before_haystack)
    ]
    if invented:
        failures.append("invented")

    nonempty = [
        part.number
        for part in after.parts
        if part.number in (expected.get("empty_parts") or []) and part.articles
    ]
    if nonempty:
        failures.append("empty_parts")

    before_texts = {text for _part, _ref, text in before_paragraphs.values()}
    voice = [
        {"ref": ref, "match": hit["match"]}
        for _part, ref, text in after_paragraphs.values()
        if text not in before_texts
        for hit in drafted_text_hits(text) + explanatory_prose_hits(text)
    ]
    if voice:
        failures.append("voice")

    return {
        "case": case.case_id,
        "passed": not failures,
        "failures": failures,
        "obligations": obligations,
        "absent": absent,
        "lost": lost,
        "invented": invented,
        "nonempty_parts": nonempty,
        "voice": voice,
    }


# ---------------------------------------------------------------------------
# Offline report
# ---------------------------------------------------------------------------


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _modules() -> list[Any]:
    return [get_module(module_id) for module_id in AVAILABLE_MODULES]


def prompt_measurements() -> dict[str, Any]:
    """Sizes and hashes of every prompt the policy rides, by module."""
    drafting = {}
    review = {}
    for module in _modules():
        prompt = render_system_prompt(module)
        drafting[module.module_id] = {
            "chars": len(prompt),
            "sha": _digest(prompt),
            "policy_copies": prompt.count(writing_policy.core_text()),
        }
        review[module.module_id] = {
            name: {
                "chars": len(render(module)),
                "sha": _digest(render(module)),
                "policy_copies": render(module).count(writing_policy.core_text()),
            }
            for name, render in (
                ("lens", qc_engine._lens_system_prompt),
                ("verifier", qc_engine._verifier_system_prompt),
                ("consolidation", qc_engine._consolidation_system_prompt),
            )
        }
    return {
        "policy": writing_policy.manifest_facts(),
        "policy_core_chars": len(writing_policy.core_text()),
        "drafting_block_chars": len(writing_policy.drafting_block()),
        "review_block_chars": len(writing_policy.review_block()),
        "drafting_system_prompt": drafting,
        "qc_system_prompts": review,
        "chat_tools_chars": len(
            json.dumps(conversation._chat_tools(), ensure_ascii=False)
        ),
    }


def _lint_counts(section: SpecSection, module: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for issue in lint_document(section, module):
        counts[issue["rule"]] = counts.get(issue["rule"], 0) + 1
    return counts


def fixture_measurements(cases: list[Case] | None = None) -> list[dict[str, Any]]:
    """Every case's before/after assessment, lossy catches and lint."""
    module = get_module(None)
    results = []
    for case in cases or load_cases():
        before = seed_section(case)
        after = reviewed_section(case)
        caught = []
        for lossy in case.raw.get("lossy_edits") or []:
            outcome = assess(case, before, apply_ops(before, lossy_ops(case, lossy)))
            caught.append(
                {
                    "id": lossy["id"],
                    "expected": lossy["expect"],
                    "caught": lossy["expect"] in outcome["failures"],
                }
            )
        results.append(
            {
                "case": case.case_id,
                "before": assess(case, before, before)["failures"],
                "after": assess(case, before, after)["failures"],
                "lossy": caught,
                "lint_after": _lint_counts(after, module),
            }
        )
    return results


def template_lint() -> dict[str, dict[str, int]]:
    """Lint over the curated templates: any hit there is a false positive."""
    results = {}
    for path in sorted(CURATED_TEMPLATES.glob("*.bastemplate")):
        data = json.loads(path.read_text(encoding="utf-8"))
        section = SpecSection.from_dict(data["document"])
        results[path.name] = _lint_counts(section, get_module(data.get("module_id")))
    return results


def offline_report(baseline_path: Path = BASELINE_PATH) -> dict[str, Any]:
    """Everything measurable without a request, beside the recorded baseline."""
    baseline = (
        json.loads(baseline_path.read_text(encoding="utf-8"))
        if baseline_path.exists()
        else None
    )
    current = prompt_measurements()
    deltas = {}
    if baseline:
        for module_id, now in current["drafting_system_prompt"].items():
            then = baseline["drafting_system_prompt"].get(module_id)
            if then:
                deltas[f"drafting:{module_id}"] = now["chars"] - then["chars"]
        for module_id, prompts in current["qc_system_prompts"].items():
            for name, now in prompts.items():
                then = baseline["qc_system_prompts"].get(module_id, {}).get(name)
                if then:
                    deltas[f"{name}:{module_id}"] = now["chars"] - then["chars"]
    return {
        "baseline": baseline,
        "current": current,
        "char_deltas": deltas,
        "fixtures": fixture_measurements(),
        "template_lint": template_lint(),
        "unmeasured": UNMEASURED,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--baseline",
        type=Path,
        default=BASELINE_PATH,
        help="Recorded pre-policy baseline (default: %(default)s).",
    )
    args = parser.parse_args(argv)
    print(json.dumps(offline_report(args.baseline), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
