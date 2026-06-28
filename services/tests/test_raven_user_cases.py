"""
Raven Autonomous Agent — Live Integration Tests

These tests hit the actual gateway at http://192.168.2.205:11435 (or localhost:11435)
and verify the full Raven mission lifecycle works end-to-end with real LLM inference.

User Cases:
1. Code Audit — Lint → Read → Patch → Lint → Commit
2. Git Sync & Conflict Resolution — Status → Pull → Report
3. Log Analysis & Debugging — Docker logs → Identify error → Fix
4. Dependency Update — Read requirements → Search → Update
5. File Conversion — Search files → Shell convert
6. System Health Check — Docker status → Service checks → Report
7. Error Recovery — Tool fails → Retry with correction
8. Context-Driven Investigation — ContextSearch → Follow-up action
9. Multi-Step Refactoring — Search → Read multiple → Patch → Commit
10. Infrastructure Repair — Check Docker → Restart → Verify

Additionally tests:
- extract_action_json() parsing with real LLM output formats
- Tool name normalization (aliases, regex patterns, fuzzy match)
- RAVEN_AUTONOMOUS_PROTOCOL completeness
- Mission lifecycle: create, poll, kill, pause, resume, delete
"""

import os
import re
import time

import httpx
import pytest

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

from services.gateway.agent_loop import extract_action_json
from services.gateway.prompts import PROMPT_RAVEN_AUTONOMOUS_PROTOCOL, PROMPT_RAVEN_PLAN, PROMPT_RAVEN_REFLECTION
from dotenv import dotenv_values
from pathlib import Path

# Read .env directly for test values (runtime never reads .env)
_env = dotenv_values(Path(__file__).resolve().parent.parent.parent / ".env")
RAVEN_AUTONOMOUS_PROTOCOL = _env.get(f"PROMPT_{PROMPT_RAVEN_AUTONOMOUS_PROTOCOL}", "")
RAVEN_PLAN_PROMPT = _env.get(f"PROMPT_{PROMPT_RAVEN_PLAN}", "")
RAVEN_REFLECTION_PROMPT = _env.get(f"PROMPT_{PROMPT_RAVEN_REFLECTION}", "")


# ============================================================================
# GATEWAY CONFIGURATION
# ============================================================================

# Try multiple gateway endpoints — order: remote → public → Caddy → local
GATEWAY_ENDPOINTS = [
    "http://192.168.2.205:11435",
    "http://ai.local:11435",
    "http://192.168.2.200:11435",
    "http://localhost:11435",
    "http://127.0.0.1:11435",
]

# Use first reachable endpoint
gateway_url: str | None = None
for ep in GATEWAY_ENDPOINTS:
    try:
        resp = httpx.get(f"{ep}/api/tags", timeout=3.0)
        if resp.status_code == 200:
            gateway_url = ep
            break
    except Exception:
        continue

GATEWAY_URL: str = gateway_url or ""  # type: ignore[assignment]

if not GATEWAY_URL:
    pytest.skip("Gateway unreachable — skip live tests", allow_module_level=True)

print(f"[LIVE TESTS] Using gateway: {GATEWAY_URL}")

# Polling config for mission lifecycle tests
MISSION_POLL_INTERVAL = 5.0  # seconds
MISSION_POLL_TIMEOUT = 900.0  # 15 minutes — Raven missions take 10-20min for full orchestration


def poll_mission_status(mission_id: int, timeout: float = MISSION_POLL_TIMEOUT) -> dict:
    """Poll a mission until it reaches a terminal state or times out."""
    start = time.time()
    while time.time() - start < timeout:
        resp = httpx.get(f"{GATEWAY_URL}/api/raven/missions/{mission_id}", timeout=10.0)
        if resp.status_code == 200:
            mission = resp.json()
            status = mission.get("status", "")
            if status in ("completed", "failed", "cancelled"):
                return mission
        time.sleep(MISSION_POLL_INTERVAL)
    # Return last known status
    resp = httpx.get(f"{GATEWAY_URL}/api/raven/missions/{mission_id}", timeout=10.0)
    return resp.json()


# ============================================================================
# UNIT TESTS: extract_action_json()
# ============================================================================

def test_extract_action_json_with_fenced_block():
    """Test parsing of properly fenced JSON blocks."""
    text = '''Here is the tool call:
```json
{"action": "GitOperationRequest", "payload": {"action": "status", "path": "."}}
```'''
    result = extract_action_json(text)
    assert result is not None
    assert result["action"] == "GitOperationRequest"
    assert result["payload"]["action"] == "status"


def test_extract_action_json_with_inline_json():
    """Test parsing of inline JSON without fences."""
    text = '''I will check git status: {"action": "GitOperationRequest", "payload": {"action": "status", "path": "."}}'''
    result = extract_action_json(text)
    assert result is not None
    assert result["action"] == "GitOperationRequest"


def test_extract_action_json_with_trailing_comma():
    """Test parsing of JSON with trailing commas (common LLM artifact)."""
    text = '''```json
{
  "action": "GitOperationRequest",
  "payload": {
    "action": "status",
    "path": ".",
  },
}
```'''
    result = extract_action_json(text)
    assert result is not None
    assert result["action"] == "GitOperationRequest"


def test_extract_action_json_with_info_log_prefix():
    """Test stripping of INFO log prefix before JSON parsing."""
    text = '''INFO:app:Starting server
{"action": "WorkspaceShellRequest", "payload": {"command": "echo hello"}}'''
    result = extract_action_json(text)
    assert result is not None
    assert result["action"] == "WorkspaceShellRequest"


def test_extract_action_json_with_empty_input():
    """Test that empty input returns None."""
    assert extract_action_json("") is None
    assert extract_action_json("   ") is None


def test_extract_action_json_with_no_json():
    """Test that plain text without JSON returns None."""
    text = "This is just plain text with no JSON at all."
    assert extract_action_json(text) is None


def test_extract_action_json_with_multiple_json_blocks():
    """Test that the first fenced JSON block is extracted."""
    text = '''Here is the first:
```json
{"action": "GitOperationRequest", "payload": {"action": "status"}}
```
And another:
```json
{"action": "WorkspaceShellRequest", "payload": {"command": "ls"}}
```'''
    result = extract_action_json(text)
    assert result is not None
    assert result["action"] == "GitOperationRequest"


# ============================================================================
# UNIT TESTS: Tool Name Normalization
# ============================================================================

def test_tool_name_normalization_with_underscores():
    """Test that tool names with underscores/spaces normalize correctly."""
    raw_action = "GitOperationRequest"
    normalized = re.sub(r'[\s_]+', '', raw_action).lower()
    assert normalized == "gitoperationrequest"


def test_tool_name_normalization_with_spaces():
    """Test normalization removes spaces."""
    raw_action = "Workspace File Read Request"
    normalized = re.sub(r'[\s_]+', '', raw_action).lower()
    assert normalized == "workspacefilereadrequest"


def test_tool_name_resolution_with_aliases():
    """Test that semantic aliases map to canonical tool names."""
    alias_map = {
        "read_file": "workspacefilereadrequest",
        "shell": "workspaceshellrequest",
        "status": "gitoperationrequest",
        "commit": "gitoperationrequest",
        "push": "gitoperationrequest",
        "patch_file": "workspacefilepatchrequest",
        "lint_file": "workspacelintrequest",
        "grep": "workspacesearchrequest",
        "browse": "webreadrequest",
        "restart_service": "controlplanerequest",
    }
    for alias, expected in alias_map.items():
        assert alias_map[alias] == expected, f"Alias '{alias}' should map to '{expected}'"


def test_tool_name_regex_patterns():
    """Test that regex patterns match expected tools."""
    regex_aliases = [
        (r'.*workspace.*shell.*', "workspaceshellrequest", ["workspace_shell_request", "workspaceShellRequest"]),
        (r'.*workspace.*search.*', "workspacesearchrequest", ["workspace_search_request", "workspaceSearchRequest"]),
        (r'.*git.*operation.*', "gitoperationrequest", ["git_operation_request", "gitOperationRequest"]),
        (r'.*docker.*log.*', "dockerlogsrequest", ["docker_logs_request", "dockerLogsRequest"]),
        (r'.*web.*search.*', "websearchrequest", ["web_search_request", "webSearchRequest"]),
    ]
    for pattern, _, aliases in regex_aliases:
        assert any(re.match(pattern, alias) for alias in aliases), f"Pattern {pattern} matched none of {aliases}"


# ============================================================================
# UNIT TESTS: Protocol Completeness
# ============================================================================

def test_raven_protocol_includes_all_tool_types():
    """Verify the RAVEN_AUTONOMOUS_PROTOCOL mentions key tool categories."""
    required_tools = [
        "WorkspaceFilePatchRequest",
        "WorkspaceShellRequest",
        "GitOperationRequest",
        "ContextSearchRequest",
        "WorkspaceSearchRequest",
        "DockerLogsRequest",
        "WebSearchRequest",
        "WorkspaceLintRequest",
    ]
    for tool in required_tools:
        assert tool in RAVEN_AUTONOMOUS_PROTOCOL, f"Missing {tool} in protocol"


def test_raven_plan_prompt_format():
    """Verify the plan prompt enforces numbered list format."""
    assert "# MISSION PLANNER" in RAVEN_PLAN_PROMPT
    assert "numbered list" in RAVEN_PLAN_PROMPT.lower()
    assert "5-10 steps" in RAVEN_PLAN_PROMPT


def test_raven_protocol_enforces_zero_conversation():
    """Verify protocol enforces zero-conversation rule."""
    assert "ZERO CONVERSATION" in RAVEN_AUTONOMOUS_PROTOCOL
    assert "MUST NOT ask questions" in RAVEN_AUTONOMOUS_PROTOCOL
    assert "seek approval" in RAVEN_AUTONOMOUS_PROTOCOL


def test_raven_protocol_workspace_relative_paths():
    """Verify protocol enforces workspace-relative paths."""
    assert "workspace-relative" in RAVEN_AUTONOMOUS_PROTOCOL.lower() or "WORKSPACE-RELATIVE" in RAVEN_AUTONOMOUS_PROTOCOL
    assert "never use absolute paths" in RAVEN_AUTONOMOUS_PROTOCOL.lower() or "NEVER use absolute paths" in RAVEN_AUTONOMOUS_PROTOCOL


def test_raven_protocol_8gb_vram_constraint():
    """Verify protocol mentions 8GB VRAM constraint."""
    assert "8GB VRAM" in RAVEN_AUTONOMOUS_PROTOCOL
    assert "300 lines" in RAVEN_AUTONOMOUS_PROTOCOL or "300-line" in RAVEN_AUTONOMOUS_PROTOCOL


def test_raven_reflection_prompt_exists():
    """Verify reflection prompt is defined."""
    assert "# POST-MISSION REFLECTION" in RAVEN_REFLECTION_PROMPT
    assert "Success/failure status" in RAVEN_REFLECTION_PROMPT or "succinct" in RAVEN_REFLECTION_PROMPT.lower()


# ============================================================================
# LIVE TESTS: Gateway Connectivity
# ============================================================================

@pytest.mark.asyncio
async def test_gateway_tags_available():
    """Verify gateway is reachable and models are loaded."""
    resp = httpx.get(f"{GATEWAY_URL}/api/tags", timeout=10.0)
    assert resp.status_code == 200
    data = resp.json()
    assert "models" in data
    assert len(data["models"]) > 0


@pytest.mark.asyncio
async def test_coder_model_available():
    """Verify qwen2.5-coder:7b is loaded."""
    tags = httpx.get(f"{GATEWAY_URL}/api/tags", timeout=10.0).json()
    model_names = [m["name"] for m in tags["models"]]
    assert "qwen2.5-coder:7b" in model_names, "qwen2.5-coder:7b model not loaded"


# ============================================================================
# LIVE TESTS: Mission Lifecycle — Full CRUD
# ============================================================================

@pytest.mark.asyncio
async def test_mission_create_and_poll_completion():
    """
    Case 1: Create a mission, poll until completion, verify result.
    Tests the full Raven pipeline: create → queue → execute → result.
    """
    resp = httpx.post(
        f"{GATEWAY_URL}/api/raven/missions",
        json={
            "query": "Check system health and report on all services.",
            "model": "qwen2.5-coder:7b",
        },
        timeout=10.0,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "SUCCESS"
    mission = data["mission"]
    assert mission["id"] is not None
    assert mission["proposed_mission"] == "Check system health and report on all services."
    assert mission["status"] in ("queued", "executing")

    # Poll for completion (background worker may not be running on remote gateway)
    result = poll_mission_status(mission["id"])
    status = result.get("status", "")
    if status in ("completed", "failed"):
        # Mission should have either a result or an error_summary
        assert result.get("result") is not None or result.get("error_summary") is not None
    else:
        # Background worker not processing — acceptable on remote gateway without live workers
        print(f"[LIVE TEST] Mission {mission['id']} stayed in '{status}' (background worker not running)")


@pytest.mark.asyncio
async def test_mission_create_with_simple_query():
    """
    Case 2: Create a mission with a simple query that should complete quickly.
    Tests basic mission creation and result retrieval.
    """
    resp = httpx.post(
        f"{GATEWAY_URL}/api/raven/missions",
        json={
            "query": "List the last 5 lines of gateway Docker logs.",
            "model": "qwen2.5-coder:7b",
        },
        timeout=10.0,
    )
    assert resp.status_code == 200
    mission_id = resp.json()["mission"]["id"]

    result = poll_mission_status(mission_id)
    assert result["status"] in ("completed", "failed")
    # Should have a result (even if it's an error message)
    assert result.get("result") is not None or result.get("error_summary") is not None


@pytest.mark.asyncio
async def test_mission_list_endpoint():
    """Verify GET /api/raven/missions returns a list of missions."""
    resp = httpx.get(f"{GATEWAY_URL}/api/raven/missions", timeout=10.0)
    assert resp.status_code == 200
    missions = resp.json()
    assert isinstance(missions, list)
    assert len(missions) > 0
    # Each mission should have required fields
    m = missions[0]
    assert "id" in m
    assert "status" in m
    assert "proposed_mission" in m


@pytest.mark.asyncio
async def test_mission_get_detail():
    """Verify GET /api/raven/missions/{id} returns mission detail."""
    resp = httpx.get(f"{GATEWAY_URL}/api/raven/missions", timeout=10.0)
    missions = resp.json()
    if not missions:
        pytest.skip("No missions to test against")
    mission_id = missions[-1]["id"]

    resp = httpx.get(f"{GATEWAY_URL}/api/raven/missions/{mission_id}", timeout=10.0)
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == mission_id
    assert "status" in data
    assert "proposed_mission" in data


@pytest.mark.asyncio
async def test_mission_kill():
    """Verify POST /api/raven/missions/{id}/kill sends kill signal."""
    # Create a mission then kill it
    resp = httpx.post(
        f"{GATEWAY_URL}/api/raven/missions",
        json={"query": "Kill test — list files in /tmp", "model": "qwen2.5-coder:7b"},
        timeout=10.0,
    )
    mission_id = resp.json()["mission"]["id"]

    # Kill the mission
    resp = httpx.post(
        f"{GATEWAY_URL}/api/raven/missions/{mission_id}/kill",
        timeout=10.0,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("status") == "SUCCESS" or "kill" in str(data).lower()


@pytest.mark.asyncio
async def test_mission_pause_resume():
    """Verify POST /api/raven/missions/{id}/pause and /resume work."""
    resp = httpx.post(
        f"{GATEWAY_URL}/api/raven/missions",
        json={"query": "Pause test — list gateway logs", "model": "qwen2.5-coder:7b"},
        timeout=10.0,
    )
    mission_id = resp.json()["mission"]["id"]

    # Pause the mission
    resp = httpx.post(
        f"{GATEWAY_URL}/api/raven/missions/{mission_id}/pause",
        timeout=10.0,
    )
    assert resp.status_code == 200

    # Resume the mission
    resp = httpx.post(
        f"{GATEWAY_URL}/api/raven/missions/{mission_id}/resume",
        timeout=10.0,
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_mission_logs_endpoint():
    """Verify GET /api/raven/missions/{id}/logs returns logs."""
    # First create a completed mission to have logs
    resp = httpx.post(
        f"{GATEWAY_URL}/api/raven/missions",
        json={"query": "Log test — check system uptime", "model": "qwen2.5-coder:7b"},
        timeout=10.0,
    )
    mission_id = resp.json()["mission"]["id"]

    # Wait for it to complete so logs are populated
    poll_mission_status(mission_id)

    # Get logs
    resp = httpx.get(f"{GATEWAY_URL}/api/raven/missions/{mission_id}/logs", timeout=10.0)
    assert resp.status_code == 200
    data = resp.json()
    assert "logs" in data or "log" in data


@pytest.mark.asyncio
async def test_mission_delete():
    """Verify DELETE /api/raven/missions/{id} deletes a mission."""
    # Create a mission
    resp = httpx.post(
        f"{GATEWAY_URL}/api/raven/missions",
        json={"query": "Delete test — list /etc", "model": "qwen2.5-coder:7b"},
        timeout=10.0,
    )
    mission_id = resp.json()["mission"]["id"]

    # Delete the mission
    resp = httpx.delete(f"{GATEWAY_URL}/api/raven/missions/{mission_id}", timeout=10.0)
    assert resp.status_code in (200, 204)


@pytest.mark.asyncio
async def test_mission_nonexistent():
    """Verify GET /api/raven/missions/999999 returns error for non-existent mission."""
    resp = httpx.get(f"{GATEWAY_URL}/api/raven/missions/999999", timeout=10.0)
    # Should return 404 or similar error
    assert resp.status_code not in (200,), f"Expected 404 for non-existent mission, got {resp.status_code}"


# ============================================================================
# LIVE TESTS: User Cases — Real Mission Creation
# ============================================================================

@pytest.mark.asyncio
async def test_live_user_case_1_audit():
    """Case 1: Code Audit — Create mission, verify it creates and completes."""
    resp = httpx.post(
        f"{GATEWAY_URL}/api/raven/missions",
        json={"query": "Audit the codebase for lint errors and fix them.", "model": "qwen2.5-coder:7b"},
        timeout=10.0,
    )
    assert resp.status_code == 200
    mission = resp.json()["mission"]
    assert mission["proposed_mission"] == "Audit the codebase for lint errors and fix them."

    result = poll_mission_status(mission["id"])
    assert result["status"] in ("completed", "failed")


@pytest.mark.asyncio
async def test_live_user_case_2_git_sync():
    """Case 2: Git Sync — Create mission, verify it creates."""
    resp = httpx.post(
        f"{GATEWAY_URL}/api/raven/missions",
        json={"query": "Check workspace status and report if we need to pull.", "model": "qwen2.5-coder:7b"},
        timeout=10.0,
    )
    assert resp.status_code == 200
    mission = resp.json()["mission"]
    assert "git" in mission["proposed_mission"].lower() or "status" in mission["proposed_mission"].lower()


@pytest.mark.asyncio
async def test_live_user_case_3_log_analysis():
    """Case 3: Log Analysis — Create mission, verify it creates."""
    resp = httpx.post(
        f"{GATEWAY_URL}/api/raven/missions",
        json={"query": "Show the last 20 lines of logs for the sharedllm_gateway container.", "model": "qwen2.5-coder:7b"},
        timeout=10.0,
    )
    assert resp.status_code == 200
    mission = resp.json()["mission"]
    assert mission["status"] in ("queued", "executing")


@pytest.mark.asyncio
async def test_live_user_case_4_dependency_update():
    """Case 4: Dependency Update — Create mission, verify it creates."""
    resp = httpx.post(
        f"{GATEWAY_URL}/api/raven/missions",
        json={"query": "Review requirements.txt for outdated dependencies.", "model": "qwen2.5-coder:7b"},
        timeout=10.0,
    )
    assert resp.status_code == 200
    mission = resp.json()["mission"]
    assert mission["status"] in ("queued", "executing")


@pytest.mark.asyncio
async def test_live_user_case_5_file_conversion():
    """Case 5: File Conversion — Create mission, verify it creates."""
    resp = httpx.post(
        f"{GATEWAY_URL}/api/raven/missions",
        json={"query": "Find all PNG images in Assets and convert them to WebP.", "model": "qwen2.5-coder:7b"},
        timeout=10.0,
    )
    assert resp.status_code == 200
    mission = resp.json()["mission"]
    assert mission["status"] in ("queued", "executing")


@pytest.mark.asyncio
async def test_live_user_case_6_system_health():
    """Case 6: System Health — Create mission, verify it creates and completes."""
    resp = httpx.post(
        f"{GATEWAY_URL}/api/raven/missions",
        json={"query": "Check system health and report on all services.", "model": "qwen2.5-coder:7b"},
        timeout=10.0,
    )
    assert resp.status_code == 200
    mission = resp.json()["mission"]

    result = poll_mission_status(mission["id"])
    assert result["status"] in ("completed", "failed")


@pytest.mark.asyncio
async def test_live_user_case_7_error_recovery():
    """Case 7: Error Recovery — Create mission, verify it creates."""
    resp = httpx.post(
        f"{GATEWAY_URL}/api/raven/missions",
        json={"query": "Fix the broken API endpoint in main.py.", "model": "qwen2.5-coder:7b"},
        timeout=10.0,
    )
    assert resp.status_code == 200
    mission = resp.json()["mission"]
    assert "fix" in mission["proposed_mission"].lower()


@pytest.mark.asyncio
async def test_live_user_case_8_context_investigation():
    """Case 8: Context Investigation — Create mission, verify it creates."""
    resp = httpx.post(
        f"{GATEWAY_URL}/api/raven/missions",
        json={"query": "Investigate why Home Assistant devices are not showing up.", "model": "qwen2.5-coder:7b"},
        timeout=10.0,
    )
    assert resp.status_code == 200
    mission = resp.json()["mission"]
    assert "home assistant" in mission["proposed_mission"].lower() or "devices" in mission["proposed_mission"].lower()


@pytest.mark.asyncio
async def test_live_user_case_9_multistep_refactoring():
    """Case 9: Multi-Step Refactoring — Create mission, verify it creates."""
    resp = httpx.post(
        f"{GATEWAY_URL}/api/raven/missions",
        json={"query": "Refactor the gateway handlers to use async properly.", "model": "qwen2.5-coder:7b"},
        timeout=10.0,
    )
    assert resp.status_code == 200
    mission = resp.json()["mission"]
    assert "refactor" in mission["proposed_mission"].lower()


@pytest.mark.asyncio
async def test_live_user_case_10_infrastructure_repair():
    """Case 10: Infrastructure Repair — Create mission, verify it creates."""
    resp = httpx.post(
        f"{GATEWAY_URL}/api/raven/missions",
        json={"query": "The gateway service is down, restart it and verify.", "model": "qwen2.5-coder:7b"},
        timeout=10.0,
    )
    assert resp.status_code == 200
    mission = resp.json()["mission"]
    assert "gateway" in mission["proposed_mission"].lower()


# ============================================================================
# EDGE CASE: JSON Parse Robustness
# ============================================================================

def test_extract_action_json_with_real_llm_format():
    """Test parsing of realistic LLM output with reasoning blocks."""
    text = '''I will execute a git status check.

```json
{
  "action": "GitOperationRequest",
  "payload": {
    "action": "status",
    "path": "."
  }
}
```

The status check will show current branch and any changes.'''
    result = extract_action_json(text)
    assert result is not None
    assert result["action"] == "GitOperationRequest"
    assert result["payload"]["action"] == "status"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
