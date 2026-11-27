"""Jarvis requests carried over Bluetooth from a phone that cannot reach the
server, by a family phone that can.

A phone in offline mode (services/ui/android: OfflineRequests, FamilyMesh)
queues a question to Jarvis, a quick command or a location share. Its body is
sealed (the private chat's AES-256-GCM envelope) and the request signed
(HMAC-SHA256), both with keys derived from the sender's own Jarvis API key:

    sign_key = HMAC-SHA256(api_key, "jarvis-offline-sign-v1")
    seal_key = HMAC-SHA256(api_key, "jarvis-offline-seal-v1")

No new key is issued and nothing new is invented: the API key is the
credential the phone already signs in with, identity keeps it (encrypted) and
gives it back on resolution, so the gateway derives the same two keys here. A
relay phone has neither: it can carry the request, but cannot read it, change
it or make one in someone else's name. A new sign-in (a reset) changes the key,
so a request signed with the old one is refused.

``POST /api/offline-relay/submit`` is called by the relay, signed in as itself.
For each request the gateway checks, in order: the shape, the lifetime allowed
for its kind (a command a few minutes, a question an hour), that it has not
expired and is not from the future, the signature under the *sender's* key,
and that its id has not been used (replay). It then acts **as the sender**
(never the relay) and seals the answer with the sender's seal key; the relay
carries the sealed answer back. The same request arriving through two relays
is acted on once; the second gets the stored answer.

``POST /api/offline-relay/answers`` lets the sender, online again, read the
answers to its own requests directly.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request

log = logging.getLogger(__name__)

router = APIRouter()

SIGN_LABEL = b"jarvis-offline-sign-v1"
SEAL_LABEL = b"jarvis-offline-seal-v1"
VERSION = "jarvis-offline-request-v1"

#: Longest a request of each kind may live (seconds).
MAX_LIFETIME_S = {
    "command": 10 * 60,
    "location": 30 * 60,
    "ask": 60 * 60,
    # Offline edits: kept until any phone reaches the server (a day), applied once.
    "shopping_add": 24 * 3600,
    "shopping_check": 24 * 3600,
}
#: A request stamped this far in the future is a clock fault (or a forgery).
FUTURE_SLACK_S = 5 * 60
MAX_REQUESTS = 50
TEXT_MAX = 1000
#: Answers are kept this long for a second relay or the sender online again.
ANSWER_TTL_S = 24 * 3600

_ID = re.compile(r"[0-9a-f]{32}")
_USER = re.compile(r"[a-z0-9_.\-]{1,64}")


def derive_keys(api_key: str) -> tuple[bytes, bytes]:
    """(sign_key, seal_key) from a Jarvis API key."""
    k = api_key.encode("utf-8")
    return hmac.new(k, SIGN_LABEL, hashlib.sha256).digest(), hmac.new(k, SEAL_LABEL, hashlib.sha256).digest()


def canonical(req: dict) -> bytes:
    """The exact bytes a request's signature covers (MeshRules/OfflineRequestRules build the same)."""
    env = req.get("env") or {}
    parts = [VERSION, str(req.get("id")), str(req.get("from")), str(req.get("kind")),
             str(int(req.get("issued_at") or 0)), str(int(req.get("expires_at") or 0)),
             str(env.get("iv") or ""), str(env.get("ct") or "")]
    return "|".join(parts).encode("utf-8")


def sign(sign_key: bytes, req: dict) -> str:
    return base64.b64encode(hmac.new(sign_key, canonical(req), hashlib.sha256).digest()).decode()


def check_shape(req: Any, now: float) -> str | None:
    """Why a request cannot even be considered, or None. Signature and replay come after."""
    if not isinstance(req, dict):
        return "malformed"
    if not _ID.fullmatch(str(req.get("id") or "")):
        return "malformed"
    if not _USER.fullmatch(str(req.get("from") or "")):
        return "malformed"
    kind = req.get("kind")
    if kind not in MAX_LIFETIME_S:
        return "unknown_kind"
    try:
        issued, expires = int(req.get("issued_at")), int(req.get("expires_at"))
    except (TypeError, ValueError):
        return "malformed"
    env = req.get("env")
    if not isinstance(env, dict) or env.get("kind") != "sealed" or not req.get("sig"):
        return "malformed"
    if expires - issued > MAX_LIFETIME_S[kind] or expires <= issued:
        return "lifetime_too_long"
    if issued > now + FUTURE_SLACK_S:
        return "from_the_future"
    if expires <= now:
        return "expired"
    return None


def verify(sign_key: bytes, req: dict) -> bool:
    try:
        given = base64.b64decode(str(req.get("sig") or ""))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(hmac.new(sign_key, canonical(req), hashlib.sha256).digest(), given)


def seal(key: bytes, payload: dict) -> dict:
    """The private chat's envelope (AES-256-GCM, fresh 12-byte IV)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    iv = os.urandom(12)
    ct = AESGCM(key).encrypt(iv, json.dumps(payload).encode("utf-8"), None)
    return {"kind": "sealed", "v": 1, "iv": base64.b64encode(iv).decode(), "ct": base64.b64encode(ct).decode()}


def open_sealed(key: bytes, env: Any) -> dict | None:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        plain = AESGCM(key).decrypt(base64.b64decode(env["iv"]), base64.b64decode(env["ct"]), None)
        out = json.loads(plain.decode("utf-8"))
    except Exception:
        return None
    return out if isinstance(out, dict) else None


def reply_text(resp: Any) -> str | None:
    """The words of a /api/chat answer (the same fields the app reads: chatReply.ts)."""
    if not isinstance(resp, dict):
        return None
    message = resp.get("message")
    if isinstance(message, dict):
        message = message.get("content")
    choices = resp.get("choices")
    choice = choices[0].get("message", {}).get("content") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
    for text in (resp.get("response"), message, choice):
        if isinstance(text, str) and text.strip():
            return text.strip()
    return None


async def _call_self(method: str, path: str, api_key: str, body: dict) -> tuple[int, Any]:
    """Call this gateway's own route in-process, signed in as ``api_key`` (the sender)."""
    from services.gateway.main import app

    raw = json.dumps(body).encode("utf-8")
    # A query string belongs in the scope's query_string, not the path: "/api/geo/trips?limit=10" as
    # a path matched no route, so offline snapshots never carried trips or today's calendar (404).
    path, _, query = path.partition("?")
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
        "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": query.encode(),
        "root_path": "", "server": ("offline-relay", 80), "client": ("127.0.0.1", 0),
        "headers": [(b"host", b"offline-relay"), (b"content-type", b"application/json"),
                    (b"authorization", f"Bearer {api_key}".encode()),
                    (b"content-length", str(len(raw)).encode())],
    }
    sent = {"done": False}
    status = {"code": 500}
    chunks: list[bytes] = []

    async def receive():
        if sent["done"]:
            return {"type": "http.disconnect"}
        sent["done"] = True
        return {"type": "http.request", "body": raw, "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            status["code"] = message["status"]
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))

    await app(scope, receive, send)
    data = b"".join(chunks)
    try:
        return status["code"], json.loads(data.decode("utf-8")) if data else None
    except ValueError:
        return status["code"], None


async def act_as_sender(api_key: str, sender: str, kind: str, payload: dict) -> dict:
    """Do what the request asks, as its sender. The answer, in words."""
    if kind in ("ask", "command"):
        text = " ".join(str(payload.get("text") or "").split())[:TEXT_MAX]
        if not text:
            return {"ok": False, "text": "The request was empty."}
        code, resp = await _call_self("POST", "/api/chat", api_key, {"query": text, "stream": False})
        answer = reply_text(resp)
        if code == 200 and answer:
            return {"ok": True, "text": answer}
        return {"ok": False, "text": answer or "Jarvis could not answer that just now."}
    if kind == "location":
        try:
            lat, lon = float(payload["lat"]), float(payload["lon"])
        except (KeyError, TypeError, ValueError):
            return {"ok": False, "text": "That was not a place."}
        body = {"latitude": lat, "longitude": lon, "accuracy": float(payload.get("accuracy") or 0) or None,
                "timestamp": payload.get("at"), "device_id": payload.get("device")}
        code, _ = await _call_self("POST", f"/api/users/{sender}/location", api_key, {k: v for k, v in body.items() if v is not None})
        return {"ok": code < 300, "text": "Your location was shared." if code < 300 else "Your location could not be shared."}
    if kind in ("shopping_add", "shopping_check"):
        return await shopping_op(api_key, kind, str(payload.get("item") or ""))
    return {"ok": False, "text": "Unknown request."}


SHOPPING_NOTE = {"title": "Shopping list", "storage": "nextcloud", "path": "Recipes/Shopping list.md"}
_CHECKBOX = re.compile(r"^-\s*\[([ xX])\]\s*(.+?)\s*$")


def normalise_item(text: str) -> str:
    return " ".join(text.strip().lower().split())


def shopping_state(body: str) -> dict[str, bool]:
    """Normalised item -> ticked, as the app reads the list (shoppingList.ts)."""
    out: dict[str, bool] = {}
    for line in body.split("\n"):
        m = _CHECKBOX.match(line)
        if m and m.group(2).strip():
            out[normalise_item(m.group(2))] = m.group(1).lower() == "x"
    return out


async def shopping_op(api_key: str, kind: str, item: str) -> dict:
    """Add or tick one item on the sender's shopping list, by the list's own rules:
    adding an item already there and ticking one already ticked change nothing,
    so an edit applied twice is applied once."""
    item = " ".join(item.split())[:120]
    if not item:
        return {"ok": False, "text": "No item."}
    code, resp = await _call_self("POST", "/api/communication/notes/read", api_key, dict(SHOPPING_NOTE))
    body = str((resp or {}).get("message") or "") if code == 200 and (resp or {}).get("status") == "SUCCESS" else ""
    state = shopping_state(body)
    key = normalise_item(item)
    if kind == "shopping_add":
        if key in state:
            return {"ok": True, "text": f"{item} was already on the list."}
        code, resp = await _call_self("POST", "/api/communication/notes/append", api_key,
                                      {**SHOPPING_NOTE, "content": f"- [ ] {item}"})
        ok = code == 200 and (resp or {}).get("status") == "SUCCESS"
        return {"ok": ok, "text": f"Added {item}." if ok else "The shopping list could not be changed."}
    if state.get(key):
        return {"ok": True, "text": f"{item} was already ticked."}
    if key not in state:
        return {"ok": False, "text": f"{item} is not on the list."}
    code, resp = await _call_self("POST", "/api/communication/notes/check_off", api_key,
                                  {"title": SHOPPING_NOTE["title"], "storage": SHOPPING_NOTE["storage"],
                                   "path": SHOPPING_NOTE["path"], "item": item})
    ok = code == 200 and (resp or {}).get("status") == "SUCCESS"
    return {"ok": ok, "text": f"Ticked {item}." if ok else "The shopping list could not be changed."}


async def _sender_key(sender: str) -> str | None:
    """The sender's own API key, from identity (None: no such person, or no key kept)."""
    from services.gateway.main import resolve_identity

    try:
        creds = await resolve_identity({"rag_user": sender})
    except Exception:
        return None
    if not isinstance(creds, dict) or str(creds.get("user") or "").lower() != sender:
        return None
    key = creds.get("api_key")
    return str(key) if key else None


def _req_key(req_id: str) -> str:
    return f"offline_relay:req:{req_id}"


def _ans_key(req_id: str) -> str:
    return f"offline_relay:ans:{req_id}"


async def handle(conn, req: Any, now: float) -> dict:
    """One relayed request: its outcome, with the sealed answer when there is one."""
    problem = check_shape(req, now)
    if problem:
        return {"status": problem}
    sender = str(req["from"])
    api_key = await _sender_key(sender)
    if not api_key:
        return {"status": "unknown_sender"}
    sign_key, seal_key = derive_keys(api_key)
    if not verify(sign_key, req):
        # Not made with the sender's key: a relay (or anyone) trying to speak for them.
        return {"status": "bad_signature"}
    req_id = str(req["id"])
    ttl = max(60, int(req["expires_at"] - now)) + ANSWER_TTL_S
    if not await conn.set(_req_key(req_id), sender, nx=True, ex=ttl):
        stored = await conn.get(_ans_key(req_id))
        if stored:
            return {"status": "answered", **json.loads(stored)}
        return {"status": "in_progress"}
    payload = open_sealed(seal_key, req["env"])
    if payload is None:
        return {"status": "unreadable"}
    try:
        result = await act_as_sender(api_key, sender, str(req["kind"]), payload)
    except Exception as e:
        log.warning(f"[offline-relay] request failed: {type(e).__name__}")
        result = {"ok": False, "text": "Jarvis could not do that just now."}
    answer = {"to": sender, "answer": seal(seal_key, {"id": req_id, "ok": result["ok"], "text": result["text"],
                                                       "answered_at": int(now)})}
    await conn.set(_ans_key(req_id), json.dumps(answer), ex=ANSWER_TTL_S)
    return {"status": "answered", **answer}


@router.post("/api/offline-relay/submit")
async def submit(request: Request):
    """A family phone that is online hands in the requests it carried."""
    from services.gateway.main import _require_authenticated
    from services.gateway.notices import _redis

    relay = await _require_authenticated(request)
    body = await request.json()
    items = body.get("requests") if isinstance(body, dict) else None
    if not isinstance(items, list):
        raise HTTPException(status_code=422, detail="requests must be a list")
    now = time.time()
    results: dict[str, dict] = {}
    # Edits are applied in the order they were made, whichever phone carried them.
    ordered = sorted(items[:MAX_REQUESTS], key=lambda q: (int(q.get("issued_at") or 0), str(q.get("id") or ""))
                     if isinstance(q, dict) else (0, ""))
    async with _redis() as conn:
        for req in ordered:
            rid = str(req.get("id") or "") if isinstance(req, dict) else ""
            outcome = await handle(conn, req, now)
            if _ID.fullmatch(rid):
                results[rid] = outcome
    log.info(f"[offline-relay] {relay} handed in {len(results)} request(s)")
    return {"results": results}


@router.post("/api/offline-relay/answers")
async def answers(request: Request):
    """The sender, online again: the answers to its own requests."""
    from services.gateway.main import _require_authenticated
    from services.gateway.notices import _redis

    me = str(await _require_authenticated(request)).split(".")[-1].lower()
    body = await request.json()
    ids = [str(i) for i in ((body or {}).get("ids") or []) if isinstance(i, str) and _ID.fullmatch(i)][:200]
    out: dict[str, dict] = {}
    if ids:
        async with _redis() as conn:
            for rid, raw in zip(ids, await conn.mget([_ans_key(i) for i in ids]), strict=True):
                if not raw:
                    continue
                stored = json.loads(raw)
                if stored.get("to") == me:
                    out[rid] = stored
    return {"answers": out}
