"""WI3: API-key status / delete / test endpoints, and the store helpers.

Hermetic: no real keyring, no network. The conftest injects a placeholder
``ANTHROPIC_API_KEY``; tests that exercise keyring/file resolution delete it
first so the env var doesn't shadow them.
"""
from __future__ import annotations

import httpx
import anthropic
from fastapi.testclient import TestClient

from backend.app import create_app
from backend import api_key_store


def _client() -> TestClient:
    return TestClient(create_app())


class _FakeKeyring:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, user: str):
        return self.store.get((service, user))

    def set_password(self, service: str, user: str, value: str) -> None:
        self.store[(service, user)] = value

    def delete_password(self, service: str, user: str) -> None:
        if (service, user) not in self.store:
            raise RuntimeError("no such password")
        del self.store[(service, user)]


def _use_fake_keyring(monkeypatch) -> _FakeKeyring:
    fake = _FakeKeyring()
    monkeypatch.setattr(api_key_store, "_keyring", fake)
    monkeypatch.setattr(api_key_store, "_KEYRING_AVAILABLE", True)
    return fake


# ---------------------------------------------------------------------------
# key_status / delete_api_key units
# ---------------------------------------------------------------------------


def test_key_status_env_source_and_mask(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret-TAIL")
    status = api_key_store.key_status()
    assert status["present"] is True
    assert status["source"] == "env"
    assert status["masked"] == "…TAIL"
    # The full key is never in the masked preview.
    assert "secret" not in status["masked"]


def test_key_status_keyring_then_file(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    fake = _use_fake_keyring(monkeypatch)
    # No key anywhere yet.
    monkeypatch.setattr(api_key_store, "api_key_paths", lambda: [tmp_path / "k.txt"])
    assert api_key_store.key_status() == {
        "present": False,
        "source": "none",
        "masked": "",
    }
    # Keyring resolution.
    fake.store[(api_key_store._KEYRING_SERVICE, api_key_store._KEYRING_USERNAME)] = (
        "sk-ant-keyring-ABCD"
    )
    assert api_key_store.key_status()["source"] == "keyring"
    # File resolution when keyring is empty.
    fake.store.clear()
    (tmp_path / "k.txt").write_text("sk-ant-file-WXYZ", encoding="utf-8")
    status = api_key_store.key_status()
    assert status["source"] == "file" and status["masked"] == "…WXYZ"


def test_delete_api_key_clears_keyring_and_files(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    fake = _use_fake_keyring(monkeypatch)
    path = tmp_path / "k.txt"
    monkeypatch.setattr(api_key_store, "api_key_paths", lambda: [path])
    fake.store[(api_key_store._KEYRING_SERVICE, api_key_store._KEYRING_USERNAME)] = "x"
    path.write_text("sk-ant-file", encoding="utf-8")

    cleared = api_key_store.delete_api_key()
    assert cleared == {"keyring": True, "file": True}
    assert not path.exists()
    assert api_key_store.key_status()["present"] is False


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def test_status_endpoint_masks_and_never_leaks(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-supersecret-9999")
    resp = _client().get("/api/key/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["present"] is True
    assert data["source"] == "env"
    assert data["env_locked"] is True
    assert data["masked"] == "…9999"
    # The full key never appears anywhere in the response body.
    assert "supersecret" not in resp.text


def test_delete_endpoint_removes_key_and_updates_health(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _use_fake_keyring(monkeypatch)
    monkeypatch.setattr(api_key_store, "api_key_paths", lambda: [tmp_path / "k.txt"])
    api_key_store.save_api_key("sk-ant-tobedeleted")
    client = _client()
    assert client.get("/api/health").json()["api_key_present"] is True

    resp = client.delete("/api/key")
    assert resp.status_code == 200
    assert resp.json()["present"] is False
    assert client.get("/api/health").json()["api_key_present"] is False


def test_test_key_endpoint_success(monkeypatch):
    from types import SimpleNamespace

    def _ok_client(_key: str):
        return SimpleNamespace(models=SimpleNamespace(list=lambda **kw: SimpleNamespace(data=[])))

    monkeypatch.setattr("backend.app.build_probe_client", _ok_client)
    resp = _client().post("/api/key/test", json={"api_key": "sk-ant-anything"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_test_key_endpoint_auth_failure_does_not_store(monkeypatch):
    def _bad_client(_key: str):
        req = httpx.Request("GET", "https://api.anthropic.com/v1/models")
        raise anthropic.AuthenticationError(
            "invalid x-api-key",
            response=httpx.Response(401, request=req),
            body=None,
        )

    monkeypatch.setattr("backend.app.build_probe_client", _bad_client)
    resp = _client().post("/api/key/test", json={"api_key": "sk-ant-bad"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "x-api-key" in data["error"]
