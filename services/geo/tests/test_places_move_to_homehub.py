"""The one-time move of geo's saved places and place settings into HomeHub (services/geo/hub_places.py).

Every name, id and coordinate here is made up.
"""
import json

import pytest

from services.geo import hub_places

PLACE = {"id": "a" * 32, "name": "Made-up Library", "lat": 10.5, "lon": 20.5, "radius": 200.0, "scope": "family",
         "owner": "kim", "important": True, "notify": ["sam"], "icon": "school", "created_at": 1700000000.0}
PRIVATE = {"id": "b" * 32, "name": "Made-up Den", "lat": 10.6, "lon": 20.6, "radius": 80.0, "scope": "user",
           "owner": "sam", "important": False, "notify": [], "icon": "", "created_at": 1700000100.0}


class R:
    def __init__(self):
        self.kv, self.h = {}, {}

    async def get(self, k):
        return self.kv.get(k)

    async def set(self, k, v, ex=None, nx=False):
        self.kv[k] = v
        return True

    async def hgetall(self, k):
        return dict(self.h.get(k, {}))

    async def hset(self, k, f, v):
        self.h.setdefault(k, {})[f] = v

    async def scan_iter(self, match):
        prefix = match.rstrip("*")
        for k in list(self.kv) + list(self.h):
            if k.startswith(prefix):
                yield k


def _stored(r):
    r.h[hub_places.LEGACY_PLACES_KEY] = {PLACE["id"]: json.dumps(PLACE), PRIVATE["id"]: json.dumps(PRIVATE)}
    r.h[hub_places.LEGACY_SETTINGS_KEY] = {"zone.home": json.dumps({"important": True, "notify": ["kim"], "icon": ""})}
    r.kv["geo:zone_state:kim"] = json.dumps({"zone": "place." + "a" * 32, "t": 1.0})
    r.kv["geo:zone_state:sam"] = json.dumps({"zone": "place." + "b" * 32, "t": 1.0})
    r.h["geo:place_reminders:kim"] = {"r1": json.dumps({"id": "r1", "zone": "place." + "a" * 32, "on": "leave"})}


@pytest.mark.asyncio
async def test_places_move_once_keep_their_ids_and_geo_keeps_its_copy(monkeypatch):
    r = R()
    _stored(r)
    before = {k: dict(v) for k, v in r.h.items() if k.startswith("geo:place")}
    calls = []

    async def call(method, path, *, params=None, json_body=None):
        calls.append((method, path, json_body))
        return {"created": 2, "kept": 0, "skipped": 0, "settings": 1, "settings_kept": 0, "unmatched": [],
                "entity_ids": {"place." + "a" * 32: "zone.made_up_library", "place." + "b" * 32: "place." + "b" * 32}}

    monkeypatch.setattr(hub_places, "_call", call)
    report = await hub_places.migrate(r, 1000.0)
    assert calls[0][:2] == ("POST", "/hub/zones/import")
    assert {p["id"] for p in calls[0][2]["places"]} == {"a" * 32, "b" * 32}
    assert calls[0][2]["settings"]["zone.home"]["notify"] == ["kim"]
    assert (report["places"], report["created"], report["settings_moved"]) == (2, 2, 1)
    # Presence state and reminders follow the family place to its zone.*; the private one keeps its id.
    assert json.loads(r.kv["geo:zone_state:kim"])["zone"] == "zone.made_up_library"
    assert json.loads(r.kv["geo:zone_state:sam"])["zone"] == "place." + "b" * 32
    assert json.loads(r.h["geo:place_reminders:kim"]["r1"])["zone"] == "zone.made_up_library"
    # Geo's own copy is a backup, untouched.
    assert r.h[hub_places.LEGACY_PLACES_KEY] == before[hub_places.LEGACY_PLACES_KEY]
    assert r.h[hub_places.LEGACY_SETTINGS_KEY] == before[hub_places.LEGACY_SETTINGS_KEY]
    # Once.
    assert await hub_places.migrate(r, 2000.0) is None and len(calls) == 1


@pytest.mark.asyncio
async def test_a_move_homehub_cannot_take_is_tried_again(monkeypatch):
    r = R()
    _stored(r)

    async def down(*_a, **_k):
        raise hub_places.PlacesUnavailable("HomeHub did not answer", 502)

    monkeypatch.setattr(hub_places, "_call", down)
    with pytest.raises(hub_places.PlacesUnavailable):
        await hub_places.migrate(r, 1000.0)
    assert hub_places.MIGRATED_KEY not in r.kv  # not marked done
    assert json.loads(r.kv["geo:zone_state:kim"])["zone"] == "place." + "a" * 32


@pytest.mark.asyncio
async def test_nothing_stored_is_nothing_to_move(monkeypatch):
    r = R()

    async def never(*_a, **_k):
        raise AssertionError("nothing to send")

    monkeypatch.setattr(hub_places, "_call", never)
    assert await hub_places.migrate(r, 1000.0) is None
    assert hub_places.MIGRATED_KEY in r.kv
