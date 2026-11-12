"""Importing an HA config directory (a small synthetic one shaped like the real export)."""
from __future__ import annotations

import json
import sqlite3
import time

import pytest
from cryptography.fernet import Fernet

from services.homehub.core import Hub
from services.homehub.importer import import_config_dir
from services.homehub.runtime import Runtime
from services.homehub.store import Store


def _storage(d, name, data):
    (d / ".storage" / name).write_text(json.dumps({"version": 1, "key": name, "data": data}))


@pytest.fixture
def ha_dir(tmp_path):
    d = tmp_path / "homeassistant"
    (d / ".storage").mkdir(parents=True)
    _storage(d, "core.config", {"latitude": 39.9725, "longitude": -105.3257, "elevation": 360, "time_zone": "Etc/GMT+7",
                                "location_name": "Home", "unit_system_v2": "us_customary"})
    _storage(d, "core.area_registry", {"areas": [{"id": "kitchen", "name": "Kitchen", "aliases": ["cook room", None]}]})
    _storage(d, "core.floor_registry", {"floors": []})
    _storage(d, "core.device_registry", {"devices": [
        {"id": "dev1", "name": "Kitchen Light 1", "area_id": "kitchen", "config_entry_id": "e_wiz",
         "identifiers": [["wiz", "02000000010a"]], "connections": [["mac", "02:00:00:00:01:0a"]],
         "manufacturer": "WiZ", "model": "SHRGB", "future_field": 1}]})
    _storage(d, "core.entity_registry", {"entities": [
        {"id": "reg-uuid-1", "entity_id": "light.demo_light_1", "platform": "wiz", "unique_id": "02000000010a",
         "config_entry_id": "e_wiz", "device_id": "dev1", "has_entity_name": True, "original_name": None,
         "aliases": [], "supported_features": 4, "brand_new_key": True},
        {"id": "reg-uuid-2", "entity_id": "switch.old", "platform": "wiz", "unique_id": "x",
         "config_entry_id": "e_wiz", "disabled_by": "user"}]})
    _storage(d, "core.config_entries", {"entries": [
        {"entry_id": "e_wiz", "domain": "wiz", "title": "WiZ", "data": {"host": "192.0.2.12"}, "options": {},
         "source": "dhcp"},
        {"entry_id": "e_ma", "domain": "music_assistant", "title": "MA",
         "data": {"url": "http://d5369777-music-assistant:8094", "token": "MA-TOKEN"}, "options": {}, "source": "hassio"},
        {"entry_id": "e_atv", "domain": "androidtv_remote", "title": "Office TV", "data": {"host": "1.2.3.4"},
         "options": {}, "source": "zeroconf"},
        {"entry_id": "e_phone", "domain": "mobile_app", "title": "Sam's Phone",
         "data": {"user_id": "u_m", "device_name": "Sam's Phone"}, "options": {}, "source": "registration"},
        {"entry_id": "e_todo", "domain": "local_todo", "title": "Reminders",
         "data": {"storage_key": "reminders", "todo_list_name": "Reminders"}, "options": {}, "source": "user"}]})
    _storage(d, "auth", {"users": [{"id": "u_m", "name": "Sam Rivera", "is_owner": False, "is_active": True,
                                    "group_ids": ["system-admin"]}],
                         "refresh_tokens": [{"id": "rt1", "user_id": "u_m", "jwt_key": "k" * 64,
                                             "token_type": "long_lived_access_token", "token": "secret"}]})
    _storage(d, "person", {"items": [{"id": "sam", "name": "Sam", "user_id": "u_m",
                                      "device_trackers": ["device_tracker.sam_s_phone"]}]})
    _storage(d, "zone", {"items": [{"id": "work", "name": "Work", "latitude": 40.0725, "longitude": -105.4257, "radius": 200}]})
    _storage(d, "core.restore_state", [{"state": {"entity_id": "light.demo_light_1", "state": "on",
                                                  "attributes": {"brightness": 120},
                                                  "last_changed": "2026-11-11T19:00:00+00:00",
                                                  "last_updated": "2026-11-11T19:00:00+00:00"}}])
    _storage(d, "scheduler.storage", {"schedules": []})
    (d / ".storage" / "androidtv_remote_cert.pem").write_text("CERT")
    (d / ".storage" / "androidtv_remote_key.pem").write_text("KEY")
    (d / ".storage" / "local_todo.reminders.ics").write_text("BEGIN:VCALENDAR\nEND:VCALENDAR\n")
    (d / "automations.yaml").write_text("- id: '1'\n  alias: Lights Off\n  trigger:\n  - platform: time\n    at: '23:00:00'\n"
                                        "  action:\n  - service: light.turn_off\n    target:\n      entity_id: !secret x\n")
    (d / "scripts.yaml").write_text("announce:\n  alias: Announce\n  sequence: []\n")
    (d / "scenes.yaml").write_text("")
    db = sqlite3.connect(d / "home-assistant_v2.db")
    db.executescript("""
        CREATE TABLE states_meta (metadata_id INTEGER PRIMARY KEY, entity_id TEXT);
        CREATE TABLE state_attributes (attributes_id INTEGER PRIMARY KEY, shared_attrs TEXT);
        CREATE TABLE states (state_id INTEGER PRIMARY KEY, state TEXT, last_changed_ts REAL, last_updated_ts REAL,
                             metadata_id INTEGER, attributes_id INTEGER);
    """)
    now = time.time()
    db.execute("INSERT INTO states_meta VALUES (1, 'light.demo_light_1')")
    db.execute("INSERT INTO state_attributes VALUES (1, '{\"brightness\": 5}')")
    db.execute("INSERT INTO states VALUES (1, 'on', NULL, ?, 1, 1)", (now - 3600,))
    db.execute("INSERT INTO states VALUES (2, 'off', ?, ?, 1, NULL)", (now - 600, now - 600))
    db.execute("INSERT INTO states VALUES (3, 'on', NULL, ?, 1, NULL)", (now - 86400 * 40,))
    db.commit()
    db.close()
    return d


def test_import_carries_registry_credentials_and_history(tmp_path, ha_dir):
    key = Fernet.generate_key().decode()
    store = Store(str(tmp_path / "hub.db"), key)
    report = import_config_dir(store, str(ha_dir), history_days=7)
    assert report["entities"] == 2 and report["entities_enabled"] == 1
    assert report["devices"] == 1 and report["config_entries"] == 5 and report["refresh_tokens"] == 1
    assert report["history_rows"] == 2 and report["restored_states"] == 1

    entries = {e.entry_id: e for e in store.load_config_entries()}
    assert entries["e_ma"].data["token"] == "MA-TOKEN"
    assert entries["e_ma"].options["url"].startswith("http://127.0.0.1")  # no longer the add-on hostname
    assert entries["e_atv"].data["cert_pem"] == "CERT" and entries["e_atv"].data["key_pem"] == "KEY"
    assert entries["e_phone"].options["jarvis_user"] == "sam"
    assert "BEGIN:VCALENDAR" in entries["e_todo"].data["ics"]
    assert b"MA-TOKEN" not in (tmp_path / "hub.db").read_bytes()

    assert store.get_kv("entity_registry_ids")["reg-uuid-1"] == "light.demo_light_1"
    assert store.load_automations("automation")[0]["alias"] == "Lights Off"
    assert store.load_automations("script")[0]["id"] == "announce"
    assert store.refresh_token("rt1")[1] == "k" * 64

    hub = Hub(time_zone="Europe/Berlin")
    runtime = Runtime(hub, store)
    runtime.load_registry()
    # The imported config is kept, but the zone stays the household's
    # (services/common/household_tz.py): one setting governs Jarvis and the hub.
    assert hub.location["time_zone"] == "Etc/GMT+7"
    assert hub.time_zone == "Europe/Berlin"
    st = hub.states.get("light.demo_light_1")
    assert st.state == "on" and st.last_changed.year == 2026
    assert hub.area_name("light.demo_light_1") == "Kitchen"
    assert hub.areas["kitchen"].aliases == ["cook room"]
    assert hub.entities["switch.old"].disabled_by == "user"


def test_import_is_repeatable(tmp_path, ha_dir):
    store = Store(str(tmp_path / "hub.db"), Fernet.generate_key().decode())
    import_config_dir(store, str(ha_dir))
    import_config_dir(store, str(ha_dir))
    assert store.count("entities") == 2 and store.count("config_entries") == 5 and store.count("automations") == 2


def test_import_refuses_a_directory_that_is_not_ha(tmp_path):
    store = Store(str(tmp_path / "hub.db"), Fernet.generate_key().decode())
    with pytest.raises(FileNotFoundError):
        import_config_dir(store, str(tmp_path))
