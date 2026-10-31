"""Geo test isolation."""
import pytest

from services.common import notify


@pytest.fixture(autouse=True)
def _no_family_lookup(monkeypatch):
    """Alert recipients include the family list from Identity; tests name
    their people through ``_tracked_users`` (or patch this) instead."""

    async def family(**_kw):
        return []

    monkeypatch.setattr(notify, "family", family)
