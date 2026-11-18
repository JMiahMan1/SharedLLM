"""Every device feature for a person with no Home Assistant login of their own (2026-11-17).

Home Assistant is retired and the per-person HA tokens are being removed. A
caller without one reaches homehub with the hub's own login
(``services.common.hub_login``): never another person's token, and only the
entities their permissions allow. With no hub configured at all, each route
says so instead of failing on an assertion or answering with nothing.
"""
import os

import pytest
from fastapi.testclient import TestClient

import services.config as config
import services.execution.entity_access as entity_access
import services.execution.main as exec_main
from services.common import hub_login
from services.execution import hardware_router, satellite_timers
from services.execution.entity_access import EntityPermissions
from services.execution.handlers import climate, security, timer
from services.execution.handlers import light as light_handler
from services.execution.handlers import media as media_handler
from services.execution.schemas import ExecutionResult, LightControlRequest, TimerRequest, UserContext

client = TestClient(exec_main.app)

HUB_URL = "http://hub.test:8123"
HUB_SECRET = "hub-internal-secret"
KID = {"user": "kid1"}


def _headers() -> dict:
    return {"X-Internal-Secret": os.environ["INTERNAL_SECRET"]}


@pytest.fixture
def hub(monkeypatch):
    """A configured hub; kid1 may use only the den light and the playroom speaker and TV."""
    monkeypatch.setattr(config, "HA_URL", HUB_URL)
    monkeypatch.setattr(config, "HOMEHUB_URL", HUB_URL)
    monkeypatch.setattr(config, "INTERNAL_SECRET", HUB_SECRET)
    allowed = frozenset({"light.den", "media_player.playroom", "lock.kids_window"})

    async def permissions(username, is_admin=False):
        return EntityPermissions.everything() if is_admin else EntityPermissions(assigned=allowed)

    monkeypatch.setattr(entity_access, "load_entity_permissions", permissions)
    monkeypatch.setattr(exec_main, "load_entity_permissions", permissions)


@pytest.fixture
def commands(monkeypatch):
    """Device commands that reached the router, with the login they carried."""
    sent: list[tuple[str, str, str | None, str | None]] = []

    async def fake(ctx, domain, service, entity_id, service_data=None, service_name="ha_service"):
        sent.append((f"{domain}.{service}", entity_id, ctx.ha_url, ctx.ha_token))
        return ExecutionResult(status="SUCCESS", message="sent", service=service_name, detail={"route": "ha"})

    monkeypatch.setattr(hardware_router, "execute_device_command", fake)
    return sent


# -- the rule ---------------------------------------------------------------------------------

def test_own_login_wins_then_the_hubs_then_none(monkeypatch):
    monkeypatch.setattr(config, "HA_URL", HUB_URL + "/")
    monkeypatch.setattr(config, "HOMEHUB_URL", HUB_URL + "/")
    monkeypatch.setattr(config, "INTERNAL_SECRET", HUB_SECRET)
    assert hub_login.hub_login({"ha_url": "http://own", "ha_token": "t"}) == ("http://own", "t")
    assert hub_login.hub_login(UserContext(user="kid1")) == (HUB_URL, HUB_SECRET)
    assert hub_login.hub_login({"ha_url": "http://own", "ha_token": None}) == (HUB_URL, HUB_SECRET)
    assert hub_login.hub_login(None) == (HUB_URL, HUB_SECRET)
    monkeypatch.setattr(config, "HA_URL", None)
    monkeypatch.setattr(config, "HOMEHUB_URL", "")
    assert hub_login.hub_login({}) is None
    assert hub_login.hub_pair({}) == ("", "")


def test_with_hub_login_leaves_an_own_login_alone(hub):
    own = UserContext(user="p", ha_url="http://own", ha_token="t")
    assert hardware_router.with_hub_login(own) is own
    hub_ctx = hardware_router.with_hub_login(UserContext(user="kid1"))
    assert (hub_ctx.ha_url, hub_ctx.ha_token) == (HUB_URL, HUB_SECRET)


# -- lights, climate, locks ----------------------------------------------------------------------

async def test_a_light_is_switched_with_the_hubs_login(hub, commands, monkeypatch):
    async def no_state(*a, **k):
        return None

    monkeypatch.setattr(light_handler.ha_client, "get_state", no_state)
    res = await light_handler.handle_light(
        LightControlRequest(user_context=UserContext(**KID), entity_id="light.den", action="turn_on"))
    assert res.status == "SUCCESS", res.message
    assert commands == [("light.turn_on", "light.den", HUB_URL, HUB_SECRET)]


async def test_without_any_hub_a_light_says_so(monkeypatch, commands):
    monkeypatch.setattr(config, "HA_URL", None)
    monkeypatch.setattr(config, "HOMEHUB_URL", "")
    res = await light_handler.handle_light(
        LightControlRequest(user_context=UserContext(**KID), entity_id="light.den", action="turn_on"))
    assert res.status == "FAILURE" and res.message == hub_login.NO_HUB_MESSAGE
    assert commands == []


async def test_a_thermostat_no_longer_dies_on_an_assertion(hub, commands, monkeypatch):
    async def allowed(*a, **k):
        return True

    monkeypatch.setattr(entity_access, "verify_entity_access", allowed)
    res = await climate.handle_climate(
        climate.ClimateRequest(user_context=UserContext(user="kid1"), entity_id="climate.den", temperature=68))
    assert res.status == "SUCCESS", res.message


async def test_a_lock_reads_and_commands_with_the_hubs_login_only_if_the_caller_may(hub, monkeypatch):
    reads, calls = [], []

    async def get_state(url, token, entity_id, *a, **k):
        reads.append((url, token, entity_id))
        return {"entity_id": entity_id, "state": "locked"}

    async def call_service(url, token, domain, service, entity_id="", *a, **k):
        calls.append((url, token, f"{domain}.{service}", entity_id))
        return {"ok": True}

    monkeypatch.setattr(security.ha_client, "get_state", get_state)
    monkeypatch.setattr(security.ha_client, "call_service", call_service)
    ok = await security.handle_security(
        security.SecurityRequest(user_context=UserContext(**KID), entity_id="lock.kids_window", action="status"))
    assert ok.status == "SUCCESS" and reads == [(HUB_URL, HUB_SECRET, "lock.kids_window")]

    denied = await security.handle_security(
        security.SecurityRequest(user_context=UserContext(**KID), entity_id="lock.front_door", action="lock"))
    assert denied.status == "FAILURE" and "Access Denied" in denied.message
    assert calls == []

    ok = await security.handle_security(
        security.SecurityRequest(user_context=UserContext(**KID), entity_id="lock.kids_window", action="lock"))
    assert ok.status == "SUCCESS" and calls == [(HUB_URL, HUB_SECRET, "lock.lock", "lock.kids_window")]


# -- routes that used to check nothing -----------------------------------------------------------

def test_tv_cast_checks_the_tv_and_uses_the_hubs_login(hub, monkeypatch):
    seen = []

    async def fake_cast(req):
        seen.append((req.media_player_entity_id, req.user_context.ha_url, req.user_context.ha_token))
        return ExecutionResult(status="SUCCESS", message="cast", service="tv_cast")

    monkeypatch.setattr(media_handler, "handle_tv_cast", fake_cast)
    body = {"user_context": KID, "media_content_id": "http://example.test/v.mp4"}
    denied = client.post("/execute/tv_cast", headers=_headers(),
                         json={**body, "media_player_entity_id": "media_player.study_tv"})
    assert denied.status_code == 403 and seen == []
    ok = client.post("/execute/tv_cast", headers=_headers(),
                     json={**body, "media_player_entity_id": "media_player.playroom"})
    assert ok.status_code == 200, ok.text
    assert seen == [("media_player.playroom", HUB_URL, HUB_SECRET)]


def test_a_voice_command_on_someone_elses_light_is_refused(hub, commands):
    resp = client.post("/execute/voice/command", headers=_headers(),
                       json={"transcript": "turn on the light", "user_context": KID, "entity_id": "light.study"})
    assert resp.json()["status"] == "FAILURE" and "Access denied" in resp.json()["message"]
    assert commands == []


def test_announcing_on_a_speaker_the_caller_may_not_use_is_403(hub, monkeypatch):
    async def no_index(*a, **k):
        return None

    async def no_states(*a, **k):
        return []

    import services.execution.capability_index as capability_index

    monkeypatch.setattr(capability_index, "routing_index", no_index)
    monkeypatch.setattr(exec_main.ha_client, "get_states", no_states)
    resp = client.post("/execute/announce", headers=_headers(),
                       json={"user_context": KID, "message": "dinner", "entity_id": "media_player.study_tv"})
    assert resp.status_code == 403


def test_the_tv_stage_never_reads_the_raw_ha_url_env(hub, monkeypatch):
    """The raw HA_URL env is the retired HA host; only services.config.HA_URL points at the hub."""
    monkeypatch.setenv("HA_URL", "https://retired-ha.test")
    monkeypatch.setenv("HA_TOKEN", "retired-token")

    async def nobody(**kw):
        return None

    monkeypatch.setattr(exec_main, "resolve_internal_user", nobody)
    import asyncio

    assert asyncio.run(exec_main._stage_ha_creds("kid1")) == (HUB_URL, HUB_SECRET)


# -- timers on voice satellites --------------------------------------------------------------------

async def test_a_timer_tells_its_satellite_with_the_hubs_login(hub, monkeypatch):
    sent = []

    async def fake_send(timer_obj, event, url, token, *a, **k):
        sent.append((event, url, token))
        return {"ok": True}

    monkeypatch.setattr(satellite_timers, "send", fake_send)
    req = TimerRequest(user_context=UserContext(**KID), action="list")
    await timer._tell_satellite({"satellite": "assist_satellite.den"}, "started", req)
    assert sent == [("started", HUB_URL, HUB_SECRET)]


def test_the_stack_login_goes_only_to_homehub_never_to_a_real_home_assistant(monkeypatch):
    """HA stays supported: HA_URL may be a real Home Assistant, which must never get INTERNAL_SECRET."""
    monkeypatch.setattr(config, "HA_URL", "http://real-ha.test:8123")
    monkeypatch.setattr(config, "HOMEHUB_URL", "http://hub.test:8123")
    url, token = hub_login.hub_login(UserContext(user="kid1"))
    assert url == "http://hub.test:8123" and token == config.INTERNAL_SECRET
    # A person's own HA login still goes to their own HA.
    assert hub_login.hub_login({"ha_url": "http://real-ha.test:8123", "ha_token": "t"}) == ("http://real-ha.test:8123", "t")
