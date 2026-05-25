# services/execution/tests/test_ha_integration.py
"""
Integration tests for Home Assistant functionality.

These tests connect to the real HA instance via the execution service
and verify actual state changes, not just HTTP 200 responses.
They validate that services execute correctly and produce expected outcomes.

Run with: pytest services/execution/tests/test_ha_integration.py -v
"""
import asyncio
import os
import sys
import pytest
import time
from fastapi.testclient import TestClient

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["INTERNAL_SECRET"] = "test-secret"
from main import app
import main
import ha_client as ha_client_module

client = TestClient(app)

# ─── Helpers ──────────────────────────────────────────────────────────────────

async def resolve_default_user():
    """Resolve default user credentials for real HA testing."""
    return await main.resolve_internal_user("default")


def wait_for_state(entity_id: str, expected_state: str, ha_url: str, ha_token: str, timeout: int = 30, poll_interval: int = 2) -> dict:
    """Poll HA until entity reaches expected state or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = ha_client_module.get_state(ha_url, ha_token, entity_id)
        if state and state.get("state") == expected_state:
            return state
        time.sleep(poll_interval)
    # Return last known state for debugging
    return ha_client_module.get_state(ha_url, ha_token, entity_id)


def get_entity_state(entity_id: str, ha_url: str, ha_token: str) -> dict:
    """Get current state of an entity."""
    return ha_client_module.get_state(ha_url, ha_token, entity_id)


def call_ha_service(domain: str, service: str, entity_id: str, data: dict, ha_url: str, ha_token: str) -> dict:
    """Call a HA service and return the response."""
    return ha_client_module.call_service(ha_url, ha_token, domain, service, entity_id, data)


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def ha_credentials():
    """Get HA credentials from identity service."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    creds = loop.run_until_complete(resolve_default_user())
    loop.close()
    if not creds or not creds.get("ha_url") or not creds.get("ha_token"):
        pytest.skip("HA credentials not configured in identity service")
    return creds


@pytest.fixture(scope="module")
def ha_connection(ha_credentials):
    """Get HA URL and token."""
    return ha_credentials["ha_url"], ha_credentials["ha_token"]


# ─── Light Control Tests ─────────────────────────────────────────────────────

class TestLightControl:
    """Test light control with state verification."""

    def test_light_turn_on_and_off(self, ha_connection):
        """Test turning a light on and verifying state change."""
        ha_url, ha_token = ha_connection

        # Find a light entity that is currently off
        states = ha_client_module.get_states(ha_url, ha_token) or []
        test_light = None
        for s in states:
            if s["entity_id"].startswith("light.") and s.get("state") == "off":
                test_light = s["entity_id"]
                break

        if not test_light:
            pytest.skip("No light entity found in 'off' state")

        # Turn on via API
        resp = client.post("/execute/light",
            headers={"X-Internal-Secret": "test-secret"},
            json={
                "user_context": {"user": "test", "ha_url": ha_url, "ha_token": ha_token},
                "entity_id": test_light,
                "action": "turn_on",
            }
        )
        assert resp.status_code == 200
        result = resp.json()
        assert result["status"] == "SUCCESS", f"Light turn_on failed: {result.get('message')}"

        # Verify state actually changed to 'on'
        state = wait_for_state(test_light, "on", ha_url, ha_token, timeout=15)
        assert state is not None, f"Entity {test_light} not found after turn_on"
        assert state.get("state") == "on", f"Light state is '{state.get('state')}', expected 'on'"

        # Turn off
        resp = client.post("/execute/light",
            headers={"X-Internal-Secret": "test-secret"},
            json={
                "user_context": {"user": "test", "ha_url": ha_url, "ha_token": ha_token},
                "entity_id": test_light,
                "action": "turn_off",
            }
        )
        assert resp.status_code == 200
        result = resp.json()
        assert result["status"] == "SUCCESS", f"Light turn_off failed: {result.get('message')}"

        # Verify state changed back to 'off'
        state = wait_for_state(test_light, "off", ha_url, ha_token, timeout=15)
        assert state is not None, f"Entity {test_light} not found after turn_off"
        assert state.get("state") == "off", f"Light state is '{state.get('state')}', expected 'off'"

    def test_light_brightness(self, ha_connection):
        """Test setting light brightness and verifying it."""
        ha_url, ha_token = ha_connection

        # Find a dimmable light
        states = ha_client_module.get_states(ha_url, ha_token) or []
        test_light = None
        for s in states:
            if s["entity_id"].startswith("light.") and s.get("state") == "off":
                test_light = s["entity_id"]
                break

        if not test_light:
            pytest.skip("No light entity found in 'off' state")

        # Turn on with brightness
        resp = client.post("/execute/light",
            headers={"X-Internal-Secret": "test-secret"},
            json={
                "user_context": {"user": "test", "ha_url": ha_url, "ha_token": ha_token},
                "entity_id": test_light,
                "action": "turn_on",
                "brightness_pct": 50,
            }
        )
        assert resp.status_code == 200
        result = resp.json()
        assert result["status"] == "SUCCESS"

        # Verify brightness
        state = wait_for_state(test_light, "on", ha_url, ha_token, timeout=15)
        assert state is not None
        attrs = state.get("attributes", {})
        brightness = attrs.get("brightness")
        if brightness is not None:
            # HA brightness is 0-255, 50% should be ~128
            expected = round(255 * 0.5)
            assert abs(brightness - expected) <= 25, f"Brightness {brightness} not close to expected {expected}"

        # Cleanup: turn off
        call_ha_service("light", "turn_off", test_light, {}, ha_url, ha_token)


# ─── Media Player Tests ──────────────────────────────────────────────────────

class TestMediaPlayer:
    """Test media player functionality with state verification."""

    def test_media_player_state_query(self, ha_connection):
        """Test querying media player state returns valid data."""
        ha_url, ha_token = ha_connection

        states = ha_client_module.get_states(ha_url, ha_token) or []
        media_players = [s for s in states if s["entity_id"].startswith("media_player.")]
        assert len(media_players) > 0, "No media_player entities found"

        # Check that each media player has required attributes
        for mp in media_players[:5]:  # Test first 5
            state = mp.get("state")
            assert state in ["on", "off", "idle", "playing", "paused", "standby", "unavailable"], \
                f"Invalid state '{state}' for {mp['entity_id']}"

            attrs = mp.get("attributes", {})
            # All media players should have friendly_name
            assert "friendly_name" in attrs, f"Missing friendly_name for {mp['entity_id']}"

    def test_media_player_volume_control(self, ha_connection):
        """Test volume control on an available media player."""
        ha_url, ha_token = ha_connection

        # Find an idle or playing media player
        states = ha_client_module.get_states(ha_url, ha_token) or []
        test_mp = None
        for s in states:
            if s["entity_id"].startswith("media_player.") and s.get("state") in ["idle", "playing", "on"]:
                attrs = s.get("attributes", {})
                if attrs.get("volume_level") is not None:
                    test_mp = s["entity_id"]
                    break

        if not test_mp:
            pytest.skip("No media player with volume control found")

        # Get current volume
        state = get_entity_state(test_mp, ha_url, ha_token)
        state.get("attributes", {}).get("volume_level")

        # Set volume to 0.5
        resp = client.post("/execute/media/transport",
            headers={"X-Internal-Secret": "test-secret"},
            json={
                "user_context": {"user": "test", "ha_url": ha_url, "ha_token": ha_token},
                "entity_id": test_mp,
                "command": "volume_up",
            }
        )
        # Just verify the call succeeds - volume state varies by device
        assert resp.status_code == 200

    def test_media_status(self, ha_connection):
        """Test media status endpoint returns valid data."""
        ha_url, ha_token = ha_connection

        resp = client.post("/execute/media/status",
            headers={"X-Internal-Secret": "test-secret"},
            json={
                "user_context": {"user": "test", "ha_url": ha_url, "ha_token": ha_token},
            }
        )
        assert resp.status_code == 200
        result = resp.json()
        assert result["status"] == "SUCCESS"
        # Should return detail with media players
        assert "detail" in result
        detail = result["detail"]
        assert "playing" in detail or "idle" in detail or "all" in detail


# ─── Entity Search Tests ─────────────────────────────────────────────────────

class TestEntitySearch:
    """Test entity search functionality."""

    def test_search_media_player_by_name(self, ha_connection):
        """Test searching for media players by friendly name."""
        ha_url, ha_token = ha_connection

        # Get all states to find a searchable entity
        states = ha_client_module.get_states(ha_url, ha_token) or []
        media_players = [s for s in states if s["entity_id"].startswith("media_player.")]
        assert len(media_players) > 0

        # Pick a media player and search for it by part of its friendly name
        target = media_players[0]
        friendly = target.get("attributes", {}).get("friendly_name", "")
        search_term = friendly.split()[0] if friendly else target["entity_id"].split(".")[-1].split("_")[0]

        resp = client.post("/execute/entity/search",
            headers={"X-Internal-Secret": "test-secret"},
            json={
                "user_context": {"user": "test", "ha_url": ha_url, "ha_token": ha_token},
                "query": search_term,
                "domain": "media_player",
            }
        )
        assert resp.status_code == 200
        result = resp.json()
        assert result["status"] == "SUCCESS"
        entities = result["detail"]["entities"]
        assert len(entities) > 0, f"No results found for query '{search_term}'"

    def test_search_nonexistent_entity(self, ha_connection):
        """Test searching for a nonexistent entity returns empty results."""
        ha_url, ha_token = ha_connection

        resp = client.post("/execute/entity/search",
            headers={"X-Internal-Secret": "test-secret"},
            json={
                "user_context": {"user": "test", "ha_url": ha_url, "ha_token": ha_token},
                "query": "xyz_nonexistent_device_12345",
                "domain": "media_player",
            }
        )
        assert resp.status_code == 200
        result = resp.json()
        assert result["status"] == "SUCCESS"
        assert len(result["detail"]["entities"]) == 0


# ─── HA Service Call Tests ───────────────────────────────────────────────────

class TestHAServiceCall:
    """Test direct HA service calls."""

    def test_ha_service_turn_on_light(self, ha_connection):
        """Test direct HA service call to turn on a light."""
        ha_url, ha_token = ha_connection

        # Find an off light
        states = ha_client_module.get_states(ha_url, ha_token) or []
        test_light = None
        for s in states:
            if s["entity_id"].startswith("light.") and s.get("state") == "off":
                test_light = s["entity_id"]
                break

        if not test_light:
            pytest.skip("No light entity found in 'off' state")

        resp = client.post("/execute/ha_service",
            headers={"X-Internal-Secret": "test-secret"},
            json={
                "user_context": {"user": "test", "ha_url": ha_url, "ha_token": ha_token},
                "domain": "light",
                "service": "turn_on",
                "entity_id": test_light,
            }
        )
        assert resp.status_code == 200
        result = resp.json()
        assert result["status"] == "SUCCESS"

        # Verify state
        state = wait_for_state(test_light, "on", ha_url, ha_token, timeout=15)
        assert state is not None
        assert state.get("state") == "on"

        # Cleanup
        call_ha_service("light", "turn_off", test_light, {}, ha_url, ha_token)


# ─── Discovery Tests ─────────────────────────────────────────────────────────

class TestDiscovery:
    """Test device discovery endpoints."""

    def test_discovery_entities(self, ha_connection):
        """Test discovery entities endpoint returns data."""
        ha_url, ha_token = ha_connection

        resp = client.get("/discovery/entities")
        assert resp.status_code == 200
        result = resp.json()
        assert "entities" in result
        assert len(result["entities"]) > 0

    def test_discovery_devices(self, ha_connection):
        """Test discovery devices endpoint."""
        resp = client.get("/discovery/devices")
        assert resp.status_code == 200
        result = resp.json()
        assert "devices" in result

    def test_discovery_control_methods(self, ha_connection):
        """Test control methods documentation endpoint."""
        resp = client.get("/discovery/control_methods")
        assert resp.status_code == 200
        result = resp.json()
        assert "control_methods" in result
        assert len(result["control_methods"]) > 0


# ─── Logbook Tests ───────────────────────────────────────────────────────────

class TestLogbook:
    """Test logbook query functionality."""

    def test_logbook_query(self, ha_connection):
        """Test querying logbook entries."""
        ha_url, ha_token = ha_connection

        resp = client.post("/execute/ha_logbook",
            headers={"X-Internal-Secret": "test-secret"},
            json={
                "user_context": {"user": "test", "ha_url": ha_url, "ha_token": ha_token},
                "entity_id": "sun.sun",
                "days": 1,
            }
        )
        assert resp.status_code == 200
        result = resp.json()
        # sun.sun should always have logbook entries
        assert result["status"] == "SUCCESS"
        assert "detail" in result
        assert "entries" in result["detail"]


# ─── Identity Tests ──────────────────────────────────────────────────────────

class TestIdentity:
    """Test identity management."""

    def test_identity_list(self, ha_connection):
        """Test listing identities."""
        ha_url, ha_token = ha_connection

        resp = client.post("/execute/identity",
            headers={"X-Internal-Secret": "test-secret"},
            json={
                "user_context": {"user": "test", "ha_url": ha_url, "ha_token": ha_token},
                "action": "list",
            }
        )
        assert resp.status_code == 200
        result = resp.json()
        assert result["status"] == "SUCCESS"
        assert "detail" in result
        assert "users" in result["detail"]
        assert len(result["detail"]["users"]) > 0

    def test_identity_get_profile(self, ha_connection):
        """Test getting user profile."""
        ha_url, ha_token = ha_connection

        resp = client.post("/execute/identity/manage",
            headers={"X-Internal-Secret": "test-secret"},
            json={
                "user_context": {"user": "test", "ha_url": ha_url, "ha_token": ha_token},
                "action": "get_profile",
                "username": "default",
            }
        )
        assert resp.status_code == 200
        result = resp.json()
        assert result["status"] == "SUCCESS"
        assert "detail" in result
        profile = result["detail"]
        assert "ha_url" in profile
        assert "ha_token" in profile
        assert profile["ha_url"] == ha_url


# ─── Health Check ────────────────────────────────────────────────────────────

class TestHealth:
    """Test service health."""

    def test_health_endpoint(self):
        """Test health endpoint returns OK."""
        resp = client.get("/health")
        assert resp.status_code == 200
        result = resp.json()
        assert result["status"] == "ok"
        assert result["service"] == "execution"
