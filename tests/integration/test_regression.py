"""
Regression tests for issues encountered during development.
These tests catch patterns that have broken before so we know immediately when they regress.

Issues covered:
- Ollama URL must resolve from Identity settings, not hardcoded defaults
- /api/config/models must always return 'models' key (even on error)
- Identity service must define _require_internal_secret for group/telemetry endpoints
- Group endpoints must accept flexible field names (name or group_id/cluster_id/pattern_id)
- All internal endpoints must require X-Internal-Secret header
"""
import pytest
import httpx
import os
import time

SERVER_IP = os.getenv("SERVER_IP", "192.168.2.205")
GATEWAY_URL = os.getenv("GATEWAY_URL", f"http://{SERVER_IP}:8080")
IDENTITY_URL = os.getenv("IDENTITY_URL", f"http://{SERVER_IP}:8001")
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")


@pytest.fixture
def internal_headers():
    return {"X-Internal-Secret": INTERNAL_SECRET}


@pytest.fixture
def internal_client(internal_headers):
    return httpx.Client(headers=internal_headers, timeout=30.0)


# ─── Issue: Ollama URL hardcoded instead of from Identity settings ─────────────

@pytest.mark.local_only
class TestOllamaUrlResolution:
    """Regression: Ollama URL was hardcoded to http://ollama:11434 which doesn't exist.
    Must resolve from Identity settings (llm_local_url) at runtime.
    """

    def test_config_models_returns_success_not_dns_error(self):
        """If Ollama URL is wrong, we get DNS errors. Success means URL is resolved correctly."""
        resp = httpx.get(f"{GATEWAY_URL}/api/config/models", timeout=10.0)
        assert resp.status_code == 200
        data = resp.json()
        # DNS error means URL is hardcoded to a non-existent host
        assert "Name or service not known" not in data.get("message", ""), (
            "Ollama URL appears to be hardcoded to a non-existent host. "
            "Must resolve from Identity settings (llm_local_url)."
        )

    def test_config_models_returns_models_key_on_success(self):
        """When successful, models key must contain actual model names."""
        resp = httpx.get(f"{GATEWAY_URL}/api/config/models", timeout=10.0)
        data = resp.json()
        if data.get("status") == "SUCCESS":
            assert len(data["models"]) > 0, "Models list should not be empty when status is SUCCESS"
            for model in data["models"]:
                assert model, "Model name should not be empty"
                assert len(model) >= 3, f"Model name '{model}' is too short to be valid"


# ─── Issue: /api/config/models missing 'models' key on error ──────────────────

@pytest.mark.local_only
class TestConfigEndpointContracts:
    """Regression: /api/config/models returned {status, message} on error but UI expected 'models' key.
    All endpoints must maintain consistent response contracts.
    """

    def test_models_endpoint_always_returns_models_key(self):
        """Even on error, models endpoint must return 'models' key (empty list)."""
        resp = httpx.get(f"{GATEWAY_URL}/api/config/models", timeout=10.0)
        assert resp.status_code == 200
        data = resp.json()
        assert "models" in data, (
            "/api/config/models must always return 'models' key. "
            "UI dropdowns depend on this key existing even when status is ERROR."
        )
        assert isinstance(data["models"], list), "'models' must be a list"

    def test_gateway_config_returns_all_model_mappings(self):
        """UI needs assistant_model, coding_model, librarian_model from /api/config."""
        resp = httpx.get(f"{GATEWAY_URL}/api/config", timeout=10.0)
        assert resp.status_code == 200
        data = resp.json()
        config = data.get("config", {})
        for key in ["assistant_model", "coding_model", "librarian_model"]:
            assert key in config, f"/api/config must return '{key}' for UI model dropdowns"


# ─── Issue: _require_internal_secret missing from identity service ─────────────

@pytest.mark.local_only
class TestIdentityInternalSecret:
    """Regression: _require_internal_secret was not defined in identity/main.py,
    causing NameError on all group and telemetry endpoints.
    """

    def test_groups_media_requires_internal_secret(self):
        """Media groups endpoint must require X-Internal-Secret header."""
        resp = httpx.get(f"{IDENTITY_URL}/api/groups/media")
        assert resp.status_code == 422, "Should reject request without X-Internal-Secret header"

    def test_groups_lights_requires_internal_secret(self):
        """Light clusters endpoint must require X-Internal-Secret header."""
        resp = httpx.get(f"{IDENTITY_URL}/api/groups/lights")
        assert resp.status_code == 422, "Should reject request without X-Internal-Secret header"

    def test_groups_patterns_requires_internal_secret(self):
        """Light patterns endpoint must require X-Internal-Secret header."""
        resp = httpx.get(f"{IDENTITY_URL}/api/groups/patterns")
        assert resp.status_code == 422, "Should reject request without X-Internal-Secret header"

    def test_telemetry_enroll_requires_internal_secret(self):
        """Telemetry enrollment endpoint must require X-Internal-Secret header."""
        resp = httpx.get(f"{IDENTITY_URL}/api/telemetry/enroll")
        assert resp.status_code == 422, "Should reject request without X-Internal-Secret header"

    def test_telemetry_snapshot_requires_internal_secret(self):
        """Telemetry snapshot endpoint must require X-Internal-Secret header."""
        resp = httpx.post(f"{IDENTITY_URL}/api/telemetry/snapshot", json={"entity_id": "test"})
        assert resp.status_code == 422, "Should reject request without X-Internal-Secret header"


# ─── Issue: Group endpoints requiring exact field names ───────────────────────

@pytest.mark.local_only
class TestGroupEndpointFlexibility:
    """Regression: Group endpoints required exact 'group_id'/'cluster_id'/'pattern_id' fields.
    UI sends 'name' field, so endpoints must accept both.
    """

    def test_create_media_group_accepts_name_field(self, internal_client):
        """Media group creation must accept 'name' as alternative to 'group_id'."""
        test_name = f"regression_test_{int(time.time())}"
        resp = internal_client.post(
            f"{IDENTITY_URL}/api/groups/media",
            json={"name": test_name, "member_entity_ids": ["media_player.test"]},
        )
        assert resp.status_code == 200, f"Should accept 'name' field: {resp.text}"
        # Clean up
        internal_client.delete(f"{IDENTITY_URL}/api/groups/media/{test_name}")

    def test_create_light_cluster_accepts_name_field(self, internal_client):
        """Light cluster creation must accept 'name' as alternative to 'cluster_id'."""
        test_name = f"regression_test_{int(time.time())}"
        resp = internal_client.post(
            f"{IDENTITY_URL}/api/groups/lights",
            json={"name": test_name, "member_entity_ids": ["light.test"]},
        )
        assert resp.status_code == 200, f"Should accept 'name' field: {resp.text}"
        # Clean up
        internal_client.delete(f"{IDENTITY_URL}/api/groups/lights/{test_name}")

    def test_create_light_pattern_accepts_name_field(self, internal_client):
        """Light pattern creation must accept 'name' as alternative to 'pattern_id'."""
        test_name = f"regression_test_{int(time.time())}"
        resp = internal_client.post(
            f"{IDENTITY_URL}/api/groups/patterns",
            json={"name": test_name, "steps": [{"brightness": 100}]},
        )
        assert resp.status_code == 200, f"Should accept 'name' field: {resp.text}"
        # Clean up
        internal_client.delete(f"{IDENTITY_URL}/api/groups/patterns/{test_name}")


# ─── Issue: Telemetry endpoints not working ───────────────────────────────────

@pytest.mark.local_only
class TestTelemetryEndpoints:
    """Regression: Telemetry endpoints must work end-to-end with proper auth."""

    def test_telemetry_enroll_and_unenroll(self, internal_client):
        """Full lifecycle: enroll, verify, unenroll."""
        entity_id = f"sensor.regression_test_{int(time.time())}"
        
        # Enroll
        resp = internal_client.post(
            f"{IDENTITY_URL}/api/telemetry/enroll",
            json={"entity_id": entity_id, "offline_alert_threshold_minutes": 20},
        )
        assert resp.status_code == 200, f"Enroll failed: {resp.text}"
        
        # Verify enrollment
        resp = internal_client.get(f"{IDENTITY_URL}/api/telemetry/enroll")
        assert resp.status_code == 200
        enrollments = resp.json().get("enrollments", [])
        enrolled_ids = [e["entity_id"] for e in enrollments]
        assert entity_id in enrolled_ids, f"Entity {entity_id} should be enrolled"
        
        # Unenroll
        resp = internal_client.delete(f"{IDENTITY_URL}/api/telemetry/enroll/{entity_id}")
        assert resp.status_code == 200, f"Unenroll failed: {resp.text}"

    def test_telemetry_snapshot_and_summary(self, internal_client):
        """Snapshot ingestion and summary retrieval."""
        entity_id = f"sensor.regression_test_{int(time.time())}"
        
        # Enroll
        internal_client.post(
            f"{IDENTITY_URL}/api/telemetry/enroll",
            json={"entity_id": entity_id},
        )
        
        # Ingest snapshot
        resp = internal_client.post(
            f"{IDENTITY_URL}/api/telemetry/snapshot",
            json={"entity_id": entity_id, "power_w": 50.5, "is_available": True, "state": "on"},
        )
        assert resp.status_code == 200, f"Snapshot failed: {resp.text}"
        
        # Get summary
        resp = internal_client.get(f"{IDENTITY_URL}/api/telemetry/summary/{entity_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("summary") is not None, "Summary should not be None after snapshot"
        assert data["summary"]["current_power_w"] == 50.5
        
        # Clean up
        internal_client.delete(f"{IDENTITY_URL}/api/telemetry/enroll/{entity_id}")
