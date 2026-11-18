"""A caller's own Home Assistant login is not used while the HA integration is off.

Integrations step 2: with HA switched off (blank HA_URL / ``ha_url``), no HA
login is used for calls, the person's own included. ``hub_login`` falls through
to the hub's own login (HomeHub) or to None. Nothing stored is changed.
"""
import services.config as config
from services.common import hub_login

OWN = {"ha_url": "http://own.example.test:8123", "ha_token": "made-up-own-token"}
HUB_URL = "http://hub.example.test:8123"


def test_own_login_is_used_while_ha_is_on(monkeypatch):
    monkeypatch.setattr(config, "HA_URL", "http://ha.example.test:8123")
    monkeypatch.setattr(config, "HOMEHUB_URL", HUB_URL)
    assert hub_login.has_own_login(OWN) is True
    assert hub_login.hub_login(OWN) == (OWN["ha_url"], OWN["ha_token"])


def test_own_login_falls_through_to_homehub_while_ha_is_off(monkeypatch):
    monkeypatch.setattr(config, "HA_URL", "")
    monkeypatch.setattr(config, "HOMEHUB_URL", HUB_URL)
    monkeypatch.setattr(config, "INTERNAL_SECRET", "made-up-secret")
    assert hub_login.has_own_login(OWN) is False
    assert hub_login.hub_login(OWN) == (HUB_URL, "made-up-secret")


def test_no_hub_at_all_means_no_login_even_with_an_own_token(monkeypatch):
    monkeypatch.setattr(config, "HA_URL", "")
    monkeypatch.setattr(config, "HOMEHUB_URL", "")
    assert hub_login.hub_login(OWN) is None
    assert hub_login.hub_pair(OWN) == ("", "")
