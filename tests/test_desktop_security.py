"""Focused local-server identity, bootstrap, and CSRF regressions."""
from __future__ import annotations

import hashlib
import socket
import types
import urllib.parse

import pytest
from fastapi.testclient import TestClient

import main
from backend.app import DesktopSecurityConfig, create_app


_HOST = "127.0.0.1"
_PORT = 43123
_ORIGIN = f"http://{_HOST}:{_PORT}"
_BOOT_NONCE = "boot-nonce-for-test-only"
_API_TOKEN = "test-token-" + ("x" * 64)


def _security(
    *, boot_nonce: str = _BOOT_NONCE, api_token: str = _API_TOKEN
) -> DesktopSecurityConfig:
    return DesktopSecurityConfig(
        boot_nonce=boot_nonce,
        api_token=api_token,
        bound_host=_HOST,
        bound_port=_PORT,
        allowed_hosts=(f"{_HOST}:{_PORT}",),
        allowed_origins=(_ORIGIN,),
    )


def _secure_client() -> TestClient:
    return TestClient(
        create_app(desktop_security=_security(), _record_start_event=False),
        base_url=_ORIGIN,
    )


def test_direct_testclient_security_remains_explicitly_inactive():
    client = TestClient(create_app(_record_start_event=False))
    health = client.get("/api/health")
    assert health.status_code == 200
    assert "workspace_id" in health.json()
    assert "boot_nonce" not in health.json()
    assert client.get("/api/doc").status_code == 200
    assert client.get("/openapi.json").status_code == 200


def test_secure_desktop_bootstrap_auth_cookie_host_origin_and_headers():
    security = _security()
    assert _API_TOKEN not in repr(security)
    client = _secure_client()

    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json() == {
        "status": "ok",
        "app": "Build-a-Spec",
        "version": health.json()["version"],
        "boot_nonce_fingerprint": hashlib.sha256(
            _BOOT_NONCE.encode("utf-8")
        ).hexdigest(),
        "bound_host": _HOST,
        "bound_port": _PORT,
    }
    assert _BOOT_NONCE not in health.text
    assert health.headers["x-content-type-options"] == "nosniff"
    assert health.headers["x-frame-options"] == "DENY"
    assert health.headers["cache-control"] == "no-store"

    denied = client.get("/api/doc")
    assert denied.status_code == 401
    assert denied.json()["code"] == "desktop_auth_required"
    assert denied.headers["x-buildaspec-outcome-code"] == "desktop_auth_required"

    wrong_host = client.get("/api/health", headers={"Host": "evil.example"})
    assert wrong_host.status_code == 421
    assert wrong_host.json()["code"] == "invalid_host"

    bad_boot = client.get(
        "/api/bootstrap", headers={"X-BuildASpec-Boot-Nonce": "wrong"}
    )
    assert bad_boot.status_code == 403
    assert bad_boot.json()["code"] == "invalid_boot_nonce"

    boot = client.get(
        "/api/bootstrap",
        headers={"X-BuildASpec-Boot-Nonce": _BOOT_NONCE, "Origin": _ORIGIN},
    )
    assert boot.status_code == 200
    assert boot.json()["api_token"] == _API_TOKEN
    cookie = boot.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert "path=/api" in cookie

    authenticated = client.get("/api/health").json()
    assert "workspace_id" in authenticated
    assert authenticated["bound_port"] == _PORT
    assert "boot_nonce" not in authenticated
    assert client.get("/api/doc").status_code == 200

    # Cookie-only downloads/GETs work, but a mutation also needs a trusted
    # Origin. The fetch wrapper's custom header is independent CSRF proof.
    no_csrf = client.post("/api/session/reset")
    assert no_csrf.status_code == 403
    assert no_csrf.json()["code"] == "csrf_required"
    assert client.post(
        "/api/session/reset", headers={"Origin": _ORIGIN}
    ).status_code == 200
    assert client.post(
        "/api/session/reset", headers={"X-BuildASpec-Token": _API_TOKEN}
    ).status_code == 200
    invalid_origin = client.post(
        "/api/session/reset",
        headers={
            "X-BuildASpec-Token": _API_TOKEN,
            "Origin": "https://evil.example",
        },
    )
    assert invalid_origin.status_code == 403
    assert invalid_origin.json()["code"] == "invalid_origin"

    # The static viewer contains no trace data and remains deliberately
    # launchable in the user's separate system browser without its cookie.
    fresh = _secure_client()
    viewer = fresh.get("/api/trace/viewer")
    assert viewer.status_code == 200
    assert "'unsafe-inline'" in viewer.headers["content-security-policy"]
    assert fresh.get("/openapi.json").status_code == 404


def test_security_rejection_does_not_acquire_workspace_write(monkeypatch):
    def forbidden_active_write(*_args, **_kwargs):
        raise AssertionError("unauthenticated request acquired active_write")

    monkeypatch.setattr("backend.app.sessions.active_write", forbidden_active_write)
    client = _secure_client()

    # Import is one of the slow mutations protected by the lease middleware.
    # Security must reject it before that middleware is entered.
    denied = client.post("/api/import/master")
    assert denied.status_code == 401
    assert denied.json()["code"] == "desktop_auth_required"


def test_parallel_desktop_instances_use_distinct_cookie_names():
    first = _secure_client()
    second_security = _security(
        boot_nonce="another-launch-nonce",
        api_token="another-token-" + ("y" * 64),
    )
    second = TestClient(
        create_app(
            desktop_security=second_security,
            _record_start_event=False,
        ),
        base_url=_ORIGIN,
    )
    first_cookie = first.get(
        "/api/bootstrap",
        headers={"X-BuildASpec-Boot-Nonce": _BOOT_NONCE},
    ).headers["set-cookie"].split("=", 1)[0]
    second_cookie = second.get(
        "/api/bootstrap",
        headers={"X-BuildASpec-Boot-Nonce": second_security.boot_nonce},
    ).headers["set-cookie"].split("=", 1)[0]

    assert first_cookie.startswith("buildaspec_session_")
    assert second_cookie.startswith("buildaspec_session_")
    assert first_cookie != second_cookie


def test_secure_desktop_cors_preflight_allows_only_configured_frontend():
    client = _secure_client()
    response = client.options(
        "/api/doc/edit",
        headers={
            "Origin": _ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "X-BuildASpec-Token, Content-Type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == _ORIGIN
    assert response.headers["access-control-allow-credentials"] == "true"

    rejected = client.options(
        "/api/doc/edit",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert rejected.status_code == 403


def test_launcher_uses_fixed_vite_port_and_ephemeral_packaged_port(monkeypatch):
    monkeypatch.setattr(main.settings, "PORT", 9876)
    monkeypatch.setattr(main.settings, "dev_mode", lambda: True)
    assert main._desired_backend_port() == 9876
    origins, hosts = main._desktop_origins_and_hosts(9876)
    assert "http://localhost:5173" in origins
    assert "localhost:5173" in hosts

    monkeypatch.setattr(main.settings, "dev_mode", lambda: False)
    assert main._desired_backend_port() == 0


def test_exclusive_prebind_rejects_a_second_listener():
    listener = main._reserve_backend_socket(0)
    try:
        port = int(listener.getsockname()[1])
        with pytest.raises(OSError):
            duplicate = main._reserve_backend_socket(port)
            duplicate.close()
    finally:
        listener.close()


def test_health_identity_and_boot_fragment_are_exact_and_non_query():
    runtime = types.SimpleNamespace(boot_nonce=_BOOT_NONCE, port=_PORT)
    fingerprint = hashlib.sha256(_BOOT_NONCE.encode("utf-8")).hexdigest()
    assert main._health_identity_matches(
        runtime,
        {
            "status": "ok",
            "app": main.settings.APP_NAME,
            "version": main.settings.VERSION,
            "boot_nonce_fingerprint": fingerprint,
            "bound_port": _PORT,
        },
    )
    assert not main._health_identity_matches(
        runtime,
        {
            "status": "ok",
            "app": main.settings.APP_NAME,
            "version": main.settings.VERSION,
            "boot_nonce_fingerprint": hashlib.sha256(b"other").hexdigest(),
            "bound_port": _PORT,
        },
    )
    url = main._url_with_boot_fragment("http://127.0.0.1:9999/", _BOOT_NONCE)
    parsed = urllib.parse.urlsplit(url)
    assert parsed.query == ""
    assert urllib.parse.parse_qs(parsed.fragment) == {
        "buildaspec_boot": [_BOOT_NONCE]
    }


def test_prebound_socket_can_be_used_by_uvicorn_contract():
    # Keep this regression at the socket contract: Uvicorn takes ownership of
    # an already-bound (not pre-listening) socket via Server.run(sockets=[...]).
    listener = main._reserve_backend_socket(0)
    try:
        assert listener.family == socket.AF_INET
        assert int(listener.getsockname()[1]) > 0
        assert listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) == 0
    finally:
        listener.close()
