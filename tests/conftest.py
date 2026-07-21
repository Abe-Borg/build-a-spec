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


@pytest.fixture(autouse=True)
def _fresh_session():
    from backend import sessions
    from backend.llm.client import reset_client_cache

    sessions.reset_session()
    reset_client_cache()
    yield
    sessions.reset_session()
    reset_client_cache()
