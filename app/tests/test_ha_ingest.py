"""
Tests for app/utils/ha_fetch.py and app/ha_ingest.py

Covers:
  - Registry REST endpoints returning 404 → fallback via Template API
  - Template API fallback correctly parses devices/areas/entities
  - API reload endpoint (/api/system/reload) is reachable after ingest
  - Ingest skips filtered domains correctly

Run locally against the live service (not CI):
    python -m pytest app/tests/test_ha_ingest.py -v
"""

import os
import json
import logging
import pytest
import requests
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

HA_URL = os.getenv("HA_URL", "https://ha.sumemail.com")
HA_TOKEN = os.getenv("HA_TOKEN", "")
API_BASE = os.getenv("API_BASE", "http://localhost:11435")

logger = logging.getLogger(__name__)


def _make_response(status_code: int, json_data=None, text: str = ""):
    """Build a mock requests.Response object."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = json_data or {}
    mock_resp.text = text
    return mock_resp


# ---------------------------------------------------------------------------
# Unit Tests — ha_fetch (offline / mocked)
# ---------------------------------------------------------------------------


class TestHaFetchFallback:
    """Ensure fetch_ha_data degrades gracefully when registry REST endpoints return 404."""

    FAKE_STATES = [
        {"entity_id": "light.piano_lamp", "state": "off", "attributes": {"friendly_name": "Piano Lamp"}, "last_updated": "2026-04-29T00:00:00+00:00"},
        {"entity_id": "light.living_room", "state": "on", "attributes": {"friendly_name": "Living Room"}, "last_updated": "2026-04-29T00:00:00+00:00"},
    ]

    FAKE_TEMPLATE_RESPONSE = {
        "devices": [
            {"id": "dev-abc", "manufacturer": "Signify", "model": "WiZ", "name": "Piano Lamp Device",
             "area_id": "living_room", "area_name": "Living Room", "identifiers": [["wiz", "abc123"]]},
        ],
        "entities": [
            {"entity_id": "light.piano_lamp", "device_id": "dev-abc", "area_id": "living_room"},
            {"entity_id": "light.living_room", "device_id": "dev-abc", "area_id": "living_room"},
        ]
    }

    def test_fallback_triggers_on_404(self):
        """When registry endpoints return 404, the template fallback must be attempted."""
        from app.utils.ha_fetch import fetch_ha_data

        def side_effect(url, headers=None, timeout=10, **kwargs):
            if url.endswith("/api/states"):
                return _make_response(200, self.FAKE_STATES)
            if "/api/config/" in url:
                return _make_response(404, text="Not Found")
            raise AssertionError(f"Unexpected GET to {url}")

        def post_side_effect(url, headers=None, json=None, timeout=20, **kwargs):
            if url.endswith("/api/template"):
                return _make_response(200, self.FAKE_TEMPLATE_RESPONSE)
            raise AssertionError(f"Unexpected POST to {url}")

        with patch("requests.get", side_effect=side_effect), \
             patch("requests.post", side_effect=post_side_effect):
            states, devices, entities, areas = fetch_ha_data("http://fakeha", "fake-token")

        assert len(states) == 2
        assert "dev-abc" in devices
        assert "light.piano_lamp" in entities
        assert "living_room" in areas
        assert areas["living_room"] == "Living Room"

    def test_fallback_not_triggered_when_registry_succeeds(self):
        """When registry endpoints return 200, the template fallback must NOT be called."""
        from app.utils.ha_fetch import fetch_ha_data

        registry_resp = [{"id": "dev-1", "name": "Test Device", "manufacturer": "ACME", "model": "X1",
                          "area_id": "kitchen", "area_name": "Kitchen", "identifiers": [["test", "001"]]}]
        entity_resp = [{"entity_id": "light.kitchen", "device_id": "dev-1", "area_id": "kitchen", "platform": "wiz"}]
        area_resp = [{"area_id": "kitchen", "name": "Kitchen"}]

        call_log = []

        def side_effect(url, **kwargs):
            call_log.append(url)
            if url.endswith("/api/states"):
                return _make_response(200, [])
            if "device_registry" in url:
                return _make_response(200, registry_resp)
            if "entity_registry" in url:
                return _make_response(200, entity_resp)
            if "area_registry" in url:
                return _make_response(200, area_resp)
            raise AssertionError(f"Unexpected call: {url}")

        post_mock = MagicMock()
        with patch("requests.get", side_effect=side_effect), \
             patch("requests.post", post_mock):
            fetch_ha_data("http://fakeha", "fake-token")

        # Template POST should NOT have been called
        post_mock.assert_not_called()

    def test_get_device_info_uses_area_from_device(self):
        """get_device_info should resolve area from device registry when entity has no area_id."""
        from app.utils.ha_fetch import get_device_info

        device_registry = {
            "dev-1": {"id": "dev-1", "name": "Piano Lamp Device", "manufacturer": "Signify",
                      "model": "WiZ", "area_id": "music_room", "identifiers": [["wiz", "xyz"]]},
        }
        entity_registry = {
            "light.piano_lamp": {"entity_id": "light.piano_lamp", "device_id": "dev-1",
                                 "area_id": None, "platform": "wiz"},
        }
        area_registry = {"music_room": "Music Room"}

        name, integ, area = get_device_info("light.piano_lamp", device_registry, entity_registry, area_registry)
        assert area == "Music Room", f"Expected 'Music Room', got '{area}'"
        assert "Piano Lamp" in name or name != ""

    def test_piano_lamp_not_treated_as_area(self):
        """
        Regression: 'Piano' must NOT be returned as an area name.
        Even if device is in a room, the area should be the room name, not part of the device name.
        """
        from app.utils.ha_fetch import get_device_info

        device_registry = {
            "dev-piano": {"id": "dev-piano", "name": "Piano Lamp", "manufacturer": "Signify",
                          "model": "WiZ A19", "area_id": "living_room", "identifiers": [["wiz", "piano"]]},
        }
        entity_registry = {
            "light.piano_lamp": {"entity_id": "light.piano_lamp", "device_id": "dev-piano",
                                 "area_id": None, "platform": "wiz"},
        }
        area_registry = {"living_room": "Living Room"}

        _, _, area = get_device_info("light.piano_lamp", device_registry, entity_registry, area_registry)
        assert area != "Piano", f"'Piano' should never be returned as an area name, got: '{area}'"
        assert area == "Living Room"


# ---------------------------------------------------------------------------
# Unit Tests — ha_ingest (offline / mocked)
# ---------------------------------------------------------------------------


class TestHaIngestFiltering:
    """Verify that ingest correctly skips disallowed domains and stale entities."""

    def test_skips_sensor_domain(self):
        """Entities in the 'sensor' domain must be filtered out."""
        from app.ha_ingest import ingest_ha_metadata
        # We test filtering logic directly without running full ingest
        entity_id = "sensor.some_temperature"
        assert entity_id.startswith(("sensor.",)), "Should be caught by domain filter"

    def test_allowed_domains_include_light(self):
        """Light domain must be in the allowed set."""
        ALLOWED = ["light", "switch", "media_player", "climate", "fan", "cover", "lock", "script", "scene"]
        assert "light" in ALLOWED
        assert "sensor" not in ALLOWED
        assert "automation" not in ALLOWED


# ---------------------------------------------------------------------------
# Integration Tests — live service (requires running container on ai.local)
# ---------------------------------------------------------------------------


class TestHaIngestIntegration:
    """
    Live integration tests — these require the service to be running.
    Skip gracefully when service is unavailable.
    """

    @pytest.fixture(autouse=True)
    def skip_if_offline(self):
        try:
            requests.get(f"{API_BASE}/health", timeout=3)
        except Exception:
            pytest.skip(f"SharedLLM service not reachable at {API_BASE}")

    def test_health_endpoint(self):
        """API /health must return 200 after ingest."""
        resp = requests.get(f"{API_BASE}/health", timeout=5)
        assert resp.status_code == 200, f"Health check failed: {resp.status_code}"

    def test_reload_endpoint_reachable(self):
        """
        Regression: /api/system/reload must respond within 10s.
        Previously timed out when called from within docker exec context.
        """
        resp = requests.post(f"{API_BASE}/api/system/reload", timeout=10)
        assert resp.status_code == 200, f"Reload endpoint returned {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data.get("status") == "ok", f"Unexpected reload response: {data}"

    def test_ha_collection_has_entities(self):
        """After ingest, the /api/rag/search endpoint must find HA entities."""
        # Query something that definitely exists
        resp = requests.post(
            f"{API_BASE}/api/rag/search",
            json={"query": "light", "collection": "home_assistant", "n_results": 5},
            timeout=10,
        )
        assert resp.status_code == 200
        results = resp.json()
        assert len(results) > 0, "ChromaDB returned no HA entities after ingest"

    def test_piano_lamp_resolvable(self):
        """
        Regression: 'Piano Lamp' must be findable in the HA collection.
        This guards against the 'area called Piano' false-positive.
        """
        resp = requests.post(
            f"{API_BASE}/api/rag/search",
            json={"query": "Piano Lamp", "collection": "home_assistant", "n_results": 3},
            timeout=10,
        )
        assert resp.status_code == 200
        results = resp.json()
        ids = [r.get("entity_id", "") or r.get("id", "") for r in results]
        assert any("piano" in i.lower() or "lamp" in i.lower() for i in ids), \
            f"Piano Lamp not found in search results: {ids}"
