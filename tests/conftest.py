"""Hermetic test setup — no API key, no network.

Same posture as Spec Critic's suite: a placeholder ``ANTHROPIC_API_KEY`` is
injected so client construction never fails, and every test that would talk
to the API monkeypatches the client factory with a fake.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Repo root on sys.path so `backend` imports resolve when pytest is run
# from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-hermetic")
# Hermetic suite: tracing off so no test writes to the user state dir.
# The tracing tests opt back in with a monkeypatched trace dir.
os.environ.setdefault("BUILD_A_SPEC_TRACE", "0")
# Same for the diagnostics activity log — no test writes the user state
# dir; test_diagnostics.py opts back in with a tmp log dir.
os.environ.setdefault("BUILD_A_SPEC_LOG", "0")
# Never let a test's update check reach the network or the real state file.
os.environ.setdefault("BUILD_A_SPEC_DISABLE_UPDATE_CHECK", "1")


def _restore_default_module():
    """Undo module/discipline leaks between tests.

    ``SessionState.reset()`` deliberately KEEPS the active module and
    discipline (app semantics: a fresh session in the same discipline), so
    a test that switches the singleton to the generic module would poison
    every later test. Restoring the default here is test-only hygiene, not
    an app behavior change.
    """
    from backend import sessions
    from backend.spec_modules import get_module

    session = sessions.get_session()
    session.module = get_module(None)
    session.discipline = ""


def settle_capability_sweep(timeout: float = 120.0) -> None:
    """Make the imported-source permission report current for this state.

    The sweep probes every element against the real gate, so it is O(document)
    per probe and no longer runs inline anywhere — a response taken the moment
    an import or a body edit returns reports ``pending`` capabilities. Tests
    that assert what the permissions ARE call this first; tests about the
    asynchrony itself deliberately do not.

    This derives the report rather than only waiting on a warm. Waiting alone
    is not enough and fails intermittently: if nothing has read capabilities
    since the last change there is no warm in flight, so the wait returns
    immediately and the test's next read is the one that starts the sweep —
    and gets ``pending``. ``block=True`` joins an in-flight warm instead of
    duplicating it, then publishes the memo every later reader hits.

    The derivation runs on its own thread so ``timeout`` actually bounds it.
    ``block=True`` waits without a deadline — correct in production, where a
    QC run must get a real answer however long it takes — so calling it
    inline would let a stalled sweep hang the whole suite instead of failing
    this assertion. The daemon thread is abandoned on timeout, exactly like
    the session's own abandoned warms.
    """
    import threading

    from backend import sessions

    session = sessions.get_session()
    derived = threading.Event()

    def _derive() -> None:
        try:
            session.source_edit_capabilities(block=True)
        finally:
            derived.set()

    threading.Thread(
        target=_derive,
        name="settle-capability-sweep",
        daemon=True,
    ).start()
    assert derived.wait(timeout), (
        "the background capability sweep did not settle"
    )


@pytest.fixture(autouse=True)
def _fresh_session(monkeypatch):
    from backend import sessions
    from backend.llm.client import reset_client_cache
    from backend.llm.conversation import reset_thinking_display_probe

    sessions.reset_session()
    _restore_default_module()
    reset_client_cache()
    # The thinking.display capability degrade is process-scoped; re-arm it so
    # a fallback test can't leak "omitted" into a later test's request.
    reset_thinking_display_probe()
    # Reference uploads use Anthropic's remote token-counting endpoint in
    # production. Keep the suite hermetic while preserving deterministic
    # cumulative-limit behavior.
    from types import SimpleNamespace
    from backend import app as app_module

    fake_client = SimpleNamespace(
        messages=SimpleNamespace(
            count_tokens=lambda **kwargs: SimpleNamespace(
                input_tokens=max(1, len(str(kwargs["messages"][0]["content"])) // 4)
            )
        )
    )
    monkeypatch.setattr(app_module, "get_client", lambda: fake_client)
    yield
    sessions.reset_session()
    _restore_default_module()
    reset_client_cache()
    reset_thinking_display_probe()
