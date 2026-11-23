"""Echo Reverse WSS Tunnel — manages outbound WebSocket-over-HTTPS tunnels
that multiplex TCP connections (e.g. ESPHome API) over a single binary WS link.

Protocol (binary frames, 1-byte type header):
  0x01 OPEN  — conn_id: 4 bytes big-endian  (starts multiplexing on a new TCP session)
  0x02 DATA  — conn_id: 4 bytes + payload    (pipe data on that TCP session)
  0x03 CLOSE — conn_id: 4 bytes              (close that TCP session)
  0x04 PING                              (keep-alive)
  0x05 PONG                              (respond to PING)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import struct
from typing import Any

log = logging.getLogger("homehub.echo_tunnel")


# --- Module-level frame encode/decode (for import by tests and other modules) ---

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


class TunnelSession:
    """Represents one registered tunnel node with its TCP server and state."""

    def __init__(self, node: str, local_port: int, connected_at: float):
        self.node = node
        self.local_port = local_port
        self.connected_at = connected_at
        self.active_conns: set[int] = set()  # conn_ids currently multiplexed
        self._ws: Any = None  # websocket reference, set by the handler


class EchoTunnelManager:
    """Manages outbound WSS tunnels that reverse-tunnel LAN traffic over HTTPS 443.

    - register_tunnel(node, websocket): binds an asyncio TCP server on ('127.0.0.1', 0),
      records a TunnelSession, multiplexes TCP connections on 127.0.0.1:local_port over the WS,
      and cleanly closes server + connections on disconnect.
    - status() -> dict with active_tunnels list.
    - disconnect(node) -> bool: removes the tunnel and closes everything.
    """

    def __init__(self):
        # node -> TunnelSession
        self._sessions: dict[str, TunnelSession] = {}
        # conn_id -> {node, writer, reader} for piping DATA frames to TCP
        self._conn_map: dict[int, dict[str, Any]] = {}
        # Tracks next conn_id to assign
        self._next_conn_id: int = 1
        # Per-tunnel TCP servers: node -> asyncio.Server
        self._tcp_servers: dict[str, asyncio.Server] = {}
        self._lock = asyncio.Lock()

    def local_endpoint(self, node: str) -> tuple[str, int] | None:
        """127.0.0.1 and the tunnel's local port while a tunnel for node is live, else None."""
        session = self._sessions.get(node)
        if session is None:
            return None
        return ("127.0.0.1", session.local_port)

    # ---- Protocol frame encoding/decoding (use module-level functions) ----

    @staticmethod
    def encode_frame(frame_type: int, conn_id: int | None, payload: bytes | None) -> bytes:
        return encode_frame(frame_type, conn_id, payload)  # module-level function

    @staticmethod
    def decode_frame(data: bytes) -> tuple[int, int | None, bytes | None]:
        return decode_frame(data)  # module-level function

    # ---- Register / unregister ----

    async def register_tunnel(self, node: str, websocket) -> int:
        """Bind an asyncio TCP server on 127.0.0.1:0, record a TunnelSession.

        Returns the assigned local_port.

        The caller (gateway route) is responsible for admin auth before calling this.
        NOTE: We do NOT send an OPEN frame here. OPEN frames are sent only when
        an actual TCP client connects to the local port.
        """
        async with self._lock:
            # Free up any stale session for this node
            await self._disconnect_node_locked(node, force=True)

            # Create a TCP server bound to 127.0.0.1:0 (OS picks a free port)
            # Use a closure to capture the node for the TCP handler
            async def handle_tcp_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
                await self._handle_tcp_client(reader, writer, node)

            server = await asyncio.start_server(
                handle_tcp_client, "127.0.0.1", 0
            )
            actual_port = server.sockets[0].getsockname()[1]

            session = TunnelSession(node, actual_port, asyncio.get_event_loop().time())
            self._sessions[node] = session
            self._tcp_servers[node] = server

            # Store WS reference on the session for later frame broadcasting
            session._ws = websocket

            log.info(
                "Echo tunnel registered for node=%s on 127.0.0.1:%s", node, actual_port
            )
            return actual_port

    async def _disconnect_node_locked(self, node: str, force: bool = False) -> None:
        """Clean up a tunnel node: close TCP server, flush connections, remove session.

        Caller MUST hold self._lock.
        """
        server = self._tcp_servers.pop(node, None)
        session = self._sessions.pop(node, None)

        # Close the TCP server
        if server is not None:
            server.close()
            with contextlib.suppress(Exception):
                await server.wait_closed()

        # Notify all active connections to close via CLOSE frames
        conns = list(session.active_conns) if session else []
        for conn_id in conns:
            try:
                frame = encode_frame(0x03, conn_id, None)
                ws = getattr(session, "_ws", None)
                if ws is not None:
                    await ws.send_bytes(frame)
            except Exception:
                pass

        # Remove from conn_map
        for conn_id in conns:
            self._conn_map.pop(conn_id, None)

        # Remove any remaining conn_map entries for this node
        to_remove = [cid for cid, info in self._conn_map.items() if info.get("node") == node]
        for conn_id in to_remove:
            self._conn_map.pop(conn_id, None)

        log.info(
            "Echo tunnel disconnected for node=%s (force=%s)", node, force
        )

    async def disconnect(self, node: str) -> bool:
        """Disconnect a tunnel by node name. Acquires lock and calls _disconnect_node_locked."""
        async with self._lock:
            found = node in self._sessions
            if found:
                await self._disconnect_node_locked(node, force=False)
            return found

    # ---- TCP client handler ----

    async def _handle_tcp_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        node: str,
    ) -> None:
        """Handle an incoming TCP client connection for the given node.

        A new conn_id is assigned, OPEN frame sent to WS, then data piped
        between the TCP stream and the WS as DATA frames. On EOF, CLOSE frame sent.
        """
        conn_id = self._next_conn_id
        self._next_conn_id += 1

        # Register this connection in the conn_map (needs lock)
        async with self._lock:
            session = self._sessions.get(node)
            if session is None:
                # Session was removed while we were connecting
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
                return

            self._conn_map[conn_id] = {
                "node": node,
                "writer": writer,
                "reader": reader,
            }

        # Send OPEN frame to the remote Echo over WS
        open_frame = encode_frame(0x01, conn_id, None)
        ws = session._ws
        try:
            if ws is not None:
                await ws.send_bytes(open_frame)
        except Exception:
            log.warning("Failed to send OPEN frame for conn_id=%s", conn_id)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            async with self._lock:
                self._conn_map.pop(conn_id, None)
                session.active_conns.discard(conn_id)
            return

        # Register this connection as active
        async with self._lock:
            session.active_conns.add(conn_id)

        log.debug("Tunnel OPEN for node=%s conn_id=%s", node, conn_id)

        try:
            # Pipe TCP reads to WS as DATA frames
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                data_frame = encode_frame(0x02, conn_id, data)
                ws = session._ws
                if ws is not None:
                    try:
                        await ws.send_bytes(data_frame)
                    except Exception:
                        break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("TCP read error for conn_id=%s: %s", conn_id, exc)
        finally:
            # Send CLOSE frame on EOF or error
            close_frame = encode_frame(0x03, conn_id, None)
            ws = session._ws
            if ws is not None:
                with contextlib.suppress(Exception):
                    await ws.send_bytes(close_frame)

            # Clean up
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

            async with self._lock:
                session.active_conns.discard(conn_id)
                self._conn_map.pop(conn_id, None)

            log.debug("Tunnel CLOSE for node=%s conn_id=%s", node, conn_id)

    # ---- Status ----

    async def status(self) -> dict[str, Any]:
        """Return status of all active tunnels."""
        async with self._lock:
            tunnels = []
            for node, session in self._sessions.items():
                tunnels.append(
                    {
                        "node": node,
                        "local_port": session.local_port,
                        "connected_at": session.connected_at,
                        "active_connections": len(session.active_conns),
                    }
                )
            return {"active_tunnels": tunnels}

    # ---- Public API methods for the WS handler ----

    async def handle_ws_frame(self, frame: bytes, node: str) -> str | None:
        """Process one incoming binary frame from the tunnel WS for the given node.

        Returns a status string, or None if the frame was handled (no close needed).
        """
        frame_type, conn_id, payload = decode_frame(frame)

        async with self._lock:
            session = self._sessions.get(node)
            if session is None:
                log.warning("WS frame for unknown tunnel node=%s", node)
                return "error"

            if frame_type == 0x01:  # OPEN
                # OPEN frame: a new TCP session is being established by the remote side.
                # This is for the reverse direction (remote initiating connection).
                # We don't use this in the current architecture (only local->remote).
                # But handle it for completeness.
                if conn_id is None:
                    conn_id = self._next_conn_id
                    self._next_conn_id += 1
                    self._conn_map[conn_id] = {"node": node, "writer": None}
                session.active_conns.add(conn_id)
                log.debug("Tunnel OPEN for node=%s conn_id=%s", node, conn_id)
                return None

            elif frame_type == 0x02:  # DATA
                if conn_id is None:
                    log.warning("DATA frame without conn_id for node=%s", node)
                    return "error"
                if conn_id not in self._conn_map:
                    log.warning("DATA frame for unknown conn_id=%s on node=%s", conn_id, node)
                    return "error"
                conn_entry = self._conn_map[conn_id]
                writer = conn_entry.get("writer")
                if writer is not None:
                    try:
                        writer.write(payload or b"")
                        await writer.drain()
                    except Exception:
                        pass
                log.debug("Tunnel DATA for node=%s conn_id=%s", node, conn_id)
                return None

            elif frame_type == 0x03:  # CLOSE
                if conn_id is not None:
                    session.active_conns.discard(conn_id)
                    conn_entry = self._conn_map.pop(conn_id, None)
                    if conn_entry and conn_entry.get("writer") is not None:
                        with contextlib.suppress(Exception):
                            conn_entry["writer"].close()
                log.debug("Tunnel CLOSE for node=%s conn_id=%s", node, conn_id)
                return None

            elif frame_type == 0x04:  # PING
                # Respond with PONG
                pong_frame = encode_frame(0x05, conn_id, None)
                with contextlib.suppress(Exception):
                    await session._ws.send_bytes(pong_frame)
                return None

            elif frame_type == 0x05:  # PONG
                log.debug("Tunnel PONG received for node=%s conn_id=%s", node, conn_id)
                return None

            else:
                log.warning("Unknown frame type 0x%02x for node=%s", frame_type, node)
                return "error"

        return None
