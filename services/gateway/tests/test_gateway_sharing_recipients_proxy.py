"""The gateway must proxy GET /api/users/sharing-recipients.

The activity-sharing audience picker calls this to populate the list of people
a non-admin may share with. Identity served it all along, but the gateway had
no route for it, so the request fell through to the "/api/users/{username}"
routes -- which are PATCH-only -- and returned **405 Method Not Allowed**.

A non-admin therefore got an empty picker and "Everyone" as the only audience,
which is the exact opposite of the opt-in that endpoint exists to protect. The
live failure was only visible to a non-admin caller, which is why it survived.

Identity's own view of who may share with whom is authoritative and is tested
in services/identity/tests/test_sharing_recipients.py. The gateway's only job
here is to forward the caller's own credentials and relay the result.
"""
import json
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app

RECIPIENTS = [
    {"username": "default", "display_name": "Default"},
    {"username": "jeremiah", "display_name": "Jeremiah"},
]


def _patch_identity(monkeypatch, status=200, payload=None, captured=None):
    """Capture whatever the gateway forwards to identity.

    ``shared_http_client`` also ships request logs, so a naive capture would be
    overwritten by the logging POST. Only identity-bound calls are recorded.
    """
    captured = captured if captured is not None else {}
    captured["calls"] = []
    identity_svc = str(gateway_main.IDENTITY_SVC)

    class _Resp:
        def __init__(self):
            self.status = status
            self.text = json.dumps(payload if payload is not None else {})

        async def json(self):
            return payload if payload is not None else {}

        async def read(self):
            return json.dumps(payload if payload is not None else {}).encode()

    class _Client:
        async def _record(self, verb, url, **kwargs):
            if url.startswith(identity_svc):
                captured["calls"].append(
                    {"verb": verb, "url": url, "headers": kwargs.get("headers") or {}}
                )
            return _Resp()

        async def get(self, url, **kw):
            return await self._record("get", url, **kw)

        async def post(self, url, **kw):
            return await self._record("post", url, **kw)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    @asynccontextmanager
    async def fake():
        yield _Client()

    monkeypatch.setattr(gateway_main, "shared_http_client", fake)
    return captured


@pytest.fixture
def client():
    return TestClient(app)


def test_reaching_the_endpoint_is_not_a_405(client, monkeypatch):
    """The regression itself: a plain GET used to answer 405."""
    cap = _patch_identity(monkeypatch, payload=RECIPIENTS)
    r = client.get("/api/users/sharing-recipients")
    assert r.status_code == 200
    assert r.json() == RECIPIENTS
    assert len(cap["calls"]) == 1


def test_forwards_to_identity_verbatim(client, monkeypatch):
    cap = _patch_identity(monkeypatch, payload=RECIPIENTS)
    client.get("/api/users/sharing-recipients")
    assert cap["calls"][0]["url"] == f"{gateway_main.IDENTITY_SVC}/api/users/sharing-recipients"
    assert cap["calls"][0]["verb"] == "get"


def test_forwards_the_callers_own_credentials(client, monkeypatch):
    """Identity scopes the list by the authenticated user, so the token has to
    travel with the request -- an admin token here would return everyone."""
    cap = _patch_identity(monkeypatch, payload=RECIPIENTS)
    client.get(
        "/api/users/sharing-recipients",
        headers={"Authorization": "Bearer non-admin-token"},
    )
    assert cap["calls"][0]["headers"].get("Authorization") == "Bearer non-admin-token"


def test_does_not_invent_identity_when_the_token_is_absent(client, monkeypatch):
    """No token must mean no Authorization header at all -- inventing one would
    fall back to the default admin, the failure mode this endpoint guards."""
    cap = _patch_identity(monkeypatch, payload=[])
    client.get("/api/users/sharing-recipients")
    assert "Authorization" not in cap["calls"][0]["headers"]


def test_relays_an_empty_list_rather_than_failing(client, monkeypatch):
    """A user who has shared with nobody still gets a usable 200."""
    _patch_identity(monkeypatch, payload=[])
    r = client.get("/api/users/sharing-recipients")
    assert r.status_code == 200
    assert r.json() == []


def test_relays_identity_refusals_unchanged(client, monkeypatch):
    """A 403 from identity must not be laundered into a 200 with an empty
    list, which would look to the UI like "you may share with nobody"."""
    _patch_identity(monkeypatch, status=403, payload={"detail": "Forbidden"})
    r = client.get("/api/users/sharing-recipients")
    assert r.status_code == 403


def test_the_literal_path_is_not_captured_as_a_username(client, monkeypatch):
    """A route-ordering slip would instead look up a user literally named
    "sharing-recipients" and the UI would see an empty picker again."""
    cap = _patch_identity(monkeypatch, payload=RECIPIENTS)
    client.get("/api/users/sharing-recipients")
    forwarded = cap["calls"]
    assert len(forwarded) == 1
    assert forwarded[0]["url"] == f"{gateway_main.IDENTITY_SVC}/api/users/sharing-recipients"
