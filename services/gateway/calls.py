"""In-app family calls.

The gateway is only the signalling relay: browsers exchange SDP offers,
answers and ICE candidates here, then send audio and video directly to one
another over WebRTC. No call media passes through this service.

Rooms are Nextcloud Talk conversation tokens so the Family calls list can use
the family's existing conversations, but the call itself does not touch
Nextcloud Talk. Talk still owns the chat; this module owns the call.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect

from services.gateway.config import IDENTITY_SVC

log = logging.getLogger("gateway.calls")

router = APIRouter()

#: A mesh call works well for a family. Beyond this, every phone would encode
#: one stream per participant, so the room refuses rather than melting down.
MAX_PEERS = max(2, int(os.getenv("CALL_MAX_PEERS", "8")))
#: SDP is a few kilobytes; this is generous but stops a client from filling the
#: process with a single frame.
MAX_SIGNAL_BYTES = 128 * 1024
#: Talk tokens are short alphanumeric ids. Keeping the character set tight
#: means a room key can never collide with a path or a Redis-style key.
ROOM_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

#: A public STUN server is enough for a home network and most NATs. A TURN
#: relay can be supplied through CALL_ICE_SERVERS without changing the code.
DEFAULT_ICE_SERVERS: list[dict[str, Any]] = [
    {"urls": ["stun:stun.l.google.com:19302", "stun:stun1.l.google.com:19302"]},
    {"urls": ["stun:stun.cloudflare.com:3478"]},
]


def _ice_servers() -> list[dict[str, Any]]:
    raw = (os.getenv("CALL_ICE_SERVERS") or "").strip()
    if not raw:
        return DEFAULT_ICE_SERVERS
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("[calls] CALL_ICE_SERVERS is not valid JSON; using public STUN")
        return DEFAULT_ICE_SERVERS
    if not isinstance(parsed, list) or not parsed:
        log.warning("[calls] CALL_ICE_SERVERS must be a non-empty list; using public STUN")
        return DEFAULT_ICE_SERVERS
    return parsed


ICE_SERVERS = _ice_servers()


@dataclass
class Peer:
    """One connected browser in one call room."""

    id: str
    room: str
    username: str
    name: str
    websocket: WebSocket
    muted: bool = False
    camera: bool = False
    joined_at: float = field(default_factory=time.time)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "username": self.username,
            "name": self.name,
            "muted": self.muted,
            "camera": self.camera,
        }


class CallHub:
    """The in-process rooms and the people in them.

    Single-worker gateway: one uvicorn process owns these dicts. A gateway
    restart ends calls, which is the same thing a dropped signalling socket
    means to the browsers.
    """

    def __init__(self) -> None:
        self._rooms: dict[str, dict[str, Peer]] = {}
        self._names: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def join(self, peer: Peer, call_name: str = "") -> list[Peer] | None:
        """Add a peer; return the people already in the room, or None if full."""
        async with self._lock:
            room = self._rooms.setdefault(peer.room, {})
            if len(room) >= MAX_PEERS:
                return None
            existing = list(room.values())
            room[peer.id] = peer
            if call_name and peer.room not in self._names:
                self._names[peer.room] = call_name[:100]
            return existing

    async def leave(self, peer: Peer) -> list[Peer]:
        """Remove a peer and return whoever is left in its room."""
        async with self._lock:
            room = self._rooms.get(peer.room)
            if not room or peer.id not in room:
                return []
            del room[peer.id]
            if not room:
                self._rooms.pop(peer.room, None)
                self._names.pop(peer.room, None)
            return list(room.values())

    async def room_name(self, room: str) -> str:
        async with self._lock:
            return self._names.get(room, "")

    async def snapshot(self) -> list[dict[str, Any]]:
        """The active calls the Family list polls."""
        async with self._lock:
            calls = []
            for token, room in self._rooms.items():
                if not room:
                    continue
                participants = sorted(room.values(), key=lambda p: p.joined_at)
                calls.append(
                    {
                        "token": token,
                        "name": self._names.get(token, ""),
                        "started_at": participants[0].joined_at,
                        "participants": [p.public() for p in participants],
                    }
                )
            calls.sort(key=lambda c: c["started_at"], reverse=True)
            return calls

    async def target(self, peer: Peer, target_id: object) -> Peer | None:
        async with self._lock:
            room = self._rooms.get(peer.room, {})
            target = room.get(str(target_id or ""))
            return target

    async def relay(self, peer: Peer, message: dict[str, Any]) -> None:
        target = await self.target(peer, message.get("to"))
        if target is None or target.id == peer.id:
            return
        payload = {
            "type": message["type"],
            "from": peer.id,
            "sdp": message.get("sdp"),
            "candidate": message.get("candidate"),
        }
        await _send(target, payload)

    async def broadcast(self, peers: list[Peer], payload: dict[str, Any]) -> None:
        if not peers:
            return
        await asyncio.gather(*(_send(peer, payload) for peer in peers), return_exceptions=True)

    async def update_state(self, peer: Peer, muted: object, camera: object) -> None:
        peer.muted = bool(muted)
        peer.camera = bool(camera)
        async with self._lock:
            peers = list(self._rooms.get(peer.room, {}).values())
        await self.broadcast(peers, {"type": "peer-state", "peer": peer.public()})


hub = CallHub()


async def _send(peer: Peer, payload: dict[str, Any]) -> None:
    try:
        async with peer.send_lock:
            await peer.websocket.send_json(payload)
    except Exception as e:  # a closed socket is normal during a call
        log.debug(f"[calls] could not send to {peer.id}: {type(e).__name__}: {e}")


async def _user_for_token(token: str) -> dict[str, Any] | None:
    """Resolve a Jarvis API key through Identity, as the other WebSockets do."""
    if not token:
        return None
    timeout = aiohttp.ClientTimeout(total=5)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session, session.get(
            f"{IDENTITY_SVC}/api/users/me",
            headers={"Authorization": f"Bearer {token}"},
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            return data if isinstance(data, dict) else None
    except Exception as e:
        log.warning(f"[calls] identity lookup failed: {type(e).__name__}: {e}")
        return None


async def _caller(request: Request) -> dict[str, Any]:
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    user = await _user_for_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in to see family calls.")
    return user


def _display_name(user: dict[str, Any]) -> str:
    return str(user.get("full_name") or user.get("display_name") or user.get("username") or "Family member")


@router.get("/api/communication/calls")
async def list_calls(_user: dict[str, Any] = Depends(_caller)) -> dict[str, Any]:
    """Every call currently in progress, for the Family Calls tab."""
    return {"calls": await hub.snapshot()}


@router.websocket("/api/communication/calls/ws")
async def calls_websocket(
    websocket: WebSocket,
    token: str = "",
    room: str = "",
    name: str = "",
) -> None:
    if not token or not ROOM_RE.match(room or ""):
        await websocket.close(code=1008, reason="Missing call token or room")
        return

    user = await _user_for_token(token)
    if not user:
        await websocket.close(code=1008, reason="Invalid token")
        return

    await websocket.accept()

    peer = Peer(
        id=uuid.uuid4().hex[:12],
        room=room,
        username=str(user.get("username") or ""),
        name=_display_name(user),
        websocket=websocket,
    )
    existing = await hub.join(peer, name)
    if existing is None:
        await _send(peer, {"type": "error", "message": "That call is full."})
        await websocket.close(code=1008, reason="Call is full")
        return

    await _send(
        peer,
        {
            "type": "welcome",
            "self": peer.public(),
            "peers": [p.public() for p in existing],
            "ice_servers": ICE_SERVERS,
            "room": room,
            "name": await hub.room_name(room),
        },
    )
    await hub.broadcast(existing, {"type": "peer-joined", "peer": peer.public()})
    log.info(f"[calls] {peer.name} joined {room} ({len(existing) + 1} in the call)")

    try:
        while True:
            raw = await websocket.receive_text()
            if len(raw) > MAX_SIGNAL_BYTES:
                await _send(peer, {"type": "error", "message": "That signalling message is too large."})
                continue
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(message, dict):
                continue

            kind = message.get("type")
            if kind in ("offer", "answer", "ice"):
                await hub.relay(peer, message)
            elif kind == "state":
                await hub.update_state(peer, message.get("muted", peer.muted), message.get("camera", peer.camera))
            elif kind == "ping":
                await _send(peer, {"type": "pong"})
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.warning(f"[calls] {peer.name} signalling failed: {type(e).__name__}: {e}")
    finally:
        remaining = await hub.leave(peer)
        await hub.broadcast(remaining, {"type": "peer-left", "id": peer.id})
        log.info(f"[calls] {peer.name} left {room} ({len(remaining)} remaining)")
