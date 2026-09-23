"""What a change in the redline on your original rests on, as comment text
(Redline on your original, Phase 3).

A pure function over data the export captured under the session guard: the
current tree, the research profile, the attached documents' identities and
the durable QC fix record. It returns, per element uid, a
:class:`~backend.spec_doc.redline_comments.CommentBasis` — the comment
paragraphs for the element — and never decides WHICH changes get a comment
(the redline's own records do, in ``spec_doc/redline_comments.py``).

Three kinds of basis, in plain language, and never more than the record
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

``source_item_id`` is advisory and can name something the current research
no longer holds; such an element gets no research basis and is flagged
``unresolved_source`` for the export's counts.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

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
    item_id = str(item.item_id or "")
    if item.grounded:
        dated = f", researched {item.research_date}" if item.research_date else ""
        head = f"Basis: requirements research (item {item_id}{dated})"
    else:
        head = (
            f"Basis: a requirements research lead (item {item_id}) that was "
            "not verified against a retrieved source"
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


def _qc_paragraphs(entry: Mapping[str, Any]) -> tuple:
    applied = str(entry.get("applied_at") or "")[:10]
    when = f", applied {applied}" if applied else ""
    detail = ", ".join(
        part
        for part in (
            str(entry.get("severity") or "").strip(),
            str(entry.get("lens_title") or entry.get("lens_id") or "").strip(),
        )
        if part
    )
    title = trimmed(entry.get("title") or "", 200) or "(untitled finding)"
    head = f"Changed by a Final QC fix{when}: {title}"
    if detail:
        head += f" ({detail})"
    paragraphs: list[tuple] = [(head,)]
    if str(entry.get("issue") or "").strip():
        paragraphs.append((trimmed(entry["issue"]),))
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
) -> dict[str, CommentBasis]:
    """Per element uid, what its change rests on (see the module docstring).

    ``references`` are the attached documents' ``{rid, title, filename}``;
    ``fix_log`` the durable QC fix record. Neither is modified."""
    references_by_id = {
        str(document.get("rid") or ""): document
        for document in references
        if str(document.get("rid") or "")
    }
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
    for entry in latest.values():
        for uid in covered_uids(entry):
            if not entry_covers(entry, current, uid):
                continue
            targets = (uid, SECTION_TITLE_UID) if uid == "sec" else (uid,)
            for target in targets:
                qc.setdefault(target, []).extend(_qc_paragraphs(entry))

    bases: dict[str, CommentBasis] = {}
    for uid in set(research) | set(qc) | unresolved:
        bases[uid] = CommentBasis(
            qc=tuple(qc.get(uid, ())),
            research=research.get(uid, ()),
            unresolved_source=uid in unresolved,
        )
    return bases


__all__ = [
    "MAX_COMMENT_LINKS",
    "MAX_COMMENT_TEXT",
    "redline_comment_basis",
    "trimmed",
]
