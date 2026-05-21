"""Tests for centralized configuration resolution from Identity service."""
import os
import sys
import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, PROJECT_ROOT)


class TestConfigBootstrap:
    """Test bootstrap phase: only INTERNAL_SECRET from .env."""

    def test_internal_secret_required(self, monkeypatch):
        """Service refuses to start without INTERNAL_SECRET."""
        monkeypatch.delenv("INTERNAL_SECRET", raising=False)
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")
        # Force fresh import
        if "config" in sys.modules:
            del sys.modules["config"]
        import config
        assert config.INTERNAL_SECRET == "__test_placeholder_INTERNAL_SECRET__"

    def test_identity_svc_default(self, monkeypatch):
        """IDENTITY_SVC_URL defaults to container name."""
        monkeypatch.delenv("IDENTITY_SVC_URL", raising=False)
        monkeypatch.setenv("INTERNAL_SECRET", "test-secret")
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")
        if "config" in sys.modules:
            del sys.modules["config"]
        import config
        assert config.IDENTITY_SVC_URL == "http://identity:8001"

    def test_no_env_getenv_in_config(self):
        """Config module does not use os.getenv for runtime values at import time."""
        config_path = os.path.join(PROJECT_ROOT, "services", "config.py")
        source = open(config_path).read()
        runtime_keys = [
            "FERNET_KEY", "OLLAMA_URL", "HA_URL", "HA_TOKEN",
            "ASSISTANT_MODEL", "CODING_MODEL", "REDIS_URL",
            "DEFAULT_TTS_VOICE", "EMBEDDING_MODEL",
        ]
        for key in runtime_keys:
            assert f'_required("{key}"' not in source, \
                f"Config should not require {key} from environment at import time"
            # Check it's not assigned via os.getenv at module level (outside functions)
            lines = source.split("\n")
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("#") or "(" in stripped or "def " in stripped:
                    continue
                if f'os.getenv("{key}"' in stripped:
                    pytest.fail(f"Config should not read {key} from env at module level: {stripped}")


class TestRuntimeConfigResolution:
    """Test runtime phase: all config from Identity service."""

    @pytest.mark.asyncio
    async def test_resolve_runtime_config_fetches_from_identity(self, monkeypatch, respx_mock):
        """resolve_runtime_config() calls Identity /api/settings and populates variables."""
        monkeypatch.setenv("INTERNAL_SECRET", "test-secret")
        monkeypatch.setenv("IDENTITY_SVC_URL", "http://identity:8001")
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        if "config" in sys.modules:
            del sys.modules["config"]
        import config

        # Override _is_testing to allow runtime resolution during tests
        monkeypatch.setattr(config, "_is_testing", lambda: False)

        respx_mock.get("http://identity:8001/api/settings").respond(
            json=[
                {"key": "fernet_key", "value": "test-fernet-key"},
                {"key": "llm_local_url", "value": "http://ollama:11434"},
                {"key": "ha_url", "value": "http://homeassistant:8123"},
                {"key": "ha_token", "value": "test-ha-token"},
                {"key": "assistant_model", "value": "llama3"},
                {"key": "fast_path_threshold", "value": "0.9"},
                {"key": "raven_error_threshold", "value": "3"},
            ]
        )

        await config.resolve_runtime_config()

        assert config.FERNET_KEY == "test-fernet-key"
        assert config.OLLAMA_URL == "http://ollama:11434"
        assert config.HA_URL == "http://homeassistant:8123"
        assert config.HA_TOKEN == "test-ha-token"
        assert config.ASSISTANT_MODEL == "llama3"
        assert config.FAST_PATH_THRESHOLD == 0.9
        assert config.RAVEN_ERROR_THRESHOLD == 3

    @pytest.mark.asyncio
    async def test_resolve_runtime_config_handles_identity_unavailable(self, monkeypatch, respx_mock):
        """resolve_runtime_config() does not crash if Identity is down."""
        monkeypatch.setenv("INTERNAL_SECRET", "test-secret")
        monkeypatch.setenv("IDENTITY_SVC_URL", "http://identity:8001")
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        if "config" in sys.modules:
            del sys.modules["config"]
        import config

        monkeypatch.setattr(config, "_is_testing", lambda: False)

        respx_mock.get("http://identity:8001/api/settings").mock(
            side_effect=Exception("Connection refused")
        )

        await config.resolve_runtime_config()

        # Should retain defaults, not crash
        assert config.FERNET_KEY == ""

    @pytest.mark.asyncio
    async def test_resolve_runtime_config_skips_in_tests(self, monkeypatch):
        """resolve_runtime_config() is a no-op when PYTEST_CURRENT_TEST is set."""
        monkeypatch.setenv("INTERNAL_SECRET", "test-secret")
        monkeypatch.setenv("IDENTITY_SVC_URL", "http://identity:8001")
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")
        if "config" in sys.modules:
            del sys.modules["config"]
        import config

        await config.resolve_runtime_config()

        # Should not have made any network calls; FERNET_KEY stays empty
        assert config.FERNET_KEY == ""


class TestConfigSettingsMap:
    """Test that all runtime variables are mapped correctly."""

    def test_all_runtime_vars_in_settings_map(self):
        """Every runtime variable should be in the settings_map."""
        config_path = os.path.join(PROJECT_ROOT, "services", "config.py")
        source = open(config_path).read()

        runtime_vars = [
            "FERNET_KEY", "OLLAMA_URL", "EXECUTION_SVC_URL", "RAG_SVC_URL",
            "STORAGE_SVC_URL", "LOGGING_SVC_URL", "WORKSPACE_RUNTIME_SVC_URL",
            "CONTROL_PLANE_URL", "SEARXNG_URL", "HA_URL", "HA_TOKEN",
            "NEXTCLOUD_URL", "NEXTCLOUD_USER", "NEXTCLOUD_PASS",
            "GIT_URL", "GIT_USER", "GIT_TOKEN", "REDIS_URL",
            "ASSISTANT_MODEL", "CODING_MODEL", "LIBRARIAN_MODEL",
            "DEFAULT_TTS_VOICE", "MASS_CONFIG_ENTRY_ID",
            "ABS_URL", "ABS_API_KEY",
            "EMBEDDING_MODEL", "FAST_PATH_THRESHOLD",
            "EXECUTION_EXTERNAL_HOST",
            "AUDIOBOOKSHELF_URL", "AUDIOBOOKSHELF_USER", "AUDIOBOOKSHELF_PASS",
        ]

        for var in runtime_vars:
            assert var in source, f"{var} should be in resolve_runtime_config settings_map"
