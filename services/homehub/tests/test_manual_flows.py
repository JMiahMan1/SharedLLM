"""Adding by hand every integration the hub drives: forms, checks, and the records each creates.

Every id, address, login and name here is made up (example.com, RFC 5737 addresses).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from services.homehub import flows, manual_flows
from services.homehub.api import build_app
from services.homehub.auth import Auth
from services.homehub.core import Area, ConfigEntry, EntityEntry, Hub
from services.homehub.discovery import DiscoveryManager
from services.homehub.flows import FlowError, NeedsInput
from services.homehub.store import Store
from services.homehub.tests.test_flows import FakeRuntime

SECRET = "internal-secret"
H = {"Authorization": f"Bearer {SECRET}"}


@pytest.fixture
def rt(tmp_path):
    store = Store(str(tmp_path / "hub.db"), Fernet.generate_key().decode())
    hub = Hub(time_zone="Etc/GMT+7", location={"latitude": 33.4, "longitude": -111.9})
    hub.store = store
    hub.areas["den"] = Area("den", "Den")
    runtime = FakeRuntime(hub, store)
    runtime.discovery = DiscoveryManager(hub, store, notifier=None)
    return runtime


async def add(rt, domain, input_=None, **body):
    finding = manual_flows.manual_finding(domain, {"input": input_ or {}, **body})
    return await flows.adopt(rt, finding, {"input": input_ or {}, **body})


def test_the_list_covers_every_kind_with_fields(rt):
    out = {f["domain"]: f for f in manual_flows.manual_list(rt.hub)}
    assert out["sun"]["kind"] == "helper" and out["sun"]["single"] is True and out["sun"]["configured"] is False
    assert out["roku"]["kind"] == "local" and out["esphome_builder"]["kind"] == "service"
    assert [f["key"] for f in out["emporia_vue"]["fields"]] == ["email", "password"]
    kinds = [f["kind"] for f in manual_flows.manual_list(rt.hub)]
    assert kinds == sorted(kinds, key=list(manual_flows.KINDS).index)  # grouped: local, service, cloud, helper


def test_choices_come_from_the_registry(rt):
    rt.hub.entities["switch.example_plug"] = EntityEntry("switch.example_plug", "esphome", "u1")
    rt.hub.states.set("zone.work", "0", {"friendly_name": "Work"})
    fields = {f["key"]: f for f in manual_flows.fields_for(rt.hub, "switch_as_x")}
    assert [o["value"] for o in fields["entity_id"]["options"]] == ["switch.example_plug"]
    zone = manual_flows.fields_for(rt.hub, "open_meteo")[0]
    assert {"value": "zone.work", "label": "Work"} in zone["options"]


def test_an_address_is_required_only_where_there_is_one():
    with pytest.raises(FlowError):
        manual_flows.manual_finding("cast", {})
    with pytest.raises(FlowError):
        manual_flows.manual_finding("cast", {"host": "bad host!"})
    assert manual_flows.manual_finding("sun", {})["integration"] == "sun"
    assert manual_flows.manual_finding("roku", {"host": "192.0.2.9"})["host"] == "192.0.2.9"  # pairing's


async def test_sun_is_one_per_hub_with_its_sensors(rt):
    out = await add(rt, "sun")
    uids = sorted(rt.hub.entities[e].unique_id.rsplit("-", 1)[-1] for e in out["entities"])
    assert "next_rising" in uids and "solar_elevation" in uids and len(uids) == 9
    assert rt.started == [out["entry_id"]]
    with pytest.raises(FlowError) as err:
        await add(rt, "sun")
    assert err.value.status == 409


async def test_lists_shopping_and_switch_as_x(rt):
    out = await add(rt, "local_todo", {"todo_list_name": "Chores for Robin"})
    assert out["entities"] == ["todo.chores_for_robin"]
    with pytest.raises(FlowError):
        await add(rt, "local_todo", {"todo_list_name": "Chores for Robin"})
    out = await add(rt, "shopping_list")
    assert out["entities"] == ["todo.shopping_list"]
    rt.hub.entities["switch.example_plug"] = EntityEntry("switch.example_plug", "esphome", "u1")
    with pytest.raises(NeedsInput):
        await add(rt, "switch_as_x", {"entity_id": "switch.example_plug"})
    out = await add(rt, "switch_as_x", {"entity_id": "switch.example_plug", "target_domain": "light"},
                    name="Porch string lights")
    ent = rt.hub.entities[out["entities"][0]]
    assert ent.domain == "light" and ent.unique_id == out["entry_id"]
    assert rt.hub.config_entries[out["entry_id"]].options == {"entity_id": "switch.example_plug",
                                                              "target_domain": "light", "invert": False}
    with pytest.raises(FlowError):
        await add(rt, "switch_as_x", {"entity_id": "switch.example_plug", "target_domain": "fan"})


async def test_weather_from_a_zone_and_from_the_nws(rt):
    out = await add(rt, "open_meteo", {"zone": "zone.home"})
    assert rt.hub.entities[out["entities"][0]].unique_id == "zone.home"
    with pytest.raises(NeedsInput):
        await add(rt, "nws", {})
    out = await add(rt, "nws", {"api_key": "alex@example.com"})
    entry = rt.hub.config_entries[out["entry_id"]]
    assert entry.data["location"] == {"latitude": 33.4, "longitude": -111.9}
    weather = [e for e in out["entities"] if e.startswith("weather.")]
    assert len(weather) == 1 and rt.hub.entities[weather[0]].unique_id == "33.4_-111.9"


async def test_mjpeg_asks_for_a_login_then_adds_the_camera(rt, monkeypatch):
    calls = []

    async def get(url, *, auth=None, read=0, timeout=0):
        calls.append((url, auth))
        if auth is None:
            return 401, "text/html", b""
        return 200, "image/jpeg", b"\xff\xd8\xff"

    monkeypatch.setattr(manual_flows, "http_get", get)
    with pytest.raises(NeedsInput) as need:
        await add(rt, "mjpeg", {"mjpeg_url": "http://192.0.2.50/video", "still_image_url": "http://192.0.2.50/snap"})
    assert "login" in need.value.message
    out = await add(rt, "mjpeg", {"mjpeg_url": "http://192.0.2.50/video", "still_image_url": "http://192.0.2.50/snap",
                                  "username": "sam", "password": "pw"}, area_id="den")
    entry = rt.hub.config_entries[out["entry_id"]]
    assert entry.data["password"] == "pw" and calls[-1][0] == "http://192.0.2.50/snap"
    cam = rt.hub.entities[out["entities"][0]]
    assert cam.domain == "camera" and cam.unique_id == entry.entry_id
    assert rt.hub.devices[cam.device_id].area_id == "den"
    assert "pw" not in str(rt.store._exec("SELECT doc FROM config_entries").fetchall())  # sealed at rest


async def test_mjpeg_refuses_a_page_that_is_not_a_picture(rt, monkeypatch):
    async def get(url, **_):
        return 200, "text/html", b"<html>"

    monkeypatch.setattr(manual_flows, "http_get", get)
    with pytest.raises(FlowError) as err:
        await add(rt, "mjpeg", {"mjpeg_url": "http://192.0.2.50/"})
    assert "rather than a picture" in str(err.value)


async def test_cast_by_address_reads_its_id(rt, monkeypatch):
    async def info(host):
        return {"name": "Kitchen display", "ssdp_udn": "0a0b0c0d-0000-1000-8000-000000000003",
                "device_info": {"model_name": "Display", "manufacturer": "Example"}}

    monkeypatch.setattr(manual_flows, "cast_info", info)
    out = await add(rt, "cast", host="192.0.2.60")
    ent = rt.hub.entities[out["entities"][0]]
    assert ent.unique_id == "0a0b0c0d-0000-1000-8000-000000000003"
    assert "192.0.2.60" in rt.hub.config_entries[out["entry_id"]].data["known_hosts"]


async def test_homekit_by_address_needs_the_accessory_to_announce_itself(rt):
    with pytest.raises(FlowError) as err:
        await add(rt, "homekit_controller", host="192.0.2.80")
    assert err.value.status == 404 and "pairing mode" in str(err.value)


async def test_music_assistant_checks_the_server_then_reads_players_when_it_connects(rt, monkeypatch):
    async def get(url, **_):
        return 200, "application/json", b'{"server_id": "0123abcd", "server_version": "2.6.0"}'

    monkeypatch.setattr(manual_flows, "http_get", get)
    from services.homehub import hardware

    monkeypatch.setattr(hardware, "SYNC_WAIT_S", 0.0)
    out = await add(rt, "music_assistant", {"url": "http://192.0.2.5:8095/"})
    entry = rt.hub.config_entries[out["entry_id"]]
    assert entry.unique_id == "0123abcd" and entry.data == {"url": "http://192.0.2.5:8095"}
    assert out["sync"]["status"] == "not_synced"  # the fake runtime never connects it
    with pytest.raises(FlowError) as err:
        await add(rt, "music_assistant", {"url": "http://192.0.2.5:8095"})
    assert err.value.status == 409


async def test_srp_checks_the_account_number_and_the_login(rt, monkeypatch):
    seen = []

    async def login(account, user, password):
        seen.append(account)
        return password == "right"

    monkeypatch.setattr(manual_flows, "srp_login", login)
    with pytest.raises(NeedsInput) as need:
        await add(rt, "srp_energy", {"id": "12", "username": "alex", "password": "right"})
    assert "9-digit" in need.value.error and seen == []
    with pytest.raises(NeedsInput):
        await add(rt, "srp_energy", {"id": "123456789", "username": "alex", "password": "wrong"})
    out = await add(rt, "srp_energy", {"id": "123456789", "username": "alex", "password": "right", "is_tou": "1"})
    assert rt.hub.config_entries[out["entry_id"]].data["is_tou"] is True
    assert rt.hub.entities[out["entities"][0]].unique_id == f"{out['entry_id']}_total_usage"


async def test_emporia_signs_in_and_registers_every_circuit(rt, monkeypatch):
    async def login(email, password):
        return [SimpleNamespace(device_gid=100001, device_name="Panel", model="VUE002", firmware="f",
                                channels=[SimpleNamespace(channel_num="1,2,3", name=None),
                                          SimpleNamespace(channel_num="4", name="Oven")])]

    monkeypatch.setattr(manual_flows, "emporia_login", login)
    out = await add(rt, "emporia_vue", {"email": "alex@example.com", "password": "pw"}, area_id="den")
    assert len(out["entities"]) == 6
    names = {rt.hub.entities[e].original_name for e in out["entities"]}
    assert "Oven Power" in names and "Panel Energy today" in names
    assert rt.started == [out["entry_id"]]


async def test_roborock_emails_a_code_then_signs_in(rt, monkeypatch):
    sent = []

    async def request(email):
        sent.append(email)

    async def login(email, code):
        if code != "123456":
            raise type("RoborockInvalidCode", (Exception,), {})("bad code")
        return {"token": "t"}, "https://example.com"

    monkeypatch.setattr(manual_flows, "roborock_request_code", request)
    monkeypatch.setattr(manual_flows, "roborock_code_login", login)
    from services.homehub import hardware

    monkeypatch.setattr(hardware, "SYNC_WAIT_S", 0.0)
    with pytest.raises(NeedsInput) as need:
        await add(rt, "roborock", {"email": "sam@example.com"})
    assert sent == ["sam@example.com"] and [n["key"] for n in need.value.needs] == ["email", "code"]
    with pytest.raises(NeedsInput) as need:
        await add(rt, "roborock", {"email": "sam@example.com", "code": "000000"})
    assert need.value.error == "That code was not accepted."
    out = await add(rt, "roborock", {"email": "sam@example.com", "code": "123456"})
    assert rt.hub.config_entries[out["entry_id"]].data["user_data"] == {"token": "t"}


async def test_go2rtc_is_one_per_hub(rt, monkeypatch):
    async def get(url, **_):
        return 200, "application/json", b"{}"

    monkeypatch.setattr(manual_flows, "http_get", get)
    await add(rt, "go2rtc", {"url": "http://127.0.0.1:1984"})
    with pytest.raises(FlowError) as err:
        await add(rt, "go2rtc", {"url": "http://127.0.0.1:1984"})
    assert err.value.status == 409


async def test_a_discovered_roborock_or_music_server_goes_through_its_flow(rt, monkeypatch):
    finding = {"id": "roborock:020000000031", "integration": "roborock", "host": "192.0.2.31", "source": "dhcp",
               "props": {}}
    assert flows.needs_for(finding) == [manual_flows.EMAIL]
    with pytest.raises(NeedsInput):
        await flows.adopt(rt, finding, {})


def test_the_api_lists_and_adds(rt):
    app = build_app(rt, Auth(rt.hub, SECRET))
    client = TestClient(app)
    flows_ = client.get("/hub/flows", headers=H).json()["flows"]
    assert {"sun", "roku", "mjpeg", "roborock"} <= {f["domain"] for f in flows_}
    r = client.post("/hub/flows/sun", headers=H, json={})
    assert r.status_code == 200 and r.json()["status"] == "adopted"
    r = client.post("/hub/flows/sun", headers=H, json={})
    assert r.status_code == 409
    r = client.post("/hub/flows/mobile_app", headers=H, json={})
    assert r.status_code == 501
    rt.hub.config_entries["x"] = ConfigEntry("x", "go2rtc", "go2rtc")
    assert client.post("/hub/flows/go2rtc", headers=H, json={"input": {"url": "http://192.0.2.1:1984"}}
                       ).status_code == 409
