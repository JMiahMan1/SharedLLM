import os
import pytest
from unittest.mock import AsyncMock
from fastapi.testclient import TestClient
from contextlib import asynccontextmanager

# Set environment variables for testing
os.environ["INTERNAL_SECRET"] = "test-secret"
os.environ["IDENTITY_SVC_URL"] = "http://identity"

from gateway.main import app

@pytest.fixture
def client(monkeypatch):
    @asynccontextmanager
    async def noop_lifespan(_app):
        yield
    monkeypatch.setattr(app.router, "lifespan_context", noop_lifespan)
    with TestClient(app) as test_client:
        yield test_client

def test_gateway_extracts_bearer_token(client, mocker):
    """
    Test that the Gateway extracts the Bearer token from the Authorization header
    and passes it to the Identity service's /api/resolve endpoint.
    """
    # FIX: Use AsyncMock for all awaited functions
    mock_resolve = mocker.patch("gateway.main.resolve_identity", new_callable=AsyncMock, return_value={"user": "testuser"})
    
    # Mock other downstream calls to prevent errors
    mocker.patch("gateway.main.get_history", new_callable=AsyncMock, return_value=[])
    mocker.patch("gateway.main.fetch_ha_entities", new_callable=AsyncMock, return_value=[])
    mocker.patch("gateway.main.contextualize_query", new_callable=AsyncMock, return_value="test query")
    mocker.patch("gateway.main.decompose_command_query", new_callable=AsyncMock, return_value=[])
    mocker.patch("gateway.main.update_history", new_callable=AsyncMock, return_value=None)
    
    # Mock the LLM response
    class MockResponse:
        def __init__(self):
            self.status_code = 200
        def json(self):
            return {"message": {"content": "Test response"}}
    
    mocker.patch("gateway.main.call_ollama", new_callable=AsyncMock, return_value=MockResponse())

    # Send a request with a Bearer token
    resp = client.post(
        "/api/chat",
        json={"query": "test query"},
        headers={"Authorization": "Bearer sk-test-123"}
    )
    
    assert resp.status_code == 200
    
    # The Assertion: Check that resolve_identity was called with api_key in the body
    mock_resolve.assert_called_once()
    passed_body = mock_resolve.call_args[0][0]
    assert passed_body["api_key"] == "sk-test-123"
    print("\nSUCCESS: Gateway correctly extracted and passed the Bearer token.")

@pytest.mark.asyncio
async def test_gateway_enforces_capabilities_for_coding(client, mocker):
    """
    Test that the Gateway intercepts requests when required capabilities are missing.
    In this case, intent 'workspace_coding' requires 'github_token'.
    """
    # 1. Resolve identity with NO github_token
    mocker.patch("gateway.main.resolve_identity", new_callable=AsyncMock, return_value={
        "user": "testuser",
        "github_token": None  # MISSING!
    })
    
    # 2. Force intent to 'workspace_coding'
    mocker.patch("gateway.main.engine.classify", return_value=("workspace_coding", 1.0))
    
    # 3. Mock Ollama for the persona-driven redirection message
    class MockPersonaResponse:
        def __init__(self):
            self.status_code = 200
        def json(self):
            return {"response": "Please set up your GitHub token in the Identity page."}
            
    mocker.patch("gateway.main.call_ollama", new_callable=AsyncMock, return_value=MockPersonaResponse())
    
    # 4. Mock the downstream service (should NOT be called)
    mock_workspace = mocker.patch("gateway.main.orchestrate_code_change", new_callable=AsyncMock)

    # Send request
    resp = client.post(
        "/api/chat",
        json={"query": "fix my code"},
        headers={"Authorization": "Bearer sk-test-123"}
    )
    
    assert resp.status_code == 200
    data = resp.json()
    content = data["message"]["content"]
    assert "GitHub" in content or "Identity" in content
    
    # PROOF: Downstream was never called
    mock_workspace.assert_not_called()
    print("\nSUCCESS: Gateway correctly blocked request due to missing capability.")
