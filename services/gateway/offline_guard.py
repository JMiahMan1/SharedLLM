"""A parent's screen-time controls reaching a child's phone with no network.

This is not a second control system: the commands are the existing ones
(``POST /api/screen-time/{child}/pause`` to lock, ``{"resume": true}`` to
unlock, ``/bonus`` for extra minutes), carried over Bluetooth instead of the
internet. The child's phone applies them at once, offline, and whichever phone
reaches the server first hands them in here, where they are applied by the
same routes, as the parent, exactly once.

**Why a device key pair here.** Everywhere else the offline path signs with a
key derived from the sender's API key (offline_relay), which the server can
check. A child's phone must check a parent's command itself, offline, and a key
it can check with is a key it could sign with if it were symmetric. So each
phone keeps an EC P-256 key pair in the Android Keystore (the private half never
leaves the device; standard ECDSA with SHA-256, nothing invented) and registers
the public half here while online (``POST /api/offline-keys``). A child's phone
keeps the public keys of its own parents' devices (``GET /api/offline-keys``,
versioned), and refuses anything else. Its own key signs the acknowledgement a
parent's phone checks.

A command says who it is for, which parent device made it, when it was made,
when it expires and a sequence number that only ever grows per parent device.
A child's phone keeps the last sequence it accepted from each parent device, so
an old unlock played again, or commands shuffled, are refused there; here the
id is claimed once (Redis SET NX), so one command is applied once however many
phones hand it in.

**Conflicts:** the most recent parent decision wins. Online, that is the order
the server applies them in. Offline, the child's phone orders by the signed
time, then the sequence; when it is back online it hands the commands in (in
that order) and from then on the server's state, which now includes them, is the
truth.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from services.gateway import offline_relay

log = logging.getLogger(__name__)

router = APIRouter()

COMMAND_VERSION = "jarvis-guard-command-v1"
ACK_VERSION = "jarvis-guard-ack-v1"
ACTIONS = ("lock", "unlock", "bonus")
#: A lock or unlock left undelivered this long is stale: a parent decides again.
MAX_LIFETIME_S = 6 * 3600
FUTURE_SLACK_S = 5 * 60
KEEP_S = 7 * 86400
MAX_DEVICES = 10

_ID = re.compile(r"[0-9a-f]{32}")
_USER = re.compile(r"[a-z0-9_.\-]{1,64}")
_DEVICE = re.compile(r"[A-Za-z0-9_.:\-]{1,128}")


def command_bytes(c: dict) -> bytes:
    parts = [COMMAND_VERSION, c.get("id"), c.get("parent"), c.get("device"), c.get("child"), c.get("action"),
             str(int(c.get("arg") or 0)), str(int(c.get("issued_at") or 0)), str(int(c.get("expires_at") or 0)),
             str(int(c.get("seq") or 0))]
    return "|".join(str(p) for p in parts).encode("utf-8")


def ack_bytes(a: dict) -> bytes:
    parts = [ACK_VERSION, a.get("id"), a.get("child"), a.get("device"), str(int(a.get("applied_at") or 0)),
             a.get("result")]
    return "|".join(str(p) for p in parts).encode("utf-8")


def verify(pub_b64: str, data: bytes, sig_b64: str) -> bool:
    """ECDSA P-256 / SHA-256 (Android's SHA256withECDSA: DER signature, X.509 public key)."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    try:
        key = serialization.load_der_public_key(base64.b64decode(pub_b64))
        if not isinstance(key, ec.EllipticCurvePublicKey) or key.curve.name != "secp256r1":
            return False
        key.verify(base64.b64decode(sig_b64), data, ec.ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def check_command(c: Any, now: float) -> str | None:
    if not isinstance(c, dict) or not _ID.fullmatch(str(c.get("id") or "")):
        return "malformed"
    if not _USER.fullmatch(str(c.get("parent") or "")) or not _USER.fullmatch(str(c.get("child") or "")):
        return "malformed"
    if not _DEVICE.fullmatch(str(c.get("device") or "")) or c.get("action") not in ACTIONS or not c.get("sig"):
        return "malformed"
    try:
        issued, expires, arg, seq = (int(c.get(k) or 0) for k in ("issued_at", "expires_at", "arg", "seq"))
    except (TypeError, ValueError):
        return "malformed"
    if seq < 1 or arg < 0 or arg > 24 * 60:
        return "malformed"
    if expires <= issued or expires - issued > MAX_LIFETIME_S:
        return "lifetime_too_long"
    if issued > now + FUTURE_SLACK_S:
        return "from_the_future"
    return None


async def _keys_of(conn, user: str) -> dict[str, dict]:
    raw = await conn.get(f"offline_keys:{user}")
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


async def _parents_of(child: str) -> list[str]:
    from services.gateway.main import _my_parents

    parents, _ = await _my_parents(child)
    return [str(p).lower() for p in parents or []]


def _apply_call(c: dict) -> tuple[str, dict]:
    """The existing route and body for a command."""
    child = c["child"]
    if c["action"] == "lock":
        minutes = int(c.get("arg") or 0)
        body = {"until": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(int(c["issued_at"]) + minutes * 60))} if minutes else {}
        return f"/api/screen-time/{child}/pause", body
    if c["action"] == "unlock":
        return f"/api/screen-time/{child}/pause", {"resume": True}
    return f"/api/screen-time/{child}/bonus", {"minutes": int(c.get("arg") or 0)}


async def hand_in(conn, c: Any, ack: Any, now: float) -> dict:
    """One command (and the child's acknowledgement, when it has one): checked, applied once, recorded."""
    problem = check_command(c, now)
    if problem:
        return {"status": problem}
    parent, child = c["parent"], c["child"]
    if parent not in await _parents_of(child):
        return {"status": "not_a_parent"}
    key = (await _keys_of(conn, parent)).get(c["device"])
    if not key or not verify(key["pub"], command_bytes(c), c["sig"]):
        return {"status": "bad_signature"}
    record_key = f"offline_guard:cmd:{c['id']}"
    stored = await conn.get(record_key)
    record = json.loads(stored) if stored else None
    if record is None:
        if not await conn.set(f"offline_guard:claim:{c['id']}", "1", nx=True, ex=KEEP_S):
            return {"status": "in_progress"}
        parent_key = await offline_relay._sender_key(parent)
        applied = False
        if parent_key:
            path, body = _apply_call(c)
            code, _ = await offline_relay._call_self("POST", path, parent_key, body)
            applied = code < 300
        record = {"command": c, "applied_at": int(now) if applied else None, "ack": None}
        if not applied:
            await conn.delete(f"offline_guard:claim:{c['id']}")
    if ack and record.get("ack") is None and isinstance(ack, dict) and ack.get("id") == c["id"] and ack.get("child") == child:
        child_key = (await _keys_of(conn, child)).get(str(ack.get("device") or ""))
        if child_key and verify(child_key["pub"], ack_bytes(ack), str(ack.get("sig") or "")):
            record["ack"] = {k: ack.get(k) for k in ("applied_at", "result", "device")}
    await conn.set(record_key, json.dumps(record), ex=KEEP_S)
    return {"status": "applied" if record.get("applied_at") else "not_applied", "ack": record.get("ack")}


@router.post("/api/offline-keys")
async def register_key(request: Request):
    """A phone registers the public half of its offline key (EC P-256, X.509 DER, base64)."""
    from services.gateway.main import _require_authenticated
    from services.gateway.notices import _redis

    me = str(await _require_authenticated(request)).split(".")[-1].lower()
    body = await request.json()
    device = str((body or {}).get("device_id") or "")
    pub = str((body or {}).get("public_key") or "")
    if not _DEVICE.fullmatch(device):
        raise HTTPException(status_code=422, detail="device_id is required")
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        key = serialization.load_der_public_key(base64.b64decode(pub))
        if not isinstance(key, ec.EllipticCurvePublicKey) or key.curve.name != "secp256r1":
            raise ValueError("not P-256")
    except Exception as e:
        raise HTTPException(status_code=422, detail="public_key must be an EC P-256 public key") from e
    async with _redis() as conn:
        keys = await _keys_of(conn, me)
        keys[device] = {"pub": pub, "registered_at": int(time.time())}
        if len(keys) > MAX_DEVICES:
            for old in sorted(keys, key=lambda d: keys[d].get("registered_at", 0))[: len(keys) - MAX_DEVICES]:
                keys.pop(old, None)
        await conn.set(f"offline_keys:{me}", json.dumps(keys))
    return {"status": "ok"}


@router.get("/api/offline-keys")
async def household_keys(request: Request):
    """The keys this phone trusts offline: its parents' devices (commands) and, for a
    parent, their children's devices (acknowledgements). Versioned by content."""
    from services.gateway.main import _identity_as_caller, _require_authenticated
    from services.gateway.notices import _redis

    me = str(await _require_authenticated(request)).split(".")[-1].lower()
    parents = await _parents_of(me)
    status, body = await _identity_as_caller(request, "GET", "/api/users/family")
    roster = body if status == 200 and isinstance(body, list) else []
    children = [str(m.get("username") or "").lower() for m in roster
                if isinstance(m, dict) and me in await _parents_of(str(m.get("username") or "").lower())]
    out: dict[str, list[dict]] = {"parents": [], "children": []}
    async with _redis() as conn:
        for group, users in (("parents", parents), ("children", children)):
            for u in sorted(set(users)):
                for device, key in sorted((await _keys_of(conn, u)).items()):
                    out[group].append({"user": u, "device": device, "pub": key["pub"]})
    version = hashlib.sha256(json.dumps(out, sort_keys=True).encode()).hexdigest()[:16]
    return {**out, "version": version}


@router.post("/api/offline-guard/report")
async def report(request: Request):
    """Any family phone hands in parent commands it carried (with the child's acknowledgements)."""
    from services.gateway.main import _require_authenticated
    from services.gateway.notices import _redis

    await _require_authenticated(request)
    body = await request.json()
    items = body.get("commands") if isinstance(body, dict) else None
    if not isinstance(items, list):
        raise HTTPException(status_code=422, detail="commands must be a list")
    now = time.time()
    ordered = sorted((i for i in items[:50] if isinstance(i, dict) and isinstance(i.get("command"), dict)),
                     key=lambda i: (int(i["command"].get("issued_at") or 0), int(i["command"].get("seq") or 0)))
    results: dict[str, dict] = {}
    async with _redis() as conn:
        for item in ordered:
            cid = str(item["command"].get("id") or "")
            results[cid] = await hand_in(conn, item["command"], item.get("ack"), now)
    return {"results": results}


@router.post("/api/offline-guard/status")
async def delivery_status(request: Request):
    """A parent: what happened to the commands they sent offline."""
    from services.gateway.main import _require_authenticated
    from services.gateway.notices import _redis

    me = str(await _require_authenticated(request)).split(".")[-1].lower()
    body = await request.json()
    ids = [i for i in ((body or {}).get("ids") or []) if isinstance(i, str) and _ID.fullmatch(i)][:200]
    out: dict[str, dict] = {}
    async with _redis() as conn:
        for cid in ids:
            raw = await conn.get(f"offline_guard:cmd:{cid}")
            if not raw:
                continue
            rec = json.loads(raw)
            if rec.get("command", {}).get("parent") == me:
                out[cid] = {"applied_at": rec.get("applied_at"), "ack": rec.get("ack")}
    return {"commands": out}
