"""Unified Phone/Web <-> Echo Dot Live Drop-In Bridge.

Wire crypto and signaling are byte-for-byte compatible with ``hassmic/dropin.c``.
Fail fast: missing keys, bad lengths, low-order points, tampered packets, and
unconfigured bridge state raise clear errors. Never log shared network keys,
ephemeral secret keys, session keys, or audio bytes.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import hashlib
import logging
import os
import struct
import time
from dataclasses import dataclass
from typing import Any, Protocol

from nacl import bindings as nacl_bindings

log = logging.getLogger("homehub.echo_dropin_bridge")

DROPIN_MAGIC = b"HMD1"
DROPIN_PORT = 28932
DROPIN_DOMAIN = b"hassmic drop in 1"

M_INVITE = 1
M_ACCEPT = 2
M_RINGING = 3
M_REFUSE = 4
M_BYE = 5
_SIGNAL_KINDS = {M_INVITE, M_ACCEPT, M_RINGING, M_REFUSE, M_BYE}

INVITE_INTERVAL_S = 0.25
ANSWER_TIMEOUT_S = 4.0
RING_TIMEOUT_S = 30.0
LOST_MEDIA_TIMEOUT_S = 5.0
BYE_REPEATS = 3

_HEADER_STRUCT = struct.Struct("!4s8sBI")
_SIGNAL_STRUCT = struct.Struct("!B8sH32sB")
_AUDIO_HEADER_LEN = _HEADER_STRUCT.size  # 17
_AEAD_TAG_LEN = 16
_NONCE_LEN = 24
_KEY_LEN = 32
_CALL_ID_LEN = 8
_SEQ_MAX = 0xFFFFFFFF
_REPLAY_WINDOW = 64


class DropInBridgeError(RuntimeError):
    """Base error for bridge failures."""


class DropInCryptoError(DropInBridgeError):
    """Crypto or packet-authentication failure."""


@dataclass(frozen=True, slots=True)
class SignalMessage:
    kind: int
    call_id: bytes
    port: int
    epk: bytes
    reason: str


class BridgeMediaTransport(Protocol):
    async def send_opus(self, frame: bytes) -> None: ...

    async def recv_opus(self) -> bytes | None: ...

    async def close(self) -> None: ...


def _require_len(name: str, value: bytes, expected: int) -> bytes:
    if not isinstance(value, (bytes, bytearray)):
        raise DropInBridgeError(f"{name} must be bytes")
    raw = bytes(value)
    if len(raw) != expected:
        raise DropInBridgeError(f"{name} must be {expected} bytes, got {len(raw)}")
    return raw


def derive_domain_key(network_key: bytes, domain: bytes = DROPIN_DOMAIN) -> bytes:
    """32-byte keyed BLAKE2b domain key, compatible with dropin.c:derive()."""
    key = _require_len("network_key", network_key, _KEY_LEN)
    if not isinstance(domain, (bytes, bytearray)):
        raise DropInBridgeError("domain must be bytes")
    return hashlib.blake2b(bytes(domain), key=key, digest_size=_KEY_LEN).digest()


def generate_x25519_keypair() -> tuple[bytes, bytes]:
    """Return (esk_32, epk_32) using random scalar + crypto_scalarmult_base."""
    esk = os.urandom(_KEY_LEN)
    epk = nacl_bindings.crypto_scalarmult_base(esk)
    return esk, _require_len("epk", epk, _KEY_LEN)


def derive_session_key(
    *,
    network_key: bytes,
    local_esk: bytes,
    peer_epk: bytes,
    call_id: bytes,
    caller_id: bytes,
    callee_id: bytes,
) -> bytes:
    """Derive the 32-byte drop-in session key, compatible with dropin.c."""
    network_key = _require_len("network_key", network_key, _KEY_LEN)
    local_esk = _require_len("local_esk", local_esk, _KEY_LEN)
    peer_epk = _require_len("peer_epk", peer_epk, _KEY_LEN)
    call_id = _require_len("call_id", call_id, _CALL_ID_LEN)
    caller_id = _require_len("caller_id", caller_id, _CALL_ID_LEN)
    callee_id = _require_len("callee_id", callee_id, _CALL_ID_LEN)

    try:
        shared = nacl_bindings.crypto_scalarmult(local_esk, peer_epk)
    except Exception as exc:  # pragma: no cover - defensive
        raise DropInCryptoError("invalid X25519 point") from exc
    shared = _require_len("shared", shared, _KEY_LEN)
    if shared == b"\x00" * _KEY_LEN:
        raise ValueError("low-order X25519 point")

    kd = derive_domain_key(network_key, DROPIN_DOMAIN)
    msg = shared + call_id + caller_id + callee_id
    if len(msg) != 56:
        raise DropInCryptoError(f"session derivation message must be 56 bytes, got {len(msg)}")
    return hashlib.blake2b(msg, key=kd, digest_size=_KEY_LEN).digest()


def _audio_header(call_id: bytes, direction: int, seq: int) -> bytes:
    call_id = _require_len("call_id", call_id, _CALL_ID_LEN)
    if direction not in (0, 1):
        raise DropInBridgeError("direction must be 0 or 1")
    if not isinstance(seq, int) or not (0 <= seq <= _SEQ_MAX):
        raise DropInBridgeError(f"seq must be 0..{_SEQ_MAX}")
    return _HEADER_STRUCT.pack(DROPIN_MAGIC, call_id, direction, seq)


def _audio_nonce(call_id: bytes, direction: int, seq: int) -> bytes:
    """Deterministic 24-byte XChaCha20-Poly1305 nonce: call_id(8)+dir(1)+seq(4)+11 zero bytes."""
    call_id = _require_len("call_id", call_id, _CALL_ID_LEN)
    if direction not in (0, 1):
        raise DropInBridgeError("direction must be 0 or 1")
    if not isinstance(seq, int) or not (0 <= seq <= _SEQ_MAX):
        raise DropInBridgeError(f"seq must be 0..{_SEQ_MAX}")
    nonce = call_id + struct.pack("!BI", direction, seq) + (b"\x00" * 11)
    if len(nonce) != _NONCE_LEN:
        raise DropInCryptoError(f"nonce must be {_NONCE_LEN} bytes")
    return nonce


def seal_audio_frame(
    session_key: bytes, call_id: bytes, direction: int, seq: int, opus_frame: bytes
) -> bytes:
    """Encrypt an Opus frame into HMD1 header + XChaCha20-Poly1305 ciphertext+tag."""
    session_key = _require_len("session_key", session_key, _KEY_LEN)
    if not isinstance(opus_frame, (bytes, bytearray)):
        raise DropInBridgeError("opus_frame must be bytes")
    header = _audio_header(call_id, direction, seq)
    nonce = _audio_nonce(call_id, direction, seq)
    ciphertext = nacl_bindings.crypto_aead_xchacha20poly1305_ietf_encrypt(
        bytes(opus_frame), header, nonce, session_key
    )
    return header + ciphertext


def open_audio_frame(
    session_key: bytes, packet: bytes, expected_call_id: bytes, expected_direction: int
) -> tuple[int, bytes]:
    """Verify and decrypt an HMD1 audio packet, returning (seq, opus_frame)."""
    session_key = _require_len("session_key", session_key, _KEY_LEN)
    expected_call_id = _require_len("expected_call_id", expected_call_id, _CALL_ID_LEN)
    if expected_direction not in (0, 1):
        raise DropInBridgeError("expected_direction must be 0 or 1")
    if not isinstance(packet, (bytes, bytearray)):
        raise DropInCryptoError("packet must be bytes")
    packet = bytes(packet)
    min_len = _AUDIO_HEADER_LEN + _AEAD_TAG_LEN
    if len(packet) < min_len:
        raise DropInCryptoError(f"audio packet too short: {len(packet)} < {min_len}")

    header = packet[:_AUDIO_HEADER_LEN]
    try:
        magic, call_id, direction, seq = _HEADER_STRUCT.unpack(header)
    except struct.error as exc:
        raise DropInCryptoError("malformed audio header") from exc
    if magic != DROPIN_MAGIC:
        raise DropInCryptoError("bad audio magic")
    if call_id != expected_call_id:
        raise DropInCryptoError("unexpected call_id")
    if direction != expected_direction:
        raise DropInCryptoError("unexpected direction")

    nonce = _audio_nonce(call_id, direction, seq)
    ciphertext = packet[_AUDIO_HEADER_LEN:]
    try:
        plaintext = nacl_bindings.crypto_aead_xchacha20poly1305_ietf_decrypt(
            ciphertext, header, nonce, session_key
        )
    except Exception as exc:
        raise DropInCryptoError("audio authentication failed") from exc
    return seq, bytes(plaintext)


def encode_signal(
    kind: int, call_id: bytes, port: int = 0, epk: bytes = b"\x00" * _KEY_LEN, reason: str = ""
) -> bytes:
    """Encode a dropin.c-compatible signaling message."""
    if kind not in _SIGNAL_KINDS:
        raise DropInBridgeError(f"unknown signal kind: {kind}")
    call_id = _require_len("call_id", call_id, _CALL_ID_LEN)
    if not isinstance(port, int) or not (0 <= port <= 0xFFFF):
        raise DropInBridgeError("port must be 0..65535")
    epk = _require_len("epk", epk, _KEY_LEN)
    if not isinstance(reason, str):
        raise DropInBridgeError("reason must be str")
    reason_bytes = reason.encode("utf-8")
    if len(reason_bytes) > 0xFF:
        raise DropInBridgeError("reason too long")
    return _SIGNAL_STRUCT.pack(kind, call_id, port, epk, len(reason_bytes)) + reason_bytes


def decode_signal(payload: bytes) -> SignalMessage:
    """Decode and validate a dropin.c-compatible signaling message."""
    if not isinstance(payload, (bytes, bytearray)):
        raise DropInBridgeError("payload must be bytes")
    payload = bytes(payload)
    min_len = _SIGNAL_STRUCT.size
    if len(payload) < min_len:
        raise DropInBridgeError(f"signal too short: {len(payload)} < {min_len}")
    kind, call_id, port, epk, reason_len = _SIGNAL_STRUCT.unpack_from(payload, 0)
    if kind not in _SIGNAL_KINDS:
        raise DropInBridgeError(f"unknown signal kind: {kind}")
    expected_len = min_len + reason_len
    if len(payload) != expected_len:
        raise DropInBridgeError(
            f"signal length mismatch: got {len(payload)}, expected {expected_len}"
        )
    reason_bytes = payload[min_len:expected_len]
    try:
        reason = reason_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DropInBridgeError("signal reason is not valid utf-8") from exc
    return SignalMessage(kind=kind, call_id=bytes(call_id), port=int(port), epk=bytes(epk), reason=reason)


class WebSocketOpusTransport:
    """Bridge binary Opus frames directly over a FastAPI/Starlette WebSocket."""

    def __init__(self, websocket: Any) -> None:
        self._ws = websocket
        self._closed = False

    async def send_opus(self, frame: bytes) -> None:
        if self._closed:
            raise DropInBridgeError("websocket transport is closed")
        if not isinstance(frame, (bytes, bytearray)):
            raise DropInBridgeError("opus frame must be bytes")
        await self._ws.send_bytes(bytes(frame))

    async def recv_opus(self) -> bytes | None:
        if self._closed:
            return None
        try:
            msg = await self._ws.receive()
        except Exception:
            self._closed = True
            return None
        msg_type = msg.get("type")
        if msg_type == "websocket.disconnect":
            self._closed = True
            return None
        if msg_type != "websocket.receive":
            self._closed = True
            return None
        data = msg.get("bytes")
        if data is None:
            # Text frames are not Opus; treat as end-of-stream.
            self._closed = True
            return None
        return bytes(data)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(Exception):
            await self._ws.close()


class AiortcWebRTCTransport:
    """Optional WebRTC adapter; fails fast when aiortc is not installed."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        try:
            import aiortc  # noqa: F401
        except Exception as exc:
            raise DropInBridgeError(
                "WebRTC Drop-In bridge requires the optional 'aiortc' package; install it to use WebRTC transport"
            ) from exc
        # aiortc is present; this adapter is intentionally minimal until a concrete
        # Gateway WebRTC track bridge is wired. Fail fast rather than silently noop.
        raise DropInBridgeError(
            "AiortcWebRTCTransport adapter is not wired to a concrete Gateway WebRTC track in this build"
        )

    async def send_opus(self, frame: bytes) -> None:  # pragma: no cover - init fails fast
        raise DropInBridgeError("AiortcWebRTCTransport is unavailable")

    async def recv_opus(self) -> bytes | None:  # pragma: no cover - init fails fast
        raise DropInBridgeError("AiortcWebRTCTransport is unavailable")

    async def close(self) -> None:  # pragma: no cover - init fails fast
        return None


class _ReplayWindow:
    """Monotonic replay window with small jitter tolerance."""

    def __init__(self, size: int = _REPLAY_WINDOW) -> None:
        self.size = max(1, int(size))
        self.max_seq: int | None = None
        self.seen: set[int] = set()

    def accept(self, seq: int) -> bool:
        if not isinstance(seq, int) or seq < 0 or seq > _SEQ_MAX:
            return False
        if self.max_seq is None:
            self.max_seq = seq
            self.seen.add(seq)
            return True
        if seq > self.max_seq:
            # Advance window; drop old entries.
            min_ok = seq - self.size
            if min_ok > 0:
                self.seen = {s for s in self.seen if s >= min_ok}
            self.max_seq = seq
            self.seen.add(seq)
            return True
        if self.max_seq - seq > self.size:
            return False
        if seq in self.seen:
            return False
        self.seen.add(seq)
        return True


@dataclass(slots=True)
class _DropInUDPProto(asyncio.DatagramProtocol):
    session: EchoDropInSession

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.session._on_datagram(data, addr)

    def error_received(self, exc: Exception) -> None:  # pragma: no cover - defensive
        log.warning("drop-in UDP error: %s", type(exc).__name__)

    def connection_lost(self, exc: Exception | None) -> None:
        self.session._on_udp_closed(exc)


class EchoDropInSession:
    """One live bidirectional Phone/Web <-> Echo Dot Drop-In session."""

    def __init__(
        self,
        *,
        network_key: bytes,
        local_id: bytes,
        peer_id: bytes,
        peer_addr: tuple[str, int],
        media_transport: BridgeMediaTransport,
        role: str = "caller",
        call_id: bytes | None = None,
        lost_media_timeout_s: float = LOST_MEDIA_TIMEOUT_S,
        answer_timeout_s: float = ANSWER_TIMEOUT_S,
        ring_timeout_s: float = RING_TIMEOUT_S,
        invite_interval_s: float = INVITE_INTERVAL_S,
    ) -> None:
        self.network_key = _require_len("network_key", network_key, _KEY_LEN)
        self.local_id = _require_len("local_id", local_id, _CALL_ID_LEN)
        self.peer_id = _require_len("peer_id", peer_id, _CALL_ID_LEN)
        if not (isinstance(peer_addr, tuple) and len(peer_addr) == 2):
            raise DropInBridgeError("peer_addr must be (host, port)")
        host, port = peer_addr
        if not isinstance(host, str) or not host:
            raise DropInBridgeError("peer_addr host must be a non-empty string")
        if not isinstance(port, int) or not (1 <= port <= 0xFFFF):
            raise DropInBridgeError("peer_addr port must be 1..65535")
        self.peer_addr = (host, port)
        self.media = media_transport
        if role not in ("caller", "callee"):
            raise DropInBridgeError("role must be 'caller' or 'callee'")
        self.role = role
        self.call_id = _require_len("call_id", call_id, _CALL_ID_LEN) if call_id is not None else os.urandom(_CALL_ID_LEN)
        self.lost_media_timeout_s = float(lost_media_timeout_s)
        self.answer_timeout_s = float(answer_timeout_s)
        self.ring_timeout_s = float(ring_timeout_s)
        self.invite_interval_s = float(invite_interval_s)

        self.state = "idle"
        self.reason: str | None = None
        self.local_port = 0  # Bound UDP audio port, learned in start().
        self.last_rx_at: float | None = None
        self.local_esk: bytes | None = None
        self.local_epk: bytes | None = None
        self.peer_epk: bytes | None = None
        self.session_key: bytes | None = None
        self._tx_seq = 0
        self._replay = _ReplayWindow()
        self._transport: asyncio.DatagramTransport | None = None
        self._protocol: _DropInUDPProto | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self._connected = asyncio.Event()
        self._ended = asyncio.Event()
        self._closing = False
        self._send_lock = asyncio.Lock()

    @property
    def local_direction(self) -> int:
        return 0 if self.role == "caller" else 1

    @property
    def peer_direction(self) -> int:
        return 1 - self.local_direction

    def _spawn(self, coro: Any) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def start(self, *, local_addr: tuple[str, int] = ("0.0.0.0", 0)) -> None:
        if self.state != "idle":
            raise DropInBridgeError(f"session cannot start from state {self.state}")
        loop = asyncio.get_running_loop()
        transport, protocol = await loop.create_datagram_endpoint(
            lambda: _DropInUDPProto(self), local_addr=local_addr
        )
        self._transport = transport
        self._protocol = protocol
        sockname = transport.get_extra_info("sockname")
        self.local_port = int(sockname[1]) if sockname is not None else 0
        self.local_esk, self.local_epk = generate_x25519_keypair()
        self.state = "calling" if self.role == "caller" else "ringing"
        self.last_rx_at = time.monotonic()
        self._spawn(self._watchdog())
        self._spawn(self._media_from_transport())
        if self.role == "caller":
            self._spawn(self._invite_loop())
        else:
            # Callee waits for INVITE; nothing to send proactively.
            pass

    async def wait_connected(self, timeout: float | None = None) -> bool:
        if timeout is None:
            timeout = self.answer_timeout_s
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=timeout)
            return True
        except TimeoutError:
            return False

    async def wait_ended(self, timeout: float | None = None) -> bool:
        try:
            await asyncio.wait_for(self._ended.wait(), timeout=timeout)
            return True
        except TimeoutError:
            return False

    def _on_udp_closed(self, exc: Exception | None) -> None:
        if not self._closing and self.state not in {"ended"}:
            self.reason = self.reason or "udp_closed"
            self._spawn(self._end("udp_closed", send_bye=False))

    def _send_raw(self, payload: bytes, addr: tuple[str, int] | None = None) -> None:
        if self._transport is None:
            return
        self._transport.sendto(payload, addr or self.peer_addr)

    async def _send_signal(self, kind: int, *, port: int = 0, epk: bytes | None = None, reason: str = "", addr: tuple[str, int] | None = None) -> None:
        if self.local_epk is None:
            self.local_esk, self.local_epk = generate_x25519_keypair()
        msg = encode_signal(kind, self.call_id, port=port, epk=epk or self.local_epk, reason=reason)
        self._send_raw(msg, addr=addr)

    def _on_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        if self._closing or self.state == "ended":
            return
        # Accept packets from the configured peer only.
        if addr[0] != self.peer_addr[0]:
            return
        if self.peer_addr[1] != 0 and addr[1] != self.peer_addr[1]:
            return
        # Signal messages are small; audio packets start with magic.
        if data.startswith(DROPIN_MAGIC) and len(data) >= _AUDIO_HEADER_LEN + _AEAD_TAG_LEN:
            self._on_audio_packet(data)
            return
        try:
            sig = decode_signal(data)
        except Exception:
            return
        if sig.call_id != self.call_id and not (
            self.role == "callee" and self.state == "ringing" and sig.kind == M_INVITE
        ):
            return
        self._spawn(self._handle_signal(sig, addr))

    async def _handle_signal(self, sig: SignalMessage, addr: tuple[str, int]) -> None:
        if self.state == "ended":
            return
        # Learn peer port on first valid signal.
        if self.peer_addr[1] == 0:
            self.peer_addr = (self.peer_addr[0], addr[1])

        if sig.kind == M_BYE:
            await self._end(sig.reason or "bye", send_bye=False)
            return

        if self.role == "caller":
            await self._handle_signal_as_caller(sig)
        else:
            await self._handle_signal_as_callee(sig, addr)

    async def _handle_signal_as_caller(self, sig: SignalMessage) -> None:
        if sig.kind == M_RINGING and self.state == "calling":
            self.state = "ringing"
            self.last_rx_at = time.monotonic()
            return
        if sig.kind == M_ACCEPT and self.state in {"calling", "ringing"}:
            self.peer_epk = sig.epk
            if sig.port > 0:
                # dropin.c learns the peer's audio UDP port from the signal.
                self.peer_addr = (self.peer_addr[0], sig.port)
            self.last_rx_at = time.monotonic()
            try:
                self.session_key = derive_session_key(
                    network_key=self.network_key,
                    local_esk=self._require_local_esk(),
                    peer_epk=self.peer_epk,
                    call_id=self.call_id,
                    caller_id=self.local_id,
                    callee_id=self.peer_id,
                )
            except Exception:
                await self._end("bad_session_key", send_bye=True)
                return
            self.state = "connected"
            self._connected.set()
            return
        if sig.kind == M_REFUSE:
            await self._end(sig.reason or "refused", send_bye=False)

    async def _handle_signal_as_callee(self, sig: SignalMessage, addr: tuple[str, int]) -> None:
        if sig.kind == M_INVITE and self.state == "ringing":
            # Adopt call_id/peer from invite for simple callee use.
            self.call_id = sig.call_id
            self.peer_id = sig.epk[:_CALL_ID_LEN] if self.peer_id == b"\x00" * _CALL_ID_LEN else self.peer_id
            self.peer_epk = sig.epk
            self.peer_addr = (addr[0], addr[1])
            self.last_rx_at = time.monotonic()
            # Auto-accept path for direct session use; bridge may override by handling externally.
            if self.session_key is None:
                try:
                    self.session_key = derive_session_key(
                        network_key=self.network_key,
                        local_esk=self._require_local_esk(),
                        peer_epk=self.peer_epk,
                        call_id=self.call_id,
                        caller_id=self.peer_id,
                        callee_id=self.local_id,
                    )
                except Exception:
                    await self._send_signal(M_REFUSE, reason="bad_key", addr=addr)
                    await self._end("bad_session_key", send_bye=False)
                    return
                await self._send_signal(M_ACCEPT, port=self.local_port, addr=addr)
                self.state = "connected"
                self._connected.set()
            return

    def _require_local_esk(self) -> bytes:
        if self.local_esk is None:
            raise DropInBridgeError("local keypair not initialized")
        return self.local_esk

    def _on_audio_packet(self, data: bytes) -> None:
        if self.session_key is None or self.state != "connected":
            return
        try:
            seq, opus = open_audio_frame(self.session_key, data, self.call_id, self.peer_direction)
        except DropInCryptoError:
            return
        if not self._replay.accept(seq):
            return
        self.last_rx_at = time.monotonic()
        self._spawn(self._forward_to_media(opus))

    async def _forward_to_media(self, opus: bytes) -> None:
        try:
            await self.media.send_opus(opus)
        except Exception:
            await self._end("media_send_failed", send_bye=True)

    async def _media_from_transport(self) -> None:
        try:
            while not self._ended.is_set():
                frame = await self.media.recv_opus()
                if frame is None:
                    if self.state == "connected":
                        await self._end("media_closed", send_bye=True)
                    return
                if self.state != "connected" or self.session_key is None:
                    continue
                seq = self._tx_seq
                self._tx_seq = (self._tx_seq + 1) & _SEQ_MAX
                try:
                    packet = seal_audio_frame(
                        self.session_key, self.call_id, self.local_direction, seq, frame
                    )
                except Exception:
                    await self._end("encrypt_failed", send_bye=True)
                    return
                self._send_raw(packet)
        except asyncio.CancelledError:
            return
        except Exception:
            if self.state != "ended":
                await self._end("media_recv_failed", send_bye=True)

    async def _invite_loop(self) -> None:
        try:
            started = time.monotonic()
            while not self._ended.is_set() and self.state in {"calling", "ringing"}:
                await self._send_signal(M_INVITE, port=self.local_port)
                now = time.monotonic()
                if self.state == "calling" and (now - started) >= self.answer_timeout_s:
                    # Neither M_RINGING nor M_ACCEPT arrived in time (ANSWER_MS in
                    # dropin.c). Stop inviting; the longer ring timeout only applies
                    # once the callee has actually replied with M_RINGING.
                    await self._end("no_answer", send_bye=True)
                    return
                if (now - started) >= self.ring_timeout_s:
                    await self._end("ring_timeout", send_bye=True)
                    return
                await asyncio.sleep(self.invite_interval_s)
        except asyncio.CancelledError:
            return

    async def _watchdog(self) -> None:
        try:
            while not self._ended.is_set():
                await asyncio.sleep(min(0.25, self.lost_media_timeout_s))
                if (
                    self.state == "connected"
                    and self.last_rx_at is not None
                    and (time.monotonic() - self.last_rx_at) > self.lost_media_timeout_s
                ):
                    await self._end("lost_media", send_bye=True)
                    return
        except asyncio.CancelledError:
            return

    async def hangup(self, reason: str = "cancelled") -> None:
        await self._end(reason, send_bye=True)

    async def _end(self, reason: str, *, send_bye: bool) -> None:
        if self._closing:
            await self._ended.wait()
            return
        self._closing = True
        self.reason = reason
        if send_bye and self._transport is not None:
            for _ in range(BYE_REPEATS):
                try:
                    await self._send_signal(M_BYE, reason=reason)
                except Exception:
                    break
        self.state = "ended"
        self._connected.set()
        self._ended.set()
        # Wipe key references.
        self.session_key = None
        self.local_esk = None
        self.peer_epk = None
        self.network_key = b""
        current = asyncio.current_task()
        for task in list(self._tasks):
            if task is not current:
                task.cancel()
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        with contextlib.suppress(Exception):
            await self.media.close()


@dataclass(slots=True)
class EchoDropInPeer:
    peer_id: bytes
    node: str
    ip: str
    port: int = DROPIN_PORT


def _decode_network_key(raw: str) -> bytes:
    if not isinstance(raw, str) or not raw.strip():
        raise DropInBridgeError(
            "HOMEHUB_ECHO_MESH_KEY is not configured; set HOMEHUB_ECHO_MESH_KEY to a base64-encoded 32-byte network key"
        )
    try:
        key = base64.b64decode(raw.strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise DropInBridgeError(
            "HOMEHUB_ECHO_MESH_KEY must be a valid base64-encoded 32-byte network key"
        ) from exc
    if len(key) != _KEY_LEN:
        raise DropInBridgeError(
            f"HOMEHUB_ECHO_MESH_KEY must decode to {_KEY_LEN} bytes, got {len(key)}"
        )
    return key


class EchoDropInBridge:
    """Registry and lifecycle manager for Drop-In sessions."""

    def __init__(self, *, network_key: bytes | None = None) -> None:
        self._network_key: bytes | None = None
        self.peers: dict[str, EchoDropInPeer] = {}
        self.sessions: dict[str, EchoDropInSession] = {}
        self._lock = asyncio.Lock()
        if network_key is not None:
            self.set_network_key(network_key)

    @property
    def configured(self) -> bool:
        return self._network_key is not None

    def set_network_key(self, key_bytes: bytes) -> None:
        self._network_key = _require_len("network_key", key_bytes, _KEY_LEN)

    def load_network_key_from_env(self, env_name: str = "HOMEHUB_ECHO_MESH_KEY") -> bytes:
        raw = os.getenv(env_name)
        if raw is None:
            raise DropInBridgeError(
                f"{env_name} is not configured; set {env_name} to a base64-encoded 32-byte network key"
            )
        key = _decode_network_key(raw)
        self.set_network_key(key)
        return key

    def require_network_key(self) -> bytes:
        if self._network_key is None:
            self.load_network_key_from_env("HOMEHUB_ECHO_MESH_KEY")
        assert self._network_key is not None
        return self._network_key

    def register_peer(self, *, peer_id: bytes, node: str, ip: str, port: int = DROPIN_PORT) -> EchoDropInPeer:
        if not isinstance(node, str) or not node.strip():
            raise DropInBridgeError("node must be a non-empty string")
        if not isinstance(ip, str) or not ip.strip():
            raise DropInBridgeError("ip must be a non-empty string")
        peer = EchoDropInPeer(peer_id=_require_len("peer_id", peer_id, _CALL_ID_LEN), node=node.strip(), ip=ip.strip(), port=int(port))
        self.peers[peer.node] = peer
        return peer

    def get_peer(self, node: str) -> EchoDropInPeer:
        peer = self.peers.get(node)
        if peer is None:
            raise DropInBridgeError(f"unknown echo peer: {node}")
        return peer

    async def start_call(
        self,
        node: str,
        media_transport: BridgeMediaTransport,
        *,
        local_id: bytes | None = None,
        call_id: bytes | None = None,
        lost_media_timeout_s: float = LOST_MEDIA_TIMEOUT_S,
    ) -> EchoDropInSession:
        network_key = self.require_network_key()
        peer = self.get_peer(node)
        async with self._lock:
            existing = self.sessions.get(node)
            if existing is not None and existing.state != "ended":
                raise DropInBridgeError("busy")
            session = EchoDropInSession(
                network_key=network_key,
                local_id=_require_len("local_id", local_id, _CALL_ID_LEN) if local_id is not None else os.urandom(_CALL_ID_LEN),
                peer_id=peer.peer_id,
                peer_addr=(peer.ip, peer.port),
                media_transport=media_transport,
                role="caller",
                call_id=call_id,
                lost_media_timeout_s=lost_media_timeout_s,
            )
            self.sessions[node] = session
        try:
            await session.start()
        except Exception:
            async with self._lock:
                if self.sessions.get(node) is session:
                    self.sessions.pop(node, None)
            with contextlib.suppress(Exception):
                await session.hangup("start_failed")
            raise
        return session

    async def end_call(self, call_id: str | bytes) -> EchoDropInSession:
        target = self._find_session(call_id)
        if target is None:
            raise KeyError(str(call_id))
        await target.hangup("hangup")
        async with self._lock:
            for node, sess in list(self.sessions.items()):
                if sess is target and sess.state == "ended":
                    self.sessions.pop(node, None)
        return target

    def _find_session(self, call_id: str | bytes) -> EchoDropInSession | None:
        wanted = call_id.hex() if isinstance(call_id, bytes) else str(call_id).strip().lower().removeprefix("0x")
        for session in self.sessions.values():
            if session.call_id.hex() == wanted:
                return session
        return None

    def status(self) -> dict[str, Any]:
        active = []
        for node, session in self.sessions.items():
            if session.state == "ended":
                continue
            active.append(
                {
                    "node": node,
                    "call_id": session.call_id.hex(),
                    "state": session.state,
                    "peer_addr": {"ip": session.peer_addr[0], "port": session.peer_addr[1]},
                    "reason": session.reason,
                }
            )
        return {
            "configured": self.configured,
            "active_sessions": active,
            "peers": [
                {"node": p.node, "peer_id": p.peer_id.hex(), "ip": p.ip, "port": p.port}
                for p in self.peers.values()
            ],
        }
