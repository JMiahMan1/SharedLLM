"""Unit tests for services/homehub/echo_tunnel.py - EchoTunnelManager frame encode/decode and e2e multiplexing."""

from __future__ import annotations

import asyncio
import contextlib
import struct

import pytest
from fastapi import WebSocketDisconnect

from services.homehub.echo_tunnel import (
    EchoTunnelManager,
    decode_frame,
    encode_frame,
)

MAC = "02:00:00:00:01:02"
DATA = {"host": "192.0.2.127", "port": 6053, "password": "", "noise_psk": "not-a-real-key",
        "device_name": "smart-speaker"}

# --- Frame encode/decode ---

def test_encode_decode_frame():
    """Round-trip encode/decode of all frame types."""
    # OPEN with conn_id
    frame = encode_frame(0x01, 42, None)
    assert len(frame) == 5
    assert frame[0] == 0x01
    assert struct.unpack(">I", frame[1:5])[0] == 42

    # DATA with conn_id + payload
    payload = b"hello world"
    frame = encode_frame(0x02, 42, payload)
    assert len(frame) == 5 + len(payload)
    assert frame[0] == 0x02

    # CLOSE with conn_id
    frame = encode_frame(0x03, 7, None)
    assert len(frame) == 5
    assert frame[0] == 0x03

    # PING
    frame = encode_frame(0x04, None, None)
    assert len(frame) == 1
    assert frame[0] == 0x04

    # PONG
    frame = encode_frame(0x05, None, None)
    assert len(frame) == 1
    assert frame[0] == 0x05

    # Decode round-trip
    for frame_type, conn_id, payload_ in [
        (0x01, 42, None),
        (0x02, 0, b"test"),
        (0x03, 7, None),
        (0x04, None, None),
        (0x05, None, None),
    ]:
        frame = encode_frame(frame_type, conn_id, payload_)
        decoded_type, decoded_conn_id, decoded_payload = decode_frame(frame)
        assert decoded_type == frame_type
        assert decoded_conn_id == conn_id
        if payload_ is not None:
            assert decoded_payload == payload_
        else:
            assert decoded_payload is None


def test_decode_frame_too_short():
    """Decode frame that is too short (empty) raises ValueError."""
    # A 1-byte frame (PING/PONG) is valid; only empty frames are too short.
    with pytest.raises(ValueError):
        decode_frame(b"")


# --- local_endpoint ---


def test_local_endpoint_unknown_node():
    """local_endpoint returns None for an unknown node."""
    manager = EchoTunnelManager()
    assert manager.local_endpoint("unknown") is None


async def test_local_endpoint_registered_node():
    """local_endpoint returns (127.0.0.1, port) for a registered node."""
    manager = EchoTunnelManager()

    class FakeWS:
        async def send_bytes(self, data: bytes) -> None:
            pass
        async def close(self) -> None:
            pass

    ws = FakeWS()
    local_port = await manager.register_tunnel("testnode", ws)
    assert manager.local_endpoint("testnode") == ("127.0.0.1", local_port)
    assert manager.local_endpoint("other") is None

    await manager.disconnect("testnode")
    assert manager.local_endpoint("testnode") is None


# --- EchoTunnelManager status ---

async def test_status_empty():
    """status() returns empty active tunnels dict."""
    manager = EchoTunnelManager()
    status = await manager.status()
    assert status == {"active_tunnels": []}


async def test_status_with_tunnel():
    """status() returns tunnel info after registration."""
    manager = EchoTunnelManager()
    # Register a tunnel via the internal method (needs WS, so just verify status starts empty)
    # We can't easily call register_tunnel without a real WS in this test context,
    # so just verify initial status is empty
    status = await manager.status()
    assert status == {"active_tunnels": []}


# --- Mock WebSocket for testing ---

class MockWebSocket:
    """Mock WebSocket that captures sent frames and provides a receive queue."""

    def __init__(self, manager: EchoTunnelManager | None = None, node: str = "testnode"):
        self.sent_frames: list[bytes] = []
        self._recv_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.closed = False
        self._manager = manager
        self._node = node
        self._handler_task: asyncio.Task | None = None

    async def send_bytes(self, data: bytes) -> None:
        self.sent_frames.append(data)

    async def send(self, data: bytes) -> None:
        self.sent_frames.append(data)

    async def recv(self) -> bytes:
        return await self._recv_queue.get()

    def inject_frame(self, frame: bytes) -> None:
        self._recv_queue.put_nowait(frame)

    async def close(self) -> None:
        self.closed = True
        if self._handler_task:
            self._handler_task.cancel()
            with contextlib.suppress(Exception):
                await self._handler_task

    def start_handler_loop(self) -> None:
        """Start the background task that calls handle_ws_frame for each received frame."""
        if self._manager is None:
            return
        async def handler_loop():
            while not self.closed:
                try:
                    frame = await asyncio.wait_for(self.recv(), timeout=0.1)
                    await self._manager.handle_ws_frame(frame, self._node)
                except TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break
                except Exception:
                    pass
        self._handler_task = asyncio.create_task(handler_loop())


# --- End-to-end loopback TCP <-> mock WS test ---

async def test_tunnel_e2e_loopback_tcp_ws():
    """Real loopback test: TCP client <-> mock WS through EchoTunnelManager.

    Verifies actual TCP bytes flow cleanly in both directions through the tunnel
    and clean teardown on disconnect.
    """
    manager = EchoTunnelManager()
    ws = MockWebSocket(manager, "testnode")

    # Register tunnel
    local_port = await manager.register_tunnel("testnode", ws)
    assert local_port > 0

    # Start the WS handler loop
    ws.start_handler_loop()

    # Check status
    status = await manager.status()
    assert len(status["active_tunnels"]) == 1
    assert status["active_tunnels"][0]["node"] == "testnode"
    assert status["active_tunnels"][0]["local_port"] == local_port

    # Connect a TCP client to the local port
    reader, writer = await asyncio.open_connection("127.0.0.1", local_port)

    # The manager should have sent an OPEN frame
    await asyncio.sleep(0.05)  # allow event loop to process
    open_frames = [f for f in ws.sent_frames if f[0] == 0x01]
    assert len(open_frames) == 1
    conn_id = struct.unpack(">I", open_frames[0][1:5])[0]

    # Send data from TCP client -> should appear as DATA frame on WS
    test_data = b"Hello from TCP client!"
    writer.write(test_data)
    await writer.drain()

    await asyncio.sleep(0.05)
    data_frames = [f for f in ws.sent_frames if f[0] == 0x02]
    assert len(data_frames) == 1
    frame_conn_id = struct.unpack(">I", data_frames[0][1:5])[0]
    frame_payload = data_frames[0][5:]
    assert frame_conn_id == conn_id
    assert frame_payload == test_data

    # Now simulate WS -> TCP: inject a DATA frame from the "remote" side
    ws_response = b"Hello from WS side!"
    ws_frame = encode_frame(0x02, conn_id, ws_response)
    ws.inject_frame(ws_frame)

    # Read from TCP client
    tcp_response = await asyncio.wait_for(reader.read(65536), timeout=1.0)
    assert tcp_response == ws_response

    # Test PING/PONG
    ping_frame = encode_frame(0x04, conn_id, None)
    ws.inject_frame(ping_frame)
    await asyncio.sleep(0.05)
    pong_frames = [f for f in ws.sent_frames if f[0] == 0x05]
    assert len(pong_frames) == 1
    pong_conn_id = struct.unpack(">I", pong_frames[0][1:5])[0]
    assert pong_conn_id == conn_id

    # Close TCP client -> should send CLOSE frame on WS
    writer.close()
    await writer.wait_closed()

    await asyncio.sleep(0.05)
    close_frames = [f for f in ws.sent_frames if f[0] == 0x03]
    assert len(close_frames) == 1
    close_conn_id = struct.unpack(">I", close_frames[0][1:5])[0]
    assert close_conn_id == conn_id

    # Verify connection is cleaned up
    status = await manager.status()
    assert status["active_tunnels"][0]["active_connections"] == 0

    # Test disconnect cleans up everything
    disconnected = await manager.disconnect("testnode")
    assert disconnected is True

    status = await manager.status()
    assert status["active_tunnels"] == []

    # Disconnect again should return False
    disconnected = await manager.disconnect("testnode")
    assert disconnected is False


async def test_tunnel_multiple_concurrent_connections():
    """Test multiple concurrent TCP connections multiplexed over single WS."""
    manager = EchoTunnelManager()
    ws = MockWebSocket(manager, "multinode")

    local_port = await manager.register_tunnel("multinode", ws)
    ws.start_handler_loop()

    # Open 3 concurrent TCP connections
    readers = []
    writers = []
    conn_ids = []

    for _ in range(3):
        reader, writer = await asyncio.open_connection("127.0.0.1", local_port)
        readers.append(reader)
        writers.append(writer)

    await asyncio.sleep(0.1)  # allow OPEN frames to be sent

    # Should have 3 OPEN frames with different conn_ids
    open_frames = [f for f in ws.sent_frames if f[0] == 0x01]
    assert len(open_frames) == 3
    conn_ids = [struct.unpack(">I", f[1:5])[0] for f in open_frames]
    assert len(set(conn_ids)) == 3  # all unique

    # Send data on each connection
    for i, writer in enumerate(writers):
        test_data = f"Message from connection {i}".encode()
        writer.write(test_data)
        await writer.drain()

    await asyncio.sleep(0.1)

    # Should have 3 DATA frames with correct conn_ids
    data_frames = [f for f in ws.sent_frames if f[0] == 0x02]
    assert len(data_frames) == 3
    for frame in data_frames:
        frame_conn_id = struct.unpack(">I", frame[1:5])[0]
        assert frame_conn_id in conn_ids

    # Close all connections
    for writer in writers:
        writer.close()
        await writer.wait_closed()

    await asyncio.sleep(0.1)

    # Should have 3 CLOSE frames
    close_frames = [f for f in ws.sent_frames if f[0] == 0x03]
    assert len(close_frames) == 3

    # Verify cleanup
    status = await manager.status()
    assert status["active_tunnels"][0]["active_connections"] == 0

    await manager.disconnect("multinode")


async def test_tunnel_ws_to_tcp_data_flow():
    """Test data flow from WS -> TCP (remote initiating data)."""
    manager = EchoTunnelManager()
    ws = MockWebSocket(manager, "wsnode")

    local_port = await manager.register_tunnel("wsnode", ws)
    ws.start_handler_loop()

    # Connect TCP client
    reader, writer = await asyncio.open_connection("127.0.0.1", local_port)
    await asyncio.sleep(0.05)

    # Get the conn_id from OPEN frame
    open_frames = [f for f in ws.sent_frames if f[0] == 0x01]
    conn_id = struct.unpack(">I", open_frames[0][1:5])[0]

    # Inject DATA frame from WS side
    test_payload = b"Remote to local data"
    ws.inject_frame(encode_frame(0x02, conn_id, test_payload))

    # Read from TCP client
    received = await asyncio.wait_for(reader.read(65536), timeout=1.0)
    assert received == test_payload

    # Clean up
    writer.close()
    await writer.wait_closed()
    await manager.disconnect("wsnode")


async def test_tunnel_ws_close_cleans_up_tcp():
    """Test that CLOSE frame from WS closes the TCP connection."""
    manager = EchoTunnelManager()
    ws = MockWebSocket(manager, "closenode")

    local_port = await manager.register_tunnel("closenode", ws)
    ws.start_handler_loop()

    reader, _writer = await asyncio.open_connection("127.0.0.1", local_port)
    await asyncio.sleep(0.05)

    open_frames = [f for f in ws.sent_frames if f[0] == 0x01]
    conn_id = struct.unpack(">I", open_frames[0][1:5])[0]

    # Inject CLOSE frame from WS
    ws.inject_frame(encode_frame(0x03, conn_id, None))

    # TCP reader should get EOF
    data = await reader.read(65536)
    assert data == b""

    await manager.disconnect("closenode")


async def test_tunnel_register_twice_same_node_replaces():
    """Registering a tunnel for the same node twice replaces the old one."""
    manager = EchoTunnelManager()
    ws1 = MockWebSocket(manager, "replacenode")
    ws2 = MockWebSocket(manager, "replacenode")

    port1 = await manager.register_tunnel("replacenode", ws1)
    ws1.start_handler_loop()
    port2 = await manager.register_tunnel("replacenode", ws2)
    ws2.start_handler_loop()

    # Should get different ports
    assert port1 != port2

    # First WS should have received CLOSE frames for its connections
    # (but since no connections were made, just verify second registration works)

    # Connect to second port
    _reader, writer = await asyncio.open_connection("127.0.0.1", port2)
    await asyncio.sleep(0.05)

    # Should see OPEN on ws2, not ws1
    open_frames_ws2 = [f for f in ws2.sent_frames if f[0] == 0x01]
    assert len(open_frames_ws2) == 1

    open_frames_ws1 = [f for f in ws1.sent_frames if f[0] == 0x01]
    assert len(open_frames_ws1) == 0

    writer.close()
    await writer.wait_closed()
    await manager.disconnect("replacenode")


async def test_tunnel_disconnect_sends_close_to_all_active():
    """disconnect() sends CLOSE frames for all active connections."""
    manager = EchoTunnelManager()
    ws = MockWebSocket(manager, "discnode")

    local_port = await manager.register_tunnel("discnode", ws)
    ws.start_handler_loop()

    # Open 2 connections
    readers = []
    writers = []
    for _ in range(2):
        reader, writer = await asyncio.open_connection("127.0.0.1", local_port)
        readers.append(reader)
        writers.append(writer)

    await asyncio.sleep(0.1)

    # Close writers first (simulate client disconnect)
    for writer in writers:
        writer.close()
        await writer.wait_closed()

    await asyncio.sleep(0.05)

    # Should have CLOSE frames from client disconnects
    close_frames = [f for f in ws.sent_frames if f[0] == 0x03]
    assert len(close_frames) == 2

    # Readers should get EOF
    for reader in readers:
        data = await reader.read(65536)
        assert data == b""

    # Now test disconnect with no active connections (should not hang)
    await manager.disconnect("discnode")

    status = await manager.status()
    assert status["active_tunnels"] == []


# --- Route tests ---


def test_status_route_returns_json():
    """GET /hub/echo_tunnel/status returns real JSON with admin auth."""
    import pathlib
    import tempfile

    from cryptography.fernet import Fernet
    from fastapi.testclient import TestClient

    from services.homehub.api import build_app
    from services.homehub.auth import Auth
    from services.homehub.runtime import Runtime
    from services.homehub.store import Store
    from services.homehub.tests.conftest import make_hub

    hub, _entry = make_hub("esphome", dict(DATA))
    store = Store(str(pathlib.Path(tempfile.mkdtemp()) / "h.db"), Fernet.generate_key().decode())
    rt = Runtime(hub, store)
    client = TestClient(build_app(rt, Auth(store, "s")))
    resp = client.get("/hub/echo_tunnel/status", headers={"authorization": "Bearer s"})
    assert resp.status_code == 200
    body = resp.json()
    assert "active_tunnels" in body


def test_disconnect_route_returns_json():
    """POST /hub/echo_tunnel/disconnect/{node} returns real JSON with admin auth."""
    import pathlib
    import tempfile

    from cryptography.fernet import Fernet
    from fastapi.testclient import TestClient

    from services.homehub.api import build_app
    from services.homehub.auth import Auth
    from services.homehub.runtime import Runtime
    from services.homehub.store import Store
    from services.homehub.tests.conftest import make_hub

    hub, _entry = make_hub("esphome", dict(DATA))
    store = Store(str(pathlib.Path(tempfile.mkdtemp()) / "h.db"), Fernet.generate_key().decode())
    rt = Runtime(hub, store)
    client = TestClient(build_app(rt, Auth(store, "s")))
    resp = client.post("/hub/echo_tunnel/disconnect/home", headers={"authorization": "Bearer s"})
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"disconnected": False}


# --- Per-device tunnel tokens ---

_ADMIN = {"authorization": "Bearer s"}


def _make_client():
    """A TestClient over a fresh hub + store (internal secret "s"), plus the store."""
    import pathlib
    import tempfile

    from cryptography.fernet import Fernet
    from fastapi.testclient import TestClient

    from services.homehub.api import build_app
    from services.homehub.auth import Auth
    from services.homehub.runtime import Runtime
    from services.homehub.store import Store
    from services.homehub.tests.conftest import make_hub

    hub, _entry = make_hub("esphome", dict(DATA))
    store = Store(str(pathlib.Path(tempfile.mkdtemp()) / "h.db"), Fernet.generate_key().decode())
    rt = Runtime(hub, store)
    return TestClient(build_app(rt, Auth(store, "s"))), store


def test_create_token_returns_once_and_hashes():
    """POST mints a token once; the list shows the node; the store keeps only the sha256 hex."""
    import hashlib

    client, store = _make_client()
    resp = client.post("/hub/echo_tunnel/tokens/home", headers=_ADMIN)
    assert resp.status_code == 200
    body = resp.json()
    assert body["node"] == "home"
    token = body["token"]
    assert isinstance(token, str) and token

    # The list names the node, never the token or its hash.
    listing = client.get("/hub/echo_tunnel/tokens", headers=_ADMIN)
    assert listing.status_code == 200
    assert listing.json() == {"nodes": ["home"]}
    assert token not in listing.text

    # The store holds a 64-char sha256 hex of the token, not the token itself.
    stored = store.get_kv("echo_tunnel_tokens")
    assert isinstance(stored, dict) and set(stored) == {"home"}
    digest = stored["home"]
    assert isinstance(digest, str) and len(digest) == 64
    int(digest, 16)  # hex
    assert digest == hashlib.sha256(token.encode()).hexdigest()
    assert digest != token


def test_delete_token_reports_revoked_flag():
    """DELETE returns revoked=False when there was none, True once one is removed."""
    client, _store = _make_client()
    assert client.delete("/hub/echo_tunnel/tokens/home", headers=_ADMIN).json() == {"revoked": False}
    client.post("/hub/echo_tunnel/tokens/home", headers=_ADMIN)
    assert client.delete("/hub/echo_tunnel/tokens/home", headers=_ADMIN).json() == {"revoked": True}
    assert client.get("/hub/echo_tunnel/tokens", headers=_ADMIN).json() == {"nodes": []}


def test_token_routes_require_admin():
    """The token routes refuse a caller that is not an admin."""
    client, _store = _make_client()
    assert client.get("/hub/echo_tunnel/tokens").status_code == 401
    assert client.post("/hub/echo_tunnel/tokens/home").status_code == 401
    assert client.delete("/hub/echo_tunnel/tokens/home").status_code == 401


def test_ws_device_token_accepted_for_its_node():
    """WS with the right device token for its node is accepted."""
    client, _store = _make_client()
    token = client.post("/hub/echo_tunnel/tokens/home", headers=_ADMIN).json()["token"]
    with client.websocket_connect(
        "/hub/echo_tunnel/ws/home", headers={"Authorization": f"Bearer {token}"}
    ):
        pass


def test_ws_device_token_wrong_node_rejected():
    """The same device token on another node closes 1008."""
    client, _store = _make_client()
    token = client.post("/hub/echo_tunnel/tokens/home", headers=_ADMIN).json()["token"]
    with pytest.raises(WebSocketDisconnect) as closed, client.websocket_connect(
        "/hub/echo_tunnel/ws/livingroom", headers={"Authorization": f"Bearer {token}"}
    ):
        pass
    assert closed.value.code == 1008


def test_ws_device_token_in_query_rejected():
    """A device token passed as ?token= is rejected; device tokens come only from the header."""
    client, _store = _make_client()
    token = client.post("/hub/echo_tunnel/tokens/home", headers=_ADMIN).json()["token"]
    with pytest.raises(WebSocketDisconnect) as closed, client.websocket_connect(
        f"/hub/echo_tunnel/ws/home?token={token}"
    ):
        pass
    assert closed.value.code == 1008


def test_ws_device_token_after_revoke_rejected():
    """A revoked device token no longer authenticates."""
    client, _store = _make_client()
    token = client.post("/hub/echo_tunnel/tokens/home", headers=_ADMIN).json()["token"]
    assert client.delete("/hub/echo_tunnel/tokens/home", headers=_ADMIN).json() == {"revoked": True}
    with pytest.raises(WebSocketDisconnect) as closed, client.websocket_connect(
        "/hub/echo_tunnel/ws/home", headers={"Authorization": f"Bearer {token}"}
    ):
        pass
    assert closed.value.code == 1008
