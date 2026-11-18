"""The login a service uses to read or command the home hub for one caller.

Home Assistant is retired: homehub answers its API at ``services.config.HOMEHUB_URL``
and accepts the stack's ``INTERNAL_SECRET``. That secret goes to ``HOMEHUB_URL``
and nowhere else, never to ``HA_URL``: Home Assistant stays a supported
integration, and a real one must not be handed the stack's own login. Only people imported from Home Assistant ever had a stored
HA token, and those are being removed, so a caller with no token of their own
reaches the hub with the hub's own login.

The rule every caller follows: the caller's own login when they have one,
else the hub's. Never another person's token. The hub's login can see and
command everything, so a caller on it must filter what they show by the
person's entity permissions (``entity_access.can_view``) and check commands
(``entity_access.verify_entity_access``) before sending them.

The hub's login must never leave the stack: not into a workspace sandbox, an
external agent, a URL or a response body.
"""
from __future__ import annotations

from typing import Any

#: Said when neither the caller nor the stack has a hub to talk to: neither the
#: HomeHub nor the Home Assistant integration is set up (an admin sets either one
#: up in Settings, as ``homehub_url`` or ``ha_url``).
NO_HUB_MESSAGE = (
    "No home hub is set up, so I cannot reach the home's devices. "
    "An admin can connect HomeHub or Home Assistant in Settings."
)


def _own(ctx: Any) -> tuple[str, str]:
    if ctx is None:
        return "", ""
    from services import config

    if not config.home_assistant_enabled():
        # A caller's own login is a Home Assistant token. While that integration is off no HA
        # login is used for calls, the person's own included (it stays stored, unused), so the
        # caller falls through to the hub's own login below.
        return "", ""
    if isinstance(ctx, dict):
        url, token = ctx.get("ha_url"), ctx.get("ha_token")
    else:
        url, token = getattr(ctx, "ha_url", None), getattr(ctx, "ha_token", None)
    return str(url or ""), str(token or "")


def has_own_login(ctx: Any) -> bool:
    """Does this caller carry a hub login of their own (a stored HA token)?"""
    url, token = _own(ctx)
    return bool(url and token)


def hub_login(ctx: Any) -> tuple[str, str] | None:
    """(url, token) for this caller: their own, else the hub's; None when no hub is configured.

    ``ctx`` is a UserContext-like object or an Identity creds dict.
    """
    url, token = _own(ctx)
    if url and token:
        return url, token
    return stack_login()


def stack_login() -> tuple[str, str] | None:
    """(HOMEHUB_URL, INTERNAL_SECRET): the hub's own login, only ever for the hub itself.

    None when the HomeHub integration is off (HOMEHUB_URL blank): there is no
    default address, so nothing is ever sent to a hub nobody set up.
    """
    from services import config

    url = homehub_url()
    secret = getattr(config, "INTERNAL_SECRET", "") or ""
    if url and secret:
        return url, secret
    return None


#: Said to an admin page that manages HomeHub itself while that integration is off.
NO_HOMEHUB_MESSAGE = "HomeHub is not set up. An admin can connect it in Settings (homehub_url)."


def homehub_url() -> str:
    """HOMEHUB_URL as resolved once at startup; "" while the HomeHub integration is off."""
    from services import config

    return str(getattr(config, "HOMEHUB_URL", "") or "").rstrip("/")


def hub_pair(ctx: Any) -> tuple[str, str]:
    """``hub_login`` as a pair, ("", "") when no hub is configured."""
    return hub_login(ctx) or ("", "")
