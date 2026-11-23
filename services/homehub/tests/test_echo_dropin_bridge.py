from __future__ import annotations

import asyncio
import contextlib
import os

import pytest

from services.homehub.echo_dropin_bridge import (
    BYE_REPEATS,
    DROPIN_MAGIC,
    M_ACCEPT,
    M_BYE,
    M_INVITE,
    M_REFUSE,
    M_RINGING,
    AiortcWebRTCTransport,
    DropInBridgeError,
    DropInCryptoError,
    EchoDropInBridge,
    EchoDropInSession,
    SignalMessage,
    decode_signal,
    derive_session_key,
    encode_signal,
    generate_x25519_keypair,
    open_audio_frame,
    seal_audio_frame,
)


class QueueTransport:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.sent: list[bytes] = []
        self.closed = False

    async def send_opus(self, frame: bytes) -> None:
        self.sent.append(bytes(frame))

    async def recv_opus(self) -> bytes | None:
        return await self.incoming.get()

    async def close(self) -> None:
        self.closed = True
        self.incoming.put_nowait(None)


def test_x25519_keygen_and_session_key_symmetry() -> None:
    caller_esk, caller_epk = generate_x25519_keypair()
    callee_esk, callee_epk = generate_x25519_keypair()
    assert len(caller_esk) == 32
    assert len(caller_epk) == 32
    assert len(callee_esk) == 32
    assert len(callee_epk) == 32

    network_key = os.urandom(32)
    call_id = os.urandom(8)
    caller_id = b"caller01"
    callee_id = b"callee01"

    caller_key = derive_session_key(
        network_key=network_key,
        local_esk=caller_esk,
        peer_epk=callee_epk,
        call_id=call_id,
        caller_id=caller_id,
        callee_id=callee_id,
    )
    callee_key = derive_session_key(
        network_key=network_key,
        local_esk=callee_esk,
        peer_epk=caller_epk,
        call_id=call_id,
        caller_id=caller_id,
        callee_id=callee_id,
    )
    assert caller_key == callee_key
    assert len(caller_key) == 32


def test_derive_session_key_rejects_low_order_all_zero_shared(monkeypatch: pytest.MonkeyPatch) -> None:
    import services.homehub.echo_dropin_bridge as bridge_mod

    monkeypatch.setattr(bridge_mod.nacl_bindings, "crypto_scalarmult", lambda *_args, **_kwargs: b"\x00" * 32)
    with pytest.raises(ValueError, match="low-order X25519 point"):
        derive_session_key(
            network_key=os.urandom(32),
            local_esk=os.urandom(32),
            peer_epk=os.urandom(32),
            call_id=os.urandom(8),
            caller_id=b"caller01",
            callee_id=b"callee01",
        )


def test_audio_frame_round_trip_and_rejections() -> None:
    session_key = os.urandom(32)
    call_id = os.urandom(8)
    opus = b"\x01\x02\x03opus-frame"

    packet = seal_audio_frame(session_key, call_id, 0, 42, opus)
    seq, opened = open_audio_frame(session_key, packet, call_id, 0)
    assert seq == 42
    assert opened == opus

    tampered = bytearray(packet)
    tampered[-1] ^= 0x01
    with pytest.raises(DropInCryptoError):
        open_audio_frame(session_key, bytes(tampered), call_id, 0)

    bad_header = bytearray(packet)
    bad_header[0] ^= 0x01
    with pytest.raises(DropInCryptoError):
        open_audio_frame(session_key, bytes(bad_header), call_id, 0)

    with pytest.raises(DropInCryptoError):
        open_audio_frame(session_key, packet, os.urandom(8), 0)
    with pytest.raises(DropInCryptoError):
        open_audio_frame(session_key, packet, call_id, 1)
    with pytest.raises(DropInCryptoError):
        open_audio_frame(os.urandom(32), packet, call_id, 0)


def test_signal_round_trip_all_kinds_and_malformed() -> None:
    call_id = os.urandom(8)
    epk = os.urandom(32)
    for kind in (M_INVITE, M_ACCEPT, M_RINGING, M_REFUSE, M_BYE):
        payload = encode_signal(kind, call_id, port=28932, epk=epk, reason="ok")
        msg = decode_signal(payload)
        assert msg == SignalMessage(kind=kind, call_id=call_id, port=28932, epk=epk, reason="ok")

    with pytest.raises(DropInBridgeError):
        decode_signal(b"short")
    bad_kind = bytearray(encode_signal(M_INVITE, call_id))
    bad_kind[0] = 99
    with pytest.raises(DropInBridgeError):
        decode_signal(bytes(bad_kind))
    bad_len = encode_signal(M_INVITE, call_id, reason="hi") + b"!"
    with pytest.raises(DropInBridgeError):
        decode_signal(bad_len)


class FakeEchoPeer(asyncio.DatagramProtocol):
    def __init__(self, *, network_key: bytes, caller_id: bytes, peer_id: bytes) -> None:
        self.network_key = network_key
        self.caller_id = caller_id
        self.peer_id = peer_id  # callee id
        self.call_id: bytes | None = None
        self.caller_epk: bytes | None = None
        self.esk, self.epk = generate_x25519_keypair()
        self.session_key: bytes | None = None
        self.transport: asyncio.DatagramTransport | None = None
        self.addr: tuple[str, int] | None = None
        self.signals: list[SignalMessage] = []
        self.audio: list[tuple[int, bytes]] = []
        self.accept_sent = asyncio.Event()
        self.connected = asyncio.Event()
        self.bye_count = 0
        self.tx_seq = 0

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.addr = addr
        if data.startswith(DROPIN_MAGIC) and len(data) >= 33:
            self._on_audio(data)
            return
        try:
            sig = decode_signal(data)
        except Exception:
            return
        self.signals.append(sig)
        if sig.kind == M_INVITE:
            self.call_id = sig.call_id
            self.caller_epk = sig.epk
            assert self.transport is not None
            self.transport.sendto(encode_signal(M_ACCEPT, self.call_id, port=0, epk=self.epk), addr)
            self.accept_sent.set()
            if self.session_key is None and self.caller_epk is not None:
                self.session_key = derive_session_key(
                    network_key=self.network_key,
                    local_esk=self.esk,
                    peer_epk=self.caller_epk,
                    call_id=self.call_id,
                    caller_id=self.caller_id,
                    callee_id=self.peer_id,
                )
                self.connected.set()
        elif sig.kind == M_BYE:
            self.bye_count += 1

    def _on_audio(self, packet: bytes) -> None:
        if self.session_key is None or self.call_id is None:
            return
        try:
            seq, opus = open_audio_frame(self.session_key, packet, self.call_id, 0)
        except DropInCryptoError:
            return
        self.audio.append((seq, opus))

    def send_audio(self, opus: bytes, *, seq: int | None = None) -> None:
        if self.transport is None or self.addr is None or self.session_key is None or self.call_id is None:
            raise RuntimeError("peer not ready")
        use_seq = self.tx_seq if seq is None else seq
        if seq is None:
            self.tx_seq += 1
        packet = seal_audio_frame(self.session_key, self.call_id, 1, use_seq, opus)
        self.transport.sendto(packet, self.addr)


async def _start_fake_peer(
    *, network_key: bytes, caller_id: bytes, peer_id: bytes
) -> tuple[FakeEchoPeer, asyncio.DatagramTransport, int]:
    loop = asyncio.get_running_loop()
    transport, proto = await loop.create_datagram_endpoint(
        lambda: FakeEchoPeer(network_key=network_key, caller_id=caller_id, peer_id=peer_id),
        local_addr=("127.0.0.1", 0),
    )
    port = transport.get_extra_info("sockname")[1]
    return proto, transport, port


async def test_full_loopback_session_replay_bye_and_keepalive_timeout() -> None:
    network_key = os.urandom(32)
    caller_id = b"caller01"
    callee_id = b"callee01"

    peer, peer_transport, peer_port = await _start_fake_peer(
        network_key=network_key, caller_id=caller_id, peer_id=callee_id
    )
    bridge_transport = QueueTransport()
    session = EchoDropInSession(
        network_key=network_key,
        local_id=caller_id,
        peer_id=callee_id,
        peer_addr=("127.0.0.1", peer_port),
        media_transport=bridge_transport,
        role="caller",
        lost_media_timeout_s=5.0,
    )
    try:
        await session.start(local_addr=("127.0.0.1", 0))
        assert await session.wait_connected(timeout=2.0)
        assert session.state == "connected"
        assert peer.connected.is_set()

        # Bridge -> Echo
        await bridge_transport.incoming.put(b"opus-from-bridge-1")
        for _ in range(50):
            if peer.audio:
                break
            await asyncio.sleep(0.02)
        assert peer.audio
        assert peer.audio[0][1] == b"opus-from-bridge-1"

        # Echo -> Bridge
        peer.send_audio(b"opus-from-echo-1", seq=10)
        for _ in range(50):
            if bridge_transport.sent:
                break
            await asyncio.sleep(0.02)
        assert b"opus-from-echo-1" in bridge_transport.sent

        # Replay rejection: duplicate and out-of-window old sequence numbers are ignored.
        before = len(bridge_transport.sent)
        peer.send_audio(b"opus-dup", seq=10)
        peer.send_audio(b"opus-old", seq=200)
        for _ in range(50):
            if b"opus-old" in bridge_transport.sent:
                break
            await asyncio.sleep(0.02)
        peer.send_audio(b"opus-too-old", seq=200 - 80)
        await asyncio.sleep(0.1)
        assert len(bridge_transport.sent) == before + 1  # only seq=200 accepted; replays dropped
        assert b"opus-dup" not in bridge_transport.sent
        assert b"opus-too-old" not in bridge_transport.sent
        assert b"opus-old" in bridge_transport.sent

        # Fresh seq still accepted.
        peer.send_audio(b"opus-fresh", seq=201)
        for _ in range(50):
            if b"opus-fresh" in bridge_transport.sent:
                break
            await asyncio.sleep(0.02)
        assert b"opus-fresh" in bridge_transport.sent

        await session.hangup("cancelled")
        for _ in range(50):
            if peer.bye_count >= BYE_REPEATS:
                break
            await asyncio.sleep(0.02)
        assert peer.bye_count >= BYE_REPEATS
        assert session.state == "ended"
    finally:
        if session.state != "ended":
            await session.hangup("cleanup")
        peer_transport.close()

    # Fast keepalive timeout (5s-style, but short for test)
    _peer2, peer_transport2, peer_port2 = await _start_fake_peer(
        network_key=network_key, caller_id=caller_id, peer_id=callee_id
    )
    bridge_transport2 = QueueTransport()
    session2 = EchoDropInSession(
        network_key=network_key,
        local_id=caller_id,
        peer_id=callee_id,
        peer_addr=("127.0.0.1", peer_port2),
        media_transport=bridge_transport2,
        role="caller",
        lost_media_timeout_s=0.15,
    )
    try:
        await session2.start(local_addr=("127.0.0.1", 0))
        assert await session2.wait_connected(timeout=2.0)
        # Stop sending authenticated packets from peer; session should end cleanly.
        assert await session2.wait_ended(timeout=2.0)
        assert session2.state == "ended"
        assert session2.reason == "lost_media"
    finally:
        if session2.state != "ended":
            await session2.hangup("cleanup")
        peer_transport2.close()


def test_unconfigured_bridge_fails_loudly_naming_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HOMEHUB_ECHO_MESH_KEY", raising=False)
    bridge = EchoDropInBridge()
    with pytest.raises(DropInBridgeError) as exc:
        bridge.require_network_key()
    assert "HOMEHUB_ECHO_MESH_KEY" in str(exc.value)


def test_aiortc_transport_fails_loudly_naming_aiortc() -> None:
    with pytest.raises(DropInBridgeError) as exc:
        AiortcWebRTCTransport()
    assert "aiortc" in str(exc.value)


async def test_busy_peer_refuses_second_concurrent_call() -> None:
    network_key = os.urandom(32)
    caller_id = b"caller01"
    callee_id = b"callee01"
    _peer, peer_transport, peer_port = await _start_fake_peer(
        network_key=network_key, caller_id=caller_id, peer_id=callee_id
    )
    bridge = EchoDropInBridge(network_key=network_key)
    bridge.register_peer(peer_id=callee_id, node="kitchen", ip="127.0.0.1", port=peer_port)
    t1 = QueueTransport()
    session = await bridge.start_call("kitchen", t1, local_id=caller_id)
    try:
        assert await session.wait_connected(timeout=2.0)
        with pytest.raises(DropInBridgeError) as exc:
            await bridge.start_call("kitchen", QueueTransport(), local_id=b"caller02")
        assert "busy" in str(exc.value).lower()
    finally:
        await session.hangup("cleanup")
        peer_transport.close()


class _SignalRecorderPeer(asyncio.DatagramProtocol):
    """A peer that records signals and can reply; silent unless a test sends a reply."""

    def __init__(self) -> None:
        self.signals: list[SignalMessage] = []
        self.transport: asyncio.DatagramTransport | None = None
        self.addr: tuple[str, int] | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.addr = addr
        with contextlib.suppress(Exception):
            self.signals.append(decode_signal(data))

    def send(self, data: bytes) -> None:
        assert self.transport is not None and self.addr is not None
        self.transport.sendto(data, self.addr)

    async def wait_for_signal(self, kind: int, timeout: float = 2.0) -> SignalMessage:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            for sig in self.signals:
                if sig.kind == kind:
                    return sig
            await asyncio.sleep(0.01)
        raise AssertionError(f"no signal {kind} received")


async def _start_recorder_peer() -> tuple[_SignalRecorderPeer, asyncio.DatagramTransport]:
    loop = asyncio.get_running_loop()
    recorder = _SignalRecorderPeer()
    transport, _proto = await loop.create_datagram_endpoint(
        lambda: recorder, local_addr=("127.0.0.1", 0)
    )
    return recorder, transport


async def test_default_lan_bind_advertises_local_port_and_learns_peer_audio_port() -> None:
    network_key = os.urandom(32)
    recorder, peer_transport = await _start_recorder_peer()
    peer_port = peer_transport.get_extra_info("sockname")[1]
    session = EchoDropInSession(
        network_key=network_key,
        local_id=b"caller01",
        peer_id=b"callee01",
        peer_addr=("127.0.0.1", peer_port),
        media_transport=QueueTransport(),
        role="caller",
        answer_timeout_s=2.0,
        ring_timeout_s=3.0,
        invite_interval_s=0.05,
    )
    try:
        # No local_addr: must bind the LAN wildcard, not the loopback-only address.
        await session.start()
        assert session._transport is not None
        sockname = session._transport.get_extra_info("sockname")
        assert sockname[0] == "0.0.0.0"
        assert session.local_port == sockname[1] > 0

        invite = await recorder.wait_for_signal(M_INVITE)
        assert invite.port == session.local_port  # peer learns our audio port from the signal

        # The Echo advertises its own audio port in M_ACCEPT; audio must follow it.
        epk = generate_x25519_keypair()[1]
        recorder.send(encode_signal(M_ACCEPT, session.call_id, port=41234, epk=epk))
        for _ in range(100):
            if session.peer_addr[1] == 41234:
                break
            await asyncio.sleep(0.01)
        assert session.peer_addr == ("127.0.0.1", 41234)
        assert await session.wait_connected(timeout=1.0)
    finally:
        if session.state != "ended":
            await session.hangup("cleanup")
        peer_transport.close()


async def test_answer_timeout_only_before_ringing() -> None:
    network_key = os.urandom(32)

    # 1. Silent peer: no M_RINGING / M_ACCEPT -> end before the much longer ring timeout.
    recorder, peer_transport = await _start_recorder_peer()
    session = EchoDropInSession(
        network_key=network_key,
        local_id=b"caller01",
        peer_id=b"callee01",
        peer_addr=("127.0.0.1", peer_transport.get_extra_info("sockname")[1]),
        media_transport=QueueTransport(),
        role="caller",
        answer_timeout_s=0.15,
        ring_timeout_s=2.0,
        invite_interval_s=0.05,
    )
    loop = asyncio.get_running_loop()
    started = loop.time()
    try:
        await session.start(local_addr=("127.0.0.1", 0))
        assert await session.wait_ended(timeout=1.0)
        assert session.reason == "no_answer"
        assert loop.time() - started < 1.0  # fast, not the 2s ring timeout
        for _ in range(50):  # BYE packets are UDP; allow them to arrive
            if any(sig.kind == M_BYE for sig in recorder.signals):
                break
            await asyncio.sleep(0.01)
        assert any(sig.kind == M_BYE for sig in recorder.signals)
    finally:
        if session.state != "ended":
            await session.hangup("cleanup")
        peer_transport.close()

    # 2. Peer replies M_RINGING: stay alive past answer_timeout_s until ring_timeout_s.
    recorder2, peer_transport2 = await _start_recorder_peer()
    session2 = EchoDropInSession(
        network_key=network_key,
        local_id=b"caller01",
        peer_id=b"callee01",
        peer_addr=("127.0.0.1", peer_transport2.get_extra_info("sockname")[1]),
        media_transport=QueueTransport(),
        role="caller",
        answer_timeout_s=0.15,
        ring_timeout_s=2.0,
        invite_interval_s=0.05,
    )
    try:
        await session2.start(local_addr=("127.0.0.1", 0))
        await recorder2.wait_for_signal(M_INVITE)
        recorder2.send(encode_signal(M_RINGING, session2.call_id))
        for _ in range(100):
            if session2.state == "ringing":
                break
            await asyncio.sleep(0.01)
        assert session2.state == "ringing"
        await asyncio.sleep(0.25)  # well past answer_timeout_s
        assert session2.state == "ringing"
        assert session2.reason is None
        assert not session2._ended.is_set()
    finally:
        if session2.state != "ended":
            await session2.hangup("cleanup")
        peer_transport2.close()


class _FakeApiSession:
    """Just enough of a session for the registry/API lifecycle tests (no UDP needed)."""

    def __init__(self, call_id: bytes) -> None:
        self.call_id = call_id
        self.state = "connected"
        self.reason: str | None = None
        self.peer_addr = ("192.168.1.5", 28932)

    async def hangup(self, reason: str) -> None:
        self.state = "ended"
        self.reason = reason


def test_echo_dropin_status_and_hangup_api(tmp_path) -> None:
    from cryptography.fernet import Fernet
    from fastapi.testclient import TestClient

    from services.homehub.api import build_app
    from services.homehub.auth import Auth
    from services.homehub.core import Hub
    from services.homehub.runtime import Runtime
    from services.homehub.store import Store

    secret = "internal-secret"
    store = Store(str(tmp_path / "hub.db"), Fernet.generate_key().decode())
    hub = Hub(time_zone="Etc/GMT+7")
    hub.store = store
    runtime = Runtime(hub, store)
    auth = Auth(store, secret)
    headers = {"Authorization": f"Bearer {secret}"}
    with TestClient(build_app(runtime, auth)) as client:
        # Unauthenticated requests get the same plain 401 as the rest of the API.
        assert client.get("/hub/echo_dropin/status").status_code == 401
        assert client.post("/hub/echo_dropin/hangup/deadbeef").status_code == 401

        status = client.get("/hub/echo_dropin/status", headers=headers)
        assert status.status_code == 200
        assert status.json()["active_sessions"] == []

        fake = _FakeApiSession(bytes.fromhex("cafe1234"))
        client.app.state.echo_dropin_bridge.sessions["kitchen"] = fake  # type: ignore[attr-defined]
        active = client.get("/hub/echo_dropin/status", headers=headers).json()["active_sessions"]
        assert active == [{
            "node": "kitchen",
            "call_id": "cafe1234",
            "state": "connected",
            "peer_addr": {"ip": "192.168.1.5", "port": 28932},
            "reason": None,
        }]

        missing = client.post("/hub/echo_dropin/hangup/deadbeef", headers=headers)
        assert missing.status_code == 404 and "call_id" in missing.json()["message"]

        ok = client.post("/hub/echo_dropin/hangup/cafe1234", headers=headers)
        assert ok.status_code == 200
        assert ok.json() == {"call_id": "cafe1234", "state": "ended", "reason": "hangup"}
        assert "kitchen" not in client.app.state.echo_dropin_bridge.sessions  # type: ignore[attr-defined]
