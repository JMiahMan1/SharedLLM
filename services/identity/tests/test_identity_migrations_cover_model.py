"""Every column an identity model gains must reach existing databases.

``SQLModel.metadata.create_all`` creates missing tables and never alters an
existing one, so a field added to a model is invisible to an install with data:
every query on that table fails with "no such column" until a migration adds it.
This test pins the columns each table had before ``main.SCHEMA_COLUMNS`` existed;
a column that is in neither that snapshot nor SCHEMA_COLUMNS fails here, naming
the entry to add.

A brand new table needs nothing: ``create_all`` creates it whole. Add it to
BASELINE once it has shipped, so later columns on it are caught too.
"""
from sqlalchemy import create_engine, inspect, text
from sqlmodel import SQLModel

import services.identity.models  # noqa: F401  (registers the tables)
from services.identity import main as identity_main
from services.identity.main import SCHEMA_COLUMNS

BASELINE: dict[str, set[str]] = {
    "apikey": {"created_at", "id", "key_value", "label", "user_id"},
    "assistantconversation": {"at", "device_key", "features", "id", "turns", "username"},
    "auditevent": {"action", "actor", "actor_kind", "at", "changes", "client", "id", "note", "source", "target", "user_agent"},
    "capabilitygap": {"capability", "first_seen_at", "id", "kind", "last_seen_at", "occurrences", "request", "resolved", "resolved_at", "username"},
    "capabilityinventory": {"capabilities", "device_key", "esphome_version", "id", "observed_at"},
    "chorecheck": {"chore_id", "created_at", "day", "decided_at", "decided_by", "id", "status", "title", "username"},
    "device": {"app_build", "app_version", "capabilities", "device_key", "entity_id", "esphome_version", "first_seen_at", "hardware", "id", "kind", "label", "last_ip_address", "last_seen_at", "manufacturer", "model", "os_build", "os_version", "owner_username", "registered_by", "revoked"},
    "deviceassignment": {"device_id", "id", "user_id"},
    "deviceevent": {"at", "device_key", "event", "extra", "id", "username"},
    "dnsrecord": {"created_at", "domain_name", "id", "is_active", "record_type", "ttl", "updated_at", "values"},
    "entityprotection": {"entity_id", "granted_at", "granted_by", "note", "permitted_usernames"},
    "featureusage": {"feature", "first_used_at", "kind", "last_used_at", "uses", "username"},
    "globalsetting": {"description", "id", "key", "value"},
    "guardiannote": {"author", "child", "created_at", "id", "shared", "text"},
    "guardofflinecode": {"hash", "iterations", "salt", "updated_at", "username"},
    "guardrequest": {"created_at", "decided_at", "decided_by", "id", "kind", "label", "note", "status", "target", "until", "username"},
    "guardunlockrequest": {"created_at", "decided_at", "decided_by", "delivered_at", "device_kind", "device_key", "expires_at", "id", "lock_reason", "minutes_asked", "minutes_granted", "note", "status", "until", "username"},
    "linkedaccount": {"access_token_enc", "account_id", "account_name", "cursor_ms", "expires_at", "last_error", "last_sync_at", "linked_at", "provider", "refresh_token_enc", "scope", "username"},
    "ravenmission": {"coding_model", "created_at", "depends_on_mission_id", "error_summary", "id", "mission_type", "next_mission_query", "output_log", "priority", "progress", "proposed_mission", "result", "scheduled_for", "status", "target_container", "user_id"},
    "recommendationlanes": {"data", "updated_at", "updated_by", "username"},
    "revokedcredential": {"id", "key_hash", "kind", "reason", "revoked_at", "username"},
    "role": {"builtin", "description", "id", "name", "permissions"},
    "screenrules": {"data", "updated_at", "updated_by", "username"},
    "screentimerequest": {"created_at", "day", "decided_at", "decided_by", "granted_minutes", "id", "minutes", "note", "status", "username"},
    "unifiedpushregistration": {"app", "created_at", "device_id", "id", "token", "topic", "username"},
    "user": {"display_name", "ha_token_enc", "ha_url", "id", "is_system_default", "nextcloud_pass_enc", "nextcloud_url", "nextcloud_user", "username"},
    "useractivitysharing": {"data", "username"},
    "useravatarimage": {"data", "mime", "updated_at", "username"},
    "usercalendarsetting": {"data", "username"},
    "usercredentialshare": {"granted_at", "granted_by", "note", "services", "username"},
    "usernotificationpreference": {"data", "username"},
    "userrole": {"id", "role_id", "user_id"},
    "userthemesetting": {"data", "username"},
    "userweathersetting": {"data", "username"},
    "userwidget": {"config", "id", "is_pinned", "order_index", "pinned_devices", "size", "sort_mode", "updated_at", "username", "visibility", "widget_key"},
    "userworkspacesession": {"data", "id", "updated_at", "username", "workspace_id"},
    "voicependingenrolment": {"created_at", "model", "samples", "satellite", "session", "updated_at", "username"},
    "voicesignature": {"model", "samples", "updated_at", "username"},
    "workspacechat": {"created_at", "id", "title", "updated_at", "username", "workspace_id"},
    "workspacechatmessage": {"chat_id", "created_at", "id", "meta", "parts", "role"},
}


def test_every_model_column_is_in_the_baseline_or_a_migration():
    added = {(table, column) for table, column, _ in SCHEMA_COLUMNS}
    missing = []
    for name, table in SQLModel.metadata.tables.items():
        if name not in BASELINE:
            continue
        for column in table.columns:
            if column.name not in BASELINE[name] and (name, column.name) not in added:
                missing.append(f'("{name}", "{column.name}", <SQL>)')
    assert not missing, "add these to services/identity/main.py SCHEMA_COLUMNS: " + ", ".join(missing)


def test_an_old_user_table_gains_every_new_column(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    SQLModel.metadata.create_all(engine)
    keep = sorted(BASELINE["user"])
    defs = ['"id" INTEGER PRIMARY KEY' if column == "id" else f'"{column}"' for column in keep]
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE user"))
        conn.execute(text(f"CREATE TABLE user ({', '.join(defs)})"))

    monkeypatch.setattr(identity_main, "engine", engine)
    identity_main._ensure_schema_upgrades()

    have = {column["name"] for column in inspect(engine).get_columns("user")}
    wanted = {column.name for column in SQLModel.metadata.tables["user"].columns}
    assert wanted <= have, sorted(wanted - have)
