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
        version="1.14.0",
        date="2026-08-21",
        headline="Your master keeps its formatting AND becomes editable",
        summary=(
            "Importing a Word spec used to force a choice: keep your firm's "
            "formatting and barely be able to edit anything, or edit freely "
            "and export a document that no longer looks like yours. That "
            "choice is gone. Every import now keeps the header, footer, "
            "fonts, styles and page setup exactly as they were, and is "
            "fully editable at the same time."
        ),
        sections=(
            ReleaseSection(
                title="Importing an existing spec",
                items=(
                    ReleaseItem(
                        title="Import no longer asks — it just works",
                        body=(
                            "The import dialog is gone. Your document keeps "
                            "its header, footer, fonts, styles, numbering "
                            "and page setup, and you and the assistant can "
                            "change the content and its order freely: "
                            "rewrite provisions, add and remove articles "
                            "and paragraphs, reorder anything. The exported "
                            "Word file carries your formatting with the new "
                            "content in it."
                        ),
                    ),
                    ReleaseItem(
                        title="A provision you did not touch comes back "
                        "exactly as it was",
                        body=(
                            "Untouched text is copied straight out of your "
                            "original, character for character. A provision "
                            "you edited keeps its style, font, size, indent "
                            "and numbering — only the words change. Bold or "
                            "italic applied to part of a sentence you "
                            "rewrote is the one thing that may not survive, "
                            "because the words it was attached to are gone."
                        ),
                    ),
                    ReleaseItem(
                        title="Tables, pictures and embedded objects are "
                        "preserved blocks",
                        body=(
                            "They come through your export exactly as they "
                            "arrived, so their contents cannot be retyped "
                            "here. You can still move them, delete them and "
                            "mark them reviewed, and hovering the lock chip "
                            "explains why. A schedule table is now one "
                            "block instead of one row per line, so deleting "
                            "it is a single action."
                        ),
                    ),
                    ReleaseItem(
                        title="No more waiting for permissions, and no more "
                        "read-only documents",
                        body=(
                            "The permission analysis that could sit there "
                            "for minutes on a large master — freezing every "
                            "edit while it ran — is gone entirely. So is "
                            "the whole class of frozen document: a file "
                            "with tracked changes, macros, an embedded "
                            "spreadsheet or enforced protection imports and "
                            "edits like any other."
                        ),
                    ),
                    ReleaseItem(
                        title="A stale section number in the footer is "
                        "flagged",
                        body=(
                            "Headers and footers are carried through "
                            "untouched, which means adapting a master can "
                            "leave the original section number printed on "
                            "every page. When the footer disagrees with the "
                            "section you are drafting, the issues list says "
                            "so — update it in Word before issuing."
                        ),
                    ),
                ),
            ),
            ReleaseSection(
                title="Under the hood",
                items=(
                    ReleaseItem(
                        title="Attached documents are handled as data "
                        "everywhere they are read",
                        body=(
                            "A document you attach is somebody else's file — "
                            "a vendor PDF, a client's standard. The research "
                            "and review teams already treated one as material "
                            "to read rather than instructions to follow, no "
                            "matter what its text says. The chat assistant "
                            "now does too."
                        ),
                    ),
                    ReleaseItem(
                        title="AI Generalize fails less often",
                        body=(
                            "Turning a section into a reusable template asked "
                            "the assistant for its answer as plain text the "
                            "app then had to pick apart, which occasionally "
                            "came back in a shape it could not read and sent "
                            "you back to try again. The answer now arrives in "
                            "a structured form, so that class of retry is "
                            "gone. The preview you approve, and the checks "
                            "behind it, are unchanged."
                        ),
                    ),
                ),
            ),
        ),
    ),
    ReleaseNote(
        version="1.13.0",
        date="2026-08-21",
        headline="Importing a spec finally works the way you meant it",
        summary=(
            "Importing an office master is no longer a fight. The import "
            "asks whether you want a fully editable starting point — now "
            "the default — or a byte-exact preserved original; real Word "
            "auto-numbered masters arrive with their parts and articles "
            "recognized; and the assistant knows the rules of an imported "
            "document instead of telling you it is read-only. Separately, "
            "the documents you attach as background are now read by the "
            "research and review teams, not just the chat."
        ),
        sections=(
            ReleaseSection(
                title="Importing an existing spec",
                items=(
                    ReleaseItem(
                        title="The import asks what you mean by it",
                        body=(
                            "Use as a starting point — the recommended "
                            "default — makes the imported content fully "
                            "editable for you and the assistant, "
                            "immediately. Preserve original formatting "
                            "keeps the old contract: the exported file "
                            "stays a byte-exact copy of your upload, with "
                            "editing limited to what that promise allows. "
                            "Either way your exact original stays "
                            "downloadable and the redline against it keeps "
                            "working."
                        ),
                    ),
                    ReleaseItem(
                        title="Word's own numbering is finally read as structure",
                        body=(
                            "A master whose PART and article headings are "
                            "auto-numbered by Word used to come in as one "
                            "blob under a placeholder article, wearing a "
                            "'not a spec section' banner. The import now "
                            "reads the numbering definitions themselves, so "
                            "those masters arrive with their real parts and "
                            "articles."
                        ),
                    ),
                    ReleaseItem(
                        title="Edit freely is offered wherever it applies",
                        body=(
                            "The way out of read-only used to appear only "
                            "for a locked package. It now shows on every "
                            "preserved import — while permissions are still "
                            "being checked, when the package is frozen, and "
                            "on the ordinary master where most edits are "
                            "denied — and the assistant knows to suggest it "
                            "instead of presenting a dead end."
                        ),
                    ),
                    ReleaseItem(
                        title="One click adapts the whole starter",
                        body=(
                            "Adapt imported draft walks every imported "
                            "block against this project — keep, adapt, or "
                            "delete, article by article, with honest "
                            "statuses — the way Draft full section lays "
                            "down an empty one. The review queue also gains "
                            "a press-and-hold that confirms a whole PART at "
                            "once."
                        ),
                    ),
                    ReleaseItem(
                        title="Nothing is dropped silently any more",
                        body=(
                            "Content after END OF SECTION — a second "
                            "section in a combined file, most often — is "
                            "named and counted in the import notes instead "
                            "of vanishing. Import warnings now point at the "
                            "block they mean (its ref and id), not a line "
                            "number you cannot find. And the permission "
                            "check on a preserved import is much faster, "
                            "shows how far along it is, and skips entirely "
                            "for locked packages."
                        ),
                    ),
                ),
            ),
            ReleaseSection(
                title="The documents you attach",
                items=(
                    ReleaseItem(
                        title="Research and Final QC read them",
                        body=(
                            "An owner standard, a client guideline or a "
                            "basis-of-design you attach as background used "
                            "to be visible only in the chat. The research "
                            "agents and every Final QC specialist and "
                            "reviewer now read them in full — so a "
                            "requirement that exists only in the owner's "
                            "standard is no longer invisible to the "
                            "completeness check, and a provision written to "
                            "satisfy one is no longer argued down for want "
                            "of anything supporting it."
                        ),
                    ),
                    ReleaseItem(
                        title="A provision can credit an attached document",
                        body=(
                            "The source chip beside a provision now points "
                            "at an attached document as well as a research "
                            "finding, and the review flags a provision that "
                            "credits one which does not actually support it."
                        ),
                    ),
                    ReleaseItem(
                        title="Attached text is read as reference, never as instruction",
                        body=(
                            "An attached file is somebody else's content — "
                            "a vendor PDF, a client's standard — and it now "
                            "reaches every research and review agent. It is "
                            "handled as material to work from: an owner's "
                            "requirement, never a code authority, and never "
                            "a set of instructions to the app. Where one "
                            "conflicts with the governing code, that is "
                            "reported as a finding rather than quietly "
                            "resolved."
                        ),
                    ),
                ),
            ),
            ReleaseSection(
                title="Saved Final QC reviews",
                items=(
                    ReleaseItem(
                        title="They will ask to be re-run once",
                        body=(
                            "A Final QC report refuses to call itself "
                            "current when anything it was produced from "
                            "changes. Attaching or removing a background "
                            "document counts, and so does this release's "
                            "change to what an imported document tells the "
                            "reviewers — so a report saved in a project "
                            "file may read as out of date the first time "
                            "you open it. The report itself is kept and "
                            "stays readable; re-running brings it current."
                        ),
                    ),
                ),
            ),
        ),
    ),
    ReleaseNote(
        version="1.12.0",
        date="2026-08-19",
        headline="Final QC costs about half as much",
        summary=(
            "This is the first release since 1.9.1, so it also brings the "
            "two builds in between: a Final QC report that leads with what "
            "you need to act on, and a chat that reads your research and "
            "review results and tells you what they mean for the section. "
            "The headline change here is cost. The pre-issue review is the "
            "most expensive thing the app does, and most of that went on "
            "the stage that checks findings rather than the stage that "
            "finds them. Two changes cut a typical pass by roughly half. "
            "Nothing about the review itself was traded away: the same "
            "five specialists, the same panel of reviewers arguing against "
            "every finding, the same evidence rules and the same readiness "
            "gate."
        ),
        sections=(
            ReleaseSection(
                title="What Final QC costs",
                items=(
                    ReleaseItem(
                        title="The checking stage runs at half price",
                        body=(
                            "Every finding is handed to two or three "
                            "independent reviewers, and on a long section "
                            "that is nine calls in ten. Those reviewers are "
                            "now submitted together as one batch, which "
                            "Anthropic prices at half. They read the same "
                            "document, argue the same way and reach the same "
                            "verdicts — the only difference is that they "
                            "are queued rather than watched."
                        ),
                    ),
                    ReleaseItem(
                        title="The review panel shows progress instead of activity",
                        body=(
                            "Because the checking stage is now queued, the "
                            "Review Room can no longer show each reviewer "
                            "searching and reading as it happens. It reports "
                            "how many have returned instead, and every "
                            "finding still shows its own reviewers and their "
                            "votes. The first stage — the five "
                            "specialists reading your section — is "
                            "unchanged and still live."
                        ),
                    ),
                    ReleaseItem(
                        title="Each stage now thinks as hard as its job needs",
                        body=(
                            "A specialist reads your whole section cold and "
                            "has to work out what is wrong with it. A "
                            "reviewer is handed one finding and asked "
                            "whether it holds up. Those are not the same "
                            "job, and they were being given the same amount "
                            "of thinking time. The specialists are unchanged; "
                            "the reviewers now think proportionally, which is "
                            "where most of the remaining saving comes from."
                        ),
                    ),
                    ReleaseItem(
                        title="Saved reviews will ask to be re-run once",
                        body=(
                            "A Final QC report records exactly how it was "
                            "produced, and refuses to call itself current "
                            "when any of that changes — that is what "
                            "stops an old review being trusted for a "
                            "document it no longer describes. Because this "
                            "release changes how the review runs, a report "
                            "saved in a project file will read as out of "
                            "date the first time you open it. The report "
                            "itself is kept and stays readable; re-running "
                            "brings it current."
                        ),
                    ),
                ),
            ),
        ),
    ),
    ReleaseNote(
        version="1.11.0",
        date="2026-08-19",
        headline="The chat reads the research and the review — and briefs you",
        summary=(
            "Research and Final QC used to finish into a panel and wait for "
            "you to notice. Now the chat is briefed the moment either "
            "completes: it tells you how the findings affect your draft, "
            "lays out the changes it would make, and asks whether to "
            "proceed — and Final QC's findings are finally part of the "
            "conversation at all."
        ),
        sections=(
            ReleaseSection(
                title="The chat and your reviews",
                items=(
                    ReleaseItem(
                        title="The chat can finally discuss Final QC findings",
                        body=(
                            "Ask “what did the review find?” and the "
                            "chat now knows: every open finding, its "
                            "severity, whether its fix is verified, and "
                            "whether the review still matches the document. "
                            "Before, the review lived only in the panel and "
                            "the chat had never heard of it."
                        ),
                    ),
                    ReleaseItem(
                        title="A brief the moment research or Final QC finishes",
                        body=(
                            "When a run completes, the chat sends itself one "
                            "ordinary turn: how the findings bear on your "
                            "current draft, the additions, edits and "
                            "deletions it would make, and a question — "
                            "do you want to proceed? Reply chips carry the "
                            "yes and the not-yet. It proposes; it never "
                            "applies anything until you say so. Switch it "
                            "off with BUILD_A_SPEC_AUTO_DEBRIEF=0 if you "
                            "prefer completions to land silently."
                        ),
                    ),
                    ReleaseItem(
                        title="Approve QC fixes in chat, keep the audit trail",
                        body=(
                            "Say “yes, apply the verified safe fixes” "
                            "and the exact panel-verified operations are "
                            "applied — the same validated machinery as "
                            "the panel's Apply button, one undo step, and "
                            "the audit report records each finding as "
                            "applied. The chat never rewrites a fix in its "
                            "own words, and disputed findings stay yours to "
                            "adjudicate in the panel."
                        ),
                    ),
                ),
            ),
        ),
    ),
    ReleaseNote(
        version="1.10.0",
        date="2026-08-19",
        headline="The Final QC report gets to the point",
        summary=(
            "The Final QC Word report was an audit transcript pretending to "
            "be a memo — a five-lens review of one section ran past a "
            "hundred pages, most of it repeated reviewer notes, per-seat "
            "billing counters, and the same web addresses printed again at "
            "every mention. The report now leads with what needs your "
            "decision and condenses the rest, while the JSON download keeps "
            "every persisted detail for the record."
        ),
        sections=(
            ReleaseSection(
                title="Final QC reporting",
                items=(
                    ReleaseItem(
                        title="Reviewer panels read as one table, not three essays",
                        body=(
                            "Every verifier seat still appears with its "
                            "vote, severity, and fix verdict — now as one "
                            "row in a table. When a panel agrees, one "
                            "representative note speaks for it instead of "
                            "three near-identical restatements; "
                            "disagreements, failed seats, and refutation "
                            "evidence still print in full, because those "
                            "are the parts a human has to read."
                        ),
                    ),
                    ReleaseItem(
                        title="Every source is listed once, in the Evidence Register",
                        body=(
                            "Web sources used to reprint their full address "
                            "at every mention — the same link could appear "
                            "fifty times. Findings, checks, and panels now "
                            "cite short evidence numbers (E-001) that "
                            "resolve in Appendix B, which also now covers "
                            "disputed candidates and numbers entries the "
                            "same way the report body does."
                        ),
                    ),
                    ReleaseItem(
                        title="Less boilerplate, same audit trail",
                        body=(
                            "Empty telemetry no longer prints “No record "
                            "was persisted” lines, per-seat token and cost "
                            "accounting moved to the JSON export, refuted "
                            "candidates keep their claim and refutation "
                            "basis without an unusable operation dump, and "
                            "proposed fixes render as the actual replacement "
                            "text instead of raw JSON. Nothing left the "
                            "audit record — the JSON export remains the "
                            "lossless companion, and the Word report says "
                            "so."
                        ),
                    ),
                ),
            ),
        ),
    ),
    ReleaseNote(
        version="1.9.1",
        date="2026-08-18",
        headline="The update button installs the update",
        summary=(
            "A fix for the one button whose whole job is to keep the app "
            "current. Checking for updates would find a new version and "
            "then send you to a control that was not there, and on the "
            "second launch of any day the offer went missing on its own. "
            "You can now install straight from where you checked."
        ),
        sections=(
            ReleaseSection(
                title="Keeping the app current",
                items=(
                    ReleaseItem(
                        title="Install from where you checked",
                        body=(
                            "Help \u2192 About \u2192 Check for updates used to "
                            "tell you a new version existed and send you to "
                            "the header to install it \u2014 which often showed "
                            "nothing at all, leaving no way to actually get "
                            "the update. It now shows the new version, what "
                            "is in it, and an Install button, right where "
                            "you asked."
                        ),
                    ),
                    ReleaseItem(
                        title="The offer stops disappearing after a restart",
                        body=(
                            "The app asks GitHub about new versions at most "
                            "once a day. It used to forget the answer in "
                            "between, so the second time you opened the app "
                            "on the same day the update notice vanished "
                            "until tomorrow. It now remembers what it found, "
                            "without asking again."
                        ),
                    ),
                    ReleaseItem(
                        title="Installing says it is working, and says why if it is not",
                        body=(
                            "Downloading an installer takes as long as it "
                            "takes, and the button used to sit there looking "
                            "dead the whole time. It now shows progress and "
                            "cannot be double-clicked. A download that fails "
                            "partway reports the actual reason instead of an "
                            "unhelpful internal error."
                        ),
                    ),
                ),
            ),
        ),
    ),
    ReleaseNote(
        version="1.9.0",
        date="2026-08-18",
        headline="Edit what you import, and a tour that just shows you",
        summary=(
            "An imported Word section is no longer something you can only "
            "look at. Edit freely hands you the ordinary editor back, a "
            "locked import finally says why it is locked, and outlines that "
            "used to arrive folded into themselves now come in the shape "
            "they were written in. Drafting a whole section asks what it "
            "needs to know before it writes a hundred provisions, a second "
            "research round stops paying for the first one over again, and "
            "the guided tour asks nothing of you at all. Starting a new "
            "session now genuinely leaves nothing of the old one behind."
        ),
        sections=(
            ReleaseSection(
                title="Imported Word specs",
                items=(
                    ReleaseItem(
                        title="Edit freely: import a spec and actually edit it",
                        body=(
                            "Until now an imported section was kept "
                            "deliberately restrictive so the file you "
                            "exported could be a byte-exact copy of the one "
                            "you uploaded, with only the wording changed. "
                            "That is the right default when you are handing "
                            "a marked-up master back to an office, and the "
                            "wrong one when you just want to work. Edit "
                            "freely drops that guarantee for a document and "
                            "gives you the ordinary editor back — headings, "
                            "structure, new articles, everything — and "
                            "exports a normal Word file in Build-a-Spec's "
                            "formatting instead. Your original upload stays "
                            "downloadable exactly as you sent it, and "
                            "Redline vs master still shows what you changed. "
                            "It applies to one document and cannot be "
                            "undone; importing the file again starts over."
                        ),
                    ),
                    ReleaseItem(
                        title="A read-only import now tells you why",
                        body=(
                            "Some Word files cannot be edited in place at "
                            "all: ones carrying tracked changes, macros, an "
                            "embedded Excel sheet or drawing, a digital "
                            "signature, or Restrict Editing turned on. The "
                            "app knew this and said nothing, so the section "
                            "simply arrived read-only with no explanation. "
                            "It now names the reason in the document panel "
                            "and tells you what to do about it. Tracked "
                            "changes are the one to watch for — the import "
                            "shows you the accepted text, so the file looks "
                            "perfectly clean while still being locked."
                        ),
                    ),
                    ReleaseItem(
                        title="Numbered outlines keep their shape",
                        body=(
                            "A master whose numbering starts one level in — "
                            "ordinary in an office template — came through "
                            "folded into itself: the first article swallowed "
                            "every article below it, and stray blocks piled "
                            "up under an invented \"IMPORTED CONTENT\" "
                            "heading. Articles that are siblings in your file "
                            "now arrive as siblings. Documents that already "
                            "imported correctly are parsed exactly as before."
                        ),
                    ),
                ),
            ),
            ReleaseSection(
                title="Drafting",
                items=(
                    ReleaseItem(
                        title="A full draft asks before it writes",
                        body=(
                            "\"Draft full section\" would happily write a "
                            "whole section on a blank project, confidently "
                            "inventing what it did not know. It now needs "
                            "three things first — the section number and "
                            "title, what kind of facility this is, and the "
                            "country — because those decide what the "
                            "document is, what every defaulted provision is "
                            "defended by, and which code family and units "
                            "apply. Press the button anyway and the turn "
                            "collects exactly what is missing, with a "
                            "recommended answer for each; it will not draft "
                            "that turn. Nothing else about drafting changed."
                        ),
                    ),
                    ReleaseItem(
                        title="The blank page points you at the ways in",
                        body=(
                            "The empty document panel used to offer a small "
                            "form for typing your own first article. It is "
                            "gone. Start by talking to the assistant, "
                            "pressing \"Draft full section\", importing a "
                            "master, or starting from a template; adding an "
                            "article by hand still works everywhere else, "
                            "once the page has something on it."
                        ),
                    ),
                ),
            ),
            ReleaseSection(
                title="Research",
                items=(
                    ReleaseItem(
                        title="A second round stops paying for the first",
                        body=(
                            "Pressing Research again re-ran every area from "
                            "scratch, asking questions the session had "
                            "already answered and costing about what the "
                            "first round cost. Each area that runs is now "
                            "briefed on what is already established for it "
                            "and told to report only what is new, changed or "
                            "corrected — and to spend its searches "
                            "re-checking anything still marked unverified."
                        ),
                    ),
                    ReleaseItem(
                        title="Fill only the gaps",
                        body=(
                            "When some areas finished and others failed, you "
                            "can now retry just the ones that never "
                            "completed instead of paying for a full round. "
                            "Everything already found is kept, and closing "
                            "the last gap restores issue readiness."
                        ),
                    ),
                    ReleaseItem(
                        title="A corrected project is researched from scratch",
                        body=(
                            "If you change the city, jurisdiction or client "
                            "after a round has run, the next round is not "
                            "briefed on the old project's findings — a "
                            "re-run you asked for precisely because the "
                            "project moved must not skip the requirements it "
                            "exists to find."
                        ),
                    ),
                ),
            ),
            ReleaseSection(
                title="Final QC",
                items=(
                    ReleaseItem(
                        title="The report downloads work again",
                        body=(
                            "After applying a fix — the most ordinary thing "
                            "to do with a review — downloading the Word or "
                            "JSON report went quiet and did nothing for "
                            "minutes on a large imported master. It was "
                            "waiting out a permission check it never needed. "
                            "Both downloads now answer immediately, show a "
                            "preparing state, and tell you what went wrong "
                            "if it does. When that check is still running, "
                            "the export says so on its front page rather "
                            "than recording a guess as fact."
                        ),
                    ),
                    ReleaseItem(
                        title="The report lives with the report",
                        body=(
                            "The Final QC downloads were also sitting in the "
                            "document panel's Export menu, which exports "
                            "your specification. They are now only where the "
                            "review is — the Final QC drawer and the full "
                            "report window."
                        ),
                    ),
                ),
            ),
            ReleaseSection(
                title="What things cost",
                items=(
                    ReleaseItem(
                        title="A failed audit files its receipt",
                        body=(
                            "The older compliance audit could pay for a "
                            "response, fail to make sense of it, and say "
                            "nothing to the meter. It now bills what it "
                            "spent however the response was unusable — the "
                            "same rule research and Final QC already "
                            "followed. A full pass over every rate in the "
                            "meter found nothing else owing."
                        ),
                    ),
                    ReleaseItem(
                        title="The cache line says what it is",
                        body=(
                            "The savings figure in Settings counts the "
                            "discount on re-used prompt material. Writing "
                            "that material is itself billed, and is already "
                            "in the estimate above it, so the line now calls "
                            "itself the read discount instead of quietly "
                            "taking credit for a net saving."
                        ),
                    ),
                ),
            ),
            ReleaseSection(
                title="The guided tour",
                items=(
                    ReleaseItem(
                        title="It asks nothing of you",
                        body=(
                            "The tour set homework: try a guided answer, "
                            "fill in this profile, rearrange two blocks "
                            "before Continue will light up. It is now a "
                            "fixed track you watch from start to finish. "
                            "Every control it points at is still live if you "
                            "want to try one, and Continue is always "
                            "available."
                        ),
                    ),
                    ReleaseItem(
                        title="No pausing, and no way to get stuck",
                        body=(
                            "Pausing left your real project parked behind a "
                            "protected workspace indefinitely, so it is "
                            "gone: the tour either runs or ends and hands "
                            "your project straight back. Every screen it can "
                            "show now carries a labelled way out, Escape "
                            "asks whether to end rather than closing "
                            "something out from under you, and closing a "
                            "window opened on top of the tour no longer ends "
                            "the tour as well."
                        ),
                    ),
                    ReleaseItem(
                        title="It costs nothing to take",
                        body=(
                            "Everything the tour shows is bundled with the "
                            "app: no part of it calls the model, so it works "
                            "with no API key and cannot spend anything. The "
                            "starter prompts are now taught on the empty "
                            "screen where they actually appear, and are held "
                            "inert while the tour runs so a click cannot "
                            "spend money or disturb the practice document."
                        ),
                    ),
                ),
            ),
            ReleaseSection(
                title="Your work, and the machine it runs on",
                items=(
                    ReleaseItem(
                        title="\"New session\" actually means new",
                        body=(
                            "Starting a new session, opening a project, or "
                            "starting from a template left odds and ends of "
                            "the previous one on screen — a half-typed "
                            "standard, an unsent message, the review walk's "
                            "position, and, if a background request "
                            "happened to fail, the previous project's Final "
                            "QC findings. Everything is cleared at once now. "
                            "The project profile form no longer re-offers "
                            "the last project's city and client either, "
                            "which it could previously save into the new one."
                        ),
                    ),
                    ReleaseItem(
                        title="The local server only answers this app",
                        body=(
                            "Build-a-Spec runs a small server on your own "
                            "machine. Each launch now takes a private port "
                            "of its own and a fresh credential the window is "
                            "given at startup, so nothing else on your "
                            "computer — another program, a page in a "
                            "browser — can reach your session, and two "
                            "copies running at once cannot tread on each "
                            "other."
                        ),
                    ),
                    ReleaseItem(
                        title="Logs and traces clean up after themselves",
                        body=(
                            "The local activity log and trace files grew "
                            "without limit. Each launch now writes its own "
                            "folder, and old ones are pruned by age, count "
                            "and total size — never the run you are in, "
                            "another copy that is running, or recent "
                            "evidence from a crash. The diagnostics bundle "
                            "says exactly what it included and what it "
                            "truncated."
                        ),
                    ),
                    ReleaseItem(
                        title="Exports survive stray characters",
                        body=(
                            "A stray control character pasted in from "
                            "another document — invisible on screen, illegal "
                            "in a Word file — could produce an export that "
                            "would not open. Those characters are now shown "
                            "as a visible escape in the exported document "
                            "rather than silently deleted, so nothing is "
                            "lost and the file always opens."
                        ),
                    ),
                ),
            ),
        ),
    ),
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
                        title="Cheaper did not mean checking less",
                        body=(
                            "Five independent reviewers still read your "
                            "whole section, every defect that reaches "
                            "verification still faces a panel of two or "
                            "three refuters, and the audit report is as "
                            "complete as it was. What got cheaper was "
                            "sending your document once instead of on every "
                            "one of those calls, and a model that costs less "
                            "per word."
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
