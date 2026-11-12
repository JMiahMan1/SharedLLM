"""Execution tests run against a plain Home Assistant unless a test hands them a capability index.

``capability_index.routing_index`` raises when the hub cannot be reached, so a
test that never meant to reach a hub would otherwise fail on the network. By
default the hub answers "no index here" (HTTP 404), which is the logged
fallback path; tests of the index patch ``_fetch_index`` / ``load`` themselves.
"""
from __future__ import annotations

import pytest

from services.execution import capability_index

_REAL_FETCH_INDEX = capability_index._fetch_index


@pytest.fixture(autouse=True)
def _plain_home_assistant(monkeypatch):
    async def no_index(key, ha_token):
        return None

    capability_index.clear_cache()
    monkeypatch.setattr(capability_index, "_fetch_index", no_index)
    yield
    capability_index.clear_cache()


@pytest.fixture
def real_capability_index(monkeypatch):
    """Undo the default: ``load`` fetches over HTTP again (tests fake the session)."""
    monkeypatch.setattr(capability_index, "_fetch_index", _REAL_FETCH_INDEX)
