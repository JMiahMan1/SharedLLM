# services/tests/conftest.py
"""
Test configuration and fixtures.
Loads environment variables from .env.test if available.
"""
import os
from pathlib import Path

import pytest

# Load .env.test if it exists
env_test_path = Path(__file__).parent.parent.parent / ".env.test"
if env_test_path.exists():
    with env_test_path.open() as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                key, value = line.split("=", 1)
                os.environ[key.strip()] = value.strip()


@pytest.fixture(autouse=True)
def _plain_home_assistant_index(monkeypatch):
    """These tests talk to a fake Home Assistant, which has no capability index.

    ``capability_index.routing_index`` raises when a hub cannot be reached, so
    the hub answers "no index here" (HTTP 404: the logged plain-HA path) unless
    a test sets up an index itself. Same default as services/execution/tests.
    """
    from services.execution import capability_index

    async def no_index(key, ha_token):
        return None

    capability_index.clear_cache()
    monkeypatch.setattr(capability_index, "_fetch_index", no_index)
    yield
    capability_index.clear_cache()
