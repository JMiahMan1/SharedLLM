"""Voice and chat control only the entities the hub exposes to them (voice_exposure.py).

The hub is a local aiohttp server answering ``/api/homehub/exposed_entities``; the routes are driven through
the real execution app. Every name and address here is made up.
"""
from __future__ import annotations

import os

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from fastapi.testclient import TestClient

import services.execution.entity_access as entity_access
import services.execution.main as exec_main
from services.execution import voice_exposure
from services.execution.entity_access import EntityPermissions, verify_entity_access
from services.execution.http_client import close_all_sessions
from services.execution.voice_exposure import EntityNotExposed

TOKEN = "hub-token"


class _Ctx:
    def __init__(self, via=None, is_admin=True, ha_url="http://hub.test", ha_token=TOKEN):
        self.user = "pat"
        self.is_admin = is_admin
        self.via = via
        self.ha_url = ha_url
        self.ha_token = ha_token


@pytest.fixture
def answers(monkeypatch):
    """Answer fetch_exposed from a dict, recording the entities asked about."""
    asked: list[str] = []
    table: dict[str, bool] = {}

    async def fake(ha_url, ha_token, entity_id):
        asked.append(entity_id)
        return table.get(entity_id, False)

    monkeypatch.setattr(voice_exposure, "fetch_exposed", fake)
    return table, asked


async def test_the_apps_own_controls_are_not_gated(answers):
    _table, asked = answers
    assert await verify_entity_access(_Ctx(via=None), "lock.gate")
    assert asked == []


async def test_voice_may_control_an_exposed_entity(answers):
    table, asked = answers
    table["light.den"] = True
    assert await verify_entity_access(_Ctx(via="assistant"), "light.den")
    assert asked == ["light.den"]


async def test_voice_is_refused_an_entity_the_hub_does_not_expose_even_for_an_admin(answers):
    with pytest.raises(EntityNotExposed, match="Voice can control"):
        await verify_entity_access(_Ctx(via="assistant", is_admin=True), "lock.gate")


async def test_a_get_service_only_reads_and_is_not_gated(answers):
    _table, asked = answers
    assert await verify_entity_access(_Ctx(via="assistant"), "weather.home", service="get_forecasts")
    assert asked == []


async def test_without_any_hub_the_check_fails_fast(monkeypatch):
    import services.config as config

    monkeypatch.setattr(config, "HA_URL", None)
    with pytest.raises(EntityNotExposed, match="HA_URL"):
        await voice_exposure.require_exposed(_Ctx(via="assistant", ha_url=None), "light.den")


async def test_without_a_login_of_their_own_the_hubs_login_asks(answers, monkeypatch):
    """A person with no stored HA token (every family member, once HA is gone) is checked with the hub's login."""
    import services.config as config

    table, asked = answers
    table["light.den"] = True
    monkeypatch.setattr(config, "HA_URL", "http://hub.test")
    await voice_exposure.require_exposed(_Ctx(via="assistant", ha_url=None, ha_token=None), "light.den")
    assert asked


async def test_a_dict_context_works_too(answers):
    table, _asked = answers
    table["light.den"] = True
    await voice_exposure.require_exposed({"via": "assistant", "ha_url": "http://hub.test", "ha_token": TOKEN},
                                         "light.den")


# -- the hub's answer over HTTP --------------------------------------------------------------

@pytest.fixture
async def hub_url():
    seen: list[dict] = []

    async def exposed(request: web.Request) -> web.Response:
        seen.append({"auth": request.headers.get("Authorization"), "entity_id": request.query.get("entity_id")})
        eid = request.query.get("entity_id")
        if eid == "light.broken":
            return web.json_response({"nope": 1})
        if eid == "light.error":
            return web.json_response({"message": "boom"}, status=500)
        return web.json_response({"entity_id": eid, "exposed": eid == "light.den"})

    app = web.Application()
    app.router.add_get("/api/homehub/exposed_entities", exposed)
    server = TestServer(app)
    await server.start_server()
    try:
        yield str(server.make_url("")).rstrip("/"), seen
    finally:
        await server.close()
        await close_all_sessions()


async def test_fetch_exposed_asks_the_hub_with_the_callers_token(hub_url):
    url, seen = hub_url
    assert await voice_exposure.fetch_exposed(url, TOKEN, "light.den") is True
    assert await voice_exposure.fetch_exposed(url, TOKEN, "lock.gate") is False
    assert seen[0] == {"auth": f"Bearer {TOKEN}", "entity_id": "light.den"}


@pytest.mark.parametrize("eid,match", [("light.error", "HTTP 500"), ("light.broken", "no exposed flag")])
async def test_fetch_exposed_fails_fast_on_a_bad_answer(hub_url, eid, match):
    url, _seen = hub_url
    with pytest.raises(EntityNotExposed, match=match):
        await voice_exposure.fetch_exposed(url, TOKEN, eid)


async def test_fetch_exposed_fails_fast_when_the_hub_is_unreachable():
    try:
        with pytest.raises(EntityNotExposed, match="unreachable"):
            await voice_exposure.fetch_exposed("http://127.0.0.1:9", TOKEN, "light.den")
    finally:
        await close_all_sessions()


# -- through the execution app -----------------------------------------------------------------

def _post(monkeypatch, path, body):
    async def everything(username, is_admin=False):
        return EntityPermissions.everything()

    monkeypatch.setattr(entity_access, "load_entity_permissions", everything)
    client = TestClient(exec_main.app)
    return client.post(path, headers={"X-Internal-Secret": os.environ["INTERNAL_SECRET"]}, json=body)


def test_ha_service_from_chat_on_an_unexposed_entity_is_a_403_with_the_reason(monkeypatch, answers):
    called = []

    async def fake_call(*a, **k):
        called.append(a)
        return {"ok": True}

    monkeypatch.setattr(exec_main.ha_client, "call_service", fake_call)
    resp = _post(monkeypatch, "/execute/ha_service", {
        "user_context": {"user": "pat", "via": "assistant", "ha_url": "http://hub.test", "ha_token": TOKEN},
        "domain": "lock", "service": "unlock", "entity_id": "lock.gate"})
    assert resp.status_code == 403
    assert "Voice and chat may not control lock.gate" in resp.json()["message"]
    assert called == []


def test_security_handler_refuses_an_unexposed_lock_for_voice(monkeypatch, answers):
    called = []

    async def fake_call(*a, **k):
        called.append(a)
        return {"ok": True}

    monkeypatch.setattr(exec_main.ha_client, "call_service", fake_call)
    resp = _post(monkeypatch, "/execute/security", {
        "user_context": {"user": "pat", "is_admin": True, "via": "assistant", "ha_url": "http://hub.test",
                         "ha_token": TOKEN},
        "entity_id": "lock.gate", "action": "lock"})
    assert resp.status_code == 403 and called == []
