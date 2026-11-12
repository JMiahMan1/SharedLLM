"""HA's ``mobile_app`` protocol: phones register, report sensors and locations, and receive pushes.

This is the server side of the HA companion app's native API
(https://developers.home-assistant.io/docs/api/native-app-integration),
with HA's payloads, unique ids and responses, so an app that was set up
against Home Assistant keeps working against the hub unchanged:

* Each phone is a ``mobile_app`` config entry (imported from HA, or created at
  runtime by ``POST /api/mobile_app/registrations``). Its ``webhook_id`` is
  the credential for ``POST /api/webhook/<id>``; with ``supports_encryption``
  the payloads are libsodium secretboxes keyed with the entry's ``secret``.
* A sensor's unique id is ``<webhook_id>_<the app's unique_id>``, as HA's
  registry has them, so the imported ``sensor.*`` / ``binary_sensor.*`` keep
  their entity ids and history.
* Notifications go out the way HA sent them: over the app's own WebSocket
  channel when it holds one (``mobile_app/push_notification_channel``), else
  through the push relay named in the registration's ``app_data.push_url``.
  The relay URL comes from the phone, so only relays on an allow-list (a
  setting; HA's public relay by default) are ever called.

Settings (``GET/PUT /hub/mobile_app/settings``, kv ``mobile_app_settings``) are
all editable: the companion app's external and internal URLs, how long a
companion report counts as fresh, the push relay allow-list, and whether
Jarvis's own phone reports fill in for phones whose companion app is quiet
(see ``drivers/mobile_app.py``).
"""
from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import re
import secrets
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from services.homehub.core import (
    ENTITY_INFO_FIELDS,
    UNAVAILABLE,
    UNKNOWN,
    ConfigEntry,
    DeviceEntry,
    EntityEntry,
    Hub,
    HubError,
)

log = logging.getLogger("homehub.mobile_app_webhook")

DOMAIN = "mobile_app"
SETTINGS_KEY = "mobile_app_settings"
LAST_SEEN_KEY = "mobile_app_last_seen"
DELETED_BLOB = "mobile_app_deleted_ids"
DEFAULT_SETTINGS: dict[str, Any] = {
    # Where the companion app reaches the hub. Informational for the hub (the
    # app keeps its own copy); the reverse proxy is configured from the same
    # host name (HOMEHUB_EXTERNAL_HOST, see the Caddyfile).
    "external_url": "",
    "internal_url": "",
    # A phone whose companion app reported within this many minutes owns its
    # sensors; after that, Jarvis's reports fill in.
    "companion_fresh_minutes": 30,
    "jarvis_fallback": True,
    # Create a Jarvis-sourced phone for every Jarvis user that reports a
    # location but has no phone entry, so every family phone has sensors.
    "auto_jarvis_phones": True,
    "jarvis_phone_name": "{name}'s Phone",
    # Push relays a registration may name. HA's own relay is the generic default.
    "push_url_allowlist": ["https://mobile-apps.home-assistant.io/"],
}
PUSH_CONFIRM_TIMEOUT_S = 10.0
PUSH_TIMEOUT_S = 10.0
#: HA stores the last-seen times lazily; once per this many seconds per phone is plenty.
LAST_SEEN_SAVE_EVERY_S = 300.0
SENSOR_TYPES = ("sensor", "binary_sensor")
REGISTRATION_REQUIRED = ("app_id", "app_name", "app_version", "device_name", "manufacturer", "model", "os_name")
SAFE_REGISTRATION_KEYS = ("app_data", "app_id", "app_name", "app_version", "device_name", "manufacturer", "model",
                          "os_version", "supports_encryption")
_ICON = re.compile(r"^[a-z0-9_-]+:[a-z0-9_-]+$")
_SECRET_KEY_SIZE = 32
_BACKGROUND: set[asyncio.Task] = set()


def spawn(coro: Awaitable[Any]) -> asyncio.Task:
    """Run a coroutine in the background, holding a reference so it is not collected mid-way."""
    task = asyncio.ensure_future(coro)
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)
    return task


def slugify(text: str) -> str:
    from services.homehub.drivers.mobile_app import slugify as _slug

    return _slug(text)


class WebhookResult:
    """A status, a JSON body and whether to encrypt it for the phone."""

    def __init__(self, status: int = 200, body: Any = None, *, encrypt_for: ConfigEntry | None = None) -> None:
        self.status = status
        self.body = {} if body is None else body
        self.encrypt_for = encrypt_for


# -- encryption (HA's helpers.py) ------------------------------------------------------

def _legacy_key(secret: str) -> bytes:
    """Before ``no_legacy_encryption``: the secret's first 32 bytes, NUL-padded, used raw."""
    return secret.encode("utf-8")[:_SECRET_KEY_SIZE].ljust(_SECRET_KEY_SIZE, b"\0")


def _box(secret: str, legacy: bool) -> Any:
    from nacl.encoding import HexEncoder, RawEncoder
    from nacl.secret import SecretBox

    if legacy:
        return SecretBox(_legacy_key(secret), encoder=RawEncoder)
    return SecretBox(secret.encode("utf-8"), encoder=HexEncoder)


def decrypt_payload(secret: str, ciphertext: str, *, legacy: bool = False) -> Any:
    """HA's ``decrypt_payload``: base64 secretbox; the key is the hex secret (or the legacy raw form)."""
    from nacl.encoding import Base64Encoder

    return json.loads(_box(secret, legacy).decrypt(ciphertext.encode("utf-8"), encoder=Base64Encoder))


def encrypt_payload(secret: str, data: Any, *, legacy: bool = False) -> str:
    from nacl.encoding import Base64Encoder

    return _box(secret, legacy).encrypt(json.dumps(data).encode("utf-8"), encoder=Base64Encoder).decode("utf-8")


def response_body(result: WebhookResult) -> Any:
    """The JSON a webhook answers with, encrypted when the registration asks for it (``webhook_response``)."""
    entry = result.encrypt_for
    if entry is None or not entry.data.get("supports_encryption") or not entry.data.get("secret"):
        return result.body
    legacy = not entry.data.get("no_legacy_encryption")
    return {"encrypted": True, "encrypted_data": encrypt_payload(entry.data["secret"], result.body, legacy=legacy)}


def _error(code: str, message: str, status: int = 400) -> WebhookResult:
    return WebhookResult(status, {"success": False, "error": {"code": code, "message": message}})


# -- local push channel (HA's push_notification.py) -------------------------------------------

class PushChannel:
    """One app's WebSocket push subscription; unconfirmed pushes fall back to the relay."""

    def __init__(self, webhook_id: str, support_confirm: bool, send: Callable[[dict[str, Any]], Awaitable[None]],
                 on_teardown: Callable[[], None]) -> None:
        self.webhook_id = webhook_id
        self.support_confirm = support_confirm
        self._send = send
        self.on_teardown: Callable[[], None] | None = on_teardown
        self.pending: dict[str, tuple[asyncio.TimerHandle, Callable[[], Awaitable[None]]]] = {}

    async def send(self, data: dict[str, Any], fallback: Callable[[dict[str, Any]], Awaitable[Any]]) -> None:
        if not self.support_confirm:
            await self._send(data)
            return
        confirm_id = uuid.uuid4().hex
        data = {**data, "hass_confirm_id": confirm_id}

        async def failed() -> None:
            if self.pending.pop(confirm_id, None) is None:
                return
            await self.teardown()
            with contextlib.suppress(Exception):
                await fallback(data)

        loop = asyncio.get_running_loop()
        handle = loop.call_later(PUSH_CONFIRM_TIMEOUT_S, lambda: spawn(failed()))
        self.pending[confirm_id] = (handle, failed)
        await self._send(data)

    def confirm(self, confirm_id: str) -> bool:
        item = self.pending.pop(confirm_id, None)
        if item is None:
            return False
        item[0].cancel()
        return True

    async def teardown(self) -> None:
        if self.on_teardown is None:
            return
        self.on_teardown()
        self.on_teardown = None
        waiting = [cb for _h, cb in self.pending.values()]
        for handle, _cb in self.pending.values():
            handle.cancel()
        for cb in waiting:
            with contextlib.suppress(Exception):
                await cb()


async def _post_json(url: str, payload: dict[str, Any]) -> tuple[int, Any]:
    """POST to a push relay. A seam for tests: nothing else in the hub calls a phone's relay."""
    import aiohttp

    from services.common.http import get_client

    async with get_client().post(url, json=payload, timeout=aiohttp.ClientTimeout(total=PUSH_TIMEOUT_S)) as resp:
        try:
            body = await resp.json(content_type=None)
        except ValueError:
            body = {}
        return resp.status, body


# -- the manager -------------------------------------------------------------------------------

class MobileAppManager:
    """Everything ``mobile_app`` that is not a driver: webhooks, registrations, pushes, settings."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime
        self.hub: Hub = runtime.hub
        self.store = runtime.store
        self.channels: dict[str, PushChannel] = {}
        self._lock = asyncio.Lock()
        self._last_seen: dict[str, float] = dict(self.store.get_kv(LAST_SEEN_KEY, {}) or {})
        self._last_saved = 0.0
        self.hub.mobile_app_manager = self  # type: ignore[attr-defined]
        self.apply_urls()

    # -- settings ---------------------------------------------------------------
    def settings(self) -> dict[str, Any]:
        return {**DEFAULT_SETTINGS, **(self.store.get_kv(SETTINGS_KEY, {}) or {})}

    def update_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        current = self.settings()
        for key, value in patch.items():
            if key not in DEFAULT_SETTINGS:
                raise HubError(f"unknown setting: {key}")
            if value is None:
                current[key] = DEFAULT_SETTINGS[key]
                continue
            default = DEFAULT_SETTINGS[key]
            if key in ("external_url", "internal_url"):
                value = str(value).strip().rstrip("/")
                if value and not re.match(r"^https?://[^/\s]+$", value):
                    raise HubError(f"{key} must be a URL like https://host or http://host:port")
            elif key == "push_url_allowlist":
                if not isinstance(value, list) or not all(isinstance(v, str) and v.startswith("https://")
                                                          for v in value):
                    raise HubError("push_url_allowlist must be a list of https:// URL prefixes")
            elif key == "jarvis_phone_name":
                value = str(value)
                if "{name}" not in value:
                    raise HubError("jarvis_phone_name must contain {name}")
            elif isinstance(default, bool):
                value = bool(value)
            elif isinstance(default, int):
                value = max(1, int(value))
            current[key] = value
        self.store.set_kv(SETTINGS_KEY, {k: v for k, v in current.items() if v != DEFAULT_SETTINGS[k]})
        self.apply_urls()
        return self.settings()

    def apply_urls(self) -> None:
        """``/api/config`` reports the URLs the app should use, as HA's core config did.

        Called again by each phone's driver at setup, because loading the
        registry at startup replaces the hub's location with the imported core config.
        """
        s = self.settings()
        loc = self.hub.location if isinstance(self.hub.location, dict) else {}
        for key in ("external_url", "internal_url"):
            if s.get(key):
                loc[key] = s[key]
        self.hub.location = loc

    # -- entries ----------------------------------------------------------------
    def entries(self) -> list[ConfigEntry]:
        return [e for e in self.hub.config_entries.values() if e.domain == DOMAIN]

    def entry_for_webhook(self, webhook_id: str) -> ConfigEntry | None:
        found = None
        for e in self.entries():
            wid = str(e.data.get("webhook_id") or "")
            if wid and hmac.compare_digest(wid, webhook_id):
                found = e
        return found

    def is_deleted(self, webhook_id: str) -> bool:
        try:
            ids = self.store.load_blob(DELETED_BLOB, []) or []
        except RuntimeError:
            return False
        return any(hmac.compare_digest(str(i), webhook_id) for i in ids)

    def touch(self, entry_id: str) -> None:
        now = time.time()
        self._last_seen[entry_id] = now
        if now - self._last_saved > LAST_SEEN_SAVE_EVERY_S:
            self._last_saved = now
            self.store.set_kv(LAST_SEEN_KEY, self._last_seen)

    def last_seen(self, entry_id: str) -> float | None:
        return self._last_seen.get(entry_id)

    def is_fresh(self, entry_id: str) -> bool:
        """The companion app reported recently, so its own sensors win over Jarvis's."""
        seen = self._last_seen.get(entry_id)
        return seen is not None and time.time() - seen < 60 * float(self.settings()["companion_fresh_minutes"])

    def _save_entry(self, entry: ConfigEntry) -> None:
        self.hub.config_entries[entry.entry_id] = entry
        self.store.save_config_entry(entry)

    def device_for(self, entry: ConfigEntry) -> DeviceEntry | None:
        ident = ["mobile_app", str(entry.data.get("device_id") or "")]
        for d in self.hub.devices.values():
            if ident in [list(x) for x in d.identifiers]:
                return d
        return next((d for d in self.hub.devices.values() if entry.entry_id in d.config_entries), None)

    def _ensure_device(self, entry: ConfigEntry, *, from_phone: bool = False) -> DeviceEntry:
        """The phone's device. ``from_phone``: ``entry.data`` is a registration the phone just sent, so its
        maker, model and OS version are the phone's own (confirmed); otherwise it may be what the import
        carried, which only names and identifies the phone."""
        dev = self.device_for(entry)
        d = entry.data
        if dev is None:
            dev = DeviceEntry(id=uuid.uuid4().hex, config_entries=[entry.entry_id],
                              identifiers=[["mobile_app", str(d.get("device_id") or entry.entry_id)]])
            self.hub.devices[dev.id] = dev
        dev.name = d.get("device_name") or dev.name
        if entry.entry_id not in dev.config_entries:
            dev.config_entries.append(entry.entry_id)
        self.store.save_device(dev)
        if from_phone:
            self.hub.confirm_device(dev.id, manufacturer=d.get("manufacturer") or None, model=d.get("model") or None,
                                    sw_version=d.get("os_version") or None,
                                    identifiers=[["mobile_app", str(d.get("device_id") or entry.entry_id)]])
        return dev

    def _free_entity_id(self, domain: str, name: str) -> str:
        base = f"{domain}.{slugify(name)}"
        candidate, n = base, 2
        while candidate in self.hub.entities or self.hub.states.get(candidate) is not None:
            candidate = f"{base}_{n}"
            n += 1
        return candidate

    def find_entity(self, domain: str, unique_id: str) -> EntityEntry | None:
        return next((e for e in self.hub.entities.values()
                     if e.platform == DOMAIN and e.domain == domain and e.unique_id == unique_id), None)

    def _add_entity(self, entry: ConfigEntry, domain: str, unique_id: str, name: str, **meta: Any) -> EntityEntry:
        ent = self.find_entity(domain, unique_id)
        if ent is not None:
            return ent
        dev = self._ensure_device(entry)
        ent = EntityEntry(entity_id=self._free_entity_id(domain, name), platform=DOMAIN, unique_id=unique_id,
                          config_entry_id=entry.entry_id, device_id=dev.id, original_name=name, **meta)
        # Registered by the phone (or by the hub for a Jarvis phone): every field given is the source's own.
        ent.confirmed = [f for f in ENTITY_INFO_FIELDS if getattr(ent, f) not in (None, "", {}, [])]
        self.hub.entities[ent.entity_id] = ent
        self.store.save_entity(ent)
        return ent

    # -- webhooks ---------------------------------------------------------------
    async def handle_webhook(self, webhook_id: str, raw: bytes) -> WebhookResult:
        if self.is_deleted(webhook_id):
            return WebhookResult(410)
        entry = self.entry_for_webhook(webhook_id)
        if entry is None:
            # HA answers an unknown webhook with 200 and does nothing, so a
            # prober learns nothing about which ids exist.
            return WebhookResult(200)
        name = entry.data.get("device_name") or entry.title
        try:
            req = json.loads(raw or b"{}")
        except ValueError:
            log.warning("invalid JSON from %s", name)
            return WebhookResult(400)
        if not isinstance(req, dict) or not isinstance(req.get("type"), str):
            return WebhookResult(200)
        if "encrypted" not in req and entry.data.get("supports_encryption"):
            log.warning("refusing an unencrypted webhook from %s", name)
            return _error("encryption_required", "Encryption required")
        kind = req["type"]
        payload: Any
        if "encrypted" in req:
            payload = self._decrypt(entry, str(req.get("encrypted_data") or ""))
            if payload is None:
                return WebhookResult(200)
        else:
            payload = req.get("data", {})
            if not isinstance(payload, dict | list):
                return WebhookResult(200)
        handler = COMMANDS.get(kind)
        if handler is None:
            log.info("%s sent an unsupported webhook type %s", name, kind)
            return WebhookResult(200)
        self.touch(entry.entry_id)
        return await handler(self, entry, payload)

    def _decrypt(self, entry: ConfigEntry, ciphertext: str) -> Any:
        from nacl.exceptions import CryptoError

        secret = entry.data.get("secret")
        if not secret:
            log.warning("ignoring an encrypted payload: no key is known for %s", entry.title)
            return None
        try:
            payload = decrypt_payload(secret, ciphertext)
            if not entry.data.get("no_legacy_encryption"):
                entry.data = {**entry.data, "no_legacy_encryption": True}
                self._save_entry(entry)
            return payload
        except (CryptoError, ValueError, TypeError):
            pass
        if entry.data.get("no_legacy_encryption"):
            log.warning("ignoring an encrypted payload from %s: it does not decrypt", entry.title)
            return None
        try:
            return decrypt_payload(secret, ciphertext, legacy=True)
        except (CryptoError, ValueError, TypeError):
            log.warning("ignoring an encrypted payload from %s: it does not decrypt", entry.title)
            return None

    # -- registration -------------------------------------------------------------
    async def register(self, user_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """``POST /api/mobile_app/registrations``: a new phone, or an app registering again."""
        missing = [k for k in REGISTRATION_REQUIRED if not isinstance(data.get(k), str) or not data.get(k)]
        if missing:
            raise HubError(f"Message format incorrect: missing {', '.join(missing)}")
        reg = {k: data[k] for k in REGISTRATION_REQUIRED}
        for key in ("device_id", "os_version"):
            if data.get(key) is not None:
                reg[key] = str(data[key])
        app_data = data.get("app_data") or {}
        if not isinstance(app_data, dict):
            raise HubError("Message format incorrect: app_data")
        reg["app_data"] = app_data
        reg["supports_encryption"] = bool(data.get("supports_encryption", False))
        async with self._lock:
            reg["webhook_id"] = secrets.token_hex()
            if reg["supports_encryption"]:
                reg["secret"] = secrets.token_hex(_SECRET_KEY_SIZE)
            reg["user_id"] = user_id
            if not slugify(reg["device_name"]).replace("_", "") or slugify(reg["device_name"]) == "unknown":
                reg["device_name"] = reg.get("device_id") or reg["device_name"]
            existing = None
            if reg.get("device_id"):
                uid = f"{reg['app_id']}-{reg['device_id']}"
                existing = next((e for e in self.entries() if e.unique_id == uid), None)
            if existing is not None:
                await self._reregister(existing, reg)
            else:
                await self._create_entry(reg)
        return {"cloudhook_url": None, "remote_ui_url": None, "secret": reg.get("secret"),
                "webhook_id": reg["webhook_id"]}

    async def _create_entry(self, reg: dict[str, Any]) -> ConfigEntry:
        if not reg.get("device_id"):
            reg["device_id"] = uuid.uuid4().hex
        entry = ConfigEntry(entry_id=uuid.uuid4().hex, domain=DOMAIN, title=reg["device_name"], data=reg,
                            source="registration", unique_id=f"{reg['app_id']}-{reg['device_id']}")
        user = self._jarvis_user_for(reg)
        if user:
            entry.options["jarvis_user"] = user
        self._save_entry(entry)
        tracker = self._add_entity(entry, "device_tracker", reg["device_id"], reg["device_name"])
        if _supports_push(entry):
            self._add_entity(entry, "notify", reg["device_id"], reg["device_name"])
        self._add_tracker_to_person(reg.get("user_id"), tracker.entity_id)
        self.runtime.start_entry(entry)
        log.info("registered a new phone: %s", reg["device_name"])
        return entry

    async def _reregister(self, entry: ConfigEntry, reg: dict[str, Any]) -> None:
        """HA moves the sensors to the new webhook id and reloads the entry."""
        old_wid = str(entry.data.get("webhook_id") or "")
        for ent in list(self.hub.entities.values()):
            if ent.config_entry_id == entry.entry_id and ent.domain in SENSOR_TYPES and old_wid \
                    and ent.unique_id.startswith(old_wid + "_"):
                ent.unique_id = reg["webhook_id"] + ent.unique_id[len(old_wid):]
                self.store.save_entity(ent)
        entry.data = reg
        entry.title = reg["device_name"]
        entry.disabled_by = None
        self._save_entry(entry)
        self._ensure_device(entry, from_phone=True)
        with contextlib.suppress(Exception):
            await self.runtime.stop_entry(entry.entry_id)
        self.runtime.start_entry(entry)

    def _jarvis_user_for(self, reg: dict[str, Any]) -> str | None:
        from services.homehub.importer import _jarvis_user_for_phone

        users = self.store.get_kv("users", []) or []
        persons = self.store.get_kv("persons", []) or []
        return _jarvis_user_for_phone(reg, persons, users)

    def _add_tracker_to_person(self, user_id: str | None, tracker: str) -> None:
        """HA adds a new phone's tracker to the person of the user who registered it."""
        if not user_id:
            return
        persons = self.store.get_kv("persons", []) or []
        changed = False
        for p in persons:
            if p.get("user_id") == user_id and tracker not in (p.get("device_trackers") or []):
                p.setdefault("device_trackers", []).append(tracker)
                changed = True
        if not changed:
            return
        self.store.set_kv("persons", persons)
        mgr = getattr(self.hub, "person_manager", None)
        if mgr is not None:
            for p in mgr.persons:
                if p.get("user_id") == user_id and tracker not in p["device_trackers"]:
                    p["device_trackers"].append(tracker)

    # -- Jarvis-only phones ---------------------------------------------------------
    async def create_jarvis_phone(self, jarvis_user: str, device_name: str | None = None) -> ConfigEntry:
        """A phone fed only by Jarvis's reports (no companion app): tracker, battery, steps, activity."""
        user = str(jarvis_user or "").strip().lower()
        if not re.match(r"^[a-z0-9_.-]+$", user):
            raise HubError("jarvis_user must be a Jarvis username")
        for e in self.entries():
            if e.data.get("app_id") == "jarvis" and e.options.get("jarvis_user") == user:
                raise HubError(f"{user} already has a Jarvis phone")
        name = (device_name or self.settings()["jarvis_phone_name"].replace("{name}", user.title())).strip()
        ha_user = next((u["id"] for u in self.store.get_kv("users", []) or []
                        if slugify(str(u.get("name") or "").split(" ")[0]) == slugify(user)), None)
        reg = {"app_id": "jarvis", "app_name": "Jarvis", "app_version": "", "device_name": name,
               "manufacturer": "", "model": "", "os_name": "", "app_data": {}, "supports_encryption": False,
               "device_id": f"jarvis-{slugify(user)}", "user_id": ha_user}
        entry = ConfigEntry(entry_id=uuid.uuid4().hex, domain=DOMAIN, title=name, data=reg, source="jarvis",
                            unique_id=f"jarvis-{slugify(user)}", options={"jarvis_user": user})
        self._save_entry(entry)
        uid = reg["device_id"]
        self._add_entity(entry, "device_tracker", uid, name)
        self._add_entity(entry, "sensor", f"{uid}_battery_level", f"{name} Battery level", device_class="battery",
                         original_device_class="battery", unit_of_measurement="%", entity_category="diagnostic",
                         capabilities={"state_class": "measurement"})
        self._add_entity(entry, "sensor", f"{uid}_steps_sensor", f"{name} Steps sensor",
                         unit_of_measurement="steps", capabilities={"state_class": "total_increasing"})
        self._add_entity(entry, "sensor", f"{uid}_detected_activity", f"{name} Detected activity")
        self.runtime.start_entry(entry)
        log.info("created a Jarvis-sourced phone for %s", user)
        return entry

    def ensure_phone_sensor(self, entry: ConfigEntry, domain: str, key: str, label: str,
                            **meta: Any) -> EntityEntry:
        """A phone's ``key`` sensor (HA's ``<prefix>_<key>`` unique id), registered if it is missing.

        For the status sensors only the Jarvis app reports (charging, next
        alarm): a companion phone keeps its webhook id as the prefix, as HA
        registered its sensors, and a Jarvis phone its device id, as
        ``create_jarvis_phone`` registers battery and steps.
        """
        d = entry.data
        prefix = str(d.get("device_id") or entry.entry_id) if d.get("app_id") == "jarvis" \
            else str(d.get("webhook_id") or d.get("device_id") or entry.entry_id)
        name = str(d.get("device_name") or entry.title or "")
        return self._add_entity(entry, domain, f"{prefix}_{key}", f"{name} {label}", **meta)

    async def ensure_jarvis_phones(self, jarvis_users: set[str]) -> list[str]:
        """``auto_jarvis_phones``: a Jarvis phone for each reporting user that has no phone at all."""
        if not self.settings()["auto_jarvis_phones"]:
            return []
        from services.homehub.drivers.mobile_app import MobileAppDriver

        covered = {d.jarvis_user() for d in self.hub.drivers.values() if isinstance(d, MobileAppDriver)}
        covered |= {str(e.options.get("jarvis_user") or "") for e in self.entries()}
        made = []
        async with self._lock:
            for user in sorted(jarvis_users - covered):
                with contextlib.suppress(HubError):
                    await self.create_jarvis_phone(user)
                    made.append(user)
        return made

    async def delete_phone(self, entry_id: str) -> None:
        """Remove a phone and its entities; its webhook id then answers 410 so the app registers again."""
        entry = self.hub.config_entries.get(entry_id)
        if entry is None or entry.domain != DOMAIN:
            raise HubError("No such phone")
        await self.runtime.stop_entry(entry_id)
        wid = entry.data.get("webhook_id")
        if wid:
            ids = list(self.store.load_blob(DELETED_BLOB, []) or [])
            ids.append(wid)
            self.store.save_blob(DELETED_BLOB, ids)
            channel = self.channels.pop(wid, None)
            if channel is not None:
                await channel.teardown()
        for ent in [e for e in self.hub.entities.values() if e.config_entry_id == entry_id]:
            self.hub.entities.pop(ent.entity_id, None)
            self.hub.states.remove(ent.entity_id)
            self.store._exec("DELETE FROM entities WHERE entity_id=?", (ent.entity_id,))
        for dev in [d for d in self.hub.devices.values() if entry_id in d.config_entries]:
            dev.config_entries.remove(entry_id)
            if dev.config_entries:
                self.store.save_device(dev)
            else:
                self.hub.devices.pop(dev.id, None)
                self.store._exec("DELETE FROM devices WHERE id=?", (dev.id,))
        self.hub.config_entries.pop(entry_id, None)
        self.store.delete_config_entry(entry_id)
        self._last_seen.pop(entry_id, None)

    async def set_phone_options(self, entry_id: str, patch: dict[str, Any]) -> ConfigEntry:
        entry = self.hub.config_entries.get(entry_id)
        if entry is None or entry.domain != DOMAIN:
            raise HubError("No such phone")
        for key, value in patch.items():
            if key == "jarvis_user":
                if value in (None, ""):
                    entry.options.pop("jarvis_user", None)
                else:
                    if not re.match(r"^[a-z0-9_.-]+$", str(value).strip().lower()):
                        raise HubError("jarvis_user must be a Jarvis username")
                    entry.options["jarvis_user"] = str(value).strip().lower()
            elif key == "jarvis_fallback":
                entry.options["jarvis_fallback"] = bool(value)
            else:
                raise HubError(f"unknown option: {key}")
        self._save_entry(entry)
        return entry

    # -- listing for the admin UI ---------------------------------------------------------
    def phones(self) -> list[dict[str, Any]]:
        from services.homehub.drivers.mobile_app import MobileAppDriver

        out = []
        for entry in sorted(self.entries(), key=lambda e: (e.title or "").lower()):
            d = entry.data
            drv = self.hub.drivers.get(entry.entry_id)
            sensors = []
            for ent in sorted((e for e in self.hub.entities.values() if e.config_entry_id == entry.entry_id),
                              key=lambda e: e.entity_id):
                st = self.hub.states.get(ent.entity_id)
                sensors.append({
                    "entity_id": ent.entity_id, "name": self.hub.friendly_name(ent), "domain": ent.domain,
                    "state": st.state if st else None,
                    "unit": (st.attributes.get("unit_of_measurement") if st else None)
                    or ent.trusted("unit_of_measurement"),
                    "device_class": ent.device_class or ent.trusted("original_device_class"),
                    "disabled": bool(ent.disabled_by), "source": (st.attributes.get("data_source") if st else None)
                    or ("jarvis" if d.get("app_id") == "jarvis" else "companion"),
                    "last_updated": st.last_updated.isoformat() if st else None,
                    # Why a sensor is unavailable, when its source said (an app too old to report it).
                    "reason": st.attributes.get("reason") if st else None,
                })
            seen = self.last_seen(entry.entry_id)
            out.append({
                "entry_id": entry.entry_id, "name": entry.title or d.get("device_name"),
                "kind": "jarvis" if d.get("app_id") == "jarvis" else "companion",
                "app": {k: d.get(k) for k in ("app_name", "app_version", "os_name", "os_version", "manufacturer",
                                              "model")},
                "jarvis_user": drv.jarvis_user() if isinstance(drv, MobileAppDriver) else entry.options.get("jarvis_user"),
                "jarvis_fallback": entry.options.get("jarvis_fallback", True),
                "companion_last_seen": seen, "companion_fresh": self.is_fresh(entry.entry_id),
                "encrypted": bool(d.get("supports_encryption")),
                "push": {"relay": bool((d.get("app_data") or {}).get("push_url")),
                         "local_channel": str(d.get("webhook_id") or "") in self.channels},
                "notify_service": f"notify.mobile_app_{slugify(d.get('device_name') or entry.title)}",
                "state": entry.state, "sensors": sensors,
            })
        return out

    # -- push notifications -----------------------------------------------------------------
    def relay_allowed(self, url: str) -> bool:
        return any(isinstance(p, str) and p and url.startswith(p) for p in self.settings()["push_url_allowlist"])

    async def push(self, entry: ConfigEntry, data: dict[str, Any]) -> str:
        """Send one notification the way HA did: local channel first, then the relay. Returns which."""
        wid = str(entry.data.get("webhook_id") or "")
        channel = self.channels.get(wid) if wid else None
        if channel is not None:
            await channel.send(dict(data), lambda d: self.relay(entry, d))
            return "local"
        if (entry.data.get("app_data") or {}).get("push_url"):
            await self.relay(entry, data)
            return "relay"
        raise HubError(f"{entry.title} is not connected for push notifications")

    async def relay(self, entry: ConfigEntry, data: dict[str, Any]) -> None:
        d = entry.data
        app_data = d.get("app_data") or {}
        url, token = app_data.get("push_url"), app_data.get("push_token")
        if not url or not token:
            raise HubError(f"{entry.title} has no push relay")
        if not self.relay_allowed(str(url)):
            raise HubError(f"{entry.title}'s push relay is not on the allow-list (push_url_allowlist)")
        reg_info = {"app_id": d.get("app_id"), "app_version": d.get("app_version"), "webhook_id": d.get("webhook_id")}
        if d.get("os_version"):
            reg_info["os_version"] = d["os_version"]
        payload = {**{k: v for k, v in data.items() if k != "hass_confirm_id"}, "push_token": token,
                   "registration_info": reg_info}
        try:
            status, body = await _post_json(str(url), payload)
        except Exception as exc:
            raise HubError(f"push relay for {entry.title}: {type(exc).__name__}: {exc}") from exc
        limits = (body or {}).get("rateLimits") if isinstance(body, dict) else None
        if status in (200, 201, 202):
            if limits:
                log.info("push rate limits for %s: %s sent, %s allowed, %s errors", entry.title,
                         limits.get("successful"), limits.get("maximum"), limits.get("errors"))
            return
        message = (body or {}).get("message") if isinstance(body, dict) else None
        raise HubError(f"push relay for {entry.title} answered HTTP {status}: {message or 'no message'}")


def _supports_push(entry: ConfigEntry) -> bool:
    app_data = entry.data.get("app_data") or {}
    return bool(app_data.get("push_token") and app_data.get("push_url")) or "push_websocket_channel" in app_data


_WEBHOOK_PREFIX = re.compile(r"^[0-9a-f]{64}_")


def registry_view(ent: EntityEntry, principal: Any) -> dict[str, Any]:
    """An entity registry row as a WebSocket client may see it.

    A companion app's sensor has its phone's webhook id in its unique id, and
    the webhook id is the phone's credential. Admins see the row as HA stored
    it; anyone else gets the unique id without that prefix, so a family
    member's token cannot be used to read another phone's webhook id.
    """
    row = ent.public()
    if ent.platform == DOMAIN and not getattr(principal, "is_admin", False):
        row["unique_id"] = row["id"] = _WEBHOOK_PREFIX.sub("", str(ent.unique_id))
    return row


def manager_of(hub: Hub) -> MobileAppManager | None:
    return getattr(hub, "mobile_app_manager", None)


# -- the webhook commands ------------------------------------------------------------------

Command = Callable[[MobileAppManager, ConfigEntry, Any], Awaitable[WebhookResult]]
COMMANDS: dict[str, Command] = {}


def command(name: str) -> Callable[[Command], Command]:
    def wrap(fn: Command) -> Command:
        COMMANDS[name] = fn
        return fn

    return wrap


def _ok() -> WebhookResult:
    return WebhookResult(200, {})


@command("call_service")
async def _call_service(mgr: MobileAppManager, entry: ConfigEntry, data: Any) -> WebhookResult:
    if not isinstance(data, dict) or not data.get("domain") or not data.get("service"):
        return _ok()
    service_data = data.get("service_data") if isinstance(data.get("service_data"), dict) else {}
    try:
        await mgr.hub.call_service(str(data["domain"]), str(data["service"]), service_data)
    except Exception as exc:
        log.warning("call_service from %s failed: %s", entry.title, exc)
        return WebhookResult(400, {"message": "Bad Request"})
    return _ok()


@command("fire_event")
async def _fire_event(mgr: MobileAppManager, entry: ConfigEntry, data: Any) -> WebhookResult:
    if isinstance(data, dict) and data.get("event_type"):
        event_data = data.get("event_data") if isinstance(data.get("event_data"), dict) else {}
        mgr.hub.bus.fire(str(data["event_type"]), event_data)
    return _ok()


@command("render_template")
async def _render_template(mgr: MobileAppManager, entry: ConfigEntry, data: Any) -> WebhookResult:
    from services.homehub import templating

    resp: dict[str, Any] = {}
    for key, item in (data.items() if isinstance(data, dict) else []):
        if not isinstance(item, dict) or "template" not in item:
            continue
        try:
            resp[key] = templating.render(mgr.hub, str(item["template"]), item.get("variables") or {})
        except Exception as exc:
            resp[key] = {"error": str(exc)}
    return WebhookResult(200, resp, encrypt_for=entry)


def _tracker_state(hub: Hub, data: dict[str, Any], lat: float | None, lon: float | None, acc: float) -> str:
    from services.homehub.drivers.mobile_app import _zones, zone_for, zone_state_name

    zones = data.get("in_zones")
    if isinstance(zones, list) and zones:
        if "zone.home" in zones:
            return "home"
        table = {z["entity_id"]: z for z in _zones(hub)}
        for zid in zones:
            z = table.get(zid)
            if z is not None and not z.get("passive"):
                return zone_state_name(z)
    name = data.get("location_name")
    if name and not zones:
        if name == "home":
            return "home"
        zone = hub.states.get(f"zone.{name}")
        return zone.name if zone is not None else str(name)
    if lat is not None and lon is not None:
        return zone_for(hub, lat, lon, acc)
    return UNKNOWN


@command("update_location")
async def _update_location(mgr: MobileAppManager, entry: ConfigEntry, data: Any) -> WebhookResult:
    if not isinstance(data, dict):
        return _ok()
    tracker = mgr.find_entity("device_tracker", str(entry.data.get("device_id") or "")) \
        or next((e for e in mgr.hub.entities.values()
                 if e.config_entry_id == entry.entry_id and e.domain == "device_tracker"), None)
    if tracker is None:
        return _ok()
    gps = data.get("gps")
    lat = lon = None
    if isinstance(gps, list | tuple) and len(gps) == 2:
        try:
            lat, lon = float(gps[0]), float(gps[1])
        except (TypeError, ValueError):
            return _ok()
    acc = float(data.get("gps_accuracy") or 0)
    attrs: dict[str, Any] = {"source_type": "gps", "data_source": "companion"}
    if lat is not None:
        attrs.update(latitude=lat, longitude=lon, gps_accuracy=acc)
    if data.get("battery") is not None:
        attrs["battery_level"] = data["battery"]
    for key in ("altitude", "course", "speed", "vertical_accuracy"):
        if data.get(key) is not None:
            attrs[key] = data[key]
    if isinstance(data.get("in_zones"), list):
        attrs["in_zones"] = data["in_zones"]
    mgr.hub.publish(tracker, _tracker_state(mgr.hub, data, lat, lon, acc), attrs)
    return _ok()


@command("update_registration")
async def _update_registration(mgr: MobileAppManager, entry: ConfigEntry, data: Any) -> WebhookResult:
    if not isinstance(data, dict):
        return _ok()
    allowed = {k: data[k] for k in ("app_data", "app_version", "device_name", "manufacturer", "model", "os_version")
               if k in data}
    if not all(k in allowed for k in ("app_version", "device_name", "manufacturer", "model")):
        return _ok()
    if "app_data" in allowed and not isinstance(allowed["app_data"], dict):
        return _ok()
    new = {**entry.data, **allowed}
    entry.data = new
    mgr._save_entry(entry)
    mgr._ensure_device(entry, from_phone=True)
    return WebhookResult(200, {k: new.get(k) for k in SAFE_REGISTRATION_KEYS}, encrypt_for=entry)


@command("enable_encryption")
async def _enable_encryption(mgr: MobileAppManager, entry: ConfigEntry, data: Any) -> WebhookResult:
    if entry.data.get("supports_encryption"):
        return _error("encryption_already_enabled", "Encryption already enabled")
    secret = secrets.token_hex(_SECRET_KEY_SIZE)
    entry.data = {**entry.data, "supports_encryption": True, "secret": secret}
    mgr._save_entry(entry)
    return WebhookResult(200, {"secret": secret})


@command("stream_camera")
async def _stream_camera(mgr: MobileAppManager, entry: ConfigEntry, data: Any) -> WebhookResult:
    eid = str((data or {}).get("camera_entity_id") or "") if isinstance(data, dict) else ""
    st = mgr.hub.states.get(eid)
    if st is None or not eid.startswith("camera."):
        return WebhookResult(400, {"success": False}, encrypt_for=entry)
    # The hub relays MJPEG (or a snapshot stream) but has no HLS, as the README explains.
    return WebhookResult(200, {"mjpeg_path": f"/api/camera_proxy_stream/{eid}", "hls_path": None},
                         encrypt_for=entry)


@command("scan_tag")
async def _scan_tag(mgr: MobileAppManager, entry: ConfigEntry, data: Any) -> WebhookResult:
    if isinstance(data, dict) and data.get("tag_id"):
        dev = mgr.device_for(entry)
        mgr.hub.bus.fire("tag_scanned", {"tag_id": str(data["tag_id"]), "device_id": dev.id if dev else None})
    return _ok()


@command("get_zones")
async def _get_zones(mgr: MobileAppManager, entry: ConfigEntry, data: Any) -> WebhookResult:
    zones = sorted(mgr.hub.states.all("zone"), key=lambda s: s.entity_id)
    return WebhookResult(200, [z.as_dict() for z in zones], encrypt_for=entry)


@command("get_config")
async def _get_config(mgr: MobileAppManager, entry: ConfigEntry, data: Any) -> WebhookResult:
    from services.homehub.api import HA_VERSION, config_payload

    cfg = config_payload(mgr.runtime)
    dev = mgr._ensure_device(entry)
    wid = str(entry.data.get("webhook_id") or "")
    entities = {}
    for ent in mgr.hub.entities.values():
        if ent.config_entry_id != entry.entry_id:
            continue
        uid = ent.unique_id[len(wid) + 1:] if ent.domain in SENSOR_TYPES and ent.unique_id.startswith(wid + "_") \
            else ent.unique_id
        entities[uid] = {"disabled": bool(ent.disabled_by)}
    instance = mgr.store.get_kv("instance_id")
    if not instance:
        instance = uuid.uuid4().hex
        mgr.store.set_kv("instance_id", instance)
    return WebhookResult(200, {
        "latitude": cfg["latitude"], "longitude": cfg["longitude"], "elevation": cfg["elevation"],
        "hass_device_id": dev.id, "instance_id": instance, "unit_system": cfg["unit_system"],
        "location_name": cfg["location_name"], "time_zone": cfg["time_zone"],
        "components": sorted(set(cfg["components"]) | {DOMAIN}),
        "version": HA_VERSION, "theme_color": "#03A9F4", "entities": entities,
    }, encrypt_for=entry)


def _sensor_state(kind: str, value: Any) -> str:
    if value is None or value == UNKNOWN:
        return UNKNOWN
    if value == UNAVAILABLE:
        return UNAVAILABLE
    if kind == "binary_sensor":
        if isinstance(value, str):
            return "on" if value.lower() in ("on", "true", "1") else "off"
        return "on" if value else "off"
    if isinstance(value, bool):
        return str(value)
    return str(value)


#: The companion apps' own definitions of their common sensors (what the app sends in ``register_sensor``),
#: keyed by the sensor's unique id. Used for a sensor the phone has not registered with the hub yet (one the
#: import carried over), so its unit and class come from the app's protocol, never from the import.
COMPANION_SENSORS: dict[str, dict[str, str]] = {
    "battery_level": {"unit_of_measurement": "%", "device_class": "battery", "state_class": "measurement"},
    "battery_temperature": {"unit_of_measurement": "°C", "device_class": "temperature",
                            "state_class": "measurement"},
    "battery_power": {"unit_of_measurement": "W", "device_class": "power", "state_class": "measurement"},
    "steps_sensor": {"unit_of_measurement": "steps", "state_class": "total_increasing"},
    "light_sensor": {"unit_of_measurement": "lx", "device_class": "illuminance", "state_class": "measurement"},
    "pressure_sensor": {"unit_of_measurement": "hPa", "device_class": "pressure", "state_class": "measurement"},
    "wifi_signal_strength": {"unit_of_measurement": "dBm", "device_class": "signal_strength",
                             "state_class": "measurement"},
}


def _companion_definition(mgr: MobileAppManager, ent: EntityEntry) -> dict[str, str]:
    entry = mgr.hub.config_entries.get(ent.config_entry_id or "")
    wid = str((entry.data if entry is not None else {}).get("webhook_id") or "")
    uid = ent.unique_id[len(wid) + 1:] if wid and ent.unique_id.startswith(wid + "_") else ent.unique_id
    return COMPANION_SENSORS.get(uid, {}) if ent.domain == "sensor" else {}


def publish_sensor(mgr: MobileAppManager, ent: EntityEntry, data: dict[str, Any]) -> None:
    attrs = dict(data.get("attributes") or {}) if isinstance(data.get("attributes"), dict) else {}
    # Only what the phone registered counts; a value only the import held is not the phone's word.
    icon = data.get("icon") or ent.trusted("original_icon")
    if icon:
        attrs["icon"] = icon
    known = _companion_definition(mgr, ent)
    state_class = (ent.trusted("capabilities") or {}).get("state_class") or known.get("state_class")
    if state_class and ent.domain == "sensor":
        attrs["state_class"] = state_class
    unit = ent.trusted("unit_of_measurement") or known.get("unit_of_measurement")
    if unit:
        attrs["unit_of_measurement"] = unit
    device_class = ent.trusted("original_device_class") or known.get("device_class")
    if device_class and "device_class" not in attrs:
        attrs["device_class"] = device_class
    attrs["data_source"] = "companion"
    mgr.hub.publish(ent, _sensor_state(ent.domain, data.get("state")), attrs)


@command("register_sensor")
async def _register_sensor(mgr: MobileAppManager, entry: ConfigEntry, data: Any) -> WebhookResult:
    if not isinstance(data, dict) or data.get("type") not in SENSOR_TYPES or not data.get("unique_id") \
            or not data.get("name"):
        return _ok()
    if data.get("state_class") and data["type"] != "sensor":
        return _ok()
    icon = data.get("icon", "mdi:cellphone")
    if icon is not None and not _ICON.match(str(icon)):
        return _ok()
    kind, wid = data["type"], str(entry.data.get("webhook_id") or "")
    uid = f"{wid}_{data['unique_id']}"
    device_name = entry.data.get("device_name") or entry.title
    full_name = f"{device_name} {data['name']}"
    device_class = str(data["device_class"]).lower() if data.get("device_class") else None
    ent = mgr.find_entity(kind, uid)
    if ent is None:
        ent = mgr._add_entity(
            entry, kind, uid, full_name, device_class=None, original_device_class=device_class,
            unit_of_measurement=data.get("unit_of_measurement"), entity_category=data.get("entity_category"),
            original_icon=icon, capabilities={"state_class": data["state_class"]} if data.get("state_class") else None,
            disabled_by="integration" if data.get("disabled") else None)
    else:
        if data.get("disabled") is not None:
            ent.disabled_by = "integration" if data["disabled"] else None
        # The phone describes the sensor again: that is the device's own word, so it is confirmed.
        info: dict[str, Any] = {"original_name": full_name}
        for attr, key in (("original_device_class", "device_class"), ("unit_of_measurement", "unit_of_measurement"),
                          ("entity_category", "entity_category"), ("original_icon", "icon")):
            if key in data:
                value = device_class if key == "device_class" else data[key]
                if value is None:
                    setattr(ent, attr, None)
                else:
                    info[attr] = value
        if data.get("state_class"):
            info["capabilities"] = {"state_class": data["state_class"]}
        mgr.store.save_entity(ent)
        mgr.hub.confirm_entity(ent, **info)
    if not ent.disabled_by:
        publish_sensor(mgr, ent, data)
    return WebhookResult(201, {"success": True}, encrypt_for=entry)


@command("update_sensor_states")
async def _update_sensor_states(mgr: MobileAppManager, entry: ConfigEntry, data: Any) -> WebhookResult:
    items = data if isinstance(data, list) else [data]
    wid = str(entry.data.get("webhook_id") or "")
    resp: dict[str, Any] = {}
    for sensor in items:
        if not isinstance(sensor, dict) or sensor.get("type") not in SENSOR_TYPES or not sensor.get("unique_id"):
            continue
        uid = str(sensor["unique_id"])
        ent = mgr.find_entity(sensor["type"], f"{wid}_{uid}")
        if ent is None or ent.config_entry_id != entry.entry_id:
            resp[uid] = {"success": False, "error": {"code": "not_registered",
                                                     "message": f"{sensor['type']} {uid} is not registered"}}
            continue
        if "state" not in sensor:
            resp[uid] = {"success": False, "error": {"code": "invalid_format",
                                                     "message": "required key not provided @ data['state']"}}
            continue
        resp[uid] = {"success": True}
        if ent.disabled_by:
            resp[uid]["is_disabled"] = True
            continue
        publish_sensor(mgr, ent, sensor)
    return WebhookResult(200, resp, encrypt_for=entry)
