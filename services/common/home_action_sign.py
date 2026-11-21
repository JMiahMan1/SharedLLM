"""Signed Home notification buttons: only the person a button was sent to can press it.

The device hub turns a Home Assistant notify call's ``data.actions`` into Jarvis notice buttons
(``home_notification_action``, services/homehub/drivers/mobile_app.py), and the gateway fires
``mobile_app_notification_action`` in the hub when one is tapped. That event can drive an automation
waiting for the answer (a door lock's confirmation), so the gateway must not take an action from just
anyone. The hub signs each button for the one person it sends the notice to (HMAC over the person, the
action, the notice's tag and an expiry, with the services' shared ``INTERNAL_SECRET``); the gateway
accepts a tap only from that person, for that action and tag, before the expiry. Nothing is stored on
either side, so a restart loses nothing.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

#: How long a sent button can be pressed (a notice can sit unread for days).
TTL_S = 7 * 24 * 3600


def _secret() -> bytes:
    from services import config

    secret = str(config.INTERNAL_SECRET or "")
    if not secret:  # config requires it; a blank one must not sign anything
        raise RuntimeError("INTERNAL_SECRET is not set, so Home notification buttons cannot be signed")
    return secret.encode()


def _mac(user: str, action: str, tag: str | None, exp: int) -> str:
    message = "\n".join((user.lower(), action, tag or "", str(exp))).encode()
    return hmac.new(_secret(), message, hashlib.sha256).hexdigest()


def sign(user: str, params: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
    """``params`` (``{"action", "tag"?}``) with ``exp`` and ``sig`` for ``user``."""
    exp = int((time.time() if now is None else now) + TTL_S)
    action = str(params["action"])
    tag = str(params["tag"]) if params.get("tag") is not None else None
    return {**params, "exp": exp, "sig": _mac(user, action, tag, exp)}


def verify(user: str, params: dict[str, Any], *, now: float | None = None) -> str | None:
    """None when ``user`` may press this button; otherwise why not."""
    sig, exp = params.get("sig"), params.get("exp")
    if not isinstance(sig, str) or not isinstance(exp, int) or isinstance(exp, bool):
        return "the button is not signed"
    if exp < (time.time() if now is None else now):
        return "the button has expired"
    action = params.get("action")
    tag = params.get("tag")
    if not isinstance(action, str) or (tag is not None and not isinstance(tag, str)):
        return "the button is malformed"
    if not hmac.compare_digest(sig, _mac(user, action, tag, exp)):
        return "the button was not sent to this account"
    return None
