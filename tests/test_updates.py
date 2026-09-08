"""Updater tests — manifest parsing, version grammar, download integrity,
throttle state, and the make_manifest → parse_manifest round-trip.
Patterns ported from Spec Critic's ``tests/test_updates.py``."""
from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime

import pytest

from backend import updates


# ---------------------------------------------------------------------------
# Version grammar
# ---------------------------------------------------------------------------


def test_version_ordering_and_rc_semantics():
    assert updates.is_newer("0.5.1", "0.5.0")
    assert updates.is_newer("1.0.0", "0.9.9")
    assert updates.is_newer("1.0.0", "1.0.0rc2")
    assert updates.is_newer("1.0.0rc2", "1.0.0rc1")
    assert not updates.is_newer("0.5.0", "0.5.0")
    assert not updates.is_newer("0.4.9", "0.5.0")
    with pytest.raises(ValueError):
        updates.parse_version("v0.5.0")
    with pytest.raises(ValueError):
        updates.parse_version("0.5")


# ---------------------------------------------------------------------------
# Manifest parsing (security invariants)
# ---------------------------------------------------------------------------

_GOOD_MANIFEST = {
    "version": "9.9.9",
    "url": "https://example.com/BuildASpecSetup.exe",
    "sha256": "a" * 64,
    "notes": "test",
}


def test_parse_manifest_enforces_invariants():
    info = updates.parse_manifest(dict(_GOOD_MANIFEST))
    assert info.version == "9.9.9" and info.sha256 == "a" * 64

    for corruption in (
        {"version": ""},
        {"version": "not-a-version"},
        {"url": ""},
        {"url": "http://example.com/x.exe"},  # plaintext refused
        {"sha256": "zz"},
        {"sha256": "a" * 63},
    ):
        bad = dict(_GOOD_MANIFEST)
        bad.update(corruption)
        with pytest.raises(updates.UpdateError):
            updates.parse_manifest(bad)
    with pytest.raises(updates.UpdateError):
        updates.parse_manifest("not a dict")  # type: ignore[arg-type]


def test_check_for_update_never_raises(monkeypatch):
    monkeypatch.delenv(updates.ENV_DISABLE, raising=False)

    def good_fetcher(_url, timeout=0):
        return dict(_GOOD_MANIFEST)

    result = updates.check_for_update("0.5.0", fetcher=good_fetcher)
    assert result.update_available and result.info.version == "9.9.9"

    result = updates.check_for_update("9.9.9", fetcher=good_fetcher)
    assert result.status == updates.STATUS_UP_TO_DATE

    def broken_fetcher(_url, timeout=0):
        raise OSError("network down")

    result = updates.check_for_update("0.5.0", fetcher=broken_fetcher)
    assert result.status == updates.STATUS_ERROR
    assert "network down" in (result.error or "")

    monkeypatch.setenv(updates.ENV_DISABLE, "1")
    result = updates.check_for_update("0.5.0", fetcher=good_fetcher)
    assert result.status == updates.STATUS_DISABLED


# ---------------------------------------------------------------------------
# Download + integrity
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._buffer = io.BytesIO(payload)
        self.url = "https://example.com/BuildASpecSetup.exe"
        self.headers = {"Content-Length": str(len(payload))}

    def read(self, n: int = -1) -> bytes:
        return self._buffer.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_download_installer_verifies_hash_and_promotes_atomically(tmp_path):
    payload = b"installer bytes" * 1000
    info = updates.UpdateInfo(
        version="9.9.9",
        url="https://example.com/BuildASpecSetup.exe",
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    seen_progress: list[tuple[int, int]] = []
    dest = updates.download_installer(
        info,
        tmp_path,
        opener=lambda _url, timeout: _FakeResponse(payload),
        progress=lambda done, total: seen_progress.append((done, total)),
    )
    assert dest.name == "BuildASpecSetup.exe"
    assert dest.read_bytes() == payload
    assert not dest.with_name(dest.name + ".part").exists()
    assert seen_progress and seen_progress[-1][0] == len(payload)


def test_download_installer_rejects_tampered_bytes(tmp_path):
    payload = b"tampered"
    info = updates.UpdateInfo(
        version="9.9.9",
        url="https://example.com/BuildASpecSetup.exe",
        sha256="b" * 64,  # wrong hash
    )
    with pytest.raises(updates.UpdateError, match="integrity"):
        updates.download_installer(
            info, tmp_path, opener=lambda _url, timeout: _FakeResponse(payload)
        )
    # Nothing partial or final left behind to run by mistake.
    assert list(tmp_path.iterdir()) == []


def test_download_refuses_plaintext_url(tmp_path):
    info = updates.UpdateInfo(
        version="9.9.9", url="http://example.com/x.exe", sha256="a" * 64
    )
    with pytest.raises(updates.UpdateError, match="non-https"):
        updates.download_installer(info, tmp_path)


def test_installer_filename_guards_traversal():
    assert (
        updates._installer_filename("https://x/y/../../evil/Setup.exe")
        == "Setup.exe"
    )
    assert (
        updates._installer_filename("https://x/no-extension")
        == "BuildASpecSetup.exe"
    )


# ---------------------------------------------------------------------------
# Throttle / skip state
# ---------------------------------------------------------------------------


def test_throttle_state_round_trip(tmp_path):
    path = tmp_path / "update_check.json"
    state = updates.load_state(path)
    assert state == {}

    now = datetime(2026, 7, 21, 8, 0, 0)
    assert updates.should_auto_check(state, now=now)
    updates.record_check(state, now=now)
    updates.save_state(path, state)

    reloaded = updates.load_state(path)
    assert not updates.should_auto_check(
        reloaded, now=datetime(2026, 7, 21, 20, 0, 0)
    )
    assert updates.should_auto_check(
        reloaded, now=datetime(2026, 7, 23, 8, 0, 0)
    )

    updates.mark_skipped(reloaded, "9.9.9")
    assert updates.version_is_skipped(reloaded, "9.9.9")
    assert not updates.version_is_skipped(reloaded, "9.9.8")

    # Corrupt state degrades to empty.
    path.write_text("{not json", encoding="utf-8")
    assert updates.load_state(path) == {}


# ---------------------------------------------------------------------------
# make_manifest round-trip (the maker and the consumer can never drift)
# ---------------------------------------------------------------------------


def test_make_manifest_round_trips_through_parse_manifest(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "make_manifest",
        "packaging/windows/make_manifest.py",
    )
    make_manifest = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(make_manifest)

    installer = tmp_path / "BuildASpecSetup.exe"
    installer.write_bytes(b"exe bytes")
    out = tmp_path / "latest.json"
    manifest = make_manifest.write_manifest(
        version="0.5.0",
        installer=installer,
        url="https://github.com/Abe-Borg/build-a-spec/releases/download/v0.5.0/BuildASpecSetup.exe",
        out_path=out,
        notes="notes",
        published_at="2026-07-21",
    )
    parsed = updates.parse_manifest(json.loads(out.read_text()))
    assert parsed.version == "0.5.0"
    assert parsed.sha256 == manifest["sha256"]
    updates.verify_sha256(installer, parsed.sha256)


def test_version_consistency_gate():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "check_release_version",
        "packaging/windows/check_release_version.py",
    )
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)

    from backend import settings

    assert checker.settings_version() == settings.VERSION
    assert checker.package_json_version() == settings.VERSION
    assert checker.main([]) == 0
    assert checker.main(["--tag", f"v{settings.VERSION}"]) == 0
    assert checker.main(["--tag", "v99.0.0"]) == 1


# ---------------------------------------------------------------------------
# API surface
# ---------------------------------------------------------------------------


def test_update_endpoints(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from backend.app import create_app

    monkeypatch.setenv(updates.ENV_STATE_PATH, str(tmp_path / "state.json"))
    monkeypatch.delenv(updates.ENV_DISABLE, raising=False)

    def fake_check(current, **_kwargs):
        return updates.UpdateCheckResult(
            status=updates.STATUS_UPDATE_AVAILABLE,
            current=current,
            info=updates.parse_manifest(dict(_GOOD_MANIFEST)),
        )

    monkeypatch.setattr(updates, "check_for_update", fake_check)
    client = TestClient(create_app())

    payload = client.get("/api/update/check", params={"force": True}).json()
    assert payload["status"] == updates.STATUS_UPDATE_AVAILABLE
    assert payload["version"] == "9.9.9"
    assert "releases_url" in payload

    # A second unforced check inside the throttle window makes no network
    # request — but still answers with what the first one found, so the
    # install control does not vanish for the rest of the day.
    calls: list[str] = []

    def refuse(current, **_kwargs):
        calls.append(current)
        raise AssertionError("the throttle window must not hit the network")

    monkeypatch.setattr(updates, "check_for_update", refuse)
    payload = client.get("/api/update/check").json()
    assert calls == []
    assert payload["status"] == updates.STATUS_UPDATE_AVAILABLE
    assert payload["version"] == "9.9.9"
    assert payload["cached"] is True
    assert payload["platform_supported"] == updates.installer_platform_supported()
    assert "releases_url" in payload

    monkeypatch.setattr(updates, "check_for_update", fake_check)

    # Install on a non-Windows container is refused cleanly.
    if not updates.installer_platform_supported():
        resp = client.post("/api/update/install")
        assert resp.status_code == 400
        assert "Windows-only" in resp.json()["error"]


# ---------------------------------------------------------------------------
# The throttle window remembers the answer (a same-day relaunch still installs)
# ---------------------------------------------------------------------------


def test_remembered_update_is_rejudged_against_the_running_version():
    """A remembered version is not a standing claim — it is re-decided.

    The record outlives the build that wrote it, so replaying it blindly
    would offer to install the version already running.
    """
    state: dict = {}
    updates.remember_check_result(
        state,
        updates.UpdateCheckResult(
            status=updates.STATUS_UPDATE_AVAILABLE,
            current="1.0.0",
            info=updates.parse_manifest(dict(_GOOD_MANIFEST)),
        ),
    )
    assert state[updates.LAST_KNOWN_VERSION_KEY] == "9.9.9"

    known = updates.remembered_update(state, "1.0.0")
    assert known is not None and known.version == "9.9.9"

    # Same version, and a newer one: neither is an update any more.
    assert updates.remembered_update(state, "9.9.9") is None
    assert updates.remembered_update(state, "10.0.0") is None

    # It carries no url and no sha256: the state file is not a root of
    # trust, and an install must re-fetch and re-verify the manifest.
    assert not hasattr(known, "url")
    assert not hasattr(known, "sha256")


def test_a_failed_check_does_not_erase_what_the_last_one_found():
    """"We could not ask today" is not evidence the answer changed."""
    state: dict = {}
    updates.remember_check_result(
        state,
        updates.UpdateCheckResult(
            status=updates.STATUS_UPDATE_AVAILABLE,
            current="1.0.0",
            info=updates.parse_manifest(dict(_GOOD_MANIFEST)),
        ),
    )
    for dead in (
        updates.UpdateCheckResult(
            status=updates.STATUS_ERROR, current="1.0.0", error="offline"
        ),
        updates.UpdateCheckResult(status=updates.STATUS_DISABLED, current="1.0.0"),
    ):
        updates.remember_check_result(state, dead)
        known = updates.remembered_update(state, "1.0.0")
        assert known is not None and known.version == "9.9.9"


def test_a_malformed_remembered_version_reports_nothing():
    for value in ("", "   ", "v9.9.9", "banana", 9, None, ["9.9.9"]):
        assert (
            updates.remembered_update({updates.LAST_KNOWN_VERSION_KEY: value}, "1.0.0")
            is None
        )
    assert updates.remembered_update({}, "1.0.0") is None

    # Notes that are not a string degrade to empty rather than raising.
    known = updates.remembered_update(
        {
            updates.LAST_KNOWN_VERSION_KEY: "9.9.9",
            updates.LAST_KNOWN_NOTES_KEY: {"oops": True},
        },
        "1.0.0",
    )
    assert known is not None and known.notes == ""


def test_a_throttled_check_still_offers_an_update_after_a_relaunch(
    monkeypatch, tmp_path
):
    """The bug this fixes: the header's install control went missing.

    The launch check is throttled to once a day, so the second launch of any
    day used to answer ``THROTTLED`` with no version — the header pill
    disappeared, and the forced check in Help could only point back at it.
    """
    from fastapi.testclient import TestClient

    from backend import settings
    from backend.app import create_app

    state_file = tmp_path / "state.json"
    monkeypatch.setenv(updates.ENV_STATE_PATH, str(state_file))
    monkeypatch.delenv(updates.ENV_DISABLE, raising=False)

    manifest = dict(_GOOD_MANIFEST)
    manifest["notes"] = "Fixes the update button."

    def fake_check(current, **_kwargs):
        info = updates.parse_manifest(manifest)
        return updates.UpdateCheckResult(
            status=(
                updates.STATUS_UPDATE_AVAILABLE
                if updates.is_newer(info.version, current)
                else updates.STATUS_UP_TO_DATE
            ),
            current=current,
            info=info,
        )

    monkeypatch.setattr(updates, "check_for_update", fake_check)
    client = TestClient(create_app())

    # First launch of the day: a real check, and it is remembered.
    first = client.get("/api/update/check").json()
    assert first["status"] == updates.STATUS_UPDATE_AVAILABLE
    assert first.get("cached") is None
    assert json.loads(state_file.read_text(encoding="utf-8"))[
        updates.LAST_KNOWN_VERSION_KEY
    ] == "9.9.9"

    # Second launch, same day: throttled, and still installable.
    again = client.get("/api/update/check").json()
    assert again["status"] == updates.STATUS_UPDATE_AVAILABLE
    assert again["version"] == "9.9.9"
    assert again["notes"] == "Fixes the update button."
    assert again["cached"] is True

    # A skipped version stays skipped through the throttle window too.
    state = updates.load_state(state_file)
    updates.mark_skipped(state, "9.9.9")
    updates.save_state(state_file, state)
    skipped = client.get("/api/update/check").json()
    assert skipped["status"] == "THROTTLED"
    assert "version" not in skipped

    # Nothing remembered at all is still an honest throttled answer.
    updates.save_state(state_file, {"last_check": datetime.now().isoformat()})
    bare = client.get("/api/update/check").json()
    assert bare["status"] == "THROTTLED"
    assert bare["cached"] is True
    assert bare["current"] == settings.VERSION
    assert "version" not in bare


def test_a_dropped_download_reports_the_reason_not_a_500(monkeypatch, tmp_path):
    """``urllib`` failures are OSError subclasses, and used to escape.

    A dropped connection mid-download became an opaque ``internal_error``
    from the catch-all handler instead of the reason the install failed.
    """
    from fastapi.testclient import TestClient

    from backend.app import create_app

    monkeypatch.setenv(updates.ENV_STATE_PATH, str(tmp_path / "state.json"))
    monkeypatch.delenv(updates.ENV_DISABLE, raising=False)
    monkeypatch.setattr(updates, "installer_platform_supported", lambda: True)
    monkeypatch.setattr(
        updates,
        "check_for_update",
        lambda current, **_k: updates.UpdateCheckResult(
            status=updates.STATUS_UPDATE_AVAILABLE,
            current=current,
            info=updates.parse_manifest(dict(_GOOD_MANIFEST)),
        ),
    )

    def dropped(*_args, **_kwargs):
        raise ConnectionResetError("connection reset by peer")

    monkeypatch.setattr(updates, "download_installer", dropped)

    client = TestClient(create_app())
    resp = client.post("/api/update/install")
    assert resp.status_code == 502
    body = resp.json()
    assert body["ok"] is False
    assert "connection reset by peer" in body["error"]


def test_updates_switched_off_are_not_offered_from_the_remembered_result(
    monkeypatch, tmp_path
):
    """The throttle skips the one place the disable switch is enforced.

    ``check_for_update`` is what honours BUILD_A_SPEC_DISABLE_UPDATE_CHECK,
    and the throttled branch does not call it — so replaying a remembered
    update there would put an Install button in front of someone who has
    switched updates off, and ``/api/update/install`` would then refuse it.
    """
    from fastapi.testclient import TestClient

    from backend.app import create_app

    state_file = tmp_path / "state.json"
    monkeypatch.setenv(updates.ENV_STATE_PATH, str(state_file))
    monkeypatch.delenv(updates.ENV_DISABLE, raising=False)
    monkeypatch.setattr(
        updates,
        "check_for_update",
        lambda current, **_k: updates.UpdateCheckResult(
            status=updates.STATUS_UPDATE_AVAILABLE,
            current=current,
            info=updates.parse_manifest(dict(_GOOD_MANIFEST)),
        ),
    )
    client = TestClient(create_app())

    # Found and remembered while updates were still switched on.
    assert (
        client.get("/api/update/check").json()["status"]
        == updates.STATUS_UPDATE_AVAILABLE
    )

    # Switched off afterwards: the throttled reply must not replay it.
    monkeypatch.setenv(updates.ENV_DISABLE, "1")
    payload = client.get("/api/update/check").json()
    assert payload["status"] == updates.STATUS_DISABLED
    assert "version" not in payload
    # The record itself is untouched — switching updates back on restores
    # the offer without waiting for the throttle window to reopen.
    assert updates.load_state(state_file)[updates.LAST_KNOWN_VERSION_KEY] == "9.9.9"

    monkeypatch.delenv(updates.ENV_DISABLE, raising=False)
    assert (
        client.get("/api/update/check").json()["status"]
        == updates.STATUS_UPDATE_AVAILABLE
    )


# ---------------------------------------------------------------------------
# Installing an update closes the app, so it is gated like the window close
# ---------------------------------------------------------------------------


def _installable(monkeypatch, tmp_path):
    """An update is available, the platform is Windows, and installing succeeds.

    The download and the spawn are stubbed to record rather than act, so a
    test can assert that a refused request never reached either.
    """
    monkeypatch.setenv(updates.ENV_STATE_PATH, str(tmp_path / "state.json"))
    monkeypatch.delenv(updates.ENV_DISABLE, raising=False)
    monkeypatch.setattr(updates, "installer_platform_supported", lambda: True)
    monkeypatch.setattr(
        updates,
        "check_for_update",
        lambda current, **_k: updates.UpdateCheckResult(
            status=updates.STATUS_UPDATE_AVAILABLE,
            current=current,
            info=updates.parse_manifest(dict(_GOOD_MANIFEST)),
        ),
    )
    calls = {"downloads": 0, "spawns": 0}

    def download(*_args, **_kwargs):
        calls["downloads"] += 1
        return tmp_path / "BuildASpecSetup.exe"

    def spawn(*_args, **_kwargs):
        calls["spawns"] += 1

    monkeypatch.setattr(updates, "download_installer", download)
    monkeypatch.setattr(updates, "spawn_installer", spawn)
    return calls


def test_install_is_refused_while_the_session_is_busy(monkeypatch, tmp_path):
    """A running turn, research, audit or Final QC keeps the installer away.

    The installer closes the app; launching it over a streaming reply was
    exactly what the window-close prompt exists to prevent, and this button
    had no such gate at all.
    """
    from fastapi.testclient import TestClient

    from backend import sessions
    from backend.app import create_app

    calls = _installable(monkeypatch, tmp_path)
    session = sessions.get_session()
    session.turn_active = True
    try:
        client = TestClient(create_app())
        resp = client.post("/api/update/install")
    finally:
        session.turn_active = False

    assert resp.status_code == 409
    body = resp.json()
    assert body["ok"] is False and body["code"] == "workspace_busy"
    assert "chat" in body["error"]
    assert calls == {"downloads": 0, "spawns": 0}


def test_install_is_refused_on_unsaved_work_until_acknowledged(
    monkeypatch, tmp_path
):
    """Unsaved work asks first; the acknowledgement is the caller's promise
    that it asked the user (the Save / Install without saving prompt)."""
    from fastapi.testclient import TestClient

    from backend import sessions
    from backend.app import create_app

    calls = _installable(monkeypatch, tmp_path)
    sessions.get_session().history.append(
        {"role": "user", "content": [{"type": "text", "text": "unsaved"}]}
    )
    client = TestClient(create_app())

    refused = client.post("/api/update/install")
    assert refused.status_code == 409
    assert refused.json()["code"] == "unsaved_progress"
    assert calls == {"downloads": 0, "spawns": 0}

    # Saying so without meaning it is still a refusal.
    still = client.post("/api/update/install", json={"acknowledge_unsaved": False})
    assert still.status_code == 409 and still.json()["code"] == "unsaved_progress"

    accepted = client.post("/api/update/install", json={"acknowledge_unsaved": True})
    assert accepted.status_code == 200
    assert accepted.json() == {"ok": True, "version": _GOOD_MANIFEST["version"]}
    assert calls == {"downloads": 1, "spawns": 1}


def test_a_fresh_session_installs_without_a_prompt(monkeypatch, tmp_path):
    """Nothing to lose, nothing to ask — and a bodyless POST still works."""
    from fastapi.testclient import TestClient

    from backend.app import create_app

    calls = _installable(monkeypatch, tmp_path)
    client = TestClient(create_app())
    resp = client.post("/api/update/install")
    assert resp.status_code == 200 and resp.json()["ok"] is True
    assert calls == {"downloads": 1, "spawns": 1}


def test_install_is_refused_inside_a_tutorial_workspace(monkeypatch, tmp_path):
    """The user's real project is parked behind the tour; the installer
    would close the app on top of it."""
    from fastapi.testclient import TestClient

    from backend import sessions
    from backend.app import create_app

    calls = _installable(monkeypatch, tmp_path)
    client = TestClient(create_app())
    sessions.workspace_manager().begin_tutorial(request_id="install-gate")
    try:
        resp = client.post("/api/update/install", json={"acknowledge_unsaved": True})
    finally:
        sessions.workspace_manager().force_restore_original()

    assert resp.status_code == 409
    assert resp.json()["code"] == "tutorial_active"
    assert calls == {"downloads": 0, "spawns": 0}


def test_a_second_click_during_a_blocked_download_is_refused(
    monkeypatch, tmp_path
):
    """Two clicks used to be two downloads into the same ``.part`` file.

    The first request is parked inside the (stubbed) download on a real
    thread; the second must be answered ``install_in_progress`` rather than
    queued behind it or run beside it, and the first must still succeed
    once released — the lock is per attempt, not per process.
    """
    import threading

    from fastapi.testclient import TestClient

    from backend.app import create_app

    calls = _installable(monkeypatch, tmp_path)
    entered = threading.Event()
    release = threading.Event()

    def blocked_download(*_args, **_kwargs):
        calls["downloads"] += 1
        entered.set()
        assert release.wait(10), "the test never released the download"
        return tmp_path / "BuildASpecSetup.exe"

    monkeypatch.setattr(updates, "download_installer", blocked_download)

    with TestClient(create_app()) as client:
        first: dict = {}

        def run_first():
            first["resp"] = client.post("/api/update/install")

        worker = threading.Thread(target=run_first, daemon=True)
        worker.start()
        assert entered.wait(10), "the first install never reached its download"

        second = client.post("/api/update/install")
        assert second.status_code == 409
        assert second.json()["code"] == "install_in_progress"

        release.set()
        worker.join(10)
        assert not worker.is_alive()

    assert first["resp"].status_code == 200
    assert calls == {"downloads": 1, "spawns": 1}

    # Released after the attempt, so a retry after a declined installer
    # (or a dropped download) is one click, not a relaunch.
    with TestClient(create_app()) as client:
        release.set()
        again = client.post("/api/update/install")
        assert again.status_code == 200


def test_new_work_is_refused_while_an_install_is_in_flight(monkeypatch, tmp_path):
    """The workspace is reserved for the whole attempt (Codex, PR #152).

    The gate ran once, then the download ran for as long as it took, and a
    turn started or an edit made meanwhile was closed over by the installer
    without ever being asked about. While the install lock is held every
    mutating API request is refused; reads and the stop routes keep
    answering, and the hold lifts with the attempt.
    """
    import threading

    from fastapi.testclient import TestClient

    from backend.app import create_app

    calls = _installable(monkeypatch, tmp_path)
    entered = threading.Event()
    release = threading.Event()

    def blocked_download(*_args, **_kwargs):
        calls["downloads"] += 1
        entered.set()
        assert release.wait(10), "the test never released the download"
        return tmp_path / "BuildASpecSetup.exe"

    monkeypatch.setattr(updates, "download_installer", blocked_download)

    with TestClient(create_app()) as client:
        first: dict = {}
        worker = threading.Thread(
            target=lambda: first.update(resp=client.post("/api/update/install")),
            daemon=True,
        )
        worker.start()
        assert entered.wait(10)

        held = client.post("/api/doc/edit", json={"ops": []})
        assert held.status_code == 409
        assert held.json()["code"] == "workspace_busy"
        assert "update is being installed" in held.json()["error"]
        assert client.post("/api/session/reset", json={}).status_code == 409

        # Reads keep answering, and stopping is never new work.
        assert client.get("/api/doc").status_code == 200
        stop = client.post("/api/chat/stop")
        assert "update is being installed" not in stop.json().get("error", "")

        release.set()
        worker.join(10)
        assert not worker.is_alive()
        assert first["resp"].status_code == 200

        # The hold lifts with the attempt.
        after = client.post("/api/doc/edit", json={"ops": []})
        assert "update is being installed" not in after.json().get("error", "")
    assert calls == {"downloads": 1, "spawns": 1}


def test_work_that_slips_in_during_the_download_stops_the_spawn(
    monkeypatch, tmp_path
):
    """The last look before the spawn is at the session as it is NOW.

    The hold above is what keeps work out; this is the authoritative check
    the installer's launch waits on regardless, so a turn that got in by any
    other route is refused after the download rather than closed over.
    """
    from fastapi.testclient import TestClient

    from backend import sessions
    from backend.app import create_app

    calls = _installable(monkeypatch, tmp_path)
    session = sessions.get_session()

    def download_then_turn(*_args, **_kwargs):
        calls["downloads"] += 1
        session.turn_active = True
        return tmp_path / "BuildASpecSetup.exe"

    monkeypatch.setattr(updates, "download_installer", download_then_turn)
    try:
        resp = TestClient(create_app()).post("/api/update/install")
    finally:
        session.turn_active = False

    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "workspace_busy" and "not launched" in body["error"]
    assert calls == {"downloads": 1, "spawns": 0}


def test_unsaved_work_that_appears_during_the_download_asks_before_the_spawn(
    monkeypatch, tmp_path
):
    from fastapi.testclient import TestClient

    from backend import sessions
    from backend.app import create_app

    calls = _installable(monkeypatch, tmp_path)
    session = sessions.get_session()

    def download_then_work(*_args, **_kwargs):
        calls["downloads"] += 1
        session.history.append(
            {"role": "user", "content": [{"type": "text", "text": "late"}]}
        )
        return tmp_path / "BuildASpecSetup.exe"

    monkeypatch.setattr(updates, "download_installer", download_then_work)
    client = TestClient(create_app())

    refused = client.post("/api/update/install")
    assert refused.status_code == 409
    assert refused.json()["code"] == "unsaved_progress"
    assert calls == {"downloads": 1, "spawns": 0}

    accepted = client.post("/api/update/install", json={"acknowledge_unsaved": True})
    assert accepted.status_code == 200
    assert calls == {"downloads": 2, "spawns": 1}
