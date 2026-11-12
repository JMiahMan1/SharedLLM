"""Who may call the hub.

Two kinds of bearer token are accepted:

* the stack's ``INTERNAL_SECRET`` (service-to-service calls), and
* Home Assistant access tokens, including the long-lived tokens every Jarvis
  account already stores. HA signs an access token as an HS256 JWT whose
  ``iss`` is the refresh token's id, keyed with that refresh token's
  ``jwt_key``. The importer copies the refresh tokens (sealed in the store),
  so the household's existing tokens keep working against the hub without
  anyone re-entering a credential.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import jwt

from services.common.secrets import secret_matches
from services.homehub.store import Store

log = logging.getLogger("homehub.auth")


@dataclass
class Principal:
    user_id: str
    name: str
    is_admin: bool
    token_id: str | None = None


class Auth:
    def __init__(self, store: Store, internal_secret: str | None) -> None:
        self.store = store
        self.internal_secret = internal_secret
        self._cache: dict[str, tuple[float, Principal | None]] = {}

    def users(self) -> dict[str, dict[str, Any]]:
        return {u["id"]: u for u in self.store.get_kv("users", []) or []}

    def authenticate(self, token: str | None) -> Principal | None:
        if not token:
            return None
        if secret_matches(token, self.internal_secret):
            return Principal(user_id="internal", name="Jarvis", is_admin=True)
        hit = self._cache.get(token)
        now = time.monotonic()
        if hit and now - hit[0] < 60:
            return hit[1]
        principal = self._verify_jwt(token)
        self._cache[token] = (now, principal)
        if len(self._cache) > 512:
            self._cache.pop(next(iter(self._cache)))
        return principal

    def _verify_jwt(self, token: str) -> Principal | None:
        try:
            unverified = jwt.decode(token, options={"verify_signature": False})
        except jwt.PyJWTError:
            return None
        token_id = unverified.get("iss")
        if not token_id:
            return None
        found = self.store.refresh_token(str(token_id))
        if found is None:
            return None
        meta, key = found
        try:
            jwt.decode(token, key, algorithms=["HS256"], leeway=10)
        except jwt.ExpiredSignatureError:
            return None
        except jwt.PyJWTError:
            log.warning("token for refresh token %s failed its signature check", token_id)
            return None
        user = self.users().get(meta.get("user_id") or "")
        if user is None or not user.get("is_active", True):
            return None
        groups = user.get("group_ids") or []
        return Principal(
            user_id=user["id"],
            name=user.get("name") or "",
            is_admin=bool(user.get("is_owner")) or "system-admin" in groups,
            token_id=str(token_id),
        )


def bearer(header: str | None) -> str | None:
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == "bearer" and value.strip() else None
