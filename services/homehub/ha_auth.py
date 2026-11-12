"""Home Assistant's login for the companion apps: refresh tokens, passwords, auth codes.

The HA companion app holds a refresh token and trades it at ``/auth/token``
for a 30 minute access token. Without that grant every app session dies the
first time its access token expires, so this module's main job is that grant,
done the way HA did it:

* An access token is an HS256 JWT ``{"iss": <refresh token id>, "iat", "exp"}``
  signed with that refresh token's ``jwt_key``. :mod:`services.homehub.auth`
  already verifies those, so tokens issued here work on every hub route.
* HA kept refresh-token values in plain text in ``.storage/auth``. The hub
  never keeps a value: the importer stores only its SHA-256 (sealed with the
  stack's ``FERNET_KEY``), which is enough to recognise a presented token and
  useless to anyone who reads the database. The values are 512-bit random
  strings, so an unsalted hash cannot be reversed by guessing.
* Passwords are HA's own bcrypt hashes (``auth_provider.homeassistant``,
  base64 of the bcrypt string), sealed the same way, checked with ``bcrypt``.
  A re-login (``/auth/login_flow``) issues an authorization code, which the app
  trades for a new refresh token (``grant_type=authorization_code``).

Everything secret lives in one sealed blob (``ha_auth``):
``{"credentials": [...], "tokens": {token_id: {...}}}``. The refresh tokens'
public rows and ``jwt_key`` stay where :class:`services.homehub.store.Store`
keeps them, so the existing token check is untouched.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlparse

import jwt

from services.homehub.store import Store

log = logging.getLogger("homehub.ha_auth")

BLOB = "ha_auth"
#: HA's lifetimes: a normal refresh token expires 90 days after its last use;
#: its access tokens last 30 minutes; an authorization code 10 minutes.
REFRESH_TOKEN_EXPIRATION_S = 90 * 86400
ACCESS_TOKEN_EXPIRATION_S = 1800
AUTH_CODE_EXPIRATION_S = 600
LOGIN_FLOW_EXPIRATION_S = 600
#: Failed logins for one username before that username is refused for a while.
#: Requests reach the hub through two proxies that rewrite the client address,
#: so limiting by IP would only ever see the proxy; limiting by username and
#: overall is what can actually be enforced.
MAX_FAILURES_PER_USER = 5
MAX_FAILURES_TOTAL = 30
FAILURE_WINDOW_S = 900
#: The companion apps' fixed client ids and their app-scheme callbacks (HA's indieauth whitelist).
APP_REDIRECTS = {
    "https://home-assistant.io/iOS": {"homeassistant://auth-callback"},
    "https://home-assistant.io/android": {
        "homeassistant://auth-callback",
        "https://wear.googleapis.com/3p_auth/io.homeassistant.companion.android",
        "https://wear.googleapis-cn.com/3p_auth/io.homeassistant.companion.android",
    },
}
PROVIDER = {"name": "Home Assistant Local", "id": None, "type": "homeassistant"}
#: A real bcrypt hash of a throwaway password: an unknown username costs the same time as a known one.
_DUMMY_HASH = b"$2b$12$CiuFGszHx9eNHxPuQcwBWez4CwDTOcLTX5CbOpV6gef2nYuXkY7BO"


class AuthError(Exception):
    """A refused grant: ``code`` is the OAuth error, ``status`` the HTTP status."""

    def __init__(self, code: str, description: str | None = None, status: int = 400) -> None:
        super().__init__(description or code)
        self.code = code
        self.description = description
        self.status = status


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_username(username: str) -> str:
    """HA's rule: surrounding space ignored, case folded."""
    return str(username or "").strip().casefold()


# -- import ---------------------------------------------------------------------------

def _read_storage(config_dir: str, name: str) -> Any:
    path = os.path.join(config_dir, ".storage", name)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("data")


def import_auth(store: Store, config_dir: str) -> dict[str, int]:
    """Copy HA's login data into the sealed ``ha_auth`` blob (refresh-token hashes, password hashes).

    Runs after the importer has written the users and the refresh tokens'
    ``jwt_key`` rows. Replaces the blob, as the rest of the import replaces
    the registry. Returns counts for the import report.
    """
    auth = _read_storage(config_dir, "auth") or {}
    provider = _read_storage(config_dir, "auth_provider.homeassistant") or {}
    passwords = {normalize_username(u.get("username", "")): u.get("password")
                 for u in provider.get("users", []) if u.get("username") and u.get("password")}
    credentials = []
    for c in auth.get("credentials", []):
        if c.get("auth_provider_type") != "homeassistant":
            continue
        username = (c.get("data") or {}).get("username")
        if not username:
            continue
        credentials.append({
            "id": c.get("id") or uuid.uuid4().hex, "user_id": c.get("user_id"), "username": username,
            "password": passwords.get(normalize_username(username)),
        })
    tokens: dict[str, dict[str, Any]] = {}
    for t in auth.get("refresh_tokens", []):
        if not t.get("token") or not t.get("jwt_key"):
            continue
        tokens[t["id"]] = {
            "sha256": token_hash(t["token"]), "client_id": t.get("client_id"),
            "token_type": t.get("token_type") or "normal", "user_id": t.get("user_id"),
            "expire_at": t.get("expire_at"), "credential_id": t.get("credential_id"),
            "access_token_expiration": t.get("access_token_expiration") or ACCESS_TOKEN_EXPIRATION_S,
            "last_used_at": t.get("last_used_at"),
        }
    mobile = _read_storage(config_dir, "mobile_app") or {}
    core_uuid = _read_storage(config_dir, "core.uuid") or {}
    if core_uuid.get("uuid"):
        store.set_kv("instance_id", core_uuid["uuid"])  # the app's get_config reports HA's own instance id
    store.save_blob(BLOB, {"credentials": credentials, "tokens": tokens})
    # A webhook id HA deleted answers 410 Gone, which tells an app to register again.
    store.save_blob("mobile_app_deleted_ids", list(mobile.get("deleted_ids") or []))
    return {"login_credentials": sum(1 for c in credentials if c["password"]), "refresh_token_hashes": len(tokens)}


# -- redirect and client checks (HA's indieauth rules) -------------------------------------

def _parse(url: str) -> Any:
    parts = urlparse(url)
    parts = parts._replace(netloc=parts.netloc.lower())
    if parts.path == "":
        parts = parts._replace(path="/")
    return parts


def verify_client_id(client_id: str | None) -> bool:
    """IndieAuth client ids: an http(s) URL with a host, no fragment, no credentials, no dot segments."""
    if not client_id or len(client_id) > 2048:
        return False
    try:
        parts = _parse(client_id)
        _ = parts.port
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    if parts.fragment or parts.username is not None or parts.password is not None:
        return False
    if any(seg in (".", "..") for seg in parts.path.split("/")):
        return False
    try:
        addr = ip_address(parts.hostname)
    except ValueError:
        return True
    return addr.is_private or addr.is_loopback


def verify_redirect_uri(client_id: str, redirect_uri: str) -> bool:
    """Same scheme and host as the client id, or one of the companion apps' own callbacks."""
    if redirect_uri in APP_REDIRECTS.get(client_id, set()):
        return True
    try:
        c, r = _parse(client_id), _parse(redirect_uri)
    except ValueError:
        return False
    return bool(c.scheme and c.scheme == r.scheme and c.netloc == r.netloc)


def verify_code_verifier(verifier: str, challenge: str) -> bool:
    """PKCE S256 (RFC 7636)."""
    if not verifier or not (43 <= len(verifier) <= 128):
        return False
    digest = hashlib.sha256(verifier.encode("ascii", "ignore")).digest()
    computed = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return hmac.compare_digest(computed, challenge)


# -- the manager ----------------------------------------------------------------------------

@dataclass
class _Flow:
    flow_id: str
    client_id: str
    redirect_uri: str
    code_challenge: str | None
    created: float = field(default_factory=time.monotonic)


@dataclass
class _Code:
    client_id: str
    credential_id: str
    user_id: str
    code_challenge: str | None
    created: float = field(default_factory=time.monotonic)


class HAAuth:
    """HA's token endpoint and login flow over the hub's store."""

    def __init__(self, store: Store, auth: Any = None) -> None:
        self.store = store
        self.auth = auth  # services.homehub.auth.Auth: its cache is cleared when a token is revoked
        self._lock = threading.RLock()
        self._flows: dict[str, _Flow] = {}
        self._codes: dict[str, _Code] = {}
        self._failures: list[tuple[float, str]] = []

    # -- storage ------------------------------------------------------------
    def _blob(self) -> dict[str, Any]:
        blob = self.store.load_blob(BLOB, None) or {}
        blob.setdefault("credentials", [])
        blob.setdefault("tokens", {})
        return blob

    def _save(self, blob: dict[str, Any]) -> None:
        self.store.save_blob(BLOB, blob)

    def users(self) -> dict[str, dict[str, Any]]:
        return {u["id"]: u for u in self.store.get_kv("users", []) or []}

    def _user_allowed(self, user_id: str | None) -> dict[str, Any] | None:
        user = self.users().get(user_id or "")
        if user is None or not user.get("is_active", True) or user.get("system_generated"):
            return None
        return user

    # -- refresh tokens -------------------------------------------------------
    def find_refresh_token(self, value: str) -> tuple[str, dict[str, Any]] | None:
        if not value:
            return None
        wanted = token_hash(value)
        found = None
        # Every stored hash is compared, as HA compared every token, so the
        # time taken does not depend on where (or whether) a match is.
        for tid, meta in self._blob()["tokens"].items():
            if hmac.compare_digest(str(meta.get("sha256") or ""), wanted):
                found = (tid, meta)
        return found

    def _issue_access_token(self, token_id: str, meta: dict[str, Any]) -> tuple[str, int]:
        row = self.store.refresh_token(token_id)
        if row is None:
            raise AuthError("invalid_grant")
        _public, jwt_key = row
        now = int(time.time())
        expire_at = meta.get("expire_at")
        if expire_at and float(expire_at) < now:
            raise AuthError("invalid_grant", "Refresh token has expired")
        if self._user_allowed(meta.get("user_id")) is None:
            raise AuthError("access_denied", "User is not active", status=403)
        lifetime = int(float(meta.get("access_token_expiration") or ACCESS_TOKEN_EXPIRATION_S))
        token = jwt.encode({"iss": token_id, "iat": now, "exp": now + lifetime}, jwt_key, algorithm="HS256")
        return token, lifetime

    def _log_usage(self, token_id: str) -> None:
        """HA moves a normal token's expiry 90 days past its last use."""
        with self._lock:
            blob = self._blob()
            meta = blob["tokens"].get(token_id)
            if meta is None:
                return
            now = time.time()
            meta["last_used_at"] = now
            if meta.get("expire_at"):
                meta["expire_at"] = now + REFRESH_TOKEN_EXPIRATION_S
            self._save(blob)

    def refresh_grant(self, refresh_token: str | None, client_id: str | None) -> dict[str, Any]:
        """``grant_type=refresh_token``: a new access token for a stored refresh token."""
        if client_id is not None and not verify_client_id(client_id):
            raise AuthError("invalid_request", "Invalid client id")
        if not refresh_token:
            raise AuthError("invalid_request")
        found = self.find_refresh_token(refresh_token)
        if found is None:
            raise AuthError("invalid_grant")
        token_id, meta = found
        if meta.get("token_type") == "system":
            raise AuthError("invalid_grant")
        if (meta.get("client_id") or None) != (client_id or None):
            raise AuthError("invalid_request")
        access, lifetime = self._issue_access_token(token_id, meta)
        self._log_usage(token_id)
        return {"access_token": access, "token_type": "Bearer", "expires_in": lifetime}

    def create_refresh_token(self, user_id: str, client_id: str, credential_id: str | None) -> tuple[str, str]:
        """A new normal refresh token, as HA makes one at the end of a login. Returns (id, value)."""
        token_id = uuid.uuid4().hex
        value = secrets.token_hex(64)
        jwt_key = secrets.token_hex(64)
        now = time.time()
        self.store.save_refresh_token(token_id, {
            "user_id": user_id, "client_name": None, "token_type": "normal", "created_at": now,
            "access_token_expiration": ACCESS_TOKEN_EXPIRATION_S,
        }, jwt_key)
        with self._lock:
            blob = self._blob()
            blob["tokens"][token_id] = {
                "sha256": token_hash(value), "client_id": client_id, "token_type": "normal", "user_id": user_id,
                "expire_at": now + REFRESH_TOKEN_EXPIRATION_S, "credential_id": credential_id,
                "access_token_expiration": ACCESS_TOKEN_EXPIRATION_S, "last_used_at": now,
            }
            self._save(blob)
        return token_id, value

    def revoke(self, value: str | None) -> bool:
        """``/auth/revoke``: forget the refresh token and every access token it signed."""
        found = self.find_refresh_token(value or "")
        if found is None:
            return False
        token_id, _meta = found
        with self._lock:
            blob = self._blob()
            blob["tokens"].pop(token_id, None)
            self._save(blob)
            self.store._exec("DELETE FROM refresh_tokens WHERE id=?", (token_id,))
        cache = getattr(self.auth, "_cache", None)
        if isinstance(cache, dict):
            cache.clear()  # the access tokens it signed stop working now, not in a minute
        return True

    # -- login ------------------------------------------------------------------
    def _limited(self, username: str) -> bool:
        cutoff = time.monotonic() - FAILURE_WINDOW_S
        self._failures = [f for f in self._failures if f[0] >= cutoff]
        mine = sum(1 for _, u in self._failures if u == username)
        return mine >= MAX_FAILURES_PER_USER or len(self._failures) >= MAX_FAILURES_TOTAL

    def validate_login(self, username: str, password: str) -> dict[str, Any] | None:
        """The credential for a correct username and password, else None (bcrypt, timing-safe)."""
        import bcrypt

        name = normalize_username(username)
        if self._limited(name):
            log.warning("login refused for a while after repeated failures")
            return None
        found = None
        for cred in self._blob()["credentials"]:
            if normalize_username(cred.get("username", "")) == name and cred.get("password"):
                found = cred
        ok = False
        try:
            if found is None:
                bcrypt.checkpw(b"foo", _DUMMY_HASH)
            else:
                ok = bcrypt.checkpw(str(password or "").encode()[:72], base64.b64decode(found["password"]))
        except (ValueError, TypeError):
            ok = False
        if ok and found is not None and self._user_allowed(found.get("user_id")) is not None:
            return found
        self._failures.append((time.monotonic(), name))
        return None

    def start_flow(self, client_id: str, redirect_uri: str, code_challenge: str | None) -> _Flow:
        self._prune()
        flow = _Flow(uuid.uuid4().hex, client_id, redirect_uri, code_challenge)
        self._flows[flow.flow_id] = flow
        return flow

    def get_flow(self, flow_id: str) -> _Flow | None:
        self._prune()
        return self._flows.get(flow_id)

    def drop_flow(self, flow_id: str) -> bool:
        return self._flows.pop(flow_id, None) is not None

    def create_code(self, flow: _Flow, credential: dict[str, Any]) -> str:
        code = uuid.uuid4().hex
        self._codes[code] = _Code(flow.client_id, credential["id"], credential["user_id"], flow.code_challenge)
        self._flows.pop(flow.flow_id, None)
        return code

    def code_grant(self, client_id: str | None, code: str | None, code_verifier: str | None) -> dict[str, Any]:
        """``grant_type=authorization_code``: a new refresh token and its first access token."""
        if not client_id or not verify_client_id(client_id):
            raise AuthError("invalid_request", "Invalid client id")
        if not code:
            raise AuthError("invalid_request", "Invalid code")
        self._prune()
        entry = self._codes.pop(code, None)
        if entry is None or entry.client_id != client_id:
            raise AuthError("invalid_request", "Invalid code")
        if entry.code_challenge is not None:
            if not code_verifier:
                raise AuthError("invalid_request", "Code verifier required")
            if not verify_code_verifier(code_verifier, entry.code_challenge):
                raise AuthError("invalid_grant", "Invalid code verifier")
        elif code_verifier:
            raise AuthError("invalid_request", "Code verifier provided but no code challenge was present")
        if self._user_allowed(entry.user_id) is None:
            raise AuthError("access_denied", "User is not active", status=403)
        token_id, value = self.create_refresh_token(entry.user_id, client_id, entry.credential_id)
        access, lifetime = self._issue_access_token(token_id, self._blob()["tokens"][token_id])
        return {"access_token": access, "token_type": "Bearer", "refresh_token": value,
                "expires_in": lifetime, "ha_auth_provider": "homeassistant"}

    def _prune(self) -> None:
        now = time.monotonic()
        for fid in [f for f, v in self._flows.items() if now - v.created > LOGIN_FLOW_EXPIRATION_S]:
            self._flows.pop(fid, None)
        for c in [c for c, v in self._codes.items() if now - v.created > AUTH_CODE_EXPIRATION_S]:
            self._codes.pop(c, None)
