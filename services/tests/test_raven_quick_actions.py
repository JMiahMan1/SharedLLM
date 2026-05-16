"""
Tests for Raven Quick Action mission templates.

Each Quick Action in the UI (JarvisLab.tsx) maps to a mission query.
These tests verify that:
1. The mission query is properly routed to the Raven autonomous protocol
2. The correct model (coding) is selected
3. The system prompt includes the right tool guides
4. The expected tool calls are extractable from the LLM response format
"""

import os
import json
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

os.environ.setdefault("INTERNAL_SECRET", "test-secret")
os.environ.setdefault("OLLAMA_URL", "http://localhost:11434")
os.environ.setdefault("IDENTITY_SVC_URL", "http://identity:8001")
os.environ.setdefault("EXECUTION_SVC_URL", "http://execution:8003")
os.environ.setdefault("RAG_SVC_URL", "http://rag:8004")
os.environ.setdefault("STORAGE_SVC_URL", "http://storage:8005")
os.environ.setdefault("LOGGING_SVC_URL", "http://logging:8006")
os.environ.setdefault("WORKSPACE_RUNTIME_SVC_URL", "http://workspace_runtime:8007")
os.environ.setdefault("CONTROL_PLANE_URL", "http://control_plane:8008")
os.environ.setdefault("SEARXNG_URL", "http://searxng:8080")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from gateway.prompts import RAVEN_AUTONOMOUS_PROTOCOL, AUTONOMOUS_EVOLUTION_AGENT_PROMPT, CODE_HELPER_SYSTEM_INSTRUCTION
from gateway.main import select_model_for_query, select_system_instruction_for_query, AUTONOMOUS_SIGNALS
from gateway.agent_loop import extract_action_json


MISSION_TEMPLATES = [
    {
        "label": "Audit Codebase",
        "query": "Audit the codebase for lint errors, unused imports, and code quality issues. Fix all findings.",
        "expected_tools": ["workspacelintrequest", "workspacefilepatchrequest", "workspacefilewriterequest"],
    },
    {
        "label": "Sync Workspaces",
        "query": "Check workspace status, pull latest from remote, and report any conflicts.",
        "expected_tools": ["gitoperationrequest"],
    },
    {
        "label": "Convert Files",
        "query": "Find all PNG images in the Assets workspace and convert them to WebP format.",
        "expected_tools": ["workspacesearchrequest", "workspaceshellrequest"],
    },
    {
        "label": "Check Dependencies",
        "query": "Review requirements.txt and package.json for outdated or vulnerable dependencies.",
        "expected_tools": ["workspacefilereadrequest", "websearchrequest"],
    },
]


def _patch_model_selection():
    return patch("gateway.main.get_coding_model", new=AsyncMock(return_value="qwen2.5-coder:7b"))


@pytest.mark.asyncio
async def test_audit_codebase_routes_to_raven():
    template = MISSION_TEMPLATES[0]
    with _patch_model_selection():
        model = await select_model_for_query(template["query"])
        prompt = select_system_instruction_for_query(template["query"], model)
        assert model == "qwen2.5-coder:7b"
        assert prompt == RAVEN_AUTONOMOUS_PROTOCOL


@pytest.mark.asyncio
async def test_sync_workspaces_routes_to_raven():
    template = MISSION_TEMPLATES[1]
    with _patch_model_selection():
        model = await select_model_for_query(template["query"])
        prompt = select_system_instruction_for_query(template["query"], model)
        assert model == "qwen2.5-coder:7b"
        assert prompt == RAVEN_AUTONOMOUS_PROTOCOL


@pytest.mark.asyncio
async def test_convert_files_routes_to_raven():
    template = MISSION_TEMPLATES[2]
    with _patch_model_selection():
        model = await select_model_for_query(template["query"])
        prompt = select_system_instruction_for_query(template["query"], model)
        assert model == "qwen2.5-coder:7b"
        assert prompt == RAVEN_AUTONOMOUS_PROTOCOL


@pytest.mark.asyncio
async def test_check_dependencies_routes_to_raven():
    template = MISSION_TEMPLATES[3]
    with _patch_model_selection():
        model = await select_model_for_query(template["query"])
        prompt = select_system_instruction_for_query(template["query"], model)
        assert model == "qwen2.5-coder:7b"
        assert prompt == RAVEN_AUTONOMOUS_PROTOCOL


def test_extract_git_action_from_sync_query():
    simulated_response = '''
Here is the git status:
```json
{
  "action": "GitOperationRequest",
  "payload": {
    "action": "status",
    "path": "."
  }
}
```
'''
    tool_data = extract_action_json(simulated_response)
    assert tool_data is not None
    assert tool_data.get("action", "").lower() == "gitoperationrequest"
    payload = tool_data.get("payload", {})
    assert payload.get("action") == "status"


def test_extract_lint_action_from_audit_query():
    simulated_response = '''
I will start by linting the gateway service:
{"action": "WorkspaceLintRequest", "payload": {"path": "services/gateway"}}
'''
    tool_data = extract_action_json(simulated_response)
    assert tool_data is not None
    assert tool_data.get("action", "").lower() == "workspacelintrequest"


def test_raven_prompt_includes_git_tool_guide():
    assert "GitOperationRequest" in RAVEN_AUTONOMOUS_PROTOCOL
    assert "WorkspaceFilePatchRequest" in RAVEN_AUTONOMOUS_PROTOCOL
    assert "WorkspaceSearchRequest" in RAVEN_AUTONOMOUS_PROTOCOL


def test_raven_prompt_includes_shell_command():
    assert "WorkspaceShellRequest" in RAVEN_AUTONOMOUS_PROTOCOL


def test_all_templates_contain_autonomy_signals():
    for template in MISSION_TEMPLATES:
        query_lower = template["query"].lower()
        has_signal = any(signal in query_lower for signal in AUTONOMOUS_SIGNALS)
        assert has_signal, f"Quick Action '{template['label']}' lacks autonomy signal in: {template['query']}"
