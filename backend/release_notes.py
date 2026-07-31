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
        date="2026-07-31",
        headline="Cheaper, faster, and a good deal more honest",
        summary=(
            "The pre-issue review pass costs roughly a third of what it did "
            "and finishes in a fraction of the time, without checking any "
            "less of your section. Beyond that, a long pass over the whole "
            "app tightened the places where it was telling you something "
            "more comforting than the truth: research that only half ran now "
            "says so and holds up issue readiness, reviewers who disagree "
            "about a serious finding now say that out loud instead of "
            "quietly dropping it, the running meter counts work you paid for "
            "and did not get, and the window stops freezing while it exports "
            "or imports. The assistant also knows today's date now, which "
            "changes how it talks about code editions."
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
                            "three refuters for every finding they raise, "
                            "and the same full audit report. Only the bill "
                            "and the wait changed."
                        ),
                    ),
                    ReleaseItem(
                        title="Reviewers are allowed to disagree out loud",
                        body=(
                            "A finding used to be either kept or killed. "
                            "When the refuters split — two of three saying a "
                            "life-safety finding is real — the old rule "
                            "quietly dropped it. A split now comes back to "
                            "you as disputed, with both sides shown, and it "
                            "holds up issue readiness until you decide. "
                            "Address it or dismiss it with a reason; the "
                            "app will not make that call for you."
                        ),
                    ),
                    ReleaseItem(
                        title="Knocking down a serious finding takes evidence",
                        body=(
                            "A reviewer refuting a critical or high finding "
                            "now has to point at something it actually read "
                            "— a source it retrieved, or a place in your "
                            "document. Having run a search is not evidence. "
                            "Without it the finding is treated as disputed "
                            "and comes to you rather than disappearing."
                        ),
                    ),
                    ReleaseItem(
                        title="The same defect no longer arrives five times",
                        body=(
                            "Five reviewers reading one section routinely "
                            "raise the same problem in different words, and "
                            "each copy used to buy its own panel of refuters "
                            "— and land in your fix queue separately. "
                            "Versions of one defect are now grouped before "
                            "verification, so you see it once. Every "
                            "original wording is kept in the full report."
                        ),
                    ),
                    ReleaseItem(
                        title="Findings you dismissed will come back once",
                        body=(
                            "The way a finding is identified includes how "
                            "its panel voted, and the voting rules changed. "
                            "A review saved by an earlier version therefore "
                            "cannot match its dismissals to the findings a "
                            "fresh pass raises: anything you had set aside "
                            "reappears the first time you re-run. Dismiss it "
                            "again and it stays dismissed from then on."
                        ),
                    ),
                    ReleaseItem(
                        title="\"Ready to issue\" and \"open findings\" can "
                        "no longer both be true",
                        body=(
                            "The report could print \"Issue readiness: Yes\" "
                            "on its front page and \"REVIEW REQUIRED — OPEN "
                            "FINDINGS REMAIN\" on its sign-off page, of the "
                            "same review. The sign-off wins: an open finding "
                            "of any severity, or an undecided dispute, "
                            "blocks issue readiness, and the front page now "
                            "names which check is blocking. There is also a "
                            "short executive summary at the top of the Word "
                            "report — the full annex is unchanged."
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
            ReleaseSection(
                title="Code editions",
                items=(
                    ReleaseItem(
                        title="The assistant knows what today's date is",
                        body=(
                            "It never did before. An AI model has no clock, "
                            "so left to itself it judges whether an edition "
                            "is current against material it learned during "
                            "training — which gets further out of date every "
                            "month the app stays installed. It is now told "
                            "the real date off your own machine at the start "
                            "of every chat turn, every research run, and "
                            "every Final QC review."
                        ),
                    ),
                    ReleaseItem(
                        title="A likely newer edition is raised, not assumed",
                        body=(
                            "Codes and standards revise on multi-year "
                            "cycles. When enough time has passed that a "
                            "newer edition of something you are citing has "
                            "probably been published, the assistant now says "
                            "so and offers to check, instead of either "
                            "quietly drafting to the old one or switching "
                            "you to a new one you never adopted. Your "
                            "recorded editions still only change when you "
                            "say so."
                        ),
                    ),
                ),
            ),
            ReleaseSection(
                title="Research",
                items=(
                    ReleaseItem(
                        title="Half-finished research says so",
                        body=(
                            "A research run succeeds if any of its areas "
                            "finish, so it was normal to get findings for "
                            "governing codes and nothing at all for the "
                            "authority having jurisdiction — with no way to "
                            "tell that apart from \"nothing applies here.\" "
                            "Areas that never completed are now named, to "
                            "you and to the assistant, and the assistant is "
                            "told to flag a provision that would have "
                            "depended on one rather than assuming it is "
                            "clear."
                        ),
                    ),
                    ReleaseItem(
                        title="Issue readiness now checks that, too",
                        body=(
                            "\"Research complete\" used to mean the run "
                            "ended, even if three of four areas had failed. "
                            "It now means every area actually finished at "
                            "some point across your rounds — so a missing "
                            "one blocks readiness and tells you which, and "
                            "pressing Research again to fill the gap "
                            "restores it. A later round that fails never "
                            "takes away coverage an earlier one established."
                        ),
                    ),
                    ReleaseItem(
                        title="You can see what the agents are searching for",
                        body=(
                            "The live board showed \"Searching the web…\" "
                            "with no query attached, because of how the "
                            "search tool was being called. The actual "
                            "queries and the pages being read now appear as "
                            "they happen — in research, in Final QC, and in "
                            "chat. The same change fixed research areas that "
                            "would occasionally die part-way through a long "
                            "run."
                        ),
                    ),
                    ReleaseItem(
                        title="A dropped connection no longer freezes the board",
                        body=(
                            "A research run takes half an hour, and if the "
                            "connection carrying its progress dropped, the "
                            "board simply stopped moving — the run carried "
                            "on, but the only way to see it again was to "
                            "restart the app. It now reconnects on its own "
                            "and picks up where it left off, without the "
                            "board ever jumping backwards."
                        ),
                    ),
                ),
            ),
            ReleaseSection(
                title="What things cost",
                items=(
                    ReleaseItem(
                        title="A long conversation costs much less",
                        body=(
                            "Every chat turn re-sends the conversation so "
                            "far, and the app was paying full price for all "
                            "of it every time, despite claiming otherwise. "
                            "It now re-uses what it already sent, so only "
                            "your newest exchange is charged at full rate. "
                            "The saving grows with the length of the "
                            "conversation."
                        ),
                    ),
                    ReleaseItem(
                        title="Work you paid for and did not get is counted",
                        body=(
                            "A research round where every area failed, and "
                            "one you stopped part-way, both cost real money "
                            "and neither reached the meter. They do now. "
                            "Stopping a chat reply is the one case where the "
                            "token count is itself an estimate — the reply "
                            "is cut off before the provider sends its final "
                            "tally — so it is shown separately and never "
                            "mixed into the reported numbers."
                        ),
                    ),
                    ReleaseItem(
                        title="The estimate matches the real rate card",
                        body=(
                            "Longer-lived reuse of a prompt costs more to "
                            "set up than short-lived reuse, and the meter "
                            "was charging the cheaper rate for both. Each is "
                            "now priced as billed. Saved review reports keep "
                            "the rate card they were actually priced under "
                            "rather than being quietly re-costed."
                        ),
                    ),
                ),
            ),
            ReleaseSection(
                title="The window stops freezing",
                items=(
                    ReleaseItem(
                        title="Exporting and importing no longer lock the app",
                        body=(
                            "Exporting a large section, importing a master "
                            "or a template, and opening a project each did "
                            "seconds of work that stopped everything else — "
                            "including the chat you were in the middle of. "
                            "They now take a snapshot and get out of the "
                            "way, so the rest of the app keeps answering."
                        ),
                    ),
                    ReleaseItem(
                        title="Stop responds immediately",
                        body=(
                            "The stop button could sit dead for several "
                            "seconds on exactly the reply you most wanted to "
                            "stop — a long one that had just read a large "
                            "PDF. It answers straight away now, and a reply "
                            "stopped before it was sent costs nothing at "
                            "all."
                        ),
                    ),
                    ReleaseItem(
                        title="Stopping a run cannot disturb the next one",
                        body=(
                            "Stopping research or Final QC and immediately "
                            "starting again could let the stopped run's "
                            "ending land on its replacement — cancelling a "
                            "run you never stopped, or showing its progress "
                            "as the new one's. A run now ends in one piece, "
                            "and the tutorial can no longer be torn down "
                            "while it is still preparing a chapter."
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
