"""Geo picks its hub login through services.common.hub_login (integrations step 3).

Home Assistant's login is used only while that integration is on, even if geo still
holds an HA value from before; else HomeHub with the hub's own login; with no hub at
all, the shared NO_HUB_MESSAGE.
"""
import pytest
from fastapi import HTTPException

import services.config as config
from services.common.hub_login import NO_HUB_MESSAGE

HUB = "http://hub.example.test:8123"
SECRET = "made-up-internal-secret"


@pytest.fixture
def ha_on(monkeypatch):
    monkeypatch.setattr(config, "HA_URL", "http://ha.example.test:8123")
    monkeypatch.setattr(config, "HOMEHUB_URL", HUB)
    monkeypatch.setattr(config, "INTERNAL_SECRET", SECRET)


@pytest.fixture
def ha_off(monkeypatch):
    monkeypatch.setattr(config, "HA_URL", "")
    monkeypatch.setattr(config, "HOMEHUB_URL", HUB)
    monkeypatch.setattr(config, "INTERNAL_SECRET", SECRET)


@pytest.fixture
def no_hub(monkeypatch):
    monkeypatch.setattr(config, "HA_URL", "")
    monkeypatch.setattr(config, "HOMEHUB_URL", "")
    monkeypatch.setattr(config, "INTERNAL_SECRET", SECRET)


def test_geo_uses_the_household_ha_login_only_while_ha_is_on(ha_on, monkeypatch):
    import services.geo.main as geo

    monkeypatch.setattr(geo, "HA_URL", "http://ha.example.test:8123/")
    monkeypatch.setattr(geo, "HA_TOKEN", "made-up-household-token")
    assert geo._hub_target() == ("http://ha.example.test:8123", "made-up-household-token")
    assert geo._hub_kind() == "home_assistant"


def test_geo_uses_homehub_while_ha_is_off_even_with_a_stale_ha_value(ha_off, monkeypatch):
    import services.geo.main as geo

    monkeypatch.setattr(geo, "HA_URL", "http://ha.example.test:8123")
    monkeypatch.setattr(geo, "HA_TOKEN", "made-up-household-token")
    assert geo._hub_target() == (HUB, SECRET)
    assert geo._hub_kind() == "homehub"


def test_geo_says_no_hub_without_a_hub(no_hub, monkeypatch):
    import services.geo.main as geo

    monkeypatch.setattr(geo, "HA_URL", "")
    monkeypatch.setattr(geo, "HA_TOKEN", None)
    assert geo._hub_target() is None and geo._hub_kind() is None
    with pytest.raises(HTTPException) as err:
        geo._require_hub()
    assert err.value.detail == NO_HUB_MESSAGE
