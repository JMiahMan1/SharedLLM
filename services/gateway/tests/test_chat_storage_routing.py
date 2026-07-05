import pytest
import respx
import aiohttp
import sys
from unittest.mock import MagicMock, AsyncMock
_mock_redis_async = MagicMock()
_mock_redis = MagicMock()
_mock_redis.asyncio = _mock_redis_async
sys.modules['redis'] = _mock_redis
sys.modules['redis.asyncio'] = _mock_redis_async

from fastapi.testclient import TestClient
from services.gateway.main import app, STORAGE_SVC
from services.gateway.config import IDENTITY_SVC, RAG_SVC
import json
import os
from sqlmodel import Session, create_engine, select
from services.identity.models import GlobalSetting

# Provide default model values so get_test_settings() doesn't raise
os.environ.setdefault("ASSISTANT_MODEL", "qwen3:8b")
os.environ.setdefault("CODING_MODEL", "qwen2.5-coder:7b")
os.environ.setdefault("LIBRARIAN_MODEL", "qwen3:8b")
os.environ.setdefault("EMBEDDING_MODEL", "nomic-ai/nomic-embed-text-v1.5")

def get_test_settings():
    db_path = "/data/identity.db"
    if not os.path.exists(db_path):
        _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        db_path = os.path.join(_root, "data", "identity.db")

    settings = {}
    if os.path.exists(db_path) and os.path.getsize(db_path) > 0:
        try:
            engine = create_engine(f"sqlite:///{db_path}")
            with Session(engine) as session:
                for s in session.exec(select(GlobalSetting)).all():
                    settings[s.key] = s.value
        except Exception:
            pass

    # If DB has no values or doesn't exist, try querying identity service API directly
    if not settings.get("assistant_model") or not settings.get("coding_model"):
        identity_url = os.getenv("IDENTITY_SVC_URL", "http://127.0.0.1:8001")
        internal_secret = os.getenv("INTERNAL_SECRET", "change-me-in-production")
        try:
            import aiohttp
            with aiohttp.ClientSession(timeout=5.0) as client:
                resp = client.get(
                    f"{identity_url}/api/settings",
                    headers={"X-Internal-Secret": internal_secret}
                )
                if resp.status_code == 200:
                    for item in resp.json():
                        settings[item["key"]] = item["value"]
        except Exception:
            pass

    # Fallback to env without hardcoded strings
    if not settings.get("assistant_model"):
        settings["assistant_model"] = os.getenv("ASSISTANT_MODEL")
    if not settings.get("coding_model"):
        settings["coding_model"] = os.getenv("CODING_MODEL")
    if not settings.get("librarian_model"):
        settings["librarian_model"] = os.getenv("LIBRARIAN_MODEL") or settings.get("assistant_model")
    if not settings.get("llm_local_url"):
        settings["llm_local_url"] = os.getenv("OLLAMA_URL") or "http://localhost:11434"
    if not settings.get("embedding_model"):
        settings["embedding_model"] = os.getenv("EMBEDDING_MODEL") or "nomic-ai/nomic-embed-text-v1.5"
    if not settings.get("active_llm_provider"):
        settings["active_llm_provider"] = os.getenv("ACTIVE_LLM_PROVIDER") or "ollama"

    # Validate that we actually got model values
    if not settings.get("assistant_model"):
        raise ValueError(
            "assistant_model could not be loaded from database, identity API, or environment. "
            "Please configure ASSISTANT_MODEL."
        )
    if not settings.get("coding_model"):
        raise ValueError(
            "coding_model could not be loaded from database, identity API, or environment. "
            "Please configure CODING_MODEL."
        )

    # Force service URLs to match the test environment
    for key, env_var in [
        ("identity_svc_url", "IDENTITY_SVC_URL"),
        ("execution_svc_url", "EXECUTION_SVC_URL"),
        ("rag_svc_url", "RAG_SVC_URL"),
        ("storage_svc_url", "STORAGE_SVC_URL"),
        ("logging_svc_url", "LOGGING_SVC_URL"),
        ("workspace_runtime_svc_url", "WORKSPACE_RUNTIME_SVC_URL"),
        ("control_plane_url", "CONTROL_PLANE_URL"),
        ("llama_server_proxy_url", "LLAMA_SERVER_PROXY_URL")
    ]:
        if os.getenv(env_var):
            settings[key] = os.getenv(env_var)

    return settings

client = TestClient(app)

@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer test-token"}

@respx.mock
def test_chat_storage_routing(auth_headers):
    settings = get_test_settings()
    identity_svc = settings.get("identity_svc_url") or IDENTITY_SVC
    rag_svc = settings.get("rag_svc_url") or RAG_SVC
    storage_svc = settings.get("storage_svc_url") or STORAGE_SVC

    # Mock system instruction loading to avoid real Identity service calls
    import services.gateway.main as gateway_main
    gateway_main.select_system_instruction_for_query = lambda q, m: "# System instruction mock"
    gateway_main.load_prompt = AsyncMock(return_value="# Raven protocol mock")

    # Mock identity settings (needed by get_assistant_model in chat path)
    respx.get(f"{identity_svc}/api/settings").mock(
        return_value=MagicMock(200, json=[
            {"key": k, "value": v} for k, v in settings.items()
        ])
    )
    
    # Mock identity resolution
    respx.post(f"{identity_svc}/api/resolve").mock(
        return_value=MagicMock(200, json={
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
    respx.post(f"{rag_svc}/rag/search").mock(
        return_value=MagicMock(200, json={"results": []})
    )

    # Mock Ollama generation - simulate a generated JSON tool block from LLM
    # We use a side effect to return a tool call the first time and a conversational response the second time
    # to avoid infinite loops in the AgentLoop.
    responses = [
        {
            "model": settings.get("assistant_model"),
            "message": {
                "role": "assistant",
                "content": "Sure, I will index your storage.\n```json\n{\"action\": \"storageindexrequest\", \"payload\": {\"path\": \"/myfolder\"}}\n```"
            },
            "done": True
        },
        {
            "model": settings.get("assistant_model"),
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
            return MagicMock(200, json=next(response_iter))
        except StopIteration:
            return MagicMock(200, json=responses[-1])

    llm_local_url = settings.get("llm_local_url")
    llm_route = respx.post(f"{llm_local_url}/api/chat").mock(side_effect=ollama_side_effect)

    # Mock Storage Index call
    storage_route = respx.post(f"{storage_svc}/index/full").mock(
        return_value=MagicMock(200, json={"message": "Indexing started"})
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
