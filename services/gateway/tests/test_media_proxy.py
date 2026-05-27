"""Comprehensive tests for gateway media proxy routes."""
import os
import sys
os.environ["INTERNAL_SECRET"] = "test-secret"

import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, AsyncMock, patch


@pytest.fixture(name="client")
def client_fixture(monkeypatch):
    """Setup gateway test client with mocked dependencies."""
    sys.modules["fastembed"] = MagicMock()
    mock_engine = MagicMock()
    mock_engine.engine = MagicMock()
    mock_engine.engine.classify.return_value = ("unknown", 0.0)
    mock_engine.engine.should_bypass_llm.return_value = False
    sys.modules["intent_engine"] = mock_engine
    sys.modules["background_worker"] = MagicMock()
    
    from main import app
    import main
    main.background_tasks = None  # pyright: ignore[reportAttributeAccessIssue]
    
    return TestClient(app)


class MockHTTPXResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json_data = json_data or {"status": "SUCCESS"}
    
    def json(self):
        return self._json_data


@pytest.mark.asyncio
async def test_proxy_media_status_resolves_identity(monkeypatch, client):
    """Test that media status proxy resolves identity before proxying."""
    import main as gateway_main
    
    # Mock identity resolution
    mock_creds = {"user": "testuser", "ha_url": "http://ha.local", "ha_token": "secret"}
    
    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(return_value=mock_creds)):
        with patch('httpx.AsyncClient') as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=MockHTTPXResponse(json_data={
                "status": "SUCCESS",
                "detail": {"active": None, "available": [], "all_players": []}
            }))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            
            resp = client.post("/execute/media/status", json={})
            
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "SUCCESS"


@pytest.mark.asyncio
async def test_proxy_media_status_falls_back_to_first_user(monkeypatch, client):
    """Test that media status proxy falls back to first user when identity resolution fails."""
    import main as gateway_main
    
    # Mock identity resolution to raise exception
    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(side_effect=Exception("Identity service down"))):
        with patch.object(gateway_main, 'resolve_first_user', new=AsyncMock(return_value={
            "user": "default", "ha_url": "http://ha.local", "ha_token": "secret"
        })):
            with patch('httpx.AsyncClient') as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(return_value=MockHTTPXResponse(json_data={
                    "status": "SUCCESS",
                    "detail": {"active": None, "available": [], "all_players": []}
                }))
                mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)
                
                resp = client.post("/execute/media/status", json={})
                
                assert resp.status_code == 200


@pytest.mark.asyncio
async def test_proxy_media_transport_resolves_identity(monkeypatch, client):
    """Test that media transport proxy resolves identity."""
    import main as gateway_main
    
    mock_creds = {"user": "testuser", "ha_url": "http://ha.local", "ha_token": "secret"}
    
    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(return_value=mock_creds)):
        with patch('httpx.AsyncClient') as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=MockHTTPXResponse(json_data={
                "status": "SUCCESS"
            }))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            
            resp = client.post("/execute/media/transport", json={"entity_id": "media_player.tv", "command": "pause"})
            
            assert resp.status_code == 200


@pytest.mark.asyncio
async def test_proxy_media_play_resolves_identity(monkeypatch, client):
    """Test that media play proxy resolves identity."""
    import main as gateway_main
    
    mock_creds = {"user": "testuser", "ha_url": "http://ha.local", "ha_token": "secret"}
    
    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(return_value=mock_creds)):
        with patch('httpx.AsyncClient') as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=MockHTTPXResponse(json_data={
                "status": "SUCCESS"
            }))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            
            resp = client.post("/execute/media/play", json={"entity_id": "media_player.tv", "media_type": "music", "query": "test"})
            
            assert resp.status_code == 200


@pytest.mark.asyncio
async def test_proxy_audiobookshelf_resolves_identity(monkeypatch, client):
    """Test that audiobookshelf proxy resolves identity."""
    import main as gateway_main
    
    mock_creds = {"user": "testuser", "ha_url": "http://ha.local", "ha_token": "secret"}
    
    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(return_value=mock_creds)):
        with patch('httpx.AsyncClient') as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=MockHTTPXResponse(json_data={
                "status": "SUCCESS"
            }))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            
            resp = client.post("/execute/audiobookshelf", json={"action": "libraries"})
            
            assert resp.status_code == 200


@pytest.mark.asyncio
async def test_proxy_media_status_forwards_correct_payload(monkeypatch, client):
    """Test that media status proxy forwards correct user context to execution."""
    import main as gateway_main
    
    captured_payload = {}
    
    def capture_post(*args, **kwargs):
        captured_payload.update(kwargs.get("json", {}))
        return MockHTTPXResponse(json_data={"status": "SUCCESS"})
    
    mock_creds = {"user": "testuser", "ha_url": "http://ha.local", "ha_token": "secret"}
    
    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(return_value=mock_creds)):
        with patch('httpx.AsyncClient') as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=capture_post)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            
            resp = client.post("/execute/media/status", json={"area": "Living Room"})
            
            assert resp.status_code == 200
            assert "user_context" in captured_payload
            assert captured_payload["user_context"]["user"] == "testuser"


@pytest.mark.asyncio
async def test_proxy_media_status_preserves_request_body(monkeypatch, client):
    """Test that media status proxy preserves request body fields."""
    import main as gateway_main
    
    captured_payload = {}
    
    def capture_post(*args, **kwargs):
        captured_payload.update(kwargs.get("json", {}))
        return MockHTTPXResponse(json_data={"status": "SUCCESS"})
    
    mock_creds = {"user": "testuser", "ha_url": "http://ha.local", "ha_token": "secret"}
    
    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(return_value=mock_creds)):
        with patch('httpx.AsyncClient') as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=capture_post)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            
            resp = client.post("/execute/media/status", json={"area": "Kitchen", "entity_id": "media_player.tv"})
            
            assert resp.status_code == 200
            assert captured_payload.get("area") == "Kitchen"
            assert captured_payload.get("entity_id") == "media_player.tv"


@pytest.mark.asyncio
async def test_proxy_media_transport_preserves_command(monkeypatch, client):
    """Test that media transport proxy preserves command fields."""
    import main as gateway_main
    
    captured_payload = {}
    
    def capture_post(*args, **kwargs):
        captured_payload.update(kwargs.get("json", {}))
        return MockHTTPXResponse(json_data={"status": "SUCCESS"})
    
    mock_creds = {"user": "testuser", "ha_url": "http://ha.local", "ha_token": "secret"}
    
    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(return_value=mock_creds)):
        with patch('httpx.AsyncClient') as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=capture_post)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            
            resp = client.post("/execute/media/transport", json={
                "entity_id": "media_player.tv",
                "command": "pause"
            })
            
            assert resp.status_code == 200
            assert captured_payload.get("entity_id") == "media_player.tv"
            assert captured_payload.get("command") == "pause"


@pytest.mark.asyncio
async def test_proxy_media_play_preserves_query(monkeypatch, client):
    """Test that media play proxy preserves query fields."""
    import main as gateway_main
    
    captured_payload = {}
    
    def capture_post(*args, **kwargs):
        captured_payload.update(kwargs.get("json", {}))
        return MockHTTPXResponse(json_data={"status": "SUCCESS"})
    
    mock_creds = {"user": "testuser", "ha_url": "http://ha.local", "ha_token": "secret"}
    
    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(return_value=mock_creds)):
        with patch('httpx.AsyncClient') as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=capture_post)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            
            resp = client.post("/execute/media/play", json={
                "entity_id": "media_player.tv",
                "media_type": "music",
                "query": "test song"
            })
            
            assert resp.status_code == 200
            assert captured_payload.get("entity_id") == "media_player.tv"
            assert captured_payload.get("media_type") == "music"
            assert captured_payload.get("query") == "test song"


@pytest.mark.asyncio
async def test_proxy_audiobookshelf_preserves_action(monkeypatch, client):
    """Test that audiobookshelf proxy preserves action field."""
    import main as gateway_main
    
    captured_payload = {}
    
    def capture_post(*args, **kwargs):
        captured_payload.update(kwargs.get("json", {}))
        return MockHTTPXResponse(json_data={"status": "SUCCESS"})
    
    mock_creds = {"user": "testuser", "ha_url": "http://ha.local", "ha_token": "secret"}
    
    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(return_value=mock_creds)):
        with patch('httpx.AsyncClient') as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=capture_post)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            
            resp = client.post("/execute/audiobookshelf", json={"action": "libraries", "library_id": "123"})
            
            assert resp.status_code == 200
            assert captured_payload.get("action") == "libraries"
            assert captured_payload.get("library_id") == "123"


@pytest.mark.asyncio
async def test_proxy_media_status_empty_body(monkeypatch, client):
    """Test that media status proxy handles empty body gracefully."""
    import main as gateway_main
    
    mock_creds = {"user": "testuser", "ha_url": "http://ha.local", "ha_token": "secret"}
    
    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(return_value=mock_creds)):
        with patch('httpx.AsyncClient') as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=MockHTTPXResponse(json_data={"status": "SUCCESS"}))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            
            resp = client.post("/execute/media/status", json={})
            
            assert resp.status_code == 200


@pytest.mark.asyncio
async def test_proxy_media_status_execution_returns_error(monkeypatch, client):
    """Test that media status proxy forwards error status from execution service."""
    import main as gateway_main
    
    mock_creds = {"user": "testuser", "ha_url": "http://ha.local", "ha_token": "secret"}
    
    with patch.object(gateway_main, '_resolve_identity_from_request', new=AsyncMock(return_value=mock_creds)):
        with patch('httpx.AsyncClient') as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=MockHTTPXResponse(status_code=500, json_data={
                "status": "FAILURE",
                "message": "HA connection failed"
            }))
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            
            resp = client.post("/execute/media/status", json={})
            
            # Gateway forwards the execution service status code
            assert resp.status_code == 500
            data = resp.json()
            assert data["status"] == "FAILURE"
            assert "HA connection failed" in data["message"]
