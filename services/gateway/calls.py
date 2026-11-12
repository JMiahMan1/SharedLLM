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
import base64
import hashlib
import hmac
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

from services.gateway.config import IDENTITY_SVC, INTERNAL_SECRET

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
#: How long a new call rings the people it calls. A phone with auto drop-in on
#: answers by itself inside this window; everyone else sees an incoming call
#: they can answer or decline. After it, the call is still listed and can be
#: joined, it just stops ringing.
RING_SECONDS = 60

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

#: How long a TURN credential handed to one caller stays valid. A drop-in
#: call can run for a long while; the relay only checks it when the call
#: allocates (and refreshes) its relay, so a few hours is plenty.
TURN_CREDENTIAL_SECONDS = 6 * 3600


def turn_ice_server(username: str, now: float | None = None) -> dict[str, Any] | None:
    """A short-lived TURN credential for coturn's ``use-auth-secret`` mode.

    Without a relay, two phones behind different NATs (one on mobile data)
    often cannot send each other audio at all. coturn on the home server
    checks ``credential == base64(HMAC-SHA1(TURN_SECRET, username))`` where
    ``username`` is ``<expiry>:<who>``, so no per-user password is stored and a
    leaked credential expires on its own. None when TURN is not configured.
    """
    secret = (os.getenv("TURN_SECRET") or "").strip()
    urls = [u.strip() for u in (os.getenv("TURN_URLS") or "").split(",") if u.strip()]
    if not secret or not urls:
        return None
    expiry = int((now if now is not None else time.time()) + TURN_CREDENTIAL_SECONDS)
    user = f"{expiry}:{re.sub(r'[^A-Za-z0-9_.-]', '', username) or 'jarvis'}"
    digest = hmac.new(secret.encode(), user.encode(), hashlib.sha1).digest()
    return {"urls": urls, "username": user, "credential": base64.b64encode(digest).decode()}


def ice_servers_for(username: str) -> list[dict[str, Any]]:
    """The ICE servers one caller gets: STUN, plus a TURN relay when set up."""
    turn = turn_ice_server(username)
    return [*ICE_SERVERS, turn] if turn else ICE_SERVERS


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


@dataclass
class Ring:
    """Who a call is ringing: the caller rang the conversation's members."""

    caller: str
    caller_name: str
    recipients: set[str]
    started_at: float = field(default_factory=time.time)
    declined: set[str] = field(default_factory=set)
    #: Recipients who let the caller drop in: their devices answer by themselves.
    drop_in: set[str] = field(default_factory=set)

    def rings(self, username: str, now: float) -> bool:
        return (
            username in self.recipients
            and username not in self.declined
            and now - self.started_at < RING_SECONDS
        )


class CallHub:
    """The in-process rooms and the people in them.

    Single-worker gateway: one uvicorn process owns these dicts. A gateway
    restart ends calls, which is the same thing a dropped signalling socket
    means to the browsers.

    A room's ``_members`` are the conversation's members, recorded when the
    caller rings them. Once they are known the call is listed only to them,
    so a direct call between two people never shows up (or gets auto-answered
    by a drop-in device) anywhere else in the family.
    """

    def __init__(self) -> None:
        self._rooms: dict[str, dict[str, Peer]] = {}
        self._names: dict[str, str] = {}
        self._members: dict[str, set[str]] = {}
        self._rings: dict[str, Ring] = {}
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
                self._members.pop(peer.room, None)
                self._rings.pop(peer.room, None)
            return list(room.values())

    async def is_in(self, room: str, username: str) -> bool:
        async with self._lock:
            return any(p.username == username for p in self._rooms.get(room, {}).values())

    async def ring(
        self, room: str, caller: str, caller_name: str, recipients: set[str],
        drop_in: set[str] | None = None,
    ) -> bool:
        """Start ringing a call's other members. False if it already rang or ended.

        One ring per call: a second "ring" (a retry, a second tab) changes
        nothing, so nobody gets two incoming-call alerts for one call.
        """
        async with self._lock:
            room_peers = self._rooms.get(room)
            if not room_peers or room in self._rings:
                return False
            present = {p.username for p in room_peers.values()}
            self._members[room] = {caller, *recipients}
            self._rings[room] = Ring(
                caller, caller_name, set(recipients) - present, drop_in=set(drop_in or ()) & set(recipients)
            )
            return True

    async def decline(self, room: str, username: str) -> list[Peer]:
        """Stop ringing ``username``; returns the people in the call to tell."""
        async with self._lock:
            ring = self._rings.get(room)
            if ring is None or username not in ring.recipients:
                return []
            ring.declined.add(username)
            return list(self._rooms.get(room, {}).values())

    async def room_name(self, room: str) -> str:
        async with self._lock:
            return self._names.get(room, "")

    async def snapshot(self, username: str = "") -> list[dict[str, Any]]:
        """The active calls ``username`` may see, for the Family list.

        ``ringing`` is true while the call is ringing this person: their app
        shows an incoming call, or answers it by itself with auto drop-in on.
        """
        now = time.time()
        async with self._lock:
            calls = []
            for token, room in self._rooms.items():
                if not room:
                    continue
                participants = sorted(room.values(), key=lambda p: p.joined_at)
                present = {p.username for p in participants}
                members = self._members.get(token)
                if members is not None and username not in members and username not in present:
                    continue
                ring = self._rings.get(token)
                calls.append(
                    {
                        "token": token,
                        "name": self._names.get(token, ""),
                        "started_at": participants[0].joined_at,
                        "participants": [p.public() for p in participants],
                        "ringing": bool(ring and username not in present and ring.rings(username, now)),
                        "caller": ring.caller_name if ring else "",
                        # This person lets the caller drop in: answer by itself.
                        "drop_in": bool(ring and username in ring.drop_in),
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


def talk_recipients(
    people: list[Any], users: list[Any], me: str
) -> tuple[str, set[str], list[str]]:
    """Map a Talk room's members to the family usernames a ring reaches.

    Returns ``me``'s display name in the room, the other members' usernames,
    and the Talk actor ids that matched nobody (for the log). Nextcloud user
    ids keep the case they were created with ("Noah") while Identity stores
    them as typed ("noah"), so the match ignores case: an exact match missed
    that member on every call to them. The system account never counts.
    """
    by_talk: dict[str, str] = {}
    for u in users:
        if not isinstance(u, dict) or u.get("is_system_default"):
            continue
        username = str(u.get("username") or "")
        talk_id = str(u.get("nextcloud_user") or "")
        if username and talk_id:
            by_talk[talk_id.casefold()] = username
    display = ""
    recipients: set[str] = set()
    unmatched: list[str] = []
    for person in people:
        if not isinstance(person, dict):
            continue
        actor = str(person.get("actor_id") or "")
        if person.get("actor_type") not in (None, "users"):
            continue  # guests and e-mail invitees have no phone to ring
        username = by_talk.get(actor.casefold())
        if not username:
            if actor:
                unmatched.append(actor)
            continue
        if username == me:
            display = str(person.get("display_name") or username)
            continue
        recipients.add(username)
    return display or me, recipients, unmatched


def one_to_one_members(conversation: Any) -> list[dict[str, Any]]:
    """The other member of a one-to-one Talk room, from the room itself.

    Talk names a one-to-one room after the other person's user id, so the
    room alone says whom to ring when its member list cannot be read.
    """
    if not isinstance(conversation, dict) or conversation.get("type") != 1:
        return []
    other = str(conversation.get("name") or "")
    if not other:
        return []
    return [{"actor_type": "users", "actor_id": other, "display_name": conversation.get("display_name") or other}]


@router.get("/api/communication/calls")
async def list_calls(device_key: str = "", user: dict[str, Any] = Depends(_caller)) -> dict[str, Any]:
    """The calls in progress this person may see, for the Family Calls tab.

    With ``device_key``, also whether this device answers drop-ins by itself
    (``device_drop_in``), so a parent's change on a child's phone arrives with
    the next poll and the phone can store it for answering in the background.
    """
    out: dict[str, Any] = {"calls": await hub.snapshot(str(user.get("username") or ""))}
    if device_key and len(device_key) <= 128:
        out["device_drop_in"] = (await device_drop_in([device_key]))[device_key]
    return out


@router.post("/api/communication/calls/{room}/decline")
async def decline_call(room: str, user: dict[str, Any] = Depends(_caller)) -> dict[str, Any]:
    """Stop the call ringing this person and tell the people in it."""
    if not ROOM_RE.match(room):
        raise HTTPException(status_code=422, detail="Unknown call.")
    peers = await hub.decline(room, str(user.get("username") or ""))
    await hub.broadcast(
        peers,
        {"type": "declined", "username": user.get("username") or "", "name": _display_name(user)},
    )
    return {"declined": bool(peers)}


# ---------------------------------------------------------------------------
# Who may drop in on whom
# ---------------------------------------------------------------------------
#
# Each person has a list of family members allowed to drop in on them: a ring
# from one of them is answered by that person's devices by themselves, any
# other ring just rings. Adults choose their own list; a child's or teen's is
# chosen by a parent (father, mother or an admin), and they cannot change it.
# Kept in Redis (append-only, on a volume), one hash field per person.

DROP_IN_KEY = "calls:drop_in"
#: Per device: "0" means this device never answers by itself. Absent is on,
#: so the account's list decides. Room speakers (ESPHome assistants) can be
#: devices here too, keyed by their device_key like a phone.
DROP_IN_DEVICES_KEY = "calls:drop_in_devices"
PARENT_ROLES = frozenset({"father", "mother"})
MINOR_ROLES = frozenset({"child", "teen"})


def is_parent(user: dict[str, Any]) -> bool:
    return bool(user.get("is_admin")) or user.get("family_role") in PARENT_ROLES


def is_minor(user: dict[str, Any]) -> bool:
    return not user.get("is_admin") and user.get("family_role") in MINOR_ROLES


def may_set_drop_in(actor: dict[str, Any], target: dict[str, Any]) -> bool:
    """Adults set their own; parents set a child's or teen's. Nobody else's."""
    if str(actor.get("username") or "") == str(target.get("username") or ""):
        return not is_minor(actor)
    return is_minor(target) and is_parent(actor)


async def drop_in_lists(usernames: list[str]) -> dict[str, list[str] | None]:
    """Each person's allowed callers; None when they never chose (nobody)."""
    if not usernames:
        return {}
    from services.gateway.notices import _redis

    try:
        async with _redis() as conn:
            raw = await conn.hmget(DROP_IN_KEY, usernames)
    except Exception as e:
        log.warning(f"[calls] drop-in settings unavailable: {type(e).__name__}: {e}")
        return {u: None for u in usernames}
    out: dict[str, list[str] | None] = {}
    for user, value in zip(usernames, raw, strict=True):
        try:
            data = json.loads(value) if value else None
        except ValueError:
            data = None
        allow = data.get("allow_from") if isinstance(data, dict) else None
        out[user] = [str(a) for a in allow] if isinstance(allow, list) else None
    return out


async def device_drop_in(device_keys: list[str]) -> dict[str, bool]:
    """Whether each device answers drop-ins by itself (on unless switched off)."""
    if not device_keys:
        return {}
    from services.gateway.notices import _redis

    try:
        async with _redis() as conn:
            raw = await conn.hmget(DROP_IN_DEVICES_KEY, device_keys)
    except Exception as e:
        log.warning(f"[calls] device drop-in settings unavailable: {type(e).__name__}: {e}")
        return {k: True for k in device_keys}
    return {k: v != "0" for k, v in zip(device_keys, raw, strict=True)}


async def _owned_devices(username: str) -> list[dict[str, Any]]:
    """``username``'s phones and tablets from Identity ([] when unreadable)."""
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session, session.get(
            f"{IDENTITY_SVC}/api/internal/owned-devices/{username}",
            headers={"X-Internal-Secret": INTERNAL_SECRET},
        ) as resp:
            data = await resp.json(content_type=None) if resp.status == 200 else None
    except Exception as e:
        log.warning(f"[calls] could not list {username}'s devices: {type(e).__name__}: {e}")
        return []
    details = data.get("details") if isinstance(data, dict) else None
    return [
        d for d in (details or [])
        if isinstance(d, dict) and d.get("device_key") and not d.get("revoked")
    ]


async def drop_in_recipients(caller: str, recipients: set[str]) -> set[str]:
    """The recipients who let ``caller`` drop in on them."""
    lists = await drop_in_lists(sorted(recipients))
    return {r for r, allow in lists.items() if allow and caller in allow}


async def _family() -> list[dict[str, Any]]:
    """The household's people (not the system account), from Identity."""
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session, session.get(
            f"{IDENTITY_SVC}/api/users", headers={"X-Internal-Secret": INTERNAL_SECRET}
        ) as resp:
            users = await resp.json(content_type=None) if resp.status == 200 else None
    except Exception as e:
        log.warning(f"[calls] could not list the family: {type(e).__name__}: {e}")
        users = None
    if not isinstance(users, list):
        raise HTTPException(status_code=503, detail="Could not read the family list. Try again.")
    return [
        u for u in users
        if isinstance(u, dict) and u.get("username") and not u.get("is_system_default")
    ]


def _person(u: dict[str, Any]) -> dict[str, Any]:
    return {
        "username": u["username"],
        "name": _display_name(u),
        "minor": is_minor(u),
    }


@router.get("/api/communication/calls/drop-in")
async def get_drop_in(user: dict[str, Any] = Depends(_caller)) -> dict[str, Any]:
    """Who may drop in on you, and on each child or teen you look after.

    ``people`` is everyone who could be allowed. ``settings`` holds the lists
    this person can see: their own and, for a parent, every child's and
    teen's; ``can_edit`` says which of them they may change.
    """
    me = str(user.get("username") or "")
    family = await _family()
    visible = [u for u in family if u["username"] == me or (is_parent(user) and is_minor(u))]
    lists = await drop_in_lists([u["username"] for u in visible])
    owned = {u["username"]: await _owned_devices(u["username"]) for u in visible}
    switches = await device_drop_in([d["device_key"] for ds in owned.values() for d in ds])
    return {
        "me": me,
        "people": [_person(u) for u in family],
        "settings": [
            {
                **_person(u),
                "allow_from": lists.get(u["username"]) or [],
                "configured": lists.get(u["username"]) is not None,
                "can_edit": may_set_drop_in(user, u),
                "devices": [
                    {
                        "device_key": d["device_key"],
                        "label": str(d.get("label") or d.get("kind") or "Device"),
                        "kind": str(d.get("kind") or ""),
                        "drop_in": switches.get(d["device_key"], True),
                    }
                    for d in owned[u["username"]]
                ],
            }
            for u in visible
        ],
    }


@router.put("/api/communication/calls/drop-in/{username}")
async def put_drop_in(username: str, request: Request, user: dict[str, Any] = Depends(_caller)) -> dict[str, Any]:
    """Set who may drop in on ``username`` (yourself, or a child as a parent)."""
    try:
        body = await request.json()
    except ValueError:
        body = None
    allow = body.get("allow_from") if isinstance(body, dict) else None
    if not isinstance(allow, list) or not all(isinstance(a, str) for a in allow):
        raise HTTPException(status_code=422, detail="allow_from must be a list of usernames.")
    family = {u["username"]: u for u in await _family()}
    target = family.get(username)
    if target is None:
        raise HTTPException(status_code=404, detail="No such family member.")
    if not may_set_drop_in(user, target):
        raise HTTPException(
            status_code=403,
            detail="A parent chooses who can drop in on you." if username == user.get("username")
            else "You can change this only for yourself or a child.",
        )
    chosen = sorted({a for a in allow if a in family and a != username})
    from services.gateway.notices import _redis

    try:
        async with _redis() as conn:
            await conn.hset(DROP_IN_KEY, username, json.dumps({
                "allow_from": chosen, "set_by": str(user.get("username") or ""), "at": time.time(),
            }))
    except Exception as e:
        log.warning(f"[calls] could not save drop-in for {username}: {type(e).__name__}: {e}")
        raise HTTPException(status_code=503, detail="Could not save. Try again.") from e
    return {"username": username, "allow_from": chosen}


@router.put("/api/communication/calls/drop-in/{username}/devices/{device_key}")
async def put_device_drop_in(
    username: str, device_key: str, request: Request, user: dict[str, Any] = Depends(_caller)
) -> dict[str, Any]:
    """Switch one of ``username``'s devices answering drop-ins by itself on or off."""
    try:
        body = await request.json()
    except ValueError:
        body = None
    on = body.get("drop_in") if isinstance(body, dict) else None
    if not isinstance(on, bool):
        raise HTTPException(status_code=422, detail="drop_in must be true or false.")
    family = {u["username"]: u for u in await _family()}
    target = family.get(username)
    if target is None:
        raise HTTPException(status_code=404, detail="No such family member.")
    if not may_set_drop_in(user, target):
        raise HTTPException(status_code=403, detail="You can change this only for yourself or a child.")
    if device_key not in {d["device_key"] for d in await _owned_devices(username)}:
        raise HTTPException(status_code=404, detail="No such device.")
    from services.gateway.notices import _redis

    try:
        async with _redis() as conn:
            if on:
                await conn.hdel(DROP_IN_DEVICES_KEY, device_key)
            else:
                await conn.hset(DROP_IN_DEVICES_KEY, device_key, "0")
    except Exception as e:
        log.warning(f"[calls] could not save device drop-in: {type(e).__name__}: {e}")
        raise HTTPException(status_code=503, detail="Could not save. Try again.") from e
    return {"device_key": device_key, "drop_in": on}


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
            "ice_servers": ice_servers_for(peer.username),
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
