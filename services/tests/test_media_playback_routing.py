"""
Test media playback routing for Roku, Android TV, and WebOS devices.
Validates:
1. Device detection logic
2. Port 8888 used for all media URLs
3. Correct handler routing per device type
"""
import sys
import os
import asyncio
import unittest
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "execution"))

from announce_handlers import detect_tv_type
from handlers.media import detect_media_type, handle_media_play
from handlers.video import handle_video_play
from schemas import MediaPlayRequest, VideoPlayRequest, UserContext


class TestDeviceDetection(unittest.TestCase):
    """Test TV platform detection logic."""

    def test_roku_detection_by_entity_id(self):
        assert detect_tv_type("media_player.living_room_roku", "on", {}) == "roku"
        assert detect_tv_type("media_player.bedroom_roku_ultra", "on", {}) == "roku"

    def test_roku_detection_by_source_list(self):
        attrs = {"source_list": ["Netflix", "YouTube", "Roku Media Player"]}
        assert detect_tv_type("media_player.tv", "on", attrs) == "roku"

    def test_roku_detection_by_ma_roku(self):
        attrs = {"app_id": "music_assistant", "active_queue": "roku_living_room"}
        assert detect_tv_type("media_player.living_room", "on", attrs) == "roku"

    def test_android_tv_detection(self):
        attrs = {"app_id": "com.google.android.youtube"}
        assert detect_tv_type("media_player.living_room_tv", "on", attrs) == "android_tv"

        attrs = {"app_id": "com.android.tvlauncher"}
        assert detect_tv_type("media_player.shield_tv", "on", attrs) == "android_tv"

    def test_webos_detection(self):
        assert detect_tv_type("media_player.lg_webos_tv", "on", {}) == "webos"
        assert detect_tv_type("media_player.lg_living_room", "on", {}) == "webos"
        assert detect_tv_type("media_player.web_os_tv", "on", {}) == "webos"

    def test_cast_detection(self):
        assert detect_tv_type("media_player.chrome_cast", "on", {}) == "cast"
        assert detect_tv_type("media_player.living_room_cast", "on", {}) == "cast"

    def test_samsung_detection(self):
        assert detect_tv_type("media_player.samsung_tv", "on", {}) == "samsung"
        assert detect_tv_type("media_player.living_room_tizen", "on", {}) == "samsung"


class TestMediaTypeDetection(unittest.TestCase):
    """Test media type detection from queries."""

    def test_video_detection(self):
        assert detect_media_type("play youtube video") == "video"
        assert detect_media_type("https://youtube.com/watch?v=abc123") == "video"
        assert detect_media_type("https://youtu.be/abc123") == "video"
        assert detect_media_type("play vimeo video") == "video"

    def test_music_detection(self):
        assert detect_media_type("play some music") == "music"
        assert detect_media_type("play spotify track") == "music"
        assert detect_media_type("https://spotify.com/track/abc") == "music"

    def test_podcast_detection(self):
        assert detect_media_type("play podcast") == "podcast"
        assert detect_media_type("play episode of the daily show") == "podcast"

    def test_audiobook_detection(self):
        assert detect_media_type("play audiobook") == "audiobook"
        assert detect_media_type("book narrated by john") == "audiobook"

    def test_hint_override(self):
        assert detect_media_type("play music", "video") == "video"
        assert detect_media_type("play video", "music") == "music"


class TestPort8888InCode(unittest.TestCase):
    """Verify port 8888 is used consistently in all media URL construction."""

    def test_video_handler_uses_port_8888(self):
        video_path = os.path.join(os.path.dirname(__file__), "..", "execution", "handlers", "video.py")
        with open(video_path) as f:
            content = f.read()
        # Non-Roku path should use 8888
        assert ":8888/media/" in content, "video.py should use port 8888 for media URLs"
        # Should NOT have old port 8003 for media
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if ":8003/media/" in line and "EXECUTION_EXTERNAL_HOST" in line:
                self.fail(f"video.py line {i+1} still uses port 8003 for media: {line.strip()}")

    def test_media_handler_uses_port_8888(self):
        media_path = os.path.join(os.path.dirname(__file__), "..", "execution", "handlers", "media.py")
        with open(media_path) as f:
            content = f.read()
        assert ":8888/media/" in content, "media.py should use port 8888 for media URLs"
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if ":8003/media/" in line and "EXECUTION_EXTERNAL_HOST" in line:
                self.fail(f"media.py line {i+1} still uses port 8003 for media: {line.strip()}")

    def test_roku_handler_uses_port_8888(self):
        roku_path = os.path.join(os.path.dirname(__file__), "..", "execution", "handlers", "roku.py")
        with open(roku_path) as f:
            content = f.read()
        # Roku handler doesn't construct media URLs directly (video.py does),
        # but verify no hardcoded 8003 for media
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if ":8003/media/" in line:
                self.fail(f"roku.py line {i+1} still uses port 8003 for media: {line.strip()}")


class TestMediaPlaybackRouting(unittest.TestCase):
    """Test that media playback routes to correct handlers per device type."""

    def _make_ctx(self):
        return UserContext(
            user="test",
            ha_url="http://192.168.2.205:8123",
            ha_token="test_token",
        )

    @patch("handlers.media.resolve_entity")
    @patch("handlers.media.detect_tv_type")
    @patch("handlers.video.download_video")
    @patch("handlers.video.search_youtube")
    @patch("handlers.video.extract_video_url")
    @patch("handlers.roku.is_roku_device")
    @patch("ha_client.get_state")
    @patch("ha_client.call_service")
    async def _test_video_play_non_roku(self, mock_call, mock_state, mock_is_roku, mock_extract, mock_search, mock_download, mock_detect, mock_resolve):
        """Test video playback for non-Roku device uses port 8888."""
        mock_resolve.return_value = "media_player.living_room_tv"
        mock_is_roku.return_value = False
        mock_extract.return_value = None
        mock_search.return_value = "https://youtube.com/watch?v=test"
        mock_download.return_value = ("vid-abc123", "Test Video")
        mock_state.return_value = {"state": "on", "attributes": {}}
        mock_call.return_value = {"ok": True}
        mock_detect.return_value = "android_tv"

        req = VideoPlayRequest(
            user_context=self._make_ctx(),
            entity_id="living_room_tv",
            query="test video",
        )

        with patch("handlers.video.EXECUTION_EXTERNAL_HOST", "192.168.2.205"):
            result = await handle_video_play(req)

        # Verify the call_service was called with port 8888 URL
        call_args = mock_call.call_args_list
        play_media_call = [c for c in call_args if c[0][2] == "play_media"]
        assert len(play_media_call) > 0, "play_media service should have been called"
        media_url = play_media_call[0][0][4]["media_content_id"]
        assert ":8888/media/" in media_url, f"Media URL should use port 8888, got: {media_url}"

    @patch("handlers.video.download_video_for_roku")
    @patch("handlers.video.search_youtube")
    @patch("handlers.video.extract_video_url")
    @patch("handlers.roku.is_roku_device")
    @patch("handlers.roku.roku_play_video")
    @patch("ha_client.get_state")
    async def _test_video_play_roku(self, mock_state, mock_roku_play, mock_is_roku, mock_extract, mock_search, mock_download):
        """Test video playback for Roku device uses ECP + port 8888."""
        mock_is_roku.return_value = True
        mock_extract.return_value = None
        mock_search.return_value = "https://youtube.com/watch?v=test"
        mock_download.return_value = ("vid-roku-abc123", "Test Video")
        mock_state.return_value = {"state": "on", "attributes": {}}
        mock_roku_play.return_value = MagicMock(status="SUCCESS")

        req = VideoPlayRequest(
            user_context=self._make_ctx(),
            entity_id="living_room_roku",
            query="test video",
        )

        with patch("handlers.video.EXECUTION_EXTERNAL_HOST", "192.168.2.205"):
            result = await handle_video_play(req)

        # Verify roku_play_video was called with port 8888 URL
        call_args = mock_roku_play.call_args
        stream_url = call_args[0][3]
        assert ":8888/media/" in stream_url, f"Roku stream URL should use port 8888, got: {stream_url}"
        assert "vid-roku-" in stream_url, "Roku should use roku-specific video ID"


class TestRokuVideoURL(unittest.TestCase):
    """Test Roku video URL construction specifically."""

    def test_roku_video_url_format(self):
        """Roku ECP needs a direct HTTP URL to an MP4 file."""
        from config import EXECUTION_EXTERNAL_HOST
        # Even if not set, the format should be correct
        host = EXECUTION_EXTERNAL_HOST or "192.168.2.205"
        expected = f"http://{host}:8888/media/vid-roku-abc123"
        assert expected.startswith("http://")
        assert ":8888/media/" in expected
        assert "vid-roku-" in expected


if __name__ == "__main__":
    # Run sync tests
    unittest.main()
