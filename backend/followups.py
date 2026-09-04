"""Waiting on you: the ``track_followups`` chat tool and its session store.

During an interview the model raises questions, surfaces decisions only the
user can make, and accumulates to-dos. Before this module none of that was
tracked — it existed as prose in a chat bubble and, sometimes, as a
suggested-reply chip, both of which scroll away. This is the store that
remembers them, and the tool the model writes it through.

Not to be confused with OPEN ITEMS
----------------------------------
``spec_doc.model.open_questions`` derives a list from the document tree
(``[TBD: ...]`` markers and ``needs_input`` blocks) — a pure projection with
no independent existence. This store is the opposite: model-authored state
that lives nowhere in the paragraph tree, because "the client has not said
whether the tenant fit-out is in scope" is not a provision. The two are
rendered side by side and must never be conflated; the context block below
is headed ``WAITING ON THE USER`` for exactly that reason.

A store, not a latest-only set
------------------------------
``suggest_prompts`` replaces its whole list every turn (silence = clear).
That rule is precisely wrong here: an item the model forgets to restate must
NOT vanish, since forgetting is the failure this feature exists to prevent.
So the tool is additive plus resolving, and the store persists across turns
and into the project file.

Turn atomicity
--------------
``FigureStore`` can roll back with a high-water mark because it is
append-only within a turn. This store *mutates* items (resolving one edits
it in place), so :meth:`FollowUpStore.begin_turn` snapshots the list and
:meth:`rollback_turn` restores it — the ``DocumentStore._turn_backup``
shape. Ids stay monotonic across a rollback and are never reused (the
document-store philosophy): a rolled-back id is skipped, not recycled.

Token posture
-------------
The payload is small (a title, a sentence of detail), so nothing is elided:
the ``tool_use`` input rides committed history verbatim, as with
``suggest_prompts``. What IS bounded is the context block — only the open
items and a short tail of recently settled ones reach the model, so a long
session cannot grow its own per-turn bill without limit.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

FOLLOWUP_KINDS = ("question", "decision", "todo")
FOLLOWUP_STATUSES = ("open", "resolved")

# Runaway breakers, not security boundaries. Raising past MAX_OPEN is
# refused with a message telling the model to settle something first — a
# tracker nobody can read is worse than no tracker.
MAX_OPEN = 20
# Settled items are kept so the panel can show what was checked off and the
# model does not re-raise them. This is a working list, not an audit log, so
# the oldest fall off rather than accumulating forever.
MAX_RESOLVED_KEPT = 50
MAX_TITLE_CHARS = 140
MAX_DETAIL_CHARS = 600
MAX_RESOLUTION_CHARS = 300
# How many settled items the model is shown. Enough to stop it re-raising
# what was just answered; not so many that the block grows with the session.
CONTEXT_RESOLVED_TAIL = 5

# What the panel records when the user ticks an item off without saying what
# they decided. Deliberately explicit: a UI shortcut must not become a fact
# the model invents around, so the context block discloses it (see
# ``context_block``).
PANEL_RESOLUTION = "Marked done in the panel."


class FollowUpError(ValueError):
    """A malformed ``track_followups`` request. Reported to the model to fix."""


def _clean_str(value: Any, limit: int, what: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise FollowUpError(f"track_followups: '{what}' must be a string.")
    text = " ".join(value.split())
    if len(text) > limit:
        raise FollowUpError(
            f"track_followups: '{what}' is too long ({len(text)} > {limit} "
            "chars). State it in one line; put the reasoning in your reply."
        )
    return text


def _match_key(title: str) -> str:
    """Normalized title, for the open-item duplicate check."""
    return " ".join(title.split()).casefold()


@dataclass
class FollowUp:
    """One thing the model is waiting on the user for.

    ``raised_turn`` is the assistant-bubble ordinal at creation (the same
    counter figures use), so age renders as "raised N replies ago" — the
    measure that matches the user's actual experience of a thing going
    unanswered.
    """

    fid: str
    kind: str
    title: str
    detail: str = ""
    blocking: bool = False
    element_id: str = ""
    status: str = "open"
    resolution: str = ""
    resolved_by: str = ""
    raised_turn: int = 0
    created_at: str = ""
    resolved_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "fid": self.fid,
            "kind": self.kind,
            "title": self.title,
            "detail": self.detail,
            "blocking": self.blocking,
            "element_id": self.element_id,
            "status": self.status,
            "resolution": self.resolution,
            "resolved_by": self.resolved_by,
            "raised_turn": self.raised_turn,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FollowUp":
        kind = str(data.get("kind", ""))
        if kind not in FOLLOWUP_KINDS:
            raise ValueError(f"unknown follow-up kind {kind!r}")
        status = str(data.get("status", "open"))
        if status not in FOLLOWUP_STATUSES:
            raise ValueError(f"unknown follow-up status {status!r}")
        fid = str(data.get("fid", ""))
        if not fid:
            raise ValueError("follow-up is missing its id")
        return cls(
            fid=fid,
            kind=kind,
            title=str(data.get("title", "")),
            detail=str(data.get("detail", "")),
            blocking=bool(data.get("blocking", False)),
            element_id=str(data.get("element_id", "")),
            status=status,
            resolution=str(data.get("resolution", "")),
            resolved_by=str(data.get("resolved_by", "")),
            raised_turn=int(data.get("raised_turn", 0) or 0),
            created_at=str(data.get("created_at", "")),
            resolved_at=str(data.get("resolved_at", "")),
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class FollowUpStore:
    """Session-level tracker with per-turn atomicity and persistence."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.items: list[FollowUp] = []
        self._next_seq = 1
        # Pre-turn copy of ``items``; None outside a turn. A snapshot rather
        # than a high-water mark because a turn can RESOLVE an existing item
        # (see the module docstring).
        self._turn_backup: list[FollowUp] | None = None

    # -- turn lifecycle ---------------------------------------------------

    def begin_turn(self) -> None:
        if self._turn_backup is not None:
            # A previous turn never resolved (abandoned mid-stream) — drop
            # its provisional writes before starting fresh.
            self.rollback_turn()
        self._turn_backup = [replace(item) for item in self.items]

    def commit_turn(self) -> None:
        self._turn_backup = None

    def rollback_turn(self) -> None:
        if self._turn_backup is not None:
            self.items = self._turn_backup
        self._turn_backup = None
        # _next_seq is deliberately NOT restored: ids are never reused, so a
        # rolled-back id is skipped rather than recycled.

    # -- mutation ---------------------------------------------------------

    def add(
        self, payload: Any, *, message_index: int = 0
    ) -> tuple[FollowUp, bool]:
        """Track one new item. Returns ``(item, was_duplicate)``.

        A title matching an already-OPEN item is a no-op returning that
        item — the model restating a question it already tracked must not
        double the list. A title matching a *resolved* item creates a new
        one: the question genuinely came back.
        """
        if not isinstance(payload, dict):
            raise FollowUpError("track_followups: every 'add' entry must be an object.")
        kind = payload.get("kind")
        if kind not in FOLLOWUP_KINDS:
            raise FollowUpError(
                "track_followups: 'kind' must be one of "
                f"{', '.join(FOLLOWUP_KINDS)}."
            )
        title = _clean_str(payload.get("title"), MAX_TITLE_CHARS, "title")
        if not title:
            raise FollowUpError(
                "track_followups: 'title' is required — state the ask itself "
                "in one line."
            )
        key = _match_key(title)
        for item in self.items:
            if item.status == "open" and _match_key(item.title) == key:
                return item, True
        if sum(1 for item in self.items if item.status == "open") >= MAX_OPEN:
            raise FollowUpError(
                f"track_followups: already tracking {MAX_OPEN} open items — "
                "settle some of them before adding more, or leave this one "
                "out if it is not genuinely waiting on the user."
            )
        item = FollowUp(
            fid=f"fu-{self._next_seq}",
            kind=str(kind),
            title=title,
            detail=_clean_str(payload.get("detail"), MAX_DETAIL_CHARS, "detail"),
            blocking=bool(payload.get("blocking", False)),
            element_id=_clean_str(payload.get("element_id"), 80, "element_id"),
            raised_turn=max(0, int(message_index)),
            created_at=_now(),
        )
        self._next_seq += 1
        self.items.append(item)
        return item, False

    def apply(
        self, payload: dict[str, list[Any]], *, message_index: int = 0
    ) -> dict[str, Any]:
        """Apply one validated ``track_followups`` batch, all or nothing.

        Every unknown id and every malformed entry is rejected with the
        store untouched, so the model never has to reason about which half
        of its request survived. Returns the compact summary the tool result
        echoes back.
        """
        unknown = [
            entry["id"]
            for entry in payload.get("resolve", [])
            if self.get(entry["id"]) is None
        ]
        if unknown:
            open_ids = ", ".join(item.fid for item in self.open_items()) or "none"
            raise FollowUpError(
                f"no tracked item {', '.join(unknown)}. Open items: {open_ids}."
            )
        before = [replace(item) for item in self.items]
        before_seq = self._next_seq
        added: list[str] = []
        duplicate: list[str] = []
        try:
            for entry in payload.get("add", []):
                item, was_duplicate = self.add(entry, message_index=message_index)
                (duplicate if was_duplicate else added).append(item.fid)
        except FollowUpError:
            self.items = before
            self._next_seq = before_seq
            raise
        resolved: list[str] = []
        already: list[str] = []
        for entry in payload.get("resolve", []):
            outcome = self.resolve(entry["id"], entry["resolution"], by="model")
            (already if outcome == "already" else resolved).append(entry["id"])
        summary: dict[str, Any] = {"waiting": len(self.open_items())}
        if added:
            summary["added"] = added
        if resolved:
            summary["resolved"] = resolved
        if duplicate:
            summary["already_tracked"] = duplicate
        if already:
            summary["already_settled"] = already
        return summary

    def resolve(
        self, fid: str, resolution: str, *, by: str = "model"
    ) -> str:
        """Settle one item. Returns ``resolved`` / ``already`` / ``missing``.

        Re-resolving is idempotent rather than an error: the model saying so
        twice is harmless and should not cost a correction round.
        """
        item = self.get(fid)
        if item is None:
            return "missing"
        if item.status == "resolved":
            return "already"
        item.status = "resolved"
        item.resolution = resolution
        item.resolved_by = by
        item.resolved_at = _now()
        self._trim_resolved()
        return "resolved"

    def annotate(self, fid: str, resolution: str) -> bool:
        """Record what the user decided about an already-settled item.

        Separate from :meth:`resolve`, which is deliberately idempotent so a
        model restating a settlement costs nothing. That idempotence is
        wrong for the panel's "add a note" affordance — a user typing what
        they decided must actually change the record, and the model must
        stop being told they never said.
        """
        item = self.get(fid)
        if item is None or item.status != "resolved":
            return False
        item.resolution = resolution
        item.resolved_by = "user"
        return True

    def reopen(self, fid: str) -> bool:
        """Put a settled item back on the waiting list (a mis-click's way out)."""
        item = self.get(fid)
        if item is None or item.status == "open":
            return False
        item.status = "open"
        item.resolution = ""
        item.resolved_by = ""
        item.resolved_at = ""
        return True

    def _trim_resolved(self) -> None:
        resolved = [item for item in self.items if item.status == "resolved"]
        excess = len(resolved) - MAX_RESOLVED_KEPT
        if excess <= 0:
            return
        drop = {id(item) for item in resolved[:excess]}
        self.items = [item for item in self.items if id(item) not in drop]

    # -- views ------------------------------------------------------------

    def get(self, fid: str) -> FollowUp | None:
        for item in self.items:
            if item.fid == fid:
                return item
        return None

    def open_items(self) -> list[FollowUp]:
        return [item for item in self.items if item.status == "open"]

    def snapshot(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self.items]

    def next_to_surface(self) -> FollowUp | None:
        """The one item the model should raise this turn.

        Oldest blocking open item, else the oldest open item. Deterministic,
        so the policy can name it and the behaviour is reproducible.
        """
        candidates = self.open_items()
        if not candidates:
            return None
        blocking = [item for item in candidates if item.blocking]
        pool = blocking or candidates
        return min(pool, key=lambda item: (item.raised_turn, _seq_of(item)))

    def context_block(self, *, message_index: int = 0) -> str:
        """The WAITING ON THE USER block for this turn's PROJECT CONTEXT.

        Empty store renders ``""`` so a session with no tracked items builds
        a byte-identical request.
        """
        if not self.items:
            return ""
        lines: list[str] = []
        open_items = sorted(
            self.open_items(), key=lambda item: (item.raised_turn, _seq_of(item))
        )
        nxt = self.next_to_surface()
        if open_items:
            lines.append(
                "WAITING ON THE USER (questions, decisions and to-dos you are "
                "tracking for them — resolve each with track_followups the "
                "moment it is settled):"
            )
            for item in open_items:
                marker = "[NEXT] " if nxt is not None and item is nxt else ""
                flag = " BLOCKING" if item.blocking else ""
                age = _age_phrase(message_index - item.raised_turn)
                lines.append(
                    f"- {marker}{item.fid} [{item.kind}]{flag} {item.title} ({age})"
                )
                if item.detail:
                    lines.append(f"    Why it matters: {item.detail}")
                if item.element_id:
                    lines.append(f"    Relates to element {item.element_id}.")
            if nxt is not None:
                lines.append(
                    "[NEXT] marks the one to surface in this turn's reply or "
                    "as a suggested-reply chip."
                )
        settled = [item for item in self.items if item.status == "resolved"]
        if settled:
            lines.append("Recently settled (do not raise these again):")
            for item in settled[-CONTEXT_RESOLVED_TAIL:]:
                if item.resolved_by == "user" and item.resolution == PANEL_RESOLUTION:
                    note = (
                        "the user ticked this off in the panel without saying "
                        "what was decided — ask only if the answer still "
                        "affects the draft"
                    )
                else:
                    note = item.resolution or "settled"
                lines.append(f"- {item.fid} {item.title} — {note}")
        return "\n".join(lines)

    # -- persistence ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {"followups": self.snapshot(), "next_seq": self._next_seq}

    def load(self, data: Any) -> None:
        """Lenient restore. Resets first, so an absent block clears the store.

        That matters because ``load_project`` never calls ``session.reset()``
        — loading over a live session must not inherit its tracked items.
        """
        self.reset()
        if not isinstance(data, dict):
            return
        raw = data.get("followups")
        if not isinstance(raw, list):
            return
        restored: list[FollowUp] = []
        max_seq = 0
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            try:
                item = FollowUp.from_dict(entry)
            except (ValueError, KeyError, TypeError):
                continue
            restored.append(item)
            tail = item.fid.split("-")[-1]
            if tail.isdigit():
                max_seq = max(max_seq, int(tail))
        self.items = restored
        stored_seq = data.get("next_seq")
        # Belt and braces: a hand-edited file must not make the store mint an
        # id that collides with one it just restored.
        self._next_seq = max(
            max_seq + 1,
            int(stored_seq) if isinstance(stored_seq, int) else 1,
        )


def _seq_of(item: FollowUp) -> int:
    tail = item.fid.split("-")[-1]
    return int(tail) if tail.isdigit() else 0


def _age_phrase(replies: int) -> str:
    if replies <= 0:
        return "raised just now"
    if replies == 1:
        return "raised 1 reply ago"
    return f"raised {replies} replies ago"


def validate_track_payload(payload: Any) -> dict[str, list[Any]]:
    """Validate a raw ``track_followups`` input; return the normalized halves.

    Strict (model-facing): raises :class:`FollowUpError`, surfaced as an
    ``is_error`` tool result the model self-corrects from — never a turn
    failure. Entry-level validation of an ``add`` happens in
    :meth:`FollowUpStore.add`, which owns the field rules.
    """
    if not isinstance(payload, dict):
        raise FollowUpError("track_followups: input must be an object.")
    added = payload.get("add") or []
    resolved = payload.get("resolve") or []
    if not isinstance(added, list) or not isinstance(resolved, list):
        raise FollowUpError(
            "track_followups: 'add' and 'resolve' must each be a list."
        )
    if not added and not resolved:
        raise FollowUpError(
            "track_followups: nothing to do — send at least one 'add' or one "
            "'resolve' entry."
        )
    cleaned_resolve: list[dict[str, str]] = []
    for entry in resolved:
        if not isinstance(entry, dict):
            raise FollowUpError(
                "track_followups: every 'resolve' entry must be an object "
                "with 'id' and 'resolution'."
            )
        fid = _clean_str(entry.get("id"), 40, "id")
        if not fid:
            raise FollowUpError("track_followups: a 'resolve' entry needs an 'id'.")
        resolution = _clean_str(
            entry.get("resolution"), MAX_RESOLUTION_CHARS, "resolution"
        )
        if not resolution:
            raise FollowUpError(
                f"track_followups: resolving {fid} needs a 'resolution' — one "
                "line saying what was settled, so the record means something."
            )
        cleaned_resolve.append({"id": fid, "resolution": resolution})
    return {"add": list(added), "resolve": cleaned_resolve}


# Lenient schema (the create_figure posture, NOT the research strict shape):
# validation lives in this module, and a bad payload becomes an is_error tool
# result the model corrects. The description is version-static — it precedes
# the system prompt in the cached prefix, so nothing session-varying may ever
# render into it.
TRACK_FOLLOWUPS_TOOL: dict[str, Any] = {
    "name": "track_followups",
    "description": (
        "Track what you are waiting on the user for, and check items off as "
        "they are settled. The list is shown to the user in a 'Waiting on "
        "you' panel beside the document and is repeated to you in every "
        "turn's PROJECT CONTEXT, so nothing you asked can quietly scroll "
        "away.\n\n"
        "Add an item when you ask something the user has not answered, when "
        "a decision is genuinely theirs to make, or when either of you owes "
        "the other a to-do. Do NOT add an unknown that belongs in the "
        "document instead: a value you can draft around goes in as "
        "[TBD: ...] or a needs_input block through apply_spec_edits, and is "
        "already tracked as an Open item.\n\n"
        "Resolve an item in the SAME turn it is settled — the user answers "
        "it, a decision is made, the to-do is done, or it becomes moot. The "
        "'resolution' is one line saying what was settled; it is what the "
        "user sees on the checked-off row.\n\n"
        "Set 'blocking' only when the draft cannot be correct without the "
        "answer — not merely when it is important. Set 'element_id' when the "
        "item is about a specific provision, so the user can jump to it.\n\n"
        "Call this at most once per turn, with both halves in the one call. "
        "Re-adding a title already open is a no-op, so restating is safe but "
        "pointless."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "add": {
                "type": "array",
                "description": "New items to start tracking.",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": list(FOLLOWUP_KINDS),
                            "description": (
                                "question = you asked and are awaiting an "
                                "answer; decision = a choice only the user "
                                "can make; todo = an action one of you owes."
                            ),
                        },
                        "title": {
                            "type": "string",
                            "description": (
                                "The ask itself, in one line the user can "
                                "read at a glance."
                            ),
                        },
                        "detail": {
                            "type": "string",
                            "description": (
                                "Optional: why it matters, or what changes "
                                "either way."
                            ),
                        },
                        "blocking": {
                            "type": "boolean",
                            "description": (
                                "True only when the draft cannot be correct "
                                "until this is answered."
                            ),
                        },
                        "element_id": {
                            "type": "string",
                            "description": (
                                "Optional element id this item is about."
                            ),
                        },
                    },
                    "required": ["kind", "title"],
                },
            },
            "resolve": {
                "type": "array",
                "description": "Items now settled.",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "The tracked item's id, e.g. fu-3.",
                        },
                        "resolution": {
                            "type": "string",
                            "description": (
                                "One line saying what was settled, in plain "
                                "words the user will recognize."
                            ),
                        },
                    },
                    "required": ["id", "resolution"],
                },
            },
        },
    },
}
