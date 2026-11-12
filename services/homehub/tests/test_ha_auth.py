"""HA's login for the companion apps: the refresh grant, the login flow, revocation. No network."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time

import bcrypt
import jwt
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from services.homehub import ha_auth
from services.homehub.api import build_app
from services.homehub.auth import Auth
from services.homehub.core import Hub
from services.homehub.runtime import Runtime
from services.homehub.store import Store

SECRET = "internal-secret"
ANDROID = "https://home-assistant.io/android"
CALLBACK = "homeassistant://auth-callback"
# Made-up values: 128 hex characters like HA's tokens, never real ones.
APP_TOKEN = "a1" * 64
LLAT_TOKEN = "b2" * 64
SYSTEM_TOKEN = "c3" * 64
OLD_TOKEN = "d4" * 64


def jwt_key(tid: str) -> str:
    return f"key-{tid}-" + "0" * 32  # long enough for HS256


def _hash(password: str) -> str:
    return base64.b64encode(bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4))).decode()


def write_export(config_dir, *, now: float) -> None:
    """A minimal HA ``.storage`` with two users, their refresh tokens and passwords."""
    storage = config_dir / ".storage"
    storage.mkdir(parents=True)

    def put(name, data):
        (storage / name).write_text(json.dumps({"version": 1, "data": data}))

    def token(tid, value, user, kind, client_id=None, expire_at=None):
        return {"id": tid, "user_id": user, "client_id": client_id, "client_name": None, "client_icon": None,
                "token_type": kind, "created_at": "2026-01-01T00:00:00+00:00", "access_token_expiration": 1800.0,
                "token": value, "jwt_key": jwt_key(tid), "last_used_at": None, "last_used_ip": None,
                "expire_at": expire_at, "credential_id": "cred-alex", "version": "2026.11.0"}

    put("auth", {
        "users": [
            {"id": "u-alex", "name": "Alex", "is_owner": True, "is_active": True, "system_generated": False,
             "group_ids": ["system-admin"]},
            {"id": "u-sam", "name": "Sam", "is_owner": False, "is_active": False, "system_generated": False,
             "group_ids": ["system-users"]},
            {"id": "u-sys", "name": "Supervisor", "is_owner": False, "is_active": True, "system_generated": True,
             "group_ids": ["system-admin"]},
        ],
        "groups": [],
        "credentials": [
            {"id": "cred-alex", "user_id": "u-alex", "auth_provider_type": "homeassistant",
             "auth_provider_id": None, "data": {"username": "alex"}},
            {"id": "cred-sam", "user_id": "u-sam", "auth_provider_type": "homeassistant",
             "auth_provider_id": None, "data": {"username": "sam"}},
        ],
        "refresh_tokens": [
            token("rt-app", APP_TOKEN, "u-alex", "normal", ANDROID, now + 86400),
            token("rt-llat", LLAT_TOKEN, "u-alex", "long_lived_access_token"),
            token("rt-sys", SYSTEM_TOKEN, "u-sys", "system"),
            token("rt-old", OLD_TOKEN, "u-alex", "normal", ANDROID, now - 10),
        ],
    })
    put("auth_provider.homeassistant", {"users": [{"username": "alex", "password": _hash("correct horse")},
                                                  {"username": "sam", "password": _hash("battery staple")}]})
    put("core.config", {"location_name": "Home", "latitude": 1.0, "longitude": 2.0, "time_zone": "UTC"})
    put("mobile_app", {"deleted_ids": ["f" * 64], "live_activity_tokens": {}})
    put("core.uuid", {"uuid": "0123456789abcdef0123456789abcdef"})


@pytest.fixture
def env(tmp_path):
    config_dir = tmp_path / "homeassistant"
    write_export(config_dir, now=time.time())
    store = Store(str(tmp_path / "hub.db"), Fernet.generate_key().decode())
    from services.homehub.importer import import_config_dir

    report = import_config_dir(store, str(config_dir))
    hub = Hub(time_zone="UTC", location={"latitude": 1.0, "longitude": 2.0})
    hub.store = store
    runtime = Runtime(hub, store)
    auth = Auth(store, SECRET)
    app = build_app(runtime, auth)
    with TestClient(app) as client:
        yield {"client": client, "store": store, "auth": auth, "runtime": runtime, "report": report,
               "db": str(tmp_path / "hub.db")}


def test_the_import_keeps_hashes_never_token_values_or_passwords_in_the_clear(env):
    store, report = env["store"], env["report"]
    assert report["refresh_token_hashes"] == 4 and report["login_credentials"] == 2
    blob = store.load_blob(ha_auth.BLOB)
    assert blob["tokens"]["rt-app"]["sha256"] == hashlib.sha256(APP_TOKEN.encode()).hexdigest()
    assert blob["tokens"]["rt-app"]["client_id"] == ANDROID
    assert store.get_kv("instance_id") == "0123456789abcdef0123456789abcdef"
    with open(env["db"], "rb") as f:
        raw = f.read()
    # "JDJiJ" is base64 of "$2b$": HA's stored password hashes must only exist sealed.
    for leaked in (APP_TOKEN, LLAT_TOKEN, "correct horse", jwt_key("rt-app"), "JDJiJ"):
        assert leaked.encode() not in raw, leaked


def test_refresh_grant_issues_an_access_token_every_hub_route_accepts(env):
    c, auth = env["client"], env["auth"]
    r = c.post("/auth/token", data={"grant_type": "refresh_token", "refresh_token": APP_TOKEN, "client_id": ANDROID})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    body = r.json()
    assert body["token_type"] == "Bearer" and body["expires_in"] == 1800 and "refresh_token" not in body
    claims = jwt.decode(body["access_token"], jwt_key("rt-app"), algorithms=["HS256"])
    assert claims["iss"] == "rt-app" and claims["exp"] - claims["iat"] == 1800
    p = auth.authenticate(body["access_token"])
    assert p is not None and p.user_id == "u-alex" and p.is_admin
    assert c.get("/api/", headers={"Authorization": f"Bearer {body['access_token']}"}).status_code == 200
    # Using it moves the 90-day expiry forward, as HA does.
    meta = env["store"].load_blob(ha_auth.BLOB)["tokens"]["rt-app"]
    assert meta["expire_at"] > time.time() + 89 * 86400


@pytest.mark.parametrize(("form", "status", "error"), [
    ({"refresh_token": APP_TOKEN, "client_id": "https://example.invalid/other"}, 400, "invalid_request"),
    ({"refresh_token": APP_TOKEN}, 400, "invalid_request"),  # the app's token is bound to its client id
    ({"refresh_token": "e5" * 64, "client_id": ANDROID}, 400, "invalid_grant"),
    ({"refresh_token": OLD_TOKEN, "client_id": ANDROID}, 400, "invalid_grant"),  # expired
    ({"refresh_token": SYSTEM_TOKEN}, 400, "invalid_grant"),
    ({"client_id": ANDROID}, 400, "invalid_request"),
    ({"refresh_token": APP_TOKEN, "client_id": "javascript:alert(1)"}, 400, "invalid_request"),
])
def test_refresh_grant_refusals(env, form, status, error):
    r = env["client"].post("/auth/token", data={"grant_type": "refresh_token", **form})
    assert r.status_code == status and r.json()["error"] == error


def test_long_lived_tokens_refresh_without_a_client_id_like_ha(env):
    r = env["client"].post("/auth/token", data={"grant_type": "refresh_token", "refresh_token": LLAT_TOKEN})
    assert r.status_code == 200


def test_token_endpoint_rejects_odd_requests(env):
    c = env["client"]
    assert c.post("/auth/token", data={"grant_type": "password"}).json() == {"error": "unsupported_grant_type"}
    dup = "grant_type=refresh_token&refresh_token=x&refresh_token=y"
    r = c.post("/auth/token", content=dup, headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 400 and r.json()["error"] == "invalid_request"


def _login(c, password="correct horse", challenge=None):
    body = {"client_id": ANDROID, "handler": ["homeassistant", None], "redirect_uri": CALLBACK}
    if challenge:
        body.update(code_challenge=challenge, code_challenge_method="S256")
    flow = c.post("/auth/login_flow", json=body).json()
    assert flow["type"] == "form" and flow["step_id"] == "init"
    assert [f["name"] for f in flow["data_schema"]] == ["username", "password"]
    return c.post(f"/auth/login_flow/{flow['flow_id']}",
                  json={"client_id": ANDROID, "username": " Alex ", "password": password}).json()


def test_login_flow_then_code_grant_with_pkce_then_refresh(env):
    c = env["client"]
    assert c.get("/auth/providers").json()["providers"] == [
        {"name": "Home Assistant Local", "id": None, "type": "homeassistant"}]
    verifier = "v" * 50
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    done = _login(c, challenge=challenge)
    assert done["type"] == "create_entry" and done["result"]
    bad = c.post("/auth/token", data={"grant_type": "authorization_code", "client_id": ANDROID,
                                      "code": done["result"], "code_verifier": "w" * 50})
    assert bad.json()["error"] == "invalid_grant"
    # A code works once: the failed attempt above consumed it, as a stolen code must not be retried.
    again = _login(c, challenge=challenge)
    r = c.post("/auth/token", data={"grant_type": "authorization_code", "client_id": ANDROID,
                                    "code": again["result"], "code_verifier": verifier})
    assert r.status_code == 200
    tokens = r.json()
    assert tokens["refresh_token"] and tokens["ha_auth_provider"] == "homeassistant"
    assert env["auth"].authenticate(tokens["access_token"]).user_id == "u-alex"
    r2 = c.post("/auth/token", data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
                                     "client_id": ANDROID})
    assert r2.status_code == 200
    stored = env["store"].load_blob(ha_auth.BLOB)["tokens"]
    assert all(tokens["refresh_token"] not in json.dumps(v) for v in stored.values())


def test_wrong_password_and_lockout(env):
    c = env["client"]
    assert _login(c, password="nope")["errors"] == {"base": "invalid_auth"}
    for _ in range(ha_auth.MAX_FAILURES_PER_USER):
        _login(c, password="nope")
    # Locked: even the right password is refused for a while.
    assert _login(c)["errors"] == {"base": "invalid_auth"}


def test_inactive_users_and_unknown_flows(env):
    c = env["client"]
    flow = c.post("/auth/login_flow", json={"client_id": ANDROID, "handler": ["homeassistant", None],
                                            "redirect_uri": CALLBACK}).json()
    r = c.post(f"/auth/login_flow/{flow['flow_id']}",
               json={"client_id": ANDROID, "username": "sam", "password": "battery staple"})
    assert r.json()["errors"] == {"base": "invalid_auth"}
    assert c.post("/auth/login_flow/nope", json={"client_id": ANDROID}).status_code == 404
    assert c.delete(f"/auth/login_flow/{flow['flow_id']}").status_code == 200
    bad = c.post("/auth/login_flow", json={"client_id": ANDROID, "handler": ["homeassistant", None],
                                           "redirect_uri": CALLBACK, "code_challenge": "x" * 43,
                                           "code_challenge_method": "plain"})
    assert bad.status_code == 400


def test_redirects_must_belong_to_the_client(env):
    c = env["client"]
    flow = c.post("/auth/login_flow", json={"client_id": ANDROID, "handler": ["homeassistant", None],
                                            "redirect_uri": "https://attacker.invalid/cb"}).json()
    r = c.post(f"/auth/login_flow/{flow['flow_id']}",
               json={"client_id": ANDROID, "username": "alex", "password": "correct horse"})
    assert r.status_code == 403
    assert ha_auth.verify_redirect_uri("https://hub.example.invalid/", "https://hub.example.invalid/?auth_callback=1")
    assert not ha_auth.verify_redirect_uri("https://hub.example.invalid/", "https://other.invalid/")
    assert ha_auth.verify_redirect_uri(ANDROID, CALLBACK)


def test_authorize_page_escapes_its_parameters(env):
    c = env["client"]
    r = c.get("/auth/authorize", params={"client_id": ANDROID, "redirect_uri": CALLBACK,
                                         "state": '"><script>alert(1)</script>'})
    assert r.status_code == 200 and "<script>alert(1)" not in r.text
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert c.get("/auth/authorize", params={"client_id": ANDROID,
                                            "redirect_uri": "https://attacker.invalid/"}).status_code == 400


def test_revoke_ends_the_session_now(env):
    c, auth = env["client"], env["auth"]
    access = c.post("/auth/token", data={"grant_type": "refresh_token", "refresh_token": APP_TOKEN,
                                         "client_id": ANDROID}).json()["access_token"]
    assert auth.authenticate(access) is not None
    assert c.post("/auth/revoke", data={"token": APP_TOKEN}).status_code == 200
    assert auth.authenticate(access) is None
    r = c.post("/auth/token", data={"grant_type": "refresh_token", "refresh_token": APP_TOKEN, "client_id": ANDROID})
    assert r.json()["error"] == "invalid_grant"
    assert c.post("/auth/revoke", data={"token": "unknown"}).status_code == 200


def test_ws_current_user(env):
    c = env["client"]
    access = c.post("/auth/token", data={"grant_type": "refresh_token", "refresh_token": APP_TOKEN,
                                         "client_id": ANDROID}).json()["access_token"]
    with c.websocket_connect("/api/websocket") as ws:
        assert ws.receive_json()["type"] == "auth_required"
        ws.send_json({"type": "auth", "access_token": access})
        assert ws.receive_json()["type"] == "auth_ok"
        ws.send_json({"id": 1, "type": "auth/current_user"})
        msg = ws.receive_json()
        assert msg["success"] and msg["result"]["id"] == "u-alex" and msg["result"]["is_admin"] is True


def test_no_import_means_no_logins(tmp_path):
    store = Store(str(tmp_path / "x.db"), Fernet.generate_key().decode())
    mgr = ha_auth.HAAuth(store)
    assert mgr.find_refresh_token(APP_TOKEN) is None
    assert mgr.validate_login("alex", "correct horse") is None
    assert os.path.exists(tmp_path / "x.db")
