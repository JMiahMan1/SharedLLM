import os
import json
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch

os.environ.setdefault("INTERNAL_SECRET", "test-secret")
os.environ.setdefault("FAST_PATH_THRESHOLD", "0.85")
os.environ.setdefault("IDENTITY_SVC_URL", "http://identity")
os.environ.setdefault("EXECUTION_SVC_URL", "http://execution")
os.environ.setdefault("OLLAMA_URL", "http://ollama")
os.environ.setdefault("ASSISTANT_MODEL", "qwen3:8b")
os.environ.setdefault("CODING_MODEL", "qwen2.5-coder:7b")
os.environ.setdefault("LIBRARIAN_MODEL", "qwen3:8b")

from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import Response
import pytest

import gateway.main as gateway_main
import gateway.orchestrator as gateway_orchestrator
from gateway.main import app, select_model_for_query, select_system_instruction_for_query
from gateway.prompts import (
    ASSIST_SYSTEM_INSTRUCTION,
    CODE_HELPER_SYSTEM_INSTRUCTION,
    LIBRARIAN_SYSTEM_INSTRUCTION,
    RAVEN_AUTONOMOUS_PROTOCOL,
)


@pytest.fixture
def client(monkeypatch):
    @asynccontextmanager
    async def noop_lifespan(_app):
        yield

    async def fake_request(*args, **kwargs):
        return SimpleNamespace(status_code=200, json=lambda: {"status": "SUCCESS"}, text="")

    async def fake_post(*args, **kwargs):
        return SimpleNamespace(status_code=200, json=lambda: {"status": "SUCCESS"}, text="")

    async def fake_get(*args, **kwargs):
        return SimpleNamespace(status_code=200, json=lambda: {"status": "SUCCESS"}, text="")

    monkeypatch.setattr(app.router, "lifespan_context", noop_lifespan)
    monkeypatch.setattr(gateway_main, "_global_http_client", SimpleNamespace(request=fake_request, post=fake_post, get=fake_get))
    test_client = TestClient(app)
    try:
        yield test_client
    finally:
        test_client.close()


class MockOllamaResponse:
    def __init__(self, content: str):
        self.status_code = 200
        self._content = content

    def json(self):
        return {"message": {"content": self._content}}


def _json_request(payload: dict) -> Request:
    body = json.dumps(payload).encode()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [(b"content-type", b"application/json")],
        },
        receive,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        "Fix this Python traceback in my FastAPI app",
        "Write a pytest for this bug",
        "Help me refactor this SQL query",
        "Why is this Docker build failing",
        "Show me the git command to squash these commits",
        "Edit this file: greeting.py and change one string",
    ],
)
async def test_select_model_for_query_uses_coding_model_for_code_requests(query, monkeypatch):
    monkeypatch.setattr(gateway_main, "get_coding_model", AsyncMock(return_value="qwen2.5-coder:7b"))
    monkeypatch.setattr(gateway_main, "get_assistant_model", AsyncMock(return_value="qwen3:8b"))
    monkeypatch.setattr(gateway_main, "get_librarian_model", AsyncMock(return_value="qwen3:8b"))
    assert await select_model_for_query(query) == "qwen2.5-coder:7b"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        "What is the weather like today?",
        "Summarize my notes from this week",
        "Play some jazz in the kitchen",
    ],
)
async def test_select_model_for_query_uses_assistant_model_for_general_requests(query, monkeypatch):
    monkeypatch.setattr(gateway_main, "get_coding_model", AsyncMock(return_value="qwen2.5-coder:7b"))
    monkeypatch.setattr(gateway_main, "get_assistant_model", AsyncMock(return_value="qwen3:8b"))
    monkeypatch.setattr(gateway_main, "get_librarian_model", AsyncMock(return_value="qwen3:8b"))
    assert await select_model_for_query(query) == "qwen3:8b"


def test_select_system_instruction_handles_standalone_main_import(monkeypatch):
    original_package = gateway_main.__package__
    monkeypatch.setattr(gateway_main, "__package__", None)
    try:
        select_system_instruction_for_query(
            "Please analyze logs and self repair this service",
            "qwen2.5-coder:7b",
        )
    finally:
        monkeypatch.setattr(gateway_main, "__package__", original_package)


@pytest.mark.asyncio
async def test_chat_handler_routes_direct_file_edit_requests_to_code_orchestration(monkeypatch):
    monkeypatch.setattr(gateway_main, "resolve_identity", AsyncMock(return_value={"user": "alice"}))
    monkeypatch.setattr(gateway_main, "update_history", AsyncMock(return_value=None))
    monkeypatch.setattr(gateway_main, "get_coding_model", AsyncMock(return_value="qwen2.5-coder:7b"))
    monkeypatch.setattr(gateway_main, "get_assistant_model", AsyncMock(return_value="qwen3:8b"))
    mock_orchestrate = AsyncMock(return_value=gateway_main._make_ollama_response("orchestrated", "qwen2.5-coder:7b"))
    monkeypatch.setattr(gateway_main, "orchestrate_code_change", mock_orchestrate)
    monkeypatch.setattr(gateway_main.job_queue, "enqueue_job", AsyncMock(return_value="test-job-id"))

    response = await gateway_main.chat_handler(
        _json_request(
            {
                "query": "Create a pytest file named temp/test_demo.py that asserts 2 + 2 == 4. Verify it with pytest.",
                "rag_user": "alice",
                "model": "qwen2.5-coder:7b",
            }
        )
    )

    assert response.status_code == 200
    assert "orchestrated" in json.loads(response.body)["message"]["content"]
    mock_orchestrate.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolve_chat_workspace_bootstraps_unavailable_workspace(monkeypatch):
    calls = []

    async def fake_runtime_request(method, path, *, json_payload=None, params=None):
        calls.append((method, path, json_payload, params))
        if method == "GET" and path == "/workspaces":
            return {
                "status": "SUCCESS",
                "workspaces": [
                    {
                        "id": "sharedllm",
                        "scope": "user",
                        "available": False,
                        "resolved_path": None,
                    }
                ],
            }
        if method == "POST" and path == "/workspaces/bootstrap":
            return {
                "status": "SUCCESS",
                "workspace": {
                    "id": "sharedllm",
                    "scope": "user",
                    "available": True,
                    "resolved_path": "/workspace/SharedLLM",
                },
            }
        raise AssertionError(f"Unexpected runtime request: {(method, path)}")

    monkeypatch.setattr(gateway_main, "workspace_runtime_request", fake_runtime_request)

    workspace = await gateway_main.resolve_chat_workspace({}, "alice")

    assert workspace is not None
    assert workspace["id"] == "sharedllm"
    assert any(path == "/workspaces/bootstrap" for _, path, _, _ in calls)


@pytest.mark.asyncio
async def test_workspace_bootstrap_proxy_uses_gateway_route(monkeypatch):
    captured = {}

    async def fake_request(method, url, json=None, headers=None):
        captured["method"] = method
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"status": "SUCCESS", "workspace": {"id": "alice-demo"}},
            text="",
        )

    mock_client = SimpleNamespace(request=fake_request)
    monkeypatch.setattr(gateway_main, "_global_http_client", mock_client)
    monkeypatch.setattr(gateway_main, "resolve_identity", AsyncMock(return_value={"user": "alice"}))

    response = await gateway_main.bootstrap_workspace_proxy(
        _json_request(
            {
                "workspace_id": "alice-demo",
                "rag_user": "alice",
                "repo_url": "https://example.com/demo.git",
                "create_if_missing": True,
            }
        )
    )

    payload = json.loads(response.body)
    assert response.status_code == 200
    assert payload["workspace"]["id"] == "alice-demo"
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/workspaces/bootstrap")
    assert captured["json"]["workspace_id"] == "alice-demo"
    assert captured["headers"]["X-Internal-Secret"] == gateway_main.INTERNAL_SECRET


@pytest.mark.asyncio
async def test_workspace_pytest_proxy_uses_gateway_route(monkeypatch):
    captured = {}

    async def fake_request(method, url, json=None, headers=None):
        captured["method"] = method
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"status": "SUCCESS", "exit_code": 0},
            text="",
        )

    mock_client = SimpleNamespace(request=fake_request)
    monkeypatch.setattr(gateway_main, "_global_http_client", mock_client)
    monkeypatch.setattr(gateway_main, "resolve_identity", AsyncMock(return_value={"user": "alice"}))

    response = await gateway_main.pytest_workspace_proxy(
        _json_request(
            {
                "workspace_id": "alice-demo",
                "rag_user": "alice",
                "targets": ["tests/test_demo.py"],
            }
        )
    )

    payload = json.loads(response.body)
    assert response.status_code == 200
    assert payload["exit_code"] == 0
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/tests/pytest")
    assert captured["json"]["targets"] == ["tests/test_demo.py"]
    assert captured["headers"]["X-Internal-Secret"] == gateway_main.INTERNAL_SECRET


@pytest.mark.asyncio
async def test_orchestrate_code_change_uses_review_branch_workflow_payload(monkeypatch):
    captured = {}

    async def fake_execute_inference(payload):
        return {
            "message": {
                "content": json.dumps(
                    {
                        "relative_path": "tests/test_sample.py",
                        "content": "def test_sample():\n    assert True\n",
                        "reasoning": "Create a minimal targeted test file.",
                        "test_cmd": "pytest tests/test_sample.py -q",
                    }
                )
            }
        }

    async def fake_workspace_runtime_request(method, path, *, json_payload=None, params=None):
        captured["method"] = method
        captured["path"] = path
        captured["json"] = json_payload
        return {
            "commit": {"commit": "abc123"},
            "provider_sync": {"status": "SUCCESS"},
            "review": {
                "head": "raven/alice/test-sample-123",
                "base": "main",
                "summary": {
                    "pytest": {"passed": True, "targets": ["tests/test_sample.py"]},
                },
            },
        }

    monkeypatch.setattr(gateway_main, "resolve_chat_workspace", AsyncMock(return_value={"id": "alice-demo"}))
    monkeypatch.setattr(gateway_main, "build_workspace_readme_context", AsyncMock(return_value="workspace context"))
    monkeypatch.setattr(gateway_main, "execute_inference", fake_execute_inference)
    monkeypatch.setattr(gateway_main, "workspace_runtime_request", fake_workspace_runtime_request)

    response = await gateway_main.orchestrate_code_change(
        body={},
        user_id="alice",
        refined_query="Add a targeted pytest for the sample module",
        selected_model="qwen2.5-coder:7b",
        should_stream=False,
        is_openai=False,
    )

    assert captured["method"] == "POST"
    assert captured["path"] == "/workflow/write-sync-commit"
    assert captured["json"]["workspace_id"] == "alice-demo"
    assert captured["json"]["relative_path"] == "tests/test_sample.py"
    assert captured["json"]["lint_paths"] == ["tests/test_sample.py"]
    assert captured["json"]["pytest_targets"] == ["tests/test_sample.py"]
    assert captured["json"]["auto_create_review_branch"] is True
    assert captured["json"]["review_branch_prefix"] == "raven"
    assert captured["json"]["push"] is True

    payload = json.loads(response.body)
    assert payload["message"]["content"].find("Review Branch") != -1


@pytest.mark.asyncio
async def test_orchestrate_code_change_parses_fenced_json_payload(monkeypatch):
    async def fake_execute_inference(payload):
        return {
            "message": {
                "content": """```json
{
  "relative_path": "temp/test_raven_live.py",
  "content": "def test_raven_sanity():\\n    assert 2 + 2 == 4\\n",
  "reasoning": "Create a minimal sanity test.",
  "test_cmd": "pytest temp/test_raven_live.py -q"
}
```"""
            }
        }

    async def fake_workspace_runtime_request(method, path, *, json_payload=None, params=None):
        if path != "/workflow/write-sync-commit":
            raise AssertionError(f"Unexpected path: {path}")
        return {
            "commit": {"commit": "abc123"},
            "provider_sync": {"status": "SUCCESS"},
            "review": {
                "head": "raven/alice/test-raven-live",
                "base": "main",
                "summary": {
                    "pytest": {"passed": True, "targets": ["temp/test_raven_live.py"]},
                },
            },
        }

    monkeypatch.setattr(gateway_main, "resolve_chat_workspace", AsyncMock(return_value={"id": "alice-demo"}))
    monkeypatch.setattr(gateway_main, "build_workspace_readme_context", AsyncMock(return_value="workspace context"))
    monkeypatch.setattr(gateway_main, "execute_inference", fake_execute_inference)
    monkeypatch.setattr(gateway_main, "workspace_runtime_request", fake_workspace_runtime_request)

    response = await gateway_main.orchestrate_code_change(
        body={},
        user_id="alice",
        refined_query="Create a pytest file named temp/test_raven_live.py that asserts 2 + 2 == 4. Verify it with pytest.",
        selected_model="qwen3.6-35b-a3b:q4_k_m",
        should_stream=False,
        is_openai=False,
    )

    payload = json.loads(response.body)
    assert response.status_code == 200
    assert "temp/test_raven_live.py" in payload["message"]["content"]


@pytest.mark.asyncio
async def test_single_turn_inference_supports_capability_index_tool(monkeypatch):
    captured = {}

    class FakeAsyncClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return SimpleNamespace(
                status_code=200,
                text="",
                json=lambda: {"message": "Capability index refreshed."},
            )

    monkeypatch.setattr(
        gateway_orchestrator,
        "call_ollama",
        AsyncMock(
            return_value={
                "message": {
                    "content": """```json
{"action":"CapabilityIndexRequest","payload":{"scope":"full"}}
```"""
                }
            }
        ),
    )
    monkeypatch.setattr(gateway_orchestrator.httpx, "AsyncClient", FakeAsyncClient)

    creds = gateway_main.ResolvedCredentials(user="alice")
    result = await gateway_orchestrator._single_turn_inference(
        query="Refresh your capability index.",
        model="qwen3:latest",
        system_prompt=ASSIST_SYSTEM_INSTRUCTION,
        rag_context="",
        history=[],
        creds=creds,
    )

    assert result == "Capability index refreshed."
    assert captured["url"].endswith("/execute/index_capabilities")
    assert captured["json"]["scope"] == "full"
    assert captured["json"]["user_context"]["user"] == "alice"


@pytest.mark.asyncio
async def test_single_turn_inference_uses_assist_prompt_with_full_capability_guide(monkeypatch):
    captured = {}

    async def fake_call_ollama(payload, use_chat=True):
        captured["payload"] = payload
        return {"message": {"content": "I can help with that."}}

    monkeypatch.setattr(gateway_orchestrator, "call_ollama", fake_call_ollama)

    creds = gateway_main.ResolvedCredentials(user="alice")
    result = await gateway_orchestrator._single_turn_inference(
        query="What is the temperature upstairs?",
        model="qwen3:latest",
        system_prompt=ASSIST_SYSTEM_INSTRUCTION,
        rag_context="[HA_ENTITIES]\nsensor.upstairs_temperature",
        history=[],
        creds=creds,
    )

    system_message = captured["payload"]["messages"][0]["content"]
    assert result == "I can help with that."
    assert system_message.startswith(ASSIST_SYSTEM_INSTRUCTION)
    assert "System Capability Context:" in system_message
    assert "ClimateRequest" in system_message
    assert "ExecutionLogRequest" in system_message
    assert "sensor.upstairs_temperature" in system_message


@pytest.mark.local_only
def test_chat_slow_path_uses_coding_model_for_code_requests(client):
    captured = {}

    async def mock_call_ollama(payload, use_chat=True):
        captured["payload"] = payload
        captured["use_chat"] = use_chat
        return MockOllamaResponse("Use the stack trace to narrow the failing module.")

    async def passthrough_query(query, history):
        return query

    with patch("gateway.main.resolve_identity", new=AsyncMock(return_value={"user": "alice"})), \
         patch("gateway.main.get_history", new=AsyncMock(return_value=[])), \
         patch("gateway.main.fetch_ha_entities", new=AsyncMock(return_value=[])), \
         patch("gateway.main.contextualize_query", new=AsyncMock(side_effect=passthrough_query)), \
         patch("gateway.main.update_history", new=AsyncMock(return_value=None)), \
         patch("gateway.main.emit_log", new=AsyncMock(return_value=None)), \
         patch("gateway.main.engine.classify", return_value=("unknown", 0.10)), \
         patch("gateway.orchestrator.call_ollama", new=AsyncMock(side_effect=mock_call_ollama)):
        response = client.post(
            "/api/chat",
            json={"query": "Help me fix this Python traceback in the gateway service", "voice_id": "alice"},
        )

    assert response.status_code == 200
    assert captured["use_chat"] is True
    assert captured["payload"]["model"] == "qwen2.5-coder:7b"
    assert captured["payload"]["messages"][0]["content"] == CODE_HELPER_SYSTEM_INSTRUCTION
    assert "CODE CONTEXT:" in captured["payload"]["messages"][-1]["content"]
    assert "No live local Git workspace is attached to this gateway path." in captured["payload"]["messages"][-1]["content"]


@pytest.mark.local_only
def test_coding_query_bypasses_fast_path_even_when_intent_engine_misclassifies(client):
    captured = {}

    async def mock_call_ollama(payload, use_chat=True):
        captured["payload"] = payload
        return MockOllamaResponse("```python\npass\n```")

    async def passthrough_query(query, history):
        return query

    with patch("gateway.main.resolve_identity", new=AsyncMock(return_value={"user": "alice"})), \
         patch("gateway.main.get_history", new=AsyncMock(return_value=[])), \
         patch("gateway.main.fetch_ha_entities", new=AsyncMock(return_value=[])), \
         patch("gateway.main.contextualize_query", new=AsyncMock(side_effect=passthrough_query)), \
         patch("gateway.main.update_history", new=AsyncMock(return_value=None)), \
         patch("gateway.main.emit_log", new=AsyncMock(return_value=None)), \
         patch("gateway.main.engine.classify", return_value=("media_transport", 0.99)), \
         patch("gateway.orchestrator.call_ollama", new=AsyncMock(side_effect=mock_call_ollama)):
        response = client.post(
            "/api/chat",
            json={"query": "Fix this Python code bug in math_utils.py", "voice_id": "alice", "model": "qwen2.5-coder:7b"},
        )

    assert response.status_code == 200
    assert captured["payload"]["model"] == "qwen2.5-coder:7b"
    assert captured["payload"]["messages"][0]["content"] == CODE_HELPER_SYSTEM_INSTRUCTION


@pytest.mark.local_only
def test_chat_slow_path_respects_explicit_coding_model_for_plain_edit_prompts(client):
    captured = {}

    async def mock_call_ollama(payload, use_chat=True):
        captured["payload"] = payload
        captured["use_chat"] = use_chat
        return MockOllamaResponse('MESSAGE = "hello from SharedLLM"')

    async def passthrough_query(query, history):
        return query

    with patch("gateway.main.resolve_identity", new=AsyncMock(return_value={"user": "alice"})), \
         patch("gateway.main.get_history", new=AsyncMock(return_value=[])), \
         patch("gateway.main.fetch_ha_entities", new=AsyncMock(return_value=[])), \
         patch("gateway.main.contextualize_query", new=AsyncMock(side_effect=passthrough_query)), \
         patch("gateway.main.update_history", new=AsyncMock(return_value=None)), \
         patch("gateway.main.emit_log", new=AsyncMock(return_value=None)), \
         patch("gateway.main.engine.classify", return_value=("unknown", 0.10)), \
         patch("gateway.orchestrator.call_ollama", new=AsyncMock(side_effect=mock_call_ollama)):
        response = client.post(
            "/api/chat",
            json={
                "query": "Edit this file: greeting.py and change one string.",
                "voice_id": "alice",
                "model": "qwen2.5-coder:7b",
            },
        )

    assert response.status_code == 200
    assert captured["use_chat"] is True
    assert captured["payload"]["model"] == "qwen2.5-coder:7b"
    assert captured["payload"]["messages"][0]["content"] == CODE_HELPER_SYSTEM_INSTRUCTION
    assert "CODE CONTEXT:" in captured["payload"]["messages"][-1]["content"]


@pytest.mark.local_only
def test_chat_slow_path_uses_assistant_model_for_general_requests(client):
    captured = {}

    async def mock_call_ollama(payload, use_chat=True):
        captured["payload"] = payload
        captured["use_chat"] = use_chat
        return MockOllamaResponse("The weather looks clear.")

    async def passthrough_query(query, history):
        return query

    with patch("gateway.main.resolve_identity", new=AsyncMock(return_value={"user": "alice"})), \
         patch("gateway.main.get_history", new=AsyncMock(return_value=[])), \
         patch("gateway.main.fetch_ha_entities", new=AsyncMock(return_value=[])), \
         patch("gateway.main.contextualize_query", new=AsyncMock(side_effect=passthrough_query)), \
         patch("gateway.main.update_history", new=AsyncMock(return_value=None)), \
         patch("gateway.main.emit_log", new=AsyncMock(return_value=None)), \
         patch("gateway.main.engine.classify", return_value=("unknown", 0.10)), \
         patch("gateway.orchestrator.call_ollama", new=AsyncMock(side_effect=mock_call_ollama)):
        response = client.post(
            "/api/chat",
            json={"query": "What should I make for dinner?", "voice_id": "alice"},
        )

    assert response.status_code == 200
    assert captured["use_chat"] is True
    assert captured["payload"]["model"] == "qwen3:latest"
    assert captured["payload"]["messages"][0]["content"] == ASSIST_SYSTEM_INSTRUCTION
    assert captured["payload"]["messages"][-1]["content"].startswith("CONTEXT:\n")


@pytest.mark.local_only
@pytest.mark.asyncio
async def test_chat_workspace_readme_request_uses_workspace_runtime_and_coding_model():
    captured = {"requests": []}

    class FakeRuntimeResponse:
        def __init__(self, payload):
            self.status_code = 200
            self._payload = payload
            self.text = ""

        def json(self):
            return self._payload

    async def fake_request(method, url, json_payload=None, params=None, headers=None, timeout=None):
        captured["requests"].append({"method": method, "url": url, "json": json_payload, "params": params})
        if url.endswith("/workspaces"):
            return FakeRuntimeResponse(
                {
                    "status": "SUCCESS",
                    "workspaces": [
                        {
                            "id": "sharedllm",
                            "available": True,
                            "scope": "user",
                            "resolved_path": "/workspace/SharedLLM",
                        }
                    ],
                }
            )
        if url.endswith("/files/list"):
            return FakeRuntimeResponse(
                {
                    "status": "SUCCESS",
                    "entries": [
                        {"path": "README.md", "name": "README.md", "is_dir": False},
                        {"path": "services", "name": "services", "is_dir": True},
                    ],
                }
            )
        if url.endswith("/files/read"):
            relative_path = (json_payload or {}).get("relative_path", "")
            return FakeRuntimeResponse(
                {
                    "status": "SUCCESS",
                    "content": f"content from {relative_path}",
                }
            )
        if url.endswith("/git/status"):
            return FakeRuntimeResponse(
                {
                    "status": "SUCCESS",
                    "branch": "microservices",
                    "porcelain": [],
                }
            )
        if url.endswith("/files/write"):
            return FakeRuntimeResponse({"status": "SUCCESS", "relative_path": (json_payload or {}).get("relative_path", "")})
        if url.endswith("/provider/sync/file"):
            return FakeRuntimeResponse({"status": "SUCCESS", "provider_path": "/Code/SharedLLM/temp/README.md"})
        return FakeRuntimeResponse({"status": "SUCCESS"})

    async def fake_post(*args, **kwargs):
        return FakeRuntimeResponse({"status": "SUCCESS"})

    async def fake_get(*args, **kwargs):
        return FakeRuntimeResponse({"status": "SUCCESS"})

    async def mock_call_ollama(payload, use_chat=True):
        captured["ollama_payload"] = payload
        return MockOllamaResponse("# Generated README\n")

    async def passthrough_query(query, history):
        return query

    with patch("gateway.main.resolve_identity", new=AsyncMock(return_value={"user": "alice"})), \
         patch("gateway.main.get_history", new=AsyncMock(return_value=[])), \
         patch("gateway.main.fetch_ha_entities", new=AsyncMock(return_value=[])), \
         patch("gateway.main.contextualize_query", new=AsyncMock(side_effect=passthrough_query)), \
         patch("gateway.main.update_history", new=AsyncMock(return_value=None)), \
         patch("gateway.main.emit_log", new=AsyncMock(return_value=None)), \
         patch("gateway.orchestrator.call_ollama", new=AsyncMock(side_effect=mock_call_ollama)), \
         patch.object(gateway_main, "_global_http_client", SimpleNamespace(request=fake_request, post=fake_post, get=fake_get)):
        response = await gateway_main.generate_workspace_readme_via_coding_model(
            body={
                "query": "Analyze this git repo and generate a README.md in temp for the workspace.",
                "voice_id": "alice",
            },
            user_id="alice",
            refined_query="Analyze this git repo and generate a README.md in temp for the workspace.",
            selected_model="qwen2.5-coder:7b",
            should_stream=False,
            is_openai=False,
        )

    assert isinstance(response, dict), f"Expected dict, got {type(response).__name__}"
    assert response["model"] == "qwen2.5-coder:7b"
    assert response["message"]["content"].startswith("I generated temp/README.md")
    assert "# Generated README" in response["message"]["content"]
    assert captured["ollama_payload"]["model"] == "qwen2.5-coder:7b"
    request_urls = [item["url"] for item in captured["requests"]]
    assert any(url.endswith("/files/list") for url in request_urls)
    assert any(url.endswith("/files/write") for url in request_urls)
    assert any(url.endswith("/provider/sync/file") for url in request_urls)


def test_select_system_instruction_for_query_uses_code_helper_prompt_for_coding_queries():
    instruction = select_system_instruction_for_query(
        "Help me fix this Python traceback in the gateway service",
        "qwen2.5-coder:7b",
    )
    assert instruction == CODE_HELPER_SYSTEM_INSTRUCTION


def test_select_system_instruction_for_query_uses_assist_prompt_for_general_queries():
    instruction = select_system_instruction_for_query(
        "What should I make for dinner?",
        "qwen3:latest",
    )
    assert instruction == ASSIST_SYSTEM_INSTRUCTION


def test_select_system_instruction_for_query_keeps_librarian_alias_for_general_queries():
    instruction = select_system_instruction_for_query(
        "What should I make for dinner?",
        "qwen3:latest",
    )
    assert instruction == ASSIST_SYSTEM_INSTRUCTION
    assert LIBRARIAN_SYSTEM_INSTRUCTION == ASSIST_SYSTEM_INSTRUCTION


def test_select_system_instruction_for_query_uses_raven_prompt_for_explicit_repair_queries():
    instruction = select_system_instruction_for_query(
        "Use Raven to self repair the gateway service",
        "qwen2.5-coder:7b",
    )
    assert instruction == RAVEN_AUTONOMOUS_PROTOCOL


def test_gateway_top_level_import_loads_prompts():
    gateway_dir = Path(__file__).resolve().parents[1] / "gateway"
    services_dir = gateway_dir.parent
    env = os.environ.copy()
    env.setdefault("INTERNAL_SECRET", "test-secret")
    env["PYTHONPATH"] = str(services_dir)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import gateway.main as m; assert hasattr(m, 'ASSIST_SYSTEM_INSTRUCTION'); "
            "assert m.ASSIST_SYSTEM_INSTRUCTION",
        ],
        cwd=gateway_dir,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
