"""Telemetry tests never reach identity: preference, topic and admin lookups
fail fast (preferences count as on, no ntfy topic) unless a test stubs them."""
import pytest

from services.telemetry import notices, ntfy


@pytest.fixture(autouse=True)
def no_identity(monkeypatch):
    async def unreachable(session, path, params=None):
        raise RuntimeError("identity is not reachable in tests")

    monkeypatch.setattr(notices, "_identity_get", unreachable)
    monkeypatch.setattr(notices, "_prefs_cache", {})
    monkeypatch.setattr(notices, "_topic_cache", {})
    monkeypatch.setattr(notices, "_admins_cache", None)
    monkeypatch.setattr(notices, "_minors_cache", None)
    monkeypatch.setattr(ntfy, "_failing", False)
