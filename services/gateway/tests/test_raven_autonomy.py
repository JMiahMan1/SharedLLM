import pytest
import os
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
import httpx

from services.gateway.agent_loop import AgentLoop
from services.gateway.schemas import ResolvedCredentials

@pytest.mark.asyncio
async def test_autonomous_fix_the_app_scenario():
    """
    Scenario 1: Raven receives instruction to "fix the app"
    Expects AgentLoop to run and produce a final answer (no errors in orchestration)
    """
    # Mock identity resolution to return admin user with HA URL
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "user": "raven_test",
        "is_admin": True,
        "ha_url": "http://ha.local",
        "llm_cloud_url": "http://ollama:11434",
        "llm_cloud_api_key": "",
        "active_llm_provider": "ollama",
        "assistant_model": "llama3.2:3b",
        "coding_model": "codellama:7b",
        "librarian_model": "llama3.2:3b",
        "ollama_url": "http://ollama:11434",
        "ollama_timeout": "600",
        "fast_path_threshold": "0.85",
        "raven_max_total_seconds": "1800",
        "raven_iteration_timeout": "600",
        "raven_heartbeat_interval": "30",
        "raven_hung_threshold": "600",
        "raven_check_interval": "300",
        "raven_error_threshold": "5",
        "timezone": "UTC",
        "embedding_model": "nomic-ai/nomic-embed-text-v1.5",
    }

    # Mock config fetch (Identity service)
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_resp)):
        # Mock other external calls (RAG, execution, storage) to return minimal responses
        with patch("httpx.AsyncClient.post", new=AsyncMock()) as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"message": "ok", "results": []}
            # Mock agent loop to avoid actual LLM calls but verify orchestration path
            with patch("services.gateway.orchestrator._single_turn_inference", new=AsyncMock()) as mock_infer:
                mock_infer.return_value = "I have completed the requested fix."
                creds = ResolvedCredentials(
                    user="raven_test",
                    is_admin=True,
                    ha_url="http://ha.local",
                    ha_token="test-token",
                )
                result = await AgentLoop(
                    query="fix the app",
                    model="codellama:7b",
                    system_prompt="You are a helpful assistant.",
                    short_term=[],
                    user_id="raven_test",
                    creds=creds,
                    mission_id="test-mission-1",
                    rag_context="",
                    show_thinking=False
                )
                assert result is not None
                assert "completed" in result.lower()
                # Verify that AgentLoop attempted to orchestrate (mock called)
                assert mock_infer.called