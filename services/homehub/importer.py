"""Import a Home Assistant config directory into the hub's store.

Reads only. The source is a copy of HA's ``/config`` (``.storage`` and the
YAML files), never a live HA. Everything keeps HA's ids, so entity ids,
device ids, area ids and the automations that name them carry over unchanged.

What is carried over:
  core.config, areas, floors, devices, entities (disabled ones too, so a
  later enable keeps its id), config entries with their credentials (sealed),
  last known states, HA users and refresh tokens (so existing tokens keep
  working), persons, zones, automations/scripts/scenes, the scheduler's
  schedules, to-do lists, the Android TV remote certificate, and optionally
  recent history from ``home-assistant_v2.db``.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from dataclasses import fields
from typing import Any

import yaml

from services.homehub.core import Area, ConfigEntry, DeviceEntry, EntityEntry, State
from services.homehub.store import Store

log = logging.getLogger("homehub.importer")

# Where the hub reaches services HA used to host as add-ons. Overridable per
# install; written into each entry's options so the stored HA data stays as HA
# had it.
DEFAULT_ENDPOINTS = {
    "matter": os.getenv("MATTER_SERVER_URL", "ws://127.0.0.1:5580/ws"),
    "music_assistant": os.getenv("MUSIC_ASSISTANT_URL", "http://127.0.0.1:8095"),
}


class _Loader(yaml.SafeLoader):
    """HA's YAML tags (``!secret``, ``!include``) load as markers, never followed."""


def _tag(loader: yaml.SafeLoader, node: yaml.Node) -> dict[str, Any]:
    return {"__tag__": node.tag, "value": loader.construct_scalar(node) if isinstance(node, yaml.ScalarNode) else None}


for _t in ("!secret", "!include", "!include_dir_list", "!include_dir_merge_list", "!include_dir_named",
           "!include_dir_merge_named", "!env_var", "!input"):
    _Loader.add_constructor(_t, _tag)


def _read_storage(config_dir: str, name: str) -> Any:
    path = os.path.join(config_dir, ".storage", name)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("data")


def _read_yaml(config_dir: str, name: str) -> Any:
    path = os.path.join(config_dir, name)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return yaml.load(f, Loader=_Loader)


def _only(cls: type, data: dict[str, Any]) -> dict[str, Any]:
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in data.items() if k in names}


def _slug(text: str) -> str:
    import re
    import unicodedata

    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def import_config_dir(store: Store, config_dir: str, *, history_days: int = 0) -> dict[str, Any]:
    """Replace the store's registry with the HA config at ``config_dir``. Returns counts."""
    if not os.path.isdir(os.path.join(config_dir, ".storage")):
        raise FileNotFoundError(f"{config_dir} has no .storage directory; is it an HA config dir?")
    report: dict[str, Any] = {"source": config_dir}

    # -- location / units ---------------------------------------------------
    core = _read_storage(config_dir, "core.config") or {}
    store.set_kv("core_config", {
        "location_name": core.get("location_name", "Home"),
        "latitude": core.get("latitude"), "longitude": core.get("longitude"),
        "elevation": core.get("elevation", 0), "radius": core.get("radius", 100),
        "time_zone": core.get("time_zone", "UTC"),
        "unit_system": core.get("unit_system_v2") or core.get("unit_system") or "us_customary",
        "currency": core.get("currency", "USD"), "country": core.get("country"),
        "language": core.get("language", "en"),
        "external_url": core.get("external_url"), "internal_url": core.get("internal_url"),
    })

    # -- areas / floors -------------------------------------------------------
    for table in ("areas", "devices", "entities"):
        store._exec(f"DELETE FROM {table}")
    areas = (_read_storage(config_dir, "core.area_registry") or {}).get("areas", [])
    for a in areas:
        store.save_area(Area(area_id=a["id"], name=a["name"], floor_id=a.get("floor_id"),
                             aliases=[x for x in a.get("aliases") or [] if x], icon=a.get("icon"),
                             labels=a.get("labels") or []))
    floors = (_read_storage(config_dir, "core.floor_registry") or {}).get("floors", [])
    store.set_kv("floors", {f.get("floor_id") or f.get("id"): f for f in floors})
    report["areas"] = len(areas)

    # -- devices --------------------------------------------------------------
    devices = (_read_storage(config_dir, "core.device_registry") or {}).get("devices", [])
    for d in devices:
        entries = d.get("config_entries") or ([d["config_entry_id"]] if d.get("config_entry_id") else [])
        doc = _only(DeviceEntry, d)
        doc["config_entries"] = entries
        doc["identifiers"] = [list(x) for x in d.get("identifiers") or []]
        doc["connections"] = [list(x) for x in d.get("connections") or []]
        store.save_device(DeviceEntry(**doc))
    report["devices"] = len(devices)

    # -- entities -------------------------------------------------------------
    entities = (_read_storage(config_dir, "core.entity_registry") or {}).get("entities", [])
    registry_ids: dict[str, str] = {}
    for e in entities:
        doc = _only(EntityEntry, e)
        doc["unique_id"] = str(e.get("unique_id") or "")
        doc["aliases"] = [x for x in e.get("aliases") or [] if isinstance(x, str)]
        doc["supported_features"] = int(e.get("supported_features") or 0)
        store.save_entity(EntityEntry(**doc))
        if e.get("id"):
            registry_ids[e["id"]] = e["entity_id"]
    store.set_kv("entity_registry_ids", registry_ids)
    report["entities"] = len(entities)
    report["entities_enabled"] = sum(1 for e in entities if not e.get("disabled_by"))

    # -- config entries ---------------------------------------------------------
    store._exec("DELETE FROM config_entries")
    raw_entries = (_read_storage(config_dir, "core.config_entries") or {}).get("entries", [])
    extra = _integration_files(config_dir)
    persons = (_read_storage(config_dir, "person") or {}).get("items", [])
    users = (_read_storage(config_dir, "auth") or {}).get("users", [])
    for r in raw_entries:
        data = dict(r.get("data") or {})
        options = dict(r.get("options") or {})
        domain = r["domain"]
        if domain in DEFAULT_ENDPOINTS:
            options.setdefault("url", DEFAULT_ENDPOINTS[domain])
        if domain == "androidtv_remote" and extra.get("androidtv_cert"):
            data["cert_pem"], data["key_pem"] = extra["androidtv_cert"], extra["androidtv_key"]
        if domain == "local_todo":
            data["ics"] = extra.get("todo_ics", {}).get(data.get("storage_key", ""), "")
        if domain == "shopping_list":
            data["items"] = extra.get("shopping_list", [])
        if domain == "mobile_app":
            user = _jarvis_user_for_phone(data, persons, users)
            if user:
                options.setdefault("jarvis_user", user)
        store.save_config_entry(ConfigEntry(
            entry_id=r["entry_id"], domain=domain, title=r.get("title") or domain, data=data, options=options,
            source=r.get("source") or "import", disabled_by=r.get("disabled_by"), unique_id=r.get("unique_id"),
        ))
    report["config_entries"] = len(raw_entries)

    # -- users and tokens -----------------------------------------------------
    auth = _read_storage(config_dir, "auth") or {}
    store.set_kv("users", [
        {"id": u["id"], "name": u.get("name"), "is_owner": u.get("is_owner", False),
         "is_active": u.get("is_active", True), "system_generated": u.get("system_generated", False),
         "group_ids": u.get("group_ids") or []}
        for u in auth.get("users", [])
    ])
    store._exec("DELETE FROM refresh_tokens")
    tokens = 0
    for t in auth.get("refresh_tokens", []):
        if not t.get("jwt_key"):
            continue
        store.save_refresh_token(t["id"], {
            "user_id": t.get("user_id"), "client_name": t.get("client_name"), "token_type": t.get("token_type"),
            "created_at": t.get("created_at"), "access_token_expiration": t.get("access_token_expiration"),
        }, t["jwt_key"])
        tokens += 1
    report["users"], report["refresh_tokens"] = len(auth.get("users", [])), tokens

    # -- people, zones ----------------------------------------------------------
    store.set_kv("persons", persons)
    store.set_kv("zones", (_read_storage(config_dir, "zone") or {}).get("items", []))
    report["persons"] = len(persons)

    # -- automations, scripts, scenes, schedules ----------------------------------
    store._exec("DELETE FROM automations")
    autos = _read_yaml(config_dir, "automations.yaml") or []
    for i, a in enumerate(autos if isinstance(autos, list) else []):
        store.save_automation(str(a.get("id") or i), "automation", a)
    scripts = _read_yaml(config_dir, "scripts.yaml") or {}
    for sid, s in (scripts.items() if isinstance(scripts, dict) else []):
        store.save_automation(sid, "script", {"id": sid, **(s or {})})
    scenes = _read_yaml(config_dir, "scenes.yaml") or []
    for i, s in enumerate(scenes if isinstance(scenes, list) else []):
        store.save_automation(str(s.get("id") or i), "scene", s)
    try:
        from services.homehub.automation import load_blueprints

        store.set_kv("blueprints", load_blueprints(config_dir))
    except Exception:
        log.exception("blueprints could not be imported")
    sched = _read_storage(config_dir, "scheduler.storage") or {}
    store.set_kv("scheduler", sched)
    report["automations"], report["scripts"] = len(autos or []), len(scripts or {})

    # -- last states ------------------------------------------------------------
    store._exec("DELETE FROM last_states")
    restored = 0
    for item in _read_storage(config_dir, "core.restore_state") or []:
        st = item.get("state")
        if isinstance(st, dict) and st.get("entity_id"):
            store.save_last_state(State.from_dict(st))
            restored += 1
    report["restored_states"] = restored

    if history_days > 0:
        report["history_rows"] = import_history(store, os.path.join(config_dir, "home-assistant_v2.db"),
                                                history_days)
    store.set_kv("imported_at", time.time())
    store.set_kv("import_report", report)
    log.info("imported %s", report)
    return report


def _read_text(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _integration_files(config_dir: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    sdir = os.path.join(config_dir, ".storage")
    cert, key = os.path.join(sdir, "androidtv_remote_cert.pem"), os.path.join(sdir, "androidtv_remote_key.pem")
    if os.path.exists(cert) and os.path.exists(key):
        out["androidtv_cert"] = _read_text(cert)
        out["androidtv_key"] = _read_text(key)
    out["todo_ics"] = {}
    for name in os.listdir(sdir):
        if name.startswith("local_todo.") and name.endswith(".ics"):
            out["todo_ics"][name[len("local_todo."):-len(".ics")]] = _read_text(os.path.join(sdir, name))
    sl = os.path.join(config_dir, ".shopping_list.json")
    if os.path.exists(sl):
        try:
            out["shopping_list"] = json.loads(_read_text(sl))
        except ValueError:
            out["shopping_list"] = []
    return out


def _jarvis_user_for_phone(data: dict[str, Any], persons: list[dict], users: list[dict]) -> str | None:
    """The Jarvis username for a phone: the HA person that owns its HA user, by first name."""
    uid = data.get("user_id")
    for p in persons:
        if uid and p.get("user_id") == uid and p.get("name"):
            return _slug(p["name"].split()[0])
    for u in users:
        if uid and u.get("id") == uid and u.get("name"):
            return _slug(u["name"].split()[0])
    name = (data.get("device_name") or "").lower()
    for p in persons:
        first = (p.get("name") or "").split()[0].lower() if p.get("name") else ""
        if first and first in name:
            return _slug(first)
    return None


def import_history(store: Store, db_path: str, days: int) -> int:
    """Copy the last ``days`` of state history from HA's recorder (read-only open)."""
    if not os.path.exists(db_path):
        return 0
    uri = f"file:{db_path}?mode=ro&immutable=1"
    src = sqlite3.connect(uri, uri=True)
    try:
        cutoff = time.time() - days * 86400
        cur = src.execute(
            "SELECT m.entity_id, s.state, COALESCE(a.shared_attrs, '{}'), "
            "COALESCE(s.last_changed_ts, s.last_updated_ts), s.last_updated_ts "
            "FROM states s JOIN states_meta m ON s.metadata_id = m.metadata_id "
            "LEFT JOIN state_attributes a ON s.attributes_id = a.attributes_id "
            "WHERE s.last_updated_ts >= ? ORDER BY s.last_updated_ts", (cutoff,))
        total = 0
        while True:
            rows = cur.fetchmany(5000)
            if not rows:
                break
            store.import_history_rows([(r[0], r[1] if r[1] is not None else "unknown", r[2], r[3], r[4])
                                       for r in rows])
            total += len(rows)
        return total
    finally:
        src.close()
