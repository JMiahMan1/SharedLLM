"""Snapping trip routes to roads with OSRM, and serving raw points when it
cannot (no OSRM configured, OSRM down, too few good fixes)."""
import pytest

from services.geo import map_match


def test_only_accurate_fixes_are_matched():
    pts = [{"t": 3, "lat": 1.0, "lon": 1.0, "acc": 8}, {"t": 1, "lat": 2.0, "lon": 2.0, "acc": 600},
           {"t": 2, "lat": 3.0, "lon": 3.0, "acc": 20}, {"t": 4, "lat": 1.0, "lon": 1.0, "acc": 5}]
    used = map_match.usable(pts)
    assert [p["t"] for p in used] == [2, 3]  # time order, coarse fix out, repeat out


@pytest.mark.asyncio
async def test_no_osrm_means_no_match():
    assert await map_match.match([{"t": 1, "lat": 1, "lon": 1, "acc": 5}, {"t": 2, "lat": 2, "lon": 2, "acc": 5}], None) is None


@pytest.mark.asyncio
async def test_osrm_down_means_no_match():
    pts = [{"t": 1, "lat": 33.1, "lon": -111.5, "acc": 5}, {"t": 2, "lat": 33.2, "lon": -111.5, "acc": 5}]
    assert await map_match.match(pts, "http://127.0.0.1:9") is None


@pytest.mark.asyncio
async def test_a_match_returns_the_road_path(monkeypatch):
    async def fake_chunk(session, base, chunk):
        # OSRM returns [lon, lat]; the road bends through an extra vertex
        return [[-111.5, 33.1], [-111.49, 33.15], [-111.5, 33.2]], 6000.0
    monkeypatch.setattr(map_match, "_match_chunk", fake_chunk)
    pts = [{"t": 1, "lat": 33.1, "lon": -111.5, "acc": 5}, {"t": 2, "lat": 33.2, "lon": -111.5, "acc": 5}]
    got = await map_match.match(pts, "http://osrm:5000")
    assert got["coordinates"] == [[33.1, -111.5], [33.15, -111.49], [33.2, -111.5]]
    assert got["distance_m"] == 6000.0


@pytest.mark.asyncio
async def test_long_trails_are_matched_in_overlapping_chunks(monkeypatch):
    calls = []

    async def fake_chunk(session, base, chunk):
        calls.append(len(chunk))
        return [[p["lon"], p["lat"]] for p in chunk], 1.0
    monkeypatch.setattr(map_match, "_match_chunk", fake_chunk)
    pts = [{"t": i, "lat": 33 + i * 0.001, "lon": -111.5, "acc": 5} for i in range(250)]
    got = await map_match.match(pts, "http://osrm:5000")
    assert calls == [100, 100, 52]
    assert len(got["coordinates"]) == 250  # shared boundary fixes are not doubled
