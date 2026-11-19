"""Login providers: the hub's own users, Jarvis's accounts mirrored in, logins, permissions. No network.

A fake identity (``FakeIdentity``) answers the three routes the hub calls. The hub starts from a small
made-up Home Assistant export (``test_ha_auth.write_export``: Alex, an admin with a password and tokens; Sam,
inactive). Every name, password and entity here is made up.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from services.homehub import entries as entries_mod
from services.homehub import manual_flows
from services.homehub.api import build_app
from services.homehub.auth import Auth
from services.homehub.auth_providers.base import EntityAccess, ProviderUnavailable
from services.homehub.auth_providers.jarvis_identity import JarvisIdentityProvider, provider_user
from services.homehub.core import ConfigEntry, EntityEntry, Hub
from services.homehub.runtime import Runtime
from services.homehub.store import Store
from services.homehub.tests.test_ha_auth import ANDROID, APP_TOKEN, CALLBACK, write_export

SECRET = "internal-secret"
ADMIN = {"Authorization": f"Bearer {SECRET}"}
JARVIS = "jarvis_identity"


class FakeIdentity(JarvisIdentityProvider):
    """Identity's answers, kept in memory; ``down`` makes it unreachable."""

    def __init__(self) -> None:
        super().__init__("http://identity.invalid", "unused")
        self.users: list[dict[str, Any]] = [
            {"id": 1, "username": "alex", "display_name": "Alex", "is_admin": True, "family_role": "father",
             "has_password": True},
            {"id": 2, "username": "robin", "display_name": "Robin", "is_admin": False, "family_role": "child",
             "has_password": True, "avatar": "preset:fox"},
            {"id": 3, "username": "default", "display_name": "", "is_admin": True, "is_system_default": True,
             "has_password": True},
        ]
        self.passwords = {"alex": "alex-jarvis-pass", "robin": "robin-jarvis-pass", "default": "default-pass"}
        self.access: dict[str, dict[str, Any]] = {"robin": {"device_ids": ["light.kitchen"],
                                                             "protected_entity_ids": ["lock.front"],
                                                             "permitted_entity_ids": [], "all_unprotected": False}}
        self.down = False
        self.locked: set[str] = set()
        self.logins: list[str] = []

    async def _request(self, method: str, path: str, **kw: Any) -> tuple[int, Any]:
        if self.down:
            raise ProviderUnavailable("Jarvis's identity service cannot be reached (ClientConnectorError)")
        if path == "/api/users":
            return 200, [dict(u) for u in self.users]
        if path == "/api/internal/verify-password":
            name, password = kw["json"]["username"], kw["json"]["password"]
            self.logins.append(name)
            if name in self.locked:
                return 429, {"detail": "Too many sign-in attempts."}
            user = next((u for u in self.users if u["username"] == name), None)
            if user is None or self.passwords.get(name) != password:
                return 401, {"detail": "Invalid username or password"}
            return 200, {"ok": True, "id": user["id"], "username": name}
        if path == "/api/internal/user-device-assignments":
            return 200, self.access.get(kw["params"]["username"], {"device_ids": [], "protected_entity_ids": [],
                                                                     "permitted_entity_ids": []})
        return 404, None


def _make(tmp_path, *, configured: bool = True, persons: list[dict[str, Any]] | None = None):
    config_dir = tmp_path / "homeassistant"
    write_export(config_dir, now=time.time())
    store = Store(str(tmp_path / "hub.db"), Fernet.generate_key().decode())
    from services.homehub.importer import import_config_dir

    import_config_dir(store, str(config_dir))
    if persons is not None:
        store.set_kv("persons", persons)
    hub = Hub(time_zone="UTC", location={"latitude": 1.0, "longitude": 2.0})
    hub.store = store
    runtime = Runtime(hub, store)
    if configured:
        runtime.provider_configs = {JARVIS: {"url": "http://identity.invalid", "secret": "unused"}}
    auth = Auth(store, SECRET)
    app = build_app(runtime, auth)
    fake = FakeIdentity()
    if configured:
        runtime.auth_providers.providers[JARVIS] = fake
    return app, runtime, auth, store, fake


@pytest.fixture
def env(tmp_path):
    app, runtime, auth, store, fake = _make(tmp_path, persons=[
        {"id": "p-alex", "name": "Alex", "user_id": "u-alex", "jarvis_user": None, "device_trackers": []},
        {"id": "p-robin", "name": "Robbie", "user_id": None, "jarvis_user": "robin", "device_trackers": []},
        {"id": "p-guest", "name": "Guest", "user_id": None, "jarvis_user": None, "device_trackers": []},
    ])
    with TestClient(app) as client:
        mgr = runtime.auth_providers
        assert mgr.ensure_entries() == [JARVIS]
        yield {"client": client, "runtime": runtime, "auth": auth, "store": store, "fake": fake, "mgr": mgr}


def sync(env) -> dict[str, Any]:
    return env["client"].post(f"/hub/auth/providers/{JARVIS}/sync", headers=ADMIN).json()


def users(env) -> dict[str, dict[str, Any]]:
    return {(u.get("provider") or {}).get("username") or u["id"]: u for u in env["store"].get_kv("users")}


def persons(env) -> dict[str, dict[str, Any]]:
    return {p["id"]: p for p in env["store"].get_kv("persons")}


def login(c, username, password):
    flow = c.post("/auth/login_flow", json={"client_id": ANDROID, "handler": ["homeassistant", None],
                                            "redirect_uri": CALLBACK}).json()
    return c.post(f"/auth/login_flow/{flow['flow_id']}",
                  json={"client_id": ANDROID, "username": username, "password": password}).json()


def token_for(c, username, password) -> str:
    done = login(c, username, password)
    assert done["type"] == "create_entry", done
    r = c.post("/auth/token", data={"grant_type": "authorization_code", "client_id": ANDROID, "code": done["result"]})
    return r.json()["access_token"]


# -- sync ---------------------------------------------------------------------------------------------

def test_first_sync_links_the_imported_users_and_makes_the_rest(env):
    report = sync(env)
    assert report["ok"] and report["provider_users"] == 3
    assert report["users_matched"] == 1 and report["users_added"] == 2
    assert report["standalone_users"] == ["Sam"]
    u = users(env)
    assert u["alex"]["id"] == "u-alex" and u["alex"]["provider"]["linked"] == "matched"
    assert u["robin"]["provider"]["linked"] == "created" and u["robin"]["entity_access"]["assigned"] == ["light.kitchen"]
    assert u["alex"]["entity_access"] is None  # admins are never limited
    assert u["alex"]["jarvis_user"] == "alex" and u["robin"]["jarvis_user"] == "robin"
    # Alex's imported refresh token still works, and Alex is still an admin (from Jarvis now).
    access = env["client"].post("/auth/token", data={"grant_type": "refresh_token", "refresh_token": APP_TOKEN,
                                                     "client_id": ANDROID}).json()["access_token"]
    p = env["auth"].authenticate(access)
    assert p.user_id == "u-alex" and p.is_admin and p.provider == JARVIS
    # Persons: Alex's kept and linked; "Robbie" (linked by Jarvis user) linked and renamed; no person for default.
    ps = persons(env)
    assert ps["p-alex"]["source"] == JARVIS and ps["p-alex"]["jarvis_user"] == "alex"
    assert ps["p-robin"]["name"] == "Robin" and ps["p-robin"]["user_id"] == u["robin"]["id"]
    assert ps["p-robin"]["avatar"] == "preset:fox"
    assert ps["p-guest"].get("source") is None
    assert not any(p.get("user_id") == u["default"]["id"] for p in ps.values())
    assert report["persons_linked"] == 2 and report["persons_created"] == 0 and report["persons_renamed"] == 1
    # Idempotent: nothing changes the second time.
    again = sync(env)
    assert again["ok"] and all(again[k] == 0 for k in ("users_added", "users_matched", "users_updated",
                                                       "users_disabled", "persons_created", "persons_linked",
                                                       "persons_renamed", "trackers_attached"))


def test_a_new_identity_user_gets_a_person_and_their_phones(env):
    sync(env)
    env["fake"].users.append({"id": 9, "username": "kit", "display_name": "Kit", "family_role": "teen"})
    rt = env["runtime"]
    # Kit's phone registered with Kit's own token after the first sync, and a Jarvis-fed tablet for kit.
    sync(env)
    kit = users(env)["kit"]
    rt.hub.config_entries["m1"] = ConfigEntry(entry_id="m1", domain="mobile_app", title="Kit phone",
                                              data={"user_id": kit["id"]})
    rt.hub.config_entries["m2"] = ConfigEntry(entry_id="m2", domain="mobile_app", title="Kit tablet",
                                              options={"jarvis_user": "kit"})
    for eid, entry in (("device_tracker.kit_phone", "m1"), ("device_tracker.kit_tablet", "m2")):
        rt.hub.entities[eid] = EntityEntry(entity_id=eid, platform="mobile_app", unique_id=eid,
                                           config_entry_id=entry)
    report = sync(env)
    assert report["trackers_attached"] == 2
    kit_person = next(p for p in persons(env).values() if p.get("user_id") == kit["id"])
    assert kit_person["device_trackers"] == ["device_tracker.kit_phone", "device_tracker.kit_tablet"]
    assert kit_person["jarvis_user"] == "kit"
    assert any(e.platform == "person" and e.unique_id == kit_person["id"] for e in rt.hub.entities.values())


def test_rename_disable_and_return(env):
    sync(env)
    c = env["client"]
    robin_token = token_for(c, "robin", "robin-jarvis-pass")
    person_eid = next(e.entity_id for e in env["runtime"].hub.entities.values()
                      if e.platform == "person" and e.unique_id == "p-robin")
    env["fake"].users[1]["display_name"] = "Robin Q"
    report = sync(env)
    assert report["users_updated"] == 1 and report["persons_renamed"] == 1
    assert users(env)["robin"]["name"] == "Robin Q" and persons(env)["p-robin"]["name"] == "Robin Q"
    assert next(e.entity_id for e in env["runtime"].hub.entities.values()
                if e.platform == "person" and e.unique_id == "p-robin") == person_eid  # the entity id stays
    # Removed in Jarvis: the hub user stays (history names them) but is inactive, and their session ends.
    gone = env["fake"].users.pop(1)
    report = sync(env)
    assert report["users_disabled"] == 1
    robin = users(env)["robin"]
    assert robin["is_active"] is False and robin["provider"]["removed"] is True
    assert env["auth"].authenticate(robin_token) is None
    assert "p-robin" in persons(env)  # never deleted
    assert login(c, "robin", "robin-jarvis-pass")["errors"] == {"base": "invalid_auth"}
    # Back in Jarvis: active again, same hub user.
    env["fake"].users.append(gone)
    report = sync(env)
    assert report["users_reactivated"] == 1 and users(env)["robin"]["id"] == robin["id"]
    assert users(env)["robin"]["is_active"] is True


def test_a_disabled_flag_from_identity_disables(env):
    sync(env)
    env["fake"].users[1]["disabled"] = True
    assert sync(env)["users_disabled"] == 1
    assert users(env)["robin"]["is_active"] is False


@pytest.mark.parametrize("row,admin", [
    ({"is_admin": True, "family_role": "other"}, True),
    ({"is_admin": False, "family_role": "mother"}, True),
    ({"is_admin": False, "family_role": "father"}, True),
    ({"is_admin": False, "family_role": "teen"}, False),
    ({"is_admin": False}, False),
])
def test_admins_are_identity_admins_and_parents(row, admin):
    u = provider_user({"id": 5, "username": "x", **row})
    assert u is not None and u.is_admin is admin


def test_admin_rights_follow_identity_on_the_next_request_not_the_login(env):
    sync(env)
    c = env["client"]
    token = token_for(c, "robin", "robin-jarvis-pass")
    assert env["auth"].authenticate(token).is_admin is False
    env["fake"].users[1]["family_role"] = "mother"
    sync(env)
    assert env["auth"].authenticate(token).is_admin is True  # same token, new right
    assert c.get("/hub/users", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_identity_down_keeps_the_synced_users_and_their_sessions(env):
    sync(env)
    c = env["client"]
    token = token_for(c, "robin", "robin-jarvis-pass")
    env["fake"].down = True
    report = sync(env)
    assert report["ok"] is False and "cannot be reached" in report["error"]
    assert users(env)["robin"]["is_active"] is True
    assert env["auth"].authenticate(token) is not None  # an existing session keeps working
    done = login(c, "robin", "robin-jarvis-pass")
    assert done["errors"] == {"base": "cannot_connect"}
    assert "cannot be checked right now" in done["description_placeholders"]["message"]
    status = c.get("/hub/auth/providers", headers=ADMIN).json()
    jarvis = next(p for p in status["providers"] if p["type"] == JARVIS)
    assert jarvis["last_sync"]["ok"] is False and jarvis["users"] == 3


# -- logins -------------------------------------------------------------------------------------------

def test_jarvis_password_logs_in_and_the_hub_keeps_no_password(env):
    sync(env)
    c = env["client"]
    assert login(c, "robin", "wrong")["errors"] == {"base": "invalid_auth"}
    token = token_for(c, "Robin", "robin-jarvis-pass")
    assert env["auth"].authenticate(token).user_id == users(env)["robin"]["id"]
    blob = env["store"].load_blob("ha_auth")
    assert not any(cr.get("user_id") == users(env)["robin"]["id"] for cr in blob["credentials"])
    # A linked HA user logs in with the Jarvis password, not the old hub one, while Jarvis is on.
    assert login(c, "alex", "correct horse")["errors"] == {"base": "invalid_auth"}
    assert token_for(c, "alex", "alex-jarvis-pass")


def test_identity_lockout_is_reported(env):
    sync(env)
    env["fake"].locked.add("robin")
    done = login(env["client"], "robin", "robin-jarvis-pass")
    assert done["errors"] == {"base": "locked"} and "Too many" in done["description_placeholders"]["message"]


def test_an_account_made_in_jarvis_since_the_last_sync_logs_in_at_once(env):
    sync(env)
    env["fake"].users.append({"id": 7, "username": "jo", "display_name": "Jo"})
    env["fake"].passwords["jo"] = "jo-jarvis-pass"
    token = token_for(env["client"], "jo", "jo-jarvis-pass")
    assert env["auth"].authenticate(token).name == "Jo"


def test_hub_only_users_keep_their_own_logins_beside_jarvis(env):
    sync(env)
    c = env["client"]
    r = c.post("/hub/users", json={"name": "Visitor", "username": "visitor", "password": "visitor-pass"},
               headers=ADMIN)
    assert r.status_code == 200 and r.json()["source"] == "local"
    assert token_for(c, "visitor", "visitor-pass")
    # A later sync never takes a hub-only user over.
    sync(env)
    assert "provider" not in next(u for u in env["store"].get_kv("users") if u["name"] == "Visitor")
    # A username Jarvis has cannot be given to a hub-only user.
    assert c.post("/hub/users", json={"name": "R", "username": "robin", "password": "12345678"},
                  headers=ADMIN).status_code == 409


def test_primary_decides_a_username_both_have(env):
    c = env["client"]
    c.post("/hub/users", json={"name": "Kit here", "username": "kit", "password": "kit-hub-pass"}, headers=ADMIN)
    env["fake"].users.append({"id": 9, "username": "kit", "display_name": "Kit"})
    env["fake"].passwords["kit"] = "kit-jarvis-pass"
    sync(env)
    assert env["mgr"].primary() == JARVIS  # set when the provider was added
    assert env["auth"].authenticate(token_for(c, "kit", "kit-jarvis-pass")).name == "Kit"
    assert c.put("/hub/auth/providers", json={"primary": "local"}, headers=ADMIN).status_code == 200
    assert env["auth"].authenticate(token_for(c, "kit", "kit-hub-pass")).name == "Kit here"


def test_turning_hub_logins_off_needs_an_admin_who_can_still_log_in(env):
    c = env["client"]
    # Before any sync there is no Jarvis admin: refused.
    r = c.put("/hub/auth/providers", json={"local_logins": False}, headers=ADMIN)
    assert r.status_code == 409 and "No admin could log in" in r.json()["message"]
    sync(env)
    c.post("/hub/users", json={"name": "Visitor", "username": "visitor", "password": "visitor-pass"}, headers=ADMIN)
    assert c.put("/hub/auth/providers", json={"local_logins": False}, headers=ADMIN).json()["local_logins"] is False
    assert login(c, "visitor", "visitor-pass")["errors"] == {"base": "invalid_auth"}
    assert token_for(c, "alex", "alex-jarvis-pass")
    # Jarvis's entry removed: hub logins count as on again, whatever the setting says (no lockout).
    entry = env["mgr"].entry_of(JARVIS)
    asyncio.run(entries_mod.remove_entry(env["runtime"], entry.entry_id))
    assert env["mgr"].local_logins() is True
    assert token_for(c, "visitor", "visitor-pass")
    # And Alex, linked before, logs in with the hub password again.
    assert token_for(c, "alex", "correct horse")


def test_the_last_admin_cannot_be_demoted_while_hub_logins_are_the_only_way_in(tmp_path):
    app, _runtime, _auth, _store, _fake = _make(tmp_path, configured=False)
    with TestClient(app) as c:
        r = c.patch("/hub/users/u-alex", json={"is_admin": False}, headers=ADMIN)
        assert r.status_code == 409
        c.post("/hub/users", json={"name": "Second", "username": "second", "password": "second-pass",
                                   "is_admin": True}, headers=ADMIN)
        assert c.patch("/hub/users/u-alex", json={"is_admin": False}, headers=ADMIN).status_code == 200


# -- permissions --------------------------------------------------------------------------------------

@pytest.fixture
def limited(env):
    hub = env["runtime"].hub
    for eid in ("light.kitchen", "light.office", "lock.front"):
        hub.states.set(eid, "off", {"friendly_name": eid})
    calls: list[list[str]] = []

    async def handler(_hub, call):
        calls.append(list(call.entity_ids))

    hub.register_service("light", "turn_on", handler)
    hub.register_service("lock", "unlock", handler)
    sync(env)
    token = token_for(env["client"], "robin", "robin-jarvis-pass")
    return {**env, "token": token, "calls": calls, "H": {"Authorization": f"Bearer {token}"}}


def test_limits_apply_to_rest_calls(limited):
    c, h = limited["client"], limited["H"]
    assert [s["entity_id"] for s in c.get("/api/states", headers=h).json()] == ["light.kitchen"]
    assert c.get("/api/states/light.office", headers=h).status_code == 404
    assert c.post("/api/services/light/turn_on", json={"entity_id": "light.office"}, headers=h).status_code == 403
    assert c.post("/api/services/lock/unlock", json={"entity_id": "lock.front"}, headers=h).status_code == 403
    assert c.post("/api/services/light/turn_on", json={}, headers=h).status_code == 403
    assert c.post("/api/services/light/turn_on", json={"entity_id": "light.kitchen"}, headers=h).status_code == 200
    assert limited["calls"] == [["light.kitchen"]]
    assert [e["entity_id"] for e in c.get("/api/config/entity_registry", headers=h).json()] == []
    # Everything outside the routes that apply limits is closed to a limited user.
    assert c.get("/hub/users", headers=h).status_code == 401
    assert c.get("/api/homehub/cameras", headers=h).status_code == 401
    # An admin token sees everything.
    assert len(c.get("/api/states", headers=ADMIN).json()) >= 3


def test_limits_apply_to_websocket_calls(limited):
    c = limited["client"]
    with c.websocket_connect("/api/websocket") as ws:
        ws.receive_json()
        ws.send_json({"type": "auth", "access_token": limited["token"]})
        assert ws.receive_json()["type"] == "auth_ok"
        ws.send_json({"id": 1, "type": "get_states"})
        assert [s["entity_id"] for s in ws.receive_json()["result"]] == ["light.kitchen"]
        ws.send_json({"id": 2, "type": "call_service", "domain": "light", "service": "turn_on",
                      "target": {"entity_id": "light.office"}})
        msg = ws.receive_json()
        assert msg["success"] is False and msg["error"]["code"] == "unauthorized"
        ws.send_json({"id": 3, "type": "render_template", "template": "{{ states('lock.front') }}"})
        assert ws.receive_json()["success"] is False
        ws.send_json({"id": 4, "type": "config/auth/list"})
        assert ws.receive_json()["success"] is False
        ws.send_json({"id": 5, "type": "auth/current_user"})
        me = ws.receive_json()["result"]
        assert me["is_admin"] is False and me["name"] == "Robin"
        ws.send_json({"id": 6, "type": "subscribe_entities"})
        assert ws.receive_json()["success"] is True
        assert set(ws.receive_json()["event"]["a"]) == {"light.kitchen"}


def test_entity_access_snapshot_rules():
    a = EntityAccess.from_snapshot({"assigned": ["light.a"], "protected": ["lock.b", "light.a"],
                                    "permitted": ["lock.b", "light.z"], "all_unprotected": True})
    assert a.may_control("lock.b") and not a.may_control("light.a")  # protection wins over assignment
    assert a.may_control("switch.anything")  # every unlocked entity
    assert "light.z" not in a.permitted  # a permit for an unlocked entity is meaningless
    assert not EntityAccess.from_snapshot("garbage").may_view("light.a")  # unreadable means nothing, never all


# -- persons and the admin views ------------------------------------------------------------------------

def test_jarvis_persons_are_read_only_on_the_hub(env):
    sync(env)
    c = env["client"]
    listed = {p["id"]: p for p in c.get("/hub/persons", headers=ADMIN).json()["persons"]}
    assert listed["p-robin"]["source"] == JARVIS and listed["p-robin"]["editable"] is False
    assert listed["p-guest"]["source"] == "hub" and listed["p-guest"]["editable"] is True
    r = c.patch("/hub/persons/p-robin", json={"name": "X"}, headers=ADMIN)
    assert r.status_code == 409 and "Jarvis" in r.json()["message"]
    assert c.delete("/hub/persons/p-robin", headers=ADMIN).status_code == 409
    assert c.patch("/hub/persons/p-guest", json={"name": "Guest two"}, headers=ADMIN).status_code == 200
    state = env["runtime"].hub.states.get("person.robbie") or env["runtime"].hub.states.get("person.robin")
    assert state is None or state.attributes["editable"] is False


def test_users_view_and_jarvis_users_are_edited_in_jarvis(env):
    sync(env)
    c = env["client"]
    rows = {r["username"]: r for r in c.get("/hub/users", headers=ADMIN).json()["users"] if r["username"]}
    assert rows["robin"]["source"] == JARVIS and rows["robin"]["editable"] is False
    assert rows["alex"]["hub_login"] is True  # the imported password is kept, unused while Jarvis is on
    assert rows["sam"]["source"] == "local" and rows["sam"]["editable"] is True
    r = c.patch(f"/hub/users/{rows['robin']['id']}", json={"name": "Z"}, headers=ADMIN)
    assert r.status_code == 409 and "Jarvis" in r.json()["message"]
    text = c.get("/hub/users", headers=ADMIN).text + c.get("/hub/auth/providers", headers=ADMIN).text
    assert "password" not in text.replace("hub_login", "") and "unused" not in text  # no secrets in either


def test_the_provider_is_added_once_and_stays_removed(env):
    mgr = env["mgr"]
    entry = mgr.entry_of(JARVIS)
    assert entry is not None and mgr.ensure_entries() == []
    asyncio.run(entries_mod.remove_entry(env["runtime"], entry.entry_id))
    assert mgr.ensure_entries() == [] and mgr.entry_of(JARVIS) is None
    assert mgr.primary() == "local"
    # It can be added again by hand from Integrations.
    result = asyncio.run(manual_flows.adopt_jarvis_identity(env["runtime"], {}, None, None, {}))
    assert result["domain"] == JARVIS and mgr.is_active(JARVIS)


# -- standalone -------------------------------------------------------------------------------------

def test_a_hub_with_no_identity_runs_on_its_own_users(tmp_path):
    app, runtime, auth, _store, _fake = _make(tmp_path, configured=False)
    with TestClient(app) as c:
        mgr = runtime.auth_providers
        assert list(mgr.providers) == ["local"] and mgr.ensure_entries() == []
        status = c.get("/hub/auth/providers", headers=ADMIN).json()
        jarvis = next(p for p in status["providers"] if p["type"] == JARVIS)
        assert jarvis["configured"] is False and jarvis["active"] is False
        assert status["primary"] == "local" and status["local_logins"] is True
        # The imported login works as before, and the HA token too.
        token = token_for(c, " Alex ", "correct horse")
        assert auth.authenticate(token).user_id == "u-alex" and auth.authenticate(token).access is None
        assert c.get("/api/states", headers={"Authorization": f"Bearer {token}"}).status_code == 200
        # Hub-only users can be added, renamed, deactivated; nobody is limited.
        r = c.post("/hub/users", json={"name": "Visitor", "username": "visitor", "password": "visitor-pass"},
                   headers=ADMIN)
        uid = r.json()["id"]
        assert c.patch(f"/hub/users/{uid}", json={"name": "Visitor two", "password": "new-visitor-pass"},
                       headers=ADMIN).status_code == 200
        assert token_for(c, "visitor", "new-visitor-pass")
        assert c.patch(f"/hub/users/{uid}", json={"is_active": False}, headers=ADMIN).status_code == 200
        assert login(c, "visitor", "new-visitor-pass")["errors"] == {"base": "invalid_auth"}
        # Persons stay fully editable, and Jarvis's integration cannot be added without its address.
        assert c.post("/hub/persons", json={"name": "Pat"}, headers=ADMIN).status_code == 200
        with pytest.raises(manual_flows.FlowError):
            asyncio.run(manual_flows.adopt_jarvis_identity(runtime, {}, None, None, {}))
        assert c.post(f"/hub/auth/providers/{JARVIS}/sync", headers=ADMIN).status_code == 404
        # The login page asks for the hub's own login.
        page = c.get("/auth/authorize", params={"client_id": ANDROID, "redirect_uri": CALLBACK}).text
        assert "Log in to your home" in page


def test_the_login_page_asks_for_the_jarvis_account_when_jarvis_is_primary(env):
    page = env["client"].get("/auth/authorize", params={"client_id": ANDROID, "redirect_uri": CALLBACK}).text
    assert "Log in with your Jarvis account" in page


async def test_the_real_transport_reports_an_unreachable_identity():
    provider = JarvisIdentityProvider("http://127.0.0.1:9", "unused")
    with pytest.raises(ProviderUnavailable):
        await provider.list_users()


async def test_the_entry_loads_by_syncing_and_retries_while_identity_is_down(tmp_path):
    from services.homehub.auth_providers.jarvis_identity import JarvisIdentityDriver

    _app, runtime, _auth, _store, fake = _make(tmp_path)
    runtime.auth_providers.ensure_entries()
    entry = runtime.auth_providers.entry_of(JARVIS)
    fake.down = True
    driver = JarvisIdentityDriver(runtime.hub, entry)
    with pytest.raises(RuntimeError, match="cannot be reached"):
        await driver.async_setup()
    fake.down = False
    await driver.async_setup()
    assert any((u.get("provider") or {}).get("username") == "robin" for u in runtime.store.get_kv("users"))
    assert driver.poll_interval == 60.0


def test_a_synced_user_owns_and_sees_their_private_place_by_jarvis_username(env):
    """Places (registries.py) map the Jarvis username the gateway sends to the hub user that carries it."""
    sync(env)
    c, u = env["client"], users(env)
    place = {"name": "Practice field", "latitude": 1.01, "longitude": 2.01, "radius": 80, "visibility": "private"}
    made = c.post("/hub/zones", json={**place, "owner": "Robin"}, headers=ADMIN).json()
    assert made["owner"] == u["robin"]["id"]

    def seen(viewer: str) -> list[str]:
        return [z["name"] for z in c.get("/hub/zones", params={"viewer": viewer}, headers=ADMIN).json()["zones"]]

    assert "Practice field" in seen("robin")
    assert "Practice field" not in seen("alex")
    # Alex (an admin from Jarvis) logging in to the hub sees his own private place by his hub login.
    mine = c.post("/hub/zones", json={**place, "name": "Alex only", "owner": "alex"}, headers=ADMIN).json()
    assert mine["owner"] == "u-alex"  # the HA-imported user, linked by username
    token = token_for(c, "alex", "alex-jarvis-pass")
    names = [z["name"] for z in c.get("/hub/zones", headers={"Authorization": f"Bearer {token}"}).json()["zones"]]
    assert "Alex only" in names and "Practice field" not in names
