"""User-facing release notes, shipped inside the app.

The updater (``backend/updates.py``) already carried a ``notes`` string in
``latest.json``, but it only ever reached the user as the tooltip on the
"update available" pill. This module is the other half: a structured,
versioned changelog **bundled with the build**, so a freshly-updated app can
tell the user what changed without going back to the network.

Two consumers, one source of truth:

- **In-app** — ``GET /api/release-notes`` serves the entries the user has
  not seen yet; the frontend opens the What's-new modal once after an
  update, and Settings can reopen it any time.
- **Release-time** — ``packaging/windows/make_manifest.py --notes-file`` and
  the release workflow render :func:`manifest_summary` /
  :func:`markdown_notes` so ``latest.json`` and the GitHub Release page say
  the same thing this module does.

Writing an entry: the audience is a working spec author, not a developer.
Say what the user can now DO, in their words — not the subsystem that
changed. Keep ``body`` to a sentence or two.

Deliberately plain data — no I/O, no model, no app imports. The "have they
seen it" bookkeeping lives in the update state file
(``updates.last_seen_version``/``mark_version_seen``); the fresh-install vs
upgrade decision is :func:`resolve_pending`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .updates import parse_version

# The oldest version a user could plausibly be upgrading FROM. Used only
# when the update state file predates ``last_seen_version`` (i.e. the app
# ran before this feature existed) and we therefore cannot know which build
# they came from — see :func:`resolve_pending`.
EARLIEST_KNOWN_VERSION = "1.0.0"


@dataclass(frozen=True)
class ReleaseItem:
    """One thing the user can now do."""

    title: str
    body: str

    def to_dict(self) -> dict:
        return {"title": self.title, "body": self.body}


@dataclass(frozen=True)
class ReleaseSection:
    """A themed group of items ("Drafting", "Your existing documents", ...)."""

    title: str
    items: tuple[ReleaseItem, ...]

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "items": [item.to_dict() for item in self.items],
        }


@dataclass(frozen=True)
class ReleaseNote:
    """Everything new in one released version."""

    version: str
    date: str
    headline: str
    summary: str
    sections: tuple[ReleaseSection, ...] = field(default=())

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "date": self.date,
            "headline": self.headline,
            "summary": self.summary,
            "sections": [section.to_dict() for section in self.sections],
        }


# --------------------------------------------------------------------------
# The changelog itself — newest first.
# --------------------------------------------------------------------------

RELEASE_NOTES: tuple[ReleaseNote, ...] = (
    ReleaseNote(
        version="1.8.0",
        date="2026-07-28",
        headline="Final QC is much cheaper and much faster",
        summary=(
            "The pre-issue review pass now costs roughly a third of what it "
            "did and finishes in a fraction of the time, without checking "
            "any less of your section. Same five reviewers, same panel of "
            "refuters for every finding, same audit report."
        ),
        sections=(
            ReleaseSection(
                title="Final QC",
                items=(
                    ReleaseItem(
                        title="About a third of the previous cost",
                        body=(
                            "A review pass sends your section to the model "
                            "dozens of times — once per reviewer, then again "
                            "for every reviewer who checks a finding. It was "
                            "paying full price for your document on every "
                            "one of those. It now sends the document once "
                            "and refers back to it, and it runs on a model "
                            "that costs half as much per word while being "
                            "just as strong at review work."
                        ),
                    ),
                    ReleaseItem(
                        title="Finishes in a fraction of the time",
                        body=(
                            "The verification stage runs twice as many "
                            "checks at once as it used to, so a pass with "
                            "many findings no longer queues up behind "
                            "itself."
                        ),
                    ),
                    ReleaseItem(
                        title="Nothing about the review got smaller",
                        body=(
                            "Five independent reviewers, a panel of two or "
                            "three refuters for every finding they raise, a "
                            "tie still going to the refuters, and the same "
                            "full audit report. Only the bill and the wait "
                            "changed."
                        ),
                    ),
                    ReleaseItem(
                        title="Available on zero-retention accounts",
                        body=(
                            "The previous review model required 30-day data "
                            "retention, so organisations running "
                            "zero-data-retention had every Final QC request "
                            "rejected. That restriction is gone."
                        ),
                    ),
                    ReleaseItem(
                        title="Saved reviews will ask to be re-run",
                        body=(
                            "A Final QC result saved by an earlier version "
                            "was produced by a different reviewer, so it is "
                            "marked out of date when you open the project "
                            "and its fixes cannot be applied until you run "
                            "the pass again. Your document and everything "
                            "else in the project are untouched."
                        ),
                    ),
                ),
            ),
        ),
    ),
    ReleaseNote(
        version="1.7.0",
        date="2026-07-28",
        headline="A much bigger app than 1.0",
        summary=(
            "This is the first update since 1.0, and it is a large one. "
            "Build-a-Spec now drafts any discipline rather than fire "
            "suppression alone, reads background documents you attach, "
            "keeps your original Word formatting on export, and can teach "
            "itself to you with a guided tour that runs on a practice copy "
            "of your own project."
        ),
        sections=(
            ReleaseSection(
                title="Finding your way around",
                items=(
                    ReleaseItem(
                        title="A guided tour of the whole workflow",
                        body=(
                            "Ten chapters that walk you through drafting, "
                            "reviewing, research and issue-ready checks — "
                            "running on a protected practice copy of a real "
                            "project, so you can click anything without "
                            "consequences. Your own project is always "
                            "restored when you leave."
                        ),
                    ),
                    ReleaseItem(
                        title="Help topics in the header",
                        body=(
                            "Short answers on how to use the app, common "
                            "workflows, and how it actually works — "
                            "available without leaving what you are doing."
                        ),
                    ),
                    ReleaseItem(
                        title="“Why trust it?”, answered in full",
                        body=(
                            "A detailed dossier covering every action you "
                            "can take: what runs on your machine, what "
                            "leaves it, where each word in the draft came "
                            "from, and which parts involve no AI at all."
                        ),
                    ),
                ),
            ),
            ReleaseSection(
                title="Drafting",
                items=(
                    ReleaseItem(
                        title="Any discipline, USA and Canada",
                        body=(
                            "The app is no longer limited to fire "
                            "suppression. Start a blank session in any "
                            "discipline and it adapts — including Canadian "
                            "projects, with the right standards bodies and "
                            "metric units."
                        ),
                    ),
                    ReleaseItem(
                        title="One-tap suggested replies",
                        body=(
                            "A row of ready answers above the message box, "
                            "chosen fresh each turn — including “I don't "
                            "know”, which accepts a sensible default and "
                            "flags it as an assumption for your reviewer."
                        ),
                    ),
                    ReleaseItem(
                        title="Diagrams, schematics and tables in chat",
                        body=(
                            "Ask for a figure and it appears inline, with "
                            "SVG, PNG and CSV downloads. Useful for "
                            "sequences of operation, zone layouts and "
                            "schedules."
                        ),
                    ),
                    ReleaseItem(
                        title="Reusable templates",
                        body=(
                            "Save a finished section as a starting point for "
                            "future projects — either an exact snapshot or a "
                            "generalized version with the project-specific "
                            "wording rewritten out."
                        ),
                    ),
                    ReleaseItem(
                        title="A stop button",
                        body=(
                            "Stop a reply, a research run or a Final QC pass "
                            "at any time. Stopping a reply keeps everything "
                            "written up to that point."
                        ),
                    ),
                ),
            ),
            ReleaseSection(
                title="Working with documents you already have",
                items=(
                    ReleaseItem(
                        title="Attach reference documents",
                        body=(
                            "Give the app an owner's standard, a basis of "
                            "design or a product data sheet as background — "
                            "PDF, Word, text, XML or CSV. It reads from "
                            "them and never edits them, and never treats "
                            "them as your section."
                        ),
                    ),
                    ReleaseItem(
                        title="Word exports that keep your original file",
                        body=(
                            "When you start from an office master, the "
                            "export preserves the source document's own "
                            "formatting rather than re-rendering it, so what "
                            "you issue still looks like your firm's spec."
                        ),
                    ),
                    ReleaseItem(
                        title="Importing no longer freezes the app",
                        body=(
                            "Large masters upload in the background with a "
                            "progress indicator, and the chat stays "
                            "answerable throughout. A big section that used "
                            "to lock the window now stays usable."
                        ),
                    ),
                    ReleaseItem(
                        title="Honest handling of files that aren't specs",
                        body=(
                            "Upload a memo or a report and the app no longer "
                            "dresses it up with an invented section number "
                            "and empty PART headings. It says what it has "
                            "and asks what you want done with it."
                        ),
                    ),
                ),
            ),
            ReleaseSection(
                title="Research and review",
                items=(
                    ReleaseItem(
                        title="Research adds to what you already found",
                        body=(
                            "Running research again appends a new round "
                            "instead of replacing the last one. Earlier "
                            "grounded findings — and the citations attached "
                            "to your provisions — survive."
                        ),
                    ),
                    ReleaseItem(
                        title="A full research findings report",
                        body=(
                            "Read everything a run turned up, grouped by "
                            "topic, with sources and dates — rather than "
                            "only what reached the draft."
                        ),
                    ),
                    ReleaseItem(
                        title="Curate the standards list per document",
                        body=(
                            "Add a standard, correct an edition, or exclude "
                            "one that doesn't belong in this project's "
                            "REFERENCES — each with a stated reason, and "
                            "each undoable."
                        ),
                    ),
                    ReleaseItem(
                        title="Final QC can now do the safe fixing",
                        body=(
                            "A completed review separates fixes it can apply "
                            "safely from project decisions and professional "
                            "review. Preview the safe set for free, approve "
                            "one undoable batch, and get a receipt explaining "
                            "every applied or skipped finding; paid reruns "
                            "remain a separate choice."
                        ),
                    ),
                ),
            ),
            ReleaseSection(
                title="Keeping your work safe",
                items=(
                    ReleaseItem(
                        title="You are asked before losing anything",
                        body=(
                            "Closing the app, starting a new session or "
                            "opening another project all offer to save "
                            "first. Losing work now takes an explicit "
                            "“without saving”."
                        ),
                    ),
                    ReleaseItem(
                        title="See what a session is costing",
                        body=(
                            "A running spend estimate and a conversation "
                            "size meter in the header, with a full "
                            "breakdown in Settings. Research also got "
                            "meaningfully cheaper to run."
                        ),
                    ),
                    ReleaseItem(
                        title="Developer tools for when something misbehaves",
                        body=(
                            "Settings can show the activity log, this run's "
                            "trace events and a one-click diagnostics bundle "
                            "to attach to a bug report. Every run is "
                            "recorded on your machine, not just the ones "
                            "that fail."
                        ),
                    ),
                    ReleaseItem(
                        title="Smaller things that were annoying",
                        body=(
                            "An expired API key explains itself instead of "
                            "showing a raw error, saved project files are "
                            "date-stamped so same-day saves stop colliding, "
                            "links open in your browser, and text in the "
                            "window can be selected."
                        ),
                    ),
                ),
            ),
        ),
    ),
)


# --------------------------------------------------------------------------
# Queries
# --------------------------------------------------------------------------


def _sortable(version: str) -> tuple | None:
    """Version key, or ``None`` when the string is outside the grammar."""
    try:
        return parse_version(version)
    except ValueError:
        return None


def note_for(version: str) -> ReleaseNote | None:
    for note in RELEASE_NOTES:
        if note.version == version:
            return note
    return None


def notes_between(*, after: str, through: str) -> tuple[ReleaseNote, ...]:
    """Entries with ``after < version <= through``, newest first.

    A malformed bound is treated as "no lower bound" / "no upper bound"
    rather than raising: this feeds a cosmetic modal, and a corrupt state
    file must never keep the app from starting.
    """
    low = _sortable(after)
    high = _sortable(through)
    selected = []
    for note in RELEASE_NOTES:
        key = _sortable(note.version)
        if key is None:
            continue
        if low is not None and key <= low:
            continue
        if high is not None and key > high:
            continue
        selected.append(note)
    return tuple(sorted(selected, key=lambda n: _sortable(n.version), reverse=True))


def resolve_pending(
    *,
    current: str,
    last_seen: str,
    ran_before: bool,
) -> tuple[ReleaseNote, ...]:
    """Which entries to announce on this launch.

    Three cases, in order:

    - ``last_seen`` recorded — announce everything newer than it, which is
      the steady state from this version on.
    - No ``last_seen``, but the app has run before (``ran_before``: an
      update-state file already existed at startup) — the user is coming
      from a build that predates this feature, and there is no record of
      which one. Announce everything since :data:`EARLIEST_KNOWN_VERSION`.
    - No ``last_seen`` and no prior state — a fresh install. Announce
      nothing; a first-time user does not need the product's back
      catalogue.
    """
    if last_seen.strip():
        return notes_between(after=last_seen.strip(), through=current)
    if ran_before:
        return notes_between(after=EARLIEST_KNOWN_VERSION, through=current)
    return ()


# --------------------------------------------------------------------------
# Renderings — the manifest's plain text and the release page's markdown
# --------------------------------------------------------------------------


def manifest_summary(version: str) -> str:
    """Plain-text summary for ``latest.json``'s ``notes`` field.

    Shown to a user who has NOT updated yet (the update pill's tooltip and
    the update dialog), so it describes the version they would be getting.
    Kept short — the full entry ships inside the build they install.
    """
    note = note_for(version)
    if note is None:
        return f"Build-a-Spec {version} — see the release page for details."
    lines = [f"Build-a-Spec {version} — {note.headline}", "", note.summary]
    for section in note.sections:
        for item in section.items:
            lines.append(f"• {item.title}")
    return "\n".join(lines)


def markdown_notes(version: str) -> str:
    """Markdown for the GitHub Release body."""
    note = note_for(version)
    if note is None:
        return ""
    out = [f"## What's new in {note.version} — {note.headline}", "", note.summary, ""]
    for section in note.sections:
        out.append(f"### {section.title}")
        out.append("")
        for item in section.items:
            out.append(f"- **{item.title}** — {item.body}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"
