"""Light groups: the group driver (fan-out, state from the members) and making, changing and listing them.

Every name and id here is made up.
"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from services.homehub import light_cluster_move, light_groups
from services.homehub.api import build_app
from services.homehub.auth import Auth
from services.homehub.core import Area, ConfigEntry, DeviceEntry, Driver, EntityEntry, Hub, HubError
from services.homehub.drivers import driver_class
from services.homehub.drivers.group import LightGroupDriver, aggregate
from services.homehub.store import Store
from services.homehub.tests.test_flows import FakeRuntime

SECRET = "internal-secret"
H = {"Authorization": f"Bearer {SECRET}"}
BULBS = [f"light.pantry_bulb_{n}" for n in (1, 2, 3)]


class FakeBulbDriver(Driver):
    """Stands in for the WiZ driver that owns the bulbs; ``dead`` bulbs refuse every command."""

    platform = "wiz"

    def __init__(self, hub, entry) -> None:
        super().__init__(hub, entry)
        self.calls: list[tuple[str, str, dict]] = []
        self.dead: set[str] = set()

    async def async_call(self, entity, domain, service, data):
        self.calls.append((entity.entity_id, service, dict(data)))
        if entity.entity_id in self.dead:
            raise HubError(f"{entity.entity_id} did not answer")
        attrs = {"supported_color_modes": ["color_temp", "hs"], "color_mode": "color_temp",
                 "brightness": round(data.get("brightness_pct", 100) * 255 / 100), "color_temp_kelvin": 3000}
        self.publish(entity, "on" if service == "turn_on" else "off", attrs if service == "turn_on" else {})


def _runtime(tmp_path):
    store = Store(str(tmp_path / "hub.db"), Fernet.generate_key().decode())
    hub = Hub(time_zone="Etc/GMT+7")
    hub.store = store
    for a in (Area("pantry", "Pantry"), Area("den", "Den")):
        hub.areas[a.area_id] = a
        store.save_area(a)
    wiz = ConfigEntry("wiz1", "wiz", "Bulbs")
    hub.config_entries[wiz.entry_id] = wiz
    store.save_config_entry(wiz)
    for i, eid in enumerate(BULBS, 1):
        dev = DeviceEntry(f"d{i}", name=f"Pantry Bulb {i}", area_id="pantry", config_entries=["wiz1"])
        hub.devices[dev.id] = dev
        ent = EntityEntry(eid, "wiz", f"uid{i}", "wiz1", dev.id)
        hub.entities[eid] = ent
        hub.states.set(eid, "on", {"friendly_name": f"Pantry Bulb {i}", "brightness": 255})
    bulbs = FakeBulbDriver(hub, wiz)
    hub.drivers["wiz1"] = bulbs
    return FakeRuntime(hub, store), bulbs


@pytest.fixture
def env(tmp_path):
    return _runtime(tmp_path)


async def _start(rt, entry_id):
    """What the runtime does for a new entry: the group's driver comes up."""
    entry = rt.hub.config_entries[entry_id]
    drv = LightGroupDriver(rt.hub, entry)
    rt.hub.drivers[entry_id] = drv
    await drv.async_setup()
    return drv


def test_the_registry_knows_the_driver():
    assert driver_class("group") is LightGroupDriver


async def test_making_a_group_gives_one_light_in_the_room(env):
    rt, _ = env
    out = await light_groups.create_group(rt, {"name": "Pantry lights", "entities": BULBS, "area_id": "Pantry"})
    assert out["entity_id"] == "light.pantry_lights"
    assert out["area_id"] == "pantry" and out["area"] == "Pantry"
    assert [m["name"] for m in out["members"]] == ["Pantry Bulb 1", "Pantry Bulb 2", "Pantry Bulb 3"]
    entry = rt.hub.config_entries[out["entry_id"]]
    assert entry.domain == "group" and entry.options["group_type"] == "light"
    assert entry.options["entities"] == BULBS
    ent = rt.hub.entities["light.pantry_lights"]
    assert ent.unique_id == entry.entry_id and ent.device_id is None and ent.area_id == "pantry"
    assert rt.started == [entry.entry_id]


async def test_commands_fan_out_and_the_state_follows_the_members(env):
    rt, bulbs = env
    out = await light_groups.create_group(rt, {"name": "Pantry lights", "entities": BULBS})
    await _start(rt, out["entry_id"])
    group = "light.pantry_lights"
    st = rt.hub.states.get(group)
    assert st.state == "on" and st.attributes["entity_id"] == BULBS and st.attributes["brightness"] == 255
    await rt.hub.call_service("light", "turn_off", {"entity_id": group})
    assert sorted(c[0] for c in bulbs.calls) == BULBS and {c[1] for c in bulbs.calls} == {"turn_off"}
    assert rt.hub.states.get(group).state == "off"
    bulbs.calls.clear()
    await rt.hub.call_service("light", "turn_on", {"entity_id": group, "brightness_pct": 40})
    assert all(c[2].get("brightness_pct") == 40 for c in bulbs.calls)
    st = rt.hub.states.get(group)
    assert st.state == "on" and st.attributes["brightness"] == 102 and st.attributes["color_temp_kelvin"] == 3000
    await rt.hub.call_service("light", "toggle", {"entity_id": group})
    assert rt.hub.states.get(group).state == "off"
    # One bulb on is enough for the group to be on.
    rt.hub.publish(BULBS[1], "on", {"brightness": 100})
    assert rt.hub.states.get(group).state == "on"


async def test_one_bulb_that_does_not_answer_does_not_stop_the_others(env):
    rt, bulbs = env
    out = await light_groups.create_group(rt, {"name": "Pantry lights", "entities": BULBS})
    await _start(rt, out["entry_id"])
    bulbs.dead.add(BULBS[2])
    await rt.hub.call_service("light", "turn_off", {"entity_id": "light.pantry_lights"})
    assert rt.hub.states.get(BULBS[0]).state == "off" and rt.hub.states.get(BULBS[2]).state == "on"
    assert rt.hub.states.get("light.pantry_lights").state == "on"  # never claims more than the bulbs say


async def test_unavailable_only_when_no_member_answers_and_unreachable_ones_are_skipped(env):
    rt, bulbs = env
    out = await light_groups.create_group(rt, {"name": "Pantry lights", "entities": BULBS})
    await _start(rt, out["entry_id"])
    rt.hub.publish(BULBS[0], "unavailable", {})
    rt.hub.publish(BULBS[1], "off", {})
    rt.hub.publish(BULBS[2], "off", {})
    assert rt.hub.states.get("light.pantry_lights").state == "off"
    await rt.hub.call_service("light", "turn_on", {"entity_id": "light.pantry_lights"})
    assert BULBS[0] not in [c[0] for c in bulbs.calls]
    for eid in BULBS[1:]:
        rt.hub.publish(eid, "unavailable", {})
    assert rt.hub.states.get("light.pantry_lights").state == "unavailable"


async def test_a_deleted_member_is_skipped_and_listed_as_missing(env):
    rt, bulbs = env
    out = await light_groups.create_group(rt, {"name": "Pantry lights", "entities": BULBS})
    drv = await _start(rt, out["entry_id"])
    rt.hub.entities.pop(BULBS[2])
    rt.hub.states.remove(BULBS[2])
    drv.refresh()
    await rt.hub.call_service("light", "turn_off", {"entity_id": "light.pantry_lights"})
    assert BULBS[2] not in [c[0] for c in bulbs.calls]
    assert rt.hub.states.get("light.pantry_lights").state == "off"
    view = light_groups.list_groups(rt)["groups"][0]
    assert view["missing"] == 1 and view["members"][2]["missing"] is True
    # Editing keeps a missing member only if it is still chosen; new ones must exist.
    with pytest.raises(light_groups.GroupError):
        await light_groups.update_group(rt, out["entry_id"], {"entities": [*BULBS[:2], "light.nowhere"]})
    kept = await light_groups.update_group(rt, out["entry_id"], {"entities": BULBS})
    assert [m["entity_id"] for m in kept["members"]] == BULBS


async def test_a_hidden_duplicate_member_is_still_switched(env):
    rt, bulbs = env
    out = await light_groups.create_group(rt, {"name": "Pantry lights", "entities": BULBS})
    await _start(rt, out["entry_id"])
    rt.hub.entities[BULBS[0]].hidden_by = "device"
    await rt.hub.call_service("light", "turn_off", {"entity_id": "light.pantry_lights"})
    assert BULBS[0] in [c[0] for c in bulbs.calls]
    view = light_groups.list_groups(rt)
    assert view["groups"][0]["members"][0]["hidden"] is True
    assert next(c for c in view["lights"] if c["entity_id"] == BULBS[0])["hidden"] is True


async def test_what_a_group_may_hold(env):
    rt, _ = env
    for body, says in [
        ({"entities": BULBS}, "name"),
        ({"name": "x", "entities": BULBS[:1]}, "two"),
        ({"name": "x", "entities": ["switch.pantry_fan", *BULBS]}, "not a light"),
        ({"name": "x", "entities": ["light.nowhere", *BULBS]}, "no light"),
        ({"name": "x", "entities": BULBS, "area_id": "Attic"}, "no room"),
    ]:
        with pytest.raises(light_groups.GroupError, match=says):
            await light_groups.create_group(rt, body)
    out = await light_groups.create_group(rt, {"name": "Pantry lights", "entities": BULBS})
    with pytest.raises(light_groups.GroupError, match="another group"):
        await light_groups.create_group(rt, {"name": "All", "entities": [out["entity_id"], BULBS[0]]})
    with pytest.raises(light_groups.GroupError) as err:
        await light_groups.create_group(rt, {"name": "pantry LIGHTS", "entities": BULBS})
    assert err.value.status == 409
    # The group is not offered as a light that can join a group.
    assert out["entity_id"] not in [c["entity_id"] for c in light_groups.list_groups(rt)["lights"]]


async def test_renaming_and_moving_keeps_the_entity_id(env):
    rt, _ = env
    out = await light_groups.create_group(rt, {"name": "Pantry lights", "entities": BULBS})
    changed = await light_groups.update_group(rt, out["entry_id"], {"name": "Snack corner", "area_id": "den",
                                                                    "entities": BULBS[:2]})
    assert changed["entity_id"] == "light.pantry_lights"
    assert changed["name"] == "Snack corner" and changed["area"] == "Den" and len(changed["members"]) == 2
    assert rt.hub.config_entries[out["entry_id"]].options["entities"] == BULBS[:2]
    assert rt.stopped[-1] == out["entry_id"] and rt.started[-1] == out["entry_id"]
    cleared = await light_groups.update_group(rt, out["entry_id"], {"area_id": None})
    assert cleared["area_id"] is None
    with pytest.raises(light_groups.GroupError) as err:
        await light_groups.update_group(rt, "nope", {"name": "x"})
    assert err.value.status == 404


async def test_deleting_a_group_leaves_the_lights(env):
    rt, _ = env
    out = await light_groups.create_group(rt, {"name": "Pantry lights", "entities": BULBS})
    gone = await light_groups.delete_group(rt, out["entry_id"])
    assert gone["entities"] == ["light.pantry_lights"]
    assert "light.pantry_lights" not in rt.hub.entities and out["entry_id"] not in rt.hub.config_entries
    assert all(b in rt.hub.entities for b in BULBS)


def test_aggregate_rules():
    from services.homehub.core import State

    on = State("light.a", "on", {"brightness": 200, "supported_color_modes": ["onoff"], "color_mode": "onoff"})
    off = State("light.b", "off", {"supported_color_modes": ["brightness"]})
    state, attrs = aggregate([on, off])
    assert state == "on" and attrs["brightness"] == 200 and attrs["supported_color_modes"] == ["brightness"]
    assert aggregate([on, off], all_on=True)[0] == "off"
    assert aggregate([State("light.a", "unavailable", {})])[0] == "unavailable"
    assert aggregate([])[0] == "unavailable"


def test_the_api_lists_makes_changes_and_removes(env):
    rt, _ = env
    rt.automations = None
    client = TestClient(build_app(rt, Auth(rt.store, SECRET)))
    assert client.get("/hub/light_groups").status_code == 401
    made = client.post("/hub/light_groups", headers=H, json={"name": "Pantry lights", "entities": BULBS})
    assert made.status_code == 200, made.text
    entry_id = made.json()["entry_id"]
    listed = client.get("/hub/light_groups", headers=H).json()
    assert [g["name"] for g in listed["groups"]] == ["Pantry lights"]
    assert {a["name"] for a in listed["areas"]} == {"Pantry", "Den"}
    bad = client.post("/hub/light_groups", headers=H, json={"name": "x", "entities": []})
    assert bad.status_code == 400 and "two" in bad.json()["message"]
    renamed = client.patch(f"/hub/light_groups/{entry_id}", headers=H, json={"name": "Pantry"})
    assert renamed.json()["name"] == "Pantry"
    assert client.delete(f"/hub/light_groups/{entry_id}", headers=H).json()["status"] == "removed"
    assert client.delete(f"/hub/light_groups/{entry_id}", headers=H).status_code == 404


async def test_light_clusters_move_once(env, monkeypatch):
    rt, _ = env
    clusters = [
        {"cluster_id": "pantry", "cluster_name": "Pantry glow", "member_entity_ids": [*BULBS, "light.gone"],
         "room": "Pantry"},
        {"cluster_id": "lonely", "cluster_name": "Lonely", "member_entity_ids": [BULBS[0]]},
    ]
    deleted: list[str] = []
    monkeypatch.setattr(light_cluster_move, "read_clusters", lambda: list(clusters))
    monkeypatch.setattr(light_cluster_move, "delete_cluster", deleted.append)
    out = await light_cluster_move.migrate(rt)
    assert out["status"] == "moved" and deleted == ["pantry", "lonely"]
    groups = light_groups.list_groups(rt)["groups"]
    assert [(g["name"], g["area"], [m["entity_id"] for m in g["members"]]) for g in groups] == [
        ("Pantry glow", "Pantry", BULBS)]
    lonely = next(c for c in out["clusters"] if c["cluster"] == "lonely")
    assert lonely["members"] == [BULBS[0]] and "fewer than two" in lonely["group"]
    # Once: a second start changes nothing.
    monkeypatch.setattr(light_cluster_move, "read_clusters", lambda: pytest.fail("asked identity again"))
    assert (await light_cluster_move.migrate(rt))["status"] == "done before"


async def test_light_clusters_wait_for_identity(env, monkeypatch):
    rt, _ = env

    def down():
        raise OSError("connection refused")
    monkeypatch.setattr(light_cluster_move, "read_clusters", down)
    with pytest.raises(OSError):
        await light_cluster_move.migrate(rt)
    assert rt.store.get_kv(light_cluster_move.MARKER, None) is None
