"""What a change in the redline on your original rests on, as comment text
(Redline on your original, Phase 3).

A pure function over data the export captured under the session guard: the
current tree, the research profile, the attached documents' identities and
the durable QC fix record. It returns, per element uid, a
:class:`~backend.spec_doc.redline_comments.CommentBasis` — the comment
paragraphs for the element — and never decides WHICH changes get a comment
(the redline's own records do, in ``spec_doc/redline_comments.py``).

Four kinds of basis, in plain language, and never more than the record
says:

* **Research.** ``Paragraph.source_item_id`` naming an ``r-…`` item. A
  GROUNDED item cites the sources grounding accepted — the ones actually
  retrieved and matched; an ungrounded one is labelled a lead that was not
  verified against a retrieved source and lists what it CITED (the research
  trust model's ``[UNVERIFIED]`` rule, in words).
* **Attached document.** ``source_item_id`` naming a ``ref-…`` document: its
  title and file name. No link — it is a file on the user's machine.
* **Final QC fix.** A fix record entry (``backend/qc/fix_log.py``) that
  still covers the element (``entry_covers``): its title, severity and lens,
  the issue, and the finding's ACCEPTED sources.

* **The edit's reason.** ``SpecSection.edit_reasons[uid]`` — what the
  assistant said each edit of the element was for (every model edit carries
  one since 2026-10-07; a deletion's reason is kept under the deleted uid
  and every uid deleted with it). Spoken as ``Reason: …``, or a numbered
  list oldest first when the element was edited more than once. The header
  ("sec") speaks on the upload's header line too. A reason that only names
  a Final QC fix whose record already speaks for the element
  (``qc_fix_reason``) is not said twice.

The file may go to a client, so a comment says what a reader of the Word
file can use and nothing of Build-a-Spec's own bookkeeping (owner,
2026-10-08):

* **No workflow reasons.** A reason given for a status or source-link
  change — "user cleared assumed status after review" — explains nothing the
  redline shows, so it is left out: marked when recorded
  (``SpecSection.workflow_reasons``), and for a project edited before the
  mark existed, read off the version history (:func:`_workflow_flags` — a
  turn that recorded a reason on an element but changed none of its words,
  its place or its presence).
* **No app ids or bookkeeping dates.** The research heading names no item
  id and no research date, the QC heading no applied date, and every reason
  and QC text has the app's ids rewritten (:func:`reader_text`): an element
  id becomes its number, an attached document's id its title, and any other
  id (a research item, a fact, a finding, a follow-up) is dropped with its
  label — a parenthetical holding only ids and their dates goes whole.

``source_item_id`` is advisory and can name something the current research
no longer holds; such an element gets no research basis and is flagged
``unresolved_source`` for the export's counts.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .qc.apply import qc_fix_reason
from .qc.fix_log import covered_uids, entry_covers
from .spec_doc.redline_comments import CommentBasis, CommentLink, safe_link_url
from .spec_doc.source_format import SECTION_TITLE_UID

#: At most this many links under one heading, then "+N more".
MAX_COMMENT_LINKS = 5
#: Long text is trimmed to about this many characters, with "…".
MAX_COMMENT_TEXT = 500
_SPACE_RE = re.compile(r"\s+")


def trimmed(text: object, limit: int = MAX_COMMENT_TEXT) -> str:
    """``text`` with its whitespace folded, cut at a word near ``limit``
    characters and marked "…" when it had to be."""
    folded = _SPACE_RE.sub(" ", str(text or "")).strip()
    if len(folded) <= limit:
        return folded
    cut = folded[: limit - 1]
    space = cut.rfind(" ")
    if space > limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:.") + "…"


# ---------------------------------------------------------------------------
# Reader text: no app ids in a comment
# ---------------------------------------------------------------------------

#: An element id (``pt1``, ``pt1.a2``, ``pt1.a2.p3.p1``) and every record id
#: the app mints: a research item (``r-`` + 12 hex), a Final QC finding
#: (``qc-`` + 12 hex), an established fact (``pf-3``, ``pf-conflict1``), an
#: attached document (``ref-2``) and a follow-up (``fu-4``). Never inside a
#: word, a dotted name or a URL path.
_ID_RE = re.compile(
    r"(?<![\w./\-])(?:"
    r"(?P<element>pt[1-3](?:\.a\d+(?:\.p\d+)*)?)"
    r"|(?P<record>(?:r|qc)-[0-9a-f]{12}|pf-(?:conflict)?\d+|ref-\d+|fu-\d+)"
    r")(?![\w\-/])"
)
#: The research heading's own dating of an item, "researched 2026-10-07".
#: Only that verb: "applied on 2026-10-07" can be a fact of the work (a
#: coating, a test), so a date after it is the reader's (Codex review on
#: PR #302).
_BOOKKEEPING_DATE_RE = re.compile(
    r"\bresearched\s+(?:on\s+)?\d{4}-\d{2}-\d{2}(?:T[\d:.+\-Z]*)?",
    re.IGNORECASE,
)
#: Where a dropped id (or bookkeeping date) was, until the text is tidied.
_DROPPED = "\x00"
#: The words that only introduced a dropped id: its label ("item r-…",
#: "fact pf-3", "paragraph pt1.a9.p1"), then a connective left pointing at
#: nothing ("per ref-9", "and fact pf-3").
_LABEL_OF_DROPPED_RE = re.compile(
    r"\b(?:item|fact|reference|document|finding|follow-?up|element|id|"
    r"paragraph|provision|article)s?\s+(?=\x00)",
    re.IGNORECASE,
)
_CONNECTIVE_OF_DROPPED_RE = re.compile(
    r"\b(?:and|or|per|see|from|by|in|with|under|to|of|via|after|before|on)\s+(?=\x00)",
    re.IGNORECASE,
)
_PARENTHETICAL_RE = re.compile(r"\s*\(([^()]*)\)")
#: What a parenthetical may hold besides dropped ids and still be pure
#: bookkeeping — "(item r-…, researched 2026-10-07)". Checked only on a
#: parenthetical that held a dropped id.
_BOOKKEEPING_RE = re.compile(
    r"\x00|\d{4}-\d{2}-\d{2}(?:T[\d:.+\-Z]*)?"
    r"|\b(?:researched|applied|recorded|dated|see|per|and|on|research)\b"
    r"|[\s,;:&/]",
    re.IGNORECASE,
)
_EMPTY_PARENS_RE = re.compile(r"\s*\(\s*[,;:]?\s*\)")
_SPACE_BEFORE_PUNCTUATION_RE = re.compile(r"\s+([,;:.!?)])")
_SPACE_AFTER_PAREN_RE = re.compile(r"\(\s+")
_DANGLING_PUNCTUATION_RE = re.compile(r"[,;:]\s*(?=[,;:.!?)]|$)")
_WORD_RE = re.compile(r"[^\W_]")


def reader_text(
    text: object,
    *,
    numbers: Mapping[str, str] | None = None,
    titles: Mapping[str, str] | None = None,
) -> str:
    """``text`` as a reader of the Word file should get it: the app's ids
    rewritten and its own dating of a record dropped (owner, 2026-10-08 — no
    "(item r-ec2b37e839e6, researched 2026-10-07)" in a comment).

    An element id that ``numbers`` holds becomes its number ("1.2.C",
    "PART 2"); an attached document's id that ``titles`` holds becomes its
    title in quotes. Every other id is dropped with the word that only
    introduced it ("item", "fact" …), as is a "researched <date>"; a
    parenthetical left holding nothing but those and bookkeeping words goes
    whole, and the punctuation is tidied. Text with
    neither comes back as it was (only stripped); text that was nothing but
    ids comes back empty."""
    value = str(text or "").strip()
    if not (_ID_RE.search(value) or _BOOKKEEPING_DATE_RE.search(value)):
        return value
    numbers = numbers or {}
    titles = titles or {}

    def rewrite(match: re.Match) -> str:
        found = match.group(0)
        if match.group("element"):
            return numbers.get(found) or _DROPPED
        title = titles.get(found) if found.startswith("ref-") else None
        return f'"{title}"' if title else _DROPPED

    capital = value[:1].isupper()
    value = _BOOKKEEPING_DATE_RE.sub(_DROPPED, _ID_RE.sub(rewrite, value))
    value = _LABEL_OF_DROPPED_RE.sub("", value)
    value = _CONNECTIVE_OF_DROPPED_RE.sub("", value)

    def parenthetical(match: re.Match) -> str:
        inside = match.group(1)
        if _DROPPED in inside and not _BOOKKEEPING_RE.sub("", inside):
            return ""
        return match.group(0)

    value = _PARENTHETICAL_RE.sub(parenthetical, value).replace(_DROPPED, "")
    value = _SPACE_RE.sub(" ", value)
    value = _EMPTY_PARENS_RE.sub("", value)
    value = _SPACE_AFTER_PAREN_RE.sub("(", value)
    value = _SPACE_BEFORE_PUNCTUATION_RE.sub(r"\1", value)
    value = _DANGLING_PUNCTUATION_RE.sub("", value)
    value = _SPACE_RE.sub(" ", value).strip().lstrip(",;:. ").strip()
    if capital:
        value = value[:1].upper() + value[1:]
    return value if _WORD_RE.search(value) else ""


class _Numbers(Mapping[str, str]):
    """Element numbers for one export: the current tree's, and — only when a
    text names an element the tree no longer holds — the number it last had
    in the version history, so "Removed pt1.a1.p1" on a deleted provision
    reads "Removed 1.1.A", not "Removed" (Codex review on PR #302). The
    history is walked once, on the first such miss."""

    def __init__(
        self, current: Mapping[str, Any], history: Sequence[Mapping[str, Any]] = ()
    ) -> None:
        self._current = _element_numbers(current)
        self._history = history
        self._former: dict[str, str] | None = None

    def _lookup(self, uid: str) -> str | None:
        if uid in self._current:
            return self._current[uid]
        if self._former is None:
            self._former = {}
            for snapshot in self._history:
                if isinstance(snapshot, Mapping):
                    try:
                        self._former.update(_element_numbers(snapshot))
                    except (AttributeError, TypeError):
                        continue  # a malformed version names nothing
        return self._former.get(uid)

    def __getitem__(self, uid: str) -> str:
        number = self._lookup(uid)
        if number is None:
            raise KeyError(uid)
        return number

    def __iter__(self):
        return iter(self._current)

    def __len__(self) -> int:
        return len(self._current)


@dataclass(frozen=True)
class _Reader:
    """:func:`reader_text` bound to one export's numbers and titles."""

    numbers: Mapping[str, str] = field(default_factory=dict)
    titles: Mapping[str, str] = field(default_factory=dict)

    def text(self, value: object) -> str:
        return reader_text(value, numbers=self.numbers, titles=self.titles)


def _element_numbers(snapshot: Mapping[str, Any]) -> dict[str, str]:
    """Per element uid of a serialized tree, the number a reader sees:
    "PART 1", "1.2", "1.2.C", "1.2.C.1". A preserved block carries no label
    and so no number."""
    numbers: dict[str, str] = {}

    def walk(nodes: Any, prefix: str) -> None:
        for node in nodes or ():
            label = str(node.get("label") or "").rstrip(".)")
            if label:
                number = f"{prefix}.{label}"
                numbers[str(node.get("id") or "")] = number
                walk(node.get("children"), number)

    for part in snapshot.get("parts") or ():
        numbers[str(part.get("id") or "")] = f"PART {part.get('number')}"
        for article in part.get("articles") or ():
            number = str(article.get("number") or "")
            if number:
                numbers[str(article.get("id") or "")] = number
                walk(article.get("paragraphs"), number)
    numbers.pop("", None)
    return numbers


# ---------------------------------------------------------------------------
# Workflow reasons in a project edited before they were marked
# ---------------------------------------------------------------------------


def _snapshot_elements(
    snapshot: Any,
) -> tuple[dict[str, tuple[str, str]], dict[str, list[str]]]:
    """``(elements, children)`` of a saved version (``SpecSection.to_dict``
    shape): per element uid, its parent's uid and the words the redline
    shows of it (a provision's text, an article's title, the header's number
    and title under "sec"); per parent, its children in order. Read
    defensively — anything malformed simply holds no elements, which reads
    as a visible change, the side that keeps a reason said."""
    elements: dict[str, tuple[str, str]] = {}
    children: dict[str, list[str]] = {}
    if not isinstance(snapshot, Mapping):
        return elements, children
    header = snapshot.get("section")
    if isinstance(header, Mapping):
        elements["sec"] = ("", f"{header.get('number', '')}\n{header.get('title', '')}")

    def walk(parent: str, nodes: Any, words: str, below: str) -> None:
        order = children.setdefault(parent, [])
        for node in nodes if isinstance(nodes, list) else ():
            if not isinstance(node, Mapping) or not str(node.get("id") or ""):
                continue
            uid = str(node["id"])
            order.append(uid)
            elements[uid] = (parent, str(node.get(words, "")))
            walk(uid, node.get(below), "text", "children")

    for part in snapshot.get("parts") or ():
        if isinstance(part, Mapping):
            walk(str(part.get("id") or ""), part.get("articles"), "title", "paragraphs")
    return elements, children


def _changed_visibly(uid: str, before: tuple, after: tuple) -> bool:
    """Whether a step changed what the redline shows of ``uid``: its
    presence, its words, or its place — its own order, or an enclosing
    article's, among the siblings both versions hold. A status or a source
    link is none of these."""
    elements_before, children_before = before
    elements_after, children_after = after
    if uid not in elements_before or uid not in elements_after:
        return True
    if elements_before[uid] != elements_after[uid]:
        return True
    node = uid
    while node in elements_before and node in elements_after:
        parent = elements_after[node][0]
        if not parent:
            break  # the header has no siblings
        common = set(children_before.get(parent, ())) & set(children_after.get(parent, ()))
        order_before = [child for child in children_before.get(parent, ()) if child in common]
        order_after = [child for child in children_after.get(parent, ()) if child in common]
        if node not in common or order_before.index(node) != order_after.index(node):
            return True
        node = parent
    return False


def _trails(snapshot: Any) -> dict[str, list[str]]:
    raw = snapshot.get("edit_reasons") if isinstance(snapshot, Mapping) else None
    if not isinstance(raw, Mapping):
        return {}
    return {
        str(uid): [str(reason) for reason in trail]
        for uid, trail in raw.items()
        if isinstance(trail, list)
    }


def _overlap(before: Sequence[str], after: Sequence[str]) -> int:
    """How many of ``after``'s first entries are ``before``'s last ones —
    what one step kept of an element's trail (a step appends at the end and
    drops the oldest past the cap)."""
    for size in range(min(len(before), len(after)), 0, -1):
        if list(before[-size:]) == list(after[:size]):
            return size
    return 0


def _workflow_flags(
    history: Sequence[Mapping[str, Any]], current: Mapping[str, Any]
) -> dict[str, list[bool]]:
    """Per element uid, one flag per entry of its trail in ``current``: True
    where the step that recorded the entry changed nothing the redline shows
    of the element — a status or source-link edit, or one that left the
    element as it was.

    ``history`` is the store's versions up to the current one, oldest first;
    each committed turn is one step. A step that changed the element's words
    or place AND recorded more than one reason on it cannot say which reason
    was which, so all of them stay said; an entry already on the first
    version is said too. A step that changed the element's words (or
    deleted it) but left its trail as it was gave its newest reason again (a
    repeat is not appended; a hand edit would have cleared the trail), so
    that entry is said from then on — the shown edit wins, as the stored
    mark has it (Codex review on PR #302). Only words or presence count
    there: a sibling's move shifts the element's order too. This is how a project edited before
    ``workflow_reasons`` existed (1.25.0–1.26.0) keeps its status reasons out
    of the comments; for a newer edit the mark is exact and this agrees."""
    snapshots = [*history, current]
    trails_before = _trails(snapshots[0])
    flags = {uid: [False] * len(trail) for uid, trail in trails_before.items()}
    cached: list[Any] = [None, None]

    def shape(index: int) -> tuple:
        if cached[0] != index:
            cached[0], cached[1] = index, _snapshot_elements(snapshots[index])
        return cached[1]

    for index in range(1, len(snapshots)):
        trails = _trails(snapshots[index])
        for uid in set(trails_before) - set(trails):
            flags.pop(uid, None)
        changed = [uid for uid, trail in trails.items() if trail != trails_before.get(uid)]
        repeated = [
            uid
            for uid, trail in trails.items()
            if trail and trail == trails_before.get(uid) and (flags.get(uid) or [False])[-1]
        ]
        if not (repeated or changed):
            trails_before = trails
            continue
        before = shape(index - 1)
        after = shape(index)
        for uid in repeated:
            # Words or presence only: a sibling's move shifts this element's
            # order too, and must not turn its status reason back on.
            if before[0].get(uid) != after[0].get(uid):
                flags[uid][-1] = False
        if changed:
            for uid in changed:
                old, new = trails_before.get(uid, []), trails[uid]
                kept = _overlap(old, new)
                prior = flags.get(uid, [])
                if len(prior) != len(old):
                    prior = [False] * len(old)
                workflow = not _changed_visibly(uid, before, after)
                flags[uid] = (prior[len(prior) - kept :] if kept else []) + [
                    workflow
                ] * (len(new) - kept)
        trails_before = trails
    return flags


def _link_paragraphs(heading: str, sources: Iterable[tuple[str, str]]) -> tuple:
    """``heading`` then one paragraph per source — a link when it is http(s),
    plain text otherwise — at most :data:`MAX_COMMENT_LINKS`, then "+N more"."""
    unique: list[tuple[str, str]] = []
    seen: set[str] = set()
    for url, label in sources:
        url = str(url or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        unique.append((url, trimmed(label or url, 200) or url))
    if not unique:
        return ()
    paragraphs: list[tuple] = [(heading,)]
    for url, label in unique[:MAX_COMMENT_LINKS]:
        if safe_link_url(url) is not None:
            paragraphs.append((CommentLink(url=url, label=label),))
        else:
            paragraphs.append((trimmed(url, 200),))
    if len(unique) > MAX_COMMENT_LINKS:
        paragraphs.append((f"+{len(unique) - MAX_COMMENT_LINKS} more",))
    return tuple(paragraphs)


def _research_paragraphs(item) -> tuple:
    # No item id, no research date: both are Build-a-Spec's bookkeeping,
    # meaningless to a reader of the Word file (owner, 2026-10-08).
    if item.grounded:
        head = "Basis: requirements research"
    else:
        head = (
            "Basis: a requirements research lead that was not verified "
            "against a retrieved source"
        )
    paragraphs: list[tuple] = [(head,)]
    if str(item.requirement or "").strip():
        paragraphs.append((trimmed(item.requirement),))
    if str(item.authority or "").strip():
        paragraphs.append((f"Authority: {trimmed(item.authority, 200)}",))
    if str(item.code_reference or "").strip():
        paragraphs.append((f"Code reference: {trimmed(item.code_reference, 200)}",))
    if item.grounded:
        paragraphs.extend(
            _link_paragraphs("Sources:", ((url, url) for url in item.accepted_sources))
        )
    else:
        paragraphs.extend(
            _link_paragraphs(
                "Cited (not verified):", ((url, url) for url in item.source_urls)
            )
        )
    return tuple(paragraphs)


def _reference_paragraphs(document: Mapping[str, Any]) -> tuple:
    title = trimmed(document.get("title") or "", 200)
    filename = trimmed(document.get("filename") or "", 200)
    named = f'"{title}"' if title else "(untitled)"
    tail = f" ({filename})" if filename and filename != title else ""
    return ((f"Basis: attached document {named}{tail}.",),)


def _qc_paragraphs(entry: Mapping[str, Any], reader: "_Reader") -> tuple:
    # No applied date and no lens id: bookkeeping, not the finding.
    lens = str(entry.get("lens_title") or "").strip() or (
        str(entry.get("lens_id") or "").replace("_", " ").strip()
    )
    detail = ", ".join(
        part for part in (str(entry.get("severity") or "").strip(), lens) if part
    )
    title = trimmed(reader.text(entry.get("title") or ""), 200) or "(untitled finding)"
    head = f"Changed by a Final QC fix: {title}"
    if detail:
        head += f" ({detail})"
    paragraphs: list[tuple] = [(head,)]
    issue = reader.text(entry.get("issue") or "")
    if issue:
        paragraphs.append((trimmed(issue),))
    paragraphs.extend(
        _link_paragraphs(
            "Sources:",
            (
                (source.get("url", ""), source.get("title") or source.get("url", ""))
                for source in entry.get("sources") or ()
                if isinstance(source, Mapping)
            ),
        )
    )
    return tuple(paragraphs)


#: A reason is already cut near 240 characters when recorded; this is only a
#: guard against a hand-edited project file.
MAX_REASON_TEXT = 300


def _reason_paragraphs(trail: Sequence[str], reader: "_Reader") -> tuple:
    """An element's recorded reasons, oldest first: one ``Reason:`` paragraph
    when there is one, else a heading and one numbered paragraph each. A
    reason that is nothing but ids says nothing to a reader and is left
    out."""
    reasons = [
        trimmed(text, MAX_REASON_TEXT)
        for reason in trail
        if (text := reader.text(reason))
    ]
    if not reasons:
        return ()
    if len(reasons) == 1:
        return ((f"Reason: {reasons[0]}",),)
    return (
        ("Reasons, oldest first:",),
        *((f"{index}. {reason}",) for index, reason in enumerate(reasons, 1)),
    )


def _paragraph_links(section) -> Iterable[tuple[str, str]]:
    """``(uid, source_item_id)`` for every provision that names a source."""

    def walk(nodes):
        for node in nodes:
            if str(node.source_item_id or "").strip():
                yield node.uid, str(node.source_item_id).strip()
            yield from walk(node.children)

    for part in section.parts:
        for article in part.articles:
            yield from walk(article.paragraphs)


def redline_comment_basis(
    current,
    *,
    profile=None,
    references: Sequence[Mapping[str, Any]] = (),
    fix_log: Sequence[Mapping[str, Any]] = (),
    history: Sequence[Mapping[str, Any]] = (),
) -> dict[str, CommentBasis]:
    """Per element uid, what its change rests on (see the module docstring).

    ``references`` are the attached documents' ``{rid, title, filename}``;
    ``fix_log`` the durable QC fix record; ``history`` the document store's
    versions up to the current one, oldest first (what lets a project edited
    before ``workflow_reasons`` existed keep its status reasons out). None
    is modified."""
    references_by_id = {
        str(document.get("rid") or ""): document
        for document in references
        if str(document.get("rid") or "")
    }
    snapshot = current.to_dict()
    reader = _Reader(
        numbers=_Numbers(snapshot, history),
        titles={
            rid: trimmed(document.get("title") or document.get("filename") or "", 200)
            for rid, document in references_by_id.items()
        },
    )
    research: dict[str, tuple] = {}
    unresolved: set[str] = set()
    for uid, source_id in _paragraph_links(current):
        if source_id.startswith("ref-"):
            document = references_by_id.get(source_id)
            if document is not None:
                research[uid] = _reference_paragraphs(document)
                continue
        else:
            item = profile.item(source_id) if profile is not None else None
            if item is not None:
                research[uid] = _research_paragraphs(item)
                continue
        unresolved.add(uid)

    # The newest entry for a finding wins; each covering finding once.
    latest: dict[str, Mapping[str, Any]] = {}
    for entry in fix_log:
        finding_id = str(entry.get("finding_id") or "")
        if finding_id:
            latest.pop(finding_id, None)
            latest[finding_id] = entry
    qc: dict[str, list[tuple]] = {}
    # Per target, how each covering fix reads as an edit reason — so the
    # trail's "Final QC fix: <title>" is not said beside the record of the
    # very fix that wrote it.
    spoken_fixes: dict[str, set[str]] = {}
    for entry in latest.values():
        for uid in covered_uids(entry):
            if not entry_covers(entry, current, uid):
                continue
            targets = (uid, SECTION_TITLE_UID) if uid == "sec" else (uid,)
            for target in targets:
                qc.setdefault(target, []).extend(_qc_paragraphs(entry, reader))
                spoken_fixes.setdefault(target, set()).add(
                    qc_fix_reason(str(entry.get("title") or ""))
                )

    # The reasons the assistant gave, less those for a workflow edit (a
    # status or source link — marked when recorded, read off the history for
    # an older project): the header's speak on the upload's header line as
    # well, the QC shape.
    derived = _workflow_flags(history, snapshot) if history else {}
    marked = getattr(current, "workflow_reasons", None) or {}
    reasons: dict[str, tuple] = {}
    for uid, trail in (getattr(current, "edit_reasons", None) or {}).items():
        if not isinstance(trail, list):
            continue
        flags = derived.get(uid) or ()
        if len(flags) != len(trail):
            flags = [False] * len(trail)
        workflow = set(marked.get(uid) or ())
        said = [
            reason
            for reason, flagged in zip(trail, flags)
            if not flagged and reason not in workflow
        ]
        targets = (uid, SECTION_TITLE_UID) if uid == "sec" else (uid,)
        for target in targets:
            kept = [
                reason for reason in said if reason not in spoken_fixes.get(target, ())
            ]
            paragraphs = _reason_paragraphs(kept, reader)
            if paragraphs:
                reasons[target] = paragraphs

    bases: dict[str, CommentBasis] = {}
    for uid in set(research) | set(qc) | unresolved | set(reasons):
        bases[uid] = CommentBasis(
            qc=tuple(qc.get(uid, ())),
            research=research.get(uid, ()),
            reasons=reasons.get(uid, ()),
            unresolved_source=uid in unresolved,
        )
    return bases


__all__ = [
    "MAX_COMMENT_LINKS",
    "MAX_COMMENT_TEXT",
    "MAX_REASON_TEXT",
    "reader_text",
    "redline_comment_basis",
    "trimmed",
]
