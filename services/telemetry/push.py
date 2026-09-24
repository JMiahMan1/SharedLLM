"""Push notification delivery.

The repo had no push backend at all, so reports only ever appeared in-app. Two
transports are supported behind one interface:

* **Web Push (VAPID)** — self-contained and enabled by default. Subscriptions
  live in Redis, keys are generated once and reused, and delivery goes through
  ``pywebpush`` when it is installed.
* **FCM** — used for the native Android app. It activates only when Firebase
  credentials are present (``GOOGLE_APPLICATION_CREDENTIALS`` /
  ``TELEMETRY_FCM_CREDENTIALS``); without them nothing is attempted and the
  notification stays in the in-app outbox.

Delivery is best-effort: a failed send never blocks report generation, and dead
subscriptions are pruned so one bad device cannot fail a notification forever.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import aiohttp

from services.telemetry.config import INTERNAL_SECRET, REDIS_URL
from services.telemetry.store import internal_headers

log = logging.getLogger("telemetry.push")

SUB_PREFIX = "tel:push:sub:"
VAPID_PRIVATE_KEY = "tel:push:vapid_private"
VAPID_PUBLIC_KEY = "tel:push:vapid_public"
VAPID_EMAIL = "tel:push:vapid_email"
PUSH_CLAIM_LIMIT = 20


def _redis():
    import redis.asyncio as redis

    return redis.from_url(REDIS_URL, decode_responses=True)


# ── subscriptions ───────────────────────────────────────────────────────────

async def save_subscription(user: str, subscription: dict[str, Any]) -> None:
    rc = _redis()
    key = f"{SUB_PREFIX}{user}"
    existing = await rc.smembers(key)
    payload = json.dumps(subscription, sort_keys=True)
    if payload not in existing:
        await rc.sadd(key, payload)
    # Keep a single active subscription per endpoint across users.
    endpoint = subscription.get("endpoint")
    if endpoint:
        await rc.set(f"{SUB_PREFIX}endpoint:{hash(endpoint) & 0xFFFFFFFFFFFF}", user, ex=31536000)


async def remove_subscription(user: str, endpoint: str) -> bool:
    rc = _redis()
    key = f"{SUB_PREFIX}{user}"
    members = await rc.smembers(key)
    for raw in members or []:
        try:
            if json.loads(raw).get("endpoint") == endpoint:
                await rc.srem(key, raw)
                return True
        except Exception:
            continue
    return False


async def list_subscriptions(user: str) -> list[dict[str, Any]]:
    rc = _redis()
    out: list[dict[str, Any]] = []
    for raw in await rc.smembers(f"{SUB_PREFIX}{user}") or []:
        try:
            out.append(json.loads(raw))
        except Exception:
            continue
    return out


# ── VAPID keys ──────────────────────────────────────────────────────────────

async def ensure_vapid_keys() -> dict[str, str] | None:
    """Return the server's VAPID key pair, generating one on first use."""
    rc = _redis()
    private = await rc.get(VAPID_PRIVATE_KEY)
    public = await rc.get(VAPID_PUBLIC_KEY)
    if private and public:
        return {"public_key": public, "private_key": private, "email": await rc.get(VAPID_EMAIL)}

    configured = os.getenv("TELEMETRY_VAPID_PRIVATE_KEY")
    if configured:
        public = os.getenv("TELEMETRY_VAPID_PUBLIC_KEY", "")
        if public:
            await rc.set(VAPID_PRIVATE_KEY, configured)
            await rc.set(VAPID_PUBLIC_KEY, public)
            return {
                "public_key": public,
                "private_key": configured,
                "email": os.getenv("TELEMETRY_VAPID_EMAIL", "mailto:admin@localhost"),
            }

    try:
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import serialization
    except Exception as e:
        log.warning("VAPID key generation unavailable: %s", e)
        return None

    key = ec.generate_private_key(ec.SECP256R1())
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_b64 = _b64url(
        key.public_key().public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint,
        )
    )
    email = os.getenv("TELEMETRY_VAPID_EMAIL", "mailto:admin@localhost")
    await rc.set(VAPID_PRIVATE_KEY, private_pem)
    await rc.set(VAPID_PUBLIC_KEY, public_b64)
    await rc.set(VAPID_EMAIL, email)
    return {"public_key": public_b64, "private_key": private_pem, "email": email}


def _b64url(raw: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


async def vapid_public_key() -> str | None:
    keys = await ensure_vapid_keys()
    return keys["public_key"] if keys else None


# ── transports ──────────────────────────────────────────────────────────────

def _fcm_available() -> bool:
    return bool(
        os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        or os.getenv("TELEMETRY_FCM_CREDENTIALS")
    )


async def _send_webpush(subscription: dict, payload: dict, keys: dict) -> bool:
    try:
        from pywebpush import WebPushException, webpush
    except Exception as e:
        # Missing dependency is a deployment gap, not a delivery failure:
        # surface it loudly and keep the in-app outbox working.
        log.error("pywebpush is not installed — Web Push disabled (%s)", e)
        return False
    try:
        await asyncio.to_thread(
            webpush,
            subscription_info=subscription,
            data=json.dumps(payload),
            vapid_private_key=keys["private_key"],
            vapid_claims={"sub": keys["email"]},
        )
        return True
    except WebPushException as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status in (404, 410):
            return False  # dead subscription — caller prunes it
        log.warning("Web push failed: %s", e)
        return False
    except Exception as e:
        log.warning("Web push error: %s", e)
        return False


async def _send_fcm(user: str, title: str, body: str) -> bool:
    """FCM HTTP v1 send. Inert until Firebase credentials are configured."""
    if not _fcm_available():
        return False
    token_key = f"tel:push:fcm_token:{user}"
    rc = _redis()
    token = await rc.get(token_key)
    if not token:
        return False
    # Project-specific send requires the Firebase Admin SDK; when it is present
    # this is the only path needed for the native app.
    try:
        import firebase_admin
        from firebase_admin import messaging
    except Exception:
        log.info("FCM credentials configured but firebase-admin is not installed")
        return False
    try:
        messaging.send(
            messaging.Message(
                token=token,
                notification=messaging.Notification(title=title, body=body),
            )
        )
        return True
    except Exception as e:
        log.warning("FCM send failed: %s", e)
        return False


async def send_to_user(user: str, title: str, body: str, data: dict | None = None) -> dict:
    """Best-effort push to every registered device. Never raises."""
    result = {"webpush": 0, "fcm": False, "pruned": 0}
    payload = {"title": title, "body": body, "data": data or {}}

    subscriptions = await list_subscriptions(user)
    if subscriptions:
        keys = await ensure_vapid_keys()
        if keys:
            for subscription in subscriptions:
                # Each device is isolated: one bad send must not stop the rest.
                try:
                    ok = await _send_webpush(subscription, payload, keys)
                except Exception as e:
                    log.warning("Web push transport error: %s", e)
                    ok = False
                if ok:
                    result["webpush"] += 1
                else:
                    await remove_subscription(user, subscription.get("endpoint", ""))
                    result["pruned"] += 1

    try:
        result["fcm"] = await _send_fcm(user, title, body)
    except Exception as e:
        log.warning("FCM transport error: %s", e)
        result["fcm"] = False
    return result


__all__ = [
    "INTERNAL_SECRET",
    "internal_headers",
    "aiohttp",
    "ensure_vapid_keys",
    "list_subscriptions",
    "remove_subscription",
    "save_subscription",
    "send_to_user",
    "vapid_public_key",
]
