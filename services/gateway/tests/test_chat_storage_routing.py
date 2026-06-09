import pytest
import respx
import httpx
import sys
from unittest.mock import MagicMock
sys.modules['redis'] = MagicMock()

from fastapi.testclient import TestClient
from services.gateway.main import app, STORAGE_SVC
import json

client = TestClient(app)

@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer test-token"}

@respx.mock
def test_chat_storage_routing(auth_headers):
    # Mock identity resolution
    respx.post("http://127.0.0.1:8001/api/resolve").mock(
        return_value=httpx.Response(200, json={
            "user": "testuser",
            "is_admin": True,
            "ha_url": "http://ha.local",
            "ha_token": "token",
            "nextcloud_url": "http://nc.local",
            "nextcloud_user": "ncuser",
            "nextcloud_pass": "ncpass"
        })
    )
    
    # Mock the RAG search
    respx.post("http://127.0.0.1:8004/rag/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )

    # Mock Ollama generation - simulate a generated JSON tool block from LLM
    # We use a side effect to return a tool call the first time and a conversational response the second time
    # to avoid infinite loops in the AgentLoop.
    responses = [
        {
            "model": "qwen3:8b",
            "message": {
                "role": "assistant",
                "content": "Sure, I will index your storage.\n```json\n{\"action\": \"storageindexrequest\", \"payload\": {\"path\": \"/myfolder\"}}\n```"
            },
            "done": True
        },
        {
            "model": "qwen3:8b",
            "message": {
                "role": "assistant",
                "content": "I have started the indexing process for you. (System Update: Indexing started)"
            },
            "done": True
        }
    ]
    
    response_iter = iter(responses)
    def ollama_side_effect(request):
        try:
            return httpx.Response(200, json=next(response_iter))
        except StopIteration:
            return httpx.Response(200, json=responses[-1])

    llm_route = respx.post("http://127.0.0.1:11434/api/chat").mock(side_effect=ollama_side_effect)

    # Mock Storage Index call
    storage_route = respx.post(f"{STORAGE_SVC}/index/full").mock(
        return_value=httpx.Response(200, json={"message": "Indexing started"})
    )

    # Trigger chat handler
    response = client.post("/api/chat", json={
        "query": "index my storage please",
        "stream": False
    }, headers=auth_headers)
    
    assert response.status_code == 200
    data = response.json()
    assert "Indexing started" in data["message"]["content"]
    
    # 1. Verify the anti-refusal nudge was injected into the prompt
    assert llm_route.called
    llm_request_payload = json.loads(llm_route.calls.last.request.content)
    system_prompt = llm_request_payload["messages"][0]["content"]
    assert "CRITICAL DIRECTIVE: You have full permission to access the storage system" in system_prompt
    
    # 2. Verify the storage routing correctly mapped the payload
    assert storage_route.called
    storage_request_payload = json.loads(storage_route.calls.last.request.content)
    
    # 3. Verify it overrode the payload to match NextCloud Provider schema
    assert "provider" in storage_request_payload
    assert storage_request_payload["provider"]["kind"] == "nextcloud"
    assert storage_request_payload["provider"]["settings"]["username"] == "ncuser"
    assert storage_request_payload["provider"]["settings"]["password"] == "ncpass"
    assert storage_request_payload["path"] == "/myfolder"
    assert storage_request_payload["recursive"] is True
