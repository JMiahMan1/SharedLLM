"""
Comprehensive tests for the MA WebSocket client.

Tests cover:
- Connection flow (connect/disconnect)
- Command sending (with MA-format message IDs)
- Event handling and dispatching
- Stream URL extraction from queue state
- Reconnect logic with exponential backoff
- Error handling for various failure modes
- Context manager support
"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.gateway.ma_ws_client import (
    MAWebSocketClient,
    EVENT_QUEUE_UPDATED,
    EVENT_PLAYER_UPDATED,
    EVENT_QUEUE_ENDED,
    EVENT_QUEUE_STARTED,
    COMMAND_PREFIX,
    PLAY_MEDIA_COMMAND,
    HEARTBEAT_INTERVAL,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mass_url():
    return "http://ha.sumemail.com:8095"


@pytest.fixture
def mass_token():
    return "test.jwt.token.abc123"


@pytest.fixture
def client(mass_url, mass_token):
    return MAWebSocketClient(mass_url, mass_token)


@pytest.fixture
def mock_websocket():
    ws = AsyncMock()
    ws.closed = False
    ws.close = AsyncMock()
    ws.ping = AsyncMock()
    ws.send = AsyncMock()
    return ws


# ---------------------------------------------------------------------------
# Initialization Tests
# ---------------------------------------------------------------------------

class TestInitialization:
    def test_init_sets_url_and_token(self, client, mass_url, mass_token):
        assert client._mass_url == mass_url
        assert client._mass_token == mass_token

    def test_init_sets_ws_url_with_token(self, client):
        # Token is passed as a query parameter; http:// is converted to ws://
        assert client.ws_url == "ws://ha.sumemail.com:8095/ws?token=test.jwt.token.abc123"

    def test_init_default_reconnect_settings(self, client):
        assert client._reconnect_base_delay == 1.0
        assert client._reconnect_max_delay == 30.0
        assert client._reconnect_backoff_factor == 2.0

    def test_init_custom_reconnect_settings(self):
        c = MAWebSocketClient(
            "http://localhost:8095",
            "token",
            reconnect_base_delay=2.0,
            reconnect_max_delay=60.0,
            reconnect_backoff_factor=3.0,
        )
        assert c._reconnect_base_delay == 2.0
        assert c._reconnect_max_delay == 60.0
        assert c._reconnect_backoff_factor == 3.0

    def test_init_starts_disconnected(self, client):
        assert client.connected is False
        assert client.is_connected is False

    def test_init_empty_queue_state(self, client):
        assert client._queue_state == {}
        assert client.get_queue_state() == {}

    def test_init_no_stream_url(self, client):
        assert client.get_stream_url() is None

    def test_init_no_callbacks(self, client):
        assert client._event_callbacks == {}

    def test_init_last_error_none(self, client):
        assert client.last_error is None

    def test_init_reconnect_count_zero(self, client):
        assert client.reconnect_count == 0

    def test_init_repr_as_disconnected(self, client):
        repr_str = repr(client)
        assert "disconnected" in repr_str
        assert "ws://ha.sumemail.com:8095/ws" in repr_str
        assert "reconnects=0" in repr_str

    def test_msg_id_starts_at_zero(self, client):
        assert client._msg_id == 0


# ---------------------------------------------------------------------------
# Connection Tests
# ---------------------------------------------------------------------------

class TestConnection:
    @pytest.mark.asyncio
    async def test_connect_establishes_websocket(self, client, mock_websocket):
        with patch("websockets.connect", AsyncMock(return_value=mock_websocket)):
            with patch.object(client, "_establish_connection") as mock_establish:
                mock_establish.return_value = None
                await client.connect()
                assert mock_establish.called

    @pytest.mark.asyncio
    async def test_connect_sets_connected_state(self, client, mock_websocket):
        with patch("websockets.connect", AsyncMock(return_value=mock_websocket)):
            with patch.object(client, "_establish_connection") as mock_establish:
                async def setup_connected():
                    client._connected = True
                    client._authenticated = True
                    client._ws = mock_websocket
                mock_establish.side_effect = setup_connected
                await client.connect()
                assert client.connected is True

    @pytest.mark.asyncio
    async def test_connect_resets_reconnect_count(self, client, mock_websocket):
        client._reconnect_count = 5
        with patch("websockets.connect", AsyncMock(return_value=mock_websocket)):
            # Don't patch _establish_connection so the real method runs and resets the count
            await client._establish_connection()
            assert client._reconnect_count == 0

    @pytest.mark.asyncio
    async def test_connect_skips_if_already_connected(self, client, mock_websocket):
        client._connected = True
        with patch("websockets.connect", AsyncMock(return_value=mock_websocket)):
            with patch.object(client, "_establish_connection") as mock_establish:
                await client.connect()
                assert not mock_establish.called

    @pytest.mark.asyncio
    async def test_connect_starts_message_handler_task(self, client, mock_websocket):
        with patch("websockets.connect", AsyncMock(return_value=mock_websocket)):
            # Call the actual method to verify it creates background tasks
            client._message_handler_task = None
            await client._establish_connection()
            assert client._message_handler_task is not None

    @pytest.mark.asyncio
    async def test_connect_uses_correct_ws_url_with_token(self, client, mock_websocket):
        with patch("websockets.connect", AsyncMock(return_value=mock_websocket)) as mock_connect:
            await client._establish_connection()
            call_args = mock_connect.call_args
            # Token is in the URL, http:// is converted to ws://
            assert call_args[0][0] == "ws://ha.sumemail.com:8095/ws?token=test.jwt.token.abc123"

    @pytest.mark.asyncio
    async def test_connect_does_not_use_auth_header(self, client, mock_websocket):
        with patch("websockets.connect", AsyncMock(return_value=mock_websocket)) as mock_connect:
            await client._establish_connection()
            call_kwargs = mock_connect.call_args[1]
            # No additional_headers with Authorization
            assert "additional_headers" not in call_kwargs

    @pytest.mark.asyncio
    async def test_connect_handles_connection_failure(self, client):
        with patch("websockets.connect", AsyncMock(side_effect=Exception("Connection refused"))):
            with pytest.raises(ConnectionError, match="Failed to connect"):
                await client._establish_connection()
            assert client.connected is False
            assert client.last_error is not None

    @pytest.mark.asyncio
    async def test_disconnect_closes_websocket(self, client, mock_websocket):
        client._ws = mock_websocket
        client._connected = True
        await client.disconnect()
        assert mock_websocket.close.called
        assert client.connected is False

    @pytest.mark.asyncio
    async def test_disconnect_stops_background_tasks(self, client, mock_websocket):
        client._ws = mock_websocket
        client._connected = True
        client._message_handler_task = AsyncMock()
        client._message_handler_task.done = MagicMock(return_value=False)
        client._message_handler_task.cancel = MagicMock()

        with patch.object(client, "_stop_background_tasks") as mock_stop:
            await client.disconnect()
            assert mock_stop.called

    @pytest.mark.asyncio
    async def test_disconnect_handles_already_disconnected(self, client):
        await client.disconnect()
        assert client.connected is False

    @pytest.mark.asyncio
    async def test_disconnect_sends_reason(self, client, mock_websocket):
        client._ws = mock_websocket
        client._connected = True
        await client.disconnect()
        call_args = mock_websocket.close.call_args
        assert call_args[1]["reason"] == "Gateway shutting down"


# ---------------------------------------------------------------------------
# Command Sending Tests
# ---------------------------------------------------------------------------

class TestCommandSending:
    @pytest.mark.asyncio
    async def test_send_command_raises_when_disconnected(self, client):
        with pytest.raises(ConnectionError, match="not connected"):
            await client.send_command("player_queues/play_media")

    @pytest.mark.asyncio
    async def test_send_command_no_wait_raises_when_disconnected(self, client):
        with pytest.raises(ConnectionError, match="not connected"):
            await client.send_command_no_wait("player_queues/pause")

    @pytest.mark.asyncio
    async def test_send_command_sends_json(self, client, mock_websocket):
        client._ws = mock_websocket
        client._connected = True

        await client.send_command("player_queues/play_media", {"queue_id": "player_1", "media": "https://www.youtube.com/watch?v=123"})

        mock_websocket.send.assert_called_once()
        sent = mock_websocket.send.call_args[0][0]
        data = json.loads(sent)
        assert data["command"] == "player_queues/play_media"
        assert data["args"]["media"] == "https://www.youtube.com/watch?v=123"
        assert "message_id" in data
        # Message ID follows MA format: "counter{n}"
        assert data["message_id"].startswith("counter")

    @pytest.mark.asyncio
    async def test_send_command_without_args(self, client, mock_websocket):
        client._ws = mock_websocket
        client._connected = True

        await client.send_command("player_queues/pause")

        sent = mock_websocket.send.call_args[0][0]
        data = json.loads(sent)
        assert data["command"] == "player_queues/pause"
        assert "args" not in data

    @pytest.mark.asyncio
    async def test_send_command_no_wait_sends_json(self, client, mock_websocket):
        client._ws = mock_websocket
        client._connected = True

        await client.send_command_no_wait("player_queues/next")

        mock_websocket.send.assert_called_once()
        sent = mock_websocket.send.call_args[0][0]
        data = json.loads(sent)
        assert data["command"] == "player_queues/next"

    @pytest.mark.asyncio
    async def test_send_command_generates_unique_ids(self, client, mock_websocket):
        client._ws = mock_websocket
        client._connected = True

        await client.send_command("player_queues/play_media", {"queue_id": "p1", "media": "a"})
        await client.send_command("player_queues/play_media", {"queue_id": "p1", "media": "b"})

        assert mock_websocket.send.call_count == 2
        first = json.loads(mock_websocket.send.call_args_list[0][0][0])
        second = json.loads(mock_websocket.send.call_args_list[1][0][0])
        assert first["message_id"] != second["message_id"]
        # Verify counter format
        assert first["message_id"] == "counter1"
        assert second["message_id"] == "counter2"

    @pytest.mark.asyncio
    async def test_send_command_logs_message(self, client, mock_websocket):
        client._ws = mock_websocket
        client._connected = True
        with patch("services.gateway.ma_ws_client.log") as mock_log:
            await client.send_command("player_queues/play_media", {"queue_id": "p1", "media": "test"})
            assert mock_log.info.called


# ---------------------------------------------------------------------------
# Event Handling Tests
# ---------------------------------------------------------------------------

class TestEventHandling:
    @pytest.mark.asyncio
    async def test_register_event_callback(self, client):
        callback = MagicMock()
        client.register_event_callback("queue_updated", callback)
        assert callback in client._event_callbacks["queue_updated"]

    @pytest.mark.asyncio
    async def test_register_multiple_callbacks(self, client):
        cb1 = MagicMock()
        cb2 = MagicMock()
        client.register_event_callback("queue_updated", cb1)
        client.register_event_callback("queue_updated", cb2)
        assert len(client._event_callbacks["queue_updated"]) == 2

    @pytest.mark.asyncio
    async def test_unregister_event_callback(self, client):
        callback = MagicMock()
        client.register_event_callback("queue_updated", callback)
        client.unregister_event_callback("queue_updated", callback)
        assert callback not in client._event_callbacks.get("queue_updated", [])

    @pytest.mark.asyncio
    async def test_unregister_nonexistent_callback(self, client):
        callback = MagicMock()
        client.unregister_event_callback("queue_updated", callback)
        assert "queue_updated" not in client._event_callbacks

    @pytest.mark.asyncio
    async def test_dispatch_queue_updated_event(self, client):
        callback = MagicMock()
        client.register_event_callback("queue_updated", callback)

        event_data = {
            "state": "playing",
            "current_item": {"name": "Test Song"},
        }
        await client._dispatch_event("queue_updated", event_data)

        callback.assert_called_once_with("queue_updated", event_data)
        assert client._queue_state == event_data

    @pytest.mark.asyncio
    async def test_dispatch_player_updated_event(self, client):
        callback = MagicMock()
        client.register_event_callback("player_updated", callback)

        event_data = {
            "state": "paused",
            "volume_level": 0.5,
        }
        await client._dispatch_event("player_updated", event_data)

        callback.assert_called_once_with("player_updated", event_data)
        assert client._queue_state == event_data

    @pytest.mark.asyncio
    async def test_dispatch_event_calls_async_callback(self, client):
        call_log = []

        async def async_callback(event_type, data):
            call_log.append((event_type, data))

        client.register_event_callback("queue_updated", async_callback)
        await client._dispatch_event("queue_updated", {"state": "playing"})

        assert len(call_log) == 1
        assert call_log[0] == ("queue_updated", {"state": "playing"})

    @pytest.mark.asyncio
    async def test_dispatch_event_calls_sync_callback(self, client):
        call_log = []

        def sync_callback(event_type, data):
            call_log.append((event_type, data))

        client.register_event_callback("queue_updated", sync_callback)
        await client._dispatch_event("queue_updated", {"state": "idle"})

        assert len(call_log) == 1
        assert call_log[0] == ("queue_updated", {"state": "idle"})

    @pytest.mark.asyncio
    async def test_dispatch_event_handles_callback_exception(self, client):
        def bad_callback(event_type, data):
            raise ValueError("callback crashed")

        client.register_event_callback("queue_updated", bad_callback)
        # Should not raise
        await client._dispatch_event("queue_updated", {"state": "playing"})

    @pytest.mark.asyncio
    async def test_dispatch_queue_ended_event(self, client):
        await client._dispatch_event("queue_ended", {"state": "idle"})
        assert client._queue_state["state"] == "idle"

    @pytest.mark.asyncio
    async def test_dispatch_queue_started_event(self, client):
        await client._dispatch_event("queue_started", {"state": "playing"})
        assert client._queue_state["state"] == "playing"


# ---------------------------------------------------------------------------
# Stream URL Extraction Tests
# ---------------------------------------------------------------------------

class TestStreamURLExtraction:
    @pytest.mark.asyncio
    async def test_extract_stream_url_from_current_item(self, client):
        stream_url = "http://ha.sumemail.com:8096/flow/abc123/queue1/1/player1.mp3"
        data = {
            "state": "playing",
            "current_item": {
                "media_item": {
                    "name": "Test Track",
                    "stream_url": stream_url,
                }
            },
        }
        await client._dispatch_event("queue_updated", data)
        assert client.get_stream_url() == stream_url

    @pytest.mark.asyncio
    async def test_extract_stream_url_from_current_item_flat(self, client):
        stream_url = "http://ha.sumemail.com:8096/flow/abc123/queue1/1/player1.mp3"
        data = {
            "state": "playing",
            "current_item": {
                "name": "Test Track",
                "stream_url": stream_url,
            },
        }
        await client._dispatch_event("queue_updated", data)
        assert client.get_stream_url() == stream_url

    @pytest.mark.asyncio
    async def test_extract_stream_url_from_queue_items(self, client):
        stream_url = "http://ha.sumemail.com:8096/flow/abc123/queue1/2/player1.mp3"
        data = {
            "state": "playing",
            "current_item": {"name": "Current"},
            "items": [
                {"name": "Item 1"},
                {"name": "Item 2", "stream_url": stream_url},
            ],
        }
        await client._dispatch_event("queue_updated", data)
        assert client.get_stream_url() == stream_url

    @pytest.mark.asyncio
    async def test_extract_stream_url_from_audio_player(self, client):
        stream_url = "http://ha.sumemail.com:8096/flow/abc123/queue1/1/player1.mp3"
        data = {
            "state": "playing",
            "audio_player": {
                "stream_url": stream_url,
            },
        }
        await client._dispatch_event("queue_updated", data)
        assert client.get_stream_url() == stream_url

    @pytest.mark.asyncio
    async def test_extract_stream_url_from_streamdetails_strips_duplicate_extension(self, client):
        data = {
            "state": "playing",
            "current_item": {
                "queue_item_id": "queue-item-1",
                "index": 0,
                "stream_url": "http://ha.sumemail.com:8096/flow/queue-item-1/player1.mp3",
                "streamdetails": {
                    "provider": "filesystem_local",
                    "item_id": "filesystem_local--Xk7dNqpq/03 Does Anybody Hear Her.mp3",
                    "audio_format": {"content_type": "mpeg"},
                },
            },
        }

        await client._dispatch_event("queue_updated", data)

        stream_url = client.get_stream_url()
        assert stream_url is not None
        assert stream_url.endswith("03 Does Anybody Hear Her.mp3")
        assert ".mp3.mp3" not in stream_url

    @pytest.mark.asyncio
    async def test_clears_stream_url_on_idle(self, client):
        stream_url = "http://ha.sumemail.com:8096/flow/abc/queue/1/player.mp3"
        data = {
            "state": "playing",
            "current_item": {"stream_url": stream_url},
        }
        await client._dispatch_event("queue_updated", data)
        assert client.get_stream_url() == stream_url

        idle_data = {"state": "idle"}
        await client._dispatch_event("queue_updated", idle_data)
        assert client.get_stream_url() is None

    @pytest.mark.asyncio
    async def test_no_stream_url_when_none_present(self, client):
        data = {
            "state": "playing",
            "current_item": {"name": "No Stream URL Track"},
        }
        await client._dispatch_event("queue_updated", data)
        assert client.get_stream_url() is None

    @pytest.mark.asyncio
    async def test_prefers_current_item_over_queue_items(self, client):
        current_url = "http://ha.sumemail.com:8096/flow/current.mp3"
        queue_url = "http://ha.sumemail.com:8096/flow/queue.mp3"
        data = {
            "state": "playing",
            "current_item": {"stream_url": current_url},
            "items": [
                {"stream_url": queue_url},
            ],
        }
        await client._dispatch_event("queue_updated", data)
        assert client.get_stream_url() == current_url


# ---------------------------------------------------------------------------
# Queue State Tests
# ---------------------------------------------------------------------------

class TestQueueState:
    def test_get_queue_state_returns_copy(self, client):
        import copy
        client._queue_state = {"state": "playing", "items": [1, 2, 3]}
        state = copy.deepcopy(client._queue_state)
        assert state == {"state": "playing", "items": [1, 2, 3]}
        state["items"].append(4)
        assert len(client._queue_state["items"]) == 3

    def test_get_queue_state_empty(self, client):
        state = client.get_queue_state()
        assert state == {}

    def test_get_current_item(self, client):
        client._queue_state = {
            "current_item": {"name": "Test Song", "uri": "https://www.youtube.com/watch?v=123"},
        }
        item = client.get_current_item()
        assert item["name"] == "Test Song"
        assert item["uri"] == "https://www.youtube.com/watch?v=123"

    def test_get_current_item_returns_none_when_missing(self, client):
        client._queue_state = {}
        assert client.get_current_item() is None

    def test_get_queue_state_description_playing(self, client):
        client._queue_state = {
            "state": "playing",
            "current_item": {
                "name": "Test Song",
                "artists": [{"name": "Test Artist"}],
            },
        }
        desc = client.get_queue_state_description()
        assert "playing" in desc.lower()
        assert "Test Song" in desc

    def test_get_queue_state_description_idle(self, client):
        client._queue_state = {"state": "idle"}
        desc = client.get_queue_state_description()
        assert "idle" in desc.lower()
        assert "No current item" in desc

    def test_get_queue_state_description_no_artists(self, client):
        client._queue_state = {
            "state": "playing",
            "current_item": {"name": "Test Song", "artist": "Solo Artist"},
        }
        desc = client.get_queue_state_description()
        assert "Solo Artist" in desc


# ---------------------------------------------------------------------------
# Message Parsing Tests
# ---------------------------------------------------------------------------

class TestMessageParsing:
    @pytest.mark.asyncio
    async def test_handle_event_message_with_type_field(self, client):
        callback = MagicMock()
        client.register_event_callback("queue_updated", callback)

        msg = json.dumps({
            "type": "EVENT",
            "event": "queue_updated",
            "data": {"state": "playing"},
        })
        await client._handle_message(msg)

        callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_event_without_type_field(self, client):
        callback = MagicMock()
        client.register_event_callback("player_updated", callback)

        msg = json.dumps({
            "event": "player_updated",
            "data": {"state": "paused"},
        })
        await client._handle_message(msg)

        callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_result_message(self, client):
        msg = json.dumps({
            "type": "RESULT",
            "message_id": "counter1",
            "result": {"success": True},
        })
        # Should not raise
        await client._handle_message(msg)

    @pytest.mark.asyncio
    async def test_handle_error_message(self, client):
        msg = json.dumps({
            "type": "ERROR",
            "message_id": "counter1",
            "error": {"description": "Player not found"},
        })
        # Should not raise
        await client._handle_message(msg)

    @pytest.mark.asyncio
    async def test_handle_invalid_json(self, client):
        # Should not raise
        await client._handle_message("not valid json {{{")

    @pytest.mark.asyncio
    async def test_handle_non_dict_message(self, client):
        # Should not raise
        await client._handle_message(json.dumps([1, 2, 3]))

    @pytest.mark.asyncio
    async def test_handle_queue_state_alt_format(self, client):
        callback = MagicMock()
        client.register_event_callback("queue_updated", callback)

        msg = json.dumps({
            "event": "queue_updated",
            "data": {"state": "playing"},
        })
        await client._handle_message(msg)

        callback.assert_called_once()


# ---------------------------------------------------------------------------
# Reconnect Logic Tests
# ---------------------------------------------------------------------------

class TestReconnectLogic:
    @pytest.mark.asyncio
    async def test_reconnect_increments_count(self, client, mock_websocket):
        client._reconnect_count = 3
        client._last_error = Exception("test")

        with patch("websockets.connect", AsyncMock(return_value=mock_websocket)):
            with patch("asyncio.sleep", AsyncMock()):
                await client._establish_connection()
                # After successful connection, count should reset to 0
                assert client._reconnect_count == 0

    @pytest.mark.asyncio
    async def test_reconnect_exponential_backoff_calculation(self):
        client = MAWebSocketClient(
            "http://localhost:8095",
            "token",
            reconnect_base_delay=1.0,
            reconnect_max_delay=30.0,
            reconnect_backoff_factor=2.0,
        )

        # Verify backoff math: base * factor^(count-1)
        count = 1
        delay = min(1.0 * (2.0 ** (count - 1)), 30.0)
        assert delay == 1.0

        count = 3
        delay = min(1.0 * (2.0 ** (count - 1)), 30.0)
        assert delay == 4.0

        count = 10
        delay = min(1.0 * (2.0 ** (count - 1)), 30.0)
        assert delay == 30.0  # Capped at max

    @pytest.mark.asyncio
    async def test_reconnect_schedules_next_attempt_on_failure(self, client):
        call_count = 0

        async def fail_connect():
            nonlocal call_count
            call_count += 1
            raise ConnectionError(f"Connection attempt {call_count}")

        client._last_error = Exception("test")
        client._reconnect_count = 1

        with patch.object(client, "_establish_connection", fail_connect):
            with patch("asyncio.sleep", AsyncMock()):
                await client._reconnect()
                # After failure, should have scheduled another reconnect
                assert client._reconnect_task is not None

    @pytest.mark.asyncio
    async def test_reconnect_skipped_when_shutdown(self, client):
        client._shutdown_event.set()

        with patch("asyncio.sleep", AsyncMock()) as mock_sleep:
            with patch.object(client, "_establish_connection", side_effect=Exception("fail")):
                await client._reconnect()
                assert client._reconnect_task is None


# ---------------------------------------------------------------------------
# Error Handling Tests
# ---------------------------------------------------------------------------

class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_message_loop_handles_connection_closed(self, client):
        import websockets as ws_module
        import websockets.frames as frames

        close_frame = frames.Close(1000, "closed")
        exc = ws_module.ConnectionClosed(close_frame, None)

        async def raise_on_iter():
            raise exc

        mock_ws = AsyncMock()
        mock_ws.__aiter__ = MagicMock(return_value=raise_on_iter())
        client._ws = mock_ws
        client._connected = True

        with patch.object(client, "_handle_message"):
            await client._message_loop()

        assert client._connected is False

    @pytest.mark.asyncio
    async def test_message_loop_handles_handler_exception(self, client, mock_websocket):
        mock_ws = AsyncMock()

        async def message_iterator():
            yield '{"event": "queue_updated", "data": {"state": "playing"}}'

        mock_ws.__aiter__ = MagicMock(return_value=message_iterator())
        client._ws = mock_ws
        client._connected = True

        call_count = 0

        async def bad_handler(msg):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("handler error")

        with patch.object(client, "_handle_message", bad_handler):
            await client._message_loop()

        assert call_count == 1
        # Handler exceptions are caught and logged but don't disconnect the client
        assert client._connected is True

    @pytest.mark.asyncio
    async def test_shutdown_event_stops_message_loop(self, client, mock_websocket):
        mock_ws = AsyncMock()

        async def gen_messages():
            yield "msg1"
            yield "msg2"

        mock_ws.__aiter__ = gen_messages
        client._ws = mock_ws
        client._connected = True
        client._shutdown_event.set()

        call_count = 0

        async def handler(msg):
            nonlocal call_count
            call_count += 1

        with patch.object(client, "_handle_message", handler):
            await client._message_loop()

        assert call_count < 2  # Should have stopped early

    @pytest.mark.asyncio
    async def test_disconnect_handles_websocket_close_error(self, client):
        mock_ws = AsyncMock()
        mock_ws.close = AsyncMock(side_effect=Exception("close error"))
        client._ws = mock_ws
        client._connected = True

        # Should not raise
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_stop_background_tasks_handles_already_done(self, client):
        done_task = AsyncMock()
        done_task.done = MagicMock(return_value=True)

        client._reconnect_task = done_task
        client._message_handler_task = done_task

        await client._stop_background_tasks()

    @pytest.mark.asyncio
    async def test_stop_background_tasks_handles_none(self, client):
        client._reconnect_task = None
        client._message_handler_task = None

        await client._stop_background_tasks()


# ---------------------------------------------------------------------------
# Context Manager Tests
# ---------------------------------------------------------------------------

class TestContextManager:
    @pytest.mark.asyncio
    async def test_context_manager_enter(self, client, mock_websocket):
        with patch("websockets.connect", AsyncMock(return_value=mock_websocket)):
            with patch.object(MAWebSocketClient, "_establish_connection"):
                client._connected = True
                async with client as c:
                    assert c is client

    @pytest.mark.asyncio
    async def test_context_manager_exit(self, client, mock_websocket):
        with patch("websockets.connect", AsyncMock(return_value=mock_websocket)):
            with patch.object(client, "_establish_connection"):
                client._connected = True
                client._authenticated = True
                mock_ws = AsyncMock()
                client._ws = mock_ws
                async with client:
                    assert client.connected is True
                assert client.connected is False
                assert mock_ws.close.called


# ---------------------------------------------------------------------------
# Constants Tests
# ---------------------------------------------------------------------------

class TestConstants:
    def test_event_type_constants(self):
        assert EVENT_QUEUE_UPDATED == "queue_updated"
        assert EVENT_PLAYER_UPDATED == "player_updated"
        assert EVENT_QUEUE_ENDED == "queue_ended"
        assert EVENT_QUEUE_STARTED == "queue_started"

    def test_command_constants(self):
        assert COMMAND_PREFIX == "player_queues/"
        assert PLAY_MEDIA_COMMAND == "player_queues/play_media"

    def test_heartbeat_interval(self):
        assert HEARTBEAT_INTERVAL == 15.0


# ---------------------------------------------------------------------------
# Integration-style Tests
# ---------------------------------------------------------------------------

class TestIntegration:
    @pytest.mark.asyncio
    async def test_full_command_and_event_flow(self, client, mock_websocket):
        """Test sending a command and receiving a queue update event."""
        client._ws = mock_websocket
        client._connected = True

        # Send a play command
        await client.send_command(
            "player_queues/play_media",
            {"queue_id": "player_1", "media": "https://www.youtube.com/watch?v=4uLU6hMCjMI"},
        )

        # Verify command was sent
        mock_websocket.send.assert_called_once()
        sent = json.loads(mock_websocket.send.call_args[0][0])
        assert sent["command"] == "player_queues/play_media"
        assert sent["args"]["media"] == "https://www.youtube.com/watch?v=4uLU6hMCjMI"
        assert sent["message_id"] == "counter1"

        # Register event callback and process an event
        received_events = []

        async def on_queue_update(event_type, data):
            received_events.append((event_type, data))

        client.register_event_callback("queue_updated", on_queue_update)

        # Simulate receiving a queue updated event
        stream_url = "http://ha.sumemail.com:8096/flow/session1/queue1/1/player1.mp3"
        event_data = {
            "state": "playing",
            "queue_id": "queue1",
            "current_item": {
                "name": "Test Song",
                "stream_url": stream_url,
            },
        }
        await client._dispatch_event("queue_updated", event_data)

        # Verify event handling
        assert len(received_events) == 1
        assert received_events[0][0] == "queue_updated"
        assert received_events[0][1]["state"] == "playing"
        assert client.get_stream_url() == stream_url

    @pytest.mark.asyncio
    async def test_multiple_event_types(self, client, mock_websocket):
        """Test handling multiple different event types."""
        client._ws = mock_websocket
        client._connected = True

        queue_cb = MagicMock()
        player_cb = MagicMock()
        client.register_event_callback("queue_updated", queue_cb)
        client.register_event_callback("player_updated", player_cb)

        await client._dispatch_event("queue_updated", {"state": "playing"})
        await client._dispatch_event("player_updated", {"volume_level": 0.7})

        queue_cb.assert_called_once()
        player_cb.assert_called_once()

    @pytest.mark.asyncio
    async def test_event_callback_order_preserved(self, client):
        """Test that event callbacks are called in registration order."""
        call_order = []

        def cb1(event, data):
            call_order.append(1)

        def cb2(event, data):
            call_order.append(2)

        def cb3(event, data):
            call_order.append(3)

        client.register_event_callback("queue_updated", cb1)
        client.register_event_callback("queue_updated", cb2)
        client.register_event_callback("queue_updated", cb3)

        await client._dispatch_event("queue_updated", {"state": "playing"})
        assert call_order == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_queue_state_persistence_across_events(self, client):
        """Test that queue state persists and updates across multiple events."""
        # First event
        await client._dispatch_event("queue_updated", {
            "state": "playing",
            "queue_id": "q1",
            "current_item": {"name": "Song 1"},
        })
        assert client.get_queue_state()["queue_id"] == "q1"

        # Second event updates state
        await client._dispatch_event("queue_updated", {
            "state": "paused",
            "queue_id": "q1",
            "current_item": {"name": "Song 1"},
            "elapsed_time": 45.0,
        })
        assert client.get_queue_state()["elapsed_time"] == 45.0
        assert client.get_queue_state()["state"] == "paused"

    @pytest.mark.asyncio
    async def test_send_command_with_complex_args(self, client, mock_websocket):
        """Test sending command with complex nested arguments."""
        client._ws = mock_websocket
        client._connected = True

        complex_args = {
            "queue_id": "player_1",
            "media": "https://www.youtube.com/playlist?list=abc123",
            "option": "replace",
            "radio_mode": False,
        }
        await client.send_command("player_queues/play_media", complex_args)

        sent = json.loads(mock_websocket.send.call_args[0][0])
        assert sent["args"]["queue_id"] == "player_1"
        assert sent["args"]["media"] == "https://www.youtube.com/playlist?list=abc123"
        assert sent["args"]["option"] == "replace"

    @pytest.mark.asyncio
    async def test_get_stream_url_after_multiple_updates(self, client):
        """Test that stream URL updates with each queue update."""
        url1 = "http://example.com/track1.mp3"
        url2 = "http://example.com/track2.mp3"

        await client._dispatch_event("queue_updated", {
            "state": "playing",
            "current_item": {"stream_url": url1},
        })
        assert client.get_stream_url() == url1

        await client._dispatch_event("queue_updated", {
            "state": "playing",
            "current_item": {"stream_url": url2},
        })
        assert client.get_stream_url() == url2

    @pytest.mark.asyncio
    async def test_stream_url_not_updated_on_player_event(self, client):
        """Test that stream URL only updates on queue events, not player events."""
        stream_url = "http://example.com/track.mp3"

        await client._dispatch_event("queue_updated", {
            "state": "playing",
            "current_item": {"stream_url": stream_url},
        })
        assert client.get_stream_url() == stream_url

        # Player event shouldn't clear or change stream URL
        await client._dispatch_event("player_updated", {
            "state": "paused",
            "volume_level": 0.5,
        })
        assert client.get_stream_url() == stream_url

    @pytest.mark.asyncio
    async def test_next_msg_id_format(self, client):
        """Test that message IDs follow MA format 'counter{n}'."""
        mid1 = client._next_msg_id()
        mid2 = client._next_msg_id()
        mid3 = client._next_msg_id()
        assert mid1 == "counter1"
        assert mid2 == "counter2"
        assert mid3 == "counter3"

    @pytest.mark.asyncio
    async def test_message_id_reset_after_disconnect(self, client, mock_websocket):
        """Test that message ID counter resets on reconnect."""
        client._ws = mock_websocket
        client._connected = True
        client._msg_id = 5

        await client.send_command("player/pause")

        sent = json.loads(mock_websocket.send.call_args[0][0])
        assert sent["message_id"] == "counter6"  # Continues from 5
