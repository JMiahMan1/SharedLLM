import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock, MagicMock
from services.gateway.main import app

client = TestClient(app)


class TestPauseMission:
    """Tests for POST /api/raven/missions/{id}/pause endpoint."""

    def test_pause_mission_unauthorized(self):
        response = client.post("/api/raven/missions/42/pause")
        assert response.status_code in [401, 403, 503]

    @pytest.mark.asyncio
    async def test_pause_mission_success(self, monkeypatch):
        mock_mission_resp = MagicMock()
        mock_mission_resp.status_code = 200
        mock_mission_resp.json.return_value = {"id": 42, "status": "executing"}

        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock()
        mock_redis.close = AsyncMock()

        mock_redis_module = MagicMock()
        mock_redis_module.from_url = MagicMock(return_value=mock_redis)

        monkeypatch.setattr("redis.asyncio", mock_redis_module)

        with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_mission_resp)):
            with patch("services.gateway.main._resolve_identity_from_request", new=AsyncMock(return_value={"user": "admin", "is_admin": True})):
                response = client.post("/api/raven/missions/42/pause")
                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "SUCCESS"
                assert "paused" in data["message"].lower()
                mock_redis.set.assert_called_once()


class TestResumeMission:
    """Tests for POST /api/raven/missions/{id}/resume endpoint."""

    def test_resume_mission_unauthorized(self):
        response = client.post("/api/raven/missions/42/resume")
        assert response.status_code in [401, 403, 503]

    @pytest.mark.asyncio
    async def test_resume_mission_success(self, monkeypatch):
        mock_mission_resp = MagicMock()
        mock_mission_resp.status_code = 200
        mock_mission_resp.json.return_value = {"id": 42, "status": "executing"}

        mock_redis = AsyncMock()
        mock_redis.delete = AsyncMock()
        mock_redis.close = AsyncMock()

        mock_redis_module = MagicMock()
        mock_redis_module.from_url = MagicMock(return_value=mock_redis)

        monkeypatch.setattr("redis.asyncio", mock_redis_module)

        with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_mission_resp)):
            with patch("services.gateway.main._resolve_identity_from_request", new=AsyncMock(return_value={"user": "admin", "is_admin": True})):
                response = client.post("/api/raven/missions/42/resume")
                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "SUCCESS"
                assert "resumed" in data["message"].lower()
                mock_redis.delete.assert_called_once()


class TestPauseMissionNotFound:
    """Tests for pause/resume with non-existent missions."""

    @pytest.mark.asyncio
    async def test_pause_mission_not_found(self, monkeypatch):
        mock_resp = MagicMock()
        mock_resp.status_code = 404

        with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_resp)):
            with patch("services.gateway.main._resolve_identity_from_request", new=AsyncMock(return_value={"user": "admin", "is_admin": True})):
                response = client.post("/api/raven/missions/999/pause")
                assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_resume_mission_not_found(self, monkeypatch):
        mock_resp = MagicMock()
        mock_resp.status_code = 404

        with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_resp)):
            with patch("services.gateway.main._resolve_identity_from_request", new=AsyncMock(return_value={"user": "admin", "is_admin": True})):
                response = client.post("/api/raven/missions/999/resume")
                assert response.status_code == 404
