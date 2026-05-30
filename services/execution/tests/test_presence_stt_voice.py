"""
Tests for presence detection, STT, and voice command features.
Run from services/execution/ directory: python -m pytest tests/test_presence_stt_voice.py -v
"""
import pytest


# ─── Presence Tests ───────────────────────────────────────────────────────────

class TestPresenceTracker:
    """Test ESPresense presence tracking."""

    @pytest.fixture
    def tracker(self):
        from services.execution.presence import PresenceTracker
        return PresenceTracker(
            mqtt_host="localhost",
            mqtt_port=1883,
            redis_url="redis://localhost:6379/0",
        )

    def test_register_user_mac(self, tracker):
        tracker.register_user_mac("user1", "AA:BB:CC:DD:EE:FF")
        assert "user1" in tracker._user_mac_map
        assert tracker._user_mac_map["user1"] == "aa:bb:cc:dd:ee:ff"

    def test_process_room_update(self, tracker):
        """Test room-level presence message processing."""
        payload = {
            "occupants": [
                {"id": "user1", "confidence": 0.85},
                {"id": "user2", "confidence": 0.6},
            ]
        }
        # Should not crash even without event loop
        tracker._process_room_update("living_room", payload)

    def test_process_device_update(self, tracker):
        """Test device-level presence message processing."""
        tracker.register_user_mac("user1", "aa:bb:cc:dd:ee:ff")
        payload = {"room": "kitchen", "confidence": 0.75}
        # Should not crash even without event loop
        tracker._process_device_update("aa:bb:cc:dd:ee:ff", payload)

    def test_process_device_update_unknown_mac(self, tracker):
        """Test device update with unregistered MAC."""
        payload = {"room": "bedroom", "confidence": 0.5}
        tracker._process_device_update("11:22:33:44:55:66", payload)

    @pytest.mark.asyncio
    async def test_get_user_presence_no_redis(self, tracker):
        """Test presence lookup when Redis is not connected."""
        result = await tracker.get_user_presence("user1")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_all_presence_no_redis(self, tracker):
        """Test all presence lookup when Redis is not connected."""
        result = await tracker.get_all_presence()
        assert result == {}

    @pytest.mark.asyncio
    async def test_get_rooms_no_redis(self, tracker):
        """Test room list when Redis is not connected."""
        result = await tracker.get_rooms()
        assert result == []


# ─── Voice Command Logic Tests ────────────────────────────────────────────────

class TestVoiceCommandLogic:
    """Test voice command keyword detection logic."""

    def test_light_keywords_detection(self):
        """Test light command keyword detection."""
        light_keywords = ["light", "lights", "lamp", "brightness", "dim", "turn on", "turn off"]
        test_cases = [
            ("turn on the lights", True),
            ("dim the kitchen lamp", True),
            ("set brightness to 50", True),
            ("turn off bedroom lights", True),
            ("play some music", False),
            ("what is the weather", False),
        ]
        for transcript, expected in test_cases:
            result = any(kw in transcript for kw in light_keywords)
            assert result == expected, f"Failed for '{transcript}'"

    def test_media_keywords_detection(self):
        """Test media command keyword detection."""
        media_keywords = ["play", "pause", "stop", "music", "video", "youtube", "spotify"]
        test_cases = [
            ("play music", True),
            ("pause the video", True),
            ("stop playback", True),
            ("open youtube", True),
            ("turn on the lights", False),
            ("what time is it", False),
        ]
        for transcript, expected in test_cases:
            result = any(kw in transcript for kw in media_keywords)
            assert result == expected, f"Failed for '{transcript}'"


# ─── Intercom Presence Routing Tests ──────────────────────────────────────────

class TestIntercomPresenceRouting:
    """Test intercom routing via presence data."""

    @pytest.mark.asyncio
    async def test_resolve_user_room_no_presence(self):
        """Test room resolution when no presence data."""
        from services.execution.handlers.intercom import _resolve_user_room
        result = await _resolve_user_room("nonexistent_user")
        assert result is None

    @pytest.mark.asyncio
    async def test_resolve_user_room_with_mock_presence(self):
        """Test room resolution with mocked presence data."""
        from unittest.mock import patch, AsyncMock
        from services.execution.handlers.intercom import _resolve_user_room

        mock_tracker = AsyncMock()
        mock_tracker.get_user_presence.return_value = {
            "room": "living_room",
            "confidence": 0.85,
            "last_seen": 1234567890,
        }

        with patch("services.execution.presence.get_presence_tracker", return_value=mock_tracker):
            result = await _resolve_user_room("user1")
            assert result == "living_room"
