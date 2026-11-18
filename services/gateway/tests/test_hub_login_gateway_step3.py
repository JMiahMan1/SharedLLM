"""The gateway picks a hub login through services.common.hub_login (integrations step 3).

While Home Assistant is off, a caller's stored HA login is not used for dashboards,
live entity states or pictures; the hub's own login is used where that is allowed,
and with no hub at all the shared NO_HUB_MESSAGE is said.
"""
import pytest
from fastapi import HTTPException

import services.config as config
from services.common.hub_login import NO_HUB_MESSAGE

OWN = {"user": "alex", "ha_url": "http://ha.example.test:8123", "ha_token": "made-up-own-token"}
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


def test_dashboards_read_with_the_own_login_while_ha_is_on(ha_on):
    from services.gateway.dashboard_routes import _hub_login

    assert _hub_login(OWN, personal=False) == (OWN["ha_url"], OWN["ha_token"])


def test_dashboards_read_with_the_hubs_login_while_ha_is_off(ha_off):
    from services.gateway.dashboard_routes import _hub_login

    assert _hub_login(OWN, personal=False) == (HUB, SECRET)
    with pytest.raises(HTTPException):
        _hub_login(OWN, personal=True)  # their own data on the hub never falls back


def test_dashboards_say_no_hub_without_a_hub(no_hub):
    from services.gateway.dashboard_routes import _hub_login

    with pytest.raises(HTTPException) as err:
        _hub_login(OWN, personal=False)
    assert err.value.detail == NO_HUB_MESSAGE


def test_live_states_pass_no_ha_login_while_ha_is_off(ha_off):
    from services.gateway.ha_state_cache import _login_params

    assert _login_params(OWN["ha_url"], OWN["ha_token"]) == {"include_hidden": "1"}


def test_live_states_pass_the_own_login_while_ha_is_on(ha_on):
    from services.gateway.ha_state_cache import _login_params

    params = _login_params(OWN["ha_url"], OWN["ha_token"])
    assert params["ha_url"] == OWN["ha_url"] and params["ha_token"] == OWN["ha_token"]


def test_live_states_never_pass_the_hubs_own_login(ha_on):
    from services.gateway.ha_state_cache import _login_params

    assert _login_params(HUB, SECRET) == {"include_hidden": "1"}

