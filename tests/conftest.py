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
# Never let a test's update check reach the network or the real state file.
os.environ.setdefault("BUILD_A_SPEC_DISABLE_UPDATE_CHECK", "1")


@pytest.fixture(autouse=True)
def _fresh_session():
    from backend import sessions
    from backend.llm.client import reset_client_cache
    from backend.llm.conversation import reset_thinking_display_probe

    sessions.reset_session()
    reset_client_cache()
    # The thinking.display capability degrade is process-scoped; re-arm it so
    # a fallback test can't leak "omitted" into a later test's request.
    reset_thinking_display_probe()
    yield
    sessions.reset_session()
    reset_client_cache()
    reset_thinking_display_probe()
