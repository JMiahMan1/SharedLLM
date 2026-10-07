"""Finding the map regions trips need that the road network lacks."""
import json

import pytest

from services.geo import map_regions

# Two squares, a parent containing a child: the child is the one to add.
INDEX = {"features": [
    {"properties": {"id": "us-west", "name": "US West", "parent": "us",
                    "urls": {"pbf": "https://download.geofabrik.de/north-america/us-west-latest.osm.pbf"}},
     "geometry": {"type": "Polygon", "coordinates": [[[-125, 30], [-100, 30], [-100, 50], [-125, 50], [-125, 30]]]}},
    {"properties": {"id": "us/new-mexico", "name": "New Mexico", "parent": "us-west",
                    "urls": {"pbf": "https://download.geofabrik.de/north-america/us/new-mexico-latest.osm.pbf"}},
     "geometry": {"type": "MultiPolygon", "coordinates": [[[[-109, 31], [-103, 31], [-103, 37], [-109, 37], [-109, 31]]]]}},
]}


def test_the_smallest_region_containing_the_point():
    got = map_regions.region_for(INDEX, 35.08, -106.65)  # Albuquerque
    assert got["name"] == "New Mexico" and got["url"].endswith("new-mexico-latest.osm.pbf")


def test_no_region_outside_every_outline():
    assert map_regions.region_for(INDEX, 10.0, 10.0) is None


class FakeRedis:
    def __init__(self):
        self.h, self.s = {}, {}

    async def get(self, k):
        return self.s.get(k)

    async def set(self, k, v, ex=None):
        self.s[k] = v

    async def hget(self, k, f):
        return self.h.get(k, {}).get(f)

    async def hset(self, k, f, v):
        self.h.setdefault(k, {})[f] = v

    async def hgetall(self, k):
        return dict(self.h.get(k, {}))


@pytest.mark.asyncio
async def test_an_off_map_trip_end_is_recorded_once(monkeypatch):
    r = FakeRedis()
    await r.set(map_regions.INDEX_KEY, json.dumps(INDEX))

    async def off(lat, lon, url):
        return True
    monkeypatch.setattr(map_regions, "off_map", off)
    await map_regions.note_if_off_map(r, 35.08, -106.65, "http://osrm:5000")
    await map_regions.note_if_off_map(r, 35.10, -106.60, "http://osrm:5000")
    needed = await map_regions.needed(r)
    assert [n["name"] for n in needed] == ["New Mexico"]


@pytest.mark.asyncio
async def test_an_on_map_point_records_nothing(monkeypatch):
    r = FakeRedis()

    async def on(lat, lon, url):
        return False
    monkeypatch.setattr(map_regions, "off_map", on)
    assert await map_regions.note_if_off_map(r, 33.4, -111.9, "http://osrm:5000") is None
    assert await map_regions.needed(r) == []
