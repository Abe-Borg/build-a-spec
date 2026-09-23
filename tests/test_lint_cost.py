"""The lint stays cheap on the documents that used to stall it.

``duplicate_provision`` compared every pair of sibling provisions with a
character-level ``SequenceMatcher(autojunk=False).ratio()``. Its numeric gate
skips a pair whose numbers differ, so long provisions WITHOUT numbers — a
memo or a narrative imported as one long article — paid that comparison for
every pair: forty paragraphs of about 1,200 characters took over twenty
seconds. The lint runs inside every document payload (``GET /api/doc`` builds
it holding the session guard, so the stop button and new chat turns waited
behind it), in every chat turn's PROJECT CONTEXT before the first frame, and
again for a doc-changing turn's ``lint`` event.

Two changes, pinned here without a clock:

* Exact upper bounds on the ratio (``linting._RatioBounds``) settle the pairs
  that cannot reach the bar, so the character comparison runs only where it
  could flag something. A bound can only ever SKIP a comparison that would
  have come out below the bar, so no finding moves — and every test below
  that compares findings holds them to the plain comparison as it stood
  (``_reference_duplicate_siblings``), not to a second opinion.
* The session remembers each committed version's report
  (``SessionState.document_lint``), so the payload, the readiness checklist,
  the chat turn and the ``lint`` event share one pass per version.
"""
from __future__ import annotations

import difflib
import random
import re
from difflib import SequenceMatcher
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend import sessions
from backend.app import create_app
from backend.llm import conversation
from backend.llm.conversation import SessionState
from backend.spec_doc import linting
from backend.spec_doc.model import SpecSection, iter_paragraphs
from backend.spec_modules import get_module
from tests.fakes import FakeClient, text_turn, tool_turn

_BAR = linting._DUPLICATE_RATIO

# ---------------------------------------------------------------------------
# Fixtures: number-free prose, and the comparison as it stood
# ---------------------------------------------------------------------------

_VOCABULARY = (
    "the contractor shall provide coordinate install verify maintain submit "
    "review approve all required materials equipment systems components "
    "accordance with applicable codes standards authority having jurisdiction "
    "owner architect engineer before during after construction work areas "
    "including piping valves fittings hangers supports insulation labeling "
    "testing commissioning documentation training warranty services where "
    "indicated specified shown drawings field conditions existing new "
    "manufacturer recommendations written instructions acceptable quality "
    "workmanship clear access service clearances adjacent trades schedule "
    "sequence protection storage handling delivery project site cleaning "
    "record documents operation maintenance manuals spare parts tools "
    "responsible for ensuring that each assembly is properly aligned secured "
    "and protected from damage until final acceptance by the owner"
).split()


def _memo_paragraph(rng: random.Random, target_chars: int) -> str:
    """A paragraph of spec-flavoured prose with no digits in it."""
    sentences: list[str] = []
    while len(" ".join(sentences)) < target_chars:
        words = [rng.choice(_VOCABULARY) for _ in range(rng.randint(10, 22))]
        sentence = " ".join(words)
        sentences.append(sentence[0].upper() + sentence[1:] + ".")
    return " ".join(sentences)


def _node(text: str, uid: str) -> SimpleNamespace:
    return SimpleNamespace(text=text, uid=uid)


def _reference_duplicate_siblings(siblings):
    """``_duplicate_siblings`` before the bounds: ``ratio()`` for every pair
    the numeric gate lets through. The oracle every finding is held to."""
    normalized = [linting._normalized_provision(p.text) for p in siblings]
    numerics = [linting._numeric_tokens(text) for text in normalized]
    matched: set[int] = set()
    for later in range(1, len(siblings)):
        if len(normalized[later]) < linting._DUPLICATE_MIN_CHARS:
            continue
        for earlier in range(later):
            if earlier in matched and later in matched:
                continue
            if len(normalized[earlier]) < linting._DUPLICATE_MIN_CHARS:
                continue
            if normalized[earlier] == normalized[later]:
                matched.add(later)
                yield siblings[earlier], siblings[later], True
                break
            if numerics[earlier] != numerics[later]:
                continue
            ratio = SequenceMatcher(
                None, normalized[earlier], normalized[later], autojunk=False
            ).ratio()
            if ratio >= _BAR:
                matched.add(later)
                yield siblings[earlier], siblings[later], False
                break


def _findings(fn, siblings) -> list[tuple[str, str, bool]]:
    return [(a.uid, b.uid, exact) for a, b, exact in fn(siblings)]


# ---------------------------------------------------------------------------
# The cost: long number-free siblings never reach the character comparison
# ---------------------------------------------------------------------------


def test_forty_long_number_free_siblings_never_reach_the_character_comparison(
    monkeypatch,
):
    """The pin, counted rather than timed.

    Forty ~1,150-character paragraphs with no numbers are 780 pairs the
    numeric gate cannot skip — the shape of a memo imported as one article,
    and 22 s of ``ratio()`` before the bounds. Every one of those pairs must
    now be settled before a ``SequenceMatcher`` is even built.
    """
    rng = random.Random(7)
    siblings = [_node(_memo_paragraph(rng, 1_150), f"p{i}") for i in range(40)]
    assert not any(ch.isdigit() for node in siblings for ch in node.text)
    assert min(len(node.text) for node in siblings) >= 1_150

    class Counted(SequenceMatcher):
        built = 0
        ratios = 0

        def __init__(self, *args, **kwargs):
            type(self).built += 1
            super().__init__(*args, **kwargs)

        def ratio(self):  # never worth computing: the pin wants none
            type(self).ratios += 1
            return 0.0

    monkeypatch.setattr(difflib, "SequenceMatcher", Counted)
    assert list(linting._duplicate_siblings(siblings)) == []
    assert (Counted.built, Counted.ratios) == (0, 0), (
        f"{Counted.built} SequenceMatcher(s) built and {Counted.ratios} "
        "ratio() call(s) for 780 pairs the bounds settle"
    )
    monkeypatch.undo()

    # The premise, so this test keeps exercising the bound that matters:
    # prose shares its character mix, so difflib's own quick bounds settle
    # none of these pairs — the order-aware one does.
    for earlier, later in zip(siblings, siblings[1:]):
        a = linting._normalized_provision(earlier.text)
        b = linting._normalized_provision(later.text)
        matcher = SequenceMatcher(None, a, b, autojunk=False)
        assert matcher.real_quick_ratio() >= _BAR
        assert matcher.quick_ratio() >= _BAR
    # And with the real comparison nothing here is a duplicate.
    assert list(linting._duplicate_siblings(siblings)) == []


# ---------------------------------------------------------------------------
# No finding moves: near-duplicates of every kind, around the bar
# ---------------------------------------------------------------------------

_BASES = (
    "Provide clear service access to each control valve assembly. Locate "
    "valves where they can be operated from the floor without a ladder. "
    "Coordinate access panels with the architectural ceiling layout.",
    "The contractor shall submit shop drawings for review before any "
    "fabrication begins. Include hanger locations, pipe sizes and the "
    "sequence of installation for each zone.",
    "Hydrostatically test new piping at 200 psi for 2 hours. Repair any "
    "leak found and repeat the test until the system holds pressure.",
    "Protect installed equipment from dust, moisture and construction "
    "damage until final acceptance by the owner. Replace any component "
    "damaged before acceptance at no cost to the owner.",
    "Maintain a minimum clearance of 18 inches below sprinkler deflectors "
    "in all storage areas, and post signs stating the clearance at each "
    "rack aisle.",
    "Label every valve, test connection and drain with an engraved tag "
    "fastened by a brass chain. Match the tag text to the record drawings "
    "and the valve schedule.",
)
_OTHERS = (
    "Comply with the manufacturer's written installation instructions.",
    "Verify field conditions before starting the work and report any "
    "discrepancy to the architect in writing.",
    "Coordinate the work with adjacent trades before installation.",
)
_SYNONYMS = {
    "provide": "furnish", "shall": "must", "submit": "deliver",
    "review": "approval", "each": "every", "protect": "shield",
    "install": "mount", "coordinate": "arrange", "locate": "place",
    "include": "show", "repair": "fix", "maintain": "keep", "post": "hang",
    "label": "tag", "match": "align", "before": "prior to",
    "damage": "harm", "all": "every", "test": "check", "system": "network",
}


def _sentences(text: str) -> list[str]:
    return [part for part in re.split(r"(?<=[.])\s+", text.strip()) if part]


def _reword(text: str, count: int, rng: random.Random) -> str:
    words = text.split()
    swappable = [i for i, w in enumerate(words) if w.lower().strip(".,") in _SYNONYMS]
    rng.shuffle(swappable)
    for i in swappable[:count]:
        core = words[i].lower().strip(".,")
        words[i] = words[i].lower().replace(core, _SYNONYMS[core])
    return " ".join(words)


def _typos(text: str, count: int, rng: random.Random) -> str:
    chars = list(text)
    for _ in range(count):
        i = rng.randrange(len(chars))
        op = rng.choice("sid")
        if op == "s":
            chars[i] = rng.choice("abcdefghijklmnopqrstuvwxyz")
        elif op == "i":
            chars.insert(i, rng.choice("abcdefghijklmnopqrstuvwxyz"))
        else:
            del chars[i]
    return "".join(chars)


def _change_a_number(text: str) -> str:
    return re.sub(r"\d", lambda m: str((int(m.group(0)) + 1) % 10), text, count=1)


def _variants(base: str, rng: random.Random):
    """Every kind of near-duplicate the rule exists to catch, graded so the
    pairs straddle the bar."""
    sentences = _sentences(base)
    yield "case and spacing only", "  ".join(base.upper().split())
    for count in (1, 2, 3, 4, 6, 8):
        yield f"reworded x{count}", _reword(base, count, rng)
    yield "reordered", " ".join(sentences[1:] + sentences[:1])
    head, _, tail = sentences[0].rstrip(".").partition(" ")
    yield "clause moved", " ".join([tail + " " + head.lower() + "."] + sentences[1:])
    yield "one sentence added", " ".join(sentences + [_OTHERS[0]])
    yield "one sentence dropped", " ".join(sentences[:-1])
    yield "a number changed", _change_a_number(base)
    yield "a number changed, reworded", _reword(_change_a_number(base), 1, rng)
    for count in (1, 3, 6, 10, 14, 18, 24, 32):
        yield f"typos x{count}", _typos(base, count, rng)
    yield "reworded x2, typos x6", _typos(_reword(base, 2, rng), 6, rng)


def _near_duplicate_groups():
    rng = random.Random(20260923)
    for base in _BASES:
        for kind, variant in _variants(base, rng):
            yield kind, [base, variant]
            yield kind, [_OTHERS[1], base, _OTHERS[2], variant, _OTHERS[0]]
            yield kind, [variant, base, _reword(base, 1, rng)]


def test_the_bounds_never_change_a_finding():
    """Held to the plain comparison, group by group, around the bar.

    Findings depend on order (each paragraph is reported against its FIRST
    match), so the groups mix a variant with its base, unrelated siblings
    and a second restatement, and the whole finding list must match.
    """
    groups = 0
    for kind, texts in _near_duplicate_groups():
        siblings = [_node(text, f"p{i}") for i, text in enumerate(texts)]
        expected = _findings(_reference_duplicate_siblings, siblings)
        assert _findings(linting._duplicate_siblings, siblings) == expected, kind
        groups += 1
    assert groups >= 300


def test_a_pair_that_reaches_the_bar_is_never_settled_by_a_bound():
    """Pair by pair, independent of order: a bound may only skip a
    comparison that comes out below the bar. The pairs are graded so that
    some land on each side of it, close by — otherwise this would prove
    nothing about the threshold."""
    rng = random.Random(7)
    just_below = at_or_just_above = 0
    for base in _BASES + tuple(_memo_paragraph(rng, 400) for _ in range(4)):
        for kind, variant in _variants(base, rng):
            a = linting._normalized_provision(base)
            b = linting._normalized_provision(variant)
            if a == b or linting._numeric_tokens(a) != linting._numeric_tokens(b):
                continue
            ratio = SequenceMatcher(None, a, b, autojunk=False).ratio()
            may_reach = linting._RatioBounds([a, b]).may_reach(0, 1)
            if ratio >= _BAR:
                assert may_reach, (kind, ratio)
            if 0.85 <= ratio < _BAR:
                just_below += 1
            elif _BAR <= ratio < 0.95:
                at_or_just_above += 1
    assert just_below >= 10 and at_or_just_above >= 10, (just_below, at_or_just_above)


# ---------------------------------------------------------------------------
# The bounds themselves
# ---------------------------------------------------------------------------


def test_the_match_count_needed_is_exactly_the_bar_difflib_applies():
    """``ratio()`` is ``2.0 * matches / total``: the count every bound is
    compared with must be the least one that float expression accepts."""
    for total in range(1, 20_001):
        need = linting._matches_needed(total)
        assert 2.0 * need / total >= _BAR, total
        assert need == 0 or 2.0 * (need - 1) / total < _BAR, total


def _lcs(a: str, b: str) -> int:
    previous = [0] * (len(b) + 1)
    for char in a:
        current = [0]
        for j, other in enumerate(b):
            current.append(
                previous[j] + 1 if char == other else max(previous[j + 1], current[j])
            )
        previous = current
    return previous[-1]


def test_the_subsequence_bound_is_never_low_and_exact_when_it_reaches():
    """Against a plain dynamic program, on small alphabets where the longest
    common subsequence and difflib's matching blocks diverge most — and on
    lengths either side of the rows where the early exit is checked."""
    rng = random.Random(3)
    for trial in range(3_000):
        alphabet = ("ab", "abcd ", "abcdefghij ")[trial % 3]
        short_len = rng.choice([0, 1, 15, 16, 17, 31, 32, 33, rng.randint(0, 90)])
        short = "".join(rng.choice(alphabet) for _ in range(short_len))
        long = "".join(rng.choice(alphabet) for _ in range(short_len + rng.randint(0, 30)))
        exact = _lcs(short, long)
        need = rng.randint(0, short_len + 2)
        bound = linting._lcs_upper_bound(
            short, linting._position_masks(long), len(long), need
        )
        assert bound >= exact
        assert (bound >= need) == (exact >= need)
        if bound >= need:
            assert bound == exact


class _CountingMasks(dict):
    """Position masks that count the rows the bound actually processed."""

    rows = 0

    def get(self, key, default=None):
        self.rows += 1
        return super().get(key, default)


def test_the_subsequence_bound_stops_once_the_rest_cannot_reach():
    rng = random.Random(11)
    short = linting._normalized_provision(_memo_paragraph(rng, 1_200))
    long = linting._normalized_provision(_memo_paragraph(rng, 1_250))
    need = linting._matches_needed(len(short) + len(long))
    masks = _CountingMasks(linting._position_masks(long))
    assert linting._lcs_upper_bound(short, masks, len(long), need) < need
    assert masks.rows < len(short) // 2, (masks.rows, len(short))


def test_difflibs_quick_bounds_are_the_ones_applied(monkeypatch):
    """With the subsequence bound out of the way, what is left settles a
    pair exactly when ``real_quick_ratio()`` or ``quick_ratio()`` does."""
    monkeypatch.setattr(linting, "_lcs_upper_bound", lambda *args: 10**9)
    rng = random.Random(5)
    for _ in range(2_000):
        a = "".join(rng.choice("abcdef ") for _ in range(rng.randint(1, 60)))
        b = "".join(rng.choice("abcdef ") for _ in range(rng.randint(1, 60)))
        matcher = SequenceMatcher(None, a, b, autojunk=False)
        expected = matcher.real_quick_ratio() >= _BAR and matcher.quick_ratio() >= _BAR
        assert linting._RatioBounds([a, b]).may_reach(0, 1) == expected, (a, b)


def test_the_cheap_bounds_run_before_the_expensive_ones(monkeypatch):
    """The length bound settles a pair before any character is counted, and
    the character counts settle one before the subsequence is computed."""

    def refuse(*_args, **_kwargs):
        raise AssertionError("reached a bound that should not have been needed")

    monkeypatch.setattr(linting._RatioBounds, "_count", refuse)
    assert linting._RatioBounds(["a" * 30, "a" * 100]).may_reach(0, 1) is False
    monkeypatch.undo()
    monkeypatch.setattr(linting, "_lcs_upper_bound", refuse)
    assert linting._RatioBounds(["a" * 60, "b" * 60]).may_reach(0, 1) is False


# ---------------------------------------------------------------------------
# One pass per committed version
# ---------------------------------------------------------------------------


def _count_lint_passes(monkeypatch) -> list[object]:
    passes: list[object] = []
    real = conversation.lint_document

    def counted(section, module, **kwargs):
        passes.append(section)
        return real(section, module, **kwargs)

    monkeypatch.setattr(conversation, "lint_document", counted)
    return passes


def _edit(client: TestClient, ops: list[dict]) -> None:
    response = client.post("/api/doc/edit", json={"ops": ops})
    assert response.status_code == 200, response.text


_SEED = [
    {"action": "replace", "target_id": "sec", "text": "FIRE PUMPS", "numbering": "21 30 00"},
    {"action": "add_article", "target_id": "pt1", "text": "SUMMARY"},
    {
        "action": "add_paragraph",
        "target_id": "pt1.a1",
        "text": "Provide clear service access to each control valve assembly.",
    },
]


def test_one_committed_version_is_linted_once_for_every_reader(monkeypatch):
    """The panel's payload, the readiness checklist and a chat turn's
    context all read one version's report; a new version is linted once."""
    passes = _count_lint_passes(monkeypatch)
    client = TestClient(create_app())
    _edit(client, _SEED)  # the edit's own payload lints the new version
    session = sessions.get_session()
    assert len(passes) == 1

    for _ in range(3):
        assert client.get("/api/doc").status_code == 200
    assert client.get("/api/readiness").status_code == 200
    conversation._turn_context_text(session)
    assert len(passes) == 1

    _edit(
        client,
        [
            {
                "action": "add_paragraph",
                "target_id": "pt1.a1",
                "text": "Provide clear service access to each control valve assembly.",
            }
        ],
    )
    assert len(passes) == 2
    payload = client.get("/api/doc").json()
    assert client.get("/api/readiness").status_code == 200
    conversation._turn_context_text(session)
    assert len(passes) == 2
    # The report served from the memo is the report: the restatement above
    # is a duplicate, and every reader sees it.
    assert [item["rule"] for item in payload["lint"]].count("duplicate_provision") == 1

    # Undo returns to a version whose tree is rebuilt as a new object: that
    # is a new key, linted once more, then remembered again.
    assert client.post("/api/doc/undo").status_code == 200
    assert client.get("/api/doc").status_code == 200
    assert len(passes) == 3


def test_a_doc_changing_turn_lints_its_new_version_once(monkeypatch):
    """The turn's ``lint`` event lints the version it committed; the panel's
    refresh and the readiness poll that follow read the same report."""
    passes = _count_lint_passes(monkeypatch)
    client = TestClient(create_app())
    _edit(client, _SEED)
    before = len(passes)
    fake = FakeClient(
        [
            tool_turn(
                ["Adding one."],
                {
                    "edits": [
                        {
                            "action": "add_paragraph",
                            "target_id": "pt1.a1",
                            "text": "Submit shop drawings before fabrication begins.",
                        }
                    ]
                },
            ),
            text_turn(["Done."]),
        ]
    )
    monkeypatch.setattr("backend.llm.conversation.get_client", lambda: fake)
    response = client.post("/api/chat", json={"message": "Add a submittal."})
    assert '"type": "lint"' in response.text
    assert '"type": "turn_complete"' in response.text
    assert client.get("/api/doc").status_code == 200
    assert client.get("/api/readiness").status_code == 200
    # The turn start read the remembered pre-turn report; only the new
    # version cost a pass.
    assert len(passes) - before == 1


def test_a_tree_a_turn_is_still_editing_is_linted_fresh_and_never_kept(
    monkeypatch,
):
    passes = _count_lint_passes(monkeypatch)
    session = SessionState()
    session.doc.begin_turn()
    session.doc.apply_edits(_SEED)
    session.doc.commit_turn()
    committed = session.document_lint(session.doc.doc)
    assert len(passes) == 1

    session.doc.begin_turn()
    session.doc.apply_edits(
        [{"action": "add_article", "target_id": "pt3", "text": "EXECUTION"}]
    )
    assert session.doc.provisional
    session.document_lint(session.doc.doc)
    session.document_lint(session.doc.doc)
    assert len(passes) == 3  # provisional: linted every time, never kept
    session.doc.rollback_turn()
    # The rollback rebuilt the pre-turn tree as a new object — a new key.
    assert session.document_lint(session.doc.doc) == committed
    assert len(passes) == 4
    session.document_lint(session.doc.doc)
    assert len(passes) == 4


def test_a_version_record_rewritten_in_place_is_relinted():
    """Why the tree is the key rather than the version record: the
    tutorial's detached practice copy rewrites its version records IN PLACE
    and then rebuilds the tree from them. The record keeps its identity; the
    tree is a new object, so the report follows the content."""
    session = SessionState()
    session.doc.begin_turn()
    session.doc.apply_edits(
        _SEED[:2]
        + [
            {
                "action": "add_paragraph",
                "target_id": "pt1.a1",
                "text": "Provide [INSERT RATING] control valves.",
            }
        ]
    )
    session.doc.commit_turn()
    assert "placeholder_marker" in {
        item["rule"] for item in session.document_lint(session.doc.doc)
    }

    record = session.doc.versions[session.doc.index]
    rewritten = SpecSection.from_dict(record)
    for _part, _article, paragraph, _depth, _ref in iter_paragraphs(rewritten):
        paragraph.text = "Provide rated control valves."
    record.clear()
    record.update(rewritten.to_dict())
    session.doc.doc = SpecSection.from_dict(record)

    assert "placeholder_marker" not in {
        item["rule"] for item in session.document_lint(session.doc.doc)
    }


def test_a_report_handed_out_is_a_copy(monkeypatch):
    session = SessionState()
    session.doc.begin_turn()
    session.doc.apply_edits(_SEED[1:])  # articles without a header: one finding
    session.doc.commit_turn()
    first = session.document_lint(session.doc.doc)
    assert first
    first[0]["message"] = "tampered"
    first.append({"rule": "invented"})
    again = session.document_lint(session.doc.doc)
    assert again != first
    assert all(item["rule"] != "invented" for item in again)
    assert all(item["message"] != "tampered" for item in again)


def test_each_callers_inputs_get_their_own_report_for_one_version(monkeypatch):
    """The payload passes the preserved header/footer lines; the ``lint``
    event does not. Both are remembered for the version, apart."""
    passes = _count_lint_passes(monkeypatch)
    session = SessionState()
    session.doc.begin_turn()
    session.doc.apply_edits(_SEED)
    session.doc.commit_turn()
    chrome = ("SECTION 23 05 48 - VIBRATION CONTROLS",)
    plain = session.document_lint(session.doc.doc)
    with_chrome = session.document_lint(session.doc.doc, preserved_chrome=chrome)
    assert len(passes) == 2
    assert "stale_document_identifier" not in {item["rule"] for item in plain}
    assert "stale_document_identifier" in {item["rule"] for item in with_chrome}
    assert session.document_lint(session.doc.doc) == plain
    assert session.document_lint(session.doc.doc, preserved_chrome=chrome) == with_chrome
    assert len(passes) == 2


def test_a_module_switch_is_a_new_key(monkeypatch):
    passes = _count_lint_passes(monkeypatch)
    session = SessionState()
    session.doc.begin_turn()
    session.doc.apply_edits(_SEED)
    session.doc.commit_turn()
    session.module = get_module("generic")
    session.document_lint(session.doc.doc)
    session.module = get_module("hyperscale_fire")
    session.document_lint(session.doc.doc)
    assert len(passes) == 2
