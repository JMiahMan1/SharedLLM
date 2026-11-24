"""Tasmota templates: the catalog, suggestions, and loading one onto a made-up device over real HTTP.

Every name, address and MAC is made up (tasmota_fakes.py); the device below adds Tasmota's ``Template``,
``Module`` and ``Backlog`` commands and a restart to the shared fake.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx
import jwt
import pytest
from aiohttp import web
from cryptography.fernet import Fernet
from fastapi import FastAPI

from services.homehub import tasmota_templates as tt
from services.homehub import tasmota_templates_api
from services.homehub.auth import Auth
from services.homehub.core import ConfigEntry, DeviceEntry, EntityEntry, Hub
from services.homehub.runtime import Runtime
from services.homehub.store import Store
from services.homehub.tests.tasmota_fakes import FakeTasmota

SECRET = "made-up-internal-secret"
MAC = "02:00:00:00:0A:01"
MAC_UP = "020000000A01"
GENERIC = {"NAME": "Generic", "GPIO": [1, 1, 1, 1, 1, 1, 0, 0, 1, 1, 1, 1, 1, 0], "FLAG": 0, "BASE": 18}
KAUF = '{"NAME":"Kauf Bulb","GPIO":[0,0,0,0,416,419,0,0,417,420,418,0,0,0],"FLAG":0,"BASE":18}'


# -- a device that has a template and restarts ------------------------------------------------------

class TemplateTasmota(FakeTasmota):
    """A fresh flash: Module 18 (Generic), nothing driven, until a template and ``Module 0`` restart it."""

    def __init__(self, *, name: str = "Kauf Bulb", hostname: str | None = None, down_s: float = 0.2,
                 template: dict[str, Any] | None = None, module: int = 18, restarts: bool = True, **kw: Any) -> None:
        super().__init__("plug", mac=MAC.lower(), name=name, **kw)
        self.hostname = hostname or name
        self.template = dict(template or GENERIC)
        self.module = module
        self.down_s, self.down_until, self.restarts = down_s, 0.0, restarts
        self.sts = {"UptimeSec": 3600, "MqttCount": 0, "Wifi": {"SSId": "lan", "LinkCount": 1, "RSSI": 70}}
        if module == 0:
            self._drive()

    def _drive(self) -> None:
        """What the template drives once it is the device's module: a light with its PWM channels."""
        n = tt.pwm_channels(self.template)
        kind = {1: None, 2: "ct", 3: "rgb", 4: "rgbw", 5: "rgbcw"}.get(n)
        keep = {k: self.sts[k] for k in ("Wifi", "MqttCount") if k in self.sts}
        self.sts = FakeTasmota(kind).sts if kind else {"POWER": "OFF"}
        self.white = kind == "ct"
        self.sts.update(keep)

    def status0(self) -> dict[str, Any]:
        out = super().status0()
        out["Status"]["Module"] = self.module
        out["StatusNET"]["Hostname"] = self.hostname
        out.pop("StatusSNS", None)
        out["StatusSNS"] = {"Time": "2026-11-11T10:00:00"}
        return out

    def run(self, command: str) -> dict[str, Any]:
        name, _, arg = command.partition(" ")
        low = name.lower()
        if low == "backlog":
            self.commands.append(command)
            for part in arg.split(";"):
                self.run(part.strip())
            return {}
        if low == "template":
            self.commands.append(command)
            if arg:
                self.pending = json.loads(arg)
                return dict(self.pending)
            return dict(self.template)
        if low == "module":
            self.commands.append(command)
            if arg:
                self.template = getattr(self, "pending", self.template)
                self.module = int(arg)
                if self.restarts:
                    self.down_until = time.monotonic() + self.down_s
                    self.sts["UptimeSec"] = 4
                if self.module == 0:
                    self._drive()
                else:
                    keep = {k: self.sts[k] for k in ("Wifi", "MqttCount", "UptimeSec") if k in self.sts}
                    self.sts = keep
            return {"Module": {str(self.module): self.template["NAME"] if self.module == 0 else "Generic"}}
        if low.startswith("setoption"):
            self.commands.append(command)
            return {name: arg}
        return super().run(command)

    async def _cm(self, request: web.Request) -> web.Response:
        if time.monotonic() < self.down_until:
            return web.json_response({}, status=503)
        return await super()._cm(request)


class FakeRuntime(Runtime):
    def __init__(self, hub, store):
        super().__init__(hub, store)
        self.started: list[str] = []
        self.stopped: list[str] = []

    def start_entry(self, entry):
        self.started.append(entry.entry_id)

    async def stop_entry(self, entry_id):
        self.stopped.append(entry_id)


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(tt, "RESTART_GRACE_S", 0.05)
    monkeypatch.setattr(tt, "RESTART_POLL_S", 0.05)
    monkeypatch.setattr(tt, "RESTART_WAIT_S", 2.0)


@pytest.fixture
async def made():
    devices: list[FakeTasmota] = []

    async def make(**kw: Any) -> tuple[TemplateTasmota, str]:
        dev = TemplateTasmota(**kw)
        devices.append(dev)
        return dev, await dev.start()

    yield make
    for dev in devices:
        await dev.stop()


def hub_with(tmp_path, host: str, *, title: str = "Kauf Bulb", model: str = "ESP8266EX",
             data: dict[str, Any] | None = None) -> tuple[Hub, FakeRuntime, ConfigEntry]:
    store = Store(str(tmp_path / "hub.db"), Fernet.generate_key().decode())
    hub = Hub(time_zone="Etc/GMT+7")
    hub.store = store
    entry = ConfigEntry(entry_id="t1", domain="tasmota", title=title,
                        data={"host": host, "mac": MAC.lower(), **(data or {})}, unique_id=MAC_UP)
    hub.config_entries["t1"] = entry
    store.save_config_entry(entry)
    hub.devices["dev1"] = DeviceEntry(id="dev1", name=title, config_entries=["t1"], manufacturer="Tasmota",
                                      model=model)
    sensor = EntityEntry(entity_id="sensor.kauf_bulb_ssid", platform="tasmota", config_entry_id="t1",
                         unique_id=f"{MAC_UP}_status_sensor_status_sensor_status_ssid", device_id="dev1")
    hub.entities[sensor.entity_id] = sensor
    return hub, FakeRuntime(hub, store), entry


def facts(**kw: Any) -> dict[str, Any]:
    base = {"device_name": None, "friendly_name": [], "hostname": None, "chip": "ESP8266EX", "module": 18,
            "module_name": "Generic", "template": GENERIC, "template_name": "Generic", "light": False,
            "light_channels": 0, "relays": 0, "energy": False, "other_names": []}
    return {**base, **kw}


# -- the catalog -----------------------------------------------------------------------------------

def test_every_catalog_entry_is_a_whole_template_from_its_page():
    with open(tt.CATALOG_PATH, encoding="utf-8") as fh:
        raw = json.load(fh)["templates"]
    assert 15 <= len(raw) <= 30
    ids = [e["id"] for e in raw]
    assert len(ids) == len(set(ids))
    for e in raw:
        assert set(e) >= {"id", "manufacturer", "model", "kind", "chip", "template", "commands", "source_url", "notes"}
        assert e["kind"] in tt.KINDS, e["id"]
        assert re.fullmatch(r"https://templates\.blakadder\.com/[A-Za-z0-9_.-]+\.html", e["source_url"]), e["id"]
        tpl = json.loads(e["template"])
        assert isinstance(tpl["NAME"], str) and tpl["NAME"]
        assert isinstance(tpl["FLAG"], int) and isinstance(tpl["BASE"], int)
        assert all(isinstance(g, int) and not isinstance(g, bool) for g in tpl["GPIO"])
        family = tt.chip_family(e["chip"])
        assert family is not None and len(tpl["GPIO"]) in tt.GPIO_LENGTHS[family], e["id"]
        assert tt.validate_template(e["template"]) == tpl
        assert isinstance(e["commands"], list) and all(isinstance(c, str) for c in e["commands"])
    kinds = {e["kind"] for e in raw}
    assert kinds == set(tt.KINDS)


def test_the_three_kauf_bulbs_carry_the_template_from_their_pages():
    for model in ("BLF10", "BLF15", "BLF19"):
        e = tt.catalog_entry(f"kauf-{model.lower()}")
        assert e is not None and e["template"] == KAUF and e["kind"] == "bulb"
        assert e["source_url"] == f"https://templates.blakadder.com/kauf_{model}.html"


def test_the_catalog_filters_by_kind_and_words():
    bulbs = tt.search_catalog("bulb")
    assert bulbs and all(e["kind"] == "bulb" for e in bulbs)
    assert [e["id"] for e in tt.search_catalog(None, "kauf blf15")] == ["kauf-blf15"]
    assert tt.search_catalog("plug", "kauf")[0]["id"] == "kauf-plf10"
    assert tt.search_catalog(None, "nothing like this") == []
    assert tt.search_catalog("bulb", "kauf")[0]["channels"] == 5


# -- validating a pasted template ------------------------------------------------------------------

@pytest.mark.parametrize(("text", "says"), [
    ("", "empty"),
    ("{not json", "not valid JSON"),
    ("[1,2]", "JSON object"),
    ('{"GPIO":[0,0,0,0,0,0,0,0,0,0,0,0,0,0],"FLAG":0,"BASE":18}', "no NAME"),
    ('{"NAME":"X","FLAG":0,"BASE":18}', "no GPIO"),
    ('{"NAME":"X","GPIO":[0,0,0,0,0,0,0,0,0,0,0,0,0,0],"FLAG":0}', "no BASE"),
    ('{"NAME":"X","GPIO":[0,"a",0,0,0,0,0,0,0,0,0,0,0,0],"FLAG":0,"BASE":18}', "whole number"),
    ('{"NAME":"X","GPIO":[17,255,0,255,0,0,255,255,21,56,0,0,255],"FLAG":0,"BASE":1}', "old"),
    ('{"NAME":"X","GPIO":[0,0,0],"FLAG":0,"BASE":18}', "has 3 GPIO"),
    ('{"NAME":"A;B","GPIO":[0,0,0,0,0,0,0,0,0,0,0,0,0,0],"FLAG":0,"BASE":18}', "';'"),
])
def test_a_malformed_template_is_refused_with_what_is_wrong(text, says):
    with pytest.raises(tt.TemplateError) as err:
        tt.validate_template(text)
    assert says in str(err.value) and err.value.status == 400


def test_templates_are_compared_by_their_pins():
    kauf = json.loads(KAUF)
    assert tt.same_template(kauf, {**kauf, "NAME": "Renamed"})
    assert not tt.same_template(kauf, {**kauf, "BASE": 1})
    assert tt.template_text(kauf) == KAUF
    assert tt.chip_family("ESP8266EX") == "esp8266" and tt.chip_family("ESP32-C3") == "esp32c3"
    assert tt.chip_family("ESP32-D0WD") == "esp32" and tt.chip_family(None) is None
    assert tt.parse_module({"Module": {"0": "Kauf Bulb"}}) == (0, "Kauf Bulb")
    assert tt.parse_module({"Module": "18 (Generic)"}) == (18, "Generic")


# -- suggestions -----------------------------------------------------------------------------------

def test_a_device_named_like_a_kauf_bulb_gets_the_kauf_template_first():
    out = tt.suggest(facts(device_name="Kauf Bulb", hostname="kauf-bulb-0a01"))
    assert len(out) == 3 and all(s["id"].startswith("kauf-blf") for s in out)
    assert any("KAUF" in r for r in out[0]["reasons"])
    assert any("no light channels" in r for r in out[0]["reasons"])
    # The model in its name picks the one model.
    exact = tt.suggest(facts(device_name="Porch", hostname="kauf-blf15-0a01"))
    assert exact[0]["id"] == "kauf-blf15" and any("BLF15" in r for r in exact[0]["reasons"])


def test_a_plug_like_device_gets_plug_templates():
    out = tt.suggest(facts(device_name="Desk Plug", relays=1, energy=True))
    assert out and all(s["kind"] == "plug" for s in out)
    assert any("power use" in r for r in out[0]["reasons"])


def test_a_template_for_another_chip_is_never_suggested():
    out = tt.suggest(facts(device_name="Shelly relay", chip="ESP32-D0WD", relays=1))
    assert out and all(tt.chip_family(s["chip"]) == "esp32" for s in out)
    score, why = tt.score_entry(tt.catalog_entry("kauf-blf15"), facts(device_name="Kauf Bulb", chip="ESP32"))
    assert score == -1 and "ESP32" in why[0]


def test_a_device_with_nothing_to_go_on_gets_no_suggestions():
    assert tt.suggest(facts(device_name="tasmota_0A01", hostname="tasmota-0A01")) == []


# -- reading and loading, against the made-up device -----------------------------------------------

async def test_reading_shows_the_template_and_suggests_one(made, tmp_path):
    _dev, host = await made(hostname="kauf-bulb-0a01")
    _hub, runtime, _entry = hub_with(tmp_path, host)
    out = await tt.read_device(runtime, "t1")
    cur = out["current"]
    assert cur["template_name"] == "Generic" and cur["module"] == 18 and not cur["active"]
    assert cur["chip"] == "ESP8266EX" and cur["hostname"] == "kauf-bulb-0a01" and cur["catalog_matches"] == []
    assert not out["up_to_date"] and out["suggestions"][0]["id"].startswith("kauf-blf")
    assert out["previous_template"] is None


async def test_a_template_already_in_the_catalog_is_recognised(made, tmp_path):
    _dev, host = await made(template=json.loads(KAUF), module=0)
    _hub, runtime, _entry = hub_with(tmp_path, host)
    out = await tt.read_device(runtime, "t1")
    assert out["up_to_date"] and "nothing to do" in out["message"]
    ids = [m["id"] for m in out["current"]["catalog_matches"]]
    # The KAUF entries carry its NAME too; other makers' bulbs with the same pin map follow.
    assert set(ids[:3]) == {"kauf-blf10", "kauf-blf15", "kauf-blf19"} and "athom-lb10-5w-tas" in ids[3:]
    assert "KAUF" in out["message"]
    assert out["current"]["light_channels"] == 5


async def test_applying_sends_the_backlog_waits_verifies_and_keeps_the_previous(made, tmp_path):
    dev, host = await made()
    hub, runtime, entry = hub_with(tmp_path, host)
    out = await tt.apply_template(runtime, "t1", {"catalog_id": "kauf-blf15", "confirm": True})
    sent = f"Backlog Template {KAUF}; Module 0"
    assert out["sent"] == sent and sent in dev.commands
    assert out["verified"] and out["restarted"] and out["status"] == "applied"
    assert dev.commands.index(sent) < len(dev.commands) - 1  # it was read back after
    assert dev.commands[-3:] == ["Template", "Module", "Status 0"]
    previous = tt.template_text(GENERIC)
    assert out["previous_template"] == previous
    assert entry.data["previous_template"] == previous and entry.data["previous_module"] == 18
    assert next(e for e in runtime.store.load_config_entries() if e.entry_id == "t1").data["previous_template"] == previous
    # The light it now drives is an entity, and the entry was reloaded.
    assert out["entities_added"] == [f"{MAC_UP}_light_light_0"]
    light = next(e for e in hub.entities.values() if e.unique_id == f"{MAC_UP}_light_light_0")
    assert light.domain == "light" and light.capabilities["supported_color_modes"] == ["color_temp", "hs"]
    assert runtime.stopped == ["t1"] and runtime.started == ["t1"]
    assert out["current"]["active"] and out["current"]["template_name"] == "Kauf Bulb"


async def test_a_pasted_template_is_loaded_as_it_was_pasted(made, tmp_path):
    _dev, host = await made()
    _hub, runtime, _entry = hub_with(tmp_path, host)
    pasted = '  {"NAME":"Porch Bulb","GPIO":[0,0,0,0,416,419,0,0,417,420,418,0,0,0],"FLAG":0,"BASE":18}\n'
    out = await tt.apply_template(runtime, "t1", {"template": pasted, "confirm": True})
    assert out["verified"] and out["sent"] == f"Backlog Template {pasted.strip()}; Module 0"


async def test_nothing_is_sent_without_confirm_or_with_a_malformed_paste(made, tmp_path):
    dev, host = await made()
    _hub, runtime, entry = hub_with(tmp_path, host)
    for body in ({"catalog_id": "kauf-blf15"}, {"catalog_id": "kauf-blf15", "confirm": "yes"},
                 {"template": KAUF, "confirm": False}):
        with pytest.raises(tt.TemplateError, match="confirm"):
            await tt.apply_template(runtime, "t1", body)
    with pytest.raises(tt.TemplateError, match="not valid JSON"):
        await tt.apply_template(runtime, "t1", {"template": "{oops", "confirm": True})
    with pytest.raises(tt.TemplateError, match="No such catalog"):
        await tt.apply_template(runtime, "t1", {"catalog_id": "nope", "confirm": True})
    with pytest.raises(tt.TemplateError) as err:
        await tt.apply_template(runtime, "t1", {"catalog_id": "shelly-plus-1", "confirm": True})
    assert err.value.status == 409
    assert dev.commands == [] and "previous_template" not in entry.data


async def test_a_device_that_already_has_it_is_left_alone(made, tmp_path):
    dev, host = await made(template=json.loads(KAUF), module=0)
    _hub, runtime, _entry = hub_with(tmp_path, host)
    out = await tt.apply_template(runtime, "t1", {"catalog_id": "kauf-blf19", "confirm": True})
    assert out["status"] == "unchanged" and not any(c.startswith("Backlog") for c in dev.commands)


async def test_a_device_that_reports_another_template_is_not_verified(made, tmp_path, monkeypatch):
    dev, host = await made()
    _hub, runtime, _entry = hub_with(tmp_path, host)
    real = dev.run

    def stubborn(command: str) -> dict[str, Any]:
        if command.startswith("Template {"):
            dev.commands.append(command)
            return {}  # ignores it
        return real(command)

    monkeypatch.setattr(dev, "run", stubborn)
    out = await tt.apply_template(runtime, "t1", {"catalog_id": "kauf-blf15", "confirm": True})
    assert out["status"] == "applied" and not out["verified"] and "not verified" in out["message"]


async def test_a_device_that_does_not_come_back_says_so(made, tmp_path, monkeypatch):
    monkeypatch.setattr(tt, "RESTART_WAIT_S", 0.2)
    _dev, host = await made(down_s=60)
    _hub, runtime, entry = hub_with(tmp_path, host)
    out = await tt.apply_template(runtime, "t1", {"catalog_id": "kauf-blf15", "confirm": True})
    assert not out["verified"] and "did not answer" in out["message"]
    assert entry.data["previous_template"] == tt.template_text(GENERIC)  # kept before anything was sent


async def test_extra_commands_go_first_one_by_one(made, tmp_path, monkeypatch):
    dev, host = await made()
    _hub, runtime, _entry = hub_with(tmp_path, host)
    entry = {**tt.catalog_entry("kauf-blf15"), "commands": ["SetOption37 54"]}
    monkeypatch.setattr(tt, "catalog_entry", lambda _id: entry)
    out = await tt.apply_template(runtime, "t1", {"catalog_id": "kauf-blf15", "confirm": True})
    assert out["verified"]
    assert dev.commands.index("SetOption37 54") < dev.commands.index(out["sent"])


async def test_restore_sends_the_previous_template_back(made, tmp_path):
    dev, host = await made()
    _hub, runtime, entry = hub_with(tmp_path, host)
    with pytest.raises(tt.TemplateError, match="no previous"):
        await tt.restore_previous(runtime, "t1", {"confirm": True})
    await tt.apply_template(runtime, "t1", {"catalog_id": "kauf-blf15", "confirm": True})
    with pytest.raises(tt.TemplateError, match="confirm"):
        await tt.restore_previous(runtime, "t1", {})
    out = await tt.restore_previous(runtime, "t1", {"confirm": True})
    assert out["sent"] == f"Backlog Template {tt.template_text(GENERIC)}; Module 18"
    assert out["verified"] and dev.module == 18 and dev.template == GENERIC
    # Restoring keeps what it replaced, so it can be undone too.
    assert entry.data["previous_template"] == KAUF and entry.data["previous_module"] == 0


async def test_the_web_password_goes_with_every_command(made, tmp_path):
    dev, host = await made(password="made-up-pw")
    _hub, runtime, _entry = hub_with(tmp_path, host, data={"username": "admin", "password": "made-up-pw"})
    out = await tt.read_device(runtime, "t1")
    assert out["current"]["template_name"] == "Generic" and set(dev.auth_seen) == {"made-up-pw"}
    (tmp_path / "other").mkdir()
    _hub, runtime2, _entry = hub_with(tmp_path / "other", host)  # the same device, no password entered
    with pytest.raises(tt.TemplateError) as err:
        await tt.read_device(runtime2, "t1")
    assert err.value.status == 409 and "password" in str(err.value)


async def test_entries_that_are_not_tasmota_are_refused(tmp_path):
    hub, runtime, _entry = hub_with(tmp_path, "192.0.2.9")
    hub.config_entries["w1"] = ConfigEntry(entry_id="w1", domain="wiz", title="Lamp")
    with pytest.raises(tt.TemplateError) as err:
        await tt.read_device(runtime, "w1")
    assert err.value.status == 400
    with pytest.raises(tt.TemplateError) as err:
        await tt.read_device(runtime, "missing")
    assert err.value.status == 404


# -- the API ---------------------------------------------------------------------------------------

def kid_token(store: Store) -> str:
    store.set_kv("users", [{"id": "u2", "name": "Kid", "is_active": True, "group_ids": []}])
    store.save_refresh_token("rt2", {"user_id": "u2"}, "j" * 64)
    now = int(time.time())
    return jwt.encode({"iss": "rt2", "iat": now, "exp": now + 60}, "j" * 64, algorithm="HS256")


async def test_the_api_is_for_admins_and_needs_confirm(made, tmp_path):
    dev, host = await made(hostname="kauf-bulb-0a01")
    _hub, runtime, entry = hub_with(tmp_path, host)
    app = FastAPI()
    app.include_router(tasmota_templates_api.build_router(runtime, Auth(runtime.store, SECRET)))
    admin = {"Authorization": f"Bearer {SECRET}"}
    kid = {"Authorization": f"Bearer {kid_token(runtime.store)}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://hub") as c:
        for verb, path in (("get", "/hub/tasmota/templates"), ("get", "/hub/tasmota/t1/template"),
                           ("post", "/hub/tasmota/t1/template"), ("post", "/hub/tasmota/t1/template/restore")):
            kw = {"json": {"catalog_id": "kauf-blf15", "confirm": True}} if verb == "post" else {}
            assert (await getattr(c, verb)(path, headers=kid, **kw)).status_code == 401
            assert (await getattr(c, verb)(path, **kw)).status_code == 401
        assert dev.commands == []
        cat = (await c.get("/hub/tasmota/templates?kind=bulb&q=kauf", headers=admin)).json()
        assert [e["id"] for e in cat["templates"]] == ["kauf-blf10", "kauf-blf15", "kauf-blf19"]
        assert (await c.get("/hub/tasmota/templates?kind=toaster", headers=admin)).status_code == 400
        got = (await c.get("/hub/tasmota/t1/template", headers=admin)).json()
        assert got["suggestions"][0]["id"].startswith("kauf-blf")
        refused = await c.post("/hub/tasmota/t1/template", headers=admin, json={"catalog_id": "kauf-blf15"})
        assert refused.status_code == 400 and "confirm" in refused.json()["message"]
        bad = await c.post("/hub/tasmota/t1/template", headers=admin, json={"template": "{nope", "confirm": True})
        assert bad.status_code == 400 and "not valid JSON" in bad.json()["message"]
        assert not any(cmd.startswith("Backlog") for cmd in dev.commands)
        done = (await c.post("/hub/tasmota/t1/template", headers=admin,
                             json={"catalog_id": "kauf-blf15", "confirm": True})).json()
        assert done["verified"] and done["previous_template"] == tt.template_text(GENERIC)
        back = (await c.post("/hub/tasmota/t1/template/restore", headers=admin, json={"confirm": True})).json()
        assert back["verified"] and entry.data["previous_template"] == KAUF
        assert (await c.get("/hub/tasmota/zz/template", headers=admin)).status_code == 404
