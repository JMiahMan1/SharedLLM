"""ha_client flags 401/403 responses with credential_alerts.

The stored HA login expiring is how background flows went quiet: every call
returned empty while only the logs told the story. A rejected call now trips
the shared flag once; a successful one clears it.
"""

import types
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import aiohttp
import pytest

from services.execution import ha_client


class _Resp:
    def __init__(self, status=401, payload=None):
        self.status = status
        self._payload = payload if payload is not None else []

    def raise_for_status(self):
        if self.status >= 400:
            raise aiohttp.ClientResponseError(
                request_info=types.SimpleNamespace(real_url="https://ha.example/api/template"),
                history=(),
                status=self.status,
                message="Unauthorized",
            )

    async def json(self, content_type=None):
        return self._payload


class _CM:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, exc_type, exc, tb):
        return None


class _Client:
    def __init__(self, resp):
        self._resp = resp

    def post(self, url, headers=None, json=None, timeout=None):
        return _CM(self._resp)

    def get(self, url, headers=None, timeout=None):
        return _CM(self._resp)


@pytest.fixture
def session(monkeypatch):
    def run(resp):
        @asynccontextmanager
        async def fake_session(ha_url, verify=True):
            yield _Client(resp)

        monkeypatch.setattr(ha_client, "_ha_session", fake_session)
        monkeypatch.setattr(ha_client.credential_alerts, "auth_failed", AsyncMock())
        monkeypatch.setattr(ha_client.credential_alerts, "auth_ok", AsyncMock())

    return run


async def test_a_401_from_a_real_states_call_flags_the_credential(session):
    session(_Resp(status=401))
    assert await ha_client.get_states("https://ha.example", "tok", account="system account") == []
    ha_client.credential_alerts.auth_failed.assert_awaited_once_with(
        "home_assistant", "system account", "HTTP 401"
    )


class _ByToken:
    """HA's /api/template: 401 for a non-admin token, areas for the admin one."""

    def __init__(self, admin_token):
        self.admin_token = admin_token
        self.tokens: list[str] = []

    def post(self, url, headers=None, json=None, timeout=None):
        token = (headers or {}).get("Authorization", "").removeprefix("Bearer ")
        self.tokens.append(token)
        if token == self.admin_token:
            return _CM(_Resp(status=200, payload=[{"eid": "media_player.x", "a": "Kitchen"}]))
        return _CM(_Resp(status=401))


@pytest.fixture
def template_api(monkeypatch):
    def run(admin_token, household_url="https://ha.example"):
        api = _ByToken(admin_token)

        @asynccontextmanager
        async def fake_session(ha_url, verify=True):
            yield api

        monkeypatch.setattr(ha_client, "_ha_session", fake_session)
        monkeypatch.setattr(ha_client.credential_alerts, "auth_failed", AsyncMock())
        monkeypatch.setattr(ha_client.credential_alerts, "auth_ok", AsyncMock())
        from services import config

        monkeypatch.setattr(config, "HA_URL", household_url)
        monkeypatch.setattr(config, "HA_TOKEN", admin_token)
        return api

    return run


async def test_a_non_admin_token_is_not_taken_for_an_expired_login(template_api):
    """/api/template is admin-only in HA: a kid's valid token gets 401 there."""
    api = template_api("household-admin")

    areas = await ha_client.get_areas("https://ha.example", "kid-token", account="kiddo")

    ha_client.credential_alerts.auth_failed.assert_not_awaited()
    assert areas == {"media_player.x": "Kitchen"}
    assert api.tokens == ["kid-token", "household-admin"]


async def test_no_household_token_for_that_ha_means_no_areas_and_no_alert(template_api):
    api = template_api("household-admin", household_url="https://other-ha.example")

    assert await ha_client.get_areas("https://ha.example", "kid-token", account="kiddo") == {}
    ha_client.credential_alerts.auth_failed.assert_not_awaited()
    assert api.tokens == ["kid-token"]


async def test_success_acknowledges_the_credential(session):
    session(_Resp(status=200, payload=[{"eid": "media_player.x", "a": "Kitchen"}]))
    areas = await ha_client.get_areas("https://ha.example", "tok", account="system account")
    assert areas == {"media_player.x": "Kitchen"}
    ha_client.credential_alerts.auth_ok.assert_awaited_once_with("home_assistant", "system account")
