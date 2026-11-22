"""The media pages say why Music Assistant is not there, in identity's words, and whose server it is.

Before, every case read "Music Assistant is not configured" or "returned HTTP 502", which did
not tell a person whether their own server was down, their token refused, or nothing was set
up at all. Values are made up.
"""
import asyncio

import aiohttp
import pytest
from fastapi import HTTPException

import services.gateway.main as gw


def _creds(**extra):
    async def resolve(request, body=None):
        return {"user": "sam", **extra}
    return resolve


def _run(monkeypatch, creds, rpc=None):
    monkeypatch.setattr(gw, "_resolve_ma_creds", creds)
    if rpc is not None:
        monkeypatch.setattr(gw, "_ma_rpc", rpc)
    with pytest.raises(gw._MediaUpstreamError) as err:
        asyncio.run(gw._media_ma_rpc(object(), "players/all"))
    return str(err.value)


def test_nothing_set_up_says_how_to_set_it_up(monkeypatch):
    msg = _run(monkeypatch, _creds(mass_error="No Music Assistant is set up: add the Music Assistant integration "
                                              "in HomeHub or your own in Accounts & Integrations."))
    assert msg.startswith("No Music Assistant is set up")


def test_an_own_server_that_does_not_answer_is_named_as_theirs(monkeypatch):
    async def down(*a, **k):
        raise aiohttp.ClientConnectionError()

    msg = _run(monkeypatch, _creds(mass_url="http://mine.example.test:8095", mass_token="t", mass_server="own"), down)
    assert msg.startswith("Your own Music Assistant at http://mine.example.test:8095 did not answer")
    assert "switch to the household Music Assistant" in msg


def test_a_refused_token_says_to_update_it(monkeypatch):
    async def refused(*a, **k):
        raise HTTPException(status_code=502, detail="Music Assistant returned HTTP 401: unauthorized")

    msg = _run(monkeypatch, _creds(mass_url="http://music.example.test:8095", mass_token="t", mass_server="household"),
               refused)
    assert msg == "Music Assistant refused your access token. Update it in Accounts & Integrations."
