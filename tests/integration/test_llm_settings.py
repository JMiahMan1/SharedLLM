"""
Tests for LLM Settings UI component and model availability API.
"""
import os

import httpx
import pytest

SERVER_IP = os.getenv("SERVER_IP", "192.168.2.205")
GATEWAY_URL = os.getenv("GATEWAY_URL", f"http://{SERVER_IP}:8080")


@pytest.mark.local_only
class TestLLMModelAvailability:
    """Validates that the model dropdown in LLMSettings receives proper data."""

    def test_available_models_endpoint_returns_array(self):
        """GET /api/config/models must return {status, models: string[]}."""
        resp = httpx.get(f"{GATEWAY_URL}/api/config/models", timeout=10.0)
        assert resp.status_code == 200
        data = resp.json()
        assert "models" in data, "Response must contain 'models' key"
        assert isinstance(data["models"], list), "'models' must be a list"
        if data.get("status") == "SUCCESS":
            assert len(data["models"]) > 0, "Models list should not be empty when status is SUCCESS"
            for model in data["models"]:
                assert isinstance(model, str), "Each model must be a string"

    def test_available_models_endpoint_handles_ollama_unavailable(self):
        """GET /api/config/models must gracefully handle Ollama being unreachable."""
        resp = httpx.get(f"{GATEWAY_URL}/api/config/models", timeout=10.0)
        assert resp.status_code == 200
        data = resp.json()
        if data.get("status") == "ERROR":
            assert "message" in data, "Error response must include a message"
            assert "models" in data, "Error response must still include 'models' key (empty list)"
            assert data["models"] == [], "Models should be empty list on error"

    def test_gateway_config_returns_model_mappings(self):
        """GET /api/config must return model mappings for UI dropdowns."""
        resp = httpx.get(f"{GATEWAY_URL}/api/config", timeout=10.0)
        assert resp.status_code == 200
        data = resp.json()
        assert "config" in data, "Response must contain 'config' key"
        config = data["config"]
        assert "assistant_model" in config, "Config must include assistant_model"
        assert "coding_model" in config, "Config must include coding_model"
        assert "librarian_model" in config, "Config must include librarian_model"
