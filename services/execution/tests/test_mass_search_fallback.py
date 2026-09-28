"""BUG-17 (P2-T8): mass_client.search raises typed MASearchError; caller falls back.

`mass_client.search` used to swallow every exception (and MA error_code
responses / timeouts) into `[]`, so `search_ma`'s HA fallback never ran — an
empty list looked like "no matches" instead of "direct search failed". It also
never received `artist`/`album`/`library_only`, which only the HA path honored.
"""
import asyncio
import json
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest

import services.execution.main as exec_main
from services.execution.handlers import mass_client, mass_ha_client
from services.execution.handlers.mass_client import MASearchError

P = "services.execution.handlers.mass_client."
MA_URL = "http://ma.local:8095"
MA_TOKEN = "ma-token"
CREDS = {
    "mass_url": MA_URL,
    "mass_token": MA_TOKEN,
    "ha_url": "http://ha.local:8123",
    "ha_token": "ha-token",
    "mass_config_entry_id": "ce1",
}


class _FakeMsg:
    def __init__(self, data: str):
        self.type = aiohttp.WSMsgType.TEXT
        self.data = data


class _FakeWS:
    def __init__(self, search_response: dict):
        self._greetings = ['{"type":"hello"}', '{"type":"auth_ok"}']
        self._search_response = search_response
        self.sent = []

    async def receive_str(self):
        return self._greetings.pop(0)

    async def send_str(self, data):
        self.sent.append(data)

    async def receive(self):
        return _FakeMsg(json.dumps(self._search_response))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    def __init__(self, ws: _FakeWS):
        self._ws = ws

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def ws_connect(self, url, **kwargs):
        return self._ws


def test_mass_client_search_raises_typed_error_on_connect_failure():
    """Connection failures surface as MASearchError, not a silent []."""

    def _boom(_url):
        raise aiohttp.ClientConnectionError("nope")

    with patch(P + "_mass_session", new=_boom):
        with pytest.raises(MASearchError):
            asyncio.run(mass_client.search(MA_URL, MA_TOKEN, "hobbit"))


def test_mass_client_search_raises_on_ma_error_code():
    """An MA `error_code` response is a failure, not an empty result set."""
    ws = _FakeWS({"message_id": "mass_search", "error_code": "error", "details": "boom"})
    with patch(P + "_mass_session", new=lambda _url: _FakeSession(ws)):
        with pytest.raises(MASearchError):
            asyncio.run(mass_client.search(MA_URL, MA_TOKEN, "hobbit"))


def test_search_ma_falls_back_to_ha_when_direct_search_raises():
    """A typed direct-search error triggers the HA proxy fallback."""

    async def _raise(*args, **kwargs):
        raise MASearchError("ws down")

    ha_search = AsyncMock(return_value=[{"name": "Song"}])
    with patch.object(exec_main, "_resolve_mass_ha_creds", new=AsyncMock(return_value=CREDS)), \
         patch(P + "search", new=_raise), \
         patch.object(mass_ha_client, "search", new=ha_search):
        result = asyncio.run(
            exec_main.search_ma(user_id="u", query="q", artist="A", album="B", library_only=False)
        )

    assert result["status"] == "SUCCESS"
    assert result["source"] == "ha"
    assert result["results"] == [{"name": "Song"}]
    ha_search.assert_awaited_once()
    _, kwargs = ha_search.await_args
    assert kwargs["artist"] == "A"
    assert kwargs["album"] == "B"
    assert kwargs["library_only"] is False


def test_search_ma_empty_direct_result_is_authoritative():
    """Legitimately-empty direct results must NOT fall through to HA."""

    direct_search = AsyncMock(return_value=[])
    ha_search = AsyncMock(return_value=[{"name": "Song"}])
    with patch.object(exec_main, "_resolve_mass_ha_creds", new=AsyncMock(return_value=CREDS)), \
         patch(P + "search", new=direct_search), \
         patch.object(mass_ha_client, "search", new=ha_search):
        result = asyncio.run(exec_main.search_ma(user_id="u", query="zzz"))

    assert result["status"] == "SUCCESS"
    assert result["source"] == "music_assistant"
    assert result["results"] == []
    ha_search.assert_not_awaited()


def test_search_ma_forwards_filters_to_direct_search():
    """artist/album/library_only reach the direct search too (were ignored)."""

    direct_search = AsyncMock(return_value=[{"name": "Song"}])
    with patch.object(exec_main, "_resolve_mass_ha_creds", new=AsyncMock(return_value=CREDS)), \
         patch(P + "search", new=direct_search):
        result = asyncio.run(
            exec_main.search_ma(user_id="u", query="q", artist="A", album="B", library_only=False, limit=5)
        )

    assert result["source"] == "music_assistant"
    _, kwargs = direct_search.await_args
    assert kwargs["artist"] == "A"
    assert kwargs["album"] == "B"
    assert kwargs["library_only"] is False
    assert kwargs["limit"] == 5
