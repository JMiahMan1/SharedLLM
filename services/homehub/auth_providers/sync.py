"""Mirroring an external provider's users into the hub's users and persons. Knows no provider by name.

A hub user that comes from a provider carries ``provider`` (and, for a provider whose usernames are Jarvis's,
``jarvis_user`` = the Jarvis username, lower-case)::

    {"type", "key", "username", "is_admin", "roles", "avatar", "can_login", "disabled", "removed",
     "synced_at", "linked": "matched" | "created"}

and ``entity_access`` (a stored :class:`~services.homehub.auth_providers.base.EntityAccess` snapshot, or None
when the provider sets no limits or the user is an admin). The hub reads both on every request
(:mod:`services.homehub.auth`), so a change at the provider reaches the hub at the next sync.

Rules, idempotent (a second sync with nothing changed changes nothing):

* One hub user per provider user, found by the provider's key. A hub user not yet linked is matched by its
  hub login's username (case-insensitive): it keeps its id, refresh tokens and long-lived tokens. Users an
  admin added on the hub (``local_only``), service users and HA's system users are never matched.
* Name, admin, picture and roles follow the provider. A user the provider disables or no longer lists is
  made inactive (sessions end); nothing is ever deleted, so history keeps its names. One that comes back is
  active again.
* Each user that is a person gets a linked ``person.*``: the person already linked to the hub user, else the
  one whose Jarvis user is the username, else a new one. Its name follows the provider (its entity id never
  changes). Trackers of the user's phones (``mobile_app`` entries registered with their token, or fed by
  Jarvis for their username) are attached unless another person already has them.
* Hub users that match nothing stay the hub's own; the report names them.
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from services.homehub.auth_providers.base import AuthProvider, EntityAccess, ProviderUnavailable, ProviderUser

log = logging.getLogger("homehub.auth_providers.sync")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def login_usernames(ha_auth: Any) -> dict[str, str]:
    """hub user id -> their hub login's username (case-folded)."""
    from services.homehub.ha_auth import normalize_username

    out: dict[str, str] = {}
    for cred in ha_auth._blob()["credentials"]:
        if cred.get("user_id") and cred.get("username"):
            out[str(cred["user_id"])] = normalize_username(cred["username"])
    return out


def is_people_user(u: dict[str, Any]) -> bool:
    """A user that is a person's account: not a service login, not one of HA's system users."""
    return not u.get("service") and not u.get("system_generated")


def _groups(is_admin: bool) -> list[str]:
    return ["system-admin"] if is_admin else ["system-users"]


async def fetch(provider: AuthProvider) -> tuple[list[ProviderUser], dict[str, EntityAccess | None], list[str]]:
    """Everything one sync needs from the provider, before anything is changed.

    Raises ProviderUnavailable when the user list cannot be read. A single user's entity limits that cannot
    be read are reported and that user keeps the limits they had.
    """
    users = await provider.list_users()
    access: dict[str, EntityAccess | None] = {}
    problems: list[str] = []
    for u in users:
        if u.is_admin or u.disabled:
            continue
        try:
            access[u.key] = await provider.entity_access(u)
        except ProviderUnavailable as exc:
            problems.append(f"{u.username}: {exc}")
    return users, access, problems


def _tracker_ids(hub: Any, entry_id: str) -> list[str]:
    return sorted(e.entity_id for e in hub.entities.values()
                  if e.config_entry_id == entry_id and e.entity_id.startswith("device_tracker."))


def phones_of(hub: Any, hub_user_id: str, jarvis_user: str | None) -> list[str]:
    """The trackers of the phones that are this user's."""
    out: list[str] = []
    for entry in hub.config_entries.values():
        if entry.domain != "mobile_app":
            continue
        mine = (entry.data or {}).get("user_id") == hub_user_id
        if jarvis_user and str((entry.options or {}).get("jarvis_user") or "").strip().lower() == jarvis_user:
            mine = True
        if mine:
            out.extend(_tracker_ids(hub, entry.entry_id))
    return list(dict.fromkeys(out))


def apply_users(store: Any, ha_auth: Any, provider: AuthProvider, users: list[ProviderUser],
                access: dict[str, EntityAccess | None], counts: dict[str, Any]) -> list[dict[str, Any]]:
    """Mirror the users into the store's ``users``; returns the stored list."""
    stored = [u for u in (store.get_kv("users", []) or []) if isinstance(u, dict)]
    by_key = {str(u["provider"]["key"]): u for u in stored
              if isinstance(u.get("provider"), dict) and u["provider"].get("type") == provider.type}
    logins = login_usernames(ha_auth)
    now = _now()
    seen: set[str] = set()
    for pu in users:
        seen.add(pu.key)
        user = by_key.get(pu.key)
        if user is None:
            # A hub user (from the HA import) whose login has this username, not linked to anything yet.
            user = next((u for u in stored if is_people_user(u) and not u.get("local_only")
                         and not isinstance(u.get("provider"), dict) and logins.get(str(u.get("id"))) == pu.username),
                        None)
            if user is not None:
                user["provider"] = {"type": provider.type, "key": pu.key, "linked": "matched"}
                counts["users_matched"] += 1
                log.info("linked hub user %s to %s account %s", user.get("id"), provider.type, pu.username)
            else:
                user = {"id": uuid.uuid4().hex, "name": pu.name, "is_owner": False, "is_active": True,
                        "system_generated": False, "group_ids": _groups(pu.is_admin),
                        "provider": {"type": provider.type, "key": pu.key, "linked": "created"}}
                stored.append(user)
                counts["users_added"] += 1
            by_key[pu.key] = user
        link = user["provider"]
        before = (user.get("name"), user.get("is_active", True),
                  {k: v for k, v in link.items() if k != "synced_at"}, user.get("entity_access"),
                  list(user.get("group_ids") or []))
        active = not pu.disabled
        if user.get("is_active", True) and not active:
            counts["users_disabled"] += 1
        elif not user.get("is_active", True) and active:
            counts["users_reactivated"] += 1
        user["name"] = pu.name
        user["is_active"] = active
        if provider.jarvis_usernames:
            # Who this hub user is in Jarvis: places (a zone's owner) and other Jarvis-facing code map a Jarvis
            # username to the hub user by it.
            user["jarvis_user"] = pu.username
        if link.get("linked") == "created":
            user["group_ids"] = _groups(pu.is_admin)
        link.update({"username": pu.username, "is_admin": pu.is_admin, "roles": list(pu.roles), "avatar": pu.avatar,
                     "person": pu.person, "can_login": pu.can_login, "disabled": pu.disabled, "removed": False})
        if pu.is_admin:
            user["entity_access"] = None
        elif pu.key in access and not pu.disabled:
            limits = access[pu.key]
            user["entity_access"] = None if limits is None else limits.snapshot()
        elif "entity_access" not in user:
            user["entity_access"] = EntityAccess().snapshot()  # never read: nothing until the provider answers
        elif user["entity_access"] is None:
            # No longer an admin (or now disabled) and no fresh limits arrived: "no limits" was an admin's,
            # so it becomes nothing at all until the provider answers, never everything.
            user["entity_access"] = EntityAccess().snapshot()
            log.warning("%s account %s is not an admin and its entity limits could not be read: it sees and "
                        "controls nothing until the next sync reads them", provider.type, pu.username)
        after = (user.get("name"), user.get("is_active", True), {k: v for k, v in link.items() if k != "synced_at"},
                 user.get("entity_access"), list(user.get("group_ids") or []))
        if before != after and before[2].get("username"):
            counts["users_updated"] += 1
        link["synced_at"] = now
    for key, user in by_key.items():
        if key in seen or user["provider"].get("removed"):
            continue
        user["provider"]["removed"] = True
        user["provider"]["synced_at"] = now
        if user.get("is_active", True):
            user["is_active"] = False
            counts["users_disabled"] += 1
        log.info("%s account %s is gone: hub user %s is now inactive (kept for its history)",
                 provider.type, user["provider"].get("username"), user.get("id"))
    store.set_kv("users", stored)
    counts["standalone_users"] = sorted(str(u.get("name") or u.get("id")) for u in stored
                                        if is_people_user(u) and not isinstance(u.get("provider"), dict))
    return stored


def apply_persons(runtime: Any, provider: AuthProvider, stored_users: list[dict[str, Any]],
                  counts: dict[str, Any]) -> bool:
    """Linked persons for the provider's people; returns whether any person changed."""
    from services.homehub import registries

    hub = runtime.hub
    persons = registries._stored_persons(runtime)
    changed = False
    for user in stored_users:
        link = user.get("provider")
        if not isinstance(link, dict) or link.get("type") != provider.type or link.get("removed"):
            continue
        if not link.get("person", True):
            continue
        uid, username = str(user["id"]), str(link.get("username") or "")
        jarvis = username if provider.jarvis_usernames else None
        person = next((p for p in persons if p.get("user_id") == uid), None)
        if person is None and jarvis:
            person = next((p for p in persons if str(p.get("jarvis_user") or "").lower() == jarvis
                           and not p.get("user_id")), None)
            if person is not None:
                person["user_id"] = uid
                changed = True
        created = person is None
        if person is None:
            person = {"id": uuid.uuid4().hex, "name": user.get("name") or username, "user_id": uid,
                      "jarvis_user": None, "device_trackers": []}
            persons.append(person)
            counts["persons_created"] += 1
            changed = True
        if person.get("source") != provider.type or person.get("provider_key") != link.get("key"):
            if not created:
                counts["persons_linked"] += 1
            person["source"], person["provider_key"] = provider.type, link.get("key")
            changed = True
        name = user.get("name") or username
        if person.get("name") != name:
            person["name"] = name
            counts["persons_renamed"] += 1
            changed = True
        if jarvis and person.get("jarvis_user") != jarvis and not any(
                p is not person and str(p.get("jarvis_user") or "").lower() == jarvis for p in persons):
            person["jarvis_user"] = jarvis
            changed = True
        if person.get("avatar") != link.get("avatar"):
            person["avatar"] = link.get("avatar")
            changed = True
        taken = {t for p in persons if p is not person for t in (p.get("device_trackers") or [])}
        mine = list(person.get("device_trackers") or [])
        for tracker in phones_of(hub, uid, jarvis):
            if tracker not in mine and tracker not in taken:
                mine.append(tracker)
                counts["trackers_attached"] += 1
                changed = True
        person["device_trackers"] = mine
    if changed:
        runtime.store.set_kv("persons", persons)
        for p in persons:
            if p.get("source") == provider.type:
                registries._registry_entity(runtime, "person", str(p["id"]), str(p.get("name") or p["id"]))
    return changed


def new_counts() -> dict[str, Any]:
    return {"provider_users": 0, "users_added": 0, "users_matched": 0, "users_updated": 0, "users_disabled": 0,
            "users_reactivated": 0, "persons_created": 0, "persons_linked": 0, "persons_renamed": 0,
            "trackers_attached": 0, "standalone_users": []}
