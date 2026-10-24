"""Linking a member's own Spotify: "not configured" until the household's app is set up."""
import time
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from services.common import spotify
from services.gateway import main as gateway_main
from services.gateway.main import INTERNAL_SECRET, app

CONFIGURED = [
    {"key": "spotify_client_id", "value": "cid"},
    {"key": "spotify_client_secret", "value": "csecret"},
    {"key": "spotify_redirect_uri", "value": "https://jarvis.example/api/spotify/callback"},
    {"key": "unrelated", "value": "x"},
]


class _Resp:
    def __init__(self, status, body):
        self.status, self._body = status, body

    async def json(self, content_type=None):
        return self._body

    async def text(self):
        return ""


@pytest.fixture
def wired(monkeypatch):
    calls: list[dict] = []
    state = {"settings": [], "token": {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600}}

    class _Client:
        async def get(self, url, **kw):
            calls.append({"method": "GET", "url": url, **kw})
            if url.endswith("/api/settings"):
                return _Resp(200, state["settings"])
            if url.endswith("/v1/me"):
                return _Resp(200, {"id": "emma1", "display_name": "Emma S"})
            return _Resp(404, {})

        async def post(self, url, **kw):
            calls.append({"method": "POST", "url": url, **kw})
            return _Resp(200, state["token"])

        async def put(self, url, **kw):
            calls.append({"method": "PUT", "url": url, **kw})
            return _Resp(200, {"linked": True})

        async def request(self, method, url, **kw):
            calls.append({"method": method, "url": url, **kw})
            return _Resp(200, {"linked": False})

    @asynccontextmanager
    async def fake():
        yield _Client()

    async def _who(_request, body=None):
        return {"user": "emma"}

    monkeypatch.setattr(gateway_main, "borrow_http_client", fake)
    monkeypatch.setattr(gateway_main, "_resolve_identity_from_request", _who)
    return TestClient(app, headers={"Authorization": "Bearer emma-key"}), calls, state


def test_not_configured_is_said_plainly_and_linking_is_refused(wired):
    client, _, _ = wired
    body = client.get("/api/spotify/status").json()
    assert body["configured"] is False and body["missing"] == list(spotify.SETTING_KEYS) and body["linked"] is False
    r = client.post("/api/spotify/link")
    assert r.status_code == 409 and "not configured" in r.text


def test_linking_sends_the_browser_to_spotify_with_a_signed_state(wired):
    client, _, state = wired
    state["settings"] = CONFIGURED
    url = client.post("/api/spotify/link").json()["url"]
    q = parse_qs(urlsplit(url).query)
    assert url.startswith(spotify.AUTHORIZE_URL) and q["client_id"] == ["cid"]
    assert spotify.verify_state(INTERNAL_SECRET, q["state"][0]) == "emma"


def test_the_callback_exchanges_the_code_and_keeps_the_tokens_in_identity(wired):
    client, calls, state = wired
    state["settings"] = CONFIGURED
    signed = spotify.sign_state(INTERNAL_SECRET, "emma", now=time.time())
    r = client.get("/api/spotify/callback", params={"state": signed, "code": "C0DE"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/identity?spotify=linked"
    token_call = next(c for c in calls if c["method"] == "POST")
    assert token_call["url"] == spotify.TOKEN_URL and token_call["data"]["code"] == "C0DE"
    stored = next(c for c in calls if c["method"] == "PUT")
    assert stored["url"].endswith("/api/internal/linked-accounts/spotify/emma")
    assert stored["json"]["access_token"] == "AT" and stored["json"]["account_name"] == "Emma S"


def test_a_forged_or_cancelled_callback_stores_nothing(wired):
    client, calls, state = wired
    state["settings"] = CONFIGURED
    forged = spotify.sign_state("not-the-secret", "dad", now=time.time())
    r = client.get("/api/spotify/callback", params={"state": forged, "code": "C"}, follow_redirects=False)
    assert r.headers["location"] == "/identity?spotify=failed"
    ok = spotify.sign_state(INTERNAL_SECRET, "emma", now=time.time())
    r = client.get("/api/spotify/callback", params={"state": ok, "error": "access_denied"}, follow_redirects=False)
    assert r.headers["location"] == "/identity?spotify=cancelled"
    assert not any(c["method"] in ("POST", "PUT") for c in calls)


def test_a_refused_token_exchange_reports_failure(wired):
    client, calls, state = wired
    state["settings"] = CONFIGURED
    state["token"] = {"error": "invalid_grant"}
    ok = spotify.sign_state(INTERNAL_SECRET, "emma", now=time.time())
    r = client.get("/api/spotify/callback", params={"state": ok, "code": "C"}, follow_redirects=False)
    assert r.headers["location"] == "/identity?spotify=failed"
    assert not any(c["method"] == "PUT" for c in calls)
