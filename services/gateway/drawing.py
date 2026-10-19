"""Live family drawing boards.

The gateway keeps one small, in-memory board per room and relays brush
segments between the people drawing on it. The canvas owns the pixels; this
module only carries the geometry, so a phone that joins late can replay the
board without downloading an image per stroke.

Boards live for as long as the gateway process. That is the same lifetime the
family calls already have, and it means a drawing is never mistaken for a
saved family creation: the Create tab still sends finished drawings to chat or
downloads them explicitly.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

import aiohttp
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from services.gateway.config import IDENTITY_SVC

log = logging.getLogger("gateway.drawing")

router = APIRouter()

#: Board codes are short, readable and safe in a query string or log line.
ROOM_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
#: The client sends a uuid, a counter or any other stable short id.
SEGMENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

CANVAS_WIDTH = 640
CANVAS_HEIGHT = 416
#: A long family session is still tiny at this size (about 1 MB of JSON).
MAX_SEGMENTS = 5000
#: A drawing board is not a call: it can hold every phone in the house.
MAX_PEERS = max(2, int(os.getenv("DRAWING_MAX_PEERS", "12")))
MAX_MESSAGE_BYTES = 8 * 1024
#: Keep the process bounded if people create boards and never come back.
MAX_BOARDS = max(1, int(os.getenv("DRAWING_MAX_BOARDS", "100")))


@dataclass
class Drawer:
    """One connected browser on one board."""

    id: str
    room: str
    username: str
    name: str
    websocket: WebSocket
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "username": self.username, "name": self.name}


@dataclass
class Board:
    segments: list[dict[str, Any]] = field(default_factory=list)
    drawers: dict[str, Drawer] = field(default_factory=dict)
    touched_at: float = 0.0


class DrawingHub:
    """The in-process boards and the people connected to them."""

    def __init__(self) -> None:
        self._boards: dict[str, Board] = {}
        self._lock = asyncio.Lock()

    async def join(self, drawer: Drawer) -> tuple[list[Drawer], list[dict[str, Any]]] | None:
        """Add a drawer; return the people and strokes already on the board."""
        async with self._lock:
            board = self._boards.get(drawer.room)
            if board is None:
                self._make_room(drawer.room)
                board = self._boards.get(drawer.room)
            if board is None:
                return None
            if len(board.drawers) >= MAX_PEERS:
                return None
            existing = list(board.drawers.values())
            board.drawers[drawer.id] = drawer
            board.touched_at = asyncio.get_running_loop().time()
            return existing, [dict(segment) for segment in board.segments]

    async def leave(self, drawer: Drawer) -> list[Drawer]:
        async with self._lock:
            board = self._boards.get(drawer.room)
            if board is None:
                return []
            board.drawers.pop(drawer.id, None)
            board.touched_at = asyncio.get_running_loop().time()
            return list(board.drawers.values())

    async def others(self, drawer: Drawer) -> list[Drawer]:
        async with self._lock:
            board = self._boards.get(drawer.room)
            if board is None:
                return []
            return [peer for peer in board.drawers.values() if peer.id != drawer.id]

    async def add_segment(self, drawer: Drawer, raw: object) -> dict[str, Any] | None:
        """Validate and store one brush segment from ``drawer``."""
        segment = _clean_segment(raw, drawer)
        if segment is None:
            return None
        async with self._lock:
            board = self._boards.get(drawer.room)
            if board is None or len(board.segments) >= MAX_SEGMENTS:
                return None
            if any(existing.get("id") == segment["id"] for existing in board.segments):
                return None
            board.segments.append(segment)
            board.touched_at = asyncio.get_running_loop().time()
            return dict(segment)

    async def undo(self, drawer: Drawer, segment_id: object = None) -> str | None:
        """Remove one of this drawer's segments, newest first when no id is given."""
        wanted = str(segment_id or "")
        async with self._lock:
            board = self._boards.get(drawer.room)
            if board is None:
                return None
            for index in range(len(board.segments) - 1, -1, -1):
                segment = board.segments[index]
                if segment.get("user") != drawer.username:
                    continue
                if wanted and segment.get("id") != wanted:
                    continue
                board.segments.pop(index)
                board.touched_at = asyncio.get_running_loop().time()
                return str(segment.get("id") or "")
        return None

    async def clear(self, drawer: Drawer) -> bool:
        async with self._lock:
            board = self._boards.get(drawer.room)
            if board is None:
                return False
            changed = bool(board.segments)
            board.segments.clear()
            board.touched_at = asyncio.get_running_loop().time()
            return changed

    async def count(self, room: str) -> int:
        async with self._lock:
            board = self._boards.get(room)
            return len(board.drawers) if board else 0

    async def broadcast(self, drawers: list[Drawer], payload: dict[str, Any]) -> None:
        if not drawers:
            return
        await asyncio.gather(*(_send(drawer, payload) for drawer in drawers), return_exceptions=True)

    def _make_room(self, room: str) -> None:
        """Create a board, evicting the oldest empty one when the cap is reached."""
        if len(self._boards) < MAX_BOARDS:
            self._boards[room] = Board()
            return
        empty = [(key, board) for key, board in self._boards.items() if not board.drawers]
        if not empty:
            return
        oldest = min(empty, key=lambda item: item[1].touched_at)
        self._boards.pop(oldest[0], None)
        self._boards[room] = Board()


hub = DrawingHub()


def _clean_segment(raw: object, drawer: Drawer) -> dict[str, Any] | None:
    """Only finite in-bounds geometry reaches the board."""
    if not isinstance(raw, dict):
        return None
    x1 = _number(raw.get("x1"), 0, CANVAS_WIDTH)
    y1 = _number(raw.get("y1"), 0, CANVAS_HEIGHT)
    x2 = _number(raw.get("x2"), 0, CANVAS_WIDTH)
    y2 = _number(raw.get("y2"), 0, CANVAS_HEIGHT)
    width = _number(raw.get("width"), 1, 32)
    if None in (x1, y1, x2, y2, width):
        return None
    color = raw.get("color")
    color = color if isinstance(color, str) and COLOR_RE.fullmatch(color) else "#1e293b"
    segment_id = raw.get("id")
    if not isinstance(segment_id, str) or not SEGMENT_ID_RE.fullmatch(segment_id):
        segment_id = uuid.uuid4().hex
    return {
        "id": segment_id,
        "x1": x1,
        "y1": y1,
        "x2": x2,
        "y2": y2,
        "color": color,
        "width": width,
        "user": drawer.username,
        "name": drawer.name,
    }


def _number(value: object, minimum: float, maximum: float) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < minimum or number > maximum:
        return None
    return round(number, 2)


async def _send(drawer: Drawer, payload: dict[str, Any]) -> None:
    try:
        async with drawer.send_lock:
            await drawer.websocket.send_json(payload)
    except Exception as e:  # a closed socket is normal when someone leaves
        log.debug(f"[drawing] could not send to {drawer.id}: {type(e).__name__}: {e}")


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
        log.warning(f"[drawing] identity lookup failed: {type(e).__name__}: {e}")
        return None


def _display_name(user: dict[str, Any]) -> str:
    return str(user.get("full_name") or user.get("display_name") or user.get("username") or "Family member")


@router.websocket("/api/family/drawing/ws")
async def drawing_websocket(
    websocket: WebSocket,
    token: str = "",
    room: str = "",
) -> None:
    if not token or not ROOM_RE.match(room or ""):
        await websocket.close(code=1008, reason="Missing drawing token or room")
        return

    user = await _user_for_token(token)
    if not user:
        await websocket.close(code=1008, reason="Invalid token")
        return

    await websocket.accept()

    drawer = Drawer(
        id=uuid.uuid4().hex[:12],
        room=room,
        username=str(user.get("username") or ""),
        name=_display_name(user),
        websocket=websocket,
    )
    joined = await hub.join(drawer)
    if joined is None:
        await _send(drawer, {"type": "error", "message": "That drawing board is full."})
        await websocket.close(code=1008, reason="Drawing board full")
        return

    existing, segments = joined
    await _send(
        drawer,
        {
            "type": "welcome",
            "self": drawer.public(),
            "participants": [peer.public() for peer in existing] + [drawer.public()],
            "segments": segments,
            "room": room,
        },
    )
    await hub.broadcast(existing, {"type": "participant-joined", "participant": drawer.public()})
    log.info(f"[drawing] {drawer.name} joined {room} ({len(existing) + 1} drawing)")

    try:
        while True:
            raw = await websocket.receive_text()
            if len(raw) > MAX_MESSAGE_BYTES:
                await _send(drawer, {"type": "error", "message": "That drawing message is too large."})
                continue
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(message, dict):
                continue

            kind = message.get("type")
            if kind == "segment":
                segment = await hub.add_segment(drawer, message.get("segment"))
                if segment is not None:
                    await hub.broadcast(await hub.others(drawer), {"type": "segment", "segment": segment})
            elif kind == "undo":
                segment_id = await hub.undo(drawer, message.get("id"))
                if segment_id is not None:
                    await hub.broadcast(await hub.others(drawer), {"type": "undo", "id": segment_id})
            elif kind == "clear":
                if await hub.clear(drawer):
                    await hub.broadcast(await hub.others(drawer), {"type": "clear", "by": drawer.public()})
            elif kind == "ping":
                await _send(drawer, {"type": "pong"})
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.warning(f"[drawing] {drawer.name} connection failed: {type(e).__name__}: {e}")
    finally:
        remaining = await hub.leave(drawer)
        await hub.broadcast(remaining, {"type": "participant-left", "id": drawer.id})
        log.info(f"[drawing] {drawer.name} left {room} ({len(remaining)} drawing)")
