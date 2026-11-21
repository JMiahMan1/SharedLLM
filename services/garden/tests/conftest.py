"""Garden tests run with no Home Hub unless a test says otherwise.

Rooms are Home Hub's areas (``services/garden/hub_rooms.py``), so a hub URL left
in the environment -- the repo ``conftest.py``'s dummy integration URLs, or a
developer's ``.env`` -- would send every room read and write at a host that is
not there, and the tests would fail on something they never meant to exercise.
Tests that want a hub patch ``config.HOMEHUB_URL`` themselves
(``test_hub_rooms.py`` does, with a fake hub).
"""
from __future__ import annotations

import pytest

from services import config
from services.garden import homehub_hardware, hub_rooms


@pytest.fixture(autouse=True)
def _no_home_hub(monkeypatch):
    monkeypatch.setattr(config, "HOMEHUB_URL", "")
    monkeypatch.setattr(hub_rooms, "_cache", None)
    # Phase 8: same reason, for the hardware discovery caches.
    monkeypatch.setattr(homehub_hardware, "_states_cache", None)
    monkeypatch.setattr(homehub_hardware, "_tags_cache", None)


@pytest.fixture(autouse=True)
def _household_zone(monkeypatch):
    """Garden counts by the household's day and refuses to guess a zone, so the
    tests name one. UTC keeps existing date arithmetic stable; tests about the
    zone itself set their own."""
    from services.common import household_tz

    household_tz.reset_cache()
    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.delenv("TIMEZONE", raising=False)
