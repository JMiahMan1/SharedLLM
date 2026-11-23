"""Lightweight standalone async client for the Mobile Echo Reverse WSS Tunnel.

Tunnels a local ESPHome API (:26053) over HTTPS/WSS 443 to Jarvis from any
internet connection. Uses exponential backoff reconnect.

The auth token is this Echo's OWN per-device token, not an admin key. An admin mints
it once in Jarvis with ``POST /api/homehub/echo_tunnel/tokens/<node>``; the token is
shown once, stored on the Echo, and passed here through ``--token-env``.

CLI args:
  --server wss://...          WSS server URL (required)
  --node <name>               Tunnel node name (required)
  --token-env JARVIS_ECHO_TOKEN  Environment variable with the auth token (required)
  --target-host 127.0.0.1     Local target host (default: 127.0.0.1)
  --target-port 26053         Local target port (default: 26053)
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import random
import struct
import sys
from typing import Any

log = logging.getLogger("echo_mobile_tunnel")


# --- Frame encoding/decoding (same as homehub.echo_tunnel) ---

def encode_frame(frame_type: int, conn_id: int | None, payload: bytes | None) -> bytes:
    """Encode a binary frame: 1-byte type, optional 4-byte conn_id (big-endian),
    optional payload."""
    if frame_type not in (0x01, 0x02, 0x03, 0x04, 0x05):
        raise ValueError(f"Invalid frame type: 0x{frame_type:02x}")
    buf = bytearray()
    buf.append(frame_type)
    if conn_id is not None:
        buf += struct.pack(">I", conn_id)
    if payload is not None:
        buf.extend(payload)
    return bytes(buf)


def decode_frame(data: bytes) -> tuple[int, int | None, bytes | None]:
    """Decode a binary frame. Returns (frame_type, conn_id_or_None, payload_or_None)."""
    if len(data) < 1:
        raise ValueError("Frame too short")
    frame_type = data[0]
    offset = 1
    conn_id: int | None = None
    if len(data) - offset >= 4:
        conn_id = struct.unpack(">I", data[offset:offset + 4])[0]
        offset += 4
    payload = data[offset:] if len(data) > offset else None
    return frame_type, conn_id, payload


# --- Tunnel protocol handler ---

class TunnelProtocol:
    """Manages the WSS tunnel protocol for a single node.

    Connects to a Gateway /api/homehub/echo_tunnel/ws/{node} endpoint and pipes
    data between the local ESPHome API and the remote WebSocket.
    """

    def __init__(self, wss_url: str, node: str, token: str,
                 target_host: str, target_port: int):
        # Normalize the server URL to use /api/homehub/echo_tunnel/ws/{node}
        # The wss_url should be like wss://jarvis.local:443 (base URL without path)
        self.base_wss_url = wss_url.rstrip("/")
        self.node = node
        self.token = token
        self.target_host = target_host
        self.target_port = target_port
        self.websocket: Any = None
        self._running = False
        self._conn_map: dict[int, tuple[asyncio.StreamReader, asyncio.StreamWriter]] = {}  # conn_id -> (reader, writer)

    @property
    def ws_url(self) -> str:
        """Construct the full WebSocket URL with node and auth."""
        # Connect to Gateway's /api/homehub/echo_tunnel/ws/{node} with Authorization header
        base = self.base_wss_url
        if base.startswith("https://"):
            ws_base = "wss://" + base[len("https://"):]
        elif base.startswith("http://"):
            ws_base = "ws://" + base[len("http://"):]
        else:
            ws_base = base
        return f"{ws_base}/api/homehub/echo_tunnel/ws/{self.node}"

    async def run(self) -> None:
        """Run the tunnel: connect to WSS, handle frames, pipe data."""
        self._running = True

        url = self.base_wss_url
        if not url.startswith(("ws://", "wss://", "http://", "https://")):
            log.error("Server URL must start with ws://, wss://, http://, or https://")
            return

        backoff = 1.0
        max_backoff = 60.0

        while self._running:
            try:
                await self._connect_and_run()
                log.info("Tunnel connection closed normally")
                break
            except Exception as exc:
                log.warning("Tunnel connection error: %s", exc)
                if not self._running:
                    break
                await asyncio.sleep(min(backoff, max_backoff) + random.uniform(0, min(backoff, max_backoff)))
                backoff = min(backoff * 2, max_backoff)

    async def _connect_and_run(self) -> None:
        """Connect to WSS and run the frame handling loop."""
        import websockets

        ws_url = self.ws_url
        log.info("Connecting to %s", ws_url)

        # Connect with Authorization header (Bearer token)
        headers = {"Authorization": f"Bearer {self.token}"}

        async with websockets.connect(
            ws_url,
            additional_headers=headers,
        ) as ws:
            self.websocket = ws
            await self._handle_ws_frames(ws)

    async def _handle_ws_frames(self, ws: Any) -> None:
        """Handle incoming frames from the WebSocket."""
        try:
            while True:
                message = await ws.recv()
                if isinstance(message, str):
                    # Text message; ignore
                    continue
                elif isinstance(message, (bytes, bytearray)):
                    frame_type, conn_id, payload = decode_frame(message)

                    if frame_type == 0x01:  # OPEN
                        await self._handle_open(conn_id)

                    elif frame_type == 0x02:  # DATA
                        await self._handle_data(conn_id, payload)

                    elif frame_type == 0x03:  # CLOSE
                        await self._handle_close(conn_id)

                    elif frame_type == 0x04:  # PING
                        await self._handle_ping(conn_id)

                    elif frame_type == 0x05:  # PONG
                        log.debug("Received PONG frame conn_id=%s", conn_id)

                    else:
                        log.warning("Unknown frame type 0x%02x", frame_type)
                else:
                    log.warning("Unexpected message type from WS")
        except Exception as exc:
            log.warning("WS frame handling error: %s", exc)
        finally:
            # Clean up all connections
            await self._cleanup_all()

    async def _handle_open(self, conn_id: int | None) -> None:
        """Handle OPEN frame: connect to target ESPHome API."""
        if conn_id is None:
            log.warning("OPEN frame without conn_id")
            return

        log.debug("Received OPEN frame conn_id=%s", conn_id)

        if conn_id in self._conn_map:
            log.warning("Connection already exists for conn_id=%s", conn_id)
            return

        # Connect to target ESPHome API
        try:
            reader, writer = await asyncio.open_connection(
                self.target_host, self.target_port
            )
        except Exception as exc:
            log.warning("Failed to connect to ESPHome API at %s:%s for conn_id=%s: %s",
                        self.target_host, self.target_port, conn_id, exc)
            # Send CLOSE frame back to indicate failure
            close_frame = encode_frame(0x03, conn_id, None)
            with contextlib.suppress(Exception):
                await self.websocket.send(close_frame)
            return

        self._conn_map[conn_id] = (reader, writer)
        log.info("Connected to ESPHome API for conn_id=%s", conn_id)

        # Start piping: target -> WS (DATA frames)
        # Store task reference to avoid RUF006
        task = asyncio.create_task(self._pipe_target_to_ws(conn_id, reader, writer))
        # Task is tracked by the event loop; we don't need to keep a reference
        _ = task

    async def _handle_data(self, conn_id: int | None, payload: bytes | None) -> None:
        """Handle DATA frame: write to TCP connection."""
        if conn_id is None:
            log.warning("DATA frame without conn_id")
            return
        if conn_id not in self._conn_map:
            log.warning("DATA frame for unknown conn_id=%s", conn_id)
            return
        if not payload:
            return

        _, writer = self._conn_map[conn_id]
        try:
            writer.write(payload)
            await writer.drain()
        except Exception as exc:
            log.warning("Write to target failed for conn_id=%s: %s", conn_id, exc)
            await self._handle_close(conn_id)

    async def _handle_close(self, conn_id: int | None) -> None:
        """Handle CLOSE frame: close TCP connection."""
        if conn_id is None:
            return
        if conn_id not in self._conn_map:
            return

        _reader, writer = self._conn_map.pop(conn_id)
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        log.debug("Closed TCP connection for conn_id=%s", conn_id)

    async def _handle_ping(self, conn_id: int | None) -> None:
        """Handle PING frame: respond with PONG."""
        pong_frame = encode_frame(0x05, conn_id, None)
        with contextlib.suppress(Exception):
            await self.websocket.send(pong_frame)

    async def _pipe_target_to_ws(self, conn_id: int,
                                  reader: asyncio.StreamReader,
                                  writer: asyncio.StreamWriter) -> None:
        """Pipe data from target TCP connection to WS as DATA frames."""
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                data_frame = encode_frame(0x02, conn_id, data)
                try:
                    # Use ws.send() for binary data (websockets library)
                    await self.websocket.send(data_frame)
                except Exception:
                    break
        except Exception:
            pass
        finally:
            # Send CLOSE frame when target closes
            close_frame = encode_frame(0x03, conn_id, None)
            with contextlib.suppress(Exception):
                await self.websocket.send(close_frame)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            self._conn_map.pop(conn_id, None)

    async def _cleanup_all(self) -> None:
        """Close all active TCP connections."""
        for _conn_id, (_reader, writer) in list(self._conn_map.items()):
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
        self._conn_map.clear()


# --- CLI entry point ---

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mobile Echo Reverse WSS Tunnel CLI"
    )
    parser.add_argument(
        "--server",
        required=True,
        help="WSS server URL (e.g. wss://jarvis.local:443 or https://jarvis.local)",
    )
    parser.add_argument(
        "--node",
        required=True,
        help="Tunnel node name (e.g. home)",
    )
    parser.add_argument(
        "--token-env",
        required=True,
        help="Environment variable holding this Echo's own tunnel token "
             "(minted once by an admin via POST /api/homehub/echo_tunnel/tokens/<node>; "
             "e.g. JARVIS_ECHO_TOKEN). Never an admin key.",
    )
    parser.add_argument(
        "--target-host",
        default="127.0.0.1",
        help="Local target host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--target-port",
        type=int,
        default=26053,
        help="Local target port (default: 26053, ESPHome API port)",
    )

    args = parser.parse_args()

    # Get token from environment
    token_env = os.environ.get(args.token_env, "")
    if not token_env:
        print(f"Error: Environment variable {args.token_env} not set", file=sys.stderr)
        sys.exit(1)

    token = token_env

    # Don't log the token!
    log.info("Starting tunnel: server=%s node=%s target=%s:%s",
             args.server, args.node, args.target_host, args.target_port)

    async def run_tunnel() -> None:
        protocol = TunnelProtocol(
            wss_url=args.server,
            node=args.node,
            token=token,
            target_host=args.target_host,
            target_port=args.target_port,
        )
        await protocol.run()

    try:
        asyncio.run(run_tunnel())
    except KeyboardInterrupt:
        print("\nTunnel interrupted", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
