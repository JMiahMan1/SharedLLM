"""Tests for the geo (family-location) service.

These are unit tests: they mock the Home Assistant state fetch so no live
HA connection is required. They validate endpoint shape and the static
MapLibre client being served at "/".
"""
from unittest.mock import patch

import pytest


@pytest.fixture
def client(monkeypatch):
    import services.geo.main as geo

    # Keep HA as "configured" for index/health, but feed canned states.
    monkeypatch.setattr(geo, "HA_URL", "https://ha.test")
    monkeypatch.setattr(geo, "HA_TOKEN", "tok")

    sample = [
        {
            "entity_id": "person.summers",
            "state": "home",
            "attributes": {
                "friendly_name": "Summers",
                "latitude": 33.1666,
                "longitude": -111.5646,
                "gps_accuracy": 30,
            },
        },
        {
            "entity_id": "zone.home",
            "state": "3",
            "attributes": {
                "friendly_name": "Home",
                "latitude": 33.1667,
                "longitude": -111.5646,
                "radius": 100,
            },
        },
        # No lat/lon -> must be skipped.
        {"entity_id": "person.noloc", "state": "away", "attributes": {}},
    ]

    async def fake_states():
        return sample

    with patch.object(geo, "_ha_get_states", fake_states):
        from fastapi.testclient import TestClient

        yield TestClient(geo.app)


def test_health_reports_configured(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["ha_configured"] is True
    assert r.json()["service"] == "geo"


def test_people_geojson(client):
    r = client.get("/people")
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "FeatureCollection"
    ids = {f["properties"]["entity_id"] for f in body["features"]}
    assert "person.summers" in ids
    assert "person.noloc" not in ids  # skipped: no coords


def test_zones_geojson(client):
    r = client.get("/zones")
    assert r.status_code == 200
    body = r.json()
    ids = {f["properties"]["entity_id"] for f in body["features"]}
    assert "zone.home" in ids


def test_index_serves_client(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "maplibre" in r.text.lower()
    assert "Family Location" in r.text


def test_see_requires_secret(client):
    r = client.post(
        "/people/device_tracker.phone/see",
        json={"latitude": 1.0, "longitude": 2.0},
    )
    assert r.status_code == 403
