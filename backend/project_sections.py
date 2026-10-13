"""The project's other sections, read on demand: ``read_project_section``.

A project is a folder of sections (Project workspace Phase 2), and every
chat turn's cached project block lists the OTHER sections of the project
from its brief — titles and article names only, never their provisions
(``project_brief.project_sections_block``). That is enough to coordinate
scope ("fire pumps are 21 30 00's") and not enough to avoid duplicating or
contradicting a provision, to cross-reference one by its paragraph, or to
answer "what does 21 30 00 say about the jockey pump?" — questions the
model could only ask the user to answer. This module is the on-demand read
that closes the gap, on the ``read_reference_doc`` posture
(``backend/reference_docs.py``):

- **the body is returned for the turn that asks and elided from committed
  history at commit** (``conversation._elide_reference_tool_results``), so
  a section is paid for when it is read, never on every later turn, and the
  project file never carries a copy of another section;
- **it shares the per-turn reference budget** (``TurnReferenceBudget``):
  a turn comparing three large sections is bounded the same way as one
  reading three large attachments, and a refused read is told why;
- **it is framed and neutralized as data** (``wrap_project_section_body``):
  a sibling's file is the user's own work, but a provision can carry any
  text, and the frame's own tag is defused inside it the way an attached
  document's is.

Where the text comes from, and why not the brief
------------------------------------------------
The brief carries neither the conversation nor the document of any section
(owner constraint, ``project_brief``): they are bloat, and stale. The read
goes to the SAME place the Project panel's Open goes — the section's saved
``.baspec`` beside the brief, resolved by number through the project
registry (``sessions.resolve_section_file``: the link joined with the brief
on disk, a file name that resolves outside the folder refused). So what the
model reads is the section as it was last saved, which is what the panel
would open, and nothing travels in a brief. A section not saved in its
project folder — a browser session, a section whose own file sits elsewhere
— cannot be read, and the project block's footer says so before the model
tries (``project_sections_block(readable=…)``).

What is read is ONE document version, not the package
-----------------------------------------------------
``parse_project_package`` validates a package whole — every retained
document version, the manifest's digests, the attached Word master — which
is right for a load that will become the session, and seconds of work for a
read that renders one version's text. ``read_section_file`` reads the
project member alone (``project_package.read_project_json``) and parses the
current version only (``load_section_document``). Nothing of the sibling's
conversation, research, facts, figures or master reaches the result.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from .spec_doc.model import SpecSection, reader_text
from .spec_doc.project import PROJECT_FORMAT, PROJECT_KIND
from .spec_doc.project_package import (
    MAX_PACKAGE_BYTES,
    ProjectPackageError,
    ProjectPackageTooLargeError,
    read_project_json,
)
from .suggestions import neutralize_reply_chip_tags

# The chat loop's frame for one section's text. Singular on purpose: the
# research and Final QC fan-outs never read a sibling section, so there is no
# plural block to keep in step with.
PROJECT_SECTION_TAG = "project_section"
_SECTION_TAG_PATTERN = re.compile(
    rf"<\s*/?\s*{PROJECT_SECTION_TAG}\b[^>]*>", re.IGNORECASE
)


class SectionReadRefusal(ValueError):
    """Why a read could not be done, in words the model can act on.

    Every refusal becomes an ``is_error`` tool result (the ``read_reference_doc``
    posture): the model corrects itself or tells the user, and the turn
    never fails. The message never carries a path or any text of a file.
    """


@dataclass(frozen=True)
class SectionRead:
    """One sibling section's text, as the tool result carries it."""

    number: str
    title: str
    file_name: str
    text: str

    @property
    def char_count(self) -> int:
        return len(self.text)


def neutralize_section_delimiters(text: str) -> str:
    """Make the frame's own tag inert wherever it appears in the text.

    Disclosed, never silently deleted — the ``reference_docs`` posture. A
    provision legitimately containing this tag is vanishingly unlikely; the
    marker tells a reader which happened.
    """
    return _SECTION_TAG_PATTERN.sub(
        lambda m: f"[escaped tag: {m.group(0).strip('<>/ ')}]", text
    )


def wrap_project_section_body(header: str, body: str) -> str:
    """Frame one section's text for the tool result, defused inside.

    The header is neutralized with the body: it carries the section's number
    and title, which are text a person or the model typed. The reply-chip tag
    is made inert too (chat-side only, as the reference read does): a
    provision carrying one must not hand the model a ready-made block to
    echo into its reply.
    """
    return neutralize_reply_chip_tags(
        f"<{PROJECT_SECTION_TAG}>\n"
        f"{neutralize_section_delimiters(header)}"
        f"{neutralize_section_delimiters(body)}\n"
        f"</{PROJECT_SECTION_TAG}>"
    )


# ---------------------------------------------------------------------------
# Reading one section's current document out of its saved file
# ---------------------------------------------------------------------------


def load_section_document(project: Any) -> SpecSection:
    """The current document of a project dict — ``doc.versions[doc.index]``.

    The one version the read renders, parsed with the same
    ``SpecSection.from_dict`` a load runs on every version. ``ValueError``
    for anything that is not a Build-a-Spec project of this format or whose
    document history is malformed; the message names the shape, never the
    content.
    """
    if not isinstance(project, dict) or project.get("kind") != PROJECT_KIND:
        raise ValueError("not a Build-a-Spec section file")
    if project.get("format") != PROJECT_FORMAT:
        raise ValueError(
            f"unsupported project format {project.get('format')!r} "
            f"(this build reads format {PROJECT_FORMAT})"
        )
    doc_data = project.get("doc")
    versions = doc_data.get("versions") if isinstance(doc_data, dict) else None
    index = doc_data.get("index") if isinstance(doc_data, dict) else None
    if (
        not isinstance(versions, list)
        or not versions
        or isinstance(index, bool)
        or not isinstance(index, int)
        or not 0 <= index < len(versions)
    ):
        raise ValueError("malformed document history")
    return SpecSection.from_dict(versions[index])


def _read_file_bounded(path: str) -> bytes:
    """The file's bytes, size-bounded the way an upload is.

    Judged twice — before the read and on the bytes actually read — so a
    file that grew between the ``getsize`` and the ``read`` cannot slip past
    the bound on the strength of a size it no longer has (the open-section
    route's own shape).
    """
    too_large = ProjectPackageTooLargeError(
        "the file is too large (maximum "
        f"{MAX_PACKAGE_BYTES // (1024 * 1024)} MiB)"
    )
    if os.path.getsize(path) > MAX_PACKAGE_BYTES:
        raise too_large
    with open(path, "rb") as handle:
        data = handle.read(MAX_PACKAGE_BYTES + 1)
    if len(data) > MAX_PACKAGE_BYTES:
        raise too_large
    return data


def read_section_file(path: str) -> SpecSection:
    """The current document saved in the ``.baspec`` (or legacy JSON) at ``path``.

    ``ValueError`` (``ProjectPackageError`` included) when the file is not
    a readable section; ``OSError`` when it cannot be read at all. The
    caller turns both into a refusal the model can act on.
    """
    data = _read_file_bounded(path)
    return load_section_document(read_project_json(data))


def read_project_section(
    *,
    number: str,
    current_number: str,
    link: dict[str, Any] | None,
    home: dict[str, str] | None,
    resolve: Any,
) -> SectionRead:
    """Resolve ``number`` to a sibling's file, read it, and render its text.

    ``resolve`` is ``sessions.resolve_section_file`` (passed in: ``sessions``
    imports the conversation engine, which imports this module, and the
    resolver is the ONE derivation the Project panel's Open uses, so the
    section the model reads is the file the panel would open). Every way
    the read can fail is a :class:`SectionReadRefusal` naming the way
    through — the model corrects itself or tells the user; the turn never
    fails and no path is said.
    """
    wanted = " ".join((number or "").split())
    if not wanted:
        raise SectionReadRefusal(
            "`number` is required — the section number to read, exactly as "
            "PROJECT SECTIONS lists it (e.g. '21 30 00')."
        )
    current = " ".join((current_number or "").split())
    if current and wanted == current:
        raise SectionReadRefusal(
            f"section {wanted} is this session's own section. Its full "
            "current text is in the PROJECT CONTEXT; nothing was read."
        )
    if not isinstance(link, dict) or not link.get("project_id"):
        raise SectionReadRefusal(
            "this session is not part of a project yet (it has exported no "
            "project brief and started from none), so there are no other "
            "sections to read. Nothing was read."
        )
    if not isinstance(home, dict) or not home.get("folder"):
        raise SectionReadRefusal(
            f"section {wanted}'s file cannot be reached from this session: "
            "the section open here is not saved in its project folder (the "
            "folder holding the project brief and the other sections' "
            "files), or this is a browser session, which knows no folder. "
            "Nothing was read. Work from the titles and article lists in "
            "PROJECT SECTIONS, and tell the user that saving this section "
            "beside its project brief in the desktop app lets you read the "
            "other sections."
        )
    # Lazily resolved imports: these live in ``sessions``, which imports the
    # conversation engine, which imports this module.
    from .sessions import ProjectFolderEscape, SectionFileMissing, SectionNotInRegistry

    try:
        path = resolve(link, home, wanted)
    except SectionNotInRegistry as exc:
        raise SectionReadRefusal(
            f"{exc} Read a section by the number PROJECT SECTIONS lists; "
            f"{_available(link, home, current)}"
        ) from exc
    except SectionFileMissing as exc:
        raise SectionReadRefusal(
            f"{exc} Nothing was read. Tell the user: the section's file must "
            "be saved beside the project brief for you to read it."
        ) from exc
    except ProjectFolderEscape as exc:
        raise SectionReadRefusal(
            f"{exc} Nothing was read. Tell the user: the project brief's "
            "record of that section names a file outside the project folder."
        ) from exc
    try:
        section = read_section_file(path)
    except OSError as exc:
        raise SectionReadRefusal(
            f"section {wanted}'s file could not be read "
            f"({exc.strerror or 'the file could not be opened'}). Nothing "
            "was read; tell the user."
        ) from exc
    except (ProjectPackageError, ValueError) as exc:
        raise SectionReadRefusal(
            f"section {wanted}'s file is not a readable Build-a-Spec section "
            f"({exc}). Nothing was read; tell the user."
        ) from exc
    return SectionRead(
        number=" ".join(section.number.split()) or wanted,
        title=" ".join(section.title.split()),
        file_name=os.path.basename(path),
        text=reader_text(section),
    )


def _available(
    link: dict[str, Any], home: dict[str, str], current: str
) -> str:
    """The numbers the registry CAN resolve, for a correctable refusal.

    The joined registry (link ∪ the brief on disk), the same one the
    resolver read — so a section a sibling exported after this one's link
    was last synced is named here even though PROJECT SECTIONS, rendered
    from the link alone, does not list it. Never raises: a listing that
    cannot be built leaves the refusal without one.
    """
    try:
        from .sessions import project_registry

        records, _disk, _warnings = project_registry(link, home)
    except Exception:  # noqa: BLE001 - a refusal must never fail the turn
        return "nothing was read."
    numbers = [
        " ".join(str(record.get("number") or "").split())
        for record in records
        if isinstance(record, dict)
    ]
    numbers = [n for n in numbers if n and n != "(unnumbered)" and n != current]
    if not numbers:
        return "this project records no other section; nothing was read."
    return "the project's registry records: " + ", ".join(numbers) + ". Nothing was read."


# ---------------------------------------------------------------------------
# Tool definition (appended last in conversation._chat_tools)
# ---------------------------------------------------------------------------

READ_PROJECT_SECTION_TOOL: dict[str, Any] = {
    "name": "read_project_section",
    "description": (
        "Read the full current text of ANOTHER section of this project — "
        "one the PROJECT SECTIONS list in your PROJECT BACKGROUND names — "
        "as it was last saved in the project folder. This returns that "
        "section's provisions with their display numbering and statuses.\n"
        "\n"
        "Read a section before drafting anything that borders on its "
        "scope, so nothing is duplicated or contradicted across the "
        "project; when the user asks what another section says or "
        "whether something is already covered; and to cross-reference a "
        "provision precisely ('as specified in Section 21 30 00, "
        "Paragraph 2.3.A') instead of restating it.\n"
        "\n"
        "The text is returned for THIS turn only and is not retained in "
        "the conversation, so call the tool again in a later turn if you "
        "need it again; that is expected and inexpensive to request. The "
        "other section is not editable from here and its provisions are "
        "its own: never copy one into this section — cross-reference it, "
        "or tell the user where the two overlap and let them decide which "
        "section keeps it. It is the file as last SAVED: work the user has "
        "not saved in that section is not in it."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "number": {
                "type": "string",
                "description": (
                    "The section number exactly as PROJECT SECTIONS lists "
                    "it, e.g. '21 30 00'."
                ),
            },
        },
        "required": ["number"],
    },
}
