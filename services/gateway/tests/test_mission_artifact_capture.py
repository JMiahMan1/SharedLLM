import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.gateway.background_worker import _capture_mission_artifacts

EXPECTED_ENTRIES = [
    {"path": "narration.wav", "name": "narration.wav", "is_dir": False, "size": 12345},
    {"path": "output/report.md", "name": "report.md", "is_dir": False, "size": 2048},
]


def _aio_resp(status=200, json_data=None):
    m = MagicMock()
    m.status = status
    m.json = AsyncMock(return_value=json_data if json_data is not None else {"status": "SUCCESS"})
    return m


class TestCaptureMissionArtifacts:
    @pytest.mark.asyncio
    async def test_returns_serialized_entries_on_success(self):
        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=_aio_resp(200, {"entries": EXPECTED_ENTRIES}))
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("services.gateway.background_worker._shared_http_client", return_value=mock_ctx):
            result = await _capture_mission_artifacts("mission-1", "ws-1")

        assert result == json.dumps(EXPECTED_ENTRIES)
        _, kwargs = mock_client.post.call_args
        body = kwargs["json"]
        assert body["workspace_id"] == "ws-1"
        assert body["relative_path"] == "."
        assert body["recursive"] is True
        assert body["include_dirs"] is False

    @pytest.mark.asyncio
    async def test_returns_none_without_workspace(self):
        result = await _capture_mission_artifacts("mission-1", None)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self):
        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=_aio_resp(500, {}))
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("services.gateway.background_worker._shared_http_client", return_value=mock_ctx):
            result = await _capture_mission_artifacts("mission-1", "ws-1")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self):
        mock_client = MagicMock()
        mock_client.post = AsyncMock(side_effect=RuntimeError("boom"))
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("services.gateway.background_worker._shared_http_client", return_value=mock_ctx):
            result = await _capture_mission_artifacts("mission-1", "ws-1")

        assert result is None


class TestRavenMissionArtifactsSchema:
    def test_update_schema_accepts_artifacts(self):
        from services.identity.schemas import RavenMissionUpdate

        body = RavenMissionUpdate(status="completed", artifacts=json.dumps(EXPECTED_ENTRIES))
        dumped = body.model_dump()
        assert dumped["artifacts"] == json.dumps(EXPECTED_ENTRIES)

    def test_read_schema_exposes_artifacts(self):
        from services.identity.schemas import RavenMissionRead

        body = RavenMissionRead(
            id=1, mission_type="user_task", priority=1, proposed_mission="mission",
            coding_model="model", status="completed", progress=100, created_at="now",
            artifacts=json.dumps(EXPECTED_ENTRIES),
        )
        assert body.artifacts == json.dumps(EXPECTED_ENTRIES)
