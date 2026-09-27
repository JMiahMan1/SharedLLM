"""Signed media tokens (media overhaul plan §7.4).

Short-lived HMAC-SHA256 tokens for media URLs that must carry auth as a query
param (image proxies, audio streams, SSE), where headers cannot be set.
Replaces embedding the user's API key in media URLs (BUG-064).

Token format: ``{exp}.{hmac_hex}`` where the signature is
``HMAC_SHA256(secret, "{user}:{scope}:{exp}")``. The secret is the
``MEDIA_TOKEN_SECRET`` env var when set, falling back to ``INTERNAL_SECRET``.
"""
import hashlib
import hmac
import os
import time

TOKEN_TTL_SECONDS = 3600
# Device-facing URLs (8888 file server, ABS gateway streams) are fetched by
# HA/Cast/Roku for hours with no way to refresh ``?mt=`` — plan §7.4's
# "refresh 5 min before expiry" only applies to the UI, so those signs use
# this longer TTL. Verification is unchanged (it only checks exp).
DEVICE_TOKEN_TTL_SECONDS = 43200
SCOPE = "media"


def _secret() -> bytes:
    return (os.getenv("MEDIA_TOKEN_SECRET") or os.getenv("INTERNAL_SECRET") or "").encode()


def _signature(user: str, scope: str, exp: int) -> str:
    payload = f"{user}:{scope}:{exp}".encode()
    return hmac.new(_secret(), payload, hashlib.sha256).hexdigest()


def sign(user: str, now: float | None = None, ttl: int = TOKEN_TTL_SECONDS) -> tuple[str, int]:
    """Return ``(token, expires_at)`` for ``user``, valid for ``ttl`` seconds."""
    exp = int(time.time() if now is None else now) + ttl
    return f"{exp}.{_signature(user, SCOPE, exp)}", exp


def verify(token: str, user: str, now: float | None = None) -> bool:
    """Check that ``token`` is a valid, unexpired media token for ``user``.

    Fails on malformed tokens, bad signatures (including tokens minted with
    a different scope or for a different user), and expiry.
    """
    try:
        exp_str, signature = token.split(".")
        exp = int(exp_str)
    except (AttributeError, ValueError):
        return False
    if not user or not signature:
        return False
    if exp <= int(time.time() if now is None else now):
        return False
    return hmac.compare_digest(signature, _signature(user, SCOPE, exp))
