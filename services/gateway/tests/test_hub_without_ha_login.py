"""Gateway pages and streams for a person with no Home Assistant login of their own (2026-11-17).

Home Assistant is retired and the per-person HA tokens are being removed. Every
page reads the hub with the person's own login when they have one, else with
the hub's own (``services.common.hub_login``), and shows only what their entity
permissions allow. The hub's login never reaches the browser or a query string.
"""
import pytest
from conftest_media import DEFAULT_IDENTITY, mock_upstream

import services.config as config
from services.execution.entity_access import EntityPermissions
from services.gateway import config as gw_config
from services.gateway import dashboard_routes, hub_access, media_events
from services.gateway import main as gateway_main

HUB = "http://hub.test:8123"
SECRET = "hub-secret"
STATES = [
    {"entity_id": "todo.shopping_list", "state": "1",
     "attributes": {"friendly_name": "Shopping List", "supported_features": 15}},
    {"entity_id": "todo.workshop", "state": "0", "attributes": {"friendly_name": "Workshop", "supported_features": 15}},
    {"entity_id": "light.den", "state": "on", "attributes": {}},
    {"entity_id": "light.office", "state": "on", "attributes": {}},
]


@pytest.fixture
def no_login(monkeypatch):
    """A family member with no stored HA token, a configured hub, and only light.den assigned."""
    async def resolve(body):
        return {**DEFAULT_IDENTITY, "is_admin": False, "ha_url": None, "ha_token": None}

    perms = EntityPermissions(assigned=frozenset({"light.den"}), protected=frozenset({"todo.workshop"}))

    async def load(username, is_admin=False):
        return EntityPermissions.everything() if is_admin else perms

    monkeypatch.setattr(gateway_main, "resolve_identity", resolve)
    monkeypatch.setattr(config, "HA_URL", HUB)
    monkeypatch.setattr(config, "INTERNAL_SECRET", SECRET)
    monkeypatch.setattr(gw_config, "INTERNAL_SECRET", SECRET)
    monkeypatch.setattr(dashboard_routes, "load_entity_permissions", load)
    monkeypatch.setattr(hub_access, "load_entity_permissions", load)
    import services.execution.entity_access as entity_access

    monkeypatch.setattr(entity_access, "load_entity_permissions", load)


def _auth(upstream, method, prefix):
    return [call.kwargs["headers"]["Authorization"] for (m, url), calls in upstream.requests.items()
            if m == method and str(url).startswith(prefix) for call in calls]


# -- discovery reads ------------------------------------------------------------------------------

def test_discovery_params_carry_only_a_persons_own_login():
    assert hub_access.discovery_params({"ha_url": "http://own", "ha_token": "t"}) == {
        "ha_url": "http://own", "ha_token": "t"}
    assert hub_access.discovery_params({"user": "kid", "ha_url": None, "ha_token": None}) == {}
    assert hub_access.discovery_params({"user": "kid", "ha_url": "http://own", "ha_token": None}) == {}


async def test_visible_entities_trims_the_hubs_view_to_the_callers(no_login):
    kid = {"user": "kid", "is_admin": False}
    assert [e["entity_id"] for e in await hub_access.visible_entities(kid, STATES)] == ["light.den"]
    own = {"user": "parent", "ha_url": "http://own", "ha_token": "t"}
    assert len(await hub_access.visible_entities(own, STATES)) == len(STATES)
    assert len(await hub_access.visible_entities({"user": "a", "is_admin": True}, STATES)) == len(STATES)


def test_entity_dropdowns_read_through_the_hub_without_a_token_in_the_query(client, upstream, no_login):
    mock_upstream(upstream, "GET", f"{gateway_main.EXECUTION_SVC}/discovery/entities", payload={"entities": STATES})
    resp = client.get("/api/entities")
    assert resp.status_code == 200
    assert [e["entity_id"] for e in resp.json()["entities"]] == ["light.den"]
    (url,) = [str(u) for (m, u), _c in upstream.requests.items() if "/discovery/entities" in str(u)]
    assert "ha_token" not in url and SECRET not in url


def test_entity_dropdowns_say_when_no_hub_is_set_up(client, upstream, no_login):
    mock_upstream(upstream, "GET", f"{gateway_main.EXECUTION_SVC}/discovery/entities", status=503,
                  payload={"detail": "No home hub is set up (HA_URL is not configured)"})
    body = client.get("/api/entities").json()
    assert body["status"] == "FAILURE" and "HA_URL" in body["message"]


# -- pages ----------------------------------------------------------------------------------------

def test_lists_are_read_and_written_with_the_hubs_login(client, upstream, no_login):
    mock_upstream(upstream, "GET", f"{HUB}/api/states", payload=STATES)
    mock_upstream(upstream, "POST", f"{HUB}/api/services/todo/get_items",
                  payload={"service_response": {"todo.shopping_list": {"items": []}}})
    resp = client.get("/api/lists")
    assert resp.status_code == 200, resp.text
    assert [x["entity_id"] for x in resp.json()["lists"]] == ["todo.shopping_list"]
    assert set(_auth(upstream, "POST", f"{HUB}/api/services/todo")) == {f"Bearer {SECRET}"}
    assert SECRET not in resp.text


def test_dashboard_settings_no_longer_503_and_hide_what_the_caller_may_not_see(client, upstream, no_login):
    mock_upstream(upstream, "GET", f"{HUB}/api/homehub/dashboard/prefs", payload={
        "climate_power": {"climate.hall": ["sensor.hall_power"]}, "hidden": ["light.den", "light.office"]})
    resp = client.get("/api/dashboard/prefs")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"climate_power": {}, "hidden": ["light.den"]}


# -- pictures ---------------------------------------------------------------------------------------

def test_a_player_picture_comes_through_the_hub_only_for_a_player_the_caller_may_see(client, upstream, no_login):
    mock_upstream(upstream, "GET", f"{HUB}/api/media_player_proxy/light.den", body=b"img",
                  content_type="image/jpeg")
    ok = client.get("/api/media/imageproxy", params={"path": "/api/media_player_proxy/light.den", "service": "ha"})
    assert ok.status_code == 200
    assert _auth(upstream, "GET", f"{HUB}/api/media_player_proxy") == [f"Bearer {SECRET}"]
    other = client.get("/api/media/imageproxy",
                       params={"path": "/api/media_player_proxy/light.office", "service": "ha"})
    assert other.status_code == 403
    states = client.get("/api/media/imageproxy", params={"path": "/api/states", "service": "ha"})
    assert states.status_code == 403


# -- the live media stream --------------------------------------------------------------------------

def test_the_media_stream_uses_the_hubs_login_and_publishes_only_visible_players(monkeypatch):
    monkeypatch.setattr(config, "HA_URL", HUB)
    monkeypatch.setattr(config, "INTERNAL_SECRET", SECRET)
    published = []
    hub = media_events.MediaEventHub("kid")
    monkeypatch.setattr(hub, "ingest_ha_state", published.append)
    (source,) = media_events._build_sources(hub, {"user": "kid"}, lambda eid: eid == "media_player.kids_room")
    assert (source._ha_url, source._ha_token) == (HUB, SECRET)
    source._on_state({"entity_id": "media_player.kids_room", "state": "playing"})
    source._on_state({"entity_id": "media_player.office", "state": "playing"})
    assert [s["entity_id"] for s in published] == ["media_player.kids_room"]


def test_without_any_hub_the_media_stream_has_no_hub_source(monkeypatch):
    monkeypatch.setattr(config, "HA_URL", None)
    hub = media_events.MediaEventHub("kid")
    assert media_events._build_sources(hub, {"user": "kid"}) == []
