import json
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch

import httpx

os.environ.setdefault("INTERNAL_SECRET", "test-secret")
os.environ.setdefault("FAST_PATH_THRESHOLD", "0.85")
os.environ.setdefault("IDENTITY_SVC_URL", "http://identity")
os.environ.setdefault("EXECUTION_SVC_URL", "http://execution")
os.environ.setdefault("OLLAMA_URL", "http://ollama")

from sqlmodel import Session, create_engine, select

from services.identity.models import GlobalSetting


def get_test_settings():
    db_path = "/data/identity.db"
    if not os.path.exists(db_path):
        _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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
            import httpx
            with httpx.Client(timeout=5.0) as client:
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

    return settings

try:
    TEST_SETTINGS = get_test_settings()
except Exception:
    TEST_SETTINGS = {}

ASSISTANT_MODEL = TEST_SETTINGS.get("assistant_model") or "test-assistant-model"
CODING_MODEL = TEST_SETTINGS.get("coding_model") or "test-coding-model"
LIBRARIAN_MODEL = TEST_SETTINGS.get("librarian_model") or "test-librarian-model"

os.environ["ASSISTANT_MODEL"] = ASSISTANT_MODEL
os.environ["CODING_MODEL"] = CODING_MODEL
os.environ["LIBRARIAN_MODEL"] = LIBRARIAN_MODEL

# Live gateway detection for integration tests
LIVE_GATEWAY_ENDPOINTS = [
    "http://192.168.2.205:11435",
    "http://ai.local:11435",
    "http://192.168.2.200:11435",
    "http://localhost:11435",
    "http://127.0.0.1:11435",
]
LIVE_GATEWAY_URL = ""
for ep in LIVE_GATEWAY_ENDPOINTS:
    try:
        resp = httpx.get(f"{ep}/api/tags", timeout=1.0)
        if resp.status_code == 200:
            LIVE_GATEWAY_URL = ep
            break
    except Exception:
        continue

LIVE_GATEWAY_AVAILABLE = bool(LIVE_GATEWAY_URL)

import pytest
from dotenv import dotenv_values
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import Response as StarletteResponse

import services.gateway.main as gateway_main
import services.gateway.orchestrator as gateway_orchestrator
from services.gateway.main import app, select_model_for_query, select_system_instruction_for_query
from services.gateway.prompts import (
    PROMPT_ASSISTANT_SYSTEM_INSTRUCTION,
    PROMPT_CODE_HELPER_SYSTEM_INSTRUCTION,
    PROMPT_LIBRARIAN_SYSTEM_INSTRUCTION,
    PROMPT_RAVEN_AUTONOMOUS_PROTOCOL,
)

# Read .env directly for test values (runtime never reads .env)
_env = dotenv_values(Path(__file__).resolve().parent.parent.parent / ".env")
ASSIST_SYSTEM_INSTRUCTION = _env.get(f"PROMPT_{PROMPT_ASSISTANT_SYSTEM_INSTRUCTION}") or "Test Assistant System Instruction"
CODE_HELPER_SYSTEM_INSTRUCTION = _env.get(f"PROMPT_{PROMPT_CODE_HELPER_SYSTEM_INSTRUCTION}") or "Test Code Helper System Instruction"
LIBRARIAN_SYSTEM_INSTRUCTION = _env.get(f"PROMPT_{PROMPT_LIBRARIAN_SYSTEM_INSTRUCTION}") or ASSIST_SYSTEM_INSTRUCTION
RAVEN_AUTONOMOUS_PROTOCOL = _env.get(f"PROMPT_{PROMPT_RAVEN_AUTONOMOUS_PROTOCOL}") or "Test Raven Autonomous Protocol"


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
    monkeypatch.setattr(gateway_main, "get_http_client", lambda: SimpleNamespace(request=fake_request, post=fake_post, get=fake_get))
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
    monkeypatch.setattr(gateway_main, "get_coding_model", AsyncMock(return_value=CODING_MODEL))
    monkeypatch.setattr(gateway_main, "get_assistant_model", AsyncMock(return_value=ASSISTANT_MODEL))
    monkeypatch.setattr(gateway_main, "get_librarian_model", AsyncMock(return_value=ASSISTANT_MODEL))
    assert await select_model_for_query(query) == CODING_MODEL


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
    monkeypatch.setattr(gateway_main, "get_coding_model", AsyncMock(return_value=CODING_MODEL))
    monkeypatch.setattr(gateway_main, "get_assistant_model", AsyncMock(return_value=ASSISTANT_MODEL))
    monkeypatch.setattr(gateway_main, "get_librarian_model", AsyncMock(return_value=ASSISTANT_MODEL))
    assert await select_model_for_query(query) == ASSISTANT_MODEL


def test_select_system_instruction_handles_standalone_main_import(monkeypatch):
    original_package = gateway_main.__package__
    monkeypatch.setattr(gateway_main, "__package__", None)
    monkeypatch.setattr(gateway_main, "load_prompt_sync", lambda x: "test-prompt")
    try:
        select_system_instruction_for_query(
            "Please analyze logs and self repair this service",
            CODING_MODEL,
        )
    finally:
        monkeypatch.setattr(gateway_main, "__package__", original_package)


@pytest.mark.asyncio
async def test_chat_handler_routes_direct_file_edit_requests_to_code_orchestration(monkeypatch):
    monkeypatch.setattr(gateway_main, "resolve_identity", AsyncMock(return_value={"user": "alice"}))
    monkeypatch.setattr(gateway_main, "update_history", AsyncMock(return_value=None))
    monkeypatch.setattr(gateway_main, "get_coding_model", AsyncMock(return_value=CODING_MODEL))
    monkeypatch.setattr(gateway_main, "get_assistant_model", AsyncMock(return_value=ASSISTANT_MODEL))
    mock_orchestrate = AsyncMock(return_value=gateway_main._make_ollama_response("orchestrated", CODING_MODEL))
    monkeypatch.setattr(gateway_main, "orchestrate_code_change", mock_orchestrate)
    mock_jq = AsyncMock()
    mock_jq.enqueue_job = AsyncMock(return_value="test-job-id")
    monkeypatch.setattr(gateway_main, "job_queue", mock_jq)

    response = await gateway_main.chat_handler(
        _json_request(
            {
                "query": "Create a pytest file named temp/test_demo.py that asserts 2 + 2 == 4. Verify it with pytest.",
                "rag_user": "alice",
                "model": CODING_MODEL,
            }
        )
    )

    resp = cast(StarletteResponse, response)
    assert resp.status_code == 200
    assert "orchestrated" in json.loads(resp.body if isinstance(resp.body, bytes) else resp.body.tobytes())["message"]["content"]
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
            status=200,
            json=AsyncMock(return_value={"status": "SUCCESS", "workspace": {"id": "alice-demo"}}),
            text=AsyncMock(return_value=""),
        )

    mock_client = SimpleNamespace(request=fake_request)
    monkeypatch.setattr(gateway_main, "get_http_client", lambda: mock_client)
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

    resp = cast(StarletteResponse, response)
    payload = json.loads(resp.body if isinstance(resp.body, bytes) else resp.body.tobytes())
    assert resp.status_code == 200
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
            status=200,
            json=AsyncMock(return_value={"status": "SUCCESS", "exit_code": 0}),
            text=AsyncMock(return_value=""),
        )

    mock_client = SimpleNamespace(request=fake_request)
    monkeypatch.setattr(gateway_main, "get_http_client", lambda: mock_client)
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

    resp = cast(StarletteResponse, response)
    payload = json.loads(resp.body if isinstance(resp.body, bytes) else resp.body.tobytes())
    assert resp.status_code == 200
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
    monkeypatch.setattr(gateway_main, "load_prompt", AsyncMock(return_value="You are a helpful coding assistant."))

    response = await gateway_main.orchestrate_code_change(
        body={},
        user_id="alice",
        refined_query="Add a targeted pytest for the sample module",
        selected_model=CODING_MODEL,
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

    resp = cast(StarletteResponse, response)
    payload = json.loads(resp.body if isinstance(resp.body, bytes) else resp.body.tobytes())
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
    monkeypatch.setattr(gateway_main, "load_prompt", AsyncMock(return_value="You are a helpful coding assistant."))

    response = await gateway_main.orchestrate_code_change(
        body={},
        user_id="alice",
        refined_query="Create a pytest file named temp/test_raven_live.py that asserts 2 + 2 == 4. Verify it with pytest.",
        selected_model="qwen3.6-35b-a3b:q4_k_m",
        should_stream=False,
        is_openai=False,
    )

    resp = cast(StarletteResponse, response)
    payload = json.loads(resp.body if isinstance(resp.body, bytes) else resp.body.tobytes())
    assert resp.status_code == 200
    assert "temp/test_raven_live.py" in payload["message"]["content"]


@pytest.mark.asyncio
async def test_single_turn_inference_supports_capability_index_tool(monkeypatch):
    captured = {}

    class FakeAsyncClient:
        def __init__(self, timeout=None, connector=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None, headers=None, **kwargs):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return SimpleNamespace(
                status=200,
                text=AsyncMock(return_value=""),
                json=AsyncMock(return_value={"message": "Capability index refreshed."}),
            )

        async def get(self, url, **kwargs):
            return SimpleNamespace(
                status=200,
                text=AsyncMock(return_value=""),
                json=AsyncMock(return_value=[]),
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
    monkeypatch.setattr(gateway_orchestrator, "load_prompt_sync", lambda x: "test-single-turn-guide")
    monkeypatch.setattr(gateway_main, "shared_http_client", lambda: FakeAsyncClient())

    creds = gateway_main.ResolvedCredentials(user="alice")
    result = await gateway_orchestrator._single_turn_inference(
        query="Refresh your capability index.",
        model=ASSISTANT_MODEL,
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
    monkeypatch.setattr(gateway_orchestrator, "load_prompt_sync", lambda x: "Test Assistant System Instruction\n\nSystem Capability Context:\nClimateRequest\nExecutionLogRequest")

    creds = gateway_main.ResolvedCredentials(user="alice")
    result = await gateway_orchestrator._single_turn_inference(
        query="What is the temperature upstairs?",
        model=ASSISTANT_MODEL,
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


@pytest.mark.skipif(not LIVE_GATEWAY_AVAILABLE, reason="No live gateway endpoint detected")
def test_chat_slow_path_uses_coding_model_for_code_requests():
    with httpx.Client(base_url=LIVE_GATEWAY_URL, timeout=30.0) as c:
        resp = c.post(
            "/api/chat",
            json={"query": "Help me fix this Python traceback in the gateway service", "voice_id": "alice"},
        )
    assert resp.status_code == 200, f"Gateway returned {resp.status_code}: {resp.text[:500]}"
    data = resp.json()
    assert data["model"] == CODING_MODEL, f"Expected model {CODING_MODEL}, got {data.get('model')}"


@pytest.mark.skipif(not LIVE_GATEWAY_AVAILABLE, reason="No live gateway endpoint detected")
def test_coding_query_bypasses_fast_path_even_when_intent_engine_misclassifies():
    with httpx.Client(base_url=LIVE_GATEWAY_URL, timeout=30.0) as c:
        resp = c.post(
            "/api/chat",
            json={"query": "Fix this Python code bug in math_utils.py", "voice_id": "alice", "model": CODING_MODEL},
        )
    assert resp.status_code == 200, f"Gateway returned {resp.status_code}: {resp.text[:500]}"
    data = resp.json()
    assert data["model"] == CODING_MODEL, f"Expected model {CODING_MODEL}, got {data.get('model')}"


@pytest.mark.skipif(not LIVE_GATEWAY_AVAILABLE, reason="No live gateway endpoint detected")
def test_chat_slow_path_respects_explicit_coding_model_for_plain_edit_prompts():
    with httpx.Client(base_url=LIVE_GATEWAY_URL, timeout=30.0) as c:
        resp = c.post(
            "/api/chat",
            json={
                "query": "Edit this file: greeting.py and change one string.",
                "voice_id": "alice",
                "model": CODING_MODEL,
            },
        )
    assert resp.status_code == 200, f"Gateway returned {resp.status_code}: {resp.text[:500]}"
    data = resp.json()
    assert data["model"] == CODING_MODEL, f"Expected model {CODING_MODEL}, got {data.get('model')}"


@pytest.mark.skipif(not LIVE_GATEWAY_AVAILABLE, reason="No live gateway endpoint detected")
def test_chat_slow_path_uses_assistant_model_for_general_requests():
    with httpx.Client(base_url=LIVE_GATEWAY_URL, timeout=30.0) as c:
        resp = c.post(
            "/api/chat",
            json={"query": "What should I make for dinner?", "voice_id": "alice"},
        )
    assert resp.status_code == 200, f"Gateway returned {resp.status_code}: {resp.text[:500]}"
    data = resp.json()
    assert data["model"] == ASSISTANT_MODEL, f"Expected model {ASSISTANT_MODEL}, got {data.get('model')}"


@pytest.mark.skipif(not LIVE_GATEWAY_AVAILABLE, reason="No live gateway endpoint detected")
def test_chat_workspace_readme_request_uses_coding_model():
    with httpx.Client(base_url=LIVE_GATEWAY_URL, timeout=60.0) as c:
        resp = c.post(
            "/api/chat",
            json={
                "query": "Analyze this git repo and generate a README.md in temp for the workspace.",
                "voice_id": "alice",
            },
        )
    assert resp.status_code == 200, f"Gateway returned {resp.status_code}: {resp.text[:500]}"
    data = resp.json()
    assert data["model"] == CODING_MODEL, f"Expected model {CODING_MODEL} for workspace readme request, got {data.get('model')}"


def test_select_system_instruction_for_query_uses_code_helper_prompt_for_coding_queries():
    with patch("services.gateway.main.load_prompt_sync", return_value=CODE_HELPER_SYSTEM_INSTRUCTION):
        instruction = select_system_instruction_for_query(
            "Help me fix this Python traceback in the gateway service",
            CODING_MODEL,
        )
    assert instruction == CODE_HELPER_SYSTEM_INSTRUCTION


def test_select_system_instruction_for_query_uses_assist_prompt_for_general_queries():
    with patch("services.gateway.main.load_prompt_sync", return_value=ASSIST_SYSTEM_INSTRUCTION):
        instruction = select_system_instruction_for_query(
            "What should I make for dinner?",
            ASSISTANT_MODEL,
        )
    assert instruction == ASSIST_SYSTEM_INSTRUCTION


def test_select_system_instruction_for_query_keeps_librarian_alias_for_general_queries():
    with patch("services.gateway.main.load_prompt_sync", return_value=ASSIST_SYSTEM_INSTRUCTION):
        instruction = select_system_instruction_for_query(
            "What should I make for dinner?",
            ASSISTANT_MODEL,
        )
    assert instruction == ASSIST_SYSTEM_INSTRUCTION
    assert LIBRARIAN_SYSTEM_INSTRUCTION == ASSIST_SYSTEM_INSTRUCTION


def test_select_system_instruction_for_query_uses_raven_prompt_for_explicit_repair_queries():
    with patch("services.gateway.main.load_prompt_sync", return_value=RAVEN_AUTONOMOUS_PROTOCOL):
        instruction = select_system_instruction_for_query(
            "Use Raven to self repair the gateway service",
            CODING_MODEL,
        )
    assert instruction == RAVEN_AUTONOMOUS_PROTOCOL


def test_gateway_top_level_import_loads_prompts():
    gateway_dir = Path(__file__).resolve().parents[1] / "gateway"
    services_dir = gateway_dir.parent
    project_root = services_dir.parent
    env = os.environ.copy()
    env.setdefault("INTERNAL_SECRET", "test-secret")
    env["PYTHONPATH"] = str(project_root)

    # Check that the gateway main has the select_system_instruction_for_query function
    # and that prompts module has the load_prompt_sync function
    # Note: Prompts are loaded dynamically at runtime, not as module-level variables
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from services.gateway.main import select_system_instruction_for_query; "
            "from services.gateway.prompts import load_prompt_sync; "
            "assert callable(select_system_instruction_for_query); "
            "assert callable(load_prompt_sync)",
        ],
        cwd=gateway_dir,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
