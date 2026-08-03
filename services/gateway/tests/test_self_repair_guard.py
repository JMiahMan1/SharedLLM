"""Guard tests for the self-repair (log-scan) mission loop.

Covers: no silent fallback chains for the coding model (only the config DB
value), per-container cooldown, and dedup against already-pending admin_fix
missions — so the coder agent is not re-triggered on every health-check cycle.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.gateway.background_worker import RavenWorker


def _aio_resp(status=200, json_data=None, text=None):
    m = MagicMock()
    m.status = status
    m.json = AsyncMock(return_value=json_data if json_data is not None else {})
    m.text = AsyncMock(return_value=text if text is not None else "")
    return m


def _client_ctx(mock_client):
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    return mock_ctx


PROBLEMATIC = [{"name": "sharedllm_rag", "count": 7, "sample": ["boom"]}]


class TestTriggerSelfRepair:
    @pytest.mark.asyncio
    async def test_skips_without_coding_model_and_never_pushes(self):
        worker = RavenWorker()
        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=_aio_resp(200, {"status": "SUCCESS"}))
        with patch("services.gateway.background_worker._shared_http_client", return_value=_client_ctx(mock_client)):
            await worker.trigger_self_repair(PROBLEMATIC, {"ollama_coding_model": "qwen2.5-coder:7b"})
        mock_client.post.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pushes_with_coding_model_from_settings(self):
        worker = RavenWorker()
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=_aio_resp(200, [], text="[]"))
        mock_client.post = AsyncMock(return_value=_aio_resp(200, {"status": "SUCCESS"}))
        with patch("services.gateway.background_worker._shared_http_client", return_value=_client_ctx(mock_client)):
            await worker.trigger_self_repair(PROBLEMATIC, {"coding_model": "qwen3-6-35b-a3b-ud-iq4-nl-mtp"})
        mock_client.post.assert_awaited_once()
        _, kwargs = mock_client.post.await_args
        assert kwargs["json"]["coding_model"] == "qwen3-6-35b-a3b-ud-iq4-nl-mtp"
        assert kwargs["json"]["mission_type"] == "admin_fix"
        assert kwargs["json"]["target_container"] == "sharedllm_rag"

    @pytest.mark.asyncio
    async def test_cooldown_skips_second_push_for_same_container(self):
        worker = RavenWorker()
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=_aio_resp(200, [], text="[]"))
        mock_client.post = AsyncMock(return_value=_aio_resp(200, {"status": "SUCCESS"}))
        ctx = _client_ctx(mock_client)
        with patch("services.gateway.background_worker._shared_http_client", return_value=ctx):
            await worker.trigger_self_repair(PROBLEMATIC, {"coding_model": "m1"})
            await worker.trigger_self_repair(PROBLEMATIC, {"coding_model": "m1"})
        assert mock_client.post.await_count == 1

    @pytest.mark.asyncio
    async def test_skips_when_admin_fix_already_pending_for_container(self):
        worker = RavenWorker()
        pending = [
            {
                "id": 77,
                "mission_type": "admin_fix",
                "target_container": "sharedllm_rag",
                "status": "executing",
            }
        ]
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=_aio_resp(200, pending, text=json.dumps(pending)))
        mock_client.post = AsyncMock(return_value=_aio_resp(200, {"status": "SUCCESS"}))
        with patch("services.gateway.background_worker._shared_http_client", return_value=_client_ctx(mock_client)):
            await worker.trigger_self_repair(PROBLEMATIC, {"coding_model": "m1"})
        mock_client.post.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_allows_push_when_pending_is_for_other_container(self):
        worker = RavenWorker()
        pending = [
            {
                "id": 78,
                "mission_type": "admin_fix",
                "target_container": "sharedllm_gateway",
                "status": "executing",
            }
        ]
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=_aio_resp(200, pending, text=json.dumps(pending)))
        mock_client.post = AsyncMock(return_value=_aio_resp(200, {"status": "SUCCESS"}))
        with patch("services.gateway.background_worker._shared_http_client", return_value=_client_ctx(mock_client)):
            await worker.trigger_self_repair(PROBLEMATIC, {"coding_model": "m1"})
        mock_client.post.assert_awaited_once()


class TestGetCodingModelFromSettings:
    @pytest.mark.asyncio
    async def test_uses_only_coding_model_key(self):
        worker = RavenWorker()
        settings = {
            "coding_model": "qwen3-6-35b-a3b-ud-iq4-nl-mtp",
            "ollama_coding_model": "qwen2.5-coder:7b",
            "assistant_model": "some-assistant",
        }
        with patch(
            "services.gateway.orchestrator.get_all_settings", new=AsyncMock(return_value=settings)
        ):
            assert await worker._get_coding_model_from_settings() == "qwen3-6-35b-a3b-ud-iq4-nl-mtp"

    @pytest.mark.asyncio
    async def test_ignores_fallback_keys_when_coding_model_missing(self):
        worker = RavenWorker()
        settings = {"ollama_coding_model": "qwen2.5-coder:7b", "assistant_model": "some-assistant"}
        with patch(
            "services.gateway.orchestrator.get_all_settings", new=AsyncMock(return_value=settings)
        ), pytest.raises(RuntimeError, match="coding_model"):
            await worker._get_coding_model_from_settings()

    @pytest.mark.asyncio
    async def test_raises_when_settings_unavailable(self):
        worker = RavenWorker()
        with patch(
            "services.gateway.orchestrator.get_all_settings",
            new=AsyncMock(side_effect=RuntimeError("down")),
        ), pytest.raises(RuntimeError, match="coding_model"):
            await worker._get_coding_model_from_settings()


class TestResolveCurrentCodingModel:
    @pytest.mark.asyncio
    async def test_uses_only_coding_model_key(self):
        from services.gateway.main import resolve_current_coding_model

        settings = {
            "coding_model": "qwen3-6-35b-a3b-ud-iq4-nl-mtp",
            "ollama_coding_model": "qwen2.5-coder:7b",
            "assistant_model": "some-assistant",
        }
        with patch(
            "services.gateway.main.get_all_settings", new=AsyncMock(return_value=settings)
        ):
            assert await resolve_current_coding_model() == "qwen3-6-35b-a3b-ud-iq4-nl-mtp"

    @pytest.mark.asyncio
    async def test_raises_with_clear_error_when_missing(self):
        from services.gateway.main import resolve_current_coding_model

        settings = {"ollama_coding_model": "qwen2.5-coder:7b"}
        with patch(
            "services.gateway.main.get_all_settings", new=AsyncMock(return_value=settings)
        ), pytest.raises(RuntimeError, match="Set coding_model in Identity settings"):
            await resolve_current_coding_model()


class TestExecuteRavenMissionModel:
    @pytest.mark.asyncio
    async def test_admin_fix_uses_current_settings_model_not_frozen(self):
        from services.gateway import main as gateway_main

        missions = [
            {
                "id": 55,
                "mission_type": "admin_fix",
                "coding_model": "qwen2.5-coder:7b",
                "proposed_mission": "SYSTEM ALERT: Health check detected errors.",
                "status": "queued",
            }
        ]
        settings = {"coding_model": "qwen3-6-35b-a3b-ud-iq4-nl-mtp"}

        async def fake_get_all_settings():
            return settings

        async def fake_resolve_identity(req):
            return {"user": "default", "is_admin": True}

        job_payloads = []

        async def fake_enqueue(job_type, payload):
            job_payloads.append((job_type, payload))

        class FakeQueue:
            async def enqueue_job(self, job_type, payload):
                await fake_enqueue(job_type, payload)

        mock_client = MagicMock()
        mock_client.get = AsyncMock(
            return_value=_aio_resp(200, missions, text=str(missions))
        )
        mock_client.patch = AsyncMock(return_value=_aio_resp(200, {}, text="{}"))
        mock_ctx = _client_ctx(mock_client)

        with (
            patch.object(gateway_main, "get_all_settings", side_effect=fake_get_all_settings),
            patch.object(gateway_main, "_resolve_identity_from_request", side_effect=fake_resolve_identity),
            patch.object(gateway_main, "borrow_http_client", return_value=mock_ctx),
            patch.object(gateway_main, "fetch_autonomous_protocols", new=AsyncMock(return_value="")),
            patch.object(gateway_main, "job_queue", FakeQueue()),
        ):
            from fastapi import Request

            request = MagicMock(spec=Request)
            await gateway_main.execute_raven_mission(55, request)

        assert job_payloads[0][1]["model"] == "qwen3-6-35b-a3b-ud-iq4-nl-mtp"
