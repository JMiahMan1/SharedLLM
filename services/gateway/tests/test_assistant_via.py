"""Jarvis acting for a person (chat and voice) marks its control requests ``via: "assistant"``.

Execution then controls only the entities the home hub exposes to voice (services/execution/voice_exposure.py).
The app's own controls never carry the mark.
"""
from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from services.gateway import agent_loop
from services.gateway.schemas import ResolvedCredentials


def test_credentials_carry_via_to_execution():
    assert ResolvedCredentials(user="pat").model_dump()["via"] is None
    assert ResolvedCredentials(user="pat", via="assistant").model_dump()["via"] == "assistant"


def test_the_agent_loop_marks_every_tool_call_as_the_assistant():
    # The loop's tool dispatch builds the user_context it sends to execution; the LLM's own arguments
    # are overwritten by it, so the mark cannot be dropped by a tool call.
    src = inspect.getsource(agent_loop)
    block = src[src.index('"identified_by": getattr(creds, "identified_by", None)'):][:600]
    assert '"via": "assistant"' in block


@pytest.mark.asyncio
async def test_a_chat_turn_is_queued_with_via_assistant(client: TestClient, monkeypatch):
    from services.gateway import main

    monkeypatch.setattr(main, "resolve_identity", AsyncMock(return_value={
        "user": "pat", "ha_url": "http://hub.test", "ha_token": "tok"}))
    monkeypatch.setattr(main, "fetch_ha_entities", AsyncMock(return_value=[]))
    monkeypatch.setattr(main, "update_history", AsyncMock(return_value=None))
    monkeypatch.setattr(main, "get_history", AsyncMock(return_value=[]))
    monkeypatch.setattr(main, "contextualize_query", AsyncMock(return_value="hello"))
    monkeypatch.setattr(main, "decompose_command_query", AsyncMock(return_value=[]))
    monkeypatch.setattr(main, "get_llm_settings", AsyncMock(return_value={
        "active_llm_provider": "ollama", "assistant_model": "qwen3:8b"}))
    monkeypatch.setattr(main, "get_assistant_model", AsyncMock(return_value="qwen3:8b"))
    monkeypatch.setattr(main, "fetch_global_setting", AsyncMock(return_value="0.85"))
    monkeypatch.setattr(main, "select_system_instruction_for_query", lambda q, m: "# System instruction mock")
    from services.gateway import sentence_triggers

    monkeypatch.setattr(sentence_triggers, "ask_hub", AsyncMock(return_value=None))  # no hub sentence matches
    queue = MagicMock()
    queue.enqueue_job = AsyncMock(return_value="job-1")
    queue.get_job_status = AsyncMock(return_value={"status": "completed", "result": "ok"})
    queue.get_chunks = AsyncMock(return_value=["ok"])
    monkeypatch.setattr(main, "job_queue", queue)

    class _Resp:
        status = 200

        async def json(self):
            return {"results": []}

    http = MagicMock()
    http.post = AsyncMock(return_value=_Resp())
    monkeypatch.setattr(main, "get_http_client", lambda: http)

    resp = client.post("/api/chat", json={"query": "hello", "user_id": "pat"},
                       headers={"X-Internal-Secret": "test-secret"})
    assert resp.status_code == 200
    payload = queue.enqueue_job.await_args.args[1]
    assert payload["creds"]["via"] == "assistant"
