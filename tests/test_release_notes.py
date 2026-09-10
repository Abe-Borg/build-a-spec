"""Release notes: the changelog shipped inside the build.

Covers the three things that can go wrong in a way the user would notice:
the wrong people get the What's-new modal (a fresh install seeing the back
catalogue, or an upgrader seeing nothing), a corrupt state file breaking the
launch, and the shipped notes drifting out of step with ``settings.VERSION``
— which would put an empty modal in front of every user who updates.
"""
from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from backend import release_notes, settings, updates
from backend.app import create_app


@pytest.fixture()
def state_file(tmp_path, monkeypatch):
    """Point the update state file (which carries last_seen_version) at tmp."""
    path = tmp_path / "update_check.json"
    monkeypatch.setenv(updates.ENV_STATE_PATH, str(path))
    return path


def _client(*, ran_before: bool) -> TestClient:
    app = create_app()
    app.state.ran_before = ran_before
    return TestClient(app)


def _shipped_versions() -> list[str]:
    """Every entry in the shipped changelog, newest first.

    The back-catalogue grows every release, so tests assert against this
    rather than against a literal list — otherwise each release breaks them.
    """
    return [note.version for note in release_notes.RELEASE_NOTES]


# --------------------------------------------------------------------------
# The entry itself
# --------------------------------------------------------------------------


def test_the_shipped_notes_describe_the_shipped_version():
    """A release whose notes were not written ships an empty modal.

    The whole feature is "tell the user what changed when they update", so
    a version bump without a matching entry is a release-blocking mistake,
    not a cosmetic one.
    """
    note = release_notes.note_for(settings.VERSION)
    assert note is not None, (
        f"backend/settings.py VERSION is {settings.VERSION} but "
        "backend/release_notes.py has no entry for it — add one before "
        "tagging the release."
    )
    assert note.headline.strip()
    assert note.summary.strip()
    assert note.sections, "an entry with no sections renders as a blank modal"
    for section in note.sections:
        assert section.items, f"section {section.title!r} has no items"
        for item in section.items:
            assert item.title.strip() and item.body.strip()


def test_entries_are_ordered_newest_first_and_parse_as_versions():
    versions = [note.version for note in release_notes.RELEASE_NOTES]
    keys = [updates.parse_version(v) for v in versions]  # raises if malformed
    assert keys == sorted(keys, reverse=True)
    assert len(set(versions)) == len(versions), "duplicate version entries"


# --------------------------------------------------------------------------
# Who gets told what
# --------------------------------------------------------------------------


def test_an_upgrade_from_the_last_release_is_announced():
    """Upgrading off the immediately-previous release announces just this one."""
    previous = release_notes.RELEASE_NOTES[1].version
    pending = release_notes.resolve_pending(
        current=settings.VERSION, last_seen=previous, ran_before=True
    )
    assert [n.version for n in pending] == [settings.VERSION]


def test_a_fresh_install_is_told_nothing():
    """No state at all means nobody has run this app — not an upgrade."""
    assert (
        release_notes.resolve_pending(
            current=settings.VERSION, last_seen="", ran_before=False
        )
        == ()
    )


def test_an_upgrade_off_a_build_without_the_marker_gets_the_full_history():
    """1.0.0 predates ``last_seen_version``, so there is no record to read.

    ``ran_before`` (an update state file existed at boot) is the only signal
    that distinguishes those users from a first-ever launch.
    """
    pending = release_notes.resolve_pending(
        current=settings.VERSION, last_seen="", ran_before=True
    )
    assert [n.version for n in pending] == _shipped_versions()


def test_the_current_version_is_announced_only_once():
    assert (
        release_notes.resolve_pending(
            current=settings.VERSION,
            last_seen=settings.VERSION,
            ran_before=True,
        )
        == ()
    )


def test_notes_never_describe_a_version_this_build_is_not():
    """An entry newer than the running build must not leak into the modal."""
    pending = release_notes.notes_between(after="0.0.1", through="1.0.0")
    assert all(
        updates.parse_version(n.version) <= updates.parse_version("1.0.0")
        for n in pending
    )


def test_a_corrupt_last_seen_version_degrades_instead_of_raising():
    """A cosmetic modal must never be able to stop the app starting."""
    pending = release_notes.resolve_pending(
        current=settings.VERSION, last_seen="not-a-version", ran_before=True
    )
    # An unreadable bound degrades to "no bound", i.e. the full history.
    assert [n.version for n in pending] == _shipped_versions()


# --------------------------------------------------------------------------
# The endpoints
# --------------------------------------------------------------------------


def test_launch_check_announces_then_stays_quiet(state_file):
    """The modal opens once per update, not once per launch."""
    client = _client(ran_before=True)

    first = client.get("/api/release-notes").json()
    assert first["ok"] is True
    assert first["pending"] is True
    assert first["current"] == settings.VERSION
    assert [e["version"] for e in first["entries"]] == _shipped_versions()

    assert client.post("/api/release-notes/seen").json() == {
        "ok": True,
        "last_seen": settings.VERSION,
    }
    assert json.loads(state_file.read_text())["last_seen_version"] == (
        settings.VERSION
    )

    second = client.get("/api/release-notes").json()
    assert second["pending"] is False
    assert second["entries"] == []
    assert second["last_seen"] == settings.VERSION


def test_a_fresh_install_is_not_shown_the_modal(state_file):
    payload = _client(ran_before=False).get("/api/release-notes").json()
    assert payload["pending"] is False
    assert payload["entries"] == []


def test_settings_can_reopen_the_notes_after_they_are_dismissed(state_file):
    """?all=true is the Settings button — it must work when nothing is due."""
    client = _client(ran_before=True)
    client.post("/api/release-notes/seen")

    payload = client.get("/api/release-notes?all=true").json()
    assert [e["version"] for e in payload["entries"]] == _shipped_versions()
    # Not "pending": the user asked, the app did not volunteer.
    assert payload["pending"] is False


def test_the_endpoint_survives_a_corrupt_state_file(state_file):
    state_file.write_text("{not json at all")
    payload = _client(ran_before=True).get("/api/release-notes").json()
    assert payload["ok"] is True


def test_seen_marker_does_not_disturb_the_update_throttle(state_file):
    """Both live in one file; recording one must not erase the other."""
    updates.save_state(state_file, {"last_check": "2026-07-28T00:00:00"})
    _client(ran_before=True).post("/api/release-notes/seen")
    saved = json.loads(state_file.read_text())
    assert saved["last_check"] == "2026-07-28T00:00:00"
    assert saved["last_seen_version"] == settings.VERSION


# --------------------------------------------------------------------------
# Release-time renderings
# --------------------------------------------------------------------------


def test_manifest_summary_round_trips_through_the_updater(tmp_path):
    """``latest.json`` is what a not-yet-updated app reads, so the summary
    has to survive the maker → ``parse_manifest`` path the updater uses."""
    import sys

    sys.path.insert(0, str(settings.REPO_ROOT / "packaging" / "windows"))
    import make_manifest

    installer = tmp_path / "BuildASpecSetup.exe"
    installer.write_bytes(b"pretend installer")
    summary = release_notes.manifest_summary(settings.VERSION)

    manifest = make_manifest.build_manifest(
        version=settings.VERSION,
        installer=installer,
        url=(
            "https://github.com/Abe-Borg/build-a-spec/releases/download/"
            f"v{settings.VERSION}/BuildASpecSetup.exe"
        ),
        notes=summary,
        published_at="2026-07-28",
    )
    info = updates.parse_manifest(manifest)
    assert info.notes == summary
    assert settings.VERSION in info.notes
    # Small enough that the updater's 64 KiB manifest cap is never in play.
    assert len(json.dumps(manifest)) < updates.MAX_MANIFEST_BYTES


def test_manifest_summary_degrades_for_an_unknown_version():
    text = release_notes.manifest_summary("9.9.9")
    assert "9.9.9" in text


def test_markdown_notes_render_every_item_for_the_release_page():
    md = release_notes.markdown_notes(settings.VERSION)
    note = release_notes.note_for(settings.VERSION)
    assert md.startswith(f"## What's new in {settings.VERSION}")
    for section in note.sections:
        assert f"### {section.title}" in md
        for item in section.items:
            assert item.title in md
    assert release_notes.markdown_notes("9.9.9") == ""


# --------------------------------------------------------------------------
# A release covers every version that never got one of its own
# --------------------------------------------------------------------------
#
# The version bump and the release are separate acts here, and they have come
# apart repeatedly: 1.14.0, 1.16.0 and 1.18.0 were each bumped, merged and
# then superseded by the next bump without ever being tagged. Their work
# ships in the following installer, so the release page and ``latest.json``
# have to name it — those are the two surfaces a user reads BEFORE deciding
# to update, and the in-app modal they see afterwards already spans the gap
# on its own through ``last_seen_version``. A release page that describes
# less than the build contains is the thing these pin.


def _span_versions(text: str) -> list[str]:
    """Versions a rendered body or summary actually names, in order."""
    return re.findall(
        r"^(?:## What's new in|## Also in this release —|Also includes"
        r"|Build-a-Spec) ([0-9][^ :—]*)",
        text,
        re.M,
    )


def test_the_release_page_covers_every_version_that_never_shipped():
    current = release_notes.RELEASE_NOTES[0].version
    skipped = release_notes.RELEASE_NOTES[1]
    last_released = release_notes.RELEASE_NOTES[2].version

    md = release_notes.markdown_notes(current, after=last_released)

    assert md.startswith(f"## What's new in {current}")
    assert _span_versions(md) == [current, skipped.version]
    # Named is not enough — the skipped version's actual work has to be on
    # the page, which is the whole reason it is there.
    assert skipped.headline in md
    for section in skipped.sections:
        for item in section.items:
            assert item.title in md, item.title


def test_the_manifest_summary_names_every_version_in_the_span():
    """``latest.json``'s notes are what a not-yet-updated app shows in the
    update pill. It describes what you would be getting, so it carries every
    unreleased version's items — headline and titles, not whole summaries."""
    current = release_notes.RELEASE_NOTES[0].version
    skipped = release_notes.RELEASE_NOTES[1]
    last_released = release_notes.RELEASE_NOTES[2].version

    summary = release_notes.manifest_summary(current, after=last_released)

    assert _span_versions(summary) == [current, skipped.version]
    assert skipped.headline in summary
    for section in skipped.sections:
        for item in section.items:
            assert item.title in summary, item.title
    # Kept short: the earlier entry contributes titles, never its paragraph.
    assert skipped.summary not in summary


def test_the_widest_possible_span_still_fits_the_update_manifest():
    """A span is bounded by the changelog itself, and ``latest.json`` has a
    hard 64 KiB ceiling the updater enforces — blowing it would break the
    update path rather than merely making a tooltip long."""
    current = release_notes.RELEASE_NOTES[0].version
    widest = release_notes.manifest_summary(
        current, after=release_notes.EARLIEST_KNOWN_VERSION
    )
    assert len(_span_versions(widest)) == len(release_notes.RELEASE_NOTES)
    assert len(widest.encode("utf-8")) < updates.MAX_MANIFEST_BYTES / 2


def test_a_release_without_a_bound_renders_exactly_what_it_always_did():
    current = release_notes.RELEASE_NOTES[0].version
    assert release_notes.markdown_notes(current, after="") == (
        release_notes.markdown_notes(current)
    )
    assert release_notes.manifest_summary(current, after="") == (
        release_notes.manifest_summary(current)
    )
    assert "## Also in this release" not in release_notes.markdown_notes(current)
    assert "Also includes" not in release_notes.manifest_summary(current)


@pytest.mark.parametrize(
    "bound",
    [
        "garbage",
        "v1.17.0",   # a raw git tag: the caller must strip the v, not us
        "9.9.9",     # above every entry
        "   ",
    ],
)
def test_an_unusable_bound_falls_back_to_one_entry_not_the_back_catalogue(bound):
    """``notes_between`` reads an unparseable bound as "no lower bound",
    which is right for a modal that must never fail to open and exactly
    wrong here — it would empty the whole changelog onto one release page.
    Every ambiguous bound collapses to the single entry instead."""
    current = release_notes.RELEASE_NOTES[0].version
    assert release_notes.markdown_notes(current, after=bound) == (
        release_notes.markdown_notes(current)
    )
    assert release_notes.notes_for_release(current, after=bound) == (
        release_notes.note_for(current),
    )


def test_a_bound_at_the_released_version_still_describes_it():
    """``after`` equal to the version selects nothing at all. The release
    still has to describe itself."""
    current = release_notes.RELEASE_NOTES[0].version
    assert release_notes.notes_for_release(current, after=current) == (
        release_notes.note_for(current),
    )
    assert release_notes.markdown_notes(current, after=current).strip()


def test_the_renderer_covers_the_span_the_workflow_hands_it(tmp_path):
    """End to end through the script the release workflow actually calls,
    including the leading ``v`` a git tag arrives with."""
    import sys

    sys.path.insert(0, str(settings.REPO_ROOT / "packaging" / "windows"))
    import render_release_notes

    current = release_notes.RELEASE_NOTES[0].version
    skipped = release_notes.RELEASE_NOTES[1].version
    last_released = release_notes.RELEASE_NOTES[2].version
    notes_out = tmp_path / "release-notes.txt"
    body_out = tmp_path / "release-body.md"

    code = render_release_notes.main(
        [
            "--version", current,
            "--notes-out", str(notes_out),
            "--body-out", str(body_out),
            "--since", f"v{last_released}",
        ]
    )

    assert code == 0
    body = body_out.read_text(encoding="utf-8")
    assert _span_versions(body) == [current, skipped]
    assert _span_versions(notes_out.read_text(encoding="utf-8")) == [current, skipped]
    # The install/SmartScreen instructions still follow the notes.
    assert "## Install (Windows)" in body


def test_the_release_workflow_asks_git_for_the_previous_release():
    """release.yml is never exercised by CI — a tag build is the first time
    it runs — so the wiring is pinned here instead. All three parts matter:
    without the tags there is nothing to describe against, without the
    lookup there is no bound, and without the argument the renderer is back
    to describing one version."""
    workflow = (
        settings.REPO_ROOT / ".github" / "workflows" / "release.yml"
    ).read_text(encoding="utf-8")

    assert "fetch-depth: 0" in workflow, "a depth-1 checkout fetches no tags"
    assert "git describe --tags --abbrev=0" in workflow
    assert "since=$since" in workflow, "the resolved bound is never published"
    assert '"--since", $since' in workflow, "the renderer is never given it"
    # git describe exits nonzero when it finds nothing, and a pwsh step exits
    # on the last native exit code: a first release must not fail the build.
    assert "$global:LASTEXITCODE = 0" in workflow


def test_the_renderer_warns_only_when_the_bound_is_unusable(tmp_path, capsys):
    """A span of one entry is the ORDINARY release — the previous release is
    the version below this one — so warning on span length would cry wolf
    every time and train whoever cuts the release to ignore it. The bound
    nobody can parse is the actual mistake, and it is the only one that
    speaks up."""
    import sys

    sys.path.insert(0, str(settings.REPO_ROOT / "packaging" / "windows"))
    import render_release_notes

    current = release_notes.RELEASE_NOTES[0].version
    just_below = release_notes.RELEASE_NOTES[1].version

    def render(since: str) -> str:
        code = render_release_notes.main(
            [
                "--version", current,
                "--notes-out", str(tmp_path / "n.txt"),
                "--body-out", str(tmp_path / "b.md"),
                "--since", since,
            ]
        )
        assert code == 0, "an unusable bound falls back; it never fails the build"
        return capsys.readouterr().err

    assert render(just_below) == ""
    assert "WARNING" in render("nonsense")
