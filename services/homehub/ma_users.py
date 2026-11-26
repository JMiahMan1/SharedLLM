"""Giving each household member their own Music Assistant account.

Identity knows who lives here; Music Assistant (MA) is where a person's own
listening lives. A person added to the household should not need an
administrator to open MA's settings and hand-make an account and a provider
filter, so this module derives both from data that already exists:

* the people come from Identity, passed in by the caller that just provisioned
  their Audiobookshelf accounts;
* MA's Audiobookshelf provider instances come from ``config/providers``, and the
  instance that belongs to a person is the one whose ``name`` ends with that
  username in brackets - MA has no field to say it otherwise, and an instance
  cannot be renamed through the API;
* everybody else inherits the household instance, which is the fallback the
  household already agreed on.

Nothing here knows a person's name in advance, and no instance id is written
down: both are read from MA at the time of the call.
"""

from __future__ import annotations

import re
from typing import Any

#: The provider domain whose instances belong to people rather than to the house.
ABS_DOMAIN = "audiobookshelf"
#: MA refuses an account password shorter than this.
MIN_PASSWORD = 8


def _driver(hub: Any) -> Any:
    """The connected Music Assistant driver, or None when the integration is off."""
    for driver in getattr(hub, "drivers", {}).values():
        if getattr(driver, "platform", "") == "music_assistant":
            return driver
    return None


def instance_owner(name: str | None) -> str | None:
    """The member an MA provider instance belongs to, from ``Audiobookshelf (alex)``.

    MA cannot rename an instance, so its display name is the only place an
    instance says whose credentials it carries.
    """
    match = re.search(r"\(([^()]+)\)\s*$", name or "")
    return match.group(1).strip().lower() if match else None


def choose_instances(providers: list[dict[str, Any]], members: list[str]) -> tuple[dict[str, str], str | None]:
    """Map each member to the ABS instance that is theirs, and name the fallback.

    A person with an instance of their own gets it. Everyone else gets the
    household instance: the one belonging to a member who is not in this call's
    list (the household account is not a household member), and failing that the
    first instance MA reports.
    """
    wanted = {name.strip().lower() for name in members}
    by_owner: dict[str, str] = {}
    unclaimed: list[str] = []
    for provider in providers:
        if provider.get("domain") != ABS_DOMAIN:
            continue
        instance_id = provider.get("instance_id")
        if not instance_id:
            continue
        owner = instance_owner(provider.get("name"))
        if owner and owner in wanted:
            by_owner.setdefault(owner, instance_id)
        elif owner not in wanted:
            unclaimed.append(instance_id)
    fallback = unclaimed[0] if unclaimed else None
    return by_owner, fallback


async def reconcile(hub: Any, members: list[str], *, password: str | None = None,
                    display_names: dict[str, str] | None = None) -> dict[str, Any]:
    """Make every member an MA account, pointed at their own Audiobookshelf.

    Returns ``{"created": [...], "updated": [...], "skipped": [{username, reason}]}``.
    A member who already has an account keeps their other providers and only has
    the Audiobookshelf instance for them added, so a person's own music sources
    are never taken away.
    """
    names = [str(name).strip() for name in members if str(name).strip()]
    driver = _driver(hub)
    if driver is None:
        return {"created": [], "updated": [], "skipped": [{"username": name, "reason": "Music Assistant is not set up"} for name in names]}

    providers = await driver.cmd("config/providers") or []
    own, fallback = choose_instances(providers, names)
    users = await driver.cmd("auth/users") or []
    by_name = {str(user.get("username") or "").strip().lower(): user for user in users}

    created: list[str] = []
    updated: list[str] = []
    skipped: list[dict[str, str]] = []
    for name in names:
        key = name.lower()
        user = by_name.get(key)
        mine = own.get(key)
        # A person who already has an Audiobookshelf view keeps it: an existing
        # household view must not be joined by a second one, which is what gave
        # two members both the household instance and somebody else's.
        has_one = any(ABS_DOMAIN in str(item) for item in ((user or {}).get("provider_filter") or []))
        instance = mine or (None if has_one else fallback)
        if user is None:
            if not password or len(password) < MIN_PASSWORD:
                skipped.append({"username": name, "reason": "a Music Assistant password of at least 8 characters is needed"})
                continue
            try:
                await driver.cmd(
                    "auth/user/create",
                    username=name,
                    password=password,
                    role="user",
                    display_name=(display_names or {}).get(name) or name.title(),
                    provider_filter=[instance] if instance else [],
                )
            except Exception as exc:  # reported, never fatal to the others
                skipped.append({"username": name, "reason": f"could not create: {exc}"})
                continue
            created.append(name)
            continue
        if not instance:
            if has_one:
                continue  # their own view already stands
            skipped.append({"username": name, "reason": "Music Assistant has no Audiobookshelf instance to give them"})
            continue
        # Keep every other provider the person already has; only Audiobookshelf is ours to set.
        current = list(user.get("provider_filter") or [])
        keep = [item for item in current if ABS_DOMAIN not in str(item)]
        wanted = [*keep, instance]
        if wanted == current:
            continue
        try:
            await driver.cmd("auth/user/update", user_id=user.get("user_id"), provider_filter=wanted)
        except Exception as exc:  # reported, not fatal to the others
            skipped.append({"username": name, "reason": f"could not update: {exc}"})
            continue
        updated.append(name)
    return {"created": created, "updated": updated, "skipped": skipped}
