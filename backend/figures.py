"""Chat-authored figures: diagrams, schematics, and data tables.

A session-level store of visual/data exhibits the model produces through the
``create_figure`` tool during an interview turn — Mermaid diagrams (riser
schematics as flow/sequence/decision graphs), hand-authored SVG (spatial
line diagrams), and data tables (device/valve schedules). They render inline
in the chat and download as SVG / PNG / CSV; they are NOT part of the
SectionFormat tree.

Token posture (deliberate, mirrors the fetched-PDF elision policy)
------------------------------------------------------------------
Figure *source* is heavy (an SVG can be thousands of tokens) and this app
re-bills the entire document context every turn. So figure source lives here
only — it never renders into the PROJECT CONTEXT block or the model's tool
results. The model sees a one-line stub per figure (id / kind / title) so it
knows what exists and can reference it; to revise, it regenerates. Recurring
token cost is therefore a rounding error regardless of figure count.

Security note
-------------
Figure ``source`` is model-authored and therefore untrusted. This module
stores it verbatim and never serves it as an executable document (the only
content endpoint, CSV, emits ``text/csv``). The sanitization boundary is the
frontend renderer (DOMPurify + a scriptless sandbox iframe); see
``frontend/src/lib/figures.ts``. The caps here are anti-abuse bounds, not a
sanitizer.

Turn atomicity
--------------
Figures created during a turn are provisional: :meth:`FigureStore.begin_turn`
marks the pre-turn size, :meth:`commit_turn` keeps the turn's additions, and
:meth:`rollback_turn` drops them — so a failed or abandoned turn leaves no
orphan figures, exactly like the document store's per-turn versioning. Ids
are monotonic and never reused (the document-store philosophy), so a rolled-
back id is simply skipped, never recycled.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# Anti-abuse bounds (NOT a security boundary — see the module docstring).
FIGURE_KINDS = ("mermaid", "svg", "table")
_MAX_TITLE = 200
_MAX_CAPTION = 600
_MAX_ALT = 600
_MAX_SOURCE = 40_000  # a detailed SVG is legitimately large; this only stops abuse
_MAX_COLUMNS = 30
_MAX_ROWS = 500
_MAX_CELL = 2_000


class FigureError(ValueError):
    """A malformed ``create_figure`` request. Reported to the model to fix."""


@dataclass
class Figure:
    """One chat-authored exhibit. ``source`` for mermaid/svg; columns+rows
    for a table. ``message_index`` is the ordinal of the assistant chat
    bubble that created it, so a reloaded project re-inlines it correctly."""

    fid: str
    kind: str
    title: str
    caption: str = ""
    alt_text: str = ""
    source: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    created_at: str = ""
    message_index: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "fid": self.fid,
            "kind": self.kind,
            "title": self.title,
            "caption": self.caption,
            "alt_text": self.alt_text,
            "source": self.source,
            "columns": list(self.columns),
            "rows": [list(row) for row in self.rows],
            "created_at": self.created_at,
            "message_index": self.message_index,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Figure":
        kind = str(data.get("kind", ""))
        if kind not in FIGURE_KINDS:
            raise ValueError(f"unknown figure kind {kind!r}")
        columns = data.get("columns") or []
        rows = data.get("rows") or []
        if not isinstance(columns, list) or not isinstance(rows, list):
            raise ValueError("figure columns/rows must be lists")
        return cls(
            fid=str(data["fid"]),
            kind=kind,
            title=str(data.get("title", "")),
            caption=str(data.get("caption", "")),
            alt_text=str(data.get("alt_text", "")),
            source=str(data.get("source", "")),
            columns=[str(c) for c in columns],
            rows=[[str(c) for c in row] for row in rows if isinstance(row, list)],
            created_at=str(data.get("created_at", "")),
            message_index=int(data.get("message_index", 0) or 0),
        )

    def to_csv(self) -> str:
        """Render a table figure as CSV text (empty for non-table kinds)."""
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        if self.columns:
            writer.writerow(self.columns)
        for row in self.rows:
            writer.writerow(row)
        return buffer.getvalue()


def _clean_str(value: Any, limit: int, what: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise FigureError(f"create_figure: '{what}' must be a string.")
    text = value.strip()
    if len(text) > limit:
        raise FigureError(
            f"create_figure: '{what}' is too long ({len(text)} > {limit} chars)."
        )
    return text


def _validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a raw ``create_figure`` tool input; return normalized fields.

    Raises :class:`FigureError` (surfaced to the model as a correctable tool
    error, never a turn failure) on anything malformed.
    """
    if not isinstance(payload, dict):
        raise FigureError("create_figure: input must be an object.")
    kind = payload.get("kind")
    if kind not in FIGURE_KINDS:
        raise FigureError(
            "create_figure: 'kind' must be one of "
            f"{', '.join(FIGURE_KINDS)}."
        )
    title = _clean_str(payload.get("title"), _MAX_TITLE, "title")
    if not title:
        raise FigureError("create_figure: a non-empty 'title' is required.")
    caption = _clean_str(payload.get("caption"), _MAX_CAPTION, "caption")
    alt_text = _clean_str(payload.get("alt_text"), _MAX_ALT, "alt_text")

    source = ""
    columns: list[str] = []
    rows: list[list[str]] = []
    if kind == "table":
        raw_columns = payload.get("columns")
        raw_rows = payload.get("rows")
        if not isinstance(raw_columns, list) or not raw_columns:
            raise FigureError(
                "create_figure: a table needs a non-empty 'columns' list."
            )
        if len(raw_columns) > _MAX_COLUMNS:
            raise FigureError(
                f"create_figure: too many columns (max {_MAX_COLUMNS})."
            )
        columns = [_clean_str(c, _MAX_CELL, "column") for c in raw_columns]
        if not isinstance(raw_rows, list):
            raise FigureError("create_figure: 'rows' must be a list of rows.")
        if len(raw_rows) > _MAX_ROWS:
            raise FigureError(
                f"create_figure: too many rows (max {_MAX_ROWS})."
            )
        for row in raw_rows:
            if not isinstance(row, list):
                raise FigureError("create_figure: each row must be a list of cells.")
            cells = [_clean_str(c, _MAX_CELL, "cell") for c in row]
            # Pad/truncate to the column count so the CSV/table is rectangular.
            if len(cells) < len(columns):
                cells += [""] * (len(columns) - len(cells))
            elif len(cells) > len(columns):
                cells = cells[: len(columns)]
            rows.append(cells)
    else:  # mermaid | svg
        source = _clean_str(payload.get("source"), _MAX_SOURCE, "source")
        if not source:
            raise FigureError(
                f"create_figure: a '{kind}' figure needs non-empty 'source' "
                f"({'Mermaid diagram text' if kind == 'mermaid' else 'SVG markup'})."
            )
        if kind == "svg" and "<svg" not in source.lower():
            raise FigureError(
                "create_figure: an 'svg' figure's source must be SVG markup "
                "containing an <svg> element."
            )

    return {
        "kind": kind,
        "title": title,
        "caption": caption,
        "alt_text": alt_text,
        "source": source,
        "columns": columns,
        "rows": rows,
    }


class FigureStore:
    """Session-level figure list with per-turn atomicity + persistence."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.figures: list[Figure] = []
        self._next_seq = 1
        # Size of ``figures`` at the current turn's start; None outside a turn.
        self._turn_mark: int | None = None

    # -- turn lifecycle -----------------------------------------------------

    def begin_turn(self) -> None:
        if self._turn_mark is not None:
            # A previous turn never resolved (abandoned mid-stream) — drop its
            # provisional additions before starting fresh.
            self.rollback_turn()
        self._turn_mark = len(self.figures)

    def commit_turn(self) -> None:
        self._turn_mark = None

    def rollback_turn(self) -> None:
        if self._turn_mark is not None:
            del self.figures[self._turn_mark :]
        self._turn_mark = None

    # -- mutation -----------------------------------------------------------

    def create(self, payload: dict[str, Any], *, message_index: int = 0) -> Figure:
        """Validate + append a figure; return it. Raises :class:`FigureError`."""
        fields = _validate_payload(payload)
        figure = Figure(
            fid=f"fig-{self._next_seq}",
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            message_index=max(0, int(message_index)),
            **fields,
        )
        self._next_seq += 1
        self.figures.append(figure)
        return figure

    def delete(self, fid: str) -> bool:
        for i, figure in enumerate(self.figures):
            if figure.fid == fid:
                del self.figures[i]
                return True
        return False

    def get(self, fid: str) -> Figure | None:
        for figure in self.figures:
            if figure.fid == fid:
                return figure
        return None

    # -- views --------------------------------------------------------------

    def snapshot(self) -> list[dict[str, Any]]:
        """Full figures for the frontend (source included — it renders them)."""
        return [figure.to_dict() for figure in self.figures]

    def context_stubs(self) -> str:
        """One compact line per figure for the PROJECT CONTEXT block.

        Source is deliberately omitted (token discipline): the model sees
        what exists, not the heavy markup.
        """
        if not self.figures:
            return ""
        lines = [
            "FIGURES (exhibits you created this session; source not shown — "
            "reference by id, regenerate to revise):"
        ]
        for figure in self.figures:
            extra = (
                f" ({len(figure.rows)} rows)"
                if figure.kind == "table"
                else ""
            )
            caption = f" — {figure.caption}" if figure.caption else ""
            lines.append(
                f"- {figure.fid} [{figure.kind}] {figure.title}{extra}{caption}"
            )
        return "\n".join(lines)

    # -- persistence --------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {"figures": self.snapshot(), "next_seq": self._next_seq}

    def load(self, data: Any) -> None:
        """Restore from a project file. Malformed data degrades to empty
        rather than failing the load (the doc + history are load-bearing;
        figures are supplementary)."""
        self.reset()
        if not isinstance(data, dict):
            return
        raw = data.get("figures")
        if not isinstance(raw, list):
            return
        restored: list[Figure] = []
        max_seq = 0
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            try:
                figure = Figure.from_dict(entry)
            except (ValueError, KeyError, TypeError):
                continue
            restored.append(figure)
            # Track the highest sequence so new ids never collide with restored.
            match = figure.fid.split("-")[-1]
            if match.isdigit():
                max_seq = max(max_seq, int(match))
        self.figures = restored
        stored_seq = data.get("next_seq")
        self._next_seq = max(
            max_seq + 1,
            int(stored_seq) if isinstance(stored_seq, int) else 1,
        )


# ---------------------------------------------------------------------------
# Tool definition (registered alongside apply_spec_edits in the chat loop)
# ---------------------------------------------------------------------------

CREATE_FIGURE_TOOL: dict[str, Any] = {
    "name": "create_figure",
    "description": (
        "Create a downloadable figure that renders inline in the chat: a "
        "diagram, schematic, or data table to accompany the specification. "
        "Figures are EXHIBITS, not spec text — never move a normative "
        "requirement into a figure alone; the enforceable words still belong "
        "in a provision via apply_spec_edits. Use a figure when a picture or "
        "a schedule genuinely aids understanding (a sprinkler/standpipe riser "
        "schematic, a sequence of operations, a hazard/commodity "
        "classification decision tree, a device or valve schedule) — not for "
        "decoration.\n"
        "\n"
        "Choose 'kind':\n"
        "- mermaid: a Mermaid diagram (source = Mermaid text). Best for "
        "flowcharts, sequence diagrams, state/decision trees, and process "
        "timelines. Keep node labels plain text.\n"
        "- svg: hand-authored SVG markup (source = the <svg>…</svg>). Use for "
        "spatial line schematics a flowchart cannot express (a riser diagram, "
        "a zone map). Use plain shapes, lines, and <text>; do not include "
        "scripts, event handlers, or external references — they are stripped "
        "when rendered.\n"
        "- table: a data table (provide 'columns' and 'rows', not 'source'). "
        "Best for schedules; downloads as CSV.\n"
        "\n"
        "Always give a short 'title'. Add a one-line 'caption' for context "
        "and 'alt_text' describing the figure for accessibility. In your chat "
        "reply, say in one line what the figure shows; do not paste its "
        "source. To revise a figure, create a new one — the old id is kept "
        "for reference."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": list(FIGURE_KINDS)},
            "title": {"type": "string"},
            "caption": {"type": "string"},
            "alt_text": {"type": "string"},
            "source": {
                "type": "string",
                "description": "Mermaid text (kind=mermaid) or SVG markup "
                "(kind=svg). Omit for a table.",
            },
            "columns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Table header cells (kind=table).",
            },
            "rows": {
                "type": "array",
                "items": {"type": "array", "items": {"type": "string"}},
                "description": "Table body rows (kind=table); each row a list "
                "of cell strings.",
            },
        },
        "required": ["kind", "title"],
    },
}
