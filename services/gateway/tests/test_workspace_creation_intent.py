import pytest
from unittest.mock import AsyncMock, patch

from services.gateway.main import (
    wants_workspace_creation,
    extract_workspace_name_from_query,
    wants_direct_code_orchestration,
)
from services.gateway.orchestrator import _execute_single_tool, SINGLE_TURN_TOOL_ENDPOINTS, _TOOL_SERVICE_MAP
from services.gateway.schemas import ResolvedCredentials


def test_wants_workspace_creation():
    assert wants_workspace_creation("create a workspace environment for 'Home Work'") is True
    assert wants_workspace_creation('create a workspace environment for "Home Work"') is True
    assert wants_workspace_creation("create a workspace for Home Work") is True
    assert wants_workspace_creation("make a new workspace named project-alpha") is True
    assert wants_workspace_creation("setup a workspace for data science") is True
    assert wants_workspace_creation("create workspace Homework") is True

    # Should NOT match raven missions
    assert wants_workspace_creation("raven create a workspace for testing") is False

    # Should NOT match code/file creation queries
    assert wants_workspace_creation("create a python file in the workspace") is False
    assert wants_workspace_creation("create a test file named test_app.py") is False
    assert wants_workspace_creation("create temp/README.md in workspace") is False
    assert wants_workspace_creation("what is in the workspace") is False


def test_extract_workspace_name_from_query():
    assert extract_workspace_name_from_query("create a workspace environment for 'Home Work'") == "Home Work"
    assert extract_workspace_name_from_query('create a workspace environment for "Home Work"') == "Home Work"
    assert extract_workspace_name_from_query("create a workspace for Home Work") == "Home Work"
    assert extract_workspace_name_from_query("create a workspace named project-alpha") == "project-alpha"
    assert extract_workspace_name_from_query("create workspace Homework") == "Homework"


def test_wants_direct_code_orchestration_excludes_workspace_creation():
    # Workspace creation queries should NOT be intercepted by code orchestration
    assert wants_direct_code_orchestration("create a workspace environment for 'Home Work'") is False
    assert wants_direct_code_orchestration("create a workspace for Home Work") is False
    assert wants_direct_code_orchestration("create workspace Homework") is False

    # File code orchestration should still be detected
    assert wants_direct_code_orchestration("create a python file named app.py") is True
    assert wants_direct_code_orchestration("edit file main.py") is True


def test_orchestrator_workspace_create_tool_registered():
    assert "workspacecreaterequest" in SINGLE_TURN_TOOL_ENDPOINTS
    assert SINGLE_TURN_TOOL_ENDPOINTS["workspacecreaterequest"] == "/workspaces"
    assert _TOOL_SERVICE_MAP["workspacecreaterequest"] == "workspace_runtime_svc_url"


@pytest.mark.asyncio
async def test_orchestrator_execute_workspace_create():
    creds = ResolvedCredentials(
        user="testuser",
        is_admin=True,
        api_key="key",
    )
    settings = {
        "workspace_runtime_svc_url": "http://workspace_runtime:8000",
    }
    tool_data = {
        "action": "workspacecreaterequest",
        "name": "Home Work",
    }

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={"id": "home-work", "display_name": "Home Work"})

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch("services.gateway.orchestrator.get_llm_settings", AsyncMock(return_value=settings)), \
         patch("services.gateway.main.shared_http_client") as mock_http_ctx:
        mock_http_ctx.return_value.__aenter__.return_value = mock_client

        result = await _execute_single_tool(
            action="workspacecreaterequest",
            tool_data=tool_data,
            query="create a workspace environment for 'Home Work'",
            creds=creds,
        )

        assert "Created workspace environment 'Home Work'" in result
        assert "home-work" in result

        # Verify posted payload
        called_args, called_kwargs = mock_client.post.call_args
        payload = called_kwargs.get("json")
        assert payload["id"] == "home-work"
        assert payload["display_name"] == "Home Work"
        assert payload["owner_user"] == "testuser"
        assert "user_context" not in payload
