"""Reference documents: files the model reads *from*, never edits.

The app has exactly one document — the spec section being authored — and
importing a ``.docx`` replaces it. That left no way to say "here is the
owner's design standard / my basis-of-design narrative / this cut sheet;
draft from it", so the only route for supporting material was to import it
as the document, which is what made a memo come back dressed as a spec.

A reference document is the other thing entirely: text attached to the
session as *context*. It never enters the SectionFormat tree, never affects
lint, diff, QC, readiness, or export, and cannot be edited. The model reads
it through the ``read_reference_doc`` tool and drafts provisions from what it
learns, in its own words, through ``apply_spec_edits``.

Token posture (mirrors the fetched-PDF and figure-source policies)
------------------------------------------------------------------
Reference text is large by nature and this app re-bills the whole PROJECT
CONTEXT block every turn, so the body must never live there. The per-turn
context carries a one-line stub per document (id, title, size) — enough for
the model to know what is available and decide to open it. The body arrives
only as a tool result, inside the turn that asked for it, and is elided from
committed history exactly like a fetched PDF: leaving it would re-bill it on
every later turn and balloon the project file. Re-reading is cheap to
express and the stub always advertises it.

Persistence
-----------
Reference documents are user-supplied content, so they ride the project file
and count toward the unsaved-work save gate. Unlike figures they are not
turn-atomic: they are uploaded through REST by the user, not authored by the
model mid-turn, so there is no begin/commit/rollback to do.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .reference_extract import REFERENCE_KIND_LABELS

# Anti-abuse bounds. The stored text is what a tool result can return, so
# this caps one model-visible payload rather than the upload itself (every
# supported type is separately bounded by the upload read limit).
MAX_REFERENCE_DOCS = 20
MAX_TEXT_CHARS = 400_000
MAX_TITLE = 200
_EXCERPT_CHARS = 280

# Cumulative ceiling on reference text returned within ONE turn, across every
# read_reference_doc call in it. Per-document caps are not enough on their own:
# commit-time elision only trims *committed history*, so every body read this
# turn stays in the continuation request until the turn ends. Twenty maximal
# attachments read in one turn would be ~8M characters ahead of the document
# context, blowing the model's window and failing the turn on an opaque API
# error instead of something the model can act on.
#
# A runaway circuit breaker, not a quality limit (the MAX_TOOL_ROUNDS posture):
# sized so no legitimate turn meets it — one full-size document plus most of a
# second — and reported to the model as a correctable tool error so it can
# work with what it already read.
MAX_TURN_TEXT_CHARS = 600_000

# Appended to the stored text when a document exceeds MAX_TEXT_CHARS. The
# truncation is also flagged structurally (``truncated``) and surfaced in the
# stub and the tool result — silent loss would be the one unacceptable
# outcome for material the user expects the model to have read.
TRUNCATION_MARKER = (
    "\n\n[... reference document truncated at {kept:,} of {total:,} "
    "characters. The remainder was not stored and has NOT been read. Tell "
    "the user if the answer may depend on the omitted tail.]"
)


class ReferenceDocError(ValueError):
    """A rejected reference-document operation."""


class TurnReferenceBudget:
    """How much reference text one turn has already pulled into its request.

    Turn-scoped and single-threaded (the tool loop dispatches serially), so it
    needs no locking. Created fresh per turn and discarded with it: the ceiling
    is about one request's size, not a session-long allowance.
    """

    def __init__(self, limit: int = MAX_TURN_TEXT_CHARS) -> None:
        self.limit = limit
        self.spent = 0

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.spent)

    def take(self, amount: int) -> bool:
        """Charge ``amount`` to the turn; False when it would overrun."""
        if self.spent + amount > self.limit:
            return False
        self.spent += amount
        return True


@dataclass
class ReferenceDoc:
    """One attached reference document."""

    rid: str
    filename: str
    title: str
    text: str
    # Length of the extracted text BEFORE truncation, so the record stays
    # honest about what the stored copy leaves out.
    char_count: int
    block_count: int
    truncated: bool = False
    tracked_changes: bool = False
    added_at: str = ""
    # Which extractor produced the text (``reference_extract.REFERENCE_KINDS``).
    # Defaults to Word: it was the only supported type when the store shipped,
    # so a project file without the field can only hold Word attachments.
    kind: str = "docx"

    def kind_label(self) -> str:
        return REFERENCE_KIND_LABELS.get(self.kind, self.kind or "file")

    def excerpt(self) -> str:
        head = self.text[:_EXCERPT_CHARS].strip()
        return head + "…" if len(self.text) > _EXCERPT_CHARS else head

    def to_dict(self) -> dict[str, Any]:
        return {
            "rid": self.rid,
            "filename": self.filename,
            "title": self.title,
            "text": self.text,
            "char_count": self.char_count,
            "block_count": self.block_count,
            "truncated": self.truncated,
            "tracked_changes": self.tracked_changes,
            "added_at": self.added_at,
            "kind": self.kind,
        }

    def metadata(self) -> dict[str, Any]:
        """The frontend view: everything except the body.

        ``_doc_payload`` carries this on every document response, so the text
        must not ride along — the list would then cost a full copy of every
        attached document on each poll.
        """
        return {
            "rid": self.rid,
            "filename": self.filename,
            "title": self.title,
            "char_count": self.char_count,
            "block_count": self.block_count,
            "truncated": self.truncated,
            "tracked_changes": self.tracked_changes,
            "added_at": self.added_at,
            "kind": self.kind,
            "kind_label": self.kind_label(),
            "excerpt": self.excerpt(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReferenceDoc":
        rid = str(data["rid"])
        text = str(data.get("text", ""))
        return cls(
            rid=rid,
            filename=str(data.get("filename", "")),
            title=str(data.get("title", "")) or rid,
            text=text,
            char_count=int(data.get("char_count", len(text)) or len(text)),
            block_count=int(data.get("block_count", 0) or 0),
            truncated=bool(data.get("truncated", False)),
            tracked_changes=bool(data.get("tracked_changes", False)),
            added_at=str(data.get("added_at", "")),
            kind=str(data.get("kind", "") or "docx"),
        )


class ReferenceDocStore:
    """Session-level list of attached reference documents."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.docs: list[ReferenceDoc] = []
        self._next_seq = 1

    # -- mutation -----------------------------------------------------------

    def add(
        self,
        *,
        filename: str,
        text: str,
        block_count: int,
        title: str = "",
        tracked_changes: bool = False,
        kind: str = "docx",
    ) -> ReferenceDoc:
        """Attach one document. Raises :class:`ReferenceDocError`."""
        if len(self.docs) >= MAX_REFERENCE_DOCS:
            raise ReferenceDocError(
                f"This session already has the maximum of "
                f"{MAX_REFERENCE_DOCS} reference documents. Remove one first."
            )
        body = text.strip()
        if not body:
            raise ReferenceDocError(
                "That document has no readable text to use as reference."
            )
        total = len(body)
        truncated = total > MAX_TEXT_CHARS
        if truncated:
            body = body[:MAX_TEXT_CHARS] + TRUNCATION_MARKER.format(
                kept=MAX_TEXT_CHARS, total=total
            )
        clean_title = " ".join((title or filename).split())[:MAX_TITLE]
        doc = ReferenceDoc(
            rid=f"ref-{self._next_seq}",
            filename=filename,
            title=clean_title or f"ref-{self._next_seq}",
            text=body,
            char_count=total,
            block_count=max(0, int(block_count)),
            truncated=truncated,
            tracked_changes=tracked_changes,
            added_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            kind=kind or "docx",
        )
        self._next_seq += 1
        self.docs.append(doc)
        return doc

    def delete(self, rid: str) -> bool:
        for i, doc in enumerate(self.docs):
            if doc.rid == rid:
                del self.docs[i]
                return True
        return False

    def get(self, rid: str) -> ReferenceDoc | None:
        for doc in self.docs:
            if doc.rid == rid:
                return doc
        return None

    # -- views --------------------------------------------------------------

    def snapshot(self) -> list[dict[str, Any]]:
        """Metadata for the frontend list (no bodies — see
        :meth:`ReferenceDoc.metadata`)."""
        return [doc.metadata() for doc in self.docs]

    def context_stubs(self) -> str:
        """One compact line per document for the PROJECT CONTEXT block.

        Deliberately excludes the body: this renders every turn, and the
        point of the store is that the text is billed only when actually
        read.
        """
        if not self.docs:
            return ""
        lines = [
            "REFERENCE DOCUMENTS (attached by the user as background; NOT "
            "part of the spec and not editable). Call read_reference_doc "
            "with the id to read one in full before drafting from it:"
        ]
        for doc in self.docs:
            marks = []
            if doc.truncated:
                marks.append("TRUNCATED")
            if doc.tracked_changes:
                marks.append("was tracked-changes, read as Accept-All")
            if doc.kind == "pdf":
                # Worth one clause: it is what lets the model answer "where
                # does the standard say that?" with a page.
                marks.append("page markers [page N] in the text")
            suffix = f" [{'; '.join(marks)}]" if marks else ""
            lines.append(
                f"- {doc.rid} \"{doc.title}\" — {doc.kind_label()}, "
                f"{doc.block_count} blocks, "
                f"~{max(1, doc.char_count // 4):,} tokens{suffix}"
            )
        return "\n".join(lines)

    # -- persistence --------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "reference_docs": [doc.to_dict() for doc in self.docs],
            "next_seq": self._next_seq,
        }

    def load(self, data: Any) -> None:
        """Restore from a project file. Malformed data degrades to empty
        rather than failing the load (the doc + history are load-bearing;
        reference material is supplementary) — the FigureStore posture."""
        self.reset()
        if not isinstance(data, dict):
            return
        raw = data.get("reference_docs")
        if not isinstance(raw, list):
            return
        restored: list[ReferenceDoc] = []
        max_seq = 0
        for entry in raw[:MAX_REFERENCE_DOCS]:
            if not isinstance(entry, dict):
                continue
            try:
                doc = ReferenceDoc.from_dict(entry)
            except (ValueError, KeyError, TypeError):
                continue
            restored.append(doc)
            tail = doc.rid.split("-")[-1]
            if tail.isdigit():
                max_seq = max(max_seq, int(tail))
        self.docs = restored
        stored_seq = data.get("next_seq")
        self._next_seq = max(
            max_seq + 1,
            int(stored_seq) if isinstance(stored_seq, int) else 1,
        )


# ---------------------------------------------------------------------------
# Tool definition (registered alongside apply_spec_edits in the chat loop)
# ---------------------------------------------------------------------------

READ_REFERENCE_DOC_TOOL: dict[str, Any] = {
    "name": "read_reference_doc",
    "description": (
        "Read the full text of a reference document the user attached to "
        "this session. The per-turn PROJECT CONTEXT lists what is available "
        "(ids like 'ref-1' with titles and sizes); this returns one "
        "document's complete text.\n"
        "\n"
        "Read a reference document before drafting content that should be "
        "based on it — an owner's design standard, a basis-of-design "
        "narrative, a product data sheet, a previous project's section. Do "
        "not guess at what it says.\n"
        "\n"
        "The text is returned for THIS turn only and is not retained in the "
        "conversation, so call the tool again in a later turn if you need it "
        "again; that is expected and inexpensive to request. Reference "
        "documents are background, not spec text: never paste their wording "
        "into a provision verbatim, and never treat them as authority for a "
        "code requirement — write the provision in proper specification "
        "language through apply_spec_edits, and record the provenance of any "
        "requirement honestly."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "ref_id": {
                "type": "string",
                "description": "The document id, e.g. 'ref-1'.",
            },
        },
        "required": ["ref_id"],
    },
}
