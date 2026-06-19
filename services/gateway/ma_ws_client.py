"""
Music Assistant WebSocket client for the Jarvis gateway.

Connects to MA's WebSocket API for real-time queue state updates and
command dispatch. Handles authentication, auto-reconnect, and event
processing.

Based on the official MA websocket_client.py implementation:
- Token is passed via URL query parameter (?token=xxx)
- Server-initiated heartbeats (websockets library handles ping/pong automatically)
- Message IDs follow MA format: f"counter{num}"
- Events arrive as clean JSON: {"event": "queue_updated", "data": {...}}

Usage:
    client = MAWebSocketClient(mass_url, mass_token)
    await client.connect()
    client.register_event_callback("queue_updated", handle_queue_updated)
    await client.send_command("player_queues/play_media", {"queue_id": "player_id", "media": "youtube.com/watch?v=..."})
    # ... use the client ...
    await client.disconnect()
"""
import asyncio
import inspect
import json
import logging
import time
from typing import Any, Callable, Dict, Optional

import websockets

log = logging.getLogger("gateway.ma_ws")

# Event types we care about
EVENT_QUEUE_UPDATED = "queue_updated"
EVENT_PLAYER_UPDATED = "player_updated"
EVENT_QUEUE_ENDED = "queue_ended"
EVENT_QUEUE_STARTED = "queue_started"
EVENT_QUEUE_VOLATILE_UPDATED = "queue_volatile_updated"

# Reconnect settings
RECONNECT_BASE_DELAY = 1.0
RECONNECT_MAX_DELAY = 30.0
RECONNECT_BACKOFF_FACTOR = 2.0
RECONNECT_JITTER = 0.5

# Heartbeat: MA server pings us; websockets library replies with pong automatically.
# We do NOT send our own pings — the MA server expects to initiate heartbeat.
# The websockets library's default ping_interval (20s) is sufficient for keep-alive.
HEARTBEAT_INTERVAL = 15.0

# Command format
COMMAND_PREFIX = "player_queues/"
PLAY_MEDIA_COMMAND = f"{COMMAND_PREFIX}play_media"

EventCallback = Callable[[str, Dict[str, Any]], None]


class MAWebSocketClient:
    """
    Async WebSocket client for Music Assistant's WebSocket API.

    Provides:
    - Persistent connection with auto-reconnect and exponential backoff
    - Authentication with MA's JWT token (via URL query param)
    - Command dispatch (player_queues/play_media, player_queues/pause, etc.)
    - Event callbacks for queue/player state changes
    - Stream URL extraction from QUEUE_UPDATED events
    """

    def __init__(
        self,
        mass_url: str,
        mass_token: str,
        reconnect_base_delay: float = RECONNECT_BASE_DELAY,
        reconnect_max_delay: float = RECONNECT_MAX_DELAY,
        reconnect_backoff_factor: float = RECONNECT_BACKOFF_FACTOR,
    ):
        """
        Initialize the MA WebSocket client.

        Args:
            mass_url: MA base URL (e.g., "http://ha.sumemail.com:8095")
            mass_token: JWT authentication token
            reconnect_base_delay: Base delay for reconnect attempts (seconds)
            reconnect_max_delay: Maximum delay between reconnect attempts (seconds)
            reconnect_backoff_factor: Multiplier for exponential backoff
        """
        self._mass_url = mass_url.rstrip("/")
        self._mass_token = mass_token

        # Token is passed as a query parameter — this is how MA's WebSocket
        # middleware expects authentication (not via headers).
        # Convert http:// to ws:// and https:// to wss:// for WebSocket URLs.
        ws_scheme = "wss://" if self._mass_url.startswith("https://") else "ws://"
        http_base = self._mass_url.replace("http://", "").replace("https://", "")
        self._ws_url = f"{ws_scheme}{http_base}/ws?token={mass_token}"
        self._reconnect_base_delay = reconnect_base_delay
        self._reconnect_max_delay = reconnect_max_delay
        self._reconnect_backoff_factor = reconnect_backoff_factor

        # Connection state
        self._ws: Any = None
        self._connected = False
        self._authenticated = False
        self._server_info: Optional[Dict[str, Any]] = None
        self._auth_event = asyncio.Event()
        self._last_error: Optional[Exception] = None
        self._ma_error_code: Optional[str] = None
        self._ma_error_details: Optional[str] = None
        self._reconnect_count = 0
        self._reconnect_task: Optional[asyncio.Task] = None
        self._message_handler_task: Optional[asyncio.Task] = None
        self._shutdown_event = asyncio.Event()

        # Queue state tracking
        self._queue_state: Dict[str, Any] = {}
        self._stream_url: Optional[str] = None

        # Event callbacks
        self._event_callbacks: Dict[str, list] = {}

        # Message ID counter for request/response correlation
        self._msg_id = 0

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        """Whether the client is currently connected and authenticated."""
        return self._connected and self._authenticated and self._ws is not None

    @property
    def authenticated(self) -> bool:
        """Whether the client has completed MA authentication."""
        return self._authenticated

    @property
    def server_info(self) -> Optional[Dict[str, Any]]:
        """The server info received from MA after authentication."""
        return self._server_info

    @property
    def is_connected(self) -> bool:
        """Alias for connected property."""
        return self.connected

    @property
    def ws_url(self) -> str:
        """The WebSocket URL being connected to."""
        return self._ws_url

    @property
    def last_error(self) -> Optional[Exception]:
        """The last error that occurred on the connection."""
        return self._last_error

    @property
    def reconnect_count(self) -> int:
        """Number of reconnect attempts made."""
        return self._reconnect_count

    @property
    def queue_state(self) -> Dict[str, Any]:
        """The latest queue state from MA."""
        return self._queue_state

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """
        Connect to MA WebSocket and authenticate.

        Raises:
            ConnectionError: If the connection or authentication fails.
        """
        if self._connected:
            log.warning("[MA-WS] Already connected, skipping connect")
            return

        self._shutdown_event.clear()
        await self._establish_connection()

    async def disconnect(self) -> None:
        """
        Gracefully disconnect from MA WebSocket.
        """
        self._shutdown_event.set()
        await self._stop_background_tasks()

        if self._ws:
            try:
                await self._ws.close(code=1000, reason="Gateway shutting down")
            except Exception:
                pass
            self._ws = None

        self._connected = False
        log.info("[MA-WS] Disconnected")

    async def send_command(self, command: str, args: Optional[Dict[str, Any]] = None) -> None:
        """
        Send a command to MA via WebSocket.

        Args:
            command: Command name (e.g., "player_queues/play_media", "player_queues/pause")
            args: Command arguments dict

        Raises:
            ConnectionError: If not connected.
        """
        if not self._connected:
            raise ConnectionError("MA WebSocket client is not connected")

        msg_id = self._next_msg_id()
        payload: Dict[str, Any] = {
            "message_id": msg_id,
            "command": command,
        }
        if args is not None:
            payload["args"] = args

        try:
            ws = self._ws
            await ws.send(json.dumps(payload))
            log.info(f"[MA-WS] Sent command '{command}' (msg_id={msg_id})")
        except Exception as e:
            log.error(f"[MA-WS] Failed to send command '{command}': {e}")
            raise

    async def send_command_no_wait(self, command: str, args: Optional[Dict[str, Any]] = None) -> None:
        """
        Send a command without waiting for a response (fire-and-forget).

        Args:
            command: Command name
            args: Command arguments dict
        """
        if not self._connected:
            raise ConnectionError("MA WebSocket client is not connected")

        msg_id = self._next_msg_id()
        payload: Dict[str, Any] = {
            "message_id": msg_id,
            "command": command,
        }
        if args is not None:
            payload["args"] = args

        try:
            ws = self._ws
            await ws.send(json.dumps(payload))
            log.info(f"[MA-WS] Sent command '{command}' (no-wait, msg_id={msg_id})")
        except Exception as e:
            log.error(f"[MA-WS] Failed to send command '{command}': {e}")
            raise

    def register_event_callback(self, event_type: str, callback: EventCallback) -> None:
        """
        Register a callback for a specific event type.

        Args:
            event_type: Event type string (e.g., "queue_updated")
            callback: Async or sync callable receiving (event_type, data)
        """
        if event_type not in self._event_callbacks:
            self._event_callbacks[event_type] = []
        self._event_callbacks[event_type].append(callback)
        log.debug(f"[MA-WS] Registered callback for '{event_type}'")

    def unregister_event_callback(self, event_type: str, callback: EventCallback) -> None:
        """
        Remove a previously registered event callback.

        Args:
            event_type: Event type string
            callback: The callback to remove
        """
        if event_type in self._event_callbacks:
            callbacks = self._event_callbacks[event_type]
            if callback in callbacks:
                callbacks.remove(callback)
                log.debug(f"[MA-WS] Unregistered callback for '{event_type}'")

    def get_stream_url(self) -> Optional[str]:
        """
        Get the latest resolved stream URL from the most recent QUEUE_UPDATED event.

        Returns:
            Stream URL string, or None if not yet available.
        """
        return self._stream_url

    def get_queue_state(self) -> Dict[str, Any]:
        """
        Get the current queue state snapshot.

        Returns:
            Dict containing queue state (state, current_item, queue_id, etc.)
        """
        return dict(self._queue_state)

    def get_current_item(self) -> Optional[Dict[str, Any]]:
        """
        Get the currently playing media item from queue state.

        Returns:
            Dict with item details, or None.
        """
        current = self._queue_state.get("current_item")
        if isinstance(current, dict):
            return current
        return None

    def has_error(self) -> bool:
        """Whether an MA error response was received."""
        return self._ma_error_code is not None

    def get_ma_error(self) -> Optional[Dict[str, str]]:
        """Get the last MA error if one was received."""
        if self._ma_error_code:
            return {"code": self._ma_error_code, "details": self._ma_error_details or ""}
        return None

    def get_queue_state_description(self) -> str:
        """
        Get a human-readable description of the current queue state.

        Returns:
            String description of queue state.
        """
        state = self._queue_state.get("state", "unknown")
        current = self.get_current_item()
        if current:
            name = current.get("name", "Unknown")
            artists = current.get("artists")
            if isinstance(artists, list) and artists:
                artist = artists[0].get("name", "") if isinstance(artists[0], dict) else ""
            else:
                artist = current.get("artist", "")
            return f"[{state}] {name}" + (f" by {artist}" if artist else "")
        return f"[{state}] No current item"

    # ------------------------------------------------------------------
    # Internal - Connection Management
    # ------------------------------------------------------------------

    async def _establish_connection(self) -> None:
        """
        Establish WebSocket connection and authenticate with MA.

        Authentication is done via the token query parameter in the WebSocket URL.
        The MA server's WebSocket middleware validates the token on connect.
        After connection, a server_info command is sent to complete authentication.
        """
        try:
            log.info(f"[MA-WS] Connecting to {self._ws_url}")
            # Token is in the URL query string; no auth header needed.
            self._ws = await websockets.connect(
                self._ws_url,
                ping_interval=HEARTBEAT_INTERVAL,
                ping_timeout=10.0,
                close_timeout=5.0,
            )

            self._connected = True
            self._reconnect_count = 0
            log.info("[MA-WS] Connection established")

            # Start background message handler
            self._message_handler_task = asyncio.create_task(self._message_loop())

           # Wait for MA server_info response (completes authentication)
            try:
                await asyncio.wait_for(self._auth_event.wait(), timeout=5.0)
                log.info("[MA-WS] Authentication complete via server_info")
                # Send explicit auth command with token to complete MA auth
                try:
                    await self.send_command("auth", {"token": self._mass_token})
                    log.info("[MA-WS] Sent auth command with token")
                except Exception as e:
                    log.warning(f"[MA-WS] Auth command failed: {e}")
            except asyncio.TimeoutError:
                log.warning("[MA-WS] Timeout waiting for server_info, proceeding anyway")

        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._last_error = e
            self._connected = False
            log.error(f"[MA-WS] Connection failed: {e}")
            raise ConnectionError(f"Failed to connect to MA WebSocket: {e}")

    async def _message_loop(self) -> None:
        """
        Main message processing loop. Receives messages from MA and
        dispatches events to registered callbacks.

        MA WebSocket messages are simple JSON:
        - Events: {"event": "queue_updated", "data": {...}}
        - Results: {"type": "RESULT", "message_id": "counter123", "result": {...}}
        - Errors: {"type": "ERROR", "message_id": "counter123", "error": {...}}
        """
        try:
            async for message in self._ws:
                if self._shutdown_event.is_set():
                    break

                try:
                    await self._handle_message(message)
                except Exception as e:
                    log.error(f"[MA-WS] Error handling message: {e}", exc_info=True)
        except websockets.ConnectionClosed as e:
            log.warning(f"[MA-WS] Connection closed: {e}")
            self._connected = False
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error(f"[MA-WS] Message loop error: {e}", exc_info=True)
            self._last_error = e
            self._connected = False

    async def _handle_message(self, message: str) -> None:
        """
        Parse and dispatch an incoming WebSocket message.

        Args:
            message: Raw JSON string from WebSocket
        """
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            log.warning(f"[MA-WS] Invalid JSON received: {message[:200]}")
            return

        if not isinstance(data, dict):
            log.debug(f"[MA-WS] Non-dict message: {type(data)}")
            return

        # Determine message type
        msg_type = data.get("type")
        event_type = data.get("event")

        # ── Event messages ──────────────────────────────────────────────
        # MA sends events with or without a "type" field:
        #   {"event": "queue_updated", "data": {...}}
        #   {"type": "EVENT", "event": "player_updated", "data": {...}}
        if event_type:
            evt_data = data.get("data", {})
            log.info(f"[MA-WS] Received event: {event_type}, keys={list(evt_data.keys()) if isinstance(evt_data, dict) else type(evt_data).__name__}")
            await self._dispatch_event(event_type, evt_data)

        # ── Result messages ─────────────────────────────────────────────
        # Response to a command we sent
        elif msg_type == "RESULT":
            result = data.get("result", {})
            log.info(f"[MA-WS] Received result: msg_id={data.get('message_id')}, result_keys={list(result.keys()) if isinstance(result, dict) else type(result).__name__}")

        # ── Error messages ──────────────────────────────────────────────
        elif msg_type == "ERROR":
            error = data.get("error", {})
            log.error(f"[MA-WS] Received error: msg_id={data.get('message_id')}, error={json.dumps(error) if isinstance(error, dict) else error}")

        # ── MA server_info response (sent automatically after connect) ────
        elif data.get("server_id") is not None and data.get("server_version") is not None:
            log.info(f"[MA-WS] Received MA server_info: name={data.get('name')}, version={data.get('server_version')}")
            self._server_info = data
            self._authenticated = True
            self._auth_event.set()

        # ── MA error responses without type field ───────────────────────
        elif data.get("error_code") is not None or ("message_id" in data and "details" in data):
            error_code = data.get("error_code", "unknown")
            details = data.get("details", "")
            msg_id = data.get("message_id")
            self._ma_error_code = error_code
            self._ma_error_details = str(details)
            self._last_error = RuntimeError(f"MA error: {error_code}: {details}")
            log.error(f"[MA-WS] MA error response (no type field): msg_id={msg_id}, error_code={error_code!r}, details={details!r}")

        # ── Unknown message types ───────────────────────────────────────
        else:
            log.warning(f"[MA-WS] Unknown message type: msg_type={msg_type!r}, event={event_type!r}, all_keys={list(data.keys())}")

    async def _dispatch_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """
        Dispatch an event to all registered callbacks.

        Also updates internal queue state and stream URL tracking.

        Args:
            event_type: The event type string
            data: The event data payload
        """
        # Update queue state for queue-related events
        if event_type == EVENT_QUEUE_UPDATED:
            self._queue_state = data
            self._extract_stream_url(data)
        elif event_type == EVENT_PLAYER_UPDATED:
            self._queue_state = data
        elif event_type in (EVENT_QUEUE_ENDED, EVENT_QUEUE_STARTED, EVENT_QUEUE_VOLATILE_UPDATED):
            self._queue_state = data
            self._extract_stream_url(data)

        # Dispatch to callbacks
        if event_type in self._event_callbacks:
            callbacks = list(self._event_callbacks[event_type])
            for callback in callbacks:
                try:
                    if inspect.iscoroutinefunction(callback):
                        await callback(event_type, data)
                    else:
                        callback(event_type, data)
                except Exception as e:
                    log.error(f"[MA-WS] Callback error for '{event_type}': {e}", exc_info=True)

    def _extract_stream_url(self, data: Dict[str, Any]) -> None:
        """
        Extract the resolved stream URL from queue state data.

        MA resolves URIs to actual stream URLs in the queue state.
        The URL pattern is typically:
        http://{mass_url}:8096/flow/{session_id}/{queue_id}/{queue_item_id}/{player_id}.mp3

        Search order (highest priority first):
        1. current_item.media_item.stream_url
        2. current_item.stream_url
        3. items[*].stream_url
        4. audio_player.stream_url
        5. current_item.streamdetails (for opensubsonic/library tracks)

        Args:
            data: Queue state data from MA
        """
        log.info(f"[MA-WS] Extracting stream URL from queue state: keys={list(data.keys())}, state={data.get('state')}")

        # Check for stream_url in the current item
        current_item = data.get("current_item", {})
        if isinstance(current_item, dict):
            log.info(f"[MA-WS] current_item keys: {list(current_item.keys())}")
            media_item = current_item.get("media_item", {})
            stream_url = media_item.get("stream_url") if isinstance(media_item, dict) else None
            if not stream_url:
                stream_url = current_item.get("stream_url")
            if stream_url:
                self._stream_url = stream_url
                log.info(f"[MA-WS] Stream URL resolved: {stream_url[:100]}...")
                return

            # ── Construct stream URL from streamdetails for opensubsonic/library ──
            streamdetails = current_item.get("streamdetails", {})
            if isinstance(streamdetails, dict) and streamdetails.get("item_id"):
                provider = streamdetails.get("provider", "")
                item_id = streamdetails.get("item_id", "")
                content_type = streamdetails.get("audio_format", {}).get("content_type", "mp3")
                # Normalize content_type: mpeg -> mp3, flac -> flac, etc.
                ct_map = {"mpeg": "mp3", "mp4a": "m4a", "webm": "webm"}
                ext = ct_map.get(content_type, content_type or "mp3")
                # Try MA Flow API URL: /flow/{queue_id}/{queue_item_id}/{track_index}/{provider}/{item_id}.{ext}
                queue_id = data.get("queue_id", "")
                queue_item_id = current_item.get("queue_item_id", "")
                track_index = current_item.get("index", 0)
                http_base = self._mass_url.replace("http://", "").replace("https://", "")
                # Primary: Flow API URL (what MA web player uses)
                stream_url = f"http://{http_base}/flow/{queue_id}/{queue_item_id}/{track_index}/{provider}/{item_id}.{ext}"
                self._stream_url = stream_url
                log.info(f"[MA-WS] Stream URL from flow: {stream_url[:200]}")
                log.info(f"[MA-WS] streamdetails full: {json.dumps(streamdetails)[:500]}")
                log.info(f"[MA-WS] media_item keys: {list(media_item.keys()) if isinstance(media_item, dict) else 'none'}")
                return

        # Check for queue items with stream URLs
        queue_items = data.get("items", [])
        if isinstance(queue_items, list):
            for i, item in enumerate(queue_items):
                if isinstance(item, dict):
                    stream_url = item.get("stream_url")
                    if stream_url:
                        self._stream_url = stream_url
                        log.info(f"[MA-WS] Stream URL from queue item {i}: {stream_url[:100]}...")
                        return
                    if i == 0:
                        log.info(f"[MA-WS] First queue item keys: {list(item.keys())}")

        # Check for audio_player stream info
        audio_player = data.get("audio_player", {})
        if isinstance(audio_player, dict):
            log.info(f"[MA-WS] audio_player keys: {list(audio_player.keys())}")
            stream_url = audio_player.get("stream_url")
            if stream_url:
                self._stream_url = stream_url
                log.info(f"[MA-WS] Stream URL from audio_player: {stream_url[:100]}...")
                return

        # Clear stream URL if not found (may indicate stopped state)
        if data.get("state") == "idle":
            self._stream_url = None
            log.info(f"[MA-WS] Stream URL cleared (state=idle)")

    # ------------------------------------------------------------------
    # Internal - Reconnect Logic
    # ------------------------------------------------------------------

    async def _reconnect(self) -> None:
        """
        Attempt to reconnect with exponential backoff and jitter.
        """
        self._reconnect_count += 1
        delay = min(
            self._reconnect_base_delay * (self._reconnect_backoff_factor ** (self._reconnect_count - 1)),
            self._reconnect_max_delay,
        )
        # Add jitter to prevent thundering herd
        jitter = delay * RECONNECT_JITTER * (1.0 - 2.0 * hash(str(time.time())) % 1)
        delay = max(0.1, delay + jitter)

        log.warning(
            f"[MA-WS] Reconnect attempt {self._reconnect_count} in {delay:.1f}s "
            f"(last error: {self._last_error})"
        )

        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return

        try:
            await self._establish_connection()
        except Exception as e:
            self._last_error = e
            log.error(f"[MA-WS] Reconnect attempt {self._reconnect_count} failed: {e}")
            # Schedule another reconnect attempt
            if not self._shutdown_event.is_set():
                self._reconnect_task = asyncio.create_task(self._reconnect())

    async def _stop_background_tasks(self) -> None:
        """
        Cancel all background tasks gracefully.
        """
        for task in (self._reconnect_task, self._message_handler_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        self._reconnect_task = None
        self._message_handler_task = None

    # ------------------------------------------------------------------
    # Internal - Utilities
    # ------------------------------------------------------------------

    def _next_msg_id(self) -> str:
        """Generate a unique MA-format message ID: 'counter{n}'."""
        self._msg_id += 1
        return f"counter{self._msg_id}"

    # ------------------------------------------------------------------
    # Context Manager Support
    # ------------------------------------------------------------------

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect()
        return False

    # ------------------------------------------------------------------
    # String representation
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        status = "connected" if self._connected else "disconnected"
        return (
            f"<MAWebSocketClient {status} url={self._ws_url} "
            f"reconnects={self._reconnect_count} stream_url={'set' if self._stream_url else 'none'}>"
        )
