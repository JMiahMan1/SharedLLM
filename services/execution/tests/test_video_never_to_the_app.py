"""A video page sent to a TV is streamed by yt-dlp, never handed to the TV's own app.

Every Jarvis video path (Cast, Android TV, Samsung, webOS, Roku, VideoPlayRequest) fetches the
video with yt-dlp and plays execution's stream. A raw "play_media this YouTube link" through the
generic service route used to go to the TV as-is.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from services.execution.schemas import ExecutionResult

H = {"X-Internal-Secret": "test-secret"}
USER = {"user": "tester", "is_admin": True, "ha_url": "http://hub.test:8123", "ha_token": "t"}


@pytest.fixture
def client():
    from services.execution.main import app
    return TestClient(app)


@pytest.mark.parametrize("link", ["https://www.youtube.com/watch?v=abcDEF12345", "https://youtu.be/abcDEF12345"])
def test_a_youtube_link_goes_through_the_yt_dlp_video_path(client, link):
    played = AsyncMock(return_value=ExecutionResult.ok("Playing", service="media_play"))
    device = AsyncMock()
    with patch("services.execution.main.verify_entity_access", new=AsyncMock(return_value=True)), \
            patch("services.execution.main.ha_client.get_state", new=AsyncMock(return_value={"state": "on"})), \
            patch("services.execution.main.media.handle_media_play", new=played), \
            patch("services.execution.main.hardware_router.execute_device_command", new=device):
        r = client.post("/execute/ha_service", headers=H, json={
            "user_context": USER, "domain": "media_player", "service": "play_media",
            "entity_id": "media_player.den_tv",
            "service_data": {"media_content_id": link, "media_content_type": "url"}})
    assert r.status_code == 200 and r.json()["status"] == "SUCCESS"
    device.assert_not_awaited()
    req = played.await_args.args[0]
    assert (req.entity_id, req.query, req.media_type) == ("media_player.den_tv", link, "video")


def test_other_media_still_goes_to_the_player(client):
    played = AsyncMock()
    device = AsyncMock(return_value=ExecutionResult.ok("ok", service="ha_service"))
    with patch("services.execution.main.verify_entity_access", new=AsyncMock(return_value=True)), \
            patch("services.execution.main.ha_client.get_state", new=AsyncMock(return_value={"state": "on"})), \
            patch("services.execution.main.media.handle_media_play", new=played), \
            patch("services.execution.main.hardware_router.execute_device_command", new=device):
        client.post("/execute/ha_service", headers=H, json={
            "user_context": USER, "domain": "media_player", "service": "play_media",
            "entity_id": "media_player.den_tv",
            "service_data": {"media_content_id": "http://nas.test/movie.mp4", "media_content_type": "video/mp4"}})
    played.assert_not_awaited()
    device.assert_awaited_once()
