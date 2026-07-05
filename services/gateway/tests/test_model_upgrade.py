import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from services.gateway.background_worker import RavenWorker


class TestModelUpgrade:
    """Tests for automatic model upgrade on schema failures."""

    def setup_method(self):
        self.worker = RavenWorker()

    def test_schema_error_triggers_upgrade(self):
        result = 'SCHEMA ERROR (422): [{"type": "missing", "loc": ["body", "patch"]}]'
        payload = {"_retry_count": 0}
        assert self.worker._should_upgrade_model(result, payload) is True

    def test_short_suspicious_success_triggers_upgrade(self):
        result = "Successfully wrote to services/tests/test_identity_resolution.py."
        payload = {"_retry_count": 0}
        assert self.worker._should_upgrade_model(result, payload) is True

    def test_no_upgrade_after_max_retries(self):
        result = 'SCHEMA ERROR (422): [{"type": "missing"}]'
        payload = {"_retry_count": 1}
        assert self.worker._should_upgrade_model(result, payload) is False

    def test_meaningful_failure_no_upgrade(self):
        result = "The CI test was fixed by adding an Authorization header. Verified with pytest."
        payload = {"_retry_count": 0}
        assert self.worker._should_upgrade_model(result, payload) is False

    def test_read_only_no_upgrade(self):
        result = "Read 11 lines from services/tests/test_identity_resolution.py (offset=0)"
        payload = {"_retry_count": 0}
        assert self.worker._should_upgrade_model(result, payload) is False

    def test_validation_error_triggers_upgrade(self):
        result = "Validation error: field 'patch' is required"
        payload = {"_retry_count": 0}
        assert self.worker._should_upgrade_model(result, payload) is True

    def test_no_valid_tool_call_triggers_upgrade(self):
        result = "ERROR: Agent failed to produce valid tool calls after multiple attempts."
        payload = {"_retry_count": 0}
        assert self.worker._should_upgrade_model(result, payload) is True

    def test_failed_to_produce_valid_tool_triggers_upgrade(self):
        result = "Mission did not accomplish meaningful work. Result: failed to produce valid tool"
        payload = {"_retry_count": 0}
        assert self.worker._should_upgrade_model(result, payload) is True


class TestDynamicModelSelection:
    """Tests for dynamic model upgrade selection via Ollama API."""

    def setup_method(self):
        self.worker = RavenWorker()

    @pytest.mark.asyncio
    async def test_selects_largest_model(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "models": [
                {"name": "qwen3:8b", "size": 5_000_000_000},
                {"name": "qwen3.6-35b-a3b:q4_k_m", "size": 21_000_000_000},
                {"name": "qwen2.5-coder:7b", "size": 4_000_000_000},
            ]
        }

        mock_settings = AsyncMock(return_value={"llm_local_url": "http://localhost:11434"})

        with patch("services.gateway.orchestrator.get_all_settings", mock_settings):
            with patch("aiohttp.ClientSession") as mock_client:
                mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client.return_value)
                mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
                mock_client.return_value.get = AsyncMock(return_value=mock_resp)

                result = await self.worker._get_upgrade_model("qwen3:8b")
                assert result == "qwen3.6-35b-a3b:q4_k_m"

    @pytest.mark.asyncio
    async def test_excludes_current_model(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "models": [
                {"name": "qwen3.6-35b-a3b:q4_k_m", "size": 21_000_000_000},
                {"name": "qwen3:8b", "size": 5_000_000_000},
            ]
        }

        mock_settings = AsyncMock(return_value={"llm_local_url": "http://localhost:11434"})

        with patch("services.gateway.orchestrator.get_all_settings", mock_settings):
            with patch("aiohttp.ClientSession") as mock_client:
                mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client.return_value)
                mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
                mock_client.return_value.get = AsyncMock(return_value=mock_resp)

                result = await self.worker._get_upgrade_model("qwen3.6-35b-a3b:q4_k_m")
                assert result == "qwen3:8b"

    @pytest.mark.asyncio
    async def test_returns_current_on_api_failure(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 500

        with patch("aiohttp.ClientSession") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client.return_value)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value.get = AsyncMock(return_value=mock_resp)

            result = await self.worker._get_upgrade_model("qwen3:8b")
            assert result == "qwen3:8b"

    @pytest.mark.asyncio
    async def test_returns_current_on_no_models(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"models": []}

        with patch("aiohttp.ClientSession") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client.return_value)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value.get = AsyncMock(return_value=mock_resp)

            result = await self.worker._get_upgrade_model("qwen3:8b")
            assert result == "qwen3:8b"

    @pytest.mark.asyncio
    async def test_returns_current_on_exception(self):
        with patch("aiohttp.ClientSession") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(side_effect=Exception("Connection refused"))
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await self.worker._get_upgrade_model("qwen3:8b")
            assert result == "qwen3:8b"
