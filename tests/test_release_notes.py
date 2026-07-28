"""Release notes: the changelog shipped inside the build.

Covers the three things that can go wrong in a way the user would notice:
the wrong people get the What's-new modal (a fresh install seeing the back
catalogue, or an upgrader seeing nothing), a corrupt state file breaking the
launch, and the shipped notes drifting out of step with ``settings.VERSION``
— which would put an empty modal in front of every user who updates.
"""
from __future__ import annotations

import json

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
